# goldbot product backlog

2026-10-10 · product owner · base `874262b`. Inputs: `docs/TRADER_LIFECYCLE.md` (roadmap), `docs/TRACEABILITY.md`
("Larger gaps" and every partial or missing row), `docs/research/state-of-the-art.md` (section C), `HANDOFF.md`
"Next steps", open issues (#34 progress, research reports #37–#53, data coverage) and PR #60.

**Where we stand.** No strategy is net positive after costs. tsmom has the only gross edge (+0.06 R, t 2.4–2.6),
and it is negative net (−0.014 R at 4h before swap). Meta-models add nothing (AUC ≈ 0.50). The Q4 trial budget is
spent (20/20); trials resume 2027-01-01. So the ranking favours two things: work that can still flip tsmom or find a
new edge cheaply (**FIND**), and work that must exist before the system runs unattended on demo (**RUN**). Every
expected-impact figure below is uncertain. None of these items is expected to create profit on its own.

Rank = expected impact on net profit and safety ÷ effort. Size: S < 1 day, M 1–3 days, L > 3 days.
Status: ready / in progress / in review / done / rejected. Item numbers are stable ids; the order of the table is
the rank.

**Status sweep 2026-10-16 (product owner).** Every fragment was checked against `git log` on
`claude/gifted-goldberg-lcb7pl` and `main` (PRs #60–#77) and the open lane branches. 28, 25 and 29, the top three
since 2026-10-10, merged in #71 and are done. The table now holds at most 15 rows (AGENT_STANDARDS "Accountability
and cadence"). Each row's status cell names exactly one accountable role and a due date, and the acceptance
criteria are in the item's detail block. Done items are under "Done", and items moved off the table are under
"Later (parked)", each with a one-line reason. Ranking: safety rails first (an unsafe demo is worse than no
demo). Then comes the north star (ADR 0004): nothing trades or gets measured until the demo deploy (1) runs.
Research integrity for the Q1 freeze is last. Late on 2026-10-16: 1 (was due 2026-10-13), 44 and 52 (their
re-reviews were asked on 2026-10-12 and have not started; the reviewed batch has sat off `main` since 2026-10-12).

**Job-check sweep 2026-10-16 evening (product owner, base 3092923).** 48, 49, 54, 55, 56 and 57 moved to Done, each
with the seven-check acceptance scorecard in its detail block; all are accepted on the condition that their PR
merges (PR #79 open, CI running; 55 goes in the next PR). 44 and 52 were re-reviewed today (fixes 869bb04 and
d2ecb8f), so they are no longer late; their re-verifies are owed by 2026-10-17. 59 is ranked second: the engine
start at the reopen (Sun 2026-10-18 22:02 UTC) waits on it. 58, 59 and 60 now name one accountable role and a date.
36 and 51 are merged on the integration branch and ship in the next PR (release-manager). 13 ranked rows. The
daily scorecard is `docs/process/scorecards/2026-10-16.md`.

**Decision sweep 2026-10-16 late (program director + product owner, owner delegated; base 3db12c8).** New standing
rule from the owner: routine product, ops and deploy decisions are made by the program director with the product
owner and reported to the owner, not asked; safety-touching choices take the conservative option. Recorded today:
62 (kill switch closes every symbol, rank 5), 63 (card-bound approvals, rank 9), 64 (header shows the role, rank
12), 65 (review hygiene, rank 15); 12 notes the digest's `verdict_adj` line; 45 carries the holiday-list decision
schedule (quant ruling 10-23, decision 10-30). 58 and 52 left the table as done (shipped in PR #79). Stale statuses
fixed: 59 (SRE re-verify on record), 61 (merged at 921a38a), 45 (draft delivered feda07a). 15 ranked rows.

**CFO and CTO follow-ups 2026-10-16 night (product owner; base abcbe2b; all-hands memo of 2026-10-16).** From
`docs/finance/capital-plan.md`, `docs/finance/budget-2026-10.md` and the CTO's tech-debt list: new items 66 (bridge
lot ceiling scaled to equity, rank 3, architect plan by 10-23), 67 (Object Storage 15 GB alert, sre), 68 (CI-minutes
explanation and worktree clean-up, release-manager) and 69 (monthly audit of leverage and the largest order,
finance-auditor, 11-04). 27 records the terminal's contract terms read today and is in progress. To hold 15 rows,
36, 51 and 61 left the table: they are merged on the integration branch and only wait for the next PR
(release-manager, due 10-23, wave lane 4; accepted at that PR). 65 is parked. The CFO's owner-only matters (C1-C5,
OD-12) are in ROADMAP section 6 as OD-16 to OD-20.

## Ranked items

| # | Item | Stage · kind | Size | Status |
| --- | --- | --- | --- | --- |
<!-- backlog.d:table -->

<!-- backlog.d:items from=1 -->

### 16–24. From the XAUUSD trader playbook (strategy researcher, 2026-10-10)
Source and evidence: `docs/research/xauusd-trader-playbook.md` section 6 (gap ids in brackets). Ranked by the
researcher. The product owner re-ranks against items 1–15. None of these uses trial budget. Engine, risk and
execution items need trading-safety review. Feature items need quant review.
<!-- backlog.d:items from=16 -->

### 25–33. From ADR 0004 (amended 2026-10-10: £1,000 GBP account, £50 daily loss limit, £50+/day north star) and the opportunity-day research (`docs/research/opportunity-days.md`; 30a/30b, the 29 and 31 additions, 32, 33)
Source: `docs/decisions/0004-starting-account-and-daily-target.md`. The IC Markets demo is £1,000 GBP, read from
MT5. At £1,000 only a 15m minimum lot fits (0.82% true risk), and only once 28 and 25 land. The audit found the
gate treats USD amounts as GBP (HIGH). None of these items uses trial budget or touches the holdout; 30 and 31 are
hypotheses that go through the trial registry in a future quarter's budget.
<!-- backlog.d:items from=25 -->

## Needs the owner
These are never decided by the product owner or by agents. Since 2026-10-16 (owner rule) only truly owner-only
matters stay here: money or paid plans; going live, `unlock_live` or phase-gate thresholds; loosening any risk limit;
the owner's own credentials; legal. Routine product, ops and deploy decisions are made by the program director with
the product owner and reported on #34 (ROADMAP section 6, "Decided by PD+PO").
Every owner step below has `chaser: program-director`, who asks on #34 with a recommendation and a chase date.
- **Capital and leverage (CFO capital plan section 7, queued 2026-10-16; ROADMAP section 6, OD-16 to OD-20).**
  C1 no real money before the paper -> tiny-live gate (recommend: confirm). C2 tiny-live cannot trade at £1,000;
  it needs about £6,850: (a) fund, (b) keep £1,000 and never go live, (c) amend the min-lot rule (a loosening; not
  recommended); decide before the backtest -> paper gate, earliest about April 2027. C3 no demo deposits; live
  deposits sized by what the next gate needs. C4 confirm the existing limits as the risk appetite by Mon 2026-11-30.
  C5 set the broker leverage to the lowest IC Markets offers at or above 1:20 (tightens; your action in the client
  area). OD-12: the agent budget (recommend about $10 a month) by Fri 2026-10-23, and whether the Claude
  subscription counts as a running cost.
- **Moved to PD+PO 2026-10-16 (they only tighten; the conservative option):** adopting the rails below. The PD+PO decide
  each on the evidence of 45 and 46; any value looser than the built default goes back to the owner.
  **Adopt the built, off-by-default rails (sweep 2026-10-16).** Holiday, reopen and tier-2 windows (17, 18), the
  heat cap (19), the loss-streak throttle (21) and the open-risk projection (35) are merged and off. For each:
  adopt, set the values, or leave off. Recommendation: wait for 45 (holiday evidence, due 2026-10-30) before the
  holiday window, and for 46 (replay) before the others, except 35, which only makes the £50 limit stricter.
- **Automatic deployment to the trading servers (CD).** CI runs on every change. Deploying to the Oracle VMs
  automatically (on merge or tag, with health check and rollback) waits for your approval. Until then, deploys stay
  manual via `vps_update` / the Linux equivalents.
- ~~**Event floor for daily-bar families.**~~ **Decided 2026-10-11** (owner approved the research director's
  recommendations): 150 rule-only events, retire-only (`research.screen_min_events_daily: 150`;
  `docs/research/preregistration-2027Q1.md` "Owner rulings").
- ~~**Large-tick instruments.**~~ **Decided 2026-10-11:** out of scope (gold only).
- ~~**Trial budget.**~~ **Decided 2026-10-11:** 20 a quarter, unchanged. Also decided that day: no consensus feed
  (H-11 blocked); the conservative K_eff default confirmed; `costs-v1` published by 2026-12-11 if the demo
  terminal runs, otherwise "priors accepted" on 2026-12-14; the owner's trade history exported in full, analysed
  locally, summary only.
- **Paper-vs-backtest gate: require the paper mean's lower 95% bound above 0** (phase-gate threshold, ROADMAP
  OD-15). The quant reviewer recommends yes; the program director and product owner concur. Yours to rule.
