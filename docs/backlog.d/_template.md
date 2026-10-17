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

**Order outcomes after approval 2026-10-17 (program director + product owner; base 765ec27).** From the two risks
`docs/ux/order-not-placed.md` flags. New 71 (rank 6: the definite-reject build itself, copy final but never
ranked), 72 (rank 7: ambiguous results, NOT CONFIRMED) and 73 (parked: auto-mode reject notices). Why this order:
both 71 and 72 fix a false safety state (a card reads APPROVED when no trade, or an unknown one, exists) on the
propose path the demo runs from its first day, so they rank with the safety rails and above 62 (other symbols, which
the demo account does not hold). 71 is ready and small; 72 also closes a record gap (an ambiguous result's row is
marked `rejected`, so a fill that closes inside one reconciliation pass is never recorded) and needs a
trading-safety plan first, so it ranks second. 73 cannot fire until auto mode exists (A10: 100 decided proposals), so
it is parked with a trigger. To hold 15 rows, 45, 69 and 70 are parked (work and dates unchanged): the holiday window
they feed stays off, and the November audit is two weeks out.

**Acceptance pass 2026-10-17 (product owner; base bdd89f7; PR #85 open, backend CI re-running).** Each delivered
item was checked criterion by criterion; its suites were re-run at bdd89f7 (556 passed, 1 failed). Accepted: 60, 61,
63, 66 (on `main` with their reviews recorded at the merge). Accepted with a follow-up: 43 (ui-ux re-check not on
record), 64 (ui-ux and code verdicts not on record), 65 (N3 ADR owed 10-30), W1-1 bridge smoke (item 1; never run on
the demo yet), the W2-3 Progress API, the e2e fix (TD-3, TD-4, TD-6 done, issue #82 closed; TD-10 only unblocked,
no code-review record) and PR 0 golden grid (34bfdf3, trading-safety APPROVE per ADR 0023 A1). Returned: 71 (no
verdict recorded on d067a19, no TRACEABILITY row) and the W2-3 Progress screens (the ui-ux-designer asked for
changes; b90cdef has no re-check on record). **Release blocker:** `runner.py` is 3504 lines against PR 0's ratchet
of 3492 (71 merged after the cap was set), so `test_runner_py_stays_under_its_line_cap` fails at bdd89f7 and at the
PR #85 head 22a9055; PR #85's backend job will go red. The CTO rules today (re-baseline once with a reason, or
trim), and 62 must hold the cap too. 11 ranked rows.

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
Only items that are not done (acceptance pass 2026-10-17, base bdd89f7). One runner.py lane at a time; every other
lane touches different files.

1. **Release blocker, PR #85** (release-manager with the CTO's ruling, today): the runner.py line-cap ratchet
   (`tests/test_engine_structure.py`, cap 3492, file 3504). Only that test file, or a trim in runner.py, changes.
2. **Item 71 gaps** (implementer, due 2026-10-18): the TRACEABILITY A row (`docs/traceability.d/`) and the
   trading-safety, code and ui-ux verdicts on d067a19 written into `docs/handoff.d/2026-10-17-not-placed-copy.md`
   (reviewers read-only).
3. **Item 1, demo deploy and the bridge smoke run** (`lane/claude-run-deploy`, sre runs it, release-manager
   accountable): `docs/deploy/`, `goldbot/ops/linux/`, the server. Engine start Sun 2026-10-18 22:02 UTC; smoke run
   by Mon 2026-10-26.
4. **Item 62, kill switch on every symbol** (trading-safety reviewer, read-only, due 2026-10-22): verdict on
   f211e0a/90b2603/e35f566; it merges only after lane 1 settles the cap (runner.py).
5. **Item 72, NOT CONFIRMED build** (implementer): starts when 71 is accepted and 62 has merged (runner.py,
   `not_placed.py`, `bot.py`, `ProposalCard.tsx`); tests/test_order_not_confirmed.py first.
6. **W2-3 Progress screens re-check** (ui-ux-designer, read-only, due 2026-10-19): b90cdef against
   docs/ux/progress-screens.md; records the verdict in `docs/handoff.d/2026-10-17-progress-screens.md`.
7. **Verdict records owed** (read-only, due 2026-10-19): ui-ux-designer for 43 and 64, code-reviewer for 64 and for
   the e2e fix (a2c66b0, ae5fbf1), code and trading-safety for 65 (a)(b). Each writes into that item's HANDOFF fragment.
8. **Item 59 and 44 re-verifies** (trading-safety, read-only): 59 before the engine start; 44 by 2026-10-19.
9. **Item 67, Object Storage alert** (sre, due 2026-10-23): `goldbot/ops/health.py` and settings, after lane 3.
10. **Item 68, release hygiene** (release-manager): worktree clean-up Mon 2026-10-19; CI-minutes answer by 10-23.
11. **Item 27 and 5** (running): terminal terms after the engine start; research-director in `docs/research/`.
12. **TD-10, e2e in local gates** (test-engineer, `scripts/gates.sh` only, due 2026-10-30).
74 waits for the ADR 0023 step-1 move; 65 (N3 ADR, research-director, 2026-10-30) and 73 are parked.

