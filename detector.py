"""Detector module: runs an outside sonar tool on scan images and reads its
answer back as detections in scan image pixels.

Interface:
  Adapter            the seam. RockMapper and GhostVision are the two real
                     adapters (rockmapper_adapter.py, ghostvision_adapter.py);
                     test_render.py has a fake one.
  Detections         boxes (cx, cy, w, h, class, confidence) and an optional
                     class raster, both in scan image pixels.
  export_scenes      the batch scene loop: render each test scene, hand the
                     scan image to the adapter.
  write_truth, read_truth   vent truth on disk, for the command-line summary.
  score              vent mound hit scoring of detections against truth.
  draw_overlay       truth outlines and detections drawn on an image.
  launch             start the adapter's Run .cmd with output piped to a log.
  read_version       installed tool version from a command's last line.

Sonar Studio never imports tool code. Each adapter owns its export format,
launcher, conda env name, version reading and upgrade command.
"""
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
MINIFORGE = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "miniforge3"
SCENES = ("vent field", "mountain interior", "trench edge", "plain floor")
DET_COLOR = (255, 70, 70)
TRUTH_COLOR = (90, 255, 120)


@dataclass(frozen=True)
class Box:
    cx: float     # center column, scan image pixels
    cy: float     # center row, scan image pixels
    w: float
    h: float
    cls: str
    conf: float
    count: int = 1   # raw boxes merged into this one


@dataclass
class Detections:
    """A tool's answer on one scan image. classes: uint8 class raster on the
    scan image grid (0 no data), or None."""
    boxes: list = field(default_factory=list)
    classes: np.ndarray | None = None
    class_rgb: dict = field(default_factory=dict)   # class value -> RGB for drawing

    def above(self, conf):
        return Detections([b for b in self.boxes if b.conf >= conf], self.classes, self.class_rgb)

    def merged(self, frac=None):
        """One box per object: merge_boxes on these boxes, same class raster."""
        return Detections(merge_boxes(self.boxes, MERGE_FRAC if frac is None else frac),
                          self.classes, self.class_rgb)

    def __bool__(self):
        return bool(self.boxes) or self.classes is not None


MERGE_FRAC = 0.5


def merge_boxes(boxes, frac=MERGE_FRAC):
    """Merge boxes that mark one object. Uses only the boxes, never vent truth.
    Greedy, highest confidence first: each unclaimed box seeds a cluster and
    claims every unclaimed box whose center lies within frac x the mean side
    of the larger of the two boxes. Overlapping tiles crop the same object
    differently, so center distance holds where IoU drops. Seeding from one
    box stops chains of neighbors joining across separate objects.
    Result per cluster: confidence = max, center = confidence-weighted mean,
    width and height = median, class of the seed, count = boxes merged."""
    order = sorted(boxes, key=lambda b: -b.conf)
    used = [False] * len(order)
    out = []
    for i, s in enumerate(order):
        if used[i]:
            continue
        group = []
        for j in range(i, len(order)):
            b = order[j]
            if used[j]:
                continue
            reach = frac * max((s.w + s.h) / 2, (b.w + b.h) / 2)
            if np.hypot(b.cx - s.cx, b.cy - s.cy) <= reach:
                used[j] = True
                group.append(b)
        wt = np.array([max(b.conf, 1e-6) for b in group])
        out.append(Box(float(np.average([b.cx for b in group], weights=wt)),
                       float(np.average([b.cy for b in group], weights=wt)),
                       float(np.median([b.w for b in group])), float(np.median([b.h for b in group])),
                       s.cls, s.conf, sum(b.count for b in group)))
    return out


