"""A saturated patch chart, P3-tagged like the stock camera's JPEGs, to push through Phantom.
    python make_chart.py <icc source jpg> <out jpg>
"""
import sys

import numpy as np
from PIL import Image

icc = Image.open(sys.argv[1]).info["icc_profile"]
levels = np.linspace(0, 1, 9)
r, g, b = np.meshgrid(levels, levels, levels, indexing="ij")
codes = np.stack([r, g, b], -1).reshape(-1, 3)  # 729 patches
cols, size = 27, 160
rows = len(codes) // cols
img = np.zeros((rows * size, cols * size, 3), np.uint8)
for i, c in enumerate(codes):
    y, x = divmod(i, cols)
    img[y * size:(y + 1) * size, x * size:(x + 1) * size] = np.round(c * 255)
# Phantom skips files under 1 MB and photos whose DATE_TAKEN is over 5 minutes old (PhantomService.kt:253-256):
# a little noise for size, EXIF DateTimeOriginal for the date.
rng = np.random.default_rng(0)
img = np.clip(img.astype(int) + rng.integers(-2, 3, img.shape), 0, 255).astype(np.uint8)
from datetime import datetime
exif = Image.Exif()
stamp = datetime.now().strftime("%Y:%m:%d %H:%M:%S")
exif[0x0132] = stamp
exif.get_ifd(0x8769)[0x9003] = stamp
Image.fromarray(img).save(sys.argv[2], quality=100, subsampling=0, icc_profile=icc, exif=exif)
np.save(sys.argv[2] + ".codes.npy", codes)
print(img.shape, len(codes), "patches")
