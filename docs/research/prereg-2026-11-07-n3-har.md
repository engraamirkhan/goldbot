# Pre-registration 2026-11-07: N-3 HAR low-range-day abstention (and the N-1a routing note)

Status: **registered before any outcome**, with the release that carries `goldbot/engine/refusals_novel.py` (signed
plan 2026-11-07, WS-6; card N-3 of docs/research/novel-techniques-2026-11-07.md). Kill-only: the rule can only refuse
an entry. Family `novel:2026-11`, **K +1** (N-3). N-1a adds **K +0** (section 6).

## 1. Hypothesis and why it is a cost rule, not an edge claim

On a day whose predicted range is low, cost per R is highest and the learning arms' gross is about zero, so standing
aside on the bottom quintile of predicted range keeps that day's cost. The predictor is H-16's HAR baseline, the only
range input that survived the H-16 screen (quintile ratio 1.165; the forward quintile split held in all 6 OOS years).
No direction is read.

## 2. The forecast (nightly job `har_days`, goldbot/research/har_abstention.py)

- Inputs: gold 1h mid bars of the release feed (`bars_1h`, source dukascopy) up to the first minute of the canonical
  broker's own feed (`bars_1m_live`, source `research.har_abstention.broker`, ICM), that feed from it on: one feed
  per span, one cut-over (engine/history.py's rule). Only minutes visible at the cut-off are read.
- Cut-off: the latest opportunity-day close (22:00 UTC) at or before the job's slot. **Nothing after it is read.**
- Daily inputs, HAR regressors and the forecast are `features.day_type`'s (H-16): `hat` = exp(b0 + b1 ln w1 + b5 ln w5
  + b22 ln w22) for the next trading opportunity day.
- Fit: **one frozen OLS fit** on the rows whose target day closed in [`fit_from`, `fit_to`) with `fit_to` =
  2025-10-01, the holdout start (a load-time check refuses a later `fit_to`). The holdout trains nothing; recent days
  enter only as regressors (inference), as every live feature does.
- Threshold: q20 = the 20 % quantile of the trailing 250 forecasts emitted before this one by the same fit. **Low day**:
  hat <= q20.
- A row is written only when the last stub-free input day is the trading day before the forecast day, the fit has at
  least 500 rows, 250 trailing forecasts exist and no two consecutive input days across those 250 forecasts and their
  HAR(22) lookback are more than 4 calendar days apart (`max_gap_days`; a Thursday -> Monday holiday is 4; a longer gap
  is a data hole); otherwise the job fails and writes nothing, and the alert is the scheduler's nightly job-failure
  health alert (`job:har_days` FAIL).
- First row wins: a later run for the same day never changes a row that was already available (point in time).

## 3. The rule (engine, `refusals_novel`, registered in `entry_refusals.REFUSALS`)

- Scope: gold (XAUUSD) exploration signals of `role: learning` arms and of these learning controls, a fixed set
  (`refusals_novel.SCOPE_CONTROLS`): ZCD and the LC* twins LCS5A, LCS5B, LCZ1P, LCZ2P, LCZ3D, LCZ4D, LCZ4P, LCZ5P,
  LCZ6D, LCZ6V, LCZ7D, LCZ7P, LCZ7V, LCZ9D, LCZ10D. Retiring an arm never moves a control into scope; a new learning
  twin needs an amendment (a test pins the set to the arm config). Strategy, cost, owner-experiment arms and shared
  controls (S0, S0R, F1C, G1C, GS1C) are outside it. Exits, stops, targets, holds and the RiskGate are untouched
  (entry hook only).
- `har_low_range`: the row for the signal's opportunity day says low -> refused, shadowed (`exploration_obs`,
  `blocked_reason = har_low_range`).
- `har_unavailable` (**fails closed**, trading-safety objection 1): once the job has written any row, a signal whose
  opportunity day has no row available at its decision time is refused and a warning is logged once per day. The
  alert for a missing row is the nightly job-failure health alert above; the engine's warning is a log line.
  Expected behaviour: a signal less than 30 minutes after the 22:00 UTC cut-off (the job runs at 22:05) usually finds
  no row yet; it is refused as `har_unavailable` without the warning.
- Not registered while no row exists (the job never ran on this host): N-3 then refuses nothing (signed plan: "If the
  nightly HAR job has not written a row by the dry run, N-3 is not registered tonight ... and moves to Wed").
- Off switch: `research.har_abstention.enabled: false` makes the job write an `off` row; from its availability the
  rule refuses nothing. That is the kill path.
- Order: window_0207, f1_pit, learning_icm_only, har_unavailable, har_low_range. A signal inside 02:00-07:00 UTC is
  counted as window_0207, so N-3's saving excludes the overlap with the 02-07 block.

## 4. Read (20 session days from the first `ok` row, read once)

- Saving = minus the summed net (GBP at the arm's size, measured costs) of the `har_low_range` shadow candidates as
  labelled by `exploration_data.label_candidates`; per week = saving / weeks.
- Fill change = learning fills per session day in the window vs the 20 session days before it, all accounts.
- **Pass**: saving >= GBP 25/week and learning fills on non-low days unchanged (within 10 %).
- **Kill** (signed plan): learning fills fall by more than 25 %, or saving < GBP 10/week. Between the two: continue
  one more 20-day window, then kill unless it passes.
- A pass is never evidence of an edge for any arm; it keeps a cost rule.

## 5. Integrity

- Holdout >= 2025-10-01: no fit row, no quantile fit; only inference regressors from live minutes.
- The demo outcomes of refused candidates are read only for this rule's saving, never for any arm's qualification.
- No parameter (quantile, 250 days, fit window) changes after the first row without a new amendment and K row.

## 6. N-1a: gold learning arms on ICM only (routing, K +0)

- Today no gold learning arm or learning control is active on vantage-demo: Z6V, Z7V, LCZ6V, LCZ7V and ZCV were
  retired on 2026-11-06 under the signed A5 kill (a5-signoff-2026-11-06.md section 7, net < 0 on history). Recreating
  them on ICM under new ids would resurrect killed arms; **not done**, so the ids reserved for N-1a in the WS-3 assert
  are not used (K +0).
- The routing is enforced for any future row by the refusal `learning_icm_only`: a gold learning arm or a control read
  only against learning arms on an engine whose broker is not ICM is refused (journalled, shadowed). Vantage gold cost
  probes (`cost_probe`, outside `_send`) are untouched.
- Measured costs (measured-2026-11-07-v1): gold round trip (spread + 2 x commission) is **0.40 bps on both brokers**
  (ICM 0.20 spread + 2 x 0.099; Vantage 0.27 + 2 x 0.066). The routing's cost case therefore rests on the fill-level
  evidence of the novel doc (ICM tighter in 65 % of same-minute pairs, Vantage entry slip +$0.055..0.135), not on the
  quoted round trip. N-1a's EV is about zero today (nothing to move); it is a guard.

## 7. Sign-off

- quant-reviewer: **pending** (frozen code version = the commit that adds this file; unit N-3, K +1 in `novel:2026-11`).
  The K row is not written to the research registry by this change; it is written at sign-off, before the first `ok`
  row, by the research director.
- trading-safety-reviewer: **pending** (entry-only refusals; fail-closed `har_unavailable`).
