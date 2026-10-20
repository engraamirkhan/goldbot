"""Entry status (docs/ux/entry-status.md): per account, can it take a new trade right now; if not, why and what fixes
it, in the owner's words. Reporting only: nothing here gates an order (the RiskGate does, in the engine).

Which reasons apply is decided by the engine, never here (BACKLOG 43, ADR 0011): state/engine_<account>.json carries
`entry_blocks`, the RiskGate's account-level reasons (`RiskGate.account_reasons`) at every state write, and
`entry_refusals`, each agent's last per-trade refusal (gate.PER_TRADE_REFUSALS) with its time. This module words them,
using the facts published beside them for the details (the reopen time from `sessions`, the news title from
`blackout`, the spread, the open trades, the entry `windows` times; BACKLOG 17/18), and adds what only it sees: the
supervisor's halt as the engine reads it (`Supervisor.engine_should_halt`), the owner's halt (control.json), the drift
watch's system halt, the loss budget (goldbot/ops/account_view.py), the per-agent throttle the engine publishes under
`throttle`, and engine silence. An engine that does not publish `entry_blocks` is never shown OPEN
(`entry_reasons_missing`). A reason with no entry in `REASONS` renders generically, never as nothing.

Nothing free-text from the engine reaches the owner (probe reasons name zones and commits, supervisor reasons name
files): only mapped codes, numbers, agent ids and the news title the dashboard already shows. One exception:
`windows.holiday_name`, which is text from the owner's own file (config/holidays_cme_metals.yaml, reviewed like code)
and is capped at 60 characters.
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Collection, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel

from goldbot.api.schema import EntryBlock, EntryStatus
from goldbot.config import Settings
from goldbot.data.calendar import (
    DEFAULT_SESSIONS,
    SessionTable,
    session_table_from_cache,
)
from goldbot.data.timeutil import floor_tf, server_to_utc, server_wall
from goldbot.execution.classifier import MIN_TICKS
from goldbot.execution.not_confirmed import PENDING_REFUSAL, PENDING_REFUSAL_CODE
from goldbot.ops.account_view import LossBudget, loss_budget
from goldbot.ops.accounts import engine_state_files
from goldbot.ops.health import ENGINE_FAIL_S
from goldbot.risk.gate import RiskLimits
from goldbot.risk.supervisor import Supervisor, SupervisorLimits
from goldbot.risk.windows import holiday_end, load_holidays, reopen_window_end

log = logging.getLogger("goldbot.api.entry_status")

RUNBOOK_URL = "https://github.com/engraamirkhan/goldbot/blob/main/docs/RUNBOOK.md"
# Engine silence (review H1): within the supervisor's heartbeat (SupervisorLimits.heartbeat_max_age_s) the report is
# current; past it, the market closed at now is "market closed" (the engine idles), past health's ENGINE_FAIL_S with the
# market open throughout is "engine not reporting" (act), anything in between is "status unknown" (wait), never OPEN.
CODE = re.compile(r"[a-z0-9_]{1,64}")      # anything else from a state file is never shown (review L6)
AGENT = re.compile(r"[A-Za-z0-9_.-]{1,80}")   # an agent id (`<family>-g<n>-<hash>`) from entry_refusals; else dropped
TICK_ERROR_NOTE_S = 15 * 60         # health warns this long after an error the engine carried on from
NEXT_OPEN_SEARCH = pd.Timedelta(days=8)
ROLLOVER_MIN = 5                    # EngineConfig.rollover_min: no entries within this of 00:00 server (a test pins it)

GENERIC = "Engines are running but block every entry"
CLOCK = "Server clock (US-EU DST gap weeks)"
HALT = "Halt and re-arm"
UPDATE = "Updating the VPS to a new version"
HEALTH = "Health check"
WINDOWS = "Entry windows (reopen, holidays, tier-2 news)"
HOLIDAY_LIST_FIX = ("Deploy a holiday list (config/holidays_cme_metals.yaml) with a new version that covers today; "
                    "entries resume once it loads.")

EXITS_OK = "Exits keep running: open trades keep their stop, target and automatic exits."
EXITS_SILENT = ("Open trades keep the stop and target placed at the broker; the engine's own exits (time exit, "
                "weekend rule) resume when it reports again.")
NOTHING = "Nothing to do"


class Reason(BaseModel):
    text: str               # "{agent}" for a strategy's own block
    fix: str
    kind: Literal["act", "wait"]
    runbook: str | None = GENERIC


def _r(text: str, fix: str, kind: Literal["act", "wait"], runbook: str | None = GENERIC) -> Reason:
    return Reason(text=text, fix=fix, kind=kind, runbook=runbook)


# The display order within a kind (what needs the owner comes first overall). Every RiskGate and supervisor reason has
# an entry (tests/test_api_entry_status.py::test_every_gate_and_supervisor_reason_has_owner_text).
REASONS: dict[str, Reason] = {
    "status_unknown": _r("Engine status unknown: no recent report",
                         "Usually a short gap; if it lasts, check the engine service and the Health tab.", "wait", HEALTH),
    "engine_not_reporting": _r("Engine not reporting", "Check the engine service is running and restart it if not.",
                               "act", HEALTH),
    # scheduled cost probes (ADR 0037 amendment 5): demo-only cost measurement, never a trading signal
    "cost_measurement_budget": _r("Cost probe skipped: today's probe budget is used",
                                  "Nothing to do; probes resume when the day resets.", "wait"),
    "cost_measurement_hold": _r("Cost probe refused: its planned hold is longer than 60 seconds",
                                "A settings error: check cost_probe.scheduled.hold_s for this account.", "act"),
    "cost_measurement_stop_above_cap": _r("Cost probe refused: its stop is wider than $3/oz",
                                          "A settings error: check cost_probe.scheduled.stop_usd for this account.",
                                          "act"),
    "cost_measurement_terminal_not_demo": _r("Cost probe refused: the terminal is not a demo terminal",
                                             "Check which account the MT5 terminal is logged in to; probes run on "
                                             "demo only.", "act", HEALTH),
    "cost_measurement_not_probe": _r("Cost probe refused: only scheduled cost probes may use this purpose",
                                     "A code error: report it.", "act"),
    "entry_reasons_missing": _r("Can't check whether new trades are allowed: this engine version doesn't report it",
                                "Update the brain to the current version, then restart the engine.", "act", UPDATE),
    "owner_halt": _r("New entries halted by you or an approver",
                     "Re-arm on this page when ready (owner and authenticator code).", "act", HALT),
    "supervisor_halt": _r("The safety supervisor stopped new entries",
                          "Check the supervisor service and the Health tab.", "act"),
    "no_supervisor_heartbeat": _r("Safety supervisor is not running", "Start the supervisor service.", "act"),
    "supervisor_heartbeat_stale": _r("Safety supervisor stopped reporting", "Restart the supervisor service.", "act"),
    "supervisor_heartbeat_future": _r("Safety supervisor's report is stamped ahead of this server's clock",
                                      "Check the server clock (time sync), then restart the supervisor service.",
                                      "act"),
    "engine_heartbeat_future": _r("An engine's report is stamped ahead of the server clock: entries stopped on "
                                  "every account",
                                  "Check the server clock (time sync), then restart the engine the Health tab names.",
                                  "act"),
    "supervisor_state_unreadable": _r("Safety supervisor's state can't be read",
                                      "Restart the supervisor service; it rewrites the file.", "act"),
    "engine_state_unreadable": _r("The supervisor can't read an engine's state file",
                                  "Restart the engine that wrote it.", "act"),
    "engine_startup_refused": _r("An engine refused to start: entries stopped on every account",
                                 "Health shows the cause on the engine line; fix it and restart that engine, "
                                 "or retire that account.", "act"),
    "open_risk_unknown": _r("Open risk can't be measured (a trade without a stop?)",
                            "Put a stop on every open trade; the Health tab names the account.", "act"),
    "mixed_account_currencies": _r("Engines report different account currencies",
                                   "Stop the engine that should not be running.", "act"),
    "combined_drawdown_halt": _r("All accounts together are 12% below their high: trading stopped",
                                 "Review the losses before anything else.", "act"),
    "drawdown_halt": _r("This account is 12% below its high: trading stopped",
                        "Review the losses before anything else.", "act"),
    "drift_system_halt": _r("Models drifted: entries stopped for your review",
                            "Review on the Health tab, then clear it with the command shown there.", "act", HEALTH),
    "bridge_incompatible": _r("The MT5 box runs an incompatible bridge version",
                              "Update the MT5 box to the same version as the brain.", "act", UPDATE),
    "server_clock_mismatch": _r("Broker clock differs from the expected time zone",
                                "See the Runbook: Server clock. Entries resume a minute after the clocks agree.",
                                "act", CLOCK),
    "tick_from_future": _r("Broker prices are stamped ahead of this server's clock",
                           "See the Runbook: Server clock.", "act", CLOCK),
    "stale_data": _r("No fresh prices from the broker",
                     "Check the MT5 terminal is running, logged in and connected.", "act"),
    "fx_conversion_unavailable": _r("Can't convert dollars to the account currency right now",
                                    "Check the MT5 terminal is logged in and XAUUSD is in Market Watch.", "act"),
    "account_class_unknown": _r("Account type (raw or standard) not identified yet",
                                "Usually clears once the engine has measured the broker's costs; see the Runbook "
                                "if it lasts.", "act"),
    "calendar_unavailable": _r("Economic calendar out of date: entries refused to be safe",
                               "Check the calendar feed on the Feeds tab (it needs a fetch from the last 7 days that "
                               "covers the next 24 h); entries resume once it is current.", "act", WINDOWS),
    "throttle_unknown": _r("{agent}: recent losses couldn't be checked",
                           "Usually clears at the next decision; check the Health tab if it lasts.", "act", HEALTH),
    "market_closed": _r("Market closed", f"{NOTHING}: it reopens by itself.", "wait"),
    "weekend": _r("Weekend: no new trades from Friday evening", f"{NOTHING}: entries resume when the market reopens.",
                  "wait"),
    "holiday_thin": _r("Holiday trading: thin market, entries paused",
                       f"{NOTHING}: entries resume on the next normal trading day.", "wait", WINDOWS),
    "reopen_window": _r("Market just reopened: waiting for prices to settle",
                        f"{NOTHING}: entries resume a few minutes after the reopen.", "wait", WINDOWS),
    "rollover": _r("Daily rollover around broker midnight", f"{NOTHING}: entries resume in a few minutes.", "wait"),
    "news_blackout": _r("News blackout", f"{NOTHING}: entries resume after the event.", "wait"),
    "tier2_blackout": _r("Second-tier US news: entries paused around the release",
                         f"{NOTHING}: entries resume shortly after the release.", "wait", WINDOWS),
    "daily_loss_limit_abs": _r("Today's loss limit reached", f"{NOTHING}: it resets at the start of the next day.",
                               "wait"),
    "daily_cap": _r("Today's loss limit reached", f"{NOTHING}: it resets at the start of the next day.", "wait"),
    "weekly_cap": _r("This week's loss limit reached", f"{NOTHING}: it resets when the new week starts.", "wait"),
    "combined_daily_cap": _r("All accounts together hit today's loss limit",
                             f"{NOTHING}: it resets at the start of the next day.", "wait"),
    "combined_daily_loss_abs": _r("All accounts together hit today's loss limit",
                                  f"{NOTHING}: it resets at the start of the next day.", "wait"),
    "combined_weekly_cap": _r("All accounts together hit this week's loss limit",
                              f"{NOTHING}: it resets when the new week starts.", "wait"),
    "throttle_loss_streak": _r("{agent} paused after losses in a row",
                               f"{NOTHING}: it resumes after its cooldown or a winning shadow trade.", "wait"),
    "throttle_entry_rate": _r("{agent} made its trades for today", f"{NOTHING}: the count resets at 00:00 UTC.",
                              "wait"),
    # BACKLOG 72 (docs/ux/order-not-confirmed.md): an earlier order is still unresolved at the broker; "since" its send
    PENDING_REFUSAL_CODE: _r("An earlier order isn't confirmed by the broker yet",
                             f"{NOTHING}: the engine is looking it up at the broker. If it lasts over 10 minutes, check "
                             "MT5 for an open trade and its stop.", "wait", "Order not confirmed"),
    "tick_error": _r("The engine hit an error reading prices",
                     "Usually clears at the next clean bar; if it repeats, restart the engine.", "wait"),
    "data_quality_error": _r("Market data failed a quality check",
                             "Usually clears by itself; check the Feeds tab if it lasts.", "wait"),
    "invalid_state": _r("The broker sent account figures the safety checks can't use",
                        "Usually clears at the next update; if it lasts, check the MT5 terminal is logged in and see "
                        "the Health tab.", "wait", HEALTH),
    "equity_unconfirmed": _r("The broker's equity reading looked wrong: checking it again",
                             f"{NOTHING}: entries resume after a minute of normal readings; a second 12% reading "
                             "stops trading.", "wait"),
    "data_recovering": _r("Prices just recovered: waiting a minute to be sure", f"{NOTHING}.", "wait"),
    "spread_too_wide": _r("Spread too wide right now", f"{NOTHING}: it narrows by itself (news or rollover).", "wait"),
    "max_positions": _r("Already the maximum number of open trades",
                        f"{NOTHING}: a new entry is possible once one closes.", "wait"),
    "heat_cap": _r("Too much risk already open", f"{NOTHING}: a new entry is possible once open risk falls.", "wait"),
    "same_direction_cap": _r("Enough trades open on this side already",
                             f"{NOTHING}: a new entry this way is possible once one closes.", "wait"),
    "combined_exposure_cap": _r("Enough gold already open across accounts",
                                f"{NOTHING}: a new entry is possible once exposure falls.", "wait"),
    "margin_level_floor": _r("Not enough free margin", f"{NOTHING}: wait for open trades to close.", "wait"),
    "min_lot_exceeds_risk": _r("The smallest trade would risk too much for this balance",
                               "Normal on a small account; the Runbook explains the minimum-lot setting.", "wait"),
    "standard_account_15m": _r("Standard account: no 15-minute trades", f"{NOTHING}: by design on this account type.",
                               "wait"),
    "standard_account_edge": _r("Standard account: edge too small for its costs", f"{NOTHING}: normal filtering.",
                                "wait"),
    "risk_above_approved": _r("Risk rose above the card you approved", f"{NOTHING}: the next proposal sizes afresh.",
                              "wait"),
    "negative_ev": _r("Expected value negative after costs", f"{NOTHING}: normal filtering.", "wait"),
    "target_below_cost_floor": _r("Target too small for the costs", f"{NOTHING}: normal filtering.", "wait"),
    "time_exit_ev_unsupported": _r("Strategy has no target, so its value can't be checked",
                                   f"{NOTHING}: the strategy needs a target before it trades.", "wait"),
    # ADR 0037 amendment 2026-10-19: a demo learning probe's own fence
    "learning_probe_not_demo": _r("Learning trades run on demo accounts only, before the live gate",
                                  f"{NOTHING}: refused here by design.", "wait"),
    "learning_probe_not_min_lot": _r("Learning trades use the broker's smallest lot only",
                                     f"{NOTHING}: refused here by design.", "wait"),
    "negative_gross_edge": _r("No edge even before costs", f"{NOTHING}: normal filtering.", "wait"),
    # ADR 0021: the bridge smoke's operational order only (goldbot/ops/smoke.py prints them; never an engine block)
    "operational_not_demo": _r("Test trades run on demo accounts only, before the live gate",
                               f"{NOTHING}: the bridge smoke is refused here by design.", "wait"),
    "invalid_volume": _r("The broker's smallest trade size cannot be placed",
                         "Check the symbol's minimum lot and lot step in the MT5 terminal.", "act"),
}
_ORDER = {code: i for i, code in enumerate(REASONS)}
# BACKLOG 43 (ui-ux review 2026-10-16): a strategy's last refused trade (`entry_refusals`, gate.PER_TRADE_REFUSALS) is
# worded for that trade, never with the account reason's text (a refused trade is not a spent limit): shown as
# "{agent_label}: last entry refused, {reason}"; the kind, fix and Runbook stay the code's own (REASONS)
REFUSAL_TEXT: dict[str, str] = {
    "daily_loss_limit_abs": "this trade could lose more than is left of today's loss limit",
    "margin_level_floor": "not enough free margin",
    "heat_cap": "too much risk already open",
    "combined_exposure_cap": "enough gold already open across accounts",
    "min_lot_exceeds_risk": "the smallest trade would risk too much for this balance",
    "fx_conversion_unavailable": "couldn't convert dollars to the account currency",
}
# the reason in a card refused at approval (the Telegram line and the dashboard note, docs/ux/order-not-confirmed.md):
# a per-trade refusal's words, or an account reason's own; any other code in plain words, never the raw code
APPROVAL_REFUSAL_TEXT: dict[str, str] = {**REFUSAL_TEXT, PENDING_REFUSAL_CODE: PENDING_REFUSAL}


def refusal_words(code: str) -> str:
    """The words for one RiskGate code in "refused it at approval (…)"."""
    return APPROVAL_REFUSAL_TEXT.get(code) or code.replace("_", " ")


API_ONLY = frozenset({"engine_not_reporting", "status_unknown", "entry_reasons_missing"})   # raised here, not by the engine
WINDOW_CODES = frozenset({"tier2_blackout", "reopen_window", "holiday_thin", "calendar_unavailable"})
# worded ahead of the lanes adding them (bridge check, playbook G-items); the engine publishes them in entry_blocks
FUTURE = frozenset({"bridge_incompatible"})
RESETS_DAILY = frozenset({"combined_daily_cap", "combined_daily_loss_abs", "daily_cap", "daily_loss_limit_abs"})
RESETS_WEEKLY = frozenset({"combined_weekly_cap", "weekly_cap"})
_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}


def _slug(title: str) -> str:
    """GitHub's heading anchor: lower case, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")


