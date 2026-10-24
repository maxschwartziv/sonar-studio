"""Vent field shapes: mound clusters on a jittered grid and fissures.
The strip never wraps, so there is no map seam to handle.

Vent numbers come from vent_defaults.json next to this file at import.
FALLBACK holds the values used when the file is missing.
"""
import json
from pathlib import Path

import numpy as np

import terrain as P

DEFAULTS = Path(__file__).with_name("vent_defaults.json")
FALLBACK = {
    "mounds_per_km2": 0.1, "mound_min_m": 300.0, "mound_max_m": 1500.0,
    "mound_height_m": 80.0, "fissure_m": 15.0, "fissure_wavelength_m": 4000.0,
    "fissure_width": 0.04, "backscatter": 0.95, "looks": 2.0,
}
# Seed offsets for mounds and fissures.
SEED_VENT = 3
SEED_FISSURE = 12
# Slider limits.
PER_KM2_MAX = 10.0
SCALE_MIN, SCALE_MAX = 0.25, 10.0
HEIGHT_MIN_M, HEIGHT_MAX_M = 5.0, 400.0
# Vent truth: a pixel counts as mound where the mound stands above this
# share of the peak of the mound that dominates the pixel.
TRUTH_SHARE = 0.25
REACH_CELLS = 4.0   # largest mound reach, in grid cells


def load_rules(path=DEFAULTS):
    """Vent numbers from vent_defaults.json over FALLBACK."""
    rules = dict(FALLBACK)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return rules
    for key in rules:
        if key in data:
            rules[key] = float(data[key])
    return rules


RULES = load_rules()


def mound_cell(per_km2, max_m):
    """Grid cell side (m) and search reach in cells. The cell grows when
    mounds reach past REACH_CELLS cells."""
    reach = 0.75 * max_m
    cell = max(1000.0 / np.sqrt(per_km2), reach / REACH_CELLS)
    return cell, max(int(np.ceil(reach / cell)), 1), reach


def vent_mounds(x, y, seed, per_km2, min_m, max_m, height_m):
    """Mound clusters on a jittered grid, one candidate per cell. The search
    covers every cell a mound can reach, so wide mounds stay whole. Each
    mound is a dome min_m to max_m across and 30 % to 100 % of height_m
    tall. Returns (height m, nearness 0 to 1); nearness is 1 on a mound and
    0 at 1.5 times its radius."""
    return vent_mounds_peak(x, y, seed, per_km2, min_m, max_m, height_m)[:2]


def mound_id(gx, gy):
    """Id of the mound in grid cell (gx, gy): unique over 2^20 x 2^20 cells,
    never 0, exact in float64."""
    return (np.asarray(gx, np.int64) % (1 << 20)) * (1 << 20) + (np.asarray(gy, np.int64) % (1 << 20)) + 1


def vent_mounds_peak(x, y, seed, per_km2, min_m, max_m, height_m, with_id=False):
    """vent_mounds plus a third array: the peak height (m) of the mound
    that gives each point its height, 0 off every mound. with_id adds a
    fourth: that mound's id (mound_id of its grid cell), 0 off every mound."""
    if per_km2 <= 0:
        z = np.zeros_like(x)
        return (z, z.copy(), z.copy(), z.copy()) if with_id else (z, z.copy(), z.copy())
    cell, k, reach = mound_cell(per_km2, max_m)
    cx = np.floor(x / cell).astype(np.int64)
    cy = np.floor(y / cell).astype(np.int64)
    # One table entry per grid cell in reach of the points: hashing per
    # cell, not per pixel, keeps fine pixel sizes fast.
    x0, y0 = int(cx.min()) - k, int(cy.min()) - k
    gx, gy = np.meshgrid(np.arange(x0, int(cx.max()) + k + 1, dtype=np.int64),
                         np.arange(y0, int(cy.max()) + k + 1, dtype=np.int64), indexing="ij")
    tpx = (gx + P.hash01(gx, gy, seed)) * cell
    tpy = (gy + P.hash01(gx, gy, seed + 1)) * cell
    trad = 0.5 * (min_m + (max_m - min_m) * P.hash01(gx, gy, seed + 2))
    thgt = height_m * (0.3 + 0.7 * P.hash01(gx, gy, seed + 3))
    tid = mound_id(gx, gy).astype(float)
    best = np.zeros_like(x)
    near = np.zeros_like(x)
    peak = np.zeros_like(x)
    ids = np.zeros_like(x)
    if x.ndim == 2 and np.array_equal(x, np.broadcast_to(x[:1], x.shape))             and np.array_equal(y, np.broadcast_to(y[:, :1], y.shape)):
        # Grid points (a meshgrid): draw each mound into its own box only.
        xs, ys = x[0], y[:, 0]
        for px, py, radius, height, mid in zip(tpx.ravel(), tpy.ravel(), trad.ravel(), thgt.ravel(),
                                               tid.ravel()):
            c0, c1 = np.searchsorted(xs, [px - reach, px + reach])
            r0, r1 = np.searchsorted(ys, [py - reach, py + reach])
            if c0 >= c1 or r0 >= r1:
                continue
            d = np.hypot(xs[None, c0:c1] - px, ys[r0:r1, None] - py)
            u = d / radius
            b = best[r0:r1, c0:c1]
            h = np.where(d < radius, height * (1 - u * u), 0.0)
            pk = peak[r0:r1, c0:c1]
            pk[h > b] = height
            ids[r0:r1, c0:c1][h > b] = mid
            np.maximum(b, h, out=b)
            n = near[r0:r1, c0:c1]
            np.maximum(n, np.where(d < reach, P.smoothstep(radius * 1.5, radius, d), 0.0), out=n)
        return (best, near, peak, ids) if with_id else (best, near, peak)
    ix, iy = cx - x0, cy - y0
    for ox in range(-k, k + 1):
        for oy in range(-k, k + 1):
            i, j = ix + ox, iy + oy
            px, py, radius, height, mid = tpx[i, j], tpy[i, j], trad[i, j], thgt[i, j], tid[i, j]
            d = np.hypot(x - px, y - py)
            u = d / radius
            h = np.where(d < radius, height * (1 - u * u), 0.0)
            peak = np.where(h > best, height, peak)
            ids = np.where(h > best, mid, ids)
            best = np.maximum(best, h)
            near = np.maximum(near, np.where(d < reach, P.smoothstep(radius * 1.5, radius, d), 0.0))
    return (best, near, peak, ids) if with_id else (best, near, peak)


