# goldbot program roadmap

2026-11-23 · program director · base `2baf9528` (main after #424). Re-planned after the 2026-11-20 team audit
(finding TEAM-SCORECARD: no scorecard since 10-16, this file stale since 11-02). The October plan (M1-M6 by
engineering phase, Waves 1-2) is retired: its engineering milestones shipped, and its research milestones assumed an
edge that has not appeared. Every milestone below is tied to **net £ or to a first survivor**; nothing counts trades,
jobs or PRs as progress (owner directive). Sources: config/findings.yaml, the GD-1 card and amendment
(`docs/research/prereg-2026-11-20-gold-daily.md`, `prereg-amendment-2026-11-20-gd1-asia-learning.md`), the discovery
K-cap rule (`docs/research/discovery-k-cap-2026-11-18.md`), ADR 0088, today's scorecard (`docs/process/scorecards/2026-11-23.md`).
Re-planned at the start of every session and the day any date slips. One accountable role per row.

**What is not guaranteed.** No strategy is net-profitable. Demo is -£866.39 cumulative, and no arm beats its random
twin. Every survivor milestone may end in a kill; a kill is a result, not a slip. Rails are unchanged: demo only,
RiskGate the only path to an order, no limit loosened, live needs the owner's `unlock_live` and phrase.

## 1. Milestones

| # | Milestone | Accountable | Date | Exit criteria (money or survivor) |
| --- | --- | --- | --- | --- |
| N0 | **Stop paying for no evidence** | research-director | done 2026-11-22 (#424) | Only arms with evidence place orders; random controls replay-only; red-box arms retired. Check: next weekly book shows no control fills |
| N1 | **Nothing depends on the laptop** | sre | VM created by owner Tue 2026-11-24; cycle, deploys, registry publish moved by Thu 11-26 | 4 h cycle runs 48 h on the ops VM with the laptop off; no cycle gap > 4 h after 11-26 (21-23 Nov stall not repeated) |
| N2 | **GD-1 A1 cost gate** (first survivor's first read) | research-director (evaluator reads) | 40 fills, earliest ≈ Wed 2026-12-23 | Mean realised round trip <= 1.1 bp, else kill the arm that day |
| N3 | **EVIDENCE-RB-HISTORY** red-box 1h/4h history replay | research-director | prereg by Fri 11-27; read by Fri **2026-12-04** | Pre-registered replay R against its random ladder; pass → a power-checked demo card; fail → closed for good |
| N4 | **Discovery Q4 read** | research-director (quant rules) | **2026-12-31** | Written read under the K-cap continuation rule; ≥ 1 power-admitted survivor or the nightly search is cut back. ADR 0088 Accepted before any discovered spec registers |
| N5 | **Demo book stops bleeding** | performance-analyst | weekly from Fri 11-27; target by Fri 2026-12-18 (A1-NET-LOSS) | Weekly net £ loss limited to the GD-1 arm's own result; no arm < -0.25 R mean over 30+ trades in 14 days |
| N6 | **GD-1 loss gate** | research-director | 120 fills, earliest ≈ 2027-02-19 | Mean net > -4 bp, else kill |
| N7 | **GD-1 continue gate → first real candidate** | research-director, evaluator | 250 fills, earliest ≈ 2027-05-18 | Mean net >= +3.5 bp and above its random twin; then and only then a paper-gate card (design gates, holdout once) |
| N8 | **KoG weekly red-box shadow review** | research-director | weekly Friday reads; review **2027-05-23** | Zero-order forward shadow; edge over its random ladder with power admitted, or closed. Never orders before that |
| N9 | Paper → tiny-live gate | program-director | not before N7 passes plus the design's paper period | Design gates unchanged; owner records the gate, `unlock_live`, typed phrase |

## 2. Critical path

```
#424 (evidence-only orders) -> GD-1 first entry Tue 11-24 00:00 UTC -> 40 fills (N2, ~12-23) -> 120 (N6) -> 250 (N7) -> paper card
owner creates ops VM (11-24) -> Claude login + gh auth there -> cycle/deploys/registry on VM (N1, 11-26) -> no stalls
RB-history prereg (11-27) -> replay read (N3, 12-04) -> pass: power-checked card | fail: closed
ADR 0088 Accepted (quant + trading-safety, by 12-04) -> discovery may register -> Q4 read (N4, 12-31)
```

GD-1 is the only line that can produce money evidence before 2027; N1 protects it (a laptop stall missed nothing yet,
but would miss GD-1 reads and kill checks). Everything else feeds a second survivor.

## 3. Refused: not on the critical path

Not started, and stopped if running, until a milestone above needs it:
- More strategy families or arms on demo without a pre-registered, power-checked card (Q4 budget 3,416 / 3,500).
- Retrying closed families: gold intraday, learner H-96, exits H-95/L2, trend pool, valmom, xmom, carry, seasonals,
  MIM-1, red-box week. A return needs a new card with new data, not a re-read.
- Turning any model back on before one beats no-model out of sample.
- Volume or trade-count targets of any kind; broker-filled random controls.
- Dashboard, UI and Telegram features with no money or survivor effect.
- New instruments, new brokers, new data feeds without a consumer on a card.
- Raising the discovery K cap above 15,000 before the 12-31 read.
- Any loosening of RiskGate, limits or the demo-only phase.

## 4. Owner steps and cadence

| Step | Due | Status | Chaser |
| --- | --- | --- | --- |
| Create the ops VM (`~/tmp/create_ops_vm.sh`), then Claude login and `gh auth login` on it | Tue 2026-11-24 | asked 11-23; only these interactive logins stay with the owner | program-director |

**Cadence (re-armed 2026-11-23).** The 4-hourly autonomous cycle (`~/bin/goldbot-cycle-prompt.md`, moving to the
ops VM with N1) writes `docs/process/scorecards/<YYYY-MM-DD>.md` on its first run after 00:00 UTC each day, in this file's
format, as an independent read-only grading; roles at C or below get their role-file edit routed that day. Weekly
roll-up on #34 on Fridays with the GD-1 and KoG reads. The findings register re-measures every row daily.

## 5. Risk register

| # | Risk | L | I | Owner agent | Mitigation |
| --- | --- | --- | --- | --- | --- |
| 1 | **No edge:** GD-1 killed and discovery finds nothing by 12-31 | High | High | research-director | Kill rules written first; RB-history and discovery as second lines; honest close, no re-reads |
| 2 | **Laptop dependency:** cycle, deploys, registry stall when the Mac sleeps (21-23 Nov) | High | High | sre | N1 ops VM by 11-26; until then each session checks the last cycle time first |
| 3 | **Load-time and deploy bugs cost survivor days** (GD-1 missed Mon 11-23) | Med | High | implementer | ENG-LEARNING-SINCE fix by 12-04; deploys outside market hours unless a safety fix (SRE-DEPLOY-HOURS) |
| 4 | **Costs erase a small edge** (GD-1 cost reads 0.1-0.3 bp low) | Med | High | microstructure-researcher | Cost kill at 40 fills; realised cost per fill; exit-leg slippage added to the read |
| 5 | **Trial budget exhausted** (84 of Q4 left) | High | Med | research-director | Only power-admitted cards; Q1 budget set at the 12-31 read |
| 6 | **Order-path ADRs unaccepted** (0016, 0031, 0032, 0052, 0056, 0075, 0088) | Med | High | cto | Accept or retire by 12-04; AST guard on every order path |
| 7 | **Reporting drift:** activity reported as progress; scorecard lapses again | Med | Med | program-director | Daily scorecard in the cycle; findings register flags past-due highs on #34 |
| 8 | **Public repo leak** of identifiers or real-money figures | Low | High | security-reviewer | SEC-1/SEC-2 by 11-27; pre-commit guard; findings file refuses money figures |
| 9 | **Single-host failure** (brain or bridge down, exits unmanaged) | Med | High | sre | Server-side stops; dead-man alerts; rebuild drill |

Sections 5a and 6 below are kept as the dated record of 2026-10-30 and 2026-10-16. The owner's later directives
(no owner decisions; experts decide; owner out of the loop) supersede section 6's queue: open rows there are decided
by PD+PO with the CTO or CFO, except live, real money and loosening a limit, which stay with the owner.

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
| FR-11 | Unused feeds, jobs and screens are carried as if live (added 2026-11-02, ADR 0066) | Features were added without a consumer or a retirement rule. The TradingView chart-data path (pull, webhook, CSV import, features, `tv_event` specialist): no live reader; it duplicates the broker's real-time prices. News: 4,764 polls in 30 days, all `scored: 0` (no key), so the shock blackout never fired. `agents_*`: skipped on every run. Ten population-loop jobs feed no live strategy | Phase 1 (ADR 0066): remove the whole TradingView chart-data path (the author-ideas feed stays), the `agents_*` jobs, the fxstreet feed, and the Research and Feeds screens, as one cleanup release. Phase 2 (BACKLOG 142): retire the population loop, decide the news shock blackout, prune offline research code | A feed, job or screen that has had no consumer for 30 days is listed in the weekly engineering read, with a keep or retire decision | Services, health rows, heartbeat lists, deploy health window, tests and dashboard space; a rail that looks active but is not (D16) | 1 (phase 1) + 2 (phase 2) | cto (register); architect, then implementer (phase 2) | phase 1 Mon 11-02; phase 2 Mon 11-09 |

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