def agent_label(aid: str) -> str:
    """How every agent-level line names a strategy (ui-ux review 2026-10-16): its family in words, then its id, e.g.
    "Trend (trend-g0)", "Session open (session_open-g2-ab12)"."""
    # lazily: the research package is heavy for the API's import
    from goldbot.research.attribution import family_of
    family = family_of(aid).replace("_", " ")
    return f"{family[:1].upper()}{family[1:]} ({aid})"


def runbook_url(section: str | None) -> str | None:
    return f"{RUNBOOK_URL}#{_slug(section)}" if section else None


def money(x: float, currency: Any) -> str:
    """"£50", "$51.50", "50.00 CHF"; a plain number without a currency."""
    body = f"{x:,.0f}" if abs(x - round(x)) < 0.005 else f"{x:,.2f}"
    ccy = currency.upper() if isinstance(currency, str) and currency else None
    if ccy in _SYMBOL:
        return f"{_SYMBOL[ccy]}{body}"
    return f"{body} {ccy}" if ccy else body


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def _ts(x: Any) -> pd.Timestamp | None:
    try:
        t = pd.Timestamp(x)
    except (TypeError, ValueError):
        return None
    if pd.isna(t):
        return None
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _dt(t: pd.Timestamp | None) -> datetime | None:
    return None if t is None else t.floor("s").to_pydatetime()


