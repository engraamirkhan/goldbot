# When-to-trade map on random entries: screen result (H-83, bet 3, H-A)

microstructure-researcher, 2026-10-30. Pre-registration: `prereg-2026-10-30-when-map.md`, commit `43491224`, committed
before any return was computed. Data: Dukascopy XAUUSD 1m, 2010-2017 only (local `raw/`, not in the repo). No 2018+ bar
and no holdout bar was read. Script outside the repo (`~/tmp/whenmap/run.py`); only aggregates are reported here.

## Verdicts

| Test | Rule (pre-registered) | Result | Verdict |
|---|---|---|---|
| 24 cells | Holm-adjusted day-bootstrap lower bound > 0 | **0 of 24.** Every cell's net mean is negative (-0.058 to -0.109 R); every one-sided p = 1.0 | **FAIL** |
| Plan line 3 retirement | Retire if no cell has net > 0, day-clustered t >= 3 and >= 6 of 8 years positive | No cell has net > 0; 0 of 8 years positive in every cell | **Line 3 (timing-only at the S0 clock) RETIRED** |
| H-A trend side | mean(with - against) >= 0.05 R and 90 % LB > 0 | **-0.008 R** (90 % CI -0.013 .. -0.003, day-clustered t -2.7, n 246,847 entry times). "With" is slightly *worse* | **FAIL**: do not add the 4h trend filter to S5/Z4/Z7 |
| Deflated read | Best cell's t vs null max of 25 (~2.0) and of cumulative K ~1,250 (~3.8) | Best cell t = -7.4 (NY overlap, high, against) | nothing to deflate |

Multiple testing: **K +25** (24 cells + H-A). No registry trial id was used; the director decides whether to book it.

## The 24 cells

S0 geometry ($3 stop, 1 R target, 30 min hold), both sides at every 10-minute entry, 2010-04-26 to 2017-12-29 (1,987
trading days after the tercile warm-up), 246,847 entry times, 493,694 trades. Net = gross - ($0.23/oz + the thin-hour
spread add-on) / $3. The add-on averaged 0.006-0.007 R in London and NY overlap and 0.013-0.017 R in Asia and NY late.
"Holm-adj. LB" is the cell mean's bootstrap quantile at the Holm alpha its rank would take (B = 20,000); with every
p = 1.0 the step-down stops at the first cell, so the column only shows how far each cell is from passing.

| Session | Range tercile | Side | n | Gross R | Net R | Holm-adj. LB | Day-cl. t | Years > 0 | Net R at $0.144 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| Asia | low | with | 29,569 | +0.009 | -0.083 | -0.092 | -24.8 | 0/8 | -0.039 | fail |
| Asia | low | against | 29,569 | -0.009 | -0.101 | -0.108 | -31.1 | 0/8 | -0.057 | fail |
| Asia | mid | with | 29,105 | -0.011 | -0.103 | -0.111 | -28.7 | 0/8 | -0.059 | fail |
| Asia | mid | against | 29,105 | +0.011 | -0.081 | -0.089 | -23.4 | 0/8 | -0.037 | fail |
| Asia | high | with | 29,008 | -0.016 | -0.109 | -0.118 | -28.7 | 0/8 | -0.064 | fail |
| Asia | high | against | 29,008 | +0.016 | -0.078 | -0.087 | -21.0 | 0/8 | -0.032 | fail |
| London | low | with | 21,789 | +0.025 | -0.058 | -0.071 | -11.0 | 0/8 | -0.023 | fail |
| London | low | against | 21,789 | -0.025 | -0.108 | -0.121 | -20.6 | 0/8 | -0.073 | fail |
| London | mid | with | 21,321 | -0.000 | -0.083 | -0.097 | -15.4 | 0/8 | -0.048 | fail |
| London | mid | against | 21,321 | +0.000 | -0.083 | -0.097 | -15.2 | 0/8 | -0.048 | fail |
| London | high | with | 21,629 | -0.019 | -0.103 | -0.118 | -17.8 | 0/8 | -0.067 | fail |
| London | high | against | 21,629 | +0.019 | -0.065 | -0.081 | -11.4 | 0/8 | -0.029 | fail |
| NY overlap | low | with | 12,252 | +0.007 | -0.077 | -0.102 | -8.3 | 0/8 | -0.041 | fail |
| NY overlap | low | against | 12,252 | -0.007 | -0.090 | -0.116 | -9.7 | 0/8 | -0.055 | fail |
| NY overlap | mid | with | 12,030 | -0.004 | -0.087 | -0.113 | -9.2 | 0/8 | -0.052 | fail |
| NY overlap | mid | against | 12,030 | +0.004 | -0.079 | -0.106 | -8.4 | 0/8 | -0.044 | fail |
| NY overlap | high | with | 12,166 | -0.018 | -0.102 | -0.126 | -11.4 | 0/8 | -0.066 | fail |
| NY overlap | high | against | 12,166 | +0.018 | -0.066 | -0.092 | -7.4 | 0/8 | -0.030 | fail |
| NY late | low | with | 19,426 | -0.008 | -0.098 | -0.112 | -19.6 | 0/8 | -0.056 | fail |
| NY late | low | against | 19,426 | +0.008 | -0.083 | -0.097 | -16.6 | 0/8 | -0.040 | fail |
| NY late | mid | with | 19,121 | -0.011 | -0.101 | -0.117 | -18.3 | 0/8 | -0.059 | fail |
| NY late | mid | against | 19,121 | +0.011 | -0.080 | -0.095 | -14.5 | 0/8 | -0.037 | fail |
| NY late | high | with | 19,431 | -0.007 | -0.098 | -0.109 | -18.1 | 0/8 | -0.055 | fail |
| NY late | high | against | 19,431 | +0.006 | -0.085 | -0.095 | -15.4 | 0/8 | -0.042 | fail |

