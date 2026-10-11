"""State store step 3 (docs/proposals/2026-10-state-store.md): the Telegram outbox, scheduler job state and the
staff-agent run log and spend ledger live in aux.db. Each old file is imported once and kept; the readers outside
this step (dashboard, health, the agents' read_state tool, owner notices) still see the data through the exports."""
import json
import subprocess
import sys
import threading
from pathlib import Path

import pandas as pd
import pytest

from goldbot.agents.roles import ROLES
from goldbot.agents.runner import AgentRunner, SpendLedger
from goldbot.agents.tools import ReadOnlyTools
from goldbot.data.store import Store
from goldbot.db import Database
from goldbot.db.bookkeeping import AgentRunLog
from goldbot.db.import_state import import_state
from goldbot.ops import health
from goldbot.ops.scheduler import Schedule, Scheduler
from goldbot.telegram.approvals import ApprovalCenter
from goldbot.telegram.bus import ApprovalBus
from goldbot.telegram.outbox import Outbox
from tests.test_agents import FakeClient, _resp, _text, _usage
from tests.test_approval_bus import _p
from tests.test_health import make_ctx

NOW = pd.Timestamp("2026-10-05 23:45", tz="UTC")
REPO = Path(__file__).resolve().parents[1]


def _rows(tmp: Path, sql: str) -> list[tuple]:
    with Database.open(tmp, "aux").connection() as conn:
        return conn.execute(sql).fetchall()


def _old_files(tmp: Path) -> dict[str, bytes]:
    """Files as the pre-database code wrote them."""
    runs = [{"role": "risk_officer", "status": "ok", "cost_usd": 0.1},
            {"role": "account_security", "status": "notice", "cost_usd": 0.0, "detail": "password reset"},
            {"role": "data_steward", "status": "ok", "cost_usd": 0.2}]
    files = {
        "agent_runs.jsonl": ("\n".join(json.dumps(r) for r in runs) + "\nnot json\n").encode(),
        "agent_spend.json": json.dumps({"2026-09": 12.5, "2026-10": 3.25}).encode(),
        "scheduler.json": json.dumps({"ts": "2026-10-05T23:00:00+00:00", "jobs": {"nightly_costs": {
            "last_slot": "2026-10-05T23:10:00+00:00", "last_ok": True, "runs": 7, "next_slot": "x"}}}).encode(),
        "telegram_outbox.json": json.dumps({"sent": {"p1": [[42, 7]]}, "reports_offset": 2}).encode(),
    }
    for name, data in files.items():
        (tmp / name).write_bytes(data)
    return files


# ----------------------------------------------------------------------------------------------- import once
def test_each_old_file_is_imported_once_and_its_original_is_kept(tmp_path):
    files = _old_files(tmp_path)
    clock = lambda: pd.Timestamp("2026-10-06 08:00", tz="UTC")            # noqa: E731
    sch = Scheduler(tmp_path / "scheduler.json", clock=clock)
    ledger = SpendLedger(tmp_path / "agent_spend.json", monthly_cap_usd=40.0)
    Outbox(tmp_path)
    assert sch.state["nightly_costs"].runs == 7 and sch.state["nightly_costs"].last_ok is True
    assert ledger.spent == {"2026-09": 12.5, "2026-10": 3.25} and ledger.month_spent(NOW) == 3.25
    assert _rows(tmp_path, "SELECT role, jsonl_line FROM agent_runs ORDER BY id") == [
        ("risk_officer", 0), ("account_security", 1), ("data_steward", 2)]   # the unreadable line is skipped
    assert _rows(tmp_path, "SELECT proposal_id FROM outbox_sent") == [("p1",)]
    # reopening (with this code's own exports on disk) imports nothing again and counts no fallback
    ledger.add(NOW, 1.0)
    Outbox(tmp_path).mark_sent("p2", [(42, 8)])
    sch.add("job", Schedule(kind="daily", at="09:00"), lambda slot: None)
    Scheduler(tmp_path / "scheduler.json", clock=clock)
    assert SpendLedger(tmp_path / "agent_spend.json", 40.0).month_spent(NOW) == 4.25
    assert Outbox(tmp_path).sent == {"p1": [(42, 7)], "p2": [(42, 8)]}
    assert len(_rows(tmp_path, "SELECT id FROM agent_runs")) == 3
    assert _rows(tmp_path, "SELECT * FROM state_fallback") == []
    assert {s for (s,) in _rows(tmp_path, "SELECT source FROM imports")} >= set(files)
    # the files the DB keeps regenerating are kept byte for byte under pre-db/
    for name in ("agent_spend.json", "scheduler.json", "telegram_outbox.json"):
        assert (tmp_path / "pre-db" / name).read_bytes() == files[name]
    assert (tmp_path / "agent_runs.jsonl").read_bytes().startswith(files["agent_runs.jsonl"])   # append-only


