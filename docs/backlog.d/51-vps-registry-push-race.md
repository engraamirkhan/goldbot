---
number: 51
rank: 10
title: 'VPS monthly loop registry push: re-check the release asset just before upload'
stage: Idea generation (integrity) · FIND
size: S
status: 'merged on the integration branch 2026-10-16 (5053d31, review fixes c027221 and cc00630); not in PR #79, ships in the next PR; security re-verify of cc00630 not on record · accountable: release-manager · due 2026-10-23'
---
- **51. VPS monthly loop registry push: re-check the release asset just before upload (security re-verify of
  BACKLOG 47, 2026-10-11).** Value: research.yml and preregister-quarter.yml serialise their writes to
  `research-v1/registry.jsonl` through `concurrency: research-registry`, but the VPS monthly loop pushes through
  `goldbot.research.registry_sync.sync` outside that group. A workflow upload that lands between the loop's pull and
  its push is overwritten, and the reverse can happen too, so a trial or pre-registration row can be dropped from the
  shared copy. Do: in `registry_sync.push_release`, and in `scripts/registry_release.sh save`, record the asset's
  `updated_at` (or id) when pulling, re-read it just before the delete and upload, and when it changed, pull again,
  merge again and retry a bounded number of times, failing loudly if it keeps changing. Keep the existing refusal of
  a merge shorter than the published copy. Acceptance: tests with a fake release whose asset changes between the pull
  and the upload, showing both rows survive, and a bounded-retry failure. Security review. Depends on: BACKLOG 47.
  Sweep 2026-10-16 (product owner): accountable `implementer`, due 2026-10-30, ahead of the Q1 row writes (2026-12-18). Security review. Acceptance as above.
  **Built 2026-10-16:** one protocol in `goldbot.research.registry_sync` for both writers (HTTP on the VPS, `gh` via
  `scripts/registry_release.sh fetch | save | recover`). It notes the asset's stamp (id, updated_at, size, digest) at
  the pull and uploads the merge as `registry.jsonl.pending-*`. It re-checks the stamp, deletes the pulled id (a 404
  is a lost race), renames, then re-reads, so our id must be the published copy. A lost race drops the pending
  upload, waits a jittered backoff, then pulls, merges and retries up to 3 times before failing loudly. After the
  delete, the pending upload is kept unless another copy is published; a rename that keeps failing leaves it for
  `recover`. Reads refuse a pending-only release, and refuse a release without `registry.jsonl` unless `--bootstrap`
  is given. The shorter-merge refusal is kept and applies on the VPS too. Tests: tests/test_registry_push_race.py,
  tests/test_registry_release.py.
