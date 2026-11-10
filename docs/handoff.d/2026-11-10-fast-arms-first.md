- 2026-11-10 (branch fix/fast-arms-first; R118; vantage-demo refusals diagnosis 2026-11-10 fixes 2-4): 1m/5m
  exploration arms now go ahead of the 15m+ close work. Each book's exploration pass runs its 1m/5m part (reads,
  the minute's decisions, a send) before its learning read and 15m+ step; a multi-book pass runs every book's 1m/5m
  part first and the 15m+ parts after (`books._slow_parts`), starting none while a 1m/5m signal is queued or a minute
  is due or inside its 1.5 s grace (max wait SLOW_WAIT_S 20 s; past the 5 s budget the rest are carried, first next
  pass). Fake-clock test, 11 books x 3 s step at a :15 close: S0 ages at send 6.2-38.2 s (9-10 of 11 stale) before,
  3.0-6.9 s (0 stale) after; last 15m+ step starts at ~41 s (60 s limit). Only the order changes: refusals, RiskGate,
  budget reads, PER_PASS per book and journal rows unchanged. The deferral warning shows exploration sub-phases
  `[reads, learning, step, minutes, entry, budget]` and the carried count. Not changed: gold's own bar close still
  runs before the other books' 1m parts; per-book step CPU is not reduced (next: optimise the top sub-phase).
