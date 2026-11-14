# goldbot product backlog

2026-11-13 · product owner · base `da616da0` (triage; first written 2026-10-10 at `874262b`). Inputs:
`docs/research/profitability-2026-11-12.md` (live demo record), `docs/TRADER_LIFECYCLE.md`, `docs/TRACEABILITY.md`,
`HANDOFF.md`, the EVAL-2 pre-registration (ADR 0076/0077) and merged PRs to #383.

**Where we stand (2026-11-13).** No strategy is net positive after costs and none is a live candidate. Over 30 days
the signal arms are about break-even net (+0.03 to +0.06 R per trade on the two ICM accounts, -0.03 R on Vantage);
their random controls lose about the cost (-0.06 to -0.19 R); nothing clears the deflated bar at K 174 (Bonferroni
t about 3.6). The best account made 1.0 % in its best week against the owner's 20-30 % goal. Z10D, Z9D and Z5P are
the only arms ahead of their matched controls; H-90 (London only) is the one pre-registered session hypothesis.
So the ranking favours, in order: honest reads that can produce a live candidate (**PROVE**), an evaluation that
judges arms fairly against matched twins net of measured costs (**PROVE**), rails that keep exits running and stop
paying for noise (**RUN**), and cost and calendar fixes that change which arms pass (**FIND**). Every
expected-impact figure is uncertain; none of these items creates profit on its own.

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

**Triage 2026-11-13 (product owner; base da616da0; owner focus: profitability only).** Every fragment was checked
against `git log` on `da616da0` and the merged PRs (#60-#383). 68 items shipped and are
marked done with their PR or commit (among them 153-165, 170 and 174 from PRs #371-#381, the trades UI 128-139 from
#206/#211, the order-outcome work 71-87 from #85-#93, and 104 from #349); 27 are retired (21 whole, 6 remainders) with a one-line reason
(focus clean-up ADR 0066, the TradingView pull retired 2026-11-02, the Q1 2027 queue and the time-structure learning
loop superseded by EVAL-2, the holiday-list line superseded by 152); 23 are parked under "Later" with one
accountable role, UI and cosmetic items among them. One new item (188, survivor reads) leads the table. No ranked
item waits on the owner: 142 (b) is now a PD+PO decision after trading-safety review, 34's ADR 0006 ruling is
retired, 127's private-repository step was overtaken by deleting data-v1, and adopting the off-by-default rails
(17, 18, 19, 21, 35) only tightens, so the PD+PO decide on the trading-safety reviewer's recommendation. 15 ranked
rows.

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
Only money, going live and sizing stay here (owner rule; CLAUDE.md). Everything else is decided by the program
director with the product owner, the CTO on technical and the CFO on financial matters, and reported on #34.
- **Going live.** Demo until the paper -> tiny-live gate is recorded in `state/phase_state.json`, then `unlock_live`
  and the typed phrase. The pilot is deferred (owner, 2026-10-30) until a candidate passes 188's reads and the
  confirm window. Recommendation: no live step before one arm PASSES against its matched control at its
  pre-registered read; then the CFO prices the pilot.
- **Phase-gate thresholds** (item 8 values; ROADMAP OD-15: the paper mean's lower 95 % bound above 0).
  Recommendation: yes, as the quant reviewer advised.
- **Sizing and risk limits.** Live sizing, the daily loss limit and any loosening of RiskGate. Recommendation:
  unchanged until a live candidate exists.
- **Spending.** A paid news-scoring key for the shock blackout (142 (b) option 1). Recommendation: do not fund; the
  PD+PO take option 2 or 3 after trading-safety review. Any paid data feed (119, 123) goes through the CFO first;
  free routes only until then.
- **Automatic deployment to the trading servers (CD).** Deploys stay run by Claude end to end on request; automatic
  deploy on merge waits for the owner.

## Next parallel wave
Only items not yet done. The top three ready items have lanes; each lane touches different files.

1. **188 survivor reads** (evaluator, read-only on the brain; `docs/research/`): fill counts on 2026-11-20, verdicts
   at each pre-registered trigger.
2. **150 + 149 EVAL-2 runner checks** (implementer, one lane: `goldbot/research/eval2/` runners and
   `goldbot/research/catalogue/screen.py`, their tests).
3. **117 bad arm row keeps the engine up** (implementer: `goldbot/engine/exploration.py`,
   `goldbot/research/arms.py`, a new test file).
4. **145 N-3 enable** (data-engineer: `scripts/local_bars.sh` refresh and the overlap note; the research director's
   K row and `config/settings.yaml` `har_abstention` only after both).
5. **146 session-split cost** (implementer: `goldbot/research/catalogue/costs.py` and its test). Not in the same
   lane as 150/149 if those touch `costs.py`.
6. **152 exchange holidays** (data-engineer: `goldbot/research/catalogue/dispatcher.py` calendar input).

Starts after these: 148 Lane 5B (engine consumers; after 150/149 and S5 green), 144 (engine 1d/1w; runner files,
not in parallel with 148 5B), 90, 187 (once flat), 175 (after the first weekend BTCUSD hold), 142, 179, 105.

## Later (parked)
Each item's status names its accountable role and the trigger that brings it back.
- Rails built and off by default: 17, 18, 19, 21, 35. They only tighten; the trading-safety-reviewer recommends and
  the PD+PO decide, no owner input.
- 69, leverage and largest-order audit: folded into the finance-auditor's monthly audit (next 2026-12-04).
- 111 allocator against controls and 116 higher-timeframe features: return with a live candidate or a model lane.
- Data with no queued consumer: 119 COMEX volume, 121 broker history depth, 123 central-bank and ETF flows, 124
  second intraday source, 125 GPR/GDELT, 126 COT availability floor (before any positioning read), 147 ALFRED
  vintages.
- 143 `accounts delete` (security hygiene, LOW); 151 (only if Lane 3 adds a probe builder).
- UI and Telegram, at the bottom by the owner's profit focus: 93, 94, 96 (approval card, real money only), 98
  (Limits screen), 134 (Telegram push policy, matters once real money is live).
- Older parked lines: M28 TreeSHAP per decision, R8 `order_calc_margin`, D10/A2/M9/D11/D13/D7 engine refinements,
  M25 CUSUM calibration, M10 learned allocator, entry order type study (limit vs market).

## Done
**Triage 2026-11-13 (product owner, base da616da0).** Marked done from git, each with its PR or commit in the
item's status: 1, 16, 27, 36, 44, 51, 52, 59, 62, 67, 71, 72, 74-89, 92, 95, 99-101, 103, 104, 107, 112, 115, 118,
127-133, 135-141, 153-165, 170, 174. Retired with a reason in the status: 5, 11, 31-34, 45, 46, 53, 68, 70, 73, 97,
106, 108-110, 113, 114, 120, 122, and the remainders of 12, 13, 24, 26, 30 and 65. Acceptance for these was taken
from the merge (green CI and the reviews named in each PR); the criterion-by-criterion re-run was not repeated in
this sweep. Open sign-offs carried: quant for 23 (V6) and 50 (M45).

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
