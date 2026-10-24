"""Feed Sonar Studio renders to GhostVision (GPL-3.0-or-later, separate tool).
Command-line entry point; the logic lives in ghostvision_adapter.py behind
the detector seam (detector.py). No GhostVision code is imported: Sonar
Studio writes JPG tiles that GhostVision reads and reads the CSV it writes.

  python ghostvision_feed.py export      write tiles to ghostvision_out/tiles/<mode>/
      (takes --vents N, --vent-size X, --vent-height M, --px M (default: the GhostVision pixel size setting, 50 m), --strip-km K
      and --swath-km K, as rockmapper_feed.py export)
  Run GhostVision.cmd                    run GhostVision on those tiles
  python ghostvision_feed.py summarize   draw circles, write ghostvision_results/
  python ghostvision_feed.py summarize-run <folder>
      print one run folder's summary (Sonar Studio's Run GhostVision)

Settings come from ghostvision_settings.json next to this file (Sonar
Studio's GhostVision settings dialog writes it). Flags override them:
--model, --confidence, --iou, --tile-pings, --stride-pings, --sides,
--mode, --declared-px, --export-image, --export-video.

Tile format (GhostVision moving-window sonogram tiles, "wcp" type): one
side-scan channel per tile, 8-bit grayscale JPG, no georeferencing, range
down the rows from the ship track, pings along the columns, tile_pings wide
(default 500), stride_pings apart (default 100). Names end in
_<chunk>_<offset>.jpg, which GhostVision parses.

Scale: the tiles carry no pixel size. "honest" and "declared" tiles hold the
same pixels; the mode only changes the scale recorded in tiles.json.
"""
import json
import sys

import detector as D
import ghostvision_adapter as GA


def main():
    window, args = D.window_args(sys.argv[1:])
    gs, args = GA.settings_args(args)
    window.setdefault("px_m", gs["px_m"])
    cmd = args[0] if args else "help"
    if cmd == "export":
        D.export_scenes(GA.GhostVision(), gs, **window)
    elif cmd == "summarize":
        GA.GhostVision().summarize()
    elif cmd == "summarize-run" and len(args) > 1:
        print(json.dumps(GA.GhostVision().summarize_run(args[1])))
    elif cmd == "args":
        print(" ".join(GA.launcher_args(gs)))
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:     # sonar_core.TooLarge and bad flag values
        sys.exit(f"Error: {e}")
