# Backlog throughput: measured diagnosis and operating model (2026-10-16)

Author: program-director, asked by the owner on 2026-10-16 ("deal with the backlog significantly faster"). Base
`9f04599` on `claude/gifted-goldberg-lcb7pl`; `origin/main` at `df6fbb1`. Follows `docs/process/speed-up-review.md`
(2026-10-11), whose items 1-3 and the health/run.py registries have landed; items 4 (continuous release), 5
(tiers), 6 (checklists) and 9 (integration-test speed) have not. Every rail in CLAUDE.md stays exactly as it is.

**Verdict.** Build time is not the bottleneck. A small (S) item is built and reviewed in 2-4 hours of lane time
when its lanes run. Most of its elapsed time is spent waiting: off `main` in a batch (up to 5 days), in serial
review rounds (2.2 per item on average, wording rewritten after the build), and for a lane or reviewer to start.
The fastest gains come from shipping to `main` every day, reviewing the plan and the copy before the build, running
reviewers in parallel, and having scripts do the integration chores. Splitting `runner.py` helps too, but it
carries the most risk, so it comes last.

## 1. Measurements

Measured with three short, uncommitted scripts that run only the git and `gh` commands named in each row. They
become the committed `scripts/flow_metrics.py` on 10-20 (section 3).

