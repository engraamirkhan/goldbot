"""sr-v3 Stage 0: the label-blind size gate (docs/research/prereg-2026-11-26-sr-v3.md, rev 6, section 2.3, quant rulings
Q1 and Q2, rev-4 texts A-C) and the frozen constants of card 3.6.

Q1, as coded (`ladder`): "The gate is Riley on ridge effective df, computed label-blind on the smallest training fold
before Stage 0. ... Each parameter is charged against the n_eff that informs it:
- unit intercept and instrument levels: n_i = Kish n_eff ÷ [1 + (m̄ − 1)·ρ_u,day], where m̄ = Σm²/Σm per
  instrument-day and ρ_u,day is the residual (not outcome) same-day ICC, upper 95 % CI (D-12);
- β column j: n_j = Kish n_eff ÷ [1 + (m̄ − 1)·ρ_x,j,day·ρ_u,day], and when pooled, further
  ÷ [1 + (k − 1)·ρ_x,j,inst·ρ_u,inst]. ...
- df_p = n_p/(n_p + λ_p). Gate: Σ_p 430·df_p / n_p ≤ 1."

How the inputs are read (each is recorded in the Stage 0 report):
- "Kish n_eff" of an instrument = Σu × the Kish efficiency (Σu)²/(N·Σu²) of its fold weights (`InstCounts`): Σu is the
  card 2.2 count (one event per bar, phase 0), the efficiency is the measured `KISH_LADDER` (0.62 until measured).
  m = Σu per instrument-session-day (2.2's S_d), m̄ = Σm²/Σm, floored at 1 (dense_labels.n_eff's max(S_d, 1)).
- The unit intercept is unpenalised (card 3.3), so df = 1 and it is charged 430/n. In a pooled body it is informed by
  every member: n = Σ_i n_i ÷ [1 + (k − 1)·ρ_u,inst] (the ρ_x = 1 case of the β rule).
- Instrument levels: λ_a = 1.35/τ_a² of the pooled-body row (D-12 (d)); a gold body has none.
- ρ_x,j: the D-12a file's upper 95 % CI where measured (core columns, 1m..4h); a column or tf it does not cover takes
  ρ_x = 1, the rev-3 floor, and is listed in `RhoInputs.defaults`.
- ρ_u,day: the D-12 binding per class and hold bucket, floored at 0.05 for holds <= 60 min and at 0.25 above
  (ADR 0085 Q1: "labels of <= 60 min take max(0.05, the measured bound); longer labels keep rho_day, floor 0.25").

Orders (rev 4 A): the LEVEL is the longest prefix of the level order (core-12, then the rest of P30) that meets the
gate, set before Stage 0 and never moved by block admission. The SERVED body is the longest prefix of the served order
(core-6, then each admitted block whole in X1..X6 order (X6 price action last), the first block that does not fit ending the block section,
then the rest of core-12, then the rest of P30), never fewer than core-6. A unit below L3 is intercept-only (B8).
Tie rule (Q2): the body with the higher level; on equal level, gold.

This module reads no label: its inputs are the outcome-blind skeleton, the uniqueness weights and the committed D-12
file (card test 18 (iv)). It is on the obj-v1 guard (objective.GUARDED_MODULES).
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord

# ---------------------------------------------------------------------------------------------- frozen constants (3.6)
SPEC_VERSION = "sr-v3"
PREREG = "docs/research/prereg-2026-11-26-sr-v3.md"
CLIP_R = 10.0
LAMBDA_ROWS = 540.0
RESIDUAL_VAR = 1.35                 # R²: λ = RESIDUAL_VAR / τ², τ = 0.05 R per input sd
TAU_A_FLOOR = 0.01                  # R: LAMBDA_INST's floor on τ_a
RILEY_PER_PARAM = 430.0
MIN_KEPT = 100
MIN_KEPT_DAYS = 30
MIN_EDGE_R = 0.05
MIN_CTRL_EDGE_R = 0.05
MIN_BLOCK_DS_R = 0.01
ALPHA_S = 0.05
ALPHA_HOLM = 0.10
BOOT = 10_000
MEAN_BLOCK_DAYS = 5
SEED_BOOT, SEED_B2, SEED_CANARY = 20261206, 20261207, 20261208
JUDGED_HEAD = "T2"
KISH_LADDER = 0.62
RHO_DAY_PRIOR = 0.25
RHO_FLOOR_LE_60M = 0.05
RHO_INST = 0.5
FIRST_OOF = 2012
FIRST_OOF_B = 2014                  # remedy (b), only for a unit still below L3 after (a)
# the smallest training folds (card 2.3, rev 9 D2): (a), and remedy (b); FIRST_OOF / FIRST_OOF_B follow them
FOLD_A: tuple[int, ...] = (2010, 2011)
FOLD_B: tuple[int, ...] = (2010, 2011, 2012, 2013)
LEARN_YEARS: tuple[int, ...] = tuple(range(2010, 2018))
MIN_LEARN_CANDIDATES = 1_500
MIN_PER_FOLD = 60
MIN_FRESH_NEFF = 200
SD_R = 1.2
LEVELS: tuple[tuple[str, int], ...] = (("L1", 40), ("L2", 12), ("L3", 6))
MAX_INPUTS = 40

# ---------------------------------------------------------------------------------------------- columns (3.1)
# quant ruling 2026-11-26 (label-blind, before any sr-v3 read): core-12 position 4 is rv_ratio, replacing
# atr_ratio_14_100, which moves to the rest of P30 in its 3.1 position, at every timeframe (card rev 8 follows)
CORE_12: tuple[str, ...] = ("side", "tsmom_score", "dist_vwap48_atr", "rv_ratio", "er_30", "bb_z_20",
                            "ret_16", "ret_96", "donchian_pos_20", "adx14", "compression_8_96",
                            "min_since_london_open")
CORE_6: tuple[str, ...] = CORE_12[:6]
# P30 = sr-v2's 40 inputs minus the 8 source-scaled cost/tick columns (cost enters only as the offset) and the two d1
# columns that are structurally NaN today (they return in X2); the rest in sr-v2's FEATURES order (a test pins it)
DROPPED_COST: tuple[str, ...] = ("em_atr_cost", "em_yz_4_cost", "em_yz_16_cost", "em_yz_48_cost", "spread_atr",
                                 "spread_rel_median_48", "tick_count_z_48", "tick_vol_ratio_20")
DROPPED_D1: tuple[str, ...] = ("d1_atr_ratio_14_100", "d1_rv_ratio")
P30_REST: tuple[str, ...] = ("atr_ratio_14_100", "atr14_pct", "vol_ts_yz_20_60", "vol_of_vol_60", "jump_z",
                             "bars_since_jump",
                             "vol_tercile", "hour_utc", "dow", "min_since_newyork_open", "session_id", "ret_4",
                             "slope_ema50", "dist_ema50_atr", "rsi14", "sess_high_dist_atr", "sess_low_dist_atr",
                             "d1_slope_ema20")
P30: tuple[str, ...] = (*CORE_12, *P30_REST)
BLOCK_ORDER: tuple[str, ...] = ("X1", "X2", "X3", "X4", "X5", "X6")
# X6 price action (PR #484, goldbot/features/price_action_x6.py): the last block of the served order (quant 2026-11-26).
# A block is served only once admitted by the block screen (5.2); none is admitted before Stage 0
X6_COLUMNS: tuple[str, ...] = ("x6_body_frac", "x6_clv", "x6_io_bar", "x6_ms_state", "x6_ms_bos", "x6_lv_dist_atr",
                               "x6_lv_room_net_atr", "x6_lv_brk_state")

# ---------------------------------------------------------------------------------------------- units
H_S: dict[str, int] = {"1m": 3_600, "5m": 4 * 3_600, "15m": 8 * 3_600, "30m": 16 * 3_600, "1h": 86_400,
                       "2h": 2 * 86_400, "4h": 5 * 86_400, "1d": 10 * 86_400, "1w": 20 * 86_400}   # = dense_labels
UNITS: dict[str, tuple[str, ...]] = {"1m": ("1m",), "5m": ("5m",), "15m": ("15m",), "30m": ("30m",), "1h": ("1h",),
                                     "2h": ("2h",), "4h": ("4h",), "1d+1w": ("1d", "1w")}
GOLD = "XAUUSD"
FX10: tuple[str, ...] = ("AUDJPY", "AUDUSD", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF",
                         "USDJPY")
METALS: tuple[str, ...] = ("XAGUSD",)
POOLED_MEMBERS: tuple[str, ...] = (GOLD, *FX10, *METALS)      # pooled body: every graded A/A-cons instrument (a)
Body = Literal["gold", "pooled"]


def instrument_class(instrument: str) -> str:
    return "gold" if instrument == GOLD else "metals" if instrument in METALS else "fx10" if instrument in FX10 \
        else "other"


def hold_bucket(tf: str) -> str:
    """The D-12 hold bucket of a tf's time barrier H: le60m, 1h-4h, gt4h."""
    h = H_S[tf]
    return "le60m" if h <= 3_600 else "1h-4h" if h <= 4 * 3_600 else "gt4h"


