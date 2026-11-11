"""Read-only health snapshot of a phone (battery, heat, throttling, Wi-Fi, mobile data), timed.

Every command here only READS: getprop, dumpsys, cat of /proc and /sys, logcat -d. Nothing is
reset, cleared or set. Output goes to diagnostics/<name>/<UTC time>/ (gitignored: it holds
personal data such as Wi-Fi names and app lists) and never leaves the laptop. A timing table is
printed, so the run on a Xiaomi 14T can be planned from the POCO dry run.

    python health.py --serial <adb serial> --name poco [--bugreport] [--radio-minutes 10]

--radio-minutes samples telephony.registry once a minute to catch 5G/LTE flapping.
--bugreport adds `adb bugreport` (a zip of a few tens of MB, several minutes of phone CPU).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phone import ADB  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

READS = {
    "getprop": "getprop",
    "battery": "dumpsys battery",
    "batterystats": "dumpsys batterystats --charged",
    "power": "dumpsys power",
    "deviceidle": "dumpsys deviceidle",
    "alarm": "dumpsys alarm",
    "procstats": "dumpsys procstats --hours 24",
    "cpuinfo": "dumpsys cpuinfo",
    "thermal": "dumpsys thermalservice",
    "cpufreq": "for c in /sys/devices/system/cpu/cpu[0-9]*; do echo $c $(cat $c/cpufreq/scaling_cur_freq "
               "$c/cpufreq/scaling_max_freq $c/cpufreq/cpuinfo_max_freq 2>/dev/null); done",
    "battery_sysfs": "for f in cycle_count charge_full charge_full_design health temp; do "
                     "echo $f $(cat /sys/class/power_supply/battery/$f 2>/dev/null); done",
    "wifi": "dumpsys wifi",
    "connectivity": "dumpsys connectivity",
    "netstats": "dumpsys netstats",
    "telephony": "dumpsys telephony.registry",
    "storage": "df -h /data /sdcard",
    "packages": "pm list packages -U",
    "logcat_radio": "logcat -d -b radio -t 4000",
    "logcat_main": "logcat -d -b main -t 4000",
    "logcat_system": "logcat -d -b system -t 4000",
    "logcat_crash": "logcat -d -b crash",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--bugreport", action="store_true")
    ap.add_argument("--radio-minutes", type=int, default=0)
    a = ap.parse_args()
    env = os.environ | {"MSYS_NO_PATHCONV": "1"}
    out = ROOT / "diagnostics" / a.name / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out.mkdir(parents=True, exist_ok=True)
    timing = []
    for name, cmd in READS.items():
        t = time.time()
        r = subprocess.run([ADB, "-s", a.serial, "shell", cmd], capture_output=True, env=env, timeout=300)
        (out / f"{name}.txt").write_bytes(r.stdout + r.stderr)
        timing.append((name, time.time() - t, len(r.stdout)))
    for i in range(a.radio_minutes):
        r = subprocess.run([ADB, "-s", a.serial, "shell", "dumpsys telephony.registry"], capture_output=True, env=env)
        (out / f"telephony_t{i:02d}.txt").write_bytes(r.stdout)
        time.sleep(60)
    if a.bugreport:
        t = time.time()
        subprocess.run([ADB, "-s", a.serial, "bugreport", str(out / "bugreport.zip")], env=env, timeout=1800)
        size = (out / "bugreport.zip").stat().st_size if (out / "bugreport.zip").exists() else 0
        timing.append(("bugreport", time.time() - t, size))
    lines = [f"{n:<16} {s:7.1f} s {b / 1024:9.0f} KB" for n, s, b in timing]
    (out / "timing.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"total {sum(s for _, s, _ in timing):.0f} s -> {out}")


if __name__ == "__main__":
    main()
