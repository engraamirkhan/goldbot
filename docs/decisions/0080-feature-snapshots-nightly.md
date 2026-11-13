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

**Bounds and fairness:**
- At most `research.exploration.snapshot_max_decisions` (600) decision bars a night.
- The batch is shared round-robin between accounts, newest first within each, so one account's backlog cannot starve
  another's.
- An account with no bars, or one missing from accounts.yaml, gets a retry date one day out and is skipped until then.
- A bar with too little history is retried on 3 nights, then recorded as `insufficient_bars`.

**Storage (aux.db migration 32):**
- `exploration_feature_vectors` holds each distinct vector once, keyed by content.
- `exploration_feature_snapshots` holds one row per candidate, with its `pit` mark.
- Both tables are append-only.
- Two small bookkeeping tables, updated in place, hold the retries and the account retry dates.
- Nothing in `goldbot/engine` reads any of them, and a test enforces that.
- Gapless numbering keeps this at 30 on the branch. It becomes 32 once the review lanes' 30 and 31 are on main.

**Point in time is by write time** (quant review of `a8c3a6d7`):
- From 2026-11-13 the engine's minute writer and the broker-M1 reconcile stamp each stored minute with
  `available_utc`, its write time.
- A minute written more than 2 minutes after its close (a back-fill or re-pull) counts as known only from its write
  time. One the engine stored as it built it counts as known at its close, which is how the engine knew it.
- Feeds are resolved per decision. A minute revised later is read from the next feed that had it at the time, or not
  at all.
- Quarantined minutes are read by no snapshot.
- Where no broker feed has a minute, the release minutes the engine warm-starts on fill it.
- Tests hold each snapshot equal to `engine.frames.frame` on the same minutes, at 15m and 1h.

## Consequences
- The engine tick, the order path and every decision are unchanged. The engine-side change is one stored column in
  the history writer's background thread; it is not on the order path, and the engine's own reads ignore it. It is
  listed for trading-safety review all the same.
- **The guarantee, precisely:** no snapshot reads a minute whose stored write time is after its decision.
  - Minutes with no stored write time are taken at their nominal close, which can be earlier than when they really
    reached the store. That covers everything written before 2026-11-13 and every release minute.
  - A snapshot that read any such minute is marked `pit = nominal`, with the count of those minutes.
  - Strict point-in-time research reads `pit = written` rows only (`read_snapshots(pit="written")`).
  - Because the engine keeps 90,000 minutes in memory, about 62 trading days pass before snapshots can be `written`.
- A minute quarantined after its decision is left out, although the engine may have read it. That is a removal, not a
  value from the future.
- The vector is the one the engine would have built from the stored minutes. A minute the engine sampled live and the
  store later replaced with the broker's M1 shows the version known at the decision.
- Coverage arrives with a delay of up to one night. Until the backlog clears, about 600 bars a night. The nightly job
  reports counts for gold candidates: candidates, snapshots, with features, `written_pit`.
- Only gold candidates are snapshotted. The non-gold arms retire on 2026-11-14 (ruling of 2026-11-13).
- The brain spends a few minutes of CPU a night.
- **What would change this decision:** a model that has to score candidates live (H-62's ranked branch). That needs
  the vector in the tick, with its own latency budget and trading-safety review.
