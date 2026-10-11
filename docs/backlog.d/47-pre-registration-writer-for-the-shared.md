---
number: 47
rank: 42.0
title: Pre-registration writer for the shared registry (queued rows, slots, sync to `research-v1`)
stage: Idea generation (integrity) · FIND
size: S
status: proposed (research director 2026-10-11; needed by 2026-12-04 for the Q1 freeze)
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
