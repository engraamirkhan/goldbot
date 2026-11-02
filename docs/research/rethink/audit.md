# Rethink audit and the C1 re-score (2026-11-02)

Principal software engineer, for the program director and the research director. Amendment C1 was adopted on
2026-11-02 on the independent audit and frozen before any re-score. Its code is ADR 0069. Pre-confirm freeze: commit
`3c3aa00c`. That commit holds the cost model, the gates, the re-score script and `c1-composites.yaml`, and it was made
before the one confirm read. Aggregates only; no market data and no trade row is committed. Nothing at or after
2025-10-01 was read: every lane's own loader asserts this.

**Verdict.**
- Under C1, 20 of the 200 sprint-200 rules pass explore. Under the old rules 1 passed.
- 0 of 24 confirm tests pass: 19 new singles and 5 family composites, BH-FDR at q 0.10, m 24. The smallest p is
  0.026, against a first BH threshold of 0.004.
- S200-TREND-03 had its confirm read earlier, so it goes to TEST-OLD only. The 2006-02..2009 data is not on disk, so
  it is **old-test pending**.
- Survivors: none. No arm, no `exploration_arms.yaml` row, no `arm_rules_c1.py`.
- K: +5, one trial per family composite. The re-score itself adds no trial (declared by the amendment).

## 1. Audit findings (condensed from the amendment)

"0 of 3,330" is mostly a process artefact. Under the old rules, the power to detect a real +0.10 R net edge was 1%.
The flaws, ranked:

1. **Costs were charged in today's dollars against 2010-17 dollar stops.**
   - Gold was $1,160-1,670 then and is about $4,000 now. The 1h ATR in bps is stable, 15-26 bps every year.
   - Screens charged the dollar upper bound (round trip $0.19-0.27, overshoot at least $0.12) and the shipped swap
     (long -$0.60/oz a night, short 0).
   - The demo realised spread is $0.08-0.10 plus $0.080 commission. That matches the point round trip of $0.166; the
     screens charged $0.23, 39% more.
   - Net was understated by about 0.05-0.10 R a trade at 1h-4h. Measured here, the median old cost was 0.06-0.14 R a
     trade per lane, against a median C1 cost of 0.013-0.030 R.
2. **Confirm was deflated at program K.** A pass needed about t 3.6 out of sample.
3. **The same-side "leak-free" twin plus a 0.05 R hurdle** rejects pure directional (TSMOM-type) edges by
   construction: a twin minutes later on the same side carries the same side edge.
   `tests/research/test_c1_gates.py::test_a_planted_side_edge_with_random_timing_passes_the_random_side_control`
   plants a 0.2 R side edge with random timing. The random-side control passes it; the same-side twin rejects it.
4. **There was no ensemble stage.**
5. **Horizons and venues were mixed, and there was a single regime split** (2010-17 bear and range, 2018-25 bull).

### Which past screens used which cost model

| screen | cost model | where |
|---|---|---|
| sprint-200 trend (and mlexec V0, identical by its parity) | cor-2026-10-31.2 upper bound + ruled overshoot + shipped swap | `scripts/sprint200/trend_screen.py:926-944` (`price`) |
| sprint-200 meanrev | same, via `screen_net_r` | `scripts/sprint200/meanrev_screen.py:898-909` |
| sprint-200 macro | same | `scripts/sprint200/macro_screen.py:556-562` |
| sprint-200 patterns | same, swap per leg | `scripts/sprint200/patterns_screen.py:440-465` |
| sprint-200 stats | same, own swap constants -0.60 / 0 | `origin/research/sprint200-stats:scripts/sprint200/stats_screen.py:52, 828-837` |
| sprint-200 time | same | `origin/research/sprint200-time:scripts/sprint200/time_screen.py:653-664` |
| mlexec V1-V5 | same (gated), plus a W2 bps version (reported) | `origin/research/mlprob-mlexec:scripts/mlprob/mlexec_screen.py:727-748` |
| H-17 trend day | frozen $0.23 round trip + $0.70 per stop exit | `scripts/research/h17_screen.py:54` |
| H-84 macro | max($0.23, spread + $0.37) + binding swap | `scripts/research/h84_macro_screen.py:50, 149-155` |
| discovery v2 | measured cells + $0.123 extra + $0.084 overshoot on every trade + swap prior | `scripts/research/autodisc_v2.py:75-78` |
| sprint-3000 ranking | cor-2026-10-31.2 upper bound + shipped swap | `scripts/sprint3000_catalogue.py:187, 194` |
| positioning, meta-label | cor upper bound, one session / per session | `scripts/research/positioning_screen.py:164`, `scripts/research/meta_label_screen.py:168` |
| micro-tick, retail, cross-asset, growth, opp-signals | cor point cells (round trip + overshoot) | `scripts/research/micro_tick_screen.py:98-101`, `docs/research/candidates/*/…_screen.py` (`load_costs`) |
| arms matrix (`run_arm_backtest.py`) | frozen $0.23 / [0, 0.70], or the pinned cor screen | `scripts/run_arm_backtest.py:254, 280` |
| H-89 FOMC confirm | commission $0.08 + own spread | `scripts/research/h89_fomc_confirm.py:37` |

## 2. The C1 machinery

- **Cost.** `goldbot/research/cost_of_record.py` `C1Costs.cost_r`:
  `cost_R = (RT_bps x P + max(0, OS_bps x P x w - simulated gap) - nights x swap_bps(side) x P) / stop`.
  - RT and OS are the ICM point cells per session, in bps at $4,000: all-session 0.415 / 0.275 bps.
  - The swap is from `config/cost_of_record.yaml` `swap_measured.icm` (`swap-icm-2026-11-02`, read from the broker
    terms on 2026-11-02, numbers only): long -52.22, short +36.00 USD/lot/night, Wednesday triple. That is -1.31 and
    +0.90 bps a night.
  - The cells and `cor-2026-10-31.2` are byte-identical, so pinned screens still load.
- **Gates.** `goldbot/research/c1_gates.py`:
  - the random-side twin, in expectation: both sides walked from the same minute with the same stop and exit;
  - the drift-adjusted gross, which subtracts side x in-window drift x hold;
  - day-summed t, years positive, the equal-weight day composite, the one-sided day-bootstrap p and BH-FDR.
- **Re-score.** `scripts/rethink/c1_rescore.py`:
  - each lane's own frozen signal and walk code, unchanged (stats and time are read from their branches);
  - the opposite side walked from each entry minute;
  - 4 worker processes.
- **Parity.** The "old net R" column below is recomputed by the re-score with the lane's own pricing. It matches every
  published explore row exactly (n and net R) in all six lanes, for example TREND-03 449 / +0.206 and MACRO-15
  49 / +0.333.
- **Not re-scored** (cheap re-runs only, as the brief allowed):
  - **H-17:** gross -0.003 R, t -0.05.
  - **H-84:** gross t from -0.59 to +0.84 in every variant. Both fail C1's cost-free legs (gross day t, drift-adjusted
    gross) under any cost model. The C1 short credit, about 0.05 R over H-84's 12-night holds, cannot lift a gross
    t below 1 to 2.
  - **Discovery v1/v2:** a search judged against its own search null (864 specs). Re-pricing it means re-running the
    search and its null, which is not cheap. It is left to the research director as a separate, pre-registered run.
  - **mlexec V1-V5:** not in the brief. V0 is the trend lane exactly.

## 3. Explore re-score, all 200 rules (LEARN 2010-01..2017-12)

