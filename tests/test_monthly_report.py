"""Monthly north-star report (BACKLOG 26 backend, ADR 0004 section 6): time-weighted % return net of deposits, R,
max drawdown, average per trading day, best days and the milestone progress, from closed trades. Reporting only."""
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
from goldbot.ops.monthly_report import build_monthly_report, format_report, main, month_to_date_line
from goldbot.ops.report_days import DAY_BOUNDARY

NOW = pd.Timestamp("2026-11-01 06:00", tz="UTC")


def _trade(day: str, pnl: float, equity_before: float, r: float | None, account: str = "icm-demo", pid: int = 0,
           **kw: Any) -> ClosedTrade:
    """A net row by default (pnl_basis "net": commission and swap inside pnl); `kw` overrides any field."""
    fields: dict[str, Any] = dict(account_id=account, broker="icm", mode="demo",
                                  exit_utc=pd.Timestamp(f"{day} 15:00", tz="UTC"),
                                  ret=0.0, pnl=pnl, equity_before=equity_before, r=r, position_id=pid,
                                  pnl_basis="net")
    fields.update(kw)
    return ClosedTrade(**fields)


def _seed(state: Path, currency: str | None = "GBP", engine: dict | None = None) -> None:
    rows = [
        _trade("2026-09-30", 500.0, 1000.0, 5.0, pid=1),       # September: not in October's report
        _trade("2026-10-01", 20.0, 1000.0, 2.0, pid=2),        # +2%
        _trade("2026-10-02", -10.2, 1020.0, -1.0, pid=3),      # -1%
        # a £1,000 deposit on Mon 5 Oct: the next trade starts from £2,009.80 + ... = 2,010 (not a return)
        _trade("2026-10-06", 60.3, 2010.0, 3.0, pid=4),        # +3%
        _trade("2026-10-07", -20.703, 2070.3, -1.0, pid=5),    # -1%
    ]
    for t in rows:
        append_closed_trade(state, t)
    e = {"account": "icm-demo", "equity": 2049.397, "balance": 2049.397, "day_start_equity": 2049.397, "ts": 0}
    if currency is not None:
        e["currency"] = currency
    e.update(engine or {})
    (state / "engine_icm-demo.json").write_text(json.dumps(e))


def test_golden_october_with_a_mid_month_deposit_that_is_not_counted_as_return(tmp_path):
    _seed(tmp_path)
    rep = build_monthly_report(tmp_path, month="2026-10", now=NOW)
    [a] = rep.accounts
    assert a.account_id == "icm-demo" and a.currency == "GBP" and a.trades == 4
    assert a.twr_pct == pytest.approx(100 * (1.02 * 0.99 * 1.03 * 0.99 - 1))          # 2.969306%, not +105%
    assert a.net_r == pytest.approx(3.0) and a.r_per_trade == pytest.approx(0.75) and a.r_trades == 4
    lo, hi = a.r_ci90                                                                  # type: ignore[misc]
    assert (lo, hi) == (pytest.approx(-1.6759, abs=1e-3), pytest.approx(3.1759, abs=1e-3))
    assert a.max_drawdown_pct == pytest.approx(1.0)
    assert a.pnl == pytest.approx(49.397)
    assert a.trading_days == 22                                                        # weekdays in October 2026
    assert a.avg_per_day == pytest.approx(49.397 / 22)
    assert [d.day for d in a.best_days] == ["2026-10-06", "2026-10-01", "2026-10-02"]
    assert [d.pnl for d in a.best_days] == pytest.approx([60.3, 20.0, -10.2])
    assert [d.r for d in a.best_days] == [3.0, 2.0, -1.0]
    assert a.days_50_plus == 1


def test_a_month_in_progress_averages_over_the_weekdays_so_far(tmp_path):
    _seed(tmp_path)
    [a] = build_monthly_report(tmp_path, month="2026-10", now=pd.Timestamp("2026-10-10 06:00", tz="UTC")).accounts
    assert a.trading_days == 7 and a.avg_per_day == pytest.approx(49.397 / 7)


