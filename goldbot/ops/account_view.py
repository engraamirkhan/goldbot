"""The live account view (owner request; ADR 0004 sections 3 and 4): the loss budget and the milestone ladder,
computed from what the engine publishes in state/engine_<account>.json and the supervisor in state/supervisor.json.
Nothing here reads config: balances, currency, limits, price, ATR and lot terms are the engine's live values.
(The gate still sizes with its default lot terms until ADR 0004 A6 lands, so the ladder's broker lot terms can differ
from what the gate uses.) Reporting only: nothing here sizes or gates an order.

Loss budget. Every loss cap that stops new entries is listed with its limit, its use and what is left, in account
currency: the account's daily 2% and weekly 5% (gate.py, on day- and week-start equity), the supervisor's combined
daily 1.5% and weekly 4% (supervisor.py, on combined start equity) and the owner's absolute daily limit (ADR 0004
section 3). The budget shown is the cap with least left (a bad week can bind on a quiet day). Notes:
* each cap is on the loss from its period's start, as the gate computes it, so a profitable day adds room;
* the combined caps need a supervisor report under 60 s old and one currency across accounts (summing pounds and
  dollars would be wrong: ADR 0004 audit A8). With the report missing, unreadable, stale, stamped ahead or without
  its caps, or with the accounts in mixed currencies (`unknown_mixed`; BACKLOG 61, until BACKLOG 28 converts them),
  the budget "can't check" (`unknown`, the owner's text, and `unknown_paused`, whether the engines pause new entries
  on it), never an amount: the account's own caps alone would look roomier than the combined ones the supervisor
  enforces (found by the release manager 2026-10-16), and a combined cap rebuilt here from settings and one
  account's equity would miss the other accounts' losses. The engines pause new entries on the same rejected report
  (`Supervisor.engine_should_halt`); with mixed currencies only while the supervisor halts on
  `mixed_account_currencies` (a leftover engine file in another currency, over 60 s old, does not halt it);
* the absolute limit is listed only once the engine publishes `limits.daily_loss_limit_abs` (account currency, per
  account; the currency lane, BACKLOG 28/29): until the RiskGate enforces it, showing it would claim a protection
  that is not there. Its check is projected, so when the engine also publishes `limits.min_lot_risk_account` (the
  smallest trade's stop risk) that amount is reserved: the limit reads as spent once less than the smallest
  trade's stop is left;
* the caps are checked before an entry against the loss so far, so a day can still end one stop past a cap.

Milestone ladder. A timeframe is tradeable when its minimum lot fits the RiskGate's sizing rule at the current equity
(goldbot/risk/sizing.py): the stop is 1.5 x ATR(14) of that timeframe (design "Sizing"; a specialist's own stop can be
1.0-1.5 x ATR, so this is the design default, not every strategy's), floored at the stops level plus the spread; the
minimum lot's loss at that stop in account currency must be within 1.2 x the target risk at normal conviction
(risk per trade x multiplier 1, after the 8% stage), or within `min_lot_risk_cap` (halved in the 8% stage) once the
engine publishes it (BACKLOG 25). The stop is in USD per ounce; on a non-USD account the loss is converted with the
engine's own `usd_to_account` factor (BACKLOG 28, from the broker). Without that factor, or without the symbol's lot
terms from the broker, the ladder says "pending broker terms" and shows no amounts: it never guesses a rate. ATR is
the engine's mid-price ATR of its own bars (1d on the feature-day), so it can differ slightly from a broker chart's.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Callable, Literal

from goldbot.base import FrozenRecord
from goldbot.data.timeutil import heartbeat_problem
from goldbot.risk.sizing import MIN_LOT_TOLERANCE, effective_risk, stop_distance

LADDER_TIMEFRAMES = ("15m", "1h", "4h", "1d")   # ADR 0004 section 4: M1 15m, M2 1h, M3 4h, M4 1d
LADDER_STOP_ATR = 1.5                            # design "Sizing": stop = 1.5 x ATR(14) of the decision timeframe
PENDING_BROKER_TERMS = "pending broker terms"
Period = Literal["today", "this week"]
# why a loss budget can't be checked: "paused"/"mixed_currencies_paused" are red (new entries paused on it),
# "not_summed"/"mixed_currencies" amber (BACKLOG 61)
CantCheck = Literal["paused", "not_summed", "mixed_currencies", "mixed_currencies_paused"]
SUPERVISOR_MAX_AGE_S = 60.0                     # the engines' own heartbeat rule (SupervisorLimits.heartbeat_max_age_s)


class LossCap(FrozenRecord):
    name: str                  # "account 2.0% daily cap", "combined 4.0% weekly cap", "absolute daily limit"
    label: str                 # the same in plain words for the owner: "all accounts together (1.5% of today's start)"
    period: Period
    limit: float               # account currency
    used: float                # lost since the period started (00:00 UTC / Sunday 00:00 UTC), floating P&L included
    remaining: float           # max(0, limit - loss since the start [- the smallest trade's stop]); today's gains add room


class LossBudget(FrozenRecord):
    limit: float               # the binding cap's limit, account currency
    used: float
    remaining: float
    binding: str
    binding_label: str
    period: Period
    caps: list[LossCap]
    notes: list[str] = []
    # why the budget can't be checked (the owner's text: the combined caps can't be checked), else None.
    # limit/used/remaining are then the account's own caps only, kept for the entry status's spent-cap blocks; the
    # card shows no amount
    unknown: str | None = None
    unknown_paused: bool = False   # with `unknown`: the engines pause new entries on the same report (red, not amber)
    unknown_mixed: bool = False    # with `unknown`: the cause is accounts in mixed currencies (BACKLOG 61)

    @property
    def cant_check(self) -> CantCheck | None:
        """Why the budget can't be checked, as a code (`/api/proposals` `loss_cant_check`); None when it is known."""
        if self.unknown is None:
            return None
        if self.unknown_mixed:
            return "mixed_currencies_paused" if self.unknown_paused else "mixed_currencies"
        return "paused" if self.unknown_paused else "not_summed"

    @property
    def cant_check_reason(self) -> str | None:
        """The approval card's short reason for "can't check" (Telegram and the web), None when the budget is known."""
        if self.unknown is None:
            return None
        if self.unknown_mixed:
            return "the accounts report different currencies" + ("; new entries paused" if self.unknown_paused else "")
        return ("supervisor not reporting; new entries paused" if self.unknown_paused
                else "the supervisor hasn't added up the accounts yet")


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


