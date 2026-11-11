"""Find the chart in a phone photo and read every patch.

Steps: detect the four ArUco markers, fit a homography from chart pixels to photo pixels, then
average the middle half of each patch in LINEAR light (so leftover screen texture averages
out instead of biasing the mean). Colours are returned in sRGB and CIELAB (D65). The phone's
JPEGs are Display P3, so the gamut is chosen from the file's ICC description, not assumed.

    python measure.py captures/poco/baseline.jpg          # prints a summary
    python measure.py shot.jpg --debug scratch/dbg.jpg    # also draws what it sampled
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import colour
import cv2
import numpy as np
from PIL import Image, ImageCms

ROOT = Path(__file__).resolve().parent.parent
CHART = json.loads((ROOT / "charts" / "chart60.json").read_text())


def chart_layout(name: str = "chart60") -> dict:
    """Marker and patch layout of a chart drawn by charts.py (chart60, dense_a, dense_b, dense_v)."""
    return CHART if name == "chart60" else json.loads((ROOT / "charts" / f"{name}.json").read_text())
INNER = 0.5  # sample the central 50% x 50% of each patch


def gamut_of(path: Path) -> str:
    icc = Image.open(path).info.get("icc_profile")
    if not icc:
        return "sRGB"
    desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).lower()
    return "Display P3" if "p3" in desc else "sRGB"


def find_chart(bgr: np.ndarray, chart: dict = CHART) -> np.ndarray:
    """Homography chart-px -> photo-px from the four markers.

    The chart is shot slightly out of focus on purpose (it removes moire from the screen's pixel
    grid), so markers are looked for at several scales and the first sighting of each is kept.
    """
    gray0 = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    dic = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    loose = cv2.aruco.DetectorParameters()  # for softer frames: wider threshold windows, more lenient bits
    loose.adaptiveThreshWinSizeMax, loose.adaptiveThreshWinSizeStep = 75, 6
    loose.perspectiveRemoveIgnoredMarginPerCell, loose.maxErroneousBitsInBorderRate = 0.25, 0.5
    loose.errorCorrectionRate = 0.8
    sharp = cv2.addWeighted(gray0, 2.0, cv2.GaussianBlur(gray0, (0, 0), 6), -1.0, 0)  # unsharp mask
    found: dict[int, np.ndarray] = {}
    for params, img in ((cv2.aruco.DetectorParameters(), gray0), (loose, gray0), (loose, sharp)):
        det = cv2.aruco.ArucoDetector(dic, params)
        for width in (1600, 800, 1200, 2400, 1000):
            scale = width / max(img.shape)
            corners, ids, _ = det.detectMarkers(cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
            for c, i in zip(corners, [] if ids is None else ids.ravel()):
                if str(i) in chart["markers"] and int(i) not in found:
                    found[int(i)] = c.reshape(4, 2) / scale
            if len(found) == 4:
                break
        if len(found) == 4:
            break
    src, dst = [], []
    for i, c in found.items():
        x, y, s = chart["markers"][str(i)]
        src += [(x, y), (x + s, y), (x + s, y + s), (x, y + s)]
        dst += list(c)
    if len(src) < 12:
        raise RuntimeError(f"only {len(src) // 4} of 4 markers found")
    H, _ = cv2.findHomography(np.float32(src), np.float32(dst))
    return H


def read_patches(path: Path, debug: Path | None = None, chart: str = "chart60") -> dict:
    layout = chart_layout(chart)
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    H = find_chart(bgr, layout)
    gamut = gamut_of(path)
    means, spreads = [], []
    dbg = bgr.copy() if debug else None
    ih, iw = bgr.shape[:2]
    for p in layout["patches"]:
        x, y, w, h = p["rect"]
        m = (1 - INNER) / 2
        quad = np.float32([[x + m * w, y + m * h], [x + (1 - m) * w, y + m * h],
                           [x + (1 - m) * w, y + (1 - m) * h], [x + m * w, y + (1 - m) * h]])
        q = cv2.perspectiveTransform(quad[None], H)[0].astype(np.int32)
        # Region of interest: only this patch's bounding box is turned into linear light and masked, not the
        # whole 12 MP frame for every patch. Integer offsets and the same fill keep the sampled pixels, and so
        # the means, identical to the whole-frame version.
        x0, y0 = max(int(q[:, 0].min()), 0), max(int(q[:, 1].min()), 0)
        x1, y1 = min(int(q[:, 0].max()) + 1, iw), min(int(q[:, 1].max()) + 1, ih)
        mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.fillConvexPoly(mask, q - np.int32([x0, y0]), 1)
        crop = bgr[y0:y1, x0:x1][..., ::-1].astype(np.float64) / 255
        px = colour.cctf_decoding(crop[mask.astype(bool)], function="sRGB")  # both sRGB and Display P3 use the sRGB curve
        means.append(px.mean(0))
        spreads.append(px.std(0).mean())
        if dbg is not None:
            cv2.polylines(dbg, [q], True, (0, 255, 255), 4)
    lin_mean = np.array(means)
    XYZ = colour.RGB_to_XYZ(lin_mean, colour.RGB_COLOURSPACES[gamut], apply_cctf_decoding=False)
    srgb = colour.XYZ_to_sRGB(XYZ)  # display-referred sRGB, may fall outside 0..1 for P3 colours
    Lab = colour.XYZ_to_Lab(XYZ, colour.CCS_ILLUMINANTS["CIE 1931 2 Degree Standard Observer"]["D65"])
    if debug:
        cv2.imwrite(str(debug), cv2.resize(dbg, None, fx=0.3, fy=0.3))
    return {
        "file": str(path), "gamut": gamut,
        "chart": chart, "names": [p["name"] for p in layout["patches"]],
        "XYZ": XYZ.tolist(), "srgb": srgb.tolist(), "Lab": Lab.tolist(),
        "noise": spreads,
        "max_code": float(bgr.max()) / 255,  # the whole frame's brightest code, as before (uint8 max, then the same /255)
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("photo")
    ap.add_argument("--debug")
    ap.add_argument("--json")
    a = ap.parse_args()
    r = read_patches(Path(a.photo), Path(a.debug) if a.debug else None)
    if a.json:
        Path(a.json).write_text(json.dumps(r))
    Lab = np.array(r["Lab"])
    print(f"{r['gamut']}; patch noise (linear std) median {np.median(r['noise']):.4f}")
    for i in (18, 19, 20, 21, 22, 23):
        print(f"{r['names'][i]:<22} L*={Lab[i,0]:6.1f} a*={Lab[i,1]:6.1f} b*={Lab[i,2]:6.1f}")
    ramp = Lab[24:36, 0]
    print("grey ramp L*:", " ".join(f"{v:.0f}" for v in ramp))
