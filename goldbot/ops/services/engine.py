"""The engine service (`python -m goldbot.ops.run engine <account_id>`): one engine loop per account, and the helpers
the loop runs on every pass (session refresh, bridge compatibility, the guarded tick pass, start refusal, risk limits).

Moved verbatim from goldbot/ops/run.py, which re-exports every name; calls between moved names go through
goldbot.ops.run (`resolve`, goldbot/ops/services/_base.py) so patching there still takes effect.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from goldbot.ops.services._base import log, resolve

if TYPE_CHECKING:   # service entry points import lazily; these are for annotations only
    import pandas as pd

    from goldbot.config import Settings
    from goldbot.data.calendar import SessionTable
    from goldbot.ops.accounts import Account
    from goldbot.ops.watchdog import LoopWatch
    from goldbot.risk import RiskLimits
    from goldbot.risk.limits_profile import LimitsPolicy


SESSIONS_TIMEOUT_S = 5.0     # a session read slower than this keeps the last table (the bridge RPC times out at 10 s)


class SessionRefresher:
    """Reads the engine's next session table (D13) on a background thread so the tick loop never waits for the broker:
    `start(now)` launches a read (never two at once; a hung read blocks only further reads), `collect(now, current)`
    returns the table to use once the read finished, else None. A read that raised keeps `current`; so does a static
    fallback while `current` is a broker table still covering `now` (a failed re-read must not throw away a holiday
    schedule already read)."""

    def __init__(self, source: Any) -> None:
        self.source = source
        self._thread: Any = None
        self._box: dict[str, Any] = {}

    def start(self, now: "pd.Timestamp") -> bool:
        import threading
        if self._thread is not None:
            return False                 # a read is running (or finished and not collected yet)
        box: dict[str, Any] = {}

        def work() -> None:
            try:
                box["table"] = self.source.table(now)
            except Exception as exc:      # the type only: a bridge error can name the tunnel endpoint
                box["error"] = type(exc).__name__
        self._box = box
        self._thread = threading.Thread(target=work, name="sessions-refresh", daemon=True)
        self._thread.start()
        return True

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def collect(self, now: "pd.Timestamp", current: "SessionTable | None") -> "SessionTable | None":
        if self._thread is None or self._thread.is_alive():
            return None
        self._thread, box = None, self._box
        if "table" not in box:
            log.warning("session table refresh failed (%s); keeping the last table", box.get("error", "no result"))
            return current
        new = box["table"]
        if new.source != "broker" and current is not None and current.source == "broker" \
                and current.covers_until is not None and now < current.covers_until:
            log.warning("session table fell back to static (%s); keeping the broker table read earlier", new.note)
            return current
        return new


def refresh_sessions(source: Any, now: "pd.Timestamp", current: "SessionTable | None",
                     timeout_s: float = SESSIONS_TIMEOUT_S) -> "SessionTable | None":
    """One blocking read through `SessionRefresher`, for the engine's start only (before the tick loop runs): waits at
    most `timeout_s`, then keeps `current`."""
    r = resolve(SessionRefresher)(source)
    r.start(now)
    if r._thread is not None:
        r._thread.join(timeout_s)
    if r.running():
        log.warning("session table refresh took over %g s; keeping the last table", timeout_s)
        return current
    return r.collect(now, current)


def _set_bridge_verdict(eng: Any, verdict: Any) -> None:
    prev = eng.bridge_compat
    if prev is None or prev.status != verdict.status:
        level = logging.ERROR if verdict.blocks else logging.WARNING if verdict.status != "ok" else logging.INFO
        log.log(level, "bridge %s: %s%s", verdict.status, verdict.reason,
                " -- entries refused (bridge_incompatible), exits unaffected" if verdict.blocks else "")
    eng.set_bridge_compat(verdict)


def apply_bridge_compat(eng: Any, new: Any, now: "pd.Timestamp | None" = None) -> None:
    """Hand the engine the bridge verdict (goldbot/ops/bridge_compat.py, BACKLOG 42): `next_verdict` (an unreachable
    /health never confirms a bridge, keeps an earlier `incompatible`), then `effective` (an ok over 5 min old is
    unchecked); a change of status is logged once. The engine's gate reads the flag for entries only
    (`bridge_incompatible`); exits never do. None (the check did not finish in time) changes nothing."""
    import pandas as pd

    from goldbot.ops.bridge_compat import effective, next_verdict
    if new is None:
        return
    verdict = effective(next_verdict(eng.bridge_compat, new), now if now is not None else pd.Timestamp.now("UTC"))
    resolve(_set_bridge_verdict)(eng, verdict)


def expire_bridge_compat(eng: Any, now: "pd.Timestamp") -> None:
    """Every loop pass: a non-blocking verdict whose last confirmation is over 5 min old becomes `unchecked` (a hung
    check thread or a long silence never leaves an old ok in force). No I/O."""
    from goldbot.ops.bridge_compat import effective
    v = eng.bridge_compat
    if v is not None and not v.blocks:
        e = effective(v, now)
        if e is not v:
            resolve(_set_bridge_verdict)(eng, e)


def begin_bridge_check(eng: Any, checker: Any, timeout_s: float | None = None) -> None:
    """Engine start: entries refused (`unchecked`) BEFORE the first check, then one blocking check of at most
    `timeout_s`; a check that does not finish leaves the engine blocked until the loop collects a verdict."""
    from goldbot.ops.bridge_compat import CHECK_TIMEOUT_S, unchecked
    resolve(_set_bridge_verdict)(eng, unchecked("engine starting"))
    resolve(apply_bridge_compat)(eng, checker.run_once(CHECK_TIMEOUT_S + 1.0 if timeout_s is None else timeout_s))


def engine_pass(eng: Any, broker: Any, symbol: str) -> bool:
    """One pass of the engine loop: read the latest tick and feed it. Any exception (an AssertionError too) is recorded
    on the engine (`tick_error`: entries blocked until a clean decision bar, rate-limited logging, alerted by health)
    and the loop carries on, so a bad tick or a terminal fault never crash-loops the engine (the next pass retries the
    exits). A `last_tick` read that raises also runs `Engine.no_quote_pass` (closes already decided, a pending
    kill-switch flatten, host-clock time barriers and the Friday flatten). False on an error."""
    try:
        tick = broker.last_tick(symbol)
    except Exception as exc:                 # no quote at all: record it, then the exits that need no price
        if not _behind_order(eng, exc):
            resolve(_record_tick_error)(eng, exc)
        try:
            eng.no_quote_pass()
        except Exception:
            log.exception("engine no-quote pass failed")
        return False
    try:
        eng.on_tick(tick)
        return True
    except Exception as exc:
        resolve(_record_tick_error)(eng, exc)
        return False


def _behind_order(eng: Any, exc: BaseException) -> bool:
    """A tick read that timed out behind our own entry order on the MT5 box (engine/order_send.py `excused`): logged,
    not a tick error; entries are refused `unconfirmed_order_pending` meanwhile and the no-quote pass still runs."""
    from goldbot.engine import order_send
    try:
        excused = order_send.excused(eng, exc)
    except Exception:
        log.exception("order-in-flight check failed")
        return False
    if excused:
        log.warning("tick read failed (%s) while an entry order is in flight on the MT5 box: not a tick error",
                    type(exc).__name__)
    return excused


def _record_tick_error(eng: Any, exc: BaseException) -> None:
    try:
        eng.note_tick_error(exc)
    except Exception:
        log.exception("engine tick failed and the error could not be recorded")


def refuse_start(account_id: str, why: str, info: Any = None, state_dir: str | Path = "state", *,
                 probe_only: bool = False) -> NoReturn:
    """End an engine start the terminal-account check refused (review LOW-A): `startup_error` goes into
    engine_<account>.json first, so health names the cause instead of showing a stale engine, then SystemExit. The
    deposit currency is kept for the supervisor's currency check; no login, server or balance is written. Merged into
    the previous engine file (review LOW-D: the last-known exposure keeps counting; `write_startup_error`).
    `probe_only` is the account's configured flag (ADR 0037 amendment 4): a probe account's refusal halts no real one."""
    from goldbot.risk.supervisor import write_startup_error
    currency = str(getattr(info, "currency", "") or "").strip().upper() or None
    try:
        write_startup_error(state_dir, account_id, why, dq_checks=["startup_refused"], currency=currency,
                            probe_only=probe_only)
    except OSError:
        log.exception("startup refusal not written to the engine state")
    raise SystemExit(why)


# SRE finding 2026-10-27: an account `enabled: false` in config/accounts.yaml never trades. Its refusal exits with
# EX_CONFIG, which goldbot-engine@.service (RestartPreventExitStatus=) and NSSM (AppExit 78 Exit) never restart: a
# config refusal does not cure itself in 10 s, unlike a bridge outage (Restart=always).
ENGINE_DISABLED_EXIT = 78


def refuse_disabled(account_id: str, why: str) -> NoReturn:
    """End the start of an account that may not trade: the reason in the journal, then ENGINE_DISABLED_EXIT. Nothing
    is written to engine_<account>.json: the supervisor halts every real account on a `startup_error` there
    (`engine_startup_refused`), and a switched-off account must not stop the enabled ones."""
    log.error("refusing to start %s: %s (exit %d, not restarted)", account_id, why, ENGINE_DISABLED_EXIT)
    raise SystemExit(ENGINE_DISABLED_EXIT)


def disabled_wind_down(acc: Account, broker: Any) -> bool:
    """A demo account `enabled: false`: True when it must start to wind down, else the start is refused
    (`refuse_disabled`). Design rule: exits are never gated, so a position this engine owns (its magic range, as
    Engine._own) is never stranded by a switch-off: the engine starts with every entry refused `account_disabled`
    and manages the exits; a positions read that fails counts as a position. With nothing of its own open the start is
    refused; the next restart after the wind-down is flat refuses the same way."""
    try:
        own = [p for p in broker.positions() if acc.magic_base <= p.magic < acc.magic_base + 100]
    except Exception as exc:  # noqa: BLE001 - unknown exposure: manage it rather than strand it
        log.warning("%s is disabled and its positions are unreadable (%s): winding down, entries refused",
                    acc.account_id, type(exc).__name__)
        return True
    if not own:
        refuse_disabled(acc.account_id, "enabled: false in config/accounts.yaml and no position of its own open")
    log.warning("%s is disabled with %d position(s) of its own open: winding down, entries refused, exits managed",
                acc.account_id, len(own))
    return True


def start_refused(acc: Account, why: str, info: Any = None) -> NoReturn:
    """A start check failed (approval ids, the engine lock, no broker, a wrong terminal account, symbol terms). An
    enabled account ends through `refuse_start` (startup_error written, restarted); a disabled one through
    `refuse_disabled` (trading-safety LOW on 893f0cc6): exit 78, nothing written, so a switched-off account's failed
    start never halts the enabled accounts through the supervisor nor restart-loops."""
    if not acc.enabled:
        refuse_disabled(acc.account_id, f"enabled: false in config/accounts.yaml; {why}")
    resolve(refuse_start)(acc.account_id, why, info, probe_only=acc.probe_only)


# trading-safety review of 10a18031 (M1): an account with no bridge and no local terminal refuses to start; the paper
# stand-in (fake fills under the account's broker key, its £ daily limit dropped) only on this explicit developer opt-in
PAPER_ENGINE_ENV = "GOLDBOT_PAPER_ENGINE"
# the engine prices in these terms (spread points over 0.01, stops and targets rounded to 2 decimals, RiskLimits.point)
PRICE_POINT, PRICE_DIGITS = 0.01, 2


def symbol_terms_problem(info: Any) -> str | None:
    """Why the engine would misprice this terminal symbol, or None (trading-safety review LOW of 10a18031): the spread
    in points, the stop/target rounding and the gate's point all assume a $0.01 point and 2-decimal prices (IC Markets
    XAUUSD and Vantage XAUUSD+ both quote 2 digits). Any other quote refuses the start instead of sending orders
    rounded to the wrong tick."""
    point, digits = float(info.point), int(info.digits)
    if abs(point - PRICE_POINT) > 1e-12 or digits != PRICE_DIGITS:
        return (f"symbol {info.name} quotes point {point} with {digits} digits; the engine prices in point "
                f"{PRICE_POINT} and {PRICE_DIGITS} digits")
    return None


def release_symbol(settings: Settings, acc: Account) -> str:
    """The store symbol of the research release bars for `acc` (trading-safety review M2 of 10a18031): the canonical
    name whose `brokers.<broker>.symbol_map` entry is the account's traded symbol (Vantage XAUUSD+ -> XAUUSD), else
    the traded symbol itself. Orders always use `acc.symbol`."""
    b = settings.brokers.get(acc.broker)
    for canonical, traded in (b.symbol_map.items() if b is not None else ()):
        if traded == acc.symbol:
            return canonical
    return acc.symbol


LONGEST_CLOSE_S = 9_999_999_999        # the widest ten-digit bar close (2286): the longest id an engine makes


def approval_ids_problem(account_id: str) -> str | None:
    """Why no approval card could carry this account's proposal ids, or None (trading-safety review LOW-2 of 2c6e805):
    the longest id, from the engine's own `Engine._proposal_id` (it reads only `cfg.account_id`, so it is called on a
    stand-in holding just that), must pass `valid_proposal_id` and fit every Telegram button. Checked at engine start,
    before any broker call, so an engine never raises proposals nobody can approve."""
    from types import SimpleNamespace

    import pandas as pd

    # the class itself (runner), not goldbot.engine.Engine, which tests replace with a stand-in
    from goldbot.engine.runner import Engine
    from goldbot.telegram.approvals import Proposal, decision_callbacks
    stand_in: Any = SimpleNamespace(cfg=SimpleNamespace(account_id=account_id))
    pid = Engine._proposal_id(stand_in, "x", pd.Timestamp(LONGEST_CLOSE_S, unit="s", tz="UTC"))
    try:
        decision_callbacks(Proposal(proposal_id=pid, account_id=account_id, agent_id="x", side=1, lots=0.0, entry=0.0,
                                    stop=0.0, target=0.0, p=0.0, ev_r=0.0, spread_points=0.0, top_features=[]))
    except ValueError as exc:
        return f"its proposal ids cannot be approved: {exc}"
    return None


def start_loop_watch(account_id: str, state_dir: str | Path = "state") -> "LoopWatch":
    """The engine's LoopWatch, started: Python tracebacks of every thread go to stderr (the journal) on the
    watchdog's SIGABRT, and phase "starting" beats feed the watchdog until `ready()` (a long reconcile or model load
    never trips it)."""
    import faulthandler

    from goldbot.ops.watchdog import LoopWatch
    if sys.stderr is not None:
        faulthandler.enable()                      # SIGABRT from WatchdogSec: where the loop was stuck, in the log
    watch = LoopWatch(state_dir, account_id)
    watch.starting()
    return watch


def run_engine(account_id: str) -> None:
    watch = resolve(start_loop_watch)(account_id)   # loop heartbeat + systemd watchdog (goldbot/ops/watchdog.py)
    try:
        resolve(_run_engine)(account_id, watch)
    finally:                                   # a refused or failed start: no beats from a process on its way out
        watch.stop()


def _run_engine(account_id: str, watch: "LoopWatch") -> None:
    from pathlib import Path

    import pandas as pd

    from goldbot.config import load_settings
    from goldbot.engine import Engine, EngineConfig, broker_bars, exploration, order_send
    from goldbot.execution.costs import settings_swap
    from goldbot.ops import accounts
    from goldbot.research.model_registry import ModelRegistry
    from goldbot.research.population import Population
    from goldbot.specialists import SPECIALISTS
    from goldbot.telegram.approvals import ApprovalCenter
    from goldbot.telegram.bus import ApprovalBus
    configured = accounts.load_accounts()
    acc = configured[account_id]
    # before any broker connection: a live account trades only enabled AND past the phase gate, never to wind down
    if acc.is_live and acc not in accounts.enabled_accounts("live", loaded=configured):
        refuse_disabled(account_id, "live account not enabled or not unlocked by the phase gate")
    if why := approval_ids_problem(account_id):
        start_refused(acc, f"refusing to start {account_id}: {why}")
    from goldbot.ops.engine_lock import hold_engine
    if why := hold_engine("state", account_id):      # held for the process's life: no repair rewrites its files
        start_refused(acc, f"refusing to start {account_id}: {why}")
    settings = load_settings()
    bridge = accounts.bridge_endpoint(account_id)
    if bridge is not None:
        # terminal on another machine (Oracle free tier: MT5 under Wine); a bridge failure ends this process and the
        # service manager restarts it, so restart reconciliation settles any order whose result was lost (row X7)
        from goldbot.execution.bridge import RemoteBroker
        broker: Any = RemoteBroker(bridge[0], bridge[1], name=f"mt5-remote-{account_id}")
        info = broker.account()
        why = accounts.verify_terminal_account(acc, info)
        if why:
            start_refused(acc, f"refusing to trade {account_id} through the bridge: {why}", info)
    elif sys.platform == "win32":
        from goldbot.execution.mt5_adapter import MT5Broker
        acc = accounts.ensure_login(acc)
        broker = MT5Broker(terminal_path=acc.terminal_path, login=acc.login, password=accounts.account_password(acc),
                           server=acc.server, server_tz=acc.server_tz, symbol=acc.symbol, account_label=account_id,
                           # the terminal can be switched to another login after this start check: every entry is
                           # checked again before it is sent (closes never are), as the bridge does
                           verify=lambda info: accounts.verify_terminal_account(acc, info))
        info = broker.account()
        why = accounts.verify_terminal_account(acc, info)
        if why:
            start_refused(acc, f"refusing to trade {account_id}: {why}", info)
    elif os.environ.get(PAPER_ENGINE_ENV) == "1":
        from goldbot.execution.paper import PaperBroker
        broker = PaperBroker(symbol=acc.symbol)
        log.warning("%s=1: running %s against the paper broker (developer stand-in)", PAPER_ENGINE_ENV, account_id)
    else:
        start_refused(acc, f"refusing to start {account_id}: no MT5 bridge (mt5-bridge-url/token in the keyring) "
                           "and no local terminal on this host")
    try:
        why = symbol_terms_problem(broker.symbol_info(acc.symbol))
    except Exception as exc:  # noqa: BLE001 - a symbol the terminal cannot describe is never traded
        why = f"symbol_info({acc.symbol}) unavailable ({type(exc).__name__})"
    if why:
        start_refused(acc, f"refusing to trade {account_id}: {why}")
    wind_down = not acc.enabled and disabled_wind_down(acc, broker)   # refuses when nothing of its own is open
    paper =resolve(paper_stand_in)(broker)
    policy = resolve(engine_limits_policy)(settings, acc, paper=paper, state_dir="state",
                                           accounts=list(configured.values()))
    limits = policy.defaults
    # proposals go to the approval bus in state/; the dashboard and the Telegram service decide through it
    center = ApprovalCenter(set(settings.telegram.allowed_user_ids), bus=ApprovalBus("state"))
    registry_file = Path(settings.research.models_dir) / "registry.json"

    population_file = Path("state") / "population.json"

    def champions() -> dict:
        # no champion for an agent -> no model -> the engine never proposes for it (no placeholder probabilities)
        return ModelRegistry(settings.research.models_dir).champion_models()

    def live_versions() -> dict[str, str]:
        # agent -> its champion's version: only that version's shadow wins clear its loss-streak throttle (BACKLOG 21)
        reg = ModelRegistry(settings.research.models_dir)
        return {aid: ch.version for aid in reg.agent_ids() if (ch := reg.champion(aid)) is not None}

    def shadow_set() -> dict:
        reg = ModelRegistry(settings.research.models_dir)
        pop = Population(population_file)
        pop.ensure_founders(pd.Timestamp.now("UTC"))
        for m in pop.in_book():                           # ADR 0038: a roster rule agent shadow-trades a RuleModel
            if getattr(SPECIALISTS.get(m.family), "frozen_config", False):
                reg.ensure_rule_challenger(family=m.family, agent_id=m.agent_id)
        return reg.shadow_models()

    def population_view() -> tuple[list, dict[str, float]]:
        """Agents in the shadow book (live, shadow, recently retired) and the live agents' capital shares."""
        pop = Population(population_file)
        pop.ensure_founders(pd.Timestamp.now("UTC"))
        members = pop.in_book()
        # ADR 0037 amendment 4: a probe_only account trades no live agent (the engine refuses one anyway)
        shares = {} if acc.probe_only else {m.agent_id: m.capital_weight for m in members if m.status == "live"}
        return [m.specialist() for m in members], shares

    shadow_host = is_shadow_host(acc, settings)

    agents, live_shares = population_view()
    eng = Engine(EngineConfig(account_id=account_id, broker_name=acc.broker, mode=acc.mode, declared_class=acc.account_class, approval_mode=acc.approval_mode, symbol=acc.symbol,
                              release_symbol=release_symbol(settings, acc),
                              cost_probe=acc.cost_probe.enabled, cost_probe_budget=acc.cost_probe.budget_share,
                              cost_probe_max_open=acc.cost_probe.max_open, cost_probe_max_loss=acc.cost_probe.max_loss,
                              cost_probe_scheduled=acc.cost_probe.scheduled,
                              exploration_config=exploration.config_for(acc),   # None unless enabled (plan 2026-10-25)
                              shadow_max_open=settings.shadow.max_open_per_agent,
                              magic_base=acc.magic_base, server_tz=acc.server_tz, state_dir="state", data_root=settings.data_root,
                              shadow_host=shadow_host, probe_only=acc.probe_only, wind_down=wind_down, halt_checks=True,
                              news_blackout=True,
                              blackout_before_min=settings.risk.blackout.before_min,
                              blackout_after_min=settings.risk.blackout.after_min,
                              shock_blackout_min=settings.news.shock_blackout_min,
                              shock_min_relevance=settings.news.shock_min_relevance, live_clock=True,
                              broker_bars=bridge is not None,   # through the bridge only (engine/broker_bars.py)
                              approval_window_s=settings.risk.approval_window_seconds,
                              tv_max_lag_s=settings.tv.max_lag_s,
                              swap_prior=settings_swap(settings)), broker, agents,
                 champions(), center, limits=limits, limits_policy=policy, shadow_models=shadow_set() if shadow_host else None,
                 live_shares=live_shares, live_versions=live_versions())
    if shadow_host and eng.store is not None:      # replica agents (box_break_close): carried state, shadow only
        resolve(attach_replica)(eng, settings)
    from goldbot.data.calendar import BrokerSessions, SessionTable
    # the session read runs on its own thread: through the bridge it gets its own client (a requests.Session is not
    # shared across threads); MT5Broker.trading_sessions makes no terminal call (documented hours)
    session_broker: Any = broker
    if bridge is not None:
        from goldbot.execution.bridge import RemoteBroker
        session_broker = RemoteBroker(bridge[0], bridge[1], name=f"mt5-remote-{account_id}-sessions")
    sessions = BrokerSessions(session_broker, acc.symbol, fallback=SessionTable(server_tz=acc.server_tz))
    table = resolve(refresh_sessions)(sessions, pd.Timestamp.now("UTC"), None)     # once, before the loop starts
    if table is not None:
        eng.set_sessions(table)
    refresher = resolve(SessionRefresher)(sessions)
    compat: Any = None                      # BACKLOG 42: the bridge's methods, protocol and zone against this brain's
    if bridge is not None:
        from goldbot.ops.bridge_compat import BridgeCompatChecker, check_bridge, repo_commit
        brain_commit, url, tz = repo_commit(), bridge[0], acc.server_tz
        compat = BridgeCompatChecker(lambda: check_bridge(url, tz, brain_commit))
        resolve(begin_bridge_check)(eng, compat)   # blocked (unchecked) first, then one check before the loop starts
    # the history fill (engine/history.py) pulls ticks on its own thread, so through the bridge it gets its own client;
    # a direct MT5 terminal (one module-wide connection) is never called from a second thread: no fill without a bridge
    history_broker: Any = None
    if bridge is not None:
        from goldbot.execution.bridge import RemoteBroker
        history_broker = RemoteBroker(bridge[0], bridge[1], name=f"mt5-remote-{account_id}-history")
        # the broker's M1 bars (engine/broker_bars.py) on their own thread and client: one attempt, a short timeout,
        # so the tick thread never waits on the bridge for them
        bars_client = RemoteBroker(bridge[0], bridge[1], name=f"mt5-remote-{account_id}-bars",
                                   timeout_s=broker_bars.FETCH_TIMEOUT_S)
        bars_client.read_attempts = 1
        eng.broker_m1.fetcher = broker_bars.Fetcher(bars_client)
        # entry orders (engine/order_send.py) on their own thread and client, with this account's order timeout, so a
        # slow fill neither stalls the tick thread nor raises as a tick error
        eng.order_sender = order_send.bridge_sender(bridge[0], bridge[1], name=f"mt5-remote-{account_id}-orders",
                                                    timeout_s=acc.order_timeout_s)
    warm = eng.warm_start(pd.Timestamp.now("UTC"), history_broker)   # store read only; the fill runs in the background
    log.info("engine %s started (%s), models: %s, %d 1m bars of history, risk per trade %.4f, limits %s", account_id,
             type(broker).__name__, sorted(eng.models), warm, limits.risk_per_trade, eng.limits_current.profile)
    def mtimes() -> tuple[float, ...]:
        return tuple(f.stat().st_mtime if f.exists() else -1.0 for f in (registry_file, population_file))

    seen = mtimes()
    last_check = time.time()
    watch.ready()                                  # READY=1: from here only this loop feeds the watchdog
    while True:
        watch.tick()                               # every pass, ticks or none: a stuck loop stops beating
        resolve(engine_pass)(eng, broker, acc.symbol)
        try:                                       # D13: a finished background read is applied; the loop never waits
            table = refresher.collect(pd.Timestamp.now("UTC"), eng.sessions)
            if table is not None:
                eng.set_sessions(table)
        except Exception:
            log.exception("session refresh failed; keeping the last table")
        if compat is not None:                     # a finished bridge check is applied; the loop never waits for it
            try:
                resolve(apply_bridge_compat)(eng, compat.collect())
                resolve(expire_bridge_compat)(eng, pd.Timestamp.now("UTC"))
            except Exception:                      # fail closed for entries; exits never read the verdict
                log.exception("bridge compatibility verdict not applied; entries refused until the next check")
                from goldbot.ops.bridge_compat import unchecked
                prev = eng.bridge_compat
                eng.set_bridge_compat(unchecked("verdict could not be applied", since=prev.unchecked_since
                                                if prev is not None and prev.status == "unchecked" else None))
        if time.time() - last_check > 60:          # promotions by the scheduler take effect without a restart
            last_check = time.time()
            refresher.start(pd.Timestamp.now("UTC"))   # once per server day the read reaches the broker
            if compat is not None:
                compat.start()                     # GET /health on its own thread (BACKLOG 42)
            now_m = mtimes()
            if now_m != seen:
                seen = now_m
                try:
                    agents, live_shares = population_view()
                    eng.agents = {a.agent_id: a for a in agents}
                    eng.live_shares = live_shares
                    eng.models, eng.live_versions = champions(), live_versions()
                    if shadow_host:
                        eng.set_shadow_models(shadow_set())
                    log.info("reloaded: %d agents, %d live, models %s, shadow %s", len(eng.agents), len(live_shares),
                             sorted(eng.models), sorted(eng.shadow_models))
                except (ValueError, TypeError, OSError) as exc:
                    log.error("model reload refused, keeping current models: %s", exc)
        time.sleep(0.25)


