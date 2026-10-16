# Platform review and technical-debt register

2026-10-16 · cto · base `9f04599` (`claude/gifted-goldberg-lcb7pl`). Re-read and re-dated weekly (Fridays) with the
debt and quality review; next review Fri 10-23. Evidence is file:line at the base commit, a run id, an issue or a
measured number. Nothing here changes a safety rail.

## Verdict

The platform is sound on the money path: fail-closed fallbacks exist for every cross-process field checked, the bridge
protocol is versioned and tested, and persisted reads are tolerant with skew reported to health. What slows us down is
**structure and test tooling**, not correctness: one 3,438-line engine file takes a third of all engine-path commits,
the e2e suite spends ~95% of its dashboard time waiting for TOTP clock steps, and three process gaps (ADR numbers,
worktrees, interpreter choice) cost lane time every day. Two items are time-critical: the Wine pin (bridge stops on an
unattended upgrade) and extracting the engine's persistence boundary before W2-1 starts building on Mon 11-02.

## 1. State of the platform (measured)

| Area | Measured | Source |
| --- | --- | --- |
| Engine hotspot | `goldbot/engine/runner.py` 3,438 lines, one `Engine` class with 141 methods (306-3438); 99 non-merge commits since 10-01, twice the next file (`ops/health.py` 51, `config.py` 48, `ops/run.py` 48) | `wc -l`; `git log --since=2026-10-01 --name-only` |
| Other large modules | `research/owner_history.py` 1,464, `research/director.py` 1,447, `ops/jobs.py` 1,211 (40 commits), `ops/perf_report.py` 1,110, `api/auth.py` 1,107 | `wc -l` |
| Unit suite | 5,203 passed in 104-181 s locally (gate logs 16 Oct, load-dependent); CI `backend` job 9 m 34 s (run 38956784066) | `~/tmp/gates/unit.log`, `a39-unit.log` |
| Integration suite | 80 passed in 295-385 s: slower than the 5,203 unit tests | `~/tmp/gates/integration.log`, `lane61-integ.log` |
| e2e | 19 tests, `workers: 1` (`web/playwright.config.ts:15`); dashboard file 5.7 min; CI `frontend` job 8 m 43 s | issue #82 (run 38956784066) |
| e2e time sink | Dashboard tests 3-8, 10, 13 took 28.9 / 60.0 / 60.0 / 29.8 / 30.0 / 29.7 / 58.3 / 34.1 s = ~331 s of 342 s; durations cluster on the 30 s TOTP step because `freshTotp` sleeps until an unused step (`web/e2e/totp.ts:19-30`) | issue #82 |
| Flaky e2e | `auth.spec.ts:184` (email sign-in link) timed out at 90 s on `auth.spec.ts:220`, waiting for the "Authenticator code" field after `await freshTotp(ownerSecret)`; 2 tests did not run | issue #82, PR #81 |
| Local gates | `scripts/gates.sh --web` runs lint, typecheck, vitest and build but **not e2e** (`scripts/gates.sh:54-61`), so e2e failures first appear in CI | file |
| Test tooling | `pytest-testmon` used by `scripts/lane_test.sh:2-6` (lane-test-rules) is not in `pyproject.toml` or `uv.lock`; no `pytest-timeout`, so a hung test runs to the 40 min job limit (`ci.yml:34`) | `grep -c testmon pyproject.toml uv.lock` = 0 |
| Worktrees | 60 registered (incl. the main checkout), 54 directories, 28 GB under `.claude/worktrees`; 54 sit on branches already merged into the integration branch; 10 locked; 172 local branches | `git worktree list --porcelain`, `git branch --merged`, `du -sh` |
| ADRs | 22 records; 0019 and 0021 `proposed`; tonight 0022 was first committed as 0021 and renamed (`docs/decisions/0022-results-verdict-rule.md:6-8`) | files |
| Cross-release safety | Bridge: `API_VERSION = 5` with `MIN_COMPATIBLE` (`execution/bridge.py:81-93`), `tests/test_bridge_compat.py`, commit check (`ops/checks/bridge.py:37-56`). State: tolerant reads + `schema_skew` in health (`base.py:125-152`), `tests/test_persistence_compat.py`. **No test runs release N-1 against release N** | files |
| State store | `goldbot/db` steps 0-3 done (aux.db v5); core.db holds HWM only; orders, risk state, approvals still JSON (W2-1 plan `docs/proposals/2026-10-w2-1-state-store-plan.md`); no `state_db` health check, no `run.py migrate` | proposal header |
| MT5 box | `winehq-devel` installed unpinned (`ops/linux/mt5_bootstrap.sh:71`); `mt5setup.exe` fetched unpinned and unhashed (`:127`; a rolling installer, so no hash is possible), and the terminal self-updates | file; ADR 0019 |

