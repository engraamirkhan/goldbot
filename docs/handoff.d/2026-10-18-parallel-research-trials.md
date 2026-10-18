- Parallel research trials, serialized registry record (`research.yml`, `goldbot/research/cells.py`). The workflow is now
  `plan` -> `trials` (matrix, `max-parallel: 10`, `contents: read` only) -> `record` (`concurrency: research-registry`,
  the only job that writes the registry or an issue). Inputs: the existing single-trial ones (one cell per variant, so
  `variants` now runs in parallel) and a new `batch` (JSON list of `{specialist, variants, pooled, timeframe, ...}`, a
  missing key takes the dispatch input; max 64 cells). Each cell fetches bars and a read-only registry snapshot, runs
  `research_pass.py` on a copy of it and uploads only the rows it added. `record` fetches the fresh registry under the
  lock, merges the rows (registry_sync's keyed union, idempotent on a re-run), restates every deflated Sharpe and the
  gate's DSR verdict on the FINAL `n_trials_effective` (`cells.restate_dsr`; the run's own value stays in
  `results.dsr_at_run`), saves by the usual compare-and-swap and posts one comment per `research: <family>` issue.
  - A cell's own report is provisional: its DSR and trial numbers use the snapshot count, not its siblings; the posted
    report says so and shows `at run -> restated on K`.
  - A cell that crashed, timed out or left no artifact records a `failed` trial row (counted in N, never passes a gate).
    A cell refused before any look (budget, queue, holdout rule; a message, no traceback) records nothing and is listed.
  - Known limit: each cell checks the quarter's budget on its snapshot, so a batch can overshoot it by up to its size;
    the report states the quarter's count after the record. Size a batch to the remaining budget.
