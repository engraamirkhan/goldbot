# Entry status panel

The question the owner asks most on a phone: **can it take new trades right now, and if not, why and what fixes it?**
The Overview answers it first, one block per account, in plain words, with the next step and, where it helps, the
Runbook section. Exits never depend on any of this; every block says so.

Code: `goldbot/api/entry_status.py` (reasons, words, next steps), `AccountSummary.entry_status` on `GET /api/accounts`,
`web/src/screens/EntryStatus.tsx` (layout), `web/src/lib/entry.ts` (time text, badges). Decision record: ADR 0011.

## Data

| Shown | Source |
|---|---|
| State, headline, reasons, next steps, Runbook links | `AccountSummary.entry_status` (`EntryStatus`, `EntryBlock`), built by the API; the browser holds no policy |
| Owner halt | `state/control.json` (the approval bus) |
| Supervisor halt | `Supervisor.engine_should_halt`: exactly what the engines read (missing, stale or unreadable heartbeat included) |
| Drift system halt | `state/drift.json` (unreadable halts, as in the engine) |
| Engine reasons (BACKLOG 43) | `engine_<account>.json` `entry_blocks`: the RiskGate's account-level codes (`RiskGate.account_reasons`, rollover and the weekend cut included), written at every state write and heartbeat; the API words them and decides nothing. Missing: `entry_reasons_missing` (act), never OPEN |
| Details beside them | `ts`, `sessions` (reopen time), `blackout` (title), `spread_points`, `open_positions`, `windows` (times), `last_tick_error_at` + `tick_errors_total` (note), `throttle.agents[*].blocked` + `cooldown_until`, `bridge_compat.commit_differs` (note) |
| A strategy's last refusal | `entry_refusals`: per agent, the last per-trade refusal (fx conversion, minimum lot, margin, combined exposure, absolute daily limit, heat cap) and its time; shown as that strategy's block "since" then, until the gate next allows it |
| Loss limits | `account_view.loss_budget.caps`: one block per spent cap (account daily/weekly, absolute, combined), each with its amount in the account currency and its own reset (00:00 UTC; Sunday 00:00 UTC for weekly) |
| Drawdown texts | `RiskLimits.dd_stage2` and `SupervisorLimits.dd_stage2` from settings ("12%" is never hard-coded) |
| Thresholds (stale tick, tick ahead, spread, positions) | `RiskLimits.from_settings` (the RiskGate's own limits), never a copy |

Free text from the engine (probe reasons, bridge commits, supervisor file names) never reaches the screen: only mapped
codes, numbers, strategy ids and the news title. No login, server or host is ever shown.

## States

| `state` | Badge (word + colour) | Meaning | Headline |
|---|---|---|---|
| `open` | **OPEN**, green | Nothing blocks a new trade | "Can take new trades" |
| `partial` | **PARTLY OPEN**, amber | The account can trade; named strategies are held back (throttle) | "Can take new trades, except trend-g0" |
| `waiting` | **PAUSED**, amber | No new trade; every reason clears by itself | "No new trades for now: Market closed"; with `clears_at` (every reason has a time): "· clears Sun 01:00 your time (in 2 d 11 h)", the last of them |
| (stale) | **UNKNOWN**, grey | The data is older than 15 s or the last refresh failed: the rows are the last report, under a banner | as reported |
| `blocked` | **BLOCKED**, red | No new trade and something needs the owner or an operator | "No new trades: Broker clock differs from the expected time zone (+1 more)" |

Red only for blocked (something must be done before trading resumes). Every block is a word badge first, colour second.
Each reason is `act` ("Needs you" tag, red rule) or `wait` (amber rule); `act` reasons are listed first.

## Layout

Phone (360 px, single column, 16 px gutters):

```
Entry status
┃ [BLOCKED] icm-demo  demo              as of 14:02:31 ┃  word badge, account, mode; time in the owner's zone,
┃ No new trades: Broker clock differs from the        ┃  UTC on hover
┃ expected time zone (+2 more)                        ┃  headline, 16 px semibold
┃ │ [Needs you] Broker clock differs from the        ┃
┃ │ expected time zone                                ┃
┃ │ See the Runbook: Server clock. Entries resume a  ┃  next step, 14 px muted
┃ │ minute after the clocks agree.                    ┃
┃ │ Runbook: Server clock (US-EU DST gap weeks) ↗     ┃  44 px tap target, opens the RUNBOOK section
┃ │ Market closed · opens Sun 23:02 your time (in 5 h)┃  time in the owner's zone, weekday when not today,
┃ │ Nothing to do: it reopens by itself.              ┃  how long until; UTC on hover
┃ │ trend-g0 paused after 3 losses in a row ·        ┃
┃ │ until 14:30 your time (in 28 min)                 ┃
┃ Exits keep running: open trades keep their stop,   ┃  always last
┃ target and automatic exits.                         ┃
```

Desktop: the same blocks in the Overview column (one per account, stacked), max width of the main column. It sits
above the tiles, the halt control and the account cards.

## Cases

| Case | Shown |
|---|---|
| Loading | Heading plus one placeholder block of the final height (`aria-busy`, "Loading entry status"): no shift when it fills |
| Server unreachable or data older than 15 s (after data) | Amber banner, `role=alert`: "Can't reach the server since 14:02; below is the last report." Every badge turns grey **UNKNOWN**; the reasons stay as last reported |
| Error (no data yet) | Amber, `role=alert`: "Entry status unavailable: the dashboard can't reach the server (retrying). Exits keep running on the engine." A failed refresh after data keeps the last data |
| Empty (no engine file) | Amber: "No engine is reporting, so no new trades can open. Start the engine; the Health tab shows what is running." |
| Engine silent, market closed now (published session table) | Paused: "Market closed (engine idle while closed) · opens …"; the header's "as of" is the last report. The engine writes nothing while closed, so it is never blamed |
| Engine silent past the supervisor's 60 s heartbeat, market open | Paused: "Engine status unknown: no recent report · since …" (wait), never OPEN |
| Engine silent past health's ENGINE_FAIL_S (45 min) with the market open throughout, or a report stamped > 60 s in the future, or no `ts` | Blocked: "Engine not reporting · since 14:02 your time (50 min ago)"; exits line: "Open trades keep the stop and target placed at the broker; the engine's own exits (time exit, weekend rule) resume when it reports again." Other engine flags are not shown (they are stale) |
| Status could not be built (a bug, an unreadable input) | Blocked: "Entry status couldn't be worked out", pointing to the Health tab; never OPEN |
| Older API (no `entry_status`) | Grey: "Entry status not reported by this server version." |
| Note (never blocks) | Muted line under the reasons: a tick error the engine carried on from in the last 15 min; bridge and brain on different commits |

## Reasons and words

The full table is `REASONS` in `goldbot/api/entry_status.py`; a test fails when the RiskGate or the supervisor gains a
reason without words. Examples:

| Code | Text | Next step | Kind |
|---|---|---|---|
| `market_closed` | Market closed · opens {time} | Nothing to do: it reopens by itself. | wait |
| `server_clock_mismatch` | Broker clock differs from the expected time zone | See the Runbook: Server clock. … | act |
| `throttle_loss_streak` | trend-g0 paused after 3 losses in a row · until {time} | Nothing to do: it resumes after its cooldown or a winning shadow trade. | wait (strategy only) |
| `daily_loss_limit_abs` | Today's £50 loss limit reached · resets {time} | Nothing to do: it resets at the start of the next day. | wait |
| `fx_conversion_unavailable` | Can't convert dollars to the account currency right now | Check the MT5 terminal is logged in and XAUUSD is in Market Watch. | act |
| `tick_error` | The engine hit an error reading prices | Usually clears at the next clean bar; if it repeats, restart the engine. | wait |
| `bridge_incompatible` | The MT5 box runs an incompatible bridge version | Update the MT5 box to the same version as the brain. | act |
| `heat_cap`, `tier2_blackout`, `reopen_window`, `holiday_thin` | worded in advance for the lanes adding them | | wait |
| anything else | New entries paused ({code in words}) | See the Runbook; the Health tab may say more. | act |

Copy rules: no jargon codes, no times inside `text` (the time is its own element so it can be shown in the owner's
zone), every amount with its currency symbol, "Nothing to do: …" for what clears by itself, a verb first for what
needs the owner.

## Accessibility

- Contrast: the badge and rule colours are the strip's AA tokens (`--ok-fg`/`--ok-tint`, `--warn-fg`/`--warn-tint`,
  `--bad-fg`/`--bad-tint`) in light and dark; body text is `--fg` on `--surface`.
