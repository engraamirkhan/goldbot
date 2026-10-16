- Trial registry push re-checks the release asset just before the swap (2026-10-16, BACKLOG 51). The VPS monthly loop
  pushes `research-v1/registry.jsonl` outside the workflows' `concurrency: research-registry`, so a workflow save
  that landed between the loop's pull and its push was overwritten, and the other way round too. Both writers now run
  one implementation: `goldbot.research.registry_sync`, over HTTP on the VPS and through `gh` in the workflows.
  `scripts/registry_release.sh fetch | save | recover` is a thin wrapper over it. Each save is a compare-and-swap:
  - Note the asset's stamp (id, updated_at, size, digest) at the pull.
  - Upload the merged rows as `registry.jsonl.pending-*`.
  - Re-read the stamp, then delete the pulled asset by its id.
  - Rename the pending asset, then re-read: published means our id is the lowest-id `registry.jsonl`, with the
    deleted id filtered out. The rename's status code is not relied on. A re-read that still lists the deleted id,
    or does not list our upload, is stale and is read again after a wait; our upload is never dropped on one.

  Downloads are by asset id (`gh api -H 'Accept: application/octet-stream' .../releases/assets/<id>`), and every
  `gh` call times out after 120 s. `recover` prints what each copy adds (row count and trial keys) before it
  publishes.

  Another writer is detected by a changed stamp, a 404 on the delete, or another copy being published at the rename.
  The pending upload is then dropped, and the writer waits a jittered 0.5-1.5 s per attempt, pulls, merges and
  retries, at most 3 times. After that it fails loudly. On the VPS, `registry_sync` records `failed: ...` and the
  loop carries on; in a workflow the job fails. The local file keeps every union it saw, so a lost race drops no
  row. The keyed union counts each trial once in the deflated Sharpe.

  Once the old asset is deleted, the pending upload is never deleted unless another copy is published. If the rename
  keeps failing, the upload stays and the save fails with "run `scripts/registry_release.sh recover`". `recover`
  unions every `registry.jsonl` and `registry.jsonl.pending-*` on the release and publishes the result the same way.
  Reads never start from an empty registry when the release exists, in two cases:
  - a pending upload with no published copy (a writer mid-swap, or stranded);
  - a release with no `registry.jsonl` at all, unless `--bootstrap` is passed.

  The refusal of a union shorter than the published copy is kept, and the VPS applies it too. `--clobber` is no
  longer used. GitHub has no conditional upload, so the re-check alone is not atomic with the delete; the delete by
  id and the post-rename re-read are what keep that window safe. A writer outside this protocol (a manual
  `gh release upload --clobber`) is still not protected against.

  Rollback: a pre-51 VPS release reads a pending-only release as an empty registry and its push deletes and
  re-uploads `registry.jsonl` directly. Do not roll the VPS back while a pending asset is stranded; run `recover`
  first.

  Tests: tests/test_registry_push_race.py and tests/test_registry_release.py. They cover another writer landing
  after the upload, before the delete and during the rename; a rename that fails, or answers 200 without renaming;
  two protocol writers interleaved, including the first-ever push; a failed re-check; recover; bootstrap; and the
  bounded-retry failure.
