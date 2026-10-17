"""C9 (BACKLOG 62 plan review): health warns, and tells the owner, when the 12% kill switch's close-everything flag
stays set with closes still due (docs/proposals/2026-10-feed-reconcile-outage-minutes.md, part 2)."""
from pathlib import Path

import pandas as pd

from goldbot.ops import health
from goldbot.ops.checks import risk as risk_checks
from tests.test_health import NOW, SATURDAY, engine, make_ctx, write


def _tripped(tmp_path: Path, minutes_ago: float, *, now: pd.Timestamp = NOW, kill: bool = True,
             due: list[int] | None = None, halted_at: bool = True) -> None:
    engine(tmp_path, 5, now=now, stage="halted", closes_due=[111, 222] if due is None else due)
    at = (now - pd.Timedelta(minutes=minutes_ago)).isoformat() if halted_at else None
    write(tmp_path / "risk_icm-demo.json", {"last_reset": now.isoformat(), "stage": "halted",
                                            "kill_everything": kill, "halted_at": at})


def _check(tmp_path: Path, now: pd.Timestamp = NOW) -> list[health.Check]:
    return risk_checks.check_kill_switch(make_ctx(tmp_path, now=now), "icm-demo")


def test_no_check_while_the_flag_is_clear_or_nothing_is_due(tmp_path):
    _tripped(tmp_path, 60, kill=False)
    assert _check(tmp_path) == []
    _tripped(tmp_path, 60, due=[])
    assert _check(tmp_path) == []


def test_a_fresh_trip_with_closes_due_is_ok_inside_the_grace(tmp_path):
    _tripped(tmp_path, 2)
    [c] = _check(tmp_path)
    assert c.name == "kill_switch:icm-demo" and c.status == "ok"


def test_closes_due_past_five_minutes_warn_and_tell_the_owner(tmp_path):
    _tripped(tmp_path, 6)
    [c] = _check(tmp_path)
    assert c.status == "warn" and c.name.startswith(health.NOTIFY_ON_WARN)
    assert "2 close(s) still due" in c.reason and "6 min" in c.reason


def test_closes_due_past_thirty_minutes_of_open_market_fail(tmp_path):
    _tripped(tmp_path, 31)
    [c] = _check(tmp_path)
    assert c.status == "fail"


def test_a_closed_market_keeps_it_a_warning(tmp_path):
    _tripped(tmp_path, 120, now=SATURDAY)
    [c] = _check(tmp_path, now=SATURDAY)
    assert c.status == "warn" and "market closed" in c.reason


def test_an_unknown_trip_time_warns_at_once(tmp_path):
    _tripped(tmp_path, 0, halted_at=False)
    [c] = _check(tmp_path)
    assert c.status == "warn" and "since an unknown time" in c.reason


def test_unreadable_files_leave_it_to_the_engine_and_risk_checks(tmp_path):
    (tmp_path / "engine_icm-demo.json").write_text("{")
    write(tmp_path / "risk_icm-demo.json", {"stage": "halted", "kill_everything": True})
    assert _check(tmp_path) == []


def test_a_corrupt_file_is_failed_loudly_by_the_engine_and_risk_checks(tmp_path):
    """Review B: kill_switch stays silent on an unreadable file because these two FAIL on it."""
    (tmp_path / "engine_icm-demo.json").write_text("{")
    (tmp_path / "risk_icm-demo.json").write_text("{")
    ctx = make_ctx(tmp_path)
    assert _check(tmp_path) == []
    assert health.check_engine(ctx, "icm-demo").status == "fail"
    assert health.check_risk_state(ctx, "icm-demo").status == "fail"


def test_run_checks_includes_it(tmp_path):
    _tripped(tmp_path, 6)
    names = [c.name for c in health.run_checks(make_ctx(tmp_path)).checks]
    assert "kill_switch:icm-demo" in names
