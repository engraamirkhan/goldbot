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

from goldbot.config import load_settings
from goldbot.data.store import Store
from goldbot.ops import gap_watch as G
from goldbot.ops.accounts import Account
from goldbot.ops.gates_phase import best_backtest_trial
from goldbot.ops.jobs import JobContext, cpcv_quarterly
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.population import Population
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


# ---------------------------------------------------------------------------------------------- gap watch
def test_gap_watch_founds_no_agent_from_a_pre_swap_gate_pass(tmp_path: Path):
    pop = Population(tmp_path / "population.json")
    pop.ensure_founders(NOW - pd.Timedelta(days=60))
    base = SPECIALISTS["trend"].default_config
    pre_swap = {"trial": 7, "family": "trend", "config": {**base, "target_atr": base["target_atr"] * 1.2},
                "status": "evaluated", "results": _results(costs=False)}
    charged = {"trial": 8, "family": "trend", "config": {**base, "target_atr": base["target_atr"] * 1.1},
               "status": "evaluated", "results": _results()}
    assert [g.gap_id for g in G.research_ready([pre_swap, charged], pop)] == ["research_ready:trial8"]


# ---------------------------------------------------------------------------------------------- CPCV
def test_the_quarterly_cpcv_job_re_evaluates_no_pre_swap_gate_pass(tmp_path: Path):
    acc = Account(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=None,
                  terminal_path="", server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100, enabled=True)
    ctx = JobContext(settings=load_settings(), store=Store(tmp_path / "data"), state_dir=tmp_path,
                     models=ModelRegistry(tmp_path / "models"), trials=TrialRegistry(tmp_path / "trials.jsonl"),
                     accounts=[acc], population=Population(tmp_path / "population.json"))
    ctx.trials.record(agent_id="a", family="session_open", config=dict(SPECIALISTS["session_open"].default_config),
                      feature_version="v", rationale="a pass from before swap was charged", results=_results(costs=False))
    out = cpcv_quarterly(ctx, pd.Timestamp("2025-10-05 14:00", tz="UTC"))
    assert out["trials"] == {} and not out["errors"]
    assert "0 trial(s) passed the gates" in Path(out["report"]).read_text()


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