## 2. Ranking method

**Risk** = likelihood × impact (1-5 each; impact 5 = money path or exits, 3 = delivery or merges blocked, 2 = lane
time). **Cost** = 1 (≤ half a lane-day), 2 (≤ 2 days), 3 (≤ 1 week), 4 (≤ 2 weeks), 5 (> 2 weeks). **Priority** =
risk × (6 − cost), so a cheap fix to a real risk ranks first. Ties go to the item on the money path.

## 3. Ranked register

| Rank | Id | Item | Risk (L×I) | Cost | Priority | Accountable | Date | ADR |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | TD-1 | Wine (and MT5 build) unpinned on the MT5 box | 15 (3×5) | 1 | **75** | sre | decision Wed 10-21; held at the next owner-approved MT5-box command, by Sat 10-24 (market closed) | ADR 0019 (decide) |
| 2 | TD-2 | Extract the engine's persistence boundary from `runner.py` before W2-1 (split step 1) | 16 (4×4) | 2 | **64** | implementer (seam map: architect, with the W2-1 plan Wed 10-21) | merged on the integration branch Fri 10-30 | yes: 0023 runner split |
| 3 | TD-3 | e2e `PYTHON` default can run the server on the wrong interpreter and the wrong tree | 12 (4×3) | 1 | **60** | implementer | Tue 10-20 (after R-AUTH merges; same files) | no |
| 4 | TD-4 | Flaky auth e2e: email sign-in timeout | 12 (4×3) | 2 | **48** | test-engineer | Tue 10-20 | no |
| 5 | TD-5 | Release-compatibility test and deploy-together guard (card_created, entry_blocks) | 15 (3×5) | 3 | **45** | test-engineer (sre: health and deploy parts) | ADR Fri 10-23; test Fri 11-06, before W2-1 merges | yes: 0024 release compatibility |
| 6 | TD-6 | e2e speed: TOTP step waits and one worker | 10 (5×2) | 2 | **40** | test-engineer | Fri 10-23 | no |
| 7 | TD-7 | ADR number collisions: reservation in `scripts/docs_assemble.py` | 8 (4×2) | 2 | **32** | implementer | interim rule today; command Fri 10-23 | yes: 0025 amends 0012 |
| 8 | TD-8 | Worktree and branch pile-up (60 / 172, 28 GB) | 6 (3×2) | 1 | **30** | release-manager | first sweep Mon 10-19; removal at merge from today; script Fri 10-30 | no |
| 9 | TD-9 | Test tooling: undeclared `pytest-testmon`, no per-test timeout | 6 (3×2) | 1 | **30** | test-engineer | Fri 10-23 | no |
| 10 | TD-10 | e2e not in local gates | 6 (3×2) | 1 | **30** | test-engineer | Fri 10-30 (after TD-6) | no |
| 11 | TD-11 | Unit and integration suite speed | 10 (5×2) | 3 | **30** | test-engineer | durations measured Wed 10-21; fixes Fri 11-06 | no |
| 12 | TD-12 | State store: W2-1 cutover and its rollback story | 10 (2×5) | 4 | **20** | implementer (plan: architect) | rollback ADR Wed 10-28; build Wave 2, 11-02 – 11-20 | yes: 0026 core.db cutover and rollback |
| 13 | TD-13 | State store leftovers: step 3 remainder, steps 4-5 and 8, `state_db` health, `run.py migrate` | 9 (3×3) | 3 | **27** → ranked after TD-12, which it depends on | implementer | plan Fri 11-20; JSON exports retired (step 8) Fri 12-11 | covered by 0026 |
| 14 | TD-14 | `runner.py` split steps 2-7 (after W2-1) | 16 (4×4) | 5 | **16** | implementer (plan: architect, Fri 11-13) | one seam per PR, Mon 11-23 – Fri 12-11, before the 12-14 freeze | covered by 0023 |
| 15 | TD-15 | Watch list: `owner_history.py`, `director.py`, `jobs.py`, `perf_report.py` above 1,100 lines | 4 (2×2) | 3 | **12** | architect | review Fri 11-20 | no |

