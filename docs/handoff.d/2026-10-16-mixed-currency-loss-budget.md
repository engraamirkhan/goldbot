- **Mixed-currency accounts: the loss budget reads "can't check", not the account's own cap (BACKLOG 61, UX review
  2026-10-16; display only; ui-ux-designer 2026-10-16: amber text changed, red and approval text approved;
  trading-safety and code review of 4c3d087 pending).** When the accounts reported different currencies,
  `account_view._combined` left out the combined 1.5%/4% caps with a note. The Overview card, the safety-strip chip
  and the approval cards then showed the account's 2% cap, which is the roomier figure. Now:
  - `LossBudget.unknown` carries the owner's reason, for example "The accounts report different currencies (GBP,
    USD), so the combined loss cap can't be checked. Each account's own loss limit still applies; exits always
    run." A new field, `LossBudget.unknown_mixed`,
    names the cause.
  - The budget is amber by default. It is red ("New entries are paused; exits always run.") only while the supervisor
    halts on `mixed_account_currencies`. A leftover engine file in another currency that is over 60 s old does not
    halt the supervisor, so it shows amber.
  - If the supervisor reports `mixed_account_currencies`, the budget reads "can't check" even when the dashboard sees
    only one currency, because the supervisor's combined figures then add pounds to dollars. An account that does not
    report its currency counts as mixed.
  - `/api/proposals` `loss_cant_check` gains `mixed_currencies` (amber) and `mixed_currencies_paused` (red), with
    `loss_left` null. Telegram and the web approval cards read "can't check (the accounts report different
    currencies[; new entries paused])".
  - Entry blocks are unchanged: the account caps stay in `caps`, and the supervisor's halt is still listed. Nothing
    on the order or exit path changed.
  - Until BACKLOG 28's currency lane converts the amounts, this is the end state.

  Tests: `test_mixed_currencies_mean_the_budget_cant_be_checked_in_amber_never_the_roomier_account_cap`,
  `test_mixed_currencies_read_red_when_the_supervisor_halts_entries_on_them`,
  `test_a_supervisor_halting_on_mixed_currencies_is_believed_even_when_this_view_sees_one_currency`,
  `test_a_gbp_and_a_usd_engine_show_the_budget_as_cant_check_never_the_account_cap` (API, amber and red),
  `test_api_proposal_with_a_gbp_and_a_usd_engine_says_cant_check_without_an_amount`, and the BalanceCard,
  SafetyStrip and approval-line tests.
