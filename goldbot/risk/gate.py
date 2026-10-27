"""RiskGate: hard limits no model output can override. Sits between the allocator and the broker.

Per-account limits (daily 2% / weekly 5% of own equity; staged drawdown 8% size-down, 12% stop), plus
the supervisor applies tighter limits on combined equity (1.5% / 4%) and writes a HALT flag.
All state is explicit and serialisable so the engine can reconcile after a restart.
"""
from __future__ import annotations

import json
import logging
import math
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal

import pandas as pd
from pydantic import Field

from goldbot.base import Record
from goldbot.config import (
    HeatSettings,
    HolidaySettings,
    ReopenSettings,
    RiskSettings,
    ThrottleSettings,
    Tier2Blackout,
    tf_seconds,
)
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
    # risk + its round-trip cost and swap (BACKLOG 36) > min(daily_cap x day-start equity, this). None = off
    daily_loss_limit_abs: float | None = None
    # BACKLOG 21 (playbook G-6): per-agent loss-streak and entry-rate throttle; off by default (owner: thresholds)
    throttle: ThrottleSettings = Field(default_factory=ThrottleSettings)
    # BACKLOG 19 (playbook G-4): open-risk heat cap and same-direction cap; off by default (owner: values)
    heat: HeatSettings = Field(default_factory=HeatSettings)
    # BACKLOG 35: the absolute daily projection also counts open positions' remaining stop risk; off by default
    project_open_risk: bool = False
    # BACKLOG 44 (ADR 0016): a 12% equity read trips the kill switch at once only when the balance plus the open P&L
    # agrees with it within this fraction of the high-water mark; otherwise the next account read must confirm it
    equity_check_tolerance: float = 0.02
    # ...and a pending 12% read clears only after every read stayed below dd_stage2 - tolerance this long (seconds)
    equity_recovery_s: float = 60.0
    # BACKLOG 54 (ADR 0018): a rise in balance_closed_hwm needs the deal history or a second balance read to agree with
    # it within this fraction of the mark (goldbot/risk/hwm.py)
    hwm_rise_tolerance: float = 0.001
    # BACKLOG 17/18 (playbook G-2, G-3): entry windows around tier-2 events, after reopens and on thin-liquidity days;
    # off by default (the owner adopts them). The engine raises the AccountState flags; check() refuses only when on
    tier2: Tier2Blackout = Field(default_factory=Tier2Blackout)
    reopen: ReopenSettings = Field(default_factory=ReopenSettings)
    holidays: HolidaySettings = Field(default_factory=HolidaySettings)
    # dynamic limits (docs/design/dynamic-limits-2026-10-19.md; set only by engine/limits_apply.py, behind the
    # account's `dynamic_limits` flag): the day's risk per trade by model version, before the multiplier (a version
    # not here, or a value that is not a finite number > 0, trades `risk_per_trade`), and the same-day tighten-only
    # scale k_vol x k_dd x k_day in (0, 1]. "Dynamic values are only ever <= the ceilings": a version's value is
    # clamped to `max_risk_per_trade` here whatever the engine sends. Empty and 1.0: today's fixed values
    strategy_risk: dict[str, float] = Field(default_factory=dict)
    risk_scale: float = Field(1.0, gt=0, le=1.0)
    # exploration arms (plan 2026-10-25, lane L1): the account's own facts, from config/accounts.yaml, never from an
    # intent. `probe_only` is the account's flag (ADR 0037 amendment 4); `exploration_arms` maps each registered arm id
    # to its per-trade loss cap at the stop, account currency (`accounts.exploration_arm_caps`: empty unless the
    # account's `exploration.enabled`). Defaults: no account explores, so the purpose `exploration` is always refused
    probe_only: bool = False
    exploration_arms: dict[str, float] = Field(default_factory=dict)

    @property
    def dynamic(self) -> bool:
        """Whether any dynamic term is in force (False: every decision is the fixed-limits one)."""
        return bool(self.strategy_risk) or self.risk_scale != 1.0

    def risk_source(self, version: str | None) -> str:
        """Where `risk_for(version)` comes from: `strategy:<version>` for a usable per-strategy value, else `base`."""
        v = self.strategy_risk.get(version) if version else None
        return f"strategy:{version}" if v is not None and math.isfinite(v) and v > 0 else "base"

    def risk_for(self, version: str | None) -> float:
        """The risk per trade (before the multiplier) an entry of model `version` is sized at: the version's dynamic
        value clamped to the per-trade maximum (design Hard limits: 1%), else `risk_per_trade`; then x `risk_scale`
        (only ever tightens). With no dynamic term it is exactly `risk_per_trade`."""
        if self.risk_source(version) == "base":
            r = self.risk_per_trade
        else:
            r = min(self.strategy_risk[version or ""], self.max_risk_per_trade)
        return r * self.risk_scale

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
                   min_lot_risk_cap=None if tiny_live else risk.min_lot_risk_cap, throttle=risk.throttle,
                   heat=risk.heat, project_open_risk=risk.project_open_risk,
                   equity_check_tolerance=risk.equity_check_tolerance, equity_recovery_s=risk.equity_recovery_s,
                   hwm_rise_tolerance=risk.hwm_rise_tolerance,
                   tier2=risk.blackout.tier2, reopen=risk.reopen, holidays=risk.holidays)


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
    # accounts.yaml `enabled: false` with this engine's positions still open: it only winds down (services.engine
    # disabled_wind_down); every entry is refused `account_disabled`, exits are never gated
    account_disabled: bool = False
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
    # BACKLOG 17/18 entry windows (goldbot/risk/windows.py), raised by the engine only for windows that are enabled
    tier2_blackout: bool = False      # inside -before/+after of a tier-2 USD event (risk.blackout.tier2)
    reopen_window: bool = False       # just after the daily reopen or the weekly open (risk.reopen)
    holiday_thin: bool = False        # a thin-liquidity server date of the holiday list (risk.holidays)
    calendar_unavailable: bool = False  # an enabled calendar window has no calendar to judge by (fail closed)
    bridge_incompatible: bool = False  # the MT5 bridge lacks a method, protocol or zone this brain needs (BACKLOG 42)
    # BACKLOG 72 (trading-safety condition 1, the refusal path): an order the broker neither filled nor refused is still
    # unresolved, so its exposure is unknown; entries are refused `unconfirmed_order_pending` until it is settled
    unconfirmed_order: bool = False
    # BACKLOG 19/35: what this account's open positions can still lose down to their stops, account currency
    # (goldbot/risk/open_risk.py); None = unknown (not read yet, a position without SL, or no usable conversion; review
    # LOW-2: unknown until the engine has read the positions), and the positions on each side, whoever opened them
    open_risk: float | None = None
    open_long: int = 0
    open_short: int = 0
    # BACKLOG 44 (ADR 0016): a 12% equity read is pending confirmation (the engine sets it until the next 12% read
    # confirms it or the reads stay below 12% minus the tolerance for equity_recovery_s). The stage machine does not
    # run and entries are refused `equity_unconfirmed`; exits are never gated
    equity_unconfirmed: bool = False


