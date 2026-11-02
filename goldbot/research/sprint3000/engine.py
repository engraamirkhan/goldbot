"""The sprint-3000 batch screen: every catalogue spec on 1m bars, priced with the C1 cost model, gated against its
random-side control and the in-window drift, summarised against the frozen rules (config/research/sprint3000_rules.yaml).

Conventions (sprint-3000 brief; amendments W2 and C1, which overrides the cost and control parts):
- "entry at the next 1m open after the decision close, stop-first on same-minute hits": the fill is the open of the first
  minute at or after the bar's close; a minute touching both stop and target is a stop; a minute that opens through the
  stop fills at its open (worse than the stop; `gap_at_open=False` reproduces the harness, which fills at the stop).
- One position per spec at a time; a signal while one is open is skipped.
- Exits: target_r(r), chandelier(atr_n, mult) (the highest high / lowest low of EARLIER minutes less mult x ATR, never
  looser than the initial stop), time(bars), opposite_signal (the open of the first minute at or after the first opposite
  entry signal after the fill); every trade also stops out and ends at the close of bar i + max_hold_bars.
- Costs, C1: "cost_R = (RT_point_bps x P_entry + OS_point_bps x P_entry x [stop or trail exit] - nights x swap_bps(side)
  x P_entry) / stop_usd", the cost of record's POINT estimates at reference price 4,000, the measured broker swap
  (shorts credited); a stop exit that gapped is already filled at the open and pays no overshoot on top. The old dollar
  upper-bound line (`ArmCosts.screen_charges`, shipped swap) is reported as `net_r_usd`, a stress column.
- Gate control, C1: the random-side twin (same minute, stop and exit, side at random) at its exact expectation, excess
  = (real - opposite) / 2 >= 0; and the drift-adjusted gross (side x in-window drift x hold subtracted) > 0. Same-side
  twins are the entry-timing diagnostic only.
- Explore (and learn_old): n >= 100 (60 on 4h/1d), net > 0, t >= 2 on DAY-SUMMED net R, >= 5/8 years positive,
  drift-adjusted gross > 0, random-side excess >= 0; long and short legs reported. Ensemble stage: per family an
  equal-weight composite of its explore passers and its members with gross t >= 2, frozen at the end of explore, one
  trial per family. Confirm: BH-FDR q 0.10 over m = singles + families sent, on a one-sided day-block bootstrap p, net
  > 0, >= 5/8 years, n >= 100; no DSR term; ex-2025 and drift-adjusted gross reported. TEST-OLD: n, net > 0, t >= 1.28.
- DSR is reported at program K and K_eff (this run's specs clustered by daily-return correlation), never gated.
- Null controls (the catalogue's grade-D specs): more than 10% of them passing explore voids the run; any passing
  confirm or test_old marks it "engine audit required".
Results hold summary statistics only: no trade row leaves the process."""
from __future__ import annotations

import hashlib
import math
import pickle
import tempfile
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, NamedTuple

import numpy as np
import pandas as pd
import yaml
from pydantic import Field
from scipy.cluster.hierarchy import fcluster, linkage

from goldbot.base import FrozenRecord
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.labels import SwapSpec, rollover_nights
from goldbot.research import arms as A
from goldbot.research.metrics import MIN_TRADES_FOR_DSR, DsrInputs
from goldbot.research.sprint3000 import control as C
from goldbot.research.sprint3000 import vocab as V
from goldbot.research.sprint3000.market import DAY, MIN, WINDOWS, Market, Tape, Window

ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = ROOT / "config" / "research" / "sprint3000_rules.yaml"
COSTS_PATH = ROOT / "config" / "arm_costs.yaml"
BIG = np.iinfo(np.int64).max
STOP_KINDS = ("stop", "trail")
EXPLORE_LIKE = ("explore", "learn_old")


# ---------------------------------------------------------------- the frozen rules
class CostRules(FrozenRecord):
    broker: str
    version: str
    reference_price_usd: float = Field(gt=0)
    swap_long_usd_per_lot: float
    swap_short_usd_per_lot: float
    swap_triple_weekday: int = Field(ge=0, le=4)
    stress_overshoot_usd: float = Field(ge=0)


class ControlRules(FrozenRecord):
    timing_twins: int = Field(ge=0)
    min_session_minutes: int = Field(ge=1)
    fallback_minutes: int = Field(ge=1)


class ExploreRules(FrozenRecord):
    min_n: int
    min_n_slow: int
    slow_tfs: tuple[str, ...]
    min_t_day: float
    min_years_positive_share: float = Field(ge=0, le=1)
    min_control_excess: float
    min_drift_adj_gross: float


class EnsembleRules(FrozenRecord):
    min_gross_t: float
    min_members: int = Field(ge=2)


class ConfirmRules(FrozenRecord):
    min_n: int
    fdr_q: float = Field(gt=0, lt=1)
    bootstrap_resamples: int = Field(ge=100)
    min_years_positive_share: float = Field(ge=0, le=1)


class TestOldRules(FrozenRecord):
    min_n: int
    min_n_slow: int
    slow_tfs: tuple[str, ...]
    min_t_day: float


class NullRules(FrozenRecord):
    entry_types: tuple[str, ...]
    fade_mode: bool
    max_explore_pass_share: float = Field(ge=0, le=1)


class DsrRules(FrozenRecord):
    k_program: int = Field(ge=1)
    keff_rho: float = Field(gt=0, lt=1)


class PassRules(FrozenRecord):
    """config/research/sprint3000_rules.yaml: thresholds live there, never in code."""
    version: str
    cost: CostRules
    controls: ControlRules
    explore: ExploreRules
    ensemble: EnsembleRules
    confirm: ConfirmRules
    test_old: TestOldRules
    nulls: NullRules
    dsr: DsrRules


@lru_cache(maxsize=4)
def load_rules(path: Path = RULES_PATH) -> PassRules:
    return PassRules.model_validate(yaml.safe_load(Path(path).read_text()))


