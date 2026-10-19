"""Adaptive risk limits (ADR 0031): the account and supervisor loss caps and the minimum-lot exception cap, learnt from
the account's own trades between fixed floors and owner-approved ceilings.

The rules were frozen before any replay (ADR 0031; a replay validates, never tunes). Quoted:

* "Evidence: only trades AFTER each strategy's freeze/go-live date (never backtest/OOF); retired strategies' trades stay
  in; cut off strictly before the decision day; epoch_ns times; source ids kept."
* "Loosening evidence = demo fills ONLY (net R incl. real costs). Shadow (lot-fit) trades may only TIGHTEN (CFO)."
* "Statistic on DAILY R sums (not per trade): per strategy, d = daily net-R sums, n_days, mean m, sd s; LCB = m −
  t_{0.80, n_days−1}·s/√n_days ... Combine across strategies by the MINIMUM per-strategy LCB among strategies with
  evidence (conservative)."
* "Quarter-Kelly in R: f = LCB/(4·E[r²]) per trade ...; daily risk fraction = f · trades_per_day (measured) ...;
  q = clamp(daily_fraction / 3%, 0, 1). Daily target = 2% + q·3% (0.5% steps); weekly = 5% + q·5% (1% steps);
  supervisor target scales the same between its floor and ceiling."
* "Min-lot cap: off unless demo trades n ≥ 60 and LCB of exception trades > 0; then steps 0.6% → 0.8% → 1.0%; forced
  off when 20-day median ATR15 ≥ 1-year p90."
* "Loosen only if: demo n ≥ 60 total and ≥ 20 per contributing strategy; target above current by ≥ ½ step on 3
  consecutive risk-days; ≥ 10 new demo trades since the last loosen; ≤ 1 step/day; no loosening within 5 days of any
  tighten."
* "Tighten immediately (may skip steps, may happen intraday; uses demo+shadow at full weight): DD ≥ 4% from HWM →
  floors; 4 consecutive losses with r ≤ −0.8 → floors today+tomorrow; 20-day measured cost/model > 1.25 → floors,
  1.10–1.25 → one step down; stage SIZE_DOWN/HALTED → floors until cleared."
* "Bounds: clamp to [floor, ceiling]; live/tiny-live ceiling = default. Fail closed: missing/corrupt evidence or
  unreadable table → floors + dq_event."
* "Loosening applies only at the risk-day roll; intraday only tightening."

The readings made where the text left a choice (units in the Kelly step, ladders, hysteresis, cooldown) are listed in
ADR 0031; each takes the conservative side. `step` is pure (a replay from the same inputs gives the same states);
`AdaptiveController` adds the core.db state (`adaptive_state`) and the append-only change log (`adaptive_changes`).

ADR 0039 (dynamic per-strategy limits, docs/design/dynamic-limits-2026-10-19.md) adds, behind the account's
`dynamic_limits` flag (`LimitBounds.dynamic`; off = exactly the ADR 0031 behaviour above), quoted:

* "Per-trade risk r_s ... f_s = the existing A1 per-trade/per-day quarter-Kelly fraction (before /Q_SCALE). Shrinkage
  w = n_days/(n_days+20) (cluster count = trading days). r_s = base + w(f_s - base), quantised to 0.1% steps, clamped
  [0.25%, ceiling]. base = today's 0.5%. Loosen only with demo n >= 20 and the 0031 3-day/cooldown rules; shadow may
  only tighten; LCB <= 0 gives 0.25%."
* "Daily allowance: the existing 0031 ladder plus one new tighten rule, vol regime >= p90 -> one step down."
* "Fail closed: missing or corrupt evidence/ATR/state gives today's fixed values (0.5%)."
* The intraday factors k_vol, k_dd, k_day (`k_vol`, `k_dd`, `k_day`) are applied by the engine at each decision
  (engine/limits_apply.py); they only ever lower r.

Entries only: these are caps on new risk. Nothing here gates an exit, and nothing here places an order (RiskGate reads
the limits; lane B wires it).
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, timedelta
from itertools import accumulate
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, ValidationError, model_validator
from scipy import stats

from goldbot.base import FrozenRecord
from goldbot.db.connection import Database
from goldbot.db.repo import RecordRepo
from goldbot.risk.gate import Stage
from goldbot.risk.risk_terms import (  # noqa: F401  (re-exported: scipy-free home)
    MIN_LOT_MAX,
    RISK_MIN,
    RiskTerm,
)

if TYPE_CHECKING:
    from goldbot.config import RiskSettings

log = logging.getLogger("goldbot.risk.adaptive")

# ---- the frozen rules (ADR 0031): changing any of these is a new ADR, never a tuning step -----------------------------
LCB_QUANTILE = 0.80              # t_{0.80, n_days-1}
KELLY_DIVISOR = 4.0              # quarter-Kelly
Q_SCALE = 0.03                   # q = daily_fraction / 3%
MIN_DAYS = 2                     # a strategy "with evidence" has a standard deviation
MIN_DEMO_TRADES = 60
MIN_TRADES_PER_STRATEGY = 20
LOOSEN_DAYS = 3
LOOSEN_NEW_TRADES = 10
COOLDOWN_DAYS = 5
DD_FLOOR = 0.04
LOSS_STREAK = 4
LOSS_R = -0.8
COST_FLOOR = 1.25                # cost/model above this -> floors
COST_STEP = 1.10                 # 1.10 to 1.25 inclusive -> one step down (once per risk day)
DAILY_STEP = 0.005
WEEKLY_STEP = 0.01
SUPERVISOR_STEPS = {"supervisor_daily_cap": 6, "supervisor_weekly_cap": 5}   # as the account demo ladders (2-5%, 5-10%)
MIN_LOT_RUNGS = (0.006, 0.008, 0.010)
# Amendment A1 (quant review, before any replay): the demo minimum-lot exploration budget
EXPLORE_TRADES = 60              # the cap is the top rung (1.0%) for the first 60 exception trades
EXPLORE_STOP_R = -10.0           # cumulative net R of those trades at or below this: off for good (a re-test = new trial)

# ---- ADR 0039, dynamic per-strategy limits (frozen with it; a change is a new ADR) --------------------------------------
RISK_STEP = 0.001                # quantisation and the loosening step (0.1%)
RISK_SHRINK_DAYS = 20            # w = n_days / (n_days + 20)
RISK_BASE = 0.005                # today's fixed risk per trade (settings.yaml risk.risk_per_trade)
K_VOL = 0.5                      # 20-day median ATR15 >= 1-year p90
K_DD_START, K_DD_END, K_DD_MIN = 0.02, 0.04, 0.5    # 1 -> 0.5 linearly as the drawdown goes 2% -> 4%
K_DAY_AT, K_DAY = 0.5, 0.5       # 0.5 once today's loss >= 50% of the daily allowance

CAP_FIELDS = ("daily_cap", "weekly_cap", "supervisor_daily_cap", "supervisor_weekly_cap")
FIELDS = (*CAP_FIELDS, "min_lot_risk_cap")
_EPS = 1e-9
_DAY_NS = 86_400 * 10**9
_EPOCH = date(1970, 1, 1)


def day_of_ns(ns: int) -> date:
    """The UTC risk day (timeutil.risk_day) holding an epoch-ns instant."""
    return _EPOCH + timedelta(days=ns // _DAY_NS)


def day_start_ns(day: date) -> int:
    return (day - _EPOCH).days * _DAY_NS


# ---- records ---------------------------------------------------------------------------------------------------------

class Limits(FrozenRecord):
    """The adaptive limits, fractions of equity. `min_lot_risk_cap` None = the minimum-lot exception is off."""

    daily_cap: float = Field(gt=0, lt=1)
    weekly_cap: float = Field(gt=0, lt=1)
    supervisor_daily_cap: float = Field(gt=0, lt=1)
    supervisor_weekly_cap: float = Field(gt=0, lt=1)
    min_lot_risk_cap: float | None = Field(None, gt=0, le=MIN_LOT_MAX)
    # ADR 0039: per-trade risk per strategy (model version), fraction of equity. A version not in it trades the base
    # (today's 0.5%); empty = today's fixed values. Never above the 1% per-trade hard limit
    strategy_risk: dict[str, Annotated[float, Field(gt=0, le=MIN_LOT_MAX)]] = {}

    @classmethod
    def defaults(cls, risk: RiskSettings) -> Limits:
        """The floors: today's configured caps, with the minimum-lot exception off (owner, ADR 0031)."""
        return cls(daily_cap=risk.daily_cap, weekly_cap=risk.weekly_cap,
                   supervisor_daily_cap=risk.supervisor_daily_cap, supervisor_weekly_cap=risk.supervisor_weekly_cap,
                   min_lot_risk_cap=None)


