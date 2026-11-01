# Opportunity signals screen, 2010-2017: exhaustion fade, weekly-open gap fade, volatility-regime switch

Opportunity researcher, 2026-11-01. Pre-registration `prereg-opportunity-signals-2026-11-01.md`, frozen at `d4dba0f3`
before any outcome. Code `candidates/opportunity_signals/opp_screen.py` (sha256 `7ba7c7cd...`; a typing-only edit after
the run, re-run byte-identical). One look per rule on local Dukascopy 1m bars 2010-01-01..2017-12-31. The loader
asserts no 2018+ bar was read, and the holdout stays sealed. Cost `cor-2026-10-31.2` ICM at 1.5x, plus overshoot on
stops and the long swap prior. Explore screen: **K +3** (program K 3,014 -> 3,017), 0 registry trials.

**Verdict: 0 of 3 pass.** SA FAIL, SB COUNT (n 90 < 100, and no signal anyway), SD FAIL. The two declined lines (c, b-ii)
cost K +0 (prereg section 0).

| Rule | n | Trades/wk | Gross R (t) | Net R | LB90 | Twin net R | Real - twin (3p) | Years + | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| SA exhaustion fade, 3-sigma 60m outside news | 2,582 | 6.2 | +0.093 (3.0) | -0.197 | -0.247 | -0.141 | -0.138 (1.00) | 0/8 | FAIL |
| SB Sunday-reopen gap fade to Friday close | 90 | 0.2 | +0.065 (0.8) | -0.016 | -0.150 | -0.000 | -0.015 (1.00) | 4/8 | COUNT |
| SD 1h trend in high RV5, reversion in low | 1,895 | 4.5 | -0.013 (-0.4) | -0.126 | -0.184 | +0.001 | -0.223 (1.00) | 3/8 | FAIL |

## Readings

- **SA. The fade is worse than random.** Its gross +0.093 R (t 3.0) is real on mid quotes. But random entries with the
  same stop and target distance, near the same time, earn +0.148 R gross. The structure stop sits beyond the extreme and
  the target is nearer, so a bracket like that collects quote mean-reversion on 1m mid bars whatever the side. The
  time-structure slices saw the same thing: random brackets earn +0.03 to +0.05 R. Cost then takes 0.29 R
  (median R $1.57 in 2010-2017), and every year is net negative. Shorts (fading up-moves) lose less than longs:
  -0.15 R against -0.24 R net. Neither side clears its twin. A 3-sigma hour with no news does not mark exhaustion in
  gold. This agrees with Caporale, Gil-Alana and Plastun's daily finding of inertia rather than reversal.
  Fires: 3,721, of which 474 were blocked by news and 1,082 overlapped an open trade.
- **SB. The gap is too rare to read.** Only 90 of 408 Sunday reopens gap at least 0.20 x ATR20d by +30 min. The gross
  is +0.065 R (t 0.8) and the twin earns the same. A larger sample cannot rescue it: 2018-2025 has the same weekend count.
- **SD. The switch adds nothing, and the reversion half loses.** The rule is wrong in both halves:
  - Reversion leg: n 1,449, gross -0.079 R, net -0.197 R, against +0.014 R for its twin. This matches the prior
    against it (Nagel 2012: liquidity provision pays in *high* volatility).
  - Trend leg: n 446, gross +0.200 R, net +0.108 R. Its gross t is only 1.79, the leg is carried by 2011 and 2013,
    and 2017 is -0.61 R. It is the retired 1h breakout and TSMOM family (R-04, R-06), whose gross t of 2.4-2.6 could
    not clear the DSR bar.
  - The leg split was descriptive. It is **not a lead**, and no follow-up is proposed.

## What this closes, and what I would not do

- Retire as specified: SA, SB, SD. Do not re-try neighbouring thresholds (2.5 or 4 sigma, VWAP or half-retrace
  targets, another RV window or tercile cut) without a new mechanism. Twin-beating is the binding test, and every
  variant failed it by a wide margin.
- Lines (c) and (b-ii) stay closed by the earlier time-structure screen. Re-opening (c) needs COMEX options open
  interest by strike. That data is paid, so the spend is the **owner's decision**, and I do not recommend it: the
  dates alone showed no range effect.
- The opportunity score (H-16) is unchanged. None of these rules forecasts *which* days pay. The only time effects
  that measure as real are range effects, not direction (`time-structure-2026-10-26.md` 2.2).

Reproduce: `.venv/bin/python docs/research/candidates/opportunity_signals/opp_screen.py --raw raw --cal
~/tmp/ts-data/cal/us_calendar.parquet` (3 worker processes; outputs in `~/tmp/opp_signals/`, not committed).
