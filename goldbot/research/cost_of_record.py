"""Build the cost of record (config/cost_of_record.yaml, docs/research/cost-of-record-2026-10-31.md) from the
exploration ledger: per broker x session, spread, entry and exit slippage, commission, the round trip and the
stop-exit overshoot, each with n and a 90% interval. Read-only on aux.db; writes aggregates only (no trade row, no
account, no host).

    python -m goldbot.research.cost_of_record --state-dir state --data-root <store> \
        --from-day 2026-10-26 --to-day 2026-10-30 --host icm-demo=icm --host vantage-demo=vantage \
        --version cor-2026-10-31.2 --out config/cost_of_record.yaml

Definitions (every one in USD per oz, adverse positive):
* population: filled exploration market orders sent in the window; order ids void for `cost_cells`
  (`evidence_exclusions`) are dropped from the entries, the closes, the stop exits and the exit legs alike;
* spread = ask - bid at send; entry slip = side x (fill - requested); exit slip = side x (requested - filled) on the
  store's `exit_fills` legs of exploration agents; a broker with no exit leg in a session reads its own scheduled
  probes (`cost_probes`, purpose cost_measurement): side x (exit touch - exit price) (ADR 0043: never another broker);
* commission = |booked commission| / (lots x contract x account-per-USD rate), on the window's closes (XAUUSD: 100 oz);
* round trip = spread + max(0, entry slip) + max(0, exit slip) + commission, pooled first and floored after;
* overshoot = side x ((requested - side x stop_usd) - exit price) on stop exits at the INITIAL stop: exits of an arm
  that trails its stop (`TRAILING_ARM_PREFIXES`) and exits more than `MOVED_STOP_USD` better than the initial stop
  (a moved stop: break-even or trailed) are counted apart, never in the mean. A cell whose stop exits fill at the stop
  price (|overshoot| < `EXACT_STOP_USD` on `EXACT_STOP_SHARE` or more of the broker's pooled stop exits, at least
  `MIN_STOPS_FOR_TRUST`) is a demo artefact: `trusted` false on every cell of that broker;
* intervals: 5th-95th percentile of a bootstrap that resamples whole minutes (fills on the same bar are correlated):
  entries cluster on their send minute, closes, stop exits and exit legs on their exit minute; spread and entry slip
  share their resample.
* per instrument (multi-instrument plan 2026-11-02: "costs per (account, symbol)", never pooled with XAUUSD): a fill's
  instrument is its ledger row's `symbol` (a row before it is XAUUSD) and an exit leg's the store's `symbol` column when
  it has one; XAUUSD cells keep their broker key (`brk`), another instrument's are `brk:<instrument>`, in units of its
  own price (USD per oz reads "profit currency per unit" there) and its own contract.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.timeutil import epoch_ns
from goldbot.instruments import GOLD, contract_size, registry

SESSIONS = tuple(DEFAULT_SESSIONS.sessions_local)
ALL = "all"
STOP_EXITS = frozenset({"stop", "engine_stop_close"})
TRAILING_ARM_PREFIXES = ("BXR",)      # H-80 runners: break-even at +1 R, then trailed (engine.exploration.trail)
MOVED_STOP_USD = 0.50                 # an exit this far better than the initial stop was at a moved stop
EXACT_STOP_USD = 0.005
EXACT_STOP_SHARE = 0.95
MIN_STOPS_FOR_TRUST = 20
RESAMPLES = 4000
SEED = 20261031
MINUTE_NS = 60_000_000_000
COLUMNS = {"entries": ["broker", "symbol", "session", "minute", "spread", "slip"],
           "closes": ["broker", "symbol", "session", "minute", "comm", "stop", "over", "moved"],
           "legs": ["broker", "symbol", "session", "minute", "xslip"],
           "probes": ["broker", "symbol", "session", "minute", "xslip"]}


def cell_key(broker: str, symbol: str) -> str:
    """The table's key of one broker's cells of one instrument: the broker for XAUUSD (unchanged), else broker:key."""
    return broker if symbol == GOLD else f"{broker}:{symbol}"


def _contract(symbol: str) -> float:
    """The instrument's units per lot; NaN for one the registry no longer lists (its commission is then unread, never
    spread over another instrument's contract, and the build goes on)."""
    try:
        return contract_size(symbol)
    except KeyError:
        return float("nan")


def _leg_symbols(df: pd.DataFrame, broker: str) -> list[str]:
    """Each exit leg's instrument key: the store's `symbol` column resolved on the broker, XAUUSD without one; a name
    the registry does not know stays as it is (its own cell, never another instrument's)."""
    if "symbol" not in df.columns:
        return [GOLD] * len(df)
    reg = registry()
    return [(spec.key if (spec := reg.resolve(str(x), broker) or reg.resolve(str(x))) is not None else str(x))
            for x in df["symbol"]]


@dataclass(frozen=True)
class Frames:
    entries: pd.DataFrame
    closes: pd.DataFrame
    legs: pd.DataFrame
    probes: pd.DataFrame
    n_void: int


def _labels(ts: Sequence[pd.Timestamp]) -> list[str]:
    if not ts:
        return []
    idx = pd.DatetimeIndex(pd.to_datetime(list(ts), utc=True))
    return [str(x) if o else "" for x, o in zip(DEFAULT_SESSIONS.session_label(idx), DEFAULT_SESSIONS.is_open(idx),
                                                 strict=True)]


def _minute(ts: Sequence[pd.Timestamp]) -> list[int]:
    if not ts:
        return []
    return [int(x) // MINUTE_NS for x in epoch_ns(pd.DatetimeIndex(pd.to_datetime(list(ts), utc=True)))]


def _frame(rows: dict[str, list[Any]], kind: str) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=COLUMNS[kind])


