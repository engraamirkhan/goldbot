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

## The rule (engine: `goldbot/engine/exploration.py` `daily_refusal`; accounts.yaml `exploration.daily`)

Per account, counted as fills since the engine's day reset, every book together:

1. At most `max_per_day` exploration fills (icm-probe 85, icm-demo 70, vantage-demo 40: 195, plus the cost probes'
   55-a-day plan = 250) and at most `max_per_hour` in one clock hour, so no session spends the day.
2. A random-control fill only while control fills + 1 <= `controls_per_edge` (1.0) x strategy/learning fills: at most
   one control fill per edge or learning fill. A control does not fill before its account's first edge fill of the day.
3. Thinned first: an **unpaired per-instrument control** (a control on a non-gold instrument whose row names no
   learning arm: S0-<key>, I0-<key>) fills only while its fills + 1 <= `thin_control_share` (0.25) x that control
   allowance. Gold's controls (S0, S0R, I0, W0, VI0, ZCD, ZCV, ...) and the controls dedicated to learning arms (`params.control`
   "learning:<ids>": LC*, LBC-<key>) take the rest, so the twins that pair active
   learning arms pair first.

4. **Control stops (PD+PO decision 2026-11-06; performance-analyst kill list 2026-11-06: the unpaired controls were
   the biggest drain, about -£58 a day).** A control measures the null of the arms it pairs; one that pairs no active
   arm stops entering:
   - the unpaired per-instrument controls (the 48 S0-<key> / I0-<key> rows; no active arm on a non-gold symbol runs
     their geometry, the LB-<key> arms have their dedicated LBC-<key>) stop for good once each has
     `thin_control_fills` (30) fills on its account: the per-instrument cost cell is their only consumer and needs
     n >= 30 (`thin_cell_complete`). Ten had their 30 on 2026-11-06 (XTIUSD, USDCHF, EURJPY, EURUSD, EURGBP, GBPJPY,
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

A refused control candidate is recorded blocked (`control_ratio`, `thin_controls`, `thin_cell_complete`,
`daily_entries`, `hourly_entries`) with propensity 0, as every other cap (research E6: off-policy estimates stay unbiased).

## What does not change

- **K is unchanged.** No arm is added, removed or re-registered; no config_hash, rule, timeframe, params or max_open
  changes. The only edits to config/exploration_arms.yaml are the unhashed `retired_day` / `retired_rule` of the
  rows in point 4 (the file header's retirement form). Every arm keeps its id, its row and its count.
- Each read's rule, threshold, window and matched-control method are unchanged. A thinned control reaches its read
  later; its read is still the first 300 admissible fills.
- Exits: never counted, never gated. The RiskGate is still the only path to an order.

## Bias statement

The thinning acts on control **volume** only and by the clock order of signals, never on any outcome: a control's
fills are a random subsample of its own random entries (the budget does not look at P&L or side). The matched read
(control's mean R on the same risk day x session x side cell) keeps its meaning; fewer control fills widen its
interval. Session balance: the hourly bound keeps a busy first hour from spending the day, so the controls sample
the sessions their arms trade.

## Rollback

Drop `daily:` from an account's `exploration` block and restart its engine.
