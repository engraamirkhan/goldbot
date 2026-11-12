# Profitability cycle 2026-11-12 (live demo record)

Research director, read-only on the brain. Source: `aux.db` (exploration_trades, exploration_obs, cost_probes,
evidence_exclusions, balance_snapshots), `orders_<acct>.json`, `shadow_book.json`, engine journals (7 days).
Money is broker-net GBP: `pnl` = profit + commission + fee + swap (`goldbot/execution/deals.py`), and slippage is
already in the fill prices. The 132 exploration fills in the evidence_exclusions dq-quarantine span are left out of
the family tables. They are kept in the reconciliation. Windows are by exit time to 2026-11-12 19:00 UTC. The demo
record starts on 2026-10-26, so "30 d" covers 14 trading days. R = pnl / the trade's registered stop loss in GBP.
CIs are clustered by entry day.

**Re-derived from raw data.** The sum of fills plus cost probes, 2026-11-04 23:25 to 2026-11-11 23:26, against the
change in broker balance:

| account | fills sum | broker balance change |
|---|---:|---:|
| icm-demo | +£112.42 | +£112.39 |
| icm-probe | -£83.93 | -£83.84 |
| vantage-demo | +£7.48 | +£7.52 |

## 1. Net P&L per account

| account | 7 d n | 7 d net £ | 30 d n | 30 d net £ | 30 d cost probes £ | week vs balance |
|---|---:|---:|---:|---:|---:|---:|
| icm-demo | 440 | +82.6 | 1,404 | -111.7 | -6.8 (n 5) | +1.0 % |
| icm-probe | 638 | -119.6 | 2,914 | -10.5 | -74.7 (n 533) | -0.8 % |
| vantage-demo | 328 | +58.5 | 885 | -136.7 | -64.4 (n 320) | +0.1 % |

The weekly goal is 20-30 %, which on these balances is about £2,000-3,000 a week per account. The best account
made 1.0 %.

### Signal arms against their random controls (30 d, R per trade, i.i.d. 95 % CI)

| account | signal n | signal R | control n | control R | excess | t |
|---|---:|---:|---:|---:|---:|---:|
| icm-demo | 804 | +0.062 ±0.077 | 600 | -0.089 ±0.097 | +0.151 | 2.39 |
| icm-probe | 1,068 | +0.029 ±0.072 | 1,846 | -0.060 ±0.051 | +0.089 | 1.96 |
| vantage-demo | 306 | -0.032 ±0.102 | 578 | -0.190 ±0.089 | +0.158 | 2.29 |

The signal arms are about break-even after costs. The random controls lose 0.06 to 0.19 R per trade, which is
roughly the cost. Neither clears the deflated bar: K is 174 demo arms (27 families), so the Bonferroni bar is about
t 3.6. The registry K is larger still.

### Other cuts (30 d, every class)

- **Session:**
  - London (07-12 UTC): signal arms n 674, +0.132 R, day-clustered t 2.9, +£288. Their controls: -0.078 R, +£14.
  - Asia (00-07 UTC): signal arms -£228, controls -£153. In total Asia lost £381 at t -4.6.
  - These cuts were made after the data was seen. They are a hypothesis, not evidence.
- **Side:**
  - Shorts: signal arms +£206, controls +£118.
  - Longs: signal arms -£62, controls -£521.
  - Gold fell over the window, and the controls carry that drift. Read every family net of its matched control.
- **Timeframe:** none differs from zero (1m -£29, 5m -£116, 15m -£12, 1h +£7).

## 2. Ranking: honest edge (30 d, R/trade with day-clustered 95 % CI, excess = R minus the same account and timeframe control mean)

