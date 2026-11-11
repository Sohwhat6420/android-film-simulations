"""Drive the POCO X6 Pro camera's Pro-mode colour controls and shoot the chart at each setting.

Pro mode must already be open with exposure, ISO, white balance and focus locked, and the chart
showing on the monitor (viewer.py). Every setting is read back from the camera's own UI
(uiautomator) before the shot, so a missed swipe cannot silently produce a mislabelled photo.

The POCO's Pro "Parameters" are Saturation / Contrast / Sharpness, -3..+3; a drag of 62 device
px moves one step and dragging LEFT raises the value. Its Pro filters are picked by name.

    python poco_sweep.py --serial <serial> --plan sweep1 --out captures/poco/sweep1
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure import read_patches  # noqa: E402
from phone import ADB, adb, shoot  # noqa: E402

PKG = "com.android.camera:id/"
PARAMS_ICON, FILTER_ICON = (124, 1750), (1096, 1750)
SLIDER_Y, STEP_PX = 2160, 62
FILTERS = ["Original", "Positive", "Negative", "Nature", "Vivid", "Cinematic", "Gold vibes", "Freehand",
           "Monsoon", "Parkour", "Orchid", "Lazy day", "Old town", "Alley", "Reversal"]


class Phone:
    def __init__(self, serial: str):
        self.serial = serial

    def tap(self, x: int, y: int) -> None:
        adb(self.serial, "shell", "input", "tap", str(x), str(y))
        time.sleep(0.6)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 600) -> None:
        adb(self.serial, "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))
        time.sleep(0.8)

    def nodes(self) -> list[dict]:
        adb(self.serial, "shell", "uiautomator", "dump", "/sdcard/window_dump.xml")
        root = ET.fromstring(adb(self.serial, "shell", "cat", "/sdcard/window_dump.xml"))
        out = []
        for n in root.iter("node"):
            b = [int(v) for v in re.findall(r"\d+", n.get("bounds", "[0,0][0,0]"))]
            out.append({"id": n.get("resource-id", "").replace(PKG, ""), "text": n.get("text", ""),
                        "desc": n.get("content-desc", ""), "selected": n.get("selected") == "true",
                        "centre": ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)})
        return out

    def close_panel(self) -> None:
        """While the Filters or Parameters panel is open the other icon is hidden; Back closes it."""
        if any(n["id"] == "text_item_title" for n in self.nodes()):
            adb(self.serial, "shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(0.8)

    # --- Parameters: Saturation / Contrast / Sharpness ---
    def params(self, tries: int = 4) -> dict[str, int]:
        """The panel's values, re-read if a dump lands mid-animation (seen 18 Oct: a read with no Saturation)."""
        for i in range(tries):
            ns = self.nodes()
            keys = [n for n in ns if n["id"] == "pic_style_key"]
            vals = [n for n in ns if n["id"] == "pic_style_value"]
            try:
                p = {k["text"]: int(v["text"]) for k, v in zip(keys, vals)}
            except ValueError:
                p = {}
            if len(p) == 3 or i == tries - 1:
                return p | {"_selected": next((k["text"] for k in keys if k["selected"]), None)}
            time.sleep(0.7)
        return {}

    def open_params(self) -> dict:
        p = self.params(tries=1)
        for _ in range(3):  # a tap can be swallowed while the camera refocuses (seen 18 Oct)
            if len(p) == 4:  # three sliders + "_selected"
                break
            self.close_panel()
            self.tap(*PARAMS_ICON)
            time.sleep(0.8)
            p = self.params()
        if len(p) < 4:
            raise RuntimeError("Parameters panel did not open")
        return p

    def set_param(self, name: str, value: int) -> None:
        p = self.open_params()
        if p["_selected"] != name:
            label = next(n for n in self.nodes() if n["id"] == "pic_style_key" and n["text"] == name)
            self.tap(*label["centre"])
        for _ in range(4):
            cur = self.params()[name]
            if cur == value:
                return
            half = (value - cur) * STEP_PX // 2  # dragging LEFT raises the value
            self.swipe(610 + half, SLIDER_Y, 610 - half, SLIDER_Y)
        raise RuntimeError(f"{name} would not go to {value} (at {self.params()[name]})")

    # --- Pro filters ---
    def filter_nodes(self) -> list[dict]:
        return [n for n in self.nodes() if n["id"] == "effect_item_text"]

    def set_filter(self, name: str) -> None:
        items = self.filter_nodes()
        for _ in range(3):  # same swallowed-tap retry as open_params
            if items:
                break
            self.close_panel()
            self.tap(*FILTER_ICON)
            time.sleep(0.8)
            items = self.filter_nodes()
        if not items:
            raise RuntimeError("filter panel did not open")
        for direction in ("left", "right"):
            for _ in range(6):
                hit = [n for n in items if n["text"] == name]
                if hit:
                    self.tap(*hit[0]["centre"])
                    time.sleep(1.0)
                    if self.selected_filter() != name:  # one retry: a tap during a fling only stops it
                        hit = [n for n in self.filter_nodes() if n["text"] == name]
                        if hit:
                            self.tap(*hit[0]["centre"])
                            time.sleep(1.0)
                    if self.selected_filter() != name:
                        raise RuntimeError(f"tapped {name!r} but {self.selected_filter()!r} is selected")
                    return
                if direction == "left":  # slow drags, then wait, so the list is still when tapped
                    self.swipe(1000, 1881, 400, 1881, 1200)
                else:
                    self.swipe(250, 1881, 850, 1881, 1200)
                time.sleep(0.8)
                items = self.filter_nodes()
        raise RuntimeError(f"filter {name!r} not found")

    def selected_filter(self) -> str | None:
        """Which visible tile wears the white selection frame (rows ~1765-1770 above the thumbnail).

        The accessibility text says "<name>, Selected" only when the list is first drawn, so the
        frame itself is read from a screen capture.
        """
        png = subprocess.run([ADB, "-s", self.serial, "exec-out", "screencap", "-p"], capture_output=True, timeout=30).stdout
        grey = np.asarray(Image.open(io.BytesIO(png)).convert("L")).astype(int)
        for n in self.filter_nodes():
            cx = n["centre"][0]
            if 40 <= cx <= 1180 and grey[1762:1774, cx - 40:cx + 40].mean(1).max() > 200:
                return n["text"]
        return None