def _next_day(now: pd.Timestamp) -> pd.Timestamp:
    return now.normalize() + pd.Timedelta(days=1)


def _next_week(now: pd.Timestamp) -> pd.Timestamp:
    return floor_tf(pd.DatetimeIndex([now]), 7 * 86400)[0] + pd.Timedelta(days=7)    # weeks start Sunday 00:00 UTC


def _table(sessions: Any) -> SessionTable:
    """The engine's session table as it publishes it; the static one when missing or malformed."""
    t = session_table_from_cache(sessions) if isinstance(sessions, dict) else None
    return t or DEFAULT_SESSIONS


def next_open(sessions: Any, now: pd.Timestamp) -> pd.Timestamp | None:
    """The first minute the engine's session table (as it publishes it) says the market is open; None if not found."""
    try:
        grid = pd.date_range(now.ceil("min"), now + NEXT_OPEN_SEARCH, freq="min")
        hits = _table(sessions).is_open(grid).nonzero()[0]
        return pd.Timestamp(grid[hits[0]]) if len(hits) else None
    except Exception:                       # noqa: BLE001 - reporting only: no reopen time is shown
        log.exception("next market open not computed")
        return None


def rollover_end(sessions: Any, now: pd.Timestamp) -> pd.Timestamp | None:
    """When the rollover pause around `now` ends: the engine refuses entries within ROLLOVER_MIN of 00:00 server time
    (runner `_server_clock`), so it clears at the nearest server midnight + ROLLOVER_MIN, on the server zone the engine
    publishes in `sessions` (any zone, NY+7 included). None when it cannot be placed."""
    try:
        tz = _table(sessions).server_tz
        srv = server_wall(now, tz)[0]
        midnight = srv.normalize() + pd.Timedelta(days=1 if srv.hour >= 12 else 0)
        end = server_to_utc(pd.DatetimeIndex([midnight + pd.Timedelta(minutes=ROLLOVER_MIN)]), tz)[0]
        return None if pd.isna(end) else pd.Timestamp(end)
    except Exception:                       # noqa: BLE001 - reporting only: no time is promised
        log.exception("rollover end not computed")
        return None


def weekend_reopen(sessions: Any, now: pd.Timestamp) -> pd.Timestamp | None:
    """When the week reopens after the weekend cut: the first open minute after the market next closes on the
    published session table. From the Friday cut until the close the market is still open, so `next_open` would say
    "now"; once closed, this is `next_open`. None if not found."""
    try:
        grid = pd.date_range(now.ceil("min"), now + NEXT_OPEN_SEARCH, freq="min")
        is_open = np.asarray(_table(sessions).is_open(grid), dtype=bool)
        closed = (~is_open).nonzero()[0]
        if not len(closed):
            return None
        after = is_open[closed[0]:].nonzero()[0]
        return pd.Timestamp(grid[closed[0] + after[0]]) if len(after) else None
    except Exception:                       # noqa: BLE001 - reporting only: no reopen time is shown
        log.exception("weekend reopen not computed")
        return None


def _open_at(sessions: Any, *ts: pd.Timestamp) -> bool | None:
    """Whether the published table says the market is open at every one of `ts`; None when it cannot be read."""
    try:
        return bool(_table(sessions).is_open(pd.DatetimeIndex(list(ts))).all())
    except Exception:                       # noqa: BLE001
        log.exception("session table unreadable")
        return None


def block(code: str, *, text: str | None = None, agent: str | None = None, at: pd.Timestamp | None = None,
          at_label: Literal["opens", "until", "resets", "since"] | None = None, fix: str | None = None,
          kind: Literal["act", "wait"] | None = None) -> EntryBlock:
    r = REASONS.get(code)
    if r is None:                           # a newer engine's reason: shown, never dropped
        r = Reason(text=f"New entries paused ({code.replace('_', ' ')})",
                   fix="See the Runbook; the Health tab may say more.", kind="act")
    body = text if text is not None else r.text.replace("{agent}", agent_label(agent) if agent else "a strategy")
    return EntryBlock(code=code, scope="agent" if agent else "account", agent_id=agent, text=body,
                      fix=fix if fix is not None else r.fix, kind=kind or r.kind, at=_dt(at) if at_label else None,
                      at_label=at_label if at is not None else None, runbook=r.runbook, runbook_url=runbook_url(r.runbook))


