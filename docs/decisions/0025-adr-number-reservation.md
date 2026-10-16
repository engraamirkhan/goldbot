# 0025: ADR numbers are reserved under a lock; rendered docs merge by re-rendering

Status: proposed (implementer, 2026-10-16). Amends 0012. Number reserved by the CTO register
(`docs/process/tech-debt.md` TD-7) and claimed with `new-adr --number 25`.

## Context
- On 16 Oct two lanes committed ADR 0021 within one minute. The second was renamed to 0022 after the fact
  (`0022-results-verdict-rule.md`). Renumbering is costly because ADR numbers are cited in code comments and docs.
- Since 10-12, 31 of 47 integration merges had conflict events. HANDOFF.md accounted for 29, BACKLOG.md for 16 and
  TRACEABILITY.md for 8 (throughput plan M7). ADR 0012 already makes those three files pure renders of
  conflict-free fragments. Even so, a plain `git merge`, `rebase` or `cherry-pick` stops on them, because both sides
  changed the same rendered lines. Only `docs_assemble.py merge` worked around this.

## Decision
- **Merge driver.** `.gitattributes` routes the three rendered files to the `goldbot-docs` driver
  (`docs_assemble.py merge-driver`). git computes a merge in memory, so the working tree is not yet merged while the
  driver runs.
  - **What it renders.** The driver takes the three commits from git's conflict-marker labels (`GITHEAD_*` for
    `git merge`). It merges the fragment directories per file, three-way, and writes the render of that result.
  - **When it fails.** A fragment changed differently on both sides, or a render error such as two lanes taking one
    id, makes the driver exit 1. It then leaves the usual conflict markers and names the fragment or the `fix-ids`
    hint, so the merge stops loudly. git conflicts on the fragment itself as before.
  - **Fallback.** When the labels name no commits (a stash), the driver falls back to a text merge.
  - **Setup.** `install-merge-driver` registers the driver in the repository's git config, once per clone; every
    worktree shares that config. Without it, git merges the files as text, as before. CI and cloud sandboxes need
    nothing.
- **ADR numbers.** `docs_assemble.py new-adr --slug S --title T` takes the next number while holding a lock
  (`flock`) in the git common dir (`git rev-parse --git-common-dir`), which every worktree on the machine shares.
  - **Which number.** The next number is one past the highest of four sources: the ADR files in the tree, the ADRs
    committed on any local branch, the numbers already handed out (a ledger in the common dir) and the reservations
    in `docs/decisions/reserved.yaml`. The command records the number in the ledger before it writes
    `docs/decisions/NNNN-S.md` from a `Status: proposed` template. A number, once handed out, is never handed out
    again.
  - **Claiming a reserved number.** A lane whose brief names a reserved number claims it with `--number N`. The
    command refuses a number that is unreserved or already taken.
  - **Check.** `docs_assemble.py check` (the pre-commit hook, now also triggered by `docs/decisions/`) fails on two
    ADR files with one number.
- Nothing machine-specific is committed. The ledger, the lock and the driver's interpreter path live in `.git/`.

## Consequences
- Rendered docs no longer conflict in a plain merge, rebase or cherry-pick once the driver is installed. Only real
  fragment conflicts and id clashes stop a merge.
- **Gaps.** An abandoned reservation leaves a gap in the numbering. Gaps are allowed; reuse is not.
- **Machines that share nothing.** A cloud sandbox does not share the git common dir, so it still takes its number
  from the brief, as the interim rule says. The check catches any collision at merge.
- **Not done here.** The check does not yet fail on a citation of an ADR number that does not exist (TD-7 asks for it
  too). The register still cites reserved numbers whose records are unwritten, so that check needs the reservation
  file as its allow-list. It is left to a follow-up.
