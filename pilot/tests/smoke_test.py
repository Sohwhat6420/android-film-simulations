"""Offline smoke test for the pilot pipeline. No phone, no monitor, no Fujifilm files.

    python pilot/tests/smoke_test.py

What it checks, in the order the real pipeline uses these pieces:
  1. charts.py redraws the committed chart layouts byte for byte (the layouts are deterministic).
  2. measure.py finds the four ArUco markers and reads every patch back from a flat chart (error ~0).
  3. The same from a fake "phone photo": the chart warped in perspective, blurred, noised and saved as
     a JPEG. That is the hard case: the reading must stay well under 1 dE2000 (below what anyone can see).
  4. build_targets.make_inverse inverts a synthetic 3D LUT, the step that turns Fujifilm's F-Log2 LUTs
     into "Provia -> other look" transforms. A made-up LUT stands in for Fujifilm's, so nothing of theirs
     is needed or shipped.
  5. .cube files survive a write/read round trip.
  6. make_preset.py writes a camera preset zip the camera would accept (5120-byte limit).
Exit code 0 only if every check passes.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import warnings
import zipfile
from pathlib import Path

warnings.filterwarnings("ignore", message='.*"Matplotlib" related API')  # colour-science's optional plotting

import colour  # noqa: E402
import cv2
import numpy as np

PILOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PILOT / "scripts"))

import charts  # noqa: E402

failures = 0
checks = 0


def check(name: str, ok: bool, detail: str) -> None:
    global failures, checks
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    checks += 1
    failures += 0 if ok else 1


def chart_lab(layout: dict) -> np.ndarray:
    srgb = np.array([p["srgb"] for p in layout["patches"]], dtype=np.float64) / 255
    XYZ = colour.sRGB_to_XYZ(srgb)
    return colour.XYZ_to_Lab(XYZ, colour.CCS_ILLUMINANTS["CIE 1931 2 Degree Standard Observer"]["D65"])


def fake_photo(png: Path, out: Path, seed: int = 7) -> Path:
    """Warp the chart into a 4000x3000 frame at an angle, soften it and add sensor-like noise."""
    rng = np.random.default_rng(seed)
    img = cv2.imread(str(png))
    h, w = img.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[520, 430], [3460, 610], [3390, 2560], [610, 2420]])
    M = cv2.getPerspectiveTransform(src, dst)
    frame = cv2.warpPerspective(img, M, (4000, 3000), flags=cv2.INTER_AREA, borderValue=(40, 40, 40))
    frame = cv2.GaussianBlur(frame, (0, 0), 1.6)
    frame = np.clip(frame + rng.normal(0, 2.0, frame.shape), 0, 255).astype(np.uint8)
    cv2.imwrite(str(out), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return out


def main() -> int:
    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="afs-smoke-"))
    charts_dir = PILOT / "charts"

    # 1. Deterministic chart layouts: redraw into a temp folder and compare with the committed JSON.
    png60 = charts.render(tmp)
    pages = charts.dense_pages()
    for page in ("dense_a", "dense_b", "dense_v"):
        charts.render_page(tmp, page, pages[page])
    same = [n for n in ("chart60", "dense_a", "dense_b", "dense_v")
            if json.loads((tmp / f"{n}.json").read_text()) == json.loads((charts_dir / f"{n}.json").read_text())]
    check("chart layouts", len(same) == 4, f"{len(same)}/4 redrawn identical to pilot/charts/*.json "
          f"({sum(len(json.loads((tmp / f'{n}.json').read_text())['patches']) for n in same)} patches)")

    # measure.py reads pilot/charts/*.json at import time, so import it only once they are checked.
    import measure  # noqa: E402

    # 2. Flat chart: the drawn PNG read straight back.
    ref60 = chart_lab(measure.chart_layout("chart60"))
    r = measure.read_patches(png60)
    d = colour.delta_E(np.array(r["Lab"]), ref60, method="CIE 2000")
    check("read flat chart60", d.max() < 0.05, f"{len(d)} patches, dE2000 mean {d.mean():.3f}, max {d.max():.3f}")

    # 3. Fake phone photos: chart60 and the held-out validation page.
    for name, png in (("chart60", png60), ("dense_v", tmp / "dense_v.png")):
        shot = fake_photo(png, tmp / f"{name}_shot.jpg")
        r = measure.read_patches(shot, chart=name)
        d = colour.delta_E(np.array(r["Lab"]), chart_lab(measure.chart_layout(name)), method="CIE 2000")
        check(f"read warped {name} photo", d.mean() < 0.5 and d.max() < 1.5,
              f"{len(d)} patches, dE2000 mean {d.mean():.2f}, max {d.max():.2f} (limit 0.5 / 1.5)")

    # 4. Invert a synthetic LUT (a smooth, monotone stand-in for an F-Log2 LUT: curve plus channel cross-talk).
    import build_targets as bt  # noqa: E402

    n = 17
    V = bt.cube_points(n)
    mix = np.array([[0.86, 0.10, 0.04], [0.06, 0.88, 0.06], [0.03, 0.12, 0.85]])
    base = ((V ** 0.75) @ mix.T).reshape(n, n, n, 3)
    rng = np.random.default_rng(3)
    x = rng.uniform(0.05, 0.95, (400, 3))
    Y = bt.apply_lut(base, x)
    inverse = bt.make_inverse(base)
    v, inside = inverse(Y)
    err = np.abs(bt.apply_lut(base, v) - Y).max(axis=1)
    check("invert synthetic 17^3 LUT", err.max() < 1e-4 and inside.mean() > 0.99,
          f"400 colours, worst residual {err.max():.1e}, {inside.mean():.0%} inside the output hull")

    # 5. .cube round trip.
    cube = tmp / "synthetic.cube"
    bt.write_cube(cube, base, "synthetic", "smoke test, not a Fujifilm look")
    back = bt.read_cube(cube)
    check(".cube write/read", back.shape == base.shape and np.abs(back - np.clip(base, 0, 1)).max() < 1e-6,
          f"{n}^3 grid, max difference {np.abs(back - np.clip(base, 0, 1)).max():.1e}")

    # 6. A camera preset zip.
    import make_preset  # noqa: E402

    entries = make_preset.preset_entries(steps={"saturation": 1, "contrast": 4})
    z = make_preset.write_preset(tmp / "presets", "duchamp", "Classic-Chrome", entries, created_ms=1760000000000)
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        ok = zf.testzip() is None and names == entries
    check("camera preset zip", ok and z.stat().st_size <= 5120,
          f"{len(names)} entries, {z.stat().st_size} bytes (camera limit 5120)")

    print(f"\n{checks - failures} of {checks} checks passed, {time.time() - t0:.1f} s")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
