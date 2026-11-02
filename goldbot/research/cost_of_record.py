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
  share their resample;
* swap_measured (per broker, its own version): the broker terms' overnight swap per lot, frozen as numbers only
  (`--swap-terms <broker>=<terms.json> --swap-version <v>`; `--from-table` re-renders a table with unchanged cells).

The C1 research cost (amendment C1, ADR 0069), `C1Costs.cost_r`: "cost_R = (RT_point_bps x P_entry + OS_point_bps x
P_entry x [stop or trail exit] - nights x swap_bps(side) x P_entry) / stop_usd", the point estimates converted at the
reference price 4,000, the measured swap (shorts credited) and, where the simulated fill already gapped past the stop,
"the overshoot term is the measured residual only".
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

from goldbot.base import FrozenRecord
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.timeutil import epoch_ns
from goldbot.labels import rollover_nights
from goldbot.research.arms import CostOfRecord

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
REFERENCE_PRICE_USD = 4000.0          # C1 / W2: dollar costs of record -> bps of the entry price at this gold price
BPS = 1e-4
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
        if "kind" in df.columns:
            df = df[df["kind"].astype(str) != "target"]    # a target is a limit fill: it never offsets a stop's cost
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
# swap_measured: each broker's measured overnight swap, USD per lot (100 oz) per night, broker sign (+ = credited),
# versioned on its own; the C1 research cost (ADR 0069, `cost_of_record.C1Costs`) charges it per rollover held.
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
    if table.get("swap_measured"):
        out.append("swap_measured:")
        for broker, sw in table["swap_measured"].items():
            parts = [f"{k}: {json.dumps(v) if isinstance(v, str) else _num(v) if isinstance(v, float) else v}"
                     for k, v in sw.items()]
            out.append(f"  {broker}: {{{', '.join(parts)}}}")
    return "\n".join(out) + "\n"


SWAP_SOURCE = "broker terms (symbol swap, points mode, broker conversion)"


def swap_block(terms: Mapping[str, Any], *, version: str) -> dict[str, Any]:
    """The `swap_measured` entry of one broker from its broker terms file (state/broker_terms_<account>.json): the
    measured time and the three swap numbers only; the account id, commission and notes are never copied."""
    return {"version": version, "measured_utc": str(terms["measured_utc"]), "source": SWAP_SOURCE,
            "long_usd_per_lot": float(terms["swap_long_usd_per_lot"]),
            "short_usd_per_lot": float(terms["swap_short_usd_per_lot"]),
            "triple_weekday": int(terms["swap_triple_weekday"])}


def table_from_yaml(text: str) -> dict[str, Any]:
    """A rendered table read back in the builder's own shapes (intervals as tuples), to re-render it unchanged."""
    import yaml
    table: dict[str, Any] = yaml.safe_load(text)
    for row in table["cells"].values():
        for cell in row.values():
            for k, v in list(cell.items()):
                if isinstance(v, list):
                    cell[k] = tuple(v)
    return table


# ---------------------------------------------------------------- amendment C1: the shared research cost in R
class C1Costs(FrozenRecord):
    """Amendment C1 (ADR 0069), the cost every research screen charges from 2026-11-02: "cost_R = (RT_point_bps x
    P_entry + OS_point_bps x P_entry x [stop or trail exit] - nights x swap_bps(side) x P_entry) / stop_usd". RT and OS
    are the cost of record's ICM POINT estimates (not the upper bound) in bps of the reference price, per session (the
    pooled `all` outside every session); the swap is the broker's measured terms per night (shorts credited); "no
    double count: if fills already gap to the minute open, the overshoot term is the measured residual only". The
    dollar upper bound stays a stress column of the caller (`ArmCosts.screen_charges`), never this gate."""
    version: str
    broker: str
    reference_price_usd: float
    rt_bps: dict[str, float]
    os_bps: dict[str, float]
    swap_long_bps: float              # per night, broker sign: negative = paid, positive = credited
    swap_short_bps: float
    triple_weekday: int
    server_tz: str

    @classmethod
    def of_record(cls, table: CostOfRecord, broker: str = "icm",
                  reference_price_usd: float = REFERENCE_PRICE_USD) -> C1Costs:
        """Point estimates of `broker`'s cells and its own measured swap (ADR 0043: never another broker's)."""
        if broker not in table.cells:
            raise KeyError(f"no cost of record for broker {broker!r}")
        if broker not in table.swap_measured:
            raise KeyError(f"no measured swap for broker {broker!r} (has {sorted(table.swap_measured)})")
        sw = table.swap_measured[broker]
        per = 1.0 / reference_price_usd / BPS
        cells = table.cells[broker]
        return cls(version=f"c1/{table.version}/{sw.version}", broker=broker, reference_price_usd=reference_price_usd,
                   rt_bps={s: c.round_trip_usd * per for s, c in cells.items()},
                   os_bps={s: max(0.0, c.overshoot_usd) * per for s, c in cells.items()},
                   swap_long_bps=sw.long_usd_per_lot / sw.contract_oz * per,
                   swap_short_bps=sw.short_usd_per_lot / sw.contract_oz * per,
                   triple_weekday=sw.triple_weekday, server_tz=sw.server_tz)

    def nights(self, entry_utc: pd.DatetimeIndex, exit_utc: pd.DatetimeIndex) -> np.ndarray:
        """Rollovers held, the measured triple day counting three (`labels.rollover_nights`)."""
        return rollover_nights(pd.DatetimeIndex(entry_utc), pd.DatetimeIndex(exit_utc), self.server_tz,
                               self.triple_weekday)

    def swap_usd(self, entry_px: np.ndarray, nights: np.ndarray, side: np.ndarray) -> np.ndarray:
        """Swap per oz over the hold, broker sign (a short is credited: positive)."""
        bps = np.where(np.asarray(side) > 0, self.swap_long_bps, self.swap_short_bps)
        return np.asarray(np.asarray(nights, dtype=float) * bps * BPS * np.asarray(entry_px, dtype=float))

    def _session_bps(self, table: dict[str, float], sessions: np.ndarray | None, n: int) -> np.ndarray:
        if sessions is None:
            return np.full(n, table[ALL])
        labels = [str(x) or ALL for x in np.asarray(sessions)]
        bad = sorted({x for x in labels if x not in table})
        if bad:                         # quant review #238: never a silent pooled read of a misspelt session
            raise ValueError(f"unknown session {bad}: one of {sorted(table)} or '' (outside every session)")
        return np.array([table[x] for x in labels], dtype=float)

    def cost_r(self, *, entry_px: np.ndarray, stop_usd: np.ndarray, stop_exit: np.ndarray, nights: np.ndarray,
               side: np.ndarray, sessions: np.ndarray | None = None,
               sim_overshoot_usd: np.ndarray | float = 0.0) -> np.ndarray:
        """Cost per trade in R (adverse positive; a credited swap lowers it). `stop_exit` is 0/1 or a leg weight;
        `sim_overshoot_usd` is how far past the stop the simulated fill already went (a gap to the minute open)."""
        n = len(np.atleast_1d(entry_px))
        return cost_r(entry_px, stop_usd, stop_exit, nights, side,
                      rt_bps=self._session_bps(self.rt_bps, sessions, n),
                      os_bps=self._session_bps(self.os_bps, sessions, n),
                      swap_long_bps=self.swap_long_bps, swap_short_bps=self.swap_short_bps,
                      sim_overshoot_usd=sim_overshoot_usd)


