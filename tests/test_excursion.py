"""Maximum adverse / favourable excursion (BACKLOG 23, playbook G-9): side-correct MAE and MFE in price, R and time on
every closed shadow trade (from bars, as the labels) and every closed live/demo trade (from ticks while the engine
holds the position, approximated from stored 1m bars across a restart and flagged), old records loading unchanged,
and tracking that can never block an exit."""
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import AttributionSettings
from goldbot.engine import runner
from goldbot.engine.runner import OpenTrade
from goldbot.engine.shadow import ShadowBook, ShadowTrade, VersionBook
from goldbot.execution.broker import Tick
from goldbot.labels.excursion import label_excursions
from goldbot.labels.triple_barrier import BarrierSpec, one_at_a_time, triple_barrier
from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
from goldbot.ops.monthly_report import build_monthly_report, format_report
from goldbot.research.attribution import CostModel, build_report
from tests.test_closed_trades import TREND, _engine, _enter, _records, _settle
from tests.test_shadow import _bars

T0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
STOP = TREND.label_spec.stop_atr                     # 1.25: the trend agent's stop distance at ATR 1.0


def _at(minutes: float) -> pd.Timestamp:
    return T0 + pd.Timedelta(minutes=minutes)


def _q(minutes: float, bid: float, ask: float) -> Tick:
    return Tick(ts_utc=_at(minutes), bid=bid, ask=ask)


# ---------------------------------------------------------------------------------------------- live / demo (ticks)
@pytest.mark.parametrize("side", [1, -1])
def test_a_closed_trade_records_side_correct_mae_and_mfe_from_the_ticks_it_was_open_for(tmp_path, side):
    """A long's MAE is the lowest bid and its MFE the highest bid; a short's MAE the highest ask and its MFE the
    lowest ask (the price each would exit at), with R against the initial stop and the tick time of each."""
    eng, pb = _engine(tmp_path, TREND)
    _enter(eng, pb, TREND, side=side)
    eng.on_tick(_q(1, 1999.2, 1999.6))               # down 0.8 on the bid, 0.4 on the ask
    eng.on_tick(_q(2, 2000.8, 2001.2))               # up 0.8 on the bid, 1.2 on the ask
    eng.on_tick(_q(3, 2000.1, 2000.5))
    eng._kill_switch(everything=True)
    eng._save_orders()
    (t,) = _records(tmp_path)
    if side > 0:
        assert (t.mae_price, t.mfe_price) == (1999.2, 2000.8)
        assert (t.mae_utc, t.mfe_utc) == (_at(1), _at(2))
        assert t.mae_r == pytest.approx(-0.8 / STOP) and t.mfe_r == pytest.approx(0.8 / STOP)
    else:
        assert (t.mae_price, t.mfe_price) == (2001.2, 1999.6)
        assert (t.mae_utc, t.mfe_utc) == (_at(2), _at(1))
        assert t.mae_r == pytest.approx(-1.2 / STOP) and t.mfe_r == pytest.approx(0.4 / STOP)
    assert t.excursion_approx is False


def test_the_exit_fill_counts_but_prices_after_a_server_side_exit_never_do(tmp_path):
    """The server stop fills a gap at the market (the real MAE); ticks the engine sees before it notices the close are
    clamped at its stop, so a further fall after the exit is not the trade's."""
    eng, pb = _engine(tmp_path, TREND)
    _enter(eng, pb, TREND)
    eng.on_tick(_q(1, 1998.5, 1998.5))               # through the 1998.75 stop: paper fills the gap at 1998.5
    eng.on_tick(_q(2, 1990.0, 1990.0))               # after the exit, before the engine's sweep
    _settle(eng)
    (t,) = _records(tmp_path)
    assert t.exit_reason == "stop" and t.mae_price == pytest.approx(1998.5)
    assert t.mae_r == pytest.approx(-1.5 / STOP)


def test_the_track_survives_a_restart_and_the_gap_is_filled_from_stored_bars_and_flagged_approximate(tmp_path):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    eng.on_tick(_q(1, 2000.9, 2000.9))               # tracked before the restart: the MFE
    eng._save_orders()
    pb.on_tick(_q(3, 1999.4, 1999.4))                # engine down: only the stored 1m bars saw the low
    again, _ = _engine(tmp_path, TREND, pb=pb)
    assert again.open[pid].excursion is not None and again.open[pid].excursion_approx
    again.bars_1m = pd.DataFrame({"ts_utc": [_at(3)], "bid_high": [1999.6], "bid_low": [1999.1],
                                  "ask_high": [1999.7], "ask_low": [1999.2]})
    pb.on_tick(_q(10, 2000.2, 2000.2))
    again._kill_switch(everything=True)
    again._save_orders()
    (t,) = _records(tmp_path)
    assert t.mfe_price == pytest.approx(2000.9) and t.mfe_utc == _at(1)
    assert t.mae_price == pytest.approx(1999.1) and t.mae_utc == _at(3)
    assert t.excursion_approx is True


