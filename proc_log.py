"""Run a tool and show its console output inside Sonar Studio."""
import collections
import os
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk

import tooltips as T

MAX_LINES = 5000


def start(args, cwd, on_line, console=False):
    """Start args. Piped: stdout and stderr merged, each line passed to on_line
    from a reader thread, then an "[exit code N]" line. console: a visible cmd
    window that stays open (cmd /k), no capture. Returns the Popen."""
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    if console:
        return subprocess.Popen(["cmd", "/k", *args], cwd=cwd, env=env,
                                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    proc = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def read():
        for raw in proc.stdout:
            on_line(raw.decode("utf-8", "replace").rstrip("\r\n"))
        on_line(f"[exit code {proc.wait()}]")

    threading.Thread(target=read, daemon=True).start()
    return proc


class LogWindow:
    """Scrolling monospace log, one per tool. Lines arrive from any thread."""

    def __init__(self, root, title):
        self.root, self.title = root, title
        self.q = queue.Queue()
        self.lines = collections.deque(maxlen=MAX_LINES)
        self.top = None
        root.after(200, self.tick)

    def tick(self):
        try:
            self.pump()
            self.root.after(200, self.tick)
        except tk.TclError:   # root destroyed
            pass

    def open(self):
        if self.top and self.top.winfo_exists():
            self.top.deiconify()
            self.top.lift()
            return
        self.top = tk.Toplevel(self.root)
        self.top.title(f"{self.title} output")
        self.top.geometry("820x420")
        bar = ttk.Frame(self.top)
        bar.pack(side="bottom", fill="x")
        ttk.Button(bar, text="Copy", command=self.copy).pack(side="left", padx=4, pady=4)
        ttk.Button(bar, text="Clear", command=self.clear).pack(side="left", padx=4, pady=4)
        self.text = tk.Text(self.top, font=("Consolas", 9), wrap="word", bg="#02080d", fg="#9fd")
        sb = ttk.Scrollbar(self.top, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.text.insert("end", "".join(l + "\n" for l in self.lines))
        self.text.see("end")
        self.text.configure(state="disabled")
        T.tip(self.text, "log")
        T.tip_buttons(self.top)

    def add(self, line):
        """Thread-safe; call pump() on the Tk thread to show queued lines."""
        self.q.put(line)

    def pump(self):
        new = []
        while True:
            try:
                new.append(self.q.get_nowait())
            except queue.Empty:
                break
        self.lines.extend(new)
        if new and self.top and self.top.winfo_exists():
            t = self.text
            t.configure(state="normal")
            t.insert("end", "".join(l + "\n" for l in new))
            extra = int(t.index("end-1c").split(".")[0]) - 1 - MAX_LINES
            if extra > 0:
                t.delete("1.0", f"{extra + 1}.0")
            t.see("end")
            t.configure(state="disabled")

    def copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(self.lines))

    def clear(self):
        self.lines.clear()
        if self.top and self.top.winfo_exists():
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.configure(state="disabled")
