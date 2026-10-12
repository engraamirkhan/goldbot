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
  Gate 1 also requires the new item `research_gates` (that trial's own `gate_pass_counts`; the evidence names any
  failed checks), and `paper_expectancy` / `live_drawdown` name their comparator trial and have no backtest
  reference (not met) unless it passed the gates (review of 978c667). Tying the comparator to the deployed model's
  own trial is BACKLOG 53. The research view shows a recorded pass without its cost treatment as
  `pre-swap: not counted` (`TrialRow.pre_swap`; `gates_passed` is false for it; API contract regenerated).
  **Holdout rows without cost recording are filtered and cannot be re-scored:** a survivor's holdout pass recorded
  before swap was charged no longer counts for `passed_gates`, and `holdout_scored` still refuses a second scoring of
  that configuration (the holdout is spent), so such a configuration cannot be promoted through a discovery holdout;
  it needs a new pre-registered configuration. **Data pins per row:** `preregister_quarter.py --bars` now pins each
  queued row's data-v1 files from the year of its `bars_from` (every research-window file for a row without one) and
  records `plan.data_years`; the runners still refuse any file read but not pinned or pinned but not read, so H-02
  runs with `--from-year` = its `bars_from` year (the refusal names the row's pinned years). No Q1 row has been
  written yet (checked 2026-10-12: research-v1's registry.jsonl has 20 rows, none for 2027Q1, none preregistered;
  the spec still has `freeze_commit: null` and H-02/H-06 pending, so the writer refuses). **Any queued row written
  before per-row pinning must be withdrawn (`TrialRegistry.withdraw`) and rewritten before the quarter starts**:
  its plan pins every file from data-v1's first year and has no `data_years`. **Owed counts:** H-06's
  `daily_floor` / `below_floor` are now `retire_floor` / `below_retire_floor` (compat: older owed_counts.json use the
  old names), and the report states that 150 is the retire floor and a pass needs `screen_min_events` (1,000).
