"""A heartbeat stamped in the future, or with a timestamp that is not a finite number, is never read as fresh
(found 2026-10-16). `now - ts > limit` is negative for a future `ts` and False for NaN, so a dead supervisor (or
engine, loop or service) whose last write carried a fast clock, a bad value or came from a restored backup looked
healthy forever. Every safety heartbeat now fails closed: future beyond a small tolerance, or not a number."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.api import entry_status as es
from goldbot.data.timeutil import CLOCK_AHEAD_TOLERANCE_S, heartbeat_problem
from goldbot.ops import bridge_compat as bc
from goldbot.ops import health
from goldbot.ops.account_view import _combined
from goldbot.ops.bridge_compat import BridgeCompat
from goldbot.ops.checks._base import HEALTH_CLOCK_AHEAD_S
from goldbot.ops.watchdog import loop_heartbeat_path
from goldbot.risk.supervisor import Supervisor
from tests.test_health import NOW, make_ctx, write

BAD_TS: list[Any] = [float("nan"), float("inf"), "1760000000", True, None]


def _raw(path: Path, doc: dict[str, Any]) -> None:
    """json.dumps writes NaN / Infinity as bare tokens, which json.loads reads back as floats: what a bad write leaves."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))


# ------------------------------------------------------------------------------------------- the shared rule
def test_heartbeat_problem_names_future_invalid_and_stale_stamps():
    now = 1_760_000_000.0
    assert heartbeat_problem(now, now, 60) is None
    assert heartbeat_problem(now - 60, now, 60) is None
    assert heartbeat_problem(now - 61, now, 60) == "stale"
    assert heartbeat_problem(now + CLOCK_AHEAD_TOLERANCE_S, now, 60) is None        # small skew: still fresh
    assert heartbeat_problem(now + CLOCK_AHEAD_TOLERANCE_S + 1, now, 60) == "future"
    assert heartbeat_problem(now + 3600, now, 60, ahead_s=60) == "future"
    assert heartbeat_problem(int(now), now, 60) is None                              # an int epoch is fine
    for ts in BAD_TS:
        assert heartbeat_problem(ts, now, 60) == "invalid", ts


# ------------------------------------------------------------------------------------- the engines' halt rule
def _sup(tmp_path: Path, ts: Any, **kw: Any) -> None:
    _raw(tmp_path / "supervisor.json", {"ts": ts, "halt": False, "reasons": [], **kw})


def test_engines_halt_on_a_supervisor_heartbeat_stamped_in_the_future(tmp_path):
    _sup(tmp_path, time.time() + 30)
    assert Supervisor.engine_should_halt(tmp_path) == (True, "supervisor_heartbeat_future")
    _sup(tmp_path, time.time() + 10 * 365 * 86400)                                  # a restored fast-clock backup
    assert Supervisor.engine_should_halt(tmp_path) == (True, "supervisor_heartbeat_future")


def test_a_supervisor_heartbeat_a_second_or_two_ahead_is_still_fresh(tmp_path):
    _sup(tmp_path, time.time() + 2)
    assert Supervisor.engine_should_halt(tmp_path) == (False, "")


@pytest.mark.parametrize("ts", BAD_TS)
def test_engines_halt_on_a_supervisor_heartbeat_that_is_not_a_finite_number(tmp_path, ts):
    _sup(tmp_path, ts)
    assert Supervisor.engine_should_halt(tmp_path) == (True, "supervisor_state_unreadable")


def test_engines_halt_on_a_supervisor_file_that_is_not_an_object(tmp_path):
    (tmp_path / "supervisor.json").write_text("[1, 2]")
    assert Supervisor.engine_should_halt(tmp_path) == (True, "supervisor_state_unreadable")


# ------------------------------------------------------------------------- the supervisor reading engine files
def _engine(tmp_path: Path, account: str, ts: float) -> None:
    _raw(tmp_path / f"engine_{account}.json", {"account": account, "ts": ts, "currency": "GBP", "equity": 10_000.0,
                                               "day_start_equity": 10_000.0, "week_start_equity": 10_000.0,
                                               "balance_closed_hwm": 10_000.0})


