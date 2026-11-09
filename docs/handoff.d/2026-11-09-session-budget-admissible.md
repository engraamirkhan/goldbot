- 2026-11-09 (branch fix/session-budget-admissible; volume diagnosis 2026-11-09 cause 4, fix 2; review round 1 of
  2277c121; docs/research/prereg-amendment-2026-11-09-session-roll-forward.md, quant sign-off pending): the
  exploration daily budget's session split (`exploration_budget.session_budgets`) counts only admissible edge
  candidates of the trailing 10 trading days: candidates of active arms (retired, entry_refused and unqualified arms
  of every book out) whose decision time passes the static entry refusals (`entry_refusals.STATIC`: window_0207,
  f1_pit, learning_icm_only; the HAR refusals read a day's job row and are not applied to history). The UTC day has
  four segments (asia, london, newyork, late_asia after the New York close), each with a share from its own candidate
  count: one fill each first, the rest by largest remainder, so the four sum to max_per_day. A segment's unspent
  budget rolls forward to the next one that day (`session_entries` once the day's fills reach the budgets' sum
  through the signal's segment). It stops the whole pass only for a signal of the pass's own segment
  (`pass_wide`). A signal closed before UTC midnight is refused as `daily_entries`, that signal alone.
- The split is cached once per (ledger, account, UTC day, active arm set) for every book together (`SHARES`), not
  per book. The duration is logged, with a warning above 1 s. A STATIC refusal that raises excludes its candidates
  and is warned once per read. Aux migration 27 indexes `exploration_obs(account_id, is_control, decision_ns)`.
- PD ruling 5(d): at most `scalp_day_cap` (accounts.yaml `exploration.daily`, default 30) fills a day per account from
  1m/5m arms, refused as `scalp_day_cap` and recorded blocked. `daily_refusal` takes `segment=` (`day_segment`) plus
  `fast=` and `prior_day=`. RiskGate, max_per_day/max_per_hour, loss caps and thin_control_* are unchanged.
- Expected: about +10-15 fills a day on icm-demo/icm-probe from the roll-forward, inside the same day caps, less what
  the 1m/5m cap removes (about 25 5m fills on icm-demo per the vantage-arms note). Tests:
  `tests/test_exploration_daily_budget.py`.