def _cap_block(name: str, period: str, limit: float, used: float, currency: Any, now: pd.Timestamp) -> EntryBlock:
    """One spent loss cap (account_view.LossCap) with its own reset time."""
    week = period == "this week"
    amount = money(limit, currency)
    when = "this week" if week else "today"
    state = "reached" if used >= limit - 0.005 else "nearly reached: less than the smallest trade left"
    if name.startswith("combined"):
        code = "combined_weekly_cap" if week else "combined_daily_cap"
        text = f"All accounts together: {when}'s {amount} loss limit {state}"
    else:
        code = "weekly_cap" if week else ("daily_loss_limit_abs" if name.startswith("absolute") else "daily_cap")
        text = f"{when.capitalize()}'s {amount} loss limit {state}"
    return block(code, text=text, at=_next_week(now) if week else _next_day(now), at_label="resets")


def _budget_blocks(budget: LossBudget, currency: Any, now: pd.Timestamp) -> list[EntryBlock]:
    spent = [c for c in budget.caps if c.remaining <= 0]
    if spent:
        return [_cap_block(c.name, c.period, c.limit, c.used, currency, now) for c in spent]
    if budget.remaining <= 0:
        return [_cap_block(budget.binding, budget.period, budget.limit, budget.used, currency, now)]
    return []


def _throttle_blocks(th: Any, now: pd.Timestamp) -> tuple[list[EntryBlock], set[str]]:
    """The strategies the throttle holds back, and the strategies it covers (the only roster the engine publishes)."""
    if not isinstance(th, dict) or not th.get("enabled") or not isinstance(th.get("agents"), dict):
        return [], set()
    out = []
    agents = th["agents"]
    for aid, v in agents.items():
        if not isinstance(v, dict) or not isinstance(v.get("blocked"), list):
            continue
        for code in [str(c) for c in v["blocked"] if CODE.fullmatch(str(c))]:
            if code == "throttle_loss_streak":
                n = _num(v.get("loss_streak"))
                text = f"{agent_label(str(aid))} paused after {int(n)} losses in a row" if n else None
                out.append(block(code, agent=str(aid), text=text, at=_ts(v.get("cooldown_until")), at_label="until"))
            elif code == "throttle_entry_rate":
                n = _num(th.get("max_entries_per_day_per_agent")) or _num(v.get("entries_today"))
                text = f"{agent_label(str(aid))} made its {int(n)} trades for today" if n else None
                out.append(block(code, agent=str(aid), text=text, at=_next_day(now), at_label="resets"))
            else:
                out.append(block(code, agent=str(aid)))
    return out, {str(a) for a in agents}


def _reopen_until(sessions: Any, now: pd.Timestamp, limits: RiskLimits) -> pd.Timestamp | None:
    """Fallback for an engine that does not publish `reopen_until`: the end of the reopen window on the published
    session table, with the minutes read here (only when the settings read here have the window on). None when it
    cannot be placed."""
    if not limits.reopen.enabled:
        return None
    try:
        return reopen_window_end(_table(sessions), now, limits.reopen)
    except Exception:                       # noqa: BLE001 - reporting only: no time is promised
        log.exception("reopen window end not computed")
        return None


def _holiday(sessions: Any, now: pd.Timestamp, limits: RiskLimits) -> tuple[str | None, pd.Timestamp | None]:
    """Fallback for an engine that does not publish `holiday_name` / `holiday_until`: from the configured list;
    (None, None) when the list or the table cannot be read or today is not listed."""
    try:
        hit = holiday_end(load_holidays(limits.holidays.file), _table(sessions), now)
    except Exception:                       # noqa: BLE001
        log.exception("holiday window unreadable")
        return None, None
    return hit if hit is not None else (None, None)


