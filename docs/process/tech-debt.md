# Platform review and technical-debt register

2026-10-16 · cto · base `9f04599` (`claude/gifted-goldberg-lcb7pl`). Re-read and re-dated weekly (Fridays) with the
debt and quality review; next review Fri 10-23. Evidence is file:line at the base commit, a run id, an issue or a
measured number. Nothing here changes a safety rail.

**Update 2026-10-30, cto (new role holder):** delivery-tooling debt (TD-3, TD-4, TD-8 and the "interpreter choice"
gap named below) is re-measured and superseded by `docs/ROADMAP.md` section 5a (FR-1 to FR-10), with ADR 0060 (lane
flow) and `docs/process/deploy-approval-runbook.md`. TD-8's rule ("removal at merge") did not hold: 60 worktrees /
28 GB on 10-16, 373 / 168 GiB on 10-30. The remaining TD items stand until the Fri 11-06 review re-dates them.

**Update 2026-10-16 (late), cto:** ADR 0019 (TD-1) and ADR 0021 accepted by PD + PO + CTO under the owner's
delegation; TD-16 added (bridge lot ceiling, ranked 3rd); TD-13 given lane id W3-1; standing rule on self-hosted CI
runners added (section 4, "Standing constraint"). ROADMAP §5 now points here.

**Update 2026-10-17, cto:** ADR 0023 (TD-2 / TD-14 engine split) accepted with conditions after review of the architect's
seam map (`docs/proposals/2026-10-runner-seam-map.md`, `7dc12a3`). Step-1 interface accepted (conditions S1-S7);
`_orders_record` stays in the runner; steps reordered (account guard 4th, windows 2nd); stop rule made measurable;
line-cap target after step 7 is ≤ 1,500, with ≤ 1,200 left to a step 8. W2-1 reuses `RiskSnapshot` (its
`RiskStateRecord` is dropped). Ruling 11 in section 7.

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
| 1 | TD-1 | Wine (and MT5 build) unpinned on the MT5 box | 15 (3×5) | 1 | **75** | sre | **decided 10-16 (ADR 0019 accepted, option 2)**; held and in `/health` at the next owner-approved MT5-box command, by Sat 10-24 (market closed) | ADR 0019 (accepted) |
| 2 | TD-2 | Extract the engine's persistence boundary from `runner.py` before W2-1 (split step 1) | 16 (4×4) | 2 | **64** | implementer (seam map: architect, delivered `7dc12a3` 10-17) | PR 0 golden grid Tue 10-27; move merged on the integration branch Fri 10-30 | ADR 0023 (accepted 10-17, conditions S1-S7) |
| 3 | TD-16 | Bridge's only independent size ceiling is a fixed 3.0 lots while the demo account offers 1:5000: an equity-scaled lot ceiling in `GuardedBroker` | 15 (3×5) | 2 | **60** (money path, ranked above TD-3) | architect (plan); implementer builds; trading-safety reviews | plan Fri 10-23; built and merged Fri 11-06 | yes: 0027 bridge lot ceiling |
| 4 | TD-3 | e2e `PYTHON` default can run the server on the wrong interpreter and the wrong tree | 12 (4×3) | 1 | **60** | implementer | Tue 10-20 (after R-AUTH merges; same files) | no |
| 5 | TD-4 | Flaky auth e2e: email sign-in timeout | 12 (4×3) | 2 | **48** | test-engineer | Tue 10-20 | no |
| 6 | TD-5 | Release-compatibility test and deploy-together guard (card_created, entry_blocks) | 15 (3×5) | 3 | **45** | test-engineer (sre: health and deploy parts) | ADR Fri 10-23; test Fri 11-06, before W2-1 merges | yes: 0024 release compatibility |
| 7 | TD-6 | e2e speed: TOTP step waits and one worker | 10 (5×2) | 2 | **40** | test-engineer | Fri 10-23 | no |
| 8 | TD-7 | ADR number collisions: reservation in `scripts/docs_assemble.py` | 8 (4×2) | 2 | **32** | implementer | interim rule today; command Fri 10-23 | yes: 0025 amends 0012 |
| 9 | TD-8 | Worktree and branch pile-up (60 / 172, 28 GB) | 6 (3×2) | 1 | **30** | release-manager | first sweep Mon 10-19; removal at merge from today; script Fri 10-30 | no |
| 10 | TD-9 | Test tooling: undeclared `pytest-testmon`, no per-test timeout | 6 (3×2) | 1 | **30** | test-engineer | Fri 10-23 | no |
| 11 | TD-10 | e2e not in local gates | 6 (3×2) | 1 | **30** | test-engineer | Fri 10-30 (after TD-6) | no |
| 12 | TD-11 | Unit and integration suite speed | 10 (5×2) | 3 | **30** | test-engineer | durations measured Wed 10-21; fixes Fri 11-06 | no |
| 13 | TD-12 | State store: W2-1 cutover and its rollback story | 10 (2×5) | 4 | **20** | implementer (plan: architect) | rollback ADR Wed 10-28; build Wave 2, 11-02 – 11-20 | yes: 0026 core.db cutover and rollback |
| 14 | TD-13 | State store leftovers (lane **W3-1**): step 3 remainder, steps 4-5 and 8, `state_db` health, `run.py migrate` | 9 (3×3) | 3 | **27** → ranked after TD-12, which it depends on | implementer | plan Fri 11-20; JSON exports retired (step 8) Fri 12-11 | covered by 0026 |
| 15 | TD-14 | `runner.py` split steps 2-7 (after W2-1) | 16 (4×4) | 5 | **16** | implementer (plan and state-ownership tables for account guard, reconcile and clock: architect, Fri 11-13) | one seam per PR, Tue 11-24 – Fri 12-11, before the 12-14 freeze; order windows, exits, account guard, sizing, reconcile, clock | ADR 0023 |
| 16 | TD-15 | Watch list: `owner_history.py`, `director.py`, `jobs.py`, `perf_report.py` above 1,100 lines | 4 (2×2) | 3 | **12** | architect | review Fri 11-20 | no |

