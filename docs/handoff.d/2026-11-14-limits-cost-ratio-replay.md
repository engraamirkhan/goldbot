- 2026-11-14 (branch feat/limits-cost-ratio-replay-1114; R150; BACKLOG 255 and 90 done): quant-reviewer approved
  the R150 cost/model definition 2026-11-14. `scripts/replay_limits.py --data-root DIR` now measures each replay day's
  20-day cost/model ratio through the nightly job's own `_cost_ratio`, `store_exit_legs` and `store_label_bars` (no
  definition parameter can be passed in), so replayed limits include the cost rule; without a data root the header
  says the rule is inert (None). Known differences from the night itself, stated in the module docstring: the store's
  bars are today's (a fill the job held `pending` is measured) and the USD factor is the engine file's current one.
  The quant's non-blocking suggestion is in: `CostRatio.ratio_bound` prices each unmeasured fill at the window's
  worst measured fill against the smallest model charge; logged, in the job row (`cost_ratio_bound`) and the replay
  table, never an input to the limits. No replay was run (ADR 0031's replay log unchanged); nothing on the order path
  changed.
