"""RiskGate: hard limits no model output can override. Sits between the allocator and the broker.

Per-account limits (daily 2% / weekly 5% of own equity; staged drawdown 8% size-down, 12% stop), plus
the supervisor applies tighter limits on combined equity (1.5% / 4%) and writes a HALT flag.
All state is explicit and serialisable so the engine can reconcile after a restart.
"""
from __future__ import annotations

import logging
import math
from enum import Enum
from typing import Any, Callable

import pandas as pd
from pydantic import Field

from goldbot.base import Record
from goldbot.config import RiskSettings, ThrottleSettings, tf_seconds
from goldbot.data.timeutil import floor_tf, risk_day
from goldbot.risk import sizing
from goldbot.risk.throttle import AgentThrottle

log = logging.getLogger("goldbot.risk")

MarginFn = Callable[[int, float, float], "float | None"]   # (side, lots, price) -> broker margin, None if unknown
REARM_PHRASE = "REARM"   # what rearm() expects; the engine passes it only for an owner re-arm seen on the bus


class Stage(str, Enum):
    NORMAL = "normal"
    SIZE_DOWN = "size_down"      # 8% drawdown: multiplier ceiling 0.5, risk per trade halved
    HALTED = "halted"            # 12%: no entries until /rearm + 10 days positive shadow + 30 days propose


class RiskLimits(Record):
    risk_per_trade: float = 0.005
    max_risk_per_trade: float = 0.01
    multiplier_bounds: tuple[float, float] = (0.25, 1.5)
    daily_cap: float = 0.02
    weekly_cap: float = 0.05
    dd_stage1: float = 0.08
    dd_stage2: float = 0.12
    dd_stage1_clear: float = 0.05
    max_positions: int = 2
    max_spread_points: float = 45.0
    point: float = 0.01
    stale_tick_seconds: int = 20
    # a tick stamped more than this far ahead of the host clock: wrong server_tz, broker clock or host clock (entries
    # only; exits are never gated). Equal to the engine's clock_skew rule (runner.SKEW_BLOCK_S, a test keeps them
    # equal), so a broker clock a few seconds fast is the skew rule's case and never refuses every entry on its own.
    max_tick_ahead_s: float = 10.0
    min_target_over_cost: float = 2.5
    margin_level_floor: float = 3.0      # 300%
    leverage_cap: float = 20.0           # FCA retail gold
    # combined exposure across all accounts, treated as one position: at most this many lots, and open notional at
    # most this fraction of what the combined equity can carry at the leverage cap (30% x 20 = 6x combined equity)
    max_combined_lots: float = 3.0
    max_combined_notional_frac: float = 0.30
    # account class (design: Account classifier): on Standard the 15m specialists except session-open are disabled
    # and the rest need an expected edge of at least this many round-trip costs; Unknown trades nothing (paper only)
    standard_edge_over_cost: float = 1.5
    # BACKLOG 25: below the minimum lot, exactly the minimum lot if its realised risk <= this (halved in size-down;
    # never above max_risk_per_trade); None = off (the 1.2x rule alone)
    min_lot_risk_cap: float | None = None
    # BACKLOG 29: absolute daily loss limit, account currency; entries refused when loss so far + this trade's stop
    # risk > min(daily_cap x day-start equity, this). None = off
    daily_loss_limit_abs: float | None = None
    # BACKLOG 21 (playbook G-6): per-agent loss-streak and entry-rate throttle; off by default (owner: thresholds)
    throttle: ThrottleSettings = Field(default_factory=ThrottleSettings)

    @classmethod
    def from_settings(cls, risk: RiskSettings, *, tiny_live: bool) -> RiskLimits:
        """The `risk:` section of settings.yaml; `tiny_live` selects the tiny-live phase's risk per trade. Tiny-live
        never takes the minimum-lot exception (trading-safety review MEDIUM-2: a 0.1% phase must not place a lot that
        risks up to 1%). The absolute daily loss limit is per account (accounts.yaml), set by ops.run.engine_limits."""
        return cls(risk_per_trade=risk.risk_per_trade_tiny_live if tiny_live else risk.risk_per_trade,
                   multiplier_bounds=risk.multiplier_bounds, daily_cap=risk.daily_cap, weekly_cap=risk.weekly_cap,
                   dd_stage1=risk.drawdown_stage1, dd_stage2=risk.drawdown_stage2,
                   dd_stage1_clear=risk.drawdown_stage1_clear, max_positions=risk.max_positions_per_account,
                   max_spread_points=risk.max_spread_points, stale_tick_seconds=risk.stale_tick_seconds,
                   min_target_over_cost=risk.min_target_over_cost,
                   min_lot_risk_cap=None if tiny_live else risk.min_lot_risk_cap, throttle=risk.throttle)