- **Going live.** Demo until the paper → tiny-live gate is recorded in `state/phase_state.json`, then `unlock_live`
  and the typed phrase. Item 8 computes whether the gate is met. It never flips it.
- **Research evidence across feature versions (ADR 0006, item 34).** For now the tournament accepts a gate pass
  from another feature version, so the DST correction does not void every trial. Sign off the material feature
  version as the principled fix, or rule that evidence must be re-run after any feature bump.
- **Trader-playbook risk policy** (items 17–22; playbook section 7): the weekend rule (recommend flat for intraday
  families), tier-2 and holiday windows, throttle and heat-cap values, edge-linked sizing (G-10). The account size
  and tiny-live risk for H-01 were decided under your delegation in ADR 0004 (minimum-lot exception at ≤ 1%; H-01
  stays in shadow until equity reaches ~£7,900).
- **Account and goal (ADR 0004, amended).** The demo is £1,000 GBP (answered); £50 is your absolute daily loss
  limit (item 29); £50+/day is the north star, realistic as a monthly average at ~£21k–£53k. Yours: an optional
  deposit plan (deposits climb the ladder faster than returns: 1h at ~£1,650), and confirm the demo is a Raw Spread
  account (on Standard nothing trades at £1,000).
- **Opportunity days (`docs/research/opportunity-days.md` section 6).**
  - An event-count floor for H-18 (~690 events vs 1,000).
  - Whether to project the percentage daily caps (item 29). At £1,000 that means at most one full loss a day.
