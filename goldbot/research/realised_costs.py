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
           construction, `execution.costs.DEMO_EXIT_AT_LEVEL`): the MEAN of the same broker's scheduled-probe
           market-exit slip in that session (`cost_probes`, exit quote recorded: prereg K11; hold exits within
           2 s of their quote only, `probe_exit_slip`), floored at 0 (quant review of ADR 0092, point 5; version .1
           used the p75 of every probe with a quote, its file says so in `probe_exit_stat`);
  `prior`  no probe evidence for the broker and session: the settings slippage prior;
* commission = |booked commission| / (lots x contract x account-per-USD rate); swap paid is reported, NOT charged
  (the RiskGate charges swap itself, `Engine._swap_usd_per_oz`, from the broker's measured rates);
* round trip = spread + entry slip + exit slip + commission;
* engine cost = the cost the engine charged at send (`cost`, account currency) per oz, for the old-vs-new comparison;
* gross = side x (exit - fill) + spread + entry slip + exit slip: the fill's move before every price cost (a stop at its
  level reads -stop + spread), used by the edge cap (`arm_stops.edge_cap_states`, quant C3).

Large exits (quant C1): every level-measured exit slip above `LARGE_SLIP_USD` is checked against the broker's own
recorded ticks (state/ticks/<account>/, `goldbot.data.ticks`) by `large_exit_error`: kept when a quote reached the level
and the exit price was quoted within `EXIT_QUOTE_WINDOW_S` before the exit (a genuine gap: the mean is the estimator,
nothing is trimmed); dropped with its reason when the ticks never reached the level or never quoted the exit price (a
data error); kept and counted when no ticks cover it (unverifiable is not an error). The counts go in the record.

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
point and its account x session `all` cell's charge (sparse never undercuts pooled). A cell under `BROKER_POOL_DAYS`
session days is pooled per BROKER (quant C1: ICM's accounts share one server): it charges the larger of its own point and
the broker x session x timeframe cell's charge (`pooled_from: broker`). Gold only: other instruments keep the legacy table (their cells are not measured
here). Aggregates only in the output: no trade row, account login, balance or host.

    python -m goldbot.research.realised_costs --state-dir state --from-day 2026-10-26 --to-day 2026-11-20 \
        --version ecr-2026-11-20.2 --out config/engine_costs/ecr-2026-11-20.2.yaml
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from collections.abc import Callable, Collection, Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import yaml
from pydantic import Field
from scipy import stats

from goldbot.base import FrozenRecord, UtcTimestamp, load_record
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
ProbeStat = Literal["p75", "mean"]
PROBE_STAT: ProbeStat = "mean"        # the probe market-exit slip charged to an exit with no measurement of its own
PROBE_HOLD_EXIT = "cost_probe_hold"   # engine.cost_probe.HOLD_EXIT: the market close sent on the recorded quote
PROBE_QUOTE_MAX_LAG_S = 2.0           # its deal no further than this from that quote (else the quote is not the exit's)
LARGE_SLIP_USD = 2.0                  # quant C1: a level exit slip above this is checked against the broker's ticks
EXIT_QUOTE_WINDOW_S = 10.0            # the exit price must be quoted this close before the exit (or 5 s after it)
EXIT_QUOTE_AFTER_S = 5.0
QUOTE_TOL_USD = 0.01
BROKER_POOL_DAYS = 10                 # quant C1: a cell under this many session days is pooled per broker
DROP_NEVER_REACHED = "ticks never reached the level"
DROP_NOT_QUOTED = "exit price not quoted at the exit"
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


def session_of(ts: Any) -> str:
    """The engine's session label of one UTC stamp (the cell a decision at `ts` is charged on)."""
    return str(_sessions([ts])[0])


