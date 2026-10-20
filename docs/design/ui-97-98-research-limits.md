# Design note: BACKLOG 97 (Research summary-first) and 98 (Limits empty state, combined cap in £)
Designer: ui-ux-designer staff; signed off with the corrections below by the Principal Product Designer, 2026-10-19. Read-only; no code changed. Sources: web/src/screens/Research.tsx, ResearchToday.tsx, Limits.tsx, goldbot/api/schema.py, ADR 0004 / 0039, shots in ~/tmp/ui-shots/phone-light/12,13 (Research is 390x11,329 px today).

## Principal sign-off (2026-10-19): corrections applied in this file
1. Line 2 no longer shows a projected "£x on an average opportunity day". A money figure from test data on the first
   screen reads as a promise, and it rests on backlog 33 (opportunity-day evaluation), which has not been built. The line
   is the verdict in words plus net R per trade after costs, with the number of trades and "test data". A £ figure needs
   33 and quant-reviewer sign-off first. `best_candidate` drops `net_r_per_opportunity_day_gbp`.
2. Reset times are not asserted in copy ("Sunday 00:00 UTC" was a guess). They come from the API: LimitAccount and the
   supervisor view gain `day_resets_utc` and `week_resets_utc`, shown in local time with UTC on hover.
3. "Binds first" is defined as: the combined £ left is smaller than every account's £ left. Ties show no chip.
4. Summary card at most 520 px at 390 wide (the backlog said 600; the tighter figure stands). Whole page under 2,500 px
   with every section collapsed.
5. Line 1 and the best-candidate wording need research-director sign-off before release (not before the build: the
   copy is in one table in `lib/researchWords.ts`).

## 97. Research
### Why it is long (measured from code)
Everything is open or always rendered: plan prose, focus list with reasons, retired cards, "moves" table, registry (up to 200 rows, 10 columns), hypothesis tables (first open, one card per row, ~60+ cards), hashes (:68-79). Hash/drift warnings are developer text.

### Layout (390 px). Target: collapsed page ~1,500 px (budget < 2,500)
1. Summary card (always open, <= 520 px) - "Is research getting us to £50 a day?"
2. Collapsed sections, each a 56 px row with a one-line summary and a count (44 px tap target, chevron, aria-expanded):
   - What is running now
   - Results: passed and failed
   - What is next (plan)
   - Retired ideas
   - All trials (registry)
   - Ideas list (hypotheses)
   - Audit detail
Order is the owner's four questions: closer? running? passed/failed? next?