def _window_block(code: str, w: Any, sessions: Any, now: pd.Timestamp, limits: RiskLimits) -> EntryBlock:
    """One entry window the engine lists (BACKLOG 17/18), worded with the times it publishes in `windows`
    (`tier2_until`, `reopen_until`, `holiday_until`, `holiday_name`, `calendar_unavailable_since`); the limits read
    here are only a fallback for wording and times when it does not publish them, never a reason to hide the window.
    The engine's free-text `calendar_unavailable_reason` is only read to choose between two mapped texts, never shown."""
    w = w if isinstance(w, dict) else {}
    if code == "calendar_unavailable":
        why = w.get("calendar_unavailable_reason")
        holiday_list = isinstance(why, str) and why.startswith("holiday list")
        return block(code, text="Holiday list out of date: entries refused to be safe" if holiday_list else None,
                     fix=HOLIDAY_LIST_FIX if holiday_list else None,
                     at=_ts(w.get("calendar_unavailable_since")), at_label="since")
    if code == "tier2_blackout":
        until = _ts(w.get("tier2_until"))
        t2 = limits.tier2
        fix = None if until is not None or not t2.enabled else \
            f"{NOTHING}: entries resume {t2.after_min} min after the release (the pause starts {t2.before_min} min before it)."
        return block(code, fix=fix, at=until, at_label="until")
    if code == "reopen_window":
        until = _ts(w.get("reopen_until"))
        return block(code, at=until if until is not None else _reopen_until(sessions, now, limits), at_label="until")
    name = w.get("holiday_name") if isinstance(w.get("holiday_name"), str) else None       # holiday_thin
    until = _ts(w.get("holiday_until"))
    if name is None or until is None:
        fb_name, fb_until = _holiday(sessions, now, limits)
        name, until = name or fb_name, until if until is not None else fb_until
    name = name.strip()[:60] if name else None
    return block(code, text=f"Holiday trading ({name}): thin market, entries paused" if name else None, at=until,
                 at_label="until")


def _engine_block(code: str, e: dict[str, Any], now: pd.Timestamp, limits: RiskLimits) -> EntryBlock:
    """One reason the engine lists in `entry_blocks`, worded with the facts it publishes beside it."""
    sessions = e.get("sessions")
    if code == "drawdown_halt":
        return block(code, text=f"This account is {_pct(limits.dd_stage2)} below its high: trading stopped")
    if code == "market_closed":
        return block(code, at=next_open(sessions, now), at_label="opens")
    if code == PENDING_REFUSAL_CODE:          # since the oldest unresolved order was sent
        uo = e.get("unconfirmed_orders")
        return block(code, at=_ts(uo.get("oldest_utc")) if isinstance(uo, dict) else None, at_label="since")
    if code == "rollover":
        return block(code, at=rollover_end(sessions, now), at_label="until")
    if code == "weekend":
        return block(code, at=weekend_reopen(sessions, now), at_label="opens")
    if code == "news_blackout":
        bo = e.get("blackout")                # a blackout is listed whatever its title (review L3)
        bo = bo if isinstance(bo, dict) else {}
        label = "News shock" if bo.get("kind") == "news_shock" else "News blackout"
        title = str(bo.get("title") or "").strip()[:80]
        return block(code, text=f"{label}: {title}" if title else label)
    if code == "spread_too_wide":
        spread = _num(e.get("spread_points"))
        return block(code, text=None if spread is None else
                     f"Spread too wide right now ({spread:.0f} points; limit {limits.max_spread_points:.0f})")
    if code == "max_positions":
        n_open = _num(e.get("open_positions"))
        return block(code, text=None if n_open is None else
                     f"Already {int(n_open)} trades open (limit {limits.max_positions})")
    if code in RESETS_DAILY:
        return block(code, at=_next_day(now), at_label="resets")
    if code in RESETS_WEEKLY:
        return block(code, at=_next_week(now), at_label="resets")
    if code in WINDOW_CODES:
        return _window_block(code, e.get("windows"), sessions, now, limits)
    return block(code)


def _refusal_blocks(refusals: Any) -> list[EntryBlock]:
    """`entry_refusals` (BACKLOG 43): each agent's last per-trade refusal, as that agent's own block since its time.
    The engine clears one when the gate next allows that agent. Codes and agent ids only; anything else is dropped."""
    if not isinstance(refusals, dict):
        return []
    out = []
    for aid, v in refusals.items():
        code = v.get("code") if isinstance(v, dict) else None
        if not isinstance(code, str) or not CODE.fullmatch(code) or not AGENT.fullmatch(str(aid)):
            continue
        why = REFUSAL_TEXT.get(code)
        out.append(block(code, agent=str(aid), at=_ts(v.get("at")), at_label="since",
                         text=f"{agent_label(str(aid))}: last entry refused, {why}" if why else None))
    return out


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%" if abs(100 * x - round(100 * x)) < 1e-9 else f"{100 * x:.1f}%"


def unknown_status() -> EntryStatus:
    """When the status could not be built (a bug or a state file the API cannot read): never OPEN."""
    b = block("status_unknown", text="Entry status couldn't be worked out",
              fix="Check the Health tab; the engine still applies every rule itself.")
    b = b.model_copy(update={"kind": "act"})
    return EntryStatus(state="blocked", headline=f"No new trades known: {b.text}", blocks=[b], exits=EXITS_SILENT)


