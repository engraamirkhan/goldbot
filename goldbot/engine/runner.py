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
import time
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.allocator import Regime, RuleAllocator
from goldbot.base import Record
from goldbot.config import tf_seconds
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.econ_calendar import blackout_window
from goldbot.data.news import shock_window
from goldbot.data.resample import BAR_COLUMNS, mid, resample_bars, ticks_to_1m
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
from goldbot.risk.gate import Stage
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
    entry_bar_ts: pd.Timestamp
    max_bars: int
    bars_held: int = 0


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
        self.last_bar_close: pd.Timestamp | None = None
        self.state = AccountState(equity=0, balance_closed_hwm=0, day_start_equity=0, week_start_equity=0,
                                  open_positions=0, margin_used=0, last_tick_age_s=0, spread_points=0)
        self.decisions: list[dict] = []
        self.store = Store(cfg.data_root) if cfg.data_root else None
        self._tick_log: list[Tick] = []
        self._journaled = 0                  # decisions already written to the store's journal
        self._cost_cache: tuple[float, CostTable | None] = (-1.0, None)
        self._ctx_cache: dict[str, tuple[pd.Timestamp, tuple[pd.DataFrame, pd.DataFrame] | None]] = {}
        self._calendar: tuple[pd.Timestamp | None, pd.DataFrame] = (None, pd.DataFrame())
        self._blackout_event: dict | None = None
        self._news: tuple[pd.Timestamp | None, pd.DataFrame] = (None, pd.DataFrame())
        # shadow book: version -> (family, model); champions and challengers paper-trade without orders
        self.shadow = ShadowBook(cfg.state_dir) if cfg.shadow_host else None
        self.shadow_models: dict[str, tuple[str, Model]] = {}
        self.set_shadow_models(shadow_models or {})
        Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
        self.center.on_decision = self._on_decision

    # ------------------------------------------------------------------ ticks and bars
    def on_tick(self, t: Tick) -> list[dict]:
        """Feed a tick; returns any decisions made at a bar close."""
        self.ticks.append(t)
        self._log_tick(t)
        self.center.poll_bus()          # approvals from the dashboard / Telegram service, applied within a tick
        if hasattr(self.broker, "on_tick"):
            self.broker.on_tick(t)  # paper broker fills
        sec = tf_seconds(self.cfg.decision_tf)
        bar_close = pd.Timestamp((int(t.ts_utc.timestamp()) // sec) * sec, unit="s", tz="UTC")
        out: list[dict] = []
        if self.last_bar_close is not None and bar_close > self.last_bar_close:
            out = self.on_bar_close(self.last_bar_close + pd.Timedelta(seconds=sec), t)
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
            self.bars_1m = pd.concat([self.bars_1m, done]).drop_duplicates("ts_utc", keep="last").tail(self.cfg.max_bars_in_memory).reset_index(drop=True) \
                if not self.bars_1m.empty else done.reset_index(drop=True)
        self.ticks = [x for x in self.ticks if x.ts_utc >= last_minute]

    # ------------------------------------------------------------------ main step
    def _frame(self, complete: pd.DataFrame, tf: str, close_ts: pd.Timestamp) -> _Frame | None:
        """Completed bars of one decision timeframe with features and its context (the same rule as research)."""
        dec = resample_bars(complete, tf).reset_index(drop=True)
        dec = dec[dec["visible_at"] <= close_ts].tail(self.cfg.feature_window).reset_index(drop=True)
        if len(dec) < 120:
            return None
        m = mid(dec)
        X = build_features(m, DEFAULT_FEATURE_NAMES)
        for ctf in context_tfs(tf):
            hit = self._context(complete, ctf, close_ts)
            if hit is not None:
                X = merge_higher_tf(X, hit[1], hit[0], TF_LABEL[ctf])
        return _Frame(dec=dec, m=m, X=X, atr=atr(m, 14))

    def _context(self, complete: pd.DataFrame, tf: str, close_ts: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame] | None:
        """Completed context bars and their features, rebuilt only when a new bar of `tf` has completed: the key is
        the period close_ts falls in (UTC floor intraday, the feature-day for 1d), which changes at a bar's visible_at."""
        at = pd.DatetimeIndex([close_ts])
        key = feature_day(at)[0] if tf == "1d" else floor_tf(at, tf_seconds(tf))[0]
        cached = self._ctx_cache.get(tf)
        if cached is not None and cached[0] == key:
            return cached[1]
        hb = resample_bars(complete, tf).reset_index(drop=True)
        hb = hb[hb["visible_at"] <= close_ts].reset_index(drop=True)
        out = None if len(hb) < 30 else (hb, build_features(mid(hb), ["atr", "trend_strength", "moving_averages", "realised_vol"]))
        self._ctx_cache[tf] = (key, out)
        return out

    def on_bar_close(self, close_ts: pd.Timestamp, last_tick: Tick) -> list[dict]:
        """Runs on every base (decision_tf) bar. Each agent decides on its own timeframe, so a 1h agent is
        evaluated only at closes that complete a 1h bar; position management stays on the base clock."""
        self._rebuild_bars()
        complete = self.bars_1m[self.bars_1m["visible_at"] <= close_ts]
        if len(complete) < 400:
            return []
        base = self._frame(complete, self.cfg.decision_tf, close_ts)
        if base is None:
            return []
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
        cost_atr = self._cost_atr(float(a.iloc[last]), close_ts)
        cands_by_agent = {agent.agent_id: agent.candidates(m, X) for agent in agents}
        if self.shadow is not None:
            self._shadow_step(tf, dec, X, agents, cands_by_agent, float(a.iloc[last]), cost_atr)
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
            feats = X.drop(columns=["ts_utc"]).iloc[[last]].replace([np.inf, -np.inf], np.nan)
            cols = model.feature_names or [c for c in feats.columns]
            p = float(model.predict(feats[[c for c in cols if c in feats.columns]] if model.feature_names else feats)[0])
            ls = agent.label_spec
            w = fam_w.get(agent.family, 0.0) * share
            mult = float(size_multiplier(np.array([p]), w, ls.target_atr, ls.stop_atr, cost_atr)[0])
            price = last_tick.ask if side > 0 else last_tick.bid
            intent = Intent(agent_id=agent.agent_id, side=side, p=p, target_atr=ls.target_atr, stop_atr=ls.stop_atr, atr_usd=float(a.iloc[last]), cost_atr=cost_atr,
                            multiplier=mult if mult > 0 else 0.0, price=price)
            if mult <= 0 or p <= breakeven_prob(ls.target_atr, ls.stop_atr, cost_atr) + 0.02:
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
        magic = self.cfg.magic_base + (abs(hash(agent.family)) % 100)
        oi = OrderIntent(client_order_id=prop.proposal_id, symbol=self.cfg.symbol, side=prop.side, lots=lots, sl=round(stop, 2), tp=round(target, 2), magic=magic,
                         comment=prop.proposal_id[-31:])
        self.sent_ids.add(prop.proposal_id)   # written BEFORE sending: never double-send
        res = self.broker.place_order(oi)
        if res.ok and res.position_id is not None:
            self.open[res.position_id] = OpenTrade(position_id=res.position_id, agent_id=agent.agent_id, side=prop.side, lots=res.filled_lots,
                                                  entry_bar_ts=pd.Timestamp.now('UTC'), max_bars=self._base_bars(agent))
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
        # reconciliation: adopt orphans with our magic range
        for p in self.broker.positions():
            if p.position_id not in self.open and self.cfg.magic_base <= p.magic < self.cfg.magic_base + 100:
                self.open[p.position_id] = OpenTrade(position_id=p.position_id, agent_id="orphan", side=p.side, lots=p.lots, entry_bar_ts=p.open_time_utc, max_bars=48)
                self.decisions.append({"ts": time.time(), "action": "adopt_orphan", "position": p.position_id})

    # ------------------------------------------------------------------ helpers
    def _refresh_account(self, tick: Tick) -> None:
        acc = self.broker.account()
        st = self.state
        st.equity = acc.equity
        st.margin_used = acc.margin
        if st.balance_closed_hwm == 0:
            st.balance_closed_hwm = st.day_start_equity = st.week_start_equity = acc.balance
        st.balance_closed_hwm = max(st.balance_closed_hwm, acc.balance)
        st.open_positions = len(self.broker.positions())
        st.spread_points = (tick.ask - tick.bid) / 0.01
        st.last_tick_age_s = 0.0
        if self.cfg.halt_checks:
            st.supervisor_halt, _ = Supervisor.engine_should_halt(self.cfg.state_dir)
            st.owner_halt = self.center.bus.control().halted if self.center.bus is not None else False
        if self.cfg.news_blackout:
            st.in_blackout = self._blackout(tick.ts_utc) is not None
        self.gate.update_stage(st)

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

    def _base_bars(self, agent: Specialist) -> int:
        """The agent's time barrier in base bars (position management runs on the decision_tf clock)."""
        return int(agent.label_spec.max_bars * tf_seconds(agent.timeframe) // tf_seconds(self.cfg.decision_tf))

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

    def _cost_atr(self, atr_usd: float, ts: pd.Timestamp) -> float:
        """Round-trip cost in ATR for the current session from the nightly cost table; config fallback without one."""
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
        c = table.round_trip_atr(session, atr_usd)
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
            "open_positions": st.open_positions, "spread_points": st.spread_points, "last_tick_age_s": st.last_tick_age_s,
            "terminal_connected": True, "account_class": self._account_class(), "pending": len(self.center.pending),
            "approval_mode": self.cfg.approval_mode, "blackout": self._blackout_event,
        }
        Path(self.cfg.state_dir, f"engine_{self.cfg.account_id}.json").write_text(json.dumps(payload))
