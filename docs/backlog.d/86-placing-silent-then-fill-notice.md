---
number: 86
title: A fill after a silent-engine window still sends one "opened" notice
stage: Execution and reconciliation (entry outcome truth) · PROTECT
size: S
status: 'done (triage 2026-11-13): PR #93 (a31a5b15, d1d8ab5c)'
---
- **86. A fill after a silent-engine window still sends one "opened" notice.** Value: if the engine is silent ≥ PLACING_SILENT_S mid-send and the send then returns a plain fill, the card stays NOT CONFIRMED and no "opened" notice goes out. Acceptance: when placing lasted ≥ PLACING_SILENT_S and the order filled, exactly one "opened" notice is sent and the card updated; test.