class Intent(Record):
    agent_id: str
    side: int
    p: float
    target_atr: float
    stop_atr: float
    atr_usd: float            # ATR in price units ($/oz)
    cost_atr: float           # round-trip cost in ATR (spread, slippage, commission: the engine's `_cost_atr`)
    # BACKLOG 36: the paid swap per oz (USD) the position would be charged at a broker rollover before the risk day
    # ends (engine `_swap_usd_per_oz`, ADR 0017's rule); 0 = none. Only the daily projection reads it
    swap_usd_per_oz: float = 0.0
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
    # owner 2026-10-19 (the demo account is for learning and testing strategies; ADR 0037 amendment, research and
    # program directors' ruling): a demo cost probe is not judged on the edge AFTER costs. Set only by
    # goldbot/engine/cost_probe.py after its demo lock. The gate checks the lock again itself (`account_mode` demo and
    # no live gate in `phase_file`, as F1 for operational orders, else `learning_probe_not_demo`), requires the minimum
    # lot (`learning_probe_not_min_lot`) and a positive edge BEFORE costs (`negative_gross_edge`); it skips only
    # negative_ev, target_below_cost_floor and the standard-account edge rules. time_exit_ev_unsupported, every loss
    # limit, heat, margin, exposure, throttle and account check still apply
    learning_probe: bool = False
    account_mode: str | None = None   # the registry's (accounts.yaml); read only for a learning probe
    phase_file: Path | None = None    # state/phase_state.json; read only for a learning probe
    # ADR 0037 Amendment 5 (owner-approved 2026-10-20, "Allow, demo only"): a scheduled cost probe has no signal, so
    # the purpose `cost_measurement` skips ONLY the expected-value requirement (`time_exit_ev_unsupported`,
    # `negative_gross_edge`), and only inside its fence (`_cost_measurement_reasons`): a learning probe, the demo lock,
    # a terminal that itself reports demo, the minimum lot, a stop of at most COST_MEASUREMENT_MAX_STOP_USD, a planned
    # hold of at most COST_MEASUREMENT_MAX_HOLD_S and the loss at the stop within both probe budgets. Every account
    # limit, halt, window and sizing limit still applies. Set only by goldbot/engine/cost_probe.py (`_send_scheduled`)
    #
    # Exploration arms (plan 2026-10-25, lane L1): an arm trades to MEASURE an edge, so the purpose `exploration` skips
    # ONLY the expected-value and gross-edge rules (`time_exit_ev_unsupported`, `negative_ev`,
    # `target_below_cost_floor`, `standard_account_15m`, `standard_account_edge`), and only inside its fence
    # (`_exploration_reasons`, `_exploration_sized`): a probe_only account (`RiskLimits.probe_only`), the demo lock
    # (`account_mode` demo and no live gate in `phase_file`), a terminal that itself reports demo
    # (`terminal_trade_mode`), exactly the minimum lot, an `arm_id` registered in `RiskLimits.exploration_arms` and the
    # loss at the gate's stop within that arm's cap. Outside the fence every edge rule applies again. Halts, windows,
    # every loss cap, heat, margin, exposure, throttle and sizing limit still apply
    purpose: Literal["signal", "cost_measurement", "exploration"] = "signal"
    arm_id: str | None = None                # the exploration arm (registered in the account's config); exploration only
    terminal_trade_mode: str | None = None   # MT5 account_info().trade_mode at the order; cost_measurement, exploration
    hold_s: float | None = None              # the planned hold, seconds; cost_measurement only
    # account currency: what the probes may still lose today (the cost-probe budget) and the dynamic per-probe ceiling
    # (`probe_limits`); cost_measurement only, None refuses
    probe_headroom: float | None = None
    probe_max_loss: float | None = None
    # the live model version the intent comes from (runner `live_versions`): the key of `RiskLimits.strategy_risk`;
    # None (unknown) trades the base risk per trade
    model_version: str | None = None