- Also yours: sign off the P7 threshold values (item 8); commit or approve the first measured cost table until
  item 2 lands; VM provisioning and every credential (item 1).

## Next parallel wave
Only items that are not done (job-check sweep 2026-10-16 evening). The reviewer queue rule applies first:
trading-safety holds four items (59, 44, 58, 60) against a limit of 2, so two trading-safety reviewer lanes run
before any new trading-safety builder. Every lane below touches different files.

1. **Trading-safety reviewer lane A: 59, then 58** (read-only). 59's re-verify of 412fd0d is due Sun 2026-10-18
   20:00 UTC, ahead of the engine start; SRE re-verifies 59 in the same window.
2. **Trading-safety reviewer lane B: 44 (869bb04 re-verify), then 60 (bf5240b)** (read-only).
3. **Security reviewer lane: 52 (d2ecb8f re-verify), then 51 (cc00630 re-verify)** (read-only).
4. **Release-manager: PR #79** merged on green, main merged back; then the next PR with 36, 51, 55, 60 and 52's
   d2ecb8f by 2026-10-23 (ROADMAP M1).
5. **Item 1, demo deploy** (`lane/claude-run-deploy`, sre runs it, release-manager accountable): `docs/deploy/`,
   `goldbot/ops/linux/`. The bridge smoke (ROADMAP W1-1, new `goldbot/ops/smoke.py`) follows the architect's plan.
