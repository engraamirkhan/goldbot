"""Measured on the brain 2026-10-31: the learning book (aux.db exploration, control and probe closes, ~2,000 trades)
showed in perf-report's learning section only, while Costs, Days and North star read the closed-trade record alone and
said "not recorded". Those three sections now also read the learning book, labelled as the demo learning book, with
voided rows left out and censored ones never in R or money (exactly as the learning section does). The phase gate
keeps its own source."""
from __future__ import annotations

import pandas as pd
import pytest

from goldbot.ops.gates_phase import load_closed_trades
from goldbot.ops.perf_report import build_performance_report, format_markdown
from tests.test_learning_reports import ACC, CONTROL, _arm, _engine
from tests.test_learning_reports_matched import _void
from tests.test_perf_report import _bars

NOW = pd.Timestamp("2026-10-10 06:00", tz="UTC")
START, END = "2026-10-05", "2026-10-09"


@pytest.fixture(autouse=True)
def _accounts(enabled_accounts):
    enabled_accounts(ACC, "icm-demo")


def _report(state, **kw):
    return build_performance_report(state, start=START, end=END, now=NOW, **kw)


def test_days_section_reads_the_learning_book_when_the_closed_trade_record_is_empty(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "e1", day="2026-10-06", pnl=3.0)
    _arm(tmp_path, "e2", day="2026-10-06", minute=20, pnl=-1.0)
    _arm(tmp_path, "e3", day="2026-10-07", pnl=0.5)
    rep = _report(tmp_path)
    [a] = rep.accounts
    assert a.days.traded_days == 0                                              # the record's own days are untouched
    book = a.learning.days
    assert book.traded_days == 2 and book.weekdays == 5
    assert book.best_day == "2026-10-06" and book.best_day_pnl == pytest.approx(2.0)
    assert book.worst_day == "2026-10-07" and book.mean_per_weekday == pytest.approx(2.5 / 5)
    assert "Days: demo learning book" in format_markdown(rep)


def test_costs_section_reads_commission_and_swap_of_the_learning_book(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "e1", day="2026-10-06", commission=0.07, swap=-0.03)
    _arm(tmp_path, "e2", day="2026-10-06", minute=20, commission=0.07, swap=-0.03)
    _arm(tmp_path, "e3", day="2026-10-06", minute=40, commission=None)          # not recorded: not counted
    rep = _report(tmp_path)
    k = rep.accounts[0].learning.costs
    assert k.commission_trades == 2 and k.commission_total == pytest.approx(0.14)
    assert k.swap_trades == 2 and k.swap_total == pytest.approx(-0.06)
    assert k.cost_r_trades == 2 and k.cost_r_mean is not None and k.cost_r_mean > 0
    assert "Costs: demo learning book" in format_markdown(rep)


def test_voided_rows_are_left_out_and_censored_rows_never_reach_days_or_costs(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "ok", day="2026-10-06", pnl=1.0)
    _arm(tmp_path, "void", day="2026-10-07", pnl=50.0)
    _arm(tmp_path, "cens", day="2026-10-08", pnl=-30.0, censored=True)
    _void(tmp_path, "void")
    book = _report(tmp_path).accounts[0].learning
    assert book.days.traded_days == 1 and book.days.best_day_pnl == pytest.approx(1.0)
    assert book.costs.commission_trades == 1


def test_north_star_reads_the_learning_book_by_entry_opportunity_day(tmp_path):
    _engine(tmp_path)
    data = tmp_path / "data"
    _bars(data, "2026-10-07")
    _arm(tmp_path, "opp", day="2026-10-07", pnl=52.0)
    _arm(tmp_path, "ord", day="2026-10-05", pnl=-8.0)
    _arm(tmp_path, "c", CONTROL, day="2026-10-05", minute=30, pnl=1.0)
    rep = _report(tmp_path, data_root=data)
    ns = rep.accounts[0].learning.north_star
    assert ns.opportunity_days == 1 and ns.traded_opportunity_days == 1
    assert ns.pnl_on_opportunity_days == pytest.approx(52.0) and ns.pnl_on_other_days == pytest.approx(-7.0)
    assert ns.days_50_plus_on_opportunity == 1
    assert "North star: demo learning book" in format_markdown(rep)


def test_north_star_without_bars_says_so_for_the_learning_book_too(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "e1", day="2026-10-06")
    ns = _report(tmp_path).accounts[0].learning.north_star
    assert ns.opportunity_days is None and "not recorded" in ns.note


def test_the_phase_gate_source_is_untouched_by_the_learning_sections(tmp_path):
    _arm(tmp_path, "e1", day="2026-10-06")
    _report(tmp_path)
    assert load_closed_trades(tmp_path)[0] == []
