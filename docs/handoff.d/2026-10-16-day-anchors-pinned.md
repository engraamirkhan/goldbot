- **Day anchors pinned (2026-10-16; BACKLOG 57, TRACEABILITY R40; trading-safety review pending).** No code reads
  `risk_day_anchor` or `feature_day_anchor`; the loss caps reset at 00:00 UTC (`timeutil.risk_day` and the four other
  places listed under "Finding: risk_day_anchor" below) and daily bars close at 13:30 New York (`timeutil.feature_day`).
  The settings are now pinned, not wired: `Settings._day_anchor_is_the_fixed_day` (goldbot/config.py) refuses any
  `risk_day_anchor` other than UTC 00:00 and any `feature_day_anchor` other than America/New_York 13:30 at load, with
  an error that names the fixed day and says to delete the key from `config/settings.local.yaml` (exact text in
  docs/RUNBOOK.md, "Settings refuse to load: a day anchor"). The comparison is exact, so `Etc/UTC` is refused too.
  - **Not changed.** When any cap resets: `tests/test_config.py::test_the_risk_day_and_so_the_loss_cap_reset_is_pinned_at_00_utc`
    pins `new_day` (no reset at 23:59:59.999 UTC, a reset at 00:00). The shipped yaml loads unchanged. Rollback-safe:
    an older release accepts any anchor and ignores it, as before.
  - **Deploy precondition (servers).** A server whose `config/settings.local.yaml` sets either key would stop loading
    its settings after this deploy. No server exists yet, so the precondition holds trivially for the first deploy.
    Any server set up later, before it takes a release with this change, must be checked:
    `grep -nE 'risk_day_anchor|feature_day_anchor' config/settings.local.yaml` must print nothing.
  - **Still an owner decision.** Moving the risk day itself (for example to the 22:00 UTC roll) needs one shared
    risk-day function for all five places, a migration for the persisted reset time and a trading-safety review.
