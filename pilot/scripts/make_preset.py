"""Build a Xiaomi Camera Pro-mode preset zip ("Manual" workspace item).

Format (confirmed from official Xiaomi zips and the decompiled camera, see FORMAT.md):
a flat zip of EMPTY files whose names carry the settings, `p_<prefKey>_<value>`,
plus `t` (its mtime is the creation time), `v_3` (format version) and `ac`.
The zip itself is named `Manual_<Build.DEVICE>_<epoch-ms>_<display name>.zip`.

Usage:
    python make_preset.py --device duchamp --name "Fuji Classic Chrome" \
        --tone 10 --vibrance -20 --texture 0 --temp -5 --tint -10 --out presets/
"""
from __future__ import annotations

import argparse
import time
import zipfile
from pathlib import Path

SLIDER_RANGE = (-50, 50)  # style_* sliders, integers (official presets use exactly -50 and 50)


def preset_entries(
    *,
    tone: int = 0,
    vibrance: int = 0,
    texture: int = 0,
    temp: int | None = None,
    tint: int | None = None,
    ev: float = 0.0,
    exposure_ns: int = 0,
    iso: int = 0,
    wb: int = 1,
    focus: int = 1000,
    zoom: str = "1.0",
    cv_type: int = 1,
    filter_id: int | None = None,
    filter_degree: int | None = None,
    steps: dict[str, int] | None = None,
) -> list[str]:
    """Return the entry names in the same order the official zips use.

    exposure_ns / iso = 0 means Auto; wb = 1 means Auto, otherwise Kelvin; focus 1000 = AF.
    temp / tint are left out when None (the POCO's camera has no such sliders).
    """
    for label, v in (("tone", tone), ("vibrance", vibrance), ("texture", texture), ("temp", temp), ("tint", tint)):
        if v is not None and not (SLIDER_RANGE[0] <= v <= SLIDER_RANGE[1]):
            raise ValueError(f"{label}={v} is outside {SLIDER_RANGE}")
    ev_text = "0" if ev == 0 else f"{ev:.1f}"
    names = [
        "p_pref_camera_cv_type_key_%d" % cv_type,
        "p_pref_camera_autoexposure_key_1",
        "p_pref_qc_camera_exposuretime_key_%d" % exposure_ns,
        "p_pref_qc_camera_iso_key_%d" % iso,
        "p_pref_qc_camera_pro_exposure_value_key_%s" % ev_text,
        "p_pref_camera_manually_lens_wide",
        "p_pref_camera_zoom_retain_key_%s" % zoom,
        "p_pref_camera_whitebalance_key_new_%d" % wb,
        "p_pref_focus_position_key_%d" % focus,
        "p_pref_qc_camera_style_tone_key_%d" % tone,
    ]
    if temp is not None:
        names.append("p_pref_qc_camera_style_color_temp_key_%d" % temp)
    if tint is not None:
        names.append("p_pref_qc_camera_style_color_tone_key_%d" % tint)
    names += [
        "p_pref_qc_camera_style_vibrance_key_%d" % vibrance,
        "p_pref_qc_camera_style_texture_key_%d" % texture,
    ]
    # Cameras without the Leica "style" sliders (e.g. the POCO X6 Pro's 6.1 build) have three
    # 7-step Pro controls instead: stored value 0..6 means -3..+3, default 3.
    for control, step in (steps or {}).items():
        if control not in ("saturation", "contrast", "sharpness") or not 0 <= step <= 6:
            raise ValueError(f"{control}={step}")
        names.append(f"p_pref_qc_camera_{control}_key_{step}")
    if filter_id is not None:
        names.append("p_pref_camera_shader_coloreffect_key_%d" % filter_id)
        names.append("p_pref_camera_shader_coloreffect_degree_key_%d" % (-1 if filter_degree is None else filter_degree))
    names += ["p_pref_camera_raw_key_JPEG", "p_pref_ultra_pixel_167_OFF", "t", "v_3", "ac"]
    for n in names:
        # The parser splits key from value at the LAST underscore, so a value must not contain one.
        if n.startswith("p_") and "_" in n.rsplit("_", 1)[1]:
            raise ValueError(n)
    return names


def write_preset(out_dir: Path, device: str, name: str, entries: list[str], created_ms: int | None = None) -> Path:
    if "_" in name or len(name) > 28:
        raise ValueError("display name must be at most 28 characters with no underscore: %r" % name)
    created_ms = created_ms or int(time.time() * 1000)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"Manual_{device}_{created_ms}_{name}.zip"
    stamp = time.localtime(created_ms / 1000)[:6]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in entries:
            info = zipfile.ZipInfo(entry, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0  # MS-DOS, as in the official zips
            zf.writestr(info, b"")
    if path.stat().st_size > 5120:  # the camera refuses imports over 5120 bytes
        raise ValueError(f"{path.name} is {path.stat().st_size} bytes; the camera rejects > 5120")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", required=True, help="Build.DEVICE: degas (14T) or duchamp (POCO X6 Pro)")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", default="presets")
    for s in ("tone", "vibrance", "texture"):
        ap.add_argument(f"--{s}", type=int, default=0)
    ap.add_argument("--temp", type=int)
    ap.add_argument("--tint", type=int)
    ap.add_argument("--ev", type=float, default=0.0)
    ap.add_argument("--exposure-ns", type=int, default=0)
    ap.add_argument("--iso", type=int, default=0)
    ap.add_argument("--wb", type=int, default=1)
    ap.add_argument("--focus", type=int, default=1000)
    ap.add_argument("--filter-id", type=int)
    ap.add_argument("--filter-degree", type=int)
    for c in ("saturation", "contrast", "sharpness"):
        ap.add_argument(f"--{c}-step", type=int, help="7-step Pro control (0..6 = -3..+3), POCO-style cameras")
    a = ap.parse_args()
    steps = {c: getattr(a, f"{c}_step") for c in ("saturation", "contrast", "sharpness") if getattr(a, f"{c}_step") is not None}
    entries = preset_entries(
        tone=a.tone, vibrance=a.vibrance, texture=a.texture, temp=a.temp, tint=a.tint, ev=a.ev,
        exposure_ns=a.exposure_ns, iso=a.iso, wb=a.wb, focus=a.focus,
        filter_id=a.filter_id, filter_degree=a.filter_degree, steps=steps,
    )
    print(write_preset(Path(a.out), a.device, a.name, entries))


if __name__ == "__main__":
    main()
