"""scripts/ci_changes.py: which CI jobs a pull request needs. Prose-only PRs skip lint, integration and the frontend;
docs/research/ is runtime input (the research director and the API read hypotheses.md), so it counts as code.
Unit tests always run: some read docs (RUNBOOK.md, ADRs, first-deploy.md)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("ci_changes", Path(__file__).resolve().parents[1] / "scripts" / "ci_changes.py")
assert _spec is not None and _spec.loader is not None
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def test_prose_only_changes_are_not_code():
    assert cc.needs_code_jobs(["docs/process/notes.md", "README.md", "docs/process/x.yaml", "web/README.md"]) is False


def test_any_code_or_config_file_needs_the_code_jobs():
    for f in ("goldbot/engine.py", "web/src/App.tsx", "pyproject.toml", ".github/workflows/ci.yml", "config/settings.yaml",
              "scripts/gates.sh", "tests/conftest.py", "HANDOFF.txt"):
        assert cc.needs_code_jobs(["docs/RUNBOOK.md", f]) is True, f


def test_research_docs_are_runtime_input():
    assert cc.needs_code_jobs(["docs/research/hypotheses.md"]) is True


def test_every_doc_that_runtime_integration_or_e2e_code_reads_counts_as_code():
    # audit: every tracked docs/** or *.md file named as a string literal by runtime code (goldbot/, scripts/,
    # web/src, web/e2e) or by an integration-marked test file must not be skippable as prose. Unit tests may read
    # docs freely: the unit suite runs on every PR.
    root = Path(__file__).resolve().parents[1]
    reads = cc.doc_reads(root)
    assert "docs/research/hypotheses.md" in reads and "docs/ROADMAP.md" in reads   # the audit finds known readers
    skippable = {p: who for p, who in reads.items() if cc.is_prose(p)}
    assert not skippable, f"add these to RUNTIME_DOCS in scripts/ci_changes.py: {skippable}"


def test_a_doc_read_only_by_unit_tests_stays_prose():
    assert cc.is_prose("docs/process/lane-checklist.md") and cc.is_prose("docs/ux/header-role.md")


def test_unknown_or_truncated_file_lists_fail_safe_to_running_everything():
    assert cc.needs_code_jobs([]) is True
    assert cc.needs_code_jobs(["docs/a.md"] * cc.PR_FILES_LIMIT) is True
