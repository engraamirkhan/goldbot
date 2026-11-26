"""Outcome-blind n_eff counts for config/data/sufficiency.yaml by the sr-v3 card 2.2 method, from a served r11 tiers
file (graded days per instrument-month; no label, outcome or price is read).

Card 2.2 (ADR 0085 Q1/Q3), reproduced from the card's own table (gold learn 2,070 days -> 1m 7,328 ... 1w 104):
* holds under a day: u = 1,380 / hold minutes a day, n_eff/day = u / (1 + (u - 1) rho_day), rho_day 0.25;
* holds of a day or more: days / hold days;
* pooled over k instruments: / (1 + (k - 1) rho_inst), rho_inst 0.5.
Hold per timeframe, solved from the card's gold row (exact to the unit): 1m 60 min, 5m 240, 15m 480, 30m 960, 1h 1,380
(one session), 2h 2 days, 4h 5, 1d 10, 1w 20.
Days per unit and window: the `days` of the instrument-months whose tier the loader keeps at that timeframe: intraday
cells A and A-consistent for EVERY unit (a B month loads no intraday row, also in a "B" unit: quant CH1); 1d/1w A,
A-consistent and B.

    uv run python scripts/research/sufficiency_counts.py --tiers raw/tiers/tiers_served_r11-v2.2.csv
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from goldbot.data.holdout_guard import HOLDOUT_START

TFS = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w")
HOLD_MIN = {"1m": 60, "5m": 240, "15m": 480, "30m": 960, "1h": 1380}
HOLD_DAYS = {"2h": 2, "4h": 5, "1d": 10, "1w": 20}
RHO_DAY, RHO_INST = 0.25, 0.5
AB = ("A", "A-consistent", "B")
AA = ("A", "A-consistent")
# unit -> (keys, windows)
UNITS: dict[str, tuple[tuple[str, ...], dict[str, tuple[str, str]]]] = {
    "gold": (("XAUUSD",), {"learn": ("2010-01", "2017-12"), "judge": ("2018-01", "2025-09")}),
    "fx10": (("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "EURJPY", "GBPJPY", "AUDJPY"),
             {"learn": ("2010-01", "2017-12"), "judge": ("2018-01", "2025-09")}),
    "metals_crosses_b": (("XAGUSD", "XAUEUR", "EURAUD", "EURGBP", "GBPAUD"),
                         {"learn": ("2010-01", "2017-12"), "judge": ("2018-01", "2025-09")}),
    "index_energy_b": (("US500", "USTEC", "UK100", "DE40", "XTIUSD", "USINDEX"),
                       {"learn": ("2018-01", "2021-12"), "judge": ("2022-01", "2025-09"),
                        "card_confirm": ("2018-01", "2025-09")}),
}


def per_day(tf: str) -> float:
    if tf in HOLD_MIN:
        u = 1380 / HOLD_MIN[tf]
        return u / (1 + (u - 1) * RHO_DAY)
    return 1 / HOLD_DAYS[tf]


def n_eff(days: int, k: int, tf: str) -> int:
    return round(days * per_day(tf) / (1 + (k - 1) * RHO_INST))


def unit_days(t: pd.DataFrame, keys: tuple[str, ...], tiers: tuple[str, ...], lo: str, hi: str) -> int:
    m = t["month"]
    sel = t["key"].isin(keys) & t["tier"].isin(tiers) & (m >= lo) & (m <= hi)
    return int(t.loc[sel, "days"].sum())


def counts(t: pd.DataFrame) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for unit, (keys, wins) in UNITS.items():
        k = len(keys)
        rec: dict[str, object] = {"k": k}
        for w, (lo, hi) in wins.items():
            d_intra, d_daily = unit_days(t, keys, AA, lo, hi), unit_days(t, keys, AB, lo, hi)
            rec[f"{w}_days_intraday"], rec[f"{w}_days_daily"] = d_intra, d_daily
            rec[w] = [n_eff(d_daily if tf in ("1d", "1w") else d_intra, k, tf) for tf in TFS]
        out[unit] = rec
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tiers", type=Path, required=True)
    a = ap.parse_args()
    t = pd.read_csv(a.tiers, dtype={"month": str})
    t = t[t["month"] < HOLDOUT_START.strftime("%Y-%m")]        # no sealed month is ever counted (holdout guard)
    res = counts(t)
    print(json.dumps({"tiers_sha256": hashlib.sha256(a.tiers.read_bytes()).hexdigest(), "method_check": {
        "gold_learn_2070_days": [n_eff(2070, 1, tf) for tf in TFS],
        "card_gold_learn": [7328, 5443, 4054, 2683, 2071, 1035, 414, 207, 104]}, "units": res}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
