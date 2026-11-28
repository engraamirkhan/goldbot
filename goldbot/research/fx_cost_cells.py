"""FX engine cost cells from the brokers' own demo fills, for the measured FX cost of record (backlog 280(a); U2 card
prereg-2026-11-28-u2-pooled-body.md section 3, P3; srv3_cost_record schema 2).

Units: every cost is in bp of the fill price (scale-free, so a 2026 fill's cost reads on a 2012 entry as gold's USD
cost does after / entry). Per closed FX exploration fill of a measured account (aux.db `exploration_trades`):
* spread   = the quoted ask - bid at send (`spread_usd` holds the symbol's price units for FX); with an exit quote, the
             mean of the entry and exit spreads, as realised_costs. A recorded 0.0 is a raw-spread quote and is kept;
             a missing quote drops the fill;
* entry slip = side x (fill - requested), signed;
* exit slip  = `quote`: side x (touch - exit price) from the recorded exit quote, else `level`: a stop or target at its
             initial level (realised_costs.exit_level rules, the moved-stop tolerance scaled to the FX stop distance:
             10 % of it), else the exit is NOT measurable and the fill is dropped and counted. Never the gold probe
             or the USD/oz prior: FX has no scheduled cost probes, and vantage's demo stop/target exits fill at the level
             by construction (realised_costs.DEMO_EXIT_AT_LEVEL), so they measure nothing;
* commission = |booked commission| / account-per-quote-unit / quote notional (lots x 100k x fill). The fill's
             `fx_acct_per_usd` is the account currency per unit of the symbol's QUOTE currency (USD for gold and the
             X/USD pairs; per JPY ~0.0047 on USDJPY/EURJPY, per CAD ~0.53 on USDCAD, checked on the brain 2026-11-28),
             so no cross rate is needed;
* round trip = spread + entry slip + exit slip + commission (bp).

Cells per instrument x BROKER (ICM's accounts pool: one server, quant C1) x session (send time) x tf and `all`: n, session
days, mean, the day-clustered 95 % upper bound (realised_costs.clustered_ci), point = spread + max(0, slips) +
commission, charged = max(point, ub95) (charge rule ub95). A cell is trusted at >= min_fills fills and >= MIN_DAYS days.

Membership (card P3, quant verbatim): "An instrument with fewer than 30 fills at either broker in every session stays
`prior` and leaves the body; the measured broker is never charged alone." Fills sent at or after FILL_CUTOFF (2026-12-12
00:00 UTC) are not counted. Membership follows fill counts only (label-blind).

Cost rows of a measured instrument per broker x session x tf: the tf cell when trusted (`ecr`), else the session `all`
cell (`ecr-session-all`), else the broker's worst trusted session `all` charge (`ecr-broker-max:session-below-min-fills`;
gold's builder fell to W2 there, flagged as the less conservative direction). rt_bp = max(W2, charged); slip_x_bp =
max(0, charged - spread - commission) of the charged cell (quant F4 on #515).

    python -m goldbot.research.fx_cost_cells --state-dir state --out ~/tmp/fx-cost-dryrun   # DRY RUN, not of record
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.research import decision_cost as DC
from goldbot.research import realised_costs as RC
from goldbot.research import srv3_cost_record as CR

FX10: tuple[str, ...] = ("AUDJPY", "AUDUSD", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF",
                         "USDJPY")
FILL_CUTOFF = pd.Timestamp(CR.FILL_CUTOFF_UTC)
CONTRACT = 100_000.0
MOVED_STOP_FRAC = 0.10              # an exit this share of the stop distance better than the stop was at a moved stop
MIN_FILLS = 30
MIN_DAYS = RC.MIN_DAYS
SESSIONS = RC.SESSIONS
ALL = RC.ALL
DROP_QUOTE = "no entry spread quote"
DROP_FIELDS = "missing fill, request, exit, lots or FX rate"
DROP_EXIT = "exit slip not measurable (no exit quote, no level)"


def _fx_level(r: Mapping[str, Any], broker: str) -> float | None:
    reason, side, req = str(r.get("exit_reason") or ""), int(r["side"]), float(r["requested_price"])
    if broker in RC.DEMO_EXIT_AT_LEVEL:
        return None
    if reason in RC.STOP_EXITS and r.get("stop_usd") and not str(r.get("arm_id", "")).startswith(
            RC.TRAILING_ARM_PREFIXES):
        stop = float(r["stop_usd"])
        level = req - side * stop
        return level if side * (level - float(r["exit_price"])) >= -MOVED_STOP_FRAC * stop else None
    if reason in RC.TARGET_EXITS and r.get("target_usd"):
        return req + side * float(r["target_usd"])
    return None


def fx_trade_costs(rows: Iterable[Mapping[str, Any]], brokers: Mapping[str, str], *, lo: pd.Timestamp | None = None,
                   hi: pd.Timestamp = FILL_CUTOFF, instruments: tuple[str, ...] = FX10
                   ) -> tuple[pd.DataFrame, dict[str, int]]:
    """(one row per measured closed FX fill, bp; the dropped fills counted by reason). Module docstring."""
    if hi > FILL_CUTOFF:
        raise ValueError(f"hi {hi} is after the card's fill cut-off {FILL_CUTOFF}: fills after it are not counted")
    out: list[dict[str, Any]] = []
    drops: Counter[str] = Counter()
    for r in rows:
        acc, sym = str(r.get("account_id")), str(r.get("symbol") or "")
        if acc not in brokers or sym not in instruments or r.get("group_id") not in (None, r.get("client_order_id")):
            continue
        sent = pd.Timestamp(r["sent_utc"])
        if (lo is not None and sent < lo) or sent >= hi:
            continue
        if any(r.get(k) in (None, 0, 0.0) for k in ("fill_price", "requested_price", "exit_price", "fx_acct_per_usd",
                                                    "lots", "exit_utc")):
            drops[DROP_FIELDS] += 1
            continue
        if r.get("spread_usd") is None:
            drops[DROP_QUOTE] += 1
            continue
        broker = brokers[acc]
        side, fill, req, xpx = int(r["side"]), float(r["fill_price"]), float(r["requested_price"]), float(
            r["exit_price"])
        spread = float(r["spread_usd"])
        x_bid, x_ask = r.get("exit_bid"), r.get("exit_ask")
        level = _fx_level(r, broker)
        if x_bid is not None and x_ask is not None:
            source, xslip = "quote", side * ((float(x_bid) if side > 0 else float(x_ask)) - xpx)
            spread = (spread + float(x_ask) - float(x_bid)) / 2
        elif level is not None and str(r.get("exit_reason")) in RC.STOP_EXITS:
            source, xslip = "level", side * (level - xpx)
        elif level is not None:
            source, xslip = "level", max(0.0, side * (level - xpx))
        else:
            drops[DROP_EXIT] += 1
            continue
        notional_quote = float(r["lots"]) * CONTRACT * fill
        comm_bp = abs(float(r.get("commission") or 0.0)) / float(r["fx_acct_per_usd"]) / notional_quote * 1e4
        bp = 1e4 / fill
        out.append({"instrument": sym, "account": acc, "broker": broker, "session": RC.session_of(sent),
                    "tf": str(r.get("tf") or "?"), "sent_utc": sent, "exit_source": source,
                    "spread": spread * bp, "entry_slip": side * (fill - req) * bp, "exit_slip": xslip * bp,
                    "commission": comm_bp})
    cols = ["instrument", "account", "broker", "session", "tf", "sent_utc", "exit_source", "spread", "entry_slip",
            "exit_slip", "commission"]
    df = pd.DataFrame(out, columns=cols)
    df["day"] = RC.session_day(df["sent_utc"]) if len(df) else pd.Series(dtype=str)
    df["round_trip"] = df["spread"] + df["entry_slip"] + df["exit_slip"] + df["commission"]
    return df, dict(drops)


def _cell(g: pd.DataFrame, min_fills: int) -> dict[str, Any]:
    x, day = g["round_trip"].to_numpy(float), g["day"].to_numpy()
    _, hi = RC.clustered_ci(x, day)
    m = {k: float(g[k].mean()) for k in ("spread", "entry_slip", "exit_slip", "commission")}
    point = m["spread"] + max(0.0, m["entry_slip"]) + max(0.0, m["exit_slip"]) + m["commission"]
    n_days = int(len(np.unique(day)))
    return {"n": len(x), "n_days": n_days, "mean": float(x.mean()), "ub95": float(hi), "point": point,
            "charged": max(point, float(hi)), **m, "trusted": len(x) >= min_fills and n_days >= MIN_DAYS}


def cells(trades: pd.DataFrame, min_fills: int = MIN_FILLS) -> pd.DataFrame:
    """Per instrument x broker x session x (tf | `all`) cell (module docstring)."""
    out = []
    for (i, b, s), g in trades.groupby(["instrument", "broker", "session"]):
        out.append({"instrument": i, "broker": b, "session": s, "tf": ALL, **_cell(g, min_fills)})
        for tf, h in g.groupby("tf"):
            out.append({"instrument": i, "broker": b, "session": s, "tf": str(tf), **_cell(h, min_fills)})
    return pd.DataFrame(out, columns=["instrument", "broker", "session", "tf", "n", "n_days", "mean", "ub95", "point",
                                      "charged", "spread", "entry_slip", "exit_slip", "commission", "trusted"])


def membership(cell: pd.DataFrame, brokers: tuple[str, ...], instruments: tuple[str, ...] = FX10,
               min_fills: int = MIN_FILLS) -> dict[str, tuple[str, str]]:
    """instrument -> (measured | prior, why): measured only when every broker has >= min_fills fills in at least one
    session `all` cell (card P3; the measured broker is never charged alone)."""
    out = {}
    for i in instruments:
        a = cell[(cell["instrument"] == i) & (cell["tf"] == ALL)]
        best = {b: int(a.loc[a["broker"] == b, "n"].max()) if (a["broker"] == b).any() else 0 for b in brokers}
        thin = [f"{b} {n}" for b, n in best.items() if n < min_fills]
        out[i] = (("prior", f"best session fills below {min_fills} at {', '.join(thin)}") if thin
                  else ("measured", f"best session fills {best}"))
    return out


def cost_rows(cell: pd.DataFrame, member: Mapping[str, tuple[str, str]], brokers: tuple[str, ...],
              tfs: tuple[str, ...], sessions: tuple[str, ...] = SESSIONS) -> pd.DataFrame:
    """The FX cost-table rows of the measured instruments (module docstring), srv3_cost_record.FxCell fields."""
    out = []
    for i in sorted(k for k, (b, _) in member.items() if b == "measured"):
        for b in brokers:
            c = cell[(cell["instrument"] == i) & (cell["broker"] == b)]
            alls = c[(c["tf"] == ALL) & c["trusted"]]
            worst = alls.iloc[int(np.argmax(alls["charged"].to_numpy(float)))] if len(alls) else None
            for s in sessions:
                sa = c[(c["session"] == s) & (c["tf"] == ALL)]
                session_n = int(sa["n"].iloc[0]) if len(sa) else 0
                for tf in tfs:
                    t = c[(c["session"] == s) & (c["tf"] == tf) & c["trusted"]]
                    if len(t):
                        top, basis = t.iloc[0], "ecr"
                    elif len(sa) and bool(sa["trusted"].iloc[0]):
                        top, basis = sa.iloc[0], "ecr-session-all"
                    elif worst is not None:
                        top, basis = worst, "ecr-broker-max:session-below-min-fills"
                    else:
                        raise ValueError(f"{i} {b}: measured but no trusted session cell")
                    ch = float(top["charged"])
                    out.append({"instrument": i, "broker": b, "session": s, "tf": tf,
                                "rt_bp": round(max(DC.RT_BP, ch), 6), "ecr_ub95_bp": round(ch, 6),
                                "slip_x_bp": round(max(0.0, ch - float(top["spread"]) - float(top["commission"])), 6),
                                "measured_n": int(top["n"]), "session_n": session_n,
                                "basis": basis if ch > DC.RT_BP else f"{basis}:w2-floor"})
    return pd.DataFrame(out, columns=list(CR.FxCell.model_fields))


def commission_bp(trades: pd.DataFrame) -> dict[str, dict[str, float]]:
    """instrument -> broker -> mean round-trip commission, bp of the fill."""
    return {str(i): {str(b): round(float(h["commission"].mean()), 6) for b, h in g.groupby("broker")}
            for i, g in trades.groupby("instrument")}


def build_record(gold: CR.CostRecord, trades: pd.DataFrame, *, tfs: tuple[str, ...], dry_run: bool,
                 files: Mapping[str, str], content_sha256: str, min_fills: int = MIN_FILLS) -> CR.CostRecord:
    """A schema-2 record: gold's cells and commissions as recorded, FX classes measured or prior by the card's
    membership, the measured instruments' cells. `dry_run` records are labelled NOT OF RECORD (and refused by
    srv3_cost_record._load)."""
    brokers = tuple(sorted(gold.commission_rt_usd_per_oz))
    cell = cells(trades, min_fills)
    member = membership(cell, brokers, min_fills=min_fills)
    rows = cost_rows(cell, member, brokers, tfs)
    comm = commission_bp(trades)
    measured = {i for i, (b, _) in member.items() if b == "measured"}
    classes = tuple(c.model_copy(update={"basis": "measured" if c.instrument in measured else c.basis})
                    if c.asset_class == "fx" else c for c in gold.classes)
    label = "DRY RUN - NOT OF RECORD (fills before the 2026-12-11 cut-off)" if dry_run else "microstructure-researcher"
    return gold.model_copy(update={
        "schema_version": 2, "dry_run": dry_run, "fill_cutoff_utc": CR.FILL_CUTOFF_UTC,
        "version": "srv3-cost-" + content_sha256[:16], "content_sha256": content_sha256, "record_sha256": "",
        "files": dict(files), "frozen_by": label, "classes": classes,
        "fx_commission_rt_bp": {i: comm[i] for i in sorted(measured)},
        "fx_cells": tuple(CR.FxCell.model_validate(r) for r in rows.to_dict("records")),
        "built_utc": str(pd.Timestamp.now(tz="UTC"))})


def fx_spread_table(raw: Path, instruments: tuple[str, ...] = FX10, years: range = range(2003, 2026)
                    ) -> pd.DataFrame:
    """Dukascopy FX quoted spread per instrument x session x month in price units (raw/dukascopy_fx/
    <inst>_1m_dukascopy_<year>.parquet: ts_utc, spread_mean = the minute's mean ask - bid, dq_flag), 2003-01..2025-09
    (the holdout and later are never read): mean, ub95 = mean + 1.96 x the standard error of the session-day means,
    quote_basis `bid_ask`. A file without `spread_mean` (bid-only) gives `bid_only` rows with no ub95: never imputed.
    `available_utc` = the first instant after the month (as gold's spread table)."""
    from goldbot.data.calendar import DEFAULT_SESSIONS
    out = []
    cut = pd.Timestamp("2025-10-01", tz="UTC")
    for inst in instruments:
        for y in years:
            p = raw / "dukascopy_fx" / f"{inst.lower()}_1m_dukascopy_{y}.parquet"
            if not p.exists():
                continue
            d = pd.read_parquet(p)
            ts = pd.DatetimeIndex(pd.to_datetime(d["ts_utc"], utc=True))
            keep = np.asarray(ts < cut)
            bid_only = "spread_mean" not in d
            if not bid_only:
                spr = d["spread_mean"].astype(float).to_numpy()
                flag = d["dq_flag"].astype(str) if "dq_flag" in d else pd.Series("", index=d.index)
                keep &= ~flag.str.startswith("error").to_numpy() & np.isfinite(spr) & (spr > 0)
            d, ts = d[keep], ts[keep]
            f = pd.DataFrame({"session": np.asarray(DEFAULT_SESSIONS.session_label(ts)).astype(str),
                              "month": ts.strftime("%Y-%m"),
                              "day": (ts + pd.Timedelta(hours=24 - RC.SESSION_DAY_CUT_H)).strftime("%Y-%m-%d"),
                              "spread": np.nan if bid_only else d["spread_mean"].astype(float).to_numpy()})
            for (sess, mo), g in f[f["session"].isin(SESSIONS)].groupby(["session", "month"], sort=True):
                av = (pd.Period(str(mo), "M") + 1).start_time.tz_localize("UTC")
                if bid_only:
                    out.append({"instrument": inst, "session": sess, "month": mo, "minutes": len(g), "days": 0,
                                "mean_px": np.nan, "ub95_px": np.nan, "quote_basis": "bid_only", "available_utc": av})
                    continue
                dm = g.groupby("day")["spread"].mean()
                se = float(dm.std(ddof=1) / np.sqrt(len(dm))) if len(dm) > 1 else float("nan")
                mean = float(g["spread"].mean())
                out.append({"instrument": inst, "session": sess, "month": mo, "minutes": len(g), "days": len(dm),
                            "mean_px": mean, "ub95_px": mean + 1.96 * se if np.isfinite(se) else mean,
                            "quote_basis": CR.BID_ASK, "available_utc": av})
    return pd.DataFrame(out, columns=["instrument", "session", "month", "minutes", "days", "mean_px", "ub95_px",
                                      "quote_basis", "available_utc"])


def main(argv: list[str] | None = None) -> int:
    """DRY RUN on fills to date (aggregates only, never a record file in config/): counts, membership, cells."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--host", action="append", default=[], help="account=broker (repeatable)")
    a = ap.parse_args(argv)
    hosts = a.host or ["icm-demo=icm", "icm-probe=icm", "vantage-demo=vantage"]
    brokers = dict(h.split("=", 1) for h in hosts)
    conn = sqlite3.connect(f"file:{Path(a.state_dir, 'aux.db')}?mode=ro", uri=True)
    rows, _, void = RC.read_rows(conn)
    rows = [r for r in rows if r.get("client_order_id") not in void]
    tr, drops = fx_trade_costs(rows, brokers)
    cell = cells(tr)
    member = membership(cell, tuple(sorted(set(brokers.values()))))
    a.out.mkdir(parents=True, exist_ok=True)
    body = {"label": "DRY RUN - NOT OF RECORD", "built_utc": str(pd.Timestamp.now(tz="UTC")),
            "fill_cutoff_utc": CR.FILL_CUTOFF_UTC, "measured_fills": len(tr), "dropped": drops,
            "membership": member,
            "fills": {f"{i}|{b}|{s}": int(n) for i, b, s, n in tr.groupby(["instrument", "broker", "session"])
                      .size().reset_index().itertuples(index=False)},
            "cells": cell.round(4).to_dict("records")}
    (a.out / "fx_cost_cells_dryrun.json").write_text(json.dumps(body, indent=1, default=str))
    print(json.dumps({k: body[k] for k in ("label", "measured_fills", "dropped", "membership", "fills")}, indent=1,
                     default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
