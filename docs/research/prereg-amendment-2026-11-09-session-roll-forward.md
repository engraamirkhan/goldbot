# Amendment 2026-11-09: session roll-forward and the 1m/5m day cap under the daily entry budget

Status: **in force with the release that carries it** (branch `fix/session-budget-admissible`; quant conditions
C1-C4 of 2026-11-09, PD ruling 5(d)). Amends rule 1 of prereg-amendment-2026-11-06-control-thinning.md.

## Disclosure

Written after the 2026-11-09 fills were seen (volume diagnosis, cause 4: session budgets stranded in Asia hours that
`window_0207` now refuses and in arms retired on 2026-11-06). Changes volume only, never a threshold, reading rule,
window, matched-control method or K.

## The rule (engine: `goldbot/engine/exploration_budget.py`; accounts.yaml `exploration.daily`)

Rule 1 per-session cap replaced:

- The shares come from admissible edge candidates: candidates of active arms (not retired, `entry_refused` or
  unqualified, in any book of the account) whose decision time passes `entry_refusals.STATIC` (`window_0207`,
  `f1_pit`, `learning_icm_only`). The candidates come from the trailing 10 trading days, as before.
- The UTC day has four segments: early Asia, London, New York, and late Asia (after the New York close). Each takes
  its own share from its own candidate count. Each segment gets one fill first, and the rest of `max_per_day` is split
  by largest remainder, so the four sum to `max_per_day`.
- The cap is cumulative through the signal's day segment. A segment's unspent fills pass to the next one the same
  UTC day. An early segment never spends a later one's share.
- New rule (PD ruling 5(d)): at most `scalp_day_cap` (30) fills a day per account from 1m and 5m arms, by the arm's
  timeframe, every kind together (`scalp_day_cap`, recorded blocked).

`max_per_day`, `max_per_hour` and `thin_control_*` are unchanged.

## Regime break

Fills from the deploy date on are admitted under this rule. A read pooled across sessions notes that date.

## Rollback

Revert the release. `scalp_day_cap: null` in accounts.yaml lifts the 1m/5m cap alone.

Quant sign-off:
