---
number: 267
rank: 92
title: Snapshot writer stores the count of unstamped minutes in the last 24 h
stage: 'Research data (learning loop)'
size: S
status: proposed
---
- **267. Snapshot writer stores the count of unstamped minutes in the last 24 h.**
  - **Value.** Quant S5 on PR #434 asked for this. Today a nominal-pit snapshot only says that some minute in its
    90,000-minute window lacks a write stamp. Release minutes never get one. So the self-retrain re-reads the store
    every Sunday (`goldbot/research/snapshot_audit.py`) to find the snapshots with an unstamped minute in
    [decision - 24 h, decision].
  - **The change.** `research/feature_snapshots.py` stores that count per snapshot at write time, in a new nullable
    column of `exploration_feature_snapshots` (additive migration).
  - **Payoff.** The trainer can then admit a nominal snapshot from the row alone.
  - **Acceptance:**
    1. The column is NULL for snapshots written before the change.
    2. Its value equals `snapshot_audit.audit` on the same minutes, checked in a parity test over 12 cuts.
    3. The self-retrain uses the column where it is set and keeps the audit only for NULL rows.
