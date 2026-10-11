---
section: next
---
- **Pre-registration writer (BACKLOG 47, 2026-10-11; security approved; quant blockers M1-M5 fixed, re-review
  pending).** `scripts/preregister_quarter.py --quarter 2027Q1 --from docs/research/preregistration-2027Q1.yaml
  [--dry-run] [--allow-late REASON]`, and the `preregister-quarter` workflow (workflow_dispatch; dry run by default
  with a read-only token; the write job runs only on `main`, with full history, and holds
  `concurrency: research-registry`, shared with research.yml). Both workflows read and save
  `research-v1/registry.jsonl` only through `scripts/registry_release.sh`: a failed read fails the job, and an upload
  shorter than the published copy is refused. The YAML is the reviewed extract of the markdown. Each config is
  rebuilt the way its runner builds it (`resolve_variant`, shared with `research_pass.py`; `discovery_trial_config`,
  shared with `discovery_pass`; an end-to-end test shows `--discover` links the written H-02-style row) and checked
  against its pinned hash, agent id and feature version. Each reading rule must be the whole text of a "Reading rule"
  bullet of the markdown, read at `freeze_commit` (`git show`). `bars_from`/`bars_to` must be the run's own UTC
  timestamp text. Rows + slots = `reservation` = `research.reserved_trials_quarter` <= 20. Writes through
  `preregister(queue=True, target_quarter=)` / `reserve_slot` under the lock, idempotent, never a trial; a withdrawn
  row (or a slot of a withdrawn parent) no longer blocks a spec without it, and no slot is reserved on a withdrawn
  parent. **Late:** `--allow-late` writes ad-hoc rows only, no slots (rows 6-9 lost; H-02 survivors from the open
  budget), and is refused unless the settings' 2027Q1 reservation equals the queued rows already standing (normally
  0), changed in the same change. research.yml gains `discover`, `families`, `discover_config` and `rule_ref` inputs,
  and a `macro` run now fails if macro-v1 cannot be downloaded. **Not writable yet, by design:** H-02's config is
  owed by the strategy researcher (2026-12-11; F1: the markdown's H-02 method vs `DiscoveryConfig` defaults is for
  the research director), H-06's count (or withdrawal) by BACKLOG 48, and `freeze_commit` by the research director
  (2026-12-14). BACKLOG 50 (F2: holdout cut in `--discover`, data-v1 digests) is due before the freeze.
