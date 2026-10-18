"""`GET /api/limits` and `GET /api/limits/changes`: the Limits card (docs/ux/limits-and-tradingview.md, card A).

"See, without acting, what the risk limits are now and why they last changed." Read-only: the current values are what
each engine publishes in state/engine_<account>.json (`limits` = the RiskGate's limits after ADR 0032's clamp,
`limits_current` = their profile and reason, `limits_reverted`, the day/week start equity, `daily_loss_limit` = the
gate's min(daily cap x day-start equity, absolute limit), `lot_terms`), the supervisor's combined caps from
state/supervisor.json, the revert's time and cause from core.db `limits_state`, and the change log from
goldbot/ops/limits_log.py. Nothing is recomputed from config: what is shown is what the gate enforces. No login,
server, evidence id or balance history leaves here. A bad state file shows as an error on its card, never a 500.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from goldbot.api.schema import LimitAccount, LimitChange, LimitsSupervisorView, LimitsView
from goldbot.ops import limits_log
from goldbot.ops.limits_log import LimitChangeEntry, LimitsLogError
from goldbot.risk.limits_profile import DEFAULT_PROFILE, SUPERVISOR_DEMO_ENVELOPE

if TYPE_CHECKING:
    from goldbot.config import Settings

log = logging.getLogger(__name__)

STALE_S = 60.0                     # the card greys out an engine file older than this (spec: "Stale (>60 s)")
MAX_CHANGES = 100
# settings.yaml risk.supervisor_daily_cap / supervisor_weekly_cap defaults, when settings cannot be read
SUPERVISOR_DEFAULTS = (0.015, 0.04)


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def _pos(x: Any) -> float | None:
    v = _num(x)
    return v if v is not None and v > 0 else None


def change_view(e: LimitChangeEntry) -> LimitChange:
    return LimitChange(account_id=e.account_id, ts=e.ts_utc.to_pydatetime(), field=e.field, old_value=e.old,
                       new_value=e.new, source=e.source, reason=e.reason, adr=e.adr)


def changes_view(state_dir: Path, account: str | None, limit: int) -> list[LimitChange]:
    """`/api/limits/changes`: newest first, `limit` clamped to 1..MAX_CHANGES; [] when core.db cannot be read (the
    Limits card reports that error)."""
    try:
        rows = limits_log.changes(state_dir, account or None, min(max(limit, 1), MAX_CHANGES))
    except LimitsLogError as exc:
        log.warning("limit changes unreadable: %s", exc)
        return []
    return [change_view(e) for e in rows]


def _mode(e: dict[str, Any]) -> Literal["demo", "live", "paper"]:
    m = e.get("mode")
    return m if m in ("demo", "live") else "paper"


def _account(e: dict[str, Any], now: pd.Timestamp, state_dir: Path, last: LimitChange | None,
             errors: list[str]) -> LimitAccount:
    acct = str(e.get("account", "?"))
    lim = e.get("limits") if isinstance(e.get("limits"), dict) else None
    gate: dict[str, Any] = lim or {}
    cur = e.get("limits_current") if isinstance(e.get("limits_current"), dict) else None
    reverted = e.get("limits_reverted") is True
    ts = _num(e.get("ts"))
    updated = datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else None
    eq, day0, week0 = _num(e.get("equity")), _pos(e.get("day_start_equity")), _pos(e.get("week_start_equity"))
    reason = str(cur.get("reason", "default")) if cur else "default"
    source, adr = limits_log.current_source(reason)
    if reverted:
        source, adr = "revert", "0032"
    terms: dict[str, Any] = e["lot_terms"] if isinstance(e.get("lot_terms"), dict) else {}
    since, why = None, None
    if reverted:
        try:
            st = limits_log.revert_state(state_dir, acct)
        except LimitsLogError as exc:
            errors.append(str(exc))
            st = None
        if st is not None and st.reverted:
            since = st.since_utc.to_pydatetime() if st.since_utc is not None else None
            why = limits_log.revert_reason(st.reason)
    daily = _pos(gate.get("daily_cap"))
    weekly = _pos(gate.get("weekly_cap"))
    daily_limit = _pos(e.get("daily_loss_limit"))
    if daily_limit is None and daily is not None and day0 is not None:
        daily_limit = daily * day0
    weekly_limit = weekly * week0 if weekly is not None and week0 is not None else None
    daily_used = max(0.0, day0 - eq) if day0 is not None and eq is not None else None
    weekly_used = max(0.0, week0 - eq) if week0 is not None and eq is not None else None
    hit: Literal["none", "daily", "weekly"] = "none"
    if daily_limit is not None and daily_used is not None and daily_used >= daily_limit:
        hit = "daily"
    elif weekly_limit is not None and weekly_used is not None and weekly_used >= weekly_limit:
        hit = "weekly"
    error = None if lim is not None else "the engine does not publish its limits yet"
    return LimitAccount(
        account_id=acct, mode=_mode(e), profile=str(cur.get("profile") or DEFAULT_PROFILE) if cur else DEFAULT_PROFILE,
        source=source, adr=adr, updated_utc=updated,
        stale=ts is None or now.timestamp() - ts > STALE_S,
        currency=e.get("currency") if isinstance(e.get("currency"), str) else None, balance=_num(e.get("balance")),
        day_start_equity=day0, week_start_equity=week0, daily_cap_pct=daily, daily_limit=daily_limit if lim else None, daily_used=daily_used,
        daily_resets_utc=limits_log.next_day_reset(now).to_pydatetime(),
        absolute_daily_limit=_pos(gate.get("daily_loss_limit_abs")),
        weekly_cap_pct=weekly, weekly_limit=weekly_limit, weekly_used=weekly_used,
        weekly_resets_utc=limits_log.next_week_reset(now).to_pydatetime(),
        min_lot=_pos(terms.get("volume_min")), min_lot_risk=_pos(gate.get("min_lot_risk_account")),
        min_lot_risk_cap=_pos(gate.get("min_lot_risk_cap")),
        revert_active=reverted, revert_since=since, revert_reason=why, cap_hit=hit if lim else "none",
        last_change=last, error=error)


def limits_view(state_dir: Path, engines: list[dict[str, Any]], supervisor: dict[str, Any],
                settings: Settings | None, now: pd.Timestamp | None = None) -> LimitsView:
    now = (now if now is not None else pd.Timestamp.now("UTC")).floor("us")
    errors: list[str] = []
    try:
        entries = limits_log.changes(state_dir, None, 10_000)
    except LimitsLogError as exc:
        errors.append(str(exc))
        entries = []
    last: dict[str, LimitChange] = {}
    for c in entries:                                  # newest first: the first per account is its last change
        last.setdefault(c.account_id, change_view(c))
    accounts = [_account(e, now, state_dir, last.get(str(e.get("account"))), errors)
                for e in sorted(engines, key=lambda e: str(e.get("account", "")))]
    d_def, w_def = (settings.risk.supervisor_daily_cap, settings.risk.supervisor_weekly_cap) if settings \
        else SUPERVISOR_DEFAULTS
    sup_ts = _num(supervisor.get("ts"))
    fresh = sup_ts is not None and abs(now.timestamp() - sup_ts) <= STALE_S     # a silent supervisor shows no caps
    sup = LimitsSupervisorView(daily_cap=_pos(supervisor.get("daily_cap")) if fresh else None,
                               weekly_cap=_pos(supervisor.get("weekly_cap")) if fresh else None,
                               default_daily=d_def, default_weekly=w_def, envelope_daily=SUPERVISOR_DEMO_ENVELOPE[0],
                               envelope_weekly=SUPERVISOR_DEMO_ENVELOPE[1])
    return LimitsView(generated_utc=now.to_pydatetime(), accounts=accounts, supervisor=sup,
                      error="; ".join(dict.fromkeys(errors)) or None)
