# Pre-registration 2026-10-26: time-structure programme (H-70..H-74; arms F1A, F1C, G1A, G1C, WFRI; Q1 rows O-11, O-12)

Research director, 2026-10-26. Programme and evidence: `time-structure-2026-10-26.md`. Frame: `time-structure/README.md`.

Before this file was committed:
- no arm below had a fill;
- no confirmation-half (2018-01-01..2025-09-30) outcome of H-70, H-71 or H-73 had been computed by anyone (slices
  F, G, H and the director never loaded it for these rules);
- the holdout (2025-10-01..2026-09-30) was not read for them.

The git history of this file is the pre-registration proof. Changing a rule after the first fill or the first
confirmation computation needs a dated amendment and a new id.

## 0. Fixed for everything in this file

- **Costs.**
  - *Research prior* (cost of record for registry trials and gate 1): the Dukascopy spread at the bar + $0.37/oz + swap
    −60/0 USD per lot-night (long/short). About $1.07/oz today (quant audit 2026-10-26).
  - *Measured cost* (for arms and the cost condition of O-11/O-12): the **worse** of
    (a) $0.23/oz plus $0.70 on each stop exit, and
    (b) the arm's own signal-time fill cost (spread + slippage + commission + overshoot on stop exits). (b) is read at
    the 95% upper bound per (session x exit type) cell with at least 30 fills, broker-keyed, never pooled across
    brokers.
  - Both nets are reported on every read.
- **Clocks.** Local exchange clocks via `zoneinfo`, as `goldbot/features/session.py` v2. Trading day = NY date after
  +7 h. No UTC session window in any new arm.
- **Point in time.** A decision on the close of bar i uses bars ≤ i only. Every external input joins through
  `asof_join` on `available_utc`. Levels exist from the bar that confirms them. No centred filter.
- **Common arm filters** (`arms-2026-10-25.md` section 0): tier-1 blackout, no entry 20:45-22:15 UTC, none on Friday
  after 19:00 UTC (WFRI excepted, section 3.5), 0.01 lot, own stop never widened, a candidate outside its stop band
  skipped and logged with its propensity.