| # | arm (family, tf, account) | n | R ± CI | excess | net £ | max DD £ | status |
|---|---|---:|---|---:|---:|---:|---|
| 1 | Z10D three_horizon 5m icm-demo | 55 | +0.216 ±0.150 | +0.33 | +40.0 | -7.6 | learning, active |
| 2 | Z9D htf_reclaim 5m icm-demo | 38 | +0.325 ±0.385 | +0.44 | +41.5 | -12.2 | learning, active |
| 3 | Z4D round_reject 1m icm-demo | 181 | +0.094 ±0.070 | +0.37 | +38.0 | -10.6 | retired |
| 4 | S5B ema_pullback 1m icm-probe | 155 | +0.154 ±0.209 | +0.21 | +67.0 | -32 | retired 11-06 |
| 5 | Z5P extreme_break 5m icm-probe | 42 | +0.198 ±0.446 | +0.22 | +28.1 | -38.2 | learning, active (7 d -£22) |
| 6 | S5A ema_pullback 1m icm-probe | 175 | +0.087 ±0.237 | +0.15 | +33.9 | -31 | retired 11-06 |
| 7 | GS1 session_range_break 15m icm-demo (owner experiment) | 19 | +0.502 ±0.951 | **-0.43 vs its own control GS1C** | +57.9 | -40 | active to 11-27 |
| 8 | Z3D round_break 5m icm-demo | 191 | +0.021 ±0.159 | +0.14 | +13.8 | -64.5 | learning, active |
| 9 | Z7V halfhour_persist 5m vantage | 203 | +0.014 ±0.118 | +0.11 | +15.6 | -74 | retired 11-06 |
| 10 | Z1P tick_surge 1m icm-probe | 40 | +0.047 ±0.347 | +0.11 | +4.3 | -16.2 | learning, active |

Bottom 10 by net £ (30 d, including controls, because they cost money):

| arm | role | n | R | net £ | 7 d £ |
|---|---|---:|---:|---:|---:|
| I0-<instrument> 15m (icm-probe, vantage) | unpaired cost control | 290 | about -0.3 | -£150.7 in 7 d alone | -150.7 |
| ZCD scalp_control 5m icm-demo | control of the H-81 strata that ADR 0061 refuses | 105 | -0.263 | -92.8 | -38.1 |
| S0-SUGAR | control | 13 | -0.966 | -57.7 | 0 (stopped 11-03) |
| F1A hurst_band_break 15m icm-probe | cost read O-11 (26 of 200 fills) | 26 | -0.376 | -45.2 | -14.0 |
| LCZ10D scalp_control 5m icm-demo | Z10D's control | 76 | -0.173 | -44.2 | -33.0 |
| Z7P halfhour_persist 5m icm-probe | learning (retired) | 221 | 0.000 | -0.4 | -31.1 |
| Z4P round_reject 1m icm-probe | learning (retired) | 181 | -0.102 | -41.0 | +10.9 |
| G1A level_break 1h icm-demo | cost read O-12 | 5 | -0.374 | -17.6 | -27.3 |
| F1C hurst control 15m icm-probe | F1A's control | 17 | +0.04 | +0.3 | -22.7 |
| IEX15 / IEX1H gold 15m/1h icm-demo | execution-cost controls | 47 | -0.03 | -37.4 | -37.4 |

## 3. Stop, scale and live candidates

**(a) Stop.** Each of these is net-negative after costs and either controls nothing still entering, or is a cost
read that cannot finish in budget. Together they lost £270 in the last 7 days. The exploration book as a whole made
+£21.5 in the same 7 days.

- The I0-<instrument> index and gold-cross controls: USTEC, XAUEUR, DE40 and US500 on icm-probe; US30 and XAUJPY on
  vantage-demo. They lose £5-9 a trade at 0.1 lots and are still under 30 fills each. Note that I0's `retired_day`
  of 2026-11-06 does not stop the I0-<instrument> rows: they kept entering through 11-12.
- ZCD.
- F1A, F1C and G1A (cost reads).
- IEX15, whose cost cell is complete at 38 fills.

**(b) More demo budget.** Keep these learning arms running to their pre-registered 300-fill read: Z10D, Z9D and
Z5P. The daily slots that (a) frees (the cap of 70 a day on icm-demo is unchanged) go to them automatically. No cap
is raised.

**(c) Live candidates: none.**

- GS1, the only arm that is up at size, earned less than its own random-entry twin GS1C over the same 19 trades
  (+£57.9 against +£125.7). Its profit is the short drift in gold, not the signal.
- Z10D is the strongest arm. To become a candidate it still needs:
  - the 300-fill read against LCZ10D;
  - the confirm window (2018-25);
  - deflation at the real K;
  - cost cells of at least 30 fills.

**Owner's method (rule 1(c)).** The shadow `box_break_close` arms, as encoded (gross, before costs):

| timeframe | n | mean R |
|---|---:|---:|
| 15m | 41 | -0.05 |
| 30m | 11 | -0.18 |
| 1h | 6 | -1.0 (all six lost) |
| 4h | 3 | -1.0 (all three lost) |

