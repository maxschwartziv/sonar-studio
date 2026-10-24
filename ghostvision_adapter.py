"""GhostVision adapter at the detector seam (detector.py). GhostVision is
GPL-3.0-or-later and runs as a separate tool: this module never imports it.

Export: JPG tiles of a gray scan image under ghostvision_out/tiles/<mode>/
(command-line batch), or one render's own run folder
ghostvision_out/runs/<slug>_<time>/tiles|pred (Sonar Studio's Run GhostVision).
Run: Run GhostVision.cmd in the ghostvision conda env. Read: GhostVision's
detections CSV, mapped back to scan image pixels as boxes.

Settings come from ghostvision_settings.json (Sonar Studio's GhostVision
settings dialog writes it).
"""
import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image

import detector as D
import tile_index as TI

HERE = D.HERE
OUT = HERE / "ghostvision_out"
TILES = OUT / "tiles"
PRED = OUT / "pred"
RUNS = OUT / "runs"
RUN_INFO = "run.json"
RESULTS = HERE / "ghostvision_results"
SETTINGS_PATH = HERE / "ghostvision_settings.json"
MODELS_DIR = Path.home() / ".ghostvision" / "models" / "ghostvision-models"
MODEL_NAMES = {"1": "RF-DETR", "2": "YOLO26", "5": "YOLOv12"}

# name -> (default, kind, low, high, label with unit)
SETTINGS = {
    "model": ("2", "model", None, None, "Model"),
    # Defaults measured 24 October 2026 on the default vent field: YOLO26 at 50 m per pixel,
    # confidence 0.01, auto stride, merged boxes found 89 of 119 mounds; 83 of 102 objects sat on a mound.
    "confidence": (0.01, float, 0.0, 1.0, "Confidence threshold (0 to 1)"),
    "px_m": (50.0, float, 5.0, 200.0, "GhostVision pixel size (m)"),
    "iou": (0.5, float, 0.0, 1.0, "Box overlap (IoU) threshold (0 to 1)"),
    "tile_pings": (500, int, 50, 5000, "Tile length (pings)"),
    "stride_pings": ("auto", int, 10, 5000, "Tile stride (pings, or auto)"),
    "sides": ("both", ("both", "port", "star"), None, None, "Track sides"),
    "mode": ("honest", ("honest", "declared"), None, None, "Scale"),
    "declared_px_m": (0.1, float, 0.01, 5.0, "Declared pixel size (m)"),
    "export_image": (True, bool, None, None, "Write images with boxes"),
    "export_video": (False, bool, None, None, "Write video"),
}
FLAGS = {"--model": "model", "--confidence": "confidence", "--iou": "iou",
         "--tile-pings": "tile_pings", "--stride-pings": "stride_pings", "--sides": "sides",
         "--mode": "mode", "--declared-px": "declared_px_m", "--export-image": "export_image",
         "--export-video": "export_video"}


# ---------- settings ----------

def defaults():
    return {k: v[0] for k, v in SETTINGS.items()}


def models():
    """Model numbers found in ~/.ghostvision/models/ghostvision-models/, or the known three."""
    found = sorted(p.name for p in MODELS_DIR.glob("*") if p.is_dir()) if MODELS_DIR.exists() else []
    return found or sorted(MODEL_NAMES)


def check(name, value):
    """Value converted to the setting's type; ValueError when out of range."""
    default, kind, lo, hi, label = SETTINGS[name]
    if kind == "model":
        value = str(value).rsplit("/", 1)[-1]
        if not value:
            raise ValueError(f"{label}: empty")
        return value
    if kind is bool:
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(kind, tuple):
        if value not in kind:
            raise ValueError(f"{label}: {value} is not one of {', '.join(kind)}")
        return value
    if name == "stride_pings" and str(value).strip().lower() == "auto":
        return "auto"
    value = kind(float(value)) if kind is int else float(value)
    if not lo <= value <= hi:
        raise ValueError(f"{label}: {value} is outside {lo} to {hi}")
    return value


