"""Render core for Sonar Studio. No GUI code here.

Mountain and trench terrain comes from terrain.py, vent fields from
vent_shapes.py. Side-scan shading, hillshade and grain take pixel size and
swath as arguments, so render threads share no module state.
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter

import terrain as P
import vent_shapes as V

BIG = 2_000_000.0
# name -> (areas, origin of the ship track in world meters)
SCENES = {
    "mountain front": ([("mountain", (0.0, 1_006_000.0, BIG, 1_300_000.0))], (1_200_000.0, 1_000_000.0)),
    "mountain interior": ([("mountain", (0.0, 1_006_000.0, BIG, 1_300_000.0))], (1_200_000.0, 1_100_000.0)),
    "trench edge": ([("trench", (0.0, 504_000.0, BIG, 700_000.0))], (900_000.0, 500_000.0)),
    "vent field": ([("vent", (0.0, 0.0, BIG, BIG))], (1_500_000.0, 1_400_000.0)),
    "plain floor": ([], (300_000.0, 300_000.0)),
}
PIXEL_SIZES_M = (5, 10, 20, 50, 100, 200)
PALETTES = tuple(P.PALETTES)
VENT_PER_KM2_MAX = V.PER_KM2_MAX
VENT_SCALE_MIN, VENT_SCALE_MAX = V.SCALE_MIN, V.SCALE_MAX
VENT_HEIGHT_MIN_M, VENT_HEIGHT_MAX_M = V.HEIGHT_MIN_M, V.HEIGHT_MAX_M


def generate(x, y, areas, seed, per_km2, scale, height_m=None):
    """Terrain from terrain.generate plus vent fields from vent_shapes.
    Returns (z, backscatter, looks, mound height m, dominating mound peak m,
    dominating mound id; 0 off every mound).
    Vent frequency, size and height use the slider limits (vent_shapes.clamp)."""
    if height_m is None:
        height_m = V.RULES["mound_height_m"]
    per_km2, scale, height_m = V.clamp(per_km2, scale, height_m)
    others = [a for a in areas if a[0] != "vent"]
    z, bs, looks = P.generate(x, y, others, seed)
    mound = np.zeros_like(x)
    peak = np.zeros_like(x)
    ids = np.zeros_like(x)
    vents = [box for kind, box in areas if kind == "vent"]
    if not vents:
        return z, bs, looks, mound, peak, ids
    # Same ripple as terrain.generate; hard vent ground carries none.
    ripple = 0.6 * np.sin((x * 0.6 + y * 0.8) / 40.0 * 2 * np.pi
                          + 3 * P.fbm(x, y, seed + 9, 600.0, 3))
    dz, hard, m, pk, mid = V.vent_field(x, y, seed, per_km2, scale, height_m=height_m,
                                        with_peak=True, with_id=True)
    for box in vents:
        sd = P.box_signed_distance(x, y, box)
        if sd.min() <= P.EDGE_WARP_M:
            # The edge warp (at most EDGE_WARP_M) only matters near the box edge.
            sd = sd + P.EDGE_WARP_M * P.fbm(x, y, seed + 50, 6000.0, 6)
        inside = (sd >= 0).astype(float)
        z = z + (dz - ripple * hard) * inside
        t = hard * inside
        bs = bs + (V.RULES["backscatter"] - bs) * t
        looks = looks + (V.RULES["looks"] - looks) * t
        mound = np.maximum(mound, m * inside)
        peak = np.maximum(peak, pk * inside)
        ids = np.maximum(ids, mid * inside)
    return z, bs, looks, mound, peak, ids


@dataclass
class Settings:
    scene: str = "mountain front"
    seed: int = 7
    px_m: float = 100.0
    strip_km: float = 60.0
    swath_km: float = 10.0
    ship_depth_m: float = 0.0       # ship below the ice, meters
    track_offset_m: float = 0.0     # ship track shift across track, meters
    palette: str = "cyan"
    grain: bool = True
    grain_strength: float = 0.35
    shadows: bool = True
    view: str = "side-scan"         # or "truth hillshade"
    boxes: bool = False
    vent_per_km2: float = V.RULES["mounds_per_km2"]   # vent mound clusters per km2
    vent_scale: float = 1.0                             # vent mound and fissure width multiplier
    vent_height_m: float = V.RULES["mound_height_m"]  # vent mound peak height, meters

    def speckle(self):
        """Random speckle only with grain off. With grain on, grain alone
        draws the speckle: speckle at 2 looks per 5 m pixel hid vent mounds
        below 50 m pixels."""
        return not (self.grain and self.grain_strength > 0)

    def floor(self, x, y, areas):
        """(z, backscatter, looks, mound height m, mound peak m, mound id) at world points."""
        return generate(x, y, areas, int(self.seed), self.vent_per_km2, self.vent_scale,
                        self.vent_height_m)


@dataclass
class ScanImage:
    """One render and everything measured on its pixels. image, gray and
    truth share one (height, width) grid; row track_row is the ship track,
    rows above it are port, rows below starboard."""
    image: Image.Image          # RGB as shown: palette, view and area boxes applied
    gray: np.ndarray            # uint8 1..255 intensity (0 stays free for no data)
    truth: np.ndarray           # int32 vent truth: mound label 1..N, 0 off mounds (vent_shapes.truth_labels)
    origin: tuple               # world meters (x, y) of column 0 at the ship track
    px_m: float                 # meters per pixel
    swath_m: float              # range per side, meters
    seconds: float = 0.0
    longest_shadow_m: float = 0.0

    @property
    def track_row(self):
        return self.gray.shape[0] // 2

    def world_box(self, rows=slice(None), cols=slice(None)):
        """World bounds (x0, x1, y0, y1) of pixel centers in a row and column slice."""
        h, w = self.gray.shape
        r = np.arange(h)[rows]
        c = np.arange(w)[cols]
        xs = self.origin[0] + c * self.px_m
        ys = self.origin[1] - self.swath_m + r * self.px_m + self.px_m / 2
        return float(xs.min()), float(xs.max()), float(ys.min()), float(ys.max())


def to_gray8(inten):
    """Tone-mapped intensity to 1..255. Value 0 stays free for no data."""
    return (1 + np.round(P.tone(inten) * 254)).astype(np.uint8)


def mound_count(scan, s, rows=slice(None), cols=slice(None)):
    """Vent mound centers inside a pixel slice of the scan image."""
    areas, _ = SCENES[s.scene]
    per_km2, scale = V.clamp(s.vent_per_km2, s.vent_scale)
    if not any(kind == "vent" for kind, _ in areas) or per_km2 <= 0:
        return 0
    return len(V.mound_centers(*scan.world_box(rows, cols), int(s.seed), per_km2,
                               V.RULES["mound_max_m"] * scale))


def longest_run(mask):
    best = 0
    run = np.zeros(mask.shape[1], dtype=int)
    for row in mask:
        run = np.where(row, run + 1, 0)
        best = max(best, int(run.max()))
    return best


def side_scan(z, bs, looks, ys, seed, px_m, swath_m, shadows=True, speckle=True):
    """Side-scan intensity and the longest shadow (m). The ship runs along
    x at y = 0. z is height relative to the ship (ship at 0), rows of z are
    y (across track)."""
    gy, gx = np.gradient(z, px_m)
    longest = 0.0
    shadow = np.zeros_like(z, dtype=bool)
    if shadows:
        for side in (1, -1):
            rows = np.where(ys * side > 0)[0]
            rows = rows if side > 0 else rows[::-1]
            r = np.abs(ys[rows])[:, None]
            tan_dep = (-z[rows]) / r
            prev = np.minimum.accumulate(tan_dep, axis=0)
            prev = np.vstack([np.full((1, z.shape[1]), np.inf), prev[:-1]])
            sh = tan_dep > prev + 1e-9
            shadow[rows] = sh
            longest = max(longest, longest_run(sh) * px_m)
    nx, ny = -gx, -gy
    nlen = np.sqrt(nx * nx + ny * ny + 1)
    vy = -ys[:, None] * np.ones_like(z)
    vz = -z
    vlen = np.sqrt(vy * vy + vz * vz)
    cos_i = (ny * vy + vz) / (nlen * vlen)
    r = np.abs(ys)[:, None]
    zbar = gaussian_filter(z, 400.0 / px_m)
    cos_flat = -zbar / np.sqrt(r * r + zbar * zbar)
    inten = bs * (np.clip(cos_i, 0, 1) / np.maximum(cos_flat, 0.02)) ** 1.6
    inten = inten / (1.0 + inten * 0.15)
    inten *= 1.0 / (1.0 + 0.3 * (r / swath_m) ** 2)
    rng = np.random.default_rng(seed)
    looks = looks * (px_m / 5.0) ** 2
    if speckle:
        inten *= rng.gamma(looks, 1.0 / looks)
    inten[shadow] *= 0.04
    inten *= P.smoothstep(30.0, 90.0, r)
    return np.clip(inten, 0, None), longest


def grain(shape, origin, px_m, looks, seed, strength):
    """Display grain anchored to world cells one pixel wide."""
    ys, xs = np.indices(shape)
    gx = (xs + int(origin[0] / px_m)).astype(np.int64)
    gy = (ys + int(origin[1] / px_m)).astype(np.int64)
    k = np.maximum(np.round(looks).astype(int), 1)
    total = np.zeros(shape)
    for i in range(int(k.max())):
        e = -np.log(np.clip(P.hash01(gx, gy, seed + 17 * (i + 1)), 1e-6, 1))
        total += np.where(i < k, e, 0)
    g = 1 + strength * (total / k - 1)
    ping = 1 + 0.06 * (P.hash01(gx[0:1, :], np.zeros_like(gx[0:1, :]), seed + 999) * 2 - 1)
    return g * ping


def hillshade(z, px_m):
    gy, gx = np.gradient(z, px_m)
    light = np.array([-1.0, -1.0, 1.4])
    light /= np.linalg.norm(light)
    n = np.stack([-gx, -gy, np.ones_like(z)])
    n /= np.linalg.norm(n, axis=0)
    shade = np.clip(np.tensordot(light, n, axes=1), 0, 1)
    depth = (z - z.min()) / max(np.ptp(z), 1e-6)
    return 0.25 + 0.55 * shade + 0.2 * depth


def draw_boxes(img, areas, origin, px_m, swath_m):
    d = ImageDraw.Draw(img)
    colors = {"mountain": (230, 190, 120), "trench": (120, 160, 255), "vent": (255, 140, 60)}
    h = img.height
    for kind, (x0, y0, x1, y1) in areas:
        a = ((x0 - origin[0]) / px_m, (y0 - origin[1] + swath_m) / px_m)
        b = ((x1 - origin[0]) / px_m, (y1 - origin[1] + swath_m) / px_m)
        d.rectangle([a, b], outline=colors[kind], width=2)
        lx = max(a[0], 0) + 6
        ly = min(max(a[1], 0), h - 20) + 6
        d.text((lx, ly), f"{kind} area", fill=colors[kind])
    d.line([(0, h / 2), (img.width, h / 2)], fill=(120, 240, 100), width=1)
    d.text((6, h / 2 + 4), "ship track", fill=(120, 240, 100))


MAX_PIXELS = 20_000_000   # larger images need several GB of memory


class TooLarge(ValueError):
    pass


def check_size(s):
    """Image width and height in pixels; TooLarge above MAX_PIXELS."""
    w = int(np.ceil(s.strip_km * 1000.0 / s.px_m))
    h = int(np.ceil(2 * s.swath_km * 1000.0 / s.px_m))
    if w * h > MAX_PIXELS:
        raise TooLarge(f"Image would be {w} x {h} px ({w * h / 1e6:.0f} M px), over the "
                       f"{MAX_PIXELS / 1e6:.0f} M px limit. Shorten the strip or the swath.")
    return w, h


def floor_bands(s, X, Y, areas, progress=None, share=1.0):
    """s.floor over row bands on worker threads (numpy releases the GIL).
    Calls progress(done fraction * share) after each band."""
    rows = X.shape[0]
    step = max(1, min(rows, 400_000 // max(X.shape[1], 1)))
    bands = [(r, min(r + step, rows)) for r in range(0, rows, step)]
    out = [np.empty(X.shape) for _ in range(6)]
    done = [0]
    lock = threading.Lock()

    def one(band):
        r0, r1 = band
        for o, part in zip(out, s.floor(X[r0:r1], Y[r0:r1], areas)):
            o[r0:r1] = part
        with lock:
            done[0] += 1
            if progress:
                progress(share * done[0] / len(bands))

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        list(pool.map(one, bands))
    return out


def render(s: Settings, progress=None) -> ScanImage:
    """Render one scan image; terrain generates once. progress(fraction 0
    to 1, stage name) is called from this thread as stages finish; it must
    be thread safe. gray is the side-scan intensity even in the truth
    hillshade view."""
    t0 = time.perf_counter()
    report = progress or (lambda f, stage: None)
    check_size(s)
    areas, base = SCENES[s.scene]
    origin = (base[0], base[1] + s.track_offset_m)
    px, swath = float(s.px_m), s.swath_km * 1000.0
    xs = np.arange(0, s.strip_km * 1000.0, px)
    ys = np.arange(-swath, swath, px) + px / 2
    X, Y = np.meshgrid(xs + origin[0], ys + origin[1])
    report(0.0, "heights")
    z, bs, looks, mound, peak, ids = floor_bands(s, X, Y, areas, lambda f: report(0.8 * f, "heights"))
    report(0.8, "shading and shadows")
    inten, longest = side_scan(z + s.ship_depth_m, bs, looks, ys, int(s.seed),
                               px, swath, s.shadows, speckle=s.speckle())
    if s.grain and s.grain_strength > 0:
        report(0.9, "grain")
        inten = inten * grain(inten.shape, origin, px, looks, int(s.seed), s.grain_strength)
    report(0.95, "tone")
    if s.view == "truth hillshade":
        img = Image.fromarray((hillshade(z, px) * 255).astype(np.uint8)).convert("RGB")
        longest = 0.0
    else:
        img = Image.fromarray(P.to_rgb(P.tone(inten), s.palette))
    if s.boxes:
        draw_boxes(img, areas, origin, px, swath)
    report(1.0, "done")
    return ScanImage(img, to_gray8(inten), V.truth_labels(mound, peak, ids), origin, px, swath,
                     time.perf_counter() - t0, longest)
