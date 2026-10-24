"""Seafloor heights, materials, noise and palettes. No GUI code here.

Every height and material value is a function of world position and seed
only, so any patch of floor renders alone and neighboring patches join
without seams. Mountain and trench areas come from here; vent fields come
from vent_shapes.py.
"""
import numpy as np

DEFAULT_DEPTH_M = 1000.0     # plain floor depth below the ship
BLEND_M = 5000.0             # width of the slope from an area's edge to plain floor
EDGE_WARP_M = 1500.0         # largest wander of an area's edge

# Shaping and backscatter for each area kind. Lower looks give grainier speckle.
KINDS = {
    "floor":    dict(relief_m=6.0, backscatter=0.45, looks=8.0),
    "mountain": dict(depth_m=200.0, relief_m=260.0, backscatter=0.85, looks=3.0),
    "trench":   dict(depth_m=2000.0, relief_m=40.0, backscatter=0.40, looks=6.0),
}


# ---------- seeded hash and value noise with derivatives ----------

def hash01(ix, iy, seed):
    """Integer lattice point -> float in [0, 1). Pure function of inputs."""
    h = (ix.astype(np.int64) * 374761393 + iy.astype(np.int64) * 668265263
         + seed * 2147483647) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h = h ^ (h >> 16)
    return h.astype(np.float64) / 4294967296.0


def _blend(a, b, c, d, fx, fy):
    u = fx * fx * fx * (fx * (fx * 6 - 15) + 10)
    v = fy * fy * fy * (fy * (fy * 6 - 15) + 10)
    du = 30 * fx * fx * (fx - 1) ** 2
    dv = 30 * fy * fy * (fy - 1) ** 2
    k = a - b - c + d
    n = a + (b - a) * u + (c - a) * v + k * u * v
    return n, du * ((b - a) + k * v), dv * ((c - a) + k * u)


def value_noise_direct(x, y, seed):
    """Value noise in [-1, 1] and its x, y derivatives (per lattice unit).
    Hashes four lattice corners for every point."""
    ix, iy = np.floor(x), np.floor(y)
    fx, fy = x - ix, y - iy
    ix, iy = ix.astype(np.int64), iy.astype(np.int64)
    a = hash01(ix, iy, seed) * 2 - 1
    b = hash01(ix + 1, iy, seed) * 2 - 1
    c = hash01(ix, iy + 1, seed) * 2 - 1
    d = hash01(ix + 1, iy + 1, seed) * 2 - 1
    return _blend(a, b, c, d, fx, fy)


def value_noise(x, y, seed):
    """Same result as value_noise_direct. When the points are denser than
    the lattice, corner hashes come from a table with one entry per lattice
    point, which is faster."""
    ix, iy = np.floor(x), np.floor(y)
    x0, y0 = int(ix.min()), int(iy.min())
    w, h = int(ix.max()) - x0 + 2, int(iy.max()) - y0 + 2
    if w * h * 4 > x.size:
        return value_noise_direct(x, y, seed)
    gx, gy = np.meshgrid(np.arange(x0, x0 + w, dtype=np.int64),
                         np.arange(y0, y0 + h, dtype=np.int64), indexing="ij")
    table = hash01(gx, gy, seed) * 2 - 1
    i, j = (ix - x0).astype(np.intp), (iy - y0).astype(np.intp)
    return _blend(table[i, j], table[i + 1, j], table[i, j + 1], table[i + 1, j + 1],
                  x - ix, y - iy)


def fbm(x, y, seed, wavelength_m, octaves, eroded=False):
    """Fractal noise, roughly in [-1, 1]. eroded=True damps detail on steep
    ground (derivative-damped fBm), which draws gullies and sharp ridges
    without a whole-area erosion pass."""
    total = np.zeros_like(x)
    dx_sum = np.zeros_like(x)
    dy_sum = np.zeros_like(x)
    amp, norm = 0.5, 0.0
    freq = 1.0 / wavelength_m
    cos_r, sin_r = 0.8, 0.6   # rotate each octave to hide lattice lines
    px, py = x, y
    for o in range(octaves):
        n, ndx, ndy = value_noise(px * freq, py * freq, seed + o * 101)
        if eroded:
            dx_sum += ndx
            dy_sum += ndy
            n = n / (1.0 + dx_sum * dx_sum + dy_sum * dy_sum)
        total += amp * n
        norm += amp
        amp *= 0.5
        freq *= 2.0
        px, py = cos_r * px - sin_r * py, sin_r * px + cos_r * py
    return total / norm


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# ---------- areas: boxes, signed distance in meters ----------

