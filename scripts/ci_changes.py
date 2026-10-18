#!/usr/bin/env python3
"""Which CI jobs a pull request needs: prints `code=true|false` for $GITHUB_OUTPUT.

  ci_changes.py < changed-file-list      (one path per line, e.g. `gh api repos/R/pulls/N/files --paginate --jq '.[].filename'`)

code=false only when every changed file is prose: `docs/**` or `*.md`, except `docs/research/**`, which the research
director and the API read at run time. Prose-only PRs still run smoke (pre-commit, docs checks) and the unit suite
(unit tests read RUNBOOK.md, ADRs and first-deploy.md); they skip lint, integration and the frontend jobs.
Fail-safe: an empty or possibly truncated list means code=true. Pushes to main never use this (they run everything).
Standard library only.
"""
from __future__ import annotations

import sys

PR_FILES_LIMIT = 3000   # GitHub's pulls/{n}/files returns at most 3000 files; a list that long may be truncated
RUNTIME_DOCS = ("docs/research/",)


def is_prose(path: str) -> bool:
    if path.startswith(RUNTIME_DOCS):
        return False
    return path.startswith("docs/") or path.endswith(".md")


def needs_code_jobs(files: list[str]) -> bool:
    if not files or len(files) >= PR_FILES_LIMIT:
        return True
    return not all(is_prose(f) for f in files)


def main() -> int:
    files = [line.strip() for line in sys.stdin if line.strip()]
    code = needs_code_jobs(files)
    print(f"code={'true' if code else 'false'}")
    print(f"ci_changes: {len(files)} changed files, code jobs {'needed' if code else 'skipped (prose only)'}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
