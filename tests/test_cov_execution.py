"""Paper broker fills, measured cost tables and the execution audit (design: Broker abstraction, Running cost)."""
import json

import numpy as np
import pandas as pd
import pytest

from goldbot.execution.audit import execution_audit
from goldbot.execution.broker import OrderIntent, Tick
from goldbot.execution.costs import CostTable, SlippageStat, SpreadStat, build_cost_table, slippage_table
from goldbot.execution.paper import PaperBroker

T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")       # Wednesday, London session


def _tick(sec: float, bid: float, ask: float) -> Tick:
    return Tick(ts_utc=T0 + pd.Timedelta(seconds=sec), bid=bid, ask=ask)


def _order(cid: str, side: int, sl: float, tp: float, lots: float = 0.10, magic: int = 260101) -> OrderIntent:
    return OrderIntent(client_order_id=cid, symbol="XAUUSD", side=side, lots=lots, sl=sl, tp=tp, magic=magic, comment=cid)


# ---------------------------------------------------------------------------------------------- paper broker
def test_entries_fill_at_the_touch_on_the_paying_side_when_spreads_are_steady():
    pb = PaperBroker()
    for i in range(10):
        pb.on_tick(_tick(i, 2400.0, 2400.30))
    buy = pb.place_order(_order("b", 1, 2390, 2410))
    sell = pb.place_order(_order("s", -1, 2410, 2390))
    assert buy.price == pytest.approx(2400.30) and sell.price == pytest.approx(2400.0)


def test_entries_fill_worse_than_the_touch_by_half_the_spread_deviation():
    pb = PaperBroker()
    for i, sp in enumerate([0.2, 0.4] * 5):
        pb.on_tick(_tick(i, 2400.0, 2400.0 + sp))
    half_sd = float(np.std([0.2, 0.4] * 5)) / 2
    buy = pb.place_order(_order("b", 1, 2390, 2410))
    sell = pb.place_order(_order("s", -1, 2410, 2390))
    assert buy.price == pytest.approx(2400.4 + half_sd)
    assert sell.price == pytest.approx(2400.0 - half_sd)


def test_short_stop_fills_with_adverse_slippage_and_target_fills_at_the_level():
    pb = PaperBroker()
    pb.on_tick(_tick(0, 2400.0, 2400.25))
    pb.place_order(_order("s1", -1, sl=2403.0, tp=2395.0))
    pb.on_tick(_tick(1, 2402.80, 2403.05))                  # ask through the stop
    stop = pb.deals_since(T0).query("type == 'stop'").iloc[0]
    assert stop["price"] == pytest.approx(2403.0 + 0.20)    # 20 points against a short
    pb.place_order(_order("s2", -1, sl=2410.0, tp=2400.0))
    pb.on_tick(_tick(2, 2399.70, 2399.95))                  # ask at/below the target
    tgt = pb.deals_since(T0).query("type == 'target'").iloc[0]
    assert tgt["price"] == pytest.approx(2400.0) and pb.positions() == []


def test_long_target_fill_and_round_trip_commission_accounting():
    pb = PaperBroker(equity=10_000, commission_per_lot_side=3.5)
    pb.on_tick(_tick(0, 2400.0, 2400.20))
    r = pb.place_order(_order("l1", 1, sl=2395.0, tp=2405.0, lots=0.5))
    assert r.price is not None
    assert pb.account().balance == pytest.approx(10_000 - 3.5 * 0.5)            # entry side charged at once
    pb.on_tick(_tick(1, 2402.0, 2402.20))
    acc = pb.account()
    assert acc.equity == pytest.approx(acc.balance + (2402.0 - r.price) * 0.5 * 100)   # floating P&L marked at the bid
    pb.on_tick(_tick(2, 2405.10, 2405.30))
    assert pb.positions() == []
    expected = 10_000 + (2405.0 - 2400.20) * 0.5 * 100 - 2 * 3.5 * 0.5
    assert pb.account().balance == pytest.approx(expected)
    deal = pb.deals_since(T0).query("type == 'target'").iloc[0]
    assert deal["pnl"] == pytest.approx((2405.0 - 2400.20) * 0.5 * 100 - 3.5 * 0.5)


