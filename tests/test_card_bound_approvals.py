"""BACKLOG 63: a tap is bound to the exact card the owner saw. The Telegram Approve/Reject buttons and the dashboard
decision request carry the card's `created` (the proposal stamp the card was rendered from); the bus refuses, and the
engine refuses and logs (`card_mismatch`), any decision whose stamp differs from the pending proposal's, and a decision
without a stamp (an old card, an old client) is refused, never accepted (fail closed). Every callback data string fits
Telegram's 64-byte limit for the longest proposal id the system can make."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import types
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api.app import create_app
from goldbot.engine import Engine
from goldbot.ops import accounts
from goldbot.telegram.approvals import (
    CALLBACK_MAX_BYTES,
    REASON_CODES,
    ApprovalCenter,
    Outcome,
    Proposal,
    decision_callbacks,
    parse_decision_callback,
)
from goldbot.telegram.bus import ApprovalBus
from tests.test_api_owner_controls import _owner_secret

OWNER_ID = 4242


def _p(pid: str = "icm-demo-1760000000-0123456789", created: float | None = None,
       account_id: str = "icm-demo") -> Proposal:
    return Proposal(proposal_id=pid, account_id=account_id, agent_id="session_open-g0-x", side=1, lots=0.1,
                    entry=2400.0, stop=2396.0, target=2406.0, p=0.6, ev_r=0.3, spread_points=20.0,
                    top_features=[("a", 0.1)], created=time.time() if created is None else created)


# ------------------------------------------------------------------------------------------- the callback data
def test_the_approve_and_every_reject_button_carry_the_exact_created_stamp_of_the_card():
    p = _p(created=time.time() + 0.1234567891)            # a full-precision stamp survives the round trip exactly
    cbs = decision_callbacks(p)
    assert set(cbs) == {None, *REASON_CODES}
    for reason, data in cbs.items():
        tap = parse_decision_callback(data)
        assert tap.proposal_id == p.proposal_id and tap.card_created == p.created
        assert tap.approve is (reason is None) and tap.reason_code == reason


def _longest_proposal_id() -> str:
    """The engine's own id format (Engine._proposal_id) for the longest account name load_accounts accepts and a bar
    close with the widest epoch before 2286 (ten digits)."""
    account = "a" * accounts.ACCOUNT_ID_MAX_BYTES
    eng = types.SimpleNamespace(cfg=types.SimpleNamespace(account_id=account))
    close = pd.Timestamp(9_999_999_999, unit="s", tz="UTC")
    return Engine._proposal_id(eng, "session_open-g12-0123456789", close)   # type: ignore[arg-type]


def test_every_callback_data_string_fits_telegrams_64_byte_limit_for_the_longest_proposal_id():
    pid = _longest_proposal_id()
    for created in (time.time(), 1e-300, -1.7976931348623157e308, float("nan")):   # the token is fixed-width
        for data in decision_callbacks(_p(pid, created=created)).values():
            assert len(data.encode("utf-8")) <= CALLBACK_MAX_BYTES == 64, data


def test_a_proposal_id_too_long_for_telegrams_button_data_is_refused_rather_than_cut():
    pid = _longest_proposal_id()
    room = CALLBACK_MAX_BYTES - max(len(d.encode("utf-8")) for d in decision_callbacks(_p(pid)).values())
    with pytest.raises(ValueError, match="64"):
        decision_callbacks(_p(pid + "x" * (room + 1)))


def test_an_account_name_longer_than_the_button_budget_is_refused_at_load(tmp_path):
    def registry(name: str) -> Path:
        f = tmp_path / f"{len(name)}.yaml"
        f.write_text(json.dumps({"accounts": {name: {
            "broker": "icm", "mode": "demo", "server": "S", "terminal_path": "t", "server_tz": "Europe/Athens",
            "symbol": "XAUUSD", "magic_base": 1, "enabled": False, "login": 1}}}))
        return f
    ok = "b" * accounts.ACCOUNT_ID_MAX_BYTES
    assert list(accounts.load_accounts(registry(ok))) == [ok]
    with pytest.raises(ValueError, match="too long"):
        accounts.load_accounts(registry(ok + "b"))


@pytest.mark.parametrize("data", ["ok:icm-demo-1760000000-0123456789",           # a card sent before BACKLOG 63
                                  "no:icm-demo-1760000000-0123456789:news",
                                  "ok:not-a-token!:icm-demo-1",
                                  "no:z:AAAAAAAAAAA:icm-demo-1"])
def test_a_callback_without_a_valid_card_stamp_is_refused(data):
    with pytest.raises(ValueError):
        parse_decision_callback(data)


# ------------------------------------------------------------------------------------------- the bus (submit)
def test_submit_refuses_a_decision_without_the_card_stamp(tmp_path):
    bus = ApprovalBus(tmp_path)
    bus.publish(_p("x"))
    with pytest.raises(ValueError, match="card_missing"):
        bus.submit("x", True, None, by="dashboard", card_created=None)
    assert not (bus.decisions_dir / "x.json").exists()


def test_submit_refuses_a_decision_made_on_another_card_with_the_same_id(tmp_path, caplog):
    bus = ApprovalBus(tmp_path)
    old = _p("x", created=time.time() - 20)
    bus.publish(_p("x", created=time.time() - 5))         # re-raised under the same id
    with caplog.at_level(logging.WARNING), pytest.raises(KeyError, match="card_mismatch"):
        bus.submit("x", True, None, by="telegram", card_created=old.created)
    assert not (bus.decisions_dir / "x.json").exists() and "card_mismatch" in caplog.text


# ------------------------------------------------------------------------------------------- the engine (poll_bus)
def _decision_file(bus: ApprovalBus, pid: str, **over: object) -> None:
    row = {"proposal_id": pid, "approve": True, "by": "dashboard", "ts": time.time(), **over}
    (bus.decisions_dir / f"{pid}.json").write_text(json.dumps(row))


def test_the_engine_refuses_and_logs_card_mismatch_for_a_decision_bound_to_another_card(tmp_path, caplog):
    """Defence in depth behind `submit`: the pending file can be replaced between the submit check and the poll."""
    bus = ApprovalBus(tmp_path)
    center = ApprovalCenter({1}, bus=bus)
    p = center.propose(_p("x"))
    _decision_file(bus, "x", card_created=p.created - 7.0)
    with caplog.at_level(logging.WARNING, logger="goldbot.approvals"):
        assert center.poll_bus() == []
    assert "card_mismatch" in caplog.text and "x" in center.pending
    assert not (bus.decisions_dir / "x.json").exists()                     # discarded: the card shown stays decidable


def test_the_engine_refuses_a_decision_without_a_card_stamp_whatever_its_time(tmp_path, caplog):
    """Fail closed: the BACKLOG 60 fallback (an unbound decision stamped after the proposal was applied) is gone."""
    bus = ApprovalBus(tmp_path)
    center = ApprovalCenter({1}, bus=bus)
    p = center.propose(_p("x"))
    _decision_file(bus, "x", ts=p.created + 1.0)
    with caplog.at_level(logging.WARNING, logger="goldbot.approvals"):
        assert center.poll_bus() == []
    assert "card_missing" in caplog.text and "x" in center.pending


# ------------------------------------------------------------------------------------------- Telegram taps
def _bot(state: Path):
    from goldbot.telegram.bot import OffLoopAudit, TelegramBot
    bot = TelegramBot.__new__(TelegramBot)                    # no network: only the handlers' state
    bot.bus, bot.owner_ids, bot.state_dir, bot.deploy = ApprovalBus(state), {OWNER_ID}, str(state), None
    bot.audit = OffLoopAudit(str(state))
    return bot


def _tap(bot, data: str) -> list[str]:
    answers: list[str] = []

    async def answer(text: str = "", show_alert: bool = False) -> None:
        answers.append(text)

    async def edit_message_text(text: str) -> None:
        answers.append(text)

    q = types.SimpleNamespace(from_user=types.SimpleNamespace(id=OWNER_ID), data=data, answer=answer,
                              edit_message_text=edit_message_text, message=types.SimpleNamespace(text="card"))

    async def go() -> None:
        await bot._on_button(types.SimpleNamespace(callback_query=q), None)
        await bot.audit.drain()
    asyncio.run(go())
    return answers


def test_a_tap_on_the_pre_restart_card_for_a_re_raised_proposal_is_refused(tmp_path):
    bot = _bot(tmp_path)
    before = _p("x", created=time.time() - 30)
    stale = decision_callbacks(before)[None]                  # the card the owner still sees
    center = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))   # the engine after a restart raises the same id again
    center.propose(_p("x", created=time.time() - 2))
    answers = _tap(bot, stale)
    assert "card_mismatch" in answers[0] and not (bot.bus.decisions_dir / "x.json").exists()
    assert center.poll_bus() == [] and "x" in center.pending


def test_a_tap_on_the_current_card_approves(tmp_path):
    bot = _bot(tmp_path)
    center = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))
    p = center.propose(_p("x"))
    assert _tap(bot, decision_callbacks(p)[None])[0] == "sent to the engine"
    assert [q.outcome for q in center.poll_bus()] == [Outcome.APPROVED]


def test_a_reject_tap_on_the_current_card_rejects_with_its_reason(tmp_path):
    bot = _bot(tmp_path)
    center = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))
    p = center.propose(_p("x"))
    _tap(bot, decision_callbacks(p)["discretion"])
    (done,) = center.poll_bus()
    assert done.outcome == Outcome.REJECTED and done.reason_code == "discretion"


def test_a_tap_on_a_card_sent_before_the_binding_is_refused(tmp_path):
    bot = _bot(tmp_path)
    center = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))
    center.propose(_p("x"))
    answers = _tap(bot, "ok:x")
    assert "card_missing" in answers[0] and not (bot.bus.decisions_dir / "x.json").exists()


# ------------------------------------------------------------------------------------------- the dashboard
@pytest.fixture
def app_client(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", owner_email="o@x.io")
    c = TestClient(app)
    owner, _ = _owner_secret(c, app)
    return c, owner, ApprovalBus(state)


@pytest.mark.integration
def test_the_dashboard_card_carries_created_and_a_decision_on_it_is_accepted(app_client):
    c, owner, bus = app_client
    p = _p("x", created=time.time() + 0.1234567891)
    bus.publish(p)
    (card,) = c.get("/api/proposals", headers=owner).json()
    assert card["created"] == p.created                       # exact through JSON
    r = c.post("/api/decisions", json={"proposal_id": "x", "action": "approve", "created": card["created"]},
               headers=owner)
    assert r.status_code == 200 and r.json()["outcome"] == "SUBMITTED"
    assert json.loads((bus.decisions_dir / "x.json").read_text())["card_created"] == p.created


@pytest.mark.integration
def test_the_dashboard_decision_request_without_created_is_refused(app_client):
    c, owner, bus = app_client
    bus.publish(_p("x"))
    r = c.post("/api/decisions", json={"proposal_id": "x", "action": "approve"}, headers=owner)
    assert r.status_code == 422 and not (bus.decisions_dir / "x.json").exists()


@pytest.mark.integration
def test_the_dashboard_decision_on_another_card_with_the_same_id_is_refused(app_client):
    c, owner, bus = app_client
    p = _p("x")
    bus.publish(p)
    r = c.post("/api/decisions", json={"proposal_id": "x", "action": "approve", "created": p.created - 1.0},
               headers=owner)
    assert r.status_code == 400 and "card_mismatch" in r.json()["detail"]
    assert not (bus.decisions_dir / "x.json").exists()
