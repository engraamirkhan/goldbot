"""Known-truth checks of the research backtest engines (rethink lane `sanity`, 2026-11-02): `trend_screen.walk` (the
sprint-200 engine) and `triple_barrier_next_open_1m` (the arms harness behind scripts/run_arm_backtest.py).

* buy and hold books exactly the price change; a planted edge is recovered net of exactly the stated costs and a
  martingale books about zero gross;
* a minute that touches both barriers exits at the stop; a minute that OPENS through the stop fills at that open in both
  engines (the harness used to fill at the stop: optimistic on every weekend / daily-break gap);
* bid/ask: the harness buys the ask open and stops on the bid low (shorts: the bid open, the ask high);
* swap is a financing rate: research charges today's rate on each trade's own notional (`SwapSpec.usd_per_oz_at`), a
  long pays, the shipped short pays nothing, and a spec without a reference price is unchanged (live paths);
* pandas 3 keeps the ms unit of the Dukascopy files: the loader still yields epoch nanoseconds.
Every input is synthetic and built per test (tmp_path for files): no network, no clock, no order dependence."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import CostSettings
from goldbot.data.timeutil import epoch_ns
from goldbot.labels import SwapSpec, rollover_nights
from goldbot.labels.triple_barrier import BarrierSpec, triple_barrier_next_open_1m
from goldbot.research import arms as A

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("trend_screen_sanity", ROOT / "scripts" / "sprint200" / "trend_screen.py")
assert _spec and _spec.loader
T = importlib.util.module_from_spec(_spec)
sys.modules["trend_screen_sanity"] = T              # dataclasses resolve their module by name
_spec.loader.exec_module(T)
COSTS = A.load_costs(ROOT / "config" / "arm_costs.yaml")
MIN = 60_000_000_000


def _ctx(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, t0: str = "2012-03-06 08:00") -> Any:
    """A minute context (mid prices) without the session / day tables: what `walk` and `price` read."""
    t = pd.Timestamp(t0, tz="UTC").value + np.arange(len(o), dtype=np.int64) * MIN
    z = np.zeros(len(o), dtype=np.int64)
    return T.Ctx(t, np.asarray(o, float), np.asarray(h, float), np.asarray(lo, float), np.asarray(c, float), z, z, z, z,
                 {}, pd.DataFrame(), int(t[0]), int(t[-1]) + MIN)


def _flat(n: int, px: float = 1300.0) -> tuple[np.ndarray, ...]:
    a = np.full(n, px)
    return a.copy(), a + 0.05, a - 0.05, a.copy()


# ---------------------------------------------------------------- (a) buy and hold, (b) planted edge, (c) martingale
def test_buy_and_hold_books_exactly_the_price_change() -> None:
    rng = np.random.default_rng(11)
    c = 1300 + np.cumsum(rng.normal(0, 0.4, 5_000))
    o = np.r_[1300.0, c[:-1]]
    ctx = _ctx(o, np.maximum(o, c) + 0.1, np.minimum(o, c) - 0.1, c)
    g_long, kind, _, _ = T.walk(ctx, 0, 1, 1e9, np.nan, np.nan, ctx.end, None)
    g_short, _, _, _ = T.walk(ctx, 0, -1, 1e9, np.nan, np.nan, ctx.end, None)
    assert kind == "time"
    assert g_long == pytest.approx(c[-1] - o[0], abs=1e-9)
    assert g_short == pytest.approx(-(c[-1] - o[0]), abs=1e-9)


def _planted(plant_usd: float, days: int, seed: int) -> tuple[Any, np.ndarray]:
    """Weekday minutes, a martingale plus a deterministic +`plant_usd` over each 09:00 and 13:00 UTC hour (offset over
    the rest of the day so the day nets zero). Returns the context and the planted rule's decision times."""
    ts = pd.date_range("2012-03-05", periods=days * 1440, freq="1min", tz="UTC")
    ts = ts[ts.dayofweek < 5]
    hot = np.isin(ts.hour, [9, 13])
    drift = np.where(hot, plant_usd / 60, -plant_usd * 2 / (1440 - 120))
    rng = np.random.default_rng(seed)
    c = 1300 + np.cumsum(rng.normal(0, 0.3, len(ts)) + drift)
    o = np.r_[1300.0, c[:-1]]
    t = np.asarray(epoch_ns(ts), dtype=np.int64)
    z = np.zeros(len(ts), dtype=np.int64)
    ctx = T.Ctx(t, o, np.maximum(o, c) + 0.05, np.minimum(o, c) - 0.05, c, z, z, z, z, {}, pd.DataFrame(), int(t[0]),
                int(t[-1]) + MIN)
    dec = t[hot & (ts.minute == 0)]
    return ctx, dec


