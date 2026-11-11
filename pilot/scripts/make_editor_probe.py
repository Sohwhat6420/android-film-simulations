"""Probe files that show exactly how the Gallery editor applies a .cube to a photo, before any chart shots.

The laptop ceiling for the exact tier (0.18-0.39 dE) assumes the editor samples the LUT correctly.
On the phone's GPU, three things could break that, and these files pin each one down in one editor
session (import the three cubes, apply each to the probe image, save, pull):
- texel addressing: ProbeIdentity is the identity LUT. Correct sampling returns every probe value
  unchanged (to within the 8-bit storage). Sampling at rgb instead of rgb*(N-1)/N + 0.5/N stretches
  the output about mid-grey, by up to half a cell (about 2 codes).
- interpolation weights: ProbeTriangle sets red to 0.25 / 0.75 on alternating grid nodes (green and
  blue identity). Across the probe's red ramp, linear weights give a clean triangle wave, nearest
  sampling gives steps, and coarse fixed-point weights give a staircase on each slope.
- storage and export: ProbeOffset adds 8 codes to red only. The saved file shows whether the result
  is re-quantised, converted to sRGB or re-tagged (compare its ICC profile with the input's).
The probe image (untagged PNG; the plain P3 chart photo already covers the P3 case) holds four 256-step ramps (grey, red, green, blue; 4 px per code value) and a 16 x 16 x 16 lattice that
puts samples on grid nodes and between them.

    python make_editor_probe.py   # writes targets/editor/probe/
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_targets import cube_points, write_cube  # noqa: E402
from make_editor_cubes import for_photo_editor  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "targets/editor/probe"
N = 64


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ident = cube_points(N).reshape(N, N, N, 3)
    tri = ident.copy()
    tri[..., 0] = np.where(np.arange(N)[:, None, None] % 2 == 0, 0.25, 0.75)
    off = ident.copy()
    off[..., 0] = np.clip(off[..., 0] + 8 / 255, 0, 1)
    for name, T, note in (("ProbeIdentity", ident, "identity: shows texel addressing"),
                          ("ProbeTriangle", tri, "red alternates 0.25/0.75 per node: shows interpolation weights"),
                          ("ProbeOffset", off, "red +8 codes: shows re-quantisation and colour conversion on save")):
        write_cube(OUT / f"{name}.cube", for_photo_editor(T), name, note)

    w = 1024
    ramps = []
    for ch in ("grey", "r", "g", "b"):
        v = np.repeat(np.arange(256, dtype=np.uint8), 4)
        px = np.zeros((48, w, 3), np.uint8)
        if ch == "grey":
            px[:] = v[None, :, None]
        else:
            px[..., "rgb".index(ch)] = v[None, :]
        ramps.append(px)
    lv = np.round(np.linspace(0, 255, 16)).astype(np.uint8)
    lat = np.stack(np.meshgrid(lv, lv, lv, indexing="ij"), -1).reshape(64, 64, 3)  # 4096 colours
    lat = np.repeat(np.repeat(lat, 16, 0), 16, 1)
    img = np.concatenate(ramps + [np.zeros((16, w, 3), np.uint8), lat], 0)
    Image.fromarray(img).save(OUT / "probe.png")
    print(f"wrote {OUT}: 3 cubes + probe.png {img.shape[1]}x{img.shape[0]}")


if __name__ == "__main__":
    main()
