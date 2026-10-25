"""The nightly adaptive-limits decision (ADR 0031, lane B): builds each demo account's `Evidence`, runs one
`AdaptiveController` evaluation for the next risk day, persists it (core.db `adaptive_state` / `adaptive_changes`) and
tells the owner about every change. The engine reads the stored decision (engine/limits_apply.py
`AdaptiveLimitsController`); this job is the only writer.

Design rules (ADR 0031, quoted):

* "Evidence: only trades AFTER each strategy's freeze/go-live date (never backtest/OOF); retired strategies' trades stay
  in; cut off strictly before the decision day; epoch_ns times; source ids kept." A strategy is a registered model
  version; its freeze is its `promoted_utc` (go-live). Retired and previous versions keep their entries in the
  registry, so their trades stay in. The evidence is the FULL history since each freeze, never a window: Amendment A1
  counts every demo exception trade toward the 60, so a window would reset the exploration count.
* "Loosening evidence = demo fills ONLY (net R incl. real costs). Shadow (lot-fit) trades may only TIGHTEN (CFO)."
  Demo R is the closed record's R less the imputed cost of a gross row (ADR 0017, `CostImputer`); shadow R is the
  shadow trade's R of its initial stop less the measured extra cost (slippage + commission, the nightly cost table)
  and the paid swap. Only shadow trades the version would have taken, of versions that went live.
* "Fail closed: missing/corrupt evidence or unreadable table → floors + dq_event." An engine file that is missing,
  stale or without a usable equity and high-water mark, or a demo trade without a net R, gives no evidence: the
  controller gives the floors and the dq_event (dq_events table, source `adaptive_limits`).
* Accounts: only a demo account with the owner's ceilings (`risk_overrides`, ADR 0032) and `risk.adaptive_limits` on.
  Every other account trades its defaults (StaticController) and is not evaluated.

The decision is for the risk day after the slot (the job runs before the 00:00 UTC roll); the engine adopts it at the
roll and never loosens intraday. The owner gets an FYI on every change (docs/ux/limits-and-tradingview.md: masked
account, units on every number, no buttons, no approval). Health check `limits_controller:<account>` FAILs when the
last decision is older than STALE_DECISION_H.

ADR 0039 (dynamic per-strategy limits): on an account with `dynamic_limits` (accounts.yaml, default off) the same
decision also sets each strategy's risk per trade (`Limits.strategy_risk`, its why in `AdaptiveState.risk_terms`) and
steps the daily allowance down once when the 20-day median ATR15 reaches its 1-year p90 (the ATR regime is already in
the evidence, `atr15_stats`). Bounds: demo [0.25%, the 1% per-trade hard limit], live the default (tighten only).

Known gap (reported, not guessed): no 20-day measured cost/model series exists yet, so `cost_ratio_20d` is None and the
cost rule cannot fire. The engine's own drawdown, stage and revert rules still tighten.
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.data.timeutil import epoch_ns
from goldbot.research.promotion import EVIDENCE_START_UTC
from goldbot.risk.adaptive import (
    AdaptiveChange,
    AdaptiveController,
    AdaptiveState,
    Evidence,
    LimitBounds,
    Limits,
    TradeEvidence,
    day_of_ns,
    day_start_ns,
)
from goldbot.risk.gate import Stage
from goldbot.risk.limits_profile import active_overrides, supervisor_ceilings

if TYPE_CHECKING:
    from goldbot.config import Settings
    from goldbot.engine.shadow import ShadowBook
    from goldbot.labels.triple_barrier import SwapSpec
    from goldbot.ops.accounts import Account
    from goldbot.ops.cost_imputation import CostImputer
    from goldbot.ops.gates_phase import ClosedTrade
    from goldbot.ops.health import Check, HealthContext
    from goldbot.ops.jobs import JobContext
    from goldbot.research.model_registry import ModelRegistry

log = logging.getLogger("goldbot.limits_job")

UNATTRIBUTED = "unattributed"     # a demo fill without a registered version: one strategy, all of its fills in
ENGINE_MAX_AGE_S = 2 * 3600       # an engine file older than this is no evidence of today's drawdown or stage
STALE_DECISION_H = 26.0           # health FAILs past this: one missed nightly run plus two hours
ATR_N = 14
ATR_MEDIAN_DAYS = 20              # "20-day median ATR15"
ATR_P90_DAYS = 365                # "1-year p90"
ATR_MIN_SPAN_DAYS = 358           # a year of 15m bars, a week's slack; less: no p90 (the minimum-lot cap is off)
# the last bar at most this old: the brain's 15m bars come from release data-v1, refreshed on Saturdays (Friday's
# close, synced by the Saturday retrain), so they are up to 8 days old by the next Saturday; older: no ATR (cap off)
ATR_MAX_LAG_DAYS = 9
NOTICE_ROLE = "limits"
DQ_SOURCE = "adaptive_limits"


# ---------------------------------------------------------------------------------------------- bounds
def dynamic_enabled(acc: Account) -> bool:
    """ADR 0039: the account's `dynamic_limits` flag (accounts.yaml; off when absent, so this module works before and
    after the field exists)."""
    return getattr(acc, "dynamic_limits", False) is True


def adaptive_enabled(settings: Settings, acc: Account) -> bool:
    """ADR 0031 runs on a demo account with the owner's ceilings, when `risk.adaptive_limits` is on. Never on a
    probe-only account (ADR 0037 am. 4): it has no strategies, so no evidence, and the controller's fail-closed floors
    (minimum-lot cap off) refused every 0.01-lot probe (incident 2026-10-23); it trades its ceilings (StaticController)
    under its own daily £ limit."""
    return bool(settings.risk.adaptive_limits) and active_overrides(acc) is not None and not acc.probe_only


def bounds_for(settings: Settings, acc: Account, accounts: Iterable[Account]) -> LimitBounds:
    """[floor, ceiling] per field. Floors: the account's defaults (`engine_limits`, settings.yaml `risk:`), so the
    adaptive floor is exactly the ADR 0032 default. Ceilings: the owner's demo ceilings (`risk_overrides`) and, when
    every enabled account is demo with ceilings, the supervisor envelope (`supervisor_ceilings`). Any other account:
    "live/tiny-live ceiling = default". ADR 0039: the per-strategy risk around the account's own risk per trade, up to
    its per-trade hard limit on demo, only when the account's `dynamic_limits` is on."""
    from goldbot.ops.services.engine import engine_limits
    d = engine_limits(settings, acc)
    floor = Limits.defaults(settings.risk).model_copy(update={"daily_cap": d.daily_cap, "weekly_cap": d.weekly_cap,
                                                              "min_lot_risk_cap": d.min_lot_risk_cap})
    ov = active_overrides(acc)
    dyn, base, r_max = dynamic_enabled(acc), d.risk_per_trade, d.max_risk_per_trade
    if ov is None:
        return LimitBounds.build(floor, floor, mode="live", supervisor_all_demo=False, dynamic=dyn, risk_base=base,
                                 risk_max=r_max)
    sup = supervisor_ceilings(list(accounts))
    c = ov.ceilings
    ceiling = floor.model_copy(update={
        "daily_cap": c.daily or floor.daily_cap, "weekly_cap": c.weekly or floor.weekly_cap,
        "supervisor_daily_cap": (sup.daily if sup is not None else None) or floor.supervisor_daily_cap,
        "supervisor_weekly_cap": (sup.weekly if sup is not None else None) or floor.supervisor_weekly_cap,
        "min_lot_risk_cap": c.min_lot_risk_cap})
    return LimitBounds.build(floor, ceiling, mode="demo", supervisor_all_demo=sup is not None, dynamic=dyn,
                             risk_base=base, risk_max=r_max)


