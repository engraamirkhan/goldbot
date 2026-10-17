"""Entry status (docs/ux/entry-status.md): per account, can it take a new trade now, and if not why and what fixes it,
in plain words. Built by goldbot/api/entry_status.py from what the engine publishes in engine_<account>.json, the
supervisor, the owner's control file and the drift watch; the browser only renders it (no policy in the browser)."""
import json
import re
import time
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api import entry_status as es
from goldbot.api.app import create_app
from goldbot.api.auth import totp_code
from goldbot.ops.account_view import LossBudget, LossCap
from goldbot.ops.health import ENGINE_FAIL_S
from goldbot.risk.gate import RiskLimits
from goldbot.risk.supervisor import SupervisorLimits

# Wednesday 14:00 UTC: gold trades (static table: Athens server time 17:00)
WED = pd.Timestamp("2026-10-14 14:00", tz="UTC")
# Saturday 10:00 UTC: closed for the weekend; the static table reopens Monday 01:02 Athens = Sunday 22:02 UTC
SAT = pd.Timestamp("2026-10-17 10:00", tz="UTC")


def engine(**over) -> dict:
    e = {"account": "icm-demo", "broker": "icm", "mode": "demo", "ts": WED.timestamp(), "equity": 1000.0,
         "day_start_equity": 1000.0, "week_start_equity": 1000.0, "balance_closed_hwm": 1000.0, "stage": "normal",
         "open_positions": 0, "account_class": "raw", "currency": "GBP", "spread_points": 20.0, "last_tick_age_s": 1.0,
         "tick_ahead_s": 0.0, "market_closed": False, "session_source": "broker", "tick_error": False,
         "tick_errors_total": 0, "last_tick_error_at": None, "dq_error": False, "stale_bars": False,
         "data_recovering": False, "blackout": None, "server_clock": {"status": "ok", "reason": "fine"},
         "throttle": {"enabled": False}, "sessions": {"server_tz": "Europe/Athens", "source": "static"},
         # BACKLOG 43: the RiskGate's account reasons as the engine publishes them (none: it can trade)
         "entry_blocks": [], "entry_refusals": {}}
    e.update(over)
    return e


def status(e: dict, now: pd.Timestamp = WED, **kw):
    args: dict[str, Any] = dict(owner_halt=None, supervisor_reasons=None, drift_reasons=None, budget=None,
                                limits=RiskLimits(), is_owner=True)
    args.update(kw)
    return es.account_entry_status(e, now=now, **args)


def codes(s) -> list[str]:
    return [b.code for b in s.blocks]


def test_a_healthy_engine_can_take_new_trades_and_exits_keep_running():
    s = status(engine())
    assert (s.state, s.blocks, s.headline) == ("open", [], "Can take new trades")
    assert s.exits.startswith("Exits keep running")


def test_market_closed_says_when_it_reopens_and_that_nothing_needs_doing():
    s = status(engine(market_closed=True, entry_blocks=["market_closed"], ts=SAT.timestamp()), now=SAT)
    [b] = s.blocks
    assert (b.code, b.text, b.kind, b.at_label) == ("market_closed", "Market closed", "wait", "opens")
    assert b.at == pd.Timestamp("2026-10-18 22:02", tz="UTC").to_pydatetime()     # Monday 01:02 Athens (EEST, UTC+3)
    assert s.state == "waiting" and s.headline == "No new trades for now: Market closed"
    assert "reopens by itself" in b.fix


def test_a_live_engine_on_a_closed_market_shows_market_closed_not_stale_data():
    """BACKLOG 59: the weekend heartbeat publishes an hours-old tick and stale bars with market_closed; no fault."""
    s = status(engine(market_closed=True, ts=SAT.timestamp(), last_tick_age_s=13 * 3600.0, stale_bars=True,
                      entry_blocks=["stale_data", "market_closed"]), now=SAT)
    assert codes(s) == ["market_closed"] and s.state == "waiting"
    opened = status(engine(last_tick_age_s=13 * 3600.0, stale_bars=True, entry_blocks=["stale_data"]))   # market hours
    assert "stale_data" in codes(opened)


def test_server_clock_mismatch_points_to_the_runbook_and_needs_the_owner():
    s = status(engine(server_clock={"status": "mismatch", "reason": "server clock reads UTC+3, Europe/Athens gives UTC+2"},
                      entry_blocks=["server_clock_mismatch"]))
    [b] = s.blocks
    assert b.code == "server_clock_mismatch" and b.text == "Broker clock differs from the expected time zone"
    assert b.kind == "act" and b.runbook == "Server clock (US-EU DST gap weeks)"
    assert b.runbook_url and b.runbook_url.endswith("#server-clock-us-eu-dst-gap-weeks")
    assert s.state == "blocked" and s.headline.startswith("No new trades: Broker clock differs")


