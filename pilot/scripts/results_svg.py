"""Draw the pilot's results (pilot/results/*.json) as a dot plot SVG for the README.

    python pilot/scripts/results_svg.py pilot/results/poco-x6-pro.json docs/results.svg

One row per look, three marks per row: doing nothing (open circle), the best in-camera setting
(filled circle) and the exact .cube tier (diamond). Shapes differ as well as colours, so the chart
reads in greyscale. Values past the axis are clipped and labelled with their real number.
"""
from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path

INK, MUTED, GRID, BAND = "#1f2328", "#59636e", "#d1d9e0", "#eef6ee"
NOTHING, SETTING, CUBE = "#59636e", "#b35900", "#0b6e63"
X_MAX = 12.0


def main(src: Path, out: Path) -> None:
    data = json.loads(src.read_text(encoding="utf-8"))
    looks = data["looks"]
    left, right, top, row = 190, 40, 92, 34
    width = 900
    plot_w = width - left - right
    height = top + row * len(looks) + 74

    def x(v: float) -> float:
        return left + min(v, X_MAX) / X_MAX * plot_w

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
         f'font-family="-apple-system, Segoe UI, Helvetica, Arial, sans-serif" role="img" '
         f'aria-label="Colour error per Fujifilm look on a {escape(data["phone"])}">',
         f'<rect width="{width}" height="{height}" rx="10" fill="#ffffff"/>',
         f'<text x="24" y="34" font-size="18" font-weight="600" fill="{INK}">How far each look lands from Fujifilm\'s '
         f'target (ΔE2000, lower is better)</text>',
         f'<text x="24" y="58" font-size="13" fill="{MUTED}">{escape(data["phone"])} · {len(looks)} looks · '
         f'{data["shots"]} real shots · scored on 135 colours the fit never saw</text>']
    y0, y1 = top - 12, top + row * len(looks) - 10
    s.append(f'<rect x="{x(0):.1f}" y="{y0}" width="{x(1) - x(0):.1f}" height="{y1 - y0}" fill="{BAND}"/>')
    s.append(f'<text x="{x(0) + 4:.1f}" y="{y0 - 4}" font-size="11" fill="#2f6b34">under 1: invisible</text>')
    for t in range(0, int(X_MAX) + 1, 2):
        s.append(f'<line x1="{x(t):.1f}" y1="{y0}" x2="{x(t):.1f}" y2="{y1}" stroke="{GRID}" stroke-width="1"/>')
        s.append(f'<text x="{x(t):.1f}" y="{y1 + 18}" font-size="12" fill="{MUTED}" text-anchor="middle">{t}</text>')
    for i, lk in enumerate(looks):
        cy = top + i * row + 6
        s.append(f'<text x="{left - 14}" y="{cy + 4}" font-size="13.5" fill="{INK}" text-anchor="end">{escape(lk["look"])}</text>')
        n, st, c = lk["dE_nothing"], lk["dE_setting"], lk["dE_cube"]
        if n > st:
            s.append(f'<line x1="{x(st):.1f}" y1="{cy}" x2="{x(n):.1f}" y2="{cy}" stroke="{SETTING}" stroke-width="2" opacity="0.45"/>')
        s.append(f'<circle cx="{x(n):.1f}" cy="{cy}" r="6" fill="#ffffff" stroke="{NOTHING}" stroke-width="2"/>')
        if n > X_MAX:
            s.append(f'<text x="{x(n) - 12:.1f}" y="{cy - 9}" font-size="11" fill="{MUTED}" text-anchor="end">{n:.1f} →</text>')
        if n > st:
            s.append(f'<circle cx="{x(st):.1f}" cy="{cy}" r="6" fill="{SETTING}"/>')
        d = 6.5
        s.append(f'<path d="M{x(c):.1f} {cy - d}L{x(c) + d:.1f} {cy}L{x(c):.1f} {cy + d}L{x(c) - d:.1f} {cy}Z" fill="{CUBE}"/>')
    ly = height - 26
    s.append(f'<text x="{left}" y="{ly - 22}" font-size="12" fill="{MUTED}">ΔE2000 · axis clipped at {X_MAX:g} · '
             f'rig repeat noise {data["rig_repeat_noise"]}</text>')
    lx = left
    s.append(f'<circle cx="{lx + 6}" cy="{ly}" r="6" fill="#ffffff" stroke="{NOTHING}" stroke-width="2"/>'
             f'<text x="{lx + 18}" y="{ly + 4}" font-size="13" fill="{INK}">doing nothing</text>')
    lx += 140
    s.append(f'<circle cx="{lx + 6}" cy="{ly}" r="6" fill="{SETTING}"/>'
             f'<text x="{lx + 18}" y="{ly + 4}" font-size="13" fill="{INK}">best in-camera setting (measured)</text>')
    lx += 265
    s.append(f'<path d="M{lx + 6} {ly - 6.5}L{lx + 12.5} {ly}L{lx + 6} {ly + 6.5}L{lx - 0.5} {ly}Z" fill="{CUBE}"/>'
             f'<text x="{lx + 18}" y="{ly + 4}" font-size="13" fill="{INK}">exact .cube in the Gallery editor (simulated)</text>')
    s.append("</svg>")
    out.write_text("\n".join(s) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
