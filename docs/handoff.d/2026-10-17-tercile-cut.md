- E-2 top-tercile cut (BACKLOG 75, implementer, 2026-10-17; plan docs/proposals/2026-10-backlog75-tercile-cut-plan.md
  with the quant plan review's conditions 1-7; TRACEABILITY M48). O-4 can now pre-register
  `DiscoveryConfig.threshold_rule: tercile_inner`.
  - The cut (`pipeline.fold_cuts`) is the 2/3 quantile (`method="linear"`) of the raw out-of-fold p. It uses only
    rows that are test rows of an earlier fold and also in fold k's `train_idx`. Only finite p count, and a fold
    with fewer than 50 such rows takes no trade. A candidate is taken when its raw p >= the cut, with no break-even
    floor. Overlapping test folds raise.
  - CPCV (`cpcv.path_selection`) takes the cut from p_raw before the 0.5 fill. Earlier groups on the path are the
    prior set, with no purged variant (quant ruling 1).
  - Default unchanged: `break_even_margin` gives byte-identical walk-forward and CPCV outputs (golden
    `tests/data/tercile_default_golden.json`, captured on 65ece6e before the change). It also keeps the same
    `reading_rule` string and the same `DiscoveryConfig` dump and config hash, because the default rule is left out
    of the dump, so H-02's pin and every queued hash hold.
  - Under the tercile rule the reading rule's "(cross-fitted, break-even + margin)" is replaced, and one sentence is
    added: "promotion needs a separate trial under the live rule".
  - The discovery row records `threshold_rule`, `tercile_q`, `tercile_method`, `margin`, `fold_cuts`, `retention` and
    `share_taken_below_breakeven`. The pre-registration plan records the rule pins.
  - A run under the other rule hashes differently, so it is a new trial. Against a pending queued row it is refused
    (`director.queued_mismatch`).
  - The pre-registration is amended (2026-10-17, before any H-14 count): the cut is pinned, O-4 names
    `tercile_inner`, and O-5/O-6 name the live rule.
  - Not changed: RiskGate, execution, live thresholds, Q4 rows, frozen readings.
  - Quant review round 1 (code APPROVE, quant REQUEST CHANGES), fixed in one commit:
    - Under the tercile rule, "continue" needs at least 1,000 taken trades (`reading_verdict(min_taken=)`, as O-4's
      rule reads). The clause says "(at least 1,000 taken)". The default rule and its string do not move.
    - `tercile_q` and `tercile_method` are `DiscoveryConfig` fields. They are in the dump and the config hash under
      the tercile rule, so another q is another trial, and the clause is built from them. Under the break-even rule
      all three are dropped from the dump, keyed on the literal "break_even_margin", and a non-default q is refused.
    - `check_threshold_config` refuses a tercile run with `step_months < test_months` (overlapping test folds). It
      runs in `discovery_pass` and in the pre-registration writer, before the budget check or any registry row.
    - `threshold_plan` also adds its four keys to default-rule pre-registration plans. Plans are not hashed, so this
      is harmless.
  - Reviews needed: quant re-review of round 1.
