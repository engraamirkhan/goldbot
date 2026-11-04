"""Health alerts with hysteresis (owner complaint 2026-11-04: "the health checks have been horrible ... up and
down"). A check alerts as failing only after N consecutive polls AND T minutes (telegram.alert_confirm_polls /
alert_confirm_min), recovers the same way, and a short list of real-money and position-safety checks alerts at once.
The outside dead man's switch pings /fail only for a confirmed failure, from its own liveness cadence."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from goldbot.config import TelegramSettings, load_settings
from goldbot.ops import health
from goldbot.ops.checks import bridge as bridge_checks
from goldbot.ops.health import AlertPolicy, Check, HealthReport, HealthWatch

T0 = pd.Timestamp("2026-11-04 09:00", tz="UTC")
POLICY = AlertPolicy(confirm_polls=3, confirm_s=300, flap_note_at=3)


def at(minute: float) -> pd.Timestamp:
    return T0 + pd.Timedelta(minutes=minute)


def rep(minute: float, **statuses: str) -> HealthReport:
    checks = [Check(name=n.replace("__", ":"), status=s, reason=f"{n} is {s}")  # type: ignore[arg-type]
              for n, s in statuses.items()]
    return HealthReport(ts=at(minute), status=health._worst([c.status for c in checks]), checks=checks)


def watch(tmp_path: Path) -> HealthWatch:
    return HealthWatch(tmp_path, policy=POLICY)


# ------------------------------------------------------------------------------------------------ onset and recovery
def test_a_failure_that_clears_inside_the_window_sends_nothing(tmp_path):
    w = watch(tmp_path)
    assert w.poll(rep(0, supervisor="ok")) is None
    assert w.poll(rep(1, supervisor="fail")) is None
    assert w.poll(rep(2, supervisor="fail")) is None
    assert w.poll(rep(3, supervisor="ok")) is None            # cleared before 3 polls and 5 min: silent
    assert w.poll(rep(4, supervisor="ok")) is None            # and no "recovered" for a failure never announced


def test_a_failure_alerts_only_after_three_polls_and_five_minutes(tmp_path):
    w = watch(tmp_path)
    assert w.poll(rep(0, supervisor="fail")) is None
    assert w.poll(rep(1, supervisor="fail")) is None
    assert w.poll(rep(2, supervisor="fail")) is None          # three polls, but only 2 min
    assert w.poll(rep(4, supervisor="fail")) is None
    text = w.poll(rep(5, supervisor="fail"))                  # 5 min since first seen, 5 polls
    assert text is not None and "FAIL supervisor" in text
    assert w.poll(rep(6, supervisor="fail")) is None          # still failing: no repeat


def test_five_minutes_alone_is_not_enough_without_three_polls(tmp_path):
    w = watch(tmp_path)
    assert w.poll(rep(0, supervisor="fail")) is None
    assert w.poll(rep(10, supervisor="fail")) is None         # 10 min, but two polls (the service was down between)
    assert w.poll(rep(11, supervisor="fail")) is not None


def test_recovery_is_sent_only_after_three_ok_polls_and_five_minutes(tmp_path):
    w = watch(tmp_path)
    for m in range(6):
        w.poll(rep(m, supervisor="fail"))
    assert w.failing() == {"supervisor"}
    assert w.poll(rep(6, supervisor="ok")) is None
    assert w.poll(rep(7, supervisor="ok")) is None
    assert w.poll(rep(8, supervisor="fail")) is None          # back: still failing as announced, nothing to say
    assert w.poll(rep(9, supervisor="ok")) is None
    assert w.poll(rep(11, supervisor="ok")) is None
    assert w.failing() == {"supervisor"}                      # recovery not confirmed yet
    text = w.poll(rep(14, supervisor="ok"))                   # three ok polls since 9, 5 min
    assert text is not None and "recovered: supervisor (ok)" in text
    assert w.failing() == set()


def test_a_notify_on_warn_warning_waits_for_the_window_too(tmp_path):
    w = watch(tmp_path)
    assert w.poll(rep(0, tick_errors__icm="warn")) is None
    assert w.poll(rep(1, tick_errors__icm="ok")) is None
    assert w.poll(rep(2, history_gap__icm="warn")) is None
    for m in (3, 4, 5, 6):
        assert w.poll(rep(m, history_gap__icm="warn")) is None
    text = w.poll(rep(7, history_gap__icm="warn"))
    assert text is not None and "WARN history_gap:icm" in text


def test_several_confirmed_changes_go_in_one_message(tmp_path):
    w = watch(tmp_path)
    texts = [w.poll(rep(m, supervisor="fail", news="fail", disk="ok")) for m in range(6)]
    sent = [t for t in texts if t]
    assert len(sent) == 1 and "2 new failure(s)" in sent[0] and "FAIL supervisor" in sent[0] and "FAIL news" in sent[0]


def test_an_unsent_alert_is_retried_and_the_streak_is_kept_across_a_restart(tmp_path):
    for m in range(4):
        assert watch(tmp_path).poll(rep(m, supervisor="fail")) is None   # a new HealthWatch each pass: a restart
    w = watch(tmp_path)
    r = rep(5, supervisor="fail")
    assert w.alert(r) is not None                     # send failed: record() not called
    assert watch(tmp_path).alert(r) is not None       # retried by the next pass
    w.record(r)
    assert watch(tmp_path).poll(rep(6, supervisor="fail")) is None


# ------------------------------------------------------------------------------------------------ immediate exceptions
def test_the_immediate_list_is_explicit():
    """Real money and position safety alert on the first poll: these checks either debounce themselves already
    (bridge 5 min, service heartbeat 5 min, engine state 45 min) or must not wait (a loss cap, a kill switch, a failed
    order, a broken backup, a terminal refusing every order, anything CRITICAL)."""
    assert health.IMMEDIATE_NAMES == ("kill_switch:", "daily_cap:", "weekly_cap:", "order_failed:", "backup_age",
                                      "backup_prune", "object_storage", "bridge:", "heartbeat:", "engine:")
    assert health.IMMEDIATE_REASONS == ("CRITICAL", "MT5 terminal AutoTrading is off",
                                        "MT5 terminal logged in to a different account")


@pytest.mark.parametrize("check", [
    Check(name="kill_switch:icm-demo", status="fail", reason="tripped"),
    Check(name="daily_cap:icm-demo", status="warn", reason="tripped"),
    Check(name="weekly_cap:icm-demo", status="warn", reason="tripped"),
    Check(name="order_failed:icm-demo", status="warn", reason="retcode 10027"),
    Check(name="backup_age", status="fail", reason="no backup for 30 h"),
    Check(name="backup_prune", status="warn", reason="prune failed"),
    Check(name="object_storage", status="warn", reason="upload failed"),
    Check(name="bridge:icm-demo", status="fail", reason="MT5 bridge unreachable for 5 min"),
    Check(name="heartbeat:telegram", status="fail", reason="silent for 6 min"),
    Check(name="engine:icm-demo", status="fail", reason="state 50 min old"),
    Check(name="terminal:icm-demo", status="fail", reason=bridge_checks._TERMINAL_FAIL["autotrading_off"]),
    Check(name="terminal:icm-demo", status="fail", reason=bridge_checks._TERMINAL_FAIL["wrong_account"]),
    Check(name="terminal:icm-demo", status="fail", reason="CRITICAL: 2 open position(s) on this account cannot be"),
])
def test_money_and_position_safety_checks_alert_on_the_first_poll(tmp_path, check):
    w = watch(tmp_path)
    text = w.poll(HealthReport(ts=T0, status=check.status, checks=[check]))
    assert text is not None and check.name in text
    assert health.alerts_at_once(check)


@pytest.mark.parametrize("check", [
    Check(name="engine_loop:icm-demo", status="fail", reason="silent for 160 s"),
    Check(name="tick_errors:icm-demo", status="warn", reason="3 tick errors"),
    Check(name="history_gap:icm-demo", status="warn", reason="gap"),
    Check(name="open_risk:icm-demo", status="warn", reason="open risk unknown"),
    Check(name="terminal:icm-demo", status="fail", reason=bridge_checks._TERMINAL_FAIL["not_running"]),
    Check(name="supervisor", status="fail", reason="old"),
])
def test_other_checks_wait_for_the_window(tmp_path, check):
    assert not health.alerts_at_once(check)
    assert watch(tmp_path).poll(HealthReport(ts=T0, status=check.status, checks=[check])) is None


def test_a_recovery_of_an_immediate_check_still_waits_for_the_window(tmp_path):
    w = watch(tmp_path)
    assert w.poll(rep(0, kill_switch__icm="fail")) is not None
    assert w.poll(rep(1, kill_switch__icm="ok")) is None
    assert w.failing() == {"kill_switch:icm"}


# ------------------------------------------------------------------------------------------------ flapping note
def test_a_check_flapping_under_the_threshold_gets_one_note_an_hour(tmp_path):
    w = watch(tmp_path)
    texts = {}
    for m in range(0, 120, 2):                       # fail one poll, ok the next, for two hours
        texts[m] = w.poll(rep(m, supervisor="fail"))
        texts[m + 1] = w.poll(rep(m + 1, supervisor="ok"))
    sent = {m: t for m, t in texts.items() if t}
    assert all("flapping" in t and "supervisor" in t for t in sent.values())
    assert list(sent) == [5, 65]                     # the third flap at 5, then once per hour
    assert "3 times in the last hour" in sent[5] and "not alerted" in sent[5]
    assert w.failing() == set()


def test_old_shape_watch_file_loads_with_the_announced_statuses(tmp_path):
    (tmp_path / "health_last.json").write_text(json.dumps(
        {"ts": "2026-11-04T08:59:00+00:00", "statuses": {"supervisor": "fail", "disk": "ok"}}))
    w = watch(tmp_path)
    assert w.state.announced == {"supervisor": "fail", "disk": "ok"}
    assert w.poll(rep(0, supervisor="fail", disk="ok")) is None       # already announced: not repeated
    assert w.failing() == {"supervisor"}


def test_the_immediate_policy_is_the_rule_before_hysteresis(tmp_path):
    w = HealthWatch(tmp_path)                        # no policy: every change at once (check-level tests)
    assert w.poll(rep(0, supervisor="fail")) is not None
    assert w.poll(rep(1, supervisor="ok")) is not None


# ------------------------------------------------------------------------------------------------ settings
def test_settings_carry_the_hysteresis_defaults_and_the_policy_reads_them():
    t = load_settings().telegram
    assert (t.alert_confirm_polls, t.alert_confirm_min, t.alert_flap_note) == (3, 5, 3)
    p = AlertPolicy.from_settings(TelegramSettings(alert_confirm_polls=4, alert_confirm_min=7, alert_flap_note=2))
    assert p == AlertPolicy(confirm_polls=4, confirm_s=420, flap_note_at=2)
    yaml = (Path(__file__).resolve().parents[1] / "config/settings.yaml").read_text(encoding="utf-8")
    assert "alert_confirm_polls: 3" in yaml and "alert_confirm_min: 5" in yaml and "alert_flap_note: 3" in yaml


# ------------------------------------------------------------------------------------------------ alert log (aux.db)
def test_every_sent_message_is_logged_in_aux_db(tmp_path):
    from goldbot.ops.alert_log import AlertLog
    log = AlertLog(tmp_path)
    log.append(at(0), channel="telegram", kinds=("new", "recovered"), checks=("supervisor", "news"))
    log.append(at(1), channel="deadman", kinds=("fail",), checks=("engine:icm",))
    with sqlite3.connect(tmp_path / "aux.db") as conn:
        rows = conn.execute("SELECT ts_ns, channel, kinds, checks FROM health_alerts ORDER BY id").fetchall()
    assert rows == [(at(0).value, "telegram", "new,recovered", "supervisor,news"),
                    (at(1).value, "deadman", "fail", "engine:icm")]
    assert log.count_since(at(0)) == 2 and log.count_since(at(0), channel="deadman") == 1


def test_the_step_names_what_it_sent(tmp_path):
    w = watch(tmp_path)
    for m in range(5):
        w.poll(rep(m, supervisor="fail"))
    step = w.step(rep(5, supervisor="fail"))
    assert step.text is not None and step.kinds == ("new",) and step.checks == ("supervisor",)
