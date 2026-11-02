"""Render the sprint-200 `stats` lane result JSONs (scripts/sprint200/stats_screen.py) as one markdown table.

    uv run python scripts/sprint200/stats_table.py ~/tmp/s200stats/explore.json [~/tmp/s200stats/confirm.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

NAMES = {
    "01": "HMM2-1d-LOWVOL-TREND", "02": "HMM2-1d-HIGHVOL-REVERT", "03": "HMM2-4h-LOWVOL-TREND",
    "04": "RVPCT-1d-LOW-DONCHIAN", "05": "RVPCT-1d-HIGH-FADE", "06": "RVRATIO-4h-COMPRESSION-BREAK",
    "07": "KALMAN-4h-SLOPE-CROSS", "08": "KALMAN-1h-SLOPE-CROSS", "09": "KALMAN-1d-SLOPE-CROSS",
    "10": "KALMAN-1h-INNOV-FADE", "11": "VR-1h-TREND-MOM", "12": "VR-1h-REVERT-FADE", "13": "AC-1h-FOLLOW",
    "14": "AC-1h-FADE", "15": "VR-1d-MOM", "16": "AC-4h-FOLLOW", "17": "INTRADAY-MOM-COMEX", "18": "RSKEW-1d-CONTRA",
    "19": "JUMP-1d-CONT", "20": "JUMP-1d-REV", "21": "SJ-1d-CONTRA", "22": "MAX-1d-LOTTERY", "23": "RSKEW-4h-CONTRA",
    "24": "ML-LR-1h", "25": "ML-GBM-1h", "26": "ML-LR-4h", "27": "ML-GBM-4h", "28": "ML-LR-1d", "29": "ML-GBM-1d",
    "30": "KMEANS-1d-REGIME", "31": "ENS-4h-TREND", "32": "ENS-1d-TREND", "33": "ENS-4h-STAT",
}


def f(x: object, p: int = 3) -> str:
    return "-" if x is None else (f"{x:+.{p}f}" if isinstance(x, float) else str(x))


def main() -> None:
    ex = json.loads(Path(sys.argv[1]).expanduser().read_text())["rules"]
    co = json.loads(Path(sys.argv[2]).expanduser().read_text())["rules"] if len(sys.argv) > 2 else {}
    print("| id | rule | explore n | gross R | net R | t | fwd twin R | real - fwd twin | real - sameday twin | "
          "yrs+ /8 | explore | confirm n | net R | t | DSR K3229 | yrs+ /8 | verdict |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for rid in sorted(NAMES):
        e = ex.get(rid, {"n": 0})
        c = co.get(rid)
        conf = (f"{c['n']} | {f(c['net_mean_r'])} | {c['t']:.2f} | {c['dsr_k3229']:.3f} | "
                f"{c['confirm_years_positive_of_8']} | {'PASS' if c['pass'] else 'FAIL (confirm)'}") if c else \
            ("- | - | - | - | - | " + ("FAIL (explore)" if not e.get("pass") else "not run"))
        if e.get("n", 0) == 0:
            print(f"| S200-STATS-{rid} | {NAMES[rid]} | 0 | - | - | - | - | - | - | - | FAIL | {conf} |")
            continue
        print(f"| S200-STATS-{rid} | {NAMES[rid]} | {e['n']} | {f(e['gross_mean_r'])} | {f(e['net_mean_r'])} | "
              f"{e['t']:.2f} | {f(e['twin_net_mean_r'])} | {f(e['minus_twin'])} | "
              f"{f(e.get('minus_sameday_twin'))} | {e['years_positive']} | "
              f"{'pass' if e['pass'] else 'FAIL'} | {conf} |")


if __name__ == "__main__":
    main()