6. **Item 43, `entry_blocks`** (`lane/entry-blocks`, implementer): `goldbot/risk/gate.py` (`account_reasons`), the
   engine state write in `goldbot/engine/runner.py`, `goldbot/api/entry_status.py`. Starts when lane A has cleared 59
   and lane 9 (62, higher rank, same file) has merged: one runner.py lane at a time.
7. **Item 61, mixed-currency budget**: merged at 921a38a with its reviews; release-manager ships it in the next PR,
   then product-owner acceptance.
8. **Running:** 45 (quant ruling on the draft, due 10-23), 5 (research-director, `docs/research/`).
9. **Item 62, kill switch on every symbol** (implementer, then trading-safety): `_kill_switch` in
   `goldbot/engine/runner.py`, `goldbot/execution/mt5_adapter.py` `_filling`/`close`, ADR 0016, RUNBOOK. Starts when
   lane A has cleared 59; lane 6 (43) waits for it. Not before the Sunday start.
10. **Item 64, header role** (ui-ux-designer spec, then implementer): `web/src/` header only. Free now; disjoint
   from every other lane's files.
11. **Item 66, bridge lot ceiling: architect plan** (read-only; plan by Fri 2026-10-23), then a trading-safety
   verdict on the plan when lane A or B has room. The build (`goldbot/execution/bridge.py`, tests) waits for that
   verdict; no other lane touches bridge.py.
12. **Item 67, Object Storage alert** (sre): `goldbot/ops/health.py` check and settings, after lane 5's deploy steps
   free the SRE (due 10-23). Disjoint from lanes 6 and 9.
13. **Item 68, release hygiene** (release-manager): worktree clean-up Mon 2026-10-19; CI-minutes answer by 10-23
   (ROADMAP section 2, budget-2026-10.md).
14. **Item 69** (finance-auditor): one row in `docs/finance/audit-framework.md`, by the 2026-11-04 audit.
63 waits for 60's fix lane (same approval files); 65 is parked.

Top three ready items (2026-10-16 night): 66 (lane 11, architect plan), 62 (lane 9) and 43 (lane 6). 27 is in
progress, read from the terminal; the rest of it follows item 1's engine start (Sun 2026-10-18).

## Later (parked)
Moved off the ranked table in the sweep of 2026-10-16. Each item's detail block has its status and the full reason.
- 11, trader-toolkit evaluation: runs as H-02 inside the Q1 queue (5).
- 12 (rest), attribution dashboard view: the backend and the director input are done; the screen is ROADMAP W2-3.
- 13 (rest), 1d founders: needs the engine to hold daily history, which is an engine change for later.
- 16, swap in the live EV: at £1,000 only 15m trades fit, so overnight swap barely moves the EV. Revisit at the 1h
  milestone.
- 17, 18, 19, 21, 35: built and off by default. Adopting them is the owner's call ("Needs the owner").
- 24 (rest), D-2 deterministic calendars: H-04 was frozen without them, and no queued trial needs them.
- 26 (rest), north-star card, Telegram line and capture KPI: the backend is done; there are no trades until 1.
- 30b and 30's trials, 31, 32, 33: Q2 2027 hypotheses, or reports that need trades first.
- 34, material feature version: waits on the owner's ADR 0006 ruling.
- 46, replay of the entry windows: needs a shadow book (after 1); schedule after 45.
- 53, phase-gate comparator on the deployed trial: matters only once a model is promoted, and none is.
- 65, review hygiene (parked 2026-10-16 for the cap): none of its parts can lose money; the quant N1 text (10-19)
  and the N3 ADR (10-30) are still owed on their dates.
- 36, 51, 61 (off the table 2026-10-16, not parked): merged on the integration branch, shipping in the next PR
  (release-manager, due 10-23); accepted at that PR.
- M28 TreeSHAP per decision, A5 per-trade SHAP, and a post-trade review after each exit.
- R8 `order_calc_margin` (touches the broker protocol; schedule after item 6).
- D10 bar close by clock, A2 window from settings, M9 allocator blackout input, D11
  `flags` dedup, D13 session state from `session_deals`, D7 4h bars across the daily break.
