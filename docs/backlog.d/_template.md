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
These are never decided by the product owner or by agents.
Every owner step below has `chaser: program-director`, who asks on #34 with a recommendation and a chase date.
- **Adopt the built, off-by-default rails (sweep 2026-10-16).** Holiday, reopen and tier-2 windows (17, 18), the
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
Only items that are not done (sweep 2026-10-16). The reviewer queue rule applies first: 44 and 52 have waited on
re-review since 2026-10-12, so reviewer lanes start before new builders.

1. **Reviewer lanes: 44 (trading-safety-reviewer) and 52 (security-reviewer, then trading-safety-reviewer).**
   Read-only. After they sign off, the release-manager ships the reviewed batch.
2. **Item 1, demo deploy and bridge smoke** (`lane/claude-run-deploy`): `docs/deploy/`, `goldbot/ops/linux/`, and
   the new `ops/smoke.py` (W1-1).
3. **Item 57, risk_day_anchor validator** (`lane/anchor-validator`): `goldbot/config.py`, its test, `docs/RUNBOOK.md`.
4. **Item 43, heartbeat while closed** (`lane/market-closed-state-cadence`): `goldbot/engine/runner.py` state write.
   54 also edits `runner.py` (`_refresh_account`), so 54 starts when this lane merges.
5. **Item 56, digest Yesterday line** (`lane/digest-yesterday`): `goldbot/telegram/digest.py`.
6. **Free now, disjoint:** 55 (`tests/test_auth_security.py`, test-engineer), 49 (`config/settings.yaml`
   `retired_families` and `docs/research/hypotheses.md` B, strategy-researcher), 45 (a new report script,
   performance-analyst).

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
Checked against git on 2026-10-16. Main merge SHAs: #60 7ef3a00, #61 8bc7952, #62 1a194e9, #63 5f7da74,
#68 e00af06, #71 423d2b1, #73 d28a482, #74 a913d42, #75 a52b389, #76 520d731, #77 bc030d3. Acceptance record: each
item merged on green CI, and the tests it names exist. The criteria were not re-run one by one in this sweep. The
product owner runs the criterion-by-criterion acceptance scorecard (AGENT_STANDARDS) for these by 2026-10-23.
- #60: 3. #61: 4, 9, 10. #62: 2, 7. #63: 6, 8, 12 (part), 13 (part), 14, 15. #68: 12 (director input), 20, 22.
- #71: 24 (D-1), 25, 26 (backend), 28, 29. #73: 23, 30a, 37, 38, 39, 40, 41; the approval-card lines for 25, 28 and 29.
- #74: 42. #76: 47. #77: 50.
- Open sign-offs on done items: quant for 23 (V6) and 50 (M43); the owner for the values of 8.

## Rejected
- New 15m/1h trend, breakout, intraday momentum or mean-reversion variants, and meta-labelling over primaries with
  no gross edge. These are known nulls (research C3, issues #37–#53).
