# Speeding up goldbot delivery: evidence and ranked changes (2026-10-11)

Author: program-director. Status: proposal, not committed. The money-path rules in CLAUDE.md stay as they are:
RiskGate is the only path to an order, demo stays demo until the gate is passed, exits are never gated, and
trading-safety and quant review remain mandatory on their paths.

## Evidence (since 2026-10-05)
| Measure | Value |
| --- | --- |
| Commits / merge commits | 407 / 152 (230 commits on 2026-10-10 alone) |
| Merges that needed hand resolution (`diff-tree --cc`) | **105 of 152 (69%)** |
| Files resolved by hand, by merge count | HANDOFF.md 91, TRACEABILITY.md 71, BACKLOG.md 26, RUNBOOK.md 24, runner.py 21, ops/run.py 19, health.py 19, settings.yaml 16, config.py 15, jobs.py 11, gate.py 6 |
| Most-touched files (commits) | HANDOFF 137, TRACEABILITY 101, runner.py 61, RUNBOOK 47, health.py 38, BACKLOG 37, run.py 34 |
| Hot file sizes (lines) | engine/runner.py 2,769 (119 defs), HANDOFF.md 1,432, ops/health.py 1,518 (71 defs), BACKLOG 688, gate.py 489, TRACEABILITY 347 |
| Local gates (last run) | integration **384.7 s for 74 tests** (22 files); unit 156.6 s for 4,078 tests; mypy, ruff and pre-commit take seconds; dry run 15 s; web gate FAILED (18 errors) |
| Machine during that run | 12 cores, **load 44 / 68 / 41** (1/5/15 min). The rule in AGENT_STANDARDS is `-n 4`; gates.sh defaults to `-n auto` |
| CI | one PR run + one push-to-main run per batch, about **15-16 min each**; integration runs inside the `backend` job, after unit |
| Integration branch | 46 commits ahead of main, shipped as one PR; open PRs are Dependabot only |
| Fix/review follow-up commits | at least 20 commit subjects are review or finding fixes (counted by subject, so a lower bound) |

What this says: the main cost is not test time but **serial hand-integration**. Seven in ten merges need a human
(the orchestrator) to resolve conflicts, almost all in shared docs and four Python hubs. Gate time is also
inflated by CPU oversubscription: 12 lanes, each starting `-n auto` (12 workers), on 12 cores.

## Ranked changes
**1. HANDOFF.md as per-change fragments (towncrier-style).** Problem: 91 hand resolutions and 137 commits; at 1,432
lines it is a log, not a handoff. Change: each lane writes `changes/<branch>.md` (new file, so no conflicts);
`scripts/assemble_handoff.py` builds the "recent changes" section at PR time; HANDOFF.md keeps only the current
state (about 150 lines) and is edited by the integrator alone. Speed-up: removes about 60% of conflict events.
Cost: half a day. Risk: none to the money path.

**2. Conflict-free TRACEABILITY and BACKLOG.** Problem: 71 + 26 resolutions, caused by row ids and the counts table
(`implemented | 178`). Change: rows move to `docs/trace/<id>.yaml` (one file per row); ids are slugs or come from
`scripts/next_id.py` (date plus lane, no shared counter); the markdown table and counts are generated, and a CI
check fails when they are stale. BACKLOG items get stable slugs, and the ranking lives in one short list that only
the product owner edits. Speed-up: about 30% fewer conflict events; no more renumbering. Cost: 1 day plus a migration
script. Risk: low, as long as the generator is tested so no traceability row is lost (check counts before and after).

**3. Pace CPU and fix the gates default.** Problem: load 44-68 on 12 cores, and gates.sh defaults to `-n auto`
against the written `-n 4` rule. The 6.4 min integration time is measured under contention. Change: gates.sh
defaults to `GATES_WORKERS=4` and to `nice` in lanes; the orchestrator allows N lanes such that N x 4 <= about 16
workers (the memory rule "<12 add, >24 none" stays). Measure `--durations=15` once on an idle machine and record a
baseline. Speed-up: likely 2-3x on wall time per gate run (to be confirmed by the idle baseline). Cost: 1 hour.
Risk: none.

**4. Smaller PRs, merged all the time, with auto-merge.** Problem: 46 commits in one PR; each batch costs two CI
runs of about 15 min plus a merge-main-back step, and one red job blocks everything. Change: one PR per lane, or
per 2-3 lanes on disjoint files, straight to main with `gh pr merge --auto --squash`. Turn on branch protection
with required checks and a GitHub merge queue (it is free on public repos), so main is tested with the PR applied
and the merge-main-back step goes away. Retire the long-lived integration branch. Speed-up: no batch-wide
blocking; the integrator stops serialising. Cost: 1 day. Risk: more CI minutes (free on a public repo). Safety:
required checks enforce the gates more strictly than today.

