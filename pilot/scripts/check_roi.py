"""Regression check for the region-of-interest rewrite of measure.read_patches.

For every per-shot JSON in captures/ (437 of them), re-read its photo with BOTH the whole-frame version of measure.py
(--old, a copy of the file from before the rewrite) and the current one, and check:
  * the largest per-patch CIEDE2000 between the two Lab results is <= 0.01 for every shot;
  * `max_code` is identical;
  * CPU time per shot falls by >= 70 %.
The stored JSON is compared too, for information only: some were written by an earlier detector (a few tenths of
a dE of difference from either version), so they cannot judge a rewrite that must not change the maths.

    git show <before>:./scripts/measure.py > scratch/measure_old.py   # then set its ROOT to this folder
    .venv/Scripts/python scripts/check_roi.py --old scratch/measure_old.py [--limit N] [--out scratch/roi-check.json]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import colour
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import measure  # noqa: E402


def load_old(path: Path):
    spec = importlib.util.spec_from_file_location("measure_old", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def progress(*args: str) -> None:
    sh = os.environ.get("CLAUDE_PROGRESS_SH")
    if sh:
        subprocess.run(["bash", sh, "-n", "roi-check", *args], check=False, capture_output=True)


def de00(a: dict, b: dict) -> float:
    return float(colour.delta_E(np.array(a["Lab"]), np.array(b["Lab"]), method="CIE 2000").max())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT / "captures"))
    ap.add_argument("--old", required=True, help="the whole-frame measure.py")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    old_mod = load_old(Path(a.old))
    jsons = sorted(p for p in Path(a.root).rglob("*.json") if p.with_suffix(".jpg").exists())
    jsons = [p for p in jsons if "Lab" in json.loads(p.read_text())]
    if a.limit:
        jsons = jsons[: a.limit]
    print(f"{len(jsons)} per-shot JSONs with a photo", flush=True)
    progress("-t", str(len(jsons)))
    rows, failures = [], []
    cpu_old = cpu_new = 0.0
    for i, jp in enumerate(jsons, 1):
        stored = json.loads(jp.read_text())
        photo, chart = jp.with_suffix(".jpg"), stored.get("chart", "chart60")
        try:
            t0 = time.process_time()
            o = old_mod.read_patches(photo, chart=chart)
            t1 = time.process_time()
            n = measure.read_patches(photo, chart=chart)
            t2 = time.process_time()
        except Exception as e:
            failures.append((jp.name, repr(e)))
            continue
        cpu_old += t1 - t0
        cpu_new += t2 - t1
        rows.append({"shot": str(jp.relative_to(a.root)), "de_old_new": de00(o, n), "de_stored_new": de00(stored, n),
                     "max_code_equal": o["max_code"] == n["max_code"], "xyz_equal": o["XYZ"] == n["XYZ"],
                     "cpu_old": t1 - t0, "cpu_new": t2 - t1})
        if i % 5 == 0:
            progress(f"{i}/{len(jsons)}", f"worst old-vs-new {max(r['de_old_new'] for r in rows):.1e}")
    progress("done")
    worst = max(r["de_old_new"] for r in rows)
    bad = [r["shot"] for r in rows if not r["max_code_equal"]]
    cut = 1 - cpu_new / cpu_old
    print(f"shots compared: {len(rows)}; unreadable: {len(failures)} {failures[:3]}")
    print(f"old vs new: max dE00 over every patch of every shot {worst:.3e}; XYZ bit-identical on {sum(r['xyz_equal'] for r in rows)}/{len(rows)}")
    print(f"max_code mismatches: {len(bad)} {bad[:3]}")
    print(f"stored JSON vs new (information only): worst {max(r['de_stored_new'] for r in rows):.3f}, "
          f"{sum(r['de_stored_new'] > 0.01 for r in rows)} of {len(rows)} above 0.01")
    print(f"CPU per shot: whole-frame {cpu_old / len(rows):.2f} s, ROI {cpu_new / len(rows):.2f} s, cut {cut:.1%}")
    if a.out:
        Path(a.out).write_text(json.dumps({"rows": rows, "failures": failures}))
    ok = worst <= 0.01 and not bad and not failures and cut >= 0.70
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
