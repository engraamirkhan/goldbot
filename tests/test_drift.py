"""Drift and health (design: Drift and health; rows M26, M27): PSI against the training distribution, calibration on
the trailing trades, a residual CUSUM that halts an agent, and the system halt (two agents halted, or a 30-day drawdown
above 1.5x backtest) pending the owner's review. Entries only; exits are never touched."""
import json
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import DriftSettings, load_settings
from goldbot.data.store import Store
from goldbot.engine import Engine, EngineConfig
from goldbot.engine.shadow import ShadowTrade
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import health
from goldbot.ops.jobs import JobContext, drift_watch
from goldbot.ops.run import drift_review_cli
from goldbot.research.drift import (
    assess,
    drawdown,
    psi,
    reference_bins,
    residual_cusum,
    system_halt_reasons,
    trade_residuals,
)
from goldbot.research.model import MetaLabelModel
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.population import Population
from goldbot.research.registry import TrialRegistry
from goldbot.risk import AccountState, Intent, RiskGate
from goldbot.telegram.approvals import ApprovalCenter
from tests.test_health import make_ctx

S = DriftSettings()
NOW = pd.Timestamp("2027-01-08 23:40", tz="UTC")
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")


def _trade(i: int, win: bool, p: float = 0.55, ret: float | None = None) -> ShadowTrade:
    ts = NOW - pd.Timedelta(days=20) + pd.Timedelta(hours=i)
    return ShadowTrade(version="v1", agent_id="a1", side=1, entry_ts=ts, entry=2400.0, stop=2398.0, target=2403.0,
                       max_bars=8, timeframe="1h", p=p, taken=True, threshold=0.5, exit_ts=ts + pd.Timedelta(hours=1),
                       exit=2403.0 if win else 2398.0, barrier="target" if win else "stop",
                       ret=ret if ret is not None else (0.00125 if win else -0.000833))


# ---------------------------------------------------------------------------------------------- the measures
def test_psi_is_small_for_the_same_distribution_and_large_for_a_shift():
    rng = np.random.default_rng(0)
    train = pd.DataFrame({"x": rng.normal(0, 1, 5000)})
    ref = reference_bins(train)["x"]
    assert psi(ref, rng.normal(0, 1, 2000)) < 0.05
    assert psi(ref, rng.normal(1.0, 1, 2000)) > 0.25
    assert psi(ref, np.full(500, np.nan)) > 0.25                  # a feature that went missing is drift too


def test_cusum_alarms_on_persistent_underperformance_only():
    rng = np.random.default_rng(1)
    assert not residual_cusum(list(rng.normal(0, 1, 200)))[0]
    assert residual_cusum(list(rng.normal(-1.0, 1, 30)))[0]


def test_residuals_are_zero_mean_when_p_is_right_and_negative_when_overconfident():
    rng = np.random.default_rng(2)
    fair = [_trade(i, bool(rng.uniform() < 0.55)) for i in range(2000)]
    assert abs(np.mean(trade_residuals(fair))) < 0.06
    bad = [_trade(i, bool(rng.uniform() < 0.30)) for i in range(2000)]
    assert np.mean(trade_residuals(bad)) < -0.4


def test_drawdown_of_compounded_returns():
    assert drawdown([0.1, -0.5, 0.2]) == pytest.approx(0.5)
    assert drawdown([]) == 0.0


# ---------------------------------------------------------------------------------------------- one agent
class _Model:
    """Stand-in with a training reference and gain importance."""

    def __init__(self, ref: dict[str, Any]) -> None:
        self.feature_ref = ref

    def design(self, X: pd.DataFrame) -> pd.DataFrame:
        return X

    def importance(self) -> pd.Series:
        return pd.Series([2.0, 1.0], index=["x", "y"])


def test_assess_sizes_down_on_feature_drift_or_bad_calibration_and_halts_on_cusum():
    rng = np.random.default_rng(3)
    train = pd.DataFrame({"x": rng.normal(0, 1, 3000), "y": rng.normal(0, 1, 3000)})
    model = _Model(reference_bins(train))
    calm = pd.DataFrame({"x": rng.normal(0, 1, 300), "y": rng.normal(0, 1, 300)})
    good = [_trade(i, bool(rng.uniform() < 0.55)) for i in range(120)]
    h = assess("a1", "v1", model=model, live=calm, closed_taken=good, recent_taken=good, backtest_dd=0.1, s=S)
    assert h.size_factor == 1.0 and not h.halted and set(h.psi) == {"x", "y"}
    shifted = calm.assign(x=calm["x"] + 2.0)
    h = assess("a1", "v1", model=model, live=shifted, closed_taken=good, recent_taken=good, backtest_dd=0.1, s=S)
    assert h.psi_size_down == ["x"] and h.size_factor == S.size_down_factor and not h.halted
    over = [_trade(i, bool(rng.uniform() < 0.25), p=0.6) for i in range(120)]   # p 0.6, wins 25%
    h = assess("a1", "v1", model=model, live=calm, closed_taken=over, recent_taken=over, backtest_dd=0.1, s=S)
    assert h.ece is not None and h.ece > S.ece_size_down and h.size_factor == S.size_down_factor
    assert h.halted and h.cusum_alarm


def test_without_a_reference_or_enough_rows_psi_is_skipped_not_guessed():
    h = assess("a1", "v1", model=_Model({}), live=pd.DataFrame({"x": [1.0]}), closed_taken=[], recent_taken=[],
               backtest_dd=None, s=S)
    assert h.psi == {} and h.size_factor == 1.0 and "no training reference" in h.notes[0]


