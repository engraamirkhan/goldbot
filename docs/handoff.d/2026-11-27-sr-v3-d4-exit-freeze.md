- 2026-11-27 sr-v3 D-4 (card rev 10, label-blind, K 0): the exit templates T0-T7 are frozen as `xt-v1`
  (`dense_labels.EXIT_FREEZE`, sha256 559739c72fc720a2d27035c14011dc7dc9a12e62da3121496bd16b270478979c), exactly
  the card 2.1 table, with the W2-2 h_ref = H of the tf (factor 1). Reasons:
  `docs/research/prereg-amendment-2026-11-27-sr-v3-d4-exit-freeze.md`.
  - `gate_status` now blocks on `exit_freeze_hash(spec) != EXIT_FREEZE_SHA256` instead of `h_ref == "H"`; the
    shipped spec clears the D-4 block. The D-3 block (w2-point cost rows) is unchanged.
  - `LabelSpec.h_ref` is a per-tf seconds map and d0 = max(ATR14 x sqrt(H/h_ref), floor) is now computed, not
    implied. Label values are numerically unchanged, but spec and code hashes change: every dl-v1 partition re-keys
    and the label factory must re-run before the next trainer read.
  - Quant review of #514 (changes applied): the freeze hashes the exit mechanics too (`behaviour_sha` over
    first_passage, decision_bars/floor_at, entry_and_horizon on a fixed synthetic set); every row and manifest carries
    `exit_freeze_sha` and `gate_status` checks the rows, not only the spec.
  - D-12 bindings do not carry over to xt-v1 (new card item D-12-xt, ml-researcher, before Stage 0); the rho
    validator refuses a file without the xt-v1 sha for gate/power reads (`check_rho_measurement(gate_read=True)`);
    fit-only reads keep the recorded values.