class LimitBounds(FrozenRecord):
    """[floor, ceiling] per field. Build it with `build`: "live/tiny-live ceiling = default"."""

    floor: Limits
    ceiling: Limits
    # Amendment A1: the minimum-lot exploration budget runs on demo only (`build` sets it; live never explores)
    explore_min_lot: bool = False
    # ADR 0039: the account's `dynamic_limits` flag. Off: no per-strategy risk and no volatility step (ADR 0031 alone)
    dynamic: bool = False
    risk_base: float = Field(RISK_BASE, gt=0, le=MIN_LOT_MAX)      # the account's fixed risk per trade today
    risk_ceiling: float = Field(RISK_BASE, gt=0, le=MIN_LOT_MAX)   # demo: the 1% hard limit; live: the base

    @model_validator(mode="after")
    def _ordered(self) -> LimitBounds:
        for f in CAP_FIELDS:
            if getattr(self.ceiling, f) < getattr(self.floor, f) - _EPS:
                raise ValueError(f"{f}: ceiling below floor")
        lo, hi = self.floor.min_lot_risk_cap, self.ceiling.min_lot_risk_cap
        if lo is not None and (hi is None or hi < lo - _EPS):
            raise ValueError("min_lot_risk_cap: ceiling below floor")
        if self.risk_ceiling < self.risk_base - _EPS:
            raise ValueError("risk_ceiling below risk_base")
        return self

    @property
    def risk_floor(self) -> float:
        """The lowest r_s: 0.25%, or the base when the base is below it (tiny-live's 0.1%: never raised)."""
        return min(RISK_MIN, self.risk_base)

    @classmethod
    def build(cls, floor: Limits, demo_ceiling: Limits, *, mode: Literal["demo", "live"],
              supervisor_all_demo: bool, dynamic: bool = False, risk_base: float = RISK_BASE,
              risk_max: float = MIN_LOT_MAX) -> LimitBounds:
        """Demo: the owner-approved ceilings (a ceiling below its floor is the floor). Live (tiny-live included): the
        ceilings are the floors, so the limits may only stay or tighten. The supervisor ceilings apply only when every
        account it watches is demo. ADR 0039: `dynamic` turns the per-strategy risk on; its ceiling is `risk_max`
        (the per-trade hard limit, 1%) on demo and the base on live ("live/tiny-live bounds = defaults")."""
        r_hi = max(risk_base, min(risk_max, MIN_LOT_MAX)) if mode == "demo" else risk_base
        if mode != "demo":
            return cls(floor=floor, ceiling=floor, explore_min_lot=False, dynamic=dynamic, risk_base=risk_base,
                       risk_ceiling=r_hi)
        up: dict[str, float | None] = {}
        for f in CAP_FIELDS:
            sup = f.startswith("supervisor_")
            up[f] = getattr(floor, f) if sup and not supervisor_all_demo else max(getattr(floor, f), getattr(demo_ceiling, f))
        up["min_lot_risk_cap"] = demo_ceiling.min_lot_risk_cap if demo_ceiling.min_lot_risk_cap is not None \
            else floor.min_lot_risk_cap
        ceiling = floor.model_copy(update=up)
        return cls(floor=floor, ceiling=ceiling, explore_min_lot=ceiling.min_lot_risk_cap is not None,
                   dynamic=dynamic, risk_base=risk_base, risk_ceiling=r_hi)

    def rungs(self, f: str) -> tuple[float | None, ...]:
        """The field's ladder, floor first. One rung (the floor) when the ceiling is not above it."""
        if f == "min_lot_risk_cap":
            hi = self.ceiling.min_lot_risk_cap
            lo = self.floor.min_lot_risk_cap
            above = tuple(r for r in MIN_LOT_RUNGS if hi is not None and r <= hi + _EPS and (lo is None or r > lo + _EPS))
            return (lo, *above)
        lo_f, hi_f = float(getattr(self.floor, f)), float(getattr(self.ceiling, f))
        span = hi_f - lo_f
        if span <= _EPS:
            return (lo_f,)
        if f in SUPERVISOR_STEPS:
            n = SUPERVISOR_STEPS[f]
        else:
            n = max(1, round(span / (DAILY_STEP if f == "daily_cap" else WEEKLY_STEP)))
        return (*(round(lo_f + i * span / n, 10) for i in range(n)), hi_f)

    def level_of(self, f: str, value: float | None) -> int:
        """The highest rung at or below `value` (a changed ceiling never raises a limit); below the floor = 0."""
        best = 0
        for i, r in enumerate(self.rungs(f)):
            if r is not None and value is not None and r <= value + _EPS:
                best = i
        return best

    def limits_at(self, levels: Mapping[str, int]) -> Limits:
        return Limits(**{f: self.rungs(f)[levels[f]] for f in FIELDS})  # type: ignore[arg-type]

    def floors(self) -> Limits:
        return self.floor