def read_frames(conn: sqlite3.Connection, exit_legs: Mapping[str, pd.DataFrame], hosts: Mapping[str, str],
                lo: pd.Timestamp, hi: pd.Timestamp) -> Frames:
    """The window [lo, hi) of `hosts` (account -> broker): entries by send time, closes and exit legs by exit time."""
    from goldbot.engine import cost_probe
    from goldbot.engine.exploration import LEDGER
    from goldbot.research.evidence_exclusions import excluded
    void = excluded(conn, "cost_cells")
    a, b = int(lo.value), int(hi.value)
    marks = ",".join("?" * len(hosts))
    sent = [t for t in LEDGER.select(conn, f"account_id IN ({marks}) AND ts_ns >= ? AND ts_ns < ?", (*hosts, a, b))
            if t.fill_price is not None and t.requested_price is not None]
    n_void = sum(t.client_order_id in void for t in sent)
    sent = [t for t in sent if t.client_order_id not in void]
    closed = [t for t in LEDGER.select(conn, f"account_id IN ({marks}) AND closed = 1 AND exit_ns >= ? AND exit_ns < ?",
                                       (*hosts, a, b))
              if t.fill_price is not None and t.client_order_id not in void]
    entries = _frame({"broker": [hosts[t.account_id] for t in sent], "symbol": [t.symbol for t in sent],
                      "session": _labels([t.sent_utc for t in sent]),
                      "minute": _minute([t.sent_utc for t in sent]),
                      "spread": [np.nan if t.spread_usd is None else float(t.spread_usd) for t in sent],
                      "slip": [t.side * (float(t.fill_price or 0.0) - float(t.requested_price or 0.0)) for t in sent]},
                     "entries")
    comm, over, moved = [], [], []
    for t in closed:
        contract = _contract(t.symbol)
        per_oz = t.lots * contract * float(t.fx_acct_per_usd) if t.fx_acct_per_usd and t.lots > 0 else np.nan
        comm.append(abs(float(t.commission)) / per_oz if t.commission is not None else np.nan)
        o, m = np.nan, False
        if t.exit_reason in STOP_EXITS and t.requested_price is not None and t.exit_price is not None and t.stop_usd:
            o = t.side * ((float(t.requested_price) - t.side * float(t.stop_usd)) - float(t.exit_price))
            m = t.arm_id.startswith(TRAILING_ARM_PREFIXES) or o < -MOVED_STOP_USD
        over.append(np.nan if m else o)
        moved.append(m)
    ex_ts = [t.exit_utc or t.sent_utc for t in closed]
    closes = _frame({"broker": [hosts[t.account_id] for t in closed], "symbol": [t.symbol for t in closed],
                     "session": _labels(ex_ts), "minute": _minute(ex_ts),
                     "comm": comm, "stop": [t.exit_reason in STOP_EXITS for t in closed], "over": over, "moved": moved},
                    "closes")
    leg_rows: dict[str, list[Any]] = {k: [] for k in COLUMNS["legs"]}
    for host, df in exit_legs.items():
        if host not in hosts or df is None or df.empty:
            continue
        df = df[df["agent_id"].astype(str).str.startswith("explore:")].dropna(subset=["requested", "filled"])
        if "kind" in df.columns:
            df = df[df["kind"].astype(str) != "target"]    # a target is a limit fill: it never offsets a stop's cost
        if "client_order_id" in df.columns:
            df = df[~df["client_order_id"].astype(str).isin(void)]
        ts = pd.to_datetime(df["ts_utc"], utc=True)
        df = df[((ts >= lo) & (ts < hi)).to_numpy()]
        stamps = list(pd.to_datetime(df["ts_utc"], utc=True))
        leg_rows["broker"] += [hosts[host]] * len(df)
        leg_rows["symbol"] += _leg_symbols(df, hosts[host])
        leg_rows["session"] += _labels(stamps)
        leg_rows["minute"] += _minute(stamps)
        leg_rows["xslip"] += list(df["side"].to_numpy(dtype=float) * (df["requested"].to_numpy(dtype=float)
                                                                       - df["filled"].to_numpy(dtype=float)))
    probes = [p for p in cost_probe.LEDGER.select(conn, f"account_id IN ({marks}) AND ts_ns >= ? AND ts_ns < ?",
                                                  (*hosts, a, b))
              if p.purpose == "cost_measurement" and p.client_order_id not in void and p.exit_price is not None
              and p.exit_bid is not None and p.exit_ask is not None]
    pr = _frame({"broker": [hosts[p.account_id] for p in probes], "symbol": [GOLD] * len(probes),   # gold probes only
                 "session": _labels([p.sent_utc for p in probes]),
                 "minute": _minute([p.sent_utc for p in probes]),
                 "xslip": [p.side * ((float(p.exit_bid or 0.0) if p.side > 0 else float(p.exit_ask or 0.0)) - float(p.exit_price or 0.0))
                           for p in probes]}, "probes")
    return Frames(entries=entries, closes=closes, legs=_frame(leg_rows, "legs"), probes=pr, n_void=n_void)


def clustered_means(values: np.ndarray, clusters: np.ndarray, rng: np.random.Generator, resamples: int = RESAMPLES,
                    idx: np.ndarray | None = None) -> np.ndarray:
    """`resamples` bootstrap means of `values`, resampling whole clusters (sum over drawn clusters / their count).
    `idx` (resamples x n_clusters, into the sorted unique clusters) shares one draw between columns of one table."""
    v = np.asarray(values, dtype=float)
    keep = ~np.isnan(v)
    v, c = v[keep], np.asarray(clusters)[keep]
    if len(v) == 0:
        return np.full(resamples, np.nan)
    keys, inv = np.unique(c, return_inverse=True)
    sums = np.bincount(inv, weights=v, minlength=len(keys))
    counts = np.bincount(inv, minlength=len(keys)).astype(float)
    draw = idx if idx is not None else rng.integers(0, len(keys), size=(resamples, len(keys)))
    n = counts[draw].sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.asarray(sums[draw].sum(axis=1) / n)


