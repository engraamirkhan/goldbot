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
Status: ready / in progress / in review / done / rejected.

## Ranked items

| # | Item | Stage · kind | Size | Status |
| --- | --- | --- | --- | --- |
| 1 | MT5-under-Wine demo smoke test on Oracle | Operational resilience, execution · RUN | S | ready |
| 2 | Measured cost table published as a release asset for research | Execution quality · FIND | S | done (PR #62) |
| 3 | Drift and health (M26/M27) | Learning and adaptation · RUN | S | done (PR #60) |
| 4 | FRED macro data pipeline | Market and macro preparation · FIND | M | done (PR #61) |
| 5 | Q1 2027 pre-registered trial queue | Idea generation · FIND | S | in progress (draft; freeze by 2026-12-31) |
| 6 | Live exit policies and blackout early close | Trade management, exit · FIND + RUN | M | in review (safety fixes) |
| 7 | Ops alerts: heartbeats, FAILED_EXEC, weekly cap | Operational resilience · RUN | S–M | done (PR #62) |
| 8 | Stop rule and gate thresholds in code | Capital and drawdown management · RUN | S | done (owner sign-off and engine trade record open) |
| 9 | UI/UX approval card | Entry timing (owner approval) · RUN | M | done (PR #61) |
| 10 | Trader-toolkit features (sessions, S/R, FVG, order blocks) | Idea generation · FIND | M | done (PR #61) |
| 11 | Trader-toolkit evaluation as primary signals | Idea generation, confluence · FIND | S–M | ready (via H-02 in Q1) |
| 12 | Deterministic attribution that feeds back into research | Performance attribution, learning · FIND | M | in review (daily report + staff-agent feedback done; director input and dashboard open) |
| 13 | Bounded spawning (`gap_watch`) and the 4h founder path | Learning and adaptation · FIND | M–L | in review (gap_watch and the 4h retrain path done; 1d research-only) |
| 14 | Cross-feed check (Dukascopy vs broker) | Idea generation (honesty) · FIND | M | done (2026-10-10; promotion gate once 90 days of broker M1 exist) |
| 15 | Auto-mode offer after 100 proposals | Entry timing, governance · RUN | S–M | in review |
| 16 | Swap in the live EV check (playbook G-1) | Capital management, costs · RUN | S | proposed |
| 17 | Holiday, daily-reopen and Monday-open entry rules (G-2) | Operational risk · RUN | S–M | proposed (owner: windows) |
| 18 | Tier-2 event blackout and proximity feature (G-3) | Market and macro preparation · RUN | S | proposed (owner: adopt) |
| 19 | Open-risk (heat) cap and same-direction stacking (G-4) | Capital and drawdown management · RUN | S | proposed (owner: value) |
| 20 | Drawdown / risk-of-ruin Monte Carlo report (G-5) | Capital and drawdown management · RUN | S | proposed |
| 21 | Per-agent loss-streak and entry-rate throttle (G-6) | Discipline (system analogue) · RUN | S | proposed (owner: thresholds) |
| 22 | Min-lot sizing-feasibility report (G-7) | Position sizing · RUN | S | proposed (owner: account size for H-01) |
| 23 | MAE/MFE in closed-trade and shadow records (G-9) | Post-trade review · FIND | S | proposed |
| 24 | DST-aware sessions and deterministic calendars (D-1, D-2) | Idea generation (honesty) · FIND | M | proposed |
| 25 | Minimum-lot exception in RiskGate (ADR 0004) | Position sizing · RUN | S | ready (trading-safety review) |
| 26 | Milestone ladder and monthly % / R progress report (ADR 0004) | Performance review, capital growth · RUN | S–M | ready |
| 27 | Broker contract terms recorded from the demo terminal (ADR 0004) | Execution, position sizing · RUN | S | ready (after 1) |

### 1. MT5-under-Wine demo smoke test on Oracle
Value: until a real terminal has run, no cost, fill or reconciliation number in the system is measured. Every later
item rests on them (RUN).
Acceptance:
- A `run.py bridge-smoke` command, with a unit test against the paper broker, connects through the bridge. It
  prints account login, server, balance and a live XAUUSD tick, then opens and closes 0.01 lot on a demo account. It
  exits non-zero if the account is not classified as demo.
- On the Oracle VM, `run.py health` reports the engine, bridge, supervisor and scheduler as running for 24 h with
  no stale-engine entry. The output is pasted on issue #34.
- `state/costs_<account>.json` carries a measured spread and at least one measured swap after the first rollover.

Depends on: owner steps (VM provisioned, demo credentials in the keyring). Rows: X6–X10 unverified on real
hardware; lifecycle "Operational resilience".

### 2. Measured cost table published as a release asset
Value: removes the owner's manual commit step and makes `research.yml` charge measured swap, commission and slippage
instead of the pessimistic priors. That alone may move tsmom 4h across zero (FIND).
Acceptance:
- A scheduled job (or the `nightly_costs` job) exports the canonical cost table and uploads it to release `costs-v1`
  as `costs_measured.json`, with `measured_at` and the fill count per field.
- `research.yml` fetches that asset. Its report states, per field, whether the value is measured or a prior.
  Integration test: with a fixture asset, the pipeline uses the asset values. Without it, it falls back to the priors
  and says so.
- The export refuses (non-zero, no upload) when swap is unmeasured or there are fewer than 50 fills, so a prior is
  never published as a measurement.

Depends on: 1 for real values (the plumbing can land first). Rows: X9, X10, D4.

### 3. Drift and health (in review, PR #60)
Value: halts or sizes down a decaying champion the moment one exists (RUN).
Acceptance: CI green on PR #60. Tests cover the PSI and ECE size-down, residual CUSUM halt and system halt.
`run.py drift-review --clear` is the only path that clears a halt. TRACEABILITY M26/M27 read implemented.
Rows: M26, M27. M25 (CUSUM calibration) stays under "later".

### 4. FRED macro data pipeline (in progress)
Value: gold's best-documented drivers (real yields, the dollar) are absent from every model today (FIND).
Acceptance:
- A weekly `data-macro.yml` publishes DFII10, T10YIE, DTWEXBGS and GVZCLS with `available_utc` set to the
  publication time plus lag, as Parquet on a release, loaded like `data-v1`.
- `macro` is in the research candidate pool. The lookahead check passes on it. Every join to bars goes through
  `asof_join` on `available_utc`, and a test fails if a value is visible before `available_utc`.
- The allocator can read a real-yield and dollar regime input (20-day change), with a unit test.

Rows: D14 (loader only today); new row for the pull.

### 5. Q1 2027 pre-registered trial queue
Value: spends the Q1 budget on the hypotheses research ranks highest, written down before any result is seen (FIND).
Acceptance:
- `research_plan.json` holds, dated before 2027-01-01, these entries, each with a reading rule written in advance:
  - slow tsmom (4h bars, 1d trend condition, long `max_bars`, long and short, measured swap), superseding
    `tsmom 4h max_bars 12`;
  - Asia-session drift (00:00–07:00 UTC on 1h bars, flat before London);
  - the macro-conditioned variant of slow tsmom, whose reading rule requires 2022–24 to be positive.
- The budget guard accepts the queue on 2027-01-01 and refuses anything beyond the quarter's budget (existing test
  extended).
- Each trial's report issue shows gross and net R, the per-year table and DSR with the real trial count.

Depends on: 2 (costs), 4 (variant 3). The daily-family event floor is an owner decision (below).

### 6. Live exit policies and blackout early close (in review)
Value: momentum edges are made in the exits. Labels cannot test exits the engine cannot run, and exits are never
gated, so they must work unattended (FIND + RUN).
Acceptance:
- The engine executes each family's `exit_policy`: trend trails at 1.5 ATR once 1.25 ATR in profit (M3); breakout
  closes half at 1.0 ATR and trails the rest at 1.0 ATR (M6); session-open goes flat 1 h before the next session
  (M7). Each has a test on the paper broker with a scripted price path.
- Shadow-book parity: the shadow book and the label generator produce identical outcomes for each policy on the
  same path (extends the existing `triple_barrier` parity test).
- R21: in a tier-1 blackout, open positions whose re-scored p < 0.5 are closed at market. Tested.
- No exit path consults the approval queue (test asserts it).

Rows: M3, M6, M7, R21.

### 7. Ops alerts
Value: an unattended system must tell the owner when it is silent, failing to fill or capped (RUN).
Acceptance:
- Every service (engine, bridge, scheduler, supervisor, API, Telegram) writes a heartbeat. A Telegram alert fires
  within 6 minutes of 5 minutes' silence, once per incident, with a recovery message. Tested with a fake clock (S5).
- A `FAILED_EXEC` after the retries sends a Telegram alert with account, side, lots and retcode (X6).
- A weekly 5% cap trip sends the owner a Telegram message once per week per account (R11).
- Alerts only add information: a test asserts that no alert path changes orders, sizing or halts.

Rows: S5, X6, R11.

### 8. Stop rule and gate thresholds in code
Value: governance the design commits to before the first paper trade, and the safe-direction automatic halt (RUN).
Acceptance:
- A daily job computes P6 (after 18 months, more than 500 pooled trades and a lower 90% bound on expectancy below
  zero, or any single loss larger than the weekly cap). A breach sets the existing system halt. Only the owner clears
  it. Tested on synthetic trade sets on each side of each condition.
- P7: the trade-count and time thresholds for each phase gate live in `settings.yaml`. A job writes `met / not met`
  with the evidence to `state/phase_state.json`. It never flips the phase itself. Tested.
- The dashboard shows the stop-rule and gate status (API schema regenerated).

Depends on: the owner signs off the threshold values. Rows: P6, P7.

Status (2026-10-10): done in `goldbot/ops/gates_phase.py`, `run.py gates` / `gate-evidence`, health checks
`stop_rule` and `phase_gates`, `settings.yaml` `gates:`; tests in tests/test_phase_gates.py. Differences from the
acceptance above, as briefed for this change: a stop-rule breach is a health FAIL and a Telegram alert that
recommends /halt; it does not set the system halt (the RiskGate's caps are unchanged). The report goes to
`state/gate_report.json`, not into `phase_state.json`, so a report can never be read as a recorded gate. The health
pass (every 60 s) evaluates it, so no scheduler job was added. Open: owner sign-off of the PROPOSED values; the
engine appending `ClosedTrade` rows to `state/closed_trades.jsonl` (engine owner); the dashboard card (api/web
owners; the report JSON is ready to serve).

### 9. UI/UX approval card (in progress)
Value: the owner's one click is the only manual step, so the card must carry everything needed to decide in 90 s
(RUN).
Acceptance: the card shows account, direction, lots, stop, target, p, top features, current spread and a countdown.
Approve and reject each take one click and are re-checked by the RiskGate. The e2e test covers approve, reject and
expiry. The Telegram and dashboard approvals resolve the same proposal exactly once. Rows: A1, A3, A5 (gain
importance until M28).

### 10. Trader-toolkit features (in progress)
Value: gives research the tools top discretionary gold traders use (session ranges, S/R, fair-value gaps, order
blocks) as point-in-time features (FIND).
Acceptance: each tool is a registered feature family with a docstring definition. The lookahead check passes on
truncated history. A unit test on a hand-built bar series shows each FVG and order block appearing only after the
bar that confirms it.

### 11. Trader-toolkit evaluation as primary signals
Value: answers, with numbers, whether these tools carry a gross edge on gold, instead of assuming it (FIND).
Acceptance:
- Each tool is run as a primary-signal event study through the P4 screen on pre-holdout data: events, gross R, t,
  and net R at measured costs, on one issue per tool.
- The runs are pre-registered and charged to the trial registry (Q1 2027 budget). The holdout is not touched.
- A tool that fails the screen is recorded as `tested_negative` in the hypotheses. One that passes goes to the
  walk-forward gates like any family.

Depends on: 10, 2. Note: research C3 says 15m/1h trend variants are a known null. The level and structure tools are
not covered by that null, which is why they get a test.

### 12. Deterministic attribution that feeds back
Value: analytics today are LLM prose. A deterministic table lets the director, tournament and owner act on where
P&L actually comes from (FIND).
Acceptance:
- A weekly job writes `state/attribution.json` from the shadow book and live journal: R, count and t by family,
  timeframe, session, side, regime and cost component. Golden-file test on a fixture journal.
- `research_director` reads it. A test shows a family or session with negative attributed R over 20+ trades
  reduces that family's priority in the plan.
- A dashboard attribution view (API schema regenerated, e2e smoke).

Rows: lifecycle "Performance attribution"; G2.

Status (2026-10-10): in review. Done: daily (not weekly) job `attribution` -> `state/attribution.json` and `.md`
(goldbot/research/attribution.py) from the shadow book, fills, orders and the cost table, by family, agent,
timeframe, session, side, regime, decision, exit and cost component, small cells marked noise; tests on a synthetic
shadow book (tests/test_attribution.py) instead of a golden file (determinism is tested: same book, same bytes); the
improvement agent and research analyst read it (`read_attribution`). Open: `research_director` priority input (a
change to research budget allocation, so it needs quant review and its own test), the dashboard view (goldbot/api
and web/ were out of scope), live realised R (item 8's engine trade record). Row G12.

### 13. Bounded spawning and the 4h founder path
Value: explores uncovered timeframes and regimes unattended, under the same gates, so the system keeps evolving
without a live winner to clone (FIND).
Acceptance: as in TRADER_LIFECYCLE section 3, with these tests passing:
- `tests/test_population.py::test_gap_founder_starts_in_shadow_with_zero_capital`;
- `::test_gap_founders_respect_the_monthly_and_reserved_slot_caps`;
- `tests/test_jobs_integration.py::test_no_spawn_during_system_halt`.

Also: `saturday_retrain` covers 4h, every spawn raises `n_pop`, and every spawn is logged with its gap id.

Status (2026-10-10): `gap_watch` and `Population.spawn_founder` are in review with the three tests above (G11).
4h founder path (2026-10-10): settings carry a 4h walk-forward window (48/6/6, purge 10 d, embargo 4 d), so
`saturday_retrain` trains 4h agents into challengers and gap_watch spawns 4h founders
(`tests/test_jobs_integration.py::test_a_4h_agent_retrains_into_a_challenger`,
`tests/test_gap_watch.py::test_a_4h_founder_is_spawned_for_an_uncovered_4h_timeframe_and_1d_stays_refused`); the
engine needed no change (`tests/test_engine.py::test_4h_agent_decides_only_on_4h_closes_and_its_shadow_trades_count_4h_bars`).
Still open: 1d. The engine keeps ~40 trading days of 1m bars (`EngineConfig.max_bars_in_memory`), short of the 120
daily bars a frame needs, so a daily agent would never decide; gap_watch keeps refusing 1d founders with a BACKLOG
suggestion until the engine holds daily history (an engine change, trading-safety review).

Rows: G3, G6, G11.

### 14. Cross-feed check
Value: stops a broker-feed artefact being "found" as an edge once broker data accumulates (FIND, honesty).
Acceptance: a signal is dropped unless its gross edge survives on both Dukascopy and broker bars over the overlap
(F10, D23). The spike rule checks the other feed before flagging (D20). Tests use synthetic diverging feeds.

### 15. Auto-mode offer
Value: the owner's one-click approval stays the default, and `/mode auto` becomes available only on evidence (RUN).
Acceptance: `/mode auto` is refused until 100 proposals with no RiskGate breach and no statistically
distinguishable difference between approved and rejected outcomes. The test covers 99 vs 100 and the difference
test on both sides. The offer is a message. Switching still needs TOTP. Row: A10.

### 16–24. From the XAUUSD trader playbook (strategy researcher, 2026-10-10)
Source and evidence: `docs/research/xauusd-trader-playbook.md` section 6 (gap ids in brackets). Ranked by the
researcher. The product owner re-ranks against items 1–15. None of these uses trial budget. Engine, risk and
execution items need trading-safety review. Feature items need quant review.
- **16 (G-1).** The gate's EV adds expected swap in ATR (side, nights implied by `max_bars`, triple Wednesday).
  Test: a 4h long whose EV is positive before swap and negative after is refused; shorts are unchanged.
- **17 (G-2).** Holiday calendar (US, UK, JP, CN, 24 Dec–2 Jan, early closes): no entries on those days. No
  entries for 15 min after each daily reopen and 30 min after the weekly open. Fake-clock tests. Exits are untouched.
- **18 (G-3).** Tier-2 USD events (PPI, retail sales, ISM, GDP, JOLTS, claims, FOMC minutes, Fed chair) get a
  −5/+15 min entry blackout from config, plus a `min_to_next_tier2` feature. A test per tier.
- **19 (G-4).** Sum of open risk in R per account and combined ≤ the configured cap. Same-side positions of
  different agents count as one bet. Gate test on each side of the cap.
- **20 (G-5).** Monthly bootstrap of shadow or backtest R at live risk settings: P(8%), P(12%), P(weekly cap) in
  6 and 12 months. Reporting only. Golden test on a fixed seed.
- **21 (G-6).** After 3 consecutive full stop-outs in a risk day, or more than N entries in a session, an agent
  makes no entries until the next session. Logged and alerted. Never affects exits.
- **22 (G-7).** `run.py sizing-feasibility`: per agent, the minimum equity at which the minimum lot stays within
  1.2× target risk at the current ATR and phase risk rate. Written as phase-gate evidence.
- **23 (G-9).** Maximum adverse and favourable excursion in R on every shadow and closed trade, shown by exit
  type in attribution.
- **24 (D-1, D-2).** Session windows, zones and cost-table sessions follow London and New York local time.
  Deterministic calendars become PIT features: holidays, COMEX option expiry and first notice, Lunar New Year,
  Indian festivals, month and quarter end. Truncation test. Must land before H-04 and H-14 run.

### 25–27. From ADR 0004 (starting account £50 and the daily target, 2026-10-10)
Source: `docs/decisions/0004-starting-account-and-daily-target.md`. At £50 RiskGate refuses every trade (0.01 lot
risks 16–354% of equity); these items make the small-account path explicit and measurable. None uses trial budget
or touches the holdout.
- **25. Minimum-lot exception (needs trading-safety review).** Value: lets tiny-live trade one minimum lot at small
  equity without loosening any limit (Position sizing · RUN). Acceptance:
  - New `risk.min_lot_risk_cap` in settings (validated 0 < x ≤ `max_risk_per_trade`, or null). With null, every
    existing gate test passes unchanged and a test shows decisions identical to today on a grid of equities and
    stops.
  - Applies only when `lots_raw < volume_min`: the gate allows exactly `volume_min` iff its realised risk ≤ the cap
    (half the cap in `SIZE_DOWN` or combined size-down), else refuses `min_lot_exceeds_risk`. Tests, tiny-live, 15m
    stop $10.90: equity $1,500 allowed at 0.01 lot (0.73%); $1,000 refused (1.09%); $1,500 in `SIZE_DOWN` refused
    (cap 0.5%); `lots_raw ≥ volume_min` unchanged; never more than `volume_min` under the exception.
  - Margin (max(broker, 1:20), 300% floor), combined notional, caps, stages, blackouts and spread checks still run
    after it: a test at $600 equity with a stop that fits the cap is refused `margin_level_floor`.
  - An allowed exception decision carries reason `min_lot_exception`, realised risk and the phase rate; it reaches
    the decisions log and the approval card's risk line. Exits untouched (test: exit path never calls the gate).
  - Reviews: trading-safety, code; DESIGN Sizing paragraph and TRACEABILITY row updated in the same change.
  Depends on: nothing; 19 (heat cap) must count exception trades in full when it lands.
- **26. Milestone ladder and monthly % / R report.** Value: reports progress the way ADR 0004 decided (monthly %
  and R, equity against the next milestone), never £/day (Performance review · RUN). Acceptance:
  - A monthly report (job + dashboard card + one Telegram line) with time-weighted % return net of deposits and
    withdrawals, net R, R/trade with trade count and 90% interval, max drawdown %, all by timeframe; golden test on a
    fixture with a mid-month deposit (the deposit is not counted as return).
  - The milestone ladder (equity at which the minimum lot fits 15m/1h/4h/1d at the 1% cap and at 0.5%) is computed
    from the sizing-feasibility report (item 22) at the current price and measured ATR, not hard-coded; test that a
    50% higher ATR raises every threshold by 50%.
  - No £/day figure or target anywhere in the report, card or digest (test on the rendered text).
  - Reviews: code, ui-ux (screen and Telegram), quant (the R interval). Read-only: no trading-safety review.
  Depends on: 22.
- **27. Broker contract terms recorded.** Value: replaces the ADR's unverified leverage, minimum lot, step, contract
  size and stop-out level with the terminal's own values (Execution · RUN). Acceptance:
  - On the demo terminal, `state/broker_terms_<account>.json` records `volume_min`, `volume_step`, contract size,
    account leverage, margin for 0.01 lot at the current price (`order_calc_margin`) and the stop-out level, with a
    timestamp; no login, balance or account number.
  - ADR 0004's assumptions table is updated with the measured values (or a note that they matched).
  - Reviews: code, sre. Read-only on the broker: no trading-safety review.
  Depends on: 1.

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
- **Trader-playbook risk policy** (items 17–22; playbook section 7): the weekend rule (recommend flat for intraday
  families), tier-2 and holiday windows, throttle and heat-cap values, edge-linked sizing (G-10). The account size
  and tiny-live risk for H-01 were decided under your delegation in ADR 0004 (minimum-lot exception at ≤ 1%; H-01
  stays in shadow until equity reaches ~£7,900).
- **Starting account (ADR 0004).** £50 places no trade under the rails; the £50/day goal is a long-run outcome at
  roughly £19k–38k equity. Yours: the deposit plan toward the first milestone (~£825 for 15m, ~£1,650 for 1h), and
  the demo balance (recommend the first milestone you expect to fund, not £50).
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
