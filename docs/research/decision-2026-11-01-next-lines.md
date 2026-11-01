# Decision 2026-11-01: which research lines remain, and when research spend pauses

Research director. State: every line has failed on history net of measured costs (H-16, H-17, H-80 R2, H-82, H-83, H-84, discovery v1 K 864 and v2 K 1,704, TV parser T-1, H-86..88); 0 demo families beat the random control; the owner's manual book is gross ~0, net -£258. Cost of record (`config/cost_of_record.yaml` cor-2026-10-31.2): ICM RT $0.164-0.204/oz (commission $0.08 of it), Vantage $0.158-0.293. K before this memo ~2,971.

**New evidence, measured here** (director, raw Dukascopy 1m 2010-2017 + keyless US calendar; 14 event types looked at once, all counted: K +15 -> ~2,986). Signal sign(mid at t+5m - mid at t-1m), hold t+5m -> t+60m. Only `fomc_statement` stands out: n 64, mean +$3.90/oz (t 3.98; Bonferroni x14 p ~0.003), median +$3.13, 73% positive, **8 of 8 years positive**, trimmed (3+3) +$3.57, net at Dukascopy bid/ask +$3.49 (t 3.58). Every other type |t| < 2.5 (NFP -0.67, t -0.89; retail sales t 2.46 fails Bonferroni). Release-minute spread rises only ~$0.13 (0.45 vs 0.33).

## (1) Ranked lines (max 3; two admitted)
| # | Line | Mechanism, evidence grade | Prior | Cost | Kill rule (frozen now, before 2018+ is opened) |
|---|---|---|---|---|---|
| 1 | **H-89 post-FOMC continuation** | Slow digestion of policy news; intraday momentum strongest on news days (Gao, Han, Li & Zhou 2018 JFE, R, adjacent asset); gold is the rate-sensitive asset. Falsified if 2018+ (press conference every meeting from 2019) shows no drift | 30% | 3 agent-h confirm; +6 h demo arm if it passes | One look, 2018-01-01..2025-09-30 fomc_statement: same signal, entry t+6m bar close at ask/bid (1 min extra latency), exit t+60m at bid/ask, +$0.30/oz extra cost. PASS only if mean net > 0 with t >= 2, >= 5 of 8 years positive, share > 0 >= 55%. Any leg fails -> retired, final. PASS -> holdout once (2025-10 on), then a 0.01-lot demo arm |
| 2 | **Release-minute execution cost** (cost arm, ADR 0061 exempt) | Line 1 is only tradable if the release-minute fill is near the bar spread | n/a (measurement) | 4 agent-h | 0.01-lot market round trips at t+6m on both demo brokers at FOMC (next 2026-12-09) and NFP/CPI. Stop at 30 release fills per broker or 2027-01-31. If release-minute RT > $1.00/oz, line 1 is not tradable at any size |

**Declined, with the number:**
- *Limit-order entries (L1)*: commission $0.08 is unreducible; scalp R ~$3.3 (0.166 / 0.05 R), so commission alone is 0.024 R, above the measured gross bound of +/-0.010 R (hb-exits, 337k entries). Best case saves the half spread ($0.043) before adverse selection. Built only as a fill mode for line 1 if it passes.
- *Volatility-regime sizing*: scales an edge, cannot create one; at gross ~0 it only reshapes variance. Applied inside line 1 if it passes, no separate trials.
- *1d-1w trend/carry*: H-84 V3 gross +0.125 R (t 0.84, n 93); the CFD long swap (-60 USD/lot/night) costs 0.13-0.15 R a trade, above any plausible single-asset TSMOM gross. Only testable on a swap-free venue (futures/ETF), which is an owner spending decision.
- *Options-implied*: free means daily GVZ only; already failed as a range input (H-16) and as VRP (H-84 V4 gross +0.077 R, t 0.62). No free intraday options data.

## (2) Pause recommendation (owner-only: spending)
Even if H-89 passes everything, it is ~8 trades a year at ~$3.5/oz: at 0.01 lot (a $10 stop ~1% of £1,000) about $28 a year. Nothing we have measured reaches £50 a day, and the owner's own book (gross ~0, net -£258) points the same way: costs are the whole result.
- **A (recommended):** run line 1 (confirm decided by 2026-11-04) and line 2. If line 1 fails, **pause research spend on new hypotheses** until there is a new free point-in-time source with a mechanism, a swap-free instrument, or the Q1 state-of-the-art refresh (2027-01). Meanwhile demo runs only the random controls, cost probes cut to what the protocol needs (~60-90 fills a day) and the release-minute probes. Perf-report and daily review stay automated, discovery stays frozen (A4), no new families, no live pilot.
- B: keep researching on a reduced budget (1 line a month, pre-registered). Expected value is low given ~2,986 trials and 0 survivors.
- C: fund a futures or ETF venue to test 1d-1w trend without the swap (new spending; owner decides).