TD-13 scores 27 but cannot start before TD-12's tables exist; it is ranked by dependency, not score.

## 4. Items

### TD-1 Wine and MT5 version pinning (ADR 0019, accepted 2026-10-16)
**Decided 2026-10-16 by PD + PO + CTO (owner delegated): option 2.** `apt-mark hold` the installed, verified
`winehq-devel` 11.19 and hold the verified MT5 build; Wine version and MT5 build in the bridge `/health`; upgrades
only through a reviewed change rehearsed on a VM. Pinning is a tightening, so it is within the delegation. The sre
implements and verifies it on the box by Sat 10-24; the background below is the evidence it was decided on.

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

### TD-16 Equity-scaled lot ceiling at the bridge (new 2026-10-16, ranked 3rd)
**Finding.** Read from the demo terminal 2026-10-16: leverage 1:5000, margin call 100%, stop-out 50%, margin 0.63 GBP
for 0.01 lot of XAUUSD, about 15.9 lots possible on free margin. goldbot's own sizing is bounded several times over:
the gate's margin check uses the stricter of the broker's figure and 1:20 (`goldbot/risk/gate.py:62`, `:365-388`),
combined open notional is capped at 0.30 × 20 = 6× combined equity (`gate.py:63-66`, `:536-544`), and the per-order
ceiling is the smaller of the broker's maximum and 2.0 lots (`gate.py:176-180`, applied in
`goldbot/engine/runner.py:1032`). The bridge, the last process before the broker and the only one that runs on the
MT5 box, has one independent size check: a fixed `MAX_ORDER_LOTS = 3.0` (`goldbot/execution/bridge.py:104`, checked
at `:266`). It does not scale with equity.

**What that means in money** (derived from the terminal's figures: 0.63 GBP at 1:5000 for 0.01 lot = 1 oz implies
about £3,150 per oz, so 1 lot ≈ £315,000 notional; equity ≈ £1,000 from the 15.9-lot figure, consistent with ADR
0004):