def box_signed_distance(x, y, box):
    """Positive inside, negative outside, meters to the nearest edge."""
    x0, y0, x1, y1 = box
    dx = np.maximum(x0 - x, x - x1)
    dy = np.maximum(y0 - y, y - y1)
    outside = np.hypot(np.maximum(dx, 0), np.maximum(dy, 0))
    inside = np.minimum(np.maximum(dx, dy), 0)
    return np.where(outside > 0, -outside, -inside)


def area_weight(sd):
    """1 inside the area, smoothstep to 0 over BLEND_M outside."""
    return np.where(sd >= 0, 1.0, smoothstep(-BLEND_M, 0.0, sd))


# ---------- height and material, pointwise ----------

def generate(x, y, areas, seed, kinds=None):
    """World coordinates (m) -> floor height z (m, negative down),
    backscatter strength and speckle looks. areas holds (kind, box) pairs
    with kind "mountain" or "trench". kinds overrides KINDS."""
    kinds = KINDS if kinds is None else kinds
    floor = kinds["floor"]
    z = -DEFAULT_DEPTH_M + floor["relief_m"] * fbm(x, y, seed, 2000.0, 8)
    z += 1.5 * fbm(x, y, seed + 4, 150.0, 4)
    # Sediment ripples: low ridges, about 40 m apart.
    ripple = 0.6 * np.sin((x * 0.6 + y * 0.8) / 40.0 * 2 * np.pi
                          + 3 * fbm(x, y, seed + 9, 600.0, 3))
    bs = np.full_like(x, floor["backscatter"])
    looks = np.full_like(x, floor["looks"])
    sediment = np.ones_like(x)    # 1 where loose sediment carries ripples

    for kind, box in areas:
        sd = box_signed_distance(x, y, box)
        # Warp edges so area boxes do not read as ruler lines.
        sd = sd + EDGE_WARP_M * fbm(x, y, seed + 50, 6000.0, 6)
        w = area_weight(sd)
        k = kinds[kind]
        if kind == "mountain":
            rise = DEFAULT_DEPTH_M - k["depth_m"]
            # Steeper front than a plain smoothstep blend, same end depths.
            front = smoothstep(0.0, 1.0, w) ** 1.5
            relief = (k["relief_m"] * fbm(x, y, seed + 1, 12000.0, 11, eroded=True)
                      + 70.0 * fbm(x, y, seed + 5, 1500.0, 8, eroded=True)
                      + 4.0 * fbm(x, y, seed + 6, 60.0, 3))
            z += rise * front + relief * front
            rock = smoothstep(0.15, 0.5, w)
            bs = bs * (1 - rock) + k["backscatter"] * rock
            looks = looks * (1 - rock) + k["looks"] * rock
            sediment *= 1 - rock
        elif kind == "trench":
            drop = k["depth_m"] - DEFAULT_DEPTH_M
            # Terraced wall: fault steps inside the blend band.
            steps = 6.0
            t = w * steps
            terr = (np.floor(t) + smoothstep(0.7, 1.0, t - np.floor(t))) / steps
            wall = 0.35 * w + 0.65 * terr
            z -= drop * wall
            z += k["relief_m"] * fbm(x, y, seed + 2, 3000.0, 8) * w
            exposed = smoothstep(0.05, 0.3, w) * (1 - smoothstep(0.85, 1.0, w))
            bs = bs * (1 - exposed) + 0.8 * exposed
            looks = looks * (1 - exposed) + 3.0 * exposed
            sediment *= 1 - exposed
    z += ripple * sediment
    return z, bs, looks


# ---------- output ----------

PALETTES = {
    "amber": [(0, 0, 0), (60, 30, 5), (170, 100, 30), (250, 210, 140), (255, 250, 230)],
    "cyan":  [(1, 6, 10), (8, 40, 60), (40, 140, 170), (150, 230, 240), (235, 255, 255)],
}


def to_rgb(v, palette):
    """Values 0 to 1 -> RGB uint8 through the palette's color stops."""
    stops = np.array(PALETTES[palette], dtype=float)
    t = np.clip(v, 0, 1) * (len(stops) - 1)
    i = np.minimum(t.astype(int), len(stops) - 2)
    f = (t - i)[..., None]
    return (stops[i] * (1 - f) + stops[i + 1] * f).astype(np.uint8)


def tone(inten):
    """Intensity -> 0 to 1: the 99.5th percentile maps to 1, then gamma 0.6."""
    return np.clip(inten / np.percentile(inten, 99.5), 0, 1) ** 0.6
