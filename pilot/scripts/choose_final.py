"""Pick each look's final advice from REAL shots only, weighing test-chart colours and photos equally.

Options per look, each with a real chart score (all 405 colours) and a real photo score (six scene
pages, scene_check.py), averaged:
    the chart's best setting     verify1 (score_verify.py)  + scenes1 (score_scenes.py)
    leaving the camera alone     verify1 forced to Original 0 0, + the plain shots of scenes1
    a photo-aware setting        verify2 (scene_fit.py's pick, shot on chart and photos) + its scenes
verify2 ran in a later session, and the monitor and room drift between sessions (its own plain
shots scored 0.1-0.2 further from target than verify1's), so its scores are scaled by the ratio of
the two sessions' do-nothing scores before comparing. scenes1 shot each look's verify1 pick under
the target of that time; where the current pick differs (Eterna), --v1-shot gives the score of the
setting scenes1 really shot, so chart and photo numbers belong to one setting.
Writes report/poco-verify-final.json and report/poco-scenes-final.json in the same shapes as
verify1 / scenes1, so build_report.py reads them unchanged, and copies the chosen setting's scene
images into report/scenes/.

    python choose_final.py --v1 scratch/verify1-hull.json --v1-plain scratch/verify1-plain.json \
        --s1 report/poco-scenes1.json --v2 scratch/verify2.json --s2 scratch/scenes2.json --s2-sheets scratch/scenes2
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def scene_mean(per_page: dict, part: str) -> float | None:
    vals = [r[part]["mean"] for r in per_page.values()]
    return float(np.mean(vals)) if vals else None


def name(best: dict) -> str:
    if best["config"] == "plain":
        return "nothing"
    sign = lambda v: f"{v:+d}".replace("-", "−")  # noqa: E731  (a true minus, as the page prints)
    return f"{best['filter']} (Saturation {sign(best['Saturation'])}, Contrast {sign(best['Contrast'])})"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # the setting names carry a true minus sign
    ap = argparse.ArgumentParser()
    for k in ("--v1", "--v1-plain", "--v1-shot", "--s1", "--v2", "--s2", "--s2-sheets"):
        ap.add_argument(k, required=k in ("--v1", "--v1-plain", "--s1"))
    a = ap.parse_args()
    load = lambda p: json.loads(Path(p).read_text()) if p and Path(p).exists() else {}  # noqa: E731
    v1, v1p, s1, v2, s2 = load(a.v1), load(a.v1_plain), load(a.s1), load(a.v2), load(a.s2)
    v1["sims"] |= load(a.v1_shot).get("sims", {})
    final = {"noise_floor_neighbour_plain_dE": v1["noise_floor_neighbour_plain_dE"], "sims": {}}
    scenes, log = {}, {}
    for sim, e1 in v1["sims"].items():
        opts = []
        sc1 = s1.get(sim) or {}
        if e1["best"]["config"] != "plain":
            opts.append(("chart", e1, sc1, scene_mean(sc1, "look_vs_target"), ROOT / "report" / "scenes"))
        opts.append(("nothing", v1p["sims"][sim], sc1, scene_mean(sc1, "plain_vs_target"), ROOT / "report" / "scenes"))
        if sim in (v2.get("sims") or {}) and (s2.get(sim) or {}):
            e2 = json.loads(json.dumps(v2["sims"][sim]))
            k_chart = v1p["sims"][sim]["all_patches"]["mean"] / e2["do_nothing"]["mean"]
            k_photo = scene_mean(sc1, "plain_vs_target") / scene_mean(s2[sim], "plain_vs_target")
            e2["all_patches"] = dict(e2["all_patches"], mean=round(e2["all_patches"]["mean"] * k_chart, 2),
                                     session_scale=round(k_chart, 3))
            opts.append(("photo-aware", e2, s2[sim], scene_mean(s2[sim], "look_vs_target") * k_photo, Path(a.s2_sheets)))
        scored = [(o[1]["all_patches"]["mean"] if o[3] is None else (o[1]["all_patches"]["mean"] + o[3]) / 2, o)
                  for o in opts]
        log[sim] = {o[0]: {"setting": name(o[1]["best"]), "chart": o[1]["all_patches"]["mean"],
                           "photos": None if o[3] is None else round(o[3], 2), "combined": round(sc, 2)}
                    for sc, o in scored}
        _, (kind, entry, per_page, photos, sheets) = min(scored, key=lambda t: t[0])
        entry = json.loads(json.dumps(entry))
        if kind == "nothing" and e1["best"]["config"] != "plain":
            entry["best"] |= {"why": "photos", "chart_pick": name(e1["best"]),
                              "chart_pick_scenes": round(scene_mean(sc1, "look_vs_target"), 2),
                              "nothing_scenes": round(scene_mean(sc1, "plain_vs_target"), 2)}
        entry["chosen_by"] = kind
        final["sims"][sim] = entry
        scenes[sim] = per_page
        if kind == "photo-aware":  # its scene images replace the chart pick's
            for img in sheets.glob(f"{sim}_*.jpg"):
                shutil.copyfile(img, ROOT / "report" / "scenes" / img.name)
        print(f"{sim:<15} {kind:<12} {name(entry['best'])}   " +
              "  ".join(f"{k}: {v['combined']}" for k, v in log[sim].items()))
    final["choice"] = log
    (ROOT / "report" / "poco-verify-final.json").write_text(json.dumps(final, indent=1))
    (ROOT / "report" / "poco-scenes-final.json").write_text(json.dumps(scenes, indent=1))


if __name__ == "__main__":
    main()
