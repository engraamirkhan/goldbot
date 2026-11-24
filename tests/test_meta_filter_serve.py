"""Serving the weekly self-retrain's ACTIVE per-arm filters at the exploration send (goldbot/engine/meta_filter.py;
owner 2026-11-24, docs/research/prereg-2026-11-24-self-retrain.md section 3: "It may only drop candidates of a demo
strategy arm before the RiskGate. It never adds an order, raises a size, or touches a control, a `replay_only` arm, a
risk limit or a live account"). Synthetic engine (tests/test_exploration_engine.py) and models written into aux.db
`self_retrain_models` the way the weekly job writes them."""
from __future__ import annotations

import ast
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import arm_registry, exploration, long_bars, meta_filter
from goldbot.engine.exploration import Arm, Pending
from goldbot.research import feature_snapshots as fs
from goldbot.research import self_retrain as sr
from goldbot.research.arms import Arm as SpecArm
from tests.test_exploration_engine import CLOSE, LONG, QUALIFIED, _actions, _arm, _at, _engine, _sent
from tests.test_exploration_engine import (
    _rules as _rules,  # noqa: F401  (autouse: the test arms' fixed rules)
)

ROOT = Path(__file__).parents[1]
ACTIVE_FROM = pd.Timestamp("2025-03-01 22:00", tz="UTC")     # before CLOSE: the model is in force


def _model(p: float, tau: float = 0.5, inputs: tuple[str, ...] = sr.INPUTS) -> sr.LogitModel:
    """A filter whose score is `p` for every vector (zero weights, the intercept logit(p)), cut at `tau`."""
    n = len(inputs)
    return sr.LogitModel(inputs=inputs, medians=(0.0,) * n, means=(0.0,) * n, scales=(1.0,) * n, coef=(0.0,) * n,
                         intercept=math.log(p / (1 - p)), tau=tau)


def _store(tmp_path: Path, arm_id: str, model: sr.LogitModel, *, status: str = "active", broker: str = "icm",
           activation: pd.Timestamp = ACTIVE_FROM, version: str | None = None,
           spec: str = sr.SPEC_VERSION) -> str:
    v = version or f"{spec}-{broker}-test-{arm_id}"
    with exploration.ledger_db(str(tmp_path)).write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO self_retrain_models (model_version, run_id, broker, arm_id, spec_version, status, "
            "status_reason, cutoff_ns, activation_ns, n_train, n_features, n_weights, model, oos, recorded_ns, "
            "status_ns) VALUES (?, 'run', ?, ?, ?, ?, 'passed', ?, ?, 300, 40, 41, ?, '{}', 0, 0)",
            (v, broker, arm_id, spec, status, int((activation - pd.Timedelta(days=2)).value), int(activation.value),
             model.model_dump_json()))
    return v


def _serving(eng: Any, on: bool = True) -> Any:
    eng.cfg.meta_filter_serve = on
    return eng


def _obs(tmp_path: Path) -> list[dict[str, Any]]:
    import sqlite3
    with exploration.ledger_db(str(tmp_path)).connection() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM exploration_obs ORDER BY arm_id, decision_ns")]


def _spy_gate(eng: Any) -> list[Any]:
    seen: list[Any] = []
    check = eng.gate.check

    def spy(*a: Any, **k: Any) -> Any:
        seen.append(a)
        return check(*a, **k)

    eng.gate.check = spy
    return seen


def _role_arm(arm_id: str, role: str, *, control_mode: str | None = None, tf: str = "15m") -> Arm:
    extra: dict[str, Any] = {"control_mode": control_mode} if control_mode else {}
    spec = SpecArm(id=arm_id, rule=LONG, timeframe=tf, max_open=5,  # type: ignore[arg-type]
                   params={"stop_usd": 5.0, "target_r": 2.0, "hold_bars": 4}, role=role,  # type: ignore[arg-type]
                   qualification=QUALIFIED if role == "strategy" else None, **extra)
    return Arm(spec=spec, style="intraday", control_share=0.05 if role == "control" else None)