# ---------------------------------------------------------------------------------------------- evidence
def _ns(ts: pd.Timestamp) -> int:
    return int(epoch_ns(pd.DatetimeIndex([pd.Timestamp(ts)]))[0])


def strategy_freezes(models: ModelRegistry) -> dict[str, int]:
    """version -> its go-live (promoted_utc, epoch ns), for every version that ever went live (retired ones too).
    `UNATTRIBUTED` is frozen at 0: a demo fill always followed a go-live, it just was not recorded with its version."""
    out = {e.version: _ns(e.promoted_utc) for e in models.entries if e.promoted_utc is not None}
    out[UNATTRIBUTED] = 0
    return out


def demo_trade_id(t: ClosedTrade) -> str:
    ref = t.position_id if t.position_id is not None else t.client_order_id
    return f"demo:{t.account_id}:{ref if ref is not None else _ns(t.exit_utc)}"


def demo_evidence(trades: Iterable[ClosedTrade], account_id: str, imputer: CostImputer, freezes: dict[str, int],
                  before_ns: int, *, evidence_start: pd.Timestamp | None = EVIDENCE_START_UTC,
                  ) -> tuple[list[TradeEvidence], list[str]]:
    """The account's demo fills that closed before `before_ns`, in net R (the record's R less the imputed cost of a
    gross row). A fill whose net R cannot be known is a problem (fail closed), never dropped: dropping could hide a
    loss. ADR 0039: a fill opened before `evidence_start` (H-52, the history-gap fix) is `quarantined`; without an
    entry time, when it closed before it."""
    out: list[TradeEvidence] = []
    problems: list[str] = []
    for t in trades:
        if t.account_id != account_id or t.mode != "demo":
            continue
        exit_ns = _ns(t.exit_utc)
        if exit_ns >= before_ns:
            continue
        charge = imputer.charge(t)
        if t.r is None or charge.r is None or not math.isfinite(t.r):
            problems.append(f"no_net_r:{demo_trade_id(t)}")
            continue
        strategy = t.model_version if t.model_version in freezes else UNATTRIBUTED
        opened = t.entry_utc if t.entry_utc is not None else t.exit_utc
        out.append(TradeEvidence(id=demo_trade_id(t), strategy=strategy, source="demo", exit_ns=exit_ns,
                                 net_r=float(t.r - charge.r), min_lot_exception=bool(t.min_lot_exception),
                                 quarantined=_quarantined(opened, evidence_start)))
    return out, problems


