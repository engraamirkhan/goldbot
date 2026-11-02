# ML+prob lane MLNET: results (learned net-R selection of 11 trend rules; 0 of 4 pass confirm)

Principal ML researcher, 2026-11-02. Pre-registration `mlnet-prereg.md` (commit `33971afa`, before any look;
amendment W2 + reporting additions `9b38630b`, before any run). Scripts committed before any run (`d2404340`):
`scripts/mlprob/mlnet_engine.py` (the trend lane's `trend_screen.py`, PR #272, copied; changes marked MLNET),
`mlnet_pool.py`, `mlnet_select.py`. Cost of record `cor-2026-10-31.2`, ICM upper bounds + overshoot + shipped swap.
Nothing at or after 2025-10-01 was read (loader assertion; 2026 file never opened).

**Verdict: FAIL. 1 of 4 variants passed explore (V3 HGB-TOP3, net +0.088 R, clustered t 2.22); it failed confirm
(net -0.012 R, t -0.37, DSR 0.000 at K 3,237, 4 of 8 years). No arm, no rule code, no `exploration_arms.yaml`
entry. TEST-OLD not run (no confirm passer). K += 4.**

## Run log (integrity)

prereg commit -> W2 amendment commit -> scripts commit -> explore pool build (once) -> explore selection (once) ->
confirm pool build (once) -> confirm selection of V3 only (once). No rerun, no parameter changed. Parity: the explore
pool reproduces the trend lane's per-rule n, net mean R and leak-free control excess exactly for all 11 rules
(e.g. 03: n 449, net +0.206, control excess +0.115). Shuffled-label V2 (leakage check): selected minus unselected
-0.013 R (about 0, as expected). Detail disclosed: the ATRd percentile feature uses `min_periods=60` inside its
250-day window (3.4% NaN), set in the script before the run.

## Table (explore = walk-forward OOS 2012-2017, annual expanding refit; confirm = 2018-2025.09, same refit)

t = trading-day cluster-robust t (frozen gate); vs pool = selected minus unselected; vs control = selected minus
paired leak-free twins; vs drift = selected minus same-time-of-day twin on a random day within +/-20 trading days
(reported only, W2). Pool net mean: explore OOS +0.014 R (n 6,563), confirm -0.037 R (n 8,723).

| model / variant | explore n | net R | t | vs pool | vs control | vs drift | confirm n | net R | t | DSR | years+ | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V1 RIDGE-POS | 3,834 | +0.040 | 1.41 | +0.061 | +0.056 | +0.055 | not run | | | | | FAIL explore (t) |
| V2 HGB-POS | 3,533 | +0.039 | 1.34 | +0.054 | +0.053 | +0.047 | not run | | | | | FAIL explore (t) |
| V3 HGB-TOP3 | 2,236 | +0.088 | 2.22 | +0.111 | +0.065 | +0.095 | 3,087 | -0.012 | -0.37 | 0.000 | 4/8 | FAIL confirm |
| V4 RULEPICK (baseline) | 5,001 | +0.021 | 0.89 | +0.027 | +0.051 | +0.052 | not run | | | | | FAIL explore (t, vs pool) |

V3 confirm detail: gross +0.071 R, cost 0.083 R, stress (overshoot $0.70) -0.059 R; vs pool +0.039 R, vs control
+0.024 R, vs drift +0.015 R, drift-adjusted gross +0.019 R; Spearman(prediction, net R) +0.009 (explore +0.026);
long share 58%; years 2018 -0.084, 2019 -0.061, 2020 +0.028, 2021 +0.031, 2022 -0.105, 2023 -0.010, 2024 +0.047,
2025 +0.163.

Reported additions (coordinator / W2, not gating):
- **Excluding 2025** (confirm, V3): n 2,923, net -0.021 R, t -0.65, vs control +0.023, vs drift +0.011; 3 of 7
  years positive. 2025 (n 164, +0.163 R) is the best year; without it the selection is plainly negative.
- **bps cost version** (dollar costs / 4,000 x entry price): explore V3 +0.150 R; confirm V3 +0.036 R (ex-2025
  +0.028). At 2010-2024 gold prices ($1,200-2,600 against the $4,000 reference) the bps model charges about 30-65% of
  the frozen dollar cost, which flatters old
  years; it does not turn the confirm read into a pass.
- **W2 verdicts**: explore V3 passes W2 (day-summed t 2.20, vs control >= 0); V1, V2, V4 fail. Confirm V3: day-block
  bootstrap one-sided p 0.638, BH-FDR (m = 1, q 0.10) not rejected, 4 of 8 years: W2 FAIL too.

## What this says (and does not)

1. **The learned selector has almost no ranking skill on net R.** Rank correlation of prediction with outcome is
   +0.026 in explore and +0.009 in confirm: the same "AUC about 0.50" story as M1/M2, now with net R as the target
   and with cost, consensus and strategy-momentum features added. The new information did not carry it.
2. **What survives is a small relative lift, not an edge.** In both windows the selected third beats the rejected
   two thirds (+0.11 R explore, +0.04 R confirm) and its random twins (+0.065, +0.024). It is not a cost cut (the
   selected trades' cost is 0.093 R vs 0.094 R for the scored pool in explore), and it is too small to lift a pool
   whose confirm net is -0.037 R above zero.
3. **The explore pass was the expected false positive.** With 4 variants on a pool pre-selected for gross t >= 2,
   one marginal pass (t 2.22) is unremarkable; confirm net is negative in 4 of 8 years.
4. **Confirms the statistics expert's read** of the trend premium: confirm gross of the selected trades is +0.071 R
   and drift-adjusted gross +0.019 R; the gross "trend premium" out of sample is mostly long drift and 2025.

## Retire / do not re-test

- Learned net-R meta-selection of the sprint-200 trend pool (V1-V4, these 33 features) is retired. A re-test needs
  a genuinely new information source (e.g. order flow or positioning that is point in time and not in H-86..88),
  not a new learner.
- Do not count the V3 explore pass as evidence for the trend family.

## Owner-only items (flagged, not assumed)

None decided here. The only lever left for this pool is cost (venue, commission, swap-free account): an owner
decision on instruments and spending.

K added: 4 (MLNET-V1..V4). Program K after this lane: about 3,237 plus other lanes' trials.
