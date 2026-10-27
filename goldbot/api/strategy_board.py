"""The strategy board (docs/design/trades-ux-2026-10-27.md §2.3, §3.4, item 132; ADR 0050, 0051):
`GET /api/strategies/board`. Read-only: nothing here writes, sizes or orders.

One row per family (config/exploration_arms.yaml rule, labels from config/strategy_labels.yaml), its arms nested as
variants (timeframes, broker twins). Progress follows each pre-registration's READ UNIT (the evaluator's
config/arm_reads.yaml; quant 2026-10-27): an arm read alone carries its n, read size, ETA and status itself; a family
read pooled (W1, W2, H-80, H-81: "the n is the family's pooled count") carries them once, in the family's `pooled`
progress, and its arms only their own counts. Per arm also: fills today and in 7 days, open trades, and the control
it is read against (strategy_labels: named, own family, else the sleeve's random control).

Sources: the trade ledger's rows (goldbot/api/trades_ledger.py, one build per LEDGER_TTL_S) and, for n and the H-61
stop, `labs.arm_statuses` (the same count and stop the daily report and the daily review use: void and held-out
trades out of n, every admissible trade in the stop). An arm whose n is its stop exits (S0R, `counts`) counts its
counted trades closed at the stop. A unit's n is the sum over its `counted` arms (H-80: BXR's fills, not the virtual
BXT/BXF exits on them).

Progress and reads (ADR 0050, 0051; the per-family results before a read are the ranking's since ADR 0053,
GET /api/strategies/ranking, which the Strategies tab shows by default with this board's progress beside it):
* Before its unit's read a variant or pooled progress carries counts, progress, ETA, status and annealing state.
* "'Near its stop' only once fired": status is warming, running or retired (at its H-61 stop; a pooled unit when all
  its counted arms are); a running loss is "running" whatever its size.
* After the read (the unit listed in config/arm_reads.yaml `reads`): the recorded verdict and the read sample's
  figures, frozen (the unit's first n counted trades closed by `read_utc`), with the rest labelled "since read". The
  difference against the control is the evaluator's RECORDED comparison only (the prereg defines it, e.g. H-81's
  day-paired family minus control, and never reads the control itself); nothing is computed here (quant 2026-10-27).
* Families are ordered by state, then closeness to the read (n / read size), then name: the "By progress to read"
  order (the ranked order is the ranking's).
* Lab totals (per account and sleeve, 7 days): counts and money after costs (no withholding since ADR 0053).
Engine strategies and shadow models are not pre-registered arms: full figures, always with n and a 90% CI.
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

import pandas as pd

from goldbot.api import trades_ledger as tl
from goldbot.api.schema import (
    AnnealState,
    ArmStatusWord,
    BoardFamily,
    BoardState,
    BoardVariant,
    EngineStrategy,
    LabTotal,
    Learning,
    ReadFigures,
    ReadProgress,
    SinceRead,
    StrategyBoardView,
    TradeRow,
)
from goldbot.api.strategy_labels import ArmInfo, ArmRead, Catalog, account_label, catalog
from goldbot.ops.report_days import day_bounds, report_day
from goldbot.telegram import labs

log = logging.getLogger(__name__)

__all__ = ["BOARD_TTL_S", "PACE_DAYS", "Z90", "board", "retired_arms"]

BOARD_TTL_S = 30.0                  # one build serves every poll for 30 s (the Strategies tab polls every 60 s)
PACE_DAYS = 7                       # the ETA's pace: counted trades over the last 7 opportunity days
Z90 = 1.645                         # two-sided 90% normal interval
TF_ORDER = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
STATE_ORDER: tuple[BoardState, ...] = ("proven", "candidate", "running", "retired", "control")
ANNEAL_RANK: dict[AnnealState, int] = {"P": 0, "C1": 1, "E": 2, "R": 3}
_ARM_KINDS = frozenset({"exploration", "control"})
_ENGINE_KINDS = ("real", "strategy", "shadow")


def _ci(xs: Sequence[float]) -> tuple[float, float] | None:
    """Mean +/- Z90 standard errors; None under 2 values."""
    if len(xs) < 2:
        return None
    half = Z90 * stdev(xs) / math.sqrt(len(xs))
    m = fmean(xs)
    return round(m - half, 3), round(m + half, 3)


def _money(rows: Sequence[TradeRow]) -> tuple[float | None, str | None]:
    """The rows' net in their one currency; None when a net is unknown or currencies differ (never summed)."""
    ccys = {r.currency for r in rows}
    if not rows or None in ccys or len(ccys) != 1 or any(r.pnl is None for r in rows):
        return None, next(iter(ccys), None) if len(ccys) == 1 else None
    return round(sum(r.pnl or 0.0 for r in rows), 2), next(iter(ccys))


