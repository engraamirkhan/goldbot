# Token efficiency (S14), 2026-10-17

Cause: the owner hit the usage limit twice on 2026-10-17 (~120 subagents, mostly Opus reviews, 2-3 reviewers plus
re-verify per build, full `gates.sh` per lane at load 150-250, big files re-read per lane, long reports, CI polling).
Goal: at least 50% fewer tokens per delivered item. Nothing below touches S1-S13, review routing coverage, E1, or the
HIGH/CRITICAL re-verify rule. Estimates are planning figures, not measurements; the PD records actual spend weekly.

## Ranked rules

| # | Rule | Saves | Quality risk |
| --- | --- | --- | --- |
| 1 | **Model tiers.** Opus only for trading-safety (engine/risk/execution/approvals), quant rulings, architecture, CTO/CFO. Sonnet for code-reviewer, security-reviewer, sre, data-engineer, test-engineer, implementer. Haiku for ui-ux-designer copy checks, docs-only review, PD status chores, CI/data polling. | 30-40% | Low. Escalate to Opus when a Sonnet reviewer rates anything HIGH. |
| 2 | **Reviewers sized to risk.** Only the routed reviewers for what the diff touches. LOW docs/test-only change: one combined reviewer prompt (code + docs). Re-verify only HIGH/CRITICAL (same reviewer); LOW/MEDIUM fixes are checked by the PD against the diff. | 15-25% | Low. HIGH/CRITICAL rule unchanged. |
| 3 | **Tests narrow in lanes.** Lane: `pytest --testmon -n 2` plus the touched web suite. `gates.sh` in a lane only if it touches engine/risk/execution. ONE full `gates.sh --web` per release candidate, run by the test-engineer. | 10-15%, and removes Mac load | Low: the release gate is unchanged. |
| 4 | **Briefs point, not "read X".** Give file:line ranges and the fragment path; no whole-file reads of runner.py, preregistration, rendered BACKLOG/HANDOFF/TRACEABILITY (read fragments; `grep -n` then `Read` offset/limit). | 10-15% | Low. |
| 5 | **Hand-backs: verdict first, at most 150 words** (reviews at most 100), a table for findings, evidence as sha/run id/file:line. No restating the brief. | 5-10% | Low. |
| 6 | **Fewer lanes.** When the queue is research docs or LOW items, run at most 3 lanes and batch similar items into one brief. Spawn only for independent work. | 10-20% | Low. |
| 7 | **Security scan once per release** (diff-wide, Sonnet), not per lane. A lane that edits workflows, auth, keyring or root tooling still gets its own. | 3-5% | Low-medium. Never skipped before a push. |
| 8 | **PD reads `git diff --stat` and changed hunks only**; no full-file re-reads to accept a lane. | 3-5% | Low. |
| 9 | **No polling.** Check CI or data runs at most once per 30 min, with `gh run list --limit 3`; otherwise wait for the completion notice (E3 chases stay on the clock). | 3-5% | None. |
| 10 | **Cache-friendly prompts.** Stable prefix first (role, standards, rules), the changing task last; do not edit shared prefixes mid-wave. | 5% | None. |

## Workflow or single agents

- A Workflow: one-shot fan-out of many identical, independent, read-only checks (for example 10 files, same question).
  It saves orchestration turns and the PD's context.
- Single agents: anything with dependent steps, edits, or judgement; and any item under three files.

## Budget and tripwires

- Each lane brief states a token ceiling (default: 1 builder + routed reviewers only). The PD logs lane count, model
  mix and rough spend in the daily scorecard.
- At 70% of the usage window: stop new lanes, finish in-flight, run only safety-critical work. At 90%: park and report
  to the owner with the resume list.
- Expected combined saving: about 55-65% per item (rules overlap; not additive).
