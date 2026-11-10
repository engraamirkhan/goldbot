- 2026-11-09 Pass timing and stale order (branch fix/pass-timing-stale-order, R111; SRE volume-drop diagnosis cause 5,
  fixes 3 and 4): a stale exploration signal of an arm refused for good (`arm_retired`, `thin_cell_complete`) is now
  journalled `exploration_refused:<reason>` (with `age_s`) and recorded blocked with that reason, so
  `exploration_stale` counts only entries staleness cost (expect icm-probe's ~885 a day to fall to ~30; vantage's to
  ~0). Such a 1m/5m arm is `dormant` (journalled `exploration_arm_dormant` once): no minute frame or rule work and its
  book is no longer run early as `due`; its exits run as before. The multi-book deferral warning now reads
  `quotes X s; exits X s; phases: bridge, bars, zones, exploration (all books); slowest: <book> X s (bridge, bars,
  zones, exploration)`. Gold's key-zones build starts after the pass's entries (multi-book: after every book, no
  decision due, at most `books.ZONES_WAIT_S` 60 s later), on the inputs parked at its 15m close. Next: run
  `scripts/bench_account_pass.py` on the brain at a 15m close and read the new warning lines before choosing the next
  cut.