def rules_sha256(path: Path = RULES_PATH) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def _costs() -> A.ArmCosts:
    return A.load_costs(COSTS_PATH)


def is_null_control(spec: V.Spec, rules: PassRules | None = None) -> bool:
    r = (rules or load_rules()).nulls
    return spec.entry.type in r.entry_types or (r.fade_mode and spec.entry.params.get("mode") == "fade")


def _window(window: Window | str) -> Window:
    return window if isinstance(window, Window) else WINDOWS[window]


def years_needed(share: float, n_years: int) -> int:
    """5 of 8 at share 0.625; rounded up on other year counts (2 of 3)."""
    return math.ceil(share * n_years - 1e-9)


# ---------------------------------------------------------------- the C1 cost model
class C1Costs(FrozenRecord):
    """The cost of record's ICM point estimates per session (USD/oz at the reference price) and the measured swap."""
    reference_price_usd: float
    round_trip_usd: dict[str, float]
    overshoot_usd: dict[str, float]
    swap: SwapSpec


def c1_costs(costs: A.ArmCosts, rules: PassRules) -> C1Costs:
    """C1's inputs from the pinned cost of record (refuses another version). An untrusted overshoot cell charges the
    stress figure (ADR 0043); ICM's cells are trusted."""
    r = rules.cost
    cells = costs.require_version(r.version).cells[r.broker]
    shipped = SwapSpec.shipped()
    rt, os_ = {}, {}
    for s in A.SESSION_NAMES:
        cell = cells.get(s, cells[A.ALL_SESSIONS])
        rt[s] = cell.round_trip_usd
        os_[s] = cell.overshoot_usd if cell.overshoot_trusted else A.OVERSHOOT_STRESS_USD
    return C1Costs(reference_price_usd=r.reference_price_usd, round_trip_usd=rt, overshoot_usd=os_,
                   swap=SwapSpec(long_usd_per_lot=r.swap_long_usd_per_lot, short_usd_per_lot=r.swap_short_usd_per_lot,
                                 triple_weekday=r.swap_triple_weekday, server_tz=shipped.server_tz))


def cost_r(entry: np.ndarray, risk: np.ndarray, sessions: np.ndarray, overshoot_exit: np.ndarray, nights: np.ndarray,
           side: np.ndarray, c: C1Costs) -> np.ndarray:
    """C1: (RT_point_bps x P + OS_point_bps x P x [overshoot exit] - nights x swap_bps(side) x P) / stop_usd, bps =
    USD at the reference price / reference price. Positive = a cost; a credited short swap lowers it. One function so
    the shared `goldbot.research.cost_of_record.cost_r` (C1 lane, not on main at this writing) can replace it."""
    s = np.asarray(entry, float) / c.reference_price_usd
    rt = np.array([c.round_trip_usd[str(x)] for x in sessions], dtype=float)
    os_ = np.array([c.overshoot_usd[str(x)] for x in sessions], dtype=float)
    sw = c.swap.usd_per_oz(np.asarray(side))
    return (rt * s + os_ * s * np.asarray(overshoot_exit, float) - np.asarray(nights, float) * sw * s) \
        / np.asarray(risk, float)


# ---------------------------------------------------------------- the 1m walk
class Exit(NamedTuple):
    gross: float          # USD/oz, signed for the side
    kind: str             # stop | trail | target | flag | time
    k: int                # exit minute index
    t: int                # exit time, epoch ns
    gap: bool = False     # a stop exit filled at a minute open beyond the stop


def walk(tp: Tape, k0: int, kend: int, side: int, risk: float, tgt_r: float, trail: float, kflag: int,
         gap_at_open: bool = True) -> Exit:
    """One position from the open of minute k0 to at most the close of minute kend; `kflag` >= 0 exits at that minute's
    open (opposite signal). Searched in growing chunks, so a trade that ends early reads few minutes."""
    o, h, lo, c, t = tp.o, tp.h, tp.l, tp.c, tp.t
    kend = max(kend, k0)
    last = kend if kflag < 0 or kflag > kend else max(kflag - 1, k0)
    e = float(o[k0])
    stop0 = e - side * risk
    tgt = e + side * tgt_r * risk if np.isfinite(tgt_r) else np.nan
    trailing = np.isfinite(trail)
    carry = -np.inf if side > 0 else np.inf
    a, size = k0, 256
    while a <= last:
        b = min(last + 1, a + size)
        if side > 0:
            if trailing:
                hh = h[a:b]
                stop = np.maximum(stop0, np.maximum.accumulate(np.r_[carry, hh[:-1]]) - trail)
                carry = max(carry, float(hh.max()))
                hs = lo[a:b] <= stop
            else:
                stop = np.full(b - a, stop0)
                hs = lo[a:b] <= stop0
            ht = h[a:b] >= tgt if np.isfinite(tgt) else None
        else:
            if trailing:
                ll = lo[a:b]
                stop = np.minimum(stop0, np.minimum.accumulate(np.r_[carry, ll[:-1]]) + trail)
                carry = min(carry, float(ll.min()))
                hs = h[a:b] >= stop
            else:
                stop = np.full(b - a, stop0)
                hs = h[a:b] >= stop0
            ht = lo[a:b] <= tgt if np.isfinite(tgt) else None
        js = int(np.argmax(hs)) if hs.any() else BIG
        jt = int(np.argmax(ht)) if ht is not None and ht.any() else BIG
        if js <= jt and js < BIG:
            sp, op = float(stop[js]), float(o[a + js])
            gapped = gap_at_open and side * (op - sp) < 0
            px = op if gapped else sp
            kind = "trail" if side * (sp - stop0) > 1e-9 else "stop"
            return Exit(side * (px - e), kind, a + js, int(t[a + js]), gapped)
        if jt < BIG:
            return Exit(float(tgt_r * risk), "target", a + jt, int(t[a + jt]))
        a, size = b, size * 4
    if 0 <= kflag <= kend:
        return Exit(side * (float(o[kflag]) - e), "flag", kflag, int(t[kflag]))
    return Exit(side * (float(c[last]) - e), "time", last, int(t[last]) + MIN)


