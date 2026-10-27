---
number: 133
rank: 8.7
title: 'Trade detail with a chart: entry, stop, target and exit on the bars'
stage: API · UI
size: M
status: 'ready after 129 · accountable: implementer · design: ui-ux-designer · reviews: code-reviewer · due 2026-11-16'
---
- **133. Trade detail with a chart: entry, stop, target and exit on the bars.** Value: one trade, fully explained: levels on the bars, why it was taken, what closed it, what it cost (docs/design/trades-ux-2026-10-27.md §3.3). Fields: `GET /api/trades/{id}/bars` (OHLC of the trade's timeframe, 1m for 1m/5m arms, 30 bars before entry to 5 after exit). Acceptance: inline SVG chart, no chart dependency; levels labelled with price and word; fixed height (no shift while loading); missing bars state; costs breakdown; evidence state; Details disclosure holds ids; sheet on phone, side panel on desktop. Lifecycle: run an edge (explain one trade) and find an edge (see what a rule saw). Depends: 129. TRACEABILITY: none yet; implementer adds a U row. Tests: a seeded closed trade renders entry, stop (initial and last), target and exit lines with price and word labels and entry and exit markers on the right bars; the why sentence and the exit reason appear in words; the costs breakdown sums to gross − net; e2e on 360 px opens the detail from List and closes it with back; a trade without bars shows the missing-bars state, not an empty chart.
