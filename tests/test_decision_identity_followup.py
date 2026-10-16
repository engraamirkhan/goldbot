"""Security follow-ups to BACKLOG 52 (A15). Who decided or halted is the owner's audit trail, never a state file or an
API answer: the decision file and control.json keep only the channel ("dashboard" | "telegram"); `/api/status`,
the entry status and the health check report the channel to every role; the Telegram service writes the owner's
Telegram id to the VPS audit log (state/aux.db, the table the dashboard audits into) for each decision and halt; an
engine start sweeps decision files left behind (no pending file, or older than a day)."""
import asyncio
import json
import os
import re
import time
import types

import pytest
from fastapi.testclient import TestClient

from goldbot.api.app import create_app
from goldbot.api.auth import AuthStore
from goldbot.ops.health import check_halt
from goldbot.telegram import automode
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal
from goldbot.telegram.bus import DECISION_MAX_AGE_S, ApprovalBus
from tests.test_api_owner_controls import _owner_secret
from tests.test_api_security_followup import _viewer
from tests.test_health import make_ctx

TELEGRAM_ID = 987654321
EMAIL = "owner-secret@example.org"


def _prop(pid: str, **kw) -> Proposal:
    return Proposal(proposal_id=pid, account_id="icm-demo", agent_id="a", side=1, lots=0.1, entry=2000.0, stop=1998.0,
                    target=2004.0, p=0.6, ev_r=0.2, spread_points=20.0, top_features=[], **kw)


def _assert_no_identity(text: str) -> None:
    assert str(TELEGRAM_ID) not in text and EMAIL not in text


# ------------------------------------------------------------------------------------- the decision file
@pytest.mark.parametrize("by", [f"dashboard:{EMAIL}", f"telegram:{TELEGRAM_ID}"])
def test_a_submitted_decision_file_keeps_only_the_channel_even_when_given_an_identity(tmp_path, by):
    bus = ApprovalBus(tmp_path)
    bus.publish(_prop("p1"))
    d = bus.submit("p1", True, None, by=by)
    text = (bus.decisions_dir / "p1.json").read_text()
    _assert_no_identity(text)
    assert "@" not in text and json.loads(text)["by"] == by.split(":")[0] == d.by


def test_an_older_decision_file_with_an_identity_is_still_applied_by_its_channel(tmp_path):
    bus = ApprovalBus(tmp_path)
    center = ApprovalCenter({TELEGRAM_ID}, bus=bus)
    center.propose(_prop("p1"))
    (bus.decisions_dir / "p1.json").write_text(json.dumps({"proposal_id": "p1", "approve": True, "by":
                                                           f"telegram:{TELEGRAM_ID}", "ts": time.time()}))
    (p,) = center.poll_bus()
    assert p.outcome == Outcome.APPROVED and p.decided_channel == "telegram"
    _assert_no_identity((bus.done_dir / "p1.json").read_text())


# ------------------------------------------------------------------------------------- sweep of leftover decisions
def _decision(bus: ApprovalBus, pid: str, *, age_s: float = 0.0, pending: bool = True) -> None:
    if pending:
        bus.publish(_prop(pid))
    f = bus.decisions_dir / f"{pid}.json"
    f.write_text(json.dumps({"proposal_id": pid, "approve": True, "by": f"dashboard:{EMAIL}", "ts": time.time()}))
    if age_s:
        then = time.time() - age_s
        os.utime(f, (then, then))


def test_the_sweep_deletes_decisions_without_a_pending_file_or_older_than_a_day_and_keeps_live_ones(tmp_path):
    bus = ApprovalBus(tmp_path)
    _decision(bus, "orphan", pending=False)                              # archived, its decision left behind
    _decision(bus, "old", age_s=DECISION_MAX_AGE_S + 60)                  # long expired, the engine never came back
    _decision(bus, "live")                                                # waiting for the engine's next tick
    (bus.decisions_dir / "torn.json").write_text("{")                     # unreadable and orphaned: swept by name
    assert bus.sweep_decisions() == 3
    assert sorted(f.name for f in bus.decisions_dir.glob("*.json")) == ["live.json"]
    assert (bus.pending_dir / "old.json").exists()          # the sweep touches decisions only; the engine archives


