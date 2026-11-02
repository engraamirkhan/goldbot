"""The sprint-3000 batch screen (goldbot/research/sprint3000/engine.py) under amendments W2 and C1: the 1m walk (stop
first, gap fills, trailing on prior minutes only), barrier parity with `triple_barrier_next_open_1m`, the C1 cost model
(point estimates in bps of the entry price, measured swap with shorts credited; equal cost in R in 2012 and 2025) beside
the dollar upper-bound stress line (`ArmCosts.screen_charges`), the random-side gate control and the drift adjustment
(a planted side edge with random timing passes), the same-side timing diagnostic, the windows and the 2025-10-01 cut,
the configured gates, the ensemble stage, BH-FDR, K_eff, the passer chains and the 20-spec sample end to end."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.base import FrozenRecord
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.labels import SwapSpec, rollover_nights
from goldbot.labels.triple_barrier import BarrierSpec, triple_barrier_next_open_1m
from goldbot.research import arms as A
from goldbot.research.sprint3000 import engine as E
from goldbot.research.sprint3000 import market as M
from goldbot.research.sprint3000 import vocab as V
from goldbot.research.sprint3000.control import h_u
from tests.research.sprint3000_bars import regime, synthetic_market, synthetic_minutes, window

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "tests" / "fixtures" / "sprint3000" / "sample_catalogue.yaml"
REAL = ROOT / "config" / "research" / "sprint3000_catalogue.yaml"
MIN = M.MIN
RULES = E.load_rules()
COSTS = A.load_costs(ROOT / "config" / "arm_costs.yaml")


def spec(**over: Any) -> V.Spec:
    base: dict[str, Any] = {
        "id": "S3K-9001", "rank": 1, "prior_score": 0.5, "family": "test", "tf": "1h",
        "entry": {"type": "donchian_break", "params": {"n": 10}}, "filters": [], "side": "signal",
        "stop": {"atr_n": 14, "atr_mult": 1.5}, "exit": {"type": "target_r", "params": {"r": 2.0}},
        "max_hold_bars": 12}
    base.update(over)
    return V.Spec.model_validate(base)


@pytest.fixture(scope="module")
def mk() -> M.Market:
    return synthetic_market(days=90, seed=21)


def tape(o: list[float], h: list[float], lo: list[float], c: list[float]) -> M.Tape:
    t0 = pd.Timestamp("2015-01-06 10:00", tz="UTC").value
    return M.Tape.of(t0 + np.arange(len(o), dtype=np.int64) * MIN, np.array(o, float), np.array(h, float),
                     np.array(lo, float), np.array(c, float))


# ---------------------------------------------------------------- the frozen configuration
def test_the_pass_rules_and_costs_come_from_config_as_amendment_c1_froze_them() -> None:
    r = RULES
    e = r.explore
    assert (e.min_n, e.min_n_slow, e.min_t_day, e.min_years_positive_share) == (100, 60, 2.0, 0.625)
    assert (e.min_control_excess, e.min_drift_adj_gross) == (0.0, 0.0)
    assert (r.confirm.min_n, r.confirm.fdr_q, r.confirm.min_years_positive_share) == (100, 0.10, 0.625)
    assert (r.test_old.min_n, r.test_old.min_n_slow, r.test_old.min_t_day) == (100, 60, 1.28)
    assert (r.cost.broker, r.cost.version, r.cost.reference_price_usd) == ("icm", "cor-2026-10-31.2", 4000.0)
    assert (r.cost.swap_long_usd_per_lot, r.cost.swap_short_usd_per_lot) == (-52.2, 36.0)
    assert (r.ensemble.min_gross_t, r.ensemble.min_members) == (2.0, 2)
    assert len(E.rules_sha256()) == 64


def test_the_windows_are_amendment_w2s_table() -> None:
    w = M.WINDOWS
    ts = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    assert (w["explore"].data_from, w["explore"].dec_from, w["explore"].end) == \
        (ts("2009-01-01"), ts("2010-01-01"), ts("2018-01-01"))                    # B3: 2009 is warm-up only
    assert (w["learn_old"].dec_from, w["learn_old"].end) == (ts("2003-01-01"), ts("2006-01-01"))
    assert (w["test_old"].dec_from, w["test_old"].end) == (ts("2006-02-01"), ts("2010-01-01"))   # 2006-01 embargo
    assert (w["confirm"].dec_from, w["confirm"].end) == (ts("2018-01-01"), M.HOLDOUT_FROM)
    assert w["confirm"].entry_years == tuple(range(2018, 2026))
    assert w["test_old"].entry_years == (2006, 2007, 2008, 2009)


# ---------------------------------------------------------------- the 1m walk
def test_a_minute_holding_both_stop_and_target_is_a_stop() -> None:
    tp = tape([100, 100], [100.5, 105], [99.5, 98.5], [100, 100])
    x = E.walk(tp, 0, 1, 1, 1.0, 2.0, np.nan, -1)
    assert (x.kind, x.k, x.gap) == ("stop", 1, False) and x.gross == pytest.approx(-1.0)


def test_a_gap_through_the_stop_fills_at_the_open_unless_parity_mode_fills_at_the_stop() -> None:
    tp = tape([100, 98], [100.5, 98.2], [99.5, 97.5], [100, 98])
    x = E.walk(tp, 0, 1, 1, 1.0, 2.0, np.nan, -1)
    assert x.gross == pytest.approx(-2.0) and x.gap
    assert E.walk(tp, 0, 1, 1, 1.0, 2.0, np.nan, -1, gap_at_open=False).gross == pytest.approx(-1.0)
    sh = tape([100, 102], [100.5, 102.5], [99.5, 101.8], [100, 102])        # a short gapped through its stop
    assert E.walk(sh, 0, 1, -1, 1.0, 2.0, np.nan, -1).gross == pytest.approx(-2.0)


def test_the_chandelier_trails_the_highest_high_of_earlier_minutes_only() -> None:
    tp = tape([100, 100.4, 102.8], [100.5, 103, 102.9], [99.8, 100.3, 100.9], [100.4, 102.8, 101])
    x = E.walk(tp, 0, 2, 1, 1.0, np.nan, 2.0, -1)
    # minute 1: stop from the highs before it (100.5 - 2 < 99), so its 100.3 low is no exit; minute 2: 103 - 2 = 101
    assert (x.kind, x.k) == ("trail", 2) and x.gross == pytest.approx(1.0)


def test_an_opposite_flag_exits_at_that_minutes_open_and_time_exits_at_the_last_close() -> None:
    tp = tape([100, 100.2, 100.6, 100.9], [100.3, 100.7, 101, 101.1], [99.9, 100.1, 100.5, 100.8],
              [100.2, 100.6, 100.9, 101])
    x = E.walk(tp, 0, 3, 1, 1.0, 5.0, np.nan, 2)
    assert (x.kind, x.k) == ("flag", 2) and x.gross == pytest.approx(0.6)
    y = E.walk(tp, 0, 1, -1, 1.0, 5.0, np.nan, -1)
    assert y.kind == "time" and y.gross == pytest.approx(-0.6) and y.t == tp.t[1] + MIN


def test_a_long_walk_finds_a_late_stop_across_search_chunks() -> None:
    n = 5000
    c = np.full(n, 100.0)
    lo = c - 0.1
    lo[4321] = 98.0
    tp = tape(list(c), list(c + 0.1), list(lo), list(c))
    x = E.walk(tp, 0, n - 1, 1, 1.0, 50.0, np.nan, -1)
    assert (x.kind, x.k) == ("stop", 4321)


# ---------------------------------------------------------------- the simulation
def test_every_entry_fills_at_the_open_of_the_first_minute_at_or_after_the_decision_close(mk: M.Market) -> None:
    real = E.simulate(spec(), mk).real
    assert len(real) >= 30
    k = np.searchsorted(mk.tape.t, real.t_dec.to_numpy(), side="left")
    assert np.array_equal(mk.tape.t[k], real.t_fill.to_numpy())
    assert np.allclose(mk.tape.o[k], real.entry.to_numpy())


def test_one_position_at_a_time(mk: M.Market) -> None:
    real = E.simulate(spec(entry={"type": "consecutive_bars", "params": {"n": 2, "mode": "follow"}}), mk).real
    assert (real.t_fill.to_numpy()[1:] >= real.t_exit.to_numpy()[:-1]).all()


def test_barrier_parity_with_triple_barrier_next_open_1m(mk: M.Market) -> None:
    """Every real trade of a target/stop/time spec equals the harness's label on the same decision bar: same barrier,
    same exit price (the synthetic tape has no minute gaps, the only place the engine's open fill differs)."""
    real = E.simulate(spec(), mk).real
    b = mk.bars["1h"]
    a = V.atr(b, 14)
    m1 = pd.DataFrame({"ts_utc": pd.to_datetime(mk.tape.t, utc=True)})
    bars = pd.DataFrame({"ts_utc": pd.to_datetime(b.dec - 3_600_000_000_000, utc=True),
                         "visible_at": pd.to_datetime(b.dec, utc=True)})
    for k, col, bc in (("open", mk.tape.o, b.o), ("high", mk.tape.h, b.h), ("low", mk.tape.l, b.l),
                       ("close", mk.tape.c, b.c)):
        m1[f"bid_{k}"] = m1[f"ask_{k}"] = col
        bars[f"bid_{k}"] = bars[f"ask_{k}"] = bc
    sig = pd.DataFrame({"idx": real.bar.to_numpy(), "side": real.side.to_numpy()})
    ref = triple_barrier_next_open_1m(bars, m1, sig, BarrierSpec(target_atr=3.0, stop_atr=1.5, max_bars=11), pd.Series(a))
    assert len(ref) == len(real) >= 30
    assert list(ref.barrier_hit) == list(real.kind)
    np.testing.assert_allclose(ref.side * (ref.exit - ref.entry), real.gross, atol=1e-9)
    np.testing.assert_allclose(ref.entry, real.entry, atol=1e-12)


# ---------------------------------------------------------------- costs (C1, and the dollar stress line)
def _swap_usd(real: pd.DataFrame, swap: SwapSpec) -> np.ndarray:
    ent = pd.DatetimeIndex(pd.to_datetime(real.t_fill, utc=True))
    ext = pd.DatetimeIndex(pd.to_datetime(real.t_exit, utc=True))
    return rollover_nights(ent, ext, swap.server_tz, swap.triple_weekday) * swap.usd_per_oz(real.side.to_numpy())


def test_the_gated_cost_is_c1_point_estimates_in_bps_and_the_stress_line_is_the_dollar_upper_bound(
        mk: M.Market) -> None:
    real = E.simulate(spec(exit={"type": "chandelier", "params": {"atr_n": 14, "mult": 2.0}}, max_hold_bars=48, tf="4h",
                           stop={"atr_n": 14, "atr_mult": 1.0}), mk).real
    p = E.price(real, COSTS)
    sess = DEFAULT_SESSIONS.session_label(pd.DatetimeIndex(pd.to_datetime(real.t_fill, utc=True)))
    stop_exit = real.kind.isin(["stop", "trail"]).to_numpy()
    assert len(p) >= 10 and stop_exit.any()
    # stress: exactly the shared screen arithmetic in today's dollars, upper bounds, the shipped swap
    sw_old = _swap_usd(real, SwapSpec.shipped())
    want_usd = A.screen_net_r(real.gross.to_numpy(), real.risk.to_numpy(), stop_exit, COSTS, broker="icm",
                              sessions=sess, version="cor-2026-10-31.2", extra_usd=-sw_old)
    np.testing.assert_allclose(p.net_r_usd, want_usd, atol=1e-12)
    # gated: the cost of record's POINT estimates, scaled to the entry price; the measured swap; no overshoot on a gap
    cells = COSTS.require_version("cor-2026-10-31.2").cells["icm"]
    rt = np.array([cells[s].round_trip_usd for s in sess])
    os_ = np.array([cells[s].overshoot_usd for s in sess])
    c1 = SwapSpec(long_usd_per_lot=-52.2, short_usd_per_lot=36.0)
    s = real.entry.to_numpy() / 4000.0
    want = (real.gross.to_numpy() - rt * s - os_ * s * (stop_exit & ~real.gap.to_numpy()) + _swap_usd(real, c1) * s) \
        / real.risk.to_numpy()
    np.testing.assert_allclose(p.net_r, want, atol=1e-12)
    np.testing.assert_allclose(p.gross_r - p.net_r, p.cost_r, atol=1e-12)


def test_the_same_atr_multiple_stop_costs_the_same_r_in_2012_and_2025() -> None:
    """C1's test: costs in bps of the entry price, so a stop of the same ATR multiple (ATR stable in bps) costs the same
    R at $1,600 and at $4,000 (the dollar line charged 2.5x more at the 2012 price)."""
    t12 = pd.Timestamp("2012-03-07 10:00", tz="UTC").value
    t25 = pd.Timestamp("2025-03-05 10:00", tz="UTC").value
    df = pd.DataFrame({"t_fill": [t12, t25], "t_exit": [t12 + 26 * 3600 * 10**9, t25 + 26 * 3600 * 10**9],
                       "side": [1, 1], "entry": [1600.0, 4000.0], "risk": [1600 * 0.004, 4000 * 0.004],
                       "gross": [0.0, 0.0], "kind": ["stop", "stop"], "gap": [False, False], "id": [0, 1]})
    p = E.price(df, COSTS)
    assert p.cost_r.iloc[0] == pytest.approx(p.cost_r.iloc[1], rel=0.05)
    assert p.cost_r.iloc[0] == pytest.approx(p.cost_r.iloc[1], rel=1e-9)
    assert p.net_r_usd.iloc[0] < p.net_r_usd.iloc[1] - 0.1                         # the old dollar line did not


def test_an_overnight_short_is_credited_its_swap_and_a_long_pays() -> None:
    t = pd.Timestamp("2015-01-06 15:00", tz="UTC").value                            # Tuesday, held over one rollover
    df = pd.DataFrame({"t_fill": [t, t], "t_exit": [t + 20 * 3600 * 10**9] * 2, "side": [-1, 1], "entry": [4000.0] * 2,
                       "risk": [10.0] * 2, "gross": [0.0, 0.0], "kind": ["time", "time"], "gap": [False] * 2,
                       "id": [0, 1]})
    p = E.price(df, COSTS)
    rt_r = 0.166 / 10                                                              # newyork point round trip at 4,000
    assert p.net_r.iloc[0] == pytest.approx(-rt_r + 0.36 / 10)                     # short: credited 0.36 / oz
    assert p.net_r.iloc[1] == pytest.approx(-rt_r - 0.522 / 10)                    # long: pays 0.522 / oz
    with pytest.raises(ValueError, match="pinned"):
        E.price(df, COSTS, rules=RULES.model_copy(update={"cost": RULES.cost.model_copy(update={"version": "x"})}))


# ---------------------------------------------------------------- controls
def test_the_random_side_control_is_the_same_minute_stop_and_exit_with_the_side_flipped(mk: M.Market) -> None:
    sim = E.simulate(spec(), mk)
    r, o = sim.real.set_index("id"), sim.opposite.set_index("id")
    assert len(r) >= 30 and o.index.equals(r.index)
    assert (o.t_fill == r.t_fill).all() and (o.side == -r.side).all() and (o.risk == r.risk).all()
    assert (o.entry == r.entry).all()


def test_the_same_side_timing_twins_never_enter_before_the_real_fill(mk: M.Market) -> None:
    sim = E.simulate(spec(tf="15m", max_hold_bars=16), mk)
    real, tw = sim.real, sim.timing
    assert len(tw) >= 3 * len(real) >= 90
    r = real.set_index("id")
    assert (tw.t_fill.to_numpy() >= r.t_fill.reindex(tw.id).to_numpy()).all()
    assert (tw.side.to_numpy() == r.side.reindex(tw.id).to_numpy()).all()
    k0 = np.searchsorted(mk.tape.t, r.t_fill.reindex(tw.id).to_numpy())
    kt = np.searchsorted(mk.tape.t, tw.t_fill.to_numpy())
    hi = np.maximum(mk.run_end[k0], np.searchsorted(mk.tape.t, mk.tape.t[k0] + 60 * MIN) - 1)
    assert (kt <= hi).all()


def test_the_controls_are_deterministic(mk: M.Market) -> None:
    a = E.simulate(spec(tf="15m", max_hold_bars=16), mk)
    b = E.simulate(spec(tf="15m", max_hold_bars=16), mk)
    pd.testing.assert_frame_equal(a.timing, b.timing)
    pd.testing.assert_frame_equal(a.opposite, b.opposite)


def test_drift_adjusted_gross_removes_the_window_drift_an_always_long_rule_rides() -> None:
    mkt = synthetic_market(days=150, seed=9, drift=0.003)
    sp = spec(entry={"type": "momentum_sign", "params": {"lookback_bars": 3}}, side="long_only",
              exit={"type": "time", "params": {"bars": 6}}, max_hold_bars=6, stop={"atr_n": 14, "atr_mult": 3.0})
    res, _, _ = E.run_spec(sp, mkt, RULES, 6233)
    assert res.gross_r is not None and res.gross_r > 0.15
    assert res.drift_adj_gross_r is not None and abs(res.drift_adj_gross_r) < 0.25 * res.gross_r   # mostly drift


class Planted(FrozenRecord):
    share: float
    regime_days: float
    t0: int


def test_a_planted_side_edge_with_random_timing_passes_explore() -> None:
    """C1's test: the side carries a 0.2R edge (it follows a drift that flips every 10 days, zero net), the timing is
    random. The random-side control and the drift adjustment pass it; the old same-side twin saw no excess."""
    t0 = pd.Timestamp("2012-01-02", tz="UTC").value

    def planted(p: Planted, b: V.Bars, mk: M.Market | None) -> tuple[np.ndarray, np.ndarray]:
        fire = np.array([h_u("planted", int(d)) < p.share for d in b.dec])
        side = regime(b.dec, p.t0, p.regime_days)
        return fire & (side > 0), fire & (side < 0)

    V.register_entry("test_planted_side", Planted)(planted)
    try:
        mkt = synthetic_market(days=420, seed=17, drift=0.0018, regime_days=10)
        sp = spec(entry={"type": "test_planted_side", "params": {"share": 0.12, "regime_days": 10, "t0": t0}},
                  exit={"type": "time", "params": {"bars": 6}}, max_hold_bars=6, stop={"atr_n": 14, "atr_mult": 2.0})
        res, _, _ = E.run_spec(sp, mkt, RULES, 6233)
    finally:
        V.ENTRY.pop("test_planted_side")
    assert res.n >= 300 and res.gross_r is not None and 0.12 < res.gross_r < 0.3
    assert res.delta is not None and res.delta > 0.1                                 # random-side excess
    assert res.timing_delta is not None and abs(res.timing_delta) < 0.05            # the old control saw nothing
    assert res.passed, res.gates


# ---------------------------------------------------------------- the simulation's exits
def test_a_time_exit_never_holds_past_its_bar_count(mk: M.Market) -> None:
    real = E.simulate(spec(exit={"type": "time", "params": {"bars": 3}}, max_hold_bars=10), mk).real
    b = mk.bars["1h"]
    assert len(real) >= 20
    assert (real.t_exit.to_numpy() <= b.dec[real.bar.to_numpy() + 3]).all()
    assert (real.kind == "time").any()


def test_an_opposite_signal_exit_fills_at_the_first_opposite_signal_after_the_fill(mk: M.Market) -> None:
    sp = spec(entry={"type": "ma_cross", "params": {"fast": 5, "slow": 20, "kind": "ema"}},
              exit={"type": "opposite_signal", "params": {}}, stop={"atr_n": 14, "atr_mult": 6.0}, max_hold_bars=200)
    real = E.simulate(sp, mk).real
    b = mk.bars["1h"]
    L, S = V.entry_signals(sp.entry, b, mk)
    flagged = real[real.kind == "flag"]
    assert len(flagged) >= 5
    for side, fill, exit_ in zip(flagged.side.to_numpy(), flagged.t_fill.to_numpy(), flagged.t_exit.to_numpy()):
        opp = b.dec[S if side > 0 else L]
        first = opp[opp > fill][0]
        assert exit_ == mk.tape.t[np.searchsorted(mk.tape.t, first)]


def test_a_trade_belongs_to_the_window_of_its_entry() -> None:
    m = synthetic_minutes(start="2012-01-02", days=120, seed=8)
    w = window("2012-01-02", "2012-05-01", dec_from="2012-03-01")
    mkt = M.Market.from_minutes(m, w)
    real = E.simulate(spec(), mkt).real
    assert len(real) >= 10
    assert (real.t_dec >= w.dec_from.value).all() and (real.t_fill < w.end.value).all()


# ---------------------------------------------------------------- point-in-time truncation
def test_no_spec_signal_risk_or_level_before_the_cut_changes_when_later_bars_are_removed() -> None:
    m = synthetic_minutes(days=200, seed=13)
    cut = pd.Timestamp("2012-06-01 13:37", tz="UTC")
    full = M.Market.from_minutes(m, window("2012-01-02", "2012-07-25"))
    part = M.Market.from_minutes(m[m.t < cut.value].reset_index(drop=True), window("2012-01-02", "2012-06-02"))
    for sp in V.load_catalogue(SAMPLE).specs:
        f, p = V.spec_signals(sp, full), V.spec_signals(sp, part)
        keep_f, keep_p = f.dec <= cut.value, p.dec <= cut.value
        assert np.array_equal(f.dec[keep_f], p.dec[keep_p]), sp.id
        assert np.array_equal(f.side[keep_f], p.side[keep_p]), sp.id
        np.testing.assert_allclose(f.risk[keep_f], p.risk[keep_p], err_msg=sp.id)
    for s in ("asia", "london", "ny"):
        fi, pi = V.session_instances(full, s), V.session_instances(part, s)
        done = pi.end <= cut.value
        assert done.sum() >= 50
        for col in ("start", "end", "hi", "lo"):
            np.testing.assert_array_equal(getattr(fi, col)[: done.sum()], getattr(pi, col)[done])
    for lv in ("day", "week", "month"):
        fl, pl = V.prior_levels(full, full.bars["1h"], lv), V.prior_levels(part, part.bars["1h"], lv)
        k = int(np.searchsorted(part.bars["1h"].dec, cut.value, side="right"))
        np.testing.assert_array_equal(fl[0][:k], pl[0][:k])
        np.testing.assert_array_equal(fl[1][:k], pl[1][:k])


# ---------------------------------------------------------------- the 2025-10-01 cut and the windows on disk
def _fake_raw(tmp: Path, years: tuple[int, ...] = (2017, 2018, 2024, 2025, 2026)) -> Path:
    for y in years:
        ts = pd.date_range(f"{y}-01-01", f"{y}-12-31 23:00", freq="7h", tz="UTC")
        df = pd.DataFrame({"ts_utc": ts})
        for k in ("open", "high", "low", "close"):
            df[f"bid_{k}"] = 1.0
            df[f"ask_{k}"] = 1.2
        df.to_parquet(tmp / f"xauusd_1m_dukascopy_{y}.parquet")
    return tmp


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    opened: list[str] = []
    real_read = pd.read_parquet

    def spy(path: Any, *a: Any, **k: Any) -> pd.DataFrame:
        opened.append(Path(path).name)
        return real_read(path, *a, **k)

    monkeypatch.setattr(M.pd, "read_parquet", spy)
    return opened


def test_nothing_at_or_after_2025_10_01_is_ever_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _fake_raw(tmp_path)
    opened = _spy(monkeypatch)
    m = M.load_minutes(raw, pd.Timestamp("2024-01-01", tz="UTC"), M.HOLDOUT_FROM)
    assert int(m.t.max()) < M.HOLDOUT_FROM.value
    assert "xauusd_1m_dukascopy_2026.parquet" not in opened
    with pytest.raises(ValueError, match="holdout"):
        M.load_minutes(raw, pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-10-02", tz="UTC"))
    assert M.HOLDOUT_FROM == pd.Timestamp("2025-10-01", tz="UTC")
    assert M.WINDOWS["confirm"].end == M.HOLDOUT_FROM


def test_explore_never_opens_a_confirm_year(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _fake_raw(tmp_path)
    opened = _spy(monkeypatch)
    m = M.load_minutes(raw, pd.Timestamp("2017-01-01", tz="UTC"), M.WINDOWS["explore"].end)
    assert opened == ["xauusd_1m_dukascopy_2017.parquet"] and int(m.t.max()) < M.WINDOWS["explore"].end.value


def test_a_market_refuses_minutes_past_the_cut() -> None:
    m = synthetic_minutes(start="2025-09-25", days=10)
    with pytest.raises(AssertionError, match="holdout"):
        M.Market.from_minutes(m, window("2025-09-25", "2025-10-01"))
    with pytest.raises(ValueError, match="holdout"):
        window("2025-09-25", "2025-10-02")


def test_a_window_whose_years_are_not_on_disk_reports_no_data(tmp_path: Path) -> None:
    raw = _fake_raw(tmp_path, (2017,))
    mkt = M.Market.load(raw, M.WINDOWS["test_old"])
    assert mkt.years_missing == (2006, 2007, 2008, 2009) and len(mkt.tape.t) == 0
    rep = E.run_screen(V.load_catalogue(SAMPLE), mkt, workers=1, cache_dir=tmp_path)
    assert rep.years_missing == [2006, 2007, 2008, 2009] and rep.status == "NO-DATA"
    assert {r.verdict for r in rep.results} == {"NO-DATA"} and not any(r.passed for r in rep.results)


# ---------------------------------------------------------------- gates
def _by_year(per_year: int, mean: float, years: range, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Net R with exactly `mean` in every year (sd 1 within each year) and the year of each trade."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in years:
        z = rng.standard_normal(per_year)
        out.append(mean + (z - z.mean()) / z.std(ddof=1))
    return np.concatenate(out), np.repeat(np.array(list(years)), per_year)


def _priced(net: np.ndarray, years: np.ndarray, per_day: int = 1, opp: float | np.ndarray = -0.1) -> pd.DataFrame:
    n = len(net)
    day0 = np.array([pd.Timestamp(f"{y}-01-01", tz="UTC").value for y in years], dtype=np.int64)
    within = np.concatenate([np.arange((years == y).sum()) for y in np.unique(years)]) // per_day
    t = day0 + within * M.DAY + 10 * M.HOUR
    return pd.DataFrame({"id": np.arange(n), "t_fill": t, "net_r": net, "net_r_usd": net, "gross_r": net + 0.05,
                         "stress_r": net - 0.05, "cost_r": np.full(n, 0.05), "side": np.where(np.arange(n) % 3, 1, -1),
                         "risk": np.full(n, 3.0), "kind": ["target"] * n, "year": years,
                         "opp_net_r": np.broadcast_to(opp, n).copy(), "drift_adj_gross_r": net + 0.05})


EXPLORE_YEARS = range(2010, 2018)


def test_explore_passes_on_count_day_summed_t_years_drift_and_the_random_side_control() -> None:
    sp = spec()
    net, yrs = _by_year(19, 0.2, EXPLORE_YEARS)                                      # 152 trades, t_day 2.47
    res = E.summarize(sp, _priced(net, yrs), "explore")
    assert res.passed and set(res.gates) == {"n", "net", "t_day", "years", "drift_adj", "control"}
    assert (res.legs["long"]["n"] or 0) + (res.legs["short"]["n"] or 0) == res.n
    assert not E.summarize(sp, _priced(net, yrs, opp=0.5), "explore").gates["control"]   # below its random side
    lose = net.copy()
    lose[yrs >= 2014] -= 0.5                                                            # 4 of 8 years positive
    assert not E.summarize(sp, _priced(lose + 0.2, yrs), "explore").gates["years"]
    few, fy = _by_year(9, 0.5, EXPLORE_YEARS)                                          # n 72
    assert E.summarize(sp, _priced(few, fy), "explore").verdict == "FAIL-COUNT"
    assert E.summarize(spec(tf="4h"), _priced(few, fy), "explore").gates["n"]          # 60 on the slow timeframes


def test_explore_t_is_on_day_summed_r() -> None:
    sp = spec()
    z, yrs = _by_year(19, 0.0, EXPLORE_YEARS, seed=3)
    paired = _priced(np.repeat(0.12 + z, 2), np.repeat(yrs, 2), per_day=2)           # pairs share one move
    res = E.summarize(sp, paired, "explore")
    assert res.t is not None and res.t_day is not None
    assert res.t > 2.0 and res.t_day < 2.0 and not res.gates["t_day"]


def test_test_old_needs_n_net_and_t_of_1_28() -> None:
    net, yrs = _by_year(18, 0.2, range(2006, 2010))                                    # n 72, t_day 1.70
    assert not E.summarize(spec(tf="1h"), _priced(net, yrs), "test_old").passed       # n 72 < 100
    assert E.summarize(spec(tf="4h"), _priced(net, yrs), "test_old").passed           # n >= 60
    weak, wy = _by_year(18, 0.1, range(2006, 2010))                                    # t 0.85
    assert not E.summarize(spec(tf="4h"), _priced(weak, wy), "test_old").passed


def test_confirm_gates_on_n_net_and_years_with_no_dsr_term_and_leaves_bh_to_the_run() -> None:
    net, yrs = _by_year(50, 0.3, range(2018, 2026))
    good = E.summarize(spec(), _priced(net, yrs), "confirm")
    assert set(good.gates) == {"n", "net", "years"} and good.passed
    assert good.p_boot is not None and good.p_boot < 0.001
    assert good.ex2025 is not None and good.ex2025["n"] == 350
    neg = net.copy()
    for y in (2018, 2019, 2020, 2021):
        neg[yrs == y] -= 2.0
    assert not E.summarize(spec(), _priced(neg + 1.0, yrs), "confirm").gates["years"]   # 4 of 8


def test_bh_fdr_rejects_the_step_up_set_over_m_fixed_before_the_run() -> None:
    p = np.array([0.001, 0.02, 0.03, 0.2])
    assert E.bh_fdr(p, m=4, q=0.10).tolist() == [True, True, True, False]
    assert E.bh_fdr(p, m=40, q=0.10).tolist() == [True, False, False, False]          # m counts what was sent


def test_the_day_block_bootstrap_p_is_small_for_a_real_edge_and_near_a_half_for_none() -> None:
    edge, _ = _by_year(500, 0.3, range(2012, 2013))
    none, _ = _by_year(500, 0.0, range(2012, 2013))
    assert E.boot_p(edge, 2000, seed=1) < 0.001
    assert 0.3 < E.boot_p(none, 2000, seed=1) < 0.7


def test_k_eff_merges_specs_whose_daily_returns_move_together() -> None:
    rng = np.random.default_rng(0)
    a, c = rng.standard_normal(300), rng.standard_normal(300)
    x = np.vstack([a, a + 0.05 * rng.standard_normal(300), c, np.zeros(300)])
    assert E.k_eff(x, rho=0.5) == 3


# ---------------------------------------------------------------- the ensemble stage
def _res(i: int, family: str = "f", null: bool = False, passed: bool = False, status: str = "evaluated",
         gross_t: float | None = None) -> E.SpecResult:
    return E.SpecResult.model_validate({"id": f"S3K-{i:04d}", "rank": i, "family": family, "tf": "1h",
                                        "window": "explore", "null_control": null, "passed": passed,
                                        "status": status, "gross_t": gross_t})


def test_an_ensemble_per_family_holds_its_explore_passers_and_its_gross_t_2_members() -> None:
    res = [_res(1, "a", passed=True), _res(2, "a", gross_t=2.4), _res(3, "a", gross_t=1.9), _res(4, "b", passed=True),
           _res(5, "c", gross_t=2.1), _res(6, "c", gross_t=3.0), _res(7, "c", status="not_evaluable", gross_t=None)]
    ens = E.build_ensembles(res, RULES)
    assert [(e.id, e.members) for e in ens] == [("ENS-a", ("S3K-0001", "S3K-0002")),
                                                ("ENS-c", ("S3K-0005", "S3K-0006"))]   # b has one member: no ensemble


def test_an_ensemble_is_the_equal_weight_composite_of_its_members_daily_net_r() -> None:
    days = np.arange(400, dtype=np.int64) + 15_000
    rng = np.random.default_rng(1)
    a, b = 0.2 + rng.standard_normal(400), 0.2 + rng.standard_normal(400)
    ens = E.Ensemble(id="ENS-x", family="x", members=("S3K-0001", "S3K-0002"))
    r = E.ensemble_result(ens, {"S3K-0001": (days, a, 400), "S3K-0002": (days[::2], b[::2], 200)}, "confirm", RULES)
    comp = a.copy()
    comp[::2] += b[::2]
    assert r.n == 600 and r.n_days == 400
    assert r.net_r_day == pytest.approx(float(np.mean(comp / 2)), abs=1e-6)
    assert r.p_boot is not None and set(r.gates) == {"n", "net", "years"}


# ---------------------------------------------------------------- null controls
def test_the_null_controls_are_the_catalogue_grade_d_specs() -> None:
    cat = {s.id: s for s in V.load_catalogue(SAMPLE).specs}
    assert E.is_null_control(cat["S3K-0007"])                                          # inside_bar_break
    assert E.is_null_control(cat["S3K-0008"]) and E.is_null_control(cat["S3K-0009"])   # session / opening range
    assert not E.is_null_control(cat["S3K-0001"]) and not E.is_null_control(cat["S3K-0018"])
    assert E.is_null_control(spec(entry={"type": "zscore", "params": {"n": 20, "z": 2.0, "mode": "fade"}}))


def test_more_than_a_tenth_of_nulls_passing_explore_voids_the_run() -> None:
    ten = [_res(i, null=True, passed=i < 1) for i in range(10)] + [_res(99, null=True, passed=True,
                                                                         status="not_evaluable")]
    nc, void, audit = E.null_check("explore", ten, RULES)
    assert (nc.n, nc.evaluated, nc.passed, void, audit) == (11, 10, 1, False, False)     # 10%: not more than
    two = [_res(i, null=True, passed=i < 2) for i in range(10)] + [_res(50, passed=True)]
    assert E.null_check("explore", two, RULES)[1]
    assert not E.null_check("learn_old", two, RULES)[1]                               # ruling (b): report-only


def test_any_null_passing_confirm_or_test_old_requires_an_engine_audit() -> None:
    one = [_res(1, null=True, passed=True), _res(2, passed=True)]
    assert E.null_check("confirm", one, RULES)[2] and E.null_check("test_old", one, RULES)[2]
    assert not E.null_check("confirm", [_res(1, null=True), _res(2, passed=True)], RULES)[2]
    assert not E.null_check("confirm", one, RULES)[1]


# ---------------------------------------------------------------- passer chains
def _report(cat: V.Catalogue, passing: set[str], window: str = "explore", sha: str | None = None,
            ensembles: list[dict[str, Any]] | None = None, **over: Any) -> dict[str, Any]:
    return {"window": window, "catalogue_sha256": sha or cat.sha256, "status": "OK", "audit_required": False,
            "rules_sha256": E.rules_sha256(), "engine_sha256": E.engine_provenance()[1],
            "results": [{"id": s.id, "passed": s.id in passing} for s in cat.specs], "ensembles": ensembles or [],
            **over}


def test_a_report_scored_under_other_rules_or_by_another_engine_or_needing_an_audit_is_refused() -> None:
    cat = V.load_catalogue(SAMPLE)
    with pytest.raises(ValueError, match="rules"):
        E.passer_ids(cat, _report(cat, {"S3K-0002"}, rules_sha256="0" * 64), "confirm")
    with pytest.raises(ValueError, match="engine"):
        E.passer_ids(cat, _report(cat, {"S3K-0002"}, engine_sha256="0" * 64), "confirm")
    with pytest.raises(ValueError, match="audit"):                                  # M1
        E.passer_ids(cat, _report(cat, {"S3K-0002"}, window="confirm", audit_required=True), "test_old")


def test_confirm_is_read_once_unless_the_plan_hash_is_identical(tmp_path: Path) -> None:
    cat = V.load_catalogue(SAMPLE)
    ledger = tmp_path / "reads.jsonl"
    p1 = E.plan(cat, _report(cat, {"S3K-0002", "S3K-0005"}), "confirm")
    p2 = E.plan(cat, _report(cat, {"S3K-0002"}), "confirm")
    h1, h2 = E.plan_hash(p1, "confirm", cat), E.plan_hash(p2, "confirm", cat)
    assert h1 != h2
    E.claim_read(ledger, "confirm", cat, h1)
    E.claim_read(ledger, "confirm", cat, h1)                                        # the same plan again: allowed
    with pytest.raises(ValueError, match="already read"):
        E.claim_read(ledger, "confirm", cat, h2)
    E.claim_read(ledger, "test_old", cat, h2)                                       # another window: its own read
    assert len(ledger.read_text().splitlines()) == 2


def test_the_report_carries_the_engine_sha_and_a_dirty_flag(tmp_path: Path) -> None:
    head, esha, dirty = E.engine_provenance()
    assert len(esha) == 64 and isinstance(dirty, bool)
    rep = E.run_screen(V.load_catalogue(SAMPLE), synthetic_market(days=40), workers=1, cache_dir=tmp_path,
                       ids=["S3K-0001"])
    assert (rep.engine_git_sha, rep.engine_sha256, rep.engine_dirty) == (head, esha, dirty)
    assert len(head) == 40 or head == ""


def test_the_cli_refuses_ids_and_limit_on_confirm_and_test_old(tmp_path: Path) -> None:
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("s3k_cli", ROOT / "scripts" / "sprint3000_screen.py")
    assert spec_ and spec_.loader
    cli = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(cli)
    cat = V.load_catalogue(SAMPLE)
    rep = tmp_path / "explore.json"
    rep.write_text(json.dumps(_report(cat, {"S3K-0002"})))
    base = ["--catalogue", str(SAMPLE), "--passers", str(rep), "--ledger", str(tmp_path / "l.jsonl"),
            "--raw", str(tmp_path)]
    for win in ("confirm", "test_old"):
        for extra in (["--ids", "S3K-0002"], ["--limit", "1"]):
            with pytest.raises(SystemExit, match="refused"):
                cli.main([*base, "--window", win, *extra])
    assert not (tmp_path / "l.jsonl").exists()                                      # refused before any read


def test_the_reader_pushes_the_window_end_down_as_a_parquet_filter(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _fake_raw(tmp_path, (2025,))
    seen: list[Any] = []
    real_read = pd.read_parquet

    def spy(path: Any, *a: Any, **k: Any) -> pd.DataFrame:
        seen.append(k.get("filters"))
        got = real_read(path, *a, **k)
        assert pd.DatetimeIndex(got["ts_utc"]).max() < M.HOLDOUT_FROM                # nothing past the cut decoded
        return got

    monkeypatch.setattr(M.pd, "read_parquet", spy)
    M.load_minutes(raw, pd.Timestamp("2025-01-01", tz="UTC"), M.HOLDOUT_FROM)
    assert seen and ("ts_utc", "<", M.HOLDOUT_FROM) in seen[0]


def test_near_reopen_marks_fills_within_5_minutes_of_a_reopen() -> None:
    t = np.r_[np.arange(0, 100), np.arange(200, 300)].astype(np.int64) * MIN          # a 100-minute gap at 100
    fills = np.array([0, 4, 5, 50, 200, 204, 205], dtype=np.int64) * MIN
    assert E.near_reopen(t, fills).tolist() == [True, True, False, False, True, True, False]


def test_learn_old_is_report_only_and_never_void() -> None:
    net, yrs = _by_year(40, 0.4, range(2003, 2006))
    res = E.summarize(spec(), _priced(net, yrs), "learn_old")
    assert all(res.gates.values()) and not res.passed and res.verdict == "REPORT-ONLY"
    two = [_res(i, null=True, passed=True) for i in range(10)]
    assert not E.null_check("learn_old", two, RULES)[1]


def test_null_controls_never_join_a_family_ensemble_and_a_null_ensemble_passing_confirm_requires_audit() -> None:
    res = [_res(1, "a", passed=True), _res(2, "a", gross_t=2.5), _res(3, "a", null=True, passed=True),
           _res(4, "a", null=True, gross_t=2.2), _res(5, "a", null=True, gross_t=1.0)]
    ens = E.build_ensembles(res, RULES)
    assert [(e.id, e.members, e.null_control) for e in ens] == [
        ("ENS-a", ("S3K-0001", "S3K-0002"), False), ("ENS-NULL-a", ("S3K-0003", "S3K-0004"), True)]
    ok = E.EnsembleResult(id="ENS-NULL-a", family="a", members=("S3K-0003", "S3K-0004"), window="confirm",
                          null_control=True, passed=True)
    assert E.null_check("confirm", [_res(1, passed=True)], RULES, [ok])[2]
    assert E.null_check("test_old", [_res(1, passed=True)], RULES, [ok])[2]
    cand = ok.model_copy(update={"id": "ENS-a", "null_control": False})
    assert not E.null_check("confirm", [_res(1, passed=True)], RULES, [cand])[2]
    cat = V.load_catalogue(SAMPLE)
    rep = _report(cat, {"S3K-0002"}, ensembles=[{"id": "ENS-a", "family": "a", "members": ["S3K-0001", "S3K-0005"]},
                                                {"id": "ENS-NULL-a", "family": "a", "members": ["S3K-0007", "S3K-0008"],
                                                 "null_control": True}])
    assert E.plan(cat, rep, "confirm").m == 2                                       # the null ensemble is not in m


def _write_year_files(raw: Path, m: pd.DataFrame) -> None:
    ts = pd.to_datetime(m.t, utc=True)
    for y in sorted(set(ts.dt.year)):
        part = m[ts.dt.year == y]
        df = pd.DataFrame({"ts_utc": pd.to_datetime(part.t, utc=True).dt.as_unit("ms")})
        for k, col in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c")):
            df[f"bid_{k}"] = df[f"ask_{k}"] = part[col].to_numpy()
        df.to_parquet(raw / f"xauusd_1m_dukascopy_{y}.parquet")


def test_explore_opens_2009_for_warmup_only_and_no_trade_decides_before_2010(tmp_path: Path) -> None:
    _write_year_files(tmp_path, synthetic_minutes(start="2009-10-05", days=150, seed=19))
    mkt = M.Market.load(tmp_path, M.WINDOWS["explore"])
    assert pd.Timestamp(int(mkt.tape.t[0]), tz="UTC").strftime("%Y-%m") == "2009-10"
    assert 2009 not in mkt.years_missing and 2010 not in mkt.years_missing
    sp = spec()
    sig = V.spec_signals(sp, mkt)
    assert (sig.dec < pd.Timestamp("2010-01-01", tz="UTC").value).sum() > 10        # 2009 bars do signal ...
    real = E.simulate(sp, mkt).real
    assert len(real) >= 10 and (real.t_dec >= pd.Timestamp("2010-01-01", tz="UTC").value).all()   # ... never trade
    b = mkt.bars["1h"]
    first = int(np.searchsorted(b.dec, pd.Timestamp("2010-01-01", tz="UTC").value))
    assert np.isfinite(V.atr(b, 14)[first])                                          # warmed on the first 2010 bar


def test_confirm_runs_explore_passers_only_in_rank_order() -> None:
    cat = V.load_catalogue(SAMPLE)
    rep = _report(cat, {"S3K-0007", "S3K-0002"})
    assert E.passer_ids(cat, rep, "confirm") == ["S3K-0002", "S3K-0007"]
    with pytest.raises(ValueError, match="S3K-0003"):
        E.passer_ids(cat, rep, "confirm", ["S3K-0002", "S3K-0003"])
    with pytest.raises(ValueError, match="catalogue"):
        E.passer_ids(cat, _report(cat, {"S3K-0002"}, sha="0" * 64), "confirm")
    with pytest.raises(ValueError, match="explore"):
        E.passer_ids(cat, _report(cat, {"S3K-0002"}, window="confirm"), "confirm")
    with pytest.raises(ValueError, match="VOID"):
        E.passer_ids(cat, {**rep, "status": "VOID"}, "confirm")


def test_confirm_sends_the_frozen_ensembles_and_m_counts_singles_and_families() -> None:
    cat = V.load_catalogue(SAMPLE)
    ens = [{"id": "ENS-trend_ma", "family": "trend_ma", "members": ["S3K-0002", "S3K-0017"]}]
    plan = E.plan(cat, _report(cat, {"S3K-0002", "S3K-0005"}, ensembles=ens), "confirm")
    assert plan.singles == ["S3K-0002", "S3K-0005"] and [e.id for e in plan.ensembles] == ["ENS-trend_ma"]
    assert plan.run_ids == ["S3K-0002", "S3K-0005", "S3K-0017"] and plan.m == 3


def test_test_old_refuses_any_id_that_did_not_pass_confirm() -> None:
    cat = V.load_catalogue(SAMPLE)
    with pytest.raises(ValueError, match="confirm"):
        E.passer_ids(cat, _report(cat, {"S3K-0002"}), "test_old")                   # an explore report
    rep = _report(cat, {"S3K-0002"}, window="confirm")
    assert E.passer_ids(cat, rep, "test_old") == ["S3K-0002"]
    with pytest.raises(ValueError, match="S3K-0007"):
        E.passer_ids(cat, rep, "test_old", ["S3K-0007"])
    assert E.passer_ids(cat, None, "explore") == [s.id for s in cat.specs]
    assert E.passer_ids(cat, None, "learn_old") == [s.id for s in cat.specs]
    with pytest.raises(ValueError, match="passers"):
        E.passer_ids(cat, None, "confirm")


# ---------------------------------------------------------------- end to end
def test_the_sample_catalogue_runs_end_to_end_identically_on_one_or_two_workers(tmp_path: Path) -> None:
    cat = V.load_catalogue(SAMPLE)
    mkt = synthetic_market(days=200, seed=31)
    one = E.run_screen(cat, mkt, workers=1, cache_dir=tmp_path)
    two = E.run_screen(cat, mkt, workers=2, cache_dir=tmp_path)
    assert [r.id for r in one.results] == [s.id for s in cat.specs]
    assert one.model_dump(exclude={"elapsed_s", "workers"}) == two.model_dump(exclude={"elapsed_s", "workers"})
    doc = json.loads(one.model_dump_json())
    for r in doc["results"]:
        for key in ("n", "n_signals", "gross_r", "gross_t", "net_r", "net_r_usd", "t", "t_day", "control_net_r",
                    "delta", "timing_delta", "drift_adj_gross_r", "legs", "year_net_r", "dsr", "dsr_keff", "verdict",
                    "gap_share", "reopen_fill_share", "member_only",
                    "passed", "gates"):
            assert key in r
    assert doc["catalogue_sha256"] == cat.sha256 and doc["k"] == RULES.dsr.k_program
    assert 1 <= doc["k_eff_run"] <= 20 and doc["k_eff"] == doc["k"] - 20 + doc["k_eff_run"]
    assert doc["rules_sha256"] == E.rules_sha256() and "ensembles" in doc
    assert sum(r["n"] for r in doc["results"]) > 200
    assert doc["data_last_utc"] < M.HOLDOUT_FROM.isoformat()


def test_a_confirm_run_applies_bh_fdr_over_singles_and_ensembles(tmp_path: Path) -> None:
    cat = V.load_catalogue(SAMPLE)
    mkt = synthetic_market(days=200, seed=31).model_copy(update={"window": M.WINDOWS["confirm"].model_copy(
        update={"dec_from": pd.Timestamp("2012-01-02", tz="UTC"), "data_from": pd.Timestamp("2012-01-02", tz="UTC")})})
    ens = [E.Ensemble(id="ENS-trend_ma", family="trend_ma", members=("S3K-0002", "S3K-0017"))]
    rep = E.run_screen(cat, mkt, workers=1, cache_dir=tmp_path, ids=["S3K-0001", "S3K-0008", "S3K-0002", "S3K-0017"],
                       singles=["S3K-0001", "S3K-0008"], ensembles=ens, m=5)
    assert rep.m == 5 and [e.id for e in rep.ensembles] == ["ENS-trend_ma"]
    for r in rep.results:
        if r.id in ("S3K-0001", "S3K-0008"):
            assert "bh_fdr" in r.gates and r.passed == all(r.gates.values())
        else:                                                                       # L3: marked, never gated
            assert r.verdict == "MEMBER-ONLY" and not r.passed and r.member_only and r.member_of == ("ENS-trend_ma",)
    assert rep.member_only_ids == ["S3K-0002", "S3K-0017"]
    assert "bh_fdr" in rep.ensembles[0].gates


@pytest.mark.skipif(not REAL.exists(), reason="the catalogue lane's file is not on this branch")
def test_the_real_catalogue_runs_on_a_synthetic_tape_and_keeps_level_specs_as_not_evaluable(tmp_path: Path) -> None:
    """Smoke only, on synthetic bars (never real ones before the orchestrator's run): every vocabulary path the real
    catalogue uses executes, and the level specs are reported, not dropped."""
    cat = V.load_catalogue(REAL)
    by_id = cat.by_id()
    seen: set[tuple[str, str, str]] = set()
    pick: list[str] = []
    for s in cat.specs:                                  # one spec per (tf, entry, first filter) and some level specs
        key = (s.tf, s.entry.type, s.filters[0].type if s.filters else "")
        if key not in seen or (s.pending and sum(1 for i in pick if by_id[i].pending) < 5):
            seen.add(key)
            pick.append(s.id)
    rep = E.run_screen(cat, synthetic_market(days=120, seed=5), workers=2, cache_dir=tmp_path, ids=pick)
    assert rep.n_specs == len(pick) and rep.n_not_evaluable >= 5
    assert {r.verdict for r in rep.results if r.status == "not_evaluable"} == {"NOT-EVALUABLE"}
    assert {t for r in rep.results for t in r.pending_types} <= V.PENDING_ENTRY | V.PENDING_FILTER
    assert rep.n_evaluated == len(pick) - rep.n_not_evaluable
