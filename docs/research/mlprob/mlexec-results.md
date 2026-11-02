# ML + probability team, lane MLEXEC: results (55 execution variants, 0 survivors)

ML researcher (staff), 2026-11-02. Pre-registration `mlexec-prereg.md` (commit `61428d9e`, amendment 1 for W2 in
`a137b187`, both before any run); script and engine tests `scripts/mlprob/mlexec_screen.py`,
`tests/test_mlprob_mlexec.py` (commit `89a84b8a`, before any run). Cost of record `cor-2026-10-31.2` (ICM round trip
upper bound, overshoot floor, shipped long swap), W2 bps costs reported beside it. Nothing at or after 2025-10-01 read.

**Verdict: 0 of 55 pass explore, so confirm was not run (the script refuses: "no passers") and TEST-OLD does not
apply. No arm, no rule code, no `exploration_arms.yaml` row. K added: 55.** No execution variant turned the gross
trend premium into a net edge; the cheapest-looking lever (a pullback limit entry) is the most harmful.

## Run order (integrity)

prereg commit -> amendment 1 (W2) -> script + tests commit -> engine parity (V0 only) -> one explore run of V1-V5 ->
confirm invoked once and refused (no passers). No rerun, no parameter changed, no code change after the script commit.

- **Parity:** V0 reproduces the trend lane's published explore row for all 11 rules exactly (n, net R, t, gross R,
  leak-free control excess; e.g. 03: n 449, net +0.206, t 2.68, gross +0.316, control +0.115).
- **Engine tests** (5): limit fill needs a $0.10 trade-through, a gap fills at the open, expiry skips, the short
  mirror; in the fill minute only the stop counts; swap-cut exits a long below +0.5 R and holds one above; bps scale.
- Explore JSON (aggregates only, not committed): `~/tmp/mlprob_mlexec/explore.json`, sha256 `1e1a1112...5313`.

## Pooled read (mean over the 11 rules of per-trade uplift vs market entry)

| variant | mean uplift R | trade-weighted | rules with uplift > 0 | what happened |
|---|---|---|---|---|
| V1 LIM30 (limit 0.3 R, 60 min) | -0.110 | -0.083 | 1 of 11 | fills 10-71% of signals; filled trades lose gross (03: gross +0.316 -> +0.021) |
| V2 LIM15 (limit 0.15 R, 30 min) | -0.097 | -0.061 | 3 of 11 | same adverse selection, smaller |
| V3 SWAPCUT (longs out before rollover unless +0.5 R) | -0.001 | +0.001 | 6 of 11 | saves swap, cuts the same amount of gross |
| V4 WIDE (stop 1.5x) | -0.035 | -0.020 | 3 of 11 | cost in R falls, gross in R falls faster |
| V5 LEARN (walk-forward chooser V0/V1) | -0.010 | -0.003 | 6 of 11 | chose the limit on 12-65% of signals; no better than market |

## What this says (and does not)

1. **Pullback limits are adversely selected on trend breaks, strongly.** The breaks that come back 0.15-0.3 R within
   30-60 minutes are the failed breaks; the ones that run never fill. Filled-trade gross falls by 0.1-0.3 R, which is
   2-3x the cost the limit saves. This is the Handa-Schwartz / Harris-Hasbrouck adverse-selection result on gold
   1h-4h breakouts, and it holds on every rule with a real gross edge (01, 03, 04, 15, 28, 31). Retire limit-entry
   variants of breakout rules (H-29/H-30 style) unless they come with new information about which breaks hold.
2. **Swap is not separable from the edge.** Exiting longs that are not +0.5 R up before the rollover removes the
   swap and the same amount of gross (03: gross -0.07, cost -0.03, net -0.04). The trend premium needs the overnight
   hold, as the trend lane suspected; a swap-free venue (owner decision) remains the only route via this lever.
3. **Wider stops do not dilute cost for free**: gross R falls more than cost R on 8 of 11 rules (the other three
   gain +0.001 to +0.011 R).
