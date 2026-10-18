# Continuous research loop (plan, 2026-10-18)

Owner directive 2026-10-18: run indefinitely, generate strategies, backtest them on several timeframes in parallel,
keep what works, and add each proven strategy to the live system. Sources: DESIGN "Bounded research loop" (line 98),
"Validation" (90), "A competing population" (196), Risks (202, 208); TRACEABILITY M19 (in review), M30, G6, G11, G13.

**The main constraint.** DESIGN line 98 says: "This is bounded feature and parameter search, not discovery of novel
strategies", and line 202 calls an unbounded search "the largest risk". So "forever" here means a loop that keeps
running on a schedule. It does not mean an unlimited number of trials (K). Throughput is capped by
`research.trial_budget_quarter` (20, goldbot/config.py:343), which is an owner decision. With 20 trials a quarter, 10-20
parallel cells use up a quarter's budget in one run. Parallelism shortens how long a batch takes. It does not raise
how many trials we can run.

## 1. The loop

**Generator (no LLM).** New module `goldbot/research/generator.py`. It builds a deterministic grid of
family x timeframe (the family's own `timeframe` plus `timeframes`, e.g. goldbot/specialists/time_series_momentum.py:120-121)
x numeric exit and label settings, each within +-25% (DESIGN 98). Every candidate must pass
`population.check_founder_config` (goldbot/research/population.py:227), so any survivor can later be spawned. Candidates
are ranked by `ResearchPlan` family shares (director.py:1432 `build_plan`). They then go through cheap pre-filters that
never look at returns, so they cost no trial:
- skip a config hash already in the registry;
- skip screening families (jobs.py:648) and retired families;
- drop a candidate when 0.01 lot would risk more than 1.2 x the target risk at GBP 1,000. This uses
  `goldbot/research/min_lot.py` on ATR only.

For scale: at ATR15 of $8 (docs/research/atr-measured-2026-10-18.md), a 1.5 ATR stop costs $12 against about $6.6 of
target risk. Today most 1h/4h/1d grids are infeasible, so the filter spends trials only where a trade could actually be
placed. Feature discovery (`--discover`) stays a pre-registered, single trial charged at K_eff
(registry.py:151). Session filters and regime gates need new specialist keys, so they wait for phase 3. When the owner
runs a session, Claude agents may add hypotheses by pre-registering them through the same budget.

**Scheduler.** New `.github/workflows/research-loop.yml`, running monthly on the first Saturday at 09:00 UTC. That is
after `data-dukascopy.yml` (Sat 03:17) and before the brain's Saturday jobs (config/settings.yaml:139-145). It runs the
generator and dispatches `research.yml` with a `batch` of at most `budget - used - reserved`
(`director.reserved_trials`). The batch matrix comes from the running lane (`goldbot/research/cells.py`, `MAX_CELLS` 64).
- **Minutes:** the repo is public (verified with `gh repo view`). Standard GitHub-hosted runners are free with no
  minute limit for public repos.
- **Limits:** 20 concurrent jobs on the Free plan, 6 h per job, 256 matrix jobs per workflow.
- **Parallelism:** set `max-parallel: 16`.
- **Keep-alive:** GitHub disables scheduled workflows in a public repo after 60 days without repository activity.

**Selection (pass rule).** A candidate passes when all of these hold:
- `gates.research_gates` (gates.py:45): 1,500 candidates, 60 per fold, 3 positive years including 2021-22, at least 200
  trades, DSR >= 0.95. The DSR is restated on the final merged K by `cells.restate_dsr`.
- It is net of the measured `costs-v1` table, swap included (`registry.costs_recorded`).
- It is the latest rerun (`latest_evaluated`).
- It survives a cost stress at 1.5 x spread and slippage. This is new, reported in the same trial and not a new trial.
- Sizing is feasible as above.
- Loop configurations also need a passed holdout scoring, as discovery survivors already do (registry.py:682-704).
  Holdout scorings are charged as trials and spent once per configuration (`holdout_scored`).

**Hand-off.** This already exists. The survivor's artifact is its registry row on release research-v1. The brain
unions it in (`_sync_trials`, jobs.py:847). `gap_watch.research_ready` (goldbot/ops/gap_watch.py:228) then spawns a
zero-capital shadow founder through `Population.spawn_founder` (population.py:290), capped at 2 a month and 4 reserved
slots. `saturday_retrain` trains it as a challenger (jobs.py:373, 15m/1h/4h only). Promotion follows:
- `evaluate_promotion` (promotion.py:55): 4 weeks and 40 trades;
- the tournament: DSR at population size plus `passed_gates` (jobs.py:628);
- live entries then still go through RiskGate and the owner's 90 s confirmation.

1d stays research-only (settings.yaml:41).

**Retirement.** No change: `model_watch` CUSUM rollback (jobs.py:411), `drift_watch` (566), and tournament retirement
(lower 80% bound < 0 after 100 trades, or bottom quarter for two months) with six months of shadow tail.

## 2. The K problem
Options:
- (a) global DSR at running K, which is today's rule;
- (b) alpha-investing or LORD online FDR;
- (c) per-family Bonferroni budgets.

**Recommended: (a) with two rules.**
1. *K is frozen at decision time.* A promotion is judged at the K of its decision. After that the trade is protected by
   live and shadow evidence (CUSUM, tournament), not by restating its DSR again later. Reading taken: DESIGN asks for the
   "real trial count" when deciding. Later decay of a past survivor is model_watch's job.
2. *The quarterly budget is the spending schedule.* The director's per-family shares (G13) already work as per-family
   budgets.

Option (b) controls FDR rather than the design's DSR bar, so adopting it would change a gate, which only the owner can
do. Option (c) throws away cross-family information.

On power (illustrative: independent trials, SR standard deviation 1/sqrt(n)), the per-trade Sharpe needed for DSR 0.95
at 300 trades is:

| K | Per-trade Sharpe needed |
| --- | --- |
| 20 | 0.205 |
| 100 | 0.241 |
| 1,000 | 0.283 |
| 5,000 | 0.308 |

At 1,000 trades the same range is 0.112 to 0.169. K grows the bar only logarithmically. Trade count matters more, so
the loop should favour configurations with many trades. **Owner decision:** whether to raise `trial_budget_quarter`.
Going from 20 to 60 raises the bar by about 13% at n=300.

## 3. Files, tests, phases
**Phase 1 (one day; no overlap with lane ae61, which owns `research.yml`, `cells.py` and `gates.py`).**
- Add: `goldbot/research/generator.py`, with `generate(rows, settings, plan, atr) -> list[Candidate]` and
  `batch(cands, remaining) -> list[dict]`.
- Add: `scripts/research_queue.py`, which prints the batch JSON.
- Docs: TRACEABILITY row R-loop and a HANDOFF fragment.

Tests to write first, all in `tests/test_research_generator.py`:
- `test_grid_is_deterministic_for_the_same_registry_and_plan`
- `test_every_candidate_passes_check_founder_config`
- `test_already_tried_config_hashes_are_skipped`
- `test_batch_is_empty_at_zero_remaining_and_exactly_full_at_remaining`
- `test_screening_and_retired_families_are_excluded`
- `test_min_lot_risk_at_exactly_1_2x_target_is_kept_and_just_above_is_dropped`
- `test_no_candidate_window_reaches_the_holdout`
- `test_unreadable_registry_yields_no_batch`

**Phase 2 (after lane ae61 merges).**
- Add `research-loop.yml`.
- In `cells.py`, check the budget against the snapshot minus in-flight cells.
- Add a loop marker key in the configuration so `passed_gates` requires a holdout pass.
- Tests:
  - `tests/test_research_cells.py::test_batch_cannot_overshoot_the_quarter_budget`
  - `tests/test_registry.py::test_loop_config_needs_a_passed_holdout`

**Phase 3 (brain).**
- Add a cost-stress check in `scripts/research_pass.py`.
- Add a `research_loop_health` brain job that alerts when the workflow is disabled or no batch has run for 35 days.
- Add optional `session_filter` and `regime_gate` keys, absent by default so no pinned config hash moves.
- Update the RUNBOOK if a VPS step changes.

**Non-negotiables.**
- RiskGate and owner entry confirmation are unchanged. The loop only creates shadow agents.
- Exits are untouched.
- Every trial stays within the 40-feature cap of `pipeline.py`.
- Joins stay as they are; no new bar joins.
- `epoch_ns` is used in the generator's window checks.
- Accounts stay demo. No secrets: the workflow uses `github.token` only.

## 4. Risks (by impact)
1. **Budget overshoot inside one batch.** cells.py says cells "can overshoot it by up to the batch size". Fix: phase 2.
2. **The holdout year wears out.** Every survivor scores the same 2025-10 to 2026-09 window. **Owner decision:**
   a Bonferroni holdout t-bar, or rolling the window forward each year.
3. **Sizing infeasibility.** At GBP 1,000 almost nothing above 15m can trade. The loop will find edges the account
   cannot take.
4. **The schedule silently dies** after 60 days of inactivity. Fix: phase 3 health job.
5. **Shadow slots are scarce.** There are 2 founders a month and 4 reserved slots, so survivors queue.
   `gaps.founders_per_month` can be raised; that is a routine product-owner and program-director decision.
6. **M19 is still "in review".** DSR is not yet a gate on challengers, so the loop must not merge before M19 closes.

## Owner overrides, 2026-10-18 (these win over the text above)
The owner said: "it should be able to backtest as much as it needs now in parallel for strategies" and "back test
strategies on multiple time frames in parallel, finding new strategies, back testing, keeping the ones that work and
continue doing it indefinitely and adding to system as a strategy is proven to be profitable". So:
- The loop is not limited by the quarterly 20-trial budget. `research.loop_trials_per_run` (default 16) is a separate,
  explicit per-batch budget; `trial_budget_quarter` is unchanged. Every trial is still a registry row and the deflated
  Sharpe uses the full running K.
- Cadence every 6 hours (not monthly), up to 16 parallel jobs. The held-out year stays untouched by the search.
- Open item for phase 2 (lane ae61 owns `research.yml`, `cells.py`): `scripts/research_pass.py` still refuses runs beyond
  `trial_budget_quarter` (TrialRegistry.check_budget), so a loop batch needs a loop-budget path there before the
  schedule can run. Phase 1 (generator, queue script, settings) does not touch it.
