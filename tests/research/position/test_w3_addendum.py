"""The W3 addendum rulings (docs/research/pos1/w3-addendum.md; quant review of PR #291, findings 1-13)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from goldbot.research.position import engine, financing, stats
from goldbot.research.position.costs import PointCosts
from goldbot.research.position.engine import EngineConfig
from goldbot.research.position.financing import Rates, venue_a
from goldbot.research.position.signals import LookaheadError, Panel, PositionSpec, assert_point_in_time
from goldbot.research.position.windows import Window, get_windows
from tests.research.position.helpers import (
    NO_FINANCING,
    ZERO_COSTS,
    daily_from_returns,
    flat_daily,
    momentum_sign,
    series_signal,
)

ROOT = Path(__file__).resolve().parents[3]
FAST = EngineConfig(null_draws=100, boot_resamples=200, warmup_days=60)
UTC_AT = pd.DatetimeIndex(["2012-01-02 23:00"], tz="UTC")


def _w(bars: pd.DataFrame, lo: int, hi: int) -> Window:
    return Window(name="T", start=bars.index[lo].date(), end=bars.index[hi].date(), use="test")


def _rw(n: int, start: str = "2004-01-05", seed: int = 3, drift: float = 0.0) -> pd.DataFrame:
    return daily_from_returns(np.random.default_rng(seed).normal(drift, 0.01, n), start=start)


def _run(spec: PositionSpec, panel: Panel, window, cfg: EngineConfig = FAST, venues=None,
         rates: Rates | None = None) -> engine.PositionResult:
    return engine.run(spec, panel, window, cfg=cfg, costs={s: PointCosts(rt_bps=0.5, overshoot_bps=0.25)
                                                          for s in spec.symbols},
                      venues=venues or {"A": NO_FINANCING}, rates=rates or Rates(constant_pct=0.0))


# ---------------------------------------------------------------- 1 FX and non-USD financing
def test_an_fx_pair_pays_quote_minus_base_an_index_its_home_rate_and_a_market_without_its_rate_is_refused() -> None:
    usd = pd.DataFrame({"available_utc": UTC_AT, "rate_pct": [1.0]})
    eur = pd.DataFrame({"available_utc": UTC_AT, "rate_pct": [3.0]})
    rates = financing.build_rates(["XAUUSD", "EURUSD", "GER40"], usd, {"EUR": eur})
    at = pd.DatetimeIndex(["2012-01-03 21:00"], tz="UTC")
    assert rates.pct("EURUSD", at).tolist() == [-2.0]               # USD 1% minus EUR 3%
    assert rates.pct("GER40", at).tolist() == [3.0] and rates.pct("XAUUSD", at).tolist() == [1.0]
    a = venue_a(markup_long_pct=0.5, markup_short_pct=0.5, triple_weekday={"EURUSD": 2})
    long_eur = a.financing_pnl("EURUSD", np.array([1.0]), np.array([-2.0]), np.array([365]))
    assert long_eur.tolist() == pytest.approx([(2.0 - 0.5) / 100])  # a long EURUSD earns the EUR carry less markup
    with pytest.raises(KeyError, match="JPY"):
        financing.build_rates(["USDJPY"], usd, {"EUR": eur})
    with pytest.raises(KeyError, match="USDJPY"):
        Rates(series=usd).pct("USDJPY", at)
    bars = flat_daily(30)
    spec = PositionSpec(id="FX", symbols=("USDJPY",), signal=series_signal(pd.Series(1.0, index=bars.index)))
    with pytest.raises(KeyError):
        _run(spec, {"USDJPY": bars}, _w(bars, 0, 29), venues={"A": venue_a(triple_weekday={"USDJPY": 2})},
             rates=Rates(series=usd))


# ---------------------------------------------------------------- 2 EXPLORE = LEARN-OLD + LEARN
def test_explore_reads_learn_old_and_learn_as_one_series_each_flat_at_its_own_edges() -> None:
    bars = _rw(3900, start="2002-06-03")                            # 2002-06 .. 2017-05
    spec = PositionSpec(id="E", symbols=("XAUUSD",), signal=momentum_sign(20))
    panel = {"XAUUSD": bars}
    res = _run(spec, panel, get_windows("EXPLORE"))
    old, learn = (_run(spec, panel, w) for w in get_windows("EXPLORE"))
    assert res.windows_read == ("LEARN-OLD", "LEARN")
    days = pd.DatetimeIndex(res.venues["A"].daily.index)
    assert not ((days >= pd.Timestamp("2006-01-01")) & (days < pd.Timestamp("2010-01-01"))).any()
    a, o, lr = res.venues["A"].book, old.venues["A"].book, learn.venues["A"].book
    assert a.net_total == pytest.approx(o.net_total + lr.net_total)
    assert a.n_round_trips_signal == o.n_round_trips_signal + lr.n_round_trips_signal
    assert a.days_in_market == o.days_in_market + lr.days_in_market
    assert a.null_mean_net == pytest.approx((o.null_mean_net or 0) + (lr.null_mean_net or 0))   # per-draw sums
    assert a.t_boot == pytest.approx(stats.block_t(res.venues["A"].daily["net"], resamples=FAST.boot_resamples,
                                                   block_days=FAST.boot_block_days, seed=FAST.null_seed))
    f = res.venues["A"].assets_daily["XAUUSD"]
    assert f.loc["2005-12-30", "held"] == 0 and f.loc[f.index >= "2010-01-01", "units"].iloc[0] == 0


# ---------------------------------------------------------------- 3 years rule on years in the market
def test_the_years_rule_counts_only_years_with_a_day_in_the_market() -> None:
    days = pd.bdate_range("2010-01-01", "2012-12-31")
    net = pd.Series(np.where(days.year == 2011, 0.0, 1.0), index=days)
    in_mkt = pd.Series(days.year != 2011, index=days)
    assert stats.years_positive(net, in_mkt) == (2, 2)              # the flat year is not "available"
    bars = _rw(900, start="2010-01-04")
    res = _run(PositionSpec(id="Y", symbols=("XAUUSD",), signal=momentum_sign(5)), {"XAUUSD": bars},
               _w(bars, 0, 899), cfg=FAST.model_copy(update={"warmup_days": 300}))
    b = res.venues["A"].book
    assert b.n_years == 3                                           # 2010 is all warm-up: 2011-2013 only


# ---------------------------------------------------------------- 5 read once, frozen pre-registration, BH at CONFIRM
def _screen() -> ModuleType:
    spec = importlib.util.spec_from_file_location("position_screen_w3", ROOT / "scripts" / "position_screen.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


TABLE = """| id | SR_prior (basis) | P_explore | P_chain | status |
|---|---|---|---|---|
| 001 | 0.40 (MOP 2012 / HOP 2017) | 0.23 | 0.022 | member |
| **026** | **1.036** (16 markets x 0.467, rho 0.15) | **0.90** | **0.627** | **confirm-eligible** |
"""


def test_a_read_once_window_needs_a_committed_prereg_in_a_clean_tree_and_refuses_a_second_read(tmp_path: Path) -> None:
    ps = _screen()
    _git(tmp_path, "init", "-q")
    (tmp_path / "prereg.md").write_text(TABLE)
    (tmp_path / ".gitignore").write_text("state/\n")
    with pytest.raises(SystemExit, match="not tracked"):
        ps.frozen_prereg(tmp_path, "prereg.md")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "prereg")
    blob = ps.frozen_prereg(tmp_path, "prereg.md")
    assert blob == _git(tmp_path, "rev-parse", "HEAD:prereg.md")
    (tmp_path / "prereg.md").write_text(TABLE + "edited after the freeze\n")
    with pytest.raises(SystemExit, match="not clean"):
        ps.frozen_prereg(tmp_path, "prereg.md")
    reg = tmp_path / "state" / "reads.jsonl"
    ps.claim_reads(reg, ["POS1-026", "POS1-001"], "CONFIRM", blob)
    with pytest.raises(SystemExit, match="read once"):
        ps.claim_reads(reg, ["POS1-001"], "CONFIRM", blob)
    ps.claim_reads(reg, ["POS1-001"], "TEST-OLD", blob)               # another window is its own read
    assert ps.eligible_ids(TABLE) == ["026"]
    assert ps._matches("POS1-026", ["026"]) and not ps._matches("POS1-0026x", ["026"])


def test_a_confirm_batch_runs_bh_only_over_the_committed_eligible_ids(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    ps = _screen()
    _git(tmp_path, "init", "-q")
    (tmp_path / "prereg.md").write_text(TABLE)
    (tmp_path / ".gitignore").write_text("state/\nraw/\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "prereg")
    rng = np.random.default_rng(1)
    dates = pd.bdate_range("2016-06-01", "2025-09-30")
    close = 1500 * np.cumprod(1 + rng.normal(0, 0.01, len(dates)))
    (tmp_path / "raw" / "daily").mkdir(parents=True)
    pd.DataFrame({"date": dates, "open": np.r_[1500, close[:-1]], "high": close * 1.004, "low": close * 0.996,
                  "close": close}).to_parquet(tmp_path / "raw" / "daily" / "XAUUSD.parquet")
    monkeypatch.setattr(ps.data, "load_xau_daily",
                        lambda raw: ps.data.load_daily_file(Path(raw) / "daily" / "XAUUSD.parquet"))
    mod = ModuleType("w3_specs")
    mod.SPECS = [PositionSpec(id="POS1-026", symbols=("XAUUSD",), signal=momentum_sign(20)),  # type: ignore[attr-defined]
                 PositionSpec(id="POS1-001", symbols=("XAUUSD",), signal=momentum_sign(60))]
    monkeypatch.setitem(sys.modules, "w3_specs", mod)
    argv = ["--raw-dir", str(tmp_path / "raw"), "--settings", str(ROOT / "config" / "settings.yaml"), "--spec",
            "w3_specs:SPECS", "--window", "CONFIRM", "--frozen-prereg", "prereg.md", "--allow-constant-rate",
            "--out", str(tmp_path / "state" / "confirm.json")]
    assert ps.main(argv, root=tmp_path) == 0
    got = json.loads((tmp_path / "state" / "confirm.json").read_text())
    rows = {r["id"]: r for r in got["specs"]}
    assert "bh_reject" in rows["POS1-026"] and "bh_reject" not in rows["POS1-001"]
    assert got["bh_eligible"] == ["026"] and got["prereg_blob"] == _git(tmp_path, "rev-parse", "HEAD:prereg.md")
    with pytest.raises(SystemExit, match="read once"):
        ps.main(argv, root=tmp_path)


# ---------------------------------------------------------------- 6 and 8 restore after a stop, one round trip
def test_a_monthly_signal_is_back_in_at_the_next_open_after_a_stop_and_the_round_trip_continues() -> None:
    bars = flat_daily(60, start="2012-01-02")
    bars["high"], bars["low"] = 1005.0, 995.0
    k = 30                                                           # Feb 10: not a month end
    bars.loc[bars.index[k], "low"] = 960.0
    months = pd.DatetimeIndex(bars.index).month
    month_end = pd.Series(np.r_[months[1:] != months[:-1], True], index=bars.index)
    sig = pd.Series(np.where(month_end, 1.0, np.nan), index=bars.index)
    cfg = EngineConfig(target_vol=None, warmup_days=20, null_draws=20, boot_resamples=50, pit_cuts=0)
    path = engine.simulate(sig, bars, cfg, stop_atr=3.0, band=0.1, window=_w(bars, 0, 59))
    assert path.stop_day[k] and not month_end.iloc[k] and not month_end.iloc[k - 1]
    assert path.b[k + 1] > 0                                         # restored at the last decision, not a month later
    res = _run(PositionSpec(id="M", symbols=("XAUUSD",), signal=series_signal(sig)), {"XAUUSD": bars},
               _w(bars, 0, 59), cfg=cfg)
    b = res.venues["A"].book
    assert b.n_stops == 1 and b.n_round_trips_signal == 1


# ---------------------------------------------------------------- 7 aux frames cut on available_utc
def test_an_aux_series_read_on_its_value_date_is_refused_as_lookahead_and_one_without_available_utc_is_refused() -> None:
    bars = _rw(400)
    value_dates = pd.DatetimeIndex(bars.index)
    aux = pd.DataFrame({"value": np.random.default_rng(4).normal(size=len(value_dates)),
                        "available_utc": (value_dates + pd.Timedelta(days=3, hours=23)).tz_localize("UTC")},
                       index=value_dates)

    def leaky(symbol: str, panel: Panel) -> pd.Series:               # joins on the value date: a 3-day leak
        return np.sign(panel["MACRO"]["value"].reindex(panel[symbol].index))

    def honest(symbol: str, panel: Panel) -> pd.Series:              # as of the decision close, on available_utc
        m = panel["MACRO"].set_index("available_utc")["value"].sort_index()
        at = pd.DatetimeIndex(panel[symbol]["close_utc"])
        return pd.Series(np.sign(m.reindex(at, method="ffill").to_numpy()), index=panel[symbol].index)
    panel = {"XAUUSD": bars, "MACRO": aux}
    with pytest.raises(LookaheadError):
        assert_point_in_time(PositionSpec(id="L", symbols=("XAUUSD",), signal=leaky), panel, [bars.index[200]])
    assert_point_in_time(PositionSpec(id="H", symbols=("XAUUSD",), signal=honest), panel, [bars.index[200]])
    with pytest.raises(ValueError, match="available_utc"):
        _run(PositionSpec(id="H", symbols=("XAUUSD",), signal=honest),
             {"XAUUSD": bars, "MACRO": aux.drop(columns="available_utc")}, _w(bars, 100, 399))


# ---------------------------------------------------------------- 9 exposure-matched null
def test_the_exposure_matched_null_of_an_always_long_spec_is_about_buy_and_hold() -> None:
    bars = _rw(1200, drift=0.0006, seed=12)
    spec = PositionSpec(id="AL", symbols=("XAUUSD",), signal=series_signal(pd.Series(1.0, index=bars.index)),
                        long_only=True)
    b = _run(spec, {"XAUUSD": bars}, _w(bars, 100, 1199), cfg=FAST.model_copy(update={"stop_atr": None})).venues["A"].book
    assert b.null_kind == "exposure" and b.null_mean_net is not None and b.buy_hold_net is not None
    assert b.null_mean_net == pytest.approx(b.buy_hold_net, abs=0.05 * abs(b.buy_hold_net) + 0.005)


# ---------------------------------------------------------------- 11 re-clip after the book scale
def test_book_scaled_exposures_are_re_clipped_to_the_leverage_cap() -> None:
    bars = _rw(500, seed=8)
    panel = {"XAUUSD": bars, "XAGUSD": bars.copy()}
    spec = PositionSpec(id="C", symbols=("XAUUSD", "XAGUSD"), sizing="unit", book_target_vol=1.0,
                        signal=series_signal(pd.Series(1.5, index=bars.index)))
    cfg = EngineConfig(warmup_days=100)
    book = engine._book_level(spec, engine.targets(spec, panel, cfg), panel, cfg)
    late = book["XAUUSD"].iloc[150:]
    assert late.max() == pytest.approx(2.0) and (late.abs() <= 2.0 + 1e-12).all()


# ---------------------------------------------------------------- 12 triple day per symbol
def test_a_symbol_without_a_triple_rollover_day_is_refused() -> None:
    v = venue_a(triple_weekday={"XAUUSD": 2})
    assert v.triple("XAUUSD") == 2
    with pytest.raises(KeyError, match="XAGUSD"):
        v.triple("XAGUSD")
    bars = flat_daily(30)
    spec = PositionSpec(id="T", symbols=("XAGUSD",), signal=series_signal(pd.Series(1.0, index=bars.index)))
    with pytest.raises(KeyError, match="triple"):
        _run(spec, {"XAGUSD": bars}, _w(bars, 0, 29), venues={"A": v})


# ---------------------------------------------------------------- 13 truncation, negation, covariance
def test_the_whole_engine_on_a_panel_cut_at_the_window_end_gives_the_same_books_and_earlier_days_never_move() -> None:
    bars = _rw(900, seed=21)
    spec = PositionSpec(id="W", symbols=("XAUUSD",), signal=momentum_sign(10))
    w = _w(bars, 300, 700)
    full = _run(spec, {"XAUUSD": bars}, w).venues["A"]
    cut = _run(spec, {"XAUUSD": bars.iloc[:701]}, w).venues["A"]
    pd.testing.assert_frame_equal(full.daily, cut.daily)
    assert full.book == cut.book
    longer = _run(spec, {"XAUUSD": bars}, _w(bars, 300, 850)).venues["A"]
    before = full.daily.index < bars.index[700]                       # the short window's force-close day differs
    cols = ["gross", "cost", "financing", "net"]
    pd.testing.assert_frame_equal(full.daily.loc[before, cols], longer.daily.loc[full.daily.index[before], cols])


def test_an_episode_sign_flip_is_an_exact_negation_of_its_gross() -> None:
    bars = _rw(400, seed=5)
    sig = pd.Series(1.0, index=bars.index)
    cfg = EngineConfig(target_vol=None, warmup_days=0, stop_atr=None)
    p = engine.simulate(sig, bars, cfg, stop_atr=None, band=0.1, window=_w(bars, 50, 399), symbol="XAUUSD")
    actual = engine.book_path(p, ZERO_COSTS, NO_FINANCING, Rates(constant_pct=0.0))["gross"].sum()
    g, c, f = engine.null_parts(p, PointCosts(rt_bps=0.5, overshoot_bps=0.25), NO_FINANCING, Rates(constant_pct=0.0),
                                kind="sign", draws=400, seed=1)
    assert set(np.round(np.abs(g) / abs(actual), 12)) == {1.0}         # one episode: +G or -G, nothing else
    assert (g > 0).any() and (g < 0).any() and np.ptp(c) == 0           # costs do not depend on the sign


def test_the_book_covariance_scale_on_a_panel_cut_at_t_is_unchanged_up_to_t() -> None:
    a, b = _rw(600, seed=1), _rw(600, seed=2)
    spec = PositionSpec(id="BC", symbols=("XAUUSD", "XAGUSD"), book_target_vol=0.10, min_live=2,
                        signal=momentum_sign(20))
    cfg = EngineConfig(warmup_days=100)
    full = engine._book_level(spec, engine.targets(spec, {"XAUUSD": a, "XAGUSD": b}, cfg),
                              {"XAUUSD": a, "XAGUSD": b}, cfg)
    t = a.index[400]
    pa, pb = a.loc[:t], b.loc[:t]
    part = engine._book_level(spec, engine.targets(spec, {"XAUUSD": pa, "XAGUSD": pb}, cfg),
                              {"XAUUSD": pa, "XAGUSD": pb}, cfg)
    for s in ("XAUUSD", "XAGUSD"):
        pd.testing.assert_series_equal(part[s], full[s].loc[:t])


def test_the_engine_reports_y_learn_from_live_years_in_learn_old_and_learn() -> None:
    bars = _rw(2400, start="2009-01-05")                              # 2009-01 .. 2018-03; 2009 is warm-up
    res = _run(PositionSpec(id="YL", symbols=("XAUUSD",), signal=momentum_sign(5)), {"XAUUSD": bars},
               get_windows("EXPLORE"), cfg=FAST.model_copy(update={"warmup_days": 260}))
    assert res.windows_read == ("LEARN",) and 7.5 <= res.y_learn <= 8.0
    assert stats.p_explore(0.4, res.y_learn) < 0.5
