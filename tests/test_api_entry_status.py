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
from goldbot.risk.gate import RiskLimits

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
         "throttle": {"enabled": False}, "sessions": {"server_tz": "Europe/Athens", "source": "static"}}
    e.update(over)
    return e


def status(e: dict, now: pd.Timestamp = WED, **kw):
    args: dict[str, Any] = dict(owner_halt=None, supervisor_reasons=None, drift_reasons=None, budget=None,
                                limits=RiskLimits())
    args.update(kw)
    return es.account_entry_status(e, now=now, **args)


def codes(s) -> list[str]:
    return [b.code for b in s.blocks]


def test_a_healthy_engine_can_take_new_trades_and_exits_keep_running():
    s = status(engine())
    assert (s.state, s.blocks, s.headline) == ("open", [], "Can take new trades")
    assert s.exits.startswith("Exits keep running")


def test_market_closed_says_when_it_reopens_and_that_nothing_needs_doing():
    s = status(engine(market_closed=True, ts=SAT.timestamp()), now=SAT)
    [b] = s.blocks
    assert (b.code, b.text, b.kind, b.at_label) == ("market_closed", "Market closed", "wait", "opens")
    assert b.at == pd.Timestamp("2026-10-18 22:02", tz="UTC").to_pydatetime()     # Monday 01:02 Athens (EEST, UTC+3)
    assert s.state == "waiting" and s.headline == "No new trades for now: Market closed"
    assert "reopens by itself" in b.fix


def test_server_clock_mismatch_points_to_the_runbook_and_needs_the_owner():
    s = status(engine(server_clock={"status": "mismatch", "reason": "server clock reads UTC+3, Europe/Athens gives UTC+2"}))
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
    assert b.text == "trend-g0 paused after 3 losses in a row" and b.at_label == "until"
    assert b.at == pd.Timestamp(until).to_pydatetime() and b.kind == "wait"
    assert s.state == "partial" and s.headline == "Can take new trades, except trend-g0"
    # every agent throttled: nothing can open
    th["agents"]["tsmom-g0"]["blocked"] = ["throttle_entry_rate"]
    s = status(engine(throttle=th))
    assert s.state == "waiting" and codes(s) == ["throttle_loss_streak", "throttle_entry_rate"]
    rate = s.blocks[1]
    assert rate.text == "tsmom-g0 made its 3 trades for today" and rate.at_label == "resets"
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
    # a supervisor halt without reasons still blocks
    assert codes(status(engine(), supervisor_reasons=[])) == ["supervisor_halt"]


def test_engine_flags_map_to_their_gate_reasons():
    lim = RiskLimits()
    s = status(engine(stage="halted", dq_error=True, stale_bars=True, tick_ahead_s=30.0, tick_error=True,
                      data_recovering=True, spread_points=lim.max_spread_points + 1, open_positions=lim.max_positions,
                      account_class="unknown", blackout={"kind": "calendar", "title": "CPI m/m"},
                      bridge_compat={"status": "incompatible", "reason": "bridge commit abc123 lacks loss_per_lot"}))
    assert set(codes(s)) == {"drawdown_halt", "data_quality_error", "stale_data", "tick_from_future", "tick_error",
                             "data_recovering", "spread_too_wide", "max_positions", "account_class_unknown",
                             "news_blackout", "bridge_incompatible"}
    # what needs the owner comes first
    kinds = [b.kind for b in s.blocks]
    assert kinds == sorted(kinds)                                     # "act" < "wait"
    news = next(b for b in s.blocks if b.code == "news_blackout")
    assert news.text == "News blackout: CPI m/m"
    # nothing from the engine's free-text reasons reaches the owner (commits, zones, files, hosts)
    assert "abc123" not in json.dumps(s.model_dump(mode="json"))


def test_a_paper_engine_is_not_blocked_by_an_unknown_account_class():
    assert codes(status(engine(mode="paper", account_class="unknown"))) == []


def test_a_silent_engine_says_since_when_and_that_broker_stops_still_protect_open_trades():
    s = status(engine(ts=(WED - pd.Timedelta(minutes=5)).timestamp(), market_closed=True))
    [b] = s.blocks
    assert (b.code, b.at_label, b.kind) == ("engine_not_reporting", "since", "act")
    assert b.at == (WED - pd.Timedelta(minutes=5)).to_pydatetime()
    assert "stop and target" in s.exits and s.exits != status(engine()).exits


def test_reasons_the_engine_publishes_itself_are_used_and_unknown_ones_render_generically():
    s = status(engine(entry_blocks=["heat_cap", "holiday_thin", "some_new_rule"]))
    by = {b.code: b for b in s.blocks}
    assert by["heat_cap"].text != "heat cap" and by["holiday_thin"].text.startswith("Holiday")
    assert by["some_new_rule"].text == "New entries paused (some new rule)" and by["some_new_rule"].kind == "act"
    assert by["some_new_rule"].runbook == "Engines are running but block every entry"


def test_a_recent_tick_error_the_engine_carried_on_from_is_a_note_not_a_block():
    s = status(engine(tick_errors_total=4, last_tick_error_at=(WED - pd.Timedelta(minutes=4)).isoformat()))
    assert s.state == "open" and s.notes and "carried on" in s.notes[0]


def test_every_gate_and_supervisor_reason_has_owner_text():
    """The class of problem this prevents: a new RiskGate or supervisor reason reaching the owner as a bare code."""
    root = Path(__file__).resolve().parents[1]
    found = set()
    for f in ("goldbot/risk/gate.py", "goldbot/risk/supervisor.py"):
        src = (root / f).read_text()
        found |= set(re.findall(r'reasons\.append\("([a-z0-9_]+)"\)', src))
        found |= set(re.findall(r'reasons=\["([a-z0-9_]+)"\]', src))
        found |= set(re.findall(r'return \["(throttle_[a-z_]+)"\]', src))
        found |= set(re.findall(r'out\.append\("(throttle_[a-z_]+)"\)', src))
    found |= {"no_supervisor_heartbeat", "supervisor_state_unreadable", "supervisor_heartbeat_stale"}
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
        ts=now, server_clock={"status": "mismatch", "reason": "x"}, login=12345678, server="ICMarketsSC-Demo")))
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
                                  {"ts": "soon"}, {"sessions": None, "market_closed": True}, {"blackout": "CPI"}])
def test_junk_engine_fields_never_break_the_status(junk):
    s = status(engine(**junk))
    assert s.state in ("open", "partial", "waiting", "blocked")
