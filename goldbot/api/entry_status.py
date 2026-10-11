"""Entry status (docs/ux/entry-status.md): per account, can it take a new trade right now; if not, why and what fixes
it, in the owner's words. Reporting only: nothing here gates an order (the RiskGate does, in the engine).

Built from what the engine publishes in state/engine_<account>.json (the RiskGate's account-level inputs: drawdown
stage, data flags, market_closed, server_clock, tick_error, throttle, bridge_compat, blackout and, from engines that
publish it, `entry_blocks`), the supervisor's halt as the engine reads it (`Supervisor.engine_should_halt`), the owner's
halt (control.json), the drift watch's system halt and the loss budget (goldbot/ops/account_view.py). Thresholds come
from the RiskGate's own limits (`RiskLimits.from_settings`), never a copy, so the browser holds no policy at all.

Reasons the RiskGate only knows per trade (negative EV, fx conversion, heat cap) are listed when the engine publishes
them in `entry_blocks`; rollover and the Friday weekend cut are not published yet (the engine should publish
`entry_blocks`, ADR 0010). A reason with no entry in `REASONS` renders generically, never as nothing.

Nothing free-text from the engine reaches the owner (probe reasons name zones and commits, supervisor reasons name
files): only mapped codes, numbers, agent ids and the news title the dashboard already shows.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel

from goldbot.api.schema import EntryBlock, EntryStatus
from goldbot.data.calendar import DEFAULT_SESSIONS, session_table_from_cache
from goldbot.data.timeutil import floor_tf
from goldbot.ops.account_view import LossBudget
from goldbot.risk.gate import RiskLimits

log = logging.getLogger("goldbot.api.entry_status")

RUNBOOK_URL = "https://github.com/engraamirkhan/goldbot/blob/main/docs/RUNBOOK.md"
ENGINE_SILENT_S = 60.0              # the supervisor's heartbeat rule (SupervisorLimits.heartbeat_max_age_s)
TICK_ERROR_NOTE_S = 15 * 60         # health warns this long after an error the engine carried on from
NEXT_OPEN_SEARCH = pd.Timedelta(days=8)

GENERIC = "Engines are running but block every entry"
CLOCK = "Server clock (US-EU DST gap weeks)"
HALT = "Halt and re-arm"
UPDATE = "Updating the VPS to a new version"
HEALTH = "Health check"

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
    "engine_not_reporting": _r("Engine not reporting", "Check the engine service is running and restart it if not.",
                               "act", HEALTH),
    "owner_halt": _r("New entries halted by you or an approver",
                     "Re-arm on this page when ready (owner and authenticator code).", "act", HALT),
    "supervisor_halt": _r("The safety supervisor stopped new entries",
                          "Check the supervisor service and the Health tab.", "act"),
    "no_supervisor_heartbeat": _r("Safety supervisor is not running", "Start the supervisor service.", "act"),
    "supervisor_heartbeat_stale": _r("Safety supervisor stopped reporting", "Restart the supervisor service.", "act"),
    "supervisor_state_unreadable": _r("Safety supervisor's state can't be read",
                                      "Restart the supervisor service; it rewrites the file.", "act"),
    "engine_state_unreadable": _r("The supervisor can't read an engine's state file",
                                  "Restart the engine that wrote it.", "act"),
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
    "throttle_unknown": _r("{agent}: recent losses couldn't be checked",
                           "Usually clears at the next decision; check the Health tab if it lasts.", "act", HEALTH),
    "market_closed": _r("Market closed", f"{NOTHING}: it reopens by itself.", "wait"),
    "weekend": _r("Weekend: no new trades from Friday evening", f"{NOTHING}: entries resume when the market reopens.",
                  "wait"),
    "holiday_thin": _r("Holiday trading: thin market, entries paused", f"{NOTHING}: entries resume on a normal day.",
                       "wait"),
    "reopen_window": _r("Market just reopened: waiting for prices to settle", f"{NOTHING}.", "wait"),
    "rollover": _r("Daily rollover around broker midnight", f"{NOTHING}: entries resume in a few minutes.", "wait"),
    "news_blackout": _r("News blackout", f"{NOTHING}: entries resume after the event.", "wait"),
    "tier2_blackout": _r("Second-tier news blackout", f"{NOTHING}: entries resume after the release.", "wait"),
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
    "data_recovering": _r("Prices just recovered: waiting a minute to be sure", f"{NOTHING}.", "wait"),
    "spread_too_wide": _r("Spread too wide right now", f"{NOTHING}: it narrows by itself (news or rollover).", "wait"),
    "max_positions": _r("Already the maximum number of open trades",
                        f"{NOTHING}: a new entry is possible once one closes.", "wait"),
    "heat_cap": _r("Too much risk already open", f"{NOTHING}: a new entry is possible once open risk falls.", "wait"),
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


def next_open(sessions: Any, now: pd.Timestamp) -> pd.Timestamp | None:
    """The first minute the engine's session table (as it publishes it) says the market is open; None if not found."""
    try:
        table = session_table_from_cache(sessions) if isinstance(sessions, dict) else None
        table = table or DEFAULT_SESSIONS
        grid = pd.date_range(now.ceil("min"), now + NEXT_OPEN_SEARCH, freq="min")
        is_open = table.is_open(grid)
        hits = is_open.nonzero()[0]
        return pd.Timestamp(grid[hits[0]]) if len(hits) else None
    except Exception:                       # noqa: BLE001 - reporting only: no reopen time is shown
        log.exception("next market open not computed")
        return None


