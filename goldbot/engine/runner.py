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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from goldbot.allocator import Regime, RuleAllocator
from goldbot.config import tf_seconds
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.execution.broker import Broker, OrderIntent, Tick
from goldbot.features import build_features
from goldbot.features.mtf import merge_higher_tf
from goldbot.features.technical import atr
from goldbot.research.metrics import breakeven_prob, size_multiplier
from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.gate import Stage
from goldbot.specialists.base import Specialist
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal


class Model(Protocol):
    feature_names: list[str]
    def predict(self, X: pd.DataFrame) -> np.ndarray: ...


@dataclass
class ConstantModel:
    """Stand-in until a trained MetaLabelModel is loaded: returns a fixed probability."""
    p: float = 0.6
    feature_names: list[str] = field(default_factory=list)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.full(len(X), self.p)


@dataclass
class EngineConfig:
    account_id: str
    broker_name: str
    mode: str = "paper"              # paper | demo | live (what the broker is); approvals are separate
    approval_mode: str = "propose"   # propose | auto
    symbol: str = "XAUUSD"
    decision_tf: str = "15m"
    context_tfs: tuple[str, ...] = ("1h", "1d")
    magic_base: int = 260100
    state_dir: str = "state"
    cost_atr: float = 0.10           # round-trip cost in ATR until the measured cost table exists
    max_bars_in_memory: int = 60000     # 1m bars kept (~40 trading days)
    feature_window: int = 800            # decision bars the features are computed on
    owner_user_id: int = 0