REPLICA_HISTORY_YEARS = 2


def is_shadow_host(acc: Account, settings: Settings) -> bool:
    """ADR 0037 amendment 4: one shadow book per deployment, run by the account flagged `shadow_host` in
    config/accounts.yaml (load_accounts refuses two enabled hosts). The book is priced on the canonical-cost broker, so
    a flag on another broker's account is logged and not hosted."""
    if not acc.shadow_host:
        return False
    cfg = settings.brokers.get(acc.broker)
    if not (cfg and cfg.canonical_costs):
        log.error("shadow_host is set on an account whose broker is not the canonical-cost broker: not hosted")
        return False
    return True


def attach_replica(eng: Any, settings: Settings) -> None:
    """Serve replica-reading agents (smc boxes, money flow, structure) from the carried state in state/aux.db
    (`engine.replica_context.ReplicaShadow`), bootstrapped from the store's bars since `tv.replica_series_start`.
    Shadow only; any failure leaves the replica unset, so those agents propose nothing (fail closed)."""
    import pandas as pd

    from goldbot.db.connection import Database
    from goldbot.engine.replica_context import ReplicaShadow, StoreHistory
    try:
        # bounded bootstrap: at most the last REPLICA_HISTORY_YEARS full years (fixed per calendar year, so a restart in
        # the same year resumes the stored state). Enough: the boxes, money flow and range are windows of <= 313 bars;
        # the swing structure and seeded averages forget their start within a few 50-bar swings (weeks on 4h)
        floor = pd.Timestamp(f"{pd.Timestamp.now('UTC').year - REPLICA_HISTORY_YEARS}-01-01", tz="UTC")
        start = max(pd.Timestamp(settings.tv.replica_series_start, tz="UTC"), floor)
        eng._tv.replica = ReplicaShadow(Database.open(Path(eng.cfg.state_dir), "aux"), symbol=eng.cfg.symbol.lower(),
                                        history=StoreHistory(eng.store, eng.cfg.symbol, start), series_start=start)
    except Exception:
        log.exception("replica state not attached: replica agents propose nothing")