- Never colour alone: badge word, "Needs you" tag, "Nothing to do" wording.
- Each account is an `article` named "Entry status <account>"; the heading is an `h2`; links are real links with a
  visible name ("Runbook: Server clock …") and a 44 px tap height; keyboard focus follows document order.
- Live updates (5 s poll, the relative times every 30 s) replace text in place: fixed structure, no layout shift.

## Consistency

The safety strip on the Approvals tab and the proposal card's warning use the same API reasons
(`entryBlockers` in `web/src/lib/entry.ts`), so the Overview and Approvals never disagree. A strategy's own throttle
counts on that strategy's proposal card only.

## Viewer and approver

Only the owner re-arms: an owner halt tells the owner "Re-arm on this page when ready" and everyone else "The owner
re-arms on this page when ready."

## Engine reasons (BACKLOG 43, ADR 0011)

The engine publishes which reasons apply (`entry_blocks`, the running gate's own list) and the API only words them, so
the panel cannot disagree with the gate. Two presentation rules stay in the API: with `market_closed` listed,
`stale_data` is not shown (a closed market has no fresh prices by design, BACKLOG 59), and the engine's
`supervisor_halt` is not shown when the supervisor's own reasons already are.

The entry windows (BACKLOG 17/18) are shown when the engine lists them; whether a window is on is the running gate's
setting, so the settings the API reads never hide one. Times are the engine's own (`tier2_until`, `reopen_until`,
`holiday_name`, `holiday_until`); the API's settings are a fallback for wording and times only:
`reopen_window` (wait, "until"), `holiday_thin` (wait, names the holiday, "until" server midnight of the first unlisted
day), `tier2_blackout` (wait, "until" the release + after-minutes), `calendar_unavailable` (act, "since"; holiday-list
or economic-calendar wording chosen from the engine's reason prefix, the reason itself never shown).

## Telegram `/status` and the daily digest

`/status` is built by `goldbot/telegram/entry_text.py` from `entry_status.statuses_from_state`, the same inputs and the
same `account_entry_status` as the panel, so both channels say the same words. Plain text (no parse_mode), one block per
account, at most 15 lines each:

```
Entry status · Wed 14 Oct 15:00 BST (14:00 UTC)

BLOCKED · icm-demo (demo) · as of 15:00 BST            badge word, config name, mode, last report (owner's zone)
No new trades: Broker clock differs ... (+1 more)      the API's headline (+ "· clears ..." when it says)
Needs you:                                             act reasons first, each with its fix on the next line
• Broker clock differs from the expected time zone
  → See the Runbook: Server clock. ...
Waits:                                                 wait reasons with their time; a timed "Nothing to do" fix omitted
• Market closed · opens Sun 23:02 BST (22:02 UTC, in 1 d 12 h)
Exits keep running: open trades keep their stop, target and automatic exits.

Approval mode: propose · pending proposals: 0
```

Times are in `telegram.owner_tz` (default Europe/London) with UTC beside them; an unknown zone shows UTC only. Blocks
that do not fit are counted ("+N more on the dashboard"), never dropped silently. "on this page" in a fix reads "on the
dashboard (Overview)". A config name with 5+ digits is cut to its last two ("icm-demo-…67"). No engine: "No engine has
reported yet ..."; a failed build: BLOCKED with "Entry status couldn't be worked out" (never OPEN). The daily digest
carries one line: "Entries not open: icm-demo PAUSED (Market closed)" or "Entries: open on every account".
