- 2026-11-15 (branch feat/power-first-gates; R157; ADR 0085): power-first research gates and the alpha-spending Holm
  confirm, for **research and demo promotion only**. This follows the panel ruling of 2026-11-15 on the evaluator's
  memo, docs/research/power-first-gates-2026-11.md (carried on this branch).
  - **The owner's phase gate is pinned.** Gate 1 and the tournament's shadow -> live check read
    `registry.phase_gate_refusal`. This is the old `promotion_refusal` under a new name, with the body unchanged.
    tests/test_phase_gate_pin.py compares their decisions with a golden file written by 9d1abbc7 and finds them byte
    for byte the same. Live unlock, the real-money confirmation and RiskGate are untouched.
  - **Power-first rule** (re-ruled by the quant 2026-11-15). `goldbot/research/power_first.py` (`evaluate`,
    `start_or_refuse`) powers every gate for a fixed delta of 0.10 R. It uses
    n_eff = n / (1 + (m_day - 1) 0.25) / (1 + (k - 1) rho_inst), with rho_inst 0.5, or 0.7 for metal pools; both rho
    values are PRIORS. sd_tf = max(1.2, the demo-measured sd), and the measured table is still empty.
    - A card starts only if every gate has >= 0.5 power, the joint power is >= 0.25, and n_eff is >= 200 at each
      fresh-data gate.
    - goal_edge_R is recorded as information only and never gates.
    - A refused card gets a `not_started_power` registry row: no K and no trial number, also after a merge. The
      registry accepts that status only from `start_or_refuse`.
    - Settings can only tighten.
  - **Holm confirm.** `goldbot/research/promotion_confirm.py` (`run_batch`, `research_promotion_refusal`) runs Holm
    at alpha_j = 0.05·2^-y / 4. There is one global ledger, made of `promotion_confirm` trial rows. The rules:
    - TEST-OLD is read once per family, and never after an EVAL-2 TEST-OLD read of that family. The EVAL-2
      families are a required argument (`eval2_test_old_families(ledger)`).
    - Ledger years are read in UTC and are never reopened backwards.
    - The holdout is read at most once per config.
    - Every read's frozen stamp must equal its card's pre-registration (`plan.frozen_stamp`), and the read must be
      net of measured costs at the upper CI.
    - A read needs at least 200 clustered effective observations.
    - The registry-K DSR is reported only.
  - **Rule-only gates.** `gates.rule_only_promotion_gates` has no candidate or fold minimums. It counts regime years
    on pooled family trades, or on pre-registered multi-year blocks, which need 10 trades for each year they span.
  - **Not done (ADR 0085 follow-ups):**
    - rewire autodiscovery's monthly 5 % Holm and its demo registration to this ledger;
    - retire EVAL-2 confirm reads as a promotion source;
    - record `rule_only_promotion_gates` in research_pass;
    - derive the rho priors and fill the demo-measured sd by timeframe.
  - The pin golden now also covers a CPCV veto, stale and missing CPCV evidence, missing costs and missing
    results.gates. It was regenerated from a 9d1abbc7 archive only.
  - Review: quant-reviewer (rule encoding, n_eff floor, the open item); trading-safety-reviewer (the phase-gate pin);
    code-reviewer.
