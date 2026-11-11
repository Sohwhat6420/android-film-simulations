"""Test chart shown full-screen on the laptop monitor and shot by the phone.

60 patches (10 x 6) on a mid-grey field, with four ArUco markers so measure.py can find and
un-warp the chart in any phone photo:
  - the 24 ColorChecker colours (sRGB, from colour-science's post-2014 reference data);
  - a 12-step neutral ramp, even in CIE L*;
  - a 24-colour hue ring: 12 hues at two saturations.

Absolute monitor accuracy does not matter: every target is computed from the phone's OWN
shot of this chart, so the monitor only has to stay the same between shots.

    python charts.py            # writes charts/chart60.png and charts/chart60.json
"""
from __future__ import annotations

import colorsys
import json
from pathlib import Path

import colour
import cv2
import numpy as np

W, H = 2560, 1440
COLS, ROWS = 10, 6
PATCH, GAP = 180, 30
MARKER = 170
BACKGROUND = 118  # sRGB code value of about 18% grey, so the camera meters sensibly
ARUCO = cv2.aruco.DICT_4X4_50


def patch_colours() -> list[tuple[str, tuple[int, int, int]]]:
    cc = colour.characterisation.CCS_COLOURCHECKERS["ColorChecker24 - After November 2014"]
    out = []
    for name, xyY in cc.data.items():
        XYZ = colour.xyY_to_XYZ(xyY)
        rgb = colour.XYZ_to_sRGB(XYZ, illuminant=cc.illuminant, chromatic_adaptation_transform="Bradford")
        out.append((name, tuple(int(round(v)) for v in np.clip(rgb, 0, 1) * 255)))
    for i in range(12):  # neutral ramp, L* 5..97
        L = 5 + i * (92 / 11)
        Y = colour.Lab_to_XYZ([L, 0, 0])[1]
        v = colour.cctf_encoding(Y)
        out.append((f"grey L{L:.0f}", (int(round(v * 255)),) * 3))
    for s, v in ((1.0, 0.9), (0.5, 0.75)):
        for k in range(12):
            r, g, b = colorsys.hsv_to_rgb(k / 12, s, v)
            out.append((f"hue{k * 30:03d} s{s:.1f}", tuple(int(round(c * 255)) for c in (r, g, b))))
    assert len(out) == COLS * ROWS
    return out


def layout():
    gx = (W - (COLS * PATCH + (COLS - 1) * GAP)) // 2
    gy = (H - (ROWS * PATCH + (ROWS - 1) * GAP)) // 2
    rects = []
    for r in range(ROWS):
        for c in range(COLS):
            x, y = gx + c * (PATCH + GAP), gy + r * (PATCH + GAP)
            rects.append((x, y, PATCH, PATCH))
    pad = 25
    markers = {  # id -> top-left corner, in the left/right margins
        0: (pad, pad),
        1: (W - pad - MARKER, pad),
        2: (W - pad - MARKER, H - pad - MARKER),
        3: (pad, H - pad - MARKER),
    }
    return rects, markers


