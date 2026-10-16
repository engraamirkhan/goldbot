"""Entry status (docs/ux/entry-status.md): per account, can it take a new trade right now; if not, why and what fixes
it, in the owner's words. Reporting only: nothing here gates an order (the RiskGate does, in the engine).

Built from what the engine publishes in state/engine_<account>.json (the RiskGate's account-level inputs: drawdown
stage, data flags, market_closed, server_clock, tick_error, throttle, bridge_compat, blackout, the entry `windows`
(reopen, holiday, tier-2 news, calendar unavailable; BACKLOG 17/18) and, from engines that publish it,
`entry_blocks`), the supervisor's halt as the engine reads it (`Supervisor.engine_should_halt`), the owner's
halt (control.json), the drift watch's system halt and the loss budget (goldbot/ops/account_view.py). Thresholds come
from the RiskGate's own limits (`RiskLimits.from_settings`), never a copy, so the browser holds no policy at all.

Reasons the RiskGate only knows per trade (negative EV, fx conversion, heat cap) are listed when the engine publishes
them in `entry_blocks`; rollover and the Friday weekend cut are not published yet (the engine should publish
`entry_blocks`, ADR 0011, BACKLOG 43). A reason with no entry in `REASONS` renders generically, never as nothing.

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
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel

from goldbot.api.schema import EntryBlock, EntryStatus
from goldbot.config import Settings
from goldbot.data.calendar import (
    DEFAULT_SESSIONS,
    SessionTable,
    session_table_from_cache,
)
from goldbot.data.timeutil import floor_tf
from goldbot.ops.account_view import LossBudget, Milestones, loss_budget, milestones
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
TICK_ERROR_NOTE_S = 15 * 60         # health warns this long after an error the engine carried on from
NEXT_OPEN_SEARCH = pd.Timedelta(days=8)

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
    "tick_error": _r("The engine hit an error reading prices",
                     "Usually clears at the next clean bar; if it repeats, restart the engine.", "wait"),
    "data_quality_error": _r("Market data failed a quality check",
                             "Usually clears by itself; check the Feeds tab if it lasts.", "wait"),
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
}
_ORDER = {code: i for i, code in enumerate(REASONS)}
API_ONLY = frozenset({"engine_not_reporting", "status_unknown"})       # raised here, not by the engine
# worded ahead of the lanes adding them (bridge check, playbook G-items); the engine publishes them in entry_blocks
FUTURE = frozenset({"bridge_incompatible"})
RESETS_DAILY = frozenset({"combined_daily_cap", "combined_daily_loss_abs", "daily_cap", "daily_loss_limit_abs"})
RESETS_WEEKLY = frozenset({"combined_weekly_cap", "weekly_cap"})
_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}


def _slug(title: str) -> str:
    """GitHub's heading anchor: lower case, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")


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


def _open_at(sessions: Any, *ts: pd.Timestamp) -> bool | None:
    """Whether the published table says the market is open at every one of `ts`; None when it cannot be read."""
    try:
        return bool(_table(sessions).is_open(pd.DatetimeIndex(list(ts))).all())
    except Exception:                       # noqa: BLE001
        log.exception("session table unreadable")
        return None