- **Statistic.** Mean per event with the day-clustered t (CR1, G − 1 df). Controls as stated per rule.
- **What a read can do.** A rule-only read can say at most **continue**. Promotion still needs gate 1 (a
  model-filtered walk-forward, DSR at the day's K) and the holdout once. Exploration fills are never promotion
  evidence.

## 1. K and budget

- Registry K: 145 (2026Q4 69/69).
- Programme looks: about 2,460 raw, charged **161 effective** (`time-structure-2026-10-26.md` 7.1). The evaluator adds
  them to `n_trials_effective` at every later read until L-1 records them. Gate-1 t is about 4.5.
- **Arms:** 5 registrations (F1A, F1C, G1A, G1C, WFRI). Precedent (the Vantage twins) charges one 2026Q4 slot each, so
  they need the 2026Q4 override **69 -> 74** (program-director decision under the owner's standing directive, reported
  to the owner). If refused, they start 2027-01-01 against the Q1 free reserve, and O-11/O-12 move to Q2.
- **Q1 rows O-11 and O-12:** 1 slot each from the 2027Q1 free reserve, by the dated note in
  `preregistration-2027Q1.md`.

## 2. H-70 F-1 Hurst-switched band breakout, 15m

### 2.1 Rule (identical in history and live)

Definitions are those of `scripts/research/volatility_bands_diag.py` at commit 5ebd2b83 (KC, BB, DC20) and
`docs/research/candidates/hurst_proxy_check_2026-10-26.py` (tercile), restated here:

- **Bands at the close of 15m bar i:**
  - KC = EMA20(close) ± 2.0 x ATR14 (Wilder);
  - BB = SMA20(close) ± 2 x SD20(close);
  - DC20 = highest high / lowest low of bars i−20..i−1.
- **Break event:** close_i outside at least one band while close_{i−1} was inside that band. Side of the break =
  +1 above, −1 below. At most one event per 4 bars (the first).
- **Regime:** `hurst_128` = `goldbot.features.survey.rolling_hurst` of 15m log mid returns. Its tercile is the
  percentile rank of hurst_128 among bars i−249..i: ≤ 1/3 low, > 2/3 high, else middle.
- **Side:**
  - low tercile: trade **with** the break;
  - high tercile: trade **against** it;
  - middle: no trade.
- **Entry:** market right after the close of bar i (history: the open of bar i+1).
- **Exit:** time exit at the close of bar i+4. Stop = entry ∓ 1.0 x ATR14(15m) at bar i, a fixed price searched on 1m
  bars (stop first), sent as a resting broker stop where the host allows it (S0R finding). No target.
- **Stop band:** $3.00 ≤ stop ≤ $8.64 (the £6.50 scalp cap at 0.01 lot and GBPUSD 1.33). Outside it the candidate is
  skipped and logged. One position at a time.
- **Recorded, not traded separately:**
  - `ny_pit` = entry within 08:00-13:30 New York;
  - `htf4h_kc_pos` = (close − EMA20)/(2 ATR14) of the last closed 4h bar, signed by side (the F-2 flag).

### 2.2 Arms (2026Q4, host `icm-probe`, scalp pot)

- **F1A:** the rule above.
- **F1C** (control, never graduates): fires at **the same decision times as every F1A candidate**, taken or skipped.
  Side from the frozen hash with arm id `F1C` (`arms-2026-10-25.md` section 0); same exits and stop band. On a
  netting host F1C shifts by one 15m bar, and the shift is recorded.
- **Signal-time cost read:** at ≥ 200 admissible F1A fills (expected about 2026-12, at 3-6 a day). 0 slots.
- **Graduation:** H-61's rule (one read at the 300th admissible trade, P(mean > 0) > 1 − 0.05/m), **and** F1A minus
  F1C net R, paired by decision time, with a 95% CI > 0.

### 2.3 O-11 confirmation read (2027Q1, 1 slot)

- **Data:** 2018-01-01..2025-09-30, data-v1 mid. **Statistic as discovered:** the signed 4-bar return from the next
  open in ATR14(15m) of bar i, with no stop.
- **Runs only if:** F1A's signal-time cost read exists, and L-2 (confirmation read type) is merged, by 2027-02-26.
  Else Q2.
- **continue** if all hold:
  1. gross mean ≥ +0.05 ATR15 with day-clustered t ≥ 3.0;
  2. both arms positive (low-tercile with-break mean > 0 and high-tercile fade mean > 0);
  3. gross positive in ≥ 5 of the 8 calendar years 2018-2025;
  4. net at the measured cost (section 0) > 0 on the events whose stop at bar i would lie in $3.00-8.64 (the placeable
     band), mean and t reported.
- **stop** if the gross mean ≤ +0.03 ATR15, or t < 2.0, or either arm ≤ 0.
- **inconclusive** otherwise.
- **Reported, not in the rule:**
  - net at the research prior;
  - the `ny_pit` stratum (pre-specified secondary: continue there needs the same conditions 1-4);
  - `htf4h_kc_pos` > 1 split;
  - counts per year.
- **What continue unlocks:** F-1 as a primary for H-62 v2 and one model-research row (gate-1 path). It never unlocks
  promotion by itself.

## 3. H-71 G-1 Asia-hours break of a prior-period level, 1h

### 3.1 Rule

Definitions as `scripts/research/key_levels_discovery.py` at commit 371e7b9a, restated:

- **Level set at the start of trading day D** (17:00 New York): PDH, PDL, PDC of D−1; the most recent completed Asia,
  London and New York session high and low (`session.py` v2 windows); previous-week high/low (fixed Friday 17:00 New
  York); previous-month high/low; every $10, $50 and $100 round number within 3 ATR14(1h) of the 17:00 price.
- **Window:** the touch bar opens between 17:00 New York and 08:00 London (local clocks).
- **Entry:** the first 1h bar of day D in the window whose range touches a level and whose close is ≥ 0.1 x ATR14(1h)
  beyond it. Side = break direction. Market entry right after that close. At most one trade per trading day.
- **Exit:** stop = level − side x 1.0 x ATR14(1h) (a fixed price, 1m search, stop first); target 2R; time exit at 32
  bars. **Fixed 2R, not the partial + trail:** a 0.01 lot cannot be split. The fixed-2R cell is the one with the
  control CI above 0 (+0.067 [+0.017, +0.105]).
- **Stop band:** stop ≤ $59.8 (the £45 intraday cap) and ≥ $3.00, else skipped and logged.
- **Recorded:** level class, range used vs the 20-day forecast (the G-4 veto, logged only), session of the touch.

### 3.2 Arms (2026Q4, host `icm-demo`, intraday pot)

- **G1A:** the rule above.
- **G1C** (control, never graduates): at 17:00 New York each day, draw a level class uniformly from the day's real
  levels and a sign from the frozen hash (arm id `G1C`). The control level = 17:00 price + sign x |17:00 price −
  that level|. Then the same window, entry, exit and band rules.
- **Signal-time cost read:** at ≥ 100 admissible G1A fills.
- **Graduation:** H-61's rule **and** G1A minus G1C net R (same days, unpaired, day-block bootstrap) 95% CI > 0.

### 3.3 O-12 confirmation read (2027Q1, 1 slot, conditional)

- **Data:** 2018-01-01..2025-09-30. History form of the rule, with the G1C control computed with 6 random levels per
  day as in the discovery.
- **Runs only if:** G1A's signal-time cost read exists by 2027-03-12. Else Q2.
- **continue** if all hold:
  1. real minus control gross > 0 with a day-block 95% CI lower bound > 0;
  2. real gross day-clustered t ≥ 3.0;
  3. real above control in ≥ 6 of 8 years;
  4. net at the measured cost > 0.
- **stop** if real minus control ≤ 0, or net at the measured cost ≤ 0.
- **inconclusive** otherwise.

## 4. H-72 Friday long (forward-only; the 2018+ half was seen by slice B)

- **Rule:** long at the close of the first 1h bar ending at or after 19:00 New York on Thursday. Exit at market at
  16:30 New York on Friday. Stop 1.0 x ATR14(4h) at entry, skipped if > $53.2 (the £40 swing cap). Not on a CME
  metals holiday or early close (`config/holidays_cme_metals.yaml`) on either day. No target. No rollover is crossed,
  so no swap.
- **Arm:** WFRI, `icm-demo` swing pot. It shares the one swing slot under ADR 0043, and a blocked candidate is logged.
- **Read (forward, once, at 26 admissible Fridays; 1 slot in the quarter it falls, expected 2027Q2):**
  - **continue** if mean gross ≥ +6 bp per Friday with t > 1.3, and net at the measured cost > 0;
  - **stop** if the mean < 0;
  - else **inconclusive**.
  - Continue earns a pre-registered longer forward read, never promotion.

## 5. H-73 time-structure range features (0 trials; K_eff +1 as one group)

- **Columns:** at most 12. The final list is fixed by the opportunity-researcher's spec before any H-16 refresh or
  H-62 v2 fit reads them. Candidates:
  - `fomc_day`, `nfp_day` (from `calendar-v1` via `asof_join` on `available_utc`);
  - `ny_pit_window`;
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
own confirmation read is impossible as a standalone: about 115 events in 2018-2025-09 is below the 150 retire floor.

## 7. Held, closed and declined (recorded so they are not re-proposed)

- **Held for reconciliation (no arm, no slot):**
  - H-75 D-1 (Asia trend persistence 1h; H's rebuild null);
  - H-76 A-1 (failed Asia-range fade; H's rebuild flipped sign; holdout seen by A);
  - H-77 D-2 (CMF20 1h; redundancy with the MA regime unchecked).
  Each needs a reconciliation note and then a new pre-registration.
- **Declined:**
  - **Local-clock twins S0L/S4L/S5AL** (quant ruling: one trial each, read as paired net-R vs UTC on the same dates,
    95% CI > 0). New arms use local clocks by construction, and README 6.9 settles which clock activity follows; 3
    slots for a scheduling answer is poor value. Deferred to the Q2 draft.
  - **E-1 order-block arm:** controls explain its gross.
- **Closed at 0 trials:** every item whose "died on" is signal, count or seen in `time-structure-2026-10-26.md`
  section 4, in the form tested.