def test_manual_close_modify_and_unknown_positions():
    pb = PaperBroker()
    pb.on_tick(_tick(0, 2400.0, 2400.20))
    pid = pb.place_order(_order("l1", 1, sl=2390.0, tp=2410.0)).position_id
    assert pid is not None
    m = pb.modify(pid, sl=2395.0, tp=None)
    assert m.ok and pb.positions()[0].sl == 2395.0 and pb.positions()[0].tp == 2410.0
    bad = pb.modify(999, sl=1.0, tp=None)
    assert not bad.ok and bad.retcode == 10013
    assert not pb.close(999).ok
    pb.on_tick(_tick(1, 2401.0, 2401.20))
    c = pb.close(pid)
    assert c.ok and c.price == 2401.0 and pb.positions() == []                   # a long closes at the bid
    assert not pb.close(pid).ok                                                  # already closed


def test_positions_filter_by_magic_prefix_and_deals_by_time():
    pb = PaperBroker()
    assert pb.deals_since(T0).empty
    pb.on_tick(_tick(0, 2400.0, 2400.20))
    pb.place_order(_order("icm", 1, 2390, 2410, magic=260101))
    pb.on_tick(_tick(60, 2400.0, 2400.20))
    pb.place_order(_order("van", 1, 2390, 2410, magic=260201))
    assert [p.magic for p in pb.positions(magic_prefix=2601)] == [260101]
    assert len(pb.positions()) == 2
    assert len(pb.deals_since(T0 + pd.Timedelta(seconds=30))) == 1


def test_symbol_info_reports_the_real_commission():
    info = PaperBroker(commission_per_lot_side=3.25).symbol_info("XAUUSD")
    assert info.commission_per_lot_side == 3.25 and info.volume_step == 0.01 and info.trade_allowed


@pytest.mark.xfail(strict=True, reason="BUG: paper.py _check_exits fills a gapped stop at sl - 20 points, not at the market "
                                       "that gapped through it, so weekend/event gaps fill better than reality "
                                       "(design: paper fills slightly worse than reality)")
def test_a_stop_gapped_through_fills_no_better_than_the_market():
    pb = PaperBroker()
    pb.on_tick(_tick(0, 2400.0, 2400.20))
    pb.place_order(_order("gap", 1, sl=2396.0, tp=2410.0))
    pb.on_tick(_tick(3600, 2380.0, 2380.30))               # gap $16 through the stop
    stop = pb.deals_since(T0).query("type == 'stop'").iloc[0]
    assert stop["price"] <= 2380.0


# ---------------------------------------------------------------------------------------------- cost tables
def _table(**kw) -> CostTable:
    base = dict(account_id="icm", built_utc=T0,
                spread={"london": SpreadStat(median=0.20, p90=0.30, n=100), "newyork": SpreadStat(median=0.35, p90=0.5, n=100)},
                slippage={"london:market": SlippageStat(mean=-0.05, n=80, from_prior=False),
                          "newyork:market": SlippageStat(mean=0.04, n=80, from_prior=False)},
                commission_per_lot_side_usd=3.5, slippage_prior_usd=0.15)
    base.update(kw)
    return CostTable(**base)  # type: ignore[arg-type]


def test_round_trip_never_credits_favourable_slippage_and_borrows_the_widest_spread():
    t = _table()
    # london: spread 0.20 + slippage clipped at 0 + commission 2 x 3.5 / 100
    assert t.round_trip_usd_per_oz("london") == pytest.approx(0.20 + 0.0 + 0.07)
    assert t.round_trip_usd_per_oz("newyork") == pytest.approx(0.35 + 0.08 + 0.07)
    # asia has no ticks: widest measured spread and the prior slippage (conservative)
    assert t.round_trip_usd_per_oz("asia") == pytest.approx(0.35 + 0.30 + 0.07)
    assert t.round_trip_atr("london", 2.7) == pytest.approx(0.27 / 2.7)
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        assert t.round_trip_atr("london", bad) is None


def test_unmeasured_tables_and_missing_files_give_no_cost(tmp_path):
    assert _table(spread={}).round_trip_atr("london", 3.0) is None
    assert CostTable.load(tmp_path / "missing.json") is None
    t = build_cost_table("icm", pd.DataFrame(columns=["ts_utc", "bid", "ask"]), pd.DataFrame(),
                         commission_per_lot_side_usd=3.5, slippage_prior_usd=0.15, now=T0)
    assert t.spread == {} and t.notes and all(s.from_prior and s.mean == 0.15 for s in t.slippage.values())


