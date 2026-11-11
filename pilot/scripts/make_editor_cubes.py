"""Fuji .cube files for Xiaomi's Gallery editor (Media Editor's ".CUBE colour lookup table" import).

What the editor accepts (decompiled Media Editor 1.10.0.8.1, rq/b.java and vlog BaseFilterFragment):
- PHOTO editor: LUT_3D_SIZE 16 or 64 only (33 is refused). The first data line must match
  `d.dddddd d.dddddd d.dddddd`. Every entry is TRUNCATED to 8 bits, (int)(v * 255), and stored as
  a 512x512 PNG. At most 6 custom LUTs. The file name becomes the filter's name.
- VIDEO editor: the file is handed to the native renderer as is (any size, float values); names
  are cut to 20 characters.

So two folders are written under targets/editor/:
    photo/  size 64, each entry written as (round(v*255) + 0.5) / 255 so the truncation lands on
            the nearest 8-bit step instead of always rounding down
    video/  size 64, exact values

The phone saves Display P3 JPEGs. Whether the editor applies a LUT to the stored P3 code values
or converts to sRGB first is not documented, so each look comes in two variants and the phone decides:
    <Name>_P3    P3 code values in and out, aimed at fit.fuji_target (the scoring target used
                 everywhere in this project): Provia is "do nothing", colours outside sRGB keep
                 their extra saturation scaled by the look's own chroma change
    <Name>_sRGB  the plain sRGB transform T_sim (sRGB in, sRGB out), resampled to 64
Provia is written too, as a control. Only Provia_P3 is exact identity, so only it measures the
editor's own error; Provia_sRGB is T_PROVIA, which bends near-black reds and the gamut edge.

    python make_editor_cubes.py [--size 64]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import colour
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_targets import cube_points, write_cube  # noqa: E402
from fit import D65, de, fuji_target, load_cube, sims  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
P3 = colour.RGB_COLOURSPACES["Display P3"]
NAMES = {"ACROS": "Acros", "ASTIA": "Astia", "CLASSIC-CHROME": "ClassicChrome", "CLASSIC-Neg": "ClassicNeg",
         "ETERNA-BB": "EternaBleach", "ETERNA": "Eterna", "PRO-NegHigh": "ProNegHi", "PRO-NegStd": "ProNegStd",
         "PROVIA": "Provia", "REALA-ACE": "RealaAce", "Velvia": "Velvia"}


def p3_code_to_lab(c: np.ndarray) -> np.ndarray:
    xyz = colour.RGB_to_XYZ(colour.cctf_decoding(c, function="sRGB"), P3)
    return colour.XYZ_to_Lab(xyz, D65)


def lab_to_p3_code(lab: np.ndarray, iters: int = 24) -> np.ndarray:
    """Lab -> P3 code values, mapping targets a P3 JPEG cannot hold into gamut first.

    Clipping each channel on its own bends hue and drags neighbouring grid nodes along (seen
    19 Oct: a gamut-edge blue whose Classic Neg / Pro Neg Hi target has chroma 157-170 scored
    15 dE). Instead the chroma is lowered at constant lightness and hue, in Oklab (whose hue
    lines stay straight for blues, unlike CIELAB), to the largest value that fits.
    """
    xyz = colour.Lab_to_XYZ(lab, D65)
    ok = colour.XYZ_to_Oklab(xyz)
    ok[..., 0] = np.clip(ok[..., 0], 0, 1)
    to_lin = lambda o: colour.XYZ_to_RGB(colour.Oklab_to_XYZ(o), P3)
    fits = lambda lin: (lin.min(-1) >= -1e-4) & (lin.max(-1) <= 1 + 1e-4)
    inside = fits(to_lin(ok))
    lo, hi = np.zeros(ok.shape[:-1]), np.ones(ok.shape[:-1])
    for _ in range(iters):
        mid = (lo + hi) / 2
        trial = ok.copy()
        trial[..., 1:] *= mid[..., None]
        good = fits(to_lin(trial))
        lo, hi = np.where(good, mid, lo), np.where(good, hi, mid)
    scale = np.where(inside, 1.0, lo)
    mapped = ok.copy()
    mapped[..., 1:] *= scale[..., None]
    # Constant-lightness chroma reduction is not always the closest colour in dE2000 (Velvia's
    # slightly out-of-gamut colours got worse with it), so each out-of-gamut node takes whichever
    # of plain clipping, the chroma map, or blends of the two lands nearest its target.
    clip = np.clip(to_lin(colour.XYZ_to_Oklab(xyz)), 0, 1)
    cands = [clip] + [np.clip(to_lin(t * mapped + (1 - t) * colour.XYZ_to_Oklab(xyz)), 0, 1)
                      for t in (0.25, 0.5, 0.75, 1.0)]
    errs = np.stack([de(colour.XYZ_to_Lab(colour.RGB_to_XYZ(c, P3), D65), lab) for c in cands])
    best = np.take_along_axis(np.stack(cands), errs.argmin(0)[None, ..., None], 0)[0]
    return colour.cctf_encoding(np.where(inside[..., None], clip, best), function="sRGB")


def for_photo_editor(T: np.ndarray) -> np.ndarray:
    """Pre-compensate the editor's (int)(v * 255) truncation."""
    return (np.round(np.clip(T, 0, 1) * 255) + 0.5) / 255


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=64, choices=(16, 64), help="the photo editor takes only 16 or 64")
    a = ap.parse_args()
    n = a.size
    pts = cube_points(n)  # [r, g, b], b fastest
    lab = p3_code_to_lab(pts)
    provia = load_cube(sims()["PROVIA"])
    for sim, cube in sims().items():
        T = load_cube(cube)
        variants = {"P3": lab_to_p3_code(fuji_target(lab, T, provia)), "sRGB": T(pts)}
        for var, vals in variants.items():
            vals = vals.reshape(n, n, n, 3)
            name = f"{NAMES[sim]}_{var}"
            note = f"Fujifilm {sim}, {var} in and out, {n}-point grid. Plain Xiaomi photo -> Fuji look."
            for folder, data in (("photo", for_photo_editor(vals)), ("video", vals)):
                out = ROOT / "targets" / "editor" / folder
                out.mkdir(parents=True, exist_ok=True)
                write_cube(out / f"{name}.cube", data, name, note)
        print(sim, "written")


if __name__ == "__main__":
    main()
