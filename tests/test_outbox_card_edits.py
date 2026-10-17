"""BACKLOG 87 (docs/ux/order-not-confirmed.md, "the card is edited at approval, then once more when resolved"): when a
NOT CONFIRMED order resolves, the Telegram message that reads "⚠️ NOT CONFIRMED" is edited to the outcome's words
(`not_confirmed.card_text`). The outbox keeps that message's id until the outcome is settled, re-reads the archived
proposal on every poll, and edits once per outcome; idempotent over a restart, never for a card it never sent, and
best effort: a lost edit never hides the owner's notice."""
from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path
from typing import Any

import pytest

from goldbot.execution.not_confirmed import Confirmation, card_text
from goldbot.telegram import bot as botmod
from goldbot.telegram.approvals import Outcome
from goldbot.telegram.bus import ApprovalBus
from goldbot.telegram.outbox import NOT_CONFIRMED_OUTCOME, Outbox, ReportRetry, SendGuard
from tests.test_engine_safety import _prop

PID = "icm-demo-87-a"
CHAT, CARD_MID = 111, 7
REPLY_MID = 101                                    # the first message the fake bot sends: the NOT CONFIRMED reply
FACTS = {"opened_closed": {"entry": 2400.2, "exit": 2406.2, "pnl": 12.3, "currency": "GBP"}}
RESOLVED = ["opened", "opened_closed", "no_trade", "opened_late", "still_unknown"]
EXPECTED = {
    "opened": "✅ OPENED: the broker confirmed it late. Stop, target and exit are automatic.",
    "opened_closed": "ℹ️ OPENED AND CLOSED: the broker confirmed it late; it opened at 2400.20 and closed at 2406.20 "
                     "(+£12.30).",
    "no_trade": "🛑 NOT PLACED: approved and sent, but the broker has no trade for it (checked twice, at least 2 "
                "minutes apart). No trade was opened.",
    "opened_late": "✅ OPENED AFTER ALL: the broker confirmed it after it was reported not placed. Stop, target and "
                   "exit are automatic.",
    "still_unknown": "⚠️ STILL NOT CONFIRMED after 10 minutes. The engine keeps looking; check MT5 for an open XAUUSD "
                     "trade.",
}


def _archive(bus: ApprovalBus, outcome: str | None = "pending", pid: str = PID) -> None:
    """The approved card as the engine archives it NOT CONFIRMED (`unconfirmed.archive_pending` / `_card`)."""
    c = None if outcome is None else Confirmation(outcome=outcome, **FACTS.get(outcome, {}))  # type: ignore[arg-type]
    bus.archive(_prop(pid).model_copy(update={"outcome": Outcome.APPROVED, "gate_refusal": ["not_confirmed"],
                                              "not_confirmed": c}))


class _Telegram:
    """The bot API calls the pump makes; `fail_edits` makes every text edit raise (Telegram down, message gone)."""

    def __init__(self, fail_edits: bool = False) -> None:
        self.sent: list[tuple[int, str]] = []
        self.edits: list[tuple[int, int, str]] = []
        self.fail_edits = fail_edits
        self.next_id = 100

    async def send_message(self, chat: int, text: str, **_: Any) -> Any:
        self.sent.append((chat, text))
        self.next_id += 1
        return types.SimpleNamespace(message_id=self.next_id)

    async def edit_message_reply_markup(self, chat: int, mid: int, reply_markup: Any = None) -> None:
        return None

    async def edit_message_text(self, text: str, chat_id: int, message_id: int) -> None:
        if self.fail_edits:
            raise RuntimeError("Bad Request: message to edit not found")
        self.edits.append((chat_id, message_id, text))


