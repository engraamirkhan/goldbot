# Pre-registration: Q1 2027 research trials (DRAFT: freeze before 2027-01-01)

Written 2026-10-10, before any of these configurations has been run on data. Source of the ranking:
`docs/research/hypotheses.md` (section A), `state-of-the-art.md`, `indicator-survey.md`. Each trial below is written
into the registry as status `preregistered` (with this file's commit id) before it runs; its result is read only by the
rule stated here. Changing a rule after seeing data turns the trial into an exploratory one that cannot justify
promotion.

## Fixed for every trial
- Data: Dukascopy 1m XAUUSD 2010-01 .. 2025-09 (release `data-v1`); holdout 2025-10-01 .. 2026-09-30 untouched;
  macro release `macro-v1` (first releases, available next US business day 23:00 UTC) only where stated.
- Costs: the measured canonical cost table (release `costs-v1`) if it exists at run time, otherwise the settings priors;
  the report states which. Swap charged per rollover held (`triple_barrier(swap=...)`).
- Evaluation: purged walk-forward per `WINDOWS` (4h: 48/6/6 months, purge 10 d, embargo 4 d; 1d: 60/12/12 expanding),
  cross-fitted calibration and threshold, one position per agent, lookahead check must report 0 columns.
- Gates (unchanged, `research/gates.py`): >= 1,500 candidates, >= 60 per complete test fold, positive expectancy in
  >= 3 calendar years including one of 2021/2022, DSR >= 0.95 on >= 200 model-filtered trades with the real trial count.
- Rule-only gates are reported alongside (informational).
- DSR trial count: 20 (Q4 2026) + Q1 trials run so far, + K_eff (clusters screened) for any trial of an H-02 survivor.

## Order and budget (13 planned of 20; 7 reserve)

| # | ID | Trial | Depends on | Trials |
|---|---|---|---|---|
| 1 | H-13 | Volatility-forecast cost filter, applied as a reporting overlay to every trial below (net R with / without) | none | 0 |
| 2 | H-02 | Feature discovery (one trial) | tooling `research_pass.py --discover` | 1 |
| 3 | H-01 | Slow TSMOM | owner ruling A | 1 |
| 4 | H-06 | Macro-conditioned slow TSMOM | H-01 run; macro release | 1 |
| 5 | H-04 | Asia-session drift | none | 1 |
| 6-9 | H-03, H-05, H-07, H-08, H-10 | Each only if its features survive H-02 (stability >= 0.6) | H-02 | 1 each, at most 4 |
| 10 | tsmom 4h, max_bars 12, with swap | The trial refused by the budget guard on 2026-10-09, kept for continuity | none | 1 |
| - | reserve | follow-ups decided by these rules only | | 7 |

## Trials and reading rules

### H-02 Feature discovery
- Config: label = 4h triple barrier, target 3.0 ATR / stop 1.5 ATR / 12 bars (tsmom 4h defaults), candidates = every 4h
  bar (no rule), side from the sign of the 20-day vol-scaled return; features = every registered family (incl.
  trader-toolkit, survey and macro), grouped into clusters by family for K_eff.
- Method: stability selection inside each training fold only (50 subsamples x 50%), top-40 by gain; permutation
  importance on an inner split as a tie-break.
- Reading rule (decided now): a feature cluster **survives** if its selection stability >= 0.6 across folds AND the
  walk-forward on the selected set has OOF AUC >= 0.53 with the lower 95% bound > 0.50. Survivors earn their own
  trial (rows 6-9); if no cluster survives, rows 6-9 are cancelled and their budget returns to reserve.

### H-01 Slow TSMOM
- Config: `specialist=tsmom`, signal from 1d bars (20/60/120-day vol-scaled returns, 60-day vol), execution and
  labels on 4h bars, target 3.0 ATR(1d) / stop 1.5 ATR(1d), time barrier 20 trading days, long and short, swap
  charged.
- Implemented as the tsmom preset `slow` (`goldbot/specialists/time_series_momentum.py`; tsmom's default
  configuration and labels unchanged, `tests/test_tsmom_slow.py`). Signal on the feature-day bars (d1 context), read on
  the first 4h bar whose close is at or after the settlement, entry at that bar's close (the open of the first 4h bar
  after the signal is visible), at most one signal per daily bar; min_score 0.5 (tsmom's daily default, not set by
  the hypothesis); target/stop from ATR(14) of the daily bars, frozen at entry (R = one 1.5 x ATR(1d) stop); time
  barrier 124 4h bars (20 trading days: 31 four-hour bars a trading week); one position at a time; swap per rollover
  in the net labels. Walk-forward: the 4h window (train 48 / test 6 / step 6 months, embargo 4 days) with purge
  raised from 10 to 31 days, because the label lives up to 28 calendar days plus holidays (M14). Agent id
  `tsmom-g0-cf39165431`.