def test_an_engine_start_sweeps_leftover_decision_files(tmp_path):
    from tests.test_cov_engine import _engine
    bus = ApprovalBus(tmp_path)
    _decision(bus, "orphan", pending=False)
    _engine(tmp_path, bus=True)
    assert not (bus.decisions_dir / "orphan.json").exists()


def test_a_failing_sweep_never_stops_an_engine_start(tmp_path, monkeypatch):
    from tests.test_cov_engine import _engine

    def broken(self, max_age_s=DECISION_MAX_AGE_S):
        raise OSError("disk")
    monkeypatch.setattr(ApprovalBus, "sweep_decisions", broken)
    eng, _, _ = _engine(tmp_path, bus=True)
    assert eng is not None


# ------------------------------------------------------------------------------------- control.json and the halt
def test_halt_and_rearm_keep_only_the_channel_in_control_json(tmp_path):
    bus = ApprovalBus(tmp_path)
    bus.set_halt(True, by=f"telegram:{TELEGRAM_ID}", reason="fomc")
    _assert_no_identity(bus.control_path.read_text())
    assert bus.control().by == "telegram"
    bus.owner_rearm(by=f"dashboard:{EMAIL}")
    _assert_no_identity(bus.control_path.read_text())
    assert (bus.control().by, bus.control().rearm_by) == ("dashboard", "dashboard")


def _legacy_control(state, **kw) -> None:
    """control.json as a release before A15 wrote it: who halted and who re-armed, by identity."""
    (state / "control.json").write_text(json.dumps({"halted": True, "by": f"telegram:{TELEGRAM_ID}", "ts": time.time(),
                                                    "reason": "fomc", "rearm_id": "r1",
                                                    "rearm_by": f"dashboard:{EMAIL}", "rearm_ts": time.time(), **kw}))


def test_the_halt_health_check_names_the_channel_not_who(tmp_path):
    _legacy_control(tmp_path)
    c = check_halt(make_ctx(tmp_path))
    _assert_no_identity(c.reason)
    assert c.status == "warn" and "HALTED via telegram" in c.reason and "re-arm via dashboard" in c.reason


