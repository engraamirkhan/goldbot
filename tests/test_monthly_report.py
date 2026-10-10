"""Monthly north-star report (BACKLOG 26 backend, ADR 0004 section 6): time-weighted % return net of deposits, R,
max drawdown, average per trading day, best days and the milestone progress, from closed trades. Reporting only."""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
from goldbot.ops.monthly_report import build_monthly_report, format_report, main, month_to_date_line

NOW = pd.Timestamp("2026-11-01 06:00", tz="UTC")


def _trade(day: str, pnl: float, equity_before: float, r: float | None, account: str = "icm-demo", pid: int = 0) -> ClosedTrade:
    return ClosedTrade(account_id=account, broker="icm", mode="demo", exit_utc=pd.Timestamp(f"{day} 15:00", tz="UTC"),
                       ret=0.0, pnl=pnl, equity_before=equity_before, r=r, position_id=pid)


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