4. **The learned chooser learned nothing usable**: on 11 point-in-time features it picked limits on a minority of
   signals and its uplift is -0.010 R pooled (6 of 11 rules up, none materially). Consistent with the programme's
   meta-model AUC near 0.50: the information to tell holding breaks from failing ones is not in these features.
5. **Near misses, disclosed:** 03-V3 (n 501, net +0.166, t 2.46, ctrl +0.074) and 03-V4 (n 438, net +0.126,
   t 2.35, ctrl +0.067) meet the brief's base explore rule and fail only the lane's uplift rule (-0.039, -0.079 R):
   they are weaker copies of 03 market entry, whose confirm already failed (net +0.070, t 0.99). Not run on confirm.
6. **W2 readings (reported, not gated):** bps costs (gold at $1,100-1,800 in explore, so costs are 30-45% of the
   dollar figures) lift every row by 0.03-0.10 R and change no ranking: the best new variant (03 V3, bps net +0.220)
   stays below its market row (+0.279). The drift control (same side, same time, another day within
   +/-20 trading days) is beaten by 03 V0 (+0.202) and V3 (+0.109) but by no limit variant; much of the leak-free
   excess of 03-V0 is not explained by drift in explore, but the statistics expert's point is about confirm 2025.
7. Not shown: excluding 2025 and the confirm W2 statistics (confirm was not run).

## All 55 (explore 2010-2017; V5 explore = 2012-2017 and its uplift is against V0 on the same 2012-2017 signals; V0 rows are the reference, already counted by the trend lane)

filled/signals = trades' source signals that produced a fill (limit variants) out of in-window signals; uplift =
variant net R per trade - V0 net R per trade; ctrl = leak-free twin excess; drift = drift-twin excess; bps net = net R
with W2 bps costs; day t = t of day-summed net R; yrs+ = explore years with net > 0.