@pytest.fixture
def app_client(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", owner_email="o@x.io")
    return app, TestClient(app), state


@pytest.mark.integration
def test_a_viewer_reads_who_halted_as_a_channel_with_no_email_or_digits(app_client):
    app, c, state = app_client
    owner, _ = _owner_secret(c, app)
    viewer = _viewer(c, owner)
    (state / "engine_icm.json").write_text(json.dumps({
        "account": "icm-demo", "broker": "icm", "mode": "demo", "equity": 9900, "day_start_equity": 10000,
        "week_start_equity": 10000, "balance_closed_hwm": 10000, "stage": "normal", "account_class": "raw",
        "ts": time.time()}))
    _legacy_control(state, mode_by=f"telegram:{TELEGRAM_ID}", approval_mode="propose", mode_ts=time.time())
    halted_by = c.get("/api/status", headers=viewer).json()["halted_by"]
    assert halted_by == "telegram" and "@" not in halted_by and not re.search(r"\d", halted_by)
    for path in ("/api/status", "/api/accounts", "/api/health", "/api/automode", "/api/proposals/recent"):
        r = c.get(path, headers=viewer)
        assert r.status_code == 200, path
        _assert_no_identity(r.text)
    assert c.post("/api/halt", json={"reason": "cpi"}, headers=owner).json()["halted_by"] == "dashboard"
    assert c.get("/api/status", headers=viewer).json()["halted_by"] == "dashboard"
    assert "o@x.io" not in (state / "control.json").read_text()
    assert any(e["event"] == "halt" and e["by"] == "o@x.io" for e in app.state.st.auth.audit_events())


@pytest.mark.integration
def test_a_dashboard_decision_file_holds_no_email_and_the_audit_log_names_the_approver(app_client):
    app, c, state = app_client
    owner, _ = _owner_secret(c, app)
    bus = ApprovalBus(state)
    bus.publish(_prop("p1"))
    assert c.post("/api/decisions", json={"proposal_id": "p1", "action": "approve"}, headers=owner).status_code == 200
    text = (bus.decisions_dir / "p1.json").read_text()
    assert "@" not in text and json.loads(text)["by"] == "dashboard"
    assert any(e["event"] == "decision" and e["by"] == "o@x.io" and e["proposal"] == "p1"
               for e in app.state.st.auth.audit_events())


@pytest.mark.integration
def test_no_api_route_serves_the_audit_log(app_client):
    """The audit log (who decided, halted, re-armed, signed in) is read only on the VPS: no route exposes it."""
    app, _, _ = app_client
    paths = {getattr(r, "path", "") for r in app.routes}
    assert not [p for p in paths if "audit" in p]


# ------------------------------------------------------------------------------------- the Telegram service
def _bot(state, opener=None):
    from goldbot.telegram.bot import OffLoopAudit, TelegramBot
    bot = TelegramBot.__new__(TelegramBot)                    # no network: only the handlers' state
    bot.bus, bot.owner_ids, bot.state_dir, bot.deploy = ApprovalBus(state), {TELEGRAM_ID}, str(state), None
    bot.audit = OffLoopAudit(str(state), opener=opener)
    return bot


def _query(data: str, log: list[str]) -> types.SimpleNamespace:
    async def answer(text: str = "", show_alert: bool = False) -> None:
        log.append(text)

    async def edit_message_text(text: str) -> None:
        log.append(text)
    return types.SimpleNamespace(from_user=types.SimpleNamespace(id=TELEGRAM_ID), data=data, answer=answer,
                                 edit_message_text=edit_message_text, message=types.SimpleNamespace(text="card"))


def _click(bot, data: str) -> list[str]:
    answers: list[str] = []

    async def go() -> None:
        await bot._on_button(types.SimpleNamespace(callback_query=_query(data, answers)), None)
        await bot.audit.drain()
    asyncio.run(go())
    return answers


def _command(bot, text: str) -> list[str]:
    replies: list[str] = []

    async def reply_text(t: str) -> None:
        replies.append(t)

    async def go() -> None:
        msg = types.SimpleNamespace(text=text, reply_text=reply_text)
        await bot._on_command(types.SimpleNamespace(effective_user=types.SimpleNamespace(id=TELEGRAM_ID), message=msg),
                              None)
        await bot.audit.drain()
    asyncio.run(go())
    return replies


def _events(state, event: str) -> list[dict]:
    return [e for e in AuthStore(state).audit_events() if e["event"] == event]


@pytest.mark.parametrize(("data", "action", "reason"), [("ok:p1", "approve", None), ("no:p1:news", "reject", "news")])
def test_a_telegram_decision_is_audited_with_the_telegram_id_and_the_approval_files_never_carry_it(tmp_path, data,
                                                                                                  action, reason):
    bot = _bot(tmp_path)
    center = ApprovalCenter({TELEGRAM_ID}, bus=bot.bus)
    center.propose(_prop("p1"))
    before = time.time()
    assert _click(bot, data)[0] == "sent to the engine"
    (row,) = _events(tmp_path, "decision")
    assert (row["by"], row["telegram_id"], row["proposal"], row["action"], row["reason"]) == \
        (f"telegram:{TELEGRAM_ID}", TELEGRAM_ID, "p1", action, reason)
    assert row["ts"] >= before
    _assert_no_identity((bot.bus.decisions_dir / "p1.json").read_text())
    center.poll_bus()                                               # the engine applies and archives it
    _assert_no_identity((bot.bus.done_dir / "p1.json").read_text())
    assert not list(bot.bus.decisions_dir.glob("*.json"))


def test_a_refused_telegram_decision_is_not_audited_as_a_decision(tmp_path):
    bot = _bot(tmp_path)
    assert _click(bot, "ok:unknown")[0] == "unknown or already decided proposal"
    assert _events(tmp_path, "decision") == []


def test_a_failing_audit_write_never_undoes_or_hides_a_telegram_decision_or_halt(tmp_path, monkeypatch):
    def broken(_state):
        def audit(*_a, **_kw):
            raise OSError("aux.db locked")
        return audit
    monkeypatch.setattr(automode, "audit_to", broken)
    bot = _bot(tmp_path)
    bot.bus.publish(_prop("p1"))
    assert _click(bot, "ok:p1")[0] == "sent to the engine"
    assert (bot.bus.decisions_dir / "p1.json").exists()
    assert _command(bot, "/halt")[0].startswith("HALTED") and bot.bus.control().halted


def test_a_telegram_halt_is_audited_with_the_telegram_id_and_control_json_keeps_only_the_channel(tmp_path):
    bot = _bot(tmp_path)
    assert _command(bot, "/halt fomc")[0].startswith("HALTED") and bot.bus.control().halted
    _assert_no_identity(bot.bus.control_path.read_text())
    (row,) = _events(tmp_path, "halt")
    assert (row["by"], row["telegram_id"], row["reason"]) == (f"telegram:{TELEGRAM_ID}", TELEGRAM_ID, "fomc")


def test_a_slow_audit_write_never_delays_the_click_or_blocks_the_event_loop(tmp_path):
    """Review M1: aux.db can be locked for seconds; neither click waits for it and the loop (proposal pump) runs."""
    order: list[str] = []
    opened: list[int] = []

    def opener(_state):
        opened.append(1)

        def audit(event, **_kw):
            time.sleep(0.5)                                          # a busy aux.db lock
            order.append(f"audit {event}")
        return audit
    bot = _bot(tmp_path, opener=opener)
    bot.bus.publish(_prop("p1"))
    bot.bus.publish(_prop("p2"))
    ticks: list[float] = []

    async def pump() -> None:                                        # stands in for the proposal pump
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.01)

    async def go() -> float:
        task = asyncio.create_task(pump())
        t0 = time.monotonic()
        await bot._on_button(types.SimpleNamespace(callback_query=_query("ok:p1", order)), None)
        await bot._on_button(types.SimpleNamespace(callback_query=_query("ok:p2", order)), None)
        handled = time.monotonic() - t0
        mid = len(ticks)
        await bot.audit.drain()
        assert len(ticks) - mid >= 20                                # the loop kept running during ~1 s of audit
        task.cancel()
        return handled
    handled = asyncio.run(go())
    assert handled < 0.3                                             # neither click waited for the audit
    assert order.count("sent to the engine") == 2
    assert order.count("audit decision") == 2 and opened == [1]       # one audit store, opened once


