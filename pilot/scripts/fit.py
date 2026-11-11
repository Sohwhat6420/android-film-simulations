"""Find the camera settings that come closest to each Fujifilm look, by measurement.

Target: the phone's own neutral shot of the chart (baseline), re-graded by the Fuji transform
T_sim from build_targets.py (targets/T_<sim>.cube, sRGB in -> sRGB out). So the target is "what
this very frame would look like as Classic Chrome", not a colour from the internet.

Candidates (POCO X6 Pro): every Pro filter x Saturation -3..+3 x Contrast -3..+3. Each filter
was shot directly; Saturation and Contrast were each swept alone on the baseline, and their
effect is learnt as a smooth colour mapping (2nd-order polynomial in sRGB, fitted on the 60
patches) that is then applied on top of a filter's measured output. The model only RANKS
candidates: the winners are shot for real and only those real shots are scored in the report.

    python fit.py --sweep captures/poco/sweep1 --out report/poco-fit.json
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import colour
import numpy as np
from scipy.interpolate import RegularGridInterpolator

ROOT = Path(__file__).resolve().parent.parent
D65 = colour.CCS_ILLUMINANTS["CIE 1931 2 Degree Standard Observer"]["D65"]


def load_cube(path: Path):
    size, rows = None, []
    for line in path.read_text().splitlines():
        t = line.strip().split()
        if not t or t[0].startswith("#"):
            continue
        if t[0] == "LUT_3D_SIZE":
            size = int(t[1])
        elif t[0][0] in "-.0123456789" and len(t) == 3:
            rows.append([float(v) for v in t])
    lut = np.array(rows).reshape(size, size, size, 3)  # [b][g][r], red varies fastest
    axis = np.linspace(0, 1, size)
    f = RegularGridInterpolator((axis, axis, axis), lut, method="linear")
    return lambda rgb: f(np.clip(rgb, 0, 1)[..., ::-1].reshape(-1, 3)).reshape(np.shape(rgb))


def srgb_to_lab(srgb: np.ndarray) -> np.ndarray:
    return colour.XYZ_to_Lab(colour.sRGB_to_XYZ(srgb), D65)


def fuji_target(lab: np.ndarray, T, T_provia) -> np.ndarray:
    """The Fuji look of a phone colour (CIELAB in, CIELAB out), with Provia as "do nothing".

    The Fuji transforms are defined on sRGB but the phone records Display P3, so a colour is
    looked up at its nearest sRGB colour c and the look's change is added:
        target = Lab(x) + Lab(T(c)) - Lab(T_PROVIA(c)) = Lab(T(c)) + (Lab(x) - Lab(T_PROVIA(c)))
    The bracket is what Provia cannot express: colour beyond sRGB, and colours at the gamut edge
    that Provia never produces. Its a*/b* part is scaled by how much the look itself changes
    chroma at c. Without that, a black-and-white look kept up to 70 units of chroma on the
    phone's most saturated colours (found 18 Oct). Where Provia is "do nothing" (x = T_PROVIA(c))
    nothing changes.

    Near grey the scale is a ratio of two tiny chromas, so for colour looks it fades to 1 below
    C(T_PROVIA(c)) of 2-6 (otherwise a look that tints greys doubles Provia's own near-black
    error). A monochrome look keeps the full scale everywhere, so its target stays grey.

    c is first pulled towards the grey of its own lightness, to the edge of what Provia actually
    produces (where T_PROVIA is "do nothing"). About 3.5% of the sRGB cube, mostly on its faces,
    lies outside Provia's output, and there the inverted LUT picks an arbitrary scene: at
    sRGB (0.32, 0, 0.67) it lifted L* by 14, while at G = 0.05 it was exact, so every colour
    beyond sRGB got a target that jumped (found 19 Oct). The leftover then gets the look's local
    hue rotation as well as its chroma scale, faded the same way near grey.
    """
    rgb = into_provia_gamut(np.clip(colour.XYZ_to_sRGB(colour.Lab_to_XYZ(lab, D65)), 0, 1), T_provia)
    t_sim, t_pro = srgb_to_lab(T(rgb)), srgb_to_lab(T_provia(rgb))
    chroma = lambda v: np.hypot(v[..., 1], v[..., 2])
    c_sim, c_pro = chroma(t_sim), chroma(t_pro)
    k = np.clip(c_sim / np.maximum(c_pro, 1e-6), 0, 2)
    turn = np.arctan2(t_sim[..., 2], t_sim[..., 1]) - np.arctan2(t_pro[..., 2], t_pro[..., 1])
    turn = (turn + np.pi) % (2 * np.pi) - np.pi
    probe = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 0], [0, 1, 1], [1, 0, 1]], float)
    if chroma(srgb_to_lab(T(probe))).max() >= 2:                  # a colour look
        w = np.clip((c_pro - 2) / 4, 0, 1)
        w = w * w * (3 - 2 * w)
        k, turn = 1 + w * (k - 1), w * turn
    else:
        turn = np.zeros_like(turn)
    r = (lab - t_pro)[..., 1:]
    cos, sin = np.cos(turn), np.sin(turn)
    r = np.stack([cos * r[..., 0] - sin * r[..., 1], sin * r[..., 0] + cos * r[..., 1]], -1)
    out = lab + t_sim - t_pro
    out[..., 1:] = t_sim[..., 1:] + k[..., None] * r
    return out


def into_provia_gamut(rgb: np.ndarray, T_provia, tol: float = 0.5, iters: int = 12) -> np.ndarray:
    """Move sRGB colours that Provia never produces towards the grey of the same lightness, to
    the nearest point where T_PROVIA is within `tol` dE2000 of "do nothing". Others are unchanged."""
    grey = np.repeat(colour.cctf_encoding(colour.sRGB_to_XYZ(rgb)[..., 1:2].clip(0, 1), function="sRGB"),
                     3, -1)
    off = lambda c: de(srgb_to_lab(c), srgb_to_lab(T_provia(c))) > tol
    out = off(rgb)
    if not out.any():
        return rgb
    lo, hi = np.zeros(out.sum()), np.ones(out.sum())
    c, g = rgb[out], grey[out]
    for _ in range(iters):
        mid = (lo + hi) / 2
        bad = off(g + mid[:, None] * (c - g))
        lo, hi = np.where(bad, lo, mid), np.where(bad, mid, hi)
    res = rgb.copy()
    res[out] = g + lo[:, None] * (c - g)
    return res


def de(a_lab: np.ndarray, b_lab: np.ndarray) -> np.ndarray:
    return colour.delta_E(a_lab, b_lab, method="CIE 2000")


def stats(d: np.ndarray) -> dict:
    return {"mean": round(float(d.mean()), 2), "p90": round(float(np.percentile(d, 90)), 2), "max": round(float(d.max()), 2)}


def features(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb.T
    return np.stack([r, g, b, r * r, g * g, b * b, r * g, r * b, g * b, np.ones_like(r)], 1)


def fit_op(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    return np.linalg.lstsq(features(src), dst, rcond=None)[0]


def apply_op(W: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    return features(rgb) @ W


def load_sweep(d: Path) -> dict[str, np.ndarray]:
    out = {}
    for line in (d / "log.jsonl").read_text().splitlines():
        tag = json.loads(line)["tag"]
        out[tag] = np.array(json.loads((d / f"{tag}.json").read_text())["srgb"])
    return out


def sims() -> dict[str, Path]:
    """Fuji targets by name, using the 65-point grids (the 33-point ones are for the Gallery)."""
    return {p.stem[2:-3]: p for p in sorted((ROOT / "targets").glob("T_*_65.cube"))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int, default=3)
    a = ap.parse_args()
    m = load_sweep(Path(a.sweep))
    bases = [m[t] for t in m if t.startswith("base")]
    base = np.mean(bases, 0)
    repeat = max(float(de(srgb_to_lab(x), srgb_to_lab(base)).mean()) for x in bases)
    ops = {("Saturation", 0): None, ("Contrast", 0): None}
    for tag, v in m.items():
        for ctl, pre in (("Saturation", "sat"), ("Contrast", "con")):
            if tag.startswith(pre):
                ops[(ctl, int(tag[3:]))] = fit_op(base, v)
    filters = {"Original": base} | {t[2:]: v for t, v in m.items() if t.startswith("f_")}

    # how well does "filter, then the learnt slider effect" predict a real shot? (checked again on phone)
    model_err = {f"{ctl} {v:+d}": round(float(de(srgb_to_lab(apply_op(W, base)), srgb_to_lab(m[f'{ctl[:3].lower()}{v:+d}'])).mean()), 2)
                 for (ctl, v), W in ops.items() if W is not None}

    def predict(f: str, s: int, c: int) -> np.ndarray:
        x = filters[f]
        if ops[("Saturation", s)] is not None:
            x = apply_op(ops[("Saturation", s)], x)
        if ops[("Contrast", c)] is not None:
            x = apply_op(ops[("Contrast", c)], x)
        return x

    results = {}
    for sim, cube in sims().items():
        T = load_cube(cube)
        target_lab = srgb_to_lab(T(np.clip(base, 0, 1)))
        ranked = []
        for f, s, c in itertools.product(filters, range(-3, 4), range(-3, 4)):
            d = de(srgb_to_lab(predict(f, s, c)), target_lab)
            ranked.append((float(d.mean()), f, s, c, stats(d)))
        ranked.sort(key=lambda r: r[0])
        results[sim] = {
            "do_nothing": stats(de(srgb_to_lab(base), target_lab)),
            "candidates": [{"filter": f, "Saturation": s, "Contrast": c, "predicted": st} for _, f, s, c, st in ranked[:a.top]],
        }
        best = ranked[0]
        print(f"{sim:<16} do-nothing {results[sim]['do_nothing']['mean']:5.2f}  best {best[0]:5.2f}  {best[1]} sat {best[2]:+d} con {best[3]:+d}")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"baseline_repeatability_dE": round(repeat, 2), "slider_model_dE": model_err,
                                       "sims": results}, indent=1))
    print("baseline repeatability dE", round(repeat, 2), "| slider model dE", model_err)


if __name__ == "__main__":
    main()