def test_import_state_cli_imports_the_bookkeeping_files_idempotently(tmp_path):
    _old_files(tmp_path)
    first = {r.source: r.status for r in import_state(tmp_path)}
    assert {k: first[k] for k in ("agent_runs.jsonl", "agent_spend.json", "scheduler.json", "telegram_outbox.json")} \
        == dict.fromkeys(("agent_runs.jsonl", "agent_spend.json", "scheduler.json", "telegram_outbox.json"), "imported")
    again = {r.source: r.status for r in import_state(tmp_path)}
    assert again["agent_runs.jsonl"] == again["scheduler.json"] == "skipped"
    assert len(_rows(tmp_path, "SELECT id FROM agent_runs")) == 3


def test_rows_written_before_the_file_appeared_are_never_duplicated_by_the_import(tmp_path):
    AgentRunLog(tmp_path).append(json.dumps({"role": "risk_officer", "status": "ok", "cost_usd": 0.1}))
    assert (tmp_path / "agent_runs.jsonl").exists()                        # the mirror line
    AgentRunLog(tmp_path)                                                   # reopen: the file is now "found"
    assert len(_rows(tmp_path, "SELECT id FROM agent_runs")) == 1


# ----------------------------------------------------------------------------------------------- spend cap
def test_concurrent_reservations_never_exceed_the_monthly_cap(tmp_path):
    ledger = SpendLedger(tmp_path / "agent_spend.json", monthly_cap_usd=5.0)
    granted: list[float] = []
    lock = threading.Lock()

    def take():
        g = SpendLedger(tmp_path / "agent_spend.json", monthly_cap_usd=5.0).reserve(NOW, 1.0)
        with lock:
            granted.append(g)
    threads = [threading.Thread(target=take) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(granted) == pytest.approx(5.0) and ledger.month_spent(NOW) == pytest.approx(5.0)
    assert json.loads((tmp_path / "agent_spend.json").read_text()) == {"2026-10": 5.0}   # health's export


def test_concurrent_processes_never_spend_past_the_cap(tmp_path):
    code = ("import sys, pandas as pd; from goldbot.agents.runner import SpendLedger; "
            "print(SpendLedger(sys.argv[1], 3.0).reserve(pd.Timestamp('2026-10-05', tz='UTC'), 1.0))")
    SpendLedger(tmp_path / "agent_spend.json", 3.0)                         # migrate once up front
    procs = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "agent_spend.json")], cwd=REPO,
                              stdout=subprocess.PIPE, text=True) for _ in range(8)]
    outs = [float(p.communicate(timeout=120)[0]) for p in procs]
    assert all(p.returncode == 0 for p in procs)
    assert sum(outs) == pytest.approx(3.0) and sorted(outs)[-3:] == [1.0, 1.0, 1.0]
    assert SpendLedger(tmp_path / "agent_spend.json", 3.0).month_spent(NOW) == pytest.approx(3.0)


def test_a_run_reserves_its_budget_and_books_only_what_it_spent(tmp_path):
    ledger = SpendLedger(tmp_path / "agent_spend.json", monthly_cap_usd=40.0)
    tools = ReadOnlyTools(tmp_path, Store(tmp_path / "data"), now=lambda: NOW)
    seen: list[float] = []

    class Peek(FakeClient):
        def _create(self, **kw):
            seen.append(ledger.month_spent(NOW))                           # mid-run: the whole budget is reserved
            return super()._create(**kw)
    run = AgentRunner(Peek([_resp([_text("ok")], usage=_usage(inp=10_000, out=1_000))]), tools, ledger,
                      tmp_path).run(ROLES["risk_officer"], NOW)
    assert seen == [ROLES["risk_officer"].max_cost_usd]
    assert ledger.month_spent(NOW) == pytest.approx(run.cost_usd) and run.cost_usd > 0