def cost_r(entry_px: np.ndarray, stop_usd: np.ndarray, stop_exit: np.ndarray, nights: np.ndarray, side: np.ndarray,
           *, rt_bps: np.ndarray | float, os_bps: np.ndarray | float, swap_long_bps: float, swap_short_bps: float,
           sim_overshoot_usd: np.ndarray | float = 0.0) -> np.ndarray:
    """The C1 formula (ADR 0069) per trade: (RT_bps x P + max(0, OS_bps x P x w - simulated overshoot) - nights x
    swap_bps(side) x P) / stop_usd, w = the stop-exit weight. Every price-relative term scales with P, so the same
    ATR-multiple stop costs the same R at $1,650 and at $3,300."""
    p = np.asarray(entry_px, dtype=float)
    w = np.asarray(stop_exit, dtype=float)
    sim = np.asarray(sim_overshoot_usd, dtype=float) * (w > 0)
    os_resid = np.maximum(0.0, np.asarray(os_bps, dtype=float) * BPS * p * w - sim)
    swap = np.asarray(nights, dtype=float) * np.where(np.asarray(side) > 0, swap_long_bps, swap_short_bps) * BPS * p
    return np.asarray((np.asarray(rt_bps, dtype=float) * BPS * p + os_resid - swap) / np.asarray(stop_usd, dtype=float))


def _day_bounds(first: str, last: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    from goldbot.ops.report_days import day_bounds
    return day_bounds(first)[0], day_bounds(last)[1]


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--state-dir", help="the folder holding aux.db (opened read-only)")
    ap.add_argument("--data-root", help="the store root for exit_fills (none: probe exits only)")
    ap.add_argument("--from-day")
    ap.add_argument("--to-day")
    ap.add_argument("--host", action="append", help="account=broker, repeatable")
    ap.add_argument("--version")
    ap.add_argument("--from-table", help="re-render this table's cells unchanged (no aux.db read)")
    ap.add_argument("--swap-terms", action="append", default=[],
                    help="broker=<broker_terms json>, repeatable: freeze that broker's measured swap (numbers only)")
    ap.add_argument("--swap-version", help="the version of the swap block (required with --swap-terms)")
    ap.add_argument("--out", help="the YAML to write (default: stdout)")
    args = ap.parse_args(argv)
    if args.swap_terms and not args.swap_version:
        ap.error("--swap-terms needs --swap-version")
    if args.from_table:
        table = table_from_yaml(Path(args.from_table).read_text())
    else:
        if not (args.state_dir and args.from_day and args.to_day and args.host and args.version):
            ap.error("a build needs --state-dir, --from-day, --to-day, --host and --version (or --from-table)")
        table = _build(args)
    for spec in args.swap_terms:
        broker, path = spec.split("=", 1)
        table.setdefault("swap_measured", {})[broker] = swap_block(json.loads(Path(path).read_text()),
                                                                   version=args.swap_version)
    text = render_yaml(table)
    if args.out:
        Path(args.out).write_text(text)
    else:
        print(text, end="")


def _build(args: argparse.Namespace) -> dict[str, Any]:
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
    return build_table(fr, version=args.version, window_utc=(lo.isoformat(), hi.isoformat()))


if __name__ == "__main__":
    main()
