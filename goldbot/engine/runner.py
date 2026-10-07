"""Trading engine: one process per broker account.

Per decision-bar close:
  bars (from ticks) -> features (+ higher-TF context, no lookahead) -> candidates from each live agent
  -> model p -> allocator weight -> bet sizing -> RiskGate -> Proposal (propose mode) or direct order (auto)
  -> broker.place_order with SL/TP attached -> position management (time exit, trailing) -> reconciliation
  -> engine state file for the supervisor and the API.

Everything below is deterministic and testable with PaperBroker and a replayed tick stream. The MT5
adapter plugs into the same `Broker` protocol on the VPS.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import zlib
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.allocator import Regime, RuleAllocator
from goldbot.base import Record, UtcTimestamp
from goldbot.config import tf_seconds
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.econ_calendar import blackout_window
from goldbot.data.news import shock_window
from goldbot.data.quality import DQEvent, check_bars, events_frame, stale_feed
from goldbot.data.resample import BAR_COLUMNS, IncrementalResampler, mid, ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.timeutil import feature_day, floor_tf
from goldbot.engine.shadow import ShadowBook
from goldbot.execution.broker import Broker, OrderIntent, Tick
from goldbot.execution.costs import CostTable
from goldbot.features import build_features
from goldbot.features.mtf import TF_LABEL, context_tfs, merge_higher_tf
from goldbot.features.technical import atr
from goldbot.research.metrics import breakeven_prob, size_multiplier
from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.gate import REARM_PHRASE, Stage, new_day
from goldbot.risk.supervisor import Supervisor
from goldbot.specialists.base import Specialist
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal


class Model(Protocol):
    feature_names: list[str]
    def predict(self, X: pd.DataFrame) -> np.ndarray: ...


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
    server_tz: str = "Europe/Athens"         # broker server clock: rollover (00:00 +-5 min) and the Friday 21:30 rule
    rollover_min: int = 5
    weekend_cut: str = "21:30"               # Friday, server time: close losers, tighten winners, no new entries


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
        self.allocator = allocator or RuleAllocator()
        self.gate = RiskGate(limits)
        self.ticks: list[Tick] = []
        self.bars_1m = pd.DataFrame()
        self.open: dict[int, OpenTrade] = {}
        self.pending: dict[str, tuple[Intent, Proposal, Specialist]] = {}
        self.sent_ids: set[str] = set()
        self._orders: dict[str, SentOrder] = {}     # pending_orders: every id ever sent (pruned after a week)
        self.last_bar_close: pd.Timestamp | None = None
        self.state = AccountState(equity=0, balance_closed_hwm=0, day_start_equity=0, week_start_equity=0,
                                  open_positions=0, margin_used=0, last_tick_age_s=0, spread_points=0)
        self.decisions: list[dict] = []
        self.store = Store(cfg.data_root) if cfg.data_root else None
        self._tick_log: list[Tick] = []
        self._journaled = 0                  # decisions already written to the store's journal
        self._cost_cache: tuple[float, CostTable | None] = (-1.0, None)
        self._ctx_cache: dict[str, tuple[pd.Timestamp, tuple[pd.DataFrame, pd.DataFrame] | None]] = {}
        # per timeframe: completed 1m bars -> bars, re-aggregating only the groups that changed since the last close
        self._resamplers: dict[str, IncrementalResampler] = {}
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
        self._unhealthy_at: pd.Timestamp | None = None    # last time the data was seen stale or in error
        # risk periods and the owner's re-arm survive a restart (state/risk_<account>.json)
        self._last_reset: pd.Timestamp | None = None
        self._rearm_seen: str | None = None
        self._weekend_done: str | None = None        # server date of the last Friday the weekend rule ran
        self._halted_at: pd.Timestamp | None = None  # when the 12% kill switch tripped (re-arm conditions count from it)
        self._propose_only_until: pd.Timestamp | None = None   # after a re-arm: no auto entries before this
        self._rearm_refused: str | None = None       # why the last owner re-arm was refused (engine state, dashboard)
        self._load_risk_state()
        self._load_orders()
        self._last_state_write: pd.Timestamp | None = None
        self._last_reconcile: pd.Timestamp | None = None
        self._last_atr: float | None = None          # decision-tf ATR at the last bar close (orphan stops)
        self._foreign: list[int] = []                # positions with unknown magic (manual trades): listed, never touched
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
        self._last_tick = t
        self.ticks.append(t)
        self._log_tick(t)
        self._roll_risk_period(t.ts_utc)
        if hasattr(self.broker, "on_tick"):
            self.broker.on_tick(t)  # paper broker fills
        if self._last_reconcile is None or (t.ts_utc - self._last_reconcile).total_seconds() >= self.cfg.reconcile_every_s:
            self._reconcile(t.ts_utc)
        sec = tf_seconds(self.cfg.decision_tf)
        bar_close = pd.Timestamp((int(t.ts_utc.timestamp()) // sec) * sec, unit="s", tz="UTC")
        out: list[dict] = []
        if self.last_bar_close is not None and bar_close > self.last_bar_close:
            out = self.on_bar_close(self.last_bar_close + pd.Timedelta(seconds=sec), t)
            self._last_state_write = t.ts_utc
        elif self._last_state_write is None or (t.ts_utc - self._last_state_write).total_seconds() >= self.cfg.state_every_s:
            self._rebuild_bars()            # completed minutes join the bars between closes, so their age is current
            self._refresh_account(t)
            self._write_state()
            self._last_state_write = t.ts_utc
        self.last_bar_close = bar_close
        return out

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

    def _rebuild_bars(self) -> None:
        """Incremental: only ticks since the last completed minute are aggregated; bars accumulate."""
        if not self.ticks:
            return
        tdf = pd.DataFrame({"ts_utc": [x.ts_utc for x in self.ticks], "bid": [x.bid for x in self.ticks], "ask": [x.ask for x in self.ticks]})
        new = ticks_to_1m(tdf, DEFAULT_SESSIONS)
        if new.empty:
            return
        last_minute = new["ts_utc"].iloc[-1]
        done = new[new["ts_utc"] < last_minute]          # the current minute may still receive ticks
        if not done.empty:
            self._check_new_bars(done)
            self.bars_1m = pd.concat([self.bars_1m, done]).drop_duplicates("ts_utc", keep="last").tail(self.cfg.max_bars_in_memory).reset_index(drop=True) \
                if not self.bars_1m.empty else done.reset_index(drop=True)
        self.ticks = [x for x in self.ticks if x.ts_utc >= last_minute]

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
        regime = self._regime(base.X)
        fam_w = self.allocator.weights(regime)
        decisions: list[dict] = []
        tfs = {a.timeframe for a in self.agents.values()} | {self.cfg.decision_tf}
        if self.shadow is not None:            # open shadow trades keep ageing after their agent leaves the set
            tfs |= {t.timeframe for b in self.shadow.books.values() for t in b.open}
        for tf in sorted(tfs, key=tf_seconds):
            if tf != "1d" and int(close_ts.timestamp()) % tf_seconds(tf):
                continue                       # cheap pre-check: this close does not complete an intraday bar of tf
            fr = base if tf == self.cfg.decision_tf else self._frame(complete, tf, close_ts)
            if fr is None or pd.Timestamp(fr.dec["visible_at"].iloc[-1]) != close_ts:
                continue
            agents = [a for a in self.agents.values() if a.timeframe == tf]
            decisions += self._decide(tf, fr, agents, fam_w, close_ts, last_tick)
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
            w = fam_w.get(agent.family, 0.0) * share
            mult = float(size_multiplier(np.array([p]), w, ls.target_atr, ls.stop_atr, hurdle_atr)[0])
            price = last_tick.ask if side > 0 else last_tick.bid
            intent = Intent(agent_id=agent.agent_id, side=side, p=p, target_atr=ls.target_atr, stop_atr=ls.stop_atr, atr_usd=float(a.iloc[last]), cost_atr=cost_atr,
                            multiplier=mult if mult > 0 else 0.0, price=price)
            if mult <= 0 or p <= breakeven_prob(ls.target_atr, ls.stop_atr, hurdle_atr) + 0.02:
                decisions.append(self._record(agent, close_ts, p, mult, "below_threshold"))
                continue
            gd = self.gate.check(intent, self.state)
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
                            top_features=self._top_features(model, feats), window_s=90)
            if self.cfg.approval_mode == "auto":
                self._execute(prop, agent, gd.lots, stop, target, requested=price)
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
            gd = self.gate.check(intent, self.state)  # re-check at approval time
            if gd.allowed:
                price = tick.ask if prop.side > 0 else tick.bid
                self._execute(prop, agent, gd.lots, price - prop.side * gd.stop_distance,
                              price + prop.side * agent.label_spec.target_atr * intent.atr_usd, requested=price)
            else:
                self.decisions.append({"ts": time.time(), "agent": agent.agent_id, "action": "gate_at_approval:" + ",".join(gd.reasons)})
        self._write_state()

    def _execute(self, prop: Proposal, agent: Specialist, lots: float, stop: float, target: float, *, requested: float) -> None:
        if prop.proposal_id in self.sent_ids:       # sent before (this process or before a restart): never again
            self.decisions.append({"ts": time.time(), "agent": agent.agent_id, "action": "duplicate_suppressed",
                                   "proposal": prop.proposal_id})
            return
        magic = self._magic(agent.family)
        oi = OrderIntent(client_order_id=prop.proposal_id, symbol=self.cfg.symbol, side=prop.side, lots=lots, sl=round(stop, 2), tp=round(target, 2), magic=magic,
                         comment=prop.proposal_id[-31:])
        # pending_orders row persisted BEFORE sending: a crash between send and result is reconciled on restart
        self._orders_record(prop.proposal_id, agent_id=agent.agent_id, side=prop.side, magic=magic, lots=lots,
                            max_bars=self._base_bars(agent), sl=oi.sl, tp=oi.tp, ts=pd.Timestamp.now("UTC"))
        res = self.broker.place_order(oi)
        rec = self._orders[prop.proposal_id]
        rec.status, rec.position_id = ("filled", res.position_id) if res.ok and res.position_id is not None else ("rejected", None)
        if res.ok and res.position_id is not None:
            self.open[res.position_id] = OpenTrade(position_id=res.position_id, agent_id=agent.agent_id, side=prop.side, lots=res.filled_lots,
                                                  entry_bar_ts=pd.Timestamp.now('UTC'), max_bars=self._base_bars(agent),
                                                  sl=oi.sl, tp=oi.tp)
        self._save_orders()
        self.decisions.append({"ts": time.time(), "agent": agent.agent_id, "action": "order", "ok": res.ok,
                               "retcode": res.retcode, "price": res.price, "lots": res.filled_lots})
        if self.store is not None and res.ok and res.price is not None:
            # requested vs filled feeds the nightly slippage table
            fill = pd.DataFrame([{"ts_utc": self.broker.last_tick(self.cfg.symbol).ts_utc, "client_order_id": prop.proposal_id,
                                  "agent_id": agent.agent_id, "side": prop.side, "lots": res.filled_lots, "requested": requested,
                                  "filled": res.price, "order_type": "market", "retcode": res.retcode, "commission": np.nan}])
            self.store.append("fills", fill, source=self.cfg.account_id, symbol=self.cfg.symbol, dedupe=False)

    # ------------------------------------------------------------------ position management
    def _manage_open(self, dec: pd.DataFrame) -> None:
        live = {p.position_id for p in self.broker.positions()}
        for pid in list(self.open):
            if pid not in live:
                del self.open[pid]  # closed by stop/target at the broker
                continue
            tr = self.open[pid]
            tr.bars_held += 1
            if tr.bars_held >= tr.max_bars:
                self.broker.close(pid)
                self.decisions.append({"ts": time.time(), "agent": tr.agent_id, "action": "time_exit", "position": pid})
                del self.open[pid]
        self._reconcile(self.last_bar_close)

    def _own(self, magic: int) -> bool:
        return self.cfg.magic_base <= magic < self.cfg.magic_base + 100

    def _reconcile(self, now: pd.Timestamp | None) -> None:
        """Broker positions are the source of truth (design: Reconciliation), on start, every bar close and every
        `reconcile_every_s` of tick time: an orphan in this engine's magic range is adopted and, without a stop, given
        one `orphan_stop_atr` x ATR from entry; a stop or target missing at the broker, or a stop looser than the one
        this engine set, is reinstated. Positions with unknown magic numbers are listed and never touched."""
        if now is not None:
            self._last_reconcile = now
        positions = self.broker.positions()
        self._foreign = sorted(p.position_id for p in positions if not self._own(p.magic))
        for p in positions:
            if not self._own(p.magic):
                continue
            tr = self.open.get(p.position_id)
            if tr is None:
                tr = self.open[p.position_id] = OpenTrade(position_id=p.position_id, agent_id="orphan", side=p.side, lots=p.lots,
                                                          entry_bar_ts=p.open_time_utc, max_bars=48, sl=p.sl, tp=p.tp)
                self.decisions.append({"ts": time.time(), "action": "adopt_orphan", "position": p.position_id})
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
                sl = tr.sl if loose else p.sl
                tp = tr.tp if lost_tp else p.tp
                res = self.broker.modify(p.position_id, sl, tp)
                self.decisions.append({"ts": time.time(), "action": "reinstate_stops", "position": p.position_id, "sl": sl,
                                       "tp": tp, "ok": res.ok, "retcode": res.retcode})
            elif tr.sl is None and p.sl is not None:
                tr.sl = p.sl                  # adopted with a stop: that stop is the floor from now on

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
        positions = self.broker.positions()
        st.open_positions = len(positions)
        st.open_lots = round(sum(p.lots for p in positions), 6)       # every position on the account is exposure
        st.open_notional = st.open_lots * 100.0 * (tick.bid + tick.ask) / 2
        st.spread_points = (tick.ask - tick.bid) / 0.01
        now = self._now(tick)
        st.last_tick_age_s = max(0.0, (now - tick.ts_utc).total_seconds())
        stale = stale_feed(tick.ts_utc, now, limit_seconds=self.cfg.stale_feed_s)
        st.dq_error = stale or bool(self._dq_bar_errors or self._dq_pending)
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
        self._server_clock(now, tick)
        stage = st.stage
        if self.gate.update_stage(st) != stage:
            if st.stage == Stage.HALTED:
                self._halted_at = now
            self._save_risk_state()             # a drawdown stage change survives a restart at once
        if st.stage == Stage.HALTED:
            self._kill_switch(everything=stage != Stage.HALTED)
        if self._propose_only_until is not None and now < self._propose_only_until:
            self.cfg.approval_mode = "propose"  # 30 days propose-and-approve after a re-arm

    def _kill_switch(self, *, everything: bool) -> None:
        """12% drawdown: close at market immediately (design: Drawdown kill switch) and fall back to propose-and-
        approve. On the trip every position on the account is closed; while halted, any of this engine's positions
        (its magic range) that is still open, e.g. after a failed close, is closed again on each refresh."""
        self.cfg.approval_mode = "propose"
        for p in self.broker.positions():
            if everything or self.cfg.magic_base <= p.magic < self.cfg.magic_base + 100:
                res = self.broker.close(p.position_id)
                self.open.pop(p.position_id, None)
                self.decisions.append({"ts": time.time(), "action": "kill_switch_close", "position": p.position_id,
                                       "ok": res.ok, "retcode": res.retcode, "price": res.price})
        self.state.open_positions = len(self.broker.positions())

    def _server_clock(self, now: pd.Timestamp, tick: Tick) -> None:
        """Rollover (+-rollover_min of 00:00 server: no entries) and weekend (Friday weekend_cut server until the week
        reopens: no entries; at the cut, once per Friday, this engine's losers are closed and winners' stops tightened)."""
        srv = now.tz_convert(ZoneInfo(self.cfg.server_tz))
        mins = srv.hour * 60 + srv.minute + srv.second / 60
        self.state.in_rollover = min(mins, 1440 - mins) <= self.cfg.rollover_min
        h, m = (int(x) for x in self.cfg.weekend_cut.split(":"))
        friday_cut = srv.weekday() == 4 and mins >= h * 60 + m
        self.state.weekend = friday_cut or srv.weekday() >= 5
        if friday_cut and self._weekend_done != srv.date().isoformat():
            self._weekend_done = srv.date().isoformat()
            self._weekend_rule(tick)
            self._save_risk_state()

    def _weekend_rule(self, tick: Tick) -> None:
        """Design: weekend gaps blow through stops, so losers are closed and winners' stops moved to lock in half the
        open profit (never loosened). Exits are automatic and never gated."""
        for p in self.broker.positions():
            if not self._own(p.magic):
                continue
            px = tick.bid if p.side > 0 else tick.ask
            if p.side * (px - p.open_price) <= 0:
                res = self.broker.close(p.position_id)
                self.open.pop(p.position_id, None)
                self.decisions.append({"ts": time.time(), "action": "weekend_close_loser", "position": p.position_id,
                                       "ok": res.ok, "retcode": res.retcode, "price": res.price})
                continue
            sl = round(p.open_price + 0.5 * (px - p.open_price), 2)
            if p.sl is None or p.side * (sl - p.sl) > 0:
                res = self.broker.modify(p.position_id, sl, p.tp)
                if p.position_id in self.open:
                    self.open[p.position_id].sl = sl
                self.decisions.append({"ts": time.time(), "action": "weekend_tighten", "position": p.position_id, "sl": sl,
                                       "ok": res.ok, "retcode": res.retcode})
        self.state.open_positions = len(self.broker.positions())

    def _now(self, tick: Tick) -> pd.Timestamp:
        return pd.Timestamp.now("UTC") if self.cfg.live_clock else tick.ts_utc

    # ------------------------------------------------------------------ data quality
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
        _, events = check_bars(joined)
        errs = [e for e in events if e.severity == "error"]
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
                       tp: float, ts: pd.Timestamp) -> None:
        """Write the id to the pending_orders table (atomically, on disk) before order_send."""
        self.sent_ids.add(cid)
        self._orders[cid] = SentOrder(client_order_id=cid, ts_utc=ts, agent_id=agent_id, side=side, magic=magic, lots=lots,
                                      max_bars=max_bars, sl=sl, tp=tp)
        self._save_orders()

    def _save_orders(self) -> None:
        keep_after = pd.Timestamp.now("UTC") - pd.Timedelta(days=7)
        self._orders = {k: o for k, o in self._orders.items() if o.ts_utc >= keep_after or o.status == "sending"}
        payload = {"sent": {k: o.model_dump(mode="json") for k, o in self._orders.items()},
                   "open": {str(k): t.model_dump(mode="json") for k, t in self.open.items()}}
        path = self._orders_path()
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, path)

    def _load_orders(self) -> None:
        """On start: reload the pending_orders table and the open trades, then reconcile them with the broker: an id
        whose send was never confirmed is looked up in the broker's positions and deals (by client id / MT5 comment);
        a fill is adopted with its own agent, no fill marks it unfilled. Every id stays in sent_ids, so a restart never
        re-sends. Open trades the broker no longer has were closed while the engine was down."""
        path = self._orders_path()
        if not path.exists():
            return
        d = json.loads(path.read_text())          # unreadable -> fail loudly rather than forget what was sent
        self._orders = {k: SentOrder.model_validate(v) for k, v in d.get("sent", {}).items()}
        self.sent_ids = set(self._orders)
        self.open = {int(k): OpenTrade.model_validate(v) for k, v in d.get("open", {}).items()}
        positions = {p.position_id: p for p in self.broker.positions()}
        self.open = {k: t for k, t in self.open.items() if k in positions}
        for cid, rec in self._orders.items():
            if rec.status != "sending":
                continue
            pos = next((p for p in positions.values() if p.comment == cid[-31:] or p.comment == cid), None)
            deals = self.broker.deals_since(rec.ts_utc - pd.Timedelta(minutes=5))
            dealt = not deals.empty and (
                ("client_order_id" in deals.columns and bool((deals["client_order_id"] == cid).any()))
                or ("comment" in deals.columns and bool(deals["comment"].isin([cid, cid[-31:]]).any())))
            if pos is not None:
                rec.status, rec.position_id = "filled", pos.position_id
                self.open[pos.position_id] = OpenTrade(position_id=pos.position_id, agent_id=rec.agent_id, side=pos.side,
                                                       lots=pos.lots, entry_bar_ts=pos.open_time_utc, max_bars=rec.max_bars,
                                                       sl=rec.sl, tp=rec.tp)
                self.decisions.append({"ts": time.time(), "agent": rec.agent_id, "action": "reconcile_adopt_sent",
                                       "proposal": cid, "position": pos.position_id})
            else:
                rec.status = "filled" if dealt else "unfilled"     # filled and already closed, or never filled
                self.decisions.append({"ts": time.time(), "agent": rec.agent_id, "action": f"reconcile_sent_{rec.status}",
                                       "proposal": cid})
        self._save_orders()

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
        path = self._risk_path()
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, path)

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

    def _regime(self, X: pd.DataFrame) -> Regime:
        row = X.iloc[-1]
        adx = float(row.get("h1_adx14", row.get("adx14", 20.0)) or 20.0)
        rv = X["h1_rv_20"] if "h1_rv_20" in X.columns else X.get("rv_20", pd.Series([np.nan]))
        q = int(pd.qcut(rv.dropna().tail(60 * 24), 4, labels=False, duplicates="drop").iloc[-1]) if rv.notna().sum() > 40 else 1
        tier = int(row.get("in_blackout", 0) or 0)
        return Regime(adx_1h=adx, atr_1h_quartile=q, vol_tercile=int(row.get("vol_tercile", 1) or 1),
                      minutes_to_tier1=0.0 if tier else None, minutes_since_tier1=None)

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
                p = float(model.predict(feats[cols])[0])
                ls = agent.label_spec
                if p <= breakeven_prob(ls.target_atr, ls.stop_atr, cost_atr) + 0.02:
                    continue
                self.shadow.open_trade(version=version, agent_id=agent.agent_id, side=side, bar_ts=pd.Timestamp(bar["ts_utc"]),
                                       entry=float(bar["ask_close"] if side > 0 else bar["bid_close"]), atr_usd=atr_usd,
                                       target_atr=ls.target_atr, stop_atr=ls.stop_atr, max_bars=ls.max_bars, p=p, timeframe=tf)

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

    def _account_class(self) -> str:
        path = Path(self.cfg.state_dir, f"classifier_{self.cfg.account_id}.json")
        try:
            return str(json.loads(path.read_text()).get("class") or "unknown") if path.exists() else "unknown"
        except (ValueError, OSError):
            return "unknown"

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
            "dq_error": st.dq_error, "stale_bars": st.stale_bars, "data_recovering": st.data_recovering, "dq_checks": sorted({e.check for e in self._dq_bar_errors + self._dq_pending}),
            "foreign_positions": self._foreign, "rearm_refused": self._rearm_refused,
            "propose_only_until": self._propose_only_until.isoformat() if self._propose_only_until is not None else None,
        }
        path = Path(self.cfg.state_dir, f"engine_{self.cfg.account_id}.json")
        tmp = path.with_suffix(f".{os.getpid()}.tmp")       # the supervisor never reads a half-written file
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, path)
        self._save_risk_state()
        self._save_orders()