| Order | Notional | Broker margin at 1:5000 | Loss per $1 move (at ~1.33 USD/GBP) | Move that loses £1,000 |
| --- | --- | --- | --- | --- |
| What the gate allows (6× equity) | ≈ £6,000 → 0.01 lot | £0.63 | ≈ £0.75 | ≈ $1,330 |
| Bridge ceiling, 3.0 lots | ≈ £945,000 (≈ 945× equity) | ≈ £189 | ≈ £225 | ≈ $4.4 (≈ 0.1%) |

The broker accepts the 3.0-lot order (margin £189 against £1,000), and the 50% stop-out only acts once equity falls
under ≈ £95. So if a sizing bug got past the RiskGate (wrong `contract_oz`, a unit or fx error, a lot field taken from
the wrong record), the account could be wiped by an ordinary few-dollar move before anything stops it. The bridge's
ceiling is ~300× larger than what the gate would ever send.

**Why likelihood 3, not 2.** The sizing path is the most-changed code in the repo (`runner.py`, 99 commits since
10-01, section 1), ADR 0009 exists because an order exceeding its card was possible, and TD-2/TD-14 move this code
again. Impact 5: whole account.

**Direction for the plan (architect, due Fri 10-23; ADR 0027, CTO decides, trading-safety reviews):**
1. `GuardedBroker.place_order` refuses an order whose lots, plus the account's open goldbot lots, exceed a ceiling
   derived from the terminal's own reads at order time (equity, the symbol's contract size and price, margin or
   notional in account currency), independent of anything the brain sent. Starting point for the bound: the gate's
   own combined notional cap (6× equity) with headroom for one volume step, so it never refuses an order the gate
   allows; the plan states the exact formula and its margin.
2. **Fail closed** for entries: if equity, the tick or the contract terms cannot be read, the order is refused with a
   named reason. **Exits are never gated:** `close` (`bridge.py:277-278`) and `modify` keep their current paths; the
   plan proves no close or kill-switch path calls the new check.
3. `MAX_ORDER_LOTS` stays as an absolute ceiling; the new check only tightens.
4. Internal only, as ADR 0021 F4: no change to `METHODS`, signatures, `API_VERSION` or `protocol_digest`.
5. Tests first: refusal at the boundary, equality behaviour stated, a read failure refuses, a close at any size
   passes, and a grid showing every order the gate's limits allow passes the bridge.
6. Open question for the plan: whether the bridge should also refuse when the account's leverage exceeds a configured
   ceiling, or only report it. Reporting the broker's leverage in `/health` is in scope; refusing on it is not, until
   the CFO and owner have seen the capital plan (`docs/finance/`).

The ceiling is a tightening (a second, independent rail behind RiskGate), so it is within the delegation; it changes
no gate threshold or risk limit. **Reverse if** the plan shows the terminal's own reads at order time are not reliable
enough to bound on (then the bound moves to a configured equity figure refreshed by the sre, still fail-closed).

### Standing constraint: no self-hosted CI runner on the brain without a security review (CFO flag, 2026-10-16)
ROADMAP W2-8 plans a self-hosted CI runner on the brain VM to save CI minutes (risk 7). On a **public** repo, a
self-hosted runner can be made to run a fork PR's code; on the brain that is anyone's code running on the machine that
holds the Telegram token, the dashboard keys and the engine's state, with a network path to the bridge. Ruling:
**no self-hosted runner is registered on the brain host, or on any host holding trading secrets, until the
security-reviewer has reviewed the design and recorded GO.** The review covers at least: fork-PR workflows never reach
the runner (runner limited to `push` on protected branches; "require approval for all outside collaborators" on), an
ephemeral runner as its own user with no keyring access, and no network path to the bridge. CTO direction: prefer a
host that holds no trading secrets; if the only free capacity is the brain, the security GO must say why the
isolation is enough. Accountable: release-manager (W2-8) with the security-reviewer. Any paid alternative (a larger
CI plan, a VM outside the free tier) goes to the CFO and then the owner.

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
3. **Ratchet.** A test caps `runner.py`'s line count at its post-step size, lowered at each step, so the hotspot
   cannot regrow. Target after step 7: ≤ 1,500 (ADR 0023: the seams move 2,078 lines of 3,492); ≤ 1,200 needs a
   step 8, scoped at the step-7 review.