def test_the_supervisor_halts_on_an_engine_file_stamped_in_the_future(tmp_path):
    _engine(tmp_path, "a", time.time())
    _engine(tmp_path, "b", time.time() + 600)
    st = Supervisor(tmp_path).evaluate()
    assert st["halt"] and "engine_heartbeat_future" in st["reasons"]
    assert st["future_engines"] == ["engine_b.json"]
    assert "b" not in st["stale_engines"]
    assert Supervisor.engine_should_halt(tmp_path) == (True, "engine_heartbeat_future")


def test_the_supervisor_does_not_halt_on_an_engine_a_second_or_two_ahead(tmp_path):
    _engine(tmp_path, "a", time.time() + 2)
    st = Supervisor(tmp_path).evaluate()
    assert not st["halt"] and st["future_engines"] == []


# ------------------------------------------------------------------------------------- owner-facing text
def test_the_new_halt_reasons_read_in_plain_words_on_the_dashboard_and_telegram(tmp_path):
    for code in ("supervisor_heartbeat_future", "engine_heartbeat_future"):
        r = es.REASONS[code]
        assert r.kind == "act" and "clock" in r.text.lower() and "_" not in r.text
    _sup(tmp_path, time.time() + 600)
    inputs = es.entry_inputs(tmp_path, None, is_owner=True)
    assert inputs["supervisor_reasons"] == ["supervisor_heartbeat_future"]


# ------------------------------------------------------------------------------------------------- health
def test_health_fails_the_supervisor_on_a_heartbeat_stamped_in_the_future(tmp_path):
    write(tmp_path / "supervisor.json", {"ts": NOW.timestamp() + HEALTH_CLOCK_AHEAD_S + 60, "halt": False, "reasons": []})
    c = health.check_supervisor(make_ctx(tmp_path))
    assert c.status == "fail" and "ahead" in c.reason and "engines block entries" in c.reason
    write(tmp_path / "supervisor.json", {"ts": NOW.timestamp() + 20, "halt": False, "reasons": []})
    assert health.check_supervisor(make_ctx(tmp_path)).status == "ok"              # the pass's own lag: tolerated


@pytest.mark.parametrize("ts", BAD_TS)
def test_health_fails_the_supervisor_on_a_heartbeat_that_is_not_a_number(tmp_path, ts):
    _raw(tmp_path / "supervisor.json", {"ts": ts, "halt": False, "reasons": []})
    c = health.check_supervisor(make_ctx(tmp_path))
    assert c.status == "fail" and "not a number" in c.reason


def test_health_fails_the_supervisor_and_names_an_engine_file_stamped_in_the_future(tmp_path):
    write(tmp_path / "supervisor.json", {"ts": NOW.timestamp(), "halt": True, "reasons": ["engine_heartbeat_future"],
                                         "future_engines": ["engine_b.json"]})
    c = health.check_supervisor(make_ctx(tmp_path))
    assert c.status == "fail" and "engine_b.json" in c.reason and "clock" in c.reason


def _engine_at(tmp_path: Path, ts: float) -> None:
    write(tmp_path / "engine_icm-demo.json", {"account": "icm-demo", "ts": ts, "stage": "normal"})


def test_health_fails_an_engine_state_stamped_in_the_future(tmp_path):
    _engine_at(tmp_path, NOW.timestamp() + HEALTH_CLOCK_AHEAD_S + 60)
    c = health.check_engine(make_ctx(tmp_path), "icm-demo")
    assert c.status == "fail" and "ahead" in c.reason
    _engine_at(tmp_path, NOW.timestamp() + 20)
    assert "ahead" not in health.check_engine(make_ctx(tmp_path), "icm-demo").reason


@pytest.mark.parametrize("ts", [float("nan"), "x"])
def test_health_fails_an_engine_loop_heartbeat_stamped_in_the_future_or_not_a_number(tmp_path, ts):
    f = loop_heartbeat_path(tmp_path, "icm-demo")
    _raw(f, {"ts": NOW.timestamp() + HEALTH_CLOCK_AHEAD_S + 60, "phase": "loop"})
    c = health.check_engine_loop(make_ctx(tmp_path), "icm-demo")
    assert c.status == "fail" and "ahead" in c.reason
    _raw(f, {"ts": NOW.timestamp() + 20, "phase": "loop"})
    assert health.check_engine_loop(make_ctx(tmp_path), "icm-demo").status == "ok"
    _raw(f, {"ts": ts, "phase": "loop"})
    assert health.check_engine_loop(make_ctx(tmp_path), "icm-demo").status == "fail"


