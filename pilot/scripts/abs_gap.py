"""Estimate the gap every target here assumes away: the phone's plain photo versus REAL Provia.

All targets are relative (T_sim o T_PROVIA^-1 applied to the phone's own plain shot), so Provia
scores 0 by definition. This puts a number on what that hides, from data already on disk:

- scene: the ColorChecker's reference Lab (bastibe's `color checker.cht`);
- real Provia: the 24 patches of bastibe's genuine Fujifilm Provia JPEG of that chart;
- the phone: its plain response to displayed colours, learnt from the dense1 plain shots
  (displayed sRGB -> measured Lab, RBF), then asked for the ColorChecker's colours.

Exposure is matched on the four middle greys (one scale on Y), because the Fuji frame and the
locked rig expose differently. ASSUMES the monitor shows sRGB accurately once neutralised: with no
colorimeter that is unverified, so the result is an estimate, not a measurement.

    python abs_gap.py [--out report/abs-gap.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import colour
import numpy as np
from scipy.interpolate import RBFInterpolator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_targets as bt  # noqa: E402
from fit import D65, de  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
D50 = colour.CCS_ILLUMINANTS["CIE 1931 2 Degree Standard Observer"]["D50"]


# PG270 PRO primaries from its EDID (WmiMonitorColorCharacteristics, 1/256 steps), read 19 Oct.
# A wide-gamut panel: red is P3 red, green sits between sRGB and P3. sRGB codes reach it unmanaged.
PANELS = {"sRGB": None,
          "PG270 PRO (EDID)": np.array([[174, 82], [73, 163], [34, 19]]) / 256}


def emitted_lab(codes: np.ndarray, primaries) -> np.ndarray:
    """Lab of what the monitor emits for 0..1 codes (sRGB curve, white D65, given primaries)."""
    if primaries is None:
        return colour.XYZ_to_Lab(colour.sRGB_to_XYZ(codes), D65)
    M = colour.normalised_primary_matrix(primaries, D65)
    return colour.XYZ_to_Lab(colour.cctf_decoding(codes, function="sRGB") @ M.T, D65)


def phone_plain_model(primaries=None):
    """What the monitor emits (Lab) -> the phone's plain Lab, from every plain shot of the dense pages."""
    X, Y = [], []
    for page in ("dense_a", "dense_b", "dense_v"):
        shown = {p["name"]: p["srgb"] for p in json.loads((ROOT / f"charts/{page}.json").read_text())["patches"]}
        for f in sorted((ROOT / "captures/poco/dense1").glob(f"base*@{page}.json")):
            m = json.loads(f.read_text())
            for name, lab in zip(m["names"], m["Lab"]):
                X.append(np.array(shown[name], float) / 255)
                Y.append(lab)
    X, Y = np.array(X), np.array(Y)
    Xl = emitted_lab(X, primaries)
    return RBFInterpolator(Xl, Y, kernel="thin_plate_spline", smoothing=len(X) * 0.05), len(X)


def match_exposure(lab, ref, greys):
    """Scale lab's luminance (on XYZ) so its greys' Y matches ref's (geometric mean of ratios)."""
    xyz, xyz_r = colour.Lab_to_XYZ(lab, D65), colour.Lab_to_XYZ(ref, D65)
    k = float(np.exp(np.mean(np.log(xyz_r[greys, 1] / xyz[greys, 1]))))
    return colour.XYZ_to_Lab(xyz * k, D65), k


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "report/abs-gap.json"))
    a = ap.parse_args()

    names_raw, exp_lab = bt.parse_cht()
    provia = bt.patch_labs(bt.load_fuji("Provia.jpg"))
    orients = [lambda v: v, lambda v: v[::-1, ::-1], lambda v: v[::-1, :], lambda v: v[:, ::-1]]
    fn = min(orients, key=lambda f: float(bt.de2000(provia, f(exp_lab).reshape(24, 3)).mean()))
    names, scene50 = fn(names_raw).ravel(), fn(exp_lab).reshape(24, 3)
    # reference Lab is D50; adapt to D65 to show it on a D65 display
    scene = colour.XYZ_to_Lab(colour.chromatic_adaptation(colour.Lab_to_XYZ(scene50, D50),
                              colour.xy_to_XYZ(D50), colour.xy_to_XYZ(D65), "Von Kries", transform="Bradford"), D65)

    def stats(d):
        return {"mean": round(float(d.mean()), 2), "p90": round(float(np.percentile(d, 90)), 2),
                "max": round(float(d.max()), 2)}

    order = {nm: i for i, nm in enumerate(names)}
    greys = [order[k] for k in ("D2", "D3", "D4", "D5")]
    scene_m, k_scene = match_exposure(scene, provia, greys)
    out = {"note": "ESTIMATE: the monitor is modelled, not measured (no colorimeter).",
           "scene_vs_real_provia": stats(de(scene_m, provia)), "panels": {}}
    for panel, prim in PANELS.items():
        model, n = phone_plain_model(prim)
        phone = model(scene)            # the phone's plain photo of the chart, emitted exactly
        phone_m, k_phone = match_exposure(phone, provia, greys)
        d_pp = de(phone_m, provia)
        out["panels"][panel] = {
            "plain_samples": n, "exposure_scale_phone": round(k_phone, 3),
            "phone_plain_vs_real_provia": stats(d_pp),
            "phone_plain_vs_scene": stats(de(phone_m, scene_m)),
            "dL_mean": round(float((phone_m[:, 0] - provia[:, 0]).mean()), 2),
            "dC_mean": round(float((np.hypot(*phone_m[:, 1:].T) - np.hypot(*provia[:, 1:].T)).mean()), 2),
            "per_patch": {nm: round(float(d_pp[i]), 2) for i, nm in enumerate(names)}}
    Path(a.out).write_text(json.dumps(out, indent=1))
    for v in out["panels"].values():
        v = dict(v); v.pop("per_patch")
        print(json.dumps(v))
    print("scene vs real Provia", out["scene_vs_real_provia"])


if __name__ == "__main__":
    main()