class TradeEvidence(FrozenRecord):
    """One closed trade. `source` is demo (a real fill on the demo account) or shadow (lot-fit); never backtest/OOF.
    `net_r` includes the real costs."""

    id: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    source: Literal["demo", "shadow"]
    exit_ns: int
    net_r: float
    min_lot_exception: bool = False
    # ADR 0039 (quant review 2026-10-19): opened before the H-52 history-gap fix (promotion.EVIDENCE_START_UTC). Not
    # evidence for the per-strategy risk; ADR 0031's frozen statistic and the A1 budget still read it
    quarantined: bool = False


class Evidence(FrozenRecord):
    """What the controller sees at one evaluation. `freeze_ns`: each strategy's freeze / go-live time (epoch ns).
    `cost_ratio_20d`: measured cost / model cost over 20 days, None when nothing was measured. The ATR fields: the
    20-day median of ATR15 and its 1-year 90th percentile."""

    account_id: str = Field(min_length=1)
    as_of_ns: int
    trades: tuple[TradeEvidence, ...] = ()
    freeze_ns: dict[str, int]
    drawdown: float = Field(ge=0)               # from the high-water mark, fraction
    stage: Stage
    cost_ratio_20d: float | None = None
    atr15_median_20d: float | None = None
    atr15_p90_1y: float | None = None
    # ADR 0039's volatility regime, like with like: the 20-day median and the 1-year p90 of the DAILY median ATR15
    # (the per-bar p90 above is set by the busiest session hour, so the regime would almost never fire on it)
    vol_median_20d: float | None = None
    vol_p90_1y: float | None = None

    def day(self) -> date:
        return day_of_ns(self.as_of_ns)

    def problems(self) -> list[str]:
        """Why this evidence cannot be trusted (empty: it can). Any problem gives the floors (fail closed)."""
        out: list[str] = []
        for name in ("drawdown", "cost_ratio_20d", "atr15_median_20d", "atr15_p90_1y", "vol_median_20d",
                     "vol_p90_1y"):
            v = getattr(self, name)
            if v is not None and not math.isfinite(v):
                out.append(f"non_finite_{name}")
        if any(not math.isfinite(t.net_r) for t in self.trades):
            out.append("non_finite_r")
        missing = sorted({t.strategy for t in self.trades} - set(self.freeze_ns))
        out += [f"no_freeze_{s}" for s in missing]
        if len({t.id for t in self.trades}) != len(self.trades):
            out.append("duplicate_id")
        if any(t.exit_ns > self.as_of_ns for t in self.trades):
            out.append("exit_after_as_of")
        return out

    def used(self) -> list[TradeEvidence]:
        """Trades after their strategy's freeze (retired strategies' trades stay in), by exit time then id."""
        keep = [t for t in self.trades if t.strategy in self.freeze_ns and t.exit_ns >= self.freeze_ns[t.strategy]]
        return sorted(keep, key=lambda t: (t.exit_ns, t.id))


_RISK_WHY = {
    "no_evidence": "fewer than 2 trading days of evidence: the base risk",
    "lcb_not_positive": "no proven edge yet (lower confidence bound at or below 0): the 0.25% minimum",
    "shrunk": "quarter-Kelly of the measured edge, shrunk toward the base by the number of trading days",
    "hold": "the measured target is close to the current risk: unchanged",
    "waiting": "the target is higher: rises one 0.1% step after 3 qualifying days and 10 new demo trades",
    "cooldown": "the target is higher, but a limit was tightened within 5 days: no rise yet",
    "demo_too_few": "fewer than 20 demo trades: the risk may fall but not rise above the base",
    "loosen": "3 qualifying days on demo trades: one 0.1% step up",
    "tighten": "the measured edge is weaker (demo and shadow trades): lowered at once",
    "bounds": "outside its bounds: clamped",
    "floors": "account-wide floors (drawdown, loss streak, costs or stage): at most the base",
    "evidence_invalid": "evidence missing or unreadable: at most the base",
}




class AdaptiveState(FrozenRecord):
    """One account's adaptive limits and the counters the rules need (core.db `adaptive_state`)."""

    account_id: str
    day: date                                        # the risk day of the last evaluation
    limits: Limits
    up_streak: dict[str, int] = {}                   # consecutive qualifying risk-days per field
    loosened_day: dict[str, date] = {}
    demo_n_at_loosen: dict[str, int] = {}            # demo trades in the statistic at the field's last loosen
    tightened_day: date | None = None                # the last risk day with any tighten
    floors_until: date | None = None                 # loss streak: floors through this risk day
    loss_streak_ns: int | None = None                # exit of the last trade of the streak that fired (fires once)
    minlot_explore_stopped: bool = False             # A1: the exploration hit -10R; off until a new trial resets it
    # lane B: when the controller last decided (epoch ns; health `limits_controller` FAILs past 26 h). Set by
    # `AdaptiveController.current`, never by `step`, so a replay from the same inputs still gives the same states
    decided_ns: int | None = None
    # ADR 0039 (additive JSON: a row written before it reads with the defaults). The per-strategy decided risk is in
    # `limits.strategy_risk`; these are its why and the 3-day / cooldown counters, per strategy
    risk_terms: dict[str, RiskTerm] = {}
    risk_up_streak: dict[str, int] = {}
    risk_demo_n_at_loosen: dict[str, int] = {}
    risk_tightened_day: dict[str, date] = {}
    vol_high: bool | None = None                     # 20-day median ATR15 >= 1-year p90; None = unknown


class AdaptiveChange(FrozenRecord):
    """One row of the append-only `adaptive_changes` log."""

    account_id: str
    day: date
    field: str
    old: float | None
    new: float | None
    reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    ts_ns: int


class DailyStat(FrozenRecord):
    n_days: int
    n_trades: int
    mean: float
    sd: float
    lcb: float


# ---- the statistic ---------------------------------------------------------------------------------------------------

