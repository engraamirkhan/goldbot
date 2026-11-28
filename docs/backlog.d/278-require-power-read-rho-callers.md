---
number: 278
rank: 101
title: Power-read and Stage 0 drivers call data_sufficiency.require_power_read_rho
stage: 'Data (sr-v3 rho)'
size: S
status: proposed (data-engineer with the implementer; quant, re-verify of #525)
---
- **278. Power-read and Stage 0 drivers call data_sufficiency.require_power_read_rho.** Value: the #514 ruling made `require_power_read_rho(cfg, power)` the one entry point a Stage 0, power or gate read calls before it uses any rho, but no production code calls it (tests only). A power or Stage 0 driver can therefore read a pre-xt-v1 or unverified rho file without the guard running. Acceptance: `scripts/research/train_sr_v3.py` (Stage 0), the power-first start path (`power_first.start_or_refuse` or its driver) and the sufficiency renderer's gate rows call `require_power_read_rho` with the shipped sufficiency config and `research.power_first` before their first rho read, and refuse on its ValueError. A test per driver seeds a pre-xt-v1 rho file and shows the driver refuses before reading a label or computing power. A repo test fails if a module that calls `rho_inputs(freeze_sha=...)` or `measured_rho(gate_read=True)` does not also reach `require_power_read_rho`.
