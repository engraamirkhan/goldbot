"""smc_structure (docs/research/prereg-tv-replica-2026-10-18.md, B2 and the research director's B4a): TVR-1 internal
CHoCH with the swing trend and an FVG, TVR-2 swing BOS continuation; 15m, stop 1.0 / target 2.0 ATR(14), 32 bars.
Candidates equal the written definitions, no signal or label changes when later bars are appended, the series start
is pinned, one trade at a time, stop first on a shared bar, and the family stays out of the loop grid."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features import smc_replica as smc
from goldbot.features.smc_live import SERIES_START, STATE_KEY
from goldbot.features.technical import atr
from goldbot.labels.triple_barrier import one_at_a_time, triple_barrier
from goldbot.ops.jobs import research_families
from goldbot.research.generator import GRID_V1, _eligible, grid_members
from goldbot.research.pipeline import pooled_members
from goldbot.research.population import Population
from goldbot.research.preregistration import resolve_variant
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.smc_structure import SmcStructureConfig, SmcStructureSpecialist

TVR1 = SmcStructureSpecialist(**resolve_variant("smc_structure", "tvr1"))
TVR2 = SmcStructureSpecialist(**resolve_variant("smc_structure", "tvr2"))


@pytest.fixture(scope="module")
def bars() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(15m store bars, their mid bars) over five months of synthetic ticks."""
    dec = resample_bars(ticks_to_1m(synthetic_ticks("2024-01-01", "2024-06-01", ticks_per_minute=1, seed=17)),
                        "15m").reset_index(drop=True)
    return dec, mid(dec)


