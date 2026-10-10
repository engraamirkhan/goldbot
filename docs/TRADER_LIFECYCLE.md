# goldbot against a top gold trader's lifecycle

2026-10-10 · architect review · sources: `docs/DESIGN.md`, `HANDOFF.md`, `docs/TRACEABILITY.md` (row ids below),
`goldbot/agents/`, `goldbot/research/{population,director}.py`, `goldbot/ops/jobs.py`, `goldbot/engine/runner.py`.

**No strategy makes money after costs yet.** Five families show no gross edge; tsmom's small gross edge
(+0.06 R per trade, t 2.4–2.6) turns negative after costs (−0.079 R at 1h, −0.014 R at 4h before
swap). Meta-models add nothing (out-of-fold AUC ≈ 0.50). The Q4 trial budget is spent (20 of 20). So the gaps below
are tagged **[edge]** when they help *find* an edge and **[run]** when they help *run* one safely. Until something
passes the gates, the engines propose no trades, by design.

## 1. Lifecycle table

| Stage | What goldbot does today (file / row) | Status | Gap |
| --- | --- | --- | --- |
| Market and macro preparation | Forex Factory calendar with tiers and the tier-1 blackout (`data/econ_calendar.py`, job `calendar_archive`, R20); headlines scored by an LLM, plus a 30-min block after a news shock (`data/news.py`, D16); `macro_news_analyst` writes the pre-session briefing; loaders for FRED, COT and GLD (`data/macro.py`, D14) and an as-of feature `f_macro` (`features/session.py`) | partial | **[edge]** Macro is never used: `research/pipeline.py:25` leaves `macro` out of `DEFAULT_FEATURE_NAMES`, no workflow pulls FRED/COT/GLD (the sandboxes cannot reach those hosts), and the allocator has no real-yield or DXY input. Central-bank and ETF flows arrive only as headlines. |
| Regime and volatility assessment | Rule-table allocator using ADX, ATR quartile and blackout (`allocator/rules.py`, M8); realised-vol features | partial | M9: the allocator's own blackout never fires live. M10: no learned allocator. There is no persisted, reported regime state, and macro regime inputs are missing. **[edge]** |
| Idea and setup generation (15m/1h/4h/1d) | Six families: `session_open`, `mean_reversion`, `intraday_momentum` (15m); `trend`, `breakout`, `tsmom` (1h; 4h and 1d research only). Higher-timeframe context via `features.mtf` (D15) | partial | **[edge]** 4h is research-only because `saturday_retrain` skips timeframes with no settings window. 1d cannot reach the screen's 1,000-event floor (454 events). No level-based or structure families: KOG levels and TradingView signals are missing (F12). Only tsmom has gross signal. |
| Setup selection and confluence | Primary-signal screen (M33); LightGBM meta-labeller (F5); pooled model (M34); EV > 0 and 2.5× cost gates (R27, R28); allocator weights | partial | All the mechanics are built, but there is no confluence skill to select with (AUC ≈ 0.50). The TradingView filter features (F12) are missing. |
| Entry timing | Rule triggers on bar close; the owner confirms within 90 s, and the RiskGate runs again at approval (A1, A3) | partial | M3: trend's 15m execution leg is missing. D10: bar close is detected by the next tick, not by the clock. |
| Position sizing and portfolio risk | RiskGate sizing, per-trade clamp, multiplier bounds, 1.2× skip (R1–R6, M11); 2 positions per account (R19); combined exposure cap (R7); supervisor caps (S1–S4) | done | R8: `order_calc_margin` is not called. There is no portfolio volatility target; correlation between agents enters only through tournament fitness. **[run]** |
| Execution quality | Nightly spread, slippage, swap and commission tables (X9, X10, D4); `execution/audit.py` with the `execution_auditor`; paper broker (X4); account classifier acting on account class (X11–X14); bridge safety | partial | **[run]** Nothing has been measured on a real terminal yet. X6: no alert on `FAILED_EXEC`. Only market orders are used; there is no study of entry order type (limit vs market). |
| Trade management | Server-side SL/TP at entry (X5); time exit (`Engine._manage_open`); Friday weekend rule (R22); reconciliation every 30 s (X8) | partial | Exits declared in `exit_policy` are not executed: trend trailing (M3), breakout scale-out and trailing (M6), session-open hard flat (M7). R21: positions are not closed early in a blackout when p < 0.5. **[run]**, and **[edge]** because labels can only test exits the engine can run. |
| Exit | Deterministic exits, never gated (A6); 12% kill switch closes at market (R15) | partial | Barrier and time exits are done. Policy exits are missing (row above). |
| Journaling and review | `decisions` journal; reject reason codes (A4); weekly `journal_coach` | partial | M28: no TreeSHAP per decision. A5: proposals show gain importance instead. No post-trade review after each exit, which the design calls for. |
| Performance attribution | Shadow book records every candidate (M35); population stats (G2); execution audit; the LLM `improvement_agent` scorecards; dev agent `performance-analyst` | partial | No deterministic attribution by regime, session or side. Today it comes only from LLM prose. **[edge]** |
| Learning and adaptation | Saturday retrain (M20); shadow promotion gates (M21–M23); CUSUM rollback (M24); weekly recalibration (M36); tournament (G1–G6); director plus analyst (G10); drift and health (M26, M27: implemented and tested, uncommitted on this branch) | partial | The machinery is complete, but there is no edge for it to adapt. Missing: CPCV (M16); M25 (CUSUM not tuned to a false-alarm rate). |
| Capital and drawdown management | Daily and weekly caps (R9–R12); staged 8% and 12% drawdown stages with re-arm probation (R13–R18); demo first with a phase gate (P1–P4) | partial | **[run]** P6: the stop rule is not in code. P7: gate thresholds are not computed. A10: the auto-mode offer is missing. R11: the owner gets no message when the weekly cap trips. |
| Operational resilience | NSSM or systemd restarts; idempotent order ids (X7); supervisor fails closed (S3, S4); health checks (`ops/health.py`); Oracle/Wine bridge | partial | S5: no heartbeat alert, and only the engines have heartbeats. D12: no daily M1 reconciliation. MT5 under Wine is unverified on the real VM. Backups were not checked in this review. |