class Adapter:
    """One outside tool at the detector seam. Subclasses set the class
    attributes and implement export_scene, read and summarize."""
    name = ""          # shown in the window, and in "Run <name>.cmd"
    env = ""           # conda env
    package = ""       # Python package whose version is shown
    alias = ""         # pinginstaller alias
    model_note = ""    # sentence about models for the upgrade dialog
    # Opt-in last upgrade step: (command, warning, launcher argument), or None.
    optional_step = None

    def export_scene(self, scan, settings, slug, opts):
        """Write one scan image in the tool's input format. Returns a summary line."""
        raise NotImplementedError

    def read(self, slug, mode, shape):
        """Detections of one exported scene in scan image pixels (shape (h, w))."""
        raise NotImplementedError

    def summarize(self):
        """Read every result, score it against truth, write the results folder."""
        raise NotImplementedError

    def run_args(self, opts):
        """Command for the tool's Run .cmd, run from this folder."""
        return [f"Run {self.name}.cmd"]

    # ---------- upgrade and version (PINGInstaller route) ----------

    def upgrade_launcher(self):
        return HERE / f"Upgrade {self.name}.cmd"

    def upgrade_steps(self, optional=False):
        """Commands the upgrade launcher runs in Miniforge's base env, in order.
        optional: also the opt-in step, when the tool has one."""
        steps = ["pip install --force-reinstall pinginstaller",
                 f"python -m pinginstaller {self.alias}"]
        return steps + [self.optional_step[0]] if optional and self.optional_step else steps

    def upgrade_args(self, optional=False):
        """Upgrade launcher command, without its prompt and pause."""
        extra = [self.optional_step[2]] if optional and self.optional_step else []
        return [self.upgrade_launcher().name, "nopause", *extra]

    def confirm_text(self):
        return (f"Upgrade {self.name}?\n\n"
                f"Runs in Miniforge's base env ({MINIFORGE}):\n"
                + "".join(f"  {s}\n" for s in self.upgrade_steps()) +
                f"\nChanges the '{self.env}' conda env in place (conda env update --prune). "
                "Packages not in the tool's env file are removed from it.\n"
                "Also updates every package in the base env and cleans the conda package cache.\n\n"
                "Downloads packages from the internet, often several GB. "
                + self.model_note + "\n\nIt can take 10 to 30 minutes.")

    def version_command(self, conda=None):
        """Command that prints the tool's installed version from its env."""
        code = f"import importlib.metadata as m; print(m.version('{self.package}'))"
        return [conda or str(MINIFORGE / "Scripts" / "conda.exe"), "run", "-n", self.env,
                "python", "-c", code]

    def installed_version(self):
        return read_version(self.version_command())


def read_version(cmd, timeout=120):
    """Run cmd and return the last line it prints, or 'unknown (<reason>)'."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"unknown ({type(e).__name__})"
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    if r.returncode != 0 or not lines:
        return f"unknown (exit code {r.returncode})"
    return lines[-1]


def launch(args, on_line, console=False):
    """Start a Run or Upgrade .cmd from this folder: output piped line by line
    to on_line, or a visible console. Returns the Popen."""
    import proc_log
    cmd = list(args) if console else ["cmd", "/c", *args]
    on_line("> " + " ".join(f'"{a}"' if " " in a else a for a in cmd))
    return proc_log.start(cmd, HERE, on_line, console=console)


# ---------- scene loop and truth ----------

def scene_settings(scene, seed=7, vent_per_km2=None, vent_scale=1.0, vent_height_m=None,
                   px_m=None, strip_km=None, swath_km=None):
    """Settings of one batch test scene. Pixel size, strip length and swath
    default to Sonar Studio's defaults (sonar_core.Settings)."""
    import sonar_core as C
    d = C.Settings()
    return C.Settings(scene=scene, seed=seed,
                      px_m=float(d.px_m if px_m is None else px_m),
                      strip_km=float(d.strip_km if strip_km is None else strip_km),
                      swath_km=float(d.swath_km if swath_km is None else swath_km),
                      vent_per_km2=d.vent_per_km2 if vent_per_km2 is None else vent_per_km2,
                      vent_scale=vent_scale,
                      vent_height_m=d.vent_height_m if vent_height_m is None else vent_height_m)


def export_scenes(adapter, opts=None, seed=7, **window):
    """Render each test scene once and hand its scan image to the adapter.
    window: vent_per_km2, vent_scale, vent_height_m, px_m, strip_km,
    swath_km (scene_settings). Raises sonar_core.TooLarge over the pixel limit."""
    import sonar_core as C
    lines = []
    for scene in SCENES:
        s = scene_settings(scene, seed, **window)
        line = adapter.export_scene(C.render(s), s, scene.replace(" ", "_"), opts)
        print(line)
        lines.append(line)
    return lines


def write_truth(path, truth):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    np.save(path, truth)


def read_truth(path, shape=None):
    """Truth mask from disk, or None when missing or not shaped like `shape`."""
    path = Path(path)
    if not path.exists():
        return None
    t = np.load(path)
    return t if shape is None or t.shape == tuple(shape) else None


# ---------- scoring ----------

def labels(truth):
    """Mound labels (int, 0 off mounds) of a truth array. Scan image truth
    already carries one label per mound; a bool mask (older truth files)
    gets one label per connected patch."""
    from scipy import ndimage
    if truth is None:
        return None
    return ndimage.label(truth)[0] if truth.dtype == bool else truth


