"""CPCV/PBO evidence is enforced at promotion (research/cpcv_gate.py; ADR 0083 amendment, ADR 0003 veto only).

A gate-passing trial is promoted only with CPCV evidence stamped in the promotion's quarter whose veto-only verdict
is clean and whose PBO was computed against the family's other walk-forward trials when it has any. Missing evidence
is produced on demand and attached to the trial, never written as a trial row."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
import pytest

from goldbot.research import cpcv, cpcv_gate
from goldbot.research.registry import TrialRegistry, quarter_of
from goldbot.specialists import SPECIALISTS

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
LAST_QUARTER = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)
COSTS = {"swap": {"long_usd_per_lot": -60.0, "short_usd_per_lot": 20.0, "triple_weekday": 2,
                  "server_tz": "Europe/Athens", "contract_oz": 100.0}, "cost_source": "PRIORS ONLY: test"}
FAMILY = "session_open"


def _cfg(**over: Any) -> dict[str, Any]:
    return {**SPECIALISTS[FAMILY].default_config, **over}


def _trial(reg: TrialRegistry, *, passed: bool = True, **over: Any) -> dict[str, Any]:
    return reg.record(agent_id="a", family=FAMILY, config=_cfg(**over), feature_version="f", rationale="r",
                      results={"gates": {"passed": passed, "checks": []}, **COSTS})


def _paths(mean_rs: list[float]) -> list[dict[str, Any]]:
    return [{"path": i, "mean_r": r} for i, r in enumerate(mean_rs)]


def _result(mean_rs: list[float] | None = None, fv: str = "f") -> dict[str, Any]:
    return {"n_paths": 5, "paths": _paths(mean_rs or [0.1, 0.2, 0.05, 0.1, -0.02]), "group_r": [[0.0] * 6] * 5,
            "feature_version": fv}


def _pbo(p: float) -> dict[str, Any]:
    return {"pbo": p, "n_configs": 2, "n_combinations": 20, "logit_median": 0.1, "trials": [1, 2]}


def _attach(reg: TrialRegistry, trial: int, result: dict[str, Any], pbo: dict[str, Any] | None,
            when: datetime = NOW) -> None:
    reg.attach_evidence(trial, cpcv.EVIDENCE_KIND, cpcv.evidence_payload(result, pbo, "test"), now=when)


def _refusal(reg: TrialRegistry, row: dict[str, Any], run: cpcv_gate.Runner | None = None) -> str | None:
    return cpcv_gate.promotion_refusal(reg, row, reg._rows(), now=NOW, run=run)


def test_a_gate_passing_trial_without_cpcv_evidence_is_refused(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row = _trial(reg)
    why = _refusal(reg, row)
    assert why is not None and "no CPCV evidence" in why and quarter_of(NOW) in why


def test_evidence_from_an_earlier_quarter_does_not_count(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row = _trial(reg)
    _attach(reg, row["trial"], _result(), None, when=LAST_QUARTER)
    assert "no CPCV evidence" in str(_refusal(reg, row))


def test_clean_evidence_this_quarter_lets_a_lone_trial_through(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row = _trial(reg)
    _attach(reg, row["trial"], _result(), None)                   # no peers to compare with: PBO cannot exist
    assert _refusal(reg, row) is None


def test_pbo_above_the_design_threshold_refuses_the_promotion(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row, _ = _trial(reg), _trial(reg, passed=False, asia_range_max_atr_d=1.0)
    _attach(reg, row["trial"], _result(), _pbo(0.6))
    why = _refusal(reg, row)
    assert why is not None and "PBO 60% > 50%" in why


def test_pbo_at_the_threshold_is_not_a_veto(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row, _ = _trial(reg), _trial(reg, passed=False, asia_range_max_atr_d=1.0)
    _attach(reg, row["trial"], _result(), _pbo(cpcv.PBO_FRAGILE))
    assert _refusal(reg, row) is None


def test_most_paths_negative_refuses_the_promotion(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row = _trial(reg)
    _attach(reg, row["trial"], _result([-0.1, -0.2, -0.1, 0.3, 0.2]), None)
    assert "negative mean R" in str(_refusal(reg, row))


def test_missing_pbo_refuses_when_the_family_has_other_walk_forward_trials_on_the_timeframe(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row, _ = _trial(reg), _trial(reg, passed=False, asia_range_max_atr_d=1.0)
    _attach(reg, row["trial"], _result(), None)
    why = _refusal(reg, row)
    assert why is not None and "PBO" in why and "#2" in why


@pytest.mark.parametrize("payload", [{"error": "no candidates in the research window"},
                                     {"skipped": "feature version g differs from the trial's f"}])
def test_failed_or_skipped_cpcv_evidence_refuses_the_promotion(tmp_path, payload):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row = _trial(reg)
    _attach(reg, row["trial"], payload, None)
    assert "CPCV failed" in str(_refusal(reg, row))


def test_evidence_run_on_another_feature_version_does_not_count(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row = _trial(reg)
    _attach(reg, row["trial"], _result(fv="g"), None)
    assert "feature version" in str(_refusal(reg, row))


def test_a_stored_clean_verdict_is_recomputed_not_trusted(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row, _ = _trial(reg), _trial(reg, passed=False, asia_range_max_atr_d=1.0)
    payload = {**_result(), "pbo": _pbo(0.9), "verdict": {"fragile": False, "reasons": []}, "source": "hand"}
    reg.attach_evidence(row["trial"], cpcv.EVIDENCE_KIND, payload, now=NOW)
    assert "PBO 90%" in str(_refusal(reg, row))


def test_missing_evidence_is_run_on_demand_attached_to_the_trial_and_then_decides(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row, peer = _trial(reg), _trial(reg, passed=False, asia_range_max_atr_d=1.0)
    calls: list[tuple[list[int], list[int], str]] = []

    def run(compare: list[dict[str, Any]], selection: list[int], timeframe: str) -> dict[str, Any]:
        calls.append(([int(r["trial"]) for r in compare], selection, timeframe))
        return {"per_trial": {int(r["trial"]): _result() for r in compare}, "pbo": _pbo(0.2)}

    before = (reg.n_trials, reg.n_trials_effective, reg.budget_used(quarter_of(NOW)))
    assert _refusal(reg, row, run) is None
    assert calls == [([row["trial"], peer["trial"]], [row["trial"], peer["trial"]], cpcv.trial_timeframe(row))]
    ev = reg.evidence(row["trial"], cpcv.EVIDENCE_KIND)
    assert len(ev) == 1 and ev[0]["quarter"] == quarter_of(NOW) and ev[0]["payload"]["pbo"]["pbo"] == 0.2
    assert ev[0]["payload"]["source"].startswith("promotion")
    assert reg.evidence(peer["trial"]) == []                        # only the trial being promoted gets evidence
    assert (reg.n_trials, reg.n_trials_effective, reg.budget_used(quarter_of(NOW))) == before   # not a trial
    assert _refusal(reg, row, run) is None and len(calls) == 1      # this quarter's evidence is reused


def test_a_fragile_on_demand_run_is_attached_and_refuses(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row, _ = _trial(reg), _trial(reg, passed=False, asia_range_max_atr_d=1.0)

    def run(compare: list[dict[str, Any]], selection: list[int], timeframe: str) -> dict[str, Any]:
        return {"per_trial": {int(r["trial"]): _result() for r in compare}, "pbo": _pbo(0.8)}

    assert "PBO 80%" in str(_refusal(reg, row, run))
    assert reg.evidence(row["trial"], cpcv.EVIDENCE_KIND)[0]["payload"]["verdict"]["fragile"] is True


def test_an_on_demand_run_that_raises_refuses_and_attaches_nothing(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row = _trial(reg)

    def run(compare: list[dict[str, Any]], selection: list[int], timeframe: str) -> dict[str, Any]:
        raise ValueError("no 1h bars in the store")

    why = _refusal(reg, row, run)
    assert why is not None and "no 1h bars" in why and reg.evidence(row["trial"]) == []


def test_an_on_demand_run_that_skips_the_trial_refuses_and_attaches_nothing(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row = _trial(reg)

    def run(compare: list[dict[str, Any]], selection: list[int], timeframe: str) -> dict[str, Any]:
        return {"per_trial": {row["trial"]: {"skipped": "feature version g differs from the trial's f"}}, "pbo": None}

    assert "feature version g" in str(_refusal(reg, row, run)) and reg.evidence(row["trial"]) == []


def test_the_comparison_is_capped_and_keeps_the_most_recent_peers(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    row = _trial(reg)
    peers = [_trial(reg, passed=False, asia_range_max_atr_d=1.0 + i / 10) for i in range(cpcv_gate.CPCV_MAX_CONFIGS + 2)]
    got = [int(r["trial"]) for r in cpcv_gate.compare_set(row, reg._rows())]
    assert got == [row["trial"], *[p["trial"] for p in peers][-(cpcv_gate.CPCV_MAX_CONFIGS - 1):]]


def test_gate_passing_lists_only_walk_forward_trials_whose_pass_counts_for_a_promotion(tmp_path):
    reg = TrialRegistry(tmp_path / "t.jsonl")
    ok = _trial(reg)
    _trial(reg, passed=False, asia_range_max_atr_d=1.0)
    reg.record(agent_id="a", family=FAMILY, config=_cfg(asia_range_max_atr_d=2.0), feature_version="f",
               rationale="r", results={"gates": {"passed": True, "checks": []}})       # no cost treatment recorded
    assert [r["trial"] for r in cpcv_gate.gate_passing(reg._rows())] == [ok["trial"]]


def clean_runner(compare: list[dict[str, Any]], selection: list[int], timeframe: str) -> dict[str, Any]:
    """A CPCV run whose every configuration has mostly positive paths and whose PBO is low: no veto."""
    return {"per_trial": {int(r["trial"]): _result(fv=str(r.get("feature_version") or "f")) for r in compare},
            "pbo": _pbo(0.1) if len(compare) > 1 else None}


# ---------------------------------------------------------------------------------------------- the tournament job
def _tournament_setup(tmp_path):
    from goldbot.config import load_settings
    from goldbot.data.store import Store
    from goldbot.engine.shadow import ShadowBook
    from goldbot.ops.jobs import JobContext
    from goldbot.research.model_registry import ModelRegistry
    from goldbot.research.population import Member, Population
    from goldbot.research.promotion import EVIDENCE_START_UTC
    from tests.test_population import _add_trades, _good

    reg = TrialRegistry(tmp_path / "trials.jsonl")
    row = _trial(reg, asia_range_max_atr_d=1.0)
    now = EVIDENCE_START_UTC + pd.Timedelta(days=60)
    pop = Population(tmp_path / "pop.json")
    pop.members["strong"] = Member(agent_id="strong", family=FAMILY, config=_cfg(asia_range_max_atr_d=1.0),
                                   generation=1, created_utc=now - pd.Timedelta(days=200),
                                   status_since_utc=now - pd.Timedelta(days=200))
    book = ShadowBook(tmp_path)
    _add_trades(book, "strong", _good(150), start=EVIDENCE_START_UTC)
    book.save(now)
    ctx = JobContext(settings=load_settings(), store=Store(tmp_path / "data"), state_dir=tmp_path,
                     models=ModelRegistry(tmp_path / "models"), trials=reg, accounts=[], population=pop)
    return ctx, reg, row, pop, now


def test_the_tournament_holds_a_gate_passing_agent_back_when_cpcv_cannot_run(tmp_path):
    from goldbot.ops.jobs import tournament
    ctx, reg, row, pop, now = _tournament_setup(tmp_path)          # empty store: CPCV has no bars to run on
    out = tournament(ctx, now)
    assert out["promoted"] == [] and out["awaiting_research"] == ["strong"] and pop.members["strong"].status == "shadow"
    assert "CPCV failed" in out["cpcv_refused"]["strong"] and reg.evidence(row["trial"]) == []


def test_the_tournament_runs_cpcv_on_demand_and_promotes_on_clean_evidence(tmp_path, monkeypatch):
    from goldbot.ops import jobs
    ctx, reg, row, pop, now = _tournament_setup(tmp_path)
    monkeypatch.setattr(jobs, "_cpcv_runner", lambda c, slot: clean_runner)
    out = jobs.tournament(ctx, now)
    assert out["promoted"] == ["strong"] and out["cpcv_refused"] == {} and pop.members["strong"].status == "live"
    assert reg.evidence(row["trial"], cpcv.EVIDENCE_KIND)[0]["quarter"] == quarter_of(now.to_pydatetime())


def test_the_tournament_refuses_promotion_on_a_pbo_veto(tmp_path, monkeypatch):
    from goldbot.ops import jobs
    ctx, reg, row, pop, now = _tournament_setup(tmp_path)
    _attach(reg, row["trial"], _result(), _pbo(0.75), when=now.to_pydatetime())
    monkeypatch.setattr(jobs, "_cpcv_runner", lambda c, slot: clean_runner)   # evidence exists: not re-run
    out = jobs.tournament(ctx, now)
    assert out["promoted"] == [] and "PBO 75% > 50%" in out["cpcv_refused"]["strong"]
