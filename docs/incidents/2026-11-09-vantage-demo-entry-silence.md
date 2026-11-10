# 2026-11-09: vantage-demo entered nothing for 9.5 h of open market and nothing alerted

Status: open (closed when vantage-demo has enterable arms again and the session-split fix is merged; the detection
items are shipped)
Severity: S2. A demo account's learning stopped and no service was down. No money was at risk, and exits and caps
were unaffected.
Author: sre. Reviewed by: pending (code-reviewer, research-director).

## Summary
On Monday 2026-11-09 vantage-demo made its last exploration fill at 10:30 UTC (LB-GBPJPY). It made none for the rest
of the open market, at least 9.5 h until the diagnosis around 20:00. Every gold arm on the account had been retired
on 11-06/07. Its unpaired per-instrument controls had spent their daily share (0.25 x 40 = 10 fills) by 09:10, so
the account had nothing left that could enter. No health check or alert covered this. A person found it while asking
why demo volume had dropped.

## Impact
- vantage-demo filled 15 of its 40-a-day budget (211 on Friday 11-06). The demo total for the day was 148 fills plus
  4 cost probes, just under the 150-250 floor of the owner's decision (2026-10-30). About 25 learning fills were lost
  on Vantage.
- Safe all along: nothing changed for exits, stops, the RiskGate or the loss caps. No RiskGate refusals were found and
  no arm stopped for a fault. Every refusal was a rule working as designed.
- The volume drop across all accounts (837 to 148 fills, Friday to Monday) was about 89 % by design: A5 and the
  unpaired-control retirements (55 %) and control thinning (34 %). Volume-drop diagnosis 2026-11-09, section 2.

## Timeline (UTC)
| Time | Event |
| --- | --- |
| 11-06 21:41 | 1c90d3b4 merged: daily entry budgets, thin-control caps (30 fills a cell, 0.25 x max_per_day), the A5 and unpaired-control retirements. Every Vantage gold arm is now retired in the spec (VI0 VC1H VW0 Z6V Z7V ZCV LCZ6V LCZ7V) |
| 11-07 07:04, 16:59 | deploys f9467395 and f1fa6b35 put it live; the engines journal `exploration_arm_retired` at start (vantage 57 rows) |
| 11-07/08 | weekend, market closed |
| 11-09 ~09:10 | vantage's 10 thin-control fills are spent (`thin_controls`; `thin_cell_complete` on the cells already at 30) |
| 11-09 10:30 | last vantage fill (LB-GBPJPY); from here no signal it has may enter (145 `thin_cell_complete` + 122 `thin_controls` refusals that day) |
| 11-09 ~20:00 | detected by a person (the volume-drop question), diagnosed read-only by the SRE from aux.db and the decisions journal |
| 11-09 | detection shipped on feat/entry-silence-alert (62813983, 20dab4d1); supply fix with research-director / PD |

## Root cause
Each change on 11-06 was reviewed and correct on its own: the A5 sign-off, the control-thinning amendment and the
thin-control stop. Nobody worked out what the changes left per account. On Vantage they left only the thin controls,
capped at 10 fills a day, plus a few LB/LBC learning signals. The account's supply was gone by mid-morning.

The process let it through because:
- No step in the review or the deploy computes "arms that may enter, per account" after a config change.
- The only volume alarm, `daily_review.volume_alert_below`, opens an issue only after two low trading days. It
  compares against a plan that was itself overstated: `probe_plan` said Vantage 40 and ICM 15, but the 11-07 probe
  hours allow 14 and 6-8. The weekend made Monday the first low day, so it would have fired on Tuesday at the
  earliest, a day late.
- Health had a `qualification` line for an account with no qualified arm. It did not alert, and it did not count
  retirements at the stop, budget stops or controls left without an active arm.

## Detection
It was found by a person about 9.5 h after the last fill. It should have been found by a health warning within
minutes of the last arm being capped (09:10, so about 09:20 with the 3-poll / 5-min hysteresis). That warning,
`entry_silence:<account>`, is now shipped.

## Recovery
RUNBOOK, "Entry silence on a demo account": read today's candidates by arm and refusal reason with the read-only
aux.db query. With no supply, no restart helps. Volume returns only with new enterable arms, from research-director
and PD. Retired arms are never reopened, and the thin-control limits are never raised to pad volume (quant rulings).

## Prevention
| Item | Type | Where | Status |
| --- | --- | --- | --- |
| `entry_silence:<account>` warns when no arm may enter while the market is open, or after 4 h of London/NY market time without a fill. At most once per account per day, on Telegram | check | goldbot/ops/entry_silence.py, goldbot/ops/checks/entries.py; tests/test_health_entry_silence.py (vantage day replayed through the alert hysteresis: exactly one warning) | shipped (62813983, 20dab4d1) |
| `probe_plan` matches the scheduled probe slots (ICM 7, Vantage 14), so the low-volume plan is not overstated | test | config/settings.yaml; tests/test_daily_review.py `test_the_probe_plan_is_what_the_scheduled_slots_can_send` | shipped (62813983) |
| RUNBOOK recovery for both conditions | runbook | docs/RUNBOOK.md "Entry silence on a demo account" | shipped (62813983) |
| Enterable arms for vantage-demo (15m-1d L2 cells, KoG/G learning arms with paired controls) | decision | research-director / PD (diagnosis fix 1) | open (PD to date by 2026-11-10) |
| Session split from admissible candidates, with roll-forward | code | fix/session-budget-admissible (diagnosis fix 2); entry_silence then uses the engine's helpers | open (lane in flight) |
| Cache the `qualification` view, the same ~2 s config read on every 60 s health pass | code | goldbot/ops/health.py `_qualification_view` | open (sre, 2026-11-12) |

## Lessons
- What went well: the diagnosis took one read-only pass. Every refusal was journalled with its reason, so the causes
  could be counted exactly.
- What did not: we reviewed rule changes one by one and never their combined effect on each account's supply. A
  volume alarm that needs two low days is too slow for a learning system.
- Where we were lucky: it was a demo account, and the gap was learning volume. On a real-money account, the same
  blind spot would hide an engine that has silently stopped entering.
- New rule (to the product owner for docs/AGENT_STANDARDS.md): a change that retires arms or tightens an entry budget
  states the resulting enterable arms per account in its PR. `entry_silence` is the backstop if that step is missed.
