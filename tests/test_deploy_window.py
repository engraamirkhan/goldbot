"""Alerts a deploy causes itself are held for its own window (audit 2026-11-20, SRE finding 3: about 63% of new alerts
were self-inflicted, tick_errors bursts after deploys and bridge_compat during rollouts). goldbot-deploy on the brain
writes state/deploy_window.json; goldbot-deploy-all opens state/deploy_window_manual.json before it deploys the MT5
boxes (`python -m goldbot.ops.deploy_window open --minutes N`). Held means deferred, never dropped: a check still bad
when the window ends is told on the next poll; a real outage (bridge:, engine_loop:, heartbeat:) is never held."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from goldbot.ops import deploy_window as dw
from goldbot.ops import health
from goldbot.ops.checks._base import Check

T0 = pd.Timestamp("2026-11-18 12:00", tz="UTC")
S0 = int(T0.timestamp())
POLICY = health.AlertPolicy(confirm_polls=3, confirm_s=300.0, flap_note_at=3)


def _write(state: Path, name: str, start: int, until: int, source: str = "goldbot-deploy") -> None:
    (state / name).write_text(json.dumps({"from": start, "until": until, "source": source}))


def test_a_window_is_active_between_its_from_and_until(tmp_path):
    _write(tmp_path, "deploy_window.json", S0 - 60, S0 + 600)
    assert dw.active(tmp_path, T0) is not None
    assert dw.active(tmp_path, T0 + pd.Timedelta(seconds=601)) is None
    assert dw.active(tmp_path, T0 - pd.Timedelta(seconds=61)) is None


def test_a_junk_or_overlong_window_mutes_nothing(tmp_path):
    (tmp_path / "deploy_window.json").write_text("{not json")
    assert dw.active(tmp_path, T0) is None
    _write(tmp_path, "deploy_window.json", S0 - 60, S0 + dw.MAX_WINDOW_S + 60)        # longer than any deploy
    assert dw.active(tmp_path, T0) is None
    (tmp_path / "deploy_window.json").write_text(json.dumps({"from": "x", "until": S0 + 60}))
    assert dw.active(tmp_path, T0) is None


def test_the_manual_window_counts_too(tmp_path):
    _write(tmp_path, "deploy_window.json", S0 - 7200, S0 - 3600)                      # an old deploy's
    _write(tmp_path, "deploy_window_manual.json", S0 - 60, S0 + 60, "manual")
    w = dw.active(tmp_path, T0)
    assert w is not None and w.source == "manual"


def test_open_extends_and_never_shortens(tmp_path):
    w = dw.open_manual(tmp_path, 30, now=T0)
    assert (w.start, w.until) == (S0, S0 + 1800)
    w = dw.open_manual(tmp_path, 10, now=T0 + pd.Timedelta(minutes=5))
    assert (w.start, w.until) == (S0, S0 + 1800)                                      # kept the longer one
    w = dw.open_manual(tmp_path, 60, now=T0 + pd.Timedelta(minutes=5))
    assert (w.start, w.until) == (S0, S0 + 300 + 3600)
    assert json.loads((tmp_path / "deploy_window_manual.json").read_text())["source"] == "manual"


def test_open_caps_the_minutes(tmp_path):
    assert dw.main(["open", "--minutes", "500", "--state-dir", str(tmp_path)]) == 2
    assert dw.main(["open", "--minutes", "0", "--state-dir", str(tmp_path)]) == 2
    assert dw.main(["open", "--minutes", "45", "--state-dir", str(tmp_path)]) == 0
    assert dw.active(tmp_path, pd.Timestamp.now("UTC")) is not None


def _report(ts: pd.Timestamp, *checks: Check) -> health.HealthReport:
    return health.HealthReport(ts=ts, status="warn", checks=list(checks))


TICK = Check(name="tick_errors:icm-demo", status="warn", reason="tick error (BridgeUnavailable) 1 min ago")
COMPAT = Check(name="bridge_compat:icm-demo", status="fail", reason="unchecked")
BRIDGE = Check(name="bridge:icm-demo", status="fail", reason="GET /health did not answer for 6 min")


def _poll(watch: health.HealthWatch, ts: pd.Timestamp, *checks: Check) -> str | None:
    return watch.poll(_report(ts, *checks))


def test_a_deploys_tick_errors_and_compat_alerts_are_held_and_cleared_quietly(tmp_path):
    _write(tmp_path, "deploy_window.json", S0, S0 + 1800)
    watch = health.HealthWatch(tmp_path, POLICY)
    texts = [_poll(watch, T0 + pd.Timedelta(minutes=m), TICK, COMPAT) for m in range(0, 16)]
    assert texts == [None] * 16                                       # 15 min of bursts: nothing told
    assert _poll(watch, T0 + pd.Timedelta(minutes=16)) is None        # cleared inside the window
    assert watch.state.flaps == {}                                    # and not a flap either
    assert _poll(watch, T0 + pd.Timedelta(minutes=40)) is None


def test_a_held_alert_still_bad_after_the_window_is_told_at_once(tmp_path):
    _write(tmp_path, "deploy_window.json", S0, S0 + 600)
    watch = health.HealthWatch(tmp_path, POLICY)
    for m in range(0, 10):
        assert _poll(watch, T0 + pd.Timedelta(minutes=m), TICK) is None
    text = _poll(watch, T0 + pd.Timedelta(minutes=11), TICK)          # the window is over, the fault is not
    assert text is not None and "tick_errors:icm-demo" in text


def test_a_real_outage_is_never_held(tmp_path):
    _write(tmp_path, "deploy_window.json", S0, S0 + 1800)
    watch = health.HealthWatch(tmp_path, POLICY)
    text = _poll(watch, T0, BRIDGE, TICK)
    assert text is not None and "bridge:icm-demo" in text and "tick_errors" not in text


def test_without_a_window_tick_errors_alert_as_before(tmp_path):
    watch = health.HealthWatch(tmp_path, POLICY)
    texts = [_poll(watch, T0 + pd.Timedelta(minutes=m), TICK) for m in range(0, 6)]
    assert any(t and "tick_errors:icm-demo" in t for t in texts)


def test_the_held_prefixes_are_exactly_the_two_deploy_noises():
    assert dw.HELD_PREFIXES == ("tick_errors:", "bridge_compat:")
