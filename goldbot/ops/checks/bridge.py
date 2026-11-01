"""MT5 bridge health checks: /health through the tunnel, compatibility, broker server clock."""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.data.calendar import SessionTable, session_table_from_cache
from goldbot.ops.checks._base import AccountRef, Check, Status, _read_json
from goldbot.ops.checks.registry import BLOCK_ACCOUNT, BLOCK_CORE, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext


# Incident 2026-11-01: a bridge in a restart loop for 6 h. One page per outage, inside the 6-minute alert objective:
# unreachable under BRIDGE_DOWN_FAIL_S is a warning (a deploy or a restart passes), then a FAIL that names the causes
# and the steps. The outage's start is kept in state/BRIDGE_DOWN_FILE (SQLite: robust-storage rule) across health
# runs, so a bridge that answers now and then (a restart loop) cannot restart the clock; an outage that reached FAIL
# stays one incident until the bridge has answered for BRIDGE_UP_CLOSE_S (no recovered + new page on every flap). A
# record that cannot be read or written fails at once, as before this change: never silent.
BRIDGE_DOWN_FAIL_S = 5 * 60
BRIDGE_UP_CLOSE_S = 3 * 60
BRIDGE_DOWN_FILE = "health_outages.db"


def _outage(ctx: HealthContext, account_id: str, down: bool) -> tuple[float, float | None] | None:
    """(seconds since the outage began, seconds the bridge has answered again after an outage that reached FAIL, else
    None) after recording this probe; (0, None) when nothing is to be said; None when the record is unusable."""
    now = ctx.now.timestamp()
    try:
        con = sqlite3.connect(ctx.state_dir / BRIDGE_DOWN_FILE, timeout=5.0)
    except (sqlite3.Error, OSError):
        return None
    try:
        with con:
            con.execute("CREATE TABLE IF NOT EXISTS bridge_outage (account TEXT PRIMARY KEY, since REAL NOT NULL, "
                        "answered REAL, failed INTEGER NOT NULL DEFAULT 0)")
            row = con.execute("SELECT since, answered, failed FROM bridge_outage WHERE account = ?",
                              (account_id,)).fetchone()
            if row is None:
                if down:
                    con.execute("INSERT INTO bridge_outage (account, since) VALUES (?, ?)", (account_id, now))
                return 0.0, None
            since, failed = float(row[0]), bool(row[2])
            if down:
                failed = failed or now - since >= BRIDGE_DOWN_FAIL_S
                con.execute("UPDATE bridge_outage SET answered = NULL, failed = ? WHERE account = ?",
                            (int(failed), account_id))
                return now - since, None
            back = now if row[1] is None else float(row[1])
            if now - back >= BRIDGE_UP_CLOSE_S:          # the incident is over; the next outage starts afresh
                con.execute("DELETE FROM bridge_outage WHERE account = ?", (account_id,))
                return 0.0, None
            con.execute("UPDATE bridge_outage SET answered = ? WHERE account = ?", (back, account_id))
            return (now - since, now - back) if failed else (0.0, None)
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return None
    finally:
        con.close()


def _bridge_down_reason(account_id: str, minutes: int, why: str) -> str:
    return (f"MT5 bridge unreachable for {minutes} min ({why[:160]}): no ticks, no entries and no position management "
            "by goldbot on this account; open positions keep their broker stop and target. On the MT5 box: `sudo "
            f"journalctl -u goldbot-bridge@{account_id} -n 30`. 'password refused by the broker' there is a latched "
            "login that no restart clears: RUNBOOK 'MT5 login' (read the terminal journal before --retry-refused); "
            "otherwise RUNBOOK 'Terminal down'.")