def account_entry_status(e: dict[str, Any], *, now: pd.Timestamp, owner_halt: str | None,
                         supervisor_reasons: list[str] | None, drift_reasons: list[str] | None,
                         budget: LossBudget | None, limits: RiskLimits, is_owner: bool = True,
                         combined_dd: float | None = None, class_ticks: int | None = None) -> EntryStatus:
    """class_ticks: the classifier's London/NY tick count while it lacks ticks (`class_ticks_pending`); owner_halt: the channel that halted (None: not halted); supervisor_reasons: the supervisor's halt as the engine reads it
    (None: no halt; empty: a halt without reasons); drift_reasons: the drift watch's system halt (None: none);
    budget: the account's loss budget (account_view.loss_budget); is_owner: the viewer may re-arm; combined_dd: the
    supervisor's combined drawdown halt (SupervisorLimits)."""
    now = pd.Timestamp(now).tz_convert("UTC")
    found: list[EntryBlock] = []
    notes: list[str] = []

    def add(b: EntryBlock) -> None:
        if not any(x.code == b.code and x.agent_id == b.agent_id for x in found):
            found.append(b)

    ts = _num(e.get("ts"))
    reported = pd.Timestamp(ts, unit="s", tz="UTC") if ts is not None else None
    heartbeat = SupervisorLimits().heartbeat_max_age_s
    age = (now - reported).total_seconds() if reported is not None else None
    future = age is not None and age < -heartbeat               # a clock fault: not a report (review L4)
    silent = age is None or future or age > heartbeat
    sessions = e.get("sessions")
    budget_blocks = _budget_blocks(budget, e.get("currency"), now) if budget is not None and not silent else []

    for b in budget_blocks:                 # first, so their reset times survive the supervisor's same codes
        add(b)
    if owner_halt is not None:
        add(block("owner_halt", fix=None if is_owner else "The owner re-arms on this page when ready."))
    if supervisor_reasons is not None:
        codes = [r for r in supervisor_reasons if CODE.fullmatch(r)]
        if supervisor_reasons and not codes:
            codes = ["supervisor_halt"]
        for r in codes or ["supervisor_halt"]:
            if r == "combined_drawdown_halt":
                dd = combined_dd if combined_dd is not None else SupervisorLimits().dd_stage2
                add(block(r, text=f"All accounts together are {_pct(dd)} below their high: trading stopped"))
            elif r in RESETS_DAILY:
                add(block(r, at=_next_day(now), at_label="resets"))
            elif r in RESETS_WEEKLY:
                add(block(r, at=_next_week(now), at_label="resets"))
            else:
                add(block(r))
    if drift_reasons is not None:
        add(block("drift_system_halt"))

    if silent:
        closed_now = _open_at(sessions, now) is False
        if closed_now:                      # the engine is idle while the market is closed: never a fault (review H1)
            add(block("market_closed", text="Market closed (engine idle while closed)", at=next_open(sessions, now),
                      at_label="opens"))
        elif not future and age is not None and age > ENGINE_FAIL_S \
                and _open_at(sessions, now, now - pd.Timedelta(seconds=ENGINE_FAIL_S)):
            add(block("engine_not_reporting", at=reported, at_label="since"))    # health fails it too
        elif future or age is None:
            add(block("engine_not_reporting"))
        else:
            add(block("status_unknown", at=reported, at_label="since"))
    else:
        published = e.get("entry_blocks")
        listed = [c for c in published if isinstance(c, str) and CODE.fullmatch(c)] \
            if isinstance(published, list) else []
        # an engine from before BACKLOG 43, a failed listing (left out), or a list of which nothing is a code
        # (trading-safety INFO-4): the engine refuses for a reason the API cannot say, so never OPEN
        if not isinstance(published, list) or (published and not listed):
            add(block("entry_reasons_missing"))
        else:
            for code in listed:
                if code == "stale_data" and "market_closed" in listed:
                    continue                # BACKLOG 59: a closed market has no fresh prices by design; never a fault
                if code == "supervisor_halt" and supervisor_reasons is not None:
                    continue                # the supervisor's own reasons, named above, say more
                if code == "account_class_unknown" and class_ticks is not None:
                    add(block(code, text=f"Account type not identified yet: {class_ticks:,} of {MIN_TICKS:,} "
                                         f"market ticks recorded",
                              fix="Checked every night; clears by itself once enough ticks are recorded.",
                              kind="wait"))
                    continue
                add(_engine_block(code, e, now, limits))
        declared = e.get("account_class_declared")
        if declared in ("raw", "standard") and e.get("account_class_measured") == "unknown":
            notes.append(f"Account type: {str(declared).capitalize()} (from the account settings; not measured yet, "
                         f"checked every night).")
        for b in _refusal_blocks(e.get("entry_refusals")):
            add(b)
        last_err = _ts(e.get("last_tick_error_at"))
        if e.get("tick_error") is not True and last_err is not None \
                and 0 <= (now - last_err).total_seconds() <= TICK_ERROR_NOTE_S:
            n = int(_num(e.get("tick_errors_total")) or 0)
            mins = max(1, round((now - last_err).total_seconds() / 60))
            notes.append(f"Price error {mins} min ago; the engine carried on ({n} since it started).")
        bc = e.get("bridge_compat")
        if isinstance(bc, dict) and bc.get("status") == "ok" and bc.get("commit_differs") is True:
            notes.append("The MT5 box and the brain run different versions; entries are not affected.")

    agent_blocks, roster = ([], set()) if silent else _throttle_blocks(e.get("throttle"), now)
    for b in agent_blocks:
        add(b)
    found.sort(key=lambda b: (b.kind, _ORDER.get(b.code, len(_ORDER))))
    account = [b for b in found if b.scope == "account"]
    held = sorted({b.agent_id for b in found if b.agent_id})
    # every known strategy held (code review LOW-2): the known ones are the throttle's roster plus any agent a refusal
    # names, so held must cover the roster; with no roster published, never claimed (partly open at most)
    everyone = bool(roster) and roster <= set(held)
    stopping = account if account else (found if everyone else [])
    clears: pd.Timestamp | None = None
    if stopping:
        state: Literal["open", "partial", "waiting", "blocked"] = \
            "blocked" if any(b.kind == "act" for b in stopping) else "waiting"
        more = f" (+{len(found) - 1} more)" if len(found) > 1 else ""
        headline = f"No new trades{'' if state == 'blocked' else ' for now'}: {stopping[0].text}{more}"
        if state == "waiting" and all(b.at is not None and b.at_label != "since" for b in stopping):
            clears = max(pd.Timestamp(b.at) for b in stopping if b.at is not None)
    elif held:
        state, headline = "partial", f"Can take new trades, except {', '.join(agent_label(str(a)) for a in held)}"
    else:
        state, headline = "open", "Can take new trades"
    return EntryStatus(state=state, headline=headline, blocks=found, notes=notes,
                       exits=EXITS_SILENT if silent and _open_at(sessions, now) is not False else EXITS_OK,
                       as_of=_dt(reported), clears_at=_dt(clears))


