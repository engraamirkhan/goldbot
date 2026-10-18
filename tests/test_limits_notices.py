"""Telegram FYI on limit changes (docs/ux/limits-and-tradingview.md "Telegram"): no buttons, owner chat only. A limit
change is silent; a safety revert has sound (protection changed). The copy is golden: at most 2 lines, units on every
number, money and percent together, local time with the zone name, no ids. Delivery is at least once: the cursor (aux.db)
moves only after the service acks, and a first start does not replay history."""
import json
from pathlib import Path

import pandas as pd

from goldbot.db.connection import Database
from goldbot.risk.limits_profile import LimitsChange, LimitsStore, RiskLimitsCurrent
from goldbot.telegram.limits_notices import LimitsNotices

PROFILE = "demo-2026-10-18"
T0 = pd.Timestamp("2026-10-17 08:12", tz="UTC")       # 09:12 BST


def cur(daily: float, weekly: float, mlr: float | None, reason: str, profile: str = PROFILE) -> RiskLimitsCurrent:
    return RiskLimitsCurrent(profile=profile, daily_cap=daily, weekly_cap=weekly, min_lot_risk_cap=mlr,
                             project_open_risk=True, reason=reason)


def log(state: Path, ts: pd.Timestamp, c: RiskLimitsCurrent) -> None:
    assert LimitsStore(state).log_change(LimitsChange(account_id="icm-demo", ts_utc=ts, current=c))


def engine(state: Path, **over: object) -> None:
    e = {"account": "icm-demo", "mode": "demo", "currency": "GBP", "day_start_equity": 1000.0,
         "week_start_equity": 1200.0, "daily_loss_limit_abs": 50.0, **over}
    (state / "engine_icm-demo.json").write_text(json.dumps(e))


def started(state: Path) -> LimitsNotices:
    """A service that has started (its first pass sets the cursor at the end of the logs)."""
    n = LimitsNotices(state, "Europe/London")
    msgs, ack = n.pending()
    assert msgs == [] and ack is None
    return n


def test_a_first_start_does_not_replay_old_changes(tmp_path):
    log(tmp_path, T0, cur(0.02, 0.05, None, "default", "default"))
    log(tmp_path, T0 + pd.Timedelta(hours=1), cur(0.05, 0.10, 0.01, "static: the owner's ceilings"))
    n = LimitsNotices(tmp_path, "Europe/London")
    assert n.pending() == ([], None)
    assert n.pending() == ([], None)


def test_a_limit_change_is_one_silent_message_in_the_spec_copy(tmp_path):
    engine(tmp_path)
    log(tmp_path, T0 - pd.Timedelta(days=1), cur(0.04, 0.10, None, "default", "default"))
    n = started(tmp_path)
    log(tmp_path, T0, cur(0.05, 0.10, None, "static: the owner's ceilings"))
    (m,), ack = n.pending()
    assert m.silent is True
    assert m.text == ("Limit change (demo): daily loss limit £40 -> £50 (4.0% -> 5.0% of £1,000). "
                      "Why: the owner's approved demo ceilings apply (ADR 0032). Week limit unchanged at £120.\n"
                      "17 Oct 09:12 BST")
    assert len(m.text.splitlines()) <= 2
    assert ack is not None
    n.ack(ack)
    assert n.pending() == ([], None)


def test_a_change_not_acked_is_offered_again(tmp_path):
    engine(tmp_path)
    log(tmp_path, T0 - pd.Timedelta(days=1), cur(0.04, 0.10, None, "default", "default"))
    n = started(tmp_path)
    log(tmp_path, T0, cur(0.05, 0.10, None, "static: the owner's ceilings"))
    first, _ = n.pending()
    again, _ = LimitsNotices(tmp_path, "Europe/London").pending()
    assert [m.text for m in again] == [m.text for m in first]


def test_a_safety_revert_has_sound_and_names_the_default_limits(tmp_path):
    engine(tmp_path)
    log(tmp_path, T0 - pd.Timedelta(days=1), cur(0.05, 0.10, 0.01, "static: the owner's ceilings"))
    n = started(tmp_path)
    log(tmp_path, T0, cur(0.02, 0.05, None, "reverted", "default"))
    (m,), _ = n.pending()
    assert m.silent is False
    assert m.text == ("Safety revert (demo): limits back to default. Day 2.0% (£20), week 5.0% (£60). "
                      "Minimum-lot exception off.\n17 Oct 09:12 BST")