def rho_class(body: Body, members: Sequence[str]) -> str:
    """The D-12 class row of a body: gold alone, gold+fx10 without metals, else pooled."""
    if body == "gold":
        return "gold"
    return "pooled" if any(m in METALS for m in members) else "gold+fx10"


def level_name(prefix: int) -> str:
    for name, need in LEVELS:
        if prefix >= need:
            return name
    return "below L3"


def file_sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------------------------- inputs
class RhoInputs(FrozenRecord):
    """The measured inputs of one body's ladder at one tf (Q1), with their source."""
    rho_class: str
    bucket: str
    rho_u_day: float
    rho_u_inst: float
    lambda_a: float
    rho_x_day: dict[str, float]
    rho_x_inst: dict[str, float]
    defaults: tuple[str, ...]           # columns whose ρ_x is the rev-3 floor 1.0 (not measured)
    source: str
    source_sha256: str | None

    def x_day(self, col: str) -> float:
        return self.rho_x_day.get(col, 1.0)

    def x_inst(self, col: str) -> float:
        return self.rho_x_inst.get(col, 1.0)


def rev3_inputs(columns: Sequence[str] = P30) -> RhoInputs:
    """The rev-3 bound (test 18 (i)): ρ_day 0.25, ρ_inst 0.5, every ρ_x = 1, λ_a = 540."""
    return RhoInputs(rho_class="rev3", bucket="any", rho_u_day=RHO_DAY_PRIOR, rho_u_inst=RHO_INST, lambda_a=LAMBDA_ROWS,
                     rho_x_day={}, rho_x_inst={}, defaults=tuple(columns), source="card rev 3", source_sha256=None)


