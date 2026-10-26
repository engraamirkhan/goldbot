# Daily learning report and weekly digest (Telegram)

Lane L5a of the closed learning loop. Design note by the ui-ux-designer, 2026-10-26. Status: **spec, ready to build**.
Builds on the existing morning digest (`goldbot/telegram/digest.py`, ADR 0012 copy rules, `docs/ux/entry-status.md`
words) and the exploration program (ADR 0043, `docs/research/prereg-2026-10-25-high-volume.md` H-60/H-61/H-62).

The owner reads this on a phone, once, before London opens. The question it answers in ten seconds:
**"Did the labs run, what did learning cost, is anything near a line, and do I need to do anything?"**

## 1. Decisions taken in this spec

| # | Decision | Why | What would change it |
|---|---|---|---|
| D1 | **One morning message, not two.** The learning report *replaces the body* of today's 06:45 UTC digest (same `DigestSchedule`, same state file, same 6 h late rule). Health, halts and "For you" stay; the exploration lines take the place of the proposals/trades lines on `probe_only` accounts. | Two bot messages in the same minute train the owner to skim both. The current digest's "Trades closed" reads `closed_trades_*.jsonl`, which by design (research E9, `population.py` isolation) never holds exploration trades, so on the labs it reports "none" while 60 trades closed: actively misleading. | A real-money account going live: its proposal/approval line (approved, rejected, expired) comes back above the labs, because that is the decision the owner makes. |
| D2 | **Weekly digest replaces the Sunday 06:45 UTC daily.** Sunday's daily would only say "weekend, market closed". | One message per day, never more. Saturday 06:45 still carries Friday's day. | None foreseen. |
| D3 | **Arms are described, never judged, before their read.** No p-values, t, "probability best", "winning/losing", "significant". Words: "ahead", "behind", with n and "first read at 300 trades". | The prereg fixes **one read per arm, at its 300th admissible trade, no interim reads**. A daily league table is an interim look by the owner and by every agent reading the message; the words must not invite acting on it. The only pre-registered rule that can act before 300 is the **per-arm stop** (scalp −30R in 300, intraday −15R in 100, swing −10R in 40), so the report shows distance to that stop: it is the one number that will actually change something. | Quant-reviewer ruling that a descriptive owner view is an interim read: then the arm block moves to the weekly only. **Quant-reviewer sign-off on §3.4 wording is required before build.** |
| D4 | Bandit share (P(best)) is shown only as **"share of the lab's budget"**, never as a probability of success. | Thompson share is an allocation, not evidence. | — |
| D5 | Telegram text only: no inline keyboard on these messages at all (not even "open dashboard"). A deep link is plain text. | Constraint: nothing that places trades; zero buttons removes the class of mistake and of misclick. | — |
| D6 | Accounts appear by **role label**, not slug: `icm-probe` = **Scalp lab**, `icm-demo` = **Day/swing lab**, `vantage-demo` = **Vantage lab**, a real-money account = **Live (ICM)**. Mapped in one table in code; an unmapped account prints "Other account", never its slug, login, server or host. | Plain words; nothing that identifies an account outside the repo; the slug's meaning changed on 2026-10-26 (per-style hosts) and will change again. | — |

## 2. The daily report (06:45 UTC, about 07:45 UK in summer, 06:45 in winter)

### 2.1 Mock: a normal weekday (Tue 27 Oct 2026, UK on GMT)

```
☀️ Mon 26 Oct results · day ended 22:00 UTC (22:00 UK)
✅ Health 41 of 41 ok · Halts: none · Entries open on 2 of 2
Scalp lab: 214 signals · 63 taken · 61 filled · 58 closed −£17.60 · −3.9R
  costs £10.90 (2.4R): spread £6.80 · slippage £1.40 · commission £2.70
  refused: 41 slots full · 22 spread wide · 9 news window
  down 2.1% (£230) from peak · warns at 6% (£657) · pauses at 8% (£876)
Day/swing lab: 37 signals · 9 taken · 9 filled · 7 closed +£21.60 · +1.4R
  costs £2.10 (0.1R) · refused: 14 slots full · 8 news window · down 0.8% (£88)
Arms, net R so far (trades) · too early to judge, first read at 300
  ahead: MB30 +6.2R (41) · S2A +3.1R (118)
  behind: S3B −11.4R (126; stops at −30R) · I2B −5.0R (22; stops at −15R)
Learner 21:30 UTC: scalp S2A 18→24%, S3B 9→5% of budget · intraday, swing no change
Month: +£112.40 · +9.8R over 18 trading days · open now 4 trades (0.04 lot)
👉 For you: nothing
```

