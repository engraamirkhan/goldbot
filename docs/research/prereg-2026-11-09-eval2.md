# Pre-registration 2026-11-09: EVAL-2 thresholds, frozen signatures, gamma sequences, integrity record

Status: **signed by the quant-reviewer 2026-11-09** (014d4c93; resamples helper condition for Lane 3, section 4.6). Author: research director, 2026-11-09. Written before any EVAL-2 code exists, before S1
has run, before any L2 fit on real cells and before any TEST-OLD return or EVAL-2 2018-25 return is read.
Method: `docs/research/eval2-method-2026-11-09.md` (v6, signed by the quant-reviewer 2026-11-09). ADRs: 0076
(EVAL-2), 0077 (Ruling A withdrawal), 0078 (snooping ledger). Where this file and the method differ on a number, the
figures update of section 2 (quant-verified 2026-11-09) wins; on anything else the method wins and this file is
corrected.

What this file is not: no registry row, no K, no budget slot. It freezes the rules and the API the build lanes code
and test against. Lanes 1-5B must not start before it is merged with the quant's signature (method, build plan (a)).
Holdout 2025-10-01..2026-09-30 is not read by anything here.

## 1. Frozen thresholds (from v6; no change without a new signed amendment)

| Item | Value |
|---|---|
| Twins per instance M | 20, replay-only; same instrument, timeframe, side, hour +/-1 h, regime level when the card registers one |
| Twin draw window | intraday: decision bars in the 7 days after the signal's exit; 1d: the 20 bars after exit; bars where any instance of the same family fires excluded |
| Twin geometry | stop, target and hold = the signal's ATR multiples at the **twin's** decision-bar ATR; exit logic the signal's; same cost path, swap included |
| Clustering | standard errors clustered by session day |
| Card admission | expected gross >= 2x deciding cost (3x for <= 15m); median round trip / native stop <= 0.15 R, decision-time inputs only |
| Power floor | n_min = ((z(1 - q/m) + 0.84) x sd_card / delta_card)^2 must fit the window's expected n; m = families actually read, fixed before the read |
| L1 probe | symmetric first passage at +/-1 ATR (decision-TF ATR), from the fill bar's close, capped at the native horizon; side-0 OCO bar = loss |
| L1 family retire (NO SIGNAL) | pooled 90 % UB of probe excess < cheapest feasible cell's deciding cost in ATR **and** power >= 0.8 at that cost |
| L2 model | theta_c = mu_F + a_TF + b_inst + u_c on day-clustered cell means of excess; REML; Student-t; prior fitted leave-family-out on every cell ever viewed (real + NULL-RAND), de-duplicated by config hash; empirical null from the controls (Efron). Student-t (df 5, fixed) on the cell random effects only; Gaussian likelihood on day-clustered cell means; S1 runs with this exact spec. |
| L2 carry | P(theta_c > 0) >= 0.80 **and** P(net_c > 0 at 1.5x deciding cost) >= 0.80; at most 3 cells per family by posterior mean |
| L2 cell kill | P(net > 0.05 R) < 0.05 |
| L2 threshold rule | 0.80 raised, never lowered, until the S1 null carry rate is <= 5 %; thresholds, model and code hash frozen in the card before the L2 run on real cells |
| Futility retire (all units, ADR 0077) | 300 <= n < 1,000: one-sided 90 % UB of the card-named statistic < +0.03 R **and** clustered SE <= 0.055 R, both pre-registered and counted in K; else "inconclusive (event floor)"; < 300 "insufficient" |
| L3 windows | TEST-OLD primary: intraday (<= 4h) 2008-01..2009-12 only; 1d 2006-02..2009-12. 2006-07 intraday added back only if, after the refill, the 4h sqrt-time ATR ratio is within 15 % **and** the median 1m range (bp) is within 1.5x of 2008-09, both run outcome-blind and recorded in the card before any TEST-OLD return is read. 2018-01..2025-09 corroboration |
| L3 statistic | control-variate net: family precision-weighted mean excess + mean net of all twins in the carried cells, at 1.0x deciding cost; weights fixed from explore (n, sd) in the frozen card |
| L3 p | one-sided stationary day-block bootstrap (mean block = E1 lag), instances resampled with their twins |
| L3 gate (all AND) | BatchPRDS rejection at q 0.10 on that p (section 3); one-sided p of mean excess <= 0.10; bootstrap 80 % LB of net at 1.5x deciding cost > 0; over 2006-09 + 2018-25: net > 0 in >= 3 calendar years, at least one of 2021/2022, no 2-year sub-period with net t <= -2; 2018-25 corroboration one-sided p <= 0.10 on net (CONFIRM-FRESH carried cells only); E9 for holds >= 1 day |
| L3 cells | after the family rejects: Holm over its <= 3 carried cells at the family's BatchPRDS level, on each cell's control-variate net p |
| L3 read condition | power >= 0.3 on TEST-OLD at the L2 shrunk estimate, else INSUFFICIENT (power) and neither window is read; the 0.3 floor is not lowered |
| CONFIRM-SEEN | trigger class or defining geometry looked at on that window (formal or pre-freeze, trade or diagnostic) on the same instrument at the same or lower TF, or excess overlap e >= 15 pp; never in that window's hypothesis; keeps the 2018-25 p <= 0.10 AND with no credit; also needs the forward holdout |
| HOLDOUT-SEEN | trigger class matches a rule scored on bars >= 2025-10-01 (P1, P2, P6, P8-A) or e >= 15 pp (overlap script sha256 76b075b0...); fixed in the card before L2, never changed |
| Forward holdout (SEEN) | shadow replay of the frozen card after the freeze, read once at n = min(12 months, power 0.8 at half the L3 shrunk estimate), L3 control-variate net, one-sided alpha 0.05 |
| L4 (i) execution | realised cost <= modelled + 0.02 R, paired fill vs replay v2, per broker and leg; kill-only |
| L4 (ii) exit mix | stop/target/time shares vs the history Dirichlet, about 50 trades |
| L4 (iii) futility | e >= 10 against H1: excess >= max(0.05 R, 0.5 x L3 shrunk estimate), after >= 100 signals -> KILLED |
| L4 (iv) edge | e vs H0 excess <= 0; reported; e >= 20 = "proven forward" |
| L4 ON TRACK | (i)-(iii) green after >= 50 fills per family |
| Deciding cost | max(today R, bp-engine R on the window) + overshoot x (stop-exit share + stop-entry share) + swap (strict-interior rollover, #343, twins too) + positive S5 gap until S5 passes; gates at 1.5x; pin `atr-pin-2026-11-09.2` (re-seeded from 2026-10-01) |
| Stress line | 2010-17 $-constant: 15m 0.151, 30m 0.103, 1h 0.071, 4h 0.035 R; reported, never gated; net < 0 -> COST-SENSITIVE |
| Executable side | intrabar stop/limit families <= 4h: L2 and L3 also need net > 0 with triggers on ask (buy) / bid (sell) and fills at the trigger level |
| S0-S6 | method section 6 table, verbatim; CI fast set: planted +0.20 R on 1h/1,000 passes >= 80 %, planted 0 passes <= 5 % |
| L5 sizing ceiling offered | risk at which the 95th-percentile simulated drawdown <= 8 % (owner decides) |

## 2. K and figures (quant-verified 2026-11-09; supersede the method's 3,479 / 21,341 / 97)

- Registry after the sprint-3000 backfill: **6,961 rows**, sha 73fc51e1...; `n_trials_effective` **3,471**;
  quarter_trials(2026Q4) **3,395**; headroom **105** under the 3,500 budget.
- Snooping ledger (ADR 0078): 17,862 (sensitivity 16,680). **DSR K = 3,471 + 17,862 = 21,333** (sensitivity
  **20,151**). Reported, not gated, before L5.
- Backfilled rows carry ts = 2026-11-09. Anything that orders reads by time, the BatchPRDS batch numbering included,
  uses `results.backfill.true_read_ts`, never the row ts.
- Registrations stay frozen until Lane 0's `tests/test_registry_unlinked_prereg.py` is green on CI and merged.
- Not trials, not counted: the S1-S3 plants and null replicates; replay v2 itself (a relabel). An arm re-read on v2
  is a new read under its own prereg.

## 3. BatchPRDS and the gamma sequences

### 3.1 Verified against the paper
Zrnic, Jiang, Ramdas and Jordan, "The power of batching in multiple hypothesis testing", AISTATS 2020
(arXiv 1910.04968), section 5, Definition 4 and Algorithm 1, read 2026-11-09:
- Input: FDR level alpha, non-negative {gamma_s} with sum_s gamma_s = 1. alpha_1 = gamma_1 alpha. For t = 1, 2, ...:
  run BH at level alpha_t on batch P_t; set **alpha_{t+1} = alpha gamma_{t+1} (n_{t+1} + sum_{s<=t} R_s) / n_{t+1}**.
- It controls FDP-hat(t) = sum_{s<=t} alpha_s n_s / (n_s + sum_{r<s} R_r) <= alpha.
- Theorem 3: anytime FDR(t) <= alpha when every batch is PRDS **and** null p-values are independent of other batches.
- The method's "BatchBH (BatchPRDS variant)" is this Algorithm 1 (`BatchPRDS_BH`); the paper's BatchBH (Algorithm 2,
  uses R+_s) assumes independence and is not used. Here n_t = m_t, the families read in batch t, fixed before the read.

**Quant ruling 2026-11-09:** both windows use pure alpha-spending: alpha_j = q·gamma_j, BH at alpha_j within batch j (PRDS within a batch), no rejection credit ever. Valid under arbitrary cross-batch dependence: FDR ≤ Σ_j FDR_j ≤ q. Algorithm 1's rejection reward is not used.

### 3.2 The sequences (one form, both windows, q = 0.10)
gamma_1 = 0.5; gamma_j = 0.5 x (6 / pi^2) / (j - 1)^2 for j >= 2 (sums to 1). This is the paper's two choices joined:
half the budget up front, as the paper does for larger batches, and the s^-2 decay it uses for small batches, so the
level never vanishes abruptly for later families.

| j | gamma_j | alpha_j = q gamma_j (0 prior rejections) |
|---|---|---|
| 1 | 0.500000 | 0.0500000 |
| 2 | 0.303964 | 0.0303964 |
| 3 | 0.075991 | 0.0075991 |
| 4 | 0.033774 | 0.0033774 |
| 5 | 0.018998 | 0.0018998 |
| 6-14 | 0.012159, 0.008443, 0.006203, 0.004749, 0.003753, 0.003040, 0.002512, 0.002111, 0.001799 | q x gamma_j |
| 15 | 0.001551 | 0.0001551 |
| 16 | 0.001351 | 0.0001351 |

- **TEST-OLD (2006-02..2009-12):** never read. EVAL-2 batch 1 is index 1, alpha_1 = 0.05.
- **2018-01..2025-09:** batches 1-14 are the earlier reads in true read order (by `true_read_ts`), each credited
  0 rejections: 1-9 = P1-P9, 10-14 = F1-F5 (ledger, section 6.1). F6 and F7 read no outcomes (m 0) and are not
  batches. EVAL-2's first 2018-25 read is **batch 15**, alpha_15 = 0.000155 (BH at m 3: z about 3.88). Power there
  is about 0: 2018-25 corroborates, it does not decide.

### 3.3 Re-derived for this file
C03-V1 30m on TEST-OLD, alpha_1 0.05, BH m 3 one true: z(1 - 0.05/3) = 2.128; SE 1.56 / sqrt(218) = 0.1057; full
excess 0.2655 R -> Phi(2.513 - 2.128) = 0.650; half -> 0.192. On 2008-09 only (n 109): 0.363 / 0.108. Matches the
method (65/19, 36/11). Ruling A: 1.3 / sqrt(150) = 0.106 R. `config/settings.yaml:266` holds
`screen_min_events_daily: 150`; `screen_retire_floor` is absent; `goldbot/` has no mSPRT;
`arm_stops.unit_stop` (`goldbot/research/arm_stops.py:168`) is called via `daily_review._arm_health` (`:614`).

## 4. Frozen function signatures (Lanes 1-5 code and test against these)

Conventions: `numpy as np`, `pandas as pd`; `Bars` from `goldbot.research.catalogue.data`, `Events` from
`goldbot.research.catalogue.core`, `CostModel` from `goldbot.research.catalogue.costs`. Results are
`@dataclass(frozen=True, slots=True)`. Times are int64 epoch ns (`goldbot.data.timeutil.epoch_ns`). R values are
floats in R units; ATR values in decision-TF ATR. A name, argument order, keyword-only marker or return type may not
change without a signed amendment; private helpers are free. Every random draw takes an explicit `seed_key: str`.

### 4.1 Twins (Lane 1, `goldbot/research/catalogue/sim.py`)
```python
TWIN_M: Final = 20
TWIN_DAYS_INTRADAY: Final = 7
TWIN_BARS_1D: Final = 20
TWIN_HOUR_TOL_H: Final = 1

@dataclass(frozen=True, slots=True)
class TwinDraw:
    signal_k: int                  # index into Events
    decision_bars: np.ndarray      # int64[TWIN_M]; -1 = no eligible bar
    stratum: tuple[str, str, int, int, int | None]   # (inst, tf, side, hour_utc, regime_level)
    n_eligible: int

def twin_pool(b: Bars, ev: Events, k: int, *, exit_bar: int, family_fire: np.ndarray,
              regime: np.ndarray | None = None) -> np.ndarray: ...          # int64 eligible decision bars
def draw_twins(b: Bars, ev: Events, *, exits: np.ndarray, family_fire: np.ndarray, seed_key: str,
               m: int = TWIN_M, regime: np.ndarray | None = None) -> list[TwinDraw]: ...
def simulate_twins(b: Bars, ev: Events, draws: Sequence[TwinDraw], *, costs: CostModel,
                   fine: Bars | None = None, opposite_side: bool = False) -> np.ndarray: ...
    # float64[n_signals, m] net R at the deciding cost; NaN where not drawn
def excess_r(signal_net: np.ndarray, twin_net: np.ndarray) -> np.ndarray: ...   # signal - nanmean(twins, axis=1)
def twin_correlation(signal_net: np.ndarray, twin_net: np.ndarray, day: np.ndarray) -> float: ...  # S0's rho
```
`goldbot/research/catalogue/run.py` adds `read_batch_index(window: Literal["test_old", "confirm"]) -> int`, counting
ledger reads ordered by `true_read_ts` (2018-25 returns 15 for EVAL-2's first read).

### 4.2 Costs and lint (Lane 2, `goldbot/research/catalogue/costs.py`, new `lint.py`)
```python
GATE_COST_MULT: Final = 1.5
ATR_PIN: Final = "atr-pin-2026-11-09.2"

@dataclass(frozen=True, slots=True)
class DecidingCost:
    r: float                # total at 1.0x
    today_r: float
    bp_engine_r: float
    overshoot_r: float
    swap_r: float
    s5_gap_r: float         # 0.0 once S5 passes for (broker, tf band)
    pin: str

def deciding_cost(inst: str, tf: str, *, side: int, price: float, atr_decision: float, stop_atr: float,
                  t_fill_ns: int, t_exit_ns: int, stop_exit_share: float, stop_entry_share: float,
                  window: Literal["explore", "test_old", "confirm", "forward"], broker: str = "icm",
                  costs: CostModel | None = None) -> DecidingCost: ...
def stress_cost(inst: str, tf: str, *, atr_decision: float, stop_atr: float) -> float: ...   # R, 2010-17 $-constant
def cost_sensitive(net_at_stress_r: float) -> bool: ...                                      # net < 0

# lint.py
class RealisedFieldFilter(ValueError): ...
def lint_card(card: Mapping[str, Any]) -> list[str]: ...   # empty = clean
def assert_decision_time(card: Mapping[str, Any]) -> None: ...   # raises RealisedFieldFilter
```

### 4.3 Ladder stats (Lane 3, `goldbot/research/catalogue/stats.py`)
```python
def cluster_se(x: np.ndarray, day: np.ndarray) -> float: ...
def cell_day_means(units: pd.DataFrame, *, col: str = "excess") -> pd.DataFrame: ...  # cell_id, day, mean, n
def power_floor_n(*, q: float, m: int, sd: float, delta: float, power: float = 0.8) -> int: ...
def mde(*, n: int, sd: float, alpha: float, power: float = 0.8) -> float: ...
def power_at(*, n: int, sd: float, delta: float, alpha: float) -> float: ...
def control_variate_net(units: pd.DataFrame, weights: Mapping[str, float]) -> float: ...
def year_rule(units: pd.DataFrame, *, chop_years: tuple[int, ...]) -> dict[str, bool]: ...
    # keys: years_pos_ge3, chop_year_pos, no_2y_t_le_m2
```
`units` columns (every Lane 3/4/5A function): `cell_id, family, inst, tf, side, day, decision_ns, net_r,
twin_mean_net_r, excess_r, cost_r, year`.

### 4.4 L1 probe (Lane 3, `goldbot/research/eval2/probe.py`)
```python
@dataclass(frozen=True, slots=True)
class ProbeResult:
    family: str; cell_id: str; n: int; excess_atr: float; se_atr: float; ub90_atr: float

def first_passage(b: Bars, *, fill_bars: np.ndarray, sides: np.ndarray, horizon_bars: int,
                  barrier_atr: float = 1.0) -> np.ndarray: ...   # +1 / -1 per instance; side-0 OCO bar = -1
def probe_cell(signal: np.ndarray, twins: np.ndarray, day: np.ndarray, *, family: str, cell_id: str) -> ProbeResult: ...
def family_no_signal(probes: Sequence[ProbeResult], *, cheapest_cost_atr: float,
                     power_min: float = 0.8) -> bool: ...
```

### 4.5 EB hierarchical model (Lane 3, `goldbot/research/eval2/hier.py`)
```python
@dataclass(frozen=True, slots=True)
class HierSpec:
    terms: tuple[str, ...] = ("family", "tf", "inst", "cell")
    method: Literal["REML"] = "REML"
    t_df: float = 5.0                    # Student-t on the random effects u_c only; fixed, not estimated (quant 2026-11-09)
    t_on: Literal["random_effects"] = "random_effects"   # the likelihood of day-clustered cell means is Gaussian
    carry_p: float = 0.80
    carry_cost_mult: float = 1.5
    kill_net_r: float = 0.05
    kill_p: float = 0.05
    max_carry: int = 3
    prior_population_hash: str = ""
    code_hash: str = ""

@dataclass(frozen=True, slots=True)
class CellPosterior:
    cell_id: str; mean: float; sd: float; p_pos: float; p_net_pos_gate: float; p_net_gt_kill: float; n_days: int

@dataclass(frozen=True, slots=True)
class L2Decision:
    family: str; carried: tuple[str, ...]; killed: tuple[str, ...]; held: tuple[str, ...]

def freeze_spec(spec: HierSpec, card_path: Path) -> str: ...          # writes into the card; raises if already frozen
def efron_null(control_z: np.ndarray) -> tuple[float, float]: ...     # empirical null (mean, sd)
def fit_prior(cells: pd.DataFrame, *, leave_out_family: str, spec: HierSpec) -> Mapping[str, float]: ...
def posteriors(cells: pd.DataFrame, prior: Mapping[str, float], *, spec: HierSpec,
               deciding_cost_r: Mapping[str, float]) -> list[CellPosterior]: ...
def l2_decide(posts: Sequence[CellPosterior], *, family: str, spec: HierSpec) -> L2Decision: ...
```

### 4.6 Confirm: bootstrap, BatchPRDS, Holm, L3 (Lane 3, `goldbot/research/eval2/confirm.py`)
```python
Q_CONFIRM: Final = 0.10
GAMMA_FIRST: Final = 0.5

def gamma(j: int) -> float: ...           # section 3.2; j >= 1
def confirm_resamples(m_j: int, alpha_j: float) -> int: ...  # max(10_000, ceil(20 * m_j / alpha_j))
def stationary_block_bootstrap(units: pd.DataFrame, stat: Callable[[pd.DataFrame], float], *, mean_block: float,
                               resamples: int, seed_key: str) -> np.ndarray: ...
    # resamples = max(10_000, ceil(20 * m_j / alpha_j)) for every confirm-batch bootstrap (all three functions)
    # day blocks, instances resampled with their twins; returns the resampled stat
def boot_p_one_sided(units: pd.DataFrame, stat: Callable[[pd.DataFrame], float], *, mean_block: float,
                     resamples: int, seed_key: str) -> float: ...
def boot_lower_bound(units: pd.DataFrame, stat: Callable[[pd.DataFrame], float], *, level: float, mean_block: float,
                     resamples: int, seed_key: str) -> float: ...

@dataclass(frozen=True, slots=True)
class BatchPRDS:
    window: Literal["test_old", "confirm"]
    q: float = Q_CONFIRM
    def level(self, j: int, n_j: int) -> float: ...  # q * gamma(j)
    def test(self, p: Sequence[float], *, j: int) -> np.ndarray: ...

def holm(p: Sequence[float], alpha: float) -> np.ndarray: ...

@dataclass(frozen=True, slots=True)
class L3Result:
    family: str; window: str; batch_j: int; alpha_j: float; p_cv_net: float; p_excess: float
    lb80_net_gate: float; years: Mapping[str, bool]; corroboration_p: float | None; e9: bool | None
    rejected: bool; cells_forwarded: tuple[str, ...]

def l3_read(units: pd.DataFrame, *, family: str, carried: Sequence[str], weights: Mapping[str, float],
            batch: BatchPRDS, j: int, batch_p: Sequence[float],
            corroboration: pd.DataFrame | None, mean_block: float, seed_key: str) -> L3Result: ...
```
**Lane 3 condition (quant signature 2026-11-09).** The frozen helper `confirm_resamples(m_j, alpha_j)` is called by
`l3_read` itself, with m_j = len(batch_p) and alpha_j = batch.level(j, m_j); the resamples are never taken from the
caller (`l3_read` has no `resamples` argument). Test: `tests/test_eval2_confirm.py::test_l3_read_resamples_follow_alpha`
asserts the resamples `l3_read` uses at j = 15, m = 3 are >= 386,889 = ceil(60 / alpha_15) with the exact
alpha_15 = 0.1 x gamma_15 = 0.000155083 (re-derived; the 387,097 in the condition as relayed is ceil(60 / 0.000155),
alpha_15 rounded to 3 significant figures, which an exact implementation does not reach, so the test uses the exact
value; quant confirmed 386,889 on 2026-11-09). The test computes the bound from `gamma(15)` (ceil(20 * 3 / (0.1 * gamma(15)))) and never hard-codes the number.

### 4.7 Futility (Lane 3, `goldbot/research/screen.py`; settings in `goldbot/config.py`)
```python
@dataclass(frozen=True, slots=True)
class FutilityResult:
    verdict: Literal["retire", "inconclusive (event floor)", "insufficient"]
    n: int; ub90_r: float; se_r: float; statistic: str

def futility_retire(*, n: int, mean_r: float, se_r: float, statistic: Literal["gross", "delta"],
                    preregistered: bool, floor: int = 300, ub_r: float = 0.03,
                    max_se_r: float = 0.055) -> FutilityResult: ...
```
`ResearchSettings`: `screen_retire_floor: int = Field(300, ge=1)`, `screen_retire_ub_r: float = 0.03`,
`screen_retire_max_se_r: float = 0.055`; `screen_min_events_daily` default stays `None` and `settings.yaml` sets
it `null` (ADR 0077). `screen_verdict` keeps its signature and gains the futility branch for 300 <= n < pass floor.
`preregistered=False` can never return "retire".

### 4.8 Verdicts (Lane 3, `goldbot/research/eval2/verdict.py`)
```python
class Verdict(StrEnum):
    NOT_TESTED = "NOT TESTED"; INSUFFICIENT = "INSUFFICIENT (power)"; NO_SIGNAL = "NO SIGNAL"
    COST_BOUND = "COST-BOUND"; EXIT_DRAG = "EXIT-DRAG"; TRADABLE = "TRADABLE ON HISTORY"
    CONFIRM_SEEN = "CONFIRM-SEEN"; CONFIRMED = "CONFIRMED"; ON_TRACK = "ON TRACK"; DRIFTING = "DRIFTING"
    KILLED = "KILLED"; LIVE_READY = "LIVE-READY"

class HoldoutStatus(StrEnum):
    SEEN = "HOLDOUT-SEEN"; FRESH = "HOLDOUT-FRESH"

@dataclass(frozen=True, slots=True)
class FamilyVerdict:
    family: str; verdict: Verdict; holdout: HoldoutStatus; confirm_seen_cells: tuple[str, ...]
    net_gbp_day: float; net_gbp_day_lo80: float; net_gbp_day_hi80: float; reason: str

def family_verdict(*, family: str, probe_no_signal: bool | None, l2: L2Decision | None, l3: L3Result | None,
                   l4: "ForwardState | None", holdout: HoldoutStatus, confirm_seen_cells: Sequence[str],
                   gbp_per_r: float, signals_per_day: float) -> FamilyVerdict: ...
```

### 4.9 Forward e-processes (Lane 5A, `goldbot/research/eval2/forward.py`)
```python
@dataclass(frozen=True, slots=True)
class ForwardState:
    family: str; n_signals: int; n_fills: int; e_exec: float; exit_mix_ok: bool; e_futility: float; e_edge: float
    status: Literal["ON TRACK", "DRIFTING", "KILLED", "WAITING"]

def e_execution(fill_minus_replay_r: np.ndarray, *, tol_r: float = 0.02) -> float: ...
def exit_mix_ok(counts: Mapping[str, int], prior_alpha: Mapping[str, float]) -> bool: ...
def e_futility(excess: np.ndarray, *, h1_r: float, min_signals: int = 100) -> float: ...   # kill at >= 10
def e_edge(excess: np.ndarray) -> float: ...                                                 # report; 20 = proven
def forward_state(units: pd.DataFrame, *, family: str, l3_shrunk_r: float, s5_passed: bool) -> ForwardState: ...
```
`ForwardState` reads count only when `s5_passed` is true for the broker and TF band.

### 4.10 Self-tests S0-S6 (Lane 4, `scripts/eval2_selftest.py`; tests in section (a) of the method)
```python
@dataclass(frozen=True, slots=True)
class SelfTestResult:
    test: Literal["S0", "S1", "S2", "S3", "S4", "S5", "S6"]; passed: bool; metrics: Mapping[str, float]
    threshold: str; harness_hash: str

def s0_harness(*, harness_hash: str, seed_key: str, fast: bool = False) -> SelfTestResult: ...
def s1_null_population(*, reps: int = 500, workers: int = 8, seed_key: str) -> SelfTestResult: ...
def s2_label_shift(*, plants_r: Sequence[float] = (0.05, 0.10, 0.20), reps: int, seed_key: str,
                   fast: bool = False) -> SelfTestResult: ...
def s3_full_path(*, reps: int, seed_key: str, fast: bool = False) -> SelfTestResult: ...
def s4_leakage(*, seed_key: str) -> SelfTestResult: ...
def s5_execution(*, broker: str, tf_band: str, replay_version: str = "r2") -> SelfTestResult: ...
def s6_truncation(*, samples: int, seed_key: str, fast: bool = False) -> SelfTestResult: ...
def main(argv: Sequence[str] | None = None) -> int: ...   # --only S0..S6, --fast, --workers <= 8; exit 1 on any fail
```
Pytest marker `selftest` (fast set S0, S2, S3, S6) and CI job `selftest` (SRE co-owns).

## 5. Replay v2 spec (Lane 5A) and its pre-registered S5 expectation
- `replay_version = "r2"` in a side table keyed (candidate_id, replay_version); `label_sim_r` is never overwritten.
- Per broker, per leg (entry, stop exit, target exit, time exit), by session: charge max(0, adverse upper 95 % bound
  of the measured slippage mean) once the cell has >= min_fills, else the $0.15 prior. Favourable slippage is never
  credited. Side-signed: + = fill better than the level.
- Stop exit price = level - side x measured stop overshoot (its own term, not double-charged with the stop leg).
- 1m/5m deadline = decision + delay + max_hold.
- Void every non-gold v1 label (the 241 paired rows and the rest); every reader filters by instrument.
- The exploration ledger fills feed `goldbot/execution/costs.py` `slippage_table` per broker and leg.
- **Pre-registered expectation:** icm fill-vs-replay bias falls from +0.090 R (v1) to about +0.02 R on v2 (the
  uncredited target give-back, +0.017 R, remains). Vantage cannot pass S5 until its exit deal price is recorded.
  S5 passes at |bias| <= 0.02 R per broker and TF band.

## 6. Integrity record

### 6.1 Confirm-read ledger (2018-01..2025-09; TEST-OLD 2006-02..2009 never read)
Source: `~/tmp/confirm_reads_ledger_1109.md` (quant-reviewer-staff, read-only, 2026-11-09).

| Batch j | Id | Date | m | What |
|---|---|---|---|---|
| 1 | P1 | 10-06..10-09 | 20 | g0 walk-forwards (session_open, trend, mean_rev, 8-bar compression break, tsmom 4h, ...); span **through 2026-10 (holdout)** |
| 2 | P2 | 10-18 | 66 | rule-only screens, 15m-1d; span **through 2025-12 (holdout)** |
| 3 | P3 | 10-18 | 1 | H-40 box-course feature selection, 15m |
| 4 | P4 | 10-19 | 1 | H-48 sweep of PDH/PDL fade, 15m, fail |
| 5 | P5 | 10-19 | 1 | R-13 EQH/EQL magnet diagnostic, 15m (basis of C02 15m CONFIRM-SEEN) |
| 6 | P6 | 10-19 | 1 | H-50 box break + close + MFI, 15m; span **through 2025-12 (holdout)** |
| 7 | P7 | 10-25 | 22 | H-60 arms matrix (0-trial look), 1m-1d |
| 8 | P8 | 10-23..10-26 | ~140+ | time-structure slices; A **through 2026-10-23 (holdout)** |
| 9 | P9 | 10-30 | 1 | H-80 R2 box runner, 15m-4h, FAIL n 5,355 |
| 10 | F1 | 11-01 | 1 | H-89 post-FOMC continuation, FAIL |
| 11 | F2 | 11-02 | 1 | sprint-200 TREND-03, FAIL |
| 12 | F3 | 11-02 | 1 | MLNET V3 HGB-TOP3 (pool of 11 rules), FAIL |
| 13 | F4 | 11-02 | 24 | C1 re-score, 0/24 |
| 14 | F5 | 11-02 15:23 | 328 | sprint-3000: 480 specs scored, 0 passed |
| — | F6 | 11-06 | 0 | POS1 trend-xasset, confirm set empty, not read |
| — | F7 | 11-07 16:02 | 0 | catalogue G1 prebuild: price caches built, never scored, deleted |

Formal confirm looks F1-F5: 355. `state/catalogue_reads.jsonl` has 1 line (explore, 11-07 09:38) and 0 confirm reads.
Order is by `true_read_ts`.

### 6.2 Holdout touches (2025-10-01..2026-09-30; holdout NOT pristine, status per family, method section 1b)
- P1 (reg:1-20) and P8-A read holdout months through 2026-10; P2 (reg:21-120) and P6 (reg:122) through 2025-12.
- The ATR pin `atr-pin-2026-11-09.1` loaded 2026 (`~/tmp/atr_pin.py` line 24, `load([2026])`): Wilder warm-up ran
  through 2026-01..09, volatility only, no returns, nothing scored. Lane 2 re-seeds it from 2026-10-01 as `.2`.
- Guard: `tests/test_holdout_seal.py` (Lane 4) makes any research loader asking for bars in the holdout outside
  `--score-holdout` raise.

### 6.3 Deleted confirm cache (F7)
`~/tmp/catalogue_cache/confirm/` held 25 `<INST>_1d.npz` price-bar caches (prices only, no returns), written
2026-11-07 16:02 by the G1 daily-closes check, never scored. The evaluator recorded each sha256 in
`~/tmp/confirm_cache_1107_sha256.txt` and deleted the files on 2026-11-09 (directory confirmed absent today). Hashes:

```
e85e591ae79db48b2c1efa3ec50da92ae1f239e12c971b950fd6b1d145e512ba  AUDJPY_1d.npz
2ebd0c733338b3bb48b8b3e0e5500caf4b0082fd84296e348cc11e9b4ad18575  AUDUSD_1d.npz
e29f11af522a126ee19b3ca2182f73828eb94073dc13e766e448d10149fb84b1  BTCUSD_1d.npz
47d267abb8ca4f9210ce8cfea0c52f797faf45e5d9587f3c44dc361e320f3ab5  DE40_1d.npz
81e0b87e20745cd9d6a9e65c7933a9ccb5cbf431d8f941406a2588e4fd26dc7d  EURAUD_1d.npz
b0bbe5a0cce822764b3d4d128cbf74a3188779c7c711940636bf0534da2798c1  EURGBP_1d.npz
ea2e396471a7a97684b6668fead6190c8f740196ac2157b89ba41e4e5251b24f  EURJPY_1d.npz
4c136ea862fe54985fbbef620eb008942d1829a3f11f48e1a4a7a756e4a02941  EURUSD_1d.npz
5e90815648489b1ed0cd30bd12e70912f72934c471b30e993649aad9559feafa  GBPAUD_1d.npz
7f35eb3378fa6387447db2107da981a3d86ee728a59eeacb756df3fc36f3a109  GBPJPY_1d.npz
eb666d83dc29dd3029ce2d0ffc258771438b01f96bfe6cb8298161e0e72b0605  GBPUSD_1d.npz
5d70d3de08a69c0effab7c9e3d5ddce52a193c7cae18d97263972eeada24b6b2  NZDUSD_1d.npz
2de625af3d83020642896cd891ef75aba1392c4a1d98db975893d53c53b7af6b  UK100_1d.npz
c57b46a85933571a82b6c7a26725bbefc5ece081a1805c35a6f925a28ced3f42  US30_1d.npz
62d5bfbeb2d9b9fe90e42deeddd3069df53c69758cd302a1b6e60c4a60be6256  US500_1d.npz
48d348f2fbd6330cffd7d7796894f399392c96dbcd6e8f8ec2d52c2981dd9ac8  USDCAD_1d.npz
4e5c6159666de6329fc35689d2737103a930eec3665d182c52242728868b3960  USDCHF_1d.npz
1ec16d68959d0e7ba7f9e02a6c81b979830c4b10e91b4a93a4f23485d88d2fbb  USDJPY_1d.npz
b4f50909ef50e58c658d066f09e142aa8218db09d0dbef73e733b6bf2c13c3ef  USINDEX_1d.npz
357a9fba0d9806265cfeb7cb4ade58bd47bfb70726cd2bf63681ac4ec1f0fbb2  USTEC_1d.npz
0b5b154c4dc0781e628416b7adc0580ff64339037bb034a327abcad6614e11e5  XAGUSD_1d.npz
17584f58f264fbc49eeaa4579b906e4341d6c0755d5f2c1009ef869014ec1d0b  XAUEUR_1d.npz
831784d05ee2a698de5ac0d1813be8cfcb16c2e8e4f7daacf252e21cec0c9120  XAUJPY_1d.npz
abde9951f710dfa162e8c499cb800f370d9ad0910d098ff33479f1f23381358f  XAUUSD_1d.npz
d503a237f3966288e5929dd89e26ec36f489f38348710b999a8fb5200dfaccc8  XTIUSD_1d.npz
```

### 6.4 Registry merge and backfill
- Merge (2026-11-09): brain is the registry of record; union by (ts, config_hash); 196 Mac-only rows pushed, 102
  brain-only kept; 3,455 rows, prefix identical to the backup (brain sha 13b64c7d...). The Mac is a pulled read-only
  mirror.
- Backfill (2026-11-09, applied, quant-verified): result rows for every scored sprint-3000 spec, linked by
  (ts, config_hash), unscored specs listed apart and withdrawn with evidence; registry 6,961 rows, sha 73fc51e1...;
  `n_trials_effective` 3,471; quarter_trials(2026Q4) 3,395; headroom 105. Backfilled rows carry ts = 2026-11-09 and
  the true read time in `results.backfill.true_read_ts`.
- Guard (Lane 0): `tests/test_registry_unlinked_prereg.py`; registrations frozen until it is green on CI and merged.
- Standard: every runner that reads a window writes its result row in the same run.

### 6.5 Voided labels
241 non-gold paired rows in `exploration_obs` carry a `label_sim_r` replayed on gold bars with gold costs (written
before the 2026-11-04 instrument guard). They and every other non-gold v1 label are void (Lane 5A); no read that
pooled them is valid.

## 7. Amendment: the unbuilt mSPRT is superseded
`prereg-amendment-2026-11-07-kill-only.md` section 5 pre-registered a sequential mSPRT kill (delta 0.15, 20-fill
floor). It was never built (`goldbot/` has no mSPRT, re-derived today). It is **superseded**, not built:
- Arms registered after ADR 0076: the L4 e-processes (section 4.9) are the kill rule.
- Existing arms: the live rule stays `arm_stops.unit_stop` on `config/arm_stops.yaml` (H-61 running net R by style:
  -30 R within 300 scalp / -15 R within 100 intraday / -10 R within 40 swing; W1 per-family -30 R within 300; W2/Z
  futility at 300, one-sided 95 % UB < +0.03 R; Z cost stop), applied daily by `daily_review._arm_health`. An
  existing arm moves to L4 only as a new registry row (K +1).
- Kill-only: nothing here lets any arm continue, scale or graduate. Shadow signals count toward n.

## 8. Signatures
| Role | Name | Date | Signature |
|---|---|---|---|
| Author | research director | 2026-11-09 | signed |
| Quant-reviewer | quant-reviewer | 2026-11-09 | signed (014d4c93; resamples helper condition for Lane 3) |

<!-- Numbered section 10 because Lane 4's amendment A1 (S0 cell floor) takes section 9. Text as signed in the quant-reviewer's Lane 3 review (2026-11-09); only the section numbers 9 -> 10 changed in the body; the signature line is verbatim, so its "9.2" and "9.3" are 10.2 and 10.3 here. -->

## 10. Amendment 2026-11-09 (quant-reviewer, Lane 3 review of e196e663): CONFIRM-SEEN in the L3 read, E9, verdict inputs

Written before any L2 fit on real cells and before any TEST-OLD or EVAL-2 2018-25 return is read. Changes the frozen
signatures of sections 4.6 and 4.8 as stated; nothing else changes.

**10.1 CONFIRM-SEEN and the 2018-25 corroboration (A15).** `l3_read` gains a required keyword-only argument
`confirm_seen: Collection[str]` (no default): the card's CONFIRM-SEEN cells for 2018-01..2025-09, fixed before L2.
`L3Result` gains a last field `corroboration_seen_p: float | None`. With F = carried minus confirm_seen and S = carried
intersected with confirm_seen:
(a) The TEST-OLD legs (control-variate p and BatchPRDS, p of mean excess, LB80 at 1.5x, Holm) read all carried cells.
(b) `corroboration_p` is the one-sided p of the control-variate net over F's 2018-25 units, with the card weights
renormalised over F. It is the corroboration AND (p <= 0.10) and is None when F is empty.
(c) When S is non-empty, `corroboration_seen_p` is the same p over S's 2018-25 units (weights renormalised over S). It
must be <= 0.10: an AND with no evidential credit.
(d) The family rejects only if (a) passes, (b) passes when F is non-empty, (c) passes when S is non-empty, and the
year rule and E9 (10.2) pass. A family with F empty is decided by (a), (c), the year rule and E9. Its best verdict is
CONFIRM-SEEN, which also needs the forward holdout and L4, whatever its holdout status.
(e) The year rule reads TEST-OLD plus the 2018-25 units of every carried cell (F and S), as a necessary condition with
no credit. The 2-year sub-periods are the consecutive pairs (y, y+1) in which both years hold data.
(f) If F is non-empty and `corroboration` holds no F units, or S is non-empty and it holds no S units, the family is
not rejected.
(g) With `batch.window == "confirm"` (the 2018-25 BatchPRDS p reported at batch 15), `carried` must not contain a
CONFIRM-SEEN cell (ValueError). That read is reported and never decides.

**10.2 E9 in the L3 read (A16).** `l3_read` gains required keyword-only arguments `holds_multi_day: bool` and
`e9: bool | None`.
- `holds_multi_day` comes from the frozen card. It is true if any carried cell's registered maximum hold (time exit
  x bar length) is >= 24 h, or its tf is 1d or 1w, or it has no registered time exit (an unbounded hold counts as
  >= 24 h; RD countersignature condition 1). It is never derived from realised holds. `l3_read` raises if a 1d or 1w
  carried cell is present and `holds_multi_day` is false.
- `e9` is the caller's EVAL-1 C1 drift guard (`stats.drift_guard`, `stats._e9_pass`; Newey-West alpha t >= 1.0, alpha
  >= 50 % of mean daily net, beats the exposure-scaled financed buy-and-hold book in >= 5 of 8 years; against every
  side traded). It is computed on the 2018-01..2025-09 daily closes over every carried cell (F and S): a necessary
  condition with no credit, on the 8-year frame where C1 is defined. The TEST-OLD beta rule of `stats.test_old_gates`
  ("beta > 0.3 must be net >= 0") is applied on TEST-OLD too.
