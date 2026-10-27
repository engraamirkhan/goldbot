"""MT5 bridge health checks: /health through the tunnel, compatibility, broker server clock."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.data.calendar import SessionTable, session_table_from_cache
from goldbot.ops.checks._base import AccountRef, Check, Status, _read_json
from goldbot.ops.checks.registry import BLOCK_ACCOUNT, BLOCK_CORE, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext


@register_check("account", block=BLOCK_ACCOUNT, pos=110)
def check_bridge(ctx: HealthContext, account_id: str) -> Check | None:
    """The MT5 bridge answers GET /health through the SSH tunnel (only when a bridge URL is in the keyring and a probe
    is configured). A bridge down means no ticks, no orders and no position management for that account."""
    try:
        url = ctx.get_secret(f"mt5-bridge-url-{account_id}")
    except Exception:
        url = None
    if not url or ctx.bridge_probe is None:
        return None
    name = f"bridge:{account_id}"
    why = ctx.bridge_probe(url)          # the URL is never quoted in a reason (it names the tunnel endpoint)
    if why is not None:
        return Check(name=name, status="fail", reason=f"bridge does not answer /health through the tunnel: {why[:160]}")
    return Check(name=name, status="ok", reason="bridge answers /health")


TERMINAL_NOT_CONNECTED_FAIL_S = 5 * 60    # a terminal off the broker server this long FAILs (warns before)
_NO_ENTRIES = "No entries; stops stay at the broker."
_TERMINAL_FAIL = {
    "not_running": "MT5 terminal not running on the MT5 box; it is being restarted. " + _NO_ENTRIES
                   + " If this lasts 10 min: RUNBOOK 'Terminal down'.",
    "not_logged_in": "MT5 terminal running but not logged in (saved login lost?). " + _NO_ENTRIES
                     + " The MT5 box logs it in from the keyring; if it stays: goldbot-mt5 login <account>,"
                     " RUNBOOK 'Terminal down'.",
    "wrong_account": "MT5 terminal logged in to a different account than registered; the bridge refuses orders.",
    # incident 2026-10-20: the terminal unit's start sets [Experts] Enabled=1 (mt5-experts-enable.sh)
    "autotrading_off": "MT5 terminal AutoTrading is off: the terminal refuses every order (10027). Restart the terminal"
                       " unit on the MT5 box (its start enables it): `sudo systemctl restart"
                       " goldbot-mt5-terminal@<account>`",
}


@register_check("account", block=BLOCK_ACCOUNT, pos=115)
def check_terminal(ctx: HealthContext, account_id: str) -> Check | None:
    """The MT5 terminal behind the bridge, as the bridge's GET /health reports it: one token (ok, not_running,
    not_connected, not_logged_in, wrong_account, autotrading_off; docs/proposals/2026-10-mt5-terminal-lifecycle.md
    section 3). An older brain reads autotrading_off as an unknown value: `noanswer`, a warning here. The
    wording never carries a login, a server or a host (security condition 6). Omitted without a bridge URL or a probe;
    a bridge that does not answer is `bridge:<account>`'s FAIL, so here it is a warning."""
    try:
        url = ctx.get_secret(f"mt5-bridge-url-{account_id}")
    except Exception:
        url = None
    if not url or ctx.terminal_probe is None:
        return None
    name = f"terminal:{account_id}"
    try:
        state, for_s = ctx.terminal_probe(url)
    except Exception as exc:              # the type only: a message can name the tunnel endpoint
        return Check(name=name, status="warn", reason=f"MT5 terminal state not read ({type(exc).__name__})")
    if state == "ok":
        return Check(name=name, status="ok", reason="MT5 terminal running, logged in to the registered account and "
                     "connected")
    if state == "autotrading_off" and (n := _open_positions(ctx, account_id)):
        # trading-safety review: goldbot cannot close anything (time barrier, weekend, kill switch) while it is off
        return Check(name=name, status="fail", reason=f"CRITICAL: {n} open position(s) on this account cannot be "
                     "closed by goldbot; only their broker stop and target protect them. " + _TERMINAL_FAIL[state])
    if state in _TERMINAL_FAIL:
        return Check(name=name, status="fail", reason=_TERMINAL_FAIL[state])
    if state == "not_connected":
        long = isinstance(for_s, int) and for_s >= TERMINAL_NOT_CONNECTED_FAIL_S
        span = f" for {for_s // 60} min" if isinstance(for_s, int) and for_s >= 60 else ""
        return Check(name=name, status="fail" if long else "warn",
                     reason=f"MT5 terminal not connected to the broker server.{span and ' (' + span.strip() + ')'} "
                            + _NO_ENTRIES + " Its unit and the watchdog restart it after 10 min; RUNBOOK 'Terminal down'.")
    if state == "unreported":
        return Check(name=name, status="warn", reason="the bridge does not report the MT5 terminal state (older than "
                     "this release): deploy the MT5 box (RUNBOOK 0.5)")
    return Check(name=name, status="warn", reason=f"MT5 terminal state unknown: the bridge does not answer /health "
                 f"(see bridge:{account_id})")


