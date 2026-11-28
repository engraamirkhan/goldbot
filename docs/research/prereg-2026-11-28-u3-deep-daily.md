# Pre-registration: H-107 (U3), gold deep daily 1971-1998: a 12-month trend side filter on the 1d+1w unit

Research director, 2026-11-28. Owner approval of 2026-11-28 for the fresh routes of H-104 §10 (PR #521). This card is
route **U3, DEEP-DAILY**: "gold 1978-2002 daily is a pre-learn window, so it needs its own card. It is the one route
where cost per candidate is small (1d+1w: 0.054 R)."

**Label-blind for the window.** No price, return or label of 1968-2002 has been read for any statistic. The only reads
behind this card are the `date` and `source` columns of `raw/daily/xauusd_daily_1978_present.parquet` (row counts per
source and year) and the brain registry snapshot of 2026-11-24 (no row reads a pre-2003 window).

**Seen and disclosed (other windows, the same family):** registry trials 49-87 (2026-10-18), family `tsmom` at 1d on
Dukascopy gold 2010-2025, 20-120 day lookbacks with barrier exits: screen gross +0.10 to +0.12 R per trade (t 1.6-2.1,
n 421-457), net −0.02 to −0.06 R at the then cost priors, which the 11-15 audit found 2.6x too high. That evidence shaped
the prior below. It is not this window.

**Quant review of #523 (verbatim):** "Seen: POS1-001 (same sign(r252) signal), net SR 0.40, t 1.61, 2003-05 + 2010-17;
family closed 2026-11-06 (A1.4). This card offers no new mechanism evidence. It is admissible only as a closure record
under that rule. Deflation K includes the family's line K 19 and registry trials 49-87." **This card is therefore a
closure record**, not a reopening of the family.

**Status: proposed.** Quant-reviewer and evaluator sign. **0 K now.** Real money, sizing and live unlock are
owner-only.

## 0. Money first

At 1d+1w, cost per candidate is 0.054 R on the sr-v3 learn rows (H-104 §0, seen), against 0.13-0.95 R at 15m-4h. Take-all
1d+1w was −0.007 R net with gross about +0.047 R (seen): the cost is small, but a filter must still find gross. This card
asks whether 27 years of untouched gold daily history can give a gold daily rule a powered judged read.

## 1. Data, population and windows

**What exists (re-derived from the file's `date`/`source` columns):**

| Source in the stitched file | Span | Rows | Usable here |
|---|---|---|---|
| Bundesbank Frankfurt gold fixing, DEM/kg converted to USD/oz with the Frankfurt USD fixing (`bbk:XAU_DEM_frankfurt_fix_in_USD`) | 1968-06-18..1998-12-30 | 7,622 | yes, from 1971-08-16 |
| (hole: no free daily USD gold source) | 1999-01..2000-08 | 0 | no |
| Yahoo GC=F continuous futures | 2000-08-30..2003-05-02 | 665 | **no**: futures, not roll-adjusted (ADR 0046 decision 3: never spliced into spot levels) |
| Dukascopy | 2003-05..2025-09 | 5,809 | no: that is the learn, confirm and test_old of other cards |

- **Window:** decisions 1971-08-16..1998-12-30, the end of the dollar's gold window to the last Frankfurt fix. ADR 0046
  excludes pre-1971 prices from return and volatility statistics, so no lookback reads before 1971-08-16. That leaves
  6,842 fix days; after the 252-day lookback warm-up and the last horizon, about **6,580 decision days**.
- **Price:** one fixing a day (a point price, not OHLC). So only close-to-close (T0) labels and close-only inputs exist.
  No barrier template, no ATR, no P30 learner (P30 reads ranges). This rules out G-cw, HB, AVG, SEQ-1 and CAS here.
- **4h: not justified.** There is no intraday gold before 2003. ADR 0046 forbids synthetic intraday bars as evidence.
- **Grade (if the card ever starts; not commissioned, section 6).** ADR 0105 grades minute data against an independent
  minute source; no such source exists before 2003. The daily rule must be written by the quant before any grade.
  Draft for the quant, `rd-v1`, label-free:
  - per month, coverage of ≥ (Frankfurt business days − 2) fixes (tolerates 2 missing days a month);
  - no run of ≥ 3 identical consecutive USD fixes;
  - |ln(month mean of the USD fix / World Bank Pink Sheet monthly gold average)| ≤ tol. tol is the smallest 5 bp step at
    which ≥ 95 % of 2010-2018 calibration months pass, comparing the Dukascopy 15:00 London mid month mean with the same
    reference. Void if tol > 100 bp.
  - "B-daily needs a day-level independent reference (a London fix daily series, where a local copy exists); without
    one the grade is 'monthly-consistent only' and is not B-daily." (quant, verbatim) A month that passes only the
    monthly checks above is 'monthly-consistent only'. No local London fix daily series exists for 1971-1998 today.

## 2. The cell (Holm m = 1, fixed now)

**H-107 "TSM-12": one rule-only side filter on the sr-v3 1d+1w unit, gold only.**
- **Candidates:** every fix day (1d) and the last fix of each ISO week (1w), both sides (dense, as sr-v3).
- **Signal:** s_t = sign(ln(P_t / P_{t−252})), fixes as of t. Keep the side equal to s_t and abstain on the other side
  (k = 0.5). s_t = 0: abstain on both. No parameter is fitted. The 12-month lookback is the published default
  (Moskowitz, Ooi & Pedersen 2012). "The 12-month lookback was selected in published samples that include gold
  1985-1998; the window is fresh to this program only." (quant, verbatim)
- **Label (T0):** entry at the next fix P_{t+1}, exit at P_{t+1+H}, H = 10 sessions (1d) and 20 sessions (1w), the sr-v3
  H. gross R = side × ln(P_exit / P_entry) / (σ20_t × √H), where σ20_t is the sd of the 20 daily log returns to t. One R
  is one sigma of the hold, so the gross sd is about 1 and the 1.2 R sd floor is honest. Clipped at ±10 R.
- **Cost of record:** a new version `srv3-cost-deep-<sha>`, built label-blind by the microstructure-researcher and
  recorded on an ADR 0098 R-H line before Stage 0. It charges **today's** costs scale-free, because the trade being judged
  is today's:
  - the round trip in bp is the maximum over brokers and sessions of the record's own cost-table cells for tf ∈ {1d, 1w}:
    max(cell `rt_bp`, cell `ecr_ub95_usd` / reference price × 1e4) at the worse broker, floored at W2. The reference
    price is fixed at the cell's ECR-window mean broker mid. It is not computed as the maximum of `gold_row_bp` over
    2018-25 daily rows (quant item 5);
  - swap in bp per calendar night held, per side, from the record's swap table at its latest month (Wednesday triple
    folded into the nightly mean);
  - stress at 2x.
- **Population n_eff:** ADR 0085 Q3 computes uniqueness across tfs, so 1w labels share the 1d days. The unit's n_eff is
  counted as decision days / 10 = **≤ 658**. The sr-v3 / H-104 summed convention (1d + 1w = 658 + 329 = 987) is an upper
  bound, and it is **not** used. "Stage 0 states `power_first.uniqueness` on the label-free 1d+1w event timetable
  (expected about 540, power about 0.28)." (quant, verbatim) The binding figure is that one, and it is below the 658 days/10
  figure used in section 4. Both fail.

## 3. Hypothesis and prior (rule 2 card)

| Id | Hypothesis | Mechanism (why gold pays it) | Expected gross per kept trade | Cost per candidate (today, bp → R) | Expected net £/day at 0.01 lot | Prior P(pass) |
|---|---|---|---|---|---|---|
| H-107 TSM-12 | Gold's 12-month return sign predicts its next 2-4 weeks | Slow information diffusion, hedging and central-bank flows; trend-following premia across commodities (Hurst, Ooi & Pedersen 2017) | at a net annual Sharpe of 0.3-0.5: 0.06-0.10 R (SR × √(H/252)) | about 1-2 bp round trip (≈ 0.01 R) + long swap ≈ 1.2 bp a night (≈ 0.04 R over 10 nights); about 0.03 R averaged over sides | ≈ £0.5/day non-overlapping (+0.05 R × about £110 per R at 1 oz (one 10-session sigma at $4,000 gold) × 25 trades a year) | **3 %** |

- Rule 2 holds on gross vs cost: 0.06-0.10 R ≥ 2 × 0.03 R.
- **The gate does not hold.** Gate 1 needs S ≥ +0.05 R per candidate, so kept mean net ≥ +0.10 R at k = 0.5. That needs
  a net annual Sharpe of about 0.5 at H = 10 sessions, at the top of the published range for a single commodity. Hence
  the 3 %.

## 4. Power (ADR 0085, label-free) and the predicted Stage 0

- Fresh route: the deep window itself (pre-learn, never read). A rule-only cell has no Stage L; the read is one batch on
  the ADR 0085 ledger. Ledger year 1 (read before 2027-11-15): α_j 0.00625, m 1, **z 2.50**, n_req = (2.50 × 1.2 / 0.10)²
  = **898**.
- n_eff **≤ 658** (days/10) if every month grades, power Φ(0.10 × √658 / 1.2 − 2.50) = **0.36 < 0.5**. On the uniqueness count (about 540) power is about 0.28.
- The smallest per-trade edge detectable at power 0.5 is 0.117 R, a net annual Sharpe of about 0.59.
- After 2027-11-14, z is 2.73 and n_req is 1,077. The route never gets better with time.
- **No hold length fixes it:** with R = one sigma of the hold, n_eff ∝ 1/H while the per-trade edge ∝ √H. The true power
  does not depend on H. Choosing H to pass the δ = 0.10 R rule would be gaming it, and is not done.

| Item | Predicted |
|---|---|
| S0-1 (grade rule `rd-v1`, grades, World Bank reference, deep cost version) | fail today; **not commissioned** (S0-3 fails on the ungraded upper bound) |
| S0-2 (cost feasibility, reported) | pass: cost ≈ 0.03 R per candidate |
| **S0-3 (record reason)** | **fail: uniqueness n_eff about 540 (power about 0.28), and at most 658 even by days/10 (power 0.36), both < 898 at z 2.50; with the summed 1d+1w upper bound, 987 / 0.55 would pass, but Q3 refuses that count** |
| **Predicted** | **NOT STARTED (power), 0 K. U3 closes UNTESTABLE for gold-only deep daily under ADR 0085.** |

**The powered variant, named and not registered.** Pooling gold with the five Frankfurt-fix FX series of 1973-1998
(EURUSD from DEM, GBPUSD, USDCHF, USDJPY, USDCAD; local, `raw/daily/long/`) at ρ_inst 0.5 gives about 1,074 n_eff
(power 0.62 at m 1). That evidence would be five-sixths FX, the series share the USD/DEM leg (so the true ρ is higher),
and it says little about gold today. It is a new card if the director wants it.

## 5. Reading rule (fixed now; used only if Stage 0 ever starts the cell)

- Gates: sr-v3 5.3 gates 1-6 and 8. Gate 7 uses B0, B1 (take-all), B3 (always long / always short), B7 (random twin by
  day and side) and buy-and-hold over the window after one round trip (ADR 0106 decision 4). Gate 11: n_eff ≥ 200.
- Gate 12, deep form: ≥ 3 calendar years with positive kept net and ≥ 10 kept trades each, with at least one in
  1972-1985 and at least one in 1986-1998. Gate 13 reported.
- Stationary day-block bootstrap (BOOT 10,000, mean block 20 days for the 10-20 session holds, seed 20261206). One read.
  S by year and decade, losers next to winners, £/day at 0.01 lot.
- A pass licenses a learning-class demo arm on gold 1d with a matched twin, never real money (owner-only).

## 6. K, budget, kill rule, dates

- **K:** 0 now; worst case 1 (the single read); predicted 0.
- **Budget:** 2026Q4 80 free (stale brain snapshot of 2026-11-24; the ops VM is the count of record); 2027Q1 12 open on
  main (20 − 8 reserved, no override until ADR 0099's settings land).
- **Kill (quant F5 form):** Stage 0 is evaluated on the **ungraded upper bound first**. If S0-3 fails there, no grading,
  reference pull or cost version is commissioned (rule 2: no tokens on a route that cannot start). U3 closes
  **UNTESTABLE** the same day: no read, not a NULL, the window stays unread. Reopening needs a new card: an admitted
  1999-2002 spot daily source (it adds about 100 n_eff, not enough alone) or the pooled variant above.

| Date | Step | Owner |
|---|---|---|
| 2026-11-28 | Card | research director |
| last signature, no later than 2026-12-04 | Stage 0 on the upper bound recorded; closure the same day if predicted | evaluator; research director |

## 7. Integrity

- The 1971-1998 window is unread: no registry row reads a pre-2003 window (brain snapshot of 2026-11-24, 6,990 rows),
  and no research doc reports a pre-2003 gold return. At Stage 0 the evaluator re-checks the ops-VM registry, and also
  `state/position_reads.jsonl`, `window_reads.jsonl` and any ADR 0046 regime-robustness daily-proxy check that touched
  1971-1998 (quant item 6).
- Holdout untouched (the window ends in 1998). One instrument, one rule, m 1, K counted if ever read.
- **Re-derived for this card:** fix-day counts (7,622 from 1968; 6,842 from 1971-08-16) from the file's `date`/`source`
  columns. The registry tsmom 1d screen figures above come from the registry rows' `results.screen` (trials 49, 81).

## Sign-off

- Research director: 2026-11-28.
- Quant-reviewer: CHANGES on #523 (items 1-6), applied verbatim 2026-11-28; re-verify pending.
- Evaluator: pending.
