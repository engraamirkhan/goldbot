# Amendment 2026-11-06: control thinning under the daily entry budget

Status: **in force with the release that carries `exploration.daily`** (PD 2026-11-06, owner directive of the same
day: "improve the system significantly now", within the owner decision of 2026-10-30: demo 150-250 fills a day in
total). Amends the control registrations of prereg-2026-10-25-high-volume.md, learning-2026-11-02.md and
learning-mi-2026-11-02.md (the per-instrument controls S0-<key>, I0-<key> of the multi-instrument plan, lane L3).

## What was measured

Demo fills 2026-11-04 11:00 to 2026-11-06 19:30, all three demo accounts:

- 830-860 fills a day against the standing cap of 150-250.
- 69% of them were random controls: the per-instrument S0-<key> / I0-<key> controls added with the 25-instrument
  list (#331; 262 S0-* and 120 I0-* fills on icm-probe, 204 S0-* and 48 I0-* on vantage-demo), the LC* learning
  twins, S0/S0R and ZCD/ZCV.
- Edge/learning arms -£0.173 a fill, controls -£0.113 a fill; no arm beats its control.

A control measures the null its arms are read against. Fills beyond what pairs the arms buy no read; they spend the
cap and the money.

## The rule (engine: `goldbot/engine/exploration_budget.py`; accounts.yaml `exploration.daily`)

Per account, counted as fills by their signal close (aux migrations 23-24 `signal_ns`) since the UTC day's start, every
book together (quant ruling 2026-11-06 Q1/Q3 amend the first draft of this section):

1. At most `max_per_day` exploration fills (icm-probe 85, icm-demo 70, vantage-demo 40: 195, plus the cost probes'
   55-a-day plan = 250), at most `max_per_hour` in one clock hour, and per session (asia / london / newyork) at most
   max(1, round(max_per_day x its share)), the share being the account's edge (strategy, learning, cost) candidates
   in that session over the trailing 10 trading days (equal thirds with none): a saturated Asia never stops the first
   New York fill.
2. A **paired** control is admitted per pair, never pooled and never "after an edge fill": its arms are the learning
   arms naming it (`learning.control`, `params.control` "learning:<ids>"), `research.arms.COST_CONTROLS`,
   `arm_rules_levels.CONTROLS`, ZCD -> Z3D/Z9D/Z10D (and ZCV -> Z6V/Z7V, GS1C -> GS1). Matched-time Bernoulli
   thinning: p = min(1, controls_per_edge (1.0) x its active arms' fills / its own signals) in the signal's (session,
   UTC hour) cell over the trailing 10 trading days before today; admitted when hash_u(control id, signal close) < p
   (seeded, restart-safe). p is the candidate's recorded propensity, taken or thinned (`control_thinned`). A cell
   where its arms filled and it never signalled takes p = 1, one where they never filled p = 0; a pair whose active
   arms have no fill in the window at all takes p = 1 (cold start), and a pair with no active arm p = 0. An unpaired gold
   control (S0, S0R) is paired with the account's active gold scalp edge arms that name no other control, and is
   thinned like any paired control (quant ruling 2026-11-06 (d); a learning arm's dedicated twin does not count as
   another control: the H-81 strata were registered against the borrowed S0).
3. An **unpaired per-instrument control** (a non-gold control whose row names no learning arm: S0-<key>, I0-<key>)
   takes at most `thin_control_share` (0.25) x max_per_day fills a day (`thin_controls`).

4. **Control stops (PD+PO decision 2026-11-06; performance-analyst kill list 2026-11-06: the unpaired controls were
   the biggest drain, about -£58 a day).** A control measures the null of the arms it pairs; one that pairs no active
   arm stops entering:
   - the unpaired per-instrument controls (the 48 S0-<key> / I0-<key> rows; no active arm on a non-gold symbol runs
     their geometry, the LB-<key> arms have their dedicated LBC-<key>) stop for good once their instrument's cost
     cell (every arm's fills of it on the account, LB-<key> and LBC-<key> too) has `thin_control_fills` (30): the cell
     is their only consumer and needs n >= 30 (`thin_cell_complete`). Ten had their 30 on 2026-11-06 (XTIUSD, USDCHF, EURJPY, EURUSD, EURGBP, GBPJPY,
     USDJPY, GBPUSD, XAUJPY, XAGUSD); the rest stop at theirs;
   - I0, VI0 (15m), C1H, VC1H (1h), W0, VW0 (4h) retire on 2026-11-06 (spec rows `retired_day`): only BXR*/XBB15 used
     these geometries, and those retire the same day (A2: the BXR exemption ended with the H-80 R2 fail; XBB15 under 2
     fills a session day);
   - with kill rule A5 (quant-reviewer signed 2026-11-06, docs/research/a5-signoff-2026-11-06.md) the learning arms
     S5A, S5B, Z4D, Z4P, Z6D, Z6V, Z7D, Z7P, Z7V retire and their dedicated controls LCS5A, LCS5B, LCZ4D, LCZ4P, LCZ6D,
     LCZ6V, LCZ7D, LCZ7P, LCZ7V stop with them (learning-2026-11-02.md);
   - ZCV (H-81 unit control on vantage-demo) paired only Z6V and Z7V there: it retires with them. ZCD still pairs Z3D,
     Z9D and Z10D on icm-demo and stays;
   - kept: S0R (overshoot read), ZCD, F1C, G1C, GS1C, the LBC-<key> twins, the remaining LC* twins, WKS0/WKI0. S0
     stops with S1A/S1B on 2026-11-13 (not in this amendment).
   A retired row keeps its id, K row and registry row; open positions keep every exit.

Every refused candidate is recorded with its reason (`control_thinned`, `thin_controls`, `thin_cell_complete`,
`daily_entries`, `hourly_entries`, `session_entries`). A thinned control's propensity is its p; any other blocked
candidate carries mu 0 and is not an off-policy estimate's sample: the guard against a biased read is that every
blocked candidate still receives its shadow label (`label_sim_r`, the bar replay of the label it would have earned).

## What does not change

- **K is unchanged.** No arm is added, removed or re-registered; no config_hash, rule, timeframe, params or max_open
  changes. The only edits to config/exploration_arms.yaml are the unhashed `retired_day` / `retired_rule` of the
  rows in point 4 (the file header's retirement form). Every arm keeps its id, its row and its count.
- Each read's rule, threshold, window and matched-control method are unchanged (the uncovered-cell share is reported
  beside it). A thinned control reaches its read later; its read is still the first 300 admissible fills.
- Exits: never counted, never gated. The RiskGate is still the only path to an order.

## Bias statement

Admission depends on the same arms' recent activity (their trailing 10 trading days, per session x hour), so the
controls' sample follows when the arms trade; this is mitigated by matched-time thinning (the control is drawn in the
cells where its arms fill, at their rate), and every matched read reports the share of the arm's fills in cells with
no control fill: above 10% the read is "not readable" (`ops/perf_report.py` `_excess`), never a verdict.

**Disclosure.** This thinning was written after the 2026-11-04..06 fills (and their P&L by arm and control) were
looked at. It is acceptable only because it changes volume (how many control fills are taken) and kills (the stops
and retirements of point 4), and never a threshold, a reading rule, a window or a matched-control method.

## Rollback

Drop `daily:` from an account's `exploration` block and restart its engine.