def paper_stand_in(broker: Any) -> bool:
    """True when the engine runs against the paper broker standing in for the account (by type, never by its `name`
    string: re-verification LOW). Only then is the account's absolute daily limit dropped (engine_limits)."""
    from goldbot.execution.paper import PaperBroker
    return isinstance(broker, PaperBroker)


def engine_limits(settings: Settings, acc: Account, *, paper: bool = False) -> RiskLimits:
    """RiskLimits from settings.yaml `risk:`; a live account before the full-size gate (or with an unreadable phase
    file) gets the tiny-live risk per trade (and no minimum-lot exception). The account's own absolute daily loss
    limit (accounts.yaml, its deposit currency) applies only against its real terminal: `paper=True` (the paper broker
    standing in for it, a USD book) drops it, so paper and shadow evidence are not changed by a £ figure."""
    from goldbot.ops import accounts
    from goldbot.risk import RiskLimits
    limits = RiskLimits.from_settings(settings.risk, tiny_live=accounts.tiny_live_risk(acc))
    # plan 2026-10-25 (lane L1 contract): the exploration fence's account facts, never on the paper stand-in
    return limits.model_copy(update={"daily_loss_limit_abs": None if paper else acc.daily_loss_limit_abs,
                                     "probe_only": acc.probe_only and not paper,
                                     # PD 2026-10-26: the probe host's own open-position cap (demo probe_only only)
                                     **({"max_positions": acc.max_positions}
                                        if acc.max_positions is not None and not paper else {}),
                                     "exploration_arms": {} if paper else accounts.exploration_arm_caps(acc)})


