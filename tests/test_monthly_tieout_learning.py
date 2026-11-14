"""Issue #394: the monthly review's broker tie-out never saw a demo trade. Every demo trade is an exploration trade or a
cost probe, recorded only in aux.db (`exploration_trades`, `cost_probes`) and never in state/closed_trades*.jsonl, so
the tie-out put the whole balance move in `difference` and kept the tolerance at its fixed part. The tie-out and the
monthly report now also read those trades from the trade ledger (ops/learning_ledger.py), counting rows voided as
evidence too: "the H-61 stops, account P&L and the broker reconciliation keep every trade" (evidence_exclusions).

All money here is synthetic."""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.db import Database
from goldbot.ops import learning_ledger, ledger
from goldbot.ops import monthly_review as mr
from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
from goldbot.ops.monthly_report import build_monthly_report, format_report, month_to_date_line
from goldbot.research import evidence_exclusions as ev
from tests.test_learning_reports import _arm, _engine, _probe

SETTINGS = load_settings()
ACC = "icm-probe"                                     # tests.test_learning_reports' account
MONTH = "2026-10"
NOW = pd.Timestamp("2026-11-01 06:30", tz="UTC")


@pytest.fixture(autouse=True)
def _accounts(enabled_accounts):
    enabled_accounts(ACC)


def _snap(state: Path, ts: str, balance: float, identity: str | None = "fp-a") -> None:
    t = pd.Timestamp(ts, tz="UTC")
    with Database.open(state, "aux").write() as conn:
        ledger.SNAPSHOTS.upsert(conn, ledger.BalanceSnapshot(
            account_id=ACC, ts_utc=t, balance=balance, equity=balance, margin=0.0, margin_free=balance,
            currency="GBP", deals_through_utc=t, slot_utc=t, identity=identity))


def _review(state: Path) -> mr.MonthlyReview:
    class Trials:
        def budget_used(self, quarter: str) -> int:
            return 0
    return mr.build_monthly_review(settings=SETTINGS, state_dir=state, month=MONTH, now=NOW, account_ids=[ACC],
                                   trials=Trials())


def _exploration_month(state: Path) -> None:
    """Two exploration trades (+1.20, -2.40) and a probe (+1.41) in October: +0.21 in all."""
    _engine(state)
    _arm(state, "e1", pnl=1.2)
    _arm(state, "e2", minute=20, pnl=-2.4)
    _probe(state)


def test_an_exploration_only_account_whose_learning_trades_explain_the_balance_reconciles(tmp_path):
    _exploration_month(tmp_path)
    _snap(tmp_path, "2026-09-30 23:25", 1000.0)
    _snap(tmp_path, "2026-10-31 23:25", 1000.21)
    t = _review(tmp_path).accounts[0].tie_out
    assert t.status == "reconciled", t.reasons
    assert t.trades == 3 and t.trade_pnl == pytest.approx(0.21) and t.difference == pytest.approx(0.0)
    # the per-trade tolerance grows with the learning trades too (as the square root of the count)
    assert t.tolerance == pytest.approx(SETTINGS.monthly_review.balance_tolerance
                                        + math.sqrt(3) * SETTINGS.monthly_review.balance_tolerance_per_trade, abs=0.005)


def test_learning_trades_outside_the_snapshot_window_are_not_counted(tmp_path):
    _exploration_month(tmp_path)
    _snap(tmp_path, "2026-09-30 23:25", 1000.0)
    _snap(tmp_path, "2026-10-28 09:30", 1001.41)                # after the probe's exit, before the exploration ones
    t = _review(tmp_path).accounts[0].tie_out
    assert t.trades == 1 and t.trade_pnl == pytest.approx(1.41) and t.status == "reconciled", t.reasons


def test_a_row_voided_as_evidence_still_counts_as_money(tmp_path):
    _exploration_month(tmp_path)
    ev.record(Database.open(tmp_path, "aux"), [ev.Exclusion(
        client_order_id="e2", kind="exploration", account_id=ACC, scope=ev.EXPLORATION_SCOPE, reason="test",
        span_ref="span:test", decided_utc=pd.Timestamp("2026-10-29", tz="UTC"))])
    # the evidence reader still drops it ...
    assert {c.position_id or c.exit_utc.minute for c in learning_ledger.read_learning_closes(tmp_path, NOW).rows} \
        == {10, 0}
    # ... the money reader keeps it, and the tie-out balances only with it
    money = learning_ledger.read_learning_closes(tmp_path, NOW, include_excluded=True).rows
    assert len(money) == 3
    _snap(tmp_path, "2026-09-30 23:25", 1000.0)
    _snap(tmp_path, "2026-10-31 23:25", 1000.21)
    rev = _review(tmp_path)
    t = rev.accounts[0].tie_out
    assert t.status == "reconciled" and t.trades == 3, t.reasons
    # the report labels it account money, not evidence, and says how many rows are void as evidence
    rep = rev.accounts[0].report
    assert rep is not None and rep.learning_voided == 1 and rep.learning_censored == 0