def plans() -> dict[str, list[dict]]:
    base = {"Saturation": 0, "Contrast": 0, "filter": "Original"}
    sweep1 = [dict(base, tag="base_a")]
    for ctl in ("Saturation", "Contrast"):
        for v in (-3, -2, -1, 1, 2, 3):
            sweep1.append(dict(base, **{ctl: v}, tag=f"{ctl[:3].lower()}{v:+d}"))
    sweep1.append(dict(base, tag="base_b"))
    for f in FILTERS[1:]:
        sweep1.append(dict(base, filter=f, tag="f_" + f.lower().replace(" ", "-")))
    sweep1.append(dict(base, tag="base_c"))

    # dense1: every control setting on the three dense pages, each one BRACKETED by plain shots
    # (base, setting, base, setting, ..., base) so the monitor's slow colour drift cancels out:
    # each setting's effect is learnt against the plain shots taken minutes either side of it.
    pages = ["dense_a", "dense_b", "dense_v"]
    configs = [dict(base, **{ctl: v}, tag=f"{ctl[:3].lower()}{v:+d}")
               for ctl in ("Saturation", "Contrast") for v in (-3, -2, -1, 1, 2, 3)]
    configs += [dict(base, filter=f, tag="f_" + f.lower().replace(" ", "-")) for f in FILTERS[1:]]
    dense1 = [dict(base, tag="base00", pages=pages)]
    for i, c in enumerate(configs, 1):
        dense1 += [dict(c, pages=pages), dict(base, tag=f"base{i:02d}", pages=pages)]
    # surround1: does the phone render a colour differently depending on what surrounds it (local
    # tone mapping)? The same 60 patches on mid-grey, dark and light surrounds, plain and with a
    # strong look, twice, so the surround effect can be told apart from shot noise.
    rounds = ["chart60", "surround_dark", "surround_light"]
    strong = dict(base, filter="Vivid", Saturation=2)
    surround1 = []
    for rep in (1, 2):
        surround1 += [dict(base, tag=f"plain{rep}", pages=rounds), dict(strong, tag=f"vivid{rep}", pages=rounds)]
    # exposure1: are the controls' effects the same one stop darker? The held-out page at full and
    # half brightness (patches only, markers untouched), plain and with four different settings,
    # bracketed. The effects learnt from dense1 at full brightness must predict the dim shots.
    exp_pages = ["dense_v", "dense_vdim"]
    exposure1 = [dict(base, tag="base00", pages=exp_pages)]
    for i, c in enumerate([dict(base, Saturation=3, tag="sat+3"), dict(base, Contrast=3, tag="con+3"),
                           dict(base, filter="Vivid", tag="f_vivid"), dict(base, filter="Cinematic", tag="f_cinematic")], 1):
        exposure1 += [dict(c, pages=exp_pages), dict(base, tag=f"base{i:02d}", pages=exp_pages)]
    return {"sweep1": sweep1, "dense1": dense1, "surround1": surround1, "exposure1": exposure1}


