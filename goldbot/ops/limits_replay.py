"""A deterministic replay of the adaptive limits (ADR 0031) from the record: the closed trades, the shadow book and the
model registry of a state folder. Prints the decision of every risk day. Read only: nothing is written, not even the
core.db tables the nightly job keeps.

ADR 0031: "The rules below were therefore pre-registered (frozen) before any replay was run. A replay validates them;
it never tunes them. A change to any rule is a new ADR." So this prints, it never searches: no parameter of the rules
can be passed in. Every run is a validation that counts: record it (date, range, what it showed) in ADR 0031's replay
log.

What the record cannot give is taken on the strict side and said in the header: the drawdown and stage come from the
closed trades' realised equity path (a lower bound: floating losses are not seen), the 20-day cost/model ratio is not
measured (None, as in the nightly job), and the ATR15 inputs come from `--data-root` 15m bars when given (else None:
the minimum-lot cap stays off).

    python scripts/replay_limits.py --account icm-demo --from 2026-10-20 --to 2026-11-30 [--state state]
        [--models models] [--data-root DIR]
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.ops.limits_job import (
    ATR_P90_DAYS,
    _ns,
    atr15_stats,
    bounds_for,
    build_evidence,
    demo_evidence,
    market_closed_day,
    shadow_evidence,
    strategy_freezes,
)
from goldbot.risk.adaptive import AdaptiveState, Limits, day_start_ns, step
from goldbot.risk.gate import Stage

if TYPE_CHECKING:
    from goldbot.config import Settings
    from goldbot.ops.accounts import Account
    from goldbot.ops.gates_phase import ClosedTrade
    from goldbot.research.model_registry import ModelRegistry

HEADER = ("Validation replay of the frozen ADR 0031 rules (never a tuning step): record this run in ADR 0031's replay "
          "log. Drawdown and stage from realised closed trades (lower bound); cost/model not measured; ATR15 {atr}.")


class ReplayDay(FrozenRecord):
    day: date
    limits: Limits
    reasons: tuple[str, ...]
    demo_trades: int
    shadow_trades: int
    drawdown: float
    problems: tuple[str, ...] = ()


def realised_drawdown(trades: Sequence[ClosedTrade], account_id: str, before_ns: int) -> float:
    """1 - equity / high-water mark on the realised path of the account's closed trades before `before_ns` (each
    trade's equity before plus its P&L). 0 without trades."""
    rows = sorted((t for t in trades if t.account_id == account_id and _ns(t.exit_utc) < before_ns),
                  key=lambda t: _ns(t.exit_utc))
    hwm = eq = 0.0
    for t in rows:
        hwm = max(hwm, t.equity_before)
        eq = t.equity_before + t.pnl
        hwm = max(hwm, eq)
    return max(0.0, 1.0 - eq / hwm) if hwm > 0 else 0.0