def probe_exit_slip(probes: Iterable[Mapping[str, Any]], brokers: Mapping[str, str], *,
                    void: Collection[str] = frozenset(), stat: ProbeStat = PROBE_STAT) -> dict[tuple[str, str], float]:
    """(broker, session) -> the `stat` (mean, or p75) of scheduled probes' market-exit slip against the exit-side quote
    recorded when the close was sent (side x (touch - exit price)), floored at 0. Probes of one broker pool across its
    accounts (one server, one execution); never across brokers (ADR 0043). Only a hold exit (the close sent on that
    quote) whose deal is within PROBE_QUOTE_MAX_LAG_S of the quote counts: a probe the broker stopped carries the quote
    of a later hold pass (up to 888 s after its deal on vantage-demo, 2026-11-02), which is not its exit's."""
    rows = [p for p in probes if p.get("purpose") == "cost_measurement" and p.get("exit_price") is not None
            and p.get("exit_bid") is not None and p.get("exit_ask") is not None
            and p.get("exit_reason") == PROBE_HOLD_EXIT and p.get("exit_utc") and p.get("exit_quote_utc")
            and abs((pd.Timestamp(p["exit_utc"]) - pd.Timestamp(p["exit_quote_utc"])).total_seconds())
            <= PROBE_QUOTE_MAX_LAG_S
            and p.get("account_id") in brokers and p.get("client_order_id") not in void
            and (p.get("symbol") in (None, "XAUUSD"))]
    if not rows:
        return {}
    side = np.array([float(p["side"]) for p in rows])
    touch = np.array([float(p["exit_bid"]) if p["side"] > 0 else float(p["exit_ask"]) for p in rows])
    slip = side * (touch - np.array([float(p["exit_price"]) for p in rows]))
    df = pd.DataFrame({"broker": [brokers[p["account_id"]] for p in rows],
                       "session": _sessions(p["sent_utc"] for p in rows), "slip": slip})
    pick = (lambda x: x.mean()) if stat == "mean" else (lambda x: x.quantile(0.75))
    return {(str(b), str(s)): max(0.0, float(pick(g["slip"]))) for (b, s), g in df.groupby(["broker", "session"])}


COLUMNS = ["account", "broker", "session", "tf", "day", "exit_reason", "exit_source", "spread", "entry_slip",
           "exit_slip", "commission", "swap_paid", "engine_cost", "client_order_id", "arm_id", "stop_usd", "gross",
           "sent_utc"]


def exit_level(r: Mapping[str, Any], broker: str) -> float | None:
    """The level a stop or target exit is measured against (the request -/+ the gate's distance), or None when the
    exit is not measurable at its level: another exit, a trailing arm or a moved stop, a broker whose demo exits fill
    at the level by construction, or a row without the distance."""
    reason, side, req = str(r.get("exit_reason") or ""), int(r["side"]), float(r["requested_price"])
    if broker in DEMO_EXIT_AT_LEVEL or r.get("exit_price") is None:
        return None
    if reason in STOP_EXITS and r.get("stop_usd") and not str(r.get("arm_id", "")).startswith(TRAILING_ARM_PREFIXES):
        level = req - side * float(r["stop_usd"])
        return level if side * (level - float(r["exit_price"])) >= -MOVED_STOP_USD else None
    if reason in TARGET_EXITS and r.get("target_usd"):
        return req + side * float(r["target_usd"])
    return None


def large_exit_error(r: Mapping[str, Any], ticks: pd.DataFrame | None, broker: str = "icm") -> str | None:
    """Quant C1: the data-error reason of a level exit slipping more than LARGE_SLIP_USD, or None (kept: genuine, not
    large, or unverifiable without ticks). `ticks`: ts (UTC), bid, ask of the account's broker around the exit. Genuine
    when an exit-side quote reached the level after the send, and the exit price was quoted between the later of that
    first touch and EXIT_QUOTE_WINDOW_S before the exit, and EXIT_QUOTE_AFTER_S after it."""
    level = exit_level(r, broker)
    if level is None:
        return None
    side, exit_px = int(r["side"]), float(r["exit_price"])
    if side * (level - exit_px) <= LARGE_SLIP_USD or ticks is None or ticks.empty:
        return None
    ts = pd.to_datetime(ticks["ts"], utc=True)
    ex_t, sent = pd.Timestamp(r["exit_utc"]), pd.Timestamp(r["sent_utc"])
    px = (ticks["bid"] if side > 0 else ticks["ask"]).to_numpy(dtype=float)
    stop = str(r.get("exit_reason") or "") in STOP_EXITS
    reached = (side * (px - level) <= 0) if stop else (side * (px - level) >= 0)
    live = ((ts >= sent) & (ts <= ex_t + pd.Timedelta(seconds=EXIT_QUOTE_AFTER_S))).to_numpy()
    if not (reached & live).any():
        return DROP_NEVER_REACHED
    first = ts[reached & live].min()
    near = live & (ts >= max(first, ex_t - pd.Timedelta(seconds=EXIT_QUOTE_WINDOW_S))).to_numpy()
    if not near.any() or not (px[near].min() - QUOTE_TOL_USD <= exit_px <= px[near].max() + QUOTE_TOL_USD):
        return DROP_NOT_QUOTED
    return None