def _timed_out_click(bot, data: str) -> None:
    """A tap whose answer fails (Telegram: a timeout, or "query is too old") after the action stood."""
    async def answer(text: str = "", show_alert: bool = False) -> None:
        raise TimeoutError

    async def go() -> None:
        q = _query(data, [])
        q.answer = answer
        with pytest.raises(TimeoutError):
            await bot._on_button(types.SimpleNamespace(callback_query=q), None)
        await bot.audit.drain()
    asyncio.run(go())


def test_a_decision_whose_answer_times_out_is_still_audited(tmp_path):
    bot = _bot(tmp_path)
    bot.bus.publish(_prop("p1"))
    _timed_out_click(bot, "ok:p1")
    assert (bot.bus.decisions_dir / "p1.json").exists()              # the approval stands
    (row,) = _events(tmp_path, "decision")
    assert (row["telegram_id"], row["proposal"], row["action"]) == (TELEGRAM_ID, "p1", "approve")


def test_a_deploy_tap_whose_answer_times_out_is_still_audited(tmp_path):
    from goldbot.ops.deploy import DeployWatch
    sha = "b" * 40
    bot = _bot(tmp_path)
    bot.deploy = DeployWatch(str(tmp_path), tmp_path)
    bot.deploy.dir.mkdir(parents=True)
    (bot.deploy.dir / "pending.json").write_text(json.dumps({"sha": sha}))
    _timed_out_click(bot, f"dep:{sha}")
    assert json.loads((bot.deploy.dir / "approved.json").read_text()) == {"sha": sha}    # the deploy stands
    (row,) = _events(tmp_path, "deploy_approve")
    assert (row["telegram_id"], row["sha"]) == (TELEGRAM_ID, sha)


