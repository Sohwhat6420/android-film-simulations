"""Is the Fuji target built right? Two checks against the genuine chart pairs, no new data.

1. Decode curve. build_targets.py reads the official LUTs' output with the sRGB curve, which beat
   the BT.709 OETF. Pure gamma 2.2 and BT.1886 (gamma 2.4) were never tried. Each curve rebuilds
   T_sim (33 grid) and is scored on the five looks with a genuine frame (24 patches + whole image).
2. Look strength. If Fuji's stills looks are uniformly stronger or weaker than its video LUTs, a
   shared correction  target = provia + a * (T(provia) - provia)  in Lab, with one a for L* and one
   for a*b*, should help every look. It keeps Provia exact by construction. Fitted on four looks
   and tested on the fifth (leave one look out); adopted only if it helps every held-out look.
   Per-look a values are printed as a diagnostic, not as a correction: fitting each look to these
   frames would copy Capture One, which exported them.

    python target_variants.py [--out report/target-variants.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import least_squares

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_targets as bt  # noqa: E402

GRID = 33
VALIDATED = ["ASTIA", "CLASSIC-CHROME", "ETERNA", "PRO-Neg.Std", "Velvia"]


def power(g: float):
    return (lambda lin: np.power(np.clip(lin, 0, None), 1 / g)), (lambda v: np.power(np.clip(v, 0, None), g))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(bt.ROOT / "report/target-variants.json"))
    a = ap.parse_args()
    t0 = time.time()
    bt.CONVENTIONS["gamma22"] = power(2.2)
    bt.CONVENTIONS["gamma24"] = power(2.4)
    luts = {s: bt.read_cube(bt.LUT_DIR / f"FLog2_to_{s}_65grid_V.1.00.cube") for s in [bt.BASE] + VALIDATED}
    ref = {s: bt.load_fuji(bt.GENUINE[s]) for s in [bt.BASE] + VALIDATED}
    small = {s: cv2.resize(v, bt.SMALL, interpolation=cv2.INTER_AREA).astype(np.float64) / 255 for s, v in ref.items()}
    rng = np.random.default_rng(bt.SEED)
    pix = rng.choice(small[bt.BASE].shape[0] * small[bt.BASE].shape[1], 20000, replace=False)
    prov_px = small[bt.BASE].reshape(-1, 3)[pix]
    ref_px_lab = {s: bt.lab(small[s].reshape(-1, 3)[pix]) for s in VALIDATED}
    p_prov = bt.patch_labs(ref[bt.BASE])
    p_ref = {s: bt.patch_labs(ref[s]) for s in VALIDATED}
    out = {"curves": {}, "strength": {}}

    pred = {}
    inverse = bt.make_inverse(luts[bt.BASE])
    X = bt.cube_points(GRID)
    for conv in ("srgb", "bt709", "gamma22", "gamma24"):
        v, _ = inverse(bt.to_lut(X, conv))
        row = {}
        for s in VALIDATED:
            T = bt.from_lut(bt.apply_lut(luts[s], v), conv).reshape(GRID, GRID, GRID, 3)
            pt = bt.patch_labs(ref[bt.BASE], T)
            px = bt.lab(bt.apply_lut(T, prov_px))
            row[s] = {"patch": round(float(bt.de2000(pt, p_ref[s]).mean()), 3),
                      "image": round(float(bt.de2000(px, ref_px_lab[s]).mean()), 3)}
            pred[(conv, s)] = (pt, px)
        row["mean_patch"] = round(float(np.mean([row[s]["patch"] for s in VALIDATED])), 3)
        row["mean_image"] = round(float(np.mean([row[s]["image"] for s in VALIDATED])), 3)
        out["curves"][conv] = row
        bt.log(f"{conv:<8} patch " + "  ".join(f"{s[:6]} {row[s]['patch']:.2f}" for s in VALIDATED)
               + f"  | mean patch {row['mean_patch']:.3f}  image {row['mean_image']:.3f}", t0)

    best = min(out["curves"], key=lambda c: out["curves"][c]["mean_patch"] + out["curves"][c]["mean_image"])
    out["best_curve"] = best
    prov_px_lab = bt.lab(prov_px)

    def apply(k, s):
        pt, px = pred[(best, s)]
        f = lambda base, t: np.concatenate([base[:, :1] + k[0] * (t[:, :1] - base[:, :1]),  # noqa: E731
                                            base[:, 1:] + k[1] * (t[:, 1:] - base[:, 1:])], 1)
        return f(p_prov, pt), f(prov_px_lab, px)

    def resid(k, looks):
        r = []
        for s in looks:
            pt, px = apply(k, s)
            r += [(pt - p_ref[s]).ravel() * np.sqrt(len(px) / 24), (px - ref_px_lab[s]).ravel()]
        return np.concatenate(r)

    def score(k, s):
        pt, px = apply(k, s)
        return float(bt.de2000(pt, p_ref[s]).mean()), float(bt.de2000(px, ref_px_lab[s]).mean())

    for s in VALIDATED:
        k_own = least_squares(resid, [1.0, 1.0], args=([s],)).x
        rest = [x for x in VALIDATED if x != s]
        k_loo = least_squares(resid, [1.0, 1.0], args=(rest,)).x
        base_p, base_i = score([1, 1], s)
        loo_p, loo_i = score(k_loo, s)
        out["strength"][s] = {"own_aL": round(k_own[0], 3), "own_aC": round(k_own[1], 3),
                              "loo_aL": round(k_loo[0], 3), "loo_aC": round(k_loo[1], 3),
                              "patch": [round(base_p, 3), round(loo_p, 3)], "image": [round(base_i, 3), round(loo_i, 3)]}
        bt.log(f"{s:<15} own a=({k_own[0]:.2f}, {k_own[1]:.2f})  held-out a=({k_loo[0]:.2f}, {k_loo[1]:.2f}): "
               f"patch {base_p:.3f} -> {loo_p:.3f}, image {base_i:.3f} -> {loo_i:.3f}", t0)
    gains = [min(v["patch"][0] - v["patch"][1], v["image"][0] - v["image"][1]) for v in out["strength"].values()]
    out["strength_adopt"] = bool(min(gains) > 0.05)
    print(f"best curve: {best}; shared strength correction helps every held-out look by >0.05: {out['strength_adopt']}")
    Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
