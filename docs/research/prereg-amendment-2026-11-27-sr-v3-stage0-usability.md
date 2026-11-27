# Pre-registration amendment: Stage 0 usability is per column set, not per P30 (`sr-v3` rev 8 -> rev 9)

Amends `docs/research/prereg-2026-11-26-sr-v3.md` (H-103) rev 8, sections 2.3, 3.6 and 5.1, and the trainer of PR #488
(ADR 0108 decisions 2 and 4). Written by the research director on 2026-11-27. **Label-blind:** the only inputs are the
served tiers file (`raw/tiers/tiers_served_r11-v2.1.csv`, sha256 ff653ea7..., the r11-v2.1 grades), the registry's
declared column lookbacks and the Stage 0 event counts (outcome-blind). No label value, R, gross, net or Stage 0/L
statistic of sr-v3 exists; the ledger holds no `sr-v3:<unit>` read-once row and no `consumption` row with procedure sr-v3. If either holds an sr-v3 row dated before
this amendment's merge commit, the amendment is void and the fold question goes back to the quant. K 0.
Quant-reviewer signs (required). Evaluator countersigns item 4 (read population).

## 1. What failed

Stage 0 (PR #488 trainer on the data team's training sets, 2026-11-27) returned NOT STARTED (data) for every unit:
gold has 0 usable rows on fold 2010-11, so `decide_unit` stops before the ladder.

Cause, re-derived by the research director from the tiers file and the code path (method in 5):
- The training-set skeleton is "tier-usable under P30" (`sr_v3_train` docstring, item 1). `dense_labels.input_lookback(tf, P30)`
  is 128.8 days at 1m-2h and 189 days at 4h. It is set by the P30 tail (`d1_slope_ema20` through the 1d context, and
  the 810-bar `atr_ratio_14_100`). A 129-day strict span over 2010-13 always meets a B or C month, so 0 rows survive.
- Core-6 and core-12 read far less history: `input_lookback` = 11 h (1m), 2.3 d (5m), 7.0 d (15m), 14.0 d (30m),
  28.1 d (1h), 56.1 d (2h), 112.2 d (4h). The strict (non-close-only) part is shorter still: `dist_vwap48_atr`
  (86 bars) for core-6, `adx14` (125 bars) for core-12. The rest are close-only under R-a and accept B months.
- The card already makes usability a function of the column set: 2.3, "A unit with no usable graded rows under ADR 0104
  §4 (**for the column set at that level**) is NOT STARTED (data)"; ADR 0104 §4, "a row's usability is the
  conjunction over its requested columns"; rev 8's own counts (3.1) are core-12 counts and show gold 2010-17 n_eff
  5,232 at 1m. The P30 mask on the ladder is a trainer deviation from the card, not a property of gold data.

## 2. Decision

**D1. Usability follows the columns that are read.** For every ladder prefix, body and learner, a row is usable iff
`dense_labels.attach_tiers(rows, tiers, columns=<that column set>)` says so, under R-a unchanged:
- **Ladder (2.3).** The n_eff charged for prefix k is counted on rows usable under prefix k's columns. The level is the
  longest prefix that meets the gate on its own rows. Rows can only shrink as k grows; the gate is monotone in k. The same per-prefix rule sets the served body (served-order prefixes).
- **Served body and the judged learner (L-cw).** Train, impute (medians of the smallest training fold (2010-11, or 2010-13 under (b)) rows usable under the
  served columns) and score on rows usable under the served columns. The judged read population is the set of rows usable under the served body's columns, the T2 head, and the chosen body's members (gold rows only for the gold body). Gates 1-14, the baselines B0-B8, the control twin, the S split and N22 are all computed on that set, never on P30 rows. Gate 10 counts learn OOF 2012-17 per fold. Gates 11-12 count confirm rows usable under the same columns, after the C6 edge drop. Rows dropped by the mask are reported per year.
- **P30-wide learners (HB, D-40/G-cw, AVG).** Keep the P30 mask. Their folds with no P30-usable training row are skipped
  and listed (ADR 0108 decision 4, unchanged). Their P30 medians come from their own smallest fold with usable rows.
  They stay unjudged and sealed (zoo-v2), so they add 0 K.
- **Block A/B tests.** Both arms of a block test use the rows usable under core-6 + admitted blocks + X.
- **Freeze.** The served column list, the tiers sha256 and the `COLUMN_LOOKBACKS` hash are recorded in the read-once ref before each Stage L/C read. A monthly regrade or a lookback change never alters a read that is already booked.
- **Pooled body.** The same per-prefix rule for every member, on the pooled level order.
- **Row sets for comparisons and priors (quant C1).** Every read statistic of the judged learner, its baselines B0-B8, the twin, the fold cost priors (p_stop, night share) and gates 10-12 are computed on one population, the rows usable under the served columns. A comparison between the judged learner and a P30-wide learner (paired D, AVG inputs) is computed only on the intersection of their usable rows, and that row count is reported beside it. Every ladder prefix and every candidate served prefix is charged in full, with all its parameters, on its own usable rows; the charges are never computed incrementally on one row set.

**D2. Folds unchanged.** Smallest fold 2010-11, first OOF 2012; remedy (b) (fold 2010-13, first OOF 2014) only for a
unit still below L3 after (a). Code fix: `decide_unit` must try (b) when the gold body has no usable row on (a), and
return NOT STARTED (data) only when gold has no usable row on both folds.

**D3. Gap per quant ruling on #488 (2026-11-27, quant C2):** gap = max(1 d, max H over the unit's tfs) for every unit and learner (1d+1w: 20 d). Under expanding forward folds with a full-horizon purge, an input lookback carries no information from after the cut, so it is not in the gap.

**D4. Disclosure.** The Stage 0 report lists, per unit, the C and B months excluded in 2010-17 and the realised-vol
tercile mix of the excluded vs kept months (`confirm_disclosure` method, applied to learn), before Stage L.

## 3. Options weighed and refused

- **(a) Move the first fold to 2014-15 (OOF 2016-17).** Not needed under D1. It cuts the scored window from 6 OOF years
  to 2, so Stage L n_eff falls to about a third. The card's power statement (gold 1h powered at k <= 0.5, 2h at
  k <= 0.2, 5.1) would no longer hold. It would also change `FIRST_OOF` after the tier map was seen. K is unchanged
  either way (same units), but power is spent for nothing. Refused.
- **(b1) Admit B for non-close-only columns.** Refused. B in 2010-13 is `range2_extremes` (0.545-0.870 of days pass):
  Dukascopy bar highs and lows disagree with the independent reference. Those are the inputs of ATR, ADX, Donchian
  and the barrier label itself. R-a already admits B where only closes are read. That is the correct boundary.
- **(b2) Re-grade C.** C in 2010-13 has 10 months: 8 are `close_vs_ref` at 0.950-0.957 (one disagreeing day in 20-23),
  2010-12 is 0.909 (two days) and 2013-01 is `stale_runs`. That is month-level granularity in a frozen rule (ADR 0105:
  "the grade is applied as frozen"; R-a: "not arbitrable under r11-v2.1"). It is not a defect a second source fixes:
  D-14 has no admissible candidate (Forexite closed, quant C2; broker MT5 holds about 3 months). A day-level C mask
  would be a new rule after the grades were seen. Not taken. Under D1 it is not needed: C months are simply out.
- **(c) Learn from 2003-05.** Gold 2003-08 is graded C throughout (no independent minute reference, ADR 0105), so it
  gives 0 usable rows. It is test_old under the card ("never moves test_old into training"). Refused.
- **(d) Gold-only reduced set (core-6/core-12).** Adopted in the form the card already specifies: the ladder prefix
  is the reduced set and D1 counts it correctly. No new column set is introduced, and P30 stays the level order's tail.

## 4. Expected usable gold rows (T2 long events, phase 0, label-blind)

Under D1, dukascopy XAUUSD, learn years. The per-year columns use the core-6 mask (the L3 prefix), and the
`fold 2010-11 core-12` column uses the L2 prefix. The `P30 (today)` column is what Stage 0 saw. 1d/1w stay 0 (no
tier-surviving row; unchanged, re-assessed at each monthly regrade).