class GateDecision(Record):
    allowed: bool
    lots: float = 0.0
    reasons: list[str] = Field(default_factory=list)   # why refused; on an allowed decision, notes (min_lot_exception)
    risk_fraction: float = 0.0
    stop_distance: float = 0.0
    margin_needed: float = 0.0     # account currency: the margin the level check used, max(broker figure, 1:20 figure)
    risk_amount: float = 0.0       # account currency lost at the stop by the allowed lots
    cost_amount: float = 0.0       # account currency: the round trip (cost_atr) and swap of those lots (BACKLOG 36)
    margin_note: str = ""          # which figure won, or why the broker's could not be used (fallback to 1:20)
    # dynamic limits: the risk per trade (before the multiplier) the entry was sized at and where it came from
    # (`RiskLimits.risk_source`); None and "" while no dynamic term is in force (today's fixed values)
    risk_per_trade: float | None = None
    risk_source: str = ""


class OperationalIntent(Record):
    """ADR 0021: an operational order (the bridge smoke, goldbot/ops/smoke.py), checked by `check_operational`. No p,
    target, cost, multiplier or lots: the gate sizes it at the minimum lot and sets its stop itself (F2). The contract
    terms are the terminal's symbol_info, as for `Intent`. `account_mode` is the registry's (accounts.yaml) and
    `phase_file` the roadmap's phase state (state/phase_state.json), which the gate reads itself (F1)."""
    side: Literal[1, -1]
    price: float
    contract_oz: float = 100.0
    volume_step: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 2.0
    stops_level_points: float = 0.0
    account_currency: str = "USD"
    acct_per_usd: float | None = None
    account_mode: str
    phase_file: Path


# ADR 0021 (F2): an operational stop is never closer than this (USD per oz), nor than the stops level plus the spread.
# 0.01 lot x 100 oz x $1.00 = $1 at the stop: the smoke's whole risk
OPERATIONAL_MIN_STOP_USD = 1.0
# ADR 0021 F1 (trading-safety INFO-6): any of these recorded refuses an operational order, not only the first, so a
# phase file edited by hand cannot reopen it. The roadmap gates from paper_to_tiny_live on (goldbot/ops/accounts.py
# GATES; tests/test_risk_gate_split.py keeps the two equal)
LIVE_GATES = frozenset({"paper_to_tiny_live", "tiny_live_to_full_size"})


def _learning_probe_reasons(intent: Intent, *, gross_edge: bool = True) -> list[str]:
    """The learning probe's own fence (see `Intent.learning_probe`): demo by the registry and the phase file (F1's
    rule: an unreadable phase file or a missing one on a non-demo account refuses), the minimum lot only, and a
    positive gross edge (p x target above (1 - p) x stop, before costs) unless `gross_edge` is False (a
    cost-measurement probe, `_cost_measurement_reasons`)."""
    out: list[str] = []
    gates = phase_gates(intent.phase_file) if intent.account_mode == "demo" and intent.phase_file is not None else None
    if gates is None or LIVE_GATES & set(gates):
        out.append("learning_probe_not_demo")
    if intent.volume_max > intent.volume_min + 1e-9:
        out.append("learning_probe_not_min_lot")
    if gross_edge and intent.p * intent.target_atr - (1 - intent.p) * intent.stop_atr <= 0:
        out.append("negative_gross_edge")
    return out


# ADR 0037 Amendment 5 (prereg docs/research/prereg-cost-measurement-2026-10.md, section 3: "a stop of at most $3 from
# the fill", "a hold <= 60 s, closed by the engine"): the cost-measurement fence's bounds
COST_MEASUREMENT_MAX_STOP_USD = 3.0
COST_MEASUREMENT_MAX_HOLD_S = 60.0


def _ok_amount(v: float | None) -> bool:
    return v is not None and math.isfinite(v) and v > 0


