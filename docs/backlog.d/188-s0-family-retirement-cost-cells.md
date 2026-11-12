---
number: 188
rank: 71
title: Decide whether retiring S0 should stop the S0-<instrument> cost controls
stage: 'research / engine (follow-up of R133, 2026-11-12)'
size: S
status: 'proposed · accountable: research-director · reviews: quant-reviewer'
---
- **188. Decide whether retiring S0 should stop the S0-<instrument> cost controls.** Value: R133 makes a retired gold family stop its per-instrument rows (`exploration.family_retirement`). S0 is planned to stop with S1A/S1B (prereg-amendment-2026-11-06-control-thinning.md point 4); with R133 that retirement would also stop every S0-<key> control, which are the main feed of the per-instrument cost cells (`thin_cell_complete`, 30 fills). Acceptance: the research director rules whether S0-<key> should stop with S0 or keep running to their cell; if they keep running, a test-first change exempts them by an explicit row field (not by id), else the S0 retirement row records the cascade in its rule.
