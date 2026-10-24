"""RockMapper adapter at the detector seam (detector.py).

Export: single-band 8-bit GeoTIFFs under rockmapper_out/input/<mode>/,
written with Pillow and raw GeoTIFF tags (no GDAL). Run: RockMapper's
do_work in the rockmapper conda env. Read: the class mosaic, sampled at the
input pixel centers from its GeoTIFF tags, so reading needs no rasterio.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from PIL.TiffImagePlugin import ImageFileDirectory_v2

import detector as D

HERE = D.HERE
OUT = HERE / "rockmapper_out"
INPUT = OUT / "input"
RESULTS = HERE / "rockmapper_results"

EPSG = 32615                  # placeholder projected CRS (UTM zone 15N)
ORIGIN_E, ORIGIN_N = 500_000.0, 3_000_000.0   # top-left corner, meters
NODATA = 0                    # RockMapper treats 0 as no data
DECLARED_PX_M = 0.1           # default declared pixel size, meters
HONEST_CROP_PX = (80, 40)     # honest export crop, px (width, height); 400 m x 200 m at 5 m per pixel
CLASS_NAMES = {0: "NoData", 1: "Shadow", 2: "Other", 3: "Gravel",
               4: "Cobble Boulder", 5: "Bedrock"}
CLASS_RGB = {0: (0, 0, 0), 1: (60, 60, 60), 2: (230, 210, 150), 3: (240, 140, 40),
             4: (200, 40, 40), 5: (90, 120, 230)}


def write_geotiff(path, gray, px_m, epsg=EPSG):
    """Single-band uint8 GeoTIFF, north-up, pixel-is-area, nodata 0."""
    ifd = ImageFileDirectory_v2()
    ifd[33550] = (float(px_m), float(px_m), 0.0)                 # ModelPixelScale
    ifd.tagtype[33550] = 12
    ifd[33922] = (0.0, 0.0, 0.0, ORIGIN_E, ORIGIN_N, 0.0)        # ModelTiepoint
    ifd.tagtype[33922] = 12
    ifd[34735] = (1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3072, 0, 1, int(epsg))
    ifd.tagtype[34735] = 3                                        # GeoKeyDirectory
    ifd[42113] = str(NODATA)                                      # GDAL_NODATA
    ifd.tagtype[42113] = 2
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(gray, mode="L").save(path, tiffinfo=ifd)


def geo_grid(img):
    """(east, north of the top-left corner, pixel width m, pixel height m) of a GeoTIFF."""
    sx, sy = img.tag_v2[33550][:2]
    tp = img.tag_v2[33922]
    return tp[3] - tp[0] * sx, tp[4] + tp[1] * sy, sx, sy


def sample_classes(mosaic_path, input_path):
    """RockMapper class mosaic resampled (nearest) onto the input GeoTIFF's grid."""
    with Image.open(input_path) as src:
        e0, n0, sx, sy = geo_grid(src)
        h, w = src.height, src.width
    with Image.open(mosaic_path) as m:
        me, mn, msx, msy = geo_grid(m)
        cls = np.asarray(m)
    cols = np.floor((e0 + (np.arange(w) + 0.5) * sx - me) / msx).astype(int)
    rows = np.floor((mn - (n0 - (np.arange(h) + 0.5) * sy)) / msy).astype(int)
    ok_c = (cols >= 0) & (cols < cls.shape[1])
    ok_r = (rows >= 0) & (rows < cls.shape[0])
    out = cls[np.clip(rows, 0, cls.shape[0] - 1)][:, np.clip(cols, 0, cls.shape[1] - 1)]
    out = np.where(ok_r[:, None] & ok_c[None, :], out, 0).astype(np.uint8)
    return out


def truth_path(mode, slug):
    return INPUT / mode / f"{slug}_truth.npy"