How to read the table:
- **old net R** is the lane's published gate: the cost of record upper bound, the ruled overshoot and the shipped swap.
  It is now the stress column.
- **C1 net R** and **day t** are on day-summed R.
- **drift-adj gross** is gross R less side x window drift x hold. The window drift is +17% over 2010-17, which is
  negligible per trade.
- **rand-side excess** is net less its random-side twin.
- **net, no short credit** is a sensitivity column, reported and not gated. It is C1 with the short swap floored at 0,
  because today's short credit reflects 2026 USD rates and may not have existed in 2010-17.
- Pass rule: n >= 100 (60 for 4h/1d), net > 0, day t >= 2, >= 5/8 years, drift-adjusted gross > 0, random-side
  excess >= 0.
- Failed legs: t = day t, side = random-side, drift = drift-adjusted gross.

Per lane (C1 explore passers / net > 0 under the old pricing / net > 0 under C1 / passers that still have net > 0 and
day t >= 2 without the short credit):

| lane | rules | C1 passers | old net > 0 | C1 net > 0 | passers without the credit |
|---|---|---|---|---|---|
| trend | 34 | 12 | 15 | 24 | 9 |
| meanrev | 33 | 0 | 2 | 14 | 0 |
| macro | 33 | 0 | 11 | 20 | 0 |
| patterns | 34 | 3 | 11 | 23 | 3 |
| stats | 33 | 3 | 6 | 19 | 2 |
| time | 33 | 2 | 9 | 17 | 2 |
| **all** | **200** | **20** | 54 | 117 | 16 |

