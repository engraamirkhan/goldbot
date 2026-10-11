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
the rank. **Top priority (ADR 0004 amended, 2026-10-10):** 28 (currency-correct sizing, HIGH), then 25 (minimum-lot
exception) and 29 (absolute daily loss limit). Nothing trades on the £1,000 GBP demo without 28 and 25.

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
- **Automatic deployment to the trading servers (CD).** CI runs on every change. Deploying to the Oracle VMs
  automatically (on merge or tag, with health check and rollback) waits for your approval. Until then, deploys stay
  manual via `vps_update` / the Linux equivalents.
- **Event floor for daily-bar families.** P4 requires 1,000 events, and tsmom 1d has 454. Either keep the floor (slow
  tsmom runs on 4h bars with a 1d condition, item 5), or set a lower floor for daily families.
- **Large-tick instruments.** Research (Kurth et al.) suggests large-tick contracts keep trend where gold CFDs do
  not. Adding instruments (silver or others) is your call.
- **Trial budget.** `trial_budget_quarter` is 20, spent for Q4. Research does not recommend raising it. Item 11 adds
  toolkit trials that compete with item 5 for Q1's 20.
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
Three ready items with disjoint files, avoiding the four in-flight streams (`goldbot/features/`, `data/macro.py` +
`data-macro.yml`, `web/` + approvals, `research/drift.py`):

1. **Item 2, measured costs release.** Touches `.github/workflows/research.yml`, a new costs-publish step,
   `scripts/fetch_data_release.py` and `goldbot/execution/costs.py`.
2. **Item 6, live exit policies.** Touches `goldbot/engine/runner.py`, `goldbot/engine/shadow.py` and the
   `exit_policy` methods in `goldbot/specialists/`.
3. **Item 7, ops alerts.** Touches `goldbot/ops/health.py`, `goldbot/telegram/outbox.py` and the `FAILED_EXEC` path in
   `goldbot/execution/mt5_adapter.py` / `bridge.py`. It must not edit `engine/runner.py` (item 6): the engine
   heartbeat and the weekly-cap notice are read from existing state and journal files. It must coordinate with the
   approval card on `goldbot/telegram/`.

Item 1 needs the owner's VM, so it runs as an owner step alongside the wave.

## Later (parked)
- M28 TreeSHAP per decision, A5 per-trade SHAP, and a post-trade review after each exit.
- R8 `order_calc_margin` (touches the broker protocol; schedule after item 6).
- D10 bar close by clock, A2 window from settings, M9 allocator blackout input, D11
  `flags` dedup, D13 session state from `session_deals`, D7 4h bars across the daily break.
- M25 CUSUM calibrated to 5% quarterly false alarms; M15 walk-forward reads settings; U4 lockout timing.
- M16 CPCV quarterly; M10 learned allocator (after three months of data); F12 TradingView feature family.
- Economic calendar with consensus and surprises (needed for hypothesis 4); other C2 sources (COT, ETF holdings, GPR,
  GC basis).
- Entry order type study (limit vs market); portfolio volatility target (risk use only, not alpha, per C3).

## Rejected
- New 15m/1h trend, breakout, intraday momentum or mean-reversion variants, and meta-labelling over primaries with
  no gross edge. These are known nulls (research C3, issues #37–#53).
