"""Research director: evidence per family, the budget allocation rule, the plan file, and monthly_research using it."""
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.agents.roles import ROLES
from goldbot.agents.tools import ReadOnlyTools
from goldbot.config import load_settings
from goldbot.data.store import Store
from goldbot.ops import jobs
from goldbot.ops.jobs import JobContext, monthly_research, research_budget, research_director
from goldbot.research.director import (
    DEFAULT_HOLDOUT_FROM,
    DEFAULT_QUARTER_BUDGET,
    PLAN_FILE,
    AgentEvidence,
    FamilyScore,
    ResearchPlan,
    ShadowEvidence,
    allocate,
    build_plan,
    holdout_window,
    quarter_budget,
    quarter_usage,
    score_family,
)
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.pipeline import ResearchResult
from goldbot.research.population import Population
from goldbot.research.promotion import PerfStats
from goldbot.research.registry import TrialRegistry
from goldbot.specialists import SPECIALISTS

NOW = pd.Timestamp("2026-10-03 12:30", tz="UTC")
FAMILIES = sorted(SPECIALISTS)


def _trial(n: int, family: str, auc: float | None = None, n_oof: int = 300, mf_n: int = 0, dsr: float | None = None,
           n_candidates: int = 600, n_folds: int = 4, lookahead: list[str] | None = None) -> dict[str, Any]:
    res: dict[str, Any] = {"n_candidates": n_candidates, "n_folds": n_folds}
    if auc is not None:
        res.update(oof_auc=auc, all_candidates={"n": n_oof}, model_filtered={"n": mf_n, "dsr": dsr, "hit_rate": 0.5})
    if lookahead is not None:
        res["lookahead"] = {"lookahead_columns": lookahead, "columns_checked": 100}
    return {"trial": n, "family": family, "results": res}


def _score(family: str, evidence: float, blocked: bool = False) -> FamilyScore:
    s = score_family(family, [], [], [])
    return s.model_copy(update={"evidence": evidence, "blocked": blocked})


# ------------------------------------------------------------------------------------------- evidence
def test_family_evidence_is_traceable_to_the_registry_numbers():
    rows = [_trial(1, "trend", auc=0.54, n_oof=200), _trial(2, "trend", auc=0.56, n_oof=300, mf_n=250, dsr=0.95),
            _trial(3, "trend", auc=0.60, n_oof=400, mf_n=10, dsr=0.99),     # DSR on 10 trades is not evidence
            _trial(4, "breakout", auc=0.70)]
    s = score_family("trend", rows, [], [])
    assert s.trials == 3 and s.best_auc == 0.60 and s.median_auc == pytest.approx(0.56) and s.median_n_oof == 300
    assert s.auc_se == pytest.approx(1 / math.sqrt(900)) and s.auc_z == pytest.approx(0.06 * 30)
    assert s.auc_evidence == pytest.approx(1.8 / 3.0)
    assert s.best_dsr == 0.95 and s.best_dsr_trial == 2 and s.dsr_evidence == pytest.approx(1.0)
    assert s.shadow_evidence == 0.0 and s.evidence == pytest.approx(0.6 + 1.0)
    assert not s.blocked and s.max_filtered_trades == 250


def test_lookahead_blocks_until_a_clean_check_is_recorded_and_other_flags():
    dirty = [_trial(1, "breakout", auc=0.6, lookahead=["vol_z", "h1_x"])]
    s = score_family("breakout", dirty, [], [])
    assert s.blocked and "lookahead" in s.flags and s.lookahead_columns == ["vol_z", "h1_x"]
    fixed = dirty + [_trial(2, "breakout", auc=0.6), _trial(3, "breakout", auc=0.6, lookahead=[])]
    assert not score_family("breakout", fixed, [], []).blocked
    few = score_family("session_open", [_trial(1, "session_open", n_candidates=100, n_folds=4)], [], [])
    assert {"few_candidates_per_fold", "no_oof_auc", "few_filtered_trades"} <= set(few.flags) and not few.blocked
    assert score_family("trend", [], [], []).flags == ["no_trials"]