def block(code: str, *, text: str | None = None, agent: str | None = None, at: pd.Timestamp | None = None,
          at_label: Literal["opens", "until", "resets", "since"] | None = None, fix: str | None = None) -> EntryBlock:
    r = REASONS.get(code)
    if r is None:                           # a newer engine's reason: shown, never dropped
        r = Reason(text=f"New entries paused ({code.replace('_', ' ')})",
                   fix="See the Runbook; the Health tab may say more.", kind="act")
    body = text if text is not None else r.text.replace("{agent}", agent or "a strategy")
    return EntryBlock(code=code, scope="agent" if agent else "account", agent_id=agent, text=body,
                      fix=fix if fix is not None else r.fix, kind=r.kind, at=_dt(at) if at_label else None,
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


def _throttle_blocks(th: Any, now: pd.Timestamp) -> tuple[list[EntryBlock], int]:
    """The strategies the throttle holds back, and how many strategies it covers."""
    if not isinstance(th, dict) or not th.get("enabled") or not isinstance(th.get("agents"), dict):
        return [], 0
    out = []
    agents = th["agents"]
    for aid, v in agents.items():
        if not isinstance(v, dict) or not isinstance(v.get("blocked"), list):
            continue
        for code in [str(c) for c in v["blocked"] if CODE.fullmatch(str(c))]:
            if code == "throttle_loss_streak":
                n = _num(v.get("loss_streak"))
                text = f"{aid} paused after {int(n)} losses in a row" if n else None
                out.append(block(code, agent=str(aid), text=text, at=_ts(v.get("cooldown_until")), at_label="until"))
            elif code == "throttle_entry_rate":
                n = _num(th.get("max_entries_per_day_per_agent")) or _num(v.get("entries_today"))
                text = f"{aid} made its {int(n)} trades for today" if n else None
                out.append(block(code, agent=str(aid), text=text, at=_next_day(now), at_label="resets"))
            else:
                out.append(block(code, agent=str(aid)))
    return out, len(agents)


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


def _window_blocks(w: Any, sessions: Any, now: pd.Timestamp, limits: RiskLimits) -> list[EntryBlock]:
    """The engine's entry windows (`windows` in engine_<account>.json, BACKLOG 17/18), refused as the running
    RiskGate refuses them. Whether a window is on comes from the engine's own `enabled` alone (the running gate's
    setting; a window it has off is ignored whatever its flag, since its fail-closed path raises flags regardless);
    with no `enabled` published a raised flag is shown. The settings read here (`limits`) never hide a window: a
    settings edit without a restart, or settings that fail to load, would otherwise show OPEN while the gate refuses.
    Times are the engine's own (`tier2_until`, `reopen_until`, `holiday_until`, `holiday_name`); the limits are only a
    fallback for wording and times when it does not publish them. The engine's free-text
    `calendar_unavailable_reason` is only read to choose between two mapped texts, never shown."""
    if not isinstance(w, dict):
        return []
    enabled = w.get("enabled")

    def on(key: str) -> bool:
        return enabled.get(key) is True if isinstance(enabled, dict) else True
    tier2, reopen, holidays = on("tier2"), on("reopen"), on("holidays")
    out = []
    if (tier2 or holidays) and w.get("calendar_unavailable") is True:
        why = w.get("calendar_unavailable_reason")
        holiday_list = holidays and isinstance(why, str) and why.startswith("holiday list")
        out.append(block("calendar_unavailable",
                         text="Holiday list out of date: entries refused to be safe" if holiday_list else None,
                         fix=HOLIDAY_LIST_FIX if holiday_list else None,
                         at=_ts(w.get("calendar_unavailable_since")), at_label="since"))
    if tier2 and w.get("tier2_blackout") is True:
        until = _ts(w.get("tier2_until"))
        t2 = limits.tier2
        fix = None if until is not None or not t2.enabled else \
            f"{NOTHING}: entries resume {t2.after_min} min after the release (the pause starts {t2.before_min} min before it)."
        out.append(block("tier2_blackout", fix=fix, at=until, at_label="until"))
    if reopen and w.get("reopen_window") is True:
        until = _ts(w.get("reopen_until"))
        out.append(block("reopen_window", at=until if until is not None else _reopen_until(sessions, now, limits),
                         at_label="until"))
    if holidays and w.get("holiday_thin") is True:
        name = w.get("holiday_name") if isinstance(w.get("holiday_name"), str) else None
        until = _ts(w.get("holiday_until"))
        if name is None or until is None:
            fb_name, fb_until = _holiday(sessions, now, limits)
            name, until = name or fb_name, until if until is not None else fb_until
        name = name.strip()[:60] if name else None
        out.append(block("holiday_thin", text=f"Holiday trading ({name}): thin market, entries paused"
                         if name else None, at=until, at_label="until"))
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
                         budget: LossBudget | None, limits: RiskLimits, milestones: Milestones | None = None,
                         is_owner: bool = True, combined_dd: float | None = None) -> EntryStatus:
    """owner_halt: the channel that halted (None: not halted); supervisor_reasons: the supervisor's halt as the engine reads it
    (None: no halt; empty: a halt without reasons); drift_reasons: the drift watch's system halt (None: none);
    budget: the account's loss budget (account_view.loss_budget); milestones: its ladder (account_view.milestones);
    is_owner: the viewer may re-arm; combined_dd: the supervisor's combined drawdown halt (SupervisorLimits)."""
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
        if e.get("stage") == "halted":
            add(block("drawdown_halt", text=f"This account is {_pct(limits.dd_stage2)} below its high: trading stopped"))
        bc = e.get("bridge_compat")
        if isinstance(bc, dict) and bc.get("status") == "incompatible":     # published by the bridge-check lane
            add(block("bridge_incompatible"))
        sc = e.get("server_clock")
        if isinstance(sc, dict) and sc.get("status") == "mismatch":
            add(block("server_clock_mismatch"))
        if (_num(e.get("tick_ahead_s")) or 0.0) > limits.max_tick_ahead_s:
            add(block("tick_from_future"))
        tick_age = _num(e.get("last_tick_age_s"))
        if e.get("stale_bars") is True or (tick_age is not None and tick_age > limits.stale_tick_seconds):
            add(block("stale_data"))
        if e.get("account_class") == "unknown" and e.get("mode") != "paper":
            add(block("account_class_unknown"))
        if e.get("market_closed") is True:
            add(block("market_closed", at=next_open(sessions, now), at_label="opens"))
        bo = e.get("blackout")
        if isinstance(bo, dict):            # a blackout is on whatever its title (review L3)
            label = "News shock" if bo.get("kind") == "news_shock" else "News blackout"
            title = str(bo.get("title") or "").strip()[:80]
            add(block("news_blackout", text=f"{label}: {title}" if title else label))
        if e.get("tick_error") is True:
            add(block("tick_error"))
        if e.get("dq_error") is True:
            add(block("data_quality_error"))
        if e.get("data_recovering") is True:
            add(block("data_recovering"))
        spread = _num(e.get("spread_points"))
        if spread is not None and spread > limits.max_spread_points:
            add(block("spread_too_wide", text=f"Spread too wide right now ({spread:.0f} points; limit "
                                              f"{limits.max_spread_points:.0f})"))
        n_open = _num(e.get("open_positions"))
        if n_open is not None and n_open >= limits.max_positions:
            add(block("max_positions", text=f"Already {int(n_open)} trades open (limit {limits.max_positions})"))
        if milestones is not None and not milestones.tradeable and milestones.pending is None:
            # inferred from the ladder, not reported by the engine (BACKLOG 43): worded as likely
            add(block("min_lot_exceeds_risk", text="Likely: the smallest trade risks too much on every timeframe"))
        for b in _window_blocks(e.get("windows"), sessions, now, limits):
            add(b)
        published = e.get("entry_blocks")
        if isinstance(published, list):     # the engine's own reasons win; the mapping above is the fallback
            for code in published:
                if isinstance(code, str) and CODE.fullmatch(code):
                    add(block(code))
        last_err = _ts(e.get("last_tick_error_at"))
        if e.get("tick_error") is not True and last_err is not None \
                and 0 <= (now - last_err).total_seconds() <= TICK_ERROR_NOTE_S:
            n = int(_num(e.get("tick_errors_total")) or 0)
            mins = max(1, round((now - last_err).total_seconds() / 60))
            notes.append(f"Price error {mins} min ago; the engine carried on ({n} since it started).")
        if isinstance(bc, dict) and bc.get("status") == "ok" and bc.get("commit_differs") is True:
            notes.append("The MT5 box and the brain run different versions; entries are not affected.")

    agent_blocks, n_agents = ([], 0) if silent else _throttle_blocks(e.get("throttle"), now)
    for b in agent_blocks:
        add(b)
    found.sort(key=lambda b: (b.kind, _ORDER.get(b.code, len(_ORDER))))
    account = [b for b in found if b.scope == "account"]
    held = sorted({b.agent_id for b in found if b.agent_id})
    everyone = n_agents > 0 and len(held) >= n_agents
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
        state, headline = "partial", f"Can take new trades, except {', '.join(str(a) for a in held)}"
    else:
        state, headline = "open", "Can take new trades"
    return EntryStatus(state=state, headline=headline, blocks=found, notes=notes,
                       exits=EXITS_SILENT if silent and _open_at(sessions, now) is not False else EXITS_OK,
                       as_of=_dt(reported), clears_at=_dt(clears))