def test_the_default_month_is_the_current_one_and_an_empty_month_says_no_trades(tmp_path):
    _seed(tmp_path)
    rep = build_monthly_report(tmp_path, now=NOW)                  # 1 Nov: November, nothing closed yet
    assert rep.month == "2026-11" and rep.accounts == []
    assert "no closed trades" in format_report(rep).lower()


def test_fifty_plus_days_are_counted_only_in_pounds(tmp_path):
    _seed(tmp_path, currency="USD")
    [a] = build_monthly_report(tmp_path, month="2026-10", now=NOW).accounts
    assert a.days_50_plus is None


def test_milestone_progress_comes_from_the_engines_live_state(tmp_path):
    lad = {"limits": {"risk_per_trade": 0.005, "max_risk_per_trade": 0.01, "multiplier_bounds": [0.25, 1.5],
                      "daily_cap": 0.02, "weekly_cap": 0.05},
           "price": 4150.0, "spread_points": 0.0, "stage": "normal",
           "ladder_atr": {"15m": 7.27, "1h": 14.6, "4h": 29.0, "1d": 70.0},
           "lot_terms": {"contract_oz": 100.0, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 50.0,
                         "stops_level_points": 0.0}}
    _seed(tmp_path, engine=lad)
    text = format_report(build_monthly_report(tmp_path, month="2026-10", now=NOW))
    assert "pending broker terms" in text                          # GBP and no currency factor from the engine yet
    _seed(tmp_path, engine={**lad, "usd_to_account": 1 / 1.33})
    rep = build_monthly_report(tmp_path, month="2026-10", now=NOW)
    [a] = rep.accounts
    assert a.milestones is not None and a.milestones.tradeable == ["15m"] and a.milestones.next_timeframe == "1h"
    assert "next: 1h at £" in format_report(rep)


def test_the_rendered_report_never_frames_a_day_as_a_target_to_hit(tmp_path):
    _seed(tmp_path)
    rep = build_monthly_report(tmp_path, month="2026-10", now=NOW)
    text = (format_report(rep) + "\n" + "\n".join(month_to_date_line(tmp_path, NOW - pd.Timedelta(days=1)))).lower()
    for word in ("target", "behind", "catch up", "catch-up", "countdown", "quota", "short of"):
        assert word not in text
    assert "£49.40" in text and "+2.97%" in text


def test_month_to_date_line_for_the_digest(tmp_path):
    _seed(tmp_path)
    [line] = month_to_date_line(tmp_path, pd.Timestamp("2026-10-10 06:45", tz="UTC"))
    assert line.startswith("• October so far:") and "4 trades" in line and "+3.00R" in line and "+2.97%" in line
    assert month_to_date_line(tmp_path, NOW) == []                 # nothing closed in November yet: no line