## 2. Self-sustaining loop

**What runs unattended** (`config/settings.yaml: scheduler`, `ops/jobs.py JOBS`):

| Cadence (UTC) | Job | Effect |
| --- | --- | --- |
| Weekdays 06:10 / 06:30 | `calendar_archive`, `agents_presession` | Blackout calendar; macro briefing |
| Weekdays 23:10 | `nightly_costs` (+ classifier on Friday) | Measured costs feed the engines and retraining |
| Daily 23:30 / 23:40 | `model_watch`, `drift_watch` | CUSUM rollback; PSI and ECE size-downs, agent and system halts |
| Weekdays 23:45 | `agents_daily` | Data steward, risk officer, execution auditor |
| Sat 03:17 | Actions `data-dukascopy.yml` | Appends last week's bars |
| Sat 06:00 → 11:30 → 12:00 → 12:30 → 13:00 | `saturday_retrain` → `recalibrate` → `tournament` → `research_director` → `agents_weekly` | Challengers; bounded calibrator refit; retire, promote and clone; trial plan; coach, improvement agent, analyst (≤ 2 trials) |
| First Sunday 08:00 | `monthly_research` | Label grid (paused) |
| Continuous | engines, supervisor, news, Telegram | Ticks, shadow book, exits, reconciliation |

**What still needs the owner.** Entry confirmations. VPS setup and every credential. Committing
`config/costs_measured.json`. Clearing drift halts (`run.py drift-review --clear`). Re-arming after the kill switch.
Approving allocator, rule and label changes. Raising `trial_budget_quarter`. Adding instruments. The live unlock and
its typed phrase. Merging PRs. Dispatching `research.yml` trials.

**What to automate next, without loosening any rail:**
- Publish the canonical cost table as a release asset that `research.yml` fetches, so the owner no longer has to
  commit it. The values are measured, not chosen, so this does not weaken the cost model.
- Add a weekly `data-macro.yml` Actions pull of FRED, COT and GLD with `available_utc`, published like `data-v1`.
- Compute the stop rule (P6) and gate status (P7) every day. Breaching the stop rule halts entries automatically,
  which is the safe direction; resuming stays with the owner.
- Telegram alerts for S5, X6 and R11. These add information only.
- At each quarter start, auto-draft the pre-registered trial queue from `research_plan.json` and the hypotheses.
  Dispatch stays inside the budget guard, which already refuses over-budget runs.

## 3. "Spawn agents as required"

**Today.** Trading agents spawn only by cloning. `Population.tournament` gives each top-quartile live agent 2–3
mutated shadow children (parameters, feature seed, or timeframe), capped at 24 shadow and 12 live. Founders are
fixed: one per registered family (`ensure_founders`). Staff agents are a fixed `ROLES` table run on a schedule, with
read-only tools. Their only writes are `file_hypothesis`, `update_hypothesis` and the analyst's bounded `run_trial`
(±50% overrides, at most 2 per run, charged to the trial registry). Spend is capped at $40 a month. Cloning needs a
live winner, so today the population cannot grow.

**Proposed bounded design.** Add a deterministic `gap_watch` job (daily, after `drift_watch`) that writes
`state/gaps.json`. Nothing spawns without a gap record, and every spawn goes through existing code paths.

