"""Sprint-3000 lane CATALOGUE: build the 3000-spec ranked catalogue (docs/research/sprint3000/prereg.md).

    uv run python scripts/sprint3000_catalogue.py            # writes config/research/sprint3000_catalogue.yaml
    uv run python scripts/sprint3000_catalogue.py --check    # exits 1 if the committed file differs from a rebuild

Ranking uses ONLY (a) the cost of record (config/arm_costs.yaml -> cor-2026-10-31.2, ICM) and the shipped swap,
(b) the explore-window gross numbers already published by the sprint-200 lanes and R-06, written below as constants,
and (c) fixed reference ATRs and fire-rate guesses written below. No bar, trade or outcome file is opened: the
script never touches raw/. Deterministic: same inputs, same bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import itertools
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "research" / "sprint3000_catalogue.yaml"
COR_VERSION = "cor-2026-10-31.2"
N_SPECS = 3000

# ---------------------------------------------------------------- vocabulary (brief + ADDENDUM A, exactly)
TFS = ("15m", "1h", "4h", "1d")
ENTRY_PARAMS: dict[str, tuple[str, ...]] = {
    "donchian_break": ("n",), "ma_cross": ("fast", "slow", "kind"), "ema_pullback": ("n", "depth_atr"),
    "keltner_break": ("n", "k"), "bb_break": ("n", "k"), "nr_breakout": ("n",), "inside_bar_break": (),
    "session_range_break": ("session",), "opening_range_break": ("session", "minutes"),
    "prior_level_break": ("level",), "momentum_sign": ("lookback_bars",), "consecutive_bars": ("n", "mode"),
    "atr_expansion": ("n", "mult"), "adx_onset": ("n", "level"), "supertrend_flip": ("n", "mult"),
    "macd_turn": ("fast", "slow", "signal"), "rsi_threshold": ("n", "level", "mode"), "zscore": ("n", "z", "mode"),
    # ADDENDUM A (implemented by lane KEYLEVELS)
    "level_break": ("min_strength_pct", "min_tf"), "level_reject": ("min_strength_pct", "min_tf"),
}
FILTER_PARAMS: dict[str, tuple[str, ...]] = {
    "trend": ("tf", "ema_n"), "vol_regime": ("atr_n", "pct_lo", "pct_hi"), "session": ("session",),
    "weekday": ("days",), "adx_min": ("n", "level"), "compression": ("n", "pct_max"),
    "near_level": ("max_dist_atr", "min_strength_pct"), "room_to_level": ("min_r",),
}
EXIT_PARAMS: dict[str, tuple[str, ...]] = {
    "target_r": ("r",), "chandelier": ("atr_n", "mult"), "time": ("bars",), "opposite_signal": (),
}
SIDES = ("signal", "long_only", "short_only")
LEVEL_ENTRIES = {"level_break", "level_reject"}
LEVEL_FILTERS = {"near_level", "room_to_level"}

# ---------------------------------------------------------------- priors (prereg section 3; no outcome data)
# raw explore-window gross R of the nearest sprint-200 / R-06 analogue, and the mechanism grade
PRIOR: dict[str, tuple[float, str]] = {
    "donchian_break": (0.20, "A"),     # TREND-03 +0.316 t4.08, 04 +0.162 t3.87, 01 +0.188 t2.26, 02 +0.169 t1.69
    "momentum_sign": (0.08, "A"),      # R-06/R-10 tsmom +0.06 t2.4-2.6 1h/4h; MOP 2012 replicated
    "ma_cross": (0.15, "B"),           # TREND-24 +0.201 t2.65
    "adx_onset": (0.15, "B"),          # TREND-28 +0.252 t2.26
    "bb_break": (0.12, "B"),           # TREND-15 +0.289 t2.22 (with squeeze)
    "macd_turn": (0.12, "B"),          # TREND-27 +0.164 t1.95
    "keltner_break": (0.08, "B"),      # TREND-05 +0.102 t1.24
    "atr_expansion": (0.08, "B"),      # TREND-19 +0.110 t2.73 (1h); TREND-29 1d -0.106 (n 66)
    "supertrend_flip": (0.06, "C"),    # TREND-33 +0.078 t1.60
    "nr_breakout": (0.06, "C"),        # TREND-16 +0.081 t2.46; PATTERNS NRB4/7 +0.09/+0.04
    "prior_level_break": (0.06, "C"),  # TREND-11 +0.074, 12 +0.116
    "level_break": (0.08, "C"),        # TREND-11/12 + PATTERNS-15/16 order-block/level +0.13-0.15 (t<1.8)
    "ema_pullback": (0.03, "C"),       # TREND-31 +0.145 but control excess +0.006; 06/25/30 <= 0
    "consecutive_bars": (0.04, "C"),   # follow: PATTERNS 3S +0.14/+0.30 (n 90-125)
    "rsi_threshold": (0.04, "C"),      # follow: no direct test
    "zscore": (0.06, "C"),             # follow = band breakout analogue
    "inside_bar_break": (0.02, "D"),   # TREND-18 -0.004, PATTERNS-12 -0.026
    "session_range_break": (0.02, "D"),  # TREND-07/13/14, GS-C2, R-02: retired
    "opening_range_break": (0.02, "D"),  # TREND-08/09/10, 15m ORB x24: retired
    "level_reject": (0.0, "D"),        # level fades retired (sprint-200 meanrev, KB key-level fades)
}
FADE_GROSS = 0.0                       # every *_mode=fade: sprint-200 meanrev 0 of 33, gross ~0
GRADE_W = {"A": 1.0, "B": 0.85, "C": 0.7, "D": 0.4}
SHRINK = 0.5                           # winner's curse: TREND-03 gross halved out of sample (+0.316 -> +0.165)
TF_GROSS = {"15m": 0.3, "1h": 1.0, "4h": 1.0, "1d": 0.8}
EXIT_GROSS = {"chandelier": 1.2, "target_r": 1.0, "opposite_signal": 1.0, "time": 0.8}
FAMILY = {
    "donchian_break": "trend_channel", "keltner_break": "trend_channel", "bb_break": "trend_channel",
    "ma_cross": "trend_ma", "macd_turn": "trend_ma", "supertrend_flip": "trend_ma", "ema_pullback": "pullback",
    "momentum_sign": "momentum", "consecutive_bars": "momentum", "rsi_threshold": "momentum", "zscore": "momentum",
    "adx_onset": "trend_onset", "atr_expansion": "trend_onset", "nr_breakout": "compression_breakout",
    "inside_bar_break": "compression_breakout", "prior_level_break": "level_break", "level_break": "key_level",
    "level_reject": "key_level", "session_range_break": "session_breakout", "opening_range_break": "session_breakout",
}
BREAKOUTS = {"donchian_break", "keltner_break", "bb_break", "nr_breakout", "inside_bar_break", "session_range_break",
             "opening_range_break", "prior_level_break", "atr_expansion", "level_break", "zscore"}

# reference ATR(14) in USD/oz over 2010-2017 (from sprint-200 published stop medians; prior, not measured here)
ATR_REF = {"15m": 1.1, "1h": 2.6, "4h": 5.8, "1d": 16.0}
TF_HOURS = {"15m": 0.25, "1h": 1.0, "4h": 4.0, "1d": 23.0}
EXPLORE_BARS = {"15m": 2050 * 92, "1h": 2050 * 23, "4h": 2050 * 6, "1d": 2050}
MAX_HOLD = {"15m": 32, "1h": 48, "4h": 30, "1d": 20}
N_CAP = 1500
COST_PRICE_REF = 4000.0        # Amendment W2: cost of record dollars / 4,000 = basis points of price
EXPLORE_PRICE_REF = 1300.0     # typical gold price level 2010-2017 (a level, not a return): bps -> explore dollars
OLD_TEST_SHARE = 47 / 96       # TEST-OLD 2006-02..2009-12 months / explore months: expected old-test count share
OLD_TEST_MIN = {"15m": 100, "1h": 100, "4h": 60, "1d": 60}

# ---------------------------------------------------------------- grids
ENTRY_GRID: dict[str, list[dict[str, Any]]] = {
    "donchian_break": [{"n": n} for n in (10, 20, 30, 48, 55, 100)],
    "ma_cross": [{"fast": f, "slow": s, "kind": k} for (f, s) in ((5, 20), (9, 21), (10, 50), (20, 50), (20, 100),
                                                                   (50, 200)) for k in ("ema", "sma")],
    "ema_pullback": [{"n": n, "depth_atr": d} for n in (20, 50) for d in (0.0, 0.5, 1.0)],
    "keltner_break": [{"n": n, "k": k} for n in (20, 50) for k in (1.5, 2.0, 2.5)],
    "bb_break": [{"n": n, "k": k} for n in (20, 50) for k in (1.5, 2.0, 2.5)],
    "nr_breakout": [{"n": n} for n in (4, 7)],
    "inside_bar_break": [{}],
    "session_range_break": [{"session": s} for s in ("asia", "london", "ny")],
    "opening_range_break": [{"session": s, "minutes": m} for s in ("asia", "london", "ny") for m in (15, 30, 60)],
    "prior_level_break": [{"level": lv} for lv in ("day", "week", "month")],
    "momentum_sign": [{"lookback_bars": n} for n in (12, 24, 48, 96, 120, 250)],
    "consecutive_bars": [{"n": n, "mode": m} for n in (3, 4, 5) for m in ("follow", "fade")],
    "atr_expansion": [{"n": n, "mult": m} for n in (14, 20) for m in (1.5, 2.0, 2.5)],
    "adx_onset": [{"n": n, "level": lv} for n in (10, 14) for lv in (20, 25)],
    "supertrend_flip": [{"n": n, "mult": m} for n in (10, 20) for m in (2.0, 3.0, 4.0)],
    "macd_turn": [{"fast": f, "slow": s, "signal": g} for (f, s, g) in ((12, 26, 9), (8, 21, 5), (19, 39, 9))],
    "rsi_threshold": [{"n": n, "level": lv, "mode": m} for (n, lv) in ((14, 60), (14, 70), (14, 80), (2, 80), (2, 90))
                      for m in ("follow", "fade")],
    "zscore": [{"n": n, "z": z, "mode": m} for n in (20, 50, 100) for z in (1.5, 2.0, 2.5) for m in ("follow", "fade")],
    "level_break": [{"min_strength_pct": s, "min_tf": t} for s in (50, 75, 90) for t in ("1h", "4h", "1d", "1w")],
    "level_reject": [{"min_strength_pct": s, "min_tf": t} for s in (75, 90) for t in ("4h", "1d")],
}
STOP_MULT = {"15m": (2.0, 3.0, 4.0), "1h": (1.5, 2.0, 3.0), "4h": (1.5, 2.0, 3.0), "1d": (1.0, 1.5, 2.0)}
TIME_BARS = {"15m": (16, 32), "1h": (8, 24), "4h": (6, 18), "1d": (5, 10)}


def _f(t: str, **p: Any) -> dict[str, Any]:
    return {"type": t, "params": p}


def filter_sets(tf: str, entry: str) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = [[], [_f("trend", tf="1d", ema_n=50)], [_f("trend", tf="1d", ema_n=200)],
                                       [_f("vol_regime", atr_n=14, pct_lo=50, pct_hi=100)],
                                       [_f("vol_regime", atr_n=14, pct_lo=0, pct_hi=50)],
                                       [_f("adx_min", n=14, level=20)], [_f("weekday", days=[0, 1, 2, 3])],
                                       [_f("trend", tf="1d", ema_n=50), _f("vol_regime", atr_n=14, pct_lo=50, pct_hi=100)],
                                       [_f("room_to_level", min_r=2.0)], [_f("room_to_level", min_r=3.0)],
                                       [_f("trend", tf="1d", ema_n=50), _f("room_to_level", min_r=2.0)],
                                       [_f("near_level", max_dist_atr=0.5, min_strength_pct=75)]]
    if tf in ("15m", "1h"):
        out += [[_f("trend", tf="4h", ema_n=50)], [_f("session", session="london")], [_f("session", session="ny")],
                [_f("trend", tf="1d", ema_n=50), _f("session", session="london")],
                [_f("trend", tf="1d", ema_n=50), _f("session", session="ny")]]
    if entry in BREAKOUTS:
        out += [[_f("compression", n=20, pct_max=20)]]
    return out


def exits(tf: str) -> list[tuple[dict[str, Any], int]]:
    an = 20 if tf == "1d" else 14
    out = [({"type": "target_r", "params": {"r": r}}, MAX_HOLD[tf]) for r in (1.5, 2.0, 3.0)]
    out += [({"type": "chandelier", "params": {"atr_n": an, "mult": m}}, MAX_HOLD[tf]) for m in (2.5, 3.0, 4.0)]
    out += [({"type": "time", "params": {"bars": b}}, b) for b in TIME_BARS[tf]]
    out += [({"type": "opposite_signal", "params": {}}, MAX_HOLD[tf])]
    return out


def entry_valid(entry: str, p: dict[str, Any], tf: str) -> bool:
    if entry in ("session_range_break", "opening_range_break"):
        return tf in ("15m", "1h") and not (entry == "opening_range_break" and tf == "1h" and p["minutes"] < 60)
    if entry == "prior_level_break":
        return {"day": tf in ("15m", "1h", "4h"), "week": tf in ("1h", "4h", "1d"), "month": tf in ("4h", "1d")}[p["level"]]
    if entry in LEVEL_ENTRIES:   # the zone's timeframe must be at least the entry timeframe
        order = ("15m", "1h", "4h", "1d", "1w")
        return order.index(p["min_tf"]) >= order.index(tf)
    if entry == "momentum_sign" and tf == "1d":
        return p["lookback_bars"] >= 24
    return True


# ---------------------------------------------------------------- cost arithmetic (prereg section 2)
def cost_cells() -> tuple[dict[str, tuple[float, float]], float]:
    sys.path.insert(0, str(ROOT))
    from goldbot.labels.triple_barrier import SwapSpec
    from goldbot.research.arms import load_costs
    costs = load_costs(ROOT / "config" / "arm_costs.yaml")
    labels = ["asia", "london", "newyork", ""]
    rt, ov = costs.screen_charges("icm", np.array(labels), COR_VERSION)   # pins the version (raises otherwise)
    # Amendment W2: the dollar cost of record becomes basis points of the entry price (/ COST_PRICE_REF); priced back
    # into explore-window dollars at EXPLORE_PRICE_REF. Swap: % a year of notional, same conversion.
    k = EXPLORE_PRICE_REF / COST_PRICE_REF
    cells = {lab or "dearest": (float(r) * k, float(o) * k) for lab, r, o in zip(labels, rt, ov, strict=True)}
    cells["pooled"] = (sum(cells[s][0] for s in ("asia", "london", "newyork")) / 3,
                       sum(cells[s][1] for s in ("asia", "london", "newyork")) / 3)
    swap = SwapSpec.shipped()
    long_night = max(0.0, -swap.long_usd_per_lot) / swap.contract_oz * k
    return cells, long_night


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def fire_rate(entry: str, p: dict[str, Any], tf: str) -> float:
    """Signals per bar (prior guess; no bar read)."""
    bpd = EXPLORE_BARS[tf] / 2050
    r: float
    if entry == "donchian_break":
        r = 1.0 / p["n"]
    elif entry == "ma_cross":
        r = 1.2 / p["slow"]
    elif entry == "ema_pullback":
        r = 0.03 / (1 + p["depth_atr"])
    elif entry in ("keltner_break", "bb_break"):
        r = {1.5: 0.06, 2.0: 0.025, 2.5: 0.01}[p["k"]]
    elif entry == "nr_breakout":
        r = 0.7 / p["n"]
    elif entry == "inside_bar_break":
        r = 0.07
    elif entry in ("session_range_break", "opening_range_break"):
        r = 0.7 / bpd
    elif entry == "prior_level_break":
        r = {"day": 0.6, "week": 0.5 / 5, "month": 0.5 / 21}[p["level"]] / bpd
    elif entry == "momentum_sign":
        r = 1.0
    elif entry == "consecutive_bars":
        r = 2 * 0.5 ** p["n"]
    elif entry == "atr_expansion":
        r = {1.5: 0.08, 2.0: 0.03, 2.5: 0.012}[p["mult"]]
    elif entry == "adx_onset":
        r = 0.02
    elif entry == "supertrend_flip":
        r = 0.02 * (3 / p["mult"]) * (10 / p["n"])
    elif entry == "macd_turn":
        r = 0.06 * 9 / p["signal"]
    elif entry == "rsi_threshold":
        r = {60: 0.04, 70: 0.02, 80: 0.01, 90: 0.03}[p["level"]] * (2.0 if p["n"] == 2 else 1.0)
    elif entry == "zscore":
        r = {1.5: 0.06, 2.0: 0.03, 2.5: 0.01}[p["z"]]
    else:   # level_break / level_reject
        r = {50: 0.02, 75: 0.012, 90: 0.006}[p["min_strength_pct"]] * \
            {"1h": 1.0, "4h": 0.7, "1d": 0.5, "1w": 0.3}[p["min_tf"]]
    return min(r, 1.0)


def is_fade(entry: str, p: dict[str, Any]) -> bool:
    return p.get("mode") == "fade" or entry == "level_reject"


@dataclass
class Cand:
    spec: dict[str, Any]
    raw: float
    cell: tuple[str, str]
    entry_key: str
    core_key: str
    pool: str
    key: str = field(default="")
    stop_key: str = field(default="")
    exit_key: str = field(default="")
    filter_key: str = field(default="")


def score(entry: str, p: dict[str, Any], tf: str, flt: list[dict[str, Any]], side: str, m: float,
          ex: dict[str, Any], hold_cap: int, cells: dict[str, tuple[float, float]], long_night: float) -> float:
    g_raw, grade = PRIOR[entry]
    fade = is_fade(entry, p)
    if fade:
        g_raw, grade = FADE_GROSS, "D"
    w = GRADE_W[grade]
    ftypes = {f["type"]: f["params"] for f in flt}
    exit_mult = 0.8 if fade and ex["type"] == "chandelier" else EXIT_GROSS[ex["type"]]
    g = SHRINK * g_raw * w * TF_GROSS[tf] * exit_mult
    bonus = 0.0
    if "trend" in ftypes:
        bonus += -0.02 if fade else 0.02
    if "adx_min" in ftypes:
        bonus += -0.01 if fade else 0.01
    if "compression" in ftypes and entry in BREAKOUTS:
        bonus += 0.02
    if "room_to_level" in ftypes and not fade:
        tr = ex["params"].get("r") if ex["type"] == "target_r" else None
        bonus += 0.015 if (tr is not None and ftypes["room_to_level"]["min_r"] >= tr) else 0.01
    if "near_level" in ftypes:
        bonus += 0.0
    g += bonus * w
    # cost per trade, USD
    atr = ATR_REF[tf]
    if "vol_regime" in ftypes:
        atr *= 1.35 if ftypes["vol_regime"]["pct_lo"] >= 50 else 0.7
    if "compression" in ftypes:
        atr *= 0.8
    if entry == "atr_expansion":
        atr *= 1.1
    stop = m * atr
    if "session" in ftypes:
        rt, ov = cells["newyork" if ftypes["session"]["session"] == "ny" else ftypes["session"]["session"]]
    elif entry in ("session_range_break", "opening_range_break"):
        s = p["session"]
        nxt = {"asia": "london", "london": "newyork", "ny": "newyork"}[s] if entry == "session_range_break" else \
            ("newyork" if s == "ny" else s)
        rt, ov = cells[nxt]
    else:
        rt, ov = cells["pooled"]
    if ex["type"] == "target_r":
        r = ex["params"]["r"]
        hold = 1.5625 * r * m * m
        p_stop = r / (1 + r)
        sd = math.sqrt(r)
    elif ex["type"] == "chandelier":
        hold = 2 * 1.5625 * m * ex["params"]["mult"]
        p_stop, sd = 1.0, 1.5
    elif ex["type"] == "time":
        hold = float(ex["params"]["bars"])
        p_stop = min(1.0, 2 * _phi(-1.25 * m / math.sqrt(hold)))
        sd = 1.0
    else:
        hold, p_stop, sd = 0.5 * hold_cap, 0.45, 1.5
    hold = min(hold, hold_cap)
    long_share = {"signal": 0.5, "long_only": 1.0, "short_only": 0.0}[side]
    nights = hold * TF_HOURS[tf] / 23.0
    swap_usd = long_night * 1.4 * nights * long_share          # 7 nights charged per 5 rollovers
    cost_r = (rt + ov * p_stop + swap_usd) / stop
    # expected event count
    keep = 1.0
    if "trend" in ftypes:
        keep *= 0.5
    if side != "signal":
        keep *= 0.5
    if "vol_regime" in ftypes:
        keep *= (ftypes["vol_regime"]["pct_hi"] - ftypes["vol_regime"]["pct_lo"]) / 100
    if "adx_min" in ftypes:
        keep *= 0.5
    if "compression" in ftypes:
        keep *= 0.3
    if "session" in ftypes:
        keep *= {"london": 0.25, "ny": 0.3, "asia": 0.4}[ftypes["session"]["session"]]
    if "weekday" in ftypes:
        keep *= len(ftypes["weekday"]["days"]) / 5
    if "room_to_level" in ftypes:
        keep *= 0.6 if ftypes["room_to_level"]["min_r"] <= 2 else 0.4
    if "near_level" in ftypes:
        keep *= 0.35
    bars = EXPLORE_BARS[tf]
    n = min(fire_rate(entry, p, tf) * keep * bars, bars / (hold + 1))
    t = (g - cost_r) / sd * math.sqrt(min(n, N_CAP))
    if n < 100:                                   # cannot pass explore
        t -= 3.0
    elif n < 150:
        t -= 1.0
    if n * OLD_TEST_SHARE < OLD_TEST_MIN[tf]:     # W2: likely too few trades to qualify on TEST-OLD
        t -= 1.5
    return t


def canon(spec: dict[str, Any]) -> str:
    body = {k: spec[k] for k in ("tf", "entry", "filters", "side", "stop", "exit", "max_hold_bars")}
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def pool_of(entry: str, p: dict[str, Any], flt: list[dict[str, Any]]) -> str:
    if PRIOR[entry][1] == "D" or is_fade(entry, p):
        return "null"
    if entry in LEVEL_ENTRIES or any(f["type"] in LEVEL_FILTERS for f in flt):
        return "level"
    return "main"


def candidates() -> list[Cand]:
    cells, long_night = cost_cells()
    out: list[Cand] = []
    for entry, grid in ENTRY_GRID.items():
        for p, tf in itertools.product(grid, TFS):
            if not entry_valid(entry, p, tf):
                continue
            an = 20 if tf == "1d" else 14
            for flt, side, m, (ex, cap) in itertools.product(filter_sets(tf, entry), SIDES, STOP_MULT[tf], exits(tf)):
                if is_fade(entry, p) and ex["type"] == "chandelier":
                    continue
                spec = {"family": FAMILY[entry], "tf": tf, "entry": {"type": entry, "params": dict(p)},
                        "filters": [dict(f) for f in flt], "side": side, "stop": {"atr_n": an, "atr_mult": m},
                        "exit": ex, "max_hold_bars": cap}
                raw = score(entry, p, tf, flt, side, m, ex, cap, cells, long_night)
                ek = f"{entry}|{tf}|{json.dumps(p, sort_keys=True)}"
                c = Cand(spec, raw, (FAMILY[entry], tf), ek, f"{ek}|{m}|{json.dumps(ex, sort_keys=True)}",
                         f"{pool_of(entry, p, flt)}:{side}")
                c.key = canon(spec)
                c.stop_key = f"{tf}|{m}"
                c.exit_key = f"{tf}|{ex['type']}"
                c.filter_key = "+".join(f["type"] for f in flt) or "none"
                out.append(c)
    return out


# diversity penalties (t units) and quotas (prereg section 4)
LAMBDA_CELL, LAMBDA_ENTRY, LAMBDA_CORE, LAMBDA_TYPE = 0.004, 0.12, 0.25, 0.001
LAMBDA_STOP, LAMBDA_EXIT, LAMBDA_FILTER, LAMBDA_SIDE = 0.0015, 0.0015, 0.0015, 0.01
# pool = evidence class : side class. The quotas sum to N_SPECS, so every pool is filled exactly. long_only and
# short_only get EQUAL quotas: no direction is favoured (gold's later price path is never used; prereg section 4).
QUOTA = {"main:signal": 2240, "main:long_only": 150, "main:short_only": 150,
         "level:signal": 280, "level:long_only": 30, "level:short_only": 30,
         "null:signal": 120, "null:long_only": 0, "null:short_only": 0}
CAP_TF = {"15m": 200}
CAP_ENTRY_TYPE = 400


def select(cands: list[Cand]) -> list[tuple[Cand, float]]:
    counts: dict[str, int] = {}

    def pen(c: Cand) -> float:
        et = c.spec["entry"]["type"]
        return (LAMBDA_CELL * counts.get("cell:" + "|".join(c.cell), 0) + LAMBDA_ENTRY * counts.get("e:" + c.entry_key, 0)
                + LAMBDA_CORE * counts.get("c:" + c.core_key, 0) + LAMBDA_TYPE * counts.get("t:" + et, 0)
                + LAMBDA_STOP * counts.get("st:" + c.stop_key, 0) + LAMBDA_EXIT * counts.get("x:" + c.exit_key, 0)
                + LAMBDA_FILTER * counts.get("f:" + c.filter_key, 0)
                + (LAMBDA_SIDE * counts.get("side:" + c.spec["side"], 0) if c.spec["side"] != "signal" else 0.0))

    def blocked(c: Cand, taken: dict[str, int]) -> bool:
        s = c.spec
        return (taken[c.pool] >= QUOTA[c.pool] or counts.get("tf:" + s["tf"], 0) >= CAP_TF.get(s["tf"], 10 ** 9)                or counts.get("t:" + s["entry"]["type"], 0) >= CAP_ENTRY_TYPE)

    heap = [(-c.raw, c.key, i) for i, c in enumerate(cands)]
    heapq.heapify(heap)
    taken = {k: 0 for k in QUOTA}
    chosen: list[tuple[Cand, float]] = []
    while heap and len(chosen) < N_SPECS:
        negv, key, i = heapq.heappop(heap)
        c = cands[i]
        if blocked(c, taken):
            continue
        cur = c.raw - pen(c)
        if cur < -negv - 1e-12:          # stale: penalties grew since it was pushed (lazy greedy)
            heapq.heappush(heap, (-cur, key, i))
            continue
        chosen.append((c, cur))
        taken[c.pool] += 1
        s = c.spec
        for k in ("cell:" + "|".join(c.cell), "e:" + c.entry_key, "c:" + c.core_key, "t:" + s["entry"]["type"],
                  "tf:" + s["tf"], "side:" + s["side"], "st:" + c.stop_key, "x:" + c.exit_key, "f:" + c.filter_key):
            counts[k] = counts.get(k, 0) + 1
    if len(chosen) != N_SPECS:
        raise SystemExit(f"only {len(chosen)} specs selectable")
    return chosen


def build() -> dict[str, Any]:
    chosen = select(candidates())
    specs = []
    for rank, (c, adj) in enumerate(chosen, start=1):
        s = c.spec
        specs.append({"id": f"S3K-{rank:04d}", "rank": rank, "prior_score": round(adj, 4), "family": s["family"],
                      "tf": s["tf"], "entry": s["entry"], "filters": s["filters"], "side": s["side"],
                      "stop": s["stop"], "exit": s["exit"], "max_hold_bars": s["max_hold_bars"]})
    digest = hashlib.sha256(json.dumps(specs, sort_keys=True).encode()).hexdigest()
    return {"version": "sprint3000-catalogue-v1", "prereg": "docs/research/sprint3000/prereg.md",
            "cost_of_record": COR_VERSION, "generator": "scripts/sprint3000_catalogue.py", "n_specs": len(specs),
            "specs_sha256": digest, "specs": specs}


def render(doc: dict[str, Any]) -> str:
    head = {k: v for k, v in doc.items() if k != "specs"}
    lines = ["# GENERATED by scripts/sprint3000_catalogue.py (prereg docs/research/sprint3000/prereg.md); do not edit.",
             "# Repeated entry/filters/stop/exit blocks are YAML anchors under `blocks` (yaml.safe_load expands every",
             "# alias, so each spec loads as the full schema); this keeps the file under the 800 KB repository limit.",
             yaml.safe_dump(head, sort_keys=False).rstrip(), "blocks:"]
    names: dict[tuple[str, str], str] = {}
    for s in doc["specs"]:
        for part, tag in (("entry", "e"), ("filters", "f"), ("stop", "s"), ("exit", "x")):
            key = (part, json.dumps(s[part], sort_keys=True))
            if key not in names:
                names[key] = f"{tag}{sum(1 for k in names if k[0] == part) + 1}"
                lines.append(f"  {names[key]}: &{names[key]} {_flow(s[part])}")
    lines.append("specs:")
    for s in doc["specs"]:
        row = {k: (f"*{names[(k, json.dumps(v, sort_keys=True))]}" if k in ("entry", "filters", "stop", "exit")
                   else v) for k, v in s.items()}
        lines.append("- " + _flow(row))
    text = "\n".join(lines) + "\n"
    if yaml.safe_load(text)["specs"] != doc["specs"]:     # the compact emitter must round-trip exactly
        raise SystemExit("compact YAML does not round-trip")
    return text


def _flow(v: Any) -> str:
    """Compact flow YAML (no quotes; every string here is a plain identifier) so 3000 specs stay under the
    repository's 800 KB file limit."""
    if isinstance(v, dict):
        return "{" + ",".join(f"{k}: {_flow(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ",".join(_flow(x) for x in v) + "]"
    if isinstance(v, bool) or v is None:
        raise TypeError(f"unexpected value {v!r}")
    return str(v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    text = render(build())
    if a.check:
        same = a.out.exists() and a.out.read_text() == text
        print("catalogue up to date" if same else "catalogue differs from a rebuild")
        return 0 if same else 1
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
