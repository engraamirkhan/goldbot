- **Loss budget reads "can't check" when the supervisor's report is missing or rejected (release manager finding,
  2026-10-16; display only; trading-safety and UX review).** With supervisor.json missing, torn, stale (no `ts`
  counts as stale, as in `engine_should_halt`), dated ahead or without its caps, `account_view._combined` dropped the
  combined 1.5%/4% caps. The Overview card and the safety-strip chip then showed the engine's 2% daily cap, the
  roomier figure. Now:
  - `LossBudget.unknown` carries the owner's reason (for example "The safety supervisor hasn't reported for over a
    minute, so the combined loss cap can't be checked. New entries are paused; exits always run.").
  - `LossBudget.unknown_paused` says whether the engines pause new entries on the same report. When they do, the
    card ("Loss budget: can't check", the reason, no amount) and the chip ("can't check") are red. They are amber
    when the supervisor hasn't added up the accounts yet.
  - The approval cards read "loss budget left today: can't check (supervisor not reporting; new entries paused)" on
    Telegram, and the same in red on the web. `/api/proposals` gains `loss_cant_check` ("paused" / "not_summed"),
    with `loss_left` null.
  - A cap rebuilt from settings was rejected: one account's equity misses the other accounts' losses.
  - Entry blocks are unchanged: the account caps stay in `caps`, and the engines already halt on the report.
  - Mixed currencies still show the account cap (BACKLOG 61).

  Tests: `test_a_rejected_supervisor_report_shows_the_budget_as_unknown_never_the_roomier_account_cap` (API, five
  cases), `test_api_proposal_says_the_budget_cant_be_checked_without_an_amount`,
  `test_a_rejected_or_capless_supervisor_report_means_the_budget_cant_be_checked_with_the_reason`, and the
  BalanceCard, SafetyStrip and approval-line component tests.