def _stat(values: pd.Series, boot: np.ndarray) -> dict[str, Any]:
    v = values.dropna().to_numpy(dtype=float)
    ok = boot[~np.isnan(boot)]
    lo, hi = (float(np.percentile(ok, 5)), float(np.percentile(ok, 95))) if len(v) > 1 and len(ok) else \
        ((float(v.mean()),) * 2 if len(v) else (0.0, 0.0))
    return {"n": int(len(v)), "mean": float(v.mean()) if len(v) else 0.0,
            "p90": float(np.percentile(v, 90)) if len(v) else 0.0, "ci90": (lo, hi)}


def _cell(fr: Frames, broker: str, session: str, rng: np.random.Generator, resamples: int,
          symbol: str = GOLD) -> dict[str, Any] | None:
    def sel(df: pd.DataFrame) -> pd.DataFrame:
        m = (df["broker"] == broker) & (df["symbol"] == symbol)
        return df[m if session == ALL else m & (df["session"] == session)]
    e, c, x, p = sel(fr.entries), sel(fr.closes), sel(fr.legs), sel(fr.probes)
    if e.empty:
        return None
    keys = np.unique(e["minute"].to_numpy())
    draw = rng.integers(0, len(keys), size=(resamples, len(keys)))
    sp_b = clustered_means(e["spread"].to_numpy(), e["minute"].to_numpy(), rng, resamples, draw)
    en_b = clustered_means(e["slip"].to_numpy(), e["minute"].to_numpy(), rng, resamples, draw)
    if len(x):
        src, legs = "strategy_exit_legs", x
    elif len(p):
        src, legs = "scheduled_probes", p
    else:
        src, legs = "none", x
    ex_b = clustered_means(legs["xslip"].to_numpy(), legs["minute"].to_numpy(), rng, resamples) if len(legs) \
        else np.zeros(resamples)
    cm_b = clustered_means(c["comm"].to_numpy(), c["minute"].to_numpy(), rng, resamples) if len(c) \
        else np.zeros(resamples)
    stops = c[c["stop"].astype(bool) & ~c["moved"].astype(bool)]
    ov_b = clustered_means(stops["over"].to_numpy(), stops["minute"].to_numpy(), rng, resamples) if len(stops) \
        else np.zeros(resamples)
    sp, en, ex = _stat(e["spread"], sp_b), _stat(e["slip"], en_b), _stat(legs["xslip"], ex_b)
    cm, ov = _stat(c["comm"], cm_b), _stat(stops["over"], ov_b)
    rt_b = sp_b + np.maximum(en_b, 0.0) + np.maximum(ex_b, 0.0) + cm_b
    rt_b = rt_b[~np.isnan(rt_b)]
    rt = sp["mean"] + max(en["mean"], 0.0) + max(ex["mean"], 0.0) + cm["mean"]
    o = stops["over"].dropna().to_numpy(dtype=float)
    exact = len(o) >= MIN_STOPS_FOR_TRUST and float(np.mean(np.abs(o) < EXACT_STOP_USD)) >= EXACT_STOP_SHARE
    return {"n_fills": sp["n"], "n_minutes": int(len(keys)), "spread_usd": sp["mean"], "spread_p90_usd": sp["p90"],
            "spread_ci90": sp["ci90"], "entry_slip_usd": en["mean"], "entry_slip_ci90": en["ci90"],
            "n_exit_legs": ex["n"], "exit_slip_usd": ex["mean"], "exit_slip_ci90": ex["ci90"], "exit_slip_source": src,
            "commission_usd": cm["mean"], "round_trip_usd": rt,
            "round_trip_ci90": (min(float(np.percentile(rt_b, 5)), rt), max(float(np.percentile(rt_b, 95)), rt))
            if len(rt_b) else (rt, rt),
            "n_stop_exits": ov["n"], "n_moved_stop_exits": int(c["moved"].astype(bool).sum()),
            "overshoot_usd": ov["mean"], "overshoot_ci90": ov["ci90"], "overshoot_trusted": not exact}


def _gold_by_default(fr: Frames) -> Frames:
    """Frames built without a `symbol` column (before the multi-instrument plan) are XAUUSD's."""
    def tag(df: pd.DataFrame) -> pd.DataFrame:
        return df if "symbol" in df.columns else df.assign(symbol=GOLD)
    return Frames(entries=tag(fr.entries), closes=tag(fr.closes), legs=tag(fr.legs), probes=tag(fr.probes),
                  n_void=fr.n_void)


def build_table(fr: Frames, *, version: str, window_utc: tuple[str, str], seed: int = SEED,
                resamples: int = RESAMPLES) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    fr = _gold_by_default(fr)
    cells: dict[str, dict[str, Any]] = {}
    pairs = sorted(set(zip(fr.entries["broker"], fr.entries["symbol"], strict=True)),
                   key=lambda bs: (bs[1] != GOLD, bs[0], bs[1]))    # every gold cell first: its draws as before
    for broker, symbol in pairs:
        row = {s: c for s in (*SESSIONS, ALL) if (c := _cell(fr, broker, s, rng, resamples, symbol)) is not None}
        if ALL in row:
            # stops filled at the stop price are the broker's demo server, not a session: the pooled cell decides
            for c in row.values():
                c["overshoot_trusted"] = row[ALL]["overshoot_trusted"]
            cells[cell_key(broker, symbol)] = row
    return {"version": version, "window_utc": list(window_utc),
            "source": f"exploration fills (aux.db exploration_trades), market orders, evidence exclusions removed "
                      f"({fr.n_void} fills)",
            "interval": f"bootstrap clustered by minute (entries: send minute; closes and exit legs: exit minute), "
                        f"{resamples} resamples, seed {seed}",
            "cells": cells}