def loss_budget(engine: dict[str, Any], supervisor: dict[str, Any], engines: list[dict[str, Any]], *,
                now: float | None = None, trade_risk: float | None = None) -> LossBudget | None:
    """The loss budget for this engine's account; None when the engine does not publish its limits or day start.

    Each cap is on the loss from its period's start equity (the gate's `1 - equity / start >= cap`), so a profitable
    day adds room: left = max(0, limit - (start - equity)). The binding cap is the one with least left. The combined
    caps need a fresh supervisor report (heartbeat within SUPERVISOR_MAX_AGE_S) and one currency across accounts.

    With `trade_risk` (a proposal's stop risk, account currency) every cap reserves that amount instead, so the
    budget reads "left if this trade loses" (the approval card); the absolute limit then reserves the trade, not the
    smallest trade."""
    limits = engine.get("limits")
    eq, day0 = _num(engine.get("equity")), _pos(engine.get("day_start_equity"))
    if not isinstance(limits, dict) or eq is None or day0 is None:
        return None
    week0 = _pos(engine.get("week_start_equity"))
    caps: list[LossCap] = []
    notes: list[str] = []

    def add(name: str, label: str, period: Period, limit: float, start: float, current: float,
            reserve: float = 0.0) -> None:
        loss = start - current
        reserve = trade_risk if trade_risk is not None else reserve
        caps.append(LossCap(name=name, label=label, period=period, limit=limit, used=max(0.0, loss),
                            remaining=max(0.0, limit - loss - reserve)))

    daily, weekly = _pos(limits.get("daily_cap")), _pos(limits.get("weekly_cap"))
    if daily is not None:
        add(f"account {_pct(daily)} daily cap", f"this account ({_pct(daily)} of today's start)", "today",
            daily * day0, day0, eq)
    if weekly is not None and week0 is not None:
        add(f"account {_pct(weekly)} weekly cap", f"this account this week ({_pct(weekly)} of the week's start)",
            "this week", weekly * week0, week0, eq)
    unknown, paused, mixed = _combined(engine, supervisor, engines, time.time() if now is None else now, add)
    absolute = _pos(limits.get("daily_loss_limit_abs"))
    if absolute is not None:
        nxt = _pos(limits.get("min_lot_risk_account"))
        smallest = nxt is not None and trade_risk is None
        label = "your absolute daily limit" + (" (includes the smallest trade's stop)" if smallest else "")
        add("absolute daily limit", label, "today", absolute, day0, eq, reserve=nxt or 0.0)
    else:
        notes.append("the absolute daily loss limit (ADR 0004) is not enforced by the risk check yet (BACKLOG 29)")
    if not caps:
        return None
    bind = min(caps, key=lambda c: c.remaining)
    return LossBudget(limit=bind.limit, used=bind.used, remaining=bind.remaining, binding=bind.name,
                      binding_label=bind.label, period=bind.period, caps=caps, notes=notes, unknown=unknown,
                      unknown_paused=paused, unknown_mixed=mixed)


