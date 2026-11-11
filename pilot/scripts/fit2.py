"""Fit camera settings to each Fujifilm look from a BRACKETED dense sweep (poco_sweep.py --plan dense1).

What makes this version trustworthy:
- Drift cancels. Each setting was shot between two plain shots taken a minute or two either side,
  and its effect is learnt as a map from THAT pair's mean to the setting's shot. A slow change in
  the monitor or the room moves both sides of the pair together.
- Effects are learnt as smooth maps in CIELAB (thin-plate radial basis functions with a linear
  part), trained on pages dense_a + dense_b (270 colours) and scored on page dense_v (135 colours
  that appear on no training page). The held-out error is reported for every control.
- Targets never clip. The Fuji transforms are defined on sRGB, but the phone records Display P3,
  so its most saturated colours fall outside sRGB. A target is therefore the phone's colour plus
  the change the simulation makes relative to Provia at the nearest sRGB colour:
      target = Lab(x) + Lab(T_sim(clip x)) - Lab(T_PROVIA(clip x))
  which makes Provia exactly "do nothing" and leaves out-of-gamut colours honest. The part beyond
  sRGB is scaled by the look's own chroma change (fit.fuji_target), so Acros stays grey.
- The model only RANKS. Winners are shot for real (plan verify) and only real shots are scored.

    python fit2.py --sweep captures/poco/dense1 --out report/poco-fit2.json
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import colour
import numpy as np
from scipy.interpolate import RBFInterpolator

from fit import D65, de, fuji_target, load_cube, sims, stats

PAGES = ("dense_a", "dense_b", "dense_v")
TRAIN, TEST = ("dense_a", "dense_b"), ("dense_v",)


def lab_to_srgb(lab: np.ndarray) -> np.ndarray:
    return colour.XYZ_to_sRGB(colour.Lab_to_XYZ(lab, D65))


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    return colour.XYZ_to_Lab(colour.sRGB_to_XYZ(rgb), D65)


def load(sweep: Path) -> tuple[dict, list[dict]]:
    """{config: {page: Lab array}} plus the log rows, with base shots kept separately by index."""
    rows = [json.loads(l) for l in (sweep / "log.jsonl").read_text().splitlines()]
    shots: dict[str, dict[str, np.ndarray]] = {}
    for r in rows:
        data = json.loads((sweep / f"{r['tag']}.json").read_text())
        if "Lab" not in data:  # a scene page (no patches); scene_check.py scores those
            continue
        shots.setdefault(r["config"], {})[r["page"]] = np.array(data["Lab"])
    pages = {p for v in shots.values() for p in v}
    shots = {c: v for c, v in shots.items() if set(v) == pages}  # drop a setting the sweep did not finish
    return shots, rows


def shot_minutes(rows: list[dict]) -> dict[str, float]:
    """Minutes since the first shot, per config (its first shot), unwrapping past midnight."""
    out, day, last = {}, 0.0, None
    for r in rows:
        h, m, s = (int(x) for x in r["time"].split(":"))
        t = h * 60 + m + s / 60
        if last is not None and t + day < last - 60:  # the clock went back: a new day
            day += 24 * 60
        last = t + day
        out.setdefault(r["config"], last)
    return out


def brackets(shots: dict, rows: list[dict] | None = None, max_gap: float = 6.0) -> dict[str, dict[str, np.ndarray]]:
    """For every non-base config, the mean of the plain shots taken just before and just after it.

    With the log rows, a plain shot more than `max_gap` minutes away from the setting is not used:
    after a thermal rest the monitor has drifted (about 0.07 dE/min), so a plain shot from before
    the rest would leak that drift into the setting's measured effect (seen 18 Oct, a 47-minute rest).
    """
    order = [c for c in shots]  # insertion order = shooting order
    when = shot_minutes(rows) if rows else {}
    out = {}
    for i, c in enumerate(order):
        if c.startswith("base"):
            continue
        before = next((order[j] for j in range(i - 1, -1, -1) if order[j].startswith("base")), None)
        after = next((order[j] for j in range(i + 1, len(order)) if order[j].startswith("base")), None)
        near = [b for b in (before, after) if b and (not when or abs(when[b] - when[c]) <= max_gap)]
        pair = [shots[b] for b in near if all(p in shots[b] for p in shots[c])]
        if pair:
            out[c] = {p: np.mean([b[p] for b in pair], 0) for p in shots[c]}
    return out


class Effect:
    """A camera setting's effect on colour, as a smooth map in Lab (plain -> with the setting)."""

    def __init__(self, src: np.ndarray, dst: np.ndarray, smoothing: float = 1.0):
        self.rbf = RBFInterpolator(src, dst - src, kernel="thin_plate_spline", smoothing=smoothing, degree=1)

    def __call__(self, lab: np.ndarray) -> np.ndarray:
        return lab + self.rbf(lab)