| id | tf | n | old net R | C1 net R | C1 cost R | day t | yrs+ | gross R | drift-adj gross | rand-side excess | net, no short credit | C1 explore |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| TREND-01 | 4h | 329 | +0.047 | +0.168 | 0.021 | 2.02 | 6 | +0.188 | +0.186 | +0.114 | +0.149 | **PASS** |
| TREND-02 | 4h | 237 | +0.048 | +0.149 | 0.019 | 1.50 | 6 | +0.169 | +0.166 | +0.091 | +0.135 | FAIL (t) |
| TREND-03 | 1h | 449 | +0.206 | +0.294 | 0.022 | 3.78 | 8 | +0.316 | +0.313 | +0.215 | +0.287 | **PASS** |
| TREND-04 | 1h | 1395 | +0.042 | +0.139 | 0.023 | 3.32 | 7 | +0.162 | +0.161 | +0.100 | +0.132 | **PASS** |
| TREND-05 | 4h | 398 | -0.031 | +0.081 | 0.021 | 0.99 | 3 | +0.102 | +0.100 | +0.070 | +0.066 | FAIL (t, years) |
| TREND-06 | 4h | 246 | -0.092 | -0.012 | 0.016 | -0.15 | 2 | +0.004 | +0.002 | -0.022 | -0.022 | FAIL (net, t, years, side) |
| TREND-07 | 1h | 971 | -0.023 | +0.028 | 0.016 | 0.50 | 4 | +0.043 | +0.044 | +0.025 | +0.028 | FAIL (t, years) |
| TREND-08 | 1h | 1710 | -0.008 | +0.052 | 0.019 | 1.13 | 5 | +0.071 | +0.072 | +0.026 | +0.052 | FAIL (t) |
| TREND-09 | 1h | 789 | -0.062 | -0.019 | 0.013 | -0.57 | 5 | -0.006 | -0.006 | -0.008 | -0.019 | FAIL (net, t, drift, side) |
| TREND-10 | 1h | 1354 | -0.019 | +0.056 | 0.025 | 2.33 | 7 | +0.081 | +0.080 | +0.071 | +0.056 | **PASS** |
| TREND-11 | 1h | 649 | +0.002 | +0.057 | 0.017 | 0.83 | 3 | +0.074 | +0.073 | +0.030 | +0.057 | FAIL (t, years) |
| TREND-12 | 4h | 358 | -0.010 | +0.096 | 0.020 | 1.29 | 6 | +0.116 | +0.113 | +0.082 | +0.081 | FAIL (t) |
| TREND-13 | 1h | 150 | +0.104 | +0.198 | 0.029 | 0.93 | 4 | +0.227 | +0.228 | +0.031 | +0.198 | FAIL (t, years) |
| TREND-14 | 1h | 955 | -0.080 | -0.035 | 0.014 | -0.98 | 2 | -0.021 | -0.021 | -0.024 | -0.035 | FAIL (net, t, years, drift, side) |
| TREND-15 | 4h | 183 | +0.149 | +0.270 | 0.019 | 2.09 | 6 | +0.289 | +0.288 | +0.203 | +0.251 | **PASS** |
| TREND-16 | 1h | 554 | +0.059 | +0.075 | 0.005 | 2.30 | 7 | +0.081 | +0.080 | +0.059 | +0.075 | **PASS** |
| TREND-17 | 1h | 550 | -0.064 | +0.010 | 0.022 | 0.16 | 5 | +0.032 | +0.029 | -0.002 | +0.007 | FAIL (t, side) |
| TREND-18 | 4h | 380 | -0.088 | -0.018 | 0.014 | -0.28 | 3 | -0.004 | -0.006 | -0.017 | -0.027 | FAIL (net, t, years, drift, side) |
| TREND-19 | 1h | 998 | -0.003 | +0.087 | 0.023 | 2.21 | 5 | +0.110 | +0.109 | +0.073 | +0.082 | **PASS** |
| TREND-20 | 1h | 411 | +0.092 | +0.117 | 0.008 | 2.12 | 5 | +0.124 | +0.124 | +0.067 | +0.117 | **PASS** |
| TREND-21 | 1h | 597 | +0.020 | +0.047 | 0.008 | 1.38 | 5 | +0.056 | +0.055 | +0.051 | +0.047 | FAIL (t) |
| TREND-22 | 1d | 80 | +0.049 | +0.066 | 0.006 | 0.48 | 5 | +0.072 | +0.073 | +0.075 | +0.066 | FAIL (t) |
| TREND-23 | 1h | 424 | -0.042 | -0.014 | 0.009 | -0.37 | 2 | -0.005 | -0.005 | -0.030 | -0.014 | FAIL (net, t, years, drift, side) |
| TREND-24 | 1h | 491 | +0.085 | +0.181 | 0.020 | 2.41 | 7 | +0.201 | +0.200 | +0.079 | +0.172 | **PASS** |
| TREND-25 | 4h | 230 | -0.221 | -0.146 | 0.009 | -1.65 | 2 | -0.137 | -0.135 | -0.201 | -0.159 | FAIL (net, t, years, drift, side) |
| TREND-26 | 1h | 1769 | -0.096 | -0.010 | 0.026 | -0.34 | 4 | +0.016 | +0.016 | +0.033 | -0.010 | FAIL (net, t, years) |
| TREND-27 | 4h | 304 | +0.056 | +0.147 | 0.017 | 1.76 | 5 | +0.164 | +0.163 | +0.049 | +0.134 | FAIL (t) |
| TREND-28 | 4h | 207 | +0.141 | +0.236 | 0.016 | 2.10 | 6 | +0.252 | +0.251 | +0.168 | +0.221 | **PASS** |
| TREND-29 | 1d | 66 | -0.199 | -0.113 | 0.007 | -0.85 | 3 | -0.106 | -0.104 | +0.002 | -0.133 | FAIL (net, t, years, drift) |
| TREND-30 | 1h | 138 | -0.195 | -0.131 | 0.008 | -1.48 | 2 | -0.123 | -0.121 | -0.149 | -0.143 | FAIL (net, t, years, drift, side) |
| TREND-31 | 4h | 607 | +0.044 | +0.129 | 0.016 | 2.39 | 7 | +0.145 | +0.144 | +0.088 | +0.118 | **PASS** |
| TREND-32 | 1h | 2361 | -0.012 | +0.029 | 0.012 | 2.21 | 6 | +0.041 | +0.041 | +0.037 | +0.028 | **PASS** |
| TREND-33 | 1h | 758 | -0.004 | +0.067 | 0.011 | 1.38 | 6 | +0.078 | +0.078 | +0.049 | +0.055 | FAIL (t) |
| TREND-34 | 1h | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | FAIL (n) |
| MEANREV-01 | 1h | 3381 | -0.117 | -0.033 | 0.027 | -1.69 | 2 | -0.006 | -0.006 | -0.005 | -0.033 | FAIL (net, t, years, drift, side) |
| MEANREV-02 | 1h | 1557 | -0.215 | -0.113 | 0.025 | -3.08 | 1 | -0.088 | -0.088 | -0.087 | -0.119 | FAIL (net, t, years, drift, side) |
| MEANREV-03 | 1h | 759 | -0.146 | -0.063 | 0.025 | -1.52 | 3 | -0.038 | -0.038 | -0.049 | -0.063 | FAIL (net, t, years, drift, side) |
| MEANREV-04 | 1h | 239 | -0.017 | +0.045 | 0.020 | 0.83 | 5 | +0.065 | +0.065 | +0.064 | +0.045 | FAIL (t) |
| MEANREV-05 | 1h | 1484 | -0.039 | +0.035 | 0.024 | 1.06 | 6 | +0.059 | +0.060 | +0.023 | +0.035 | FAIL (t) |
| MEANREV-06 | 1h | 1059 | -0.065 | +0.015 | 0.020 | 0.38 | 5 | +0.036 | +0.036 | +0.015 | +0.010 | FAIL (t) |
| MEANREV-07 | 1h | 344 | -0.040 | +0.062 | 0.027 | 0.82 | 4 | +0.089 | +0.090 | +0.075 | +0.057 | FAIL (t, years) |
| MEANREV-08 | 1h | 287 | -0.036 | +0.067 | 0.025 | 0.69 | 4 | +0.091 | +0.091 | +0.058 | +0.059 | FAIL (t, years) |
| MEANREV-09 | 1h | 489 | -0.092 | -0.025 | 0.021 | -0.51 | 2 | -0.004 | -0.003 | -0.025 | -0.025 | FAIL (net, t, years, drift, side) |
| MEANREV-10 | 1h | 42 | +0.324 | +0.384 | 0.008 | 2.11 | 7 | +0.392 | +0.395 | +0.457 | +0.375 | FAIL (n) |
| MEANREV-11 | 1h | 962 | -0.087 | -0.005 | 0.019 | -0.11 | 3 | +0.014 | +0.015 | +0.005 | -0.011 | FAIL (net, t, years) |
| MEANREV-12 | 1h | 3724 | -0.086 | -0.012 | 0.024 | -0.70 | 2 | +0.011 | +0.012 | -0.004 | -0.012 | FAIL (net, t, years, side) |
| MEANREV-13 | 1h | 1634 | -0.090 | -0.002 | 0.028 | -0.06 | 4 | +0.026 | +0.026 | +0.039 | -0.002 | FAIL (net, t, years) |
| MEANREV-14 | 1h | 61 | -0.065 | +0.013 | 0.013 | 0.08 | 3 | +0.026 | +0.028 | +0.126 | +0.005 | FAIL (n, t, years) |
| MEANREV-15 | 1h | 866 | -0.064 | +0.002 | 0.022 | 0.06 | 4 | +0.024 | +0.024 | +0.016 | +0.002 | FAIL (t, years) |
| MEANREV-16 | 1h | 214 | -0.035 | +0.036 | 0.018 | 0.42 | 6 | +0.054 | +0.054 | -0.009 | +0.031 | FAIL (t, side) |
| MEANREV-17 | 1h | 8117 | -0.124 | -0.039 | 0.028 | -3.92 | 1 | -0.011 | -0.010 | -0.010 | -0.039 | FAIL (net, t, years, drift, side) |
| MEANREV-18 | 1h | 193 | -0.030 | +0.021 | 0.011 | 0.28 | 4 | +0.032 | +0.031 | -0.015 | +0.015 | FAIL (t, years, side) |
| MEANREV-19 | 1h | 546 | -0.126 | -0.054 | 0.023 | -0.89 | 2 | -0.031 | -0.031 | -0.074 | -0.054 | FAIL (net, t, years, drift, side) |
| MEANREV-20 | 1h | 1170 | -0.100 | -0.022 | 0.025 | -0.54 | 3 | +0.003 | +0.003 | -0.006 | -0.022 | FAIL (net, t, years, side) |
| MEANREV-21 | 1h | 241 | -0.046 | +0.050 | 0.015 | 0.40 | 4 | +0.065 | +0.067 | -0.014 | +0.037 | FAIL (t, years, side) |
| MEANREV-22 | 1h | 1937 | -0.071 | -0.022 | 0.016 | -1.21 | 2 | -0.007 | -0.007 | +0.002 | -0.022 | FAIL (net, t, years, drift) |
| MEANREV-23 | 1h | 357 | -0.233 | -0.166 | 0.017 | -3.57 | 0 | -0.149 | -0.148 | -0.118 | -0.170 | FAIL (net, t, years, drift, side) |
| MEANREV-24 | 1h | 98 | -0.123 | -0.071 | 0.007 | -0.62 | 3 | -0.064 | -0.063 | -0.115 | -0.080 | FAIL (n, net, t, years, drift, side) |
| MEANREV-25 | 1h | 1 | +0.010 | +0.063 | 0.028 | nan | 1 | +0.090 | +0.081 | +0.069 | +0.063 | FAIL (n, t, years) |
| MEANREV-26 | 1h | 52 | -0.130 | -0.103 | 0.001 | -1.39 | 3 | -0.101 | -0.100 | -0.098 | -0.110 | FAIL (n, net, t, years, drift, side) |
| MEANREV-27 | 1h | 110 | -0.162 | -0.118 | 0.010 | -1.30 | 2 | -0.109 | -0.109 | -0.108 | -0.123 | FAIL (net, t, years, drift, side) |
| MEANREV-28 | 1h | 1398 | -0.161 | -0.057 | 0.027 | -1.75 | 3 | -0.031 | -0.031 | -0.028 | -0.064 | FAIL (net, t, years, drift, side) |
| MEANREV-29 | 1h | 310 | -0.014 | -0.002 | 0.004 | -0.05 | 4 | +0.002 | +0.003 | -0.016 | -0.002 | FAIL (net, t, years, side) |
| MEANREV-30 | 1h | 298 | -0.173 | -0.100 | 0.023 | -1.54 | 2 | -0.078 | -0.078 | -0.100 | -0.100 | FAIL (net, t, years, drift, side) |
| MEANREV-31 | 1h | 385 | -0.024 | +0.071 | 0.029 | 1.53 | 6 | +0.100 | +0.100 | +0.101 | +0.070 | FAIL (t) |
| MEANREV-32 | 1h | 695 | -0.077 | +0.008 | 0.024 | 0.23 | 4 | +0.032 | +0.033 | +0.035 | +0.006 | FAIL (t, years) |
| MEANREV-33 | 1h | 158 | -0.062 | -0.031 | 0.003 | -0.59 | 4 | -0.028 | -0.027 | -0.026 | -0.037 | FAIL (net, t, years, drift, side) |
| MACRO-01 | 1h | 360 | -0.131 | -0.032 | 0.030 | -0.46 | 3 | -0.003 | -0.003 | +0.004 | -0.032 | FAIL (net, t, years, drift) |
| MACRO-02 | 1h | 259 | -0.294 | -0.191 | 0.031 | -2.40 | 2 | -0.160 | -0.160 | -0.095 | -0.191 | FAIL (net, t, years, drift, side) |
| MACRO-03 | 1h | 139 | +0.063 | +0.147 | 0.026 | 1.34 | 8 | +0.173 | +0.173 | +0.138 | +0.147 | FAIL (t) |
| MACRO-04 | 1h | 119 | +0.057 | +0.178 | 0.035 | 1.40 | 5 | +0.213 | +0.213 | +0.178 | +0.174 | FAIL (t) |
| MACRO-05 | 1h | 959 | -0.123 | -0.022 | 0.030 | -0.52 | 3 | +0.008 | +0.008 | +0.020 | -0.023 | FAIL (net, t, years) |
| MACRO-06 | 1h | 230 | -0.097 | +0.019 | 0.036 | 0.29 | 4 | +0.055 | +0.055 | +0.056 | +0.019 | FAIL (t, years) |
| MACRO-07 | 1h | 145 | -0.134 | -0.001 | 0.041 | -0.02 | 5 | +0.040 | +0.040 | +0.040 | -0.001 | FAIL (net, t) |
| MACRO-08 | 1h | 69 | -0.112 | +0.018 | 0.039 | 0.15 | 4 | +0.057 | +0.057 | +0.058 | +0.018 | FAIL (n, t, years) |
| MACRO-09 | 1h | 561 | -0.169 | -0.044 | 0.038 | -1.05 | 3 | -0.006 | -0.006 | -0.006 | -0.044 | FAIL (net, t, years, drift, side) |
| MACRO-10 | 1h | 335 | +0.026 | +0.097 | 0.021 | 1.11 | 4 | +0.118 | +0.118 | +0.029 | +0.097 | FAIL (t, years) |
| MACRO-11 | 1h | 242 | -0.093 | -0.023 | 0.020 | -0.19 | 4 | -0.003 | -0.003 | -0.028 | -0.024 | FAIL (net, t, years, drift, side) |
| MACRO-12 | 1h | 136 | +0.082 | +0.131 | 0.015 | 1.18 | 7 | +0.145 | +0.146 | +0.074 | +0.131 | FAIL (t) |
| MACRO-13 | 1h | 882 | -0.004 | +0.060 | 0.019 | 1.16 | 6 | +0.079 | +0.079 | +0.033 | +0.060 | FAIL (t) |
| MACRO-14 | 1h | 130 | +0.053 | +0.066 | 0.004 | 0.96 | 6 | +0.071 | +0.070 | +0.057 | +0.066 | FAIL (t) |
| MACRO-15 | 1h | 49 | +0.333 | +0.345 | 0.004 | 2.84 | 6 | +0.349 | +0.349 | +0.332 | +0.345 | FAIL (n) |
| MACRO-16 | 1h | 310 | +0.025 | +0.037 | 0.004 | 0.87 | 6 | +0.042 | +0.041 | +0.027 | +0.037 | FAIL (t) |
| MACRO-17 | 1h | 473 | -0.111 | -0.071 | 0.015 | -2.66 | 3 | -0.055 | -0.059 | -0.069 | -0.071 | FAIL (net, t, years, drift, side) |
| MACRO-18 | 1h | 202 | +0.000 | +0.071 | 0.018 | 0.71 | 3 | +0.088 | +0.088 | +0.092 | +0.067 | FAIL (t, years) |
| MACRO-19 | 1h | 215 | -0.110 | -0.055 | 0.017 | -0.69 | 3 | -0.038 | -0.038 | -0.084 | -0.055 | FAIL (net, t, years, drift, side) |
| MACRO-20 | 1h | 117 | +0.073 | +0.083 | 0.003 | 1.59 | 7 | +0.086 | +0.087 | +0.065 | +0.083 | FAIL (t) |
| MACRO-21 | 1h | 117 | -0.058 | -0.046 | 0.003 | -0.77 | 2 | -0.043 | -0.043 | -0.065 | -0.046 | FAIL (net, t, years, drift, side) |
| MACRO-22 | 1h | 229 | -0.009 | +0.001 | 0.003 | 0.04 | 5 | +0.005 | +0.004 | -0.005 | +0.001 | FAIL (t, side) |
| MACRO-23 | 1h | 157 | -0.063 | -0.053 | 0.003 | -1.29 | 3 | -0.049 | -0.049 | -0.054 | -0.053 | FAIL (net, t, years, drift, side) |
| MACRO-24 | 1h | 176 | -0.073 | +0.025 | 0.030 | 0.25 | 4 | +0.055 | +0.055 | +0.051 | +0.025 | FAIL (t, years) |
| MACRO-25 | 1h | 223 | -0.191 | -0.109 | 0.026 | -1.26 | 1 | -0.084 | -0.084 | -0.091 | -0.110 | FAIL (net, t, years, drift, side) |
| MACRO-26 | 1h | 219 | -0.258 | -0.127 | 0.042 | -1.87 | 1 | -0.085 | -0.085 | -0.086 | -0.127 | FAIL (net, t, years, drift, side) |
| MACRO-27 | 1h | 465 | +0.009 | +0.071 | 0.018 | 1.10 | 5 | +0.090 | +0.090 | +0.034 | +0.071 | FAIL (t) |
| MACRO-28 | 1h | 181 | +0.059 | +0.072 | 0.004 | 1.19 | 5 | +0.076 | +0.075 | +0.054 | +0.072 | FAIL (t) |
| MACRO-29 | 1h | 358 | -0.059 | +0.041 | 0.020 | 0.97 | 6 | +0.061 | +0.059 | +0.055 | +0.029 | FAIL (t) |
| MACRO-30 | 1h | 188 | -0.036 | +0.059 | 0.019 | 0.93 | 5 | +0.078 | +0.077 | +0.049 | +0.047 | FAIL (t) |
| MACRO-31 | 1h | 479 | -0.082 | +0.019 | 0.031 | 0.30 | 4 | +0.049 | +0.049 | +0.066 | +0.019 | FAIL (t, years) |
| MACRO-32 | 1h | 332 | -0.106 | -0.010 | 0.028 | -0.13 | 3 | +0.018 | +0.018 | +0.033 | -0.011 | FAIL (net, t, years) |
| MACRO-33 | 1h | 360 | -0.016 | +0.040 | 0.017 | 0.51 | 5 | +0.057 | +0.057 | +0.033 | +0.040 | FAIL (t) |
| PATTERNS-01 | 1h | 1181 | +0.017 | +0.167 | 0.038 | 2.22 | 7 | +0.205 | +0.202 | +0.053 | +0.157 | **PASS** |
| PATTERNS-02 | 1h | 1445 | -0.118 | -0.004 | 0.030 | -0.12 | 3 | +0.026 | +0.025 | +0.006 | -0.010 | FAIL (net, t, years) |
| PATTERNS-03 | 1h | 452 | -0.053 | +0.044 | 0.024 | 0.66 | 5 | +0.068 | +0.069 | +0.056 | +0.038 | FAIL (t) |
| PATTERNS-04 | 4h | 354 | -0.098 | +0.030 | 0.023 | 0.28 | 4 | +0.053 | +0.052 | +0.076 | +0.012 | FAIL (t, years) |
| PATTERNS-05 | 15m | 1640 | -0.105 | +0.047 | 0.048 | 0.96 | 5 | +0.094 | +0.094 | +0.019 | +0.047 | FAIL (t) |
| PATTERNS-06 | 1h | 370 | +0.179 | +0.347 | 0.045 | 2.45 | 6 | +0.391 | +0.388 | +0.243 | +0.337 | **PASS** |
| PATTERNS-07 | 1h | 438 | -0.143 | -0.034 | 0.030 | -0.59 | 3 | -0.004 | -0.004 | -0.061 | -0.039 | FAIL (net, t, years, drift, side) |
| PATTERNS-08 | 4h | 231 | -0.046 | +0.052 | 0.021 | 0.61 | 4 | +0.073 | +0.071 | +0.060 | +0.042 | FAIL (t, years) |
| PATTERNS-09 | 15m | 4277 | -0.154 | -0.007 | 0.046 | -0.38 | 4 | +0.039 | +0.039 | +0.031 | -0.007 | FAIL (net, t, years) |
| PATTERNS-10 | 1h | 173 | -0.169 | -0.058 | 0.032 | -0.56 | 2 | -0.027 | -0.029 | -0.069 | -0.063 | FAIL (net, t, years, drift, side) |
| PATTERNS-11 | 1h | 704 | +0.041 | +0.160 | 0.031 | 1.96 | 6 | +0.192 | +0.189 | +0.067 | +0.153 | FAIL (t) |
| PATTERNS-12 | 4h | 204 | -0.069 | +0.004 | 0.015 | 0.05 | 4 | +0.019 | +0.019 | -0.026 | -0.004 | FAIL (t, years, side) |
| PATTERNS-13 | 15m | 995 | -0.066 | +0.040 | 0.033 | 1.01 | 5 | +0.073 | +0.073 | +0.027 | +0.040 | FAIL (t) |
| PATTERNS-14 | 1h | 773 | -0.161 | -0.020 | 0.039 | -0.39 | 3 | +0.019 | +0.019 | +0.008 | -0.026 | FAIL (net, t, years) |
| PATTERNS-15 | 1h | 513 | +0.053 | +0.171 | 0.031 | 1.94 | 7 | +0.202 | +0.200 | +0.126 | +0.165 | FAIL (t) |
| PATTERNS-16 | 1h | 209 | +0.054 | +0.144 | 0.024 | 1.63 | 6 | +0.167 | +0.166 | +0.098 | +0.139 | FAIL (t) |
| PATTERNS-17 | 4h | 265 | +0.087 | +0.147 | 0.010 | 1.61 | 6 | +0.157 | +0.156 | +0.105 | +0.138 | FAIL (t) |
| PATTERNS-18 | 1h | 489 | -0.079 | +0.010 | 0.023 | 0.17 | 5 | +0.033 | +0.034 | +0.004 | +0.005 | FAIL (t) |
| PATTERNS-19 | 1h | 1223 | +0.082 | +0.209 | 0.031 | 3.01 | 8 | +0.240 | +0.239 | +0.123 | +0.200 | **PASS** |
| PATTERNS-20 | 4h | 382 | -0.037 | +0.042 | 0.016 | 0.65 | 7 | +0.058 | +0.056 | +0.046 | +0.032 | FAIL (t) |
| PATTERNS-21 | 15m | 2596 | -0.143 | +0.002 | 0.044 | 0.04 | 3 | +0.046 | +0.046 | +0.003 | +0.002 | FAIL (t, years) |
| PATTERNS-22 | 1h | 1139 | -0.176 | -0.025 | 0.037 | -0.35 | 3 | +0.013 | +0.012 | +0.022 | -0.034 | FAIL (net, t, years) |
| PATTERNS-23 | 1h | 1252 | -0.083 | -0.021 | 0.018 | -0.59 | 3 | -0.003 | -0.003 | -0.000 | -0.022 | FAIL (net, t, years, drift, side) |
| PATTERNS-24 | 4h | 140 | +0.065 | +0.160 | 0.021 | 1.42 | 8 | +0.181 | +0.177 | +0.079 | +0.150 | FAIL (t) |
| PATTERNS-25 | 15m | 660 | -0.263 | -0.079 | 0.061 | -0.99 | 4 | -0.018 | -0.018 | +0.009 | -0.079 | FAIL (net, t, years, drift) |
| PATTERNS-26 | 1h | 380 | -0.212 | -0.061 | 0.043 | -0.53 | 3 | -0.018 | -0.020 | -0.009 | -0.067 | FAIL (net, t, years, drift, side) |
| PATTERNS-27 | 1h | 485 | -0.067 | +0.024 | 0.027 | 0.53 | 4 | +0.052 | +0.052 | +0.055 | +0.024 | FAIL (t, years) |
| PATTERNS-28 | 1h | 125 | +0.058 | +0.165 | 0.028 | 1.08 | 5 | +0.193 | +0.189 | +0.119 | +0.158 | FAIL (t) |
| PATTERNS-29 | 4h | 90 | +0.265 | +0.348 | 0.012 | 1.70 | 4 | +0.360 | +0.357 | +0.283 | +0.334 | FAIL (t, years) |
| PATTERNS-30 | 1h | 576 | -0.113 | +0.068 | 0.047 | 0.55 | 4 | +0.115 | +0.113 | -0.130 | +0.057 | FAIL (t, years, side) |
| PATTERNS-31 | 4h | 307 | +0.017 | +0.097 | 0.024 | 1.13 | 5 | +0.121 | +0.119 | +0.064 | +0.094 | FAIL (t) |
| PATTERNS-32 | 1h | 317 | -0.119 | +0.001 | 0.030 | 0.01 | 4 | +0.031 | +0.028 | +0.083 | -0.007 | FAIL (t, years) |
| PATTERNS-33 | 1h | 1024 | -0.149 | -0.039 | 0.030 | -0.90 | 3 | -0.009 | -0.009 | +0.016 | -0.044 | FAIL (net, t, years, drift) |
| PATTERNS-34 | 4h | 75 | -0.309 | -0.167 | 0.025 | -1.00 | 3 | -0.142 | -0.143 | +0.033 | -0.187 | FAIL (net, t, years, drift) |
| STATS-01 | 1d | 78 | +0.012 | +0.096 | 0.010 | 0.73 | 4 | +0.106 | +0.106 | +0.065 | +0.081 | FAIL (t, years) |
| STATS-02 | 1d | 78 | -0.320 | -0.246 | 0.011 | -2.53 | 1 | -0.235 | -0.236 | -0.214 | -0.259 | FAIL (net, t, years, drift, side) |
| STATS-03 | 4h | 1125 | +0.004 | +0.072 | 0.013 | 1.94 | 6 | +0.085 | +0.085 | +0.069 | +0.063 | FAIL (t) |
| STATS-04 | 1d | 64 | -0.022 | +0.068 | 0.019 | 0.48 | 4 | +0.087 | +0.082 | +0.060 | +0.055 | FAIL (t, years) |
| STATS-05 | 1d | 51 | -0.036 | +0.037 | 0.015 | 0.29 | 6 | +0.052 | +0.048 | +0.025 | +0.028 | FAIL (n, t) |
| STATS-06 | 4h | 74 | -0.034 | +0.056 | 0.026 | 0.39 | 4 | +0.083 | +0.076 | +0.059 | +0.050 | FAIL (t, years) |
| STATS-07 | 4h | 300 | +0.038 | +0.113 | 0.014 | 1.59 | 6 | +0.127 | +0.126 | +0.112 | +0.103 | FAIL (t) |
| STATS-08 | 1h | 987 | -0.082 | -0.005 | 0.019 | -0.12 | 2 | +0.014 | +0.013 | +0.007 | -0.010 | FAIL (net, t, years) |
| STATS-09 | 1d | 53 | -0.070 | +0.012 | 0.014 | 0.08 | 3 | +0.025 | +0.023 | +0.059 | -0.002 | FAIL (n, t, years) |
| STATS-10 | 1h | 425 | -0.020 | +0.054 | 0.016 | 0.92 | 6 | +0.070 | +0.070 | +0.057 | +0.047 | FAIL (t) |
| STATS-11 | 1h | 140 | -0.070 | -0.011 | 0.014 | -0.11 | 2 | +0.003 | +0.003 | +0.006 | -0.016 | FAIL (net, t, years) |
| STATS-12 | 1h | 310 | -0.257 | -0.182 | 0.019 | -2.59 | 2 | -0.163 | -0.163 | -0.164 | -0.187 | FAIL (net, t, years, drift, side) |
| STATS-13 | 1h | 446 | -0.009 | +0.062 | 0.017 | 1.03 | 5 | +0.079 | +0.079 | +0.040 | +0.057 | FAIL (t) |
| STATS-14 | 1h | 801 | -0.170 | -0.089 | 0.019 | -2.04 | 1 | -0.070 | -0.069 | -0.116 | -0.095 | FAIL (net, t, years, drift, side) |
| STATS-15 | 1d | 24 | -0.310 | -0.194 | 0.022 | -1.13 | 1 | -0.173 | -0.178 | -0.099 | -0.209 | FAIL (n, net, t, years, drift, side) |
| STATS-16 | 4h | 151 | -0.060 | +0.005 | 0.009 | 0.05 | 3 | +0.014 | +0.015 | -0.021 | -0.006 | FAIL (t, years, side) |
| STATS-17 | 1h | 2010 | -0.025 | +0.009 | 0.010 | 1.15 | 5 | +0.019 | +0.019 | +0.019 | +0.009 | FAIL (t) |
| STATS-18 | 1d | 275 | -0.081 | -0.003 | 0.009 | -0.04 | 3 | +0.006 | +0.007 | -0.024 | -0.018 | FAIL (net, t, years, side) |
| STATS-19 | 1d | 208 | -0.040 | +0.039 | 0.011 | 0.57 | 5 | +0.050 | +0.049 | +0.058 | +0.024 | FAIL (t) |
| STATS-20 | 1d | 202 | -0.085 | -0.010 | 0.007 | -0.14 | 4 | -0.004 | -0.002 | +0.009 | -0.026 | FAIL (net, t, years, drift) |
| STATS-21 | 1d | 269 | -0.114 | -0.037 | 0.009 | -0.56 | 4 | -0.028 | -0.027 | -0.064 | -0.052 | FAIL (net, t, years, drift, side) |
| STATS-22 | 1d | 130 | -0.156 | -0.078 | 0.012 | -1.01 | 3 | -0.066 | -0.068 | -0.057 | -0.090 | FAIL (net, t, years, drift, side) |
| STATS-23 | 4h | 1141 | -0.114 | -0.044 | 0.012 | -1.27 | 0 | -0.032 | -0.031 | -0.027 | -0.054 | FAIL (net, t, years, drift, side) |
| STATS-24 | 1h | 3892 | -0.023 | +0.040 | 0.020 | 2.32 | 5 | +0.060 | +0.060 | +0.032 | +0.039 | **PASS** |
| STATS-25 | 1h | 3682 | +0.016 | +0.079 | 0.019 | 4.64 | 6 | +0.098 | +0.098 | +0.073 | +0.078 | **PASS** |
| STATS-26 | 4h | 928 | +0.024 | +0.070 | 0.006 | 2.05 | 5 | +0.075 | +0.076 | +0.052 | +0.062 | **PASS** |
| STATS-27 | 4h | 777 | -0.029 | +0.023 | 0.009 | 0.65 | 4 | +0.033 | +0.033 | +0.042 | +0.017 | FAIL (t, years) |
| STATS-28 | 1d | 210 | -0.167 | -0.089 | 0.015 | -1.34 | 2 | -0.073 | -0.076 | -0.100 | -0.101 | FAIL (net, t, years, drift, side) |
| STATS-29 | 1d | 148 | -0.076 | -0.011 | -0.005 | -0.11 | 2 | -0.017 | -0.008 | -0.008 | -0.033 | FAIL (net, t, years, drift, side) |
| STATS-30 | 1d | 103 | -0.135 | -0.054 | 0.013 | -0.46 | 2 | -0.042 | -0.043 | -0.097 | -0.068 | FAIL (net, t, years, drift, side) |
| STATS-31 | 4h | 670 | +0.021 | +0.096 | 0.016 | 2.00 | 7 | +0.112 | +0.111 | +0.089 | +0.087 | FAIL (t) |
| STATS-32 | 1d | 70 | -0.024 | +0.047 | 0.004 | 0.37 | 4 | +0.051 | +0.054 | +0.059 | +0.030 | FAIL (t, years) |
| STATS-33 | 4h | 281 | -0.033 | +0.037 | 0.015 | 0.51 | 4 | +0.052 | +0.051 | +0.037 | +0.029 | FAIL (t, years) |
| TIME-01 | 1h | 600 | -0.053 | -0.004 | 0.015 | -0.09 | 3 | +0.011 | +0.011 | -0.020 | -0.004 | FAIL (net, t, years, side) |
| TIME-02 | 1h | 638 | +0.060 | +0.104 | 0.014 | 2.33 | 6 | +0.118 | +0.118 | +0.059 | +0.104 | **PASS** |
| TIME-03 | 1h | 1478 | -0.029 | +0.010 | 0.012 | 0.73 | 4 | +0.022 | +0.022 | +0.016 | +0.010 | FAIL (t, years) |
| TIME-04 | 1h | 627 | -0.008 | +0.026 | 0.011 | 1.81 | 6 | +0.037 | +0.037 | +0.039 | +0.026 | FAIL (t) |
| TIME-05 | 1h | 706 | +0.011 | +0.052 | 0.013 | 1.89 | 6 | +0.064 | +0.064 | +0.051 | +0.052 | FAIL (t) |
| TIME-06 | 1h | 187 | -0.025 | +0.015 | 0.012 | 0.43 | 4 | +0.027 | +0.027 | +0.031 | +0.015 | FAIL (t, years) |
| TIME-07 | 1h | 615 | +0.008 | +0.047 | 0.012 | 2.02 | 6 | +0.060 | +0.060 | +0.053 | +0.047 | **PASS** |
| TIME-08 | 1h | 167 | -0.017 | +0.031 | 0.015 | 0.57 | 5 | +0.046 | +0.046 | +0.045 | +0.031 | FAIL (t) |
| TIME-09 | 1h | 151 | -0.160 | -0.117 | 0.013 | -1.89 | 1 | -0.104 | -0.103 | -0.123 | -0.117 | FAIL (net, t, years, drift, side) |
| TIME-10 | 1h | 309 | +0.001 | +0.049 | 0.015 | 0.65 | 5 | +0.063 | +0.063 | +0.028 | +0.049 | FAIL (t) |
| TIME-11 | 1h | 136 | +0.017 | +0.063 | 0.014 | 0.67 | 5 | +0.078 | +0.078 | +0.080 | +0.063 | FAIL (t) |
| TIME-12 | 1h | 189 | -0.176 | -0.138 | 0.012 | -1.91 | 3 | -0.126 | -0.126 | -0.133 | -0.138 | FAIL (net, t, years, drift, side) |
| TIME-13 | 1h | 783 | -0.050 | -0.011 | 0.012 | -0.39 | 3 | +0.001 | +0.001 | -0.003 | -0.011 | FAIL (net, t, years, side) |
| TIME-14 | 1h | 81 | -0.006 | +0.028 | 0.011 | 0.34 | 4 | +0.040 | +0.040 | +0.020 | +0.028 | FAIL (n, t, years) |
| TIME-15 | 1h | 201 | -0.049 | -0.006 | 0.013 | -0.09 | 4 | +0.008 | +0.007 | -0.017 | -0.006 | FAIL (net, t, years, side) |
| TIME-16 | 1h | 216 | -0.126 | -0.082 | 0.014 | -0.91 | 3 | -0.068 | -0.068 | -0.107 | -0.082 | FAIL (net, t, years, drift, side) |
| TIME-17 | 1h | 190 | +0.000 | +0.046 | 0.014 | 0.60 | 4 | +0.061 | +0.061 | +0.031 | +0.046 | FAIL (t, years) |
| TIME-18 | 1h | 190 | -0.025 | +0.019 | 0.013 | 0.33 | 3 | +0.032 | +0.032 | +0.024 | +0.019 | FAIL (t, years) |
| TIME-19 | 1h | 127 | -0.079 | -0.041 | 0.012 | -0.38 | 5 | -0.030 | -0.034 | -0.176 | -0.041 | FAIL (net, t, drift, side) |
| TIME-20 | 1h | 110 | -0.113 | -0.051 | 0.019 | -0.62 | 4 | -0.032 | -0.032 | -0.031 | -0.051 | FAIL (net, t, years, drift, side) |
| TIME-21 | 1h | 160 | -0.127 | -0.082 | 0.014 | -1.62 | 2 | -0.069 | -0.069 | -0.059 | -0.082 | FAIL (net, t, years, drift, side) |
| TIME-22 | 1h | 240 | -0.111 | -0.057 | 0.015 | -0.83 | 4 | -0.042 | -0.039 | +0.017 | -0.058 | FAIL (net, t, years, drift) |
| TIME-23 | 1h | 144 | +0.029 | +0.082 | 0.016 | 0.69 | 4 | +0.098 | +0.095 | -0.017 | +0.082 | FAIL (t, years, side) |
| TIME-24 | 1h | 154 | -0.042 | -0.005 | 0.011 | -0.05 | 3 | +0.007 | +0.003 | -0.021 | -0.005 | FAIL (net, t, years, side) |
| TIME-25 | 1h | 97 | -0.094 | -0.051 | 0.013 | -0.72 | 4 | -0.038 | -0.037 | -0.061 | -0.051 | FAIL (n, net, t, years, drift, side) |
| TIME-26 | 1h | 223 | -0.144 | -0.052 | 0.016 | -1.03 | 3 | -0.036 | -0.036 | -0.022 | -0.065 | FAIL (net, t, years, drift, side) |
| TIME-27 | 1h | 85 | -0.216 | -0.166 | 0.016 | -1.87 | 4 | -0.150 | -0.150 | -0.179 | -0.166 | FAIL (n, net, t, years, drift, side) |
| TIME-28 | 1h | 431 | -0.029 | +0.011 | 0.013 | 0.36 | 5 | +0.024 | +0.024 | +0.013 | +0.011 | FAIL (t) |
| TIME-29 | 1h | 930 | -0.075 | -0.035 | 0.012 | -1.69 | 2 | -0.023 | -0.023 | -0.016 | -0.035 | FAIL (net, t, years, drift, side) |
| TIME-30 | 1h | 150 | -0.050 | +0.002 | 0.017 | 0.04 | 4 | +0.019 | +0.019 | +0.044 | +0.002 | FAIL (t, years) |
| TIME-31 | 1h | 127 | +0.098 | +0.129 | 0.008 | 1.16 | 5 | +0.137 | +0.138 | +0.114 | +0.128 | FAIL (t) |
| TIME-32 | 1h | 191 | +0.062 | +0.113 | 0.016 | 1.12 | 6 | +0.129 | +0.127 | +0.038 | +0.113 | FAIL (t) |
| TIME-33 | 1h | 209 | -0.090 | -0.044 | 0.015 | -1.39 | 3 | -0.028 | -0.029 | -0.045 | -0.044 | FAIL (net, t, years, drift, side) |

