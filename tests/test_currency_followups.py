"""Final-review follow-ups to BACKLOG 28: no money is ever summed across currencies (supervisor with no fresh engine,
the digest, the monthly report), and the digest prints the account's own currency symbol."""
import json
import time
from pathlib import Path

import pandas as pd
import pytest

from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
from goldbot.ops.monthly_report import account_month, build_monthly_report, format_report, month_to_date_line
from goldbot.risk.supervisor import Supervisor
from goldbot.telegram.digest import trades_line

T0 = pd.Timestamp("2026-10-07 10:00", tz="UTC")


def _engine_file(d: Path, account: str, currency: str | None, *, equity: float = 1_000.0, age_s: float = 600,
                 lots: float = 0.0) -> None:
    (d / f"engine_{account}.json").write_text(json.dumps({
        "account": account, "ts": time.time() - age_s, "equity": equity, "day_start_equity": equity,
        "week_start_equity": equity, "balance_closed_hwm": equity, "currency": currency, "open_lots": lots,
        "open_notional": lots * 100 * 4_150.0}))


# ---------------------------------------------------------------------------------------------- 1. supervisor
def test_with_no_fresh_engine_stale_files_in_one_currency_are_summed_and_flagged(tmp_path):
    _engine_file(tmp_path, "a", "GBP", equity=1_000.0)
    _engine_file(tmp_path, "b", "GBP", equity=500.0)
    s = Supervisor(tmp_path).evaluate()
    assert s["no_fresh_engine"] and s["combined_equity"] == pytest.approx(1_500.0) and s["money_excluded"] == []


def test_with_no_fresh_engine_mixed_stale_currencies_sum_no_money_but_keep_exposure(tmp_path):
    _engine_file(tmp_path, "a", "GBP", equity=1_000.0, lots=0.01)
    _engine_file(tmp_path, "b", "USD", equity=10_000.0, lots=0.02)
    s = Supervisor(tmp_path).evaluate()
    assert s["no_fresh_engine"]
    assert s["combined_equity"] == 0.0 and s["day_loss"] == 0.0 and s["drawdown"] == 0.0
    assert s["money_excluded"] == ["engine_a.json", "engine_b.json"]
    assert s["combined_open_lots"] == pytest.approx(0.03)                   # exposure kept (strict)
    assert all(x["equity"] == 0.0 for x in s["exposure"].values())


def test_with_no_fresh_engine_a_file_without_a_currency_sums_no_money(tmp_path):
    _engine_file(tmp_path, "a", None, equity=1_000.0)
    s = Supervisor(tmp_path).evaluate()
    assert s["no_fresh_engine"] and s["combined_equity"] == 0.0 and s["money_excluded"] == ["engine_a.json"]


def test_a_fresh_engine_clears_the_flag(tmp_path):
    _engine_file(tmp_path, "a", "GBP", age_s=1.0)
    assert not Supervisor(tmp_path).evaluate()["no_fresh_engine"]


# ---------------------------------------------------------------------------------------------- 2/4. digest
def _trade(pnl: float, pnl_currency: str | None, pid: int, account: str = "icm-demo", r: float | None = 0.5,
           at: pd.Timestamp = T0) -> ClosedTrade:
    return ClosedTrade(account_id=account, broker="icm", mode="demo", exit_utc=at, ret=0.001, pnl=pnl,
                       equity_before=1_000.0, position_id=pid, r=r, pnl_currency=pnl_currency, pnl_basis="net")


def _state(d: Path, currencies: dict[str, str | None]) -> None:
    for acc, ccy in currencies.items():
        (d / f"engine_{acc}.json").write_text(json.dumps({"account": acc, "currency": ccy}))


def test_the_digest_prints_pounds_on_a_gbp_account(tmp_path):
    _state(tmp_path, {"icm-demo": "GBP"})
    append_closed_trade(tmp_path, _trade(8.20, "GBP", 1))
    append_closed_trade(tmp_path, _trade(-3.10, None, 2))                     # legacy row: the account's currency
    line = trades_line(tmp_path, T0 - pd.Timedelta(hours=1), T0 + pd.Timedelta(hours=1))[0]
    assert "net +£5.10" in line and "$" not in line


def test_the_digest_sums_per_currency_never_across(tmp_path):
    _state(tmp_path, {"icm-demo": "GBP"})
    append_closed_trade(tmp_path, _trade(5.00, "GBP", 1))
    append_closed_trade(tmp_path, _trade(-10.90, "USD", 2, r=-1.0))          # unconverted fallback
    line = trades_line(tmp_path, T0 - pd.Timedelta(hours=1), T0 + pd.Timedelta(hours=1))[0]
    assert "net +£5.00 · -$10.90" in line
    assert "-5.90" not in line


def test_the_digest_says_when_the_currency_is_not_reported(tmp_path):
    append_closed_trade(tmp_path, _trade(5.00, None, 1))                      # no engine file at all
    line = trades_line(tmp_path, T0 - pd.Timedelta(hours=1), T0 + pd.Timedelta(hours=1))[0]
    assert "net +5.00 (currency not reported)" in line and "$" not in line and "£" not in line


# ---------------------------------------------------------------------------------------------- 3. monthly report
def test_the_monthly_report_leaves_other_currency_rows_out_of_the_money(tmp_path):
    rows = [_trade(5.00, "GBP", 1), _trade(-10.90, "USD", 2, r=-1.0, at=T0 + pd.Timedelta(hours=1)),
            _trade(2.00, None, 3, at=T0 + pd.Timedelta(hours=2))]
    start, end = pd.Timestamp("2026-10-01", tz="UTC"), pd.Timestamp("2026-10-08", tz="UTC")
    m = account_month("icm-demo", rows, start, end, {"currency": "GBP", "equity": 1_000.0})
    assert m.pnl == pytest.approx(7.00) and m.trades == 3
    assert m.pnl_note == "1 in USD not summed" and m.pnl_excluded == 1
    assert m.twr_pct == pytest.approx(100 * ((1 + 5 / 1_000) * (1 + 2 / 1_000) - 1))
    assert sum(d.pnl for d in m.best_days) == pytest.approx(7.00)
    assert m.net_r == pytest.approx(0.0)                                      # R is currency-free: all rows count
    for r in rows:
        append_closed_trade(tmp_path, r)
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps({"account": "icm-demo", "currency": "GBP",
                                                               "equity": 1_000.0}))
    rep = build_monthly_report(tmp_path, now=T0 + pd.Timedelta(days=1))
    assert "(1 in USD not summed)" in format_report(rep)
    assert "(1 in USD not summed)" in month_to_date_line(tmp_path, T0 + pd.Timedelta(days=1))[0]


def test_a_month_in_one_currency_has_no_note(tmp_path):
    start, end = pd.Timestamp("2026-10-01", tz="UTC"), pd.Timestamp("2026-10-08", tz="UTC")
    m = account_month("icm-demo", [_trade(5.00, "GBP", 1)], start, end, {"currency": "GBP"})
    assert m.pnl_note is None and m.pnl_excluded == 0 and m.pnl == pytest.approx(5.0)
