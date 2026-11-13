---
number: 106
title: Confirmation read type on 2018-01..2025-09 (one read per config, K_conf)
stage: 'Research · LEARN'
size: M
status: 'retired (triage 2026-11-13): superseded by EVAL-2 L3 confirm (ADR 0076/0077, PR #355)'
---
- **106. Confirmation read type on 2018-01..2025-09 (one read per config, K_conf).** Value: stage 2 of the learning loop (docs/research/time-structure/learning-loop.md L-2): one pre-registered read per configuration on the confirmation half 2018-01-01..2025-09-30, which no time-structure slice reads before it. Acceptance: (1) the window is fixed in code; (2) a second read of the same config hash is refused (as `holdout_scored`); (3) the read needs a pre-registered row whose `rule_ref` names its reading rule; (4) K_conf (confirmation reads ever made) is reported beside the registry K; (5) it is recorded as a registry trial (1 slot), with tests for each point.
