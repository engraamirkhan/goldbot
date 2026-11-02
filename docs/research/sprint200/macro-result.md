# Sprint-200, lane `macro`: explore result, 0 of 33 pass (2026-11-02)

Principal gold macro and flows researcher. Pre-registration `docs/research/sprint200/macro-prereg.md`, committed at
**`dd9883a2`** before any price was read; code `scripts/sprint200/macro_screen.py` committed at **`438b8805`** before
the run. One explore look on decisions 2010-01-01..2017-12-31 (bars to 2018-01-15 for exits only; nothing at or after
2025-10-01 loaded, asserted). Costs: `cor-2026-10-31.2`, ICM, charged round trip and overshoot per ADR 0064, swap per
`SwapSpec.shipped()`. Calendar sha256 `2da61655...`, macro-v1 sha256 `47473d2c...`. Output: statistics only,
`~/tmp/s200macro/explore.json` (not committed). Reproduce:
`uv run python scripts/sprint200/macro_screen.py --phase explore --out <file>`.

**Verdict: 0 of 33 pass the explore rule. No confirm look is run (0 of 2018-2025 read). No survivors, so no arm
rules, no `exploration_arms.yaml` rows, no tests.**

## Result (net mean R at the cost of record; control = 10 same-day, same-session, same-side random twins)

Pass needs n >= 100, net > 0, t >= 2.0 and net - control >= 0.05 R.

| id | mechanism | n | net R | t | gross R | control R | net - control | stop rate | median R $ | yrs + /8 | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 01 | T1 range breakout, 2R | 360 | -0.131 | -1.82 | -0.003 | -0.143 | +0.012 | 0.60 | 2.88 | 1 | FAIL |
| 02 | claims range breakout | 259 | -0.294 | -3.63 | -0.160 | -0.170 | -0.124 | 0.65 | 2.81 | 0 | FAIL |
| 03 | ISM range breakout | 139 | +0.063 | 0.56 | +0.173 | -0.025 | +0.088 | 0.46 | 3.38 | 5 | FAIL (t) |
| 04 | FED range breakout | 119 | +0.057 | 0.44 | +0.213 | +0.079 | -0.022 | 0.51 | 2.88 | 3 | FAIL |
| 05 | all-event breakout | 959 | -0.123 | -2.83 | +0.008 | -0.104 | -0.020 | 0.58 | 2.89 | 2 | FAIL |
| 06 | T1 15m overshoot fade | 230 | -0.097 | -1.42 | +0.055 | -0.172 | +0.076 | 0.45 | 2.00 | 3 | FAIL |
| 07 | claims 15m fade | 145 | -0.134 | -1.55 | +0.040 | -0.172 | +0.038 | 0.47 | 1.50 | 1 | FAIL |
| 08 | ISM 15m fade | 69 | -0.112 | -0.90 | +0.057 | -0.293 | +0.181 | 0.45 | 1.67 | 2 | FAIL (n) |
| 09 | all-event 15m fade | 561 | -0.169 | -3.87 | -0.006 | -0.211 | +0.043 | 0.48 | 1.76 | 0 | FAIL |
| 10 | T1 30m -> 16:00 NY drift | 335 | +0.026 | 0.30 | +0.118 | -0.141 | +0.168 | 0.54 | 3.83 | 3 | FAIL (t) |
| 11 | claims drift | 242 | -0.093 | -0.77 | -0.003 | -0.182 | +0.089 | 0.60 | 3.96 | 4 | FAIL |
| 12 | ISM drift | 136 | +0.082 | 0.74 | +0.145 | +0.032 | +0.050 | 0.33 | 4.80 | 5 | FAIL (t) |
| 13 | all-event drift | 882 | -0.004 | -0.07 | +0.079 | -0.082 | +0.079 | 0.48 | 4.18 | 6 | FAIL |
| 14 | T1 next-day drift | 130 | +0.053 | 0.77 | +0.071 | +0.042 | +0.012 | 0.09 | 17.23 | 5 | FAIL |
| 15 | FED next-day drift | 49 | +0.333 | 2.74 | +0.349 | +0.253 | +0.080 | 0.04 | 17.05 | 6 | FAIL (n < 100) |
| 16 | all-event next-day drift | 310 | +0.025 | 0.58 | +0.042 | -0.005 | +0.030 | 0.10 | 17.49 | 6 | FAIL |
| 17 | pre-event long 24h | 473 | -0.111 | -4.19 | -0.055 | -0.054 | -0.057 | 0.11 | 17.75 | 1 | FAIL |
| 18 | T1+FED pre-drift unwind | 202 | +0.000 | 0.00 | +0.089 | -0.080 | +0.080 | 0.45 | 4.52 | 3 | FAIL |
| 19 | claims+ISM pre-drift unwind | 215 | -0.110 | -1.39 | -0.038 | -0.118 | +0.008 | 0.49 | 4.54 | 3 | FAIL |
| 20 | Monday fade of Friday shock | 117 | +0.073 | 1.39 | +0.086 | +0.020 | +0.053 | 0.06 | 17.81 | 7 | FAIL (t) |
| 21 | Monday continuation | 117 | -0.058 | -0.96 | -0.043 | -0.074 | +0.017 | 0.09 | 17.81 | 2 | FAIL |
| 22 | day-after 1.5 sd shock fade | 229 | -0.009 | -0.25 | +0.005 | -0.038 | +0.029 | 0.08 | 17.81 | 4 | FAIL |
| 23 | day-after event-shock fade | 157 | -0.063 | -1.54 | -0.049 | -0.072 | +0.009 | 0.10 | 17.23 | 3 | FAIL |
| 24 | 01 + 1d trend gate | 176 | -0.073 | -0.71 | +0.055 | -0.115 | +0.042 | 0.58 | 2.73 | 2 | FAIL |
| 25 | 05 + high-RV gate | 223 | -0.191 | -2.18 | -0.084 | -0.071 | -0.120 | 0.62 | 3.69 | 1 | FAIL |
| 26 | 09 + low-RV gate | 219 | -0.258 | -3.70 | -0.085 | -0.197 | -0.061 | 0.52 | 1.57 | 0 | FAIL |
| 27 | 13 + 1d trend gate | 465 | +0.009 | 0.14 | +0.090 | -0.121 | +0.130 | 0.46 | 4.18 | 5 | FAIL (t) |
| 28 | 16 + 1d trend gate | 181 | +0.059 | 0.99 | +0.076 | +0.017 | +0.042 | 0.11 | 17.54 | 5 | FAIL |
| 29 | 4h event-bar momentum | 358 | -0.059 | -1.38 | +0.061 | +0.179 | -0.238 | 0.16 | 6.19 | 2 | FAIL |
| 30 | 29 + 1d trend gate | 188 | -0.036 | -0.56 | +0.078 | +0.239 | -0.274 | 0.17 | 6.21 | 3 | FAIL |
| 31 | 05 + real-yield gate | 479 | -0.082 | -1.32 | +0.049 | -0.077 | -0.005 | 0.57 | 2.85 | 1 | FAIL |
| 32 | 05 + VIX-high gate | 332 | -0.106 | -1.44 | +0.018 | -0.050 | -0.057 | 0.57 | 3.14 | 3 | FAIL |
| 33 | T1 breakout, range stop, hold to 16:00 NY | 360 | -0.016 | -0.21 | +0.057 | -0.120 | +0.104 | 0.49 | 5.05 | 5 | FAIL (t) |

