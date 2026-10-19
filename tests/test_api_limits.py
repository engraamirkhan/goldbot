"""Limits card (docs/ux/limits-and-tradingview.md, card A): `GET /api/limits` and `GET /api/limits/changes`.

Read-only views of what the engines publish (state/engine_<account>.json `limits`, `limits_current`,
`limits_reverted`) and of core.db's change logs: ADR 0032 `limits_changes` (one snapshot of the current limits per
change) and `limits_state` (the revert), ADR 0031 `adaptive_changes` (one row per field). Nothing here writes."""
import json
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api.app import create_app
from goldbot.db.connection import Database
from goldbot.ops import limits_log
from goldbot.risk.limits_profile import LimitsChange, LimitsState, LimitsStore, RiskLimitsCurrent
from tests.test_api_views import _login

PROFILE = "demo-2026-10-18"
T0 = pd.Timestamp("2026-10-17 08:12", tz="UTC")


def engine_file(state: Path, **over: object) -> dict:
    e: dict = {"account": "icm-demo", "mode": "demo", "ts": time.time(), "currency": "GBP", "balance": 1000.0,
               "equity": 988.0, "day_start_equity": 1000.0, "week_start_equity": 1000.0, "balance_closed_hwm": 1000.0,
               "stage": "normal", "daily_loss_limit": 50.0, "daily_loss_limit_abs": 50.0,
               "lot_terms": {"contract_oz": 100, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 50.0,
                             "stops_level_points": 0},
               "limits": {"daily_cap": 0.05, "weekly_cap": 0.10, "min_lot_risk_cap": 0.01, "daily_loss_limit_abs": 50.0,
                          "min_lot_risk_account": 8.0, "risk_per_trade": 0.005},
               "limits_current": {"profile": PROFILE, "daily_cap": 0.05, "weekly_cap": 0.10, "min_lot_risk_cap": 0.01,
                                  "project_open_risk": True, "reason": "static: the owner's ceilings",
                                  "evidence_ids": []},
               "limits_reverted": False}
    e.update(over)
    (state / f"engine_{e['account']}.json").write_text(json.dumps(e))
    return e


def cur(daily: float, weekly: float, mlr: float | None, reason: str, profile: str = PROFILE) -> RiskLimitsCurrent:
    return RiskLimitsCurrent(profile=profile, daily_cap=daily, weekly_cap=weekly, min_lot_risk_cap=mlr,
                             project_open_risk=True, reason=reason)


def log(state: Path, ts: pd.Timestamp, c: RiskLimitsCurrent, prev: str | None = None, account: str = "icm-demo") -> None:
    assert LimitsStore(state).log_change(LimitsChange(account_id=account, ts_utc=ts, previous_profile=prev, current=c))


@pytest.fixture
def client(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", docs_dir=tmp_path / "docs",
                     owner_email="o@x.io")
    c = TestClient(app)
    return c, _login(c, app), state


def test_limits_need_a_session_and_are_empty_before_any_engine_reports(client):
    c, h, _ = client
    assert c.get("/api/limits").status_code == 401
    assert c.get("/api/limits/changes").status_code == 401
    v = c.get("/api/limits", headers=h["viewer"]).json()
    assert v["accounts"] == [] and c.get("/api/limits/changes", headers=h["viewer"]).json() == []


def test_the_card_shows_the_current_caps_in_money_and_percent_with_what_is_used(client):
    c, h, state = client
    engine_file(state)
    (a,) = c.get("/api/limits", headers=h["viewer"]).json()["accounts"]
    assert (a["account_id"], a["mode"], a["profile"], a["source"], a["adr"]) == ("icm-demo", "demo", PROFILE, "static",
                                                                                  "0032")
    assert (a["daily_cap_pct"], a["daily_limit"], a["daily_used"]) == (0.05, 50.0, 12.0)
    assert (a["weekly_cap_pct"], a["weekly_limit"], a["weekly_used"]) == (0.10, 100.0, 12.0)
    assert (a["absolute_daily_limit"], a["currency"], a["balance"]) == (50.0, "GBP", 1000.0)
    assert (a["day_start_equity"], a["week_start_equity"]) == (1000.0, 1000.0)
    assert (a["min_lot"], a["min_lot_risk"], a["min_lot_risk_cap"]) == (0.01, 8.0, 0.01)
    assert a["cap_hit"] == "none" and a["revert_active"] is False and a["stale"] is False and a["error"] is None