class AccountState(Record):
    equity: float
    balance_closed_hwm: float
    day_start_equity: float
    week_start_equity: float
    open_positions: int
    margin_used: float
    last_tick_age_s: float
    spread_points: float
    stage: Stage = Stage.NORMAL
    supervisor_halt: bool = False
    owner_halt: bool = False          # /halt from Telegram or the dashboard (state/control.json)
    in_blackout: bool = False
    dq_error: bool = False
    stale_bars: bool = False          # the last completed bar is more than one decision period old
    data_recovering: bool = False     # data was unhealthy within the last 60 s (design: reset after 60 s healthy)
    in_rollover: bool = False         # +-5 min of 00:00 broker server time
    weekend: bool = False             # from Friday 21:30 server until the week reopens
    open_lots: float = 0.0            # this account's open exposure
    open_notional: float = 0.0
    other_lots: float = 0.0           # the other accounts' exposure and equity, as the supervisor last published them
    other_notional: float = 0.0
    other_equity: float = 0.0
    combined_size_down: bool = False  # the supervisor's 8% combined drawdown stage
    account_class: str = "raw"        # raw | standard | unknown, from the classifier; a paper broker stays "raw"
    drift_halt: bool = False          # design: Drift and health -- system halt pending the owner's review
    tick_ahead_s: float = 0.0         # how far the last tick is stamped ahead of the host clock (0 when behind)
    clock_mismatch: bool = False      # the server-clock probe measured a > 5 min offset (goldbot/ops/server_clock.py)
    market_closed: bool = False       # the broker session table (D13) says the market is closed now
    tick_error: bool = False          # a tick raised in the engine; cleared after TICK_ERROR_CLEAR_TICKS clean ticks


class Intent(Record):
    agent_id: str
    side: int
    p: float
    target_atr: float
    stop_atr: float
    atr_usd: float            # ATR in price units ($/oz)
    cost_atr: float           # round-trip cost in ATR
    multiplier: float         # from allocator x bet sizing, before bounds
    price: float
    # contract terms: the engine passes the terminal's symbol_info (runner `_sizing_terms`); volume_max is also the
    # engine's per-order ceiling (the smaller of the broker's maximum and 2.0)
    contract_oz: float = 100.0
    volume_step: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 2.0
    stops_level_points: float = 0.0
    # BACKLOG 28: the deposit currency and the broker's conversion of USD P&L into it (account currency per USD,
    # from loss_per_lot); a non-USD account without it refuses entries (fx_conversion_unavailable)
    account_currency: str = "USD"
    acct_per_usd: float | None = None
    timeframe: str = ""       # the agent's decision timeframe and family, for the account-class rules
    family: str = ""
    has_target: bool = True   # False: a time-exit label spec (BarrierSpec.has_target); the barrier EV does not apply


class GateDecision(Record):
    allowed: bool
    lots: float = 0.0
    reasons: list[str] = Field(default_factory=list)   # why refused; on an allowed decision, notes (min_lot_exception)
    risk_fraction: float = 0.0
    stop_distance: float = 0.0
    margin_needed: float = 0.0     # account currency: the margin the level check used, max(broker figure, 1:20 figure)
    risk_amount: float = 0.0       # account currency lost at the stop by the allowed lots
    margin_note: str = ""          # which figure won, or why the broker's could not be used (fallback to 1:20)


