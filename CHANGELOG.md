# Changelog

All notable changes to this project. Dates are in Singapore time.

## [Unreleased]

### Next
- Measure the Xiaomi 14T, Samsung Galaxy S24 Ultra and Huawei P30 Pro.
- Test the exact `.cube` tier on the phone itself (it is a laptop simulation so far).

## [0.1.0] - 2026-11-11

The POCO X6 Pro pilot.

### Added
- `pilot/`: the scripts exactly as they ran on 18–19 Oct 2026. They drive the phone over adb, shoot colour charts on
  a monitor (about 490 automated shots), read every patch through ArUco markers, fit the in-camera filters and
  sliders, verify the best candidates with real shots, and score them in ΔE2000 on a held-out chart page.
- `pilot/results/poco-x6-pro.json`: results for 11 Fujifilm looks (numbers only, no Fujifilm data). Acros
  goes from 25.6 doing nothing to 2.1 with one in-camera setting; the simulated exact `.cube` tier is
  0.18–0.39 for every look.
- An offline smoke test (7 checks, no phone, monitor or Fujifilm files) and CI on Ubuntu and Windows.
- `docs/results.svg`, drawn from the results file by `pilot/scripts/results_svg.py`.
- `docs/ARCHITECTURE.md`: the pipeline, the three tiers, what is built and what is planned, and the ideas
  borrowed from other projects with their licences.

## [0.0.1] - 2026-10-19

### Added
- README with the three tiers (native preset, exact `.cube`, optional root), the "bring your own Fujifilm LUTs"
  rule, the MIT licence and a `.gitignore` that keeps Fujifilm data and phone captures out of git.

[Unreleased]: https://github.com/Sohwhat6420/android-film-simulations/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Sohwhat6420/android-film-simulations/releases/tag/v0.1.0
[0.0.1]: https://github.com/Sohwhat6420/android-film-simulations/commit/d9f74c4
