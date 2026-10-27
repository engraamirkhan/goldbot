"""Today per account from the trade ledger (docs/design/trades-ux-2026-10-27.md §2.4, §3.5, item 131): fills against
the per-account plan and the pace, today's money by kind after costs, caps used, the exploration sleeves used, and the
daily net strip with week and month to date. One builder: `GET /api/today` (home_cards.today_money) and, with item
134, Telegram's /today read the same figures, from the same rows as `GET /api/trades`. Reporting only.

Design rules kept: demo and live never summed, currencies never summed (each account its own card); figures from
net-of-cost ledger rows; a day is an opportunity day ([D-1 22:00, D 22:00) UTC). Blinding (ADR 0050, quant
tightenings): a money figure is withheld (None, named in `withheld`) when its rows' unread-arm closes cover fewer than
`MIN_ARMS_FOR_TOTAL` strategy arms in any account-day cell (trades_ledger.withheld_reason). The arms' own buckets
(exploration, control) never show money before their read: control money beside exploration money is the banned
comparison with the random baseline, and either bucket beside the account's net is the other by subtraction; the
arms together are the account's net less the other kinds (a lab total). "Quiet day" wording only when the volatility
screen says so: until an engine publishes `opportunity_day`, `quiet_day` is None.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import pandas as pd

from goldbot.api.schema import CapUse, CostSplit, KindDay, PeriodNet, StripDay, TradeRow
from goldbot.api.strategy_labels import Catalog
from goldbot.api.trades_ledger import oday, withheld_reason
from goldbot.base import FrozenRecord
from goldbot.config import Settings
from goldbot.ops.report_days import day_bounds, report_day

log = logging.getLogger(__name__)

__all__ = ["PACE_WINDOW", "AccountPlan", "account_day", "plans_from_config"]

PACE_WINDOW = pd.Timedelta(hours=2)     # spec §2.4: "pace = projected fills by day end from the last 2 h"
_ARMS = ("exploration", "control")


class AccountPlan(FrozenRecord):
    """An account's plan figures: settings `trades_view` and its exploration sleeves (config/accounts.yaml)."""
    fills_target: int | None = None
    daily_target: float | None = None   # fixed, account currency; else daily_share x the day's starting equity
    daily_share: float = 0.05
    pot: float | None = None            # the exploration sleeves together, account currency
    strip_days: int = 14


def plans_from_config(settings: Settings | None) -> dict[str, AccountPlan]:
    """Every configured account's plan; {} when accounts.yaml cannot be read (the card then shows no targets)."""
    from goldbot.ops.accounts import load_accounts
    tv = settings.trades_view if settings is not None else None
    try:
        accounts = load_accounts(logins=False)
    except Exception as exc:              # noqa: BLE001 - reporting only
        log.warning("today: accounts.yaml unreadable (%s): no plan figures", type(exc).__name__)
        return {}
    out = {}
    for aid, a in accounts.items():
        ex = a.exploration
        target = None if a.mode == "live" or tv is None else tv.fills_target_by_account.get(aid, tv.fills_target)
        out[aid] = AccountPlan(fills_target=target, daily_target=tv.daily_target_by_account.get(aid) if tv else None,
                               daily_share=tv.daily_target_share if tv else 0.05,
                               pot=round(sum(p.gbp for p in ex.pots.values()), 2) if ex.enabled and ex.pots else None,
                               strip_days=tv.strip_days if tv else 14)
    return out


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _sum(vals: Sequence[float | None]) -> float:
    return round(sum(v for v in vals if v is not None), 2)


def _net(rows: Sequence[TradeRow]) -> tuple[float | None, float | None, int]:
    """(net, partial, unknown): the closed rows' net after costs, or None with the known rows' sum as `partial` and the
    count of trades whose net is unknown (finance audit 2026-10-27: a total missing a trade is never shown as net)."""
    unknown = sum(1 for r in rows if r.pnl is None)
    total = _sum([r.pnl for r in rows])
    return (None, total, unknown) if unknown else (total, None, 0)


def _split(rows: Sequence[TradeRow]) -> CostSplit:
    parts = [r.costs for r in rows if r.costs is not None]

    def part(name: str) -> float | None:
        vals = [getattr(c, name) for c in parts]
        return _sum(vals) if vals else 0.0
    return CostSplit(spread=part("spread"), slippage=part("slippage"), commission=part("commission"),
                     swap=part("swap"), total=_sum([c.total for c in parts]), complete=all(c.complete for c in parts),
                     spread_exit_estimated=any(c.spread_exit_estimated for c in parts),
                     slippage_exit_known=bool(parts) and all(c.slippage_exit_known for c in parts))


def _blind(rows: Sequence[TradeRow], cat: Catalog) -> bool:
    return withheld_reason(rows, narrowed=False, cat=cat)[0] is not None


def _closed_on(rows: Sequence[TradeRow], days: set[date]) -> list[TradeRow]:
    return [r for r in rows if r.status == "closed" and r.exit_utc is not None and oday(r.exit_utc) in days]


def _trading_days(today: date, n: int) -> list[date]:
    out: list[date] = []
    d = today
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _cap(used: float | None, limit: float | None) -> CapUse | None:
    if used is None or limit is None or limit <= 0:
        return None
    return CapUse(used=round(max(0.0, used), 2), limit=round(limit, 2), share=round(max(0.0, used) / limit, 4))


