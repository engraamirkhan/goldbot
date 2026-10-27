---
number: 112
rank: 45
title: Time-structure exploration arms F1A/F1C/G1A/G1C/WFRI
stage: 'Research · LEARN'
size: M
status: 'proposed (research director, time-structure synthesis, 2026-10-26) · accountable: implementer, strategy-researcher (arm text) · reviews: trading-safety-reviewer, quant-reviewer'
---
- **112. Time-structure exploration arms F1A/F1C/G1A/G1C/WFRI.** Value: forward demo learning, and the signal-time cost measurement for the two best time-structure candidates plus a forward-only Friday read (docs/research/prereg-2026-10-26-time-structure.md sections 2-4). Acceptance: (1) rule generators for F1A (15m Hurst-switched band break), F1C (same decision times, hashed side), G1A (1h Asia-hours prior-period level break, fixed 2R), G1C (random-distance level control) and WFRI (Thursday 19:00 to Friday 16:30 New York long), each bar-for-bar equal to its history form on 2010-2017 (parity test); (2) local clocks via zoneinfo, no UTC session window; (3) stop bands and £ caps of ADR 0043 enforced and skips logged with propensity; (4) arm ids registered only after the program-director decides the 2026Q4 override (69 -> 74), else from 2027-01-01; (5) the arms are excluded from the H-60 history matrix (flat prior; `arms.runnable_ids` today excludes only broker twins); (6) F1C and G1C are classed as controls everywhere a control is recognised (e.g. `telegram/labs.py` CONTROL_RULES keys on the rule name `random_bracket`); (7) swap per rollover enters the measured cost; (8) status moves from blocked to run only after (1)-(7) and the prereg is on main; no gate, cap or RiskGate path changed (trading-safety sign-off).