def _cost_measurement_reasons(intent: Intent) -> list[str]:
    """The cost-measurement purpose's fence before sizing (ADR 0037 Amendment 5, see `Intent.purpose`): only a learning
    probe may claim it (`cost_measurement_not_probe`); the learning probe's demo lock and minimum lot; a terminal that
    itself reports demo, never inferred from a label (`cost_measurement_terminal_not_demo`); the stop asked for within
    COST_MEASUREMENT_MAX_STOP_USD (`cost_measurement_stop_above_cap`; a missing stop is `invalid:stop_atr`); a planned
    hold in (0, COST_MEASUREMENT_MAX_HOLD_S] (`cost_measurement_hold`); both probe budgets known and positive
    (`cost_measurement_budget`). `check` repeats the stop and budget checks on the sized order."""
    out = [] if intent.learning_probe else ["cost_measurement_not_probe"]
    out += _learning_probe_reasons(intent, gross_edge=False)
    if intent.terminal_trade_mode != "demo":
        out.append("cost_measurement_terminal_not_demo")
    if intent.stop_atr * intent.atr_usd > COST_MEASUREMENT_MAX_STOP_USD + 1e-9:
        out.append("cost_measurement_stop_above_cap")
    if not _ok_amount(intent.hold_s) or (intent.hold_s or 0.0) > COST_MEASUREMENT_MAX_HOLD_S:
        out.append("cost_measurement_hold")
    if not (_ok_amount(intent.probe_headroom) and _ok_amount(intent.probe_max_loss)):
        out.append("cost_measurement_budget")
    return out


def _cost_measurement_sized(intent: Intent, d: GateDecision) -> list[str]:
    """The fence on the sized order: exactly the minimum lot, the stop the gate set (stops level and spread floored)
    within COST_MEASUREMENT_MAX_STOP_USD, and its loss at the stop within both probe budgets."""
    out: list[str] = []
    if abs(d.lots - intent.volume_min) > 1e-9:
        out.append("learning_probe_not_min_lot")
    if d.stop_distance > COST_MEASUREMENT_MAX_STOP_USD + 1e-9:
        out.append("cost_measurement_stop_above_cap")
    if d.risk_amount > min(intent.probe_headroom or 0.0, intent.probe_max_loss or 0.0) + 1e-9:
        out.append("cost_measurement_budget")
    return out


def _exploration_reasons(intent: Intent, limits: RiskLimits) -> list[str]:
    """The exploration purpose's fence before sizing (see `Intent.purpose`): a probe_only account
    (`exploration_not_probe_only`); the demo lock by the registry and the phase file, F1's rule
    (`exploration_not_demo`); a terminal that itself reports demo, never inferred from a label
    (`exploration_terminal_not_demo`); the minimum lot only (`exploration_not_min_lot`); an arm id registered with a
    usable cap (`exploration_arm_unregistered`). `check` judges the arm's cap on the sized order."""
    out = [] if limits.probe_only else ["exploration_not_probe_only"]
    gates = phase_gates(intent.phase_file) if intent.account_mode == "demo" and intent.phase_file is not None else None
    if gates is None or LIVE_GATES & set(gates):
        out.append("exploration_not_demo")
    if intent.terminal_trade_mode != "demo":
        out.append("exploration_terminal_not_demo")
    if intent.volume_max > intent.volume_min + 1e-9:
        out.append("exploration_not_min_lot")
    if not intent.arm_id or not _ok_amount(limits.exploration_arms.get(intent.arm_id)):
        out.append("exploration_arm_unregistered")
    return out


def _exploration_sized(intent: Intent, limits: RiskLimits, d: GateDecision) -> list[str]:
    """The fence on the sized order: exactly the minimum lot, and the loss at the stop the gate set (stops level and
    spread floored) within the arm's per-trade cap; equal is allowed (1e-9 absorbs float noise)."""
    out: list[str] = []
    if abs(d.lots - intent.volume_min) > 1e-9:
        out.append("exploration_not_min_lot")
    if d.risk_amount > limits.exploration_arms.get(intent.arm_id or "", 0.0) + 1e-9:
        out.append("exploration_arm_cap")
    return out


def phase_gates(path: Path) -> list[str] | None:
    """The roadmap gates recorded in the phase file (`gates_passed`); [] when the file does not exist (phase 0); None
    when it cannot be read or is not the shape `accounts.record_gate` writes (F1: an unreadable phase file refuses)."""
    if not path.exists():
        return []
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    gates = d.get("gates_passed", []) if isinstance(d, dict) else None
    if not isinstance(gates, list) or not all(isinstance(g, str) for g in gates):
        return None
    return gates


# every number the entry check and the sizing read (October safety batch, HIGH; trading-safety review MEDIUM-3): a limit
# written `if x > limit: refuse` lets NaN through, because every comparison with NaN is False, and a zero or negative
# price, ATR, stop, contract or volume makes the sizing meaningless. `acct_per_usd` may be None ("unknown": the
# explicit fx_conversion_unavailable refusal); `open_positions` is an int and cannot be NaN
_STATE_NUMBERS = ("equity", "balance_closed_hwm", "day_start_equity", "week_start_equity", "margin_used",
                  "last_tick_age_s", "spread_points", "open_lots", "open_notional", "other_lots", "other_notional",
                  "other_equity", "tick_ahead_s")
