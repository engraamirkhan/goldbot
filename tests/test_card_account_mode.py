"""Backlog 103 (ADR 0036): the approval card says demo or real money, why a demo account asks, and uses the proposal's
own window (docs/design/ui-91-92-approval-card.md sections 1, 2 and 5).

"Demo accounts enter automatically; real-money accounts need the owner's one-click confirmation", so a demo card is an
exception and says why it asks; a real-money card never carries a reason. A demo account on a terminal that is not a
demo account (`terminal_not_demo`) is real money: no channel may approve it (sign-off correction 2)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import auto_entry
from goldbot.risk.gate import Stage
from goldbot.telegram.approvals import ASK_KINDS, AskReason, Outcome, Proposal, ask_reason_text, window_words
from goldbot.telegram.bus import ApprovalBus, BusDecision
from tests.cards import submit_shown
from tests.test_demo_auto_entries import _engine, _refreshed

UNTIL = datetime(2026, 11, 17, 14, 5, tzinfo=timezone.utc).timestamp()     # a Tuesday


def _prop(**kw: Any) -> Proposal:
    base: dict[str, Any] = dict(proposal_id="icm-1-a", account_id="icm-demo", agent_id="trend-g0-x", side=1, lots=0.05,
                                entry=2400.25, stop=2396.25, target=2406.25, p=0.62, ev_r=0.31, spread_points=22,
                                top_features=[("atr_14", 0.4)], currency="GBP", risk_amount=8.2,
                                risk_fraction=0.0082, created=time.time())
    base.update(kw)
    return Proposal(**base)


# ---------------------------------------------------------------------------------------------- words
@pytest.mark.parametrize("seconds, words", [(90, "90 s"), (20, "20 s"), (120, "120 s"), (150, "2.5 min"),
                                            (300, "5 min"), (3600, "60 min")])
def test_the_window_reads_in_seconds_up_to_two_minutes_then_in_minutes(seconds, words):
    assert window_words(seconds) == words


@pytest.mark.parametrize("kind, until, text", [
    ("rearm_lock", UNTIL, "this account was just re-armed; it asks for every entry until Tue 17 Nov 14:05 UTC."),
    ("rearm_lock", None, "this account was just re-armed; it asks for every entry for now."),
    ("kill_switch", None, "the 12% drawdown stop fired; entries ask you until the account is re-armed."),
    ("owner_propose", None, "you set entry mode to Propose in Settings. Demo normally enters on its own."),
    ("terminal_not_demo", None, "this demo account's terminal does not report a demo account, so it is not "
                                "allowed to enter on its own. Fix the account setup first."),
    ("terminal_unknown", None, "the account has not been read yet, so its terminal cannot be confirmed as a demo "
                               "account; it is not allowed to enter on its own until the next good read."),
    ("risk_unreadable", None, "the risk file could not be read, so automatic entry is off until it is fixed."),
    ("config_propose", None, "this account is set to ask in its configuration.")])
def test_every_ask_kind_reads_as_one_plain_sentence(kind, until, text):
    assert ask_reason_text(AskReason(kind=kind, until=until)) == text


def test_a_terminal_that_reports_real_money_is_named_as_one():
    assert ask_reason_text(AskReason(kind="terminal_not_demo", terminal="contest")).startswith(
        "this demo account's terminal does not report a demo account")
    assert ask_reason_text(AskReason(kind="terminal_not_demo", terminal="real")) == (
        "this demo account is connected to a real-money terminal, so it is not allowed to enter on its own. Fix the "
        "account setup first.")


def test_the_seven_ask_kinds_are_the_designed_set():
    assert set(ASK_KINDS) == {"rearm_lock", "kill_switch", "risk_unreadable", "owner_propose", "terminal_not_demo",
                              "terminal_unknown", "config_propose"}


# ---------------------------------------------------------------------------------------------- Telegram card
def test_a_real_money_telegram_card_leads_with_the_badge_and_never_says_why_it_asks():
    p = _prop(account_id="icm-live", account_mode="live", ask_reason=AskReason(kind="owner_propose"))  # wrongly given
    lines = p.text().splitlines()
    assert lines[0] == "🔒 REAL MONEY · icm-live"
    assert lines[1] == "🟢 LONG 0.05 lots XAUUSD"
    assert not [x for x in lines if x.startswith("Asking because")]
    assert lines[-1] == "⏱ approve within 90 s or it expires · exits are automatic"
    assert p.approve_label == "Approve real money"


def test_a_demo_telegram_card_says_demo_and_why_it_asks():
    p = _prop(account_mode="demo", ask_reason=AskReason(kind="owner_propose"), window_s=3600)
    lines = p.text().splitlines()
    assert lines[0] == "DEMO · no real money · icm-demo"
    assert lines[1] == "Asking because: you set entry mode to Propose in Settings. Demo normally enters on its own."
    assert lines[2] == "🟢 LONG 0.05 lots XAUUSD"
    assert lines[-1] == "⏱ approve within 60 min or it expires · exits are automatic"
    assert p.approve_label == "Approve demo"


def test_an_older_engines_card_says_the_account_type_is_unknown_never_demo():
    p = _prop()
    assert p.text().splitlines()[0] == "ACCOUNT TYPE UNKNOWN · icm-demo"
    assert p.approve_label == "Approve (account type unknown)"
    assert _prop(account_mode="paper").text().splitlines()[0] == "PAPER · simulated · icm-demo"


def test_a_demo_card_on_a_real_money_terminal_reads_as_real_money_and_offers_no_approve():
    p = _prop(account_mode="demo", ask_reason=AskReason(kind="terminal_not_demo", terminal="real"))
    lines = p.text().splitlines()
    assert lines[0] == "🔒 REAL MONEY (terminal) · icm-demo"
    assert lines[1].startswith("Asking because: this demo account is connected to a real-money terminal")
    assert lines[-1] == "⛔ approving is off for this card: fix the account setup first · it expires in 90 s"
    assert p.approve_label is None and not p.approvable


def test_a_demo_card_whose_terminal_is_not_confirmed_says_so_and_offers_no_approve():
    p = _prop(account_mode="demo", ask_reason=AskReason(kind="terminal_unknown"))
    lines = p.text().splitlines()
    assert lines[0] == "🔒 ACCOUNT TYPE UNKNOWN (terminal not confirmed) · icm-demo"
    assert lines[1].startswith("Asking because: the account has not been read yet")
    assert lines[-1].startswith("⛔ approving is off for this card")
    assert p.approve_label is None and not p.approvable


# ---------------------------------------------------------------------------------------------- nobody approves it
FAULTS = [("terminal_not_demo", "real"), ("terminal_unknown", None)]


@pytest.mark.parametrize("kind, terminal", FAULTS)
def test_the_bus_refuses_an_approval_of_a_terminal_fault_card_but_takes_a_rejection(tmp_path, kind, terminal):
    bus = ApprovalBus(tmp_path)
    bus.publish(_prop(account_mode="demo", ask_reason=AskReason(kind=kind, terminal=terminal)))
    with pytest.raises(ValueError, match=kind):
        submit_shown(bus, "icm-1-a", True, None, by="dashboard")
    assert not (tmp_path / "approvals" / "decisions" / "icm-1-a.json").exists()
    assert submit_shown(bus, "icm-1-a", False, "other", by="dashboard").approve is False


@pytest.mark.parametrize("kind, terminal", FAULTS)
def test_the_engine_never_applies_an_approval_of_a_terminal_fault_card_that_reached_the_bus(tmp_path, kind, terminal):
    eng, _, center = _engine(tmp_path, approval="propose")
    p = center.propose(_prop(account_mode="demo", ask_reason=AskReason(kind=kind, terminal=terminal)))
    bus = center.bus
    assert bus is not None
    (bus.decisions_dir / f"{p.proposal_id}.json").write_text(BusDecision(   # written past `submit`, as a bad writer
        proposal_id=p.proposal_id, approve=True, reason_code=None, by="dashboard", ts=time.time(),
        card_created=p.created).model_dump_json())
    assert center.poll_bus() == []
    assert p.proposal_id in center.pending and p.outcome is None
    with pytest.raises(ValueError, match=kind):
        center.decide(p.proposal_id, next(iter(center.allowed)), True)
    assert center.decide(p.proposal_id, next(iter(center.allowed)), False, "other").outcome == Outcome.REJECTED


# ---------------------------------------------------------------------------------------------- the engine's reason
NOW = pd.Timestamp("2025-03-03 08:00", tz="UTC")


def test_a_live_or_paper_account_has_no_ask_reason(tmp_path):
    eng, pb, _ = _engine(tmp_path, mode="live", approval="propose", trade_mode="real")
    _refreshed(eng, pb)
    assert auto_entry.ask_reason(eng, NOW) is None
    eng, pb, _ = _engine(tmp_path / "p", mode="paper", approval="propose", trade_mode=None)
    _refreshed(eng, pb)
    assert auto_entry.ask_reason(eng, NOW) is None


def test_a_demo_account_entering_automatically_has_no_ask_reason(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    assert _refreshed(eng, pb) == "auto"
    assert auto_entry.ask_reason(eng, NOW) is None


def test_a_demo_account_held_on_propose_by_the_owner_says_so(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    eng.center.bus.set_mode("propose", by="telegram", reason="owner")
    _refreshed(eng, pb)
    assert auto_entry.ask_reason(eng, NOW) == AskReason(kind="owner_propose")


def test_a_demo_account_configured_to_ask_says_its_configuration(tmp_path):
    eng, pb, _ = _engine(tmp_path, approval="propose")
    _refreshed(eng, pb)
    assert auto_entry.ask_reason(eng, NOW) == AskReason(kind="config_propose")


def test_the_re_arm_window_and_the_kill_switch_name_themselves(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    eng._propose_only_until = pd.Timestamp("2030-01-01", tz="UTC")
    _refreshed(eng, pb)
    assert auto_entry.ask_reason(eng, NOW) == AskReason(kind="rearm_lock", until=eng._propose_only_until.timestamp())
    eng.state.stage = Stage.HALTED
    assert auto_entry.ask_reason(eng, NOW) == AskReason(kind="kill_switch")


@pytest.mark.parametrize("trade_mode", ["real", "contest"])
def test_a_demo_account_on_a_terminal_that_is_not_demo_is_terminal_not_demo_before_anything_else(tmp_path, trade_mode):
    eng, pb, _ = _engine(tmp_path, trade_mode=trade_mode)
    eng.state.stage = Stage.HALTED                                           # the terminal fault still comes first
    _refreshed(eng, pb)
    assert auto_entry.ask_reason(eng, NOW) == AskReason(kind="terminal_not_demo", terminal=trade_mode)


def test_a_terminal_that_does_not_say_or_is_not_read_yet_is_terminal_unknown_not_blamed(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    assert auto_entry.ask_reason(eng, NOW) == AskReason(kind="terminal_unknown")      # no account read yet
    eng, pb, _ = _engine(tmp_path / "n", trade_mode=None)
    eng.state.stage = Stage.HALTED                                           # still before anything else
    _refreshed(eng, pb)
    assert auto_entry.ask_reason(eng, NOW) == AskReason(kind="terminal_unknown")


def test_an_account_read_that_fails_at_the_order_fails_closed_as_terminal_unknown(tmp_path, monkeypatch):
    eng, pb, _ = _engine(tmp_path)
    assert _refreshed(eng, pb) == "auto"

    def broken() -> None:
        raise OSError("bridge down")
    monkeypatch.setattr(pb, "account", broken)
    assert not auto_entry.permitted(eng, NOW) and eng.cfg.approval_mode == "propose"
    assert auto_entry.ask_reason(eng, NOW) == AskReason(kind="terminal_unknown")


def test_a_terminal_that_turns_real_at_the_order_makes_the_card_terminal_not_demo(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    assert _refreshed(eng, pb) == "auto"
    pb.trade_mode = "real"                                                   # switched after the refresh
    assert not auto_entry.permitted(eng, NOW)
    assert auto_entry.ask_reason(eng, NOW) == AskReason(kind="terminal_not_demo", terminal="real")


def test_the_engine_stamps_its_mode_and_reason_on_a_proposal_and_publishes_the_reason(tmp_path):
    eng, pb, _ = _engine(tmp_path, approval="propose")
    _refreshed(eng, pb)
    p = auto_entry.asking(eng, _prop(account_mode=None), NOW)
    assert (p.account_mode, p.ask_reason) == ("demo", AskReason(kind="config_propose"))
    eng._write_state()
    published = json.loads(Path(tmp_path / "engine_icm-demo.json").read_text())
    assert published["ask_reason"] == {"kind": "config_propose", "until": None, "terminal": None}


def test_the_telegram_keyboard_names_the_money_class_and_has_no_approve_on_a_terminal_not_demo_card(tmp_path,
                                                                                                     monkeypatch):
    import asyncio
    import types

    from goldbot.telegram import bot as bot_mod
    from tests.test_card_bound_approvals import _bot
    monkeypatch.setattr(bot_mod, "InlineKeyboardMarkup", lambda rows: rows, raising=False)
    monkeypatch.setattr(bot_mod, "InlineKeyboardButton", lambda text, callback_data: text, raising=False)
    bot = _bot(tmp_path)
    sent: list[Any] = []

    async def send_message(uid: int, text: str, reply_markup: Any = None) -> types.SimpleNamespace:
        sent.append(reply_markup)
        return types.SimpleNamespace(message_id=len(sent))
    bot.app = types.SimpleNamespace(bot=types.SimpleNamespace(send_message=send_message))
    asyncio.run(bot.send_proposal(_prop(account_id="icm-live", account_mode="live")))
    asyncio.run(bot.send_proposal(_prop(account_mode="demo",
                                        ask_reason=AskReason(kind="terminal_not_demo", terminal="real"))))
    asyncio.run(bot.send_proposal(_prop(account_mode="demo", ask_reason=AskReason(kind="terminal_unknown"))))
    live, *faults = sent
    assert live[0] == ["Approve real money"]
    for fault in faults:
        assert not [b for row in fault for b in row if b.startswith("Approve")] and fault[0][0].startswith("Reject")


@pytest.mark.integration      # drives 5 days of synthetic ticks through the engine (as tests/test_demo_auto_entries.py)
def test_a_driven_demo_engine_held_on_propose_sends_cards_that_say_demo_and_why(tmp_path):
    from tests.test_demo_auto_entries import _drive
    eng, _, center = _engine(tmp_path)
    eng.center.bus.set_mode("propose", by="telegram", reason="owner")
    _drive(eng)
    cards = [*center.pending.values(), *center.log]
    assert cards and all((p.account_mode, p.ask_reason) == ("demo", AskReason(kind="owner_propose")) for p in cards)