def test_a_throttled_agent_blocks_only_itself_until_its_cooldown_ends():
    until = "2026-10-14T14:30:00+00:00"
    th: dict[str, Any] = {"enabled": True, "loss_streak": 3, "max_entries_per_day_per_agent": 3, "agents": {
        "trend-g0": {"timeframe": "1h", "loss_streak": 3, "cooldown_until": until, "blocked": ["throttle_loss_streak"]},
        "tsmom-g0": {"timeframe": "4h", "loss_streak": 0, "cooldown_until": None, "blocked": []}}}
    s = status(engine(throttle=th))
    [b] = s.blocks
    assert (b.code, b.scope, b.agent_id) == ("throttle_loss_streak", "agent", "trend-g0")
    assert b.text == "Trend (trend-g0) paused after 3 losses in a row" and b.at_label == "until"
    assert b.at == pd.Timestamp(until).to_pydatetime() and b.kind == "wait"
    assert s.state == "partial" and s.headline == "Can take new trades, except Trend (trend-g0)"
    # every agent throttled: nothing can open
    th["agents"]["tsmom-g0"]["blocked"] = ["throttle_entry_rate"]
    s = status(engine(throttle=th))
    assert s.state == "waiting" and codes(s) == ["throttle_loss_streak", "throttle_entry_rate"]
    rate = s.blocks[1]
    assert rate.text == "Tsmom (tsmom-g0) made its 3 trades for today" and rate.at_label == "resets"
    assert rate.at == pd.Timestamp("2026-10-15", tz="UTC").to_pydatetime()


def _budget(binding: str, period: Literal["today", "this week"], limit: float) -> LossBudget:
    cap = LossCap(name=binding, label="x", period=period, limit=limit, used=limit, remaining=0.0)
    return LossBudget(limit=limit, used=limit, remaining=0.0, binding=binding, binding_label="x", period=period,
                      caps=[cap])


def test_a_spent_loss_limit_names_the_amount_in_the_account_currency_and_when_it_resets():
    s = status(engine(), budget=_budget("absolute daily limit", "today", 50.0))
    [b] = s.blocks
    assert (b.code, b.text, b.at_label) == ("daily_loss_limit_abs", "Today's £50 loss limit reached", "resets")
    assert b.at == pd.Timestamp("2026-10-15", tz="UTC").to_pydatetime() and b.kind == "wait"
    s = status(engine(currency="USD"), budget=_budget("account 5.0% weekly cap", "this week", 51.5))
    [b] = s.blocks
    assert (b.code, b.text) == ("weekly_cap", "This week's $51.50 loss limit reached")
    assert b.at == pd.Timestamp("2026-10-18", tz="UTC").to_pydatetime()             # weeks start Sunday 00:00 UTC
    s = status(engine(), budget=_budget("account 2.0% daily cap", "today", 20.0))
    assert codes(s) == ["daily_cap"]
    s = status(engine(), budget=_budget("combined 1.5% daily cap", "today", 15.0))
    assert codes(s) == ["combined_daily_cap"] and "all accounts together" in s.blocks[0].text.lower()


def test_halts_from_the_owner_supervisor_and_drift_watch_are_named_with_their_fix():
    s = status(engine(), owner_halt="dashboard:o@x.io", supervisor_reasons=["supervisor_heartbeat_stale"],
               drift_reasons=["dd_30d 9% > 1.5 x backtest"])
    assert codes(s) == ["owner_halt", "supervisor_heartbeat_stale", "drift_system_halt"]
    assert all(b.kind == "act" for b in s.blocks) and s.state == "blocked"
    assert "Re-arm" in s.blocks[0].fix
    # a viewer or approver is not told to re-arm (only the owner can)
    viewer = status(engine(), owner_halt="telegram", is_owner=False)
    assert "Re-arm on this page" not in viewer.blocks[0].fix and "owner" in viewer.blocks[0].fix
    # supervisor reasons that are not codes never reach the screen
    junk = status(engine(), supervisor_reasons=["combined_daily_cap", "engine_icm-demo.json bad", "x" * 80])
    assert codes(junk) == ["combined_daily_cap"]
    # a supervisor halt without reasons still blocks
    assert codes(status(engine(), supervisor_reasons=[])) == ["supervisor_halt"]


