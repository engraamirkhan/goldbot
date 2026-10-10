"""The live account view (owner request; ADR 0004 sections 3 and 4): today's daily loss budget and the milestone
ladder, computed from what the engine publishes in state/engine_<account>.json and the supervisor in
state/supervisor.json. Nothing here reads config: balances, currency, limits, price, ATR and lot terms are the engine's
live values, so the view shows what the RiskGate is acting on. Reporting only: nothing here sizes or gates an order.

Daily loss budget. The effective daily loss limit is min(account cap x day-start equity, combined cap x combined
day-start equity, absolute limit) in account currency (ADR 0004 section 3). Each cap is listed with its own use so
far; the binding one is the cap with the least left. The combined cap is only added when every engine reports the
same currency (summing pounds and dollars would be wrong: ADR 0004 audit A8). The absolute limit is listed only once
the engine publishes it (`limits.daily_loss_limit_abs`, BACKLOG 29): until the RiskGate enforces it, showing it as a
limit would claim a protection that is not there.

Milestone ladder. A timeframe is tradeable when its minimum lot fits the RiskGate's sizing rule at the current equity
(goldbot/risk/sizing.py): the stop is 1.5 x ATR(14) of that timeframe (design "Sizing"), floored at the stops level
plus the spread; the minimum lot's loss at that stop in account currency must be within 1.2 x the target risk
(risk per trade x multiplier 1, after the 8% stage), or within `min_lot_risk_cap` (halved in the 8% stage) once the
engine publishes it (BACKLOG 25). The stop is in USD per ounce; on a non-USD account the loss is converted with the
engine's own `usd_to_account` factor (BACKLOG 28, from the broker). Without that factor, or without the symbol's lot
terms from the broker, the ladder says "pending broker terms" and shows no amounts: it never guesses a rate.
"""
from __future__ import annotations

import math
from typing import Any

from goldbot.base import FrozenRecord
from goldbot.risk.sizing import MIN_LOT_TOLERANCE, effective_risk, stop_distance

LADDER_TIMEFRAMES = ("15m", "1h", "4h", "1d")   # ADR 0004 section 4: M1 15m, M2 1h, M3 4h, M4 1d
LADDER_STOP_ATR = 1.5                            # design "Sizing": stop = 1.5 x ATR(14) of the decision timeframe
PENDING_BROKER_TERMS = "pending broker terms"


class LossCap(FrozenRecord):
    name: str                  # "account 2.0% cap", "combined 1.5% cap", "absolute limit"
    limit: float               # account currency
    used: float                # lost since the day started (00:00 UTC), floating P&L included; >= 0
    remaining: float           # >= 0


class LossBudget(FrozenRecord):
    limit: float               # the binding cap's limit, account currency
    used: float
    remaining: float
    binding: str
    caps: list[LossCap]
    notes: list[str] = []


class MilestoneRung(FrozenRecord):
    timeframe: str
    equity_needed: float | None = None    # account currency; None while pending
    reached: bool | None = None
    pending: str | None = None


class Milestones(FrozenRecord):
    rule: str                              # the sizing rule the thresholds come from, in words
    rungs: list[MilestoneRung]
    tradeable: list[str]                   # timeframes whose minimum lot fits the current equity
    next_timeframe: str | None = None
    next_equity: float | None = None       # account currency
    progress: float | None = None          # equity / next_equity, 0..1
    pending: str | None = None             # why no amounts are shown (the whole ladder)