def daily_lcb(trades: Iterable[TradeEvidence]) -> DailyStat | None:
    """'LCB = m − t_{0.80, n_days−1}·s/√n_days' on the daily net-R sums of these trades (one strategy). Same-day
    trades are one observation, so correlated trades never inflate n. None with fewer than 2 days."""
    sums: dict[date, float] = defaultdict(float)
    n = 0
    for t in trades:
        sums[day_of_ns(t.exit_ns)] += t.net_r
        n += 1
    k = len(sums)
    if k < MIN_DAYS:
        return None
    d = list(sums.values())
    m = sum(d) / k
    s = math.sqrt(sum((x - m) ** 2 for x in d) / (k - 1))
    lcb = m - float(stats.t.ppf(LCB_QUANTILE, k - 1)) * s / math.sqrt(k)
    return DailyStat(n_days=k, n_trades=n, mean=m, sd=s, lcb=lcb)


def _by_strategy(trades: Iterable[TradeEvidence]) -> dict[str, list[TradeEvidence]]:
    out: dict[str, list[TradeEvidence]] = defaultdict(list)
    for t in trades:
        out[t.strategy].append(t)
    return out


def combined_lcb(trades: Iterable[TradeEvidence]) -> float | None:
    """'the MINIMUM per-strategy LCB among strategies with evidence'; None when no strategy has evidence."""
    lcbs = [s.lcb for s in (daily_lcb(ts) for ts in _by_strategy(trades).values()) if s is not None]
    return min(lcbs) if lcbs else None


def _a1_daily_fraction(trades: Sequence[TradeEvidence], st: DailyStat) -> float:
    """Amendment A1's DAILY risk fraction: min(LCB_s/(4·E[r²]_s), N̄_s·LCB_s/(4·E[D_s²])), LCB_s on daily sums; 0 when
    LCB_s <= 0."""
    if st.lcb <= 0:
        return 0.0
    sums: dict[date, float] = defaultdict(float)
    for t in trades:
        sums[day_of_ns(t.exit_ns)] += t.net_r
    e_r2 = sum(t.net_r ** 2 for t in trades) / len(trades)
    e_d2 = sum(v ** 2 for v in sums.values()) / len(sums)
    per_trade = st.lcb / (KELLY_DIVISOR * e_r2)
    per_day = (len(trades) / len(sums)) * st.lcb / (KELLY_DIVISOR * e_d2)
    return max(0.0, min(per_trade, per_day))


def strategy_f(trades: Sequence[TradeEvidence]) -> float | None:
    """ADR 0039, one strategy s: the quarter-Kelly PER-TRADE fraction (quant review 2026-10-19). A1's terms are daily
    fractions (the LCB is of the daily R sums), so per trade: f_s = min((LCB_s/N̄_s)/(4·E[r²]_s), LCB_s/(4·E[D_s²]))
    = A1's daily fraction / N̄_s, N̄_s its mean trades per day. The day term binds when losses cluster on the same
    days. 0 when LCB_s <= 0; None without evidence (fewer than 2 days)."""
    st = daily_lcb(trades)
    return None if st is None else _a1_daily_fraction(trades, st) * st.n_days / st.n_trades


def strategy_q(trades: Sequence[TradeEvidence]) -> float | None:
    """Amendment A1, one strategy s: q_s = its daily fraction / 3%, clamped to [0, 1]. None without evidence."""
    st = daily_lcb(trades)
    return None if st is None else min(1.0, max(0.0, _a1_daily_fraction(trades, st) / Q_SCALE))


def quarter_kelly_q(trades: Sequence[TradeEvidence]) -> float:
    """q = the minimum of `strategy_q` over the strategies with evidence (E[r²] is never pooled across strategies);
    0 when none has evidence."""
    qs = [q for q in (strategy_q(ts) for ts in _by_strategy(trades).values()) if q is not None]
    return min(qs) if qs else 0.0


def _quantise(r: float) -> float:
    """Down to the 0.1% step: quantisation never raises risk."""
    return round(math.floor(r / RISK_STEP + _EPS) * RISK_STEP, 10)


def strategy_risk(trades: Sequence[TradeEvidence], base: float, *, ceiling: float = MIN_LOT_MAX,
                  floor: float = RISK_MIN) -> RiskTerm:
    """ADR 0039, one strategy's target per-trade risk from these trades (no 3-day rule here, see `step`):
    "r_s = base + w(f_s - base), w = n_days/(n_days+20), quantised to 0.1% steps, clamped [0.25%, ceiling]; LCB <= 0
    gives 0.25%". Fewer than 2 trading days: the base. Never above the 1% per-trade hard limit."""
    hi = max(min(ceiling, MIN_LOT_MAX), floor)
    n_demo = sum(1 for t in trades if t.source == "demo")
    st = daily_lcb(trades)
    if st is None:
        return RiskTerm(r=min(max(base, floor), hi), target=base, n_days=len({day_of_ns(t.exit_ns) for t in trades}),
                        n_demo=n_demo, reason="no_evidence")
    if st.lcb <= 0:
        return RiskTerm(r=floor, target=floor, f=0.0, n_days=st.n_days, n_demo=n_demo, lcb=st.lcb, reason="lcb_not_positive")
    f = _a1_daily_fraction(trades, st) * st.n_days / st.n_trades
    w = st.n_days / (st.n_days + RISK_SHRINK_DAYS)
    # f is clamped to the ceiling BEFORE shrinking (quant review): a few very good days cannot reach the cap
    r = min(max(_quantise(base + w * (min(f, hi) - base)), floor), hi)
    return RiskTerm(r=r, target=r, f=f, n_days=st.n_days, n_demo=n_demo, lcb=st.lcb, w=w, reason="shrunk")


def k_vol(vol_high: bool | None) -> float:
    """ADR 0039: 0.5 when the 20-day median ATR15 is at or above its 1-year p90; 1 otherwise or when unknown (today's
    fixed values: fail closed never goes below them)."""
    return K_VOL if vol_high else 1.0


def k_dd(drawdown: float | None) -> float:
    """ADR 0039: 1 -> 0.5 linearly as the drawdown from the high-water mark goes 2% -> 4%; 1 when unknown (the stage
    and ADR 0031's 4% floors still apply)."""
    if drawdown is None or not math.isfinite(drawdown) or drawdown <= K_DD_START:
        return 1.0
    if drawdown >= K_DD_END:
        return K_DD_MIN
    return 1.0 - (1.0 - K_DD_MIN) * (drawdown - K_DD_START) / (K_DD_END - K_DD_START)