def test_the_engines_published_reasons_decide_and_the_api_only_words_them():
    """BACKLOG 43: the engine publishes the RiskGate's account reasons (`entry_blocks`); the API words them, with the
    published facts for the details (a spread, a count, a news title), and no longer maps flags itself."""
    lim = RiskLimits()
    flags: dict[str, Any] = dict(stage="halted", dq_error=True, stale_bars=True, tick_ahead_s=30.0, tick_error=True,
                                 data_recovering=True, spread_points=lim.max_spread_points + 1,
                                 open_positions=lim.max_positions, account_class="unknown",
                                 blackout={"kind": "calendar", "title": "CPI m/m"},
                                 bridge_compat={"status": "incompatible", "reason": "bridge commit abc123 lacks x"})
    published = ["drawdown_halt", "data_quality_error", "news_blackout", "rollover", "weekend", "stale_data",
                 "tick_from_future", "tick_error", "bridge_incompatible", "data_recovering", "spread_too_wide",
                 "max_positions", "account_class_unknown"]
    s = status(engine(**flags, entry_blocks=published))
    assert sorted(codes(s)) == sorted(published)
    kinds = [b.kind for b in s.blocks]
    assert kinds == sorted(kinds)                                     # what needs the owner comes first ("act" < "wait")
    by = {b.code: b for b in s.blocks}
    assert by["news_blackout"].text == "News blackout: CPI m/m"
    assert by["spread_too_wide"].text == f"Spread too wide right now (46 points; limit {lim.max_spread_points:.0f})"
    assert by["max_positions"].text == f"Already {lim.max_positions} trades open (limit {lim.max_positions})"
    assert by["rollover"].kind == by["weekend"].kind == "wait"
    # nothing from the engine's free-text reasons reaches the owner (commits, zones, files, hosts)
    assert "abc123" not in json.dumps(s.model_dump(mode="json"))
    # the same flags with nothing published: the gate is not refusing, so neither is the status
    assert status(engine(**flags)).state == "open"


def test_an_engine_that_does_not_publish_its_reasons_is_never_shown_open():
    """An engine from before BACKLOG 43 (or a torn field): the API cannot say why, so it says it does not know."""
    for e in (engine(entry_blocks=None), {k: v for k, v in engine().items() if k != "entry_blocks"},
              engine(entry_blocks="weekend")):
        s = status(e)
        [b] = s.blocks
        assert (b.code, b.kind, s.state) == ("entry_reasons_missing", "act", "blocked")
        assert b.runbook == "Updating the VPS to a new version"
        assert b.text == "Can't check whether new trades are allowed: this engine version doesn't report it"
        assert b.fix == "Update the brain to the current version, then restart the engine."


def test_a_list_with_no_valid_code_is_entry_reasons_missing_never_open():
    """Trading-safety INFO-4: a non-empty entry_blocks of which nothing is a code says the engine refuses for a reason
    the API cannot read; never OPEN."""
    for junk in (["invalid:equity"], ["Bad Code", 3, None], [""]):
        s = status(engine(entry_blocks=junk))
        assert codes(s) == ["entry_reasons_missing"] and s.state == "blocked", junk
    assert status(engine(entry_blocks=[])).state == "open"          # an empty list: the gate refuses nothing


def test_invalid_field_codes_are_never_shown_but_the_invalid_state_is():
    s = status(engine(entry_blocks=["invalid_state", "invalid:equity"]))
    assert codes(s) == ["invalid_state"] and s.state != "open"
    assert s.blocks[0].text == "The broker sent account figures the safety checks can't use"
    assert s.blocks[0].fix == ("Usually clears at the next update; if it lasts, check the MT5 terminal is logged in "
                               "and see the Health tab.")


def test_the_engines_supervisor_halt_adds_nothing_to_the_supervisors_own_reasons():
    s = status(engine(entry_blocks=["supervisor_halt"]), supervisor_reasons=["combined_daily_cap"])
    assert codes(s) == ["combined_daily_cap"]
    # the engine read a halt the API does not see yet: still shown
    assert codes(status(engine(entry_blocks=["supervisor_halt"]))) == ["supervisor_halt"]


def test_a_per_trade_refusal_is_its_agents_own_block_since_when_it_happened():
    at = (WED - pd.Timedelta(minutes=20)).isoformat()
    s = status(engine(entry_refusals={"trend-g0": {"code": "margin_level_floor", "at": at},
                                      "tsmom-g0": {"code": "fx_conversion_unavailable", "at": at},
                                      "junk": {"code": "Not A Code", "at": at}, "bad": "x"}))
    by = {b.agent_id: b for b in s.blocks}
    assert set(by) == {"trend-g0", "tsmom-g0"}
    m = by["trend-g0"]
    assert (m.code, m.scope, m.at_label, m.kind) == ("margin_level_floor", "agent", "since", "wait")
    assert m.text == "Trend (trend-g0): last entry refused, not enough free margin"
    assert m.at == pd.Timestamp(at).to_pydatetime()
    assert by["tsmom-g0"].kind == "act"
    assert by["tsmom-g0"].text == "Tsmom (tsmom-g0): last entry refused, couldn't convert dollars to the account currency"
    assert s.state == "partial" and s.headline == "Can take new trades, except Trend (trend-g0), Tsmom (tsmom-g0)"


@pytest.mark.parametrize("code, reason", [
    ("daily_loss_limit_abs", "this trade could lose more than is left of today's loss limit"),
    ("margin_level_floor", "not enough free margin"), ("heat_cap", "too much risk already open"),
    ("combined_exposure_cap", "enough gold already open across accounts"),
    ("min_lot_exceeds_risk", "the smallest trade would risk too much for this balance"),
    ("fx_conversion_unavailable", "couldn't convert dollars to the account currency")])