@pytest.mark.parametrize("plant", [4.0, 0.0])
def test_a_planted_edge_is_recovered_net_of_exactly_the_stated_costs(plant: float, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, dec = _planted(plant, days=140, seed=5)
    risk = 20.0                                                   # 0.2 R planted when plant = 4.0
    spec = T.Spec(T.frame(dec, 1, risk=risk, cap=dec + 60 * MIN))
    monkeypatch.setattr(T, "N_TWIN", 0)                            # twins read the session tables this ctx lacks
    res = T.simulate(ctx, "PLANT", spec)
    df = T.price(res["real"], COSTS, SwapSpec.research_shipped())
    assert len(df) == len(dec) and set(df.kind) == {"time"}
    se = float(df.gross_R.std(ddof=1) / np.sqrt(len(df)))
    assert abs(float(df.gross_R.mean()) - plant / risk) < 4 * se          # recovered (0 on the martingale)
    rt, _ = COSTS.screen_charges(T.BROKER, np.array(["london", "newyork"]), T.COST_VERSION)
    assert float(df.swap.abs().max()) == 0.0                                # in-day holds cross no rollover
    assert np.allclose(df.R - df.gross_R, -df.cost.to_numpy() / risk)       # net = gross - the stated round trip
    assert set(np.round(df.cost.unique(), 6)) <= set(np.round(rt, 6))


# ---------------------------------------------------------------- (e) same minute, gaps, bid/ask
@pytest.mark.parametrize("side", [1, -1])
def test_walk_takes_the_stop_when_one_minute_touches_both_barriers(side: int) -> None:
    o, h, lo, c = _flat(30)
    h[5], lo[5] = 1300 + 6.0, 1300 - 6.0                          # minute 5 crosses the 5 stop and the 1 R target
    ctx = _ctx(o, h, lo, c)
    g, kind, kx, _ = T.walk(ctx, 0, side, 5.0, 1.0, np.nan, ctx.end, None)
    assert (kind, kx, g) == ("stop", 5, -5.0)


@pytest.mark.parametrize("side", [1, -1])
def test_walk_fills_a_gap_through_the_stop_at_the_open(side: int) -> None:
    o, h, lo, c = _flat(30)
    o[7] = c[7] = 1300 - side * 9.0                                  # opens 9 through a 5 stop
    h[7], lo[7] = max(o[7], c[7]) + 0.05, min(o[7], c[7]) - 0.05
    ctx = _ctx(o, h, lo, c)
    g, kind, kx, _ = T.walk(ctx, 0, side, 5.0, np.nan, np.nan, ctx.end, None)
    assert (kind, kx) == ("stop", 7) and g == pytest.approx(-9.0)


def _harness_frames(bid: dict[str, np.ndarray], spread: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """1m bars (bid + spread = ask) and one 10-minute decision bar ending at the first minute."""
    n = len(bid["open"])
    ts = pd.date_range("2012-03-06 08:00", periods=n, freq="1min", tz="UTC")
    b1 = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1)})
    for k, v in bid.items():
        b1[f"bid_{k}"], b1[f"ask_{k}"] = v, v + spread
    dec = pd.DataFrame({"ts_utc": [ts[0] - pd.Timedelta(minutes=10), ts[-1]], "visible_at": [ts[0], ts[-1]]})
    for k in ("open", "high", "low", "close"):
        dec[f"bid_{k}"] = dec[f"ask_{k}"] = 1300.0
    return dec, b1


