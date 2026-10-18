# Dashboard v2: audit, information architecture, screens, build slices (2026-10-18)

Status: proposed, for architect review. Tokens and components: `docs/ux/design-system.md`.
API contracts unchanged except the SRE lane's `GET /api/chart/mt5?tf=15|60|240|1D` (PNG + timestamp).

## 1. Audit of the current dashboard (evidence: ~/tmp/ui-audit/before/index.html, 312 shots, 78 screen states)
1. **12 flat tabs** (Overview, Approvals, Progress, Limits, Chart, News, Agents, Health, Research, Feeds, Users,
   Account); on a 390 px phone only 5 fit, the rest scroll off sideways. No grouping, no deep links.
2. **Home does not answer the 3 questions.** Overview stacks 9 blocks (entry status, progress line, 3 tiles, halt
   form, auto-mode card with code input, balance card, limits chip, TradingView chip, research line, a 9-column
   table). Today's P&L is the 6th block; a waiting trade is not shown at all (only a count tile "Pending approvals 2").
3. **Danger next to the fold.** "Halt new entries" (red) and "Enable auto mode" with a code field sit on Home between
   status tiles; owner-only, rare actions with the most weight on the page.
4. **Duplication.** Account shown 3 times on Home (entry row, balance card, table); loss budget 3 times (balance card,
   limits chip, Approvals strip). Safety state differs between Home (tiles) and Approvals (7 chips).
5. **Inconsistent visuals.** 9 card styles (`acct`, `card`, `pcard`, `mode-card`, `entry-row`, `hyp-card`,
   `limit-card`, `tv-card`, `chip`), 5 badge styles, left borders 6 px in gold/green/blue/amber; gold accent with
   white text fails AA (3.0:1); type sizes 10-22 px with no scale; uppercase micro-labels mixed with sentence case.
6. **Jargon/internal codes on screen**: `icm-demo`, `session_open-g0-abc`, `raw · demo`, "Supervisor ok",
   "Drift halt off", "p 0.62", "+0.31 R", "size_down", "Welch p", "PSI", "CUSUM", "ECE", "pre-swap".
7. **Approval card** is good in content but long: risk sentence, loss budget box, levels, metrics, why, footer with
   agent id; decided cards repeat the full body (4 decided cards = 2 screens of scroll on a phone).
8. **Empty/error states**: plain grey text or red `err` lines ("Could not load ... (Internal Server Error)"), often
   several per page (each card fails separately), no retry; loading is mixed ("Loading…" text vs skeletons).
9. **Tables on phone** (Overview, Agents, News, Health, Users) overflow sideways; numbers not tabular in several.
10. **Dark mode**: surfaces barely differ from background; green "Approve" and gold bars dominate.

## 2. Information architecture (7 destinations)
| Nav | Holds (current screens) | Who |
|---|---|---|
| **Home** | status, waiting trade(s), today's P&L vs limits, open positions summary | all |
| **Trades** | segmented: Waiting (Approvals) · Recent (decided 10 min + your trades) · Results (Progress results) | all |
| **Chart** | TradingView chart + MT5 chart side by side | all |
| **Limits** | Limits (unchanged content, new cards) | all |
| **Research** | segmented: Today · Road to live (Progress gates, stop rule) · Strategies (Agents league) · Trials (Research) | all |
| **Feeds** | segmented: Data feeds (Feeds + TradingView card) · News & calendar (News) · System health (Health) | all |
| **Settings** | Entry mode (auto mode), Halt / re-arm, Your sign-in (Account), Users (owner), Recovery codes | role-gated |
Phone bottom bar: Home, Trades, Chart, Limits, More (sheet with Research, Feeds, Settings). Desktop: left rail, all 7.
Hash routes (`#/trades/waiting`) so Telegram cards can link straight to a trade. A waiting trade puts a count badge
on Trades and the header pill turns to "1 trade waiting · 0:58".