def test_every_per_trade_refusal_has_its_own_words_not_the_account_reasons(code, reason):
    """ui-ux review 2026-10-16: a refused trade is not a spent limit, so a per-trade daily_loss_limit_abs never says the
    limit is reached; each per-trade refusal is worded for the trade."""
    from goldbot.risk.gate import PER_TRADE_REFUSALS
    assert set(es.REFUSAL_TEXT) == PER_TRADE_REFUSALS
    s = status(engine(entry_refusals={"session_open-g1-abc": {"code": code, "at": WED.isoformat()}}))
    [b] = s.blocks
    assert b.text == f"Session open (session_open-g1-abc): last entry refused, {reason}"
    assert "limit reached" not in b.text.lower()


def test_a_refused_agent_outside_the_throttle_list_never_makes_it_blocked_for_everyone():
    """Code review LOW-2 (558588e): the throttle list is the only roster; an agent named only by entry_refusals widens the
    known agents, so one agent still free keeps the account partly open."""
    th: dict[str, Any] = {"enabled": True, "loss_streak": 3, "max_entries_per_day_per_agent": 3, "agents": {
        "trend-g0": {"loss_streak": 3, "cooldown_until": None, "blocked": ["throttle_loss_streak"]},
        "tsmom-g0": {"loss_streak": 0, "cooldown_until": None, "blocked": []}}}
    s = status(engine(throttle=th, entry_refusals={"session_open-g0": {"code": "heat_cap", "at": WED.isoformat()}}))
    assert s.state == "partial" and "Tsmom" not in s.headline
    # every known agent held (roster and refused alike): nothing can open
    th["agents"]["tsmom-g0"]["blocked"] = ["throttle_entry_rate"]
    assert status(engine(throttle=th, entry_refusals={"session_open-g0": {"code": "heat_cap",
                                                                          "at": WED.isoformat()}})).state == "waiting"
    # no roster published (throttle off): refusals alone never claim every strategy is held
    assert status(engine(entry_refusals={"trend-g0": {"code": "heat_cap", "at": WED.isoformat()}})).state == "partial"


def test_the_agent_label_names_the_family_then_the_id():
    assert es.agent_label("trend-g0") == "Trend (trend-g0)"
    assert es.agent_label("session_open-g2-deadbeef") == "Session open (session_open-g2-deadbeef)"
    assert es.agent_label("solo") == "Solo (solo)"


def test_a_silent_engine_while_the_market_is_open_past_the_health_threshold_needs_the_owner():
    gone = WED - pd.Timedelta(seconds=ENGINE_FAIL_S + 300)
    s = status(engine(ts=gone.timestamp()))
    [b] = s.blocks
    assert (b.code, b.at_label, b.kind) == ("engine_not_reporting", "since", "act")
    assert b.at == gone.to_pydatetime() and s.state == "blocked"
    assert "stop and target" in s.exits and s.exits != status(engine()).exits


def test_a_short_silence_is_status_unknown_never_open():
    quiet = WED - pd.Timedelta(seconds=SupervisorLimits().heartbeat_max_age_s + 240)
    s = status(engine(ts=quiet.timestamp()))
    [b] = s.blocks
    assert (b.code, b.kind, b.at_label) == ("status_unknown", "wait", "since")
    assert s.state == "waiting" and s.headline.startswith("No new trades for now")


def test_an_engine_idle_while_the_market_is_closed_is_market_closed_not_a_fault():
    """The engine writes nothing while the market is closed: never blame it (review H1)."""
    last = SAT - pd.Timedelta(hours=15)
    s = status(engine(ts=last.timestamp(), market_closed=False), now=SAT)
    [b] = s.blocks
    assert (b.code, b.kind, b.at_label) == ("market_closed", "wait", "opens")
    assert b.text == "Market closed (engine idle while closed)"
    assert b.at == pd.Timestamp("2026-10-18 22:02", tz="UTC").to_pydatetime()
    assert s.state == "waiting" and s.as_of == last.to_pydatetime()


def utc(s: str):
    return pd.Timestamp(s, tz="UTC").to_pydatetime()


@pytest.mark.parametrize("now,until", [
    ("2026-10-14 20:57", "2026-10-14 21:05"),       # 23:57 Athens (EEST): the window runs past server midnight
    ("2026-10-14 21:02", "2026-10-14 21:05"),       # 00:02 Athens: the same window, after midnight
    ("2026-11-04 21:57", "2026-11-04 22:05"),       # 23:57 Athens in winter (EET, UTC+2)
])
def test_the_rollover_says_until_when_server_midnight_plus_the_engines_minutes(now, until):
    """BACKLOG 65 (b): rollover is "+-rollover_min of 00:00 server" (runner `_server_clock`), so it clears at server
    midnight + rollover_min, placed with the server zone the engine publishes in `sessions`; the time is its own
    element so the dashboard and Telegram show it in the owner's zone, as market_closed does."""
    at = pd.Timestamp(now, tz="UTC")
    s = status(engine(entry_blocks=["rollover"], ts=at.timestamp()), now=at)
    [b] = s.blocks
    assert (b.code, b.text, b.at_label, b.at) == ("rollover", "Daily rollover around broker midnight", "until", utc(until))
    assert s.state == "waiting" and s.clears_at == utc(until)