def screen_large_exits(df: pd.DataFrame, rows: Mapping[str, Mapping[str, Any]],
                       tick_source: Callable[[Mapping[str, Any]], pd.DataFrame | None]
                       ) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Quant C1 over `trade_costs` rows: every level exit slip above LARGE_SLIP_USD checked against its ticks
    (`tick_source(row)`); data errors dropped, counted by reason. Returns (kept rows, the audit for the record)."""
    big = df[(df["exit_source"] == "level") & (df["exit_slip"] > LARGE_SLIP_USD)]
    audit: dict[str, Any] = {"checked": int(len(big)), "genuine": 0, "unverified_no_ticks": 0, "dropped": {}}
    drop: list[str] = []
    for _, t in big.iterrows():
        r = rows[str(t["client_order_id"])]
        ticks = tick_source(r)
        if ticks is None or ticks.empty:
            audit["unverified_no_ticks"] += 1
            continue
        why = large_exit_error(r, ticks, str(t["broker"]))
        if why is None:
            audit["genuine"] += 1
        else:
            audit["dropped"][why] = audit["dropped"].get(why, 0) + 1
            drop.append(str(t["client_order_id"]))
    return df[~df["client_order_id"].isin(drop)], audit


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
        level = exit_level(r, broker)
        if x_bid is not None and x_ask is not None:
            source, xslip = "quote", side * ((float(x_bid) if side > 0 else float(x_ask)) - exit_px)
            spread = (spread + float(x_ask) - float(x_bid)) / 2
        elif level is not None and reason in STOP_EXITS:
            source, xslip = "level", side * (level - exit_px)
        elif level is not None:
            source, xslip = "level", max(0.0, side * (level - exit_px))
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
                    "engine_cost": engine, "client_order_id": str(r.get("client_order_id")),
                    "arm_id": str(r.get("arm_id") or ""), "stop_usd": float(r.get("stop_usd") or np.nan),
                    "gross": side * (exit_px - fill) + spread + eslip + xslip, "sent_utc": sent})
    df = pd.DataFrame(out, columns=COLUMNS)
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
    pooled_from: Literal["account", "broker"] = "account"   # broker: under BROKER_POOL_DAYS, charged on the broker


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
    probe_exit_stat: ProbeStat = "p75"        # how the probe slip was taken (version .1: p75; from .2 the mean)
    brokers: dict[str, str] = Field(default_factory=dict)    # account -> broker key of the measured accounts
    broker_pool_days: int | None = None       # cells under this many days pooled per broker (None: never, as .1)
    large_exits: dict[str, Any] = Field(default_factory=dict)   # quant C1 audit: checked, genuine, unverified, dropped
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
        """A version file, read tolerantly (ADR 0010): a field a newer release added is dropped and logged."""
        return load_record(EngineCostRecord, yaml.safe_load(Path(path).read_text()), source="engine_cost_record")

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


def _cells(g: pd.DataFrame, *, min_fills: int, min_days: int, rule: ChargeRule) -> dict[str, CellCost]:
    """One account's (or broker's) cells of one session: the pooled `all` and one per timeframe seen."""
    pooled = _cell(g, None, min_fills=min_fills, min_days=min_days, rule=rule)
    by_tf = {ALL: pooled}
    for tf, h in g.groupby("tf"):
        by_tf[str(tf)] = _cell(h, pooled, min_fills=min_fills, min_days=min_days, rule=rule)
    return by_tf


def _to_broker(own: CellCost, broker: CellCost | None) -> CellCost:
    """A thin account cell charged on its broker's cell, never below its own point (quant C1)."""
    if broker is None:
        return own
    return own.model_copy(update={"pooled_from": "broker",
                                  "charged_usd": round(max(own.point_usd, broker.charged_usd), 4)})


