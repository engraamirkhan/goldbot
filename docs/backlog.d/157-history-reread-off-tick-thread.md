---
number: 157
rank: 62
title: History re-read off the tick thread
stage: 'engine (trading-safety review of R123, item 4, 2026-11-10)'
size: S
status: 'proposed · accountable: implementer · reviews: trading-safety-reviewer'
---
- **157. History re-read off the tick thread.** Value: while `history_read` or `history_short` refuses entries, `history.reread` reads up to the engine's whole window from the store every 30 min on the tick thread, after the close's exits. A slow disk or a large store stalls the next ticks' stop checks for that long. R123 logs each re-read's duration and warns over REREAD_WARN_S (2 s); it does not bound it. Acceptance, tests first: the re-read runs on a thread of its own (as `HistoryFill`), its result is merged on the tick thread at a bar close (as `absorb`), and a read that has not finished never delays a tick; or the read is time-boxed and a read past the box keeps the refusal. The refusal holds until a merge has succeeded, as today.