def test_the_rollover_follows_a_new_york_plus_seven_server_clock():
    at = pd.Timestamp("2026-10-14 20:58", tz="UTC")       # 16:58 New York (EDT) = 23:58 NY+7
    s = status(engine(entry_blocks=["rollover"], ts=at.timestamp(), sessions={"server_tz": "NY+7"}), now=at)
    assert s.blocks[0].at == utc("2026-10-14 21:05")


def test_the_rollover_minutes_are_the_engines_own():
    from goldbot.engine.runner import EngineConfig
    assert es.ROLLOVER_MIN == EngineConfig.model_fields["rollover_min"].default


@pytest.mark.parametrize("now", ["2026-10-16 19:00", "2026-10-17 10:00"])
def test_the_weekend_cut_says_when_the_market_opens_again(now):
    """BACKLOG 65 (b): from the Friday cut (21:30 server, while the market is still open) and through the weekend the
    block says when the week reopens: the first open minute after the weekend close on the published session table
    (Monday 01:02 Athens = Sunday 22:02 UTC), never "now" because the market happens to be open at the cut."""
    at = pd.Timestamp(now, tz="UTC")
    s = status(engine(entry_blocks=["weekend"], ts=at.timestamp()), now=at)
    [b] = s.blocks
    assert (b.code, b.at_label, b.at) == ("weekend", "opens", utc("2026-10-18 22:02"))
    assert b.text == "Weekend: no new trades from Friday evening"


def test_a_report_stamped_in_the_future_counts_as_not_reporting():
    s = status(engine(ts=(WED + pd.Timedelta(minutes=5)).timestamp()))
    assert codes(s) == ["engine_not_reporting"] and s.blocks[0].at is None


def test_reasons_the_engine_publishes_itself_are_used_and_unknown_ones_render_generically():
    s = status(engine(entry_blocks=["heat_cap", "holiday_thin", "some_new_rule"]))
    by = {b.code: b for b in s.blocks}
    assert by["heat_cap"].text != "heat cap" and by["holiday_thin"].text.startswith("Holiday")
    assert by["some_new_rule"].text == "New entries paused (some new rule)" and by["some_new_rule"].kind == "act"
    assert by["some_new_rule"].runbook == "Engines are running but block every entry"


def test_a_recent_tick_error_the_engine_carried_on_from_is_a_note_not_a_block():
    s = status(engine(tick_errors_total=4, last_tick_error_at=(WED - pd.Timedelta(minutes=4)).isoformat()))
    assert s.state == "open" and s.notes and "carried on" in s.notes[0]


def test_every_spent_cap_is_its_own_block_and_the_headline_says_when_the_last_clears():
    caps = [LossCap(name="absolute daily limit", label="x", period="today", limit=50.0, used=50.0, remaining=0.0),
            LossCap(name="account 5.0% weekly cap", label="x", period="this week", limit=50.0, used=52.0, remaining=0.0),
            LossCap(name="combined 1.5% daily cap", label="x", period="today", limit=15.0, used=5.0, remaining=10.0)]
    b = LossBudget(limit=50.0, used=50.0, remaining=0.0, binding="absolute daily limit", binding_label="x",
                   period="today", caps=caps)
    s = status(engine(), budget=b, supervisor_reasons=["combined_weekly_cap"])
    assert codes(s)[:2] == ["daily_loss_limit_abs", "weekly_cap"]
    by = {x.code: x for x in s.blocks}
    assert by["daily_loss_limit_abs"].at == pd.Timestamp("2026-10-15", tz="UTC").to_pydatetime()
    assert by["weekly_cap"].at == pd.Timestamp("2026-10-18", tz="UTC").to_pydatetime()
    assert s.clears_at == pd.Timestamp("2026-10-18", tz="UTC").to_pydatetime()
    # something that needs the owner, or a block without a time: no promise of when it clears
    assert status(engine(), budget=b, owner_halt="x").clears_at is None


def test_the_drawdown_texts_come_from_the_limits():
    s = status(engine(stage="halted", entry_blocks=["drawdown_halt"]), limits=RiskLimits(dd_stage2=0.10),
               supervisor_reasons=["combined_drawdown_halt"], combined_dd=0.15)
    by = {x.code: x for x in s.blocks}
    assert "10% below its high" in by["drawdown_halt"].text
    assert "15% below their high" in by["combined_drawdown_halt"].text


