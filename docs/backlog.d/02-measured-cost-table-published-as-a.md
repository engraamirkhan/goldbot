---
number: 2
rank: 5
title: Measured cost table published as a release asset for research
stage: Execution quality · FIND
size: S
status: 'done (PR #62)'
---

### 2. Measured cost table published as a release asset
Value: removes the owner's manual commit step and makes `research.yml` charge measured swap, commission and slippage
instead of the pessimistic priors. That alone may move tsmom 4h across zero (FIND).
Acceptance:
- A scheduled job (or the `nightly_costs` job) exports the canonical cost table and uploads it to release `costs-v1`
  as `costs_measured.json`, with `measured_at` and the fill count per field.
- `research.yml` fetches that asset. Its report states, per field, whether the value is measured or a prior.
  Integration test: with a fixture asset, the pipeline uses the asset values. Without it, it falls back to the priors
  and says so.
- The export refuses (non-zero, no upload) when swap is unmeasured or there are fewer than 50 fills, so a prior is
  never published as a measurement.

Depends on: 1 for real values (the plumbing can land first). Rows: X9, X10, D4.
