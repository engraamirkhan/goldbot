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
* commission = |booked commission| / (lots x 100 x account-per-USD rate), on the window's closes;
* round trip = spread + max(0, entry slip) + max(0, exit slip) + commission, pooled first and floored after;
* overshoot = side x ((requested - side x stop_usd) - exit price) on stop exits at the INITIAL stop: exits of an arm
  that trails its stop (`TRAILING_ARM_PREFIXES`) and exits more than `MOVED_STOP_USD` better than the initial stop
  (a moved stop: break-even or trailed) are counted apart, never in the mean. A cell whose stop exits fill at the stop
  price (|overshoot| < `EXACT_STOP_USD` on `EXACT_STOP_SHARE` or more of the broker's pooled stop exits, at least
  `MIN_STOPS_FOR_TRUST`) is a demo artefact: `trusted` false on every cell of that broker;
* intervals: 5th-95th percentile of a bootstrap that resamples whole minutes (fills on the same bar are correlated):
  entries cluster on their send minute, closes, stop exits and exit legs on their exit minute; spread and entry slip
  share their resample.
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
COLUMNS = {"entries": ["broker", "session", "minute", "spread", "slip"],
           "closes": ["broker", "session", "minute", "comm", "stop", "over", "moved"],
           "legs": ["broker", "session", "minute", "xslip"],
           "probes": ["broker", "session", "minute", "xslip"]}


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
    entries = _frame({"broker": [hosts[t.account_id] for t in sent], "session": _labels([t.sent_utc for t in sent]),
                      "minute": _minute([t.sent_utc for t in sent]),
                      "spread": [np.nan if t.spread_usd is None else float(t.spread_usd) for t in sent],
                      "slip": [t.side * (float(t.fill_price or 0.0) - float(t.requested_price or 0.0)) for t in sent]},
                     "entries")
    comm, over, moved = [], [], []
    for t in closed:
        per_oz = t.lots * 100.0 * float(t.fx_acct_per_usd) if t.fx_acct_per_usd and t.lots > 0 else np.nan
        comm.append(abs(float(t.commission)) / per_oz if t.commission is not None else np.nan)
        o, m = np.nan, False
        if t.exit_reason in STOP_EXITS and t.requested_price is not None and t.exit_price is not None and t.stop_usd:
            o = t.side * ((float(t.requested_price) - t.side * float(t.stop_usd)) - float(t.exit_price))
            m = t.arm_id.startswith(TRAILING_ARM_PREFIXES) or o < -MOVED_STOP_USD
        over.append(np.nan if m else o)
        moved.append(m)
    ex_ts = [t.exit_utc or t.sent_utc for t in closed]
    closes = _frame({"broker": [hosts[t.account_id] for t in closed], "session": _labels(ex_ts), "minute": _minute(ex_ts),
                     "comm": comm, "stop": [t.exit_reason in STOP_EXITS for t in closed], "over": over, "moved": moved},
                    "closes")
    leg_rows: dict[str, list[Any]] = {k: [] for k in COLUMNS["legs"]}
    for host, df in exit_legs.items():
        if host not in hosts or df is None or df.empty:
            continue
        df = df[df["agent_id"].astype(str).str.startswith("explore:")].dropna(subset=["requested", "filled"])
        if "client_order_id" in df.columns:
            df = df[~df["client_order_id"].astype(str).isin(void)]
        ts = pd.to_datetime(df["ts_utc"], utc=True)
        df = df[((ts >= lo) & (ts < hi)).to_numpy()]
        stamps = list(pd.to_datetime(df["ts_utc"], utc=True))
        leg_rows["broker"] += [hosts[host]] * len(df)
        leg_rows["session"] += _labels(stamps)
        leg_rows["minute"] += _minute(stamps)
        leg_rows["xslip"] += list(df["side"].to_numpy(dtype=float) * (df["requested"].to_numpy(dtype=float)
                                                                       - df["filled"].to_numpy(dtype=float)))
    probes = [p for p in cost_probe.LEDGER.select(conn, f"account_id IN ({marks}) AND ts_ns >= ? AND ts_ns < ?",
                                                  (*hosts, a, b))
              if p.purpose == "cost_measurement" and p.client_order_id not in void and p.exit_price is not None
              and p.exit_bid is not None and p.exit_ask is not None]
    pr = _frame({"broker": [hosts[p.account_id] for p in probes], "session": _labels([p.sent_utc for p in probes]),
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


def _cell(fr: Frames, broker: str, session: str, rng: np.random.Generator, resamples: int) -> dict[str, Any] | None:
    def sel(df: pd.DataFrame) -> pd.DataFrame:
        m = df["broker"] == broker
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


def build_table(fr: Frames, *, version: str, window_utc: tuple[str, str], seed: int = SEED,
                resamples: int = RESAMPLES) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    cells: dict[str, dict[str, Any]] = {}
    for broker in sorted(set(fr.entries["broker"])):
        row = {s: c for s in (*SESSIONS, ALL) if (c := _cell(fr, broker, s, rng, resamples)) is not None}
        if ALL in row:
            # stops filled at the stop price are the broker's demo server, not a session: the pooled cell decides
            for c in row.values():
                c["overshoot_trusted"] = row[ALL]["overshoot_trusted"]
            cells[broker] = row
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


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--state-dir", required=True, help="the folder holding aux.db (opened read-only)")
    ap.add_argument("--data-root", help="the store root for exit_fills (none: probe exits only)")
    ap.add_argument("--from-day", required=True)
    ap.add_argument("--to-day", required=True)
    ap.add_argument("--host", action="append", required=True, help="account=broker, repeatable")
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", help="the YAML to write (default: stdout)")
    args = ap.parse_args(argv)
    hosts = dict(h.split("=", 1) for h in args.host)
    lo, hi = _day_bounds(args.from_day, args.to_day)
    legs: dict[str, pd.DataFrame] = {}
    if args.data_root:
        from goldbot.data.store import Store
        store = Store(args.data_root)
        for host in hosts:
            df = store.read("exit_fills", source=host, start=lo, end=hi)
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
