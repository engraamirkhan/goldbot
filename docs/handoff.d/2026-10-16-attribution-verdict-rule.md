- Attribution results verdicts follow ADR 0022 (2026-10-16, quant review of the results verdicts). The rule was
  recorded before the code, in its own commit. Changes in `goldbot/research/attribution.py`:
  - **Day-clustered errors.** t, `ci95_net` and the new `ci_adj` use standard errors clustered by entry UTC day
    (CR1, `epoch_ns`). Tripling a day's trades no longer raises t.
  - **Row bar.** Every cell carries `t_bar`, `ci_adj` and `verdict_adj`.
    - Breakdown rows use the Bonferroni bar `t_bar = max(2, Φ⁻¹(1 − 0.025/m))`, m = breakdown cells at the minimum,
      read on the row's days − 1 degrees of freedom. The report carries `t_bar` and `m`.
    - The overall cell, the challengers and the live fills keep 2.0.
    - `verdict_adj` is signed if and only if `ci_adj` excludes 0.
    - The raw `verdict` (|t| >= 2) is kept for `read_attribution`.
  - **Live fills** are compared with the backtest's slippage prior, not with the measured table built from the same
    fills. `LiveFills.mean_table_usd` is now `prior_usd`.
  - **Stale note fixed.** The note on live realised R now points at `ClosedTrade.r` in `closed_trades*.jsonl`.

  **Deviation from the review's formula, with evidence (needs quant re-review).** The review's formula (CR0, normal
  quantile) fails the review's own family-wise test: 0.085 in theory, 0.072 over seeds 0–499, against a bar of 0.07.
  CR1 with t on G − 1 degrees of freedom gives 0.049. The gates and the stop rule are unchanged.

  Tests: tests/test_attribution.py
  - ::test_rows_bonferroni_fwer, ::test_t_bar_values
  - ::test_cell_stats_duplicated_same_day_trades_do_not_raise_t
  - ::test_adjusted_verdict_is_signed_exactly_when_the_adjusted_interval_excludes_zero
  - ::test_live_fills_are_compared_with_the_backtest_slippage_prior_not_the_table_built_from_them
  - ::test_build_report_notes_do_not_say_live_r_unrecorded