def test_the_absolute_daily_limit_caps_the_money_figure(tmp_path):
    engine(tmp_path, day_start_equity=2000.0)
    log(tmp_path, T0 - pd.Timedelta(days=1), cur(0.02, 0.05, None, "default", "default"))
    n = started(tmp_path)
    log(tmp_path, T0, cur(0.05, 0.05, None, "static: the owner's ceilings"))
    (m,), _ = n.pending()
    assert "daily loss limit £40 -> £50 (2.0% -> 5.0% of £2,000)" in m.text


def test_without_an_engine_file_the_message_gives_percent_only(tmp_path):
    log(tmp_path, T0 - pd.Timedelta(days=1), cur(0.04, 0.10, None, "default", "default"))
    n = started(tmp_path)
    log(tmp_path, T0, cur(0.05, 0.10, None, "static: the owner's ceilings"))
    (m,), _ = n.pending()
    assert "daily loss limit 4.0% -> 5.0% of the day's start" in m.text and "£" not in m.text


def test_an_adaptive_change_is_announced_with_its_reason_in_words(tmp_path):
    engine(tmp_path)
    n = started(tmp_path)
    with Database.open(tmp_path, "core").write() as conn:
        conn.execute("INSERT INTO adaptive_changes (account_id, day, field, old, new, reasons, evidence_ids, ts_ns) "
                     "VALUES ('icm-demo', '2026-10-17', 'weekly_cap', 0.10, 0.05, ?, '[]', ?)",
                     (json.dumps(["dd_floor", "weekly_cap:dd_floor"]), int(T0.value)))
    (m,), _ = n.pending()
    assert m.text.startswith("Limit change (demo): weekly loss limit £120 -> £60 (10.0% -> 5.0% of £1,200). Why: ")
    assert "dd_floor" not in m.text and m.silent is False          # a tighten on drawdown changes protection: sound


def test_no_message_names_an_account_id_login_or_evidence(tmp_path):
    engine(tmp_path)
    log(tmp_path, T0 - pd.Timedelta(days=1), cur(0.04, 0.10, None, "default", "default"))
    n = started(tmp_path)
    log(tmp_path, T0, RiskLimitsCurrent(profile=PROFILE, daily_cap=0.05, weekly_cap=0.10, reason="static: x",
                                        evidence_ids=("trade-77",)))
    (m,), _ = n.pending()
    assert "icm-demo" not in m.text and "trade-77" not in m.text and PROFILE not in m.text


class _Telegram:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[int, str, bool]] = []
        self.fail = fail

    async def send_message(self, chat: int, text: str, disable_notification: bool = False, **_: object) -> None:
        if self.fail:
            raise OSError("network down")
        self.sent.append((chat, text, disable_notification))


def _pump(state: Path, tg: _Telegram) -> object:
    import types

    from goldbot.config import TelegramSettings
    from goldbot.telegram import bot as botmod
    b = botmod.TelegramBot.__new__(botmod.TelegramBot)          # no network: the pump's state only
    b.state_dir, b.owner_ids, b.settings, b.limits_notices = str(state), {7}, TelegramSettings(), None
    b.app = types.SimpleNamespace(bot=tg)
    return b


def test_the_pump_sends_to_the_owner_chat_without_buttons_and_acks_only_after_delivery(tmp_path):
    import asyncio
    engine(tmp_path)
    log(tmp_path, T0 - pd.Timedelta(days=1), cur(0.04, 0.10, None, "default", "default"))
    down = _Telegram(fail=True)
    b = _pump(tmp_path, down)
    asyncio.run(b._limits_pass())                                # type: ignore[attr-defined]  # first start: cursor
    log(tmp_path, T0, cur(0.05, 0.10, None, "static: the owner's ceilings"))
    asyncio.run(b._limits_pass())                                # type: ignore[attr-defined]  # send fails: no ack
    up = _Telegram()
    b.app.bot = up                                               # type: ignore[attr-defined]
    asyncio.run(b._limits_pass())                                # type: ignore[attr-defined]
    asyncio.run(b._limits_pass())                                # type: ignore[attr-defined]
    (sent,) = up.sent
    assert sent[0] == 7 and sent[1].startswith("Limit change (demo)") and sent[2] is True
