# 0031: Adaptive risk limits

Status: proposed (implementer, 2026-10-18; rules frozen before any replay; needs trading-safety, quant and CFO
review). Lane A: the controller (`goldbot/risk/adaptive.py`) and its tables; lane B wires it into the runner.

## Context
The owner wants the account limits to be "learnt over time" instead of fixed. Owner-approved DEMO ceilings: account
daily 5%, weekly 10%, `min_lot_risk_cap` 1%; supervisor all-demo ceilings 3.75% / 7.5% (CFO). Floors are today's
defaults (account 2% / 5%, supervisor 1.5% / 4%, minimum-lot exception off). Live and tiny-live ceilings are the
defaults: on a live account the limits may only tighten.

A controller that sets risk limits from its own results can talk itself into risk. The rules below were therefore
pre-registered (frozen) before any replay was run. A replay validates them; it never tunes them. A change to any rule
is a new ADR.

## Decision
The frozen rules, verbatim:

- Evidence: only trades AFTER each strategy's freeze/go-live date (never backtest/OOF); retired strategies' trades stay
  in; cut off strictly before the decision day; epoch_ns times; source ids kept.
- Loosening evidence = demo fills ONLY (net R incl. real costs). Shadow (lot-fit) trades may only TIGHTEN (CFO).
- Statistic on DAILY R sums (not per trade): per strategy, d = daily net-R sums, n_days, mean m, sd s; LCB = m −
  t_{0.80, n_days−1}·s/√n_days (single shrinkage; no extra n/(n+100) factor). Combine across strategies by the MINIMUM
  per-strategy LCB among strategies with evidence (conservative).
- Quarter-Kelly in R: f = LCB/(4·E[r²]) per trade (E[r²] from per-trade demo net R); daily risk fraction = f ·
  trades_per_day (measured) · risk_per_trade-equivalent; q = clamp(daily_fraction / 3%, 0, 1). Daily target = 2% +
  q·3% (0.5% steps); weekly = 5% + q·5% (1% steps); supervisor target scales the same between its floor and ceiling.
- Min-lot cap: off unless demo trades n ≥ 60 and LCB of exception trades > 0; then steps 0.6% → 0.8% → 1.0%; forced
  off when 20-day median ATR15 ≥ 1-year p90.
- Loosen only if: demo n ≥ 60 total and ≥ 20 per contributing strategy; target above current by ≥ ½ step on 3
  consecutive risk-days; ≥ 10 new demo trades since the last loosen; ≤ 1 step/day; no loosening within 5 days of any
  tighten.
- Tighten immediately (may skip steps, may happen intraday; uses demo+shadow at full weight): DD ≥ 4% from HWM →
  floors; 4 consecutive losses with r ≤ −0.8 → floors today+tomorrow; 20-day measured cost/model > 1.25 → floors,
  1.10–1.25 → one step down; stage SIZE_DOWN/HALTED → floors until cleared.
- Bounds: clamp to [floor, ceiling]; live/tiny-live ceiling = default. Fail closed: missing/corrupt evidence or
  unreadable table → floors + dq_event.
- Loosening applies only at the risk-day roll; intraday only tightening.

### Readings made at implementation (before any replay; each is the conservative side)
1. **Units in the Kelly step.** The LCB is in R per day; `f` is per trade, so the LCB enters `f` as its per-trade
   equivalent, LCB / trades_per_day. Then daily_fraction = f · trades_per_day = LCB / (4·E[r²]), a fraction of equity
   (the "risk_per_trade-equivalent": 1R = f of equity). Read literally (the daily LCB inside a per-trade `f`, then
   times trades per day) the trades would be counted twice and the target overstated by trades_per_day.
2. **A trade's day** is the UTC risk day of its exit (`exit_ns`, epoch ns); the statistic keeps exits strictly before
   00:00 UTC of the decision day. "With evidence" means at least 2 trading days (a standard deviation exists).
