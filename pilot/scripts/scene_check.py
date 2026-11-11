"""Score a look on a REAL scene, so nothing depends on the monitor.

Shoot one scene twice from a fixed phone: plain (no filter, sliders 0) and with the look's
settings. The Fuji target is the plain shot pushed through T_sim. The look's shot is compared
with that target pixel by pixel (after a light blur and downscale, which hides sensor noise
and tiny misalignment), in CIEDE2000. Clipped and near-black pixels are left out because
their colour is undefined. Writes a three-panel sheet: plain | phone look | Fuji target.

    python scene_check.py plain.jpg look.jpg targets/T_CLASSIC-CHROME.cube --sheet report/x.jpg
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import colour
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fit import D65, fuji_target, load_cube, sims  # noqa: E402
from measure import chart_layout, find_chart, gamut_of  # noqa: E402

WIDTH = 640


def to_srgb(path: Path, page: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Photo -> (display sRGB, the photo's own codes), both 0..1, downscaled and lightly blurred.

    sRGB may leave 0..1 for a P3 photo; the raw codes are what tells a clipped pixel.

    With a scene page name (scene_pages.py), the photo is first rectified from its four markers
    and cropped to the picture area, so two shots of the same page line up pixel for pixel.
    """
    bgr = cv2.imread(str(path))
    if page:
        layout = chart_layout(page)
        Hm = find_chart(bgr, layout)
        x, y, w, h = layout["image_rect"]
        shift = np.array([[1, 0, x], [0, 1, y], [0, 0, 1]], float)
        bgr = cv2.warpPerspective(bgr, Hm @ shift, (w, h), flags=cv2.WARP_INVERSE_MAP | cv2.INTER_AREA)
    small = cv2.resize(bgr, (WIDTH, round(bgr.shape[0] * WIDTH / bgr.shape[1])), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 1.2)
    rgb = raw = small[..., ::-1].astype(np.float64) / 255
    gamut = gamut_of(path)
    if gamut != "sRGB":
        rgb = colour.RGB_to_RGB(rgb, colour.RGB_COLOURSPACES[gamut], colour.RGB_COLOURSPACES["sRGB"],
                                apply_cctf_decoding=True, apply_cctf_encoding=True)
    return rgb, raw


def lab(srgb: np.ndarray) -> np.ndarray:
    return colour.XYZ_to_Lab(colour.sRGB_to_XYZ(srgb), D65)


def check(plain: Path, look: Path, cube: Path, sheet: Path | None, page: str | None = None,
          provia: Path | None = None) -> dict:
    """dE2000 per pixel between the look's shot and the Fuji target made from the plain shot.

    Target = fit.fuji_target (plain + T_sim(plain) - T_PROVIA(plain) in Lab), as in fit2.py, so
    colours outside sRGB are not clipped and Provia is exactly "do nothing".
    """
    (p, raw_p), (l, raw_l) = to_srgb(plain, page), to_srgb(look, page)
    lab_p = lab(p)
    target_lab = fuji_target(lab_p, load_cube(cube), load_cube(provia or sims()["PROVIA"]))
    target = np.clip(colour.XYZ_to_sRGB(colour.Lab_to_XYZ(target_lab, D65)), 0, 1)
    # clipped in either shot (judged on the photos' own codes, so saturated P3 colours stay in)
    valid = (raw_p.max(-1) < 0.97) & (raw_l.max(-1) < 0.97) & (lab_p[..., 0] > 6)
    d = colour.delta_E(lab(l), target_lab, method="CIE 2000")[valid]
    d0 = colour.delta_E(lab_p, target_lab, method="CIE 2000")[valid]
    p, l = np.clip(p, 0, 1), np.clip(l, 0, 1)
    if sheet:
        panels = []
        sheet.parent.mkdir(parents=True, exist_ok=True)
        for img, text, part in zip((p, l, target), ("phone, plain", "phone, look", "Fuji target"),
                                   ("plain", "look", "fuji")):
            panel = np.ascontiguousarray(np.uint8(np.round(img * 255)))
            # unlabelled copy of each panel, for the report page's before/after wipe
            cv2.imwrite(str(sheet.with_name(f"{sheet.stem}_{part}.jpg")), panel[..., ::-1],
                        [cv2.IMWRITE_JPEG_QUALITY, 84])
            cv2.putText(panel, text, (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
            panels.append(panel)
        row = np.concatenate(panels, 1)
        cv2.imwrite(str(sheet), row[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 90])
    return {"pixels": int(valid.sum()),
            "look_vs_target": {"mean": round(float(d.mean()), 2), "p90": round(float(np.percentile(d, 90)), 2)},
            "plain_vs_target": {"mean": round(float(d0.mean()), 2), "p90": round(float(np.percentile(d0, 90)), 2)}}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("plain")
    ap.add_argument("look")
    ap.add_argument("cube")
    ap.add_argument("--sheet")
    ap.add_argument("--page", help="scene page name, e.g. scene_sunset, to rectify by markers")
    ap.add_argument("--provia", help="T_PROVIA cube (default: the one in targets/)")
    a = ap.parse_args()
    print(json.dumps(check(Path(a.plain), Path(a.look), Path(a.cube), Path(a.sheet) if a.sheet else None,
                           a.page, Path(a.provia) if a.provia else None)))
