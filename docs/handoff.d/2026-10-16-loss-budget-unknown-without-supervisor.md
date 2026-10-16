- **Loss budget reads "unknown" when the supervisor's report is missing or rejected (release manager finding,
  2026-10-16; display only; trading-safety review).** With supervisor.json missing, torn, stale, stamped ahead or
  without its caps, `account_view._combined` dropped the combined 1.5%/4% caps. The Overview card and the safety-strip
  chip then showed the engine's 2% daily cap, the roomier figure. `LossBudget.unknown` now carries the reason (for
  example "supervisor report stale: the combined cap can't be checked; new entries are paused"). The card shows
  "Loss budget unknown" in red with that reason and no amount, the chip reads "unknown", and `proposal_budget` returns
  None, so the approval cards on Telegram and the web read "unknown". A cap rebuilt from settings was rejected: one
  account's equity misses the other accounts' losses. Entry blocks are unchanged: the account caps stay in `caps`,
  and the engines already halt on the same report (`Supervisor.engine_should_halt`). Mixed currencies still drop the
  combined caps with a note, as before. Tests:
  `test_a_rejected_supervisor_report_shows_the_budget_as_unknown_never_the_roomier_account_cap` (API, four cases),
  `test_a_stale_missing_or_capless_supervisor_report_makes_the_budget_unknown_with_the_reason`, and the BalanceCard
  and SafetyStrip component tests.
