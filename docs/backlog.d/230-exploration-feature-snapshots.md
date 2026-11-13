---
number: 230
rank: 78
title: Point-in-time feature snapshots on every gold exploration candidate
stage: 'research data (ML review 2026-11-13 improvement B, lane ML1)'
size: M
status: 'done 2026-11-13 (branch feat/ml-cleanup-snapshots; R143; ADR 0080) · accountable: implementer · reviews: quant-reviewer, data-engineer, code-reviewer'
---
- **230. Point-in-time feature snapshots on every gold exploration candidate.** Value: this turns the demo log into a dataset that matches our broker, our costs and the policy that chose the trades, so later models can learn from our own fills.

  Acceptance:
  - Each candidate gets the engine's default feature row (DEFAULT_FEATURE_NAMES plus h1/h4/d1 context) at its decision.
  - The row is built from the account's stored broker minutes visible by the decision.
  - Storage is aux.db migration 32: content-addressed vectors plus one row per candidate, append-only.
  - The job runs nightly after the learner and is bounded (600 decision bars, newest first). It is never in the engine tick.
  - Tests (`tests/test_feature_snapshots.py`):
    - Recomputing from bars cut at each decision gives 0 mismatches.
    - Shuffled-label AUC is 0.50 +- 0.02, and an informative label reads above 0.9.
    - No engine module names the tables.

  - Quant review of a8c3a6d7 (fixed in the follow-up commit, tests in `tests/test_feature_snapshots_pit.py`):
    - Batches are shared fairly between accounts. A stuck account (no bars, or not configured) gets a retry date and
      never starves a healthy one.
    - Point in time is by write time: the engine's minute writer and the broker-M1 reconcile stamp `available_utc`.
      A minute written more than 2 minutes after its close counts from its write time.
    - Feeds are resolved per decision. Late, revised and quarantined minutes are tested.
    - A snapshot matches `engine.frames.frame` exactly, at 15m and 1h.
    - Rows with no write time are marked `pit = nominal`.
    - A too-short history is retried for 3 nights, with the release minutes the engine warm-starts on as the
      last-resort feed.
    - Equal shares never spend more of a style than the bandit would. Cold arms keep the cold-start floor.
    - Coverage counts gold candidates only.

  Follow-ups (not done):
  - Add the coverage line to the daily review / morning report.
  - Snapshots for non-gold candidates.