def _bot(state: Path, tg: _Telegram, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(botmod, "ReplyParameters", lambda message_id: None, raising=False)  # [live] extra not installed
    bot = botmod.TelegramBot.__new__(botmod.TelegramBot)       # no network: the pump's state only
    bot.bus, bot.state_dir, bot.owner_ids, bot.poll_s = ApprovalBus(state), str(state), {CHAT}, 2.0
    bot.outbox = Outbox(state, bot.bus)
    bot.send_guard, bot.report_retry = SendGuard(), ReportRetry()
    bot.app = types.SimpleNamespace(bot=tg)
    return bot


def _pass(bot: Any, n: int = 1) -> None:
    asyncio.run(bot._outbox_pass(bot.outbox, n))


def _reported(state: Path, monkeypatch: pytest.MonkeyPatch, tg: _Telegram | None = None) -> tuple[Any, _Telegram]:
    """A card sent, approved, archived NOT CONFIRMED and reported on Telegram as such (the reply the pump sends)."""
    tg = tg or _Telegram()
    bot = _bot(state, tg, monkeypatch)
    _archive(bot.bus)
    bot.outbox.mark_sent(PID, [(CHAT, CARD_MID)])
    _pass(bot)
    assert tg.sent == [(CHAT, f"{PID}: {card_text(None)}")]
    return bot, tg



# ---------------------------------------------------------------------------------------------- one edit per outcome
@pytest.mark.parametrize("outcome", RESOLVED)
def test_a_resolved_not_confirmed_card_is_edited_once_with_the_outcome_words(tmp_path, monkeypatch, outcome):
    bot, tg = _reported(tmp_path, monkeypatch)
    _pass(bot)
    assert tg.edits == []                                          # still pending: nothing to edit
    _archive(bot.bus, outcome)                                     # unconfirmed._card rewrites the archive
    _pass(bot)
    assert tg.edits == [(CHAT, REPLY_MID, f"{PID}: {EXPECTED[outcome]}")]
    assert EXPECTED[outcome] == card_text(Confirmation(outcome=outcome, **FACTS.get(outcome, {})))  # type: ignore[arg-type]
    _pass(bot)
    _pass(bot)
    assert len(tg.edits) == 1                                      # one edit per outcome


def test_no_trade_then_opened_after_all_edits_the_card_again_with_the_correction(tmp_path, monkeypatch):
    bot, tg = _reported(tmp_path, monkeypatch)
    _archive(bot.bus, "no_trade")
    _pass(bot)
    _archive(bot.bus, "opened_late")
    _pass(bot)
    assert [t for _, _, t in tg.edits] == [f"{PID}: {EXPECTED['no_trade']}", f"{PID}: {EXPECTED['opened_late']}"]


def test_still_not_confirmed_then_opened_edits_the_card_for_each_outcome(tmp_path, monkeypatch):
    bot, tg = _reported(tmp_path, monkeypatch)
    _archive(bot.bus, "still_unknown")
    _pass(bot)
    _archive(bot.bus, "opened")
    _pass(bot)
    assert [t for _, _, t in tg.edits] == [f"{PID}: {EXPECTED['still_unknown']}", f"{PID}: {EXPECTED['opened']}"]


def test_a_settled_outcome_stops_the_watch_so_later_archive_changes_edit_nothing(tmp_path, monkeypatch):
    bot, tg = _reported(tmp_path, monkeypatch)
    _archive(bot.bus, "opened")
    _pass(bot)
    assert bot.outbox.card_edits() == [] and not bot.outbox.watched
    _archive(bot.bus, "still_unknown")                             # never happens; if it did, no further edit
    _pass(bot)
    assert len(tg.edits) == 1


# ---------------------------------------------------------------------------------------------- restart
def test_a_restart_neither_repeats_an_edit_nor_loses_a_watched_card(tmp_path, monkeypatch):
    bot, tg = _reported(tmp_path, monkeypatch)
    _archive(bot.bus, "still_unknown")
    _pass(bot)
    restarted = _bot(tmp_path, tg, monkeypatch)                    # a new service: same aux.db
    _pass(restarted)
    assert len(tg.edits) == 1                                      # the edit already made is not repeated
    _archive(restarted.bus, "opened")
    _pass(_bot(tmp_path, tg, monkeypatch))
    assert [t for _, _, t in tg.edits][-1] == f"{PID}: {EXPECTED['opened']}" and len(tg.edits) == 2


def test_watching_a_card_twice_keeps_what_it_already_shows(tmp_path):
    bus = ApprovalBus(tmp_path)
    _archive(bus, "no_trade")
    ob = Outbox(tmp_path, bus)
    ob.watch(PID, [(CHAT, 9)])
    [edit] = ob.card_edits()
    ob.card_edited(PID, edit.outcome)
    ob.watch(PID, [(CHAT, 9)])                                     # a repeated call never resets it to pending
    assert ob.card_edits() == []


# ---------------------------------------------------------------------------------------------- never sent, never edited
def test_a_card_the_outbox_never_sent_is_never_edited(tmp_path, monkeypatch):
    tg = _Telegram()
    bot = _bot(tmp_path, tg, monkeypatch)
    _archive(bot.bus)                                              # archived NOT CONFIRMED, but never sent to Telegram
    _pass(bot)
    _archive(bot.bus, "opened")
    _pass(bot)
    assert tg.sent == [] and tg.edits == [] and bot.outbox.card_edits() == []


def test_a_card_whose_not_confirmed_reply_never_went_out_is_never_edited(tmp_path, monkeypatch):
    tg = _Telegram()
    bot = _bot(tmp_path, tg, monkeypatch)

    async def down(*_: Any, **__: Any) -> Any:
        raise RuntimeError("network down")
    tg.send_message = down                                         # type: ignore[method-assign]
    _archive(bot.bus)
    bot.outbox.mark_sent(PID, [(CHAT, CARD_MID)])
    _pass(bot)
    _archive(bot.bus, "opened")
    _pass(bot)
    assert tg.edits == [] and not bot.outbox.watched


def test_only_not_confirmed_outcomes_are_watched(tmp_path, monkeypatch):
    tg = _Telegram()
    bot = _bot(tmp_path, tg, monkeypatch)
    bot.bus.archive(_prop(PID).model_copy(update={"outcome": Outcome.REJECTED}))
    bot.outbox.mark_sent(PID, [(CHAT, CARD_MID)])
    _pass(bot)
    assert tg.sent == [(CHAT, f"{PID}: ❌ REJECTED")] and not bot.outbox.watched
    assert NOT_CONFIRMED_OUTCOME == "GATE_REFUSED: not_confirmed"


def test_a_card_left_placing_by_a_silent_engine_is_watched_and_edited_when_resolved(tmp_path, monkeypatch):
    tg = _Telegram()
    bot = _bot(tmp_path, tg, monkeypatch)
    bot.bus.archive(_prop(PID).model_copy(update={"outcome": Outcome.APPROVED, "placing": True}))
    bot.outbox.mark_sent(PID, [(CHAT, CARD_MID)])                  # no engine heartbeat: reported NOT CONFIRMED
    _pass(bot)
    assert tg.sent == [(CHAT, f"{PID}: {card_text(None)}")]
    _archive(bot.bus, "opened")                                    # BACKLOG 84: the restarted engine finds it open
    _pass(bot)
    assert [t for _, _, t in tg.edits] == [f"{PID}: {EXPECTED['opened']}"]


def test_a_watched_card_whose_archive_is_gone_is_forgotten(tmp_path):
    bus = ApprovalBus(tmp_path)
    _archive(bus)
    ob = Outbox(tmp_path, bus)
    ob.watch(PID, [(CHAT, 9)])
    (bus.done_dir / f"{PID}.json").unlink()
    assert ob.card_edits() == [] and not ob.watched


def test_a_card_watched_longer_than_the_engine_looks_is_forgotten(tmp_path, monkeypatch):
    from goldbot.telegram import outbox as outbox_mod
    bus = ApprovalBus(tmp_path)
    _archive(bus)
    ob = Outbox(tmp_path, bus)
    ob.watch(PID, [(CHAT, 9)])
    monkeypatch.setattr(outbox_mod, "WATCH_MAX_S", -1.0)
    _archive(bus, "opened")
    assert ob.card_edits() == [] and not ob.watched


def test_an_unreadable_archive_does_not_stop_the_other_cards_edits(tmp_path):
    bus = ApprovalBus(tmp_path)
    _archive(bus, pid="icm-demo-87-b")
    _archive(bus)
    ob = Outbox(tmp_path, bus)
    ob.watch("icm-demo-87-b", [(CHAT, 8)])
    ob.watch(PID, [(CHAT, 9)])
    (bus.done_dir / "icm-demo-87-b.json").write_text("{not json")
    _archive(bus, "opened")
    assert [(e.proposal_id, e.outcome) for e in ob.card_edits()] == [(PID, "opened")]
    assert "icm-demo-87-b" in ob.watched                           # kept: read again on the next poll


# ---------------------------------------------------------------------------------------------- best effort
def test_a_lost_edit_never_hides_the_notice_and_is_not_retried_every_poll(tmp_path, monkeypatch):
    tg = _Telegram()
    bot, _ = _reported(tmp_path, monkeypatch, tg)
    bot.outbox.pending_reports()                                   # first start: the report cursor is set
    tg.fail_edits = True
    _archive(bot.bus, "opened")
    bot.bus.notify_owner("Order confirmed: LONG 0.1 lots XAUUSD on demo is open at 2400.20.")
    _pass(bot, n=0)                                                # a report pass: the notice goes out
    assert any(t.endswith("Order confirmed: LONG 0.1 lots XAUUSD on demo is open at 2400.20.") for _, t in tg.sent)
    assert tg.edits == [] and bot.outbox.card_edits() == []        # one attempt per outcome, logged


def test_an_outbox_failure_while_editing_never_stops_the_reports(tmp_path, monkeypatch):
    tg = _Telegram()
    bot, _ = _reported(tmp_path, monkeypatch, tg)
    bot.outbox.pending_reports()

    def broken() -> Any:
        raise RuntimeError("database is locked")
    monkeypatch.setattr(bot.outbox, "card_edits", broken)
    bot.bus.notify_owner("a notice")
    _pass(bot, n=0)
    assert any(t.endswith("\n\na notice") for _, t in tg.sent)


def test_the_watch_table_is_not_exported_to_the_rollback_file(tmp_path, monkeypatch):
    bot, _ = _reported(tmp_path, monkeypatch)
    doc = json.loads((tmp_path / "telegram_outbox.json").read_text())
    assert PID not in doc["sent"] and PID in bot.outbox.watched
