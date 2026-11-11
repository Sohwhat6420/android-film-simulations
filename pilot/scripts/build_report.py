"""Build the pilot report page (report/site/index.html) from the measured results.

Reads, when present:
    report/poco-verify1.json        best real setting per look, scores, swatch colours (score_verify.py)
    report/poco-scenes1.json        per-scene scores (score_scenes.py) + report/scenes/*_{look,fuji}.jpg
    report/reference-validation.json and report/pro-neg-high.json   how trustworthy each Fuji target is
If the verification sweep has not run yet, the swatches fall back to the plain shots of dense1 against
their Fuji targets, and every look is marked "not measured yet", so the page never shows a guess as a result.

    python build_report.py            # writes report/site/index.html and report/site/scenes/*.jpg
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fit import de, load_cube, sims, stats  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "report"
SITE = REPORT / "site"

# Fujifilm's own menu order, with a plain description of each look (our words, not Fujifilm's)
LOOKS = [
    ("PROVIA", "Provia", "Standard", "Fujifilm's everyday look: natural colour, moderate contrast. Everything else is measured as a change from it."),
    ("Velvia", "Velvia", "Vivid", "Slide film for landscapes: dense, saturated colour, deep blues and greens."),
    ("ASTIA", "Astia", "Soft", "Slide film with gentler contrast. Keeps colour rich but treats skin kindly."),
    ("CLASSIC-CHROME", "Classic Chrome", "", "Documentary look: muted colour, firm shadows, quieter blues and reds."),
    ("REALA-ACE", "Reala Ace", "", "Faithful colour with crisper tone than Provia."),
    ("PRO-NegHigh", "Pro Neg. Hi", "", "Portrait negative film with extra contrast for flat light."),
    ("PRO-NegStd", "Pro Neg. Std", "", "Portrait negative film: soft tone and natural skin, for even light."),
    ("CLASSIC-Neg", "Classic Neg.", "", "Snapshot colour-negative film: hard tone and shifted, nostalgic hues."),
    ("ETERNA", "Eterna", "Cinema", "Motion-picture film: low saturation and soft, open shadows."),
    ("ETERNA-BB", "Eterna Bleach Bypass", "", "Cinema bleach bypass: very low saturation with hard contrast."),
    ("ACROS", "Acros", "", "Fujifilm's fine-grained black and white, with rich tonal steps."),
]
SCENE_NAMES = {"lake-blossom": "Lake and blossom", "forest-water": "Forest stream", "sunset": "Sunset",
               "glossy": "Glossy colours", "pastel": "Pastels", "fuji-provia-scene": "Fujifilm test scene"}


def norm(key: str) -> str:
    return key.replace(".", "")


def reference_trust() -> dict:
    """How well each Fuji target matches genuine Fujifilm files (validation against real JPEGs)."""
    out = {}
    val = json.loads((REPORT / "reference-validation.json").read_text())["sims"]
    for k, v in val.items():
        k = norm(k)
        if v.get("patch_t"):
            out[k] = {"kind": "checked", "ref_mean": v["patch_t"]["mean"], "ref_nothing": v["patch_baseline"]["mean"]}
        else:
            out[k] = {"kind": "official-only"}
    pnh = REPORT / "pro-neg-high.json"
    if pnh.exists():
        d = json.loads(pnh.read_text())
        c = d["candidates"]
        out["PRO-NegHigh"] = {"kind": "fitted", "ref_mean": c[d["chosen"]]["patch"]["mean"],
                              "ref_nothing": c["do nothing (Provia)"]["patch"]["mean"]}
    out["PROVIA"] = {"kind": "identity"}
    return out


def fallback_swatches() -> tuple[dict, dict]:
    """Plain dense_v shots of dense1 against each look's target (no setting measured yet)."""
    from fit2 import load
    from score_verify import target_for, to_u8
    shots, _ = load(ROOT / "captures" / "poco" / "dense1")
    bases = [c for c in shots if c.startswith("base")]
    plain = np.mean([shots[c]["dense_v"] for c in bases], 0)
    noise = [float(de(shots[x]["dense_v"], shots[y]["dense_v"]).mean()) for x, y in zip(bases, bases[1:])]
    cubes, out = sims(), {}
    provia = load_cube(cubes["PROVIA"])
    hexes = lambda lab: ["#%02x%02x%02x" % tuple(v) for v in to_u8(lab)]  # noqa: E731
    for sim, cube in cubes.items():
        tgt = target_for(plain, load_cube(cube), provia)
        d = de(plain, tgt)
        out[sim] = {"swatches": {"phone": hexes(plain), "fuji": hexes(tgt), "plain": hexes(plain),
                                 "dE": [round(float(x), 2) for x in d]},
                    "do_nothing": stats(d)}
    return out, stats(np.array(noise))


