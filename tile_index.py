"""GhostVision tile index, inside the GhostVision adapter.

Cuts a gray scan image into sonogram tiles, writes a versioned index
(tiles.json) and maps GhostVision boxes back to scan image pixels.

Interface:
  write(folder, slug, gray, px_m, tile_pings, stride_pings, sides) -> tile count
  load(folder) -> tile entries (dicts: name, scene, side, offset, px_m, render_h)
  to_scan(tile, x, y) -> scan image (column, row) of a tile pixel point

Tile geometry: the scan image's track row splits it into port (rows above,
flipped so the track is tile row 0) and starboard (rows below, track on
tile row 0). Tile column x is scan column offset + x. Tiles are not resized.

Index format, version 2: {"version": 2, "tiles": [entry, ...]}. Version 1
(no version field) is a bare list of entries; entries written before
render_h was recorded take half the render height from GhostVision's
img_height (to_scan's fallback argument).
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image

VERSION = 2
INDEX = "tiles.json"


def cut(gray, tile_pings=500, stride_pings=100, sides="both"):
    """(side, offset, tile) for each moving-window tile of a gray scan image."""
    h = gray.shape[0] // 2
    chans = {"port": gray[:h][::-1], "star": gray[h:]}
    for side, son in chans.items():
        if sides not in ("both", side):
            continue
        w = son.shape[1]
        last = max(w - tile_pings, 0)
        offs = list(range(0, last + 1, stride_pings))
        # The stride rarely lands on the strip's end; one more tile, flush with
        # the end, covers the pings past the last regular tile.
        if offs[-1] != last:
            offs.append(last)
        for off in offs:
            yield side, off, np.ascontiguousarray(son[:, off:off + tile_pings])


def load(folder):
    """Index entries in folder/tiles.json, any version; [] when missing."""
    path = Path(folder) / INDEX
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if isinstance(data, list):           # version 1
        return data
    if data.get("version", 1) > VERSION:
        raise ValueError(f"{path}: index version {data['version']} is newer than {VERSION}")
    return data["tiles"]


def write(folder, slug, gray, px_m, tile_pings=500, stride_pings=100, sides="both"):
    """Write slug's JPG tiles and replace its entries in the index. Returns the tile count."""
    d = Path(folder)
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob(f"{slug}_*.jpg"):
        old.unlink()
    rows = []
    for i, (side, off, tile) in enumerate(cut(gray, tile_pings, stride_pings, sides)):
        name = f"{slug}_{side}_{i}_{off}.jpg"
        Image.fromarray(tile, mode="L").save(d / name, quality=95)
        rows.append(dict(name=name, scene=slug, side=side, offset=off, px_m=px_m,
                         render_h=int(gray.shape[0])))
    tiles = [t for t in load(d) if t["scene"] != slug] + rows
    (d / INDEX).write_text(json.dumps({"version": VERSION, "tiles": tiles}, indent=1))
    return len(rows)


def to_scan(tile, x, y, img_height=None):
    """Scan image (column, row) of point (x, y) in a tile. img_height (the
    tile height GhostVision reports) stands in for an entry without render_h."""
    h = tile["render_h"] // 2 if "render_h" in tile else int(float(img_height))
    return tile["offset"] + x, (h - y if tile["side"] == "port" else h + y)