HEADER = """# The cost of record (docs/research/cost-of-record-2026-10-31.md): what one 0.01-lot market round trip costs per oz on
# each broker's demo, by session, from its own fills. Every research screen reads it through
# goldbot.research.arms.load_costs (arm_costs.yaml `cost_of_record`); a pre-registration pins `version`.
# GENERATED by `python -m goldbot.research.cost_of_record` (definitions in its docstring); do not edit by hand.
# USD per oz (= per 0.01 lot), adverse positive. Aggregates only: no trade row, account or host.
"""


def _num(x: float) -> str:
    return f"{round(float(x), 3) + 0.0:g}"


def render_yaml(table: dict[str, Any]) -> str:
    out = [HEADER.rstrip("\n"), f"version: {table['version']}",
           f"window_utc: [\"{table['window_utc'][0]}\", \"{table['window_utc'][1]}\"]",
           f"source: {json.dumps(table['source'])}", f"interval: {json.dumps(table['interval'])}", "cells:"]
    for broker, row in table["cells"].items():
        out.append(f"  {broker}:")
        for session, c in row.items():
            parts = []
            for k, v in c.items():
                if isinstance(v, bool):
                    parts.append(f"{k}: {'true' if v else 'false'}")
                elif isinstance(v, tuple):
                    parts.append(f"{k}: [{_num(v[0])}, {_num(v[1])}]")
                elif isinstance(v, int):
                    parts.append(f"{k}: {v}")
                elif isinstance(v, float):
                    parts.append(f"{k}: {_num(v)}")
                else:
                    parts.append(f"{k}: {v}")
            out.append(f"    {session}: {{{', '.join(parts)}}}")
    return "\n".join(out) + "\n"