_INTENT_NUMBERS = ("p", "target_atr", "stop_atr", "atr_usd", "cost_atr", "multiplier", "price", "contract_oz",
                   "volume_step", "volume_min", "volume_max", "stops_level_points", "acct_per_usd", "swap_usd_per_oz")
_POSITIVE_STATE = ("equity",)
_POSITIVE_INTENT = ("price", "atr_usd", "stop_atr", "contract_oz", "volume_step", "volume_min", "volume_max")
_OPERATIONAL_NUMBERS = ("price", "contract_oz", "volume_step", "volume_min", "volume_max", "stops_level_points",
                        "acct_per_usd")
_POSITIVE_OPERATIONAL = ("price", "contract_oz", "volume_step", "volume_min", "volume_max")


# BACKLOG 43: the refusals check() can only reach once it has sized the trade (they need the intent's stop, price and
# conversion). The engine publishes the last one per agent (`entry_refusals` in engine_<account>.json); every other
# refusal is an account reason (`RiskGate.account_reasons`, published as `entry_blocks`) or the intent's own filtering
# (negative EV, cost floor, account class rules, same-direction cap), which is not published
PER_TRADE_REFUSALS = frozenset({"fx_conversion_unavailable", "min_lot_exceeds_risk", "margin_level_floor",
                                "combined_exposure_cap", "daily_loss_limit_abs", "heat_cap"})


def _bad(obj: Any, f: str, positive: tuple[str, ...]) -> bool:
    v = getattr(obj, f)
    return v is not None and (not math.isfinite(v) or (f in positive and not v > 0))


def invalid_state_fields(st: AccountState) -> list[str]:
    """The account state fields the gate reads that it cannot compare (as `invalid_fields`)."""
    return [f for f in _STATE_NUMBERS if _bad(st, f, _POSITIVE_STATE)]