def exact_tier() -> dict:
    """The .cube route per look: editor-ceiling stats plus swatches from one real plain shot of
    dense_v and the same frame with the look's _P3 cube applied as the Gallery editor holds it."""
    ceil_p = REPORT / "editor-ceiling.json"
    if not ceil_p.exists():
        return {}
    from make_editor_cubes import NAMES
    from measure import read_patches
    from score_verify import to_u8
    ceil = json.loads(ceil_p.read_text())["sims"]
    edit = ROOT / "scratch" / "edit"
    hexes = lambda lab: ["#%02x%02x%02x" % tuple(v) for v in to_u8(lab)]  # noqa: E731
    plain = np.array(read_patches(edit / "plain_v.jpg", chart="dense_v")["Lab"]) if (edit / "plain_v.jpg").exists() else None
    cubes = sims()
    provia = load_cube(cubes["PROVIA"])
    out = {}
    for sim, st in ceil.items():
        e = {"file": f"{NAMES[sim]}_P3.cube", "stats": st["P3"]}
        shot = edit / f"plain_v_sim_{NAMES[sim]}_P3.jpg"
        if plain is not None and shot.exists():
            from fit import fuji_target
            got = np.array(read_patches(shot, chart="dense_v")["Lab"])
            tgt = fuji_target(plain, load_cube(cubes[sim]), provia)
            e["swatches"] = {"phone": hexes(got), "fuji": hexes(tgt),
                             "dE": [round(float(x), 2) for x in de(got, tgt)]}
        out[sim] = e
    return out


def main() -> None:
    trust = reference_trust()
    exact = exact_tier()
    # the final choice (chart + photos, choose_final.py) when it exists, else the chart-only verify1
    verify_p = next((q for q in (REPORT / "poco-verify-final.json", REPORT / "poco-verify1.json") if q.exists()),
                    REPORT / "poco-verify1.json")
    measured = verify_p.exists()
    if measured:
        v = json.loads(verify_p.read_text())
        per_sim, noise = v["sims"], v["noise_floor_neighbour_plain_dE"]
    else:
        per_sim, noise = fallback_swatches()
    scenes_p = next((q for q in (REPORT / "poco-scenes-final.json", REPORT / "poco-scenes1.json") if q.exists()),
                    REPORT / "poco-scenes1.json")
    scenes = json.loads(scenes_p.read_text()) if scenes_p.exists() else {}
    (SITE / "scenes").mkdir(parents=True, exist_ok=True)
    looks = []
    for key, name, alt, blurb in LOOKS:
        s = per_sim.get(key)
        if s is None:
            continue
        entry = {"key": key, "name": name, "alt": alt, "blurb": blurb, "trust": trust.get(key, {"kind": "official-only"}),
                 "measured": measured, "swatches": s["swatches"], "do_nothing": s["do_nothing"]}
        if measured:
            entry |= {"best": s["best"], "all": s["all_patches"], "held": s["held_out_page"]}
        sc = []
        for page, r in (scenes.get(key) or {}).items():
            slug = page.removeprefix("scene_")
            imgs = {}
            # when the advice is to leave the camera alone, the photo shown is the plain one
            plain_pick = measured and s["best"]["config"] == "plain"
            for part, src_part in (("look", "plain" if plain_pick else "look"), ("fuji", "fuji")):
                src = REPORT / "scenes" / f"{key}_{slug}_{src_part}.jpg"
                if src.exists():
                    shutil.copyfile(src, SITE / "scenes" / src.name)
                    imgs[part] = f"scenes/{src.name}"
            sc.append({"slug": slug, "name": SCENE_NAMES.get(slug, slug),
                       "look": r["plain_vs_target"] if plain_pick else r["look_vs_target"],
                       "nothing": r["plain_vs_target"], "img": imgs if len(imgs) == 2 else None})
        entry["scenes"] = sc
        if key in exact:
            entry["exact"] = exact[key]
        looks.append(entry)
    data = {"device": "POCO X6 Pro", "camera": "Xiaomi Camera 6.1", "noise": noise, "measured": measured,
            "looks": looks}
    html = (SITE / "template.html").read_text(encoding="utf-8")
    (SITE / "index.html").write_text(html.replace("/*DATA*/null", json.dumps(data, separators=(",", ":"))),
                                     encoding="utf-8")
    print(f"wrote {SITE / 'index.html'}: {len(looks)} looks, measured={measured}, "
          f"scene images={sum(1 for l in looks for s in l['scenes'] if s['img'])}")


if __name__ == "__main__":
    main()
