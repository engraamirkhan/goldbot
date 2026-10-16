- Round-trip costs in the daily £ projection (2026-10-16, BACKLOG 36, trading-safety review LOW-6). Accounts with
  an absolute daily loss limit (icm-demo, £50) now refuse an entry when loss so far (+ open risk) + stop risk + round
  trip > the limit. Before, the round trip was left out. The round trip has two parts, both x lots x contract x fx in
  the account currency:
  - spread, slippage and commission: `cost_atr` x ATR, the engine's `_cost_atr` (the nightly cost table, else
    `cost_atr: 0.10`);
  - the paid swap, when a broker rollover falls before 00:00 UTC.

  The swap reuses ADR 0017's rule. `cost_imputation.account_swap` takes the table's measured rates, else the settings
  prior. `paid_swap_per_oz` counts the triple day and never counts a credit. The closed-trade charge now calls the same
  two helpers. The projection can only grow: a negative cost or a credit counts as 0, and a NaN swap refuses
  (`invalid:swap_usd_per_oz`). The approval re-check prices the swap again at the click. No other check reads the
  cost, and exits are untouched. Worked example at £5,000 GBP (fx 0.75, ATR $10, 0.01 lot): stop £7.50, round trip
  £0.30, one night of the -$60 prior £0.45 (£1.35 on Wednesday). With £42 lost, the projection was £49.50 (allowed)
  and is now £50.25 (refused). New `GateDecision.cost_amount` and `Intent.swap_usd_per_oz` (in-memory only, nothing
  persisted). Tests: tests/test_projection_costs.py; three older boundary pins moved by exactly the cost.

  Trading-safety review fixes (one MEDIUM, four LOW):
  - A NaN swap rate no longer reads as "pays nothing". `SwapSpec` and the cost table's swap fields are finite-only,
    so a NaN table fails to load and the prior is charged. A NaN broker reading becomes "no swap" in the nightly job.
    If a non-finite rate still reaches the engine, `_swap_usd_per_oz` returns NaN and the gate refuses.
  - A swap that cannot be priced at the click refuses the approval (`swap_unavailable`).
  - The spread is counted twice on purpose: `cost_atr` includes it, and so does the stop risk measured from the fill.
    This is conservative and recorded in the gate comment and R42.
  - Quant, code and trading-safety reviews done.
