"""Risk and limits health checks: supervisor, owner halt, risk state, phase and gates, loss caps, throttle, open risk, entry windows."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.data.timeutil import heartbeat_problem
from goldbot.ops.checks._base import (
    COSTS_WARN_DAYS,
    ENGINE_FAIL_S,
    HEALTH_CLOCK_AHEAD_S,
    HWM_PENDING_WARN_S,
    REARM_APPLY_S,
    SUPERVISOR_FAIL_S,
    SUPERVISOR_WARN_S,
    Check,
    Status,
    _age,
    _ahead_s,
    _epoch,
    _market_open_throughout,
    _read_json,
    _skipped,
    _worst,
)
from goldbot.ops.checks.registry import BLOCK_ACCOUNT, BLOCK_CORE, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext


@register_check("global", block=BLOCK_CORE, pos=10)
def check_supervisor(ctx: HealthContext) -> Check:
    f = ctx.state_dir / "supervisor.json"
    if not f.exists():
        return Check(name="supervisor", status="fail", reason="no supervisor.json: supervisor not running, engines block entries")
    try:
        s = _read_json(f)
    except (ValueError, OSError) as exc:
        return Check(name="supervisor", status="fail", reason=f"supervisor.json unreadable: {exc}")
    if not isinstance(s, dict):
        return Check(name="supervisor", status="fail", reason="supervisor.json unreadable: not an object; engines block entries")
    now = _epoch(ctx.now)
    if heartbeat_problem(s.get("ts", 0), now, math.inf, ahead_s=math.inf) == "invalid":
        return Check(name="supervisor", status="fail",
                     reason=f"supervisor.json heartbeat is not a number ({s.get('ts')!r}): engines block entries")
    ahead = _ahead_s(float(s.get("ts", 0)), ctx)
    if ahead > HEALTH_CLOCK_AHEAD_S:
        return Check(name="supervisor", status="fail",
                     reason=f"heartbeat stamped {_age(ahead)} ahead of this server's clock (clock fault, "
                            "bad write or restored backup): engines block entries until the supervisor rewrites it")
    age = now - float(s.get("ts", 0))
    if age > SUPERVISOR_FAIL_S:
        return Check(name="supervisor", status="fail",
                     reason=f"heartbeat {_age(age)} old (> {SUPERVISOR_FAIL_S} s): engines block entries")
    if "mixed_account_currencies" in (s.get("reasons") or []):
        # BACKLOG 28: equities in different currencies are never summed, so every engine's entries are halted; name
        # the files so the owner can stop the wrong engine or delete a leftover file (RUNBOOK: currency rollout)
        cur, files = s.get("currencies") or {}, s.get("currency_files") or {}
        named = ", ".join(f"{files.get(a) or f'engine_{a}.json'} ({c or 'no currency'})" for a, c in sorted(cur.items()))
        return Check(name="supervisor", status="fail",
                     reason=f"HALT mixed_account_currencies: {named}. Entries are blocked on every account until only "
                            "one currency runs (stop the other engine or delete its leftover engine_*.json)")
    # stale engine files in another (or no) currency: their money is not summed, their open exposure is (BACKLOG 28)
    out = s.get("money_excluded") or []
    note = (f"; {', '.join(out)}: stale, other or no currency, money not summed (exposure still counted; delete the "
            "file if that engine no longer runs)") if out else ""
    reasons = [str(r) for r in s.get("reasons") or []]
    # a halt that is a fault, not a cap: FAIL, so the Telegram health pass announces it (trading-safety review MEDIUM-1)
    if "supervisor_error" in reasons:
        return Check(name="supervisor", status="fail",
                     reason=f"HALT supervisor_error: the supervisor's evaluation raised {s.get('error') or 'an error'}; "
                            "entries are blocked on every account until it evaluates cleanly (supervisor log)")
    if "engine_state_unreadable" in reasons:
        files = ", ".join(str(f) for f in s.get("unreadable_engines") or []) or "an engine file"
        return Check(name="supervisor", status="fail",
                     reason=f"HALT engine_state_unreadable: {files} cannot be read or holds a non-number; entries are "
                            "blocked on every account until the engine rewrites it (delete it if that engine no longer runs)")
    if "engine_heartbeat_future" in reasons:
        files = ", ".join(str(f) for f in s.get("future_engines") or []) or "an engine file"
        return Check(name="supervisor", status="fail",
                     reason=f"HALT engine_heartbeat_future: {files} is stamped ahead of the server clock (clock fault or "
                            "bad write); entries are blocked on every account until that engine rewrites it with a sane "
                            "clock (move the file aside if that engine no longer runs)")
    if s.get("halt"):
        label = "combined-cap HALT" if any(r.startswith("combined_") for r in reasons) else "HALT"
        return Check(name="supervisor", status="warn",
                     reason=f"{label}: {', '.join(reasons)}; heartbeat {_age(age)}{note}")
    if out:
        return Check(name="supervisor", status="warn", reason=f"heartbeat {_age(age)}{note}")
    if age > SUPERVISOR_WARN_S:
        return Check(name="supervisor", status="warn", reason=f"heartbeat {_age(age)} old")
    note = ", size-down" if s.get("size_down") else ""
    return Check(name="supervisor", status="ok", reason=f"heartbeat {_age(age)}{note}")


def _control(ctx: HealthContext) -> dict[str, Any] | None:
    """control.json as a dict ({} when absent); None when unreadable."""
    f = ctx.state_dir / "control.json"
    if not f.exists():
        return {}
    try:
        c = _read_json(f)
        return c if isinstance(c, dict) else None
    except (ValueError, OSError):
        return None


def _channel(by: object) -> str:
    """"telegram:<id>" -> "telegram": a control.json from before A15 names who; the check names the channel only."""
    return str(by).split(":", 1)[0] if by else "unknown"


@register_check("global", block=BLOCK_CORE, pos=20)
def check_halt(ctx: HealthContext) -> Check:
    c = _control(ctx)
    if c is None:
        return Check(name="owner_halt", status="fail", reason="control.json unreadable: engines treat it as halted")
    rearm = ""
    if c.get("rearm_id"):
        at = pd.Timestamp(float(c.get("rearm_ts") or 0), unit="s", tz="UTC")
        rearm = f"; last owner re-arm via {_channel(c.get('rearm_by'))} at {at:%Y-%m-%d %H:%M} UTC"
    if c.get("halted"):
        since = pd.Timestamp(float(c.get("ts") or 0), unit="s", tz="UTC")
        why = f" ({c['reason']})" if c.get("reason") else ""
        return Check(name="owner_halt", status="warn",
                     reason=f"HALTED via {_channel(c.get('by'))}{why} since {since:%Y-%m-%d %H:%M} UTC; re-arm on the dashboard{rearm}")
    return Check(name="owner_halt", status="ok", reason="armed" + rearm)


@register_check("account", block=BLOCK_ACCOUNT, pos=20)
def check_risk_state(ctx: HealthContext, account_id: str) -> Check:
    """state/risk_<account>.json: the engine's persisted risk periods, drawdown stage and the last re-arm it applied.
    It is read even while the engine is down, so a persisted drawdown halt is visible before a restart."""
    name = f"risk:{account_id}"
    f = ctx.state_dir / f"risk_{account_id}.json"
    if not f.exists():
        return Check(name=name, status="warn", reason=f"no risk_{account_id}.json yet (engine never ran)")
    try:
        r = _read_json(f)
        if not isinstance(r, dict):
            raise ValueError("not an object")
    except (ValueError, OSError) as exc:
        return Check(name=name, status="fail", reason=f"risk state unreadable (the engine refuses to start): {exc}")
    parts: list[str] = []
    statuses: list[Status] = []
    stage = str(r.get("stage", "normal"))
    if stage == "halted":
        statuses.append("fail")
        parts.append("drawdown HALT persisted: owner re-arm on the dashboard needed")
    else:
        parts.append(f"stage {stage}")
    last = r.get("last_reset")
    if last:
        days = (ctx.now - pd.Timestamp(last)).total_seconds() / 86400
        if days > COSTS_WARN_DAYS and _market_open_throughout(ctx, ENGINE_FAIL_S):
            statuses.append("warn")
            parts.append(f"loss-cap day last rolled {days:.1f} d ago")
    c = _control(ctx) or {}
    rid = c.get("rearm_id")
    if rid and r.get("rearm_seen") != rid and _epoch(ctx.now) - float(c.get("rearm_ts") or 0) > REARM_APPLY_S:
        statuses.append("warn")
        parts.append("the last owner re-arm has not been applied by this engine (engine down?)")
    hwm_note = _hwm_pending(ctx, account_id, r)
    if hwm_note:
        statuses.append("warn")
        parts.append(hwm_note)
    return Check(name=name, status=_worst(statuses), reason="; ".join(parts))


KILL_DUE_WARN = pd.Timedelta(minutes=5)    # close-everything set with closes due this long after the trip: WARN
KILL_DUE_FAIL = pd.Timedelta(minutes=30)   # ... this long with the market open throughout: FAIL


@register_check("account", block=BLOCK_ACCOUNT, pos=25)
def check_kill_switch(ctx: HealthContext, account_id: str) -> list[Check]:
    """C9 (BACKLOG 62 plan review): the 12% kill switch's close-everything flag (`kill_everything`,
    state/risk_<account>.json) still set with closes the engine could not get filled (`closes_due`,
    engine_<account>.json). Timed from the trip (`halted_at`): ok inside KILL_DUE_WARN, then WARN (Telegram,
    NOTIFY_ON_WARN), FAIL after KILL_DUE_FAIL with the market open throughout. Nothing while the flag is clear or no
    close is due; an unreadable file is check_engine's and check_risk_state's to report. Reporting only."""
    try:
        r = _read_json(ctx.state_dir / f"risk_{account_id}.json")
        due = list(_read_json(ctx.state_dir / f"engine_{account_id}.json").get("closes_due") or [])
        since = pd.Timestamp(r["halted_at"]) if r.get("halted_at") else None
        flag = bool(r.get("kill_everything"))
    except (ValueError, OSError, AttributeError, TypeError):
        # silent here on purpose: a corrupt engine or risk file FAILs check_engine / check_risk_state, and a missing one
        # is reported there too (tests/test_kill_switch_health.py pins this), so one fault raises one alert
        return []
    if not (flag and due):
        return []
    name = f"kill_switch:{account_id}"
    what = (f"{len(due)} close(s) still due after the 12% kill switch: the whole account is not flat yet; closes are "
            "re-sent every tick (with back-off while the broker says the market is closed), stops rest with the broker")
    if since is None:
        return [Check(name=name, status="warn", reason=f"{what}; tripped since an unknown time")]
    age = ctx.now - (since.tz_localize("UTC") if since.tzinfo is None else since)
    if age <= KILL_DUE_WARN:
        return [Check(name=name, status="ok", reason=f"{what}; tripped {_age(age.total_seconds())} ago")]
    open_ = _market_open_throughout(ctx, KILL_DUE_FAIL.total_seconds())
    status: Status = "fail" if age > KILL_DUE_FAIL and open_ else "warn"
    tail = "" if _market_open_throughout(ctx, 0) else "; market closed: the closes go out when it opens"
    return [Check(name=name, status=status, reason=f"{what}; tripped {_age(age.total_seconds())} ago{tail}; close them "
                                                   "by hand in the terminal if this persists (RUNBOOK, kill switch)")]


def _hwm_pending(ctx: HealthContext, account_id: str, risk: dict[str, Any]) -> str | None:
    """ADR 0018: a balance rise the engine has not confirmed or refused for HWM_PENDING_WARN_S in market hours (the
    terminal's balance reads keep disagreeing, so the drawdown may be measured on a mark below the real one). No
    amounts: the high-water mark stays out of health reasons. A row for another mark is void, as in the engine. The age
    runs from the first of an unbroken run of disagreeing candidates (review M2)."""
    from goldbot.risk import hwm
    try:
        conf = hwm.read_only_confirmation(ctx.state_dir, account_id)
    except Exception as exc:
        return f"high-water-mark confirmation unreadable in core.db ({type(exc).__name__})"
    if conf is None or conf.mark != float(risk.get("balance_closed_hwm") or 0.0):
        return None
    conf = conf.fresh(ctx.now)                    # an expired candidate is void, as in the engine (review L2)
    if conf.pending_since_utc is None:
        return None
    age = (ctx.now - conf.pending_since_utc).total_seconds()
    if age <= HWM_PENDING_WARN_S or not _market_open_throughout(ctx, HWM_PENDING_WARN_S):
        return None
    return (f"a balance rise has waited {_age(age)} for confirmation (ADR 0018): the terminal's balance reads disagree, "
            "so the high-water mark may be below the real one")


@register_check("global", block=BLOCK_CORE, pos=30)
def check_phase(ctx: HealthContext) -> Check:
    """state/phase_state.json (written by `run.py record-gate`): live accounts unlock only after paper_to_tiny_live."""
    from goldbot.ops.accounts import GATES
    f = ctx.state_dir / "phase_state.json"
    if not f.exists():
        return Check(name="phase", status="ok", reason="phase 0, no gate recorded (demo only)")
    try:
        st = _read_json(f)
        passed = list(st.get("gates_passed", []))
        phase = int(st.get("phase", 0))
    except (ValueError, OSError, AttributeError, TypeError) as exc:
        return Check(name="phase", status="fail", reason=f"phase_state.json unreadable: {exc}")
    if passed != list(GATES[:len(passed)]):
        return Check(name="phase", status="fail",
                     reason=f"gates out of order or unknown: {passed} (record with `python -m goldbot.ops.run record-gate`)")
    return Check(name="phase", status="ok", reason=f"phase {phase}, gates passed: {', '.join(passed) or 'none'}")


@register_check("global", block=BLOCK_CORE, pos=40)
def check_stop_rule(ctx: HealthContext) -> Check:
    """P6, the design's stop rule (goldbot/ops/gates_phase.py): FAIL when, after 18 months of paper plus live, more than
    500 pooled trades still have a lower 90% bound on expectancy below zero, or when any single trade lost more than the
    weekly cap. The FAIL is the whole action: the alert loop tells the owner once, and the health CLI exits 1. It does
    not halt, close or resize anything (the RiskGate's own caps are unchanged); stopping is the owner's /halt."""
    from goldbot.ops import gates_phase as gp
    if ctx.settings is None:
        return _skipped("stop_rule", "settings do not load")
    trades, err = gp.load_closed_trades(ctx.state_dir)
    _, when, perr = gp.phase_record(ctx.state_dir)
    from goldbot.ops.cost_imputation import CostImputer
    s = gp.evaluate_stop_rule(trades, when.get("backtest_to_paper"), ctx.settings.risk.weekly_cap, ctx.settings.gates,
                              ctx.now, imputer=CostImputer.from_state(ctx.state_dir, ctx.settings))
    if s.breached:
        return Check(name="stop_rule", status="fail",
                     reason=("STOP RULE BREACHED: the design says the project stops; send /halt to stop entries. Nothing "
                             "was halted automatically (RiskGate caps still apply). " + "; ".join(s.reasons))[:600])
    problems = "; ".join(e for e in (err, perr) if e)
    if problems:
        return Check(name="stop_rule", status="warn", reason=f"record partly unreadable ({problems}); {s.detail}"[:400])
    return Check(name="stop_rule", status="ok", reason=s.detail)


@register_check("global", block=BLOCK_CORE, pos=50)
def check_phase_gates(ctx: HealthContext) -> Check:
    """P7: the next unrecorded roadmap gate, met or not, from the evidence (`run.py gates` prints the detail).
    Informational: a gate is recorded only by `run.py record-gate`, and live needs unlock-live plus the typed phrase."""
    from goldbot.ops import gates_phase as gp
    if ctx.settings is None:
        return _skipped("phase_gates", "settings do not load")
    try:
        rep = gp.evaluate_gates(ctx.state_dir, ctx.settings, ctx.now)
    except Exception as exc:                 # evidence files are best effort here; the gate report shows the detail
        return Check(name="phase_gates", status="warn", reason=f"gate evaluation failed: {type(exc).__name__}: {exc}"[:300])
    nxt = next((g for g in rep.gates if not g.recorded), None)
    if nxt is None:
        return Check(name="phase_gates", status="ok", reason="every roadmap gate recorded")
    req = [i for i in nxt.items if i.required]
    missing = [i.name for i in req if not i.met]
    if nxt.met:
        return Check(name="phase_gates", status="ok",
                     reason=f"next gate {nxt.gate}: MET on the evidence; the owner records it with run.py record-gate")
    return Check(name="phase_gates", status="ok",
                 reason=f"next gate {nxt.gate}: not met ({len(req) - len(missing)}/{len(req)} items; missing {', '.join(missing)})"[:400])


@register_check("account", block=BLOCK_ACCOUNT, pos=50)
def check_loss_caps(ctx: HealthContext, account_id: str) -> list[Check]:
    """R11: the per-account daily and weekly loss caps from the engine's state (equity against the day/week-start
    equity, the RiskGate's own arithmetic). A tripped cap warns and is announced (NOTIFY_ON_WARN): the gate already
    blocks entries, this only tells the owner. Weekly stays tripped until the risk week rolls, so it is told once a week."""
    f = ctx.state_dir / f"engine_{account_id}.json"
    if ctx.settings is None or not f.exists():
        return []
    try:
        e = _read_json(f)
        eq = float(e.get("equity") or 0.0)
        starts = {"daily": float(e.get("day_start_equity") or 0.0), "weekly": float(e.get("week_start_equity") or 0.0)}
        # BACKLOG 29: the engine's effective daily loss limit (min of the % cap and the absolute) and what is left
        limit, left = e.get("daily_loss_limit"), e.get("daily_loss_remaining")
        allowance = (float(limit), float(left)) if limit is not None and left is not None else None
    except (ValueError, OSError, AttributeError, TypeError):
        return []                                   # check_engine reports the unreadable file
    caps = {"daily": ctx.settings.risk.daily_cap, "weekly": ctx.settings.risk.weekly_cap}
    out = []
    for period in ("daily", "weekly"):
        start = starts[period]
        loss = 1 - eq / start if start > 0 else 0.0
        msg = f"{account_id}: {period} loss {100 * loss:.2f}% of {'day' if period == 'daily' else 'week'}-start equity {start:,.2f}"
        if period == "daily" and allowance is not None:
            msg += f", daily loss limit {allowance[0]:,.2f} ({allowance[1]:,.2f} left)"
            if allowance[1] <= 0 and loss < caps[period]:
                out.append(Check(name=f"{period}_cap:{account_id}", status="warn",
                                 reason=f"DAILY LOSS LIMIT reached: {msg}: no new entries until the day rolls"))
                continue
        if loss >= caps[period]:
            out.append(Check(name=f"{period}_cap:{account_id}", status="warn",
                             reason=f"{period.upper()} LOSS CAP hit: {msg} (cap {100 * caps[period]:.1f}%): no new entries until it rolls"))
        else:
            out.append(Check(name=f"{period}_cap:{account_id}", status="ok", reason=f"{msg} (cap {100 * caps[period]:.1f}%)"))
    return out


@register_check("account", block=BLOCK_ACCOUNT, pos=70)
def check_throttle(ctx: HealthContext, account_id: str) -> list[Check]:
    """BACKLOG 21 (playbook G-6: "Logged and alerted"): while the per-agent throttle is on, one check per agent
    (`throttle:<account>:<agent>`, so each newly throttled agent alerts on its own, review MEDIUM-1). It warns (and
    tells the owner, NOTIFY_ON_WARN) while RiskGate refuses the agent's entries for a loss streak or the daily entry
    rate, and is ok otherwise. It FAILs when the block cannot lift by itself or the facts are unknown: a loss-streak
    block without a cooldown end (unknown timeframe), `throttle_unknown`, or a throttle state the engine could not
    publish (`throttle:<account>`). Nothing while the throttle is off or the engine has not published it."""
    f = ctx.state_dir / f"engine_{account_id}.json"
    if not f.exists():
        return []
    try:
        th = _read_json(f).get("throttle") or {}
        if not th.get("enabled"):
            return []
        error = th.get("error")
        agents = {str(k): dict(v) for k, v in (th.get("agents") or {}).items()}
    except (ValueError, OSError, AttributeError, TypeError):
        return []                                   # check_engine reports the unreadable file
    if error:
        return [Check(name=f"throttle:{account_id}", status="fail",
                      reason=f"{account_id}: throttle state not published ({error}); entries are judged per decision")]
    out = []
    for a, v in sorted(agents.items()):
        name, blocked = f"throttle:{account_id}:{a}", [str(r) for r in v.get("blocked") or []]
        facts = (f"streak {v.get('loss_streak')}, {v.get('entries_today')} entries today, cooldown until "
                 f"{v.get('cooldown_until') or 'n/a'}")
        if not blocked:
            out.append(Check(name=name, status="ok", reason=f"{account_id} {a}: not throttled ({facts})"))
            continue
        stuck = "throttle_unknown" in blocked or ("throttle_loss_streak" in blocked and not v.get("cooldown_until"))
        out.append(Check(name=name, status="fail" if stuck else "warn",
                         reason=f"{account_id} {a}: entries throttled: {','.join(blocked)} ({facts})"
                                + ("; the block cannot lift by itself" if stuck else "") + "; exits unaffected"))
    return out


@register_check("account", block=BLOCK_ACCOUNT, pos=80)
def check_open_risk(ctx: HealthContext, account_id: str) -> list[Check]:
    """BACKLOG 19/35 (review LOW-3): while the heat cap is on, or the open-risk projection is on with an absolute daily
    limit, the engine's `open_risk` must be known; null means RiskGate refuses every entry (`open_risk_unknown`: a
    position without SL, a conversion the broker cannot give, or a failed read). One check `open_risk:<account>`: warn
    and tell the owner (NOTIFY_ON_WARN) while it is unknown, ok with the amount otherwise. Nothing while both rules are
    off or the engine has not published them."""
    f = ctx.state_dir / f"engine_{account_id}.json"
    if not f.exists():
        return []
    try:
        e = _read_json(f)
        lim = e.get("limits") or {}
        heat = bool((lim.get("heat") or {}).get("enabled"))
        projected = bool(lim.get("project_open_risk")) and lim.get("daily_loss_limit_abs") is not None
        risk = e.get("open_risk")
    except (ValueError, OSError, AttributeError, TypeError):
        return []                                   # check_engine reports the unreadable file
    if not (heat or projected):
        return []
    rules = " and ".join(n for n, on in (("heat cap", heat), ("open-risk projection", projected)) if on)
    name = f"open_risk:{account_id}"
    if risk is None:
        return [Check(name=name, status="warn",
                      reason=f"{account_id}: open risk unknown (a position without a stop, no conversion, or a failed "
                             f"read): every entry refused while the {rules} is on; exits unaffected")]
    return [Check(name=name, status="ok", reason=f"{account_id}: open risk {float(risk):.2f} ({rules} on)")]


WINDOWS_UNAVAILABLE_FAIL = pd.Timedelta(minutes=30)   # calendar_unavailable this long with the market open: FAIL
WINDOWS_FETCH_WARN = pd.Timedelta(hours=36)            # newest calendar fetch older than this: WARN
WINDOWS_HOLIDAY_WARN_DAYS = 90                         # holiday list ending within this many days: WARN


@register_check("account", block=BLOCK_ACCOUNT, pos=90)
def check_entry_windows(ctx: HealthContext, account_id: str) -> list[Check]:
    """BACKLOG 17/18 entry windows fail closed (`calendar_unavailable` refuses every entry), so the owner must hear
    when that happens (review M1), from the engine's `windows` block. Checks, only for enabled windows:
    `entry_windows:<account>:calendar` FAILs once the calendar has been unavailable over 30 min with the market open
    (ok while the market is closed or for shorter spells, which the Sunday fetch and the 5-minute re-read clear);
    `entry_windows:<account>:calendar_age` (tier 2) warns when the newest Forex Factory fetch is over 36 h old;
    `entry_windows:<account>:holiday_list` warns when the holiday list ends within 90 days. Warnings reach Telegram
    (NOTIFY_ON_WARN). Nothing while every window is off or the engine has not published the block."""
    f = ctx.state_dir / f"engine_{account_id}.json"
    if not f.exists():
        return []
    try:
        w = _read_json(f).get("windows") or {}
        on = {k: bool(v) for k, v in (w.get("enabled") or {}).items()}
    except (ValueError, OSError, AttributeError, TypeError):
        return []                                   # check_engine reports the unreadable file
    if not any(on.values()):
        return []
    out = []
    pre = f"entry_windows:{account_id}"
    if on.get("tier2") or on.get("holidays"):
        since = pd.Timestamp(w["calendar_unavailable_since"]) if w.get("calendar_unavailable_since") else None
        if not w.get("calendar_unavailable") or since is None:
            out.append(Check(name=f"{pre}:calendar", status="ok", reason=f"{account_id}: calendar known"))
        else:
            lasted = ctx.now - since
            open_ = _market_open_throughout(ctx, WINDOWS_UNAVAILABLE_FAIL.total_seconds())
            bad = lasted > WINDOWS_UNAVAILABLE_FAIL and open_
            why = w.get("calendar_unavailable_reason") or "unknown"
            out.append(Check(name=f"{pre}:calendar", status="fail" if bad else "ok",
                             reason=f"{account_id}: calendar_unavailable since {since:%Y-%m-%d %H:%M} UTC ({why}): "
                                    "entries refused, exits unaffected"
                                    + ("" if bad else " (market closed or under 30 min)")))
    if on.get("tier2"):
        rec = pd.Timestamp(w["calendar_received_utc"]) if w.get("calendar_received_utc") else None
        if rec is None or ctx.now - rec > WINDOWS_FETCH_WARN:
            out.append(Check(name=f"{pre}:calendar_age", status="warn",
                             reason=f"{account_id}: newest calendar fetch {rec or 'never'} is over 36 h old; check the "
                                    "calendar_archive job"))
        else:
            out.append(Check(name=f"{pre}:calendar_age", status="ok",
                             reason=f"{account_id}: calendar fetched {rec:%Y-%m-%d %H:%M} UTC"))
    if on.get("holidays") and w.get("holiday_valid_through"):
        end = pd.Timestamp(w["holiday_valid_through"]).date()
        left = (end - ctx.now.date()).days
        near = left <= WINDOWS_HOLIDAY_WARN_DAYS
        out.append(Check(name=f"{pre}:holiday_list", status="warn" if near else "ok",
                         reason=f"{account_id}: holiday list {w.get('holiday_version')} valid through {end} ({left} d)"
                                + ("; extend config/holidays_cme_metals.yaml or every entry is refused after it"
                                   if near else "")))
    return out
