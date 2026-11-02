"""Sprint-3000 catalogue schema (docs/research/sprint3000/prereg.md): exactly 3000 specs, ids and ranks unique
1..3000, every spec uses only the brief's vocabulary (plus ADDENDUM A), no duplicate spec, the rank follows the prior
score, the frozen composition holds, and the committed file is what the generator builds."""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = ROOT / "config" / "research" / "sprint3000_catalogue.yaml"

# the brief's vocabulary, written out independently of the generator so a drift in either is caught
TFS = {"15m", "1h", "4h", "1d"}
ZONE_TFS = {"15m", "1h", "4h", "1d", "1w"}
SESSIONS = {"asia", "london", "ny"}
MODES = {"follow", "fade"}
ENTRIES: dict[str, set[str]] = {
    "donchian_break": {"n"}, "ma_cross": {"fast", "slow", "kind"}, "ema_pullback": {"n", "depth_atr"},
    "keltner_break": {"n", "k"}, "bb_break": {"n", "k"}, "nr_breakout": {"n"}, "inside_bar_break": set(),
    "session_range_break": {"session"}, "opening_range_break": {"session", "minutes"}, "prior_level_break": {"level"},
    "momentum_sign": {"lookback_bars"}, "consecutive_bars": {"n", "mode"}, "atr_expansion": {"n", "mult"},
    "adx_onset": {"n", "level"}, "supertrend_flip": {"n", "mult"}, "macd_turn": {"fast", "slow", "signal"},
    "rsi_threshold": {"n", "level", "mode"}, "zscore": {"n", "z", "mode"},
    "level_break": {"min_strength_pct", "min_tf"}, "level_reject": {"min_strength_pct", "min_tf"},
}
FILTERS: dict[str, set[str]] = {
    "trend": {"tf", "ema_n"}, "vol_regime": {"atr_n", "pct_lo", "pct_hi"}, "session": {"session"},
    "weekday": {"days"}, "adx_min": {"n", "level"}, "compression": {"n", "pct_max"},
    "near_level": {"max_dist_atr", "min_strength_pct"}, "room_to_level": {"min_r"},
}
EXITS: dict[str, set[str]] = {"target_r": {"r"}, "chandelier": {"atr_n", "mult"}, "time": {"bars"},
                              "opposite_signal": set()}
SIDES = {"signal", "long_only", "short_only"}
KEYS = {"id", "rank", "prior_score", "family", "tf", "entry", "filters", "side", "stop", "exit", "max_hold_bars"}


@pytest.fixture(scope="module")
def specs() -> list[dict[str, Any]]:
    doc = yaml.safe_load(CATALOGUE.read_text())
    assert doc["n_specs"] == 3000 and doc["cost_of_record"] == "cor-2026-10-31.2"
    return list(doc["specs"])


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def test_exactly_3000_with_unique_ids_and_ranks(specs: list[dict[str, Any]]) -> None:
    assert len(specs) == 3000
    assert sorted(s["rank"] for s in specs) == list(range(1, 3001))
    assert [s["id"] for s in specs] == [f"S3K-{r:04d}" for r in range(1, 3001)]
    assert all(s["id"] == f"S3K-{s['rank']:04d}" for s in specs)


def test_every_spec_uses_only_the_vocabulary(specs: list[dict[str, Any]]) -> None:
    for s in specs:
        assert set(s) == KEYS, s["id"]
        assert s["tf"] in TFS and s["side"] in SIDES and isinstance(s["family"], str) and s["family"]
        e = s["entry"]
        assert set(e) == {"type", "params"} and set(e["params"]) == ENTRIES[e["type"]], s["id"]
        p = e["params"]
        if "session" in p:
            assert p["session"] in SESSIONS
        if "mode" in p:
            assert p["mode"] in MODES
        if "kind" in p:
            assert p["kind"] in {"ema", "sma"}
        if "level" in p and e["type"] == "prior_level_break":
            assert p["level"] in {"day", "week", "month"}
        if "min_tf" in p:
            assert p["min_tf"] in ZONE_TFS
        if e["type"] in {"ma_cross", "macd_turn"}:
            assert p["fast"] < p["slow"]
        assert all(_num(v) for k, v in p.items() if k not in {"session", "mode", "kind", "level", "min_tf"}) \
            or e["type"] == "prior_level_break"
        assert isinstance(s["filters"], list)
        types = [f["type"] for f in s["filters"]]
        assert len(types) == len(set(types)), s["id"]
        for f in s["filters"]:
            assert set(f) == {"type", "params"} and set(f["params"]) == FILTERS[f["type"]], s["id"]
            fp = f["params"]
            if f["type"] == "trend":
                assert fp["tf"] in {"4h", "1d"}
            if f["type"] == "session":
                assert fp["session"] in SESSIONS
            if f["type"] == "weekday":
                assert fp["days"] and set(fp["days"]) <= {0, 1, 2, 3, 4}
            if f["type"] == "vol_regime":
                assert 0 <= fp["pct_lo"] < fp["pct_hi"] <= 100
        assert set(s["stop"]) == {"atr_n", "atr_mult"} and s["stop"]["atr_n"] > 0 and s["stop"]["atr_mult"] > 0
        x = s["exit"]
        assert set(x) == {"type", "params"} and set(x["params"]) == EXITS[x["type"]], s["id"]
        assert isinstance(s["max_hold_bars"], int) and s["max_hold_bars"] > 0
        if x["type"] == "time":
            assert x["params"]["bars"] == s["max_hold_bars"]


def test_no_duplicate_spec(specs: list[dict[str, Any]]) -> None:
    body = [json.dumps({k: s[k] for k in KEYS - {"id", "rank", "prior_score", "family"}}, sort_keys=True)
            for s in specs]
    assert len(set(body)) == 3000


def test_rank_follows_the_prior_score(specs: list[dict[str, Any]]) -> None:
    by_rank = sorted(specs, key=lambda s: s["rank"])
    scores = [s["prior_score"] for s in by_rank]
    assert all(a >= b for a, b in zip(scores, scores[1:], strict=False))


def test_frozen_composition(specs: list[dict[str, Any]]) -> None:
    """Prereg section 4: equal long_only/short_only shares, 15m cap, 10-15% level-based, null-control share."""
    side = Counter(s["side"] for s in specs)
    assert side["long_only"] == side["short_only"] == 180
    assert Counter(s["tf"] for s in specs)["15m"] <= 200
    level = sum(1 for s in specs if s["entry"]["type"] in {"level_break", "level_reject"}
                or any(f["type"] in {"near_level", "room_to_level"} for f in s["filters"]))
    assert 300 <= level <= 450
    assert max(Counter(s["entry"]["type"] for s in specs).values()) <= 400


def test_the_committed_file_is_what_the_generator_builds() -> None:
    spec = importlib.util.spec_from_file_location("sprint3000_catalogue", ROOT / "scripts" / "sprint3000_catalogue.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod             # dataclasses resolve their module by name
    spec.loader.exec_module(mod)
    assert mod.render(mod.build()) == CATALOGUE.read_text()