def replay(settings: Settings, acc: Account, accounts: Sequence[Account], *, state_dir: Path, models: ModelRegistry,
           start: date, end: date,
           bars_15m: Callable[[pd.Timestamp, pd.Timestamp], pd.DataFrame] | None = None) -> list[ReplayDay]:
    """`step` for every risk day from `start` to `end` inclusive, each on the evidence closed before it, from no prior
    state. Pure in its inputs: the same folder gives the same days."""
    from goldbot.engine.shadow import ShadowBook
    from goldbot.execution.costs import settings_extra_cost_usd, settings_swap
    from goldbot.ops.cost_imputation import CostImputer
    from goldbot.ops.gates_phase import load_closed_trades
    bounds = bounds_for(settings, acc, accounts)
    freezes = strategy_freezes(models)
    trades, err = load_closed_trades(Path(state_dir))
    book = ShadowBook(state_dir)
    imputer = CostImputer.from_state(Path(state_dir), settings)
    r = settings.risk
    bars = None
    if bars_15m is not None:
        lo = pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=ATR_P90_DAYS + 2)
        bars = bars_15m(lo, pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1))
    out: list[ReplayDay] = []
    prev: AdaptiveState | None = None
    day = start
    while day <= end:
        if market_closed_day(day):                     # as the nightly job: no decision for a weekend day
            day += timedelta(days=1)
            continue
        as_of = day_start_ns(day)
        demo, problems = demo_evidence(trades, acc.account_id, imputer, freezes, as_of)
        if err:
            problems.append("closed_trades_unreadable")
        shadow = shadow_evidence(book, freezes, extra_cost_usd=settings_extra_cost_usd(settings),
                                 swap=settings_swap(settings), before_ns=as_of)
        dd = realised_drawdown(trades, acc.account_id, as_of)
        stage = Stage.HALTED if dd >= r.drawdown_stage2 else Stage.SIZE_DOWN if dd >= r.drawdown_stage1 else Stage.NORMAL
        atr = atr15_stats(bars, as_of) if bars is not None else (None, None)
        ev = None if problems else build_evidence(acc.account_id, as_of, demo=demo, shadow=shadow, freezes=freezes,
                                                  drawdown=dd, stage=stage, cost_ratio_20d=None, atr15=atr)
        prev, reasons = step(prev, ev, day, bounds, account_id=acc.account_id)
        out.append(ReplayDay(day=day, limits=prev.limits, reasons=reasons, demo_trades=len(demo),
                             shadow_trades=len(shadow), drawdown=dd, problems=tuple(problems[:5])))
        day += timedelta(days=1)
    return out


def _pct(v: float | None) -> str:
    return "off" if v is None else f"{v * 100:.2f}%"


def render(days: Sequence[ReplayDay], atr: str) -> list[str]:
    lines = [HEADER.format(atr=atr), "",
             "day         daily   weekly  sup-d   sup-w   min-lot  demo shadow    dd  reasons"]
    for d in days:
        L = d.limits
        why = ",".join(r for r in d.reasons if ":" not in r) or "-"
        if d.problems:
            why += " [" + ",".join(d.problems) + "]"
        lines.append(f"{d.day.isoformat()}  {_pct(L.daily_cap):>6}  {_pct(L.weekly_cap):>6}  "
                     f"{_pct(L.supervisor_daily_cap):>6}  {_pct(L.supervisor_weekly_cap):>6}  "
                     f"{_pct(L.min_lot_risk_cap):>7}  {d.demo_trades:>4} {d.shadow_trades:>6}  {d.drawdown:>5.1%}  {why}")
    return lines


def main(argv: list[str] | None = None) -> int:
    from goldbot.config import load_settings
    from goldbot.ops import accounts
    from goldbot.research.model_registry import ModelRegistry
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--account", required=True)
    ap.add_argument("--state", default="state", type=Path)
    ap.add_argument("--models", default="models", type=Path)
    ap.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    ap.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    ap.add_argument("--data-root", type=Path, default=None, help="Parquet store with bars_15m (the ATR15 inputs)")
    a = ap.parse_args(argv)
    settings = load_settings()
    configured = accounts.load_accounts()
    if a.account not in configured:
        ap.error("unknown account (see config/accounts.yaml)")
    bars: Callable[[pd.Timestamp, pd.Timestamp], pd.DataFrame] | None = None
    if a.data_root is not None:
        from goldbot.data.store import Store
        store = Store(a.data_root)

        def bars(s: pd.Timestamp, e: pd.Timestamp) -> pd.DataFrame:
            return store.read("bars_15m", start=s, end=e)
    days = replay(settings, configured[a.account], list(configured.values()), state_dir=a.state,
                  models=ModelRegistry(a.models), start=a.start, end=a.end, bars_15m=bars)
    print("\n".join(render(days, "from " + str(a.data_root) if a.data_root else "not given (minimum-lot cap off)")))
    return 0


__all__: list[Any] = ["ReplayDay", "main", "realised_drawdown", "render", "replay"]