## 4. Ensemble stage (frozen in `c1-composites.yaml` at `3c3aa00c`, before the confirm read)

Per family (= sprint-200 lane): an equal-weight day composite of the C1 explore passers plus the members with
day-summed gross t >= 2. A rule whose confirm was already read is excluded (S200-TREND-03). A family with fewer than
two members has no composite: macro has one, MACRO-15, which has gross day t 2.87 but only 49 trades and failed
explore on n. Each composite is one trial: K +5.

| composite | members | explore day net R (t), years+ |
|---|---|---|
| C1-COMP-TREND | 01, 04, 10, 15, 16, 19, 20, 24, 28, 31, 32 (all passers) | +0.036 (6.02), 8/8 |
| C1-COMP-MEANREV | 10, 31 (gross t >= 2 only) | +0.065 (2.22), 8/8 |
| C1-COMP-PATTERNS | 01, 06, 19 (passers); 09, 11, 15 (gross t >= 2) | +0.061 (3.41), 7/8 |
| C1-COMP-STATS | 24, 25, 26 (passers); 03, 17, 31 (gross t >= 2) | +0.052 (5.18), 8/8 |
| C1-COMP-TIME | 02, 07 (passers); 04, 05 (gross t >= 2) | +0.024 (3.96), 6/8 |