def test_an_unreadable_learning_source_gives_a_reason_never_a_silent_zero(tmp_path, monkeypatch):
    _snap(tmp_path, "2026-09-30 23:25", 1000.0)
    _snap(tmp_path, "2026-10-31 23:25", 1000.0)                 # flat: it would pass if the trades were read as 0

    def torn(*a, **k):
        return learning_ledger.LearningCloses(rows=[], unreadable=["exploration"])
    monkeypatch.setattr(mr, "closed_learning_trades", torn)
    t = _review(tmp_path).accounts[0].tie_out
    assert t.status == "unreconciled" and any("exploration" in r and "missing, not zero" in r for r in t.reasons)

    def boom(*a, **k):
        raise OSError("torn")
    monkeypatch.setattr(mr, "closed_learning_trades", boom)
    rev = _review(tmp_path)
    t = rev.accounts[0].tie_out
    assert t.status == "unreconciled" and any("trade ledger unreadable" in r for r in t.reasons)
    assert rev.status == "flagged"


def test_a_snapshot_without_a_login_fingerprint_cannot_rule_out_a_shared_login(tmp_path):
    _snap(tmp_path, "2026-09-30 23:25", 1000.0, identity=None)
    _snap(tmp_path, "2026-10-31 23:25", 1000.0)
    t = _review(tmp_path).accounts[0].tie_out
    assert t.status == "unreconciled" and any("shared login cannot be ruled out" in r for r in t.reasons)

    fine = tmp_path / "fine"
    fine.mkdir()
    _snap(fine, "2026-09-30 23:25", 1000.0)
    _snap(fine, "2026-10-31 23:25", 1000.0)
    assert _review(fine).accounts[0].tie_out.status == "reconciled"


def test_the_monthly_report_shows_an_exploration_only_account(tmp_path):
    _exploration_month(tmp_path)
    rep = build_monthly_report(tmp_path, month=MONTH, now=NOW)
    [a] = rep.accounts
    assert a.account_id == ACC and a.trades == 0                 # the closed-trade figures are untouched
    assert a.learning_trades == 3 and a.learning_pnl == pytest.approx(0.21)
    assert "3 exploration and probe trades" in format_report(rep)
    # and the review carries that report instead of None
    _snap(tmp_path, "2026-09-30 23:25", 1000.0)
    _snap(tmp_path, "2026-10-31 23:25", 1000.21)
    assert _review(tmp_path).accounts[0].report is not None


def _closes(n: int, pnl: float) -> list[learning_ledger.LearningClose]:
    t = pd.Timestamp("2026-10-15 12:00", tz="UTC")
    return [learning_ledger.LearningClose(account=ACC, kind="exploration", family="x", opened_utc=t, exit_utc=t,
                                          side="long", currency="GBP", pnl=pnl, net=True) for _ in range(n)]


def test_thousands_of_learning_trades_do_not_widen_the_tolerance_enough_to_hide_ten_pounds(tmp_path):
    # per-row rounding is at most half a penny, so the month's sum spreads as sqrt(n), not n: a linear tolerance at
    # 4,000 trades (~40) would pass a 10 move that nothing explains
    _snap(tmp_path, "2026-09-30 23:25", 1000.0)
    _snap(tmp_path, "2026-10-31 23:25", 1000.0 + 4000 * 0.01 + 10.0)
    t = mr.tie_out(Database.open(tmp_path, "aux"), ACC, [], MONTH, SETTINGS.monthly_review, _closes(4000, 0.01))
    assert t.trades == 4000 and t.status == "unreconciled" and t.difference == pytest.approx(10.0)
    big = mr.tie_out(Database.open(tmp_path, "aux"), ACC, [], MONTH, SETTINGS.monthly_review, _closes(4400, 0.0))
    assert big.tolerance is not None and big.tolerance < 2.0


def test_the_digest_month_line_shows_only_accounts_with_phase_gate_trades(tmp_path):
    # the digest prints the month line only beside a real-money account: an exploration-only demo account (and the
    # learning rows) stay out of it
    _exploration_month(tmp_path)
    append_closed_trade(tmp_path, ClosedTrade(
        account_id="icm-live", broker="icm", mode="live", exit_utc=pd.Timestamp("2026-10-28 12:00", tz="UTC"),
        ret=0.002, pnl=2.0, equity_before=1000.0, position_id=7, r=1.0, commission=0.1, swap=0.0, pnl_basis="net",
        pnl_currency="GBP"))
    [line] = month_to_date_line(tmp_path, pd.Timestamp("2026-10-30 12:00", tz="UTC"))
    assert ACC not in line and "exploration" not in line and "1 trade" in line


def test_an_account_with_only_learning_trades_says_no_phase_gate_trades_and_counts_censored(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "e1", pnl=1.2)
    _arm(tmp_path, "e2", minute=20, pnl=-0.5, censored=True)
    rep = build_monthly_report(tmp_path, month=MONTH, now=NOW)
    [a] = rep.accounts
    assert a.learning_censored == 1 and a.learning_voided == 0
    text = format_report(rep)
    assert "no phase-gate trades" in text and "Return" not in text and "Max drawdown" not in text
    assert "account money, not evidence" in text and "1 censored" in text


def test_a_learning_row_on_an_unknown_account_is_an_error_not_a_new_account(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "e1", account="icm-elsewhere")
    rep = build_monthly_report(tmp_path, month=MONTH, now=NOW)
    assert rep.accounts == []
    assert any("icm-elsewhere" in e and "not a configured account" in e for e in rep.errors)