def proposal_budget(engines: list[dict[str, Any]], supervisor: dict[str, Any], account_id: str,
                    risk: float | None, *, now: float | None = None) -> LossBudget | None:
    """The loss budget left on `account_id` if a proposal with stop risk `risk` (account currency) loses: the card's
    "£11.80 left today if this loses". None when the risk or the account's engine file is unknown (never guessed);
    a budget that can't be checked keeps `unknown` set and is shown without an amount."""
    if risk is None or not math.isfinite(risk) or risk < 0:
        return None
    engine = next((e for e in engines if isinstance(e, dict) and e.get("account") == account_id), None)
    if engine is None:
        return None
    return loss_budget(engine, supervisor, engines, now=now, trade_risk=risk)


def proposal_budget_from_state(state_dir: str | Path, account_id: str, risk: float | None) -> LossBudget | None:
    """`proposal_budget` from the state files (engine_*.json, supervisor.json), for the Telegram service. Reporting
    only: any unreadable file reads as unknown (None), never as a failed proposal message."""
    d = Path(state_dir)
    try:                                   # a torn supervisor.json: the budget can't be checked, as on the dashboard
        sup = json.loads((d / "supervisor.json").read_text()) if (d / "supervisor.json").exists() else {}
    except (OSError, ValueError):
        sup = {}
    try:
        engines = [e for f in sorted(d.glob("engine_*.json")) if isinstance(e := json.loads(f.read_text()), dict)]
        return proposal_budget(engines, sup if isinstance(sup, dict) else {}, account_id, risk)
    except Exception:                      # noqa: BLE001
        return None


_PAUSED = "New entries are paused; exits always run."
_CANT_CHECK = {
    "missing": "The safety supervisor's report is missing or unreadable, so the combined loss cap can't be checked. "
               + _PAUSED,
    "invalid": "The safety supervisor's report is missing or unreadable, so the combined loss cap can't be checked. "
               + _PAUSED,
    "stale": "The safety supervisor hasn't reported for over a minute, so the combined loss cap can't be checked. "
             + _PAUSED,
    "future": "The safety supervisor's report is dated ahead of this server's clock (check the server time), so the "
              "combined loss cap can't be checked. " + _PAUSED,
    "halted": "The safety supervisor has halted and isn't reporting the combined loss cap. " + _PAUSED,
    "not_summed": "The safety supervisor hasn't added up the accounts yet, so the combined loss cap isn't shown. "
                  "Exits always run.",
}


