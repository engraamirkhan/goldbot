- 2026-11-15 (branch feat/power-first-gates; R157; ADR 0085): power-first research gates and the alpha-spending Holm
  confirm, for **research and demo promotion only**. This follows the panel ruling of 2026-11-15 on the evaluator's
  memo, docs/research/power-first-gates-2026-11.md (carried on this branch).
  - **The owner's phase gate is pinned.** Gate 1 and the tournament's shadow -> live check read
    `registry.phase_gate_refusal`. This is the old `promotion_refusal` under a new name, with the body unchanged.
    tests/test_phase_gate_pin.py compares their decisions with a golden file written by 9d1abbc7 and finds them byte
    for byte the same. Live unlock, the real-money confirmation and RiskGate are untouched.
  - **Power-first rule.** `goldbot/research/power_first.py` (`evaluate`, `start_or_refuse`) refuses a card below
    0.5 power at any gate or 0.25 joint power. A refused card gets a `not_started_power` registry row. That row is
    not a trial, so it costs no K and reads no data. Settings: `research.power_first` (sd table, rho priors, sims).
  - **Holm confirm.** `goldbot/research/promotion_confirm.py` (`run_batch`, `research_promotion_refusal`) runs Holm
    at alpha_j = 0.05·2^-y / 4. There is one global ledger, made of `promotion_confirm` trial rows. The rules:
    - TEST-OLD is read once per family, and never after an EVAL-2 TEST-OLD read of that family.
    - The holdout is read at most once per config.
    - Every read carries a frozen stamp and must be net of measured costs at the upper CI.
    - A read needs at least 200 clustered effective observations.
    - The registry-K DSR is reported only.
  - **Rule-only gates.** `gates.rule_only_promotion_gates` has no candidate or fold minimums. It counts regime years
    on pooled family trades, or on pre-registered multi-year blocks.
  - **Not done (ADR 0085 follow-ups):**
    - rewire autodiscovery's monthly 5 % Holm and its demo registration to this ledger;
    - retire EVAL-2 confirm reads as a promotion source;
    - record `rule_only_promotion_gates` in research_pass;
    - derive `rho_day` and `rho_instrument`, which are PRIORS today.
  - **Open item for the quant.** Condition (a)'s goal edge binds everywhere (4.2 R a trade at 5m, up to 420 R at
    1d), so the deciding power is about 1. `power_at_floor` is recorded beside it.
  - Review: quant-reviewer (rule encoding, n_eff floor, the open item); trading-safety-reviewer (the phase-gate pin);
    code-reviewer.