def _pot_used(mine: Sequence[TradeRow], today: list[TradeRow]) -> float:
    """Realised arm losses today (each arm net of its profits, at least 0) plus the open arms' loss at their stops: the
    sleeves' own measure (exploration.realised + the pot check), from the ledger."""
    by_arm: dict[str, float] = {}
    for r in today:
        if r.kind in _ARMS and r.arm_id is not None:
            # a close without its net counts at its loss at the stop plus its costs, as the engine's sleeve does
            loss = r.pnl if r.pnl is not None else -((r.risk_amount or 0.0) + (r.costs.total if r.costs else 0.0))
            by_arm[r.arm_id] = by_arm.get(r.arm_id, 0.0) + loss
    held = sum(r.risk_amount or 0.0 for r in mine if r.kind in _ARMS and r.status == "open")
    return round(sum(max(0.0, -v) for v in by_arm.values()) + held, 2)


def account_day(name: str, e: dict[str, Any], rows: Sequence[TradeRow], now: pd.Timestamp, plan: AccountPlan | None,
                cat: Catalog) -> dict[str, Any]:
    """TodayAccount's ledger fields for the account named `name` (config name, digits cut) with engine file `e`."""
    plan = plan or AccountPlan()
    mine = [r for r in rows if r.account == name and r.kind != "shadow"]
    today = date.fromisoformat(report_day(now))
    lo, hi = day_bounds(today.isoformat())
    filled = [r for r in mine if lo <= r.opened_utc < hi]
    closed = _closed_on(mine, {today})
    withheld: list[str] = []
    recent = sum(1 for r in filled if r.opened_utc >= now - PACE_WINDOW)
    window_h = min(PACE_WINDOW, now - lo).total_seconds() / 3600
    pace = round(len(filled) + recent / window_h * max(0.0, (hi - now).total_seconds() / 3600)) if window_h > 0 else None
    arms_blind = any(r.kind in _ARMS and r.arm_id is not None and not cat.is_read(r.arm_id) for r in closed)
    by_kind: dict[str, KindDay] = {}
    for kind in sorted({r.kind for r in [*filled, *closed]}):
        k_closed = [r for r in closed if r.kind == kind]
        hide = kind in _ARMS and arms_blind
        rs = [r.r for r in k_closed if r.r is not None]
        k_net, _, k_unknown = _net(k_closed)
        by_kind[kind] = KindDay(fills=sum(r.kind == kind for r in filled), closed=len(k_closed),
                                net_pnl=None if hide else k_net, net_unknown=k_unknown,
                                costs=None if hide else _sum([r.costs.total for r in k_closed if r.costs]),
                                net_r=None if hide else (round(sum(rs), 3) if rs else None), withheld=hide)
    day_hidden = _blind(closed, cat)
    if day_hidden:
        withheld += ["net_today", "costs_today", "exploration_pot_used"]
    eq, d0, w0 = _num(e.get("equity")), _num(e.get("day_start_equity")), _num(e.get("week_start_equity"))
    limit, remaining = _num(e.get("daily_loss_limit")), _num(e.get("daily_loss_remaining"))
    raw = e.get("limits")
    limits: dict[str, Any] = raw if isinstance(raw, dict) else {}
    weekly = _num(limits.get("weekly_cap"))
    daily_target = plan.daily_target if plan.daily_target is not None else (
        round(plan.daily_share * d0, 2) if d0 else None)
    strip = []
    for d in _trading_days(today, plan.strip_days):
        on = _closed_on(mine, {d})
        hidden = _blind(on, cat)
        d_net, _, d_unknown = _net(on)
        strip.append(StripDay(day=d, n=len(on), net_pnl=None if hidden else d_net, net_unknown=d_unknown,
                            costs=None if hidden else _sum([r.costs.total for r in on if r.costs]),
                            target=daily_target, opportunity=None, withheld=hidden))
    periods = {}
    for key, start in (("week_to_date", today - timedelta(days=today.weekday())), ("month_to_date", today.replace(day=1))):
        on = [r for r in mine if r.status == "closed" and r.exit_utc is not None and start <= oday(r.exit_utc) <= today]
        hidden = _blind(on, cat)
        if hidden:
            withheld.append(key)
        p_net, p_part, p_unknown = _net(on)
        periods[key] = PeriodNet(since=start, n=len(on), net_pnl=None if hidden else p_net, net_unknown=p_unknown,
                                 net_partial=None if hidden else p_part,
                                 costs=None if hidden else _split(on), withheld=hidden)
    today_net, today_partial, today_unknown = _net(closed)
    opp = e.get("opportunity_day")
    return dict(
        fills_today=len(filled), fills_target=plan.fills_target, pace=pace, by_kind=by_kind,
        net_today=None if day_hidden else today_net, net_unknown=today_unknown,
        net_today_partial=None if day_hidden else today_partial,
        costs_today=None if day_hidden else _split(closed),
        open_n=int(v) if (v := _num(e.get("open_positions"))) is not None else None, open_risk=_num(e.get("open_risk")),
        day_cap_used=_cap(None if limit is None or remaining is None else limit - remaining, limit),
        week_cap_used=_cap(None if w0 is None or eq is None else w0 - eq, None if weekly is None or not w0 else weekly * w0),
        exploration_pot_used=None if day_hidden else _cap(_pot_used(mine, closed), plan.pot),
        daily_target=daily_target, strip=strip, **periods,
        quiet_day=(not opp) if isinstance(opp, bool) else None, withheld=withheld)