def test_bars_that_straddle_the_entry_or_the_exit_are_left_out_of_the_approximation(tmp_path):
    eng, pb = _engine(tmp_path, TREND)
    _enter(eng, pb, TREND, at=T0 + pd.Timedelta(seconds=30))
    again, _ = _engine(tmp_path, TREND, pb=pb)
    again.bars_1m = pd.DataFrame({"ts_utc": [_at(0), _at(4), _at(9)], "bid_high": [2005.0, 2000.4, 2005.0],
                                  "bid_low": [1995.0, 1999.9, 1995.0], "ask_high": [2005.0, 2000.4, 2005.0],
                                  "ask_low": [1995.0, 1999.9, 1995.0]})
    pb.on_tick(_q(9.5, 2000.0, 2000.0))
    again._kill_switch(everything=True)
    again._save_orders()
    (t,) = _records(tmp_path)
    assert t.mae_price == pytest.approx(1999.9) and t.mfe_price == pytest.approx(2000.4) and t.excursion_approx


def test_stored_bars_without_the_exit_side_columns_keep_the_tick_track(tmp_path):
    eng, pb = _engine(tmp_path, TREND)
    _enter(eng, pb, TREND, side=-1)
    eng.on_tick(_q(1, 1999.0, 1999.4))
    eng._save_orders()
    again, _ = _engine(tmp_path, TREND, pb=pb)
    again.bars_1m = pd.DataFrame({"ts_utc": [_at(3)], "bid_high": [2009.0], "bid_low": [1990.0]})   # no ask side
    pb.on_tick(_q(5, 1999.8, 2000.2))
    again._kill_switch(everything=True)
    again._save_orders()
    (t,) = _records(tmp_path)
    assert t.mfe_price == pytest.approx(1999.4) and t.mae_price == pytest.approx(2000.2) and t.excursion_approx


def test_a_tracking_exception_never_blocks_an_exit_or_the_record(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)

    def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("excursion bug")
    monkeypatch.setattr(runner.Excursion, "fold", broken)
    eng.on_tick(_q(1, 2000.2, 2000.2))               # tracking raises: swallowed, no tick error
    assert not eng.state.tick_error
    eng.open[pid].sl = 2000.5                        # tighter than the broker's: the engine closes on the next tick
    eng.on_tick(_q(2, 2000.4, 2000.4))
    assert pid not in eng.open and not pb.positions()
    (t,) = _records(tmp_path)                        # recorded, without an excursion
    assert t.exit_reason == "engine_stop_close" and t.mae_price is None and t.mfe_r is None


# ---------------------------------------------------------------------------------------------- shadow (bars)
def _bar(ts: pd.Timestamp, bid_low: float, bid_high: float, spread: float = 0.2) -> dict:
    return {"ts_utc": ts, "bid_low": bid_low, "bid_high": bid_high, "bid_close": (bid_low + bid_high) / 2,
            "ask_low": bid_low + spread, "ask_high": bid_high + spread, "ask_close": (bid_low + bid_high) / 2 + spread}


@pytest.mark.parametrize("side", [1, -1])
def test_a_shadow_trade_takes_mae_and_mfe_from_the_exit_side_of_each_bar(tmp_path, side):
    """A long reads bid low (MAE) and bid high (MFE), a short ask high (MAE) and ask low (MFE); the time exit's bar
    counts whole, the signal bar not at all."""
    b0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
    step = pd.Timedelta(minutes=15)
    book = ShadowBook(tmp_path)
    book.track("v1", b0)
    t = book.open_trade(version="v1", agent_id="a", side=side, bar_ts=b0, entry=2000.0, atr_usd=10.0, target_atr=1.0,
                        stop_atr=1.0, max_bars=1, p=0.6)
    assert t is not None
    for i, (lo, hi) in enumerate([(1980.0, 2020.0), (1998.0, 2001.0), (1997.0, 2003.0)]):
        book.on_bar(pd.Series(_bar(b0 + i * step, lo, hi)))      # the first is the signal bar itself
    assert t.barrier == "time"
    if side > 0:
        assert (t.mae_price, t.mfe_price, t.mae_utc, t.mfe_utc) == (1997.0, 2003.0, b0 + 2 * step, b0 + 2 * step)
        assert t.mae_r == pytest.approx(-0.3) and t.mfe_r == pytest.approx(0.3)
    else:
        assert t.mae_price == pytest.approx(2003.2) and t.mfe_price == pytest.approx(1997.2)
        assert t.mae_r == pytest.approx(-0.32) and t.mfe_r == pytest.approx(0.28)


