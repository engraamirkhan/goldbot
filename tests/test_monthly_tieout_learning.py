"""Issue #394: the monthly review's broker tie-out never saw a demo trade. Every demo trade is an exploration trade or a
cost probe, recorded only in aux.db (`exploration_trades`, `cost_probes`) and never in state/closed_trades*.jsonl, so
the tie-out put the whole balance move in `difference` and kept the tolerance at its fixed part. The tie-out and the
monthly report now also read those trades from the trade ledger (ops/learning_ledger.py), counting rows voided as
evidence too: "the H-61 stops, account P&L and the broker reconciliation keep every trade" (evidence_exclusions).

All money here is synthetic."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.db import Database
from goldbot.ops import learning_ledger, ledger
from goldbot.ops import monthly_review as mr
from goldbot.ops.monthly_report import build_monthly_report, format_report
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
    # the per-trade tolerance grows with the learning trades too
    assert t.tolerance == pytest.approx(SETTINGS.monthly_review.balance_tolerance
                                        + 3 * SETTINGS.monthly_review.balance_tolerance_per_trade)


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
    t = _review(tmp_path).accounts[0].tie_out
    assert t.status == "reconciled" and t.trades == 3, t.reasons


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