TD-13 scores 27 but cannot start before TD-12's tables exist; it is ranked by dependency, not score.

## 4. Items

### TD-1 Wine and MT5 version pinning (ADR 0019, pending)
Wine 10.3-11.5 stopped the terminal outright on 16 Oct (`docs/incidents/2026-10-16-mt5-debugger-found-under-wine.md`).
Today any `apt-get upgrade` can repeat that with the next devel release; the bridge stops, the engine loses its
exits-in-engine and only broker-side stops protect open trades (risk register #4). **CTO recommendation: ADR 0019
option 2** (`apt-mark hold` the installed `winehq-devel`, bump on purpose at the weekend with `bridge-smoke` after),
plus: Wine version and MT5 terminal build in the bridge `/health`, a health WARN when either changes and a FAIL in a
known-bad Wine range; unattended-upgrades confirmed off for `winehq-*`. Option 3 (`winehq-stable` ≥ 11.6) when it
exists. **Who decides:** holding a package version is an ops engineering choice, not one of the owner-only matters
(money, live, limits, credentials, legal). I recommend PD+PO+CTO decide it under the 16 Oct delegation by Wed 10-21
and report it on #34, unless the owner wants it back; running the command on the box stays an owner-approved deploy
step. If the owner keeps the decision, the program-director chases it on #34 for Wed 10-21. The MT5 terminal
self-updates and `mt5setup.exe` cannot be hashed (rolling installer): the mitigation there is detection (build in
`/health`), not pinning.

### TD-2 / TD-14 `runner.py` split strategy, sequenced against W2-1
**Problem.** `Engine` is one class, 306-3438, and the money path's every concern lives in it: tick loop, decisions,
execution, exits, reconciliation, equity and kill switch, clock and data quality, sizing, windows, persistence. 99
commits in 16 days; Wave 3 (A2, D10, M9, M3) and W2-1 all edit it, so lanes queue behind one another and merges of
money-path code need hand resolution.

**Options for sequencing against W2-1** (orders, risk state and approvals into `core.db`):

| Option | For | Against |
| --- | --- | --- |
| A. Full split first, then W2-1 | W2-1 lands in clean modules | Delays W2-1 by ~2 weeks; large money-path churn during the engine's first two weeks and the DST-gap week |
| B. W2-1 first in `runner.py`, split later | No wait | W2-1 adds lines to the hotspot and conflicts with every other lane touching it in Wave 2; the split then has to move fresh code |
| **C. Extract only the persistence boundary first (move-only), W2-1 swaps the implementation behind it, the rest after W2-1** | W2-1 edits one new module, not `runner.py`; the first step is small and reviewable; the remaining seams land between Wave 2 and Wave 3 | Two review rounds on the engine instead of one |

**Ruling: option C.** Rules for every split step:
1. **Move-only PRs, one seam each.** No behaviour change, no test edits other than imports; the reviewer checks with
   `git diff --color-moved=dimmed-zebra` and the full gates. trading-safety-reviewer reviews each (engine path).
2. **Composition, not mixins.** Each seam becomes a collaborator with its own state and a narrow interface; `Engine`
   keeps its public methods (`on_tick`, `on_bar_close`, `warm_start`, `no_quote_pass`, `set_*`, `flush_*`,
   `note_tick_error`, `clock_skew_s`) as the facade, so `ops/run.py` and the tests do not change. A seam that reads too
   much of `Engine` to extract cleanly is stopped and recorded, not forced through a mixin.
3. **Ratchet.** A test caps `runner.py`'s line count at its post-step size, lowered at each step (target ≤ 1,200
   after step 7), so the hotspot cannot regrow.
