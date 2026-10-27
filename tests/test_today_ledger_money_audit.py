"""Finance audit 2026-10-27 (commit 88daf137): failing tests that show money defects in the Today / trades ledger.

A closed trade whose net is unknown (the engine-fills fallback, pnl_basis "gross", or an arm whose broker deals were
unreadable) is silently left out of net_today and the List totals: the figure is shown as a complete net while a
closed trade's money is missing ("a number missing a trade is not a net figure")."""
from __future__ import annotations

import pandas as pd
import pytest

from goldbot.api import trades_ledger as tl
from goldbot.ops import gates_phase
from goldbot.ops.gates_phase import ClosedTrade
from tests.test_today_ledger import _day
from tests.test_trades_ledger import DAY, NOW, SCALP, _catalog, _closed, _engine


@pytest.fixture(autouse=True)
def _accounts(enabled_accounts):
    enabled_accounts(SCALP, DAY)


def _gross_close(state, pid: int, pnl: float) -> None:
    gates_phase.append_closed_trade(state, ClosedTrade(
        account_id=DAY, broker="icm", mode="demo", exit_utc=NOW - pd.Timedelta(minutes=20), ret=-0.001, pnl=pnl,
        equity_before=10_000.0, position_id=pid, agent_id="box_break_close-g0-1h", side=1, lots=0.05,
        entry_utc=NOW - pd.Timedelta(hours=2), entry_price=2400.0, exit_price=2398.0, exit_reason="stop", r=-1.0,
        commission=None, swap=None, timeframe="1h", initial_sl=2398.0, final_tp=2404.0))


def test_a_closed_trade_without_a_net_is_not_dropped_silently_from_net_today(tmp_path):
    _engine(tmp_path, DAY)
    _closed(tmp_path, DAY, "demo", 41, 3.95, "trail_close")
    _gross_close(tmp_path, 42, -8.00)
    d = _day(tmp_path, _catalog(tmp_path), account=DAY)
    # two closes today, one of them a -£8.00 loss whose commission and swap are unknown: +£3.95 is not the day's net
    assert d["net_today"] != 3.95, "net_today shows +3.95 as the day's net while a -8.00 close is missing from it"
    assert (d["net_today"], d["net_unknown"], d["net_today_partial"]) == (None, 1, 3.95)   # flagged, counted, shown
    assert d["by_kind"]["strategy"].net_unknown == 1 and d["strip"][-1].net_unknown == 1


def test_list_totals_do_not_present_a_partial_net_as_complete(tmp_path):
    _engine(tmp_path, DAY)
    _closed(tmp_path, DAY, "demo", 41, 3.95, "trail_close")
    _gross_close(tmp_path, 42, -8.00)
    cat = _catalog(tmp_path)
    page = tl.query(tl.build_ledger(tmp_path, NOW, cat=cat), tl.TradeQuery(account=(DAY,)))
    assert page.totals.n_closed == 2
    assert page.totals.net_pnl != 3.95, "totals.net_pnl is the net of 1 of 2 closed trades, with no flag"
