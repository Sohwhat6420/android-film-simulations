"""Rebuild the Pro Neg High reference transform T_PRO-NegHigh with low-dimensional models.

Why. Pro Neg High has no official Fujifilm LUT. build_targets.py fits it with a 3000-centre RBF from
the one genuine Provia / Pro Neg High pair; that reaches 0.57 dE2000 on held-out pixels but 2.72 on
the held-out ColorChecker patches, worse than doing nothing (1.88). It memorises the photo.

Method. Fujifilm describes Pro Neg Hi as Pro Neg Std with more contrast, so every main candidate is
a small model M on top of the official transform: T_PNH(x) = M(T_PNS(x)), T_PNS read from
targets/T_PRO-NegStd_65.npy (clipped to [0, 1], as in the written cube). Families:

  A-lab   monotone tone curve f on L* (PCHIP through K knots, uniform in L*), a* and b* kept
  A-rgb   the same curve, applied by scaling linear RGB by Y(f(L*)) / Y(L*)
  B-*     A plus a chroma gain exp(c0 + c1 u + c2 u^2), u = L*/100 - 0.5 (hue kept)
  Bh-*    B plus a 2-harmonic hue term exp(sum a_k cos kh + b_k sin kh), k = 1, 2, on the gain
  C1      per-channel monotone curves (K knots each, uniform in sRGB code value; a monotone curve on
          the code value is a monotone curve in linear light)
  C2      a 3x3 matrix in linear light, then C1

Each family is also fitted (control) directly on Provia: T_PNH = M(x). And each family is fitted
a third way, on the GENUINE Pro Neg Std / Pro Neg High pair (M learns only the Std-to-High step,
not the errors of T_PNS on this photo), then applied as M(T_PNS(x)).

Fitting. Training and held-out pixels are the same split build_targets.py uses: the quarter-scale
frame (1500 x 1000) minus the chart box, permuted with seed 0, first half train, second half held
out. The ColorChecker patches are never used for fitting. Parameters minimise per-pixel dE2000
(scipy least_squares, a weak pull of the curves towards identity) on a 20k-pixel training sample.
The knot count K is chosen per family by 4-fold spatial cross-validation (fit on three image
quadrants, score the fourth) over K in KS.

Evaluation. Every candidate is baked into a 65-grid cube and scored exactly as the report scores
the others: patch mean / p90 / max dE2000 of T(genuine Provia) against genuine Pro Neg High at full
resolution, and image mean / p90 dE2000 at quarter scale on the held-out pixels. The chosen model is
the best held-out patch mean among candidates whose held-out image mean beats doing nothing. It is
written to targets/ only if its patch mean also beats the current RBF; the RBF files are first kept
as targets/old/T_PRO-NegHigh_rbf_*. Re-running build_targets.py rewrites the RBF cubes, so run
this script after it.

    .venv/Scripts/python scripts/pro_neg_high.py
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import least_squares

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_targets as bt  # noqa: E402

ROOT = bt.ROOT
OUT = bt.OUT
OLD = OUT / "old"
REPORT_MD = bt.REPORT / "reference-validation.md"
REPORT_JSON = bt.REPORT / "pro-neg-high.json"
SECTION = "## Pro Neg High, rebuilt"
KS = (4, 6, 8, 10)
N_FIT = 20000      # training pixels per final fit
N_CV = 5000        # training pixels per cross-validation fit (and per scored fold)
WORKERS = 8        # one process per (family, fit source) job
FILES = {"PROVIA": "Provia.jpg", "PNS": "Pro Neg Std.jpg", "PNH": "Pro Neg High.jpg"}

# ---------------------------------------------------------------- fast colour maths (fit loop only)
_CS = bt.colour.RGB_COLOURSPACES["sRGB"]
M_RGB2XYZ = np.asarray(_CS.matrix_RGB_to_XYZ)
M_XYZ2RGB = np.linalg.inv(M_RGB2XYZ)  # exact inverse, so an identity model round-trips
_x, _y = bt.D65
WHITE = np.array([_x / _y, 1.0, (1 - _x - _y) / _y])
EPS, KAPPA = 216 / 24389, 24389 / 27


def lin_to_lab(lin):
    xyz = np.clip(lin, 0, 1) @ M_RGB2XYZ.T / WHITE
    f = np.where(xyz > EPS, np.cbrt(xyz), (KAPPA * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def lab_to_lin(lab):
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    f = np.stack([fx, fy, fz], -1)
    xyz = np.where(f ** 3 > EPS, f ** 3, (116 * f - 16) / KAPPA) * WHITE
    return xyz @ M_XYZ2RGB.T


def L_to_Y(L):
    fy = (L + 16) / 116
    return np.where(fy ** 3 > EPS, fy ** 3, (116 * fy - 16) / KAPPA)


def de2000_fast(l1, l2):
    L1, a1, b1 = l1[..., 0], l1[..., 1], l1[..., 2]
    L2, a2, b2 = l2[..., 0], l2[..., 1], l2[..., 2]
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cb7 = ((C1 + C2) / 2) ** 7
    G = 0.5 * (1 - np.sqrt(Cb7 / (Cb7 + 25.0 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(C1p * C2p == 0, 0, dh)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    hs = h1p + h2p
    hbp = np.where(np.abs(h1p - h2p) > 180, np.where(hs < 360, (hs + 360) / 2, (hs - 360) / 2), hs / 2)
    hbp = np.where(C1p * C2p == 0, hs, hbp)
    T = (1 - 0.17 * np.cos(np.radians(hbp - 30)) + 0.24 * np.cos(np.radians(2 * hbp))
         + 0.32 * np.cos(np.radians(3 * hbp + 6)) - 0.20 * np.cos(np.radians(4 * hbp - 63)))
    dth = 30 * np.exp(-(((hbp - 275) / 25) ** 2))
    Cbp7 = Cbp ** 7
    RC = 2 * np.sqrt(Cbp7 / (Cbp7 + 25.0 ** 7))
    SL = 1 + 0.015 * (Lbp - 50) ** 2 / np.sqrt(20 + (Lbp - 50) ** 2)
    SC, SH = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    RT = -np.sin(np.radians(2 * dth)) * RC
    return np.sqrt((dLp / SL) ** 2 + (dCp / SC) ** 2 + (dHp / SH) ** 2 + RT * (dCp / SC) * (dHp / SH))


def feats(enc):
    lin = bt.srgb_decode(np.clip(enc, 0, 1))
    lab = lin_to_lab(lin)
    return {"enc": np.clip(enc, 0, 1), "lin": lin, "lab": lab}


# ---------------------------------------------------------------- monotone curves
def mono_values(theta, x0, x1, k):
    """K knot values: free start, then positive increments exp(theta). Identity at theta = init."""
    return theta[0] + np.concatenate([[0.0], np.cumsum(np.exp(theta[1:k]))])


def mono_init(x0, x1, k):
    return np.concatenate([[x0], np.full(k - 1, np.log((x1 - x0) / (k - 1)))])


def mono_eval(theta, x, x0, x1, k):
    knots = np.linspace(x0, x1, k)
    return PchipInterpolator(knots, mono_values(theta, x0, x1, k), extrapolate=True)(np.clip(x, x0, x1))


# ---------------------------------------------------------------- model families
class Model:
    """A low-dimensional colour model. apply(theta, f) maps feature dict f to sRGB code values."""

    def __init__(self, family: str, k: int):
        self.family, self.k = family, k
        tone = family.split("-")[-1] if "-" in family else None
        self.tone = tone
        parts = []
        if family.startswith(("A-", "B-", "Bh-")):
            parts.append(("tone", k))
        if family.startswith(("B-", "Bh-")):
            parts.append(("gain", 3))
        if family.startswith("Bh-"):
            parts.append(("hue", 4))
        if family == "C2":
            parts.append(("matrix", 9))
        if family in ("C1", "C2"):
            parts += [("curve_r", k), ("curve_g", k), ("curve_b", k)]
        self.parts = parts
        self.n = sum(p[1] for p in parts)

    def init(self):
        th = []
        for name, n in self.parts:
            if name == "tone":
                th.append(mono_init(0, 100, n))
            elif name.startswith("curve"):
                th.append(mono_init(0, 1, n))
            elif name == "matrix":
                th.append(np.eye(3).ravel())
            else:
                th.append(np.zeros(n))
        return np.concatenate(th)

    def split(self, theta):
        out, i = {}, 0
        for name, n in self.parts:
            out[name] = theta[i:i + n]
            i += n
        return out

    def penalty(self, theta):
        """Weak pull of every monotone curve towards identity (log-increment space)."""
        p, init = self.split(theta), self.split(self.init())
        return np.concatenate([p[nm][1:] - init[nm][1:] for nm, _ in self.parts
                               if nm == "tone" or nm.startswith("curve")] or [np.zeros(0)])

    def apply(self, theta, f):
        p = self.split(theta)
        if self.family in ("C1", "C2"):
            lin = f["lin"]
            if "matrix" in p:
                lin = lin @ p["matrix"].reshape(3, 3).T
            enc = bt.srgb_encode(np.clip(lin, 0, 1))
            out = np.stack([mono_eval(p[f"curve_{c}"], enc[..., i], 0, 1, self.k)
                            for i, c in enumerate("rgb")], -1)
            return np.clip(out, 0, 1)
        lab = f["lab"]
        L = lab[..., 0]
        Lp = np.clip(mono_eval(p["tone"], L, 0, 100, self.k), 0, 100)
        if self.tone == "lab":
            new = np.stack([Lp, lab[..., 1], lab[..., 2]], -1)
        else:  # scale linear RGB by the luminance ratio, then back to Lab for the chroma terms
            ratio = L_to_Y(Lp) / np.maximum(L_to_Y(L), 1e-6)
            new = lin_to_lab(np.clip(f["lin"] * ratio[..., None], 0, 1))
        if "gain" in p:
            u = L / 100 - 0.5
            c = p["gain"]
            g = np.exp(c[0] + c[1] * u + c[2] * u * u)
            if "hue" in p:
                h = np.arctan2(lab[..., 2], lab[..., 1])
                a = p["hue"]
                g = g * np.exp(a[0] * np.cos(h) + a[1] * np.sin(h) + a[2] * np.cos(2 * h) + a[3] * np.sin(2 * h))
            new = np.stack([new[..., 0], new[..., 1] * g, new[..., 2] * g], -1)
        return np.clip(bt.srgb_encode(np.clip(lab_to_lin(new), 0, 1)), 0, 1)


def fit(model: Model, f_in: dict, lab_target: np.ndarray, lam: float = 0.05):
    w = lam * np.sqrt(len(lab_target))

    def resid(theta):
        out = model.apply(theta, f_in)
        d = de2000_fast(lin_to_lab(bt.srgb_decode(out)), lab_target)
        return np.concatenate([d, w * model.penalty(theta)])

    r = least_squares(resid, model.init(), method="trf", x_scale="jac", ftol=1e-5, xtol=1e-5,
                      max_nfev=60)
    return r.x


def subset(f: dict, idx) -> dict:
    return {k: v[idx] for k, v in f.items()}


def fit_job(fam: str, X: np.ndarray, tgt: np.ndarray, quad: np.ndarray, seed: int):
    """One family on one fit source: K by 4-fold spatial CV, then the final fit. Runs in a worker."""
    rng = np.random.default_rng(seed)
    F = feats(X)
    cv = {}
    for k in KS:
        errs = []
        for q in range(4):
            tr = np.flatnonzero(quad != q)
            te = np.flatnonzero(quad == q)
            tr = rng.choice(tr, min(N_CV, len(tr)), replace=False)
            te = rng.choice(te, min(N_CV, len(te)), replace=False)
            m = Model(fam, k)
            th = fit(m, subset(F, tr), tgt[tr])
            d = de2000_fast(lin_to_lab(bt.srgb_decode(m.apply(th, subset(F, te)))), tgt[te])
            errs.append(float(d.mean()))
        cv[k] = round(float(np.mean(errs)), 3)
    k_best = min(cv, key=cv.get)
    m = Model(fam, k_best)
    sel = rng.choice(len(X), min(N_FIT, len(X)), replace=False)
    return cv, k_best, fit(m, subset(F, sel), tgt[sel])


# ---------------------------------------------------------------- evaluation (report method)
def evaluate(T65, prov_full, pnh_full, prov_small, pnh_lab_small, test_idx, patch_ref):
    p_t = bt.patch_labs(prov_full, T65)
    d_p = bt.de2000(p_t, patch_ref)
    pred = bt.apply_lut(T65, prov_small).reshape(-1, 3)[test_idx]
    d_i = bt.de2000(bt.lab(pred), pnh_lab_small.reshape(-1, 3)[test_idx])
    ps, im = bt.stats(d_p), bt.stats(d_i)
    return {"patch": ps, "image": {"mean": im["mean"], "p90": im["p90"]}}


def main() -> None:
    t0 = time.time()
    full = {k: bt.load_fuji(v) for k, v in FILES.items()}
    small = {k: cv2.resize(v, bt.SMALL, interpolation=cv2.INTER_AREA).astype(np.float64) / 255
             for k, v in full.items()}
    H, W = small["PROVIA"].shape[:2]
    pnh_lab_small = bt.lab(small["PNH"])
    patch_ref = bt.patch_labs(full["PNH"])

    # same split as build_targets.fit_pro_neg_high
    yy, xx = np.mgrid[0:H, 0:W]
    x0, y0, x1, y1 = bt.CHART_SMALL
    keep = np.flatnonzero(~((xx >= x0) & (xx < x1) & (yy >= y0) & (yy < y1)).ravel())
    rng = np.random.default_rng(bt.SEED)
    perm = rng.permutation(keep)
    half = len(perm) // 2
    train_idx, test_idx = perm[:half], perm[half:]
    quad = ((yy.ravel() >= H // 2).astype(int) * 2 + (xx.ravel() >= W // 2).astype(int))

    T_PNS = np.clip(np.load(OUT / "T_PRO-NegStd_65.npy").astype(np.float64), 0, 1)
    rbf_src = OLD / "T_PRO-NegHigh_rbf_65.npy"
    if not rbf_src.exists():
        rbf_src = OUT / "T_PRO-NegHigh_65.npy"
    T_RBF = np.load(rbf_src).astype(np.float64)
    bt.log(f"loaded frames, T_PNS and RBF ({rbf_src.relative_to(ROOT)})", t0)

    ev = lambda T: evaluate(T, full["PROVIA"], full["PNH"], small["PROVIA"], pnh_lab_small, test_idx, patch_ref)
    grid65 = bt.cube_points(65)
    identity = grid65.reshape(65, 65, 65, 3)
    results = {
        "do nothing (Provia)": {"base": "-", "source": "-", "k": None, "n_params": 0, **ev(identity)},
        "T_PNS alone": {"base": "PNS", "source": "-", "k": None, "n_params": 0, **ev(T_PNS)},
        "RBF (build_targets)": {"base": "Provia", "source": "Provia/High pair", "k": None, "n_params": 3000 * 3,
                                **ev(T_RBF)},
    }
    for nm, r in results.items():
        bt.log(f"{nm}: patch {r['patch']['mean']} image {r['image']['mean']}", t0)

    # inputs per fit source: (features of the model input, Lab target), over every pixel
    X_prov = small["PROVIA"].reshape(-1, 3)
    X_pns_t = bt.apply_lut(T_PNS, small["PROVIA"]).reshape(-1, 3)
    X_pns_g = small["PNS"].reshape(-1, 3)
    lab_tgt = lin_to_lab(bt.srgb_decode(small["PNH"].reshape(-1, 3)))
    sources = {  # name -> (input pixels, base applied at bake time, source label)
        "PNS": (X_pns_t, "PNS", "T_PNS(Provia) / High"),
        "Provia": (X_prov, "Provia", "Provia / High"),
        "PNS-genuine": (X_pns_g, "PNS", "genuine Std / High"),
    }
    families = ["A-lab", "A-rgb", "B-lab", "B-rgb", "Bh-lab", "Bh-rgb", "C1", "C2"]
    bake_in = {"PNS": feats(bt.apply_lut(T_PNS, grid65)), "Provia": feats(grid65)}
    fitted = {}
    jobs = {}
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        for si, (sname, (X, base, slabel)) in enumerate(sources.items()):
            for fi, fam in enumerate(families):
                jobs[(sname, fam)] = pool.submit(fit_job, fam, X[train_idx], lab_tgt[train_idx],
                                                 quad[train_idx], 100 * si + fi)
        for (sname, fam), fut in jobs.items():
            X, base, slabel = sources[sname]
            cv, k_best, th = fut.result()
            m = Model(fam, k_best)
            T65 = m.apply(th, bake_in[base]).reshape(65, 65, 65, 3)
            name = f"{fam} on {base}" + (" (fit on genuine Std)" if sname == "PNS-genuine" else "")
            results[name] = {"base": base, "source": slabel, "family": fam, "k": k_best, "n_params": m.n,
                             "cv_image_mean_by_k": cv, **ev(T65)}
            fitted[name] = (m, th, base)
            r = results[name]
            bt.log(f"{name}: K={k_best} cv={cv} patch {r['patch']['mean']}/{r['patch']['max']} "
                   f"image {r['image']['mean']}", t0)

    # ---- choice
    base_img = results["do nothing (Provia)"]["image"]["mean"]
    rbf_patch = results["RBF (build_targets)"]["patch"]["mean"]
    eligible = {n: r for n, r in results.items() if n in fitted and r["image"]["mean"] < base_img}
    chosen = min(eligible, key=lambda n: eligible[n]["patch"]["mean"]) if eligible else None
    replace = bool(chosen) and results[chosen]["patch"]["mean"] < rbf_patch
    bt.log(f"chosen {chosen}; replaces RBF: {replace}", t0)

    if replace:
        OLD.mkdir(exist_ok=True)
        for suffix in ("65.cube", "65.npy", "33.cube"):
            dst = OLD / f"T_PRO-NegHigh_rbf_{suffix}"
            if not dst.exists():
                shutil.copy2(OUT / f"T_PRO-NegHigh_{suffix}", dst)
        m, th, base = fitted[chosen]
        note = (f"Pro Neg High rebuilt by scripts/pro_neg_high.py: {chosen}, {m.n} parameters, fitted on "
                "non-chart pixels of one real pair. sRGB display in and out.")
        for n in bt.GRIDS:
            g = bt.cube_points(n)
            f_in = feats(bt.apply_lut(T_PNS, g)) if base == "PNS" else feats(g)
            T = m.apply(th, f_in).reshape(n, n, n, 3)
            bt.write_cube(OUT / f"T_PRO-NegHigh_{n}.cube", T, f"T_PRO-NegHigh {n}grid {chosen}", note)
            if n == 65:
                np.save(OUT / "T_PRO-NegHigh_65.npy", T.astype(np.float32))
        # check the written file scores what the in-memory model scored
        written = bt.read_cube(OUT / "T_PRO-NegHigh_65.cube")
        results[chosen]["written_cube_check"] = ev(written)
        t_u8 = np.clip(np.round(bt.apply_lut(written, small["PROVIA"]) * 255), 0, 255).astype(np.uint8)
        small_u8 = {k: cv2.resize(v, bt.SMALL, interpolation=cv2.INTER_AREA) for k, v in full.items()}
        bt.sheet(bt.VAL_DIR / "PRO-NegHigh.jpg", [small_u8["PROVIA"], t_u8, small_u8["PNH"]],
                 ["Provia (genuine)", "T_PRO-NegHigh(Provia)", "Pro Neg High (genuine)"])
        bt.log("wrote cubes, npy and validation sheet", t0)

    # ---- diagnostics: why the premise and the chroma terms fail on this pair
    keep_lab_p = bt.lab(small["PROVIA"]).reshape(-1, 3)[keep]
    keep_lab_h = pnh_lab_small.reshape(-1, 3)[keep]
    c_scene_p = np.hypot(keep_lab_p[:, 1], keep_lab_p[:, 2])
    c_scene_h = np.hypot(keep_lab_h[:, 1], keep_lab_h[:, 2])
    p_prov = bt.patch_labs(full["PROVIA"])
    c_pp, c_ph = np.hypot(p_prov[:, 1], p_prov[:, 2]), np.hypot(patch_ref[:, 1], patch_ref[:, 2])
    sat = c_pp > 50
    diag = {
        "patch_mean_genuine_std_vs_high": round(float(bt.de2000(bt.patch_labs(full["PNS"]), patch_ref).mean()), 3),
        "scene_chroma_provia_p99_9": round(float(np.percentile(c_scene_p, 99.9)), 1),
        "patch_chroma_provia_max": round(float(c_pp.max()), 1),
        "scene_median_chroma_ratio_high_over_provia_C_gt_5":
            round(float(np.median(c_scene_h[c_scene_p > 5] / c_scene_p[c_scene_p > 5])), 3),
        "patch_median_chroma_ratio_high_over_provia_C_gt_50": round(float(np.median(c_ph[sat] / c_pp[sat])), 3),
        "patches_with_C_gt_50": int(sat.sum()),
    }
    bt.log(f"diagnostics {diag}", t0)

    out = {"date": bt.date.today().isoformat(), "chosen": chosen, "replaced_rbf": replace, "diagnostics": diag,
           "rule": "best held-out patch mean among fitted candidates whose held-out image mean beats doing "
                   "nothing; written only if it also beats the RBF patch mean",
           "train_pixels": int(len(train_idx)), "held_out_pixels": int(len(test_idx)),
           "fit_sample": N_FIT, "cv_sample": N_CV, "ks": list(KS), "candidates": results}
    REPORT_JSON.write_text(json.dumps(out, indent=2), encoding="utf-8")
    write_section(out)
    bt.log("wrote report section and json", t0)


def write_section(out: dict) -> None:
    res = out["candidates"]
    lines = [SECTION, "",
             f"Generated {out['date']} by `scripts/pro_neg_high.py` (numbers also in `report/pro-neg-high.json`). "
             "The RBF above memorised the one photo it was fitted on. Here every candidate has few parameters and "
             "sits on the official PRO-Neg.Std transform (Fuji describes Pro Neg Hi as Pro Neg Std with more "
             "contrast), with the same families fitted on Provia directly as a control, and once more on the "
             "genuine Pro Neg Std / Pro Neg High pair so the model learns only the Std-to-High step.",
             "",
             "- Families: A = monotone tone curve on L* (K knots), applied in Lab keeping a*/b* (`-lab`) or by "
             "scaling linear RGB (`-rgb`); B = A plus a lightness-dependent chroma gain (3 parameters); Bh = B plus "
             "a 2-harmonic hue term on the gain (4 more); C1 = per-channel monotone curves (K knots each); "
             "C2 = 3x3 linear-light matrix then C1.",
             f"- Fitted on {out['fit_sample']} of the {out['train_pixels']} training pixels (non-chart, the same "
             "split as the RBF), minimising dE2000. K chosen from "
             f"{out['ks']} by 4-fold spatial cross-validation over the image quadrants.",
             f"- Scored through the baked 65 cube, as in the table above: patches are held out (never fitted); "
             f"image numbers are the {out['held_out_pixels']} held-out pixels.",
             "",
             "| Candidate | Fitted on | K | Params | CV image mean | Patch dE mean / p90 / max | Held-out image mean / p90 |",
             "|---|---|---|---|---|---|---|"]
    for name, r in res.items():
        cv = r.get("cv_image_mean_by_k")
        cvs = str(cv[r["k"]]) if cv else "-"
        p, i = r["patch"], r["image"]
        star = " **(chosen)**" if name == out["chosen"] else ""
        lines.append(f"| {name}{star} | {r['source']} | {r['k'] or '-'} | {r['n_params']} | {cvs} | "
                     f"{p['mean']} / {p['p90']} / {p['max']} | {i['mean']} / {i['p90']} |")
    lines.append("")
    dg = out["diagnostics"]
    pns = res["T_PNS alone"]
    dn0 = res["do nothing (Provia)"]
    lines += [
        "Findings:",
        "",
        f"- The premise does not hold for these reference frames. Genuine Pro Neg Std is {dg['patch_mean_genuine_std_vs_high']} "
        f"dE2000 from genuine Pro Neg High on the patches, farther than genuine Provia is ({dn0['patch']['mean']}); "
        f"T_PNS(Provia) alone scores {pns['patch']['mean']}. Building on T_PNS starts from behind, and no PNS-based "
        "candidate beats doing nothing on the patches. Fitting the Std-to-High step on the genuine Std / High pair "
        "and stacking it on T_PNS adds T_PNS's own error, so its held-out image numbers are the worst.",
        f"- The scene cannot teach chroma. Outside the chart, Provia chroma stays below C* {dg['scene_chroma_provia_p99_9']} "
        f"(99.9th percentile), while the patches reach {dg['patch_chroma_provia_max']}. In the scene Pro Neg High "
        f"has {dg['scene_median_chroma_ratio_high_over_provia_C_gt_5']} of Provia's chroma; on the "
        f"{dg['patches_with_C_gt_50']} saturated patches (C* above 50) the ratio is "
        f"{dg['patch_median_chroma_ratio_high_over_provia_C_gt_50']}. Every chroma term (B, Bh, C1, C2) is extrapolation: "
        "it lowers the image error, but against the tone-only fit on the same base it mostly raises the patch error (C2 on PNS is the one exception), worst for the hue harmonics. The largest remaining "
        "patch errors are these saturated patches, where Pro Neg High is less saturated than Provia.",
        ("- The winning model is a tone curve alone: it improves the dark end and the overall tone, and leaves "
         "the saturated-patch chroma at the do-nothing error. "
         if out["chosen"] and res[out["chosen"]].get("family", "").startswith("A-") else "- ")
        + "A better reference needs a second genuine pair with saturated content outside the chart, not a "
          "different model.",
        "",
    ]
    ch = out["chosen"]
    if ch is None:
        lines.append("No fitted candidate beat doing nothing on the held-out image; the RBF cubes stay in place.")
    elif out["replaced_rbf"]:
        r = res[ch]
        rb = res["RBF (build_targets)"]
        dn = res["do nothing (Provia)"]
        lines.append(
            f"Choice: **{ch}** ({r['n_params']} parameters). Rule: the best held-out patch mean among candidates "
            f"whose held-out image mean beats doing nothing. Patch mean {r['patch']['mean']} against "
            f"{rb['patch']['mean']} for the RBF and {dn['patch']['mean']} for doing nothing; held-out image mean "
            f"{r['image']['mean']} (RBF {rb['image']['mean']}, nothing {dn['image']['mean']}). It replaces the RBF "
            "in `targets/T_PRO-NegHigh_65.cube`, `_65.npy` and `_33.cube`; the RBF files are kept as "
            "`targets/old/T_PRO-NegHigh_rbf_*`, and `targets/validation/PRO-NegHigh.jpg` now shows this model.")
        if "written_cube_check" in r:
            w = r["written_cube_check"]
            lines.append(f"Written 65 cube re-read and re-scored: patch mean {w['patch']['mean']}, image mean "
                         f"{w['image']['mean']}.")
        lines.append("")
        lines.append("Caveats: the patches are held out of every fit, but choosing between families by patch "
                     "mean uses them once, so the chosen number is slightly optimistic. Everything rests on one "
                     "real pair, exported through Capture One (see the provenance caveat). The PRO-Neg.High row "
                     "in the results table above is the RBF; re-running `build_targets.py` writes the RBF cubes "
                     "back, so run `pro_neg_high.py` after it.")
    else:
        lines.append(f"Best candidate {ch} (patch mean {res[ch]['patch']['mean']}) does not beat the RBF patch "
                     f"mean {res['RBF (build_targets)']['patch']['mean']}; the RBF cubes stay in place.")
    lines.append("")
    text = REPORT_MD.read_text(encoding="utf-8")
    if SECTION in text:  # replace our own earlier section, keep anything after it
        pre, rest = text.split(SECTION, 1)
        nxt = rest.find("\n## ")
        post = rest[nxt + 1:] if nxt >= 0 else ""
        text = pre.rstrip("\n") + "\n\n" + "\n".join(lines) + ("\n" + post if post else "")
    else:
        text = text.rstrip("\n") + "\n\n" + "\n".join(lines)
    REPORT_MD.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