Top three ready items (2026-10-17): the PR #85 ratchet (lane 1), 71's gaps (lane 2) and item 1 (lane 3); all three
have lanes. 62 is in review, not ready for a builder.

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
- 65, review hygiene: (a), (b), (c) and (d N1, N2) accepted with follow-up 2026-10-17; only the N3 ADR
  (research-director, 2026-10-30) and the verdict records remain.
- 45 and 70, holiday list (parked 2026-10-17 for the cap): the window is off until adopted after 46; the 45 final
  and the v3 list keep their 2026-10-30 dates.
- 69, audit of leverage and the largest order (parked 2026-10-17 for the cap): re-enters 2026-11-01 for the
  2026-11-04 audit.
- 73, auto-mode reject notices (parked 2026-10-17): auto mode is off and offered only after 100 decided proposals;
  re-enters at 80 decided proposals or before any /mode auto, whichever is first.
- 36, 51 (off the table 2026-10-16, not parked): shipped to `main` in PR #83/#84; the product owner's
  criterion-by-criterion check is owed with the 10-23 safety re-run.
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
**Acceptance pass 2026-10-17 (product owner, base bdd89f7).** Suites re-run at bdd89f7: test_not_placed,
test_engine_safety, test_approval_window_clock, test_card_bound_approvals, test_bridge, test_engine_store_golden,
test_engine_structure, test_e2e_server_tree, test_api_progress, test_smoke, test_account_view,
test_api_entry_status, test_config, test_risk_gate_split: 556 passed, 1 failed (the line cap, see the sweep note).

| Item | Verdict | Criteria and evidence | Gap, owner, date |
| --- | --- | --- | --- |
| 60 approval window | ACCEPT | future/NaN expired, monotonic deadline, restart drops, digest counts (test_approval_window_clock.py); trading-safety APPROVE re-verify, 9113ce6 on `main` | none |
| 61 mixed-currency budget | ACCEPT | amber can't-check, red on halt, caps kept (API + web tests); TS, code, ui-ux at 921a38a | none (L1/L2 via 65 a) |
| 63 card-bound approvals | ACCEPT | card_created bound, missing/mismatch refused, 64-byte test, API regenerated; TS x2, code x2, security x2 at 3e1ee0a | none |
| 66 bridge ceiling | ACCEPT | 0.01 passes / 0.02 refused at 1,000, scales, fail closed, closes ungated (test_bridge.py); plan 8567c4d; all six reviews at 0bd0c10 | none |
| 43 entry_blocks | ACCEPT WITH FOLLOW-UP | account_reasons shared, entry_blocks published, API words them; code and TS APPROVE (420k differential cases) | ui-ux re-check not recorded; ui-ux-designer, 10-19 |
| 64 header role | ACCEPT WITH FOLLOW-UP | role shown, email only in Account; unit + e2e | ui-ux and code verdicts not recorded at a merge; 10-19 |
| 65 hygiene | ACCEPT WITH FOLLOW-UP | (a)(b)(d N1,N2) on `main`; (c) in the W2-3 API | N3 ADR, research-director, 10-30; verdict records 10-19 |
| W1-1 bridge smoke (item 1) | ACCEPT WITH FOLLOW-UP | test_smoke.py, 960-case gate grid; TS APPROVE c399976, security PUSH-OK, SRE APPROVE at a9e3274; gates green at ab671be | never run on the demo; sre, 10-26 |
| W2-3 Progress API | ACCEPT WITH FOLLOW-UP | quant, security, code APPROVE on b8c33a5; F1/F2 kept (34bdd78); test_api_progress.py | fixes 34bdd78 not re-verified on record; code-reviewer, 10-19 |
| W2-3 Progress screens | REJECT (returned) | progress.test.ts, Progress.test.tsx; code and security APPROVE | ui-ux asked for changes; b90cdef has no re-check; ui-ux-designer, 10-19 |
| 71 not placed | REJECT (returned) | tests pass; API regenerated | no verdicts on d067a19; no TRACEABILITY row; breaks the line cap; implementer, 10-18 |
| e2e fix TD-3/4/6/10 | ACCEPT WITH FOLLOW-UP | TD-3 fdd362b + test_e2e_server_tree.py; TD-4 root cause proven, #82 closed; TD-6 7.5 min -> 24 s | TD-10 only unblocked; no code-review record; test-engineer, 10-30 |
| PR 0 golden grid | ACCEPT WITH FOLLOW-UP | 192-case grid and edge tests pass; TS APPROVE (ADR 0023 A1) | its line cap is red at the tip; CTO ruling today |

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
