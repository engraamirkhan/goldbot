---
number: 53
title: The phase gates compare paper and live with the deployed model's own trial
stage: Roadmap gates (P7) · VERIFY
size: M
status: 'parked: matters only once a model is deployed and promoted; no champion exists'
---
- **53. The phase gates compare paper and live with the deployed model's own trial (review of 978c667,
  2026-10-12).** Value: `gates_phase.evaluate_gates` takes its backtest reference for `paper_expectancy`
  (paper -> tiny live) and `live_drawdown` (tiny live -> full size) from `best_backtest_trial`, the registry's
  highest-DSR evaluated row with costs recorded. Since M46 the evidence names that trial and refuses it unless it
  passed the research gates with costs recorded (`registry.gate_pass_counts`). But it is still the best trial in
  the registry, not the trial of the model actually trading: a later, better trial of another family or
  configuration would replace the reference without anyone choosing it. Also, the DSR shown on gate 1 is the one
  stored with that trial, so it was deflated by the trial count at the time the trial ran, not by today's count
  ("registry now holds N trials" sits beside it but did not deflate it). Do: record the trial number (row_ref)
  of the trial a model was promoted from in the model registry. The gates then take paper's and live's
  reference from the deployed champion's own trial, and show "no deployed model" when there is none. Gate 1
  either re-deflates the shown DSR with the current trial count or labels it "DSR at N trials (when run)".
  Acceptance: a test that a newer, better trial of another configuration does not change the paper or live
  reference; a test that the reference is the champion's promotion trial and that its gate pass is required; a
  test that gate 1 shows which trial count the DSR was deflated by; trading-safety and quant review (phase-gate
  inputs).
  Sweep 2026-10-16 (product owner): Parked: matters only once a model is deployed and promoted; no champion exists.