## 5. Confirm (2018-01..2025-09, read once; BH-FDR q 0.10 over m = 24)

The p column is the one-sided day-bootstrap p of the confirm net (10,000 resamples). A pass needs BH rejection, net > 0,
>= 5/8 years and n >= 100. The composites' R is per day (the mean over members).

| test | kind | explore C1 net R (day t) | confirm n | confirm C1 net R | day t | p (1-sided) | yrs+ /8 | ex-2025 net R | gross R | drift-adj gross R | rand-side excess | BH reject | confirm |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S200-PATTERNS-01 | single | +0.167 (2.22) | 1089 | +0.111 | 1.62 | 0.052 | 5 | +0.079 | +0.152 | +0.128 | +0.084 | no | FAIL |
| S200-PATTERNS-06 | single | +0.347 (2.45) | 363 | +0.053 | 0.45 | 0.336 | 6 | -0.005 | +0.102 | +0.053 | -0.052 | no | FAIL |
| S200-PATTERNS-19 | single | +0.209 (3.01) | 1180 | +0.052 | 0.81 | 0.202 | 5 | +0.041 | +0.090 | +0.068 | +0.008 | no | FAIL |
| S200-STATS-24 | single | +0.040 (2.32) | 4880 | -0.038 | -2.71 | 0.996 | 1 | -0.030 | -0.016 | -0.014 | -0.020 | no | FAIL |
| S200-STATS-25 | single | +0.079 (4.64) | 4742 | -0.001 | -0.07 | 0.531 | 4 | +0.009 | +0.018 | +0.021 | +0.013 | no | FAIL |
| S200-STATS-26 | single | +0.070 (2.05) | 1135 | -0.023 | -0.77 | 0.775 | 3 | -0.010 | -0.021 | -0.006 | -0.032 | no | FAIL |
| S200-TIME-02 | single | +0.104 (2.33) | 642 | +0.046 | 1.24 | 0.109 | 5 | +0.042 | +0.061 | +0.060 | +0.067 | no | FAIL |
| S200-TIME-07 | single | +0.047 (2.02) | 451 | +0.046 | 1.96 | 0.026 | 6 | +0.041 | +0.059 | +0.060 | +0.059 | no | FAIL |
| S200-TREND-01 | single | +0.168 (2.02) | 345 | +0.055 | 0.64 | 0.264 | 5 | +0.046 | +0.077 | +0.051 | +0.035 | no | FAIL |
| S200-TREND-04 | single | +0.139 (3.32) | 1270 | +0.027 | 0.63 | 0.264 | 3 | +0.033 | +0.053 | +0.045 | +0.027 | no | FAIL |
| S200-TREND-10 | single | +0.056 (2.33) | 1478 | +0.016 | 0.74 | 0.231 | 7 | +0.012 | +0.041 | +0.040 | +0.031 | no | FAIL |
| S200-TREND-15 | single | +0.270 (2.09) | 184 | +0.007 | 0.06 | 0.492 | 5 | +0.044 | +0.032 | +0.008 | -0.020 | no | FAIL |
| S200-TREND-16 | single | +0.075 (2.30) | 537 | +0.024 | 0.76 | 0.225 | 4 | +0.016 | +0.030 | +0.027 | +0.030 | no | FAIL |
| S200-TREND-19 | single | +0.087 (2.21) | 943 | +0.042 | 1.05 | 0.153 | 4 | +0.053 | +0.069 | +0.064 | +0.058 | no | FAIL |
| S200-TREND-20 | single | +0.117 (2.12) | 391 | +0.085 | 1.70 | 0.043 | 4 | +0.087 | +0.094 | +0.093 | +0.074 | no | FAIL |
| S200-TREND-24 | single | +0.181 (2.41) | 493 | -0.018 | -0.26 | 0.603 | 4 | -0.039 | +0.011 | -0.011 | -0.010 | no | FAIL |
| S200-TREND-28 | single | +0.236 (2.10) | 223 | -0.147 | -1.63 | 0.947 | 1 | -0.139 | -0.132 | -0.139 | -0.174 | no | FAIL |
| S200-TREND-31 | single | +0.129 (2.39) | 613 | -0.007 | -0.14 | 0.561 | 4 | -0.021 | +0.015 | -0.002 | +0.018 | no | FAIL |
| S200-TREND-32 | single | +0.029 (2.21) | 2286 | +0.000 | 0.01 | 0.497 | 3 | +0.003 | +0.014 | +0.011 | +0.001 | no | FAIL |
| C1-COMP-TREND | composite | composite | 8763 | +0.005 | 0.92 | 0.184 | 6 | +0.005 | +0.013 | n/a | +0.007 | no | FAIL |
| C1-COMP-MEANREV | composite | composite | 633 | -0.047 | -1.68 | 0.955 | 1 | -0.015 | -0.017 | n/a | -0.023 | no | FAIL |
| C1-COMP-PATTERNS | composite | composite | 8140 | +0.004 | 0.22 | 0.414 | 4 | +0.001 | +0.035 | n/a | +0.000 | no | FAIL |
| C1-COMP-STATS | composite | composite | 14642 | -0.014 | -1.40 | 0.914 | 3 | -0.007 | +0.005 | n/a | -0.002 | no | FAIL |
| C1-COMP-TIME | composite | composite | 2509 | +0.005 | 0.88 | 0.200 | 5 | +0.005 | +0.010 | n/a | +0.009 | no | FAIL |


