"""Would an exposure-compensation lever help? An offline estimate before any shooting.

A preset can carry EV compensation (FORMAT.md), so EV is a fourth lever beside filter, Saturation
and Contrast. This predicts its value from dense1 alone:
- the phone's plain response to a displayed colour, g (displayed Lab -> plain Lab, RBF over the
  405 dense colours, using the mean of all plain shots);
- a shot at EV e of displayed colour d is taken as g(d with Y x 2^e), corrected by the measured
  residual at e = 0, so e = 0 reproduces the real plain shot exactly;
- the filter and slider effects (fit2.Effect) are then applied on top, and scored against the
  Fuji target of the UNSHIFTED plain shot (the scene as the user would have shot it).
Bright colours pushed above the display's white are extrapolated, so +EV is the less certain side.

    python ev_estimate.py --sweep captures/poco/dense1 [--out report/ev-estimate.json]
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import colour
import numpy as np
from scipy.interpolate import RBFInterpolator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fit2  # noqa: E402
from fit import D65, de, fuji_target, load_cube, sims  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVS = (-2 / 3, -1 / 3, 0.0, 1 / 3, 2 / 3)


def displayed_lab(pages) -> np.ndarray:
    out = []
    for page in pages:
        shown = {p["name"]: p["srgb"] for p in json.loads((ROOT / f"charts/{page}.json").read_text())["patches"]}
        names = json.loads(next((ROOT / "captures/poco/dense1").glob(f"base00@{page}.json")).read_text())["names"]
        out.append(np.array([shown[n] for n in names], float) / 255)
    return colour.XYZ_to_Lab(colour.sRGB_to_XYZ(np.concatenate(out)), D65)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--out", default=str(ROOT / "report/ev-estimate.json"))
    a = ap.parse_args()
    shots, rows = fit2.load(Path(a.sweep))
    br = fit2.brackets(shots, rows)
    bases = [c for c in shots if c.startswith("base")]
    effects = {c: fit2.Effect(fit2.stack(ref, fit2.TRAIN), fit2.stack(shots[c], fit2.TRAIN)) for c, ref in br.items()}
    X = np.mean([fit2.stack(shots[b], fit2.PAGES) for b in bases], 0)
    D = displayed_lab(fit2.PAGES)
    g = RBFInterpolator(D, X, kernel="thin_plate_spline", smoothing=1.0, degree=1)
    resid = X - g(D)

    def at_ev(e: float) -> np.ndarray:
        xyz = colour.Lab_to_XYZ(D, D65) * 2 ** e
        return g(colour.XYZ_to_Lab(xyz, D65)) + resid

    lever = lambda p, v: effects.get(f"{p}{v:+d}") if v else None
    filters = {"Original": None} | {c[2:]: e for c, e in effects.items() if c.startswith("f_")}
    provia = load_cube(sims()["PROVIA"])
    targets = {s: fuji_target(X, load_cube(c), provia) for s, c in sims().items()}
    best = {s: {} for s in targets}
    for e in EVS:
        base = at_ev(e)
        for f, s, k in itertools.product(filters, range(-3, 4), range(-3, 4)):
            if (s and lever("sat", s) is None) or (k and lever("con", k) is None):
                continue
            y = base
            for eff in (filters[f], lever("sat", s), lever("con", k)):
                if eff is not None:
                    y = eff(y)
            for sim, t in targets.items():
                m = float(de(y, t).mean())
                key = f"{e:+.2f}"
                if key not in best[sim] or m < best[sim][key]["mean"]:
                    best[sim][key] = {"mean": round(m, 2), "filter": f, "Saturation": s, "Contrast": k}
    for sim, by_ev in best.items():
        row = "  ".join(f"{k}:{v['mean']:5.2f}" for k, v in by_ev.items())
        top = min(by_ev.items(), key=lambda kv: kv[1]["mean"])
        print(f"{sim:<15} {row}   best EV {top[0]} {top[1]['filter']} s{top[1]['Saturation']:+d} c{top[1]['Contrast']:+d}")
    Path(a.out).write_text(json.dumps(best, indent=1))


if __name__ == "__main__":
    main()
