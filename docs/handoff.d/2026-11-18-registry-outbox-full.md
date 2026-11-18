- Registry outbox never cleared (SRE, 2026-11-18): `scripts/registry_publish.sh` now also reads the brain's whole
  `state/research_registry.jsonl` (read-only, `sudo cat`; a failed read stops the publish), so rows written after the
  outbox snapshot are published too; the brain clears the outbox once the release holds the OUTBOX's rows (not every
  local row), in the weekly sync and in a new daily read-only scheduler job `registry_outbox` (10:20 UTC,
  `registry_sync.check_outbox`); a sync after a published entry restarts the wait for newer rows. Health
  `feedback_loop` keeps judging the outbox age (the fallback when the release cannot be read). Deploy to the brain;
  the stuck entry clears at the first run (`run_if_never_run`).
