# UI build plan, backlog 92-101 and 103 (2026-10-19)

Owner of the plan: Principal Product Designer. Specs: `docs/design/ui-91-92-approval-card.md`,
`docs/design/ui-97-98-research-limits.md`. The backlog items in `docs/backlog.d/` (92-101, 103) hold the acceptance criteria.
Every lane writes failing tests first, takes before/after shots with `node scripts/ui_snapshots.mjs <label>`, and runs
the web gates (`npm run lint && npm run typecheck && npm test && npm run build`, e2e for any lane touching a screen).

## Rule: one owner per file
- `goldbot/api/schema.py`, `web/openapi.json` and `web/src/lib/schema.d.ts` are generated or shared, so only lane 1
  writes them. All API fields for 103, 92, 93, 97 and 98 land there in one contract change.
- `web/src/styles.css` belongs to lane 4 only. Other lanes put new CSS in their own screen file (`research.css`,
  `limits.css`), or `styles/base.css` for lane 3.
- `web/src/ui/AccountBadge.tsx` is created by lane 4 in its first commit. Lane 5 rebases on it and does not edit it.

## Wave 1 (start together)
| Lane | Items | Files (exclusive) | Failing tests first | Reviews |
| --- | --- | --- | --- | --- |
| 1 API contract | 103, 92, 93, 97, 98 (API parts) | `goldbot/api/schema.py`, API builders (`goldbot/api/explain.py`, `goldbot/api/limits.py`, the research and automode views in `goldbot/api/app.py`), `goldbot/telegram/approvals.py` (account badge line, ask reason, `windowWords`), the engine Proposal record (`account_mode`, `window_s`, `ask_reason`), the ForcedPropose kinds (+ owner_propose, terminal_not_demo, config_propose), `scripts/e2e_server.py` seed (one card at 90 s on a live-mode account, one forced-demo), `web/openapi.json`, `web/src/lib/schema.d.ts` | pytest: the ask_reason mapping (6 kinds, null on live), API refuses approval for terminal_not_demo, Telegram text golden for both account types, `spread_normal_points` and `open_positions`, research `summary`, limits money fields and reset times | trading-safety, code, security |
| 2 Formatters | 95 (no-break units) | `web/src/ui/format.ts`, `web/src/lib/tradesOverview.ts`, `web/src/lib/activity.ts`, `web/src/screens/HomeCards.tsx` | vitest: every number-unit formatter joins with U+00A0 | code |
| 3 Status and fold | 99, 100, 101 | `web/src/screens/SafetyStrip.tsx`, `web/src/shell/StatusPill.tsx`, `web/src/screens/Home.tsx`, `web/src/screens/Trades.tsx`, `web/src/styles/base.css` | vitest: one no-engine phrase, Account tile "not reported", the Home load error rendered as amber ErrorState; e2e: Approve inside the viewport at 360x740 | code, ui-ux |

## Wave 2 (after lane 1 merges; the four lanes run in parallel)
| Lane | Items | Files (exclusive) | Failing tests first | Reviews |
| --- | --- | --- | --- | --- |
| 4 Approval card | 103, 93, 94 (card words), 96, and the `.dir` part of 95 | `web/src/screens/ProposalCard.tsx`, `web/src/lib/approval.ts`, `web/src/lib/features.ts`, `web/src/ui/AccountBadge.tsx` (new), `web/src/styles.css`, `web/src/screens/Approvals.tsx` | vitest: the 12 states in spec 103 section 4, the window bar at 50% for 300 s, the expiring threshold, "60 min" expired copy, no reason line on live, terminal_not_demo Approve disabled, budget label once, plain-word metrics; grep gate for a literal 90; e2e badge at 390 and 1440 | trading-safety, code, ui-ux |
| 5 Entry mode | 92 | `web/src/screens/AutoMode.tsx` (+ test) | vitest: demo only, demo + live, no engines (never "Propose"), forced row, history collapsed, never implies live can go automatic | trading-safety, code, ui-ux |
| 6 Research | 97 | `web/src/screens/Research.tsx`, `web/src/screens/ResearchToday.tsx`, `web/src/lib/researchWords.ts` (new), `web/src/screens/research.css` (new) | vitest: summary lines from existing data, "not reported yet" fallbacks, all sections collapsed; e2e: full page under 2,500 px at 390 | code, ui-ux; research-director signs off the wording before release |
| 7 Limits | 98 | `web/src/screens/Limits.tsx` (+ test), `web/src/screens/limits.css` (new) | vitest: empty, error (amber, Try again), loaded money lines, the "Binds first" rule, spent banner, one heading | trading-safety, code, ui-ux |

Later, not in these waves: 94's words in Trades and Activity (after lane 2, in `lib/activity.ts`). The demo-back-to-auto
control in 92 waits on the trading-safety ruling.

Rank (severity, then what it unblocks): lane 1, lane 4, lane 5 (HIGH), lane 3 (101 is in the 90-second path), lane 7,
lane 2, lane 6.