def _quarantined(opened: pd.Timestamp, evidence_start: pd.Timestamp | None) -> bool:
    from goldbot.research.promotion import quarantine_reason
    return quarantine_reason(opened, evidence_start) is not None


def shadow_evidence(book: ShadowBook, freezes: dict[str, int], *, extra_cost_usd: float, swap: SwapSpec,
                    before_ns: int, evidence_start: pd.Timestamp | None = EVIDENCE_START_UTC) -> list[TradeEvidence]:
    """Closed shadow trades the version would have taken (`taken`), of versions that went live, closed before
    `before_ns`, in R of the initial stop less the measured extra cost (slippage both sides + commission, USD per oz)
    and the paid swap. Shadow trades only tighten (the controller's rule), so they need no lot fit to be safe. ADR
    0039: one opened before `evidence_start` (H-52) is `quarantined`."""
    from goldbot.ops.cost_imputation import paid_swap_per_oz
    out: list[TradeEvidence] = []
    for version, vb in sorted(book.books.items()):
        if version not in freezes or version == UNATTRIBUTED:
            continue
        for t in vb.closed:
            if not t.taken or t.exit_ts is None or t.ret is None or not math.isfinite(t.ret):
                continue
            risk = abs(t.entry - t.stop)
            exit_ns = _ns(t.exit_ts)
            if not risk > 0 or exit_ns >= before_ns:
                continue
            cost = max(0.0, extra_cost_usd) + paid_swap_per_oz(swap, t.side, t.entry_ts, t.exit_ts)
            out.append(TradeEvidence(id=f"shadow:{version}:{_ns(t.entry_ts)}", strategy=version, source="shadow",
                                     exit_ns=exit_ns, net_r=float(t.ret * t.entry / risk - cost / risk),
                                     quarantined=_quarantined(t.entry_ts, evidence_start)))
    return out


