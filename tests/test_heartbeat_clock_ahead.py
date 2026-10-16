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
# "Ahead" is judged against max(ctx.now, the wall clock) (`_ahead_s`, SRE review MEDIUM), so these tests stamp
# relative to the real clock: a fixed past ctx.now could never make a stamp look ahead of time.time().
SKEW_S = 10.0                       # beyond the 5 s tolerance: FAILs health (the first cut's 60 s let it through)
assert SKEW_S > HEALTH_CLOCK_AHEAD_S == CLOCK_AHEAD_TOLERANCE_S


def _live(tmp_path: Path, started_s_ago: float = 0.0, **kw: Any) -> health.HealthContext:
    """A pass whose `now` was taken `started_s_ago` seconds before the real clock (a slow pass)."""
    return make_ctx(tmp_path, now=pd.Timestamp.now("UTC") - pd.Timedelta(seconds=started_s_ago), **kw)


def _ahead(s: float) -> float:
    return time.time() + s


def _iso(ts: float) -> str:
    return pd.Timestamp(ts, unit="s", tz="UTC").isoformat()


def _write_all(tmp_path: Path, ts: float) -> None:
    """Every liveness stamp health reads, all at `ts`."""
    write(tmp_path / "supervisor.json", {"ts": ts, "halt": False, "reasons": []})
    write(tmp_path / "engine_icm-demo.json", {"account": "icm-demo", "ts": ts, "stage": "normal"})
    write(loop_heartbeat_path(tmp_path, "icm-demo"), {"ts": ts, "phase": "loop"})
    write(health.heartbeat_path(tmp_path, "telegram"), {"service": "telegram", "ts": ts})
    write(tmp_path / "scheduler.json", {"ts": _iso(ts), "jobs": {}})
    write(tmp_path / health.WATCH_FILE, {"ts": _iso(ts), "statuses": {}})


def _liveness(ctx: health.HealthContext) -> dict[str, health.Check]:
    return {"supervisor": health.check_supervisor(ctx), "engine": health.check_engine(ctx, "icm-demo"),
            "loop": health.check_engine_loop(ctx, "icm-demo"), "heartbeat": health.check_heartbeat(ctx, "telegram"),
            "scheduler": health.check_scheduler(ctx)[0], "alerts": health.check_alert_loop(ctx)}


def test_health_fails_every_liveness_stamp_ten_seconds_ahead(tmp_path):
    _write_all(tmp_path, _ahead(SKEW_S))
    got = _liveness(_live(tmp_path, settings=None))
    for name, c in got.items():
        assert "ahead" in c.reason, (name, c.reason)
        assert c.status == ("warn" if name == "alerts" else "fail"), (name, c)
    assert "engines block entries" in got["supervisor"].reason


def test_health_tolerates_a_stamp_a_second_or_two_ahead(tmp_path):
    _write_all(tmp_path, _ahead(2))
    for name, c in _liveness(_live(tmp_path, settings=None)).items():
        assert "ahead" not in c.reason, (name, c.reason)
        if name != "engine":                         # the engine line also judges the market hours
            assert c.status == "ok", (name, c)


def test_a_slow_health_pass_does_not_flag_stamps_written_while_it_ran(tmp_path):
    """SRE review MEDIUM: with the bridge timing out (~20 s an account) a pass runs for minutes after taking `now`;
    services that beat meanwhile are fresh, not a clock fault."""
    _write_all(tmp_path, time.time())                # written two minutes after the pass took its `now`
    for name, c in _liveness(_live(tmp_path, started_s_ago=120, settings=None)).items():
        assert "ahead" not in c.reason, (name, c.reason)
        if name != "engine":
            assert c.status == "ok", (name, c)


def test_a_slow_pass_still_flags_a_stamp_ahead_of_the_wall_clock(tmp_path):
    _write_all(tmp_path, _ahead(SKEW_S))
    for name, c in _liveness(_live(tmp_path, started_s_ago=120, settings=None)).items():
        assert "ahead" in c.reason, (name, c.reason)


@pytest.mark.parametrize("ts", BAD_TS)
def test_health_fails_the_supervisor_on_a_heartbeat_that_is_not_a_number(tmp_path, ts):
    _raw(tmp_path / "supervisor.json", {"ts": ts, "halt": False, "reasons": []})
    c = health.check_supervisor(make_ctx(tmp_path))
    assert c.status == "fail" and "not a number" in c.reason


@pytest.mark.parametrize("ts", [float("nan"), float("inf"), "x"])
def test_health_fails_a_loop_or_service_heartbeat_that_is_not_a_number(tmp_path, ts):
    _raw(loop_heartbeat_path(tmp_path, "icm-demo"), {"ts": ts, "phase": "loop"})
    assert health.check_engine_loop(make_ctx(tmp_path), "icm-demo").status == "fail"
    _raw(health.heartbeat_path(tmp_path, "telegram"), {"service": "telegram", "ts": ts})
    assert health.check_heartbeat(make_ctx(tmp_path), "telegram").status == "fail"


def test_health_fails_the_supervisor_and_names_an_engine_file_stamped_in_the_future(tmp_path):
    write(tmp_path / "supervisor.json", {"ts": NOW.timestamp(), "halt": True, "reasons": ["engine_heartbeat_future"],
                                         "future_engines": ["engine_b.json"]})
    c = health.check_supervisor(make_ctx(tmp_path))
    assert c.status == "fail" and "engine_b.json" in c.reason and "clock" in c.reason


def test_health_fails_a_scheduler_heartbeat_stamped_in_the_future_even_while_a_job_runs(tmp_path):
    job = {"last_started": _iso(time.time() - 300), "last_finished": None}
    write(tmp_path / "scheduler.json", {"ts": _iso(_ahead(SKEW_S)), "jobs": {"retrain": job}})
    c = health.check_scheduler(_live(tmp_path, settings=None))[0]
    assert c.name == "scheduler" and c.status == "fail" and "ahead" in c.reason


# --------------------------------------------------------------------------- dashboard and bridge verdict
def test_the_combined_caps_are_not_shown_from_a_supervisor_report_stamped_in_the_future():
    now = 1_760_000_000.0
    sup = {"combined_equity": 9_900.0, "combined_day_start_equity": 10_000.0, "daily_cap": 0.015, "ts": now + 600}
    shown: list[Any] = []
    notes: list[str] = []
    eng = {"currency": "GBP"}
    unknown, paused = _combined(eng, sup, [eng], now, lambda *a, **k: shown.append(a), notes)
    assert not shown and paused and unknown is not None and "dated ahead of this server's clock" in unknown
    assert _combined(eng, dict(sup, ts=now + 2), [eng], now, lambda *a, **k: shown.append(a), notes) == (None, False)
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
    assert v.status == "unchecked" and v.blocks and "120 min ahead" in v.reason
    short = BridgeCompat(status="ok", reason="ok", checked_at=wed + pd.Timedelta(seconds=20))
    assert "20 s ahead" in bc.effective(short, wed).reason          # not "0 min" (review LOW)
    near = BridgeCompat(status="ok", reason="ok", checked_at=wed + pd.Timedelta(seconds=2))
    assert bc.effective(near, wed) is near
