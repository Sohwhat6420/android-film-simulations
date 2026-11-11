# Xiaomi Camera Pro-mode preset zips: what is confirmed

Confirmed three ways:
- **Official zips:** Xiaomi's own exported presets, e.g. `Manual_official_3_Documentary.zip`, 16 of them listed.
- **v6.4 smali dump:** the public dump of the Leica camera (`Fleur-Project/MiuiCamera-smali`).
- **Our decompile:** the POCO X6 Pro's own camera, Xiaomi Camera **6.1.000360.0**, decompiled with jadx.

The import is also proven on a real phone: on 18 Oct 2026 the POCO imported two zips made by `scripts/make_preset.py`.

## The file
- A **flat zip of empty (0-byte) files**. Each file's *name* carries one setting: `p_<preference key>_<value>`.
  - The camera splits key from value at the **last** underscore, so a value can never contain `_`.
  - An invalid value falls back to that setting's default.
- Three more empty entries:
  - `t`: its timestamp is the creation time;
  - `v_3`: format version 3;
  - `ac`.
- **Name:** `Manual_<Build.DEVICE>_<epoch milliseconds>_<display name>.zip`.
  - `<Build.DEVICE>` is `degas` on the 14T and `duchamp` on the POCO.
  - The display name is ≤ 28 characters, with no underscore.
- **Import limits:**
  - ≤ 5120 bytes per zip;
  - ≤ 50 imported presets;
  - no duplicate names and no identical parameter sets;
  - only the `.zip` type is offered.
- `p_pref_ultra_pixel_<mode>_ON` crashed one user's import, so our zips always say `_OFF`.

## What a preset actually stores: it depends on the camera
`ManualWorkspaceItem.declareComponentList()` decides which settings a preset records and applies. It has two branches.

| | Leica-branded camera (Xiaomi 14T and up) | Non-Leica camera (POCO X6 Pro) |
|---|---|---|
| Exposure: metering, shutter, ISO, EV, focus, lens, zoom, WB | yes | yes |
| Leica style (`cv_type`: Authentic / Vibrant) | yes | no |
| Contrast `style_tone` −50..50 | yes | no |
| Cold/warm `style_color_temp` −50..50 | yes | no |
| Tint `style_color_tone` −50..50 | yes | no |
| Saturation `style_vibrance` −50..50 | yes, on cameras that have the slider | no |
| Sharpness `style_texture` −50..50 | yes | no |
| Pro **filter** (`pref_camera_shader_coloreffect_key`) | **not in this camera build** | no |
| POCO's Saturation / Contrast / Sharpness (−3..+3) | n/a | **no** |

**Tested on the POCO, 18 Oct 2026:**
- "Pilot Test A" carried the Leica style keys, and "Pilot Test B" carried the POCO's own keys (`p_pref_qc_camera_saturation_key_5`, etc.).
- Both imported cleanly, and **neither changed any colour setting**. That matches the code: the POCO's presets keep exposure only.

**What this means:**
- **On the POCO**, colour looks can't travel inside a preset. A look is "pick this filter, set these two sliders", done by hand. The live controls are measured exactly the same way, so the pilot still proves the method.
- **On the 14T**, presets do carry the Leica style and all five colour sliders, which is the point of the project.
  - In this camera build no preset stores a Pro filter. Where a look needs a filter, the user picks the filter once (it stays selected) and the preset carries the rest.
  - **Still to check on the 14T's own build** (Phase 1 recon): its camera is newer than the POCO's 6.1, so whether its presets save the filter is checked there.

## Where to look in the code (6.1 build)
- `jadx2/.../mm/manually/ManualWorkspaceItem.java`: `declareComponentList` (the two branches above).
- `jadx2/.../mm/manually/ManualWorkspace.java`: the `Manual` prefix, `VERSION_3`, and the official presets.
- `jadx1/sources/d1/b2.java`, `k1.java`, `c2.java`: the POCO's Saturation, Contrast and Sharpness controls.
  - Stored values are "0".."6", default "3"; the screen shows −3..+3.
- `jadx1/sources/d1/{i2,d1,f1,k2,g2}.java`: the Leica style sliders (tone, temp, tint, vibrance, texture).
