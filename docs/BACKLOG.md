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
| 2 | Measured cost table published as a release asset for research | Execution quality · FIND | S | ready |
| 3 | Drift and health (M26/M27) | Learning and adaptation · RUN | S | in review (PR #60) |
| 4 | FRED macro data pipeline | Market and macro preparation · FIND | M | in progress |
| 5 | Q1 2027 pre-registered trial queue | Idea generation · FIND | S | ready |
| 6 | Live exit policies and blackout early close | Trade management, exit · FIND + RUN | M | ready |
| 7 | Ops alerts: heartbeats, FAILED_EXEC, weekly cap | Operational resilience · RUN | S–M | ready |
| 8 | Stop rule and gate thresholds in code | Capital and drawdown management · RUN | S | ready |
| 9 | UI/UX approval card | Entry timing (owner approval) · RUN | M | in progress |
| 10 | Trader-toolkit features (sessions, S/R, FVG, order blocks) | Idea generation · FIND | M | in progress |
| 11 | Trader-toolkit evaluation as primary signals | Idea generation, confluence · FIND | S–M | ready (after 10) |
| 12 | Deterministic attribution that feeds back into research | Performance attribution, learning · FIND | M | ready |
| 13 | Bounded spawning (`gap_watch`) and the 4h founder path | Learning and adaptation · FIND | M–L | ready |
| 14 | Cross-feed check (Dukascopy vs broker) | Idea generation (honesty) · FIND | M | ready |
| 15 | Auto-mode offer after 100 proposals | Entry timing, governance · RUN | S–M | ready |

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

### 13. Bounded spawning and the 4h founder path
Value: explores uncovered timeframes and regimes unattended, under the same gates, so the system keeps evolving
without a live winner to clone (FIND).
Acceptance: as in TRADER_LIFECYCLE section 3, with these tests passing:
- `tests/test_population.py::test_gap_founder_starts_in_shadow_with_zero_capital`;
- `::test_gap_founders_respect_the_monthly_and_reserved_slot_caps`;
- `tests/test_jobs_integration.py::test_no_spawn_during_system_halt`.

Also: `saturday_retrain` covers 4h, every spawn raises `n_pop`, and every spawn is logged with its gap id.

Rows: G3, G6; new rows.

### 14. Cross-feed check
Value: stops a broker-feed artefact being "found" as an edge once broker data accumulates (FIND, honesty).
Acceptance: a signal is dropped unless its gross edge survives on both Dukascopy and broker bars over the overlap
(F10, D23). The spike rule checks the other feed before flagging (D20). Tests use synthetic diverging feeds.

### 15. Auto-mode offer
Value: the owner's one-click approval stays the default, and `/mode auto` becomes available only on evidence (RUN).
Acceptance: `/mode auto` is refused until 100 proposals with no RiskGate breach and no statistically
distinguishable difference between approved and rejected outcomes. The test covers 99 vs 100 and the difference
test on both sides. The offer is a message. Switching still needs TOTP. Row: A10.

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
- D10 bar close by clock, A2 window from settings, M9 allocator blackout input, D12 daily M1 reconciliation, D11
  `flags` dedup, D13 session state from `session_deals`, D7 4h bars across the daily break.
- M25 CUSUM calibrated to 5% quarterly false alarms; M15 walk-forward reads settings; U4 lockout timing.
- M16 CPCV quarterly; M10 learned allocator (after three months of data); F12 TradingView feature family.
- Economic calendar with consensus and surprises (needed for hypothesis 4); other C2 sources (COT, ETF holdings, GPR,
  GC basis).
- Entry order type study (limit vs market); portfolio volatility target (risk use only, not alpha, per C3).

## Rejected
- New 15m/1h trend, breakout, intraday momentum or mean-reversion variants, and meta-labelling over primaries with
  no gross edge. These are known nulls (research C3, issues #37–#53).
