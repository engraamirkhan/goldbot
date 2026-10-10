"""Research director: evidence per family, the budget allocation rule, the plan file, and monthly_research using it."""
import hashlib
import json
import math
import random
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.agents.roles import ROLES
from goldbot.agents.tools import ReadOnlyTools
from goldbot.config import RetiredFamily, load_settings
from goldbot.data.store import Store
from goldbot.ops import jobs
from goldbot.ops.jobs import JobContext, monthly_research, research_budget, research_director
from goldbot.research import registry as registry_mod
from goldbot.research.attribution import REPORT_FILE
from goldbot.research.director import (
    ATTRIBUTION_FILE,
    ATTRIBUTION_MAX_AGE_DAYS,
    DEFAULT_HOLDOUT_FROM,
    DEFAULT_QUARTER_BUDGET,
    GRID_RATIONALE,
    PLAN_FILE,
    REINSTATE_SOURCES,
    REINSTATE_T,
    AgentEvidence,
    FamilyScore,
    ResearchPlan,
    Reservation,
    ShadowEvidence,
    allocate,
    build_plan,
    holdout_window,
    hypotheses_retired,
    is_queued,
    later_preregistration,
    load_attribution,
    load_hypotheses,
    pending_preregistration,
    quarter_budget,
    quarter_usage,
    reinstatement_threshold,
    reserved_trials,
    retired_drift,
    score_family,
    tilted_shares,
)
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.pipeline import ResearchResult
from goldbot.research.population import MIN_RANK_TRADES, Population
from goldbot.research.promotion import PerfStats
from goldbot.research.registry import TrialBudgetExceeded, TrialRegistry, planned_quarter, quarter_of
from goldbot.specialists import SPECIALISTS

