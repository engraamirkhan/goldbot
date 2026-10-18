# Limits, TradingView link and research progress (read-only "just show me")

Owner need: see, without acting, (a) what the risk limits are now and why they last changed, (b) whether the
TradingView link works, (c) how research is going. Nothing here has a button that changes anything. Style as
`approval-card.md` and `health-research.md`: red only for things that stop trading, state in words as well as colour,
every number with unit and time (owner's zone, UTC on hover), fixed-height cards so live updates never shift layout.

Source note: ADR 0031/0032 and the `limit_changes` / `adaptive_*` tables are not on this branch. Field names below are
the proposal; the implementer maps them to the real columns and corrects this note.

## Placement

- **Overview** gets one new strip, "Limits", under the balance card: one line per account. Tap opens the full card.
- New tab **Limits** (after Progress, so it stays on screen when the tab row scrolls): card A per account.
- **Feeds** tab gets card B "TradingView" at the top (it is a feed). Overview shows one chip "TradingView: signed in / expired" beside the other feed chips.
- **Research** tab gets card C "Today" at the top; Overview shows the same one-liner.
- Amber, never red, for TradingView problems: it is an optional second feed and cannot stop trading (`tv_pull_health.py`: WARN only). Red in A only for "trading stopped by the daily/weekly cap".

## A. Risk limits (per account; 360 px one column, 960 px+ accounts side by side)

```
Demo account (icm-demo)            Profile: demo-2026-10-18
Updated 14:05 BST (13:05 UTC)
+----------------------------------------------+
| Today's loss limit   £50  (5.0% of £1,000)   |
|  used £12 -- 24%   [=====-----------]        |
| This week's limit    £120 (12.0% of £1,000)  |
|  used £12 -- 10%   [==--------------]        |
| Smallest trade size  0.01 lot (cap 0.05 lot) |
| Auto-revert at 8%:   OFF  (or ON since ...)  |
+----------------------------------------------+
Last change: 17 Oct 09:12 BST -- daily limit £40 -> £50
Why: "balance grew to £1,000 after broker reconcile"   [See all changes]
```

Rows, in order:
1. **Header**: account name, class chip (Demo / Live), profile name (`default` or `demo-2026-10-18`), "updated" time.
2. **Today's loss limit**: £ cap, % of balance (with balance date), £ used and % of cap as bar plus number (not colour alone). "Day resets 00:00 UTC (01:00 BST)".
3. **This week's limit**: same shape; "Week resets Sun 22:00 UTC".
4. **Smallest trade size** (min-lot cap): "0.01 lot minimum; largest allowed 0.05 lot". Hint when relevant: "A 0.01 lot trade with an 8-point stop risks £8, within your £50 day".
5. **8% revert**: "Auto-revert at 8%: ON. Limits go back to default if the account falls 8% from its peak." State chip ON/OFF; when it has fired: "Active since 16 Oct 11:30 BST: account was down 8.2% from peak".
6. **Last change**: time, old -> new in £ and %, who or what made it (adaptive rule, owner, ADR), and the reason in one plain sentence. "See all changes" opens a list, newest first, 20 rows, same three parts; read-only.

States:
- **Normal**: neutral card.
- **Near cap** (>=75% used): amber chip "Close to today's limit"; bar amber plus the word.
- **Cap hit** (red, it stops trading): "Daily loss limit reached. New entries are stopped until 00:00 UTC (01:00 BST). Exits keep working."
- **Revert active**: neutral info chip, not red: "Limits are on the default profile (8% revert active since ...)".
- **Loading**: skeleton of the same height. **Empty** (no change logged): "No limit changes yet. Limits are the profile defaults." **Error**: "Could not read the limits (<reason>). Engines fail closed on this; see Health." plus Retry. **Stale** (>60 s): grey chip "Updated 5 min ago".

## B. TradingView link (Feeds tab, top card)

```
TradingView                      [ Signed in ]   (or [ Expired ], amber)
Last successful pull: 06:02 BST (05:02 UTC), 8 h ago
Indicators found: 3    Live bars: 1,204 (last 24 h)    History bars: 52,310
  Name              Live 24h  History   Repaint check
  Supertrend        288       18,000    Passed
  RSI Divergence    288       17,310    Not checked yet
  Volume Profile    0         17,000    Not run (history only)
```

- Status chip words: **Signed in** (dot + word), **Expired** (amber), **Not set up** (grey: "TradingView is optional; nothing to do"), **Unknown** (amber: state unreadable, treated as expired like `tv_pull_health`).
- Indicator names only; never plot values (existing rule in `TvIndicatorStatus`).
- Repaint copy from `repaint_status`: `validated` -> "Passed: history matches live"; `unvalidated` -> "Not checked yet: not used for trading until it passes"; `none` -> "No history imported"; new `failed` -> amber "Repaints: not used".
- If `gaps_24h > 0`: "3 missed bars in 24 h".
- **Expired cookie** (amber, `role=status`): "TradingView sign-in expired on 18 Oct 06:02 BST. Trading is not affected. History is kept, but no new TradingView data arrives. To fix, run this on the brain:" then the single-line command `goldbot accounts set tradingview-session` with a Copy button (command pasted from the rendered text). Cleared by the next successful pull.
- **Stale** (no pull for >36 h, status still ok): "No pull for 40 h; one is expected daily."
- Loading skeleton; error "Could not read TradingView status"; empty "No indicators yet. Alerts or a chart export have not arrived."
- Below 720 px the table becomes one labelled card per indicator.

## C. Research today (top of Research tab; one-liner on Overview)

```
Research today (18 Oct, UTC day)
Trials run: 6   Passed all gates: 0   Failed: 5   Running: 1
K = 38 of 60 trials this quarter (22 left, quarter ends 31 Dec)
Latest: "4h trend, session filter" -- Failed: costs eat the edge (net -0.03 R)
```
- Verdict words: Passed all gates / Failed (first failing gate in words) / Needs more data / Running. A pass with no cost source: "Passed, but costs not recorded: not counted".
- K amber at <=10% left ("Plan the rest carefully"); at 0: "Budget used up: no new trials until <date>".
- Empty: "No trials today." Error: reuse `plan_error`; the rest of the screen still renders.

## Telegram (FYI, no buttons; silent unless protection changes)

Limit changed (silent):
`Limit change (demo): daily loss limit £40 -> £50 (4% -> 5% of £1,000). Why: balance reconciled to broker. Week limit unchanged at £120. 17 Oct 09:12 BST`

Revert fired (with sound):
`Safety revert (demo): account down 8.2% from peak, limits back to default. Day £30, week £90. Min lot unchanged. 16 Oct 11:30 BST`

Cap hit: `Daily loss limit £50 reached (demo). New entries stopped until 01:00 BST. Exits keep working.`

TradingView expired (one per expiry; replaces `NOTICE_TEXT`): `TradingView sign-in expired. Trading is not affected. Fix when convenient: goldbot accounts set tradingview-session`
Recovered: `TradingView is back: last pull 06:02 BST, 3 indicators.`

Research digest (once, 20:00 BST): `Research today: 6 trials, 0 passed, 5 failed, 1 running. K 38 of 60 (22 left).`

Rules: at most 2 lines, units on every number, £ and % together, local time with zone name, no ids or code except the one fix command.

## API fields needed

1. `GET /api/limits` -> `LimitsView { generated_utc, accounts: [LimitAccount] }`.
   `LimitAccount`: `account_id`, `account_class` ("demo"|"live"), `profile`, `balance_gbp`, `balance_as_of`,
   `daily_cap_gbp`, `daily_cap_pct`, `daily_used_gbp`, `daily_resets_utc`, `weekly_cap_gbp`, `weekly_cap_pct`,
   `weekly_used_gbp`, `weekly_resets_utc`, `min_lot`, `max_lot`, `min_lot_risk_gbp: float|None`, `revert_8pct_enabled`,
   `revert_active`, `revert_since: datetime|None`, `revert_reason: str|None`, `cap_hit: "none"|"daily"|"weekly"`,
   `last_change: LimitChange|None`, `error: str|None`.
   `LimitChange { ts, field, old_value, new_value, unit, source ("adaptive"|"owner"|"adr"|"revert"), reason (plain sentence), adr: str|None }`.
2. `GET /api/limits/changes?account=&limit=20` -> `list[LimitChange]` from core.db `limit_changes` / `adaptive_*`.
3. Extend `TvStatusView`: `signin: "ok"|"expired"|"not_set_up"|"unknown"`, `last_pull_utc`, `expired_since_utc`, `stale: bool`,
   `fix_command`, `live_bars_24h`, `history_bars`. `TvIndicatorStatus.repaint_status` gains `"failed"`. Source `tv_auth.db` and the
   `tv_indicators` origin column; read-only, never opens `tv.db`.
4. `ResearchView.today: ResearchToday { date_utc, run, passed, failed, running, latest: TrialRow|None, k_used, k_budget, k_left, quarter_start, quarter_end }`
   (server-side so the UTC-day boundary lives in one place).
5. Telegram notice kinds `limit_change`, `limit_revert`, `limit_cap_hit`, `tv_recovered`, `research_digest`; copy as above, golden-string tests.
6. WebSocket event `limits` so the card refreshes on change (invalidate `["limits"]`).

## Tests for the builder

Vitest per card state (normal, near cap, cap hit, revert active, expired cookie, stale, empty, error, loading); no card
renders plot values or an email; contrast check light/dark at 360 px; e2e: a seeded limit change shows with its reason,
and Copy puts the single-line fix command on the clipboard.
