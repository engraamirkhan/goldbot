"""Realised round-trip cost per (account, session, timeframe) from the broker's own fills, and the engine cost of
record built from it (finance audit 2026-11-20, N10 / P5 / P6; ADR 0092).

Why: the engine's cost (`CostTable.round_trip_usd_per_oz`: spread + 2 x slippage + commission) charged the $0.15/side
slippage PRIOR on every cell, because its slippage cells read only main-engine fills (there are none while every engine
is `probe_only`) and the probe sleeve may only raise a cell. That prior is $0.30 of a ~$0.47 round trip whose measured
parts are ~$0.17: the 2.8x of N10. This module measures the round trip the fills actually paid, and writes it as a
VERSIONED engine cost of record (`EngineCostRecord`, config/engine_costs/<version>.yaml) charged at a conservative
bound per cell (`charge_rule` below). The engine reads the version settings name (`costs.engine_record`); the legacy table stays the
fallback for every cell the record does not cover, and each exploration row stamps the version it was charged on, so a
past read keeps its own cost (K / holdout honesty: a version is immutable, a new one is a new file).

Per closed gold exploration fill (aux.db `exploration_trades`; USD per oz; adverse positive):
* spread = the quoted ask - bid at send; with the exit quote recorded (P6: `exit_bid`/`exit_ask`), the mean of the entry
  and exit spreads (half paid on each leg), else the entry spread for both halves;
* entry slip = side x (fill - requested), signed (the send latency's market move is noise around the true cost:
  flooring each fill would turn that noise into cost; favourable slippage is floored on the CELL's mean instead);
* exit slip, signed, by the first source that applies (`exit_source`):
  `quote`  side x (touch - exit price), touch the exit-side quote recorded at the exit (P6);
  `level`  a stop at its initial level: side x (stop level - exit price); a target: side x (target level - exit price)
           floored at 0 per fill (a limit's demo price improvement is not bankable: v3's target rule), the levels
           from the requested price and the gate's distances (as `research.cost_of_record`);
  `probe`  anything else (time exits, moved or trailed stops, the brokers whose demo exits fill at the level by
           construction, `execution.costs.DEMO_EXIT_AT_LEVEL`): the p75 of the same broker's scheduled-probe market-exit
           slip in that session (`cost_probes`, exit quote recorded: prereg K11), floored at 0;
  `prior`  no probe evidence for the broker and session: the settings slippage prior;
* commission = |booked commission| / (lots x contract x account-per-USD rate); swap paid is reported, NOT charged
  (the RiskGate charges swap itself, `Engine._swap_usd_per_oz`, from the broker's measured rates);
* round trip = spread + entry slip + exit slip + commission;
* engine cost = the cost the engine charged at send (`cost`, account currency) per oz, for the old-vs-new comparison.

Cells: account x session (by send time, the engine's `session_label`) x timeframe, plus `all` pooled over timeframes.
Each cell: n, session days, the mean with a 95 % t interval clustered by session day (22:00 UTC), p50 / p75 / p90, the
components' means and the engine's mean. Its point cost = spread + max(0, entry slip mean) + max(0, exit slip mean) +
commission (favourable slippage never credited, as the v2/v3 cost of record). Charged (`charge_rule`):
  `ub95` (default)  max(point, the 95 % upper bound of the mean): the cost is an EXPECTATION input (the gate's cost
                    share of R, the EV hurdle, sizing, the H-81 cost stop), so the conservatism belongs on the
                    estimate's uncertainty, not on per-trade dispersion;
  `p75`             max(point, upper bound, the cell's 75th percentile of per-trade round trips): stricter; it adds
                    about 0.67 sd of per-fill noise to every cell.
A cell under `MIN_FILLS` fills or `MIN_DAYS` days does not trust its own interval: it charges the larger of its own
point and its account x session `all` cell's charge (sparse never undercuts pooled). Gold only: other instruments keep the legacy table (their cells are not measured
here). Aggregates only in the output: no trade row, account login, balance or host.

    python -m goldbot.research.realised_costs --state-dir state --from-day 2026-10-26 --to-day 2026-11-20 \
        --version ecr-2026-11-20.1 --out config/engine_costs/ecr-2026-11-20.1.yaml
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import yaml
from pydantic import Field
from scipy import stats

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.calendar import DEFAULT_SESSIONS

GOLD_OZ = 100.0
SESSIONS = ("asia", "london", "newyork")
ALL = "all"
QUANTILE = 0.75
MIN_FILLS = 30
MIN_DAYS = 5
STOP_EXITS = frozenset({"stop", "engine_stop_close"})
TARGET_EXITS = frozenset({"target"})
TRAILING_ARM_PREFIXES = ("BXR",)      # H-80 runners trail their stop: the initial level is not the exit's
MOVED_STOP_USD = 0.50                 # an exit this far better than the initial stop was at a moved stop
PROBE_QUANTILE = 0.75                 # the probe market-exit slip charged to an exit with no measurement of its own
# Quant ruling 2026-11-09 (execution.costs.DEMO_EXIT_AT_LEVEL): these brokers' demo stop and target exits fill at the
# bracket level by construction, so their level slip is not evidence; their exits take the probe or the prior
DEMO_EXIT_AT_LEVEL = frozenset({"vantage", "vantage247", "vantage_obs"})
SESSION_DAY_CUT_H = 22                # a session day runs 22:00 -> 22:00 UTC (the daily break)
ExitSource = Literal["quote", "level", "probe", "prior"]
ChargeRule = Literal["ub95", "p75"]
DEFAULT_DIR = Path("config/engine_costs")


def session_day(ts: pd.Series) -> pd.Series:
    """The session day (22:00 UTC cut) of each UTC stamp: a fill at 23:00 UTC belongs to the next day."""
    t = pd.to_datetime(ts, utc=True)
    return (t + pd.Timedelta(hours=24 - SESSION_DAY_CUT_H)).dt.strftime("%Y-%m-%d")


def _sessions(ts: Iterable[Any]) -> np.ndarray:
    idx = pd.DatetimeIndex(pd.to_datetime(list(ts), utc=True))
    return np.asarray(DEFAULT_SESSIONS.session_label(idx)).astype(str) if len(idx) else np.array([], dtype=str)


def probe_exit_slip(probes: Iterable[Mapping[str, Any]], brokers: Mapping[str, str], *,
                    void: Collection[str] = frozenset()) -> dict[tuple[str, str], float]:
    """(broker, session) -> the PROBE_QUANTILE of scheduled probes' market-exit slip against the exit-side quote
    recorded when the close was sent (side x (touch - exit price)), floored at 0. Probes of one broker pool across its
    accounts (one server, one execution); never across brokers (ADR 0043)."""
    rows = [p for p in probes if p.get("purpose") == "cost_measurement" and p.get("exit_price") is not None
            and p.get("exit_bid") is not None and p.get("exit_ask") is not None
            and p.get("account_id") in brokers and p.get("client_order_id") not in void
            and (p.get("symbol") in (None, "XAUUSD"))]
    if not rows:
        return {}
    side = np.array([float(p["side"]) for p in rows])
    touch = np.array([float(p["exit_bid"]) if p["side"] > 0 else float(p["exit_ask"]) for p in rows])
    slip = side * (touch - np.array([float(p["exit_price"]) for p in rows]))
    df = pd.DataFrame({"broker": [brokers[p["account_id"]] for p in rows],
                       "session": _sessions(p["sent_utc"] for p in rows), "slip": slip})
    return {(str(b), str(s)): max(0.0, float(g["slip"].quantile(PROBE_QUANTILE)))
            for (b, s), g in df.groupby(["broker", "session"])}


def trade_costs(rows: Iterable[Mapping[str, Any]], brokers: Mapping[str, str], *, exit_prior_usd: float,
                probe_slip: Mapping[tuple[str, str], float], void: Collection[str] = frozenset(),
                lo: pd.Timestamp | None = None, hi: pd.Timestamp | None = None) -> pd.DataFrame:
    """One row per closed, filled gold exploration trade of an account in `brokers`, sent in [lo, hi): the round trip
    and its parts in USD per oz (module docstring). Pyramid adds (a `group_id` other than its own id) and ids void for
    the cost cells are dropped; a row without a requested price, entry spread, exit price, lots or FX rate is dropped
    (it cannot be measured, and is never imputed)."""
    out: list[dict[str, Any]] = []
    for r in rows:
        acc = str(r.get("account_id"))
        if acc not in brokers or (r.get("symbol") or "XAUUSD") != "XAUUSD" or r.get("client_order_id") in void:
            continue
        if r.get("group_id") not in (None, r.get("client_order_id")):
            continue
        need = ("fill_price", "requested_price", "spread_usd", "exit_price", "fx_acct_per_usd", "lots", "exit_utc")
        if any(r.get(k) in (None, 0, 0.0) for k in need):
            continue
        sent = pd.Timestamp(r["sent_utc"])
        if (lo is not None and sent < lo) or (hi is not None and sent >= hi):
            continue
        side, fill, req = int(r["side"]), float(r["fill_price"]), float(r["requested_price"])
        exit_px, reason, broker = float(r["exit_price"]), str(r.get("exit_reason") or ""), brokers[acc]
        oz = float(r["lots"]) * GOLD_OZ * float(r["fx_acct_per_usd"])      # account currency per USD per oz move
        session = str(_sessions([sent])[0])
        spread = float(r["spread_usd"])
        x_bid, x_ask = r.get("exit_bid"), r.get("exit_ask")
        source: ExitSource
        if x_bid is not None and x_ask is not None:
            source, xslip = "quote", side * ((float(x_bid) if side > 0 else float(x_ask)) - exit_px)
            spread = (spread + float(x_ask) - float(x_bid)) / 2
        elif broker not in DEMO_EXIT_AT_LEVEL and reason in STOP_EXITS and r.get("stop_usd") and \
                not str(r.get("arm_id", "")).startswith(TRAILING_ARM_PREFIXES) and \
                side * ((req - side * float(r["stop_usd"])) - exit_px) >= -MOVED_STOP_USD:
            source, xslip = "level", side * ((req - side * float(r["stop_usd"])) - exit_px)
        elif broker not in DEMO_EXIT_AT_LEVEL and reason in TARGET_EXITS and r.get("target_usd"):
            source, xslip = "level", max(0.0, side * ((req + side * float(r["target_usd"])) - exit_px))
        elif (broker, session) in probe_slip:
            source, xslip = "probe", probe_slip[(broker, session)]
        else:
            source, xslip = "prior", exit_prior_usd
        eslip = side * (fill - req)
        comm = abs(float(r.get("commission") or 0.0)) / oz
        swap_paid = max(0.0, -float(r.get("swap") or 0.0)) / oz
        engine = float(r["cost"]) / oz if r.get("cost") is not None else np.nan
        out.append({"account": acc, "broker": broker, "session": session, "tf": str(r.get("tf") or "?"),
                    "day": sent, "exit_reason": reason, "exit_source": source, "spread": spread,
                    "entry_slip": eslip, "exit_slip": xslip, "commission": comm, "swap_paid": swap_paid,
                    "engine_cost": engine})
    df = pd.DataFrame(out, columns=["account", "broker", "session", "tf", "day", "exit_reason", "exit_source",
                                    "spread", "entry_slip", "exit_slip", "commission", "swap_paid", "engine_cost"])
    if df.empty:
        df["round_trip"] = pd.Series(dtype=float)
        return df
    df["day"] = session_day(df["day"])
    df["round_trip"] = df["spread"] + df["entry_slip"] + df["exit_slip"] + df["commission"]
    return df


def clustered_ci(x: np.ndarray, day: np.ndarray, level: float = 0.95) -> tuple[float, float]:
    """`level` t interval of the mean with the standard error clustered by session day (CR1, G-1 degrees of
    freedom); a single day or a single fill returns the mean twice (no interval to speak of)."""
    x = np.asarray(x, dtype=float)
    m = float(x.mean())
    keys, inv = np.unique(np.asarray(day), return_inverse=True)
    g, n = len(keys), len(x)
    if g < 2 or n < 2:
        return m, m
    sums = np.bincount(inv, weights=x - m)
    var = (g / (g - 1)) * float((sums ** 2).sum()) / n ** 2      # CR1 with one regressor (the mean)
    half = float(stats.t.ppf(0.5 + level / 2, g - 1)) * float(np.sqrt(var))
    return m - half, m + half


class CellCost(FrozenRecord):
    """One account x session x timeframe cell of realised round trips, USD per oz (adverse positive)."""
    n: int = Field(ge=1)
    n_days: int = Field(ge=1)
    mean_usd: float                           # per-trade round trip, slippage signed
    ci95: tuple[float, float]                 # day-clustered t interval of the mean
    p50_usd: float
    p75_usd: float
    p90_usd: float
    spread_usd: float                         # component means (slips signed)
    entry_slip_usd: float
    exit_slip_usd: float
    commission_usd: float
    swap_paid_usd: float                      # reported only: the RiskGate charges swap itself
    point_usd: float                          # spread + max(0, entry slip) + max(0, exit slip) + commission
    exit_sources: dict[str, int]              # exits measured by quote / level, or charged the probe / the prior
    engine_cost_usd: float | None             # the mean the engine charged at send (the old basis), per oz
    sparse: bool
    charged_usd: float = Field(ge=0)          # what the engine charges this cell (module docstring)


class EngineCostRecord(FrozenRecord):
    """A versioned engine cost of record: account -> session -> timeframe (or `all`) -> cell. Immutable once
    written; a new measurement is a new version (a new file), never an edit of an old one."""
    version: str = Field(min_length=1)
    basis: Literal["realised_fills"] = "realised_fills"
    built_utc: UtcTimestamp
    window_utc: tuple[UtcTimestamp, UtcTimestamp]
    charge_rule: ChargeRule = "ub95"
    min_fills: int = Field(MIN_FILLS, ge=1)
    min_days: int = Field(MIN_DAYS, ge=1)
    exit_prior_usd: float = Field(ge=0)
    probe_exit_slip_usd: dict[str, float] = Field(default_factory=dict)   # "<broker>:<session>" -> charged probe slip
    notes: list[str] = Field(default_factory=list)
    cells: dict[str, dict[str, dict[str, CellCost]]]

    def cell(self, account: str, session: str, tf: str | None) -> CellCost | None:
        """The cell the engine charges: the timeframe's own, else the account x session `all` cell; None when the
        record does not cover the account or session (the engine then keeps its legacy table)."""
        by_tf = self.cells.get(account, {}).get(session)
        if not by_tf:
            return None
        return by_tf.get(tf or ALL) or by_tf.get(ALL)

    def charged_usd(self, account: str, session: str, tf: str | None) -> float | None:
        c = self.cell(account, session, tf)
        return None if c is None else c.charged_usd

    @classmethod
    def load(cls, path: str | Path) -> EngineCostRecord:
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))

    def save(self, path: str | Path) -> None:
        p = Path(path)
        if p.exists():
            raise FileExistsError(f"{p} exists: an engine cost of record is immutable, write a new version")
        p.parent.mkdir(parents=True, exist_ok=True)
        head = ("# Engine cost of record (ADR 0092). GENERATED by\n"
                "# `python -m goldbot.research.realised_costs`; immutable: a new measurement is a new version.\n"
                "# USD per oz round trip, adverse positive; aggregates only (no trade row, account login or host).\n")
        p.write_text(head + yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False, width=140))


def _cell(g: pd.DataFrame, pooled: CellCost | None, *, min_fills: int, min_days: int, rule: ChargeRule) -> CellCost:
    x, day = g["round_trip"].to_numpy(dtype=float), g["day"].to_numpy()
    lo, hi = clustered_ci(x, day)
    n_days = int(len(np.unique(day)))
    p50, p75, p90 = (float(np.quantile(x, q)) for q in (0.5, QUANTILE, 0.9))
    mean = {k: float(g[k].mean()) for k in ("spread", "entry_slip", "exit_slip", "commission", "swap_paid")}
    point = mean["spread"] + max(0.0, mean["entry_slip"]) + max(0.0, mean["exit_slip"]) + mean["commission"]
    sparse = len(x) < min_fills or n_days < min_days
    if sparse and pooled is not None:
        charged = max(point, pooled.charged_usd)
    else:
        charged = max(point, hi, p75 if rule == "p75" else -np.inf)
    eng = g["engine_cost"].dropna()
    r4 = lambda v: round(float(v), 4)          # noqa: E731
    return CellCost(n=len(x), n_days=n_days, mean_usd=r4(x.mean()), ci95=(r4(lo), r4(hi)), p50_usd=r4(p50),
                    p75_usd=r4(p75), p90_usd=r4(p90), spread_usd=r4(mean["spread"]),
                    entry_slip_usd=r4(mean["entry_slip"]), exit_slip_usd=r4(mean["exit_slip"]),
                    commission_usd=r4(mean["commission"]), swap_paid_usd=r4(mean["swap_paid"]), point_usd=r4(point),
                    exit_sources={str(k): int(v) for k, v in g["exit_source"].value_counts().sort_index().items()},
                    engine_cost_usd=r4(eng.mean()) if len(eng) else None, sparse=sparse, charged_usd=r4(charged))


def build_record(trades: pd.DataFrame, *, version: str, window: tuple[pd.Timestamp, pd.Timestamp],
                 exit_prior_usd: float, probe_slip: Mapping[tuple[str, str], float],
                 built_utc: pd.Timestamp | None = None, min_fills: int = MIN_FILLS, min_days: int = MIN_DAYS,
                 charge_rule: ChargeRule = "ub95", notes: list[str] | None = None) -> EngineCostRecord:
    """The engine cost of record of `trades` (`trade_costs`): every account x session with fills gets its `all` cell
    and one cell per timeframe seen. A session with no fill of an account is absent (the legacy table charges it)."""
    cells: dict[str, dict[str, dict[str, CellCost]]] = {}
    for (acc, ses), g in trades.groupby(["account", "session"]):
        pooled = _cell(g, None, min_fills=min_fills, min_days=min_days, rule=charge_rule)
        by_tf = {ALL: pooled}
        for tf, h in g.groupby("tf"):
            by_tf[str(tf)] = _cell(h, pooled, min_fills=min_fills, min_days=min_days, rule=charge_rule)
        cells.setdefault(str(acc), {})[str(ses)] = dict(sorted(by_tf.items()))
    return EngineCostRecord(version=version, built_utc=built_utc or pd.Timestamp.now("UTC"), window_utc=window,
                            charge_rule=charge_rule, min_fills=min_fills, min_days=min_days, exit_prior_usd=exit_prior_usd,
                            probe_exit_slip_usd={f"{b}:{s}": round(v, 4) for (b, s), v in sorted(probe_slip.items())},
                            notes=list(notes or []), cells=cells)


def read_rows(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    """(closed exploration rows, cost probe rows, ids void for the cost cells) as plain JSON bodies, read-only."""
    trades = [json.loads(b) for (b,) in conn.execute("SELECT body FROM exploration_trades WHERE closed = 1")]
    probes = [json.loads(b) for (b,) in conn.execute("SELECT body FROM cost_probes")]
    try:
        void = {str(c) for c, s in conn.execute("SELECT client_order_id, scope FROM evidence_exclusions")
                if "cost_cells" in json.loads(s)}
    except sqlite3.OperationalError:
        void = set()
    return trades, probes, void


def _brokers(settings_accounts: Mapping[str, Any] | None, hosts: list[str]) -> dict[str, str]:
    out = {}
    for h in hosts:
        acc, _, brk = h.partition("=")
        out[acc] = brk or (settings_accounts or {}).get(acc, acc.split("-")[0])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--from-day", required=True)
    ap.add_argument("--to-day", required=True, help="exclusive")
    ap.add_argument("--host", action="append", default=[], help="account=broker (repeatable)")
    ap.add_argument("--exit-prior-usd", type=float, default=0.15)
    ap.add_argument("--charge", choices=["ub95", "p75"], default="ub95")
    ap.add_argument("--version")
    ap.add_argument("--out", help="write the record here (refuses an existing file)")
    ap.add_argument("--json", help="also write the per-cell table as JSON (aggregates) here")
    a = ap.parse_args(argv)
    hosts = a.host or ["icm-demo=icm", "icm-probe=icm", "vantage-demo=vantage"]
    brokers = _brokers(None, hosts)
    lo, hi = pd.Timestamp(a.from_day, tz="UTC"), pd.Timestamp(a.to_day, tz="UTC")
    conn = sqlite3.connect(f"file:{Path(a.state_dir, 'aux.db')}?mode=ro", uri=True)
    rows, probes, void = read_rows(conn)
    pslip = probe_exit_slip(probes, brokers, void=void)
    df = trade_costs(rows, brokers, exit_prior_usd=a.exit_prior_usd, probe_slip=pslip, void=void, lo=lo, hi=hi)
    rec = build_record(df, version=a.version or f"ecr-{a.to_day}.draft", window=(lo, hi),
                       exit_prior_usd=a.exit_prior_usd, probe_slip=pslip, charge_rule=a.charge,
                       notes=[f"gold exploration fills sent in [{a.from_day}, {a.to_day}); {len(df)} trades"])
    if a.out:
        rec.save(a.out)
    if a.json:
        Path(a.json).write_text(rec.model_dump_json(indent=1))
    for acc, by_s in rec.cells.items():
        for ses, by_tf in by_s.items():
            for tf, c in by_tf.items():
                print(f"{acc:13s} {ses:8s} {tf:4s} n={c.n:5d} d={c.n_days:2d} mean={c.mean_usd:.3f} "
                      f"ci=[{c.ci95[0]:.3f},{c.ci95[1]:.3f}] p50={c.p50_usd:.3f} p75={c.p75_usd:.3f} "
                      f"p90={c.p90_usd:.3f} spr={c.spread_usd:.3f} es={c.entry_slip_usd:.3f} "
                      f"xs={c.exit_slip_usd:.3f} com={c.commission_usd:.3f} swap={c.swap_paid_usd:.3f} pt={c.point_usd:.3f} "
                      f"eng={c.engine_cost_usd} chg={c.charged_usd:.3f}{' SPARSE' if c.sparse else ''} {c.exit_sources}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
