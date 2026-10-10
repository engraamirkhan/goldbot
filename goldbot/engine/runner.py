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
import time
import zlib
from collections import deque
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.allocator import Regime, RuleAllocator, tier1_minutes
from goldbot.base import Record, UtcTimestamp, write_atomic
from goldbot.config import tf_seconds
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.econ_calendar import blackout_window
from goldbot.data.news import shock_window
from goldbot.data.quality import DQEvent, bar_errors, events_frame, stale_feed
from goldbot.data.resample import BAR_COLUMNS, IncrementalResampler, mid, resample_bars, ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.timeutil import epoch_ns, feature_day, floor_tf
from goldbot.engine.shadow import ShadowBook
from goldbot.execution.broker import Broker, OrderIntent, OrderResult, Position, SymbolInfo, Tick
from goldbot.execution.costs import CostTable
from goldbot.features import build_features
from goldbot.features.mtf import TF_LABEL, context_tfs, merge_higher_tf
from goldbot.features.technical import atr
from goldbot.labels.exit_policy import ExitPolicy
from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade, load_closed_trades
from goldbot.research.metrics import breakeven_prob, size_multiplier
from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.gate import REARM_PHRASE, Stage, broker_margin, new_day
from goldbot.risk.supervisor import Supervisor
from goldbot.specialists.base import Specialist
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal

log = logging.getLogger("goldbot.engine")
# MT5 trade retcodes that mean the market will not take an order now (10017 TRADE_DISABLED, 10018 MARKET_CLOSED): a
# close refused with one of these is backed off; any other failure (requote 10004/10021, price changed 10020, a
# raising call) is retried on the next tick, so an exit is never delayed by a transient refusal
MARKET_CLOSED_RETCODES = frozenset({10017, 10018})
# design (D10): a bar close is detected by clock, close time plus this grace, not by the next bar's first tick
BAR_CLOSE_GRACE_S = 1.5
LATE_TICK_WARN_S = 60.0      # at most one late_tick data-quality warning per this many seconds (every one is counted)
SKEW_WINDOW = 120            # new ticks in the rolling median of broker-vs-wall clock skew


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


