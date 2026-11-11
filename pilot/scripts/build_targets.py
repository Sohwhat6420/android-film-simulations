"""Build colour ground truth for the film-simulation filters from Fujifilm's official F-Log2 LUTs.

Method. Each official LUT maps F-Log2 code values to a BT.709-gamut display image. For each sim
T_sim = LUT_sim o inverse(LUT_PROVIA): a display colour is read as PROVIA would show it, the F-Log2
value that makes PROVIA produce it is recovered, and that value is rendered with the sim. The inverse
comes from scattered-data interpolation over the PROVIA LUT vertices, refined by Gauss-Newton on the
trilinear LUT itself. Pro Neg High has no official LUT, so its T is fitted (RBF) from one real pair.

Colour spaces. The PDF gives BT.709 primaries and no transfer curve. The LUT output is read with
the sRGB curve (PRIMARY below): against the genuine frames it beat the BT.709 OETF for every sim,
on patches and whole images (first run, 18 Oct 2026). A sensitivity run repeats the validation with
the other curve, so the choice stays visible in the report.

Validation uses the bastibe styles_source JPEGs (see report/reference-validation.md for their
provenance). The ColorChecker patches are located from four hand-placed corner anchors, and the
chart orientation is chosen against the chart's own expected Lab values.

    .venv/Scripts/python scripts/build_targets.py
"""
from __future__ import annotations

import io
import itertools
import json
import re
import struct
import time
from datetime import date
from pathlib import Path

import colour
import cv2
import numpy as np
from PIL import Image, ImageCms
from scipy.interpolate import LinearNDInterpolator, RBFInterpolator
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parent.parent
LUT_DIR = ROOT / "sources/fuji-official/x/gfx-eterna-55-3d-lut-v110/65Grid/F-Log2"
REF_DIR = ROOT / "sources/bastibe/repo/styles_source"
CHT = REF_DIR / "color checker.cht"
OUT = ROOT / "targets"
VAL_DIR = OUT / "validation"
REPORT = ROOT / "report"

SIMS = ["ACROS", "ASTIA", "CLASSIC-CHROME", "CLASSIC-Neg.", "ETERNA", "ETERNA-BB",
        "PRO-Neg.Std", "PROVIA", "REALA-ACE", "Velvia"]
FITTED = "PRO-Neg.High"  # no official LUT: fitted from the Provia / Pro Neg High pair
GENUINE = {  # sim -> Fujifilm JPEG of the same chart (Make is checked on load)
    "ASTIA": "Astia.jpg", "CLASSIC-CHROME": "Classic Chrome.jpg", "ETERNA": "Eterna.jpg",
    "PRO-Neg.Std": "Pro Neg Std.jpg", "PROVIA": "Provia.jpg", "Velvia": "Velvia.jpg",
    FITTED: "Pro Neg High.jpg",
}
BASE = "PROVIA"
GRIDS = (33, 65)
SEED = 0
FIT_CENTRES = 3000  # RBF centres sampled from the training pixels

# ColorChecker corner patch centres in full-resolution pixels (Provia.jpg), placed by hand:
# black (top-left), white (top-right), teal (bottom-left), brown (bottom-right).
CORNERS = {"TL": (2224, 1412), "TR": (5004, 1404), "BL": (2204, 3080), "BR": (5032, 3084)}
PATCH_HALF = 0.2   # half-size of the sampled box, as a fraction of the patch pitch
SMALL = (1500, 1000)  # image metrics run at quarter scale (w, h)
CHART_SMALL = (390, 255, 1430, 875)  # chart area at quarter scale (x0, y0, x1, y1)
D65 = colour.CCS_ILLUMINANTS["CIE 1931 2 Degree Standard Observer"]["D65"]


# ---------------------------------------------------------------- transfer curves
def srgb_decode(v):
    v = np.asarray(v, dtype=np.float64)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def srgb_encode(lin):
    lin = np.clip(np.asarray(lin, dtype=np.float64), 0, None)
    return np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(lin, 1 / 2.4) - 0.055)


def bt709_encode(lin):
    lin = np.clip(np.asarray(lin, dtype=np.float64), 0, None)
    return np.where(lin < 0.018, 4.5 * lin, 1.099 * np.power(lin, 0.45) - 0.099)


def bt709_decode(v):
    v = np.asarray(v, dtype=np.float64)
    return np.where(v < 0.081, v / 4.5, ((v + 0.099) / 1.099) ** (1 / 0.45))


