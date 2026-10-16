# goldbot program roadmap

2026-10-16 · program director · base `28d2150`. Re-planned from Friday 16 October after the 2026-10-16 review
(`docs/process/scorecards/2026-W42.md`, program director graded D): the 13 Oct deploy was not chased, this file had
not been re-planned since 10-10, and 5 of 8 Wave 1 lanes never started. Sources: the design's 5 phases and 4 gates,
BACKLOG, TRACEABILITY, HANDOFF, the Q1 2027 pre-registration and the resume note. Re-planned at the start of every
session and on the day any date slips. Each row names one accountable role (docs/AGENT_STANDARDS.md "Accountability
and cadence"); owner rows also name the chaser.

**What is not guaranteed.** No strategy is net-profitable today. M1–M2 are engineering and owner work. M3 is a
calendar event whose verdicts may all be negative. M4–M6 need an edge, so their dates are earliest-possible. Unmet
gates extend a phase; they are never waived, and the stop rule applies.

## 1. Milestones

| # | Milestone | Accountable | Date | Exit criteria (source) |
| --- | --- | --- | --- | --- |
| M1 | Engineering complete for demo | release-manager | batch PR to `main` by Sat 2026-10-17; in-review lanes in a second PR by Fri 10-23; Wave 1 code by Fri 10-30 | Branch batch shipped through CI with HIGH/CRITICAL closed. In-flight lanes (BACKLOG 54, 56, 57, 58, 59, run.py split, auth e2e) and Wave 1 merged. Gates green; main merged back. |
| M2 | Running on Oracle demo | sre | brain and bridge up Fri 10-16; engine on at the reopen Sun 10-18 22:02 UTC; exit criteria by Fri 10-30 (was 11-14 – 12-12) | `bridge-smoke` round-trips 0.01 lot on demo. `health` green for 24 h, pasted on #34. Telegram alerts and approvals work. A restore test passes. Measured spread and swap in `state/costs_<account>.json`. The broker clock check holds through the DST-gap week (26–30 Oct). |
| M3 | Q1 2027 trials run, with verdicts | research-director | freeze commit by 2026-12-14; run 2027-01-04 – 02-28 | Owner rulings A–D recorded (done 2026-10-11). Each trial has a `preregistered` row with the pre-registration commit id. Every planned trial (≤ 13 of 20) is read only by its written rule. Reports show gross and net R, per-year table and DSR with the real trial count. Holdout untouched. |
| M4 | A strategy passes the gates and enters shadow | research-director | Earliest 2027-03; realistic Q2–Q3 2027; **may not happen** | Research gates: ≥ 1,500 candidates, ≥ 60 per fold, positive in ≥ 3 years including 2021 or 2022, DSR ≥ 0.95 on ≥ 200 trades. Design gate "backtest to paper": DSR ≥ 1.0 on the true trial count, ≥ 500 backtest trades, live-tick spread model. Positive on the holdout, scored once. Shadow then runs ≥ 4 weeks or 40 trades. |
| M5 | Paper → tiny-live gate | program-director | Earliest 2027-10 (6 months of paper after M4); realistic 2028 H1 | Gate "paper to tiny live": ≥ 150 paper trades; expectancy within 50% of backtest; fills within 30% of target; chaos drill passed. P7 job writes `met`. The owner records the gate, runs `unlock_live` and types the phrase. Risk is 0.1% per trade. |
| M6 | Full size | program-director | Earliest 2028-10 (12 months tiny live); realistic 2029 | Gate "tiny live to full size": ≥ 300 live trades; expectancy within 40% of paper; drawdown under 1.5× backtest; brokers within 15% of each other. The stop rule stays in force. |

**Trade-count note.** Slow TSMOM makes about 29 trades a year, so the 150-trade paper gate would take about 5 years.
A daily-signal survivor slips M5 and M6 by years unless the owner rules on thresholds before paper starts (OD-11).

## 2. Critical path and dependencies

```
hotfix PR #78 green -> merge -> deploy that SHA (Fri 10-16) -> backup keys before 22:15 UTC
BACKLOG 59 (state on host-clock cadence) reviewed + deployed (Sun 10-18 by 20:00 UTC) -> engine start 22:02 UTC
engine running -> bridge-smoke (W1-1) + 24 h health + restore drill -> M2 (by 10-30)
engine running through 26-30 Oct -> broker clock check in the DST gap (W1-9) -> M2
batch PR to main (Sat 10-17) -> WIP limit clear -> new builder lanes -> Wave 1 code -> M1 (10-30)
M2 -> measured swap and >= 50 fills -> costs-v1 (by 12-11, else "priors accepted" 12-14) --+
FRED_API_KEY secret -> calendar-v1; count-owed-events; H-06 table; H-13 (W1-5) -------------+-> freeze 12-14 -> M3
M3 positive verdict -> gates -> holdout once -> shadow -> M4 -> 6 months of paper -> M5 -> 12 months -> M6
```

- **Tonight's deploy** is Claude-run (memory "claude-runs-deploys"); the owner keeps only the sign-ups, MFA, CLI
  login, secrets typed into hidden prompts, the first SSH host-key check and the MT5 first login.
- **Engine start is held** until the Sunday reopen: with the market closed, `bridge_compat` FAILs falsely (state is
  written on tick time, health ages it on wall time). BACKLOG 59 fixes it. If 59 is not reviewed by Sun 12:00 UTC,
  the engine starts anyway at the reopen and the weekday 20:59–22:02 UTC break FAIL is accepted and silenced on
  Healthchecks until 59 deploys (new date Tue 10-20); the program director tells the owner on #34 that day.
- **WIP limit is breached today.** 31 reviewed commits have been off `main` since 10-12, so no new builder lane
  starts until the batch PR is open (AGENT_STANDARDS "Delivery rules"). The PR is the first unblocker.
- **Reviewer queue.** trading-safety holds 54 and 58 in review, with 57 and 59 arriving: over the limit of 2, so
  a second trading-safety reviewer lane starts before any new trading-safety builder.
- **costs-v1** refuses to publish with fewer than 50 fills. With no strategy trading, demo fills will not build up
  (OD-13). Without the table, trials run on the priors and say so.
- **CI minutes.** 2,000 free minutes a month; October's ran out on 2026-10-06. The self-hosted runner (`CI_RUNNER`)
  on the brain VM must be live before January (Wave 2).
- **Data.** `macro-v1` was published on 10-13; `calendar-v1` waits on the FRED_API_KEY secret (owner, chased today).
  Coverage issues #2–#32 were triaged on 10-16: data-v1 lost 21 research-window weekdays and 42 holdout weekdays
  in the 10-06 full refresh. Five freeze gaps G1–G5 (data-engineer; G3 research-director) are on the Q1 readiness
  list (`docs/research/preregistration-2027Q1.md`, item 5). H-01 is at risk (152 vs the floor of 150 on gapped
  data); its recount and the G3 recount are one count-owed-events run after G1 closes (11-06), by 11-13, checked by
  the evaluator by 11-14.

## 3. Waves

One worktree per lane on the integration branch tip; pytest `-n 2` while the load is above 24, otherwise `-n 4`.
At most 8 builder lanes at once. Every lane also needs code-reviewer and test-engineer. **Shared files:**
`ops/run.py` and `ops/jobs.py` take additive entries merged by the integrator (after the run.py split merges, new
commands go in its new modules); the OpenAPI contract is regenerated once per batch. Each lane's Definition of done
is its role file's "Definition of done" plus S1–S12.

### Wave 1: Fri 2026-10-16 – Fri 10-30 (re-planned 10-16; was 10-12 – 10-24), feeds M1/M2

**Dated events**

| When (UTC) | Event | Accountable | Depends on |
| --- | --- | --- | --- |
| Fri 10-16 evening | W1-0 Claude-run demo deploy on Oracle (brain + Wine bridge, uk-london-1): PR #78 merged, its SHA and bootstrap hashes confirmed by the owner, MT5 first login, setup, dead-man URL, dashboard enrolment | sre (release-manager signs the SHA) | owner "oracle ready"; OCI script security review GO; owner approves each deploy command |
| Fri 10-16 before 22:15 | Backup keys set, so `job:backup` passes at 22:15 | sre | W1-0 |
| Sat 10-17 | Batch PR to `main` opened (31 commits, secret scan, full gates incl. `--web`); merged on green; main merged back | release-manager | PR #78 merged |
| Sun 10-18 10:00 | First `restore_drill` passes | sre | backup keys |
| Sun 10-18 20:00 | BACKLOG 59 reviewed and deployed, or the fallback above declared | implementer | trading-safety + sre review |
| Sun 10-18 22:02 | Engine start at the reopen: preflight `server_clock` matches, health with no FAIL, Healthchecks UP, step 8b rehearsal, `/status` | sre | 59 or fallback |
| Mon 10-19 – Tue 10-20 | 24 h health green pasted on #34; first `feed_reconcile` and `broker_ledger` runs read | sre | engine start |
| Sun 10-25 / Mon 10-26 – Fri 10-30 | UK clocks go back Sun 10-25; DST-gap week: daily `server_clock` check against ADR 0005 while US is still on summer time; again Mon 11-02 after the US change | sre | engine running all week |

**Lanes**

| Lane | Item (rows) | Accountable | Due | Status / start | Files | Depends on | Extra reviewers |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R-59 | Market-closed state cadence (BACKLOG 59) | implementer | Sun 10-18 20:00 | running | engine state write path | — | trading-safety, sre |
| R-56 | Digest "Yesterday" on the opportunity day; min_lot help text (BACKLOG 56) | implementer | Tue 10-20 | running | telegram digest | — | ui-ux |
| R-57 | risk_day_anchor validator (BACKLOG 57) | implementer | Wed 10-21 | running | config validation | each server's local settings checked first | trading-safety |
| R-SWP | Backlog status sweep, park to 15 | product-owner | Sun 10-18 | running | `docs/backlog.d/` | — | — |
| R-RD | Research dispatch (`data-us-calendar`, count-owed-events on `main`) | research-director | Sun 10-18 | running | workflow runs, research docs | FRED_API_KEY for calendar-v1 | — |
| R-H06 | H-06 run-input table | strategy-researcher | Fri 10-23 (was 11-08) | running | research inputs | — | quant |
| R-AUTH | Auth e2e fixes (19/19 Playwright) | implementer | Mon 10-19 | running; security review | `web/` e2e, test mailer | — | security |
| R-OCI | OCI provisioning script fixes | sre | Fri 10-16 (tonight) | running; security review | `scripts/deploy/` | — | security |
| V-54 | HWM confirmation (BACKLOG 54) | trading-safety-reviewer | Sun 10-18 | in review; ADR number clash (two 0017s) fixed before merge | — | — | trading-safety |
| V-58 | Heartbeat clock-ahead (BACKLOG 58) | trading-safety-reviewer | Sun 10-18 | in review | — | — | trading-safety, sre |
| V-RUN | run.py split | code-reviewer | Sat 10-17 | in review; keep the httpx line after basicConfig at merge | — | — | sre |
| W1-1 | Bridge smoke command (BACKLOG 1, X6–X10) | implementer | built Wed 10-21; run on demo Thu 10-22 | first free builder slot after the batch PR; not started (was due 10-24) | new `ops/smoke.py`, its test, one command entry | batch PR open; run.py split merged; W1-0 | trading-safety, security, sre |
| W1-2 | Nightly backups and the weekly restore drill | sre | Sun 10-18 | code done (`ops/backup.py`); verified by the 22:15 run and the Sunday drill | — | W1-0 | security |
| W1-3 | Chaos-drill harness (gate 2 criterion) | sre | Fri 10-30 | not started; starts when R-OCI frees | new `scripts/chaos_drill.py`, its test | batch PR open | trading-safety |
| W1-4 | Reconciliation on the running demo (D12, ledger) and session state from `session_deals` (D13) | data-engineer | read Tue 10-20; D13 Fri 10-30 | re-scoped: D12 and the broker ledger are built; D13 is partial | `data/calendar.py` | engine running; R-59 merged | trading-safety |
| W1-5 | H-13 cost-filter overlay; freeze prep with registry rows and a budget-guard test (BACKLOG 5) | strategy-researcher | Fri 10-30 | not started (checked 10-16); **starts Mon 10-19** (research-director, 10-16 job check; R-H06's withdrawal work is done) | new `research/costfilter.py`, `research_plan.json` (not H-01/H-06), budget tests | R-H06 merged | quant |
| W1-6 | Coverage triage (#2–#32); `macro-v1` docs corrected | data-engineer | triage Tue 10-20; script Tue 10-27; repair (G1, G2) Fri 11-06 | triage done 10-16 (gaps G1–G5 on the Q1 readiness list); script after the batch PR | new `scripts/check_coverage.py`, `data-macro.yml` | — | security (workflow) |
| W1-7 | Attribution director hook and dashboard (BACKLOG 12) | performance-analyst | Fri 10-30 | in review since 10-10; resumes once trades exist | `research/attribution.py`, `director.py` hook | engine running | quant |
| W1-8 | Research callers pass settings to walk-forward (M15); CUSUM done (M25) | implementer | Fri 10-30 | not started; lowest Wave 1 priority | `research/walkforward.py` callers | batch PR open | quant |
| W1-9 | DST-gap-week broker clock check | sre | Fri 10-30, plus Mon 11-02 | dated; needs the engine running | none (preflight runs, report on #34) | engine start | trading-safety |

**Can start NOW** (within the WIP and reviewer limits): the batch PR (release-manager), a second trading-safety
reviewer lane for 59 then 57, the W1-6 triage (read-only, no code), and tonight's deploy. Every other new lane is
a builder and waits for the batch PR; the next builder slots go to W1-1, then W1-5 and W1-3 as running lanes free.

### Wave 2: Mon 2026-11-02 – Fri 11-20, after the M1 PR

| Lane | Item (rows) | Accountable | Files | Extra reviewers |
| --- | --- | --- | --- | --- |
| W2-1 | State store step 2: orders, approvals and risk state in `core.db` with fsync | implementer | `goldbot/db`, `engine/runner.py` persistence, `telegram/bus.py` | trading-safety, security, sre |
| W2-2 | `order_calc_margin` and tick-flags dedup (R8, D11) | implementer | `risk/gate.py`, `execution/mt5_adapter.py`, broker protocol | trading-safety |
| W2-3 | Attribution, stop-rule and gate screens | ui-ux-designer | `goldbot/api` (read-only routes), `web/` | ui-ux, code |
| W2-4 | Wine/bridge hardening from M2 findings (watchdog, reconnect, terminal restart) | sre | `ops/linux/`, `execution/bridge.py` | security, trading-safety |
| W2-5 | Manual-dispatch deploy workflow with health check and rollback (auto-deploy off) | sre | new `.github/workflows/deploy.yml`, `ops/linux/goldbot-deploy.sh` | security |
| W2-6 | Combinatorial purged CV, run quarterly (M16) | implementer | new `research/cpcv.py` | quant |
| W2-7 | 4h bars across the daily break; swap-day costs (D7, D4) | data-engineer | `data/resample.py`, `execution/costs.py` | quant |
| W2-8 | Self-hosted CI runner on the brain VM | release-manager | runner unit, workflow `runs-on` | security, sre |
| W2-9 | Azure Windows MT5 and Tailscale, as an update to the Oracle deploy | sre | `scripts/deploy/`, RUNBOOK | security |

Wave 3 (shares `runner.py` with W2-1): A2, D10, M9, M3 15m leg. Parked until an edge exists: M28/A5, M10, F12.

## 4. Owner steps

| Step | Due | Status | Chaser |
| --- | --- | --- | --- |
| Oracle sign-up (UK South, MFA, budget alert of 1), `oci setup config` in Terminal, API key uploaded | Fri 10-16 | waiting on "oracle ready" | program-director |
| BotFather token, Telegram id, Healthchecks check | Fri 10-16 | asked | program-director |
| First SSH to each box and host-key check; MT5 first login over VNC; secrets in the hidden prompts | Fri 10-16 | during the deploy | program-director |
| FRED_API_KEY repository secret | Sun 10-18 | asked 10-11; chased 10-16 | program-director |
| Batched decisions (section 6) | Sun 10-18 – Fri 10-30 | asked 10-16 on #34 | program-director |

## 5. Risk register (top 12)

| # | Risk | L | I | Owner agent | Mitigation |
| --- | --- | --- | --- | --- | --- |
| 1 | **No edge found:** every Q1 verdict negative | High | High | strategy-researcher | Rules written before data; trial reserve; stop rule; post-null levers go to the owner |
| 2 | **Cost underestimation:** swap, slippage or spread worse than modelled, so a gross edge turns negative | High | High | performance-analyst | costs-v1 from measured fills; H-13 overlay (W1-5); "fills within 30%" gate |
| 3 | **Owner availability:** owner steps or decisions late | Med | High | program-director | Claude-run deploys; one batched ask (section 6) with deadlines; every owner step chased on its day |
| 4 | **Wine/MT5 reliability:** terminal hangs, logs out or misses ticks under Wine | Med | High | sre | W1-1 smoke, 24 h health, W2-4 watchdog; fail closed (R25, X8); Azure Windows fallback (W2-9) |
| 5 | **Single-host failure:** brain VM down means no exits managed in the engine | Med | High | sre | Server-side SL/TP (X5); heartbeat and dead-man alerts; backups and the restore drill (W1-2); scripted rebuild |
| 6 | **Oracle capacity or reclamation:** A1 out of capacity, or idle VMs reclaimed | Med | Med | sre | E2.1.Micro brain fallback tonight; Pay As You Go raises A1 priority; keep VMs busy; scripted bootstrap |
| 7 | **CI minutes exhausted** before or during Q1 | Med | Med | release-manager | Self-hosted runner (W2-8) before January; one PR per batch |
| 8 | **Public repo security:** identifiers or secrets leak; workflow abuse | Low | High | security-reviewer | S4 scan on every push; httpx kept below INFO (token in journald fixed); keyring and local settings only |
| 9 | **Data-feed gaps:** Dukascopy, FRED or broker gaps or artefacts | Med | Med | data-engineer | Quarantine (D22); coverage triage (W1-6); reconciliation (W1-4); cross-feed survival (F10) |
| 10 | **Model drift** after promotion | Med | Med | quant-reviewer | PSI/ECE size-down and CUSUM halt (M25–M27); owner-only clear |
| 11 | **Review bottleneck:** trading-safety holds 4 items against a limit of 2; load near 100 | High | Med | program-director | Second trading-safety reviewer lane now; builders capped at 8; `-n 2` above load 24; one-pass reviews |
| 12 | **DST gap and market-closed clocks:** false FAILs or a wrong broker offset between 10-25 and 11-01 | Med | Med | sre | BACKLOG 59 before engine start; W1-9 daily clock check 26–30 Oct and on 11-02 |

## 6. Owner decision queue

Asked once, batched, on #34 on 2026-10-16. Each item has a recommendation and a deadline; the finance ids are in
`docs/finance/recommendations-2026-10.md`.

| # | Decision | Recommendation | Deadline |
| --- | --- | --- | --- |
| OD-1 | Narrow allow rules for the one demo-only deploy entry point | Yes | Fri 10-16, before the deploy |
| OD-2 | Standing approval for demo deploys of a reviewed `main` SHA, and a daily agent budget | Yes for demo only (never live; result on #34 within 24 h); owner names the figure | Fri 10-16 |
| OD-3 | Pay As You Go on Oracle | Yes, with the budget alert and a free-shapes-only quota | Fri 10-16 if A1 is out of capacity, else Fri 10-23 |
| OD-4 | Finance D6: demo is a Raw Spread account | Confirm Raw | Fri 10-16, at the MT5 login |
| OD-5 | FRED_API_KEY repository secret | Set it | Sun 10-18 |
| OD-6 | `gates.max_gross_share` | 0.05 | Sun 10-18 |
| OD-7 | `risk.equity_check_tolerance` | 0.02 (ADR 0016) | Sun 10-18 |
| OD-8 | Ledger `balance_tolerance` 0.05 plus 0.01 per deal | Approve (ADR 0015) | Sun 10-18 |
| OD-9 | `telegram.owner_tz` | Europe/London | Sun 10-18 (UK clocks change 10-25) |
| OD-10 | Commit the uncommitted docs in the main checkout (finance, hosting options, speed-up review) | Yes, after a secret scan, in the next PR | Sat 10-17 |
| OD-11 | GitHub "Keep my email private" and "Block command line pushes" | Turn both on; do not rewrite history | Sat 10-17, before the batch push |
| OD-12 | Finance D1, D3, D4 | D1 (b) about $10 a month until a strategy passes; D3 Tailscale-only; D4 also track demo cover | Fri 10-23 |
| OD-13 | Finance D2 (hosting after the Azure trial), D5 (deposits) | D2 decide on the Wine soak's evidence; D5 no recommendation | D2 by 2027-08-13; D5 open |
| OD-14 | Gate thresholds sign-off and a low-frequency variant; demo cost-probe orders; automatic deploys | Brought separately with options | Mon 11-30 (before the freeze) |
