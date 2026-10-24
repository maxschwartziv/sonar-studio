"""Scrollable left panel with collapsible sections. Open sections are saved."""
import json
import tkinter as tk
from pathlib import Path
from tkinter import ttk

STATE_PATH = Path(__file__).with_name("sections.json")


def load_state(path=STATE_PATH):
    """Saved {section name: open}; {} when missing or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): bool(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state, path=STATE_PATH):
    try:
        path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass


class ScrollPanel(ttk.Frame):
    """Vertical scrolling frame; put widgets in .inner."""

    def __init__(self, parent, width=250):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, width=width, highlightthickness=0)
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas, padding=8)
        self.win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self.fit)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.bind_all("<MouseWheel>", self.wheel, add="+")

    def fit(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"),
                              width=self.inner.winfo_reqwidth())

    def wheel(self, e):
        w = e.widget
        while w is not None:   # scroll only when the pointer is over the panel
            if w is self:
                if self.canvas.yview() != (0.0, 1.0):
                    self.canvas.yview_scroll(int(-e.delta / 120), "units")
                return
            w = getattr(w, "master", None)


class Section(ttk.Frame):
    """Header button (▸ closed, ▾ open) over a body frame."""

    def __init__(self, parent, name, state, on_toggle):
        super().__init__(parent)
        self.name, self.state, self.on_toggle = name, state, on_toggle
        self.header = ttk.Button(self, style="Section.TButton", command=self.toggle)
        self.header.pack(fill="x", pady=(6, 0))
        self.body = ttk.Frame(self, padding=(8, 0, 0, 4))
        self.set_open(state.get(name, True))

    def set_open(self, is_open):
        self.is_open = is_open
        self.header.configure(text=("▾ " if is_open else "▸ ") + self.name)
        if is_open:
            self.body.pack(fill="x")
        else:
            self.body.pack_forget()

    def toggle(self):
        self.set_open(not self.is_open)
        self.state[self.name] = self.is_open
        self.on_toggle()
