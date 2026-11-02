"""The daily position engine (amendment W3.2/W3.3/W3.6): parity with the label engine, planted edge, random walk,
financing by date, point-in-time signals, the no-trade band, the disaster stop and vol targeting."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.resample import resample_bars
from goldbot.labels.triple_barrier import BarrierSpec, triple_barrier_next_open_1m
from goldbot.research.position import data, engine, financing
from goldbot.research.position.costs import PointCosts, costs_for, point_bps_of_record
from goldbot.research.position.engine import EngineConfig
from goldbot.research.position.financing import Rates, venue_a, venue_b, venue_c
from goldbot.research.position.signals import LookaheadError, PositionSpec, assert_point_in_time
from goldbot.research.position.windows import Window
from tests.research.position.helpers import (
    NO_FINANCING,
    UNIT,
    ZERO_COSTS,
    daily_from_returns,
    flat_daily,
    lookahead,
    minutes_1m,
    momentum_sign,
    series_signal,
)

REPO = Path(__file__).resolve().parents[3]


def _window(bars: pd.DataFrame, lo: int = 0, hi: int | None = None) -> Window:
    idx = bars.index
    return Window(name="T", start=idx[lo].date(), end=idx[-1 if hi is None else hi].date(), use="test")


def _run(spec: PositionSpec, bars: pd.DataFrame, *, cfg: EngineConfig = UNIT, costs: PointCosts = ZERO_COSTS,
         venues=None, rates: Rates | None = None, window: Window | None = None) -> engine.PositionResult:
    return engine.run(spec, {"XAUUSD": bars}, window or _window(bars), cfg=cfg, costs={"XAUUSD": costs},
                      venues=venues or {"A": NO_FINANCING}, rates=rates or Rates(constant_pct=0.0))


# ---------------------------------------------------------------- parity with the label engine
@pytest.mark.parametrize("half_spread", [0.0, 0.05])
def test_a_one_day_zero_one_exposure_books_what_triple_barrier_next_open_1m_labels(half_spread: float) -> None:
    b1m = minutes_1m(40, half_spread=half_spread)
    store_daily = resample_bars(b1m, "1d", feature_day_tz=data.DAY_TZ, feature_day_anchor=data.DAY_CLOSE)
    daily = data.mid_daily(store_daily)
    assert len(daily) == len(store_daily) == 40
    trades = [(2, 3), (9, 0), (14, 6), (25, 4)]                    # (decision bar, max_bars): held max_bars + 1 days
    expo = pd.Series(0.0, index=daily.index)
    for idx, hold in trades:
        expo.iloc[idx:idx + hold + 1] = 1.0
    tb = pd.concat([triple_barrier_next_open_1m(
        store_daily, b1m, pd.DataFrame({"idx": [idx], "side": [1]}),
        BarrierSpec(target_atr=1e6, stop_atr=1e6, max_bars=hold), pd.Series(1.0, index=store_daily.index))
        for idx, hold in trades], ignore_index=True)
    assert len(tb) == len(trades) and (tb["barrier_hit"] == "time").all()
    rt_bps = 2 * half_spread / 1600.0 * 1e4                       # the spread as a point cost at the start price
    res = _run(PositionSpec(id="P", symbols=("XAUUSD",), signal=series_signal(expo)), daily,
               costs=PointCosts(rt_bps=rt_bps, overshoot_bps=0.0))
    book = res.venues["A"].book
    assert book.n_round_trips_signal == len(trades) and book.n_stops == 0
    assert book.days_in_market == sum(h + 1 for _, h in trades)
    if half_spread == 0.0:
        assert book.net_total == pytest.approx(tb["ret"].sum(), abs=1e-12)
    else:       # a dollar spread there, a bps point cost here (priced at 1,600 while the path runs 1,600-1,700)
        assert book.net_total == pytest.approx(tb["ret"].sum(), abs=len(trades) * 0.1e-4)   # 0.1 bp a trade
        assert book.net_total < book.gross_total


# ---------------------------------------------------------------- planted edge and random walk
def test_a_planted_momentum_edge_is_recovered_with_both_legs_positive() -> None:
    rng = np.random.default_rng(11)
    n, phi = 3000, 0.15
    r = np.zeros(n)
    for i in range(1, n):
        r[i] = phi * r[i - 1] + rng.normal(0, 0.01)
    bars = daily_from_returns(r)
    cfg = EngineConfig(null_draws=200, boot_resamples=1000)
    res = _run(PositionSpec(id="EDGE", symbols=("XAUUSD",), signal=momentum_sign(1)), bars, cfg=cfg,
               costs=PointCosts(rt_bps=0.415, overshoot_bps=0.275), window=_window(bars, lo=300))
    book = res.venues["A"].book
    assert book.t_boot > 3.0 and book.sharpe > 0.8
    assert book.null_kind == "sign" and book.null_excess is not None and book.null_p is not None
    assert book.null_excess > 0 and book.null_p < 0.01
    assert book.long_net > 0 and book.short_net > 0
    assert book.years_rule


def test_a_random_walk_loses_about_its_costs() -> None:
    rng = np.random.default_rng(5)
    bars = daily_from_returns(rng.normal(0, 0.01, 2500))
    cfg = EngineConfig(null_draws=50, boot_resamples=500)
    res = _run(PositionSpec(id="RW", symbols=("XAUUSD",), signal=momentum_sign(5)), bars, cfg=cfg,
               costs=PointCosts(rt_bps=4.0, overshoot_bps=0.275), window=_window(bars, lo=300))
    book, daily = res.venues["A"].book, res.venues["A"].daily
    assert book.net_total == pytest.approx(book.gross_total - book.cost_total + book.financing_total, abs=1e-12)
    assert book.cost_total > 0 and book.net_total < 0
    sd = float(daily["gross"].std()) * np.sqrt(len(daily))
    assert abs(book.net_total + book.cost_total) < 3 * sd             # what is left after costs is noise
    assert abs(book.gross_t) < 3


# ---------------------------------------------------------------- financing by date (W3.3)
def test_longs_pay_rate_plus_markup_and_shorts_receive_rate_minus_markup_per_rollover_night() -> None:
    bars = flat_daily(15, start="2012-01-02")                       # a Monday; price flat at 1000
    venue = venue_a(markup_long_pct=0.5, markup_short_pct=0.25)
    rates = Rates(constant_pct=2.0)
    for side, per_night in ((1.0, -(2.0 + 0.5) / 100 / 365), (-1.0, (2.0 - 0.25) / 100 / 365)):
        sig = pd.Series(side, index=bars.index)
        res = _run(PositionSpec(id="F", symbols=("XAUUSD",), signal=series_signal(sig)), bars, venues={"A": venue},
                   rates=rates)
        d = res.venues["A"].daily
        di = pd.DatetimeIndex(d.index)
        nights = financing.rollover_nights(di[:-1], di[1:], triple_weekday=2)
        held = res.venues["A"].assets_daily["XAUUSD"]["units_prev"].to_numpy()[1:] != 0
        assert d["financing"].to_numpy()[1:] == pytest.approx(per_night * nights * held)
        assert (np.sign(d["financing"].to_numpy()[1:][held]) == np.sign(per_night)).all()
        assert d["gross"].abs().max() == 0.0


def test_a_short_pays_when_the_rate_is_below_its_markup() -> None:
    bars = flat_daily(6, start="2012-01-02")
    res = _run(PositionSpec(id="F", symbols=("XAUUSD",), signal=series_signal(pd.Series(-1.0, index=bars.index))),
               bars, venues={"A": venue_a(markup_long_pct=0.74, markup_short_pct=0.74)}, rates=Rates(constant_pct=0.2))
    assert res.venues["A"].book.financing_total < 0


def test_wednesday_rollover_charges_three_nights_and_saturday_sunday_none() -> None:
    d = pd.DatetimeIndex(["2012-01-02", "2012-01-03", "2012-01-04", "2012-01-05", "2012-01-06", "2012-01-09"])
    assert list(financing.rollover_nights(d[:-1], d[1:], triple_weekday=2)) == [1, 1, 3, 1, 1]
    assert financing.rollover_nights(d[:1], d[-1:], triple_weekday=2).tolist() == [7]


def test_dtb3_is_read_from_the_data_lane_file_on_its_stored_available_utc_and_cut_before_the_holdout(
        tmp_path: Path) -> None:
    vd = pd.to_datetime(["2012-01-04", "2012-01-10", "2025-09-30", "2025-10-01"])
    av = pd.DatetimeIndex(["2012-01-05 23:00", "2012-01-11 23:00", "2025-09-30 23:00", "2025-10-01 23:00"], tz="UTC")
    pd.DataFrame({"series": "DTB3", "series_id": "DTB3", "value_date": vd, "value": [1.0, 5.0, 3.9, 9.9],
                  "vintage": vd, "available_utc": av, "ts_utc": av}).to_parquet(tmp_path / "fred_DTB3.parquet")
    frame = financing.load_fred(tmp_path / "fred_DTB3.parquet")
    assert frame["rate_pct"].tolist() == [1.0, 5.0, 3.9]                    # the 2025-10-01 row never loads
    rates = Rates(series=frame)
    at = pd.DatetimeIndex(["2012-01-05 21:00", "2012-01-06 21:00", "2012-01-11 21:00", "2012-01-12 21:00"], tz="UTC")
    got = rates.pct("XAUUSD", at)
    assert got.tolist() == [rates.constant_pct, 1.0, 1.0, 5.0]             # usable only after its stored 23:00 stamp
    with pytest.raises(ValueError, match="available_utc"):
        financing.fred_frame(pd.DataFrame({"value_date": vd, "value": 1.0}))


def test_venue_b_refuses_a_short_and_venue_c_charges_a_monthly_roll() -> None:
    bars = flat_daily(60, start="2012-01-02")
    short = PositionSpec(id="S", symbols=("XAUUSD",), signal=series_signal(pd.Series(-1.0, index=bars.index)))
    with pytest.raises(ValueError, match="long/flat"):
        _run(short, bars, venues={"B": venue_b()})
    long_ = PositionSpec(id="L", symbols=("XAUUSD",), signal=series_signal(pd.Series(1.0, index=bars.index)))
    res = _run(long_, bars, venues={"A": NO_FINANCING, "C": venue_c(fee_usd_per_side=2.0)})
    c, a = res.venues["C"].book, res.venues["A"].book
    month_ends = 2                                                  # Jan and Feb end inside; March is the force-close
    per_rt = (10.0) * 1e-4                                          # $2 a side at the 4,000 reference = 10 bps a round trip
    assert c.cost_total - a.cost_total == pytest.approx(per_rt * (1 + month_ends), rel=1e-6)
    assert not res.venues["C"].gates and res.venues["A"].gates


# ---------------------------------------------------------------- point in time
def test_signals_and_exposures_cut_at_t_are_unchanged_up_to_t() -> None:
    rng = np.random.default_rng(2)
    bars = daily_from_returns(rng.normal(0, 0.01, 900))
    spec = PositionSpec(id="M", symbols=("XAUUSD",), signal=momentum_sign(20))
    cfg = EngineConfig(warmup_days=60)
    full = engine.targets(spec, {"XAUUSD": bars}, cfg)["XAUUSD"]
    for cut in (bars.index[200], bars.index[555]):
        part = engine.targets(spec, {"XAUUSD": bars.loc[:cut]}, cfg)["XAUUSD"]
        pd.testing.assert_series_equal(part, full.loc[:cut])
    assert_point_in_time(spec, {"XAUUSD": bars}, [bars.index[300], bars.index[700]])


def test_a_signal_that_reads_tomorrow_is_refused() -> None:
    rng = np.random.default_rng(2)
    bars = daily_from_returns(rng.normal(0, 0.01, 400))
    spec = PositionSpec(id="LEAK", symbols=("XAUUSD",), signal=lookahead)
    with pytest.raises(LookaheadError):
        assert_point_in_time(spec, {"XAUUSD": bars}, [bars.index[100]])
    with pytest.raises(LookaheadError):
        _run(spec, bars, cfg=EngineConfig(pit_cuts=3, null_draws=10, boot_resamples=50))


# ---------------------------------------------------------------- sizing, band, stop
def test_vol_targeting_sizes_to_ten_percent_a_year_and_caps_at_two() -> None:
    cfg = EngineConfig(warmup_days=0)
    for daily_sd, want in ((0.01, 0.10 / (0.01 * np.sqrt(252))), (0.001, 2.0)):
        r = np.where(np.arange(400) % 2 == 0, daily_sd, -daily_sd)
        bars = daily_from_returns(r)
        spec = PositionSpec(id="V", symbols=("XAUUSD",), signal=series_signal(pd.Series(1.0, index=bars.index)))
        e = engine.targets(spec, {"XAUUSD": bars}, cfg)["XAUUSD"]
        assert e.iloc[-1] == pytest.approx(want, rel=0.03)


def test_the_no_trade_band_skips_a_rebalance_smaller_than_ten_percent_of_the_target() -> None:
    bars = flat_daily(8, start="2012-01-02")
    sig = pd.Series([1.0, 1.05, 1.05, 1.2, 1.2, 1.2, 0.0, 0.0], index=bars.index)
    path = engine.simulate(sig, bars, EngineConfig(target_vol=None, warmup_days=0), stop_atr=None, band=0.10,
                           window=_window(bars))
    expo = path.b * bars["open"].to_numpy()
    assert expo.tolist() == pytest.approx([0.0, 1.0, 1.0, 1.0, 1.2, 1.2, 1.2, 0.0])


def test_the_disaster_stop_flattens_until_the_next_decision_and_pays_the_overshoot() -> None:
    n = 40
    bars = flat_daily(n, start="2012-01-02")
    bars["high"], bars["low"] = 1005.0, 995.0                      # true range 10: ATR(20) 10, stop 30 below the fill
    bars.loc[bars.index[30], "low"] = 960.0                         # day 30 trades through 970
    sig = pd.Series(1.0, index=bars.index)
    path = engine.simulate(sig, bars, EngineConfig(target_vol=None, warmup_days=20), stop_atr=3.0, band=0.1,
                           window=_window(bars))
    assert path.stop_day[30] and path.x[30] == pytest.approx(970.0)
    assert path.held[30] == 0 and path.b[31] > 0                     # back in at the next open: the signal still says long
    res = _run(PositionSpec(id="S", symbols=("XAUUSD",), signal=series_signal(sig)), bars,
               cfg=EngineConfig(target_vol=None, warmup_days=20, null_draws=10, boot_resamples=50),
               costs=PointCosts(rt_bps=0.0, overshoot_bps=0.275))
    day = res.venues["A"].daily.loc[bars.index[30]]
    assert day["gross"] == pytest.approx(-30.0 / 1000.0)
    assert day["cost"] == pytest.approx(0.275e-4 * 970.0 / 1000.0)


# ---------------------------------------------------------------- costs and settings
def test_the_c1_point_costs_come_from_the_cost_of_record_in_bps_at_the_4000_reference() -> None:
    pc = point_bps_of_record(REPO / "config" / "cost_of_record.yaml")
    assert pc.rt_bps == pytest.approx(0.166 / 4000 * 1e4)
    assert pc.overshoot_bps == pytest.approx(0.11 / 4000 * 1e4)
    s = load_settings(REPO / "config" / "settings.yaml").research.position
    asia = costs_for("XAUUSD", s, REPO / "config" / "cost_of_record.yaml")     # the 21:00 UTC fill is in Asia
    assert asia.rt_bps == pytest.approx(0.204 / 4000 * 1e4) and asia.overshoot_bps == pytest.approx(0.10 / 4000 * 1e4)
    with pytest.raises(KeyError):
        costs_for("NOPE", s, REPO / "config" / "cost_of_record.yaml")          # never another market's cost


def test_the_settings_carry_the_w3_defaults() -> None:
    s = load_settings(REPO / "config" / "settings.yaml").research.position
    cfg = EngineConfig.from_settings(s)
    assert (cfg.target_vol, cfg.vol_span, cfg.max_leverage, cfg.band, cfg.stop_atr, cfg.atr_days) == \
        (0.10, 60, 2.0, 0.10, 3.0, 20)
    assert (cfg.null_draws, cfg.null_seed, cfg.boot_resamples, cfg.boot_block_days) == (1000, 20261102, 4000, 20.0)
    assert s.day_close_utc == data.DAY_CLOSE == "21:00"
    a = financing.venue_a_from_settings(s)
    assert a.markup("XAUUSD") == (0.74, 0.74) and a.markup("EURUSD") == (2.5, 2.5)


# ---------------------------------------------------------------- books and other venues
def test_a_book_of_two_identical_markets_is_scaled_to_its_portfolio_target_and_flat_below_min_live() -> None:
    rng = np.random.default_rng(8)
    bars = daily_from_returns(rng.normal(0, 0.01, 500))
    panel = {"XAUUSD": bars, "XAGUSD": bars.copy()}
    one = pd.Series(1.0, index=bars.index)
    spec = PositionSpec(id="B", symbols=("XAUUSD", "XAGUSD"), signal=series_signal(one), book_target_vol=0.10)
    cfg = EngineConfig(warmup_days=100)
    single = engine.targets(spec, panel, cfg)
    book = engine._book_level(spec, single, panel, cfg)
    # perfectly correlated: two 10% legs make a 20% book, so each leg is halved
    ratio = (book["XAUUSD"] / single["XAUUSD"]).iloc[200:]
    assert ratio.to_numpy() == pytest.approx(0.5, rel=0.02)
    late = {"XAUUSD": bars, "XAGUSD": bars.iloc[150:]}               # silver starts later: its warm-up ends later
    spec2 = PositionSpec(id="B2", symbols=("XAUUSD", "XAGUSD"), signal=series_signal(one), min_live=2)
    gated = engine._book_level(spec2, engine.targets(spec2, late, cfg), late, cfg)
    assert (gated["XAUUSD"].iloc[100:250] == 0).all() and (gated["XAUUSD"].iloc[251:] != 0).all()


def test_fx_financing_uses_the_rate_differential_quote_minus_base() -> None:
    at = pd.DatetimeIndex(["2012-01-03 23:00"], tz="UTC")
    q = pd.DataFrame({"available_utc": at, "rate_pct": [0.25]})
    b = pd.DataFrame({"available_utc": at, "rate_pct": [1.0]})
    diff = financing.fx_differential(q, b)                          # EURUSD: USD (quote) 0.25 minus EUR (base) 1.0
    assert diff["rate_pct"].tolist() == [-0.75]
    v = venue_a(markup_long_pct=0.5, markup_short_pct=0.5)
    pnl = v.financing_pnl("EURUSD", np.array([1.0, -1.0]), np.array([-0.75, -0.75]), np.array([365, 365]))
    assert pnl.tolist() == pytest.approx([-(-0.75 + 0.5) / 100, (-0.75 - 0.5) / 100])   # long earns, short pays


def test_venue_b_charges_only_the_etc_fee_on_longs() -> None:
    bars = flat_daily(12, start="2012-01-02")
    long_ = PositionSpec(id="L", symbols=("XAUUSD",), signal=series_signal(pd.Series(1.0, index=bars.index)),
                         long_only=True)
    res = _run(long_, bars, venues={"A": NO_FINANCING, "B": venue_b(fee_pct=0.12)}, rates=Rates(constant_pct=5.0))
    d = res.venues["B"].assets_daily["XAUUSD"]
    di = pd.DatetimeIndex(d.index)
    nights = financing.rollover_nights(di[:-1], di[1:], triple_weekday=2)
    held = d["units_prev"].to_numpy()[1:] != 0
    assert d["financing"].to_numpy()[1:] == pytest.approx(-0.12 / 100 / 365 * nights * held)
