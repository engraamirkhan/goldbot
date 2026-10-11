# 0012: HANDOFF, TRACEABILITY and BACKLOG are rendered from one-file-per-change fragments

Status: accepted (implementer, 2026-10-11; docs/process/speed-up-review.md items 1 and 2)

## Context
105 of 152 merges since 2026-10-05 needed hand resolution. The files resolved most often were HANDOFF.md (91),
docs/TRACEABILITY.md (71) and docs/BACKLOG.md (26). Every parallel lane appended to the same lines, took the next
row id or item number, and bumped the same counts. The orchestrator spent its time on those conflicts.

## Decision
- Each change adds new files, and `scripts/docs_assemble.py` renders the three documents from them:
  - `docs/handoff.d/<YYYY-MM-DD>-<slug>.md`: one or more bullets, rendered newest first at the top of "Where things
    are" (or of "Next steps" with `section: next`).
  - `docs/traceability.d/<ID>[-<slug>].yaml`: one row per file. The `id:` field is the id. The counts table is
    computed.
  - `docs/backlog.d/<NN>-<slug>.md`: front matter with `number`, `rank` and the table cells, and the detail block as
    the body. The table is sorted by `rank`. Item numbers stay stable.
  - The static text of each document lives in `_template.md`.
- YAML per TRACEABILITY row rather than markdown: a row is four single-line cells, so YAML needs no body parser and
  `check-yaml` already validates it.
- The rendered files stay committed (the owner reads them on GitHub). Each one starts with a "generated" comment,
  and `.gitattributes` marks them `linguist-generated`, so PR diffs show the fragments. The pre-commit hook
  `docs-assemble` (and therefore CI) fails when a rendered file differs from its fragments.
- New ids are always one past the highest in use (`new-row R`, `new-item`). Two lanes can still take the same id.
  The file names differ (the slug), so git does not conflict; `check` reports the clash and `fix-ids` renumbers it
  mechanically. The fragment that landed first on the branch's first-parent line keeps the id.
- `merge <branch>` is the orchestrator's one command. It runs `git merge --no-commit`, regenerates the rendered
  files instead of resolving them, absorbs an old-style branch (`absorb`: new HANDOFF bullets become fragments,
  TRACEABILITY and BACKLOG rows are merged three-way per field, other edits are merged into the template with
  `git merge-file`), runs fix-ids, and commits. It refuses a new-style branch that hand-edited a rendered file,
  because that text would be lost.
- Rejected:
  - A custom git merge driver: it needs per-clone config, and GitHub ignores it.
  - `merge=union`: it silently writes duplicate counts lines into the merge commit.
  - Slug-only ids: TRACEABILITY and BACKLOG numbers are cited across the code and the docs, so they must stay stable.

## Consequences
- Parallel lanes no longer conflict on these files, provided they add fragments. A plain `git merge` of two
  new-style branches can still conflict on the rendered files. Resolve it by running the script (or use `merge`),
  never by hand.
- A renumbered id is not rewritten where the lane's own text cites it. `fix-ids` and `absorb` print the old id to
  check for.
- The HANDOFF template still holds the 1,400-line log. Cutting it down to the current state is a separate, editorial
  change.
- docs/decisions/ has the same numbering race (two lanes writing ADR 0012). It is rare enough to leave as it is.
