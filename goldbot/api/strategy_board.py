"""The strategy board (docs/design/trades-ux-2026-10-27.md §2.3, §3.4, item 132; ADR 0050): `GET /api/strategies/board`.
Read-only: nothing here writes, sizes or orders.

One row per family (config/exploration_arms.yaml rule, labels from config/strategy_labels.yaml), its arms nested as
variants (timeframes, broker twins). Per arm: n counted toward its single read ("each arm is read once, at its 300th
admissible trade", prereg H-61; another n where config/arm_reads.yaml `read_at` lists it), fills today and in 7 days,
open trades, an ETA at the 7-day pace of counted trades, a status and the control it is read against
(strategy_labels: named, own family, else the sleeve's random control).

Sources: the trade ledger's rows (goldbot/api/trades_ledger.py, one build per LEDGER_TTL_S) and, for n and the H-61
stop, `labs.arm_statuses` (the same count and stop the daily report and the daily review use: void and held-out
trades out of n, every admissible trade in the stop).

Blinding (ADR 0050 with the quant reviewer's binding tightenings), enforced here and never by the client:
* "An unread arm's aggregates are never computed for the client: no per-arm sum, mean, hit rate, rank, share,
  ahead/behind, or comparison with its control." Before its read a variant carries counts, progress, ETA, status and
  annealing state only; the payload has no field that could hold a result.
* "'Near its stop' only once fired": status is warming, running or retired (at its H-61 stop); a running loss is
  "running" whatever its size.
* After the read (the arm listed in config/arm_reads.yaml): the recorded verdict and the read sample's figures,
  frozen (its first n counted trades closed by `read_utc`), with the rest labelled "since read". The difference
  against the control only once the control is read too ("controls stay unread until listed").
* Families are ordered by state, then closeness to the read (n / read size), then name: never by result.
* Lab totals (per account and sleeve, 7 days): money only when every (account, day) cell of the sleeve's unread
  closes covers MIN_ARMS_FOR_TOTAL strategy arms (trades_ledger.withheld_reason), and only when every sleeve of that
  account clears it: otherwise one sleeve would be the List's account total minus the others.
Engine strategies and shadow models are not pre-registered arms: full figures, always with n and a 90% CI.
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Collection, Iterable, Sequence
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
    SinceRead,
    StrategyBoardView,
    TradeRow,
)
from goldbot.api.strategy_labels import ArmInfo, ArmRead, Catalog, account_label, catalog
from goldbot.ops.report_days import day_bounds, report_day
from goldbot.telegram import labs

log = logging.getLogger(__name__)

__all__ = ["BOARD_TTL_S", "PACE_DAYS", "Z90", "board"]

BOARD_TTL_S = 30.0                  # one build serves every poll for 30 s (the Strategies tab polls every 60 s)
PACE_DAYS = 7                       # the ETA's pace: counted trades over the last 7 opportunity days
Z90 = 1.645                         # two-sided 90% normal interval
TF_ORDER = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
STATE_ORDER: tuple[BoardState, ...] = ("proven", "candidate", "running", "retired", "control")
ANNEAL_RANK: dict[AnnealState, int] = {"P": 0, "C1": 1, "E": 2, "R": 3}
SLEEVE_SHORT = ("another sleeve of this lab covers fewer than 3 unread arms in a day (ADR 0050): every sleeve's money "
                "waits, or that sleeve would be the lab's total minus the others")
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


# ------------------------------------------------------------------------------------------------ one arm
def _read_figures(rows: Sequence[TradeRow], read: ArmRead, size: int, control: Sequence[TradeRow] | None
                  ) -> ReadFigures:
    """The frozen read sample (the first `size` counted trades closed by the read) and what came after it."""
    counted = sorted((r for r in rows if r.evidence == "counted" and r.r is not None and r.exit_utc is not None),
                     key=lambda r: (r.exit_utc, r.trade_id))
    at = read.read_utc.to_pydatetime()
    sample = [r for r in counted if r.exit_utc is not None and r.exit_utc <= at][:size]
    taken = {r.trade_id for r in sample}
    after = [r for r in counted if r.trade_id not in taken]
    rs = [r.r for r in sample if r.r is not None]
    net, ccy = _money(sample)
    vs = vs_ci = None
    if control is not None:
        cr = [r.r for r in control if r.evidence == "counted" and r.r is not None and r.exit_utc is not None
              and r.exit_utc <= at]
        if rs and cr:
            vs = round(fmean(rs) - fmean(cr), 3)
            if len(rs) >= 2 and len(cr) >= 2:
                half = Z90 * math.sqrt(stdev(rs) ** 2 / len(rs) + stdev(cr) ** 2 / len(cr))
                vs_ci = (round(vs - half, 3), round(vs + half, 3))
    since = [r.r for r in after if r.r is not None]
    return ReadFigures(
        read_utc=at, verdict=read.verdict, n=len(rs), net_r_per_trade=round(fmean(rs), 3) if rs else None,
        ci90=_ci(rs), hit_rate=round(sum(x > 0 for x in rs) / len(rs), 3) if rs else None, net_pnl=net, currency=ccy,
        vs_control_r=vs, vs_control_ci90=vs_ci,
        since_read=SinceRead(n=len(since), net_r_per_trade=round(fmean(since), 3)) if since else None)


def _variant(arm: str, info: ArmInfo | None, rows: Sequence[TradeRow], st: labs.ArmStatus | None, cat: Catalog,
             by_arm: dict[str, list[TradeRow]], today_lo: pd.Timestamp, week_lo: pd.Timestamp,
             today: date) -> BoardVariant:
    control = info.is_control if info is not None else any(r.is_control for r in rows)
    size = cat.read_size(arm)
    n = st.n if st is not None else 0
    read = cat.reads.get(arm)
    retired = st is not None and st.band == "at_stop" and not control
    status: ArmStatusWord = "read" if read else "retired" if retired else "running" if n else "warming"
    anneal: AnnealState = read.state if read else "R" if retired else "E"
    counted_7d = sum(r.evidence == "counted" and r.exit_utc is not None and r.exit_utc >= week_lo for r in rows)
    eta: date | None = None
    if status in ("running", "warming") and not control:
        if n >= size:
            eta = today                                         # due now
        elif counted_7d:
            eta = today + timedelta(days=math.ceil((size - n) / (counted_7d / PACE_DAYS)))
    pair = info.control_arm if info is not None else None
    figures = None
    if read is not None:
        figures = _read_figures(rows, read, size, by_arm.get(pair, []) if pair and cat.is_read(pair) else None)
    accounts = info.accounts if info is not None and info.accounts else tuple(sorted({r.account for r in rows if r.account}))
    timeframe = info.timeframe if info is not None else (rows[0].timeframe or "") if rows else ""
    return BoardVariant(
        arm_id=arm, timeframe=timeframe, variant=info.variant if info is not None else timeframe,
        account_labels=[account_label(a) for a in accounts], is_control=control, control_arm_id=None if control else pair,
        status=status, anneal_state=anneal, n_counted=n, n_read_at=size,
        fills_today=sum(r.opened_utc >= today_lo for r in rows), fills_7d=sum(r.opened_utc >= week_lo for r in rows),
        open_now=sum(r.status == "open" for r in rows), eta_read_date=eta, read=figures)


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
    return max((v.n_counted / v.n_read_at for v in f.variants if not v.is_control and v.read is None), default=0.0)


def _families(variants: dict[str, BoardVariant], family_of: dict[str, str], styles: dict[str, str],
              cat: Catalog, order: dict[str, int]) -> list[BoardFamily]:
    groups: dict[str, list[BoardVariant]] = {}
    for arm, v in variants.items():
        groups.setdefault(family_of[arm], []).append(v)
    out = []
    for fam, vs in groups.items():
        vs.sort(key=lambda v: (_tf_key(v.timeframe), order.get(v.arm_id, len(order)), v.arm_id))
        state, anneal = _family_state(vs)
        style = Counter(styles[v.arm_id] for v in vs if not v.is_control).most_common(1) or \
            Counter(styles[v.arm_id] for v in vs).most_common(1)
        out.append(BoardFamily(family=fam, family_label=cat.family_label(fam), style=style[0][0],
                               accounts=sorted({a for v in vs for a in v.account_labels}), state=state,
                               anneal_state=anneal, variants=vs))
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
    reasons = {k: tl.withheld_reason(v, narrowed=False, cat=cat)[0] for k, v in cells.items()}
    short = {acc for (acc, _), why in reasons.items() if why is not None}
    out = []
    for (acc, style), rs in sorted(cells.items(), key=lambda kv: (account_label(kv[0][0]), kv[0][1])):
        why = reasons[(acc, style)] or (SLEEVE_SHORT if acc in short else None)
        t = tl.totals(rs, why)
        out.append(LabTotal(account_label=account_label(acc), style=style, period="7 days",
                            arms=len({r.arm_id for r in rs if r.status == "closed" and not r.is_control}), n=t.n,
                            n_closed=t.n_closed, net_pnl=t.net_pnl, costs=t.costs, currency=t.currency,
                            withheld_reason=why))
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
    arms = [a for a, i in cat.arms.items() if i.running and i.accounts]
    arms += sorted(a for a in set(by_arm) | set(statuses) if a not in set(arms))
    order = {a: i for i, a in enumerate(cat.arms)}
    variants, family_of, styles = {}, {}, {}
    for arm in arms:
        info = cat.arm(arm)
        rows = by_arm.get(arm, [])
        variants[arm] = _variant(arm, info, rows, statuses.get(arm), cat, by_arm, today_lo, week_lo, today)
        family_of[arm] = info.family if info is not None else (rows[0].family if rows else "unknown")
        styles[arm] = (info.style if info is not None and info.style else
                       labs._TF_STYLE.get(variants[arm].timeframe, "scalp"))
    strat = [v for v in variants.values() if not v.is_control]
    learning = Learning(
        running=sum(v.status in ("running", "warming") for v in strat), read=sum(v.status == "read" for v in variants.values()),
        proven=sum(v.anneal_state == "P" for v in strat), retired=sum(v.anneal_state == "R" for v in strat),
        counted_7d=sum(r.evidence == "counted" and not r.is_control and r.exit_utc is not None and r.exit_utc >= week_lo
                       for rs in by_arm.values() for r in rs))
    return StrategyBoardView(
        as_of=now.to_pydatetime(), learning=learning, lab_totals=_lab_totals(ledger.rows, styles, cat, week_lo),
        families=_families(variants, family_of, styles, cat, order), engine=_engine(ledger.rows, today_lo, week_lo),
        unreadable=[*ledger.unreadable, *([] if ok else ["arm status"])])