def test_shadow_evidence_needs_the_minimum_trade_counts():
    st = PerfStats(n_trades=100, sharpe_ann=1.0, hit_rate=0.5, max_dd=0.05, trades_per_week=2.0, mean_ret=0.002, std_ret=0.01)
    short = st.model_copy(update={"n_trades": 20})
    shadow = [ShadowEvidence(family="trend", version="v1", stats=st), ShadowEvidence(family="trend", version="v2", stats=short)]
    s = score_family("trend", [], shadow, [])
    assert s.shadow_z == pytest.approx(0.2 * 10) and s.shadow_source == "v1" and s.shadow_trades == 100
    agents = [AgentEvidence(family="trend", agent_id="a", status="shadow", n=59, sharpe_per_trade=1.0),
              AgentEvidence(family="trend", agent_id="b", status="live", n=64, sharpe_per_trade=0.4, fitness=0.3, ece=0.05)]
    s = score_family("trend", [], [], agents)
    assert s.shadow_z == pytest.approx(0.4 * 8) and s.shadow_source == "b" and s.shadow_evidence == 1.0
    assert s.best_fitness == 0.3 and s.best_fitness_ece == 0.05


# ------------------------------------------------------------------------------------------- allocation
def test_allocation_sums_to_budget_respects_floor_and_follows_evidence():
    scores = [_score("breakout", 0.0), _score("mean_reversion", 2.0), _score("session_open", 0.5), _score("trend", 1.0)]
    budget, left = allocate(scores, 48, floor=2)
    assert sum(budget.values()) == 48 and left == 0
    assert all(v >= 2 for v in budget.values())
    assert budget["mean_reversion"] > budget["trend"] > budget["session_open"] > budget["breakout"] == 2
    # exactly the documented rule: 40 left after the floors, split 2 : 1 : 0.5 : 0 by largest remainders
    assert budget == {"breakout": 2, "mean_reversion": 2 + 23, "session_open": 2 + 6, "trend": 2 + 11}


def test_lookahead_dirty_family_gets_nothing_and_its_share_goes_elsewhere():
    scores = [_score("breakout", 3.0, blocked=True), _score("mean_reversion", 1.0), _score("session_open", 1.0),
              _score("trend", 1.0)]
    budget, left = allocate(scores, 48, floor=2)
    assert budget["breakout"] == 0 and sum(budget.values()) == 48 and left == 0
    assert budget["mean_reversion"] == budget["session_open"] == budget["trend"] == 16
    every, left = allocate([_score("trend", 1.0, blocked=True)], 12, floor=2)
    assert every == {"trend": 0} and left == 12


def test_allocation_edge_cases_cap_zero_evidence_and_small_budget():
    flat, _ = allocate([_score(f, 0.0) for f in FAMILIES], 48, floor=2)
    assert set(flat.values()) == {12}
    capped, left = allocate([_score("trend", 5.0), _score("breakout", 0.1)], 48, floor=2, cap=26)
    assert capped == {"trend": 26, "breakout": 22} and left == 0
    full, left = allocate([_score("trend", 5.0), _score("breakout", 0.1)], 60, floor=2, cap=26)
    assert full == {"trend": 26, "breakout": 26} and left == 8
    small, _ = allocate([_score(f, 1.0 if f == "trend" else 0.0) for f in FAMILIES], 3, floor=2)
    assert sum(small.values()) == 3 and small["trend"] == 3
    with pytest.raises(ValueError):
        allocate([_score("trend", 1.0)], -1, floor=2)


