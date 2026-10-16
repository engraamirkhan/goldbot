- Trial registry push re-checks the release asset just before the swap (2026-10-16, BACKLOG 51). The VPS monthly loop
  pushes `research-v1/registry.jsonl` outside the workflows' `concurrency: research-registry`, so a workflow save
  that landed between the loop's pull and its push was overwritten, and the other way round too. Both writers now run
  the same compare-and-swap (`goldbot.research.registry_sync.push_release` and `scripts/registry_release.sh save`).
  Each one notes the asset's stamp (id, updated_at, size, digest) at the pull and uploads the merged rows as
  `registry.jsonl.pending-*`. It re-reads the stamp, deletes the pulled asset by its id, then renames the pending
  asset. Another writer is detected three ways: a changed stamp, a 404 on the delete, or a taken name on the rename.
  The pending upload is then dropped and the writer pulls, merges and tries again, at most 3 times, then fails loudly.
  On the VPS, `registry_sync` records `failed: ...` and the loop carries on; in a workflow the job fails. The local
  file keeps every union it saw, so a lost race drops no row. The keyed union counts each trial once in the deflated
  Sharpe. A pending upload with no published copy means another writer is mid-swap or crashed there. Reads then
  refuse to start from an empty registry; the fix is to rename that asset to `registry.jsonl` on the release page.
  The refusal of a union shorter than the published copy is kept, and the VPS now applies it too. `--clobber` is no
  longer used. GitHub has no conditional upload, so the re-check alone is not atomic with the delete; the delete by
  id (404) and the unique-name rename (422) are what keep that window safe. A writer outside this protocol (a manual
  `gh release upload --clobber`) is still not protected against. Tests: tests/test_registry_push_race.py (a fake
  release, with another writer landing after the upload, before the delete and in the delete-to-rename gap, plus a
  bounded-retry failure); tests/test_registry_release.py (a stateful fake `gh` with the same races for the script).