def _atr15(bars: pd.DataFrame, before_ns: int) -> tuple[np.ndarray, np.ndarray] | None:
    """(bar times in epoch ns, ATR(14)) of the 15m bars closed before `before_ns`; None without a year of bars or
    with the last bar more than ATR_MAX_LAG_DAYS old."""
    from goldbot.features.technical import atr
    if bars.empty or not {"ts_utc", "high", "low", "close"} <= set(bars.columns):
        return None
    b = bars.drop_duplicates("ts_utc", keep="last").sort_values("ts_utc")
    ts = epoch_ns(pd.DatetimeIndex(b["ts_utc"]))
    bar_ns = 15 * 60 * 10**9
    keep = ts + bar_ns <= before_ns                       # closed before the decision day
    b, ts = b[keep], ts[keep]
    day = 86_400 * 10**9
    if len(ts) < ATR_N + 1 or ts[0] > before_ns - ATR_MIN_SPAN_DAYS * day or ts[-1] < before_ns - ATR_MAX_LAG_DAYS * day:
        return None
    return ts, atr(b.reset_index(drop=True), ATR_N).to_numpy()


def atr15_stats(bars: pd.DataFrame, before_ns: int) -> tuple[float | None, float | None]:
    """(20-day median, 1-year 90th percentile) of ATR(14) on 15m bars closed before `before_ns`. None for both
    without a year of bars or with the last bar more than ATR_MAX_LAG_DAYS old: the minimum-lot cap is then off
    (ADR 0031 reading 11, fail closed)."""
    got = _atr15(bars, before_ns)
    if got is None:
        return None, None
    ts, a = got
    day = 86_400 * 10**9
    year = (ts >= before_ns - ATR_P90_DAYS * day) & np.isfinite(a)
    recent = (ts >= before_ns - ATR_MEDIAN_DAYS * day) & np.isfinite(a)
    if not recent.any() or not year.any():
        return None, None
    return float(np.median(a[recent])), float(np.quantile(a[year], 0.9))


def atr15_daily_stats(bars: pd.DataFrame, before_ns: int) -> tuple[float | None, float | None]:
    """ADR 0039's volatility regime, like with like (quant review 2026-10-19): each UTC day's median ATR15, then the
    median of the last ATR_MEDIAN_DAYS of them and the 90th percentile of the last year's. A per-bar p90 is set by
    the busiest session hour, which a 20-day median never reaches. None for both on the same data rules as
    `atr15_stats` (unknown: no regime)."""
    got = _atr15(bars, before_ns)
    if got is None:
        return None, None
    ts, a = got
    day = 86_400 * 10**9
    ok = np.isfinite(a)
    daily = pd.Series(a[ok]).groupby(ts[ok] // day).median()
    days = daily.index.to_numpy() * day
    year = daily[days >= before_ns - ATR_P90_DAYS * day]
    recent = daily[days >= before_ns - ATR_MEDIAN_DAYS * day]
    if recent.empty or year.empty:
        return None, None
    return float(recent.median()), float(year.quantile(0.9))


class EngineFacts(FrozenRecord):
    """What the engine file says about the account now: the drawdown from the high-water mark and the stage."""

    drawdown: float
    stage: Stage
    equity: float
    currency: str | None = None


def engine_facts(state_dir: Path, account_id: str, now_ns: int) -> EngineFacts | str:
    """The engine file's drawdown and stage, or why they cannot be used (missing, unreadable, stale, no equity or
    mark, unknown stage)."""
    try:
        e = json.loads((Path(state_dir) / f"engine_{account_id}.json").read_text())
    except (OSError, ValueError) as exc:
        return f"engine_file_unreadable:{type(exc).__name__}"
    if not isinstance(e, dict):
        return "engine_file_unreadable:not_an_object"
    ts, eq, hwm = e.get("ts"), e.get("equity"), e.get("balance_closed_hwm")
    nums = (ts, eq, hwm)
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in nums):
        return "engine_file_numbers"
    assert isinstance(ts, (int, float)) and isinstance(eq, (int, float)) and isinstance(hwm, (int, float))
    if abs(now_ns / 1e9 - float(ts)) > ENGINE_MAX_AGE_S:
        return "engine_file_stale"
    if not (eq > 0 and hwm > 0):
        return "engine_file_no_equity"
    try:
        stage = Stage(str(e.get("stage", "")))
    except ValueError:
        return "engine_file_stage"
    cur = e.get("currency")
    return EngineFacts(drawdown=max(0.0, 1.0 - float(eq) / float(hwm)), stage=stage, equity=float(eq),
                       currency=str(cur) if isinstance(cur, str) and cur else None)


