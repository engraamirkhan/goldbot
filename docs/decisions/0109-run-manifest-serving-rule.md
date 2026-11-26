# 0109: Run manifests and the model serving rule

Status: proposed (principal software engineer, 2026-11-26); reviews: quant-reviewer (rules), trading-safety-reviewer
(serving refusal), sre (monthly job), code-reviewer, security-reviewer

## Context
ADR 0106 decision 7 requires that any past model can be rebuilt exactly from its logged data version, settings, seed
and code sha, that every change is reversible, and that retraining runs monthly at least on a fixed schedule. Its
goals add: results stable across at least 5 seeds, profitable under slight parameter shifts, and every deep model (A,
B; decision 3 adds D) beats the gradient-boosted baseline (C) out of sample after costs or it is not deployed. Before
this ADR the model registry kept only each artefact's sha256; no run recorded its data version, settings, seeds, code
commit or dependency lock, so no model could be rebuilt or audited, and nothing stopped an A/B/D model from serving.

Two trainers are being built in parallel (IF-1 sr-v3, IF-2 seq1/RL-X1). IF-2 already carries its own run log
(`run_log.py`, a per-model JSONL); a second, incompatible record per trainer would leave the serving rule nothing
uniform to read.

Options for storage: (a) an aux.db table: transactional, but a migration number clashes with every parallel lane and
the brain is not where most fits run; (b) a JSONL ledger: mutable, edits invisible; (c) content-addressed immutable
JSON files beside the artefacts (`models/manifests/<sha256>.json`): no migration, an edit is detected on load, backed up
with the artefacts they describe.

## Decision
1. **One manifest per fitted artifact** (`goldbot/research/run_manifest.py`, option (c)): data version and per-file
   sha256, settings and their canonical hash, every seed, git HEAD and whether the tracked tree was clean, uv.lock
   sha256, runtime versions, train end and scaler fit end (refused when the scaler saw data after the train end), the
   artifact's sha256, the builder (`module:function`), results and the model-quality verdicts. The id is the sha256 of
   the canonical content.
2. **The trainers' hook is one call**: `run_manifest.fit_and_record(builder, ...)` (fits through the same code path
   the rebuild uses, one BLAS/OpenMP thread) or `run_manifest.record(artifact=<bytes>, ...)`. IF-2's `run_log` should
   become a call to `record` rather than a second ledger.
3. **Rebuild** (`run_manifest.rebuild`, `scripts/research/rebuild_model.py`) refuses a different uv.lock, a dirty tree,
   another commit, a changed data file or a missing builder before any fit, and reports any byte difference after it.
4. **Serving rule** (`model_quality.require_servable`, applied by `ModelRegistry.load_for_serving` for
   `champion_models` and `shadow_models`): a fitted model serves only with a manifest from a clean tree, a passing
   `seed_stability` over its own >= 5 seeds, no failed verdict, and, for families A, B, D, a passing `beats_baseline`
   whose baseline is a recorded C-family manifest. The rule and its thresholds are code; settings (`model_quality:`)
   can tighten the studies, never the rule. A refused model is left out and logged (the engine then proposes nothing
   for that agent), never served.
5. **Grandfathering**: fitted registry entries created before `MANIFEST_REQUIRED_FROM` (2026-11-27 UTC) keep serving;
   rule models (ADR 0038) are not fitted and need no manifest. Refusing the existing champions at deploy would remove
   models from the decision path without a review, and the specialists' retrain that made them is retired.
6. **Monthly walk-forward of record**: scheduler job `monthly_walkforward`, the 1st at 04:30 UTC, window ending 00:00
   UTC that day; it calls the trainers listed in `model_quality.monthly_trainers` and checks their manifests. It has no
   promotion path; promotion stays with the gates (ADR 0106 decision 6).
7. **Rollback** of a registry model is one command: `python -m goldbot.ops.run rollback-model <agent_id>`; refused when
   there is no previous champion or when the previous one would not serve.

## Consequences
- A model trained after 2026-11-27 without a manifest does not reach the engine. The retired specialist retrain, if
  re-enabled, must record a manifest first.
- `engine/shadow_scoring` (self_retrain_models in aux.db, where sr-v3 and seq models are expected to serve) does not
  read the registry; its loader must call `model_quality.require_servable` in the hook PR that admits a new spec
  (trading-safety review). Until then the rule covers the registry path only.
- The monthly job runs on the scheduler's single thread: a trainer listed there must bound its own run (a niced child
  with a budget, as discovery_nightly does) before it is added.
- Byte-identical rebuilds hold on the same platform and lock; a rebuild on another CPU architecture may differ in the
  last float bits, and the mismatch message names the runtime difference.
- Rebuilds read data files by path and sha256; a data file deleted or rewritten later makes the model unrebuildable,
  so the data version record (lane 5, dataset versions) must keep what manifests reference.