def test_a_run_that_cannot_get_five_cents_releases_its_reservation(tmp_path):
    ledger = SpendLedger(tmp_path / "agent_spend.json", monthly_cap_usd=5.0)
    ledger.add(NOW, 4.97)
    tools = ReadOnlyTools(tmp_path, Store(tmp_path / "data"), now=lambda: NOW)
    run = AgentRunner(FakeClient([]), tools, ledger, tmp_path).run(ROLES["risk_officer"], NOW)
    assert run.status == "monthly_cap" and ledger.month_spent(NOW) == pytest.approx(4.97)


# ----------------------------------------------------------------------------------------------- outbox
def test_outbox_sends_each_proposal_once_across_restarts_and_reports_each_outcome_once(tmp_path):
    engine = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))
    engine.propose(_p("a"))
    Outbox(tmp_path).mark_sent("a", [(42, 1)])
    assert Outbox(tmp_path).unsent_proposals() == []                       # restart: not sent again
    ApprovalBus(tmp_path).submit("a", True, "", by="dashboard:o@x.io")
    engine.poll_bus()
    one, two = Outbox(tmp_path), Outbox(tmp_path)                           # two instances see the same row
    assert one.finished() + two.finished() == [("a", "APPROVED", [(42, 1)])]
    assert Outbox(tmp_path).finished() == []


def test_outbox_relays_each_run_and_notice_exactly_once_across_restarts(tmp_path):
    runs = AgentRunLog(tmp_path)
    runs.append(json.dumps({"role": "risk_officer", "status": "ok", "cost_usd": 0.1}))
    assert Outbox(tmp_path).new_reports() == []                             # first start: no replay
    runs.append(json.dumps({"role": "data_steward", "status": "ok", "cost_usd": 0.05}))
    with (tmp_path / "agent_runs.jsonl").open("a") as fh:                   # what api/auth.py writes
        fh.write(json.dumps({"role": "account_security", "status": "notice", "cost_usd": 0.0, "detail": "reset"}) + "\n")
        fh.write('{"role": "half written')                                 # a line still being written
    msgs = Outbox(tmp_path).new_reports()
    assert len(msgs) == 2 and "data steward" in msgs[0] and "reset" in msgs[1]
    assert Outbox(tmp_path).new_reports() == []                             # restart: nothing again
    with (tmp_path / "agent_runs.jsonl").open("a") as fh:
        fh.write('", "status": "notice", "detail": "late"}\n')
    assert ["late" in m for m in Outbox(tmp_path).new_reports()] == [True]  # the finished line is not lost


def test_old_outbox_progress_neither_replays_nor_drops_reports(tmp_path):
    _old_files(tmp_path)                       # 2 of the 3 log lines handled: the third (data_steward) is still due
    msgs = Outbox(tmp_path).new_reports()
    assert len(msgs) == 1 and msgs[0].startswith("📋 data steward")
    assert Outbox(tmp_path).new_reports() == []


# ----------------------------------------------------------------------------------------------- scheduler
def test_scheduler_state_survives_a_restart_without_its_json_file(tmp_path):
    t = {"now": pd.Timestamp("2026-10-06 08:00", tz="UTC")}
    calls: list[str] = []

    def job(slot: pd.Timestamp) -> dict:
        calls.append(str(slot))
        return {"n": 1}
    sch = Scheduler(tmp_path / "scheduler.json", clock=lambda: t["now"])
    sch.add("job", Schedule(kind="daily", at="09:00"), job)
    t["now"] = pd.Timestamp("2026-10-06 09:01", tz="UTC")
    assert sch.run_pending() == ["job"]
    (tmp_path / "scheduler.json").unlink()                                  # only the export is lost
    again = Scheduler(tmp_path / "scheduler.json", clock=lambda: t["now"])
    again.add("job", Schedule(kind="daily", at="09:00"), job)
    assert again.run_pending() == [] and len(calls) == 1                    # the slot is not run twice
    assert again.state["job"].runs == 1 and again.state["job"].last_result == {"n": 1}
    assert json.loads((tmp_path / "scheduler.json").read_text())["jobs"]["job"]["runs"] == 1   # export rebuilt
    assert _rows(tmp_path, "SELECT COUNT(*) FROM scheduler_heartbeat") == [(1,)]


