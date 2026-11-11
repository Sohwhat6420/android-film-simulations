#!/usr/bin/env bash
# Every POCO stage after the dense sweep, back to back (Git Bash, from the project root):
#   fit2 -> verify winners -> score -> surround test -> exposure test -> natural photos -> score -> robustness
# Each stage resumes from its own log.jsonl, so re-running after a failure continues where it stopped.
set -euo pipefail
export MSYS_NO_PATHCONV=1
PY=.venv/Scripts/python
SERIAL="${ADB_SERIAL:?set ADB_SERIAL to your phone (adb devices)}"
quiet() { grep --line-buffered -v -i "warn\|installation" || true; }

echo "== dense1"; $PY -u scripts/poco_sweep.py --serial "$SERIAL" --plan dense1 --out captures/poco/dense1 2>&1 | quiet
echo "== fit2"; $PY -u scripts/fit2.py --sweep captures/poco/dense1 --out report/poco-fit2.json 2>&1 | quiet
echo "== verify1"; $PY -u scripts/poco_sweep.py --serial "$SERIAL" --verify report/poco-fit2.json --per-sim 2 --out captures/poco/verify1 2>&1 | quiet
echo "== score verify1"; $PY -u scripts/score_verify.py --sweep captures/poco/verify1 --out report/poco-verify1.json 2>&1 | quiet
echo "== surround1"; $PY -u scripts/poco_sweep.py --serial "$SERIAL" --plan surround1 --out captures/poco/surround1 2>&1 | quiet
echo "== exposure1"; $PY -u scripts/poco_sweep.py --serial "$SERIAL" --plan exposure1 --out captures/poco/exposure1 2>&1 | quiet
echo "== scenes1"; $PY -u scripts/poco_sweep.py --serial "$SERIAL" --best report/poco-verify1.json \
    --pages scene_lake-blossom scene_forest-water scene_sunset scene_glossy scene_pastel scene_fuji-provia-scene \
    --out captures/poco/scenes1 2>&1 | quiet
echo "== score scenes1"; $PY -u scripts/score_scenes.py --sweep captures/poco/scenes1 --best report/poco-verify1.json --out report/poco-scenes1.json 2>&1 | quiet
echo "== robustness"; $PY -u scripts/robustness.py --surround captures/poco/surround1 --exposure captures/poco/exposure1 --dense captures/poco/dense1 2>&1 | quiet
printf '%s' "$(pwd -W)/charts/chart60_s0.56_x0.60_y0.50_g0.752-0.899-1.000.png" | sed 's#/#\\#g' > scratch/viewer/show.txt
echo "== chain done"
