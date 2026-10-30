# 0060: Lane flow: one worktree, one full local gate, one combined review round, one PR per lane

Status: accepted (PD + PO + CTO, 2026-10-30, under the owner's 10-30 "parallel, local, big PRs" directive; proposed by the cto the same day); the
tooling that enforces it ships as one PR by Tue 11-03 (`docs/ROADMAP.md` section 5a, FR-1 to FR-8). Amends
`docs/process/lane-checklist.md` and the lane rules in docs/AGENT_STANDARDS.md (via the product-owner).

## Context
Measured on 2026-10-30 (sources in ROADMAP 5a):
- 15 merged PRs in 5 days, 5 of them 2-3 commits; each had its own review round, its own local gate (931-1,023 s
  today, 2.3-2.5x GitHub's 6.8 min median) and its own CI wait. 21 CI runs for 15 PRs, 6 red.
- 12 of 33 CI failures since 10-07 were deterministic guard tests (goldens, registries, allowlists, line caps) that a
  lane first ran after its push; 10 were timing or e2e flakes; 2 were a calendar date in a test.
- Lanes start in worktrees with no venv or an ad-hoc one: 6 on Python 3.12 with numpy 2.5.x against CI's 3.11 and
  numpy 2.4.6, 60 with none. The scripts and the lane brief still default to the integration branch
  `claude/gifted-goldberg-lcb7pl`, dead since 10-19, so `lane_test.sh iter` diffs against an 11-day-old base and the
  pruner compares against the wrong branch (373 worktrees, 168 GiB).
- The 10-16 register named "interpreter choice" and worktree pile-up as daily costs (tech-debt.md TD-3, TD-8); they
  were answered with rules, not tooling, and both got worse. Rules without a guard do not hold under 8-12 parallel
  lanes.

## Options
1. **Status quo:** a PR per fix, sequential reviews. Fast for one change, slow in aggregate; the fixed cost per PR
   (review round, local gate, CI) is paid 3 times a day.
2. **Daily release train on an integration branch** (what `claude/gifted-goldberg-lcb7pl` was). Batches well, but the
   branch went stale, every script kept its name, and a shared branch couples unrelated lanes' failures.
3. **One PR per lane, enforced by tooling** (chosen).

## Decision
1. **One lane = one worktree = one branch = one PR.** `scripts/new_lane.sh <slug>` creates the worktree from
   `origin/main` and builds its environment with `scripts/worktree_env.sh` (`uv sync --frozen --extra dev` on the
   3.11 pinned by `.python-version`; `npm ci` only when the lane touches `web/`). It refuses while more than 40
   worktrees exist or free disk is under 100 GB, and prints the prune command.
2. **Environment check before any gate.** `scripts/check_env.py` runs first in `gates.sh`, `ci_local.sh`,
   `lane_test.sh` and `merge_smoke.sh` and exits 2 on an interpreter other than 3.11 or a venv that differs from
   `uv.lock`. No lane reports a type or test result from a mismatched venv.
3. **Iterate narrow, with the guards.** Failing tests first; `scripts/lane_test.sh iter` runs the touched tests plus
   the `guard`-marked set. The full suite is never run while iterating.
4. **One full local gate per PR.** `scripts/ci_local.sh` runs once, on the final tree, started by `scripts/release.sh`
   alongside GitHub CI. GitHub is the gate; a local red stops auto-merge.
5. **One combined review round.** code-reviewer, the domain reviewers the routing table requires (quant,
   trading-safety, sre, data) and security-reviewer start in parallel on the same SHA. Their findings are merged into
   one list; the implementer fixes them in one batch. A HIGH or CRITICAL finding closes only on the same reviewer's
   re-verify (unchanged safety rail); LOW and MEDIUM fixes are checked by code-reviewer in the same pass.
6. **Bundle small changes.** `release.sh` refuses a PR under 50 changed lines unless it is labelled `hotfix`. Hotfix
   is reserved for the money path (an exit, RiskGate, an engine down or a stuck deploy) and ships alone at once.
   Other small fixes ride the next lane PR in the same area.
7. **Auto-merge on green, deploy, prune.** After the merge `release.sh` deploys per
   `docs/process/deploy-approval-runbook.md`, runs the post-deploy checks, and removes the lane's worktree and branch.
8. **Tests do not read the clock unfrozen.** The suite gets a date-shift option; every backend-unit CI shard runs at a
   shift derived from the SHA (0-45 days, printed, reproducible with `GOLDBOT_TEST_SHIFT_DAYS`). A flaky test is
   quarantined with an issue and an expiry of at most 48 h; an expired quarantine fails the build.

## Build vs buy: test clock
| Option | Licence | Cost and risk | Verdict |
| --- | --- | --- | --- |
| `time-machine` (dev extra only) | MIT | C-level patch of `datetime`, `time` and so `pd.Timestamp.now`; fast; one maintainer | **Chosen** |
| `freezegun` | Apache-2.0 | Pure-Python patching, slower, misses some C-level paths | Fallback |
| In-house clock seam only | - | Needed for production anyway (TD-14 `engine/clock.py`), but does not cover tests that call `datetime` directly | Complements, not replaces |
Not shipped to the brain or MT5 hosts (dev extra). Reverse if it breaks on a pandas or Python upgrade, or the seam
covers every reader. No paid plan; no CFO line needed.

## Consequences
- Fewer, larger PRs: the per-PR fixed cost (≈ 7 min CI, 15 min local gate, a review round) is paid once per lane.
  Larger diffs per review: reviewers get the lane brief and the acceptance criteria with the SHA.
- A date-bound or 3.12-only failure now fails its own PR, not everyone's two days later.
- Hotfix keeps the money path fast; the label is audited in the weekly read.

## Measured by (weekly read, from Fri 11-06)
PRs per lane (target 1), review rounds per PR (target 1, plus HIGH re-verifies), CI runs per PR (target ≤ 1.3),
red `main` runs from non-deterministic causes (target 0), dispatch-to-merge lead time, worktrees (≤ 40) and disk.

## What would reverse it
- Median combined review round over 2 h for 2 weeks, or two production rollbacks in a week traced to PR size: cap PR
  size per lane (split by seam).
- The SHA-derived date shift produces more than 2 false failures a week after the first fix sweep: run it as a
  nightly job instead of on every PR.