def block(code: str, *, text: str | None = None, agent: str | None = None, at: pd.Timestamp | None = None,
          at_label: Literal["opens", "until", "resets", "since"] | None = None) -> EntryBlock:
    r = REASONS.get(code)
    if r is None:                           # a newer engine's reason: shown, never dropped
        r = Reason(text=f"New entries paused ({code.replace('_', ' ')})",
                   fix="See the Runbook; the Health tab may say more.", kind="act")
    body = text if text is not None else r.text.replace("{agent}", agent or "a strategy")
    return EntryBlock(code=code, scope="agent" if agent else "account", agent_id=agent, text=body, fix=r.fix,
                      kind=r.kind, at=_dt(at) if at_label else None, at_label=at_label if at is not None else None,
                      runbook=r.runbook, runbook_url=runbook_url(r.runbook))


def _budget_block(budget: LossBudget, currency: Any, now: pd.Timestamp) -> EntryBlock:
    week = budget.period == "this week"
    amount = money(budget.limit, currency)
    if budget.binding.startswith("combined"):
        code = "combined_weekly_cap" if week else "combined_daily_cap"
        text = f"All accounts together reached {'this week' if week else 'today'}'s {amount} loss limit"
    else:
        code = "weekly_cap" if week else ("daily_loss_limit_abs" if budget.binding.startswith("absolute") else "daily_cap")
        text = f"{'This week' if week else 'Today'}'s {amount} loss limit reached"
    return block(code, text=text, at=_next_week(now) if week else _next_day(now), at_label="resets")


def _throttle_blocks(th: Any, now: pd.Timestamp) -> tuple[list[EntryBlock], int]:
    """The strategies the throttle holds back, and how many strategies it covers."""
    if not isinstance(th, dict) or not th.get("enabled") or not isinstance(th.get("agents"), dict):
        return [], 0
    out = []
    agents = th["agents"]
    for aid, v in agents.items():
        if not isinstance(v, dict) or not isinstance(v.get("blocked"), list):
            continue
        for code in [str(c) for c in v["blocked"]]:
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


def account_entry_status(e: dict[str, Any], *, now: pd.Timestamp, owner_halt: str | None,
                         supervisor_reasons: list[str] | None, drift_reasons: list[str] | None,
                         budget: LossBudget | None, limits: RiskLimits) -> EntryStatus:
    """owner_halt: who halted (None: not halted); supervisor_reasons: the supervisor's halt as the engine reads it
    (None: no halt; empty: a halt without reasons); drift_reasons: the drift watch's system halt (None: none);
    budget: the account's loss budget (account_view.loss_budget)."""
    now = pd.Timestamp(now).tz_convert("UTC")
    found: list[EntryBlock] = []
    notes: list[str] = []

    def add(b: EntryBlock) -> None:
        if not any(x.code == b.code and x.agent_id == b.agent_id for x in found):
            found.append(b)

    ts = _num(e.get("ts"))
    reported = pd.Timestamp(ts, unit="s", tz="UTC") if ts is not None else None
    silent = reported is None or (now - reported).total_seconds() > ENGINE_SILENT_S

    if owner_halt is not None:
        add(block("owner_halt"))
    if supervisor_reasons is not None:
        for r in supervisor_reasons or ["supervisor_halt"]:
            add(block(r))
    if drift_reasons is not None:
        add(block("drift_system_halt"))
    if silent:
        add(block("engine_not_reporting", at=reported, at_label="since"))
    else:
        if budget is not None and budget.remaining <= 0:
            add(_budget_block(budget, e.get("currency"), now))
        if e.get("stage") == "halted":
            add(block("drawdown_halt"))
        bc = e.get("bridge_compat")
        if isinstance(bc, dict) and bc.get("status") == "incompatible":
            add(block("bridge_incompatible"))
        sc = e.get("server_clock")
        if isinstance(sc, dict) and sc.get("status") == "mismatch":
            add(block("server_clock_mismatch"))
        if (_num(e.get("tick_ahead_s")) or 0.0) > limits.max_tick_ahead_s:
            add(block("tick_from_future"))
        age = _num(e.get("last_tick_age_s"))
        if e.get("stale_bars") is True or (age is not None and age > limits.stale_tick_seconds):
            add(block("stale_data"))
        if e.get("account_class") == "unknown" and e.get("mode") != "paper":
            add(block("account_class_unknown"))
        if e.get("market_closed") is True:
            opens = next_open(e.get("sessions"), now)
            add(block("market_closed", at=opens, at_label="opens"))
        bo = e.get("blackout")
        if isinstance(bo, dict) and bo.get("title"):
            shock = bo.get("kind") == "news_shock"
            add(block("news_blackout", text=f"{'News shock' if shock else 'News blackout'}: {str(bo['title'])[:80]}"))
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
        published = e.get("entry_blocks")
        if isinstance(published, list):
            for code in published:
                if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,64}", code):
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
    if stopping:
        state: Literal["open", "partial", "waiting", "blocked"] = \
            "blocked" if any(b.kind == "act" for b in stopping) else "waiting"
        more = f" (+{len(found) - 1} more)" if len(found) > 1 else ""
        headline = f"No new trades{'' if state == 'blocked' else ' for now'}: {stopping[0].text}{more}"
    elif held:
        state, headline = "partial", f"Can take new trades, except {', '.join(str(a) for a in held)}"
    else:
        state, headline = "open", "Can take new trades"
    return EntryStatus(state=state, headline=headline, blocks=found, notes=notes,
                       exits=EXITS_SILENT if silent else EXITS_OK, as_of=_dt(reported))
