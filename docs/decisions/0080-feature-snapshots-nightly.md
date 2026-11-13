# 0080: Exploration feature snapshots are computed nightly from point-in-time bars, not in the engine tick

Status: proposed (principal software engineer, lane ML1, 2026-11-13)

## Context
The ML review of 2026-11-13 (`docs/research/ml-review-2026-11-13.md`, improvement B) found that none of the 19,990
logged exploration candidates carries the features the engine saw. Without them, no later model can learn from our own
broker's fills. The lane brief asked for the snapshot "at candidate time", without slowing or blocking the engine tick.

There were two options:
1. **Build the frame in the engine at each candidate.** Every exploration host is a book without agents, and today
   such a book never builds features (`engine/frames.py`). Measured on 2026-11-04 on icm-probe, features were about
   two thirds of a 15m close's pass, and the 5 s pass budget was already being exceeded. Building them for every
   candidate would bring that cost back onto the hosts that place the demo orders. A background thread would compete
   for the same CPU, and it would put new code on the order path.
2. **Rebuild each candidate's frame nightly from the account's stored broker minutes, cut at the decision.** It uses
   the same rule as the engine: the last 90,000 minutes, the last 800 decision bars visible at the close, and the
   h1/h4/d1 context bars visible then.

## Decision
Option 2. The work is done by `goldbot/research/feature_snapshots.py`, which runs inside `nightly_costs` after the
learner.
- It is bounded by `research.exploration.snapshot_max_decisions` (600 decision bars a night) and works newest first.
- The results go to aux.db migration 30: `exploration_feature_vectors` holds each distinct vector once, keyed by
  content; `exploration_feature_snapshots` holds one row per candidate.
- Both tables are append-only. Every outcome is written once.
- Nothing in `goldbot/engine` reads either table, and a test enforces that.

## Consequences
- The engine tick, the order path and every decision are unchanged. No trading-safety review of the engine is needed.
- A snapshot is the vector the engine *would* have computed from the stored bars. It can differ from what the engine
  held in memory:
  - A minute the engine sampled live but the store later replaced with the broker's own M1 bar.
  - A minute corrected after the decision. Such a minute is left out, never read early.

  Neither difference can leak the future. The SQLite row invariant `available_utc_max_ns <= decision_ns` and the
  recompute test (0 mismatches over 12 cuts) guard this.
- Coverage arrives with a delay of up to one night. Until the backlog clears it runs at about 600 bars a night. The
  nightly job reports coverage as counts.
- Only gold candidates are snapshotted. Non-gold arms retired on 2026-11-14.
- The brain spends a few minutes of CPU a night.
- What would change this decision: a model that has to score candidates live (H-62's ranked branch). That needs the
  vector in the tick, and it would come with its own latency budget and trading-safety review.