# ----------------------------------------------------------------------------------------------- readers
def test_every_reader_outside_the_step_still_sees_the_data(tmp_path):
    from goldbot.api.app import State
    _old_files(tmp_path)
    clock = lambda: NOW                                                     # noqa: E731
    sch = Scheduler(tmp_path / "scheduler.json", clock=clock)
    sch.add("nightly_costs", Schedule(kind="daily", at="23:10"), lambda slot: None)
    ledger = SpendLedger(tmp_path / "agent_spend.json", monthly_cap_usd=40.0)
    ledger.add(NOW, 1.0)
    AgentRunLog(tmp_path).append(json.dumps({"role": "journal_coach", "status": "ok", "cost_usd": 0.3}))
    # dashboard (api/app.py): /api/jobs and /api/agent-runs
    st = State(tmp_path)
    assert st.scheduler()["jobs"]["nightly_costs"]["runs"] == 7
    assert st.agent_runs()[0]["role"] == "journal_coach"                   # newest first
    # health (ops/health.py): scheduler heartbeat and monthly spend
    ctx = make_ctx(tmp_path, now=NOW)
    assert health.check_scheduler(ctx)[0].status == "ok"
    assert "$4.25 of $40.00" in health.check_agent_spend(ctx).reason
    # the agents' read_state tool
    out, err = ReadOnlyTools(tmp_path, Store(tmp_path / "data"), now=clock).call(
        "read_state", {"name": "scheduler"}, ["read_state"])
    assert not err and json.loads(out)["jobs"]["nightly_costs"]["runs"] == 7
    # the run log repository itself
    assert [r["role"] for r in AgentRunLog(tmp_path).recent(2)] == ["data_steward", "journal_coach"]


# ----------------------------------------------------------------------------------------------- rollback
def test_roll_forward_merges_what_a_rolled_back_release_wrote_once(tmp_path):
    _old_files(tmp_path)
    clock = lambda: pd.Timestamp("2026-10-06 08:00", tz="UTC")            # noqa: E731
    Scheduler(tmp_path / "scheduler.json", clock=clock)
    SpendLedger(tmp_path / "agent_spend.json", 40.0)
    Outbox(tmp_path).new_reports()
    # the older release runs for a while: it rewrites its files and appends a run line
    (tmp_path / "agent_spend.json").write_text(json.dumps({"2026-09": 1.0, "2026-10": 9.5}))
    (tmp_path / "scheduler.json").write_text(json.dumps({"ts": "2026-10-07T00:00:00+00:00", "jobs": {"nightly_costs": {
        "last_slot": "2026-10-06T23:10:00+00:00", "last_ok": True, "runs": 8}}}))
    with (tmp_path / "agent_runs.jsonl").open("a") as fh:
        fh.write(json.dumps({"role": "journal_coach", "status": "ok", "cost_usd": 0.4}) + "\n")
    (tmp_path / "telegram_outbox.json").write_text(json.dumps({"sent": {"p9": [[42, 9]]}, "reports_offset": 5}))
    # roll forward
    ledger = SpendLedger(tmp_path / "agent_spend.json", 40.0)
    sch = Scheduler(tmp_path / "scheduler.json", clock=clock)
    box = Outbox(tmp_path)
    assert ledger.spent == {"2026-09": 12.5, "2026-10": 9.5}                # the larger amount per month
    assert sch.state["nightly_costs"].runs == 8                             # the newer slot wins
    assert box.sent == {"p9": [(42, 9)]}                                    # that release's sends and edits
    assert box.new_reports() == []                                          # it relayed the journal coach run
    assert [r["role"] for r in AgentRunLog(tmp_path).recent(1)] == ["journal_coach"]   # adopted into the table
    hits = dict(_rows(tmp_path, "SELECT source, hits FROM state_fallback"))
    assert hits == {"agent_spend.json": 1, "scheduler.json": 1, "telegram_outbox.json": 1, "agent_runs.jsonl": 1}
    SpendLedger(tmp_path / "agent_spend.json", 40.0)                        # merged once, not on every open
    Outbox(tmp_path)
    assert dict(_rows(tmp_path, "SELECT source, hits FROM state_fallback")) == hits