@pytest.mark.parametrize("ts", [float("nan"), "x"])
def test_health_fails_a_service_heartbeat_stamped_in_the_future_or_not_a_number(tmp_path, ts):
    f = health.heartbeat_path(tmp_path, "telegram")
    _raw(f, {"service": "telegram", "ts": NOW.timestamp() + HEALTH_CLOCK_AHEAD_S + 60})
    c = health.check_heartbeat(make_ctx(tmp_path), "telegram")
    assert c.status == "fail" and "ahead" in c.reason
    _raw(f, {"service": "telegram", "ts": NOW.timestamp() + 20})
    assert health.check_heartbeat(make_ctx(tmp_path), "telegram").status == "ok"
    _raw(f, {"service": "telegram", "ts": ts})
    assert health.check_heartbeat(make_ctx(tmp_path), "telegram").status == "fail"


def test_health_fails_a_scheduler_heartbeat_stamped_in_the_future_even_while_a_job_runs(tmp_path):
    ahead = NOW + pd.Timedelta(seconds=HEALTH_CLOCK_AHEAD_S + 60)
    job = {"last_started": (NOW - pd.Timedelta(minutes=5)).isoformat(), "last_finished": None}
    write(tmp_path / "scheduler.json", {"ts": ahead.isoformat(), "jobs": {"retrain": job}})
    c = health.check_scheduler(make_ctx(tmp_path, settings=None))[0]
    assert c.name == "scheduler" and c.status == "fail" and "ahead" in c.reason
    write(tmp_path / "scheduler.json", {"ts": (NOW + pd.Timedelta(seconds=20)).isoformat(), "jobs": {}})
    assert health.check_scheduler(make_ctx(tmp_path, settings=None))[0].status == "ok"


def test_health_warns_when_the_alert_loop_stamp_is_in_the_future(tmp_path):
    ahead = NOW + pd.Timedelta(seconds=HEALTH_CLOCK_AHEAD_S + 60)
    write(tmp_path / health.WATCH_FILE, {"ts": ahead.isoformat(), "statuses": {}})
    c = health.check_alert_loop(make_ctx(tmp_path))
    assert c.status == "warn" and "ahead" in c.reason


# --------------------------------------------------------------------------- dashboard and bridge verdict
def test_the_combined_caps_are_not_shown_from_a_supervisor_report_stamped_in_the_future():
    now = 1_760_000_000.0
    sup = {"combined_equity": 9_900.0, "combined_day_start_equity": 10_000.0, "daily_cap": 0.015, "ts": now + 600}
    shown: list[Any] = []
    notes: list[str] = []
    eng = {"currency": "GBP"}
    _combined(eng, sup, [eng], now, lambda *a, **k: shown.append(a), notes)
    assert not shown and any("stale" in n for n in notes)
    _combined(eng, dict(sup, ts=now + 2), [eng], now, lambda *a, **k: shown.append(a), notes)
    assert shown


def test_the_feeds_page_shows_a_future_supervisor_heartbeat_as_not_reporting():
    from goldbot.api.app import supervisor_heartbeat_age_s
    now = 1_760_000_000.0
    assert supervisor_heartbeat_age_s({"ts": now - 7}, now) == pytest.approx(7)
    assert supervisor_heartbeat_age_s({"ts": now + 2}, now) == 0.0
    assert supervisor_heartbeat_age_s({"ts": now + 600}, now) == 1e9
    assert supervisor_heartbeat_age_s({"ts": float("nan")}, now) == 1e9
    assert supervisor_heartbeat_age_s({}, now) == 1e9


def test_a_bridge_verdict_confirmed_in_the_future_counts_as_unchecked():
    wed = pd.Timestamp("2026-10-07 14:00", tz="UTC")
    ahead = BridgeCompat(status="ok", reason="ok", checked_at=wed + pd.Timedelta(hours=2))
    v = bc.effective(ahead, wed)
    assert v.status == "unchecked" and v.blocks and "ahead" in v.reason
    near = BridgeCompat(status="ok", reason="ok", checked_at=wed + pd.Timedelta(seconds=2))
    assert bc.effective(near, wed) is near