def test_the_audit_row_carries_the_time_of_the_action_not_of_the_write(tmp_path):
    """A row that waited on a locked aux.db is still stamped when the owner acted (review L3)."""
    rows: list[dict] = []

    def opener(_state):
        def audit(event, **kw):
            time.sleep(0.4)
            rows.append({"event": event, "written": time.time(), **kw})
        return audit
    bot = _bot(tmp_path, opener=opener)
    bot.bus.publish(_prop("p1"))
    before = time.time()
    _click(bot, "ok:p1")
    (row,) = rows
    assert before <= row["ts"] < before + 0.3 and row["written"] - row["ts"] >= 0.35
    assert _events(tmp_path, "decision") == []                       # the opener above stands in for aux.db


def test_a_locked_audit_database_is_retried_once(tmp_path, monkeypatch):
    import sqlite3

    from goldbot.telegram.bot import OffLoopAudit
    monkeypatch.setattr(OffLoopAudit, "RETRY_S", 0.0)
    calls: list[str] = []

    def opener(_state):
        def audit(event, **_kw):
            calls.append(event)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
        return audit
    a = OffLoopAudit(str(tmp_path), opener=opener)
    a.write_now("decision", by=f"telegram:{TELEGRAM_ID}", proposal="p1")
    assert calls == ["decision", "decision"]


def test_an_audit_write_that_still_fails_logs_event_channel_and_proposal_never_who(tmp_path, monkeypatch, caplog):
    import sqlite3

    from goldbot.telegram.bot import OffLoopAudit
    monkeypatch.setattr(OffLoopAudit, "RETRY_S", 0.0)
    calls: list[str] = []

    def opener(_state):
        def audit(event, **_kw):
            calls.append(event)
            raise sqlite3.OperationalError("database is locked")
        return audit
    a = OffLoopAudit(str(tmp_path), opener=opener)
    with caplog.at_level("WARNING", logger="goldbot.telegram.bot"):
        a.write_now("decision", by=f"telegram:{TELEGRAM_ID}", telegram_id=TELEGRAM_ID, proposal="p1")
        a.write_now("deploy_approve", by=f"telegram:{TELEGRAM_ID}", telegram_id=TELEGRAM_ID, sha="c" * 40)
    assert len(calls) == 4                                           # each tried twice, then given up
    text = caplog.text
    assert "decision via telegram (p1)" in text and f"deploy_approve via telegram ({'c' * 40})" in text
    _assert_no_identity(text)


def test_shutdown_drains_pending_audit_rows(tmp_path):
    bot = _bot(tmp_path)
    bot.bus.publish(_prop("p1"))

    async def go() -> None:
        await bot._on_button(types.SimpleNamespace(callback_query=_query("ok:p1", [])), None)
        await bot._on_shutdown(None)                                 # PTB's post_shutdown, after run_polling stops
        assert not bot.audit._tasks
    asyncio.run(go())
    assert len(_events(tmp_path, "decision")) == 1


def test_the_shutdown_drain_is_bounded_by_a_timeout(tmp_path):
    from goldbot.telegram.bot import OffLoopAudit
    release = __import__("threading").Event()

    def opener(_state):
        def audit(event, **_kw):
            release.wait(5)                                          # aux.db stuck
        return audit
    a = OffLoopAudit(str(tmp_path), opener=opener)

    async def go() -> tuple[bool, float]:
        a.write("halt", TELEGRAM_ID)
        t0 = time.monotonic()
        done = await a.drain(timeout=0.2)
        took = time.monotonic() - t0
        release.set()
        await a.drain()
        return done, took
    done, took = asyncio.run(go())
    assert done is False and took < 1.0