def _mixed(currencies: set[Any], paused: bool) -> str:
    """The owner's text for mixed currencies (BACKLOG 61): which currencies, and whether new entries are paused."""
    names = sorted({c for c in currencies if isinstance(c, str) and c})
    if any(not (isinstance(c, str) and c) for c in currencies):
        names.append("one not reported")
    return (f"The accounts report different currencies ({', '.join(names)}), so the combined loss cap can't be "
            "checked. " + (_PAUSED if paused else "Each account's own loss limit still applies; exits always run."))


def _combined(engine: dict[str, Any], supervisor: dict[str, Any], engines: list[dict[str, Any]], now: float,
              add: Callable[..., None]) -> tuple[str | None, bool, bool]:
    """The supervisor's combined daily and weekly caps, when its report is fresh and every account shares a currency.
    Returns (why the budget can't be checked, whether new entries are paused on it, whether the cause is mixed
    currencies); (None, False, False) when it can. The reason is the one the engines halt on
    (`Supervisor.engine_should_halt`, same 60 s): an empty report (the file missing or torn) is missing, one without
    `ts` is stale, a `ts` that is not a number is unreadable. Mixed currencies come from this view's engine files or
    the supervisor's own `mixed_account_currencies` (its combined figures then add pounds to dollars); red only while
    the supervisor halts on it, as only then are new entries paused for it."""
    if not supervisor:
        return _CANT_CHECK["missing"], True, False
    problem = heartbeat_problem(supervisor.get("ts", 0), now, SUPERVISOR_MAX_AGE_S)
    if problem is not None:
        return _CANT_CHECK[problem], True, False
    sup_eq = _num(supervisor.get("combined_equity"))
    sup_d0, sup_w0 = _pos(supervisor.get("combined_day_start_equity")), _pos(supervisor.get("combined_week_start_equity"))
    sup_day, sup_week = _pos(supervisor.get("daily_cap")), _pos(supervisor.get("weekly_cap"))
    if sup_eq is None or sup_day is None or sup_d0 is None:    # error_halt, or no engine summed yet
        halted = supervisor.get("halt") is True
        return _CANT_CHECK["halted" if halted else "not_summed"], halted, False
    ccys = {e.get("currency") for e in engines} | {engine.get("currency")}
    reasons = supervisor.get("reasons")
    sup_mixed = isinstance(reasons, list) and "mixed_account_currencies" in reasons
    sup_ccys = supervisor.get("currencies")
    if sup_mixed and isinstance(sup_ccys, dict):
        ccys |= set(sup_ccys.values())
    if sup_mixed or len(ccys) != 1 or None in ccys:
        paused = sup_mixed and supervisor.get("halt") is True
        return _mixed(ccys, paused), paused, True
    add(f"combined {_pct(sup_day)} daily cap", f"all accounts together ({_pct(sup_day)} of today's start)", "today",
        sup_day * sup_d0, sup_d0, sup_eq)
    if sup_week is not None and sup_w0 is not None:
        add(f"combined {_pct(sup_week)} weekly cap",
            f"all accounts together this week ({_pct(sup_week)} of the week's start)", "this week",
            sup_week * sup_w0, sup_w0, sup_eq)
    return None, False, False


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
        rule = f"the smallest trade (minimum lot) risks at most {100 * frac:.2f}% of equity (minimum-lot rule)"
        rule += " (halved: the account is in the 8% size-down stage)" if size_down else ""
    else:   # the stage halves the risk per trade and caps the multiplier: a reduction, not a plain halving
        frac = MIN_LOT_TOLERANCE * effective_risk(lim, size_down, 1.0).target
        rule = f"the smallest trade (minimum lot) risks at most {100 * frac:.2f}% of equity at normal conviction"
        rule += ", reduced (8% size-down stage)" if size_down else ""
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
