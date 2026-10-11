---
number: 50
rank: 43
title: --discover cuts b1 at holdout_from; data-v1 digests pinned in the plan and verified by runners
stage: Idea generation (integrity) · FIND
size: M
status: proposed (quant review F2 of BACKLOG 47, 2026-10-11; due before the 2026-12-11 freeze)
---
- **50. --discover cuts b1 at holdout_from; data-v1 digests pinned in the plan and verified by runners (quant review
  F2 of BACKLOG 47, 2026-10-11).** Value: H-02's queued row hashes the exact first and last 1m bar the run loads
  (`bars_from`/`bars_to`), so the hash depends on how far data-v1 reaches on the day of the run, and a refresh between
  the write and the run makes the run miss its row. Reviewer's wording: "`--discover` cuts b1 at holdout_from. Record
  data-v1 parquet sha256 digests in the plan, and have runners verify them for every queued row. A hash mismatch with
  a pending queued row refuses instead of silently falling back." Do: cut b1 at `research.holdout_from` in
  `--discover` before the config is built (so `bars_to` no longer moves with data-v1); record the data-v1 parquet
  sha256 digests in each queued row's `plan` (`scripts/preregister_quarter.py`); in `research_pass.py` (and
  `--discover`), verify the digests of every queued row the run could match, and when a pending queued row of the same
  family exists but the config hash differs, refuse with the difference instead of falling back to the open budget.
  Acceptance: tests that a data-v1 refresh after the write is refused with the digest named; that a config-hash
  mismatch against a pending queued row refuses (no fallback); that `--discover`'s `bars_to` is before
  `holdout_from` whatever the data reaches. Quant review. Depends on: BACKLOG 47.
