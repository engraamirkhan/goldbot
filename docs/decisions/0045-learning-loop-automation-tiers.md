# 0045: Learning loop: what runs automatically, what is reviewed, what never happens daily

Status: accepted (research-director decision, 2026-10-26)

## Context
ADR 0043 runs many exploration arms; `docs/research/prereg-2026-10-25-high-volume.md` pre-registers them (H-60, H-61,
H-62) with stops and a trial count K. Daily looks at per-arm results are optional stopping: they inflate K and bias
any later promotion claim. The blinding rules in `docs/ux/daily-learning-report.md` already hide per-arm results.
We need one rule for what the loop may do by itself each day, what a human/agent reviews weekly, and what never
happens on a daily cadence.

## Decision
Three tiers.

**1. AUTOMATIC daily (job `daily_review`, 06:15 UTC).**
- Retire an arm at its pre-registered H-61 stop, the day it breaches.
- Refit a (broker, session) cost cell at >=200 fills; within a day a refit may only be more conservative.
- Open or comment GitHub `learning-ops` issues for incidents, or when volume is <50% of plan two days running.
- The nightly learner (weights, labels, retrain) runs as already decided.

**2. REVIEWED weekly.**
- At most 4 variant proposals, each pre-registered, then quant and trading-safety review, then a PR, all before any
  trade of that variant.
- Champion model changes follow the H-62 spec.

**3. NEVER daily.**
- Per-arm R, rank or sign shown or acted on anywhere.
- New arms.
- Threshold, prior, cap or bandit changes.
Reason: daily looks are optional stopping and inflate K.

The daily review is blinded; the pre-registered stops are the only daily results output.

## Consequences
- K is protected: only frozen stops act on results daily; everything else waits for the weekly reviewed cycle.
- Cost cells can tighten daily but never loosen, so the loop cannot weaken RiskGate-side protections.
- Arms cannot be added or tuned in response to daily noise; this slows adaptation by design.
- `daily_review` must not emit per-arm R/rank/sign (see `docs/ux/daily-learning-report.md`); a test should enforce it.
- Related: ADR 0043, prereg-2026-10-25-high-volume.md.
