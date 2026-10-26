"""Telegram /status speaks the dashboard's entry-status language (docs/ux/entry-status.md, Telegram section): the same
goldbot.api.entry_status.account_entry_status builds both, so the badge word, headline, reasons, fixes and exits line
are the API's own words. Plain text, short enough for a phone, no account identifiers beyond the config name."""
import json
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.api import entry_status as es
from goldbot.telegram import entry_text as et
from goldbot.telegram.digest import build_digest
from tests.test_api_entry_status import SAT, WED, engine, status

LONDON = "Europe/London"


def text(s, now: pd.Timestamp = WED, account: str = "icm-demo", mode: str = "demo") -> str:
    return et.account_text(account, mode, s, now=now, tz=LONDON)


def lines(t: str) -> list[str]:
    return t.splitlines()


def test_an_open_account_says_open_the_headline_and_that_exits_keep_running():
    s = status(engine())
    t = text(s)
    assert lines(t)[0].startswith("OPEN · icm-demo (demo)")
    assert s.headline in t and s.exits in t
    assert "Needs you" not in t and "Waits" not in t
    assert len(lines(t)) <= 4


def test_a_closed_market_is_paused_with_the_reopen_time_in_the_owners_zone_and_utc():
    s = status(engine(market_closed=True, entry_blocks=["market_closed"], ts=SAT.timestamp()), now=SAT)
    t = text(s, now=SAT)
    assert lines(t)[0].startswith("PAUSED · icm-demo (demo)")
    assert s.headline in t and s.exits in t
    # reopens Sunday 22:02 UTC = Sunday 23:02 London (BST, UTC+1)
    assert "Market closed · opens Sun 23:02 BST (22:02 UTC, in 1 d 12 h)" in t
    assert "Needs you" not in t and "Waits:" in t
    assert "Nothing to do" not in t           # a wait whose fix is "nothing to do" is one line: the time says it


def test_rollover_and_the_weekend_cut_say_their_times_in_the_owners_zone_and_utc():
    """BACKLOG 65 (b): the same times as the dashboard, from the same API blocks."""
    roll = pd.Timestamp("2026-10-14 20:57", tz="UTC")              # 23:57 Athens: clears 00:05 Athens = 22:05 BST
    t = text(status(engine(entry_blocks=["rollover"], ts=roll.timestamp()), now=roll), now=roll)
    assert "Daily rollover around broker midnight · until 22:05 BST (21:05 UTC, in 8 min)" in t
    fri = pd.Timestamp("2026-10-16 19:00", tz="UTC")               # Friday 22:00 Athens, after the 21:30 cut
    t = text(status(engine(entry_blocks=["weekend"], ts=fri.timestamp()), now=fri), now=fri)
    assert "Weekend: no new trades from Friday evening · opens Sun 23:02 BST (22:02 UTC, in 2 d 3 h)" in t


def test_a_throttled_agent_is_partly_open_and_names_the_strategy_and_its_cooldown():
    th: dict[str, Any] = {"enabled": True, "loss_streak": 3, "agents": {
        "trend-g0": {"loss_streak": 3, "cooldown_until": "2026-10-14T14:30:00+00:00", "blocked": ["throttle_loss_streak"]},
        "tsmom-g0": {"loss_streak": 0, "cooldown_until": None, "blocked": []}}}
    s = status(engine(throttle=th))
    t = text(s)
    assert lines(t)[0].startswith("PARTLY OPEN · icm-demo (demo)")
    assert "Can take new trades, except Trend (trend-g0)" in t
    assert "Trend (trend-g0) paused after 3 losses in a row · until 15:30 BST (14:30 UTC, in 30 min)" in t


def test_a_strategys_last_refused_trade_reads_the_same_as_on_the_dashboard():
    s = status(engine(entry_refusals={"trend-g0": {"code": "daily_loss_limit_abs", "at": "2026-10-14T13:02:00+00:00"}}))
    t = text(s)
    assert lines(t)[0].startswith("PARTLY OPEN · icm-demo (demo)")
    assert ("Trend (trend-g0): last entry refused, this trade could lose more than is left of today's loss limit"
            " · since 14:02 BST") in t
    assert "limit reached" not in t.lower()