def _harness(bid: dict[str, np.ndarray], side: int, stop: float, target_r: float, spread: float = 0.0) -> pd.Series:
    dec, b1 = _harness_frames(bid, spread)
    sig = pd.DataFrame({"idx": [0], "side": [side], "max_bars": [0], "target_atr": [target_r]})
    lab = triple_barrier_next_open_1m(dec, b1, sig, BarrierSpec(target_atr=1.0, stop_atr=1.0, max_bars=1),
                                      pd.Series([stop, stop]))
    assert len(lab) == 1
    return lab.iloc[0]


def _bid(n: int = 30, px: float = 1300.0) -> dict[str, np.ndarray]:
    o, h, lo, c = _flat(n, px)
    return {"open": o, "high": h, "low": lo, "close": c}


@pytest.mark.parametrize("side", [1, -1])
def test_harness_takes_the_stop_when_one_minute_touches_both_barriers(side: int) -> None:
    b = _bid()
    b["high"][4], b["low"][4] = 1306.0, 1294.0
    r = _harness(b, side, 5.0, 1.0)
    assert r.barrier_hit == "stop" and r.exit == pytest.approx(1300.0 - side * 5.0)


@pytest.mark.parametrize("side", [1, -1])
def test_harness_fills_a_gap_through_the_stop_at_the_open(side: int) -> None:
    b = _bid()
    b["open"][6] = b["close"][6] = 1300.0 - side * 9.0
    b["high"][6], b["low"][6] = b["open"][6] + 0.05, b["open"][6] - 0.05
    r = _harness(b, side, 5.0, 3.0)
    assert r.barrier_hit == "stop"
    assert r.exit == pytest.approx(1300.0 - side * 9.0)              # the open, not the 1295 / 1305 stop
    assert r.ret == pytest.approx(-9.0 / 1300.0)


def test_harness_gap_rule_leaves_an_ordinary_stop_at_the_stop() -> None:
    b = _bid()
    b["low"][6] = 1290.0                                             # trades through 1295 inside the minute
    r = _harness(b, 1, 5.0, 3.0)
    assert r.barrier_hit == "stop" and r.exit == pytest.approx(1295.0)


@pytest.mark.parametrize("side", [1, -1])
def test_harness_buys_the_ask_sells_the_bid_and_stops_on_the_exit_side(side: int) -> None:
    spread = 0.4
    b = _bid()
    # long: buys the ask open 1300.4, stop 1295.4 on the BID low 1295.3 (an entry at the bid, stop 1295.0, or a stop
    # read on the ask low 1295.7 would both miss it). Short: sells the bid open 1300.0, stop 1305.0 on the ASK high
    # 1305.1 (an entry at the ask, stop 1305.4, or a stop read on the bid high 1304.7 would both miss it)
    b["low"][3] = 1295.3
    b["high"][3] = 1305.1 - spread
    r = _harness(b, side, 5.0, 10.0, spread)
    assert r.barrier_hit == "stop"
    if side > 0:
        assert (r.entry, r.exit) == (pytest.approx(1300.4), pytest.approx(1295.4))
    else:
        assert (r.entry, r.exit) == (pytest.approx(1300.0), pytest.approx(1305.0))