4. **File ownership.** After each step the ROADMAP "Shared files" note lists the new module and its owning lane.
5. **No split step deploys during the DST-gap week (10-26 – 10-30)**; merging on the integration branch is fine.

**Seams, in order** (line ranges at `9f04599`):

| Step | Module | Methods | When |
| --- | --- | --- | --- |
| 1 | `engine/store.py`: `EngineStore` interface, JSON implementation | `_orders_path`, `_orders_record`, `_save_orders`, `_load_orders` (2411-2471); `_risk_path`, `_save_risk_state`, `_save_risk_state_now`, `_load_risk_state` (2846-2900); the file write in `_write_state` (`runner.py:3434`). `_write_state` keeps building the payload | TD-2, by Fri 10-30; W2-1 then adds the `core.db` implementation behind `EngineStore` |
| 2 | `engine/exits.py` | `_manage_open` … `_blackout_rescore` (1457-1765) | after W2-1 merges |
| 3 | `engine/reconcile.py` | `_reconcile`, `_adopt`, `_agent_for_magic` (1768-1858); `_resolve_sent` … `_closed_excursion` (2472-2846) | Wave 3 start |
| 4 | `engine/account_guard.py` | `_refresh_account` … `_reset_owner_mode` (1909-2208); `_roll_risk_period`, `_poll_rearm`, `_rearm_conditions` (2353-2411) | |
| 5 | `engine/clock.py` | `_host_now`, `no_quote_pass`, `_host_clock_exits` (692-766); `_server_clock` … `_check_new_bars` (2208-2353) | |
| 6 | `engine/sizing.py` | `_account_currency` … `_published_limits` (961-1123); `_refresh_broker_terms` … `_throttle_state` (3197-3368) | |
| 7 | `engine/windows.py` | `_blackout` … `_windows_state` (2926-3078) | by Fri 12-11 |

What stays in `runner.py`: the tick loop, bar close, frame building, `_decide` / `_on_decision` / `_execute`
orchestration and shadow stepping, roughly 1,000-1,200 lines. **ADR 0023** records the seams and rules; the
architect's seam map is due with the W2-1 plan on Wed 10-21 (an addition to lane A-PLAN, read-only).
**Reverse if** step 1 shows the persistence methods share more `Engine` state than a narrow interface can carry: then
W2-1 goes first in place (option B) and the split moves after it.

### TD-3 e2e `PYTHON` default (`web/playwright.config.ts:5`)
`const PYTHON = process.env.PYTHON ?? "python"` starts `scripts/e2e_server.py` with whichever `python` is first on
PATH. CI sets `PYTHON: python` after `setup-python` (`ci.yml:113`), so CI is right; locally `python` is often missing,
the system one, or the main checkout's venv, whose editable install imports `goldbot` from the main checkout, not the
worktree (the hazard `docs/AGENT_STANDARDS.md` "Parallel work rules" warns about). The e2e run then tests a
different tree and can pass on code the lane did not write. **Fix:** default to the repo venv's interpreter
(`../.venv/bin/python`, then `python3`), and have `e2e_server.py` refuse to start unless `goldbot.__file__` is under
its own repo root, printing both paths. Same files as lane R-AUTH: added to R-AUTH if it has not merged, else the debt
lane right after.