def _upper(cell: Mapping[str, Any] | None) -> float | None:
    if not cell:
        return None
    v = cell.get("upper95")
    return None if v is None or not math.isfinite(float(v)) else float(v)


def rho_inputs(path: Path | str, *, body: Body, members: Sequence[str], tf: str,
               columns: Sequence[str] = P30, freeze_sha: str | None = None) -> RhoInputs:
    """The D-12 / D-12a measurements (docs/research/rho-measurement-2026-11.json) for a body at a tf. ρ_x is read on the
    smallest fold (`fold_2010_11`), the fold the gate is computed on; a negative upper bound counts as 0."""
    d = json.loads(Path(path).read_text())
    rc, bucket = rho_class(body, members), hold_bucket(tf)
    b = d["binding"]
    day = b["rho_u_day"][f"{rc}|{bucket}"]
    rho_day = float(day.get("rho_value", day["max"]))
    rho_day = max(RHO_FLOOR_LE_60M if bucket == "le60m" else RHO_DAY_PRIOR, rho_day)
    if body == "gold":
        rho_inst, lam_a = 0.0, LAMBDA_ROWS
    else:
        # a pooled body reads rho_u,inst and tau_a: Stage 0 is a power read, so the file must be measured at the
        # xt-v1 exit freeze (quant ruling on #514, D-12-xt; code review of #514). Gold reads neither. `freeze_sha` is
        # dense_labels.EXIT_FREEZE_SHA256 from the (unguarded) caller; None refuses (fail closed).
        from goldbot.config import xt_v1_rho_refusal
        if freeze_sha is None:
            raise ValueError(f"pooled Stage 0 on {path!s} without freeze_sha: a power read needs the xt-v1 exit freeze "
                             "sha (dense_labels.EXIT_FREEZE_SHA256) to check the rho file (D-12-xt): refused")
        if (why := xt_v1_rho_refusal(d, path, freeze_sha)) is not None:
            raise ValueError(why)
        rho_inst = min(1.0, float(b["rho_u_inst_day_mean"][f"{rc}|{bucket}"]["max"]))
        tau = max(TAU_A_FLOOR, float(b["tau_a"][f"pooled|{bucket}"]["tau_a"]))     # D-12 (d): the pooled-body row
        lam_a = RESIDUAL_VAR / tau ** 2
    xd, xi, defaults = {}, {}, []
    for c in columns:
        cell = d.get("rho_x", {}).get(f"fold_2010_11|{tf}|{rc}|{c}") or {}
        ud, ui = _upper(cell.get("day")), _upper(cell.get("inst_agg"))
        if ud is None or (body == "pooled" and ui is None):
            defaults.append(c)
            continue
        xd[c] = min(1.0, max(0.0, ud))
        if ui is not None:
            xi[c] = min(1.0, max(0.0, ui))
    return RhoInputs(rho_class=rc, bucket=bucket, rho_u_day=rho_day, rho_u_inst=rho_inst, lambda_a=lam_a,
                     rho_x_day=xd, rho_x_inst=xi, defaults=tuple(defaults), source=str(path),
                     source_sha256=file_sha256(path))


