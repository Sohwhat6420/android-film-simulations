"""Shoot with the phone's camera over adb and pull the new photo.

The camera must already be open in the wanted mode. The shot is triggered by tapping the shutter
button (its position comes from the camera's own layout, found with a uiautomator dump), and the
newest file in DCIM/Camera is pulled once it has finished writing.

    python phone.py --serial <serial> --out captures/poco/baseline.jpg
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

ADB = os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Genymobile.scrcpy_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\scrcpy-win64-v4.1\adb.exe"
)
CAMERA_DIR = "/sdcard/DCIM/Camera"


def adb(serial: str, *args: str, check: bool = True) -> str:
    r = subprocess.run([ADB, "-s", serial, *args], capture_output=True, text=True, timeout=120)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def newest(serial: str) -> tuple[str, int] | None:
    out = adb(serial, "shell", f"ls -t {CAMERA_DIR} | head -1").strip()
    if not out:
        return None
    size = adb(serial, "shell", f"stat -c %s '{CAMERA_DIR}/{out}'").strip()
    return out, int(size or 0)


def shoot(serial: str, out: Path, shutter=(609, 2360), settle: float = 1.5, timeout: float = 20) -> Path:
    before = newest(serial)
    time.sleep(settle)  # let the preview settle after any setting change
    adb(serial, "shell", "input", "tap", str(shutter[0]), str(shutter[1]))
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        time.sleep(0.7)
        cur = newest(serial)
        if cur and (before is None or cur[0] != before[0]) and cur[1] > 0:
            if last == cur:  # size unchanged across two polls: finished writing
                out.parent.mkdir(parents=True, exist_ok=True)
                adb(serial, "pull", f"{CAMERA_DIR}/{cur[0]}", str(out))
                return out
            last = cur
    raise TimeoutError("no new photo appeared in DCIM/Camera")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(shoot(a.serial, Path(a.out)))