- Run (Actions -> research -> Run workflow), exactly these inputs:

  | input | value |
  |---|---|
  | specialist | `tsmom` |
  | from_year | `2010` |
  | to_year | `2026` |
  | rationale | `H-01 slow TSMOM (preregistration-2027Q1.md, commit <freeze commit>)` |
  | variants | `["slow"]` |
  | score_holdout | false |
  | skip_screen | false |
  | pooled | (empty) |
  | macro | false |

  `["slow"]` expands to `[{"timeframe": "4h", "signal_tf": "1d", "atr_tf": "1d", "lb_fast_h": 480, "lb_mid_h": 1440,
  "lb_slow_h": 2880, "vol_window_h": 1440, "min_score": 0.5, "schedule_h": 24, "target_atr": 3.0, "stop_atr": 1.5,
  "max_bars": 124}]` (either spelling is the same configuration and agent id). One trial.
- Event count on data-v1 (2026-10-10, counted without labels or any outcome; research window, holdout excluded):
  2,631 daily signals (1,760 long, 871 short); one position at a time, if every trade ran to the 20-day time
  barrier, 165 events. The true count lies between, below the ~450 estimated for the 10-day daily option, and
  probably below the 400 floor proposed in ruling A: **ruling A must be decided on this count**.
- Reading rule: **continue** if the P4 screen passes (gross mean R > 0, t >= 2.0, on the event floor ruled in A) AND
  net mean R > 0 with t >= 1.65 AND positive net years >= 3 incl. 2021 or 2022. **Stop slow trend in gold** if net
  mean R <= 0 (the literature's best case then does not survive our costs).
- Expected: gross +0.05..0.10 R; net depends on the short side because longs pay swap.

### H-06 Macro-conditioned slow TSMOM
- Config: H-01 with candidates kept only when the 20-day change of DFII10 and of the broad dollar both agree with the
  trade side (falling real yield / dollar for longs). Macro release required.
- Reading rule: **continue** only if net mean R exceeds H-01's by >= 0.03 R AND 2022-2024 net is positive (the period
  the yield link broke). Otherwise the macro gate is retired.

### H-04 Asia-session drift
- Config: long at 00:00 UTC, exit 07:00 UTC (flat before London), stop 1.5 ATR(1h), every trading day, Data before
  2025-10 only (press figures for H1 2026 fall in the holdout).
- Reading rule: **continue** if gross t >= 2.0 on >= 1,000 events AND net mean R > 0. Expected thin: <= 0.03% per
  session against ~0.01% round trip, so a pass needs the measured cost table.

### tsmom 4h with swap (continuity)
- Config: `variants=[{"timeframe": "4h", "max_bars": 12}]`, as refused on 2026-10-09.
- Reading rule: if net mean R <= 0, the 4h tsmom horizon is retired (H-01 supersedes it).

## Needs the owner (before freezing)
- **A. Event floor for daily-signal families.** The P4 screen needs >= 1,000 events; slow TSMOM produces ~450 in
  15.75 years (one position at a time). Options: keep 1,000 (H-01 cannot pass by construction), or a floor of 400
  for families whose signal is daily, with the t >= 2.0 requirement unchanged. This changes a design gate, so it is
  yours to decide.
- **B. Trial budget.** 13 planned + 7 reserve fits the default 20; raising it is yours to decide.
- **C. Paid economic-calendar consensus feed** (H-11 needs surprises); otherwise H-11 stays blocked.
- **D. Other instruments** (large-tick futures where trend still works) are out of scope unless you decide otherwise.

## Freeze checklist (by 2027-01-01)
- [ ] Owner rulings A-D recorded here
- [ ] `costs-v1` measured table published from the VPS (or the priors accepted and stated)
- [ ] `macro-v1` release present
- [ ] `research_pass.py --discover` merged and tested
- [ ] each trial's `preregistered` registry row written with this file's commit id
