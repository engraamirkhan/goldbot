"""Count the events the pre-registrations owe from real data BEFORE their freeze, without looking at any outcome.

Owed counts (docs/research/preregistration-2027Q1.md, docs/research/preregistration-2027Q2-draft.md):
* H-04 asia_drift (Q1): entries 2010-01 .. 2025-09 and how many the 7-hour sample rule drops
  (`AsiaDriftSpecialist.candidates` + `complete_windows`, exactly the calls `research.pipeline.prepare` makes before
  labelling); with the calendar upper bound, the days without a decision bar and the ATR warm-up.
* H-01 slow TSMOM (Q1, recount on the current code; informational): daily signals by side
  (`TimeSeriesMomentumSpecialist.candidates_in_context`, preset `slow`) and the one-at-a-time lower bound with every
  trade held to its 124-bar time barrier.
* H-16 (Q2): the frozen first out-of-sample month (`h16.first_oos_month`) and `expected_eval_days`, the evaluation set
  of `h16.evaluate_h16` (finite target, not GVZ-stale, both forecasts present), counted on PLACEBO prices: the function
  receives the real 15m bar timestamps only and builds seeded random-walk prices on them, so whether a day is
  evaluable depends on the calendar, the data gaps, the GVZ dates and the calendar rows, never on a real price.
* H-17 (Q2): high days (H-16 baseline forecast >= the fit's median, `day_type.har_forecast`) with a qualifying noise
  breach in [09:00 London, 10:30 New York] from H-16's first OOS month, and the same rule on low days (shadow half).
* H-18 (Q2): tier-1 NFP/CPI/PCE/FOMC-statement events with the entry bar present, and how many pass |M| > 1.0 ATR_pre.
* H-14 (Q2): 15m candidate bars 08:00 London .. 16:00 New York after the tier-1 blackout and the H-13 cost gate,
  per year.

H-17, H-18 and H-14 are counted from the written rule because their code (`trend_day`, `event_continuation`, E-2) is
not merged yet: each says so in its `source`, and must be recounted by the specialist's own code before the freeze.

No outcome is read. The script imports no label, return or statistics function (tests/test_count_owed_events.py
checks the imports and calls by AST), never builds a label, and reads prices only up to each event's decision bar
(ATR, the noise boundary, the cost gate and M at the H-18 entry close are inputs known at the decision; the tests
perturb every price after an instant and require the same events before it). Bars, macro rows and calendar rows are
cut at `research.holdout_from` before anything is resampled or built: no holdout row is loaded.

  python scripts/fetch_data_release.py --download-only --macro --us-calendar
  python scripts/count_owed_events.py --bars raw --macro raw-macro --us-calendar raw-calendar \\
      --out-json owed_counts.json --out-md owed_counts.md
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.calendar import DEFAULT_SESSIONS  # noqa: E402
from goldbot.data.release import read_bar_files, read_macro_files, read_us_calendar_files  # noqa: E402
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.data.store import asof_join  # noqa: E402
from goldbot.data.timeutil import epoch_ns, utc_index  # noqa: E402
from goldbot.execution.costs import settings_extra_cost_usd  # noqa: E402
from goldbot.features import day_type as dt  # noqa: E402
from goldbot.features.mtf import TF_LABEL, context_tfs  # noqa: E402
from goldbot.features.technical import atr  # noqa: E402
from goldbot.research import h16  # noqa: E402
from goldbot.specialists import SPECIALISTS  # noqa: E402
from goldbot.specialists.asia_drift import CLOSED_DAYS, ENTRY_UTC  # noqa: E402

PLACEBO_SEED = 20261011
M15 = pd.Timedelta(minutes=15)
LONDON, NEW_YORK = "Europe/London", "America/New_York"
BLACKOUT_BEFORE, BLACKOUT_AFTER = pd.Timedelta(minutes=15), pd.Timedelta(minutes=30)   # E-1 tier-1 blackout
H17_FROM_LONDON, H17_TO_NY = 9 * 60, 10 * 60 + 30          # entry bar close in [09:00 London, 10:30 New York]
H17_OPEN_LONDON = 8 * 60                                   # O_D: open of the 15m bar starting 08:00 London
H17_NOISE_DAYS, H17_BREACH_K = 14, 1.0
H18_TYPES = ("nfp", "cpi", "pce", "fomc_statement")
H18_K = 1.0
H14_FROM_LONDON, H14_TO_NY = 8 * 60, 16 * 60               # candidate bar close in [08:00 London, 16:00 New York]
FROZEN_EXTRA_COST_USD = 0.37                               # Q1/Q2 pre-registrations: round trip beyond the spread
EXPECTED = {"H-04 calendar upper bound": 4087, "H-16 first OOS month": "2012-04", "H-16 eval days": 3380,
            "H-17 upper bound": 1690, "H-17 floor": 1000, "H-01 signals": 2154, "H-01 lower bound": 154}


# --------------------------------------------------------------------------- shared helpers
def cut_inputs(b1: pd.DataFrame, macro: pd.DataFrame | None, cal: pd.DataFrame | None,
               holdout_from: Any) -> tuple[pd.DataFrame, pd.DataFrame | None, pd.DataFrame | None]:
    """Every input cut at the holdout before anything is built (h16.cut_before_holdout: bars on ts_utc, macro on
    available_utc, calendar rows on available_utc and event_utc)."""
    return (h16.cut_before_holdout(b1, holdout_from),
            h16.cut_before_holdout(macro, holdout_from, ("available_utc",)),
            h16.cut_before_holdout(cal, holdout_from, ("available_utc", "event_utc")))


def per_year(ts: pd.DatetimeIndex) -> dict[str, int]:
    """Count per UTC calendar year of the instants."""
    if len(ts) == 0:
        return {}
    years, n = np.unique(np.asarray(ts.year), return_counts=True)
    return {str(int(y)): int(k) for y, k in zip(years, n, strict=True)}


def blackout_intervals(cal: pd.DataFrame | None) -> tuple[np.ndarray, np.ndarray]:
    """Tier-1 blackout windows [event - 15 min, event + 30 min) as known point in time: each calendar row holds from its
    available_utc until the event's next row; only rows in status `scheduled` black out. Returns (start_ns, end_ns)."""
    if cal is None or len(cal) == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    c = cal[cal["tier"].astype(int) == 1].copy()
    c["_av"] = epoch_ns(utc_index(c["available_utc"]))
    c["_ev"] = epoch_ns(utc_index(c["event_utc"]))
    c = c.sort_values(["event_id", "_av"], kind="stable")
    nxt = c.groupby("event_id", sort=False)["_av"].shift(-1).to_numpy(dtype=float)
    valid_to = np.full(len(nxt), np.iinfo(np.int64).max, dtype=np.int64)
    valid_to[~np.isnan(nxt)] = nxt[~np.isnan(nxt)].astype(np.int64)
    sched = (c["status"].astype(str) == "scheduled").to_numpy()
    lo = np.maximum(c["_ev"].to_numpy() - BLACKOUT_BEFORE.value, c["_av"].to_numpy())
    hi = np.minimum(c["_ev"].to_numpy() + BLACKOUT_AFTER.value, valid_to)
    keep = sched & (lo < hi)
    return lo[keep].astype(np.int64), hi[keep].astype(np.int64)


