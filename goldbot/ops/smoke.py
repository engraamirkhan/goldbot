"""`python -m goldbot.ops.run bridge-smoke <account>`: one real minimum-lot round trip on a demo terminal through the
MT5 bridge (BACKLOG 1, W1-1; ADR 0021; plan docs/proposals/2026-10-w1-1-bridge-smoke-plan.md).

Design rules this command keeps: "RiskGate is the only path to an order", "the owner confirms every entry (90 s)",
"exits are never gated", demo accounts only until the live gate. In order:

1. The terminal must be the registry's account (`accounts.verify_terminal_account`) and demo, and the registry's mode
   demo; else exit 2 and nothing is read further. The login is only ever printed masked (`****123`): this output is
   pasted on the public issue #34.
2. It prints the server, balance and a live XAUUSD tick, then needs, before any order call (S3): the engine service
   `inactive` (`systemctl is-active goldbot-engine@<account>`), the engine's loop heartbeat at least
   HEARTBEAT_MIN_AGE_S old, and `flock(LOCK_EX | LOCK_NB)` on state/.smoke-<account>.lock (mode 0600), held to the end
   so two smokes never interleave. Any of them fails: exit 4, nothing sent. The supervisor keeps running (an engine
   refuses entries on a supervisor heartbeat older than 60 s, and so does this command, through `supervisor_halt`).
3. A smoke position left by an earlier run (magic_base + SMOKE_MAGIC_OFFSET) is closed first; one that cannot be
   closed: exit 5, never a second one open (S8, S9).
4. `smoke_state` builds the AccountState from named sources, every field explicitly (S2; `STATE_SOURCES`); a source
   it cannot read refuses (exit 4). `RiskGate.check_operational` (ADR 0021) sizes and checks the order: refused, exit
   7. Only this module calls it (tests/test_operational_fence.py).
5. One owner click on the ApprovalBus within the window (F6): no click is EXPIRED_UNAPPROVED and a rejection is
   REJECTED, both exit 3 and nothing sent. At the click the preconditions and the gate run again on fresh reads; any
   refusal sends nothing.
6. A buy of the minimum lot with its stop and target `stop_distance` either side of the ask (S11). The fill's stop
   and target are verified (missing: closed at once, exit 1); then the position is closed, CLOSE_ATTEMPTS times at
   most, never through the gate. Still open: exit 5 LEFT OPEN and an owner alert.
7. The send raised (bridge timeout or error) or its result is ambiguous: positions and deals are polled for the order's
   comment for order_timeout_s + ORDER_MAX_AGE_S (25 s); a fill found is closed, none found is exit 6 UNKNOWN with an
   owner alert. The order is never sent again.

X7 departure (TRACEABILITY): no `pending_orders` row (the engine's table). The client order id is
`<account>-<epoch_ns>-smoke`, its last 31 characters the order comment (as the engine's), and the look-ups above find
the order by magic and comment instead.

Exit codes: 0 round trip done; 1 failed without risk left (broker rejected, or stop/target missing and closed); 2 not
a demo terminal or not the registry's account; 3 not approved; 4 a precondition or a source failed; 5 a smoke position
is LEFT OPEN; 6 UNKNOWN whether the order filled; 7 refused by the RiskGate.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field

from goldbot.base import Record
from goldbot.config import tf_seconds
from goldbot.data.calendar import SessionTable
from goldbot.data.timeutil import epoch_ns, server_wall
from goldbot.execution.broker import (
    SMOKE_MAGIC_OFFSET,
    AccountInfo,
    OrderIntent,
    OrderResult,
    Position,
    SymbolInfo,
    Tick,
)
from goldbot.ops.accounts import Account, verify_terminal_account
from goldbot.ops.watchdog import loop_heartbeat_path
from goldbot.risk.gate import (
    AccountState,
    GateDecision,
    OperationalIntent,
    RiskGate,
    RiskLimits,
    Stage,
    broker_margin,
    new_day,
)
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal
from goldbot.telegram.bus import ApprovalBus

log = logging.getLogger("goldbot.smoke")

EXIT_OK, EXIT_FAILED, EXIT_NOT_DEMO, EXIT_UNAPPROVED = 0, 1, 2, 3
EXIT_PRECONDITION, EXIT_LEFT_OPEN, EXIT_UNKNOWN, EXIT_GATE = 4, 5, 6, 7

HEARTBEAT_MIN_AGE_S = 150.0     # S3: the engine loop beats every 15 s; ten missed beats and the unit inactive
APPROVAL_POLL_S = 1.0           # the bus is read this often while the owner's window is open
CLOSE_ATTEMPTS = 3              # S8: a close is retried this many times in all (never gated), then LEFT OPEN
CLOSE_RETRY_S = 1.0
POLL_EVERY_S = 2.5              # S4: positions and deals are read this often after a send that raised
DEFAULT_ORDER_TIMEOUT_S = 15.0  # RemoteBroker's order timeout when the broker does not say
LOOKUP_WINDOW = pd.Timedelta(hours=24)   # S9: earlier smoke deals are looked up this far back
SEND_SLACK_S = 30.0             # LOW-5: a rewritten comment is matched by magic and an open at or after send - this
ENTRY_IN, ENTRY_OUT = 0, (1, 2, 3)       # MT5 DEAL_ENTRY_IN; OUT, INOUT, OUT_BY (as goldbot/ops/ledger.py)
BARS_TF, BARS_N = "1m", 5
SMOKE_AGENT = "bridge-smoke"    # the approval card's agent: not a model, so p and EV are shown as 0
COMMENT_LEN = 31                # MT5's order comment limit (the engine's comment is `cid[-31:]` too)
FILLED = 10009                  # TRADE_RETCODE_DONE

# S2: every AccountState field and where smoke_state reads it; a constant names its reason. tests/test_smoke.py keeps
# this list equal to the record's fields and checks smoke_state sets each one explicitly
STATE_SOURCES: dict[str, str] = {
    "equity": "account().equity",
    "balance_closed_hwm": "risk_<account>.json balance_closed_hwm (the engine's mark)",
    "day_start_equity": "risk_<account>.json, a stale day passed through new_day() in memory (never written)",
    "week_start_equity": "risk_<account>.json, a stale week passed through new_day() in memory (never written)",
    "open_positions": "account_positions(): every position on the account",
    "margin_used": "account().margin",
    "last_tick_age_s": "last_tick(): |now - tick time|",
    "spread_points": "last_tick(): (ask - bid) / point",
    "stage": "risk_<account>.json stage (the gate's stage machine then runs on this read)",
    "supervisor_halt": "Supervisor.engine_should_halt(state): halt flag, missing, unreadable or stale heartbeat",
    "owner_halt": "ApprovalBus.control().halted",
    "in_blackout": "the engine's calendar (store calendar_events, tier 1) and news shocks; unreadable refuses",
    "dq_error": "a failed tick or bars read, or stale bars",
    "stale_bars": "get_bars(1m): last bar older than one decision period",
    "data_recovering": "constant False: engine-only state (the 60 s healthy resume after an engine data error)",
    "in_rollover": "server clock (accounts.yaml server_tz): +-rollover_min of 00:00",
    "weekend": "server clock: Friday weekend_cut until the week reopens",
    "open_lots": "account_positions(): summed lots",
    "open_notional": "account_positions(): lots x contract x mid (USD)",
    "other_lots": "Supervisor.read_state(state) exposure of the other accounts",
    "other_notional": "Supervisor.read_state(state) exposure of the other accounts",
    "other_equity": "Supervisor.read_state(state) exposure of the other accounts",
    "combined_size_down": "Supervisor.read_state(state)['size_down']; a missing key refuses",
    "account_class": "classifier_<account>.json class; missing or unreadable is unknown (refused)",
    "drift_halt": "drift.json system_halt; unreadable is a halt",
    "tick_ahead_s": "last_tick(): how far the tick is stamped ahead of now",
    "clock_mismatch": "server_clock.assess on the tick; no tick is a mismatch",
    "market_closed": "trading_sessions() session table (the static table when the broker cannot say)",
    "tick_error": "last_tick() raised",
    "tier2_blackout": "risk/windows.py on the calendar, when the window is enabled",
    "reopen_window": "risk/windows.py on the session table, when enabled; an unusable table is inside the window",
    "holiday_thin": "risk/windows.py holiday list, when enabled",
    "calendar_unavailable": "risk/windows.py calendar_gap and the holiday list, when enabled",
    "bridge_incompatible": "bridge_compat.check_bridge verdict: anything but ok",
    "open_risk": "account_positions() remaining stop risk (risk/open_risk.py); unknown when it cannot be priced",
    "open_long": "account_positions(): long count",
    "open_short": "account_positions(): short count",
    "equity_unconfirmed": "risk_<account>.json stage2_pending_since (ADR 0016)",
}


class SourceError(RuntimeError):
    """An AccountState source could not be read: the smoke refuses (exit 4), never trades on a guess."""


class SmokeConfig(Record):
    """What the smoke reads from settings.yaml and accounts.yaml (main wires them; tests pass their own)."""
    limits: RiskLimits
    phase_file: Path                  # state/phase_state.json (ADR 0021 F1: the gate reads it)
    approval_window_s: int = Field(90, gt=0)   # settings risk.approval_window_seconds
    blackout_before_min: int = 15     # settings risk.blackout (tier 1), as the engine
    blackout_after_min: int = 30
    shock_blackout_min: int = 30      # settings news.shock_blackout_min
    shock_min_relevance: float = 0.7
    decision_tf: str = "15m"          # the engine's stale-bars period (EngineConfig.decision_tf)
    rollover_min: int = 5             # EngineConfig.rollover_min
    weekend_cut: str = "21:30"        # EngineConfig.weekend_cut


class SmokeIO(Record):
    """The clocks, the operator's terminal and the reads that are not the broker's (tests replace them)."""
    monotonic: Callable[[], float]
    sleep: Callable[[float], Any]
    out: Callable[[str], Any] = print
    engine_state: Callable[[str], str]                 # `systemctl is-active goldbot-engine@<account>`
    calendar: Callable[[pd.Timestamp], pd.DataFrame]   # calendar_events around now (raises when unreadable)
    news: Callable[[pd.Timestamp], pd.DataFrame]       # news of the last day (raises when unreadable)
    alert: Callable[[str], Any] | None = None          # owner alert; None: ApprovalBus.notify_owner (Telegram)


class SmokeReport(Record):
    account_id: str
    exit_code: int
    outcome: str
    login: str = "unknown"            # masked: `****123`
    server: str | None = None
    balance: float | None = None
    currency: str | None = None
    bid: float | None = None
    ask: float | None = None
    client_order_id: str | None = None
    lots: float | None = None
    sl: float | None = None
    tp: float | None = None
    position_id: int | None = None
    gate_reasons: list[str] = Field(default_factory=list)
    lines: list[str] = Field(default_factory=list)


def mask_login(login: int | None) -> str:
    """`****` and the last three digits: enough to tell accounts apart, never the login (the output goes on #34)."""
    return "unknown" if login is None else "****" + str(int(login))[-3:]


def smoke_magic(acc: Account) -> int:
    return acc.magic_base + SMOKE_MAGIC_OFFSET


def client_order_id(acc: Account, now: pd.Timestamp) -> str:
    """`<account>-<epoch_ns>-smoke`; its last COMMENT_LEN characters are the order comment (the X7 look-up key)."""
    return f"{acc.account_id}-{int(epoch_ns(pd.DatetimeIndex([now]))[0])}-smoke"


# ---------------------------------------------------------------------------------------------- preconditions
def systemctl_state(account_id: str) -> str:
    """`systemctl is-active goldbot-engine@<account>`'s answer ("inactive", "active", "failed", ...); "unknown" when
    systemctl is missing or does not answer (refused: only "inactive" lets the smoke run)."""
    try:
        p = subprocess.run(["systemctl", "is-active", f"goldbot-engine@{account_id}"], capture_output=True, text=True,
                           timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return p.stdout.strip() or "unknown"


def engine_running(acc: Account, state_dir: Path, now: pd.Timestamp, io: SmokeIO) -> str | None:
    """Why the engine may be running (S3), or None when it is stopped: its unit not `inactive`, or a loop heartbeat
    younger than HEARTBEAT_MIN_AGE_S (or unreadable, or stamped ahead of now). No heartbeat file: never ran here."""
    state = io.engine_state(acc.account_id)
    if state != "inactive":
        return f"goldbot-engine@{acc.account_id} is {state}, not inactive: stop the engine first (RUNBOOK: bridge smoke)"
    f = loop_heartbeat_path(state_dir, acc.account_id)
    if not f.exists():
        return None
    try:
        ts = json.loads(f.read_text()).get("ts")
    except (OSError, ValueError, AttributeError):
        return "the engine loop heartbeat is unreadable"
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
        return "the engine loop heartbeat has no valid time"
    age = now.timestamp() - float(ts)
    if age < HEARTBEAT_MIN_AGE_S:
        return f"the engine loop beat {age:.0f} s ago (needs {HEARTBEAT_MIN_AGE_S:.0f} s): the engine may be running"
    return None


def acquire_lock(state_dir: Path, account_id: str) -> int:
    """flock(LOCK_EX | LOCK_NB) on state/.smoke-<account>.lock (mode 0600); returns the descriptor (`release_lock`).
    Opened O_NOFOLLOW | O_CLOEXEC (security review: a planted symlink is refused, no child inherits the lock) and only
    a regular file this user owns is locked. Raises OSError when it cannot be created or taken, BlockingIOError when
    another smoke holds it. Unix only: the smoke runs on the brain."""
    import fcntl
    import stat
    path = Path(state_dir) / f".smoke-{account_id}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise PermissionError(f"{path.name} is not a regular file owned by this user")
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise
    return fd


def release_lock(fd: int) -> None:
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def smoke_lock(state_dir: Path, account_id: str) -> Iterator[None]:
    """`acquire_lock` held until the block ends."""
    fd = acquire_lock(state_dir, account_id)
    try:
        yield
    finally:
        release_lock(fd)


# ---------------------------------------------------------------------------------------------- look-ups (S8, S9)
def find_smoke(broker: Any, magic: int, since: pd.Timestamp, comment: str | None = None
               ) -> tuple[list[Position], pd.DataFrame]:
    """Open smoke positions (this magic, and this comment when given) and the smoke's deals since `since`."""
    positions = [p for p in broker.positions() if int(p.magic) == magic and (comment is None or p.comment == comment)]
    deals = broker.deals_since(since)
    if deals is None or deals.empty or "magic" not in deals.columns:
        return positions, pd.DataFrame()
    mine = deals[pd.to_numeric(deals["magic"], errors="coerce") == magic]
    if comment is not None:
        mine = mine[mine["comment"] == comment] if "comment" in mine.columns else mine.iloc[0:0]
    return positions, mine.reset_index(drop=True)


def close_position(broker: Any, position_id: int, io: SmokeIO) -> bool:
    """Close one position, CLOSE_ATTEMPTS times at most; True once the broker confirms or no longer lists it. Exits are
    never gated: nothing here reads the RiskGate, a halt or the approval bus."""
    for attempt in range(CLOSE_ATTEMPTS):
        res: OrderResult | None = None
        try:
            res = broker.close(position_id)
        except Exception as exc:
            log.warning("close of %s raised (%s), attempt %d", position_id, type(exc).__name__, attempt + 1)
        if res is not None and res.ok:
            return True
        try:
            if all(p.position_id != position_id for p in broker.positions()):
                return True
        except Exception as exc:
            log.warning("positions unreadable after a close (%s)", type(exc).__name__)
        if attempt + 1 < CLOSE_ATTEMPTS:
            io.sleep(CLOSE_RETRY_S)
    return False


# ---------------------------------------------------------------------------------------------- AccountState (S2)
def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SourceError(f"{path.name} unreadable ({type(exc).__name__})") from exc


def _risk_file(state_dir: Path, account_id: str) -> dict[str, Any]:
    path = state_dir / f"risk_{account_id}.json"
    if not path.exists():
        raise SourceError(f"{path.name} missing: no high-water mark or day start to judge the limits by (start the "
                          f"engine once, then stop it)")
    d = _json(path)
    try:
        return {"hwm": float(d["balance_closed_hwm"]), "day": float(d["day_start_equity"]),
                "week": float(d["week_start_equity"]), "stage": Stage(d.get("stage", Stage.NORMAL.value)),
                "unconfirmed": bool(d.get("stage2_pending_since")),
                "last_reset": pd.Timestamp(d["last_reset"]) if d.get("last_reset") else None}
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError(f"{path.name} malformed ({type(exc).__name__})") from exc


def _supervisor(state_dir: Path, account_id: str) -> tuple[bool, dict[str, float], bool]:
    from goldbot.risk.supervisor import Supervisor
    halt, _ = Supervisor.engine_should_halt(state_dir)
    sup = Supervisor.read_state(state_dir)
    if "size_down" not in sup:
        raise SourceError("supervisor.json has no size_down (missing, unreadable or a supervisor error): is the "
                          "supervisor running?")
    others = [v for k, v in (sup.get("exposure") or {}).items() if k != account_id and isinstance(v, dict)]
    try:
        other = {k: sum(float(v.get(k, 0.0)) for v in others) for k in ("lots", "notional", "equity")}
    except (TypeError, ValueError) as exc:
        raise SourceError("supervisor.json exposure malformed") from exc
    return bool(halt), other, bool(sup["size_down"])


def _account_class(state_dir: Path, account_id: str) -> str:
    path = state_dir / f"classifier_{account_id}.json"
    try:
        c = str(json.loads(path.read_text()).get("class") or "unknown") if path.exists() else "unknown"
    except (OSError, ValueError, AttributeError):
        c = "unknown"
    return c if c in ("raw", "standard", "unknown") else "unknown"


def _drift_halt(state_dir: Path) -> bool:
    path = state_dir / "drift.json"
    if not path.exists():
        return False
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return True
    return not isinstance(d, dict) or bool(d.get("system_halt"))


def _open_book(positions: list[Position], sym: SymbolInfo, tick: Tick | None, fx: float | None
               ) -> dict[str, Any]:
    from goldbot.risk.open_risk import remaining_stop_risk
    lots = round(sum(p.lots for p in positions), 6)
    mid = (tick.bid + tick.ask) / 2 if tick is not None else math.nan
    risk: float | None = 0.0
    for p in positions:
        r = None if tick is None or p.symbol != sym.name else remaining_stop_risk(
            side=p.side, lots=p.lots, sl=p.sl, bid=tick.bid, ask=tick.ask, contract=sym.contract_size, fx=fx)
        risk = None if r is None or risk is None else risk + r
    return {"open_positions": len(positions), "open_lots": lots, "open_notional": lots * sym.contract_size * mid,
            "open_long": sum(p.side > 0 for p in positions), "open_short": sum(p.side < 0 for p in positions),
            "open_risk": risk}


def _windows(limits: RiskLimits, sessions: SessionTable, events: pd.DataFrame, tick_ts: pd.Timestamp,
             now: pd.Timestamp) -> dict[str, bool]:
    """BACKLOG 17/18 entry windows as the engine raises them (`Engine._window_flags`), each only when enabled; a fault
    fails closed (calendar_unavailable, or inside the reopen window)."""
    from goldbot.data.econ_calendar import blackout_window
    from goldbot.risk.windows import calendar_gap, load_holidays, reopen_window, server_date
    tier2 = reopen = thin = unavailable = False
    if limits.tier2.enabled:
        tier2 = blackout_window(events, tick_ts, limits.tier2.before_min, limits.tier2.after_min, tier=2) is not None
        unavailable = calendar_gap(events, tick_ts) is not None
    if limits.reopen.enabled:
        try:
            reopen = reopen_window(sessions, now, limits.reopen)
        except Exception:
            reopen = True
    if limits.holidays.enabled:
        try:
            hl = load_holidays(limits.holidays.file)
            day = server_date(sessions, now)
            unavailable = unavailable or not hl.covers(day)
            thin = hl.covers(day) and hl.thin(day) is not None
        except Exception:
            unavailable = True
    return {"tier2_blackout": tier2, "reopen_window": reopen, "holiday_thin": thin, "calendar_unavailable": unavailable}


def _server_clock(now: pd.Timestamp, server_tz: str, cfg: SmokeConfig) -> tuple[bool, bool]:
    """(in_rollover, weekend) as the engine's `_server_clock`: +-rollover_min of 00:00 server; Friday weekend_cut
    server time until the week reopens."""
    srv = server_wall(now, server_tz)[0]
    mins = srv.hour * 60 + srv.minute + srv.second / 60
    h, m = (int(x) for x in cfg.weekend_cut.split(":"))
    friday_cut = srv.weekday() == 4 and mins >= h * 60 + m
    return min(mins, 1440 - mins) <= cfg.rollover_min, friday_cut or srv.weekday() >= 5


def smoke_state(broker: Any, bus: ApprovalBus, acc: Account, state_dir: Path, *, now: pd.Timestamp, cfg: SmokeConfig,
                io: SmokeIO, compat: Any, info: AccountInfo, sym: SymbolInfo, tick: Tick | None,
                sessions: SessionTable, fx: float | None) -> AccountState:
    """The only builder of the smoke's AccountState (plan S2): every field set explicitly from the source
    STATE_SOURCES names, never a record default. A source that cannot be read raises SourceError (the smoke refuses);
    a fault the gate knows how to refuse sets its flag instead. Read-only: nothing is written (S5: a stale day start
    is reset in memory by `new_day`, never saved; the engine saves its own on its next start)."""
    from goldbot.data.econ_calendar import blackout_window
    from goldbot.data.news import shock_window
    from goldbot.ops import server_clock
    risk = _risk_file(state_dir, acc.account_id)
    try:
        positions = list(broker.account_positions())
    except Exception as exc:
        raise SourceError(f"account positions unreadable ({type(exc).__name__})") from exc
    try:
        events = io.calendar(now)
    except Exception as exc:
        raise SourceError(f"calendar unreadable ({type(exc).__name__}): the news blackout cannot be judged") from exc
    if events is None or events.empty:
        raise SourceError("no calendar events archived around now: the news blackout cannot be judged")
    shock = None
    if cfg.shock_blackout_min > 0:
        try:
            shock = shock_window(io.news(now), now, cfg.shock_blackout_min, cfg.shock_min_relevance)
        except Exception as exc:
            raise SourceError(f"news unreadable ({type(exc).__name__}): a news shock cannot be judged") from exc
    sup_halt, other, size_down = _supervisor(state_dir, acc.account_id)
    try:
        bars = broker.get_bars(acc.symbol, BARS_TF, BARS_N)
        last = pd.Timestamp(pd.to_datetime(bars["ts_utc"], utc=True).max()) if not bars.empty else None
        bars_failed = False
    except Exception as exc:
        log.warning("bars unreadable (%s)", type(exc).__name__)
        last, bars_failed = None, True
    stale_bars = last is None or (now - last).total_seconds() > tf_seconds(cfg.decision_tf) + tf_seconds(BARS_TF)
    tick_ts = tick.ts_utc if tick is not None else now
    age = (now - tick.ts_utc).total_seconds() if tick is not None else math.inf
    in_rollover, weekend = _server_clock(now, acc.server_tz, cfg)
    try:
        closed = sessions.entry_block(now) is not None
    except Exception:
        closed = True
    clock = server_clock.assess(acc.account_id, acc.server_tz, tick.ts_utc, now, sessions) if tick is not None \
        else None
    st = AccountState(
        equity=info.equity, balance_closed_hwm=risk["hwm"], day_start_equity=risk["day"],
        week_start_equity=risk["week"], margin_used=info.margin,
        last_tick_age_s=abs(age), tick_ahead_s=max(0.0, -age),
        spread_points=(tick.ask - tick.bid) / cfg.limits.point if tick is not None else math.inf,
        stage=risk["stage"], equity_unconfirmed=risk["unconfirmed"],
        supervisor_halt=sup_halt, owner_halt=bus.control().halted,
        in_blackout=shock is not None or blackout_window(events, tick_ts, cfg.blackout_before_min,
                                                         cfg.blackout_after_min) is not None,
        dq_error=stale_bars or bars_failed or tick is None, stale_bars=stale_bars,
        data_recovering=False,   # engine-only state (runner: healthy_resume_s after an engine data error)
        in_rollover=in_rollover, weekend=weekend,
        other_lots=other["lots"], other_notional=other["notional"], other_equity=other["equity"],
        combined_size_down=size_down, account_class=_account_class(state_dir, acc.account_id),
        drift_halt=_drift_halt(state_dir), clock_mismatch=clock is None or clock.status == "mismatch",
        market_closed=closed, tick_error=tick is None, bridge_incompatible=getattr(compat, "status", None) != "ok",
        **_windows(cfg.limits, sessions, events, tick_ts, now),
        **_open_book(positions, sym, tick, fx))
    new_day(st, now, risk["last_reset"])        # in memory only (S5)
    return st


# ---------------------------------------------------------------------------------------------- the run
class _Run:
    """One smoke run's state: the report it builds and the reads it repeats at the click."""

    def __init__(self, broker: Any, bus: ApprovalBus, acc: Account, state_dir: Path, now: Callable[[], pd.Timestamp],
                 cfg: SmokeConfig, compat: Any, io: SmokeIO) -> None:
        self.broker, self.bus, self.acc, self.state_dir = broker, bus, acc, Path(state_dir)
        self.now, self.cfg, self.compat, self.io = now, cfg, compat, io
        self.gate = RiskGate(cfg.limits)
        self.known: set[int] = set()        # smoke position ids seen before the send (NEW-1)
        self.report = SmokeReport(account_id=acc.account_id, exit_code=EXIT_FAILED, outcome="started")

    def say(self, line: str) -> None:
        self.report.lines.append(line)
        self.io.out(line)

    def end(self, code: int, outcome: str, line: str | None = None) -> SmokeReport:
        if line:
            self.say(line)
        self.report.exit_code, self.report.outcome = code, outcome
        self.say(f"bridge smoke {self.acc.account_id}: {outcome} (exit {code})")
        return self.report

    def alert(self, text: str) -> None:
        try:
            (self.io.alert or self.bus.notify_owner)(text)
        except Exception as exc:                       # the exit code and the output still say it
            log.warning("owner alert not sent (%s)", type(exc).__name__)

    def tick(self) -> Tick | None:
        try:
            return self.broker.last_tick(self.acc.symbol)
        except Exception as exc:
            log.warning("tick unreadable (%s)", type(exc).__name__)
            return None

    def decide(self, info: AccountInfo, sym: SymbolInfo) -> tuple[GateDecision, Tick | None]:
        """Fresh tick, AccountState and RiskGate.check_operational (raises SourceError on an unreadable source)."""
        now, tick = self.now(), self.tick()
        fx = self.fx(sym, tick)
        sessions = SessionTable.from_broker(self.broker, self.acc.symbol, now,
                                            fallback=SessionTable(server_tz=self.acc.server_tz))
        st = smoke_state(self.broker, self.bus, self.acc, self.state_dir, now=now, cfg=self.cfg, io=self.io,
                         compat=self.compat, info=info, sym=sym, tick=tick, sessions=sessions, fx=fx)
        intent = OperationalIntent(side=1, price=tick.ask if tick is not None else math.nan,
                                   contract_oz=sym.contract_size, volume_step=sym.volume_step,
                                   volume_min=sym.volume_min, volume_max=sym.volume_max,
                                   stops_level_points=sym.stops_level_points, account_currency=info.currency,
                                   acct_per_usd=fx, account_mode=self.acc.mode, phase_file=self.cfg.phase_file)
        return self.gate.check_operational(intent, st, now, broker_margin(self.broker, self.acc.symbol)), tick

    def fx(self, sym: SymbolInfo, tick: Tick | None) -> float | None:
        """Account currency per USD of XAUUSD P&L from the broker (as the engine's `_usd_to_account`); None unknown."""
        if tick is None:
            return None
        try:
            lpl = self.broker.loss_per_lot(self.acc.symbol, 1, tick.ask, 1.0)
            v = float(lpl) / sym.contract_size if lpl is not None else math.nan
        except Exception:
            return None
        return v if math.isfinite(v) and v > 0 else None

    def preconditions(self) -> str | None:
        return engine_running(self.acc, self.state_dir, self.now(), self.io)

    # ------------------------------------------------------------------ steps
    def run(self) -> SmokeReport:
        b, acc = self.broker, self.acc
        try:
            info: AccountInfo = b.account()
        except Exception as exc:
            return self.end(EXIT_PRECONDITION, "refused: the terminal cannot be read",
                            f"refused: account unreadable through the bridge ({type(exc).__name__})")
        self.report.login, self.report.server = mask_login(info.login), str(info.server)
        self.say(f"terminal: login {mask_login(info.login)} on {info.server} ({info.trade_mode or 'unknown'})")
        why = verify_terminal_account(acc, info)
        if why or acc.mode != "demo" or info.trade_mode != "demo":
            return self.end(EXIT_NOT_DEMO, "refused: not a demo terminal of this account",
                            f"refused: {why or f'{acc.account_id} is {acc.mode}, the terminal {info.trade_mode}'}")
        self.report.balance, self.report.currency = float(info.balance), str(info.currency)
        self.say(f"balance {info.balance:,.2f} {info.currency}, equity {info.equity:,.2f}")
        tick = self.tick()
        if tick is not None:
            self.report.bid, self.report.ask = tick.bid, tick.ask
            age = (self.now() - tick.ts_utc).total_seconds()
            self.say(f"{acc.symbol} tick bid {tick.bid} ask {tick.ask} at {tick.ts_utc.isoformat()} ({age:.1f} s old)")
        else:
            self.say(f"{acc.symbol} tick unreadable")
        try:                                           # only the acquisition maps to a lock refusal (review LOW-4)
            fd = acquire_lock(self.state_dir, acc.account_id)
        except BlockingIOError:
            return self.end(EXIT_PRECONDITION, "refused: another bridge smoke holds the lock")
        except OSError as exc:
            return self.end(EXIT_PRECONDITION, f"refused: the smoke lock cannot be taken ({type(exc).__name__})")
        try:
            return self.locked(info)
        finally:
            release_lock(fd)

    def locked(self, info: AccountInfo) -> SmokeReport:
        b, acc, magic = self.broker, self.acc, smoke_magic(self.acc)
        if why := self.preconditions():
            return self.end(EXIT_PRECONDITION, "refused: precondition", f"refused: {why}")
        # SRE S1 and follow-up 1: F4 lives on the MT5 box, so both commits must be known AND equal. Stricter than
        # `BridgeCompat.commit_differs` (False when either is unknown), which stays the engine's rule
        bridge_c, brain_c = getattr(self.compat, "bridge_commit", None), getattr(self.compat, "brain_commit", None)
        if not bridge_c or not brain_c or bridge_c != brain_c:
            return self.end(EXIT_PRECONDITION, "refused: the MT5 box is not known to run this commit",
                            f"refused: the bridge runs {bridge_c or 'an unknown commit'}, this brain "
                            f"{brain_c or 'an unknown commit'}: deploy this commit to the MT5 box first")
        try:
            sym: SymbolInfo = b.symbol_info(acc.symbol)
            left, earlier = find_smoke(b, magic, self.now() - LOOKUP_WINDOW)
        except Exception as exc:
            return self.end(EXIT_PRECONDITION, "refused: the terminal cannot be read",
                            f"refused: symbol or smoke look-up unreadable ({type(exc).__name__})")
        # NEW-1: every smoke position known before this run's send is never taken for its fill
        self.known = {int(p.position_id) for p in left}
        if not earlier.empty and "position_id" in earlier.columns:
            self.known |= {int(p) for p in pd.to_numeric(earlier["position_id"], errors="coerce").dropna()}
            self.say(f"{earlier['position_id'].nunique()} earlier smoke position(s) in the last 24 h")
        for p in left:                                 # S8: an earlier smoke's position is closed first
            self.say(f"closing a leftover smoke position {p.position_id}")
            if not close_position(b, p.position_id, self.io):
                self.alert(f"bridge smoke {acc.account_id}: leftover smoke position {p.position_id} is LEFT OPEN; "
                           f"close it in the terminal")
                return self.end(EXIT_LEFT_OPEN, "LEFT OPEN: a leftover smoke position could not be closed; no new "
                                "order", f"position {p.position_id} still open: close it in the terminal")
        try:
            gd, tick = self.decide(info, sym)
        except SourceError as exc:
            return self.end(EXIT_PRECONDITION, "refused: a source is unreadable", f"refused: {exc}")
        if not gd.allowed or tick is None:
            self.report.gate_reasons = list(gd.reasons)
            return self.end(EXIT_GATE, "refused by the RiskGate", f"RiskGate refused: {', '.join(gd.reasons)}")
        return self.approve_and_trade(info, sym, gd, tick)

    def approve_and_trade(self, info: AccountInfo, sym: SymbolInfo, gd: GateDecision, tick: Tick) -> SmokeReport:
        acc = self.acc
        cid = client_order_id(acc, self.now())
        self.report.client_order_id = cid
        sl, tp = self.stops(tick.ask, gd.stop_distance, sym)
        prop = Proposal(proposal_id=cid, account_id=acc.account_id, agent_id=SMOKE_AGENT, side=1, lots=gd.lots,
                        entry=tick.ask, stop=sl, target=tp, p=0.0, ev_r=0.0,
                        spread_points=(tick.ask - tick.bid) / self.cfg.limits.point,
                        top_features=[("bridge smoke: operational order, no model", 0.0)],
                        window_s=self.cfg.approval_window_s, currency=info.currency,
                        risk_amount=gd.risk_amount, risk_fraction=gd.risk_fraction)
        center = ApprovalCenter(set(), bus=self.bus, monotonic=self.io.monotonic)
        center.propose(prop)
        self.say(f"approval needed: BUY {gd.lots:.2f} {acc.symbol} sl {sl} tp {tp}, proposal {cid}; approve on "
                 f"Telegram or the dashboard within {self.cfg.approval_window_s} s")
        try:
            decided = self.wait(center)
        finally:
            for p in list(center.pending.values()):    # interrupted: the card is closed, a late click acts on nothing
                p.outcome = Outcome.EXPIRED
                self.bus.archive(p)
        if decided.outcome != Outcome.APPROVED:
            return self.end(EXIT_UNAPPROVED, f"not approved ({decided.outcome.value if decided.outcome else 'none'})"
                            ": nothing sent")
        # F6 / A3: at the click the preconditions and the gate run again on fresh reads
        if why := self.preconditions():
            self.refuse_at_click(decided, ["engine_running"])
            return self.end(EXIT_PRECONDITION, "refused at approval: precondition", f"refused: {why}")
        try:
            gd2, tick2 = self.decide(self.broker.account(), sym)
        except SourceError as exc:
            self.refuse_at_click(decided, ["source_unreadable"])
            return self.end(EXIT_PRECONDITION, "refused at approval: a source is unreadable", f"refused: {exc}")
        except Exception as exc:
            self.refuse_at_click(decided, ["account_unreadable"])
            return self.end(EXIT_PRECONDITION, "refused at approval: the terminal cannot be read",
                            f"refused: {type(exc).__name__}")
        if not gd2.allowed or tick2 is None:
            self.report.gate_reasons = list(gd2.reasons)
            self.refuse_at_click(decided, list(gd2.reasons))
            return self.end(EXIT_GATE, "refused by the RiskGate at approval",
                            f"RiskGate refused at approval: {', '.join(gd2.reasons)}")
        return self.send(cid, sym, gd2, tick2)

    def wait(self, center: ApprovalCenter) -> Proposal:
        while True:
            done = center.poll_bus() or center.sweep_expired()
            if done:
                return done[0]
            self.io.sleep(APPROVAL_POLL_S)

    def refuse_at_click(self, p: Proposal, reasons: list[str]) -> None:
        p.gate_refusal = reasons           # the dashboard shows "refused at approval", not "approved"
        try:
            self.bus.archive(p)
        except OSError as exc:
            log.warning("refused proposal not archived (%s)", type(exc).__name__)

    @staticmethod
    def stops(ask: float, distance: float, sym: SymbolInfo) -> tuple[float, float]:
        """S11: a buy's stop below and target above the ask by at least `distance`, rounded outwards to the price's
        digits so rounding never brings either closer."""
        q = 10 ** sym.digits
        return math.floor((ask - distance) * q + 1e-6) / q, math.ceil((ask + distance) * q - 1e-6) / q

    def send(self, cid: str, sym: SymbolInfo, gd: GateDecision, tick: Tick) -> SmokeReport:
        b, acc = self.broker, self.acc
        sl, tp = self.stops(tick.ask, gd.stop_distance, sym)
        self.report.lots, self.report.sl, self.report.tp = gd.lots, sl, tp
        oi = OrderIntent(client_order_id=cid, symbol=acc.symbol, side=1, lots=gd.lots, sl=sl, tp=tp,
                         magic=smoke_magic(acc), comment=cid[-COMMENT_LEN:])
        self.say(f"sending BUY {gd.lots:.2f} {acc.symbol} at about {tick.ask}, sl {sl}, tp {tp}")
        sent_at = self.now()
        try:
            res: OrderResult | None = b.place_order(oi)
        except Exception as exc:                       # S4: it may or may not be at the broker; never sent again
            self.say(f"the send raised {type(exc).__name__}: looking for the order by its comment")
            return self.resolve_unknown(oi, sym, sent_at)
        from goldbot.engine.runner import DEFINITE_REJECT_RETCODES
        if res is None:                                # review LOW-4: no result is ambiguous, never an assert
            self.say("the send returned no result: looking for the order by its comment")
            return self.resolve_unknown(oi, sym, sent_at)
        if res.ok and res.position_id is not None:
            self.say(f"filled: position {res.position_id}, {res.filled_lots} lots at {res.price}")
            return self.settle(int(res.position_id), oi, sym)
        if not res.ok and res.retcode in DEFINITE_REJECT_RETCODES:
            return self.end(EXIT_FAILED, f"broker rejected the order (retcode {res.retcode}): nothing placed")
        self.say(f"order result ambiguous (retcode {res.retcode}): looking for it by its comment")
        return self.resolve_unknown(oi, sym, sent_at)

    @staticmethod
    def unverified(pos: Position | None, oi: OrderIntent, sym: SymbolInfo) -> str | None:
        """Why the fill's protection is not the order's (S8, trading-safety MEDIUM-2), or None when it is: the
        position listed, its sl and tp equal to the order's within one point, and sl < open < tp (a buy)."""
        if pos is None:
            return "the fill is not listed, so its stop and target were never seen"
        if not pos.sl or not pos.tp:
            return f"the fill has no {'stop' if not pos.sl else 'target'} at the broker"
        tol = sym.point + 1e-9
        if abs(pos.sl - oi.sl) > tol or abs(pos.tp - oi.tp) > tol:
            return f"the fill carries sl {pos.sl} tp {pos.tp}, the order sent sl {oi.sl} tp {oi.tp}"
        if not pos.sl < pos.open_price < pos.tp:
            return f"the fill's sl {pos.sl} and tp {pos.tp} are not either side of its open {pos.open_price}"
        return None

    def settle(self, position_id: int, oi: OrderIntent, sym: SymbolInfo) -> SmokeReport:
        """S8: the fill's stop and target verified against the order (`unverified`), then closed (never gated). A fill
        that is not listed or does not match is closed all the same and the smoke exits 1."""
        self.report.position_id = position_id
        try:
            pos = next((p for p in self.broker.positions() if p.position_id == position_id), None)
        except Exception as exc:
            log.warning("positions unreadable after the fill (%s)", type(exc).__name__)
            pos = None
        why = self.unverified(pos, oi, sym)
        if why:
            self.say(f"position {position_id}: {why}: closing it now")
        else:
            assert pos is not None
            self.say(f"position {position_id} carries sl {pos.sl} tp {pos.tp} as sent: closing it")
        if not close_position(self.broker, position_id, self.io):
            self.alert(f"bridge smoke {self.acc.account_id}: position {position_id} is LEFT OPEN after "
                       f"{CLOSE_ATTEMPTS} close attempts; close it in the terminal")
            return self.end(EXIT_LEFT_OPEN, "LEFT OPEN", f"position {position_id} still open: close it in the terminal")
        if why:
            return self.end(EXIT_FAILED, f"closed, unverified: {why}")
        return self.end(EXIT_OK, "opened and closed the minimum lot")

    def lookup(self, oi: OrderIntent, sent_at: pd.Timestamp) -> tuple[list[Position], set[int], pd.DataFrame]:
        """(open smoke positions of this order, its entry-deal position ids, every deal read). The deals are read
        FIRST and the positions after them (trading-safety HIGH-1: a fill landing between the two reads is then listed,
        never taken for a closed one). An order is matched by its comment, or, for a broker that rewrites comments
        (review LOW-5), by its magic with an open at or after the send (less SEND_SLACK_S of clock skew).
        NEW-1 (trading-safety re-verify): the fallback never matches a smoke position known before the send
        (`self.known`: leftovers and the 24 h look-up) nor one whose comment is another smoke's (`...-smoke`), so an
        earlier run's trade is never reported as this order's fill."""
        since = sent_at - pd.Timedelta(seconds=SEND_SLACK_S)

        def fallback(comment: Any, opened: pd.Series | pd.Timestamp) -> Any:
            other_smoke = isinstance(comment, str) and comment.endswith("-smoke") and comment != oi.comment
            return (not other_smoke) & (opened >= since)

        deals = self.broker.deals_since(self.now() - LOOKUP_WINDOW)
        entries: set[int] = set()
        if deals is not None and not deals.empty and {"magic", "position_id", "ts_utc"} <= set(deals.columns):
            d = deals[pd.to_numeric(deals["magic"], errors="coerce") == oi.magic]
            d = d[~pd.to_numeric(d["position_id"], errors="coerce").isin(list(self.known))]
            comments = d["comment"] if "comment" in d.columns else pd.Series("", index=d.index)
            opened = pd.to_datetime(d["ts_utc"], utc=True)
            ours = (comments == oi.comment) | pd.Series(
                [bool(fallback(c, t)) for c, t in zip(comments, opened)], index=d.index, dtype=bool)
            if "entry" in d.columns:
                ours = ours & (pd.to_numeric(d["entry"], errors="coerce") == ENTRY_IN)
            entries = {int(p) for p in d.loc[ours, "position_id"]}
        else:
            deals = pd.DataFrame()
        positions = [p for p in self.broker.positions() if int(p.magic) == oi.magic
                     and int(p.position_id) not in self.known
                     and (p.comment == oi.comment or fallback(p.comment, pd.Timestamp(p.open_time_utc)))]
        return positions, entries, deals

    @staticmethod
    def closed_by_deals(position_id: int, deals: pd.DataFrame) -> bool:
        """An exit deal (entry OUT, INOUT or OUT_BY) for this position is in the read: it is closed."""
        if deals.empty or "entry" not in deals.columns:
            return False
        rows = deals[pd.to_numeric(deals["position_id"], errors="coerce") == position_id]
        return bool(pd.to_numeric(rows["entry"], errors="coerce").isin(ENTRY_OUT).any())

    def resolve_unknown(self, oi: OrderIntent, sym: SymbolInfo, sent_at: pd.Timestamp) -> SmokeReport:
        """S4: poll deals then positions for the order for order_timeout_s + ORDER_MAX_AGE_S; never re-send. A listed
        position is settled; it is reported closed only when an exit deal for its position id is seen; otherwise the
        poll goes on and ends in exit 6 UNKNOWN with an owner alert."""
        from goldbot.execution.bridge import ORDER_MAX_AGE_S
        wait_s = float(getattr(self.broker, "order_timeout_s", DEFAULT_ORDER_TIMEOUT_S)) + ORDER_MAX_AGE_S
        start = self.io.monotonic()
        while True:
            try:
                found, entries, deals = self.lookup(oi, sent_at)
            except Exception as exc:
                log.warning("look-up after the send failed (%s)", type(exc).__name__)
                found, entries, deals = [], set(), pd.DataFrame()
            if found:
                self.say(f"found the order: position {found[0].position_id}")
                return self.settle(found[0].position_id, oi, sym)
            closed = sorted(p for p in entries if self.closed_by_deals(p, deals))
            if closed:
                self.report.position_id = closed[0]
                return self.end(EXIT_OK, "the order filled and is already closed (its entry and exit deals were found)")
            if entries:
                self.say(f"entry deal for position(s) {sorted(entries)} seen, not listed and no exit deal yet")
            if self.io.monotonic() - start >= wait_s:
                break
            self.io.sleep(POLL_EVERY_S)
        self.alert(f"bridge smoke {self.acc.account_id}: UNKNOWN whether order {oi.comment} filled after "
                   f"{wait_s:.0f} s; check the terminal for magic {oi.magic}")
        return self.end(EXIT_UNKNOWN, "UNKNOWN: not found after the send; check the terminal",
                        f"no open position or closed trade for comment {oi.comment} after {wait_s:.0f} s; never sent "
                        f"again")


def run_smoke(broker: Any, bus: ApprovalBus, acc: Account, state_dir: str | Path, *, now: Callable[[], pd.Timestamp],
              cfg: SmokeConfig, compat: Any, io: SmokeIO) -> SmokeReport:
    """One bridge smoke (see the module docstring); returns the report, `exit_code` included. Never raises for a
    broker fault after an order was sent (the exit code says what is known)."""
    return _Run(broker, bus, acc, Path(state_dir), now, cfg, compat, io).run()


def main(argv: list[str]) -> int:
    """`bridge-smoke <account> [--state DIR]`: wires the keyring's bridge endpoint, settings and the data store."""
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run bridge-smoke",
                                 description="One minimum-lot round trip on a DEMO terminal through the MT5 bridge, "
                                             "after one owner click (BACKLOG 1). Stop the engine first; keep the "
                                             "supervisor running. Exit 0 ok, 2 not demo, 3 not approved, 4 "
                                             "precondition, 5 LEFT OPEN, 6 UNKNOWN, 7 RiskGate refused.")
    ap.add_argument("account")
    ap.add_argument("--state", default="state", help="state directory (default: state)")
    a = ap.parse_args(argv)
    import time

    from goldbot.config import load_settings
    from goldbot.data.store import Store
    from goldbot.engine.runner import EngineConfig
    from goldbot.execution.bridge import RemoteBroker
    from goldbot.ops import accounts
    from goldbot.ops.bridge_compat import check_bridge, repo_commit
    from goldbot.ops.services.engine import engine_limits
    accs = accounts.load_accounts()
    acc = accs.get(a.account)
    if acc is None:
        print(f"unknown account {a.account!r} (config/accounts.yaml)")
        return EXIT_PRECONDITION
    if acc.mode != "demo":
        print(f"refused: {acc.account_id} is {acc.mode}; the bridge smoke runs on demo accounts only (ADR 0021)")
        return EXIT_NOT_DEMO
    endpoint = accounts.bridge_endpoint(acc.account_id)
    if endpoint is None:
        print(f"refused: no bridge endpoint for {acc.account_id} in the keyring (mt5-bridge-url/-token)")
        return EXIT_PRECONDITION
    settings = load_settings()
    defaults = EngineConfig.model_fields
    cfg = SmokeConfig(limits=engine_limits(settings, acc), phase_file=accounts.PHASE_FILE,
                      approval_window_s=settings.risk.approval_window_seconds,
                      blackout_before_min=settings.risk.blackout.before_min,
                      blackout_after_min=settings.risk.blackout.after_min,
                      shock_blackout_min=settings.news.shock_blackout_min,
                      shock_min_relevance=settings.news.shock_min_relevance,
                      decision_tf=defaults["decision_tf"].default, rollover_min=defaults["rollover_min"].default,
                      weekend_cut=defaults["weekend_cut"].default)
    store = Store(settings.data_root)
    io = SmokeIO(monotonic=time.monotonic, sleep=time.sleep, engine_state=systemctl_state,
                 calendar=lambda t: store.read("calendar_events", start=t - pd.Timedelta(days=1),
                                               end=t + pd.Timedelta(days=2)),
                 news=lambda t: store.read("news", start=t - pd.Timedelta(days=1), end=t))
    broker = RemoteBroker(endpoint[0], endpoint[1], name=f"mt5-remote-{acc.account_id}-smoke")
    compat = check_bridge(endpoint[0], acc.server_tz, repo_commit())
    if compat.status != "ok":
        print(f"bridge: {compat.status}: {compat.reason}")
    report = run_smoke(broker, ApprovalBus(a.state), acc, a.state, now=lambda: pd.Timestamp.now(tz="UTC"), cfg=cfg,
                       compat=compat, io=io)
    return report.exit_code
