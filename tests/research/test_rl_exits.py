"""RL-X1 offline fitted-Q exits (goldbot/research/rl_exits.py): exact replay of hold / close / breakeven on top of the
T2 template, net R rewards through the simulator's costs and realised swaps, full-feedback transitions, the fixed
exits as the reported baseline, the shuffled-state control, a frozen policy with a sha, never live."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research import rl_exits as X
from goldbot.research import run_log as RL
from goldbot.research.rl_sim import SimCosts
from goldbot.research.windows import HoldoutSealed

ROOT = Path(__file__).resolve().parents[2]
FREE = SimCosts.w2().model_copy(update={"rt_bp": 0.0, "overshoot_bp": 0.0})   # cost-free replay for exact numbers


def _ep(close: list[float], *, high: list[float] | None = None, low: list[float] | None = None,
        open_: list[float] | None = None, swap: float = 0.0, day: str = "2014-01-06") -> X.Episode:
    c = np.array(close, float)
    o = np.array(open_ if open_ is not None else np.r_[0.0, c[:-1]], float)
    h = np.array(high if high is not None else np.maximum(o, c), float)
    lo = np.array(low if low is not None else np.minimum(o, c), float)
    return X.Episode(open_r=o, high_r=h, low_r=lo, close_r=c, swap_r=np.full(len(c), swap), k=0.01, day=day)


# ------------------------------------------------------------------------------------------------ exact replay
def test_holding_reproduces_the_template_stop_target_and_time_exits():
    assert X.fixed_exit(_ep([0.2, -0.5, -1.4, 0.0]), costs=FREE) == -1.0            # stop at -1 R
    assert X.fixed_exit(_ep([0.5, 1.5, 2.5, 0.0]), costs=FREE) == 2.0               # target at +2 R
    assert X.fixed_exit(_ep([0.3, 0.1, 0.4]), costs=FREE) == pytest.approx(0.4)      # time exit at the last close


def test_a_gapped_stop_fills_at_the_open_a_gapped_target_at_the_target_and_a_two_sided_bar_is_a_stop():
    assert X.fixed_exit(_ep([0.2, -1.8], open_=[0.0, -1.6]), costs=FREE) == pytest.approx(-1.6)
    assert X.fixed_exit(_ep([0.2, 3.0], open_=[0.0, 2.7]), costs=FREE) == 2.0
    assert X.fixed_exit(_ep([0.2, 0.5], high=[0.3, 2.5], low=[-0.1, -1.2]), costs=FREE) == -1.0


def test_close_takes_the_bar_close_and_pays_the_exit_cost():
    ep = _ep([0.8, -1.5])
    net, acts = X.rollout(ep, lambda f, ok: X.CLOSE)
    assert acts == [X.CLOSE]
    assert net == pytest.approx(0.8 - SimCosts.w2().exit_cost_r(np.array([0.01]), np.array([False]))[0])


def test_breakeven_is_shielded_and_a_return_to_entry_then_exits_at_zero():
    ep = _ep([0.6, 0.3, -0.5, -1.5])
    assert X.allowed(ep, 0, 0).tolist() == [True, True, True]
    assert X.allowed(ep, 0, 1).tolist() == [True, True, False]                    # only once
    assert not X.allowed(_ep([-0.2, 0.1]), 0, 0)[X.BE]                             # never below the entry
    net, acts = X.rollout(ep, lambda f, ok: X.BE if ok[X.BE] else X.HOLD, costs=FREE)
    assert acts[0] == X.BE and net == 0.0


def test_every_held_bar_pays_its_swap_and_the_exit_pays_stop_overshoot():
    ep = _ep([0.1, 0.2, 0.3], swap=0.01)
    assert X.fixed_exit(ep, costs=FREE) == pytest.approx(0.3 - 0.03)
    c = SimCosts.w2()
    stopped = _ep([0.2, -1.5])
    assert X.fixed_exit(stopped, costs=c) == pytest.approx(-1 - c.exit_cost_r(np.array([0.01]), np.array([True]))[0])


def test_transitions_replay_every_allowed_action_of_every_reachable_state():
    ep = _ep([0.6, 0.3, 0.1, 0.2])
    tr = X.transitions([ep], costs=FREE)
    # states (0,0) (1,0) (2,0) with 3 actions each where BE is allowed; breakeven states (1,1) (2,1) with 2
    assert len(tr.A) == 3 + 3 + 3 + 2 + 2
    assert set(tr.A.tolist()) == {X.HOLD, X.CLOSE, X.BE}
    close_rows = tr.A == X.CLOSE
    assert tr.done[close_rows].all()
    np.testing.assert_allclose(sorted(tr.R[close_rows]), sorted([0.6, 0.3, 0.1, 0.3, 0.1]))


# ------------------------------------------------------------------------------------------------ learning
def _world(n: int, seed: int, *, reverting: bool) -> list[X.Episode]:
    """Half the episodes rally to +1.2 R by bar 2 and then collapse through the stop (a close there pays); the rest
    drift. With `reverting` False every path is a driftless random walk."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        if reverting and i % 2 == 0:
            c = [0.5, 1.2, 0.6, -0.2, -1.3, 0.0, 0.0, 0.0]
        else:
            c = list(np.clip(np.cumsum(rng.normal(0, 0.25, 8)), -0.95, 1.9))
        out.append(_ep(c, day=str(pd.Timestamp("2014-01-06") + pd.Timedelta(days=i // 4))[:10]))
    return out


def test_the_policy_learns_to_exit_a_planted_reversal_and_beats_the_fixed_exits_and_the_control():
    spec = X.SPEC.model_copy(update={"boot": 200})
    pol, rep = X.run(_world(200, 1, reverting=True), _world(120, 2, reverting=True), spec, FREE)
    assert rep.delta_mean > 0.5 and rep.delta_per_day_lo is not None and rep.delta_per_day_lo > 0
    assert rep.shuffled_delta_mean < rep.delta_mean
    assert rep.action_counts["close"] + rep.action_counts["breakeven"] > 0
    assert rep.live is False and pol.live is False


def test_on_a_random_walk_the_policy_finds_no_edge_over_the_fixed_exits():
    spec = X.SPEC.model_copy(update={"boot": 200})
    _, rep = X.run(_world(300, 3, reverting=False), _world(200, 4, reverting=False), spec, FREE)
    assert abs(rep.delta_mean) < 0.1


def test_the_policy_is_deterministic_round_trips_with_its_sha_and_score_row_matches_the_batch():
    eps = _world(80, 5, reverting=True)
    a = X.fit(X.transitions(eps, costs=FREE))
    b = X.fit(X.transitions(eps, costs=FREE))
    assert a.sha256 == b.sha256 == a.compute_sha256()
    back = X.load(a.model_dump_json())
    F = X.transitions(eps, costs=FREE).S[:25]
    np.testing.assert_array_equal(back.q(F), a.q(F))
    for i in range(25):
        np.testing.assert_allclose(a.score_row(F[i]), a.q(F)[i], rtol=1e-12)
    with pytest.raises(ValueError, match="sha"):
        X.load(a.model_dump_json().replace(a.sha256, "0" * 64))


def test_a_policy_rebuilds_exactly_from_its_run_log(tmp_path: Path):
    eps = _world(60, 6, reverting=True)
    pol = X.fit(X.transitions(eps, costs=FREE), n_episodes=len(eps))
    log = X.run_log_of(pol, eps, {"delta": 0.1}, costs=FREE)
    RL.append(tmp_path / "runs.jsonl", log)
    back = RL.read(tmp_path / "runs.jsonl")[0]
    assert X.rebuild(back, eps).sha256 == pol.sha256
    with pytest.raises(RL.RebuildMismatch):
        X.rebuild(back, eps[1:])


# ------------------------------------------------------------------------------------------------ bars, windows
def _bars(start: str, n: int) -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="1h", tz="UTC")
    c = 1500 + np.cumsum(np.random.default_rng(0).normal(0, 1.0, n))
    o = np.r_[1500.0, c[:-1]]
    return pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(hours=1), "open": o,
                         "high": np.maximum(o, c) + 0.3, "low": np.minimum(o, c) - 0.3, "close": c})


def test_episodes_start_at_the_bar_after_the_decision_end_by_the_horizon_and_orient_a_short():
    bars = _bars("2014-03-03", 100)
    dec = bars["visible_at"].iloc[10]
    ent = pd.DataFrame({"side": [1, -1], "decision_utc": dec, "entry": bars["open"].iloc[11], "risk_usd": 2.0,
                        "horizon_end_utc": dec + pd.Timedelta(hours=24), "usable": True, "tier": "A"})
    long_, short = X.episodes_from_bars(ent, bars)
    assert len(long_) == len(short) == 24
    assert long_.open_r[0] == 0.0
    np.testing.assert_allclose(short.close_r, -long_.close_r)
    np.testing.assert_allclose(short.high_r, -long_.low_r)


def test_episodes_outside_learn_are_refused_the_holdout_first():
    bars = _bars("2025-10-02", 50)
    ent = pd.DataFrame({"side": [1], "decision_utc": bars["visible_at"].iloc[3], "entry": 1500.0, "risk_usd": 2.0,
                        "horizon_end_utc": bars["visible_at"].iloc[20], "usable": True})
    with pytest.raises(HoldoutSealed):
        X.episodes_from_bars(ent, bars)
    b18 = _bars("2018-01-02", 50)
    ent18 = ent.assign(decision_utc=b18["visible_at"].iloc[3], horizon_end_utc=b18["visible_at"].iloc[20])
    with pytest.raises(ValueError, match="learn"):
        X.episodes_from_bars(ent18, b18)


def test_episodes_need_tier_usable_a_or_a_consistent_rows():
    bars = _bars("2014-03-03", 100)
    dec = bars["visible_at"].iloc[10]
    ent = pd.DataFrame({"side": [1], "decision_utc": dec, "entry": 1500.0, "risk_usd": 2.0,
                        "horizon_end_utc": dec + pd.Timedelta(hours=24)})
    with pytest.raises(ValueError, match="usable"):
        X.episodes_from_bars(ent, bars)
    with pytest.raises(ValueError, match="usable"):
        X.episodes_from_bars(ent.assign(usable=False), bars)
    with pytest.raises(ValueError, match="A-consistent"):
        X.episodes_from_bars(ent.assign(usable=True, tier="B"), bars)
    assert X.FIT_TIERS == __import__("goldbot.research.feature_store", fromlist=["x"]).INTRADAY_TIERS


# ------------------------------------------------------------------------------------------------ never live
@pytest.mark.parametrize("mod", ["rl_exits", "rl_sim", "seq1", "seq1_embed", "agg_v1", "learn_labels", "run_log"])
def test_the_history_models_import_nothing_on_the_order_path(mod: str):
    tree = ast.parse((ROOT / f"goldbot/research/{mod}.py").read_text())
    names = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m.startswith(("goldbot.engine", "goldbot.execution", "goldbot.risk", "goldbot.ops",
                                 "goldbot.api", "goldbot.telegram")) for m in names)


def test_nothing_in_the_engine_execution_or_risk_imports_the_history_models():
    hits = [p for d in ("engine", "execution", "risk", "ops", "api") for p in (ROOT / "goldbot" / d).rglob("*.py")
            if any(m in p.read_text() for m in ("rl_exits", "rl_sim", "seq1", "agg_v1", "learn_labels"))]
    assert hits == []
