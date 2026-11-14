"""Quant review follow-up (2026-10-11): one predicate, `registry.gate_pass_counts`, decides whether a row's recorded
gate pass counts, and every consumer of `results.gates.passed` uses it. A pass counts only when the row also records
the research cost treatment (the swap it charged and its cost source): a pass from before swap was charged is not a
pass of the swap-charged configuration. Consumers: the phase gate's backtest evidence (`best_backtest_trial`), gap
watch's research-ready founders, the quarterly CPCV job, research_pass `--cpcv` and the director's holdout slots."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.ops.gates_phase import best_backtest_trial
from goldbot.research.registry import TrialRegistry, costs_recorded, gate_pass_counts
from goldbot.specialists import SPECIALISTS

ROOT = Path(__file__).resolve().parents[1]
NOW = pd.Timestamp("2026-10-10 23:55", tz="UTC")
COSTS: dict[str, Any] = {"swap": {"long_usd_per_lot": -60.0, "short_usd_per_lot": 20.0, "triple_weekday": 2,
                                  "server_tz": "Europe/Athens", "contract_oz": 100.0},
                         "cost_source": "PRIORS ONLY: test"}


def _results(passed: bool = True, costs: bool = True, **extra: Any) -> dict[str, Any]:
    return {"gates": {"passed": passed, "checks": []}, **(COSTS if costs else {}), **extra}


# ---------------------------------------------------------------------------------------------- the predicate
def test_a_gate_pass_counts_only_with_the_swap_charged_and_the_cost_source_stated():
    assert gate_pass_counts({"results": _results()})
    assert not gate_pass_counts({"results": _results(costs=False)})                 # pre-swap pass
    assert not gate_pass_counts({"results": _results(passed=False)})
    assert not gate_pass_counts({"results": {**_results(), "cost_source": "  "}})   # no stated source
    assert not gate_pass_counts({"results": {**_results(), "swap": None}})          # swap not charged
    assert not gate_pass_counts({"results": {**COSTS, "gates": {"passed": "true"}}})  # only a real True
    assert not gate_pass_counts({}) and not gate_pass_counts({"results": None})
    assert costs_recorded(COSTS) and not costs_recorded({})


# ---------------------------------------------------------------------------------------------- phase gate input
def _bt_row(trial: int, dsr: float, costs: bool, status: str = "evaluated") -> dict[str, Any]:
    return {"trial": trial, "family": "session_open", "status": status,
            "results": _results(costs=costs, model_filtered={"n": 600, "dsr": dsr, "mean_ret": 0.002, "max_dd": 0.05})}


def test_the_phase_gates_best_backtest_trial_ignores_a_pre_swap_row_whatever_its_dsr():
    pre_swap, charged = _bt_row(1, 0.99, costs=False), _bt_row(2, 0.60, costs=True)
    assert best_backtest_trial([pre_swap, charged]) is charged
    assert best_backtest_trial([pre_swap]) is None                  # no evidence rather than pre-swap evidence
    assert best_backtest_trial([_bt_row(3, 0.99, costs=True, status="holdout"), charged]) is charged


def test_research_pass_cpcv_refuses_a_pre_swap_gate_pass_as_failed(tmp_path: Path):
    spec = importlib.util.spec_from_file_location("research_pass_gpc", ROOT / "scripts/research_pass.py")
    assert spec is not None and spec.loader is not None
    rp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rp)
    reg = TrialRegistry(tmp_path / "trials.jsonl")
    cfg = dict(SPECIALISTS["session_open"].default_config)
    reg.record(agent_id="a", family="session_open", config=cfg, feature_version="v", rationale="r", results=_results())
    reg.record(agent_id="a", family="session_open", config={**cfg, "asia_range_max_atr_d": 1.5}, feature_version="v",
               rationale="pre-swap", results=_results(costs=False))
    args = argparse.Namespace(cpcv=[1, 2], diagnostic=False)
    with pytest.raises(SystemExit, match=r"trial\(s\) 2 failed the gates"):
        rp._cpcv(args, pd.DataFrame(), reg, 0.37, None, None, "priors", None)


# ---------------------------------------------------------------------------------------------- the spent holdout
def test_a_holdout_pass_without_its_cost_treatment_is_filtered_and_the_configuration_cannot_be_re_scored(
        tmp_path: Path):
    """HANDOFF (M46): a discovery survivor's holdout pass recorded before swap was charged does not count, and the
    holdout is spent: `holdout_scored` still refuses a second look, so the configuration cannot be re-scored."""
    from goldbot.research.registry import FROM_DISCOVERY_KEY, config_hash
    reg = TrialRegistry(tmp_path / "r.jsonl")
    cfg = {"x": 1, FROM_DISCOVERY_KEY: True}
    h = config_hash(cfg)
    rows = [{"status": "evaluated", "family": "tsmom", "config_hash": h, "results": _results()},
            {"status": "holdout", "family": "tsmom", "config_hash": h, "results": {"holdout_verdict": {"passed": True}}}]
    reg._rows = lambda: rows                                                          # type: ignore[method-assign]
    assert not reg.passed_gates("tsmom", cfg)
    assert reg.holdout_scored("tsmom", cfg)