def _sig(arm: Arm, ts: pd.Timestamp = CLOSE, **kw: Any) -> Pending:
    return Pending(arm_id=arm.arm_id, side=1, bar_ts=ts, stop_usd=5.0, target_usd=10.0, hold_bars=4, **kw)


# ------------------------------------------------------------------------------------------------ the drop
def test_a_candidate_scoring_below_its_arms_active_cut_is_dropped_before_the_riskgate_and_recorded_untaken(tmp_path):
    """Dropped: journalled with its score and model; exploration_obs keeps it (taken 0, blocked_reason
    `self_retrain:<model_version>`, quant R11; mu 0, so it is replay-labelled like any other), meta_p and
    model_version recorded; the gate never asked."""
    eng, pb = _engine(tmp_path, [_arm("a")])
    _serving(eng)
    v = _store(tmp_path, "a", _model(0.2, tau=0.5))
    seen = _spy_gate(eng)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert not seen and not pb.positions() and not _sent(eng)
    [row] = _actions(eng, "exploration_refused:")
    assert row["action"] == "exploration_refused:self_retrain:" + v and meta_filter.refusal(v) == "self_retrain:" + v
    assert row["meta_p"] == pytest.approx(0.2, abs=1e-4) and row["model_version"] == v
    [obs] = _obs(tmp_path)
    assert obs["taken"] == 0 and obs["blocked_reason"] == "self_retrain:" + v and obs["mu"] == 0.0
    assert obs["meta_p"] == pytest.approx(0.2) and obs["model_version"] == v


