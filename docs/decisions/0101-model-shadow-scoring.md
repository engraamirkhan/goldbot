# 0101: Model shadow scoring off the tick, and a demo entry gate shipped off

Status: proposed (implementer, 2026-11-25; trading-safety and code review to follow)

## Context
On 2026-11-25 the owner asked for "proper model training and retraining with clear objectives". Today no model
touches any decision:
- nothing in `goldbot/engine` reads `self_retrain_models`;
- `meta_p` is NULL on every exploration candidate;
- health `model_influence` reads 0 %.

Lifecycle plan W1 (ml-researcher) asks for this: "Every candidate gets the scores of every frozen model ..., logged
with `model_version`, before the entry decision. No gating."

Two constraints conflict with "before the entry decision":
- The trainer's vector is `feature_snapshots.decision_features`, the nightly snapshot's function. It takes 0.1-0.5 s
  per (timeframe, decision): 66-210 ms in PR #438, 0.46 s median on the brain. The feature_snapshots design rule
  already keeps it off the engine's hot path ("never in the tick").
- The tick budget is under 5 ms per candidate. Scoring must never block or slow the tick.

A synchronous vector before the send would put up to half a second on the entry path. Train/serve parity rules out a
cheaper vector that differs from the trainer's.

## Decision
1. **Off the tick.** `exploration.candidate` queues each gold candidate once its row is written. The queue is bounded
   and never blocks. One daemon worker thread per exploration block then does three things:
   - it builds the vector once per (timeframe, decision), from the engine's 1m frame as it was at the record (the
     engine replaces that frame and never mutates it);
   - it scores the vector with every active or shadow model of the arm, using the trainer's `design` and
     `LogitModel.score`;
   - it writes the scores.

   A full queue drops the job. The drop is counted and logged, and the candidate stays unscored.
2. **Decision-time numbers, written once afterwards.** The vector reads only minutes visible at the decision. A model
   scores only candidates decided after its data cutoff. The score is therefore the number a synchronous scorer would
   have logged before the decision. It lands a moment after the record, in two places:
   - `exploration_model_scores`: append-only, one row per (candidate, model), with the model sha256, spec, feature
     version, vector sha256, score and `scored_ns`;
   - the candidate's `meta_p` / `model_version` / `feature_version`, written only while all three are NULL. The
     primary score is the arm's ACTIVE model's, else its newest.
3. **No gating.** The order path reads no score. A test runs one engine with models (ACTIVE and shadow, scores far
   below the cut) and one without. Orders, refusals, gate calls, positions, journal and records are identical apart
   from the three score columns.
4. **Provenance before use (fail closed per model).** Models load through `load_record_json` (ADR 0010), never a
   pickle. A row is refused, and the reason logged, unless all of these hold:
   - its spec is the trainer's `SPEC_VERSION` and its inputs are `INPUTS`;
   - its `model_sha256` equals the SHA-256 of the stored JSON;
   - its cutoff precedes its activation;
   - its training feature versions are recorded.

   A model never scores a vector whose feature version it did not train on. The trainer now writes `model_sha256` and
   `feature_versions` (aux migration 45).
5. **Switch.** `research.self_retrain.shadow_score` (on). `EngineConfig.shadow_score` is off by default for tests and
   tools.
6. **Demo entry gate, built and shipped off.** The owner asked on 2026-11-25 for "everything should be live
   immediately", then clarified: "before deploying, the models should be trained". The gate acts only when all of
   these hold:
   - the account's `model_gate` is set (accounts.yaml, default false, set on no account; refused at load on a live
     account) on a demo engine;
   - `risk.gate.is_demo` holds at the signal;
   - the arm is a gold strategy or learning arm's first unit (never a control, replay_only, owner-experiment, cost arm
     or pyramid add);
   - the model's spec is in `shadow_scoring.GATE_SPECS`. That set is **empty**: today's sr-v2 and H-101 models never
     gate. The first spec named there is the new pipeline's (objective.py, feature store, history labels), after its
     training report. Enabling the gate is then that code line plus the account flag.

   What the gate does, in `exploration._send`, before the RiskGate:
   - it keeps a candidate iff the model's stored rule keeps it (sr-v2: score >= tau; obj-v1: p_w >= 0.5 once such a
     model exists);
   - a rejected candidate whose salted draw is under 0.10 is still sent as an exploration sample (ADR 0098 section
     3). Its fill carries `evidence_class: explore_sample`, which `evidence_exclusions.NOT_EDGE_EVIDENCE` keeps out of
     the H-81 cost stop, the edge cap and an arm's n (labs);
   - every other rejected candidate is refused `model_gate:<version>`, recorded and replay-labelled;
   - propensity: every rejected candidate carries the 0.10 sample propensity (times the existing one).

   A gated signal waits in the queue at most 5 s after its bar close for its vector, which is built on the worker.
   With no vector, no model or any error, the gate fails open to the existing rule. Exits never pass here.

## Consequences
- `model_influence` reports the scored share. "Changed" stays 0, because nothing gates.
- Each frozen model gets a forward record from the day it is written: `exploration_model_scores` joined to the replay
  labels.
- Engine-vs-snapshot parity is now measurable as a join on `vector_sha256` with `exploration_feature_snapshots`. The
  engine's minutes can differ from the reconciled store, for example sampled minutes later replaced by broker bars.
- Models written before this change carry no `model_sha256` and are refused. The first scores come after the next
  weekly run (Sunday 15:00 UTC) writes models with provenance. There is no backfill: hashing today's text would
  certify it on first use and prove nothing.
- The worker shares the GIL with the tick. Each vector build (0.1-0.5 s, mostly numpy/pandas) runs only for
  candidates of arms with a served model, once per (timeframe, decision). The tick-side cost measured in tests is the
  queue put alone.
- When the gate is enabled, a gated demo entry can go out up to 5 s later than today while its vector is built. A
  sampled candidate that a later cap refuses is recorded with that cap's reason and mu 0, as any refused candidate.
- Trading-safety review covers the gate's order-path placement (drop-only, before `cost_probe._place`) before any
  account sets `model_gate`.