4. **File ownership.** After each step the ROADMAP "Shared files" note lists the new module and its owning lane.
5. **No split step deploys during the DST-gap week (10-26 – 10-30)**; merging on the integration branch is fine.

**Seams, in order** (superseded 2026-10-17 by ADR 0023, which holds the dated table and line ranges at `7dc12a3`):

| Step | Module | Due | Depends on |
| --- | --- | --- | --- |
| 1 | `engine/store.py`: `EngineStore` protocol, `JsonEngineStore`, `RiskSnapshot` / `OrdersSnapshot`; `_orders_record` and `_save_risk_state_now` stay in the runner | Fri 10-30 (PR 0 golden grid first) | BACKLOG 62 |
| 2 | `engine/windows.py` | Tue 11-24 | step 1 |
| 3 | `engine/exits.py` | Thu 11-26 | W2-1 (F4) |
| 4 | `engine/account_guard.py` (split in two if its ownership table shows > 8 shared attributes) | Tue 12-01 | BACKLOG 62, W2-1 (F5) |
| 5 | `engine/sizing.py` | Thu 12-03 | TD-16 |
| 6 | `engine/reconcile.py` | Tue 12-08 | steps 1, 3; W2-1 |
| 7 | `engine/clock.py` | Fri 12-11 | none |

What stays in `runner.py`: the tick loop, bar close, frame building, `_decide` / `_on_decision` / `_execute`
orchestration and shadow stepping. The 2026-10-16 estimate of "roughly 1,000-1,200 lines" was not backed by
arithmetic; measured, the seams leave ~1,414 lines before delegates (ADR 0023). **Reverse if** PR 0's golden grid
cannot be reproduced by a move-only step 1: then W2-1 goes first in place (option B) and the split moves after it.

**CTO review of the seam map (2026-10-17).** Evidence from `7dc12a3`: anchors re-read with `grep -n "def "`
(all match the map); shared Engine attributes per seam by a regex pass (store 14 as snapshot fields, account guard
24, reconcile 17, clock 15, exits 10, sizing 10, windows 4); `runner.py:527, 3307, 3319` still read files outside
the state store, so the AST ratchet needs an allow-list (S5); `FrozenRecord` is `extra="forbid"` (`base.py:38`)
while the risk load ignores unknown keys, so snapshots must be built from parsed values (S1). Decisions and
conditions in ADR 0023.

### TD-3 e2e `PYTHON` default (`web/playwright.config.ts:5`)
**Done 2026-10-17 (`fdd362b`):** the default is `<repo>/.venv/bin/python`, then `python3`; `e2e_server.py` refuses to run
on a `goldbot` outside its tree (`check_tree`, tests in `tests/test_e2e_server_tree.py`). Finding: the server already
inserts its own root first on `sys.path`, so the "wrong tree" half was prevented; the failure seen was the wrong
interpreter (no duckdb). Text below is the original item.

`const PYTHON = process.env.PYTHON ?? "python"` starts `scripts/e2e_server.py` with whichever `python` is first on
PATH. CI sets `PYTHON: python` after `setup-python` (`ci.yml:113`), so CI is right; locally `python` is often missing,
the system one, or the main checkout's venv, whose editable install imports `goldbot` from the main checkout, not the
worktree (the hazard `docs/AGENT_STANDARDS.md` "Parallel work rules" warns about). The e2e run then tests a
different tree and can pass on code the lane did not write. **Fix:** default to the repo venv's interpreter
(`../.venv/bin/python`, then `python3`), and have `e2e_server.py` refuse to start unless `goldbot.__file__` is under
its own repo root, printing both paths. Same files as lane R-AUTH: added to R-AUTH if it has not merged, else the debt
lane right after.