def test_slippage_cells_need_their_own_min_fills_and_ignore_unpriced_rows():
    n = 12
    fills = pd.DataFrame({"ts_utc": [T0 + pd.Timedelta(minutes=i) for i in range(n + 1)],
                          "side": [1] * n + [1], "requested": [2400.0] * n + [np.nan],
                          "filled": [2400.02] * n + [2500.0], "order_type": ["market"] * (n + 1)})
    cells = slippage_table(fills, prior_usd=0.15, min_fills=n)
    lon = cells["london:market"]
    assert lon.mean == pytest.approx(0.02) and lon.n == n and not lon.from_prior
    assert cells["asia:market"].from_prior and cells["asia:market"].mean == 0.15
    assert slippage_table(fills, prior_usd=0.15, min_fills=n + 1)["london:market"].from_prior


# ---------------------------------------------------------------------------------------------- execution audit
NOW = pd.Timestamp("2025-03-12 21:00", tz="UTC")


def _fills(n: int, slip: float, start: pd.Timestamp) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    return pd.DataFrame({"ts_utc": [start + pd.Timedelta(minutes=5 * i) for i in range(n)], "side": [1, -1] * (n // 2),
                         "requested": 2400.0,
                         "filled": [2400.0 + s * slip * (1 + 0.01 * e) for s, e in zip([1, -1] * (n // 2), rng.normal(size=n))],
                         "order_type": "market"})


def test_audit_flags_slippage_drift_against_the_table():
    t = _table(built_utc=NOW - pd.Timedelta(hours=10))
    rep = execution_audit("icm", _fills(20, 0.30, NOW - pd.Timedelta(days=1, hours=11)), pd.DataFrame(), t,
                          pd.DataFrame(), NOW)
    row = next(r for r in rep["slippage"] if r["session"] == "london")
    assert row["drift"] and row["recent_n"] == 20 and row["table_usd"] == -0.05
    assert any("slippage drift london/market" in f for f in rep["flags"])


def test_audit_does_not_flag_small_or_thin_samples():
    t = _table(built_utc=NOW - pd.Timedelta(hours=10))
    calm = execution_audit("icm", _fills(20, -0.02, NOW - pd.Timedelta(days=1, hours=11)), pd.DataFrame(), t, pd.DataFrame(), NOW)
    assert not any(r["drift"] for r in calm["slippage"])
    thin = execution_audit("icm", _fills(8, 0.9, NOW - pd.Timedelta(days=1, hours=11)), pd.DataFrame(), t, pd.DataFrame(), NOW)
    assert not any(r["drift"] for r in thin["slippage"])              # below MIN_RECENT_FILLS
    assert calm["flags"] == [] and thin["flags"] == []


def test_audit_flags_missing_or_old_tables_widened_spreads_and_failed_orders():
    rep = execution_audit("icm", pd.DataFrame(), pd.DataFrame(), None, pd.DataFrame(), NOW)
    assert rep["cost_table"] is None and any("no cost table" in f for f in rep["flags"])

    old = _table(built_utc=NOW - pd.Timedelta(days=3))
    ticks = pd.DataFrame({"ts_utc": [NOW - pd.Timedelta(days=1, hours=10, minutes=i) for i in range(30)],
                          "bid": 2400.0, "ask": 2400.45})
    decisions = pd.DataFrame({"action": ["order", "order", "order", "proposed"],
                              "detail": [json.dumps({"ok": False, "retcode": 10006}), json.dumps({"ok": True}), "not json", "{}"]})
    rep = execution_audit("icm", pd.DataFrame(), ticks, old, decisions, NOW)
    assert any("older than 2 days" in f for f in rep["flags"])
    sp = next(r for r in rep["spread"] if r["session"] == "london")
    assert sp["widened"] and sp["recent_median_usd"] == pytest.approx(0.45)
    assert rep["failed_orders"] == 1 and rep["orders"] == 3
    assert any("1 of 3 orders failed" in f for f in rep["flags"])
