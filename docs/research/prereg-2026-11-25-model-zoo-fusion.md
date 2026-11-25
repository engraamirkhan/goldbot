# Pre-registration: model zoo and fusion on the retired gold arms (H-101, spec `zoo-v1`)

Principal ML researcher, 2026-11-25. Written and committed **before any data under this card is read**: no label, no
feature row, no candidate count. Status: **proposed**. It needs a quant-reviewer signature and a research-director
allocation of K before any stage runs. No trial is dispatched by this card.

- **Owner idea (2026-11-25):** train several models on historical and present data, then compare each model with
  combinations (fusions) of them.
- **Builds on:** H-100 / `prereg-2026-11-24-self-retrain.md` (`sr-v2`, `goldbot/research/self_retrain.py`): its
  population (the 10 pre-named retired gold arms), its 40 inputs (`FEATURES` + `side`), its filter (drop the bottom
  third) and its gate statistics (`oos_metrics`, `holm`, day-block bootstrap, B = 10,000).
- **Windows:** W2 / W3 as adopted (`amendment-w3.md`): learn 2010-17 plus 2003-05, test_old 2006-02..2009 (clean
  intraday years 2008-02..2009-12, ADR 0067), confirm 2018-01..2025-09 read once, holdout 2025-10-01..2026-09-30
  sealed and never read.
- **Scope:** gold only (section 2.4). Nothing in the engine, RiskGate, `config/accounts.yaml` or the arm config
  changes. ACTIVE means candidate: nothing trades until the existing gates pass.

## 1. Prior evidence and why this is not a re-test

**What has failed in this area (do not re-test):**

| Read | What | Instrument, window | Net of costs | Result |
|---|---|---|---|---|
| R-07 | meta-models over primaries with no gross edge | XAUUSD Dukascopy, 2010-25 | yes | retired |
| R-20 / A27 (ML-M1, M1c, M2) | meta-label models on rule candidates; M2 on `box_break_close` 15m | XAUUSD, 2010-25 | yes | M2 top decile net -0.513 R; retired |
| MLNET V3, MLEXEC, PROBTREND | direct ML entry, execution and probability-trend models | XAUUSD | yes | retired (`mlprob/mlnet-results.md`) |
| R-18 | discovery v2 | XAUUSD | yes | retired |
| Specialists' LightGBM (shadow) | meta-model on live specialists | ICM demo, 145 trades | yes | AUC 0.44, Brier 0.206 vs 0.197 base; retired 2026-11-13 (`ml-review-2026-11-13.md`) |
| H-62 meta-labeller | online LightGBM | ICM demo | n/a | 0 model rows; retired |

Our meta-models sit at AUC about 0.50. **A new algorithm is not new information.** The owner's idea, read honestly,
adds two things and only two:

1. **Variance reduction across learners.** Averaging or stacking learners whose errors are not perfectly correlated
   lowers forecast variance (Bates and Granger 1969, *Operational Research Quarterly* 20(4), forecast combination,
   macro series, not a trading return; Timmermann 2006, *Handbook of Economic Forecasting* vol. 1, ch. 4, survey: the
   equal-weight average is hard to beat and estimated weights often lose out of sample, the "forecast combination
   puzzle", replicated across many macro and finance series, not net of trading costs). Stacking (Wolpert 1992,
   *Neural Networks* 5(2); Breiman 1996, *Machine Learning* 24(1)) needs out-of-fold base predictions or it overfits.
   Single studies on trading returns exist; none we know of is on gold meta-labels net of costs.
2. **A non-linear and regime-conditional read of the same 40 inputs** (trees, analogs, a volatility-regime gate).
   Gu, Kelly and Xiu 2020 (*Review of Financial Studies* 33(5), US equities 1957-2016, monthly, gross of costs) find
   trees and nets beat linear models in cross-sectional return prediction; that is equities at monthly horizon, not
   intraday gold, and **not net of costs**. Treat it as motivation only.

Neither adds data. So the **prior is low: P(any candidate passes confirm) about 3 %** (judgement, from R-07 / R-20 /
MLNET and the 0.50 AUC record). The value of the card is that **8 K settles the owner's question for this population
and feature set**, and a null retires the "model zoo over the sr-v2 inputs" family so it is not re-asked trial by trial.

**What is new against R-20:** the population (the 10 pre-named retired arms, not `box_break_close`), the frozen sr-v2
40 inputs, and the fusion comparison itself, which has never been read in this program.

