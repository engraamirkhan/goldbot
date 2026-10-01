# goldbot web — React + TypeScript dashboard

Vite + React 18 + TypeScript. Data via TanStack Query against the Python FastAPI backend
(`goldbot/api`), typed client generated from the backend's OpenAPI schema (`npm run gen:api`),
live updates over WebSocket (`/ws`). Served as static files by the backend on the VPS; works from any browser.

Screens (see design doc, Infrastructure → Dashboard):
- Overview: equity per account and combined, today/week P&L vs caps, drawdown stage, supervisor state
- Approvals: pending trade proposals with p, EV, size, top SHAP features; approve/reject with reason code (mirrors Telegram)
- Positions & trades: open positions live, last 50 trades with outcome vs model expectation
- Agents: league table of specialist agents with lineage, fitness, shadow/live status
- Models: version, last retrain, PSI heatmap, reliability curve, CUSUM trace, allocator weights
- Feeds: tick age, spread, terminal connected, webhook latency, news feed with scores, calendar with blackouts, TradingView ideas
- Journal: post-trade reviews, weekly reports, chat with the assistant over the journal (read-only)

Scaffold (Phase 1): `npm create vite@latest web -- --template react-ts`
