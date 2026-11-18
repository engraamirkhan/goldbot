- 2026-11-18 arm lifecycle, builds 2 and 3 (owner directive "find, deploy, measure, promote and retire strategies by
  itself"; rule docs/research/arm-lifecycle-2026-11-18.md, revised by the quant, trading-safety and code reviews).
  Build 3: aux.db `arm_registry` (migration 37, append-only, hash-chained; the verified tip kept in core.db
  `arm_registry_seen`, core migration 6), written by `autodisc-register --to-db STATE_DIR`, hot-loaded by the engine
  at a pass boundary (goldbot/engine/arm_registry.py) on demo probe_only accounts only (`risk.gate.is_demo` at load,
  re-read and signal); spec_rule strategy rows only, field whitelist, cap within the style ceiling; a bad row refuses
  only its arm; a broken chain or a tail below the verified tip, a stale read or a partial load refuses every
  hot-loaded entry; rows re-judged every load (a refused or missing row keeps its arm, entries refused); restart holds
  remembered arms until a verified load; orphan close held at most 3 minutes, never past the weekend backstop; health
  FAILs an engine whose registry is unverified for 5 minutes. Build 2: nightly job `arm_lifecycle` (06:25 UTC;
  goldbot/research/arm_lifecycle.py), KILL-ONLY under HV Rule 3: excess of the arm's replayed candidates over its
  matched control's replay labels per (UTC hour, side) cell, day terms frozen in aux.db `arm_lifecycle_terms` only once their labels settle (day end + longest hold, weekend
  included, + 1 day; > 5 % unlabelled then = void day), kill rules read on the broker's own accounts, RETIRE at
  e >= 40 or a kill rule (through `exploration_arm_status`), else HOLD; no promotion, no SIZE_UP; red-box week arms by
  their kill rules only. Until the replay-parity monitor (feat/replay-only-controls) is merged every twin reads
  unvalidated and only kill rules decide. Discovered arms load held by ADR 0061 (no learning-arm registration: needs K
  rows and a spec_rule bracket match). Forward evidence (`forward:`/`adr0088:`) is refused on non-demo loads and by
  `phase_gate_refusal`.
