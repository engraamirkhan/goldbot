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
def _bot(state):
    from goldbot.telegram.bot import TelegramBot
    bot = TelegramBot.__new__(TelegramBot)                    # no network: only the handlers' state
    bot.bus, bot.owner_ids, bot.state_dir, bot.deploy = ApprovalBus(state), {TELEGRAM_ID}, str(state), None
    return bot


def _click(bot, data: str) -> list[str]:
    answers: list[str] = []

    async def answer(text: str = "", show_alert: bool = False) -> None:
        answers.append(text)

    async def edit_message_text(text: str) -> None:
        answers.append(text)
    q = types.SimpleNamespace(from_user=types.SimpleNamespace(id=TELEGRAM_ID), data=data, answer=answer,
                              edit_message_text=edit_message_text, message=types.SimpleNamespace(text="card"))
    asyncio.run(bot._on_button(types.SimpleNamespace(callback_query=q), None))
    return answers


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
    replies: list[str] = []

    async def reply_text(text: str) -> None:
        replies.append(text)
    msg = types.SimpleNamespace(text="/halt", reply_text=reply_text)
    asyncio.run(bot._on_command(types.SimpleNamespace(effective_user=types.SimpleNamespace(id=TELEGRAM_ID),
                                                      message=msg), None))
    assert replies[0].startswith("HALTED") and bot.bus.control().halted


def test_a_telegram_halt_is_audited_with_the_telegram_id_and_control_json_keeps_only_the_channel(tmp_path):
    bot = _bot(tmp_path)
    replies: list[str] = []

    async def reply_text(text: str) -> None:
        replies.append(text)
    msg = types.SimpleNamespace(text="/halt fomc", reply_text=reply_text)
    asyncio.run(bot._on_command(types.SimpleNamespace(effective_user=types.SimpleNamespace(id=TELEGRAM_ID),
                                                      message=msg), None))
    assert replies[0].startswith("HALTED") and bot.bus.control().halted
    _assert_no_identity(bot.bus.control_path.read_text())
    (row,) = _events(tmp_path, "halt")
    assert (row["by"], row["telegram_id"], row["reason"]) == (f"telegram:{TELEGRAM_ID}", TELEGRAM_ID, "fomc")
