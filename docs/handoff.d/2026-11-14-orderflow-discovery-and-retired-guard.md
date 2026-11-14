- 2026-11-14 (branch feat/orderflow-governance-1114; M50, M51; BACKLOG 211 order-flow part, 91): `research_pass.py
  --discover --orderflow` screens the R139 order-flow proxies (built from the run's own 1m bars, cut at the holdout)
  beside the default features in one discovery trial. The pre-registered plan and the report carry
  `orderflow.SCREEN_NOTE` (a survivor must beat the plain return and momentum features in the same screen) and, at
  15m, the H-14 note (expected move >= 3x round trip); the result row's `orderflow_rule` reads it (strictly higher
  stability than every `ret_*` / momentum column, ties fail). Without the flag nothing changes and no hash moves.
  `--orderflow` is refused outside `--discover`. News stays collect-only: no `--news` flag until 12 months of data.
- Retired families now share their one exploration trial a quarter in code. `TrialRegistry.check_budget_reserved`
  refuses an open-budget run (rule_ref or not) on any family in research.retired_families once the quarter has a
  retired-family trial (`RetiredExplorationUsed`, with the counts in the message); only a queued pre-registration of
  the exact config lifts it. The director counts screened retired families too (`box_break_close`, `sweep_absorb`) and
  reports the trial used (`retired_trials_quarter`). H-53's procedural hold in prereg-2026-10-19-h50-followup.md s.3
  can drop once quant signs off. Behaviour change: `research_pass.py` defaults to `--specialist session_open`
  (retired R-02), so a second open-budget session_open run in a quarter is now refused.