def verify_plan(fit_json: Path, per_sim: int = 2, pages=("dense_a", "dense_b", "dense_v")) -> list[dict]:
    """Shoot the fitter's best candidates for real, each bracketed by plain shots like dense1.

    Candidates shared by several looks are shot once. Tags read f<filter>_s<sat>_c<con>.
    """
    fit = json.loads(fit_json.read_text())
    base = {"Saturation": 0, "Contrast": 0, "filter": "Original"}
    # fit2 names a filter by its sweep tag ("gold-vibes"); the camera shows "Gold vibes"
    canon = {f.lower().replace(" ", "-"): f for f in FILTERS}
    seen, configs = set(), []
    for sim in fit["sims"].values():
        for c in sim["candidates"][:per_sim]:
            c = dict(c, filter=canon.get(c["filter"].lower().replace(" ", "-"), c["filter"]))
            key = (c["filter"], c["Saturation"], c["Contrast"])
            if key in seen or key == ("Original", 0, 0):
                continue
            seen.add(key)
            tag = f"f{c['filter'].lower().replace(' ', '-')}_s{c['Saturation']:+d}_c{c['Contrast']:+d}"
            configs.append(dict(filter=c["filter"], Saturation=c["Saturation"], Contrast=c["Contrast"], tag=tag))
    plan = [dict(base, tag="base00", pages=list(pages))]
    for i, c in enumerate(configs, 1):
        plan += [dict(c, pages=list(pages)), dict(base, tag=f"base{i:02d}", pages=list(pages))]
    return plan


SHOW = Path(__file__).resolve().parent.parent / "scratch" / "viewer" / "show.txt"
CHART_FILE = "{page}_s0.56_x0.60_y0.50_g0.752-0.899-1.000.png"


def show(page: str) -> None:
    """Put a chart page on the monitor (viewer.py polls show.txt) and let the screen settle."""
    charts = SHOW.parent.parent.parent / "charts"
    png = charts / CHART_FILE.format(page=page)
    if not png.exists():  # pages drawn already placed (surround_*): only the white balance is in the name
        png = charts / f"{page}_g0.752-0.899-1.000.png"
    if SHOW.read_text().strip() != str(png):
        SHOW.write_text(str(png))
        time.sleep(1.5)


HOT, COOL = 440, 390  # tenths of a degree C, as dumpsys reports: pause at 44.0, resume at 39.0


def battery_temp(ph: "Phone") -> int:
    info = subprocess.run([ADB, "-s", ph.serial, "shell", "dumpsys", "battery"], capture_output=True, text=True).stdout
    return int(re.search(r"temperature: (\d+)", info).group(1))


