"""Shoot the plain chart every N seconds and log how far it has moved from a reference shot.

Used to measure the monitor's colour drift (and anything else in the light path) over time,
so a sweep can be corrected for it or scheduled around it.

    python drift_log.py --ref captures/poco/sweep2/base_a.jpg --out captures/poco/drift1 --every 75 --count 8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import colour
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure import read_patches  # noqa: E402
from phone import shoot  # noqa: E402

SERIAL = os.environ.get("ADB_SERIAL", "")  # your phone: `adb devices`


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--every", type=float, default=75)
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--serial", default=SERIAL)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ref = np.array(read_patches(Path(a.ref))["Lab"])
    for i in range(a.count):
        t0 = time.time()
        p = shoot(a.serial, out / f"d{i:02d}.jpg", settle=2.0)
        lab = np.array(read_patches(p)["Lab"])
        d = colour.delta_E(lab, ref, method="CIE 2000")
        row = {"i": i, "time": time.strftime("%H:%M:%S"), "dE_mean": round(float(d.mean()), 2),
               "white_Lab": [round(v, 1) for v in lab[18]], "grey52_Lab": [round(v, 1) for v in lab[20]]}
        with open(out / "log.jsonl", "a") as f:
            f.write(json.dumps(row) + "\n")
        print(row, flush=True)
        time.sleep(max(0.0, a.every - (time.time() - t0)))


if __name__ == "__main__":
    main()
