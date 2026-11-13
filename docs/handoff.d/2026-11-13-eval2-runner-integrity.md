- 2026-11-13 (branch fix/eval2-runner-integrity; BACKLOG 149, 150; R138): EVAL-2 reads of real cells now go through
  `goldbot/research/eval2/runner.py`. Every L2, L3 and forward read calls `assert_harness_sane` on the current
  `harness_hash()` first (S5 too for forward) and refuses with `ReadRefused` before touching a unit when the self-tests
  have not all passed for this tree; nothing is computed or written. The L3 batch runner writes every family's
  `family_cv_p` for batch j once to `state/catalogue_reads.jsonl` (`kind: l3_batch_p`, no `window` key, so batch
  indexing is unchanged) and `run_l3_read` takes `batch_p` only from there; units and corroboration are cut at
  decision + registered maximum hold before the holdout; a futility read's registry row must be a prior
  pre-registration of the same family and config. `confirm.l3_read` is unchanged; no trial ran, K unchanged.
  runner.py sits in the harness glob, so the harness hash changed: the self-tests must be re-run before any read.
- Quant review of f18881ab (same branch): the holdout cut now reads a per-unit `bound_end_ns`, the open of bar
  i + valid + hold + 1 on the sim's own bars (`eval2.runner.unit_bound_end_ns`, so weekends, decision -> fill and
  the kind-3 extra bar count; past the last bar counts as reaching the holdout). It is computed in eval2, not emitted
  by catalogue/sim.py: any edit under goldbot/research/catalogue fails `catalogue.run.code_matches` and voids the
  frozen catalogue screen. Batch membership and m_j come from a frozen batch card checked by sha256
  (`freeze_batch_card`); the batch-p row records the card and per-family units hashes and an L3 read on other
  units is refused. Withdrawn or expired (ad hoc, > 7 days) futility pre-registrations are refused. The self-test
  module runs from the file's current bytes, and a test fails if anything else calls l3_read, family_cv_p or
  forward_state. BACKLOG 207 carries the quant ruling: every carried cell registers its sim ev.hold before L2.