def build_evidence(account_id: str, as_of_ns: int, *, demo: Sequence[TradeEvidence], shadow: Sequence[TradeEvidence],
                   freezes: dict[str, int], drawdown: float, stage: Stage, cost_ratio_20d: float | None,
                   atr15: tuple[float | None, float | None],
                   vol: tuple[float | None, float | None] = (None, None)) -> Evidence:
    """One Evidence for the decision at `as_of_ns` (the start of the decision day): every trade since its strategy's
    freeze, closed before it."""
    used = {t.strategy for t in (*demo, *shadow)}
    return Evidence(account_id=account_id, as_of_ns=as_of_ns, trades=(*demo, *shadow),
                    freeze_ns={s: n for s, n in freezes.items() if s in used}, drawdown=drawdown, stage=stage,
                    cost_ratio_20d=cost_ratio_20d, atr15_median_20d=atr15[0], atr15_p90_1y=atr15[1],
                    vol_median_20d=vol[0], vol_p90_1y=vol[1])


# ---------------------------------------------------------------------------------------------- the owner's FYI
_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}
_LABEL = {"daily_cap": "daily loss limit", "weekly_cap": "week loss limit",
          "supervisor_daily_cap": "combined daily limit", "supervisor_weekly_cap": "combined week limit",
          "min_lot_risk_cap": "smallest-trade cap"}
_WHY = {"loosen": "3 qualifying days on demo trades", "dd_floor": "account 4% or more below its peak",
        "loss_streak_floor": "4 losing trades in a row", "floors_until": "after a losing streak (floors through today)",
        "cost_floor": "costs above the model", "cost_step": "costs above the model",
        "stage_floor": "size-down or halt stage", "target_below": "recent results weaker",
        "atr_minlot_off": "gold unusually volatile (ATR15 at its 1-year high)",
        "minlot_explore": "minimum-lot trial (first 60 trades)",
        "minlot_explore_stop": "minimum-lot trial lost 10R: off for good",
        "evidence_invalid": "evidence missing or unreadable: back to the defaults",
        "bounds": "limits outside their bounds",
        "vol_step": "gold unusually volatile (ATR15 at its 1-year high): one step down",
        "tighten": "a strategy's measured edge is weaker", "floors": "account-wide floors"}
_RISK_FIELD = "strategy_risk:"


def _pct(v: float | None) -> str:
    return "off" if v is None else f"{v * 100:.4g}%"


def _risk_pct(v: float | None) -> str:
    return "the default" if v is None else _pct(v)


def _money(frac: float, equity: float | None, currency: str | None) -> str | None:
    if equity is None or not math.isfinite(equity) or equity <= 0:
        return None
    sym = _SYMBOL.get((currency or "").upper(), f"{currency} " if currency else "")
    return f"{sym}{frac * equity:,.0f}"


