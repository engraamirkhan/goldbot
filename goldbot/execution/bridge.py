"""MT5 broker bridge: the engine on one machine, the MT5 terminal on another.

The MetaTrader5 package only talks to a terminal on the same (Windows or Wine) machine. Hosting both on Oracle Cloud's
free tier puts the terminal on a small x86 VM under Wine and everything else on a larger ARM VM, so the engine reaches
the terminal through this bridge:

* `serve(broker, host, port, token)` runs next to the terminal (`python -m goldbot.ops.run bridge <account>` under the
  Wine Python). It exposes the `Broker` protocol methods in `METHODS` (and nothing else) as JSON over HTTP POST
  /rpc, each authenticated by a bearer token compared in constant time. Bind it to the private network address only.
  Requests are accepted concurrently, but the terminal gets one call at a time (the MetaTrader5 package is not
  thread-safe) from a priority queue: closes and stop changes first, then /health, reads, and entry orders last; a
  request whose client has gone, or a read queued past its client's timeout, is never run (BACKLOG 135,
  goldbot/execution/bridge_queue.py). With the account's order lane up, entries go out on a terminal connection of
  their own, so ticks and closes keep flowing while the terminal sends one (goldbot/execution/bridge_lane.py).
* `RemoteBroker(url, token)` implements the `Broker` protocol on the engine's side.

Order safety: `place_order`, `modify` and `close` are never retried. A timeout after the bridge sent an order raises
`BridgeUnavailable`, exactly like a terminal that died after `order_send`; the engine treats an entry that raised it as
an ambiguous order (goldbot/engine/order_send.py) and finds the fill by its client order id in positions and deals
(row X7, BACKLOG 72), so an order is never sent twice. The engine sends entries through a client of their own on a
worker thread with the account's `order_timeout_s`. Read-only calls are retried once. While the bridge is unreachable there are no ticks, so the gate's stale-data rule blocks entries;
open positions keep their broker-side stop and target (sent with every order).

Defence in depth on the terminal's side: requests time out after `SOCKET_TIMEOUT_S` (a stalled connection holds
only its own thread); an order call older than `ORDER_MAX_AGE_S` when it reaches the terminal is refused
(the engine has already given up on it, and its price is stale); `GuardedBroker` refuses orders outside the account's
magic range or above `MAX_ORDER_LOTS`, and stop/target changes on positions that are not goldbot's (closes stay allowed: the kill switch closes everything). It also refuses
an order that would take this terminal's gross notional on the symbol above `MAX_NOTIONAL_EQUITY_MULT` (6x) its own
equity, the design's 30% at 1:20 (BACKLOG 66), read entirely on this box and failing closed; that refusal is a definite
reject (retcode 10006, message "bridge_notional_ceiling: ..."), never a raise, and the order's age is checked again
just before the send. The bridge listens on
127.0.0.1 only and the engine reaches it through an SSH tunnel (goldbot/ops/linux), so the token never crosses the
network in clear text. Both ends check the terminal is logged in to the registry's account (login, server, demo vs
real): `accounts.verify_terminal_account`. The bridge serves GET /health from the start but refuses every RPC until it
has once seen the terminal on that account (`verify_first`).

The terminal's state on /health (docs/proposals/2026-10-mt5-terminal-lifecycle.md): `terminal` is one fixed token of
TERMINAL_STATES (never a login, server or balance), `terminal_for_s` how long it has had that state (capped below six
digits), and the status is 200 only for `ok`, 503 otherwise. Consecutive `not_running` reads 30 s apart end the process
(TERMINAL_LOST_EXIT) so systemd re-attaches it to the terminal its own unit restarted. The root watchdog on the MT5 box
reads only that token, through an unprivileged probe (goldbot/ops/linux/goldbot-bridge-probe.sh).

No credentials pass through the bridge: the terminal keeps its own saved login, and the URL and token live in the
keyring of each machine (`mt5-bridge-url-<account>`, `mt5-bridge-token-<account>`), never in the repository.

Observe only (ADR 0063, the owner's live account read for XAUUSD247 with "NO live trading"): a bridge started with
`observe_only` (the account's `kind`, or GOLDBOT_BRIDGE_OBSERVE_ONLY=1 in its unit, whichever is stricter) serves the
OBSERVE_METHODS allowlist (bars, ticks, symbol terms, the account read) and nothing else: positions, history, sizing,
orders, stop changes and closes are refused 403 before the queue, its GuardedBroker refuses them again, and /health lists only what it serves with `observe_only: true`. On the
brain, `RemoteBroker(observe_only=True)` refuses the same calls before the network (`ObserveOnlyRefused`).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import threading
import time
from datetime import date
from datetime import time as dtime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, AsyncIterator, Callable

import pandas as pd
import requests
from pydantic import BaseModel

from goldbot.base import load_record
from goldbot.data.calendar import TradingSession
from goldbot.execution.bridge_queue import (
    HEALTH,
    MAX_QUEUED,
    READ_TTL_S,
    Job,
    LaneError,
    LaneLost,
    LaneNotSent,
    TerminalQueue,
    priority_of,
)
from goldbot.execution.broker import (
    SMOKE_MAGIC_OFFSET,
    AccountInfo,
    OrderIntent,
    OrderResult,
    Position,
    SymbolInfo,
    Tick,
    tick_key,
)
from goldbot.execution.costs import BrokerTerms
from goldbot.risk.sizing import account_fx

log = logging.getLogger("goldbot.bridge")

# method -> (returns, retry-safe). Orders are never retried (see the module docstring).
METHODS: dict[str, tuple[str, bool]] = {
    "symbol_info": ("SymbolInfo", True),
    "account": ("AccountInfo", True),
    "get_bars": ("frame", True),
    "get_bars_range": ("frame", True),       # read-only (copy_rates_range): optional, see OPTIONAL_METHODS
    "copy_ticks": ("frame", True),
    "last_tick": ("Tick", True),
    "positions": ("list[Position]", True),
    "account_positions": ("list[Position]", True),   # read-only, every symbol: the equity cross-check (ADR 0016)
    "deals_since": ("frame", True),
    "broker_terms": ("BrokerTerms", True),
    "margin_required": ("float", True),      # read-only (order_calc_margin): retry-safe, nothing reaches the market
    "loss_per_lot": ("float", True),         # read-only (order_calc_profit / tick value): the sizing conversion
    "trading_sessions": ("list[TradingSession]", True),   # read-only session hours (D13)
    "symbols_get": ("list[str]", True),      # read-only symbol names (ADR 0062 symbol check): optional
    "place_order": ("OrderResult", False),
    "modify": ("OrderResult", False),
    "close": ("OrderResult", False),
}
# The wire protocol's version, reported on GET /health with the served METHODS (BACKLOG 42, gap G6): bump it whenever a
# method is added, removed or changes its arguments, result or a wire record's fields. tests/test_bridge_compat.py pins
# (API_VERSION, MIN_COMPATIBLE, protocol_digest()) so any such change without a bump fails CI. MIN_COMPATIBLE is the
# oldest peer version this code still interoperates with, in both directions: the brain refuses entries when its
# API_VERSION is below the bridge's MIN_COMPATIBLE, or the bridge's API_VERSION is below its own MIN_COMPATIBLE, or the
# bridge lacks a method it calls (goldbot/ops/bridge_compat.py). Exits are never gated by it.
# v3 (ADR 0010): the brain reads responses tolerantly (unknown fields dropped); a v2 brain read them strictly. Same wire
# bytes, so MIN_COMPATIBLE stays 2; a future bridge that adds a response field sets MIN_COMPATIBLE = 3.
# v4: AccountInfo.margin_mode (hedging | netting | exchange). A v4 brain on a v3 bridge reads None and refuses to start
# (accounts.verify_terminal_account: a hedging account is required), so the MT5 box is deployed first. MIN_COMPATIBLE
# = 4 (review LOW-A): bridge-compat and preflight then name a v3 bridge as the cause instead of a generic refusal.
# v5 (ADR 0016): `account_positions` (every symbol) and Position.swap, read by the equity cross-check only. A v5
# brain calls a method a v4 bridge lacks, so MIN_COMPATIBLE = 5 names the v4 bridge (entries refused
# bridge_incompatible, exits unaffected): deploy the MT5 box first. A v4 brain drops the unknown swap field.
# v6: `get_bars_range` (MT5 copy_rates_range, read-only), the D12 reconcile's window read. It is OPTIONAL_METHODS: the
# brain falls back to get_bars when a v5 bridge refuses it (HTTP 403), so a v5 bridge stays compatible (MIN_COMPATIBLE
# 5, not required by bridge_compat) and either box can be deployed first.
# v7 (BACKLOG 135, ADR 0056): the bridge serves requests concurrently from a priority queue (closes and stop changes
# first, abandoned reads dropped; goldbot/execution/bridge_queue.py), may send entries through its order lane
# (`order_lane` on /health), and the brain sends its read timeout (`timeout_s`) with every request. Same methods,
# records and arguments, and a v6 server ignores the extra request key, so MIN_COMPATIBLE stays 5 and either box can
# be deployed first; only a live account requires v7 (bridge_compat.CLOSE_PRIORITY_VERSION: entries refused below it).
# v8 (ADR 0062): `symbols_get(pattern)` (read-only symbol names) and SymbolInfo's swap_mode, swap_long, swap_short,
# margin_initial and margin_maintenance, for the symbol check of a new account (goldbot/ops/symbol_check.py). Optional
# and reporting only: a v7 bridge refuses symbols_get (the check falls back to symbol_info) and its SymbolInfo reads
# with the new fields None, so MIN_COMPATIBLE stays 5 and either box can be deployed first.
# v9 (ADR 0063): AccountInfo.trade_allowed (MT5 ACCOUNT_TRADE_ALLOWED, False on an investor login), read by
# verify_terminal_account for observe_only accounts only. Read tolerantly (a v8 brain drops it; a v8 bridge reports
# None, which an observe_only account refuses and every other account ignores), so MIN_COMPATIBLE stays 5.
API_VERSION = 9
MIN_COMPATIBLE = 5
# ADR 0063 (security review of PR 236): what an observe-only bridge serves, an allowlist of read-only methods for
# bars, ticks and symbol terms, and the account read its verify needs; no positions, history, sizing or order method
OBSERVE_METHODS = frozenset({"get_bars", "get_bars_range", "copy_ticks", "last_tick", "symbol_info", "symbols_get",
                             "account"})
OBSERVE_REFUSAL = "observe_only"          # the refusal's prefix on an observe-only bridge or client (ADR 0063)
# Methods the brain calls only with a fallback for a bridge that lacks them: never required by bridge_compat, so their
# absence never refuses entries. Read-only methods only (an order method is never optional).
OPTIONAL_METHODS = frozenset({"get_bars_range", "symbols_get"})
_RECORDS: dict[str, type[BaseModel]] = {"SymbolInfo": SymbolInfo, "AccountInfo": AccountInfo, "Tick": Tick, "OrderResult": OrderResult,
            "Position": Position, "BrokerTerms": BrokerTerms, "OrderIntent": OrderIntent, "TradingSession": TradingSession}
# Any change to these records' fields goes to the trading-safety reviewer (AGENT_STANDARDS S12): responses are read
# tolerantly, so a new field must never carry an outcome or a restriction on its own.
# Records the brain sends as arguments: always decoded strictly, even inside a response (ADR 0010, `_decode`).
_REQUEST_RECORDS = frozenset({"OrderIntent"})
MAX_BODY = 1_000_000
SOCKET_TIMEOUT_S = 5            # a connection that sends nothing for this long is dropped
ORDER_MAX_AGE_S = 10.0          # an order call older than this when the terminal gets to it is refused
MAX_ORDER_LOTS = 3.0            # the design's combined exposure cap; no single order may exceed it
# BACKLOG 66 (DESIGN.md:112, "combined exposure capped at 3 lots or 30% of combined equity notional"): 30% x the 1:20
# leverage cap = 6x equity, the gate's `max_combined_notional_frac * leverage_cap` (a test pins the two equal). The
# bridge enforces it on this terminal's own readings, so a brain sizing wrongly on a 1:5000 demo terminal is refused.
MAX_NOTIONAL_EQUITY_MULT = 6.0
NOTIONAL_FRAC = 0.30            # the 30% of DESIGN.md:112; x a leverage cap = the multiple of equity
CEILING_REFUSAL = "bridge_notional_ceiling"     # the message prefix of every ceiling refusal (the engine alerts on it)
UNREADABLE_REFUSAL = f"{CEILING_REFUSAL}: unreadable:"   # ... when a figure it checks could not be read (fail closed)
STALE_REFUSAL = "bridge_stale_order"            # an order that aged past ORDER_MAX_AGE_S during the ceiling's reads
# an entry refused because the terminal's AutoTrading is off (/health autotrading_off): definite, 10006, never sent
AUTOTRADING_REFUSAL = "terminal_autotrading_off"
REFUSED_RETCODE = 10006         # MT5 "request rejected": a definite reject (runner.DEFINITE_REJECT_RETCODES)
# The terminal's state on GET /health: exactly these tokens (the root watchdog's probe matches them literally, and
# lists them in this order; a test pins the two equal). `autotrading_off` (incident 2026-10-20): on the registered
# account and connected, but the terminal's AutoTrading button is off, so it refuses every order (10027).
TERMINAL_STATES = ("ok", "not_running", "not_connected", "not_logged_in", "wrong_account", "autotrading_off")
# The keyring login's state on /health (`login`, beside `terminal`; goldbot/ops/login.py SelfHeal.status): fixed tokens,
# never a login. Old probes and brains ignore the extra field.
LOGIN_STATES = ("in_progress", "refused", "rate_limited", "waiting")
# How long /health and an RPC wait for the terminal lock the self-heal login holds (trading-safety review MEDIUM 2):
# well under the watchdog's 20 s probe and an order's ORDER_MAX_AGE_S, so neither ever waits out a login.
LOCK_WAIT_S = 2.0
TERMINAL_LOST_READS = 2         # consecutive not_running reads after which the bridge exits to be re-attached
TERMINAL_LOST_MIN_SPAN_S = 30.0  # ... and only when those reads span at least this long (review L1)
TERMINAL_LOST_EXIT = 75         # EX_TEMPFAIL: systemd's Restart=always starts it again
TERMINAL_FOR_CAP_S = 99_999     # terminal_for_s never has six digits (security condition 6: no 6-digit run on /health)


class BridgeUnavailable(ConnectionError):
    """The bridge did not answer (network, timeout, terminal down). The call may or may not have reached the broker."""


class ObserveOnlyRefused(PermissionError):
    """ADR 0063: an order, stop change or close asked of an observe-only account's client; nothing was sent."""