def load_settings(path=None):
    """Saved settings over the defaults. Bad or missing values take the default."""
    s = defaults()
    try:
        saved = json.loads(Path(path or SETTINGS_PATH).read_text())
    except (OSError, ValueError):
        return s
    for k, v in saved.items():
        if k in SETTINGS:
            try:
                s[k] = check(k, v)
            except (ValueError, TypeError):
                pass
    return s


def save_settings(s, path=None):
    clean = {k: check(k, s[k]) for k in SETTINGS}
    Path(path or SETTINGS_PATH).write_text(json.dumps(clean, indent=1))
    return clean


def settings_args(argv, s=None):
    """Settings with --flag value pairs from argv applied. Returns (settings, rest)."""
    s = dict(load_settings() if s is None else s)
    rest = []
    it = iter(argv)
    for a in it:
        if a in FLAGS:
            s[FLAGS[a]] = check(FLAGS[a], next(it))
        else:
            rest.append(a)
    return s, rest


def launcher_args(s):
    """Arguments for Run GhostVision.cmd: model path, confidence, IoU, image and video flags, mode."""
    return [f"ghostvision-models/{s['model']}", str(s["confidence"]), str(s["iou"]),
            "1" if s["export_image"] else "0", "1" if s["export_video"] else "0", s["mode"]]


MOUND_MAX_M = 1500.0   # vent_shapes RULES["mound_max_m"] default: largest mound diameter at vent size 1
IOU_WARN = 0.9


def auto_stride(tile_pings, px_m, vent_scale=1.0):
    """Tile stride that keeps 3 x the largest mound diameter in the overlap of
    neighboring tiles, so every mound lies wholly inside at least one tile:
    tile_pings - ceil(3 x diameter / px_m), at least 10 pings.
    Defaults: 500 - ceil(3 x 1500 m / 50 m) = 410."""
    try:
        import vent_shapes as V
        mound_m = V.RULES["mound_max_m"]
    except Exception:
        mound_m = MOUND_MAX_M
    return max(10, int(tile_pings) - int(np.ceil(3 * mound_m * vent_scale / px_m - 1e-9)))


def stride_of(gs, px_m, vent_scale=1.0):
    """The settings' tile stride in pings, with "auto" resolved by auto_stride."""
    st = gs["stride_pings"]
    return auto_stride(gs["tile_pings"], px_m, vent_scale) if st == "auto" else int(st)


def iou_warning(gs):
    """Status-bar warning when IoU is high enough to keep stacked boxes, else ''."""
    if float(gs["iou"]) > IOU_WARN:
        return (f"IoU {float(gs['iou']):.1f} keeps overlapping boxes. "
                f"Default {SETTINGS['iou'][0]} (Reset to defaults).")
    return ""


def slug_of(settings):
    return settings.scene.replace(" ", "_") + f"_seed{settings.seed}"


# ---------- tiles (tile_index.py) ----------

def export_gray(gray, slug, mode, px_m, truth=None, gs=None, vent_scale=1.0, true_px_m=None):
    """Tiles of a gray scan image, its PNG and truth. Returns the tile count.
    true_px_m: real meters per pixel for the auto stride (default px_m)."""
    gs = gs or load_settings()
    stride = stride_of(gs, true_px_m or px_m, vent_scale)
    n = TI.write(TILES / mode, slug, gray, px_m, gs["tile_pings"], stride, gs["sides"])
    OUT.mkdir(parents=True, exist_ok=True)
    Image.fromarray(gray, mode="L").save(OUT / f"{slug}_render.png")
    if truth is not None:
        D.write_truth(OUT / f"{slug}_mound.npy", truth)
    return n


def box_to_render(tile, r):
    """GhostVision CSV row (box center x, y, width, height in tile pixels)
    to scan image pixels (center column, center row, width, height)."""
    x, y, bw, bh = (float(r[k]) for k in ("x", "y", "width", "height"))
    return (*TI.to_scan(tile, x, y, r.get("img_height")), bw, bh)