# every number the entry check and the sizing read (October safety batch, HIGH): a limit written `if x > limit: refuse`
# lets NaN through, because every comparison with NaN is False. `acct_per_usd` may be None ("unknown": the explicit
# fx_conversion_unavailable refusal); `open_positions` is an int and cannot be NaN
_STATE_NUMBERS = ("equity", "balance_closed_hwm", "day_start_equity", "week_start_equity", "margin_used",
                  "last_tick_age_s", "spread_points", "open_lots", "open_notional", "other_lots", "other_notional",
                  "other_equity", "tick_ahead_s")
_INTENT_NUMBERS = ("p", "target_atr", "stop_atr", "atr_usd", "cost_atr", "multiplier", "price", "contract_oz",
                   "volume_step", "volume_min", "volume_max", "stops_level_points", "acct_per_usd")


def non_finite_fields(intent: Intent, st: AccountState) -> list[str]:
    """The state and intent fields the gate reads that are NaN or infinite (None is left to the explicit handling)."""
    bad = [f for f in _STATE_NUMBERS if not math.isfinite(getattr(st, f))]
    return bad + [f for f in _INTENT_NUMBERS
                  if getattr(intent, f) is not None and not math.isfinite(getattr(intent, f))]


def _family_has_target(intent: Intent) -> bool:
    """False when the intent's family (its `family`, else the prefix of its agent id `<family>-g<n>-<hash>`) declares a
    label spec without a reachable target, so a caller that forgot `Intent.has_target` is still refused. Unknown
    families are not judged here (the other checks apply)."""
    from goldbot.specialists import SPECIALISTS  # lazy: specialists import labels/features, not the gate
    family = intent.family or intent.agent_id.rsplit("-", 2)[0]
    cls = SPECIALISTS.get(family)
    if cls is None:
        return True
    try:
        return bool(cls().label_spec.has_target)
    except Exception:                                # a family that cannot build its default spec: fail closed
        return False


def broker_margin(broker: Any, symbol: str) -> MarginFn | None:
    """The gate's margin source for a broker: `broker.margin_required(symbol, side, lots, price)`, or None for a
    broker without the method (the gate then uses the 1:20 figure and records the fallback)."""
    fn = getattr(broker, "margin_required", None)
    if not callable(fn):
        return None
    return lambda side, lots, price: fn(symbol, side, lots, price)