**0 of 24 pass.** No p clears even the first BH step (0.10 / 24 = 0.004).
- The closest are TIME-07, the NY 08:30 burst (p 0.026, net +0.046 R, 6/8 years), TREND-20, the Asia+London -> NY
  session TSMOM (p 0.043, net +0.085 R), and PATTERNS-01 (p 0.052, net +0.111 R, but -0.060 R on shorts).
- These are noted only. Re-testing any of them is a new pre-registered trial.

## 6. TEST-OLD, survivors and deploy

- **No confirm passer**, so no rule or composite goes to TEST-OLD from this round.
- **S200-TREND-03** (C1 explore +0.294 R, day t 3.78, 8/8 years, random-side excess +0.215 R; its confirm was read on
  2026-11-02 under the old costs) goes to TEST-OLD only. There is no 2003-2009 file on disk (`raw/` starts at 2010),
  so it is **old-test pending** until the data lane delivers.
- **Deploy-ready survivors: none.** No `arm_rules_c1.py`, no `exploration_arms.yaml` row and no qualification record.

## 7. What this says (ranked)

1. **The old cost model was the binding constraint at explore, not the edge.**
   - C1 cuts the median charged cost from 0.06-0.14 R to 0.013-0.030 R a trade.
   - 117 of 200 rules are net positive in explore under C1, against 54 under the old pricing.
   - The C1 explore gate is now close to "gross day t >= 2 with the right side".