def test_why_this_limit_today_shows_each_strategys_rate_and_the_same_day_cuts(client):
    c, h, state = client
    engine_file(state, risk_today={"k_vol": 0.5, "k_dd": 1.0, "k_day": 0.5, "scale": 0.25, "base": 0.005,
                                   "ceiling": 0.01, "strategies": {"v1": {"decided": 0.008, "applied": 0.002,
                                                                          "reason": "22 demo days, LCB > 0"},
                                                                   "v2": {"decided": None, "applied": 0.00125}}})
    (a,) = c.get("/api/limits", headers=h["viewer"]).json()["accounts"]
    r = a["risk_today"]
    assert (r["base"], r["ceiling"], r["k_vol"], r["k_dd"], r["k_day"], r["scale"]) == (0.005, 0.01, 0.5, 1.0, 0.5,
                                                                                          0.25)
    assert [(s["version"], s["decided"], s["applied"], s["reason"]) for s in r["strategies"]] == [
        ("v1", 0.008, 0.002, "22 demo days, LCB > 0"), ("v2", None, 0.00125, None)]
    assert "high volatility" in r["why"] and "half the daily allowance lost" in r["why"] and "0.25" in r["why"]


def test_without_dynamic_limits_or_with_a_corrupt_why_there_is_no_risk_today(client):
    c, h, state = client
    engine_file(state)
    assert c.get("/api/limits", headers=h["viewer"]).json()["accounts"][0]["risk_today"] is None
    engine_file(state, risk_today=["junk"])
    assert c.get("/api/limits", headers=h["viewer"]).json()["accounts"][0]["risk_today"] is None


def test_the_day_resets_at_midnight_utc_and_the_week_on_sunday_midnight_utc():
    now = pd.Timestamp("2026-10-21 13:05", tz="UTC")              # a Wednesday
    assert limits_log.next_day_reset(now) == pd.Timestamp("2026-10-22", tz="UTC")
    assert limits_log.next_week_reset(now) == pd.Timestamp("2026-10-25", tz="UTC")      # Sunday 00:00 UTC (gate.py)
    sunday = pd.Timestamp("2026-10-25 00:00", tz="UTC")
    assert limits_log.next_week_reset(sunday) == pd.Timestamp("2026-11-01", tz="UTC")


def test_the_absolute_daily_limit_binds_the_money_figure_above_a_thousand_pounds(client):
    c, h, state = client
    engine_file(state, day_start_equity=2000.0, equity=2000.0, daily_loss_limit=50.0)
    (a,) = c.get("/api/limits", headers=h["viewer"]).json()["accounts"]
    assert a["daily_limit"] == 50.0 and a["daily_cap_pct"] == 0.05            # 5% of £2,000 is £100: the £50 wins


def test_a_spent_daily_cap_is_reported_as_cap_hit(client):
    c, h, state = client
    engine_file(state, equity=949.0)
    (a,) = c.get("/api/limits", headers=h["viewer"]).json()["accounts"]
    assert a["cap_hit"] == "daily" and a["daily_used"] == 51.0


def test_a_revert_shows_since_when_and_why_from_core_db(client):
    c, h, state = client
    engine_file(state, limits_reverted=True,
                limits_current={"profile": "default", "daily_cap": 0.02, "weekly_cap": 0.05, "min_lot_risk_cap": None,
                                "project_open_risk": True, "reason": "reverted", "evidence_ids": []})
    LimitsStore(state).save(LimitsState(account_id="icm-demo", reverted=True, since_utc=T0, reason="stage size_down"))
    (a,) = c.get("/api/limits", headers=h["viewer"]).json()["accounts"]
    assert a["revert_active"] is True and a["source"] == "revert" and a["profile"] == "default"
    assert pd.Timestamp(a["revert_since"]) == T0 and "8%" in a["revert_reason"]


def test_an_engine_without_published_limits_reads_as_an_error_not_a_guess(client):
    c, h, state = client
    engine_file(state, limits=None, limits_current=None)
    (a,) = c.get("/api/limits", headers=h["viewer"]).json()["accounts"]
    assert a["error"] and a["daily_limit"] is None and a["daily_cap_pct"] is None


def test_an_old_engine_file_is_stale(client):
    c, h, state = client
    engine_file(state, ts=time.time() - 300)
    (a,) = c.get("/api/limits", headers=h["viewer"]).json()["accounts"]
    assert a["stale"] is True


def test_the_supervisor_envelope_and_the_combined_caps_now_are_shown(client):
    c, h, state = client
    engine_file(state)
    (state / "supervisor.json").write_text(json.dumps({"ts": time.time(), "daily_cap": 0.0375, "weekly_cap": 0.075}))
    s = c.get("/api/limits", headers=h["viewer"]).json()["supervisor"]
    assert (s["daily_cap"], s["weekly_cap"]) == (0.0375, 0.075)
    assert (s["envelope_daily"], s["envelope_weekly"]) == (0.0375, 0.075)
    assert (s["default_daily"], s["default_weekly"]) == (0.015, 0.04)


