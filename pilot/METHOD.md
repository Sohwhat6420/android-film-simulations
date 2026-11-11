# The method: measured Fujifilm looks on any phone

This is the procedure proven on the POCO X6 Pro (18 Oct 2026), written so it can be repeated on
the Xiaomi 14T, the Galaxy S24 Ultra and the Huawei P30 Pro. Every number a phone gets comes from
photos that phone took; nothing is copied from the internet, and nothing is judged by eye alone.

## 1. What "accurate" means here
- **The target is Fujifilm's own transform**, built from Fujifilm's official F-Log2 LUTs:
  `T_sim = LUT_sim ∘ inverse(LUT_PROVIA)`, sRGB in and out (`scripts/build_targets.py`).
  - It's checked against genuine Fujifilm JPEGs of a ColorChecker scene (`report/reference-validation.md`).
  - Mean ΔE2000 is 1.1–1.3 for Astia, Classic Chrome, Eterna, Pro Neg Std and Velvia. About 1 is the edge of what the eye can see.
  - Pro Neg Hi has no official LUT and is fitted separately.
  - Acros, Classic Neg, Eterna Bleach Bypass and Reala Ace have no genuine reference to check against.
- **The phone is judged against its own photo.** For any plain (neutral) shot, the Fuji target is
  `plain + (T_sim(plain) − T_PROVIA(plain))` in CIELAB.
  - So Provia is exactly "do nothing".
  - The phone's wide-gamut (Display P3) colours are never clipped by the sRGB-only Fuji LUTs.