# ---------------------------------------------------------------- swap: sign, rate, units
def test_swap_long_pays_short_shipped_pays_nothing_and_the_rate_scales_with_price() -> None:
    s = SwapSpec.research_shipped()
    assert s.ref_price_usd is not None
    assert s.ref_price_usd == CostSettings.model_fields["swap_ref_price_usd"].default
    long_, short = s.usd_per_oz(np.array([1, -1]))
    assert long_ < 0 and short == 0.0
    at = s.usd_per_oz_at(np.array([1, 1]), np.array([s.ref_price_usd, s.ref_price_usd / 3]))
    assert at[0] == pytest.approx(long_) and at[1] == pytest.approx(long_ / 3)
    # no reference price (every live path): the fixed-dollar charge, whatever the price
    plain = SwapSpec.shipped()
    assert plain.ref_price_usd is None
    assert plain.usd_per_oz_at(np.array([1]), np.array([1300.0]))[0] == pytest.approx(long_)


def test_trend_screen_price_charges_the_rate_on_the_trade_notional() -> None:
    t0 = pd.Timestamp("2013-06-04 12:00", tz="UTC").value                     # Tue 12:00 -> Fri 12:00: 1 + 3 + 1 nights
    t1 = pd.Timestamp("2013-06-07 12:00", tz="UTC").value
    row = {"t_fill": t0, "t_exit": t1, "side": 1, "risk": 10.0, "gross": 0.0, "kind": "time", "id": 0, "entry": 1300.0}
    swap = SwapSpec.research_shipped()
    assert swap.ref_price_usd is not None
    df = T.price([row, {**row, "side": -1, "id": 1}], COSTS, swap)
    nights = int(rollover_nights(pd.DatetimeIndex([pd.Timestamp(t0, tz="UTC")]),
                                 pd.DatetimeIndex([pd.Timestamp(t1, tz="UTC")]), swap.server_tz, swap.triple_weekday)[0])
    assert nights == 5
    expect = nights * swap.long_usd_per_lot / 100 * 1300.0 / swap.ref_price_usd
    assert df.swap.iloc[0] == pytest.approx(expect)                          # about -1.00, not the fixed -3.00
    assert df.swap.iloc[1] == 0.0


def test_harness_swap_is_charged_on_the_entry_notional() -> None:
    n = 4 * 1440
    b = _bid(n)
    dec, b1 = _harness_frames(b, 0.0)
    sig = pd.DataFrame({"idx": [0], "side": [1], "max_bars": [0], "target_atr": [100.0]})
    spec = BarrierSpec(target_atr=1.0, stop_atr=1.0, max_bars=1)
    swap = SwapSpec.research_shipped()
    assert swap.ref_price_usd is not None
    lab = triple_barrier_next_open_1m(dec, b1, sig, spec, pd.Series([50.0, 50.0]), swap=swap)
    r = lab.iloc[0]
    assert r.swap_nights >= 1
    paid = r.swap_ret * r.entry
    assert paid == pytest.approx(r.swap_nights * swap.long_usd_per_lot / 100 * 1300.0 / swap.ref_price_usd)


def test_loader_keeps_epoch_nanoseconds_from_millisecond_files(tmp_path: Path) -> None:
    ts = pd.date_range("2012-03-06 08:00", periods=5, freq="1min", tz="UTC").as_unit("ms")
    f = pd.DataFrame({"ts_utc": ts})
    for k in ("open", "high", "low", "close"):
        f[f"bid_{k}"], f[f"ask_{k}"] = 1300.0, 1300.2
    f.to_parquet(tmp_path / "xauusd_1m_dukascopy_2012.parquet", index=False)
    assert pd.read_parquet(tmp_path / "xauusd_1m_dukascopy_2012.parquet").ts_utc.dt.unit == "ms"
    m = T.load_minutes(tmp_path, pd.Timestamp("2012-01-01", tz="UTC"), pd.Timestamp("2012-12-31", tz="UTC"))
    assert m.t.tolist() == [pd.Timestamp(x).value for x in ts]
    assert m.o.to_numpy() == pytest.approx(np.full(5, 1300.1))