def test_a_stop_bar_counts_the_adverse_side_to_the_stop_and_none_of_its_favourable_side(tmp_path):
    b0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
    book = ShadowBook(tmp_path)
    book.track("v1", b0)
    t = book.open_trade(version="v1", agent_id="a", side=1, bar_ts=b0, entry=2000.0, atr_usd=10.0, target_atr=1.0,
                        stop_atr=1.0, max_bars=5, p=0.6)
    assert t is not None
    book.on_bar(pd.Series(_bar(b0 + pd.Timedelta(minutes=15), 1999.0, 2004.0)))
    book.on_bar(pd.Series(_bar(b0 + pd.Timedelta(minutes=30), 1980.0, 2020.0)))   # both touched: stop first
    assert t.barrier == "stop" and t.mae_price == 1990.0 and t.mae_r == pytest.approx(-1.0)
    assert t.mfe_price == 2004.0 and t.mfe_r == pytest.approx(0.4)


@pytest.mark.parametrize("seed,vol", [(0, 1.0), (1, 1.0), (2, 0.15)])
def test_shadow_excursions_equal_the_labels_on_the_same_path(tmp_path, seed, vol):
    bars = _bars(seed=seed, vol=vol)
    atr = pd.Series(np.full(len(bars), 2.0))
    spec = BarrierSpec(target_atr=1.5, stop_atr=1.0, max_bars=16)
    signals = pd.DataFrame({"idx": np.arange(20, 360, 7), "side": np.where(np.arange(20, 360, 7) % 2 == 0, 1, -1)})
    labels = one_at_a_time(triple_barrier(bars, signals, spec, atr))
    want = label_excursions(bars, labels, spec.stop_atr, atr).set_index(labels["idx"])
    book = ShadowBook(tmp_path)
    book.track("v1", bars["ts_utc"].iloc[0])
    sig = dict(zip(signals["idx"], signals["side"]))
    opened = {}
    for i in range(len(bars)):
        bar = bars.iloc[i]
        book.on_bar(bar)
        if i in sig:
            opened[i] = book.open_trade(version="v1", agent_id="a", side=int(sig[i]), bar_ts=bar["ts_utc"],
                                        entry=float(bar["ask_close"] if sig[i] > 0 else bar["bid_close"]),
                                        atr_usd=2.0, target_atr=1.5, stop_atr=1.0, max_bars=16, p=0.6)
    taken = {i: t for i, t in opened.items() if t is not None}
    assert set(taken) == set(want.index) and len(taken) > 5
    for idx, t in taken.items():
        w = want.loc[idx]
        assert t.mae_price == pytest.approx(w["mae_price"]) and t.mfe_price == pytest.approx(w["mfe_price"]), idx
        assert t.mae_r == pytest.approx(w["mae_r"]) and t.mfe_r == pytest.approx(w["mfe_r"]), idx
        assert t.mae_utc == w["mae_utc"] and t.mfe_utc == w["mfe_utc"], idx
        assert t.mae_r is not None and -1.0 - 1e-9 <= t.mae_r <= 0          # a stop is filled at the stop in labels and shadow


def test_a_shadow_trade_opened_before_the_upgrade_leaves_its_excursion_empty(tmp_path):
    """Its earlier bars were never seen: an excursion from the rest would understate it, so none is recorded."""
    b0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
    old = {"v1": {"version": "v1", "started_utc": b0.isoformat(), "closed": [], "open": [{
        "version": "v1", "agent_id": "a", "side": 1, "entry_ts": b0.isoformat(), "entry": 2000.0, "stop": 1990.0,
        "target": 2010.0, "max_bars": 2, "timeframe": "15m", "p": 0.6, "bars_held": 1}]}}
    (tmp_path / "shadow_book.json").write_text(json.dumps(old))
    book = ShadowBook(tmp_path)
    (closed,) = book.on_bar(pd.Series(_bar(b0 + pd.Timedelta(minutes=30), 1995.0, 2012.0)))
    assert closed.barrier == "target" and closed.mae_price is None and closed.mfe_r is None


