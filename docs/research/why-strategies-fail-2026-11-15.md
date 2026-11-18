# Why every strategy fails: root-cause audit (2026-11-15)

Research director, base `9d1abbc7`. This is a measurement and method audit. It tests no hypothesis, writes no
registry row and does not touch the holdout (2025-10..2026-09). The parity window starts 2026-10-26, after the
holdout. **K +0.** The EVAL-2 cell sensitivity in section 3 re-reads results that were already seen. It is a
diagnostic, not evidence, and it reopens no line. Only aggregates appear here. Inputs and scripts are local: brain
`aux.db` (read-only, pulled 2026-11-15 06:24 UTC), Dukascopy 1m 2026 in `raw/`, and `~/tmp/why-fail-1115/`
(`parity.py`, `an1.py`, `an2.py`, `power.py`).

## Answer in one screen

Four causes. Each one is measured, and they are ranked by how much of "zero survivors" they explain.

1. **We searched where there is no gross edge.** At 1m-15m, single-rule entries on gold earn a gross of +/-0.01 R,
   on 337k history entries and on demo (section 1). This is the market, not our tooling. The backtester's fills are
   unbiased against the broker: the gross gap is -0.012 R a trade, barriers agree on 94.5 % of trades, and the
   trade-level correlation is 0.91 on 3,653 fills.
2. **Where cost is small (1h-1d), the gates cannot pass a real edge.** Take a true net edge of +0.10 R a trade
   (sd 1.2 R). The chances it survives are:

   | timeframe | explore | TEST-OLD | 2018-25 confirm (batch 15) | L5 deflated Sharpe at K 21,333 |
   |---|---|---|---|---|
   | 1h | 29 % | 8 % | 1 % | 0 % |
   | 1d | 8 % | 3 % | 0 % | 0 % |

   The L5 deflated Sharpe needs t >= 5.69, which is about 6,100 trades. A 1d rule would need about 67 years.
   "Zero survivors" was the expected result whether or not slow edges exist (section 3).
3. **Research charges 2.6x what the broker actually takes (ICM).** On 993 ICM random-control fills, the realised cost
   against the research mid-bar replay is **$0.116/oz [0.093, 0.140]**. The screens charge **$0.30-0.32/oz**
   (`cor-2026-10-31.2`, charged round trip plus overshoot). The bias is conservative, so it removes no false
   positives. It is real at 1m-5m: 0.10 R charged against 0.04 R paid on a $3 stop. Correcting it adds at most
   1 of 133 gold EVAL-2 cells with excess t > 2. It is necessary, not sufficient (section 2).
4. **Volume made it worse.** Every sprint raised the bar for every later line. K went from about 1,258 to 21,333 in
   two weeks, which moved the deflated-Sharpe t from 4.96 to 5.69. On demo, the broker-traded random controls are the
   largest P&L item: **-£572 over 3 weeks**, against -£51 for all learning arms. Replay twins do the same job and
   cost nothing (section 4).

The owner's own manual record (kept local) shows no gross edge after costs; it is not used as a benchmark.

## 1. Backtest-vs-demo parity (task 1)

**Method.** Population: every closed demo XAUUSD exploration fill that exited before the end of the Dukascopy file,
2026-10-26 14:00 to 2026-11-06 18:55 UTC. That is 3,653 fills on 10 session days: icm-demo 1,182, icm-probe 2,021,
vantage-demo 450. It excludes the 135 rows in `evidence_exclusions`, non-gold symbols and pyramid adds.

Each fill's own send minute, side, `stop_usd`, `target_usd` and `hold_s` were replayed with the research
backtester's mechanics (`triple_barrier_next_open_1m`):
- Dukascopy 1m **mid** bars;
- entry at the mid open of the send minute;
- stop and target searched on the mid high and low, stop first;
- a minute that opens through the stop fills at its open;
- time exit at signal + hold. Where the demo closed by hold, time, weekend or broker, the cut is at the demo's exit
  minute.

Broker R = `pnl` / FX / oz / `stop_usd`. That is the broker-booked profit + swap - commission.

**Fills (gross).**

| measure | value |
|---|---|
| barrier agreement (stop / target / time) | 94.5 %; mismatches symmetric (46 bt-target where the broker stopped, 39 the reverse) |
| trade-level correlation, broker R vs replay gross R | 0.913 |
| gross bias, broker price move minus replay, all fills | **-0.012 R** (about -$0.05/oz) |
| ICM entry gap (fill vs replay entry, adverse +) / exit gap | +$0.008 / -$0.009 a trade: they cancel |

**Cost actually taken = replay gross minus broker net, $/oz** (matched barrier, day-clustered 95 % CI):

