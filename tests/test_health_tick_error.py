"""engine:<account> on a tick error (incident 2026-11-04: one BridgeUnavailable blip at 06:27:56 UTC on all three
accounts kept engine:<account> at FAIL for 12+ minutes while the engines passed every minute). Design rule: the engine
blocks entries until a whole decision bar closes clean (exits never gated); health FAILs only while the error is
current (errors in the last few minutes, or repeating so that no clean bar has closed in two decision periods) and
otherwise WARNs with the age shown."""
import json
import time
from pathlib import Path

import pandas as pd

from goldbot.config import tf_seconds
from goldbot.engine import EngineConfig
from goldbot.ops import health
from goldbot.ops.checks import engine as engine_checks

WED = pd.Timestamp("2026-11-04 06:40", tz="UTC")        # Wednesday, market open
SAT = pd.Timestamp("2026-11-07 12:00", tz="UTC")        # Saturday, market closed


def _state(tmp_path: Path, now: pd.Timestamp, last_s: float, since_s: float | None) -> health.HealthContext:
    e = {"account": "icm-demo", "ts": now.timestamp() - 5, "stage": "normal", "tick_error": True,
         "tick_error_detail": "BridgeUnavailable", "tick_errors_total": 3,
         "last_tick_error_at": (now - pd.Timedelta(seconds=last_s)).isoformat()}
    if since_s is not None:
        e["tick_error_since"] = (now - pd.Timedelta(seconds=since_s)).isoformat()
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(e))
    return health.HealthContext(state_dir=tmp_path, now=now, get_secret=lambda k: None)


def test_a_single_tick_error_twelve_minutes_old_warns_with_its_age_instead_of_failing(tmp_path):
    c = health.check_engine(_state(tmp_path, WED, last_s=12 * 60, since_s=12 * 60), "icm-demo")
    assert c.status == "warn", c.reason
    assert "tick error (BridgeUnavailable) 12 min ago" in c.reason
    assert "entries blocked until a decision bar closes clean" in c.reason and "exits unaffected" in c.reason


def test_a_tick_error_in_the_last_two_minutes_still_fails(tmp_path):
    c = health.check_engine(_state(tmp_path, WED, last_s=30, since_s=30), "icm-demo")
    assert c.status == "fail" and "tick error (BridgeUnavailable) 30 s ago" in c.reason


def test_tick_errors_repeating_for_two_decision_periods_with_no_clean_bar_fail(tmp_path):
    c = health.check_engine(_state(tmp_path, WED, last_s=5 * 60, since_s=40 * 60), "icm-demo")
    assert c.status == "fail" and "repeating" in c.reason and "40 min" in c.reason


def test_an_engine_without_tick_error_since_falls_back_to_the_last_error_time(tmp_path):
    assert health.check_engine(_state(tmp_path, WED, last_s=12 * 60, since_s=None), "icm-demo").status == "warn"
    assert health.check_engine(_state(tmp_path, WED, last_s=40 * 60, since_s=None), "icm-demo").status == "fail"


def test_a_tick_error_left_set_over_a_closed_market_warns_because_no_bar_can_close(tmp_path):
    c = health.check_engine(_state(tmp_path, SAT, last_s=20 * 3600, since_s=20 * 3600), "icm-demo")
    assert c.status == "warn", c.reason


def test_the_repeating_bound_is_two_engine_decision_periods_plus_grace():
    period = tf_seconds(EngineConfig.model_fields["decision_tf"].default)
    assert engine_checks.TICK_ERROR_DECISION_S == period
    assert engine_checks.TICK_ERROR_STUCK_S > 2 * period


def test_a_cleared_tick_error_does_not_touch_engine_status(tmp_path):
    e = {"account": "icm-demo", "ts": time.time(), "stage": "normal", "tick_error": False, "tick_errors_total": 1,
         "last_tick_error_at": (WED - pd.Timedelta(seconds=10)).isoformat()}
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(e))
    c = health.check_engine(health.HealthContext(state_dir=tmp_path, now=WED, get_secret=lambda k: None), "icm-demo")
    assert "tick error" not in c.reason
