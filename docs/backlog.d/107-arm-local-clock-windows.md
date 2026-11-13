---
number: 107
title: Local-clock session windows for exploration arms
stage: 'Execution · LEARN'
size: S
status: 'done (triage 2026-11-13): PR #203 (window_local, 8d777955)'
---
- **107. Local-clock session windows for exploration arms.** Value: exploration-arm session windows follow the exchanges' local clocks. Measured on data-v1: the New York activity peak follows New York's clock in the UK/US DST gap weeks (time-structure README 6.9), so a UTC window mis-times the NY session about 3 weeks a year. `opening_range_break` already uses local clocks; `arm_rules.in_window` is UTC only (docs/research/time-structure/learning-loop.md L-3). Acceptance: (1) `in_window` accepts windows on Europe/London, America/New_York and Asia/Tokyo clocks; (2) a test across the 2026 gap weeks; (3) existing UTC windows and every frozen arm's config hash are unchanged (test).
