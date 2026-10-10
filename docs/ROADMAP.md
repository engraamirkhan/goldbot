# goldbot program roadmap

2026-10-10 · program director · base `677bb53`. Sources: the design's 5 phases and 4 gates, BACKLOG, TRACEABILITY,
HANDOFF, the Q1 2027 pre-registration. Re-planned each cycle.

**What is not guaranteed.** No strategy is net-profitable today. M1–M2 are engineering and owner work. M3 is a
calendar event whose verdicts may all be negative. M4–M6 need an edge, so their dates are earliest-possible. Unmet
gates extend a phase; they are never waived, and the stop rule applies.

## 1. Milestones

| # | Milestone | Window | Exit criteria (source) |
| --- | --- | --- | --- |
| M1 | Engineering complete for demo | 2026-10-24 – 11-07 | Branch batch shipped through CI with HIGH/CRITICAL closed (exits: trading-safety; discovery: quant; SQLite and accounts: security). In-flight lanes (P6/P7, gap_watch, cross-feed, A10, H-01) and wave 1 merged. Gates green; main merged back. |
| M2 | Running on Oracle demo | Owner steps + 1–2 weeks; realistic 2026-11-14 – 12-12 | `bridge-smoke` round-trips 0.01 lot on demo. `health` green for 24 h, pasted on #34. Telegram alerts and approvals work. A restore test passes. Measured spread and swap in `state/costs_<account>.json`. |
| M3 | Q1 2027 trials run, with verdicts | Freeze by 2026-12-31; run 2027-01-04 – 02-28 | Owner rulings A–D recorded. Each trial has a `preregistered` row with the pre-registration commit id. Every planned trial (≤ 13 of 20) is read only by its written rule. Reports show gross and net R, per-year table and DSR with the real trial count. Holdout untouched. |
| M4 | A strategy passes the gates and enters shadow | Earliest 2027-03; realistic Q2–Q3 2027; **may not happen** | Research gates: ≥ 1,500 candidates, ≥ 60 per fold, positive in ≥ 3 years including 2021 or 2022, DSR ≥ 0.95 on ≥ 200 trades. Design gate "backtest to paper": DSR ≥ 1.0 on the true trial count, ≥ 500 backtest trades, live-tick spread model. Positive on the holdout, scored once. Shadow then runs ≥ 4 weeks or 40 trades. |
| M5 | Paper → tiny-live gate | Earliest 2027-10 (6 months of paper after M4); realistic 2028 H1 | Gate "paper to tiny live": ≥ 150 paper trades; expectancy within 50% of backtest; fills within 30% of target; chaos drill passed. P7 job writes `met`. The owner records the gate, runs `unlock_live` and types the phrase. Risk is 0.1% per trade. |
| M6 | Full size | Earliest 2028-10 (12 months tiny live); realistic 2029 | Gate "tiny live to full size": ≥ 300 live trades; expectancy within 40% of paper; drawdown under 1.5× backtest; brokers within 15% of each other. The stop rule stays in force. |

**Trade-count note.** Slow TSMOM makes about 29 trades a year, so the 150-trade paper gate would take about 5 years.
A daily-signal survivor slips M5 and M6 by years unless the owner rules on thresholds before paper starts (D2).

## 2. Critical path and dependencies

```
branch reviews close -> PR -> CI green -> M1
owner steps (Oracle VMs, MT5 VNC login, keyring, settings.local.yaml, tunnel, bot) -> bridge-smoke -> M2
M2 -> measured swap and >= 50 fills -> costs-v1 release --+
owner rulings A-D (floor, budget, feed, instruments) -----+-> freeze (2026-12-31) -> research.yml trials -> M3
data-v1 (weekly) + macro-v1 + discovery tooling ----------+
M3 positive verdict -> gates -> holdout once -> shadow -> M4 -> 6 months of paper -> M5 -> 12 months -> M6
```

- **Owner steps** gate M2 week for week. M3 slips only if they run past about 2026-12-15 (cost table for the freeze).
- **costs-v1** refuses to publish with fewer than 50 fills. With no strategy trading, demo fills will not build up
  (D7). Without the table, trials run on the priors and say so.