def test_a_clock_mismatch_is_blocked_and_the_fix_comes_under_needs_you_first():
    s = status(engine(market_closed=True, entry_blocks=["server_clock_mismatch", "market_closed"],
                      server_clock={"status": "mismatch", "reason": "server clock reads UTC+3, Europe/Athens gives UTC+2"}))
    t = text(s)
    assert lines(t)[0].startswith("BLOCKED · icm-demo (demo)")
    assert s.headline in t
    act = next(b for b in s.blocks if b.code == "server_clock_mismatch")
    assert act.text in t and act.fix in t
    assert t.index("Needs you:") < t.index(f"• {act.text}") < t.index(f"→ {act.fix}") < t.index("Waits:") \
        < t.index("• Market closed")
    assert "UTC+3" not in t and "Athens" not in t      # the engine's free text never reaches the owner


def test_a_silent_engine_is_blocked_since_when_and_the_exits_line_says_only_the_broker_stops_hold():
    gone = WED - pd.Timedelta(seconds=es.ENGINE_FAIL_S + 300)
    s = status(engine(ts=gone.timestamp()))
    t = text(s)
    assert lines(t)[0].startswith("BLOCKED · icm-demo (demo)")
    b = s.blocks[0]
    assert f"{b.text} · since 14:10 BST (13:10 UTC, 50 min ago)" in t and b.fix in t
    assert es.EXITS_SILENT in t and es.EXITS_OK not in t


def test_every_line_of_every_status_is_from_the_api_and_a_busy_account_stays_phone_short():
    lim = es.RiskLimits()
    s = status(engine(stage="halted", dq_error=True, stale_bars=True, tick_ahead_s=30.0, tick_error=True,
                      data_recovering=True, spread_points=lim.max_spread_points + 1, open_positions=lim.max_positions,
                      account_class="unknown", blackout={"kind": "calendar", "title": "CPI m/m"},
                      entry_blocks=["drawdown_halt", "data_quality_error", "news_blackout", "stale_data",
                                    "tick_from_future", "tick_error", "data_recovering", "spread_too_wide",
                                    "max_positions", "account_class_unknown"]),
               owner_halt="telegram", drift_reasons=["x"])
    t = text(s)
    assert len(lines(t)) <= et.MAX_LINES
    assert "more on the dashboard" in lines(t)[-2]
    assert lines(t)[-1] == s.exits
    shown = [b for b in s.blocks if b.text in t]
    assert shown == s.blocks[:len(shown)]              # the order is the API's: what needs the owner first


def _state(tmp_path: Path, **over: Any) -> Path:
    now = WED.timestamp()
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(engine(
        ts=now, login=12345678, server="ICMarketsSC-Demo", host="10.1.2.3", **over)))
    # the supervisor heartbeat is judged at the status's own `now` (WED), as the engine report is
    (tmp_path / "supervisor.json").write_text(json.dumps({"ts": now - 2, "halt": False, "reasons": []}))
    return tmp_path


def test_the_status_message_is_built_from_the_state_files_like_the_api_and_carries_no_identifiers(tmp_path):
    sd = _state(tmp_path, server_clock={"status": "mismatch", "reason": "x"}, entry_blocks=["server_clock_mismatch"])
    [(account, mode, s)] = es.statuses_from_state(sd, WED, None)
    assert (account, mode, s.state) == ("icm-demo", "demo", "blocked")
    msg = et.status_message(sd, WED, tz=LONDON, settings=None, footer="Approval mode: propose · pending proposals: 0")
    assert text(s) in msg and msg.endswith("Approval mode: propose · pending proposals: 0")
    assert lines(msg)[0] == "Entry status · Wed 14 Oct 15:00 BST (14:00 UTC)"
    for secret in ("12345678", "ICMarketsSC", "10.1.2.3"):
        assert secret not in msg


def test_an_account_name_that_embeds_a_login_is_cut_to_its_last_two_digits():
    assert et.account_name("icm-demo-51234567") == "icm-demo-…67"
    assert et.account_name("icm-demo") == "icm-demo"
    t = text(status(engine()), account="icm-demo-51234567")
    assert "51234567" not in t and lines(t)[0].startswith("OPEN · icm-demo-…67 (demo)")


def test_an_owner_halt_from_control_json_is_named_in_the_status(tmp_path):
    sd = _state(tmp_path)
    (sd / "control.json").write_text(json.dumps({"halted": True, "by": "telegram:42", "reason": "fomc"}))
    msg = et.status_message(sd, WED, tz=LONDON, settings=None)
    assert "BLOCKED" in msg and es.REASONS["owner_halt"].text in msg
    # the API's fix names "this page" (the dashboard); in the chat it says where re-arming happens
    assert "Re-arm on the dashboard (Overview) when ready" in msg and "this page" not in msg
    assert "42" not in msg                             # who halted is never printed (a Telegram id)