| Gap detected (deterministic) | Who may spawn | What | Budget cap | Gates |
| --- | --- | --- | --- | --- |
| Data quality: error `dq_events` above baseline, a stale feed, or a source missing (D22) | `gap_watch` | On-demand run of the existing `data_steward` with the gap as `extra_context` | Role per-run cap; ≤ 3 on-demand staff runs a week; inside the $40 monthly cap | Read-only tools of the role; output is a report or hypothesis; fixes go by PR |
| Drift halt or system halt (M26, M27) | `gap_watch` | On-demand `risk_officer` explanation | as above | Read-only; the halt stays until the owner clears it |
| Uncovered timeframe: a timeframe in a family's `timeframes` with no shadow agent | `Population.spawn_founder` (new) | Shadow founder = an existing family at that timeframe, default config | ≤ 2 gap founders a month; ≤ 4 shadow slots reserved inside `SHADOW_CAP` | Zero capital; promotion to live needs `research_passed` for its exact config **and** DSR > 0.95 with n_pop trials (G6) |
| Regime gap: every family's allocator weight below the 0.2 gate for 20+ sessions | `research_director` | Extra focus in the plan; may file 2 hypotheses | Comes out of the quarter's trial budget (never added to it) | Screen (M33), walk-forward gates, holdout once |
| Hypothesis rated `tested_promising` by the analyst | `Population.spawn_founder` | Shadow founder with the trial's config | Same founder caps; at most one per trial id | As for founders above |
| A new family or a new staff role | Nobody automatically | Code via PR (strategy-researcher → quant-reviewer → implementer; security review for roles) | Owner merges | CI; the written rationale the design requires |

Invariants, each to be tested first:
- Staff agents never get order, model or settings tools. `ReadOnlyTools.call` already refuses tools outside a role.
  Spawned runs reuse existing roles and never get new tools.
- A spawned trading agent always starts as `status="shadow"` with `capital_weight=0`. Its config must pass
  `validate_overrides`-style bounds, and no founder may spawn in a family flagged `lookahead`.
- Each founder raises `n_pop`, the DSR's trial count, so spawning makes promotion harder.
- No spawning while `system_halt` is set or a drawdown stage is at 8% or worse.
- Every spawn is logged with its gap id in the member's `notes`.

Tests first: `tests/test_population.py::test_gap_founder_starts_in_shadow_with_zero_capital`,
`::test_gap_founders_respect_the_monthly_and_reserved_slot_caps`; `tests/test_jobs_integration.py::test_no_spawn_during_system_halt`.

Reading taken: the design says "new specialists need a written rationale". This design reads that as covering new
*families*, so those stay PR-only. New *configs* of registered families are covered by the existing clone rule (G3),
so automation may spawn them.

## 4. Prioritised roadmap

| # | Gap | Why it ranks here | Kind | Effort | Rows |
| --- | --- | --- | --- | --- | --- |
| 1 | Close the tsmom cost gap. First commit the measured costs (the swap prior of −60 USD per lot per night is probably pessimistic). Then run the pre-registered Q1 trial: tsmom 4h with `max_bars` 12. | It is the only gross edge. Net is −0.014 R before swap at 4h, so a small cost or horizon change decides it. | edge | S (owner step) + 1 trial | X9, X10, D4 |
| 2 | Macro and positioning data in research: an Actions pull, `macro` in the candidate pool, and real-yield/DXY regime inputs | Gold's best-documented drivers are absent from every model. | edge | M | D14 (loader only); new row |
| 3 | Live exit policies (trailing, scale-out, hard flat) and the R21 early blackout close, then exits as a research dimension | Momentum edges are made in the exits. Labels cannot test exits the engine cannot run. | edge + run | M | M3, M6, M7, R21 |
| 4 | Ship drift and health (implemented, uncommitted on this branch) through CI; calibrate the CUSUM | Needed safety the moment a champion exists | run | S | M26, M27, M25 |
| 5 | Stop rule, gate thresholds and the auto-mode offer in code | Governance the design commits to before the first paper trade | run | S | P6, P7, A10 |
| 6 | Bounded spawning (section 3), including the 4h founder path in `saturday_retrain` | Explores uncovered timeframes and regimes unattended, under the same gates | edge | M–L | G3, G6; new rows |
| 7 | Cross-feed check (Dukascopy vs broker) and the other-feed spike match | Stops a feed artefact being "found" as an edge once real broker data accumulates | edge (honesty) | M | F10, D23, D20 |
| 8 | Ops alerts and verification: service heartbeats, `FAILED_EXEC` and weekly-cap Telegram alerts, `order_calc_margin`, daily M1 reconciliation, MT5-under-Wine smoke test | Needed for unattended running on demo, then live | run | S–M | S5, X6, R11, R8, D12 |

Deliberately left out: TreeSHAP per decision (M28), CPCV (M16), the learned allocator (M10) and TradingView features
(F12). None of them can create an edge before items 1–3 show one. Raising the trial budget and adding instruments
(silver) stay owner decisions. If items 1–2 leave net expectancy at or below zero, those are the next levers, and the
design's stop rule applies.
