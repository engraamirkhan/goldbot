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
commands go in its new modules); the OpenAPI contract is regenerated once per batch. `engine/store.py` (ADR 0023
step 1) belongs to the TD-2 split lane; W2-1 adds `CoreDbEngineStore` there, and its protocol changes are additive
only (S6). Each lane's Definition of done
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
| R-56 | Digest "Yesterday" on the opportunity day; min_lot help text (BACKLOG 56) | implementer | Tue 10-20 | done 10-16 (ba1c520, in PR #79; accepted by the product owner) | telegram digest | — | ui-ux |
| R-57 | risk_day_anchor validator (BACKLOG 57) | implementer | Wed 10-21 | done 10-16 (f7d48db, in PR #79; accepted; sre server check before the #79 deploy) | config validation | each server's local settings checked first | trading-safety |
| R-SWP | Backlog status sweep, park to 15 | product-owner | Sun 10-18 | done 10-16 (13 ranked; 48, 49, 54-57 accepted; daily scorecard 2026-10-16) | `docs/backlog.d/` | — | — |
| R-RD | Research dispatch (`data-us-calendar`, count-owed-events on `main`) | research-director | Sun 10-18 | running | workflow runs, research docs | FRED_API_KEY for calendar-v1 | — |
| R-H06 | H-06 run-input table | strategy-researcher | Fri 10-23 (was 11-08) | running | research inputs | — | quant |
| R-AUTH | Auth e2e fixes (19/19 Playwright) | implementer | Mon 10-19 | running; security review | `web/` e2e, test mailer | — | security |
| R-OCI | OCI provisioning script fixes | sre | Fri 10-16 (tonight) | running; security review | `scripts/deploy/` | — | security |
| V-54 | HWM confirmation (BACKLOG 54) | trading-safety-reviewer | Sun 10-18 | done 10-16: APPROVED on re-verify, merged 17c5e0d (ADR 0018), in PR #79 | — | — | trading-safety |
| V-58 | Heartbeat clock-ahead (BACKLOG 58) | trading-safety-reviewer | Sun 10-18 | in review | — | — | trading-safety, sre |
| V-RUN | run.py split | code-reviewer | Sat 10-17 | in review; keep the httpx line after basicConfig at merge | — | — | sre |
| W1-1 | Bridge smoke command (BACKLOG 1, X6–X10) | implementer | plan Mon 10-19 (A-PLAN); built Fri 10-23; run on demo Mon 10-26 (re-dated 10-16 from built 10-21 / run 10-22: not started, no plan yet, and the builder slot waits on the trading-safety review queue and the engine start Sun 10-18; still inside M2's 10-30) | not started; builder starts the day the plan lands | new `ops/smoke.py`, its test, one command entry | batch PR open; run.py split merged; W1-0 | trading-safety, security, sre |
| A-PLAN | Architect plans: W1-1 bridge smoke (files, `ops/smoke.py` interface, failing tests first, fail-closed on a refused order) and W2-1 state store step 2 (from `docs/proposals/2026-10-state-store.md`) | architect | W1-1 plan Mon 10-19; W2-1 plan Wed 10-21; both by Wed 10-21 at the latest | not started; read-only lane, starts Sat 10-17 (plans are returned to the program-director, who records them as HANDOFF fragments) | none (read-only) | — | — |
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
| W2-1 | State store step 2: orders, approvals and risk state in `core.db` with fsync (architect plan by Wed 10-21, lane A-PLAN) | implementer | `goldbot/db`, `engine/runner.py` persistence, `telegram/bus.py` | trading-safety, security, sre |
| W2-2 | `order_calc_margin` and tick-flags dedup (R8, D11) | implementer | `risk/gate.py`, `execution/mt5_adapter.py`, broker protocol | trading-safety |
| W2-3 | Attribution, stop-rule and gate screens; UX spec `docs/ux/progress-screens.md` ready Fri 10-16 (due 10-23), with its API gaps listed; build in Wave 2 after the quant review of the verdict wording | ui-ux-designer | `goldbot/api` (read-only routes), `web/` | ui-ux, code |
| W2-4 | Wine/bridge hardening from M2 findings (watchdog, reconnect, terminal restart) | sre | `ops/linux/`, `execution/bridge.py` | security, trading-safety |
| W2-5 | Manual-dispatch deploy workflow with health check and rollback (auto-deploy off) | sre | new `.github/workflows/deploy.yml`, `ops/linux/goldbot-deploy.sh` | security |
| W2-6 | Combinatorial purged CV, run quarterly (M16) | implementer | new `research/cpcv.py` | quant |
| W2-7 | 4h bars across the daily break; swap-day costs (D7, D4) | data-engineer | `data/resample.py`, `execution/costs.py` | quant |
| W2-8 | Self-hosted CI runner (on the brain VM only with a security-reviewer GO first: fork PRs on a public repo must never reach a host holding trading secrets; see risk 14) | release-manager | runner unit, workflow `runs-on` | security, sre |
| W2-9 | Azure Windows MT5 and Tailscale, as an update to the Oracle deploy | sre | `scripts/deploy/`, RUNBOOK | security |

Wave 3 (shares `runner.py` with W2-1): A2, D10, M9, M3 15m leg, and **W3-1**: state-store follow-on after W2-1
(tech-debt TD-13: step 3 remainder, steps 4-5 and 8, `state_db` health, `run.py migrate`; implementer; plan Fri
11-20, JSON exports retired Fri 12-11; reviewers trading-safety, sre). Parked until an edge exists: M28/A5, M10, F12.

## 4. Owner steps

| Step | Due | Status | Chaser |
| --- | --- | --- | --- |
| Oracle sign-up (UK South, MFA, budget alert of 1), `oci setup config` in Terminal, API key uploaded | Fri 10-16 | waiting on "oracle ready" | program-director |
| BotFather token, Telegram id, Healthchecks check | Fri 10-16 | asked | program-director |
| First SSH to each box and host-key check; MT5 first login over VNC; secrets in the hidden prompts | Fri 10-16 | during the deploy | program-director |
| FRED_API_KEY repository secret | Sun 10-18 | asked 10-11; chased 10-16 | program-director |
| Batched decisions (section 6) | Sun 10-18 – Fri 10-30 | asked 10-16 on #34 | program-director |

## 5. Risk register (top 16)

| # | Risk | L | I | Owner agent | Mitigation |
| --- | --- | --- | --- | --- | --- |
| 1 | **No edge found:** every Q1 verdict negative | High | High | strategy-researcher | Rules written before data; trial reserve; stop rule; post-null levers go to the owner |
| 2 | **Cost underestimation:** swap, slippage or spread worse than modelled, so a gross edge turns negative | High | High | performance-analyst | costs-v1 from measured fills; H-13 overlay (W1-5); "fills within 30%" gate |
| 3 | **Owner availability:** owner steps or decisions late | Med | High | program-director | Claude-run deploys; one batched ask (section 6) with deadlines; every owner step chased on its day |
| 4 | **Wine/MT5 reliability:** terminal hangs, logs out or misses ticks under Wine | Med | High | sre | W1-1 smoke, 24 h health, W2-4 watchdog; fail closed (R25, X8); Azure Windows fallback (W2-9) |
| 5 | **Single-host failure:** brain VM down means no exits managed in the engine | Med | High | sre | Server-side SL/TP (X5); heartbeat and dead-man alerts; backups and the restore drill (W1-2); scripted rebuild |
| 6 | **Oracle capacity or reclamation:** A1 out of capacity, or idle VMs reclaimed | Med | Med | sre | E2.1.Micro brain fallback tonight; Pay As You Go raises A1 priority; keep VMs busy; scripted bootstrap |
| 7 | **CI minutes exhausted** before or during Q1 | Med | Med | release-manager | Self-hosted runner (W2-8) before January, after a security-reviewer GO (risk 14); one PR per batch |
| 8 | **Public repo security:** identifiers or secrets leak; workflow abuse | Low | High | security-reviewer | S4 scan on every push; httpx kept below INFO (token in journald fixed); keyring and local settings only |
| 9 | **Data-feed gaps:** Dukascopy, FRED or broker gaps or artefacts | Med | Med | data-engineer | Quarantine (D22); coverage triage (W1-6); reconciliation (W1-4); cross-feed survival (F10) |
| 10 | **Model drift** after promotion | Med | Med | quant-reviewer | PSI/ECE size-down and CUSUM halt (M25–M27); owner-only clear |
| 11 | **Review bottleneck:** trading-safety holds 4 items against a limit of 2; load near 100 | High | Med | program-director | Second trading-safety reviewer lane now; builders capped at 8; `-n 2` above load 24; one-pass reviews |
| 12 | **DST gap and market-closed clocks:** false FAILs or a wrong broker offset between 10-25 and 11-01 | Med | Med | sre | BACKLOG 59 before engine start; W1-9 daily clock check 26–30 Oct and on 11-02 |
| 13 | **Broker leverage 1:5000 behind a fixed bridge ceiling:** the bridge's only independent size check is 3.0 lots (`execution/bridge.py:104`, `:266`), ≈ 945× equity at the demo's 1:5000, so a sizing bug past the RiskGate could wipe the account on a ≈ $4 move (added 2026-10-16) | Med | High | architect | TD-16: equity-scaled ceiling in `GuardedBroker`, fail-closed, exits ungated; plan Fri 10-23, ADR 0027, trading-safety review; built Fri 11-06. Review Fri 10-23 |
| 14 | **Self-hosted CI runner exposes trading secrets:** on a public repo a fork PR could run code on the runner's host (CFO flag, added 2026-10-16) | Low | High | security-reviewer | No runner on a host holding trading secrets without a security-reviewer GO (W2-8; tech-debt "Standing constraint"). Review at W2-8's plan |
| 15 | **Delivery pipeline red for reasons other than defects:** interpreter drift, date-bound and timing tests, a stale integration-branch default (added 2026-10-30) | High | Med | cto | FR-1 to FR-6 in section 5a as one PR by Tue 11-03. Measured status 10-30: 7 of 7 red `main` runs since 10-18 non-deterministic; 12 of 33 failures were guards first run after the push. Review Fri 11-06 |
| 16 | **Deploy stall:** a fix on the money path (exit, engine down) waits for an owner approval of an exact command (added 2026-10-30) | Med | High | sre | `docs/process/deploy-approval-runbook.md`; owner decides the standing rule (FR-9). Review Fri 11-06 |

**Technical debt and platform register:** `docs/process/tech-debt.md` (cto) holds the ranked technical-debt items
TD-1 – TD-16, each with risk, cost, one accountable role and a date, the ADR numbers reserved for them (0023-0027),
and the CTO's standing technical rulings. It is re-dated every Friday with the debt review; the technical rows above
(4, 7, 13, 14) are kept consistent with it. Risk 4's pin is decided: ADR 0019 accepted 2026-10-16 (TD-1, sre, by Sat
10-24).

## 5a. Platform roadmap and delivery-friction register (cto, 2026-10-30)

2026-10-30 · cto (first cycle after the owner's replacement of the role) · base `660d2db2`. Re-measured and re-dated
every Friday in the weekly engineering read (issue #34). This section supersedes the delivery-tooling rows of
`docs/process/tech-debt.md` (TD-3, TD-4, TD-8 and the "interpreter choice" gap named there on 10-16 and still open).
Lane flow: ADR 0060 (proposed; decided by PD + PO + CTO by Mon 11-02). Deploy approvals:
`docs/process/deploy-approval-runbook.md`.

### Pipeline baseline (measured 2026-10-30 by the cto)

| Metric | Value | Source |
| --- | --- | --- |
| Merged PRs, last 15 (#200-#224, 10-26 to 10-30) | 3 per day; 5 of 15 have 2-3 commits | `gh pr list --state merged --limit 15` |
| PR open to merge | median 9 min; max 2,752 min (#215, held 46 h on calendar-test and allowlist failures) | same query |
| First commit to merge | median 0.9 h, p90 46 h. **Understates lead time**: lane commits are made late, so dispatch-to-merge is not measurable from git today (FR-8) | same query, `commits[].committedDate` |
| GitHub CI wall time (ci.yml, 200 runs 10-18 to 10-30) | median 6.8 min, p90 16.4 min; 173 success, 19 failure, 8 cancelled | `gh run list --workflow ci.yml --limit 200` |
| CI runs per PR (last 15) | 21 runs, 6 failed (#215 3 of 4, #219 1, #211 1, #206 1) | runs joined to PR head branches |
| Same-SHA pass and fail on GitHub | 0 of 200 SHAs (flakes surface as a new commit, not a re-run) | same query |
| CI failure issues | 32 `ci` issues 10-07 to 10-30 (plus 17 data-dukascopy on 10-01); 9 still open, 4 of them (#164-#167) for 10 days | `gh issue list --label ci --state all` |
| Failures on `main` | 7 issues, **all non-deterministic**: timing #100 #166, tmp `git clone` #167 #182, e2e #89 #223, unclassified #196 | issue bodies, `Branch:` line |
| Local full gate (`scripts/ci_local.sh`) | today 931 / 990 / 1,023 s (2.3-2.5x GitHub); 29 runs 10-19 to 10-26 median 621 s, p90 924 s | `~/tmp/ci/*.log`, `~/tmp/ci_local/*.log` "total N s" |
| Worktrees | 373 registered, 275 under `.claude/worktrees`; 268 of 275 have HEAD already in `main` | `git worktree list`, `merge-base --is-ancestor` |
| Worktree disk | 175,767,892 KB (168 GiB) by `du`; `.venv` ≈ 555 MB per tree (22.2 GB over a 40-tree sample). `du` counts APFS clones in full: measure the real gain with `df` before and after the first prune | `du -sk .claude/worktrees` |
| Worktree interpreters | 209 venvs on 3.11, **6 on 3.12** (numpy 2.5.3; one 2.5.4, which is not in `uv.lock`, so it was installed outside the lock), 60 with no venv | `.venv/pyvenv.cfg`, `site-packages` |
| Agent cost per merged PR | **not measured** (FR-10) | none yet |

**CI failure causes, 33 failures in 32 issues (10-07 to 10-30), top 5:**

| # | Class | Count | Issues | Deterministic? |
| --- | --- | --- | --- | --- |
| 1 | Guard test caught a lane's stale expectation after the push (golden, registry order, line cap, persistence allowlist, family registry, shard partition, feature lists, bridge or bootstrap requirements) | 12 | 72 80 86 95 117 122 123 132 137 164 216 217 | yes: catchable before the push |
| 2 | e2e: TOTP-step timeouts, layout shift under load, serial-story retry | 5 | 82 87 89 143 223 | no |
| 3 | Wall-clock latency or timing assertion on a shared runner (p95 ≤ 300 ms, decoy timing, 30 s reads) | 5 | 91 100 166 207 212 | no |
| 4 | Test coupled to the runner (git clone in tmp, gh token, shellcheck version) | 5 | 99 167 175 182 217 | no |
| 5 | Calendar date baked into a test | 2 | 218 220 | yes, from a date on |
| - | Unclassified (no failing test in the body) | 4 | 47 66 165 196 | - |

**Flaky list (owner test-engineer, quarantine or fix by Mon 11-02 per FR-3):**
`web/e2e/dashboard.spec.ts:499` layout shift (CLS 0.028 vs < 0.01 on GitHub main, run 41038754258, #223);
`dashboard.spec.ts:50` "first run creates the owner" on retry: **not flaky, deterministic**: the file is
`mode: "serial"` (`:45`), so a retry restarts the story against a server whose owner already exists (#143, #223);
`tests/test_trades_ledger.py::test_a_filtered_page_over_50k_rows_answers_within_300_ms_at_p95` (#207);
`tests/test_strategy_ranking.py::test_the_ranking_answers_over_50k_fills_within_300_ms_at_p95` (#212);
`tests/test_email_code.py::test_email_code_decoy_timing` (#166); `tests/test_goldbot_deploy_sh.py` tmp clone (#167,
#182, #217); `tests/test_mt5_terminal_state.py` 30 s reads (#91, #100).

### Delivery-friction register (ranked: money path and delivery speed first)

Cost of carrying is measured where possible; cost to fix is lane-days. All FR-1 to FR-6 tooling ships as **one PR**
(the debt lane of Wave 2, week 1): merged by **Tue 11-03**, status measured on the first lane PR after it, read on
**Fri 11-06**.

| # | Friction | Root cause (evidence) | Structural fix | Guard | Carrying | Fix | Owner | Date |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FR-1 | Worktree venvs on 3.12: false mypy errors (28 in 16 files, reported 10-30, not re-run by the cto), `ci_local` "No module named 'yaml'" | No `.python-version` in the repo; `requires-python = ">=3.11,<3.13"` (`pyproject.toml:5`) lets uv choose 3.12 (installed: `uv python list`); worktrees get ad-hoc venvs or none, and `ci_local.sh:36-40` reads the matrix with whatever `.venv` exists (`~/tmp/ci/ph2-1003.log`) | Commit `.python-version` = `3.11`; narrow `requires-python` to `>=3.11,<3.12` so the lock holds one numpy (every deploy path already pins `--python 3.11`: 8 of 8 hits in `goldbot/ops`, `scripts`); `scripts/worktree_env.sh` = `uv sync --frozen --extra dev` (+ `npm ci` only when `web/` is touched) | `scripts/check_env.py`, called first by `gates.sh`, `ci_local.sh`, `lane_test.sh`, `merge_smoke.sh`: exits 2 unless the interpreter is 3.11 and the venv matches `uv.lock`; a test asserts `.python-version` = setup-backend's `python-version` = mypy `python_version` | est. 1 lane-hour per affected lane; 6 venvs affected | 0.5 | implementer | Tue 11-03 |
| FR-2 | Calendar-dependent tests fail every PR once the date moves (#218, #220; fixed by `a9c6104e`) | No clock seam: 111 wall-clock reads in 49 production files (`grep -rnE "Timestamp.now\|datetime.now\|date.today\|utcnow" goldbot`), 68 test files read the clock, 0 use a freezing library | Tests: `time-machine` (dev-only, MIT; build-vs-buy in ADR 0060) with a conftest option that runs the whole suite shifted by N days; production: one clock seam, planned with the engine split (TD-14 `engine/clock.py`) | Every backend-unit CI shard runs at a date shift derived from the SHA (0-45 days, printed in the job summary, reproducible with `GOLDBOT_TEST_SHIFT_DAYS`), so a date-bound test fails on its own PR, not two days later on everyone's | 2 days of red PRs (10-28 to 10-30) | 1 | test-engineer (tests), architect (seam plan) | Tue 11-03; seam plan Fri 11-13 |
| FR-3 | Non-deterministic tests: 10 of 33 failures, 5 of 7 red `main` runs | Absolute wall-clock budgets on shared runners; e2e retry in a serial story is guaranteed to fail (`playwright.config.ts:26` `retries: 1` with `dashboard.spec.ts:45` serial) | Perf budgets move to a `perf` marker in their own job (`-n 0`, median of N, budget = measured CI p99 x 2) or become plan assertions (index used, rows scanned); `retries: 0` for serial projects; CLS measured after data load with reserved space | `@pytest.mark.quarantine(issue=, until=)` runs in a non-blocking job; a test fails the build once `until` (≤ 48 h) has passed; same for Playwright `test.fixme` annotations via a script check | 5 red `main` runs, each a stalled release | 1 | test-engineer | quarantine Mon 11-02; fixed Wed 11-04 |
| FR-4 | 373 worktrees, 168 GiB | `INTEGRATION_BRANCH` defaults to the dead `claude/gifted-goldberg-lcb7pl` (last commit 10-19) in `prune_worktrees.sh:17`, `lane_test.sh:14`, `merge_smoke.sh:15`, `release.sh:18` and the lane brief (`docs/process/lane-checklist.md:24`); the pruner keeps 311 of 348 trees for regenerable ignored files (`data/`, `state/`, `goldbot.egg-info/`, `web/dist/`) | One base helper (`origin/main`) for every script; the pruner treats build and test artefacts as regenerable and lists sizes; `release.sh` prunes the lane's own worktree after its merge | `scripts/new_lane.sh` (worktree from `origin/main` + `worktree_env.sh`) refuses to create a tree while more than 40 exist or free disk is under 100 GB, printing the prune command | Disk; lanes iterate against an 11-day-old base, so `lane_test.sh iter` runs far more than the touched tests | 0.5 | sre | Tue 11-03; first `--apply` sweep Mon 11-02 |
| FR-5 | One CI issue per failed run; 9 open, 4 stale 10 days | `report-failure` job writes one issue per run by SHA (`ci.yml` "one issue per failed run") | Key by failing test id: an open issue for the same test gets a comment (SHA, run, branch); PR-branch failures go to the PR as a comment, issues only for `main`; auto-close after 3 green `main` runs | Unit test of the dedup and close logic | Triage time; real failures hidden among stale ones | 0.5 | release-manager (implementer builds) | Tue 11-03; stale triage Mon 11-02 |
| FR-6 | Guard tests fail after the push (12 of 33) | Lanes iterate on touched tests only; the cross-cutting guards (goldens, registries, allowlists, caps, shard partition) are not touched, so they first run in CI | `lane_test.sh iter` always appends the `guard`-marked set (target ≤ 60 s) | A test fails if a known guard file lacks the `guard` marker | One CI round trip (≈ 7 min + rework) per occurrence | 0.5 | test-engineer | Tue 11-03 |
| FR-7 | Local full gate 15-17 min, 2.3-2.5x GitHub | Load from parallel lanes on one Mac (≤ 12 workers rule) | ADR 0060: one `ci_local.sh` per PR, run by `release.sh` alongside GitHub CI (GitHub is the gate; a local red stops auto-merge) | `release.sh` records both timings per PR | ≈ 15 min per extra local run | 0 (rule) | release-manager | Mon 11-02 |
| FR-8 | Many small PRs, a review round and CI wait each | No bundling rule; review rounds sequential per PR | ADR 0060: one PR per lane, one combined review round, fixes in one batch | `release.sh` refuses a PR under 50 changed lines unless labelled `hotfix` (money path or engine down) | per-PR fixed overhead (review, ≥ 7 min CI, local gate) | 0.5 | release-manager | Tue 11-03 |
| FR-9 | Production deploys stall on per-command approval | Auto-mode classifier blocks remote writes; no standing, exact rule | `docs/process/deploy-approval-runbook.md`; the owner decides on the standing rule | `release.sh` deploy step is the only deploy form agents use | Hours per deploy; an exit fix can wait on the owner | 0 | sre (runbook); owner (rule) | runbook 10-30; owner decision asked Mon 11-02 |
| FR-10 | Cost per merged PR unknown | Lane dispatch time and token use are not recorded | `release.sh` writes lane start (from `state/program.db`), push, merge, CI minutes; tokens summed from the session transcripts | Weekly read shows it or says why not | Cannot judge lane value | 0.5 | release-manager with cfo | Fri 11-06 |

**Debt budget.** Wave 2 week 1 spends its debt lane (one in eight) on FR-1 to FR-6 as one PR. Product lanes keep
their order; nothing here touches a safety rail or the money path's code.

## 6. Owner decision queue

**Decision forum** (owner delegation 2026-10-16): routine decisions are made by the program-director and the
product-owner, with the `cto` on technical matters and the `cfo` on anything with a money effect, recorded in
`docs/decisions/` or `docs/finance/`. Only owner-only matters come to this queue (spending, paid plans, sizing and
risk limits, instruments, going live, server deployment, gate thresholds, credentials); the CFO prepares the numbers
for each money item, the CTO the evidence for each technical one.

Asked once, batched, on #34 on 2026-10-16. Each item has a recommendation and a deadline; the finance ids are in
`docs/finance/recommendations-2026-10.md`.

**Delegated 2026-10-16 (owner rule): routine product, ops and deploy decisions are now made by the program director
with the product owner (PD+PO) and reported to the owner on #34, not asked.** Only truly owner-only matters stay in
this queue: money or paid plans; going live, `unlock_live` or phase-gate thresholds; loosening any risk limit; the
owner's own credentials; legal. Safety-touching choices take the conservative option. Rows that were routine moved
to "Decided by PD+PO" below.

| # | Decision | Recommendation | Deadline |
| --- | --- | --- | --- |
| OD-1 | Narrow allow rules for the one demo-only deploy entry point | Yes | Fri 10-16, before the deploy |
| OD-2 | Standing approval for demo deploys of a reviewed `main` SHA, and a daily agent budget | Yes for demo only (never live; result on #34 within 24 h); owner names the figure | Fri 10-16 |
| OD-3 | Pay As You Go on Oracle | Yes, with the budget alert and a free-shapes-only quota | Fri 10-16 if A1 is out of capacity, else Fri 10-23 |
| OD-5 | FRED_API_KEY repository secret | Set it | Sun 10-18 |
| OD-6 | `gates.max_gross_share` | 0.05 | Sun 10-18 |
| OD-10 | ~~Commit the uncommitted docs in the main checkout (finance, hosting options, speed-up review)~~ | **Done 10-16:** all three were committed in f7b0cdc (2026-10-11, `docs/finance/`, `docs/deploy/free-hosting-options.md`, `docs/process/speed-up-review.md`) and the main checkout is clean; no owner decision left | closed |
| OD-11 | GitHub "Keep my email private" and "Block command line pushes" | Turn both on; do not rewrite history | Sat 10-17, before the batch push |
| OD-12 | Finance D1 (agent API spend cap); and whether the Claude subscription counts as a running cost of goldbot (it changes break-even, `docs/finance/budget-2026-10.md`) | Cap: (b) about $10 a month until a strategy passes (CFO). Subscription: the owner rules; the CFO shows break-even both ways. D3 and D4 moved to "Decided by PD+PO" | Fri 10-23 |
| OD-13 | Finance D2 (hosting after the Azure trial), D5 (deposits) | D2 decide on the Wine soak's evidence; D5 no recommendation | D2 by 2027-08-13; D5 open |
| OD-14 | Gate thresholds sign-off and a low-frequency variant; demo cost-probe orders; automatic deploys | Brought separately with options | Mon 11-30 (before the freeze) |
| OD-15 | Paper-vs-backtest gate: also require the paper mean's lower 95% bound above 0 (a phase-gate threshold) | Yes: the quant-reviewer recommends it (review 2026-10-16); PD+PO concur | Mon 11-30, with OD-14, before paper starts |
| OD-16 | CFO C1: real money before the paper -> tiny-live gate. (a) none, £0 at risk; (b) a live deposit now, with no strategy and an expected value of at most £0 after costs | (a), the current rule (CFO; PD+PO concur) | standing; confirm with the batch on #34 |
| OD-17 | CFO C2: **tiny-live cannot trade at £1,000** (the 0.1% rail needs about £6,850 for a 0.01 lot). (a) fund the live account to about £6,850 at tiny-live start (£8.20 a trade, the £50 absolute limit binds, 8% stage £548, 12% halt £822); (b) keep £1,000 and the rail, so the system never goes live; (c) amend tiny-live for the minimum lot at £1,000 (0.82% a trade, worst day about £17, halt at £120): a **loosening**, needing an ADR and trading-safety review | No deposit now. (b) is the default and costs nothing; rule on (a) or (b) on paper evidence before the backtest -> paper gate is recorded. (c) is listed for completeness and **not recommended** (CFO) | before the backtest -> paper gate (earliest about Apr 2027); reviewed at the Q2 2027 freeze |
| OD-18 | CFO C3: deposits (ADR 0004 D5; supersedes OD-13's D5) | None to the demo (demo money is not real); live deposits sized by what the next gate needs (OD-17), never by the north star | at the paper gate |
| OD-19 | CFO C4: risk appetite | Confirm the existing rails (risk per trade, £50 absolute daily limit, 8%/12% stages, 6 x equity notional) as the appetite; nothing looser | Mon 11-30, with OD-14 |
| OD-20 | CFO C5: broker account leverage (read 1:5000 on 2026-10-16, BACKLOG 27). (a) keep 1:5000; (b) the lowest IC Markets offers at or above 1:20 | (b) on the live account at opening, and on the demo if the client area allows. It only tightens: the broker would cap an accidental position at about 0.06-0.09 lots instead of about 16. Your action in the broker's client area (own credentials); BACKLOG 66 adds the bridge-side ceiling regardless | demo: at the next client-area login; live: at account opening |

### Decided by PD+PO (delegated 2026-10-16), reported to the owner

| # | Decision | Decided | Why it is routine |
| --- | --- | --- | --- |
| OD-4 | Finance D6: the demo is a Raw Spread account | Raw is the working assumption; the SRE reads the account type and commission from the terminal at the MT5 login and records it on #34. If it reads Standard, budget and break-even are re-run before any 15m trade, and opening a Raw account goes back to the owner (own credentials) | Confirming a fact about the demo; no money, no limit |
| OD-7 | `risk.equity_check_tolerance` | Keep 0.02 (ADR 0016), the value in force; any increase would delay the kill switch and stays with the owner | Keeps the current, conservative value; loosens nothing |
| OD-8 | Ledger `balance_tolerance` 0.05 plus 0.01 per deal | Approved as in force (ADR 0015) | An audit reconciliation tolerance; no money, no risk limit |
| OD-9 | `telegram.owner_tz` | Europe/London, the value in force (UK clocks change 10-25) | Display time zone |
| OD-12 D3 | Dashboard access | (a) Tailscale-only, £0, no public hostname | Free, and the smaller attack surface is the conservative choice |
| OD-12 D4 | Track demo cover as well as real cover | (b) yes, reported monthly by the finance-auditor | Reporting only |

Each row: Decided 2026-10-16 by program director + product owner (owner delegated).
