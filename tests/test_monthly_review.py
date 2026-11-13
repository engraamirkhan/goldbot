"""The monthly review (goldbot/ops/monthly_review.py, R142, BACKLOG 223-226): per account the month's report and the
tie-out of goldbot's net P&L against the broker balances, agent and infra spend, the trial budget used and the
reliability scorecard; stored in aux.db `monthly_reviews`, sent to the owner on Telegram, and opened as a GitHub issue
labelled `review-monthly` for the program-director's next session."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.config import load_settings
from goldbot.db import Database
from goldbot.ops import ledger
from goldbot.ops import monthly_review as mr
from goldbot.ops.alert_log import AlertLog
from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
from goldbot.ops.jobs import JOBS, schedule_of
from goldbot.ops.scheduler import JobState, Scheduler

SETTINGS = load_settings()
ACC = "icm-demo"
UTC = "UTC"


class FakeTrials:
    def __init__(self, used: int) -> None:
        self.used = used
        self.asked: list[str] = []

    def budget_used(self, quarter: str) -> int:
        self.asked.append(quarter)
        return self.used


class RecordingSink:
    def __init__(self) -> None:
        self.opened: list[tuple[str, str, str]] = []

    def open_issue(self, label: str, title: str, body: str) -> str:
        self.opened.append((label, title, body))
        return "https://github.invalid/issue/1"


def _snap(state: Path, account: str, ts: str, balance: float, *, currency: str = "GBP") -> None:
    t = pd.Timestamp(ts, tz=UTC)
    snap = ledger.BalanceSnapshot(account_id=account, ts_utc=t, balance=balance, equity=balance, margin=0.0,
                                  margin_free=balance, currency=currency, deals_through_utc=t, slot_utc=t)
    with Database.open(state, "aux").write() as conn:
        ledger.SNAPSHOTS.upsert(conn, snap)


def _deposit(state: Path, account: str, ts: str, amount: float, ticket: int) -> None:
    d = ledger.BalanceDeal(account_id=account, ticket=ticket, ts_utc=pd.Timestamp(ts, tz=UTC),
                           kind="deposit" if amount > 0 else "withdrawal", deal_type=2, amount=amount)
    with Database.open(state, "aux").write() as conn:
        ledger.DEALS.upsert(conn, d)


def _trade(state: Path, account: str, exit_utc: str, pnl: float, pid: int) -> None:
    append_closed_trade(state, ClosedTrade(
        account_id=account, broker="icm", mode="demo", exit_utc=pd.Timestamp(exit_utc, tz=UTC), ret=pnl / 1000,
        pnl=pnl, equity_before=1000.0, position_id=pid, entry_utc=pd.Timestamp(exit_utc, tz=UTC) - pd.Timedelta("1h"),
        r=pnl / 10, commission=0.5, swap=0.0, pnl_currency="GBP"))


def _review(state: Path, month: str = "2026-11", accounts: list[str] | None = None) -> mr.MonthlyReview:
    return mr.build_monthly_review(settings=SETTINGS, state_dir=state, month=month,
                                   now=pd.Timestamp("2026-12-01 06:30", tz=UTC), account_ids=accounts or [ACC],
                                   trials=FakeTrials(40))


# ------------------------------------------------------------------------------------------------ tie-out
def test_unreconciled_balance_flags_not_passes(tmp_path):
    # opening 1,000 (last snapshot before the month), closing 1,100; goldbot recorded +60 net and a +10 deposit:
    # 30 moved that no record explains -> the account is unreconciled and the review is flagged
    _snap(tmp_path, ACC, "2026-10-31 23:25", 1000.0)
    _snap(tmp_path, ACC, "2026-11-30 23:25", 1100.0)
    _trade(tmp_path, ACC, "2026-11-10 14:00", 25.0, 1)
    _trade(tmp_path, ACC, "2026-11-20 14:00", 35.0, 2)
    _deposit(tmp_path, ACC, "2026-11-15 09:00", 10.0, 900)
    rev = _review(tmp_path)
    t = rev.accounts[0].tie_out
    assert t.status == "unreconciled"
    assert t.opening == 1000.0 and t.closing == 1100.0 and t.trade_pnl == 60.0 and t.deposits == 10.0
    assert round(t.difference or 0, 2) == 30.0 and t.tolerance is not None and t.tolerance < 1.0
    assert rev.status == "flagged" and any("unreconciled" in f for f in rev.flags)

    # the same month explained to the penny (+90 net) reconciles
    other = tmp_path / "ok"
    other.mkdir()
    _snap(other, ACC, "2026-10-31 23:25", 1000.0)
    _snap(other, ACC, "2026-11-30 23:25", 1100.0)
    _trade(other, ACC, "2026-11-10 14:00", 90.0, 1)
    _deposit(other, ACC, "2026-11-15 09:00", 10.0, 900)
    assert _review(other).accounts[0].tie_out.status == "reconciled"

    # a difference just over the tolerance still flags: the tolerance is rounding, not money
    edge = tmp_path / "edge"
    edge.mkdir()
    _snap(edge, ACC, "2026-10-31 23:25", 1000.0)
    _snap(edge, ACC, "2026-11-30 23:25", 1100.0)
    _trade(edge, ACC, "2026-11-10 14:00", 100.0 - SETTINGS.monthly_review.balance_tolerance
           - SETTINGS.monthly_review.balance_tolerance_per_trade - 0.02, 1)
    assert _review(edge).accounts[0].tie_out.status == "unreconciled"

    # no broker balance at all is never a pass: no_data flags
    empty = tmp_path / "empty"
    empty.mkdir()
    _trade(empty, ACC, "2026-11-10 14:00", 10.0, 1)
    rev = _review(empty)
    assert rev.accounts[0].tie_out.status == "no_data" and rev.status == "flagged"


def test_a_failed_daily_ledger_in_the_month_flags_even_when_the_month_nets_out(tmp_path):
    _snap(tmp_path, ACC, "2026-10-31 23:25", 1000.0)
    _snap(tmp_path, ACC, "2026-11-30 23:25", 1050.0)
    _trade(tmp_path, ACC, "2026-11-10 14:00", 50.0, 1)
    t = pd.Timestamp("2026-11-12 23:25", tz=UTC)
    rep = ledger.LedgerReport(account_id=ACC, ts_utc=t, window_from=t - pd.Timedelta("1D"), currency="GBP",
                              positions_checked=1, matched=1, pending=0, status="fail", slot_utc=t)
    with Database.open(tmp_path, "aux").write() as conn:
        ledger.REPORTS.upsert(conn, rep)
    tie = _review(tmp_path).accounts[0].tie_out
    assert tie.status == "unreconciled" and tie.ledger_fail_days == 1


# ------------------------------------------------------------------------------------------------ month and schedule
def test_month_boundary_uses_utc_and_catches_up(tmp_path):
    # the reviewed month is the calendar month before the slot's UTC month, whatever zone the slot arrives in
    assert mr.review_month(pd.Timestamp("2026-12-01 06:30", tz=UTC)) == "2026-11"
    assert mr.review_month(pd.Timestamp("2027-01-01 06:30", tz=UTC)) == "2026-12"
    # 03:00 in Karachi on 1 December is 22:00 UTC on 30 November: still November's slot, so October is reviewed
    assert mr.review_month(pd.Timestamp("2026-12-01 03:00", tz="Asia/Karachi")) == "2026-10"
    assert mr.month_window("2026-11") == (pd.Timestamp("2026-11-01", tz=UTC), pd.Timestamp("2026-12-01", tz=UTC))

    # the closing balance is the last snapshot before 00:00 UTC on the 1st; one a minute later belongs to December
    _snap(tmp_path, ACC, "2026-10-31 23:25", 1000.0)
    _snap(tmp_path, ACC, "2026-11-30 23:59", 1100.0)
    _snap(tmp_path, ACC, "2026-12-01 00:01", 5000.0)
    _trade(tmp_path, ACC, "2026-11-30 23:30", 100.0, 1)
    tie = _review(tmp_path).accounts[0].tie_out
    assert tie.closing == 1100.0 and tie.status == "reconciled"

    # registered as a scheduler job: day 1 of each month, UTC, caught up after downtime
    assert JOBS["monthly_review"].__name__ == "monthly_review"
    cfg = SETTINGS.scheduler.monthly_review
    assert cfg.kind == "monthly" and cfg.day == 1 and cfg.max_late_hours >= 72
    now = [pd.Timestamp("2026-11-15 12:00", tz=UTC)]
    slots: list[pd.Timestamp] = []

    def job(slot: pd.Timestamp) -> dict[str, Any]:
        slots.append(slot)
        return {}

    sch = Scheduler(tmp_path / "scheduler.json", clock=lambda: now[0])
    sch.add("monthly_review", schedule_of(cfg), job)
    assert sch.run_pending() == []                       # first sight waits for its next slot
    now[0] = pd.Timestamp("2026-12-04 09:00", tz=UTC)    # the brain was down from 1 to 4 December
    assert sch.run_pending() == ["monthly_review"]
    assert slots[-1].tz is not None and str(slots[-1].tz) == "UTC"
    assert slots[-1].normalize() == pd.Timestamp("2026-12-01", tz=UTC)
    assert mr.review_month(slots[-1]) == "2026-11"
    assert sch.run_pending() == []                       # once per slot
    now[0] = pd.Timestamp("2027-01-01 23:00", tz=UTC)
    assert sch.run_pending() == ["monthly_review"] and mr.review_month(slots[-1]) == "2026-12"


# ------------------------------------------------------------------------------------------------ outputs
def _identifying_state(tmp_path: Path) -> tuple[str, str]:
    login, host = "51234567", "10.20.30.40"
    account = f"icm-demo-{login}"
    _snap(tmp_path, account, "2026-10-31 23:25", 1000.0)
    _snap(tmp_path, account, "2026-11-30 23:25", 1234.56)
    _trade(tmp_path, account, "2026-11-10 14:00", 100.0, 1)
    t = pd.Timestamp("2026-11-12 23:25", tz=UTC)
    rep = ledger.LedgerReport(account_id=account, ts_utc=t, window_from=t, currency="GBP", positions_checked=0,
                              matched=0, pending=0, status="fail", slot_utc=t,
                              note=f"terminal {login} at {host} answered for another account")
    with Database.open(tmp_path, "aux").write() as conn:
        ledger.REPORTS.upsert(conn, rep)
    return login, host


def test_message_has_no_account_identifiers(tmp_path):
    login, host = _identifying_state(tmp_path)
    sink = RecordingSink()
    rev = mr.run_monthly_review(settings=SETTINGS, state_dir=tmp_path, slot=pd.Timestamp("2026-12-01 06:30", tz=UTC),
                                account_ids=[f"icm-demo-{login}"], trials=FakeTrials(40), sink=sink)
    tg = mr.format_message(rev)
    [(label, title, body)] = sink.opened
    assert label == SETTINGS.monthly_review.issue_label == "review-monthly"
    notices = [json.loads(x) for x in (tmp_path / "agent_runs.jsonl").read_text().splitlines()]
    assert [n["status"] for n in notices] == ["notice"]
    for text in (tg, title, body, notices[0]["detail"]):
        assert login not in text and host not in text
    assert "1,234.56" in tg                                                    # the owner's own chat has the money
    assert "1,234.56" not in body and "1234.56" not in body                    # the public issue never has a balance
    assert "icm-demo-…67" in body                                             # the account is still named
    assert "UNRECONCILED" in tg and "UNRECONCILED" in body


def test_review_is_stored_in_aux_db_and_posted_once_per_month(tmp_path):
    _snap(tmp_path, ACC, "2026-10-31 23:25", 1000.0)
    _snap(tmp_path, ACC, "2026-11-30 23:25", 1100.0)
    _trade(tmp_path, ACC, "2026-11-10 14:00", 100.0, 1)
    with Database.open(tmp_path, "aux").write() as conn:
        conn.execute("INSERT INTO agent_spend (month, usd) VALUES ('2026-11', 8.0)")
    sink, trials = RecordingSink(), FakeTrials(12)
    kw: dict[str, Any] = dict(settings=SETTINGS, state_dir=tmp_path, slot=pd.Timestamp("2026-12-01 06:30", tz=UTC),
                              account_ids=[ACC], trials=trials, sink=sink)
    rev = mr.run_monthly_review(**kw)
    assert rev.status == "ok" and rev.posted == {"issue": "posted", "telegram": "queued"}
    assert rev.spend.agent_usd == 8.0
    assert rev.spend.total_gbp == round(8.0 * SETTINGS.monthly_review.gbp_per_usd
                                        + sum(SETTINGS.monthly_review.hosting_gbp_month.values()), 2)
    assert rev.trials.quarter == "2026Q4" and rev.trials.used == 12 and trials.asked == ["2026Q4"]
    stored = mr.load_review(tmp_path, "2026-11")
    assert stored is not None and stored.accounts[0].tie_out.status == "reconciled"
    mr.run_monthly_review(**kw)                          # a catch-up re-run neither re-opens nor re-sends
    assert len(sink.opened) == 1
    assert len((tmp_path / "agent_runs.jsonl").read_text().splitlines()) == 1


def test_without_a_token_the_issue_text_waits_in_the_outbox(tmp_path):
    _snap(tmp_path, ACC, "2026-10-31 23:25", 1000.0)
    rev = mr.run_monthly_review(settings=SETTINGS, state_dir=tmp_path, slot=pd.Timestamp("2026-12-01 06:30", tz=UTC),
                                account_ids=[ACC], trials=FakeTrials(0), sink=None)
    assert rev.posted["issue"] == "outbox"
    assert (tmp_path / "aux" / "monthly_review" / "outbox" / "2026-11.md").exists()


def test_reliability_counts_failing_and_stale_jobs_and_market_minutes_down(tmp_path):
    with Database.open(tmp_path, "aux").write() as conn:
        for name, st in {
            "backup": JobState(last_slot=pd.Timestamp("2026-11-30 22:15", tz=UTC), last_ok=False, failures=3),
            # broker_ledger last ran for 20 November: its slots since are long past their max_late_hours
            "broker_ledger": JobState(last_slot=pd.Timestamp("2026-11-20 23:25", tz=UTC), last_ok=True, runs=5),
            "attribution": JobState(last_slot=pd.Timestamp("2026-11-30 23:50", tz=UTC), last_ok=True, runs=30),
        }.items():
            conn.execute("INSERT INTO scheduler_jobs (name, body) VALUES (?, ?)", (name, st.model_dump_json()))
    log = AlertLog(tmp_path)
    # Wednesday 11 November 10:00-11:30 UTC down (90 market minutes); Saturday 14 November down 6 h (market closed)
    for ts, kind in (("2026-11-11 10:00", "fail"), ("2026-11-11 11:30", "ok"),
                     ("2026-11-14 08:00", "fail"), ("2026-11-14 14:00", "ok")):
        log.append(pd.Timestamp(ts, tz=UTC), channel="deadman", kinds=[kind], checks=["heartbeat"])
    log.append(pd.Timestamp("2026-11-11 10:05", tz=UTC), channel="telegram", kinds=["new"], checks=["engine:x"])
    rel = _review(tmp_path).reliability
    assert rel.failing == ["backup"] and "broker_ledger" in rel.stale and "attribution" not in rel.stale
    assert rel.failures_total == 3 and rel.telegram_alerts == 1 and rel.deadman_changes == 4
    assert rel.market_minutes_down == 90
