"""Full-screen image viewer for the primary monitor, driven by a control file.

Shows whatever image path is written in scratch/viewer/show.txt (re-read every 300 ms), so the
measurement script can change the chart without touching the window. While it runs it keeps
the display awake (Windows would otherwise switch it off after 3 minutes) and wakes it on start.
Esc or deleting show.txt closes it.

    python viewer.py charts/chart60.png
"""
from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageTk

ROOT = Path(__file__).resolve().parent.parent
CONTROL = ROOT / "scratch" / "viewer" / "show.txt"
ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002


def main() -> None:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels, not scaled ones
    CONTROL.parent.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 1:
        CONTROL.write_text(str(Path(sys.argv[1]).resolve()))
    user32 = ctypes.windll.user32
    user32.mouse_event(0x0001, 1, 0, 0, 0)  # nudge the mouse to wake a sleeping display
    user32.mouse_event(0x0001, -1, 0, 0, 0)
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)

    root = tk.Tk()
    root.configure(bg="black", cursor="none")
    root.geometry(f"{user32.GetSystemMetrics(0)}x{user32.GetSystemMetrics(1)}+0+0")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    label = tk.Label(root, bg="black", bd=0)
    label.pack(fill="both", expand=True)
    state = {"path": None, "photo": None}

    def poll() -> None:
        if not CONTROL.exists():
            root.destroy()
            return
        path = CONTROL.read_text().strip()
        if path != state["path"]:
            state["path"] = path
            img = Image.open(path).convert("RGB")
            sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
            if img.size != (sw, sh):
                img = img.resize((sw, sh), Image.LANCZOS)
            state["photo"] = ImageTk.PhotoImage(img)
            label.configure(image=state["photo"])
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
        root.after(300, poll)

    root.bind("<Escape>", lambda e: root.destroy())
    poll()
    root.mainloop()
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


if __name__ == "__main__":
    main()
