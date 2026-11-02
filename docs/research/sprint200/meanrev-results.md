# Sprint-200 lane `meanrev`: explore result, 0 of 33 pass (no confirm run)

Principal market microstructure and execution researcher, 2026-11-02.
- Pre-registration: `docs/research/sprint200/meanrev-prereg.md`, commit `ae78e63a`, made before any look.
- Screen: `scripts/sprint200/meanrev_screen.py`. Frozen at `5f1e8d7b`, then fixed for the short-target bug at
  `bdc367f1`, then given the leak-free control after the coordinator's all-lane correction.
- Aggregates: `meanrev-explore.json`. Run 1 (bugged) is kept as `meanrev-explore-run1-shortbug.json`.
- Re-run: `uv run python scripts/sprint200/meanrev_screen.py explore --raw <raw>`. It is deterministic (two runs give
  the same JSON hash) and takes about 10 s on 2 workers.

## Verdict

**0 of the 33 strategies pass explore.** So:
- no confirm window was opened;
- nothing at or after 2018-01-01 was read;
- no arm is deployed.

`confirm` mode refuses to run because the explore file has no passer. Every rule, threshold, stop, target and cap is as
pre-registered.

## Disclosures (integrity)

1. **Short-target sign bug in run 1.** The first explore run booked a short that reached its target as a loss (`walk`
   returned `side x td`).
   - How it was found: random twins read gross -0.37 R at a symmetric 1 R bracket, where ~0 is expected.
   - The fix is `bdc367f1` and changes no rule. After it, random 1R/1R entries read gross -0.015 R.
   - Run 1 is kept for the evaluator. In it, all 33 rows were far below 0.
   - The prereg's "stricter reading governs" clause does not apply. Run 1 misreported the outcome; it is not a reading
     of the rule. Either way, every row fails under both readings.
2. **Control correction (coordinator, all lanes, 2026-11-02).** The brief's same-day random control leaks.
   - The leak: twins drawn before the signal take the fade side against that day's later move.
   - For this lane's fades, that makes the control 0.1-0.3 R *worse*. Example: row 01 reads -0.404 R on the leaky
     control and -0.101 R on the leak-free one. Fades would therefore "beat control" too easily on the leaky version.
   - The gate uses the leak-free control: twins at or after the trade's own entry minute, on the same day, session and
     side. If no such minute exists, the fallback is the same server minute on 4 other days.
   - Both controls are reported. No row passes under either, because no row has net > 0 with n >= 100.
3. **Implementation details the prereg did not spell out.** These were decided before any look and apply to all rows:
   - A bar that fires both sides is skipped.
   - The daily-break gap rule (row 04) decides at the 01:05 bar's open and fills at the next minute's open. This is
     stricter than filling at the same open.
   - In row 30, "first per day per side" is used up by the first trigger.

## Results, explore 2010-01-01..2017-12-31, ICM cost of record cor-2026-10-31.2 + swap

The table's columns, in order:
- n: trades, one position at a time;
- gross mean R;
- net mean R;
- t of the net;
- ctrl (leak-free): the leak-free control's net R;
- vs ctrl: net minus the leak-free control, which is the gate;
- ctrl (same-day, leaky): the brief's same-day control net R;
- stop $: the median stop in dollars;
- stress: net R at the $0.70 overshoot stress.

