"""sRGB-in/sRGB-out versions of the project's Fuji targets, for Photon Camera (its photo LUTs are sRGB).

Same target as make_editor_cubes' _P3 set (fit.fuji_target), only the code values are sRGB.
    python make_srgb_cubes.py <project root> <out dir>
"""
import sys
from pathlib import Path

import colour
import numpy as np

root, out = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root / "scripts"))
from build_targets import cube_points, write_cube  # noqa: E402
from fit import D65, de, fuji_target, load_cube, sims  # noqa: E402

SRGB = colour.RGB_COLOURSPACES["sRGB"]
NAMES = {"ACROS": "Acros", "ASTIA": "Astia", "CLASSIC-CHROME": "Classic Chrome", "CLASSIC-Neg": "Classic Neg",
         "ETERNA-BB": "Eterna Bleach Bypass", "ETERNA": "Eterna", "PRO-NegHigh": "Pro Neg Hi",
         "PRO-NegStd": "Pro Neg Std", "PROVIA": "Provia", "REALA-ACE": "Reala Ace", "Velvia": "Velvia"}


def code_to_lab(c):
    return colour.XYZ_to_Lab(colour.RGB_to_XYZ(colour.cctf_decoding(c, function="sRGB"), SRGB), D65)


def lab_to_code(lab, iters=24):
    """As make_editor_cubes.lab_to_p3_code, with sRGB as the output gamut."""
    xyz = colour.Lab_to_XYZ(lab, D65)
    ok = colour.XYZ_to_Oklab(xyz)
    ok[..., 0] = np.clip(ok[..., 0], 0, 1)
    to_lin = lambda o: colour.XYZ_to_RGB(colour.Oklab_to_XYZ(o), SRGB)
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
    clip = np.clip(to_lin(colour.XYZ_to_Oklab(xyz)), 0, 1)
    cands = [clip] + [np.clip(to_lin(t * mapped + (1 - t) * colour.XYZ_to_Oklab(xyz)), 0, 1)
                      for t in (0.25, 0.5, 0.75, 1.0)]
    errs = np.stack([de(colour.XYZ_to_Lab(colour.RGB_to_XYZ(c, SRGB), D65), lab) for c in cands])
    best = np.take_along_axis(np.stack(cands), errs.argmin(0)[None, ..., None], 0)[0]
    return colour.cctf_encoding(np.where(inside[..., None], clip, best), function="sRGB")


n = 64
pts = cube_points(n)
lab = code_to_lab(pts)
provia = load_cube(sims()["PROVIA"])
out.mkdir(parents=True, exist_ok=True)
for sim, cube in sims().items():
    vals = lab_to_code(fuji_target(lab, load_cube(cube), provia)).reshape(n, n, n, 3)
    name = f"Fuji {NAMES[sim]}"
    write_cube(out / f"{name}.cube", vals, name, f"Fujifilm {sim}, sRGB in and out, {n}-point grid. Plain phone photo -> Fuji look.")
    if sim == "PROVIA":
        print("Provia identity max error:", float(np.abs(vals.reshape(-1, 3) - pts).max()))
    print(sim, "written")