def k_day(loss_today: float, allowance: float) -> float:
    """ADR 0039: 0.5 once today's loss is at least 50% of the daily allowance (same units). An allowance that is not a
    finite number > 0 is no allowance (1; the gate refuses on it); a loss that cannot be read is the strict side."""
    if not (math.isfinite(allowance) and allowance > 0):
        return 1.0
    if not math.isfinite(loss_today):
        return K_DAY
    return K_DAY if loss_today >= K_DAY_AT * allowance - _EPS else 1.0


def stricter_risk(a: Mapping[str, float], b: Mapping[str, float], base: float) -> dict[str, float]:
    """Per strategy the smaller of two `strategy_risk` maps, a missing one being the base (intraday only lowers)."""
    return {k: min(a.get(k, base), b.get(k, base)) for k in sorted({*a, *b})}


def _demo_sufficient(demo: Sequence[TradeEvidence]) -> bool:
    """'demo n ≥ 60 total and ≥ 20 per contributing strategy' (and every one with evidence: at least 2 days)."""
    if len(demo) < MIN_DEMO_TRADES:
        return False
    for ts in _by_strategy(demo).values():
        if len(ts) < MIN_TRADES_PER_STRATEGY or len({day_of_ns(t.exit_ns) for t in ts}) < MIN_DAYS:
            return False
    return True


def _lower(a: float | None, b: float | None) -> bool:
    """b is a tighter limit than a (None = off, the tightest minimum-lot value)."""
    return a is not None and (b is None or b < a - _EPS)


# ---- one evaluation --------------------------------------------------------------------------------------------------

class _RiskDecision(FrozenRecord):
    """ADR 0039: the per-strategy part of one evaluation (merged into `AdaptiveState` by `step`)."""

    strategy_risk: dict[str, float] = {}
    terms: dict[str, RiskTerm] = {}
    up_streak: dict[str, int] = {}
    demo_n_at_loosen: dict[str, int] = {}
    tightened_day: dict[str, date] = {}
    reasons: tuple[str, ...] = ()

    def state_fields(self, vol_high: bool | None = None) -> dict[str, Any]:
        """The state's per-strategy fields; each term carries the nightly volatility cut (`k_vol`: 0.5 at or above the
        1-year p90, else 1), which the engine reads as the day's high-volatility halving (integration 2026-10-19)."""
        kv = k_vol(vol_high)
        terms = {v: t.model_copy(update={"k_vol": kv}) for v, t in self.terms.items()} if kv != 1.0 else self.terms
        return {"risk_terms": terms, "risk_up_streak": self.up_streak,
                "risk_demo_n_at_loosen": self.demo_n_at_loosen, "risk_tightened_day": self.tightened_day}


def _risk_floored(prev: AdaptiveState | None, bounds: LimitBounds, day: date, code: str) -> _RiskDecision:
    """Floors or invalid evidence: "fail closed ... gives today's fixed values". Each strategy at most the base (a
    tightened one stays down: the strict side), the 3-day counts reset. Flag off: none at all (ADR 0031 alone)."""
    if not bounds.dynamic or prev is None:
        return _RiskDecision()
    base, lo = bounds.risk_base, bounds.risk_floor
    risk = {s: max(lo, min(r, base)) for s, r in prev.limits.strategy_risk.items()}
    tight = dict(prev.risk_tightened_day)
    tight.update({s: day for s, r in risk.items() if r < prev.limits.strategy_risk[s] - _EPS})
    terms = {s: RiskTerm(r=r, target=None, reason=code, why=_RISK_WHY[code]) for s, r in risk.items()}
    moved = tuple(f"strategy_risk:{s}:{code}" for s, r in risk.items() if r != prev.limits.strategy_risk[s])
    return _RiskDecision(strategy_risk=risk, terms=terms, up_streak={s: 0 for s in risk},
                         demo_n_at_loosen=dict(prev.risk_demo_n_at_loosen), tightened_day=tight, reasons=moved)


def _strategy_risks(prev: AdaptiveState | None, in_stat: Sequence[TradeEvidence], day: date, bounds: LimitBounds, *,
                    roll: bool, last_tight: date | None) -> _RiskDecision:
    """ADR 0039 per strategy: the target is `strategy_risk` on its demo fills (never above the base with fewer than
    20 of them), lowered by the same on demo + shadow ("shadow may only tighten"). Below the current risk: lowered at
    once, any time. Above it by at least half a step: one 0.1% step up at the roll after 3 consecutive qualifying
    risk-days and 10 new demo trades since its last rise, never within 5 days of a tighten (its own or any account
    limit's: ADR 0031's cooldown). Kept within [0.25% (or a lower base), ceiling]."""
    if not bounds.dynamic:
        return _RiskDecision()
    base, lo, hi = bounds.risk_base, bounds.risk_floor, bounds.risk_ceiling
    cur_map = prev.limits.strategy_risk if prev else {}
    streak = dict(prev.risk_up_streak) if prev else {}
    n_at = dict(prev.risk_demo_n_at_loosen) if prev else {}
    tight = dict(prev.risk_tightened_day) if prev else {}
    by = _by_strategy(in_stat)
    risk: dict[str, float] = {}
    terms: dict[str, RiskTerm] = {}
    reasons: list[str] = []
    for s in sorted({*by, *cur_map}):
        ts = by.get(s, [])
        demo = [t for t in ts if t.source == "demo"]
        t_demo = strategy_risk(demo, base, ceiling=hi, floor=lo)
        t_all = strategy_risk(ts, base, ceiling=hi, floor=lo)
        up_ok = len(demo) >= MIN_TRADES_PER_STRATEGY
        demo_target = t_demo.r if up_ok else min(base, t_demo.r)
        binding = t_all if t_all.r < demo_target - _EPS else t_demo
        target = min(demo_target, t_all.r)
        raw = cur_map.get(s, base)
        cur = min(max(raw, lo), hi)
        new, code = cur, "bounds" if abs(cur - raw) > _EPS else "hold"
        if target < cur - _EPS:
            new, code = target, "tighten"
        elif roll:
            qualifies = up_ok and target >= cur + RISK_STEP / 2 - _EPS
            streak[s] = streak.get(s, 0) + 1 if qualifies else 0
            cooling = any(d is not None and (day - d).days <= COOLDOWN_DAYS for d in (last_tight, tight.get(s)))
            if qualifies and cooling:
                code = "cooldown"
            elif qualifies and streak[s] >= LOOSEN_DAYS and len(demo) - n_at.get(s, 0) >= LOOSEN_NEW_TRADES:
                new, code = round(min(cur + RISK_STEP, target), 10), "loosen"
                streak[s], n_at[s] = 0, len(demo)
            elif qualifies:
                code = "waiting"
            elif not up_ok and t_demo.r > cur + _EPS:
                code = "demo_too_few"
        if new < raw - _EPS:
            tight[s], streak[s] = day, 0
        risk[s] = new
        terms[s] = binding.model_copy(update={"r": new, "target": target, "n_demo": len(demo), "reason": code,
                                              "why": _RISK_WHY[code]})
        if s not in cur_map or abs(new - cur_map[s]) > _EPS:
            reasons.append(f"strategy_risk:{s}:{code}")
    return _RiskDecision(strategy_risk=risk, terms=terms, up_streak=streak, demo_n_at_loosen=n_at, tightened_day=tight,
                         reasons=tuple(reasons))


