---
number: 107
rank: 40
title: Local-clock session windows for exploration arms
stage: 'Execution · LEARN'
size: S
status: 'proposed (research director, time-structure learning loop, 2026-10-26) · accountable: implementer · reviews: trading-safety-reviewer'
---
- **107. Local-clock session windows for exploration arms.** Value: exploration-arm session windows follow the exchanges' local clocks. Measured on data-v1: the New York activity peak follows New York's clock in the UK/US DST gap weeks (time-structure README 6.9), so a UTC window mis-times the NY session about 3 weeks a year. `opening_range_break` already uses local clocks; `arm_rules.in_window` is UTC only (docs/research/time-structure/learning-loop.md L-3). Acceptance: (1) `in_window` accepts windows on Europe/London, America/New_York and Asia/Tokyo clocks; (2) a test across the 2026 gap weeks; (3) existing UTC windows and every frozen arm's config hash are unchanged (test).
