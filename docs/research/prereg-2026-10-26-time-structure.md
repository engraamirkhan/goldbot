# Pre-registration 2026-10-26: time-structure programme (H-70..H-74; arms F1A, F1C, G1A, G1C, WFRI; Q1 rows O-11, O-12)

Research director, 2026-10-26. Programme and evidence: `time-structure-2026-10-26.md`. Frame: `time-structure/README.md`.

What had and had not happened before this file was committed:
- no arm below had a fill;
- no confirmation-half (2018-01-01..2025-09-30) outcome of H-70, H-71 or H-73 had been computed by anyone (slices
  F, G, H and the director never loaded it for these rules);
- the holdout (2025-10-01..2026-09-30) was not read for them.

The git history of this file is the pre-registration proof. **Amended before merge, 2026-10-26**, on the quant review
(APPROVE WITH CONDITIONS 1-5) and the evaluator review (AMEND 6-10), still before any fill or confirmation
computation:
- swap added to the measured cost;
- `ny_pit` and the WFRI entry clock frozen;
- O-11 and O-12 made independent of the arms' results;
- K raised for G's 51 axes;
- override conditions written in;
- the 4h Asia secondary added to O-12;
- A-1 closed;
- `ny_pit` banned from any holdout criterion;
- O-11 moved to the armed stop exit;
- power statements, stop rules and minimum n added.

Any later change needs a dated amendment and a new id.

## 0. Fixed for everything in this file

- **Costs.**
  - *Research prior* (cost of record for registry trials and gate 1): the Dukascopy spread at the bar + $0.37/oz +
    swap −60/0 USD per lot-night (long/short), triple on Wednesday. About $1.07/oz today plus swap (quant audit
    2026-10-26).
  - *Measured cost* (for arms and the cost conditions of O-11/O-12): the **worse** of
    (a) $0.23/oz plus $0.70 on each stop exit, and
    (b) the arm's own signal-time fill cost (spread + slippage + commission + overshoot on stop exits),
    **plus, in both, the broker's swap for every 17:00 New York rollover the position is held through (Wednesday
    x3)**. The swap is read from the account's broker terms (`state/broker_terms_<account>.json`), else the settings
    prior. (b) is read at the 95% upper bound per (session x exit type) cell with at least 30 fills, broker-keyed,
    never pooled across brokers.
  - Both nets are reported on every read.
- **Clocks.** Local exchange clocks via `zoneinfo`, as `goldbot/features/session.py` v2. Trading day = NY date after
  +7 h. No UTC session window in any new arm.
- **Point in time.** A decision on the close of bar i uses bars ≤ i only. Every external input joins through
  `asof_join` on `available_utc`. Levels exist from the bar that confirms them. No centred filter.
- **Common arm filters** (`arms-2026-10-25.md` section 0): tier-1 blackout, no entry 20:45-22:15 UTC, none on Friday
  after 19:00 UTC (WFRI excepted), 0.01 lot, own stop never widened, a candidate outside its stop band skipped and
  logged with its propensity.