def _tf_key(tf: str | None) -> int:
    return TF_ORDER.index(tf) if tf in TF_ORDER else len(TF_ORDER)


def _statuses(state: Path, now: pd.Timestamp, store: Any,
              held_out: Collection[str] | None) -> tuple[dict[str, labs.ArmStatus], bool]:
    """arm -> its n toward the read and H-61 band (labs.arm_statuses over every account with an arm trade); False
    when aux.db cannot be read (every n is then missing, not zero)."""
    try:
        with labs.aux(state) as conn:
            if conn is None:
                return {}, True
            accounts = [str(a) for (a,) in conn.execute("SELECT DISTINCT account_id FROM exploration_trades")]
            arms = labs.arm_statuses(conn, accounts, store=store, now=now, held_out=held_out)
    except Exception as exc:              # noqa: BLE001 - reporting only: the board names the source, never fails
        log.warning("strategy board: arm status unreadable (%s)", type(exc).__name__)
        return {}, False
    return {a.arm_id: a for a in arms}, True


def retired_arms(state_dir: str | Path, now: pd.Timestamp, *, store: Any = None,
                 held_out: Collection[str] | None = None) -> tuple[set[str], bool]:
    """The strategy arms at their pre-registered stop (the ranking's retired band), and False when aux.db cannot be
    read (none is then known retired)."""
    statuses, ok = _statuses(Path(state_dir), now, store, held_out)
    return {a for a, s in statuses.items() if s.band == "at_stop" and not s.control}, ok


# ------------------------------------------------------------------------------------------------ arms and units
@dataclass
class _Arm:
    """One arm's inputs: what it counts toward its unit."""
    arm_id: str
    info: ArmInfo | None
    rows: list[TradeRow]
    control: bool
    n: int                              # its own count toward its unit (admissible trades, or stop exits)
    counted_7d: int
    at_stop: bool
    counted: bool                       # its fills make its unit's n (False: virtual exits on another arm's)

    def counts(self, r: TradeRow) -> bool:
        stop_only = self.info is not None and self.info.count == "stop_exits"
        return r.evidence == "counted" and (not stop_only or r.exit_reason == "stop")


def _arm(arm: str, info: ArmInfo | None, rows: list[TradeRow], st: labs.ArmStatus | None,
         week_lo: pd.Timestamp) -> _Arm:
    control = info.is_control if info is not None else any(r.is_control for r in rows)
    a = _Arm(arm_id=arm, info=info, rows=rows, control=control, n=0, counted_7d=0,
             at_stop=st is not None and st.band == "at_stop" and not control, counted=info is None or info.counted)
    a.n = sum(a.counts(r) for r in rows) if info is not None and info.count == "stop_exits" else (st.n if st else 0)
    a.counted_7d = sum(a.counts(r) and r.exit_utc is not None and r.exit_utc >= week_lo for r in rows)
    return a


def _read_figures(rows: Sequence[TradeRow], read: ArmRead, size: int) -> ReadFigures:
    """The frozen read sample (the unit's first `size` counted trades closed by the read) and what came after it;
    the comparison with the control is the evaluator's recorded one, never computed here."""
    counted = sorted((r for r in rows if r.evidence == "counted" and r.r is not None and r.exit_utc is not None),
                     key=lambda r: (r.exit_utc, r.trade_id))
    at = read.read_utc.to_pydatetime()
    sample = [r for r in counted if r.exit_utc is not None and r.exit_utc <= at][:size]
    taken = {r.trade_id for r in sample}
    rs = [r.r for r in sample if r.r is not None]
    since = [r.r for r in counted if r.trade_id not in taken and r.r is not None]
    net, ccy = _money(sample)
    return ReadFigures(
        read_utc=at, verdict=read.verdict, n=len(rs), net_r_per_trade=round(fmean(rs), 3) if rs else None,
        ci90=_ci(rs), hit_rate=round(sum(x > 0 for x in rs) / len(rs), 3) if rs else None, net_pnl=net, currency=ccy,
        vs_control_r=read.vs_control_r, vs_control_ci90=read.vs_control_ci90,
        since_read=SinceRead(n=len(since), net_r_per_trade=round(fmean(since), 3)) if since else None)