def _reference(m: pd.DataFrame, rule: str) -> pd.DataFrame:
    """B2's definitions written out bar by bar from the replica's own event arrays."""
    o, h, lo, c = (m[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    st = smc.structure_breaks(o, h, lo, c, 50, 5)
    fb, fs = smc.fair_value_gaps(o, h, lo, c)
    rows = []
    for t in range(len(m)):
        if rule == "tvr1":
            if t < 1:
                continue
            if st["choch_bull_internal"][t] and st["trend_swing"][t - 1] == 1 and fb[max(0, t - 2):t + 1].any():
                rows.append((t, 1))
            if st["choch_bear_internal"][t] and st["trend_swing"][t - 1] == -1 and fs[max(0, t - 2):t + 1].any():
                rows.append((t, -1))
        else:
            if st["bos_bull_swing"][t]:
                rows.append((t, 1))
            if st["bos_bear_swing"][t]:
                rows.append((t, -1))
    return pd.DataFrame(rows, columns=["idx", "side"]).astype(int)


def test_the_presets_are_the_preregistered_rules_on_15m_with_one_and_two_atr_barriers_and_32_bars():
    assert SPECIALISTS["smc_structure"] is SmcStructureSpecialist
    for spec, rule in ((TVR1, "tvr1"), (TVR2, "tvr2")):
        ls = spec.label_spec
        assert spec.timeframe == "15m" and spec.timeframes == ()
        assert spec.config["rule"] == rule
        assert (ls.stop_atr, ls.target_atr, ls.max_bars) == (1.0, 2.0, 32)
        assert spec.exit_spec is None and spec.screening and not spec.requires_tv
        assert 0 < len(spec.model_features) <= 39
    assert TVR1.config == {"rule": "tvr1", "swing_len": 50, "internal_len": 5, "fvg_lookback": 2,
                           "target_atr": 2.0, "stop_atr": 1.0, "max_bars": 32}
    # TVR-2 reads neither the internal scale nor the gaps: those settings are inert, so they are not in its id
    assert TVR2.config == {"rule": "tvr2", "swing_len": 50, "target_atr": 2.0, "stop_atr": 1.0, "max_bars": 32}


def test_a_configuration_hashes_the_same_whatever_spelling_of_its_typed_values():
    assert SmcStructureSpecialist().agent_id == TVR1.agent_id                     # tvr1 is the default
    assert SmcStructureSpecialist(stop_atr=1, target_atr=2, max_bars=32.0).agent_id == TVR1.agent_id
    assert SmcStructureSpecialist(rule="tvr2", internal_len=9, fvg_lookback=0).agent_id == TVR2.agent_id
    assert SmcStructureSpecialist(rule="tvr1", fvg_lookback=1).agent_id != TVR1.agent_id
    assert isinstance(SmcStructureSpecialist(stop_atr=1).config["stop_atr"], float)


@pytest.mark.parametrize("bad", [{"rule": "tvr3"}, {"stop_atr": 0.0}, {"swing_len": 1}, {"fvg_lookback": -1},
                                 {"max_bars": 0}, {"max_bars": 2.5}])
def test_an_invalid_setting_is_refused_not_coerced(bad):
    with pytest.raises(ValueError):
        SmcStructureSpecialist(**bad)
    assert set(SmcStructureConfig.model_fields) == set(SmcStructureSpecialist.default_config)


@pytest.mark.parametrize("spec", [TVR1, TVR2], ids=["tvr1", "tvr2"])
def test_candidate_bars_equal_the_preregistered_definition(bars, spec):
    _, m = bars
    got = spec.candidates(m, pd.DataFrame(index=m.index))
    want = _reference(m, spec.config["rule"])
    assert len(want) >= 5                                    # the synthetic sample has events of both kinds
    assert set(want["side"]) == {1, -1}
    pd.testing.assert_frame_equal(got[["idx", "side"]].reset_index(drop=True), want)


def test_tvr1_needs_the_swing_trend_of_the_previous_bar_and_a_gap_within_two_bars(bars):
    _, m = bars
    o, h, lo, c = (m[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    st = smc.structure_breaks(o, h, lo, c)
    got = TVR1.candidates(m, pd.DataFrame(index=m.index))
    chochs = int(st["choch_bull_internal"].sum() + st["choch_bear_internal"].sum())
    assert 0 < len(got) < chochs                             # the trend and gap filters remove most CHoCHs
    wider = SmcStructureSpecialist(fvg_lookback=10).candidates(m, pd.DataFrame(index=m.index))
    assert set(got["idx"]) <= set(wider["idx"])


@pytest.mark.parametrize("spec", [TVR1, TVR2], ids=["tvr1", "tvr2"])
def test_appending_later_bars_never_changes_a_past_signal_or_label(bars, spec):
    """B4a causality: pivots confirm `length` bars late, so an event is stamped on the bar that makes it knowable;
    the signals and the labels that closed before the cut are identical on bars ..t and on bars ..t + n."""
    dec, m = bars
    full = spec.candidates(m, pd.DataFrame(index=m.index))
    a_full = atr(m, 14)
    lab_full = one_at_a_time(triple_barrier(dec, full, spec.label_spec, a_full))
    n = len(m)
    for cut in (n // 4, n // 2, (3 * n) // 4, n - 200):
        mc, dc = m.iloc[:cut].reset_index(drop=True), dec.iloc[:cut].reset_index(drop=True)
        part = spec.candidates(mc, pd.DataFrame(index=mc.index))
        pd.testing.assert_frame_equal(part.reset_index(drop=True),
                                      full[full["idx"] < cut].reset_index(drop=True))
        lab = one_at_a_time(triple_barrier(dc, part, spec.label_spec, atr(mc, 14)))
        closed = lab_full[lab_full["t_exit"] < cut - 1].reset_index(drop=True)
        pd.testing.assert_frame_equal(lab[lab["t_exit"] < cut - 1].reset_index(drop=True), closed)


@pytest.mark.parametrize("tf", ["15m", "1h"])
def test_every_replica_column_is_prefix_invariant(tf):
    """B4a: every replica column at bar t is identical computed on bars ..t and on bars ..t + n (n >= 200)."""
    dec = resample_bars(ticks_to_1m(synthetic_ticks("2024-01-01", "2024-04-01", ticks_per_minute=1, seed=3)), tf)
    m = mid(dec.reset_index(drop=True))
    full = smc.replica_frame(m)
    cut = len(m) - 250
    part = smc.replica_frame(m.iloc[:cut])
    pd.testing.assert_frame_equal(part, full.iloc[:cut])


def test_research_refuses_a_series_that_does_not_start_at_the_pinned_2010_start(bars):
    """B4a: the FVG running-mean threshold and the leg states start at the first bar of the 2010 Dukascopy series;
    a later start would give other events, so research refuses it rather than run another rule."""
    _, m = bars
    assert SERIES_START == pd.Timestamp("2010-01-01", tz="UTC")
    with pytest.raises(ValueError, match="2010-01-01"):
        TVR1.candidates_in_context(m, pd.DataFrame(index=m.index), None)
    early = m.copy()
    early["ts_utc"] = pd.date_range("2010-01-03 22:00", periods=len(m), freq="15min", tz="UTC")
    early["visible_at"] = early["ts_utc"] + pd.Timedelta("15min")
    got = TVR1.candidates_in_context(early, pd.DataFrame(index=early.index), None)
    pd.testing.assert_frame_equal(got, TVR1.candidates(early, pd.DataFrame(index=early.index)))


def test_one_trade_at_a_time_in_total_so_an_opposite_signal_in_a_trade_is_skipped(bars):
    """B4a: one position in total, not one per side (the account holds one 0.01 lot trade)."""
    dec, m = bars
    cands = pd.DataFrame({"idx": [100, 101], "side": [1, -1]})
    lab = one_at_a_time(triple_barrier(dec, cands, SmcStructureSpecialist(stop_atr=50.0, target_atr=50.0).label_spec,
                                       atr(m, 14)))
    assert list(lab["side"]) == [1]


def test_the_stop_counts_first_when_stop_and_target_fall_in_one_bar():
    """B4a: intrabar order is pessimistic: a bar that spans both barriers exits at the stop."""
    n = 40
    ts = pd.date_range("2024-01-02", periods=n, freq="15min", tz="UTC")
    px = np.full(n, 2000.0)
    dec = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta("15min"), "tick_count": 10, "spread_mean": 0.0})
    for side in ("bid", "ask"):
        dec[f"{side}_open"], dec[f"{side}_close"] = px, px
        dec[f"{side}_high"], dec[f"{side}_low"] = px + 0.5, px - 0.5
    dec.loc[25, ["bid_high", "ask_high"]] = 2100.0                    # one bar reaches both barriers
    dec.loc[25, ["bid_low", "ask_low"]] = 1900.0
    a = pd.Series(10.0, index=dec.index)
    lab = triple_barrier(dec, pd.DataFrame({"idx": [20], "side": [1]}), TVR1.label_spec, a)
    assert lab["ret"].iloc[0] < 0 and lab["t_exit"].iloc[0] == 25


def test_the_family_is_kept_out_of_the_loop_grid_the_pools_and_the_founders(tmp_path):
    settings = load_settings()
    assert "smc_structure" not in _eligible(settings)
    assert not any(f == "smc_structure" for f, _ in grid_members(settings))
    assert grid_members(settings) == GRID_V1
    assert "smc_structure" not in pooled_members("15m") and "smc_structure" not in research_families()
    pop = Population(tmp_path / "population.json")
    added = pop.ensure_founders(pd.Timestamp("2026-10-18", tz="UTC"))
    assert added and not any(aid.startswith("smc_structure-") for aid in added)


def test_research_pass_resolves_both_preregistered_presets():
    path = Path(__file__).resolve().parents[1] / "scripts" / "research_pass.py"
    spec = importlib.util.spec_from_file_location("research_pass_smc", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.parse_variants("smc_structure", '["tvr1", "tvr2"]') == [{"rule": "tvr1"}, {"rule": "tvr2"}]


def _counts_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "smc_counts.py"
    spec = importlib.util.spec_from_file_location("smc_counts", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_overlap_is_the_share_of_tvr2_events_within_four_bars_of_a_tvr1_event():
    mod = _counts_script()
    assert mod.overlap_share(np.array([10, 100]), np.array([6, 14, 15, 104, 200])) == pytest.approx(3 / 5)
    assert mod.overlap_share(np.array([], dtype=int), np.array([1])) == 0.0
    assert np.isnan(mod.overlap_share(np.array([1]), np.array([], dtype=int)))


def test_the_count_script_reads_no_holdout_row_and_imports_no_label_or_return_code():
    import ast
    mod = _counts_script()
    src = Path(mod.__file__).read_text()
    imported = {n.module for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom)}
    assert not any(m and (m.startswith("goldbot.labels") or m.endswith(("metrics", "pipeline", "screen")))
                   for m in imported)
    b1 = ticks_to_1m(synthetic_ticks("2024-01-01", "2024-02-15", ticks_per_minute=1, seed=5))
    shift = pd.Timestamp("2010-01-04", tz="UTC") - pd.Timestamp("2024-01-01", tz="UTC")
    b1 = b1.assign(ts_utc=b1["ts_utc"] + shift, visible_at=b1["visible_at"] + shift)
    r = mod.count(b1, "2010-02-01")
    assert r["last_bar"] < "2010-02-01" and r["first_bar"].startswith("2010-01-04")
    m = mid(resample_bars(b1[b1["ts_utc"] < pd.Timestamp("2010-02-01", tz="UTC")], "15m").reset_index(drop=True))
    for preset in ("tvr1", "tvr2"):
        spec = SmcStructureSpecialist(rule=preset)
        assert r["rules"][preset]["events"] == len(spec.candidates(m, pd.DataFrame(index=m.index)))
    assert "TVR-1" in mod.markdown(r)


def test_a_live_window_with_the_carried_state_decides_as_research_does_on_the_whole_series(bars):
    """Owner directive 2026-10-18: computed from our own bars live. The engine resumes the replica from the state
    `state_lag` bars back (`features.smc_live.ReplicaFeed`), so TVR-1/TVR-2 fire on the decision bar exactly when
    they fire there on the whole series."""
    _, m = bars
    cb = smc.context_bars()
    for spec in (TVR1, TVR2):
        full = set(spec.candidates(m, pd.DataFrame(index=m.index))["idx"])
        ends = sorted(i for i in full if i >= 3 * cb)[:15] + list(range(3 * cb, len(m), 211))
        assert len(ends) > 30
        for e in ends:
            snap = e - spec.state_lag                                           # the snapshot the feed keeps
            state = smc.replica_run(m.iloc[:snap + 1], spec.replica_params)[1]
            window = m.iloc[snap + 1 - cb:e + 1].reset_index(drop=True)
            got = spec.candidates_in_context(window, pd.DataFrame(index=window.index), None, {STATE_KEY: state})
            assert (len(got) > 0 and got["idx"].iloc[-1] == len(window) - 1) == (e in full), (spec.config["rule"], e)
            assert (got["idx"] >= len(window) - 1).all()                         # only bars after the snapshot



def test_the_specialist_reads_no_tradingview_value():
    import ast

    import goldbot.specialists.smc_structure as mod
    tree = ast.parse(Path(str(mod.__file__)).read_text())
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any("tv" in m.split(".")[-1] or "webhook" in m for m in imported)
    assert not SmcStructureSpecialist.requires_tv and SmcStructureSpecialist.entry_delay_s == 0
