"""Would a white-balance lever (Kelvin, tint) bring the phone closer to any Fuji look? Offline.

A WB change is emulated as a CAT02 von Kries adaptation of the phone's plain colours (dense1, all
405 colours, mean of the plain shots) from a white at (CCT, Duv) to D65, i.e. the phone told the
light is that white. Each look's best (CCT, Duv) is reported with its gain over plain. The Fuji
targets keep greys neutral to about 1 a*/b* unit, so a global cast has little to offer; this
measures how little. A fixed-Kelvin preset would also break under any other light, so a gain has
to be large to be worth it.

    python wb_check.py [--out report/wb-check.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import colour
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fit2  # noqa: E402
from fit import D65, de, fuji_target, load_cube, sims  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
D65_DUV = 0.0032  # D65 sits at about 6504 K, Duv +0.0032


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "report/wb-check.json"))
    a = ap.parse_args()
    d1, _ = fit2.load(ROOT / "captures/poco/dense1")
    bases = [c for c in d1 if c.startswith("base")]
    X = np.mean([fit2.stack(d1[b], fit2.PAGES) for b in bases], 0)
    XYZ, w65 = colour.Lab_to_XYZ(X, D65), colour.xy_to_XYZ(D65)

    def wb(cct: float, duv: float) -> np.ndarray:
        src = colour.xy_to_XYZ(colour.UCS_uv_to_xy(colour.temperature.CCT_to_uv_Ohno2013(np.array([cct, duv]))))
        return colour.XYZ_to_Lab(colour.adaptation.chromatic_adaptation_VonKries(XYZ, src, w65, "CAT02"), D65)

    grid = {(c, round(D65_DUV + u, 4)): wb(c, D65_DUV + u)
            for c in range(5000, 8501, 125) for u in np.arange(-0.006, 0.0061, 0.001)}
    provia = load_cube(sims()["PROVIA"])
    out = {}
    for sim, cube in sims().items():
        t = fuji_target(X, load_cube(cube), provia)
        plain = float(de(X, t).mean())
        scores = {k: float(de(y, t).mean()) for k, y in grid.items()}
        k = min(scores, key=scores.get)
        out[sim] = {"plain": round(plain, 2), "best_cct": k[0], "best_duv": k[1], "best": round(scores[k], 2),
                    "gain": round(plain - scores[k], 2)}
        print(f"{sim:<15} plain {plain:5.2f}   best {k[0]} K, Duv {k[1]:+.4f}: {scores[k]:5.2f}   gain {plain - scores[k]:+.2f}")
    Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
