# Pre-registration amendment 2026-11-26: the demo learning cohort on an account without limits

Amends: docs/research/prereg-amendment-2026-11-24-demo-learning-cohort.md (ADR 0095).
Because of: ADR 0097, demo accounts without limits (owner decision 2026-11-25, "allow absolutely anything").
Filed: before the flip, before any cohort fill on the unlimited epoch. Quant review of PR #454, F4.

From the first entry on icm-probe with `limits_epoch: unlimited`, ADR 0097 lifts the cohort's caps in section 3:
- the per-arm GBP 15 cap;
- the weekly GBP 50 cap;
- one open position per arm;
- 30 fills a day and 4 an hour;
- `cohort_unread`.

What stands: each arm's frozen `max_open` and the end date 2027-01-15.

The end-early rule is evaluated in shadow. Every week the weekly loss is computed as if the GBP 50 cap applied (journal
`exploration_cohort_week_cap_shadow`). The cohort ends early if that shadow cap is reached 3 consecutive weeks.

The 2027-01-15 read is cut by `limits_epoch` and never pooled across the two epochs:
- capped: 2026-11-24 to the flip;
- unlimited: after the flip.

Within the unlimited epoch, fills for which `evidence_exclusions.unlimited_market_state` is true are left out of every
metric cell and counted.

K = 0.
