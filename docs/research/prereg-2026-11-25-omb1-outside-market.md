# Pre-registration card OMB-1: the outside-market block (gold 1h+4h filter, A/B)

Research director (card), macro-researcher (columns), ml-researcher (model). Written 2026-11-25, before any OMB-1
feature row, label or score was computed. **Status: proposed; the quant-reviewer signs before Stage 0.** The block
in section 2 is **frozen on signature, no later than 2026-11-28** (ruling D10). Line 4 of the five research lines.

**H-101 was NULL on 2026-11-25** (Stage L, K 8: edge vs no-model at most +0.0065 R on 104,367 OOF candidates,
kept mean −0.029 to −0.034 R, Holm p 1.0 for all eight). Price-only filters on sr-v2's inputs sort almost nothing.
OMB-1 is therefore the **main research line**: it is the only judged test of new information this quarter, and
H-102's further units (part (c)) now open only if OMB-1 continues or H-102's gold 1h+4h unit passes. This changes no
threshold, column or date in this card.

## 1. Card (research-director rule 2)

| Item | Value |
|---|---|
| Question | Do ≤ 10 point-in-time columns from outside gold's own price (dollar, silver, real yields, risk regime) raise the money a gold 1h+4h trade filter keeps, over the same filter on gold-price inputs only? |
| Mechanism | Gold is priced in dollars and co-moves with silver and real yields (Baur & Lucey 2010; Erb & Harvey 2013). A gold move the dollar and silver do not confirm is more likely noise or flow that fades, so a filter can drop candidates trading against that state. The block is the first information the models get that is not gold's own price |
| In-house evidence (learn era, already SEEN, not re-read) | silver relative momentum: 4h IC +0.022 (2.5 se), 1d +0.047 (2.6 se), decaying by era; X3 fair-value residual +0.019 R vs its twin (p 0.004); dollar 4h direction −0.001 (null); US500 intraday sign unstable 2012-17 (so risk regime only, never intraday returns). Source: tr-10, 2026-11-25 |
| Expected gross lift on kept rows | +0.01 to +0.03 R (tr-10: 0.01-0.02 R at 15m-1h, more at 4h) |
| Cost per trade, gold | 1h 0.06-0.09 R, 4h 0.032-0.046 R (W2 bps at a 1 ATR stop, 2014-17, H-102 draft); ruling D9 cites 0.002-0.03 R for the same unit. **The two figures disagree by about 3x.** The cost gate in section 4 uses the W1-M measured cost of record, whichever it shows |
| Expected trades | about 500 candidates a year across the unit's 7 arms, about 2 a session day; about 1 a day kept |
| Expected net £/day at demo size, if it passes | +0.05 R x about 1 kept trade a day x about £11-26 per R at 0.01 lot (a 1 ATR stop of about $15 at 1h to $35 at 4h on 1 oz) = **about £0.5-1.3 a day**. A pass is worth knowing, not a money line on its own |
| Prior | about 3-5 % that the A/B continues |
| K | **≤ 2:** Stage L 1 (2026Q4); Stage C 1 only after a continue, as the quant rules confirm charging. Refusals cost 0 |
| Budget | about $40 of agent tokens; Mac or ops-VM compute, never the brain |
| Dates | card and frozen block 11-28; Stage 0 by 12-05; Stage L **12-09** (joint with H-102, section 5); **kill date 12-18** |
| Owner | macro-researcher (columns, PIT), ml-researcher (fit and read); the evaluator reads |

**Units refused now by the cost card (0 K).** A 15m unit: expected lift 0.01-0.02 R against a cost of about
0.06-0.09 R. Even a perfect read of that lift leaves the kept mean below cost, so it is not started. 1d and 1w are
power-refused in every class (H-102 draft section 4.3), so they are context only.

## 2. The frozen block (≤ 10 columns, every join through `asof_join` on `available_utc`)

| # | Column | Definition | History source | Live source | `available_utc` |
|---|---|---|---|---|---|
| 1 | `usd_basket_ret_1` | log return over one decision bar of the dollar basket: EURUSD, USDJPY, GBPUSD, USDCAD, USDCHF at ICE weights without SEK, renormalised; + = dollar up. **Not** the broker USINDEX (its source changes on 2017-12-01) and not FRED DTWEXBGS | Dukascopy 1m FX majors | icm-probe 1m | the bar's close |
| 2 | `gold_resid_usd_6` | gold 6-bar log return minus a past-only 500-bar OLS beta x the basket's 6-bar return | as 1 + gold | as 1 | the bar's close |
| 3 | `xag_rel_mom_6` | Δ log(XAG / XAU) over 6 bars | Dukascopy 1m XAGUSD | icm-probe 1m | the bar's close |
| 4 | `xau_fair_resid_z` | z-score of gold's residual against the X3 fair value (XAG, EUR, JPY; past-only beta) | as 1 and 3 | as 1 and 3 | the bar's close |
| 5 | `gsr_z252d` | gold/silver ratio z-score over 252 daily closes; the previous 17:00 New York close (ruling D15) | daily from 1m | same | the previous daily close |
| 6 | `corr_gold_usd_120` | rolling 120-bar correlation of gold and basket returns, past-only | as 2 | as 2 | the bar's close |
| 7 | `macro_real_yield_chg20` | 20-business-day change of FRED DFII10 | FRED 2003+ | FRED | the macro feed's PIT release time; with no vintage stamp, the value date + 1 business day at 22:00 UTC |
| 8 | `macro_real_yield_z252` | z-score of DFII10 over 252 business days | FRED | FRED | as 7 |
| 9 | `macro_spx_dd20` | S&P 500 close below its 20-day high, in % | FRED SP500 or the local daily index file | FRED | as 7 |
| 10 | `macro_vix_chg5` | 5-day change of FRED VIXCLS | FRED | FRED | as 7 |