- M25 CUSUM calibrated to 5% quarterly false alarms; M15 walk-forward reads settings; U4 lockout timing.
- M16 CPCV quarterly; M10 learned allocator (after three months of data); F12 TradingView feature family.
- Economic calendar with consensus and surprises (needed for hypothesis 4); other C2 sources (COT, ETF holdings, GPR,
  GC basis).
- Entry order type study (limit vs market); portfolio volatility target (risk use only, not alpha, per C3).

## Done
**Accepted 2026-10-16 evening (job-check sweep).** Each has the seven-check scorecard in its detail block; tests
re-run at 3092923 (238 passed across their suites). Acceptance is conditional on the PR merging (check 5).

| Item | 1 criteria | 2 DoD | 3 S1-S12 | 4 reviewers | 5 CI, merged | 6 PR text | 7 role, date | Gap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 48 H-06 preset and count | yes | yes | yes | quant yes (717baa3); 9754ca0 not on record | preset on `main`; rest in #79 (open) | yes (#79) | yes | #79; quant note on 9754ca0 |
| 49 retired-family lists | yes | yes | yes | quant yes | #79 (open) | yes | yes | #79 |
| 54 HWM confirmation | yes | yes | yes | trading-safety yes | #79 (open) | yes | yes | #79 |
| 55 lockout test flake | yes | yes | yes | code yes (tests only) | integration 3092923; next PR | n/a | yes | next PR |
| 56 digest opportunity day | yes (approved wording change) | yes | yes | code yes, ui-ux applied | #79 (open) | yes | yes | #79 |
| 57 day-anchor validator | yes | yes | yes | trading-safety yes | #79 (open) | yes | yes | #79; sre server check |

**Earlier Done items: scorecard by PR (2026-10-16).** Check 5 (merged on green CI to `main`) and check 6 (every PR
body from #60 to #77 has an owner-visible behaviour section) hold for all of them. Check 7 is **no** for all: they
predate the one-accountable-role rule of 2026-10-16. Checks 1-3 lack evidence item by item: the criteria were not
re-run one by one; the named tests exist and passed in each PR's CI. Check 4: #76 and #77 name their reviews
(security, SRE, trading-safety, quant); #73 names a code review; #60-#71 have review-fix commits in git but no
per-item sign-off record. Open sign-offs: quant for 23 (V6) and 50 (M45). The product owner re-runs checks 1-3
for the safety items among them (6, 8, 25, 28, 29, 37-42) by 2026-10-23.

Checked against git on 2026-10-16. Main merge SHAs: #60 7ef3a00, #61 8bc7952, #62 1a194e9, #63 5f7da74,
#68 e00af06, #71 423d2b1, #73 d28a482, #74 a913d42, #75 a52b389, #76 520d731, #77 bc030d3. Acceptance record: each
item merged on green CI, and the tests it names exist. The criteria were not re-run one by one in this sweep. The
product owner runs the criterion-by-criterion acceptance scorecard (AGENT_STANDARDS) for these by 2026-10-23.
- #60: 3. #61: 4, 9, 10. #62: 2, 7. #63: 6, 8, 12 (part), 13 (part), 14, 15. #68: 12 (director input), 20, 22.
- #71: 24 (D-1), 25, 26 (backend), 28, 29. #73: 23, 30a, 37, 38, 39, 40, 41; the approval-card lines for 25, 28 and 29.
- #74: 42. #76: 47. #77: 50.
- Integration branch, shipping in PR #79 or the next PR: 48, 49, 54, 55, 56, 57 (table above).
- PR #79 (main 5fd90ab): 52 and 58, done with trading-safety APPROVE 2026-10-16 at 626b56b (3418bc5); 52's d2ecb8f ships in the next PR.
- Open sign-offs on done items: quant for 23 (V6) and 50 (M45); the owner for the values of 8.

## Rejected
- New 15m/1h trend, breakout, intraday momentum or mean-reversion variants, and meta-labelling over primaries with
  no gross edge. These are known nulls (research C3, issues #37–#53).