def test_cli_prints_the_report_and_json(tmp_path, capsys):
    _seed(tmp_path)
    assert main(["--state", str(tmp_path), "--month", "2026-10"]) == 0
    assert "icm-demo" in capsys.readouterr().out
    assert main(["--state", str(tmp_path), "--month", "2026-10", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["accounts"][0]["trades"] == 4
    assert main(["--state", str(tmp_path), "--month", "October"]) == 2


def test_run_py_dispatches_monthly_report(tmp_path):
    _seed(tmp_path)
    out = subprocess.run([sys.executable, "-m", "goldbot.ops.run", "monthly-report", "--state", str(tmp_path),
                          "--month", "2026-10"], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert "time-weighted" in out.stdout


def test_the_daily_digest_carries_the_month_so_far_line_under_its_line_budget(tmp_path):
    from goldbot.telegram.digest import build_digest
    _seed(tmp_path)
    text = build_digest(tmp_path, pd.Timestamp("2026-10-10 06:45", tz="UTC"), roadmap=tmp_path / "none.md")
    assert "• October so far: +2.97% time-weighted · 4 trades · +3.00R" in text
    assert len(text.splitlines()) <= 15


# ---------------------------------------------------------------------------------------------- review fixes
R_NOTE = "R understated on non-USD accounts until BACKLOG 28"


def test_r_on_a_gbp_account_is_flagged_per_trade_and_stays_flagged_after_the_currency_factor_appears(tmp_path):
    _seed(tmp_path)
    [a] = build_monthly_report(tmp_path, month="2026-10", now=NOW).accounts
    assert a.r_note == R_NOTE
    assert R_NOTE in format_report(build_monthly_report(tmp_path, month="2026-10", now=NOW))
    [line] = month_to_date_line(tmp_path, pd.Timestamp("2026-10-10 06:45", tz="UTC"))
    assert f"+3.00R ({R_NOTE})" in line
    _seed(tmp_path, engine={"usd_to_account": 1 / 1.33})       # BACKLOG 28 landed: these trades were recorded before
    [a] = build_monthly_report(tmp_path, month="2026-10", now=NOW).accounts
    assert a.r_note == R_NOTE and a.r_flagged == 4
    _seed(tmp_path, currency="USD")
    [a] = build_monthly_report(tmp_path, month="2026-10", now=NOW).accounts
    assert a.r_note is None


def test_overlapping_positions_mark_the_chained_return_as_approximate(tmp_path):
    for i, (entry, exit_) in enumerate((("2026-10-01 09:00", "2026-10-01 15:00"), ("2026-10-01 12:00", "2026-10-01 16:00"))):
        append_closed_trade(tmp_path, ClosedTrade(
            account_id="icm-demo", broker="icm", mode="demo", exit_utc=pd.Timestamp(exit_, tz="UTC"),
            entry_utc=pd.Timestamp(entry, tz="UTC"), ret=0.0, pnl=10.0, equity_before=1000.0 + 10 * i, r=1.0,
            position_id=i + 1, pnl_basis="net"))
    rep = build_monthly_report(tmp_path, month="2026-10", now=NOW)
    assert rep.accounts[0].overlapped is True
    assert "approximate: positions overlapped" in format_report(rep)
    (tmp_path / "solo").mkdir()
    _seed(tmp_path / "solo")
    solo = build_monthly_report(tmp_path / "solo", month="2026-10", now=NOW)
    assert solo.accounts[0].overlapped is False and "overlapped" not in format_report(solo)


def test_the_digest_counts_only_trades_closed_before_today(tmp_path):
    _seed(tmp_path)
    [line] = month_to_date_line(tmp_path, pd.Timestamp("2026-10-06 06:45", tz="UTC") - pd.Timedelta(days=1))
    assert "2 trades" in line                                      # the 6 and 7 Oct closes are not counted yet


def test_labels_say_what_the_figures_are(tmp_path):
    _seed(tmp_path)
    text = format_report(build_monthly_report(tmp_path, month="2026-10", now=NOW))
    assert "chained closed-trade return" in text and "Max drawdown on closed trades" in text


def test_a_trade_recorded_with_its_pnl_currency_is_not_flagged_and_a_mix_is_counted():
    from types import SimpleNamespace

    from goldbot.ops.monthly_report import r_understated
    old = SimpleNamespace(r=1.0)                                   # recorded before BACKLOG 28: no pnl_currency
    new = SimpleNamespace(r=1.0, pnl_currency="GBP")               # recorded with its currency basis
    assert r_understated(old, "GBP") and not r_understated(new, "GBP")
    assert not r_understated(old, "USD")                           # a USD account's R was never mis-scaled
    assert r_understated(old, None)                                # unknown currency: cannot vouch for it
    assert not r_understated(SimpleNamespace(r=None), "GBP")       # no R, nothing to flag


def test_labels_name_the_interval_and_drawdown_honestly(tmp_path):
    _seed(tmp_path)
    text = format_report(build_monthly_report(tmp_path, month="2026-10", now=NOW))
    assert "90% t-interval (approx.)" in text and "Max drawdown on closed trades this month" in text


# ---------------------------------------------------------------------------------------------- P&L basis (finance audit)
def _seed_mixed_basis(state: Path) -> None:
    """Two net rows, a legacy row that records commission and swap (net), a gross row and a legacy row without costs."""
    rows = [
        _trade("2026-10-01", 20.0, 1000.0, 2.0, pid=1),
        _trade("2026-10-02", -10.0, 1020.0, -1.0, pid=2),
        _trade("2026-10-05", 30.0, 1010.0, 3.0, pid=3, pnl_basis=None, commission=1.4, swap=-0.3),   # legacy, net
        _trade("2026-10-06", 70.0, 1040.0, 7.0, pid=4, pnl_basis="gross", money_source="engine"),     # costs unknown
        _trade("2026-10-07", 5.0, 1110.0, 0.5, pid=5, pnl_basis=None),                                # legacy, no costs
    ]
    for t in rows:
        append_closed_trade(state, t)
    (state / "engine_icm-demo.json").write_text(json.dumps({"account": "icm-demo", "currency": "GBP", "equity": 1115.0}))


def test_net_totals_sum_only_net_rows_and_legacy_rows_with_both_costs(tmp_path):
    _seed_mixed_basis(tmp_path)
    [a] = build_monthly_report(tmp_path, month="2026-10", now=NOW).accounts
    assert a.trades == 5 and a.net_trades == 3
    assert a.pnl == pytest.approx(40.0)                                     # 20 - 10 + 30: never the 70 or the 5
    assert a.net_r == pytest.approx(4.0) and a.r_trades == 3
    assert a.twr_pct == pytest.approx(100 * (1.02 * (1 - 10 / 1020) * (1 + 30 / 1010) - 1))
    assert [d.day for d in a.best_days] == ["2026-10-05", "2026-10-01", "2026-10-02"]   # the gross £70 day is no best day
    assert a.days_50_plus == 0                                              # a gross £70 is not a net £50 day
    assert a.avg_per_day == pytest.approx(40.0 / a.trading_days)


def test_gross_only_rows_are_counted_and_summed_apart_from_the_net(tmp_path):
    _seed_mixed_basis(tmp_path)
    [a] = build_monthly_report(tmp_path, month="2026-10", now=NOW).accounts
    assert a.gross_only == 2 and a.gross_pnl == pytest.approx(75.0) and a.gross_r == pytest.approx(7.5)
    assert a.gross_note == "2 trades gross only (costs not recorded)"


def test_the_report_never_labels_a_gross_figure_net(tmp_path):
    _seed_mixed_basis(tmp_path)
    text = format_report(build_monthly_report(tmp_path, month="2026-10", now=NOW))
    [line] = [x for x in text.splitlines() if "gross only" in x]
    assert "2 trades gross only (costs not recorded): +£75.00 · +7.50R gross, left out of every figure above" in line
    assert "net" not in line.lower()
    assert "net +£40.00 over 3 net trades of 5" in text
    [mtd] = month_to_date_line(tmp_path, pd.Timestamp("2026-10-10 06:45", tz="UTC"))
    assert "3 net trades" in mtd and "(2 trades gross only (costs not recorded), not summed)" in mtd
    assert "+4.00R" in mtd and "+11.50R" not in mtd


def test_a_month_of_net_rows_shows_no_gross_line(tmp_path):
    _seed(tmp_path)
    rep = build_monthly_report(tmp_path, month="2026-10", now=NOW)
    assert rep.accounts[0].gross_only == 0 and rep.accounts[0].gross_note is None
    assert "gross only" not in format_report(rep) and "gross only" not in month_to_date_line(tmp_path, NOW - pd.Timedelta(days=1))[0]


# ---------------------------------------------------------------------------------------------- day convention
def test_the_report_states_its_day_boundary_the_opportunity_day(tmp_path):
    rep = build_monthly_report(tmp_path, month="2026-10", now=NOW)
    assert rep.day_boundary == DAY_BOUNDARY and "22:00" in rep.day_boundary
    assert f"days run {DAY_BOUNDARY}" in format_report(rep)


def test_a_close_at_23_30_utc_belongs_to_the_next_opportunity_day_and_its_month(tmp_path):
    """Day D runs D-1 22:00 to D 22:00 UTC (as perf_report): 30 Sep 22:30 is 1 October's, 1 Oct 23:30 is 2 October's,
    and 31 Oct 23:30 is 1 November's, so it leaves October's report."""
    for pid, ts in enumerate(["2026-09-30 22:30", "2026-10-01 23:30", "2026-10-31 23:30"], 1):
        append_closed_trade(tmp_path, _trade("2026-10-01", 10.0 * pid, 1000.0, 1.0, pid=pid,
                                             exit_utc=pd.Timestamp(ts, tz="UTC")))
    [a] = build_monthly_report(tmp_path, month="2026-10", now=NOW).accounts
    assert sorted(d.day for d in a.best_days) == ["2026-10-01", "2026-10-02"] and a.trades == 2
    [n] = build_monthly_report(tmp_path, month="2026-11", now=pd.Timestamp("2026-11-02 06:00", tz="UTC")).accounts
    assert [d.day for d in n.best_days] == ["2026-11-01"] and n.trades == 1


def test_the_default_month_and_the_last_day_follow_the_opportunity_day_of_now(tmp_path):
    """At 31 Oct 23:00 UTC the opportunity day is 1 November: the default month is November."""
    append_closed_trade(tmp_path, _trade("2026-10-01", 10.0, 1000.0, 1.0, pid=1,
                                         exit_utc=pd.Timestamp("2026-10-31 22:30", tz="UTC")))
    rep = build_monthly_report(tmp_path, now=pd.Timestamp("2026-10-31 23:00", tz="UTC"))
    assert rep.month == "2026-11" and [a.trades for a in rep.accounts] == [1]
    [a] = rep.accounts
    assert a.trading_days == 0 and a.avg_per_day is None         # 1 Nov is a Sunday: no weekday elapsed yet


def test_the_digest_month_line_stops_at_yesterdays_22_utc_roll(tmp_path):
    """The digest passes `now - 1 day`: the month so far runs through that opportunity day, so a close after its
    22:00 UTC roll waits for the next digest."""
    _seed(tmp_path)
    append_closed_trade(tmp_path, _trade("2026-10-09", 5.0, 2049.397, 0.5, pid=9,
                                         exit_utc=pd.Timestamp("2026-10-09 22:30", tz="UTC")))
    [line] = month_to_date_line(tmp_path, pd.Timestamp("2026-10-09 06:45", tz="UTC"))
    assert "4 trades" in line
    [line] = month_to_date_line(tmp_path, pd.Timestamp("2026-10-10 06:45", tz="UTC"))
    assert "5 trades" in line


def test_a_month_without_a_trading_day_shows_the_average_per_day_as_n_a_not_zero(tmp_path):
    """Review Q3: a winter Friday close at 22:00 UTC is on the Saturday opportunity day; on 1 Nov (a Sunday) no weekday
    of November has elapsed, so there is no average to show: "n/a", never a £0.00 that reads as a flat day."""
    append_closed_trade(tmp_path, _trade("2026-10-01", 12.0, 1000.0, 1.0, pid=1,
                                         exit_utc=pd.Timestamp("2026-10-31 22:30", tz="UTC")))
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps({"account": "icm-demo", "currency": "GBP"}))
    now = pd.Timestamp("2026-11-01 12:00", tz="UTC")
    rep = build_monthly_report(tmp_path, now=now)
    [a] = rep.accounts
    assert a.trading_days == 0 and a.avg_per_day is None
    text = format_report(rep)
    assert "average n/a per trading day over 0 weekdays" in text and "£0.00" not in text
    [line] = month_to_date_line(tmp_path, now)
    assert "avg n/a/trading day" in line and "£0.00" not in line
