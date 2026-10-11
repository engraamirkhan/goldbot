---
number: 14
rank: 17
title: Cross-feed check (Dukascopy vs broker)
stage: Idea generation (honesty) · FIND
size: M
status: done (2026-10-10; promotion gate once 90 days of broker M1 exist)
---

### 14. Cross-feed check
Value: stops a broker-feed artefact being "found" as an edge once broker data accumulates (FIND, honesty).
Acceptance: a signal is dropped unless its gross edge survives on both Dukascopy and broker bars over the overlap
(F10, D23). The spike rule checks the other feed before flagging (D20). Tests use synthetic diverging feeds.
