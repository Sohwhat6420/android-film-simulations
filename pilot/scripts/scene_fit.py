"""Rank camera settings on real photos as well as on chart colours.

The verified presets were chosen on 405 chart colours, which cover colour space evenly. Photos do
not: they are mostly skin, foliage, sky and neutrals at middling chroma. scenes1 (19 Oct) showed
the gap: for Reala Ace the chart-best preset was WORSE than doing nothing on 5 of 6 photos, and
Velvia's only tied. So each candidate is now also scored on the six scene pages:
- the plain shot of each scene (scenes1, rectified, 640 px wide, as scene_check does) is pushed
  through the same learnt setting effects as fit2 (thin-plate RBF in Lab, trained on dense1's
  dense_a + dense_b), and compared pixel by pixel with that photo's Fuji target;
- the chart score is fit2's (dense1, all three pages, mean plain shot);
- combined = the mean of the chart score and the mean scene score.
The model is checked against the real scene shots of scenes1 (the configs it actually shot) before
any of its rankings is trusted. As always, the winners are then shot for real.

    python scene_fit.py --dense captures/poco/dense1 --scenes captures/poco/scenes1 --out report/poco-scenefit.json
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fit2  # noqa: E402
from fit import de, fuji_target, load_cube, sims, stats  # noqa: E402
from scene_check import lab, to_srgb  # noqa: E402

STEP = 5  # every 5th pixel each way of the 640-px frame: about 11,000 pixels a scene


def scene_pixels(path: Path, page: str) -> tuple[np.ndarray, np.ndarray]:
    rgb, raw = to_srgb(path, page)
    rgb, raw = rgb[::STEP, ::STEP].reshape(-1, 3), raw[::STEP, ::STEP].reshape(-1, 3)
    L = lab(rgb)
    return L, (raw.max(-1) < 0.97) & (L[:, 0] > 6)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dense", required=True)
    ap.add_argument("--scenes", required=True)
    ap.add_argument("--real", default="report/poco-scenes1.json", help="real scene scores, for the model check")
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int, default=3)
    a = ap.parse_args()

    shots, rows = fit2.load(Path(a.dense))
    br = fit2.brackets(shots, rows)
    effects = {c: fit2.Effect(fit2.stack(ref, fit2.TRAIN), fit2.stack(shots[c], fit2.TRAIN)) for c, ref in br.items()}
    bases = [c for c in shots if c.startswith("base")]
    X = np.mean([fit2.stack(shots[b], fit2.PAGES) for b in bases], 0)

    # one plain shot per scene page (the first), and every real (setting, page) pair for the model check
    sweep = Path(a.scenes)
    srows = [json.loads(l) for l in (sweep / "log.jsonl").read_text().splitlines()]
    plain_of, last_plain, real_pairs = {}, {}, {}
    for r in srows:
        p = sweep / f"{r['tag']}.jpg"
        if r["config"].startswith("base"):
            plain_of.setdefault(r["page"], p)
            last_plain[r["page"]] = p
        elif r["page"] in last_plain:
            real_pairs[(r["filter"].lower().replace(" ", "-"), r["Saturation"], r["Contrast"], r["page"])] = last_plain[r["page"]]
    scenes = {pg: scene_pixels(p, pg) for pg, p in plain_of.items()}
    print("scenes:", {pg: int(v.sum()) for pg, (_, v) in scenes.items()})

    lever = lambda p, v: effects.get(f"{p}{v:+d}") if v else None  # noqa: E731
    filters = {"original": None} | {c[2:]: e for c, e in effects.items() if c.startswith("f_")}
    keys = [(f, s, k) for f, s, k in itertools.product(filters, range(-3, 4), range(-3, 4))
            if not ((s and lever("sat", s) is None) or (k and lever("con", k) is None))]

    # all inputs (chart + every scene) in one array, and each stage cached: filter, then
    # Saturation, then Contrast, as the camera applies them
    parts = [X] + [L[v] for L, v in scenes.values()]
    cuts = np.cumsum([len(x) for x in parts])[:-1]
    stage = lambda e, y: y if e is None else e(y)  # noqa: E731
    by_f = {f: stage(filters[f], np.concatenate(parts)) for f in filters}
    by_fs = {}

    provia = load_cube(sims()["PROVIA"])
    cubes = {sim: load_cube(c) for sim, c in sims().items()}
    chart_t = {sim: fuji_target(X, T, provia) for sim, T in cubes.items()}
    scene_t = {sim: {pg: fuji_target(L[v], T, provia) for pg, (L, v) in scenes.items()} for sim, T in cubes.items()}
    chart, scene = {sim: {} for sim in cubes}, {sim: {} for sim in cubes}
    for n, key in enumerate(keys):
        f, s, k = key
        if (f, s) not in by_fs:
            by_fs[(f, s)] = stage(lever("sat", s), by_f[f])
        yc, *rest = np.split(stage(lever("con", k), by_fs[(f, s)]), cuts)
        ys = dict(zip(scenes, rest))
        for sim in cubes:
            chart[sim][key] = float(de(yc, chart_t[sim]).mean())
            scene[sim][key] = {pg: float(de(ys[pg], scene_t[sim][pg]).mean()) for pg in scenes}
        if n % 100 == 0:
            print(f"{n}/{len(keys)} candidates", flush=True)

    # model check: predicted vs real scene error for every setting scenes1 actually shot
    real = json.loads(Path(a.real).read_text()) if Path(a.real).exists() else {}
    best1 = json.loads(Path("report/poco-verify1.json").read_text())["sims"]
    check = {}
    for sim, per_page in real.items():
        b = best1[sim]["best"]
        key = (b["filter"].lower().replace(" ", "-"), b["Saturation"], b["Contrast"])
        if key not in scene[sim] or not per_page:
            continue
        check[sim] = {"setting": list(key), "pages": {
            pg.removeprefix("scene_"): {"predicted": round(scene[sim][key][pg], 2), "real": r["look_vs_target"]["mean"],
                                        "nothing_pred": round(scene[sim][("original", 0, 0)][pg], 2),
                                        "nothing_real": r["plain_vs_target"]["mean"]}
            for pg, r in per_page.items() if pg in scene[sim][key]}}
    errs = [abs(v["predicted"] - v["real"]) for c in check.values() for v in c["pages"].values()]
    print("model check |pred - real| scene dE:", stats(np.array(errs)) if errs else "none")

    out = {"note": __doc__.split("\n\n")[0], "model_check_abs_err": stats(np.array(errs)) if errs else None,
           "model_check": check, "sims": {}}
    for sim in cubes:
        sc = {key: float(np.mean(list(v.values()))) for key, v in scene[sim].items()}
        comb = {key: (chart[sim][key] + sc[key]) / 2 for key in keys}
        rank = sorted(keys, key=comb.get)
        row = lambda key: {"filter": key[0], "Saturation": key[1], "Contrast": key[2],  # noqa: E731
                           "chart": round(chart[sim][key], 2), "scenes": round(sc[key], 2), "combined": round(comb[key], 2),
                           "per_scene": {pg.removeprefix("scene_"): round(x, 2) for pg, x in scene[sim][key].items()}}
        out["sims"][sim] = {"do_nothing": row(("original", 0, 0)),
                            "chart_best": row(min(keys, key=chart[sim].get)),
                            "scene_best": row(min(keys, key=sc.get)),
                            "candidates": [row(k) for k in rank[:a.top]]}
        b, n0 = out["sims"][sim]["candidates"][0], out["sims"][sim]["do_nothing"]
        cb = out["sims"][sim]["chart_best"]
        print(f"{sim:<15} nothing c{n0['chart']:5.2f} s{n0['scenes']:5.2f} | chart-best {cb['filter']} {cb['Saturation']:+d} "
              f"{cb['Contrast']:+d} c{cb['chart']:5.2f} s{cb['scenes']:5.2f} | combined-best {b['filter']} {b['Saturation']:+d} "
              f"{b['Contrast']:+d} c{b['chart']:5.2f} s{b['scenes']:5.2f}")
    Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
