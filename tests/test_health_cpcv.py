"""Health check `cpcv_evidence` (goldbot/ops/checks/cpcv.py): every gate-passing trial has CPCV/PBO evidence for the
current quarter, so the promotion gate (research/cpcv_gate.py) never meets a gap by surprise."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from goldbot.ops import health
from goldbot.research import cpcv
from goldbot.research.registry import TrialRegistry
from tests.research.test_cpcv_gate import _attach, _pbo, _result, _trial
from tests.test_health import SETTINGS, make_ctx

NOW = pd.Timestamp("2026-11-14 12:00", tz="UTC")


def _ctx(tmp_path: Path, registry: Path | None = None, **kw):
    settings = SETTINGS.model_copy(update={"research": SETTINGS.research.model_copy(
        update={"registry": str(registry or tmp_path / "trials.jsonl")})})
    return make_ctx(tmp_path, now=NOW, settings=settings, **kw)


def test_no_registry_is_ok(tmp_path):
    c = health.check_cpcv_evidence(_ctx(tmp_path))
    assert c.status == "ok" and "no trial registry" in c.reason


def test_no_gate_passing_trial_is_ok(tmp_path):
    reg = TrialRegistry(tmp_path / "trials.jsonl")
    _trial(reg, passed=False)
    c = health.check_cpcv_evidence(_ctx(tmp_path))
    assert c.status == "ok" and "no gate-passing trial" in c.reason


def test_a_gate_passing_trial_without_cpcv_evidence_this_quarter_warns_and_names_it(tmp_path):
    reg = TrialRegistry(tmp_path / "trials.jsonl")
    row = _trial(reg)
    c = health.check_cpcv_evidence(_ctx(tmp_path))
    assert c.name == "cpcv_evidence" and c.status == "warn"
    assert f"#{row['trial']}" in c.reason and "2026Q4" in c.reason and "research_pass.py --cpcv" in c.reason


def test_failed_cpcv_evidence_warns(tmp_path):
    reg = TrialRegistry(tmp_path / "trials.jsonl")
    row = _trial(reg)
    _attach(reg, row["trial"], {"error": "no candidates in the research window"}, None, when=NOW.to_pydatetime())
    c = health.check_cpcv_evidence(_ctx(tmp_path))
    assert c.status == "warn" and "CPCV failed" in c.reason


def test_clean_evidence_is_ok(tmp_path):
    reg = TrialRegistry(tmp_path / "trials.jsonl")
    row = _trial(reg)
    _attach(reg, row["trial"], _result(), None, when=NOW.to_pydatetime())
    c = health.check_cpcv_evidence(_ctx(tmp_path))
    assert c.status == "ok" and "1 gate-passing trial" in c.reason


def test_a_vetoed_trial_has_its_evidence_and_is_reported_not_flagged(tmp_path):
    reg = TrialRegistry(tmp_path / "trials.jsonl")
    row, _ = _trial(reg), _trial(reg, passed=False, asia_range_max_atr_d=1.0)
    _attach(reg, row["trial"], _result(), _pbo(0.7), when=NOW.to_pydatetime())
    c = health.check_cpcv_evidence(_ctx(tmp_path))
    assert c.status == "ok" and "vetoed" in c.reason and "PBO 70%" in c.reason
    assert reg.evidence(row["trial"], cpcv.EVIDENCE_KIND)            # the check only reads


def test_an_unreadable_registry_is_a_warning_not_a_pass(tmp_path):
    (tmp_path / "trials.jsonl").write_text("{not json\n")
    c = health.check_cpcv_evidence(_ctx(tmp_path))
    assert c.status == "warn" and "can't check" in c.reason


def test_a_relative_registry_path_resolves_beside_the_state_dir(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    reg = TrialRegistry(tmp_path / "reg" / "trials.jsonl")
    _trial(reg)
    settings = SETTINGS.model_copy(update={"research": SETTINGS.research.model_copy(
        update={"registry": "reg/trials.jsonl"})})
    c = health.check_cpcv_evidence(make_ctx(state, now=NOW, settings=settings))
    assert c.status == "warn"