def _num(x: Any) -> float | None:
    """A finite float, else None (missing keys, nulls and junk in a state file read as unknown)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def _pos(x: Any) -> float | None:
    v = _num(x)
    return v if v is not None and v > 0 else None


def margin_level_pct(engine: dict[str, Any]) -> float | None:
    """Equity / margin in percent, as MT5 reports it; None with no margin in use (or an older engine file)."""
    eq, margin = _num(engine.get("equity")), _pos(engine.get("margin"))
    return None if eq is None or margin is None else eq / margin * 100.0


def usd_to_account(engine: dict[str, Any]) -> float | None:
    """Account currency per 1 USD of gold P&L. 1 on a USD account (XAUUSD P&L is in USD); otherwise only the factor
    the engine publishes from the broker (BACKLOG 28), never a guessed exchange rate."""
    ccy = engine.get("currency")
    if not isinstance(ccy, str) or not ccy:
        return None
    if ccy.upper() == "USD":
        return 1.0
    return _pos(engine.get("usd_to_account"))


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def loss_budget(engine: dict[str, Any], supervisor: dict[str, Any], engines: list[dict[str, Any]]) -> LossBudget | None:
    """Today's loss budget for this engine's account; None when the engine does not publish its limits or day start."""
    limits = engine.get("limits")
    eq, day0 = _num(engine.get("equity")), _pos(engine.get("day_start_equity"))
    if not isinstance(limits, dict) or eq is None or day0 is None:
        return None
    caps: list[LossCap] = []
    notes: list[str] = []

    def add(name: str, limit: float, used: float) -> None:
        used = max(0.0, used)
        caps.append(LossCap(name=name, limit=limit, used=used, remaining=max(0.0, limit - used)))

    daily = _pos(limits.get("daily_cap"))
    if daily is not None:
        add(f"account {_pct(daily)} cap", daily * day0, day0 - eq)
    sup_cap, sup_d0 = _pos(supervisor.get("daily_cap")), _pos(supervisor.get("combined_day_start_equity"))
    sup_eq = _num(supervisor.get("combined_equity"))
    if sup_cap is None or sup_d0 is None or sup_eq is None:
        notes.append("combined cap: the supervisor is not reporting it yet")
    else:
        ccys = {e.get("currency") for e in engines} | {engine.get("currency")}
        if len(ccys) != 1 or None in ccys:
            notes.append("combined cap not shown: the accounts report mixed currencies (BACKLOG 28)")
        else:
            add(f"combined {_pct(sup_cap)} cap", sup_cap * sup_d0, sup_d0 - sup_eq)
    absolute = _pos(limits.get("daily_loss_limit_abs"))
    if absolute is not None:
        add("absolute limit", absolute, day0 - eq)
    else:
        notes.append("the absolute daily loss limit (ADR 0004) is not enforced by the risk check yet (BACKLOG 29)")
    if not caps:
        return None
    bind = min(caps, key=lambda c: c.remaining)
    return LossBudget(limit=bind.limit, used=bind.used, remaining=bind.remaining, binding=bind.name, caps=caps,
                      notes=notes)


class _Limits(FrozenRecord):
    risk_per_trade: float
    max_risk_per_trade: float
    multiplier_bounds: tuple[float, float]


def milestones(engine: dict[str, Any]) -> Milestones | None:
    """The ladder at the engine's live equity, price, ATR and lot terms; None for an engine file without limits."""
    limits = engine.get("limits")
    eq = _num(engine.get("equity"))
    if not isinstance(limits, dict) or eq is None:
        return None
    try:
        lim = _Limits(risk_per_trade=limits["risk_per_trade"], max_risk_per_trade=limits["max_risk_per_trade"],
                      multiplier_bounds=tuple(limits["multiplier_bounds"]))
    except (KeyError, TypeError, ValueError):
        return None
    size_down = engine.get("stage") == "size_down" or bool(engine.get("combined_size_down"))
    cap = _pos(limits.get("min_lot_risk_cap"))
    if cap is not None:
        frac = cap / 2 if size_down else cap
        rule = f"minimum lot risks at most {100 * frac:.2f}% of equity (minimum-lot rule)"
    else:
        target = effective_risk(lim, size_down, 1.0).target
        frac = MIN_LOT_TOLERANCE * target
        rule = f"minimum lot risks at most {MIN_LOT_TOLERANCE:g} x {100 * target:.2f}% of equity"
    if size_down:
        rule += ", halved in the 8% size-down stage"
    rule += f"; stop {LADDER_STOP_ATR:g} x ATR(14) of the timeframe"
    lot: dict[str, Any] = engine["lot_terms"] if isinstance(engine.get("lot_terms"), dict) else {}
    fx = usd_to_account(engine)
    price = _pos(engine.get("price"))
    vmin, contract = _pos(lot.get("volume_min")), _pos(lot.get("contract_oz"))
    if price is None or fx is None or vmin is None or contract is None:
        pending = "waiting for a price from the broker" if price is None else PENDING_BROKER_TERMS
        return Milestones(rule=rule, rungs=[MilestoneRung(timeframe=tf, pending=pending) for tf in LADDER_TIMEFRAMES],
                          tradeable=[], pending=pending)
    stops = _num(lot.get("stops_level_points")) or 0.0
    spread = _num(engine.get("spread_points")) or 0.0
    atrs: dict[str, Any] = engine["ladder_atr"] if isinstance(engine.get("ladder_atr"), dict) else {}
    rungs: list[MilestoneRung] = []
    for tf in LADDER_TIMEFRAMES:
        a = _pos(atrs.get(tf))
        if a is None:
            rungs.append(MilestoneRung(timeframe=tf, pending=f"needs 15 {tf} bars of history for ATR(14)"))
            continue
        stop = stop_distance(LADDER_STOP_ATR, a, stops_level_points=stops, spread_points=spread)
        need = vmin * stop * contract * fx / frac
        rungs.append(MilestoneRung(timeframe=tf, equity_needed=need, reached=eq >= need))
    tradeable = [r.timeframe for r in rungs if r.reached]
    nxt = next((r for r in rungs if r.reached is False), None)
    if nxt is None or nxt.equity_needed is None:
        return Milestones(rule=rule, rungs=rungs, tradeable=tradeable)
    return Milestones(rule=rule, rungs=rungs, tradeable=tradeable, next_timeframe=nxt.timeframe,
                      next_equity=nxt.equity_needed, progress=min(1.0, max(0.0, eq / nxt.equity_needed)))