class Engine:
    def __init__(self, cfg: EngineConfig, broker: Broker, agents: list[Specialist], models: dict[str, Model],
                 center: ApprovalCenter | None = None, allocator: RuleAllocator | None = None,
                 limits: RiskLimits | None = None, shadow_models: dict[str, tuple[str, Model]] | None = None,
                 live_shares: dict[str, float] | None = None):
        self.cfg = cfg
        self.broker = broker
        self.agents = {a.agent_id: a for a in agents}
        self.models = models                 # agent_id (or family, for a single-agent setup) -> model
        # population: agent_id -> share of its family's capital; None = every agent trades at the family weight
        self.live_shares = live_shares
        self.center = center or ApprovalCenter({cfg.owner_user_id})
        # design (News blackout, row M9): the allocator zeroes weights on the same window the RiskGate blocks entries on
        self.allocator = allocator or RuleAllocator(cfg.blackout_before_min, cfg.blackout_after_min)
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
        self._dq_bar_errors: list[DQEvent] = []
        # late ticks (stamped before a bar the clock already finalised): dropped from the live bars but still logged,
        # so the nightly store has them; counted, and warned at most every LATE_TICK_WARN_S (wall clock)
        self._late_ticks = 0
        self._late_since_warn = 0
        self._late_warned_at: pd.Timestamp | None = None
        # broker tick time minus the wall clock, seconds, over the last SKEW_WINDOW new ticks (median published)
        self._skew: deque[float] = deque(maxlen=SKEW_WINDOW)
        self._unhealthy_at: pd.Timestamp | None = None    # last time the data was seen stale or in error
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
        self._closes: list[PendingClose] = []
        self._records_lost: list[int] = []           # records given up after closed_record_retries failed writes
        self._unconfirmed: dict[int, int] = {}
        # position -> (consecutive market-closed refusals, next attempt): closes back off only when the market is closed
        self._close_backoff: dict[int, tuple[int, pd.Timestamp]] = {}
        # closes the engine decided that have not succeeded yet: position -> (action, journal extras, last attempt);
        # retried every tick (kill switch, weekend, time exit, hard flat, trail, blackout, engine stop) until done
        self._close_due: dict[int, tuple[str, dict, pd.Timestamp | None]] = {}
        self._kill_pending = False                   # the kill switch has not seen this engine's book flat yet
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
                p = Proposal.model_validate_json(f.read_text())
            except (ValueError, OSError):
                continue
            if p.expired and p.proposal_id not in self.pending:
                bus.archive(p)

    # ------------------------------------------------------------------ ticks and bars
    def on_tick(self, t: Tick) -> list[dict]:
        """Feed a tick; returns any decisions made at a bar close."""
        self.center.poll_bus()          # approvals from the dashboard / Telegram service, applied within a tick
        self._poll_rearm(self._now(t))
        if not self._tick_ok(t):
            return []                   # crossed or out-of-order quote: never enters the bars; entries blocked
        prev, self._last_tick = self._last_tick, t
        # the run loop polls the terminal's latest quote several times a second, so a quiet market repeats the same
        # tick: a repeat only advances the clock (bar close by clock, exits), it is neither a new bar tick nor logged
        repeat = prev is not None and (prev.ts_utc, prev.bid, prev.ask) == (t.ts_utc, t.bid, t.ask)
        if not repeat:
            self._skew.append((t.ts_utc - self._now(t)).total_seconds())
            if self._closed_through is None or t.ts_utc >= self._closed_through:
                self.ticks.append(t)
            else:
                self._late_tick(t)      # never revises a bar the clock already finalised
            self._log_tick(t)
        self._refresh_broker_terms(t.ts_utc)
        self._roll_risk_period(t.ts_utc)
        if hasattr(self.broker, "on_tick"):
            self.broker.on_tick(t)  # paper broker fills
        self._stop_check(t)
        self._retry_closes(t)              # closes decided earlier that have not succeeded: every tick until done
        if self._kill_pending and self.state.stage == Stage.HALTED:
            self._kill_switch(everything=False)   # until this engine's book is flat
        self._scale_out(t)
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
        elif self._last_state_write is None or (t.ts_utc - self._last_state_write).total_seconds() >= self.cfg.state_every_s:
            self._rebuild_bars()            # completed minutes join the bars between closes, so their age is current
            self._refresh_account(t)
            self._write_state()
            self._last_state_write = t.ts_utc
        self.last_bar_close = bar_close
        self._flush_closed()               # after every exit of this tick has been sent
        return out

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
        out = None if len(hb) < 30 else (hb, build_features(mid(hb), ["atr", "trend_strength", "moving_averages", "realised_vol"]))
        self._ctx_cache[tf] = (key, out)
        return out

    def on_bar_close(self, close_ts: pd.Timestamp, last_tick: Tick) -> list[dict]:
        """Runs on every base (decision_tf) bar. Each agent decides on its own timeframe, so a 1h agent is
        evaluated only at closes that complete a 1h bar; position management stays on the base clock."""
        self._rebuild_bars()
        self._dq_bar_errors, self._dq_pending = self._dq_pending, []   # this close decides on the bars it just saw
        if self._dq_bar_errors and self.store is not None:
            self.store.append("dq_events", events_frame(self._dq_bar_errors), source=self.cfg.account_id,
                              symbol=self.cfg.symbol, dedupe=False)
        complete = self.bars_1m[self.bars_1m["visible_at"] <= close_ts]
        if len(complete) < 400:
            return []
        base = self._frame(complete, self.cfg.decision_tf, close_ts)
        if base is None:
            return []
        self._last_atr = float(base.atr.iloc[-1]) if np.isfinite(base.atr.iloc[-1]) else self._last_atr
        self._refresh_account(last_tick)
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

    def _decide(self, tf: str, fr: _Frame, agents: list[Specialist], fam_w: dict[str, float], close_ts: pd.Timestamp,
                last_tick: Tick) -> list[dict]:
        dec, m, X, a = fr.dec, fr.m, fr.X, fr.atr
        decisions = []
        last = len(dec) - 1
        cost_atr = self._cost_atr(float(a.iloc[last]), close_ts)            # full round trip: the RiskGate's checks
        hurdle_atr = self._cost_atr(float(a.iloc[last]), close_ts, ex_spread=True)   # p already pays the spread
        cands_by_agent = {agent.agent_id: agent.candidates(m, X) for agent in agents}
        if self.shadow is not None:
            self._shadow_step(tf, dec, X, agents, cands_by_agent, float(a.iloc[last]), hurdle_atr)
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
            mv, fv = getattr(model, "feature_version", ""), X.attrs.get("feature_version")
            if mv and mv != fv:
                # design: a model only ever scores the feature frame it was trained on
                d = self._record(agent, close_ts, 0.0, 0.0, "feature_version_mismatch")
                d.update(p=None, model_feature_version=mv, frame_feature_version=fv)     # not scored
                decisions.append(d)
                continue
            feats = X.drop(columns=["ts_utc"]).iloc[[last]].replace([np.inf, -np.inf], np.nan)
            feats["side"] = side          # side-aligned models (features include `side`) align signed inputs themselves
            cols = model.feature_names or [c for c in feats.columns]
            p = float(model.predict(feats[[c for c in cols if c in feats.columns]] if model.feature_names else feats)[0])
            ls = agent.label_spec
            w = fam_w.get(agent.family, 0.0) * share * float((drift.get("size_factor") or {}).get(agent.agent_id, 1.0))
            mult = float(size_multiplier(np.array([p]), w, ls.target_atr, ls.stop_atr, hurdle_atr)[0])
            price = last_tick.ask if side > 0 else last_tick.bid
            intent = Intent(agent_id=agent.agent_id, side=side, p=p, target_atr=ls.target_atr, stop_atr=ls.stop_atr, atr_usd=float(a.iloc[last]), cost_atr=cost_atr,
                            multiplier=mult if mult > 0 else 0.0, price=price, timeframe=tf, family=agent.family)
            if mult <= 0 or p <= breakeven_prob(ls.target_atr, ls.stop_atr, hurdle_atr) + 0.02:
                decisions.append(self._record(agent, close_ts, p, mult, "below_threshold"))
                continue
            gd = self.gate.check(intent, self.state, margin_required=broker_margin(self.broker, self.cfg.symbol))
            if not gd.allowed:
                decisions.append(self._record(agent, close_ts, p, mult, "gate:" + ",".join(gd.reasons)))
                continue
            pid = self._proposal_id(agent.agent_id, close_ts)
            if pid in self.sent_ids:
                continue
            stop = price - side * gd.stop_distance
            target = price + side * ls.target_atr * float(a.iloc[last])
            prop = Proposal(proposal_id=pid, account_id=self.cfg.account_id, agent_id=agent.agent_id, side=side, lots=gd.lots, entry=price, stop=stop, target=target, p=p,
                            ev_r=p * ls.target_atr - (1 - p) * ls.stop_atr - cost_atr, spread_points=self.state.spread_points,
                            top_features=self._top_features(model, feats), window_s=self.cfg.approval_window_s,
                            risk_usd=round(gd.lots * gd.stop_distance * intent.contract_oz, 2))
            if self.cfg.approval_mode == "auto":
                self._execute(prop, agent, gd.lots, stop, target, requested=price, atr_usd=intent.atr_usd)
                decisions.append(self._record(agent, close_ts, p, mult, "executed:auto", pid))
            else:
                self.pending[pid] = (intent, prop, agent)   # until the owner decides or the window expires
                self.center.propose(prop)
                decisions.append(self._record(agent, close_ts, p, mult, "proposed", pid))
        return decisions

    # ------------------------------------------------------------------ approvals -> orders
    def _on_decision(self, p: Proposal) -> None:
        entry = self.pending.pop(p.proposal_id, None)
        if entry is None:
            return
        intent, prop, agent = entry
        if p.outcome == Outcome.APPROVED:
            tick = self.broker.last_tick(self.cfg.symbol)
            self._refresh_account(tick)
            gd = self.gate.check(intent, self.state,  # re-check at approval time
                                 margin_required=broker_margin(self.broker, self.cfg.symbol))
            if gd.allowed:
                price = tick.ask if prop.side > 0 else tick.bid
                self._execute(prop, agent, gd.lots, price - prop.side * gd.stop_distance,
                              price + prop.side * agent.label_spec.target_atr * intent.atr_usd, requested=price,
                              atr_usd=intent.atr_usd)
            else:
                self.decisions.append({"ts": time.time(), "agent": agent.agent_id, "action": "gate_at_approval:" + ",".join(gd.reasons)})
                p.gate_refusal = list(gd.reasons)
                if self.center.bus is not None:
                    self.center.bus.archive(p)          # the dashboard shows "refused at approval", not "approved"
        self._write_state()

    def _execute(self, prop: Proposal, agent: Specialist, lots: float, stop: float, target: float, *, requested: float,
                 atr_usd: float | None = None) -> None:
        if prop.proposal_id in self.sent_ids:       # sent before (this process or before a restart): never again
            self.decisions.append({"ts": time.time(), "agent": agent.agent_id, "action": "duplicate_suppressed",
                                   "proposal": prop.proposal_id})
            return
        magic = self._magic(agent.family)
        oi = OrderIntent(client_order_id=prop.proposal_id, symbol=self.cfg.symbol, side=prop.side, lots=lots, sl=round(stop, 2), tp=round(target, 2), magic=magic,
                         comment=prop.proposal_id[-31:])
        # pending_orders row persisted BEFORE sending: a crash between send and result is reconciled on restart
        self._orders_record(prop.proposal_id, agent_id=agent.agent_id, side=prop.side, magic=magic, lots=lots,
                            max_bars=self._base_bars(agent), sl=oi.sl, tp=oi.tp, ts=pd.Timestamp.now("UTC"),
                            atr_usd=atr_usd)
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
                           equity_before=self.state.equity if self.state.equity > 0 else None)
            self._attach_policy(tr, agent, entry=fill_px, atr_usd=atr_usd, opened=opened)
            self.open[res.position_id] = tr
        self._save_orders()
        self.decisions.append({"ts": time.time(), "agent": agent.agent_id, "action": "order", "ok": res.ok,
                               "retcode": res.retcode, "price": res.price, "lots": res.filled_lots})
        if self.store is not None and res.ok and res.price is not None:
            # requested vs filled feeds the nightly slippage table
            fill = pd.DataFrame([{"ts_utc": self.broker.last_tick(self.cfg.symbol).ts_utc, "client_order_id": prop.proposal_id,
                                  "agent_id": agent.agent_id, "side": prop.side, "lots": res.filled_lots, "requested": requested,
                                  "filled": res.price, "order_type": "market", "retcode": res.retcode, "commission": np.nan}])
            self.store.append("fills", fill, source=self.cfg.account_id, symbol=self.cfg.symbol, dedupe=False)

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
        changed) or a raising call is retried on the next tick. Automatic: never gated."""
        now = now if now is not None else self._last_tick.ts_utc if self._last_tick is not None else pd.Timestamp.now("UTC")
        tr = self.open.get(pid)
        err: str | None = None
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
            return res
        self._close_due[pid] = (action, dict(extra), now)
        if res.retcode in MARKET_CLOSED_RETCODES:
            fails = self._close_backoff.get(pid, (0, now))[0]
            wait = min(self.cfg.reconcile_every_s * 2 ** fails, self.cfg.modify_backoff_max_s)
            self._close_backoff[pid] = (fails + 1, now + pd.Timedelta(seconds=wait))
        else:
            self._close_backoff.pop(pid, None)            # transient: the next tick tries again
        return res

    def _backing_off(self, pid: int, now: pd.Timestamp) -> bool:
        return pid in self._close_backoff and now < self._close_backoff[pid][1]

    def _retry_closes(self, t: Tick) -> None:
        """Every tick: re-send each close the engine decided that has not succeeded (kill switch, weekend loser, time
        exit, hard flat, trail, blackout, engine stop), unless it is backing off after a market-closed refusal or was
        already tried on this tick. A position the broker no longer lists is left to `_sweep_closed`, which records it
        once its exit deal is seen. An unreadable book skips this tick (the next one retries)."""
        now = t.ts_utc
        due = [pid for pid, (_, _, last) in self._close_due.items() if last != now and not self._backing_off(pid, now)]
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
                   and not self._backing_off(pid, now)]
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
        mv = getattr(model, "feature_version", "")
        if mv and mv != fr.X.attrs.get("feature_version"):
            return                                        # a model only scores the frame version it was trained on
        feats = fr.X.drop(columns=["ts_utc"]).iloc[[len(fr.X) - 1]].replace([np.inf, -np.inf], np.nan)
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
        for p in positions:
            if not self._own(p.magic):
                continue
            tr = self.open.get(p.position_id)
            if tr is None:
                tr = self.open[p.position_id] = self._adopt(p)
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

    def _adopt(self, p: Position) -> OpenTrade:
        """An open position of this engine's magic range that the open table does not list. When this engine sent it
        (the pending_orders row, by position id or client id / comment) the trade keeps what it was sent with: its
        agent, the stop and lots at entry (R is measured against them, never against a stop trailed since) and the
        signal ATR for its exit policy. Otherwise it is an orphan: its agent from the magic number when that is unique,
        and the broker's current stop as its initial stop."""
        rec = next((o for o in self._orders.values() if o.position_id == p.position_id), None) or \
            next((o for o in self._orders.values() if p.comment and p.comment in (o.client_order_id, o.client_order_id[-31:])), None)
        agent = self.agents.get(rec.agent_id) if rec is not None else self._agent_for_magic(p.magic)
        agent_id = rec.agent_id if rec is not None else "orphan" if agent is None else agent.agent_id
        max_bars = rec.max_bars if rec is not None else 48 if agent is None else self._base_bars(agent)
        tr = OpenTrade(position_id=p.position_id, agent_id=agent_id, side=p.side, lots=p.lots,
                       entry_bar_ts=p.open_time_utc, max_bars=max_bars, sl=p.sl, tp=p.tp,
                       client_order_id=rec.client_order_id if rec is not None else p.comment or None,
                       open_price=p.open_price, initial_sl=rec.sl if rec is not None else p.sl,
                       initial_lots=rec.lots if rec is not None else p.lots, opened_utc=p.open_time_utc)
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

    # ------------------------------------------------------------------ helpers
    def _refresh_account(self, tick: Tick) -> None:
        acc = self.broker.account()
        st = self.state
        st.equity = acc.equity
        st.margin_used = acc.margin
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
        st.open_notional = st.open_lots * 100.0 * (tick.bid + tick.ask) / 2
        st.spread_points = (tick.ask - tick.bid) / 0.01
        now = self._now(tick)
        st.last_tick_age_s = max(0.0, (now - tick.ts_utc).total_seconds())
        stale = stale_feed(tick.ts_utc, now, limit_seconds=self.cfg.stale_feed_s)
        st.dq_error = stale or bool(self._dq_errors()) or positions is None \
            or self._book_unreadable() > 0                                    # fail closed: positions_unreadable
        # stale data (design): the last completed bar must be under one decision period old; no bars is stale
        if self.bars_1m.empty:
            st.stale_bars = True
        else:
            seen = pd.Timestamp(self.bars_1m["visible_at"].iloc[-1])
            st.stale_bars = (now - seen).total_seconds() > tf_seconds(self.cfg.decision_tf)
        if st.dq_error or st.stale_bars or st.last_tick_age_s > self.gate.limits.stale_tick_seconds:
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
        srv = now.tz_convert(ZoneInfo(self.cfg.server_tz))
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
        return pd.Timestamp.now("UTC") if self.cfg.live_clock else tick.ts_utc

    # ------------------------------------------------------------------ data quality
    def _dq_errors(self) -> list[DQEvent]:
        """Blocking data-quality events since (and decided at) the last bar close; warnings never block entries."""
        return [e for e in self._dq_bar_errors + self._dq_pending if e.severity == "error"]

    def _late_tick(self, t: Tick) -> None:
        """A tick stamped before a bar close the clock already processed (D10: close plus BAR_CLOSE_GRACE_S): it is
        kept out of the live bars, which is correct, but the tick log (nightly store) still has it, so the two can
        differ. Recorded as a `late_tick` warning (stored with the bar's dq_events), at most one per LATE_TICK_WARN_S,
        every one counted in the engine state. Usually the broker clock lags the wall clock by more than the grace
        (`clock_skew_s`, warned by health)."""
        self._late_ticks += 1
        self._late_since_warn += 1
        now = self._now(t)
        if self._late_warned_at is not None and (now - self._late_warned_at).total_seconds() < LATE_TICK_WARN_S:
            return
        self._dq_pending.append(DQEvent(
            ts_utc=t.ts_utc, check="late_tick", severity="warning",
            detail=f"{self._late_since_warn} late tick(s) since the last warning, latest at {t.ts_utc} after the "
                   f"close through {self._closed_through}: kept out of the live bars, still in the tick log"))
        self._late_warned_at, self._late_since_warn = now, 0

    def clock_skew_s(self) -> float | None:
        """Broker tick time minus the wall clock (s), rolling median over the last SKEW_WINDOW new ticks; None before
        the first. Below -BAR_CLOSE_GRACE_S, bars close by clock before their last ticks arrive (late ticks). Only
        reported (health warns): entries are not blocked for skew alone; the stale-feed and stale-bar rules apply."""
        return round(float(np.median(self._skew)), 3) if self._skew else None

    def _tick_ok(self, t: Tick) -> bool:
        """Tick-level checks: a crossed or non-positive quote, or a tick older than the previous one, is a
        data-quality error (recorded; the tick is dropped)."""
        prev = self._last_tick
        if t.bid <= 0 or t.ask < t.bid:
            ev = DQEvent(ts_utc=t.ts_utc, check="bid_gt_ask", severity="error", detail=f"bid {t.bid} ask {t.ask}")
        elif prev is not None and t.ts_utc < prev.ts_utc:
            ev = DQEvent(ts_utc=t.ts_utc, check="non_monotonic", severity="error",
                         detail=f"tick at {t.ts_utc} after {prev.ts_utc}")
        else:
            return True
        self._dq_pending.append(ev)
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
                       tp: float, ts: pd.Timestamp, atr_usd: float | None = None) -> None:
        """Write the id to the pending_orders table (atomically, on disk) before order_send."""
        self.sent_ids.add(cid)
        self._orders[cid] = SentOrder(client_order_id=cid, ts_utc=ts, agent_id=agent_id, side=side, magic=magic, lots=lots,
                                      max_bars=max_bars, sl=sl, tp=tp, atr_usd=atr_usd)
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
        self._orders = {k: SentOrder.model_validate(v) for k, v in d.get("sent", {}).items()}
        self.sent_ids = set(self._orders)
        self.open = {int(k): OpenTrade.model_validate(v) for k, v in d.get("open", {}).items()}
        self._closes = [PendingClose.model_validate(v) for v in d.get("closing", [])]
        self._records_lost = [int(x) for x in d.get("records_lost", [])]
        try:
            positions = {p.position_id: p for p in self.broker.positions()}
        except Exception as exc:
            # fail closed (the engine does not start), but say why: the health check then shows an unreadable
            # terminal instead of a merely stale engine (trading-safety review)
            write_atomic(Path(self.cfg.state_dir, f"engine_{self.cfg.account_id}.json"), json.dumps({
                "account": self.cfg.account_id, "ts": time.time(), "dq_error": True,
                "dq_checks": ["positions_unreadable"], "stage": "unknown",
                "startup_error": f"{type(exc).__name__}: {exc}"[:300]}), durable=False)
            raise
        for cid, rec in self._orders.items():
            if rec.status != "sending":
                continue
            pos = next((p for p in positions.values() if p.comment == cid[-31:] or p.comment == cid), None)
            try:
                deals = self._read_deals(rec.ts_utc - pd.Timedelta(minutes=5))
            except Exception:
                if pos is None:
                    continue          # unknown yet: stays "sending" (never re-sent) and is looked up at the next start
                deals = pd.DataFrame()
            dealt = not deals.empty and (
                ("client_order_id" in deals.columns and bool((deals["client_order_id"] == cid).any()))
                or ("comment" in deals.columns and bool(deals["comment"].isin([cid, cid[-31:]]).any())))
            if pos is not None:
                rec.status, rec.position_id = "filled", pos.position_id
                tr = self.open[pos.position_id] = OpenTrade(position_id=pos.position_id, agent_id=rec.agent_id,
                                                            side=pos.side, lots=pos.lots, entry_bar_ts=pos.open_time_utc,
                                                            max_bars=rec.max_bars, sl=rec.sl, tp=rec.tp,
                                                            client_order_id=cid, open_price=pos.open_price,
                                                            initial_sl=rec.sl, initial_lots=rec.lots,
                                                            opened_utc=pos.open_time_utc)
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
        # open trades the broker no longer has were closed while the engine was down: recorded once their exit deal
        # is seen (a trade recorded before the restart is skipped by position id)
        self._sweep_closed(set(positions))
        self._save_orders()

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
                       initial_lots=rec.lots, opened_utc=rec.ts_utc)
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
                "pnl": pnl, "commission": None if comm is None else -(comm + fee), "swap": swap,
                "entry_price": float(ent["price"].iloc[0]) if not ent.empty else None,
                "lots": float(ent[vol].sum()) if not ent.empty else None, "reason": reason}, True

    def _record_closed(self, pid: int, tr: OpenTrade, reason: str, px: float | None, at: pd.Timestamp) -> None:
        """One ClosedTrade for a fully closed position (scale-out folded in: see gates_phase.ClosedTrade), appended
        durably to state/closed_trades.jsonl. Money from the broker's deals when it reports them (net P&L with
        commission and swap); otherwise from the engine's own fills at the contract size (commission and swap None)."""
        if pid in self._recorded:
            return
        try:
            info, _ = self._exit_deals(pid, tr)
        except Exception:
            log.exception("deal history unreadable for position %s; recorded from the engine's fills", pid)
            info = None
        known = info or {}
        entry = tr.open_price if tr.open_price is not None else known.get("entry_price") or tr.entry
        if entry is None:
            raise ValueError("entry price unknown")
        lots = float(tr.initial_lots or known.get("lots") or round(tr.lots + tr.scaled_lots, 8))
        contract = 100.0
        if info is not None:
            exit_px, exit_utc, pnl = info["exit_price"], info["exit_utc"], info["pnl"]
            commission, swap = info["commission"], info["swap"]
            if reason == "broker_close" and info["reason"]:
                reason = info["reason"]
        else:
            try:
                contract = float(self.broker.symbol_info(self.cfg.symbol).contract_size) or contract
            except Exception:
                log.warning("symbol info unavailable; position %s recorded at contract size %s", pid, contract)
            if px is None:
                tick = self.broker.last_tick(self.cfg.symbol)
                px = tick.bid if tr.side > 0 else tick.ask
            legs = [(tr.lots, px)] + ([(tr.scaled_lots, tr.scaled_price)] if tr.scaled_price is not None else [])
            done = sum(n for n, _ in legs)
            exit_px = sum(n * p for n, p in legs) / done if done > 0 else px
            pnl = sum(tr.side * (p - entry) * n * contract for n, p in legs)
            exit_utc, commission, swap = at, None, None
        risk = abs(entry - tr.initial_sl) * lots * contract if tr.initial_sl is not None else 0.0
        equity = tr.equity_before
        if equity is None or equity <= 0:
            equity = self.broker.account().balance - pnl       # the balance before this trade's P&L was realised
            if equity <= 0:
                equity = self.state.equity
        rec = ClosedTrade(account_id=self.cfg.account_id, broker=self.cfg.broker_name,
                          mode="live" if self.cfg.mode == "live" else "demo", exit_utc=exit_utc,
                          ret=pnl / (entry * lots * contract), pnl=pnl, equity_before=equity, position_id=pid,
                          client_order_id=tr.client_order_id, agent_id=tr.agent_id, side=tr.side, lots=lots,
                          entry_utc=tr.opened_utc or tr.entry_bar_ts, entry_price=entry, exit_price=exit_px,
                          exit_reason=reason, r=pnl / risk if risk > 0 else None, commission=commission, swap=swap,
                          partial_lots=tr.scaled_lots)
        append_closed_trade(Path(self.cfg.state_dir), rec)
        self._recorded.add(pid)
        log.info("closed trade recorded: position %s %s, pnl %.2f", pid, reason, pnl)   # the exit itself is journalled

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
        allocator's zeroing (the same blackout_before_min/after_min window) acts live; without `news_blackout` there is
        no calendar and no zeroing."""
        row = X.iloc[-1]
        adx = float(row.get("h1_adx14", row.get("adx14", 20.0)) or 20.0)
        rv = X["h1_rv_20"] if "h1_rv_20" in X.columns else X.get("rv_20", pd.Series([np.nan]))
        q = int(pd.qcut(rv.dropna().tail(60 * 24), 4, labels=False, duplicates="drop").iloc[-1]) if rv.notna().sum() > 40 else 1
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
        if self._calendar[0] is None or now - self._calendar[0] > pd.Timedelta(minutes=5):
            ev = self.store.read("calendar_events", start=now - pd.Timedelta(days=1), end=now + pd.Timedelta(days=2))
            self._calendar = (now, ev if not ev.empty else pd.DataFrame(columns=["ts_utc", "tier", "title"]))
        hit = blackout_window(self._calendar[1], now, self.cfg.blackout_before_min, self.cfg.blackout_after_min)
        if hit is None and self.cfg.shock_blackout_min > 0:
            if self._news[0] is None or now - self._news[0] > pd.Timedelta(minutes=1):   # shocks need a fast re-read
                self._news = (now, self.store.read("news", start=now - pd.Timedelta(days=1), end=now))
            hit = shock_window(self._news[1], now, self.cfg.shock_blackout_min, self.cfg.shock_min_relevance)
        self._blackout_event = hit
        return hit

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

    def _record(self, agent: Specialist, ts: pd.Timestamp, p: float, mult: float, action: str, pid: str | None = None) -> dict:
        d = {"ts": ts.isoformat(), "agent": agent.agent_id, "p": round(p, 4), "mult": round(mult, 3), "action": action, "proposal": pid}
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

    def _shadow_step(self, tf: str, dec: pd.DataFrame, X: pd.DataFrame, agents: list[Specialist],
                     cands_by_agent: dict[str, pd.DataFrame], atr_usd: float, cost_atr: float) -> None:
        assert self.shadow is not None
        last = len(dec) - 1
        bar = dec.iloc[last]
        self.shadow.on_bar(bar, tf)       # advance open shadow trades of this timeframe; a new entry starts after this bar
        feats = X.drop(columns=["ts_utc"]).iloc[[last]].replace([np.inf, -np.inf], np.nan)
        for version, (agent_key, model) in self.shadow_models.items():
            for agent in agents:
                cands = cands_by_agent[agent.agent_id]
                if agent_key not in (agent.agent_id, agent.family) or cands.empty or int(cands["idx"].iloc[-1]) != last:
                    continue
                side = int(cands["side"].iloc[-1])
                mv = getattr(model, "feature_version", "")
                if mv and mv != X.attrs.get("feature_version"):
                    continue          # a model only scores the frame version it was trained on, in shadow too
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
                                       signal_close=pd.Timestamp(bar["visible_at"]))

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
        if self.store is None:
            return
        self._tick_log.append(t)
        if (t.ts_utc - self._tick_log[0].ts_utc).total_seconds() >= self.cfg.tick_flush_s:
            self.flush_ticks()

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

    def _write_state(self) -> None:
        st = self.state
        payload = {
            "account": self.cfg.account_id, "broker": self.cfg.broker_name, "mode": self.cfg.mode, "ts": time.time(),
            "equity": st.equity, "day_start_equity": st.day_start_equity, "week_start_equity": st.week_start_equity,
            "balance_closed_hwm": st.balance_closed_hwm, "stage": st.stage.value if isinstance(st.stage, Stage) else str(st.stage),
            "open_positions": st.open_positions, "open_lots": st.open_lots, "open_notional": st.open_notional,
            "combined_size_down": st.combined_size_down, "spread_points": st.spread_points, "last_tick_age_s": st.last_tick_age_s,
            "terminal_connected": True, "account_class": self._account_class(), "pending": len(self.center.pending),
            "approval_mode": self.cfg.approval_mode, "blackout": self._blackout_event,
            "dq_error": st.dq_error, "stale_bars": st.stale_bars, "data_recovering": st.data_recovering,
            "dq_checks": sorted({e.check for e in self._dq_errors()}
                                | ({"positions_unreadable"} if self._book_unreadable() else set())),
            # consecutive failed broker reads (health alerts after broker.positions_unreadable_alert in a row)
            "positions_unreadable": self._book_unreadable(),
            "dq_warnings": [f"{e.check}: {e.detail}" for e in self._dq_warnings
                            + [e for e in self._dq_bar_errors + self._dq_pending if e.severity != "error"]],
            "late_ticks": self._late_ticks, "clock_skew_s": self.clock_skew_s(), "bar_close_grace_s": BAR_CLOSE_GRACE_S,
            "closed_records_lost": self._records_lost, "closes_due": sorted(self._close_due),
            "foreign_positions": self._foreign, "rearm_refused": self._rearm_refused,
            "propose_only_until": self._propose_only_until.isoformat() if self._propose_only_until is not None else None,
        }
        # the supervisor never reads a half-written file; a heartbeat, rewritten constantly, needs no fsync
        write_atomic(Path(self.cfg.state_dir, f"engine_{self.cfg.account_id}.json"), json.dumps(payload), durable=False)
        self._save_risk_state()
        self._save_orders()
