"""BACKLOG 47: the quarter's pre-registered queued rows and contingent slots are written into the trial registry by one
command (scripts/preregister_quarter.py, goldbot/research/preregistration.py), from a reviewed spec extracted from the
pre-registration. It validates every row against the registry's own rules and the quarter's budget and reservation,
prints the rows on --dry-run, is idempotent, refuses a queued write once the quarter has started (unless --allow-late
with a reason, which writes ad-hoc rows only), and never spends a trial or writes an outcome."""
from __future__ import annotations

import importlib.util
import re
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from goldbot.config import ResearchSettings, load_settings
from goldbot.research import registry as registry_mod
from goldbot.research.director import pending_preregistration, reserved_trials
from goldbot.research.preregistration import (
    LateWrite,
    QuarterSpec,
    SpecError,
    plan_quarter,
    write_quarter,
)
from goldbot.research.registry import TrialRegistry, config_hash, quarter_trials
from goldbot.specialists import SPECIALISTS

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/research/preregistration-2027Q1.md"
SPEC = ROOT / "docs/research/preregistration-2027Q1.yaml"
Q = "2027Q1"

READING = "**continue** if gross t >= 2.0 on >= 1,000 events AND net mean R > 0."
DERIVATION = "slot k is live only if the discovery's verdict is *continue* and at least k clusters survive"
DOC_TEXT = f"# test pre-registration\n\n- Reading rule: {READING}\n- discovery rule: survivors only\n- slot: {DERIVATION}\n"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("preregister_quarter", ROOT / "scripts/preregister_quarter.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _clock(monkeypatch: pytest.MonkeyPatch, when: datetime) -> None:
    monkeypatch.setattr(registry_mod, "_utcnow", lambda: when)


BEFORE = datetime(2026, 12, 20, 12, tzinfo=timezone.utc)
AFTER = datetime(2027, 1, 1, 0, tzinfo=timezone.utc)


def _research(reserved: int, budget: int = 20) -> ResearchSettings:
    r = load_settings().research
    return r.model_copy(update={"reserved_trials_quarter": {Q: reserved}, "trial_budget_quarter": budget})


def _complete_spec(**over: Any) -> dict[str, Any]:
    """A writable spec: one discovery parent, one rule row, a withdrawn row, two survivor slots on the parent."""
    tsmom_slow = SPECIALISTS["tsmom"](**SPECIALISTS["tsmom"].presets["slow"])
    asia = SPECIALISTS["asia_drift"]()
    spec: dict[str, Any] = {
        "quarter": Q, "source": "doc.md", "freeze_commit": "abc1234", "reservation": 4,
        "rows": [
            {"id": "D", "kind": "discovery", "specialist": "tsmom", "variant": {"timeframe": "4h", "max_bars": 12},
             "discovery": {"survivor_unit": "feature", "group_top_k": 3, "clusters": "H02_CLUSTERS"},
             "group_mode": "cluster", "macro": True, "bars_from": "2010-01-03 23:00:00+00:00",
             "bars_to": "2025-09-30 20:59:00+00:00", "feature_version": "f-51ac5ebabb",
             "rationale": "D discovery (doc.md, commit {commit})", "reading_rule": "survivors only"},
            {"id": "H-01", "specialist": "tsmom", "variant": "slow", "agent_id": tsmom_slow.agent_id,
             "config_hash": config_hash(tsmom_slow.config), "feature_version": "f-079deacc61", "rule_id": "H-01",
             "rationale": "H-01 (doc.md, commit {commit})", "reading_rule": READING},
            {"id": "H-04", "specialist": "asia_drift", "variant": {}, "agent_id": asia.agent_id,
             "config_hash": config_hash(asia.config), "feature_version": "f-079deacc61", "rule_id": "H-04",
             "rationale": "H-04 (doc.md, commit {commit})", "reading_rule": READING, "withdrawn": "test: dropped"},
        ],
        "slots": [{"parent": "D", "kind": "survivor", "k": [1, 2], "survivor_pool": ["H-03", "H-05"],
                   "rule": {"min_stability": 0.6, "min_auc": 0.53, "min_auc_lower_bound": 0.5},
                   "derivation_rule": DERIVATION}],
    }
    spec.update(over)
    return spec


def _plan(spec: dict[str, Any], reserved: int = 4, budget: int = 20) -> Any:
    return plan_quarter(QuarterSpec.model_validate(spec), research=_research(reserved, budget), doc_text=DOC_TEXT,
                        quarter=Q)


# ---------------------------------------------------------------------------------------------- the doc's spec
def _doc_rows() -> dict[str, dict[str, str]]:
    """The doc's "Queued registry rows" table: row id -> rule_id cell, feature version, agent id (when named)."""
    text = DOC.read_text()
    table = text.split("**1. Queued registry rows**", 1)[1].split("**2. Reserved slot rows.**", 1)[0]
    out: dict[str, dict[str, str]] = {}
    for line in table.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6 or cells[0] in ("Row", "") or set(cells[0]) <= {"-"}:
            continue
        rid = "C4H" if cells[0].startswith("tsmom 4h") else cells[0]
        agent = re.search(r"agent `([^`]+)`", cells[1])
        rule = re.search(r"\*\*`([A-Z0-9-]+)`\*\*", cells[2])
        out[rid] = {"rule_id": rule.group(1) if rule else "", "feature_version": re.findall(r"f-[0-9a-f]{10}", cells[3])[0],
                    "agent_id": agent.group(1) if agent else ""}
    return out


def test_the_dry_run_prints_exactly_the_docs_queued_rows_and_survivor_slots_and_writes_nothing(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, BEFORE)
    reg_path = tmp_path / "registry.jsonl"
    assert _script().main(["--quarter", Q, "--from", str(SPEC), "--registry", str(reg_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    plan = plan_quarter(QuarterSpec.model_validate(yaml.safe_load(SPEC.read_text())), research=load_settings().research,
                        doc_text=DOC.read_text(), quarter=Q)
    doc = _doc_rows()
    assert [r.id for r in plan.rows] == ["H-02", "H-01", "H-06", "H-04", "C4H"] and set(doc) == {r.id for r in plan.rows}
    for r in plan.rows:
        assert (r.rule_id or "") == doc[r.id]["rule_id"], r.id
        assert r.feature_version == doc[r.id]["feature_version"], r.id
        if doc[r.id]["agent_id"]:
            assert r.agent_id == doc[r.id]["agent_id"], r.id
        assert r.id in out and r.feature_version in out
    assert {r.id for r in plan.rows if r.pending} == {"H-02", "H-06"}       # open items the doc schedules
    # table 2: four survivor slots on H-02's row, the doc's pool and slot rule, reservation 9 = settings
    assert [(s.parent, s.kind, s.k) for s in plan.slots] == [("H-02", "survivor", k) for k in (1, 2, 3, 4)]
    assert all(s.survivor_pool == ["H-03", "H-05", "H-07", "H-08", "H-10"] for s in plan.slots)
    assert all(s.rule == {"min_stability": 0.6, "min_auc": 0.53, "min_auc_lower_bound": 0.5} for s in plan.slots)
    assert '`{"2027Q1": 9}` (5 queued + 4 slots)' in DOC.read_text() and plan.reservation == 9
    assert load_settings().research.reserved_for(Q) == 9
    assert out.count("survivor slot") == 4 and "not writable" in out
    assert reg_path.read_text() == ""


def test_the_q1_spec_is_not_writable_while_h02_h06_or_the_freeze_commit_are_open(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, BEFORE)
    reg_path = tmp_path / "registry.jsonl"
    with pytest.raises(SystemExit) as exc:
        _script().main(["--quarter", Q, "--from", str(SPEC), "--registry", str(reg_path)])
    msg = str(exc.value)
    assert "H-02" in msg and "H-06" in msg and "freeze_commit" in msg
    assert reg_path.read_text() == ""


# ---------------------------------------------------------------------------------------------- write path
def test_writing_twice_adds_nothing_the_second_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, BEFORE)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    plan = _plan(_complete_spec(reservation=4), reserved=4)
    first = write_quarter(reg, plan)
    assert [a.action for a in first] == ["written"] * 4            # D, H-01, slot 1, slot 2 (H-04 withdrawn)
    rows_after_first = reg.path.read_text()
    second = write_quarter(reg, plan)
    assert [a.action for a in second] == ["present"] * 4
    assert reg.path.read_text() == rows_after_first


def test_a_partly_written_quarter_is_completed_without_duplicating_rows(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, BEFORE)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    plan = _plan(_complete_spec(reservation=4), reserved=4)
    parent = plan.rows[0]
    reg.preregister(agent_id=parent.agent_id or "", family=parent.family, config=parent.config or {},
                    feature_version=parent.feature_version, rationale=parent.rationale,
                    reading_rule=parent.reading_rule, queue=True, target_quarter=Q)
    actions = write_quarter(reg, plan)
    assert [(a.id, a.action) for a in actions] == [("D", "present"), ("H-01", "written"), ("D#1", "written"),
                                                   ("D#2", "written")]
    assert len(reg._rows()) == 4


def test_a_written_row_is_the_one_the_runners_budget_check_matches(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, BEFORE)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    plan = _plan(_complete_spec(reservation=4), reserved=4)
    write_quarter(reg, plan)
    spec = SPECIALISTS["tsmom"](**SPECIALISTS["tsmom"].presets["slow"])       # research_pass.py --variants '["slow"]'
    in_quarter = datetime(2027, 1, 5, tzinfo=timezone.utc)
    assert pending_preregistration(reg._rows(), Q, "tsmom", config_hash(spec.config)) is not None
    _, (prereg,) = reg.check_budget_reserved([("tsmom", spec.config)], 20, 4, now=in_quarter, retired=set())
    assert prereg is not None and prereg["rule_id"] == "H-01" and prereg["target_quarter"] == Q


def test_writing_spends_no_trial_and_records_no_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, BEFORE)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    write_quarter(reg, _plan(_complete_spec(reservation=4), reserved=4))
    rows = reg._rows()
    assert reg.n_trials == 0 and reg.n_trials_effective == 0
    assert quarter_trials(rows, Q) == 0 and quarter_trials(rows, "2026Q4") == 0
    assert all(r["status"] == "preregistered" and r["results"] == {} for r in rows)
    res = reserved_trials(rows, Q, 4)
    assert (res.run, res.pending, res.reserved) == (0, 4, 4)
    assert [r["slot"]["k"] for r in rows if r.get("slot")] == [1, 2]
    assert all(r["slot"]["parent"]["ts"] == rows[0]["ts"] for r in rows if r.get("slot"))


def test_the_freeze_commit_is_written_into_every_rows_rationale(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, BEFORE)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    write_quarter(reg, _plan(_complete_spec(reservation=4), reserved=4))
    rows = [r for r in reg._rows() if not r.get("slot")]
    assert rows and all("commit abc1234" in r["rationale"] and "{commit}" not in r["rationale"] for r in rows)
    assert all(r["plan"]["freeze_commit"] == "abc1234" and r["plan"]["quarter"] == Q for r in rows)


def test_a_queued_row_of_the_quarter_that_the_spec_does_not_name_is_refused(tmp_path: Path,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, BEFORE)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    reg.preregister(agent_id="x", family="session_open", config={"a": 1}, feature_version="f-079deacc61",
                    rationale="stray", reading_rule="r", queue=True, target_quarter=Q)
    with pytest.raises(SpecError, match="not in the spec"):
        write_quarter(reg, _plan(_complete_spec(reservation=4), reserved=4))
    assert len(reg._rows()) == 1


# ---------------------------------------------------------------------------------------------- budget, reservation
def test_a_reservation_that_differs_from_the_settings_is_refused() -> None:
    with pytest.raises(SpecError, match="reserved_trials_quarter"):
        _plan(_complete_spec(reservation=4), reserved=9)


def test_a_reservation_that_differs_from_the_rows_and_slots_is_refused() -> None:
    with pytest.raises(SpecError, match="rows \\+ slots"):
        _plan(_complete_spec(reservation=5), reserved=5)          # 3 rows + 2 slots counts the withdrawn H-04


def test_rows_and_slots_over_the_quarters_trial_budget_are_refused() -> None:
    with pytest.raises(SpecError, match="trial budget"):
        _plan(_complete_spec(reservation=4), reserved=4, budget=2)


def test_withdrawing_h06_leaves_eight_reserved_and_nine_is_then_refused() -> None:
    raw = yaml.safe_load(SPEC.read_text())
    h06 = next(r for r in raw["rows"] if r["id"] == "H-06")
    h06["withdrawn"] = "not merged by 2026-12-04 (BACKLOG 48)"
    with pytest.raises(SpecError, match="rows \\+ slots"):
        plan_quarter(QuarterSpec.model_validate(raw), research=_research(9), doc_text=DOC.read_text(), quarter=Q)
    plan = plan_quarter(QuarterSpec.model_validate({**raw, "reservation": 8}), research=_research(8),
                        doc_text=DOC.read_text(), quarter=Q)
    assert "H-06" not in [r.id for r in plan.active_rows] and len(plan.active_rows) + len(plan.slots) == 8


# ---------------------------------------------------------------------------------------------- late writes
def test_writing_on_or_after_the_quarters_start_is_refused_without_allow_late(tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, AFTER)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    with pytest.raises(LateWrite, match="2027Q1 started"):
        write_quarter(reg, _plan(_complete_spec(reservation=4), reserved=4))
    with pytest.raises(LateWrite, match="reason"):
        write_quarter(reg, _plan(_complete_spec(reservation=4), reserved=4), allow_late="  ")
    assert reg._rows() == []


def test_allow_late_writes_ad_hoc_rows_naming_their_rule_and_no_slots(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, AFTER)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    actions = write_quarter(reg, _plan(_complete_spec(reservation=4), reserved=4), allow_late="freeze slipped a day")
    assert [a.action for a in actions] == ["written", "written", "skipped", "skipped"]
    rows = reg._rows()
    assert all(r["queue"] is False and r["rule_ref"].startswith("doc.md#") for r in rows)
    assert all(r["plan"]["late_reason"] == "freeze slipped a day" for r in rows)
    assert reserved_trials(rows, Q, 4).pending == 0           # an ad-hoc row holds no reservation
    assert [a.action for a in write_quarter(reg, _plan(_complete_spec(reservation=4), reserved=4),
                                            allow_late="again")] == ["present", "present", "skipped", "skipped"]


def test_the_script_refuses_a_late_write_and_takes_allow_late_with_a_reason(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, AFTER)
    doc = tmp_path / "doc.md"
    doc.write_text(DOC_TEXT)
    spec = tmp_path / "spec.yaml"
    spec.write_text(yaml.safe_dump(_complete_spec(reservation=4, source=str(doc))))
    reg_path = tmp_path / "registry.jsonl"
    script = _script()
    settings = load_settings().model_copy(update={"research": _research(4)})
    monkeypatch.setattr(script, "load_settings", lambda: settings)
    with pytest.raises(SystemExit, match="started"):
        script.main(["--quarter", Q, "--from", str(spec), "--registry", str(reg_path)])
    assert script.main(["--quarter", Q, "--from", str(spec), "--registry", str(reg_path),
                        "--allow-late", "freeze slipped"]) == 0
    assert len(TrialRegistry(reg_path)._rows()) == 2


# ---------------------------------------------------------------------------------------------- malformed rows
@pytest.mark.parametrize("mutate, match", [
    (lambda s: s["rows"][1].update(colour="red"), "colour"),                          # unknown field
    (lambda s: s["rows"][1].update(rule_id="H-99"), "rule_id"),                       # not a coded rule
    (lambda s: s["rows"][1].update(feature_version="f-xyz"), "feature_version"),      # malformed version
    (lambda s: s["slots"][0].update(kind="followup"), "kind"),                        # Q1 writes survivor slots only
    (lambda s: s["slots"][0].update(k=[1, 1]), "k"),                                  # duplicate slot index
    (lambda s: s["rows"][1].update(rationale="H-01 without the commit"), "commit"),   # freeze commit not in it
    (lambda s: s.update(freeze_commit="not-a-sha"), "freeze_commit"),
])
def test_a_malformed_row_is_refused_by_the_spec_schema(mutate: Any, match: str) -> None:
    s = _complete_spec(reservation=4)
    mutate(s)
    with pytest.raises(ValidationError, match=match):
        QuarterSpec.model_validate(s)


@pytest.mark.parametrize("mutate, match", [
    (lambda s: s["rows"][1].update(config_hash="000000000000"), "config_hash"),       # not what the runner hashes
    (lambda s: s["rows"][1].update(agent_id="tsmom-g0-0000000000"), "agent_id"),
    (lambda s: s["rows"][1].update(feature_version="f-51ac5ebabb"), "feature version"),  # macro off: default version
    (lambda s: s["rows"][1].update(reading_rule="a rule the doc never wrote"), "word for word"),
    (lambda s: s["slots"][0].update(derivation_rule="another rule"), "word for word"),
    (lambda s: s["slots"][0].update(parent="H-01"), "discovery"),                     # a survivor slot's parent
    (lambda s: s["slots"][0].update(parent="NOPE"), "no row"),
    (lambda s: s["rows"][1].update(variant="fast"), "preset"),                       # unknown preset
    (lambda s: s["rows"][1].update(variant={"lb_typo": 3}), "settings"),             # a typo is not run as defaults
    (lambda s: s["slots"][0].update(k=[1, 2, 3]), "survivor_pool"),                 # more slots than pooled clusters
    (lambda s: s.update(quarter="2027Q2"), "quarter"),
])
def test_a_row_the_registry_or_the_doc_would_not_accept_is_refused(mutate: Any, match: str) -> None:
    s = _complete_spec(reservation=4)
    mutate(s)
    with pytest.raises(SpecError, match=match):
        _plan(s, reserved=4)


def test_a_pending_row_may_be_planned_but_never_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, BEFORE)
    s = _complete_spec(reservation=4)
    s["rows"][1].update(variant=None, config_hash=None, agent_id=None, pending="preset not merged")
    plan = _plan(s, reserved=4)
    assert not plan.writable and "H-01" in plan.blockers[0]
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    with pytest.raises(SpecError, match="not writable"):
        write_quarter(reg, plan)
    assert reg._rows() == []


# ---------------------------------------------------------------------------------------------- the workflow
def test_the_workflow_dry_runs_read_only_and_writes_only_after_merging_the_release_copy() -> None:
    raw = (ROOT / ".github/workflows/preregister-quarter.yml").read_text()
    wf = yaml.safe_load(raw)
    assert re.search(r"^permissions: \{\}", raw, re.M) and "persist-credentials: false" in raw
    for use in re.findall(r"uses: (\S+)", raw):
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", use), use                # pinned to a commit
    jobs = wf["jobs"]
    assert jobs["dry-run"]["permissions"] == {"contents": "read"} and jobs["dry-run"]["if"] == "${{ inputs.dry_run }}"
    assert jobs["write"]["permissions"] == {"contents": "write"} and jobs["write"]["if"] == "${{ !inputs.dry_run }}"
    assert wf["concurrency"]["group"] == "research-registry"                  # one registry writer, as research.yml
    write = "\n".join(s.get("run", "") for s in jobs["write"]["steps"])
    assert write.index("preregister_quarter.py") < write.index("registry_sync merge") < write.index("release upload")
    assert "${{" not in write                                                  # inputs reach the shell only via env
