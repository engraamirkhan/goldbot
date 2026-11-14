---
number: 134
title: 'Telegram trades: push real money and safety only, /open and /today on demand'
stage: Telegram
size: S
status: 'retired (sweep 2026-11-14, ADR 0066: no consumer): Telegram push policy matters only with real money; re-proposed in the live-pilot readiness review'
---
- **134. Telegram trades: push real money and safety only, /open and /today on demand.** Value: Ranked above 133 because it is S and removes push noise now. at 500+ fills a day only real money and safety are pushed; the rest is a digest or a pull (docs/design/trades-ux-2026-10-27.md §4). Acceptance: no message for any demo, exploration, control, probe or shadow fill (test); one silent push per real fill and close with £, R and time held; one silent push per arm retirement or read verdict; one silent off-pace notice per lab per day at most; `/open` and `/today` owner-only commands, at most 12 lines, numbers equal to the API's; no slugs, ids or logins (leak test). Lifecycle: run an edge (alerts without noise). Depends: 128, 131. TRACEABILITY: none yet; implementer adds a U row. Also: the end-of-day report per account carries the day's net £ after costs, costs £, fills vs target and caps used, from the same builder as `/today` (test: numbers equal); the halt, cap-hit, kill-switch, not-confirmed and proposal pushes are unchanged (regression test).
