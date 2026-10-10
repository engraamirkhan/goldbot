# 0002 CPCV and PBO can only veto a config that passed the gates

## Context
Combinatorial purged CV (row M16, `goldbot/research/cpcv.py`) re-runs a registered trial's configuration on 6 time
groups (15 purged splits, 5 backtest paths) and computes the probability of backtest overfitting (PBO) across several
configurations. It is evidence attached to a trial, not a trial: it takes no budget slot and does not raise the
deflated-Sharpe count. The quant review found no rule for how to read it. Without one, CPCV becomes a second,
uncharged look at the data: a config that failed the gates could be "rescued" because its paths look good, or CPCV
could pick the best of several configs. Either way it would be a selection the trial count does not charge for.

The PBO is also optimistic. The configs a passing trial was chosen among are every registered trial of its family on
that timeframe, screened ones included, but only walk-forward trials can be re-run (a screen fitted no model), and the
quarterly job compares at most 8. Group 0 is never traded on any path (no earlier group to calibrate on), so its
column is zero for every config.

## Decision
- CPCV and PBO can only VETO. A config that passed the gates is flagged **fragile** when PBO > 0.5 or more than half
  of its paths have negative mean R (`cpcv.verdict`, stored in the evidence and shown in the report). They never
  rescue a config that failed the gates and never pick a winner among configs.
- `scripts/research_pass.py --cpcv` refuses trials that failed the gates unless `--diagnostic`. A diagnostic run is
  labelled "diagnostic, not evidence" and attaches nothing to the failed trials (gate-passing trials in the same run
  still get their evidence: their PBO is then computed against a larger part of their selection set).
- The quarterly job `cpcv_quarterly` only evaluates gate-passing trials and skips a trial whose recorded feature
  version differs from the one the store's bars build now.
- PBO is reported as a **lower bound**, with the size of the selection set (`cpcv.selection_set`: every registered
  trial of the family on that timeframe, screened ones included; pre-registrations and holdout scorings are not
  alternatives) and how many of those were compared. Group 0 is left out of PBO; the 5 scored groups are split 2
  against 3 in both directions (20 splits).
- The evidence sidecar (`<registry>.evidence.jsonl`) is local to the host that wrote it: `registry_sync` and
  research.yml do not carry it. Evidence is advisory, so losing it costs a re-run, not a wrong decision.

## Consequences
- A fragile verdict is recorded but does not yet block anything: wiring it into `TrialRegistry.passed_gates`
  (so a fragile config cannot go from shadow to live) changes a promotion gate, which is the owner's decision.
  Recommendation: wire it in once the first quarterly run has produced verdicts to look at.
- A failed config stays failed whatever its paths look like; the only way forward for it is a new, charged trial.
- A low PBO is weak comfort (a lower bound); a high one is a strong signal. The rule reads it in that direction only.
- If the sidecar later feeds a gate, it must be synced the way the registry is (union by trial and kind).
