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
DISCOVERY_RULE = "(decided now): survivors only"
DOC_TEXT = (f"# test pre-registration\n\n- Reading rule: {READING}\n- Reading rule {DISCOVERY_RULE}\n"
            f"- slot: {DERIVATION}\n")


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
             "rationale": "D discovery (doc.md, commit {commit})", "reading_rule": DISCOVERY_RULE},
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


DIGESTS = {f"xauusd_1m_dukascopy_{y}.parquet": f"{y:064x}" for y in range(2010, 2026)}   # BACKLOG 50 pins


def _plan(spec: dict[str, Any], reserved: int = 4, budget: int = 20, digests: dict[str, str] | None = None) -> Any:
    return plan_quarter(QuarterSpec.model_validate(spec), research=_research(reserved, budget), doc_text=DOC_TEXT,
                        quarter=Q, data_digests=DIGESTS if digests is None else digests)


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
    assert {r.id for r in plan.active_rows if r.pending} == {"H-02"}         # open items the doc schedules
    assert [r.id for r in plan.rows if r.withdrawn] == ["H-06"] and "107" in str(plan.rows[2].withdrawn)
    assert "H-06  [withdrawn: " in out                                      # printed, never written
    # table 2: four survivor slots on H-02's row, the doc's pool and slot rule, reservation 8 = settings (H-06 out)
    assert [(s.parent, s.kind, s.k) for s in plan.slots] == [("H-02", "survivor", k) for k in (1, 2, 3, 4)]
    assert all(s.survivor_pool == ["H-03", "H-05", "H-07", "H-08", "H-10"] for s in plan.slots)
    assert all(s.rule == {"min_stability": 0.6, "min_auc": 0.53, "min_auc_lower_bound": 0.5} for s in plan.slots)
    assert '`{"2027Q1": 8}` (4 queued + 4 slots)' in DOC.read_text() and plan.reservation == 8
    assert len(plan.active_rows) + len(plan.slots) == 8 and load_settings().research.reserved_for(Q) == 8
    assert out.count("survivor slot") == 4 and "not writable" in out
    assert not reg_path.exists()                                              # a dry run creates no registry file


def test_the_q1_spec_is_not_writable_while_h02_or_the_freeze_commit_are_open(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, BEFORE)
    reg_path = tmp_path / "registry.jsonl"
    with pytest.raises(SystemExit) as exc:
        _script().main(["--quarter", Q, "--from", str(SPEC), "--registry", str(reg_path)])
    msg = str(exc.value)
    assert "H-02" in msg and "freeze_commit" in msg
    assert "H-06" not in msg                                     # withdrawn by its rule: it owes nothing
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
def test_a_reservation_that_differs_from_the_settings_is_refused(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, BEFORE)
    plan = _plan(_complete_spec(reservation=4), reserved=9)
    assert not plan.writable and any("reserved_trials_quarter" in b for b in plan.blockers)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    with pytest.raises(SpecError, match="reserved_trials_quarter"):
        write_quarter(reg, plan)
    assert reg._rows() == []


def test_a_reservation_that_differs_from_the_rows_and_slots_is_refused() -> None:
    with pytest.raises(SpecError, match="rows \\+ slots"):
        _plan(_complete_spec(reservation=5), reserved=5)          # 3 rows + 2 slots counts the withdrawn H-04


def test_rows_and_slots_over_the_quarters_trial_budget_are_refused() -> None:
    with pytest.raises(SpecError, match="trial budget"):
        _plan(_complete_spec(reservation=4), reserved=4, budget=2)


