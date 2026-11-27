- 2026-11-27 sr-v3 D-4 (card rev 10, label-blind, K 0): the exit templates T0-T7 are frozen as `xt-v1`
  (`dense_labels.EXIT_FREEZE`, sha256 7924ed2cf26f8191ae9bc018c7bc8aeceef34be8ac0f8359555e1494cbf3af7c), exactly
  the card 2.1 table, with the W2-2 h_ref = H of the tf (factor 1). Reasons:
  `docs/research/prereg-amendment-2026-11-27-sr-v3-d4-exit-freeze.md`.
  - `gate_status` now blocks on `exit_freeze_hash(spec) != EXIT_FREEZE_SHA256` instead of `h_ref == "H"`; the
    shipped spec clears the D-4 block. The D-3 block (w2-point cost rows) is unchanged.
  - `LabelSpec.h_ref` is a per-tf seconds map and d0 = max(ATR14 x sqrt(H/h_ref), floor) is now computed, not
    implied. Label values are numerically unchanged, but spec and code hashes change: every dl-v1 partition re-keys
    and the label factory must re-run before the next trainer read.
  - Quant sign-off required; open question for the quant: whether the D-12 rho bindings (measured with h_ref = 1 bar)
    carry over to xt-v1.