def _floored(prev: AdaptiveState | None, account_id: str, day: date, bounds: LimitBounds, reasons: list[str], *,
             floors_until: date | None, loss_streak_ns: int | None, explore_stopped: bool,
             vol_high: bool | None = None, risk_code: str = "floors") -> tuple[AdaptiveState, tuple[str, ...]]:
    """Every field at its floor, the 3-day counts reset, and today recorded as a tighten (the 5-day cooldown). ADR
    0039: each strategy's risk at most the base (`_risk_floored`)."""
    last = prev.tightened_day if prev else None
    rd = _risk_floored(prev, bounds, day, risk_code)
    state = AdaptiveState(account_id=account_id, day=max(day, prev.day) if prev else day,
                          limits=bounds.floor.model_copy(update={"strategy_risk": rd.strategy_risk}),
                          up_streak={f: 0 for f in FIELDS}, loosened_day=dict(prev.loosened_day) if prev else {},
                          demo_n_at_loosen=dict(prev.demo_n_at_loosen) if prev else {},
                          tightened_day=max(day, last) if last else day, floors_until=floors_until,
                          loss_streak_ns=loss_streak_ns, minlot_explore_stopped=explore_stopped,
                          vol_high=vol_high if bounds.dynamic else None,
                          **rd.state_fields(vol_high if bounds.dynamic else None))
    return state, tuple(dict.fromkeys([*reasons, *rd.reasons]))


