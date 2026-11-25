- **Learning liveness judged by output (S5, plan 2026-11-25 W1-C; R181).** On 11-25, 7 learning tables held 0 rows,
  drift_watch and model_watch watched 0 models, and 0 of 22,010 decisions carried a model. Health still said ok.
  - **`learning:<name>`** (goldbot/ops/checks/learning.py) is one check per learner: self_retrain,
    exploration_weights, har_days, arm_lifecycle_verdicts and cost_cells.
    - A learner that is on but has 0 output rows, or nothing new within its cadence, WARNs. It FAILs 48 h after
      the grace start, and the existing alert loop sends the FAIL to Telegram.
    - The grace starts at the first deploy at or after 2026-11-26 22:00 UTC (from state/deploys.jsonl).
    - A learner switched off in settings WARNs with its flag but never FAILs.
    - Fill-driven learners have a 72 h cadence, so a weekend is not a gap.
    - Thompson weights under `allocator: equal` read ok, with the allocator named.
  - **RETIRED** (read ok, with date and reason): meta_labeller (H-62, 2026-11-13) and arm_priors (H-60 was never
    wired). long_tf_decisions and swap_reconciliations are not judged: they are bookkeeping, not learners.
  - **`watch:drift_watch`** must cover every self-retrain model in shadow or active, matched by model id.
    drift_watch (goldbot/ops/jobs.py) now stores PSI of their inputs and scores under drift.json `self_retrain`
    (`drift.self_retrain_drift`). It reports only, and never sizes or halts.
    `watch:model_watch` is the champions' CUSUM. A self-retrain filter is judged forward by its own demotion rule.
  - **`model_influence`**: % of the last 24 h's candidates a model scored and % it changed. The count is cached
    15 min, and a new index `exploration_obs_decision` (migration 44) serves it. It WARNs at 0 % and never FAILs.
  - **`self_retrain`** gives dated WARNs ("run of <date>: ...") for a late run, under 50 % of labels used, a broker
    with 0 labels, and 0 active models.
  - **`stop_coverage:<account>`**: FAILs on any open position without a broker stop, separating the engine's own
    positions from foreign ones. It WARNs "unknown" when positions are unreadable, partly listed, or the engine state
    is stale.
  - **Noise removed:** up to 3 late broker minutes are an info note on engine:<account> (replaced and still-sampled
    counted apart), and a missing optional key reads ok. Steady state has 0 noise WARNs. The learning WARNs stay
    until each learner is fixed.
  - **`scripts/learning_kpis.py`** (read-only): K1 usable rows per broker x class x 9 tf, K2 influence with its money
    effect, K4, and self-retrain drift.
  - **Open:** the engine reads no self_retrain model, so an ACTIVE filter changes no decision.
  - **Revisit:** "model_watch need not cover self-retrain filters" must be revisited when the engine starts reading a
    filter. A filter that acts on decisions needs a champion-grade CUSUM watch.
  - **Deploy note:** migration 44 builds `exploration_obs_decision` on the brain's large exploration_obs. Before the
    deploy, build it as the service user outside market hours (`CREATE INDEX IF NOT EXISTS exploration_obs_decision
    ON exploration_obs(decision_ns)` on state/aux.db), or time it on a copy first. The migration is then a no-op.
  - **Grace start:** the 48 h FAIL clock starts at the first deploy whose `to` commit contains 67a2fa46 (git
    ancestry, state/deploys.jsonl), never before 2026-11-26 22:00 UTC.