This does not reproduce the owner's £50+ a day. A comparison trade by trade with the owner's own trades is still
missing.

**restart_gap (R129).** 4 shadow wins were dropped (tsmom 3, mean_reversion 1). No gap losses were found. No family
verdict changes.

## 4. Proposed config change (not applied; the PD and PO to route it through implementer and trading-safety)

`config/exploration_arms.yaml`: add `retired_day: "2026-11-13"` and
`retired_rule: "profitability-2026-11-12 (a): net-negative control or cost read with no live counterpart"` to these
rows: `ZCD`, `F1A`, `F1C`, `G1A`, `G1C`, `IEX15`. These fields are not hashed, and open positions keep every exit.

`config/accounts.yaml`:

- `icm-probe.exploration.arms`: drop `I0-USTEC`, `I0-XAUEUR`, `I0-DE40` and `I0-US500`.
- `vantage-demo.exploration.arms`: drop `I0-US30` and `I0-XAUJPY`.
- The rollback for both is the documented "drop the line, restart", once flat.
- Or the engine retires them through `retired_day` if the implementer extends `spec_retired` to I0-<instrument>.
  That is a code change.
- `icm-demo.exploration.arms.ZCD`: remove it once it is flat (it is retired above). Keep LCZ9D and LCZ10D, the
  controls matched to the arms being kept.

Nothing in RiskGate, the daily loss limits, `max_loss` or sizing changes. Those stay owner-only.

## 5. Does the "pass budget 5.0 s" warning cost trades?

The warning was logged 3,711 times on icm-probe and 3,563 times on vantage-demo in 7 days (every minute since
11-10). It was never logged on icm-demo.

- **What it costs now: nothing.**
  - Signal arms lost 3 decisions to `stale` in 7 days (2 session_range_break 15m, 1 round_reject).
  - 15m/1h signal-to-send latency on 11-11 and 11-12: median 9-11 s and p90 13-14 s on icm-probe and vantage-demo.
    icm-demo, which never logs the warning: median 7-9 s and p90 15 s.
- **What it cost before the 11-10 split fix:** 2,480 1m entries refused as stale (icm-probe 650 on 11-06 and 874 on
  11-09; vantage-demo 899). All of them were S0-<instrument> random controls, so no signal was lost.
- **It is now log noise.** The per-book phases add up to under 1 s, and the warning repeats every minute because
  the "15m+ work carried" state resets the once-per-run flag. This is for the SRE, not research.

## Biggest lever now

Stop paying for controls that control nothing: they lost £270 in 7 days, against +£21.5 for the whole exploration
book. Then judge every survivor only against its matched control. GS1 shows how raw P&L misleads in a trending week.

K footnote: 174 demo arms and 27 families in this read. Gaps in the data:

- 14 trading days.
- No arm has 300 fills against its matched control.
- Owner trade log not on the brain.

## Applied (R133, 2026-11-12; quant ruling on the review)

- `retired_day: "2026-11-13"` with the section 4 rule on ZCD, F1A, F1C, G1A, G1C, IEX15 and the five named
  I0-<instrument> rows still entering: I0-USTEC, I0-XAUEUR, I0-DE40, I0-US500 (icm-probe), I0-US30 (vantage-demo).
  I0-XAUJPY already had its own retirement (2026-11-09) and is left as it is.
- Why they kept entering after I0 retired: not a leak. Each I0-<instrument> is its own spec row, and
  prereg-amendment-2026-11-06-control-thinning.md point 4 stops it only when its instrument's cost cell has 30 fills
  (`thin_cell_complete`); the I0 retirement never covered them. The unnamed I0-<instrument> rows stay under that rule.
- The accounts.yaml registrations are kept so open positions keep every exit (a dropped registration is closed at
  once as an orphan); they and ZCD are dropped once flat (backlog 187).
- ZCD leaves the H-81 pairing: from 2026-11-13 Z9D and Z10D are read only against their dedicated LCZ9D and LCZ10D
  (and Z3D against LCZ3D).
- O-12 (G1A/G1C, H-71) is **abandoned, inconclusive** at 5 of 100 fills: stopped for cost, not failed. O-11 (F1A/F1C,
  H-70) likewise stops at 26 of 200 fills, inconclusive.
