- 2026-11-26 ADR 0106 lane 3, reproducibility and model quality (ADR 0109). Every training run can now record one
  immutable run manifest (`goldbot/research/run_manifest.py`, `models/manifests/<sha256>.json`). It holds the data
  version and per-file sha256, the settings and their hash, the seeds, the git HEAD and whether the tree was clean, the
  uv.lock hash, the runtime, the train end, the scaler fit end (refused if after the train end), the artifact sha256,
  the builder, the results and the quality verdicts.
  - Trainer hook, one call: `run_manifest.fit_and_record("pkg.mod:build", ...)` or `run_manifest.record(artifact=...)`;
    quality verdicts from `model_quality.seed_stability`, `param_shift` and `beats_baseline` go in `quality=[...]`.
  - Rebuild: `uv run python scripts/research/rebuild_model.py <manifest id | registry version> --repo <clean
    checkout at code_sha> --data-root <dir>`; exit 0 identical, 1 different bytes, 2 refused (lock, dirty tree, other
    commit, changed data, no builder). Tested byte-identical on a tiny LightGBM and a tiny sklearn MLP.
  - Serving rule: `ModelRegistry.champion_models` and `shadow_models` leave out (and log) a fitted model created on or
    after 2026-11-27 without a manifest that passes `model_quality.require_servable`: a clean tree, a passing
    `seed_stability` over at least 5 of its own seeds, no failed verdict, and, for families A/B/D, a passing paired
    after-cost win over a C-family manifest. Older fitted entries and rule models serve as before.
  - Review rulings (ADR 0109 item 8): seeds = `seed_list(manifest_key)`, `served` declared; `beats_baseline` on
    learn-window OOF rows vs the unit's C manifest (same train_end and folds), block max(5, ceil(1.5 x horizon days)),
    B >= 10,000, one registry trial per read, `holm_adjust` across the unit's A/B/D reads; manifests carry
    `label_end_max`. A bad manifest or artefact refuses only its own model; the engine's reload catches every error.
  - Monthly walk-forward: scheduler job `monthly_walkforward` on the 1st at 04:30 UTC calls
    `model_quality.monthly_trainers` (empty, and refused while non-empty until trainers run in a bounded child) and
    never promotes; a manifest whose labels end at or after the window end fails it.
  - `python -m goldbot.ops.run rollback-model <agent_id> [--reason TEXT]` restores the previous champion under the
    registry lock and keeps the demoted one as previous (run it again to roll forward); it refuses when there is none or
    when that one would not serve. A CUSUM alarm whose previous model would not serve retires the champion and fails
    health `model_watch`.
  - Safety review round 2: the Saturday retrain (`saturday_retrain`, off while `research.specialist_retrain` is false)
    fits seed_list(key) 5 seeds, records a family-C manifest with a seed_stability verdict, and registers seed 0 only
    when the serving rule admits it (else it reports why and registers nothing). `promote` refuses a challenger the
    serving rule refuses. A blackout re-score with no served model writes `blackout_rescore_no_model` and the engine
    publishes `open_without_model` (health `engine:<account>` warns); exits untouched. New health check
    `model_serving` fails while a champion is refused and warns for a refused challenger. Registry writes re-read
    registry.json under `registry.lock`, so a long-lived scheduler or engine instance can no longer undo a
    rollback-model. Other trainers are not affected: self_retrain (aux.db) and the nightly classifier never touch
    the model registry.
  - Round 3: the engine's registry lock wait is bounded at 1 s (`ENGINE_REGISTRY_LOCK_S`, `rule_shadow_set`), so a
    stalled writer can no longer freeze the engine loop; `ModelRegistry.entries` re-reads a changed file; a failed
    recalibration of one model no longer aborts the weekly job; the refit's data_version covers the context bars.
  - Not yet wired: `engine/shadow_scoring` (aux.db self_retrain_models) does not call `require_servable`. The sr-v3
    and seq hook PRs add it under trading-safety review.
  - Deploy: goldbot/config.py is a bridge file, so this deploys in the 21:10-23:30 UTC window. The scheduler restart
    registers `job:monthly_walkforward`.