def read_csv_rows(paths):
    """GhostVision CSV rows keyed by tile file name."""
    by_name = {}
    for c in paths:
        with open(c, newline="") as f:
            for r in csv.DictReader(f):
                # GhostVision writes class_name; older output and fixtures use class.
                r["class"] = r.get("class_name") or r.get("class") or ""
                if r["class"]:
                    by_name.setdefault(Path(r.get("name_long") or r.get("name", "")).name, []).append(r)
    return by_name


def boxes(tiles, by_name):
    """Scan image boxes of the given tiles' CSV rows."""
    return [D.Box(*box_to_render(t, r), r["class"], float(r["confidence"]))
            for t in tiles for r in by_name.get(t["name"], [])]


class GhostVision(D.Adapter):
    name = "GhostVision"
    env = "ghostvision"
    package = "ghostvision"
    alias = "ghostvision-gpu"
    model_note = "GhostVision models in %USERPROFILE%\\.ghostvision\\models stay as they are."
    # inference-gpu 1.8.2 (upgraded from 0.64.8 on 24 October 2026; GhostVision results
    # unchanged). PINGInstaller's env file can put 0.64.8 back, so this step runs by default.
    # Two fixes it needs: pycuda comes prebuilt from conda-forge (pip would compile it and
    # needs Microsoft C++ Build Tools), and removing 0.64 deletes files of inference_sdk,
    # so the new packages are reinstalled over the top.
    optional_step = ("mamba install -n ghostvision -c conda-forge pycuda, then "
                     "pip install --upgrade inference-gpu, then reinstall inference-sdk, "
                     "inference-gpu and inference-models",
                     "Keeps inference-gpu at 1.x after PINGInstaller updates the env. "
                     "Default on. Turn it off to keep whatever PINGInstaller installs.",
                     "withinference")

    def export(self, scan, slug, gs=None, vent_scale=1.0):
        """Tiles of a scan image at the settings' scale, with its truth.
        Returns (tile count, folder)."""
        gs = gs or load_settings()
        mode = gs["mode"]
        px = scan.px_m if mode == "honest" else gs["declared_px_m"]
        return export_gray(scan.gray, slug, mode, px, scan.truth, gs, vent_scale, scan.px_m), TILES / mode

    def export_scene(self, scan, settings, slug, opts=None):
        import sonar_core as C
        for mode, px in (("honest", scan.px_m), ("declared", 0.1)):
            k = export_gray(scan.gray, slug, mode, px, scan.truth, opts,
                            getattr(settings, "vent_scale", 1.0), scan.px_m)
        return f"{settings.scene}: {k} tiles per mode, {C.mound_count(scan, settings)} mounds in image"

    def run_args(self, opts=None, run_dir=None):
        """Launcher arguments; with run_dir, GhostVision runs on that run folder only."""
        args = [f"Run {self.name}.cmd", *launcher_args(opts or load_settings())]
        return args + [str(run_dir)] if run_dir else args

    # ---------- run folders: one displayed render per run ----------

    def export_run(self, scan, slug, render, gs=None, stamp=None):
        """Tiles of one scan image, in the settings' mode, into a new run
        folder RUNS/<slug>_<stamp>/ with tiles/, pred/ and run.json (slug,
        mode, render settings, GhostVision settings). Returns (tile count, folder)."""
        import time
        gs = gs or load_settings()
        mode = gs["mode"]
        px = scan.px_m if mode == "honest" else gs["declared_px_m"]
        stamp = stamp or time.strftime("%Y%m%d_%H%M%S")
        run = RUNS / f"{slug}_{stamp}"
        vs = render.get("vent_scale", 1.0) if isinstance(render, dict) else 1.0
        n = TI.write(run / "tiles", slug, scan.gray, px, gs["tile_pings"],
                     stride_of(gs, scan.px_m, vs), gs["sides"])
        (run / "pred").mkdir(exist_ok=True)
        Image.fromarray(scan.gray, mode="L").save(run / "render.png")
        if scan.truth is not None:
            D.write_truth(run / "mound.npy", scan.truth)
        (run / RUN_INFO).write_text(json.dumps(dict(slug=slug, mode=mode, render=render, ghostvision=gs,
                                                    export_px_m=scan.px_m, export_shape=list(scan.gray.shape)),
                                               indent=1, default=str))
        return n, run

    def read_run(self, run, shape=None):
        """Boxes of a run folder's CSV in the exported scan image's pixels or,
        with shape (h, w) of the displayed render of the same window, in its
        pixels: scaled by the pixel size ratio about the ship track."""
        run = Path(run)
        rows = read_csv_rows(sorted((run / "pred").glob("*.csv")))
        bx = boxes(TI.load(run / "tiles"), rows)
        try:
            eh, ew = json.loads((run / RUN_INFO).read_text())["export_shape"]
        except (OSError, ValueError, KeyError):
            eh = ew = None
        if shape is not None and eh and (eh, ew) != tuple(shape):
            h, w = shape
            kx, ky = w / ew, h / eh
            bx = [D.Box(b.cx * kx, h // 2 + (b.cy - eh // 2) * ky, b.w * kx, b.h * ky, b.cls, b.conf)
                  for b in bx]
        return D.Detections(bx)

    def find_run(self, slug, render, mode):
        """Newest run folder with results for this slug, render settings and
        mode; None when none match."""
        for run in sorted(RUNS.glob(f"{slug}_*"), key=lambda p: p.name, reverse=True):
            try:
                info = json.loads((run / RUN_INFO).read_text())
            except (OSError, ValueError):
                continue
            if (info.get("slug") == slug and info.get("mode") == mode
                    and info.get("render") == json.loads(json.dumps(render, default=str))
                    and any((run / "pred").glob("*.csv"))):
                return run
        return None

    def summarize_run(self, run, dets=None, truth=None):
        """One run's summary: detections, mounds hit of rendered, confidence range."""
        run = Path(run)
        dets = dets if dets is not None else self.read_run(run)
        if truth is None:
            truth = D.read_truth(run / "mound.npy")
        merged = dets.merged()
        sc = D.score(truth, merged)
        confs = [b.conf for b in dets.boxes]
        return dict(detections=len(dets.boxes), objects=len(merged.boxes), mounds_hit=sc["mounds_hit"],
                    mounds_rendered=sc["mounds_rendered"],
                    conf_min=round(min(confs), 3) if confs else None,
                    conf_max=round(max(confs), 3) if confs else None)

    def tiles(self, mode, slug=None):
        return [t for t in TI.load(TILES / mode) if slug is None or t["scene"] == slug]

    def read(self, slug, mode, shape=None):
        rows = read_csv_rows(sorted((PRED / mode).glob("*.csv")))
        return D.Detections(boxes(self.tiles(mode, slug), rows))

    def truth(self, slug, shape=None):
        return D.read_truth(OUT / f"{slug}_mound.npy", shape)

    def summarize(self):
        """Read GhostVision's detection CSV, merge overlapping boxes, draw one
        circle per merged detection, count mounds the merged detections hit."""
        RESULTS.mkdir(exist_ok=True)
        report = []
        for mode in ("honest", "declared"):
            tiles = self.tiles(mode)
            for scene in sorted({t["scene"] for t in tiles}):
                dets = self.read(scene, mode)
                merged = dets.merged()
                mound = self.truth(scene)
                sc = D.score(mound, merged)
                confs = [b.conf for b in dets.boxes]
                row = dict(mode=mode, scene=scene, tiles=sum(t["scene"] == scene for t in tiles),
                           detections=len(dets.boxes), raw_detections=len(dets.boxes),
                           merged_detections=len(merged.boxes), classes=sorted({b.cls for b in dets.boxes}),
                           conf_min=round(min(confs), 3) if confs else None,
                           conf_max=round(max(confs), 3) if confs else None,
                           mounds_rendered=sc["mounds_rendered"], mounds_hit=sc["mounds_hit"])
                report.append(row)
                print(json.dumps(row))
                rpath = OUT / f"{scene}_render.png"
                if rpath.exists():
                    D.draw_overlay(Image.open(rpath), merged, mound, merged=True).save(
                        RESULTS / f"{mode}_{scene}_circled.png", optimize=True)
        (RESULTS / "detections.json").write_text(json.dumps(report, indent=2))