# ----------------------------------------------------------------------------- from the state directory
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


def entry_inputs(state_dir: str | Path, settings: Settings | None, *, is_owner: bool) -> dict[str, Any]:
    """What every account's entry status shares, for the dashboard and Telegram alike: the owner's halt
    (control.json), the supervisor's halt as the engines read it, the drift watch's system halt, the RiskGate's and the
    supervisor's limits from settings (the defaults when missing or unreadable) and whether the reader may re-arm."""
    from goldbot.telegram.bus import ApprovalBus  # lazily: the telegram package imports this module
    c = ApprovalBus(state_dir).control()
    halt, why = Supervisor.engine_should_halt(state_dir)
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
                        is_owner: bool = True) -> list[tuple[str, str, EntryStatus]]:
    """(account id, mode, entry status) for every engine that reports, sorted by account; built exactly as the
    dashboard's /api/accounts builds it (loss budget, ladder, shared inputs). A status that cannot be built is
    `unknown_status()`, never OPEN; a torn engine file is skipped, as the API skips it."""
    sd = Path(state_dir)
    engines: list[dict[str, Any]] = []
    for f in sorted(sd.glob("engine_*.json")):
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
        inputs: dict[str, Any] | None = entry_inputs(sd, settings, is_owner=is_owner)
    except Exception:                      # noqa: BLE001 - reporting only: every account shows status unknown
        log.exception("entry status inputs failed")
        inputs = None
    out = []
    for e in engines:
        status: EntryStatus | None = None
        if inputs is not None:
            try:
                status = account_entry_status(e, now=now, budget=_quiet(loss_budget, e, sup, engines),
                                              milestones=_quiet(milestones, e), **inputs)
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