def test_the_old_format_outbox_file_lets_a_rolled_back_release_resume_without_replay(tmp_path):
    from goldbot.telegram.outbox import OutboxState
    runs = AgentRunLog(tmp_path)
    runs.append(json.dumps({"role": "risk_officer", "status": "ok", "cost_usd": 0.1}))
    box = Outbox(tmp_path)
    assert box.new_reports() == []
    box.mark_sent("p1", [(42, 1)])
    runs.append(json.dumps({"role": "data_steward", "status": "ok", "cost_usd": 0.05}))
    assert len(box.new_reports()) == 1
    runs.append(json.dumps({"role": "journal_coach", "status": "ok", "cost_usd": 0.3}))    # not relayed yet
    old = OutboxState.model_validate_json((tmp_path / "telegram_outbox.json").read_text())   # the old code's model
    assert old.sent == {"p1": [(42, 1)]}
    lines = (tmp_path / "agent_runs.jsonl").read_text().splitlines()
    assert [json.loads(x)["role"] for x in lines[old.reports_offset:]] == ["journal_coach"]


def test_an_additive_schema_from_a_newer_release_still_opens(tmp_path):
    from goldbot.db import MIGRATIONS, Migration, check_version, migrate
    from goldbot.db.migrations import file_compat
    newer = MIGRATIONS["aux"] + (Migration(version=len(MIGRATIONS["aux"]) + 1, name="future_table", additive=True,
                                           statements=("CREATE TABLE future (x INTEGER) STRICT",)),)
    db = Database.open(tmp_path, "aux")
    with db.connection() as conn:
        migrate(conn, "aux", migrations=newer)
        assert file_compat(conn) == 4                                       # 5 (bookkeeping) is additive too
        check_version(conn, "aux")                                          # this release's code accepts it
    Database.open(tmp_path, "aux")                                          # and opens it (rollback)
    SpendLedger(tmp_path / "agent_spend.json", 40.0).add(NOW, 1.0)


# ----------------------------------------------------------------------------------------------- export failures
def _broken_export(monkeypatch):
    import goldbot.db.bookkeeping as bk

    def refuse(path, text):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")
    monkeypatch.setattr(bk, "export_json", refuse)


def test_a_failed_spend_export_never_undoes_the_booking(tmp_path, monkeypatch):
    ledger = SpendLedger(tmp_path / "agent_spend.json", monthly_cap_usd=5.0)
    _broken_export(monkeypatch)
    ledger.add(NOW, 1.25)                                                   # the headline scorer's spend
    assert ledger.reserve(NOW, 1.0) == 1.0
    ledger.settle(NOW, 1.0, 1.5)                                            # an overrun of the last turn
    assert SpendLedger(tmp_path / "agent_spend.json", 5.0).month_spent(NOW) == pytest.approx(2.75)
    monkeypatch.undo()
    ledger.add(NOW, 0.25)                                                   # the next write exports everything
    assert json.loads((tmp_path / "agent_spend.json").read_text()) == {"2026-10": 3.0}


def test_a_failed_scheduler_export_still_runs_the_job_once(tmp_path, monkeypatch):
    t = {"now": pd.Timestamp("2026-10-06 08:00", tz="UTC")}
    calls: list[str] = []

    def job(slot: pd.Timestamp) -> None:
        calls.append(str(slot))
    sch = Scheduler(tmp_path / "scheduler.json", clock=lambda: t["now"])
    sch.add("job", Schedule(kind="daily", at="09:00"), job)
    _broken_export(monkeypatch)
    t["now"] = pd.Timestamp("2026-10-06 09:01", tz="UTC")
    assert sch.run_pending() == ["job"] and len(calls) == 1                # export fails after the "started" commit
    from goldbot.db.bookkeeping import scheduler_status
    doc = scheduler_status(tmp_path)
    assert doc is not None and doc["jobs"]["job"]["runs"] == 1             # health and vps_update read this
    again = Scheduler(tmp_path / "scheduler.json", clock=lambda: t["now"])
    again.add("job", Schedule(kind="daily", at="09:00"), job)
    assert again.run_pending() == [] and len(calls) == 1