def _open_positions(ctx: HealthContext, account_id: str) -> int:
    """The open positions the engine last published (engine_<account>.json); 0 when unknown."""
    try:
        n = _read_json(ctx.state_dir / f"engine_{account_id}.json").get("open_positions")
    except (ValueError, OSError, AttributeError):
        return 0
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0


@register_check("account", block=BLOCK_ACCOUNT, pos=120, pass_account=True)
def check_bridge_compat(ctx: HealthContext, account: AccountRef) -> Check | None:
    """BACKLOG 42 (gap G6): the bridge's /health against this checkout (goldbot/ops/bridge_compat.py). FAIL when a
    method this brain calls is missing, the protocol is older, the zone differs or the bridge does not report them: the
    engine refuses entries (`bridge_incompatible`) until the MT5 box runs a compatible commit; exits are unaffected.
    A different commit alone is a warning (doc-only commits differ). /health unreachable is a warning here
    (`bridge:<account>` fails for it) unless the engine's last verdict still blocks. Omitted without a bridge URL or
    an info reader."""
    try:
        url = ctx.get_secret(f"mt5-bridge-url-{account.account_id}")
    except Exception:
        url = None
    if not url or ctx.bridge_info is None or account.server_tz is None:
        return None
    from goldbot.ops.bridge_compat import BridgeInfo, assess
    name = f"bridge_compat:{account.account_id}"
    try:
        info = ctx.bridge_info(url)
    except Exception as exc:              # the type only: a message can name the tunnel endpoint
        info = BridgeInfo(reachable=False, problem=f"/health unreadable ({type(exc).__name__})")
    v = assess(info, account.server_tz, ctx.commit)
    if v.blocks:
        return Check(name=name, status="fail", reason=f"{v.reason}: the engine refuses entries (bridge_incompatible), "
                     "exits unaffected; deploy the brain's commit to the MT5 box (RUNBOOK: bridge compatibility)")
    try:
        engine = _read_json(ctx.state_dir / f"engine_{account.account_id}.json").get("bridge_compat") or {}
    except (ValueError, OSError, AttributeError):
        engine = {}
    if not isinstance(engine, dict):
        engine = {}
    if v.status == "unreachable":
        if engine.get("status") == "incompatible":
            return Check(name=name, status="fail", reason=f"{v.reason}; the engine's last verdict still refuses "
                         f"entries: {str(engine.get('reason', ''))[:200]}")
        return Check(name=name, status="warn", reason=v.reason)
    stale = _engine_bridge_unconfirmed(ctx, engine)
    if stale is not None:                 # the bridge answers, but the engine's own check has not confirmed it
        why, past_grace = stale
        return Check(name=name, status="fail" if past_grace else "warn", reason=f"{v.reason}; {why}")
    return Check(name=name, status="warn" if v.commit_differs else "ok", reason=v.reason)


