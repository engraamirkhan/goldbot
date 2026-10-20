- 2026-10-20 RiskGate cost-measurement purpose (ADR 0037 Amendment 5, owner-approved "Allow, demo only"): scheduled
  `cm-sched-` probes ask `RiskGate.check` for `Intent.purpose="cost_measurement"`, which skips ONLY
  `time_exit_ev_unsupported`/`negative_gross_edge`, inside a fence: a learning probe, demo account and no live gate,
  the terminal's own `trade_mode` demo read at the order, the minimum lot, a stop <= $3/oz (as sized), a planned hold
  <= 60 s and the loss at the stop within the probe budget headroom and the dynamic per-probe ceiling. Every halt,
  window, cap, projection and sizing limit unchanged; mirror probes unchanged. Tests: tests/test_cost_measurement_gate.py,
  tests/test_probe_scheduled.py. Trading-safety review pending.