def test_system_halts_on_two_halted_agents_or_a_drawdown_beyond_one_and_a_half_backtest():
    from goldbot.research.drift import AgentHealth
    a = AgentHealth(agent_id="a", version="1", halted=True)
    b = AgentHealth(agent_id="b", version="2", halted=True)
    assert system_halt_reasons([a], S) == []
    assert "2 agents halted" in system_halt_reasons([a, b], S)[0]
    c = AgentHealth(agent_id="c", version="3", dd_30d=0.16, backtest_dd=0.10)
    assert "30-day drawdown" in system_halt_reasons([c], S)[0]
    assert system_halt_reasons([c.model_copy(update={"dd_30d": 0.14})], S) == []


def test_every_fitted_model_carries_its_training_reference():
    rng = np.random.default_rng(4)
    X = pd.DataFrame({"x": rng.normal(size=400), "y": rng.normal(size=400)})
    y = pd.Series((X["x"] + rng.normal(size=400) > 0).astype(int))
    m = MetaLabelModel(feature_names=["x", "y"], params={"n_estimators": 10, "verbose": -1}).fit(X, y)
    assert set(m.feature_ref) == {"x", "y"} and len(m.feature_ref["x"]["props"]) == len(m.feature_ref["x"]["edges"]) + 2


# ---------------------------------------------------------------------------------------------- the job, sticky halts
def _ctx(tmp_path) -> JobContext:
    return JobContext(settings=load_settings(), store=Store(tmp_path / "data"), state_dir=tmp_path,
                      models=ModelRegistry(tmp_path / "models"), trials=TrialRegistry(tmp_path / "t.jsonl"),
                      accounts=[], population=Population(tmp_path / "population.json"))


def _book(tmp_path, version: str, agent: str, trades: list[ShadowTrade]) -> None:
    from goldbot.engine.shadow import ShadowBook
    book = ShadowBook(tmp_path)
    book.track(version, NOW - pd.Timedelta(days=60))
    for t in trades:
        book.books[version].closed.append(t.model_copy(update={"version": version, "agent_id": agent}))
    book.save(NOW)


def test_drift_watch_halts_an_underperforming_champion_and_keeps_it_halted_until_review(tmp_path):
    ctx = _ctx(tmp_path)
    e = ctx.models.add_challenger(MetaLabelModel(feature_names=["x"]), family="tsmom", agent_id="agent-x",
                                  backtest={"max_dd": 0.5}, now=NOW - pd.Timedelta(days=90))
    ctx.models.promote(e.version, now=NOW - pd.Timedelta(days=60))
    rng = np.random.default_rng(5)
    _book(tmp_path, e.version, "agent-x", [_trade(i, bool(rng.uniform() < 0.15), p=0.6) for i in range(60)])
    out = drift_watch(ctx, NOW)
    assert out["halted"] == ["agent-x"] and not out["system_halt"]
    d = json.loads((tmp_path / "drift.json").read_text())
    assert d["halted"]["agent-x"]["version"] == e.version
    _book(tmp_path, e.version, "agent-x", [])                    # even with a clean record next day: still halted
    (tmp_path / "shadow_book.json").unlink(missing_ok=True)
    assert drift_watch(ctx, NOW + pd.Timedelta(days=1))["halted"] == ["agent-x"]
    assert drift_review_cli(["--clear", "checked fills and news"], state_dir=tmp_path) == 0
    assert json.loads((tmp_path / "drift.json").read_text())["halted"] == {}


# ---------------------------------------------------------------------------------------------- engine, gate, health
def test_the_gate_blocks_entries_during_a_system_halt():
    st = AccountState(equity=1e4, balance_closed_hwm=1e4, day_start_equity=1e4, week_start_equity=1e4, open_positions=0,
                      margin_used=0, last_tick_age_s=1, spread_points=20, drift_halt=True)
    it = Intent(agent_id="a", side=1, p=0.62, target_atr=1.5, stop_atr=1.0, atr_usd=4.0, cost_atr=0.1, multiplier=1.0,
                price=2400.0)
    assert RiskGate().check(it, st).reasons == ["drift_system_halt"]


def test_engine_reads_the_drift_report_and_fails_closed_on_a_corrupt_one(tmp_path):
    pb = PaperBroker(equity=10_000)
    tick = Tick(ts_utc=T0, bid=2400.0, ask=2400.2)
    pb.on_tick(tick)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)), pb, [], {},
                 ApprovalCenter({1}))
    eng._refresh_account(tick)
    assert not eng.state.drift_halt                                       # no report yet: nothing restricted
    (tmp_path / "drift.json").write_text(json.dumps({"system_halt": {"reasons": ["x"]}, "halted": {}}))
    eng._refresh_account(tick)
    assert eng.state.drift_halt
    (tmp_path / "drift.json").write_text("{torn")
    eng._drift_cache = (-2.0, {})
    eng._refresh_account(tick)
    assert eng.state.drift_halt                                           # unreadable: entries halted


def test_health_fails_on_a_system_halt_and_warns_on_agent_halts(tmp_path):
    ctx = make_ctx(tmp_path)
    assert health.check_drift(ctx).status == "ok"
    (tmp_path / "drift.json").write_text(json.dumps({"ts": ctx.now.isoformat(), "halted": {"a": {}}, "size_factor": {}}))
    assert health.check_drift(ctx).status == "warn"
    (tmp_path / "drift.json").write_text(json.dumps({"ts": ctx.now.isoformat(), "system_halt": {"reasons": ["2 agents"]}}))
    c = health.check_drift(ctx)
    assert c.status == "fail" and "drift-review" in c.reason