def test_a_telegram_deploy_tap_is_audited_and_the_approval_file_holds_only_the_sha(tmp_path):
    from goldbot.ops.deploy import DeployWatch
    sha = "a" * 40
    bot = _bot(tmp_path)
    bot.deploy = DeployWatch(str(tmp_path), tmp_path)
    bot.deploy.dir.mkdir(parents=True)
    (bot.deploy.dir / "pending.json").write_text(json.dumps({"sha": sha}))
    assert _click(bot, f"dep:{sha}")[0] == "ok"
    assert json.loads((bot.deploy.dir / "approved.json").read_text()) == {"sha": sha}
    (row,) = _events(tmp_path, "deploy_approve")
    assert (row["by"], row["telegram_id"], row["sha"]) == (f"telegram:{TELEGRAM_ID}", TELEGRAM_ID, sha)


# ------------------------------------------------------------------------------------- the approval mode (review L1)
@pytest.mark.parametrize(("by", "stored"), [(f"telegram:{TELEGRAM_ID}", "telegram"), (f"dashboard:{EMAIL}", "dashboard"),
                                            ("engine:icm-demo", "engine:icm-demo")])
def test_set_mode_keeps_only_the_channel_for_a_person_and_the_account_for_the_kill_switch(tmp_path, by, stored):
    bus = ApprovalBus(tmp_path)
    bus.set_mode("propose", by=by)
    _assert_no_identity(bus.control_path.read_text())
    assert bus.control().mode_by == stored


@pytest.mark.integration
def test_the_mode_card_names_only_the_channel_to_every_role_even_the_owner(app_client):
    app, c, state = app_client
    owner, _ = _owner_secret(c, app)
    viewer = _viewer(c, owner)
    (state / "control.json").write_text(json.dumps({"approval_mode": "propose", "mode_by": f"telegram:{TELEGRAM_ID}",
                                                    "mode_ts": time.time()}))   # written before A15
    for who in (owner, viewer):
        r = c.get("/api/automode", headers=who)
        assert r.json()["mode_by"] == "telegram"
        _assert_no_identity(r.text)


# ------------------------------------------------------------------------------------- pre-A15 control.json (review L4)
@pytest.mark.parametrize("rewrite", ["halt", "rearm", "mode"])
def test_every_control_json_rewrite_cuts_a_pre_a15_identity_to_the_channel(tmp_path, rewrite):
    """A pre-A15 file's "dashboard:<email>" / "telegram:<id>" in by, rearm_by and mode_by is never copied forward;
    the re-arm id is kept unless the rewrite is itself a re-arm, so the engines' once-per-id clear is unchanged."""
    _legacy_control(tmp_path, approval_mode="propose", mode_by=f"telegram:{TELEGRAM_ID}", mode_ts=1.0)
    bus = ApprovalBus(tmp_path)
    if rewrite == "halt":
        c = bus.set_halt(True, by="telegram", reason="cpi")
    elif rewrite == "rearm":
        c = bus.owner_rearm(by="dashboard")
    else:
        c = bus.set_mode("propose", by="dashboard")
    text = bus.control_path.read_text()
    _assert_no_identity(text)
    assert "@" not in text
    stored = bus.control()
    assert stored == c
    assert stored.rearm_by == "dashboard" and stored.mode_by in ("telegram", "dashboard")
    assert (stored.rearm_id == "r1") == (rewrite != "rearm")


def test_a_kill_switch_rewrite_keeps_the_engine_account_and_cuts_an_old_halt_identity(tmp_path):
    _legacy_control(tmp_path)
    c = ApprovalBus(tmp_path).set_mode("propose", by="engine:icm-demo", reason="12% kill switch")
    assert (c.mode_by, c.by, c.rearm_by, c.rearm_id) == ("engine:icm-demo", "telegram", "dashboard", "r1")
    _assert_no_identity((tmp_path / "control.json").read_text())