def in_intervals(t_ns: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """t in any [lo, hi) (t sorted ascending)."""
    out = np.zeros(len(t_ns) + 1, dtype=np.int64)
    if len(lo):
        np.add.at(out, np.searchsorted(t_ns, lo, side="left"), 1)
        np.add.at(out, np.searchsorted(t_ns, hi, side="left"), -1)
    return np.cumsum(out)[:-1] > 0


def _local(ts: pd.DatetimeIndex, tz: str) -> tuple[np.ndarray, np.ndarray]:
    """(minutes after local midnight, local date as datetime64[D]) per instant."""
    loc = ts.tz_convert(tz)
    return (np.asarray(loc.hour * 60 + loc.minute, dtype=np.int64),
            np.asarray(loc.tz_localize(None).normalize(), dtype="datetime64[D]"))


def first_per_group(keys: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Positions of the first True of `mask` per value of `keys` (rows in time order)."""
    pos = np.flatnonzero(mask)
    if len(pos) == 0:
        return pos
    _, first = np.unique(keys[pos], return_index=True)
    return np.sort(pos[first])


def _not_counted(reason: str, source: str) -> dict[str, Any]:
    return {"status": "not counted", "reason": reason, "source": source}


# --------------------------------------------------------------------------- H-04 asia_drift (Q1)
def count_h04(b1: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame]:
    """asia_drift entries on the cut bars: the specialist's own `candidates` (decision bar closing 00:00 UTC, market
    open, not 25 Dec / 1 Jan, ATR(14) known) and `complete_windows` (the 7-hour sample rule, bar timestamps only)."""
    spec = SPECIALISTS["asia_drift"]()
    dec = resample_bars(b1, spec.timeframe).reset_index(drop=True)
    m = mid(dec)
    cands = spec.candidates(m, pd.DataFrame(index=m.index))          # no atr14 column: atr(mid, 14), as the feature
    kept = spec.complete_windows(dec, cands)
    close = utc_index(dec["visible_at"])
    first, last = utc_index(dec["ts_utc"])[0].normalize(), close[-1].normalize()
    midnights = pd.date_range(first, last, freq="D", tz="UTC") + pd.Timedelta(hours=ENTRY_UTC.hour)
    closed = np.zeros(len(midnights), dtype=bool)
    for month, day in CLOSED_DAYS:
        closed |= np.asarray((midnights.month == month) & (midnights.day == day))
    calendar_days = midnights[DEFAULT_SESSIONS.is_open(midnights) & ~closed]
    full_bar = np.asarray(close - utc_index(dec["ts_utc"]) == pd.Timedelta(hours=1))
    has_bar = np.isin(epoch_ns(calendar_days), epoch_ns(close[full_bar]))
    entry_ts = close[cands["idx"].to_numpy(dtype=int)]
    kept_ts = close[kept["idx"].to_numpy(dtype=int)]
    # an entry whose 7-hour window ends after the last bar before the cut crosses the holdout: research drops it by
    # its exit time (pipeline.prepare), not by the sample rule, so it is counted apart
    hold = pd.Timedelta(hours=int(spec.config["max_bars"]) + 1)
    crosses = np.asarray(entry_ts + hold > close[-1])
    dropped = entry_ts[~crosses & ~np.isin(epoch_ns(entry_ts), epoch_ns(kept_ts))]
    summary = {
        "status": "counted",
        "source": f"repo code path: SPECIALISTS['asia_drift']() ({spec.agent_id}) .candidates + .complete_windows",
        "calendar_upper_bound": len(calendar_days),
        "no_decision_bar": int((~has_bar).sum()),
        "atr_warmup_or_unusable": int(has_bar.sum()) - len(cands),
        "candidates": len(cands),
        "crossing_the_holdout": int(crosses.sum()),
        "dropped_by_7h_sample_rule": len(dropped),
        "entries": len(kept),
        "dropped_dates": [f"{t:%Y-%m-%d}" for t in dropped],
        "per_year": per_year(kept_ts),
    }
    return summary, pd.DataFrame({"decision_utc": kept_ts})


# --------------------------------------------------------------------------- H-01 slow TSMOM recount (Q1)
def count_h01(b1: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame]:
    """Slow TSMOM daily signals (preset `slow`) in the research window (time barrier closes before the cut) and the
    one-at-a-time lower bound with every trade held to its time barrier (bar timestamps only)."""
    cls = SPECIALISTS["tsmom"]
    spec = cls(**cls.presets["slow"])
    dec = resample_bars(b1, spec.timeframe).reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(spec.timeframe)}
    m = mid(dec)
    cands = spec.candidates_in_context(m, pd.DataFrame(index=m.index), context)
    hold = int(spec.config["max_bars"]) + 1
    idx = cands["idx"].to_numpy(dtype=int)
    inside = idx + hold < len(dec)
    idx, side = idx[inside], cands["side"].to_numpy(dtype=int)[inside]
    close = epoch_ns(utc_index(dec["visible_at"]))
    busy, lower = -1, 0
    for i in idx:                                  # one_at_a_time's rule: enter only after the previous exit
        if close[i] > busy:
            lower += 1
            busy = close[i + hold]
    ts = utc_index(dec["visible_at"])[idx]
    summary = {"status": "counted",
               "source": f"repo code path: tsmom preset slow ({spec.agent_id}) .candidates_in_context",
               "signals": len(idx), "long": int((side == 1).sum()), "short": int((side == -1).sum()),
               "one_at_a_time_lower_bound": lower, "per_year": per_year(ts)}
    return summary, pd.DataFrame({"decision_utc": ts, "side": side})


# --------------------------------------------------------------------------- H-16 (Q2)
def placebo_bars(ts: pd.DatetimeIndex, seed: int = PLACEBO_SEED) -> pd.DataFrame:
    """Seeded random-walk OHLC on the given bar timestamps: the H-16 count's only prices (no real price is read)."""
    rng = np.random.default_rng(seed)
    n = len(ts)
    close = 1000.0 * np.exp(np.cumsum(0.001 * rng.standard_normal(n)))
    open_ = np.concatenate(([1000.0], close[:-1]))
    wiggle = np.abs(0.0005 * rng.standard_normal(n)) + 1e-4
    return pd.DataFrame({"ts_utc": ts, "open": open_, "high": np.maximum(open_, close) * (1 + wiggle),
                         "low": np.minimum(open_, close) * (1 - wiggle), "close": close})


def count_h16(ts15: pd.DatetimeIndex, macro: pd.DataFrame | None, cal: pd.DataFrame | None, *,
              min_rows: int = dt.MIN_FIT_ROWS) -> tuple[dict[str, Any], pd.DataFrame]:
    """H-16's frozen first OOS month and evaluable-day count from the 15m bar TIMESTAMPS (already cut at the holdout):
    the same evaluation set as `h16.evaluate_h16` (masks below mirror it; tests/test_count_owed_events.py checks the
    equality), on placebo prices, so only the calendar, data gaps, GVZ dates and calendar rows decide it."""
    inp = h16.evaluation_inputs(placebo_bars(ts15), macro, cal)
    refits = dt.monthly_refits(inp)
    month = h16.first_oos_month(inp, refits, min_rows=min_rows)
    if month is None:
        return {"status": "not counted", "reason": f"no month has a fit with >= {min_rows} usable rows",
                "source": "h16.first_oos_month"}, pd.DataFrame({"decision_utc": pd.DatetimeIndex([], tz="UTC")})
    base = {"first_oos_month": str(month), "min_fit_rows": int(min_rows)}
    if macro is None or len(macro) == 0 or cal is None or len(cal) == 0:
        why = "macro-v1 (GVZ) missing" if macro is None or len(macro) == 0 else "calendar-v1 missing"
        return {**base, "status": "partly counted", "reason": f"expected_eval_days needs GVZ and the calendar: {why}",
                "source": "h16.first_oos_month on placebo prices"}, pd.DataFrame(
                    {"decision_utc": pd.DatetimeIndex([], tz="UTC")})
    har = h16.oos_log_forecast(inp, h16.HAR_REGRESSORS, refits, min_rows=min_rows)
    full = h16.oos_log_forecast(inp, h16.FULL_REGRESSORS, refits, min_rows=min_rows)
    oos = np.asarray(pd.DatetimeIndex(inp["day"]).to_period("M") >= month) & np.isfinite(inp["y"].to_numpy(dtype=float))
    stale = oos & (inp["gvz_stale"].to_numpy(dtype=float) == 1.0)
    no_har = oos & ~stale & ~np.isfinite(har)
    no_full = oos & ~stale & ~no_har & ~np.isfinite(full)
    use = oos & ~stale & ~no_har & ~no_full
    missing = {c: int((no_full & ~np.isfinite(inp[c].to_numpy(dtype=float))).sum()) for c in h16.EXTRA_REGRESSORS}
    decision = utc_index(inp["decision_utc"])[use]
    summary = {**base, "status": "counted",
               "source": "h16.evaluation_inputs + first_oos_month + oos_log_forecast on placebo prices "
                         "(the evaluate_h16 masks)",
               "expected_eval_days": int(use.sum()), "n_days_oos": int(oos.sum()),
               "dropped_gvz_stale": int(stale.sum()), "dropped_no_har": int(no_har.sum()),
               "dropped_no_full": int(no_full.sum()), "dropped_no_full_by_input": missing,
               "h16_code_hash": h16.code_hash(), "day_type_version": dt.VERSION, "per_year": per_year(decision)}
    return summary, pd.DataFrame({"decision_utc": decision})


# --------------------------------------------------------------------------- H-17 (Q2, spec-derived)
def count_h17(b1: pd.DataFrame, cal: pd.DataFrame | None, first_month: str | None, *,
              min_rows: int = dt.MIN_FIT_ROWS) -> tuple[dict[str, Any], pd.DataFrame]:
    """High days (baseline forecast for D >= the fit's median of fitted values, as known at the bar) with a qualifying
    breach: the first 15m bar closing in [09:00 London, 10:30 New York] with |close - O_D| > 1.0 x B, B the mean of
    |close at the same London time - O| over the previous 14 days that had that bar (market open, ATR(14) known), from
    H-16's first OOS month. Literal rule (program decision): when that first breaching bar's close is inside a tier-1
    blackout [-15, +30) min the day has no entry (`*_events_strict`, the trial count); the next breaching bar outside
    the blackout (`*_events_next_bar`) is reported for transparency only. Low days the same (shadow half)."""
    source = "spec-derived (trend_day, E-3, not merged): preregistration-2027Q2-draft.md H-17"
    if cal is None or len(cal) == 0:
        return _not_counted("calendar-v1 missing (the tier-1 blackout is part of the rule)", source), pd.DataFrame()
    if first_month is None:
        return _not_counted("no H-16 first OOS month", source), pd.DataFrame()
    bars = resample_bars(b1, "15m").reset_index(drop=True)
    m = mid(bars)
    ts = utc_index(m["ts_utc"])
    close_ts = ts + M15
    t_ns = epoch_ns(close_ts)
    inp = dt.daily_inputs(dt.daily_bars(m))
    fc = dt.har_forecast(inp, min_rows=min_rows)
    emit = pd.DataFrame({"available_utc": utc_index(inp["close_utc"]), "hat": fc["hat"].to_numpy(),
                         "cut50": fc["cut50"].to_numpy()})
    probe = pd.DataFrame({"ts_utc": ts.as_unit("ns"), "_row": np.arange(len(ts))})
    j = asof_join(probe, emit).sort_values("_row")
    hat, cut50 = j["hat"].to_numpy(dtype=float), j["cut50"].to_numpy(dtype=float)
    known = np.isfinite(hat) & np.isfinite(cut50)
    high, low = known & (hat >= cut50), known & (hat < cut50)
    ls_min, ls_date = _local(ts, LONDON)
    lc_min, lc_date = _local(close_ts, LONDON)
    nc_min, nc_date = _local(close_ts, NEW_YORK)
    o_bar = ls_min == H17_OPEN_LONDON
    o_by_date = pd.Series(m["open"].to_numpy(dtype=float)[o_bar], index=ls_date[o_bar])
    o_by_date = o_by_date[~o_by_date.index.duplicated()]
    o_d = o_by_date.reindex(lc_date).to_numpy(dtype=float)
    after_open = (lc_date == ls_date) & (lc_min > H17_OPEN_LONDON)
    dev = np.where(after_open, np.abs(m["close"].to_numpy(dtype=float) - o_d), np.nan)
    f = pd.DataFrame({"tau": lc_min, "dev": dev})
    ok_dev = np.isfinite(dev)
    b = np.full(len(f), np.nan)
    b[ok_dev] = f[ok_dev].groupby("tau")["dev"].transform(
        lambda x: x.shift(1).rolling(H17_NOISE_DAYS, min_periods=H17_NOISE_DAYS).mean()).to_numpy()
    window = after_open & (lc_min >= H17_FROM_LONDON) & (nc_date == lc_date) & (nc_min <= H17_TO_NY)
    breach = window & np.isfinite(b) & (dev > H17_BREACH_K * b)
    lo, hi = blackout_intervals(cal)
    blackout = in_intervals(t_ns, lo, hi)
    a = atr(m, 14).to_numpy(dtype=float)
    tradable = DEFAULT_SESSIONS.is_open(close_ts) & np.isfinite(a) & (a > 0)
    start = np.datetime64(pd.Period(first_month, "M").start_time.date(), "D")
    in_oos = lc_date >= start
    out: dict[str, Any] = {"status": "counted", "source": source, "first_oos_month": first_month,
                           "rule": "literal: the day's first breaching bar; no entry that day if its close is in a "
                                   "tier-1 blackout [-15, +30) min (program decision after the quant review)"}
    times: list[np.ndarray] = []
    halves: list[str] = []
    rules: list[str] = []
    for half, day_ok in (("high", high), ("low", low)):
        eligible = breach & day_ok & tradable & in_oos
        first_breach = first_per_group(lc_date, eligible)
        strict = first_breach[~blackout[first_breach]]              # literal rule: blacked-out first breach = no entry
        next_bar = first_per_group(lc_date, eligible & ~blackout)   # old reading, reported for transparency only
        out[f"{half}_days"] = int(len(np.unique(lc_date[day_ok & window & in_oos])))
        out[f"{half}_breach_days"] = len(first_breach)
        out[f"{half}_first_breach_in_blackout"] = int(blackout[first_breach].sum())
        out[f"{half}_events_strict"] = len(strict)
        out[f"{half}_events_next_bar"] = len(next_bar)
        out[f"{half}_per_year"] = per_year(close_ts[strict])
        for rule, pos in (("strict", strict), ("next_bar", next_bar)):
            times.append(t_ns[pos])
            halves += [half] * len(pos)
            rules += [rule] * len(pos)
    out["trial_events"] = out["high_events_strict"]
    out["below_floor"] = out["high_events_strict"] < EXPECTED["H-17 floor"]
    frame = pd.DataFrame({"decision_utc": pd.to_datetime(np.concatenate(times), unit="ns", utc=True),
                          "half": halves, "rule": rules})
    return out, frame


# --------------------------------------------------------------------------- H-18 (Q2, spec-derived)
def h18_events(cal: pd.DataFrame) -> pd.DataFrame:
    """The scheduled tier-1 NFP/CPI/PCE/FOMC-statement events as known at their own time: a `scheduled` row stamped
    before its event and not superseded by a later row of the same event before the event."""
    c = cal[(cal["tier"].astype(int) == 1) & cal["event_type"].isin(H18_TYPES)].copy()
    c["_av"] = epoch_ns(utc_index(c["available_utc"]))
    c["_ev"] = epoch_ns(utc_index(c["event_utc"]))
    c = c.sort_values(["event_id", "_av"], kind="stable")
    nxt = c.groupby("event_id", sort=False)["_av"].shift(-1).to_numpy(dtype=float)
    current = np.isnan(nxt) | (nxt > c["_ev"].to_numpy(dtype=float))
    live = (c["status"].astype(str) == "scheduled").to_numpy() & (c["_av"] <= c["_ev"]).to_numpy() & current
    return c[live].sort_values("_ev", kind="stable").reset_index(drop=True)


def count_h18(b1: pd.DataFrame, cal: pd.DataFrame | None) -> tuple[dict[str, Any], pd.DataFrame]:
    """Events with the release bar (P0 = its open) and the entry bar (closing at release + 30 min) present, the first
    event of each New York day only, and how many pass |entry close - P0| > 1.0 x ATR(14) at the close of the bar
    before the release bar. Nothing after the entry bar's close is read."""
    source = "spec-derived (event_continuation, E-3, not merged): preregistration-2027Q2-draft.md H-18"
    if cal is None or len(cal) == 0:
        return _not_counted("calendar-v1 missing", source), pd.DataFrame()
    bars = resample_bars(b1, "15m").reset_index(drop=True)
    m = mid(bars)
    ts_ns = epoch_ns(utc_index(m["ts_utc"]))
    a = atr(m, 14).to_numpy(dtype=float)
    ev = h18_events(cal)
    ev = ev[ev["_ev"].to_numpy() <= (ts_ns[-1] if len(ts_ns) else 0)].reset_index(drop=True)
    ny_day = _local(utc_index(ev["event_utc"]), NEW_YORK)[1]
    first_of_day = ~pd.Series(ny_day).duplicated().to_numpy()
    e_ns = ev["_ev"].to_numpy()
    on_grid = e_ns % M15.value == 0
    p0 = np.searchsorted(ts_ns, e_ns)
    pe = np.searchsorted(ts_ns, e_ns + M15.value)
    has_p0 = (p0 < len(ts_ns)) & (ts_ns[np.minimum(p0, len(ts_ns) - 1)] == e_ns) & (p0 > 0)
    has_entry = (pe < len(ts_ns)) & (ts_ns[np.minimum(pe, len(ts_ns) - 1)] == e_ns + M15.value)
    base = first_of_day & on_grid & has_p0 & has_entry
    entry_close_ts = pd.to_datetime(ts_ns[np.minimum(pe, len(ts_ns) - 1)] + M15.value, unit="ns", utc=True)
    open_ = DEFAULT_SESSIONS.is_open(pd.DatetimeIndex(entry_close_ts))
    base &= open_
    pre_atr = np.where(base, a[np.maximum(p0 - 1, 0)], np.nan)
    move = np.where(base, m["close"].to_numpy(dtype=float)[np.minimum(pe, len(ts_ns) - 1)]
                    - m["open"].to_numpy(dtype=float)[np.minimum(p0, len(ts_ns) - 1)], np.nan)
    stop_ok = np.isfinite(a[np.minimum(pe, len(ts_ns) - 1)])
    passes = base & np.isfinite(pre_atr) & (pre_atr > 0) & stop_ok & (np.abs(move) > H18_K * pre_atr)
    entry_ts = pd.DatetimeIndex(entry_close_ts)
    out = {"status": "counted", "source": source, "calendar_events": len(ev),
           "skipped_later_same_day": int((~first_of_day).sum()), "off_15m_grid": int((first_of_day & ~on_grid).sum()),
           "with_entry_bar": int(base.sum()), "pass_move_filter": int(passes.sum()),
           "by_type_with_entry_bar": {t: int((base & (ev["event_type"] == t).to_numpy()).sum()) for t in H18_TYPES},
           "per_year_with_entry_bar": per_year(entry_ts[base]), "per_year_pass": per_year(entry_ts[passes])}
    return out, pd.DataFrame({"decision_utc": entry_ts[passes]})


# --------------------------------------------------------------------------- H-14 (Q2, spec-derived)
def count_h14(b1: pd.DataFrame, cal: pd.DataFrame | None, extra_cost_usd: float) -> tuple[dict[str, Any], pd.DataFrame]:
    """15m candidate bars closing in [08:00 London, 16:00 New York] (same local date), market open, outside the tier-1
    blackout, with ATR(14) known and the H-13 cost gate ATR(15m) >= round trip (the bar's own mean spread + the frozen
    extra cost). Blackout [-15, +30) min on the bar close. Two labelled rows per bar (both sides)."""
    source = ("spec-derived (E-2 not merged): preregistration-2027Q2-draft.md H-14; cost prior = the candidate bar's "
              "Dukascopy spread_mean (data-v1) + settings extra cost (no costs-v1 session spread read)")
    check = ("ok: 0.37 $/oz as frozen" if abs(float(extra_cost_usd) - FROZEN_EXTRA_COST_USD) < 1e-9 else
             f"DEVIATION: settings extra cost {float(extra_cost_usd):.4f} $/oz, frozen {FROZEN_EXTRA_COST_USD} $/oz")
    if cal is None or len(cal) == 0:
        return _not_counted("calendar-v1 missing (the tier-1 blackout is part of the rule)", source), pd.DataFrame()
    bars = resample_bars(b1, "15m").reset_index(drop=True)
    m = mid(bars)
    ts = utc_index(m["ts_utc"])
    close_ts = ts + M15
    lc_min, lc_date = _local(close_ts, LONDON)
    nc_min, nc_date = _local(close_ts, NEW_YORK)
    window = (lc_min >= H14_FROM_LONDON) & (nc_date == lc_date) & (nc_min <= H14_TO_NY)
    window &= DEFAULT_SESSIONS.is_open(close_ts) & np.asarray(close_ts - ts == M15)
    lo, hi = blackout_intervals(cal)
    blackout = in_intervals(epoch_ns(close_ts), lo, hi)
    a = atr(m, 14).to_numpy(dtype=float)
    spread = bars["spread_mean"].to_numpy(dtype=float)
    known = np.isfinite(a) & (a > 0) & np.isfinite(spread)
    gate = known & (a >= spread + extra_cost_usd)
    cand = window & ~blackout & gate
    out: dict[str, Any] = {"status": "counted", "source": source, "extra_cost_usd": round(float(extra_cost_usd), 4),
           "extra_cost_check": check, "window_bars": int(window.sum()), "dropped_blackout": int((window & blackout).sum()),
           "dropped_atr_unknown": int((window & ~blackout & ~known).sum()),
           "dropped_cost_gate": int((window & ~blackout & known & ~gate).sum()),
           "candidate_bars": int(cand.sum()), "labelled_rows": int(2 * cand.sum()),
           "per_year": per_year(close_ts[cand])}
    passing, eligible = per_year(close_ts[cand]), per_year(close_ts[window & ~blackout & known])
    out["per_year_share_passing_cost_gate"] = {y: round(passing.get(y, 0) / n, 3) for y, n in eligible.items()}
    return out, pd.DataFrame({"decision_utc": close_ts[cand]})


# --------------------------------------------------------------------------- report
def _git_sha() -> str:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
                              cwd=Path(__file__).resolve().parents[1]).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def markdown(result: dict[str, Any]) -> str:
    meta, c = result["meta"], result["counts"]

    def get(h: str, k: str) -> str:
        v = c.get(h, {}).get(k)
        return f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else ("n/a" if v is None else str(v))

    rows = [
        ("Q1 H-04", "asia_drift entries 2010-01 .. 2025-09", get("H-04", "entries"), c["H-04"].get("source", "")),
        ("Q1 H-04", "dropped by the 7h sample rule (exit - entry != 7 h)", get("H-04", "dropped_by_7h_sample_rule"), ""),
        ("Q1 H-04", "calendar upper bound / no decision bar / ATR warm-up",
         f"{get('H-04', 'calendar_upper_bound')} / {get('H-04', 'no_decision_bar')} / "
         f"{get('H-04', 'atr_warmup_or_unusable')}", ""),
        ("Q1 H-01", "slow TSMOM signals (long / short), recount", f"{get('H-01', 'signals')} ({get('H-01', 'long')} / "
         f"{get('H-01', 'short')})", c["H-01"].get("source", "")),
        ("Q1 H-01", "one-at-a-time lower bound (124-bar hold)", get("H-01", "one_at_a_time_lower_bound"), ""),
        ("Q2 H-16", "frozen first OOS month (`first_oos_month`)", get("H-16", "first_oos_month"),
         c["H-16"].get("source", "")),
        ("Q2 H-16", "`expected_eval_days`", get("H-16", "expected_eval_days"), c["H-16"].get("reason", "")),
        ("Q2 H-16", "dropped: GVZ-stale / no HAR / no full forecast",
         f"{get('H-16', 'dropped_gvz_stale')} / {get('H-16', 'dropped_no_har')} / {get('H-16', 'dropped_no_full')}", ""),
        ("Q2 H-17", "high-day events, literal rule (trial count; floor 1,000)", get("H-17", "high_events_strict"),
         c["H-17"].get("source", c["H-17"].get("reason", ""))),
        ("Q2 H-17", "low-half (shadow) events, literal rule", get("H-17", "low_events_strict"), ""),
        ("Q2 H-17", "high / low taking the next bar after a blacked-out first breach (transparency only)",
         f"{get('H-17', 'high_events_next_bar')} / {get('H-17', 'low_events_next_bar')}", ""),
        ("Q2 H-18", "events with the entry bar present", get("H-18", "with_entry_bar"),
         c["H-18"].get("source", c["H-18"].get("reason", ""))),
        ("Q2 H-18", "of which pass abs(M) > 1.0 ATR_pre", get("H-18", "pass_move_filter"), ""),
        ("Q2 H-14", "candidate bars after blackout and cost gate (rows = 2x)", get("H-14", "candidate_bars"),
         c["H-14"].get("source", c["H-14"].get("reason", ""))),
        ("Q2 H-14", "cost prior: bar spread_mean + settings extra cost", get("H-14", "extra_cost_check"), ""),
    ]
    lines = ["## Pre-registration event counts (no labels, returns or outcomes read)", "",
             f"- commit `{meta['git_sha']}`; bars {meta['bars_from']} .. {meta['bars_to']} (cut before holdout "
             f"{meta['holdout_from']}); macro: {meta['macro']}; calendar: {meta['us_calendar']}",
             "- spec-derived rows (H-17, H-18, H-14) must be recounted with the specialist's own code before the freeze",
             "", "| Pre-registration | Count owed | Value | Source |", "|---|---|---|---|"]
    lines += [f"| {a} | {b} | {v} | {s} |" for a, b, v, s in rows]
    lines += ["", "### Per year", "", "| Count | " + " | ".join(str(y) for y in range(2010, 2026)) + " |",
              "|---|" + "---|" * 16]
    for label, h, k in (("H-04 entries", "H-04", "per_year"), ("H-01 signals", "H-01", "per_year"),
                        ("H-16 eval days", "H-16", "per_year"), ("H-17 high", "H-17", "high_per_year"),
                        ("H-17 low", "H-17", "low_per_year"), ("H-18 pass", "H-18", "per_year_pass"),
                        ("H-14 bars", "H-14", "per_year")):
        py = c.get(h, {}).get(k) or {}
        lines.append(f"| {label} | " + " | ".join(f"{py.get(str(y), 0):,}" for y in range(2010, 2026)) + " |")
    dropped = c["H-04"].get("dropped_dates") or []
    lines += ["", f"H-04 entries dropped by the sample rule ({len(dropped)}): " + (", ".join(dropped) or "none"), ""]
    return "\n".join(lines)