| tf | 2010 | 2011 | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | fold 2010-11 core-6 | fold 2010-11 core-12 | 2010-17 core-6 | 2010-17 P30 (today) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1m | 145,570 | 231,767 | 148,753 | 196,962 | 290,165 | 321,675 | 321,735 | 351,086 | 377,337 | 377,177 | 2,007,713 | 825,710 |
| 5m | 28,754 | 45,292 | 29,620 | 38,780 | 57,840 | 64,239 | 64,136 | 70,217 | 74,046 | 73,939 | 398,878 | 165,034 |
| 15m | 8,971 | 14,101 | 9,826 | 11,668 | 18,399 | 20,974 | 21,007 | 23,406 | 23,072 | 23,019 | 128,352 | 54,964 |
| 30m | 4,153 | 6,309 | 4,828 | 5,067 | 8,715 | 10,246 | 10,260 | 11,703 | 10,462 | 10,333 | 61,281 | 27,434 |
| 1h | 1,771 | 2,402 | 2,279 | 1,833 | 3,889 | 4,889 | 4,895 | 5,851 | 4,173 | 4,048 | 27,809 | 13,694 |
| 2h | 784 | 675 | 692 | 419 | 1,704 | 2,215 | 2,207 | 2,926 | 1,459 | 1,331 | 11,622 | 6,815 |
| 4h | 289 | 263 | 45 | 0 | 620 | 876 | 670 | 1,463 | 552 | 438 | 4,226 | 2,410 |

These are rows, not n_eff. The level is not predicted here. It is recomputed mechanically at Stage 0 (2.3), and on the
rev-3 floor most units remain below L3 and serve the intercept-only body (B8). Under D1 every unit 1m-4h moves from
NOT STARTED (data) to "fit" (at least B8), and gold 1m can be laddered on the fold the card names.

## 5. Re-derivation (research director, 2026-11-27)

`dense_labels.attach_tiers` on main (d7c15966), served tiers ff653ea7, `Tiers.from_csv(source="dukascopy")`. Synthetic
decision grid: every tf bar on weekdays outside 21:00 UTC, 2010-2017, template T2, `horizon_end = decision + H(tf)`,
`label_ok` true. Per month, the usable share is scaled to the file's `bars` / tf minutes. Check against the
trainer's Stage 0 P30 counts: 1m 2014-17 139,434 / 199,620 / 135,570 / 351,086 vs 139,282 / 199,408 / 135,348 /
351,025 (≤ 0.2 %). 15m 2014 9,268 vs 9,294, 1h 2014 2,302 vs 2,310, and 2010-13 0 vs 0. 4h is within 8 %, because
of the grid approximation. Core-6/core-12 rows in section 4 come from the same run with `columns=` changed.

## 6. What changes, who signs

| Owner | Change | Acceptance check |
|---|---|---|
| implementer (PR #488) | `stage0`/`stage0_counts`: usable per ladder prefix via `attach_tiers(columns=prefix)`; `decide_unit` tries (b) before NOT STARTED; L-cw train/impute/score on the served-columns mask; HB/G-cw keep P30 and skip empty folds | Failing tests first: (i) a fixture with a B/C month 60 days before an A month gives core-6 rows and 0 P30 rows; (ii) `decide_unit` with no (a) rows and (b) rows returns fit on (b); (iii) on the served tiers file, Stage 0 gold fold 2010-11 core-6 1m rows = 377,337 ± 1 %; card test 18 (iv) still label-blind. Quant C3: `load_labels` is called with `columns` = the narrowest served column set of the unit (so it drops no row the judged learner needs). The P30 mask for HB, G-cw and AVG is applied in the trainer with `attach_tiers(columns=P30)`, never by a second label load. Test (iv): a fixture with a C month inside the P30 lookback but outside the core-6 lookback is kept for L-cw and dropped for HB. |
| data-engineer | Training sets carry every `label_ok` row 2010-17 with fs-v1 features. Rows are not filtered by the P30 mask, and `usable` is not precomputed for P30 only. Per-row `decision_utc`, `horizon_end_utc`, `tf`, `template` stay so the trainer can mask per column set | Yearly row counts per tf match section 4's synthetic grid within 2 % (1m-1h), and features are non-NaN outside warm-up for core-12 in 2010-13 A months |
| ml-researcher | Republish the 2.3 ladder table with the measured ρ inputs on the D1 counts before Stage 0 | Table in the Stage 0 report, hashes recorded |
| quant-reviewer | Signs D1-D4 (required) | Sign-off line below |
| evaluator | Countersigns D1's judged read population | Sign-off line below |

## Sign-off

- Research director, 2026-11-27: written label-blind; no sr-v3 label, Stage 0 or L statistic read.
- Quant-reviewer, 2026-11-27: signed with C1-C3 (inserted verbatim above). Notes: 4h fails gate 10 mechanically (OOF 2012 45 rows, 2013 0 under core-6), report it and do not re-fold; if the excluded months' vol mix differs from the kept months', the judged S applies only to the kept regime mix and the report says so.
- Evaluator: pending.