Pooled: gross -0.0001 R (longs -0.001, shorts +0.001), net -0.088 R; 9.3 % of trades hit the stop, 9.2 % the target,
81.5 % exit on time. The P4 expectation (|gross| < 0.03 R everywhere, net -0.08 to -0.12 R) held in all 24 cells. At the
central $0.144/oz cost (sensitivity, no verdict) the best cell is still -0.023 R.

## Trend side (H-A)

Paired per entry time, d = R(with the 4h return) - R(against). Costs cancel, so gross = net.

| | Value |
|---|---|
| Pooled mean d | **-0.008 R**, 90 % CI -0.013 .. -0.003, day-clustered t -2.7 |
| Pooled with / against (net) | -0.093 / -0.084 R (gross -0.004 / +0.004) |
| d by year 2010..2017 | -0.024, -0.031, -0.023, -0.002, -0.008, +0.006, +0.002, +0.008 |

By stratum (descriptive, no verdict):

| Session | Range tercile | n | d (R) | 90 % CI |
|---|---|---|---|---|
| Asia | low | 29,569 | +0.019 | +0.008 .. +0.029 |
| Asia | mid | 29,105 | -0.022 | -0.034 .. -0.011 |
| Asia | high | 29,008 | -0.031 | -0.043 .. -0.019 |
| London | low | 21,789 | +0.050 | +0.033 .. +0.067 |
| London | mid | 21,321 | -0.000 | -0.018 .. +0.017 |
| London | high | 21,629 | -0.038 | -0.057 .. -0.019 |
| NY overlap | low | 12,252 | +0.013 | -0.017 .. +0.044 |
| NY overlap | mid | 12,030 | -0.008 | -0.039 .. +0.024 |
| NY overlap | high | 12,166 | -0.036 | -0.065 .. -0.006 |
| NY late | low | 19,426 | -0.016 | -0.032 .. +0.001 |
| NY late | mid | 19,121 | -0.022 | -0.039 .. -0.003 |
| NY late | high | 19,431 | -0.013 | -0.031 .. +0.005 |

## Reading

1. **No "when" edge at the scalp clock.** Random $3 / 30-minute brackets lose the cost in every session and day type.
   The largest gross in any cell is +0.025 R, a third of the 0.077 R flat cost and a tenth of the scalp bar (~0.23 R).
   Session and day type change the cost (Asia and NY late pay ~2x the spread add-on of London/NY overlap), not the
   gross. Practical consequence for every live scalp: **thin hours cost ~0.01 R more for nothing**; this supports
   keeping 1m/5m arms inside London + NY overlap (a cost decision, not an edge).
2. **H-A fails, and the sign is the wrong way.** At a 30-minute horizon, trading with the trailing 4h return is
   0.008 R worse than against it, with the early years (2010-2012) driving it. The performance-analyst's observation
   on S5/Z4/Z7 was the October 2026 short drift (research-plan.md section 1), not a trend-side mechanism.
3. **Post-hoc pattern, not a finding.** Inside the strata, d is positive after narrow prior days (London low +0.050 R)
   and negative after wide ones (every high tercile -0.013 to -0.038 R): continuation on quiet days, reversal on wide
   ones. It was not pre-registered, it is selected out of 12 strata, and even taken at face value the London-low "with"
   cell grosses +0.025 R, below cost. **I do not propose it as a trial.** If the opportunity line (H-16/H-17) wants a
   day-type side rule, it must pre-register it on 2018-2025-09 under its own geometry.
4. **Scope of the retirement.** This retires timing-only edges at the S0 clock (1m, $3, 30 min). It does not test the
   1h/4h random controls (I0, C1H, W0: ATR-scaled stops, 2 R, 12-48 h holds), where cost is ~0.02-0.05 R. A when-map at
   that geometry would be a new pre-registration (K +25), and I rank it low: line 1 (H-16/H-17) already asks whether
   day type predicts direction at that horizon.

## Caveats

- Dukascopy mid with a modelled cost, not broker fills; the measured costs are 2026 IC Markets/Vantage figures applied
  to 2010-2017 paths (the flat charge is at the conservative end; the result holds at the central cost).
- A bar touching both barriers is counted as a stop; it affects both sides equally and cannot create or hide a cell
  edge of the size needed.
- 81 % time exits mean the cell gross is mostly the 30-minute return; the conclusion is about where price goes in
  30 minutes, which is the scalp question.
