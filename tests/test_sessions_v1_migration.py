"""Sessions v2 migrate without downtime (quant review of the DST fix).

The DST fix bumped `session`, `session_zones` and `market_structure` to version 2, which changes the default feature
version. A champion or shadow challenger trained before it carries the old version; promotion of a retrained
challenger needs weeks of shadow trades, so the old implementations stay registered beside v2 and the engine rebuilds
a model's frame at the version it was trained on (design: "a model can only ever score the feature frame it was
trained on"). New research and retrains build v2. A version no registered implementation reproduces is not scored.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.base import Record
from goldbot.features import FEATURES, build_features, feature_version
from goldbot.features.registry import LEGACY_FEATURES, versions_for, with_versions
from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, build_decision_frame
from tests.test_engine_safety import _decide_once

GOLDEN = Path(__file__).parent / "data" / "sessions_v1_golden.parquet"
SESSIONS = ["session", "session_zones", "market_structure"]
V1 = {n: "1" for n in SESSIONS}
PRE_FIX_DEFAULT_VERSION = "f-1b8f723a67"   # feature_version(DEFAULT_FEATURE_NAMES) on claude/gifted-goldberg-lcb7pl
#                                            1c556e6, before the DST fix


def golden_bars() -> pd.DataFrame:
    """30-minute bars on weekdays only (weekend gaps) over both 2026 DST gap periods. The golden file holds the
    pre-fix code's features on exactly these bars (written by running 1c556e6's build_features on them)."""
    ts = pd.DatetimeIndex(np.concatenate([
        pd.date_range("2026-03-02", "2026-04-04", freq="30min", tz="UTC", inclusive="left").to_numpy(),
        pd.date_range("2026-10-19", "2026-11-07", freq="30min", tz="UTC", inclusive="left").to_numpy()]), tz="UTC")
    ts = ts[ts.dayofweek < 5]
    rng = np.random.default_rng(20261010)
    c = 2300 + np.cumsum(rng.normal(0, 1.5, len(ts)))
    o = np.concatenate(([2300.0], c[:-1]))
    h = np.maximum(o, c) + np.abs(rng.normal(0, 1.0, len(ts)))
    lo = np.minimum(o, c) - np.abs(rng.normal(0, 1.0, len(ts)))
    return pd.DataFrame({"ts_utc": ts, "open": o, "high": h, "low": lo, "close": c, "spread": 0.2, "tick_count": 30.0})


def test_the_v1_implementations_reproduce_the_pre_fix_features_exactly():
    got = build_features(golden_bars(), SESSIONS, versions=V1).drop(columns=["ts_utc"])
    want = pd.read_parquet(GOLDEN)
    # the fixture was written on macOS; Linux libm differs in the last ulp of a few divisions, so compare to 1e-12
    # relative (NaN positions and every column must still match). The same-run v1-vs-v1 check below stays exact.
    pd.testing.assert_frame_equal(got.reset_index(drop=True), want.reset_index(drop=True), check_exact=False,
                                  rtol=1e-12, atol=1e-12)


def test_the_v1_feature_set_hashes_to_the_pre_fix_default_version():
    assert {(n, "1") for n in SESSIONS} <= set(LEGACY_FEATURES)
    assert feature_version(DEFAULT_FEATURE_NAMES, V1) == PRE_FIX_DEFAULT_VERSION
    assert feature_version(DEFAULT_FEATURE_NAMES) != PRE_FIX_DEFAULT_VERSION
    assert versions_for(DEFAULT_FEATURE_NAMES, PRE_FIX_DEFAULT_VERSION) == V1
    assert versions_for(DEFAULT_FEATURE_NAMES, feature_version(DEFAULT_FEATURE_NAMES)) == {}
    assert versions_for(DEFAULT_FEATURE_NAMES, "f-0000000000") is None          # cannot be rebuilt


def test_legacy_versions_keep_the_current_columns_so_a_frame_can_be_rebuilt_in_place():
    m = golden_bars().iloc[:400].reset_index(drop=True)
    for n in SESSIONS:
        assert list(FEATURES[n].fn(m, {}).columns) == list(LEGACY_FEATURES[(n, "1")].fn(m, {}).columns)
    X = build_features(m, DEFAULT_FEATURE_NAMES)
    old = with_versions(X, m, DEFAULT_FEATURE_NAMES, V1)
    assert old.attrs["feature_version"] == PRE_FIX_DEFAULT_VERSION
    pd.testing.assert_frame_equal(old, build_features(m, DEFAULT_FEATURE_NAMES, versions=V1), check_exact=True)
    assert X.attrs["feature_version"] == feature_version(DEFAULT_FEATURE_NAMES)      # the input is not modified


class _ProbeModel(Record):
    """p depends on a session feature, so a frame of the wrong version would score differently."""
    feature_version: str
    feature_names: list[str] = ["min_since_london_open"]

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return 0.30 + X["min_since_london_open"].to_numpy(dtype=float) / 10_000


def _decision_p(out: list[dict[str, Any]]) -> tuple[str, float | None]:
    [d] = out
    return d["action"], d.get("p")


def test_a_v1_champion_scores_exactly_as_before_the_fix(tmp_path):
    eng, fr, out = _decide_once(tmp_path / "v1", _ProbeModel(feature_version=PRE_FIX_DEFAULT_VERSION))
    action, p = _decision_p(out)
    assert action != "feature_version_mismatch"
    last = pd.Timestamp(fr.dec["ts_utc"].iloc[-1])
    # before the fix: minutes since a fixed 07:00 UTC London open (March 2025 is London winter time, so v2 differs)
    before = ((last.hour + last.minute / 60 - 7) % 24) * 60
    assert p == pytest.approx(0.30 + before / 10_000)
    assert fr.X["min_since_london_open"].iloc[-1] != before                         # the v2 frame is not what it read


def test_a_v2_model_scores_the_v2_frame_and_an_unknown_version_is_still_not_scored(tmp_path):
    _, fr, out = _decide_once(tmp_path / "v2", _ProbeModel(feature_version=feature_version(DEFAULT_FEATURE_NAMES)))
    action, p = _decision_p(out)
    assert action != "feature_version_mismatch"
    assert p == pytest.approx(0.30 + float(fr.X["min_since_london_open"].iloc[-1]) / 10_000)
    _, _, out = _decide_once(tmp_path / "unknown", _ProbeModel(feature_version="f-0000000000"))
    assert _decision_p(out) == ("feature_version_mismatch", None)


def test_a_v1_shadow_challenger_keeps_paper_trading_on_its_own_features(tmp_path):
    from goldbot.data.resample import ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.engine import Engine, EngineConfig
    from goldbot.execution.paper import PaperBroker
    from goldbot.telegram.approvals import ApprovalCenter
    from tests.test_engine_safety import OWNER, _always_long, _tick
    ticks = synthetic_ticks("2025-03-03", "2025-03-05", ticks_per_minute=1, seed=5)
    last = pd.Timestamp(ticks["ts_utc"].iloc[-1])
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(last, float(ticks["bid"].iloc[-1])))
    agent = _always_long()
    shadow: dict[str, tuple[str, Any]] = {"old": (agent.agent_id, _ProbeModel(feature_version=PRE_FIX_DEFAULT_VERSION)),
              "unknown": (agent.agent_id, _ProbeModel(feature_version="f-0000000000"))}
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=OWNER,
                              shadow_host=True), pb, [agent], {}, ApprovalCenter({OWNER}), shadow_models=shadow)
    eng.bars_1m = ticks_to_1m(ticks)
    close_ts = last.floor("15min")
    fr = eng._frame(eng.bars_1m[eng.bars_1m["visible_at"] <= close_ts], "15m", close_ts)
    assert fr is not None and eng.shadow is not None
    eng._refresh_account(pb.last_tick("XAUUSD"))
    eng._decide("15m", fr, [agent], {agent.family: 1.0}, close_ts, pb.last_tick("XAUUSD"))
    bar = pd.Timestamp(fr.dec["ts_utc"].iloc[-1])
    before = ((bar.hour + bar.minute / 60 - 7) % 24) * 60
    books = eng.shadow.books
    [t] = books["old"].open + books["old"].closed
    assert t.p == pytest.approx(0.30 + before / 10_000)
    assert t.scored_feature_version == PRE_FIX_DEFAULT_VERSION
    assert "unknown" not in books or not (books["unknown"].open + books["unknown"].closed)


def test_a_v2_challenger_trains_on_the_v2_features():
    from goldbot.data.resample import resample_bars, ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    b1 = ticks_to_1m(synthetic_ticks("2025-01-06", "2025-01-17", ticks_per_minute=1, seed=3))
    b15 = resample_bars(b1, "15m").reset_index(drop=True)
    _, X = build_decision_frame(b15, {})
    assert X.attrs["feature_version"] == feature_version(DEFAULT_FEATURE_NAMES) != PRE_FIX_DEFAULT_VERSION
    assert all(FEATURES[n].version == "2" for n in SESSIONS)
    ts = pd.DatetimeIndex(X["ts_utc"])
    at_ldn = np.flatnonzero((ts.hour == 8) & (ts.minute == 0))                       # January: London opens 08:00 UTC
    assert len(at_ldn) and (X["min_since_london_open"].to_numpy()[at_ldn] == 0).all()


# --------------------------------------------------------------------------- the pre-registration pins the version
def _gates(passed: bool) -> dict[str, Any]:
    return {"gates": {"passed": passed}, **_COSTS}


# the cost treatment a research row records (registry.passed_gates counts only rows with it)
_COSTS = {"swap": {"long_usd_per_lot": -60.0, "short_usd_per_lot": 20.0}, "cost_source": "PRIORS ONLY: test"}


def test_a_result_on_another_feature_version_than_its_preregistration_records_the_deviation(tmp_path):
    from goldbot.research.registry import TrialRegistry
    reg = TrialRegistry(tmp_path / "r.jsonl")
    pre = reg.preregister(agent_id="a", family="trend", config={"k": 1}, feature_version="f-aaaaaaaaaa",
                          rationale="r", reading_rule="rule")
    same = reg.record(agent_id="a", family="trend", config={"k": 1}, feature_version="f-aaaaaaaaaa", rationale="r",
                      results={}, preregistration=pre)
    assert "deviation" not in same["preregistration"]
    other = reg.record(agent_id="a", family="trend", config={"k": 1}, feature_version="f-bbbbbbbbbb", rationale="r",
                       results={}, preregistration=pre)
    assert other["preregistration"]["deviation"] == {
        "feature_version": {"preregistered": "f-aaaaaaaaaa", "run": "f-bbbbbbbbbb"}}


def test_passing_the_gates_counts_only_on_the_feature_version_asked_for(tmp_path):
    from goldbot.research.registry import TrialRegistry
    reg = TrialRegistry(tmp_path / "r.jsonl")
    reg.record(agent_id="a", family="trend", config={"k": 1}, feature_version="f-v1v1v1v1v1", rationale="r",
               results=_gates(True))
    assert reg.passed_gates("trend", {"k": 1})                                    # any version (as before)
    assert reg.passed_gates("trend", {"k": 1}, feature_version="f-v1v1v1v1v1")
    assert not reg.passed_gates("trend", {"k": 1}, feature_version="f-v2v2v2v2v2")


def test_a_discovery_survivor_needs_its_holdout_pass_on_the_same_feature_version(tmp_path):
    from goldbot.research.registry import FROM_DISCOVERY_KEY, TrialRegistry
    reg = TrialRegistry(tmp_path / "r.jsonl")
    cfg = {"k": 1, FROM_DISCOVERY_KEY: 7}
    reg.record(agent_id="a", family="trend", config=cfg, feature_version="f-v2v2v2v2v2", rationale="r",
               results=_gates(True))
    reg.record(agent_id="a", family="trend", config=cfg, feature_version="f-v1v1v1v1v1", rationale="r",
               results={"holdout_verdict": {"passed": True}, **_COSTS}, status="holdout")
    assert not reg.passed_gates("trend", cfg, feature_version="f-v2v2v2v2v2")
    assert reg.passed_gates("trend", cfg)                                         # any version (as before)


def test_a_holdout_look_on_any_feature_version_still_spends_the_configurations_one_scoring(tmp_path):
    # deliberately version-agnostic: re-scoring the holdout under a new feature version would be a second look
    from goldbot.research.registry import TrialRegistry
    reg = TrialRegistry(tmp_path / "r.jsonl")
    reg.record(agent_id="a", family="trend", config={"k": 1}, feature_version="f-v1v1v1v1v1", rationale="r",
               results={"holdout_verdict": {"passed": False}}, status="holdout")
    assert reg.holdout_scored("trend", {"k": 1})


# --------------------------------------------------------------------------- re-review: fail closed, PSI, records
def test_a_legacy_rebuild_that_raises_leaves_the_model_unscored_and_the_bar_running(tmp_path, monkeypatch):
    def boom(df: pd.DataFrame, ctx: dict) -> pd.DataFrame:
        raise RuntimeError("legacy implementation broke")
    spec = LEGACY_FEATURES[("session", "1")]
    monkeypatch.setitem(LEGACY_FEATURES, ("session", "1"), spec.model_copy(update={"fn": boom}))
    _, fr, out = _decide_once(tmp_path, _ProbeModel(feature_version=PRE_FIX_DEFAULT_VERSION))   # does not raise
    assert _decision_p(out) == ("feature_version_mismatch", None)
    assert fr.by_version[PRE_FIX_DEFAULT_VERSION] is None                       # cached: not retried on every agent


def test_decision_and_shadow_records_carry_the_feature_version_they_scored(tmp_path):
    _, _, out = _decide_once(tmp_path, _ProbeModel(feature_version=PRE_FIX_DEFAULT_VERSION))
    [d] = out
    assert d["scored_feature_version"] == PRE_FIX_DEFAULT_VERSION
    _, _, out = _decide_once(tmp_path / "v2", _ProbeModel(feature_version=feature_version(DEFAULT_FEATURE_NAMES)))
    assert out[0]["scored_feature_version"] == feature_version(DEFAULT_FEATURE_NAMES)


class _EveryBar:
    """A specialist stand-in for the drift job: a long candidate on every bar."""
    timeframe = "15m"

    def candidates(self, m: pd.DataFrame, X: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"idx": np.arange(len(m)), "side": np.ones(len(m), dtype=int)})


def _drift_bars(monkeypatch: Any) -> pd.Timestamp:
    from goldbot.data.resample import resample_bars, ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.ops import jobs
    b1 = ticks_to_1m(synthetic_ticks("2025-02-24", "2025-03-10", ticks_per_minute=1, seed=4))
    by_tf = {tf: resample_bars(b1, tf) for tf in ("15m", "1h", "4h", "1d")}
    monkeypatch.setattr(jobs, "_bars", lambda ctx, tf, start, end: by_tf[tf][by_tf[tf]["ts_utc"] < end])
    return pd.Timestamp("2025-03-10", tz="UTC")


def test_a_v1_champions_drift_inputs_are_built_on_its_v1_columns(monkeypatch):
    from goldbot.ops.jobs import _recent_candidates
    slot = _drift_bars(monkeypatch)
    v1 = _recent_candidates(None, _EveryBar(), slot, 3, feature_version=PRE_FIX_DEFAULT_VERSION)  # type: ignore[arg-type]
    v2 = _recent_candidates(None, _EveryBar(), slot, 3, feature_version=feature_version(DEFAULT_FEATURE_NAMES))  # type: ignore[arg-type]
    assert v1 is not None and v2 is not None and len(v1) == len(v2) > 50       # 3 days incl. a weekend
    from goldbot.ops import jobs
    dec = jobs._bars(None, "15m", slot, slot)  # type: ignore[arg-type]
    ts = pd.DatetimeIndex(pd.to_datetime(dec["ts_utc"], utc=True))
    ts = ts[ts >= slot - pd.Timedelta(days=3)]
    before = ((ts.hour + ts.minute / 60 - 7) % 24) * 60                  # v1: minutes since a fixed 07:00 UTC open
    np.testing.assert_allclose(v1["min_since_london_open"].to_numpy(), np.asarray(before, dtype=float))
    assert (v1["min_since_london_open"] != v2["min_since_london_open"]).any()     # early March: London winter time


def test_drift_skips_psi_with_a_note_when_a_champions_version_cannot_be_rebuilt(monkeypatch):
    from goldbot.ops.jobs import _recent_candidates
    slot = _drift_bars(monkeypatch)
    assert _recent_candidates(None, _EveryBar(), slot, 3, feature_version="f-0000000000") is None  # type: ignore[arg-type]


def test_health_counts_models_per_feature_version(tmp_path):
    import json

    from goldbot.ops.health import check_feature_versions
    from tests.test_health import SETTINGS, make_ctx
    models = tmp_path / "models"
    models.mkdir()
    cur = feature_version(DEFAULT_FEATURE_NAMES)
    rows = [{"version": f"v{i}", "family": "trend", "agent_id": f"a{i}", "status": st, "feature_version": fv,
             "created_utc": "2026-10-01T00:00:00+00:00", "feature_names": [], "artefact": "x", "sha256": "0"}
            for i, (st, fv) in enumerate([("champion", PRE_FIX_DEFAULT_VERSION), ("challenger", PRE_FIX_DEFAULT_VERSION),
                                          ("challenger", cur), ("retired", PRE_FIX_DEFAULT_VERSION)])]
    (models / "registry.json").write_text(json.dumps(rows))
    settings = SETTINGS.model_copy(update={"research": SETTINGS.research.model_copy(update={"models_dir": str(models)})})
    c = check_feature_versions(make_ctx(tmp_path, settings=settings))
    assert c.status == "ok"
    assert f"{PRE_FIX_DEFAULT_VERSION} (legacy) 2" in c.reason and f"{cur} (current) 1" in c.reason
    rows[2]["feature_version"] = "f-0000000000"
    (models / "registry.json").write_text(json.dumps(rows))
    c = check_feature_versions(make_ctx(tmp_path, settings=settings))
    assert c.status == "warn" and "f-0000000000 (cannot be rebuilt: not scored) 1" in c.reason


def _registry(tmp_path: Path, models: list[tuple[str, str]]) -> Any:
    import json

    from tests.test_health import SETTINGS
    d = tmp_path / "models"
    d.mkdir(exist_ok=True)
    rows = [{"version": f"v{i}", "family": "trend", "agent_id": f"a{i}", "status": st, "feature_version": fv,
             "created_utc": "2026-10-01T00:00:00+00:00", "feature_names": [], "artefact": "x", "sha256": "0"}
            for i, (st, fv) in enumerate(models)]
    (d / "registry.json").write_text(json.dumps(rows))
    return SETTINGS.model_copy(update={"research": SETTINGS.research.model_copy(update={"models_dir": str(d)})})


def test_health_counts_previous_champions_because_a_rollback_restores_them(tmp_path):
    from goldbot.ops.health import check_feature_versions
    from tests.test_health import make_ctx
    cur = feature_version(DEFAULT_FEATURE_NAMES)
    settings = _registry(tmp_path, [("champion", cur), ("previous", PRE_FIX_DEFAULT_VERSION)])
    c = check_feature_versions(make_ctx(tmp_path, settings=settings))
    assert f"{PRE_FIX_DEFAULT_VERSION} (legacy) 1" in c.reason        # legacy.py is still needed for the rollback


def test_health_counts_a_macro_trained_model_as_current_not_unbuildable(tmp_path):
    from goldbot.ops.health import check_feature_versions
    from goldbot.research.pipeline import MACRO_FEATURE_NAMES
    from tests.test_health import make_ctx
    macro_fv = feature_version([*DEFAULT_FEATURE_NAMES, *MACRO_FEATURE_NAMES])
    c = check_feature_versions(make_ctx(tmp_path, settings=_registry(tmp_path, [("champion", macro_fv)])))
    assert c.status == "ok" and f"{macro_fv} (current) 1" in c.reason


def _macro_rows() -> pd.DataFrame:
    from goldbot.data.macro import US_BDAY, fred_frame
    days = pd.date_range("2024-01-02", "2025-03-07", freq=US_BDAY)
    n, v = len(days), pd.Timestamp("2025-07-01")
    return pd.concat([fred_frame(s, pd.DataFrame({"value_date": days, "value": base + 0.01 * np.arange(n)}), v)
                      for s, base in (("DFII10", 1.0), ("DTWEXBGS", 120.0), ("GVZCLS", 15.0), ("VIXCLS", 15.0),
                                      ("SP500", 4000.0))], ignore_index=True)


def test_a_macro_trained_champion_keeps_its_drift_inputs_with_the_macro_columns(monkeypatch):
    from goldbot.ops import jobs
    from goldbot.research.pipeline import MACRO_FEATURE_NAMES
    slot = _drift_bars(monkeypatch)
    monkeypatch.setattr(jobs, "store_macro", lambda store: _macro_rows())
    macro_fv = feature_version([*DEFAULT_FEATURE_NAMES, *MACRO_FEATURE_NAMES])
    store_ctx = SimpleNamespace(store=None)
    live = jobs._recent_candidates(store_ctx, _EveryBar(), slot, 3, feature_version=macro_fv)  # type: ignore[arg-type]
    assert live is not None and len(live) > 50
    assert live["macro_dollar_chg20"].notna().all()                       # the macro context was passed
    old = feature_version([*DEFAULT_FEATURE_NAMES, *MACRO_FEATURE_NAMES], V1)
    assert jobs._recent_candidates(store_ctx, _EveryBar(), slot, 3, feature_version=old) is not None  # type: ignore[arg-type]


def test_a_macro_champion_without_macro_rows_skips_psi(monkeypatch):
    from goldbot.ops import jobs
    from goldbot.research.pipeline import MACRO_FEATURE_NAMES
    slot = _drift_bars(monkeypatch)
    monkeypatch.setattr(jobs, "store_macro", lambda store: pd.DataFrame())
    macro_fv = feature_version([*DEFAULT_FEATURE_NAMES, *MACRO_FEATURE_NAMES])
    store_ctx = SimpleNamespace(store=None)
    assert jobs._recent_candidates(store_ctx, _EveryBar(), slot, 3, feature_version=macro_fv) is None  # type: ignore[arg-type]