def change_notice(account_id: str, changes: Sequence[AdaptiveChange], *, equity: float | None, currency: str | None,
                  limits: Limits, at: pd.Timestamp, tz: str) -> str:
    """The FYI (docs/ux/limits-and-tradingview.md, "Limit changed"): at most 2 lines, units on every number, money and
    % together, local time with its zone, the account masked, no ids, no buttons."""
    from goldbot.telegram.entry_text import account_name, clock
    parts: list[str] = []
    risk = [c for c in changes if c.field.startswith(_RISK_FIELD)]
    if risk:                                 # ADR 0039: one part for every strategy, no version ids (UX: no ids)
        moves = ", ".join(f"{_risk_pct(c.old)} -> {_risk_pct(c.new)}" for c in risk)
        parts.append(f"per-trade risk {moves} ({len(risk)} {'strategy' if len(risk) == 1 else 'strategies'})")
    for c in changes:
        if c.field.startswith(_RISK_FIELD):
            continue
        if c.field in ("daily_cap", "weekly_cap") and c.old is not None and c.new is not None:
            a, b = _money(c.old, equity, currency), _money(c.new, equity, currency)
            base = _money(1.0, equity, currency)
            money = f"{a} -> {b} ({_pct(c.old)} -> {_pct(c.new)} of {base})" if a and b and base else \
                f"{_pct(c.old)} -> {_pct(c.new)}"
            parts.append(f"{_LABEL[c.field]} {money}")
        else:
            parts.append(f"{_LABEL.get(c.field, c.field)} {_pct(c.old)} -> {_pct(c.new)}")
    codes = list(dict.fromkeys(r for c in changes for r in c.reasons if r in _WHY))
    why = "; ".join(dict.fromkeys(_WHY[r] for r in codes)) or "rule update"
    if not any(c.field == "weekly_cap" for c in changes):
        w = _money(limits.weekly_cap, equity, currency)
        why += f". Week limit unchanged at {w + ' (' + _pct(limits.weekly_cap) + ')' if w else _pct(limits.weekly_cap)}"
    local, utc = clock(at, at, tz)
    return (f"Limit change (demo, {account_name(account_id)}): {', '.join(parts)}.\n"
            f"Why: {why}. {local}{f' ({utc})' if utc else ''}")


