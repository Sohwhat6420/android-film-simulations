"""Score REAL verification shots against each Fujifilm look, and draw split-swatch sheets.

Input: a bracketed sweep (poco_sweep.py --verify ...), where every candidate setting was shot on
the three dense pages between two plain shots. For each look and each candidate:
    target = plain + (T_sim(plain) - T_PROVIA(plain))      in CIELAB, from the bracketing plain shots
and the candidate's real shot is compared with it in dE2000. The best real candidate per look is
reported over all 405 patches and over the held-out page alone, next to "do nothing" (plain vs
target) and the noise floor (the two bracketing plain shots against each other).

Sheets (report/sheets/<look>.png): each held-out colour as a square split in two, phone preset on
the left and Fuji target on the right; a strip of the 24 worst colours; and the numbers.

    python score_verify.py --sweep captures/poco/verify1 --out report/poco-verify1.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fit import de, fuji_target, load_cube, sims, stats  # noqa: E402
from fit2 import PAGES, brackets, lab_to_srgb, load, srgb_to_lab, stack  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def target_for(plain: np.ndarray, T, provia) -> np.ndarray:
    return fuji_target(plain, T, provia)


def to_u8(lab: np.ndarray) -> np.ndarray:
    return np.uint8(np.clip(np.round(lab_to_srgb(lab) * 255), 0, 255))


def sheet(path: Path, title: str, real: np.ndarray, target: np.ndarray, lines: list[str]) -> None:
    """15 x 9 split squares (held-out page) + a strip of the 24 worst colours + text."""
    cell, gap, cols = 64, 6, 15
    rows = int(np.ceil(len(real) / cols))
    W = cols * (cell + gap) + gap
    d = de(real, target)
    worst = np.argsort(-d)[:24]
    H = 70 + rows * (cell + gap) + 40 + 2 * (cell + gap) + 30 + 26 * len(lines) + 20
    img = np.full((H, W, 3), 26, np.uint8)
    r_u8, t_u8 = to_u8(real), to_u8(target)

    def square(x, y, i):
        img[y:y + cell, x:x + cell // 2] = r_u8[i]
        img[y:y + cell, x + cell // 2:x + cell] = t_u8[i]

    cv2.putText(img, title, (gap, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (240, 240, 240), 2, cv2.LINE_AA)
    cv2.putText(img, "each square: left = phone preset, right = Fujifilm target", (gap, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (170, 170, 170), 1, cv2.LINE_AA)
    y0 = 70
    for i in range(len(real)):
        square(gap + (i % cols) * (cell + gap), y0 + (i // cols) * (cell + gap), i)
    y1 = y0 + rows * (cell + gap) + 30
    cv2.putText(img, "24 worst colours (dE2000 under each)", (gap, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (170, 170, 170), 1, cv2.LINE_AA)
    for k, i in enumerate(worst):
        x, y = gap + (k % 12) * (cell + gap) * 1.25, y1 + (k // 12) * (cell + gap)
        x = int(x)
        square(x, y, i)
        cv2.putText(img, f"{d[i]:.1f}", (x + cell + 2, y + cell // 2 + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                    (200, 200, 200), 1, cv2.LINE_AA)
    y2 = y1 + 2 * (cell + gap) + 24
    for k, line in enumerate(lines):
        cv2.putText(img, line, (gap, y2 + 26 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1, cv2.LINE_AA)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img[..., ::-1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sheets", default=str(ROOT / "report" / "sheets"))
    ap.add_argument("--force", nargs="*", default=[], metavar="SIM=FILTER,SAT,CON",
                    help="take this setting for a look instead of the chart's best (chosen on photos too, "
                         "scene_fit.py); FILTER,0,0 with FILTER=Original means leave the camera alone")
    ap.add_argument("--only", nargs="*", help="score just these looks")
    a = ap.parse_args()
    force = {}
    for f in a.force:
        sim, v = f.split("=")
        flt, sat, con = v.split(",")
        force[sim] = (flt.lower().replace(" ", "-"), int(sat), int(con))
    shots, rows = load(Path(a.sweep))
    br = brackets(shots, rows)
    settings = {r["config"]: (r["filter"], r["Saturation"], r["Contrast"]) for r in rows}
    bases = [c for c in shots if c.startswith("base")]
    noise = [float(de(stack(shots[x], PAGES), stack(shots[y], PAGES)).mean()) for x, y in zip(bases, bases[1:])]
    cubes = sims()
    provia = load_cube(cubes["PROVIA"])
    out = {"noise_floor_neighbour_plain_dE": stats(np.array(noise)), "sims": {}}
    for sim, cube in cubes.items():
        if a.only and sim not in a.only:
            continue
        T = load_cube(cube)
        scored = []
        for c, plain in br.items():
            tgt = {p: target_for(plain[p], T, provia) for p in PAGES}
            d_all = de(stack(shots[c], PAGES), stack(tgt, PAGES))
            d_val = de(shots[c]["dense_v"], tgt["dense_v"])
            nothing = de(stack(plain, PAGES), stack(tgt, PAGES))
            scored.append((float(d_all.mean()), c, stats(d_all), stats(d_val), stats(nothing), tgt))
        scored.sort(key=lambda s: s[0])
        want = force.get(sim)
        if want and want != ("original", 0, 0):
            scored.sort(key=lambda s: (settings[s[1]][0].lower().replace(" ", "-"),) + tuple(settings[s[1]][1:]) != want)
            assert want == (settings[scored[0][1]][0].lower().replace(" ", "-"),) + tuple(settings[scored[0][1]][1:]),                 f"{sim}: {want} was not shot in this sweep"
        _, c, s_all, s_val, s_nothing, tgt = scored[0]
        f, sat, con = settings[c]
        if want == ("original", 0, 0) or (not want and s_nothing["mean"] <= s_all["mean"]):  # leave the camera alone
            f, sat, con, c = "Original", 0, 0, "plain"
            s_all = s_nothing
            s_val = stats(de(br[scored[0][1]]["dense_v"], tgt["dense_v"]))
        out["sims"][sim] = {"best": {"filter": f, "Saturation": sat, "Contrast": con, "config": c},
                            "all_patches": s_all, "held_out_page": s_val, "do_nothing": s_nothing,
                            "runner_up": [{"config": x[1], "all_patches": x[2]} for x in scored[1:3]]}
        lines = [f"Best real setting: filter {f}, Saturation {sat:+d}, Contrast {con:+d}",
                 f"dE2000 all 405 colours: mean {s_all['mean']}, p90 {s_all['p90']}, max {s_all['max']}",
                 f"dE2000 held-out 135 colours: mean {s_val['mean']}, p90 {s_val['p90']}",
                 f"Doing nothing (plain photo vs target): mean {s_nothing['mean']}",
                 f"Shot-to-shot noise floor: mean {out['noise_floor_neighbour_plain_dE']['mean']}"]
        shown = br[scored[0][1]]["dense_v"] if c == "plain" else shots[c]["dense_v"]
        hexes = lambda lab: ["#%02x%02x%02x" % tuple(v) for v in to_u8(lab)]  # noqa: E731
        out["sims"][sim]["swatches"] = {"phone": hexes(shown), "fuji": hexes(tgt["dense_v"]),
                                        "plain": hexes(br[scored[0][1]]["dense_v"]),
                                        "dE": [round(float(x), 2) for x in de(shown, tgt["dense_v"])],
                                        "dE_plain": [round(float(x), 2) for x in de(br[scored[0][1]]["dense_v"], tgt["dense_v"])]}
        sheet(Path(a.sheets) / f"{sim}.png", f"Fujifilm {sim} on POCO X6 Pro", shown, tgt["dense_v"], lines)
        print(f"{sim:<15} best {f} s{sat:+d} c{con:+d}  all {s_all['mean']:5.2f}  held-out {s_val['mean']:5.2f}  "
              f"nothing {s_nothing['mean']:5.2f}")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