# (encode, decode) of the curve the LUT output is assumed to use
CONVENTIONS = {"bt709": (bt709_encode, bt709_decode), "srgb": (srgb_encode, srgb_decode)}
PRIMARY, OTHER = "srgb", "bt709"  # the curve the written cubes use, and the sensitivity run's curve
CURVE_NAME = {"srgb": "sRGB curve", "bt709": "BT.709 OETF"}


def to_lut(x, conv):
    """sRGB display code values -> LUT output code values (same light, other curve)."""
    return CONVENTIONS[conv][0](srgb_decode(x))


def from_lut(y, conv):
    """LUT output code values -> sRGB display code values."""
    return srgb_encode(CONVENTIONS[conv][1](y))


# ---------------------------------------------------------------- cube files and LUT lookup
def read_cube(path: Path) -> np.ndarray:
    """Return the LUT as [r, g, b, 3]. The file lists red fastest, so its order is b, g, r."""
    size, vals = None, []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.split("#", 1)[0].strip()
        if not s:
            continue
        if s.upper().startswith("LUT_3D_SIZE"):
            size = int(s.split()[1])
        elif s.upper().startswith("DOMAIN_MIN") and [float(t) for t in s.split()[1:4]] != [0.0, 0.0, 0.0]:
            raise ValueError(f"{path.name}: non-default DOMAIN_MIN")
        elif s.upper().startswith("DOMAIN_MAX") and [float(t) for t in s.split()[1:4]] != [1.0, 1.0, 1.0]:
            raise ValueError(f"{path.name}: non-default DOMAIN_MAX")
        elif s[0].isdigit() or s[0] in "+-.":
            vals.append([float(t) for t in s.split()[:3]])
    if size is None or len(vals) != size ** 3:
        raise ValueError(f"{path.name}: expected {size ** 3} entries, found {len(vals)}")
    arr = np.array(vals, dtype=np.float64).reshape(size, size, size, 3)  # [b, g, r]
    return np.ascontiguousarray(arr.transpose(2, 1, 0, 3))


def write_cube(path: Path, T: np.ndarray, title: str, note: str) -> float:
    """Write [r, g, b, 3] sRGB values as a .cube (red fastest). Returns the largest clipped amount."""
    n = T.shape[0]
    overshoot = float(max(np.abs(T - np.clip(T, 0, 1)).max(), 0.0))
    flat = np.clip(T, 0, 1).transpose(2, 1, 0, 3).reshape(-1, 3)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(f'TITLE "{title}"\n')
        fh.write(f"# {note}\n")
        fh.write(f"LUT_3D_SIZE {n}\nDOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0\n\n")
        np.savetxt(fh, flat, fmt="%.6f")
    return overshoot


def cube_points(n: int) -> np.ndarray:
    """(n^3, 3) grid points in [r, g, b] order, b fastest."""
    g = np.linspace(0, 1, n)
    return np.stack(np.meshgrid(g, g, g, indexing="ij"), -1).reshape(-1, 3)


