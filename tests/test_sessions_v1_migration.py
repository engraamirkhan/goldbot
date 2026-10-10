"""Sessions v2 migrate without downtime (quant review of the DST fix).

The DST fix bumped `session`, `session_zones` and `market_structure` to version 2, which changes the default feature
version. A champion or shadow challenger trained before it carries the old version; promotion of a retrained
challenger needs weeks of shadow trades, so the old implementations stay registered beside v2 and the engine rebuilds
a model's frame at the version it was trained on (design: "a model can only ever score the feature frame it was
trained on"). New research and retrains build v2. A version no registered implementation reproduces is not scored.
"""
from __future__ import annotations

from pathlib import Path
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
    pd.testing.assert_frame_equal(got.reset_index(drop=True), want.reset_index(drop=True), check_exact=True)


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
    return {"gates": {"passed": passed}}


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
               results={"holdout_verdict": {"passed": True}}, status="holdout")
    assert not reg.passed_gates("trend", cfg, feature_version="f-v2v2v2v2v2")
    assert reg.passed_gates("trend", cfg)                                         # any version (as before)


def test_a_holdout_look_on_any_feature_version_still_spends_the_configurations_one_scoring(tmp_path):
    # deliberately version-agnostic: re-scoring the holdout under a new feature version would be a second look
    from goldbot.research.registry import TrialRegistry
    reg = TrialRegistry(tmp_path / "r.jsonl")
    reg.record(agent_id="a", family="trend", config={"k": 1}, feature_version="f-v1v1v1v1v1", rationale="r",
               results={"holdout_verdict": {"passed": False}}, status="holdout")
    assert reg.holdout_scored("trend", {"k": 1})
