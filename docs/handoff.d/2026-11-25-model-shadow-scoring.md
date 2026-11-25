- **Model shadow scoring (lifecycle W1, ADR 0101).** Every gold exploration candidate now gets the score of every
  active or shadow self-retrain model of its arm. Nothing gates on it.
  - **Where it runs.** `goldbot/engine/shadow_scoring.py`. `exploration.candidate` queues each candidate after its
    row is written (bounded queue, under 5 ms on the tick). One worker thread builds the trainer's vector
    (`feature_snapshots.decision_features`) once per (timeframe, decision) and scores it with `self_retrain.design`
    and `LogitModel.score`.
  - **What it writes.** One append-only row per (candidate, model) in `exploration_model_scores` (aux migration 45),
    with the model's sha256, spec, feature version, the vector's sha256 and the score. The candidate's `meta_p`,
    `model_version` and `feature_version` are written only while all three are still NULL. They carry the ACTIVE
    model's score, else the newest model's.
  - **Provenance check.** A model is served only when its spec and inputs are the trainer's, its stored
    `model_sha256` matches its JSON, its cutoff precedes its activation and its training feature versions are
    recorded. It scores only candidates decided after its cutoff. The weekly job now writes `model_sha256` and
    `feature_versions`. Models trained earlier are refused, so the first scores come after the next Sunday run.
  - **What it changes.** No decision changes: a test runs one engine with models and one without and gets the same
    orders, gate calls and records. Health `model_influence` now reports the scored share; "changed" stays 0.
  - **Switch and state.** `research.self_retrain.shadow_score` (on). The engine state publishes `shadow_scoring`
    counts and the last failure.
  - **Demo entry gate, shipped off.** A gating model may refuse a demo strategy or learning arm's entry before the
    RiskGate. A randomised 10 % of the rejected candidates are still sent as `explore_sample` (ADR 0098 section 3);
    those fills never feed a cost stop, the edge cap or an arm's n. The gate needs three things, none set today:
    - accounts.yaml `model_gate` (false everywhere; refused on a live account);
    - `shadow_scoring.GATE_SPECS` naming the model's spec (empty, so today's sr-v2 and H-101 models never gate);
    - `risk.gate.is_demo` at the signal.

    A gated signal waits up to 5 s for its vector; any failure falls back to the existing rule.