def test_plan_ranks_focus_and_round_trips_through_json(tmp_path):
    rows = [_trial(1, "trend", auc=0.58, n_oof=400, mf_n=270, dsr=0.7), _trial(2, "breakout", auc=0.6, lookahead=["x"]),
            _trial(3, "session_open", n_candidates=90, n_folds=3)]
    plan = build_plan(NOW, FAMILIES, rows, [], [], quarter_budget=20, monthly_total=48, trial_budget_per_month=12,
                      floor=2, cap=26)
    assert sum(plan.budget.values()) == 20 and plan.budget["breakout"] == 0 and plan.quarter == "2026Q4"
    assert all(plan.grid_budget[f] <= plan.budget[f] // 2 for f in FAMILIES)
    assert plan.focus[0].family == "trend" and plan.focus[-1].family == "breakout"
    assert any("blocked" in r for r in plan.focus[-1].reasons)
    assert any("candidates per fold" in r for f in plan.focus if f.family == "session_open" for r in f.reasons)
    plan.save(tmp_path / PLAN_FILE)
    back = ResearchPlan.load(tmp_path / PLAN_FILE)
    assert back == plan and back is not None and back.created_utc == NOW
    assert ResearchPlan.load(tmp_path / "nope.json") is None and not (tmp_path / "research_plan.tmp").exists()


# ------------------------------------------------------------------------------------------- jobs
def _ctx(tmp_path: Path, **research: Any) -> JobContext:
    s = load_settings()
    if research:
        s = s.model_copy(update={"research": s.research.model_copy(update=research)})
    return JobContext(settings=s, store=Store(tmp_path / "data"), state_dir=tmp_path, models=ModelRegistry(tmp_path / "models"),
                      trials=TrialRegistry(tmp_path / "trials.jsonl"), accounts=[], population=Population(tmp_path / "pop.json"))


ENDS: list[pd.Timestamp] = []


def _fake_walk_forward(ctx: JobContext, spec: Any, end: pd.Timestamp, months: int, n_trials: int = 1) -> ResearchResult:
    ENDS.append(end)
    return ResearchResult(agent_id=spec.agent_id, n_candidates=500, n_folds=4, oof=pd.DataFrame(), feature_version="f1",
                          importance=None, metrics={"n_candidates": 500, "n_folds": 4, "oof_auc": 0.52,
                                                    "all_candidates": {"n": 400}, "model_filtered": {"n": 30, "dsr": 0.3}})


def test_research_director_job_writes_the_plan_from_the_registry(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.trials.record(agent_id="a", family="trend", config={}, feature_version="f1", rationale="r",
                      results={"oof_auc": 0.6, "all_candidates": {"n": 500}, "model_filtered": {"n": 90, "dsr": 0.8}})
    ctx.trials.record(agent_id="b", family="breakout", config={}, feature_version="f1", rationale="r",
                      results={"lookahead": {"lookahead_columns": ["vol_z"]}})
    now = pd.Timestamp.now("UTC")             # the registry stamps rows with the wall clock
    out = research_director(ctx, now)
    budget, cap = research_budget(ctx)
    assert budget == min(DEFAULT_QUARTER_BUDGET, ctx.settings.research.trial_budget_per_month * len(SPECIALISTS))
    assert cap == 26
    plan = ResearchPlan.load(tmp_path / PLAN_FILE)
    assert plan is not None and plan.budget == out["budget"] and plan.quarter_used == 2
    assert sum(plan.budget.values()) == DEFAULT_QUARTER_BUDGET - 2          # never more than the quarter has left
    assert out["blocked"] == ["breakout"] and out["focus"][0] == "trend"
    assert all(plan.budget[f] >= ctx.settings.research.director_floor for f in FAMILIES if f != "breakout")


def _plan(created: pd.Timestamp, grid: dict[str, int]) -> ResearchPlan:
    return ResearchPlan(created_utc=created, quarter="x", quarter_budget=20, quarter_used=0, total_budget=sum(grid.values()),
                        floor=0, cap=26, budget={f: 2 * g for f, g in grid.items()}, grid_budget=grid, unallocated=0,
                        holdout_from="2025-10-01", holdout_to="2026-10-01", holdout_trials_ignored=0, focus=[],
                        evidence=[_score(f, 0.0) for f in FAMILIES])


def test_monthly_research_honours_the_plan_the_quarter_budget_and_the_holdout(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_walk_forward", _fake_walk_forward)
    ENDS.clear()
    ctx = _ctx(tmp_path, trial_budget_per_month=6)
    slot = pd.Timestamp.now("UTC").floor("min")      # the registry stamps rows with the wall clock: same quarter
    grid = {"breakout": 0, "mean_reversion": 5, "session_open": 1, "trend": 2}
    _plan(slot - pd.Timedelta(days=1), grid).save(tmp_path / PLAN_FILE)
    out = monthly_research(ctx, slot)
    assert {f: out[f]["trials"] for f in FAMILIES} == grid
    assert out["plan"] is not None and "research plan of" in Path(out["report"]).read_text()
    assert all(e <= pd.Timestamp(DEFAULT_HOLDOUT_FROM, tz="UTC") for e in ENDS)       # never into the held-out year
    # a stale plan is not used: the flat per-family budget applies, but only until the quarter's 20 trials are spent
    _plan(slot - pd.Timedelta(days=30), {f: 0 for f in FAMILIES}).save(tmp_path / PLAN_FILE)
    out = monthly_research(ctx, slot)
    assert out["plan"] is None and ctx.trials.n_trials == 20
    assert [out[f]["trials"] for f in FAMILIES] == [6, 6, 0, 0] and out["session_open"]["quarter_budget_spent"]
    assert "quarter's trial budget (20) is spent" in Path(out["report"]).read_text()
    (tmp_path / PLAN_FILE).write_text("{not json")
    assert jobs.current_plan(ctx, slot) is None


def test_quarter_usage_holdout_and_budget_defaults():
    rows: list[dict[str, Any]] = [{"ts": "2026-09-30T23:00:00+00:00"}, {"ts": "2026-10-01T00:00:00+00:00"}, {"ts": "2026-12-31T10:00:00+00:00"},
            {"ts": "2027-01-01T00:00:00+00:00"}, {"ts": None}]
    assert quarter_usage(rows, NOW, 20) == ("2026Q4", 2, 18)
    assert quarter_usage(rows * 20, NOW, 20)[2] == 0
    s = load_settings().research
    assert quarter_budget(s) == getattr(s, "trial_budget_quarter", DEFAULT_QUARTER_BUDGET)
    assert quarter_budget(object()) == DEFAULT_QUARTER_BUDGET
    assert holdout_window(object()) == (pd.Timestamp("2025-10-01", tz="UTC"), pd.Timestamp("2026-10-01", tz="UTC"))
    # trials scored on the held-out year are never evidence
    rows2: list[dict[str, Any]] = [{**_trial(1, "trend", auc=0.9, n_oof=1000), "status": "holdout"}, _trial(2, "trend", auc=0.5)]
    plan = build_plan(NOW, ["trend"], rows2, [], [], quarter_budget=20, monthly_total=20, trial_budget_per_month=0, floor=2)
    assert plan.evidence[0].best_auc == 0.5 and plan.holdout_trials_ignored == 1


# ------------------------------------------------------------------------------------------- agent role and tool
def test_director_role_reads_the_plan_and_runs_before_the_research_agents(tmp_path):
    weekly = [r.name for r in ROLES.values() if r.cadence == "weekly"]
    assert weekly.index("research_director") < weekly.index("improvement_agent") < weekly.index("research_analyst")
    director = ROLES["research_director"]
    assert "read_research_plan" in director.tools and "read_research_plan" in ROLES["research_analyst"].tools
    assert not {"run_trial", "update_hypothesis"} & set(director.tools)        # it cannot run or judge trials
    tools = ReadOnlyTools(tmp_path, Store(tmp_path / "data"), now=lambda: NOW)
    out, err = tools.call("read_research_plan", {}, list(director.tools))
    assert not err and json.loads(out)["missing"] == "research_plan"
    build_plan(NOW, FAMILIES, [], [], [], quarter_budget=20, monthly_total=48, trial_budget_per_month=0, floor=2,
               cap=26).save(tmp_path / PLAN_FILE)
    out, err = tools.call("read_research_plan", {}, list(director.tools))
    plan = json.loads(out)
    assert not err and sum(plan["budget"].values()) == 20 and set(plan["grid_budget"].values()) == {0}
    d = tools.definitions(["read_research_plan"])[0]
    assert d["strict"] is True and d["input_schema"] == {"type": "object", "properties": {}, "required": [],
                                                         "additionalProperties": False}