def test_a_blackout_without_a_title_still_blocks():
    s = status(engine(blackout={"kind": "calendar"}, entry_blocks=["news_blackout"]))
    assert [(b.code, b.text) for b in s.blocks] == [("news_blackout", "News blackout")]


def test_a_failed_build_says_status_unknown_and_points_to_health():
    s = es.unknown_status()
    assert s.state == "blocked" and [b.code for b in s.blocks] == ["status_unknown"]
    assert s.blocks[0].kind == "act" and s.blocks[0].runbook == "Health check"
    assert s.exits == es.EXITS_SILENT          # nothing is known, so promise only the broker's stops (N2)


def test_every_owner_text_is_for_a_real_or_declared_reason():
    """The other direction: no wording for a reason nothing raises (typos, renamed codes), unless declared."""
    root = Path(__file__).resolve().parents[1]
    src = "".join((root / f).read_text() for f in ("goldbot/risk/gate.py", "goldbot/risk/supervisor.py",
                                                   "goldbot/engine/runner.py"))
    for code in es.REASONS:
        assert f'"{code}"' in src or code in es.API_ONLY | es.FUTURE, code
    assert not (es.API_ONLY & es.FUTURE)


def test_every_gate_and_supervisor_reason_has_owner_text():
    """The class of problem this prevents: a new RiskGate or supervisor reason reaching the owner as a bare code."""
    root = Path(__file__).resolve().parents[1]
    found = set()
    for f in ("goldbot/risk/gate.py", "goldbot/risk/supervisor.py"):
        src = (root / f).read_text()
        found |= set(re.findall(r'reasons\.append\("([a-z0-9_]+)"\)', src))
        found |= set(re.findall(r'reasons=\["([a-z0-9_]+)"\]', src))
        found |= set(re.findall(r'return \["(throttle_[a-z_]+)"\]', src))
        # every helper that collects reasons into `out` (throttle, entry windows, open risk): not only throttle_*
        found |= set(re.findall(r'out\.append\("([a-z0-9_]+)"\)', src))
        # the engines' own halt reasons (Supervisor.engine_should_halt), so a new one cannot be missed either
        found |= set(re.findall(r'return True, "([a-z0-9_]+)"', src))
    found |= {"no_supervisor_heartbeat", "supervisor_state_unreadable", "supervisor_heartbeat_stale",
              "supervisor_heartbeat_future"}
    assert len(found) > 25
    assert found <= set(es.REASONS), sorted(found - set(es.REASONS))
    for code, r in es.REASONS.items():
        assert r.text and r.fix and "_" not in r.text.replace("{agent}", ""), code


# ----------------------------------------------------------------------------- through the API
def _owner(c: TestClient, app) -> dict[str, str]:
    st = app.state.st
    uri = c.post("/api/auth/setup", json={"setup_code": st.auth.setup_code, "email": "o@x.io",
                                           "password": "owner password 123"}).json()["totp_uri"]
    secret = uri.split("secret=")[1].split("&")[0]
    tok = c.post("/api/auth/login", json={"email": "o@x.io", "password": "owner password 123",
                                          "totp": totp_code(secret)}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_accounts_carry_the_entry_status_from_engine_supervisor_and_owner_halt(tmp_path):
    now = time.time()
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(engine(
        ts=now, server_clock={"status": "mismatch", "reason": "x"}, entry_blocks=["server_clock_mismatch"],
        login=12345678, server="ICMarketsSC-Demo")))
    (tmp_path / "supervisor.json").write_text(json.dumps({"ts": now, "halt": False, "reasons": []}))
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    h = _owner(c, app)
    [row] = c.get("/api/accounts", headers=h).json()
    st = row["entry_status"]
    assert st["state"] == "blocked" and [b["code"] for b in st["blocks"]] == ["server_clock_mismatch"]
    assert "12345678" not in json.dumps(st) and "ICMarketsSC" not in json.dumps(st)
    c.post("/api/halt", json={"reason": "fomc"}, headers=h)
    [row] = c.get("/api/accounts", headers=h).json()
    assert [b["code"] for b in row["entry_status"]["blocks"]] == ["owner_halt", "server_clock_mismatch"]


def test_a_missing_supervisor_blocks_entries_as_the_engine_sees_it(tmp_path):
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(engine(ts=time.time())))
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    [row] = c.get("/api/accounts", headers=_owner(c, app)).json()
    assert [b["code"] for b in row["entry_status"]["blocks"]] == ["no_supervisor_heartbeat"]


@pytest.mark.parametrize("junk", [{"throttle": "x"}, {"server_clock": 3}, {"bridge_compat": []}, {"entry_blocks": "a"},
                                  {"ts": "soon"}, {"sessions": None, "market_closed": True}, {"blackout": "CPI"},
                                  {"ts": None}, {"sessions": {"broker_windows": "x"}, "ts": 0}])