### Summary card, exact copy
Heading: **Research: are we getting closer to £50 a day?**
Line 1 (north-star, plain words, one of):
- No strategy has passed every gate yet. Nothing is ready for live.
- 1 strategy passed every gate and is being watched on demo. Not live yet.
- N strategies passed every gate; the next step is the demo-to-live check.
Line 2 (needs data, see API): "Best candidate: {name}: {verdict words}, {+0.12 R} a trade after costs over {n} test trades (test data, not money)." If unknown: "Best candidate: none yet."
Line 3 (budget, meter as today's Budget): "Trials this quarter: {used} of {budget} used, {left} left." Warn copy when left <= 10%: "Few trials left. Plan the rest carefully." At 0: "Budget used up. New trials start {date}."
Line 4 (latest verdicts, max 3, newest first, verdictWords from lib/limits.ts): "Latest results: {family} {tf}: Failed (costs eat the edge). / Passed all gates. / Needs more data."
Line 5: "Next trial: {family} {tf}, planned for {date}." Fallback: "Next trial: not scheduled. The director plans on Saturdays."
Line 6 (small, muted): "Since last week: {n} trials run, {p} passed, {f} failed."
Stale plan (>21 days) shows an amber chip "Plan is out of date" above Line 5, no red (research never stops trading).
Loading: skeleton 280 px (no shift). Error: ErrorState title "Research could not load", stillWorks "Trading is not affected."
Verdict words: translate gate names (map in lib/limits or new lib/gateWords.ts; raw names in section details/title).

### Section one-liners (collapsed)
- What is running now: "{n} trials run today, {k} waiting" (ResearchToday counts; the old card content moves inside).
- Results: "{p} passed, {f} failed, {m} need more data (last 30 days)". Inside: newest 10 as cards (family, tf, date, verdict, net R), "Show all trials" opens registry.
- What is next: "{quarter}: {n} trials planned across {k} families. Made {date}." Inside: focus list; moves table behind "Why the plan changed"; "Evidence per family" stays a nested details.
- Retired ideas: "{n} retired, {r} reinstated". Inside: existing cards.
- All trials: "{total} trials, newest {date}". Inside: existing table (desktop) / cards (phone, new; table scrolls sideways today).
- Ideas list: "{n} ideas in {t} tables, updated {date}". All tables collapsed (drop open={i===0}).
- Audit detail: "Plan and document checks". Inside: hypotheses.md hashes (short + full on hover), drift warnings, holdout dates, file path, reservation maths, "Every trial raises the bar (deflated Sharpe)..." note.
Desktop 1440: same order; summary card left 2/3 with budget meter right 1/3; sections remain collapsed by default but remember open state in sessionStorage; open sections use the full table.

### Dropped or moved (no data removed)
Moved to Audit detail: hash strings, file path, holdout dates, drift text, reservation sentence, plan-age sentence. Moved to inline "Why" disclosures: moves table, evidence table. Removed from first view: Research today card as separate block (merged into summary + running now); duplicate budget line (card and Budget both say it). Rationale column on phone: second line of card, not a column.

### API: exists vs needed
Exists: budget{quarter,budget,used,left}; today{run,passed,failed,no_verdict,not_counted,latest,quarter_end}; trials[] (gates, net_r, rationale); plan{created_utc,stale,focus[],retired[],moves[],hashes}; hypotheses tables.
Needed (additive, ResearchView.summary):
- passed_total (all-time and 7-day): passed, failed, no_verdict counts (can derive 7-day from trials[] client-side; passed_total needs total since trials is capped at 200).
- best_candidate {family, timeframe, verdict, net_r_per_trade, n_trades, stage: "research"|"demo watch"|"live-ready"} (research-director owns the definition; opportunity-day eval is backlog 33).
- next_trial {family, timeframe, planned_date|null} (plan has focus but no dates).
- north_star_gbp: 50 (ADR 0004; from settings, not hard-coded in UI).
- since_last_week {run, passed, failed}.
Until fields exist: show Lines 3, 4, 6 from existing data, and Line 2 "Best candidate: not reported yet" (do not invent numbers). research-director must sign off the Line 1 wording.

### Accessibility / states
Chevron rows are buttons (native details/summary acceptable, but summary min-height 44 px); verdict never by colour alone (word + chip); dates local with UTC title; units on every number (R, £, trials). Empty registry: EmptyState "No trials yet" / "Results appear here as trials run." Dark mode uses existing tokens.
e2e: 390 px full-page height < 2,500 px with sections collapsed; summary card < 600 px; no layout shift on 5-min refetch (fixed min-heights).

## 98. Limits
### Common
One h1 "Limits" (drop h2 "Risk limits"). Order: combined-cap card, engine cards, then a "Defaults" footnote. Currency GBP (ADR 0004: £50 a day loss limit).

### Combined-cap card (always shown, top)
Heading: **All accounts together** with label chip "Binds first" (text chip, not colour only; shown when combined remaining < any single account's remaining, else no chip).
Body, with engines reporting:
- "Today: £{used} of £{cap} used, £{left} left" + meter
- "This week: £{used} of £{cap} used, £{left} left" + meter
- Small: "Day resets {day_resets_utc as local} · week resets {week_resets_utc as local}" (UTC on hover; fields from the API, sign-off 2). Percent on hover/title and in aria-valuetext: "{p}% of £{base}".
- Spent: red banner (reuse existing): "Combined daily limit reached. New entries are stopped until 00:00 UTC. Exits keep working."
Without engines (supervisor not reporting): "Today: cap not reported. Defaults: {1.5}% a day, {4.0}% a week of the account." Money: if no balance known, percent only plus "£ shows once an engine reports". The £50 absolute limit is not a supervisor value: do not show it as the combined cap; show "Your own daily loss limit: £50 (ADR 0004)" only when absolute_daily_limit is known from an account.

### Empty state (no engine), EmptyState
Title: **No engine is reporting**
Body: "Limits show once an engine reports. Until then the configured defaults apply: {1.5}% a day and {4.0}% a week for all accounts together; demo may use up to {3.75}% / {7.5}%. Engines refuse new entries if they cannot read their limits. Exits keep working."
Action: Button "Check again" (refetch). Link "See Health" (nav).
Is the header banner "No engine reporting" duplicating? Keep the banner (safety state) but the page does not repeat the sentence.

### Error state, ErrorState
Title: **Limits could not load** ({message} in small muted below). stillWorks: "Engines fail closed on this: they refuse new entries if they cannot read their limits. Exits keep working." Retry = "Try again". Amber, not red.

### Engine card with engines (360 and 1440)
Per account, collapsed to the essentials; "Details" disclosure keeps minimum-lot text, revert, changes.
- Header: account, Demo/Live chip, "Updated {time}" (stale chip if >60 s).
- Row "Today": "£{used} of £{limit} used, £{left} left" + meter (reuse Cap; add "left" figure).
- Row "This week": same.
- Row "Risk per trade today" (ADR 0039, only if risk_today != null): "Each trade risks about {base}% of the account ({£x}). Today: {applied}%." Then per strategy list, max 3 then "Show all": "{strategy name}: {applied}% (ceiling {ceiling}%)" and the one-sentence reason. Cut chips in words: "Cut to half: volatility high" (k_vol), "Cut: drawdown {x}%" (k_dd), "Cut: half of today's limit already used" (k_day). Summary sentence = risk_today.why. Strategy version ids shown only on hover/in Details (no version ids in owner text, ADR 0039 point 7); needs a friendly strategy name field.
- When dynamic limits are off (risk_today null): row "Risk per trade: fixed {0.5}%".
Layout 360: single column, cards stacked, meters full width, text 14 px min; 1440: combined card spans full width, engine cards in a 2-column grid (max 560 px each).

### API
Exists: LimitsSupervisorView{daily_cap,weekly_cap,default_*,envelope_*} (fractions); LimitAccount{daily_limit,daily_used,weekly_limit,weekly_used,currency,day_start_equity,week_start_equity,risk_today{base,ceiling,k_*,scale,strategies[version,decided,applied,reason],why}}.
Needed: supervisor money fields daily_cap_amount, weekly_cap_amount, daily_used_amount, weekly_used_amount, currency (backlog 98 text), plus supervisor_reporting flag if daily_cap null is not enough. Caveat: combined money needs one currency; supervisor already halts on mixed currencies (ADR 0004) so when currencies differ show percent only with "Accounts use different currencies". Strategy display name on StrategyRiskView. Money left is derived client-side (limit - used, floor 0).

### Tests (vitest)
Empty (copy, no h2, one h1), error (stillWorks text, Try again calls refetch), loaded (money lines, "Binds first" chip, per-strategy list, risk_today null fallback), spent cap banner. Shots at 360 and 1440, light and dark.

## Flags for the principal
- Line 1 / best-candidate wording needs research-director sign-off; "opportunity-day £" figure depends on backlog 33.
- 98's "binds first" needs both supervisor and account remainders; define as smaller remaining £.
- Backlog 97 says "under 600 px summary at 390"; this spec holds it at <= 520.