- If `holds_multi_day` is true and `e9` is not True, the family is not rejected. `L3Result.e9` records the value
  passed.

**10.3 Verdict inputs (A17).** `family_verdict` gains required keyword-only arguments `l0_power_ok: bool | None`,
`power_l3: float | None`, `posts: Sequence[CellPosterior] | None`, `probes: Sequence[ProbeResult] | None` and
`cheapest_cost_atr: float | None`. `L3Result` gains `cv_net: float`, `cv_net_lo80: float` and `cv_net_hi80: float`:
the control-variate net at 1.0x and its two-sided 80 % basic-bootstrap interval (the 10th and 90th percentiles of the
same resamples, seed tag `|ci80`). The rungs read in this order, with KILLED and DRIFTING from L4 as before:
1. NO SIGNAL if the L1 retire holds.
2. INSUFFICIENT (power) if `l0_power_ok` is False; or if L2 carried a cell, `l3` is None and `power_l3` < 0.3. If `l3`
   is present while `power_l3` < 0.3, raise ValueError (read condition breached).
3. When L2 carried nothing:
   - INSUFFICIENT (power) if no cell has P(theta > 0) >= carry_p and the pooled probe one-sided 90 % lower bound is
     <= 0 (no information).
   - Otherwise EXIT-DRAG if the pooled probe excess (point, the same pooling as the L1 retire) is >= `cheapest_cost_atr`
     (exit-agnostic information above cost that the exits lose).
   - Otherwise COST-BOUND.
