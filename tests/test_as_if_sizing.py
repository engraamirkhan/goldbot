"""ADR 0106 decision 2: every promotion read is judged on the trades as if sized under the real-money limits
(goldbot/research/as_if_sizing.py)."""
import importlib.util

import pandas as pd
import pytest

from goldbot.research import as_if_sizing
from goldbot.research.as_if_sizing import (
    RM_DAILY_STOP,
    RM_DD_HALT,
    RM_MAX_ADV_SHARE,
    RM_MAX_RISK_PER_TRADE,
    AsIfInput,
    as_if_real_money,
)

T0 = pd.Timestamp("2026-03-02 08:00", tz="UTC")          # a Monday
ADV = {"XAUUSD": 1e11}


def leg(r: float, *, day: int = 0, hour: float = 0.0, stop: float = 10.0, spread_points: float | None = 20.0,
        entry: float = 2000.0, symbol: str = "XAUUSD", hold_h: float = 1.0) -> AsIfInput:
    at = T0 + pd.Timedelta(days=day, hours=hour)
    return AsIfInput(entry_utc=at, exit_utc=at + pd.Timedelta(hours=hold_h), side=1, entry_price=entry,
                     initial_sl=entry - stop, r=r, symbol=symbol, spread_points=spread_points)


def test_the_limits_are_the_owners_adr_0106_numbers():
    assert (RM_MAX_RISK_PER_TRADE, RM_DAILY_STOP, RM_DD_HALT, RM_MAX_ADV_SHARE) == (0.01, 0.02, 0.15, 0.01)


@pytest.mark.skipif(importlib.util.find_spec("goldbot.risk.real_money") is None,
                    reason="lane 1 (goldbot/risk/real_money.py) not merged yet")
def test_the_constants_equal_the_riskgates_real_money_constants_once_they_exist():
    rm = importlib.import_module("goldbot.risk.real_money")
    for name in ("RM_MAX_RISK_PER_TRADE", "RM_DAILY_STOP", "RM_DD_HALT", "RM_MAX_ADV_SHARE"):
        assert getattr(as_if_sizing, name) == getattr(rm, name), name


def test_a_trade_is_sized_to_at_most_one_percent_of_equity_including_the_spread():
    # $1,250 equity: $12.50 at 1%. One lot risks ($10 stop + $0.20 spread) x 100 oz = $1,020: 0.0122 lots -> 0.01
    rep = as_if_real_money([leg(1.0)], equity=1250.0, adv_usd=ADV)
    t = rep.trades[0]
    assert t.status == "sized" and t.lots == pytest.approx(0.01)
    assert t.risk_frac == pytest.approx(0.01 * 10.2 * 100 / 1250) and t.risk_frac <= RM_MAX_RISK_PER_TRADE
    # +1 R on the stop distance of the re-sized lots: 0.01 x 100 x $10 = $10
    assert t.pnl_frac == pytest.approx(10.0 / 1250)
    assert rep.equity_end == pytest.approx(1260.0) and rep.n_sized == 1 and rep.clean


def test_a_trade_below_the_minimum_lot_is_reported_and_kept_out_of_the_power_count():
    # $500 equity: $5 at 1% buys 0.0049 lots of a $10 stop: below the 0.01 minimum, never rounded up to it
    rep = as_if_real_money([leg(1.0), leg(-1.0, day=1)], equity=500.0, adv_usd=ADV)
    assert rep.below_min_lot == 2 and rep.n_sized == 0 and rep.n_sized_days == 0
    assert rep.excluded == {"below_min_lot": 2}
    assert rep.equity_end == 500.0 and all(t.pnl_frac == 0 for t in rep.trades)


def test_a_trade_without_a_stop_or_an_r_cannot_be_sized_and_is_excluded():
    no_stop = leg(1.0).model_copy(update={"initial_sl": None})
    no_r = leg(1.0, day=1).model_copy(update={"r": None})
    rep = as_if_real_money([no_stop, no_r], equity=1250.0, adv_usd=ADV)
    assert rep.excluded == {"no_stop": 1, "no_r": 1} and rep.n_sized == 0