def queue_notice(state_dir: Path, text: str) -> None:
    """One "notice" row in state/agent_runs.jsonl, which the Telegram service relays (as `ApprovalBus.notify_owner`
    and the TradingView pull do). Best effort: a failed write is logged; the decision stands either way."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row = {"role": NOTICE_ROLE, "started_utc": now, "finished_utc": now, "status": "notice", "turns": 0,
           "cost_usd": 0.0, "report_path": None, "detail": text}
    try:
        with open(Path(state_dir) / "agent_runs.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as exc:
        log.warning("could not queue the limits notice: %s", exc)


# ---------------------------------------------------------------------------------------------- the decision
DqSink = Callable[[str, str], None]


def decide_account(settings: Settings, acc: Account, accounts: Sequence[Account], *, state_dir: Path,
                   models: ModelRegistry, decision_day: date, now_ns: int, dq: DqSink,
                   bars_15m: Callable[[pd.Timestamp, pd.Timestamp], pd.DataFrame] | None = None,
                   extra_cost_usd: float | None = None, swap: SwapSpec | None = None) -> dict[str, Any]:
    """One account's evaluation for `decision_day`: evidence, `AdaptiveController.current`, the change rows, the FYI.
    Returns the job's result row (the limits, the reasons in force, the problems that floored it)."""
    from goldbot.engine.shadow import ShadowBook
    from goldbot.execution.costs import settings_extra_cost_usd, settings_swap
    from goldbot.ops.cost_imputation import CostImputer
    from goldbot.ops.gates_phase import load_closed_trades
    state_dir = Path(state_dir)
    as_of = day_start_ns(decision_day)
    bounds = bounds_for(settings, acc, accounts)
    problems: list[str] = []
    freezes = strategy_freezes(models)
    trades, err = load_closed_trades(state_dir)
    if err:
        problems.append("closed_trades_unreadable")
    demo, bad = demo_evidence(trades, acc.account_id, CostImputer.from_state(state_dir, settings), freezes, as_of)
    problems += bad
    try:
        shadow = shadow_evidence(ShadowBook(state_dir), freezes, before_ns=as_of,
                                 extra_cost_usd=extra_cost_usd if extra_cost_usd is not None
                                 else settings_extra_cost_usd(settings), swap=swap or settings_swap(settings))
    except (OSError, ValueError) as exc:
        shadow = []
        problems.append(f"shadow_book_unreadable:{type(exc).__name__}")
    facts = engine_facts(state_dir, acc.account_id, now_ns)
    if isinstance(facts, str):
        problems.append(facts)
    atr15: tuple[float | None, float | None] = (None, None)
    vol: tuple[float | None, float | None] = (None, None)
    if bars_15m is not None:
        end = pd.Timestamp(as_of, unit="ns", tz="UTC")
        try:
            bars = bars_15m(end - pd.Timedelta(days=ATR_P90_DAYS + 2), end)
            atr15, vol = atr15_stats(bars, as_of), atr15_daily_stats(bars, as_of)
        except Exception as exc:             # the store unreadable: the minimum-lot cap is off (fail closed)
            log.warning("limits_job: 15m bars unreadable (%s): ATR unknown", type(exc).__name__)
    ev: Evidence | None = None
    if not problems and not isinstance(facts, str):
        ev = build_evidence(acc.account_id, as_of, demo=demo, shadow=shadow, freezes=freezes,
                            drawdown=facts.drawdown, stage=facts.stage, cost_ratio_20d=None, atr15=atr15, vol=vol)
    if problems:
        dq("adaptive_limits_evidence_missing", f"{acc.account_id}: {', '.join(problems[:10])}")
    ctl = AdaptiveController(state_dir, bounds, dq=dq, clock=lambda: now_ns)
    before = _changes(ctl, acc.account_id)
    # no Evidence: the floors for the decision day and the controller's own dq_event, never a guess
    limits = ctl.current(acc.account_id, ev, day=decision_day)
    new = _changes(ctl, acc.account_id)[len(before):]
    if new:
        eq = None if isinstance(facts, str) else facts.equity
        ccy = None if isinstance(facts, str) else facts.currency
        queue_notice(state_dir, change_notice(acc.account_id, new, equity=eq, currency=ccy, limits=limits,
                                              at=pd.Timestamp(now_ns, unit="ns", tz="UTC"),
                                              tz=settings.telegram.owner_tz))
    out: dict[str, Any] = {"day": decision_day.isoformat(), "limits": limits.model_dump(), "changes": len(new),
                           "demo_trades": len(demo), "shadow_trades": len(shadow), "problems": problems[:10]}
    if bounds.dynamic:                       # ADR 0039: the why per strategy and the ATR regime
        st = _state(ctl, acc.account_id)
        out["risk_terms"] = {k: t.model_dump() for k, t in st.risk_terms.items()} if st is not None else {}
        out["vol_high"] = st.vol_high if st is not None else None
    return out


def _state(ctl: AdaptiveController, account_id: str) -> AdaptiveState | None:
    try:
        return ctl.store.load(account_id)
    except Exception:                        # unreadable: the controller reported it and gave the floors
        return None


def _changes(ctl: AdaptiveController, account_id: str) -> list[AdaptiveChange]:
    try:
        return ctl.store.changes(account_id)
    except Exception:                        # unreadable: the controller reports it and gives the floors
        return []


def decision_day_of(slot: pd.Timestamp) -> date:
    """The risk day a slot decides: the one after the slot's own (the job runs before the 00:00 UTC roll)."""
    return day_of_ns(_ns(slot)) + timedelta(days=1)


def market_closed_day(day: date) -> bool:
    """A UTC Saturday or Sunday: gold is closed (Friday 21:00/22:00 UTC to Sunday 22:00/23:00 UTC), nothing trades on
    the day decided and the engine file of the night before can be stale. No decision is made for it, so a weekend
    neither floors the limits nor resets the 3-day loosening count (a risk-day count of trading days). The engine
    trades the floors on such a day (no decision for it), when nothing can be entered anyway."""
    return day.weekday() >= 5