def build_record(trades: pd.DataFrame, *, version: str, window: tuple[pd.Timestamp, pd.Timestamp],
                 exit_prior_usd: float, probe_slip: Mapping[tuple[str, str], float],
                 built_utc: pd.Timestamp | None = None, min_fills: int = MIN_FILLS, min_days: int = MIN_DAYS,
                 charge_rule: ChargeRule = "ub95", notes: list[str] | None = None,
                 brokers: Mapping[str, str] | None = None, probe_stat: ProbeStat = PROBE_STAT,
                 large_exits: Mapping[str, Any] | None = None) -> EngineCostRecord:
    """The engine cost of record of `trades` (`trade_costs`): every account x session with fills gets its `all` cell
    and one cell per timeframe seen. A session with no fill of an account is absent (the legacy table charges it).
    With `brokers` (account -> broker) a cell under BROKER_POOL_DAYS session days is charged on its broker's cell."""
    kw = dict(min_fills=min_fills, min_days=min_days, rule=charge_rule)
    by_broker: dict[tuple[str, str], dict[str, CellCost]] = {}
    if brokers:
        b = trades.assign(broker=trades["account"].map(lambda a: brokers.get(str(a), str(a))))
        by_broker = {(str(k), str(s)): _cells(g, **kw) for (k, s), g in b.groupby(["broker", "session"])}  # type: ignore[arg-type]
    cells: dict[str, dict[str, dict[str, CellCost]]] = {}
    for (acc, ses), g in trades.groupby(["account", "session"]):
        own = _cells(g, **kw)  # type: ignore[arg-type]
        if brokers:
            pool = by_broker.get((brokers.get(str(acc), str(acc)), str(ses)), {})
            own = {tf: _to_broker(c, pool.get(tf)) if c.n_days < BROKER_POOL_DAYS else c for tf, c in own.items()}
        cells.setdefault(str(acc), {})[str(ses)] = dict(sorted(own.items()))
    return EngineCostRecord(version=version, built_utc=built_utc or pd.Timestamp.now("UTC"), window_utc=window,
                            charge_rule=charge_rule, min_fills=min_fills, min_days=min_days, exit_prior_usd=exit_prior_usd,
                            probe_exit_slip_usd={f"{b}:{s}": round(v, 4) for (b, s), v in sorted(probe_slip.items())},
                            probe_exit_stat=probe_stat, brokers=dict(brokers or {}),
                            broker_pool_days=BROKER_POOL_DAYS if brokers else None,
                            large_exits=dict(large_exits or {}), notes=list(notes or []), cells=cells)


def current_record(path: str | Path | None) -> EngineCostRecord | None:
    """The record settings `costs.engine_record` names (a relative path from the repo root), for the daily review's
    stops; None when unset or unreadable (logged: the stops then read the costs as recorded)."""
    if not path:
        return None
    p = Path(path) if Path(path).is_absolute() else Path(__file__).parents[2] / path
    try:
        return EngineCostRecord.load(p)
    except Exception as exc:        # noqa: BLE001 - any unreadable record: the stops read the recorded costs
        logging.getLogger("goldbot.research").error("engine cost of record %s unreadable (%s)", p, type(exc).__name__)
        return None


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


class TickFiles:
    """The broker tick record read for the C1 check: the account's server-date days around an exit, through
    `goldbot.data.ticks.load_day` (sha256-checked against its index; live demo ticks, never research bars, so the
    research holdout does not apply). A day missing or failing its digest reads as no ticks (counted unverified)."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.cache: dict[tuple[str, str], pd.DataFrame | None] = {}

    def _day(self, account: str, day: str) -> pd.DataFrame | None:
        from goldbot.data.ticks import load_day
        if (account, day) not in self.cache:
            try:
                self.cache[(account, day)] = load_day(account, day, state_dir=self.state_dir)
            except (FileNotFoundError, ValueError):
                self.cache[(account, day)] = None
        return self.cache[(account, day)]

    def around(self, r: Mapping[str, Any]) -> pd.DataFrame | None:
        t = pd.Timestamp(r["exit_utc"])
        days = pd.date_range((t - pd.Timedelta(days=1)).date(), (t + pd.Timedelta(days=1)).date())
        frames = [f for d in days if (f := self._day(str(r["account_id"]), d.date().isoformat())) is not None]
        if not frames:
            return None
        df = pd.concat(frames).rename(columns={"ts_utc": "ts"})
        lo, hi = t - pd.Timedelta(hours=2), t + pd.Timedelta(minutes=1)
        df = df[(df["ts"] >= lo) & (df["ts"] <= hi)].sort_values("ts")
        return None if df.empty else df


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
    ticks = TickFiles(Path(a.state_dir))
    n_all = len(df)
    df, audit = screen_large_exits(df, {str(r.get("client_order_id")): r for r in rows}, ticks.around)
    rec = build_record(df, version=a.version or f"ecr-{a.to_day}.draft", window=(lo, hi),
                       exit_prior_usd=a.exit_prior_usd, probe_slip=pslip, charge_rule=a.charge, brokers=brokers,
                       large_exits=audit,
                       notes=[f"gold exploration fills sent in [{a.from_day}, {a.to_day}); {n_all} trades measured, "
                              f"{n_all - len(df)} dropped as tick-checked data errors (large_exits)"])
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