# ---------------------------------------------------------------- the simulation
class Sim(NamedTuple):
    real: pd.DataFrame
    opposite: pd.DataFrame        # the random-side control's other half: same minute, stop and exit, other side
    timing: pd.DataFrame          # same-side timing twins (diagnostic)
    n_signals: int = 0            # signals in the window (before one-at-a-time)


COLS = ["id", "bar", "t_dec", "t_fill", "t_exit", "side", "risk", "entry", "gross", "kind", "gap"]


def _frame(rows: list[tuple[Any, ...]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLS)
    return df.astype({"id": np.int64, "bar": np.int64, "t_dec": np.int64, "t_fill": np.int64, "t_exit": np.int64,
                      "side": np.int64, "risk": float, "entry": float, "gross": float, "kind": object, "gap": bool})


def simulate(spec: V.Spec, mk: Market, rules: PassRules | None = None) -> Sim:
    """The spec's real trades, their opposite-side twins and the timing twins (gross, unpriced)."""
    r = rules or load_rules()
    cr = r.controls
    tp, w = mk.tape, mk.window
    t, n_t = tp.t, len(tp.t)
    b = mk.bars[spec.tf]
    nb = len(b.dec)
    sig = V.spec_signals(spec, mk)
    keep = (sig.dec >= w.dec_from.value) & (sig.dec < w.end.value)
    ex = spec.exit_params()
    tgt_r = float(ex.r) if isinstance(ex, V.TargetR) else np.nan
    trail = ex.mult * V.atr(b, ex.atr_n, mk) if isinstance(ex, V.Chandelier) else None
    hold = min(spec.max_hold_bars, ex.bars) if isinstance(ex, V.TimeExit) else spec.max_hold_bars
    flags: dict[int, np.ndarray] = {}
    if spec.exit.type == "opposite_signal":
        L, S = V.entry_signals(spec.entry, b, mk)
        flags = {1: b.dec[S], -1: b.dec[L]}
    end = w.end.value

    def kflag_after(side: int, k: int, kend: int) -> int:
        fd = flags.get(side)
        if fd is None:
            return -1
        j = int(np.searchsorted(fd, t[k], side="right"))
        if j >= len(fd):
            return -1
        kf = int(np.searchsorted(t, fd[j], side="left"))
        return kf if kf <= kend and kf < n_t else -1

    def go(k: int, side: int, risk: float, tr: float, span: int) -> Exit:
        kend = int(np.searchsorted(t, min(int(t[k]) + span, end), side="left")) - 1
        return walk(tp, k, kend, side, risk, tgt_r, tr, kflag_after(side, k, kend))

    real: list[tuple[Any, ...]] = []
    opp: list[tuple[Any, ...]] = []
    timing: list[tuple[Any, ...]] = []
    busy = -1
    for i, dec, side, risk in zip(sig.bar[keep], sig.dec[keep], sig.side[keep], sig.risk[keep]):
        if dec < busy:
            continue
        k0 = int(np.searchsorted(t, dec, side="left"))
        if k0 >= n_t or t[k0] >= end:
            continue
        tr = float(trail[i]) if trail is not None else np.nan
        if trail is not None and not np.isfinite(tr):
            continue
        cap = int(b.dec[min(i + hold, nb - 1)])
        span = max(cap - int(t[k0]), MIN)
        x = go(k0, int(side), float(risk), tr, span)
        n_id = len(real)
        real.append((n_id, i, dec, t[k0], x.t, side, risk, tp.o[k0], x.gross, x.kind, x.gap))
        busy = x.t
        y = go(k0, -int(side), float(risk), tr, span)
        opp.append((n_id, i, dec, t[k0], y.t, -side, risk, tp.o[k0], y.gross, y.kind, y.gap))
        for k in C.leak_free_minutes(spec.id, n_id, k0, mk.run_end, t, cr.timing_twins, cr.min_session_minutes,
                                     cr.fallback_minutes):
            if k < n_t and t[k] < end:
                z = go(k, int(side), float(risk), tr, span)
                timing.append((n_id, i, dec, t[k], z.t, side, risk, tp.o[k], z.gross, z.kind, z.gap))
    return Sim(_frame(real), _frame(opp), _frame(timing), int(keep.sum()))


def window_drift_rate(mk: Market) -> float:
    """USD per oz per ns: the window's in-window drift, (last close - first close) / elapsed over its entry span."""
    t = mk.tape.t
    a = int(np.searchsorted(t, mk.window.dec_from.value, side="left"))
    z = int(np.searchsorted(t, mk.window.end.value, side="left")) - 1
    if z <= a:
        return 0.0
    return float((mk.tape.c[z] - mk.tape.o[a]) / (t[z] + MIN - t[a]))


# ---------------------------------------------------------------- pricing
def price(df: pd.DataFrame, costs: A.ArmCosts | None = None, rules: PassRules | None = None) -> pd.DataFrame:
    """Adds gross_r, cost_r and net_r (C1, gated), net_r_usd (the dollar upper-bound stress line: `screen_charges` and
    the shipped swap), stress_r (C1 with the 0.70 overshoot on every stop exit), and the entry's year and UTC day.
    Needs t_fill, t_exit, side, risk, gross, kind, entry (gap: False when absent)."""
    rr = rules or load_rules()
    r = rr.cost
    costs = costs or _costs()
    out = df.copy()
    if out.empty:
        for col in ("gross_r", "cost_r", "net_r", "net_r_usd", "stress_r"):
            out[col] = np.zeros(0)
        out["year"], out["day"] = np.zeros(0, np.int64), np.zeros(0, np.int64)
        return out
    c1 = c1_costs(costs, rr)
    ent = pd.DatetimeIndex(pd.to_datetime(out.t_fill.to_numpy(np.int64), utc=True))
    ext = pd.DatetimeIndex(pd.to_datetime(out.t_exit.to_numpy(np.int64), utc=True))
    sess = DEFAULT_SESSIONS.session_label(ent)
    side = out.side.to_numpy()
    g, risk, entry = out.gross.to_numpy(float), out.risk.to_numpy(float), out.entry.to_numpy(float)
    stop_exit = out.kind.isin(STOP_KINDS).to_numpy()
    gap = out.gap.to_numpy(bool) if "gap" in out else np.zeros(len(out), bool)
    nights = rollover_nights(ent, ext, c1.swap.server_tz, c1.swap.triple_weekday)
    out["gross_r"] = g / risk
    out["cost_r"] = cost_r(entry, risk, sess, stop_exit & ~gap, nights, side, c1)
    out["net_r"] = out.gross_r - out.cost_r
    stress = c1.model_copy(update={"overshoot_usd": {k: r.stress_overshoot_usd for k in c1.overshoot_usd}})
    out["stress_r"] = out.gross_r - cost_r(entry, risk, sess, stop_exit, nights, side, stress)
    shipped = SwapSpec.shipped()
    sw_old = rollover_nights(ent, ext, shipped.server_tz, shipped.triple_weekday) * shipped.usd_per_oz(side)
    out["net_r_usd"] = A.screen_net_r(g, risk, stop_exit, costs, broker=r.broker, sessions=sess, version=r.version,
                                      extra_usd=-sw_old)
    out["year"] = ent.year.to_numpy()
    out["day"] = out.t_fill.to_numpy(np.int64) // DAY
    return out


def attach_controls(real: pd.DataFrame, opposite: pd.DataFrame, drift_rate: float) -> pd.DataFrame:
    """Priced real trades plus their opposite twin's net R (`opp_net_r`) and the drift-adjusted gross R."""
    out = real.copy()
    o = opposite.set_index("id").net_r if len(opposite) else pd.Series(dtype=float)
    out["opp_net_r"] = out.id.map(o).to_numpy(float) if len(out) else np.zeros(0)
    hold = (out.t_exit - out.t_fill).to_numpy(float) if len(out) else np.zeros(0)
    out["drift_adj_gross_r"] = out.gross_r.to_numpy(float) - out.side.to_numpy() * drift_rate * hold \
        / out.risk.to_numpy(float) if len(out) else np.zeros(0)
    return out


# ---------------------------------------------------------------- statistics
def _t(x: np.ndarray) -> float:
    if len(x) < 2:
        return float("nan")
    s = float(np.std(x, ddof=1))
    return float(np.mean(x) / (s / np.sqrt(len(x)))) if s > 0 else float("nan")


def day_sums(net: np.ndarray, t_fill: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(entry UTC day, summed net R) over days with a trade."""
    days, inv = np.unique(np.asarray(t_fill, np.int64) // DAY, return_inverse=True)
    return days, np.bincount(inv, weights=np.asarray(net, float), minlength=len(days))


def boot_p(daily: np.ndarray, resamples: int, seed: int) -> float:
    """One-sided bootstrap p of mean(daily) > 0, days resampled with replacement under the centred null."""
    x = np.asarray(daily, float)
    if len(x) < 2:
        return 1.0
    obs = float(x.mean())
    x0 = x - obs
    rng = np.random.default_rng(seed)
    hits, done = 0, 0
    step = max(1, 2_000_000 // len(x))
    while done < resamples:
        k = min(step, resamples - done)
        hits += int((x0[rng.integers(0, len(x), (k, len(x)))].mean(axis=1) >= obs).sum())
        done += k
    return (1 + hits) / (resamples + 1)


def bh_fdr(p: np.ndarray, m: int, q: float) -> np.ndarray:
    """Benjamini-Hochberg step-up: reject the k smallest p with p_(k) <= k q / m (m >= len(p): unsent count as 1)."""
    p = np.asarray(p, float)
    if not len(p):
        return np.zeros(0, bool)
    m = max(m, len(p))
    order = np.argsort(p, kind="stable")
    ok = p[order] <= np.arange(1, len(p) + 1) * q / m
    out = np.zeros(len(p), bool)
    if ok.any():
        out[order[: int(np.flatnonzero(ok).max()) + 1]] = True
    return out


def k_eff(x: np.ndarray, rho: float) -> int:
    """Effective trials among the rows of `x` (one daily net-R series per spec): average-linkage clusters at
    correlation distance 1 - rho; a flat row (no trade) is its own trial."""
    x = np.asarray(x, float)
    if x.shape[0] <= 1:
        return int(x.shape[0])
    with np.errstate(invalid="ignore", divide="ignore"):
        cor = np.corrcoef(x)
    d = 1.0 - np.nan_to_num(cor, nan=0.0)
    np.fill_diagonal(d, 0.0)
    d = np.clip((d + d.T) / 2, 0.0, 2.0)
    return int(fcluster(linkage(d[np.triu_indices(len(d), 1)], method="average"), t=1.0 - rho,
                        criterion="distance").max())


def _seed(sid: str) -> int:
    return int.from_bytes(hashlib.sha256(sid.encode()).digest()[:4], "big")


def _f(x: float | None) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), 6)


def _years(days: np.ndarray, daily: np.ndarray) -> pd.Series:
    """Summed daily net R by calendar year of the entry day."""
    yrs = pd.DatetimeIndex(pd.to_datetime(np.asarray(days, np.int64) * DAY, utc=True)).year
    return pd.Series(np.asarray(daily, float)).groupby(np.asarray(yrs)).sum()


# ---------------------------------------------------------------- one spec's result
Status = Literal["evaluated", "not_evaluable", "no_data"]


class SpecResult(FrozenRecord):
    """One spec on one window: summary statistics only."""
    id: str
    rank: int
    family: str
    tf: str
    window: str
    status: Status = "evaluated"
    pending_types: tuple[str, ...] = ()
    null_control: bool = False
    n_signals: int = 0
    n: int = 0
    n_days: int = 0
    gross_r: float | None = None
    gross_t: float | None = None
    net_r: float | None = None
    net_r_usd: float | None = None                # stress: today's dollars, upper bounds, shipped swap (not gated)
    stress_net_r: float | None = None
    cost_r: float | None = None
    sd_r: float | None = None
    t: float | None = None
    t_day: float | None = None
    control_net_r: float | None = None            # the random-side control (gate)
    delta: float | None = None                    # net minus the random-side control
    drift_adj_gross_r: float | None = None
    timing_net_r: float | None = None             # same-side timing twins (diagnostic)
    timing_delta: float | None = None
    n_timing: int = 0
    legs: dict[str, dict[str, float | None]] = Field(default_factory=dict)
    hit: float | None = None
    median_risk_usd: float | None = None
    exit_mix: dict[str, float] = Field(default_factory=dict)
    year_net_r: dict[str, float] = Field(default_factory=dict)
    years_positive: int = 0
    years_needed: int = 0
    dsr: float | None = None
    dsr_k: int = 0
    dsr_keff: float | None = None
    dsr_inputs: dict[str, float] | None = None
    ex2025: dict[str, float | None] | None = None  # confirm: the same reading without the 2025 entries
    p_boot: float | None = None
    gates: dict[str, bool] = Field(default_factory=dict)
    passed: bool = False
    verdict: str = "FAIL"


def _gates(window: Window, tf: str, rules: PassRules, n: int, net: float, t_day: float, years_pos: int,
           drift_adj: float, delta: float) -> dict[str, bool]:
    fin = np.isfinite
    n_years = len(window.entry_years)
    if window.name == "confirm":
        c = rules.confirm
        return {"n": n >= c.min_n, "net": bool(net > 0),
                "years": years_pos >= years_needed(c.min_years_positive_share, n_years)}
    if window.name == "test_old":
        o = rules.test_old
        return {"n": n >= (o.min_n_slow if tf in o.slow_tfs else o.min_n), "net": bool(net > 0),
                "t_day": bool(fin(t_day) and t_day >= o.min_t_day)}
    e = rules.explore
    return {"n": n >= (e.min_n_slow if tf in e.slow_tfs else e.min_n), "net": bool(net > 0),
            "t_day": bool(fin(t_day) and t_day >= e.min_t_day),
            "years": years_pos >= years_needed(e.min_years_positive_share, n_years),
            "drift_adj": bool(fin(drift_adj) and drift_adj > e.min_drift_adj_gross),
            "control": bool(fin(delta) and delta >= e.min_control_excess)}


def _verdict(gates: dict[str, bool]) -> tuple[bool, str]:
    passed = bool(gates) and all(gates.values())
    return passed, "PASS" if passed else ("FAIL-COUNT" if not gates.get("n", True) else "FAIL")


def _leg(df: pd.DataFrame) -> dict[str, float | None]:
    if not len(df):
        return {"n": 0.0, "net_r": None, "gross_r": None, "t_day": None}
    _, d = day_sums(df.net_r.to_numpy(float), df.t_fill.to_numpy())
    return {"n": float(len(df)), "net_r": _f(float(df.net_r.mean())), "gross_r": _f(float(df.gross_r.mean())),
            "t_day": _f(_t(d))}


def summarize(spec: V.Spec, real: pd.DataFrame, window: Window | str, rules: PassRules | None = None,
              timing: pd.DataFrame | None = None, k: int | None = None, n_signals: int = 0) -> SpecResult:
    """The spec's statistics and gates on `window`. `real` is priced with `attach_controls` columns (opp_net_r,
    drift_adj_gross_r); confirm's BH-FDR gate is the run's."""
    r = rules or load_rules()
    w = _window(window)
    k = k or r.dsr.k_program
    base: dict[str, Any] = {"id": spec.id, "rank": spec.rank, "family": spec.family, "tf": spec.tf, "window": w.name,
                            "null_control": is_null_control(spec, r), "n_signals": n_signals, "dsr_k": k}
    n = len(real)
    if n == 0:
        gates = _gates(w, spec.tf, r, 0, float("nan"), float("nan"), 0, float("nan"), float("nan"))
        return SpecResult(**base, gates=gates, passed=False, verdict="FAIL-COUNT")
    net = real.net_r.to_numpy(float)
    gross = real.gross_r.to_numpy(float)
    days, daily = day_sums(net, real.t_fill.to_numpy())
    t_day = _t(daily)
    opp = real.opp_net_r.to_numpy(float)
    ok = np.isfinite(opp)
    delta = float(np.mean(0.5 * (net[ok] - opp[ok]))) if ok.any() else float("nan")
    ctrl = float(np.mean(0.5 * (net[ok] + opp[ok]))) if ok.any() else float("nan")
    drift_adj = float(real.drift_adj_gross_r.mean())
    t_net, t_d, n_tw = float("nan"), float("nan"), 0
    if timing is not None and len(timing):
        mm = timing.groupby("id").net_r.mean()
        has = real.id.isin(mm.index).to_numpy()
        if has.any():
            t_d = float((real.net_r[has] - real.id[has].map(mm)).mean())
        t_net, n_tw = float(timing.net_r.mean()), len(timing)
    yr = real.groupby("year").net_r.mean()
    years_pos = sum(1 for y in w.entry_years if y in yr.index and yr[y] > 0)
    dsr = inputs = None
    if n >= MIN_TRADES_FOR_DSR:
        di = DsrInputs.of(net)
        dsr, inputs = di.dsr(k), di.model_dump()
    ex: dict[str, float | None] | None = None
    p_boot = None
    if w.name == "confirm":
        rest = real[real.year != 2025]
        _, dd = day_sums(rest.net_r.to_numpy(float), rest.t_fill.to_numpy())
        ex = {"n": float(len(rest)), "net_r": _f(float(rest.net_r.mean())) if len(rest) else None, "t_day": _f(_t(dd)),
              "drift_adj_gross_r": _f(float(rest.drift_adj_gross_r.mean())) if len(rest) else None}
        p_boot = boot_p(daily, r.confirm.bootstrap_resamples, _seed(spec.id))
    gates = _gates(w, spec.tf, r, n, float(net.mean()), t_day, years_pos, drift_adj, delta)
    passed, verdict = _verdict(gates)
    return SpecResult(
        **base, n=n, n_days=len(days), gross_r=_f(float(gross.mean())), gross_t=_f(_t(gross)),
        net_r=_f(float(net.mean())), net_r_usd=_f(float(real.net_r_usd.mean())),
        stress_net_r=_f(float(real.stress_r.mean())), cost_r=_f(float(real.cost_r.mean())),
        sd_r=_f(float(np.std(net, ddof=1)) if n > 1 else float("nan")), t=_f(_t(net)), t_day=_f(t_day),
        control_net_r=_f(ctrl), delta=_f(delta), drift_adj_gross_r=_f(drift_adj), timing_net_r=_f(t_net),
        timing_delta=_f(t_d), n_timing=n_tw,
        legs={"long": _leg(real[real.side > 0]), "short": _leg(real[real.side < 0])},
        hit=_f(float((net > 0).mean())), median_risk_usd=_f(float(real.risk.median())),
        exit_mix={str(a): round(float(b), 4) for a, b in real.kind.value_counts(normalize=True).items()},
        year_net_r={str(a): round(float(b), 4) for a, b in yr.items()}, years_positive=years_pos,
        years_needed=years_needed(r.confirm.min_years_positive_share if w.name == "confirm"
                                  else r.explore.min_years_positive_share, len(w.entry_years)),
        dsr=_f(dsr) if dsr is not None else None, dsr_inputs=inputs, ex2025=ex, p_boot=p_boot, gates=gates,
        passed=passed, verdict=verdict)


Daily = tuple[np.ndarray, np.ndarray, int]      # (entry days, day-summed net R, trades)


def run_spec(spec: V.Spec, mk: Market, rules: PassRules, k: int) -> tuple[SpecResult, np.ndarray, np.ndarray]:
    """One spec end to end: (result, entry days, day-summed net R); the daily series feeds K_eff and the ensembles,
    never the output."""
    sim = simulate(spec, mk, rules)
    real, opp, tim = (price(x, rules=rules) for x in (sim.real, sim.opposite, sim.timing))
    real = attach_controls(real, opp, window_drift_rate(mk))
    res = summarize(spec, real, mk.window, rules, tim, k, n_signals=sim.n_signals)
    days, daily = day_sums(real.net_r.to_numpy(float), real.t_fill.to_numpy()) if len(real) else \
        (np.zeros(0, np.int64), np.zeros(0))
    return res, days, daily


# ---------------------------------------------------------------- the ensemble stage
class Ensemble(FrozenRecord):
    """One family's composite, frozen at the end of explore: its members, equal weight."""
    id: str
    family: str
    members: tuple[str, ...]


class EnsembleResult(FrozenRecord):
    id: str
    family: str
    members: tuple[str, ...]
    window: str
    n: int = 0
    n_days: int = 0
    net_r_day: float | None = None                # mean equal-weight composite net R per entry day
    t_day: float | None = None
    year_net_r: dict[str, float] = Field(default_factory=dict)
    years_positive: int = 0
    p_boot: float | None = None
    gates: dict[str, bool] = Field(default_factory=dict)
    passed: bool = False
    verdict: str = "FROZEN"


def build_ensembles(results: Sequence[SpecResult], rules: PassRules) -> list[Ensemble]:
    """Per family: its explore passers and every evaluated member with per-trade gross t >= min_gross_t (rank order);
    a family with fewer than min_members has no ensemble."""
    fams: dict[str, list[str]] = {}
    for x in sorted(results, key=lambda s: s.rank):
        if x.status != "evaluated":
            continue
        if x.passed or (x.gross_t is not None and x.gross_t >= rules.ensemble.min_gross_t):
            fams.setdefault(x.family, []).append(x.id)
    return [Ensemble(id=f"ENS-{f}", family=f, members=tuple(m)) for f, m in fams.items()
            if len(m) >= rules.ensemble.min_members]


def ensemble_result(ens: Ensemble, daily: dict[str, Daily], window: Window | str, rules: PassRules) -> EnsembleResult:
    """The equal-weight composite: each day's summed net R of every member, divided by the member count."""
    w = _window(window)
    parts = [daily[m] for m in ens.members if m in daily]
    base: dict[str, Any] = {"id": ens.id, "family": ens.family, "members": ens.members, "window": w.name}
    if not parts or not any(len(p[0]) for p in parts):
        return EnsembleResult(**base, verdict="FAIL-COUNT" if w.name not in EXPLORE_LIKE else "FROZEN")
    days = np.unique(np.concatenate([p[0] for p in parts]))
    comp = np.zeros(len(days))
    for d, v, _ in parts:
        comp[np.searchsorted(days, d)] += v
    comp /= len(ens.members)
    n = int(sum(p[2] for p in parts))
    yr = _years(days, comp)
    years_pos = sum(1 for y in w.entry_years if y in yr.index and yr[y] > 0)
    t_day = _t(comp)
    p_boot = boot_p(comp, rules.confirm.bootstrap_resamples, _seed(ens.id)) if w.name == "confirm" else None
    gates: dict[str, bool] = {}
    if w.name == "confirm":
        gates = {"n": n >= rules.confirm.min_n, "net": bool(comp.mean() > 0),
                 "years": years_pos >= years_needed(rules.confirm.min_years_positive_share, len(w.entry_years))}
    elif w.name == "test_old":
        gates = {"n": n >= rules.test_old.min_n, "net": bool(comp.mean() > 0),
                 "t_day": bool(np.isfinite(t_day) and t_day >= rules.test_old.min_t_day)}
    passed, verdict = _verdict(gates) if gates else (False, "FROZEN")
    return EnsembleResult(**base, n=n, n_days=len(days), net_r_day=_f(float(comp.mean())), t_day=_f(t_day),
                          year_net_r={str(a): round(float(b), 4) for a, b in yr.items()}, years_positive=years_pos,
                          p_boot=p_boot, gates=gates, passed=passed, verdict=verdict)


# ---------------------------------------------------------------- the run
class NullCheck(FrozenRecord):
    n: int
    evaluated: int
    passed: int
    share: float | None


class ScreenReport(FrozenRecord):
    window: str
    status: Literal["OK", "VOID", "NO-DATA"]
    audit_required: bool = False
    catalogue_sha256: str
    catalogue_version: str
    rules_sha256: str
    rules_version: str
    cost_version: str
    k: int
    k_eff_run: int
    k_eff: int
    m: int | None = None
    n_specs: int
    n_evaluated: int
    n_not_evaluable: int
    n_passed: int
    nulls: NullCheck
    drift_usd_per_day: float
    years_missing: list[int]
    data_first_utc: str
    data_last_utc: str
    workers: int
    elapsed_s: float
    results: list[SpecResult]
    ensembles: list[EnsembleResult] = Field(default_factory=list)


def null_check(window: str, results: Sequence[SpecResult], rules: PassRules) -> tuple[NullCheck, bool, bool]:
    """(counts, void, audit_required): prereg section 7 rule 1. On explore/learn_old more than
    `max_explore_pass_share` of the evaluated null controls passing voids the run ("the engine or its control is
    leaking"); on confirm/test_old any null passing requires an engine audit before anything deploys."""
    nulls = [x for x in results if x.null_control]
    ev = [x for x in nulls if x.status == "evaluated"]
    passed = sum(x.passed for x in ev)
    share = passed / len(ev) if ev else None
    void = window in EXPLORE_LIKE and share is not None and share > rules.nulls.max_explore_pass_share
    audit = window in ("confirm", "test_old") and passed > 0
    return NullCheck(n=len(nulls), evaluated=len(ev), passed=passed, share=share), void, audit


_MK: Market | None = None


def _init(path: str) -> None:
    global _MK
    with open(path, "rb") as fh:
        _MK = pickle.load(fh)  # noqa: S301 - our own cache, written by this run's parent process


def _work(args: tuple[V.Spec, PassRules, int]) -> tuple[SpecResult, np.ndarray, np.ndarray]:
    assert _MK is not None
    return run_spec(args[0], _MK, args[1], args[2])


def _locality(s: V.Spec) -> tuple[str, str, str]:
    return s.tf, s.entry.type, repr(sorted(s.entry.params.items()))


def _run_all(todo: list[V.Spec], mk: Market, r: PassRules, k: int, workers: int,
             cache_dir: Path | None) -> dict[str, tuple[SpecResult, np.ndarray, np.ndarray]]:
    jobs = sorted(todo, key=_locality)              # specs sharing indicators run near each other
    out: dict[str, tuple[SpecResult, np.ndarray, np.ndarray]] = {}
    if workers <= 1:
        for s in jobs:
            out[s.id] = run_spec(s, mk, r, k)
        return out
    cdir = Path(cache_dir) if cache_dir is not None else Path(tempfile.gettempdir())
    cdir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=cdir, suffix=".market.pkl", delete=False) as fh:
        pickle.dump(mk.model_copy(update={"memo": {}}), fh, protocol=pickle.HIGHEST_PROTOCOL)
        path = fh.name
    try:
        with ProcessPoolExecutor(workers, initializer=_init, initargs=(path,)) as pool:
            for s, got in zip(jobs, pool.map(_work, [(s, r, k) for s in jobs], chunksize=2)):
                out[s.id] = got
    finally:
        Path(path).unlink(missing_ok=True)
    return out


