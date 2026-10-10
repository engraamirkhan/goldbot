# 0006 Research evidence across feature versions

Status: accepted for now (quant and trading-safety re-review of the DST migration, 2026-10-10). **The principled fix
(material feature version, below) needs the owner's sign-off.**

## Context
The DST fix (playbook D-1) bumped `session`, `session_zones` and `market_structure` to version 2. `feature_version`
hashes every feature in `DEFAULT_FEATURE_NAMES` (`goldbot/features/registry.py`), so the default version moved from
`f-1b8f723a67` to `f-079deacc61`, although most specialists' models read none of the changed columns
(`Specialist.model_features`). Running models keep scoring their own version (legacy implementations,
`features/legacy.py`, `Engine._scoring_frame`), and research and retrains build v2.

The open question is what a research trial's evidence means once the feature version moves. `TrialRegistry.passed_gates`
can now match on `feature_version` (the holdout check in `scripts/research_pass.py` uses it). If the tournament's
shadow -> live check (`goldbot/ops/jobs.py` `research_passed=...passed_gates(...)`) also matched on it, every
v2 challenger would need a new v2 trial before promotion. No trial has run on v2 yet, and the quarter's budget is
spent, so a correction to three features would void all gate evidence for every family, including families whose
models never read the corrected columns.

## Decision
- The tournament's `passed_gates` stays **version-agnostic** for now. Because the hash covers every feature, matching
  on it would let any correction, however local, void all research evidence.
- The holdout check (`research_pass.py --score-holdout`) **does** match on feature version: scoring the holdout is the
  final verdict on a model built from those exact features. `holdout_scored` stays version-agnostic, because a
  second scoring after a version bump would be a second look at the same window.
- A trial run on a feature version different from its pre-registration's is recorded with a `deviation`
  (`TrialRegistry.record`). The 2027Q1 pre-registration pins `f-079deacc61`.
- Follow-up, not yet built: the tournament summary should show any gap between the passing trial's
  `feature_version` and the live model's, so a promotion on evidence from another version is visible (BACKLOG 34).
- **Principled fix, owner sign-off needed (BACKLOG 34):** a "material feature version" hashed over the columns a
  specialist's model actually reads (its selected `model_features` and their feature versions), recorded on trials
  and models. Evidence then carries over a version bump exactly when the bump does not touch the columns the model
  uses, and the engine and drift job could rebuild only those features.

## Consequences
- No downtime and no voided evidence from the DST fix. The price is that a promoted v2 model may rest on a gate
  pass measured with v1 session features. That is acceptable only because the change is a correction (an hour's
  shift on part of the year) and every promotion still needs its own shadow record.
- Until the material version exists, any feature bump in the default set changes every model's version. Each one
  needs a legacy implementation (or the affected models stop scoring) and a reviewer's call on evidence.
- `features/legacy.py` is deleted once the health check `feature_versions` shows no legacy models.