class InstCounts(FrozenRecord):
    """One instrument's label-blind counts on a fold: Σu, the Kish efficiency, m̄ and the rows."""
    instrument: str
    rows: int
    days: int
    sum_u: float
    kish_eff: float
    m_bar: float

    @property
    def kish_n(self) -> float:
        return self.sum_u * self.kish_eff


def inst_counts(instrument: str, u: np.ndarray, day: np.ndarray, *, kish_eff: float | None = None) -> InstCounts:
    """From one event per bar (phase 0): u = the uniqueness weight u_tf, day = its session day. `kish_eff` overrides
    the measured efficiency (the rev-3 table uses KISH_LADDER)."""
    u = np.asarray(u, dtype=float)
    keep = u > 0
    u, dd = u[keep], np.asarray(day)[keep]
    if not len(u):
        return InstCounts(instrument=instrument, rows=0, days=0, sum_u=0.0, kish_eff=0.0, m_bar=1.0)
    s = pd.Series(u).groupby(dd).sum().to_numpy()
    eff = float(u.sum() ** 2 / (len(u) * (u ** 2).sum())) if kish_eff is None else float(kish_eff)
    return InstCounts(instrument=instrument, rows=len(u), days=len(s), sum_u=float(u.sum()), kish_eff=eff,
                      m_bar=max(1.0, float((s ** 2).sum() / s.sum())))


# ---------------------------------------------------------------------------------------------- the gate
class Charge(FrozenRecord):
    name: str
    n: float
    lam: float
    charge: float                   # 430 · df / n = 430 / (n + λ)