@dataclass
class OpenTrade:
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
                 limits: RiskLimits | None = None):
        self.cfg = cfg
        self.broker = broker
        self.agents = {a.agent_id: a for a in agents}
        self.models = models
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
        Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
        self.center.on_decision = self._on_decision

    # ------------------------------------------------------------------ ticks and bars
    def on_tick(self, t: Tick) -> list[dict]:
        """Feed a tick; returns any decisions made at a bar close."""
        self.ticks.append(t)
        if hasattr(self.broker, "on_tick"):
            self.broker.on_tick(t)  # paper broker fills
        sec = tf_seconds(self.cfg.decision_tf)
        bar_close = pd.Timestamp((int(t.ts_utc.timestamp()) // sec) * sec, unit="s", tz="UTC")
        out: list[dict] = []
        if self.last_bar_close is not None and bar_close > self.last_bar_close:
            out = self.on_bar_close(self.last_bar_close + pd.Timedelta(seconds=sec), t)
        self.last_bar_close = bar_close
        return out

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
    def on_bar_close(self, close_ts: pd.Timestamp, last_tick: Tick) -> list[dict]:
        self._rebuild_bars()
        complete = self.bars_1m[self.bars_1m["visible_at"] <= close_ts]
        if len(complete) < 400:
            return []
        dec = resample_bars(complete, self.cfg.decision_tf).reset_index(drop=True)
        dec = dec[dec["visible_at"] <= close_ts].tail(self.cfg.feature_window).reset_index(drop=True)
        if len(dec) < 120:
            return []
        m = mid(dec)
        X = build_features(m, DEFAULT_FEATURE_NAMES)
        for tf in self.cfg.context_tfs:
            hb = resample_bars(complete, tf).reset_index(drop=True)
            hb = hb[hb["visible_at"] <= close_ts].reset_index(drop=True)
            if len(hb) < 30:
                continue
            hf = build_features(mid(hb), ["atr", "trend_strength", "moving_averages", "realised_vol"])
            X = merge_higher_tf(X, hf, hb, {"1h": "h1", "4h": "h4", "1d": "d1", "1w": "w1"}[tf])
        a = atr(m, 14)
        self._refresh_account(last_tick)
        self._manage_open(dec)
        self.center.sweep_expired()
        regime = self._regime(X)
        fam_w = self.allocator.weights(regime)
        decisions = []
        last = len(dec) - 1
        for agent in self.agents.values():
            cands = agent.candidates(m, X)
            if cands.empty or int(cands["idx"].iloc[-1]) != last:
                continue
            side = int(cands["side"].iloc[-1])
            model = self.models.get(agent.family)
            if model is None:
                continue
            feats = X.drop(columns=["ts_utc"]).iloc[[last]].replace([np.inf, -np.inf], np.nan)
            cols = model.feature_names or [c for c in feats.columns]
            p = float(model.predict(feats[[c for c in cols if c in feats.columns]] if model.feature_names else feats)[0])
            ls = agent.label_spec
            w = fam_w.get(agent.family, 0.0)
            mult = float(size_multiplier(np.array([p]), w, ls.target_atr, ls.stop_atr, self.cfg.cost_atr)[0])
            price = last_tick.ask if side > 0 else last_tick.bid
            intent = Intent(agent.agent_id, side, p, ls.target_atr, ls.stop_atr, float(a.iloc[last]), self.cfg.cost_atr,
                            mult if mult > 0 else 0.0, price)
            if mult <= 0 or p <= breakeven_prob(ls.target_atr, ls.stop_atr, self.cfg.cost_atr) + 0.02:
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
            prop = Proposal(pid, self.cfg.account_id, agent.agent_id, side, gd.lots, price, stop, target, p,
                            p * ls.target_atr - (1 - p) * ls.stop_atr - self.cfg.cost_atr, self.state.spread_points,
                            self._top_features(model, feats), window_s=90)
            self.pending[pid] = (intent, prop, agent)
            if self.cfg.approval_mode == "auto":
                self._execute(prop, agent, gd.lots, stop, target)
                decisions.append(self._record(agent, close_ts, p, mult, "executed:auto", pid))
            else:
                self.center.propose(prop)
                decisions.append(self._record(agent, close_ts, p, mult, "proposed", pid))
        self._write_state()
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
                              price + prop.side * agent.label_spec.target_atr * intent.atr_usd)
            else:
                self.decisions.append({"ts": time.time(), "agent": agent.agent_id, "action": "gate_at_approval:" + ",".join(gd.reasons)})
        self._write_state()

    def _execute(self, prop: Proposal, agent: Specialist, lots: float, stop: float, target: float) -> None:
        magic = self.cfg.magic_base + (abs(hash(agent.family)) % 100)
        oi = OrderIntent(prop.proposal_id, self.cfg.symbol, prop.side, lots, round(stop, 2), round(target, 2), magic,
                         comment=prop.proposal_id[-31:])
        self.sent_ids.add(prop.proposal_id)   # written BEFORE sending: never double-send
        res = self.broker.place_order(oi)
        if res.ok and res.position_id is not None:
            self.open[res.position_id] = OpenTrade(res.position_id, agent.agent_id, prop.side, res.filled_lots,
                                                  pd.Timestamp.now('UTC'), agent.label_spec.max_bars)
        self.decisions.append({"ts": time.time(), "agent": agent.agent_id, "action": "order", "ok": res.ok,
                               "retcode": res.retcode, "price": res.price, "lots": res.filled_lots})

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
                self.open[p.position_id] = OpenTrade(p.position_id, "orphan", p.side, p.lots, p.open_time_utc, 48)
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

    def _proposal_id(self, agent_id: str, close_ts: pd.Timestamp) -> str:
        h = hashlib.sha1(f"{self.cfg.account_id}|{agent_id}|{close_ts.isoformat()}".encode()).hexdigest()[:10]
        return f"{self.cfg.account_id}-{int(close_ts.timestamp())}-{h}"

    def _record(self, agent: Specialist, ts: pd.Timestamp, p: float, mult: float, action: str, pid: str | None = None) -> dict:
        d = {"ts": ts.isoformat(), "agent": agent.agent_id, "p": round(p, 4), "mult": round(mult, 3), "action": action, "proposal": pid}
        self.decisions.append(d)
        return d

    def _write_state(self) -> None:
        st = self.state
        payload = {
            "account": self.cfg.account_id, "broker": self.cfg.broker_name, "mode": self.cfg.mode, "ts": time.time(),
            "equity": st.equity, "day_start_equity": st.day_start_equity, "week_start_equity": st.week_start_equity,
            "balance_closed_hwm": st.balance_closed_hwm, "stage": st.stage.value if isinstance(st.stage, Stage) else str(st.stage),
            "open_positions": st.open_positions, "spread_points": st.spread_points, "last_tick_age_s": st.last_tick_age_s,
            "terminal_connected": True, "account_class": "unknown", "pending": len(self.center.pending),
        }
        Path(self.cfg.state_dir, f"engine_{self.cfg.account_id}.json").write_text(json.dumps(payload))
