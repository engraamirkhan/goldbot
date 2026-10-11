---
section: next
---
- **Pre-registration writer (BACKLOG 47, 2026-10-11; security approved, quant review pending).**
  `scripts/preregister_quarter.py --quarter 2027Q1 --from docs/research/preregistration-2027Q1.yaml [--dry-run]
  [--allow-late REASON]`, and the `preregister-quarter` workflow (workflow_dispatch; dry run by default with a
  read-only token; the write job holds `concurrency: research-registry`, shared with research.yml). Both workflows
  read and save `research-v1/registry.jsonl` only through `scripts/registry_release.sh`: a failed read fails the job,
  and an upload shorter than the published copy is refused. The YAML is the reviewed extract of the markdown: each
  config is rebuilt the way its runner builds it (`resolve_variant`, shared with `research_pass.py`;
  `discovery_trial_config`, shared with `discovery_pass`) and checked against its pinned hash, agent id and feature
  version; reading rules and the slot derivation rule must be the markdown's text; rows + slots = `reservation` =
  `research.reserved_trials_quarter` <= 20. Writes through `preregister(queue=True, target_quarter=)` /
  `reserve_slot` under the lock, idempotent, never a trial. **Not writable yet, by design:** H-02's config is owed
  by the strategy researcher (2026-12-11), H-06's count (or its withdrawal) by BACKLOG 48, and `freeze_commit` by the
  research director (2026-12-14).
