"""Headless check: each scene renders at 100 m per pixel and the seed changes output.
Run: python test_render.py   (also writes screenshot.png and screenshot_vents.png)"""
import csv
import dataclasses
import json
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

import sonar_core as C


def mound_stats(per_km2, scale, height_m=None):
    """Mound count, share of area above 1 m and tallest mound (m) over a
    20 km x 20 km vent patch at 20 m per pixel."""
    from scipy.ndimage import label
    xs = np.arange(0, 20_000.0, 20.0)
    X, Y = np.meshgrid(xs, xs)
    _, _, m = C.V.vent_field(X, Y, 10, per_km2, scale, height_m=height_m)
    _, n = label(m > 1.0)
    return n, float((m > 1.0).mean()), float(m.max())


def check_vent_height_and_truth():
    """Size changes width only, height changes height only, and
    the 25 % truth mask splits large vents into separate patches."""
    from scipy.ndimage import label
    xs = np.arange(0, 20_000.0, 20.0)
    X, Y = np.meshgrid(xs, xs)
    _, _, m80, p80 = C.V.vent_field(X, Y, 10, 1.0, 1.0, height_m=80.0, with_peak=True)
    _, _, m160, p160 = C.V.vent_field(X, Y, 10, 1.0, 1.0, height_m=160.0, with_peak=True)
    assert np.array_equal(m80 > 0, m160 > 0), "height changed the footprint"
    assert np.array_equal(C.V.truth_mask(m80, p80), C.V.truth_mask(m160, p160)), "height changed the truth"
    assert abs(m160.max() - 2 * m80.max()) < 1e-6, (m80.max(), m160.max())
    _, _, top_wide = mound_stats(1.0, 2.0)
    _, _, top_one = mound_stats(1.0, 1.0)
    assert abs(top_wide - top_one) < 0.15 * top_one and top_wide <= 80.0, ("size changed height", top_one, top_wide)
    assert C.V.clamp(1, 1, 1) == (1.0, 1.0, 5.0) and C.V.clamp(1, 1, 900)[2] == 400.0
    # Sparse large vents: 0.05 per km2 at size 8 on a 60 km x 20 km patch.
    xs = np.arange(0, 60_000.0, 100.0)
    X, Y = np.meshgrid(xs, np.arange(0, 20_000.0, 100.0))
    _, _, m, p = C.V.vent_field(X, Y, 7, 0.05, 8.0, with_peak=True)
    old_n = label(m > 1.0)[1]
    new = C.V.truth_mask(m, p)
    new_n = label(new)[1]
    assert new_n > old_n and new_n >= 3, ("25 % truth should split sparse large vents", old_n, new_n)
    assert new.mean() < (m > 1.0).mean()
    print(f"ok  vent height: footprint fixed under height, peak fixed under size; "
          f"sparse 0.05/km2 x8 truth {old_n} patches at 1 m -> {new_n} at 25 %, "
          f"cover {(m > 1.0).mean():.2f} -> {new.mean():.2f}")


def check_vents():
    # Small mounds keep overlaps rare, so the count follows the frequency.
    n1, a1, top1 = mound_stats(1.0, 0.25)
    n2, _, _ = mound_stats(2.0, 0.25)
    n0, a0, _ = mound_stats(0.0, 1.0)
    _, a_big, top_big = mound_stats(1.0, 0.5)
    _, _, top_default = mound_stats(1.0, 1.0)
    assert 1.6 < n2 / n1 < 2.4, ("doubling frequency", n1, n2)
    assert n0 == 0 and a0 == 0, "0 per km2 still drew mounds"
    assert a_big > 2.5 * a1, ("size x2 should cover about 4x the area", a1, a_big)
    assert 0.85 < top_big / top1 < 1.15, ("size x2 should keep mound height", top1, top_big)
    assert 0.9 * C.V.RULES["mound_height_m"] < top_default <= C.V.RULES["mound_height_m"], top_default
    assert C.V.clamp(50, 12) == (C.VENT_PER_KM2_MAX, 10.0)
    assert C.V.clamp(1, 0.1) == (1.0, 0.25)
    r = C.render(C.Settings(scene="vent field", px_m=20.0, strip_km=10.0, swath_km=2.0,
                            vent_per_km2=0))
    r2 = C.render(C.Settings(scene="vent field", px_m=20.0, strip_km=10.0, swath_km=2.0))
    assert not np.array_equal(np.asarray(r.image), np.asarray(r2.image)), "vent settings ignored"
    print(f"ok  vents: {n1} mounds at 1/km2, {n2} at 2/km2 (size 0.25), 0 at 0/km2; "
          f"area {a1:.3f} at size 0.25, {a_big:.3f} at size 0.5; tallest {top_default:.0f} m at size 1")


def mound_brute(x, y, seed, per_km2, min_m, max_m, height_m):
    """Mound height over 13 x 13 cells around each point, no reach limit."""
    P = C.P
    cell, _, _ = C.V.mound_cell(per_km2, max_m)
    out = []
    for px_, py_ in zip(x, y):
        cx, cy = int(np.floor(px_ / cell)), int(np.floor(py_ / cell))
        gx, gy = np.meshgrid(np.arange(cx - 6, cx + 7), np.arange(cy - 6, cy + 7))
        gx, gy = gx.astype(np.int64), gy.astype(np.int64)
        mx = (gx + P.hash01(gx, gy, seed)) * cell
        my = (gy + P.hash01(gx, gy, seed + 1)) * cell
        r = 0.5 * (min_m + (max_m - min_m) * P.hash01(gx, gy, seed + 2))
        h = height_m * (0.3 + 0.7 * P.hash01(gx, gy, seed + 3))
        d = np.hypot(px_ - mx, py_ - my)
        out.append(float(np.max(np.where(d < r, h * (1 - (d / r) ** 2), 0.0))))
    return np.array(out)