4. TRADABLE ON HISTORY on L2 carry with no L3, or on an L3 that rejected with no forwarded cell (the reason says
   "family rejected at alpha_j, no cell survives Holm"). An L3 that did not reject reads **NOT CONFIRMED** (new
   `Verdict.NOT_CONFIRMED = "NOT CONFIRMED"`; RD countersignature condition 2): closed, never re-read on TEST-OLD or
   2018-25, never on the live-candidate list. "Never listed" means it is not offered as an action. It stays in the
   registry, K, the snooping ledger and the weekly rung counts. TEST-OLD is read once per family, so the
   TRADABLE-after-rejection case above is never re-read either.
5. CONFIRM-SEEN when the family rejected and every forwarded cell is CONFIRM-SEEN (including a family with F empty).
   Otherwise CONFIRMED; the reason lists any forwarded CONFIRM-SEEN cells, which each still need the forward holdout.
6. LIVE-READY is never set here (L5, owner's record).

£/day at demo size = `gbp_per_r` x `signals_per_day` x net R per signal:
- With L3: `cv_net`, `cv_net_lo80`, `cv_net_hi80` (out-of-sample, TEST-OLD, 1.0x deciding cost).
- With L2 only: the card-weighted posterior net mean (theta + twin net) of the carried cells, +/- 1.2816 x its net sd,
  labelled "explore, shrunk, in-sample" in the reason.
- Otherwise NaN.
`lb80_net_gate` stays the gate value and is never shown as the £ interval.

Erratum 2026-11-10: with L2 only, the £/day point and interval are NaN until `CellPosterior` carries the cell's twin
net. The reason labels the rung 'explore, shrunk, in-sample'. Display only; no gate reads it. (Signed: quant-reviewer
2026-11-10.)

**Tests that pin 10.1-10.3** (Lane 3 follow-up):
- `test_eval2_confirm.py`: `::test_all_seen_family_rejects_on_test_old_with_the_seen_and`,
  `::test_a_seen_cell_failing_2018_25_blocks_the_family`,
  `::test_fresh_corroboration_p_ignores_seen_cells` (perturb S's 2018-25 units; `corroboration_p` unchanged),
  `::test_confirm_window_refuses_seen_cells`, `::test_confirm_seen_has_no_default` (TypeError),
  `::test_multi_day_hold_needs_e9_true` (4h cell with `holds_multi_day=True`, `e9=None` -> not rejected;
  `e9=True` -> rejected), `::test_daily_cell_with_holds_multi_day_false_raises`,
  `::test_no_time_exit_counts_as_multi_day`.
- `test_eval2_verdict.py` (added): `::test_l3_not_rejected_reads_not_confirmed` (also asserts
  `Verdict.NOT_CONFIRMED.value == "NOT CONFIRMED"`), `::test_a_rejected_family_with_no_forwarded_cell_is_tradable_with_reason`.
- `test_eval2_verdict.py`: `::test_no_information_reads_insufficient_not_cost_bound`,
  `::test_probe_above_cost_with_no_carry_reads_exit_drag`, `::test_probe_below_cost_with_information_reads_cost_bound`,
  `::test_low_power_reads_insufficient_and_a_read_under_it_raises`,
  `::test_gbp_view_uses_the_l3_point_and_80_interval_at_1x`, `::test_l2_only_gbp_view_is_labelled_in_sample`.

Signatures: author quant-reviewer, 2026-11-09; SIGNED, research-director, 2026-11-09: these close signature gaps before any L2/L3 read and change no threshold, K, window or budget; B3's 150 is the registered floor; signed on two tightenings that go into the inserted text: (1) in 9.2 a cell with no registered time exit counts as holding >= 24 h; (2) in 9.3 rung 4 an L3 that did not reject is not TRADABLE ON HISTORY but a closed verdict (new enum NOT CONFIRMED, never re-read, never on the live-candidate list).
