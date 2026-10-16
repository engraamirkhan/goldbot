- Attribution results verdicts follow ADR 0022 (2026-10-16, quant review of the results verdicts). The rule was
  recorded before the code, in its own commit. Changes in `goldbot/research/attribution.py`:
  - **Day-clustered errors.** t, `ci95_net` and the new `ci_adj` use standard errors clustered by entry UTC day
    (CR1, `epoch_ns`). Tripling a day's trades no longer raises t.
  - **Row bar.** Every cell carries `t_bar`, `ci_adj` and `verdict_adj`.
    - Breakdown rows use the Bonferroni bar `t_bar = max(2, Φ⁻¹(1 − 0.025/m))`, m = breakdown cells at the minimum,
      read on the row's days − 1 degrees of freedom. The report carries `t_bar` and `m`.
    - The overall cell, the challengers and the live fills use `max(2.0, t⁻¹_{G−1}(0.975))` (F4).
    - `verdict_adj` is signed if and only if `ci_adj` excludes 0.
    - A clustered se of exactly 0 while the trades disperse is "no se": indistinguishable, never a point range (B1).
    - The raw `verdict` (|t| >= 2) is kept for `read_attribution`.
  - **Telegram digest** (`goldbot/telegram/digest.py`, quant ruling F3): names only rows whose `verdict_adj` is
    signed, else "no row clears the bar (|t| ≥ t_bar over m rows) · overall ...".
  - **Live fills** are compared with the backtest's slippage prior, not with the measured table built from the same
    fills. `LiveFills.mean_table_usd` is now `prior_usd`.
  - **Stale note fixed.** The note on live realised R now points at `ClosedTrade.r` in `closed_trades*.jsonl`.

  **Change to the review's formula, ruled correct by the quant reviewer (2026-10-16).** The review's formula (CR0,
  normal quantile) fails the review's own family-wise test: 0.085 in theory, 0.072 over seeds 0–499, against a bar of
  0.07. CR1 with t on G − 1 degrees of freedom gives 0.049. The gates and the stop rule are unchanged.

  Tests: tests/test_attribution.py
  - ::test_rows_bonferroni_fwer, ::test_rows_bonferroni_fwer_with_a_day_effect, ::test_t_bar_values
  - ::test_zero_clustered_se_with_dispersion_is_not_evidence
  - ::test_overall_bar_is_two_or_the_t_quantile_on_days_minus_one_for_overall_challengers_and_live
  - ::test_cell_stats_duplicated_same_day_trades_do_not_raise_t
  - ::test_adjusted_verdict_is_signed_exactly_when_the_adjusted_interval_excludes_zero
  - ::test_live_fills_are_compared_with_the_backtest_slippage_prior_not_the_table_built_from_them
  - ::test_build_report_notes_do_not_say_live_r_unrecorded

  tests/test_digest.py::test_attribution_line_names_no_best_or_worst_row_when_no_row_clears_the_bar
