"""Hover tooltips for Sonar Studio. All tooltip text lives in TIPS."""
import re
import tkinter as tk

BANNED = ("why", "simply", "just", "easy", "powerful", "seamless", "robust", "very",
          "really", "crucial", "key", "maths", "metre", "colour", "grey")

TIPS = {
    # main window, keyed by row label or button text
    "Scene": "Picks the area under the ship: mountain front, mountain interior, trench edge, "
             "vent field or plain floor. Default mountain front.",
    "Seed": "Number that picks the random terrain. 0 to 999,999. Default 7. "
            "A new seed draws different terrain in the same scene.",
    "Reroll": "Picks a random seed and redraws the terrain.",
    "Vent mounds per km²": "Vent mound clusters per square kilometer. 0 to 10. Default 0.1. "
                           "0 draws no mounds. Type a value in the box for finer steps.",
    "Vent size ×": "Multiplies vent mound width and fissure width and length. "
                   "0.25 to 10. Default 1. Mound height stays the same.",
    "Vent height (m)": "Sets the tallest vent mound height in meters. 5 to 400 m. Default 80 m. "
                       "Fissure depth follows it. Mound width stays the same.",
    "Pixel size (m)": "Ground size of one image pixel in meters. 5 to 200 m. Default 100 m. "
                      "Smaller pixels show more detail and take longer to render.",
    "Strip length (km)": "Length of track the ship covers, in kilometers. 1 to 200 km. Default 60 km.",
    "Swath per side (km)": "Range the sonar sees on each side of the track, in kilometers. "
                           "1 to 50 km. Default 10 km.",
    "Ship depth below ice (m)": "Ship depth below the ice in meters. 0 to 900 m. Default 0 m. "
                                "A deeper ship changes the shadow angles.",
    "Track offset (m)": "Shifts the ship track across the terrain, in meters. "
                        "-50,000 to 50,000 m. Default 0 m.",
    "Palette": "Color scheme of the image: amber or cyan. Default cyan.",
    "View": "Side-scan shows the sonar image. Truth hillshade shows terrain height "
            "lit from above. Default side-scan.",
    "Grain": "Adds display grain fixed to the ground. Default on. "
             "Off adds random speckle instead.",
    "Grain strength (0 to 1)": "Strength of the grain. 0 (none) to 1 (full speckle). Default 0.35.",
    "Shadows": "Draws acoustic shadows behind tall terrain. Default on.",
    "Area boxes": "Outlines the terrain areas and the ship track on the image. Default off.",
    "Render": "Renders the image with the current settings.",
    "Save PNG": "Saves the current image as a PNG file.",
    "Declared scale (RockMapper)": "Writes the declared pixel size into the GeoTIFF. "
                                   "Off writes the true pixel size. Default on.",
    "Declared pixel size (m)": "Pixel size written into exported files, in meters. "
                               "0.01 to 5 m. Default 0.1 m.",
    "Export for RockMapper": "Writes the current image as a GeoTIFF for RockMapper.",
    "Run RockMapper": "Runs RockMapper on the exported GeoTIFFs at both scales. "
                      "Results go to rockmapper_results.",
    "Export for GhostVision": "Writes the current image as JPG tiles for GhostVision, "
                              "with its vent truth mask.",
    "GhostVision settings…": "Opens the GhostVision and tiling settings.",
    "Run GhostVision": "Tiles the displayed render in the scale set in GhostVision settings, "
                       "runs GhostVision on those tiles only, then shows its detections.",
    "Load GhostVision results": "Loads the newest GhostVision run made for the displayed render "
                                "and the current settings.",
    "upgrade_optional": "On by default. Keeps inference-gpu at 1.x in the ghostvision env "
                        "after PINGInstaller runs. Installs pycuda from conda-forge first.",
    "Open in console window": "On: tools run in a visible cmd window. "
                              "Off: their output shows in a log window here. Default off.",
    "Minimum confidence shown": "Hides GhostVision detections below this confidence. "
        "0 to 1. Default 0 (all shown). Changes only the drawing, not the saved results.",
    "Show detections": "Circles GhostVision detections in red on the image. Default on.",
    "Merge overlapping detections": "Draws one circle per object. Tiles overlap, so GhostVision "
        "finds one object in several tiles. On: boxes whose centers lie within half a box side "
        "merge into one mark, labeled with the highest confidence and the box count. "
        "Uses the boxes only, never vent truth. Off: draws every raw box. Default on.",
    "Show vent truth": "Outlines pixels where a vent mound stands above 25 % of its own "
                       "peak height, in green. Default off.",
    "Upgrade RockMapper": "Upgrades RockMapper with PINGInstaller from Miniforge's base env. "
                          "Updates the rockmapper conda env in place and updates the base env. "
                          "Downloads packages, often several GB. The model comes with the package. "
                          "Asks before it starts. Shows the version before and after.",
    "Upgrade GhostVision": "Upgrades GhostVision with PINGInstaller (ghostvision-gpu) from "
                           "Miniforge's base env. Updates the ghostvision conda env in place and "
                           "updates the base env. Downloads packages, often several GB. Keeps the "
                           "downloaded models. Asks before it starts. Shows the version before and after.",
    # section headers, keyed section_<name>
    "section_Scene": "Shows or hides the scene, seed, pixel size and strip length. Click to toggle.",
    "section_Ship and swath": "Shows or hides swath width, ship depth and track offset. Click to toggle.",
    "section_Vents": "Shows or hides the vent mound controls. Vent field scene only. Click to toggle.",
    "section_Look": "Shows or hides palette, view, grain and shadows. Click to toggle.",
    "section_Overlays": "Shows or hides the area box overlay. Click to toggle.",
    "section_Export": "Shows or hides Save PNG and the RockMapper and GhostVision exports. "
                      "Click to toggle.",
    "section_RockMapper": "Shows or hides the RockMapper run, upgrade and declared scale controls. "
                          "Click to toggle.",
    "section_GhostVision": "Shows or hides the GhostVision run, results, settings, overlays and "
                           "upgrade. Click to toggle.",
    "section_Output": "Shows or hides the tool output setting. Click to toggle.",
    "status": "Shows the last action or message.",
    "progress": "Shows render, export and tool progress.",
    # log window
    "log": "Output of the running tool. Keeps the last 5,000 lines.",
    "Copy": "Copies the whole log to the clipboard.",
    "Clear": "Empties the log.",
    # GhostVision settings dialog, keyed gv_<setting>
    "gv_model": "GhostVision model. Default 2 (YOLO26).",
    "gv_px_m": "Pixel size of the render sent to GhostVision. 5 to 200 m. Default 50 m. "
               "Detections map back onto the render shown.",
    "gv_confidence": "Lowest detection confidence kept. 0 to 1. Default 0.01. "
                     "Lower values keep more boxes.",
    "gv_iou": "Box overlap (IoU, intersection over union) above which boxes merge. "
              "0 to 1. Default 0.5.",
    "gv_tile_pings": "Tile length in pings. 50 to 5,000. Default 500.",
    "gv_stride_pings": "Step between tile starts in pings. 10 to 5,000, or auto. Default auto: "
                       "tile length minus 3 x the largest mound diameter in pixels, at least 10. "
                       "Every mound then lies whole inside at least one tile. At defaults "
                       "(500 pings, 1,500 m mounds, 50 m pixels) auto gives 500 - 90 = 410 pings.",
    "gv_sides": "Track sides to tile: both, port or star. Default both.",
    "gv_mode": "Honest tiles keep the true pixel size. Declared tiles use the declared "
               "pixel size. Default honest.",
    "gv_declared_px_m": "Declared pixel size in meters. 0.01 to 5 m. Default 0.1 m.",
    "gv_export_image": "Writes an image with boxes for each tile. Default on. "
                       "The progress bar counts these images.",
    "gv_export_video": "Writes a video of the tiles. Default off.",
    "Reset to defaults": "Sets every field to its default value.",
    "Save": "Saves these settings to ghostvision_settings.json and closes the dialog.",
    "Cancel": "Closes the dialog without saving.",
}


