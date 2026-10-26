---
number: 106
rank: 39
title: Confirmation read type on 2018-01..2025-09 (one read per config, K_conf)
stage: 'Research · LEARN'
size: M
status: 'proposed (research director, time-structure learning loop, 2026-10-26) · accountable: implementer · reviews: quant-reviewer, evaluator'
---
- **106. Confirmation read type on 2018-01..2025-09 (one read per config, K_conf).** Value: stage 2 of the learning loop (docs/research/time-structure/learning-loop.md L-2): one pre-registered read per configuration on the confirmation half 2018-01-01..2025-09-30, which no time-structure slice reads before it. Acceptance: (1) the window is fixed in code; (2) a second read of the same config hash is refused (as `holdout_scored`); (3) the read needs a pre-registered row whose `rule_ref` names its reading rule; (4) K_conf (confirmation reads ever made) is reported beside the registry K; (5) it is recorded as a registry trial (1 slot), with tests for each point.