def _progress(unit: str, members: Sequence[_Arm], cat: Catalog, size: int | None,
              today: date) -> ReadProgress:
    """A read unit's progress: n over its counted arms, status, annealing state, ETA, and its figures once read."""
    counted = [a for a in members if a.counted]
    n = sum(a.n for a in counted)
    read = cat.reads.get(unit)
    strat = [a for a in counted if not a.control]
    retired = bool(strat) and all(a.at_stop for a in strat)
    status: ArmStatusWord = "read" if read else "retired" if retired else "running" if n else "warming"
    anneal: AnnealState = read.state if read else "R" if retired else "E"
    pace = sum(a.counted_7d for a in counted)
    eta: date | None = None
    if size is not None and status in ("running", "warming"):
        if n >= size:
            eta = today                                         # due now
        elif pace:
            eta = today + timedelta(days=math.ceil((size - n) / (pace / PACE_DAYS)))
    figures = None
    if read is not None and size is not None:
        figures = _read_figures([r for a in counted for r in a.rows if a.counts(r)], read, size)
    return ReadProgress(unit_id=unit, arm_ids=[a.arm_id for a in members], n_counted=n, n_read_at=size,
                        status=status, anneal_state=anneal, eta_read_date=eta, read=figures)


def _variant(a: _Arm, unit: ReadProgress, pooled: bool, today_lo: pd.Timestamp, week_lo: pd.Timestamp) -> BoardVariant:
    info = a.info
    accounts = info.accounts if info is not None and info.accounts else tuple(sorted({r.account for r in a.rows if r.account}))
    timeframe = info.timeframe if info is not None else (a.rows[0].timeframe or "") if a.rows else ""
    return BoardVariant(
        arm_id=a.arm_id, timeframe=timeframe, variant=info.variant if info is not None else timeframe,
        account_labels=[account_label(x) for x in accounts], is_control=a.control,
        control_arm_id=None if a.control or info is None else info.control_arm, read_unit=unit.unit_id, pooled=pooled,
        status=unit.status, anneal_state=unit.anneal_state, n_counted=a.n, n_read_at=unit.n_read_at,
        fills_today=sum(r.opened_utc >= today_lo for r in a.rows), fills_7d=sum(r.opened_utc >= week_lo for r in a.rows),
        open_now=sum(r.status == "open" for r in a.rows), eta_read_date=None if pooled else unit.eta_read_date,
        read=None if pooled else unit.read)


# ------------------------------------------------------------------------------------------------ families
def _family_state(vs: Sequence[BoardVariant]) -> tuple[BoardState, AnnealState]:
    strat = [v for v in vs if not v.is_control]
    if not strat:
        return "control", "E"
    best = min((v.anneal_state for v in strat), key=ANNEAL_RANK.__getitem__)
    if best == "P":
        return "proven", best
    if best == "C1":
        return "candidate", best
    if any(v.status in ("running", "warming") for v in strat):
        return "running", best
    return "retired", best


def _closeness(f: BoardFamily) -> float:
    units = [*((p.n_counted, p.n_read_at) for p in f.pooled if p.read is None),
             *((v.n_counted, v.n_read_at) for v in f.variants if not v.pooled and not v.is_control and v.read is None)]
    return max((n / size for n, size in units if size), default=0.0)


def _families(variants: dict[str, BoardVariant], pooled: dict[str, ReadProgress], family_of: dict[str, str],
              styles: dict[str, str], cat: Catalog, order: dict[str, int]) -> list[BoardFamily]:
    groups: dict[str, list[BoardVariant]] = {}
    for arm, v in variants.items():
        groups.setdefault(family_of[arm], []).append(v)
    out = []
    for fam, vs in groups.items():
        vs.sort(key=lambda v: (_tf_key(v.timeframe), order.get(v.arm_id, len(order)), v.arm_id))
        state, anneal = _family_state(vs)
        style = Counter(styles[v.arm_id] for v in vs if not v.is_control).most_common(1) or \
            Counter(styles[v.arm_id] for v in vs).most_common(1)
        units = sorted({v.read_unit for v in vs if v.pooled})
        out.append(BoardFamily(family=fam, family_label=cat.family_label(fam), style=style[0][0],
                               accounts=sorted({a for v in vs for a in v.account_labels}), state=state,
                               anneal_state=anneal, variants=vs, pooled=[pooled[u] for u in units]))
    out.sort(key=lambda f: (STATE_ORDER.index(f.state), -_closeness(f), f.family_label))
    return out


# ------------------------------------------------------------------------------------------------ totals
def _lab_totals(rows: Iterable[TradeRow], styles: dict[str, str], cat: Catalog,
                week_lo: pd.Timestamp) -> list[LabTotal]:
    cells: dict[tuple[str, str], list[TradeRow]] = {}
    for r in rows:
        if r.kind in _ARM_KINDS and r.account and r.arm_id and (r.exit_utc is None or r.exit_utc >= week_lo):
            style = styles.get(r.arm_id) or labs._TF_STYLE.get(r.timeframe or "", "scalp")
            cells.setdefault((r.account, style), []).append(r)
    out = []
    for (acc, style), rs in sorted(cells.items(), key=lambda kv: (account_label(kv[0][0]), kv[0][1])):
        t = tl.totals(rs)
        out.append(LabTotal(account_label=account_label(acc), style=style, period="7 days",
                            arms=len({cat.unit_of(r.arm_id) for r in rs
                                      if r.arm_id and r.status == "closed" and not r.is_control}),
                            n=t.n, n_closed=t.n_closed, net_pnl=t.net_pnl, costs=t.costs, currency=t.currency))
    return out