- Columns that interact with the candidate's side enter as column x side; each still counts as one column (quant Q6).
- **Priority order for the subsets in section 4, fixed now** (by the in-house evidence above, before any OMB-1 read):
  3, 1, 2, 7, then 4, 5, 6, 8, 9, 10.
- **Per-source standardisation** (quant R10): every column is standardised within its source era (Dukascopy history,
  broker live). Serving later needs PSI < 0.10 between Dukascopy and broker per column (W1-M measures it).
- Excluded, with reasons: oil and copper (unstable or null), equity intraday returns (sign flips), broker UST10Y and
  BUND10Y (no free intraday history, live feed dead since 11-04), COT (the positioning lane's).
- A column with no Tier A/B history for an instrument-year (quant R11) is missing for that year. It is never
  back-filled from another source.

## 3. Population, models and statistic

- **Population:** the H-102 gold 1h+4h unit, history replay (`history_replay.py`, Dukascopy XAUUSD 1m, cost of
  record in bps), arms G1A, PDC1, MB1H (1h) and DON4, PDC4, KSQ4, BXF4H (4h), each with its matched control.
  MB1H and BXF4H are retired box-family arms; "retired arm + learned filter" is a new hypothesis (ruling of
  2026-11-24). No new rule body.
- **Window:** learn 2010-01..2017-12, plus 2005 for ≥ 1h only if Tier A/B (ruling D16). Out-of-fold read 2012-17,
  expanding walk-forward in yearly blocks, full-horizon purge, embargo = max(1 day, the longest max hold). test_old,
  confirm and the holdout are not read at Stage L.
- **Model A (gold-price inputs) and model B (A + the block subset):** the obj-v1 trade filter (ADR 0098): weighted
  logistic, y = 1[net R > 0], w = uniqueness x min(abs(net R), 10), keep iff p_w ≥ 0.5, no keep floor, ABSTAIN below
  MIN_KEPT. L2 at sr-v2's `C_REG`. Same rows, same folds.
- **Gold-price inputs:** `side`, `tsmom_score` x side, `atr_ratio_14_100`, `dist_vwap48_atr` x side. `em_atr_cost`,
  `spread_atr` and the tick columns are out: they are source-scaled (quant R10).
- **Statistic:** Δ = S_B − S_A, with S = mean(net R x keep) − max(0, mean(net R)) per candidate (ADR 0098). One-sided
  p from the stationary day-block bootstrap (mean block 5 days, B 10,000, seed 20261209).

## 4. Stage 0 (0 K, counts and costs only; any refusal is NOT STARTED, 0 K, and closes the line at its kill date)

1. **Leak suite first:** truncation invariance, future perturbation, the HTF boundary, a seeded future-release as-of
   test for every macro column, shuffled-label AUC 0.50 ± 0.02, oracle canaries. Holdout rows raise. A failure means
   no read.
2. **Cost gate (rule 2), per timeframe:** the W1-M measured cost of record in R at the unit's stops must be ≤ half
   of +0.07 R (the labelled gross of +0.038 R plus the expected lift of up to +0.03 R), so ≤ 0.035 R. A timeframe
   failing it leaves the unit before any read. On the W2 figures 1h fails and 4h passes narrowly; on D9's figures both
   pass. The measurement decides, not this card.
3. **Size (quant Q6, Riley et al. 2019, shrinkage ≥ 0.9, anticipated AUC 0.57).** Required n_eff for p_eff
   coefficients, re-derived for this card at outcome prevalence 0.40-0.45: p_eff 8 about 4,400-4,500; p_eff 4 about
   2,200-2,300. The ladder, fixed now:
   - level 1: A = the 4 gold-price inputs; B = A + block columns 3, 1, 2, 7 (p_eff 8);
   - level 2: A = `side`, `tsmom_score` x side; B = A + columns 3, 1 (p_eff 4);
   - below level 2: NOT STARTED (size).
   Level is chosen by the unit's learn n_eff (quant Q3) on counts only. Expected: about 500 candidates a year over
   9 learn years, about 4,500 raw rows, lower after uniqueness. **Level 2 or a refusal is the likely outcome.**
4. **Power** (`power_first.start_or_refuse`, ADR 0085 as amended 2026-11-25): delta 0.10 R, n_eff ≥ 200.

## 5. Reading rule and kill

- **Stage L (one read, K 1):** CONTINUE needs Δ ≥ +0.03 R **and** one-sided p ≤ 0.10. Otherwise the line is
  **closed**: the columns stay as context only. It is never widened, never given new columns or a new timeframe, and
  never renamed.
- **Joint with H-102:** OMB-1 and H-102's gold 1h+4h Stage L run in **one invocation on the same fitted artifacts**,
  on 12-09 if both Stage 0s are done by 12-05, otherwise both by 12-18. Neither is read before the other.
- **Stage C (only after a continue, K ≤ 1):** confirm 2018-01..2025-09, read once: Δ > 0 and p ≤ 0.10.
- **Kill date 12-18:** with no Stage L read by then for any reason, the line closes.
- **Report:** Δ, S_A, S_B, kept share, kept mean net R and £/day at 0.01 lot, with losers next to winners. AUC is a
  diagnostic only.

## 6. Not in this card

No intraday XAG or EURUSD columns beyond the block, no COT, no second block version, no 15m, 1d or 1w judged unit.
Adding a column later is a new card with its own K.

## Signatures

- Quant-reviewer: pending (block frozen on signature, by 11-28).
- Evaluator (reads Stage L): pending.
