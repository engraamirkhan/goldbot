# Home redesign: three concepts (2026-10-19)

Status: proposal for the stakeholder agents to choose from (owner request 2026-10-19: "the home page needs a complete
redesign for the app; three new ideas shared with the stakeholder agents and they choose the best one").
Author: ui-ux-designer. Bar: `docs/ux/design-system.md` (tokens, components, rules 1-6) and `docs/ux/dashboard-v2.md`.
No code in this note. The chosen concept gets a build spec (states per widget, copy, tests) before the implementer starts.

## 0. Today's Home (read from `web/src/screens/Home.tsx`, `home.css`)

Screenshots were not taken: running the app needs a built `web/dist`, the e2e server and a TOTP sign-in, which this
design-only lane did not set up. Described from the code instead:

| Position (desktop >= 1080 px: two columns; phone: one column in this order) | Widget | Data (poll) |
|---|---|---|
| Left 1 | Waiting trade: the first `ProposalCard` (1 of N), or "No trade waiting..." | `/api/proposals` (2 s), `/api/proposals/recent` (2 s), `/api/status` (5 s) |
| Left 2 | Entry status panel per account (open / waiting / blocked, reasons, fixes) | `/api/accounts` (5 s) `.entry_status` |
| Right 1 | Balance card per account (equity, day P&L, loss budget meter, milestones) | `/api/accounts` |
| Right 2 | Limits strip (link to Limits) | `/api/limits` |
| Right 3 | Progress line (road to live; link to Progress) | `/api/gates` |
| Right 4 | Research line (trials today) | `/api/research` |
| Full width | Activity feed, 480 px inner scroll | `/api/activity` (15 s) |
| Full width | Trades summary: period switch, 5 type tiles, breakdown switch, 8-column table | `/api/trades/overview` (30 s) |

What is wrong with it (ranked by impact on the owner):
1. **No market.** The system's main purpose is trading, yet Home has no price, no chart and no open trade with its
   stop and target. Open trades are only a count inside the balance card.
2. **Phone order buries the answer.** At 360 px the owner scrolls past entry status before seeing today's money, and
   the two things added today (activity, trades summary) sit at the very bottom.
3. **Learning is scattered in four places** (progress line, research line, shadow tiles in the summary, activity rows)
   with no sentence that says what the system learned.
4. **Scroll trap and density on phone:** the activity list scrolls inside the page scroll; the summary has two
   segmented controls and an 8-column table that scrolls sideways.
5. **The owner's own strategies (box break-and-close, money flow) are invisible** until they produce a trade.
6. Eight queries on five intervals; one failure shows as a hole in the page rather than a stated state.

Shared rules for all three concepts (not repeated below):
- Safety is said once, at the top, by one **Status line** (the header `StatusPill` extended to a full-width line on
  Home): "Trading normally", "Entries paused: news until 14:30", "Halted by you at 09:12". Red (`--bad`) only for a
  halt, a cap hit or a refused order; amber for anything that needs attention and does not stop trading; every tone
  has a word and an icon, never colour alone. Tap opens the Limits / Halt sheet.
- A waiting real-money trade always takes the top slot after the status line, in every concept, as today's
  `ProposalCard` (direction, size, $ risk, stop and target in price and money, p and EV, spread now vs normal, reason
  in plain words, other open trades, countdown, one-tap Approve, Reject asks for a reason). Demo trades enter
  automatically (ADR 0036), so on demo the slot shows the last automatic entry instead, for 10 minutes.
- Every number carries its unit; every time is local with UTC in `title` (`TimeText`); money via `Money`.
- Live regions have fixed heights (skeletons the size of the content); no layout shift on refresh.
- 44 px tap targets, visible focus ring, AA contrast in light and dark, `prefers-reduced-motion` respected.
- Each widget keeps its own query, so one failing source shows its own `ErrorState` ("what failed / what still works /
  what to do") and never blanks Home. No aggregate `/api/home` endpoint.

## New data flagged (the implementer adds; then `python scripts/export_openapi.py && npm --prefix web run gen:api`)

| Id | Endpoint / field | Contents | Needed by | Size |
|---|---|---|---|---|
| N1 | `GET /api/positions/open` | per open trade: account, mode, agent_id (family words), timeframe, side, lots, entry, stop, target (USD/oz), opened_utc, floating P&L in account currency, R now | 1, 3 (2 optional) | S: engine state already holds positions; today only `AccountSummary.open_positions` (a count) |
| N2 | `GET /api/strategies/watch` | per owner strategy (box break-and-close, money flow) per timeframe (5m-4h): state `idle / box_forming / waiting_close / signal / in_trade`, box high/low (USD/oz), MFI value, last signal time, one plain sentence | 1, 2 | M: reads `features/box_course.py`, `features/mfi_signals.py` outputs the engine already computes |
| N3 | `GET /api/chart/bars?tf=&n=` | OHLC (USD/oz, UTC) + markers: entries, exits, stops, targets, box edges | 1 only (full version) | M backend + a chart library (see concept 1) |
| N4 | `GET /api/learning/digest` | up to 5 plain sentences, each with a time and a link: "Box break 1h: 14 shadow trades this week, +0.31 R each after costs (was +0.12)" | 2 (v2; v1 derives lines client-side from N-free sources) | M |

---

## Concept 1: "Trading desk" (chart-first)

**Purpose.** Home is the desk a gold trader sits at: the price, my trades on it, and whether I can trade.
**Five-second question:** "Where is gold, what am I holding, and is the bot allowed to trade?"

**Hierarchy.**
Desktop (>= 1080 px, 3 areas): status line; left two-thirds = chart (15m default; 5m/15m/1h/4h switch) with open
trades drawn as entry/stop/target lines; right third = waiting trade or "last entry", today's money (hero number),
open trades list, owner strategies watch; below, full width = activity (last 8, "See all" to Trades).
Phone: status line; waiting trade (if any); chart (fixed 16:10, 225 px high at 360 px); today's money one line; open
trades; strategies watch (collapsed to one line per strategy); activity (last 5).

**Widgets -> API.**
| Widget | Source | New? |
|---|---|---|
| Status line | `/api/status`, `/api/accounts[].entry_status`, `.stage` | no |
| Waiting trade / last entry | `/api/proposals`, `/api/proposals/recent`, `/api/activity` (last `position_opened`) | no |
| Chart, v1 | `/api/chart/mt5` PNG (the broker's own chart, 60 s) with TradingView widget as fallback | no |
| Chart, v2 with trade lines | N3 + `lightweight-charts` (~45 kB gz, Apache-2.0, canvas) | **N3**, new dependency |
| Today's money | `/api/accounts` `day_pnl`, `currency`, `loss_budget` | no |
| Open trades | **N1** | **N1** |
| Strategies watch | **N2** | **N2** |
| Activity (last 8) | `/api/activity?limit=8` | no |

**Removed from Home:** trades summary card (moves to Trades > Results), research line and progress line (move to
Research), limits strip (folded into the status line), per-account balance cards (one money line per account),
the 8-column breakdown table.

**Desktop wireframe**
```
+--------------------------------------------------------------------------------------------------+
| (o) Trading normally . demo icm . news blackout none . data 12 s old           updated 14:02    |
+--------------------------------------------------------------+-----------------------------------+
| XAU/USD  2,412.35 USD/oz  +0.42% today          [5m][15m][1h][4h] | TRADE WAITING      62 s left |
|                                                               | LONG 0.05 lot . risk £8.40      |
|   ____/\      __                 ---- target 2,421.00 ------   | stop 2,405.10  target 2,421.00  |
|  /      \____/  \__/\    /\__    ==== entry  2,412.00 ======   | p 0.58 . EV +0.21 R . spread 18 |
|                      \__/        ---- stop   2,405.10 ------   | (normal 16 pts)                 |
|                                                               | Why: box broke and closed above;|
|  box 2,398-2,411 (1h) shaded                                   | money flow rising               |
|                                                               | [ Approve (one tap) ] [ Reject ]|
|  MT5 chart, captured 14:01 (UTC 13:01)                         +---------------------------------+
|                                                               | TODAY   +£23.10   loss budget   |
|                                                               | (2.3% of £1,000)  £41.60 left   |
|                                                               +---------------------------------+
|                                                               | OPEN TRADES (2)                 |
|                                                               | LONG 0.05 box 1h  +£6.20 +0.7R  |
|                                                               | SHORT 0.03 mf 15m -£1.80 -0.2R  |
|                                                               +---------------------------------+
|                                                               | YOUR STRATEGIES                 |
|                                                               | Box 1h: waiting for a close >2,411|
|                                                               | Money flow 15m: idle (MFI 48)   |
+--------------------------------------------------------------+-----------------------------------+
| ACTIVITY                                                                         See all >      |
| 14:01  Opened   LONG 0.05 @ 2,412.00 . box break 1h . demo                                      |
| 13:40  Closed   shadow money flow 15m . target . +1.1 R                                          |
| ... (8 rows, no inner scroll)                                                                    |
+--------------------------------------------------------------------------------------------------+
```

**Phone wireframe (360 px)**
```
+----------------------------------+
| (o) Trading normally   14:02     |
+----------------------------------+
| TRADE WAITING        62 s left   |
| LONG 0.05 lot . risk £8.40       |
| stop 2,405.10 . target 2,421.00  |
| p 0.58 . EV +0.21 R . spr 18/16  |
| Why: box broke, money flow up    |
| [     Approve     ] [ Reject ]   |
+----------------------------------+
| XAU 2,412.35 USD/oz  [15m v]     |
| +------------------------------+ |
| |   chart 16:10, fixed height  | |
| +------------------------------+ |
| MT5, 14:01                       |
+----------------------------------+
| Today +£23.10 . £41.60 loss left |
+----------------------------------+
| Open (2)                       > |
| LONG 0.05 box 1h   +£6.20 +0.7R  |
| SHORT 0.03 mf 15m  -£1.80 -0.2R  |
+----------------------------------+
| Box 1h: waiting for close >2,411 |
| Money flow 15m: idle             |
+----------------------------------+
| Activity (5)          See all >  |
+----------------------------------+
```

**Accessibility and colour.** Trade lines use pattern + label, not colour: stop dashed with "stop 2,405.10", target
dotted with "target", entry solid; long green `--long`, short blue `--short` (never red). The chart image has alt text
"XAU/USD 15-minute chart, MT5, captured 14:01"; open trades are also a list, so nothing is chart-only. A loss is
"-£1.80" with U+2212, not red. Red appears only in the status line (halt) and on a refused order.

**Risks.** The chart costs about 40% of the phone's first screen; on a quiet day it is decoration. v2 adds the
first chart dependency and a bars endpoint the engine must keep fast. The MT5 PNG (v1) is up to 60 s old and must
say so.

**Effort.** M for v1 (PNG chart, N1, N2); L for v2 (N3 + chart library + markers).

---

## Concept 2: "Learning cockpit" (what the system is learning first)

**Purpose.** Home tells the owner what the bot found out since he last looked, and how close it is to earning real
money; trading state is a compact header, because most of the time nothing needs him.
**Five-second question:** "Is the bot getting better, and what did it learn today?"

**Hierarchy.**
Desktop: status line; waiting trade (only when present); "Since you last looked" digest (3-5 sentences, each with time
and a link); three columns: **Strategies** (each family: shadow trades this week, R after costs, trend arrow + word),
**Experiments** (trials today: passed / failed / needs more data; budget left this quarter), **Road to live** (next
gate, what it needs, in words); full width: shadow vs taken comparison (trades overview, 7 d) as 2 tiles; activity
filtered to closed trades and verdicts.
Phone: status line; waiting trade; digest; strategies (top 3, "All" link); road to live one card; experiments one line;
today's money one line at the bottom.

**Widgets -> API.**
| Widget | Source | New? |
|---|---|---|
| Status line, waiting trade | as shared rules | no |
| Digest, v1 | built client-side from `/api/trades/overview` (7d vs all, by family), `/api/research.today.latest`, `/api/gates` next gate | no |
| Digest, v2 | **N4** | **N4** |
| Strategies | `/api/trades/overview` `by_family` (shadow_taken, demo) + `/api/agents` (status, n_trades, expectancy_r) | no |
| Owner strategies live state (optional row) | **N2** | **N2** |
| Experiments | `/api/research` `.today`, `.budget` | no |
| Road to live | `/api/gates` (`phase`, next gate, `stop_rule`) | no |
| Shadow vs taken | `/api/trades/overview` types `shadow_all`, `shadow_taken` | no |
| Today's money | `/api/accounts` | no |
| Activity (closed + refused) | `/api/activity` filtered client-side | no |

**Removed from Home:** entry status panel (its headline lives in the status line; detail on Limits), balance cards,
limits strip, the trades summary table and both segmented controls, open-trade detail (Trades tab).

**Desktop wireframe**
```
+--------------------------------------------------------------------------------------------------+
| (o) Trading normally . demo . today +£23.10 . open 2                         updated 14:02      |
+--------------------------------------------------------------------------------------------------+
| SINCE YOU LAST LOOKED (09:15)                                                                    |
| . Box break 1h: 14 shadow trades this week, +0.31 R each after costs (was +0.12 last week)  >   |
| . Money flow 15m: 3 losses in a row on spreads over 25 pts; now skips those (shadow only)    >   |
| . Trial 212 (box break 4h) passed all gates; needs 30 more trades before demo             >   |
+--------------------------------+--------------------------------+--------------------------------+
| STRATEGIES (7 days, after costs)| EXPERIMENTS TODAY (UTC day)   | ROAD TO LIVE                   |
| Box break 1h   +0.31 R  up     | Run 6 . passed 1 . failed 4   | Stage: demo                    |
| Money flow 15m -0.05 R  flat   | Needs more data 1             | Next: 100 demo trades          |
| Session open   +0.08 R  down   | Budget left 41 of 60 (Q4)     | 63 of 100 done (meter)         |
| All strategies >               | Latest: box 4h, passed  >     | Then: owner unlock + phrase    |
+--------------------------------+--------------------------------+--------------------------------+
| SHADOW: all candidates 7d +0.09 R/trade (n 210)  |  TAKEN by the model 7d +0.27 R/trade (n 48)   |
+--------------------------------------------------------------------------------------------------+
| RESULTS AND VERDICTS (activity, closed only)                                       See all >    |
+--------------------------------------------------------------------------------------------------+
```

**Phone wireframe (360 px)**
```
+----------------------------------+
| (o) Trading normally   14:02     |
| today +£23.10 . open 2           |
+----------------------------------+
| (waiting trade card if any)      |
+----------------------------------+
| Since you looked (09:15)         |
| . Box 1h: +0.31 R/trade, 7d   >  |
| . Money flow 15m: skips wide  >  |
|   spreads now                    |
| . Trial 212 passed gates      >  |
+----------------------------------+
| Strategies, 7 days               |
| Box 1h       +0.31 R   up        |
| Money flow   -0.05 R   flat      |
| Session open +0.08 R   down      |
| All >                            |
+----------------------------------+
| Road to live: demo               |
| [#######-----] 63/100 trades     |
+----------------------------------+
| Experiments: 6 today, 1 passed   |
| Budget 41/60 left this quarter   |
+----------------------------------+
```

**Accessibility and colour.** Trend is an arrow plus a word ("up / flat / down"), never colour alone; negative R uses
U+2212 and stays neutral text (a losing strategy does not stop trading, so it is never red; amber only if the stop
rule is near). The meter is `role="meter"` with "63 of 100 demo trades". Nothing on the learning part is red.

**Risks.** Puts research ahead of money: on a day with a halt or an open losing trade the owner sees learning first
unless the status line carries it (it must). v1's client-side digest is rule-based and can read as noise; the digest
needs a ranking rule (biggest change in R after costs first, max 3 lines). Weakest of the three for the 90-second
decision on the phone if the waiting card slot is ever skipped.

**Effort.** M (v1 without new endpoints); +M for N4.

---

## Concept 3: "Status card stack" (mobile-first, one question per card)

**Purpose.** Home is a short stack of cards, each answering one question in one line, each tappable to its full tab.
Built for a glance on the phone; the desktop is the same cards in a grid, not a different page.
**Five-second question:** "Do I need to do anything right now?" (and if not, the next four lines say why not).

**Hierarchy (card order = priority; the same on phone and desktop).**
1. **Needs you**: waiting real-money trade (the full `ProposalCard`), or a blocking item with its fix
   (`entry_status.blocks` with `kind: act`), or "Nothing needs you" one line. Only card that can be tall.
2. **Can it trade?** status line in card form: allowed / paused until hh:mm / halted; one tap opens Limits; Halt
   reachable here in one tap (confirm in place, rule 4).
3. **Today**: hero number (day P&L in account currency and % of equity), loss budget meter, open trade count.
4. **Open trades**: one row per trade: side, size, strategy + timeframe, floating money and R, stop and target.
5. **Your strategies**: box break-and-close and money flow, one row per timeframe with its state in words.
6. **Learning**: one sentence + 2 numbers (shadow R/trade 7 d after costs; road-to-live progress).
7. **Recent**: last 5 activity rows, no inner scroll, "See all".
Desktop (>= 1080 px): 12-column grid; row 1: Needs you (8 cols) + Can it trade/Today stacked (4 cols); row 2: Open
trades (8) + Your strategies (4); row 3: Learning (4) + Recent (8). Max width 1120.

**Widgets -> API.**
| Card | Source | New? |
|---|---|---|
| Needs you | `/api/proposals`, `/api/proposals/recent`, `/api/accounts[].entry_status.blocks` | no |
| Can it trade? | `/api/status`, `/api/accounts[].entry_status`, `stage` | no |
| Today | `/api/accounts` `day_pnl`, `day_pnl_pct`, `loss_budget`, `open_positions` | no |
| Open trades | **N1** (interim: count from `/api/accounts` + "See Trades") | **N1** |
| Your strategies | **N2** (interim: card hidden, not shown empty) | **N2** |
| Learning | `/api/trades/overview` (7d, shadow_taken R/trade), `/api/gates` (next gate) | no |
| Recent | `/api/activity?limit=5` | no |

**Removed from Home:** per-account balance cards (into Today; per-account detail on Account), entry status panel as a
separate block (merged into Needs you / Can it trade), limits strip, progress line and research line (one Learning
card), trades summary with its two switches and table (moves to Trades > Results), the 480 px activity scroller.

**Desktop wireframe**
```
+--------------------------------------------------------------------------------------------------+
| goldbot                                     (o) Trading normally          Owner   Sign out      |
+--------------------------------------------------------------+-----------------------------------+
| NEEDS YOU                                       62 s left     | CAN IT TRADE?                     |
| LONG XAU/USD 0.05 lot . risk £8.40 (0.84%)                    | (o) Yes, new entries allowed     |
| entry 2,412.00 . stop 2,405.10 (-£8.40) . target 2,421.00     | demo icm . standard . data 12 s  |
| (+£10.95) USD/oz                                              | [ Halt new entries ]              |
| p 0.58 . EV +0.21 R . spread 18 pts (normal 16)               +-----------------------------------+
| Why: box broke and closed above 2,411; money flow rising      | TODAY             since 23:00     |
| Also open: SHORT 0.03 money flow 15m                          | +£23.10  (+2.3%)                  |
| [        Approve        ]   [ Reject... ]                     | loss budget [###-------] £41.60 left|
|                                                               | open trades 2                     |
+--------------------------------------------------------------+-----------------------------------+
| OPEN TRADES (2)                                     See all > | YOUR STRATEGIES                   |
| LONG  0.05  Box break 1h   +£6.20  +0.7 R  stop 2,405.10 tgt 2,421.00 | Box 1h  waiting for a close above 2,411 |
| SHORT 0.03  Money flow 15m -£1.80  -0.2 R  stop 2,419.00 tgt 2,401.50 | Box 4h  idle             |
|                                                               | Money flow 15m  idle (MFI 48)     |
+------------------------------+-------------------------------+-----------------------------------+
| LEARNING                     | RECENT                                                See all >   |
| Shadow, 7 d: +0.27 R/trade   | 14:01 Opened  LONG 0.05 @ 2,412.00 box 1h demo                   |
| after costs (n 48)           | 13:40 Closed  shadow money flow 15m target +1.1 R                 |
| Road to live: 63/100 demo    | 13:12 Refused entry: spread 31 pts (normal 16)                    |
| Research >                   | ...                                                               |
+------------------------------+-------------------------------------------------------------------+
```

**Phone wireframe (360 px)**
```
+----------------------------------+
| goldbot     (o) Trading normally |
+----------------------------------+
| NEEDS YOU             62 s left  |
| LONG 0.05 lot . risk £8.40       |
| entry 2,412.00 USD/oz            |
| stop 2,405.10 (-£8.40)           |
| target 2,421.00 (+£10.95)        |
| p 0.58 . EV +0.21 R              |
| spread 18 pts (normal 16)        |
| Why: box broke above, money flow |
| rising                           |
| Also open: SHORT 0.03 mf 15m     |
| [        Approve (56 px)       ] |
| [ Reject... ]                    |
+----------------------------------+
| (o) Can trade . demo . 12 s    > |
+----------------------------------+
| Today  +£23.10  (+2.3%)          |
| [###-------] £41.60 loss left    |
+----------------------------------+
| Open (2)                       > |
| LONG  0.05 box 1h  +£6.20 +0.7R  |
| SHORT 0.03 mf 15m  -£1.80 -0.2R  |
+----------------------------------+
| Your strategies                > |
| Box 1h: waiting for close >2,411 |
| Money flow 15m: idle             |
+----------------------------------+
| Learning: +0.27 R/trade (7 d)  > |
| Road to live 63/100              |
+----------------------------------+
| Recent                 See all > |
| 14:01 Opened LONG 0.05 box 1h    |
| 13:40 Closed shadow mf +1.1 R    |
+----------------------------------+
| [Home][Trades][Chart][Limits][..]|
+----------------------------------+
```
With nothing waiting, card 1 is one line: "Nothing needs you. You'll get a Telegram ping." and Today rises to the
first screen, so the phone's first 640 px show status, today's money, open trades and strategies.

**Accessibility and colour.** Each card is a landmark (`section` + `aria-label`) and a single link target (44 px
header row with ">" and visible focus). Card 1 uses `--bad` only for a halt or refused order; a blocking item that
clears by itself (`kind: wait`) is amber. Up/down in money is a sign and a word, not colour. Live text in Today and the
countdown sits in fixed-height boxes; the countdown is `aria-live="polite"` only at 30 s and 10 s.

**Risks.** Without N1 and N2 two cards are thin (interim: count only; strategies card hidden). The chart is one tap
away (Chart tab), not on Home; a trader who wants price at a glance gets it only in the Needs-you card's levels.

**Effort.** S for the frontend on existing APIs (reuses `ProposalCard`, `Card`, `MetricTile`, `Meter`, `TimeText`);
+S for N1; +M for N2. Total M.

---

## Comparison for the stakeholders

| | 1 Trading desk | 2 Learning cockpit | 3 Status card stack |
|---|---|---|---|
| 5-second question | Where is gold, what do I hold, can it trade? | Is it getting better? | Do I need to act? |
| 90-second approval on phone | good (card above chart) | weakest (learning competes) | best (card 1, full height) |
| Shows the market | yes | no | levels only |
| Shows learning | no (Research tab) | best | one card |
| Owner strategies visible | yes (N2) | optional (N2) | yes (N2) |
| New endpoints | N1, N2 (+N3 for v2) | none (v1), N4 (v2) | N1, N2 |
| New dependency | chart library in v2 | none | none |
| Effort | M (v1) / L (v2) | M | S frontend, M total |

**Designer's recommendation (the stakeholders choose):** Concept 3 as the base, because it is the only one whose
first card is always the owner's one decision and it ships on existing APIs today; take Concept 1's chart as a
collapsible card 4b on desktop only (MT5 PNG, no new library) and Concept 2's digest as the Learning card's v2 (N4).
What would change this: if the owner says he opens Home to look at price, pick Concept 1; if real-money trading is more
than a month away and he mainly checks progress, pick Concept 2.

## States every concept must specify in its build spec
Loading (skeleton the size of the content, no shift), empty (one sentence + what will appear), error ("what failed /
what still works / what to do" + Retry), stale (older than 60 s: time shown, amber "data from 13:58, can't reach the
server", never "allowed" from stale data), pending (waiting card), expiring (under 15 s: countdown amber + word
"expiring", buttons stay enabled until 0), expired ("Expired at 14:03, not placed"), success ("Approved 14:02, order
sent" then "Placed at 2,412.10"), refused (red, the refusal in words from `refusal_text` / `not_placed`), halted (status
line red with who/when and Re-arm path), at 360 px and >= 1080 px, light and dark.

## Decision 2026-10-19 (product owner, program director, CFO: unanimous)
**Concept 3, the status card stack** (scores: PO 22 vs 17/14, PD 19 vs 15/12, CFO 4/5 vs 2/3), with grafts:
- From concept 1: a collapsible MT5 chart card (existing PNG), desktop only; entry/stop/target lines later (N3).
- From concept 2: up to 3 "since you last looked" digest lines in the Learning card (ranked by the largest change in
  R after costs, computed client-side in v1); the two shadow-vs-taken tiles (all candidates vs taken, 7 days, n),
  each tagged "shadow, not money".
Conditions: "Your strategies" (N2) is required (owner's box break-and-close + money flow on 15m/1h/4h, a state in
words); the open-trades endpoint (N1) returns stop, target, floating P&L and R; ProposalCard stays byte-identical and
a waiting real-money proposal is always card 1 (56 px Approve); a DEMO/REAL badge on every £ figure, real and demo
never summed; day P&L after costs split closed/open; today's costs (spread, commission, swap); a loss meter naming
the limit that binds first (probe stop ≈ £16/day or the £50 daily limit); shadow numbers in R only, with n; money or
status older than 60 s marked stale, never "allowed"; red only for halt, cap hit or a refused order; each card its
own query (one failing source never blanks Home); Playwright at 360 and 1080 px, light and dark, axe clean.
