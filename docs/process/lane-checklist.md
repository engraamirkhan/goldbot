# Lane dispatch checklist

Owner: program-director. Used by whoever dispatches a lane: program-director, release-manager or the integrator.
Source: throughput plan lever 4(c) (`docs/process/throughput-plan.md`) and the all-hands memo of 16 Oct
(`docs/process/all-hands/2026-10-16.md`, shortfall 5 and F1-F5). Copy the brief block below into every brief and
fill it in. A brief without it is not dispatched.

## Before dispatch

- [ ] **The right agent id.** Use the role file in `.claude/agents/<id>.md` that owns the work: `implementer`,
      `test-engineer`, `code-reviewer`, `trading-safety-reviewer`, `quant-reviewer`, `security-reviewer`, `sre`,
      `data-engineer`, `ui-ux-designer`, `architect` and so on. Use a reviewer id for a review and never the builder's.
      A generic agent does not carry the role's Definition of done.
- [ ] **The item is ready.** It has acceptance criteria, the files it may touch and the named failing tests. If it
      touches the money path or research, it also needs a recorded plan review (lever 2).
- [ ] **The ADR number, if any.** Reserve it first: `python scripts/docs_assemble.py new-adr --slug <s> --title <t>`.
      The command prints the number to put in the brief. A number the register reserved is claimed with `--number N`.
      Never write "the next free number".
- [ ] **No other open lane owns the same files**, `runner.py` above all (throughput plan M8).

## The brief block (copy into every brief)

```text
Agent: <agent id>                  Item: BACKLOG <NN> / TD-<n>          Due: <date, time UTC>
1. Reset first: `git reset --hard <integration tip SHA>` (claude/gifted-goldberg-lcb7pl at <SHA>), then echo
   `git rev-parse HEAD` as the first line of your hand-back.
2. Read docs/process/all-hands/2026-10-16.md (the expectations E1-E5, F1-F5) and docs/AGENT_STANDARDS.md.
3. Files you may touch: <list>. Leave every other file alone.
4. Tests first, iterate narrow (`scripts/lane_test.sh iter` or the touched test files at `-n 2`), then the full gates
   once at the end on a tree nobody edits. GATES_WORKERS=<n>, within ≤ 12 workers in total across lanes.
   Run `scripts/ci_local.sh` before any push (CI-faithful: clean env, no gh/ssh/curl/network, the CI shards; reports
   FLAKY vs FAIL; roughly 20 min). Run `scripts/merge_smoke.sh` before handing back (about 40 s: runner LINE_CAP, persistence allowlist, CLI golden,
   loop grid, migration numbers, settings load, ruff and mypy on changed files); `docs_assemble.py merge` runs it too.
5. Commit in `<type>: <description>` form. Every commit message ends with the trailer
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push. Do not spawn subagents.
6. Docs as fragments (ADR 0012): `scripts/docs_assemble.py new-handoff <slug>` / `new-row` / `new-item`, then
   `python scripts/docs_assemble.py`.
7. MANDATORY hand-back through SubagentHandback (plain text at the end is lost). Lead with the verdict: done,
   blocked or partial. Then give the base SHA, the commit SHAs, the gate results verbatim with counts, and what was
   NOT done or verified.
```

## After dispatch

- [ ] **Record the dispatch:** agent id, base SHA, brief, and the time it is due back.
- [ ] **Re-dispatch after 1 h without a hand-back.** If there is no hand-back within 1 h of the lane's expected end,
      or 1 h after its last sign of progress, inspect the worktree (`git -C <worktree> log --oneline <base>..`). Then
      re-dispatch from the last commit with the same brief and a note on what is already done. A finished lane with
      no hand-back is a failed lane (all-hands shortfall 5).
- [ ] **On the hand-back,** check that the echoed base SHA matches the one you sent. Check that every commit carries
      the trailer and that the gate counts are pasted, not summarised. Then route the reviews in parallel (lever 3).
- [ ] **Merge:** use `python scripts/docs_assemble.py merge <branch>`, or a plain `git merge`. Both work once the
      rendered-docs merge driver is installed (`python scripts/docs_assemble.py install-merge-driver`, once per
      clone). Record the reviewer, the verdict, the SHA and the date in the merge message (E1).
- [ ] **When merged:** the lane's worktree goes. `scripts/prune_worktrees.sh` lists the merged, clean worktrees that
      have been idle for 24 h. Add `--apply` to remove them; it never touches a dirty, locked or unmerged tree.
