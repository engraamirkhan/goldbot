#!/usr/bin/env python3
"""Which CI jobs a pull request needs: prints `code=true|false` for $GITHUB_OUTPUT.

  ci_changes.py < changed-file-list      (one path per line, e.g. `gh api repos/R/pulls/N/files --paginate --jq '.[].filename'`)

code=false only when every changed file is prose: `docs/**` or `*.md`, except RUNTIME_DOCS, the docs that runtime
code, integration tests or e2e read (audited by `doc_reads`, enforced by tests/test_ci_changes.py). Prose-only PRs
still run smoke (pre-commit, docs checks) and the unit suite (unit tests read RUNBOOK.md, ADRs and first-deploy.md);
they skip lint, integration and the frontend jobs.
Fail-safe: an empty or possibly truncated list means code=true. Pushes to main never use this (they run everything).
CI runs this file from the PR's BASE commit, so a PR cannot edit it to skip its own jobs. Standard library only.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PR_FILES_LIMIT = 3000   # GitHub's pulls/{n}/files returns at most 3000 files; a list that long may be truncated
RUNTIME_DOCS = (
    "docs/research/",          # research director, API explain, preregistration (runtime and integration tests)
    "docs/ROADMAP.md",         # goldbot/telegram/digest.py reads the owner decision queue
    "docs/RUNBOOK.md",         # test_us_calendar.py (a file with integration tests) reads it
)


# not doc readers for this audit: docs_assemble.py is checked by the smoke job on every PR (pre-commit hook);
# ci_changes.py only names paths
NOT_READERS = ("scripts/docs_assemble.py", "scripts/ci_changes.py")


def is_prose(path: str) -> bool:
    if path.startswith(RUNTIME_DOCS):
        return False
    return path.startswith("docs/") or path.endswith(".md")


_LIT = re.compile(r"""["']((?:docs/)[^"'\s{}*]+|[^"'\s/{}*]+\.md)["']""")
_JOIN = re.compile(r"""["']docs["']((?:\s*/\s*["'][^"'{}]+["'])+)""")


def doc_reads(root: Path) -> dict[str, list[str]]:
    """Tracked prose files named as string literals (`"docs/x.md"`, `ROOT / "docs" / "x.md"`, or a basename unique
    among prose files) in runtime code (goldbot/, scripts/, web/src, web/e2e) or in a test file with any
    integration-marked test. Over-inclusive on purpose: a false hit only means a doc edit runs every job."""
    tracked = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    prose = [f for f in tracked if f.startswith("docs/") or f.endswith(".md")]
    by_name: dict[str, list[str]] = {}
    for p in prose:
        by_name.setdefault(Path(p).name, []).append(p)
    out: dict[str, list[str]] = {}
    for f in tracked:
        if f in NOT_READERS:
            continue
        if Path(f).suffix not in (".py", ".ts", ".tsx", ".sh", ".ps1"):
            continue
        text = (root / f).read_text(errors="ignore")
        if f.startswith("tests/") and "mark.integration" not in text:
            continue
        if not f.startswith(("goldbot/", "scripts/", "web/src/", "web/e2e/", "tests/")):
            continue
        for line in text.splitlines():
            if line.lstrip().startswith(("#", "//", "*")):
                continue
            names = [m.group(1) for m in _LIT.finditer(line)]
            names += ["docs/" + "/".join(re.findall(r"""["']([^"']+)["']""", m.group(1))) for m in _JOIN.finditer(line)]
            for n in names:
                targets = [n] if n in prose else by_name.get(n, []) if "/" not in n else []
                for t in targets if len(targets) == 1 else []:
                    out.setdefault(t, []).append(f)
    return out


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