def check_wide_mounds():
    """Large sizes draw whole mounds: grid and point paths match a search with
    no reach limit. Sparse large (0.5 per km2 at 8x) is the main case."""
    xs = np.arange(-30_000.0, 30_000.0, 100.0)
    X, Y = np.meshgrid(xs, xs[:400])
    for per_km2, scale in ((0.5, 8.0), (0.1, 10.0), (1.0, 1.0), (50.0, 10.0)):
        lo, hi, h = 300.0 * scale, 1500.0 * scale, 80.0 * scale
        t = time.perf_counter()
        grid, _ = C.V.vent_mounds(X, Y, 3, per_km2, lo, hi, h)
        ms = (time.perf_counter() - t) * 1000
        k = np.arange(0, X.size, 997)
        pts = C.V.vent_mounds(X.ravel()[k], Y.ravel()[k], 3, per_km2, lo, hi, h)[0]
        brute = mound_brute(X.ravel()[k], Y.ravel()[k], 3, per_km2, lo, hi, h)
        assert np.allclose(grid.ravel()[k], brute) and np.allclose(pts, brute), (per_km2, scale)
        print(f"ok  wide mounds {per_km2}/km2 at {scale}x: no clipping, {ms:.0f} ms for {X.size} points")
    t = time.perf_counter()
    r = C.render(C.Settings(scene="vent field", vent_per_km2=0.5, vent_scale=8.0))
    assert time.perf_counter() - t < 10.0
    print(f"ok  sparse large vents (0.5/km2 at 8x) render {r.seconds:.2f} s at 100 m per pixel")


def vent_visibility(px_m):
    """Vent mound signal over noise per pixel on the tone-mapped image: the
    spread that vents add (noise-free renders with and without vents) over
    the spread speckle and grain add (with and without noise)."""
    s = C.Settings(scene="vent field", px_m=px_m, strip_km=8.0, swath_km=2.0)
    areas, base = C.SCENES[s.scene]
    sw = s.swath_km * 1000.0
    ys = np.arange(-sw, sw, px_m) + px_m / 2
    X, Y = np.meshgrid(np.arange(0, s.strip_km * 1000.0, px_m) + base[0], ys + base[1])

    def toned(per_km2, noise):
        s.vent_per_km2 = per_km2
        z, bs, lk, *_ = C.floor_bands(s, X, Y, areas)
        i, _ = C.side_scan(z, bs, lk, ys, 7, px_m, sw, True, speckle=noise and s.speckle())
        if noise:
            i = i * C.grain(i.shape, base, px_m, lk, 7, s.grain_strength)
        return C.P.tone(i)

    clean = toned(1.0, False)
    signal = np.std(clean - toned(0.0, False))
    noise = np.std(toned(1.0, True) - clean)
    return signal / noise


def check_vents_visible_at_every_pixel_size():
    ratios = {px: vent_visibility(px) for px in (5.0, 10.0, 20.0, 50.0, 100.0)}
    for px, ratio in ratios.items():
        assert ratio > 0.6, (px, ratio)
    print("ok  vent signal over noise per pixel: "
          + ", ".join(f"{px:.0f} m {r:.2f}" for px, r in ratios.items()))


def check_value_noise():
    x, y = np.meshgrid(np.arange(0, 3000.0, 7) + 1.5e6, np.arange(0, 2000.0, 7) + 1.4e6)
    for wl in (10.0, 100.0, 3000.0):
        for a, b in zip(C.P.value_noise(x / wl, y / wl, 5), C.P.value_noise_direct(x / wl, y / wl, 5)):
            assert np.array_equal(a, b), wl
    print("ok  table value noise matches direct value noise")


def check_size_limit():
    try:
        C.check_size(C.Settings(px_m=5.0))
    except C.TooLarge:
        print("ok  60 km x 20 km at 5 m per pixel is refused (48 M px)")
    else:
        raise AssertionError("no size limit")


def check_ghostvision_settings():
    import ghostvision_adapter as GV
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "gv.json"
        assert GV.load_settings(path) == GV.defaults()
        assert GV.defaults()["confidence"] == 0.01 and GV.defaults()["model"] == "2"
        assert GV.defaults()["px_m"] == 50.0
        path.write_text(json.dumps({"confidence": 0.2, "model": "5"}))   # file saved before px_m
        assert GV.load_settings(path) == {**GV.defaults(), "confidence": 0.2, "model": "5"}
        s = GV.defaults()
        s.update(model="5", confidence="0.05", tile_pings="300", sides="port", export_video="1")
        saved = GV.save_settings(s, path)
        assert saved["confidence"] == 0.05 and saved["tile_pings"] == 300 and saved["export_video"] is True
        loaded = GV.load_settings(path)
        assert loaded == saved
        for bad in (dict(confidence=1.5), dict(tile_pings=10), dict(sides="up")):
            try:
                GV.save_settings({**loaded, **bad}, path)
            except ValueError:
                pass
            else:
                raise AssertionError(bad)
        flags, rest = GV.settings_args(["export", "--confidence", "0.3", "--model", "ghostvision-models/1",
                                        "--mode", "declared"], loaded)
        assert rest == ["export"] and flags["confidence"] == 0.3 and flags["model"] == "1"
        assert flags["tile_pings"] == 300, "flags override only what they name"
        assert GV.launcher_args(flags) == ["ghostvision-models/1", "0.3", "0.5", "1", "1", "declared"]
    print("ok  GhostVision settings save, load, refuse bad values and take flag overrides")