class BridgeError(RuntimeError):
    """The bridge answered with an error (bad request, refused, or the terminal raised)."""


# ---------------------------------------------------------------------------------------------- wire format
def _encode(v: Any) -> Any:
    if isinstance(v, pd.DataFrame):
        out = v.copy()
        times = [c for c in out.columns if isinstance(out[c].dtype, pd.DatetimeTZDtype)]
        for c in times:
            out[c] = out[c].map(lambda t: None if pd.isna(t) else t.isoformat())
        return {"__frame__": json.loads(out.to_json(orient="split", index=False, date_format="iso")), "times": times}
    if isinstance(v, pd.Timestamp):
        return {"__ts__": v.isoformat()}
    if isinstance(v, (date, dtime)):                # record fields (TradingSession): pydantic parses the ISO text back
        return v.isoformat()
    if hasattr(v, "model_dump"):                    # python-mode dump: Timestamps stay Timestamps and go as __ts__
        return {"__record__": type(v).__name__, "data": _encode(v.model_dump())}
    if isinstance(v, dict):
        return {k: _encode(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_encode(x) for x in v]
    return v


def _decode(v: Any, *, response: bool) -> Any:
    """Wire JSON -> Python. ADR 0010: "read tolerantly, build strictly", but an order request is never guessed.

    `response=True` (the brain reading what the MT5 box sent back): the box is updated by hand and can run a newer
    release, so its records are read with `load_record` (unknown fields dropped and counted in `schema_skew()`; a
    wrong type or a missing field still raises). `response=False` (the bridge server reading the brain's arguments)
    and request records (`_REQUEST_RECORDS`) in either direction stay strict: an unknown field fails the call."""
    if isinstance(v, dict) and "__frame__" in v:
        f = v["__frame__"]
        df = pd.DataFrame(f["data"], columns=f["columns"])
        for c in v.get("times", []):
            df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601")
        return df
    if isinstance(v, dict) and "__ts__" in v:
        return pd.Timestamp(v["__ts__"])
    if isinstance(v, dict) and "__record__" in v:
        cls = _RECORDS.get(v["__record__"])
        if cls is None:
            raise BridgeError(f"unknown record type {v['__record__']!r}")
        data = _decode(v["data"], response=response)
        if response and v["__record__"] not in _REQUEST_RECORDS:
            return load_record(cls, data, source="bridge-response")
        return cls.model_validate(data)
    if isinstance(v, dict):
        return {k: _decode(x, response=response) for k, x in v.items()}
    if isinstance(v, list):
        return [_decode(x, response=response) for x in v]
    return v


# ---------------------------------------------------------------------------------------------- server
def _log_terminal_lost() -> None:
    log.error("MT5 terminal not running on %d consecutive reads over %g s: exiting to re-attach",
              TERMINAL_LOST_READS, TERMINAL_LOST_MIN_SPAN_S)


def _exit_terminal_lost() -> None:
    """End the bridge after TERMINAL_LOST_READS not_running reads: its IPC is gone, and a fresh process re-attaches
    (initialize(path=..., timeout=60 s)) once the terminal unit has the terminal up again. Called after the reply was
    written. `serve` passes its own callback (requests are served on threads since protocol v7, where a SystemExit
    would end only the request's thread): it stops the server and exits from the main thread."""
    _log_terminal_lost()
    raise SystemExit(TERMINAL_LOST_EXIT)


def _read_timeout(v: Any) -> float | None:
    """The client's read timeout (`timeout_s`, protocol v7) when it is a sane number of seconds, else None."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 600:
        return float(v)
    return None


def make_handler(broker: Any, token: str, *, commit: str | None = None, verify_first: bool = False,
                 on_terminal_lost: Callable[[], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, lock: threading.Lock | None = None,
                 login_status: Callable[[], str | None] | None = None,
                 terminal: TerminalQueue | None = None, lane: Any = None,
                 observe_only: bool = False) -> type[BaseHTTPRequestHandler]:
    """`commit`: the commit this bridge process runs (read once at start), reported on /health so the brain can see
    when the two boxes run different code. Nothing on /health is a secret: zone, commit (public repo), protocol, the
    order lane's state token, and the terminal's state token when the broker reports one (`terminal_state()`).

    `verify_first`: the terminal's state is read on every RPC. Every RPC is refused (503) until the terminal has once
    been seen in state `ok` (logged in to the registered account, connected), and again whenever it is CURRENTLY on
    another account (`wrong_account`: reads and closes included, a terminal switched to another account serves
    nothing). Any other state after that lets calls through: exits are never gated, and the terminal answers what it
    can. /health is served throughout, so a lost login shows as `not_logged_in` instead of a bridge that keeps
    restarting. `on_terminal_lost`: called after TERMINAL_LOST_READS consecutive `not_running` reads spanning at
    least TERMINAL_LOST_MIN_SPAN_S (default: exit, `_exit_terminal_lost`).

    `lock`: the terminal lock the bridge's background self-heal login holds (goldbot/ops/login.py SelfHeal; the
    MetaTrader5 package is not thread-safe). /health and every RPC take it for their terminal calls, waiting at most
    LOCK_WAIT_S: while a login holds it, /health answers not_logged_in with `login: in_progress` without touching the
    terminal, and an RPC gets a prompt 503 (a definite refusal; the engine sends a close again), never a wait past the
    watchdog's probe or an order's age limit. `login_status`: the self-heal's token (LOGIN_STATES) for /health.

    BACKLOG 135 (protocol v7, goldbot/execution/bridge_queue.py): the request is read, authenticated and decoded on
    its own thread; its terminal work (/health's reads included) runs on `terminal`'s one worker, closes and stop
    changes first, then /health, then reads, then orders. A request whose client went while it waited is never run;
    a read queued past its client's `timeout_s` is dropped (504); an order older than ORDER_MAX_AGE_S when it reaches
    the front is refused stale (409). `lane` (goldbot/execution/bridge_lane.py ProcessLane, or None): while it is up,
    `place_order` runs there on its own terminal connection (its own one-worker queue), checked against the last
    terminal state this connection read (the lane's GuardedBroker reads the account itself on every order); an order
    whose lane died with it gets no reply at all, so the brain books it ambiguous (BACKLOG 72).

    `observe_only` (ADR 0063): only OBSERVE_METHODS are served; any other method is refused 403 before the queue
    (the terminal never sees it), no order lane is used, and /health lists the served methods with `observe_only`."""
    if len(token) < 32:
        raise ValueError("bridge token must be at least 32 characters")
    if observe_only and lane is not None:
        raise ValueError("an observe-only bridge has no order lane (ADR 0063)")
    served = OBSERVE_METHODS if observe_only else frozenset(METHODS)
    lost_cb = on_terminal_lost or _exit_terminal_lost
    seen: dict[str, Any] = {"state": None, "since": clock(), "lost": 0, "lost_at": 0.0,
                            "verified": not verify_first}
    main = terminal if terminal is not None else TerminalQueue("terminal")
    orders = TerminalQueue("order-lane")

    def terminal_state() -> str | None:
        """The terminal's token (and its bookkeeping), or None when the broker does not report one. On the worker."""
        fn = getattr(broker, "terminal_state", None)
        state = fn() if callable(fn) else None
        if state is None:
            return None
        if state not in TERMINAL_STATES:                  # never echo anything else
            state = "not_running"
        if state != seen["state"]:
            seen["state"], seen["since"] = state, clock()
        if state in ("ok", "autotrading_off"):         # both are the registered account, connected
            seen["verified"] = True
        return str(state)

    def health() -> tuple[int, dict[str, Any], bool]:
        """GET /health on the worker: (status, body, whether the terminal is lost and the bridge must exit)."""
        body: dict[str, Any] = {"ok": True}
        state: str | None
        held = lock is not None and not lock.acquire(timeout=LOCK_WAIT_S)
        if held:                                         # a login holds the terminal: it has no account now
            state = "not_logged_in"
            if state != seen["state"]:
                seen["state"], seen["since"] = state, clock()
        else:
            try:
                state = terminal_state()
            finally:
                if lock is not None:
                    lock.release()
        login = "in_progress" if held else (login_status() if login_status is not None else None)
        if login in LOGIN_STATES and state != "ok":    # a login held back only matters without an account
            body["login"] = login
        if state is not None:                            # a fixed token and a capped age: no account data
            body["ok"] = state == "ok"
            body["terminal"] = state
            body["terminal_for_s"] = min(int(max(clock() - seen["since"], 0)), TERMINAL_FOR_CAP_S)
        tz = getattr(broker, "server_tz", None)      # the zone this box converts MT5 stamps with (not a secret)
        if isinstance(tz, str):
            body["server_tz"] = tz
        body["api_version"] = API_VERSION
        body["min_compatible"] = MIN_COMPATIBLE
        body["methods"] = sorted(served)                 # exactly what do_POST serves
        if observe_only:
            body["observe_only"] = True
        body["order_lane"] = lane.state if lane is not None else "off"
        if commit is not None:
            body["commit"] = commit
        lost = False
        if state != "not_running":
            seen["lost"] = 0
        else:
            if seen["lost"] == 0:
                seen["lost_at"] = clock()
            seen["lost"] += 1
            # one brain health pass reads /health three times within a second: only reads spanning
            # TERMINAL_LOST_MIN_SPAN_S end the bridge (a terminal restarting for a moment does not)
            if seen["lost"] >= TERMINAL_LOST_READS and clock() - seen["lost_at"] >= TERMINAL_LOST_MIN_SPAN_S:
                seen["lost"] = 0
                lost = True
        return (200 if body["ok"] else 503), body, lost

    def gate(*, read_terminal: bool) -> tuple[int, dict[str, Any]] | None:
        """verify_first (see above): a refusal, or None. `read_terminal` False (the order lane) judges the state
        this connection read last instead of reading the terminal from another thread."""
        if not verify_first:
            return None
        state = terminal_state() if read_terminal else seen["state"]
        if not seen["verified"]:                     # not yet seen on the registered account: no call at all
            return 503, {"error": f"terminal not verified: {state or 'unknown'}"}
        if state == "wrong_account":                 # switched since: nothing at all, reads and closes too
            return 503, {"error": "terminal on another account: refused"}
        # not_logged_in / not_running / not_connected: calls go through (exits are never gated); the terminal
        # answers what it can
        return None

    def call(method: str, args: list[Any], sent_at: float) -> _Outcome:
        """One RPC on the main worker."""
        if lock is not None and not lock.acquire(timeout=LOCK_WAIT_S):
            return _Outcome(503, {"error": "terminal login in progress: refused, send again"})
        try:
            if (refused := gate(read_terminal=True)) is not None:
                return _Outcome(*refused)
            if method == "place_order" and isinstance(broker, GuardedBroker):
                broker.order_sent_at = sent_at       # its age is checked again just before the send
            return _Outcome(200, result=getattr(broker, method)(*args))
        except Exception as exc:                                  # the terminal's error goes back to the caller
            log.exception("bridge call failed")
            return _Outcome(500, {"error": f"{type(exc).__name__}: {exc}"})
        finally:
            if lock is not None:
                lock.release()

    def lane_call(args: list[Any], sent_at: float) -> _Outcome:
        """place_order on the order lane's own connection (its worker), as `call`; LaneLost propagates."""
        if (refused := gate(read_terminal=False)) is not None:
            return _Outcome(*refused)
        try:
            return _Outcome(200, result=lane.place_order(args[0], sent_at))
        except LaneError as exc:
            return _Outcome(500, {"error": str(exc)})
        except LaneNotSent as exc:            # nothing reached the terminal: a definite reject, never silence
            from goldbot.execution.bridge_lane import NOT_SENT
            log.warning("order %s not sent: %s", getattr(args[0], "client_order_id", "?"), exc)
            return _Outcome(200, result=_refusal(f"{NOT_SENT}: {exc}; nothing was sent"))

    def stale(sent_at: float) -> str | None:
        age = time.time() - sent_at
        return None if -ORDER_MAX_AGE_S < age < ORDER_MAX_AGE_S else f"{age:.1f} s old; refused (stale)"

    class Handler(BaseHTTPRequestHandler):
        server_version = "goldbot-bridge"
        timeout = SOCKET_TIMEOUT_S

        def log_message(self, fmt: str, *args: Any) -> None:     # requests go to the module log, not stderr
            log.debug("%s " + fmt, self.address_string(), *args)

        def _reply(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _run(self, q: TerminalQueue, job: Job) -> bool:
            """Queue `job` and wait for it; False (a reply already sent, or the client gone) when it never ran."""
            if not q.submit(job):
                self._reply(503, {"error": "bridge busy: too many requests waiting; refused, send again"})
                return False
            if not q.wait(job, self.connection):
                log.info("bridge: %s not run, its client went while it waited", job.label)
                self.close_connection = True
                return False
            if job.status == "skipped":
                code = 409 if (job.skipped or "").endswith("(stale)") else 504
                self._reply(code, {"error": f"{job.label} is {job.skipped}"})
                return False
            return True

        def do_GET(self) -> None:
            if self.path != "/health":
                self._reply(404, {"error": "not found"})
                return
            job = Job(health, priority=HEALTH, label="health")
            if not self._run(main, job):
                return
            if job.error is not None:
                self._reply(500, {"error": f"health failed ({type(job.error).__name__})"})
                return
            status, body, lost = job.result
            self._reply(status, body)
            if lost:
                lost_cb()

        def do_POST(self) -> None:
            auth = self.headers.get("Authorization", "")
            if not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
                self._reply(401, {"error": "unauthorised"})
                return
            if self.path != "/rpc":
                self._reply(404, {"error": "not found"})
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0 or n > MAX_BODY:
                self._reply(400, {"error": "bad body"})
                return
            received = time.monotonic()
            try:
                req = json.loads(self.rfile.read(n))
                method = req["method"]
                if not isinstance(method, str) or method not in METHODS:
                    self._reply(403, {"error": f"method {method!r} not allowed"})
                    return
                if method not in served:             # ADR 0063: an observe-only bridge never queues an order call
                    log.warning("bridge: %s refused (observe-only bridge)", method)
                    self._reply(403, {"error": f"{OBSERVE_REFUSAL}: {method} refused, this bridge only reads"})
                    return
                sent_at = float(req.get("sent_at", 0.0))
                retry_safe = METHODS[method][1]
                if not retry_safe and (why := stale(sent_at)) is not None:
                    self._reply(409, {"error": f"{method} is {why}"})
                    return
                args = [_decode(a, response=False) for a in req.get("args", [])]
            except Exception as exc:                                  # a malformed request goes back to the caller
                log.exception("bridge call failed")
                self._reply(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            ttl = _read_timeout(req.get("timeout_s")) or READ_TTL_S

            def refusal() -> str | None:
                """On the worker, just before the call: an abandoned read or a stale order is never run."""
                if method == "place_order":
                    return stale(sent_at)
                if retry_safe and time.monotonic() - received > ttl:
                    return f"queued past its client's {ttl:g} s timeout; dropped"
                return None
            on_lane = method == "place_order" and lane is not None and lane.ready()
            job = Job((lambda: lane_call(args, sent_at)) if on_lane else (lambda: call(method, args, sent_at)),
                      priority=priority_of(method), label=method, refusal=refusal)
            if not self._run(orders if on_lane else main, job):
                return
            if isinstance(job.error, LaneLost):      # the order's outcome is unknown: no reply, never re-sent
                log.error("order lane lost an order in flight: no reply, the brain looks it up (BACKLOG 72)")
                self.close_connection = True
                return
            if job.error is not None:
                log.error("bridge call failed: %s", type(job.error).__name__)
                self._reply(500, {"error": f"{type(job.error).__name__}: {job.error}"})
                return
            out: _Outcome = job.result
            if out.status != 200:
                self._reply(out.status, out.body)
                return
            try:
                body = {"result": _encode(out.result)}      # encoded on this thread, never on the terminal's worker
            except Exception as exc:                          # an unencodable result: the caller hears why
                self._reply(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            self._reply(200, body)

    return Handler


class _Outcome:
    """A request's outcome from the worker: an HTTP status with an error body, or 200 with the broker's `result`."""

    def __init__(self, status: int, body: dict[str, Any] | None = None, *, result: Any = None) -> None:
        self.status, self.body, self.result = status, body or {}, result


def make_server(broker: Any, host: str, port: int, token: str, **kw: Any) -> ThreadingHTTPServer:
    """The bridge's HTTP server (protocol v7): requests accepted on threads of their own (daemon), terminal work on
    the queue's one worker (make_handler). `kw` goes to make_handler."""
    httpd = _BridgeServer((host, port), make_handler(broker, token, **kw))
    httpd.daemon_threads = True
    return httpd


class _BridgeServer(ThreadingHTTPServer):
    """A listen backlog of MAX_QUEUED, not socketserver's 5: the brain opens about six connections per account at once
    (ticks, history, M1 bars, sessions, health, orders), and a connection refused at the door would be a close lost
    under load (found by the read-flood test: a reset on connect)."""

    request_queue_size = MAX_QUEUED


def serve(broker: Any, host: str, port: int, token: str, commit: str | None = None,
          verify_first: bool = False, lock: threading.Lock | None = None,
          login_status: Callable[[], str | None] | None = None,
          lane: Any = None, observe_only: bool = False) -> None:  # pragma: no cover - blocking loop
    """Requests on threads, one terminal call at a time per connection (make_handler); the self-heal login thread
    shares `lock` with it. `lane`: the order lane (bridge_lane.ProcessLane), started here on a thread of its own;
    orders use it once it is up. A lost terminal, or a lane child that launched a terminal of its own, stops the
    server and exits from this thread (TERMINAL_LOST_EXIT, or the lane's spawned_exit). `observe_only`: ADR 0063."""
    exit_code: dict[str, int] = {}
    holder: dict[str, ThreadingHTTPServer] = {}

    def stop(code: int) -> None:
        exit_code.setdefault("code", code)
        if "httpd" in holder:
            threading.Thread(target=holder["httpd"].shutdown, name="bridge-stop", daemon=True).start()

    def lost() -> None:
        _log_terminal_lost()
        stop(TERMINAL_LOST_EXIT)
    if lane is not None:
        lane.on_spawned = lambda: stop(lane.spawned_exit)
    httpd = holder["httpd"] = make_server(broker, host, port, token, commit=commit, verify_first=verify_first,
                                          lock=lock, login_status=login_status, on_terminal_lost=lost, lane=lane,
                                          observe_only=observe_only)
    if lane is not None:
        threading.Thread(target=lane.start, name="bridge-order-lane-start", daemon=True).start()
    log.info("bridge serving %s on %s:%d", type(broker).__name__, host, port)
    try:
        httpd.serve_forever()
    finally:
        if lane is not None:
            lane.stop()
        httpd.server_close()
    if "code" in exit_code:
        raise SystemExit(exit_code["code"])


class _Unreadable(Exception):
    """A figure the notional ceiling needs could not be read, or is not finite and > 0 (fail closed)."""


def _read(field: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except _Unreadable:
        raise
    except Exception as exc:                            # noqa: BLE001 - any terminal failure refuses the order
        raise _Unreadable(f"{field} ({type(exc).__name__})") from exc


def _positive(field: str, v: Any) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise _Unreadable(field) from None
    if not math.isfinite(x) or x <= 0:
        raise _Unreadable(field)
    return x


def _refusal(message: str) -> OrderResult:
    return OrderResult(ok=False, retcode=REFUSED_RETCODE, order_id=None, position_id=None, filled_lots=0.0, price=None,
                       message=message)


class GuardedBroker:
    """The terminal-side broker behind the bridge, refusing what goldbot never sends: orders outside this account's
    magic range (magic_base .. magic_base + 99) or above MAX_ORDER_LOTS, and stop/target changes on positions outside
    that range. Closes are allowed for any position: closing only reduces risk, and the 12% kill switch closes
    everything on the account (design: Drawdown kill switch). The bridge smoke's magic (magic_base + SMOKE_MAGIC_OFFSET)
    is refused unless the terminal reports a demo account (ADR 0021 F4).

    BACKLOG 66, the notional ceiling (DESIGN.md:112: "combined exposure capped at 3 lots or 30% of combined equity
    notional", 30% x 1:20 = 6x equity): an order is refused when (every open lot on its symbol + its lots) x contract x
    price x fx > MAX_NOTIONAL_EQUITY_MULT x equity, strict as the gate. Every figure is this terminal's own (only the
    lots, side and symbol come from the brain): equity and currency (`account()`, read once per order, always), the
    contract size, the quote the order fills at (ask for a buy, bid for a sell, as MT5Broker.place_order), the
    conversion (`loss_per_lot` per USD, through `sizing.account_fx`), and `account_positions()` on the symbol, any
    magic, both sides (gross). Any read that raises, is missing, non-finite or <= 0 refuses (`unreadable:<field>`).
    A refusal is a definite reject, never a raise: OrderResult retcode 10006 with the message
    "bridge_notional_ceiling: ...", so the engine records `rejected` and alerts the owner. Order of the checks: magic
    -> 3.0 lots -> account (one read) -> F4 smoke magic on demo only -> verify -> ceiling -> the order's age again
    (`order_sent_at`) -> send. `close` and `modify` are never sized.

    AutoTrading (incident 2026-10-20, trading-safety review): an entry is refused as a definite reject (10006,
    "terminal_autotrading_off: ...") when `terminal_info().trade_allowed` is False, checked after verify and before
    the ceiling; the terminal would refuse it anyway (10027), this makes the reason the box's own. A terminal_info that
    cannot be read, or has no such field, is not judged here. Closes and modifies are never gated by it."""

    def __init__(self, broker: Any, *, magic_base: int, verify: Callable[[Any], str | None] | None = None,
                 leverage_cap: float | None = None, observe_only: bool = False) -> None:
        self._b, self.magic_base, self._verify = broker, magic_base, verify
        # ADR 0063: an observe-only account's terminal side refuses every order, stop change and close (PermissionError,
        # the bridge's 500): nothing of the owner's live account is ever touched, its own positions included
        self.observe_only = observe_only
        # ADR 0055 (trading-safety condition, HIGH): the live pilot account's own leverage cap (accounts.yaml
        # `pilot.leverage_cap`, 500), so its ceiling is NOTIONAL_FRAC x min(cap, the terminal's own leverage) x equity
        # (150x at 1:500) instead of 6x, which refuses every 0.01 lot at £50. None (every other account): 6x
        self.leverage_cap = leverage_cap
        # when the brain sent the order being served (the bridge handler sets it; one request at a time): the age is
        # checked again just before the send, after the ceiling's reads. None: not served over the bridge
        self.order_sent_at: float | None = None

    def _ours(self, magic: int) -> bool:
        return self.magic_base <= int(magic) < self.magic_base + 100

    def _own_position(self, position_id: int) -> None:
        if not any(p.position_id == position_id and self._ours(p.magic) for p in self._b.positions()):
            raise PermissionError(f"position {position_id} is not one of this account's goldbot positions")

    def _refuse_observed(self, what: str) -> None:
        if self.observe_only:
            raise PermissionError(f"{OBSERVE_REFUSAL}: {what} refused, this terminal is only read (ADR 0063)")

    def place_order(self, intent: OrderIntent) -> OrderResult:
        sent_at, self.order_sent_at = self.order_sent_at, None
        self._refuse_observed("place_order")
        if not self._ours(intent.magic):
            raise PermissionError(f"magic {intent.magic} outside {self.magic_base}..{self.magic_base + 99}")
        if not 0 < intent.lots <= MAX_ORDER_LOTS:
            raise PermissionError(f"{intent.lots} lots outside (0, {MAX_ORDER_LOTS}]")
        try:                                           # the one account() read: F4, verify and the ceiling use it
            info = _read("account", self._b.account)
        except _Unreadable as u:
            return self._refuse(intent, f"unreadable:{u}")
        if info is None:                               # MT5 account_info() returning None: no figures, never a 500
            return self._refuse(intent, "unreadable:account")
        # ADR 0021 F4: the bridge smoke's magic only ever trades a terminal that reports a demo account, whatever the
        # brain sent (internal only: METHODS, signatures and the protocol digest are unchanged)
        if int(intent.magic) == self.magic_base + SMOKE_MAGIC_OFFSET and getattr(info, "trade_mode", None) != "demo":
            raise PermissionError(f"smoke magic {intent.magic} refused: the terminal account is "
                                  f"{getattr(info, 'trade_mode', None) or 'unknown'}, not demo")
        why = self._verify(info) if self._verify is not None else None
        if why:                                        # the terminal was switched to another account after start
            raise PermissionError(f"terminal account changed: {why}")
        if self._autotrading_off():
            log.warning("order %s refused: the terminal's AutoTrading is off", intent.client_order_id)
            return _refusal(f"{AUTOTRADING_REFUSAL}: the MT5 terminal's AutoTrading is off (it refuses every order, "
                            "10027); restart the terminal unit on the MT5 box")
        if (refused := self._notional_refusal(intent, info)) is not None:
            return self._refuse(intent, refused)
        if sent_at is not None and (age := time.time() - sent_at) >= ORDER_MAX_AGE_S:
            log.warning("order %s refused: %.1f s old after the ceiling's reads", intent.client_order_id, age)
            return _refusal(f"{STALE_REFUSAL}: {age:.1f} s old before the send (max {ORDER_MAX_AGE_S:g} s)")
        return self._b.place_order(intent)

    def _autotrading_off(self) -> bool:
        """True only when the terminal says trade_allowed is False; no terminal_info, a raise or no field: False."""
        fn = getattr(self._b, "terminal_info", None)
        if not callable(fn):
            return False
        try:
            return getattr(fn(), "trade_allowed", True) is False
        except Exception:                                # noqa: BLE001 - not judged here; the send says what it can
            return False

    def _refuse(self, intent: OrderIntent, why: str) -> OrderResult:
        log.warning("order %s refused by the notional ceiling: %s", intent.client_order_id, why)
        return _refusal(f"{CEILING_REFUSAL}: {why}")

    def notional_mult(self, info: AccountInfo) -> float:
        """The ceiling's multiple of equity: MAX_NOTIONAL_EQUITY_MULT, or on the pilot account NOTIONAL_FRAC x its
        leverage cap, lowered to the terminal's own leverage when that is smaller (it only tightens)."""
        if self.leverage_cap is None:
            return MAX_NOTIONAL_EQUITY_MULT
        lev = float(getattr(info, "leverage", 0) or 0)
        cap = min(self.leverage_cap, lev) if math.isfinite(lev) and lev > 0 else self.leverage_cap
        return NOTIONAL_FRAC * cap

    def _notional_refusal(self, intent: OrderIntent, info: AccountInfo) -> str | None:
        """Why the order would take this terminal's notional on its symbol above MAX_NOTIONAL_EQUITY_MULT x equity, or
        None. Fail closed: a figure that cannot be read or is not finite and > 0 is the reason (`unreadable:<field>`)."""
        sym = intent.symbol
        try:
            equity = _positive("equity", getattr(info, "equity", None))
            currency = getattr(info, "currency", "")
            contract = _positive("contract", _read("contract", lambda: self._b.symbol_info(sym)).contract_size)
            tick = _read("price", lambda: self._b.last_tick(sym))
            px = _positive("price", tick.ask if intent.side > 0 else tick.bid)
            per_lot = _positive("fx", _read("fx", lambda: self._b.loss_per_lot(sym, intent.side, px, 1.0)))
            fx = account_fx(currency if isinstance(currency, str) else "", per_lot / contract)
            if fx is None:                             # blank currency, or a USD figure more than 1% off 1.0
                raise _Unreadable("fx")
            positions = _read("positions", self._b.account_positions)
            open_lots = _read("positions", lambda: sum(_positive("positions", p.lots) for p in positions
                                                       if p.symbol == sym))
        except _Unreadable as u:
            return f"unreadable:{u}"
        except Exception as exc:                        # noqa: BLE001 - a malformed record: never a 500, never a send
            return f"unreadable:ceiling ({type(exc).__name__})"
        notional = (open_lots + intent.lots) * contract * px * fx
        mult = self.notional_mult(info)
        if notional > mult * equity:
            return (f"notional {notional:.2f} {currency} > {mult:g} x equity {equity:.2f} "
                    f"({open_lots:g} lots open + {intent.lots:g} on {sym})")
        return None

    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        self._refuse_observed("modify")
        self._own_position(position_id)
        return self._b.modify(position_id, sl, tp)

    def close(self, position_id: int, lots: float | None = None) -> OrderResult:
        self._refuse_observed("close")
        return self._b.close(position_id, lots)

    def terminal_state(self) -> str | None:
        """The terminal's state as one token of TERMINAL_STATES, for GET /health; None when the wrapped broker cannot
        say (no `terminal_info`, e.g. the paper broker). Order: no terminal (terminal_info None or an IPC error) ->
        no account -> another account than registered (`verify`) -> not connected to the broker server -> AutoTrading
        off (`terminal_info().trade_allowed` False: the terminal refuses every order, 10027; a terminal_info without
        the field is not judged) -> ok. A login or a server never leaves this method."""
        info_fn = getattr(self._b, "terminal_info", None)
        if not callable(info_fn):
            return None
        try:
            term = info_fn()
        except Exception:                                # noqa: BLE001 - an IPC error is a terminal gone
            return "not_running"
        if term is None:
            return "not_running"
        try:
            info = self._b.account()
        except Exception:                                # noqa: BLE001 - MT5Broker.account() on account_info() None
            return "not_logged_in"
        if info is None or not getattr(info, "login", None):
            return "not_logged_in"
        try:
            why = self._verify(info) if self._verify is not None else None
        except Exception:                                # noqa: BLE001 - cannot be checked: not the registered account
            why = "unverifiable"
        if why:
            return "wrong_account"
        if not getattr(term, "connected", False):
            return "not_connected"
        if getattr(term, "trade_allowed", True) is False:
            return "autotrading_off"
        return "ok"

    @property
    def server_tz(self) -> str | None:
        """The wrapped terminal's server_tz (reported on /health so the brain can compare it with its own)."""
        tz = getattr(self._b, "server_tz", None)
        return tz if isinstance(tz, str) else None

    def __getattr__(self, name: str) -> Any:          # read-only calls pass through
        if name in METHODS and METHODS[name][1]:
            return getattr(self._b, name)
        raise AttributeError(name)


def protocol_digest() -> str:
    """sha-256 over what makes the wire protocol: METHODS (name, result, retry-safety), the signature of each method on
    the brain's side (RemoteBroker) and on the terminal's side behind the bridge (MT5Broker, the dispatch target; with
    GuardedBroker's own override where it has one), and the field names of every wire record. Pinned with API_VERSION
    by tests/test_bridge_compat.py."""
    import inspect

    from goldbot.execution.mt5_adapter import (
        MT5Broker,  # imports without the MetaTrader5 package (lazy inside)
    )

    def sig(cls: type, name: str) -> str:
        fn = cls.__dict__.get(name) if cls is GuardedBroker else getattr(cls, name, None)
        return str(inspect.signature(fn)) if fn is not None else "-"
    parts = [[m, ret, retry, sig(RemoteBroker, m), sig(MT5Broker, m), sig(GuardedBroker, m)]
             for m, (ret, retry) in sorted(METHODS.items())]
    records = [[n, sorted(cls.model_fields)] for n, cls in sorted(_RECORDS.items())]
    return hashlib.sha256(json.dumps([parts, records]).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- client
def probe_health(url: str, timeout_s: float = 5.0, get: Callable[..., Any] = requests.get) -> str | None:
    """None when the bridge answers GET /health with {"ok": true}, or with a 503 that reports a terminal state (the
    bridge answers; `terminal:<account>` reports the terminal); otherwise why not (no URL or token in the text).
    Used by the brain's health checks; no token is needed (/health reads the terminal's state, never an order)."""
    try:
        r = get(f"{url.rstrip('/')}/health", timeout=timeout_s)
    except requests.RequestException as exc:
        return f"unreachable ({type(exc).__name__})"
    if r.status_code == 503:
        try:
            body = r.json()
        except (ValueError, AttributeError):
            body = None
        if isinstance(body, dict) and body.get("terminal") in TERMINAL_STATES:
            return None
    if r.status_code != 200:
        return f"HTTP {r.status_code}"
    try:
        ok = r.json().get("ok")
    except (ValueError, AttributeError):
        return "answered, but not with the bridge's JSON"
    return None if ok is True else "answered without ok"


TERMINAL_PROBE_MAX_BYTES = 64 * 1024


def probe_terminal(url: str, timeout_s: float = 5.0,
                   get: Callable[..., Any] = requests.get) -> tuple[str, int | None, str | None]:
    """The terminal's state from the bridge's GET /health, for `terminal:<account>`: (token, seconds in it, keyring
    login token). The token is one of TERMINAL_STATES, "unreported" (a bridge older than the terminal state: 200
    without the field), or "noanswer" (no answer, junk, an unknown value, or a status that does not match the value:
    200 only with ok). The login token is one of LOGIN_STATES or None (absent or anything else; incident 2026-11-01:
    `refused` was on /health and nothing read it)."""
    try:
        r = get(f"{url.rstrip('/')}/health", timeout=timeout_s, stream=True)
    except requests.RequestException:
        return "noanswer", None, None
    try:
        buf = bytearray()
        for chunk in r.iter_content(4096):
            buf += chunk
            if len(buf) > TERMINAL_PROBE_MAX_BYTES:
                return "noanswer", None, None
        body = json.loads(bytes(buf))
    except (requests.RequestException, ValueError):
        return "noanswer", None, None
    finally:
        try:
            r.close()
        except Exception:                                # noqa: BLE001
            pass
    if not isinstance(body, dict):
        return "noanswer", None, None
    state = body.get("terminal")
    if state is None:
        return ("unreported", None, None) if r.status_code == 200 and body.get("ok") is True else ("noanswer", None, None)
    if state not in TERMINAL_STATES or r.status_code != (200 if state == "ok" else 503):
        return "noanswer", None, None
    age = body.get("terminal_for_s")
    raw_login = body.get("login")
    login = raw_login if isinstance(raw_login, str) and raw_login in LOGIN_STATES else None
    return (str(state), age if isinstance(age, int) and not isinstance(age, bool) and age >= 0 else None, login)


def bridge_server_tz(url: str, timeout_s: float = 5.0, get: Callable[..., Any] = requests.get) -> str | None:
    """The server_tz the bridge converts MT5 stamps with (GET /health), or None when it does not say (an older bridge,
    unreachable, junk). The brain compares it with its own accounts.yaml in preflight."""
    try:
        r = get(f"{url.rstrip('/')}/health", timeout=timeout_s)
        tz = r.json().get("server_tz") if r.status_code == 200 else None
    except (requests.RequestException, ValueError, AttributeError):
        return None
    return tz if isinstance(tz, str) else None


class RemoteBroker:
    """The `Broker` protocol over the bridge. Read-only calls retry once; order calls never retry. `observe_only`
    (ADR 0063): every call outside OBSERVE_METHODS raises ObserveOnlyRefused before anything is sent."""

    def __init__(self, url: str, token: str, *, name: str = "mt5-remote", timeout_s: float = 10.0,
                 order_timeout_s: float = 15.0, post: Callable[..., Any] | None = None,
                 observe_only: bool = False) -> None:
        self.url, self.name = url.rstrip("/"), name
        self.observe_only = observe_only
        self._token, self.timeout_s, self.order_timeout_s = token, timeout_s, order_timeout_s
        self.read_attempts = 2          # read-only calls; a paced caller (the tick record) sets 1 and its own timeout_s
        self._session = requests.Session()
        self._post = post or self._session.post

    def _call(self, method: str, *args: Any) -> Any:
        if self.observe_only and method not in OBSERVE_METHODS:
            raise ObserveOnlyRefused(f"{OBSERVE_REFUSAL}: {method} refused for {self.name}; nothing was sent (ADR 0063)")
        _, retry = METHODS[method]
        attempts = self.read_attempts if retry else 1
        timeout = self.timeout_s if retry else self.order_timeout_s
        for i in range(attempts):
            # timeout_s (v7): the bridge drops a read still queued after it (BACKLOG 135); v6 bridges ignore it
            body = json.dumps({"method": method, "args": [_encode(a) for a in args], "sent_at": time.time(),
                               "timeout_s": timeout})
            try:
                r = self._post(f"{self.url}/rpc", data=body, timeout=timeout,
                               headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"})
            except requests.RequestException as exc:
                if i + 1 < attempts:
                    continue
                raise BridgeUnavailable(f"bridge {method}: {type(exc).__name__}") from exc
            if r.status_code != 200:
                try:
                    msg = r.json().get("error", "")
                except ValueError:
                    msg = r.text[:200]
                raise BridgeError(f"bridge {method}: HTTP {r.status_code} {msg}")
            return _decode(r.json()["result"], response=True)
        raise AssertionError("unreachable")

    def symbol_info(self, symbol: str) -> SymbolInfo:
        return self._call("symbol_info", symbol)

    def account(self) -> AccountInfo:
        return self._call("account")

    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
        return self._call("get_bars", symbol, tf, n)

    def get_bars_range(self, symbol: str, tf: str, start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> pd.DataFrame:
        return self._call("get_bars_range", symbol, tf, start_utc, end_utc)

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        return self._call("copy_ticks", symbol, since_utc, n)

    def last_tick(self, symbol: str) -> Tick:
        return self._call("last_tick", symbol)

    async def stream_ticks(self, symbol: str) -> AsyncIterator[Tick]:  # pragma: no cover - the engine polls last_tick
        import asyncio
        last = None
        while True:
            t = self.last_tick(symbol)
            if last is None or tick_key(t) != last:
                last = tick_key(t)
                yield t
            await asyncio.sleep(0.25)

    def margin_required(self, symbol: str, side: int, lots: float, price: float) -> float | None:
        return self._call("margin_required", symbol, side, lots, price)

    def loss_per_lot(self, symbol: str, side: int, price: float, distance: float) -> float | None:
        return self._call("loss_per_lot", symbol, side, price, distance)

    def trading_sessions(self, symbol: str) -> list[TradingSession]:
        return self._call("trading_sessions", symbol)

    def symbols_get(self, pattern: str = "*") -> list[str]:
        return self._call("symbols_get", pattern)

    def place_order(self, intent: OrderIntent) -> OrderResult:
        return self._call("place_order", intent)

    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        return self._call("modify", position_id, sl, tp)

    def close(self, position_id: int, lots: float | None = None) -> OrderResult:
        return self._call("close", position_id, lots)

    def positions(self, magic_prefix: int | None = None) -> list[Position]:
        return self._call("positions", magic_prefix)

    def account_positions(self) -> list[Position]:
        return self._call("account_positions")

    def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame:
        return self._call("deals_since", since_utc)

    def broker_terms(self, account_id: str, since_utc: pd.Timestamp, now: pd.Timestamp) -> BrokerTerms:
        return self._call("broker_terms", account_id, since_utc, now)