def _age_s(now: pd.Timestamp, raw: Any) -> float:
    """Seconds since an ISO stamp from a state file; infinity when missing or unreadable (assume the worst)."""
    try:
        at = pd.Timestamp(raw) if isinstance(raw, str) else None
        if at is None or pd.isna(at):
            return float("inf")
        return (now - (at if at.tzinfo is not None else at.tz_localize("UTC"))).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def _engine_bridge_unconfirmed(ctx: HealthContext, engine: dict[str, Any]) -> tuple[str, bool] | None:
    """Why the engine's published bridge verdict leaves entries refused although this probe passed, and whether it is
    past the grace period (FAIL, alerted) rather than inside it (warn): `unchecked` for over UNCHECKED_GRACE_S, or an ok
    whose `checked_at` is over MAX_VERDICT_AGE_S old (its check thread may be stuck). A missing stamp counts as past
    grace. No verdict published, or an incompatible one (failed above when the probe agrees): None. Not done through
    NOTIFY_ON_WARN: a commit mismatch on the same check must stay quiet."""
    from goldbot.ops.bridge_compat import MAX_VERDICT_AGE_S, UNCHECKED_GRACE_S
    status = engine.get("status")
    if status is None or status == "incompatible":
        return None
    if status == "unchecked":
        age = _age_s(ctx.now, engine.get("unchecked_since"))
        span = "since an unknown time" if age == float("inf") else f"for {age / 60:.1f} min"
        return (f"the engine has not confirmed the bridge {span} ({str(engine.get('reason', ''))[:120]}): entries "
                "refused (bridge_incompatible); it retries every 60 s and replaces a hung check, restart the engine "
                "if this persists", age > UNCHECKED_GRACE_S)
    age = _age_s(ctx.now, engine.get("checked_at"))
    if age > MAX_VERDICT_AGE_S:
        return (f"the engine has not confirmed the bridge for over {MAX_VERDICT_AGE_S / 60:g} min (its check may be "
                "stuck): entries refused as unchecked", True)
    return None


def _engine_sessions(ctx: HealthContext, account_id: str) -> SessionTable:
    """The broker session table the engine last wrote to engine_<account>.json (D13), else `ctx.sessions`."""
    try:
        cached = _read_json(ctx.state_dir / f"engine_{account_id}.json").get("sessions")
    except (ValueError, OSError, AttributeError):
        cached = None
    table = session_table_from_cache(cached) if cached else None
    return table if table is not None else ctx.sessions


@register_check("account", block=BLOCK_ACCOUNT, pos=130, pass_account=True)
def check_server_clock(ctx: HealthContext, account: AccountRef) -> Check | None:
    """The broker's real UTC offset against the configured server_tz (goldbot/ops/server_clock.py). Fails on a
    mismatch over 5 min while the market is open: an entry must not open on a clock that is an hour off (session
    table, rollover gate, Friday cut, tick and bar stamps); exits are never gated by it. Omitted without a tick reader,
    a server_tz or a bridge for the account. The engine runs the same assessment on every refresh (entry gate
    `server_clock_mismatch`); this check measures it independently of the engine. "Now" is read AFTER the tick
    (`ctx.clock`), and the market-open test uses the engine's cached broker session table when it has one."""
    if ctx.tick_reader is None or account.server_tz is None:
        return None
    from goldbot.ops import server_clock
    name = f"server_clock:{account.account_id}"
    try:
        tick = ctx.tick_reader(account.account_id, account.symbol)
    except Exception as exc:              # the type only: a bridge error message can name the tunnel endpoint
        return Check(name=name, status="warn", reason=f"not measured: latest tick unreadable ({type(exc).__name__})")
    if tick is None:
        return None
    now = ctx.clock() if ctx.clock is not None else ctx.now
    try:
        p = server_clock.assess(account.account_id, account.server_tz, tick.ts_utc, now,
                                _engine_sessions(ctx, account.account_id))
    except Exception as exc:
        return Check(name=name, status="warn", reason=f"not measured: probe failed ({type(exc).__name__})")
    status: Status = "fail" if p.status == "mismatch" else "ok"     # market closed: not measurable, no alert noise
    return Check(name=name, status=status, reason=p.reason)


@register_check("global", block=BLOCK_CORE, pos=60)
def check_pilot_server_clock(ctx: HealthContext) -> list[Check]:
    """The real-money pilot's server clock before the unlock (docs/deploy/live-readiness-2026-10-30.md, L4: "server_tz
    set from a measurement on the live server"): `check_server_clock` for every configured pilot account that is not
    enabled yet (`ctx.pilot_accounts`), as soon as a bridge for it answers. Nothing without a bridge (not connected
    yet); an enabled pilot account is measured by the per-account check, once. Read-only."""
    out: list[Check] = []
    for a in ctx.pilot_accounts:
        c = check_server_clock(ctx, a)
        if c is not None:
            out.append(c)
    return out
