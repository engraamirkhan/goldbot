"""ADR 0106 decision 4: buy-and-hold of the same market, after costs and financing, over the same window
(goldbot/research/benchmarks.py)."""
import math

import pandas as pd
import pytest

from goldbot.labels.triple_barrier import SwapSpec
from goldbot.research.benchmarks import SESSION_DAYS_PER_YEAR, BenchmarkLine, buy_and_hold

# a week of daily closes at 22:00 UTC (after the 17:00 New York rollover), Monday 2026-03-02 .. Friday 03-06
CLOSES = pd.Series([2000.0, 2010.0, 2005.0, 2020.0, 2030.0],
                   index=pd.date_range("2026-03-02 22:00", periods=5, freq="D", tz="UTC"))
NO_SWAP = SwapSpec(long_usd_per_lot=0.0, short_usd_per_lot=0.0, server_tz="UTC")


def test_buy_and_hold_without_costs_is_the_price_return_of_the_window():
    bh = buy_and_hold(CLOSES, symbol="XAUUSD", round_trip_per_unit=0.0, swap=NO_SWAP)
    assert bh is not None
    assert bh.gross_ret == pytest.approx(2030 / 2000 - 1)
    assert bh.net_ret == pytest.approx(bh.gross_ret) and bh.cost_frac == 0 and bh.financing_frac == 0
    assert bh.days == 5 and bh.entry_price == 2000 and bh.exit_price == 2030


def test_buy_and_hold_pays_one_round_trip_at_the_entry_price():
    bh = buy_and_hold(CLOSES, symbol="XAUUSD", round_trip_per_unit=0.5, swap=NO_SWAP)
    assert bh is not None and bh.cost_frac == pytest.approx(0.5 / 2000)
    assert bh.net_ret == pytest.approx(2030 / 2000 - 1 - 0.5 / 2000)


def test_buy_and_hold_pays_the_long_swap_for_every_rollover_held_with_the_triple_day():
    # -$60 a lot (100 oz) a night = $0.60/oz; Mon..Fri closes hold through 4 rollovers, Wednesday's is triple: 6 nights
    swap = SwapSpec(long_usd_per_lot=-60.0, short_usd_per_lot=0.0, triple_weekday=2, server_tz="UTC")
    bh = buy_and_hold(CLOSES, symbol="XAUUSD", round_trip_per_unit=0.0, swap=swap)
    assert bh is not None and bh.nights == 6
    paid = 0.6 / 2000 + 0.6 / 2010 + 3 * 0.6 / 2005 + 0.6 / 2020   # each night at the close it was held from
    assert bh.financing_frac == pytest.approx(-paid)
    assert bh.net_ret == pytest.approx(2030 / 2000 - 1 - paid)


def test_a_swap_credit_counts_for_buy_and_hold_so_the_benchmark_is_never_understated():
    swap = SwapSpec(long_usd_per_lot=20.0, short_usd_per_lot=0.0, server_tz="UTC")
    bh = buy_and_hold(CLOSES, symbol="XAUUSD", round_trip_per_unit=0.0, swap=swap)
    assert bh is not None and bh.financing_frac > 0 and bh.net_ret > bh.gross_ret


def test_buy_and_hold_reads_only_the_window_and_needs_two_closes():
    start, end = CLOSES.index[1], CLOSES.index[3]
    bh = buy_and_hold(CLOSES, symbol="XAUUSD", round_trip_per_unit=0.0, swap=NO_SWAP, start=start, end=end)
    assert bh is not None and bh.entry_price == 2010 and bh.exit_price == 2020 and bh.days == 3
    assert buy_and_hold(CLOSES.iloc[:1], symbol="XAUUSD", round_trip_per_unit=0.0, swap=NO_SWAP) is None
    assert buy_and_hold(CLOSES, symbol="XAUUSD", round_trip_per_unit=0.0, swap=NO_SWAP,
                        start=end + pd.Timedelta(days=30)) is None


def test_buy_and_hold_sharpe_is_on_daily_net_returns_annualised_by_session_days():
    bh = buy_and_hold(CLOSES, symbol="XAUUSD", round_trip_per_unit=0.0, swap=NO_SWAP)
    r = CLOSES.pct_change().dropna()
    assert bh is not None and bh.sharpe_ann == pytest.approx(r.mean() / r.std(ddof=1) * math.sqrt(SESSION_DAYS_PER_YEAR))


def test_buy_and_hold_refuses_a_close_that_is_not_a_positive_finite_price():
    bad = CLOSES.copy()
    bad.iloc[2] = float("nan")
    with pytest.raises(ValueError, match="positive finite"):
        buy_and_hold(bad, symbol="XAUUSD", round_trip_per_unit=0.0, swap=NO_SWAP)


def test_the_benchmark_line_prints_buy_and_hold_beside_the_random_twin():
    bh = buy_and_hold(CLOSES, symbol="XAUUSD", round_trip_per_unit=0.0, swap=NO_SWAP)
    line = BenchmarkLine(random_twin_mean_r=0.01, buy_hold=bh)
    text = line.text()
    assert "random twin" in text and "buy-and-hold XAUUSD" in text
    assert "not supplied" in BenchmarkLine().text()