def test_health_and_the_updater_read_the_scheduler_heartbeat_from_the_database(tmp_path, capsys):
    from goldbot.db.__main__ import main as db_cli
    t = {"now": NOW}
    sch = Scheduler(tmp_path / "scheduler.json", clock=lambda: t["now"])
    sch.add("nightly_costs", Schedule(kind="daily", at="23:10"), lambda slot: None)
    (tmp_path / "scheduler.json").write_text(json.dumps({"ts": "2020-01-01T00:00:00+00:00", "jobs": {}}))  # stale
    assert health.check_scheduler(make_ctx(tmp_path, now=NOW))[0].status == "ok"
    assert db_cli(["scheduler-status", "--state-dir", str(tmp_path)]) == 0
    assert "nightly_costs" in json.loads(capsys.readouterr().out)["jobs"]
    assert db_cli(["scheduler-status", "--state-dir", str(tmp_path / "none")]) == 1


# ----------------------------------------------------------------------------------------------- delivery
def test_reports_are_redelivered_until_acknowledged(tmp_path):
    runs = AgentRunLog(tmp_path)
    box = Outbox(tmp_path)
    assert box.pending_reports() == ([], None)
    runs.append(json.dumps({"role": "data_steward", "status": "ok", "cost_usd": 0.05}))
    msgs, ack = box.pending_reports()                                       # the send then fails: no ack
    assert len(msgs) == 1 and ack is not None
    again, ack2 = Outbox(tmp_path).pending_reports()                        # retried, also after a restart
    assert again == msgs
    assert ack2 is not None
    box.ack_reports(ack2)
    assert Outbox(tmp_path).pending_reports() == ([], None)


def test_a_sent_proposal_whose_mark_fails_is_not_sent_again_and_the_mark_is_retried(tmp_path):
    from goldbot.telegram.outbox import SendGuard

    class Flaky(Outbox):
        fails = 2

        def mark_sent(self, proposal_id, messages):
            if self.fails:
                self.fails -= 1
                raise RuntimeError("database is locked")
            super().mark_sent(proposal_id, messages)
    ApprovalCenter({1}, bus=ApprovalBus(tmp_path)).propose(_p("a"))
    box, guard = Flaky(tmp_path), SendGuard(first_s=2.0, max_s=60.0)
    assert [p.proposal_id for p in box.unsent_proposals()] == ["a"]
    assert guard.record(box, "a", [(42, 1)], now=0.0) is False
    assert not guard.should_send("a")                                       # the pump skips it: no second message
    guard.retry(box, now=1.0)
    assert box.fails == 1                                                    # not due yet (backoff 2 s)
    guard.retry(box, now=2.0)                                               # fails again: backoff doubles to 4 s
    assert guard.unmarked["a"][2] == 4.0
    guard.retry(box, now=6.0)
    assert guard.should_send("a") and box.unsent_proposals() == [] and box.sent == {"a": [(42, 1)]}


# ----------------------------------------------------------------------------------------------- runner failures
def test_a_run_that_raises_mid_loop_keeps_its_reservation(tmp_path):
    ledger = SpendLedger(tmp_path / "agent_spend.json", monthly_cap_usd=40.0)
    tools = ReadOnlyTools(tmp_path, Store(tmp_path / "data"), now=lambda: NOW)
    class Dies(FakeClient):
        def _create(self, **kw):
            raise KeyboardInterrupt                                         # escapes the loop's own error handling
    with pytest.raises(KeyboardInterrupt):
        AgentRunner(Dies([]), tools, ledger, tmp_path).run(ROLES["risk_officer"], NOW)
    assert ledger.month_spent(NOW) == ROLES["risk_officer"].max_cost_usd     # unknown spend counts as spent


def test_a_failed_settle_still_logs_the_run(tmp_path, monkeypatch):
    ledger = SpendLedger(tmp_path / "agent_spend.json", monthly_cap_usd=40.0)
    tools = ReadOnlyTools(tmp_path, Store(tmp_path / "data"), now=lambda: NOW)

    def broken(now, reserved, actual):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(ledger, "settle", broken)
    run = AgentRunner(FakeClient([_resp([_text("all quiet")])]), tools, ledger, tmp_path).run(ROLES["risk_officer"], NOW)
    assert run.status == "ok" and run.report_path is not None
    assert [r["role"] for r in AgentRunLog(tmp_path).recent(1)] == ["risk_officer"]
    assert ledger.month_spent(NOW) == ROLES["risk_officer"].max_cost_usd     # the reservation stays booked