def engine_limits_policy(settings: Settings, acc: Account, *, paper: bool = False, state_dir: str | Path = "state",
                         accounts: list[Account] | None = None) -> LimitsPolicy:
    """ADR 0032: the account's limits policy, which the engine resolves every account refresh: the defaults
    (`engine_limits`) and the controller, clamped to [default .. ceiling] on a demo account with `risk_overrides`,
    never above the defaults on any other account. The controller is ADR 0031's `AdaptiveLimitsController` (the
    nightly limits_job's decision, read from `state_dir`) on a demo account with ceilings while `risk.adaptive_limits`
    is on, else `StaticController` (the ceilings on such an account, the defaults elsewhere). `accounts`: every
    configured account, for the supervisor envelope (default: this one alone). The paper stand-in (`paper=True`)
    keeps the defaults: paper and shadow evidence are not changed by a demo profile."""
    from goldbot.engine.limits_apply import AdaptiveLimitsController
    from goldbot.ops.limits_job import adaptive_enabled, bounds_for
    from goldbot.risk.limits_profile import LimitsPolicy
    defaults = resolve(engine_limits)(settings, acc, paper=paper)
    if paper:
        return LimitsPolicy(acc.model_copy(update={"risk_overrides": None}), defaults)
    if not adaptive_enabled(settings, acc):
        return LimitsPolicy(acc, defaults)
    bounds = bounds_for(settings, acc, accounts if accounts is not None else [acc])
    return LimitsPolicy(acc, defaults, AdaptiveLimitsController(state_dir, acc.account_id, bounds))