def banned_words(text):
    """Banned words found in text, whole words, any case."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    return [b for b in BANNED if b in words] + (["?"] if "?" in text else [])


class Tooltip:
    """Shows text after a 500 ms hover; hides on leave or click."""

    def __init__(self, widget, text, wrap_chars=50):
        self.w, self.text, self.wrap = widget, text, wrap_chars
        self.after_id = self.tip = None
        widget._tip = self
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _e=None):
        self.hide()
        self.after_id = self.w.after(500, self.show)

    def show(self):
        self.after_id = None
        self.tip = tk.Toplevel(self.w)
        self.tip.wm_overrideredirect(True)
        tk.Label(self.tip, text=self.text, justify="left", bg="#ffffe0", fg="#000",
                 relief="solid", borderwidth=1, padx=4, pady=2,
                 wraplength=self.wrap * 7).pack()
        self.tip.update_idletasks()
        w, h = self.tip.winfo_reqwidth(), self.tip.winfo_reqheight()
        x = self.w.winfo_rootx() + 12
        y = self.w.winfo_rooty() + self.w.winfo_height() + 4
        sw, sh = self.w.winfo_screenwidth(), self.w.winfo_screenheight()
        if y + h > sh:
            y = self.w.winfo_rooty() - h - 4
        self.tip.wm_geometry(f"+{max(0, min(x, sw - w))}+{max(0, y)}")

    def hide(self, _e=None):
        if self.after_id:
            self.w.after_cancel(self.after_id)
            self.after_id = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


def tip(widget, key):
    """Attach TIPS[key] to widget and every widget inside it; returns widget."""
    Tooltip(widget, TIPS[key])
    for c in widget.winfo_children():
        tip(c, key)
    return widget


def tip_buttons(widget):
    """Attach tips to buttons and checkbuttons keyed by their own text."""
    for c in widget.winfo_children():
        if c.winfo_class() in ("TButton", "TCheckbutton") and str(c.cget("text")) in TIPS:
            Tooltip(c, TIPS[str(c.cget("text"))])
        tip_buttons(c)