class Ladder(FrozenRecord):
    """One body's ladder at one fold: the fixed charges, the column charges in order and the prefix that fits."""
    body: str
    order: tuple[str, ...]
    fixed: tuple[Charge, ...]
    columns: tuple[Charge, ...]
    prefix: int
    level: str
    budget_equal_n: float           # rev-3 metric: β columns affordable at the unit intercept's n (fractional)
    total_fixed: float


def _charge(name: str, n: float, lam: float) -> Charge:
    c = RILEY_PER_PARAM / (n + lam) if n + lam > 0 else math.inf
    return Charge(name=name, n=float(n), lam=float(lam), charge=c)


def ladder(counts: Sequence[InstCounts], order: Sequence[str], rho: RhoInputs, *, body: Body,
           lam: float = LAMBDA_ROWS) -> Ladder:
    """Q1 on one body: Σ_p 430·df_p/n_p ≤ 1 for the unit intercept, the instrument levels (pooled) and the longest
    prefix of `order`."""
    cs = [c for c in counts if c.sum_u > 0]
    k = len(cs)
    n_i = {c.instrument: c.kish_n / (1 + (c.m_bar - 1) * rho.rho_u_day) for c in cs}
    pooled = body == "pooled"
    inst_deff = 1 + (k - 1) * rho.rho_u_inst if pooled else 1.0
    n_unit = sum(n_i.values()) / inst_deff
    fixed = [_charge("intercept", n_unit, 0.0)]
    if pooled:
        fixed += [_charge(f"level:{i}", n, rho.lambda_a) for i, n in sorted(n_i.items())]
    total = sum(f.charge for f in fixed)
    cols, used = [], total
    for j in order:
        xd, xi = rho.x_day(j), rho.x_inst(j)
        nj = sum(c.kish_n / (1 + (c.m_bar - 1) * xd * rho.rho_u_day) for c in cs)
        if pooled:
            nj /= 1 + (k - 1) * xi * rho.rho_u_inst
        ch = _charge(j, nj, lam)
        if used + ch.charge > 1.0 + 1e-12:
            break
        cols.append(ch)
        used += ch.charge
    per = RILEY_PER_PARAM / (n_unit + lam) if n_unit + lam > 0 else math.inf
    budget = (1.0 - total) / per if math.isfinite(per) and per > 0 else -math.inf
    return Ladder(body=body, order=tuple(order), fixed=tuple(fixed), columns=tuple(cols), prefix=len(cols),
                  level=level_name(len(cols)), budget_equal_n=float(budget), total_fixed=float(total))


CountsOf = Callable[[tuple[str, ...]], Sequence[InstCounts]]
Counts = Sequence[InstCounts] | CountsOf


def counts_fn(counts: Counts) -> CountsOf:
    """Rev 9 D1: counts as a function of the column set (rows usable under those columns). A plain list is one row set
    for every column set (a store whose rows were all usable under every column)."""
    if callable(counts):
        return counts
    fixed = list(counts)
    return lambda cols: fixed


def prefix_ladder(counts: Counts, order: Sequence[str], rho: RhoInputs, *, body: Body,
                  lam: float = LAMBDA_ROWS) -> Ladder:
    """Rev 9 D1 with quant C1 (verbatim): "Every ladder prefix and every candidate served prefix is charged in full,
    with all its parameters, on its own usable rows; the charges are never computed incrementally on one row set."
    Prefix k fits iff `ladder` on the rows usable under order[:k] admits all k columns; the level is the longest
    prefix that fits (rows only shrink as k grows, so the gate is monotone in k)."""
    cof = counts_fn(counts)
    order = tuple(order)
    best = ladder(cof(()), (), rho, body=body, lam=lam)
    for k in range(1, len(order) + 1):
        lk = ladder(cof(order[:k]), order[:k], rho, body=body, lam=lam)
        if lk.prefix < k:
            break
        best = lk
    return best.model_copy(update={"order": order})