| cell | n | realised cost | research charges (COR charged + overshoot, frozen 0.23 + [0, 0.70]) |
|---|---|---|---|
| ICM random controls | 993 | **0.116 [0.093, 0.140]** | 0.317 (COR) / 0.23-0.57 (frozen) |
| ICM all matched | 3,021 | 0.082 [0.045, 0.118] | 0.317 |
| ICM all fills, time exits included | 3,203 | 0.140 | 0.317 |
| ICM by session (Asia / London / NY) | 702 / 1,042 / 1,277 | 0.111 / 0.063 / 0.080 | 0.343 / 0.284 / 0.329 |
| ICM by exit (stop / target / time) | 1,505 / 1,165 / 351 | 0.114 / 0.001 / 0.212 | |
| Vantage all matched | 432 | 0.140 [0.006, 0.274] | 0.590 |

In R, research charge against cost paid, by timeframe (all fills):

| timeframe | charged | paid |
|---|---|---|
| 1m ($3 stop) | 0.101 R | 0.029 R |
| 5m | 0.074 R | 0.032 R |
| 15m | 0.047 R | 0.065 R (n 140, noisy) |
| 1h | 0.022 R | n 27 |

**Why the cost of record over-states the cost.** `cor-2026-10-31.2` builds the round trip on the broker's own mid:
spread + max(0, entry slip) + max(0, exit slip) + commission = $0.166, plus $0.11-0.145 overshoot on stop exits. The
research P&L, however, is measured from the Dukascopy mid. ICM fills sit only about $0.01 a side from that mid, not
the $0.043 half-spread, and the zero floors throw away favourable slippage. What a screen must charge is the
**fill-to-research-bar** cost, and that is what this table measures.

Commission ($0.080 ICM, $0.054 Vantage) is about two thirds of the real cost. The stop-exit overshoot shows as the
stop-minus-target gap, about $0.11, which agrees with the COR overshoot.

**Caveats.**
- The window is 10 session days in one regime. The CIs are day-clustered.
- Targets show about $0.08 positive slippage on demo (target exit gap -$0.18 against an entry gap of +$0.10). Real
  money may not give it. The first real-money fills must check this before any screen relies on the target cell.
- Vantage cells are too small to price by session.

**Verdict, task 1.** The backtester is **not optimistic**. Its gross matches the broker to 0.01 R, and its costs are
**pessimistic by about 2.6x** at ICM. Optimistic simulation does not explain zero survivors. Pessimistic cost
explains part of the 1m-5m kills, but not the main part (section 2).

## 2. Cost model audit (task 2)

Measured cost per trade against the gross edge each timeframe would need:

| timeframe | typical stop | real cost (R) | charged (R) | history gross on our entries | does the cost decide it? |
|---|---|---|---|---|---|
| 1m | $3 | 0.03-0.04 | 0.10 | +/-0.01 (H-82, 337k entries) | **no**: net is negative at either cost |
| 5m | $4.5 | 0.03 | 0.07 | +/-0.01; Z10/Z9/Z5 excess -0.009..+0.004 on 14-15k each | **no** |
| 5m, EVAL-2 cells (1 ATR(5m) stop) | about $1 | about 0.10 | 0.26 | median +0.015 | **yes, by design**: a 1-ATR stop at 5m makes cost a quarter of R |
| 15m-30m | $6-13 | 0.02-0.06 | 0.05-0.16 | median -0.028 / +0.010 | marginal |
| 1h-4h | $17-30 | about 0.01 | 0.02-0.08 | median +0.020 / +0.024 | no: power decides it |
| 1d-1w | | swap-dominated | 0.03-0.05 | median +0.060 / +0.125 | no: power decides it |

Sensitivity on the 133 gold cells (EVAL-2 explore rescore, `~/tmp/evalaudit2/cells.csv`):

| cost charged | cells with net > 0 | of those, cells with excess t > 2 |
|---|---|---|
| 1.0x the screen cost | 60 | 11 |
| 0.45x (the ICM parity ratio) | 69 | 12 |

**The bar is right in direction and too high in level.** It should fall about 2.6x at ICM and stay at the measured
upper CI. This changes 1m-5m economics: a 5m arm with a $4.5 stop needs a gross of about 0.06 R, not 0.15-0.19 R,
under the 1/3 speed limit. But our 1m-5m entries have no gross to clear even the lower bar.

The cost rule should also forbid the geometry that guarantees failure: a 1-ATR stop on a 5m or 15m decision bar pays
0.16-0.26 R a trade.

**Swap** matches settings: ICM -4.8 / +3.3 %/yr, measured. Long gold held on CFD pays roughly what gold's long-run
excess return has been. That is why the 13-market trend book lost 70 % of its gross to financing (POS-1 A1).