NOW = pd.Timestamp("2026-10-03 12:30", tz="UTC")
FAMILIES = jobs.research_families()      # every registered family except those under their pre-registered screen


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
    assert set(flat.values()) == {48 // len(FAMILIES)}           # an even split when no family has evidence
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


def _fake_walk_forward(ctx: JobContext, spec: Any, end: pd.Timestamp, months: int, n_trials: int = 1,
                       holdout: Any = None) -> ResearchResult:
    ENDS.append(end)
    return ResearchResult(agent_id=spec.agent_id, n_candidates=500, n_folds=4, oof=pd.DataFrame(), feature_version="f1",
                          importance=None, metrics={"n_candidates": 500, "n_folds": 4, "oof_auc": 0.52,
                                                    "all_candidates": {"n": 400}, "model_filtered": {"n": 30, "dsr": 0.3}})


def test_research_director_job_writes_the_plan_from_the_registry(tmp_path):
    ctx = _ctx(tmp_path, reserved_trials_quarter=0, retired_families=[])   # the real settings: see the last test
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
    ctx = _ctx(tmp_path, trial_budget_per_month=6, label_grid_paused=False,   # the grid is paused by default
               reserved_trials_quarter=0)
    slot = pd.Timestamp.now("UTC").floor("min")      # the registry stamps rows with the wall clock: same quarter
    grid = {"breakout": 0, "intraday_momentum": 0, "mean_reversion": 5, "session_open": 1, "trend": 2, "tsmom": 0}
    _plan(slot - pd.Timedelta(days=1), grid).save(tmp_path / PLAN_FILE)
    out = monthly_research(ctx, slot)
    assert {f: out[f]["trials"] for f in FAMILIES} == grid
    assert out["plan"] is not None and "research plan of" in Path(out["report"]).read_text()
    assert all(e <= pd.Timestamp(DEFAULT_HOLDOUT_FROM, tz="UTC") for e in ENDS)       # never into the held-out year
    # a stale plan is not used: the flat per-family budget applies, but only until the quarter's 20 trials are spent
    _plan(slot - pd.Timedelta(days=30), {f: 0 for f in FAMILIES}).save(tmp_path / PLAN_FILE)
    out = monthly_research(ctx, slot)
    assert out["plan"] is None and ctx.trials.n_trials == 20
    assert [out[f]["trials"] for f in FAMILIES] == [6, 6] + [0] * (len(FAMILIES) - 2)   # in family order
    assert out["session_open"]["quarter_budget_spent"]
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


# ------------------------------------------------------------------------------------------- attribution and retirement
HYP = """# Hypothesis portfolio

## A. Ranked portfolio

| Rank | ID | Hypothesis | Status |
|---|---|---|---|
| 1 | H-01 | **Slow TSMOM** | proposed |

## B. Retired: do not re-test without new evidence

| ID | Idea | Status | Trials (registry #, report) | Result | Reason retired |
|---|---|---|---|---|---|
| R-01 | mean_reversion (15m; RSI fades) | retired | #3 (#40) | none | no edge |
| R-04 | range breakout (1h) | retired | #6, #8 (#41) | none | no edge |
| R-06 | tsmom at 1h/4h horizons | failed; superseded by H-01 | #16 (#51) | none | costs |
| R-08 | Pre-FOMC drift | retired from the literature (never trialled) | none | - | gone |

## C. Ledger
"""
RETIRED_ON = date(2026, 6, 1)
RETIRED = [RetiredFamily(family="mean_reversion", hypothesis_id="R-01", retired=RETIRED_ON, reason="no edge", trials=[3]),
           RetiredFamily(family="breakout", hypothesis_id="R-04", retired=RETIRED_ON, reason="no edge", trials=[6, 8])]
PROMOTED: dict[str, pd.Timestamp | None] = {"v1": NOW - pd.Timedelta(days=300)}


def _rich_trials() -> list[dict[str, Any]]:
    """Registry evidence for every family, unequal, so the evidence split is not flat."""
    aucs = {"breakout": 0.53, "intraday_momentum": 0.52, "mean_reversion": 0.55, "session_open": 0.54, "trend": 0.56,
            "tsmom": 0.57}
    return [_trial(i + 1, f, auc=a, n_oof=400) for i, (f, a) in enumerate(sorted(aucs.items()))]


def _trades(family: str, n: int, t: float, start: pd.Timestamp, version: str = "v1") -> list[dict[str, Any]]:
    """n (even) taken trades whose net-R t-stat is exactly t: mean m +- 1 alternately, so t = m x sqrt(n - 1)."""
    m = t / math.sqrt(n - 1)
    return [{"family": family, "version": version, "taken": True, "entry_utc": (start + pd.Timedelta(hours=i)).isoformat(),
             "r_net": m + (1.0 if i % 2 == 0 else -1.0)} for i in range(n)]


def _report(cells: dict[str, tuple[int, float]], as_of: pd.Timestamp = NOW - pd.Timedelta(days=1),
            min_trades: int = 30, start: pd.Timestamp = NOW - pd.Timedelta(days=100),
            extra: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [r for f, (n, t) in cells.items() for r in _trades(f, n, t, start)] + (extra or [])
    return {"as_of": as_of.isoformat(), "min_trades": min_trades, "n_taken": len(rows), "trades": rows}


def _plan_with(**kw: Any) -> ResearchPlan:
    trials = kw.pop("trials", None) or _rich_trials()
    kw.setdefault("promoted", PROMOTED)
    return build_plan(NOW, FAMILIES, trials, kw.pop("shadow", []), kw.pop("agents", []), quarter_budget=100,
                      monthly_total=60, trial_budget_per_month=0, floor=2, cap=None, **kw)


def test_attribution_shift_is_capped_at_25_percent_of_the_evidence_split_and_the_floor_is_untouched():
    extreme = _report({"tsmom": (2000, 60.0), "trend": (2000, -60.0), "breakout": (1000, 40.0)})
    base, plan = _plan_with(), _plan_with(attribution=extreme)
    assert plan.evidence_budget == base.budget and sum(plan.budget.values()) == 60
    assert plan.budget["tsmom"] > base.budget["tsmom"] and plan.budget["trend"] < base.budget["trend"]
    tsmom = next(s.attribution for s in plan.evidence if s.family == "tsmom")
    assert tsmom is not None and tsmom.n == 2000 and tsmom.t_stat == pytest.approx(60.0) and tsmom.tilts
    att = [m for m in plan.moves if m.source == "attribution"]
    assert {m.family for m in att} == set(FAMILIES)        # every share in the pool moved; each one is recorded
    pool = 60 - 2 * len(FAMILIES)
    for m in att:
        assert m.shift_pct is not None and abs(m.shift_pct) <= 25.0 + 1e-6
        assert m.share_before is not None and m.share_after is not None
        assert abs(m.share_after - m.share_before) <= 0.25 * m.share_before + 1e-6
        # in trials: within 25% of the evidence-based pool share, plus one unit of rounding
        assert abs(m.budget_after - m.budget_before) <= 0.25 * m.share_before * pool + 1
    assert sum(m.share_after or 0.0 for m in att) == pytest.approx(1.0)
    assert all(v >= 2 for v in plan.budget.values())       # director_floor is never tilted
    # the bound holds for any terms, not just this case
    rng = random.Random(7)
    for _ in range(200):
        ev = {f: rng.choice([0.0, rng.random() * 3]) for f in FAMILIES}
        b, t = tilted_shares(ev, {f: rng.uniform(-1, 1) for f in FAMILIES})
        assert sum(t.values()) == pytest.approx(1.0)
        assert all(abs(t[f] - b[f]) <= 0.25 * b[f] + 1e-12 for f in FAMILIES)


def test_noise_cells_stale_or_future_reports_are_ignored():
    base = _plan_with()
    noise = _report({"tsmom": (28, 9.0), "trend": (20, -9.0)})          # under min_trades (30): noise whatever t
    future = _report({"tsmom": (500, 9.0)}, as_of=NOW + pd.Timedelta(hours=1))
    stale = _report({"tsmom": (500, 9.0)}, as_of=NOW - pd.Timedelta(days=ATTRIBUTION_MAX_AGE_DAYS + 1))
    for rep in (noise, future, stale):
        plan = _plan_with(attribution=rep)
        assert plan.budget == base.budget and plan.moves == []
        assert all(s.attribution is not None and not s.attribution.used and s.attribution.term == 0.0
                   for s in plan.evidence)
    assert "after the plan" in (_plan_with(attribution=future).attribution_note or "")
    # a noise cell cannot reinstate a retired family either
    plan = _plan_with(attribution=_report({"mean_reversion": (28, 9.0)}, start=NOW - pd.Timedelta(days=20)), retired=RETIRED)
    assert plan.budget["mean_reversion"] == 1 and next(s for s in plan.evidence if s.family == "mean_reversion").retired


def test_attribution_counts_only_trades_from_each_versions_promotion_on():
    """Out of sample: a trade entered before its version's promotion, or of a version with no promotion record, is
    left out of the cell the director uses."""
    promo = NOW - pd.Timedelta(days=30)
    before = _trades("tsmom", 400, 9.0, promo - pd.Timedelta(days=25), version="v2")       # entered before promotion
    unknown = _trades("tsmom", 400, 9.0, NOW - pd.Timedelta(days=20), version="ghost")     # never promoted
    after = _trades("tsmom", 60, 1.0, promo + pd.Timedelta(days=1), version="v2")
    rep = _report({}, extra=before + unknown + after)
    plan = _plan_with(attribution=rep, promoted={"v2": promo, "ghost": None})
    a = next(s.attribution for s in plan.evidence if s.family == "tsmom")
    assert a is not None and a.n == 60 and a.t_stat == pytest.approx(1.0) and a.excluded_pre_promotion == 800
    assert "800 trades before their version's promotion" in (plan.attribution_note or "")
    # without any promotion record nothing counts
    assert all(s.attribution is not None and s.attribution.n == 0 for s in _plan_with(attribution=rep, promoted={}).evidence)


def test_retired_families_share_one_exploration_trial_a_quarter():
    plan = _plan_with(retired=RETIRED)
    # 2026Q4 rotates to mean_reversion ((2026 x 4 + 3) mod 2 over [breakout, mean_reversion]); one trial for all
    assert plan.retired_floor == {"breakout": 0, "mean_reversion": 1} and plan.retired_explore == "mean_reversion"
    assert plan.budget["mean_reversion"] == 1 and plan.budget["breakout"] == 0 and sum(plan.budget.values()) == 60
    assert plan.budget["tsmom"] > 2                          # "failed; superseded" in the doc is not retired
    moves = {m.family: m for m in plan.moves if m.source == "retired_families"}
    assert moves["mean_reversion"].budget_after == 1 and moves["mean_reversion"].budget_before > 1
    assert moves["breakout"].budget_after == 0
    assert any("shared exploration trial" in r for f in plan.focus if f.family == "mean_reversion" for r in f.reasons)
    assert any("rotation" in r for f in plan.focus if f.family == "breakout" for r in f.reasons)
    # next quarter the rotation moves on, deterministically
    nxt = build_plan(NOW + pd.Timedelta(days=92), FAMILIES, _rich_trials(), [], [], quarter_budget=100,
                     monthly_total=60, trial_budget_per_month=0, floor=2, retired=RETIRED, promoted=PROMOTED)
    assert nxt.quarter == "2027Q1" and nxt.retired_floor == {"breakout": 1, "mean_reversion": 0}
    # the trial is shared: ANY retired family's trial this quarter uses it up for all of them
    for fam in ("mean_reversion", "breakout"):
        used = _rich_trials() + [{**_trial(7, fam), "ts": (NOW - pd.Timedelta(days=2)).isoformat()}]
        again = _plan_with(retired=RETIRED, trials=used)
        assert again.budget["mean_reversion"] == again.budget["breakout"] == 0
        assert set(again.retired_floor.values()) == {0}
    # with only a couple of trials left, live families' floors come first
    tight = build_plan(NOW, FAMILIES, _rich_trials(), [], [], quarter_budget=100, monthly_total=8,
                       trial_budget_per_month=0, floor=2, retired=RETIRED)
    assert sum(tight.budget.values()) == 8 and tight.budget["mean_reversion"] + tight.budget["breakout"] == 0
    # a lookahead-dirty retired family is skipped: the shared trial goes to a clean one
    dirty = _rich_trials() + [_trial(99, "mean_reversion", auc=0.6, lookahead=["x"])]
    d = _plan_with(retired=RETIRED, trials=dirty)
    assert d.budget["mean_reversion"] == 0 and d.budget["breakout"] == 1


def test_the_shared_retired_trial_goes_to_the_best_post_retirement_evidence_when_there_is_any():
    after = pd.Timestamp(RETIRED_ON).tz_localize("UTC") + pd.Timedelta(days=5)
    # breakout: positive out-of-sample attribution after retirement, short of reinstatement (shrunk t 0.8 x 1.5 = 1.2)
    plan = _plan_with(retired=RETIRED, attribution=_report({"breakout": (400, 1.5)}, start=after))
    br = next(s for s in plan.evidence if s.family == "breakout")
    assert br.retired and br.attribution is not None and br.attribution.t_shrunk == pytest.approx(1.2)
    assert plan.retired_floor == {"breakout": 1, "mean_reversion": 0}      # not the rotation's mean_reversion
    assert any("best post-retirement evidence" in r for f in plan.focus if f.family == "breakout" for r in f.reasons)
    # a negative record is not evidence for exploring: the rotation decides
    neg = _plan_with(retired=RETIRED, attribution=_report({"breakout": (400, -1.5)}, start=after))
    assert neg.retired_floor == {"breakout": 0, "mean_reversion": 1}
    # five retired families take one of the director's seven trials, not five
    five = [RetiredFamily(family=f, hypothesis_id=f"R-0{i}", retired=RETIRED_ON, reason="r")
            for i, f in enumerate(["breakout", "intraday_momentum", "mean_reversion", "session_open", "trend"])]
    p7 = build_plan(NOW, FAMILIES, _rich_trials(), [], [], quarter_budget=20, monthly_total=48,
                    trial_budget_per_month=0, floor=2, reserved_setting=13, retired=five)
    assert p7.total_budget == 7 and sum(p7.budget[r.family] for r in five) == 1 and p7.budget["tsmom"] == 6


def test_reinstatement_uses_only_trades_after_retirement_and_the_corrected_threshold():
    thr = reinstatement_threshold(2 * REINSTATE_SOURCES)       # two retired families, one source
    assert reinstatement_threshold(1) == pytest.approx(REINSTATE_T) and thr == pytest.approx(2.28, abs=0.01)
    assert reinstatement_threshold(5) == pytest.approx(2.61, abs=0.01)
    retired_at = pd.Timestamp(RETIRED_ON).tz_localize("UTC")
    # a strong record entered BEFORE the retirement date does not count
    old = _report({"mean_reversion": (600, 8.0)}, start=retired_at - pd.Timedelta(days=60))
    plan = _plan_with(retired=RETIRED, attribution=old)
    mr = next(s for s in plan.evidence if s.family == "mean_reversion")
    assert mr.retired and mr.attribution is not None and mr.attribution.n == 0
    assert mr.attribution.excluded_pre_retirement == 600 and plan.budget["mean_reversion"] == 1
    after = retired_at + pd.Timedelta(days=5)
    # 400 trades after it: shrunk t = t x 0.8. t 2.625 -> 2.1: past the uncorrected 2, short of the corrected 2.28
    weak = _plan_with(retired=RETIRED, attribution=_report({"mean_reversion": (400, 2.625)}, start=after))
    w = next(s for s in weak.evidence if s.family == "mean_reversion")
    assert w.attribution is not None and w.attribution.t_shrunk == pytest.approx(2.1) and w.retired
    assert weak.budget["mean_reversion"] == 1 and weak.reinstate_t == pytest.approx(thr, abs=1e-6)
    strong = _plan_with(retired=RETIRED, attribution=_report({"mean_reversion": (400, 3.0)}, start=after))
    s = next(s for s in strong.evidence if s.family == "mean_reversion")
    assert not s.retired and s.new_evidence and strong.budget["mean_reversion"] >= 2
    assert any("reinstated" in r for f in strong.focus if f.family == "mean_reversion" for r in f.reasons)


def test_shadow_evidence_is_counted_once_attribution_tilts_only_without_shadow_z():
    rep = _report({"tsmom": (400, 3.0)})
    shadowed = [AgentEvidence(family="tsmom", agent_id="tsmom-a", status="shadow", n=MIN_RANK_TRADES, sharpe_per_trade=0.5)]
    plain, both = _plan_with(attribution=rep), _plan_with(attribution=rep, agents=shadowed)
    ts_plain = next(s for s in plain.evidence if s.family == "tsmom")
    ts_both = next(s for s in both.evidence if s.family == "tsmom")
    assert ts_plain.attribution is not None and ts_plain.attribution.tilts
    assert ts_both.shadow_z is not None and ts_both.attribution is not None and ts_both.attribution.used
    assert not ts_both.attribution.tilts and not [m for m in both.moves if m.source == "attribution"]
    assert any("not a tilt" in r for f in both.focus if f.family == "tsmom" for r in f.reasons)
    # a retired family's shadow t-stat (no per-trade dates, so not cut at retirement) is not a reinstatement source
    hot = [AgentEvidence(family="breakout", agent_id="breakout-a", status="shadow", n=400, sharpe_per_trade=0.5)]
    plan = _plan_with(retired=RETIRED, agents=hot)
    br = next(s for s in plan.evidence if s.family == "breakout")
    assert br.shadow_z is not None and br.shadow_z >= 5 and br.retired and plan.budget["breakout"] == 0
    assert plan.retired_explore == "mean_reversion"        # nor does it steer the shared exploration trial


def test_director_cannot_spend_the_trials_reserved_for_the_preregistered_queue():
    assert reserved_trials([], "2026Q4", 13) == Reservation(setting=13, run=0, pending=0, reserved=13)
    plan = build_plan(NOW, FAMILIES, _rich_trials(), [], [], quarter_budget=20, monthly_total=48,
                      trial_budget_per_month=0, floor=2, reserved_setting=13)
    assert plan.quarter_reserved == 13 and plan.total_budget == sum(plan.budget.values()) + plan.unallocated == 7
    assert plan.reservation is not None and plan.reservation.reserved == 13
    # run pre-registered trials use the reservation up; queued pre-registrations stay covered
    ts = (NOW - pd.Timedelta(days=1)).isoformat()
    prereg = [{"trial": i, "ts": ts, "family": "tsmom", "config_hash": f"h{i}", "status": "preregistered"} for i in (1, 2, 3)]
    ran = [{"trial": 1, "ts": ts, "family": "tsmom", "config_hash": "h1", "status": "evaluated"},
           {"trial": 2, "ts": ts, "family": "tsmom", "config_hash": "zz", "status": "evaluated",
            "preregistration": {"trial": 2}},
           {"trial": 3, "ts": ts, "family": "trend", "config_hash": "h9", "status": "evaluated"}]   # not pre-registered
    r = reserved_trials(prereg + ran, "2026Q4", 13)
    assert (r.run, r.pending, r.reserved) == (2, 1, 11)
    assert reserved_trials(prereg, "2026Q4", 0).reserved == 3          # a queued pre-registration is always held
    assert reserved_trials(prereg + ran, "2027Q1", 13).reserved == 13  # another quarter's rows do not count
    plan = build_plan(NOW, FAMILIES, prereg + ran, [], [], quarter_budget=20, monthly_total=48,
                      trial_budget_per_month=0, floor=2, reserved_setting=13)
    assert plan.quarter_used == 3 and plan.quarter_reserved == 11 and plan.total_budget == 20 - 3 - 11


def test_grid_allowance_is_zero_while_the_label_grid_is_paused(tmp_path):
    kw: dict[str, Any] = {"quarter_budget": 20, "monthly_total": 48, "trial_budget_per_month": 12, "floor": 2}
    assert set(build_plan(NOW, FAMILIES, [], [], [], grid_paused=True, **kw).grid_budget.values()) == {0}
    assert max(build_plan(NOW, FAMILIES, [], [], [], **kw).grid_budget.values()) > 0
    ctx = _ctx(tmp_path, label_grid_paused=True, trial_budget_per_month=6)
    _plan(pd.Timestamp.now("UTC") - pd.Timedelta(days=1), {f: 6 for f in FAMILIES}).save(tmp_path / PLAN_FILE)
    out = monthly_research(ctx, pd.Timestamp.now("UTC"))
    assert "paused" in out and ctx.trials.n_trials == 0       # a plan with grid trials does not unpause it
    research_director(ctx, pd.Timestamp.now("UTC"))
    saved = ResearchPlan.load(tmp_path / PLAN_FILE)
    assert saved is not None and set(saved.grid_budget.values()) == {0}


def test_monthly_grid_cannot_spend_the_reserved_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_walk_forward", _fake_walk_forward)
    ctx = _ctx(tmp_path, trial_budget_per_month=6, label_grid_paused=False, reserved_trials_quarter=13)
    slot = pd.Timestamp.now("UTC").floor("min")
    out = monthly_research(ctx, slot)                      # no plan: the flat 6 per family, until 20 - 13 is spent
    assert ctx.trials.n_trials == 7 and [out[f]["trials"] for f in FAMILIES] == [6, 1] + [0] * (len(FAMILIES) - 2)
    assert "held for the pre-registered queue" in out["budget"]
    assert all(str(r["rationale"]).startswith(GRID_RATIONALE) for r in ctx.trials._rows())


def test_settings_and_the_hypotheses_doc_agree_on_the_retired_families():
    s = load_settings().research
    text = load_hypotheses()
    assert text is not None and retired_drift(s.retired_families, text, FAMILIES) == []
    doc, problems = hypotheses_retired(text, FAMILIES)
    assert not problems and set(doc) == {"breakout", "intraday_momentum", "mean_reversion", "session_open", "trend"}
    assert {e.family for e in s.retired_families} == set(doc) and "tsmom" not in doc
    assert s.reserved_trials_quarter == 13                  # docs/research/preregistration-2027Q1.md: 13 planned of 20
    assert load_hypotheses("/nonexistent/hypotheses.md") is None and ATTRIBUTION_FILE == REPORT_FILE


def test_the_consistency_check_catches_drift_and_a_doc_edit_never_moves_a_trial():
    assert retired_drift(RETIRED, HYP, FAMILIES) == []
    renamed = HYP.replace("| ID | Idea | Status |", "| Ref | Idea | State |")
    assert any("lacks ID, Idea and Status" in p for p in retired_drift(RETIRED, renamed, FAMILIES))
    assert any("no '## B.' section" in p for p in retired_drift(RETIRED, HYP.replace("## B.", "## Z."), FAMILIES))
    assert retired_drift(RETIRED, None, FAMILIES)
    trials_off = HYP.replace("| #3 (#40) |", "| #3, #4 (#40) |")
    assert any("trials [3, 4] vs settings [3]" in p for p in retired_drift(RETIRED, trials_off, FAMILIES))
    # R-06 flipped to retired in the doc: the check reports tsmom, the allocation does not move
    flipped = HYP.replace("| failed; superseded by H-01 |", "| retired |")
    drift = retired_drift(RETIRED, flipped, FAMILIES)
    assert drift == ["tsmom: retired in hypotheses.md (R-06) but not in research.retired_families"]
    a, b = _plan_with(retired=RETIRED, hypotheses=HYP), _plan_with(retired=RETIRED, hypotheses=flipped)
    assert a.budget == b.budget and b.budget["tsmom"] > 2 and not next(s for s in b.evidence if s.family == "tsmom").retired
    assert b.hypotheses_drift == drift and a.hypotheses_drift == []
    assert a.hypotheses_sha256 == hashlib.sha256(HYP.encode()).hexdigest() != b.hypotheses_sha256
    # and a doc that retires everything changes nothing without the settings
    assert _plan_with(hypotheses=HYP).budget == _plan_with().budget


def test_plan_is_deterministic_for_the_same_inputs():
    kw: dict[str, Any] = {"attribution": _report({"tsmom": (400, 3.1), "trend": (120, -2.2)}), "hypotheses": HYP,
                          "retired": RETIRED, "promoted": PROMOTED}
    a, b = _plan_with(**kw), _plan_with(**kw)
    assert a.model_dump_json() == b.model_dump_json() and a.moves
    shuffled = build_plan(NOW, list(reversed(FAMILIES)), list(reversed(_rich_trials())), [], [], quarter_budget=100,
                          monthly_total=60, trial_budget_per_month=0, floor=2, cap=None, **kw)
    assert shuffled.model_dump_json() == a.model_dump_json()


def test_missing_attribution_leaves_the_plan_unchanged(tmp_path):
    base = _plan_with()
    for missing in (None, {}, {"as_of": "garbage"}, load_attribution(tmp_path)):
        plan = _plan_with(attribution=missing)
        assert plan.budget == base.budget == plan.evidence_budget and plan.moves == []
        assert [f.model_dump() for f in plan.focus] == [f.model_dump() for f in base.focus]
    (tmp_path / ATTRIBUTION_FILE).write_text("{broken")
    assert load_attribution(tmp_path) is None
    # a plan file written before these fields existed still loads
    old = json.loads(base.model_dump_json())
    for k in ("evidence_budget", "moves", "attribution_as_of", "attribution_note", "hypotheses_note", "quarter_reserved",
              "reservation", "retired_floor", "retired_explore", "reinstate_t", "hypotheses_sha256",
              "hypotheses_drift"):
        old.pop(k)
    assert ResearchPlan.model_validate(old).moves == []


def _clock(monkeypatch: pytest.MonkeyPatch, y: int, m: int, d: int) -> None:
    """Set the registry's clock (what `preregister` stamps; there is no way to pass a time in)."""
    monkeypatch.setattr(registry_mod, "_utcnow", lambda: datetime(y, m, d, tzinfo=timezone.utc))


def test_a_preregistration_written_in_december_for_q1_is_counted_and_run_in_q1(tmp_path, monkeypatch):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    cfg = {"x": 1}
    _clock(monkeypatch, 2026, 12, 30)
    pre = reg.preregister(agent_id="a", family="tsmom", config=cfg, feature_version="f", rationale="Q1 plan",
                          reading_rule="r", queue=True)
    assert pre["target_quarter"] == "2027Q1"                # written within 14 days of Q1's start
    assert reserved_trials(reg._rows(), "2027Q1", 13).pending == 1
    assert pending_preregistration(reg._rows(), "2027Q1", "tsmom", pre["config_hash"]) == pre
    reg._append({"trial": 1, "ts": "2027-01-04T09:00:00+00:00", "family": "tsmom", "config_hash": pre["config_hash"],
                 "status": "evaluated", "config": cfg, "results": {}})
    r = reserved_trials(reg._rows(), "2027Q1", 13)
    assert (r.run, r.pending, r.reserved) == (1, 0, 12)     # was 13 all quarter before target_quarter
    assert reserved_trials(reg._rows(), "2026Q4", 13) == Reservation(setting=13, run=0, pending=0, reserved=13)
    assert pending_preregistration(reg._rows(), "2027Q1", "tsmom", pre["config_hash"]) is None


def test_h01_h02_q1_rows_written_in_december_hold_the_q1_reservation(tmp_path, monkeypatch):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    _clock(monkeypatch, 2026, 12, 5)
    early = reg.preregister(agent_id="a", family="tsmom", config={"h": "H-01"}, feature_version="f", rationale="H-01",
                            reading_rule="r", queue=True, target_quarter="2027Q1")
    _clock(monkeypatch, 2026, 12, 20)
    late = reg.preregister(agent_id="a", family="tsmom", config={"h": "H-02"}, feature_version="f", rationale="H-02",
                           reading_rule="r", queue=True)
    assert early["target_quarter"] == late["target_quarter"] == "2027Q1"
    assert (planned_quarter(datetime(2026, 12, 17, tzinfo=timezone.utc)),
            planned_quarter(datetime(2026, 12, 18, tzinfo=timezone.utc))) == ("2026Q4", "2027Q1")
    q1 = reserved_trials(reg._rows(), "2027Q1", 13)
    assert (q1.run, q1.pending, q1.reserved) == (0, 2, 13)
    assert reserved_trials(reg._rows(), "2026Q4", 0).pending == 0   # they do not hold December's budget
    _clock(monkeypatch, 2026, 12, 5)
    with pytest.raises(ValueError, match="before its target quarter starts"):   # Dec 5 defaults to Q4, under way
        reg.preregister(agent_id="a", family="tsmom", config={}, feature_version="f", rationale="r",
                        reading_rule="r", queue=True)


def test_a_queued_row_written_mid_quarter_does_not_use_the_reservation(tmp_path, monkeypatch):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    _clock(monkeypatch, 2026, 11, 2)
    plain = reg.preregister(agent_id="a", family="tsmom", config={"x": 1}, feature_version="f", rationale="r",
                            reading_rule="r")
    assert plain["queue"] is False and "target_quarter" not in plain       # the default is ad hoc
    with pytest.raises(ValueError, match="before its target quarter starts"):
        reg.preregister(agent_id="a", family="tsmom", config={"x": 2}, feature_version="f", rationale="r",
                        reading_rule="r", queue=True, target_quarter="2026Q4")
    # a row claiming the queue for a quarter already under way (e.g. hand-written just before its own run) is ad hoc
    reg._append({"trial": 1, "ts": "2026-11-02T00:00:00+00:00", "family": "tsmom", "config_hash": "h2",
                 "status": "preregistered", "target_quarter": "2026Q4", "config": {}, "results": {}})
    rows = reg._rows()
    assert not is_queued(rows[-1])
    assert reserved_trials(rows, "2026Q4", 0).pending == 0
    assert pending_preregistration(rows, "2026Q4", "tsmom", "h2") is None


def test_one_trial_uses_one_preregistration_across_quarters(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    for ts, target in (("2026-10-02T00:00:00+00:00", None), ("2026-12-28T00:00:00+00:00", "2027Q1")):
        row = {"trial": 1, "ts": ts, "family": "tsmom", "config_hash": "h", "status": "preregistered", "config": {},
               "results": {}}
        reg._append({**row, "target_quarter": target} if target else row)
    reg._append({"trial": 1, "ts": "2027-01-05T00:00:00+00:00", "family": "tsmom", "config_hash": "h",
                 "status": "evaluated", "config": {}, "results": {}})
    rows = reg._rows()
    # the unlinked January trial uses up the row of its own quarter (2027Q1) first; the Q4 row stays pending in Q4
    q4, q1 = reserved_trials(rows, "2026Q4", 0), reserved_trials(rows, "2027Q1", 0)
    assert (q4.run, q4.pending, q1.run, q1.pending) == (0, 1, 1, 0)


def test_an_early_run_of_a_later_quarters_config_does_not_use_up_its_preregistration(tmp_path, monkeypatch):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    _clock(monkeypatch, 2026, 11, 1)
    p = reg.preregister(agent_id="a", family="tsmom", config={"h": 1}, feature_version="f", rationale="H-01",
                        reading_rule="r", queue=True, target_quarter="2027Q1")
    # an unlinked Q4 trial of H-01's exact config (as an ad-hoc run would record it)
    reg._append({"trial": 2, "ts": "2026-11-15T00:00:00+00:00", "family": "tsmom", "config_hash": p["config_hash"],
                 "status": "evaluated", "config": {"h": 1}, "results": {}})
    rows = reg._rows()
    assert reserved_trials(rows, "2027Q1", 13) == Reservation(setting=13, run=0, pending=1, reserved=13)
    assert pending_preregistration(rows, "2027Q1", "tsmom", p["config_hash"]) == p
    assert later_preregistration(rows, "2026Q4", "tsmom", p["config_hash"]) == p
    assert later_preregistration(rows, "2027Q1", "tsmom", p["config_hash"]) is None


def test_preregister_cannot_be_backdated_and_needs_the_queue_for_a_target_quarter(tmp_path, monkeypatch):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    with pytest.raises(TypeError):
        reg.preregister(agent_id="a", family="x", config={}, feature_version="f", rationale="r",  # type: ignore[call-arg]
                        reading_rule="r", queue=True, target_quarter="2026Q4",
                        now=datetime(2026, 9, 30, tzinfo=timezone.utc))
    _clock(monkeypatch, 2026, 11, 2)
    with pytest.raises(ValueError, match="before its target quarter starts"):
        reg.preregister(agent_id="a", family="x", config={}, feature_version="f", rationale="r", reading_rule="r",
                        queue=True, target_quarter="2026Q4")
    with pytest.raises(ValueError, match="queue=True"):
        reg.preregister(agent_id="a", family="y", config={}, feature_version="f", rationale="r", reading_rule="r",
                        target_quarter="2027Q1")
    assert reg._rows() == []


def test_runners_refuse_a_config_preregistered_for_a_later_quarter(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_walk_forward", _fake_walk_forward)
    ctx = _ctx(tmp_path, trial_budget_quarter=20, reserved_trials_quarter=0, trial_budget_per_month=1,
               label_grid_paused=False)
    q = quarter_of()
    nxt = quarter_of(registry_mod.quarter_start(q) + pd.Timedelta(days=100))
    cfg = dict(SPECIALISTS["tsmom"].default_config)
    ctx.trials.preregister(agent_id="a", family="tsmom", config=cfg, feature_version="f", rationale="next quarter",
                           reading_rule="r", queue=True, target_quarter=nxt)
    out = jobs.make_trial_runner(ctx)("tsmom", {}, "research analyst: early")
    assert f"pre-registered for {nxt}" in out["error"] and ctx.trials.n_trials == 0
    with pytest.raises(TrialBudgetExceeded, match=f"pre-registered for {nxt}"):        # research_pass and discovery
        ctx.trials.check_budget_reserved([("tsmom", cfg)], 20, 0)
    monkeypatch.setattr(jobs, "label_grid", lambda default, step: [dict(default)])     # the grid's one variant
    res = monthly_research(ctx, pd.Timestamp.now("UTC"))
    assert res["tsmom"]["trials"] == 0 and f"pre-registered for {nxt}" in res["tsmom"]["skipped"][0]
    assert not any(r.get("family") == "tsmom" and r.get("status") != "preregistered" for r in ctx.trials._rows())


def test_research_director_job_reads_attribution_settings_and_the_reservation(tmp_path):
    ctx = _ctx(tmp_path)                                   # the real settings: 13 reserved, five retired families
    now = pd.Timestamp.now("UTC")
    report = _report({"tsmom": (400, 3.0)}, as_of=now - pd.Timedelta(hours=1), start=now - pd.Timedelta(days=20))
    (tmp_path / ATTRIBUTION_FILE).write_text(json.dumps(report))
    out = research_director(ctx, now)
    plan = ResearchPlan.load(tmp_path / PLAN_FILE)
    assert plan is not None and plan.attribution_as_of is not None and plan.hypotheses_sha256 is not None
    assert plan.quarter_reserved == out["quarter_reserved"] == 13 and plan.hypotheses_drift == []
    assert plan.total_budget == DEFAULT_QUARTER_BUDGET - 13 == sum(plan.budget.values())
    retired = ("breakout", "intraday_momentum", "mean_reversion", "session_open", "trend")
    assert sum(plan.budget[f] for f in retired) == 1       # retired: ONE exploration trial shared by all five
    assert plan.budget["tsmom"] == 6 and sorted(out["retired"]) == sorted(plan.retired_floor)
    assert out["retired_explore"] == plan.retired_explore and plan.retired_explore in retired
    # no promoted model version in this registry: the attribution rows are not out-of-sample evidence
    assert all(s.attribution is not None and s.attribution.n == 0 for s in plan.evidence)


def test_monthly_grid_reserves_by_the_slots_quarter_not_the_wall_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_walk_forward", _fake_walk_forward)
    ctx = _ctx(tmp_path, trial_budget_per_month=1, label_grid_paused=False, trial_budget_quarter=2,
               reserved_trials_quarter=0)
    slot = pd.Timestamp("2025-09-07 08:00", tz="UTC")
    # two queued pre-registrations of the slot's quarter (2025Q3): the slot's reservation holds both trials
    for i in (1, 2):
        ctx.trials._append({"trial": i, "ts": "2025-08-01T00:00:00+00:00", "family": "tsmom", "config_hash": f"h{i}",
                            "status": "preregistered", "config": {}, "results": {}})
    out = monthly_research(ctx, slot)
    assert ctx.trials.n_trials == 0 and "2 of the quarter held" in out["budget"]


def _analyst_ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JobContext:
    monkeypatch.setattr(jobs, "_walk_forward", _fake_walk_forward)
    ctx = _ctx(tmp_path, trial_budget_quarter=20, reserved_trials_quarter=13)
    for i in range(7):                                     # 20 - 13 reserved = 7 open trials, all spent
        ctx.trials.record(agent_id="a", family="trend", config={"i": i}, feature_version="f1", rationale="r", results={})
    return ctx


def test_research_analyst_is_refused_once_only_reserved_trials_remain(tmp_path, monkeypatch):
    ctx = _analyst_ctx(tmp_path, monkeypatch)
    out = jobs.make_trial_runner(ctx)("trend", {}, "research analyst: one more idea")
    assert "trial budget exceeded" in out["error"]
    assert "13 of the quarter held for the pre-registered queue" in out["error"]
    assert ctx.trials.n_trials == 7


def test_research_analyst_runs_a_preregistered_trial_from_the_reservation(tmp_path, monkeypatch, queued_prereg):
    ctx = _analyst_ctx(tmp_path, monkeypatch)
    # trend is retired and its seven trials this quarter used the retired families' shared exploration trial: only
    # the pending pre-registration lets the analyst run it
    assert "trend" in {e.family for e in ctx.settings.research.retired_families}
    cfg = dict(SPECIALISTS["trend"].default_config)
    pre = queued_prereg(ctx.trials, agent_id="a", family="trend", config=cfg, feature_version="f1", rationale="Q1 plan",
                                 reading_rule="DSR >= 0.95")
    run = jobs.make_trial_runner(ctx)
    out = run("trend", {}, "research analyst: the pre-registered trend trial")
    assert out.get("trial") == 8 and "error" not in out
    row = ctx.trials.row(8)
    assert row is not None and row["preregistration"]["trial"] == pre["trial"]
    r = reserved_trials(ctx.trials._rows(), quarter_of(), 13)
    assert (r.run, r.pending, r.reserved) == (1, 0, 12)
    # that pre-registration is used: running the same config again is an ordinary trial, refused
    assert "held for the pre-registered queue" in run("trend", {}, "again")["error"]


def test_research_analyst_refuses_a_retired_family_once_the_shared_exploration_trial_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_walk_forward", _fake_walk_forward)
    ctx = _ctx(tmp_path, trial_budget_quarter=20, reserved_trials_quarter=0)    # open budget is not the limit
    run = jobs.make_trial_runner(ctx)
    out = run("trend", {}, "research analyst: explore a retired idea")
    assert out.get("trial") == 1                            # the quarter's ONE shared exploration trial
    refused = run("breakout", {}, "research analyst: another retired idea")
    assert "breakout is retired" in refused["error"] and "1 retired-family trial(s)" in refused["error"]
    assert "trend is retired" in run("trend", {"max_bars": 7}, "again")["error"]
    assert ctx.trials.n_trials == 1
    assert run("tsmom", {}, "an active family").get("trial") == 2   # active families are not affected
    # a family the director's plan reinstated is back on the open budget
    monkeypatch.setattr(jobs, "_reinstated", lambda ctx, slot: {"breakout"})
    assert run("breakout", {}, "reinstated by attribution").get("trial") == 3


def test_screening_families_get_no_director_grid_or_analyst_trials(tmp_path):
    """asia_drift (H-04) runs only its pre-registered screen (research.yml): the director, the label grid, the monthly
    loop and the research analyst never plan or spend a trial on a family under its screen."""
    assert SPECIALISTS["asia_drift"].screening and "asia_drift" not in jobs.research_families()
    assert set(jobs.research_families()) == {f for f, c in SPECIALISTS.items() if not c.screening}
    ctx = _ctx(tmp_path)
    out = jobs.make_trial_runner(ctx)("asia_drift", {}, "research analyst: try H-04")
    assert "pre-registered screen" in out["error"] and ctx.trials.n_trials == 0