def step(prev: AdaptiveState | None, ev: Evidence | None, day: date, bounds: LimitBounds, *,
         account_id: str | None = None) -> tuple[AdaptiveState, tuple[str, ...]]:
    """One evaluation for risk day `day`. Pure: the same inputs always give the same state and reasons.

    Reasons are codes (`dd_floor`, `loss_streak_floor`, `floors_until`, `cost_floor`, `cost_step`, `stage_floor`,
    `target_below`, `atr_minlot_off`, `bounds`, `loosen`, `evidence_invalid`), each also as `<field>:<code>` for the
    fields it moved."""
    acct = account_id or (prev.account_id if prev else None) or (ev.account_id if ev else None) or "unknown"
    problems = ["missing"] if ev is None else ev.problems()
    if ev is not None and ev.day() != day:
        problems.append("as_of_not_on_decision_day")
    if ev is not None and ev.account_id != acct:
        problems.append("other_account")
    if prev is not None and day < prev.day:
        problems.append("day_before_last")
    if problems or ev is None:
        return _floored(prev, acct, day, bounds, ["evidence_invalid", *(f"evidence_invalid:{p}" for p in problems)],
                        floors_until=prev.floors_until if prev else None,
                        loss_streak_ns=prev.loss_streak_ns if prev else None,
                        explore_stopped=prev.minlot_explore_stopped if prev else False,
                        risk_code="evidence_invalid")
    # ADR 0039: "vol regime >= p90"; unknown ATR = no regime (today's rules: fail closed never below them)
    vol_high = None if ev.vol_median_20d is None or ev.vol_p90_1y is None \
        else ev.vol_median_20d >= ev.vol_p90_1y

    roll = prev is None or day > prev.day
    reasons: list[str] = []
    old = {f: bounds.level_of(f, getattr(prev.limits, f)) if prev else 0 for f in FIELDS}
    lvl = dict(old)
    clamped = [f for f in FIELDS if prev is not None and bounds.rungs(f)[lvl[f]] != getattr(prev.limits, f)]
    if clamped:
        reasons += ["bounds", *(f"{f}:bounds" for f in clamped)]
    clamped_down = prev is not None and any(_lower(getattr(prev.limits, f), bounds.rungs(f)[lvl[f]]) for f in clamped)

    used = ev.used()
    cut = day_start_ns(day)
    in_stat = [t for t in used if t.exit_ns < cut]
    demo = [t for t in in_stat if t.source == "demo"]

    # -- Amendment A1: the demo minimum-lot exploration budget. Every demo exception trade counts (today's too) --
    exc_trades = [t for t in used if t.source == "demo" and t.min_lot_exception]
    stopped = prev.minlot_explore_stopped if prev else False
    # the running low, in exit order, of the budget's trades: a dip to -10R stops it even if later trades recover
    # (a stop must not depend on when the controller happened to evaluate)
    if (bounds.explore_min_lot and not stopped
            and min(accumulate((t.net_r for t in exc_trades[:EXPLORE_TRADES]), initial=0.0)) <= EXPLORE_STOP_R + _EPS):
        stopped = True
        reasons.append("minlot_explore_stop")
    exploring = bounds.explore_min_lot and not stopped and len(exc_trades) < EXPLORE_TRADES

    # -- tighten to the floors (any time) --
    floors_until = prev.floors_until if prev else None
    streak_ns = prev.loss_streak_ns if prev else None
    floor_codes: list[str] = []
    if ev.drawdown >= DD_FLOOR:
        floor_codes.append("dd_floor")
    last = used[-LOSS_STREAK:]
    if len(last) == LOSS_STREAK and all(t.net_r <= LOSS_R for t in last) and last[-1].exit_ns != streak_ns:
        floor_codes.append("loss_streak_floor")
        streak_ns = last[-1].exit_ns
        floors_until = max(floors_until or day, day + timedelta(days=1))
    if floors_until is not None and day <= floors_until and "loss_streak_floor" not in floor_codes:
        floor_codes.append("floors_until")
    if ev.cost_ratio_20d is not None and ev.cost_ratio_20d > COST_FLOOR:
        floor_codes.append("cost_floor")
    if ev.stage != Stage.NORMAL:
        floor_codes.append("stage_floor")
    if floor_codes:
        moved = [f"{f}:{floor_codes[0]}" for f in FIELDS if lvl[f] > 0]
        return _floored(prev, acct, day, bounds, [*reasons, *floor_codes, *moved], floors_until=floors_until,
                        loss_streak_ns=streak_ns, explore_stopped=stopped, vol_high=vol_high)

    # -- the statistic: loosening on demo only, tightening on demo + shadow too --
    demo_ok = _demo_sufficient(demo)
    q_demo = quarter_kelly_q(demo) if demo_ok else 0.0
    q = min(q_demo, quarter_kelly_q(in_stat)) if in_stat else 0.0
    exc_demo = combined_lcb(t for t in demo if t.min_lot_exception)
    exc_all = combined_lcb(t for t in in_stat if t.min_lot_exception)
    atr_off = (ev.atr15_median_20d is None or ev.atr15_p90_1y is None or ev.atr15_median_20d >= ev.atr15_p90_1y)
    minlot_ok = demo_ok and exc_demo is not None and exc_demo > 0 and (exc_all is None or exc_all > 0) and not atr_off

    target: dict[str, int] = {}
    qualifies: dict[str, bool] = {}
    for f in FIELDS:
        n = len(bounds.rungs(f)) - 1
        if f == "min_lot_risk_cap" and (exploring or stopped):
            target[f] = n if exploring and not atr_off else 0          # A1: the fixed budget, paused by the ATR rule
            qualifies[f] = False                                       # never through the loosening rule
        elif f == "min_lot_risk_cap":
            target[f] = n if minlot_ok else 0
            qualifies[f] = target[f] > lvl[f]
        else:
            x = q * n
            target[f] = int(math.floor(x + _EPS))
            qualifies[f] = x >= lvl[f] + 0.5 - _EPS
    tightened = False
    for f in FIELDS:
        if target[f] < lvl[f]:
            code = "target_below"
            if f == "min_lot_risk_cap":
                code = "minlot_explore_stop" if stopped else "atr_minlot_off" if atr_off else code
            lvl[f] = target[f]
            reasons += [code, f"{f}:{code}"]
    if roll and ev.cost_ratio_20d is not None and COST_STEP <= ev.cost_ratio_20d <= COST_FLOOR:
        reasons.append("cost_step")
        tightened = True
        for f in FIELDS:
            if lvl[f] > 0:                  # an exploring minimum-lot cap is put back by the resume below (A1: fixed)
                lvl[f] -= 1
                reasons.append(f"{f}:cost_step")
    # ADR 0039: "vol regime >= p90 -> one step down" of the daily allowance, once on entering the regime (no ratchet);
    # no daily loosening while it holds
    if bounds.dynamic and vol_high:
        qualifies["daily_cap"] = False
        if not (prev is not None and prev.vol_high) and lvl["daily_cap"] > 0:
            lvl["daily_cap"] -= 1
            reasons += ["vol_step", "daily_cap:vol_step"]
    tightened = tightened or clamped_down or any(lvl[f] < old[f] for f in FIELDS)
    # A1: a paused exploration resumes at the next risk-day roll once nothing pauses it (no cooldown, no 3-day count)
    top = len(bounds.rungs("min_lot_risk_cap")) - 1
    if exploring and roll and target["min_lot_risk_cap"] == top and lvl["min_lot_risk_cap"] < top:
        lvl["min_lot_risk_cap"] = top
        reasons += ["minlot_explore", "min_lot_risk_cap:minlot_explore"]

    # -- loosening: at the roll only, one step, after 3 qualifying days and 10 new demo trades, not in the cooldown --
    streak = {f: (prev.up_streak.get(f, 0) if prev else 0) for f in FIELDS}
    loosened_day = dict(prev.loosened_day) if prev else {}
    n_at = dict(prev.demo_n_at_loosen) if prev else {}
    last_tight = day if tightened else (prev.tightened_day if prev else None)
    if tightened:
        streak = {f: 0 for f in FIELDS}
    elif roll:
        cooling = last_tight is not None and (day - last_tight).days <= COOLDOWN_DAYS
        for f in FIELDS:
            streak[f] = streak[f] + 1 if qualifies[f] else 0
            if (not cooling and demo_ok and streak[f] >= LOOSEN_DAYS and len(demo) - n_at.get(f, 0) >= LOOSEN_NEW_TRADES
                    and lvl[f] < len(bounds.rungs(f)) - 1):
                lvl[f] += 1
                streak[f] = 0
                loosened_day[f] = day
                n_at[f] = len(demo)
                reasons += ["loosen", f"{f}:loosen"]

    rd = _strategy_risks(prev, [t for t in in_stat if not t.quarantined], day, bounds, roll=roll,
                         last_tight=last_tight)
    state = AdaptiveState(account_id=acct, day=day,
                          limits=bounds.limits_at(lvl).model_copy(update={"strategy_risk": rd.strategy_risk}),
                          up_streak=streak, loosened_day=loosened_day, demo_n_at_loosen=n_at, tightened_day=last_tight,
                          floors_until=floors_until, loss_streak_ns=streak_ns, minlot_explore_stopped=stopped,
                          vol_high=vol_high if bounds.dynamic else None,
                          **rd.state_fields(vol_high if bounds.dynamic else None))
    return state, tuple(dict.fromkeys([*reasons, *rd.reasons]))


def changes_between(prev: AdaptiveState | None, new: AdaptiveState, reasons: Sequence[str], ev: Evidence | None, *,
                    ts_ns: int, baseline: Limits | None = None,
                    risk_base: float | None = None) -> list[AdaptiveChange]:
    """One `adaptive_changes` row per field whose value changed: old, new, the reason codes that apply to it (the plain
    codes plus its own `<field>:<code>` ones) and the ids of the trades the evaluation used. A first evaluation
    (no `prev`) is compared with `baseline` (the floors), so a first-day exploration cap is logged too. ADR 0039: one
    `strategy_risk:<version>` row per strategy whose risk changed (None = not in the map, i.e. the base; a strategy
    that appears or goes at exactly `risk_base` is no change)."""
    before = prev.limits if prev is not None else baseline
    if before is None:
        return []
    ids = tuple(t.id for t in ev.used()) if ev is not None else ()
    plain = [r for r in reasons if r.split(":", 1)[0] not in (*FIELDS, "strategy_risk")]
    rows: list[AdaptiveChange] = []
    for f in FIELDS:
        a, b = getattr(before, f), getattr(new.limits, f)
        if a == b:
            continue
        own = [r.split(":", 1)[1] for r in reasons if r.startswith(f + ":")]
        rows.append(AdaptiveChange(account_id=new.account_id, day=new.day, field=f, old=a, new=b,
                                reasons=tuple(dict.fromkeys([*plain, *own])), evidence_ids=ids, ts_ns=ts_ns))
    a_map, b_map = before.strategy_risk, new.limits.strategy_risk
    for s in sorted({*a_map, *b_map}):
        a_r, b_r = a_map.get(s), b_map.get(s)
        same = a_r is not None and b_r is not None and abs(a_r - b_r) <= _EPS
        at_base = risk_base is not None and (a_r if a_r is not None else risk_base) == \
            (b_r if b_r is not None else risk_base)
        if same or at_base:
            continue
        key = f"strategy_risk:{s}:"
        own = [r[len(key):] for r in reasons if r.startswith(key)]
        rows.append(AdaptiveChange(account_id=new.account_id, day=new.day, field=f"strategy_risk:{s}", old=a_r,
                                   new=b_r, reasons=tuple(dict.fromkeys([*plain, *own])), evidence_ids=ids,
                                   ts_ns=ts_ns))
    return rows


