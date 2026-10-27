---
number: 133
rank: 53
title: 'Trade detail with a chart: entry, stop, target and exit on the bars'
stage: API · UI
size: M
status: 'proposed · accountable: implementer · design: ui-ux-designer · reviews: code-reviewer'
---
- **133. Trade detail with a chart: entry, stop, target and exit on the bars.** Value: one trade, fully explained: levels on the bars, why it was taken, what closed it, what it cost (docs/design/trades-ux-2026-10-27.md §3.3). Fields: `GET /api/trades/{id}/bars` (OHLC of the trade's timeframe, 1m for 1m/5m arms, 30 bars before entry to 5 after exit). Acceptance: inline SVG chart, no chart dependency; levels labelled with price and word; fixed height (no shift while loading); missing bars state; costs breakdown; evidence state; Details disclosure holds ids; sheet on phone, side panel on desktop.