def test_no_engine_and_a_broken_build_are_never_shown_as_open(tmp_path, monkeypatch):
    msg = et.status_message(tmp_path, WED, tz=LONDON, settings=None)
    assert "No engine has reported yet" in msg and "OPEN" not in msg
    sd = _state(tmp_path)

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("bug")
    monkeypatch.setattr(es, "account_entry_status", boom)
    msg = et.status_message(sd, WED, tz=LONDON, settings=None)
    assert "BLOCKED · icm-demo" in msg and "Entry status couldn't be worked out" in msg


def test_an_entry_window_reaches_status_in_the_apis_words():
    from tests.test_api_entry_status import window_limits, windows
    s = status(engine(windows=windows(calendar_unavailable=True, tier2_blackout=True,
                                      calendar_unavailable_since=(WED - pd.Timedelta(hours=2)).isoformat()),
                      entry_blocks=["tier2_blackout", "calendar_unavailable"]),
               limits=window_limits())
    t = text(s)
    for b in s.blocks:
        assert f"• {b.text}" in t
    assert "since 13:00 BST (12:00 UTC, 2 h ago)" in t and "Feeds tab" in t
    assert "→ Nothing to do: entries resume 15 min after the release" in t    # no time on the block: the fix says it
    assert t.index("Needs you:") < t.index("• Economic calendar") < t.index("Waits:") < t.index("• Second-tier")


class _Bus:
    def __init__(self, fail: bool = False):
        self.fail = fail

    def control(self) -> Any:
        if self.fail:
            raise OSError("control.json gone")
        from goldbot.telegram.bus import Control
        return Control()

    def pending(self) -> list:
        return []


def test_the_reply_carries_the_footer_and_any_failure_still_answers(tmp_path):
    """Review (trading safety, LOW): /status never answers with nothing. A failing control file, pending list, settings
    load or build replies with the FAILED text."""
    sd = _state(tmp_path)
    ok = et.status_reply(sd, WED, tz=LONDON, bus=_Bus(), load=lambda: None)
    assert ok.endswith("Approval mode: each account's setting · pending proposals: 0") and "OPEN · icm-demo" in ok

    def bad_settings() -> Any:
        raise ValueError("bad yaml")
    assert "OPEN · icm-demo" in et.status_reply(sd, WED, tz=LONDON, bus=_Bus(), load=bad_settings)   # defaults
    assert et.status_reply(sd, WED, tz=LONDON, bus=_Bus(fail=True), load=lambda: None) == et.FAILED


def test_status_names_each_accounts_setting_when_the_owner_mode_is_cleared_and_the_owner_mode_when_set(tmp_path):
    from goldbot.telegram.bus import ApprovalBus
    sd = _state(tmp_path)
    bus = ApprovalBus(tmp_path / "bus")
    bus.set_mode("propose", by="telegram")
    assert "Approval mode: propose · " in et.status_reply(sd, WED, tz=LONDON, bus=bus, load=lambda: None)
    bus.set_mode(None, by="telegram")                       # /mode account (ADR 0041)
    assert "Approval mode: each account's setting · " in et.status_reply(sd, WED, tz=LONDON, bus=bus, load=lambda: None)


def test_a_bad_zone_falls_back_to_utc():
    t = et.account_text("icm-demo", "demo", status(engine(market_closed=True, entry_blocks=["market_closed"], ts=SAT.timestamp()), now=SAT),
                        now=SAT, tz="Not/AZone")
    assert "opens Sun 22:02 UTC (in 1 d 12 h)" in t


def test_the_digest_counts_the_accounts_open_for_entries_without_naming_them(tmp_path):
    """The morning report's line two counts the accounts that can take new trades (the /status words are one tap
    away); it never prints an account slug, login or server (docs/ux/daily-learning-report.md D6)."""
    sd = _state(tmp_path, market_closed=True, entry_blocks=["market_closed"])
    d = build_digest(sd, WED)
    assert "Entries open on 0 of 1" in d.splitlines()[1]
    (tmp_path / "open").mkdir()
    assert "Entries open on 1 of 1" in build_digest(_state(tmp_path / "open"), WED).splitlines()[1]
    assert "12345678" not in d and "ICMarketsSC" not in d and "icm-demo" not in d
