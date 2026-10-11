---
number: 34
---
- **34. Material feature version (ADR 0006; needs the owner's sign-off).** Value: a feature correction voids research
  evidence and forces legacy rebuilds only for the models that read the corrected columns. Acceptance:
  - A material version is hashed over a specialist's selected `model_features` and the versions of the features
    that produce them. It is recorded on trial rows and model entries, beside today's `feature_version`.
  - `passed_gates` in the tournament matches on the material version.
  - Until then, the tournament summary shows any gap between the passing trial's `feature_version` and the live
    model's.
  - Tests: a bump of a feature no model reads leaves the material version unchanged; a bump of a read column
    changes it.