### TD-4 Flaky auth e2e: email sign-in link (issue #82, PR #81)
The test failed at `auth.spec.ts:220` after `await freshTotp(ownerSecret)`. `freshTotp` (`web/e2e/totp.ts:19-30`)
sleeps until the clock reaches an unused step once the current and next are spent; the test uses the owner's secret
several times, so up to ~60 s of its 90 s budget can go to sleeping before the field is filled. That is a likely
cause, **not yet proven**: the field may also not render on the spent-link page. **Fix order:** read the CI trace
(`trace.zip` in the run) to tell the two apart; then remove the dependency on wall-clock steps (TD-6). Rule for all
flakes (to AGENT_STANDARDS via the product-owner): a flaky test gets a `ci` issue the same day, a root cause within
two working days, and is never "fixed" by raising a timeout or adding retries alone; CI's `retries: 1`
(`playwright.config.ts:16`) stays a safety net, and any test that needed the retry is reported in the job summary.

### TD-5 Release compatibility and the deploy-together constraints
**The couplings introduced tonight** are inside the brain host but across processes:
- `BusDecision.card_created` (`telegram/bus.py:55`) is written by the deciding process (API or Telegram,
  `bus.py:171-173`) and read by the engine (`telegram/approvals.py:243`); with it missing the engine falls back to
  `d.ts < p.created`.
- `entry_blocks` is written by the engine (`engine/runner.py:3428`) and read by the API
  (`api/entry_status.py:533`); with it missing the API shows `entry_reasons_missing`, never OPEN.

Both fallbacks are fail-closed, and one `goldbot-deploy` restarts every brain service from one tree
(`ops/linux/goldbot-deploy.sh:419-420`). They break on: a deploy that fails half-way (the loop returns 1 after some
services restarted), a rollback past the commit (the rollback floor, `goldbot-deploy.sh:36-49`, is set by hand), or any
future move of the API off the brain host. Today nothing tests release N-1 against release N; the existing
compat tests (`test_persistence_compat.py`, `test_bridge_compat.py`) check one release's readers against shapes
written into the test.

**Recommendation (ADR 0024):**
1. **Cross-release contract test.** Each release's writers produce golden samples of every cross-process record
   (engine state, pending and decided approvals, control, orders, risk state; `core.db` rows once W2-1 lands) into
   `tests/compat/goldens/<schema>/`. A CI job checks out the last deployed commit (the `deployed` tag the deploy
   records, else the rollback floor) in a temp worktree and runs (a) its readers on the current goldens and (b) the
   current readers on its goldens, asserting the documented fallback for each missing field (for example
   `card_created=None` takes the timestamp rule; missing `entry_blocks` gives `entry_reasons_missing`).
2. **Deploy-together manifest.** `config/release_compat.yaml` lists pairs that must deploy together with the first
   commit of each; `goldbot-deploy` raises the rollback floor to the newest such commit automatically.
3. **Release skew in health.** Every brain service publishes its commit in its heartbeat; a `release_skew` check
   FAILs when two brain services report different commits for more than 5 minutes (the bridge check already does
   this across hosts, `ops/checks/bridge.py:37-56`).
Accountable: test-engineer for 1, sre for 2 and 3 (one lane, reviewed by trading-safety and sre). It must exist before
W2-1 merges, because W2-1 moves orders and approvals into a new store: exactly the change it exists to catch.

### TD-6 e2e speed
~331 s of the dashboard file's 342 s are TOTP step waits (section 1). **Fix:** give each test that needs codes its own
user and TOTP secret (a fresh secret has no spent steps), or seed users per test through `e2e_server.py` fixtures. Do
**not** add a clock override or replay-guard bypass to `goldbot/api/auth.py`: production auth stays untouched. Then
run the two projects (dashboard, auth) on separate servers in parallel (`workers: 2`). Target: e2e ≤ 2 min in CI,
frontend job ≤ 5 min. Measured against issue #82's 7.5 min.