def test_headline_spend_racing_agent_reservations_is_never_lost(tmp_path):
    SpendLedger(tmp_path / "agent_spend.json", 10.0)
    granted: list[float] = []
    lock = threading.Lock()

    def scorer():
        led = SpendLedger(tmp_path / "agent_spend.json", 10.0)
        for _ in range(10):
            led.add(NOW, 0.1)                                               # news_collector's ledger.add

    def agent():
        g = SpendLedger(tmp_path / "agent_spend.json", 10.0).reserve(NOW, 1.0)
        with lock:
            granted.append(g)
    threads = [threading.Thread(target=scorer) for _ in range(3)] + [threading.Thread(target=agent) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = SpendLedger(tmp_path / "agent_spend.json", 10.0).month_spent(NOW)
    assert total == pytest.approx(3.0 + sum(granted))                       # no booking lost to a race
    assert sum(granted) <= 10.0 and total <= 10.0 + 3.0


def test_the_runbook_downgrade_keeps_rows_and_the_next_update_reapplies_version_5(tmp_path):
    import sqlite3
    SpendLedger(tmp_path / "agent_spend.json", 40.0).add(NOW, 2.0)
    c = sqlite3.connect(tmp_path / "aux.db")                                # the RUNBOOK one-liner
    c.execute("DELETE FROM schema_version WHERE version = 5")
    c.execute("PRAGMA user_version = 4")
    c.commit()
    c.close()
    assert SpendLedger(tmp_path / "agent_spend.json", 40.0).month_spent(NOW) == 2.0
    assert _rows(tmp_path, "SELECT MAX(version) FROM schema_version") == [(5,)]


# ----------------------------------------------------------------------------------------------- re-verification
def test_heartbeat_reads_are_never_torn_while_the_scheduler_writes(tmp_path):
    from goldbot.db.bookkeeping import save_jobs, scheduler_heartbeat
    db = Database.open(tmp_path, "aux")
    stop = threading.Event()

    def writer():
        n = 1
        while not stop.is_set():
            with db.write() as conn:                                        # one connection per write, as the scheduler
                save_jobs(conn, {}, heartbeat_ns=n, status=json.dumps({"n": n, "jobs": {}}))
            n += 1
    t = threading.Thread(target=writer)
    t.start()
    try:
        seen = 0
        for _ in range(1500):
            hb = scheduler_heartbeat(tmp_path)
            if hb is not None:
                assert hb[0] == hb[1]["n"]                                  # ts_ns and body from the same commit
                seen += 1
    finally:
        stop.set()
        t.join()
    assert seen > 0


def test_our_own_export_is_never_taken_for_a_foreign_write(tmp_path, monkeypatch):
    import goldbot.db.bookkeeping as bk
    ledger = SpendLedger(tmp_path / "agent_spend.json", 40.0)
    real = bk.export_json

    def write_then_die(path, text):
        real(path, text)
        raise OSError("killed after the rename")                            # before anything else is recorded
    monkeypatch.setattr(bk, "export_json", write_then_die)
    ledger.add(NOW, 1.0)
    monkeypatch.undo()
    workers = [threading.Thread(target=lambda: SpendLedger(tmp_path / "agent_spend.json", 40.0).add(NOW, 0.5))
               for _ in range(6)]                                           # concurrent exporters
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert SpendLedger(tmp_path / "agent_spend.json", 40.0).month_spent(NOW) == pytest.approx(4.0)
    assert _rows(tmp_path, "SELECT * FROM state_fallback") == []


def test_the_outbox_merge_is_a_union_that_keeps_pending_proposals_and_the_furthest_cursor(tmp_path):
    center = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))
    center.propose(_p("pending"))
    runs = AgentRunLog(tmp_path)
    for i in range(4):
        runs.append(json.dumps({"role": f"r{i}", "status": "ok", "cost_usd": 0.0}))
    box = Outbox(tmp_path)
    box.new_reports()                                                       # cursor at the end of the log
    box.mark_sent("pending", [(42, 1)])
    box.mark_sent("finished", [(42, 2)])                                    # not on the bus any more
    (tmp_path / "telegram_outbox.json").write_text(json.dumps({"sent": {"p9": [[42, 9]]}, "reports_offset": 1}))
    merged = Outbox(tmp_path)
    assert merged.sent == {"pending": [(42, 1)], "p9": [(42, 9)]}
    assert merged.new_reports() == []                                       # the older offset does not replay


