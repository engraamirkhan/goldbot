<!-- Copied verbatim from the signed v6 method (quant-reviewer, 2026-11-09). Scanned 2026-11-09: no secrets or account ids. Paths under ~/tmp are local working files and are not published. Figures superseded by the quant-verified update of 2026-11-09 (DSR K 21,333, sensitivity 20,151, n_trials_effective 3,471, quarter_trials 3,395, headroom 105, registry 6,961 rows; reads ordered by results.backfill.true_read_ts): see docs/research/prereg-2026-11-09-eval2.md section 2. -->

# EVAL-2: how goldbot judges a strategy (method v6, 2026-11-09; v7 2026-11-09, signed with conditions C1-C10)

**v7 (2026-11-09, signed with conditions C1-C10 by the quant-reviewer):** the quant's vantage exit-price ruling
replaces "vantage S5 blocked until exit deal price recorded" in sections 5 and 6, in the quant's exact words. Change
record, the Lane 5A choices and the conditions as built: section (e) at the end.

Chair: research director. **v3** applies the quant's addendum (holdout partly spent, per-window batch numbering,
C02 15m CONFIRM-SEEN), the PD's K ruling (`~/tmp/pd_k_ruling_1109.md`) and the PD + PO volume ruling
(`~/tmp/pd_volume_ruling_1109.md`) to v2 (`~/tmp/eval2_method_1109_v2.md`), then the quant's signing conditions R1-R5 and the TEST-OLD data-quality
result (`~/tmp/testold_dq_1109.md`). v2 applied the quant-reviewer's CHANGES ruling (findings 1-13, Ruling A, batch 1), the
CTO's build-plan changes and the S5 decomposition (`~/tmp/s5_decomp_1109.md`) to v1 (`~/tmp/eval2_method_1109_v1.md`).
Change logs: `~/tmp/eval2_method_1109_v2_changelog.md` (v1 -> v2) and `~/tmp/eval2_method_1109_v3_changelog.md` (v2 -> v3). Sources: 8 proposals (`~/tmp/evalnew/`), Evaluation Audit 2,
the futility ruling, the EVAL-1 reading rule and the ATR/cost pin `atr-pin-2026-11-09.1`. Status: **v6** applies the quant's exact v6 text (DSR K sentence, quarter_trials 278 + 3,117 after the verified backfill, withdrawal exemption,
two consistency fixes) to v5 (`~/tmp/eval2_method_1109_v5.md`; diff `~/tmp/eval2_v5_v6.diff`). v5 applied the quant's overlap rulings of 2026-11-09 to v4 (`~/tmp/eval2_method_1109_v4.md`); diff
`~/tmp/eval2_v4_v5.diff`; change log `~/tmp/eval2_method_1109_v5_changelog.md`. v4 applied the TEST-OLD intraday
window, sensitivity 16,680, DSR K, registry headroom and integrity rulings to v3. Method only.
Nothing is built, committed or run. It needs the quant's signature and an ADR before any card is registered against
it. Rule 1 money work (vantage SUGAR stops, the owner's method gap) still comes first. EVAL-2 does not take their slots.

**Integrity record.** `~/tmp/catalogue_cache/confirm/` (25 files, `<INST>_1d.npz`, 2026-11-07 16:02) held the 1d
price-bar caches built by the G1 prebuild check. It held no unit returns and was never scored. The evaluator recorded
the sha256 of each file in `~/tmp/confirm_cache_1107_sha256.txt` and deleted the files on 11-09.
`state/catalogue_reads.jsonl` has 1 line (explore, 11-07 09:38, head 7f075e36) and 0 confirm reads.
P1 and P8-A read holdout months through 2026-10, and P2 and P6 through 2025-12, before the windows were frozen. The
ATR pin's Wilder warm-up ran through 2026-01..09 (volatility only, no returns). All are recorded in the read ledger
as holdout touches. (Ledger ids P1-P9, F1-F7: `~/tmp/confirm_reads_ledger_1109.md`.)
Sprint-3000 read explore and confirm without writing result rows. A backfill (data-engineer, dry run first) and a
guard (quarter_trials/n_trials_effective count stale unlinked ad-hoc preregs) are in progress. A new standard says any
runner that reads a window writes its result row in the same run. The registry merge is applied and verified (brain
sha 13b64c7d..., prefix identical to the backup, 3,455 rows).

**Correction to v1.** v1 said "CONFIRM 2018-01..2025-09 ... unread". That was wrong. CONFIRM 2018-01..2025-09 is
unread by the catalogue ledger, but was read program-wide on 2026-11-02 (sprint-3000, m 328, gold 15m-1d, including
nr_breakout n 4/7 on 4h) and by every other confirm read in `state/*_reads.jsonl` and the registry. EVAL-2 confirm
batches are numbered after all of them (section 3: EVAL-2's first 2018-25 read is batch 15). `~/tmp/confirm_reads_ledger_1109.md` (2026-11-09): formal reads F1-F5 (355 looks), pre-freeze reads P1-P9; TEST-OLD
2006-02..2009 never read. Re-derived today from `state/sprint3000_reads.jsonl`
(1 line, window confirm, 2026-11-02 15:23 UTC) and `~/tmp/s3k/confirm.json`: m 328, 480 specs evaluated, 0 passed;
timeframes 1h 251, 4h 207, 15m 12, 1d 10; data 2016-01-03..2025-09-30; 24 specs are `nr_breakout` 4h (n 4 and n 7),
39 are `zscore` (1d 3, 4h 36), 3 are `inside_bar_break` 4h. The holdout is NOT pristine. P1 (reg:1-20) and P8-A read holdout months through 2026-10, and P2 (reg:21-120) and P6
(reg:122) through 2025-12. The ATR pin warm-up touched 2026-01..09 (volatility only). Holdout status is per family (§1b).

**Second integrity item (S5 decomposition).** 241 non-gold paired rows in `exploration_obs` carry a `label_sim_r`
replayed on GOLD bars with gold costs (written before the 2026-11-04 instrument guard, `exploration_data.py:438-455`).
Unfiltered, the paired fill-vs-replay mean is +37.2 R. Those labels are voided (Lane 5A) and every reader filters by
instrument. No read that pooled them is valid.

**Corrections to Audit 2.** The evaluator re-derived the audit: it reproduces, and its status is EXPLORATORY. None of
the corrections below changes its conclusion.
1. **Noise reference.** The reference for cell t ≥ 2 is the null units' rate, 1/76 = 1.3 %, or about 10 of 749, not
   17. So 15 against about 10 is not significant. The cells also overlap: F09-V0 and F09-V3 are identical, and S07 and
   S17 overlap. EVAL-2's empirical null uses the controls, not a nominal 2.3 %.
2. **Gold-only count.** Gold-only is 0/52 real, not 0/55: the 55 included 3 NULL-RAND variants.
3. **B04 shift.** It comes from the whole diff (D1-D5, C7-C10 and the costs), not from D5 alone.
4. **15m risk and cost.** The 15m median risk is $1.76, not $2.01. Today's 15m round trip is 0.033 R, now measured
   (ATR15 $7.00, from the pin), not relayed.
5. **Explore cost reading.** EVAL-2 uses section 4; the quant has ruled (finding 9).

## 1. Unit of evidence

- **Instance.** One setup instance: a decision made at its defining bar's close, taken or not. Its label is
  **excess R** = net R at the deciding cost (section 4) minus the mean net R of **M = 20 replay-only random twins**.
- **Twins.** Each twin uses the same instrument, timeframe and side, and the same hour ±1 h. It also uses the same
  regime level when the card pre-registers one. Twins are drawn from decision bars after the signal's exit (intraday:
  the 7 days after exit; 1d: the 20 bars after exit), excluding bars where any instance of the same family fires.
  Stop, target and hold are the signal's multiples of ATR at the **twin's** decision bar; the exit logic is the
  signal's. Forward excess is signal **replay** minus twin replay. Fill vs replay is read only in L4(i). The 1.025x SE
  claim is replaced by the measured twin correlation in S0. Twins carry the same cost path as the signal, swap
  included (section 4). An opposite-side twin at the same instant is reported, which separates direction skill from
  timing skill.
- **Cell, family, clustering.** A cell is family x instrument x timeframe x exit. A family is one setup across cells
  (owner: strategy = family). Standard errors are clustered by session day.
- **The daily book** at the registered sizing is kept for drawdown, sizing and the £ view, never for pass or fail.

## 1b. Holdout status (per family, fixed in the card before L2)

- **HOLDOUT-SEEN** if the trigger class matches any rule scored on bars ≥ 2025-10-01 (ledger P1, P2, P6, P8-A), or
  (b) excess overlap e = o − b ≥ 15 pp, where o = the share of the cell's explore instances with a same-side signal of a
  rule **as read** (its own timeframes, the read's own filters or a superset), and b = the chance base rate of that
  rule over the same window (share of all in-window cell bars, per side). The point estimate is used at any n; cells
  with n < 20 are flagged. Overlap with a rule run at a timeframe it was never read at ('same' mode) is reported,
  never counted. Superset rules can only raise o, so they stay conservative. **Window:** a rule signal matches an
  instance if same side and |τ − T| ≤ TF_cell (decision-close instants); for a rule read at several timeframes, the
  union. Fixed as of `~/tmp/overlap_1109/overlap.py` sha256 76b075b0…. **Holdout clause:** only rules scored on bars ≥
  2025-10-01 count (BRK8 for P1/P2; BOX at 15m for P6; session/ORB for P1/P8-A by trigger class). **Rationale (quant
  2026-11-09):** the raw 30 % had no chance base or window; 15 pp is the strict end (30 % minus the largest intraday
  as-read base, 15 %). No cell that raw as-read 30 % marks SEEN becomes FRESH under it.
  Otherwise **HOLDOUT-FRESH**.
- **HOLDOUT-FRESH** families use `--score-holdout` once.
- **HOLDOUT-SEEN** families get a forward holdout instead: shadow replay of the frozen card on bars after the freeze,
  read once at a fixed n = min(12 months, power 0.8 at half the L3 shrunk estimate), using the L3 control-variate net,
  one-sided, α 0.05. Demo fills are used only for L4(i).
- **Guard:** `tests/test_holdout_seal.py`: any research loader that asks for bars in 2025-10-01..2026-09-30 outside
  `--score-holdout` must raise.
- Overlap test done 2026-11-09 (`~/tmp/overlap_test_1109.md`); per-cell statuses in section (d). Status is fixed in
  the card before L2 and never changed after it.

## 2. The ladder, with statistic, threshold and power

sd = the cell's own measured sd of excess, not a pooled 1.3 R. Re-derived from the raw trades: C03-V1 30m has
sd(net) 2.26 R and sd(excess) 1.56 R. S21-V0 1d has 0.57 R. C02-V0 4h has 0.56 R. A pooled 1.3 R understates C03's MDE
by 1.2-1.7x.

| Stage | Data | Statistic and threshold | Power / MDE (80 %) |
|---|---|---|---|
| **L0 harness-sane** | Explore. Once per harness hash, and in CI (`selftest` job) | Self-tests S0-S6 (section 6) all pass | n/a |
| **L0 card admission** | None (ex ante) | Rule-2 card: expected gross ≥ 2x deciding cost (3x for ≤ 15m, the program scalp bar). Median round trip / native stop ≤ 0.15 R, from decision-time inputs only. **Power floor** n_min = ((z(1-q/m) + 0.84) x sd_card / δ_card)² must fit the window's expected n, with m = the families actually read, fixed before the read. This replaces the fixed 1,000/300/150 floors | Refuses unpowerable cards before any tokens are spent |
| **L1 information** (exit-agnostic) | Explore 2010-17 | Probe: symmetric first passage at ±1 ATR (decision-TF ATR), from the fill bar's close, capped at the native horizon. A side-0 OCO bar scores as a loss. Statistic: excess of the probe over 20 twins, partially pooled per family. **Family retire (NO SIGNAL)** if the pooled 90 % UB < the cheapest feasible cell's deciding cost in ATR and power ≥ 0.8 at that cost. Per cell the probe is a diagnostic, not a gate | 0.08 ATR at n 1,000; 0.12 under the empirical null (null t sd 1.0-1.13) |
| **L2 tradable** (carry to confirm) | Explore 2010-17 (spent: selection only, no significance claimed) | EB hierarchical model (REML, Student-t): θ_c = μ_F + a_TF + b_inst + u_c, on day-clustered cell means of excess. The prior is fitted leave-family-out on **every cell ever viewed**, real and NULL-RAND, de-duplicated by config hash. The empirical null comes from the controls (Efron). **Carry** if P(θ_c > 0) ≥ 0.80 and P(net_c > 0 at 1.5x deciding cost) ≥ 0.80. L2 carries at most 3 cells per family, mechanically, by posterior mean. **Cell kill:** P(net > 0.05 R) < 0.05. The 0.80 is raised, never lowered, until the null carry rate is ≤ 5 % (S1). **Frozen before the L2 run on real cells:** the thresholds, the model (terms, REML, Student-t df, prior population and its de-duplication) and the code hash are recorded in the card; no threshold changes after it | Unshrunk: 1.68 sd/√n gives 0.13 R at n 300 and 0.07 R at n 1,000 (sd 1.3); 0.125 R for C03 30m. Shrinkage lowers it |
| **Futility retire** (ruling 11-09, all units incl. 1d) | Any screen at 300-999 events | One-sided 90 % UB of the statistic named in the card before the read (gross, or Δ vs twins) < +0.03 R **and** clustered SE ≤ 0.055 R, both pre-registered and counted in K. Else "inconclusive" | SE ≤ 0.055 needs n ≈ 560 at sd 1.3 and ≈ 800 at sd 1.56. **Ruling A (150, daily, retire on sign) fails this power test** (SE 0.106 at n 150); its withdrawal goes to an ADR (section 8, item 19) |
| **L3 confirm** (read once per family) | **TEST-OLD 2006-02..2009 primary** (own sequence), 2018-25 corroboration (one-sided p ≤ 0.10 on net as an extra AND; its BatchPRDS p reported). Before the freeze, the 2006-09 data-quality check must pass (1m density; sqrt-time ATR scaling within 15 % from 15m to 4h), or intraday cells read NOT TESTED. **DQ result (`~/tmp/testold_dq_1109.md`, bars and ranges only, no outcomes):** 1m density passes every year (90-97 % of a full day, no gap over 4 days); ATR sqrt-time FAILS at 4h in 2006 (0.783) and 2007 (0.784), 2008-2009 pass at all timeframes (1.02-1.04); whole weekdays missing 9 / 20 / 19 / 6 per year (2008 includes Sep-Nov crisis dates); 2006 median 1m range about 12 bp vs 3-4 bp later, so quote noise likely inflates the 2006-07 15m ATR by 20-25 %. **Quant ruling 2026-11-09:** intraday cells (≤ 4h) on TEST-OLD use 2008-01..2009-12 only. 2006-07 is added back only if, after the refill, both the 4h sqrt-time ratio is within 15 % and the median 1m range (bp) is within 1.5x of 2008-09. Both checks run outcome-blind and are recorded in the card before any TEST-OLD return is read. 1d uses 2006-02..2009-12. | **Statistic:** the control-variate net: the family's precision-weighted mean excess plus the mean net of all twins in the carried cells over the window, at 1.0x deciding cost. **p:** one-sided stationary day-block bootstrap (mean block = E1 lag), resampling instances together with their twins. **Gate:** BatchBH (section 3) on that p; AND one-sided p of mean excess ≤ 0.10 (timing, not drift); AND the bootstrap 80 % lower bound of net at 1.5x deciding cost > 0; AND, over 2006-2009 plus 2018-2025: net positive in ≥ 3 calendar years, at least one of 2021 or 2022 (`research/gates.py` CHOP_YEARS), and no 2-year sub-period with net t ≤ -2; AND 2018-25 corroboration as above; AND E9 for holds ≥ 1 day. The weights are fixed from explore (n, sd) in the frozen card. **Cells:** after the family is rejected, cells go forward by Holm over its ≤ 3 carried cells at the family's BatchBH level, on the cell's control-variate net p (closed testing). Other cells are reported, not forwarded. **CONFIRM-SEEN** cells and families (section 3) never enter the 2018-25 hypothesis. They keep the 2018-25 corroboration condition (one-sided p ≤ 0.10 on net) as an AND that gives no evidential credit, and they **also** need the forward holdout of §1b, whatever their holdout status. The family's 2018-25 corroboration p is computed over its CONFIRM-FRESH carried cells only. **Read only if** power ≥ 0.3 on TEST-OLD at the L2 shrunk estimate; otherwise "insufficient (power)" and neither window is read for it | Recomputed before the card freeze on the control-variate statistic, at the BatchPRDS level of the deciding window (TEST-OLD index 1, γ_1 = 0.5; m = families read), from C03 and C02 inputs re-derived from raw explore trades at the frozen harness head. The v1 50 %/98 % figures are void. Quant estimate for C03 30m on TEST-OLD: about 65 % at full and 19 % at half excess. A family below 0.3 is not read. **C03 30m on 2008-09** (n ≈ 109): power is about 36 % full and 11 % half (re-derived). If it is below 0.3 at its shrunk estimate, it reads INSUFFICIENT and routes to L4 learning-class demo plus the forward holdout, never to real money on that route alone. The 0.3 floor is not lowered. On 2018-25 at batch 15 power is about 0 |
| **L4 forward demo** (conformance, not discovery) | Shadow replay of every signal (taken or not) + demo fills. The L4 e-processes apply to arms registered after the EVAL-2 ADR. An existing arm moves only as a new registry row (K +1) | (i) **Execution e-process:** realised cost ≤ modelled + 0.02 R, paired fill vs replay v2 (section 5), per broker and leg. Kill-only; sd ≈ 0.05-0.1 R; one term per session day (v7, C2), so a 0.05 R overrun needs about 300 fills (about 60 session days). (ii) **Exit-mix check:** stop/target/time shares vs the history Dirichlet, ≈ 50 trades. (iii) **Futility e-process:** e ≥ 10 against H1: excess ≥ max(0.05 R, ½ x L3 shrunk estimate), after ≥ 100 signals, gives KILLED. (iv) **Edge e-process** vs H0: excess ≤ 0. Reported. e ≥ 20 = "proven forward" (rare inside a year). ON TRACK = (i)-(iii) green after ≥ 50 fills per family | Forward cannot find 0.1 R in under a year: 300 signals detect a 0.17 R shortfall. Ville bound ≤ 5 % |
| **L5 real money** | — | Eligible: L3 + L4 ON TRACK + holdout (section 1b: `--score-holdout` once if HOLDOUT-FRESH, the forward holdout if HOLDOUT-SEEN) + `research/gates.py` as coded. An exemption for rule-only families or a change to DSR's K basis needs an owner ADR. L5 size above the 95th-percentile-drawdown ≤ 8 % ceiling is refused by RiskGate config validation (a tightening; built at L5 time under its own trading-safety review, not in this build). **Owner decides** go-live and size | — |

DSR K = 3,471 (registry n_trials_effective after the sprint-3000 backfill: 2,629 explore incl. 8 ensembles + 328
confirm + 159 member-only confirm + 1 null-ensemble confirm; 389 never-scored specs withdrawn with evidence) + 17,862
snooping = 21,333; sensitivity 3,471 + 16,680 = 20,151. The t required is recomputed by `goldbot.research.metrics` at
that K. Reported, not gated, before L5. At L5 the owner's K basis applies. The Harvey-Liu haircut and max drawdown at 0.5 % risk are **reported at
every stage up to L4, never gated there**; at L5 `research/gates.py` applies as coded. **Owner decision for later, at L5 time:** the DSR K basis.
The quant recommends K = hypotheses ever tested on 2018-25, about 400, so t ≈ 3.1. The haircut explore Sharpe sets each
card's expected edge (and so its power floor) and the sizing ceiling offered to the owner: the risk at which the
95th-percentile simulated drawdown ≤ 8 %.

## 3. K and multiplicity

- **Explore views.** Explore views of 11-09 (de-duplicated 17,862; 16,680 real-only as a sensitivity) are booked in the
  append-only snooping ledger (PD ruling 2026-11-09), one row per de-dup hash + source + harness. Snooping K enters DSR
  K (= registry `n_trials_effective` + snooping K), the EB prior's n_configs and the haircut. It does not draw on the
  registration budget. Any future descriptive look is appended to the ledger **before** it is computed. A view set that
  shapes a card is cited by its hashes in the card, and that card cannot pass on explore alone (it needs TEST-OLD plus
  2018-25). Registrations are frozen until the reconciliation (merged 2026-11-09: 3,455 rows; quarter_trials after the backfill 278 + 3,117 = 3,395 used; **headroom 105** under the ADR 0029 loop exclusion; backfill verified by the quant-reviewer 2026-11-09 (sha256 73fc51e1…)). The backfill was verified by the quant-reviewer on 2026-11-09; the freeze now lifts only when the Lane 0 test (`tests/test_registry_unlinked_prereg.py`) is green on CI and merged. The quant-reviewer verifies the count and headroom. Batch-1 rows come second in the PD order.
- **PD order after the freeze** (`~/tmp/pd_k_ruling_1109.md`): futility ADR (0 rows); EVAL-2 batch-1 rows; KoG/G card
  (+11); the L2 block within the remainder minus a reserve of 5; everything else 2027Q1. No owner override is needed.
- **Registry integrity (quant ruling 2026-11-09):** (a) result rows are backfilled for every scored sprint-3000 spec,
  linked by (ts, config_hash), with unscored specs listed apart; (b) quarter_trials and n_trials_effective count ad-hoc
  preregistered rows with no linked result after 7 days (`tests/test_registry_unlinked_prereg.py`). A `withdrawn` row linked by (ts, config_hash), with `reason` ∈ {`unevaluable: <cause>`, `never formed: <cause>`} and `evidence` (run output files and sha256 showing no score), written before any read of the spec, exempts it. A withdrawn spec read later needs a new, counted preregistration. Self-declared 'not a trial' text exempts nothing; (c) every research
  runner writes its result row in the run that reads the window. Mac trial numbers 105-210 are cited by (ts,
  config_hash). Anything that orders registry rows by ts (e.g. the BatchPRDS read ledger) must
  use `results.backfill.true_read_ts` for backfilled rows.
- **How K is used.** K enters (a) the EB prior's population, so the selection is shrunk by how rare edges have been;
  (b) the haircut and the DSR reported at real K; (c) `research/gates.py` at L5.
- **Numbering.** One BatchPRDS sequence per window, with γ_j fixed in the ADR. On 2018-25, P1-P9 and then F1-F5 each
  count as batches with 0 rejections credited, so EVAL-2's first 2018-25 read is **batch 15**, where power is about 0.
  The TEST-OLD sequence starts at index 1 (γ_1 = 0.5), which is why TEST-OLD is the L3 primary and 2018-25 corroborates.
- **Fresh data and confirm FDR.** BatchBH (BatchPRDS variant, because families on one window are positively dependent)
  at q 0.10 over the program's ledger of confirm batches ever read on 2018-01..2025-09, starting with sprint-3000
  (2026-11-02, m 328, 0 rejections). Any family whose mechanism (same entry type, any timeframe or instrument) was in an
  earlier confirm read is CONFIRM-SEEN. **General rule (addendum):** "A cell or family is CONFIRM-SEEN for a window if
  any earlier look at that window (formal or pre-freeze, trade or diagnostic) used its trigger class or its defining
  geometry on the same instrument at the same or a lower timeframe, or if it fails the signal-overlap test.
  CONFIRM-SEEN cells are reported for that window and never enter its hypothesis." Clause (b), its match window and the
  holdout clause are as written in §1b (one copy).
  Overlap test done 2026-11-09: `~/tmp/overlap_test_1109.md`. Ledger: `~/tmp/confirm_reads_ledger_1109.md`. Within a batch, m = the families
  actually read, fixed before the read. The implementation is verified against Zrnic, Jiang, Ramdas and Jordan, "The
  power of batching in multiple hypothesis testing", AISTATS 2020, and implements BatchPRDS (v1 cited it from memory).
- **What K costs.** No row is deleted. "Excluded, not tested" (the 18 C10 units) is recorded apart from "failed".

## 4. Costs

- **Source.** The pin `atr-pin-2026-11-09.1` (ICM, cost of record cor-2026-10-31.2: round trip $0.230, all-in:
  spread, commission and slippage; overshoot $0.145 on stop exits). The today window is 2026-10-12..11-06, outside
  every research window and the holdout.
- **Holdout warm-up touch (recorded).** The pin script (`~/tmp/atr_pin.py` line 24, `load([2026])`) loaded 2026 for the
  Wilder ATR warm-up; the warm-up ran through 2026-01..09 (volatility only, no returns, nothing scored). It is recorded
  in the read ledger as a holdout touch (integrity record). Action (Lane 2): re-seed the pin from 2026-10-01 as
  `atr-pin-2026-11-09.2`.
- **Round trip R at today's level vs bp engine** (today, 1-ATR stop): 5m 0.060 / 0.088; 15m 0.033 / 0.048;
  30m 0.023 / 0.032; 1h 0.016 / 0.022; 4h 0.008 / 0.011; 1d 0.003 / 0.004 (swap per night on top).
- **Deciding cost** = max(today R, bp-engine R on the window read) plus overshoot x (stop-exit share + stop-entry
  share), scaled in bp with the round trip on the bp engine, plus swap. Swap follows the #343 strict-interior rule (a
  rollover counts only strictly inside (fill, exit); a fill or exit at the 17:00 New York instant is not held through
  it), twins included. Until S5 passes per broker and timeframe, the deciding cost includes the measured positive
  fill-vs-replay gap in $, converted at decision-time ATR. "Positive" means fills worse than replay v2; a fill better
  than replay earns no credit. Gates are read at 1.5x that. In practice the gate charges about 2.1-2.3x today's R.
- **What the S5 decomposition changes here.** The measured icm slippage nets to about +$0.016/oz round trip (stop
  overshoot mean -$0.091, 95 % CI -0.127..-0.055; target give-back +$0.168), indistinguishable from 0 in R. The cost of
  record's $0.230 all-in and the $0.145 overshoot are therefore not contradicted and stay. The 2 x $0.15 slippage
  charged by the exploration replay is a prior, not a measurement: `costs_icm-demo.json` and `costs_vantage-demo.json`
  have n_fills = 0 because only the main engine's store fills (`runner.py:1397`) feed `goldbot/execution/costs.py slippage_table`, not
  the 3,700 exploration fills. Lane 5A routes the exploration fills into that table (section 5).
- **Stress line:** the 2010-17 $-constant cost (15m 0.151, 30m 0.103, 1h 0.071, 4h 0.035 R). It is reported, never
  gated. A cell net < 0 under it is tagged **COST-SENSITIVE**, and the tag goes to the owner with any L5 case.
- **Era spread (quant ruling, finding 9).** No third cost line. Cost always comes from the ICM cost of record, never
  from Dukascopy spreads (a different venue: Dukascopy median 2010-17 $0.335, ICM $0.086). For intrabar stop or
  limit-entry families at ≤ 4h, L2 and L3 also require net > 0 at the deciding cost with triggers on the executable
  side (ask for buys, bid for sells, Dukascopy bid/ask bars) and fills at the trigger level. The spread is not charged
  again.
- **Inputs at decision time only.** A lint (`goldbot/research/catalogue/lint.py`) refuses any filter on a realised
  field (that filter turns the 5m null from -0.28 R to +0.21 R).
- **Cost drift.** Demo fills raise the cost of record when the execution e-process fails. They never lower it.
- **Out of scope:** 5m fails the 3x bar under stress and 1m is worse. 2003-05 intraday ATR is not used; its 4h/1d is
  usable.

## 5. What replaces the demo learner and controls

- **Controls.** Matched random-control fills share only 21 (broker, day, side) cells on gold. They are replaced as the
  edge control by 20 replay twins per signal. Random-control arms shrink **by share only** to ≤ 10 % of demo capacity;
  none is removed. They serve as the fill-vs-replay calibration stream. Rule 3's "matched control" becomes the 20 twins.
- **Meta-labeller (H-62).** It was never trained: 0 model rows, `meta_p` NULL on 14,291 of 14,291. It comes out of the
  decision path. It may return only as a frozen score defining a pre-registered regime level, counted in K.
- **Thompson bandit** (`exploration_data.thompson_shares`). It ranks on raw net R at n 1-5, which rewards wide stops
  and luck. It is replaced by **equal shares among eligible arms until the first L2 carry**; after that, allocation on
  the posterior excess among L2+ arms only, with a fixed 20 % uniform-random share and the logged propensity.
  Inference uses only the random share, or IPW on the logged propensity. Allocation is never evidence.
- **Kill rule.** v1 said "the mSPRT (δ 0.15, 20-fill floor)". There is no mSPRT in the code: it is pre-registered
  (`docs/research/prereg-amendment-2026-11-07-kill-only.md` §5, signed 11-07) but not built. The live kill rule is
  `goldbot/research/arm_stops.py::unit_stop` on `config/arm_stops.yaml` (H-61 running net R by style: -30 R within 300
  scalp / -15 R within 100 intraday / -10 R within 40 swing; W1 per-family -30 R within 300; W2/Z futility at 300,
  one-sided 95 % UB < +0.03 R; Z cost stop), applied daily by `goldbot/research/daily_review.py` (`_arm_health` ->
  `retire`). For arms registered after the EVAL-2 ADR the L4 e-processes replace it; existing arms keep it (L4 row).
  The unbuilt mSPRT is superseded by a prereg amendment (Lane 6). Shadow signals count toward n.
- **Fill-vs-replay bias, decomposed** (`~/tmp/s5_decomp_1109.md`; 3,740 paired gold fills, icm 3,297, vantage 443,
  decisions 10-26..11-06). The bias is +0.084 R (t 11). +0.078 R of it is the replay's 2 x $0.15 slippage prior; with
  no prior the bias is noise (icm +0.0075 R, t 0.9; vantage -0.005 R, t -0.6). It is a cost choice, not a path error.
  Decision-to-fill latency is 6.5-13.6 s (icm 1m/5m to 15m; up to 18 s on 1h/4h) and contributes ~0 (entry slip and
  latency net -0.001 R). The gate stop floor never bound (0 of 3,740). (v7, quant ruling 2026-11-09:) "Vantage exit
  slip: measured 0 on vantage-demo. All 294 of 294 stop and target fills are at the bracket level, recorded from the
  exit deal VWAP (R110, 3e72bf9a). This is a demo-server artefact, not evidence about execution. It is never a cost
  prior for real money. Any real-money vantage hurdle uses the larger of the ICM-measured exit slip per leg and session
  and the stop-slip prior, until real-money vantage fills exist." So S5 changes from "fix a 0.08 R path bias" to three
  pieces of work, all in Lane 5A:
  1. **Replace the slippage prior** with measured per-broker, per-leg slippage (entry, stop exit, target exit, time
     exit, by session) from the exploration ledger fills: each leg charged at max(0, upper 95 % bound of the measured
     adverse mean) once the cell has ≥ min_fills, else the $0.15 prior; favourable slippage is never credited.
  2. **Exit overshoot** as its own term: stop exit price = level - side x measured stop overshoot, not double-charged
     with leg 1. **Sign convention:** slippage is side-signed, + = the fill is better than the level. The measured icm
     stop-exit mean is -$0.091 (adverse). The charge is max(0, adverse upper 95 % bound), i.e. max(0, -(lower 95 %
     bound of the signed mean)) = max(0, +$0.127) on today's CI, never a credit.
  3. **Void the non-gold labels** (the 241 paired rows and all other non-gold v1 labels) and filter every reader by
     instrument.
  Pre-registered expectation: icm bias falls from +0.090 R to about +0.02 R (the uncredited target give-back,
  +0.017 R, remains). (v7:) "Vantage S5: the exit deal price is recorded (exit deal VWAP, R110). Vantage-demo fills
  stops and targets at the level, so its exit-slip term cannot be measured on demo. S5 on vantage is read on the entry
  leg (fill vs replay, spread) only. Its exit term is not read on demo. Any real-money use takes the larger of the
  ICM-measured exit slip (per leg and session) and the prior. Vantage S5 cannot be green on the exit leg from demo
  data."
- **exploration_obs.** Counts at full weight, scored on the same replay path, only where the decision instant and the
  fill-bar close are stored point in time. Excluded only by decision-time fields (risk at decision below the floor,
  instrument price-scale mismatch), applied identically to signals and twins. Label size is never a criterion. Report
  how many of the 1,038 labels beyond 20 R the input rule removes (the 241 gold-bar non-gold labels are expected to
  be among them).
- **Versioned replay.** Every fix lands as a new replay version (`replay_version = "r2"`, new columns or a side table
  keyed (candidate_id, replay_version)). `label_sim_r` is never overwritten; the learner and the evaluator name the
  version they read.
- **Demo mix.** Demo is 1,938 of 2,041 strategy fills on 1m/5m, and 12 on 1h/4h. Demo capacity moves to L2+ cells
  (15m-1d). 1m/5m arms whose history gross bound is ±0.01 R read NO SIGNAL and are switched off under rule 3. Shadow
  signals carry the learning volume.
- **Fills/day forecast against the owner's 150-250/day.** Batch-1 detectors fire about 1.2 signals/day per account on
  gold at explore rates (C03 30m 0.22, C03 1h 0.13, C02 0.12, S21 about 0.5, S20 0.06, B07 0.10; n over about 2,000
  explore sessions). The 11-07 cap gives about 78/day today, below the 150 floor.
- **PD + PO volume ruling (`~/tmp/pd_volume_ruling_1109.md`).** The owner's 150-250/day stands; only the mix moves.
  Per account per day (x3, about 50 each at the floor): learning = every signal from 15m-1d L2 cells plus the KoG/G
  arms, uncapped (about 3-6/day now); 15m-1h random-control/execution arms about 15 (fill-vs-replay data on the
  timeframes we mean to trade); 1m/5m calibration arms capped at 30, kill-only for L4(i) per broker, leg and session,
  strata rotating, and no 1m/5m fill counts as learning. Never add 1m/5m above the cap to reach a number; a shortfall
  is accepted down to the 150 floor and reported, and is fixed with 15m-1h controls, not scalps. No owner ask now; a
  contingency question (lower the floor to about 90/day) only if on 11-16 the accounts cannot hold 150 without
  information-free fills. 5B rollout: (a) ADR, quant signature and the K freeze lifted by 11-10; (b) shadow replay v2
  plus the L4(i) e-process by 11-11; (c) KoG/G arms as new registry rows (K+1) with control twins by 11-11; (d) 15m-1h
  control arms and the 1m/5m cap by 11-11, adds before cuts; (e) 15m-1d L2 cells from 11-12 as each carries, vantage
  after the SUGAR-stop fix; (f) volume and mix review 11-16 (PD, #34).
- **RiskGate and real money unchanged.** No lane changes RiskGate, the per-trade cap, the daily loss limit, the
  real-money confirmation path or the phase gate. Lane 5B carries a test asserting that the RiskGate config, its code
  and the `state/phase_state.json` handling are unchanged by the diff.

## 6. Self-tests: all must pass before any verdict is trusted

| # | Test | Threshold |
|---|---|---|
| S0 | Harness: NULL-RAND controls per TF, recomputed with 20 twins (the 90-control regression). Measures the twin correlation used for the finite-M SE | Mean gross and mean excess within ±0.01 R, \|t\| < 2. No control cell with gross < -0.05 R at n ≥ 300. Control-cell dispersion = sampling variance (χ² p > 0.01). Buy-every-close and drift-walk sentinels fail E9 and L2 |
| S1 | Null population, **run first, before L2 on real cells**: ≥ 500 replicates of random-entry families over the real cell grid, real bars and costs; null families keep real stop geometry and exits. Also signals circularly shifted ≥ 20 days, sign-flipped units, and regime labels shuffled in 20-day blocks | L2 carry rate: Clopper-Pearson 90 % upper bound ≤ 5 % + 2 pp. Confirm FDR ≤ 0.10, and P(any family passes when all are null) ≤ 0.10, each with its CP 90 % upper bound ≤ threshold + 2 pp. Forward e-process crossings ≤ 5 % with weekly blocks. Shuffled-regime "gate" posterior ≥ 0.8 in ≤ 10 % of runs |
| S2 | Positive, label shift: plant +0.05/+0.10/+0.20 R net in 10 of ~300 cells, in 1h/4h only, with a null 15m neighbour | Detection within ±10 pp of the stated power table. Planted cells named and the 15m cell not carried in ≥ 90 % of runs. 90 % intervals cover the plant 85-95 % of the time |
| S3 | Positive, full path: a synthetic bar pattern pushed through sim + costs (+0.20 R, gold 1h, n 1,000). A cost-separation plant: +0.10 R at 15m. A gate-only plant (a pure cost-in-R effect) | ≥ 80 % detected. The 15m plant reads COST-BOUND at the stress cost and TRADABLE at the deciding cost. The gate-only plant shows as gate value with signal ≈ 0 |
| S4 | Leakage: regime states shifted +1 bar; a realised-cost filter; the OCO side-0 trap on S21 | Identical results. Lint refuses the filter. Probe \|t\| < 2 on S21 (the naive read shows 8.4) |
| S5 | Execution: fill vs replay **v2** (measured per-leg slippage, no credit, exit overshoot, non-gold labels void), per broker and timeframe band, gold only | \|bias\| ≤ 0.02 R. Today +0.084 R on replay v1, of which +0.078 R is the slippage prior; expected icm about +0.02 R on v2. Vantage (v7): read on the entry leg only; "Vantage S5 cannot be green on the exit leg from demo data" (section 5). Until S5 passes, L4 forward reads do not count and the positive gap is charged in the deciding cost |
| S6 | Truncation, `tests/test_eval2_truncation.py`: for sampled instances, recompute the signal, ATR, regime level, MTF context and twin stop on bars truncated at the decision bar's open + tf | Results identical. Higher-timeframe context is visible only after its close. Regime cut-points (terciles) come from the trailing 250 sessions ending at the prior session close, never the full window |

A fast version of S0, S2, S3 and S6 runs in CI under a new `selftest` pytest marker and its own CI job, co-owned with
the SRE: a planted +0.20 R on 1h/1,000 must pass ≥ 80 %, and a planted 0 must pass ≤ 5 %. Real-world check: tsmom
(gross +0.06 R, net negative) must reach L1 and stop at L2.

## 7. What the owner sees (graded verdicts, weekly, each with net £/day at demo size and its 80 % interval)

| Verdict | Meaning |
|---|---|
| **NOT TESTED** | Data infeasible or excluded |
| **INSUFFICIENT (power)** | MDE > the card's edge. Waits for more data or breadth |
| **NO SIGNAL** | Retired. Not re-run |
| **COST-BOUND** | Information found, but below cost. Route: timeframe, session or order type |
| **EXIT-DRAG** | Information above cost, exits lose it. Route: exits-researcher |
| **TRADABLE ON HISTORY** | L2 carry. Worth a confirm read |
| **CONFIRM-SEEN** | Trigger class or geometry already looked at on that window, or excess overlap e ≥ 15 pp (clause (b)); reported there, decided on TEST-OLD + the forward holdout + L4; the 2018-25 corroboration stays an AND with no credit |
| **HOLDOUT-SEEN / HOLDOUT-FRESH** | Which holdout route the family takes (section 1b) |
| **CONFIRMED** | L3 |
| **ON TRACK / DRIFTING / KILLED** | L4 forward |
| **LIVE-READY** | L5. The owner decides |

The owner sees rungs and £, not 0/N.

## 8. Conflicts resolved (one line each)

1. **Unit:** instance excess, day-clustered, over the sleeve book (macro) or the day-summed triple (opportunity).
2. **Twin window:** after the signal's exit, excluding same-family fire bars, at the twin's own ATR. Pre-fill twins
   inherit the selection (D4); post-fill twins overlap the signal's own path.
3. **L1 probe:** the ±1 ATR barrier, over the markout area (microstructure) and horizon returns (strategy).
4. **L1 role:** a family futility retire only, not a per-cell gate (exits).
5. **Explore multiplicity:** shrinkage plus a null-calibrated threshold frozen before the L2 run, not
   step-SPA/Romano-Wolf. Explore is spent and claims nothing; error control lives at confirm.
6. **Confirm unit:** the family over ≤ 3 carried cells; cells forward by Holm within the family (closed testing).
7. **Confirm FDR:** one BatchPRDS sequence per window; TEST-OLD primary (index 1), 2018-25 corroboration (batch 15).
8. **DSR at K:** reported, not gated, up to L4; at L5 `research/gates.py` as coded; the K basis is the owner's.
9. **Max drawdown:** sizing, not a gate; the size ceiling is enforced by RiskGate validation at L5 time.
10. **EVAL-1 gates:** E4 becomes "over 2006-2009 plus 2018-2025, net positive in ≥ 3 years incl. 2021 or 2022, no
    2-year sub-period net t ≤ -2". E7 becomes the full L3 test on TEST-OLD (the primary window). E8 counts active days. E9 kept for multi-day holds. E6
    becomes the power floor.
11. **Deciding cost:** max(today, bp engine) + overshoot (stop exits and stop entries) + swap + the positive S5 gap
    until S5 passes, x 1.5; executable-side triggers for intrabar stop/limit families ≤ 4h. No era-spread line.
12. **Forward:** a conformance test plus a futility kill, for arms registered after the ADR.
13. **No product of confirm p and forward e.**
14. **exploration_obs at full weight** on replay v2, excluded only by decision-time fields.
15. **Regimes:** ≤ 3 pre-registered levels, trailing-250-session cut-points, regime-matched twins.
16. **Real money by fill-based e-BH at 0.05** (ML): no. L5 is the owner's call on L3 + L4 + holdout + gates.py.
17. **Book spanning alpha** (macro): reported once two or more families reach L3.
18. **Kelly sizing:** an input to the owner only.
19. **Ruling A withdrawal (ADR; PD + PO + quant decide; the owner is informed):** "`screen_min_events_daily: null`;
    daily units use `screen_retire_floor` 300 with the futility rule." Today `config/settings.yaml` holds
    `screen_min_events_daily: 150` (`goldbot/config.py:485`, read by `goldbot/research/screen.py:71`);
    `screen_retire_floor` does not exist yet (Lane 3 adds it).
20. **S5:** a cost-model replacement (measured, no credit) plus label voiding, not a price-path fix.
21. **Holdout:** partly spent; per-family HOLDOUT-SEEN/FRESH, forward holdout for SEEN, sealed loader guard.
22. **Views:** a snooping ledger that raises reported K, outside the registration budget (PD ruling).

## (a) Build plan (tests first; no two lanes write the same file)

Lane 6 goes **first**. No code lane starts before its ADRs, prereg and frozen signatures are merged.

| # | Lane / owner | Files (sole writer) | Tests first | Gate |
|---|---|---|---|---|
| **0** | **Registry integrity: implementer (branch `fix/registry-unlinked-prereg`), data-engineer for the backfill, quant-reviewer verifies** | **`goldbot/research/registry.py`, the backfill script** | **`tests/test_registry_unlinked_prereg.py` (a stale ad-hoc prereg is counted in quarter_trials and n_trials_effective; a linked prereg is counted once; a queued prereg is not double-counted; a loop row is out of quarter_trials and in n_trials_effective; withdrawal exemption: an ad-hoc prereg is exempt only through a linked `withdrawn` row with reason `unevaluable:`/`never formed:` and sha256 evidence, written before any read; a self-declared "not a trial" exempts nothing)** | **Gates the registration-freeze lift together with the verified backfill; parallel with Lane 6.** |
| 6 | Docs, first: research director | `docs/research/prereg-2026-11-09-eval2.md` (incl. frozen signatures of every new public function in Lanes 1-5, the replay-v2 spec and its pre-registered S5 expectation, and the amendment superseding the unbuilt mSPRT); ADRs via `scripts/docs_assemble.py new-adr --slug eval2-method` and `--slug ruling-a-withdrawal`; `docs/research/research-plan.md`; `docs/research/live-candidates.md` | — | Quant signature; the OPEN K budget resolved before any batch-1 registry row |
| 1 | Harness twins: implementer, quant-reviewer | `goldbot/research/catalogue/sim.py`, `goldbot/research/catalogue/run.py` (twin draw wiring, read-ledger batch numbering) | `tests/test_catalogue_twins20.py` (after-exit draw, same-family exclusion, twin-ATR geometry, stratum); `tests/test_catalogue_harness_sane.py` (S0) | S0 green |
| 2 | Costs + lint: microstructure + implementer | `goldbot/research/catalogue/costs.py`, `config/catalogue_costs.yaml` (pin version, S5 gap term, strict-interior swap), new `goldbot/research/catalogue/lint.py`; the ATR pin re-seed | `tests/test_cost_decision_time.py` (deciding cost, overshoot incl. stop entries, executable-side triggers, stress line); `tests/test_catalogue_lint.py` | Parallel with 1 |
| 3 | Ladder stats: evaluator + ML + implementer | new `goldbot/research/eval2/{probe,hier,confirm,verdict}.py`; `goldbot/research/catalogue/stats.py`; futility branch in `goldbot/research/screen.py`; `goldbot/config.py` + `config/settings.yaml` research keys (`screen_retire_floor`) | `tests/test_eval2_*.py` (BatchPRDS against the AISTATS 2020 paper, control-variate bootstrap, Holm, EB freeze, verdict table, futility) | After 1+2 |
| 4 | Self-tests: evaluator-staff + SRE (CI) | `tests/test_catalogue_power.py`; `tests/test_eval2_truncation.py` (S6); `tests/test_holdout_seal.py` (holdout loader guard); `scripts/eval2_selftest.py` (≥ 500 reps, ≤ 8 workers); `selftest` marker in `pyproject.toml` and `selftest` job in `.github/workflows/ci.yml` (SRE co-owns these two) | — | S0-S6 pass, or the method is not used |
| 5A | Forward, research/brain: performance + implementer | `goldbot/research/eval2/forward.py`; `goldbot/research/exploration_data.py` (replay v2: measured per-leg slippage at the upper 95 % bound with no credit, stop overshoot term, 1m/5m deadline = decision + delay + max_hold, versioned `replay_version`; void list for non-gold v1 labels; instrument filter in every reader; shadow twins; equal shares then posterior allocation with propensity); the aux migration in `goldbot/db/migrations.py`; `goldbot/ops/jobs.py` `exploration_costs` and `goldbot/execution/costs.py` `slippage_table` (exploration ledger fills feed the per-broker, per-leg table) | `tests/test_eval2_forward.py` (Ville ≤ 5 %; S5 per broker/TF band; `label_sim_r` untouched; non-gold v1 labels void and excluded; no favourable-slippage credit; stop leg not double-charged) | Starts after Lane 6. Forward reads count only after S5 |
| 5B | Forward, engine: implementer + trading-safety-reviewer | `goldbot/engine/exploration*.py` consumers (incl. vantage `record_close` storing the deal price, not the level), `goldbot/research/arm_stops.py`, `goldbot/research/daily_review.py`, `config/arm_stops.yaml`, the per-account EVAL-2 enable flag | `tests/test_eval2_engine.py` (enable flag per account; controls shrink by share, never removed; vantage exit deal price recorded; RiskGate/real-money unchanged assertion) | Gated on S5 green + one L2 carry + trading-safety review; enabled one account at a time (stagger). The vantage exit-price fix may ship ahead, under the same safety review, because vantage S5 cannot pass without it |

**Build order and durations (working days, estimates).**

| Step | Lanes | Duration | Starts after |
|---|---|---|---|
| 1 | 6 (ADRs, prereg, frozen signatures, replay-v2 spec) | 0.5-1 | quant signature of v2 |
| 2 | 1 and 2 in parallel; 5A in parallel (replay v2, void list, slippage feed) | 2-3 | step 1 |
| 3 | 3 | 2-3 | 1 + 2 |
| 4 | 4 (S1 first, then S0, S2-S6; CI job) | 2 + compute (≥ 500 reps) | 3 |
| 5 | Batch-1 L2 on explore, card freeze (holdout and CONFIRM-SEEN status fixed), TEST-OLD (primary), then 2018-25 (corroboration) | 1-2 | S0-S6 green (S5 not needed for history reads); registration freeze lifted; TEST-OLD DQ ruling (intraday 2008-09 only unless refill + re-check pass); overlap test done; power recomputed |
| 6 | 5B engine, account by account | 2 + 1 per account (3) | S5 green + first L2 carry + safety review |

Earliest first TEST-OLD read: about 8-11 working days after v3 is signed. The engine changes come later, gated on S5.

## (b) Decision rights

- **PD + PO + quant (+ evaluator verdicts):**
  - adopting EVAL-2, its thresholds and the ADRs, incl. the Ruling A withdrawal (owner informed);
  - K booking, the Q4 budget (OPEN) and the confirm read;
  - research allocation; the fills/day shortfall (PD);
  - switching demo arms on or off, and the twin/control design, within the RiskGate caps.
- **Owner only:**
  - going live with real money and confirming L5; the DSR K basis at L5; exemptions from `research/gates.py`;
  - sizing, per-trade risk and the daily loss limit;
  - spending.
- EVAL-2 changes none of the owner-only items. It hands the owner a drawdown-based size ceiling and the
  COST-SENSITIVE tag as inputs.

## (c) Honest money expectation

- **Detectable edge.** 0.10 R x 0.5 trades/day x £50/R (0.5 % of £10k) = **£2.5/day** (re-derived).
- **Batch 1 at its explore edges** (rate x net x £50): C03 30m £2.5, C03 1h £0.6; C02 £0.5, S21 £1.2, S20 £0.7,
  B07 £1.0; total about £6.5/day. With B07 INSUFFICIENT and C02's 15m cell excluded, the confirmable part is C03 + C02
  (≥ 1h) + S20/S21 on TEST-OLD, but C03 30m's power on 2008-09 is about 36 % full and 11 % half.
- The history windows are spent (2018-25) or thin (TEST-OLD intraday 2008-09). Most batch-1 decisions will come from the
  forward holdout, at about 12 months per family.
- **After discounting.** Out-of-sample decay of 26-58 % and confirm power give **£1-3/day on a £10k demo, and
  £0.1-0.3/day at £1k real**. It is nowhere near £50/day or doubling weekly.
- **What the S5 finding means for money.** Measured icm slippage is about zero, so the demo fills have not been losing
  0.08 R to execution; the replay was charging a prior the fills did not pay. That does not make any arm profitable:
  the replay-v2 relabel will move every exploration arm's replay net up by up to +0.078 R, and each arm's read must be
  redone on v2 before any keep/kill call depends on it.
- **What EVAL-2 buys.** A real edge gets a fair test, each rejection carries its reason, and demo kills stop on noise.
  The owner must hear plainly that the catalogue is not where £50/day comes from.

## (d) First candidates (batch 1) and their K

| Family hypothesis | Candidate cells (L2 carries at most 3 mechanically) | Explore n / excess / sd(excess): v1 hand-selected cells, informational; L2 decides | Status |
|---|---|---|---|
| C03-V1 flag | Every gold cell of the detector (all registered variants, 15m-1d, registered exits) | 436+266 / +0.239 pooled / 1.55 | TEST-OLD primary (intraday 2008-09), 2018-25 corroboration, holdout once (conditions 1-6). Below 0.3 power at its shrunk estimate: INSUFFICIENT, routed to L4 learning-class demo plus the forward holdout, never to real money on that route alone. Per cell (overlap test, `~/tmp/overlap_test_1109.md`): 15m and 30m CONFIRM-FRESH and HOLDOUT-FRESH. 1h CONFIRM-SEEN (BOX e 16-19 pp); 4h CONFIRM-SEEN (BOX e 16-27 pp); all 1h/4h cells stay CONFIRM-SEEN for 2018-25 (BOX via P9). Holdout status from the BOX-15m re-run (`~/tmp/overlap_box15_1109.md`, script sha 46027b13…, explore only): V0/V1 1h n 276, e 11.0 pp, HOLDOUT-FRESH; V2 1h n 87, e 15.5 pp, HOLDOUT-SEEN; V0/V1 4h n 43, e 3.1 pp, HOLDOUT-FRESH; V2 4h n 8, e 17.4 pp, HOLDOUT-SEEN (n < 20, flagged). 1d CONFIRM-SEEN (IBB_T e 26 pp), HOLDOUT-FRESH. |
| C02-V0 double | Every gold cell of the detector at ≥ 1h (registered variants and exits). The 15m cell is CONFIRM-SEEN (ledger P5, R-13: equal-highs/lows geometry, 15m, 2018-25): excluded from the L2 candidates and the hypothesis, reported on TEST-OLD only | 249 / +0.102 pooled / 0.6 | ≥ 1h same as C03 (conditions 1-6). 15m CONFIRM-SEEN (R-13, P5, 2026-10-19, equal-high/low geometry on 2018-25); reported on TEST-OLD only. Per cell: 1h V0-V2 CONFIRM-SEEN (NR4ID1H e 19-28 pp), HOLDOUT-FRESH. 4h V0 CONFIRM-SEEN (IBB_T e 17), HOLDOUT-FRESH. 4h V1 CONFIRM-FRESH and HOLDOUT-FRESH (n 19, flagged). 4h V2 CONFIRM-SEEN and HOLDOUT-SEEN (BRK8 e 15, IBB_T e 19). 1d V0-V3 CONFIRM-SEEN (IBB_T e 41-45), HOLDOUT-FRESH. |
| S21-V0 stretch OCO | Every gold cell of the detector (all registered variants, 15m-1d, registered exits) | 1,088 / +0.050 / 0.57 | CONFIRM-SEEN and HOLDOUT-SEEN (TREND-10 opening range in F3/F4; 39 zscore specs in F5; session_open/ORB in P1, P2, P7, P8). Route: TEST-OLD plus forward holdout; S0 first |
| S20-V0 NR7 OCO | Every gold cell of the detector (all registered variants, 15m-1d, registered exits) | 128 / +0.220 / 1.20 | CONFIRM-SEEN (nr_breakout n 7 on 4h in F5) and HOLDOUT-SEEN (P1 8-bar compression break). Route: TEST-OLD plus forward holdout |
| B07-V1 triangle | Every gold cell of the detector (all registered variants, 15m-1d, registered exits) | 212 / +0.058 / 1.56 | INSUFFICIENT. Per cell: every cell is CONFIRM-SEEN. HOLDOUT-SEEN at 4h V1/V2 and 1d (all variants); HOLDOUT-FRESH elsewhere. INSUFFICIENT unchanged. |

**Conditions 1-6 for C03 and C02** (quant findings 1-6, as amended by the addendum): (1) TEST-OLD is index 1 and the
2018-25 read is batch 15 after every read in the ledger, BatchPRDS at q 0.10, and the evaluator confirms neither mechanism was in an earlier confirm read (else
CONFIRM-SEEN); (2) the views are booked in the snooping ledger and the registration freeze is lifted; (3) the L3
statistic is the control-variate net with the four-part gate; (4) S1 runs first and the L2 thresholds, model and code
hash are frozen in the card before L2 on real cells, with candidate cells as above; (5) cells go forward only by Holm
within the family; (6) the year-count and 2-year sub-period rule, and L5 under `research/gates.py` as coded.

**Batch rules.**
- Candidate cells are mechanical; nothing is hand-picked or hand-dropped (v1's "4h dropped" is deleted).
- S1 runs first. The L2 thresholds, the hierarchical model (terms, REML, Student-t df, prior population and its
  de-duplication) and the code hash are frozen and recorded in the card **before** the L2 run on real cells. No
  threshold changes after it.
- Families whose TEST-OLD power at the shrunk estimate is < 0.3 are not read. They stay INSUFFICIENT.
- Holdout status and CONFIRM-SEEN status are fixed in each card before L2 (overlap test done 2026-11-09).
- S21 is CONFIRM-SEEN on the ledger (TREND-10, zscore, session_open/ORB); no further evaluator check is needed for that.

**K booking.**
- One registry row per batch-1 family (about 5), second in the PD order after the freeze lifts.
- One row per carried cell (L2) must fit the 105-row headroom under the PD order, or move to 2027Q1.
- Each family read carries its index in the TEST-OLD sequence and its 2018-25 batch number (15).
- The explore-view block is in the snooping ledger, not the registry.
- The synthetic plants and null replicates in S1-S3 are not trials and are not counted. Replay v2 is a relabel, not a
  trial; any arm re-read on v2 is a new read under its own prereg.

**Re-derived for this hand-back:**
- C03 30m TEST-OLD power: BatchPRDS α_1 = q γ_1 = 0.05, BH m 3 one true -> z(1 - 0.05/3) = 2.13; SE = 1.56 / √218 =
  0.106; full excess 0.2655 R -> Φ(2.51 - 2.13) = 65 %; half -> Φ(1.26 - 2.13) = 19 %. Matches the addendum.
  n 218 = 436 explore instances over 8 years scaled to TEST-OLD's ~4 years. With 2008-09 only (n ≈ 109): 36 % / 11 %.
- Ledger P5 is R-13 (EQH/EQL magnet diagnostic, 15m, 2018-2025-09), the basis of C02 15m CONFIRM-SEEN.
- The sprint-3000 confirm read from `state/sprint3000_reads.jsonl` and `~/tmp/s3k/confirm.json`: m 328, 480 specs,
  0 passed, 24 `nr_breakout` 4h specs (n 4 and 7), 39 `zscore`, data 2016-01-03..2025-09-30.
- The live kill rule's code path (`arm_stops.unit_stop` via `daily_review._arm_health`); no mSPRT in `goldbot/`.
- `config/settings.yaml` `screen_min_events_daily: 150`; `screen_retire_floor` absent.
- The ATR pin's 2026 load (`~/tmp/atr_pin.py` line 24, `load([2026])`).
- Carried from v1, not re-derived today: C03-V1 gold 30m n 436, net +0.2261 R, excess +0.2655 R, sd(net) 2.26 R,
  sd(excess) 1.56 R.
- DSR K = 3,471 (registry n_trials_effective after the sprint-3000 backfill: 2,629 explore incl. 8 ensembles + 328
  confirm + 159 member-only confirm + 1 null-ensemble confirm; 389 never-scored specs withdrawn with evidence) +
  17,862 snooping = 21,333; sensitivity 3,471 + 16,680 = 20,151. The t required is recomputed by
  `goldbot.research.metrics` at that K. Reported, not gated, before L5.
- The S5 numbers are the microstructure lane's (`~/tmp/s5_decomp_1109.md`),
  not re-derived by me.

## (e) v7, 2026-11-09: vantage S5 wording and the Lane 5A reading choices (signed with conditions C1-C10)

**Status: signed with conditions by the quant-reviewer, 2026-11-09 (review of 8b2345c4).** Part 1 (vantage wording) is signed as written and binds now. Parts 2-3 are signed as amended by conditions C1-C10 of that review. C1, C4, C6, C7 and C10 must be in this text and in the code before the first S5 read, and C2, C3, C5, C8 and C9 before the first L4 read. Until then no S5 or L4 read counts. The quant's timing condition: "this text must go into EVAL-2 v7 and be signed before
any vantage S5 read is run. Written later, it is a post-hoc change to the reading rule." No S5 read has been run on
replay v2 for any broker; the Lane 5A code only computes it.

**1. Quant ruling 2026-11-09, copied exactly.**
- S5 decomposition line (replaces "vantage exit slippage is unmeasured, not zero"): "Vantage exit slip: measured 0 on
  vantage-demo. All 294 of 294 stop and target fills are at the bracket level, recorded from the exit deal VWAP (R110,
  3e72bf9a). This is a demo-server artefact, not evidence about execution. It is never a cost prior for real money.
  Any real-money vantage hurdle uses the larger of the ICM-measured exit slip per leg and session and the stop-slip
  prior, until real-money vantage fills exist."
  Conditions: "(a) Report target fills and stop fills separately. Zero slip on targets (limit orders) is normal. Zero
  slip on stops is the artefact. (b) List any stop that a bar gapped through and that still filled at the level. That
  is direct proof of a synthetic fill; include the count. (c) Rows with `exit_price` None are excluded and counted.
  They are never imputed at the level."
- EVAL-2 wording (replaces "vantage S5 blocked until exit deal price recorded", sections 5 and 6): "Vantage S5: the
  exit deal price is recorded (exit deal VWAP, R110). Vantage-demo fills stops and targets at the level, so its
  exit-slip term cannot be measured on demo. S5 on vantage is read on the entry leg (fill vs replay, spread) only. Its
  exit term is not read on demo. Any real-money use takes the larger of the ICM-measured exit slip (per leg and
  session) and the prior. Vantage S5 cannot be green on the exit leg from demo data."
- VCAL5: "DROP. Do not register it, K +0."

**2. How Lane 5A implements it** (`goldbot/execution/costs.py`, `goldbot/research/exploration_data.py`,
`goldbot/research/eval2/forward.py`).
- (a) The leg table keeps stop and target cells apart, per broker and session (`leg_slippage_table`).
- (b) `synthetic_stop_fills` counts, per account and night, the stops whose exit minute opened through the level and
  that filled within $0.005 of it (`exploration_learn` report `replay_v2.synthetic_stop_fills`).
- (c) `ledger_legs` excludes and counts exits with no exit price (`legs_excluded.no_exit_price`); never imputed.
- Vantage's replay v2 exit cells charge `max(ICM charged cell, $0.15 prior)` (`real_money_exit_charge`); its own
  demo exit legs never set a charge. Its entry cells are its own measurement. S5 on vantage reads
  `entry_fill_minus_replay_r` only and reports `exit_leg_read = False`.

**3. Reading rules as signed with conditions C1-C10 (quant review of 8b2345c4) and as built.**
- C1, point in time: each r2 label is costed only from ledger fills before its own session day (17:00 New York the
  evening before its session date, which is earlier than and so stricter than 00:00 UTC of that date). One cached model
  per session day (`daily_costs_v2`); the as-of is in the model's version; a model effective on or after the session
  day's start is refused. Commission and swap come from the broker's r1 model as at labelling (broker terms, not
  slippage measurements).
- C2, day clustering: every e-process takes one term per session day (the day's mean), with the prior sd and plug-in
  bets kept: predictable plug-in bets, winsorised from above at 3 sigma, Bennett's bound, no bet on the first 5 day
  terms (module docstring of `forward.py`). Simulated at the H0 boundary with day sd equal to the within-day sd (4,000
  paths, 5 per day): execution crosses e >= 20 in about 1.4 %, futility e >= 10 in about 3.4 %, edge e >= 20 in about
  0.9 %. Power: a 0.05 R execution overrun is caught by 300 fills (60 days) in 98 % of runs at day sd half the
  within-day sd, 66 % at equal sd; within 100 fills in about 15 %. The futility read at sd 1.3 R remains weak: the
  method's "300 signals detect a 0.17 R shortfall" holds for low-sd cells only.
- C3, execution streams: one per (broker, leg in {entry, exit}); never an exit stream for a broker whose demo fills
  exits at the level (`DEMO_EXIT_AT_LEVEL`: vantage, vantage247, vantage_obs); KILLED when any stream's running maximum
  reaches 20 x m (m = streams formed); `e_exec` reports that maximum / m.
- C4, S5: passes per broker and band on at least 50 pairs over at least 10 session days, with |mean fill - replay v2|
  <= 0.02 R and a lower one-sided 95 % day-clustered bound >= -0.02 R. Bands 1m-5m (informational only), 15m-30m,
  1h-4h, 1d.
- C5: the forward replay uses the r2 cost model whose version and sha256 the card recorded at forward start
  (`pinned_costs`); a nightly refit never changes forward labels.
- C6: the stop overshoot is measured against the bar-implied price (a sell close against min(level, the bid open of
  its exit minute), a buy close against max(level, the ask open)); a stop leg with no bar at its exit minute is left
  out and counted (`stop_no_bar`).
- C7: the nightly report gives each broker's share of entries filled within $0.005 of the quote; at 95 % or more on a
  demo-exit-at-level broker its entry cells take max(ICM entry cell, prior) and its S5 reads NOT AVAILABLE.
- C8: forward units carry broker, tf and `s5_ok` (`apply_s5`); a fill value where S5 has not passed for that
  (broker, band) is refused.
- C9: the exit prior is the family's r2 shadow labels decided before forward start (counts + 1/2), frozen with its
  sha256 in the card; a family stays WAITING until 100 signals.
- C10: the prereg carries the quant's section 5 amendment verbatim; the S5 read reports the bias by the fill's exit kind
  and the paired rows on arms the spec does not name.
- Kept from the first hand-back: L4(i) per-stream level e >= 20 (Ville 5 %; the method's (iii) e >= 10 is a 10 %
  bound); the exit mix judged from 50 fills at p >= 0.01; the time leg always charges the $0.15 prior (the ledger
  records no close quote; recording it is a Lane 5B item); the exploration fills feed only the replay's leg table, never
  the engine's `slippage` cells, the RiskGate cost, the deciding cost or any real-money hurdle; a replay with no bar at
  its entry minute is unresolved (r1 and r2).
- Coverage: r2 is computed nightly for gold candidates decided in the last `EXPLORATION_LABEL_DAYS` (30) days, the
  labeller's bar window; older candidates keep r1 only unless a one-off backfill is run.