def level_order(available: Sequence[str] | None = None) -> tuple[str, ...]:
    """Rev 4 A: core-12 in its 3.1 order, then the rest of P30; a column a body cannot carry (`available`, e.g. the
    bid-only drop of a pooled body) is left out, never reordered."""
    av = set(P30 if available is None else available)
    return tuple(c for c in P30 if c in av)


def served_body(counts: Counts, rho: RhoInputs, *, body: Body,
                admitted: Sequence[tuple[str, Sequence[str]]] = (), available: Sequence[str] | None = None,
                lam: float = LAMBDA_ROWS) -> tuple[str, ...]:
    """Rev 4 A: core-6; then each admitted block whole, in X1..X6 order (X6 last), the first that does not fit ending the block
    section; then the rest of core-12 and of P30 as a prefix. Never fewer than core-6 (the caller serves it only for a
    unit at L3 or above)."""
    av = set(P30 if available is None else available)
    core6 = [c for c in CORE_6 if c in av]
    blocks = sorted(admitted, key=lambda b: BLOCK_ORDER.index(b[0]))
    cof = counts_fn(counts)

    def fits(cols: Sequence[str]) -> bool:          # each candidate charged in full on its own usable rows (C1)
        return ladder(cof(tuple(cols)), cols, rho, body=body, lam=lam).prefix == len(cols)

    cols = list(core6)
    for _, bc in blocks:
        if fits([*cols, *bc]):
            cols += list(bc)
        else:
            break
    for c in level_order(available):
        if c in cols:
            continue
        if fits([*cols, c]):
            cols.append(c)
        else:
            break
    if len(cols) > MAX_INPUTS:
        raise ValueError(f"served body of {len(cols)} columns exceeds {MAX_INPUTS}")
    return tuple(cols)


def tie_rule(gold: Ladder, pooled: Ladder | None) -> Body:
    """Q2: the body with the higher level; on equal level, gold."""
    rank = {"L1": 3, "L2": 2, "L3": 1, "below L3": 0}
    if pooled is None:
        return "gold"
    return "pooled" if rank[pooled.level] > rank[gold.level] else "gold"


# ---------------------------------------------------------------------------------------------- the Stage 0 decision
class Stage0Unit(FrozenRecord):
    """One unit's Stage 0 result: both bodies' ladders on the smallest fold, the choice and what is served."""
    unit: str
    tfs: tuple[str, ...]
    status: Literal["fit", "not_started_data"]
    fold: tuple[int, ...]
    first_oof: int
    remedy_b: bool
    gold: Ladder | None
    pooled: Ladder | None
    body: Body | None
    members: tuple[str, ...]
    level: str
    judged: bool                     # at L3 or above: Stage L may read it
    served: tuple[str, ...]          # () = intercept-only (B8)
    rho: dict[str, RhoInputs]
    counts: dict[str, list[InstCounts]]              # per body, on the rows usable under the served columns
    note: str = ""
    prefix_rows: dict[str, list[int]] = {}           # per body: usable rows under level_order[:k], k = 0..len (D1)