def test_a_report_batch_failing_for_one_recipient_is_acknowledged_after_a_few_passes():
    from goldbot.telegram.outbox import ReportRetry, ReportsAck
    retry = ReportRetry(attempts=3)
    ack = ReportsAck(reports_id=5, notices_line=9)
    assert [retry.should_ack(ack, delivered=False) for _ in range(3)] == [False, False, True]
    assert retry.should_ack(ack, delivered=False) is False                  # a new streak starts
    assert retry.should_ack(ack, delivered=True) is True


def _downgrade_script() -> str:
    text = (REPO / "goldbot" / "ops" / "vps_update.ps1").read_text()
    return text[text.index("# BEGIN aux-downgrade"):text.index("# END aux-downgrade")]


@pytest.mark.parametrize("version, code", [(5, 0), (4, 0), (6, 3)])
def test_the_updater_steps_aux_db_back_only_from_schema_5(tmp_path, version, code):
    import sqlite3
    SpendLedger(tmp_path / "agent_spend.json", 40.0).add(NOW, 2.0)          # a schema 5 file with a row
    with sqlite3.connect(tmp_path / "aux.db") as c:
        if version == 4:
            c.execute("DELETE FROM schema_version WHERE version = 5")
        if version == 6:
            c.execute("INSERT INTO schema_version (version, name, applied_ns) VALUES (6, 'future', 0)")
    script = tmp_path / "down.py"
    script.write_text(_downgrade_script())
    out = subprocess.run([sys.executable, str(script), str(tmp_path / "aux.db")], capture_output=True, text=True)
    assert out.returncode == code
    with sqlite3.connect(tmp_path / "aux.db") as c:                         # read without migrating
        assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == (6 if version == 6 else 4)
        assert c.execute("SELECT usd FROM agent_spend").fetchone()[0] == 2.0                  # rows kept


def test_the_updater_stops_the_scheduler_and_checks_again_before_stopping_the_rest():
    text = (REPO / "goldbot" / "ops" / "vps_update.ps1").read_text()
    step3 = text[text.index("# ---- 3."):text.index("# ---- 5.")]
    stop_sched, recheck, rest = (step3.index("nssm stop goldbot-scheduler"), step3.rindex("Get-RunningJobs"),
                                 step3.index("Stop-Goldbot"))
    assert stop_sched < recheck < rest


def test_an_export_landing_during_the_foreign_check_is_not_taken_for_a_foreign_write(tmp_path, monkeypatch):
    """The race seen under load: a ledger opening (dual_read) while another process exports. The check must never
    pair the hashes known before an export with the file written by it."""
    import goldbot.db.bookkeeping as bk
    SpendLedger(tmp_path / "agent_spend.json", 40.0).add(NOW, 1.0)
    main = threading.current_thread()
    go, done = threading.Event(), threading.Event()
    real = bk.file_sha256

    def exporter():
        go.wait(10)
        SpendLedger(tmp_path / "agent_spend.json", 40.0).add(NOW, 0.5)     # records its hash and rewrites the file
        done.set()
    t = threading.Thread(target=exporter)
    t.start()

    def interleave(path):
        if threading.current_thread() is main and not go.is_set():
            go.set()
            done.wait(1.0)                    # lets the export run here, if the check does not hold it off
        return real(path)
    monkeypatch.setattr(bk, "file_sha256", interleave)
    SpendLedger(tmp_path / "agent_spend.json", 40.0)
    t.join(10)
    monkeypatch.undo()
    assert _rows(tmp_path, "SELECT * FROM state_fallback") == []
    assert SpendLedger(tmp_path / "agent_spend.json", 40.0).month_spent(NOW) == pytest.approx(1.5)
    assert json.loads((tmp_path / "agent_spend.json").read_text()) == {"2026-10": 1.5}     # not a stale snapshot


def test_an_export_killed_before_its_commit_is_still_recognised_as_ours(tmp_path):
    import sqlite3
    SpendLedger(tmp_path / "agent_spend.json", 40.0).add(NOW, 1.0)
    with sqlite3.connect(tmp_path / "aux.db") as c:                         # the hash record never committed
        c.execute("UPDATE exports SET hashes = '[]' WHERE source = 'agent_spend.json'")
    assert SpendLedger(tmp_path / "agent_spend.json", 40.0).month_spent(NOW) == 1.0
    assert _rows(tmp_path, "SELECT * FROM state_fallback") == []           # it equals what the DB renders