def test_junk_engine_fields_never_break_the_status(junk):
    s = status(engine(**junk))
    assert s.state in ("open", "partial", "waiting", "blocked")


def test_a_status_that_cannot_be_built_is_never_shown_as_open(tmp_path, monkeypatch):
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(engine(ts=time.time())))
    (tmp_path / "supervisor.json").write_text(json.dumps({"ts": time.time(), "halt": False, "reasons": []}))

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("bug")
    monkeypatch.setattr(es, "account_entry_status", boom)
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    [row] = c.get("/api/accounts", headers=_owner(c, app)).json()
    assert row["entry_status"]["state"] == "blocked"
    assert [b["code"] for b in row["entry_status"]["blocks"]] == ["status_unknown"]


# ----------------------------------------------------------------------------- entry windows (BACKLOG 17/18)
def windows(**over: Any) -> dict:
    w: dict[str, Any] = {"enabled": {"tier2": True, "reopen": True, "holidays": True}, "tier2_blackout": False,
                         "reopen_window": False, "holiday_thin": False, "calendar_unavailable": False,
                         "calendar_unavailable_since": None, "calendar_unavailable_reason": None}
    w.update(over)
    return w


def window_limits() -> RiskLimits:
    from goldbot.config import HolidaySettings, ReopenSettings, Tier2Blackout
    return RiskLimits(tier2=Tier2Blackout(enabled=True, before_min=5, after_min=15),
                      reopen=ReopenSettings(enabled=True, after_daily_reopen_min=10, after_monday_open_min=30),
                      holidays=HolidaySettings(enabled=True))


WINDOW_CODES = ("tier2_blackout", "reopen_window", "holiday_thin", "calendar_unavailable")


def test_window_reasons_are_live_not_future():
    assert not (set(WINDOW_CODES) & es.FUTURE)
    assert set(WINDOW_CODES) <= set(es.REASONS)


def test_a_tier2_blackout_says_how_long_entries_wait_around_the_release():
    s = status(engine(windows=windows(tier2_blackout=True), entry_blocks=["tier2_blackout"]), limits=window_limits())
    [b] = s.blocks
    assert (b.code, b.kind, b.text) == ("tier2_blackout", "wait", "Second-tier US news: entries paused around the release")
    assert b.fix == "Nothing to do: entries resume 15 min after the release (the pause starts 5 min before it)."
    assert s.state == "waiting"


def test_the_reopen_window_says_until_when_from_the_session_table():
    # the weekly open: Monday 01:02 Athens = Sunday 22:02 UTC; the weekly window (30 min) ends 22:32 UTC
    now = pd.Timestamp("2026-10-18 22:10", tz="UTC")
    s = status(engine(ts=now.timestamp(), windows=windows(reopen_window=True), entry_blocks=["reopen_window"]), now=now, limits=window_limits())
    [b] = s.blocks
    assert (b.code, b.kind, b.at_label) == ("reopen_window", "wait", "until")
    assert b.at == pd.Timestamp("2026-10-18 22:32", tz="UTC").to_pydatetime()
    assert s.clears_at == b.at
    # a reopen window the session table cannot place (the engine failed closed) still blocks, with no time promised
    s = status(engine(windows=windows(reopen_window=True), entry_blocks=["reopen_window"]), limits=window_limits())
    assert codes(s) == ["reopen_window"] and s.blocks[0].at is None


def test_a_holiday_names_the_day_and_clears_at_the_first_normal_server_day():
    now = pd.Timestamp("2026-11-26 14:00", tz="UTC")             # Thanksgiving; the 27th is listed too
    s = status(engine(ts=now.timestamp(), windows=windows(holiday_thin=True), entry_blocks=["holiday_thin"]), now=now, limits=window_limits())
    [b] = s.blocks
    assert (b.code, b.kind, b.at_label) == ("holiday_thin", "wait", "until")
    assert b.text == "Holiday trading (Thanksgiving Day): thin market, entries paused"
    assert b.at == pd.Timestamp("2026-11-27 22:00", tz="UTC").to_pydatetime()   # Sat 00:00 Athens (EET, UTC+2)


def test_an_unavailable_calendar_needs_the_owner_and_says_which_list_and_since_when():
    since = (WED - pd.Timedelta(hours=2)).isoformat()
    s = status(engine(windows=windows(calendar_unavailable=True, calendar_unavailable_since=since,
                                      calendar_unavailable_reason="newest fetch 2026-10-01 is not within 7 days"),
                      entry_blocks=["calendar_unavailable"]),
               limits=window_limits())
    [b] = s.blocks
    assert (b.code, b.kind, b.at_label) == ("calendar_unavailable", "act", "since")
    assert b.at == pd.Timestamp(since).to_pydatetime() and "economic calendar" in b.text.lower()
    assert "Feeds" in b.fix and s.state == "blocked"
    assert "2026-10-01" not in json.dumps(s.model_dump(mode="json"))       # the engine's free text never shows
    hol = status(engine(windows=windows(calendar_unavailable=True,
                                        calendar_unavailable_reason="holiday list cme-metals-v2 does not cover 2029-01-02"),
                        entry_blocks=["calendar_unavailable"]),
                 limits=window_limits())
    assert "holiday list" in hol.blocks[0].text.lower() and "holidays_cme_metals.yaml" in hol.blocks[0].fix
    assert "cme-metals-v2" not in json.dumps(hol.model_dump(mode="json"))