14 logical lines. Every logical line wraps to at most 2 rows on a 360 px phone at Telegram's default size
(about 40 characters a row); the whole message fits one and a half phone screens.

### 2.2 Line order and budget (hard rule: ≤ 15 non-empty logical lines)

| # | Line | Always? | Priority if over budget (1 = never cut) |
|---|---|---|---|
| 1 | Header: which trading day, when it ended (UTC + owner's zone) | yes | 1 |
| 2 | Health · halts · entries | yes | 1 |
| 3-6 | Per lab: result line + up to 3 detail lines (costs, refused, drawdown) | per enabled account | result 1; drawdown 1 when ≥ warn line, else 3; costs 2; refused 3 |
| | Second lab: result + 1 merged detail line | | as above |
| | Broker cost comparison (only when ≥ 2 brokers filled) | conditional | 3 |
| | Arms header + ahead + behind | when any arm has ≥ 10 trades | 2 (behind with a stop distance is kept longest) |
| | Learner | yes | 2 |
| | Month · open now | yes | 2 |
| last | 👉 For you | yes | 1 |

Over budget: drop priority 3 lines, then merge detail lines, then drop priority 2. Health, halts, any line at or past a
risk line, and "For you" are never cut. A cut line adds "· more on the dashboard" to the lab's result line.

### 2.3 Copy rules (apply to both messages)

- **Units on every number**: money in the account's currency with its symbol (£ for the GBP labs); R with the suffix `R`
  and an explicit sign (`+1.4R`, `−3.9R`, true minus U+2212); percentages with `%` and a £ figure beside them for any
  risk line; counts with their noun (`63 taken`, `(41)` only inside the arms block, whose header says "(trades)").
- **Every time with its zone**: UTC first, owner's zone (`telegram.owner_tz`, default Europe/London, printed "UK") in
  the header; other times UTC only (one zone per line is enough on a phone).
- **Never sum across currencies** (BACKLOG 28). A USD lab shows `$` and, in brackets, `≈ £` at the day's close rate
  with the word "≈"; the month line is per currency.
- **Net only, labelled as such**; a closed trade without a broker net figure is shown apart: "2 closed without costs
  recorded (gross +£1.10)", never folded into the net.
- **Emoji are never the only signal**: every emoji is followed by its word (✅ Health, ⚠️ Warning, ⏸ Paused,
  🛑 Halted). Red (🛑) only for something that stops all new trades on an account (halt, −12% rail, unreadable halt
  file). The −8% exploration pause is ⏸ (amber meaning): the cost probe and exits still run.
- **Plain words** for codes: refusal codes use the entry-status word map (`goldbot/api/entry_status.py`), never a raw
  code; unknown code: "other reason (n)". Arm ids stay as ids (the owner knows them from the dashboard) but never with
  family internals.
- **No identifiers**: no login, server, host, Telegram id, email, client order id or position id.
- Rounding: £ to 2 dp; R to 1 dp (sum) or 2 dp (per trade); % to 1 dp; lots to 2 dp; budget shares to whole %.

### 2.4 States (exact copy)

| State | Where | Copy |
|---|---|---|
| Normal | lab result | as mock |
| No signals while the market was open | lab result | `Scalp lab: no signals in 22 h of open market ⚠️ Warning: check the feed` (also raised by health; the ⚠️ is here because zero is abnormal on a lab) |
| Signals, nothing taken | lab result | `Day/swing lab: 12 signals · 0 taken` + the refused line (the reason is the news) |
| Taken, nothing closed yet | lab result | `… 3 filled · 0 closed (3 still open)` and no P&L figure (never "£0.00") |
| Market closed (weekend) | replaces all lab lines | `Market closed Sat–Sun: no trading. Sunday-evening trades show in Tuesday's report.` (existing `WEEKEND_LINE`) |
| Market closed (holiday / early close) | per lab | `Scalp lab: market closed (US Thanksgiving) · nothing to report`; early close: result line + "(early close 18:00 UTC)" |
| Drawdown ≥ 6% (warn) | drawdown line moves directly under the result line, never cut | `⚠️ Warning: down 6.4% (£701) from peak · pauses at 8% (£876)` |
| Drawdown ≥ 8% (exploration paused, ADR 0043 X4) | lab result line is replaced | `⏸ Scalp lab paused since Mon 14:20 UTC: down 8.3% (£909) from peak` / `  arms stopped · cost probe and exits still running · resumes: <engine's resume rule>` (the resume text comes from the engine/state, never hard-coded; until the rotation ADR exists it reads "when the drawdown is back under the clear line") |
| Drawdown ≥ 12% (halt rail) | line 2 and the lab line | line 2: `🛑 Halted: Scalp lab at 12% drawdown, no new trades · exits still run`; lab: `🛑 Scalp lab halted Mon 16:05 UTC: down 12.1% (£1,325) · re-arm on the dashboard (Risk)` |
| Owner / supervisor / drift halt | line 2 | `🛑 Halts: owner halt since Mon 09:12 UTC` (as today: who set it is not printed) |
| Account not enabled (e.g. Vantage before live) | — | omitted from the daily; listed once in the weekly ("Not running: Vantage lab") |
| Arms: none with ≥ 10 trades | arms block | `Arms: too early, no arm has 10 trades yet (most: S2A 7)` (one line) |
| Arm reached its stop | behind line, first | `S3B retired at −30.4R after 212 trades (its pre-set stop)` |
| Arm reached 300 | arms block | `S2A reached 300 trades: read due, result in the weekly` (the report never computes the read; it relays the evaluator's recorded verdict when it exists: `S2A read: passed` / `S2A read: not passed, retired`) |
| Learner did not run / weights stale (> 3 days, `MAX_WEIGHTS_AGE`) | learner | `⚠️ Learner did not run last night: equal shares in use` |
| Learner ran, nothing moved ≥ 2 points | learner | `Learner 21:30 UTC: no share moved by 2 points or more` |
| Sunday model retrain happened (H-62) | learner, appended | `· scalp model: new one kept (beat the old on 2 unseen weeks)` / `· scalp model: old one kept` |
| A source unreadable | that line only | `Scalp lab: could not read the trade ledger (OperationalError)` (existing `_section` pattern; the rest still sends) |
| Health unknown | line 2 | `❔ Health: unknown (the check did not run)` |
| Health failing | line 2 | `⚠️ Attention: fail bridge_heartbeat · warn spread_profile` (≤ 5 names, "+N more") — the one-line system-issue rule |
| Sent late (≤ 6 h) | header | header unchanged (it names the trading day); nothing extra |
| Real-money account live | above the labs | `Live (ICM): 3 proposals · ✅ 2 approved · ❌ 1 rejected · 2 closed +£14.20 · +0.9R` (existing proposals/trades lines, labelled) |

### 2.5 Field definitions (daily)

"Day" = the opportunity day before the send (`report_days.day_before`, D-2 22:00 to D-1 22:00 UTC), the same day the
dashboard and monthly report use. Lab figures are from the exploration ledger only (`aux.db exploration_obs` and
`exploration_trades`); the P0 cost probe is excluded from signals/taken/filled/closed and from arms, and included in the
broker cost comparison. All money in the account currency as the broker reports it.

| Field | Definition | Source | Unit |
|---|---|---|---|
| signals | exploration candidates whose `decision_ns` is in the day, quarantined rows excluded | `exploration_obs` | count |
| taken | of those, `taken = 1` | `exploration_obs.taken` | count |
| filled | taken candidates with a broker fill (`fill_price` set) | `exploration_trades` | count |
| closed | trades whose `exit_utc` is in the day (may have been filled the day before) | `exploration_trades.exit_ns` | count |
| P&L | Σ `pnl` (net, broker deals) over closed; rows with `pnl` null shown apart as gross | `exploration_trades.pnl` | £ |
| R (result line) | Σ per-trade R over closed, per-trade R = `pnl / stop_loss` (the loss at the gate's stop, same currency) | `exploration_trades` | R |
| costs £ | Σ over closed of spread + slippage + commission + swap | below | £ |
| spread £ | `spread_usd × oz × usd→ccy` at fill (paid once, entry) | `ExplorationRecord.spread_usd`, lots | £ |
| slippage £ | adverse part of `(fill_price − requested_price) × side × oz × usd→ccy`, entry and exit; favourable slippage is **not** netted against it (shown as cost, never credit, as the labeller does) | `requested_price`, `fill_price` | £ |
| commission £ | Σ `commission` (absolute value) | `exploration_trades.commission` | £ |
| costs R | Σ over closed trades of (cost_i ÷ stop_loss_i): each trade's costs in its own R | derived | R |
| refused (top 3) | candidates not taken because a **rule refused** them (`blocked_reason` set, branch not a policy skip), grouped by reason, top 3 by count, worded by the entry-status map | `exploration_obs.blocked_reason`, `branch` | count |
| policy skips | not taken by the 0.8/0.2 taking rule or the bandit share: **not** a refusal, not shown in either message (the owner cannot act on it; the dashboard has it) | `branch` | count |
| down … from peak | `1 − equity / balance_closed_hwm` of that engine (the RiskGate's own arithmetic), at send time, with £ = hwm − equity | `engine_<account>.json` | %, £ |
| warn / pause / halt lines | the account's configured exploration warn (6%), pause (8%, ADR 0043 X4) and `risk.drawdown_stage2` (12%), **read from config, never hard-coded**; £ = hwm × line | settings / accounts.yaml | %, £ |
| arms: net R so far | per arm, Σ net R of its closed live trades since the arm started, **in exit order, the same series the per-arm stop rule uses** (quant-reviewer confirms the series before build); controls (S0, S0R, I0, W0, P0) excluded from ahead/behind | `exploration_trades` | R |
| arms: (n) | that arm's admissible closed trades (the prereg's n toward 300) | as above | trades |
| ahead / behind | top 2 / bottom 2 arms by net R so far among arms with n ≥ 10; an arm within 50% of its stop is always listed in "behind" with "stops at …" | derived | R |
| stops at | the arm's pre-registered stop (−30R scalp, −15R intraday, −10R swing) from the prereg config | prereg / arm config | R |
| learner change | latest `exploration_weights` run vs the previous run, per style: arms whose share moved ≥ 2 points, largest 2 per style, "a→b%" | `exploration_weights` | % of the lab's style budget |
| learner time | the run's `run_ns`, UTC | same | time |
| month | as today (`monthly_report.month_to_date_line`), plus the labs' exploration P&L/R as its own term | existing + exploration | £, R, days |
| open now | open positions and lots across enabled engines | engine state | trades, lot |
| For you | pending approvals (real money only), owner decision rows, deploy on offer (existing `decisions_line`); "nothing" when all zero | existing | count |

**Needs field (implementer, confirm before build):**
1. `exploration_trades` must expose `requested_price` and `exit_requested_price` (exit slippage) and `spread_usd` for
   every fill; if the exit's requested price is not recorded, the slippage figure is entry-only and the costs line says
   "slippage (entry)".
2. A per-account **exploration state** readable without the engine's internals: `paused` (bool), `paused_since_utc`,
   `pause_line`, `warn_line`, `resume_rule` (plain text from a fixed set). Suggested home: `engine_<account>.json`
   `exploration` block, also served on `GET /api/accounts` so the dashboard and Telegram say the same thing.
3. A per-arm **status** row: `arm_id, style, n_admissible, net_r_running, stop_r, stop_n, retired_at, retired_why,
   read_verdict, read_at` (the evaluator writes `read_verdict`; the report only relays it). Without it, retirements
   cannot be reported reliably.
4. An `fx_close` rate per day (USD→GBP) for the "≈ £" figure and the spread/slippage conversion; if absent, spread
   and slippage are printed in `$` with "(USD)".

## 3. The weekly digest (Sunday 06:45 UTC, replaces Sunday's daily)

Week = the five opportunity days Mon–Fri (Sun 22:00 to Fri 22:00 UTC). Budget: **≤ 25 logical lines**.

### 3.1 Mock (Sun 25 Oct 2026)

```
📅 Week Mon 19 – Fri 23 Oct · ended Fri 22:00 UTC (23:00 UK)
✅ Health ok now · 2 failures this week, both cleared · Halts: none
Scalp lab: 1,040 signals · 318 taken · 312 filled · 305 closed −£41.20 · −9.6R
  by day: Mon −3.9R · Tue +2.2R · Wed −6.1R · Thu +1.0R · Fri −2.8R
  costs £58.10 (12.9R) · worst drawdown 3.4% (£372) Wed · now 2.1% (£230)
Day/swing lab: 188 signals · 43 taken · 41 filled · 38 closed +£64.30 · +4.1R
  by day: Mon +1.4R · Tue −0.6R · Wed +2.9R · Thu −1.1R · Fri +1.5R
  costs £9.80 (0.6R) · worst drawdown 1.2% (£132) Thu · now 0.8% (£88)
Costs ICM: £0.19 a trade at 0.01 lot vs £0.18 planned · spread wide 6% of the time
Learning paid +£23.10 demo this week (real money £0) · 1,402 usable labels
Arms, net R so far (trades) · too early to judge, first read at 300
  ahead: MB30 +6.2R (41) · S2A +3.1R (118) · W1A +2.0R (12)
  behind: S3B −11.4R (126) · S5A −4.8R (140) · I2B −5.0R (22)
  near a stop: S3B −11.4 of −30R · retired this week: none
  closest to its read: S2A 118 of 300 · random baseline S0 −8.0R (140)
Learner this week: scalp S2A 12→24%, S3B 14→5% · intraday MB30 10→19%
  swing: equal shares (3 fills, too few) · models: scalp old one kept
Research this week:
  • Box break 24-bar test: no edge after costs, closed (trial 7 of 20)
  • Intraday and swing arms moved to the Day/swing lab (ADR 0043)
Next week: US jobs report Fri 6 Nov 13:30 UTC (no new entries 13:15–14:00)
Not running: Vantage lab
👉 For you: nothing
```

23 logical lines.

### 3.2 Weekly field definitions (beyond §2.5)

| Field | Definition | Source | Unit |
|---|---|---|---|
| failures this week | distinct health checks that were `fail` at any alert pass in the week, and whether each is cleared now | health alert log | count |
| by day | Σ net R of closed trades per opportunity day; a closed day prints "closed" | `exploration_trades` | R |
| worst drawdown | max of `1 − equity/hwm` sampled at engine heartbeats in the week, with its day | engine state history (needs field 5 below) | %, £ |
| cost per trade (broker) | mean measured round trip (`cost`, scaled to 0.01 lot) over all fills incl. the P0 probe, vs the frozen cost model (`config/arm_costs.yaml` round trip, converted) | `exploration_trades.cost`, cost probes | £ per trade at 0.01 lot |
| spread wide % | share of decision minutes where the spread exceeded `risk.max_spread_points` | spread profile | % of open minutes |
| learning result | Σ net P&L of the labs in the week. Negative: "Learning cost £x demo (real money £0) · £y per usable label (n)"; positive: "Learning paid +£x demo this week (real money £0) · n usable labels". Usable labels = admissible labels written in the week (ADR 0043 ROI reading: demo £ per admissible label) | exploration ledger, `exploration_obs.labelled_ns` | £, £ per label |
| near a stop | non-control arms with net R ≤ 0.5 × their stop_r while n < stop_n (half-way or more to the stop) | arm status | R |
| closest to its read | the non-control arm with the largest n < 300 | arm status | trades |
| random baseline | the style's random control arm (S0 for scalp, I0 intraday, W0 swing) net R and n; the reference the arms are compared with by eye, without a test | arm status | R, trades |
| learner this week | first vs last `exploration_weights` run of the week, per style, moves ≥ 2 points, top 2 per style | `exploration_weights` | % of style budget |
| models | H-62 Sunday retrain outcome as recorded (kept / replaced / not enough labels: "612 of 1,500") | model registry | — |
| research this week | at most 3 bullets, newest first: trials with a recorded verdict (registry rows with a verdict dated in the week), ADRs accepted in the week (`docs/decisions`, by date), arm retirements and read verdicts; each in plain words with the trial count "trial n of budget"; more: "+N more on the dashboard" | research registry, ADR index, arm status | — |
| next week | at most 1 line: the next tier-1 news event (CPI, NFP, FOMC, PCE) with its blackout window in UTC, and any arm read expected | calendar, blackout settings | UTC |

**Needs field 5:** engine drawdown history (daily max drawdown per account, written at the 22:00 UTC roll) so "worst
drawdown this week" is a record, not a guess from the end state.

### 3.3 Weekly states

| State | Copy |
|---|---|
| Whole week closed (e.g. Christmas week) | `📅 Week … · market closed most of the week` + per lab one line with whatever traded |
| Lab paused all week at −8% | lab line: `⏸ Scalp lab paused all week (down 8.3%, £909) · cost probe ran 220 fills` |
| No research decision | `Research this week: no new result (2 trials running)` |
| Research registry unreadable | `Research this week: could not read the registry` |
| First week of a lab (no prior weights run) | `Learner: first run, equal shares` |

### 3.4 Arms wording guardrails (quant sign-off required)

- Header always carries "too early to judge, first read at 300" until an arm has its recorded read; after that, that
  arm prints its recorded verdict and leaves the ahead/behind list.
- Never print: p-value, t-stat, "probability best", "significant", "edge found", "winning", "best/worst strategy",
  win rate, or a per-trade mean R for an arm with n < 300 (sums are shown because the stop rule is a sum).
- Controls are never in ahead/behind; they appear only as "random baseline".
- Arms below 10 trades are not ranked; the line says how many were left out when it matters ("+6 arms under 10 trades").

## 4. Layout and accessibility on Telegram

- Plain text with Telegram's default font; **no monospace tables** (they overflow at 360 px and break screen readers);
  no Markdown parse mode needed (avoids escaping failures on `−`, `_`, `.`); one emoji at most per line, always with
  its word.
- Two-space indent for detail lines under a lab (visible grouping without colour).
- Light and dark themes: Telegram renders the text; nothing depends on colour, so contrast is the client's.
- Notification preview (first ~2 lines on the lock screen) must carry the essentials: line 1 = which day, line 2 =
  health + halts + entries. A halt or pause therefore always reaches line 2.
- Message ≤ 4,096 characters (Telegram limit); measured mocks: daily ~1,050, weekly ~1,700.

## 5. Acceptance criteria (for the implementer and test-engineer)

1. Golden-text unit tests reproduce the §2.1 and §3.1 mocks byte for byte from fixture state (aux.db rows, engine
   files, weights runs, registry rows).
2. Daily ≤ 15 non-empty lines and weekly ≤ 25 in every state of §2.4 and §3.3, including the worst case (3 labs, a
   halt, a pause, a failing health check, a retired arm, a model retrain): a parametrised test asserts the count and
   that health, halts, risk lines at/after warn and "For you" survive the cut.
3. Every number token in the output carries a unit (`£`, `$`, `R`, `%`, `lot`, a count noun, or the arms block's
   "(trades)") and every time a zone (`UTC` or the owner's zone): a regex test over all state fixtures.
4. No identifier leaks: a test injects a login number, server name, host, Telegram id and email into every source
   and asserts none appears; account slugs (`icm-probe`, `icm-demo`, `vantage-demo`) never appear; an unmapped
   account prints "Other account".
5. No `reply_markup` on either message (test on the bot send call).
6. Arm guardrails (§3.4): banned-word test; controls never in ahead/behind; arms with n < 10 never ranked; the arm
   series equals the stop-rule series (shared function, not a copy).
7. Exploration and real-money P&L are never summed together, and currencies are never summed together.
8. Drawdown lines (6/8/12%) come from config: a test changes them in settings and the text follows.
9. One broken source yields one "could not read" line and the message still sends (existing `_section` contract).
10. Sunday sends the weekly and not the daily; Saturday sends Friday's daily; a 6 h late send keeps the right day.
11. Quant-reviewer has signed the §3.4 wording and the arm R series; trading-safety-reviewer has confirmed the pause and
    halt copy matches engine behaviour (cost probe and exits continue at −8%).
12. Owner-verified: the first live daily is screenshotted on a 360 px phone in light and dark and attached to the
    deploy note; the owner reads it without opening the dashboard.

## 6. Risks raised

| Rank | Risk | Fix |
|---|---|---|
| 1 | The current digest reports "Trades closed: none" on the labs while they trade (exploration is isolated from `closed_trades`). The owner may already be reading wrong mornings. | D1: ship this as a replacement; until then, add one line to today's digest: "Labs: see the dashboard (exploration not counted here)". |
| 2 | Daily arm rankings invite interim reads and owner tinkering against the prereg. | D3 + §3.4 guardrails + quant sign-off; the stop distance is the only action-relevant number shown. |
| 3 | Costs in R on the scalp lab (2.4R a day on −3.9R) will read as "costs are killing it" — true and useful, but only if the planned figure is beside it. | Weekly broker line shows measured vs planned per trade; daily keeps the R figure. |
| 4 | Per-host exposure is not summed (ADR 0043 note): the owner may read two "down 2%" lines as small while both labs are on XAUUSD. | Out of scope here; the weekly could add "combined open, both labs" if the trading-safety reviewer wants it. |
