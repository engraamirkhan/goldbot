# 0100: Holdout minutes as lookback warm-up for post-holdout decisions

Status: proposed (data engineer, 2026-11-25; quant ruling on PR #451)

## Context
The sealed holdout is 2025-10-01..2026-09-30 (quant R10; `goldbot/data/holdout_guard.py`). The live store keeps those
minutes, because the engine trades on them: its memory is 90,000 minutes, about 65 trading days. A feature snapshot of
a live candidate is the engine's own frame at the decision (`research/feature_snapshots.py`). So until the engine's
window clears the holdout, about 2027-01-08, that frame reads holdout minutes as lookback.

PR #451 first cut those minutes out of the snapshot history. Training rows would then have carried a shorter history
than the engine served (train/serve skew), and the rows would have been a mix of cut and uncut. The quant ruled to keep
the stored snapshots and revert the cut.

## Decision
A holdout minute may be read only as lookback input to a feature at a decision on/after 2026-10-01 whose label lies
wholly after it. Never as a decision, label, outcome, or in any selection or threshold statistic. Self-retrain models
are forward-only and are never scored on the holdout. Any model trained on rows decided before 2027-01-08 that is ever
scored on the holdout excludes 2026-06-23..2026-09-30 from that score.

In code:
- `feature_snapshots.snapshot_pending` refuses a decision inside the holdout (`holdout_guard.refuse`; recorded as
  `error`, detail `HoldoutSealed`). It reads the holdout only as the engine's lookback.
- `holdout_guard.LIVE_EXEMPT` lists `goldbot.research.feature_snapshots` with this ADR as the reason.
- The holdout window itself is unchanged.

## Consequences
- Snapshots stay equal to the engine's frame on the same minutes (test
  `test_a_snapshot_decided_inside_the_holdout_is_refused_and_one_after_it_reads_the_engines_full_memory`).
- The overlap is one-off and ends about 2027-01-08. It is not an owner decision; the PD reports it on #34 as
  information.
- A later holdout score of a model trained before 2027-01-08 has a shorter scoring window
  (2025-10-01..2026-06-22).