3. **Contributing strategy** = any strategy with a demo trade in the evidence. If one has fewer than 20 demo trades or
   fewer than 2 days, no field loosens.
4. **Shadow may only tighten.** The tighten target uses q = min(q_demo, q_demo+shadow); the loosen target uses the
   same minimum, so shadow evidence can hold a loosen back but never cause one. Below the demo minimum (n < 60 or a
   strategy < 20) q_demo is 0: the target is the floors.
5. **The statistic tightens too.** At any evaluation, a field whose quantised-down target level is below its current
   level drops to it at once (may skip steps).
6. **Steps and ladders.** Each field moves on its own ladder: account daily floor→ceiling in 0.5% steps, weekly in 1%
   steps; supervisor daily and weekly in the same number of steps as the account demo ladders (6 and 5) between their
   own floor and ceiling ("scales the same"). The minimum-lot ladder is off → 0.6% → 0.8% → 1.0%, cut at its ceiling.
   A ladder whose ceiling is not above its floor (live) has one rung: the default.
7. **Hysteresis.** A risk-day counts towards the 3 when the continuous target is at least the current value plus half
   a step. The count is per field, of consecutive evaluations at the risk-day roll; it resets when a day falls short,
   after the field loosens (each step needs three fresh days) and on any tighten. "≥ 10 new demo trades since the
   last loosen" and "≤ 1 step/day" are per field. For the minimum-lot field the target is its top rung while its
   conditions hold, and off otherwise.
8. **Cooldown.** Any tighten starts the 5-day block for every field: a field lowered for any reason (statistic,
   ATR, bounds), or a trigger that fires (DD, loss streak, cost, stage, invalid evidence) even with the limits already
   at the floors. A field may loosen again on the 6th risk day after it. The 3-day count keeps running in the block.
9. **Cost.** cost/model above 1.25 → floors at any evaluation; 1.10 to 1.25 inclusive → one step down, applied once
   per risk day (at the roll; the 20-day measure is daily). No measured cost in the window (None) triggers nothing.
10. **Loss streak** = the last 4 exits (demo and shadow, all strategies, by exit time, up to the evaluation time)
    all at r ≤ −0.8: floors for the decision day and the next.
11. **ATR.** A missing 20-day median or 1-year p90 forces the minimum-lot exception off (fail closed).
12. **Fail closed.** Evidence that is missing, does not validate, has a non-finite R, a trade of a strategy without
    a freeze date, a duplicate id, an exit after the evidence's as-of time, or a decision day earlier than the last
    one → floors, reason `evidence_invalid`, and a dq_event. A state row or table that cannot be read, or a change
    that cannot be written → floors and a dq_event.
13. **Bounds can only tighten a stored value.** A stored value is mapped to the highest rung at or below it, so a
    changed ceiling (or a demo account turned live) never raises a limit.

### Persistence
core.db migration 4 (additive, after ADR 0032's migration 3): `adaptive_state` (one row per account: values and the
hysteresis counters) and `adaptive_changes` (append-only: account, risk day, field, old, new, reason codes, evidence ids, epoch_ns). No JSON
state file.

## Consequences
- Limits rise only on demo evidence after a strategy's freeze, slowly (3 days, 10 new trades, one step), and fall at
  once. Live accounts never rise above the defaults.
- Risk flagged for the owner: the minimum-lot floor is "off". The £1,000 demo needs the exception to trade 15m at
  0.01 lot, and the exception unlocks only on exception-trade evidence. Wired as specified, the demo would stop taking
  minimum-lot trades and never collect that evidence (a deadlock). Lane B must decide, with the owner, whether the
  minimum-lot floor on that demo is today's server setting.
- The supervisor fields are computed with the same q as the account; a fleet with any live account uses default
  supervisor ceilings.
- Any change to a frozen rule, a ceiling or a floor is an owner decision and a new ADR.
