"""Engine and loop health checks: engine state, loop heartbeat, tick errors, failed orders."""
from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.data.timeutil import heartbeat_problem
from goldbot.execution.classifier import class_mismatch
from goldbot.execution.not_confirmed import AMBIGUOUS_ALERT_S, health_line
from goldbot.ops.checks._base import (
    ENGINE_FAIL_S,
    ENGINE_WARN_S,
    FAILED_ORDER_WINDOW_H,
    HEALTH_CLOCK_AHEAD_S,
    LOOP_FAIL_S,
    LOOP_STARTING_FAIL_S,
    LOOP_WARN_S,
    QUOTE_INVALID_FAIL_S,
    Check,
    Status,
    _age,
    _ahead_s,
    _epoch,
    _market_open_throughout,
    _read_json,
    _worst,
    log,
)
from goldbot.ops.checks.registry import BLOCK_ACCOUNT, register_check
from goldbot.ops.watchdog import loop_heartbeat_path
from goldbot.risk.supervisor import engine_fault

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext


@register_check("account", block=BLOCK_ACCOUNT, pos=10)
def check_engine(ctx: HealthContext, account_id: str) -> Check:
    name = f"engine:{account_id}"
    f = ctx.state_dir / f"engine_{account_id}.json"
    market = _market_open_throughout(ctx, ENGINE_FAIL_S)
    if not f.exists():
        return Check(name=name, status="fail" if market else "warn", reason=f"no engine_{account_id}.json (engine never ran)")
    try:
        e = _read_json(f)
    except (ValueError, OSError) as exc:
        return Check(name=name, status="fail", reason=f"engine state unreadable: {exc}")
    bad = engine_fault(e) if isinstance(e, dict) else "the file"
    if bad is not None:                      # the supervisor's rule: a null, string, bool, NaN or infinite number
        return Check(name=name, status="fail", reason=f"engine state unreadable: {bad} is not a finite number")
    parts: list[str] = []
    statuses: list[Status] = []
    age = _epoch(ctx.now) - float(e.get("ts", 0))
    ahead = _ahead_s(float(e.get("ts", 0)), ctx)
    if ahead > HEALTH_CLOCK_AHEAD_S:         # whatever the market: a future stamp proves nothing (found 2026-10-16)
        statuses.append("fail")
        parts.append(f"state stamped {_age(ahead)} ahead of this server's clock (clock fault or bad write): it cannot "
                     "show the engine runs, and the supervisor halts entries on every account (engine_heartbeat_future)")
    elif not market:
        parts.append(f"market closed, state {_age(age)} old")
    elif age > ENGINE_FAIL_S:
        statuses.append("fail")
        parts.append(f"state {_age(age)} old (> {ENGINE_FAIL_S // 60} min while the market is open)")
    elif age > ENGINE_WARN_S:
        statuses.append("warn")
        parts.append(f"state {_age(age)} old")
    else:
        parts.append(f"state {_age(age)} old")
    if e.get("startup_error"):              # the engine refused to start (review LOW-A): name the cause
        statuses.append("fail")
        parts.append(f"engine refused to start: {e['startup_error']}")
    stage = str(e.get("stage", "unknown"))
    if stage == "halted":
        statuses.append("fail")
        parts.append("stage HALTED (drawdown stage 2): no entries until re-armed")
        if e.get("rearm_refused"):
            parts.append(f"last re-arm refused: {e['rearm_refused']}")
    elif stage == "size_down":
        statuses.append("warn")
        parts.append("stage size_down")
    else:
        parts.append(f"stage {stage}")
    stale_tick = ctx.settings.risk.stale_tick_seconds if ctx.settings is not None else 20
    tick_age = float(e.get("last_tick_age_s") or 0.0)
    if market and tick_age > stale_tick:
        statuses.append("warn")
        parts.append(f"last tick {_age(tick_age)} old (> {stale_tick} s)")
    if market and e.get("stale_bars"):
        statuses.append("warn")
        parts.append("last bar older than one decision period: entries blocked")
    # broker reads failing (MT5 positions_get / history_deals_get returning None): a warning from the first failure,
    # a FAIL (alerted on Telegram) after risk.positions_unreadable_alert in a row
    unreadable = int(e.get("positions_unreadable") or 0)
    alert_after = ctx.settings.risk.positions_unreadable_alert if ctx.settings is not None else 5
    if unreadable:
        statuses.append("fail" if unreadable >= alert_after else "warn")
        parts.append(f"broker positions/deals unreadable {unreadable} time(s) in a row (terminal fault?): entries "
                     f"blocked, exits retried every tick, closes not confirmed until it reads again")
    other_dq = [c for c in e.get("dq_checks") or [] if c != "positions_unreadable"]
    if e.get("dq_error") and (other_dq or not unreadable):
        statuses.append("fail")
        checks = ", ".join(other_dq) or "stale feed"
        parts.append(f"data-quality error ({checks}): entries blocked")
    for w in e.get("dq_warnings") or []:
        statuses.append("warn")
        parts.append(f"data-quality warning: {w}")
    # design (D10): bars close by clock at close + grace; a broker clock off the wall clock by more than the grace
    # closes bars before their last ticks (dropped from the live bars as late ticks). A warning only: no entry block
    skew, grace = e.get("clock_skew_s"), float(e.get("bar_close_grace_s") or 1.5)
    if market and skew is not None and abs(float(skew)) > grace:
        statuses.append("warn")
        parts.append(f"broker clock skew {float(skew):+.1f} s vs the wall clock exceeds the {grace:g} s bar-close grace: "
                     f"bars may close before their last ticks ({int(e.get('late_ticks') or 0)} late tick(s) so far)")
    since = e.get("quote_invalid_since")
    if since and _market_open_throughout(ctx, QUOTE_INVALID_FAIL_S):
        invalid_s = (ctx.now - pd.Timestamp(since)).total_seconds()
        if invalid_s >= QUOTE_INVALID_FAIL_S:
            statuses.append("fail")
            parts.append(f"quotes invalid for {_age(invalid_s)} (NaN, crossed or unreadable; > {QUOTE_INVALID_FAIL_S} s): "
                         "entries blocked; decided closes, the kill switch, time exits and the Friday flatten still run "
                         "on the host clock, stops rest with the broker")
    if e.get("tick_error"):
        statuses.append("fail")
        parts.append(f"tick error ({e.get('tick_error_detail') or 'unknown'}): the engine logged it and carries on; "
                     f"entries blocked until a decision bar closes clean, exits unaffected (details in the engine log)")
    lost = e.get("closed_records_lost") or []
    if lost:
        statuses.append("fail")
        parts.append(f"closed-trade record LOST for position(s) {', '.join(str(x) for x in lost)} after repeated write "
                     f"failures: the phase gates and stop rule undercount (details in the engine log)")
    # BACKLOG 72: an order the broker neither filled nor refused, unresolved for 10 minutes or more (the engine keeps
    # looking and refuses entries meanwhile): a warning, so the owner checks MT5 for an open trade and its stop
    uo = e.get("unconfirmed_orders")
    if isinstance(uo, dict) and int(uo.get("count") or 0) > 0 and float(uo.get("oldest_age_s") or 0.0) >= AMBIGUOUS_ALERT_S:
        statuses.append("warn")
        parts.append(health_line(int(uo["count"]), _age(float(uo["oldest_age_s"])), str(uo.get("symbol") or "XAUUSD")))
    cls = str(e.get("account_class") or "unknown")
    if e.get("mode", "paper") != "paper":
        if cls == "unknown":
            statuses.append("warn")
            parts.append("account class unknown: entries blocked until the nightly classifier reads raw or standard")
        elif cls == "standard":
            parts.append("account class standard: 15m families off except session_open, edge must exceed 1.5x cost")
        declared, measured = e.get("account_class_declared"), e.get("account_class_measured")
        if class_mismatch(declared, measured):
            statuses.append("warn")
            parts.append(f"declared {declared}, measured {measured}: check the account type "
                         f"(the stricter class is in force)")
    b = e.get("blackout")
    if isinstance(b, dict) and b:
        parts.append(f"news blackout: {b.get('title', '?')}")
    return Check(name=name, status=_worst(statuses), reason="; ".join(parts))