def test_a_snapshot_change_becomes_one_row_per_field_that_moved_with_a_plain_reason(client):
    c, h, state = client
    engine_file(state)
    log(state, T0 - pd.Timedelta(days=1), cur(0.02, 0.05, None, "default", "default"))
    log(state, T0, cur(0.05, 0.10, 0.01, "static: the owner's ceilings"), prev="default")
    rows = c.get("/api/limits/changes?account=icm-demo", headers=h["viewer"]).json()
    assert [(r["field"], r["old_value"], r["new_value"]) for r in rows] == [
        ("daily_cap", 0.02, 0.05), ("weekly_cap", 0.05, 0.10), ("min_lot_risk_cap", None, 0.01)]
    assert {r["source"] for r in rows} == {"static"} and {r["adr"] for r in rows} == {"0032"}
    assert all(r["reason"].endswith(".") and "_" not in r["reason"] for r in rows)
    (a,) = c.get("/api/limits", headers=h["viewer"]).json()["accounts"]
    assert a["last_change"]["field"] in {"daily_cap", "weekly_cap", "min_lot_risk_cap"}


def test_a_restart_that_logs_the_same_limits_again_is_not_a_change(client):
    c, h, state = client
    log(state, T0, cur(0.05, 0.10, 0.01, "static: the owner's ceilings"))
    log(state, T0 + pd.Timedelta(hours=1), cur(0.05, 0.10, 0.01, "static: the owner's ceilings"))
    assert c.get("/api/limits/changes", headers=h["viewer"]).json() == []


def test_the_owner_rearm_after_a_revert_is_attributed_to_the_owner(client):
    c, h, state = client
    log(state, T0, cur(0.05, 0.10, 0.01, "static: the owner's ceilings"))
    log(state, T0 + pd.Timedelta(hours=1), cur(0.02, 0.05, None, "reverted", "default"), prev=PROFILE)
    log(state, T0 + pd.Timedelta(hours=2), cur(0.05, 0.10, 0.01, "static: the owner's ceilings"), prev="default")
    rows = c.get("/api/limits/changes", headers=h["viewer"]).json()
    assert [r["source"] for r in rows if r["field"] == "daily_cap"] == ["owner", "revert"]      # newest first


def test_adaptive_changes_are_listed_with_adr_0031_and_their_reason_codes_in_words(client):
    c, h, state = client
    db = Database.open(state, "core")
    with db.write() as conn:
        conn.execute("INSERT INTO adaptive_changes (account_id, day, field, old, new, reasons, evidence_ids, ts_ns) "
                     "VALUES ('icm-demo', '2026-10-17', 'daily_cap', 0.05, 0.02, ?, '[]', ?)",
                     (json.dumps(["dd_floor", "daily_cap:dd_floor"]), int(T0.value)))
    (r,) = c.get("/api/limits/changes", headers=h["viewer"]).json()
    assert (r["field"], r["old_value"], r["new_value"], r["source"], r["adr"]) == ("daily_cap", 0.05, 0.02, "adaptive",
                                                                                  "0031")
    assert "4%" in r["reason"] and "dd_floor" not in r["reason"]


def test_the_change_list_is_newest_first_and_limited(client):
    c, h, state = client
    for i in range(6):
        log(state, T0 + pd.Timedelta(hours=i), cur(0.02 + 0.005 * (i % 2), 0.05, None, "static: the owner's ceilings"))
    rows = c.get("/api/limits/changes?limit=3", headers=h["viewer"]).json()
    assert len(rows) == 3 and rows[0]["ts"] > rows[1]["ts"] > rows[2]["ts"]
    assert len(c.get("/api/limits/changes?limit=0", headers=h["viewer"]).json()) == 1          # clamped to 1..100


def test_no_view_names_a_login_server_or_evidence_id(client):
    c, h, state = client
    engine_file(state, login=12345678, server="ICMarketsSC-Demo")
    log(state, T0, cur(0.02, 0.05, None, "default", "default"))
    log(state, T0 + pd.Timedelta(hours=1), RiskLimitsCurrent(
        profile=PROFILE, daily_cap=0.05, weekly_cap=0.1, reason="static: the owner's ceilings",
        evidence_ids=("trade-secret-id",)))
    text = c.get("/api/limits", headers=h["viewer"]).text + c.get("/api/limits/changes", headers=h["viewer"]).text
    assert "12345678" not in text and "ICMarketsSC" not in text and "trade-secret-id" not in text


def test_an_unreadable_core_db_gives_no_changes_and_an_error_not_a_500(client):
    c, h, state = client
    engine_file(state)
    (state / "core.db").write_bytes(b"not a database at all" * 100)
    r = c.get("/api/limits", headers=h["viewer"])
    assert r.status_code == 200
    (a,) = r.json()["accounts"]
    assert a["last_change"] is None and a["daily_limit"] == 50.0
    assert r.json()["error"] is not None