def invalid_fields(intent: Intent, st: AccountState) -> list[str]:
    """The state and intent fields the gate reads that are NaN or infinite, or not > 0 where only a positive number
    means anything (`_POSITIVE_*`). None is left to the explicit handling."""
    return invalid_state_fields(st) + [f for f in _INTENT_NUMBERS if _bad(intent, f, _POSITIVE_INTENT)]


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
        """The drawdown stage on this equity read: 8% size-down, 12% halted (only /rearm clears it). An unconfirmed
        read (ADR 0016: "one disagreeing read never trips the kill switch") moves no stage."""
        if st.equity_unconfirmed:
            return st.stage
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
        cap *= self.limits.risk_scale              # dynamic limits: the same-day cuts tighten the exception too (1.0: off)
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

    def window_reasons(self, st: AccountState) -> list[str]:
        """BACKLOG 17 (playbook G-2: "no entries on those days. No entries for 15 min after each daily reopen and 30
        min after the weekly open ... Exits are untouched") and BACKLOG 18 (G-3: "a -5/+15 min entry blackout from
        config"): `tier2_blackout`, `reopen_window`, `holiday_thin`, and `calendar_unavailable` when an enabled
        calendar window (tier 2, holidays) has no calendar to judge by. A window that is off adds nothing whatever the
        flags say, so with all three off every decision is what it was before they existed."""
        L = self.limits
        out = []
        if L.tier2.enabled and st.tier2_blackout:
            out.append("tier2_blackout")
        if L.reopen.enabled and st.reopen_window:
            out.append("reopen_window")
        if L.holidays.enabled and st.holiday_thin:
            out.append("holiday_thin")
        if (L.tier2.enabled or L.holidays.enabled) and st.calendar_unavailable:
            out.append("calendar_unavailable")
        return out

    def margin_for(self, intent: Intent | OperationalIntent, lots: float, notional: float,
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

    def account_reasons(self, st: AccountState, now_utc: pd.Timestamp | None = None,
                        throttle: AgentThrottle | None = None) -> list[str]:
        """BACKLOG 43 (ADR 0011: the engine publishes "the gate's account-level reasons at each refresh, including
        rollover and the weekend cut"): every reason `check()` refuses an entry for that does not depend on the intent,
        in the order `check()` gives them. `check()` starts from this list, so the two cannot drift; the engine
        publishes it as `entry_blocks`. `throttle` is one agent's facts (BACKLOG 21), judged at `now_utc`: the account's
        own list leaves it out (None), and the engine publishes the per-agent part under `throttle`.

        Reads only: the stage machine runs in `check()` and in the engine's account refresh, never here. A state number
        the gate cannot compare (`invalid_state_fields`) ends the list with `invalid_state` and `invalid:<field>`, and
        the loss caps, which read those numbers, are not judged on it. Entries only; exits never call the gate."""
        L = self.limits
        reasons: list[str] = []
        if st.supervisor_halt:
            reasons.append("supervisor_halt")
        if st.owner_halt:
            reasons.append("owner_halt")
        if st.account_disabled:
            reasons.append("account_disabled")
        if st.stage == Stage.HALTED:
            reasons.append("drawdown_halt")
        if st.equity_unconfirmed:
            reasons.append("equity_unconfirmed")
        if st.dq_error:
            reasons.append("data_quality_error")
        if st.in_blackout:
            reasons.append("news_blackout")
        reasons += self.window_reasons(st)
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
        if st.bridge_incompatible:
            reasons.append("bridge_incompatible")
        if st.unconfirmed_order:
            reasons.append("unconfirmed_order_pending")
        if st.data_recovering:
            reasons.append("data_recovering")
        if st.spread_points > L.max_spread_points:
            reasons.append("spread_too_wide")
        if st.open_positions >= L.max_positions:
            reasons.append("max_positions")
        reasons += self.throttle_reasons(throttle, now_utc)
        bad = invalid_state_fields(st)
        if not bad:
            day_loss = 1 - st.equity / st.day_start_equity if st.day_start_equity else 0.0
            week_loss = 1 - st.equity / st.week_start_equity if st.week_start_equity else 0.0
            if day_loss >= L.daily_cap:
                reasons.append("daily_cap")
            if week_loss >= L.weekly_cap:
                reasons.append("weekly_cap")
        if st.account_class == "unknown":
            reasons.append("account_class_unknown")
        # BACKLOG 19/35: the heat cap, or the projection with an absolute daily limit, needs the open risk; unknown, or not
        # a finite amount >= 0, refuses (fail closed). Nothing while both are off, so the fact is never read then
        needs = L.heat.enabled or (L.project_open_risk and L.daily_loss_limit_abs is not None)
        if needs and (st.open_risk is None or not math.isfinite(st.open_risk) or st.open_risk < 0):
            reasons.append("open_risk_unknown")
        if bad:
            reasons.append("invalid_state")
            reasons += [f"invalid:{f}" for f in bad]
        return reasons

    def _account_gate(self, st: AccountState, bad: list[str], now_utc: pd.Timestamp | None,
                      throttle: AgentThrottle | None) -> tuple[list[str], GateDecision | None]:
        """The start both entry checks share (ADR 0021: one code path for every account check): the stage machine on a
        comparable state, then `account_reasons`. A number outside its domain (`bad`) ends it with the refusal
        (`invalid_state`, then `invalid:<field>`); the stage machine does not run on it."""
        if not bad:
            self.update_stage(st)
        reasons = self.account_reasons(st, now_utc, throttle)
        if not bad:
            return reasons, None
        log.warning("entry refused: invalid state %s", bad)
        if "invalid_state" not in reasons:
            reasons.append("invalid_state")
        return reasons, GateDecision(allowed=False, reasons=[*reasons, *(f"invalid:{f}" for f in bad
                                                                         if f"invalid:{f}" not in reasons)])

    def _same_direction(self, st: AccountState, side: int) -> list[str]:
        """BACKLOG 19 (playbook G-4: "same-side positions of different agents count as one bet")."""
        L = self.limits
        if L.heat.enabled and (st.open_long if side > 0 else st.open_short) >= L.heat.max_same_direction:
            return ["same_direction_cap"]
        return []

    def _sized_limits(self, intent: Intent | OperationalIntent, st: AccountState, *, lots: float, risk_per_lot: float,
                      fx: float, cost_per_oz: float, stop_distance: float, realised_risk: float, notes: list[str],
                      margin_required: MarginFn | None) -> GateDecision:
        """Every limit both entry checks apply once the trade is sized (ADR 0021: "Every sizing limit still applies"):
        the margin level floor, the combined exposure cap, the absolute daily loss limit and the heat cap, in that order.
        `cost_per_oz` is the round trip and swap per oz (USD) the daily projection adds; 0 for an operational order,
        which has no cost parameter."""
        L = self.limits
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
        risk_amount = lots * risk_per_lot
        # BACKLOG 36 (review LOW-6): a full stop-out also pays the round trip (spread, slippage, commission: cost_atr x
        # ATR) and the paid swap of a rollover before the risk day ends, per oz, x lots x contract x fx. Never negative:
        # a credit or a negative cost does not shrink the projection below loss so far + stop risk. Conservative by
        # design: cost_atr includes the spread, but the stop risk is measured from the fill (ask in, bid out at the
        # stop), which already pays it, so the spread is counted twice (the acceptance asks for the full `_cost_atr`)
        cost_amount = cost_per_oz * lots * intent.contract_oz * fx
        # BACKLOG 35 (review MEDIUM-4): with project_open_risk the projection also adds what the open positions can
        # still lose down to their stops (unknown open risk was refused above), so two open trades cannot carry the day
        # past the limit. Off: as before
        held = (st.open_risk or 0.0) if L.project_open_risk else 0.0
        if L.daily_loss_limit_abs is not None:
            lost = max(0.0, st.day_start_equity - st.equity)
            if lost + held + risk_amount + cost_amount > self.daily_loss_limit(st) + 1e-9:
                return GateDecision(allowed=False, reasons=["daily_loss_limit_abs"], stop_distance=stop_distance,
                                    margin_needed=margin_needed, margin_note=margin_note, risk_amount=risk_amount,
                                    cost_amount=cost_amount)
        # BACKLOG 19 (playbook G-4): open risk + this trade's full stop risk (a minimum-lot exception trade counts in
        # full) within max_open_risk x equity; exactly at the cap is allowed, as for the daily limit
        if L.heat.enabled and (st.open_risk or 0.0) + risk_amount > L.heat.max_open_risk * st.equity + 1e-9:
            return GateDecision(allowed=False, reasons=["heat_cap"], stop_distance=stop_distance,
                                margin_needed=margin_needed, margin_note=margin_note)
        return GateDecision(allowed=True, lots=round(lots, 2), reasons=notes, risk_fraction=realised_risk,
                            stop_distance=stop_distance, margin_needed=margin_needed, margin_note=margin_note,
                            risk_amount=risk_amount, cost_amount=cost_amount)

    def check(self, intent: Intent, st: AccountState, now_utc: pd.Timestamp | None = None,
              margin_required: MarginFn | None = None, throttle: AgentThrottle | None = None) -> GateDecision:
        """`margin_required(side, lots, price)` is the broker's margin figure (`broker_margin(broker, symbol)`);
        without it the margin check uses the 1:20 figure alone and says so in `margin_note`. `throttle` is the
        intent's agent's throttle facts (BACKLOG 21), judged at `now_utc` (`throttle_reasons`).

        A refusal is `account_reasons` (BACKLOG 43) followed by the intent's own reasons. A number outside its domain
        (NaN, infinite, or not > 0 where it must be: `invalid_fields`) refuses the entry (`invalid_state`, then
        `invalid:<field>` for each) after the account reasons and before any limit or the sizing reads it; the stage
        machine does not run on it (a zero equity read is not a 100% drawdown). The gate fails closed, never open, on
        state it cannot compare. Entries only; exits never call the gate.

        ADR 0021: the account checks (`_account_gate`), the same-direction cap and the limits after sizing
        (`_sized_limits`) are shared with `check_operational`; the model checks are this method's alone.
        tests/test_risk_gate_split.py pins the decisions on a state grid captured before the split (F5)."""
        L = self.limits
        reasons, refused = self._account_gate(st, invalid_fields(intent, st), now_utc, throttle)
        if refused is not None:
            return refused
        # a time-exit rule has no reachable target: p x target would be fiction (quant review H1). Fail closed until the
        # EV uses the time-exit outcome (HANDOFF: what must change before live)
        edge_checked = True
        measuring = intent.purpose == "cost_measurement"
        exploring = intent.purpose == "exploration"
        ev_exempt = measuring              # skips time_exit_ev_unsupported
        if measuring:                        # ADR 0037 Amendment 5: no EV requirement, inside its own fence (see Intent)
            reasons += _cost_measurement_reasons(intent)
            edge_checked = not intent.learning_probe
        elif exploring:                      # plan 2026-10-25: no edge rule, but only inside its fence (see Intent)
            fence = _exploration_reasons(intent, L)
            reasons += fence
            ev_exempt = not fence            # a broken fence refuses AND keeps every edge rule's reason
            edge_checked = not ev_exempt
        elif intent.learning_probe:          # a demo learning probe: no edge judgement after costs (see Intent)
            reasons += _learning_probe_reasons(intent)
            edge_checked = False
        if not ev_exempt and (not intent.has_target or not _family_has_target(intent)):
            reasons.append("time_exit_ev_unsupported")
        # expected value and minimum target over cost
        ev = intent.p * intent.target_atr - (1 - intent.p) * intent.stop_atr - intent.cost_atr
        if edge_checked and ev <= 0:
            reasons.append("negative_ev")
        if edge_checked and intent.target_atr < L.min_target_over_cost * intent.cost_atr:
            reasons.append("target_below_cost_floor")
        if edge_checked and st.account_class == "standard":
            if intent.timeframe == "15m" and intent.family != "session_open":
                reasons.append("standard_account_15m")
            edge = intent.p * intent.target_atr - (1 - intent.p) * intent.stop_atr      # gross, before costs
            if edge <= L.standard_edge_over_cost * intent.cost_atr:            # design: must exceed 1.5x
                reasons.append("standard_account_edge")
        reasons += self._same_direction(st, intent.side)
        if reasons:
            return GateDecision(allowed=False, reasons=reasons)

        # sizing, in the account currency (design Sizing; BACKLOG 28): the stop is USD per oz, so one lot's risk is
        # stop x contract x fx, fx being the broker's account currency per USD. The arithmetic is goldbot/risk/sizing.py
        fx = sizing.account_fx(intent.account_currency, intent.acct_per_usd)
        if fx is None:                       # a non-USD account without a usable conversion: never sized on a guess
            return GateDecision(allowed=False, reasons=["fx_conversion_unavailable"])
        # per-trade risk is capped after the multiplier (design Hard limits: 1% max, clamped, not rejected)
        size_down = st.stage == Stage.SIZE_DOWN or st.combined_size_down
        risk_frac = L.risk_for(intent.model_version)     # dynamic limits: the strategy's risk per trade (base: fixed)
        eff = sizing.effective_risk(L, size_down, intent.multiplier, risk_frac=risk_frac)
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
        per_oz = max(0.0, intent.cost_atr * intent.atr_usd) + max(0.0, intent.swap_usd_per_oz)
        d = self._sized_limits(intent, st, lots=lots, risk_per_lot=sz.risk_per_lot, fx=fx, cost_per_oz=per_oz,
                               stop_distance=stop_distance, realised_risk=realised_risk, notes=notes,
                               margin_required=margin_required)
        if d.allowed and measuring and (fence := _cost_measurement_sized(intent, d)):
            return GateDecision(allowed=False, reasons=fence, stop_distance=d.stop_distance,
                                margin_needed=d.margin_needed, margin_note=d.margin_note)
        if d.allowed and exploring and (fence := _exploration_sized(intent, L, d)):
            return GateDecision(allowed=False, reasons=fence, stop_distance=d.stop_distance,
                                margin_needed=d.margin_needed, margin_note=d.margin_note, risk_amount=d.risk_amount)
        if d.allowed and L.dynamic:          # the entry audit's why: the rate sized at and its source
            d = d.model_copy(update={"risk_per_trade": eff.risk_frac,
                                     "risk_source": L.risk_source(intent.model_version)})
        return d

    def check_operational(self, intent: OperationalIntent, st: AccountState, now_utc: pd.Timestamp | None,
                          margin_required: MarginFn | None) -> GateDecision:
        """ADR 0021: "Operational orders pass the RiskGate's account limits, not its model checks." The bridge smoke's
        order (goldbot/ops/smoke.py, the only caller: tests/test_operational_fence.py) shares `check`'s account checks
        (`_account_gate`, so `account_class_unknown` refuses here too), the same-direction cap and every limit after
        sizing (`_sized_limits`: margin floor, combined exposure, absolute daily loss limit, heat). It skips only the
        model checks, by name: `time_exit_ev_unsupported`, `negative_ev`, `target_below_cost_floor`,
        `standard_account_edge`, `standard_account_15m` (S6). It takes no p, target, cost or lots.

        * F1: refused `operational_not_demo` unless `account_mode == "demo"` and no gate from `paper_to_tiny_live` on
          (LIVE_GATES) is in the phase file's `gates_passed`; a missing phase file is phase 0, an unreadable or malformed one refuses.
        * F2: the size is exactly `volume_min` rounded down to `volume_step` (`invalid_volume` when that is no lot or
          above `volume_max`); the per-trade cap (`max_risk_per_trade`, halved in size-down) refuses it
          `min_lot_exceeds_risk`. The stop is `sizing.stop_distance` floored at OPERATIONAL_MIN_STOP_USD: at or beyond
          the stops level plus the spread.
        * S10: the boundaries are `check`'s: the daily cap refuses at equality, the absolute projection allows it.
        Entries only; exits never call it."""
        L = self.limits
        gates = phase_gates(intent.phase_file) if intent.account_mode == "demo" else None
        head = [] if gates is not None and not LIVE_GATES & set(gates) else ["operational_not_demo"]
        bad = invalid_state_fields(st) + [f for f in _OPERATIONAL_NUMBERS if _bad(intent, f, _POSITIVE_OPERATIONAL)]
        reasons, refused = self._account_gate(st, bad, now_utc, None)
        if refused is not None:
            return refused.model_copy(update={"reasons": [*head, *refused.reasons]})
        reasons = [*head, *reasons, *self._same_direction(st, intent.side)]
        steps = math.floor(intent.volume_min / intent.volume_step + 1e-9)
        lots = steps * intent.volume_step
        if steps < 1 or lots > intent.volume_max + 1e-9:
            reasons.append("invalid_volume")
        if reasons:
            return GateDecision(allowed=False, reasons=reasons)
        fx = sizing.account_fx(intent.account_currency, intent.acct_per_usd)
        if fx is None:                       # a non-USD account without a usable conversion: never sized on a guess
            return GateDecision(allowed=False, reasons=["fx_conversion_unavailable"])
        stop_distance = sizing.stop_distance(1.0, OPERATIONAL_MIN_STOP_USD, stops_level_points=intent.stops_level_points,
                                             spread_points=st.spread_points, point=L.point)
        risk_per_lot = stop_distance * intent.contract_oz * fx
        realised_risk = lots * risk_per_lot / st.equity
        size_down = st.stage == Stage.SIZE_DOWN or st.combined_size_down
        if realised_risk > L.max_risk_per_trade * (sizing.SIZE_DOWN_RISK if size_down else 1.0) + 1e-12:
            return GateDecision(allowed=False, reasons=["min_lot_exceeds_risk"], stop_distance=stop_distance)
        return self._sized_limits(intent, st, lots=lots, risk_per_lot=risk_per_lot, fx=fx, cost_per_oz=0.0,
                                  stop_distance=stop_distance, realised_risk=realised_risk, notes=[],
                                  margin_required=margin_required)


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