2. **That gross edge mostly does not survive 2018-2025.**
   - The 19 singles keep a median of about a fifth (20%) of their explore net. 6 of 19 turn negative
     (STATS-24/25/26, TREND-24/28/31) and none reach day t 2.
   - The ML direction models (STATS-24/25/26) lose everything out of sample.
   - The trend composite falls from +0.036 to +0.005 R a day. This is the classic explore-to-confirm decay of
     selected rules: with 20 passers out of 200 correlated rules, about 5 would pass by chance at t >= 2.
3. **The short credit is not what made explore pass.** 16 of the 20 passers still pass (net > 0, day t >= 2) with
   the short swap floored at 0. The 4 that depend on it are TREND-01, TREND-15, TREND-28 and STATS-26, and all four
   fail confirm anyway.
4. **Losers next to winners.** The meanrev and macro families stay retired: under the cheapest defensible cost they
   still have no rule with day t >= 2. Their best rows are under 0.1 R gross, and their composite (meanrev) is
   negative out of sample.
5. **Recommendation (research director; the live gate stays owner-only):** keep C1 as the cost and control for all
   new trials, including the sprint-3000 screen.
   - Do not re-open the sprint-200 rules at neighbouring parameters.
   - The three near misses (TIME-07, TREND-20, PATTERNS-01 long side) are candidates for one pre-registered trial
     each on LEARN-OLD + 2010-17. That is a new K, and only once the 2003-2009 data lands.

## 8. K

- Re-score of past explore results: +0 (declared by the amendment; same frozen rules, explore window only).
- Composites: +5 (one per family: trend, meanrev, patterns, stats, time).
- Confirm reads: 24 looks under one BH family (19 already-counted singles, 5 composites).
- Program K 3,330 -> **3,335**.
