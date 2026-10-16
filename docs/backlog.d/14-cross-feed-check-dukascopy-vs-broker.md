---
number: 14
title: Cross-feed check (Dukascopy vs broker)
stage: Idea generation (honesty) · FIND
size: M
status: 'done (PR #63, main 5f7da74; 49cc051)'
---

### 14. Cross-feed check
Value: stops a broker-feed artefact being "found" as an edge once broker data accumulates (FIND, honesty).
Acceptance: a signal is dropped unless its gross edge survives on both Dukascopy and broker bars over the overlap
(F10, D23). The spike rule checks the other feed before flagging (D20). Tests use synthetic diverging feeds.

Sweep 2026-10-16 (product owner): Done (PR #63, main 5f7da74; 49cc051). Acceptance record: merged on green CI into main; checked against git and the named tests exist; the criteria were not re-run one by one in this sweep.