def test_the_withdrawn_h06_leaves_eight_reserved_and_nine_is_then_refused() -> None:
    raw = yaml.safe_load(SPEC.read_text())
    h06 = next(r for r in raw["rows"] if r["id"] == "H-06")
    assert raw["reservation"] == 8 and "38931951849" in h06["withdrawn"] and "pending" not in h06
    plan = plan_quarter(QuarterSpec.model_validate(raw), research=_research(8), doc_text=DOC.read_text(), quarter=Q)
    assert "H-06" not in [r.id for r in plan.active_rows] and len(plan.active_rows) + len(plan.slots) == 8
    with pytest.raises(SpecError, match="rows \\+ slots"):                 # the old 9 no longer matches the rows
        plan_quarter(QuarterSpec.model_validate({**raw, "reservation": 9}), research=_research(9),
                     doc_text=DOC.read_text(), quarter=Q)
    back = {**raw, "rows": [{k: v for k, v in r.items() if k != "withdrawn"} for r in raw["rows"]]}
    with pytest.raises(SpecError, match="rows \\+ slots"):                 # nor does 8 with H-06 back in
        plan_quarter(QuarterSpec.model_validate(back), research=_research(8), doc_text=DOC.read_text(), quarter=Q)
    # the settings must move with the spec: a settings reservation of 9 against the spec's 8 blocks every write
    assert any("reserved_trials_quarter holds 9" in b for b in
               plan_quarter(QuarterSpec.model_validate(raw), research=_research(9), doc_text=DOC.read_text(),
                            quarter=Q).blockers)


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
    late = _plan(_complete_spec(reservation=4), reserved=0)       # the settings change ships with a late write (M1)
    actions = write_quarter(reg, late, allow_late="freeze slipped a day")
    assert [a.action for a in actions] == ["written", "written", "skipped", "skipped"]
    rows = reg._rows()
    assert all(r["queue"] is False and r["rule_ref"].startswith("doc.md#") for r in rows)
    assert all(r["plan"]["late_reason"] == "freeze slipped a day" for r in rows)
    assert reserved_trials(rows, Q, 0).pending == 0           # an ad-hoc row holds no reservation
    assert [a.action for a in write_quarter(reg, late, allow_late="again")] == ["present", "present", "skipped",
                                                                                 "skipped"]


