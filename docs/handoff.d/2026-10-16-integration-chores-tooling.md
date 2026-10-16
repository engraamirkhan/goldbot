- Integration chores scripted (implementer, 2026-10-16; throughput plan lever 4, TD-7, TD-8; ADR 0025 proposed,
  amends 0012). The work comes in four parts:
  - **Rendered-docs merge driver.** `.gitattributes` routes HANDOFF.md, TRACEABILITY.md and BACKLOG.md to the
    `goldbot-docs` driver. It re-renders each file from the fragments merged three-way, so a plain merge, rebase or
    cherry-pick no longer conflicts on them. A fragment edited on both sides, or two lanes taking one id, still
    stops the merge and names the fix.
    **Enable it once per clone:** `python scripts/docs_assemble.py install-merge-driver`.
  - **ADR numbers.** `docs_assemble.py new-adr --slug S --title T` reserves the next number under a lock in the git
    common dir, so parallel worktrees never share a number. It skips `docs/decisions/reserved.yaml`, which holds
    0023-0026 from the CTO register; `--number N` claims a reserved number. `check` fails on two ADRs with one number.
  - **Worktree prune.** `scripts/prune_worktrees.sh` is a dry run by default; `--apply` removes. It handles only
    merged, clean worktrees that have been idle for 24 h, and never the main checkout or a locked, dirty or unmerged
    tree.
  - **Lane dispatch checklist.** `docs/process/lane-checklist.md`.

  Tests: `tests/test_docs_assemble_chores.py`, `tests/test_prune_worktrees.py`.
