"""Turn natural photos into monitor pages with the chart's four ArUco markers round them.

The phone shoots each page plain and with a look; measure.find_chart() then gives a homography
for each shot, so both can be rectified to the same pixel grid and compared pixel by pixel
(scene_check.py). Pages are placed and white-balanced exactly like the charts, so the same
camera settings apply.

    python scene_pages.py      # writes charts/scene_<name>.png/.json and the placed versions
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from charts import ARUCO, BACKGROUND, H, MARKER, W, layout, neutralise, place

ROOT = Path(__file__).resolve().parent.parent
SCENES = {
    "lake-blossom": Path("C:/Windows/Web/Wallpaper/Spotlight/img50.jpg"),
    "forest-water": Path("C:/Windows/Web/Wallpaper/ThemeC/img29.jpg"),
    "sunset": Path("C:/Windows/Web/Screen/img102.jpg"),
    "glossy": Path("C:/Windows/Web/Wallpaper/ThemeB/img27.jpg"),
    "pastel": Path("C:/Windows/Web/Wallpaper/ThemeD/img34.jpg"),
    "fuji-provia-scene": ROOT / "sources" / "bastibe" / "repo" / "styles_source" / "Provia.jpg",
}
IMAGE_RECT = (225, 70, 2110, 1300)  # x, y, w, h: the area between the markers
GAINS = (0.752, 0.899, 1.0)


def render(name: str, src: Path) -> Path:
    img = np.full((H, W, 3), BACKGROUND, np.uint8)
    x, y, w, h = IMAGE_RECT
    photo = cv2.imread(str(src))
    s = min(w / photo.shape[1], h / photo.shape[0])
    pw, ph = int(photo.shape[1] * s), int(photo.shape[0] * s)
    px, py = x + (w - pw) // 2, y + (h - ph) // 2
    img[py:py + ph, px:px + pw] = cv2.resize(photo, (pw, ph), interpolation=cv2.INTER_AREA)
    _, markers = layout()
    dictionary = cv2.aruco.getPredefinedDictionary(ARUCO)
    q = 20
    for mid, (mx, my) in markers.items():
        m = cv2.aruco.generateImageMarker(dictionary, mid, MARKER - 2 * q)
        img[my:my + MARKER, mx:mx + MARKER] = 255
        img[my + q:my + MARKER - q, mx + q:mx + MARKER - q] = m[..., None]
    out = ROOT / "charts" / f"scene_{name}.png"
    cv2.imwrite(str(out), img)
    meta = {"size": [W, H], "aruco": "DICT_4X4_50", "source": str(src),
            "markers": {str(k): [v[0] + q, v[1] + q, MARKER - 2 * q] for k, v in markers.items()},
            "image_rect": [px, py, pw, ph], "patches": []}
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    return neutralise(place(out, 0.56, 0.60, 0.50), GAINS)


if __name__ == "__main__":
    for n, p in SCENES.items():
        print(render(n, p))
