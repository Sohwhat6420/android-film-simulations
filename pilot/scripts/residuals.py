"""What is left after each look's best setting, and which extra lever would remove it?

For each look, the best predicted setting (fit2's model) leaves a residual against the Fuji target.
This splits it into lightness / chroma / hue, then asks how far the mean dE2000 would fall if one
more global lever existed, each fitted in closed form on the training pages and scored on the
held-out page:
    tone      a monotone lightness curve L* -> L* (what EV / a tone slider can do)
    chroma    one chroma scale (a finer saturation step)
    cast      one a*/b* offset (white balance / tint)
    all three together (roughly what the 14T's five -50..+50 sliders could reach)

    python residuals.py --sweep captures/poco/dense1 --out report/poco-residuals.json
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fit import de, fuji_target, load_cube, sims, stats  # noqa: E402
from fit2 import PAGES, Effect, brackets, load, stack  # noqa: E402

TRAIN, TEST = ("dense_a", "dense_b"), ("dense_v",)


class Monotone:
    """Least-squares non-decreasing curve (pool-adjacent-violators), linear between the fitted points."""

    def __init__(self, x: np.ndarray, y: np.ndarray):
        o = np.argsort(x)
        xs, ys = x[o], y[o].astype(float)
        vals, wts, xsum = [], [], []
        for xi, yi in zip(xs, ys):
            vals.append(yi); wts.append(1.0); xsum.append(xi)
            while len(vals) > 1 and vals[-2] > vals[-1]:
                w = wts[-2] + wts[-1]
                vals[-2] = (vals[-2] * wts[-2] + vals[-1] * wts[-1]) / w
                xsum[-2] = xsum[-2] + xsum[-1]; wts[-2] = w
                del vals[-1], wts[-1], xsum[-1]
        self.x = np.array(xsum) / np.array(wts)
        self.y = np.array(vals)

    def predict(self, x: np.ndarray) -> np.ndarray:
        return np.interp(x, self.x, self.y)


def split(lab_a: np.ndarray, lab_b: np.ndarray) -> dict:
    """Mean absolute lightness, chroma and hue differences (b relative to a)."""
    ca, cb = np.hypot(*lab_a[:, 1:].T), np.hypot(*lab_b[:, 1:].T)
    dh2 = np.clip(((lab_b[:, 1:] - lab_a[:, 1:]) ** 2).sum(1) - (cb - ca) ** 2, 0, None)
    return {"dL": round(float(np.abs(lab_b[:, 0] - lab_a[:, 0]).mean()), 2),
            "dC": round(float(np.abs(cb - ca).mean()), 2), "dH": round(float(np.sqrt(dh2).mean()), 2)}


def correct(pred: np.ndarray, tgt: np.ndarray, train: np.ndarray, kinds: set[str]):
    """Fit the chosen global corrections on the train rows; return a function applying them."""
    p, t = pred[train], tgt[train]
    tone = Monotone(p[:, 0], t[:, 0]) if "tone" in kinds else None
    k = 1.0
    if "chroma" in kinds:
        ab_p = p[:, 1:] - (t[:, 1:] - p[:, 1:]).mean(0) * ("cast" in kinds)
        k = float((ab_p * t[:, 1:]).sum() / max((ab_p ** 2).sum(), 1e-9))
    off = (t[:, 1:] - k * p[:, 1:]).mean(0) if "cast" in kinds else np.zeros(2)

    def apply(x: np.ndarray) -> np.ndarray:
        y = x.copy()
        if tone is not None:
            y[:, 0] = tone.predict(x[:, 0])
        y[:, 1:] = k * x[:, 1:] + off
        return y
    return apply


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    shots, rows = load(Path(a.sweep))
    br = brackets(shots, rows)
    bases = [c for c in shots if c.startswith("base")]
    effects = {c: Effect(stack(ref, TRAIN), stack(shots[c], TRAIN)) for c, ref in br.items()}
    X = np.mean([stack(shots[b], PAGES) for b in bases], 0)
    n = {p: len(shots[bases[0]][p]) for p in PAGES}
    train = np.r_[np.ones(n["dense_a"] + n["dense_b"], bool), np.zeros(n["dense_v"], bool)]

    def lever(prefix, v):
        return effects.get(f"{prefix}{v:+d}") if v else None

    filters = {"Original": None} | {c[2:]: e for c, e in effects.items() if c.startswith("f_")}
    cands = {}
    for f, s, k in itertools.product(filters, range(-3, 4), range(-3, 4)):
        if (s and lever("sat", s) is None) or (k and lever("con", k) is None):
            continue
        y = X
        for e in (filters[f], lever("sat", s), lever("con", k)):
            if e is not None:
                y = e(y)
        cands[(f, s, k)] = y
    provia = load_cube(sims()["PROVIA"])
    out = {}
    print(f"{'look':<15}{'best':>7}  {'dL':>5}{'dC':>6}{'dH':>6}   +tone  +chroma  +cast  +all3   (held-out page)")
    for sim, cube in sims().items():
        tgt = fuji_target(X, load_cube(cube), provia)
        key = min(cands, key=lambda c: float(de(cands[c][train], tgt[train]).mean()))
        pred = cands[key]
        held = lambda y: round(float(de(y[~train], tgt[~train]).mean()), 2)  # noqa: E731
        row = {"best": {"filter": key[0], "Saturation": key[1], "Contrast": key[2], "held_out": held(pred)},
               "residual": split(tgt[~train], pred[~train])}
        for name, kinds in (("tone", {"tone"}), ("chroma", {"chroma"}), ("cast", {"cast"}),
                            ("all3", {"tone", "chroma", "cast"})):
            row[f"plus_{name}"] = held(correct(pred, tgt, train, kinds)(pred))
        out[sim] = row
        r = row["residual"]
        print(f"{sim:<15}{row['best']['held_out']:>7}  {r['dL']:>5}{r['dC']:>6}{r['dH']:>6}   "
              f"{row['plus_tone']:>5}  {row['plus_chroma']:>7}  {row['plus_cast']:>5}  {row['plus_all3']:>5}")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