def truth_mask(mound, peak):
    """Vent truth: True where a mound stands above TRUTH_SHARE of its own
    peak height. Shared by the studio overlay, both feed exports and their
    hit counts."""
    return (peak > 0) & (mound > TRUTH_SHARE * peak)


def truth_labels(mound, peak, ids):
    """Vent truth with mound identity: 1..N for each distinct mound above
    TRUTH_SHARE of its own peak, 0 elsewhere. Overlapping mounds keep
    separate labels."""
    mask = truth_mask(mound, peak)
    out = np.zeros(mound.shape, np.int32)
    if mask.any():
        _, inv = np.unique(ids[mask], return_inverse=True)
        out[mask] = inv.ravel() + 1
    return out


def mound_centers(x0, x1, y0, y1, seed, per_km2, max_m):
    """Mound centers (x, y) inside the box, seed as passed to vent_field."""
    if per_km2 <= 0:
        return []
    s = seed + SEED_VENT
    cell, _, _ = mound_cell(per_km2, max_m)
    out = []
    for gx in range(int(np.floor(x0 / cell)) - 1, int(np.floor(x1 / cell)) + 2):
        for gy in range(int(np.floor(y0 / cell)) - 1, int(np.floor(y1 / cell)) + 2):
            ix, iy = np.int64(gx), np.int64(gy)
            px = (gx + float(P.hash01(ix, iy, s))) * cell
            py = (gy + float(P.hash01(ix, iy, s + 1))) * cell
            if x0 <= px <= x1 and y0 <= py <= y1:
                out.append((px, py))
    return out


def clamp(per_km2, scale, height_m=None):
    """Slider limits: frequency 0 to PER_KM2_MAX, scale SCALE_MIN
    to SCALE_MAX, height HEIGHT_MIN_M to HEIGHT_MAX_M. With no height, returns
    (frequency, scale); with one, (frequency, scale, height)."""
    out = (min(max(float(per_km2), 0.0), PER_KM2_MAX),
           min(max(float(scale), SCALE_MIN), SCALE_MAX))
    if height_m is None:
        return out
    return out + (min(max(float(height_m), HEIGHT_MIN_M), HEIGHT_MAX_M),)


def vent_field(x, y, seed, per_km2, scale, rules=None, height_m=None, with_peak=False, with_id=False):
    """Vent field at world points: (height change m, hard ground 0 to 1,
    mound height m), plus the dominating mound's peak (m) with with_peak,
    plus its mound id with with_id (needs with_peak).
    Scale sets width only. height_m (default mound_height_m) sets mound
    height, and fissure depth follows it. Hard ground is at least 0.6
    everywhere in the field."""
    r = RULES if rules is None else rules
    h = r["mound_height_m"] if height_m is None else height_m
    mound, near, peak, ids = vent_mounds_peak(x, y, seed + SEED_VENT, per_km2,
                                              r["mound_min_m"] * scale, r["mound_max_m"] * scale, h,
                                              with_id=True)
    # The scale stretches the noise, so fissures grow longer and wider together.
    n = P.fbm(x, y, seed + SEED_FISSURE, r["fissure_wavelength_m"] * scale, 2)
    fissure = 1.0 - P.smoothstep(0.0, r["fissure_width"], np.abs(n))
    dz = mound - r["fissure_m"] * h / r["mound_height_m"] * fissure
    hard = np.maximum(np.maximum(near, fissure), 0.6)
    if with_id:
        return dz, hard, mound, peak, ids
    return (dz, hard, mound, peak) if with_peak else (dz, hard, mound)