# ---- persistence (core.db migration 4) -------------------------------------------------------------------------------

STATE: RecordRepo[AdaptiveState] = RecordRepo(
    "adaptive_state", AdaptiveState, key="account_id", tolerant=True,
    columns={"account_id": lambda s: s.account_id, "day": lambda s: s.day.isoformat()})


class AdaptiveStore:
    """core.db `adaptive_state` and `adaptive_changes`, opened (and migrated) on first use. Errors propagate: the
    controller turns them into the floors and a dq_event."""

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self._db: Database | None = None

    @property
    def db(self) -> Database:
        if self._db is None:
            self._db = Database.open(self.state_dir, "core")
        return self._db

    def load(self, account_id: str) -> AdaptiveState | None:
        with self.db.connection() as conn:
            return STATE.get(conn, account_id)

    def save(self, state: AdaptiveState, changes: Sequence[AdaptiveChange]) -> None:
        """The state and its change rows in one transaction (synchronous FULL)."""
        with self.db.write() as conn:
            STATE.upsert(conn, state)
            conn.executemany(
                "INSERT INTO adaptive_changes (account_id, day, field, old, new, reasons, evidence_ids, ts_ns) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(c.account_id, c.day.isoformat(), c.field, c.old, c.new, json.dumps(list(c.reasons)),
                  json.dumps(list(c.evidence_ids)), c.ts_ns) for c in changes])

    def changes(self, account_id: str) -> list[AdaptiveChange]:
        with self.db.connection() as conn:
            rows = conn.execute("SELECT account_id, day, field, old, new, reasons, evidence_ids, ts_ns FROM adaptive_changes "
                                "WHERE account_id = ? ORDER BY id", (account_id,)).fetchall()
        return [AdaptiveChange(account_id=a, day=date.fromisoformat(d), field=f, old=o, new=n,
                            reasons=tuple(json.loads(r)), evidence_ids=tuple(json.loads(e)), ts_ns=ts)
                for a, d, f, o, n, r, e, ts in rows]


# ---- the controller --------------------------------------------------------------------------------------------------

DqSink = Callable[[str, str], None]


def _log_dq(code: str, detail: str) -> None:
    log.warning("dq_event %s: %s", code, detail)


class AdaptiveController:
    """ADR 0031 per account: `current` loads the state, runs one `step`, records any change and returns the limits.
    Fails closed: evidence that is missing or does not validate, a state that cannot be read, or a change that cannot
    be written gives the floors and a dq_event (`dq(code, detail)`; lane B routes it to the dq_events store).
    ADR 0032's seam (`limits_profile.LimitsController.current(account, LimitsEvidence)`) reaches this through an adapter
    lane B writes: it builds `Evidence` and maps `Limits` onto `RiskLimitsCurrent`."""

    def __init__(self, state_dir: str | Path, bounds: LimitBounds, *, dq: DqSink | None = None,
                 clock: Callable[[], int] = time.time_ns):
        self.bounds = bounds
        self.store = AdaptiveStore(state_dir)
        self.dq: DqSink = dq or _log_dq
        self.clock = clock

    def step(self, prev: AdaptiveState | None, ev: Evidence | None, day: date) -> tuple[AdaptiveState, tuple[str, ...]]:
        return step(prev, ev, day, self.bounds)

    def current(self, account_id: str, evidence: Evidence | Mapping[str, Any] | None, *,
                day: date | None = None) -> Limits:
        """`day`: the risk day decided (default: the evidence's, else the clock's). The nightly job passes it, so a
        night without evidence still records the floors for the day it decides."""
        ev: Evidence | None
        detail = ""
        try:
            ev = evidence if isinstance(evidence, Evidence) or evidence is None else Evidence.model_validate(evidence)
        except ValidationError as exc:
            ev, detail = None, f"does not validate ({exc.error_count()} errors)"
        try:
            prev = self.store.load(account_id)
        except Exception as exc:          # unreadable table or row: the floors, never a guess
            self.dq("adaptive_limits_state_unreadable", f"{account_id}: {type(exc).__name__}")
            return self.bounds.floors()
        now = self.clock()
        day = day if day is not None else ev.day() if ev is not None else day_of_ns(now)
        new, reasons = step(prev, ev, day, self.bounds, account_id=account_id)
        new = new.model_copy(update={"decided_ns": now})
        if "evidence_invalid" in reasons:
            why = ", ".join(r.split(":", 1)[1] for r in reasons if r.startswith("evidence_invalid:"))
            self.dq("adaptive_limits_evidence_invalid", f"{account_id}: {detail or why}")
        if new != prev:                   # always, with `decided_ns`: every decision is recorded
            try:
                self.store.save(new, changes_between(prev, new, reasons, ev, ts_ns=now, baseline=self.bounds.floor,
                                                     risk_base=self.bounds.risk_base))
            except Exception as exc:      # an unrecorded change never applies
                self.dq("adaptive_limits_state_unwritable", f"{account_id}: {type(exc).__name__}")
                return self.bounds.floors()
        return new.limits


__all__ = ["FIELDS", "RISK_BASE", "RISK_MIN", "AdaptiveChange", "AdaptiveController", "AdaptiveState", "AdaptiveStore",
           "DailyStat", "Evidence", "LimitBounds", "Limits", "RiskTerm", "TradeEvidence", "changes_between",
           "combined_lcb", "daily_lcb", "day_of_ns", "day_start_ns", "k_day", "k_dd", "k_vol", "quarter_kelly_q", "step",
           "strategy_f", "strategy_q", "strategy_risk", "stricter_risk"]
