"""The TradingView chart path retired on 2026-11-02 (owner: "remove the TradingView chart part ... but keep learning
from ideas shared by the authors"). What must still hold after the removal: the engine starts and closes bars with
every registered family, every shipped arm loads (specialist arms build their family), a family that needs
TradingView values proposes nothing (fails closed), the replica and ordinary agents keep their own rule, and the
authors' ideas collector stays."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from goldbot.engine.agent_candidates import AgentCandidates
from goldbot.research import arms
from goldbot.research.arm_rules_specialist import build_specialist

ROOT = Path(__file__).resolve().parents[1]
RETIRED = ("goldbot.data.tv_pull", "goldbot.data.tv_wire", "goldbot.data.tv_alias", "goldbot.data.tv_reconcile",
           "goldbot.data.tv_csv", "goldbot.data.tv_catalog", "goldbot.engine.tv_context", "goldbot.specialists.tv_event",
           "goldbot.features.tradingview", "goldbot.features.tv_plots", "goldbot.research.tv_repaint",
           "goldbot.research.tv_grid", "goldbot.ops.tv_pull_health", "goldbot.webhook")


@pytest.mark.parametrize("module", RETIRED)
def test_a_retired_tradingview_chart_module_is_gone(module):
    path = ROOT.joinpath(*module.split("."))           # checked on disk: an editable install elsewhere can still serve it
    assert not path.with_suffix(".py").exists() and not path.is_dir()


def test_the_authors_ideas_collector_stays():
    ideas = importlib.import_module("goldbot.data.tv_ideas")
    assert callable(ideas.main) and callable(ideas.check_tv_ideas)


@pytest.mark.parametrize("spec", ["exploration_arms.yaml", "spec_arms.yaml"])
def test_every_shipped_arm_loads_and_every_specialist_arm_builds_its_family(spec):
    loaded = arms.load_arms(ROOT / "config" / spec)
    assert loaded or spec == "spec_arms.yaml"          # discovered arms: none registered yet
    for arm in loaded:
        if arm.rule == "specialist":
            built = build_specialist(arm)
            assert built.timeframe == arm.timeframe and not built.requires_tv, arm.id


class _Agent:
    def __init__(self, agent_id: str, *, requires_tv: bool = False, replica: bool = False):
        self.agent_id, self.requires_tv = agent_id, requires_tv
        self.replica_params = {"rule": "x"} if replica else None

    def candidates(self, m: pd.DataFrame, X: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"idx": [len(m) - 1], "side": [1]})


class _Replica:
    def candidates(self, agent, fr) -> pd.DataFrame:
        return pd.DataFrame({"idx": [len(fr.m) - 1], "side": [-1]})


def test_a_tradingview_dependent_agent_proposes_nothing_and_the_others_keep_their_own_rule():
    fr = SimpleNamespace(m=pd.DataFrame({"x": [1, 2, 3]}), X=pd.DataFrame({"x": [1, 2, 3]}))
    plain, tv, rep = _Agent("plain"), _Agent("tv", requires_tv=True), _Agent("rep", replica=True)
    holder = AgentCandidates()
    out = holder.candidates(fr, [plain, tv, rep])                  # type: ignore[list-item]
    assert out["plain"]["side"].tolist() == [1] and out["tv"].empty and out["rep"].empty   # no replica source yet
    holder.replica = _Replica()                                    # type: ignore[assignment]
    assert holder.candidates(fr, [rep])["rep"]["side"].tolist() == [-1]                   # type: ignore[list-item]


@pytest.mark.integration
def test_the_engine_starts_and_closes_bars_with_every_registered_family(tmp_path):
    from goldbot.allocator.rules import RuleAllocator
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.engine import ConstantModel, Engine, EngineConfig
    from goldbot.execution.broker import Tick
    from goldbot.execution.paper import PaperBroker
    from goldbot.specialists.base import SPECIALISTS

    agents = [cls() for name, cls in sorted(SPECIALISTS.items()) if not cls.screening]
    assert agents and not any(a.requires_tv for a in agents)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path)),
                 PaperBroker(equity=10_000), agents, {a.agent_id: ConstantModel(p=0.6) for a in agents},
                 allocator=RuleAllocator())
    ticks = synthetic_ticks("2025-03-03", "2025-03-05", ticks_per_minute=1, seed=7)
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
    assert eng.tick_errors_total == 0 and eng.state.stage.name != "HALTED"
    assert not [m for m in sys.modules if m in RETIRED]     # nothing on the engine's path loads a retired module


def test_the_local_tradingview_chart_probe_and_its_recorded_frames_are_gone():
    """The Mac websocket probe (scripts/tv_local) was the spike for the chart pull; it goes with the chart path."""
    assert not (ROOT / "scripts" / "tv_local").exists()
    assert not (ROOT / "tests" / "fixtures" / "tv").exists()


def test_the_feed_health_row_carries_no_webhook_latency():
    """The webhook went with the chart path, so the dashboard's feed row no longer has a latency for it."""
    from goldbot.api.schema import FeedHealth
    assert "webhook_p99_latency_s" not in FeedHealth.model_fields