def render(out_dir: Path) -> Path:
    colours = patch_colours()
    rects, markers = layout()
    img = np.full((H, W, 3), BACKGROUND, np.uint8)
    for (x, y, w, h), (_, rgb) in zip(rects, colours):
        img[y:y + h, x:x + w] = rgb
    dictionary = cv2.aruco.getPredefinedDictionary(ARUCO)
    q = 20  # white quiet zone round each marker
    for mid, (x, y) in markers.items():
        m = cv2.aruco.generateImageMarker(dictionary, mid, MARKER - 2 * q)
        img[y:y + MARKER, x:x + MARKER] = 255
        img[y + q:y + MARKER - q, x + q:x + MARKER - q] = m[..., None]
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / "chart60.png"
    cv2.imwrite(str(png), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    meta = {
        "size": [W, H],
        "aruco": "DICT_4X4_50",
        "markers": {str(k): [v[0] + q, v[1] + q, MARKER - 2 * q] for k, v in markers.items()},
        "patches": [{"name": n, "srgb": list(rgb), "rect": list(r)} for (n, rgb), r in zip(colours, rects)],
    }
    (out_dir / "chart60.json").write_text(json.dumps(meta, indent=1))
    return png


def skin_tones() -> list[tuple[str, tuple[int, int, int]]]:
    """Twelve skin tones from light to deep (sRGB), the colours a portrait look is judged on."""
    hexes = ["f6d5c3", "eec1a6", "e3b18f", "d9a07c", "c98d6a", "b97a56",
             "a5694a", "8f573d", "7a4a33", "653c2a", "512f21", "3d2418"]
    return [(f"skin{i:02d}", tuple(int(h[k:k + 2], 16) for k in (0, 2, 4))) for i, h in enumerate(hexes)]


def dense_pages() -> dict[str, list[tuple[str, tuple[int, int, int]]]]:
    """Three 135-patch pages: two to learn the camera's controls from, one held out to test on.

    Training pages (270 patches): a 6x6x6 cube of sRGB values (even coverage of the whole gamut),
    an 18-step grey ramp, the 24 ColorChecker colours and twelve skin tones, shuffled so that no
    colour family sits in one corner of the screen. The validation page holds colours found on
    no training page: 113 quasi-random (Sobol) sRGB samples, ten greys between the training
    steps and twelve shifted skin tones, so a fit can be judged on colours it never saw.
    """
    from scipy.stats import qmc

    levels = [0, 51, 102, 153, 204, 255]
    cube = [(f"c{r}{g}{b}", (levels[r], levels[g], levels[b])) for r in range(6) for g in range(6) for b in range(6)]
    ramp = []
    for i in range(18):
        Y = colour.Lab_to_XYZ([3 + i * (94 / 17), 0, 0])[1]
        ramp.append((f"ramp{i:02d}", (int(round(colour.cctf_encoding(Y) * 255)),) * 3))
    train = cube + ramp + patch_colours()[:24] + skin_tones()
    assert len(train) == 270
    order = np.random.default_rng(11).permutation(len(train))
    train = [train[i] for i in order]
    sob = [(f"v{i:03d}", tuple(int(v) for v in np.round(s * 255)))
           for i, s in enumerate(qmc.Sobol(3, seed=1234).random(128)[:113])]
    greys = []
    for i in range(10):
        Y = colour.Lab_to_XYZ([8 + i * 9.3, 0, 0])[1]
        greys.append((f"vgrey{i}", (int(round(colour.cctf_encoding(Y) * 255)),) * 3))
    skins = [(f"vskin{i:02d}", tuple(int(np.clip(c * f, 0, 255)) for c in rgb))
             for i, ((_, rgb), f) in enumerate(zip(skin_tones(), [1.05, 0.95] * 6))]
    val = sob + greys + skins
    val = [val[i] for i in np.random.default_rng(12).permutation(len(val))]
    return {"dense_a": train[:135], "dense_b": train[135:], "dense_v": val}


def render_page(out_dir: Path, name: str, colours, cols: int = 15, rows: int = 9,
                patch: int = 122, gap: int = 22) -> Path:
    """Draw one dense page with the same marker layout as chart60 (so measure.py handles it)."""
    assert len(colours) == cols * rows, (name, len(colours))
    gx = (W - (cols * patch + (cols - 1) * gap)) // 2
    gy = (H - (rows * patch + (rows - 1) * gap)) // 2
    rects = [(gx + c * (patch + gap), gy + r * (patch + gap), patch, patch) for r in range(rows) for c in range(cols)]
    _, markers = layout()
    img = np.full((H, W, 3), BACKGROUND, np.uint8)
    for (x, y, w, h), (_, rgb) in zip(rects, colours):
        img[y:y + h, x:x + w] = rgb
    dictionary = cv2.aruco.getPredefinedDictionary(ARUCO)
    q = 20
    for mid, (x, y) in markers.items():
        m = cv2.aruco.generateImageMarker(dictionary, mid, MARKER - 2 * q)
        img[y:y + MARKER, x:x + MARKER] = 255
        img[y + q:y + MARKER - q, x + q:x + MARKER - q] = m[..., None]
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"{name}.png"
    cv2.imwrite(str(png), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    meta = {"size": [W, H], "aruco": "DICT_4X4_50",
            "markers": {str(k): [v[0] + q, v[1] + q, MARKER - 2 * q] for k, v in markers.items()},
            "patches": [{"name": n, "srgb": list(rgb), "rect": list(r)} for (n, rgb), r in zip(colours, rects)]}
    (out_dir / f"{name}.json").write_text(json.dumps(meta, indent=1))
    return png


def neutralise(png: Path, gains: tuple[float, float, float]) -> Path:
    """Scale R, G, B in linear light so the phone sees the greys as grey.

    The monitor's white plus the camera's fixed white balance gave a strong magenta cast, and the
    POCO has no tint control. Gains are found by shooting, measuring the grey ramp and repeating.
    """
    img = cv2.imread(str(png))[..., ::-1].astype(np.float64) / 255
    lin = colour.cctf_decoding(img, function="sRGB") * np.array(gains)
    out = np.clip(colour.cctf_encoding(np.clip(lin, 0, 1), function="sRGB") * 255 + 0.5, 0, 255).astype(np.uint8)
    path = png.with_name(f"{png.stem}_g{gains[0]:.3f}-{gains[1]:.3f}-{gains[2]:.3f}.png")
    cv2.imwrite(str(path), out[..., ::-1])
    return path


def place(chart: Path, scale: float, cx: float, cy: float) -> Path:
    """Shrink the chart and move it to where the phone on its stand can see all of it.

    The rest of the screen stays at the background grey. measure.py finds the chart by its
    markers, so placement never changes the patch maths.
    """
    img = cv2.imread(str(chart))
    small = cv2.resize(img, (round(W * scale), round(H * scale)), interpolation=cv2.INTER_AREA)
    canvas = np.full((H, W, 3), BACKGROUND, np.uint8)
    x0, y0 = round(cx * W - small.shape[1] / 2), round(cy * H - small.shape[0] / 2)
    if x0 < 0 or y0 < 0 or x0 + small.shape[1] > W or y0 + small.shape[0] > H:
        raise ValueError("placed chart runs off the screen")
    canvas[y0:y0 + small.shape[0], x0:x0 + small.shape[1]] = small
    out = chart.with_name(f"{chart.stem}_s{scale:.2f}_x{cx:.2f}_y{cy:.2f}.png")
    cv2.imwrite(str(out), canvas)
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float)
    ap.add_argument("--cx", type=float, default=0.5)
    ap.add_argument("--cy", type=float, default=0.5)
    ap.add_argument("--gain", type=float, nargs=3, metavar=("R", "G", "B"))
    ap.add_argument("--page", help="dense_a, dense_b or dense_v instead of chart60")
    a = ap.parse_args()
    charts_dir = Path(__file__).resolve().parent.parent / "charts"
    png = render_page(charts_dir, a.page, dense_pages()[a.page]) if a.page else render(charts_dir)
    if a.scale:
        png = place(png, a.scale, a.cx, a.cy)
    if a.gain:
        png = neutralise(png, tuple(a.gain))
    print(png)
