"""Score each look on natural photos shown on the monitor (a sweep made with --best ... --pages scene_*).

For every look, its verified best setting was shot on each scene page right after a plain shot of
the same page. Both shots are rectified from the page's markers, and the setting's shot is compared
pixel by pixel with the Fuji target made from the plain shot (scene_check.check). Writes a JSON
table and a three-panel sheet per look and scene: plain | phone look | Fuji target.

    python score_scenes.py --sweep captures/poco/scenes1 --best report/poco-verify1.json --out report/poco-scenes1.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fit import sims  # noqa: E402
from scene_check import check  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--best", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sheets", default=str(ROOT / "report" / "scenes"))
    a = ap.parse_args()
    sweep = Path(a.sweep)
    rows = [json.loads(l) for l in (sweep / "log.jsonl").read_text().splitlines()]
    last_plain: dict[str, Path] = {}
    shot: dict[tuple, tuple[Path, Path]] = {}
    for r in rows:
        if not r["page"].startswith("scene_"):  # a sweep may hold chart pages too (verify2)
            continue
        p = sweep / f"{r['tag']}.jpg"
        if r["config"].startswith("base"):
            last_plain[r["page"]] = p
        elif r["page"] in last_plain:
            shot[(r["filter"], r["Saturation"], r["Contrast"], r["page"])] = (last_plain[r["page"]], p)
    best = json.loads(Path(a.best).read_text())["sims"]
    cubes = sims()
    out = {}
    for sim, v in best.items():
        b = v["best"]
        if b["config"] == "plain":
            continue
        out[sim] = {}
        for (f, s, c, page), (plain, look) in shot.items():
            if (f, s, c) != (b["filter"], b["Saturation"], b["Contrast"]):
                continue
            sheet = Path(a.sheets) / f"{sim}_{page.removeprefix('scene_')}.jpg"
            out[sim][page] = check(plain, look, cubes[sim], sheet, page, cubes["PROVIA"])
        if out[sim]:
            m = np.mean([x["look_vs_target"]["mean"] for x in out[sim].values()])
            n = np.mean([x["plain_vs_target"]["mean"] for x in out[sim].values()])
            print(f"{sim:<15} scenes {len(out[sim])}  look vs target {m:5.2f}  doing nothing {n:5.2f}")
    Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