## 3. Gate audit (task 3)

Thresholds as coded and pre-registered:

| gate | threshold | source |
|---|---|---|
| EVAL-2 explore | t >= 2 | prereg-2026-11-09-eval2 |
| TEST-OLD 2008-09 | BH at q 0.10 over m 3, about z 2.13, read only if power >= 0.3 | prereg-2026-11-09-eval2 |
| 2018-25 corroboration | batch 15 under pure alpha-spending, alpha 0.000155, about z 3.88 | prereg-2026-11-09-eval2 |
| L5 | `research/gates.py`: deflated Sharpe >= 0.95 at the registry K (`var_sr_trials` = 1/n), >= 200 trades, MIN_CANDIDATES 1,500, >= 60 per fold, 3 positive years including 2021 or 2022 | `research/gates.py` |

The deflated-Sharpe hurdle in t, by K:

| K | t needed |
|---|---|
| 60 | 3.99 |
| 1,258 | 4.96 |
| 3,471 | 5.24 |
| 21,333 | **5.69** |

**Power for a true net +0.10 R a trade, sd 1.2 R** (the demo's measured sd is 1.10-1.51 R by timeframe). Trade
counts are typical event-rule frequencies from the EVAL-2 cells.

| timeframe | trades a year | P(explore, 8 y) | P(TEST-OLD, 2 y) | P(confirm, 7.75 y) | P(L5 deflated Sharpe, 2010-25) | P(demo read at 300 vs 1 control) |
|---|---|---|---|---|---|---|
| 5m | 600 | 1.00 | 0.78 | 0.96 | 0.99 | 0.27 |
| 15m | 100 | 0.64 | 0.17 | 0.06 | 0.01 | 0.27 |
| 1h | 37 | 0.29 | 0.08 | 0.01 | 0.00 | 0.27 |
| 4h | 12 | 0.12 | 0.04 | 0.00 | 0.00 | 0.27 |
| 1d | 6 | 0.08 | 0.03 | 0.00 | 0.00 | 0.27 |

Trades needed for 80 % power at 0.10 R:

| gate | trades |
|---|---|
| explore | 1,161 |
| TEST-OLD | 1,268 |
| batch-15 confirm | 3,208 |
| deflated Sharpe at K 3,471 | 5,320 |
| deflated Sharpe at K 21,333 | 6,134 |

A published-quality strategy with a net annual SR of 0.8 on daily P&L needs 12.6 years for t 2. It needs **66.6
years** for the L5 deflated Sharpe at K 21,333.

**Reading.**
- The gates are **valid** (they control false discoveries). They are **mis-specified for this program** because
  they have no power where a cost-feasible edge could live.
- The only place the chain has power is 5m and faster, where the gross is measured at zero. A 0.10 R edge at 1h is
  killed by explore 71 % of the time. Past explore, it dies at the 2018-25 batch-15 alpha, which every earlier batch
  spent.
- MIN_CANDIDATES 1,500 and 60 per fold exclude 1d rules outright.
- The demo read at 300 fills against a single control has 27 % power. It ranks a true 0.10 R arm below its control
  15 % of the time. With 20 replay twins it is 41 % and 8 %.

**What is not wrong:**
- the BH alpha-spending is valid;
- CPCV/PBO only veto (ADR 0003) and decided nothing;
- the holdout seal held.

Integrity stays as it is. The fix is to stop running tests that have no power, not to lower bars (section 6).

## 4. Rule 1 money read (10-26..11-14, exploration ledger, re-derived from raw fills)

Net GBP, closed, after `evidence_exclusions`:

| account | total | random controls | learning arms | owner experiment | cost arms |
|---|---|---|---|---|---|
| icm-demo | **-140.46** | -282.80 (584) | +77.38 (434) | +44.54 (18, GS1) | -9.76 (4) |
| icm-probe | **+31.95** | -83.90 (1,438; non-gold -72.88) | -52.64 (527) | | -18.57 (11) |
| vantage-demo | **-173.51** | -205.34 (547; non-gold -172.45) | -75.37 (150) | | |

Counts in brackets are fills.

- **(a) Leaks.** Random controls are more than 100 % of the loss on icm-demo and vantage. Their expected cost alone
  is about £235 over 3 weeks: 2,569 fills x $0.12. The SUGAR stop-inside-spread leak (14 fills, -£67.60) was 11-03
  only; the spread-over-stop fix and the 11-13 gold-only ruling closed it, with no SUGAR fill since. **Fix: the
  broker-traded random controls become replay twins** (parity above: gross bias -0.012 R, r 0.91). Keep only the
  cost probes the cost protocol needs.
- **(b) What earns.** Nothing beats its matched control (perf reads to 11-13; Z10D/Z9D/Z5P killed on their 2010-17
  replay). GS1 (owner experiment) is +£44.54 on 18 fills, but -0.43 R against GS1C (`profitability-2026-11-12.md`),
  and it ends 11-27 under its own rule.
- **(c) The owner's method.** Box break + MFI is coded and failed history (R-15, H-80 R2: runner - fixed -0.018 R,
  t -1.12, n 5,355). See section 5.