### TD-7 ADR number reservation
Two lanes picked 0021 tonight; 0022 records the rename (`0022-results-verdict-rule.md:6-8`). Renumbering after the
fact is costly because ADR numbers are cited in code comments and docs. **Interim rule, effective today:** a lane
writes an ADR only under the number its brief names; the program-director assigns numbers from this register
(0023-0026 are taken below). **Durable fix (ADR 0025, amending 0012):** `scripts/docs_assemble.py new-adr --slug S`
reserves the next number under a lock in the repository's common git directory (`git rev-parse --git-common-dir`,
shared by every worktree on the machine; nothing machine-specific is committed), writes
`docs/decisions/NNNN-S.md` from a template with `Status: reserved (<role>, <date>)`, and `check` (the pre-commit
hook) fails on two ADR files with one number or a citation of an ADR number that does not exist. Cloud sandboxes,
which do not share the git directory, take their number from the brief.

### TD-8 Worktrees and branches
60 worktrees, 54 on merged branches, 28 GB, 172 branches. Costs: disk, lanes starting from stale bases, a shared stash
stack across 60 trees, and confusion about which tree is live. **Policy (effective today):** the release-manager
removes a lane's worktree and local branch when its work is merged (F4 "stop when merged"); locked worktrees are
left to their agent. **First sweep Mon 10-19,** after the batch PR and the engine start, never while lanes run on
them. A script (`scripts/worktree_gc.sh`, dry run by default, lists merged and unlocked trees older than 24 h) by
Fri 10-30; implementer builds it, release-manager owns it.

### TD-9 Test tooling
Declare `pytest-testmon` and `pytest-timeout` in the `dev` extra and the lock; set a default per-test timeout (60 s
unit, 300 s integration) so a hang fails one test, not the 40-minute job.

### TD-10 e2e in local gates
Add `scripts/gates.sh --e2e` (implied by `--web` once TD-6 makes e2e ≤ 2 min), so e2e fails on the lane, not on CI
minutes. CI minutes ran out on 10-06 (ROADMAP risk 7).

### TD-11 Unit and integration speed
Unit: 104-181 s locally for 5,203 tests, CI backend job 9.5 min. Integration: 80 tests in 295-385 s, which says a few
tests dominate. **First measure:** `pytest --durations=30` on both suites (test-engineer, Wed 10-21, at `-n 2` on a
quiet tree), then fix the top offenders (shared fixtures, fake clocks instead of sleeps, smaller frames). Targets:
unit ≤ 90 s at `-n 4`, integration ≤ 120 s, CI backend job ≤ 6 min. No test is deleted or skipped to meet a target.

### TD-12 / TD-13 JSON-to-SQLite migration (W2-1 and what follows)
**Note on naming:** ROADMAP W2-2 is `order_calc_margin` and tick-flags dedup, not state; the state work after W2-1 is
the proposal's steps 5 and 8. I list it as TD-13 and recommend the program-director gives it a Wave 3 lane id.
- **W2-1** (proposal steps 6-7) is planned (`docs/proposals/2026-10-w2-1-state-store-plan.md`): order row and open
  trades in one `BEGIN IMMEDIATE` transaction, decisions keyed by proposal id so the first wins, JSON exported after
  each commit for one release, a corrupt `core.db` keeps today's refusal to start. **What is missing is a recorded
  rollback story**: S12 requires one, reviewed by trading-safety, for any new store holding client ids or broker
  orders. ADR 0026 (architect drafts, CTO decides, trading-safety reviews) by Wed 10-28 covers: rollback to a
  JSON-only release (the exports make it safe for one release; after step 8 the rollback floor must move past W2-1),
  the import of JSON on first start, and how the TD-5 test proves both directions.