- **CI minutes.** 2,000 free minutes a month; October's ran out on 2026-10-06. 13 trials × 30–60 min plus PR CI
  nearly fills a month, so the self-hosted runner (`CI_RUNNER`) on the brain VM must be live before January.
- **Data.** `data-v1` grows weekly; coverage issues #2–#32 need triage before the freeze; `macro-v1` is needed
  for H-06 and H-02.

## 3. Next two waves

One worktree per lane on the branch tip, `-n 4`. Lanes avoid in-flight files (`engine/runner.py`,
`research/discovery.py`, `goldbot/db`, `api/auth.py`, P6/P7 and gap_watch files, `specialists/tsmom.py`). Every lane
also needs code-reviewer and test-engineer. **Shared files:** `ops/run.py` and `ops/jobs.py` take additive entries
merged by the integrator; the OpenAPI contract is regenerated once per batch.

### Wave 1: 2026-10-12 – 10-24, feeds M1/M2

| Lane | Item (rows) | Owner agent | Files | Extra reviewers |
| --- | --- | --- | --- | --- |
| W1-1 | Bridge smoke command (BACKLOG 1, X6–X10) | implementer | new `ops/smoke.py`, its test, one `run.py` entry | trading-safety, security, sre |
| W1-2 | Nightly backups to Object Storage and a weekly restore test (state-store proposal) | sre | `ops/linux/backup.sh`, systemd timer units, RUNBOOK section | security |
| W1-3 | Chaos-drill harness (gate 2 criterion) | sre | new `scripts/chaos_drill.py`, its test | trading-safety |
| W1-4 | Daily M1 reconciliation, session state from `session_deals` (D12, D13) | data-engineer | new `data/reconcile.py`, `data/calendar.py` | trading-safety |
| W1-5 | H-13 cost-filter overlay; freeze prep with registry rows and a budget-guard test (BACKLOG 5) | strategy-researcher | new `research/costfilter.py`, `research_plan.json` (not the H-01 entry), budget tests | quant |
| W1-6 | Coverage triage (#2–#32) and `macro-v1` verification | data-engineer | new `scripts/check_coverage.py`, `data-macro.yml` | security (workflow) |
| W1-7 | Deterministic attribution backend (BACKLOG 12, G2) | performance-analyst | new `research/attribution.py`, golden-file test, `director.py` priority hook | quant |
| W1-8 | Walk-forward reads settings; CUSUM tuned to 5% quarterly false alarms (M15, M25) | implementer | `research/walkforward.py`, `research/drift.py` | quant |

### Wave 2: 2026-10-26 – 11-14, after the M1 PR

| Lane | Item (rows) | Owner agent | Files | Extra reviewers |
| --- | --- | --- | --- | --- |
| W2-1 | State store step 2: orders, approvals and risk state in `core.db` with fsync | implementer | `goldbot/db`, `engine/runner.py` persistence, `telegram/bus.py` | trading-safety, security, sre |
| W2-2 | `order_calc_margin` and tick-flags dedup (R8, D11) | implementer | `risk/gate.py`, `execution/mt5_adapter.py`, broker protocol | trading-safety |
| W2-3 | Attribution, stop-rule and gate screens | ui-ux-designer | `goldbot/api` (read-only routes), `web/` | ui-ux, code |
| W2-4 | Wine/bridge hardening from M2 findings (watchdog, reconnect, terminal restart) | sre | `ops/linux/`, `execution/bridge.py` | security, trading-safety |
| W2-5 | Manual-dispatch deploy workflow with health check and rollback (auto-deploy off until D6) | sre | new `.github/workflows/deploy.yml`, `ops/linux/goldbot-deploy.sh` | security |
| W2-6 | Combinatorial purged CV, run quarterly (M16) | implementer | new `research/cpcv.py` | quant |
| W2-7 | 4h bars across the daily break; swap-day costs (D7, D4) | data-engineer | `data/resample.py`, `execution/costs.py` | quant |

Wave 3 (shares `runner.py` with W2-1): A2, D10, M9, M3 15m leg. Parked until an edge exists: M28/A5, M10, F12.

## 4. Risk register (top 10)

| # | Risk | L | I | Owner agent | Mitigation |
| --- | --- | --- | --- | --- | --- |
| 1 | **No edge found:** every Q1 verdict negative | High | High | strategy-researcher | Rules written before data; 7-trial reserve; stop rule; post-null levers go to the owner |
| 2 | **Cost underestimation:** swap, slippage or spread worse than modelled, so a gross edge turns negative | High | High | performance-analyst | costs-v1 from measured fills; H-13 overlay; "fills within 30%" gate |
| 3 | **Owner availability:** owner steps or decisions late | Med | High | program-director | Batched queue (section 5); each step scripted in RUNBOOK; dated asks on #34 |
| 4 | **Wine/MT5 reliability:** terminal hangs, logs out or misses ticks under Wine | Med | High | sre | W1-1 smoke, 24 h health, W2-4 watchdog; fail closed (R25, X8); Windows VPS fallback |
| 5 | **Single-host failure:** brain VM down means no exits managed in the engine | Med | High | sre | Server-side SL/TP (X5); heartbeat alerts (S5); W1-2 backups; scripted rebuild |
| 6 | **Oracle capacity or reclamation:** Always Free VMs unavailable or reclaimed when idle | Med | Med | sre | Keep VMs busy; scripted bootstrap; costed paid fallback |
| 7 | **CI minutes exhausted** before or during Q1 | Med | Med | release-manager | Self-hosted runner before January; no `full_refresh` on hosted runners; one PR per batch |
| 8 | **Public repo security:** identifiers or secrets leak; workflow abuse | Low | High | security-reviewer | S4 scan on every push; keyring and `settings.local.yaml` only; security review of workflows and deploy scripts |
| 9 | **Data-feed gaps:** Dukascopy, FRED or broker gaps or artefacts | Med | Med | data-engineer | Quarantine (D22); coverage triage (W1-6); D12 reconciliation; cross-feed survival (F10) |
| 10 | **Model drift** after promotion | Med | Med | quant-reviewer | PSI/ECE size-down and CUSUM halt (M26/M27), calibrated by W1-8; owner-only clear |

## 5. Owner decision queue

**Batch 1: by 2026-10-31 (unblocks M2 and the freeze)**

| # | Decision | Recommendation |
| --- | --- | --- |
| D1 | Event floor for daily-signal families | **400**, with t ≥ 2.0 unchanged. With 1,000, H-01 cannot pass by construction |
| D2 | Sign-off of the PROPOSED gate thresholds (P7) | Approve the design's values, and rule now whether a low-frequency strategy gets a time-plus-count variant (e.g. 12 months and ≥ 60 trades, same expectancy test); deciding later is rule-fitting |
| D3 | Owner steps: Oracle account and 2 VMs, MT5 VNC login, keyring secrets, `settings.local.yaml` (owner email, Telegram ids), tunnel, bot | Complete in one sitting, following RUNBOOK; about 2–3 h |
| D4 | Budget: trial budget and monthly spend | Keep 20 trials a quarter. Cap spend at about $50 a month (Oracle free tier, staff agents within $40) |

**Batch 2: by 2026-12-15 (before the freeze)**

| # | Decision | Recommendation |
| --- | --- | --- |
| D5 | Paid economic-calendar consensus feed (H-11) | Not for Q1. Revisit if Q1 is null |
| D6 | Automatic deploys to the servers | Manual dispatch until M2 has run cleanly for 4 weeks |
| D7 | Demo cost-probe orders (0.01 lot, through RiskGate, demo only) so costs-v1 reaches 50 fills | Yes. Measured costs are the cheapest lever on tsmom |
| D8 | Other instruments (silver, large-tick futures) | Defer until H-01's verdict. If slow trend stops, research them first, with no broker change |

**Batch 3: at the M3 verdicts (about 2027-03)**: continue, re-scope or apply the stop rule, with options prepared
by the strategy-researcher.
