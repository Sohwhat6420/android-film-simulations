"""Check ev_estimate.py's exposure model against REAL darker shots, and score EV as a lever on real shots.

exposure1 shot the held-out page dense_v at full brightness and at exactly half the linear light
(dense_vdim: every patch's linear sRGB x 0.501, markers untouched). A scene one stop darker at fixed
exposure is what a -1 EV shot of the full scene records, so these are real -1 EV shots, bracketed.

1. Model check: ev_estimate's plain response at -1 EV, g(displayed Y x 0.5) + residual, against
   the real dim plain shots. If it is close, its verdict on +-1/3 and +-2/3 EV stands on real data.
2. Lever check, real shots only: for each look, the best of the settings exposure1 shot (plain,
   Sat +3, Con +3, Vivid, Cinematic) at full and at half light, each scored against the Fuji target
   of the FULL-light plain shot (the scene as the user would shoot it). Darkening is a gain only
   where the dim column beats the full column.

    python ev_check.py [--out report/ev-check.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import colour
import numpy as np
from scipy.interpolate import RBFInterpolator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fit2  # noqa: E402
from ev_estimate import displayed_lab  # noqa: E402
from fit import D65, de, fuji_target, load_cube, sims, stats  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "report/ev-check.json"))
    a = ap.parse_args()
    d1, _ = fit2.load(ROOT / "captures/poco/dense1")
    bases = [c for c in d1 if c.startswith("base")]
    X = np.mean([fit2.stack(d1[b], fit2.PAGES) for b in bases], 0)
    D = displayed_lab(fit2.PAGES)
    g = RBFInterpolator(D, X, kernel="thin_plate_spline", smoothing=1.0, degree=1)
    Dv = displayed_lab(["dense_v"])
    Xv = np.mean([d1[b]["dense_v"] for b in bases], 0)
    half = colour.XYZ_to_Lab(colour.Lab_to_XYZ(Dv, D65) * 0.5014, D65)
    pred_dim = g(half) + (Xv - g(Dv))

    ex, _ = fit2.load(ROOT / "captures/poco/exposure1")
    eb = [c for c in ex if c.startswith("base")]
    full = np.mean([ex[b]["dense_v"] for b in eb], 0)
    dim = np.mean([ex[b]["dense_vdim"] for b in eb], 0)
    noise = [float(de(ex[x]["dense_vdim"], ex[y]["dense_vdim"]).mean()) for x, y in zip(eb, eb[1:])]
    out = {"model_vs_real_dim": stats(de(pred_dim, dim)),
           "real_dim_vs_real_full": stats(de(dim, full)),
           "dim_repeat_noise": round(float(np.mean(noise)), 2),
           "looks": {}}
    print(f"model -1 EV vs real dim shots: {out['model_vs_real_dim']['mean']} mean "
          f"(the one-stop change itself is {out['real_dim_vs_real_full']['mean']}; repeat noise {out['dim_repeat_noise']})")

    settings = {"plain": None} | {c: c for c in ex if not c.startswith("base")}
    provia = load_cube(sims()["PROVIA"])
    for sim, cube in sims().items():
        t = fuji_target(full, load_cube(cube), provia)
        row = {}
        for page in ("dense_v", "dense_vdim"):
            scored = {}
            for name, c in settings.items():
                y = np.mean([ex[b][page] for b in eb], 0) if c is None else ex[c][page]
                scored[name] = round(float(de(y, t).mean()), 2)
            best = min(scored, key=scored.get)
            row[page] = {"best": best, "mean": scored[best], "all": scored}
        out["looks"][sim] = row
        print(f"{sim:<15} full light: {row['dense_v']['best']:<12} {row['dense_v']['mean']:5.2f}   "
              f"one stop darker: {row['dense_vdim']['best']:<12} {row['dense_vdim']['mean']:5.2f}")
    Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
