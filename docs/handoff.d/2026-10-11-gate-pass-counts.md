---
section: next
---
- **Quant review follow-ups to the Q1 fixes (2026-10-11; M46; quant review and trading-safety review pending).**
  **One gate-pass predicate:** `registry.gate_pass_counts(row)` (gates passed AND `results.swap` and
  `results.cost_source` recorded) replaces every hand-rolled read of `results.gates.passed`: `passed_gates`, gap
  watch's research-ready founders, the quarterly CPCV job, `research_pass.py --cpcv` (a pre-swap pass is refused as
  failed) and the director's survivor/holdout slots. **Phase gate input:** `gates_phase.best_backtest_trial` (the
  backtest -> paper evidence: DSR, trade count, positive years) now ignores rows without the cost treatment whatever
  their DSR; with only pre-swap rows the gate shows "no evaluated trial" until a swap-charged walk-forward is run.
  **Holdout rows without cost recording are filtered and cannot be re-scored:** a survivor's holdout pass recorded
  before swap was charged no longer counts for `passed_gates`, and `holdout_scored` still refuses a second scoring of
  that configuration (the holdout is spent), so such a configuration cannot be promoted through a discovery holdout;
  it needs a new pre-registered configuration. **Data pins per row:** `preregister_quarter.py --bars` now pins each
  queued row's data-v1 files from the year of its `bars_from` (every research-window file for a row without one) and
  records `plan.data_years`; the runners still refuse any file read but not pinned or pinned but not read, so H-02
  runs with `--from-year` = its `bars_from` year (the refusal names the row's pinned years). **Owed counts:** H-06's
  `daily_floor` / `below_floor` are now `retire_floor` / `below_retire_floor` (compat: older owed_counts.json use the
  old names), and the report states that 150 is the retire floor and a pass needs `screen_min_events` (1,000).