def run(b1: pd.DataFrame, macro: pd.DataFrame | None, cal: pd.DataFrame | None, *, holdout_from: Any,
        extra_cost_usd: float, min_rows: int = dt.MIN_FIT_ROWS) -> dict[str, Any]:
    """Every owed count on inputs cut at the holdout (module docstring)."""
    b1, macro, cal = cut_inputs(b1, macro, cal, holdout_from)
    if len(b1) == 0:
        raise SystemExit("no bars before the holdout")
    ts15 = utc_index(resample_bars(b1, "15m")["ts_utc"])
    h16_out, _ = count_h16(ts15, macro, cal, min_rows=min_rows)
    counts = {"H-04": count_h04(b1)[0], "H-01": count_h01(b1)[0], "H-16": h16_out,
              "H-17": count_h17(b1, cal, h16_out.get("first_oos_month"), min_rows=min_rows)[0],
              "H-18": count_h18(b1, cal)[0], "H-14": count_h14(b1, cal, extra_cost_usd)[0]}
    ts = utc_index(b1["ts_utc"])
    meta = {"git_sha": _git_sha(), "generated_utc": pd.Timestamp.now("UTC").isoformat(),
            "bars_from": f"{ts[0]:%Y-%m-%d %H:%M}", "bars_to": f"{ts[-1]:%Y-%m-%d %H:%M}", "n_bars_1m": len(b1),
            "holdout_from": str(holdout_from), "min_fit_rows": int(min_rows),
            "macro": "absent" if macro is None or len(macro) == 0 else f"{len(macro):,} rows",
            "us_calendar": "absent" if cal is None or len(cal) == 0 else f"{len(cal):,} rows",
            "expected_from_docs": EXPECTED,
            "outcomes_read": "none: no label, return or price after a decision bar (tests/test_count_owed_events.py)"}
    return {"meta": meta, "counts": counts}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--bars", default="raw")
    ap.add_argument("--macro", default="raw-macro", help="folder with the macro-v1 Parquet files (optional)")
    ap.add_argument("--us-calendar", default="raw-calendar", help="folder with the calendar-v1 Parquet (optional)")
    ap.add_argument("--from-year", type=int, default=2010)
    ap.add_argument("--out-json", default="owed_counts.json")
    ap.add_argument("--out-md", default="owed_counts.md")
    args = ap.parse_args()
    settings = load_settings()
    holdout_from = settings.research.holdout_from
    if holdout_from is None:
        raise SystemExit("no research.holdout_from configured: the counts are cut at the holdout")
    try:
        b1 = read_bar_files(Path(args.bars), args.from_year, holdout_from.year)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from None
    macro = read_macro_files(Path(args.macro))
    cal = read_us_calendar_files(Path(args.us_calendar))
    result = run(b1, macro if len(macro) else None, cal if len(cal) else None, holdout_from=holdout_from,
                 extra_cost_usd=settings_extra_cost_usd(settings))
    Path(args.out_json).write_text(json.dumps(result, indent=2, default=str))
    text = markdown(result)
    Path(args.out_md).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
