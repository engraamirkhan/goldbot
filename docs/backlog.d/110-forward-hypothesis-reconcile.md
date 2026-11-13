---
number: 110
title: Forward-to-hypothesis reconciliation (monthly, broker-keyed)
stage: 'Research · LEARN'
size: S
status: 'retired (triage 2026-11-13): superseded by the EVAL-2 forward e-processes (PR #358) and the profitability reads'
---
- **110. Forward-to-hypothesis reconciliation (monthly, broker-keyed).** Value: closes the learning loop from the system's own trades back to the hypothesis that produced them (docs/research/time-structure/learning-loop.md L-6), so a forward drift becomes a dated research decision rather than a quiet resize. It also tracks the research-vs-forward cost gap that the #88 and H-50 audits raised. Acceptance: (1) monthly, per strategy and broker, with evidence exclusions applied: forward net R and net-minus-gross against the walk-forward and confirmation expectations; (2) a breach (C6 outside [0.67, 1.5], or the forward mean below the confirmation lower 80% bound at n >= 100) writes a dated note fragment for the research director to answer; (3) nothing acts automatically (test).
