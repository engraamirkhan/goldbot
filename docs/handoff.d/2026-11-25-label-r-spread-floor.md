- **Exploration labels: a stop inside the spread is voided, never labelled (2026-11-25).**
  - **Finding.** On the brain, 1,039 `exploration_obs` rows have |label_sim_r| > 20 R. 1,038 of them are non-gold
    candidates (FX, rates, energy, ags, metals; session days 2026-11-02..03). They were replayed on XAUUSD bars with
    gold's per-oz costs before the 2026-11-04 instrument guard, and all are already void in `exploration_label_void`.
    The remaining row is a real gold tail: RBC15, +20.9 R on a time exit. It is kept, as the quant review requires.
    No non-gold label has been written since 2026-11-04. The per-class means (fx -12.7, bonds -9.9, agri -16 R) come
    from reads that skip the void table. `read_obs`, the learner and `self_retrain` already drop these rows.
  - **Change.** r1 (`label_candidates`) and r2 (`label_candidates_v2`) never label a candidate whose stop is below
    `min_label_stop`. The floor is a frozen research constant, `MIN_LABEL_STOP` (gold $0.45), so retuning the
    RiskGate's spread ceiling does not change it.
    - Such a candidate gets one `exploration_label_void` row per replay version (reason `stop_inside_spread`) and
      one warning.
    - `arm_lifecycle.lifecycle_obs` does not count it as unlabelled (VOID_UNLABELLED).
    - `self_retrain` counts it in the run detail (`stop_inside_spread`), as the prereg amendment requires.
    - Brain check on 2026-11-25: there are 0 gold rows with a label, no void row and a stop below $0.45.
  - **Rule for ad-hoc reads.** Read labels through `exploration_data.read_obs`. A raw SQL read of
    `exploration_obs.label_sim_r` must anti-join `exploration_label_void`.