**5. Risk-tiered review routing.** Problem: every change runs the full chain; 2-3 round trips per lane. Change: add
tiers to AGENT_STANDARDS. T0 is docs or tests only, with no change under `goldbot/` (code-reviewer only, or none
for generated files). T1 is non-money-path code (code-reviewer plus the domain reviewer from the table). T2 is
anything in engine, risk, execution, approvals, gate, costs or labels (full chain, unchanged). Tier detection is a
script run over `git diff --name-only`, not judgement, and the rule leans up: any T2 path makes the change T2.
Security review before every push stays for workflows and auth. Speed-up: about 40-50% fewer review invocations;
most doc and test lanes ship in one pass. Risk: a path-based tier misses a money-path change made through config
(settings.yaml). Mitigation: put `config/settings.yaml`, `goldbot/config.py` and `risk/` in T2 explicitly.

**6. Reviewer checklists in every implementer brief.** Problem: CHANGES REQUIRED 2-3 times per lane, often for
known classes of issue (S1-S12, tolerant reads per ADR 0010, asof joins, tz units). Change: each reviewer agent
keeps a short "top 10 findings" checklist in `.claude/agents/checklists/`; the brief template pastes in the ones
for its tier; the implementer hands in a self-check table. Add cheap lint rules where possible (a ruff or grep
pre-commit rule against `.asi8` and `.view("i8")`, a check for raw joins to bars without `asof_join`). Speed-up:
round trips from about 2.5 to about 1.3, and every round trip costs a full gate run. Cost: half a day. Risk: none.

**7. Split the hot Python hubs along registries.** Problem: runner.py (2,769 lines, 21 resolutions), health.py
(1,518 lines, 19), ops/run.py (19), config.py (15), settings.yaml (16). Lanes collide on append points
(`run_checks`, entry points, settings blocks). Change: health checks go to `goldbot/ops/health/<check>.py`, each
with a `@register_check` decorator, so `run_checks` iterates the registry; run.py entry points use a registry the
same way; runner.py splits into a `runner/` package (loop, entry, exits, reconcile, notifications) behind the
same public API, as a pure move; settings sections go into per-domain sub-models. Speed-up: about 15% fewer
conflicts and smaller review diffs. Cost: 2-3 days. Risk: MEDIUM for runner.py, which is the money path. Do it
as one dedicated lane with no behaviour change, a full trading-safety review, and a dry-run and replay diff
before and after. Do health.py and run.py first; they carry less risk.

**8. Test impact analysis per lane, full suite at integration.** Problem: each round trip in a lane runs about
6.5 min of gates, 2-3 times. Change: lanes run `pytest --testmon` (or tests mapped from the changed modules) with
`-n 4`, plus ruff and mypy; the full `gates.sh` runs only at integration and in CI. Speed-up: lane feedback from
minutes to under a minute. Risk: a missed dependency. Mitigation: the full suite stays mandatory before merge, so
it is never skipped, only moved later.

**9. Faster integration tests and CI layout.** Problem: 74 tests take 385 s (about 5 s each); in CI, integration
runs in series after unit inside one job. Change: split `backend` into parallel unit and integration jobs; make
the synthetic-release fixtures `scope="session"` and cache them on disk (research_pass is module-scoped today
and is rebuilt per xdist worker); use `--dist loadfile`. Speed-up: CI from about 15 min to about 9-10 min, and
local integration likely under 3 min. Cost: 1 day. Risk: shared fixtures that leak state. Keep them read-only and
copy them into `tmp_path` before writing.

**10. Spend and budget pacing.** Problem: spend limits killed agents mid-run, and the work was lost or had to be
re-run. Change: each lane commits work in progress after every green step (the branch is the checkpoint), and the
brief caps scope at about 1-2 hours of agent time. The orchestrator tracks the daily budget and stops launching
lanes at about 80% of it; read-only research uses cheaper models. Speed-up: no lost lanes. Risk: none.

**11. Fewer, bigger lanes, aligned to modules.** Evidence: 12 lanes plus a 69% conflict rate means the orchestrator
is the bottleneck, not the lanes. Change: until items 1, 2 and 7 land, run 6-8 lanes, each owning one module area
for a whole backlog theme, and not 12 single-item lanes. After the fixes, scale back up while the CPU allows.

**12. Owner actions.** (a) Standing approval to deploy to the **demo** Oracle VMs from a named script
(allow-listed in Claude settings by the owner), so deploys don't wait on leaving auto mode. Live stays behind
`unlock_live` plus the typed phrase. (b) Turn on branch protection, the merge queue and auto-merge in the repo
settings. (c) Set a daily agent budget so pacing can be planned. (d) Agree the review tiers in item 5, because
they change a standing rule in CLAUDE.md.

## Top 3 this week
1. Items 1 and 2: HANDOFF fragments and a generated TRACEABILITY/BACKLOG (removes about 90% of hand resolutions).
2. Item 3 plus 8: `-n 4` default, nice, an idle baseline, and testmon in lanes.
3. Item 4: per-lane PRs to main with a merge queue and auto-merge (owner switches on the repo settings).

## Needs owner approval
- Item 4 (repo settings: branch protection, merge queue, auto-merge; retiring the integration branch).
- Item 5 (changes the review rule in CLAUDE.md and AGENT_STANDARDS; money-path review is unchanged).
- Item 12a (standing demo-deploy allow-list) and 12c (daily budget).
- Items 1-3 and 6-11 are internal process changes and need no approval, though 7 needs a full trading-safety review.