| id | n | gross R | net R | t | ctrl (leak-free) | vs ctrl | ctrl (same-day, leaky) | stop $ | stress | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| S200-MEANREV-01 | 3381 | -0.006 | -0.117 | -5.91 | -0.101 | -0.016 | -0.404 | 2.50 | -0.208 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-02 | 1557 | -0.088 | -0.215 | -5.79 | -0.236 | +0.021 | -0.313 | 3.37 | -0.319 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-03 | 759 | -0.038 | -0.146 | -3.50 | -0.086 | -0.061 | -0.337 | 2.50 | -0.227 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-04 | 239 | +0.065 | -0.017 | -0.31 | -0.107 | +0.090 | -0.043 | 3.93 | -0.061 | FAIL (net<=0, t<2) |
| S200-MEANREV-05 | 1484 | +0.059 | -0.039 | -1.18 | -0.065 | +0.026 | -0.147 | 2.84 | -0.133 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-06 | 1059 | +0.036 | -0.065 | -1.60 | -0.075 | +0.010 | -0.096 | 4.99 | -0.138 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-07 | 344 | +0.089 | -0.040 | -0.52 | -0.152 | +0.112 | -0.124 | 3.42 | -0.137 | FAIL (net<=0, t<2) |
| S200-MEANREV-08 | 287 | +0.091 | -0.036 | -0.37 | -0.073 | +0.037 | -0.044 | 5.48 | -0.112 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-09 | 489 | -0.004 | -0.092 | -1.83 | -0.073 | -0.019 | -0.073 | 3.51 | -0.173 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-10 | 42 | +0.392 | +0.324 | +1.76 | +0.257 | +0.067 | +0.265 | 10.08 | +0.301 | FAIL (n<100, t<2) |
| S200-MEANREV-11 | 962 | +0.014 | -0.087 | -2.06 | -0.114 | +0.026 | -0.147 | 5.07 | -0.159 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-12 | 3724 | +0.011 | -0.086 | -4.80 | -0.107 | +0.021 | -0.306 | 2.77 | -0.169 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-13 | 1634 | +0.026 | -0.090 | -3.33 | -0.101 | +0.011 | -0.194 | 2.50 | -0.175 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-14 | 61 | +0.026 | -0.065 | -0.39 | -0.098 | +0.033 | -0.095 | 6.93 | -0.127 | FAIL (n<100, net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-15 | 866 | +0.024 | -0.064 | -1.71 | -0.053 | -0.011 | -0.079 | 3.48 | -0.137 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-16 | 214 | +0.054 | -0.035 | -0.40 | -0.080 | +0.046 | -0.088 | 6.60 | -0.087 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-17 | 8117 | -0.011 | -0.124 | -12.16 | -0.124 | +0.000 | -0.119 | 2.50 | -0.201 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-18 | 193 | +0.032 | -0.030 | -0.39 | -0.038 | +0.009 | -0.055 | 16.26 | -0.048 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-19 | 546 | -0.031 | -0.126 | -2.07 | -0.164 | +0.039 | -0.306 | 3.06 | -0.216 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-20 | 1170 | +0.003 | -0.100 | -2.49 | -0.108 | +0.009 | -0.306 | 2.73 | -0.199 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-21 | 241 | +0.065 | -0.046 | -0.37 | -0.123 | +0.077 | -0.061 | 5.24 | -0.130 | FAIL (net<=0, t<2) |
| S200-MEANREV-22 | 1937 | -0.007 | -0.071 | -3.72 | -0.036 | -0.034 | -0.222 | 5.13 | -0.118 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-23 | 357 | -0.149 | -0.233 | -4.87 | -0.188 | -0.045 | -0.216 | 6.05 | -0.279 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-24 | 98 | -0.064 | -0.123 | -1.06 | -0.063 | -0.061 | -0.044 | 10.15 | -0.156 | FAIL (n<100, net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-25 | 1 | +0.090 | +0.010 | +0.00 | +0.307 | -0.297 | +0.208 | 48.26 | +0.010 | FAIL (n<100, t<2, ctrl<0.05) |
| S200-MEANREV-26 | 52 | -0.101 | -0.130 | -1.75 | -0.142 | +0.012 | -0.121 | 33.67 | -0.133 | FAIL (n<100, net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-27 | 110 | -0.109 | -0.162 | -1.77 | -0.094 | -0.068 | -0.152 | 9.64 | -0.189 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-28 | 1398 | -0.031 | -0.161 | -4.92 | -0.224 | +0.063 | -0.422 | 3.41 | -0.242 | FAIL (net<=0, t<2) |
| S200-MEANREV-29 | 310 | +0.002 | -0.014 | -0.37 | -0.039 | +0.025 | -0.036 | 18.70 | -0.018 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-30 | 298 | -0.078 | -0.173 | -2.60 | -0.164 | -0.009 | -0.486 | 3.24 | -0.249 | FAIL (net<=0, t<2, ctrl<0.05) |
| S200-MEANREV-31 | 385 | +0.100 | -0.024 | -0.49 | -0.126 | +0.102 | -0.323 | 2.50 | -0.102 | FAIL (net<=0, t<2) |
| S200-MEANREV-32 | 695 | +0.032 | -0.077 | -2.13 | -0.144 | +0.067 | -0.430 | 2.81 | -0.148 | FAIL (net<=0, t<2) |
| S200-MEANREV-33 | 158 | -0.028 | -0.062 | -1.15 | -0.067 | +0.013 | -0.067 | 25.27 | -0.067 | FAIL (net<=0, t<2, ctrl<0.05) |

## What the numbers say

**Gross is about zero.** Across 33 rules and 33,168 trades, the gross mean lies between -0.15 and +0.10 R in every row
with n >= 50. This extends R-01, R-09 and R-14 from 1m-15m to 1h, 4h and daily decisions.

**Cost does not explain the failure, but it is higher than the prereg assumed.** Measured as net minus gross, cost is:
- 0.06-0.13 R on the 15m, 1h and 4h rows;
- 0.02-0.08 R on the daily rows.

Rows with bar-scale stops still had median stops of only $2.50-6 in 2010-2017. The prereg's estimate of 0.01-0.04 R
for 4h was too optimistic. The conclusion still holds: the rules have no gross edge to pay any cost with.

**Closest rows.** None of these passes, and none should be tuned:

| row | what it is | gross R | vs leak-free control | net R | n | why it fails |
|---|---|---|---|---|---|---|
| 07 | weekly-level reclaim | +0.089 | +0.11 | -0.04 | 344 | net <= 0 |
| 31 | 15m run exhaustion | +0.100 | +0.10 | -0.02 | 385 | net <= 0 |
| 04 | daily-break gap fill | +0.065 | +0.09 | -0.02 | 239 | net <= 0 |
| 10 | daily turtle soup | +0.392 | +0.07 | +0.32 (t 1.76) | 42 | too few trades |

## K

**K +33**: one explore trial per row, and no extra variants. The bug-fix re-run and the control correction re-read the
same 33 frozen specs, so they add nothing. Program K is ~3,029 plus the sprint's 200, giving 3,229, plus whatever
extras the other lanes declare.

## Retire / do not re-test

All 33 are retired as specified:
- **VWAP:** sigma-band fades with re-entry, on daily, weekly and London-anchored VWAP.
- **Prior extremes:**
  - prior-day, prior-week and prior-month high-low sweep-and-reclaim at 1h and 4h;
  - prior-day touch rejection;
  - Donchian-20 failed breaks at 1h and 4h;
  - NY sweep of the London extreme.
- **Round numbers:** $10, $50 and $100 rejection at 15m, 1h, 4h and 1d.
- **Pivots:** classic daily and weekly R1/S1/R2/S2, and Camarilla H3/L3.
- **Overextension:**
  - SMA z-score with a reversal bar at 1h, 4h and 1d;
  - RSI(2) at 4h and 1d;
  - Bollinger(20, 2.5) re-entry at 1h;
  - daily IBS;
  - daily-range exhaustion.
- **Exhaustion:** 4-8 bar runs at 15m, 1h and 1d.
- **Gaps:** weekday daily-break gap fill.

## Proposal (for the research director; not run, not counted)

Row 10 (daily turtle soup) is the only row with a large positive net mean: +0.32 R, t 1.76. It fails on count, not on
sign. Re-testing it on the same 2010-2017 bars with a looser rule would be tuning.

The admissible route is **new data**:
1. Run the same frozen rule as an explore window on a free daily XAUUSD history from before 2010 (the 1968-2000 daily
   history the program is hunting under "free data first").
2. Then confirm it on 2018-2025-09.

Expected information is low (prior about 10%), and it costs 1 trial.