def _day_bounds(first: str, last: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    from goldbot.ops.report_days import day_bounds
    return day_bounds(first)[0], day_bounds(last)[1]


# ---------------------------------------------------------------- v3: the fill-to-research-bar cost (ADR 0064 amended)
# What a screen must charge is what the broker took BEYOND the research replay: research P&L is measured on Dukascopy 1m
# mid bars, so the cost of a fill is the replay's gross less the broker's booked net, not spread + floored slippage on
# the broker's own quote (v2). why-strategies-fail-2026-11-15 section 1: v2 charges ICM $0.30-0.32/oz where the replay
# measures $0.116 [0.093, 0.140] on 993 random-control fills.
#
#     python -m goldbot.research.cost_of_record fill-to-bar --state-dir state --bars <xauusd_1m_dukascopy_2026.parquet> \
#         --from-day 2026-10-26 --to-day 2026-11-06 --host <account>=icm ... --host <account>=vantage \
#         --version cor-2026-11-15.3 --out config/cost_of_record_v3.yaml
#
# Definitions (USD per oz, adverse positive):
# * population: closed gold exploration fills sent in the window (pyramid adds and `cost_cells` exclusions dropped)
#   whose send minute has a bar within FTB_MATCH_S and whose exit is before the bars end, plus closed scheduled cost
#   probes (`cost_measurement`: a market round trip, priced as a time exit);
# * replay (the research mechanics): entry at the send minute's mid open; stop and target on mid high/low, stop first; a
#   minute opening through the stop fills at its open; a time exit at the mid close of the minute before the cut (the
#   arm's signal + hold, or the broker's exit minute when the broker closed by hold/time/weekend). A probe exits at the
#   nearest minute boundary of its own exit;
# * cost = replay gross - broker net + swap (the screen charges swap per rollover itself, never twice);
#   cost_with_swap = replay gross - broker net (the parity table's definition, kept for the acceptance check);
# * EVERY replayed fill is priced in the cell of its RESEARCH barrier (stop / target / time), whatever the broker's
#   exit: pricing only agreeing fills selects on the outcome (quant review of 89e69db3). `n_unmatched` counts the
#   fills whose broker exit disagreed, for information;
# * target exits: favourable slippage against the order's own level is not bankable for real money and is floored per
#   fill at 0: cost += max(0, side x (exit - anchor) - target_usd), anchor = the quote the bracket was priced from
#   (requested price; the fill when none). The demo evidence for that anchor: Vantage target exits sit at exactly
#   requested + target, ICM's 5-95% band against it is 4x narrower than against the fill. A real-money target exit then
#   costs commission + entry slippage against that quote + any adverse fill at the level;
# * interval: mean +/- t(G-1, 0.975) x the wider of the CR1 and CR2 day-clustered (opportunity day, 22:00 UTC)
#   standard errors; the charge is max(commission, the upper bound over every fill in the research barrier, the upper
#   bound over the fills whose broker exit agreed): a favourable disagreement is never credited, an adverse one is
#   always charged; a cell with fewer than FTB_MIN_FILLS
#   fills or FTB_MIN_DAYS days is `sparse` and charges at least its broker's pooled cell of that exit;
# * stops_trusted: false when FTB_EXACT_SHARE or more of a broker's stop exits fill within EXACT_STOP_USD of the stop
#   level (a demo artefact: Vantage); the screen then adds the 0.70 stress (arms.OVERSHOOT_STRESS_USD) to stop exits;
# * geometry: the window's median ATR14 at 1m / 5m / 15m, so a pre-registration sees what a 1-ATR stop pays in R.
FTB_EXITS = ("stop", "target", "time")
FTB_MIN_FILLS = 30
FTB_MIN_DAYS = 5
FTB_EXACT_SHARE = 0.95
FTB_SHORT_TFS = ("1m", "5m", "15m")
FTB_MATCH_S = 120
FTB_TAIL_MIN = 5
OZ_PER_LOT = 100.0
FTB_COLUMNS = ["cid", "broker", "sent", "exit", "broker_exit", "matched", "probe", "control", "cost_usd", "raw_cost_usd",
               "cost_with_swap_usd", "commission_usd", "stop_exact"]


@dataclass(frozen=True)
class MidBars:
    ts: pd.DatetimeIndex
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray


def mid_bars(b1: pd.DataFrame) -> MidBars:
    """Dukascopy 1m bid/ask bars as mid OHLC (the research price)."""
    def mid(k: str) -> np.ndarray:
        return np.asarray(((b1[f"bid_{k}"] + b1[f"ask_{k}"]) / 2).to_numpy(dtype=float))
    return MidBars(ts=pd.DatetimeIndex(pd.to_datetime(b1["ts_utc"], utc=True)), open=mid("open"), high=mid("high"),
                   low=mid("low"), close=mid("close"))


def replay(b: MidBars, k0: int, side: int, stop: float, target: float | None,
           t_end: pd.Timestamp) -> tuple[float, float, str]:
    """(entry, exit, barrier) of one fill under the research mechanics from minute `k0`; barrier "open" when the bars
    end first."""
    e = float(b.open[k0])
    sl = e - side * stop
    tp = e + side * target if target and np.isfinite(target) else None
    k1 = int(b.ts.searchsorted(t_end, side="left"))
    for k in range(k0, max(k1, k0 + 1)):
        if k >= len(b.open):
            return e, float("nan"), "open"
        lo, hi = b.low[k], b.high[k]
        if (lo <= sl) if side > 0 else (hi >= sl):
            o = float(b.open[k])
            return e, (o if side * (o - sl) < 0 else sl), "stop"
        if tp is not None and ((hi >= tp) if side > 0 else (lo <= tp)):
            return e, tp, "target"
    k = max(k0, min(k1, len(b.open)) - 1)     # a cut inside a bar gap: never a bar before the entry
    return e, float(b.close[k]), "time"


def _broker_exit(reason: str | None) -> str:
    return reason if reason in ("stop", "target") else "time"


def _days(ts: Sequence[pd.Timestamp]) -> list[str]:
    from goldbot.data.timeutil import opportunity_day
    if not ts:
        return []
    return [f"{d:%Y-%m-%d}" for d in opportunity_day(pd.DatetimeIndex(pd.to_datetime(list(ts), utc=True)))]


def read_fill_to_bar(conn: sqlite3.Connection, hosts: Mapping[str, str], lo: pd.Timestamp,
                     hi: pd.Timestamp) -> tuple[list[Any], list[Any]]:
    """The closed gold exploration fills and closed scheduled probes of `hosts` sent in [lo, hi): void (`cost_cells`)
    orders, pyramid adds and rows without the prices or P&L a replay needs are dropped."""
    from goldbot.engine import cost_probe
    from goldbot.engine.exploration import LEDGER
    from goldbot.research.evidence_exclusions import excluded
    void = excluded(conn, "cost_cells")
    marks = ",".join("?" * len(hosts))
    args = (*hosts, int(lo.value), int(hi.value))
    fills = [t for t in LEDGER.select(conn, f"account_id IN ({marks}) AND closed = 1 AND ts_ns >= ? AND ts_ns < ?", args)
             if t.client_order_id not in void and t.symbol == GOLD and not t.group_id and t.pnl is not None
             and t.fill_price is not None and t.exit_price is not None and t.exit_utc is not None and t.stop_usd
             and t.fx_acct_per_usd and t.lots > 0]
    probes = [p for p in cost_probe.LEDGER.select(conn, f"account_id IN ({marks}) AND ts_ns >= ? AND ts_ns < ?", args)
              if p.purpose == "cost_measurement" and p.client_order_id not in void and p.pnl is not None
              and p.fill_price is not None and p.exit_price is not None and p.exit_utc is not None
              and p.fx_acct_per_usd and p.lots > 0 and (getattr(p, "symbol", None) or GOLD) == GOLD]
    return fills, probes


def _minute_of(b: MidBars, ts: pd.Timestamp) -> int | None:
    m = ts.floor("min")
    k = int(b.ts.searchsorted(m, side="left"))
    if k >= len(b.ts) or abs((b.ts[k] - m).total_seconds()) > FTB_MATCH_S:
        return None
    return k


def fill_to_bar_rows(fills: Sequence[Any], probes: Sequence[Any], b: MidBars, hosts: Mapping[str, str]) -> pd.DataFrame:
    """One row per replayed fill: broker, session (of the send), opportunity day, research exit, whether the broker's
    exit agreed, and the cost columns (the v3 definitions above)."""
    last = b.ts[-1] - pd.Timedelta(minutes=FTB_TAIL_MIN) if len(b.ts) else None
    rows: list[dict[str, Any]] = []
    for t in fills:
        sent, ex = pd.Timestamp(t.sent_utc), pd.Timestamp(t.exit_utc)
        if last is None or ex >= last or (k0 := _minute_of(b, sent)) is None:
            continue
        side, stop = int(t.side), float(t.stop_usd)
        tgt = float(t.target_usd) if t.target_usd else float("nan")
        brk = _broker_exit(t.exit_reason)
        t_end = pd.Timestamp(t.signal_utc) + pd.Timedelta(seconds=float(t.hold_s)) if t.hold_s else ex
        if brk == "time":
            t_end = ex.ceil("min")          # the broker cut it (hold, time, weekend, broker): replay to that minute
        e, x, bar = replay(b, k0, side, stop, tgt, t_end)
        if bar == "open":
            continue
        oz, fx = float(t.lots) * OZ_PER_LOT, float(t.fx_acct_per_usd)
        with_swap = side * (x - e) - float(t.pnl) / fx / oz
        raw = with_swap + float(t.swap or 0.0) / fx / oz
        anchor = float(t.requested_price if t.requested_price is not None else t.fill_price)
        lucky = max(0.0, side * (float(t.exit_price) - anchor) - tgt) if brk == "target" and np.isfinite(tgt) else 0.0
        exact = brk == "stop" and min(abs(float(t.exit_price) - (a - side * stop))
                                      for a in (anchor, float(t.fill_price))) < EXACT_STOP_USD
        rows.append({"cid": t.client_order_id, "broker": hosts[t.account_id], "sent": sent, "exit": bar,
                     "broker_exit": brk, "matched": bar == brk, "probe": False, "control": t.evidence_class == "control",
                     "cost_usd": raw + lucky, "raw_cost_usd": raw, "cost_with_swap_usd": with_swap,
                     "commission_usd": abs(float(t.commission or 0.0)) / fx / oz, "stop_exact": exact})
    for p in probes:
        sent, ex = pd.Timestamp(p.sent_utc), pd.Timestamp(p.exit_utc)
        if last is None or ex >= last or (k0 := _minute_of(b, sent)) is None or (kx := _minute_of(b, ex)) is None:
            continue
        side = int(p.side)
        x = float(b.close[kx]) if ex.second >= 30 else float(b.open[kx])
        oz, fx = float(p.lots) * OZ_PER_LOT, float(p.fx_acct_per_usd)
        with_swap = side * (x - float(b.open[k0])) - float(p.pnl) / fx / oz
        raw = with_swap + float(p.swap or 0.0) / fx / oz
        rows.append({"cid": p.client_order_id, "broker": hosts[p.account_id], "sent": sent, "exit": "time",
                     "broker_exit": "time", "matched": True, "probe": True, "control": False, "cost_usd": raw, "raw_cost_usd": raw,
                     "cost_with_swap_usd": with_swap, "commission_usd": abs(float(p.commission or 0.0)) / fx / oz,
                     "stop_exact": False})
    out = pd.DataFrame(rows, columns=FTB_COLUMNS)
    stamps = list(out["sent"])
    out["session"] = _labels(stamps)
    out["day"] = _days(stamps)
    return out


def day_clustered_ci(values: np.ndarray, days: np.ndarray, level: float = 0.95) -> tuple[float, float, float]:
    """(mean, lo, hi): the mean +/- t(G-1) x the wider of two cluster-robust standard errors over G days: CR1
    (G/(G-1) corrected) and CR2 (Bell-McCaffrey; for a mean, sum_g S_g^2 / (1 - n_g / n) / n^2), so one large day
    never narrows the charge (quant review of 89e69db3). A single day has no interval (lo = hi = mean)."""
    from scipy import stats
    v = np.asarray(values, dtype=float)
    m = float(v.mean())
    keys, inv = np.unique(np.asarray(days), return_inverse=True)
    g = len(keys)
    if g < 2:
        return m, m, m
    sums = np.bincount(inv, weights=v, minlength=g)
    counts = np.bincount(inv, minlength=g).astype(float)
    n = len(v)
    s2 = (sums - counts * m) ** 2
    se1 = float(np.sqrt(g / (g - 1) * s2.sum()) / n)                                 # CR1
    lev = counts / n
    se2 = float(np.sqrt((s2 / (1.0 - lev)).sum()) / n) if (lev < 1).all() else se1   # CR2 (Bell-McCaffrey)
    se = max(se1, se2)
    q = float(stats.t.ppf(0.5 + level / 2, g - 1))
    return m, m - q * se, m + q * se


def _ftb_cell(x: pd.DataFrame) -> dict[str, Any]:
    """One cell from every fill whose research barrier is its exit (`x`): charged = max(commission, the upper bound over
    all of them, the upper bound over those whose broker exit agreed). A disagreement in our favour is never credited;
    one against us is always charged (quant ruling on 0df6b07f)."""
    m, lo, hi = day_clustered_ci(x["cost_usd"].to_numpy(), x["day"].to_numpy())
    agree = x[x["matched"].astype(bool)]
    mm, mlo, mhi = day_clustered_ci(agree["cost_usd"].to_numpy(), agree["day"].to_numpy()) if len(agree) \
        else (m, lo, hi)
    comm = float(x["commission_usd"].mean())
    n_days = int(x["day"].nunique())
    return {"n_fills": int(len(x)), "n_days": n_days, "n_probes": int(x["probe"].astype(bool).sum()),
            "n_unmatched": int(len(x) - len(agree)), "mean_usd": m, "raw_mean_usd": float(x["raw_cost_usd"].mean()),
            "ci95": (lo, hi), "mean_matched_usd": mm, "ci95_matched": (mlo, mhi), "commission_usd": comm,
            "charged_usd": max(comm, hi, mhi), "sparse": len(x) < FTB_MIN_FILLS or n_days < FTB_MIN_DAYS}


def build_fill_to_bar(rows: pd.DataFrame, *, version: str, window_utc: tuple[str, str],
                      atr14: Mapping[str, float]) -> dict[str, Any]:
    """The v3 table from `fill_to_bar_rows`: broker -> session (asia, london, newyork, all) -> exit (stop, target, time,
    all) -> cell; a sparse cell charges at least its broker's pooled cell of the same exit."""
    cells: dict[str, dict[str, dict[str, Any]]] = {}
    trusted: dict[str, bool] = {}
    checks: dict[str, Any] = {}
    for broker in sorted(set(rows["broker"])):
        rb = rows[rows["broker"] == broker]
        ok = rb["matched"].astype(bool)
        mb = rb[ok]
        row: dict[str, dict[str, Any]] = {}
        for session in (*SESSIONS, ALL):     # every replayed fill, in its RESEARCH barrier's cell (quant review)
            ms = rb if session == ALL else rb[rb["session"] == session]
            got: dict[str, Any] = {}
            for ex in (*FTB_EXITS, ALL):
                x = ms if ex == ALL else ms[ms["exit"] == ex]
                if not x.empty:
                    got[ex] = _ftb_cell(x)
            if got:
                row[session] = got
        pooled = row.get(ALL, {})
        for session, got in row.items():
            for ex, c in got.items():
                if c["sparse"] and session != ALL and ex in pooled:
                    c["charged_usd"] = max(c["charged_usd"], pooled[ex]["charged_usd"])
        cells[broker] = row
        bx = rb["broker_exit"] if "broker_exit" in rb.columns else rb["exit"]
        stops = rb[(bx == "stop").to_numpy()]
        trusted[broker] = not (len(stops) >= MIN_STOPS_FOR_TRUST
                               and float(stops["stop_exact"].astype(bool).mean()) >= FTB_EXACT_SHARE)
        for suffix, pop in (("", rb), ("_matched", mb)):     # what v3 prices; the parity table's definition
            ctl = pop[pop["control"].astype(bool) & ~pop["probe"].astype(bool)]
            if len(ctl):
                m, lo, hi = day_clustered_ci(ctl["cost_with_swap_usd"].to_numpy(), ctl["day"].to_numpy())
                checks[f"{broker}_controls_parity{suffix}"] = {"n_fills": int(len(ctl)),
                                                               "n_days": int(ctl["day"].nunique()),
                                                               "mean_usd": m, "ci95": (lo, hi)}
    return {"version": version, "basis": "fill_to_research_bar", "window_utc": list(window_utc),
            "source": "closed gold exploration fills (aux.db exploration_trades; pyramid adds and cost_cells exclusions "
                      "dropped) and scheduled cost probes (cost_probes, cost_measurement), replayed on Dukascopy 1m "
                      "mid bars",
            "research_bars": "Dukascopy 1m mid; entry at the send minute's mid open; stop/target on mid high/low, stop "
                             "first; a minute opening through the stop fills at its open; time exit at the mid close "
                             "before the cut",
            "interval": f"95% t(G-1) interval on the wider of the CR1 and CR2 standard errors clustered by session day "
                        f"(22:00 UTC); charged = max(commission, upper bound over every fill in its research "
                        f"barrier, upper bound over the fills whose broker exit agreed); sparse (< {FTB_MIN_FILLS} fills or < {FTB_MIN_DAYS} "
                        f"days) charges at least the broker's pooled exit cell",
            "target_slippage": "floored at 0 per fill against the order's own level, requested price + target (demo "
                               "price improvement is not bankable for real money)",
            "stops_trusted": trusted, "checks": checks,
            "geometry": {"atr14_median_usd": {k: float(v) for k, v in atr14.items()}},
            "cells": cells}


def refresh_due(table: Mapping[str, Any], rows: pd.DataFrame) -> list[str]:
    """ADR 0084 point 6: the cells ("broker/session/exit") whose realised mean on `rows` (`fill_to_bar_rows` of a later
    window of ten session days) exceeds the table's charge. Any entry forces a new version (.N+1)."""
    out: list[str] = []
    for broker, sessions in table["cells"].items():
        rb = rows[rows["broker"] == broker]
        for session, got in sessions.items():
            rs = rb if session == ALL else rb[rb["session"] == session]
            for ex, c in got.items():
                x = rs if ex == ALL else rs[rs["exit"] == ex]
                if len(x) and float(x["cost_usd"].mean()) > float(c["charged_usd"]):
                    out.append(f"{broker}/{session}/{ex}")
    return out


def atr14_medians(b: MidBars, lo: pd.Timestamp, hi: pd.Timestamp) -> dict[str, float]:
    """The window's median ATR14 (simple mean of the true range) at each of FTB_SHORT_TFS, on mid bars."""
    df = pd.DataFrame({"open": b.open, "high": b.high, "low": b.low, "close": b.close}, index=b.ts)
    df = df[(df.index >= lo - pd.Timedelta(days=2)) & (df.index < hi)]
    out: dict[str, float] = {}
    for tf in FTB_SHORT_TFS:
        r = df.resample(tf.replace("m", "min"), label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        prev = r["close"].shift(1)
        tr = pd.concat([r["high"] - r["low"], (r["high"] - prev).abs(), (r["low"] - prev).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        out[tf] = float(atr[(atr.index >= lo) & (atr.index < hi)].median())
    return out


FTB_HEADER = """# The cost of record v3 (ADR 0084, amending ADR 0064): what the broker takes BEYOND the
# research replay, USD per oz (= per 0.01 lot), by broker x session x exit type, from broker-reconciled demo fills
# replayed on the research bars. Charged only to NEW pre-registrations that pin this version
# (arms.ArmCosts.screen_charges); lines pinned to v2 keep reading config/cost_of_record.yaml.
# GENERATED by `python -m goldbot.research.cost_of_record fill-to-bar` (definitions in its source); do not edit by hand.
# Adverse positive. Aggregates only: no trade row, account or host.
"""


def _flow(d: Mapping[str, Any]) -> str:
    parts = []
    for k, v in d.items():
        if isinstance(v, bool):
            parts.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, tuple):
            parts.append(f"{k}: [{_num(v[0])}, {_num(v[1])}]")
        elif isinstance(v, int):
            parts.append(f"{k}: {v}")
        elif isinstance(v, float):
            parts.append(f"{k}: {_num(v)}")
        else:
            parts.append(f"{k}: {v}")
    return "{" + ", ".join(parts) + "}"


def render_fill_to_bar_yaml(table: dict[str, Any]) -> str:
    out = [FTB_HEADER.rstrip("\n"), f"version: {table['version']}", f"basis: {table['basis']}",
           f"window_utc: [\"{table['window_utc'][0]}\", \"{table['window_utc'][1]}\"]"]
    out += [f"{k}: {json.dumps(table[k])}" for k in ("source", "research_bars", "interval", "target_slippage")]
    out.append("stops_trusted: " + _flow(table["stops_trusted"]))
    out.append("checks:")
    out += [f"  {k}: {_flow(c)}" for k, c in table["checks"].items()]
    out += ["geometry:", "  atr14_median_usd: " + _flow(table["geometry"]["atr14_median_usd"]), "cells:"]
    for broker, row in table["cells"].items():
        out.append(f"  {broker}:")
        for session, got in row.items():
            out.append(f"    {session}:")
            out += [f"      {ex}: {_flow(c)}" for ex, c in got.items()]
    return "\n".join(out) + "\n"


FTB_PAD = pd.Timedelta(days=3)          # bars read either side of the window (send-minute matching, ATR warm-up)


def load_research_bars(path: str | Path, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    """The research 1m bars of [lo - FTB_PAD, hi + FTB_PAD), read with the window pushed down to the file. The holdout
    (exploration_data HOLDOUT_START .. HOLDOUT_END, exclusive) is never read: a padded window that reaches it is
    refused before the file is opened, and a holdout row that comes back anyway stops the build."""
    from goldbot.research.exploration_data import HOLDOUT_END, HOLDOUT_START
    a, z = lo - FTB_PAD, hi + FTB_PAD
    if a < HOLDOUT_END and z > HOLDOUT_START:
        raise ValueError(f"bar window {a} .. {z} reaches the holdout ({HOLDOUT_START.date()} .. {HOLDOUT_END.date()})")
    b1 = pd.read_parquet(path, filters=[("ts_utc", ">=", a), ("ts_utc", "<", z)])
    ts = pd.to_datetime(b1["ts_utc"], utc=True)
    if bool(((ts >= HOLDOUT_START) & (ts < HOLDOUT_END)).any()):
        raise ValueError("holdout bars came back from the bar file")
    return b1[((ts >= a) & (ts < z)).to_numpy()].reset_index(drop=True)


def main_fill_to_bar(argv: Sequence[str]) -> None:
    ap = argparse.ArgumentParser(description="Build the cost of record v3 (fill-to-research-bar)")
    ap.add_argument("--state-dir", required=True, help="the folder holding aux.db (opened read-only)")
    ap.add_argument("--bars", required=True, help="the research 1m bars (Dukascopy bid/ask parquet, local only)")
    ap.add_argument("--from-day", required=True)
    ap.add_argument("--to-day", required=True)
    ap.add_argument("--host", action="append", required=True, help="account=broker, repeatable")
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", help="the YAML to write (default: stdout)")
    args = ap.parse_args(argv)
    hosts = dict(h.split("=", 1) for h in args.host)
    lo, hi = _day_bounds(args.from_day, args.to_day)
    b = mid_bars(load_research_bars(args.bars, lo, hi))
    conn = sqlite3.connect(f"file:{Path(args.state_dir) / 'aux.db'}?mode=ro", uri=True)
    try:
        fills, probes = read_fill_to_bar(conn, hosts, lo, hi)
    finally:
        conn.close()
    rows = fill_to_bar_rows(fills, probes, b, hosts)
    end = min(hi, b.ts[-1] + pd.Timedelta(minutes=1)) if len(b.ts) else hi
    table = build_fill_to_bar(rows, version=args.version, window_utc=(lo.isoformat(), end.isoformat()),
                              atr14=atr14_medians(b, lo, end))
    text = render_fill_to_bar_yaml(table)
    if args.out:
        Path(args.out).write_text(text)
    else:
        print(text, end="")


def main(argv: Sequence[str] | None = None) -> None:
    import sys
    rest = list(sys.argv[1:] if argv is None else argv)
    if rest[:1] == ["fill-to-bar"]:
        main_fill_to_bar(rest[1:])
        return
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--state-dir", required=True, help="the folder holding aux.db (opened read-only)")
    ap.add_argument("--data-root", help="the store root for exit_fills (none: probe exits only)")
    ap.add_argument("--from-day", required=True)
    ap.add_argument("--to-day", required=True)
    ap.add_argument("--host", action="append", required=True, help="account=broker, repeatable")
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", help="the YAML to write (default: stdout)")
    args = ap.parse_args(rest)
    hosts = dict(h.split("=", 1) for h in args.host)
    lo, hi = _day_bounds(args.from_day, args.to_day)
    legs: dict[str, pd.DataFrame] = {}
    if args.data_root:
        from goldbot.data.store import Store
        store = Store(args.data_root)
        for host in hosts:
            df = store.read("exit_fills", source=host, symbol=None, start=lo, end=hi)   # Vantage: XAUUSD+
            legs[host] = df if df is not None else pd.DataFrame()
    conn = sqlite3.connect(f"file:{Path(args.state_dir) / 'aux.db'}?mode=ro", uri=True)
    try:
        fr = read_frames(conn, legs, hosts, lo, hi)
    finally:
        conn.close()
    text = render_yaml(build_table(fr, version=args.version, window_utc=(lo.isoformat(), hi.isoformat())))
    if args.out:
        Path(args.out).write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