def limits_job(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Nightly, before the risk-day roll: one decision per adaptive account (ADR 0031). A failing account never stops
    the others; a dq_event goes to the dq_events table."""
    from goldbot.data.quality import DQEvent, events_frame
    from goldbot.ops.jobs import _bars, live_extra_cost_usd, live_swap
    now = (ctx.clock or (lambda: pd.Timestamp.now("UTC")))()
    events: list[DQEvent] = []

    def dq(code: str, detail: str) -> None:
        log.warning("dq_event %s: %s", code, detail)
        events.append(DQEvent(ts_utc=pd.Timestamp(slot).tz_convert("UTC"), check=code, severity="warning",
                              detail=detail))

    out: dict[str, Any] = {}
    day = decision_day_of(slot)
    if market_closed_day(day):
        return {"skipped": f"{day.isoformat()} is a weekend day: no decision (the 3-day counts and cooldown keep)"}
    for acc in ctx.accounts:
        if not adaptive_enabled(ctx.settings, acc):
            continue
        try:
            out[acc.account_id] = decide_account(
                ctx.settings, acc, ctx.accounts, state_dir=ctx.state_dir, models=ctx.models, decision_day=day,
                now_ns=_ns(now), dq=dq, bars_15m=lambda s, e: _bars(ctx, "15m", s, e),
                extra_cost_usd=live_extra_cost_usd(ctx), swap=live_swap(ctx))
        except Exception as exc:             # this account keeps yesterday's decision, which the engine floors
            log.exception("limits_job failed for %s", acc.account_id)
            out[acc.account_id] = {"error": type(exc).__name__}
    if events:
        try:
            ctx.store.append("dq_events", events_frame(events), source=DQ_SOURCE, dedupe=False)
        except Exception as exc:
            log.warning("limits_job: dq_events not written (%s)", type(exc).__name__)
    return out


# ---------------------------------------------------------------------------------------------- health
def check_limits_controller(ctx: HealthContext, account_id: str) -> list[Check]:
    """limits_controller:<account>: FAIL when an adaptive account's last decision is older than STALE_DECISION_H, or
    there is none, or core.db cannot be read (the engine then trades the floors, the safe side, but the nightly
    `limits_job` is not doing its work). Nothing for an account that is not adaptive. Reporting only."""
    from goldbot.ops.health import Check
    from goldbot.risk.adaptive import AdaptiveStore
    ref = next((a for a in ctx.accounts if a.account_id == account_id), None)
    if ref is None or not ref.adaptive:
        return []
    name = f"limits_controller:{account_id}"
    floors = "the engine trades the default limits (floors) until the nightly limits_job decides"
    if not (Path(ctx.state_dir) / "core.db").exists():
        return [Check(name=name, status="warn", reason=f"no adaptive limits decision yet: {floors}")]   # safe: floors
    try:
        st = AdaptiveStore(ctx.state_dir).load(account_id)
    except Exception as exc:
        return [Check(name=name, status="fail",
                      reason=f"adaptive limits state unreadable in core.db ({type(exc).__name__}): {floors}")]
    if st is None or st.decided_ns is None:
        return [Check(name=name, status="warn", reason=f"no adaptive limits decision yet: {floors}")]   # safe: floors
    age_h = (_ns(ctx.now) - st.decided_ns) / 3.6e12
    # no decision is made for a weekend day (Friday's and Saturday's nights): two nights more on Saturday and Sunday
    limit = STALE_DECISION_H + (48.0 if ctx.now.tz_convert("UTC").weekday() >= 5 else 0.0)
    if age_h > limit:
        return [Check(name=name, status="fail",
                      reason=f"last adaptive limits decision {age_h:.0f} h ago (limit {limit:.0f} h): {floors}")]
    L = st.limits
    return [Check(name=name, status="ok",
                  reason=f"decided {age_h:.1f} h ago for {st.day.isoformat()}: daily {_pct(L.daily_cap)}, weekly "
                         f"{_pct(L.weekly_cap)}, minimum-lot cap {_pct(L.min_lot_risk_cap)}")]


__all__ = ["UNATTRIBUTED", "EngineFacts", "adaptive_enabled", "dynamic_enabled", "atr15_daily_stats", "atr15_stats", "bounds_for", "build_evidence",
           "change_notice", "check_limits_controller", "decide_account", "decision_day_of", "demo_evidence",
           "market_closed_day",
           "engine_facts", "limits_job", "shadow_evidence", "strategy_freezes"]
