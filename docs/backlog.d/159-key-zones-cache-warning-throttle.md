---
number: 159
rank: 64
title: Throttle the key zones cache-read warning
stage: 'engine, display (code review of R123, 2026-11-10)'
size: XS
status: 'in review (implemented 2026-11-12, branch fix/history-reread-off-tick, R127) · accountable: implementer · reviews: code-reviewer'
---
- **159. Throttle the key zones cache-read warning.** Value: since R123 a failed key-zones cache read (`KeyZoneWorker.refresh`) is retried at every build, once per 15m close. A store that stays unreadable logs the same warning about 96 times a day per engine. Acceptance, tests first: the first failure of each exception type is logged with its traceback, later ones at most once an hour (as `_report` does for build errors), and a read that works again logs once that the cache is back. The retry itself is unchanged.