def stack(d: dict[str, np.ndarray], pages) -> np.ndarray:
    return np.concatenate([d[p] for p in pages])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int, default=4)
    a = ap.parse_args()
    shots, rows = load(Path(a.sweep))
    br = brackets(shots, rows)
    bases = [c for c in shots if c.startswith("base")]

    # noise and drift floor: neighbouring plain shots
    steps = [float(de(stack(shots[x], PAGES), stack(shots[y], PAGES)).mean()) for x, y in zip(bases, bases[1:])]
    drift_total = float(de(stack(shots[bases[0]], PAGES), stack(shots[bases[-1]], PAGES)).mean())

    # learn each setting; report held-out error (and what "assume no effect" would score)
    effects, held_out = {}, {}
    for c, ref in br.items():
        eff = Effect(stack(ref, TRAIN), stack(shots[c], TRAIN))
        effects[c] = eff
        pred, real = eff(stack(ref, TEST)), stack(shots[c], TEST)
        held_out[c] = {"model": stats(de(pred, real)), "no_effect": stats(de(stack(ref, TEST), real))}

    # a common plain reference: the mean of every plain shot
    X = np.mean([stack(shots[b], PAGES) for b in bases], 0)

    def lever(prefix: str, v: int):
        return effects.get(f"{prefix}{v:+d}") if v else None

    filters = {"Original": None} | {c[2:]: e for c, e in effects.items() if c.startswith("f_")}
    candidates = {}
    for f, s, k in itertools.product(filters, range(-3, 4), range(-3, 4)):
        if (s and lever("sat", s) is None) or (k and lever("con", k) is None):
            continue  # a setting this sweep did not measure
        y = X
        for e in (filters[f], lever("sat", s), lever("con", k)):  # filter, then the sliders
            if e is not None:
                y = e(y)
        candidates[(f, s, k)] = y

    provia = load_cube(sims()["PROVIA"])
    results = {}
    for sim, cube in sims().items():
        target = fuji_target(X, load_cube(cube), provia)
        ranked = sorted(((float(de(y, target).mean()), key) for key, y in candidates.items()), key=lambda r: r[0])
        results[sim] = {
            "do_nothing": stats(de(X, target)),
            "candidates": [{"filter": f, "Saturation": s, "Contrast": k,
                            "predicted": stats(de(candidates[(f, s, k)], target))} for _, (f, s, k) in ranked[:a.top]],
        }
        best = results[sim]["candidates"][0]
        print(f"{sim:<15} do-nothing {results[sim]['do_nothing']['mean']:5.2f}  "
              f"best {best['predicted']['mean']:5.2f}  {best['filter']} sat {best['Saturation']:+d} con {best['Contrast']:+d}")

    report = {"plain_step_dE": stats(np.array(steps)), "plain_drift_first_to_last_dE": round(drift_total, 2),
              "held_out_effect_error": held_out, "sims": results}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(report, indent=1))
    print("plain neighbour step dE", report["plain_step_dE"], "| drift first->last", round(drift_total, 2))
    worst = sorted(held_out.items(), key=lambda kv: -kv[1]["model"]["mean"])[:5]
    print("held-out effect error (worst 5):", {c: v["model"]["mean"] for c, v in worst})


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
