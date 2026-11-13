- 2026-11-13 (branch fix/eval2-runner-integrity; BACKLOG 149, 150; R138): EVAL-2 reads of real cells now go through
  `goldbot/research/eval2/runner.py`. Every L2, L3 and forward read calls `assert_harness_sane` on the current
  `harness_hash()` first (S5 too for forward) and refuses with `ReadRefused` before touching a unit when the self-tests
  have not all passed for this tree; nothing is computed or written. The L3 batch runner writes every family's
  `family_cv_p` for batch j once to `state/catalogue_reads.jsonl` (`kind: l3_batch_p`, no `window` key, so batch
  indexing is unchanged) and `run_l3_read` takes `batch_p` only from there; units and corroboration are cut at
  decision + registered maximum hold before the holdout; a futility read's registry row must be a prior
  pre-registration of the same family and config. `confirm.l3_read` is unchanged; no trial ran, K unchanged.
  runner.py sits in the harness glob, so the harness hash changed: the self-tests must be re-run before any read.
- Follow-up: BACKLOG 207 (a carried cell with no registered time exit is refused at L3 until the quant rules).
