"""Sonar Studio: a window that renders synthetic side-scan sonar images.

Run: python sonar_studio.py
  --smoke                 close after 2 s
  --screenshot PATH       render a set scene, save a PNG of the window, quit
  --shot NAME             mountain (default), vents or dialog
  --runs DIR              GhostVision run folders for --shot vents
"""
import dataclasses
import random
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageTk

import detector as D
import ghostvision_adapter as GA
import proc_log
import rockmapper_adapter as RA
import sections
import sonar_core as C
import tooltips as T
from slider_entry import SliderWithEntry

FAST_S = 1.0   # auto-render on change only when the last render took less


class App:
    def __init__(self, root):
        self.root = root
        root.title("Sonar Studio")
        root.geometry("1200x720")
        self.result = None
        self.result_s = None      # settings of the displayed render
        self.gv_run = None        # run folder of the GhostVision run in progress
        self.busy = False
        self.pending = False
        self.last_s = 0.0
        self.photo = None
        self.after_id = None
        # Progress shared with worker threads: (fraction 0 to 1, stage).
        self.lock = threading.Lock()
        self.progress = None
        self.tools = {"GhostVision": GA.GhostVision(), "RockMapper": RA.RockMapper()}
        self.dets = {name: D.Detections() for name in self.tools}
        self.gv_proc = None
        self.rm_proc = None
        self.up_proc = {}
        self.logs = {"GhostVision": proc_log.LogWindow(root, "GhostVision"),
                     "RockMapper": proc_log.LogWindow(root, "RockMapper")}

        self.panel = sections.ScrollPanel(root)
        self.panel.pack(side="left", fill="y")
        panel = self.panel.inner
        self.status = tk.StringVar(value="Ready.")
        bottom = ttk.Frame(root)
        bottom.pack(side="bottom", fill="x")
        self.bar = ttk.Progressbar(bottom, length=220, maximum=1.0)
        self.bar.pack(side="right", padx=4)
        self.status_label = ttk.Label(bottom, textvariable=self.status, anchor="w",
                                      relief="sunken", padding=4)
        self.status_label.pack(side="left", fill="x", expand=True)
        self.canvas = tk.Canvas(root, bg="#02080d", highlightthickness=0)
        self.canvas.pack(side="right", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.show())
        ttk.Style(root).configure("Section.TButton", anchor="w", font=("Segoe UI", 9, "bold"))

        d = C.Settings()
        self.v = {
            "scene": tk.StringVar(value=d.scene),
            "seed": tk.IntVar(value=d.seed),
            "px_m": tk.StringVar(value=str(int(d.px_m))),
            "strip_km": tk.DoubleVar(value=d.strip_km),
            "swath_km": tk.DoubleVar(value=d.swath_km),
            "ship_depth_m": tk.DoubleVar(value=d.ship_depth_m),
            "track_offset_m": tk.DoubleVar(value=d.track_offset_m),
            "palette": tk.StringVar(value=d.palette),
            "grain": tk.BooleanVar(value=d.grain),
            "grain_strength": tk.DoubleVar(value=d.grain_strength),
            "shadows": tk.BooleanVar(value=d.shadows),
            "view": tk.StringVar(value=d.view),
            "boxes": tk.BooleanVar(value=d.boxes),
            "vent_per_km2": tk.DoubleVar(value=d.vent_per_km2),
            "vent_scale": tk.DoubleVar(value=d.vent_scale),
            "vent_height_m": tk.DoubleVar(value=d.vent_height_m),
        }

        self.section_state = sections.load_state()
        self.sections = {}

        def section(name):
            s = sections.Section(panel, name, self.section_state,
                                 lambda: sections.save_state(self.section_state))
            s.pack(fill="x")
            T.tip(s.header, f"section_{name}")
            self.sections[name] = s
            return s.body

        def row(parent, label, widget_fn):
            ttk.Label(parent, text=label).pack(anchor="w", pady=(6, 0))
            w = widget_fn(parent)
            w.pack(fill="x")
            T.tip(w, label)
            return w

        def combo(var, values):
            return lambda p: ttk.Combobox(p, textvariable=self.v[var], state="readonly", values=values)

        def spin(var, lo, hi, step):
            return lambda p: ttk.Spinbox(p, from_=lo, to=hi, increment=step, textvariable=self.v[var])

        def check(parent, text, var, command=None):
            ttk.Checkbutton(parent, text=text, variable=var, command=command).pack(anchor="w", pady=(4, 0))

        def button(parent, text, command):
            b = ttk.Button(parent, text=text, command=command)
            b.pack(fill="x", pady=(2, 0))
            return b

        ttk.Button(panel, text="Render", command=self.start_render).pack(fill="x", pady=(0, 2))

        s = section("Scene")
        row(s, "Scene", combo("scene", list(C.SCENES)))
        sf = row(s, "Seed", ttk.Frame)
        T.tip(ttk.Spinbox(sf, from_=0, to=999999, textvariable=self.v["seed"], width=10),
              "Seed").pack(side="left")
        ttk.Button(sf, text="Reroll", command=self.reroll).pack(side="left", padx=4)
        row(s, "Pixel size (m)", combo("px_m", [str(x) for x in C.PIXEL_SIZES_M]))
        row(s, "Strip length (km)", spin("strip_km", 1, 200, 1))

        s = section("Ship and swath")
        row(s, "Swath per side (km)", spin("swath_km", 1, 50, 1))
        row(s, "Ship depth below ice (m)", spin("ship_depth_m", 0, 900, 50))
        row(s, "Track offset (m)", spin("track_offset_m", -50000, 50000, 1000))

        s = section("Vents")
        # Sliders with a typed-value box beside them; ranges from vent_shapes.
        for var, text, lo, hi in (("vent_per_km2", "Vent mounds per km²", 0.0, C.VENT_PER_KM2_MAX),
                                  ("vent_scale", "Vent size ×", C.VENT_SCALE_MIN, C.VENT_SCALE_MAX),
                                  ("vent_height_m", "Vent height (m)", C.VENT_HEIGHT_MIN_M,
                                   C.VENT_HEIGHT_MAX_M)):
            row(s, text, lambda p, var=var, lo=lo, hi=hi:
                SliderWithEntry(p, self.v[var], lo, hi, status=self.status.set))

        s = section("Look")
        row(s, "Palette", combo("palette", list(C.PALETTES)))
        row(s, "View", combo("view", ["side-scan", "truth hillshade"]))
        check(s, "Grain", self.v["grain"])
        row(s, "Grain strength (0 to 1)",
            lambda p: SliderWithEntry(p, self.v["grain_strength"], 0.0, 1.0, status=self.status.set))
        check(s, "Shadows", self.v["shadows"])

        s = section("Overlays")
        check(s, "Area boxes", self.v["boxes"])

        s = section("Export")
        button(s, "Save PNG", self.save)
        button(s, "Export for RockMapper", lambda: self.export("rockmapper"))
        button(s, "Export for GhostVision", lambda: self.export("ghostvision"))

        self.declared = tk.BooleanVar(value=True)
        self.declared_px = tk.DoubleVar(value=0.1)
        s = section("RockMapper")
        self.tool_buttons = {"RockMapper": [button(s, "Run RockMapper", self.run_rm),
                                            button(s, "Upgrade RockMapper",
                                                   lambda: self.upgrade("RockMapper"))]}
        check(s, "Declared scale (RockMapper)", self.declared)
        row(s, "Declared pixel size (m)", lambda p: ttk.Spinbox(
            p, from_=0.01, to=5, increment=0.01, textvariable=self.declared_px))

        self.show_dets = tk.BooleanVar(value=True)
        self.show_truth = tk.BooleanVar(value=False)
        self.merge_dets = tk.BooleanVar(value=True)
        self.min_conf = tk.DoubleVar(value=0.0)
        self.min_conf.trace_add("write", lambda *_: self.conf_changed())
        s = section("GhostVision")
        run_gv = button(s, "Run GhostVision", self.run_gv)
        button(s, "Load GhostVision results", self.load_gv)
        button(s, "GhostVision settings…", self.gv_settings)
        check(s, "Show detections", self.show_dets, self.show)
        row(s, "Minimum confidence shown",
            lambda p: SliderWithEntry(p, self.min_conf, 0.0, 1.0, status=self.status.set))
        check(s, "Merge overlapping detections", self.merge_dets, self.conf_changed)
        check(s, "Show vent truth", self.show_truth, self.show)
        self.tool_buttons["GhostVision"] = [run_gv, button(s, "Upgrade GhostVision",
                                                           lambda: self.upgrade("GhostVision"))]

        self.console = tk.BooleanVar(value=False)
        s = section("Output")
        check(s, "Open in console window", self.console)

        T.tip_buttons(panel)
        T.tip(self.bar, "progress")
        T.tip(self.status_label, "status")

        for var in self.v.values():
            var.trace_add("write", lambda *a: self.changed())
        self.v["scene"].trace_add("write", lambda *a: self.show_vent_controls())
        self.show_vent_controls()

    def show_vent_controls(self):
        """The Vents section appears only for the vent field scene."""
        if self.v["scene"].get() == "vent field":
            self.sections["Vents"].pack(fill="x", after=self.sections["Ship and swath"])
        else:
            self.sections["Vents"].pack_forget()

    def settings(self):
        g = {k: v.get() for k, v in self.v.items()}
        g["px_m"] = float(g["px_m"])
        return C.Settings(**g)

    def reroll(self):
        self.v["seed"].set(random.randint(0, 999999))

    def changed(self):
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        if self.last_s < FAST_S:
            self.after_id = self.root.after(250, self.start_render)
        else:
            self.status.set("Settings changed. Press Render.")

    def start_render(self):
        self.after_id = None
        try:
            s = self.settings()
        except (tk.TclError, ValueError) as e:
            self.status.set(f"Bad value: {e}")
            return
        if self.busy:
            self.pending = True
            return
        self.busy = True
        self.status.set("Rendering…")
        self.begin_progress()

        def work():
            try:
                res, err = C.render(s, progress=self.report), None
            except Exception as e:  # shown in the status bar
                res, err = None, e
            self.root.after(0, self.done, res, err, s)

        threading.Thread(target=work, daemon=True).start()

    def report(self, fraction, stage=""):
        """Thread safe: workers call this; poll_progress shows it."""
        with self.lock:
            self.progress = (fraction, stage)

    def begin_progress(self, fraction=0.0):
        """Start the bar. fraction None: no progress count, the bar moves to
        show that work runs."""
        self.report(fraction, "")
        if fraction is None:
            self.bar.configure(mode="indeterminate")
            self.bar.start(20)
        else:
            self.bar.configure(mode="determinate", value=fraction)
        self.root.after(100, self.poll_progress)

    def end_progress(self):
        with self.lock:
            self.progress = None
        self.bar.stop()
        self.bar.configure(mode="determinate", value=0.0)

    def poll_progress(self):
        with self.lock:
            p = self.progress
        if p is None:
            return
        if p[0] is not None:
            self.bar.configure(value=p[0])
        self.root.after(100, self.poll_progress)

    def done(self, res, err, s=None):
        self.busy = False
        self.end_progress()
        if err:
            self.status.set(f"Render failed: {err}")
        else:
            self.result = res
            self.result_s = s
            self.last_s = res.seconds
            w, h = res.image.size
            self.status.set(f"Render time {res.seconds:.2f} s    Image {w} x {h} px    "
                            f"{res.px_m:.0f} m per pixel    Longest shadow {res.longest_shadow_m:.0f} m")
            self.show()
        if not err:
            # Detections of an earlier render no longer match: clear them, then
            # take a run made for this exact render, if one exists.
            self.dets = {name: D.Detections() for name in self.tools}
            self.load_gv(quiet=True)
            self.load_rm()
        if self.pending:
            self.pending = False
            self.start_render()

    def conf_changed(self):
        """Redraw the circles and report how many pass the confidence floor."""
        self.show()
        try:
            floor = float(self.min_conf.get())
        except (tk.TclError, ValueError):
            return
        if self.dets["GhostVision"].boxes:
            self.status.set(self.gv_status(floor))

    def gv_status(self, floor, run_name=None):
        """Status line: merged objects from raw detections at or above the
        floor, and mounds the merged detections hit (truth used for this
        count only)."""
        raw = self.dets["GhostVision"].above(floor)
        merged = raw.merged()
        sc = D.score(self.result.truth if self.result else None, merged)
        text = (f"{len(merged.boxes)} objects from {len(raw.boxes)} raw detections, "
                f"{sc['mounds_hit']} of {sc['mounds_rendered']} mounds hit, "
                f"confidence {floor:.2f} or higher")
        return text + (f" ({run_name})." if run_name else ".")

    def shown_dets(self):
        """Detections of both tools in one shape: GhostVision boxes at or above
        the minimum confidence slider, and the RockMapper class raster."""
        try:
            floor = float(self.min_conf.get())
        except (tk.TclError, ValueError):
            floor = 0.0
        rm = self.dets["RockMapper"]
        gv = self.dets["GhostVision"].above(floor)     # filter first, then merge
        if self.merge_dets.get():
            gv = gv.merged()
        return D.Detections(gv.boxes, rm.classes, rm.class_rgb)

    def show(self):
        if not self.result:
            return
        cw, ch = max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)
        img = self.result.image
        k = min(cw / img.width, ch / img.height)
        size = (max(int(img.width * k), 1), max(int(img.height * k), 1))
        resample = Image.NEAREST if k >= 1 else Image.LANCZOS
        shown = img.resize(size, resample)
        dets = self.shown_dets()
        if (self.show_dets.get() and dets) or self.show_truth.get():
            shown = D.draw_overlay(shown, dets, self.result.truth, size[0] / img.width,
                                    self.show_dets.get(), self.show_truth.get(), self.merge_dets.get())
        self.photo = ImageTk.PhotoImage(shown)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self.photo)

    def export(self, tool):
        """Write the current settings as RockMapper GeoTIFF or GhostVision tiles."""
        try:
            s = self.settings()
            C.check_size(s)
            dec = self.declared.get()
            dpx = float(self.declared_px.get())
        except (tk.TclError, ValueError) as e:
            self.status.set(f"Bad value: {e}")
            return
        gs = GA.load_settings()
        mode = ("declared" if dec else "honest") if tool == "rockmapper" else gs["mode"]
        self.status.set(f"Exporting for {tool} ({mode} scale)…")
        self.begin_progress()

        def work():
            try:
                scan = C.render(s, lambda f, stage: self.report(0.9 * f, "export"))
                if tool == "rockmapper":
                    path, (h, w) = self.tools["RockMapper"].export(scan, GA.slug_of(s), mode,
                                                                   dpx if dec else None)
                    msg = f"Wrote {path} ({w} x {h} px). Run 'Run RockMapper.cmd'."
                else:
                    gscan = C.render(dataclasses.replace(s, px_m=gs["px_m"]))
                    n, d = self.tools["GhostVision"].export(gscan, GA.slug_of(s), gs, s.vent_scale)
                    msg = f"Wrote {n} tiles to {d}. Press Run GhostVision."
            except Exception as e:  # shown in the status bar
                msg = f"Export failed: {e}"
            self.root.after(0, self.export_done, msg)

        threading.Thread(target=work, daemon=True).start()

    def export_done(self, msg):
        self.end_progress()
        self.status.set(msg)

    # ---------- GhostVision ----------

    def gv_settings(self):
        """Dialog for ghostvision_settings.json: GhostVision and tiling settings."""
        top = tk.Toplevel(self.root)
        top.title("GhostVision settings")
        cur = GA.load_settings()
        vars_ = {}

        def model_text(m):
            return f"{m} {GA.MODEL_NAMES.get(m, '')}".strip()

        for i, (name, (default, kind, lo, hi, label)) in enumerate(GA.SETTINGS.items()):
            shown_default = model_text(default) if kind == "model" else default
            ttk.Label(top, text=f"{label}, default {shown_default}").grid(
                row=i, column=0, sticky="w", padx=6, pady=2)
            if kind is bool:
                v = tk.BooleanVar(value=cur[name])
                w = ttk.Checkbutton(top, variable=v)
            elif kind == "model" or isinstance(kind, tuple):
                values = [model_text(m) for m in GA.models()] if kind == "model" else list(kind)
                v = tk.StringVar(value=model_text(cur[name]) if kind == "model" else cur[name])
                w = ttk.Combobox(top, textvariable=v, values=values, state="readonly", width=16)
            else:
                v = tk.StringVar(value=str(cur[name]))
                w = ttk.Entry(top, textvariable=v, width=18)
            w.grid(row=i, column=1, sticky="w", padx=6, pady=2)
            T.tip(w, f"gv_{name}")
            vars_[name] = v
        note = tk.StringVar()
        ttk.Label(top, textvariable=note, foreground="#c33").grid(
            row=len(vars_), column=0, columnspan=2, sticky="w", padx=6)

        def values():
            return {name: (v.get().split()[0] if GA.SETTINGS[name][1] == "model" else v.get())
                    for name, v in vars_.items()}

        def save():
            try:
                GA.save_settings(values())
            except (ValueError, TypeError) as e:
                note.set(str(e))
                return
            self.status.set(f"Saved {GA.SETTINGS_PATH.name}.")
            top.destroy()

        def reset():
            for name, v in vars_.items():
                d = GA.defaults()[name]
                if GA.SETTINGS[name][1] == "model":
                    v.set(model_text(d))
                else:
                    v.set(d if isinstance(v, tk.BooleanVar) else str(d))
            note.set("")

        row = ttk.Frame(top)
        row.grid(row=len(vars_) + 1, column=0, columnspan=2, pady=6)
        ttk.Button(row, text="Reset to defaults", command=reset).pack(side="left", padx=4)
        ttk.Button(row, text="Save", command=save).pack(side="left", padx=4)
        ttk.Button(row, text="Cancel", command=top.destroy).pack(side="left", padx=4)
        T.tip_buttons(top)
        return top

    def render_key(self):
        """Slug and settings of the displayed render, for matching run folders."""
        return GA.slug_of(self.result_s), dataclasses.asdict(self.result_s)

    def run_gv(self):
        """Export only the displayed render, in the GhostVision settings' mode,
        to its own run folder; run 'Run GhostVision.cmd' on that folder in the
        background, then load its detections. The bar counts the images it
        writes, one per tile."""
        if self.tool_running("GhostVision"):
            self.status.set("GhostVision is already running.")
            return
        if not self.result or self.result_s is None or self.busy:
            self.status.set("Nothing rendered yet. Press Render first.")
            return
        gs = GA.load_settings()
        slug, render = self.render_key()
        s, shown = self.result_s, self.result
        self.set_tool_busy("GhostVision", True)
        self.status.set(f"Rendering {slug} at {gs['px_m']:g} m per pixel for GhostVision…")
        self.begin_progress()

        def work():
            # GhostVision sees the displayed scene and seed at its own pixel size.
            try:
                es = dataclasses.replace(s, px_m=float(gs["px_m"]))
                C.check_size(es)
                scan = shown if es == s else C.render(es, lambda f, stage: self.report(f, "export"))
                out, err = self.tools["GhostVision"].export_run(scan, slug, render, gs), None
            except (OSError, ValueError) as e:
                out, err = None, e
            self.root.after(0, self.start_gv, gs, slug, render, out, err)

        threading.Thread(target=work, daemon=True).start()

    def start_gv(self, gs, slug, render, out, err):
        """Second half of run_gv, on the Tk thread: launch on the run folder."""
        self.end_progress()
        self.set_tool_busy("GhostVision", False)
        if err:
            self.status.set(f"Export failed: {err}")
            return
        n, run = out
        if not n:
            self.status.set("The render is narrower than one tile. Lower the tile length.")
            return
        self.logs["GhostVision"].clear()
        pred = run / "pred"
        try:
            self.gv_run = run
            self.gv_proc = self.launch("GhostVision", self.tools["GhostVision"].run_args(gs, run))
        except OSError as e:
            self.status.set(f"Could not start GhostVision: {e}")
            return
        self.set_tool_busy("GhostVision", True)
        self.status.set(f"Running GhostVision on {n} {gs['mode']} tiles of {slug}…")
        self.begin_progress(0.0 if gs["export_image"] else None)

        def watch():
            if self.gv_proc.poll() is None:
                if gs["export_image"] and pred.exists():
                    made = sum(1 for p in pred.iterdir() if p.suffix.lower() in (".jpg", ".png"))
                    self.report(min(made / n, 1.0), "GhostVision")
                self.root.after(500, watch)
                return
            self.end_progress()
            self.set_tool_busy("GhostVision", False)
            code = self.gv_proc.returncode
            if code != 0:
                self.status.set(f"GhostVision exited with code {code}.")
            elif self.result_s is None or self.render_key() != (slug, render):
                self.status.set(f"GhostVision finished for {slug}; the render changed since, "
                                "so its detections are not shown. Load them after rendering it again.")
            else:
                self.show_run(run)
                warn = GA.iou_warning(gs)
                if warn:
                    self.status.set(f"{self.status.get()} {warn}")

        self.root.after(500, watch)

    def show_run(self, run):
        """Detections of a run folder on the displayed render, with its summary."""
        dets = self.tools["GhostVision"].read_run(run, self.result.gray.shape)
        self.dets["GhostVision"] = dets
        try:
            floor = float(self.min_conf.get())
        except (tk.TclError, ValueError):
            floor = 0.0
        self.status.set(self.gv_status(floor, run.name))
        self.show()

    def launch(self, tool, args):
        """Start a tool's .cmd: output piped to its log window, or a visible console."""
        log = self.logs[tool]
        proc = D.launch(args, log.add, console=self.console.get())
        if not self.console.get():
            log.open()
        return proc

    def run_rm(self):
        """Run 'Run RockMapper.cmd' on the exported GeoTIFFs, both scales."""
        if self.tool_running("RockMapper"):
            self.status.set("RockMapper is already running.")
            return
        if not any((RA.INPUT / m).glob("*.tif") for m in ("declared", "honest")):
            self.status.set("No GeoTIFFs. Press Export for RockMapper first.")
            return
        try:
            self.rm_proc = self.launch("RockMapper", self.tools["RockMapper"].run_args())
        except OSError as e:
            self.status.set(f"Could not start RockMapper: {e}")
            return
        self.set_tool_busy("RockMapper", True)
        self.status.set("Running RockMapper…")
        self.begin_progress(None)

        def watch():
            if self.rm_proc.poll() is None:
                self.root.after(300, watch)
                return
            self.end_progress()
            self.set_tool_busy("RockMapper", False)
            code = self.rm_proc.returncode
            self.status.set("RockMapper finished. Results in rockmapper_results/." if code == 0
                            else f"RockMapper exited with code {code}.")
            self.load_rm()

        self.root.after(300, watch)

    def tool_running(self, tool):
        procs = [self.up_proc.get(tool), self.gv_proc if tool == "GhostVision" else self.rm_proc]
        return any(p is not None and p.poll() is None for p in procs)

    def set_tool_busy(self, tool, busy):
        """Disable a tool's Run and Upgrade buttons while it runs or upgrades."""
        for b in self.tool_buttons[tool]:
            b.state(["disabled"] if busy else ["!disabled"])

    def upgrade(self, tool):
        """Run 'Upgrade <tool>.cmd' after a confirmation, then show the
        installed version before and after in the log and status bar."""
        if self.tool_running(tool):
            self.status.set(f"{tool} is running. Wait for it to finish.")
            return
        optional = self.confirm_upgrade(tool)
        if optional is None:
            return
        log = self.logs[tool]
        self.set_tool_busy(tool, True)
        self.status.set(f"Reading the installed {tool} version…")
        self.begin_progress(None)
        versions = {}

        def read(key, then):
            def work():
                versions[key] = self.tools[tool].installed_version()
                self.root.after(0, then)
            threading.Thread(target=work, daemon=True).start()

        def start():
            log.add(f"{tool} before: {versions['before']}")
            try:
                self.up_proc[tool] = self.launch(tool, self.tools[tool].upgrade_args(optional))
            except OSError as e:
                finish(f"Could not start the {tool} upgrade: {e}")
                return
            self.status.set(f"Upgrading {tool}…")
            self.root.after(1000, watch)

        def watch():
            if self.up_proc[tool].poll() is None:
                self.root.after(1000, watch)
                return
            code = self.up_proc[tool].returncode
            self.status.set(f"Reading the new {tool} version…")
            read("after", lambda: finish(
                f"{tool} upgrade {'finished' if code == 0 else f'exited with code {code}'}. "
                f"Version before {versions['before']}, after {versions['after']}."))

        def finish(msg):
            log.add(msg)
            self.end_progress()
            self.set_tool_busy(tool, False)
            self.status.set(msg)

        read("before", start)

    def confirm_upgrade(self, tool):
        """Upgrade confirmation. Returns None on Cancel, else whether the
        tool's extra step (checked by default) runs too."""
        ad = self.tools[tool]
        if not ad.optional_step:
            ok = messagebox.askokcancel(f"Upgrade {tool}", ad.confirm_text(), icon="warning",
                                        parent=self.root)
            return False if ok else None
        top = tk.Toplevel(self.root)
        top.title(f"Upgrade {tool}")
        top.transient(self.root)
        ttk.Label(top, text=ad.confirm_text(), wraplength=520, justify="left").pack(padx=10, pady=8)
        opt = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(top, text=ad.optional_step[0], variable=opt)
        cb.pack(anchor="w", padx=10)
        T.tip(cb, "upgrade_optional")
        ttk.Label(top, text=ad.optional_step[1], wraplength=520, justify="left",
                  foreground="#555").pack(padx=10, pady=4)
        answer = {"ok": False}

        def ok():
            answer["ok"] = True
            top.destroy()
        row = ttk.Frame(top)
        row.pack(pady=8)
        ttk.Button(row, text="Upgrade", command=ok).pack(side="left", padx=4)
        ttk.Button(row, text="Cancel", command=top.destroy).pack(side="left", padx=4)
        T.tip_buttons(top)
        top.grab_set()
        self.root.wait_window(top)
        return opt.get() if answer["ok"] else None

    def load_gv(self, quiet=False):
        """Load the newest run folder made for the displayed render: same
        slug, render settings and GhostVision mode."""
        if not self.result or self.result_s is None:
            return
        mode = GA.load_settings()["mode"]
        slug, render = self.render_key()
        run = self.tools["GhostVision"].find_run(slug, render, mode)
        if run is None:
            self.dets["GhostVision"] = D.Detections()
            if not quiet:
                self.status.set(f"No GhostVision run matches this render ({slug}, {mode} scale, "
                                "current settings). Press Run GhostVision.")
            self.show()
            return
        if quiet:
            self.dets["GhostVision"] = self.tools["GhostVision"].read_run(run, self.result.gray.shape)
            self.show()
        else:
            self.show_run(run)

    def load_rm(self):
        """RockMapper's class raster for the current scene and seed, when it
        was exported from the window and matches the scan image's grid."""
        if not self.result:
            return
        try:
            s = self.settings()
            mode = "declared" if self.declared.get() else "honest"
        except (tk.TclError, ValueError):
            return
        self.dets["RockMapper"] = self.tools["RockMapper"].read(GA.slug_of(s), mode,
                                                                self.result.gray.shape)
        self.show()

    def save(self):
        if not self.result:
            self.status.set("Nothing to save. Press Render first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG image", "*.png")])
        if path:
            self.result.image.save(path)
            self.status.set(f"Saved {path}")


