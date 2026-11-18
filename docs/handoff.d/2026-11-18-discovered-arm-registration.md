- 2026-11-18 discovered arms on demo, the learning-class registration (standing amendment
  docs/research/prereg-amendment-2026-11-18-discovered-arms.md and docs/research/discovery-k-cap-2026-11-18.md; R163).
  `python -m goldbot.ops.run autodisc-register <hash> --account <demo> --to-db STATE_DIR --learning` checks the 2.4
  bar (explore survivor, 1h/4h gold spec, gross >= 2 x 0.90 $/oz, net > 0, kill rule readable by `until`: >= 50
  expected candidates and >= 10 signal days), the window (from 2026-11-22 22:00 UTC, none after 2027-03-31), at most
  5 running and `used + reserved + 1 <= research.trial_budget_quarter`, then writes under the registry lock the K row,
  the control's NOT_TRIAL row (its frozen hash), the journal row and the arm + control rows in one aux.db transaction,
  or a LEARNING_VOID marker (K kept, spec never again). The explore record must carry `n_bars`, `n_days`,
  `n_signal_days`, `gross_usd_mean`, `net_usd_mean`: today's ledgers do not, so nothing registers until
  discovery_nightly (ADR 0090) writes them. The engine hot-loads the pair only matched and frozen
  (`control_freeze_refusal`; a change journals `arm_registry_control_changed` and holds the arm), at most 5 at once;
  the control (`spec_control`) never sends (replay_only); a learning arm's bracket (`arm_rules_spec.bracket`, bars
  closed at the decision) is recomputed at the entry: late (> 60 s) or off by > 1e-9 -> void, not sent, arm held.
  1d discovered arms are refused until the 60 s latency guard is ruled on for ADR 0081's 22:30 entry. The phase gate
  and the tournament's `passed_gates` read a discovered spec's DSR at `n_trials_discovery` (a tightening; golden
  unchanged). `spec_rule` is at rule version 2 (`end_utc`). The nightly lifecycle read voids bracket-refused and
  post-control-change reads (5.1 (5)). The plan reports the discovery hand-off's `power_forward`
  (`handoff.candidate`; reported, never a gate). `autodisc_register.LIFECYCLE_READY` stays False: every registration
  is refused until the quant and trading-safety re-review flips it.