def run_screen(cat: V.Catalogue, mk: Market, workers: int = 8, k: int | None = None, cache_dir: Path | None = None,
               ids: Sequence[str] | None = None, m: int | None = None, rules: PassRules | None = None,
               singles: Sequence[str] | None = None, ensembles: Sequence[Ensemble] | None = None) -> ScreenReport:
    """Every (or the listed) catalogue spec on the market's window. Specs using a pending type are reported
    not_evaluable; a window with no bars on disk reports no_data. `singles` are the ids eligible to pass (default: all
    run); the others run as ensemble members only. Explore/learn_old freeze the ensembles; confirm/test_old evaluate the
    given ones; confirm applies BH-FDR over `m` (default: singles + ensembles sent)."""
    t0 = time.monotonic()
    r = rules or load_rules()
    k = k or r.dsr.k_program
    w = mk.window.name
    want = set(ids) if ids is not None else None
    specs = [s for s in cat.specs if want is None or s.id in want]
    eligible = set(singles) if singles is not None else {s.id for s in specs}
    empty = len(mk.tape.t) == 0
    out = _run_all([s for s in specs if not s.pending], mk, r, k, workers, cache_dir) if not empty else {}
    results: list[SpecResult] = []
    for s in specs:
        if s.id in out:
            res = out[s.id][0]
            if s.id not in eligible:
                res = res.model_copy(update={"passed": False, "verdict": "MEMBER-ONLY"})
            results.append(res)
            continue
        spec_status: Status = "not_evaluable" if s.pending else "no_data"
        results.append(SpecResult(id=s.id, rank=s.rank, family=s.family, tf=s.tf, window=w, status=spec_status,
                                  pending_types=s.pending, null_control=is_null_control(s, r), dsr_k=k,
                                  verdict="NOT-EVALUABLE" if s.pending else "NO-DATA"))
    # K_eff over every spec of the run (one with no trade is its own trial); DSR reported at K and K_eff, never gated
    all_days = np.unique(np.concatenate([v[1] for v in out.values()])) if out else np.zeros(0, np.int64)
    mat = np.zeros((len(results), max(len(all_days), 2)))
    for row, x in enumerate(results):
        if x.id in out:
            mat[row, np.searchsorted(all_days, out[x.id][1])] = out[x.id][2]
    ke_run = k_eff(mat, r.dsr.keff_rho) if results else 0
    ke = k - len(results) + ke_run
    final = [x.model_copy(update={"dsr_keff": _f(DsrInputs.model_validate(x.dsr_inputs).dsr(ke))})
             if x.dsr_inputs is not None else x for x in results]
    daily: dict[str, Daily] = {sid: (v[1], v[2], v[0].n) for sid, v in out.items()}
    ens = list(ensembles) if ensembles is not None else (build_ensembles(final, r) if w in EXPLORE_LIKE else [])
    ens_res = [ensemble_result(e, daily, mk.window, r) for e in ens]
    if w == "confirm":
        m = m if m is not None else len(eligible) + len(ens_res)
        ev = [i for i, x in enumerate(final) if x.status == "evaluated" and x.id in eligible]
        p = [final[i].p_boot if final[i].p_boot is not None else 1.0 for i in ev]
        p += [e.p_boot if e.p_boot is not None else 1.0 for e in ens_res]
        rej = bh_fdr(np.array(p), m, r.confirm.fdr_q)
        for i, ok in zip(ev, rej[: len(ev)]):
            g = {**final[i].gates, "bh_fdr": bool(ok)}
            passed, verdict = _verdict(g)
            final[i] = final[i].model_copy(update={"gates": g, "passed": passed, "verdict": verdict})
        for j, ok in enumerate(rej[len(ev):]):
            g = {**ens_res[j].gates, "bh_fdr": bool(ok)}
            passed, verdict = _verdict(g)
            ens_res[j] = ens_res[j].model_copy(update={"gates": g, "passed": passed, "verdict": verdict})
    nc, void, audit = null_check(w, final, r)
    run_status: Literal["OK", "VOID", "NO-DATA"] = "NO-DATA" if empty else ("VOID" if void else "OK")
    t = mk.tape.t
    return ScreenReport(
        window=w, status=run_status, audit_required=audit, catalogue_sha256=cat.sha256, catalogue_version=cat.version,
        rules_sha256=rules_sha256(), rules_version=r.version, cost_version=r.cost.version, k=k, k_eff_run=ke_run,
        k_eff=ke, m=m if w == "confirm" else None, n_specs=len(final),
        n_evaluated=sum(x.status == "evaluated" for x in final),
        n_not_evaluable=sum(x.status == "not_evaluable" for x in final), n_passed=sum(x.passed for x in final),
        nulls=nc, drift_usd_per_day=round(window_drift_rate(mk) * DAY, 6) if not empty else 0.0,
        years_missing=list(mk.years_missing),
        data_first_utc=pd.Timestamp(int(t.min()), tz="UTC").isoformat() if len(t) else "",
        data_last_utc=mk.data_last_utc, workers=workers, elapsed_s=round(time.monotonic() - t0, 1), results=final,
        ensembles=ens_res)