### TD-4 Flaky auth e2e: email sign-in link (issue #82, PR #81)
**Done 2026-10-17 (`a2c66b0`), root cause proven:** TOTP step waits. Timed on the unmodified suite, the sign-in-link test
slept 21 s + 30 s in `freshTotp` (51.5 s of its 90 s), the phone test 27 s; the server accepts each code once within
+-1 step, so an owner secret used 5 times per story leaves only real time to wait on. Not the spent-link page (the
server does not spend a code there). Fix: `e2e_server.py` steps the existing `auth._totp_now` seam through
`<state>/totp_clock_steps`, `web/e2e/totp.ts` `totpClock` advances it instead of sleeping and records spent steps in
`<state>/totp_last_step`; verification and replay guard untouched, no production code changed. Text below is the
original item.

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
**Done 2026-10-17 (`a2c66b0`):** e2e 19 tests 7.5 min -> about 24 s (3 runs: 23.3, 24.2, 23.4 s) with `workers: 2`. Not by a
fresh user per test (the owner is one per server) but by stepping the e2e server's TOTP clock (see TD-4), which also
meets the "no override in `goldbot/api/auth.py`" rule. Text below is the original item.

**Correction 2026-10-17 (issue #87, CI run on PR #85, trace read):** the TOTP waits were only the slowness; the failing
step is the same line both times (the field fill after the spent-link reopen, `auth.spec.ts:~220/223`) and the cause is a
dead dashboard. In CI's Chrome the sign-out request was cut off by the next navigation and the token stayed in the tab's
session storage; the reopened page loaded as the dashboard, the server answered 401 to every query, and the SPA never
returned to sign-in because `App` only learned `authed` from `hasToken()` at load (the page snapshot shows the dashboard
with "can't reach the server"; the trace shows the whole test at 1.4 s, then 85 s of waiting for a field that could not
appear). A real product bug (a revoked or expired session left the dashboard up forever), fixed in `App.tsx`/`api.ts`
(`onSessionEnded`), with a vitest test and a stale-token e2e test, and the test now waits for sign-out to finish. Not
reproduced locally with a different Chrome or Python (the local browser drops the token on the about:blank hop), so the
stale-token test is the deterministic proof. The `aux.db-wal` and websocket lines in the CI log are shutdown noise.

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
**Unblocked 2026-10-17:** TD-6 landed, e2e is about 24 s, so `--web` can imply it now. Text below is the original item.

Add `scripts/gates.sh --e2e` (implied by `--web` once TD-6 makes e2e ≤ 2 min), so e2e fails on the lane, not on CI
minutes. CI minutes ran out on 10-06 (ROADMAP risk 7).

### TD-11 Unit and integration speed
Unit: 104-181 s locally for 5,203 tests, CI backend job 9.5 min. Integration: 80 tests in 295-385 s, which says a few
tests dominate. **First measure:** `pytest --durations=30` on both suites (test-engineer, Wed 10-21, at `-n 2` on a
quiet tree), then fix the top offenders (shared fixtures, fake clocks instead of sleeps, smaller frames). Targets:
unit ≤ 90 s at `-n 4`, integration ≤ 120 s, CI backend job ≤ 6 min. No test is deleted or skipped to meet a target.

### TD-12 / TD-13 JSON-to-SQLite migration (W2-1 and what follows)
**Note on naming:** ROADMAP W2-2 is `order_calc_margin` and tick-flags dedup, not state; the state work after W2-1 is
the proposal's steps 5 and 8. It is TD-13, lane W3-1.
Lane id: **W3-1** (ROADMAP Wave 3), assigned 2026-10-16 so the post-W2-1 work is planned and tracked as one lane.
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
4 d, TD-6 1.5 d, TD-7 1 d, TD-8 0.5 d, TD-9 0.5 d, TD-10 0.5 d, TD-11 3 d, TD-13 5 d, TD-16 2.5 d (plan 1 d, build 1.5 d): **~22.5 lane-days** to Fri 11-20,
against ~25 lane-days one lane gives from Mon 10-19. TD-12 is a planned feature lane (W2-1), and TD-14 (~10 days)
falls in the Wave 3 window, so the default budget holds; no increase requested. The builder WIP limit (no new builder
lane until the batch PR is open, ROADMAP section 2) applies to the debt lane too: it starts Mon 10-19 with TD-3.

Debt-lane order: TD-3 → TD-2 → TD-16 build (after its plan and ADR 0027) → TD-7 → TD-8 script → TD-9 → TD-13 plan support. Test-engineer: TD-4 → TD-6 → TD-9 →
TD-11 → TD-10 → TD-5 test. SRE: TD-1, then TD-5 health and deploy parts.

## 6. ADRs this register needs

| ADR | Subject | Drafts | Decides | By |
| --- | --- | --- | --- | --- |
| 0019 | Wine pinning | sre | **accepted 2026-10-16** by PD + PO + CTO (option 2) | done |
| 0021 | Operational orders through the RiskGate | architect | **accepted 2026-10-16** by PD + PO + CTO; trading-safety APPROVE at `d1d86d6`, G1a-c fixed in `d4ce33d` | done |
| 0023 | Engine split: seams, move-only rule, ratchet | architect (seam map) | **accepted 2026-10-17** by CTO, with conditions S1-S7 | done |
| 0024 | Release compatibility: cross-release test, deploy-together manifest, release skew | test-engineer with sre | CTO | Fri 10-23 |
| 0025 | ADR number reservation (amends 0012) | implementer | CTO | Fri 10-23 |
| 0026 | `core.db` cutover and rollback (W2-1) | architect | CTO, trading-safety review | Wed 10-28 |
| 0027 | Equity-scaled lot ceiling at the bridge (TD-16) | architect | CTO, trading-safety review | plan and draft Fri 10-23 |

Numbers 0023-0027 are reserved by this register under the TD-7 interim rule.

## 7. Rulings recorded here (CTO, 2026-10-16)

1. Engine split sequencing: option C (TD-2). Reverse if the persistence seam cannot take a narrow interface.
2. ADR numbers: assigned in the brief until `new-adr` exists (TD-7).
3. Worktrees are removed at merge by the release-manager (TD-8).
4. No auth bypass or clock override in production code to speed up e2e (TD-6).
5. Flakes are root-caused, never fixed by timeout or retry alone (TD-4).
6. ADR 0019 accepted, option 2 (TD-1). Decided 2026-10-16 by PD + PO + CTO (owner delegated).
7. ADR 0021 accepted with fences F1-F6. Decided 2026-10-16 by PD + PO + CTO (owner delegated).
8. Bridge lot ceiling scaled to equity (TD-16): direction set, plan by the architect Fri 10-23, ADR 0027.
9. No self-hosted CI runner on a host holding trading secrets without a security-reviewer GO (standing constraint).
10. Post-W2-1 state-store work is lane W3-1 (TD-13).
11. **ADR 0023 accepted with conditions (2026-10-17, CTO).** Step-1 interface accepted (S1-S7); `_orders_record` stays
    in the runner (X7 ordering, `runner.py:2468-2475`); order changed to windows, exits, account guard, sizing,
    reconcile, clock (account guard 4th: most coupled seam, 24 shared attributes, proven pattern first); stop rule
    measured as non-owned, non-injected Engine attributes > 8; ratchet target ≤ 1,500 after step 7, ≤ 1,200 via a
    step 8; W2-1's `RiskStateRecord` replaced by `RiskSnapshot`. Reverse if step 1 cannot be move-only, or three
    seams in a row are stopped.
13. **Owner-facing checklist is part of the deploy (2026-10-17, CTO).** RUNBOOK tunnel (2.7) and first run (2.10) are
    not optional: no deploy is reported done until the owner has opened the dashboard and signed in (S13; incident
    2026-10-17). Reverse only by owner decision.

## 8. Not verified / left out

- TD-4's root cause is a hypothesis from the log; the CI trace was not opened (CI logs are not reachable from the
  sandbox, CLAUDE.md "Environment facts"). Whether CI's one retry also failed was not read from the log.
- Unit and integration durations per test were not measured here (no run on a shared, loaded machine); TD-11 starts
  with that measurement.
- Shared-state counts per seam (ADR 0023) are a regex pass at `7dc12a3`, not a data-flow trace; the architect's
  state-ownership tables (Fri 11-13) settle steps 4, 6 and 7.
- TD-16's figures are the terminal's as read on 2026-10-16; the notional and loss rows are derived from them (price
  and equity inferred, not read from the broker ledger). The architect's plan re-reads them on the box.
- The ROADMAP §5 pointer to this register was added 2026-10-16 (late).
- No code, config, test or workflow was changed.