## 3. Screens (wireframes at 390 px; desktop = 2 columns where noted)
### Home
```
[goldbot•]                    (● Trading normally)  [≡]
┌ Trade waiting · 0:58 ─────────────────────────────┐   <- only when one is pending; else EmptyState line
│ ▲ BUY 0.05 lots gold   Risk £8.20 (0.8%)          │
│ Entry 2400.25  Stop 2396.00  Target 2406.50       │
│ Win chance 62% · Expected +0.31× risk · Spread 22 │
│ Why: strong trend, normal volatility              │
│ [   Approve   (56px, green)   ] [ Reject… ]       │
└───────────────────────────────────────────────────┘
┌ Today ────────────────────────────────────────────┐
│ +£12.40   (+1.24%)          since 01:00 your time │  <- display number
│ Loss limit  £8 of £50 used  [■■□□□□□□□□]  £42 left│
│ Week        £8 of £100 used [■□□□□□□□□□]          │
└───────────────────────────────────────────────────┘
[Balance £1,000] [Equity £1,012.40] [Open trades 1] [Margin 641%]   <- MetricTiles 2x2 / 4x1
┌ Safety ── all clear ──────────────────────────────┐  <- one line when fine; expands to list when not
│ ✓ No halts  ✓ No news blackout  ✓ Data fresh 11:47│
└───────────────────────────────────────────────────┘
Next milestone: 1-hour trades at £2,744 (37%)  ›   Research: 2 of 53 trials used ›
```
Desktop: left column trade + today, right column tiles + safety. States: loading (skeletons same size), error
("Couldn't reach goldbot since 11:47. Exits keep running on the server." amber banner, last data greyed), empty
(no engine: "No engine is reporting, so no trades can open. See Feeds › System health."), halted (pill red
"Entries halted: FOMC", safety card lists each stopper with its clear time; trade card shows "Approving will be
refused: halted").

### Trades
Waiting: full approval card (content as `docs/ux/approval-card.md`, restyled), most urgent first; expiring <= 15 s
amber timer + bar. Recent: compact rows (direction, size, outcome badge, time, via), tap to expand. Results: Progress
results + your trades. Reject opens reason chips in place (News, Cost, Gut feel, Duplicate, Other).

### Chart
Two panels, side by side >= 1080 px, stacked on phone; shared timeframe Segmented (15m · 1h · 4h · 1D) at the top.
1. TradingView: existing sandboxed iframe widget (unchanged CSP), fixed height 420/560 px.
2. MT5 (IC Markets): `<img>` from `GET /api/chart/mt5?tf=...`, fixed aspect box (no shift), caption
   "MT5 chart, IC Markets · 11:45 your time (10:45 UTC)", refetched every 60 s and on timeframe change; stale > 2 bars
   = amber "Not updated since 11:15". Error: "MT5 chart unavailable; the TradingView chart still works." Loading:
   skeleton of the same box. Our indicators on MT5 (MQL5 ports of the replica) are a follow-up for the architect,
   not in this build. Fetched with the session token (blob URL), no identifiers in the image caption.

### Limits / Research / Feeds / Settings
Same content as today, rebuilt from Card, MetricTile, Meter, Badge, DataTable; internal names behind "Details";
tables become cards < 720 px. Settings groups owner controls with explanatory copy and the authenticator step-up.

### Sign-in
Centered 400 px card on `--surface`, wordmark, one primary button, links as ghost buttons; same flows and labels.

## 4. Build slices (3 implementers, no shared files)
| Slice | Owns (only these files) | Delivers |
|---|---|---|
| **A: system + shell** | `web/src/styles.css` (rewrite to tokens + component CSS), `web/src/ui/*` (new), `web/src/App.tsx`, `web/src/App.test.tsx`, `web/src/lib/route.ts` (new), `web/src/screens/Login.tsx` (class names only), `web/e2e/*.spec.ts` nav selectors, `web/shots/*` | tokens, components, AppShell (rail/bottom bar, hash routes, `data-nav-item`), status pill, Settings page composition (Account, Users, AutoMode, HaltControl moved) |
| **B: Home + Trades + Limits** | `screens/Overview.tsx` -> `screens/Home.tsx`, `Approvals.tsx`, `ProposalCard.tsx`, `SafetyStrip.tsx`, `EntryStatus.tsx`, `BalanceCard.tsx`, `Limits.tsx`, their tests | Home per wireframe, Trades segmented, compact decided rows |
| **C: Chart + Research + Feeds** | `Chart.tsx`, `Charts.tsx`, new `Mt5Chart.tsx`, `Progress.tsx`, `Research.tsx`, `ResearchToday.tsx`, `Agents.tsx`, `Feeds.tsx`, `TradingViewCard.tsx`, `News.tsx`, `Health.tsx`, their tests, `lib/api.ts` (one `mt5Chart` call) | Chart two-panel, Research/Feeds segmented pages |
Order: A lands tokens + `ui/` first (~15 min), B and C build on them in parallel; A integrates and re-runs shots.
Gates: `npm run lint && npm run typecheck && npm test && npm run build && npm run test:e2e`, then
`SHOTS_DIR=~/tmp/ui-audit/after npm run shots`.

## 5. Risks
- Deadline (1 h incl. deploy) is tight for 3 slices plus e2e; minimum viable cut: A + Home/Trades from B; C keeps
  current screens restyled by the tokens alone.
- e2e selectors use tab button names (`Approvals`, `Account`, `Users`, ...): slice A must keep accessible names or
  update both spec files in the same commit.
- Text-asserting unit tests (~40 files) will need copy updates where plain-English wording changes; safety wording
  (approval card, refusal, not-confirmed) stays word-for-word (shared with Telegram).