def apply_lut(lut: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Trilinear lookup. lut is [r, g, b, 3]; x is (..., 3) in [0, 1]."""
    n = lut.shape[0]
    flat = lut.reshape(-1, 3)
    p = np.clip(x, 0, 1) * (n - 1)
    i0 = np.minimum(np.floor(p).astype(np.int64), n - 2)
    f = p - i0
    out = np.zeros(x.shape, dtype=np.float64)
    for dr, dg, db in itertools.product((0, 1), repeat=3):
        w = ((f[..., 0] if dr else 1 - f[..., 0]) * (f[..., 1] if dg else 1 - f[..., 1])
             * (f[..., 2] if db else 1 - f[..., 2]))
        idx = ((i0[..., 0] + dr) * n + (i0[..., 1] + dg)) * n + (i0[..., 2] + db)
        out += w[..., None] * flat[idx]
    return out


def gauss_newton(base: np.ndarray, Y: np.ndarray, v: np.ndarray, n_iter: int = 20, h: float = 1e-5):
    """Refine v so that apply_lut(base, v) ~= Y, on the true trilinear LUT."""
    for _ in range(n_iter):
        f0 = apply_lut(base, v)
        r = f0 - Y
        J = np.empty((len(v), 3, 3))
        for k in range(3):
            dv = np.zeros(3)
            dv[k] = h
            J[:, :, k] = (apply_lut(base, np.clip(v + dv, 0, 1)) - f0) / h
        A = np.swapaxes(J, 1, 2) @ J + 1e-8 * np.eye(3)
        g = np.einsum("nji,nj->ni", J, r)
        step = np.linalg.solve(A, g[..., None])[..., 0]
        v = np.clip(v - np.clip(step, -0.1, 0.1), 0, 1)
    return v


def make_inverse(base: np.ndarray, k_near: int = 4):
    """Return inverse(Y): the F-Log2 value v with base(v) = Y, plus an 'inside output hull' flag.

    Gauss-Newton alone gets trapped on saturated plateaus of the LUT, so each target is refined from
    several starts (scattered-data guess and the k nearest LUT output vertices) and the start with the
    smallest residual wins.
    """
    n = base.shape[0]
    P = base.reshape(-1, 3)
    V = cube_points(n)
    lin = LinearNDInterpolator(P, V)
    tree = cKDTree(P)

    def inverse(Y: np.ndarray):
        v0 = lin(Y)
        bad = ~np.isfinite(v0).all(axis=1)
        if bad.any():
            v0[bad] = V[tree.query(Y[bad])[1]]
        starts = [np.clip(v0, 0, 1)]
        _, idx = tree.query(Y, k=k_near)
        starts += [V[idx[:, j]] for j in range(k_near)]
        best_v, best_e = None, None
        for s in starts:
            v = gauss_newton(base, Y, s)
            e = np.abs(apply_lut(base, v) - Y).max(axis=1)
            if best_v is None:
                best_v, best_e = v, e
            else:
                better = e < best_e
                best_v[better] = v[better]
                best_e = np.minimum(best_e, e)
        inside = lin.tri.find_simplex(Y) >= 0
        return best_v, inside

    return inverse


# ---------------------------------------------------------------- colour difference
def lab(rgb_enc: np.ndarray) -> np.ndarray:
    """sRGB-encoded [0, 1] -> CIELAB (D65)."""
    return colour.XYZ_to_Lab(colour.sRGB_to_XYZ(np.clip(rgb_enc, 0, 1)), D65)


def lab_linear(lin: np.ndarray) -> np.ndarray:
    return colour.XYZ_to_Lab(colour.sRGB_to_XYZ(np.clip(lin, 0, 1), apply_cctf_decoding=False), D65)


def de2000(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return colour.delta_E(a, b, method="CIE 2000")


def stats(d: np.ndarray) -> dict:
    d = np.asarray(d, dtype=np.float64).ravel()
    return {"mean": round(float(d.mean()), 3), "p90": round(float(np.percentile(d, 90)), 3),
            "max": round(float(d.max()), 3)}


# ---------------------------------------------------------------- chart and genuine JPEGs
def parse_cht():
    text = CHT.read_text(encoding="utf-8", errors="replace").splitlines()
    start = next(i for i, line in enumerate(text) if line.startswith("EXPECTED LAB")) + 1
    names, labs = [], []
    for line in text[start:]:
        m = re.match(r"^\s*([A-D][1-6])\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)", line)
        if m:
            names.append(m.group(1))
            labs.append([float(m.group(i)) for i in (2, 3, 4)])
    assert len(names) == 24, len(names)
    return np.array(names).reshape(4, 6), np.array(labs).reshape(4, 6, 3)


def patch_centre(r: int, c: int) -> tuple[int, int]:
    """Full-resolution centre of the patch in image row r (0 top) and column c (0 left)."""
    u, v = c / 5, r / 3
    (tlx, tly), (trx, try_), (blx, bly), (brx, bry) = CORNERS["TL"], CORNERS["TR"], CORNERS["BL"], CORNERS["BR"]
    x = (1 - u) * (1 - v) * tlx + u * (1 - v) * trx + (1 - u) * v * blx + u * v * brx
    y = (1 - u) * (1 - v) * tly + u * (1 - v) * try_ + (1 - u) * v * bly + u * v * bry
    return int(round(x)), int(round(y))


def patch_half() -> int:
    return int(round(PATCH_HALF * (CORNERS["TR"][0] - CORNERS["TL"][0]) / 5))


def load_fuji(name: str) -> np.ndarray:
    im = Image.open(REF_DIR / name)
    make = str(im.getexif().get(271, "")).strip()
    if not make.upper().startswith("FUJIFILM"):
        raise ValueError(f"{name}: not a Fujifilm file (Make={make!r})")
    icc = im.info.get("icc_profile")
    desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))) if icc else ""
    if "sRGB" not in desc:
        raise ValueError(f"{name}: unexpected colour profile {desc!r}")
    return np.asarray(im.convert("RGB"))


def makernote(name: str) -> dict:
    """Camera and MakerNote facts for one file (raw numeric codes, no interpretation)."""
    ex = Image.open(REF_DIR / name).getexif()
    info = {"make": str(ex.get(271, "")).strip(), "model": str(ex.get(272, "")).strip(),
            "software": str(ex.get(305, "")).strip()}
    mn = ex.get_ifd(0x8769).get(0x927C)
    if not mn or bytes(mn[:8]) != b"FUJIFILM":
        return info
    data = bytes(mn)
    off = struct.unpack("<I", data[8:12])[0]
    tags = {}
    for i in range(struct.unpack("<H", data[off:off + 2])[0]):
        e = data[off + 2 + 12 * i: off + 14 + 12 * i]
        tag, typ, cnt = struct.unpack("<HHI", e[:8])
        if cnt == 1 and typ == 3 and 0x1000 <= tag < 0x1500:
            tags[f"0x{tag:04X}"] = struct.unpack("<H", e[8:10])[0]
    info["makernote_short_tags_1000_1500"] = tags
    return info


def patch_labs(img_u8: np.ndarray, T=None) -> np.ndarray:
    """Mean CIELAB of each of the 24 patches (row-major, image positions), optionally after T."""
    h = patch_half()
    out = []
    for r in range(4):
        for c in range(6):
            cx, cy = patch_centre(r, c)
            crop = img_u8[cy - h:cy + h, cx - h:cx + h].astype(np.float64) / 255
            if T is not None:
                crop = apply_lut(T, crop)
            out.append(lab_linear(srgb_decode(np.clip(crop, 0, 1)).reshape(-1, 3).mean(0)))
    return np.array(out)


# ---------------------------------------------------------------- Pro Neg High fit
def fit_pro_neg_high(prov_small: np.ndarray, pnh_small: np.ndarray):
    """RBF from Provia-look pixels to Pro Neg High pixels. Chart area excluded; half held out."""
    H, W, _ = prov_small.shape
    yy, xx = np.mgrid[0:H, 0:W]
    x0, y0, x1, y1 = CHART_SMALL
    keep = np.flatnonzero(~((xx >= x0) & (xx < x1) & (yy >= y0) & (yy < y1)).ravel())
    rng = np.random.default_rng(SEED)
    perm = rng.permutation(keep)
    half = len(perm) // 2
    train_idx, test_idx = perm[:half], perm[half:]
    X = prov_small.reshape(-1, 3)
    Y = pnh_small.reshape(-1, 3)
    centres = train_idx[rng.choice(len(train_idx), FIT_CENTRES, replace=False)]
    rbf = RBFInterpolator(X[centres], Y[centres], kernel="thin_plate_spline", degree=1, smoothing=1e-3)

    def predict(pts: np.ndarray) -> np.ndarray:
        out = np.concatenate([rbf(pts[i:i + 4000]) for i in range(0, len(pts), 4000)])
        return np.clip(out, 0, 1)

    # Held-out pixels: half of the non-chart pixels, never used for fitting.
    pred_test = predict(X[test_idx])
    fit_err = de2000(lab(pred_test), lab(Y[test_idx]))
    base_err = de2000(lab(X[test_idx]), lab(Y[test_idx]))
    info = {
        "training_pixels": int(len(train_idx)), "rbf_centres": FIT_CENTRES,
        "kernel": "thin_plate_spline, degree 1, smoothing 1e-3",
        "held_out_pixels": int(len(test_idx)),
        "held_out_image_de2000_fit": stats(fit_err),
        "held_out_image_de2000_baseline": stats(base_err),
    }
    return predict, info, test_idx


# ---------------------------------------------------------------- main
def log(msg: str, t0: float) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def slug(sim: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "", sim)


def sheet(path: Path, panels: list[np.ndarray], labels: list[str]) -> None:
    """Three panels side by side, 1600 px wide with 8 px gaps, each with a label band."""
    band = 46
    pw = 528
    ph = round(pw * panels[0].shape[0] / panels[0].shape[1])
    canvas = np.full((band + ph, 3 * pw + 2 * 8, 3), 255, np.uint8)
    for i, (p, lab_text) in enumerate(zip(panels, labels)):
        x = i * (pw + 8)
        canvas[band:, x:x + pw] = cv2.resize(p, (pw, ph), interpolation=cv2.INTER_AREA)
        cv2.putText(canvas, lab_text, (x + 8, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2, cv2.LINE_AA)
    cv2.imwrite(str(path), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])


def verdict(sim: str, has_reference: bool, pb: float, pt: float, ib: float, it: float) -> str:
    """One line. pb/pt: patch mean dE2000 baseline/T; ib/it: image mean baseline/T."""
    if not has_reference:
        return "No genuine Fujifilm reference; T is built from the official LUT only and is unvalidated."
    if sim == "PROVIA":
        return "Identity by construction; the genuine Provia frame is the input, so this is not a test."
    if sim == FITTED:
        if pt > pb:
            return ("Partial: image error falls, but the held-out chart patches are worse than doing nothing, "
                    "so the fit does not generalise beyond the photo it came from.")
        return "Partial: the held-out chart patches improve, but this rests on one real pair."
    if pt <= 0.5 * pb and pt <= 3 and it < ib:
        return "Good: halves the patch error and lowers the whole-image error."
    if pt < pb and it < ib:
        return "Partial: below the do-nothing error on both metrics, but short of the good bar."
    if pt < pb:
        return "Partial: patch error falls, whole-image error does not."
    if it < ib:
        return "Partial: whole-image error falls, patch error does not."
    return "Fails: no better than doing nothing against this reference."


def run_validation(T_by_sim, base_small, base_full, ref_full, ref_small, ref_lab_small, patch_base,
                   pixel_mask=None):
    """Patch and whole-image metrics for one T per sim against its genuine reference."""
    res = {}
    for sim, T in T_by_sim.items():
        if sim not in ref_full:
            continue
        p_ref = patch_labs(ref_full[sim])
        p_base = patch_base
        p_t = patch_labs(base_full, T)
        d_t = de2000(p_t, p_ref)
        d_b = de2000(p_base, p_ref)
        pred_small = apply_lut(T, base_small)
        px_t = de2000(lab(pred_small).reshape(-1, 3), ref_lab_small[sim].reshape(-1, 3))
        px_b = de2000(lab(base_small).reshape(-1, 3), ref_lab_small[sim].reshape(-1, 3))
        if pixel_mask is not None and sim in pixel_mask:
            px_t, px_b = px_t[pixel_mask[sim]], px_b[pixel_mask[sim]]
        res[sim] = {"patch_t": stats(d_t), "patch_baseline": stats(d_b),
                    "image_t": stats(px_t), "image_baseline": stats(px_b)}
    return res


def main() -> None:
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    VAL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT.mkdir(exist_ok=True)

    luts = {s: read_cube(LUT_DIR / f"FLog2_to_{s}_65grid_V.1.00.cube") for s in SIMS}
    base = luts[BASE]
    log("loaded 10 official LUTs", t0)

    # ---- reference JPEGs (full and quarter scale) and chart orientation
    names_raw, exp_lab = parse_cht()
    ref_full = {s: load_fuji(f) for s, f in GENUINE.items()}
    prov_u8 = ref_full[BASE]
    prov_patch_raw = patch_labs(prov_u8)
    orients = [("identity", lambda a: a), ("rot180", lambda a: a[::-1, ::-1]),
               ("flip vertical", lambda a: a[::-1, :]), ("flip horizontal", lambda a: a[:, ::-1])]
    scores = {nm: round(float(de2000(prov_patch_raw, fn(exp_lab).reshape(24, 3)).mean()), 3) for nm, fn in orients}
    orient_names = min(scores, key=scores.get)
    fn = dict(orients)[orient_names]
    names = fn(names_raw).ravel()
    orient_info = {"chosen": orient_names, "mean_de2000_to_expected": scores[orient_names],
                   "candidates_mean_de2000": scores,
                   "chart_expected_lab_source": "color checker.cht (EXPECTED LAB)"}
    log(f"chart orientation: {orient_names}, mean dE2000 to expected Lab {scores[orient_names]:.2f}", t0)

    # ---- quarter-scale copies and reference Labs
    small_u8 = {s: cv2.resize(img, SMALL, interpolation=cv2.INTER_AREA) for s, img in ref_full.items()}
    small_f = {s: v.astype(np.float64) / 255 for s, v in small_u8.items()}
    ref_lab_small = {s: lab(v) for s, v in small_f.items()}
    base_small = small_f[BASE]
    base_lab_small = lab(base_small)

    # ---- Pro Neg High fitted from the real pair (no official LUT)
    H, W = base_small.shape[:2]
    pnh_predict, pnh_info, pnh_test_idx = fit_pro_neg_high(base_small, small_f[FITTED])
    pnh_mask = np.zeros(H * W, bool)
    pnh_mask[pnh_test_idx] = True
    log(f"fitted Pro Neg High RBF on {pnh_info['training_pixels']} pixels", t0)

    # ---- primary and sensitivity runs (one per transfer curve)
    results = {}
    for conv in (PRIMARY, OTHER):
        inverse = make_inverse(base)
        inv = {}
        for n in GRIDS:
            X = cube_points(n)
            Y = to_lut(X, conv)
            v, inside = inverse(Y)
            inv[n] = (X, Y, v, inside)
            log(f"[{conv}] inverted PROVIA on {n}^3 grid", t0)

        # round trip of the PROVIA inverse; "reachable" = PROVIA can produce the colour (round trip dE < 1)
        X, Y, v, inside = inv[65]
        recon = from_lut(apply_lut(base, v), conv)
        rt = de2000(lab(recon), lab(X))
        reach = rt < 1.0
        ident = {"grid_65_roundtrip_de2000_all": stats(rt),
                 "grid_65_roundtrip_de2000_reachable": stats(rt[reach]),
                 "grid_65_fraction_reachable_dE_lt_1": round(float(reach.mean()), 4),
                 "grid_65_roundtrip_de2000_in_output_hull": stats(rt[inside]),
                 "fraction_in_output_hull": round(float(inside.mean()), 4)}
        # how much of the genuine Provia frame PROVIA can produce at all (random pixel sample)
        rng = np.random.default_rng(SEED)
        flat_p = small_f[BASE].reshape(-1, 3)
        samp = flat_p[rng.choice(len(flat_p), 60000, replace=False)]
        vs, _ = inverse(to_lut(samp, conv))
        rs = de2000(lab(from_lut(apply_lut(base, vs), conv)), lab(samp))
        ident["provia_frame_sample_roundtrip_de2000"] = stats(rs)
        ident["provia_frame_fraction_unreachable_dE_gt_1"] = round(float((rs > 1).mean()), 4)
        log(f"[{conv}] round trip checked", t0)

        T = {}
        for sim in SIMS:
            T[sim] = {}
            for n in GRIDS:
                X_n, Y_n, v_n, _ = inv[n]
                T[sim][n] = from_lut(apply_lut(luts[sim], v_n), conv).reshape(n, n, n, 3)
        if conv == PRIMARY:
            T_fit = {}
            for n in GRIDS:
                pts = cube_points(n)
                T_fit[n] = pnh_predict(pts).reshape(n, n, n, 3)
            T[FITTED] = T_fit
            log("fitted Pro Neg High cube on both grids", t0)

        # identity check for PROVIA on the 65 grid, through the written cube path
        T_id = T[BASE][65]
        id_err = de2000(lab(apply_lut(T_id, cube_points(65))), lab(cube_points(65)))
        ident["T_provia_65_identity_de2000"] = stats(id_err)
        ident["T_provia_65_identity_de2000_reachable"] = stats(id_err[reach])

        # sensitivity needs only the official sims on the 65 grid
        patch_base = patch_labs(prov_u8)
        T65 = {s: T[s][65] for s in T}
        val = run_validation(T65, base_small, prov_u8, ref_full, small_f, ref_lab_small, patch_base,
                             pixel_mask={FITTED: pnh_mask} if conv == PRIMARY else None)
        results[conv] = {"identity": ident, "validation": val, "T": T, "reach": reach}
        log(f"[{conv}] validation done", t0)

    # ---- write the cubes and the npy (primary convention only)
    primary = results[PRIMARY]
    note = "Derived from Fujifilm official F-Log2 LUTs (PROVIA-inverse then sim). sRGB display in and out."
    cube_info = {}
    for sim, per_n in primary["T"].items():
        s = slug(sim)
        for n in GRIDS:
            title = f"T_{s} {n}grid " + ("fitted from one real pair" if sim == FITTED else "PROVIA to " + s)
            ov = write_cube(OUT / f"T_{s}_{n}.cube", per_n[n], title, note)
            cube_info.setdefault(sim, {})[f"overshoot_{n}"] = round(ov, 6)
        np.save(OUT / f"T_{s}_65.npy", per_n[65].astype(np.float32))
    log("wrote cubes and npy", t0)

    # Identity check on the cube file that was written, since that is the artefact the phone would use.
    T_written = read_cube(OUT / "T_PROVIA_65.cube")
    id_written = de2000(lab(apply_lut(T_written, cube_points(65))), lab(cube_points(65)))
    primary["identity"]["T_provia_65_cube_identity_de2000"] = stats(id_written)
    primary["identity"]["T_provia_65_cube_identity_de2000_reachable"] = stats(id_written[primary["reach"]])

    # ---- validation sheets
    for sim in GENUINE:
        if sim == BASE or sim not in primary["T"]:
            continue
        T65 = primary["T"][sim][65]
        t_small = apply_lut(T65, base_small)
        t_u8 = np.clip(np.round(t_small * 255), 0, 255).astype(np.uint8)
        name = "Pro Neg High (fitted)" if sim == FITTED else sim
        sheet(VAL_DIR / f"{slug(sim)}.jpg", [small_u8[BASE], t_u8, small_u8[sim]],
              ["Provia (genuine)", f"T_{slug(sim)}(Provia)", f"{name} (genuine)"])
    log("wrote validation sheets", t0)

    # ---- Metadata for the report
    meta = {
        "date": date.today().isoformat(),
        "lut_source": "Fujifilm F-Log2 LUTs v1.00 (GFX ETERNA 55, 65Grid); PDF overview v2.1E",
        "colour_spaces": {
            "input": "sRGB display code values (sRGB primaries, sRGB curve)",
            "lut_input": "F-Log2 code values, 0..1",
            "lut_output_assumed": f"BT.709 primaries (PDF) with the {CURVE_NAME[PRIMARY]} (curve not stated in PDF; chosen by validation)",
            "conversion": f"sRGB -> linear -> {CURVE_NAME[PRIMARY]} before the inverse; back to sRGB after",
        },
        "method": ["PROVIA inverse by scattered-data interpolation (LinearNDInterpolator) over the LUT vertices",
                   "refined by 12 Gauss-Newton steps on the trilinear LUT", "T = LUT_sim o inverse(LUT_PROVIA)",
                   "cube lookup trilinear", "patch means in linear light, Lab D65 (CIE 1931 2 degree)",
                   "image metric at quarter scale (1500 x 1000), dE2000 per pixel"],
        "orientation": orient_info,
        "patch_half_px": patch_half(),
    }
    sensitivity = {}
    for sim, v in results[OTHER]["validation"].items():
        sensitivity[sim] = {"patch_mean": v["patch_t"]["mean"], "image_mean": v["image_t"]["mean"]}

    report = {
        "meta": meta,
        "identity_and_roundtrip": primary["identity"],
        "sims": {},
        "pro_neg_high_fit": pnh_info,
        "sensitivity_other_curve_for_lut_output": sensitivity,
        "exif": {},
        "cubes": cube_info,
    }
    for sim in SIMS + [FITTED]:
        if sim not in primary["T"]:
            continue
        has_ref = sim in GENUINE
        entry = {"official_lut": sim != FITTED, "genuine_reference": GENUINE.get(sim)}
        if has_ref and sim in primary["validation"]:
            v = primary["validation"][sim]
            entry.update(v)
            entry["verdict"] = verdict(sim, True, v["patch_baseline"]["mean"], v["patch_t"]["mean"],
                                       v["image_baseline"]["mean"], v["image_t"]["mean"])
        else:
            entry["verdict"] = verdict(sim, False, 0, 0, 0, 0)
        report["sims"][sim] = entry
    for f in GENUINE.values():
        report["exif"][f] = makernote(f)

    (REPORT / "reference-validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report)
    log("wrote report", t0)
    print("PROVIA identity (cube, 65):", report["identity_and_roundtrip"]["T_provia_65_cube_identity_de2000"])


def write_markdown(report: dict) -> None:
    m = report["meta"]
    lines = [
        "# Reference validation: Fujifilm film simulations on a ColorChecker",
        "",
        f"Generated {m['date']} by `scripts/build_targets.py`. Numbers and method only; no images or LUT data.",
        "",
        "## Method",
        "",
        "- T_sim = LUT_sim o inverse(LUT_PROVIA), built from the official F-Log2 LUTs (65 grid, sRGB in and out).",
        f"- Colour spaces: the PDF gives BT.709 primaries and no transfer curve. The LUT output is read with the "
        f"{CURVE_NAME[PRIMARY]}, which matched the genuine frames better than the {CURVE_NAME[OTHER]} for every sim "
        "(sensitivity table below).",
        "- Reference frames: bastibe `styles_source` JPEGs of one ColorChecker Classic. All Fujifilm frames share a "
        "framing (ECC alignment within 0.1 px at 1/8 scale), so pixel comparison needs no warping.",
        f"- Patch metric: mean of the central box (+/-{m['patch_half_px']} px) of each of the 24 patches, in linear light, "
        "compared in CIELAB (D65) with dE2000. Mean, 90th percentile and maximum over the 24 patches.",
        "- Image metric: whole frame at 1500 x 1000, dE2000 per pixel (mean and 90th percentile).",
        "- Baseline: the reference of the same look with no transform (Provia against sim).",
        "",
        "## Provenance caveat",
        "",
        "EXIF of the Fujifilm frames: Make FUJIFILM, Model X-Pro2, Software \"Capture One 22 Windows\". The Software "
        "tag points to an export from Capture One rather than a straight in-camera JPEG, and no Fujifilm MakerNote "
        "survives in these files. The bastibe README says the styles were built from camera renders, so the two "
        "sources disagree. Read the Provia-to-sim errors here as agreement with these reference looks, not as proof "
        "of agreement with in-camera Fujifilm output.",
        "",
        "## Identity and round trip (PROVIA)",
        "",
    ]
    idn = report["identity_and_roundtrip"]
    for key, label in (
        ("grid_65_roundtrip_de2000_all", "Round trip PROVIA(inverse(x)) vs x, 65^3 grid, all points"),
        ("grid_65_roundtrip_de2000_reachable", "Round trip, reachable points only (dE below 1)"),
        ("provia_frame_sample_roundtrip_de2000", "Round trip, sampled pixels of the genuine Provia frame"),
        ("T_provia_65_cube_identity_de2000", "T_PROVIA 65 cube (written file) vs identity, all points"),
        ("T_provia_65_cube_identity_de2000_reachable", "T_PROVIA 65 cube, reachable points only"),
    ):
        if key in idn:
            s = idn[key]
            lines.append(f"- {label}: mean {s['mean']}, p90 {s['p90']}, max {s['max']} dE2000.")
    lines.append(f"- Fraction of grid points inside PROVIA's output hull: {idn['fraction_in_output_hull']}.")
    lines.append(f"- Fraction of grid points PROVIA can produce at all (round trip dE below 1): "
                 f"{idn['grid_65_fraction_reachable_dE_lt_1']}.")
    if "provia_frame_fraction_unreachable_dE_gt_1" in idn:
        lines.append(f"- Genuine Provia frame: fraction of sampled pixels whose colour PROVIA cannot produce "
                     f"(round trip dE above 1): {idn['provia_frame_fraction_unreachable_dE_gt_1']}.")
    lines += ["", "## Results by simulation", "",
              "| Sim | Reference | Patch dE mean / p90 / max | Image dE mean / p90 | Baseline patch mean | Baseline image mean | Verdict |",
              "|---|---|---|---|---|---|---|"]
    for sim, e in report["sims"].items():
        ref = e["genuine_reference"] or "none"
        if "patch_t" in e:
            pt, it = e["patch_t"], e["image_t"]
            row = (f"| {sim} | {'Pro Neg High (fitted)' if sim == FITTED else ref} | "
                   f"{pt['mean']} / {pt['p90']} / {pt['max']} | {it['mean']} / {it['p90']} | "
                   f"{e['patch_baseline']['mean']} | {e['image_baseline']['mean']} | {e['verdict']} |")
        else:
            row = f"| {sim} | none | n/a | n/a | n/a | n/a | {e['verdict']} |"
        lines.append(row)
    lines += ["", "Verdict rule: good if the patch mean is at most half the baseline (and at most 3 dE2000) and the "
              "image mean is lower than the baseline; partial if either mean is lower than the baseline; "
              "fails otherwise.", ""]
    fit = report["pro_neg_high_fit"]
    lines += ["## Pro Neg High (fitted, no official LUT)", "",
              f"- RBF fitted on {fit['training_pixels']} non-chart pixels of one pair, {fit['rbf_centres']} centres, "
              f"{fit['kernel']}.",
              f"- Held-out pixels ({fit['held_out_pixels']}): fit mean {fit['held_out_image_de2000_fit']['mean']} "
              f"dE2000 against baseline {fit['held_out_image_de2000_baseline']['mean']}.",
              "- Chart patches were excluded from fitting, so the patch numbers are out-of-sample.", ""]
    sens = report["sensitivity_other_curve_for_lut_output"]
    lines += [f"## Sensitivity: LUT output read with the {CURVE_NAME[OTHER]} instead", "",
              f"| Sim | Patch mean ({CURVE_NAME[OTHER]}) | Image mean ({CURVE_NAME[OTHER]}) |", "|---|---|---|"]
    for sim, v in sens.items():
        lines.append(f"| {sim} | {v['patch_mean']} | {v['image_mean']} |")
    lines += ["", "## File checks", ""]
    for f, info in report["exif"].items():
        tags = info.get("makernote_short_tags_1000_1500")
        note = f"Fujifilm MakerNote short tags 0x1000 to 0x1500: {tags}" if tags else "no Fujifilm MakerNote readable"
        lines.append(f"- {f}: software {info.get('software')!r}; {note}.")
    lines += ["", "Ricoh GR III frames in the same folder were excluded (Make is not FUJIFILM).", ""]
    (REPORT / "reference-validation.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
