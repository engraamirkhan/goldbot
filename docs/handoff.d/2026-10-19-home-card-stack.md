- Home is the status card stack (docs/ux/home-redesign-2026-10-19.md, Decision 2026-10-19; TRACEABILITY U26): new
  read-only routes `GET /api/positions/open` (N1), `/api/strategies/watch` (N2) and `/api/today` (goldbot/api/home_cards.py,
  5 s cache, viewer and above). Floating P&L is at the engine's mid price before costs; the engine publishes no box edges
  or MFI yet, so N2 says "unknown" rather than guess. Deferred: N3 chart lines (entry/stop/target on the chart), N4
  digest endpoint, axe scan (no axe dependency in web/), spread in money for position records (kept in points only).