def decide_unit(unit: str, tfs: Sequence[str], *, counts_a: Mapping[str, Counts],
                counts_b: Mapping[str, Counts] | None, rho: Mapping[str, RhoInputs],
                available: Mapping[str, Sequence[str]],
                admitted: Sequence[tuple[str, Sequence[str]]] = ()) -> Stage0Unit:
    """(a) on fold 2010-11, both bodies, tie rule; a unit still below L3 tries (b) on fold 2010-13 (first OOF 2014).
    Rev 9 D2: a unit with no usable gold row on (a) tries (b) too; NOT STARTED (data) only when gold has no usable row
    on both folds. Below L3 after both: intercept-only, not judged, still fitted and served. Counts are per column
    set (rev 9 D1, `prefix_ladder`); a plain list is one row set for every column set."""
    def bodies(counts: Mapping[str, Counts]) -> dict[str, CountsOf]:
        return {b: counts_fn(counts.get(b, ())) for b in ("gold", "pooled")}

    def run(cof: Mapping[str, CountsOf]) -> tuple[Ladder | None, Ladder | None]:
        g = prefix_ladder(cof["gold"], level_order(available["gold"]), rho["gold"], body="gold") \
            if any(c.sum_u > 0 for c in cof["gold"](())) else None
        p = prefix_ladder(cof["pooled"], level_order(available["pooled"]), rho["pooled"], body="pooled") \
            if "pooled" in rho and sum(c.sum_u > 0 for c in cof["pooled"](())) > 1 else None
        return g, p
    fold_b: tuple[int, ...] = FOLD_B
    ca = bodies(counts_a)
    cb = bodies(counts_b) if counts_b is not None else None
    g, p = run(ca)
    fold: tuple[int, ...] = FOLD_A
    first, remedy, cof = FIRST_OOF, False, ca
    if g is None and cb is not None:
        gb, pb = run(cb)
        if gb is not None:
            g, p, fold, first, remedy, cof = gb, pb, fold_b, FIRST_OOF_B, True, cb
    if g is None:
        return Stage0Unit(unit=unit, tfs=tuple(tfs), status="not_started_data", fold=fold, first_oof=first,
                          remedy_b=False, gold=None, pooled=p, body=None, members=(), level="below L3", judged=False,
                          served=(), rho=dict(rho), counts={k: list(f(())) for k, f in ca.items()},
                          note="no usable gold row on fold 2010-11 or 2010-13 (ADR 0104 4): NOT STARTED (data), 0 K")
    choice = tie_rule(g, p)
    chosen = g if choice == "gold" else p
    assert chosen is not None
    if chosen.level == "below L3" and cb is not None and not remedy:
        gb, pb = run(cb)
        if gb is not None:
            cb_choice = tie_rule(gb, pb)
            lb = gb if cb_choice == "gold" else pb
            if lb is not None and lb.level != "below L3":
                g, p, choice, chosen = gb, pb, cb_choice, lb
                fold, first, remedy, cof = fold_b, FIRST_OOF_B, True, cb
    judged = chosen.level != "below L3"
    served = served_body(cof[choice], rho[choice], body=choice, admitted=admitted,
                         available=available[choice]) if judged else ()
    members = tuple(c.instrument for c in cof[choice](tuple(served)) if c.sum_u > 0)
    prefix_rows = {b: [sum(c.rows for c in cof[b](level_order(available[b])[:k]))
                       for k in range(len(level_order(available[b])) + 1)] for b in cof if b in rho}
    return Stage0Unit(unit=unit, tfs=tuple(tfs), status="fit", fold=fold, first_oof=first, remedy_b=remedy, gold=g,
                      pooled=p, body=choice, members=members, level=chosen.level, judged=judged, served=served,
                      rho=dict(rho), counts={k: list(f(tuple(served))) for k, f in cof.items()},
                      prefix_rows=prefix_rows,
                      note="" if judged else "below L3: intercept-only (B8) body, never judged, served on demo "
                                             "(a per-instrument level minus cost(x): a cost-and-level filter)")


__all__ = ["BLOCK_ORDER", "CORE_12", "CORE_6", "FIRST_OOF", "FIRST_OOF_B", "JUDGED_HEAD", "LAMBDA_ROWS", "LEVELS",
           "P30", "RILEY_PER_PARAM", "SPEC_VERSION", "UNITS", "Charge", "InstCounts", "Ladder", "RhoInputs",
           "Stage0Unit", "counts_fn", "decide_unit", "prefix_ladder", "hold_bucket", "inst_counts", "instrument_class", "ladder", "level_name",
           "level_order", "rev3_inputs", "rho_class", "rho_inputs", "served_body", "tie_rule"]