def score(truth, dets):
    """Vent mound hits per mound label. Boxes: mounds that any box touches.
    Class raster: the patches of the class that favors mound pixels over
    ground pixels most, and how many touch a mound."""
    from scipy import ndimage
    out = {}
    lab = labels(truth)
    ids = set(np.unique(lab)) - {0} if lab is not None else set()
    lab = lab if ids else None
    out["mounds_rendered"] = len(ids)
    truth = lab > 0 if lab is not None else None
    hits = set()
    if lab is not None:
        for b in dets.boxes:
            r0, r1 = int(b.cy - b.h / 2), int(b.cy + b.h / 2) + 1
            c0, c1 = int(b.cx - b.w / 2), int(b.cx + b.w / 2) + 1
            hits |= set(np.unique(lab[max(r0, 0):r1, max(c0, 0):c1])) - {0}
    out["mounds_hit"] = len(hits)
    pred = dets.classes
    if pred is not None and lab is not None:
        valid = pred > 0
        mound = truth & valid
        ground = ~truth & valid
        lift = {k: (pred[mound] == k).mean() - (pred[ground] == k).mean()
                if mound.any() and ground.any() else 0.0 for k in range(1, 6)}
        kbest = max(lift, key=lift.get)
        plab, pn = ndimage.label(pred == kbest)
        out["mound_class"] = int(kbest)
        out["patches"] = int(pn)
        out["patches_touching_mounds"] = len(set(np.unique(plab[truth])) - {0})
    return out


# ---------- drawing ----------

def mask_edges(truth, width=1):
    """Outline pixels of each mound in a truth array, `width` pixels thick:
    pixels with a different label (or the image edge) within `width`."""
    from scipy import ndimage
    lab = labels(truth)
    if lab is None or not lab.any():
        return np.zeros(lab.shape, bool) if lab is not None else None
    size = 2 * max(int(width), 1) + 1
    hi = ndimage.maximum_filter(lab, size, mode="constant", cval=0)
    lo = ndimage.minimum_filter(lab, size, mode="constant", cval=0)
    return (lab > 0) & ((hi != lab) | (lo != lab))


def colorize(cls, class_rgb):
    rgb = np.zeros(cls.shape + (3,), np.uint8)
    for k, c in class_rgb.items():
        rgb[cls == k] = c
    return rgb


def draw_overlay(img, dets, truth=None, scale=1.0, show_dets=True, show_truth=True, merged=False):
    """img (RGB, already at `scale` x scan image size) with a class raster
    tint, vent truth outlines and the boxes. merged=False: one circle per
    box, enclosing it, labeled class and confidence. merged=True (boxes from
    merge_boxes): one circle with a center cross per box, labeled
    "confidence (count)" to its right, moved down past earlier labels."""
    img = img.convert("RGB")
    dets = dets or Detections()
    if show_dets and dets.classes is not None:
        tint = Image.fromarray(colorize(dets.classes, dets.class_rgb)).resize(img.size, Image.NEAREST)
        valid = Image.fromarray(((dets.classes > 0) * 110).astype(np.uint8)).resize(img.size, Image.NEAREST)
        img.paste(tint, mask=valid)
    if show_truth and truth is not None and truth.any():
        edge = mask_edges(truth, max(1, int(np.ceil(1 / scale))))
        e = Image.fromarray((edge * 255).astype(np.uint8)).resize(img.size, Image.NEAREST)
        img.paste(Image.new("RGB", img.size, TRUTH_COLOR), mask=e)
    if show_dets and merged:
        d = ImageDraw.Draw(img)
        taken = []
        for b in sorted(dets.boxes, key=lambda b: (b.cy, b.cx)):
            rad = max(0.25 * (b.w + b.h) * scale, 4)
            x, y = b.cx * scale, b.cy * scale
            d.ellipse([x - rad, y - rad, x + rad, y + rad], outline=DET_COLOR, width=2)
            d.line([x - 3, y, x + 3, y], fill=DET_COLOR)
            d.line([x, y - 3, x, y + 3], fill=DET_COLOR)
            text = f"{b.conf:.2f} ({b.count})"
            tw, th = 7 * len(text), 12
            tx, ty = x + rad + 2, y - th / 2
            while any(abs(tx - ax) < tw and abs(ty - ay) < th for ax, ay in taken):
                ty += th
            taken.append((tx, ty))
            d.text((tx, ty), text, fill=DET_COLOR)
    elif show_dets:
        d = ImageDraw.Draw(img)
        for b in dets.boxes:
            rad = 0.5 * np.hypot(b.w, b.h) * scale
            x, y = b.cx * scale, b.cy * scale
            d.ellipse([x - rad, y - rad, x + rad, y + rad], outline=DET_COLOR, width=2)
            d.text((x - rad, y - rad - 12), f"{b.cls} {b.conf:.2f}", fill=DET_COLOR)
    return img


WINDOW_FLAGS = {"--vents": "vent_per_km2", "--vent-size": "vent_scale", "--vent-height": "vent_height_m",
                "--px": "px_m", "--strip-km": "strip_km", "--swath-km": "swath_km"}


def window_args(argv):
    """Read --vents N, --vent-size X, --vent-height M, --px M, --strip-km K
    and --swath-km K from argv. Returns (export_scenes keywords, remaining args)."""
    window, rest = {}, []
    it = iter(argv)
    for a in it:
        if a in WINDOW_FLAGS:
            window[WINDOW_FLAGS[a]] = float(next(it))
        else:
            rest.append(a)
    return window, rest