- **The score is ΔE2000** (CIE's colour-difference formula) between the phone's real shot with the preset and that target. The report always shows:
  - mean, 90th percentile and maximum;
  - "do nothing" (plain vs target), so the gain is visible;
  - the shot-to-shot noise floor, so a score is never read as more precise than the rig allows.

## 2. The rig
- Phone on a stand 30–50 cm from the laptop monitor, plugged in, screen kept awake.
- **Room lights off**:
  - Turning the ceiling light off moved the measured greys by about 4–5 ΔE.
  - A dark room removes reflections from the screen.
  - It must not change during a run.
- Monitor (PRISM+ PG270 PRO, IPS):
  - Night light off, HDR off, brightness fixed;
  - the chart is shown full-screen by `scripts/viewer.py`, which polls `scratch/viewer/show.txt` and keeps the display awake.
- Camera locked in its manual mode:
  - ISO 50, shutter 1/50 s, white balance 6500K, manual focus;
  - focus set slightly soft on purpose, which removes moiré from the screen's pixel grid while the markers stay readable;
  - colour sliders at 0, filter "Original".
- **White-balance the chart, not the camera.**
  - The POCO has no tint control, so the chart is drawn with per-channel gains (`charts.py --gain`) until the phone sees its greys as grey.
  - Iterate: the phone's processing amplifies a change by about 2×, so take half-steps.
  - In the dark room: gains R 0.752, G 0.899, B 1.000.

## 3. Drift: measured, then designed out
- The monitor's colour drifts slowly: about 0.07 ΔE a minute during the evening of 18 Oct (`scripts/drift_log.py`). It moved 3.4 ΔE over one 30-minute sweep.
- So every control setting is **bracketed**: plain, setting, plain, setting, …, plain.
  - Each setting's effect is learnt against the mean of the two plain shots either side of it, a minute or two apart. Neighbouring plain shots differ by about 0.13 ΔE.
  - Drift then moves both sides of the comparison together and cancels.

## 4. Charts
- Shown at 0.56 scale on the right of the screen (where the stand's view falls), each with four ArUco markers.
  - `measure.py` finds the markers, un-warps the photo, and averages the middle half of every patch in linear light.
- `dense_a` + `dense_b` (270 training colours):
  - a 6×6×6 cube of sRGB values (even coverage);
  - an 18-step grey ramp;
  - the 24 ColorChecker colours;
  - 12 skin tones.
  - Shuffled, so no colour family sits in one corner.
- `dense_v` (135 held-out colours): Sobol quasi-random colours, greys between the training steps, and shifted skin tones. **No setting is ever fitted on this page.** It exists to test the fit.
- `chart60` plus `surround_dark` / `surround_light`: the same 60 patches on different surrounds. They test whether the phone's local tone mapping changes a colour depending on its neighbours.
- `scene_*` (`scene_pages.py`): natural photos with the same markers, so a plain shot and a preset shot can be rectified and compared pixel by pixel.

## 5. From measurements to a preset
1. **Sweep** (`poco_sweep.py --plan dense1`): every control setting on its own, bracketed, on all three dense pages.
2. **Model** (`fit2.py`):
   - Each setting's effect is a smooth map in CIELAB (thin-plate radial basis functions plus a linear part), fitted on the training pages and scored on `dense_v`.
   - The model ranks every combination (on the POCO: 15 filters × Saturation × Contrast), applying the filter first, then the sliders.
3. **Verify** (`poco_sweep.py --verify report/poco-fit2.json`): the best 2 candidates per look are shot for real, bracketed. **Only these real shots are scored** (`score_verify.py`), and the model's own prediction is never reported as a result.
4. **Natural photos** (`--best report/poco-verify1.json --pages scene_*`, then `score_scenes.py`): each look's winning setting on six photos, scored per pixel.
5. **Sheets**:
   - `report/sheets/<look>.png`: every held-out colour as a split square, phone left and Fuji right, plus the 24 worst.
   - `report/scenes/<look>_<scene>.jpg`: plain | phone look | Fuji target.

## 6. Per-device adapters
The measurement, fitting and scoring code is device-independent. A device needs only:
- a driver that can set its colour controls and press the shutter over ADB (`poco_sweep.Phone` is the POCO's);
- the list of controls to sweep.

| Device | Native colour levers (what gets fitted) | Can a preset carry them? | Route |
|---|---|---|---|
| POCO X6 Pro (pilot) | 14 Pro filters; Saturation and Contrast −3..+3 | No. Its presets store exposure only (`FORMAT.md`) | Fit; the user picks the filter and sets the sliders by hand |
| Xiaomi 14T | Leica Authentic/Vibrant; tone, cold/warm, tint, vibrance, texture −50..+50, step 1; Pro filters (Leica NAT/VIV/BW/Sepia/Blue, Film, Classic) | Style and all five sliders: yes. **The filter only from camera 6.4**: its `generateLCDetail` adds the filter component, likely as `p_pref_camera_shader_coloreffect_key_167_<value>`. 6.1 and 6.2 drop it. **Read the 14T's camera version first (App info)**; one real export settles the key | **Fit, then ship as importable preset zips**, the best case. On 6.1 or 6.2, fit the sliders with filter = none, or name the filter in the preset's title for the user to set |
| Galaxy S24 Ultra | Pro "Colour tone": temperature, tint, contrast, saturation, highlights, shadows; 10 filters, each with strength, temperature, contrast, saturation; Custom filter learnt from a photo | Custom filters persist (up to 99); nothing can be imported | Fit Colour tone + filters. **Then test feeding the Custom-filter learner a measured Fuji-rendered reference**. Exact route: Blackmagic Camera with the `.cube` (video) |
| Huawei P30 Pro | Standard/Vivid/Smooth colour; built-in filters; no Pro colour sliders | No | Too few levers for a fit. Shoot neutral, then apply the exact `.cube` (Lightroom profile or desktop) |

Device research with sources: `research/s24-ultra.md`, `research/p30-pro.md`, `research/xiaomi-14t.md`.

## 7. Before trusting a run
- Plain neighbours ≤ about 0.3 ΔE (otherwise something moved).
- Held-out model error for every control is reported. A control with a large error makes the ranking for looks that use it unreliable, so shoot more candidates for those looks.
- Provia's "do nothing" must be exactly 0.
- Look at every sheet before quoting a number.