**Disclosure (CONFIRM-SEEN).** The 10 arms were selected and retired on reads that touched the history windows. The
arm's own mean net R is therefore seen in every window. The filter differential (kept minus all) has never been read,
and arm-level selection largely cancels in it; the "kept mean > 0" leg does not cancel and is biased upward. So a
confirm pass cannot qualify alone: test_old 2008-02..2009-12 is read once after a confirm pass (section 4, stage T).

## 2. Spec (fixed: no grid, no inner tuning)

### 2.1 Population, labels, features (phase 1, history)

- **Arms:** the sr-v2 `PRENAMED` list: RBR15, Z3D, Z1P, Z9D, Z10D, S1A, S1B, F1A, Z2P, Z5P. RBR15 is replayed with
  RB15's rule (`HISTORY_OF`). An arm whose rule cannot be replayed from Dukascopy 1m bars is dropped **label-blind,
  before any read**, and listed in the run record. If fewer than 5 arms remain, the card does not run (0 K).
- **Candidates:** every candidate the arm's rule (`goldbot/research/arm_rules*.py`, as `scripts/research/rb_history.py`
  replays them) emits on Dukascopy XAUUSD 1m, taken or not.
- **Label:** net R per candidate under W2 bps costs (cost of record, `rb_history._bps_net` convention), the stop's
  overshoot included. y = 1[net R > 0]. Gross diagnostic: the same trade at mid, no cost.
- **Full horizon:** a row trains a fold only if decision + `max_hold_s` and its label exit both lie before the fold's
  first decision minus the 1-day embargo (sr-v2 rule).