class RiskGate:
    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or RiskLimits()

    # ---------------------------------------------------------------- drawdown stage machine
    def update_stage(self, st: AccountState) -> Stage:
        dd = 1 - st.equity / st.balance_closed_hwm if st.balance_closed_hwm > 0 else 0.0
        if st.stage == Stage.HALTED:
            return st.stage  # only /rearm clears it
        if dd >= self.limits.dd_stage2:
            st.stage = Stage.HALTED
        elif dd >= self.limits.dd_stage1:
            st.stage = Stage.SIZE_DOWN
        elif st.stage == Stage.SIZE_DOWN and dd < self.limits.dd_stage1_clear:
            st.stage = Stage.NORMAL
        return st.stage

    def rearm(self, st: AccountState, confirmation: str) -> bool:
        if confirmation != REARM_PHRASE:  # the owner's TOTP-verified re-arm (control.json rearm_seq) precedes this
            return False
        st.stage = Stage.NORMAL
        st.balance_closed_hwm = st.equity
        return True

    # ---------------------------------------------------------------- entry check + sizing
    def daily_loss_limit(self, st: AccountState) -> float:
        """The effective daily loss limit in the account currency (ADR 0004 section 3): min(daily_cap x day-start
        equity, daily_loss_limit_abs); the absolute alone before the day start is known, the percentage alone when the
        absolute is off. Published in the engine state and health."""
        pct = self.limits.daily_cap * st.day_start_equity if st.day_start_equity > 0 else math.inf
        cap = self.limits.daily_loss_limit_abs
        return min(pct, cap if cap is not None else math.inf)

    def min_lot_cap(self, size_down: bool) -> float | None:
        """The minimum-lot exception's cap on realised risk (BACKLOG 25): `min_lot_risk_cap`, clamped to the per-trade
        maximum (design Hard limits: 1%), halved in the 8% size-down stage; None when the exception is off."""
        cap = self.limits.min_lot_risk_cap
        if cap is None or not cap > 0:
            return None
        cap = min(cap, self.limits.max_risk_per_trade)
        return cap * sizing.SIZE_DOWN_RISK if size_down else cap

    def throttle_cooldown(self, timeframe: str) -> pd.Timedelta | None:
        """`throttle.cooldown_bars` bars of `timeframe`; None for a timeframe the config does not know."""
        try:
            bar_s = tf_seconds(timeframe)
        except ValueError:
            return None
        return pd.Timedelta(seconds=self.limits.throttle.cooldown_bars * bar_s)

    def throttle_cooldown_until(self, t: AgentThrottle) -> pd.Timestamp | None:
        """When the loss-streak block lifts: the last full loss's exit plus `throttle.cooldown_bars` bars of the agent's
        timeframe; None when unknown (no loss, or a timeframe the config does not know)."""
        cooldown = self.throttle_cooldown(t.timeframe)
        if t.last_loss_utc is None or cooldown is None:
            return None
        return pd.Timestamp(t.last_loss_utc) + cooldown

    def throttle_reasons(self, t: AgentThrottle | None, now_utc: pd.Timestamp | None) -> list[str]:
        """BACKLOG 21 (playbook G-6: "an agent makes no entries until ... Never affects exits"): `throttle_loss_streak`
        while the agent's streak of full losses is at least `throttle.loss_streak` and its cooldown has not passed (a
        qualifying shadow win has already ended the streak in `t`); `throttle_entry_rate` once it has made
        `max_entries_per_day_per_agent` entries this risk day; `throttle_unknown` when the engine could not derive the
        facts. Nothing when the throttle is off or no facts are given. Fails closed: without a clock or a known
        cooldown a streak keeps refusing."""
        cfg = self.limits.throttle
        if not cfg.enabled or t is None:
            return []
        if t.unknown:
            return ["throttle_unknown"]
        out = []
        if t.loss_streak >= cfg.loss_streak:
            until = self.throttle_cooldown_until(t)
            if now_utc is None or until is None or now_utc < until:
                out.append("throttle_loss_streak")
        if t.entries_today >= cfg.max_entries_per_day_per_agent:
            out.append("throttle_entry_rate")
        return out

    def margin_for(self, intent: Intent, lots: float, notional: float,
                   margin_required: MarginFn | None) -> tuple[float, str]:
        """Design (Hard limits): margin is "checked with order_calc_margin and against the FCA retail cap of 1:20".
        `notional` is in the account currency (BACKLOG 28), as is the broker's figure. The larger of the broker's
        figure and notional / 20 is used; when the broker cannot answer (no source, None, a non-finite or negative figure, or an error) the 1:20 figure is used and the reason recorded. Never smaller
        than 1:20, so a broker fault can only make the check stricter-or-equal, never fail open."""
        cap = notional / self.limits.leverage_cap
        if margin_required is None:
            return cap, "fallback 1:20: no broker margin source"
        raw: Any = None
        try:
            raw = margin_required(intent.side, lots, intent.price)
            broker = float(raw) if raw is not None else math.nan
        except Exception as exc:                      # bridge down, terminal error, junk: fall back, never fail open
            why = f"fallback 1:20: broker margin failed ({type(exc).__name__}: {exc})"
            log.warning("%s", why)
            return cap, why
        if not math.isfinite(broker) or broker < 0:
            why = f"fallback 1:20: broker margin unusable ({raw!r})"
            log.warning("%s", why)
            return cap, why
        if broker > cap:
            return broker, f"broker {broker:.2f} > 1:20 {cap:.2f}"
        return cap, f"1:20 {cap:.2f} >= broker {broker:.2f}"

    def check(self, intent: Intent, st: AccountState, now_utc: pd.Timestamp | None = None,
              margin_required: MarginFn | None = None, throttle: AgentThrottle | None = None) -> GateDecision:
        """`margin_required(side, lots, price)` is the broker's margin figure (`broker_margin(broker, symbol)`);
        without it the margin check uses the 1:20 figure alone and says so in `margin_note`. `throttle` is the
        intent's agent's throttle facts (BACKLOG 21), judged at `now_utc` (`throttle_reasons`).

        A non-finite number anywhere in the state or the intent refuses first (`non_finite_state`, then
        `non_finite:<field>` for each), before the stage machine or any limit reads it: the gate fails closed, never
        open, on state it cannot compare. Entries only; exits never call the gate."""
        bad = non_finite_fields(intent, st)
        if bad:
            log.warning("entry refused: non-finite state %s", bad)
            return GateDecision(allowed=False, reasons=["non_finite_state", *(f"non_finite:{f}" for f in bad)])
        L = self.limits
        reasons: list[str] = []
        self.update_stage(st)
        if st.supervisor_halt:
            reasons.append("supervisor_halt")
        if st.owner_halt:
            reasons.append("owner_halt")
        if st.stage == Stage.HALTED:
            reasons.append("drawdown_halt")
        if st.dq_error:
            reasons.append("data_quality_error")
        if st.in_blackout:
            reasons.append("news_blackout")
        if st.in_rollover:
            reasons.append("rollover")
        if st.weekend:
            reasons.append("weekend")
        if st.drift_halt:
            reasons.append("drift_system_halt")
        if st.last_tick_age_s > L.stale_tick_seconds or st.stale_bars:
            reasons.append("stale_data")
        if st.tick_ahead_s > L.max_tick_ahead_s:
            reasons.append("tick_from_future")
        if st.clock_mismatch:
            reasons.append("server_clock_mismatch")
        if st.market_closed:
            reasons.append("market_closed")
        if st.tick_error:
            reasons.append("tick_error")
        if st.data_recovering:
            reasons.append("data_recovering")
        if st.spread_points > L.max_spread_points:
            reasons.append("spread_too_wide")
        if st.open_positions >= L.max_positions:
            reasons.append("max_positions")
        reasons += self.throttle_reasons(throttle, now_utc)
        day_loss = 1 - st.equity / st.day_start_equity if st.day_start_equity else 0.0
        week_loss = 1 - st.equity / st.week_start_equity if st.week_start_equity else 0.0
        if day_loss >= L.daily_cap:
            reasons.append("daily_cap")
        if week_loss >= L.weekly_cap:
            reasons.append("weekly_cap")
        # a time-exit rule has no reachable target: p x target would be fiction (quant review H1). Fail closed until the
        # EV uses the time-exit outcome (HANDOFF: what must change before live)
        if not intent.has_target or not _family_has_target(intent):
            reasons.append("time_exit_ev_unsupported")
        # expected value and minimum target over cost
        ev = intent.p * intent.target_atr - (1 - intent.p) * intent.stop_atr - intent.cost_atr
        if ev <= 0:
            reasons.append("negative_ev")
        if intent.target_atr < L.min_target_over_cost * intent.cost_atr:
            reasons.append("target_below_cost_floor")
        if st.account_class == "unknown":
            reasons.append("account_class_unknown")
        elif st.account_class == "standard":
            if intent.timeframe == "15m" and intent.family != "session_open":
                reasons.append("standard_account_15m")
            edge = intent.p * intent.target_atr - (1 - intent.p) * intent.stop_atr      # gross, before costs
            if edge <= L.standard_edge_over_cost * intent.cost_atr:            # design: must exceed 1.5x
                reasons.append("standard_account_edge")
        if reasons:
            return GateDecision(allowed=False, reasons=reasons)

        # sizing, in the account currency (design Sizing; BACKLOG 28): the stop is USD per oz, so one lot's risk is
        # stop x contract x fx, fx being the broker's account currency per USD. The arithmetic is goldbot/risk/sizing.py
        fx = sizing.account_fx(intent.account_currency, intent.acct_per_usd)
        if fx is None:                       # a non-USD account without a usable conversion: never sized on a guess
            return GateDecision(allowed=False, reasons=["fx_conversion_unavailable"])
        # per-trade risk is capped after the multiplier (design Hard limits: 1% max, clamped, not rejected)
        size_down = st.stage == Stage.SIZE_DOWN or st.combined_size_down
        eff = sizing.effective_risk(L, size_down, intent.multiplier)
        stop_distance = sizing.stop_distance(intent.stop_atr, intent.atr_usd, stops_level_points=intent.stops_level_points,
                                             spread_points=st.spread_points, point=L.point)
        sz = sizing.size_lots(equity=st.equity, risk=eff, stop_distance=stop_distance, contract_oz=intent.contract_oz,
                              volume_min=intent.volume_min, volume_step=intent.volume_step,
                              volume_max=intent.volume_max, acct_per_usd=fx)
        lots, realised_risk = sz.lots, sz.realised_risk
        notes: list[str] = []
        if sz.refused:
            # minimum-lot exception (BACKLOG 25, ADR 0004): exactly the minimum lot, only within the cap (half of it
            # in size-down), never above the per-trade maximum; every check below still applies to it
            cap = self.min_lot_cap(size_down)
            lots = intent.volume_min
            realised_risk = lots * sz.risk_per_lot / st.equity
            if cap is None or lots > intent.volume_max or realised_risk > cap:
                return GateDecision(allowed=False, reasons=["min_lot_exceeds_risk"], stop_distance=stop_distance)
            notes = ["min_lot_exception"]
        notional = lots * intent.contract_oz * intent.price * fx          # account currency
        # margin: the stricter of the broker's order_calc_margin and the FCA cap (1:20); level must stay >= 300%
        margin_needed, margin_note = self.margin_for(intent, lots, notional, margin_required)
        level_after = st.equity / max(st.margin_used + margin_needed, 1e-9)
        if level_after < L.margin_level_floor:
            return GateDecision(allowed=False, reasons=["margin_level_floor"], stop_distance=stop_distance,
                                margin_needed=margin_needed, margin_note=margin_note)
        # combined exposure cap: both accounts trade one instrument, so their positions are one position. Open notional
        # is published in USD (lots x contract x price); converted here so it meets equity in the account currency
        # (the supervisor halts accounts of mixed currencies, so other_equity is in this one)
        comb_lots = st.open_lots + st.other_lots + lots
        comb_notional = (st.open_notional + st.other_notional) * fx + notional
        if comb_lots > L.max_combined_lots + 1e-9 or \
                comb_notional > L.max_combined_notional_frac * L.leverage_cap * (st.equity + st.other_equity):
            return GateDecision(allowed=False, reasons=["combined_exposure_cap"], stop_distance=stop_distance,
                                margin_needed=margin_needed, margin_note=margin_note)
        # absolute daily loss limit (BACKLOG 29, ADR 0004): projected with this trade's full stop risk, entries only.
        # Strict boundary (review LOW-3): refused only when the projection EXCEEDS the limit; exactly equal is allowed
        # (1e-9 absorbs float noise). The supervisor, which sees no trade, halts when the loss so far REACHES it
        risk_amount = lots * sz.risk_per_lot
        if L.daily_loss_limit_abs is not None:
            lost = max(0.0, st.day_start_equity - st.equity)
            if lost + risk_amount > self.daily_loss_limit(st) + 1e-9:
                return GateDecision(allowed=False, reasons=["daily_loss_limit_abs"], stop_distance=stop_distance,
                                    margin_needed=margin_needed, margin_note=margin_note)
        return GateDecision(allowed=True, lots=round(lots, 2), reasons=notes, risk_fraction=realised_risk,
                            stop_distance=stop_distance, margin_needed=margin_needed, margin_note=margin_note,
                            risk_amount=risk_amount)


def _risk_week(ts: pd.Timestamp) -> pd.Timestamp:
    return floor_tf(pd.DatetimeIndex([ts]), 7 * 86400)[0]      # weeks start Sunday 00:00 UTC (gold opens Sunday)


def new_day(st: AccountState, now_utc: pd.Timestamp, last_reset: pd.Timestamp | None) -> bool:
    """Reset the day-start equity at the risk-day boundary (00:00 UTC), and the week-start equity when the reset
    also crosses into a new risk week. Returns True when a reset happened (the caller persists `now_utc`)."""
    if last_reset is None or risk_day(pd.DatetimeIndex([now_utc]))[0] != risk_day(pd.DatetimeIndex([last_reset]))[0]:
        st.day_start_equity = st.equity
        if last_reset is None or _risk_week(now_utc) != _risk_week(last_reset):
            st.week_start_equity = st.equity
        return True
    return False