TICK_ERROR_RECENT_S = 15 * 60   # a tick error this recent is reported even after it cleared


@register_check("account", block=BLOCK_ACCOUNT, pos=30)
def check_engine_loop(ctx: HealthContext, account_id: str) -> Check:
    """The engine loop turns (state/heartbeat_engine-<account>.json, `goldbot.ops.watchdog.LoopWatch`). Judged
    whatever the market: the loop runs and beats with no ticks at all (weekend, daily break, dead feed), so a stale
    beat is a stuck loop or a dead process, and a stuck loop also stops the exits this engine manages. WARN when the
    file is missing (an engine that never ran, or a release older than the loop heartbeat)."""
    name = f"engine_loop:{account_id}"
    f = loop_heartbeat_path(ctx.state_dir, account_id)
    if not f.exists():
        return Check(name=name, status="warn",
                     reason=f"no loop heartbeat: engine {account_id} never ran (or runs a release that predates it)")
    try:
        hb = _read_json(f)
        ts, started = float(hb["ts"]), float(hb.get("started") or hb["ts"])
        phase = str(hb.get("phase", "loop"))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return Check(name=name, status="fail", reason=f"loop heartbeat unreadable: {exc}")
    now = _epoch(ctx.now)
    if heartbeat_problem(ts, now, math.inf, ahead_s=math.inf) == "invalid":   # float() read NaN or infinity
        return Check(name=name, status="fail", reason=f"loop heartbeat unreadable: ts {ts!r} is not a finite number")
    if (ahead := _ahead_s(ts, ctx)) > HEALTH_CLOCK_AHEAD_S:
        return Check(name=name, status="fail",
                     reason=f"loop heartbeat stamped {_age(ahead)} ahead of this server's clock (clock fault or bad "
                            "write): it cannot show the loop turns; check the host clock and restart the engine")
    age = now - ts
    if age > LOOP_FAIL_S:
        what = "start" if phase == "starting" else "loop"
        return Check(name=name, status="fail",
                     reason=f"engine {what} silent for {_age(age)} (> {LOOP_FAIL_S} s): stuck or not running; exits "
                            f"this engine manages are not running (stops rest with the broker), the systemd watchdog "
                            f"restarts a stuck loop after 300 s")
    if phase == "starting":
        up = now - started
        if up > LOOP_STARTING_FAIL_S:
            return Check(name=name, status="fail",
                         reason=f"engine still starting after {_age(up)} (> {LOOP_STARTING_FAIL_S // 60} min)")
        return Check(name=name, status="warn", reason=f"engine starting for {_age(up)} (reconcile, warm start)")
    if age > LOOP_WARN_S:
        return Check(name=name, status="warn", reason=f"last loop pass {_age(age)} ago (> {LOOP_WARN_S} s)")
    return Check(name=name, status="ok", reason=f"loop turning, last pass {_age(age)} ago")