- **Features:** `self_retrain.INPUTS` exactly (39 market columns + `side` = 40, `check_feature_cap`). The 11 signed
  columns are centred and multiplied by side as in `self_retrain.design`. Built per candidate by
  `feature_snapshots.decision_features` on Dukascopy minutes, every minute joined with `asof_join` on `available_utc`
  (history minutes: `available_utc` = nominal close, the repo's stored-bar convention). A test asserts
  `available_utc <= decision` for every input row.
- **Known shift:** Dukascopy spread and tick-count columns are not the broker's. Phase 1 decides which **model class**
  goes to phase 2; it never ships phase-1 coefficients to the broker.

### 2.2 The candidates (8, pre-named; each submitted candidate costs 1 K)

All base learners: median imputation and standardisation fitted on the training fold only; fixed hyperparameters, so
no inner tuning and nothing to nest. One model per arm unless stated. Score = predicted P(y = 1).

| Id | Candidate | Fixed spec | Simplicity rank (1 = simplest) |
|---|---|---|---|
| B1 LOGIT | sr-v2 incumbent, per arm | L2 logistic, C = 0.1 (`self_retrain.fit_logit`) | 2 |
| B2 GBT | gradient-boosted trees, per arm | LightGBM: num_leaves 15, max_depth 4, learning_rate 0.03, n_estimators 300, min_child_samples 200, subsample 0.8 (bagging_freq 1), colsample_bytree 0.8, reg_lambda 1.0, deterministic, n_jobs 1, seed 20261125; no early stopping | 5 |
| B3 POOL | one L2 logistic pooled across all arms (gold-only stand-in for the per-asset-class model, 2.4) | C = 0.1, same 40 inputs, no arm dummies (cap) | 1 |
| B4 REGIME | regime-gated logistic, per arm | one C = 0.1 logistic per `vol_tercile` value at decision; a cell with < 100 training rows or one class uses B1's score | 3 |
| B5 KNN | analog / nearest neighbours, per arm | standardised 40 inputs, Euclidean, k = max(50, round(sqrt(n_train))), uniform weights, score = mean y of neighbours | 4 |
| F1 AVG | equal-weight average of B1-B5 scores | no fitted weights | 6 |
| F2 STACK | out-of-fold stacked meta-learner | L2 logistic, C = 1.0, inputs = logit of the 5 base OOF scores (clipped to [0.01, 0.99]); fitted only on base OOF scores of earlier blocks (2.3) | 8 |
| F3 GATE | regime-gated selection | for each `vol_tercile` cell, use the base learner with the highest OOF edge (kept minus all, net R) in that cell over all earlier blocks; ties to the lower simplicity rank | 7 |

**Filter (all 8):** keep a candidate when score >= tau, tau = the 1/3 quantile of that candidate's own scores on its
training rows (base learners: in-sample training scores, as sr-v2; F1-F3: the base OOF rows they were fitted or
selected on).

### 2.3 Walk-forward (purged, embargoed, out-of-fold only)

- **Base OOF:** expanding walk-forward with yearly test blocks 2011, 2012, ..., 2017. Training for block t = 2003-05
  plus 2010..t-1, purged by the full-horizon rule and a 1-day embargo before the block's first decision.
- **Fusion OOF:** F2 and F3 for block t are fitted or selected on base OOF scores of blocks 2011..t-1 only. F1 needs no
  fit. 2011 is therefore a meta-training block only.
- **Judged learn OOS (all 8 on the same rows):** 2012-2017, 6 blocks. Every paired comparison uses exactly these rows.
- **Why not CPCV:** the deployed retrainer is an expanding forward walk; CPCV paths would add reads of the same rows
  without matching deployment. Not used.
- **Confirm models:** bases refit on all learn rows (2003-05 + 2010-17); F2 fitted on all learn base OOF (2011-17);
  F3's cell choices from all learn OOF; tau as 2.2. Frozen and hashed **before** confirm is opened. **No fusion weight,
  selection or threshold sees confirm, test_old or the holdout.**

### 2.4 Asset class (owner rule 2026-11-25)

Analysis is split by `asset_class` (`config/instruments.yaml`). Non-gold replay labels carry a unit bug being fixed
on branch `fix/label-r-quarantine`, so **phase 1 is gold only (`metal`, XAUUSD)**. B3 is therefore the gold pooled
model. A per-asset-class B3 and per-class reads are a separate amendment after that branch merges, with their own K;
nothing non-gold is read under this card.

## 3. Leakage checks (must pass before any label is read; failure = no read, 0 K)

1. Shuffled-label AUC (`feature_snapshots.shuffled_label_auc`) on learn, per base learner: inside [0.47, 0.53].
2. Truncation invariance for all 8 (sr-v2 test): perturbing every label decided or resolved at or after a block's
   start leaves that block's scores and keep set unchanged, F2 and F3 included.
3. OOF-only stacking test: every F2 / F3 training row's base score came from a model whose training ended before that
   row's block.
4. Point in time: `available_utc <= decision` on every feature input; no row inside the holdout.

## 4. Reading rule (written before any data is seen)

**Statistics (sr-v2 definitions, net R, W2 bps costs).** For candidate c on a row set:
- edge_nm(c) = kept mean minus all-candidates mean;
- kept(c) = kept mean;
- gross(c) = kept minus all on the mid label;
- p(c) = max(p[edge_nm <= 0], p[kept <= 0]), intersection-union, each p = (1 + #draws <= 0) / (1 + B), B = 10,000
  stationary day-block draws (mean block 5 days), seed 20261125. A draw with no kept row counts against.
- Paired D(F, B*) = kept(F) minus kept(B*) on the same rows, the same day draws for both sides; p_D as above.
- **Every result is reported per arm and pooled; only the pooled number decides.** Rows are pooled across arms,
  candidate-weighted.

**Stage 0, count (outcome-blind, 0 K).** Count learn OOS candidates and session days. If pooled 2012-17 OOS rows
< 2,000 or OOS session days < 200, the card stops underpowered, books 0 K, and says so.

**Stage L, learn (K = 8 booked when it runs).** Candidate c CONTINUES if all hold on 2012-17 OOF:
edge_nm >= +0.05 R, kept > 0, gross >= 0, and Holm-adjusted p(c) <= 0.10 across the 8.
- **Best base B\*** = the base learner with the highest learn edge_nm among continuing bases; if no base continues,
  among all bases. Recorded before confirm.
- STOP if no candidate continues: H-101 is recorded **null** and the family is retired (no zoo over these inputs on
  this population without new information).

**Stage C, confirm 2018-01..2025-09, read once (no new K).** For each continuing candidate, frozen:
1. **Pass:** edge_nm >= +0.05 R, kept > 0, gross >= 0, Holm p(c) <= 0.10 across the continuing set.
2. **A fusion beats B\*** only if it passes 1, **and** paired D(F, B\*) has Holm-adjusted p_D <= 0.10 across the
   passing fusions, **and** D > 0, **and** the fusion's kept mean net of cost > 0. B\* need not itself pass for the
   test to be read, but if B\* fails, a passing fusion still only wins through this paired test.
3. **Winner:** the fusion with the largest D among fusions meeting 2. Otherwise the **simplest passing candidate**
   (simplicity rank, 2.2). None passing: record a **null**, family retired as at Stage L.

**Stage T, test_old 2008-02..2009-12, read once, winner only** (`windows.allow_test_old_for`, CONFIRM-SEEN rule).
The winner qualifies if edge_nm > 0 and kept > 0 there (direction check, no Holm). Fail = null.

**Phase 2, forward (only with a phase-1 winner).** If the winner is B1, nothing changes: sr-v2 already runs it. If
not, the winner's model class runs inside the weekly retrainer as a challenger spec `sr-v3` on the broker's replay
labels, through the **unchanged sr-v2 gate** (OOS after `retired_day`, both edges >= +0.05 R, gross >= 0, Holm across
the run with the incumbent's pairs, two consecutive weekly passes). The challenger's ACTIVE is a re-activation
candidate only: nothing trades until the existing learning-class route and trading-safety review. Phase-1 coefficients
are never served.

## 5. Trial cost and compute

| Item | K | When |
|---|---|---|
| Stage 0 count | 0 | anytime after signature |
| Stage L, 8 candidates | **8** | booked when run (Q4: 82 left -> 74 after; or from Q1 2027) |
| Stage C, Stage T | 0 (same candidates; reads, not new candidates) | once |
| Phase 2 challenger | +1 per judged (broker, arm) pair a run, <= 10 a run, inside sr-v2's `MAX_JUDGED` slots | Q1 2027 earliest |
| Non-gold amendment (2.4) | own card | after `fix/label-r-quarantine` merges |

Paired fusion tests compare candidates already counted; they get their own Holm family (above) and add no K.

**Compute (estimate, unmeasured):** history replay of 10 arms over 2003-05, 2008-09 and 2010-25 at 1m, plus one
`decision_features` call per candidate, multiprocessing by (arm, year): about 1-3 h wall on the goldbot-ops VM
(rule 2026-11-23: no Mac jobs). Fitting 8 candidates x 7 blocks x 10 arms: minutes (LightGBM 300 trees, kNN brute on
tens of thousands of rows). Bootstrap 10,000 x 8 + 3 paired: under 10 min. Agent cost: one implementer lane, one
quant review, one read by the evaluator.

## 6. Code changes for the implementer (none made by this card)

1. `goldbot/research/model_zoo.py` (new): the 8 candidates (2.2) with constants pinned (`SPEC_VERSION = "zoo-v1"`,
   seeds, hyperparameters, `SIMPLICITY`); OOF walk-forward and the fusion OOF (2.3); stages 0 / L / C / T reusing
   `self_retrain.design`, `fit_logit`, `oos_metrics`, `holm`, `_p_value`; registry rows (family
   `model_zoo_fusion`, one per candidate at Stage L, before reading).
2. `goldbot/research/history_replay.py` (new, factored from `scripts/research/rb_history.py`): replay any pre-named
   arm on Dukascopy 1m per (arm, year), W2 bps labels, `decision_features` per candidate via `asof_join` on
   `available_utc`; parquet cache under `~/tmp` or the VM's data dir (never committed, never published).
3. `scripts/research/model_zoo_history.py` (new): CLI `count | learn | confirm | test_old`, multiprocessing; refuses
   confirm without a Stage L record and the frozen-model hash, and test_old without `allow_test_old_for`; refuses any
   non-`metal` instrument while the gold-only guard is set.
4. `goldbot/research/windows.py`: register `model_zoo_fusion` for the confirm read-once ledger and test_old (as the
   RB-history card does).
5. `tests/research/test_model_zoo.py` (new): constants pinned; 40-input cap; truncation invariance for all 8;
   OOF-only stacking; determinism (same seed, same scores); holdout refusal; gold-only guard; tau from training rows
   only.
6. Phase 2 only, after a non-B1 winner: `goldbot/research/self_retrain.py` challenger class (`sr-v3`), an aux.db
   migration adding `model_class` to `self_retrain_models` (model stored as text, never a pickle: LightGBM
   `model_to_string`; kNN needs its training matrix stored, so a kNN winner needs a storage decision first), a new
   pre-registration amendment for the gate family.

## 7. Flags

- **Research director:** allocate Stage L's 8 K (Q4 or Q1 2027). Not dispatched by this card.
- **Owner-only:** none. No instrument, budget, sizing or live change. Promotion stays behind sr-v2 and the
  learning-class route.

## 8. Quant-reviewer signature

(pending)
