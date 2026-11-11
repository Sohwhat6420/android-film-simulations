"""Per-patch check of Phantom's chart output: which colour model, and how close to our Fuji target.
    python chart_check.py <chart.jpg> <phantom.jpg> <srgb cube> <p3 cube>
"""
import io
import sys

import colour
import numpy as np
from PIL import Image, ImageCms

chart_p, out_p, srgb_cube, p3_cube = sys.argv[1:5]
codes = np.load(chart_p + ".codes.npy")
im = Image.open(out_p)
icc = im.info.get("icc_profile")
print("phantom icc:", ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip() if icc else "none")
a = np.asarray(im.convert("RGB"), dtype=np.float64) / 255
cols, n = 27, len(codes)
size = a.shape[1] / cols
got = np.array([a[int((i // cols + .3) * size):int((i // cols + .7) * size),
                  int((i % cols + .3) * size):int((i % cols + .7) * size)].mean((0, 1)) for i in range(n)])

P3, SRGB = colour.RGB_COLOURSPACES["Display P3"], colour.RGB_COLOURSPACES["sRGB"]
tet = colour.algebra.table_interpolation_tetrahedral
dec = lambda x: colour.cctf_decoding(np.clip(x, 0, 1), function="sRGB")
enc = lambda x: colour.cctf_encoding(np.clip(x, 0, 1), function="sRGB")
conv = lambda x, s, d: enc(colour.RGB_to_RGB(dec(x), s, d))
lab = lambda x, s: colour.XYZ_to_Lab(colour.RGB_to_XYZ(dec(x), s))
de = lambda x, y: colour.delta_E(x, y, method="CIE 2000")

srgb, p3 = colour.read_LUT(srgb_cube), colour.read_LUT(p3_cube)
models = {
    "A: sRGB cube on raw codes": srgb.apply(codes, interpolator=tet),
    "B: P3->sRGB, sRGB cube, ->P3": conv(srgb.apply(conv(codes, P3, SRGB), interpolator=tet), SRGB, P3),
}
for k, pred in models.items():
    d = de(lab(pred, P3), lab(got, P3))
    print(f"{k}: mean dE00 {d.mean():.2f}  p95 {np.percentile(d, 95):.2f}  max {d.max():.2f}")

# Against the intended Fuji look: our P3 cube is the target for a P3 photo (input and output P3).
target = p3.apply(codes, interpolator=tet)
for k, pred in {"phantom now (sRGB cube)": got, "if P3 cube were loaded (model A)": target}.items():
    d = de(lab(pred, P3), lab(target, P3))
    print(f"vs Fuji target, {k}: mean dE00 {d.mean():.2f}  p95 {np.percentile(d, 95):.2f}  max {d.max():.2f}")
inside = np.all((colour.RGB_to_RGB(dec(codes), P3, SRGB) >= 0) & (colour.RGB_to_RGB(dec(codes), P3, SRGB) <= 1), -1)
d = de(lab(got, P3), lab(target, P3))
print(f"  of which patches inside sRGB: mean {d[inside].mean():.2f} (n={inside.sum()}), outside: mean {d[~inside].mean():.2f} (n={(~inside).sum()})")