@register_check("account", block=BLOCK_ACCOUNT, pos=40)
def check_tick_errors(ctx: HealthContext, account_id: str) -> Check | None:
    """Tick errors the engine logged and carried on from (`tick_errors_total`, `last_tick_error_at`): a warning,
    announced on Telegram, when one happened in the last 15 minutes or since the previous health run, so a burst that
    cleared between two runs is still seen. None when the engine state has no error on record."""
    try:
        e = _read_json(ctx.state_dir / f"engine_{account_id}.json")
        last = pd.Timestamp(e["last_tick_error_at"]) if e.get("last_tick_error_at") else None
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        return None
    if last is None:
        return None
    last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
    recent = (ctx.now - last).total_seconds() <= TICK_ERROR_RECENT_S
    new = ctx.last_run is not None and last > ctx.last_run
    if not (recent or new):
        return Check(name=f"tick_errors:{account_id}", status="ok",
                     reason=f"{int(e.get('tick_errors_total') or 0)} tick error(s) since start, last {_age((ctx.now - last).total_seconds())} ago")
    return Check(name=f"tick_errors:{account_id}", status="warn",
                 reason=f"tick error ({e.get('tick_error_detail') or 'cleared'}) {_age((ctx.now - last).total_seconds())} ago, "
                        f"{int(e.get('tick_errors_total') or 0)} since the engine started: the engine carried on, entries "
                        f"were blocked until a clean decision bar; read the engine log")


