"""BACKLOG 91 follow-up: a run the retired-family guard refuses is a recorded SKIP with its reason, never an error.

`research_pass.py` defaults to `--specialist session_open` (retired R-02), and research.yml / `cells.plan` default to it
too. No scheduled caller relies on that default (research-loop.yml names every family; its `rule_ref=loop` rows are
refused for a retired family by `generator.loop_refusal` before any budget check; the brain's jobs never call
research_pass), but a manual dispatch with the default would hit the guard after the quarter's shared exploration
trial is used. That run must end clean: research_pass writes the reason to the report and exits SKIPPED_EXIT with no
row; the research.yml cell passes; the record job reports it as skipped (no look, no trial, no failed row).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from goldbot.research import cells
from goldbot.research.registry import SKIPPED_EXIT, TrialRegistry, quarter_of

ROOT = Path(__file__).resolve().parents[1]


def _rp() -> ModuleType:
    spec = importlib.util.spec_from_file_location("research_pass_skip", ROOT / "scripts" / "research_pass.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stamped(path: Path, family: str) -> None:
    """A trial of `family` stamped now (this quarter)."""
    reg = TrialRegistry(path)
    reg.record(agent_id=f"{family}-a", family=family, config={"x": 1}, feature_version="v1", rationale="earlier",
               results={})


def test_research_pass_on_the_retired_default_records_a_skip_with_the_reason_not_an_error(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rp = _rp()
    registry, report = tmp_path / "registry.jsonl", tmp_path / "report.md"
    _stamped(registry, "sweep_absorb")                       # the quarter's shared retired-family trial is used
    before = registry.read_text()
    b1 = pd.DataFrame({"ts_utc": pd.to_datetime(["2015-01-02 00:00", "2015-01-02 00:01"], utc=True)})
    monkeypatch.setattr(rp, "load_bars", lambda *a, **k: b1)
    monkeypatch.setattr(sys, "argv", ["research_pass.py", "--bars", str(tmp_path), "--registry", str(registry),
                                      "--report", str(report)])                         # --specialist left to default
    assert rp.main() == SKIPPED_EXIT
    text = report.read_text()
    assert "SKIPPED" in text and "session_open is retired" in text and quarter_of() in text
    assert registry.read_text() == before                      # no look, no trial


def test_the_record_job_reports_a_skipped_cell_as_skipped_with_no_failed_row(tmp_path: Path):
    registry = TrialRegistry(tmp_path / "registry.jsonl")
    plan = cells.plan({"SPECIALIST": "session_open", "VARIANTS": "[{}]", "FROM_YEAR": "2010", "TO_YEAR": "2026"})
    d = tmp_path / "arts"
    d.mkdir()
    (d / "status.json").write_text(json.dumps({"exit": SKIPPED_EXIT}))
    (d / "research.log").write_text("SKIPPED: session_open is retired (research.retired_families) ...")
    (d / "report.md").write_text("## session_open: SKIPPED\n")
    out = cells.record(plan, d, registry.path)
    assert out.skipped == 1 and out.failed == 0 and out.refused == 0 and out.recorded == 0
    assert not registry.path.exists() or registry.path.read_text() == ""
    body = "\n".join(r["body"] for r in out.reports)
    assert "skipped" in body and "session_open is retired" in body


def test_the_research_workflow_cell_passes_on_a_skip():
    wf = (ROOT / ".github" / "workflows" / "research.yml").read_text()
    assert f'if [ "$RC" = "{SKIPPED_EXIT}" ]; then exit 0; fi' in wf


def test_skipped_exit_is_not_a_code_python_or_a_refusal_uses():
    assert SKIPPED_EXIT not in (0, 1, 2)                     # 1: SystemExit("message"), 2: argparse
