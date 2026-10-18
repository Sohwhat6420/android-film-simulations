# Android Film Simulations

Fujifilm film simulation colours on stock Android phone cameras, **measured**, not eyeballed.

Every look is checked against Fujifilm's own data and reported as a ΔE2000 next to two
reference numbers: the error of doing nothing, and the measuring rig's own noise.

> **Status:** private, early. A full pilot runs on a POCO X6 Pro (Xiaomi HyperOS). Xiaomi 14T,
> Samsung Galaxy S24 Ultra and Huawei P30 Pro come next.

## What it does

The goal is a quick install that gives near-identical Fuji looks **in the phone's own camera**,
without changing anything else about the camera or phone. There are three tiers per phone:

| Tier | How the look is applied | Accuracy (POCO X6 Pro pilot) | Needs |
| --- | --- | --- | --- |
| **Native** | A camera preset: built-in filter plus sliders, fitted by measurement | approximate, about 2–3 ΔE for most looks | nothing |
| **Exact** | The stock Gallery editor's `.cube` import, one tap after the shot | about 0.35 ΔE, the same as the rig's noise (laptop simulation; phone test pending) | nothing |
| **Root** (optional) | Replace a built-in camera filter's lookup table with a Fuji one | exact, in the camera | a rooted phone |

## Fujifilm's data stays on your machine

Fujifilm publishes official film simulation LUTs for free, but its terms do not allow
redistribution. So this repository ships **only tools**:
1. You download Fujifilm's LUT pack yourself.
2. The scripts build the phone LUTs and presets on your machine.

No Fujifilm-derived data is committed here.

## How accuracy is measured

1. The phone shoots colour charts on a calibrated-gain monitor, with exposure, ISO, white
   balance and focus locked.
2. Every setting is bracketed by plain shots, so the monitor's slow drift cancels.
3. Effects are learnt on two chart pages and scored on a third page of colours the fit never saw.
4. The fitted model only ranks candidates. Every published number comes from real photos.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full pipeline.

## Licence

MIT, for the code in this repository. Fujifilm's LUTs remain under Fujifilm's own terms.