| # | Measure | Value | How measured |
| --- | --- | --- | --- |
| M1 | Cycle time, last 25 items (first cited commit to merge on `main`) | **Median ≈ 24 h** (16 shipped: median 5.0 h, range 2.5–127 h; the 9 not yet on `main` are 1–118 h old and ship in PR #83). **4 of 25 took 114–127 h** (43, 44, 48, 53): built on 10-11/12, shipped 10-16. | For each `docs/backlog.d/NN-*.md`, the non-merge SHAs it cites; landing = oldest first-parent commit of the branch that contains the SHA (`git merge-base --is-ancestor`, bisected over `git rev-list --first-parent --reverse <ref>`). This is a lower bound: the first cited SHA is not always the lane's first commit, and the wait before a lane starts is not counted. |
| M2 | Lane hand-back to merge on the integration branch | **Median 0.4 h** for the 10-16 cohort (0.0–1.1 h). After the review fixes, the last fix landed at **1.5–3.2 h** (55, 58, 59, 60). | Same script: first cited SHA, then its landing on `claude/gifted-goldberg-lcb7pl`; last cited SHA, then its landing. |
| M3 | Review rounds per item (16 items reviewed 10-11..10-16) | **Mean 2.2. 12 of 16 (75%) needed ≥ 2 rounds**; 7 needed 3 (43, 44, 51, 52, 59, 60, ADR 0022 attribution). Only 4 passed first time (49, 55, 57, 58). | Fix commits per item in `git log --since=2026-10-12` (subjects with review, re-verify, F-, B- or X-ids) and the verdicts in `docs/backlog.d/` and `docs/handoff.d/`. |
| M4 | Who found what (rounds after the first) | **trading-safety**: 59 HIGH, F1 kill-switch narrowing (44), 36 MEDIUM, 60 F60-1/F60-2 then finding 1 MEDIUM, 43 LOW-1. **quant**: gate-1 holdout bypass HIGH; ADR 0022 three rounds (review, then B1 and F4, then X1 and X2). **ui-ux**: wording after the build on 43, 56 and 61. **security**: 51 and 52 LOWs, each with its own re-verify. **code**: 43 LOWs. **sre**: 59 weekend pager (700c116). | Same sources. About a third of the extra rounds were wording or spec questions that could have been settled before any code (43, 56, 61, ADR 0022 B1/F4). |
| M5 | WIP off `main` over time | 0 until 10-11. **8 commits from 10-12 to 10-16 midday** (oldest 10-11 21:54, waited ~117 h). PR #79 shipped 88 commits. Now **61 non-merge commits (89 with merges)** are off `main`, all in PR #83 (68 commits). | `git rev-list --count --no-merges <main@t>..<integration@t>` twice a day, 10-05 to 10-16. |
| M6 | How long PRs sat | **Open to merge: 9–68 min.** The real wait is before the PR is opened: up to 5 days. The one batch failure (PR #79, golden path, #80) held all 88 commits for ~40 min. | `gh pr list --state all --json createdAt,mergedAt` |
| M7 | Merge conflict events on the integration branch since 10-12 | **31 of 47 merges** have a combined diff: HANDOFF.md 29, BACKLOG.md 16, TRACEABILITY.md 8. Real code conflicts: `runner.py` 6, `api/app.py` 3, `settings.yaml` 3, `ops/checks/risk.py` 3. | `git diff-tree --cc --name-only` on `git rev-list --merges --first-parent --since=2026-10-12`. A rendered doc that the assembler re-renders also shows up here, so this counts conflict events, not hand edits. |
| M8 | Lanes serialised on one file | `runner.py` (3,438 lines, 155 defs) blocks 62 behind 43; `runner.py:486` and `bus.py` block 63 behind 60 (backlog row 63). 93 commits touched `runner.py` since 10-05. | `wc -l`, `git log --no-merges --since=2026-10-05 -- goldbot/engine/runner.py` |
| M9 | Gate and CI time | Local gates 384 s wall: **integration 382 s for 80 tests** (unchanged from 385 s for 74 on 10-11), unit 159 s for 5,194 tests, e2e 7.2 min. **CI critical path is the backend job, 18 min**; frontend takes 8–9 min. 2 of 5 PR runs on 10-16 failed: one real defect (#80), one e2e flake (#82, re-run on PR #81). | `docs/handoff.d/2026-10-16-gates-next-pr.md`; `gh run view <id> --json jobs`; `gh issue list --label ci` |
| M10 | Process slips (10-16) | ADR **0021** taken by two lanes within one minute (69ec176 and d1d86d6; renumbered to 0022 in 8d68d2e; the brief said 0020, git shows 0021). 2 reviewers finished without a hand-back (all-hands shortfall 5). 1 misrouted message (reported; not visible in git). **61 worktrees and 154 agent branches, 150 of them already merged.** | `git log --all --diff-filter=AR -- 'docs/decisions/002*'`; `git worktree list`; `git branch --merged` |
| M11 | Throughput | Items reaching `main` per calendar day: 7 on 10-10, 20 on 10-11, 0 from 10-12 to 10-15 (paused), ~10 on 10-16. **About 2 items a day over the last 5 days.** Backlog: 65 items, 37 done or merged, 8 parked, 20 open (5 ready). | Cycle script landings per day; `docs/backlog.d/*` status fields |
| M12 | Repo settings | `allow_auto_merge: false`; `main` has no branch protection or required checks. | `gh api repos/<owner>/goldbot`, `.../branches/main/protection` (404) |

**What the numbers say.** The cost sits in queues, not in work:

- **Off-`main` wait (M1, M5, M6)** decides the median and the whole tail.
- **Serial review rounds (M3, M4)** add about 1–1.5 h per extra round and take time from the scarcest reviewer.
- **Starting lag** shows in the backlog rows: 5 of 8 Wave 1 lanes never started, and item 1 was 3 days late.
- **Integrator chores (M7, M10)** are done by hand, one at a time.
- **Test time (M9)** is real but second-order: about 6 min per lane gate run, and about 2.2 runs per item.

## 2. Ranked levers

Ranked by expected throughput gain per unit of risk. Gains are estimates from the measurements above, and each is
tracked by the metrics in section 4.

**Never traded away, for any lever:**

- RiskGate is the only path to an order.
- Exits are never gated.
- The owner clicks to confirm every entry (90 s).
- Demo until the phase gate; live only via `unlock_live` and the typed phrase.
- Trading-safety review on the money path, quant review on research, and security review before every push. Each
  of these reviews the final diff, never only a plan.
- HIGH and CRITICAL findings are re-verified by the same reviewer.
- Tests come first.
- Full gates and green CI before `main`.
- No secrets or identifiers in the repo.
- The trial budget and the holdout.

| Rank | Lever | Mechanism | Expected gain | Cost | Risk and guard |
| --- | --- | --- | --- | --- | --- |
| 1 | **Continuous release** (WIP limit, small PRs to `main` daily, auto-merge on green with recorded reviews) | Integration tip goes to a PR **every day by 18:00 UTC** when it holds reviewed work, or per lane when the lane touches disjoint files. Set `allow_auto_merge` and branch protection on `main` with the CI jobs as required checks. The integrator runs `gh pr merge --auto --merge` only when the merge commit names every required reviewer, verdict and SHA (E1). WIP cap: **≤ 24 h and ≤ 25 reviewed commits off `main`**. | Median cycle **~24 h → ≤ 8 h**. p80 **~5 days → ≤ 1 day**. A red job blocks one day's work, not 88 commits. | About 1 h to set up. Takes more CI runs (watch minutes, risk 7; `concurrency: cancel-in-progress`). Turning on protection and auto-merge is a platform change: decided by PD+PO with the CTO, owner informed. If the token lacks admin rights, it becomes a 2-minute owner step. | Low. Required checks enforce the gates more strictly than today. A merge without recorded reviews cannot auto-merge. |
| 2 | **Shift-left review packet and definition of ready** | No lane starts until its item has acceptance criteria, **final owner-facing copy** (ui-ux), **named failing tests**, files, and a **plan review** by each required domain reviewer (trading-safety, quant or security). Each plan review is ≤ 45 min, recorded in the HANDOFF fragment. The W1-1 plan shows the pattern: G1a-c and S1-S11 were absorbed as doc edits before any code (d1d86d6, d4ce33d). New template `docs/process/review-packet.md`: plan, copy, test names, rails touched (S3), rollback (S12), and a reviewer checklist. | Rounds **2.2 → ≤ 1.4 per item**. Wording rewrites after the build **3 → 0** per batch. About **1.2 h less elapsed per item**, and **~35% less trading-safety load**. | About 30–45 min of reviewer time per item, up front. The architect and ui-ux-designer must hold lanes (all-hands E4). | Low. The plan review **adds** a review: the diff review still happens in full. |
| 3 | **Parallel reviewers on one diff, one consolidated fix round** | When a diff is ready, the integrator dispatches **all** required reviewers on the same SHA at once (code, domain, ui-ux, sre or security). The implementer fixes every finding in **one** commit. Each reviewer re-verifies only its own findings plus the fixing diff. A third round goes to the program-director (the existing one-pass rule). | For multi-reviewer items, review elapsed time falls from the **sum to the max**: item 43 went through 3 serial reviews and would need about 1 parallel slot, so **~50–60%** less time for those items. | More concurrent reviewer agents (CPU: `-n 2` each, ≤ 12 workers in total). Findings can conflict; the integrator merges them into one list. | Low. Same reviewers and same bar. |
| 4 | **Integration chores scripted** (generated-docs merge, ADR reservation, dispatch checklist, worktree pruning) | (a) A `.gitattributes` merge driver for `HANDOFF.md`, `docs/BACKLOG.md` and `docs/TRACEABILITY.md` keeps either side and re-renders from the fragments in the same step, so no rendered file ever conflicts. `docs_assemble.py check` fails a stale render. (b) `docs_assemble.py new-adr --slug` reserves the next number in a fragment-style `docs/decisions/.reserved/NNNN-<slug>` file, committed first on the integration branch. (c) A lane dispatch checklist in every brief: agent id; `git reset --hard <integration tip>`; the base SHA echoed back; file boundaries; `GATES_WORKERS`; hand-back via SubagentHandback, **verdict first**. A missing hand-back is re-dispatched within 1 h. (d) Daily `git worktree prune` and removal of merged `worktree-agent-*` branches. | Integrator time **~5–10 min per merge × ~10 merges a day ≈ 1 h a day** back on the serial path. **0** renumber commits and **0** lost hand-backs. | About half a day for the implementer, tests first (the merge driver needs a test with two lanes that both add fragments). | Very low. Docs and tooling only. |
| 5 | **Review SLA and capacity-matched WIP** | A requested review starts within **4 h** (already the rule) and returns within **4 h for S, 8 h for M**. A plan review returns within **2 h**. In-flight work is capped by reviewer capacity: **≤ 2 trading-safety items between hand-back and verdict**. Above that, start a second trading-safety reviewer lane, not a builder. The 4-hourly check lists every review past its SLA. | Removes the **>24 h quant sign-offs (4 items)** and the 10-12 to 10-16 re-review gap (44, 52). Expected: **no item waits more than 8 h for a reviewer**. | Program-director attention every 4 h; extra reviewer lanes. | None to the rails. |
| 6 | **Test-speed budget** | (a) Integration fixtures (`research_pass`, synthetic releases) are session-scoped, cached on disk and copied into `tmp_path` before writing. (b) CI `backend` splits into parallel `unit` and `integration` jobs. (c) A flaky e2e test gets one Playwright retry, a `flaky` label and an issue the same day, and is quarantined only with a named owner and a date; never on the auth or approval specs. (d) Lanes iterate with `scripts/lane_test.sh iter` (testmon) and run the full gates once. **Budget: lane gate ≤ 4 min, CI ≤ 12 min, flake re-runs ≤ 1 per week.** | Local gate **6.4 → ~3.5 min**. CI **18 → ~10–12 min**. About **20% of PR runs** no longer re-run for flakes (1 of 5 on 10-16). | 1 day for the test-engineer. Shared fixtures can leak state: keep them read-only. | Low. The full suite still runs before every merge to `main`. A quarantined test must not be a safety test. |
| 7 | **Owner-only decisions batched weekly** | Every Sunday on #34, one batch: options, recommendation, deadline, with the CFO's numbers and the CTO's evidence. Routine decisions are made by PD+PO the same day (owner delegation 2026-10-16). Each built-but-off item (17, 18, 19, 21, 35) carries its decision row and date (OD-14, 11-30). | Owner-blocked items wait **≤ 7 days** instead of open-ended. 5 items get a dated path to close. | Owner time: one read a week. | None. Owner-only stays owner-only. |
| 8 | **Split `runner.py` along seams** (CTO debt register) | A pure move into a `goldbot/engine/runner/` package (loop, entry, exits, reconcile, state write, notifications) behind the same public API. No behaviour change. Proven by a replay diff before and after (0 differences), the full gates, and a **full trading-safety review**. | Ends the serialisation of 43/62 and 60/63-style pairs, and avoids **~6 code conflicts a week** in `runner.py`. Smaller review diffs. | 1–2 days of implementer time plus a full trading-safety review. Must land before W2-1 (state store step 2) touches the same file. | **Medium**: this is the money path. Runs as its own lane with no other `runner.py` lane open, not during the engine-start weekend and not in the DST-gap week deploy. |

Evaluated and not adopted now:

- **Risk tiers that skip reviewers** (speed-up-review item 5). Lever 2 gets most of the gain without removing a
  reviewer from any path. It stays a CTO proposal.
- **More lanes.** M2 shows lanes are not the constraint. The cap stays at 6–8 builders, refilled to match reviewer
  capacity (lever 5).

## 3. Two-week rollout (Sat 2026-10-17 – Fri 10-30)

| Date | Step | Accountable | Done when |
| --- | --- | --- | --- |
| Sat 10-17 | Merge PR #83 on green; merge `main` back; WIP back to 0 | release-manager | `git rev-list --count origin/main..<integration>` = 0 |
| Sat 10-17 | Decide branch protection, required checks and auto-merge on `main` (PD+PO with the CTO); owner informed on #34; settings applied | cto | `allow_auto_merge: true`; protection lists the CI jobs |
| Sat 10-17 | Lane dispatch checklist and hand-back rule added to the brief template; daily worktree prune | program-director | Template in `docs/process/`; worktrees ≤ 15 |
| Sun 10-18 | `docs/process/review-packet.md` template (plan, copy, test names, rails, rollback, checklist) and the definition of ready in `docs/AGENT_STANDARDS.md` | product-owner | Template merged; 62, 63, 64 and 65 have packets |
| Sun 10-18 | First weekly owner batch on #34 (OD-12, OD-14 preview, items 17/18/19/21/35) | program-director | Comment posted with deadlines |
| Mon 10-19 | Plan and copy reviews for 62, 63 and 64 before their builds (trading-safety for 62 and 63, ui-ux for 64) | architect | 3 packets reviewed and recorded |
| Mon 10-19 – Tue 10-20 | Merge driver for rendered docs and `new-adr` reservation in `scripts/docs_assemble.py` (tests first) | implementer | Two-lane fragment merge test passes; ADR reservation test passes |
| Mon 10-19 | Parallel-review dispatch: every diff from today goes to all required reviewers at once | program-director | 4-hourly check shows 0 serial review chains |
| Tue 10-20 | Daily flow metrics script (section 4) and first numbers on #34 | test-engineer | `scripts/flow_metrics.py` merged with tests; numbers posted |
| Wed 10-21 – Thu 10-22 | Session-scoped integration fixtures and the CI unit/integration split; flaky e2e policy | test-engineer | Integration ≤ 200 s locally; CI ≤ 12 min on one PR |
| Thu 10-22 | Mid-point review: rounds, cycle time and WIP against targets; adjust | program-director | Note on #34 |
| Fri 10-23 | Debt register in `docs/ROADMAP.md` with the `runner.py` split as rank 1, its plan reviewed by trading-safety | cto | Register present; split plan approved |
| Mon 10-26 – Wed 10-28 | `runner.py` split lane (pure move, replay diff, full trading-safety review); no other `runner.py` lane open | implementer | Replay diff 0; trading-safety APPROVE recorded |
| Fri 10-30 | Two-week review: metrics vs. section 4 targets; keep, adjust or drop each lever; weekly scorecard | program-director | Section 4 table filled; decision recorded in `docs/decisions/` if a lever is dropped |

## 4. Daily metrics (posted with the daily scorecard)

| Metric | Definition | Today | Target by 10-30 |
| --- | --- | --- | --- |
| Cycle time | Median and p80, first commit to merge on `main`, items shipped in the last 7 days | median ~24 h, p80 ~5 days | median ≤ 8 h, p80 ≤ 24 h |
| Review rounds | Mean review rounds per item shipped (a round = one verdict plus its fix) | 2.2 (75% ≥ 2) | ≤ 1.4 (≤ 30% ≥ 2) |
| WIP | Reviewed commits off `main`, and the age of the oldest | 61 commits; oldest 117 h before PR #79 | ≤ 25 commits, oldest ≤ 24 h |
| Throughput | Items merged to `main` per day (7-day average) | ~2 | ≥ 3 while ready items exist |
| Review wait | Hours from hand-back to the first verdict (max of the day) | > 24 h on 4 quant items | ≤ 8 h |
| CI health | PR runs needing a re-run; backend job minutes | 1 of 5; 18 min | ≤ 1 a week; ≤ 12 min |

## 5. Forecast

The open set is 20 items: the 15 ranked rows plus the 5 built-but-off items. Speed cannot close every one of them:

- **Calendar-bound (2):** 5, the Q1 freeze on 12-14; and 45, the quant ruling 10-23 then the decision 10-30.
- **Owner-decision-bound (5):** 17, 18, 19, 21 and 35, all built and off; OD-14 is due 11-30.
- **Engineering-closeable (13):** 1, 27, 36, 43, 44, 51, 59, 60, 61, 62, 63, 64 and 65. Items 1 and 27 also depend
  on the engine start on 10-18 and 24 h of health.

Not counted in the 20: the remaining parts of 12, 13, 24, 26 and 30. Their done parts count as done, and the rest
is parked until there are trades or data.

| Scenario | Rate | 13 engineering items close by | Whole open set |
| --- | --- | --- | --- |
| Today's process | ~2 items/day, 2.2 rounds, batch PRs, with 62 after 43 and 63 after 60 in series | **~Fri 10-30** (the rows' own due dates run to 10-27–10-30; any pause adds days, as 10-12–10-15 did) | 45 on 10-30; 17–35 after 11-30; 5 on 12-14 |
| Under this plan | ≥ 3 items/day from Tue 10-20, ≤ 1.4 rounds, daily release | **~Thu 10-22** (36, 51 and 61 on 10-17 with PR #83; 44, 59, 43 and 60 by 10-18–10-19; 62, 63 and 64 by 10-21–10-22; 27 by 10-21 after health; 65 by 10-22) | 45 on 10-30; 17–35 by 11-30 in the weekly batch; 5 on 12-14. Speed does not move these. |

So the plan brings the engineering backlog in by about 8 days, and from then on keeps new items to about a day
from being ready to reaching `main`. That frees Wave 2 (11-02) to start on time with a split `runner.py`. Assumptions: no spend-limit
pause longer than 1 day; trading-safety reviewer capacity of 2 parallel lanes; the engine starts on 10-18. Each
broken assumption adds about its own length to the date. The forecast covers delivery only; it says nothing about
profit.