The highest t is S15 (FOMC next-day drift, t 2.74), which fails n >= 100. That was stated before the look (the power
note in the pre-registration, section 0), and its control earns +0.25 R on the same days, so most of it is the day's
drift, not the rule. **It is not promoted, re-pooled or re-tested.**

## Reading

1. **Event-minute range breakouts do not pay at our stop size.** Gross is about 0 for T1, claims and the pooled set
   (S01, S02, S05). With a median R of about $2.9 the all-in cost is about 0.12-0.15 R, and that is the whole loss. ISM
   and FED show +0.17 to +0.21 R gross on 119-139 trades. That is noise-sized (t below 1 net) and not a basis for
   a new trial without new evidence.
2. **There is a real but small gross "event-day" effect against random timing.** Drift and fade both beat their
   same-day twins by +0.08 to +0.17 R (S06, S10, S13, S27, S33). This is not alpha in the rules. The twins pay
   the same cost on noisier entries, and the rules enter after the move, when the stop is wider. No construction
   turns it into net mean R > 0 with t >= 2.
3. **Day-horizon rules are cost-light but carry no signal.** Their cost is about 0.02 R plus about 0.03 R of long swap
   a night. Next-day drift (S14, S16, S28) and Monday/day-after shock rules (S20-S23) come in at -0.06 to +0.07 R with
   |t| < 1.4. S20 (Monday fade) is positive in 7 of 8 years but at t 1.39. It is a candidate for nothing until a new
   mechanism or source exists.
4. **Pre-event long (S17) is reliably negative** (-0.111 R, t -4.2; gross -0.055 R). Over 2010-2017, the analogue of
   the equity announcement premium does not hold for gold. If anything the sign is the other way, and gold also pays
   the long swap. The lane does not flip it after the look: that would be tuning.
5. **Regime gates (trend, realised vol, real yields, VIX) improve nothing.** They also confirm R-16 in a new form:
   macro levels as gates on event entries carry no information here (S31, S32).
6. **A control caveat for the evaluator.** The same-day twin (brief rule 4) can draw entries from before the decision.
   On days whose direction the rule infers from that day's move (S29/S30, S15), the twins share the move. That gives a
   control of +0.18 to +0.25 R. It makes the control stricter, never looser, so it cannot create a false pass. It is
   reported, not corrected.

## Multiple testing, integrity

- **K +33** (33 strategies, one variant each; no other variant was run). Program K ~3,029 + this lane's 33. The full
  sprint total is the orchestrator's sum.
- One explore look. After the look, `macro_screen.py` received a type-only change: the true range is computed through
  `pd.concat(...).max(skipna=False)` instead of `np.maximum`, and the bar-close stamp through `epoch_ns`, so mypy
  passes. The identical explore was re-run, and every number matches the first run exactly.
- The confirm window (2018-01-01..2025-09-30) was **not read**. The holdout was not touched.
- **Blocked, no trial spent:** ECB/BoJ/BoE decisions (no point-in-time non-US central-bank calendar on disk; a new
  source for the data-engineer) and data surprises (no point-in-time consensus series on disk).

## What this lane would stop doing

- Testing further price-only event-timing rules on the US calendar: 79 looks, 0 survivors: H-89 (K 31), the
  director's 14-type screen (K 15) and this lane (K 33). Only a new input (point-in-time consensus surprises, or non-US central-bank calendars)
  justifies another event trial.
- Single-asset macro direction or gate rules on FRED levels (R-16 and S31/S32).

## Owner-only flags (unchanged)

- Going live and sizing.
- A swap-free venue for day-horizon rules.
- Paying for a consensus/surprise feed or a non-US central-bank calendar.
