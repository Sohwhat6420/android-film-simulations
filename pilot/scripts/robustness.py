"""Two checks that the chart results carry over to real photos.

surround: the same 60 colours on mid-grey, dark and light surrounds (plan surround1). If the phone's
local tone mapping changes a colour depending on its neighbours, plain shots differ between
surrounds by more than the repeat noise, and a look learnt on one surround mispredicts another.

exposure: the held-out page at full and half brightness (plan exposure1). The control effects learnt
from the dense sweep at full brightness (fit2.Effect) are asked to predict the half-brightness
shots. If they predict them about as well as full-brightness ones, the presets hold when auto
exposure meets a darker scene.

    python robustness.py --surround captures/poco/surround1 --exposure captures/poco/exposure1 --dense captures/poco/dense1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from fit import de, stats
from fit2 import TRAIN, Effect, brackets, load, stack


def surround(sweep: Path) -> dict:
    shots, _ = load(sweep)
    pages = ("chart60", "surround_dark", "surround_light")
    out = {"repeat_noise": {}, "plain_vs_grey": {}, "look_prediction": {}}
    for pg in pages:
        out["repeat_noise"][pg] = stats(de(shots["plain1"][pg], shots["plain2"][pg]))
    plain = {pg: (shots["plain1"][pg] + shots["plain2"][pg]) / 2 for pg in pages}
    vivid = {pg: (shots["vivid1"][pg] + shots["vivid2"][pg]) / 2 for pg in pages}
    eff = Effect(plain["chart60"], vivid["chart60"])  # the look as learnt on the mid-grey surround
    for pg in pages[1:]:
        out["plain_vs_grey"][pg] = stats(de(plain[pg], plain["chart60"]))
        out["look_prediction"][pg] = stats(de(eff(plain[pg]), vivid[pg]))
    return out


def exposure(sweep: Path, dense: Path) -> dict:
    d_shots, d_rows = load(dense)
    d_br = brackets(d_shots, d_rows)
    shots, rows = load(sweep)
    br = brackets(shots, rows)
    out = {}
    for c in br:
        if c not in d_br:
            continue
        eff = Effect(stack(d_br[c], TRAIN), stack(d_shots[c], TRAIN))
        out[c] = {pg: stats(de(eff(br[c][pg]), shots[c][pg])) for pg in ("dense_v", "dense_vdim")}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--surround")
    ap.add_argument("--exposure")
    ap.add_argument("--dense")
    ap.add_argument("--out", default="report/poco-robustness.json")
    a = ap.parse_args()
    res = {}
    if a.surround:
        res["surround"] = surround(Path(a.surround))
    if a.exposure:
        res["exposure"] = exposure(Path(a.exposure), Path(a.dense))
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