def test_the_script_refuses_a_late_write_and_takes_allow_late_with_a_reason(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, AFTER)
    doc = tmp_path / "doc.md"
    doc.write_text(DOC_TEXT)
    spec = tmp_path / "spec.yaml"
    spec.write_text(yaml.safe_dump(_complete_spec(reservation=4, source=str(doc))))
    reg_path = tmp_path / "registry.jsonl"
    script = _script()
    settings = load_settings().model_copy(update={"research": _research(0)})
    monkeypatch.setattr(script, "load_settings", lambda: settings)
    monkeypatch.setattr(script, "doc_text", lambda source, commit: DOC_TEXT)
    bars = tmp_path / "raw"
    bars.mkdir()
    (bars / "xauusd_1m_dukascopy_2010.parquet").write_bytes(b"bars")
    with pytest.raises(SystemExit, match="started"):
        script.main(["--quarter", Q, "--from", str(spec), "--registry", str(reg_path), "--bars", str(bars)])
    assert script.main(["--quarter", Q, "--from", str(spec), "--registry", str(reg_path), "--bars", str(bars),
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
    assert jobs["write"]["permissions"] == {"contents": "write"} and "!inputs.dry_run" in jobs["write"]["if"]
    write = "\n".join(s.get("run", "") for s in jobs["write"]["steps"])
    assert write.index("registry_release.sh fetch registry.jsonl") < write.index("preregister_quarter.py") \
        < write.index("registry_release.sh save registry.jsonl")             # fetch, write, merge-and-upload
    assert "${{" not in write                                                  # inputs reach the shell only via env


# ---------------------------------------------------------------------------------------------- quant review M1-M5
def test_allow_late_is_refused_while_the_settings_still_reserve_trials_for_the_queue(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """M1: a late write leaves no queued row, so a reservation left in the settings would hold trials nothing can use."""
    _clock(monkeypatch, AFTER)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    with pytest.raises(LateWrite, match="reserved_trials_quarter"):
        write_quarter(reg, _plan(_complete_spec(reservation=4), reserved=4), allow_late="freeze slipped")
    assert reg._rows() == []


def test_allow_late_accepts_a_reservation_equal_to_the_queued_rows_already_written(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, BEFORE)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    parent = _plan(_complete_spec(reservation=4), reserved=4).rows[0]
    reg.preregister(agent_id=parent.agent_id or "", family=parent.family, config=parent.config or {},
                    feature_version=parent.feature_version, rationale=parent.rationale,
                    reading_rule=parent.reading_rule, queue=True, target_quarter=Q)
    _clock(monkeypatch, AFTER)
    with pytest.raises(LateWrite, match="reserved_trials_quarter"):
        write_quarter(reg, _plan(_complete_spec(reservation=4), reserved=0), allow_late="late")
    actions = write_quarter(reg, _plan(_complete_spec(reservation=4), reserved=1), allow_late="late")
    assert [(a.id, a.action) for a in actions] == [("D", "present"), ("H-01", "written"), ("D#1", "skipped"),
                                                   ("D#2", "skipped")]


def test_the_doc_says_a_late_write_loses_the_survivor_slots_to_the_open_budget() -> None:
    text = " ".join(DOC.read_text().split())
    assert "rows 6-9 are lost" in text and "open budget" in text and "--allow-late" in text


def test_a_withdrawn_row_dropped_from_the_spec_does_not_block_the_next_run(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """M2 recovery path: write, withdraw H-01, drop it from the spec (reservation and settings one lower), re-run."""
    _clock(monkeypatch, BEFORE)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    write_quarter(reg, _plan(_complete_spec(reservation=4), reserved=4))
    h01 = next(r for r in reg._rows() if r.get("rule_id") == "H-01")
    reg.withdraw(h01, "dropped before the freeze")
    spec = _complete_spec(reservation=3)
    spec["rows"] = [r for r in spec["rows"] if r["id"] != "H-01"]
    actions = write_quarter(reg, _plan(spec, reserved=3))
    assert [a.action for a in actions] == ["present", "present", "present"]
    assert reserved_trials(reg._rows(), Q, 3).pending == 3


def test_slots_of_a_withdrawn_parent_do_not_block_a_spec_without_them(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, BEFORE)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    write_quarter(reg, _plan(_complete_spec(reservation=4), reserved=4))
    reg.withdraw(reg._rows()[0], "discovery dropped")                     # the parent D; its slots go with it
    spec = _complete_spec(reservation=1, slots=[])
    spec["rows"] = [r for r in spec["rows"] if r["id"] != "D"]
    assert [a.action for a in write_quarter(reg, _plan(spec, reserved=1))] == ["present"]


def test_no_slot_is_reserved_on_a_withdrawn_parent_and_nothing_is_written(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(monkeypatch, BEFORE)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    parent = _plan(_complete_spec(reservation=4), reserved=4).rows[0]
    row = reg.preregister(agent_id=parent.agent_id or "", family=parent.family, config=parent.config or {},
                          feature_version=parent.feature_version, rationale=parent.rationale,
                          reading_rule=parent.reading_rule, queue=True, target_quarter=Q)
    reg.withdraw(row, "dropped")
    with pytest.raises(SpecError, match="withdrawn"):
        write_quarter(reg, _plan(_complete_spec(reservation=4), reserved=4))
    assert len(reg._rows()) == 2                                          # H-01 not written either


def test_a_reading_rule_must_be_the_whole_bullet_not_a_part_of_it() -> None:
    """M3: the rule is everything after "Reading rule" to the end of its bullet (a qualifier in brackets included)."""
    raw = yaml.safe_load(SPEC.read_text())
    h01 = next(r for r in raw["rows"] if r["id"] == "H-01")
    full = " ".join(h01["reading_rule"].split())
    assert full.startswith("(outcomes below written 2026-10-10") and \
        "net tests on rule-only net R over every candidate, see *Net tests*" in full
    h01["reading_rule"] = full.split(" **Stop slow trend")[0]              # the first sentence only
    with pytest.raises(SpecError, match="whole"):
        plan_quarter(QuarterSpec.model_validate(raw), research=_research(9), doc_text=DOC.read_text(), quarter=Q)
    s = _complete_spec(reservation=4)
    s["rows"][0]["reading_rule"] = "survivors only"                       # its bracketed qualifier left out
    with pytest.raises(SpecError, match="whole"):
        _plan(s, reserved=4)


@pytest.mark.parametrize("field, value, match", [
    ("bars_from", "2010-01-03", "bars_from"),                              # not the timestamp's own text
    ("bars_to", "2025-09-30T20:59:00+00:00", "bars_to"),                   # ISO 'T': the run writes str(Timestamp)
    ("bars_to", "2025-09-30 20:59:00", "UTC"),                             # naive
    ("bars_to", "2025-09-30 21:59:00+01:00", "UTC"),                       # another offset
])
def test_bars_from_and_to_must_be_the_runs_own_utc_timestamp_text(field: str, value: str, match: str) -> None:
    """M4: discovery_pass writes str(b1["ts_utc"].iloc[0]) (UTC); any other spelling would never match the run."""
    s = _complete_spec(reservation=4)
    s["rows"][0][field] = value
    with pytest.raises(ValidationError, match=match):
        QuarterSpec.model_validate(s)


class _Linked(Exception):
    """Raised right after `--discover`'s budget check, so the test stops before any data is read."""


def test_a_written_discovery_row_is_the_one_research_pass_discover_links(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """M5: the CLI's --discover-config JSON, --families cluster, ctx={"macro": df} and a b1 whose first and last
    ts_utc are the spec's bars_from/bars_to build the hash of the written row, and check_budget_reserved links it."""
    import argparse
    import json

    import pandas as pd

    from goldbot.research import director
    from goldbot.research.discovery import H02_CLUSTERS
    _clock(monkeypatch, BEFORE)
    from goldbot.data.release import bar_file_digests
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    bars = tmp_path / "raw"
    bars.mkdir()
    for y in range(2010, 2027):
        (bars / f"xauusd_1m_dukascopy_{y}.parquet").write_bytes(f"bars of {y}".encode())
    write_quarter(reg, _plan(_complete_spec(reservation=4), reserved=4, digests=bar_file_digests(bars, to_year=2025)))
    written = reg._rows()[0]
    rp = importlib.util.spec_from_file_location("research_pass_m5", ROOT / "scripts/research_pass.py")
    assert rp is not None and rp.loader is not None
    research_pass = importlib.util.module_from_spec(rp)
    rp.loader.exec_module(research_pass)
    ap = argparse.ArgumentParser()
    research_pass._discovery_args(ap)
    args = ap.parse_args(["--discover", "--families", "cluster", "--discover-config",
                          json.dumps({"survivor_unit": "feature", "group_top_k": 3, "clusters": H02_CLUSTERS})])
    args.specialist, args.pooled, args.score_holdout = "tsmom", "", False
    args.rationale, args.rule_ref, args.report = "H-02", None, str(tmp_path / "r.md")
    args.bars, args.from_year, args.to_year = str(bars), 2010, 2100
    variants = research_pass.parse_variants("tsmom", '[{"timeframe": "4h", "max_bars": 12}]')
    b1 = pd.DataFrame({"ts_utc": pd.to_datetime(["2010-01-03 23:00:00", "2017-06-01 00:00:00",
                                                 "2025-09-30 20:59:00"], utc=True)})
    in_quarter = datetime(2027, 1, 5, tzinfo=timezone.utc)
    real_quarter_of = registry_mod.quarter_of
    monkeypatch.setattr(registry_mod, "quarter_of", lambda ts=None: real_quarter_of(ts or in_quarter))
    monkeypatch.setattr(research_pass, "quarter_of", lambda ts=None: real_quarter_of(ts or in_quarter))
    seen: dict[str, Any] = {}

    def stop(prereg: dict[str, Any] | None, rule_ref: str | None, *, capped: bool) -> str | None:
        seen["prereg"] = prereg
        raise _Linked

    monkeypatch.setattr(director, "cited_rule", stop)
    with pytest.raises(_Linked):
        research_pass._discover(args, variants, b1, reg, load_settings(), 0.37, None,
                                load_settings().research.holdout_window(), "priors",
                                pd.DataFrame({"present": [1.0]}))
    assert seen["prereg"] is not None and seen["prereg"]["ts"] == written["ts"]
    assert seen["prereg"]["config_hash"] == written["config_hash"] and seen["prereg"]["target_quarter"] == Q


# ---------------------------------------------------------------------------------------------- F3, F4
def test_the_rules_are_read_from_the_pre_registration_at_its_freeze_commit() -> None:
    import subprocess
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    script = _script()
    assert "Pre-registration" in script.doc_text("docs/research/preregistration-2027Q1.md", head)
    with pytest.raises(SystemExit, match="freeze_commit"):
        script.doc_text("docs/research/preregistration-2027Q1.md", "0" * 40)
    with pytest.raises(SystemExit, match="freeze_commit"):
        script.doc_text("docs/research/no-such-file.md", head)


def test_the_spec_carries_h02s_full_pinned_discovery_config_with_the_cluster_map_written_out() -> None:
    """preregistration-2027Q1.md "Pinned discovery config": the full DiscoveryConfig.model_dump(), which the spec and
    the queued row carry verbatim, clusters written out as the full map. Naming the map or writing it out builds the
    same config and hash, and the spec still plans (the other rows' pinned hashes unchanged)."""
    from goldbot.research.discovery import H02_CLUSTERS, DiscoveryConfig
    from goldbot.research.preregistration import SpecRow, _discovery_config
    block = DOC.read_text().split("**Pinned discovery config.**", 1)[1].split("```yaml", 1)[1].split("```", 1)[0]
    pinned = yaml.safe_load(block)
    assert pinned["clusters"] == "discovery.H02_CLUSTERS"
    assert pinned["full_coverage_columns"] == ["macro_spx_dd20"]       # pinned 2026-10-17 (BACKLOG 88)
    # the coded dump returns a tuple where the doc and the YAML hold a list: compared alike
    want = {**pinned, "clusters": H02_CLUSTERS, "full_coverage_columns": tuple(pinned["full_coverage_columns"])}
    spec = yaml.safe_load(SPEC.read_text())
    h02 = next(r for r in spec["rows"] if r["id"] == "H-02")
    assert {**h02["discovery"], "full_coverage_columns": tuple(h02["discovery"]["full_coverage_columns"])} == want
    assert DiscoveryConfig(**h02["discovery"]).model_dump() == want      # the whole dump: no default left implicit
    research = load_settings().research
    plan = plan_quarter(QuarterSpec.model_validate(spec), research=research, doc_text=DOC.read_text(), quarter=Q)
    assert {r.id: r.config_hash for r in plan.rows if r.config_hash} == {
        "H-01": "561030150146", "H-06": "5650ad206bda", "H-04": "3d33124f210c", "C4H": "184bddf12019"}
    # with the doc's variant and bars range the full map and the named map hash the same
    tsmom = SPECIALISTS["tsmom"](timeframe="4h", max_bars=12)
    base = {**h02, "variant": {"timeframe": "4h", "max_bars": 12}, "pending": None,
            "bars_from": "2010-01-03 23:00:00+00:00", "bars_to": "2025-09-30 23:59:00+00:00"}
    full, _ = _discovery_config(SpecRow.model_validate(base), tsmom, research)
    named, _ = _discovery_config(SpecRow.model_validate({**base, "discovery": {**want, "clusters": "H02_CLUSTERS"}}),
                                 tsmom, research)
    assert config_hash(full) == config_hash(named) and full["discovery"] == want


def test_a_freeze_commit_that_is_not_an_ancestor_of_head_is_refused(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """A commit on another branch holds the file too, but its rules were never merged: `git merge-base --is-ancestor
    <freeze_commit> HEAD` must hold as well as `git show`."""
    import subprocess

    def git(*a: str) -> str:
        return subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                               "-c", "commit.gpgsign=false", *a], capture_output=True, text=True,
                              check=True).stdout.strip()
    git("init", "-q", "-b", "main")
    (tmp_path / "pre.md").write_text("- Reading rule: frozen\n")
    git("add", "pre.md")
    git("commit", "-q", "-m", "freeze")
    frozen = git("rev-parse", "HEAD")
    git("checkout", "-q", "-b", "side")
    (tmp_path / "pre.md").write_text("- Reading rule: never merged\n")
    git("commit", "-q", "-am", "side")
    side = git("rev-parse", "HEAD")
    git("checkout", "-q", "main")
    script = _script()
    monkeypatch.setattr(script, "ROOT", tmp_path)
    assert "frozen" in script.doc_text("pre.md", frozen)
    with pytest.raises(SystemExit, match="not an ancestor of HEAD"):
        script.doc_text("pre.md", side)


def test_the_write_job_runs_only_on_main_with_the_history_to_check_the_freeze_commit() -> None:
    wf = yaml.safe_load((ROOT / ".github/workflows/preregister-quarter.yml").read_text())
    write = wf["jobs"]["write"]
    assert "github.ref == 'refs/heads/main'" in write["if"]
    checkout = next(s for s in write["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0 and checkout["with"]["persist-credentials"] is False


def test_research_yml_can_run_a_preregistered_discovery_and_a_macro_run_never_runs_without_macro() -> None:
    raw = (ROOT / ".github/workflows/research.yml").read_text()
    wf = yaml.safe_load(raw)
    inputs = wf[True]["workflow_dispatch"]["inputs"]                     # yaml reads the key `on` as True
    assert {"discover", "families", "discover_config", "rule_ref"} <= set(inputs)
    assert "no macro-v1 release yet" not in raw                          # no silent run without macro
    plan_env = next(s for s in wf["jobs"]["plan"]["steps"] if s.get("id") == "plan")["env"]
    assert {"DISCOVER", "FAMILIES", "DISCOVER_CONFIG", "RULE_REF"} <= set(plan_env)
    from goldbot.research.cells import argv, plan
    argv_ = argv(plan({"DISCOVER": "true", "DISCOVER_CONFIG": "{}", "RULE_REF": "x"})[0])
    assert "--discover" in argv_ and "--discover-config={}" in argv_ and "--rule-ref=x" in argv_
    macro_on = argv(plan({"MACRO": "true"})[0])
    assert "--macro=macro" in macro_on
    fetch = next(s for s in wf["jobs"]["trials"]["steps"] if "WANT_MACRO" in str(s.get("env", {})))
    assert 'gh release download macro-v1' in fetch["run"] and '"$WANT_MACRO" = "true"' in fetch["run"]