| rule | variant | n | filled/signals | gross R | net R | t | uplift | ctrl | drift | bps net | day t | yrs+ | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 01 | V0 MKT | 329 | 786/786 | +0.188 | +0.047 | 0.58 | +0.000 | -0.025 | +0.093 | +0.141 | 0.58 | 4 | ref |
| 01 | V1 LIM30 | 66 | 76/786 | +0.069 | -0.086 | -0.44 | -0.133 | -0.028 | -0.142 | +0.017 | -0.44 | 3 | FAIL-COUNT |
| 01 | V2 LIM15 | 120 | 153/786 | +0.226 | +0.091 | 0.62 | +0.045 | +0.021 | -0.078 | +0.181 | 0.62 | 4 | FAIL |
| 01 | V3 SWAPCUT | 480 | 786/786 | +0.098 | +0.046 | 0.88 | -0.000 | -0.009 | +0.058 | +0.081 | 0.89 | 6 | FAIL |
| 01 | V4 WIDE | 313 | 786/786 | +0.153 | +0.050 | 0.85 | +0.003 | +0.007 | +0.039 | +0.119 | 0.86 | 4 | FAIL |
| 01 | V5 LEARN | 144 | 223/563 | +0.129 | +0.009 | 0.07 | -0.048 | -0.031 | -0.115 | +0.089 | 0.07 | 4 | FAIL |
| 03 | V0 MKT | 449 | 948/948 | +0.316 | +0.206 | 2.68 | +0.000 | +0.115 | +0.202 | +0.279 | 2.66 | 7 | ref |
| 03 | V1 LIM30 | 265 | 362/948 | +0.021 | -0.087 | -1.01 | -0.293 | +0.108 | -0.126 | -0.015 | -1.01 | 3 | FAIL |
| 03 | V2 LIM15 | 323 | 494/948 | +0.060 | -0.047 | -0.59 | -0.253 | +0.068 | -0.095 | +0.024 | -0.58 | 2 | FAIL |
| 03 | V3 SWAPCUT | 501 | 948/948 | +0.246 | +0.166 | 2.46 | -0.039 | +0.074 | +0.109 | +0.220 | 2.44 | 7 | FAIL |
| 03 | V4 WIDE | 438 | 948/948 | +0.202 | +0.126 | 2.35 | -0.079 | +0.067 | +0.095 | +0.176 | 2.34 | 7 | FAIL |
| 03 | V5 LEARN | 298 | 503/635 | +0.219 | +0.124 | 1.34 | -0.077 | +0.112 | +0.038 | +0.188 | 1.34 | 3 | FAIL |
| 04 | V0 MKT | 1395 | 1920/1920 | +0.162 | +0.042 | 1.01 | +0.000 | +0.105 | +0.093 | +0.122 | 1.01 | 6 | ref |
| 04 | V1 LIM30 | 879 | 1038/1920 | +0.023 | -0.097 | -1.90 | -0.139 | +0.029 | -0.018 | -0.017 | -1.88 | 3 | FAIL |
| 04 | V2 LIM15 | 1011 | 1258/1920 | +0.062 | -0.057 | -1.20 | -0.099 | +0.050 | -0.002 | +0.022 | -1.19 | 3 | FAIL |
| 04 | V3 SWAPCUT | 1443 | 1920/1920 | +0.143 | +0.044 | 1.12 | +0.002 | +0.094 | +0.086 | +0.110 | 1.12 | 5 | FAIL |
| 04 | V4 WIDE | 1364 | 1920/1920 | +0.087 | +0.004 | 0.12 | -0.038 | +0.048 | +0.052 | +0.059 | 0.12 | 5 | FAIL |
| 04 | V5 LEARN | 954 | 1250/1446 | +0.171 | +0.045 | 0.90 | +0.010 | +0.160 | +0.075 | +0.130 | 0.90 | 4 | FAIL |
| 10 | V0 MKT | 1354 | 1354/1354 | +0.081 | -0.019 | -0.79 | +0.000 | +0.006 | +0.056 | +0.048 | -0.79 | 2 | ref |
| 10 | V1 LIM30 | 553 | 553/1354 | +0.091 | -0.018 | -0.49 | +0.001 | +0.018 | +0.072 | +0.055 | -0.49 | 3 | FAIL |
| 10 | V2 LIM15 | 780 | 780/1354 | +0.090 | -0.016 | -0.49 | +0.003 | +0.012 | +0.080 | +0.055 | -0.49 | 4 | FAIL |
| 10 | V3 SWAPCUT | 1354 | 1354/1354 | +0.081 | -0.019 | -0.79 | +0.000 | +0.014 | +0.060 | +0.048 | -0.79 | 2 | FAIL |
| 10 | V4 WIDE | 1354 | 1354/1354 | +0.055 | -0.008 | -0.48 | +0.011 | -0.002 | +0.030 | +0.034 | -0.48 | 2 | FAIL |
| 10 | V5 LEARN | 664 | 664/1009 | +0.078 | -0.023 | -0.70 | +0.014 | +0.010 | +0.052 | +0.045 | -0.70 | 3 | FAIL |
| 15 | V0 MKT | 183 | 281/281 | +0.289 | +0.149 | 1.18 | +0.000 | -0.009 | +0.045 | +0.242 | 1.19 | 4 | ref |
| 15 | V1 LIM30 | 30 | 33/281 | +0.240 | +0.124 | 0.36 | -0.026 | +0.145 | +0.330 | +0.199 | 0.36 | 4 | FAIL-COUNT |
| 15 | V2 LIM15 | 60 | 67/281 | +0.132 | +0.006 | 0.03 | -0.143 | -0.003 | -0.109 | +0.089 | 0.03 | 3 | FAIL-COUNT |
| 15 | V3 SWAPCUT | 194 | 281/281 | +0.225 | +0.156 | 1.40 | +0.007 | +0.023 | +0.077 | +0.202 | 1.41 | 3 | FAIL |
| 15 | V4 WIDE | 180 | 281/281 | +0.191 | +0.091 | 1.04 | -0.058 | -0.015 | +0.088 | +0.158 | 1.05 | 3 | FAIL |
| 15 | V5 LEARN | 80 | 99/201 | +0.254 | +0.142 | 0.69 | -0.089 | +0.111 | +0.192 | +0.217 | 0.69 | 4 | FAIL-COUNT |
| 19 | V0 MKT | 998 | 1031/1031 | +0.110 | -0.003 | -0.08 | +0.000 | +0.140 | +0.114 | +0.072 | -0.08 | 4 | ref |
| 19 | V1 LIM30 | 713 | 727/1031 | +0.059 | -0.057 | -1.20 | -0.054 | +0.065 | +0.041 | +0.021 | -1.19 | 2 | FAIL |
| 19 | V2 LIM15 | 773 | 793/1031 | +0.025 | -0.091 | -1.97 | -0.087 | +0.038 | +0.019 | -0.013 | -1.94 | 2 | FAIL |
| 19 | V3 SWAPCUT | 1000 | 1031/1031 | +0.111 | +0.013 | 0.32 | +0.016 | +0.114 | +0.112 | +0.078 | 0.33 | 4 | FAIL |
| 19 | V4 WIDE | 984 | 1031/1031 | +0.066 | -0.010 | -0.33 | -0.007 | +0.079 | +0.041 | +0.041 | -0.34 | 4 | FAIL |
| 19 | V5 LEARN | 703 | 725/765 | +0.066 | -0.049 | -1.04 | -0.007 | +0.080 | +0.060 | +0.028 | -1.04 | 3 | FAIL |
| 20 | V0 MKT | 411 | 411/411 | +0.124 | +0.092 | 1.66 | +0.000 | +0.059 | +0.117 | +0.113 | 1.66 | 4 | ref |
| 20 | V1 LIM30 | 186 | 186/411 | +0.080 | +0.047 | 0.55 | -0.045 | +0.007 | +0.041 | +0.069 | 0.55 | 3 | FAIL |
| 20 | V2 LIM15 | 240 | 240/411 | +0.061 | +0.028 | 0.38 | -0.064 | -0.003 | +0.024 | +0.050 | 0.38 | 2 | FAIL |
| 20 | V3 SWAPCUT | 411 | 411/411 | +0.124 | +0.092 | 1.66 | +0.000 | +0.045 | +0.125 | +0.113 | 1.66 | 4 | FAIL |
| 20 | V4 WIDE | 411 | 411/411 | +0.069 | +0.049 | 1.29 | -0.042 | +0.003 | +0.076 | +0.062 | 1.29 | 6 | FAIL |
| 20 | V5 LEARN | 295 | 295/317 | +0.087 | +0.053 | 0.83 | +0.002 | +0.023 | +0.099 | +0.076 | 0.83 | 2 | FAIL |
| 24 | V0 MKT | 491 | 523/523 | +0.201 | +0.085 | 1.11 | +0.000 | +0.071 | +0.001 | +0.163 | 1.12 | 5 | ref |
| 24 | V1 LIM30 | 222 | 229/523 | +0.120 | -0.000 | -0.00 | -0.085 | +0.060 | +0.154 | +0.080 | -0.00 | 3 | FAIL |
| 24 | V2 LIM15 | 294 | 309/523 | +0.223 | +0.103 | 1.04 | +0.018 | +0.102 | -0.013 | +0.184 | 1.04 | 4 | FAIL |
| 24 | V3 SWAPCUT | 497 | 523/523 | +0.143 | +0.052 | 0.74 | -0.032 | +0.067 | +0.116 | +0.113 | 0.74 | 5 | FAIL |
| 24 | V4 WIDE | 468 | 523/523 | +0.093 | +0.013 | 0.21 | -0.072 | +0.009 | +0.001 | +0.066 | 0.21 | 3 | FAIL |
| 24 | V5 LEARN | 357 | 377/399 | +0.153 | +0.037 | 0.42 | +0.023 | +0.049 | +0.043 | +0.115 | 0.42 | 5 | FAIL |
| 28 | V0 MKT | 207 | 231/231 | +0.252 | +0.141 | 1.28 | +0.000 | +0.008 | +0.177 | +0.215 | 1.27 | 5 | ref |
| 28 | V1 LIM30 | 23 | 24/231 | +0.073 | -0.085 | -0.32 | -0.226 | +0.183 | -0.105 | +0.017 | -0.32 | 4 | FAIL-COUNT |
| 28 | V2 LIM15 | 38 | 39/231 | -0.180 | -0.287 | -1.56 | -0.428 | -0.047 | -0.383 | -0.217 | -1.56 | 1 | FAIL-COUNT |
| 28 | V3 SWAPCUT | 217 | 231/231 | +0.209 | +0.163 | 1.70 | +0.022 | -0.032 | +0.118 | +0.194 | 1.70 | 6 | FAIL |
| 28 | V4 WIDE | 203 | 231/231 | +0.150 | +0.070 | 0.91 | -0.071 | +0.004 | +0.020 | +0.123 | 0.91 | 3 | FAIL |
| 28 | V5 LEARN | 126 | 139/166 | +0.281 | +0.183 | 1.21 | +0.068 | +0.024 | +0.207 | +0.248 | 1.21 | 4 | FAIL |
| 31 | V0 MKT | 607 | 1778/1778 | +0.145 | +0.044 | 0.83 | +0.000 | +0.006 | +0.038 | +0.111 | 0.83 | 5 | ref |
| 31 | V1 LIM30 | 221 | 275/1778 | -0.070 | -0.149 | -1.68 | -0.193 | -0.051 | -0.248 | -0.096 | -1.69 | 3 | FAIL |
| 31 | V2 LIM15 | 325 | 474/1778 | +0.090 | +0.001 | 0.01 | -0.044 | +0.011 | -0.053 | +0.060 | 0.01 | 5 | FAIL |
| 31 | V3 SWAPCUT | 787 | 1778/1778 | +0.094 | +0.050 | 1.29 | +0.006 | +0.003 | +0.018 | +0.079 | 1.29 | 6 | FAIL |
| 31 | V4 WIDE | 554 | 1778/1778 | +0.089 | +0.015 | 0.35 | -0.030 | -0.006 | +0.007 | +0.064 | 0.35 | 5 | FAIL |
| 31 | V5 LEARN | 419 | 1098/1337 | +0.177 | +0.076 | 1.15 | +0.001 | -0.009 | +0.096 | +0.143 | 1.16 | 4 | FAIL |
| 32 | V0 MKT | 2361 | 4206/4206 | +0.041 | -0.012 | -0.93 | +0.000 | +0.019 | +0.025 | +0.024 | -0.92 | 3 | ref |
| 32 | V1 LIM30 | 893 | 1221/4206 | +0.030 | -0.030 | -1.13 | -0.017 | +0.002 | +0.004 | +0.010 | -1.10 | 3 | FAIL |
| 32 | V2 LIM15 | 1208 | 1711/4206 | +0.026 | -0.031 | -1.45 | -0.018 | +0.012 | -0.006 | +0.007 | -1.43 | 3 | FAIL |
| 32 | V3 SWAPCUT | 2397 | 4206/4206 | +0.040 | -0.007 | -0.50 | +0.006 | +0.015 | +0.019 | +0.025 | -0.50 | 3 | FAIL |
| 32 | V4 WIDE | 2211 | 4206/4206 | +0.024 | -0.012 | -1.14 | +0.001 | +0.008 | +0.011 | +0.012 | -1.14 | 2 | FAIL |
| 32 | V5 LEARN | 1357 | 2183/3198 | +0.016 | -0.041 | -2.23 | -0.009 | +0.012 | -0.002 | -0.003 | -2.18 | 1 | FAIL |

## Ranked implications (for the principal; owner-only items flagged)

1. Stop spending trials on execution tweaks of 1h-4h breakout entries at ICM costs: entry timing loses to adverse
   selection, exit timing around the rollover gives back the gross it saves, wider stops dilute gross.
2. The two cost levers left are venue-level: a swap-free / lower-commission account for the trend book (owner
   decision: spending, instruments) or the short-side swap asymmetry (one pre-registered look, trend lane item 2).
3. A learned chooser needs information about break quality the 11 bar features do not carry (order flow, depth,
   cross-asset confirmation); without it, do not re-run V5 with other learners (that would be a search, K grows).
4. Engine limit-order support is not needed by any survivor; no build ask.