# ---------------------------------------------------------------- passer chains
NEEDS: dict[str, str | None] = {"explore": None, "learn_old": None, "confirm": "explore", "test_old": "confirm"}


def _check_report(cat: V.Catalogue, report: dict[str, Any] | None, window: str) -> str | None:
    need = NEEDS[window]
    if need is None:
        return None
    if report is None:
        raise ValueError(f"a {window} run reads {need} passers only: pass the {need} report (--passers)")
    if report.get("window") != need:
        raise ValueError(f"a {window} run needs a {need} report, got a {report.get('window')!r} report")
    if report.get("catalogue_sha256") != cat.sha256:
        raise ValueError("the passers report was run on another catalogue (sha256 differs)")
    if report.get("status") == "VOID":
        raise ValueError(f"the {need} run is VOID (null controls passed): fix the engine and re-run it first")
    return need


def passer_ids(cat: V.Catalogue, report: dict[str, Any] | None, window: str,
               ids: Sequence[str] | None = None) -> list[str]:
    """The single ids a `window` run may gate: explore/learn_old any catalogue id; confirm only explore passers,
    test_old only confirm passers (of a report on this very catalogue), in rank order. An id outside is refused."""
    need = _check_report(cat, report, window)
    known = [s.id for s in cat.specs]
    if need is None:
        if ids is None:
            return known
        bad = [i for i in ids if i not in set(known)]
        if bad:
            raise ValueError(f"not in the catalogue: {bad[:10]}")
        return [i for i in known if i in set(ids)]
    assert report is not None
    passed = {str(x["id"]) for x in report.get("results", []) if x.get("passed")}
    order = [i for i in known if i in passed]
    if ids is None:
        return order
    refused = [i for i in ids if i not in passed]
    if refused:
        raise ValueError(f"not {need} passers, refused: {refused[:20]}")
    return [i for i in order if i in set(ids)]


class Plan(FrozenRecord):
    """What a window runs: the gated singles, the ensembles sent, every id to simulate, and confirm's BH m."""
    singles: list[str]
    ensembles: list[Ensemble]
    run_ids: list[str]
    m: int | None = None


def plan(cat: V.Catalogue, report: dict[str, Any] | None, window: str, ids: Sequence[str] | None = None) -> Plan:
    """Confirm: the explore passers and the ensembles frozen in the explore report, m = their count (fixed here,
    before any confirm bar is read). Test_old: the confirm passers and the ensembles that passed confirm."""
    singles = passer_ids(cat, report, window, ids)
    ens: list[Ensemble] = []
    if report is not None and NEEDS[window] is not None:
        for e in report.get("ensembles", []):
            if window == "confirm" or e.get("passed"):
                ens.append(Ensemble(id=e["id"], family=e["family"], members=tuple(e["members"])))
    members = {mid for e in ens for mid in e.members}
    run = [s.id for s in cat.specs if s.id in set(singles) | members]
    return Plan(singles=singles, ensembles=ens, run_ids=run,
                m=len(singles) + len(ens) if window == "confirm" else None)