def thermal_guard(ph: "Phone") -> bool:
    """Rest the phone when it runs hot (a standing rule: never drain or overwork a test phone).

    Charging plus continuous camera use took the POCO to 45.7 C. Above HOT the screen is switched off
    (the camera closes, so sensor, ISP and display all rest) until the battery is back under COOL. Then
    the camera is reopened and the locked Pro settings are checked before shooting resumes.
    """
    t = battery_temp(ph)
    if t < HOT:
        return False
    print(f"{time.strftime('%H:%M:%S')} phone at {t / 10:.1f} C: resting with the screen off", flush=True)
    adb(ph.serial, "shell", "input", "keyevent", "KEYCODE_SLEEP")
    for _ in range(90):  # at most 90 min
        time.sleep(60)
        t = battery_temp(ph)
        if t <= COOL:
            break
    print(f"{time.strftime('%H:%M:%S')} phone at {t / 10:.1f} C: resuming", flush=True)
    adb(ph.serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
    time.sleep(1.5)
    adb(ph.serial, "shell", "wm", "dismiss-keyguard")
    time.sleep(1.5)
    adb(ph.serial, "shell", "am", "start", "-n", "com.android.camera/.Camera")
    time.sleep(4)
    # a fresh start opens in Photo mode (seen 18 Oct); Pro keeps its locked values, so just switch back
    pro = [n for n in ph.nodes() if n["text"] == "Pro"]
    if pro and not any(n["text"] == "1/50" for n in ph.nodes()):
        ph.tap(*pro[0]["centre"])
        time.sleep(3)
    texts = {n["text"] for n in ph.nodes()}
    missing = [v for v in ("1/50", "50", "6500K", "MF") if v not in texts]
    if missing:  # exposure/WB/focus must be exactly as locked, or every later shot is incomparable
        raise RuntimeError(f"after the rest the Pro settings changed (missing {missing}); stopping")
    # the first frame after a restart is soft while the lens settles (marker sharpness 48 against
    # about 260): wait, then spend one throwaway shot before measuring anything
    time.sleep(6)
    adb(ph.serial, "shell", "input", "tap", "609", "2360")
    time.sleep(4)
    return True


def battery_guard(ph: "Phone", floor: int = 12) -> None:
    """Pause while the phone is below `floor`% and not charging, and clear any battery-saver prompt.

    On 18 Oct the POCO ran off its cable mid-sweep: a "Turn on Battery saver" sheet covered the
    camera at 19% and the sweep died. Battery saver is never turned on (it can throttle processing).
    """
    while True:
        info = subprocess.run([ADB, "-s", ph.serial, "shell", "dumpsys", "battery"], capture_output=True, text=True).stdout
        level = int(re.search(r"level: (\d+)", info).group(1))
        powered = re.search(r"(AC|USB|Wireless) powered: true", info) is not None
        for n in ph.nodes():
            if (n.get("text") or "") == "Got it":  # the battery-saver sheet; "Got it" leaves saver off
                ph.tap(*n["centre"])
                time.sleep(1)
        if powered or level >= floor:
            return
        print(f"battery {level}% and not charging: paused", flush=True)
        time.sleep(60)


def run(serial: str, plan: list[dict], out: Path) -> None:
    ph = Phone(serial)
    out.mkdir(parents=True, exist_ok=True)
    log = out / "log.jsonl"
    done = {json.loads(l)["tag"] for l in log.read_text().splitlines()} if log.exists() else set()
    state: dict = {}
    for step in plan:
        pages = step.get("pages", [None])
        shots = [(f"{step['tag']}@{pg}" if pg else step["tag"], pg) for pg in pages]
        if all(t in done for t, _ in shots):
            continue
        battery_guard(ph)
        if thermal_guard(ph):
            state = {}  # the camera was reopened: read every control again instead of trusting the cache
        for key in ("Saturation", "Contrast"):
            if state.get(key) != step[key]:
                ph.set_param(key, step[key])
                state[key] = step[key]
        if state.get("filter") != step["filter"]:
            ph.set_filter(step["filter"])
            state["filter"] = step["filter"]
        ph.close_panel()
        for tag, pg in shots:
            if tag in done:
                continue
            if pg:
                show(pg)
            for attempt in range(3):  # a soft or shaken frame can hide a marker: shoot it again
                photo = shoot(serial, out / f"{tag}.jpg", settle=2.0)
                # scene pages have no patches; scene_check.py rectifies them later from their markers
                if (pg or "").startswith("scene_"):
                    r = {"file": str(photo), "scene": pg}
                    break
                try:
                    r = read_patches(photo, chart=pg or "chart60")
                    break
                except RuntimeError as e:
                    print(f"{tag}: {e}; retaking ({attempt + 1}/3)", flush=True)
            else:
                raise RuntimeError(f"{tag}: markers not found in 3 shots")
            (out / f"{tag}.json").write_text(json.dumps(r))
            with log.open("a") as f:
                f.write(json.dumps({k: v for k, v in step.items() if k != "pages"}
                                   | {"tag": tag, "config": step["tag"], "page": pg or "chart60",
                                      "time": time.strftime("%H:%M:%S")}) + "\n")
            print(tag, "ok", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", required=True)
    ap.add_argument("--plan", default="sweep1")
    ap.add_argument("--out", required=True)
    ap.add_argument("--verify", help="fit2.py JSON: shoot its best candidates instead of a named plan")
    ap.add_argument("--per-sim", type=int, default=2)
    ap.add_argument("--pages", nargs="+", help="with --verify: pages to shoot (default the three dense pages)")
    ap.add_argument("--best", help="score_verify.py JSON: shoot each look's best REAL setting (use with --pages)")
    a = ap.parse_args()
    if a.best:  # reshape the verified winners into fit2's format, one candidate per look
        best = json.loads(Path(a.best).read_text())["sims"]
        tmp = Path(a.out) / "best-settings.json"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"sims": {s: {"candidates": [v["best"]]} for s, v in best.items()}}))
        a.verify, a.per_sim = str(tmp), 1
    if a.verify:
        plan = verify_plan(Path(a.verify), a.per_sim, *( [tuple(a.pages)] if a.pages else []))
    else:
        plan = plans()[a.plan]
    run(a.serial, plan, Path(a.out))
