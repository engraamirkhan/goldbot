"""Deterministic performance attribution (BACKLOG item 12, TRACEABILITY G12): where R is made or lost and why,
computed by code every day from the shadow book (every candidate, taken or not), the engines' fills and orders and
the canonical cost table -> state/attribution.json and the markdown summary state/attribution.md.

Unit of account: one closed shadow trade, in R (its own initial risk, |entry - stop|). The shadow book trades at the
paying side of the quote (labels' mechanics), so its R already pays the spread and nothing else. Per trade:
* spread    the cost table's median spread for the entry session (the widest measured one for a session without
            ticks), added back to give **gross** R (before every cost);
* slippage  entry and exit, the table's market-order cell for the session (its prior where unmeasured), floored at 0;
* commission both sides; swap: the broker's rate times the rollover nights the trade was open (labels'
            `rollover_nights`), positive = paid;
* **net** R = gross - spread - slippage - commission - swap.
Without a cost table the settings priors are used and the spread is unknown (gross = shadow R); the report says so.

Breakdowns (expectancy gross and net, count, t-stat, normal 95% interval, hit rate = share with net R > 0, profit
factor) by timeframe, family, agent, session at entry (Asia/London/New York, `data.calendar` UTC sessions), side,
regime (tercile of the daily realised volatility of the last COMPLETE UTC day before entry, cut against the history
up to the report date), decision (taken / not taken) and exit (stop / target / time / policy). Trading breakdowns count
taken trades on the champion path only: a version's trades from its promotion on (or a version the registry does not
know, i.e. an older book), so champion and challenger candidates on the same bar are never counted twice; challenger
trades get their own line. Calibration (p against the realised target hit, as `jobs.recalibrate` labels it) covers
taken and untaken candidates. Live fills are compared with the table cell they were charged (slippage excess, in $/oz
and, where the order's stop is on record, in R).

Honesty rules: a cell with fewer than `attribution.min_trades` trades is "noise", whatever its mean; otherwise |t| >= 2
is "positive"/"negative" and anything else "indistinguishable from zero". The report changes nothing: no trade, setting,
model or budget. Staff agents read it through `read_attribution` and file hypotheses only via `file_hypothesis`.
Output is a pure function of its inputs and `now` (sorted, rounded), so the same book gives the same bytes.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp, write_atomic
from goldbot.config import AttributionSettings, Settings, tf_seconds
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.engine.shadow import ShadowTrade
from goldbot.execution.costs import CONTRACT_OZ, CostTable
from goldbot.labels.triple_barrier import SwapSpec, rollover_nights

REPORT_FILE = "attribution.json"
SUMMARY_FILE = "attribution.md"
BREAKDOWNS = ("timeframe", "family", "agent", "session", "side", "regime", "decision", "exit")
EXIT_GROUPS = ("target", "stop", "time", "policy")
TERCILES = ("low", "mid", "high")
MIN_VOL_DAYS = 30                 # fewer days of volatility history: every regime is "unknown"
T_SIGNIFICANT = 2.0
COMPONENTS = ("spread", "slippage", "commission", "swap")


def _r(x: float | None, nd: int = 9) -> float | None:
    if x is None or not np.isfinite(x):
        return None
    return round(float(x), nd)


def family_of(agent_id: str) -> str:
    """The specialist family from an agent id (`<family>-g<generation>-<hash>`, specialists/base.py); a bare id is
    its own family (single-agent setups)."""
    head, sep, tail = agent_id.rpartition("-g")
    return head if sep and head and tail.split("-", 1)[0].isdigit() else agent_id


# ------------------------------------------------------------------------------------------------ records
class CostModel(FrozenRecord):
    """Per-oz costs the attribution charges (one canonical table: the shadow book runs on its broker's engine)."""
    spread_usd: dict[str, float]          # session -> median spread; empty = unknown
    slippage_usd: dict[str, float]        # session -> mean market-order slippage per side (measured cells)
    slippage_prior_usd: float
    commission_usd_per_oz: float          # both sides
    swap: SwapSpec | None
    source: str

    @classmethod
    def from_table(cls, table: CostTable, swap: SwapSpec | None = None) -> "CostModel":
        slip = {k.split(":", 1)[0]: s.mean for k, s in table.slippage.items() if k.endswith(":market")}
        return cls(spread_usd={k: s.median for k, s in table.spread.items()}, slippage_usd=slip,
                   slippage_prior_usd=table.slippage_prior_usd,
                   commission_usd_per_oz=2 * table.commission_per_lot_side_usd / CONTRACT_OZ, swap=swap,
                   source=f"cost table {table.account_id} built {table.built_utc:%Y-%m-%d}")

    @classmethod
    def from_settings(cls, settings: Settings) -> "CostModel":
        from goldbot.execution.costs import settings_swap
        canonical = [b for b, cfg in settings.brokers.items() if cfg.canonical_costs]
        side = settings.costs.commission_per_lot_side_usd.get(canonical[0], 0.0) if canonical else 0.0
        return cls(spread_usd={}, slippage_usd={}, slippage_prior_usd=settings.costs.slippage_prior_usd,
                   commission_usd_per_oz=2 * side / CONTRACT_OZ, swap=settings_swap(settings),
                   source="settings priors (no cost table yet; spread unknown, gross = shadow R)")

    def spread(self, session: str) -> float:
        if not self.spread_usd:
            return 0.0
        return self.spread_usd.get(session, max(self.spread_usd.values()))

    def table_slippage(self, session: str) -> float:
        """The cell the engine is charged for this session (signed, $/oz per side)."""
        return self.slippage_usd.get(session, self.slippage_prior_usd)


class VersionRole(FrozenRecord):
    status: str
    promoted_utc: UtcTimestamp | None = None


class Cell(FrozenRecord):
    n: int
    mean_r_gross: float | None
    mean_r_net: float | None
    std_r_net: float | None
    t_stat: float | None
    ci95_net: tuple[float, float] | None
    hit_rate: float | None
    profit_factor: float | None
    verdict: str                          # noise | positive | negative | indistinguishable from zero


class ExitCell(Cell):
    share: float


class TradeRow(FrozenRecord):
    version: str
    agent_id: str
    family: str
    timeframe: str
    side: str
    session: str
    regime: str
    exit: str
    barrier: str
    taken: bool
    p: float
    hit: bool
    entry_utc: UtcTimestamp
    exit_utc: UtcTimestamp
    risk_usd: float
    nights: int
    r_shadow: float
    spread_r: float
    slippage_r: float
    commission_r: float
    swap_r: float
    r_gross: float
    r_net: float


class CostSummary(FrozenRecord):
    source: str
    n: int
    mean_r: dict[str, float | None]       # spread, slippage, commission, swap, total
    share_of_gross: float | None          # total cost / mean gross R (None when gross <= 0)
    killed: int                           # gross R > 0 but net R <= 0
    by_session: dict[str, dict[str, float | None]]


class CalibrationBin(FrozenRecord):
    lo: float
    hi: float
    n: int
    mean_p: float
    hit_rate: float
    noise: bool


class Calibration(FrozenRecord):
    n: int
    mean_p: float | None
    hit_rate: float | None
    ece: float | None
    brier: float | None
    bins: list[CalibrationBin]


class LiveFills(FrozenRecord):
    fills: int
    mean_slippage_usd: float | None
    mean_table_usd: float | None
    mean_excess_usd: float | None
    t_stat: float | None
    mean_excess_r: float | None           # over fills whose order stop is on record
    n_with_risk: int
    verdict: str
    orders: dict[str, int]                # pending_orders rows by status


class AttributionReport(FrozenRecord):
    as_of: UtcTimestamp
    window_from: UtcTimestamp
    min_trades: int
    n_candidates: int
    n_taken: int
    overall: Cell
    challengers: Cell
    breakdowns: dict[str, dict[str, Cell]]
    exits: dict[str, ExitCell]
    costs: CostSummary
    calibration: dict[str, Calibration]
    live: dict[str, LiveFills]
    trades: list[TradeRow]
    notes: list[str]


# ------------------------------------------------------------------------------------------------ statistics
def _verdict(n: int, mean: float | None, t: float | None, std: float | None, min_trades: int) -> str:
    if n < min_trades or mean is None:
        return "noise"
    if t is None:                          # zero dispersion over enough trades: the sign is the evidence
        return "indistinguishable from zero" if mean == 0 or std is None else ("positive" if mean > 0 else "negative")
    if abs(t) >= T_SIGNIFICANT:
        return "positive" if t > 0 else "negative"
    return "indistinguishable from zero"


def cell_stats(gross: np.ndarray, net: np.ndarray, *, min_trades: int) -> Cell:
    """Expectancy cell: count, mean gross and net R, t-stat and normal 95% interval of the net mean, hit rate
    (net R > 0), profit factor (net wins / net losses), and the verdict."""
    g, x = np.asarray(gross, dtype=float), np.asarray(net, dtype=float)
    n = len(x)
    if n == 0:
        return Cell(n=0, mean_r_gross=None, mean_r_net=None, std_r_net=None, t_stat=None, ci95_net=None,
                    hit_rate=None, profit_factor=None, verdict="noise")
    mean = float(x.mean())
    std = float(x.std(ddof=1)) if n > 1 else None
    se = std / np.sqrt(n) if std else None
    t = mean / se if se else None
    ci = (float(mean - 1.96 * se), float(mean + 1.96 * se)) if se else None
    wins, losses = float(x[x > 0].sum()), float(-x[x < 0].sum())
    return Cell(n=n, mean_r_gross=_r(float(g.mean())), mean_r_net=_r(mean), std_r_net=_r(std), t_stat=_r(t),
                ci95_net=(_r(ci[0]) or 0.0, _r(ci[1]) or 0.0) if ci else None, hit_rate=_r(float((x > 0).mean())),
                profit_factor=_r(wins / losses) if losses > 0 else None, verdict=_verdict(n, mean, t, std, min_trades))


def _cell(rows: list[TradeRow], min_trades: int) -> Cell:
    return cell_stats(np.array([r.r_gross for r in rows]), np.array([r.r_net for r in rows]), min_trades=min_trades)


def calibration(rows: list[TradeRow], *, bins: int, min_bin: int) -> Calibration:
    """Predicted p against the realised target hit, in `bins` equal-width bins of p: ECE (count-weighted |mean p -
    hit rate|) and Brier score. A bin with fewer than `min_bin` candidates is marked noise."""
    if not rows:
        return Calibration(n=0, mean_p=None, hit_rate=None, ece=None, brier=None, bins=[])
    p = np.array([r.p for r in rows], dtype=float)
    y = np.array([r.hit for r in rows], dtype=float)
    idx = np.minimum((p * bins).astype(int), bins - 1)
    out, ece = [], 0.0
    for b in range(bins):
        m = idx == b
        k = int(m.sum())
        if not k:
            continue
        mp, hr = float(p[m].mean()), float(y[m].mean())
        ece += k / len(p) * abs(mp - hr)
        out.append(CalibrationBin(lo=round(b / bins, 6), hi=round((b + 1) / bins, 6), n=k, mean_p=_r(mp) or 0.0,
                                  hit_rate=_r(hr) or 0.0, noise=k < min_bin))
    return Calibration(n=len(rows), mean_p=_r(float(p.mean())), hit_rate=_r(float(y.mean())), ece=_r(ece),
                       brier=_r(float(((p - y) ** 2).mean())), bins=out)


# ------------------------------------------------------------------------------------------------ per trade
def _regimes(entries: pd.DatetimeIndex, daily_vol: pd.Series | None, now: pd.Timestamp) -> list[str]:
    """Volatility tercile of the last complete UTC day before each entry (visible at decision time), cut against
    the daily history up to `now`; "unknown" without enough history or without a prior day."""
    if daily_vol is None or daily_vol.empty:
        return ["unknown"] * len(entries)
    idx = pd.DatetimeIndex(daily_vol.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    vol = pd.Series(daily_vol.to_numpy(float), index=idx.normalize()).sort_index().dropna()
    vol = vol[vol.index < now.normalize()]
    if len(vol) < MIN_VOL_DAYS:
        return ["unknown"] * len(entries)
    q1, q2 = float(vol.quantile(1 / 3)), float(vol.quantile(2 / 3))
    out = []
    for t in entries:
        k = int(vol.index.searchsorted(t.normalize(), side="left")) - 1
        if k < 0:
            out.append("unknown")
            continue
        v = float(vol.iloc[k])
        out.append(TERCILES[0] if v <= q1 else TERCILES[1] if v <= q2 else TERCILES[2])
    return out


def _exit_group(barrier: str) -> str:
    return barrier if barrier in ("target", "stop", "time") else "policy"


def trade_rows(trades: list[ShadowTrade], *, costs: CostModel, daily_vol: pd.Series | None, now: pd.Timestamp,
               since: pd.Timestamp) -> list[TradeRow]:
    """Closed trades exited in [since, now], each priced in R with its cost components, sorted deterministically."""
    keep = []
    for t in trades:
        if t.exit_ts is None or t.ret is None or t.exit is None or t.barrier is None:
            continue
        risk = abs(t.entry - t.stop)
        if not np.isfinite(risk) or risk <= 0:
            continue
        tf = pd.Timedelta(seconds=tf_seconds(t.timeframe))
        entry_utc, exit_utc = pd.Timestamp(t.entry_ts) + tf, pd.Timestamp(t.exit_ts) + tf
        if not since <= exit_utc <= now + tf:
            continue
        keep.append((t, risk, entry_utc, exit_utc, t.barrier, float(t.ret)))
    keep.sort(key=lambda x: (x[3], x[2], x[0].version, x[0].agent_id, x[0].side, x[0].timeframe, x[4], x[5],
                             x[0].p, x[0].taken))
    if not keep:
        return []
    entries = pd.DatetimeIndex([k[2] for k in keep])
    sessions = DEFAULT_SESSIONS.session_label(entries)
    regimes = _regimes(entries, daily_vol, now)
    if costs.swap is not None:
        nights = rollover_nights(entries, pd.DatetimeIndex([k[3] for k in keep]), costs.swap.server_tz,
                                 costs.swap.triple_weekday)
    else:
        nights = np.zeros(len(keep), dtype=int)
    rows = []
    for (t, risk, entry_utc, exit_utc, barrier, ret), session, regime, nt in zip(keep, sessions, regimes, nights):
        session = str(session)
        r_shadow = ret * t.entry / risk
        spread = costs.spread(session) / risk
        slip = 2 * max(costs.table_slippage(session), 0.0) / risk
        comm = costs.commission_usd_per_oz / risk
        swap = float(-np.asarray(costs.swap.usd_per_oz(np.array([t.side])))[0]) * int(nt) / risk \
            if costs.swap is not None else 0.0
        gross = r_shadow + spread
        rows.append(TradeRow(version=t.version, agent_id=t.agent_id, family=family_of(t.agent_id),
                             timeframe=t.timeframe, side="long" if t.side > 0 else "short", session=session,
                             regime=regime, exit=_exit_group(barrier), barrier=barrier, taken=t.taken,
                             p=round(float(t.p), 9), hit=barrier == "target", entry_utc=entry_utc, exit_utc=exit_utc,
                             risk_usd=round(risk, 6), nights=int(nt), r_shadow=_r(r_shadow) or 0.0,
                             spread_r=_r(spread) or 0.0, slippage_r=_r(slip) or 0.0, commission_r=_r(comm) or 0.0,
                             swap_r=_r(swap) or 0.0, r_gross=_r(gross) or 0.0,
                             r_net=_r(gross - spread - slip - comm - swap) or 0.0))
    return rows


def _champion_path(roles: dict[str, VersionRole]) -> Callable[[TradeRow], bool]:
    def ok(r: TradeRow) -> bool:
        role = roles.get(r.version)
        if role is None:                   # not in the registry (an older book): it was the one shadow model
            return True
        if role.promoted_utc is not None:
            return r.entry_utc >= role.promoted_utc
        return role.status == "champion"   # a first model registered as champion without a promotion record
    return ok


# ------------------------------------------------------------------------------------------------ live fills
def live_fills(fills: pd.DataFrame, orders: dict[str, Any], costs: CostModel | None, *, min_trades: int) -> LiveFills:
    """Each fill's slippage (filled - requested, signed so positive is worse) against the cost-table cell for its
    session; in R where the order's stop is in the pending_orders table."""
    status = Counter(str(o.get("status", "unknown")) for o in (orders.get("sent") or {}).values())
    need = {"ts_utc", "side", "requested", "filled"}
    if fills is None or fills.empty or not need <= set(fills.columns):
        return LiveFills(fills=0, mean_slippage_usd=None, mean_table_usd=None, mean_excess_usd=None, t_stat=None,
                         mean_excess_r=None, n_with_risk=0, verdict="noise", orders=dict(sorted(status.items())))
    f = fills.sort_values(["ts_utc"] + (["client_order_id"] if "client_order_id" in fills else [])).reset_index(drop=True)
    side = f["side"].to_numpy(float)
    slip = side * (f["filled"].to_numpy(float) - f["requested"].to_numpy(float))
    sess = DEFAULT_SESSIONS.session_label(pd.DatetimeIndex(pd.to_datetime(f["ts_utc"], utc=True)))
    table = np.array([costs.table_slippage(str(s)) if costs is not None else np.nan for s in sess], dtype=float)
    excess = slip - table
    sent = orders.get("sent") or {}
    risk_r = []
    for i, cid in enumerate(f["client_order_id"] if "client_order_id" in f else []):
        sl = (sent.get(str(cid)) or {}).get("sl")
        risk = abs(float(f["requested"].iloc[i]) - float(sl)) if sl is not None else 0.0
        if risk > 0 and np.isfinite(excess[i]):
            risk_r.append(excess[i] / risk)
    ok = excess[np.isfinite(excess)]
    st = cell_stats(ok, ok, min_trades=min_trades)
    return LiveFills(fills=len(f), mean_slippage_usd=_r(float(slip.mean())),
                     mean_table_usd=_r(float(np.nanmean(table))) if np.isfinite(table).any() else None,
                     mean_excess_usd=st.mean_r_net, t_stat=st.t_stat,
                     mean_excess_r=_r(float(np.mean(risk_r))) if risk_r else None, n_with_risk=len(risk_r),
                     verdict=st.verdict if costs is not None else "noise", orders=dict(sorted(status.items())))


# ------------------------------------------------------------------------------------------------ report
def build_report(trades: list[ShadowTrade], *, now: pd.Timestamp, costs: CostModel, settings: AttributionSettings,
                 daily_vol: pd.Series | None = None, roles: dict[str, VersionRole] | None = None,
                 fills: dict[str, pd.DataFrame] | None = None, orders: dict[str, dict[str, Any]] | None = None,
                 tables: dict[str, CostModel | None] | None = None) -> AttributionReport:
    since = now - pd.Timedelta(days=settings.window_days)
    m = settings.min_trades
    rows = trade_rows(trades, costs=costs, daily_vol=daily_vol, now=now, since=since)
    champ = _champion_path(roles or {})
    path = [r for r in rows if champ(r)]
    taken = [r for r in path if r.taken]
    challenger = [r for r in rows if r.taken and not champ(r)]

    def split(key: Callable[[TradeRow], str], sample: list[TradeRow]) -> dict[str, Cell]:
        groups: dict[str, list[TradeRow]] = {}
        for r in sample:
            groups.setdefault(key(r), []).append(r)
        return {k: _cell(groups[k], m) for k in sorted(groups)}

    breakdowns = {
        "timeframe": split(lambda r: r.timeframe, taken), "family": split(lambda r: r.family, taken),
        "agent": split(lambda r: r.agent_id, taken), "session": split(lambda r: r.session, taken),
        "side": split(lambda r: r.side, taken), "regime": split(lambda r: r.regime, taken),
        "decision": split(lambda r: "taken" if r.taken else "not_taken", path),
        "exit": split(lambda r: r.exit, taken)}
    exits = {k: ExitCell(**c.model_dump(), share=round(c.n / len(taken), 9)) for k, c in breakdowns["exit"].items()}

    comp = {c: np.array([getattr(r, f"{c}_r") for r in taken], dtype=float) for c in COMPONENTS}
    total = sum(comp.values()) if taken else np.zeros(0)
    gross_mean = float(np.mean([r.r_gross for r in taken])) if taken else 0.0
    mean_r: dict[str, float | None] = {c: _r(float(v.mean())) if len(v) else None for c, v in comp.items()}
    mean_r["total"] = _r(float(np.mean(total))) if taken else None
    by_session: dict[str, dict[str, float | None]] = {}
    for s in sorted({r.session for r in taken}):
        sr = [r for r in taken if r.session == s]
        by_session[s] = {c: _r(float(np.mean([getattr(r, f"{c}_r") for r in sr]))) for c in COMPONENTS}
    cost_summary = CostSummary(source=costs.source, n=len(taken), mean_r=mean_r,
                               share_of_gross=_r(float(np.mean(total)) / gross_mean) if taken and gross_mean > 0 else None,
                               killed=sum(1 for r in taken if r.r_gross > 0 >= r.r_net), by_session=by_session)

    cal = {k: calibration(v, bins=settings.calibration_bins, min_bin=settings.calibration_min_bin)
           for k, v in (("taken", taken), ("not_taken", [r for r in path if not r.taken]), ("all", path))}

    live = {acc: live_fills(f[pd.to_datetime(f["ts_utc"], utc=True).between(since, now)] if not f.empty else f,
                            (orders or {}).get(acc, {}), (tables or {}).get(acc), min_trades=m)
            for acc, f in sorted((fills or {}).items())}

    notes = [f"costs: {costs.source}"]
    if not costs.spread_usd:
        notes.append("spread unknown: gross R equals the shadow R, which already pays the quoted spread")
    if any(r.regime == "unknown" for r in taken):
        notes.append("regime unknown for trades without a completed day of 1h-bar volatility before entry")
    if fills is not None and not any(v.fills for v in live.values()):
        notes.append("no engine fills in the window: live slippage cannot be compared with the cost table")
    notes.append("live realised R per position is not recorded yet (engine trade record open, BACKLOG item 8); "
                 "expectancy here is the shadow book's")
    recent = taken[-settings.trade_rows:] if settings.trade_rows else []
    return AttributionReport(as_of=now, window_from=since, min_trades=m, n_candidates=len(path), n_taken=len(taken),
                             overall=_cell(taken, m), challengers=_cell(challenger, m), breakdowns=breakdowns,
                             exits=exits, costs=cost_summary, calibration=cal, live=live, trades=recent, notes=notes)


# ------------------------------------------------------------------------------------------------ output
def _fmt(x: float | None, spec: str = "+.3f") -> str:
    return "" if x is None else format(x, spec)


def _rows(title: str, cells: dict[str, Cell], limit: int = 25) -> list[str]:
    out = [f"## {title}", "", "| cell | n | gross R | net R | t | 95% CI net | hit | PF | verdict |",
           "|---|---:|---:|---:|---:|---|---:|---:|---|"]
    items = sorted(cells.items(), key=lambda kv: (-kv[1].n, kv[0]))[:limit]
    for k, c in items:
        ci = f"{c.ci95_net[0]:+.3f} .. {c.ci95_net[1]:+.3f}" if c.ci95_net else ""
        out.append(f"| {k} | {c.n} | {_fmt(c.mean_r_gross)} | {_fmt(c.mean_r_net)} | {_fmt(c.t_stat, '+.2f')} | {ci} | "
                   f"{_fmt(c.hit_rate, '.0%')} | {_fmt(c.profit_factor, '.2f')} | {c.verdict} |")
    if len(cells) > limit:
        out.append(f"| ... {len(cells) - limit} more (read_attribution section) | | | | | | | | |")
    return out + [""]


def render_markdown(rep: AttributionReport) -> str:
    """The summary the staff agents read (read_attribution section=summary)."""
    lines = [f"# Performance attribution, {rep.as_of:%Y-%m-%d %H:%M} UTC", "",
             "Computed by code from the shadow book (every candidate, taken or not), engine fills, orders and the cost "
             "table. It changes nothing: no trade, setting, model or budget. Hypotheses go through file_hypothesis only.",
             f"Cells with fewer than {rep.min_trades} trades are **noise** and are not evidence; 'indistinguishable from "
             "zero' means |t| < 2. Expectancy is in R (initial risk); hit = net R > 0; PF = profit factor (net).", "",
             f"Window: exits {rep.window_from:%Y-%m-%d} .. {rep.as_of:%Y-%m-%d}. Champion-path candidates {rep.n_candidates} "
             f"(taken {rep.n_taken}, not taken {rep.n_candidates - rep.n_taken}); challenger trades {rep.challengers.n}.", ""]
    lines += _rows("Expectancy", {"all taken (champion path)": rep.overall, "challengers (taken)": rep.challengers})
    titles = {"timeframe": "By timeframe", "family": "By family", "agent": "By agent", "session": "By session",
              "side": "By side", "regime": "By regime (volatility tercile)", "decision": "By decision (taken vs not)"}
    for key, title in titles.items():
        lines += _rows(title, rep.breakdowns[key])
    lines += ["## Exit mix (taken)", "", "| exit | n | share | net R | t | verdict |", "|---|---:|---:|---:|---:|---|"]
    for k in EXIT_GROUPS:
        if k in rep.exits:
            e = rep.exits[k]
            lines.append(f"| {k} | {e.n} | {e.share:.0%} | {_fmt(e.mean_r_net)} | {_fmt(e.t_stat, '+.2f')} | {e.verdict} |")
    c = rep.costs
    lines += ["", "## Costs per taken trade (R)", "", f"Source: {c.source}.", "",
              "| session | " + " | ".join(COMPONENTS) + " |", "|---|" + "---:|" * len(COMPONENTS),
              "| all | " + " | ".join(_fmt(c.mean_r.get(k), ".4f") for k in COMPONENTS) + " |"]
    for s, v in c.by_session.items():
        lines.append(f"| {s} | " + " | ".join(_fmt(v.get(k), ".4f") for k in COMPONENTS) + " |")
    share = f"; {c.share_of_gross:.0%} of gross" if c.share_of_gross is not None else ""
    lines += ["", f"Total cost {_fmt(c.mean_r.get('total'), '.4f')} R per trade{share}; trades the costs turned from "
              f"winners into losers: {c.killed} of {c.n}.", "",
              "## Calibration (p vs target hit)", "", "| sample | n | mean p | hit rate | ECE | Brier |",
              "|---|---:|---:|---:|---:|---:|"]
    for k, cal in rep.calibration.items():
        lines.append(f"| {k} | {cal.n} | {_fmt(cal.mean_p, '.3f')} | {_fmt(cal.hit_rate, '.3f')} | {_fmt(cal.ece, '.3f')} | "
                     f"{_fmt(cal.brier, '.3f')} |")
    bins = rep.calibration["all"].bins
    if bins:
        lines += ["", "| p bin (all) | n | mean p | hit rate | |", "|---|---:|---:|---:|---|"]
        lines += [f"| {b.lo:.1f}-{b.hi:.1f} | {b.n} | {b.mean_p:.3f} | {b.hit_rate:.3f} | {'noise' if b.noise else ''} |"
                  for b in bins]
    if rep.live:
        lines += ["", "## Live fills vs the cost table", "",
                  "| account | fills | slippage $/oz | table $/oz | excess $/oz | excess R | t | verdict | orders |",
                  "|---|---:|---:|---:|---:|---:|---:|---|---|"]
        for acc, lf in rep.live.items():
            orders = ", ".join(f"{k} {v}" for k, v in lf.orders.items())
            lines.append(f"| {acc} | {lf.fills} | {_fmt(lf.mean_slippage_usd, '.3f')} | {_fmt(lf.mean_table_usd, '.3f')} | "
                         f"{_fmt(lf.mean_excess_usd, '+.3f')} | {_fmt(lf.mean_excess_r, '+.4f')} | {_fmt(lf.t_stat, '+.2f')} | "
                         f"{lf.verdict} | {orders} |")
    lines += ["", "## Notes", ""] + [f"- {n}" for n in rep.notes]
    return "\n".join(lines) + "\n"


def save_report(rep: AttributionReport, state_dir: str | Path) -> None:
    d = Path(state_dir)
    write_atomic(d / REPORT_FILE, rep.model_dump_json(indent=1), durable=False)
    write_atomic(d / SUMMARY_FILE, render_markdown(rep), durable=False)


def load_report(state_dir: str | Path) -> dict[str, Any] | None:
    f = Path(state_dir) / REPORT_FILE
    if not f.exists():
        return None
    data: dict[str, Any] = json.loads(f.read_text())
    return data