def _failed_order_retcodes(ctx: HealthContext, account_id: str, since: pd.Timestamp) -> list[tuple[pd.Timestamp, str, Any]]:
    """(ts, agent, retcode) of failed `order` rows in the engine's decisions journal; [] when it cannot be read."""
    if ctx.settings is None:
        return []
    from goldbot.data.store import Store
    try:
        d = Store(ctx.settings.data_root).read("decisions", source=account_id, symbol=ctx.settings.symbol, start=since, end=ctx.now)
        if d.empty:
            return []
        d = d[d["action"] == "order"]
        out = []
        for _, row in d.iterrows():
            detail = json.loads(row["detail"]) if isinstance(row["detail"], str) else {}
            if detail.get("ok") is False:
                out.append((pd.Timestamp(row["ts_utc"]), str(row["agent_id"]), detail.get("retcode")))
        return out
    except Exception:     # the journal is best effort here: the orders file alone raises the alert
        log.exception("decisions journal")
        return []


@register_check("account", block=BLOCK_ACCOUNT, pos=60, guard="order_failed")
def check_failed_orders(ctx: HealthContext, account_id: str) -> list[Check]:
    """X6: an order the engine sent that failed after the adapter's retries (pending_orders row `rejected`) or that a
    restart found never filled (`unfilled`), in the last FAILED_ORDER_WINDOW_H. One check per order so each failure is
    announced once (NOTIFY_ON_WARN), with account, side, lots and the retcode from the decisions journal."""
    f = ctx.state_dir / f"orders_{account_id}.json"
    if not f.exists():
        return []
    try:
        sent = _read_json(f).get("sent", {})
        rows = [(cid, r) for cid, r in sent.items() if r.get("status") in ("rejected", "unfilled")]
    except (ValueError, OSError, AttributeError) as exc:
        return [Check(name=f"orders:{account_id}", status="fail", reason=f"orders_{account_id}.json unreadable (the engine refuses to start): {exc}")]
    since = ctx.now - pd.Timedelta(hours=FAILED_ORDER_WINDOW_H)
    recent = [(cid, r, pd.Timestamp(r["ts_utc"])) for cid, r in rows if r.get("ts_utc") and pd.Timestamp(r["ts_utc"]) >= since]
    if not recent:
        return []
    journal = _failed_order_retcodes(ctx, account_id, since)
    out = []
    for cid, r, ts in sorted(recent, key=lambda x: x[2]):
        rc = next((code for jts, agent, code in journal
                   if agent == r.get("agent_id") and abs((jts - ts).total_seconds()) <= 120), None)
        side = "BUY" if int(r.get("side", 0)) > 0 else "SELL"
        what = "FAILED_EXEC" if r["status"] == "rejected" else "never filled (found on restart)"
        out.append(Check(name=f"order_failed:{account_id}:{cid}", status="warn",
                         reason=f"{what} at {ts:%Y-%m-%d %H:%M} UTC: account {account_id}, {side} {float(r.get('lots', 0)):g} lots, "
                                f"agent {r.get('agent_id')}, retcode {rc if rc is not None else '?'}"))
    return out