def arg(name, default=None):
    """Value after flag `name` in sys.argv, or default."""
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv[:-1] else default


SHOTS = ("mountain", "vents", "dialog")


def window_image(win):
    """Client area of a Tk window, drawn by Windows PrintWindow, so windows on
    top of it do not show in the picture."""
    import ctypes
    from ctypes import wintypes
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    hwnd = win.winfo_id()
    w, h = win.winfo_width(), win.winfo_height()
    hdc = u.GetDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, 3)   # PW_CLIENTONLY | PW_RENDERFULLCONTENT

    class BMI(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("rest", wintypes.DWORD * 6)]
    bmi = BMI(ctypes.sizeof(BMI), w, -h, 1, 32, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)


def screenshot(root, app, path, shot, runs=None):
    """Set up one of SHOTS, wait for its render, save the window to path, quit.
    mountain: mountain front side-scan, 20 km swath per side. vents: vent
    field with vent truth and the newest GhostVision run in `runs` merged.
    dialog: the main window with the GhostVision settings dialog over it."""
    open_ = {"mountain": ("Scene", "Ship and swath", "Look"),
             "vents": ("Vents", "GhostVision"), "dialog": ("Vents", "GhostVision")}[shot]
    for name, sec in app.sections.items():
        sec.set_open(name in open_)
    if shot == "mountain":
        app.v["swath_km"].set(20.0)
    if shot in ("vents", "dialog"):
        app.v["scene"].set("vent field")
        app.show_truth.set(shot == "vents")
    app.start_render()
    extra = []

    def wait():
        if app.busy or app.after_id or app.result is None or app.result_s.scene != app.v["scene"].get():
            root.after(200, wait)
            return
        if shot == "vents" and runs:
            found = sorted(p for p in Path(runs).iterdir() if (p / GA.RUN_INFO).exists())
            if found:
                app.show_run(found[-1])
                app.status.set(app.gv_status(0.0))   # without the run name, so the line fits
        if shot == "dialog":
            top = app.gv_settings()
            top.update_idletasks()
            x = root.winfo_rootx() + root.winfo_width() - top.winfo_reqwidth() - 40
            top.geometry(f"+{x}+{root.winfo_rooty() + 60}")
            extra.append(top)
        root.after(1500, grab)

    def grab():
        wins = [root, *extra]
        root.update()
        shots = [(w, window_image(w)) for w in wins]
        x0 = min(w.winfo_rootx() for w in wins)
        y0 = min(w.winfo_rooty() for w in wins)
        x1 = max(w.winfo_rootx() + w.winfo_width() for w in wins)
        y1 = max(w.winfo_rooty() + w.winfo_height() for w in wins)
        out = Image.new("RGB", (x1 - x0, y1 - y0), (40, 40, 40))
        for w, im in shots:
            out.paste(im, (w.winfo_rootx() - x0, w.winfo_rooty() - y0))
        out.save(path)
        print(f"wrote {path} ({out.width} x {out.height} px)")
        root.destroy()

    root.after(500, wait)


def main():
    shot_path = arg("--screenshot")
    if shot_path and sys.platform == "win32":
        # Physical pixels, so window coordinates match the screen grab under display scaling.
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    app = App(root)
    if shot_path:
        root.geometry("1280x960+20+20")
        screenshot(root, app, shot_path, arg("--shot", "mountain"), arg("--runs"))
    else:
        app.start_render()
    if "--smoke" in sys.argv:
        root.after(2000, root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
