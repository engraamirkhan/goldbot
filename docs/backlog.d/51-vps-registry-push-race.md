---
number: 51
rank: 10
title: 'VPS monthly loop registry push: re-check the release asset just before upload'
stage: Idea generation (integrity) · FIND
size: S
status: 'ready · accountable: implementer · due 2026-10-30'
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