- **Statistic.** Mean per event with the day-clustered t (CR1, G − 1 df). Controls as stated per rule.
- **What a read can do.** A rule-only read can say at most **continue**. Promotion still needs gate 1 (a
  model-filtered walk-forward, DSR at the day's K) and the holdout once. **Exploration fills are never promotion or
  confirmation evidence.**
- **Independence of the confirmation reads (quant condition 3).** An arm's net result cannot cancel, defer or bring
  forward O-11 or O-12. Only the arm's *cost measurement* (a fill-cost read, not an outcome) is a precondition.

## 1. K and budget

- **K.** Registry 145 + about **187 effective** programme looks (`time-structure-2026-10-26.md` 7.1, with G charged at
  its 51 axes) = about **332**. With the 5 arm registrations it is about **337**, and gate-1 t is about **4.6**. The
  evaluator adds the 187 at every later read until L-1 records the looks, with an eigenvalue-based K_eff replacing
  the grouping (L-1 acceptance).
- **Arms: 2026Q4 override 69 -> 74, approved 2026-10-26** by the program director and product owner (owner's
  no-quarterly-wait directive; the Vantage precedent). Conditions (quant 5):
  1. this file is on `main` before the first fill of any of the 5 arms;
  2. the `trial_budget_overrides` comment in `config/settings.yaml` cites the directive;
  3. the change is reported to the owner on #34 the same day;
  4. K +5 (one registry row per arm, written before its first fill);
  5. the fills are never used as evidence for promotion or confirmation.
- **Q1 rows O-11 and O-12:** 1 slot each from the 2027Q1 free reserve, by the dated note in
  `preregistration-2027Q1.md`.

## 2. H-70 F-1 Hurst-switched band breakout, 15m

### 2.1 Rule (identical in history and live)

Definitions are those of `docs/research/candidates/time_structure/volatility_bands_diag.py` at commit 5ebd2b83 (KC, BB, DC20) and
`docs/research/candidates/hurst_proxy_check_2026-10-26.py` (tercile), restated:

- **Bands at the close of 15m bar i:**
  - KC = EMA20(close) ± 2.0 x ATR14 (Wilder);
  - BB = SMA20(close) ± 2 x SD20(close);
  - DC20 = highest high / lowest low of bars i−20..i−1.
- **Break event:** close_i outside at least one band while close_{i−1} was inside that band. Side of the break = +1
  above, −1 below. At most one event per 4 bars.
- **Regime:** `hurst_128` = `goldbot.features.survey.rolling_hurst` of 15m log mid returns. Its tercile is the
  percentile rank among bars i−249..i: ≤ 1/3 low, > 2/3 high.
- **Side:**
  - low tercile: trade **with** the break;
  - high tercile: trade **against** it;
  - middle: no trade.
- **Entry:** market right after the close of bar i (history: the open of bar i+1).
- **Exit:** stop = entry ∓ 1.0 x ATR14(15m) at bar i, a fixed price searched on 1m bars (stop first), resting at the
  broker where the host allows it. Time exit at the close of bar i+4. No target.
- **Stop band:** $3.00 ≤ stop ≤ $8.64 (the £6.50 scalp cap at GBPUSD 1.33), else skipped and logged. One position at a
  time.
- **Recorded, not traded separately:**
  - **`ny_pit` = entry within 08:00-13:30 New York** (frozen; slice H's definition);
  - `htf4h_kc_pos` = (close − EMA20)/(2 ATR14) of the last closed 4h bar, signed by side.

### 2.2 Arms (host `icm-probe`, scalp pot; `config/exploration_arms.yaml`, frozen hashes)

- **F1A:** the rule above.
- **F1C** (control, never graduates): fires at the same decision times as every F1A candidate (taken or skipped).
  Side from the frozen hash with arm id `F1C`; same exits and stop band. On a netting host it shifts one 15m bar, and
  the shift is recorded.
- **Power.** The discovery effect (about +0.087 R at a per-trade SD of about 1 R) needs **about 1,000 trades alone**
  and **about 2,000 pairs** for F1A − F1C at 80% power. H-61's 300-trade read has about 35-40% power: a miss there is
  not a refutation.
- **Stops:**
  - H-61's scalp stop: running net ≤ −30 R within the first 300;
  - **futility** at 300 and at 1,000: the one-sided 95% upper bound of F1A's mean net R at measured cost < +0.03 R.
    The arm stops, and F1C stops with it.
- **Graduation:** H-61's rule at 300, **and** F1A − F1C net R paired by decision time with a 95% CI > 0. The decisive
  forward read is at 1,000 (alone) or 2,000 (paired) if no stop fires first.
- **Signal-time cost read** (a fill-cost measurement, not an outcome): at ≥ 200 admissible F1A fills.

### 2.3 O-11 confirmation read (2027Q1, 1 slot)

- **Data:** 2018-01-01..2025-09-30, data-v1 mid. **Statistic: the armed exit** (evaluator condition 10): R per event
  with the 1.0 x ATR15 stop on 1m bars (stop first) and the time exit at bar i+4, in R = the stop distance. The
  discovery's no-stop 4-bar return is reported as a secondary.
- **Runs only if:** F1A's signal-time cost read exists, and L-2 is merged, by 2027-02-26. Else Q2. The arm's net
  result plays no part (section 0).
- **continue** if all hold:
  1. gross mean ≥ +0.05 R with day-clustered t ≥ 3.0;
  2. both arms positive (low-tercile with-break mean > 0 and high-tercile fade mean > 0);
  3. gross positive in ≥ 5 of the 8 calendar years 2018-2025;
  4. net at the measured cost > 0 on the events whose stop lies in $3.00-8.64, **with at least 1,000 such events**
     (fewer: inconclusive).
- **stop** if the gross mean ≤ +0.03 R, or t < 2.0, or either arm ≤ 0.
- **inconclusive** otherwise.
- **Reported, not in the rule:**
  - net at the research prior;
  - the `ny_pit` stratum;
  - the `htf4h_kc_pos` > 1 split;
  - counts per year.
- **Holdout ban (evaluator condition 9):** the `ny_pit` stratum, or any other subset chosen after this read, may not
  define or enter any holdout criterion of H-70. A holdout scoring, if ever reached, reads the full rule as frozen here.
- **What continue unlocks:** F-1 as a primary for H-62 v2 and one model-research row (gate-1 path). Never promotion by
  itself.

## 3. H-71 G-1 Asia-hours break of a prior-period level, 1h

### 3.1 Rule

Definitions as `docs/research/candidates/time_structure/key_levels_discovery.py` at commit 371e7b9a, restated:

- **Level set at the start of trading day D** (17:00 New York): PDH, PDL, PDC of D−1; the most recent completed Asia,
  London and New York session high and low; previous-week high/low (fixed Friday 17:00 New York); previous-month
  high/low; every $10, $50 and $100 round number within 3 ATR14(1h) of the 17:00 price.
- **Window:** the touch bar opens between 17:00 New York and 08:00 London (local clocks).
- **Entry:** the first 1h bar of day D in the window whose range touches a level and whose close is ≥ 0.1 x ATR14(1h)
  beyond it. Side = the break direction. Market entry right after that close. At most one trade per trading day.
- **Exit:** stop = level − side x 1.0 x ATR14(1h) (fixed price, 1m search, stop first); target 2R; time exit at 32
  bars.
- **Why fixed 2R:** a 0.01 lot cannot take G's partial + trail. Note (evaluator condition 9): **G's 8/8 years belongs
  to the partial + trail exit; the armed fixed-2R form has the control CI [+0.017, +0.105], and its per-year count was
  not tabulated.**
- **Stop band:** $3.00 ≤ stop ≤ $22.60 (the £17 intraday per-trade cap in `accounts.yaml`), else skipped and logged.
  About half are skipped at today's ATR1h.
- **Recorded:** level class, range used vs the 20-day forecast (G-4, logged only), session of the touch. The swap for
  any rollover held is in the measured cost.

### 3.2 Arms (host `icm-demo`, intraday pot)

- **G1A:** the rule above.
- **G1C** (control, never graduates): at 17:00 New York each day, draw a level class uniformly from the day's real
  levels and a sign from the frozen hash (arm id `G1C`). Control level = 17:00 price + sign x |17:00 price − that
  level|. Same window, entry, exit and band.
- **Power.** The effect over control (+0.067 R at a per-trade SD of about 1.3 R) needs about 2,300 trades. At 0.3-0.5
  a day the arm is **a cost, parity and safety measurement**, not an edge read, for years.
- **Stops:**
  - H-61's intraday stop: ≤ −15 R within the first 100;
  - futility at 300 as F1A (upper bound < +0.03 R). G1C stops with G1A.
- **Signal-time cost read:** at ≥ 100 admissible G1A fills.

### 3.3 O-12 confirmation read (2027Q1, 1 slot, conditional)

- **Data:** 2018-01-01..2025-09-30. History form of the rule, with the control computed with 6 random levels per day
  as in the discovery.
- **Runs only if:** G1A's signal-time cost read exists by 2027-03-12. Else Q2. The arm's net result plays no part.
- **continue** if all hold:
  1. real minus control gross > 0 with a day-block 95% CI lower bound > 0;
  2. real gross day-clustered t ≥ 3.0;
  3. real above control in ≥ 6 of 8 years;
  4. net at the measured cost > 0 **on at least 1,000 events** (fewer: inconclusive).
- **stop** if real minus control ≤ 0, or net at the measured cost ≤ 0 (n ≥ 1,000).
- **inconclusive** otherwise.
- **Pre-specified secondary (evaluator condition 7), not in the rule:** the same rule on **4h** bars (stop 1.0 x
  ATR14(4h), fixed 2R, 32 bars, Asia-hours touches). Discovery: +0.059 over control [+0.011, +0.106], net at the
  research prior +0.043. Reported with the same four statistics. A secondary "continue" earns only its own
  pre-registration; it does not change O-12's verdict.

## 4. H-72 Friday long (forward-only; the 2018+ half was seen by slice B)

- **Rule (frozen):** long at the close of the first 1h bar ending at or after **19:00 New York on Thursday**. This
  avoids the 18:00-18:30 reopen spread; B measured Thursday 17:00 -> Friday 17:00, so the window differs by the first
  two hours.
- Exit at market at 16:30 New York on Friday. Stop 1.0 x ATR14(4h) at entry, skipped if > $53.2 (the £40 swing cap).
  Not on a CME metals holiday or early close (`config/holidays_cme_metals.yaml`) on either day. No target. No rollover
  is crossed, so no swap.
- **Arm:** WFRI, `icm-demo` swing pot. It shares the one swing slot under ADR 0043; a blocked candidate is logged.
- **Power.** 14 bp against a Friday SD of about 100 bp needs **about 170 Fridays** (about 3.3 years).
  - **At 26 Fridays: safety check only.** Stop if the mean ≤ −20 bp per Friday or the cumulative net ≤ −10 R.
  - **At 170 Fridays: the only read** (1 slot then). **continue** if mean gross ≥ +6 bp with t ≥ 2.0 and net at the
    measured cost > 0; **stop** if the mean < 0; else **inconclusive**.

## 5. H-73 time-structure range features (0 trials; K_eff +1 as one group)

- **Columns:** at most 12, fixed by the opportunity-researcher's spec before any H-16 refresh or H-62 v2 fit reads
  them. Candidates:
  - `fomc_day`, `nfp_day` (from `calendar-v1` via `asof_join` on `available_utc`);
  - `ny_pit_window` (08:00-13:30 New York);
  - `tick_z_how`;
  - `wav_logE_j3_1d`, `wav_logE_j5_1h`, `wav_logE_j7_1h` (causal MODWT, prefix-invariance test);
  - `rv30_rv90`;
  - `range_used_vs_forecast`;
  - `displacement_bar`;
  - `days_to_opt_expiry` (only after the date table is frozen).
- **Drop rule** (discovery half only): a column whose incremental R² over HAR(1,5,22) for the next-day range is
  < +0.005, or that fold-internal selection never keeps, leaves the group.
- **Use:** H-16 refreshes, H-54's sealed inputs (amendment needed), H-62 v2. Never an entry rule.

## 6. H-74 slow-trend swing group (0 trials here)

The 30-day-extreme first touch (C-1, from 1m, short), the F-3 4h/1d band state and the 30/60/90-day windows form one
group, at most 10 columns, written into SW-0's Q2 configuration by the macro-researcher. No arm, no slot in Q1. C-1's
standalone confirmation is impossible: about 115 events in 2018-2025-09 is below the 150 retire floor.

## 7. Held, closed and declined (recorded so they are not re-proposed)

- **Held for reconciliation (no arm, no slot):**
  - H-75 D-1 (Asia trend persistence 1h; H's rebuild null);
  - H-77 D-2 (CMF20 1h; redundancy with the MA regime unchecked).
- **Closed:**
  - **H-76 A-1, failed Asia-range fade (evaluator condition 8):** the sign flipped on an independent rebuild, slice A
    saw the holdout, and at 48 events a year it cannot be read. This version, tested this way, did not beat random
    after cost.
  - Every item whose "died on" is signal, count or seen in `time-structure-2026-10-26.md` section 4, in the form
    tested.
- **Declined:**
  - Local-clock twins S0L/S4L/S5AL (quant ruling: one trial each, paired net-R vs UTC, 95% CI > 0). New arms are local
    by construction, and README 6.9 settles the clock question. Deferred to the Q2 draft.
  - E-1 order-block arm: controls explain its gross.
