---
number: 47
title: Pre-registration writer for the shared registry (queued rows, slots, sync to `research-v1`)
stage: Idea generation (integrity) · FIND
size: S
status: 'done (PR #76, main 520d731; ab24d87, 94ebde4; c5480ae in PR #77)'
---

- **47. Pre-registration writer for the shared registry (research director, 2026-10-11).**
  Value: the Q1 queue is held in code only once its rows exist, but `TrialRegistry.preregister(queue=True,
  target_quarter=)` and `reserve_slot` are library calls with no command or workflow. The registry lives on release
  `research-v1` (checked 2026-10-11: 20 Q4 rows, no `preregistered` row, no slot). Do: a `scripts/` command plus a
  `workflow_dispatch` job that takes a reviewed JSON spec (rows with family, config, feature version, rationale with
  the freeze commit, `rule_id`; slots with parent, kind, k, derivation rule, survivor pool), writes them under the
  registry lock, unions with `research-v1` before and after (`registry_sync`), and prints the written rows. It
  refuses a spec whose config hash differs from the pre-registration's, and it refuses to write a queued row once its
  quarter has started (the existing guard). Acceptance: a dry-run mode that prints rows without writing; tests on a
  temporary registry; the Q1 spec (5 rows + 4 slots, or 4 + 4 if H-06 is withdrawn) checked by the quant reviewer
  before the real run. Security review (workflow token scope). Depends on: none.
  *Built 2026-10-11 (security approved; quant review pending):* `scripts/preregister_quarter.py`, the spec
  `docs/research/preregistration-2027Q1.yaml` (YAML rather than JSON, for the multi-line rules), the
  `preregister-quarter` workflow, `scripts/registry_release.sh`, `tests/test_preregister_quarter.py`,
  `tests/test_registry_release.py`. The spec is not writable until H-02's config, H-06's count (or withdrawal) and the
  freeze commit are settled; the dry run works now.
  Sweep 2026-10-16 (product owner): Done (PR #76, main 520d731; ab24d87, 94ebde4; c5480ae in PR #77). Acceptance record: merged on green CI into main; checked against git and the named tests exist; the criteria were not re-run one by one in this sweep.