class RockMapper(D.Adapter):
    name = "RockMapper"
    env = "rockmapper"
    package = "rockmapper"
    alias = "rockmapper"
    model_note = ("The RockMapper model ships inside the rockmapper package, so a new "
                  "package version brings its model.")

    def export(self, scan, slug, mode, declared_px_m=None):
        """The whole scan image as one GeoTIFF, with its truth. Returns (path, (h, w))."""
        path = INPUT / mode / f"{slug}.tif"
        write_geotiff(path, scan.gray, declared_px_m or scan.px_m)
        D.write_truth(truth_path(mode, slug), scan.truth)
        return path, scan.gray.shape

    def export_scene(self, scan, settings, slug, opts=None):
        """Declared: the whole scan image at the declared pixel size. Honest:
        an 80 x 40 px crop on one swath side, mid-strip, at the render size."""
        import sonar_core as C
        declared_px_m = (opts or {}).get("declared_px_m", DECLARED_PX_M)
        gray, truth = scan.gray, scan.truth
        h, w = gray.shape
        cw, ch = min(HONEST_CROP_PX[0], w), min(HONEST_CROP_PX[1], h // 2)
        r0, c0 = h // 4 - ch // 2, w // 2 - cw // 2
        crop = (slice(r0, r0 + ch), slice(c0, c0 + cw))
        n, n_crop = C.mound_count(scan, settings), C.mound_count(scan, settings, *crop)
        self.export(scan, slug, "declared", declared_px_m)
        write_geotiff(INPUT / "honest" / f"{slug}.tif", np.ascontiguousarray(gray[crop]), scan.px_m)
        D.write_truth(truth_path("honest", slug), np.ascontiguousarray(truth[crop]))
        meta_path = INPUT / "meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        s = settings
        meta[slug] = dict(scene=s.scene, shape=[h, w], mounds=n, mounds_crop=n_crop,
                          render_px_m=scan.px_m, declared_px_m=declared_px_m,
                          vent_per_km2=s.vent_per_km2, vent_scale=s.vent_scale,
                          vent_height_m=s.vent_height_m)
        meta_path.write_text(json.dumps(meta, indent=2))
        return f"{s.scene}: {w} x {h} px, {n} mounds in image, {n_crop} in honest crop"

    def run_args(self, opts=None):
        return [f"Run {self.name}.cmd", "declared", "honest"]

    def mosaic(self, mode, slug):
        files = sorted((OUT / "pred" / mode / slug / "mosaic").glob("*.tif"))
        return files[0] if files else None

    def read(self, slug, mode, shape=None):
        m = self.mosaic(mode, slug)
        if m is None:
            return D.Detections()
        cls = sample_classes(m, INPUT / mode / f"{slug}.tif")
        if shape is not None and cls.shape != tuple(shape):
            return D.Detections()
        return D.Detections(classes=cls, class_rgb=CLASS_RGB)

    def summarize(self):
        RESULTS.mkdir(exist_ok=True)
        meta = json.loads((INPUT / "meta.json").read_text())
        rows = []
        for mode in ("declared", "honest"):
            for slug, m in meta.items():
                tif = INPUT / mode / f"{slug}.tif"
                if not tif.exists():
                    continue
                gray = np.array(Image.open(tif))
                pred = self.read(slug, mode, gray.shape).classes
                if pred is None:
                    rows.append(dict(mode=mode, scene=m["scene"], note="no prediction"))
                    continue
                mound = D.read_truth(truth_path(mode, slug), gray.shape)
                if mound is None:
                    mound = np.zeros(gray.shape, bool)
                rows.append(class_row(mode, m, pred, mound))
                save_side_by_side(RESULTS / f"{mode}_{slug}.png", gray, pred)
        (RESULTS / "class_fractions.json").write_text(json.dumps(rows, indent=2))
        for r in rows:
            print(json.dumps(r))


def class_row(mode, m, pred, mound):
    """class_fractions.json row: class shares, mound and ground mixes, patches."""
    valid = pred > 0
    labs, mound = mound, D.labels(mound) > 0
    row = dict(mode=mode, scene=m["scene"], valid_px=int(valid.sum()),
               mounds=m["mounds"] if mode == "declared" else m["mounds_crop"])
    for k, name in CLASS_NAMES.items():
        if k:
            row[name] = round(float((pred[valid] == k).mean()), 3) if valid.any() else 0
    if mound.any():
        for region, sel in (("mound", mound & valid), ("ground", ~mound & valid)):
            vals = pred[sel]
            row[region] = {CLASS_NAMES[k]: round(float((vals == k).mean()), 3)
                           for k in range(1, 6) if vals.size and (vals == k).any()}
        sc = D.score(labs, D.Detections(classes=pred))
        row["mound_class"] = CLASS_NAMES[sc["mound_class"]]
        row["patches"] = sc["patches"]
        row["patches_touching_mounds"] = sc["patches_touching_mounds"]
    return row


def save_side_by_side(path, gray, pred):
    side = np.concatenate([np.stack([gray] * 3, -1),
                           np.full((gray.shape[0], 4, 3), 255, np.uint8),
                           D.colorize(pred, CLASS_RGB)], axis=1)
    im = Image.fromarray(side)
    if im.width < 800:
        im = im.resize((im.width * 8, im.height * 8), Image.NEAREST)
    elif im.width > 1600:
        im = im.resize((im.width // 2, im.height // 2), Image.NEAREST)
    im.save(path, optimize=True)


# ---------- run (RockMapper env) ----------

def run_rockmapper(mode):
    from rockmapper.rock_mapper import do_work
    model = Path(sys.prefix) / "Lib" / "site-packages" / "rockmapper" / "models" / "RockMapper_20251117_v2"
    for tif in sorted((INPUT / mode).glob("*.tif")):
        do_work(
            inDir=str(stage(tif)),
            outDirTop=str(OUT / "pred" / mode),
            modelDir=str(model),
            projName=tif.stem,
            mapRast=True,
            mapShp=False,
            epsg=EPSG,
            windowSize_m=(18, 18),
            window_stride=6 if mode == "declared" else 18,
            minArea_percent=0.5,
            threadCnt=0.5,
            mosaicFileType=".tif",
            predBatchSize=8,
        )


def stage(tif):
    """RockMapper reads every .tif under inDir, so give each file its own folder."""
    import shutil
    d = OUT / "stage" / tif.parent.name / tif.stem
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tif, d / tif.name)
    return d