# ----------------------------------------------------------------------------- from the state directory
def class_ticks_pending(state_dir: str | Path, account_id: str) -> int | None:
    """The London/NY tick count while the account's classifier is unknown only for lack of ticks ("only N London/NY
    ticks (< 1000)", execution/classifier.classify); None for a known class, any other reason, or an unreadable file."""
    if not AGENT.fullmatch(account_id):
        return None
    try:
        d = json.loads((Path(state_dir) / f"classifier_{account_id}.json").read_text())
        run = d["runs"][-1]
        m = re.fullmatch(r"only (\d+) London/NY ticks \(< \d+\)", str(run["reason"]))
        return int(m.group(1)) if d.get("class") == "unknown" and m else None
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return None


def read_drift_halt(state_dir: str | Path) -> list[str] | None:
    """Reasons for the drift watch's system halt (state/drift.json), None when there is none. Read the way the
    engines read it: missing restricts nothing, unreadable or invalid halts entries (fail closed)."""
    f = Path(state_dir) / "drift.json"
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text())
    except (ValueError, OSError):
        return ["drift.json unreadable"]
    if not isinstance(d, dict):
        return ["drift.json invalid"]
    sh = d.get("system_halt")
    if not sh:
        return None
    reasons = sh.get("reasons") if isinstance(sh, dict) else None
    return [str(r) for r in reasons] if isinstance(reasons, list) and reasons else ["drift system halt"]


def entry_inputs(state_dir: str | Path, settings: Settings | None, *, is_owner: bool,
                 now: pd.Timestamp | None = None) -> dict[str, Any]:
    """What every account's entry status shares, for the dashboard and Telegram alike: the owner's halt
    (control.json), the supervisor's halt as the engines read it, the drift watch's system halt, the RiskGate's and the
    supervisor's limits from settings (the defaults when missing or unreadable) and whether the reader may re-arm.
    `now`: the instant the supervisor heartbeat is judged at (None: the wall clock, as the engines judge it)."""
    from goldbot.telegram.bus import read_control  # lazily: the telegram package imports this module
    c = read_control(state_dir)
    halt, why = Supervisor.engine_should_halt(state_dir, now=None if now is None else pd.Timestamp(now).timestamp())
    try:
        limits = RiskLimits.from_settings(settings.risk, tiny_live=False) if settings is not None else RiskLimits()
        sup_limits = SupervisorLimits.from_settings(settings.risk) if settings is not None else SupervisorLimits()
    except Exception:                      # noqa: BLE001 - a bad settings edit: the defaults
        limits, sup_limits = RiskLimits(), SupervisorLimits()
    # the channel that halted, never who (A15): an older control.json's "<channel>:<id>" is cut to the channel
    return dict(owner_halt=(c.by or "unknown").split(":", 1)[0] if c.halted else None,
                supervisor_reasons=[r for r in why.split(",") if r] if halt else None,
                drift_reasons=read_drift_halt(state_dir), limits=limits, combined_dd=sup_limits.dd_stage2,
                is_owner=is_owner)


def statuses_from_state(state_dir: str | Path, now: pd.Timestamp, settings: Settings | None, *,
                        is_owner: bool = True, accounts: Collection[str] | None = None
                        ) -> list[tuple[str, str, EntryStatus]]:
    """(account id, mode, entry status) for every ENABLED engine that reports, sorted by account; built exactly as
    the dashboard's /api/accounts builds it (loss budget, shared inputs). A status that cannot be built is
    `unknown_status()`, never OPEN; a torn engine file is skipped, as the API skips it. `accounts`: the enabled ids
    (None: config/accounts.yaml, `goldbot.ops.accounts.engine_state_files`); health passes its own."""
    sd = Path(state_dir)
    engines: list[dict[str, Any]] = []
    for f in engine_state_files(sd, accounts):
        try:
            e = json.loads(f.read_text())
        except (OSError, ValueError):
            log.warning("engine state %s unreadable: skipped", f.name)
            continue
        if isinstance(e, dict):
            engines.append(e)
    try:
        sup = json.loads((sd / "supervisor.json").read_text()) if (sd / "supervisor.json").exists() else {}
    except (OSError, ValueError):
        sup = {}
    sup = sup if isinstance(sup, dict) else {}
    try:
        inputs: dict[str, Any] | None = entry_inputs(sd, settings, is_owner=is_owner, now=now)
    except Exception:                      # noqa: BLE001 - reporting only: every account shows status unknown
        log.exception("entry status inputs failed")
        inputs = None
    out = []
    for e in engines:
        status: EntryStatus | None = None
        if inputs is not None:
            try:
                status = account_entry_status(e, now=now, budget=_quiet(loss_budget, e, sup, engines),
                                              class_ticks=class_ticks_pending(sd, str(e.get("account") or "")), **inputs)
            except Exception:              # noqa: BLE001
                log.exception("entry status failed")
        mode = e.get("mode")
        out.append((str(e.get("account") or "unknown"), mode if isinstance(mode, str) else "unknown",
                    status or unknown_status()))
    return sorted(out, key=lambda x: x[0])


def _quiet(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception:                      # noqa: BLE001 - as the API: a bad view is null, never a failed status
        log.exception("account view %s failed", getattr(fn, "__name__", fn))
        return None