# ---------------------------------------------------------------------------------------------- old records
def test_records_written_before_the_change_load_with_empty_excursions(tmp_path):
    old_closed = {"account_id": "icm-demo", "broker": "icm", "mode": "demo", "exit_utc": "2026-10-01T10:00:00+00:00",
                  "ret": 0.001, "pnl": 5.0, "equity_before": 1000.0, "r": 0.5}
    t = ClosedTrade.model_validate(old_closed)
    assert (t.mae_price, t.mae_r, t.mae_utc, t.mfe_price, t.mfe_r, t.mfe_utc, t.excursion_approx) == (None,) * 7
    old_shadow = {"version": "v1", "agent_id": "a", "side": 1, "entry_ts": "2026-10-01T10:00:00+00:00",
                  "entry": 2000.0, "stop": 1990.0, "target": 2010.0, "max_bars": 4, "p": 0.6}
    s = ShadowTrade.model_validate(old_shadow)
    assert s.mae_price is None and s.mfe_r is None
    VersionBook.model_validate({"version": "v1", "started_utc": "2026-10-01T10:00:00+00:00", "open": [old_shadow]})
    o = OpenTrade.model_validate({"position_id": 1, "agent_id": "a", "side": 1, "lots": 0.1,
                                  "entry_bar_ts": "2026-10-01T10:00:00+00:00", "max_bars": 4})
    assert o.excursion is None and o.excursion_approx is False


# ---------------------------------------------------------------------------------------------- reporting
def _closed(pid: int, mae_r: float | None, mfe_r: float | None, approx: bool | None = False) -> ClosedTrade:
    return ClosedTrade(account_id="icm-demo", broker="icm", mode="demo",
                       exit_utc=pd.Timestamp("2026-10-05 15:00", tz="UTC"), ret=0.0, pnl=1.0, equity_before=1000.0,
                       r=0.1, position_id=pid, mae_r=mae_r, mfe_r=mfe_r,
                       excursion_approx=approx if mae_r is not None else None)


def test_the_monthly_report_shows_the_median_mae_and_mfe_in_r(tmp_path: Path):
    for i, (mae, mfe, ap) in enumerate([(-0.2, 1.5, False), (-0.6, 0.4, True), (-1.0, 0.1, False), (None, None, None)]):
        append_closed_trade(tmp_path, _closed(i, mae, mfe, ap))
    rep = build_monthly_report(tmp_path, month="2026-10", now=pd.Timestamp("2026-11-01 06:00", tz="UTC"))
    (a,) = rep.accounts
    assert a.mae_r_median == pytest.approx(-0.6) and a.mfe_r_median == pytest.approx(0.4)
    assert a.excursion_trades == 3 and a.excursion_approx == 1
    assert "median MAE -0.60R · median MFE +0.40R over 3 trades (1 approximate" in format_report(rep)


def test_attribution_shows_median_mae_and_mfe_by_exit_type():
    entry, risk = 2400.0, 10.0
    trades = []
    for i in range(6):
        stop = i % 2 == 0
        ets = pd.Timestamp("2026-09-01 07:00", tz="UTC") + pd.Timedelta(days=i)
        exit_px = entry - risk if stop else entry + 2 * risk
        trades.append(ShadowTrade(version="tsmom-g0-abcdef0123-v1", agent_id="tsmom-g0-abcdef0123", side=1,
                                  entry_ts=ets, entry=entry, stop=entry - risk, target=entry + 2 * risk, max_bars=10,
                                  timeframe="1h", p=0.6, threshold=0.55, bars_held=1,
                                  exit_ts=ets + pd.Timedelta(hours=1), exit=exit_px,
                                  barrier="stop" if stop else "target", ret=(exit_px - entry) / entry,
                                  mae_r=-1.0 if stop else -0.1 * i, mfe_r=0.2 * i if stop else 2.0))
    free = CostModel(spread_usd={}, slippage_usd={}, slippage_prior_usd=0.0, commission_usd_per_oz=0.0, swap=None,
                     source="test")
    rep = build_report(trades, now=pd.Timestamp("2026-10-09 23:50", tz="UTC"), costs=free,
                       settings=AttributionSettings(window_days=180, min_trades=30, calibration_bins=10,
                                                    calibration_min_bin=10))
    assert rep.exits["stop"].mae_r_median == pytest.approx(-1.0) and rep.exits["stop"].mfe_r_median == pytest.approx(0.4)
    assert rep.exits["target"].mae_r_median == pytest.approx(-0.3) and rep.exits["target"].mfe_r_median == 2.0