def _engine(rows: Iterable[TradeRow], today_lo: pd.Timestamp, week_lo: pd.Timestamp) -> list[EngineStrategy]:
    groups: dict[tuple[str, str, str | None], list[TradeRow]] = {}
    for r in rows:
        if r.kind in _ENGINE_KINDS and r.status == "closed":
            groups.setdefault((r.kind, r.family, r.timeframe), []).append(r)
    out = []
    for (kind, family, tf), rs in groups.items():
        rr = [r.r for r in rs if r.r is not None]
        net, ccy = (None, None) if kind == "shadow" else _money(rs)
        out.append(EngineStrategy(
            name=rs[0].family_label, family=family, timeframe=tf,
            kind="real" if kind == "real" else "shadow" if kind == "shadow" else "strategy",
            n=len(rr), net_r_per_trade=round(fmean(rr), 3) if rr else None, ci90=_ci(rr),
            hit_rate=round(sum(x > 0 for x in rr) / len(rr), 3) if rr else None, net_pnl=net, currency=ccy,
            n_today=sum(r.exit_utc is not None and r.exit_utc >= today_lo for r in rs),
            n_7d=sum(r.exit_utc is not None and r.exit_utc >= week_lo for r in rs)))
    out.sort(key=lambda e: (_ENGINE_KINDS.index(e.kind), e.name, _tf_key(e.timeframe)))
    return out


# ------------------------------------------------------------------------------------------------ the board
def board(ledger: tl.Ledger, cat: Catalog | None = None, state_dir: str | Path | None = None, *, store: Any = None,
          held_out: Collection[str] | None = None) -> StrategyBoardView:
    """The board from one ledger build. `store`/`held_out` as labs.arm_statuses (the API passes its store; tests
    pass `held_out`)."""
    cat = cat or catalog()
    now = ledger.as_of
    today = date.fromisoformat(report_day(now))
    today_lo = day_bounds(today.isoformat())[0]
    week_lo = day_bounds((today - timedelta(days=PACE_DAYS - 1)).isoformat())[0]
    statuses, ok = _statuses(Path(state_dir), now, store, held_out) if state_dir is not None else ({}, True)
    by_arm: dict[str, list[TradeRow]] = {}
    for r in ledger.rows:
        if r.arm_id is not None and r.kind in _ARM_KINDS:
            by_arm.setdefault(r.arm_id, []).append(r)
    ids = [a for a, i in cat.arms.items() if i.running and i.accounts]
    ids += sorted(a for a in set(by_arm) | set(statuses) if a not in set(ids))
    order = {a: i for i, a in enumerate(cat.arms)}
    arms = {a: _arm(a, cat.arm(a), by_arm.get(a, []), statuses.get(a), week_lo) for a in ids}
    members: dict[str, list[_Arm]] = {}
    for a in arms.values():
        members.setdefault(cat.unit_of(a.arm_id), []).append(a)
    units = {u: _progress(u, ms, cat, cat.read_size(ms[0].arm_id), today) for u, ms in members.items()}
    variants, family_of, styles = {}, {}, {}
    for arm, a in arms.items():
        unit = cat.unit_of(arm)
        variants[arm] = _variant(a, units[unit], unit != arm, today_lo, week_lo)
        info = a.info
        family_of[arm] = info.family if info is not None else (a.rows[0].family if a.rows else "unknown")
        styles[arm] = (info.style if info is not None and info.style else
                       labs._TF_STYLE.get(variants[arm].timeframe, "scalp"))
    strat = [p for u, p in units.items() if p.n_read_at is not None and not all(a.control for a in members[u])]
    learning = Learning(
        running=sum(p.status in ("running", "warming") for p in strat), read=sum(p.status == "read" for p in strat),
        proven=sum(p.anneal_state == "P" for p in strat), retired=sum(p.anneal_state == "R" for p in strat),
        counted_7d=sum(a.counted_7d for a in arms.values() if a.counted and not a.control))
    pooled = {u: p for u, p in units.items() if any(a.arm_id != u for a in members[u])}
    return StrategyBoardView(
        as_of=now.to_pydatetime(), learning=learning, lab_totals=_lab_totals(ledger.rows, styles, cat, week_lo),
        families=_families(variants, pooled, family_of, styles, cat, order),
        engine=_engine(ledger.rows, today_lo, week_lo),
        unreadable=[*ledger.unreadable, *([] if ok else ["arm status"])])
