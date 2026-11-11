"""Score a photo edited with a Fuji .cube (Gallery editor, Snapseed, ffmpeg...) against the Fuji target.

The plain photo and its edited copy are the SAME exposure, so there is no drift or shot noise:
what is left is exactly the editor's error (how it applies the LUT, 8-bit steps, JPEG re-encoding).

    python score_edit.py --plain p.jpg --edited e.jpg --sim Velvia --chart dense_v
    python score_edit.py --plain p.jpg --simulate targets/editor/Fuji_Velvia_P3.cube --sim Velvia --chart dense_v
        (--simulate: apply the cube on the laptop to the stored code values, as an ideal editor
         would, write <plain>_sim_<cube>.jpg and score it: the ceiling for this route)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.interpolate import RegularGridInterpolator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_targets import read_cube  # noqa: E402
from fit import de, fuji_target, load_cube, sims, stats  # noqa: E402
from measure import read_patches  # noqa: E402


def editor_lut(cube: Path):
    """The cube as Xiaomi's photo editor holds it: every entry truncated to 8 bits, (int)(v * 255)."""
    v = np.floor(read_cube(cube) * 255) / 255  # [r, g, b, 3]
    axis = np.linspace(0, 1, v.shape[0])
    f = RegularGridInterpolator((axis, axis, axis), v, method="linear")
    return lambda rgb: f(np.clip(rgb, 0, 1).reshape(-1, 3)).reshape(np.shape(rgb))


def simulate(plain: Path, cube: Path, out: Path, editor8: bool = False) -> Path:
    im = Image.open(plain)
    rgb = np.asarray(im.convert("RGB")).astype(np.float64) / 255
    T = editor_lut(cube) if editor8 else load_cube(cube)
    res = np.empty_like(rgb)
    for y in range(0, rgb.shape[0], 256):  # in strips, to keep memory low on a 50 MP frame
        res[y:y + 256] = T(rgb[y:y + 256])
    Image.fromarray(np.uint8(np.clip(np.round(res * 255), 0, 255))).save(
        out, quality=95, icc_profile=im.info.get("icc_profile"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plain", required=True)
    ap.add_argument("--edited")
    ap.add_argument("--simulate")
    ap.add_argument("--editor8", action="store_true", help="simulate the photo editor's 8-bit LUT entries")
    ap.add_argument("--sim", required=True)
    ap.add_argument("--chart", default="dense_v")
    a = ap.parse_args()
    plain = Path(a.plain)
    edited = Path(a.edited) if a.edited else simulate(
        plain, Path(a.simulate), plain.with_name(f"{plain.stem}_sim_{Path(a.simulate).stem}.jpg"),
        # photo/ cubes are pre-compensated for the editor's truncation, so read them as it does
        a.editor8 or Path(a.simulate).parent.name == "photo")
    p = np.array(read_patches(plain, chart=a.chart)["Lab"])
    e = np.array(read_patches(edited, chart=a.chart)["Lab"])
    cubes = sims()
    tgt = fuji_target(p, load_cube(cubes[a.sim]), load_cube(cubes["PROVIA"]))
    r = {"sim": a.sim, "edited": str(edited), "edited_vs_target": stats(de(e, tgt)),
         "plain_vs_target": stats(de(p, tgt))}
    print(json.dumps(r))


if __name__ == "__main__":
    main()