October broker figures in the brief (-138.91 / +109.31 / +29.30) cover a different window and include pre-reset
books. They are not re-derived here.

## 5. The owner's manual record

The owner's own manual record (kept local) shows no gross edge after costs; it is not used as a benchmark.

## 6. Next research plan (task 4): at most 5 lines

Expected net from research this month is **about £0/day**. If EVAL-2 batch 1 holds it is £1-3/day on a £10k demo.
Nothing here is the £50/day route. The plan spends on the two things that make any future edge measurable and
cheap, and one existing line.

| # | Line | Card (mechanism / gross / cost / rate / net) | Owner | Budget, kill rule, date | Target £/day |
|---|---|---|---|---|---|
| 1 | **Demo as an instrument**: replay twins replace broker-traded random controls; cost probes at protocol minimum | Ops change, K +0. Saves about £80/week expected cost plus about £190/week of variance on demo P&L | performance-analyst + data-engineer (PD and trading-safety sign-off; RiskGate unchanged) | Revert if retained-probe parity drifts > $0.05/oz or barrier agreement < 90 % over 300 fills; decide by **11-22** | +£11/day (cost removed), no edge claimed |
| 2 | **Cost of record v3 = fill-to-research-bar cost** (ADR 0064 amendment) | Measurement, K +0. Broker x session x exit cell, day-clustered upper CI; replaces spread + floors on the broker mid; 1-ATR stops at <= 15m flagged. New pre-registrations only; closed lines are not rescored (rule 3) | microstructure-researcher | Done by **11-20**; target-fill slippage re-checked on the first real fills | enables 1m-5m arms needing 0.06 R, not 0.15 R |
| 3 | **Power-first gates** (method, K +0) | (i) No card starts unless power >= 0.5 at its card edge on every rung it must pass (extends rule 2). (ii) L5 options for the expert panel: keep the deflated Sharpe at total K (then no 1h-1d rule can ever pass); K_eff by clustered family; or pre-registered alpha-spending with a forward holdout. Recommendation: the last, with K reported. (iii) Rule-only path without MIN_CANDIDATES 1,500 / 60 per fold for 4h-1d, using power-based n | evaluator (+ quant-reviewer) | Memo by **11-24**, panel ruling by **11-27**; any gate change through ADR and quant sign-off; going live stays owner-only | none directly; unblocks slow lines |
| 4 | **EVAL-2 batch 1** (kept: C03, C02, S21, S20, B07) | As section 0 of `research-plan.md`; gold 1d cells median excess +0.058 R | research director / evaluator | TEST-OLD read by **11-24**, power floor 0.3, close or promote that day | £1-3/day if it holds |
| 5 | **Empty on purpose** | A sixth card needs gross >= 2x the parity cost **and** power >= 0.5 at its frequency. None qualifies today. 1m-15m single rules are closed; exits and sizing amplify an edge, they do not make one (H-82 DiD +/-0.0017 R) | strategy-researcher drafts; director decides | Opens only for a card meeting both | — |

**Closed by this audit (not re-run):**
- the premise "owner manual results prove intraday gold is tradeable" (section 5);
- broker-traded random controls as the matched null (replaced by twins).

**Data missing, by value:**
1. **real-money fills**, to confirm target slippage and the $0.12 cost. This needs the owner-confirmed pilot (owner's
   call).
2. **the tick re-pull (BACKLOG 213, about 3 days)**: intrabar fill order for 1m-5m and a check on the
   stop-first ambiguity (6 % of bars).
3. **pre-2003 daily history and COMEX volume/positioning**: sample for 1d power, the binding constraint.
4. ICM depth is not needed: costs are now measured from fills.

## Sources

- `aux.db`: `exploration_trades`, `evidence_exclusions`.
- `raw/xauusd_1m_dukascopy_2026.parquet`.
- `config/cost_of_record.yaml` (`cor-2026-10-31.2`).
- `goldbot/research/{gates,metrics,arms}.py`.
- `scripts/run_arm_backtest.py`, `goldbot/labels/triple_barrier.py`.
- `prereg-2026-11-09-eval2.md`.
- `~/tmp/evalaudit2/cells.csv`.
- `profitability-2026-11-12.md`, `research-plan.md`, `hypotheses.md`.
