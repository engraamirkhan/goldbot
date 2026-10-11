"""Trading engine: one process per broker account.

Per decision-bar close:
  bars (from ticks) -> features (+ higher-TF context, no lookahead) -> candidates from each live agent
  -> model p -> allocator weight -> bet sizing -> RiskGate -> Proposal (propose mode) or direct order (auto)
  -> broker.place_order with SL/TP attached -> position management (time exit; the specialist's exit policy: trail,
  scale-out, hard flat; blackout early close) -> reconciliation
  -> engine state file for the supervisor and the API.

Everything below is deterministic and testable with PaperBroker and a replayed tick stream. The MT5
adapter plugs into the same `Broker` protocol on the VPS.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import zlib
from collections import deque
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.allocator import Regime, RuleAllocator, tier1_minutes
from goldbot.base import (
    Record,
    UtcTimestamp,
    load_record,
    load_record_json,
    note_schema_skew,
    schema_skew_summary,
    write_atomic,
)
from goldbot.config import tf_seconds
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable, session_table_cache
from goldbot.data.econ_calendar import blackout_window
from goldbot.data.news import shock_window
from goldbot.data.quality import DQEvent, bar_errors, events_frame, stale_feed
from goldbot.data.resample import BAR_COLUMNS, IncrementalResampler, mid, resample_bars, ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.timeutil import epoch_ns, feature_day, floor_tf, server_wall
from goldbot.engine.shadow import ShadowBook, ShadowTrade
from goldbot.execution.broker import Broker, OrderIntent, OrderResult, Position, SymbolInfo, Tick
from goldbot.execution.costs import CostTable
from goldbot.features import build_features
from goldbot.features.mtf import TF_LABEL, context_tfs, merge_higher_tf
from goldbot.features.registry import versions_for, with_versions
from goldbot.features.technical import atr
from goldbot.labels.excursion import Excursion
from goldbot.labels.exit_policy import ExitPolicy
from goldbot.ops import server_clock
from goldbot.ops.account_view import LADDER_TIMEFRAMES
from goldbot.ops.bridge_compat import BridgeCompat
from goldbot.ops.gates_phase import ClosedTrade, EntryAudit, append_closed_trade, load_closed_trades
from goldbot.research.metrics import breakeven_prob, size_multiplier
from goldbot.research.pipeline import CONTEXT_FEATURES, DEFAULT_FEATURE_NAMES
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits, open_risk, sizing
from goldbot.risk.gate import REARM_PHRASE, GateDecision, Stage, broker_margin, new_day
from goldbot.risk.supervisor import Supervisor, write_startup_error
from goldbot.risk.throttle import AgentThrottle, agent_throttle, latest_shadow_win
from goldbot.risk.windows import (
    HolidayList,
    calendar_gap,
    holiday_end,
    load_holidays,
    reopen_window,
    reopen_window_end,
    server_date,
)
from goldbot.specialists.base import Specialist
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal, ceil_cent

log = logging.getLogger("goldbot.engine")
LOT_TERMS_WARN_S = 600.0     # the account view's symbol-info warning, at most this often
APPROVED_RISK_TOLERANCE = 1.02   # ADR 0009: the risk sent may exceed the approved card's by 2% (fx moves), no more
# MT5 trade retcodes that mean the market will not take an order now (10017 TRADE_DISABLED, 10018 MARKET_CLOSED): a
# close refused with one of these is backed off; any other failure (requote 10004/10021, price changed 10020, a
# raising call) is retried on the next tick, so an exit is never delayed by a transient refusal
MARKET_CLOSED_RETCODES = frozenset({10017, 10018})
# MT5 trade-server retcodes that mean the order was definitely NOT placed, listed one by one (review LOW-1: only
# defined codes; 10037 does not exist): 10004 requote, 10006 reject, 10007 cancelled by trader, 10013-10022 invalid
# request / volume / price / stops, trade disabled, market closed, no money, prices changed, no quotes, invalid
# expiration, 10024-10030 too many requests, no changes, autotrading off (server, client), locked, frozen, invalid
# filling, 10032 only real accounts, 10033-10036 order and volume limits, invalid order type, position closed,
# 10038-10046 close volume, close order exists, position limit, reject cancel, long / short / close only, FIFO, hedge
# prohibited; the paper broker's duplicate refusal is 10027. Anything else that is not a fill (-1 send failed, 10008
# placed, 10010 partial, 10011 processing error, 10012 timeout, 10023 order state changed, 10031 no connection, any
# undefined or future code) is ambiguous: auto entries stop for the rest of that bar close (trading-safety review HIGH)
DEFINITE_REJECT_RETCODES = frozenset({
    10004, 10006, 10007,
    10013, 10014, 10015, 10016, 10017, 10018, 10019, 10020, 10021, 10022,
    10024, 10025, 10026, 10027, 10028, 10029, 10030,
    10032, 10033, 10034, 10035, 10036,
    10038, 10039, 10040, 10041, 10042, 10043, 10044, 10045, 10046,
})
# review MEDIUM-2 (d): a pending_orders row still `sending` this long after its send is resolved in-process
SENDING_RESOLVE_S = 60.0
# design (D10): a bar close is detected by clock, close time plus this grace, not by the next bar's first tick
BAR_CLOSE_GRACE_S = 1.5
LATE_TICK_WARN_S = 60.0      # at most one late_tick data-quality warning per this many seconds (every one is counted)
SKEW_WINDOW = 120            # new ticks in the rolling median of broker-vs-wall clock skew
# escalation to a blocking data-quality error: bars finalised without their last ticks drift from the training and
# shadow bars. |median skew| above SKEW_BLOCK_S (once SKEW_MIN_SAMPLES ticks are measured) -> `clock_skew`; late ticks
# on LATE_BARS_BLOCK consecutive decision bars -> `late_ticks` (cleared by a decision bar with none)
SKEW_BLOCK_S = 10.0
SKEW_MIN_SAMPLES = 10
LATE_BARS_BLOCK = 3
# an exception while processing a tick (the run loop logs it and carries on) blocks entries until a whole decision bar
# has closed with no error in it; exits never wait for it. Repeated errors are logged once per type, then at most every
# TICK_ERROR_LOG_S with a count, and the engine state is rewritten at most every TICK_ERROR_STATE_S while failing.
TICK_ERROR_LOG_S = 60.0
TICK_ERROR_STATE_S = 10.0
# a feed whose every quote has been invalid this long (host clock) is dead: only then do the priceless time barrier and
# the priceless Friday flatten act (re-verification NEW-1/2: never on a single bad quote). Equal to health's
# QUOTE_INVALID_FAIL_S (a test keeps them equal). Decided closes and the kill switch never wait for it
QUOTE_DEAD_S = 60.0
# a close refused for any reason but a closed market is re-sent no sooner than this (re-verification LOW-A)
CLOSE_RETRY_MIN_S = 1.0
TICK_LOG_MAX = 20_000        # ticks kept for the store while writes fail (a full disk): the oldest are dropped
# a position frozen at its broker stop or target (MAE/MFE, BACKLOG 23) that exits later than this was not taken there:
# the path after the freeze was not seen and the record says approximate
EXCURSION_FREEZE_TOLERANCE_S = 60.0


class Model(Protocol):
    feature_names: list[str]
    def predict(self, X: pd.DataFrame) -> np.ndarray: ...


def _score(model: Model, X: pd.DataFrame) -> tuple[float, float | None]:
    """(calibrated p, raw score or None) for one row: a MetaLabelModel exposes its raw score, so the shadow book can
    keep it for later recalibration; any other model gives p only."""
    raw_fn, cal_fn = getattr(model, "predict_raw", None), getattr(model, "calibrated", None)
    if raw_fn is None or cal_fn is None:
        return float(model.predict(X)[0]), None
    raw = np.asarray(raw_fn(X), dtype=float)
    return float(np.asarray(cal_fn(raw), dtype=float)[0]), float(raw[0])


class ConstantModel(Record):
    """Stand-in until a trained MetaLabelModel is loaded: returns a fixed probability."""
    p: float = 0.6
    feature_names: list[str] = Field(default_factory=list)
    feature_version: str = ""        # empty: an unversioned stand-in (a trained model always carries its version)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.full(len(X), self.p)


class EngineConfig(Record):
    account_id: str
    broker_name: str
    mode: str = "paper"              # paper | demo | live (what the broker is); approvals are separate
    approval_mode: str = "propose"   # propose | auto
    symbol: str = "XAUUSD"
    decision_tf: str = "15m"
    magic_base: int = 260100
    state_dir: str = "state"
    cost_atr: float = 0.10           # round-trip cost in ATR until the measured cost table exists
    max_bars_in_memory: int = 60000     # 1m bars kept (~40 trading days)
    feature_window: int = 800            # decision bars the features are computed on
    owner_user_id: int = 0
    # design (Approvals): the owner confirms an entry within this many seconds or it expires unapproved; production
    # passes settings.risk.approval_window_seconds (goldbot/ops/run.py)
    approval_window_s: int = Field(90, gt=0)
    data_root: str | None = None         # when set, ticks and fills are logged to the store for the nightly cost job
    shadow_host: bool = False            # this engine runs the shadow book (one per deployment: the canonical-cost broker)
    tick_flush_s: int = 60               # market seconds between tick-log flushes
    # production: block entries on the supervisor's halt (or a missing/stale heartbeat) and on the owner's halt
    halt_checks: bool = False
    # production: block entries around tier-1 events from the archived calendar (store table calendar_events)
    news_blackout: bool = False
    blackout_before_min: int = 15
    blackout_after_min: int = 30
    shock_blackout_min: int = 30             # after a high-relevance unscheduled news shock (store table news)
    shock_min_relevance: float = 0.7
    # production: tick age and feed staleness are measured against the wall clock (replays and tests use tick time)
    live_clock: bool = False
    # between bar closes the account is refreshed (drawdown stages, kill switch, halts) and the state file written
    # this often (tick time), so the supervisor's combined caps and the 12% kill switch act within seconds
    state_every_s: int = 10
    stale_feed_s: int = 90                   # no tick for this long in an open session -> data-quality error
    healthy_resume_s: int = 60               # after stale data or a data-quality error, entries wait this long healthy
    # 12% re-arm (design: Drawdown kill switch): the owner's TOTP re-arm is honoured only after this many trading days
    # of positive paper shadow since the halt, and is followed by this many days of propose-and-approve
    rearm_shadow_days: int = 10
    rearm_propose_days: int = 30
    reconcile_every_s: int = 30              # broker reconciliation (tick time), besides every bar close and the start
    orphan_stop_atr: float = 1.5             # an adopted orphan without a stop gets one this many ATR from entry
    # a rejected stop modify is re-sent by reconciliation after reconcile_every_s, doubling per reject up to this
    # (the per-tick stop check closes the trade at market meanwhile if the price reaches the engine's stop)
    modify_backoff_max_s: int = 900
    scale_out_retries: int = 3               # a failed scale-out is retried on later ticks this many times in all
    # a trade the broker no longer lists is recorded as closed once its exit deal is seen; a broker whose deal history
    # shows none is asked again at this many bar closes (a positions() reply that dropped it briefly costs nothing)
    close_confirm_checks: int = 8
    # a closed-trade record whose write fails stays queued and is retried every reconcile_every_s of tick time, this
    # many attempts in all; then it is reported lost (engine state `closed_records_lost`, a failing health check)
    closed_record_retries: int = 10
    server_tz: str = "Europe/Athens"         # broker server clock: rollover (00:00 +-5 min) and the Friday 21:30 rule
    rollover_min: int = 5
    weekend_cut: str = "21:30"               # Friday, server time: close losers, tighten winners, no new entries
    # measured costs: a broker that reports its swap and commission (MT5Broker.broker_terms) is read this often (tick
    # time) and written to state/broker_terms_<account>.json for the nightly cost job; commission from this many days
    broker_terms_every_s: int = 6 * 3600
    commission_window_days: int = 90


class _Frame(Record):
    dec: pd.DataFrame
    m: pd.DataFrame
    X: pd.DataFrame
    atr: pd.Series
    by_version: dict[str, pd.DataFrame | None] = Field(default_factory=dict)   # X rebuilt for older model versions


class OpenTrade(Record):
    position_id: int
    agent_id: str
    side: int
    lots: float
    entry_bar_ts: UtcTimestamp
    max_bars: int
    bars_held: int = 0
    sl: float | None = None           # the protection this engine expects at the broker (reinstated if lost)
    tp: float | None = None
    # the specialist's exit policy (labels.exit_policy), run on bars of the agent's timeframe as its labels were built
    policy: ExitPolicy | None = None
    timeframe: str | None = None
    entry: float | None = None        # fill price: the policy's distances are measured from it
    atr_usd: float | None = None      # the signal bar's ATR, which the policy's distances are in
    opened_utc: UtcTimestamp | None = None   # fill time (tick clock): bars closing after it count for the trail
    flat_at: UtcTimestamp | None = None      # hard-flat deadline
    scaled: bool = False              # the scale-out is done: taken, skipped (minimum volume) or out of retries
    scale_tries: int = 0              # failed scale-out attempts (bounded by EngineConfig.scale_out_retries)
    # for the closed-trade record (gates_phase.ClosedTrade): set at the fill, None on trades saved before they existed
    client_order_id: str | None = None
    open_price: float | None = None   # fill price (with or without an exit policy)
    initial_sl: float | None = None   # the stop at entry: R is measured against it
    initial_lots: float | None = None
    equity_before: float | None = None
    scaled_lots: float = 0.0          # taken by the scale-out, at scaled_price: folded into the final record
    scaled_price: float | None = None
    # MAE / MFE so far (BACKLOG 23), from every tick while this engine holds the position (`_track_excursions`);
    # approx: part of the path was not seen by ticks (carried across a restart, adopted), so the record fills it from
    # the stored 1m bars and flags it
    excursion: Excursion | None = None
    excursion_approx: bool = False
    # False for an orphan adopted without an order record: its initial stop is the broker's current one (maybe trailed
    # since entry), so the record leaves R-based excursions empty (prices and times are still recorded)
    risk_known: bool = True
    audit: EntryAudit | None = None   # finance audit entry facts for the record; None: adopted or saved before them


class PendingClose(Record):
    """A closed-trade record waiting to be written (EngineConfig.closed_record_retries attempts in all)."""
    position_id: int
    trade: OpenTrade
    reason: str
    price: float | None = None
    at: UtcTimestamp
    tries: int = 0
    next_try: UtcTimestamp | None = None


class SentOrder(Record):
    """One row of the pending_orders table (state/orders_<account>.json), written BEFORE order_send."""
    client_order_id: str
    ts_utc: UtcTimestamp
    agent_id: str
    side: int
    magic: int
    lots: float
    max_bars: int
    sl: float
    tp: float
    status: str = "sending"         # sending -> filled | rejected | unfilled (restart found no fill)
    position_id: int | None = None
    atr_usd: float | None = None    # the signal bar's ATR: a fill recovered on restart gets its exit policy back
    audit: EntryAudit | None = None  # finance audit entry facts, handed to a fill recovered on restart


class Engine:
    def __init__(self, cfg: EngineConfig, broker: Broker, agents: list[Specialist], models: dict[str, Model],
                 center: ApprovalCenter | None = None, allocator: RuleAllocator | None = None,
                 limits: RiskLimits | None = None, shadow_models: dict[str, tuple[str, Model]] | None = None,
                 live_shares: dict[str, float] | None = None, live_versions: dict[str, str] | None = None):
        self.cfg = cfg
        self.broker = broker
        self.agents = {a.agent_id: a for a in agents}
        self.models = models                 # agent_id (or family, for a single-agent setup) -> model
        # population: agent_id -> share of its family's capital; None = every agent trades at the family weight
        self.live_shares = live_shares
        # agent_id -> its champion's registry version (ops.run sets it with the models): the only shadow version whose
        # wins may clear the agent's loss-streak throttle (BACKLOG 21, review MEDIUM-3); unknown = none clears it
        self.live_versions: dict[str, str] = dict(live_versions or {})
        self.center = center or ApprovalCenter({cfg.owner_user_id})
        # design (Regime allocator): weights are 0 for 30 min either side of tier-1 events (RuleAllocator's defaults),
        # deliberately stricter than the RiskGate's -15/+30 entry block (blackout_before_min/after_min); changing the
        # allocator rule is the owner's decision (allocator/promotion.py), so the gate window never narrows it
        self.allocator = allocator or RuleAllocator()
        self.gate = RiskGate(limits)
        self.ticks: list[Tick] = []
        self.bars_1m = pd.DataFrame()
        self.open: dict[int, OpenTrade] = {}
        self.pending: dict[str, tuple[Intent, Proposal, Specialist]] = {}
        self.sent_ids: set[str] = set()
        self._orders: dict[str, SentOrder] = {}     # pending_orders: every id ever sent (pruned after a week)
        self.last_bar_close: pd.Timestamp | None = None   # open of the decision bar the last tick fell in
        self._closed_through: pd.Timestamp | None = None  # latest bar close processed (tick or clock path): never twice
        self.state = AccountState(equity=0, balance_closed_hwm=0, day_start_equity=0, week_start_equity=0,
                                  open_positions=0, margin_used=0, last_tick_age_s=0, spread_points=0)
        self.decisions: list[dict] = []
        self.store = Store(cfg.data_root) if cfg.data_root else None
        self._tick_log: list[Tick] = []
        self._journaled = 0                  # decisions already written to the store's journal
        self._cost_cache: tuple[float, CostTable | None] = (-1.0, None)
        self._class_cache: tuple[float, str] = (-2.0, "unknown")
        self._drift_cache: tuple[float, dict] = (-2.0, {})
        self._ctx_cache: dict[str, tuple[pd.Timestamp, tuple[pd.DataFrame, pd.DataFrame] | None]] = {}
        # per timeframe: completed 1m bars -> bars, re-aggregating only the groups that changed since the last close
        self._resamplers: dict[str, IncrementalResampler] = {}
        self._bars_clean: pd.DataFrame | None = None      # bars_1m as last built by _append_bars (duplicate-free)
        self._calendar: tuple[pd.Timestamp | None, pd.DataFrame] = (None, pd.DataFrame())
        self._blackout_event: dict | None = None
        # BACKLOG 17 holiday list: loaded once, when the window is first needed; (loaded, list or None if unusable)
        self._holidays: tuple[bool, HolidayList | None] = (False, None)
        # entry-window faults (review M1): when calendar_unavailable started (None while known) and why; whether the
        # reopen window is unknown; the archive's latest event and newest fetch, for the state file and health
        self._unavailable_since: pd.Timestamp | None = None
        self._unavailable_why: str | None = None
        self._reopen_fault = False
        self._calendar_seen: tuple[pd.Timestamp | None, pd.Timestamp | None] = (None, None)
        # when each window the gate refuses on ends (tier2_until, reopen_until, holiday), for the owner's displays
        self._window_ends: dict[str, Any] = {}
        self._window_end_failing: set[str] = set()     # windows whose end time is failing (logged once per episode)
        # the dashboard's account view (goldbot/ops/account_view.py), published in the state file; reporting only.
        # From the broker: balance, currency and margin (never the login or server), the symbol's lot terms; from the
        # bars: ATR(14) per ladder timeframe, refreshed at bar closes
        self._account_view: dict = {"balance": None, "currency": None, "margin": None, "margin_free": None}
        self._price: float | None = None
        self._lot_terms_warned = -LOT_TERMS_WARN_S
        self._ladder: dict = {"ladder_atr": {tf: None for tf in LADDER_TIMEFRAMES}, "lot_terms": None}
        self._news: tuple[pd.Timestamp | None, pd.DataFrame] = (None, pd.DataFrame())
        # shadow book: version -> (family, model); champions and challengers paper-trade without orders
        self.shadow = ShadowBook(cfg.state_dir) if cfg.shadow_host else None
        self.shadow_models: dict[str, tuple[str, Model]] = {}
        self.set_shadow_models(shadow_models or {})
        Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
        self.center.on_decision = self._on_decision
        # data quality: errors since the last bar close, and those the last bar close was decided with
        self._last_tick: Tick | None = None
        self._dq_pending: list[DQEvent] = []
        self._dq_bar_events: list[DQEvent] = []
        # late ticks (stamped before a bar the clock already finalised): dropped from the live bars but still logged,
        # so the nightly store has them; counted, and warned at most every LATE_TICK_WARN_S (wall clock)
        self._late_ticks = 0
        self._late_since_warn = 0
        self._late_warned_at: pd.Timestamp | None = None
        self._late_bar: pd.Timestamp | None = None   # close of the latest decision bar that got a late tick
        self._late_streak = 0                        # consecutive decision bars with late ticks, up to _late_bar
        # broker tick time minus the wall clock, seconds, over the last SKEW_WINDOW new ticks (median published)
        self._skew: deque[float] = deque(maxlen=SKEW_WINDOW)
        self._unhealthy_at: pd.Timestamp | None = None    # last time the data was seen stale or in error
        # host UTC clock for the live clock (tests replace it); ticks are compared with it, never the reverse
        self.clock: Callable[[], pd.Timestamp] = lambda: pd.Timestamp.now("UTC")
        # D13 session table: the static rule on this account's server clock until run.py sets the broker's (it refreshes
        # it outside the tick path, so on_tick never calls the broker for sessions); read with no I/O
        self.sessions: SessionTable = SessionTable(server_tz=cfg.server_tz)
        self.clock_probe: server_clock.ServerClockProbe | None = None   # latest server-clock measurement
        self.bridge_compat: BridgeCompat | None = None   # BACKLOG 42: run.py's bridge verdict; None: no bridge / unchecked
        self._tick_error_detail: str | None = None     # type of the last exception (never its message: it can name hosts)
        self.tick_errors_total = 0                     # since the engine started (engine state, health)
        self._last_tick_error_at: pd.Timestamp | None = None   # host clock (live) for health and the state file
        # the same error on the TICK clock, which decision bars are cut on: the latest tick stamp seen when it happened,
        # moved to the next tick's stamp (`_error_awaits_tick`) so an error between ticks is never dated too early
        self._error_tick_ts: pd.Timestamp | None = None
        self._error_awaits_tick = False
        self._err_log: dict[str, tuple[float, int]] = {}   # exception type -> (last logged, monotonic; suppressed since)
        self._excursion_errors: dict[str, int] = {}        # MAE/MFE tracking failures by type (reporting only)
        self._err_state_written = -1e18                # monotonic time of the last state write caused by an error
        self._tick_flush_retry: pd.Timestamp | None = None  # after a failed tick-log write, no retry before this
        self.ticks_dropped = 0                         # tick-log entries dropped while the store could not be written
        # risk periods and the owner's re-arm survive a restart (state/risk_<account>.json)
        self._last_reset: pd.Timestamp | None = None
        self._rearm_seen: str | None = None
        self._weekend_done: str | None = None        # server date of the last Friday the weekend rule ran
        self._halted_at: pd.Timestamp | None = None  # when the 12% kill switch tripped (re-arm conditions count from it)
        self._propose_only_until: pd.Timestamp | None = None   # after a re-arm: no auto entries before this
        self._rearm_refused: str | None = None       # why the last owner re-arm was refused (engine state, dashboard)
        self._load_risk_state()
        # closed-trade record (state/closed_trades.jsonl, read by the phase gates and the stop rule): positions already
        # recorded (key: this account + position id, so a restart never records one twice), closes waiting to be
        # written, and broker-side closes waiting for their exit deal (position -> bar closes asked)
        # consecutive failed broker reads (positions(), deal history): while either is non-zero the engine state carries
        # the `positions_unreadable` data-quality check, entries are blocked and exits keep trying every tick
        self._read_fails: dict[str, int] = {"positions": 0, "deals": 0}
        self._read_fail_at: dict[str, pd.Timestamp | None] = {}
        self._dq_warnings: list[DQEvent] = []        # non-blocking (an unreadable closed-trade line)
        trades, rec_err = load_closed_trades(Path(cfg.state_dir))   # bad lines are skipped, the rest still count
        if rec_err:
            log.warning("closed-trade record: %s", rec_err)
            self._dq_warnings.append(DQEvent(ts_utc=pd.Timestamp.now("UTC"), check="closed_trades_unreadable",
                                             severity="warning", detail=rec_err))
        self._recorded: set[int] = {t.position_id for t in trades
                                    if t.account_id == cfg.account_id and t.position_id is not None}
        # this account's closed trades, kept in step with the record (_record_closed): the throttle's losses and entries
        self._throttle_closed: list[ClosedTrade] = [t for t in trades if t.account_id == cfg.account_id]
        self._throttle_lost: list[PendingClose] = []    # records given up this process: provisional losses for the throttle
        self._shadow_cache: tuple[int, ShadowBook | None] = (-1, None)   # the host's shadow_book.json by mtime
        self._closes: list[PendingClose] = []
        self._records_lost: list[int] = []           # records given up after closed_record_retries failed writes
        self._currency = ""                          # the deposit currency, read once from the broker (BACKLOG 28)
        # symbol_info and the conversion, read once per decision cycle (bar close); the last contract size seen
        self._sym_cache: tuple[pd.Timestamp | None, SymbolInfo] | None = None
        self._fx_cache: dict[tuple[pd.Timestamp | None, int], float | None] = {}
        self._contract: float | None = None
        self._contract_tried = False
        self._min_lot_risk: float | None = None      # minimum lot's stop risk, account currency, at the latest stop
        # BACKLOG 19/35: each open position's remaining stop risk as last computed (published), and the conversion per
        # side read at most once a minute for it (side -> (minute, account currency per USD or None))
        self._open_risk_rows: list[dict[str, Any]] = []
        self._open_fx: dict[int, tuple[pd.Timestamp, float | None]] = {}
        # position ids in the last successful positions() read, None while unread (the auto-entry fill check)
        self._position_ids: set[int] | None = None
        # (bar close, why): auto entries stopped for the rest of that bar close after an ambiguous order or a failed
        # account re-read (review HIGH); a later bar close trades again on a fresh read
        self._auto_paused: tuple[pd.Timestamp, str] | None = None
        self._unconfirmed: dict[int, int] = {}
        # position -> (consecutive market-closed refusals, next attempt): closes back off only when the market is closed
        self._close_backoff: dict[int, tuple[int, pd.Timestamp]] = {}
        # closes the engine decided that have not succeeded yet: position -> (action, journal extras, last attempt);
        # retried every tick (kill switch, weekend, time exit, hard flat, trail, blackout, engine stop) until done
        self._close_due: dict[int, tuple[str, dict, pd.Timestamp | None]] = {}
        self._kill_pending = False                   # the kill switch has not seen this engine's book flat yet
        # an invalid feed (a dropped quote or a last_tick read that raised): since when on the host clock (engine state,
        # health FAILs after QUOTE_INVALID_FAIL_S), and when the state file was last written meanwhile
        self._quote_invalid_since: pd.Timestamp | None = None
        self._invalid_state_written: pd.Timestamp | None = None
        self._dq_repeats: dict[str, int] = {}        # ticks folded into the pending event of each check (LOW-1)
        self._load_orders()
        self._last_state_write: pd.Timestamp | None = None
        self._last_reconcile: pd.Timestamp | None = None
        self._last_atr: float | None = None          # decision-tf ATR at the last bar close (orphan stops)
        self._last_terms: pd.Timestamp | None = None  # last broker-terms reading (swap, commission)
        self._foreign: list[int] = []                # positions with unknown magic (manual trades): listed, never touched
        # position -> (consecutive rejected stop modifies, next attempt): reconciliation backs off (in memory)
        self._modify_backoff: dict[int, tuple[int, pd.Timestamp]] = {}
        self._archive_stale_proposals()

    def _archive_stale_proposals(self) -> None:
        """Proposals this account published before a restart can never be executed (the pending entries died with
        the process): archive the expired ones so the dashboard, Telegram and health checks do not list them."""
        bus = self.center.bus
        if bus is None:
            return
        for f in bus.pending_dir.glob(f"{self.cfg.account_id}-*.json"):
            try:
                p = load_record_json(Proposal, f.read_text(), source="approvals/pending")
            except (ValueError, OSError):
                continue
            if p.expired and p.proposal_id not in self.pending:
                bus.archive(p)

    # ------------------------------------------------------------------ ticks and bars
    def on_tick(self, t: Tick) -> list[dict]:
        """Feed a tick; returns any decisions made at a bar close. An approval or re-arm that raises is recorded as a
        tick error (entries blocked) and never stops this tick's exits."""
        try:
            self.center.poll_bus()      # approvals from the dashboard / Telegram service, applied within a tick
            self._poll_rearm(self._now(t))
        except Exception as exc:
            self.note_tick_error(exc)
        return self._on_tick(t)

    def _error_now(self) -> pd.Timestamp:
        if self.cfg.live_clock or self._last_tick is None:
            return self.clock()
        return self._last_tick.ts_utc

    def note_tick_error(self, exc: BaseException) -> None:
        """A tick raised (the run loop, an approval, the tick log, the risk-period read): block entries (`tick_error`)
        until a whole decision bar closes with no error in it, count it (`tick_errors_total`, `last_tick_error_at` in
        the engine state, alerted by health) and log it: the first of each type with its traceback, then at most every
        TICK_ERROR_LOG_S with the number suppressed. The state file is rewritten at most every TICK_ERROR_STATE_S."""
        kind = type(exc).__name__
        self.state.tick_error = True
        self._tick_error_detail = kind
        self.tick_errors_total += 1
        self._last_tick_error_at = self._error_now()
        self._error_tick_ts = self._last_tick.ts_utc if self._last_tick is not None else None
        self._error_awaits_tick = True
        mono = time.monotonic()
        last, suppressed = self._err_log.get(kind, (None, 0))
        if last is None or mono - last >= TICK_ERROR_LOG_S:
            more = f" ({suppressed} more since the last report)" if suppressed else ""
            log.error("tick processing failed (%s)%s; entries blocked until a clean decision bar, exits continue",
                      kind, more, exc_info=exc if last is None else None)
            self._err_log[kind] = (mono, 0)
        else:
            self._err_log[kind] = (last, suppressed + 1)
        if mono - self._err_state_written >= TICK_ERROR_STATE_S:
            self._err_state_written = mono
            try:
                self._write_state()
            except Exception:
                log.warning("engine state not written after a tick error")

    def _guard(self, fn: Callable[[], object]) -> None:
        """Run one non-exit step of a tick; an exception becomes a tick error instead of aborting the tick."""
        try:
            fn()
        except Exception as exc:
            self.note_tick_error(exc)

    def _on_tick(self, t: Tick) -> list[dict]:
        if not self._tick_ok(t):
            self.no_quote_pass()        # invalid quote: never enters the bars; entries blocked; priceless exits run
            return []
        self._quote_invalid_since = self._invalid_state_written = None
        prev, self._last_tick = self._last_tick, t
        if self._error_awaits_tick:     # date a pending tick error by this tick's stamp (never earlier than it was)
            self._error_tick_ts = t.ts_utc if self._error_tick_ts is None else max(self._error_tick_ts, t.ts_utc)
            self._error_awaits_tick = False
        # the run loop polls the terminal's latest quote several times a second, so a quiet market repeats the same
        # tick: a repeat only advances the clock (bar close by clock, exits), it is neither a new bar tick nor logged
        repeat = prev is not None and (prev.ts_utc, prev.bid, prev.ask) == (t.ts_utc, t.bid, t.ask)
        if not repeat:
            self._skew.append((t.ts_utc - self._now(t)).total_seconds())
            if self._closed_through is None or t.ts_utc >= self._closed_through:
                self.ticks.append(t)
            else:
                self._late_tick(t)      # never revises a bar the clock already finalised
        if hasattr(self.broker, "on_tick"):
            self._guard(lambda: self.broker.on_tick(t))   # type: ignore[attr-defined]  # paper broker fills
        # exits first: nothing that writes files or reads the account may stand between a quote and the stop check
        self._stop_check(t)
        self._retry_closes(t.ts_utc)       # closes decided earlier that have not succeeded: every tick until done
        if self._kill_pending and self.state.stage == Stage.HALTED:
            self._kill_switch(everything=False)   # until this engine's book is flat
        self._scale_out(t)
        self._track_excursions(t)          # reporting only, after every exit; never raises
        if not repeat:
            self._guard(lambda: self._log_tick(t))      # a full disk is a tick error, never a skipped exit
        self._refresh_broker_terms(t.ts_utc)
        self._guard(lambda: self._roll_risk_period(t.ts_utc))
        if self._last_reconcile is None or (t.ts_utc - self._last_reconcile).total_seconds() >= self.cfg.reconcile_every_s:
            self._reconcile(t.ts_utc)
        sec = tf_seconds(self.cfg.decision_tf)
        bar_close = pd.Timestamp((int(t.ts_utc.timestamp()) // sec) * sec, unit="s", tz="UTC")
        out: list[dict] = []
        due = self._due_close(t, bar_close)
        if due is not None:
            self._closed_through = due
            self._rebuild_bars(until=due)   # every minute before the close is complete; none at or after it joins
            out = self.on_bar_close(due, t)
            self._last_state_write = t.ts_utc
            bar_open = due - pd.Timedelta(seconds=sec)   # tick clock, as is _error_tick_ts (never the host clock)
            if self.state.tick_error and not self._error_awaits_tick and self._error_tick_ts is not None \
                    and self._error_tick_ts < bar_open:
                self.state.tick_error, self._tick_error_detail = False, None   # a whole clean decision bar closed
                self._write_state()
        elif self._last_state_write is None or (t.ts_utc - self._last_state_write).total_seconds() >= self.cfg.state_every_s:
            self._rebuild_bars()            # completed minutes join the bars between closes, so their age is current
            self._refresh_account(t)
            self._write_state()
            self._last_state_write = t.ts_utc
        self.last_bar_close = bar_close
        self._flush_closed()               # after every exit of this tick has been sent
        return out

    def _host_now(self) -> pd.Timestamp:
        """The clock for a pass without a valid quote: the host clock live, else the last valid tick's time (a replay
        never moves time on an invalid tick)."""
        if self.cfg.live_clock or self._last_tick is None:
            return self.clock()
        return self._last_tick.ts_utc

    def no_quote_pass(self) -> None:
        """A pass without a usable price (a quote `_tick_ok` dropped, or a `last_tick` read that raised: ops.run
        `engine_pass`). Trading-safety review MEDIUM-2: everything that needs our price is skipped, but the exits that
        do not still run: closes already decided are retried, a pending kill-switch flatten is sent (market orders need
        no price of ours), and time barriers and the Friday flatten are judged on the host clock. The state file keeps
        being written every state_every_s with `quote_invalid_since`, so health FAILs (alerted) once quotes have been
        invalid for QUOTE_INVALID_FAIL_S in market hours. Entries stay blocked (the data-quality error or tick error)."""
        now = self._host_now()
        if self._quote_invalid_since is None:
            self._quote_invalid_since = now
        self._guard(lambda: self._retry_closes(now))
        if self._kill_pending and self.state.stage == Stage.HALTED:
            self._guard(lambda: self._kill_switch(everything=False))
        self._guard(lambda: self._host_clock_exits(now))
        if self._invalid_state_written is None or \
                (now - self._invalid_state_written).total_seconds() >= self.cfg.state_every_s:
            self._invalid_state_written = now
            self._guard(self._write_state)
        self._guard(self._flush_closed)

    def _host_clock_exits(self, now: pd.Timestamp) -> None:
        """Without a price, and only once the feed is dead (re-verification NEW-1/2): never on a single bad quote.

        Time barrier: a trade whose `bars_held` plus the whole decision bars elapsed since the last processed close
        (`_closed_through`, or its entry bar) during open market hours (the session table) reaches `max_bars` is closed
        (`time_exit`), once quotes have been invalid for QUOTE_DEAD_S. A weekend or a daily break never counts.

        Friday flatten: past the weekend cut (server time) and before the weekend rule completed, every position of
        this engine is closed (`weekend_close_no_quote`: no valid quote tells losers from winners, and a flat book
        cannot gap through a stop) only when the feed is dead (invalid for QUOTE_DEAD_S), whatever happened around the
        cut (re-verification R1: a burst of bad quotes across the cut never flattens winners within seconds, and a
        feed that dies just after a valid post-cut quote never lets losers ride the weekend). A shorter gap leaves it to
        `_weekend_rule` on the next valid quote. Once per Friday; a refused close is retried every pass
        (`_retry_closes`)."""
        since = self._quote_invalid_since
        dead = since is not None and (now - since).total_seconds() >= QUOTE_DEAD_S
        if dead:
            sec = tf_seconds(self.cfg.decision_tf)
            for pid, tr in list(self.open.items()):
                opened = tr.opened_utc or tr.entry_bar_ts
                bases = [x for x in (self._closed_through, opened.floor(f"{sec}s") if opened is not None else None)
                         if x is not None]
                if pid in self._close_due or not bases:
                    continue
                if tr.bars_held + self._open_bars(max(bases), now) >= tr.max_bars:   # bars_held: closes processed
                    self._try_close(pid, "time_exit", now, clock="host")
        srv = server_wall(now, self.cfg.server_tz)[0]
        h, m = (int(x) for x in self.cfg.weekend_cut.split(":"))
        if not (srv.weekday() == 4 and srv.hour * 60 + srv.minute >= h * 60 + m) \
                or self._weekend_done == srv.date().isoformat():
            return
        self.state.weekend = True
        if not dead:
            return                                        # a short gap: `_weekend_rule` decides on the next valid quote
        positions = self._read_positions("the no-quote weekend flatten is retried next pass")
        if positions is None:
            return
        done = True
        for p in positions:
            if self._own(p.magic):
                ok = p.position_id not in self._close_due and \
                    self._try_close(p.position_id, "weekend_close_no_quote", now).ok
                done = done and ok
        if done:
            self._weekend_done = srv.date().isoformat()
            self._save_risk_state()

    def _open_bars(self, base: pd.Timestamp, now: pd.Timestamp) -> int:
        """Whole decision bars from `base` to `now` whose start the session table calls open; 0 when the table cannot
        answer (the time exit then waits for a bar close; the broker's stop stays in place)."""
        sec = tf_seconds(self.cfg.decision_tf)
        n = min(int((now - base).total_seconds() // sec), 7 * 86400 // sec)
        if n <= 0:
            return 0
        try:
            starts = pd.date_range(base, periods=n, freq=f"{sec}s")
            return int(np.asarray(self.sessions.is_open(starts)).sum())
        except Exception as exc:                          # noqa: BLE001 - a broken table never closes a trade
            log.warning("session table unusable for the priceless time barrier (%s)", type(exc).__name__)
            return 0

    def _due_close(self, t: Tick, bar_open: pd.Timestamp) -> pd.Timestamp | None:
        """Design (D10): "Bar close is detected by clock (close time plus 1.5 s grace), not by tick arrival." The bar
        the last tick fell in (opened at `last_bar_close`) is complete at open + decision_tf; it is finalised once the
        clock (the tick's time, or the wall clock in production) reaches that close plus BAR_CLOSE_GRACE_S, so a quiet
        market with no tick after the close still decides. A tick at or after the close finalises it at once: ticks
        are accepted in time order only (`_tick_ok`), so no later tick can belong to it. Each close is processed once,
        whichever path sees it first (`_closed_through`); a bar no tick fell in is never closed (as before)."""
        if self.last_bar_close is None:
            return None
        close = self.last_bar_close + pd.Timedelta(seconds=tf_seconds(self.cfg.decision_tf))
        if self._closed_through is not None and close <= self._closed_through:
            return None
        clock = max(t.ts_utc, self._now(t))
        if bar_open > self.last_bar_close or clock >= close + pd.Timedelta(seconds=BAR_CLOSE_GRACE_S):
            return close
        return None

    def warm_start(self, now: pd.Timestamp) -> int:
        """Seed the 1m history from the store (release bars synced by the scheduler) so a restarted engine can decide
        at once instead of rebuilding days of bars from live ticks; 1h agents and the daily context need weeks.
        Returns the number of bars loaded (0 without a store or bars)."""
        if self.store is None:
            return 0
        days = self.cfg.max_bars_in_memory // (60 * 23) * 7 // 5 + 3     # trading minutes -> calendar days
        b = self.store.read("bars_1m", symbol=self.cfg.symbol, start=now - pd.Timedelta(days=days), end=now)
        if b.empty:
            return 0
        b = b[[c for c in BAR_COLUMNS if c in b.columns]]
        b = b[pd.to_datetime(b["visible_at"], utc=True) <= now].sort_values("ts_utc")
        self.bars_1m = b.drop_duplicates("ts_utc", keep="last").tail(self.cfg.max_bars_in_memory).reset_index(drop=True)
        return len(self.bars_1m)

    def _rebuild_bars(self, until: pd.Timestamp | None = None) -> None:
        """Incremental: only ticks since the last completed minute are aggregated; bars accumulate. Without `until`
        the latest tick's minute may still receive ticks and stays open; with it (a bar close) every minute ending at
        or before `until` is complete and none starting at or after it is built."""
        if not self.ticks:
            return
        cutoff = until.floor("min") if until is not None else None
        if cutoff is None and self.ticks[0].ts_utc.floor("min") == self.ticks[-1].ts_utc.floor("min"):
            return      # every tick is in the current minute (ticks arrive in time order): no minute has completed
        tdf = pd.DataFrame({"ts_utc": [x.ts_utc for x in self.ticks], "bid": [x.bid for x in self.ticks], "ask": [x.ask for x in self.ticks]})
        new = ticks_to_1m(tdf, DEFAULT_SESSIONS)
        if new.empty:
            return
        if cutoff is None:
            cutoff = new["ts_utc"].iloc[-1]              # the current minute may still receive ticks
        done = new[new["ts_utc"] < cutoff]
        if not done.empty:
            self._check_new_bars(done)
            self.bars_1m = self._append_bars(done)
        self.ticks = [x for x in self.ticks if x.ts_utc >= cutoff]

    def _append_bars(self, done: pd.DataFrame) -> pd.DataFrame:
        """bars_1m + newly completed minutes, de-duplicated on ts_utc (last wins) and capped at max_bars_in_memory.
        When the buffer is one this method built (so already duplicate-free) and every new minute is later than its
        last bar, de-duplication cannot drop a row and is skipped: the same frame without hashing 60000 stamps on
        every 10-second refresh."""
        b, cap = self.bars_1m, self.cfg.max_bars_in_memory
        if b.empty:
            out = done.reset_index(drop=True)
        else:
            new_ts = epoch_ns(pd.DatetimeIndex(done["ts_utc"]))
            clean = b is self._bars_clean and len(new_ts) and bool((np.diff(new_ts) > 0).all()) \
                and new_ts[0] > epoch_ns(pd.DatetimeIndex(b["ts_utc"].iloc[-1:]))[0]
            joined = pd.concat([b, done])
            out = (joined if clean else joined.drop_duplicates("ts_utc", keep="last")).tail(cap).reset_index(drop=True)
        self._bars_clean = out          # duplicate-free by construction on every path above
        return out

    # ------------------------------------------------------------------ main step
    def _frame(self, complete: pd.DataFrame, tf: str, close_ts: pd.Timestamp) -> _Frame | None:
        """Completed bars of one decision timeframe with features and its context (the same rule as research)."""
        dec = self._resample(complete, tf)
        dec = dec[dec["visible_at"] <= close_ts].tail(self.cfg.feature_window).reset_index(drop=True)
        if len(dec) < 120:
            return None
        m = mid(dec)
        X = build_features(m, DEFAULT_FEATURE_NAMES)
        version = X.attrs["feature_version"]
        for ctf in context_tfs(tf):
            hit = self._context(complete, ctf, close_ts)
            if hit is not None:
                X = merge_higher_tf(X, hit[1], hit[0], TF_LABEL[ctf])
        X.attrs["feature_version"] = version          # as research stamps it (pipeline.build_decision_frame)
        return _Frame(dec=dec, m=m, X=X, atr=atr(m, 14))

    @staticmethod
    def _scoring_frame(fr: _Frame, model: Model) -> pd.DataFrame | None:
        """The frame `model` scores: fr.X when the model is unversioned or of fr.X's feature version; else fr.X with
        the features the model was trained on rebuilt by their registered older implementations (sessions v1 after
        the DST fix: features/legacy.py), so a champion or shadow challenger keeps scoring exactly what it was
        trained on until the normal shadow/promotion flow replaces it. None (not scored, as before) when no
        registered implementation reproduces the model's version, or when the rebuild itself fails (logged, cached for
        this frame): the model is not scored, and the bar's other work (policies, blackout closes, shadow) runs on."""
        mv = getattr(model, "feature_version", "")
        if not mv or mv == fr.X.attrs.get("feature_version"):
            return fr.X
        if mv not in fr.by_version:
            try:
                over = versions_for(DEFAULT_FEATURE_NAMES, mv)
                fr.by_version[mv] = None if over is None else with_versions(fr.X, fr.m, DEFAULT_FEATURE_NAMES, over)
            except Exception:                         # fail closed: an unscored model, never a crashed bar
                log.exception("rebuilding the frame at feature version %s failed: models of it are not scored", mv)
                fr.by_version[mv] = None
        return fr.by_version[mv]

    def _resample(self, complete: pd.DataFrame, tf: str) -> pd.DataFrame:
        """resample_bars(complete, tf), incrementally (identical output; see IncrementalResampler)."""
        rs = self._resamplers.get(tf)
        if rs is None:
            rs = self._resamplers[tf] = IncrementalResampler(tf)
        return rs(complete)

    def _context(self, complete: pd.DataFrame, tf: str, close_ts: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame] | None:
        """Completed context bars and their features, rebuilt only when a new bar of `tf` has completed: the key is
        the period close_ts falls in (UTC floor intraday, the feature-day for 1d), which changes at a bar's visible_at."""
        at = pd.DatetimeIndex([close_ts])
        key = feature_day(at)[0] if tf == "1d" else floor_tf(at, tf_seconds(tf))[0]
        cached = self._ctx_cache.get(tf)
        if cached is not None and cached[0] == key:
            return cached[1]
        hb = self._resample(complete, tf)
        hb = hb[hb["visible_at"] <= close_ts].reset_index(drop=True)
        out = None if len(hb) < 30 else (hb, build_features(mid(hb), list(CONTEXT_FEATURES)))
        self._ctx_cache[tf] = (key, out)
        return out

    def on_bar_close(self, close_ts: pd.Timestamp, last_tick: Tick) -> list[dict]:
        """Runs on every base (decision_tf) bar. Each agent decides on its own timeframe, so a 1h agent is
        evaluated only at closes that complete a 1h bar; position management stays on the base clock."""
        self._rebuild_bars()
        self._dq_bar_events, self._dq_pending = self._dq_pending, []   # this close decides on the bars it just saw
        if self._dq_bar_events and self.store is not None:
            self.store.append("dq_events", events_frame(self._dq_bar_events), source=self.cfg.account_id,
                              symbol=self.cfg.symbol, dedupe=False)
        complete = self.bars_1m[self.bars_1m["visible_at"] <= close_ts]
        if len(complete) < 400:
            return []
        base = self._frame(complete, self.cfg.decision_tf, close_ts)
        if base is None:
            return []
        self._last_atr = float(base.atr.iloc[-1]) if np.isfinite(base.atr.iloc[-1]) else self._last_atr
        try:
            self._update_ladder(complete)
        except Exception as exc:                 # noqa: BLE001 - reporting only: a bar close never fails on it
            log.warning("milestone ATR not updated: %s", type(exc).__name__)
        try:
            self._refresh_account(last_tick)
        except Exception:
            # exits first (review L3): an account read that fails must not skip this bar's position management (time
            # exits, policies); no entry is decided on this close, and the error propagates as before
            self._manage_open(base.dec)
            raise
        self._manage_open(base.dec)
        self.center.sweep_expired()
        regime = self._regime(base.X, close_ts)
        fam_w = self.allocator.weights(regime)
        decisions: list[dict] = []
        tfs = {a.timeframe for a in self.agents.values()} | {self.cfg.decision_tf}
        tfs |= {t.timeframe for t in self.open.values() if t.policy is not None and t.timeframe}
        frames: dict[str, _Frame] = {self.cfg.decision_tf: base}
        if self.shadow is not None:            # open shadow trades keep ageing after their agent leaves the set
            tfs |= {t.timeframe for b in self.shadow.books.values() for t in b.open}
        for tf in sorted(tfs, key=tf_seconds):
            if tf != "1d" and int(close_ts.timestamp()) % tf_seconds(tf):
                continue                       # cheap pre-check: this close does not complete an intraday bar of tf
            fr = base if tf == self.cfg.decision_tf else self._frame(complete, tf, close_ts)
            if fr is None or pd.Timestamp(fr.dec["visible_at"].iloc[-1]) != close_ts:
                continue
            frames[tf] = fr
            self._manage_policies(tf, fr.dec, close_ts)
            agents = [a for a in self.agents.values() if a.timeframe == tf]
            decisions += self._decide(tf, fr, agents, fam_w, close_ts, last_tick)
        self._blackout_close(complete, close_ts, frames)
        if self.shadow is not None:
            self.shadow.save(close_ts)
        self._write_state()
        self.flush_journal()
        return decisions

    def _account_currency(self) -> str:
        """The deposit currency as the broker reports it, upper case; "" while unreadable. Read once: the terminal's
        account is verified at start and on every order (accounts.verify_terminal_account), so it cannot change."""
        if not self._currency:
            try:
                self._currency = str(self.broker.account().currency or "").strip().upper()
            except Exception as exc:                  # unreadable: "" (entries refused), tried again next time
                log.warning("account currency unreadable (%s: %s)", type(exc).__name__, exc)
        return self._currency

    def _symbol_info(self, cycle: pd.Timestamp | None = None) -> SymbolInfo:
        """The terminal's symbol_info, read once per decision cycle (`cycle` = the bar close; None = read now). Raises
        when the terminal cannot answer. Remembers the contract size for the exposure figures (`_contract_size`)."""
        if cycle is not None and self._sym_cache is not None and self._sym_cache[0] == cycle:
            return self._sym_cache[1]
        info = self.broker.symbol_info(self.cfg.symbol)
        self._sym_cache = (cycle, info) if cycle is not None else self._sym_cache
        size = float(info.contract_size)
        if math.isfinite(size) and size > 0:
            self._contract = size
        return info

    def _contract_size(self) -> float:
        """Contract size for open notional (review LOW-2): the last one the terminal reported, read once if none yet;
        100 oz (the design's XAUUSD contract) only while the terminal has never answered."""
        if self._contract is None and not self._contract_tried:
            self._contract_tried = True
            try:
                self._symbol_info()
            except Exception as exc:
                log.warning("symbol_info unavailable for the contract size (%s: %s); 100 oz until it answers",
                            type(exc).__name__, exc)
        return self._contract if self._contract is not None else 100.0

    def _usd_to_account(self, side: int, price: float, distance: float, cycle: pd.Timestamp | None = None) -> float | None:
        """Account currency per USD of XAUUSD P&L from the broker's loss on one lot over `distance` (BACKLOG 28):
        loss_per_lot / (distance x contract). None when the broker has no answer (or no such method), the answer is not
        finite and positive, or the contract is unknown; the caller never substitutes a guess. Within one decision
        cycle the figure is read once per side (it is linear in the distance)."""
        fn = getattr(self.broker, "loss_per_lot", None)
        if not callable(fn) or not (math.isfinite(distance) and distance > 0):
            return None
        key = (cycle, 1 if side > 0 else -1)
        if cycle is not None and key in self._fx_cache:
            return self._fx_cache[key]
        try:
            contract = float(self._symbol_info(cycle).contract_size)
            loss = fn(self.cfg.symbol, side, price, distance)
            fx = float(loss) / (distance * contract) if loss is not None and contract > 0 else math.nan
        except Exception as exc:
            log.warning("broker conversion unavailable (%s: %s)", type(exc).__name__, exc)
            return None
        out = fx if math.isfinite(fx) and fx > 0 else None
        if cycle is not None:
            self._fx_cache = {k: v for k, v in self._fx_cache.items() if k[0] == cycle}
            self._fx_cache[key] = out
        return out

    def _sizing_terms(self, side: int, price: float, stop_distance: float,
                      cycle: pd.Timestamp | None = None) -> dict[str, Any]:
        """Intent fields read from the terminal (BACKLOG 28, ADR 0004 A3/A6): contract size, minimum lot, lot step,
        maximum lot (never above the engine's per-order ceiling, Intent's 2.0 default), stops level, the deposit currency
        and the broker's conversion of the USD stop into it. When the terminal cannot answer, or answers nonsense, the
        currency is left unknown and the conversion empty, so the RiskGate refuses the entry (fx_conversion_unavailable)
        instead of sizing on assumed terms. Exits never come here. `cycle` (the bar close) reads the terminal once per
        decision cycle; the approval re-check passes None and reads it again."""
        unknown: dict[str, Any] = {"account_currency": "", "acct_per_usd": None}
        try:
            info = self._symbol_info(cycle)
            terms = {"contract_oz": float(info.contract_size), "volume_min": float(info.volume_min),
                     "volume_step": float(info.volume_step),
                     "volume_max": min(float(info.volume_max), float(Intent.model_fields["volume_max"].default)),
                     "stops_level_points": float(info.stops_level_points)}
        except Exception as exc:
            log.warning("symbol_info unavailable (%s: %s): entries refused", type(exc).__name__, exc)
            return unknown
        positive = ("contract_oz", "volume_min", "volume_step", "volume_max")
        if not all(math.isfinite(terms[k]) and terms[k] > 0 for k in positive) or \
                not (math.isfinite(terms["stops_level_points"]) and terms["stops_level_points"] >= 0):
            log.warning("symbol_info has unusable contract terms %s: entries refused", terms)
            return unknown
        currency = self._account_currency()
        if not currency:
            return unknown
        return {**terms, "account_currency": currency,
                "acct_per_usd": self._usd_to_account(side, price, stop_distance, cycle)}

    def _card_money(self, gd: Any, intent: Intent) -> dict[str, Any]:
        """The approval card's money from an allowed GateDecision (docs/ux/approval-card.md): the stop risk in the
        account currency and its share of equity, and whether the minimum-lot exception sized it, with the normal risk
        and the cap that applied (halved in the size-down stage, as the gate applied it). Display only."""
        exc = "min_lot_exception" in gd.reasons
        size_down = self.state.stage == Stage.SIZE_DOWN or self.state.combined_size_down
        # the risk shown is rounded UP to the cent (never below the gate's); the normal risk is the gate's effective
        # target at this conviction (halved and capped in size-down), so the note never contradicts the cap
        return {"currency": intent.account_currency or None, "risk_amount": ceil_cent(gd.risk_amount),
                "risk_fraction": round(gd.risk_fraction, 5), "min_lot_exception": exc,
                "risk_normal": sizing.effective_risk(self.gate.limits, size_down, intent.multiplier).target,
                "risk_cap": self.gate.min_lot_cap(size_down) if exc else None}

    def _note_min_lot_risk(self, terms: dict[str, Any], stop_distance: float) -> None:
        """The minimum lot's stop risk in the account currency at the latest ATR stop (volume_min x stop x contract x
        fx), published as `limits.min_lot_risk_account`; None when the terms or the conversion are unknown."""
        fx = sizing.account_fx(str(terms.get("account_currency", "")), terms.get("acct_per_usd"))
        vmin, contract = terms.get("volume_min"), terms.get("contract_oz")
        if fx is None or vmin is None or contract is None or not (math.isfinite(stop_distance) and stop_distance > 0):
            self._min_lot_risk = None
            return
        self._min_lot_risk = float(vmin) * stop_distance * float(contract) * fx

    def _note_open_risk(self, positions: list[Position], tick: Tick) -> None:
        """BACKLOG 19/35: the account's open positions by side and what each can still lose down to its stop, account
        currency (`open_risk.remaining_stop_risk`: current close price to the SL x lots x contract x fx). Every position
        on the account counts, whichever agent or the owner opened it. One without SL, on another symbol, or with no
        usable conversion is unknown and makes `open_risk` None, which RiskGate refuses while the heat cap or the
        projection is on (fail closed). Read on every account refresh; exits never read it."""
        st = self.state
        st.open_long = sum(1 for p in positions if p.side > 0)
        st.open_short = sum(1 for p in positions if p.side < 0)
        rows: list[dict[str, Any]] = []
        total: float | None = 0.0
        for p in positions:
            risk: float | None = None
            if p.symbol == self.cfg.symbol:
                px = tick.bid if p.side > 0 else tick.ask
                fx = None
                if p.sl is not None and math.isfinite(p.sl) and p.side * (px - p.sl) > 0:
                    fx = self._open_risk_fx(p.side, px, p.side * (px - p.sl), tick.ts_utc)
                risk = open_risk.remaining_stop_risk(side=p.side, lots=p.lots, sl=p.sl, bid=tick.bid, ask=tick.ask,
                                                     contract=self._contract_size(), fx=fx)
            rows.append({"position_id": p.position_id, "side": p.side, "lots": p.lots, "sl": p.sl,
                         "risk": round(risk, 2) if risk is not None else None})
            total = None if total is None or risk is None else total + risk
        st.open_risk, self._open_risk_rows = total, rows

    def _open_risk_fx(self, side: int, price: float, distance: float, now: pd.Timestamp) -> float | None:
        """Account currency per USD for open-risk figures: the broker's conversion (`_usd_to_account`, checked by
        `sizing.account_fx`, so a USD account is 1 and an unknown currency is None), read at most once a minute per side
        so the per-tick refresh does not call the terminal every tick. A failed read is retried the next minute."""
        minute = now.floor("min")
        hit = self._open_fx.get(side)
        if hit is not None and hit[0] == minute:
            return hit[1]
        fx = sizing.account_fx(self._account_currency(), self._usd_to_account(side, price, distance))
        self._open_fx[side] = (minute, fx)
        return fx

    def _published_limits(self) -> dict[str, Any]:
        """`limits` in engine_<account>.json, the RiskGate limits the dashboard's account view reads (loss budget,
        ladder): risk per trade and its maximum, multiplier bounds, daily and weekly caps; the account's absolute daily
        loss limit (accounts.yaml through ops.run.engine_limits, account currency; null when off), the minimum-lot
        exception cap (null when off, always null in tiny-live) and the minimum lot's stop risk in the account currency
        at the latest ATR stop (null when unknown)."""
        lim = self.gate.limits
        mlr = self._min_lot_risk
        return {"risk_per_trade": lim.risk_per_trade, "max_risk_per_trade": lim.max_risk_per_trade,
                "multiplier_bounds": list(lim.multiplier_bounds), "daily_cap": lim.daily_cap,
                "weekly_cap": lim.weekly_cap, "daily_loss_limit_abs": lim.daily_loss_limit_abs,
                "min_lot_risk_cap": lim.min_lot_risk_cap,
                # BACKLOG 19/35: the open-risk heat cap and whether the daily projection counts open risk (both off by default)
                "heat": lim.heat.model_dump(), "project_open_risk": lim.project_open_risk,
                "min_lot_risk_account": round(mlr, 2) if mlr is not None and math.isfinite(mlr) else None}

    def _decide(self, tf: str, fr: _Frame, agents: list[Specialist], fam_w: dict[str, float], close_ts: pd.Timestamp,
                last_tick: Tick) -> list[dict]:
        dec, m, X, a = fr.dec, fr.m, fr.X, fr.atr
        decisions = []
        last = len(dec) - 1
        cost_atr = self._cost_atr(float(a.iloc[last]), close_ts)            # full round trip: the RiskGate's checks
        hurdle_atr = self._cost_atr(float(a.iloc[last]), close_ts, ex_spread=True)   # p already pays the spread
        cands_by_agent = {agent.agent_id: agent.candidates(m, X) for agent in agents}
        if self.shadow is not None:
            self._shadow_step(tf, fr, agents, cands_by_agent, float(a.iloc[last]), hurdle_atr)
        for agent in agents:
            cands = cands_by_agent[agent.agent_id]
            if cands.empty or int(cands["idx"].iloc[-1]) != last:
                continue
            side = int(cands["side"].iloc[-1])
            model = self.models.get(agent.agent_id) or self.models.get(agent.family)
            if model is None:
                continue
            share = 1.0 if self.live_shares is None else self.live_shares.get(agent.agent_id, 0.0)
            if share <= 0:
                continue      # shadow-only member of the population: the shadow book trades it, not the broker
            if self._busy(agent.agent_id):
                continue      # one position (or pending proposal) per agent, as its labels were built
            drift = self._drift()
            if agent.agent_id in (drift.get("halted") or {}):
                decisions.append(self._record(agent, close_ts, 0.0, 0.0, "drift_halt"))   # CUSUM: entries stop
                continue
            Xm = self._scoring_frame(fr, model)
            if Xm is None:
                # design: a model only ever scores the feature frame it was trained on
                d = self._record(agent, close_ts, 0.0, 0.0, "feature_version_mismatch")
                d.update(p=None, model_feature_version=getattr(model, "feature_version", ""),
                         frame_feature_version=X.attrs.get("feature_version"))     # not scored
                decisions.append(d)
                continue
            sfv = str(Xm.attrs.get("feature_version", ""))
            feats = Xm.drop(columns=["ts_utc"]).iloc[[last]].replace([np.inf, -np.inf], np.nan)
            feats["side"] = side          # side-aligned models (features include `side`) align signed inputs themselves
            cols = model.feature_names or [c for c in feats.columns]
            p = float(model.predict(feats[[c for c in cols if c in feats.columns]] if model.feature_names else feats)[0])
            ls = agent.label_spec
            w = fam_w.get(agent.family, 0.0) * share * float((drift.get("size_factor") or {}).get(agent.agent_id, 1.0))
            mult = float(size_multiplier(np.array([p]), w, ls.target_atr, ls.stop_atr, hurdle_atr)[0])
            price = last_tick.ask if side > 0 else last_tick.bid
            if mult <= 0 or p <= breakeven_prob(ls.target_atr, ls.stop_atr, hurdle_atr) + 0.02:
                decisions.append(self._record(agent, close_ts, p, mult, "below_threshold", scored_fv=sfv))
                continue
            # the terminal's contract terms and the USD -> account currency conversion (BACKLOG 28)
            terms = self._sizing_terms(side, price, ls.stop_atr * float(a.iloc[last]), cycle=close_ts)
            self._note_min_lot_risk(terms, ls.stop_atr * float(a.iloc[last]))
            intent = Intent(agent_id=agent.agent_id, side=side, p=p, target_atr=ls.target_atr, stop_atr=ls.stop_atr, atr_usd=float(a.iloc[last]), cost_atr=cost_atr,
                            multiplier=mult, price=price, timeframe=tf, family=agent.family, **terms)
            gd = self.gate.check(intent, self.state, close_ts, margin_required=broker_margin(self.broker, self.cfg.symbol),
                                 throttle=self._agent_throttle(agent.agent_id, tf, close_ts))
            if not gd.allowed:
                decisions.append(self._record(agent, close_ts, p, mult, "gate:" + ",".join(gd.reasons), scored_fv=sfv))
                continue
            pid = self._proposal_id(agent.agent_id, close_ts)
            if pid in self.sent_ids:
                continue
            stop = price - side * gd.stop_distance
            target = price + side * ls.target_atr * float(a.iloc[last])
            prop = Proposal(proposal_id=pid, account_id=self.cfg.account_id, agent_id=agent.agent_id, side=side, lots=gd.lots, entry=price, stop=stop, target=target, p=p,
                            ev_r=p * ls.target_atr - (1 - p) * ls.stop_atr - cost_atr, spread_points=self.state.spread_points,
                            top_features=self._top_features(model, feats), window_s=self.cfg.approval_window_s,
                            risk_usd=round(gd.lots * gd.stop_distance * intent.contract_oz, 2),
                            **self._card_money(gd, intent))
            if self.cfg.approval_mode == "auto":
                if self._auto_paused is not None and self._auto_paused[0] == close_ts:
                    decisions.append(self._record(agent, close_ts, p, mult, f"auto_paused:{self._auto_paused[1]}",
                                                  scored_fv=sfv))
                    continue
                try:
                    res = self._execute(prop, agent, gd.lots, stop, target, requested=price, atr_usd=intent.atr_usd)
                except Exception as exc:
                    # review LOW-C: the order may or may not be at the broker. Its pending_orders row stays "sending"
                    # (the restart reconciliation resolves it), entries are blocked (tick_error), no further auto
                    # entry this bar close; the remaining agents are still journalled and exits carry on
                    self._auto_paused = (close_ts, "order_raised")
                    self.note_tick_error(exc)
                    decisions.append(self._record(agent, close_ts, p, mult, f"auto_unconfirmed:{type(exc).__name__}",
                                                  pid, scored_fv=sfv))
                    continue
                decisions.append(self._record(agent, close_ts, p, mult, self._auto_action(res), pid, scored_fv=sfv))
                self._after_auto_order(res, gd, side, close_ts)
            else:
                self.pending[pid] = (intent, prop, agent)   # until the owner decides or the window expires
                self.center.propose(prop)
                decisions.append(self._record(agent, close_ts, p, mult, "proposed", pid, scored_fv=sfv))
            if gd.reasons:      # an allowed decision's notes (BACKLOG 25 min_lot_exception): the journal shows the risk
                decisions[-1].update(sizing=list(gd.reasons), risk_fraction=round(gd.risk_fraction, 5),
                                     risk_per_trade=self.gate.limits.risk_per_trade)
        return decisions

    # ------------------------------------------------------------------ approvals -> orders
    def _on_decision(self, p: Proposal) -> None:
        entry = self.pending.pop(p.proposal_id, None)
        if entry is None:
            return
        intent, prop, agent = entry
        if p.outcome == Outcome.APPROVED:
            tick = self.broker.last_tick(self.cfg.symbol)
            # the quote the order would be priced from must pass the tick price rules (review MEDIUM-3); its time is
            # not compared here, the approval reads the terminal's latest quote
            fault = self._quote_fault(tick, order=False)
            if fault is not None:
                gd, lots = GateDecision(allowed=False, reasons=[f"bad_quote:{fault[0]}"]), 0.0
            else:
                self._refresh_account(tick)
                # contract terms and the conversion read again from the terminal (review LOW-5): one it can no longer
                # give leaves the currency unknown, so the re-check refuses (fail closed); never the proposal-time figure
                now_px = tick.ask if prop.side > 0 else tick.bid
                intent = intent.model_copy(update={**self._sizing_terms(intent.side, now_px,
                                                                        intent.stop_atr * intent.atr_usd),
                                                   "price": now_px})
                gd = self.gate.check(intent, self.state, tick.ts_utc,  # re-check at approval time
                                     margin_required=broker_margin(self.broker, self.cfg.symbol),
                                     throttle=self._agent_throttle(agent.agent_id, intent.timeframe or agent.timeframe,
                                                                   tick.ts_utc))
                lots = gd.lots
                if gd.allowed:
                    gd, lots = self._within_card(gd, prop, agent)
            if gd.allowed:
                price = tick.ask if prop.side > 0 else tick.bid
                self._execute(prop, agent, lots, price - prop.side * gd.stop_distance,
                              price + prop.side * agent.label_spec.target_atr * intent.atr_usd, requested=price,
                              atr_usd=intent.atr_usd)
            else:
                self.decisions.append({"ts": time.time(), "agent": agent.agent_id, "action": "gate_at_approval:" + ",".join(gd.reasons)})
                p.gate_refusal = list(gd.reasons)
                if self.center.bus is not None:
                    self.center.bus.archive(p)          # the dashboard shows "refused at approval", not "approved"
        self._write_state()

    def _within_card(self, gd: GateDecision, prop: Proposal, agent: Specialist) -> tuple[GateDecision, float]:
        """ADR 0009: the order never exceeds the card the owner approved. The re-check's lots are capped at the card's
        (equity may have risen during the window); the risk at the lots sent may exceed the card's by at most
        APPROVED_RISK_TOLERANCE (the broker's conversion moved), else the approval is refused `risk_above_approved`.
        Only ever reduces: a smaller re-check size is sent as the gate decided it."""
        lots = min(gd.lots, prop.lots)
        if lots < gd.lots:
            self.decisions.append({"ts": time.time(), "agent": agent.agent_id, "action": "approval_size_capped",
                                   "proposal": prop.proposal_id, "gate_lots": gd.lots, "lots": lots})
        risk = gd.risk_amount * lots / gd.lots if gd.lots > 0 else gd.risk_amount
        if prop.risk_amount is not None and risk > prop.risk_amount * APPROVED_RISK_TOLERANCE + 1e-9:
            return gd.model_copy(update={"allowed": False, "reasons": ["risk_above_approved"]}), lots
        return gd, lots

    @staticmethod
    def _auto_action(res: OrderResult | None) -> str:
        """The journal's word for an auto entry (review LOW-B): `executed:auto` only for a fill (or a duplicate, sent
        before); `auto_rejected:<retcode>` for a definite reject (nothing placed); `auto_unconfirmed:<retcode>` for
        anything else (`_after_auto_order` then stops auto entries for the rest of the bar close)."""
        if res is None or (res.ok and res.position_id is not None):
            return "executed:auto"
        kind = "auto_rejected" if res.retcode in DEFINITE_REJECT_RETCODES else "auto_unconfirmed"
        return f"{kind}:{res.retcode}"

    def _after_auto_order(self, res: OrderResult | None, gd: GateDecision, side: int, close_ts: pd.Timestamp) -> None:
        """Trading-safety review HIGH (BACKLOG 19 review): every auto entry at one bar close must see the previous
        one, or `max_positions`, the combined cap, the heat cap and the same-direction cap can each be passed within a
        bar close. After an order the account is read again (`_refresh_account` on the broker's last tick). A fill the
        positions read does not show yet is added conservatively: one more position on its side, its lots and notional,
        the gate's margin, and its stop risk scaled to the filled lots (unknown open risk stays unknown). An ambiguous
        result (neither a fill nor a definite reject, `DEFINITE_REJECT_RETCODES`) or a failed re-read stops auto entries
        for the rest of this bar close. `res` None: nothing was sent (a duplicate), nothing changes."""
        if res is None:
            return
        filled = res.ok and res.position_id is not None
        if not filled and res.retcode in DEFINITE_REJECT_RETCODES:
            return                                      # nothing placed: the facts are unchanged
        if not filled:
            self._auto_paused = (close_ts, "unconfirmed_order")
            log.warning("auto entry result unconfirmed (retcode %s): auto entries stop for this bar close", res.retcode)
        try:
            self._refresh_account(self.broker.last_tick(self.cfg.symbol))
            seen = self._position_ids is not None and res.position_id in self._position_ids
        except Exception as exc:                        # fail closed: no further auto entry on unknown facts
            log.warning("account re-read after an auto entry failed (%s): auto entries stop for this bar close",
                        type(exc).__name__)
            self._auto_paused = self._auto_paused or (close_ts, "account_unread")
            seen = False
        if not filled or seen:
            return
        st, lots = self.state, float(res.filled_lots)
        st.open_positions += 1
        st.open_lots = round(st.open_lots + lots, 6)
        px = self._price or res.price           # USD notional at the mid, as `_refresh_account` publishes it
        if px:
            st.open_notional = st.open_lots * self._contract_size() * px
        if side > 0:
            st.open_long += 1
        else:
            st.open_short += 1
        st.margin_used += gd.margin_needed
        if st.open_risk is not None:
            st.open_risk += gd.risk_amount * lots / gd.lots if gd.lots > 0 else gd.risk_amount

    def _execute(self, prop: Proposal, agent: Specialist, lots: float, stop: float, target: float, *, requested: float,
                 atr_usd: float | None = None) -> OrderResult | None:
        """Sends the order (pending_orders row first) and returns the broker's result; None when the proposal was sent
        before and nothing is sent now."""
        if prop.proposal_id in self.sent_ids:       # sent before (this process or before a restart): never again
            self.decisions.append({"ts": time.time(), "agent": agent.agent_id, "action": "duplicate_suppressed",
                                   "proposal": prop.proposal_id})
            return None
        magic = self._magic(agent.family)
        # finance audit GAP-01/02/13: the signal's quote and spread (the proposal) next to the quote priced from now
        audit = EntryAudit(decision_price=prop.entry, decision_spread_points=prop.spread_points,
                           decision_utc=pd.Timestamp(prop.created, unit="s", tz="UTC"), requested_price=requested,
                           timeframe=agent.timeframe, family=agent.family,
                           model_version=self.live_versions.get(agent.agent_id))
        oi = OrderIntent(client_order_id=prop.proposal_id, symbol=self.cfg.symbol, side=prop.side, lots=lots, sl=round(stop, 2), tp=round(target, 2), magic=magic,
                         comment=prop.proposal_id[-31:])
        # pending_orders row persisted BEFORE sending: a crash between send and result is reconciled on restart
        self._orders_record(prop.proposal_id, agent_id=agent.agent_id, side=prop.side, magic=magic, lots=lots,
                            max_bars=self._base_bars(agent), sl=oi.sl, tp=oi.tp, ts=pd.Timestamp.now("UTC"),
                            atr_usd=atr_usd, audit=audit)
        res = self.broker.place_order(oi)
        rec = self._orders[prop.proposal_id]
        rec.status, rec.position_id = ("filled", res.position_id) if res.ok and res.position_id is not None else ("rejected", None)
        if res.ok and res.position_id is not None:
            fill_px = float(res.price if res.price is not None else requested)
            opened = self.broker.last_tick(self.cfg.symbol).ts_utc
            tr = OpenTrade(position_id=res.position_id, agent_id=agent.agent_id, side=prop.side, lots=res.filled_lots,
                           entry_bar_ts=pd.Timestamp.now('UTC'), max_bars=self._base_bars(agent), sl=oi.sl, tp=oi.tp,
                           client_order_id=prop.proposal_id, open_price=fill_px, initial_sl=oi.sl,
                           initial_lots=res.filled_lots, opened_utc=opened,
                           equity_before=self.state.equity if self.state.equity > 0 else None, audit=audit)
            self._attach_policy(tr, agent, entry=fill_px, atr_usd=atr_usd, opened=opened)
            self.open[res.position_id] = tr
        self._save_orders()
        self.decisions.append({"ts": time.time(), "agent": agent.agent_id, "action": "order",
                               "proposal": prop.proposal_id, "ok": res.ok,
                               "retcode": res.retcode, "price": res.price, "lots": res.filled_lots})
        if self.store is not None and res.ok and res.price is not None:
            # requested vs filled feeds the nightly slippage table
            fill = pd.DataFrame([{"ts_utc": self.broker.last_tick(self.cfg.symbol).ts_utc, "client_order_id": prop.proposal_id,
                                  "agent_id": agent.agent_id, "side": prop.side, "lots": res.filled_lots, "requested": requested,
                                  "filled": res.price, "order_type": "market", "retcode": res.retcode, "commission": np.nan}])
            self.store.append("fills", fill, source=self.cfg.account_id, symbol=self.cfg.symbol, dedupe=False)
        return res

    @staticmethod
    def _attach_policy(tr: OpenTrade, agent: Specialist, *, entry: float, atr_usd: float | None,
                       opened: pd.Timestamp) -> None:
        """Give `tr` its agent's exit policy (distances in the signal bar's ATR, measured from the fill `entry`; the
        hard-flat deadline from the fill time). Without a usable ATR the trade keeps only its server SL/TP."""
        policy = agent.exit_spec
        if policy is None or not policy.active or atr_usd is None or not np.isfinite(atr_usd) or atr_usd <= 0:
            return
        tr.policy, tr.timeframe, tr.atr_usd, tr.entry = policy, agent.timeframe, float(atr_usd), float(entry)
        tr.opened_utc, tr.flat_at = opened, policy.flat_deadline(opened)

    @staticmethod
    def _implied_atr(agent: Specialist, entry: float, sl: float | None) -> float | None:
        """The ATR implied by an initial stop placed stop_atr x ATR from entry (recovery when no ATR was recorded).
        A stop already tightened gives a smaller ATR, so the restored policy is tighter, never looser; a stop at
        entry implies nothing."""
        if sl is None or agent.label_spec.stop_atr <= 0:
            return None
        d = abs(entry - sl) / agent.label_spec.stop_atr
        return d if d > 0 else None

    # ------------------------------------------------------------------ position management
    # ------------------------------------------------------------------ broker reads (fail closed)
    def _read_positions(self, then: str) -> list[Position] | None:
        """The broker's positions, or None when the read fails (MT5 positions_get returning None raises in the
        adapter): the failure is counted (`positions_unreadable`), and the caller manages nothing from it."""
        try:
            positions = self.broker.positions()
        except Exception:
            self._read_failed("positions", then)
            return None
        self._read_fails["positions"] = 0
        return positions

    def _read_deals(self, since: pd.Timestamp) -> pd.DataFrame:
        """The broker's deal history; a failed read (MT5 history_deals_get returning None) is counted and raised."""
        try:
            deals = self.broker.deals_since(since)
        except Exception:
            self._read_failed("deals", "the close is confirmed on a later pass")
            raise
        self._read_fails["deals"] = 0
        return deals

    def _read_failed(self, kind: str, then: str) -> None:
        """Count a failed read, at most once per tick (several reads in one tick are one failure in a row)."""
        at = self._last_tick.ts_utc if self._last_tick is not None else None
        if at is None or self._read_fail_at.get(kind) != at or self._read_fails[kind] == 0:
            self._read_fails[kind] += 1
            self._read_fail_at[kind] = at
            if self._read_fails[kind] == 1:
                self.decisions.append({"ts": time.time(), "action": "book_unreadable", "read": kind})
        log.exception("%s read failed (%d in a row); %s", kind, self._read_fails[kind], then)

    def _book_unreadable(self) -> int:
        """Consecutive failed broker reads (the larger of positions() and deal history)."""
        return max(self._read_fails.values())

    def _manage_open(self, dec: pd.DataFrame) -> None:
        positions = self._read_positions("position management skipped this bar")
        if positions is None:                             # the terminal failed: manage at the next bar close
            return
        live = {p.position_id for p in positions}
        self._sweep_closed(live)                          # closed by stop/target at the broker
        for pid in list(self.open):
            if pid not in live:
                continue                                  # waiting for its exit deal (_sweep_closed)
            tr = self.open[pid]
            tr.bars_held += 1
            if tr.bars_held >= tr.max_bars and pid not in self._close_due:
                self._try_close(pid, "time_exit", None)   # forgotten only once the close succeeds
        self._reconcile(self.last_bar_close)

    def _manage_policies(self, tf: str, dec: pd.DataFrame, close_ts: pd.Timestamp) -> None:
        """At the close of a bar of `tf`, run the exit policy of each open trade on that timeframe with the mechanics
        of its labels (labels.exit_policy): hard flat at the first close at or after the deadline; the trail from the
        most favourable exit-side price of the bars closed since the fill, armed `trail_after_atr` beyond entry, sent
        with `modify` and effective from the next bar. A policy only tightens the stop (the server stop stays the
        floor and reconciliation reinstates the tighter one if it is lost) or closes; if the price is already through
        the new stop the trade is closed at market. Automatic: never waits for approval."""
        mine = [(pid, tr) for pid, tr in self.open.items() if tr.policy is not None and tr.timeframe == tf]
        if not mine:
            return
        positions = self._read_positions("exit policies run at the next bar close; the server stops stay in place")
        if positions is None:
            return
        live = {p.position_id: p for p in positions}
        for pid, tr in mine:
            pos = live.get(pid)
            policy = tr.policy
            if pos is None or policy is None or pid in self._close_due:
                continue                                  # closed at the broker, or a close already being retried
            if tr.flat_at is not None and close_ts >= tr.flat_at:
                self._policy_close(pid, tr, "hard_flat")
                continue
            if policy.trail_atr is None or tr.entry is None or tr.atr_usd is None or tr.opened_utc is None:
                continue
            after = dec[pd.to_datetime(dec["visible_at"], utc=True) > tr.opened_utc]
            if after.empty:
                continue
            fav = float(after["bid_high"].max()) if tr.side > 0 else float(after["ask_low"].min())
            best = fav if tr.side * (fav - tr.entry) > 0 else tr.entry
            trail = policy.trail_stop(tr.side, tr.entry, tr.atr_usd, best)
            sl = tr.sl
            if trail is not None and (sl is None or tr.side * (round(trail, 2) - sl) > 1e-9):
                sl = round(trail, 2)                      # never loosens
            if sl is None or trail is None:
                continue
            tick = self.broker.last_tick(self.cfg.symbol)
            broker_looser = pos.sl is None or tr.side * (sl - pos.sl) > 1e-9
            if broker_looser and tr.side * ((tick.bid if tr.side > 0 else tick.ask) - sl) <= 0:
                # the price is through the stop the policy set but the broker's stop is not there (a new level, or a
                # modify that did not stick): close at market, as the labels exit at that stop
                self._policy_close(pid, tr, "trail_close")
                continue
            if pos.sl is not None and tr.side * (sl - pos.sl) <= 1e-9:
                # review MEDIUM-2 (c), defence in depth: the broker's stop is already at or beyond this one (a restart
                # or an adoption left the engine's copy loose): never send a looser stop; the broker's is the floor
                if tr.sl is None or tr.side * (pos.sl - tr.sl) > 1e-9:
                    tr.sl = pos.sl
                continue
            if sl == tr.sl:
                continue
            tr.sl = sl                                    # reconciliation re-sends it if the modify did not stick
            res = self.broker.modify(pid, sl, pos.tp)
            self.decisions.append({"ts": time.time(), "agent": tr.agent_id, "action": "trail", "position": pid,
                                   "sl": sl, "ok": res.ok, "retcode": res.retcode})
        self._save_orders()

    def _policy_close(self, pid: int, tr: OpenTrade, action: str, **extra: object) -> OrderResult:
        return self._try_close(pid, action, None, **extra)

    def _try_close(self, pid: int, action: str, now: pd.Timestamp | None, **extra: object) -> OrderResult:
        """Close one position at market; never raises. The trade leaves the open table (and is queued for the
        closed-trade record) only when the broker confirms the close. A failure keeps the close due (`_retry_closes`,
        every tick): refused with a market-closed retcode (MARKET_CLOSED_RETCODES) it is backed off reconcile_every_s
        of tick time, doubling per consecutive refusal up to modify_backoff_max_s; any other refusal (requote, price
        changed) or a raising call is retried on the first tick CLOSE_RETRY_MIN_S later (`_retry_wait`). Automatic:
        never gated."""
        now = now if now is not None else self._last_tick.ts_utc if self._last_tick is not None else pd.Timestamp.now("UTC")
        tr = self.open.get(pid)
        err: str | None = None
        quote = self._last_tick                           # the quote the close was decided on (exit fill log)
        try:
            res = self.broker.close(pid)
        except Exception as e:                            # one position never stops the others
            log.exception("%s failed for position %s; retried next tick", action, pid)
            err = repr(e)
            res = OrderResult(ok=False, retcode=-1, order_id=None, position_id=pid, filled_lots=0.0, price=None,
                              message=err)
        row: dict = {"ts": time.time(), "agent": tr.agent_id if tr is not None else None, "action": action,
                     "position": pid, "ok": res.ok, "retcode": res.retcode, "price": res.price, **extra}
        if err is not None:
            row["error"] = err
        self.decisions.append(row)
        if res.ok:
            self._close_due.pop(pid, None)
            self._close_backoff.pop(pid, None)
            if tr is not None:
                self.open.pop(pid, None)
                self._note_closed(pid, tr, action, res)
                self._log_exit_fill(pid, tr, action, res.filled_lots or tr.lots, res.price, at=now, quote=quote,
                                    retcode=res.retcode)
            return res
        self._close_due[pid] = (action, dict(extra), now)
        if res.retcode in MARKET_CLOSED_RETCODES:
            fails = self._close_backoff.get(pid, (0, now))[0]
            wait = min(self.cfg.reconcile_every_s * 2 ** fails, self.cfg.modify_backoff_max_s)
            self._close_backoff[pid] = (fails + 1, now + pd.Timedelta(seconds=wait))
        else:
            self._close_backoff.pop(pid, None)            # transient: again CLOSE_RETRY_MIN_S later (`_retry_wait`)
        return res

    def _log_exit_fill(self, pid: int, tr: OpenTrade, kind: str, lots: float, filled: float | None, *,
                       at: pd.Timestamp, quote: Tick | None = None, level: float | None = None,
                       retcode: int | None = None) -> None:
        """Finance audit GAP-04: one exit leg in store `exit_fills`, requested vs filled as `fills` keeps entries. The
        requested price is the exit side of the quote the engine decided on (bid for a long) for its own closes and
        the scale-out, and the stop or target level for a broker stop or target fill; slippage is side x (requested -
        filled). Kept apart from `fills`, which the entry slippage table reads. Never raises: an exit never waits on
        its log, and a leg without a filled price is not logged."""
        if self.store is None or filled is None:
            return
        try:
            requested = level if level is not None else \
                (quote.bid if tr.side > 0 else quote.ask) if quote is not None else np.nan
            row = pd.DataFrame([{"ts_utc": at, "position_id": pid, "client_order_id": tr.client_order_id,
                                 "agent_id": tr.agent_id, "side": tr.side, "lots": float(lots), "kind": kind,
                                 "requested": float(requested), "filled": float(filled),
                                 "spread": quote.ask - quote.bid if quote is not None else np.nan,
                                 "retcode": retcode}])
            row = row.astype({"client_order_id": "string", "agent_id": "string", "kind": "string",
                              "retcode": "Int64"})              # one schema even when a value is missing
            self.store.append("exit_fills", row, source=self.cfg.account_id, symbol=self.cfg.symbol, dedupe=False)
        except Exception:
            log.exception("exit fill of position %s (%s) not logged", pid, kind)

    def _backing_off(self, pid: int, now: pd.Timestamp) -> bool:
        return pid in self._close_backoff and now < self._close_backoff[pid][1]

    def _retry_wait(self, pid: int, now: pd.Timestamp) -> bool:
        """True while a due close must not be re-sent yet: backing off after a market-closed refusal, or tried less
        than CLOSE_RETRY_MIN_S ago (re-verification LOW-A: a requote is not re-sent several times a second). An
        attempt stamped after `now` (the host and tick clocks disagree) never delays the retry."""
        if self._backing_off(pid, now):
            return True
        due = self._close_due.get(pid)
        last = due[2] if due is not None else None
        return last is not None and 0 <= (now - last).total_seconds() < CLOSE_RETRY_MIN_S

    def _retry_closes(self, now: pd.Timestamp) -> None:
        """Every tick: re-send each close the engine decided that has not succeeded (kill switch, weekend loser, time
        exit, hard flat, trail, blackout, engine stop), unless it is backing off after a market-closed refusal or was
        tried less than CLOSE_RETRY_MIN_S ago (`_retry_wait`). A position the broker no longer lists is left to `_sweep_closed`, which records it
        once its exit deal is seen. An unreadable book skips this tick (the next one retries)."""
        due = [pid for pid in self._close_due if not self._retry_wait(pid, now)]
        if not due:
            return
        positions = self._read_positions("closes are retried next tick")
        if positions is None:
            return
        live = {p.position_id for p in positions}
        for pid in due:
            if pid not in live:
                self._close_due.pop(pid, None)
                self._close_backoff.pop(pid, None)
                continue
            action, extra, _ = self._close_due[pid]
            self._try_close(pid, action, now, **extra)
        self._save_orders()

    def _stop_check(self, t: Tick) -> None:
        """Every tick: a trade whose exit-side price is at or through the stop this engine set (`tr.sl`) while the
        broker's stop is looser or missing (a trail or weekend `modify` that was rejected, a stop lost at the broker)
        is closed at market, so the engine's stop and the broker's cannot diverge for the rest of a bar. Where the
        broker's stop is in place the server executes it. Automatic: never gated; a failing close is retried every
        tick (`_try_close`), backed off only when the broker says the market is closed (the broker's own stop stays in
        place meanwhile). A positions() read that fails skips this tick; the next tick retries."""
        now = t.ts_utc
        through = [(pid, tr) for pid, tr in self.open.items()
                   if tr.sl is not None and tr.side * ((t.bid if tr.side > 0 else t.ask) - tr.sl) <= 0
                   and not self._retry_wait(pid, now)]
        if not through:
            return
        positions = self._read_positions("the engine stop check is retried next tick")
        if positions is None:
            return
        live = {p.position_id: p for p in positions}
        for pid, tr in through:
            pos = live.get(pid)
            if pos is None or (pos.sl is not None and tr.sl is not None and tr.side * (tr.sl - pos.sl) <= 1e-9):
                continue                                  # gone, or the broker's stop is at least as tight
            self._try_close(pid, "engine_stop_close", now, sl=tr.sl, broker_sl=pos.sl)
        self._close_backoff = {k: v for k, v in self._close_backoff.items() if k in self.open or k in self._close_due}
        self._save_orders()

    def _scale_out(self, t: Tick) -> None:
        """Scale-out on the tick that reaches the level (the labels fill it at the level): close `scale_fraction` of
        the position, rounded down to the volume step; when that would leave less than the minimum volume on either
        side the scale-out is skipped (size is never increased) and the trail still runs. Taken once; never gated.
        A failure (symbol info or the partial close raising or refused) keeps the position unscaled and is retried on
        a later tick at the level, `scale_out_retries` attempts in all; one position never stops the others."""
        due = []
        for pid, tr in self.open.items():
            policy = tr.policy
            if policy is None or tr.scaled or tr.entry is None or tr.atr_usd is None:
                continue
            level = policy.scale_level(tr.side, tr.entry, tr.atr_usd)
            if level is not None and tr.side * ((t.bid if tr.side > 0 else t.ask) - level) >= 0:
                due.append((pid, tr, policy))
        if not due:
            return
        positions = self._read_positions("the scale-out is retried next tick")
        if positions is None:
            return
        live = {p.position_id for p in positions if p.position_id not in self._close_due}
        info: SymbolInfo | None = None
        for pid, tr, policy in due:
            if pid not in live:
                continue
            try:
                info = info or self.broker.symbol_info(self.cfg.symbol)
                if info is None:
                    raise RuntimeError("no symbol info")
                step = info.volume_step or 0.01
                lots = round(float(np.floor(tr.lots * policy.scale_fraction / step + 1e-9)) * step, 8)
                if lots < info.volume_min - 1e-9 or tr.lots - lots < info.volume_min - 1e-9:
                    tr.scaled = True
                    self.decisions.append({"ts": time.time(), "agent": tr.agent_id, "action": "scale_out_skipped",
                                           "position": pid, "lots": tr.lots})
                    continue
                res = self.broker.close(pid, lots)
                if not res.ok:
                    raise RuntimeError(f"partial close refused: retcode {res.retcode} {res.message}")
                done = res.filled_lots or lots
                px = res.price if res.price is not None else (t.bid if tr.side > 0 else t.ask)
                tr.scaled_price = px if not tr.scaled_lots else \
                    ((tr.scaled_price or px) * tr.scaled_lots + px * done) / (tr.scaled_lots + done)
                tr.scaled_lots = round(tr.scaled_lots + done, 8)
                tr.lots, tr.scaled = round(tr.lots - done, 8), True
                self._log_exit_fill(pid, tr, "scale_out", done, res.price, at=t.ts_utc, quote=t, retcode=res.retcode)
                self.decisions.append({"ts": time.time(), "agent": tr.agent_id, "action": "scale_out", "position": pid,
                                       "lots": lots, "ok": res.ok, "retcode": res.retcode, "price": res.price})
            except Exception as e:                        # one position never stops the loop
                tr.scale_tries += 1
                tr.scaled = tr.scale_tries >= self.cfg.scale_out_retries      # out of retries: the trail still runs
                log.warning("scale-out failed for position %s (attempt %d): %r", pid, tr.scale_tries, e)
                self.decisions.append({"ts": time.time(), "agent": tr.agent_id, "action": "scale_out_failed",
                                       "position": pid, "tries": tr.scale_tries, "gave_up": tr.scaled,
                                       "error": repr(e)})
        self._save_orders()

    def _blackout_close(self, complete: pd.DataFrame, close_ts: pd.Timestamp, frames: dict[str, _Frame]) -> None:
        """Design (News blackout): early close if p < 0.5. In a tier-1 calendar blackout (not an unscheduled news
        shock) each open trade of a known agent is re-scored by its model on the agent's latest completed bar with the
        trade's side, at every base bar close; p < 0.5 closes it at market. Never gated by approval."""
        ev = self._blackout_event
        if not self.cfg.news_blackout or ev is None or ev.get("kind") == "news_shock" or not self.open:
            return
        for pid, tr in list(self.open.items()):
            try:
                self._blackout_rescore(pid, tr, complete, close_ts, frames, ev)
            except Exception as e:                        # one position never stops the loop
                # the trade keeps its server stop and target and is re-scored again at the next base bar close
                log.exception("blackout re-score failed for position %s; the position is kept", pid)
                self.decisions.append({"ts": time.time(), "agent": tr.agent_id, "action": "blackout_rescore_failed",
                                       "position": pid, "error": repr(e)})
        self._save_orders()

    def _blackout_rescore(self, pid: int, tr: OpenTrade, complete: pd.DataFrame, close_ts: pd.Timestamp,
                          frames: dict[str, _Frame], ev: dict) -> None:
        agent = self.agents.get(tr.agent_id)
        model = None if agent is None else (self.models.get(agent.agent_id) or self.models.get(agent.family))
        if agent is None or model is None:
            return
        fr = frames.get(agent.timeframe)
        if fr is None:
            fr = self._frame(complete, agent.timeframe, close_ts)
            if fr is None:
                return
            frames[agent.timeframe] = fr
        Xm = self._scoring_frame(fr, model)
        if Xm is None:
            return                                        # a model only scores the frame version it was trained on
        feats = Xm.drop(columns=["ts_utc"]).iloc[[len(Xm) - 1]].replace([np.inf, -np.inf], np.nan)
        feats["side"] = tr.side
        cols = [c for c in model.feature_names if c in feats.columns] if model.feature_names else list(feats.columns)
        p = float(model.predict(feats[cols])[0])
        if p < 0.5:
            self._policy_close(pid, tr, "blackout_close", p=round(p, 4), event=ev.get("title"))

    def _own(self, magic: int) -> bool:
        return self.cfg.magic_base <= magic < self.cfg.magic_base + 100

    def _reconcile(self, now: pd.Timestamp | None) -> None:
        """Broker positions are the source of truth (design: Reconciliation), on start, every bar close and every
        `reconcile_every_s` of tick time: an orphan in this engine's magic range is adopted and, without a stop, given
        one `orphan_stop_atr` x ATR from entry; a stop or target missing at the broker, or a stop looser than the one
        this engine set, is reinstated. An orphan whose magic maps to exactly one known agent is adopted as that
        agent's trade with its exit policy. A rejected reinstatement is re-sent after reconcile_every_s, doubling per
        consecutive reject up to modify_backoff_max_s (the per-tick stop check covers the gap). Positions with unknown
        magic numbers are listed and never touched."""
        if now is not None:
            self._last_reconcile = now
        positions = self._read_positions("reconciliation skipped this pass")
        if positions is None:                             # the next pass retries; the per-tick stop check still guards
            return
        self._foreign = sorted(p.position_id for p in positions if not self._own(p.magic))
        changed = self._resolve_stale_sending({p.position_id: p for p in positions})
        for p in positions:
            if not self._own(p.magic):
                continue
            tr = self.open.get(p.position_id)
            if tr is None:
                tr = self.open[p.position_id] = self._adopt(p)
                changed = True
                self.decisions.append({"ts": time.time(), "action": "adopt_orphan", "position": p.position_id,
                                       "agent": tr.agent_id, "policy": tr.policy is not None})
            if tr.sl is None and p.sl is None:
                a = self._atr_now()
                if a is None:
                    self.decisions.append({"ts": time.time(), "action": "orphan_stop_pending", "position": p.position_id})
                    continue                  # no ATR yet (no bars): retried on the next pass
                tr.sl = round(p.open_price - p.side * self.cfg.orphan_stop_atr * a, 2)
                res = self.broker.modify(p.position_id, tr.sl, p.tp)
                self.decisions.append({"ts": time.time(), "action": "orphan_stop", "position": p.position_id, "sl": tr.sl,
                                       "ok": res.ok, "retcode": res.retcode})
                continue
            loose = tr.sl is not None and (p.sl is None or p.side * (tr.sl - p.sl) > 1e-9)
            lost_tp = tr.tp is not None and p.tp is None
            if loose or lost_tp:
                fails, due = self._modify_backoff.get(p.position_id, (0, None))
                if now is not None and due is not None and now < due:
                    continue                  # backing off after rejects; the per-tick stop check still guards
                sl = tr.sl if loose else p.sl
                tp = tr.tp if lost_tp else p.tp
                res = self.broker.modify(p.position_id, sl, tp)
                if res.ok:
                    self._modify_backoff.pop(p.position_id, None)
                elif now is not None:
                    wait = min(self.cfg.reconcile_every_s * 2 ** fails, self.cfg.modify_backoff_max_s)
                    self._modify_backoff[p.position_id] = (fails + 1, now + pd.Timedelta(seconds=wait))
                self.decisions.append({"ts": time.time(), "action": "reinstate_stops", "position": p.position_id, "sl": sl,
                                       "tp": tp, "ok": res.ok, "retcode": res.retcode})
            elif tr.sl is None and p.sl is not None:
                tr.sl = p.sl                  # adopted with a stop: that stop is the floor from now on
        live = {p.position_id for p in positions}
        self._modify_backoff = {k: v for k, v in self._modify_backoff.items() if k in live}
        if changed:
            self._save_orders()             # an adoption or a resolved row survives a restart (review MEDIUM-2)

    def _adopt(self, p: Position) -> OpenTrade:
        """An open position of this engine's magic range that the open table does not list. When this engine sent it
        (the pending_orders row, by position id or client id / comment) the trade keeps what it was sent with: its
        agent, the stop and lots at entry (R is measured against them, never against a stop trailed since) and the
        signal ATR for its exit policy. Otherwise it is an orphan: its agent from the magic number when that is unique,
        and the broker's current stop as its initial stop."""
        rec = next((o for o in self._orders.values() if o.position_id == p.position_id), None) or \
            next((o for o in self._orders.values() if p.comment and p.comment in (o.client_order_id, o.client_order_id[-31:])), None)
        if rec is not None and rec.status != "filled":
            # review MEDIUM-2 (a): the row of a send that raised (or a fill found late) now names its position, so a
            # restart keeps this trade instead of recovering the send again and resetting its stop and state
            rec.status, rec.position_id = "filled", p.position_id
        agent = self.agents.get(rec.agent_id) if rec is not None else self._agent_for_magic(p.magic)
        agent_id = rec.agent_id if rec is not None else "orphan" if agent is None else agent.agent_id
        max_bars = rec.max_bars if rec is not None else 48 if agent is None else self._base_bars(agent)
        tr = OpenTrade(position_id=p.position_id, agent_id=agent_id, side=p.side, lots=p.lots,
                       entry_bar_ts=p.open_time_utc, max_bars=max_bars, sl=p.sl, tp=p.tp,
                       client_order_id=rec.client_order_id if rec is not None else p.comment or None,
                       open_price=p.open_price, initial_sl=rec.sl if rec is not None else p.sl,
                       initial_lots=rec.lots if rec is not None else p.lots, opened_utc=p.open_time_utc,
                       excursion_approx=True, risk_known=rec is not None,
                       audit=rec.audit if rec is not None else None)
        if agent is not None:
            atr_usd = rec.atr_usd if rec is not None and rec.atr_usd is not None else \
                self._implied_atr(agent, p.open_price, tr.initial_sl)
            self._attach_policy(tr, agent, entry=p.open_price, atr_usd=atr_usd, opened=p.open_time_utc)
        return tr

    def _agent_for_magic(self, magic: int) -> Specialist | None:
        """The agent an orphan belongs to: magic numbers are per family, so only a family with exactly one agent here
        identifies it."""
        hits = [a for a in self.agents.values() if self._magic(a.family) == magic]
        return hits[0] if len(hits) == 1 else None

    def _atr_now(self) -> float | None:
        """Decision-timeframe ATR(14): the last bar close's, else computed from the 1m history (after a restart)."""
        if self._last_atr is not None:
            return self._last_atr
        if len(self.bars_1m) < 15 * tf_seconds(self.cfg.decision_tf) // 60:
            return None
        a = atr(mid(resample_bars(self.bars_1m, self.cfg.decision_tf).reset_index(drop=True)), 14)
        v = float(a.iloc[-1]) if len(a) else float("nan")
        return v if np.isfinite(v) and v > 0 else None

    # ------------------------------------------------------------------ account view (reporting only)
    def _lot_terms(self) -> dict | None:
        """The symbol's lot terms from the broker for the milestone ladder; None (retried on the next refresh) when
        the broker cannot say. Never the design defaults: the ladder then shows 'pending broker terms'."""
        try:
            info = self.broker.symbol_info(self.cfg.symbol)
        except Exception as exc:                 # noqa: BLE001 - reporting only; trading never depends on it
            now = time.monotonic()               # retried every refresh (seconds apart): warn at most every 10 min
            if now - self._lot_terms_warned >= LOT_TERMS_WARN_S:
                self._lot_terms_warned = now
                log.warning("symbol info unavailable for the account view: %s", type(exc).__name__)
            return None
        return {"contract_oz": info.contract_size, "volume_min": info.volume_min, "volume_step": info.volume_step,
                "volume_max": info.volume_max, "stops_level_points": info.stops_level_points}

    def _update_ladder(self, complete: pd.DataFrame) -> None:
        """ATR(14) on the mid of each ladder timeframe's completed bars (None until 15 bars exist), at a bar close.
        Each timeframe is recomputed only when a new bar of it can have completed (1d: once per feature-day), from the
        engine's incremental resamplers (the same bars the decision frames use; 1d is the feature-day bar), and is
        marked done only once its ATR is computed, so a failure is retried at the next close."""
        if complete.empty:
            return
        last = pd.Timestamp(complete["visible_at"].iloc[-1])
        out = self._ladder["ladder_atr"]
        seen: dict[str, object] = self._ladder.setdefault("computed_at", {})
        at = pd.DatetimeIndex([last])
        for tf in LADDER_TIMEFRAMES:
            key = feature_day(at)[0] if tf == "1d" else floor_tf(at, tf_seconds(tf))[0]   # as _context keys it
            if seen.get(tf) == key:
                continue
            bars = self._resample(complete, tf)
            bars = bars[pd.to_datetime(bars["visible_at"], utc=True) <= last].reset_index(drop=True)
            if len(bars) < 15:
                out[tf] = None
            else:
                v = float(atr(mid(bars.tail(200).reset_index(drop=True)), 14).iloc[-1])
                out[tf] = v if np.isfinite(v) and v > 0 else None
            seen[tf] = key

    # ------------------------------------------------------------------ helpers
    def _refresh_account(self, tick: Tick) -> None:
        acc = self.broker.account()
        st = self.state
        st.equity = acc.equity
        st.margin_used = acc.margin
        self._account_view = {"balance": acc.balance, "currency": acc.currency, "margin": acc.margin,
                              "margin_free": acc.margin_free}
        self._price = (tick.bid + tick.ask) / 2
        if self._ladder["lot_terms"] is None:
            self._ladder["lot_terms"] = self._lot_terms()
        self._roll_risk_period(tick.ts_utc)
        if st.balance_closed_hwm == 0:
            st.balance_closed_hwm = acc.balance
        if st.day_start_equity == 0:            # normally set by the risk-period roll on the first tick
            st.day_start_equity = acc.balance
        if st.week_start_equity == 0:
            st.week_start_equity = acc.balance
        st.balance_closed_hwm = max(st.balance_closed_hwm, acc.balance)
        # exposure unknown: the last reading stays and entries are blocked below
        positions = self._read_positions("exposure kept from the last reading, entries blocked")
        if positions is not None:
            st.open_positions = len(positions)
            st.open_lots = round(sum(p.lots for p in positions), 6)   # every position on the account is exposure
            self._position_ids = {p.position_id for p in positions}
            try:
                self._note_open_risk(positions, tick)
            except Exception:                   # review LOW-1: never aborts the refresh; unknown refuses when on
                log.exception("open risk not computed; it is unknown until the next refresh")
                st.open_risk, self._open_risk_rows = None, []
        else:                                   # unknown book: unknown open risk (entries are blocked anyway)
            st.open_risk, self._open_risk_rows, self._position_ids = None, [], None
        st.open_notional = st.open_lots * self._contract_size() * (tick.bid + tick.ask) / 2   # USD, terminal contract
        st.spread_points = (tick.ask - tick.bid) / 0.01
        now = self._now(tick)
        # signed: a tick stamped AFTER the host clock (wrong server_tz, broker or host clock) must not look fresh, so
        # staleness uses the distance either way and the lead itself is an entry block (`tick_from_future`)
        age = (now - tick.ts_utc).total_seconds()
        st.last_tick_age_s = abs(age)
        st.tick_ahead_s = max(0.0, -age)
        sessions = self.sessions
        try:
            stale = stale_feed(now - pd.Timedelta(seconds=abs(age)), now, limit_seconds=self.cfg.stale_feed_s,
                               sessions=sessions)
            closed = sessions.entry_block(now) is not None
        except Exception as exc:        # a broken session table fails closed for entries; exits never read it
            log.warning("session table unusable (%s): entries blocked as market_closed", type(exc).__name__)
            stale, closed = False, True
        st.dq_error = stale or bool(self._dq_errors() or self._dq_blocking()) or positions is None \
            or self._book_unreadable() > 0                                    # fail closed: positions_unreadable
        # stale data (design): the last completed bar must be under one decision period old; no bars is stale
        if self.bars_1m.empty:
            st.stale_bars = True
        else:
            seen = pd.Timestamp(self.bars_1m["visible_at"].iloc[-1])
            st.stale_bars = abs((now - seen).total_seconds()) > tf_seconds(self.cfg.decision_tf)
        self.clock_probe = self._probe_clock(tick, now, sessions)
        st.clock_mismatch = self.clock_probe.status == "mismatch"
        st.market_closed = closed
        if st.dq_error or st.stale_bars or st.last_tick_age_s > self.gate.limits.stale_tick_seconds \
                or st.tick_ahead_s > self.gate.limits.max_tick_ahead_s or st.clock_mismatch:
            self._unhealthy_at = now
            st.data_recovering = False        # the error itself blocks; recovery starts when it clears
        else:
            st.data_recovering = self._unhealthy_at is not None and \
                (now - self._unhealthy_at).total_seconds() < self.cfg.healthy_resume_s
        if self.cfg.halt_checks:
            st.supervisor_halt, _ = Supervisor.engine_should_halt(self.cfg.state_dir)
            sup = Supervisor.read_state(self.cfg.state_dir)
            others = [v for k, v in (sup.get("exposure") or {}).items() if k != self.cfg.account_id]
            st.other_lots = sum(float(v.get("lots", 0.0)) for v in others)
            st.other_notional = sum(float(v.get("notional", 0.0)) for v in others)
            st.other_equity = sum(float(v.get("equity", 0.0)) for v in others)
            st.combined_size_down = bool(sup.get("size_down", False))
            st.owner_halt = self.center.bus.control().halted if self.center.bus is not None else False
        if self.cfg.news_blackout:
            st.in_blackout = self._blackout(tick.ts_utc) is not None
        self._entry_windows(tick.ts_utc, now, sessions)
        # design (Account classifier): a broker account trades by its measured class; Unknown (incl. not classified
        # yet) trades nothing, Standard is restricted in the gate. The paper broker has no account to classify.
        st.account_class = "raw" if self.cfg.mode == "paper" else self._account_class()
        st.drift_halt = bool(self._drift().get("system_halt"))
        self._server_clock(now, tick)
        owner_mode = self.center.bus.control().approval_mode if self.center.bus is not None else None
        if owner_mode is not None:
            self.cfg.approval_mode = owner_mode  # the owner's /mode (A10); the kill switch and re-arm lock win below
        stage = st.stage
        if self.gate.update_stage(st) != stage:
            if st.stage == Stage.HALTED:
                self._halted_at = now
            self._save_risk_state()             # a drawdown stage change survives a restart at once
        if st.stage == Stage.HALTED:
            self._kill_switch(everything=stage != Stage.HALTED)
        if self._propose_only_until is not None and now < self._propose_only_until:
            self.cfg.approval_mode = "propose"  # 30 days propose-and-approve after a re-arm

    def set_sessions(self, table: SessionTable) -> None:
        """The broker session table (D13), refreshed by the run loop outside the tick path (goldbot/ops/run.py
        `refresh_sessions`); used for market_closed, feed staleness and the server-clock probe."""
        self.sessions = table

    def set_bridge_compat(self, compat: BridgeCompat | None) -> None:
        """The MT5 bridge's compatibility verdict (goldbot/ops/bridge_compat.py), checked by the run loop outside the
        tick path; an incompatible bridge refuses entries (`bridge_incompatible`). Exits never read it."""
        self.bridge_compat = compat
        self.state.bridge_incompatible = compat is not None and compat.blocks

    def _probe_clock(self, tick: Tick, now: pd.Timestamp, sessions: SessionTable) -> server_clock.ServerClockProbe:
        """The server-clock probe on the tick this refresh already holds (no broker call): its stamp, converted with
        server_tz, against the host clock. Any error is "not measured", which never blocks (stale and closed-market
        rules cover those cases); a measured mismatch blocks entries only."""
        try:
            return server_clock.assess(self.cfg.account_id, self.cfg.server_tz, tick.ts_utc, now, sessions)
        except Exception as exc:
            log.warning("server clock probe failed (%s); not measured", type(exc).__name__)
            return server_clock.ServerClockProbe(account_id=self.cfg.account_id, measured_utc=now,
                                                 server_tz=self.cfg.server_tz, status="not_measured",
                                                 reason=f"probe failed ({type(exc).__name__})")

    def _kill_switch(self, *, everything: bool) -> bool:
        """12% drawdown: close at market immediately (design: Drawdown kill switch) and fall back to propose-and-
        approve. On the trip every position on the account is closed; while halted, any of this engine's positions
        (its magic range) that is still open, e.g. after a failed close, is closed again on each refresh. The trip also
        sets the owner's mode back to propose, so auto mode is offered again only on new evidence (A10).
        Each close is tried on its own (one raising or refused close never stops the others) and a failed one is
        re-sent every tick (`_retry_closes`); until a positions() read shows this engine's magic range flat the switch
        runs again on every tick (`_kill_pending`). Returns whether the range was seen flat."""
        self.cfg.approval_mode = "propose"
        if everything and self.center.bus is not None and self.center.bus.control().approval_mode == "auto":
            self.center.bus.set_mode("propose", by=f"engine:{self.cfg.account_id}", reason="12% kill switch")
            self.decisions.append({"ts": time.time(), "action": "mode_propose", "reason": "kill_switch"})
        self._kill_pending = True
        positions = self._read_positions("the kill switch runs again next tick")
        if positions is None:
            return False
        flat, left = True, len(positions)
        for p in positions:
            if not (everything or self._own(p.magic)):
                continue
            # a close already due is re-sent by _retry_closes (with its market-closed backoff), not twice per tick
            ok = p.position_id not in self._close_due and self._try_close(p.position_id, "kill_switch_close", None).ok
            left -= ok
            flat = flat and (ok or not self._own(p.magic))
        self._kill_pending = not flat
        self.state.open_positions = left
        return flat

    def _server_clock(self, now: pd.Timestamp, tick: Tick) -> None:
        """Rollover (+-rollover_min of 00:00 server: no entries) and weekend (Friday weekend_cut server until the week
        reopens: no entries; at the cut, once per Friday, this engine's losers are closed and winners' stops tightened)."""
        srv = server_wall(now, self.cfg.server_tz)[0]        # naive server wall clock; any server_tz incl. NY+7
        mins = srv.hour * 60 + srv.minute + srv.second / 60
        self.state.in_rollover = min(mins, 1440 - mins) <= self.cfg.rollover_min
        h, m = (int(x) for x in self.cfg.weekend_cut.split(":"))
        friday_cut = srv.weekday() == 4 and mins >= h * 60 + m
        self.state.weekend = friday_cut or srv.weekday() >= 5
        if friday_cut and self._weekend_done != srv.date().isoformat() and self._weekend_rule(tick):
            self._weekend_done = srv.date().isoformat()   # only once every position was handled; else retried
            self._save_risk_state()

    def _weekend_rule(self, tick: Tick) -> bool:
        """Design: weekend gaps blow through stops, so losers are closed and winners' stops moved to lock in half the
        open profit (never loosened). Exits are automatic and never gated. Each position is handled on its own (one
        raising or refused call never stops the others); a failed loser close is re-sent every tick (`_retry_closes`)
        and the rule runs again on each account refresh until it completes. Returns whether it completed for every
        position (an unreadable book is not complete)."""
        positions = self._read_positions("the weekend rule is retried on the next refresh")
        if positions is None:
            return False
        done, left = True, len(positions)
        for p in positions:
            if not self._own(p.magic):
                continue
            px = tick.bid if p.side > 0 else tick.ask
            if p.side * (px - p.open_price) <= 0:
                ok = p.position_id not in self._close_due and \
                    self._try_close(p.position_id, "weekend_close_loser", tick.ts_utc).ok
                left -= ok
                done = done and ok
                continue
            sl = round(p.open_price + 0.5 * (px - p.open_price), 2)
            tr = self.open.get(p.position_id)
            if tr is not None and tr.sl is not None and p.side * (tr.sl - sl) > 0:
                sl = tr.sl                    # the engine's stop is already tighter (e.g. a rejected trail modify)
            if p.sl is None or p.side * (sl - p.sl) > 0:
                if tr is not None:
                    tr.sl = sl                # never loosens; reconciliation and the stop check enforce it meanwhile
                try:
                    res = self.broker.modify(p.position_id, sl, p.tp)
                except Exception as e:        # one position never stops the others
                    log.exception("weekend tighten failed for position %s; retried on the next refresh", p.position_id)
                    res = OrderResult(ok=False, retcode=-1, order_id=None, position_id=p.position_id, filled_lots=0.0,
                                      price=None, message=repr(e))
                self.decisions.append({"ts": time.time(), "action": "weekend_tighten", "position": p.position_id, "sl": sl,
                                       "ok": res.ok, "retcode": res.retcode})
                done = done and res.ok
        self.state.open_positions = left
        return done

    def _now(self, tick: Tick) -> pd.Timestamp:
        return self.clock() if self.cfg.live_clock else tick.ts_utc

    # ------------------------------------------------------------------ data quality
    def _dq_errors(self) -> list[DQEvent]:
        """Blocking data-quality events since (and decided at) the last bar close; warnings never block entries."""
        return [e for e in self._dq_bar_events + self._dq_pending if e.severity == "error"]

    def _late_tick(self, t: Tick) -> None:
        """A tick stamped before a bar close the clock already processed (D10: close plus BAR_CLOSE_GRACE_S): it is
        kept out of the live bars, which is correct, but the tick log (nightly store) still has it, so the two can
        differ. Recorded as a `late_tick` warning (stored with the bar's dq_events), at most one per LATE_TICK_WARN_S,
        every one counted in the engine state. Usually the broker clock lags the wall clock by more than the grace
        (`clock_skew_s`, warned by health)."""
        self._late_ticks += 1
        self._late_since_warn += 1
        bar = self._closed_through
        if bar is not None and bar != self._late_bar:
            prev = bar - pd.Timedelta(seconds=tf_seconds(self.cfg.decision_tf))
            self._late_streak = self._late_streak + 1 if self._late_bar == prev else 1
            self._late_bar = bar
        now = self._now(t)
        if self._late_warned_at is not None and (now - self._late_warned_at).total_seconds() < LATE_TICK_WARN_S:
            return
        self._dq_pending.append(DQEvent(
            ts_utc=t.ts_utc, check="late_tick", severity="warning",
            detail=f"{self._late_since_warn} late tick(s) since the last warning, latest at {t.ts_utc} after the "
                   f"close through {self._closed_through}: kept out of the live bars, still in the tick log"))
        self._late_warned_at, self._late_since_warn = now, 0

    def _dq_blocking(self) -> set[str]:
        """Clock data-quality errors that block entries (exits are never gated): a broker clock more than
        SKEW_BLOCK_S off the wall clock (`clock_skew`), or late ticks on LATE_BARS_BLOCK consecutive decision bars
        (`late_ticks`), until a whole decision bar passes without one. Either way bars are being finalised without
        their last ticks and drift from the bars the models were trained and shadow-scored on."""
        out: set[str] = set()
        skew = self.clock_skew_s()
        if skew is not None and len(self._skew) >= SKEW_MIN_SAMPLES and abs(skew) > SKEW_BLOCK_S:
            out.add("clock_skew")
        if self._late_streak >= LATE_BARS_BLOCK and self._late_bar is not None and self._closed_through is not None \
                and (self._closed_through - self._late_bar).total_seconds() <= tf_seconds(self.cfg.decision_tf):
            out.add("late_ticks")
        return out

    def clock_skew_s(self) -> float | None:
        """Broker tick time minus the wall clock (s), rolling median over the last SKEW_WINDOW new ticks; None before
        the first. Below -BAR_CLOSE_GRACE_S, bars close by clock before their last ticks arrive (late ticks): health
        warns above the grace, and beyond SKEW_BLOCK_S it is a blocking data-quality error (`_dq_blocking`)."""
        return round(float(np.median(self._skew)), 3) if self._skew else None

    def _quote_fault(self, t: Tick, *, order: bool = True) -> tuple[str, str] | None:
        """(check, detail) when a quote breaks the tick rules: a non-finite price, a non-positive bid or a crossed quote,
        and with `order` a tick older than the previous one; None when it is usable."""
        prev = self._last_tick
        if not (math.isfinite(t.bid) and math.isfinite(t.ask)):
            return "non_finite_quote", f"bid {t.bid} ask {t.ask}"
        if t.bid <= 0 or t.ask < t.bid:
            return "bid_gt_ask", f"bid {t.bid} ask {t.ask}"
        if order and prev is not None and t.ts_utc < prev.ts_utc:
            return "non_monotonic", f"tick at {t.ts_utc} after {prev.ts_utc}"
        return None

    def _tick_ok(self, t: Tick) -> bool:
        """Tick-level checks (`_quote_fault`): a non-finite, crossed or non-positive quote, or a tick older than the
        previous one, is a data-quality error (recorded; the tick is dropped and `no_quote_pass` runs the exits that
        need no price). Repeats of one check fold into its pending event with a count (review LOW-1), so a feed stuck
        on NaN never grows the list between bar closes."""
        fault = self._quote_fault(t)
        if fault is None:
            return True
        check, detail = fault
        i = next((k for k, e in enumerate(self._dq_pending) if e.check == check), None)
        if i is None:
            self._dq_repeats[check] = 1
            self._dq_pending.append(DQEvent(ts_utc=t.ts_utc, check=check, severity="error", detail=detail))
        else:
            n = self._dq_repeats[check] = self._dq_repeats.get(check, 1) + 1
            self._dq_pending[i] = self._dq_pending[i].model_copy(update={"detail": f"{detail} (latest of {n} ticks)"})
        return False

    def _check_new_bars(self, done: pd.DataFrame) -> None:
        """The ingest checks (goldbot.data.quality.check_bars) on newly completed 1m bars, joined to the last stored
        bar so a duplicate or out-of-order minute across the boundary is caught; errors block entries."""
        tail = self.bars_1m.tail(1) if not self.bars_1m.empty else self.bars_1m
        joined = pd.concat([tail, done]).reset_index(drop=True) if not tail.empty else done
        mono = len(joined) > 1 and not pd.DatetimeIndex(joined["ts_utc"]).is_monotonic_increasing
        errs = bar_errors(joined)                       # = the error events of check_bars(joined)
        if mono and not any(e.check == "non_monotonic" for e in errs):   # check_bars sorts before testing order
            errs.append(DQEvent(ts_utc=pd.Timestamp(done["ts_utc"].iloc[0]), check="non_monotonic", severity="error",
                                detail="completed bar not after the last stored bar"))
        self._dq_pending += errs

    # ------------------------------------------------------------------ risk periods and re-arm
    def _roll_risk_period(self, now: pd.Timestamp) -> None:
        """Daily and weekly loss caps roll over at the risk-day / risk-week boundary (gate.new_day); the reset time
        is persisted so a restart neither resets twice in one day nor skips a boundary."""
        last = self._last_reset
        if last is not None and now.tz_convert("UTC").date() == last.tz_convert("UTC").date():
            return
        self.state.equity = self.broker.account().equity
        if new_day(self.state, now, last):
            self._last_reset = now
            self._save_risk_state()

    def _poll_rearm(self, now: pd.Timestamp) -> None:
        """An owner re-arm on the dashboard (owner role + TOTP) writes a new rearm id to control.json; seen once, it
        clears this engine's drawdown halt if the design's conditions hold (`_rearm_conditions`), and starts the
        propose-only period. A refused re-arm is used up: the owner re-arms again once the conditions are met.
        Nothing else clears the halt, and a restart does not."""
        if self.center.bus is None:
            return
        c = self.center.bus.control()
        if c.rearm_id is None or c.rearm_id == self._rearm_seen:
            return
        self._rearm_seen = c.rearm_id
        if self.state.stage == Stage.HALTED:
            refused = self._rearm_conditions(now)
            if refused is not None:
                self._rearm_refused = refused
                self.decisions.append({"ts": time.time(), "action": "rearm_refused", "by": c.rearm_by,
                                       "rearm_id": c.rearm_id, "reason": refused})
            else:
                self.state.equity = self.broker.account().equity
                if self.gate.rearm(self.state, REARM_PHRASE):
                    self._rearm_refused, self._halted_at = None, None
                    self._propose_only_until = now + pd.Timedelta(days=self.cfg.rearm_propose_days)
                    self.cfg.approval_mode = "propose"
                    self.decisions.append({"ts": time.time(), "action": "rearm", "by": c.rearm_by, "rearm_id": c.rearm_id,
                                           "propose_only_until": self._propose_only_until.isoformat()})
        self._save_risk_state()

    def _rearm_conditions(self, now: pd.Timestamp) -> str | None:
        """None when a 12% halt may be re-armed: at least `rearm_shadow_days` trading days since the halt and a
        positive paper-shadow record (sum of closed shadow returns, all versions) over them; else the reason."""
        if self._halted_at is None:
            self._halted_at = now                 # halt time unknown (older state file): the conditions start now
        days = int(np.busday_count(self._halted_at.date(), now.date()))
        if days < self.cfg.rearm_shadow_days:
            return (f"re-arm needs {self.cfg.rearm_shadow_days} trading days of positive paper shadow since the halt "
                    f"at {self._halted_at.isoformat()}; {days} so far")
        book = self.shadow if self.shadow is not None else ShadowBook(self.cfg.state_dir)
        rets = [r for v in book.books for r in book.returns_since(v, self._halted_at)]
        if not rets or sum(rets) <= 0:
            return (f"re-arm needs positive paper shadow since the halt: {len(rets)} closed shadow trades, "
                    f"total return {sum(rets):+.4f}")
        return None

    # ------------------------------------------------------------------ pending_orders and restart reconciliation
    def _orders_path(self) -> Path:
        return Path(self.cfg.state_dir, f"orders_{self.cfg.account_id}.json")   # not engine_*: the supervisor globs those

    def _orders_record(self, cid: str, *, agent_id: str, side: int, magic: int, lots: float, max_bars: int, sl: float,
                       tp: float, ts: pd.Timestamp, atr_usd: float | None = None,
                       audit: EntryAudit | None = None) -> None:
        """Write the id to the pending_orders table (atomically, on disk) before order_send."""
        self.sent_ids.add(cid)
        self._orders[cid] = SentOrder(client_order_id=cid, ts_utc=ts, agent_id=agent_id, side=side, magic=magic, lots=lots,
                                      max_bars=max_bars, sl=sl, tp=tp, atr_usd=atr_usd, audit=audit)
        self._save_orders()

    def _save_orders(self) -> None:
        self._flush_closed()      # a close is on disk before the open-trade table forgets the position
        keep_after = pd.Timestamp.now("UTC") - pd.Timedelta(days=7)
        self._orders = {k: o for k, o in self._orders.items() if o.ts_utc >= keep_after or o.status == "sending"}
        payload = {"sent": {k: o.model_dump(mode="json") for k, o in self._orders.items()},
                   "open": {str(k): t.model_dump(mode="json") for k, t in self.open.items()},
                   # closed-trade records not written yet (a failed write) survive a restart; lost ones stay listed
                   "closing": [c.model_dump(mode="json") for c in self._closes],
                   "records_lost": self._records_lost}
        write_atomic(self._orders_path(), json.dumps(payload))     # durable: restart reconciliation reads it

    def _load_orders(self) -> None:
        """On start: reload the pending_orders table and the open trades, then reconcile them with the broker: an id
        whose send was never confirmed is looked up in the broker's positions and deals (by client id / MT5 comment);
        a fill is adopted with its own agent (and, when that agent is loaded, its exit policy), no fill marks it unfilled. Every id stays in sent_ids, so a restart never
        re-sends. Open trades the broker no longer has were closed while the engine was down."""
        path = self._orders_path()
        if not path.exists():
            return
        d = json.loads(path.read_text())          # unreadable -> fail loudly rather than forget what was sent
        # ADR 0010 (rollback = restart): fields a newer release added are dropped, a wrong type still fails loudly
        src = "orders_<account>.json"
        if extra := sorted(set(d) - {"sent", "open", "closing", "records_lost"}):
            note_schema_skew(src, "orders file", extra)      # a newer release's table: not read here (S12)
        self._orders = {k: load_record(SentOrder, v, source=src) for k, v in d.get("sent", {}).items()}
        self.sent_ids = set(self._orders)
        self.open = {int(k): load_record(OpenTrade, v, source=src) for k, v in d.get("open", {}).items()}
        for tr in self.open.values():
            tr.excursion_approx = True            # ticks while the engine was down were not seen (BACKLOG 23)
        self._closes = [load_record(PendingClose, v, source=src) for v in d.get("closing", [])]
        self._records_lost = [int(x) for x in d.get("records_lost", [])]
        try:
            positions = {p.position_id: p for p in self.broker.positions()}
        except Exception as exc:
            # fail closed (the engine does not start), but say why: the health check then shows an unreadable
            # terminal instead of a merely stale engine (trading-safety review)
            # (review LOW-D) merged into the last engine file, so its exposure keeps counting at the supervisor
            write_startup_error(self.cfg.state_dir, self.cfg.account_id, f"{type(exc).__name__}: {exc}",
                                dq_checks=["positions_unreadable"],
                                currency=self._account_currency() or None)   # the supervisor's currency check
            raise
        for cid, rec in self._orders.items():
            if rec.status == "sending":
                self._resolve_sent(cid, rec, positions)
        # open trades the broker no longer has were closed while the engine was down: recorded once their exit deal
        # is seen (a trade recorded before the restart is skipped by position id)
        self._sweep_closed(set(positions))
        self._save_orders()

    def _resolve_sent(self, cid: str, rec: SentOrder, positions: dict[int, Position]) -> None:
        """A `sending` row (a send never confirmed: a crash, or an order call that raised) looked up in the broker's
        positions and deals by client id / MT5 comment. A position is adopted with the row's agent, entry stop, lots
        and exit policy, unless the open table already holds it (adopted by reconciliation, or carried over a
        restart): that trade is kept with its stop, bars held and scale-out state and only the row is marked filled
        (review MEDIUM-2 b). No position: filled and closed (recorded from its deals) or never filled ("unfilled").
        An unreadable deal history without a position leaves the row `sending` for the next look. Never re-sent."""
        pos = next((p for p in positions.values() if p.comment == cid[-31:] or p.comment == cid), None)
        try:
            deals = self._read_deals(rec.ts_utc - pd.Timedelta(minutes=5))
        except Exception:
            if pos is None:
                return                # unknown yet: stays "sending" (never re-sent) and is looked up again
            deals = pd.DataFrame()
        dealt = not deals.empty and (
            ("client_order_id" in deals.columns and bool((deals["client_order_id"] == cid).any()))
            or ("comment" in deals.columns and bool(deals["comment"].isin([cid, cid[-31:]]).any())))
        if pos is not None:
            rec.status, rec.position_id = "filled", pos.position_id
            if pos.position_id in self.open:
                self.decisions.append({"ts": time.time(), "agent": rec.agent_id, "action": "reconcile_sent_known",
                                       "proposal": cid, "position": pos.position_id})
                return
            tr = self.open[pos.position_id] = OpenTrade(position_id=pos.position_id, agent_id=rec.agent_id,
                                                        side=pos.side, lots=pos.lots, entry_bar_ts=pos.open_time_utc,
                                                        max_bars=rec.max_bars, sl=rec.sl, tp=rec.tp,
                                                        client_order_id=cid, open_price=pos.open_price,
                                                        initial_sl=rec.sl, initial_lots=rec.lots,
                                                        opened_utc=pos.open_time_utc, excursion_approx=True,
                                                        audit=rec.audit)
            agent = self.agents.get(rec.agent_id)
            if agent is not None:
                atr_usd = rec.atr_usd if rec.atr_usd is not None else self._implied_atr(agent, pos.open_price, rec.sl)
                self._attach_policy(tr, agent, entry=pos.open_price, atr_usd=atr_usd, opened=pos.open_time_utc)
            self.decisions.append({"ts": time.time(), "agent": rec.agent_id, "action": "reconcile_adopt_sent",
                                   "proposal": cid, "position": pos.position_id})
        else:
            rec.status = "filled" if dealt else "unfilled"     # filled and already closed, or never filled
            self.decisions.append({"ts": time.time(), "agent": rec.agent_id, "action": f"reconcile_sent_{rec.status}",
                                   "proposal": cid})
            if dealt:
                self._record_closed_while_down(cid, rec, deals)

    def _resolve_stale_sending(self, positions: dict[int, Position]) -> bool:
        """Review MEDIUM-2 (d): `sending` rows older than SENDING_RESOLVE_S (host clock, as the row was stamped) are
        resolved in-process as a restart would (`_resolve_sent`), so a send that raised does not wait for a restart.
        Younger rows may still be in flight and are left alone. Returns whether any row changed."""
        now = pd.Timestamp.now("UTC")
        changed = False
        for cid, rec in list(self._orders.items()):
            if rec.status == "sending" and (now - pd.Timestamp(rec.ts_utc)).total_seconds() >= SENDING_RESOLVE_S:
                self._resolve_sent(cid, rec, positions)
                changed = changed or rec.status != "sending"
        return changed

    # ------------------------------------------------------------------ closed-trade record (phase gates P7, stop rule P6)
    def _record_closed_while_down(self, cid: str, rec: SentOrder, deals: pd.DataFrame) -> None:
        """A send confirmed only by its deals on restart (filled and closed while the engine was down): its position
        is found by client id / comment and recorded like any other close."""
        if "position_id" not in deals.columns:
            return
        if "client_order_id" in deals.columns:
            mine = deals[deals["client_order_id"] == cid]
        else:
            mine = deals[deals["comment"].isin([cid, cid[-31:]])]
        if mine.empty:
            return
        pid = int(mine["position_id"].iloc[0])
        rec.position_id = pid
        tr = OpenTrade(position_id=pid, agent_id=rec.agent_id, side=rec.side, lots=rec.lots, entry_bar_ts=rec.ts_utc,
                       max_bars=rec.max_bars, sl=rec.sl, tp=rec.tp, client_order_id=cid, initial_sl=rec.sl,
                       initial_lots=rec.lots, opened_utc=rec.ts_utc, audit=rec.audit)
        self._note_closed(pid, tr, "broker_close")

    def _sweep_closed(self, live: set[int]) -> None:
        """Open trades the broker no longer lists closed at the broker (stop, target, stop-out, manual): each is
        recorded once its exit deal is seen and then forgotten. When the broker's deal history carries positions but
        shows no exit, or is empty (a positions() reply that dropped the trade, or a deal not synced yet), the trade is
        kept as it is and asked again at the next bar close, `close_confirm_checks` times, then recorded from what the
        engine knows. An unreadable deal history (the read raised: MT5 history_deals_get returned None) never confirms
        a close: the trade is kept, uncounted, until the history can be read. Only called with a positions() reply
        that was read successfully, so an unreadable book never records or forgets a trade. A trade that reappears
        simply carries on with its stop and policy."""
        self._unconfirmed = {k: v for k, v in self._unconfirmed.items() if k in self.open and k not in live}
        missing = [k for k in self.open if k not in live]
        if not missing:
            self._read_fails["deals"] = 0       # no deal read outstanding: a past failure no longer blocks entries
        for pid in missing:
            tr = self.open[pid]
            try:
                summary, _ = self._exit_deals(pid, tr)
            except Exception:                   # counted by _read_deals (positions_unreadable); asked again next pass
                continue
            tries = self._unconfirmed.get(pid, 0) + 1
            if summary is None and tries < self.cfg.close_confirm_checks:
                if tries == 1:
                    self.decisions.append({"ts": time.time(), "agent": tr.agent_id, "action": "close_unconfirmed",
                                           "position": pid})
                self._unconfirmed[pid] = tries
                continue
            self._unconfirmed.pop(pid, None)
            del self.open[pid]
            self._note_closed(pid, tr, "broker_close")

    def _note_closed(self, pid: int, tr: OpenTrade, reason: str, res: OrderResult | None = None) -> None:
        """Queue the record of a fully closed position. Nothing is read or written here, so an exit never waits on the
        record: the queue is written after the tick's exits (`on_tick`) and before the open-trade table is saved."""
        if pid in self._recorded or any(c.position_id == pid for c in self._closes):
            return
        px = res.price if res is not None and res.ok else None
        at = self._last_tick.ts_utc if self._last_tick is not None else pd.Timestamp.now("UTC")
        self._closes.append(PendingClose(position_id=pid, trade=tr, reason=reason, price=px, at=at))

    def _flush_closed(self) -> None:
        """Write the queued records. A failure is logged and journalled (`closed_trade_record_failed`) and never
        raised: the exit has already happened and nothing after it may stop. The record stays queued (and in the
        open-trade table file, so a restart keeps it) and is retried every reconcile_every_s of tick time,
        `closed_record_retries` attempts in all; then it is reported lost (`closed_trade_record_lost` in the journal,
        `closed_records_lost` in the engine state, which fails the engine's health check and alerts the owner)."""
        if not self._closes:
            return
        now = self._last_tick.ts_utc if self._last_tick is not None else pd.Timestamp.now("UTC")
        pending, self._closes = self._closes, []
        keep: list[PendingClose] = []
        for c in pending:
            if c.next_try is not None and (self._last_tick is None or now < c.next_try):
                keep.append(c)                  # a retry waits for its time on the tick clock
                continue
            try:
                self._record_closed(c.position_id, c.trade, c.reason, c.price, c.at)
            except Exception as e:
                c.tries += 1
                lost = c.tries >= self.cfg.closed_record_retries
                log.exception("closed trade not recorded for position %s (%s), attempt %d", c.position_id, c.reason,
                              c.tries)
                self.decisions.append({"ts": time.time(), "agent": c.trade.agent_id, "action": "closed_trade_record_failed",
                                       "position": c.position_id, "reason": c.reason, "tries": c.tries,
                                       "error": repr(e)})
                if lost:
                    log.error("closed trade record LOST for position %s after %d attempts: %s", c.position_id,
                              c.tries, c.model_dump_json())
                    self.decisions.append({"ts": time.time(), "agent": c.trade.agent_id,
                                           "action": "closed_trade_record_lost", "position": c.position_id,
                                           "reason": c.reason, "pending": c.model_dump(mode="json")})
                    self._records_lost.append(c.position_id)
                    self._throttle_lost.append(c)
                else:
                    c.next_try = now + pd.Timedelta(seconds=self.cfg.reconcile_every_s)
                    keep.append(c)
        self._closes = keep + self._closes

    def _exit_deals(self, pid: int, tr: OpenTrade) -> tuple[dict | None, bool]:
        """(summary of the position's deals, whether the broker's deal history identifies positions at all). Reads the
        paper broker's deals and MT5's (DEAL_ENTRY 0 in, 1 out, 2 in/out, 3 out by; DEAL_REASON 4 SL, 5 TP, 6 stop-out)
        alike; the summary is None when no exit deal is there, including an empty history (never a confirmation). A
        read that fails raises (`_read_deals`)."""
        starts = [x for x in (tr.opened_utc, tr.entry_bar_ts) if x is not None]
        d = self._read_deals(min(starts) - pd.Timedelta(days=1))
        if not isinstance(d, pd.DataFrame) or "position_id" not in d.columns:
            return None, False
        d = d[d["position_id"] == pid]
        mt5 = "entry" in d.columns
        is_exit = d["entry"].isin([1, 2, 3]) if mt5 else d["type"] != "entry"
        vol = "volume" if mt5 else "lots"
        ex, ent = d[is_exit], d[~is_exit]
        lots = float(ex[vol].sum()) if not ex.empty else 0.0
        if lots <= 0:
            return None, True

        def total(col: str, rows: pd.DataFrame) -> float | None:
            return float(rows[col].fillna(0.0).sum()) if col in rows.columns else None
        comm = total("commission", d)
        fee = total("fee", d) or 0.0
        swap = total("swap", d)
        gross = total("profit", ex)
        pnl = gross + (comm or 0.0) + fee + (swap or 0.0) if gross is not None else float(total("pnl", ex) or 0.0)
        last = ex.sort_values("ts_utc").iloc[-1]
        if mt5:
            reason = {4: "stop", 5: "target", 6: "stop_out"}.get(int(last.get("reason", -1)))
        else:
            reason = str(last["type"]) if last["type"] in ("stop", "target") else None
        return {"exit_price": float((ex["price"] * ex[vol]).sum()) / lots, "exit_utc": pd.Timestamp(last["ts_utc"]),
                "last_price": float(last["price"]), "last_lots": float(last[vol]),
                "pnl": pnl, "commission": None if comm is None else -(comm + fee), "swap": swap,
                "entry_price": float(ent["price"].iloc[0]) if not ent.empty else None,
                "lots": float(ent[vol].sum()) if not ent.empty else None, "reason": reason}, True

    def _record_closed(self, pid: int, tr: OpenTrade, reason: str, px: float | None, at: pd.Timestamp) -> None:
        """One ClosedTrade for a fully closed position (scale-out folded in: see gates_phase.ClosedTrade), appended
        durably to state/closed_trades.jsonl. Money from the broker's deals when it reports them (net P&L with
        commission and swap); otherwise from the engine's own fills at the contract size (commission and swap None).
        Finance audit: `money_source` says which, and `pnl_basis` is "gross" unless commission and swap are both known
        (GAP-05); the stops (GAP-03) and the entry facts (`OpenTrade.audit`: GAP-01/02/13) go on the record, and a
        broker stop or target fill is logged against its level in `exit_fills` (GAP-04)."""
        if pid in self._recorded:
            return
        try:
            info, _ = self._exit_deals(pid, tr)
        except Exception:
            log.exception("deal history unreadable for position %s; recorded from the engine's fills", pid)
            info = None
        known = info or {}
        # neither the broker's deals nor the engine's own close fill: the price is the quote read now and the time the
        # moment the engine noticed, neither of them the trade's (MAE/MFE never folds them, BACKLOG 23)
        synthetic = info is None and px is None
        entry = tr.open_price if tr.open_price is not None else known.get("entry_price") or tr.entry
        if entry is None:
            raise ValueError("entry price unknown")
        lots = float(tr.initial_lots or known.get("lots") or round(tr.lots + tr.scaled_lots, 8))
        contract = 100.0
        try:
            contract = float(self.broker.symbol_info(self.cfg.symbol).contract_size) or contract
        except Exception:
            log.warning("symbol info unavailable; position %s recorded at contract size %s", pid, contract)
        # BACKLOG 28 (ADR 0004 A5): risk and notional are USD (price x lots x contract); the broker's P&L is in the
        # deposit currency. fx converts USD into it (1 on a USD account); None when the broker cannot say
        dist = abs(entry - tr.initial_sl) if tr.initial_sl is not None else 0.0
        fx = sizing.account_fx(self._account_currency(), self._usd_to_account(tr.side, entry, dist or 1.0))
        risk_usd, notional_usd = dist * lots * contract, entry * lots * contract
        # the P&L's currency on the record (dashboard contract): the account currency when the broker's P&L is used or
        # the engine's was converted, "USD" for the unconverted own-fills fallback; None is left to legacy records
        # (the monthly report flags those as R understated), and when the account currency itself is unknown
        pnl_currency: str | None = self._account_currency() or None
        if info is not None:                           # pnl from the broker's deals, in the account currency
            exit_px, exit_utc, pnl = info["exit_price"], info["exit_utc"], info["pnl"]
            commission, swap = info["commission"], info["swap"]
            if reason == "broker_close" and info["reason"]:
                reason = info["reason"]
            if fx is not None:
                r = pnl / (risk_usd * fx) if risk_usd > 0 else None
                ret = pnl / (notional_usd * fx)
            else:                                      # no conversion: R unknown (never a wrong R); gross price return
                log.warning("position %s: no USD -> %s conversion; R left empty, return from prices", pid,
                            self._account_currency() or "account currency")
                r, ret = None, tr.side * (exit_px - entry) / entry
        else:                                          # the engine's own fills: pnl in USD, converted for the record
            if px is None:
                tick = self.broker.last_tick(self.cfg.symbol)
                px = tick.bid if tr.side > 0 else tick.ask
            legs = [(tr.lots, px)] + ([(tr.scaled_lots, tr.scaled_price)] if tr.scaled_price is not None else [])
            done = sum(n for n, _ in legs)
            exit_px = sum(n * p for n, p in legs) / done if done > 0 else px
            pnl_usd = sum(tr.side * (p - entry) * n * contract for n, p in legs)
            r = pnl_usd / risk_usd if risk_usd > 0 else None
            ret = pnl_usd / notional_usd
            if fx is None:                             # never read as factor 1: recorded in USD and flagged (LOW-1)
                log.warning("position %s: no USD -> account currency conversion; P&L recorded in USD", pid)
                pnl, pnl_currency = pnl_usd, "USD"
            else:
                pnl = pnl_usd * fx
            exit_utc, commission, swap = at, None, None
        equity = tr.equity_before
        if equity is None or equity <= 0:
            # the balance before this trade's P&L was realised (a USD-flagged P&L is not subtracted from a £ balance)
            usd_only = pnl_currency == "USD" and self._account_currency() != "USD"
            equity = self.broker.account().balance - (0.0 if usd_only else pnl)
            if equity <= 0:
                equity = self.state.equity
        rec = ClosedTrade(account_id=self.cfg.account_id, broker=self.cfg.broker_name,
                          mode="live" if self.cfg.mode == "live" else "demo", exit_utc=exit_utc,
                          ret=ret, pnl=pnl, equity_before=equity, position_id=pid,
                          client_order_id=tr.client_order_id, agent_id=tr.agent_id, side=tr.side, lots=lots,
                          entry_utc=tr.opened_utc or tr.entry_bar_ts, entry_price=entry, exit_price=exit_px,
                          exit_reason=reason, r=r, commission=commission, swap=swap, pnl_currency=pnl_currency,
                          partial_lots=tr.scaled_lots,
                          # finance audit: GAP-03 stops, GAP-05 where the money came from and whether it is net
                          initial_sl=tr.initial_sl if tr.risk_known else None, final_sl=tr.sl, final_tp=tr.tp,
                          money_source="broker" if info is not None else "engine",
                          pnl_basis="net" if commission is not None and swap is not None else "gross",
                          **(tr.audit or EntryAudit(timeframe=tr.timeframe)).model_dump(),
                          **self._closed_excursion(pid, tr, entry, tr.opened_utc or tr.entry_bar_ts, exit_utc,
                                                   None if synthetic else px if px is not None else exit_px,
                                                   exit_known=not synthetic))
        append_closed_trade(Path(self.cfg.state_dir), rec)
        level = {"stop": tr.sl, "target": tr.tp}.get(info["reason"]) if info is not None else None
        if info is not None and level is not None:     # GAP-04: a broker stop or target fill against its level
            self._log_exit_fill(pid, tr, info["reason"], info["last_lots"], info["last_price"], at=exit_utc,
                                level=level)
        self._recorded.add(pid)
        self._throttle_closed.append(rec)
        log.info("closed trade recorded: position %s %s, pnl %.2f", pid, reason, pnl)   # the exit itself is journalled

    # ------------------------------------------------------------------ MAE / MFE (BACKLOG 23, reporting only)
    def _track_excursions(self, t: Tick) -> None:
        """Fold this tick into each open position's MAE / MFE (labels/excursion.py): the exit-side price (bid for a long,
        ask for a short). Runs after the tick's per-tick exits (`_stop_check`, `_retry_closes`, the kill switch, the
        scale-out); bar-close and reconcile exits on the same tick follow it, and their fill is added by the record.
        A price at or through the stop or target the broker holds freezes the track (the broker takes the position
        there; nothing after it is folded, the record adds the actual fill). A price through a stop only the engine
        holds while its close is being retried (`_close_due`: the position is still open) is folded as it is, never
        clamped. Reporting only: a failure is logged (once per kind, then counted) and never raised or made a tick
        error, so it can neither block an exit nor an entry; the position's record is then approximate."""
        for pid, tr in self.open.items():
            try:
                entry = tr.open_price if tr.open_price is not None else tr.entry
                if entry is None or (tr.opened_utc is not None and t.ts_utc < tr.opened_utc):
                    continue
                if tr.excursion is None:
                    tr.excursion = Excursion.start(entry, tr.opened_utc or t.ts_utc)
                ex = tr.excursion
                if ex.frozen_utc is not None:
                    continue
                px = t.bid if tr.side > 0 else t.ask
                at_broker_level = (tr.sl is not None and tr.side * (px - tr.sl) <= 0) or \
                    (tr.tp is not None and tr.side * (px - tr.tp) >= 0)
                if at_broker_level and pid not in self._close_due:
                    ex.frozen_utc = t.ts_utc
                    continue
                ex.fold(tr.side, px, t.ts_utc)
                ex.last_utc = t.ts_utc
            except Exception as exc:
                tr.excursion_approx = True
                kind = type(exc).__name__
                n = self._excursion_errors.get(kind, 0)
                self._excursion_errors[kind] = n + 1
                if n == 0:
                    log.warning("MAE/MFE tracking failed (%s); reporting only, trading continues", kind, exc_info=exc)

    def _closed_excursion(self, pid: int, tr: OpenTrade, entry: float, entry_utc: pd.Timestamp,
                          exit_utc: pd.Timestamp, exit_fill: float | None, *, exit_known: bool = True) -> dict[str, Any]:
        """The ClosedTrade MAE / MFE fields: the tick track plus the exit fill, every extreme at or before `exit_utc`.
        The record is approximate, and the stored 1m bars wholly inside the trade on the exit side are folded in, when
        ticks did not see the whole path: `excursion_approx` (a restart, an adopted position), no track at all, the
        position outlived a freeze at its broker level by more than EXCURSION_FREEZE_TOLERANCE_S, or `exit_known` is
        False (no deals and no close fill: the bars stop at the last tick folded and no exit price is folded). A track
        with an extreme after the exit (a manual or broker close seen late) is dropped and rebuilt from the bars up to
        the exit. R is left empty when the initial stop is not the trade's (`risk_known`). With no usable track and
        no bar folded (a close confirmed without deals and never tracked, or a dropped track with no covering bar)
        nothing of the path was observed: the fields stay empty rather than reading 0 R / 0 R (an exit fill alone is
        not an excursion). Never raises: a failure leaves the fields empty (the trade is still recorded)."""
        try:
            track = tr.excursion
            approx = tr.excursion_approx or track is None or not exit_known
            bars_end = exit_utc
            if not exit_known:
                exit_fill = None
                bars_end = track.last_utc if track is not None and track.last_utc is not None else entry_utc
            if track is not None and track.frozen_utc is not None and exit_known and \
                    (exit_utc - track.frozen_utc).total_seconds() > EXCURSION_FREEZE_TOLERANCE_S:
                approx = True
            if track is not None and (track.mae_utc > exit_utc or track.mfe_utc > exit_utc):
                track, approx = None, True
            ex = track.model_copy() if track is not None else Excursion.start(entry, entry_utc)
            fav_col, adv_col = ("bid_high", "bid_low") if tr.side > 0 else ("ask_low", "ask_high")
            b = self.bars_1m
            folded = 0
            if approx and not b.empty and {"ts_utc", fav_col, adv_col} <= set(b.columns):
                ts = pd.to_datetime(b["ts_utc"], utc=True)
                inside = b[(ts >= entry_utc) & (ts + pd.Timedelta(minutes=1) <= bars_end)]
                for at, fav, adv in zip(pd.to_datetime(inside["ts_utc"], utc=True), inside[fav_col], inside[adv_col]):
                    ex.fold(tr.side, float(adv), at, float(fav), at)
                    folded += 1
            if track is None and folded == 0:
                return {}
            if exit_fill is not None:
                ex.fold(tr.side, float(exit_fill), exit_utc)
            out = ex.summary(tr.side, entry, tr.initial_sl if tr.risk_known else None)
            return {**out, "excursion_approx": approx}
        except Exception:
            log.warning("MAE/MFE not recorded for position %s; the trade is recorded without it", pid, exc_info=True)
            return {}

    def _risk_path(self) -> Path:
        return Path(self.cfg.state_dir, f"risk_{self.cfg.account_id}.json")   # not engine_*: the supervisor globs those

    def _save_risk_state(self) -> None:
        st = self.state
        payload = {"last_reset": self._last_reset.isoformat() if self._last_reset is not None else None,
                   "day_start_equity": st.day_start_equity, "week_start_equity": st.week_start_equity,
                   "balance_closed_hwm": st.balance_closed_hwm, "stage": Stage(st.stage).value,
                   "rearm_seen": self._rearm_seen, "weekend_done": self._weekend_done,
                   "halted_at": self._halted_at.isoformat() if self._halted_at is not None else None,
                   "propose_only_until": self._propose_only_until.isoformat() if self._propose_only_until is not None else None,
                   "rearm_refused": self._rearm_refused}
        write_atomic(self._risk_path(), json.dumps(payload))       # durable: halts and stages survive a power cut

    def _load_risk_state(self) -> None:
        path = self._risk_path()
        if not path.exists():
            # first start: an earlier re-arm on the bus is not a re-arm of this engine
            self._rearm_seen = self.center.bus.control().rearm_id if self.center.bus is not None else None
            return
        d = json.loads(path.read_text())          # unreadable -> fail loudly rather than forget a drawdown halt
        st = self.state
        self._last_reset = pd.Timestamp(d["last_reset"]) if d.get("last_reset") else None
        st.day_start_equity = float(d.get("day_start_equity", 0.0))
        st.week_start_equity = float(d.get("week_start_equity", 0.0))
        st.balance_closed_hwm = float(d.get("balance_closed_hwm", 0.0))
        st.stage = Stage(d.get("stage", Stage.NORMAL.value))
        self._rearm_seen = d.get("rearm_seen")
        self._weekend_done = d.get("weekend_done")
        self._halted_at = pd.Timestamp(d["halted_at"]) if d.get("halted_at") else None
        self._propose_only_until = pd.Timestamp(d["propose_only_until"]) if d.get("propose_only_until") else None
        self._rearm_refused = d.get("rearm_refused")

    def _regime(self, X: pd.DataFrame, close_ts: pd.Timestamp) -> Regime:
        """The allocator's inputs at this close. The tier-1 distances (row M9) come from the archived calendar the
        RiskGate's news blackout already reads (`_blackout`, refreshed by `_refresh_account` just before), so the
        allocator's own 30-minute zeroing (either side; stricter than the gate's -15/+30) acts live; without
        `news_blackout` there is no calendar and no zeroing."""
        row = X.iloc[-1]
        adx = float(row.get("h1_adx14", row.get("adx14", 20.0)) or 20.0)
        rv = X["h1_rv_20"] if "h1_rv_20" in X.columns else X.get("rv_20", pd.Series([np.nan]))
        win = rv.dropna().tail(60 * 24)
        # a constant window has no quartiles (qcut yields NaN): middle bucket, as with too little history
        q = int(pd.qcut(win, 4, labels=False, duplicates="drop").iloc[-1]) if len(win) > 40 and win.nunique() >= 2 else 1
        to_t1, since_t1 = tier1_minutes(self._calendar[1], close_ts) if self.cfg.news_blackout else (None, None)
        return Regime(adx_1h=adx, atr_1h_quartile=q, vol_tercile=int(row.get("vol_tercile", 1) or 1),
                      minutes_to_tier1=to_t1, minutes_since_tier1=since_t1)

    def _top_features(self, model: Model, feats: pd.DataFrame) -> list[tuple[str, float]]:
        imp = getattr(model, "importance", None)
        if imp is None:
            return []
        try:
            s = imp().head(3)
            return [(n, float(feats[n].iloc[0]) if n in feats.columns else 0.0) for n in s.index]
        except Exception:
            return []

    def _blackout(self, now: pd.Timestamp) -> dict | None:
        """The tier-1 event whose blackout window contains `now`, else a recent high-relevance unscheduled news shock.
        The calendar is re-read at most every 5 minutes, the news every minute;
        no store or no archived calendar means no blackout can be known, which is logged in the engine state."""
        if self.store is None:
            return None
        self._calendar_events(now)
        hit = blackout_window(self._calendar[1], now, self.cfg.blackout_before_min, self.cfg.blackout_after_min)
        if hit is None and self.cfg.shock_blackout_min > 0:
            if self._news[0] is None or now - self._news[0] > pd.Timedelta(minutes=1):   # shocks need a fast re-read
                self._news = (now, self.store.read("news", start=now - pd.Timedelta(days=1), end=now))
            hit = shock_window(self._news[1], now, self.cfg.shock_blackout_min, self.cfg.shock_min_relevance)
        self._blackout_event = hit
        return hit

    def _calendar_events(self, now: pd.Timestamp) -> pd.DataFrame | None:
        """The archived calendar from a day before `now` to two days after, re-read at most every 5 minutes (needs the
        store); None when that window holds no event at all. The Forex Factory week lists events of many countries on
        every weekday, so an empty window means the archive is missing or behind (e.g. the new week not fetched by the
        Sunday open), not a quiet market."""
        assert self.store is not None
        if self._calendar[0] is None or now - self._calendar[0] > pd.Timedelta(minutes=5):
            ev = self.store.read("calendar_events", start=now - pd.Timedelta(days=1), end=now + pd.Timedelta(days=2))
            self._calendar = (now, ev if not ev.empty else pd.DataFrame(columns=["ts_utc", "tier", "title"]))
        return None if self._calendar[1].empty else self._calendar[1]

    def _holiday_list(self) -> HolidayList | None:
        """The configured holiday list, loaded once (a fix needs a restart); None, logged, when it cannot be read."""
        if not self._holidays[0]:
            hl: HolidayList | None = None
            try:
                hl = load_holidays(self.gate.limits.holidays.file)
            except Exception as exc:
                log.warning("holiday list %s unusable (%s): entries blocked as calendar_unavailable",
                            self.gate.limits.holidays.file, type(exc).__name__)
            self._holidays = (True, hl)
        return self._holidays[1]

    def _entry_windows(self, tick_ts: pd.Timestamp, now: pd.Timestamp, sessions: SessionTable) -> None:
        """BACKLOG 17/18 entry windows into AccountState, each only when it is enabled in the gate's limits (off, its
        flag stays False and RiskGate ignores it anyway). Tier 2 is judged on tick time like the tier-1 blackout; the
        reopen and holiday windows on the clock the session table's market_closed uses. Fails closed: a calendar or
        holiday list that is missing, unreadable, stale or does not cover the next 24 h is `calendar_unavailable`; a
        session table that raises is inside the reopen window; any other error in the computation sets both (review
        L3). Each fault is logged once when it starts (review M1), and its start is published in the engine state for
        health (`check_entry_windows`). Entries only (RiskGate); no exit path reads these flags."""
        st = self.state
        self._window_ends = {}
        try:
            tier2, reopen, thin, why, reopen_fault = self._window_flags(tick_ts, now, sessions)
        except Exception as exc:
            tier2, reopen, thin = False, True, False
            why = reopen_fault = f"window computation failed ({type(exc).__name__}: {exc})"
        if why is not None and self._unavailable_since is None:
            self._unavailable_since = now
            log.warning("entry windows: calendar_unavailable (%s); entries refused until it clears, exits unaffected",
                        why)
        elif why is None and self._unavailable_since is not None:
            log.info("entry windows: calendar known again (unavailable since %s)", self._unavailable_since)
            self._unavailable_since = None
        if reopen_fault is not None and not self._reopen_fault:
            log.warning("entry windows: reopen window unknown (%s); entries refused as reopen_window", reopen_fault)
        self._reopen_fault = reopen_fault is not None
        self._unavailable_why = why
        st.tier2_blackout, st.reopen_window, st.holiday_thin = tier2, reopen, thin
        st.calendar_unavailable = why is not None

    def _window_flags(self, tick_ts: pd.Timestamp, now: pd.Timestamp, sessions: SessionTable
                      ) -> tuple[bool, bool, bool, str | None, str | None]:
        """(tier2_blackout, reopen_window, holiday_thin, why the calendar is unavailable or None, why the reopen window
        is unknown or None) for the enabled windows."""
        L = self.gate.limits
        tier2 = reopen = thin = False
        why: str | None = None
        reopen_fault: str | None = None
        if L.tier2.enabled:
            ev: pd.DataFrame | None = None
            try:
                ev = self._calendar_events(tick_ts) if self.store is not None else None
                why = None if self.store is not None else "no data store"
            except Exception as exc:
                why = f"calendar unreadable ({type(exc).__name__}: {exc})"
            if ev is not None:
                self._calendar_seen = (pd.Timestamp(pd.to_datetime(ev["ts_utc"], utc=True).max()),
                                       pd.Timestamp(pd.to_datetime(ev["received_utc"], utc=True).max())
                                       if "received_utc" in ev.columns else None)
                hit = blackout_window(ev, tick_ts, L.tier2.before_min, L.tier2.after_min, tier=2)
                tier2 = hit is not None
                if hit is not None:
                    self._window_end("tier2_until",
                                     lambda: pd.Timestamp(hit["ts_utc"]) + pd.Timedelta(minutes=L.tier2.after_min))
            why = why or calendar_gap(ev, tick_ts)
        if L.reopen.enabled:
            try:
                reopen = reopen_window(sessions, now, L.reopen)
            except Exception as exc:
                reopen, reopen_fault = True, f"session table unusable ({type(exc).__name__}: {exc})"
            if reopen and reopen_fault is None:
                self._window_end("reopen_until", lambda: reopen_window_end(sessions, now, L.reopen))
        if L.holidays.enabled:
            hl = self._holiday_list()
            day = server_date(sessions, now)
            if hl is None:
                why = why or f"holiday list {L.holidays.file} unusable"
            elif not hl.covers(day):
                why = why or f"holiday list {hl.version} does not cover {day} (valid {hl.valid_from}..{hl.valid_through})"
            else:
                thin = hl.thin(day) is not None
                if thin:
                    self._window_end("holiday", lambda: holiday_end(hl, sessions, now))
        return tier2, reopen, thin, why, reopen_fault

    def _window_end(self, key: str, compute: Callable[[], Any]) -> None:
        """When a window the gate is refusing on ends, for the state file and the owner's displays only: an error here
        never changes a flag (it only leaves the time unknown). Logged once per window when failures start and once
        when it is computed again (as review M1 for calendar_unavailable), never on every tick."""
        try:
            self._window_ends[key] = compute()
        except Exception as exc:
            if key not in self._window_end_failing:
                self._window_end_failing.add(key)
                log.warning("entry windows: %s not computed (%s: %s); shown without a time until it is",
                            key, type(exc).__name__, exc)
            return
        if key in self._window_end_failing:
            self._window_end_failing.discard(key)
            log.info("entry windows: %s computed again", key)

    def _windows_state(self) -> dict:
        """The entry windows for engine_<account>.json and health (`check_entry_windows`, review M1)."""
        L, st = self.gate.limits, self.state
        hl = self._holidays[1] if L.holidays.enabled else None
        latest, received = self._calendar_seen

        def iso(x: pd.Timestamp | None) -> str | None:
            return None if x is None or pd.isna(x) else pd.Timestamp(x).isoformat()
        ends = self._window_ends
        holiday = ends.get("holiday")
        return {"enabled": {"tier2": L.tier2.enabled, "reopen": L.reopen.enabled, "holidays": L.holidays.enabled},
                "tier2_blackout": st.tier2_blackout, "reopen_window": st.reopen_window,
                "holiday_thin": st.holiday_thin, "calendar_unavailable": st.calendar_unavailable,
                "calendar_unavailable_since": iso(self._unavailable_since),
                "calendar_unavailable_reason": self._unavailable_why,
                "holiday_version": hl.version if hl is not None else None,
                "holiday_valid_through": hl.valid_through.isoformat() if hl is not None else None,
                "calendar_latest_ts_utc": iso(latest), "calendar_received_utc": iso(received),
                # when each window ends (display only): the owner's screens show the running gate's own times
                "tier2_until": iso(ends.get("tier2_until")) if st.tier2_blackout else None,
                "reopen_until": iso(ends.get("reopen_until")) if st.reopen_window else None,
                "holiday_name": holiday[0] if holiday and st.holiday_thin else None,
                "holiday_until": iso(holiday[1]) if holiday and st.holiday_thin else None}

    def _busy(self, agent_id: str) -> bool:
        return any(t.agent_id == agent_id for t in self.open.values()) or \
            any(a.agent_id == agent_id for _, _, a in self.pending.values())

    def _magic(self, family: str) -> int:
        """Per-family magic number; crc32 is stable across processes (str hash() is salted per process), so a
        restarted engine reconciles its own positions to the same family."""
        return self.cfg.magic_base + zlib.crc32(family.encode()) % 100

    def _base_bars(self, agent: Specialist) -> int:
        """The agent's time barrier in base bars (position management runs on the decision_tf clock). The labels
        (triple_barrier) exit at the close of the (max_bars + 1)-th bar after the signal bar, so the live trade is
        held for max_bars + 1 bars of the agent's timeframe, as the models were trained."""
        return int((agent.label_spec.max_bars + 1) * tf_seconds(agent.timeframe) // tf_seconds(self.cfg.decision_tf))

    def _proposal_id(self, agent_id: str, close_ts: pd.Timestamp) -> str:
        h = hashlib.sha1(f"{self.cfg.account_id}|{agent_id}|{close_ts.isoformat()}".encode()).hexdigest()[:10]
        return f"{self.cfg.account_id}-{int(close_ts.timestamp())}-{h}"

    def _record(self, agent: Specialist, ts: pd.Timestamp, p: float, mult: float, action: str, pid: str | None = None,
                scored_fv: str | None = None) -> dict:
        d = {"ts": ts.isoformat(), "agent": agent.agent_id, "p": round(p, 4), "mult": round(mult, 3), "action": action, "proposal": pid}
        if scored_fv is not None:
            d["scored_feature_version"] = scored_fv      # the frame version the model scored (legacy rebuilds included)
        self.decisions.append(d)
        return d

    # ------------------------------------------------------------------ shadow book
    def set_shadow_models(self, models: dict[str, tuple[str, Model]]) -> None:
        """Replace the shadow set (version -> (agent_id, model)) on registry reload; versions no longer listed stop
        opening trades. Each version paper-trades with its own agent's specialist, so the record is per agent."""
        self.shadow_models = dict(models)
        if self.shadow is not None:
            now = self.last_bar_close or pd.Timestamp.now("UTC")
            for version in models:
                self.shadow.track(version, now)

    def _shadow_step(self, tf: str, fr: _Frame, agents: list[Specialist],
                     cands_by_agent: dict[str, pd.DataFrame], atr_usd: float, cost_atr: float) -> None:
        assert self.shadow is not None
        dec = fr.dec
        last = len(dec) - 1
        bar = dec.iloc[last]
        self.shadow.on_bar(bar, tf)       # advance open shadow trades of this timeframe; a new entry starts after this bar
        for version, (agent_key, model) in self.shadow_models.items():
            for agent in agents:
                cands = cands_by_agent[agent.agent_id]
                if agent_key not in (agent.agent_id, agent.family) or cands.empty or int(cands["idx"].iloc[-1]) != last:
                    continue
                side = int(cands["side"].iloc[-1])
                Xm = self._scoring_frame(fr, model)
                if Xm is None:
                    continue          # a model only scores the frame version it was trained on, in shadow too
                feats = Xm.drop(columns=["ts_utc"]).iloc[[last]].replace([np.inf, -np.inf], np.nan)
                feats["side"] = side
                cols = [c for c in model.feature_names if c in feats.columns] if model.feature_names else list(feats.columns)
                p, p_raw = _score(model, feats[cols])
                ls = agent.label_spec
                threshold = breakeven_prob(ls.target_atr, ls.stop_atr, cost_atr) + 0.02
                # every candidate is recorded (P9 counterfactual shadow): the ones below the threshold too, flagged
                # not taken, so a recalibration sees an unbiased sample; only taken ones count as shadow trades
                self.shadow.open_trade(version=version, agent_id=agent.agent_id, side=side, bar_ts=pd.Timestamp(bar["ts_utc"]),
                                       entry=float(bar["ask_close"] if side > 0 else bar["bid_close"]), atr_usd=atr_usd,
                                       target_atr=ls.target_atr, stop_atr=ls.stop_atr, max_bars=ls.max_bars, p=p, timeframe=tf,
                                       threshold=threshold, taken=p > threshold, p_raw=p_raw, policy=agent.exit_spec,
                                       signal_close=pd.Timestamp(bar["visible_at"]),
                                       scored_feature_version=str(Xm.attrs.get("feature_version", "")))

    def flush_journal(self) -> None:
        """Append new decisions (proposals, gate blocks, below-threshold scores, orders, exits, orphans) to the
        store's `decisions` table: the trade journal the agents and reviews read. One part file per flush."""
        if self.store is None or self._journaled >= len(self.decisions):
            return
        rows = []
        for d in self.decisions[self._journaled:]:
            ts = d.get("ts")
            if isinstance(ts, (int, float)):
                ts_utc = pd.Timestamp(ts, unit="s", tz="UTC")
            else:
                ts_utc = pd.Timestamp(ts) if ts is not None else pd.Timestamp.now("UTC")
            extra = {k: v for k, v in d.items() if k not in ("ts", "agent", "action", "p", "mult", "proposal")}
            rows.append({"ts_utc": ts_utc, "account_id": self.cfg.account_id, "agent_id": d.get("agent"),
                         "action": str(d.get("action")), "p": d.get("p"), "mult": d.get("mult"),
                         "proposal_id": d.get("proposal"), "detail": json.dumps(extra, default=str)})
        df = pd.DataFrame(rows)
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
        self.store.append("decisions", df, source=self.cfg.account_id, symbol=self.cfg.symbol, dedupe=False)
        self._journaled = len(self.decisions)

    def _log_tick(self, t: Tick) -> None:
        """Buffer the tick for the store and flush every tick_flush_s. A failed write (a full disk) raises once, then is
        not retried for tick_flush_s; meanwhile the buffer keeps the newest TICK_LOG_MAX ticks (older ones dropped and
        counted), so a broken disk can neither grow memory nor fail every tick."""
        if self.store is None:
            return
        self._tick_log.append(t)
        if len(self._tick_log) > TICK_LOG_MAX:
            drop = len(self._tick_log) - TICK_LOG_MAX
            self._tick_log = self._tick_log[drop:]
            self.ticks_dropped += drop
        if self._tick_flush_retry is not None and t.ts_utc < self._tick_flush_retry:
            return
        if (t.ts_utc - self._tick_log[0].ts_utc).total_seconds() >= self.cfg.tick_flush_s:
            try:
                self.flush_ticks()
                self._tick_flush_retry = None
            except Exception:
                self._tick_flush_retry = t.ts_utc + pd.Timedelta(seconds=max(self.cfg.tick_flush_s, 1))
                raise

    def flush_ticks(self) -> None:
        """Write buffered ticks as one new part file (append-only; the nightly cost job reads them)."""
        if self.store is None or not self._tick_log:
            return
        df = pd.DataFrame({"ts_utc": [x.ts_utc for x in self._tick_log], "bid": [x.bid for x in self._tick_log],
                           "ask": [x.ask for x in self._tick_log]})
        self.store.append("ticks", df, source=self.cfg.account_id, symbol=self.cfg.symbol, dedupe=False)
        self._tick_log = []

    def _refresh_broker_terms(self, now: pd.Timestamp) -> None:
        """Swap and commission as the terminal reports them -> state/broker_terms_<account>.json (nightly_costs puts
        them in the cost table). Only brokers that measure them (MT5); a failure is logged and never stops trading."""
        measure = getattr(self.broker, "broker_terms", None)
        if measure is None:
            return
        last = self._last_terms
        if last is not None and (now - last).total_seconds() < self.cfg.broker_terms_every_s:
            return
        self._last_terms = now
        try:
            terms = measure(self.cfg.account_id, now - pd.Timedelta(days=self.cfg.commission_window_days), now)
            terms.save(Path(self.cfg.state_dir, f"broker_terms_{self.cfg.account_id}.json"))
        except Exception:
            log.exception("broker terms not refreshed for %s; the last reading stays", self.cfg.account_id)

    def _cost_atr(self, atr_usd: float, ts: pd.Timestamp, ex_spread: bool = False) -> float:
        """Round-trip cost in ATR for the current session from the nightly cost table; config fallback without one.
        ex_spread: slippage and commission only, for the probability threshold and size multiplier (the model's p is
        trained on labels that already pay the spread); the fallback stays the full configured cost (conservative)."""
        path = Path(self.cfg.state_dir, f"costs_{self.cfg.account_id}.json")
        mtime = path.stat().st_mtime if path.exists() else -1.0
        if mtime != self._cost_cache[0]:
            try:
                self._cost_cache = (mtime, CostTable.load(path))
            except (ValueError, OSError):
                self._cost_cache = (mtime, None)   # a bad file must not stop trading decisions; fall back
        table = self._cost_cache[1]
        if table is None:
            return self.cfg.cost_atr
        session = str(DEFAULT_SESSIONS.session_label(pd.DatetimeIndex([ts]))[0])
        c = table.round_trip_ex_spread_atr(session, atr_usd) if ex_spread else table.round_trip_atr(session, atr_usd)
        return c if c is not None else self.cfg.cost_atr

    def _drift(self) -> dict:
        """state/drift.json from the daily drift_watch job (cached by mtime): halted agents, size factors and the system
        halt. Missing (the job has not run yet) restricts nothing; unreadable halts entries (fail closed)."""
        path = Path(self.cfg.state_dir, "drift.json")
        mtime = path.stat().st_mtime if path.exists() else -1.0
        if mtime != self._drift_cache[0]:
            try:
                d = json.loads(path.read_text()) if path.exists() else {}
            except (ValueError, OSError):
                d = {"system_halt": {"reasons": ["drift.json unreadable"]}}
            self._drift_cache = (mtime, d if isinstance(d, dict) else {"system_halt": {"reasons": ["drift.json invalid"]}})
        return self._drift_cache[1]

    def _account_class(self) -> str:
        """The classifier's stored class (state/classifier_<account>.json); unknown when missing or unreadable."""
        path = Path(self.cfg.state_dir, f"classifier_{self.cfg.account_id}.json")
        mtime = path.stat().st_mtime if path.exists() else -1.0
        if mtime != self._class_cache[0]:
            try:
                c = str(json.loads(path.read_text()).get("class") or "unknown") if path.exists() else "unknown"
            except (ValueError, OSError):
                c = "unknown"
            self._class_cache = (mtime, c if c in ("raw", "standard", "unknown") else "unknown")
        return self._class_cache[1]

    def _daily_allowance(self) -> dict[str, float | None]:
        """min(daily cap x day-start equity, risk.daily_loss_limit_abs) and that limit less today's loss so far, for the
        engine state and health; None while unknown (no day start yet and no absolute limit)."""
        st = self.state
        limit = self.gate.daily_loss_limit(st)
        if not math.isfinite(limit):
            return {"daily_loss_limit": None, "daily_loss_remaining": None}
        lost = max(0.0, st.day_start_equity - st.equity) if st.day_start_equity > 0 else 0.0
        return {"daily_loss_limit": round(limit, 2), "daily_loss_remaining": round(max(0.0, limit - lost), 2)}

    def _agent_throttle(self, agent_id: str, timeframe: str, now: pd.Timestamp) -> AgentThrottle | None:
        """The agent's throttle facts at `now` (BACKLOG 21), derived from durable records only (goldbot/risk/throttle.py):
        this account's closed-trade record, the open book and the queued closes (one entry per position), and the shadow
        book while a streak is at the threshold and cooling down. None while the throttle is off, so a disabled throttle
        reads nothing.

        A close seen but not yet in the record (queued in `_closes`: a broker stop found by the bar-close sweep is
        written after the decisions, or a write being retried; or given up as lost) counts as a provisional full loss at
        its close time until the real record replaces it (trading-safety review HIGH-1), so the loss just taken counts at
        the very next decision and a failing write fails closed. Records lost in an earlier process are not known after
        a restart (only their ids persist); health already fails on them (`closed_records_lost`).

        `now` is the decision's clock: the bar close in `_decide`, the approval tick in `_on_decision`, the last tick
        (else the host clock) in the state file. An error refuses this agent only (`throttle_unknown`, review LOW-1)."""
        cfg = self.gate.limits.throttle
        if not cfg.enabled:
            return None
        try:
            closed = list(self._throttle_closed)
            entries: dict[object, pd.Timestamp] = {}
            for t in closed:
                if t.agent_id == agent_id and t.entry_utc is not None:
                    entries[t.position_id if t.position_id is not None else id(t)] = pd.Timestamp(t.entry_utc)
            unrecorded = [c for c in [*self._closes, *self._throttle_lost]
                          if c.trade.agent_id == agent_id and c.position_id not in self._recorded]
            for tr in [*self.open.values(), *(c.trade for c in unrecorded)]:
                if tr.agent_id == agent_id:
                    entries[tr.position_id] = pd.Timestamp(tr.opened_utc if tr.opened_utc is not None else tr.entry_bar_ts)
            mode: Literal["demo", "live"] = "live" if self.cfg.mode == "live" else "demo"
            closed += [ClosedTrade(account_id=self.cfg.account_id, broker=self.cfg.broker_name, mode=mode, exit_utc=c.at,
                                   ret=0.0, pnl=0.0, equity_before=1.0, position_id=c.position_id, agent_id=agent_id,
                                   entry_utc=c.trade.opened_utc, exit_reason="provisional", r=-1.0)   # a full loss
                       for c in unrecorded]
            return agent_throttle(agent_id, timeframe, closed=closed, entry_times=entries.values(), now=now,
                                  shadow_win=lambda: self._shadow_win(agent_id), consult_shadow_at=cfg.loss_streak,
                                  cooldown=self.gate.throttle_cooldown(timeframe), provisional_losses=len(unrecorded))
        except Exception:
            log.exception("throttle facts for %s could not be derived; its entries are refused", agent_id)
            return AgentThrottle(agent_id=agent_id, timeframe=timeframe, unknown=True)

    def _shadow_win(self, agent_id: str) -> ShadowTrade | None:
        """The agent's latest qualifying shadow win on its current live model version (`live_versions`; none known, no
        win): this engine's book when it hosts it, else the host's saved file, re-read only when its mtime changes
        (review MEDIUM-2). An unreadable book clears nothing (fail closed: the streak refuses until its cooldown)."""
        version = self.live_versions.get(agent_id)
        if version is None:
            return None
        try:
            book = self.shadow
            if book is None:
                path = Path(self.cfg.state_dir, "shadow_book.json")
                if not path.exists():
                    return None
                mtime = path.stat().st_mtime_ns
                if self._shadow_cache[0] != mtime or self._shadow_cache[1] is None:
                    self._shadow_cache = (mtime, ShadowBook(self.cfg.state_dir))
                book = self._shadow_cache[1]
            assert book is not None
            return latest_shadow_win(book, agent_id, version)
        except Exception as exc:
            log.warning("throttle: shadow book unreadable (%s); no shadow win counted", type(exc).__name__)
            return None

    def _throttle_state(self, now: pd.Timestamp) -> dict[str, Any]:
        """`throttle` in engine_<account>.json and health: the settings, and when on, each agent's facts, when its
        cooldown ends and the reasons RiskGate would refuse its entry with now (`blocked`). `now` is the last tick's time,
        else the host clock (before the first tick). A failure publishes `error` (health fails), never stops the write."""
        cfg = self.gate.limits.throttle
        out: dict[str, Any] = cfg.model_dump()
        if not cfg.enabled:
            return out
        try:
            agents: dict[str, Any] = {}
            for agent in self.agents.values():
                t = self._agent_throttle(agent.agent_id, agent.timeframe, now)
                if t is None:
                    continue
                until = self.gate.throttle_cooldown_until(t)
                agents[agent.agent_id] = {**t.model_dump(mode="json", exclude={"agent_id"}),
                                          "cooldown_until": until.isoformat() if until is not None else None,
                                          "blocked": self.gate.throttle_reasons(t, now)}
            out["agents"] = agents
        except Exception as exc:
            log.exception("throttle state not published")
            out["error"] = type(exc).__name__
        return out

    def _write_state(self) -> None:
        st = self.state
        payload = {
            "account": self.cfg.account_id, "broker": self.cfg.broker_name, "mode": self.cfg.mode, "ts": time.time(),
            # the deposit currency: the supervisor refuses to combine engines in different currencies (BACKLOG 28, A8)
            "currency": self._account_currency() or None,
            # BACKLOG 29: the effective daily loss limit (account currency) and what is left of it today
            **self._daily_allowance(),
            # this account's absolute limit (accounts.yaml): the supervisor applies the smallest published one
            "daily_loss_limit_abs": self.gate.limits.daily_loss_limit_abs,
            "equity": st.equity, "day_start_equity": st.day_start_equity, "week_start_equity": st.week_start_equity,
            "balance_closed_hwm": st.balance_closed_hwm, "stage": st.stage.value if isinstance(st.stage, Stage) else str(st.stage),
            "open_positions": st.open_positions, "open_lots": st.open_lots, "open_notional": st.open_notional,
            # BACKLOG 19/35: remaining stop risk (account currency; null = unknown), per position, and positions by side
            "open_risk": round(st.open_risk, 2) if st.open_risk is not None else None,
            "open_risk_positions": self._open_risk_rows, "open_long": st.open_long, "open_short": st.open_short,
            "combined_size_down": st.combined_size_down, "spread_points": st.spread_points, "last_tick_age_s": st.last_tick_age_s,
            "terminal_connected": True, "account_class": self._account_class(), "pending": len(self.center.pending),
            "approval_mode": self.cfg.approval_mode, "blackout": self._blackout_event,
            "dq_error": st.dq_error, "stale_bars": st.stale_bars, "data_recovering": st.data_recovering,
            "dq_checks": sorted({e.check for e in self._dq_errors()} | self._dq_blocking()
                                | ({"positions_unreadable"} if self._book_unreadable() else set())),
            # consecutive failed broker reads (health alerts after broker.positions_unreadable_alert in a row)
            "positions_unreadable": self._book_unreadable(),
            "dq_warnings": [f"{e.check}: {e.detail}" for e in self._dq_warnings
                            + [e for e in self._dq_bar_events + self._dq_pending if e.severity != "error"]],
            "late_ticks": self._late_ticks, "clock_skew_s": self.clock_skew_s(), "bar_close_grace_s": BAR_CLOSE_GRACE_S,
            "closed_records_lost": self._records_lost, "closes_due": sorted(self._close_due),
            "foreign_positions": self._foreign, "rearm_refused": self._rearm_refused,
            "propose_only_until": self._propose_only_until.isoformat() if self._propose_only_until is not None else None,
            # the dashboard's account view (goldbot/ops/account_view.py): the broker's balance, currency and margin,
            # the gate's limits, the price, lot terms and ATR(14) per ladder timeframe; no login or server
            **self._account_view, "price": self._price, "ladder_atr": dict(self._ladder["ladder_atr"]),
            "lot_terms": self._ladder["lot_terms"], "limits": self._published_limits(),
            "tick_ahead_s": st.tick_ahead_s, "market_closed": st.market_closed, "session_source": self.sessions.source,
            "tick_error": st.tick_error, "tick_error_detail": self._tick_error_detail,
            # host clock; None while quotes are valid (health FAILs after QUOTE_INVALID_FAIL_S of invalid quotes)
            "quote_invalid_since": self._quote_invalid_since.isoformat() if self._quote_invalid_since is not None else None,
            "tick_errors_total": self.tick_errors_total, "ticks_dropped": self.ticks_dropped,
            "last_tick_error_at": self._last_tick_error_at.isoformat() if self._last_tick_error_at is not None else None,
            "sessions": session_table_cache(self.sessions),
            # BACKLOG 21: per-agent loss-streak and entry-rate throttle (settings; per agent when on), on the tick clock
            "windows": self._windows_state(),        # BACKLOG 17/18 entry windows (health: check_entry_windows)
            "throttle": self._throttle_state(self._last_tick.ts_utc if self._last_tick is not None else self.clock()),
            "server_clock": self.clock_probe.model_dump(mode="json") if self.clock_probe is not None else None,
            "bridge_compat": self.bridge_compat.model_dump(mode="json") if self.bridge_compat is not None else None,
            # ADR 0010: state from a newer release read by this process, bounded (health: check_schema_skew)
            "schema_skew": schema_skew_summary().model_dump(mode="json"),
        }
        # one `currency` key: the broker's deposit currency the gate sized with, read once (BACKLOG 28); the account
        # view's copy (None until the first refresh) must not blank it for the supervisor's currency check
        payload["currency"] = self._account_currency() or self._account_view.get("currency") or None
        # the supervisor never reads a half-written file; a heartbeat, rewritten constantly, needs no fsync
        write_atomic(Path(self.cfg.state_dir, f"engine_{self.cfg.account_id}.json"), json.dumps(payload), durable=False)
        self._save_risk_state()
        self._save_orders()
