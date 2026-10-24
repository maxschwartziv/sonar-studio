"""Slider with an entry box beside it for typing an exact value."""
import tkinter as tk
from tkinter import ttk


def parse_value(text, lo, hi):
    """Typed text as a float clamped to lo..hi; None when it is not a number."""
    try:
        x = float(text.strip().replace(",", "."))
    except ValueError:
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return min(hi, max(lo, x))


class SliderWithEntry(ttk.Frame):
    """ttk.Scale and an entry on one DoubleVar. Enter or leaving the box sets the value.

    ttk.Scale moves without steps, so a typed value is kept exactly. A non-number
    restores the old value and calls status(message); an out-of-range number is
    clamped and the box shows the clamped value.
    """

    def __init__(self, parent, var, lo, hi, fmt="{:.2f}", status=None):
        super().__init__(parent)
        self.var, self.lo, self.hi, self.fmt = var, lo, hi, fmt
        self.status = status or (lambda msg: None)
        self.text = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.text, width=7)
        self.entry.pack(side="right")
        ttk.Scale(self, from_=lo, to=hi, variable=var).pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", self.commit)
        self.entry.bind("<KP_Enter>", self.commit)
        self.entry.bind("<FocusOut>", self.commit)
        var.trace_add("write", lambda *a: self.refresh())
        self.refresh()

    def refresh(self):
        self.text.set(self.fmt.format(self.var.get()))

    def commit(self, _event=None):
        x = parse_value(self.text.get(), self.lo, self.hi)
        if x is None:
            self.status(f"Not a number: {self.text.get()!r}. Kept {self.fmt.format(self.var.get())}.")
            self.refresh()
            self.entry.state(["invalid"])
            self.after(600, lambda: self.entry.state(["!invalid"]))
            return
        if x != self.var.get():
            self.var.set(x)   # fires the same traces as dragging
        self.refresh()