@register_check("account", block=BLOCK_ACCOUNT, pos=110)
def check_bridge(ctx: HealthContext, account_id: str, *, debounce: bool = True) -> Check | None:
    """The MT5 bridge answers GET /health through the SSH tunnel (only when a bridge URL is in the keyring and a probe
    is configured). A bridge down means no ticks, no orders and no position management for that account: a warning
    for its first BRIDGE_DOWN_FAIL_S, then one FAIL for the whole outage (see BRIDGE_DOWN_FILE above). `debounce`
    False (preflight: a verdict now) fails at once and neither reads nor writes the outage record."""
    try:
        url = ctx.get_secret(f"mt5-bridge-url-{account_id}")
    except Exception:
        url = None
    if not url or ctx.bridge_probe is None:
        return None
    name = f"bridge:{account_id}"
    why = ctx.bridge_probe(url)          # the URL is never quoted in a reason (it names the tunnel endpoint)
    if not debounce:
        if why is None:
            return Check(name=name, status="ok", reason="bridge answers /health")
        return Check(name=name, status="fail", reason=f"bridge does not answer /health through the tunnel: {why[:160]}")
    out = _outage(ctx, account_id, down=why is not None)
    if out is None:                      # no record: fail at once rather than never
        if why is None:
            return Check(name=name, status="ok", reason="bridge answers /health")
        return Check(name=name, status="fail", reason=_bridge_down_reason(account_id, 0, why)
                     + f" (outage record state/{BRIDGE_DOWN_FILE} unusable: paged at once)")
    down_s, back_s = out
    if why is not None:
        if down_s < BRIDGE_DOWN_FAIL_S:
            return Check(name=name, status="warn", reason=f"bridge does not answer /health through the tunnel for "
                         f"{int(down_s)} s: {why[:160]} (a restart or deploy passes; paged at "
                         f"{BRIDGE_DOWN_FAIL_S // 60} min)")
        return Check(name=name, status="fail", reason=_bridge_down_reason(account_id, int(down_s // 60), why))
    if back_s is not None:
        return Check(name=name, status="fail", reason=f"bridge answers again for {int(back_s)} s after "
                     f"{int((down_s - back_s) // 60)} min unreachable; the incident closes after "
                     f"{BRIDGE_UP_CLOSE_S // 60} min answering")
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


_LOGIN_REFUSED = (
    "MT5 login refused and latched on the MT5 box: it does not heal by itself (no password is sent again, through any "
    "restart, until it is cleared), so this account stays down. " + _NO_ENTRIES + " RUNBOOK 'MT5 login': read the "
    "reason in the terminal journal first. Broker side ('Service is not available', 'No connection'): stop the "
    "watchdog timer and the bridge, then `goldbot-mt5 login {account} --retry-refused` once. 'Invalid account': store "
    "the right password with `goldbot-mt5 accounts set mt5-{account}`; never retry.")


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
        probed = tuple(ctx.terminal_probe(url))
        state, for_s = probed[0], probed[1]
        login = probed[2] if len(probed) > 2 else None
    except Exception as exc:              # the type only: a message can name the tunnel endpoint
        return Check(name=name, status="warn", reason=f"MT5 terminal state not read ({type(exc).__name__})")
    if state != "ok" and login == "refused":     # incident 2026-11-01: a latched refusal never heals by itself
        return Check(name=name, status="fail", reason=_LOGIN_REFUSED.format(account=account_id))
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
    from goldbot.ops.bridge_compat import LANE_DOWN, BridgeInfo, assess
    name = f"bridge_compat:{account.account_id}"
    try:
        info = ctx.bridge_info(url)
    except Exception as exc:              # the type only: a message can name the tunnel endpoint
        info = BridgeInfo(reachable=False, problem=f"/health unreadable ({type(exc).__name__})")
    v = assess(info, account.server_tz, ctx.commit, live=account.live)
    if v.blocks:
        return Check(name=name, status="fail", reason=f"{v.reason}: the engine refuses entries (bridge_incompatible), "
                     "exits unaffected; deploy the brain's commit to the MT5 box (RUNBOOK: bridge compatibility)")
    if info.order_lane == LANE_DOWN:      # BACKLOG 135: any account; entries share the terminal again until a restart
        return Check(name=name, status="fail", reason=f"{v.reason}; the order lane is down: entries share the bridge's "
                     "terminal connection again, so exits can wait out an order (RUNBOOK 'Order lane': restart "
                     "goldbot-bridge@<account> or roll the lane back)")
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