def test_an_unknown_spread_is_charged_the_instruments_maximum_spread_so_sizing_errs_small():
    known = as_if_real_money([leg(1.0, spread_points=20.0)], equity=5000.0, adv_usd=ADV).trades[0]
    unknown = as_if_real_money([leg(1.0, spread_points=None)], equity=5000.0, adv_usd=ADV).trades[0]
    assert unknown.status == "sized" and unknown.lots <= known.lots


def test_unknown_volume_refuses_the_trade_and_a_too_large_order_is_cut_to_one_percent_of_volume():
    rep = as_if_real_money([leg(1.0)], equity=1250.0, adv_usd={})
    assert rep.excluded == {"adv_unknown": 1} and rep.n_sized == 0
    # 1% of a $300,000 ADV is $3,000: 0.01 lots of 100 oz at $2,000 is $2,000, so 0.01 fits and 0.02 would not
    big = as_if_real_money([leg(1.0)], equity=1_000_000.0, adv_usd={"XAUUSD": 300_000.0}).trades[0]
    assert big.status == "sized" and big.lots == pytest.approx(0.01)
    tiny = as_if_real_money([leg(1.0)], equity=1_000_000.0, adv_usd={"XAUUSD": 100_000.0})
    assert tiny.excluded == {"over_adv": 1}


def test_the_daily_stop_latches_entries_for_the_rest_of_the_risk_day():
    # three full stops of ~0.82% each: after the second the day is down 1.6%, after the third > 2%: a fourth is refused
    losers = [leg(-1.0, hour=h) for h in (0, 2, 4, 6)]
    rep = as_if_real_money(losers, equity=12_500.0, adv_usd=ADV)
    assert [t.status for t in rep.trades] == ["sized", "sized", "sized", "daily_stopped"]
    assert rep.excluded == {"daily_stopped": 1}
    # the third entry came at a 1.9% day loss and lost a full 1%: the RiskGate's latch allows it, so the day breaches
    assert "daily_stop" in rep.breaches


def test_a_day_that_loses_more_than_two_percent_is_a_breach():
    # two trades open together, both stopped: the latch cannot stop what is already open, 2 x 0.82% + an overshoot
    rep = as_if_real_money([leg(-1.0, hold_h=5), leg(-1.6, hour=0.5, hold_h=4)], equity=12_500.0, adv_usd=ADV)
    assert rep.worst_day_loss > RM_DAILY_STOP and "daily_stop" in rep.breaches and not rep.clean


def test_a_fifteen_percent_drawdown_is_a_breach_and_halts_every_later_entry():
    losers = [leg(-1.0, day=d) for d in range(25)]
    rep = as_if_real_money(losers, equity=12_500.0, adv_usd=ADV)
    assert "drawdown_halt" in rep.breaches and rep.max_drawdown >= RM_DD_HALT
    assert rep.trades[-1].status == "halted" and rep.excluded.get("halted", 0) > 0


def test_a_loss_beyond_one_percent_past_the_stop_is_counted_as_an_overshoot():
    rep = as_if_real_money([leg(-1.6)], equity=12_500.0, adv_usd=ADV)
    assert rep.overshoots == 1 and rep.worst_trade_loss > RM_MAX_RISK_PER_TRADE


def test_daily_returns_are_per_session_day_of_the_exit_on_the_day_start_equity():
    rep = as_if_real_money([leg(1.0), leg(1.0, hour=3), leg(-1.0, day=1)], equity=12_500.0, adv_usd=ADV)
    days = sorted(rep.daily)
    assert len(days) == 2
    first = rep.daily[days[0]]
    assert first == pytest.approx(sum(t.pnl_money for t in rep.trades[:2]) / 12_500.0)
    assert rep.n_sized_days == 2


def test_the_replay_reports_trades_in_entry_order_whatever_the_input_order():
    late, early = leg(1.0, day=1), leg(-1.0)
    rep = as_if_real_money([late, early], equity=12_500.0, adv_usd=ADV)
    assert [t.index for t in rep.trades] == [1, 0]
