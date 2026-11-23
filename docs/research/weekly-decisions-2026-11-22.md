# Weekly review 2026-11-22: decisions and next steps

Week 15-20 Nov 2026 (market closed Fri 22:00 UTC). Demo accounts only. Full analysis: performance-analyst report
(local, 2026-11-22); P&L reconciliation: finance-auditor (local, 2026-11-22). Figures net of spread, commission and swap,
GBP, tied to broker balances.

## Results

| | Net | Trades |
|---|---|---|
| Week 15-20 Nov | **-£412.63** | 311 |
| Previous week 8-13 Nov | +£34.48 | 654 |
| Cumulative to 20 Nov 22:00 | **-£866.39** (broker basis -£865.79) | 6,568 |

- 85% of the week's loss (-£349.47 on 87 fills) came from three random execution-calibration controls (VEX15, VEX1H,
  IEX1H) that placed live demo orders. Without them the week was -£63.16.
- Gold fell 9.7% in the week; longs lost £545, shorts made £144. No arm adjusts for trend.
- No strategy arm beats its random twin beyond noise. GS1 trails its twin GS1C (+£20.66 vs +£112.11, 28 trades each).
- Red-box week (prereg-amendment-2026-11-15-redbox-week.md): every cap held (family -£19.14 vs £300); arm vs control
  is noise at every timeframe (pooled -0.15 R vs -0.17 R). No edge.
- Earlier audit gaps explained: the £25.34 / 28-trade difference was an 11-hour earlier cut; the "+£1,134" was a
  funding-baseline error, never profit. £0.72 remains (one unrecorded Brent close, 4 Nov).

## Fed back into the system (done)

| Change | Where | Check | Status |
|---|---|---|---|
| Only arms with evidence place orders; random controls and cost probes replay-only; red-box arms retired | PR #424, deployed 2026-11-23 02:25 UTC | zero orders from no-evidence arms | holding: 0 orders since the open |
| True costs: cost_version on every exploration row | PR #418, deployed 20 Nov | cost_version present | 4 of 4 rows after deploy |
| Learning check | cycle 2026-11-22 | a model is on only if it beats no-model out of sample | all models off; none passes |
| GD-1 learning arm start-date fix (`learning.since`) | PR #424 | first entry not refused | missed Mon 23 Nov 00:00 (engine started before the fix); first entry Tue 24 Nov 00:00 UTC |

## Consequence

Since the 22 Nov 23:00 UTC open the system places no orders: the only arm allowed to (GD-1) starts Tuesday, and no
model is good enough to trade. Losses have stopped; so has live learning, except replay labels. That is the state the
next steps address: trade only on evidence, and grow evidence cheaply.

## Next steps

| # | Step | Owner | Due | Pass check |
|---|---|---|---|---|
| 1 | GD-1 first entries on icm-demo and icm-probe; stop within the £40 swing cap | trading-safety + sre | Tue 24 Nov 07:00 UTC | one order each at 00:00, exit by 07:00, stop ≤ £40 |
| 2 | Replay labels for every no-evidence arm keep accruing (zero cost evidence) | performance-analyst | daily | candidates recorded per arm per day > 0 for each run arm; no orders |
| 3 | GS1 decision on its twin difference, not its total | evaluator | Fri 27 Nov | verdict recorded per the GS1 card |
| 4 | KoG weekly red-box levels: ship the zero-order shadow scorer after quant sign-off; score each Sunday post after Friday close | research-director | Fri 27 Nov (first score) | 22 Nov post scored vs twins, net of costs, no orders |
| 5 | Learning rebuilt on replay labels: one meta-model, fold-internal, switched on only if it beats no-model out of sample on the sealed-window rules | ml-researcher | Fri 4 Dec | OOS net R uplift > 0 with day-clustered CI excluding 0, else reported off |
| 6 | RB1H / RB4H history replay (red-box at 1h/4h over 2010-25 windows) | strategy-researcher | Fri 4 Dec | verdict per the amendment's history rule; K booked only if powered |
| 7 | Weekly review every Saturday after close: this record, a #34 post, and each step's status | program-director | Sat 28 Nov | weekly-decisions-2026-11-28.md merged |
| 8 | Move cycle, deploys and registry publish off the Mac onto the brain (`goldops`, capped slice) | sre | pinned by owner until usability and performance are validated | timers enabled, one full cycle run with the Mac off |

Q4 trial budget: 3,416 of 3,500 used (84 left). New research spends K only on powered, pre-registered cards.