def test_a_raised_window_flag_the_gate_does_not_refuse_on_never_blocks():
    """The engine's error path raises reopen_window and calendar_unavailable even for windows that are off; the
    RiskGate ignores them (`window_reasons`), so the engine does not list them and the status does not show them."""
    off = windows(enabled={"tier2": False, "reopen": False, "holidays": False}, tier2_blackout=True, reopen_window=True,
                  holiday_thin=True, calendar_unavailable=True)
    assert codes(status(engine(windows=off), limits=window_limits())) == []


def test_the_engines_list_decides_even_when_the_settings_read_here_say_off():
    """Review (trading safety, MEDIUM): the running gate decides. A settings edit without a restart, or settings that
    fail to load (RiskLimits() defaults: every window off), must never hide a window the gate is refusing on."""
    s = status(engine(windows=windows(reopen_window=True, calendar_unavailable=True),
                      entry_blocks=["reopen_window", "calendar_unavailable"]))          # default limits: all off
    assert codes(s) == ["calendar_unavailable", "reopen_window"] and s.state == "blocked"
    for flag in ("tier2_blackout", "reopen_window", "holiday_thin", "calendar_unavailable"):
        s = status(engine(windows=windows(**{flag: True}), entry_blocks=[flag]), limits=RiskLimits())
        assert s.state != "open" and codes(s) == [flag], flag
    # no `windows` published at all: the listed reason is still shown, in its plain words
    s = status(engine(entry_blocks=["reopen_window"]), limits=RiskLimits())
    assert codes(s) == ["reopen_window"]


def test_times_the_engine_publishes_win_over_ones_worked_out_here():
    w = windows(tier2_blackout=True, reopen_window=True, holiday_thin=True,
                tier2_until="2026-10-14T14:12:00+00:00", reopen_until="2026-10-14T14:07:00+00:00",
                holiday_name="Day after Thanksgiving", holiday_until="2026-10-15T21:00:00+00:00")
    s = status(engine(windows=w, entry_blocks=["tier2_blackout", "reopen_window", "holiday_thin"]), limits=RiskLimits())
    by = {b.code: b for b in s.blocks}
    assert (by["tier2_blackout"].at_label, by["tier2_blackout"].at) == \
        ("until", pd.Timestamp("2026-10-14 14:12", tz="UTC").to_pydatetime())
    assert by["reopen_window"].at == pd.Timestamp("2026-10-14 14:07", tz="UTC").to_pydatetime()
    assert by["holiday_thin"].text == "Holiday trading (Day after Thanksgiving): thin market, entries paused"
    assert by["holiday_thin"].at == pd.Timestamp("2026-10-15 21:00", tz="UTC").to_pydatetime()
    assert s.clears_at == pd.Timestamp("2026-10-15 21:00", tz="UTC").to_pydatetime()
    # the fallback wording from the limits read here never claims minutes it does not know
    assert by["tier2_blackout"].fix == es.REASONS["tier2_blackout"].fix


@pytest.mark.parametrize("junk", [{"windows": "x"}, {"windows": {"enabled": "all", "reopen_window": True}},
                                  {"windows": {"enabled": {"holidays": True}, "holiday_thin": "yes"}}])
def test_junk_windows_never_break_the_status(junk):
    assert status(engine(**junk), limits=window_limits()).state in ("open", "partial", "waiting", "blocked")


def test_an_unconfirmed_order_is_worded_with_the_time_it_was_sent():
    """BACKLOG 72 (docs/ux/order-not-confirmed.md, "Entry refusal"): the account-level reason, since the oldest
    unresolved order was sent, a wait; never the raw code."""
    sent = "2026-10-07T13:52:00+00:00"
    b = es._engine_block("unconfirmed_order_pending", {"unconfirmed_orders": {"count": 1, "oldest_utc": sent}},
                         pd.Timestamp("2026-10-07 14:00", tz="UTC"), RiskLimits())
    assert b.text == "An earlier order isn't confirmed by the broker yet" and b.kind == "wait"
    assert b.at_label == "since" and b.at is not None and b.at.isoformat().startswith("2026-10-07T13:52")
    assert b.fix == ("Nothing to do: the engine is looking it up at the broker. If it lasts over 10 minutes, check MT5 "
                     "for an open trade and its stop.")
    assert es.refusal_words("unconfirmed_order_pending") == "an earlier order isn't confirmed by the broker yet"
