"""The sprint-3000 vocabulary (goldbot/research/sprint3000/vocab.py): each entry primitive on hand-built bars, every
entry and filter free of look-ahead on a synthetic tape, and the catalogue schema that refuses anything outside the
vocabulary (unknown types are listed, all at once)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from goldbot.research.sprint3000 import vocab as V
from goldbot.research.sprint3000.market import MIN, Market
from tests.research.sprint3000_bars import hand_bars, synthetic_market, synthetic_minutes, window

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "sprint3000" / "sample_catalogue.yaml"


def fired(rule: str, params: dict, b: V.Bars) -> tuple[list[int], list[int]]:
    L, S = V.entry_signals(V.Rule(type=rule, params=params), b, None)
    return np.flatnonzero(L).tolist(), np.flatnonzero(S).tolist()


# ---------------------------------------------------------------- primitives on fixed bars
def test_donchian_break_fires_on_the_first_close_above_the_prior_n_bar_high_only() -> None:
    b = hand_bars([10, 10, 10, 10, 11, 12, 12.5])
    assert fired("donchian_break", {"n": 3}, b) == ([4], [])


def test_ma_cross_fires_on_the_bar_the_fast_average_crosses_the_slow() -> None:
    b = hand_bars([5, 5, 5, 5, 6, 7, 6, 5, 4])
    assert fired("ma_cross", {"fast": 2, "slow": 3, "kind": "sma"}, b) == ([4], [7])


def test_inside_bar_break_needs_an_inside_bar_then_a_close_beyond_it() -> None:
    b = hand_bars([10, 10, 11.5, 11.6], h=[12, 11, 11.7, 11.8], lo=[8, 9, 10, 11])
    assert fired("inside_bar_break", {}, b) == ([2], [])


def test_nr_breakout_breaks_the_narrowest_bar_of_the_last_n() -> None:
    b = hand_bars([10, 10, 10, 10.8], h=[12, 11.5, 10.3, 11], lo=[8, 8.5, 9.8, 10.2])
    assert fired("nr_breakout", {"n": 3}, b) == ([3], [])


def test_consecutive_bars_follow_goes_with_the_run_and_fade_against_it() -> None:
    b = hand_bars([10, 11, 12, 13, 14, 13])
    assert fired("consecutive_bars", {"n": 3, "mode": "follow"}, b) == ([3], [])
    assert fired("consecutive_bars", {"n": 3, "mode": "fade"}, b) == ([], [3])


def test_momentum_sign_fires_when_the_lookback_return_changes_sign() -> None:
    b = hand_bars([10, 11, 12, 11, 10, 9, 10, 11, 12])
    assert fired("momentum_sign", {"lookback_bars": 2}, b) == ([7], [4])


def test_atr_expansion_takes_the_side_of_the_wide_bars_body() -> None:
    c = [10.0] * 5 + [13.5]
    b = hand_bars(c, h=[10.5] * 5 + [14], lo=[9.5] * 5 + [10], o=[10.0] * 6)
    assert fired("atr_expansion", {"n": 3, "mult": 2.0}, b) == ([5], [])


def test_supertrend_flips_long_after_the_bottom_of_a_v_and_never_long_on_the_way_down() -> None:
    c = np.r_[np.linspace(200, 100, 120), np.linspace(100, 200, 120)]
    b = hand_bars(list(c))
    L, S = V.entry_signals(V.Rule(type="supertrend_flip", params={"n": 10, "mult": 3.0}), b, None)
    assert np.flatnonzero(L).size >= 1 and np.flatnonzero(L).min() > 120
    assert not L[:120].any()


@pytest.mark.parametrize(("rule", "params"), [
    ("rsi_threshold", {"n": 14, "level": 70}), ("zscore", {"n": 20, "z": 2.0}), ("consecutive_bars", {"n": 3})])
def test_fade_mode_is_follow_mode_with_the_sides_swapped(rule: str, params: dict) -> None:
    mk = synthetic_market(days=20)
    b = mk.bars["1h"]
    fl, fs = V.entry_signals(V.Rule(type=rule, params={**params, "mode": "follow"}), b, mk)
    dl, ds = V.entry_signals(V.Rule(type=rule, params={**params, "mode": "fade"}), b, mk)
    assert fl.any() and fs.any()
    assert np.array_equal(fl, ds) and np.array_equal(fs, dl)


# ---------------------------------------------------------------- examples of every vocabulary type
ENTRY_EXAMPLES: dict[str, dict] = {
    "donchian_break": {"n": 20}, "ma_cross": {"fast": 10, "slow": 30, "kind": "ema"},
    "ema_pullback": {"n": 20, "depth_atr": 0.0}, "keltner_break": {"n": 20, "k": 1.5}, "bb_break": {"n": 20, "k": 2.0},
    "nr_breakout": {"n": 4}, "inside_bar_break": {}, "session_range_break": {"session": "asia"},
    "opening_range_break": {"session": "london", "minutes": 30}, "prior_level_break": {"level": "day"},
    "momentum_sign": {"lookback_bars": 12}, "consecutive_bars": {"n": 3, "mode": "follow"},
    "atr_expansion": {"n": 14, "mult": 1.5}, "adx_onset": {"n": 14, "level": 20},
    "supertrend_flip": {"n": 10, "mult": 2.0}, "macd_turn": {"fast": 12, "slow": 26, "signal": 9},
    "rsi_threshold": {"n": 14, "level": 65, "mode": "follow"}, "zscore": {"n": 20, "z": 1.5, "mode": "fade"},
}
FILTER_EXAMPLES: dict[str, dict] = {
    "trend": {"tf": "4h", "ema_n": 20}, "vol_regime": {"atr_n": 5, "pct_lo": 10, "pct_hi": 90},
    "session": {"session": "ny"}, "weekday": {"days": [0, 2, 4]}, "adx_min": {"n": 14, "level": 20},
    "compression": {"n": 10, "pct_max": 40},
}
EXIT_EXAMPLES: dict[str, dict] = {"target_r": {"r": 2.0}, "chandelier": {"atr_n": 14, "mult": 3.0},
                                  "time": {"bars": 10}, "opposite_signal": {}}


def test_the_registry_holds_exactly_the_brief_vocabulary() -> None:
    assert set(V.ENTRY) >= set(ENTRY_EXAMPLES)
    assert set(V.FILTER) >= set(FILTER_EXAMPLES)
    assert set(V.EXIT) == set(EXIT_EXAMPLES)
    assert V.TIMEFRAMES == ("15m", "1h", "4h", "1d")


def _prefix_market(m: pd.DataFrame, cut: pd.Timestamp) -> Market:
    part = m[m.t < cut.value].reset_index(drop=True)
    return Market.from_minutes(part, window("2012-01-02", str((cut + pd.Timedelta(days=1)).date())))


@pytest.mark.parametrize("tf", ["15m", "1h", "4h"])
@pytest.mark.parametrize("rule", sorted(ENTRY_EXAMPLES))
def test_no_entry_signal_changes_when_later_minutes_are_removed(rule: str, tf: str) -> None:
    m = synthetic_minutes(days=75, seed=11)
    cut = pd.Timestamp("2012-03-01 13:37", tz="UTC")
    full = Market.from_minutes(m, window("2012-01-02", "2012-03-20"))
    part = _prefix_market(m, cut)
    r = V.Rule(type=rule, params=ENTRY_EXAMPLES[rule])
    fl, fs = V.entry_signals(r, full.bars[tf], full)
    pl, ps = V.entry_signals(r, part.bars[tf], part)
    k = int(np.searchsorted(part.bars[tf].dec, cut.value, side="right"))      # bars closed by the cut
    assert k > 50
    assert np.array_equal(fl[:k], pl[:k]) and np.array_equal(fs[:k], ps[:k])


@pytest.mark.parametrize("rule", sorted(FILTER_EXAMPLES))
def test_no_filter_changes_when_later_minutes_are_removed(rule: str) -> None:
    m = synthetic_minutes(days=75, seed=12)
    cut = pd.Timestamp("2012-03-01 13:37", tz="UTC")
    full, part = Market.from_minutes(m, window("2012-01-02", "2012-03-20")), _prefix_market(m, cut)
    r = V.Rule(type=rule, params=FILTER_EXAMPLES[rule])
    fb, pb = full.bars["1h"], part.bars["1h"]
    fl, fs = V.filter_ok(r, fb, full, np.ones(len(fb.c)))
    pl, ps = V.filter_ok(r, pb, part, np.ones(len(pb.c)))
    k = int(np.searchsorted(pb.dec, cut.value, side="right"))
    assert np.array_equal(fl[:k], pl[:k]) and np.array_equal(fs[:k], ps[:k])


def test_trend_filter_reads_the_last_higher_timeframe_bar_closed_at_the_decision() -> None:
    mk = synthetic_market(days=40, drift=0.004)
    b = mk.bars["1h"]
    L, S = V.filter_ok(V.Rule(type="trend", params={"tf": "4h", "ema_n": 10}), b, mk, np.ones(len(b.c)))
    h4 = mk.bars["4h"]
    ema = pd.Series(h4.c).ewm(span=10, adjust=False).mean().to_numpy()
    for i in range(100, len(b.c), 37):
        j = int(np.searchsorted(h4.dec, b.dec[i], side="right")) - 1     # 4h bars closed at or before the 1h close
        assert L[i] == (h4.c[j] > ema[j]) and S[i] == (h4.c[j] < ema[j])


def test_session_range_break_fires_at_most_once_per_session_instance_after_it_closes() -> None:
    mk = synthetic_market(days=30, seed=3)
    b = mk.bars["15m"]
    L, S = V.entry_signals(V.Rule(type="session_range_break", params={"session": "asia"}), b, mk)
    i = np.flatnonzero(L | S)
    assert len(i) >= 10
    inst = V.session_instances(mk, "asia")
    j = np.searchsorted(inst.end, b.dec[i], side="right") - 1
    assert (inst.end[j] <= b.dec[i]).all() and len(np.unique(j)) == len(j)
    assert ((b.c[i] > inst.hi[j]) == L[i]).all() and ((b.c[i] < inst.lo[j]) == S[i]).all()


def test_opening_range_break_fires_inside_the_session_after_the_opening_minutes() -> None:
    mk = synthetic_market(days=30, seed=4)
    b = mk.bars["15m"]
    L, S = V.entry_signals(V.Rule(type="opening_range_break", params={"session": "ny", "minutes": 30}), b, mk)
    i = np.flatnonzero(L | S)
    inst = V.session_instances(mk, "ny")
    j = np.searchsorted(inst.start, b.dec[i], side="right") - 1
    assert len(i) >= 10
    assert (b.dec[i] >= inst.start[j] + 30 * MIN).all() and (b.dec[i] <= inst.end[j]).all()


def test_prior_level_break_uses_the_previous_trading_days_high() -> None:
    mk = synthetic_market(days=30, seed=5)
    b = mk.bars["1h"]
    L, _ = V.entry_signals(V.Rule(type="prior_level_break", params={"level": "day"}), b, mk)
    d = mk.bars["1d"]
    for i in np.flatnonzero(L):
        prev = int(np.searchsorted(d.tday, b.tday[i], side="left")) - 1
        assert b.c[i] > d.h[prev] >= b.c[i - 1]
    assert L.any()


def test_long_only_and_short_only_drop_the_other_side() -> None:
    mk = synthetic_market(days=30)
    base = {"id": "S3K-9999", "rank": 1, "prior_score": 0.1, "family": "t", "tf": "1h",
            "entry": {"type": "donchian_break", "params": {"n": 10}}, "filters": [],
            "stop": {"atr_n": 14, "atr_mult": 2.0}, "exit": {"type": "target_r", "params": {"r": 2}},
            "max_hold_bars": 10}
    both = V.spec_signals(V.Spec.model_validate({**base, "side": "signal"}), mk)
    lo = V.spec_signals(V.Spec.model_validate({**base, "side": "long_only"}), mk)
    sh = V.spec_signals(V.Spec.model_validate({**base, "side": "short_only"}), mk)
    assert (both.side > 0).any() and (both.side < 0).any()
    assert (lo.side > 0).all() and (sh.side < 0).all()
    assert len(lo.side) + len(sh.side) == len(both.side)


# ---------------------------------------------------------------- the catalogue schema
def test_the_sample_catalogue_loads_with_twenty_specs_covering_the_engine_vocabulary() -> None:
    cat = V.load_catalogue(SAMPLE)
    assert len(cat.specs) == 20 and len({s.id for s in cat.specs}) == 20
    assert [s.rank for s in cat.specs] == list(range(1, 21))
    assert {s.entry.type for s in cat.specs} == set(ENTRY_EXAMPLES)
    assert {f.type for s in cat.specs for f in s.filters} == set(FILTER_EXAMPLES)
    assert {s.exit.type for s in cat.specs} == set(EXIT_EXAMPLES)
    assert {s.tf for s in cat.specs} == set(V.TIMEFRAMES)
    assert len(cat.sha256) == 64


def _write(tmp_path: Path, specs: list[dict]) -> Path:
    p = tmp_path / "cat.yaml"
    p.write_text(yaml.safe_dump({"version": "t", "specs": specs}))
    return p


def _raw_specs() -> list[dict]:
    return list(yaml.safe_load(SAMPLE.read_text())["specs"])


def test_a_catalogue_with_unknown_types_fails_and_lists_every_one(tmp_path: Path) -> None:
    specs = _raw_specs()
    specs[0]["entry"] = {"type": "fib_break", "params": {"n": 3}}
    specs[1]["filters"] = [{"type": "moon_phase", "params": {}}]
    specs[2]["exit"] = {"type": "breakeven_then_trail", "params": {}}
    with pytest.raises(V.UnknownVocabulary) as e:
        V.load_catalogue(_write(tmp_path, specs))
    msg = str(e.value)
    for name in ("fib_break", "moon_phase", "breakeven_then_trail", "S3K-0001", "S3K-0002", "S3K-0003"):
        assert name in msg


def test_specs_using_the_pending_level_types_load_and_are_marked_not_evaluable(tmp_path: Path) -> None:
    specs = _raw_specs()
    specs[0]["entry"] = {"type": "level_break", "params": {"min_strength_pct": 80, "min_tf": "4h"}}
    specs[1]["filters"] = [{"type": "room_to_level", "params": {"min_r": 2.0}}]
    specs[2]["entry"] = {"type": "level_reject", "params": {"min_strength_pct": 80, "min_tf": "1h"}}
    specs[3]["filters"] = [{"type": "near_level", "params": {"max_dist_atr": 0.5, "min_strength_pct": 70}}]
    cat = V.load_catalogue(_write(tmp_path, specs))
    assert len(cat.specs) == 20
    assert [s.id for s in cat.specs if s.pending] == ["S3K-0001", "S3K-0002", "S3K-0003", "S3K-0004"]
    assert cat.specs[0].pending == ("level_break",) and cat.specs[1].pending == ("room_to_level",)


REAL = Path(__file__).resolve().parents[2] / "config" / "research" / "sprint3000_catalogue.yaml"


@pytest.mark.skipif(not REAL.exists(), reason="the catalogue lane's file (PR #274) is not on this branch yet")
def test_the_real_catalogue_has_no_unknown_type_beyond_the_four_level_primitives() -> None:
    cat = V.load_catalogue(REAL)
    assert len(cat.specs) == 3000
    from goldbot.research.sprint3000 import engine as E
    level = V.PENDING_ENTRY | V.PENDING_FILTER
    assert {t for s in cat.specs for t in s.pending} <= level
    assert sum(1 for s in cat.specs if {s.entry.type, *(f.type for f in s.filters)} & level) == 379
    assert sum(1 for s in cat.specs if E.is_null_control(s)) == 120


@pytest.mark.parametrize(("path", "value"), [
    (("entry", "params"), {"n": 30, "m": 2}),            # extra parameter
    (("entry", "params"), {}),                           # missing parameter
    (("tf",), "5m"),                                     # timeframe outside the vocabulary
    (("side",), "both"),
    (("stop",), {"atr_n": 14, "atr_mult": 0}),
    (("max_hold_bars",), 0),
])
def test_a_spec_outside_the_schema_is_refused(tmp_path: Path, path: tuple[str, ...], value: object) -> None:
    specs = _raw_specs()
    node = specs[0]
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    with pytest.raises(ValueError):
        V.load_catalogue(_write(tmp_path, specs))


def test_duplicate_ids_or_ranks_are_refused(tmp_path: Path) -> None:
    specs = _raw_specs()
    specs[1]["id"] = specs[0]["id"]
    with pytest.raises(ValueError, match="duplicate"):
        V.load_catalogue(_write(tmp_path, specs))
    specs = _raw_specs()
    specs[1]["rank"] = 1
    with pytest.raises(ValueError, match="duplicate"):
        V.load_catalogue(_write(tmp_path, specs))


def test_a_plugin_module_adds_an_entry_type_without_touching_the_engine(tmp_path: Path) -> None:
    from goldbot.base import FrozenRecord

    class Always(FrozenRecord):
        every: int

    def always(p: Always, b: V.Bars, mk: Market | None) -> tuple[np.ndarray, np.ndarray]:
        L = np.zeros(len(b.c), dtype=bool)
        L[:: p.every] = True
        return L, np.zeros(len(b.c), dtype=bool)

    V.register_entry("test_always", Always)(always)
    try:
        specs = _raw_specs()
        specs[0]["entry"] = {"type": "test_always", "params": {"every": 5}}
        cat = V.load_catalogue(_write(tmp_path, specs))
        assert cat.specs[0].entry.type == "test_always"
    finally:
        V.ENTRY.pop("test_always")
