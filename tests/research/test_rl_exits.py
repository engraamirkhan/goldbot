"""RL-X1 offline fitted-Q exits (goldbot/research/rl_exits.py): exact replay of hold / close / breakeven on top of the
T2 template, net R rewards through the simulator's costs and realised swaps, full-feedback transitions, the fixed
exits as the reported baseline, the shuffled-state control, a frozen policy with a sha, never live."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.research import decision_cost as DC
from goldbot.research import rl_exits as X
from goldbot.research import windows as WN
from goldbot.research.dense_labels import TEMPLATES
from goldbot.research.rl_sim import SimCosts
from goldbot.research.windows import HoldoutSealed

ROOT = Path(__file__).resolve().parents[2]
FREE = SimCosts.w2().model_copy(update={"rt_bp": 0.0, "overshoot_bp": 0.0})   # cost-free replay for exact numbers
W2 = SimCosts.w2()
REC = "srv3-cost-0123456789abcdef"


def _ep(close: list[float], *, high: list[float] | None = None, low: list[float] | None = None,
        open_: list[float] | None = None, swap: float = 0.0, day: str = "2014-01-06") -> X.Episode:
    c = np.array(close, float)
    o = np.array(open_ if open_ is not None else np.r_[0.0, c[:-1]], float)
    h = np.array(high if high is not None else np.maximum(o, c), float)
    lo = np.array(low if low is not None else np.minimum(o, c), float)
    dec = pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=10)
    return X.Episode(open_r=o, high_r=h, low_r=lo, close_r=c, swap_r=np.full(len(c), swap), k=0.01, day=day,
                     rt_bp=0.0, cost_version=REC, decision_utc=dec, end_utc=dec + pd.Timedelta(hours=len(c)))


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
    ep = ep.model_copy(update={"rt_bp": None, "cost_version": None})
    net, acts = X.rollout(ep, lambda f, ok: X.CLOSE, costs=W2)
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
    stopped = _ep([0.2, -1.5]).model_copy(update={"rt_bp": None})
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
def _world(n: int, seed: int, *, reverting: bool, start: str = "2014-01-06") -> list[X.Episode]:
    """Half the episodes rally to +1.2 R by bar 2 and then collapse through the stop (a close there pays); the rest
    drift. With `reverting` False every path is a driftless random walk."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        if reverting and i % 2 == 0:
            c = [0.5, 1.2, 0.6, -0.2, -1.3, 0.0, 0.0, 0.0]
        else:
            c = list(np.clip(np.cumsum(rng.normal(0, 0.25, 8)), -0.95, 1.9))
        out.append(_ep(c, day=str(pd.Timestamp(start) + pd.Timedelta(days=i // 4))[:10]))
    return out


def _booked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str = "read-1") -> dict[str, Any]:
    fam = "rl-x1:test"                     # a fixture family: real ones are registered with the signed study plan
    monkeypatch.setitem(WN.READ_ONCE_FAMILIES, fam, frozenset({WN.Window.LEARN}))
    led = tmp_path / "reads.jsonl"
    WN.record_read_once(led, fam, WN.Window.LEARN, ref={"read_key": key})
    return {"booked_read": key, "family": fam, "ledger": led}


def test_the_policy_learns_to_exit_a_planted_reversal_and_beats_the_fixed_exits_and_the_control(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    spec = X.SPEC.model_copy(update={"boot": 200})
    pol, rep = X.run(_world(200, 1, reverting=True), _world(120, 2, reverting=True, start="2015-01-05"), spec,
                     costs=FREE, **_booked(tmp_path, monkeypatch))
    assert rep.delta_mean > 0.5 and rep.delta_per_day_lo is not None and rep.delta_per_day_lo > 0
    assert rep.shuffled_delta_mean < rep.delta_mean
    assert rep.action_counts["close"] + rep.action_counts["breakeven"] > 0
    assert rep.live is False and pol.live is False


def test_on_a_random_walk_the_policy_finds_no_edge_over_the_fixed_exits(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch):
    spec = X.SPEC.model_copy(update={"boot": 200})
    _, rep = X.run(_world(300, 3, reverting=False), _world(200, 4, reverting=False, start="2015-01-05"), spec,
                   costs=FREE, **_booked(tmp_path, monkeypatch))
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
    long_, short = X.episodes_from_bars(ent, bars, costs=W2)
    assert len(long_) == len(short) == 24
    assert long_.open_r[0] == 0.0
    np.testing.assert_allclose(short.close_r, -long_.close_r)
    np.testing.assert_allclose(short.high_r, -long_.low_r)


def test_episodes_outside_learn_are_refused_the_holdout_first():
    bars = _bars("2025-10-02", 50)
    ent = pd.DataFrame({"side": [1], "decision_utc": bars["visible_at"].iloc[3], "entry": 1500.0, "risk_usd": 2.0,
                        "horizon_end_utc": bars["visible_at"].iloc[20], "usable": True})
    with pytest.raises(HoldoutSealed):
        X.episodes_from_bars(ent, bars, costs=W2)
    b18 = _bars("2018-01-02", 50)
    ent18 = ent.assign(decision_utc=b18["visible_at"].iloc[3], horizon_end_utc=b18["visible_at"].iloc[20])
    with pytest.raises(ValueError, match="learn"):
        X.episodes_from_bars(ent18, b18, costs=W2)


def test_episodes_need_tier_usable_a_or_a_consistent_rows():
    bars = _bars("2014-03-03", 100)
    dec = bars["visible_at"].iloc[10]
    ent = pd.DataFrame({"side": [1], "decision_utc": dec, "entry": 1500.0, "risk_usd": 2.0,
                        "horizon_end_utc": dec + pd.Timedelta(hours=24)})
    with pytest.raises(ValueError, match="usable"):
        X.episodes_from_bars(ent, bars, costs=W2)
    with pytest.raises(ValueError, match="usable"):
        X.episodes_from_bars(ent.assign(usable=False), bars, costs=W2)
    with pytest.raises(ValueError, match="A-consistent"):
        X.episodes_from_bars(ent.assign(usable=True, tier="B"), bars, costs=W2)
    assert X.FIT_TIERS == __import__("goldbot.research.feature_store", fromlist=["x"]).INTRADAY_TIERS


def _charged(bars: pd.DataFrame, rt: list[float], version: str = REC, swap_rate: float = 2e-5) -> pd.DataFrame:
    dec = bars["visible_at"].iloc[10]
    return pd.DataFrame({"side": [1] * len(rt), "decision_utc": dec, "entry": 1500.0, "risk_usd": 2.0,
                         "horizon_end_utc": dec + pd.Timedelta(hours=24), "usable": True, "tier": "A",
                         "cost_version": version, "cost_rt_bp": rt, "swap_rate": swap_rate})


def test_an_entry_charged_at_the_cost_of_record_pays_its_own_round_trip_not_the_simulator_constant():
    bars = _bars("2014-03-03", 100)
    ep, = X.episodes_from_bars(_charged(bars, [23.5]), bars, costs=W2)
    assert ep.rt_bp == 23.5 and ep.cost_version == REC
    c = SimCosts.w2()
    net, _ = X.rollout(ep, lambda f, ok: X.CLOSE, costs=c)
    assert net == pytest.approx(ep.close_r[0] - ep.k * 23.5)
    stopped = X.Episode(**{**ep.model_dump(), "close_r": np.array([0.2, -1.5]), "open_r": np.array([0.0, 0.2]),
                           "high_r": np.array([0.2, 0.2]), "low_r": np.array([0.0, -1.5]), "swap_r": np.zeros(2)})
    assert X.fixed_exit(stopped, costs=c) == pytest.approx(-1 - stopped.k * (23.5 + c.overshoot_bp))


def test_point_cost_entries_keep_the_simulator_round_trip():
    bars = _bars("2014-03-03", 100)
    ep, = X.episodes_from_bars(_charged(bars, [2.0], version="w2-point"), bars, costs=W2)
    assert ep.rt_bp is None and ep.cost_version is None
    net, _ = X.rollout(ep, lambda f, ok: X.CLOSE, costs=W2)
    assert net == pytest.approx(ep.close_r[0] - SimCosts.w2().exit_cost_r(np.array([ep.k]), np.array([False]))[0])


def test_cost_of_record_entries_refuse_a_second_slippage_term_the_record_already_carries():
    bars = _bars("2014-03-03", 100)
    with pytest.raises(ValueError, match="slippage"):
        X.episodes_from_bars(_charged(bars, [23.5]), bars,
                             costs=SimCosts.w2().model_copy(update={"slippage_ub95_bp": 1.0}))
    with pytest.raises(ValueError, match="cost_rt_bp"):
        X.episodes_from_bars(_charged(bars, [float("nan")]), bars, costs=W2)
    with pytest.raises(ValueError, match="swap"):
        X.episodes_from_bars(_charged(bars, [23.5]).drop(columns="swap_rate"), bars, costs=W2)


# ------------------------------------------------------------------------------------------------ quant on #489
def test_a_gate_read_refuses_costs_none_and_point_cost_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    book = _booked(tmp_path, monkeypatch)
    train, test = _world(40, 1, reverting=True), _world(20, 2, reverting=True, start="2015-01-05")
    with pytest.raises(DC.CostNotOfRecord, match="no costs"):
        X.run(train, test, costs=None, **book)                        # type: ignore[arg-type]
    point = [e.model_copy(update={"cost_version": None, "rt_bp": None}) for e in test]
    with pytest.raises(DC.CostNotOfRecord, match="cost of record"):
        X.run(train, point, costs=FREE, **book)
    with pytest.raises(WN.ReadNotBooked):
        X.run(train, test, costs=FREE, **{**book, "booked_read": "another-read"})


def test_a_template_replay_reproduces_the_t2_fixed_exit_and_the_stop_less_t0_time_exit():
    for c in ([0.2, -0.5, -1.4, 0.0], [0.5, 1.5, 2.5, 0.0], [0.3, 0.1, 0.4]):
        ep = _ep(c)
        assert X.fixed_template(ep, TEMPLATES[2], costs=W2) == pytest.approx(X.fixed_exit(ep, costs=W2))
    assert X.fixed_template(_ep([0.2, -3.0, 0.4]), TEMPLATES[0], costs=FREE) == pytest.approx(0.4)
    # T7 moves the stop to the entry once a bar's high reached 1 R; the next bar's dip exits at 0
    assert X.fixed_template(_ep([1.1, -0.5, 2.0]), TEMPLATES[7], costs=FREE) == pytest.approx(0.0)
    # T5 trails 1 R under the best high of the earlier bars
    assert X.fixed_template(_ep([0.5, 1.8, 0.5]), TEMPLATES[5], costs=FREE) == pytest.approx(0.8)


def test_baseline_ii_chooses_its_template_on_training_episodes_only():
    train = _world(80, 1, reverting=True)
    name, f_a = X.baseline_ii(train, _world(40, 2, reverting=True, start="2015-01-05"), costs=FREE)
    other, f_b = X.baseline_ii(train, _world(40, 9, reverting=False, start="2015-01-05"), costs=FREE)
    means = {t.name: np.mean([X.fixed_template(e, t, costs=FREE) for e in train]) for t in TEMPLATES}
    assert name == other == max(means, key=lambda k: means[k])
    assert len(f_a) == len(f_b) == 40


def test_the_report_carries_baseline_ii_delta_vs_the_best_fixed_exit_and_a_doubled_cost_read(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    spec = X.SPEC.model_copy(update={"boot": 0})
    c = W2
    train = [e.model_copy(update={"rt_bp": 5.0, "swap_r": np.full(len(e), 0.002)}) for e in _world(80, 1, reverting=True)]
    test = [e.model_copy(update={"rt_bp": 5.0, "swap_r": np.full(len(e), 0.002)})
            for e in _world(40, 2, reverting=True, start="2015-01-05")]
    _, rep = X.run(train, test, spec, costs=c, **_booked(tmp_path, monkeypatch))
    assert rep.cost_version == REC and rep.booked_read == "read-1"
    assert rep.best_template in {t.name for t in TEMPLATES}
    assert rep.delta_vs_best == pytest.approx(rep.policy_mean - max(rep.fixed_mean, rep.best_fixed_mean))
    assert rep.fixed_mean_2x < rep.fixed_mean and rep.policy_mean_2x < rep.policy_mean
    assert rep.delta_vs_best_2x == pytest.approx(rep.policy_mean_2x - max(rep.fixed_mean_2x, rep.best_fixed_mean_2x))
    double = [X.at_cost_multiple(e, 2.0) for e in test]
    c2 = X.cost_multiple(c, 2.0)
    assert rep.fixed_mean_2x == pytest.approx(np.mean([X.fixed_exit(e, spec, costs=c2) for e in double]))
    assert c2.overshoot_bp == 2 * c.overshoot_bp and double[0].rt_bp == 10.0
    np.testing.assert_allclose(double[0].swap_r, 2 * test[0].swap_r)


def test_a_test_episode_inside_the_training_horizon_plus_embargo_is_refused(tmp_path: Path,
                                                                            monkeypatch: pytest.MonkeyPatch):
    train = _world(40, 1, reverting=True)
    close = _world(20, 2, reverting=True, start=train[-1].day)        # decides the day the last train episode ends
    with pytest.raises(ValueError, match="embargo"):
        X.run(train, close, costs=FREE, **_booked(tmp_path, monkeypatch))
    no_time = [e.model_copy(update={"decision_utc": None}) for e in _world(20, 2, reverting=True, start="2015-01-05")]
    with pytest.raises(ValueError, match="decision_utc"):
        X.run(train, no_time, costs=FREE, **_booked(tmp_path / "second", monkeypatch))


def test_the_swap_of_record_per_night_is_the_trainers_cost_parts_swap_per_night_on_the_same_row():
    from goldbot.data.calendar import DEFAULT_SESSIONS
    from goldbot.research.sr_v3_train import CostTable, cost_parts
    bars = _bars("2014-03-03", 100)
    dec = bars["visible_at"].iloc[10]
    sess = str(DEFAULT_SESSIONS.session_label(pd.DatetimeIndex([dec]))[0])
    table = CostTable(version=REC, rt={("XAUUSD", sess, "1h"): 4.0}, swap={("XAUUSD", "2014-03"): (-3e-5, -1e-5)})
    rows = pd.DataFrame({"instrument": "XAUUSD", "tf": "1h", "decision_utc": [dec], "side": [1], "entry": [1500.0],
                         "risk_usd": [2.0], "max_hold_s": [86_400.0]})
    parts = cost_parts(rows, table)
    assert parts["nights_full"].iloc[0] > 0
    ent = _charged(bars, [float(parts["cost_rt_bp"].iloc[0])], swap_rate=float(table.swap_rate(rows)[0]))
    ep, = X.episodes_from_bars(ent, bars, costs=W2)
    ts = bars["ts_utc"]
    a = int(ts.searchsorted(dec))
    from goldbot.labels.triple_barrier import rollover_nights
    sw = W2.swap
    n = rollover_nights(pd.DatetimeIndex(ts.iloc[a:a + len(ep)]), pd.DatetimeIndex(bars["visible_at"].iloc[a:a + len(ep)]),
                        sw.server_tz, sw.triple_weekday, sw.seven_night).astype(float)
    per_night = ep.swap_r[n > 0] / n[n > 0]
    np.testing.assert_allclose(per_night, parts["cost_c"].iloc[0] / parts["nights_full"].iloc[0], rtol=1e-12)


def test_the_state_and_greedy_action_at_a_bar_ignore_every_later_bar():
    eps = _world(80, 5, reverting=True)
    pol = X.fit(X.transitions(eps, costs=FREE))
    ep = eps[0]
    j = 2
    later = ep.model_copy(update={k: np.r_[getattr(ep, k)[:j + 1], getattr(ep, k)[j + 1:] + 7.0]
                                  for k in ("open_r", "high_r", "low_r", "close_r")})
    for be in (0, 1):
        np.testing.assert_array_equal(X._features(ep, j, be), X._features(later, j, be))
        assert pol.act(X._features(ep, j, be), X.allowed(ep, j, be)) == pol.act(X._features(later, j, be),
                                                                                 X.allowed(later, j, be))


# ------------------------------------------------------------------------------------------------ never live
@pytest.mark.parametrize("mod", ["rl_exits", "rl_sim", "seq1", "seq1_embed", "agg_v1", "learn_labels",
                                 "history_runs"])
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