def check_ghostvision_overlay():
    """One synthetic box per tile, placed on a bright square in each tile,
    must map back onto the square in the render."""
    import ghostvision_adapter as GV
    old = GV.OUT, GV.TILES, GV.PRED, GV.RESULTS
    with tempfile.TemporaryDirectory() as d:
        GV.OUT = Path(d)
        GV.TILES, GV.PRED, GV.RESULTS = GV.OUT / "tiles", GV.OUT / "pred", GV.OUT / "results"
        try:
            gray = np.full((300, 1400), 40, np.uint8)
            spots = [(120, 650), (230, 900)]          # (row, col): port side, starboard side
            for r, c in spots:
                gray[r - 3:r + 4, c - 3:c + 4] = 250
            truth = gray > 200
            gs = {**GV.defaults(), "tile_pings": 500, "stride_pings": 300}
            n = GV.export_gray(gray, "fixture", "honest", 5.0, truth, gs)
            import tile_index as TI
            tiles = TI.load(GV.TILES / "honest")
            (GV.PRED / "honest").mkdir(parents=True)
            rows = []
            for t in tiles:
                img = np.asarray(Image.open(GV.TILES / "honest" / t["name"]))
                if (img > 200).sum() < 49:
                    continue    # no whole spot in this tile
                ty, tx = (float(v.mean()) for v in np.nonzero(img > 200))   # spot center
                rows.append({"name_long": f"x/{t['name']}", "x": tx + 0.5, "y": ty + 0.5,
                             "width": 8, "height": 8, "class_name": "mound", "confidence": 0.9})
            with open(GV.PRED / "honest" / "detections.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            dets = [(b.cx, b.cy) for b in GV.GhostVision().read("fixture", "honest").boxes]
            assert len(dets) == len(rows) >= len(spots), (len(dets), len(rows))
            assert {t["side"] for t in tiles if any(r["name_long"].endswith(t["name"]) for r in rows)}                 == {"port", "star"}
            for cx, cy in dets:
                assert any(abs(cx - (c + 0.5)) <= 1 and abs(cy - (r + 0.5)) <= 1 for r, c in spots), (cx, cy)
            GV.GhostVision().summarize()
            assert (GV.RESULTS / "honest_fixture_circled.png").exists()
            import detector as D
            over = np.asarray(D.draw_overlay(Image.fromarray(gray), GV.GhostVision().read("fixture", "honest"), truth))
            assert (over == D.DET_COLOR).all(axis=2).any() and (over == D.TRUTH_COLOR).all(axis=2).any()
        finally:
            GV.OUT, GV.TILES, GV.PRED, GV.RESULTS = old
    print(f"ok  GhostVision overlay: {len(dets)} synthetic boxes in {n} tiles map back onto their spots")


def check_export_window():
    """Exports use the scan image's window: Sonar Studio's defaults, or the
    CLI flags. The pixel limit holds and tiles grow with strip length."""
    import detector as D
    import tile_index as TI
    d, s = C.Settings(), D.scene_settings("vent field")
    assert (s.px_m, s.strip_km, s.swath_km) == (d.px_m, d.strip_km, d.swath_km)
    assert s.vent_per_km2 == C.V.RULES["mounds_per_km2"] == 0.1, s.vent_per_km2
    window, rest = D.window_args(["export", "--px", "20", "--strip-km", "8", "--swath-km", "2", "--vents", "3"])
    assert rest == ["export"] and window == dict(px_m=20.0, strip_km=8.0, swath_km=2.0, vent_per_km2=3.0)
    D_SCENES = D.SCENES
    D.SCENES = ("plain floor",)
    try:
        fake = FakeTool()
        D.export_scenes(fake, **window)
        assert fake.scans["plain_floor"].gray.shape == (200, 400)
        try:
            D.export_scenes(fake, px_m=5.0)
        except C.TooLarge:
            pass
        else:
            raise AssertionError("no pixel limit on exports")
    finally:
        D.SCENES = D_SCENES
    short = sum(1 for _ in TI.cut(np.zeros((200, 200), np.uint8)))
    long_ = sum(1 for _ in TI.cut(np.zeros((200, 600), np.uint8)))
    assert long_ > short, (short, long_)
    print(f"ok  export window: Sonar Studio defaults, flags set it, pixel limit holds; "
          f"{short} tiles at 20 km, {long_} at 60 km")


def check_truth_per_mound():
    """Overlapping mounds keep their own truth label, outline and hit."""
    import detector as D
    from scipy import ndimage
    s = C.Settings(scene="vent field", px_m=50.0, strip_km=20.0, swath_km=5.0, vent_per_km2=3.0,
                   vent_scale=2.0)
    r = C.render(s)
    n_labels = len(set(np.unique(r.truth)) - {0})
    _, n_patches = ndimage.label(r.truth > 0)
    assert n_labels > n_patches, ("overlapping mounds merged", n_labels, n_patches)
    # Two touching mounds: one box on each scores two hits.
    edge = D.mask_edges(r.truth)
    a, b = None, None
    for lab in range(1, n_labels + 1):
        grown = ndimage.binary_dilation(r.truth == lab)
        others = set(np.unique(r.truth[grown])) - {0, lab}
        if others:
            a, b = lab, min(others)
            break
    assert a is not None, "no touching mounds in the test render"
    boxes = []
    for lab in (a, b):
        rr, cc = np.nonzero(r.truth == lab)
        i = len(rr) // 2
        boxes.append(D.Box(cc[i] + 0.5, rr[i] + 0.5, 0.2, 0.2, "vent", 0.9))
    sc = D.score(r.truth, D.Detections(boxes))
    assert sc["mounds_rendered"] == n_labels and sc["mounds_hit"] == 2, sc
    # The outline runs between touching mounds, not only around the merged patch.
    merged = D.mask_edges(r.truth > 0)
    assert (edge & ~merged).any(), "no outline between touching mounds"
    for lab in (a, b):
        assert (edge & (r.truth == lab)).any()
    print(f"ok  vent truth per mound: {n_labels} mounds in {n_patches} touching patches, "
          f"each outlined and scored on its own")


def check_tile_index():
    """A box at a known scan image point maps back within 1 px on
    both sides of the track; old indexes (no version, no render_h) load."""
    import tile_index as TI
    gray = np.full((240, 900), 30, np.uint8)
    points = [(37, 610), (60, 140), (119, 300), (120, 455), (175, 820), (239, 5)]   # (row, col)
    with tempfile.TemporaryDirectory() as d:
        n = TI.write(d, "rt", gray, 5.0, tile_pings=300, stride_pings=150)
        tiles = TI.load(d)
        assert json.loads((Path(d) / TI.INDEX).read_text())["version"] == TI.VERSION
        assert len(tiles) == n and {t["side"] for t in tiles} == {"port", "star"}
        h = gray.shape[0] // 2
        for r, c in points:
            side = "port" if r < h else "star"
            y = (h - 1 - r) + 0.5 if side == "port" else (r - h) + 0.5   # pixel center in the tile
            for t in (t for t in tiles if t["side"] == side and t["offset"] <= c < t["offset"] + 300):
                cx, cy = TI.to_scan(t, c - t["offset"] + 0.5, y)
                assert abs(cx - (c + 0.5)) <= 1 and abs(cy - (r + 0.5)) <= 1, (r, c, t, cx, cy)
        # Version 1: a bare list; one entry without render_h uses img_height.
        old = [{k: v for k, v in t.items() if k != "render_h"} if i == 0 else t for i, t in enumerate(tiles)]
        (Path(d) / TI.INDEX).write_text(json.dumps(old))
        loaded = TI.load(d)
        assert loaded == old
        assert TI.to_scan(loaded[0], 10, 20, img_height="120.0") == TI.to_scan(tiles[0], 10, 20)
        TI.write(d, "other", gray[:, :300], 5.0, tile_pings=300, stride_pings=150)
        assert TI.load(d)[:len(old)] == old, "rewrite kept old entries"
    print(f"ok  tile index: {len(points)} points map back within 1 px on both sides; "
          "version 1 index and missing render_h load")


def check_typed_values():
    from slider_entry import parse_value
    assert parse_value("0.05", 0.0, 10.0) == 0.05      # finer than any step, kept
    assert parse_value(" 3,5 ", 0.0, 10.0) == 3.5
    assert parse_value("12", 0.25, 10.0) == 10.0       # clamped high
    assert parse_value("-1", 0.25, 10.0) == 0.25       # clamped low
    for bad in ("", "abc", "nan", "inf", "1..2"):
        assert parse_value(bad, 0.0, 1.0) is None, bad
    print("ok  typed slider values: parse, clamp, reject non-numbers")


def check_process_log():
    import sys
    import proc_log
    lines, done = [], threading.Event()

    def on_line(l):
        lines.append(l)
        if l.startswith("[exit code"):
            done.set()
    code = "import sys; print('out 1'); print('err 2', file=sys.stderr); sys.exit(3)"
    proc_log.start([sys.executable, "-c", code], Path(__file__).parent, on_line)
    assert done.wait(20), lines
    assert sorted(lines[:2]) == ["err 2", "out 1"] and lines[2] == "[exit code 3]", lines
    print("ok  process log: stdout and stderr captured line by line, exit code last")


def check_upgrade_helpers():
    import sys
    import detector as D
    import ghostvision_adapter as GA
    import rockmapper_adapter as RA
    rm, gv = RA.RockMapper(), GA.GhostVision()
    assert rm.upgrade_steps() == ["pip install --force-reinstall pinginstaller",
                                  "python -m pinginstaller rockmapper"]
    assert gv.upgrade_steps()[1] == "python -m pinginstaller ghostvision-gpu" and len(gv.upgrade_steps()) == 2
    assert "pip install --upgrade inference-gpu" in gv.upgrade_steps(optional=True)[2]
    assert gv.upgrade_args() == ["Upgrade GhostVision.cmd", "nopause"]
    assert gv.upgrade_args(True)[-1] == "withinference" and rm.upgrade_args(True)[-1] == "nopause"
    assert "withinference" in gv.upgrade_launcher().read_text()
    cmd = gv.version_command(conda="conda")
    assert cmd[:5] == ["conda", "run", "-n", "ghostvision", "python"] and "'ghostvision'" in cmd[-1], cmd
    assert rm.upgrade_launcher().exists() and gv.upgrade_launcher().exists()
    for tool in (rm, gv):
        assert tool.env in tool.confirm_text()
        assert (D.HERE / tool.run_args()[0]).exists(), tool.run_args()
    fake = [sys.executable, "-c", "print('noise'); print('1.2.3')"]
    assert D.read_version(fake) == "1.2.3"
    assert D.read_version([sys.executable, "-c", "import sys; sys.exit(2)"]) == "unknown (exit code 2)"
    assert D.read_version(["no_such_program_xyz"]).startswith("unknown (")
    print("ok  upgrade: commands, launchers and version reading (fake command)")


class FakeTool:
    """Fake adapter at the detector seam: export keeps the scan image in
    memory, read answers with one box inside mound 1 and a class
    raster that marks truth as class 5."""
    name = "Fake"

    def __init__(self):
        self.scans = {}

    def export_scene(self, scan, settings, slug, opts=None):
        self.scans[slug] = scan
        return f"{slug}: kept"

    def read(self, slug, mode, shape):
        import detector as D
        from scipy import ndimage
        t = self.scans[slug].truth
        boxes = []
        if t.any():
            rr, cc = np.nonzero(D.labels(t) == 1)
            gr, gc = np.nonzero(D.labels(t) == 0)
            boxes = [D.Box(cc[0] + 0.5, rr[0] + 0.5, 0.2, 0.2, "vent", 0.8),
                     D.Box(gc[0] + 0.5, gr[0] + 0.5, 0.2, 0.2, "vent", 0.1)]
        return D.Detections(boxes, np.where(t, 5, 2).astype(np.uint8), {2: (0, 0, 255), 5: (255, 255, 0)})


def check_detector_seam():
    """A fake adapter drives the scene loop, scoring and overlay."""
    import detector as D
    D_SCENES = D.SCENES
    D.SCENES = ("vent field", "plain floor")
    try:
        fake = FakeTool()
        lines = D.export_scenes(fake, vent_per_km2=1.0, px_m=50.0, strip_km=10.0, swath_km=3.0)
    finally:
        D.SCENES = D_SCENES
    assert lines == ["vent_field: kept", "plain_floor: kept"]
    scan = fake.scans["vent_field"]
    dets = fake.read("vent_field", "honest", scan.gray.shape)
    sc = D.score(scan.truth, dets)
    assert sc["mounds_rendered"] >= 1 and sc["mounds_hit"] == 1, sc
    assert sc["mound_class"] == 5 and sc["patches_touching_mounds"] >= 1, sc
    assert len(dets.above(0.5).boxes) == 1
    plain = fake.scans["plain_floor"]
    assert D.score(plain.truth, fake.read("plain_floor", "honest", None)) == {"mounds_rendered": 0, "mounds_hit": 0}
    over = np.asarray(D.draw_overlay(scan.image, dets, scan.truth))
    assert (over == D.DET_COLOR).all(axis=2).any() and (over == D.TRUTH_COLOR).all(axis=2).any()
    assert not np.array_equal(over, np.asarray(scan.image.convert("RGB"))), "raster tint missing"
    with tempfile.TemporaryDirectory() as d:
        D.write_truth(Path(d) / "t.npy", scan.truth)
        assert np.array_equal(D.read_truth(Path(d) / "t.npy", scan.truth.shape), scan.truth)
        assert D.read_truth(Path(d) / "t.npy", (1, 1)) is None and D.read_truth(Path(d) / "x.npy") is None
    print(f"ok  detector seam: fake adapter drives scene loop, scoring ({sc['mounds_hit']} of "
          f"{sc['mounds_rendered']} mounds hit) and overlay")


TESTDATA = Path(__file__).with_name("testdata")


def check_recorded_tool_output():
    """Contract tests on recorded real tool output: a slice of a
    GhostVision detections.csv with its tiles.json, and a slice of a
    RockMapper class mosaic with the GeoTIFF it classified."""
    import ghostvision_adapter as GA
    import rockmapper_adapter as RA
    rows = GA.read_csv_rows([TESTDATA / "ghostvision_detections.csv"])
    tiles = json.loads((TESTDATA / "ghostvision_tiles.json").read_text())
    boxes = GA.boxes(tiles, rows)
    assert len(boxes) == 20 and all(b.cls == "Crab-Pot" and 0 < b.conf < 1 for b in boxes)
    for t in tiles:
        h = t["render_h"] // 2
        mine = GA.boxes([t], rows)
        assert mine, t["name"]
        for b in mine:
            assert t["offset"] <= b.cx <= t["offset"] + 500
            assert (0 <= b.cy <= h) if t["side"] == "port" else (h <= b.cy <= 2 * h), (t, b)
    cls = RA.sample_classes(TESTDATA / "rockmapper_mosaic_vent_field.tif",
                            TESTDATA / "rockmapper_input_vent_field.tif")
    assert cls.shape == (10, 20) and cls.dtype == np.uint8
    # The real mosaic has no-data seams between its 18 m windows.
    assert set(np.unique(cls)) <= set(RA.CLASS_NAMES) and (cls > 0).mean() > 0.4, np.unique(cls)
    # RockMapper summary on the recorded mosaic, in a temporary folder.
    import shutil
    old = RA.OUT, RA.INPUT, RA.RESULTS
    with tempfile.TemporaryDirectory() as d:
        RA.OUT = Path(d)
        RA.INPUT, RA.RESULTS = RA.OUT / "input", RA.OUT / "results"
        try:
            (RA.INPUT / "honest").mkdir(parents=True)
            shutil.copy(TESTDATA / "rockmapper_input_vent_field.tif", RA.INPUT / "honest" / "vent_field.tif")
            mdir = RA.OUT / "pred" / "honest" / "vent_field" / "mosaic"
            mdir.mkdir(parents=True)
            shutil.copy(TESTDATA / "rockmapper_mosaic_vent_field.tif", mdir / "vent_field.tif")
            truth = np.zeros((10, 20), bool)
            truth[2:5, 4:8] = True
            import detector as D
            D.write_truth(RA.truth_path("honest", "vent_field"), truth)
            (RA.INPUT / "meta.json").write_text(json.dumps({"vent_field": dict(scene="vent field", mounds=1, mounds_crop=1)}))
            RA.RockMapper().summarize()
            row = json.loads((RA.RESULTS / "class_fractions.json").read_text())[0]
            assert row["mode"] == "honest" and row["mound_class"] in RA.CLASS_NAMES.values(), row
            assert (RA.RESULTS / "honest_vent_field.png").exists()
        finally:
            RA.OUT, RA.INPUT, RA.RESULTS = old
    print(f"ok  recorded tool output: {len(boxes)} GhostVision boxes land on their tile's side; "
          f"RockMapper mosaic samples onto the 20 x 10 px input grid, classes "
          f"{sorted(int(k) for k in np.unique(cls))}")


def check_auto_stride():
    """Auto tile stride keeps 3 x the largest mound inside one tile."""
    import ghostvision_adapter as GA
    assert GA.defaults()["stride_pings"] == "auto" and GA.check("stride_pings", "Auto") == "auto"
    assert GA.auto_stride(500, 50.0) == 410
    assert GA.auto_stride(500, 50.0, 2.0) == 320
    assert GA.auto_stride(500, 5.0) == 10 and GA.auto_stride(50, 50.0, 4.0) == 10
    assert GA.stride_of({**GA.defaults(), "stride_pings": 100}, 50.0) == 100
    assert GA.iou_warning({"iou": 1.0}).startswith("IoU 1.0 keeps overlapping boxes. Default 0.5")
    assert GA.iou_warning({"iou": 0.5}) == ""
    print("ok  auto stride: 410 pings at defaults, 320 at vent size 2, never below 10; IoU warning")


def check_merge_detections():
    """Overlapping boxes from several tiles merge to one per object, using
    the boxes only. The confidence floor applies before the merge."""
    import detector as D
    # Five tiles see one point; each crops the box a little differently.
    five = [D.Box(300 + dx, 100 + dy, 20 + dw, 18, "mound", c) for dx, dy, dw, c in
            [(0, 0, 0, 0.4), (2, -1, 4, 0.7), (-2, 1, -2, 0.3), (3, 0, 0, 0.9), (-1, 2, 2, 0.5)]]
    m = D.merge_boxes(five)        # no truth argument exists, none is read
    assert len(m) == 1 and m[0].count == 5 and m[0].conf == 0.9, m
    assert 299 < m[0].cx < 303 and 99 < m[0].cy < 102 and m[0].w == 20, m
    far = [D.Box(100, 100, 20, 20, "mound", 0.5), D.Box(160, 100, 20, 20, "mound", 0.6)]
    assert len(D.merge_boxes(far)) == 2
    # Floor first: at 0.6 only two of the five pass, and they merge to count 2.
    dets = D.Detections(five)
    assert dets.above(0.6).merged().boxes[0].count == 2
    assert dets.merged().boxes[0].count == 5
    img = D.draw_overlay(Image.new("RGB", (400, 200)), dets.merged(), merged=True)
    assert (np.asarray(img) == D.DET_COLOR).all(axis=2).any()
    # Recorded GhostVision fixture: merged hit scoring.
    import ghostvision_adapter as GA
    rows = GA.read_csv_rows([TESTDATA / "ghostvision_detections.csv"])
    tiles = json.loads((TESTDATA / "ghostvision_tiles.json").read_text())
    raw = D.Detections(GA.boxes(tiles, rows))
    merged = raw.merged()
    assert 0 < len(merged.boxes) <= len(raw.boxes)
    assert sum(b.count for b in merged.boxes) == len(raw.boxes)
    h = max(t["render_h"] for t in tiles)
    w = max(t["offset"] for t in tiles) + 500
    truth = np.zeros((h, w), np.int32)
    for k, b in enumerate(merged.boxes, 1):
        truth[int(b.cy), int(b.cx)] = k
    sc_raw, sc_m = D.score(truth, raw), D.score(truth, merged)
    assert sc_m["mounds_hit"] == sc_raw["mounds_hit"] == len(merged.boxes), (sc_raw, sc_m)
    print(f"ok  merge: 5 overlapping boxes -> 1 (count 5, conf 0.9); recorded fixture "
          f"{len(raw.boxes)} raw -> {len(merged.boxes)} merged, {sc_m['mounds_hit']} mounds hit")


def check_gui_ghostvision_run():
    """Run GhostVision in the window: one run folder for the displayed
    render only, a fake launcher that writes the recorded CSV fixture,
    detections mapped onto the displayed image, re-render clears them,
    Load picks the run that matches the render."""
    import tkinter as tk
    import ghostvision_adapter as GA
    import sonar_studio as S
    import tile_index as TI
    old = GA.OUT, GA.RUNS, GA.SETTINGS_PATH
    root = tk.Tk()
    root.withdraw()
    with tempfile.TemporaryDirectory() as d:
        GA.OUT = Path(d)
        GA.RUNS, GA.SETTINGS_PATH = GA.OUT / "runs", GA.OUT / "gv.json"
        # Recorded rows were placed for 100-ping strides.
        GA.save_settings({**GA.defaults(), "mode": "declared", "stride_pings": 100, "iou": 1.0})
        try:
            app = S.App(root)
            s = C.Settings(scene="vent field", px_m=100.0, seed=252432, vent_per_km2=0.5, vent_scale=4.0)
            res = C.render(s)
            app.done(res, None, s)
            assert not app.dets["GhostVision"].boxes
            recorded = list(csv.DictReader(open(TESTDATA / "ghostvision_detections.csv", newline="")))
            launched = []

            class Done:
                returncode = 0

                def poll(self):
                    return 0

            def fake_launch(tool, args):
                run = Path(args[-1])
                launched.append(args)
                tiles = TI.load(run / "tiles")
                lab = np.load(run / "mound.npy")       # truth of the 50 m export
                assert lab.shape == (400, 1200), lab.shape
                h = lab.shape[0] // 2
                r0, c0 = (int(v[0]) for v in np.nonzero(lab == lab.max()))   # one pixel of a mound
                rows = []
                for i, r in enumerate(recorded):
                    # Recorded rows come from 200 x 200 px tiles: scale to these tiles.
                    k = h / float(r["img_height"])
                    rows.append({**r, "name_long": tiles[i % len(tiles)]["name"], "img_height": h,
                                 "x": float(r["x"]) * k, "y": float(r["y"]) * k})
                # One box on the mound pixel, in its tile's coordinates.
                side = "port" if r0 < h else "star"
                t = next(t for t in tiles if t["side"] == side and t["offset"] <= c0 < t["offset"] + 500)
                rows.append({**recorded[0], "name_long": t["name"], "x": c0 - t["offset"] + 0.5,
                             "y": (h - r0 if side == "port" else r0 - h) + 0.5, "confidence": 0.9})
                with open(run / "pred" / "detections.csv", "w", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=list(rows[0]))
                    w.writeheader()
                    w.writerows(rows)
                return Done()
            app.launch = fake_launch
            deadline = time.time() + 30

            def wait():     # the export thread hands back through the Tk main loop
                if app.dets["GhostVision"].boxes or time.time() > deadline:
                    root.quit()
                else:
                    root.after(50, wait)
            root.after(0, app.run_gv)
            root.after(50, wait)
            root.mainloop()
            runs = list(GA.RUNS.iterdir())
            assert len(launched) == 1 and len(runs) == 1, (launched, runs)
            run = runs[0]
            assert launched[0][-1] == str(run) and launched[0][-2] == "declared"
            assert launched[0][2] == "0.01", launched[0]
            info = json.loads((run / "run.json").read_text())
            assert info["slug"] == "vent_field_seed252432" and info["mode"] == "declared"
            names = {t["scene"] for t in TI.load(run / "tiles")}
            assert names == {"vent_field_seed252432"}, names
            boxes = app.dets["GhostVision"].boxes
            hgt, wid = res.gray.shape
            assert len(boxes) == len(recorded) + 1
            assert all(0 <= b.cx <= wid and 0 <= b.cy <= hgt for b in boxes), ([(b.cx, b.cy) for b in boxes], wid, hgt, [(t["offset"], t["side"]) for t in TI.load(run / "tiles")])
            status = app.status.get()
            import detector as D
            nm = len(D.merge_boxes(boxes))
            assert status.startswith(f"{nm} objects from {len(boxes)} raw detections, ")                 and "mounds hit" in status, status
            assert ", 0 of" not in status and "confidence" in status, status
            assert status.endswith("IoU 1.0 keeps overlapping boxes. Default 0.5 (Reset to defaults)."), status
            assert len(app.shown_dets().boxes) == nm
            app.merge_dets.set(False)
            assert len(app.shown_dets().boxes) == len(boxes)
            app.merge_dets.set(True)
            # Minimum confidence slider filters live.
            app.min_conf.set(0.5)
            assert len(app.shown_dets().boxes) == 1
            app.min_conf.set(0.0)
            # Re-render with another seed: detections clear.
            s2 = dataclasses.replace(s, seed=7)
            app.done(C.render(s2), None, s2)
            assert not app.dets["GhostVision"].boxes
            app.load_gv()
            assert "No GhostVision run matches" in app.status.get()
            # Back to the first render: Load finds the run, not a newer one for other settings.
            other = run.with_name(run.name[:-15] + "99991231_235959")
            import shutil
            shutil.copytree(run, other)
            info2 = {**info, "render": {**info["render"], "palette": "other"}}
            (other / "run.json").write_text(json.dumps(info2))
            app.done(res, None, s)
            assert len(app.dets["GhostVision"].boxes) == len(boxes), "render did not reload its run"
            app.dets["GhostVision"] = type(app.dets["GhostVision"])()
            app.load_gv()
            assert len(app.dets["GhostVision"].boxes) == len(boxes) and run.name in app.status.get()
        finally:
            GA.OUT, GA.RUNS, GA.SETTINGS_PATH = old
            root.destroy()
    print(f"ok  Run GhostVision in the window: one run folder for the displayed render (exported at 50 m, shown at 100 m), "
          f"{len(boxes)} detections on it, re-render clears them, Load picks the matching run")


def check_sections():
    import tempfile
    import sections
    p = Path(tempfile.mkdtemp()) / "sections.json"
    assert sections.load_state(p) == {}
    sections.save_state({"Look": False, "Scene": True}, p)
    assert sections.load_state(p) == {"Look": False, "Scene": True}
    p.write_text("not json")
    assert sections.load_state(p) == {}
    print("ok  sections: open state saves and loads, bad file reads as empty")


def check_tooltips():
    import tkinter as tk
    import sonar_studio as S
    import tooltips as T
    for key, text in T.TIPS.items():
        assert not T.banned_words(text), (key, T.banned_words(text))
    root = tk.Tk()
    root.withdraw()
    try:
        app = S.App(root)
        dialog = app.gv_settings()
        app.logs["RockMapper"].open()
        interactive = {"TButton", "TCheckbutton", "TCombobox", "TSpinbox", "TEntry", "TScale",
                       "TProgressbar", "Text"}
        missing, n = [], 0

        def walk(w):
            nonlocal n
            if w.winfo_class() in interactive:
                n += 1
                if not hasattr(w, "_tip"):
                    missing.append(f"{w.winfo_class()} {w}")
            for c in w.winfo_children():
                walk(c)
        walk(root)
        assert not missing, missing
        headers = [sec.header for sec in app.sections.values()]
        assert len(headers) == 9 and all(hasattr(h, "_tip") for h in headers), headers
        assert hasattr(app.status_label, "_tip") and dialog.winfo_exists()
    finally:
        root.destroy()
    print(f"ok  tooltips: {n} interactive widgets all have one, {len(T.TIPS)} texts pass the word rules")


def check_scan_image():
    """One scan image per render. Image, gray and truth share one
    grid, truth follows the mounds in the pixels, terrain generates once,
    and the plain floor has empty truth."""
    s = C.Settings(scene="vent field", px_m=100.0, strip_km=30, swath_km=10)
    calls = []
    real = C.floor_bands

    def counted(*a, **k):
        calls.append(1)
        return real(*a, **k)
    C.floor_bands = counted
    try:
        r = C.render(s)
    finally:
        C.floor_bands = real
    assert len(calls) == 1, ("terrain generated more than once", len(calls))
    w, h = r.image.size
    assert r.gray.shape == r.truth.shape == (h, w) and r.gray.dtype == np.uint8 and r.gray.min() >= 1
    assert r.truth.any() and r.track_row == h // 2 and r.px_m == 100.0 and r.swath_m == 10_000.0
    # Truth matches the mounds at the scan image's own pixel centers.
    x0, x1, y0, y1 = r.world_box()
    X, Y = np.meshgrid(np.linspace(x0, x1, w), np.linspace(y0, y1, h))
    areas, _ = C.SCENES[s.scene]
    _, _, _, mound, peak, ids = s.floor(X, Y, areas)
    assert np.array_equal(r.truth, C.V.truth_labels(mound, peak, ids)), "truth does not match the pixels"
    assert C.mound_count(r, s) > 0
    hs = C.render(C.Settings(scene="vent field", px_m=100.0, strip_km=30, swath_km=10, view="truth hillshade"))
    assert np.array_equal(hs.gray, r.gray) and np.array_equal(hs.truth, r.truth), "view changed gray or truth"
    p = C.render(C.Settings(scene="plain floor", px_m=100.0, strip_km=30, swath_km=10))
    assert not p.truth.any() and C.mound_count(p, C.Settings(scene="plain floor")) == 0
    print("ok  scan image: image, gray and truth share one grid; truth matches the mounds; "
          "terrain generated once; plain floor truth empty")


def check_tiles_cover_strip_end():
    """Tiles reach the last ping whatever the stride."""
    import tile_index as TI
    gray = np.zeros((40, 1200), np.uint8)
    for stride in (100, 410, 500, 700):
        offs = [off for side, off, t in TI.cut(gray, 500, stride, "star")]
        assert offs[-1] + 500 == 1200, (stride, offs)
        assert len(offs) == len(set(offs))
    print("ok  tiles: the last tile ends at the strip's last ping for strides 100, 410, 500, 700")


def main():
    for scene in C.SCENES:
        r = C.render(C.Settings(scene=scene, px_m=100.0, strip_km=60.0, swath_km=10.0))
        assert r.image.size == (600, 200), (scene, r.image.size)
        other = C.render(C.Settings(scene=scene, px_m=100.0, seed=8)).image
        assert not np.array_equal(np.asarray(r.image), np.asarray(other)), f"{scene}: seed had no effect"
        print(f"ok  {scene:18s} {r.image.size[0]} x {r.image.size[1]} px  "
              f"{r.seconds:.2f} s  longest shadow {r.longest_shadow_m:.0f} m")
    check_vents()
    check_wide_mounds()
    check_vent_height_and_truth()
    check_vents_visible_at_every_pixel_size()
    check_value_noise()
    check_size_limit()
    check_ghostvision_settings()
    check_ghostvision_overlay()
    check_tiles_cover_strip_end()
    check_detector_seam()
    check_tile_index()
    check_export_window()
    check_truth_per_mound()
    check_recorded_tool_output()
    check_scan_image()
    check_typed_values()
    check_process_log()
    check_upgrade_helpers()
    check_sections()
    check_tooltips()
    check_auto_stride()
    check_merge_detections()
    check_gui_ghostvision_run()
    shot = C.render(C.Settings(scene="mountain front", px_m=50.0, boxes=True))
    shot.image.save(Path(__file__).with_name("screenshot.png"))
    vents = C.render(C.Settings(scene="vent field", vent_per_km2=0.5, vent_scale=8.0))
    vents.image.save(Path(__file__).with_name("screenshot_vents.png"))
    print("all passed; wrote screenshot.png and screenshot_vents.png")


if __name__ == "__main__":
    main()
