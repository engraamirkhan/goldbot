"""BACKLOG 91: retired families, allocated or screened, take no trial beyond their ONE shared exploration trial a quarter.

Design rule (settings research.retired_families; director.RETIRED_EXPLORATION): "The retired families SHARE 1
exploration trial a quarter". Two gaps let a retired, screened family (`box_break_close` R-15, `sweep_absorb` R-14)
take more:

1. `TrialRegistry.check_budget_reserved` refused a retired family only on a contingent slot, so an open-budget
   research.yml / research_pass run citing a `rule_ref` went through. It is now refused unless no retired-family trial
   is stamped in the quarter yet (`retired_quarter_trials` < `RETIRED_EXPLORATION`); only a pending queued
   pre-registration of exactly that configuration lifts the refusal.
2. `build_plan` counted the quarter's retired trials over the allocated families only, so a screened family's trial
   (H-53, the 2027Q1 shared trial on `box_break_close` 1h) did not use the shared trial up. It now counts every family
   in research.retired_families and reports the trial as used.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import RetiredFamily, load_settings
from goldbot.ops import jobs
from goldbot.research import registry as registry_mod
from goldbot.research.director import RETIRED_EXPLORATION, build_plan
from goldbot.research.registry import RetiredExplorationUsed, TrialBudgetExceeded, TrialRegistry

IN_Q1 = datetime(2027, 1, 5, tzinfo=timezone.utc)
H53 = {"timeframe": "1h"}
H53_RULE = "prereg-2026-10-19-h50-followup.md#h-53"
ON = date(2026, 10, 19)
SCREENED = [RetiredFamily(family="box_break_close", hypothesis_id="R-15", retired=ON, reason="H-50 STOP", trials=[89]),
            RetiredFamily(family="sweep_absorb", hypothesis_id="R-14", retired=ON, reason="H-48 STOP", trials=[88])]
ALLOCATED = [RetiredFamily(family="mean_reversion", hypothesis_id="R-01", retired=date(2026, 10, 10),
                           reason="no edge", trials=[3])]
RETIRED = {e.family for e in SCREENED + ALLOCATED}


def _reg(tmp_path: Path) -> TrialRegistry:
    return TrialRegistry(tmp_path / "registry.jsonl")


def _stamp(reg: TrialRegistry, family: str, config: dict[str, Any], when: datetime) -> None:
    """A recorded trial of `family`, stamped at `when` (record() stamps the wall clock)."""
    reg.record(agent_id=f"{family}-a", family=family, config=config, feature_version="v1", rationale="earlier",
               results={})
    rows = reg._rows()
    rows[-1]["ts"] = when.isoformat()
    reg.path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _check(reg: TrialRegistry, jobs_: list[tuple[str, dict[str, Any]]], **kw: Any) -> Any:
    return reg.check_budget_reserved(jobs_, 20, 0, IN_Q1, retired=kw.pop("retired", RETIRED), **kw)


# ---------------------------------------------------------------------------------------------- the registry guard
def test_the_first_retired_family_trial_of_a_quarter_runs_from_the_open_budget(tmp_path: Path):
    quarter, preregs = _check(_reg(tmp_path), [("box_break_close", H53)])
    assert quarter == "2027Q1" and preregs == [None]


def test_a_screened_retired_family_is_refused_once_another_retired_family_used_the_quarters_trial(tmp_path: Path):
    reg = _reg(tmp_path)
    _stamp(reg, "sweep_absorb", {"timeframe": "15m"}, IN_Q1)
    with pytest.raises(RetiredExplorationUsed) as exc:
        _check(reg, [("box_break_close", H53)])
    msg = str(exc.value)
    assert "box_break_close is retired" in msg and "2027Q1" in msg and "1 retired-family trial" in msg
    assert f"share {RETIRED_EXPLORATION} exploration trial" in msg
    assert isinstance(exc.value, TrialBudgetExceeded)          # research_pass turns it into a SystemExit


def test_an_allocated_retired_familys_trial_also_uses_the_shared_trial(tmp_path: Path):
    reg = _reg(tmp_path)
    _stamp(reg, "mean_reversion", {"timeframe": "1h"}, IN_Q1)
    with pytest.raises(RetiredExplorationUsed):
        _check(reg, [("box_break_close", H53)])


def test_a_retired_trial_of_an_earlier_quarter_does_not_use_this_quarters_trial(tmp_path: Path):
    reg = _reg(tmp_path)
    _stamp(reg, "sweep_absorb", {"timeframe": "15m"}, datetime(2026, 12, 20, tzinfo=timezone.utc))
    assert _check(reg, [("box_break_close", H53)])[1] == [None]


def test_two_retired_family_jobs_in_one_run_are_refused_together(tmp_path: Path):
    with pytest.raises(RetiredExplorationUsed, match="2 retired-family job"):
        _check(_reg(tmp_path), [("box_break_close", H53), ("box_break_close", {"timeframe": "4h"})])


def test_a_live_family_is_not_touched_by_the_guard(tmp_path: Path):
    reg = _reg(tmp_path)
    _stamp(reg, "sweep_absorb", {"timeframe": "15m"}, IN_Q1)
    assert _check(reg, [("tsmom", {"timeframe": "4h"})])[1] == [None]


def test_a_pending_queued_preregistration_of_the_exact_config_lifts_the_refusal(tmp_path: Path,
                                                                               monkeypatch: pytest.MonkeyPatch):
    reg = _reg(tmp_path)
    monkeypatch.setattr(registry_mod, "_utcnow", lambda: datetime(2026, 12, 1, tzinfo=timezone.utc))
    row = reg.preregister(agent_id="bbc-a", family="box_break_close", config=H53, feature_version="v1",
                          rationale="queued", reading_rule="r", plan={}, queue=True, target_quarter="2027Q1")
    _stamp(reg, "sweep_absorb", {"timeframe": "15m"}, IN_Q1)
    _, preregs = _check(reg, [("box_break_close", H53)])
    assert preregs[0] is not None and preregs[0]["ts"] == row["ts"]


def test_without_a_retired_list_the_guard_reads_the_settings_and_fails_closed(tmp_path: Path):
    assert "box_break_close" in {e.family for e in load_settings().research.retired_families}
    reg = _reg(tmp_path)
    _stamp(reg, "sweep_absorb", {"timeframe": "15m"}, IN_Q1)
    with pytest.raises(RetiredExplorationUsed):
        reg.check_budget_reserved([("box_break_close", H53)], 20, 0, IN_Q1)


# ---------------------------------------------------------------------------------------------- the director
def _plan(trials: list[dict[str, Any]], now: pd.Timestamp) -> Any:
    fams = jobs.research_families()
    assert "box_break_close" not in fams and "mean_reversion" in fams     # screened vs allocated
    return build_plan(now, fams, trials, [], [], quarter_budget=100, monthly_total=60, trial_budget_per_month=0,
                      floor=2, retired=SCREENED + ALLOCATED, promoted={})


def test_the_director_counts_a_screened_retired_familys_trial_as_the_quarters_shared_trial():
    now = pd.Timestamp(IN_Q1) + pd.Timedelta(days=10)
    h53 = {"trial": 90, "family": "box_break_close", "ts": pd.Timestamp(IN_Q1).isoformat(), "status": "evaluated",
           "rule_ref": H53_RULE, "results": {}}
    before = _plan([], now)
    assert before.retired_explore == "mean_reversion" and before.retired_floor["mean_reversion"] == 1
    after = _plan([h53], now)
    assert after.retired_explore is None and set(after.retired_floor.values()) == {0}
    assert after.retired_trials_quarter == 1
    reasons = [r for f in after.focus if f.family == "mean_reversion" for r in f.reasons]
    assert any("shared exploration trial was used this quarter (1 retired-family trial" in r for r in reasons)


def test_the_plan_reports_the_shared_trial_as_used_even_with_no_allocated_retired_family():
    now = pd.Timestamp(IN_Q1) + pd.Timedelta(days=10)
    h53 = {"trial": 90, "family": "box_break_close", "ts": pd.Timestamp(IN_Q1).isoformat(), "status": "evaluated",
           "results": {}}
    plan = build_plan(now, jobs.research_families(), [h53], [], [], quarter_budget=100, monthly_total=60,
                      trial_budget_per_month=0, floor=2, retired=SCREENED, promoted={})
    assert plan.retired_trials_quarter == 1 and plan.retired_explore is None


def test_a_second_discover_run_on_a_retired_family_in_the_same_quarter_is_refused(tmp_path: Path):
    """Quant review 2026-11-14: a discovery trial is recorded as `discovery_<family>`, so the guard compared
    `discovery_session_open` with the retired `session_open` and never matched. The prefix is stripped on both the
    request and the quarter's stamped trials: the first discovery run on session_open uses the shared trial, a second
    is refused."""
    reg = _reg(tmp_path)
    _stamp(reg, "discovery_session_open", {"timeframe": "15m"}, IN_Q1)
    with pytest.raises(RetiredExplorationUsed) as exc:
        _check(reg, [("discovery_session_open", {"timeframe": "1h"})], retired=RETIRED | {"session_open"})
    assert "discovery_session_open is retired" in str(exc.value)


def test_a_first_discover_run_on_a_retired_family_runs_from_the_open_budget(tmp_path: Path):
    quarter, preregs = _check(_reg(tmp_path), [("discovery_session_open", {"timeframe": "1h"})],
                              retired=RETIRED | {"session_open"})
    assert quarter == "2027Q1" and preregs == [None]
