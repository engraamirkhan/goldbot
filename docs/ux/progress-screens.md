# Progress tab: stop rule, road to live (gates) and results (attribution)

ROADMAP W2-3. These are three read-only screens for the owner, on a phone or a Mac, and they answer three questions:

| Section | The owner's question | Answer at a glance |
|---|---|---|
| **Stop rule** | "Should the project stop?" | A word badge (NOT BREACHED, BREACHED or CAN'T CHECK), the two conditions with their progress, and what to do |
| **Road to live** | "How close are we to live, and what is missing?" | The four gates in order. Each shows RECORDED, MET, NOT MET (k of n) or WAITS. The next gate is open, with each item as "have X of Y" |
| **Results** | "Is it making money, where from, and is that real or luck?" | Net R per trade after costs, the verdict in words, and the breakdowns by timeframe, family, session and the rest, plus where costs go |

Style follows `approval-card.md`, `entry-status.md` and `health-research.md`: the same tokens, words before colour,
units on every number, and local times with UTC on hover. Red is used only for things that stop trading. Nothing on
these screens stops trading, so **nothing here is red** (see Decisions). Nothing on these screens records a gate,
unlocks live, halts or changes a setting.

## Data

| Endpoint (new, read-only) | Role | Built from |
|---|---|---|
| `GET /api/gates` → `GatesView` | any logged-in user | `gates_phase.evaluate_gates` run on request with nothing written (the `--no-write` path), cached for 60 s. It reads `closed_trades*.jsonl`, `phase_state.json`, `gate_evidence.json`, the registry and the cost tables. It never serves `gate_report.json`, which only the CLI writes and which can be days old |
| `GET /api/attribution` → `AttributionView` | any logged-in user | `state/attribution.json` (daily job at 23:50 UTC), without its `trades` rows (300 rows the screen does not use) |
| `GET /api/trades/summary` → `TradesSummary` (recommended, see Gaps) | any logged-in user | `closed_trades*.jsonl` per account, by opportunity day (`ops/report_days.py`, the monthly report's day) |

None of these exist yet. Every field the screens need is listed under **API gaps** below, and the browser holds no
policy. Verdicts, staleness, "proposed" flags and the stop-rule status all come from the API, as the entry status
does.

The polling cadence is fixed. Gates and stop rule refresh every 60 s and when the window regains focus. Results
refresh every 5 min, because the data is daily. Hidden tabs do not poll.

## Placement

There is one new tab, **Progress**, placed third: Overview, Approvals, Progress, News and so on. On a phone the tab
row scrolls sideways, so a tenth tab at the end would be off-screen. The page is a single scroll with three sections
in safety order: Stop rule, then Road to live, then Results. Under the heading, a row of three jump chips
(`Stop rule · Gates · Results`, 44 px tall, real `#` links) scrolls to each section. No content is hidden behind a
segmented control, and the browser's find works.

Overview gains one line under the entry status. It sits in a fixed-height slot, so it causes no shift, and the whole
line links to Progress:

```
Stop rule: not breached · Next gate: Paper → tiny live, 3 of 7 met      ›
```

When the stop rule is breached, Overview shows an amber banner (`role=alert`) above the tiles with the breach headline
and a link "Open Progress". It stays until the breach clears.

## Layout

Phone (360 px, single column, 16 px gutters). Every block keeps its height when it refreshes: tabular digits,
fixed rows, and meters that move with `transform`.

```
Progress                                   as of 14:02    ← evaluated time, your zone; UTC on hover
[ Stop rule ] [ Gates ] [ Results ]                       ← jump chips, 44 px

┌ Stop rule ─────────────────────── [NOT BREACHED] ┐     ← word badge first, green tint
│ Should the project stop? Not on the evidence so   │
│ far.                                              │
│                                                   │
│ LONG-RUN EDGE · can trigger from month 18         │     ← condition A
│ Time       4.2 of 18 months       ███░░░░░░░░░    │     role=meter
│ Trades     61, needs more than 500 █░░░░░░░░░░    │
│ Edge       −0.041% per trade, worst case          │     lower 90% bound, % of price
│            (must be above 0% by month 18)         │
│                                                   │
│ SINGLE BIG LOSS · any time                        │     ← condition B
│ Worst      0.82% of equity · limit 5.0%  ██░░░░   │
│            icm-demo · demo · Tue 13 Oct 16:20     │     account config name, mode, close time (your zone)
│                                                   │
│ Counts demo and live trades since Thu 1 Oct       │     clock start (the paper gate, or the first trade)
│ (paper start). 2 trades had missing costs and     │     only with imputed rows
│ were charged an estimate ($0.84 in total).        │
│ This check never halts trading by itself.         │
└───────────────────────────────────────────────────┘

┌ Road to live ─────────────────────────────────────┐
│ Now: paper (demo). Next: gate 3, Paper → tiny live │
│ ┌ 5 thresholds are proposed, not signed off. ────┐ │     ← blue info, owner-decision; tap: list + link
│ └ Agree them before the first paper trade. ──────┘ │
│ ① Leakage audit             [RECORDED] 2 Oct       │     ← collapsed rows, 48 px, tap to open
│ ② Backtest → paper (demo)   [RECORDED] 9 Oct       │
│ ③ Paper → tiny live         [NOT MET] 3 of 7    ▾  │     ← the next unrecorded gate starts open
│   ✓ Gate 2 recorded first                          │
│   ✗ Demo trades      12 of 150 closed  █░░░░░░░░   │
│   ✗ Time on paper    7 of 182 days     ░░░░░░░░░   │
│                      earliest Thu 1 Apr 2027       │     calendar date, never a forecast
│   ✗ Results vs backtest  [Proposed]                │     ← tag when the threshold is unsigned
│                      paper −0.02% vs backtest      │
│                      +0.05% per trade; needs no    │
│                      more than 50% below           │
│   ✓ Fills vs model   $0.031/oz vs $0.028/oz        │
│                      modelled (within 30%) · 41    │
│                      fills                         │
│   ✗ Chaos drill      not recorded yet              │
│   ✓ No single big loss (stop rule)                 │
│   · For context: 312 closed shadow trades          │     not required: muted, no tick or cross
│ ④ Tiny live → full size     [WAITS] for gate 3     │
│ Gates are recorded only by the owner, with a      │
│ command on the server. This page never records a  │
│ gate or turns on live.                            │
└───────────────────────────────────────────────────┘

┌ Results · last 180 days ──────── as of Wed 00:50 ┐     ← job time, your zone; UTC on hover
│ Simulated trades: every signal the models took,    │
│ traded on paper at the real spread. Not your       │
│ account's money.                                   │
│                                                    │
│ +0.04 R per trade after costs · 212 trades         │     16 px semibold
│ [CAN'T TELL FROM LUCK]                              │     verdict badge (words)
│ Likely range −0.05 to +0.13 R (95%)  ──┼──●────    │     diverging bar at 0 with the range whisker
│ Before costs +0.11 R · costs −0.07 R per trade     │
│ 1 R ≈ £5.00 at today's size (0.5% of £1,000)        │     only when the API gives it
│                                                    │
│ Break down by  [ Timeframe          ▾ ]             │     native <select>, 44 px
│ 15m   +0.02 R · 120 trades  [CAN'T TELL]        ›   │     rows in fixed order, never sorted by result
│ 1h    +0.07 R ·  71 trades  [CAN'T TELL]        ›   │
│ 4h    −0.10 R ·  21 trades  [TOO FEW]           ›   │     muted row: fewer than 30 trades
│   (tap a row: before costs, likely range, share   │
│    of winners, profit factor)                     │
│                                                    │
│ WHERE COSTS GO (per trade)                         │
│ Spread −0.04 R · Slippage −0.01 R ·                │
│ Commission −0.02 R · Swap 0.00 R                   │
│ Costs take 64% of the result before costs.         │
│ 18 trades won before costs and lost after them.    │
│                                                    │
│ LIVE FILLS VS THE COST MODEL                       │
│ icm-demo: 41 fills, $0.003/oz worse than modelled  │
│ [CAN'T TELL FROM LUCK]                              │
│                                                    │
│ These are findings, not settings. With about 30    │
│ rows, one or two can look "beyond luck" by chance. │
│ Changing a strategy because of one row is a new    │
│ trial: ask the research director.                  │
│ Notes ▸ (costs source, unknowns)                    │     collapsed
└────────────────────────────────────────────────────┘

┌ Your account's trades · last 30 days ────────────┐     ← recommended (API gap 4); hidden until served
│ icm-demo · demo                                    │
│ +£42.10 over 9 closed trades · 3 trading days      │
│ Days with £50 or more: 0 of 22 trading days        │
│ Best day Tue 13 Oct +£31.40 · worst Fri 9 Oct      │
│ −£12.80 (day = 23:00 to 23:00 your time)           │     the report day, said in the owner's zone
└────────────────────────────────────────────────────┘
```

Desktop (from 960 px): the stop rule and the road to live sit side by side in two columns, with Results full width
below them. From 720 px the breakdown is a real table: name, net R per trade, trades, verdict, before costs, likely
range, share of winners, profit factor. On a phone each row is a disclosure that opens to the same four extra figures.

## States

### Stop rule

| `status` | Badge (word, tone) | Headline | Body |
|---|---|---|---|
| `ok` | **NOT BREACHED**, green | "Should the project stop? Not on the evidence so far." | Both conditions with progress, and the clock line |
| `not_started` (no closed trade and no paper gate) | **NOT STARTED**, grey | "The stop rule starts counting at paper trading." | "The 18-month clock starts when the backtest → paper gate is recorded, or at the first closed demo trade." Conditions are shown with 0 |
| `breached` | **BREACHED**, amber, `role=alert` | "The design says the project stops." | The reason(s) in words (below), then "Nothing was halted automatically. The RiskGate's limits still apply and exits keep running." Then a 44 px button **Go to Halt** (opens Overview's halt control; for a viewer: "The owner decides whether to halt.") and "Then review: ask Claude for options (Owner guide, alerts)." |
| `cant_check` | **CAN'T CHECK**, amber | "Stop rule: can't check" | The owner's text from the API, for example "The closed-trade record is partly unreadable, so the stop rule can't be fully checked. Nothing is paused by this; exits always run." The last known figures are shown under "Last checked …" when the API has them |

Breach reasons, in words:

- **Long-run edge**: "After 18.3 months and 512 trades, the worst-case edge is still below zero (−0.012% per
  trade)."
- **Single big loss**: "One trade lost 5.6% of equity, more than the 5.0% weekly limit · icm-demo · demo · closed
  Tue 13 Oct 16:20." When there is more than one, it adds "(+2 more)" and lists up to five, worst first.

Condition A shows "can trigger from month 18" until month 18. After that it reads "checked now". The edge line
before 30 trades reads "Edge: too few trades to estimate yet".

### Road to live

| Gate state | Badge | Row text |
|---|---|---|
| recorded | **RECORDED**, green | "Recorded 9 Oct" (date from the gate log, your zone, UTC on hover) |
| met, not recorded | **MET**, blue | "Met on the evidence. Recording it is your decision." For the owner only, a Copy button with `goldbot run gates` and then `goldbot run record-gate paper_to_tiny_live --evidence state/gate_report.json`, plus "Runbook: Going live ↗". For gate 3 it also says: "Recording this gate does not turn on live: that also needs `accounts unlock-live` and the typed phrase." Viewers read "The owner records it on the server." |
| not met (next gate) | **NOT MET**, amber | "3 of 7 met". Open by default |
| not met, previous gate not recorded | **WAITS**, grey | "Waits for gate 3". Collapsed; it opens to its items, all shown as not yet |
| can't check | **CAN'T CHECK**, amber | "Gates: can't check" with the API's text. One unreadable input (for example "The gate record (phase_state.json) is unreadable, so the gates are shown as if none is recorded.") is shown at the top of the section and every gate badge is followed by "(unverified)" |

Item rows: ✓ met, ✗ not met, and · for context only. The mark always comes with words ("met" and "not met" are in
the accessible name), so state is never shown by the icon or the colour alone. Each item shows "have X of Y unit"
and a meter when the API gives `progress`. Day items add "earliest <date>": the calendar date the day count is reached,
which is never a forecast of when trades will arrive. An item whose threshold is unsigned carries a **Proposed** tag
(blue, information). Tapping it reads "This threshold is proposed and waits for your sign-off (config/settings.yaml
`gates:`)."

The proposed-thresholds note appears at the top of the section while any threshold is unsigned: "N thresholds are
proposed, not signed off. The design wants them agreed before the first paper trade." It opens to the list, each with
its proposed value, and a link "Owner guide: Decisions waiting for you ↗".

### Results

| Verdict (API `verdict`) | Badge (words) | Tone |
|---|---|---|
| `positive` | **AHEAD, BEYOND LUCK** | green |
| `negative` | **BEHIND, BEYOND LUCK** | amber (not red: it stops nothing) |
| `indistinguishable from zero` | **CAN'T TELL FROM LUCK** | grey |
| `noise` | **TOO FEW TRADES** ("12 of 30 needed" in the row) | grey, row muted |

Breakdown labels in plain words (`web/src/lib/progress.ts`; the raw key goes in the hover title): timeframe (15m, 1h,
4h, 1d), family (from the family map, for example "Session open", "Trend"), session (Asia, London, New York),
side (Long, Short), volatility (Calm, Normal, Busy, Unknown), taken vs not (Taken, Not taken), exit (Target, Stop,
Time exit, Exit rule). The "by agent" breakdown appears only on desktop, as the last option, because agent ids are
long.

Rows keep a fixed order: the natural order of the dimension (sessions in clock order, timeframes ascending) and
families alphabetically. They are never sorted by result, so the screen does not invite picking the best row.

## Cases (every section)

| Case | Shown |
|---|---|
| Loading | Section heading plus a placeholder at the final height (`aria-busy`, "Loading stop rule…", "Loading gates…", "Loading results…"). Nothing shifts when it fills |
| API error, no data yet | Amber, `role=alert`: "Could not load gates (…). Trading is not affected; safety state is on Overview and Approvals." (and the same for results) |
| Server unreachable after data, or last success more than 3 polls ago | Amber banner at the top of the tab: "Can't reach the server since 14:02; below is the last report." Badges keep their words and gain "(as of 14:02)". Nothing turns green on stale data |
| Results stale (`stale` from the API: older than the daily job's slot plus its 20 h lateness) | Amber note under the Results heading: "These results are from Tue 13 Oct 00:50 (2 days ago): the daily results job hasn't run since. Health → Scheduled jobs shows why." The figures stay visible |
| Results can't check (`attribution.json` unreadable) | Amber: "Results: can't check. The daily results file is unreadable, so results can't be shown. Trading is not affected." |
| Results empty (no file yet) | Grey: "No results yet. The results job runs daily at 00:50 your time once the shadow book has closed trades." (job time from the API, UTC on hover) |
| Results, no trades in window | Grey: "No closed simulated trades in the last 180 days yet." Costs and fills are hidden |
| Gates with no closed-trade record | Items read "0 of 150 closed (no trade record yet)". This is not an error |
| No phase record | Normal: nothing recorded, gate 1 is next |
| Your trades, empty | "No closed trades on icm-demo in the last 30 days." |
| Older server (route missing, 404) | Grey: "Not reported by this server version." |

The "can't check" wording follows the loss budget's (BACKLOG 61, `account_view._CANT_CHECK`). The bold line reads
"<Thing>: can't check", then one sentence says why and what cannot be checked, then the consequence. Unlike the loss
budget, these checks never pause entries, so they are always amber and always say so ("Nothing is paused by this;
exits always run" or "Trading is not affected").

## Copy rules

- **Units on every number.** R ("R = what one trade risks; +0.10 R means each trade made a tenth of its risk on
  average", in the Results heading's info tip), "% per trade" (a price return), "% of equity", days, months, trades,
  `$`/oz (labelled USD; slippage is measured in dollars per ounce), and the account currency for money.
- **Times** are in the owner's zone, with the weekday when the time is not today, and UTC in the hover title
  (`entry.ts` helpers `whenText`/`utcText`). Text from the API never carries a time (the API sends `…_utc` fields),
  so every time is localised.
- Say "simulated" for shadow-book figures and "your account's" for closed trades, every time. The two are never
  added together or shown in one table.
- "Worst case" means the lower 90% bound. "Likely range" means the 95% interval. "Beyond luck" means |t| ≥ 2. Each
  of these has an info tip that gives the statistical term.
- Never "approved", "ready to go live" or "passed" for a gate that is only met. "Met on the evidence" is the
  strongest phrase. Recording is the owner's decision, and live is a separate lock.

## What must never be shown (A15 and the public-repo rule)

- No email, Telegram id, MT5 login, server name, host or IP, and nobody's identity. Who recorded a gate, recorded
  evidence or halted is never shown (A15: the audit log only, served by no route). The tab's own header keeps the
  current behaviour (see Risks).
- Accounts appear by config name. Any run of 5 or more digits is cut to its last two ("icm-demo-…67"), the
  `entry_text.py` rule, applied by the API so no client can forget it. Brokers appear by broker name only.
- No free text typed by an operator. The `--detail` note of `gate-evidence` (for example "killed terminal
  mid-position") stays in the CLI output. The API serves the value, pass or fail, and the date only, because typed
  notes can hold a login or host.
- No absolute paths, usernames or home directories. Commands use repo-relative paths (`state/gate_report.json`).
- No raw exception text. "Can't check" reasons are mapped codes with owner wording. The exception goes to the server
  log.

## Accessibility

- Contrast: the badge and meter tokens are the existing AA pairs (`--ok-*`, `--warn-*`, `--bad-*`, plus `--info-*` for
  MET and Proposed), checked in light and dark mode. Muted "too few" rows stay at least 4.5 : 1.
- Never colour alone: every badge is a word, item marks have accessible names, and the bars repeat their numbers as
  text.
- Meters are `role=meter` with `aria-valuenow/min/max` and a label ("Demo trades, 12 of 150"). The diverging R bar
  has an SVG `<title>`, and the numbers sit next to it.
- Tap targets: jump chips, gate rows, breakdown rows, Copy and Go to Halt are all at least 44 px. The select is a
  native `<select>`.
- Each section is a `section` with an `h2`. Gates are a list of `details`/`summary` pairs, so keyboard and screen
  reader users can open them natively. Focus is a 3 px `:focus-visible` ring.
- Live updates replace text in place. Gate rows keep their open or closed state across refreshes. The relative
  times ("2 days ago") update every 60 s without reflow.

## Telegram

Nothing new is required. The existing one-time stop-rule alert (health `stop_rule` FAIL) should carry the screen's
words when it is next touched:

```
STOP RULE BREACHED
One trade lost 5.6% of equity, more than the 5.0% weekly limit · icm-demo · demo · Tue 13 Oct 16:20 BST (15:20 UTC)
The design says the project stops. Nothing was halted automatically; exits keep running.
Send /halt to stop new entries. Details: dashboard → Progress.
```

## Decisions

1. **One tab with three sections, not three tabs.** The options were three tabs, one tab with a segmented control,
   or one tab with a scrolling page. With 9 tabs already, a phone shows about four, so new tabs would sit off-screen.
   A segmented control hides two thirds of the content and the browser's find. The single page with jump chips is
   the simplest design that keeps working. I would change this if the owner opens Results daily and the other two
   rarely: then Results moves to its own tab.
2. **A breach is amber, not red.** Red means "trading is stopped now". A breach stops nothing until the owner halts.
   Showing it red would teach the owner that red can be ignored. Its weight comes from the word badge, `role=alert`,
   the Overview banner and the Telegram alert instead. If the owner later makes a breach halt automatically (the
   original BACKLOG 8 wording, an owner decision), it becomes red like the other halts.
3. **Gates are evaluated on request, not read from `gate_report.json`.** Only the CLI writes that file, so it can be
   any age. Health already evaluates the same code every 60 s, so the cost is known.
4. **No R-to-money projection, and no "earliest live date".** A forecast built from a trade rate would read as a
   promise. Only calendar facts are shown ("earliest Thu 1 Apr 2027" for a day count).

## API gaps (for the implementer; then `python scripts/export_openapi.py && npm --prefix web run gen:api`)

1. **`GET /api/gates` → `GatesView`**: `generated_utc`; `phase`; `gates: list[GateView]`; `stop_rule: StopRuleView`;
   `cant_check: str | None` (owner text, from mapped `errors`).
   - `GateView`: `gate` (id), `met`, `recorded`, **`recorded_utc`** (from `gate_log`, not in `GateStatus` today),
     `items: list[GateItemView]`.
   - `GateItemView`: `name`, `met`, `required`, `need`, `evidence` (**without** the owner's `detail`),
     **`proposed: bool`**, **`progress: {value, target, unit} | None`** (trades, days, months, fills), and
     **`earliest_utc`** for the day items.
   - **`proposed` needs a settings field.** Today "PROPOSED" is only a YAML comment. Add `gates.signed_off:
     list[str]` (threshold names the owner has signed off; empty by default) and mark every other proposed threshold
     `proposed=True`. Signing off is the owner's decision, so the field only records it.
2. **`StopRuleView`**: `status: "ok" | "not_started" | "breached" | "cant_check"`; `cant_check: str | None`;
   `months`, **`after_months`**, `pooled_trades`, **`min_pooled_trades`**, **`confidence`**, `lower_bound` (per-trade
   return, fraction), `worst_loss_frac`, **`weekly_cap`**, **`clock_start_utc`** and **`clock_source: "paper_gate" |
   "first_trade" | None`**; **`breach: list["long_run" | "single_loss"]`**; **`incidents: list[{account, mode,
   closed_utc, loss_frac}]`** (worst first, at most 5, the account with its digits cut) with `incident_count`;
   **`imputed: {n, charged_usd, unpriced}`**. Today `StopRuleStatus` has only formatted `reasons` and `detail`
   strings, which carry UTC times and the account and cannot be localised. Also: a closed-trade write reported lost
   (an engine's `closed_records_lost`, which health already FAILs on) must make the status `cant_check`, because a missing loss can hide an incident.
3. **`GET /api/attribution` → `AttributionView`**: the `AttributionReport` fields except `trades` and `calibration`
   (calibration stays on Health); **`stale: bool`** (from the scheduler's `at` plus `max_late_hours`, so the browser
   holds no policy); **`next_run_utc`**; `live` keyed by the account with its digits cut; **`r_money: {amount,
   currency, risk_fraction} | None`** (1 R at today's normal risk × equity, from the account view; null when either
   is missing); `cant_check: str | None`.
4. **`GET /api/trades/summary` → `list[TradesSummary]`** (recommended; it serves the north star, ADR 0004): per
   account `{account, mode, currency, days: 30, net, n, trading_days, days_at_target (net ≥ 50 in the account
   currency, the ADR's figure from settings), best_day, worst_day, by_day: list[{day, net, n}], gross_rows}` on the
   opportunity day (`report_days.py`, the monthly report's). Rows that are not net are flagged, never dropped.
5. **Stale note in `attribution.py`.** `build_report` always appends "live realised R per position is not recorded
   yet (… BACKLOG item 8)". The engine now writes `ClosedTrade.r`, so the note is out of date and would mislead on
   the screen. This is for the performance-analyst, and the quant reviewer should check the change.

## Risks raised

- **The dashboard header shows the signed-in email** (`App.tsx`, `currentUser()?.email`). It is the user's own, but it
  appears in every screenshot the owner shares from a phone. Recommendation: show the role only in the header and the
  email on the Account tab. This needs a one-line change in its own lane and an owner yes or no.
- **Multiple comparisons on Results.** About 30 cells at |t| ≥ 2 will show one or two "beyond luck" rows by chance.
  The footnote says so, and the order is fixed. The quant reviewer should sign off the verdict words before the
  build.
- **PROPOSED thresholds are still unsigned** (OWNER_GUIDE "Decisions waiting for you"). The screen makes that visible.
  It does not decide it.