def test_a_candidate_at_or_above_the_cut_is_sent_unchanged_and_its_record_carries_the_score(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")])
    _serving(eng)
    v = _store(tmp_path, "a", _model(0.7, tau=0.5))
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [order] = _sent(eng)
    assert order["action"] == "exploration:executed:auto"
    [pos] = pb.positions()
    assert pos.lots == pytest.approx(0.01)                     # the minimum lot: never resized
    [obs] = _obs(tmp_path)
    assert obs["taken"] == 1 and obs["blocked_reason"] is None
    assert obs["meta_p"] == pytest.approx(0.7) and obs["model_version"] == v


def test_with_serving_switched_off_an_active_model_changes_nothing(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")])
    _serving(eng, False)
    _store(tmp_path, "a", _model(0.2, tau=0.5))
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 1
    [obs] = _obs(tmp_path)
    assert obs["meta_p"] is None and obs["model_version"] is None


def test_serving_is_off_by_default_and_production_switches_it_with_the_settings():
    from goldbot.config import load_settings
    from goldbot.engine import EngineConfig
    from goldbot.ops.services.engine import meta_filter_serve
    assert EngineConfig(account_id="x", broker_name="icm").meta_filter_serve is False
    s = load_settings()
    sr_cfg = s.research.self_retrain

    def with_(enabled: bool, serve: bool) -> Any:
        return s.model_copy(update={"research": s.research.model_copy(update={
            "self_retrain": sr_cfg.model_copy(update={"enabled": enabled, "serve": serve})})})
    assert meta_filter_serve(with_(True, True)) is True
    assert meta_filter_serve(with_(True, False)) is False
    assert meta_filter_serve(with_(False, True)) is False


def test_a_model_of_another_broker_or_not_active_is_never_served(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")])
    _serving(eng)
    _store(tmp_path, "a", _model(0.2), broker="vantage")
    _store(tmp_path, "a", _model(0.2), status="shadow", version="shadow-a")
    _store(tmp_path, "a", _model(0.2), status="demoted", version="demoted-a")
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 1


def test_a_model_is_not_served_before_its_activation_time(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")])
    _serving(eng)
    _store(tmp_path, "a", _model(0.2), activation=CLOSE + pd.Timedelta(hours=1))
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 1
    [obs] = _obs(tmp_path)
    assert obs["meta_p"] is None


# ------------------------------------------------------------------------------------------------ which arms
@pytest.mark.parametrize("role, control_mode", [("control", None), ("control", "replay_only"), ("learning", None),
                                                ("owner_experiment", None), ("cost", None)])
def test_controls_replay_only_learning_cost_and_owner_experiment_arms_are_never_filtered(role, control_mode):
    """Only a demo STRATEGY arm (the card's words). GD-1's GD1D/GD1P are learning-class evidence arms: excluded, the
    card does not cover them; controls are the random baseline the gate judges the filter against."""
    arm = _role_arm("x", role, control_mode=control_mode)
    assert not meta_filter.eligible(arm, _sig(arm))
    strat = _role_arm("y", "strategy")
    assert meta_filter.eligible(strat, _sig(strat))


@pytest.mark.parametrize("tf", ["1m", "5m", "1d", "1w"])
def test_sub_15m_and_long_timeframe_arms_are_not_filtered(tf):
    """1m/5m: the vector's build does not fit the fast send path; 1d/1w: the engine's 90 000 minutes hold no 120
    decision bars, so the trainer has no snapshot of them either."""
    arm = _role_arm("x", "strategy", tf=tf)
    assert not meta_filter.eligible(arm, _sig(arm))


def test_a_pyramid_add_and_a_non_gold_arm_are_not_filtered():
    arm = _role_arm("x", "strategy")
    assert not meta_filter.eligible(arm, _sig(arm, group_id="g", add_no=1))
    other = arm.model_copy(update={"spec": arm.spec.model_copy(update={
        "params": {**arm.spec.params, "instrument": "US500"}})})
    assert not meta_filter.eligible(other, _sig(other))


def test_a_control_with_an_active_model_row_is_sent_as_before(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("c", control_share=1.0)])
    _serving(eng)
    _store(tmp_path, "c", _model(0.01))
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert not _actions(eng, "exploration_refused:self_retrain:")
    [obs] = _obs(tmp_path)
    assert obs["meta_p"] is None


def test_an_account_that_is_not_demo_is_never_filtered(tmp_path, monkeypatch):
    """`risk.gate.is_demo` (the one demo predicate) read at the signal: not demo, the model is never consulted."""
    eng, pb = _engine(tmp_path, [_arm("a")])
    _serving(eng)
    _store(tmp_path, "a", _model(0.2))
    monkeypatch.setattr(arm_registry, "demo_now", lambda _eng: False)
    tick = _at(eng, pb, CLOSE)
    ex = eng.exploration
    assert ex is not None
    assert meta_filter.check(eng, ex, ex.arms["a"], _sig(ex.arms["a"]), tick.ts_utc) is None


# ------------------------------------------------------------------------------------------------ the vector
def test_the_served_score_is_the_trainers_model_on_the_trainers_snapshot_vector(tmp_path):
    """The same point-in-time function the nightly snapshot uses (`feature_snapshots.decision_features` at the
    candidate's decision) and the trainer's `design` (signed columns, side), scored by the stored model."""
    eng, pb = _engine(tmp_path, [_arm("a")])
    _serving(eng)
    weights = tuple(0.05 * ((i % 7) - 3) for i in range(len(sr.INPUTS)))
    m = _model(0.5, tau=0.0).model_copy(update={"coef": weights, "intercept": 0.1})
    _store(tmp_path, "a", m)
    tick = _at(eng, pb, CLOSE)
    ex = eng.exploration
    assert ex is not None
    arm = ex.arms["a"]
    s = _sig(arm).model_copy(update={"side": -1})
    got = meta_filter.check(eng, ex, arm, s, tick.ts_utc)
    assert got is not None and got.meta_p is not None
    snap = fs.decision_features(eng.bars_1m, "15m", long_bars.origin("15m", s.bar_ts, s.decision_utc))
    assert snap is not None
    want = float(m.score(sr.design(pd.DataFrame([{**snap.features, "side": -1}])))[0])
    assert got.meta_p == pytest.approx(want, rel=1e-12)
    assert snap.available_utc_max <= CLOSE                           # point in time: nothing after the decision


# ------------------------------------------------------------------------------------------------ failing open
def test_a_scoring_error_fails_open_to_the_unfiltered_send_logs_and_publishes_a_health_warning(tmp_path, monkeypatch,
                                                                                               caplog):
    def boom(*_: Any, **__: Any) -> Any:
        raise ZeroDivisionError("bad")

    monkeypatch.setattr(fs, "decision_features", boom)
    eng, pb = _engine(tmp_path, [_arm("a")])
    _serving(eng)
    _store(tmp_path, "a", _model(0.2))
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 1                                      # existing behaviour: sent as without a model
    [obs] = _obs(tmp_path)
    assert obs["meta_p"] is None and obs["taken"] == 1
    assert any("meta filter" in r.getMessage() for r in caplog.records)
    pub = meta_filter.published(eng)
    assert "ZeroDivisionError" in pub["meta_filter_error"] and pub["meta_filter"]["failed_open"] == 1
    from goldbot.ops.checks.engine import meta_filter_part
    got = meta_filter_part(pub, pub["meta_filter_error_at"] + 1)
    assert got is not None
    status, text = got
    assert status == "warn" and "failed open" in text


def test_a_vector_that_cannot_be_built_fails_open_and_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "decision_features", lambda *a, **k: None)
    eng, pb = _engine(tmp_path, [_arm("a")])
    _serving(eng)
    _store(tmp_path, "a", _model(0.2))
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 1
    assert "no feature vector" in meta_filter.published(eng)["meta_filter_error"]


def test_an_unreadable_model_table_fails_open(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_arm("a")])
    _serving(eng)
    _store(tmp_path, "a", _model(0.2))

    def unreadable(*_: Any, **__: Any) -> Any:
        raise RuntimeError("disk")

    monkeypatch.setattr(meta_filter, "load_active", unreadable)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert len(_sent(eng)) == 1
    assert "RuntimeError" in meta_filter.published(eng)["meta_filter_error"]


def test_a_malformed_or_other_spec_model_row_is_skipped_and_the_other_arms_models_still_serve(tmp_path):
    db = exploration.ledger_db(str(tmp_path))
    _store(tmp_path, "a", _model(0.2, inputs=sr.INPUTS[:-1]))                # 39 inputs: not the frozen spec's 40
    _store(tmp_path, "b", _model(0.2), spec="sr-v0")
    v = _store(tmp_path, "c", _model(0.2))
    got, problems = meta_filter.load_active(db, "icm")
    assert set(got) == {"c"} and got["c"].model_version == v
    assert len(problems) == 2 and any("a" in p for p in problems) and any("b" in p for p in problems)


def test_models_are_cached_and_reloaded_at_most_every_reload_interval(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")])
    _serving(eng)
    tick = _at(eng, pb, CLOSE)
    ex = eng.exploration
    assert ex is not None
    arm = ex.arms["a"]
    assert meta_filter.check(eng, ex, arm, _sig(arm), tick.ts_utc) is None        # loaded: no model yet
    _store(tmp_path, "a", _model(0.2))
    soon = tick.ts_utc + pd.Timedelta(seconds=meta_filter.RELOAD_S - 1)
    assert meta_filter.check(eng, ex, arm, _sig(arm), soon) is None               # cached: not re-read
    later = tick.ts_utc + pd.Timedelta(seconds=meta_filter.RELOAD_S)
    got = meta_filter.check(eng, ex, arm, _sig(arm), later)
    assert got is not None and got.drop


# ------------------------------------------------------------------------------------------------ latency
def test_the_filter_adds_under_5_ms_a_candidate_once_its_decisions_vector_is_built(tmp_path, capsys):
    """Target < 5 ms a candidate (owner brief 2026-11-24). Measured: an arm without a model (today: every arm), and an
    arm with one once its (timeframe, decision) vector is built; the vector itself is built once per decision and
    shared by every arm of the timeframe (printed: it is the engine's default frame, ~0.1-0.2 s)."""
    eng, pb = _engine(tmp_path, [_arm("a"), _arm("b")])
    _serving(eng)
    _store(tmp_path, "a", _model(0.7))
    tick = _at(eng, pb, CLOSE)
    ex = eng.exploration
    assert ex is not None
    a, b = ex.arms["a"], ex.arms["b"]
    meta_filter.check(eng, ex, b, _sig(b), tick.ts_utc)                          # the model cache loaded
    t0 = time.perf_counter()
    assert meta_filter.check(eng, ex, a, _sig(a), tick.ts_utc) is not None     # cold: builds the vector
    cold_ms = (time.perf_counter() - t0) * 1000

    def median_ms(arm: Arm, n: int = 200) -> float:
        out = []
        for _ in range(n):
            t = time.perf_counter()
            meta_filter.check(eng, ex, arm, _sig(arm), tick.ts_utc)
            out.append((time.perf_counter() - t) * 1000)
        return statistics.median(out)

    none_ms, warm_ms = median_ms(b), median_ms(a)
    with capsys.disabled():
        print(f"\nmeta_filter latency: no model {none_ms:.4f} ms, scored (vector cached) {warm_ms:.3f} ms, "
              f"vector build (once per tf+decision) {cold_ms:.1f} ms")
    assert none_ms < 5.0 and warm_ms < 5.0


# ------------------------------------------------------------------------------------------------ structure
def _refs(tree: ast.AST, attr: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else scope
            if (isinstance(child, ast.Attribute) and child.attr == attr) or (isinstance(child, ast.Name)
                                                                            and child.id == attr):
                out.append((scope, child.lineno))
            visit(child, inner)

    visit(tree, "<module>")
    return out


def test_the_filter_is_called_only_from_the_entry_branch_of_send_before_the_order_core():
    """Trading-safety: drop-only at one place. `meta_filter.check` is referenced only by `exploration._send` (never an
    exit, `_pyramid`'s stop moves, the order core or another module), before `cost_probe._place`; the module reaches no
    order, close, size or limit."""
    hits: list[tuple[str, str]] = []
    for path in sorted((ROOT / "goldbot").rglob("*.py")):
        if path.name == "meta_filter.py":
            continue
        tree = ast.parse(path.read_text())
        hits += [(path.name, fn) for fn, _ in _refs(tree, "check") if "meta_filter" in path.read_text()
                 and fn == "_send" and path.name == "exploration.py"]
    assert hits, "exploration._send calls meta_filter.check"
    ex = ast.parse((ROOT / "goldbot" / "engine" / "exploration.py").read_text())
    send = [n for n in ast.walk(ex) if isinstance(n, ast.FunctionDef) and n.name == "_send"][0]
    calls = [n.lineno for n in ast.walk(send) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "check" and isinstance(n.func.value, ast.Name) and n.func.value.id == "meta_filter"]
    places = [ln for _, ln in _refs(send, "_place")]
    assert len(calls) == 1 and places and calls[0] < min(places)
    others = [n for n in ast.walk(ex) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "check" and isinstance(n.func.value, ast.Name) and n.func.value.id == "meta_filter"]
    assert len(others) == 1
    for path in sorted((ROOT / "goldbot").rglob("*.py")):
        if path.name not in ("exploration.py", "meta_filter.py", "runner.py", "engine.py") and \
                "meta_filter.check" in path.read_text():
            raise AssertionError(path)
    mod = ast.parse((ROOT / "goldbot" / "engine" / "meta_filter.py").read_text())
    for name in ("_execute", "_place", "order_send", "_try_close", "close", "modify", "_sizing_terms", "limits",
                 "gate"):
        assert not _refs(mod, name), name


def test_the_filter_only_reads_the_model_table():
    text = (ROOT / "goldbot" / "engine" / "meta_filter.py").read_text().upper()
    for verb in ("INSERT", "UPDATE ", "DELETE", "REPLACE", "DROP TABLE", ".WRITE("):
        assert verb not in text, verb


def test_the_published_state_is_empty_while_nothing_failed(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")])
    _serving(eng)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    pub = meta_filter.published(eng)
    assert "meta_filter_error" not in pub
    assert json.loads(json.dumps(pub))["meta_filter"]["failed_open"] == 0