- **TD-13** leftovers: step 3 remainder (`news_spend.json`, hypotheses, health dedupe, news feeds, recalibrations),
  step 4 (not started), step 5 (supervisor halt inputs, out of W2-1), step 8 (retire JSON exports and the seven
  readers listed in the plan), plus a `state_db` health check and `run.py migrate`. The JSON exports are debt with a
  date: retired by Fri 12-11, one release after W2-1 deploys, before the freeze.

### TD-15 Watch list
Four modules above 1,100 lines. None is a hotspot yet (`ops/jobs.py` 40 commits since 10-01 is the nearest). Reviewed
Fri 11-20; split only if churn or merge conflicts show it.

## 5. Debt budget

Default one lane in eight. With at most 8 builder lanes, that is one standing debt lane (implementer) plus the
test-engineer's and release-manager's own items. Estimated effort: TD-1 0.5 d, TD-2 2 d, TD-3 0.5 d, TD-4 1 d, TD-5
4 d, TD-6 1.5 d, TD-7 1 d, TD-8 0.5 d, TD-9 0.5 d, TD-10 0.5 d, TD-11 3 d, TD-13 5 d: **~20 lane-days** to Fri 11-20,
against ~25 lane-days one lane gives from Mon 10-19. TD-12 is a planned feature lane (W2-1), and TD-14 (~10 days)
falls in the Wave 3 window, so the default budget holds; no increase requested. The builder WIP limit (no new builder
lane until the batch PR is open, ROADMAP section 2) applies to the debt lane too: it starts Mon 10-19 with TD-3.

Debt-lane order: TD-3 → TD-2 → TD-7 → TD-8 script → TD-9 → TD-13 plan support. Test-engineer: TD-4 → TD-6 → TD-9 →
TD-11 → TD-10 → TD-5 test. SRE: TD-1, then TD-5 health and deploy parts.

## 6. ADRs this register needs

| ADR | Subject | Drafts | Decides | By |
| --- | --- | --- | --- | --- |
| 0019 | Wine pinning (exists, proposed) | sre | PD+PO+CTO under the delegation, or the owner if kept | Wed 10-21 |
| 0021 | Operational orders through the RiskGate (exists, proposed) | architect | CTO with trading-safety; not in this register, dated here because proposed ADRs are decided within a week | Fri 10-23 |
| 0023 | Engine split: seams, move-only rule, ratchet | architect | CTO | Wed 10-21 |
| 0024 | Release compatibility: cross-release test, deploy-together manifest, release skew | test-engineer with sre | CTO | Fri 10-23 |
| 0025 | ADR number reservation (amends 0012) | implementer | CTO | Fri 10-23 |
| 0026 | `core.db` cutover and rollback (W2-1) | architect | CTO, trading-safety review | Wed 10-28 |

Numbers 0023-0026 are reserved by this register under the TD-7 interim rule.

## 7. Rulings recorded here (CTO, 2026-10-16)

1. Engine split sequencing: option C (TD-2). Reverse if the persistence seam cannot take a narrow interface.
2. ADR numbers: assigned in the brief until `new-adr` exists (TD-7).
3. Worktrees are removed at merge by the release-manager (TD-8).
4. No auth bypass or clock override in production code to speed up e2e (TD-6).
5. Flakes are root-caused, never fixed by timeout or retry alone (TD-4).

## 8. Not verified / left out

- TD-4's root cause is a hypothesis from the log; the CI trace was not opened (CI logs are not reachable from the
  sandbox, CLAUDE.md "Environment facts"). Whether CI's one retry also failed was not read from the log.
- Unit and integration durations per test were not measured here (no run on a shared, loaded machine); TD-11 starts
  with that measurement.
- The method counts and seam line ranges are from `grep` at `9f04599`; state shared between seams was not traced, which
  is what the architect's seam map (TD-2) settles.
- The pointer to this register from `docs/ROADMAP.md` section 5 is left to the program-director, who owns that file's
  layout, to avoid a merge conflict with lanes editing it tonight.
- No code, config, test or workflow was changed.
