"""Feed Sonar Studio renders to RockMapper. Command-line entry point; the
logic lives in rockmapper_adapter.py behind the detector seam (detector.py).

Two steps, two Python environments:

  python rockmapper_feed.py export
      Plain Python (numpy, scipy, Pillow). Renders the test scenes in the window below
      and writes single-band 8-bit GeoTIFFs under rockmapper_out/input/.
      --vents N sets vent mound clusters per km2 (0 to 10, default 0.1 from
      vent_defaults.json). --vent-size X sets vent width (0.25 to 10, default 1).
      --vent-height M sets vent mound height in meters (5 to 400, default
      80 m from vent_defaults.json). --px M, --strip-km K and --swath-km K set
      the window (default as Sonar Studio: 100 m per pixel, 60 km strip,
      10 km swath per side; at most 20 million pixels).

  <rockmapper env python> rockmapper_feed.py run
      RockMapper conda env. Runs RockMapper on each GeoTIFF, then writes
      side-by-side PNGs and class fractions to rockmapper_results/.

GeoTIFFs are written with Pillow and raw GeoTIFF tags, so export needs no GDAL.
"""
import sys

import detector as D
import rockmapper_adapter as RA


def main():
    window, args = D.window_args(sys.argv[1:])
    cmd = args[0] if args else "help"
    tool = RA.RockMapper()
    if cmd == "export":
        px = float(args[1]) if len(args) > 1 else RA.DECLARED_PX_M
        D.export_scenes(tool, {"declared_px_m": px}, **window)
    elif cmd == "run":
        for mode in args[1:] or ("declared", "honest"):
            RA.run_rockmapper(mode)
        tool.summarize()
    elif cmd == "summarize":
        tool.summarize()
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:     # sonar_core.TooLarge and bad flag values
        sys.exit(f"Error: {e}")
