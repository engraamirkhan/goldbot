"""ADR 0010 follow-up: state written by a newer release (expected only after a rollback) reaches the owner, not only
the log. The engine publishes a bounded `schema_skew` summary in engine_<account>.json and health check
`schema_skew:<account>` warns while any skew is present (naming the sources) and fails when a strict reader refused a
record, i.e. an owner's click was dropped (bus.decisions_for)."""
import json
import time
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

from goldbot import base
from goldbot.base import SKEW_FIELDS_MAX, SKEW_SUMMARY_MAX, SkewSummary, note_schema_skew, skew_summary
from goldbot.engine import Engine, EngineConfig
from goldbot.execution.paper import PaperBroker
from goldbot.ops import health
from goldbot.specialists.session_open import SessionOpenSpecialist

NOW = pd.Timestamp("2026-01-15 16:00", tz="UTC")


@pytest.fixture
def fresh_skew(monkeypatch):
    """The skew counter is process-wide; each test starts from an empty one."""
    monkeypatch.setattr(base, "_skew", Counter())
    monkeypatch.setattr(base, "_refused", set())
    monkeypatch.setattr(base, "_last_refused", None)


def test_the_summary_is_empty_when_nothing_was_read_from_a_newer_release(fresh_skew):
    assert base.schema_skew_summary() == SkewSummary(count=0, reads=0, refused=False, entries=[], truncated=0,
                                                       last_refused_utc=None)


def test_the_summary_names_source_model_fields_count_and_whether_the_entry_was_refused(fresh_skew):
    note_schema_skew("risk_<account>.json", "RiskState", ["new_cap"])
    note_schema_skew("risk_<account>.json", "RiskState", ["new_cap"])
    note_schema_skew("approvals/decisions:p1", "BusDecision", ["max_lots"], refused=True)
    s = base.schema_skew_summary()
    assert (s.count, s.reads, s.refused, s.truncated) == (2, 3, True, 0)
    assert s.last_refused_utc is not None                                     # wall clock of the refusal
    assert abs((pd.Timestamp.now(tz="UTC") - s.last_refused_utc).total_seconds()) < 60
    assert [(e.source, e.model, e.fields, e.count, e.refused) for e in s.entries] == [
        ("approvals/decisions:p1", "BusDecision", ["max_lots"], 1, True),        # refused first
        ("risk_<account>.json", "RiskState", ["new_cap"], 2, False)]


def test_the_summary_is_bounded_and_never_truncates_a_refused_entry_away():
    counts: Counter = Counter({(f"src{i:03d}", "M", tuple(f"f{j}" for j in range(30))): 1 for i in range(100)})
    refused_key = ("zzz-decision", "BusDecision", ("max_lots",))
    counts[refused_key] = 1
    s = skew_summary(counts, {refused_key})
    assert len(s.entries) == SKEW_SUMMARY_MAX and s.truncated == 101 - SKEW_SUMMARY_MAX and s.count == 101
    assert s.entries[0].source == "zzz-decision" and s.entries[0].refused and s.refused
    assert all(len(e.fields) <= SKEW_FIELDS_MAX for e in s.entries)
    assert len(json.dumps(s.model_dump(mode="json"))) < 8_000            # a heartbeat file stays small


def _engine(tmp_path: Path) -> Engine:
    pb = PaperBroker(equity=10_000, commission_per_lot_side=0.0, adverse_slip_points=0.0)
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=111),
                  pb, [SessionOpenSpecialist()], {})


def test_the_engine_state_file_publishes_the_schema_skew_summary(tmp_path, fresh_skew):
    eng = _engine(tmp_path)
    eng._write_state()
    assert json.loads((tmp_path / "engine_icm-demo.json").read_text())["schema_skew"]["count"] == 0
    note_schema_skew("approvals/decisions:icm-demo-1-x", "BusDecision", ["max_lots"], refused=True)
    eng._write_state()
    sk = json.loads((tmp_path / "engine_icm-demo.json").read_text())["schema_skew"]
    assert sk["refused"] is True and sk["count"] == 1 and pd.Timestamp(sk["last_refused_utc"]).tzinfo is not None
    assert sk["entries"] == [{"source": "approvals/decisions:icm-demo-1-x", "model": "BusDecision",
                              "fields": ["max_lots"], "count": 1, "refused": True}]


# ---------------------------------------------------------------------------------------------- health
def _ctx(tmp_path: Path) -> health.HealthContext:
    return health.HealthContext(state_dir=tmp_path, now=NOW, get_secret=lambda k: None,
                                accounts=[health.AccountRef(account_id="icm-demo")])


def _publish(tmp_path: Path, skew: object) -> None:
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps({"account": "icm-demo", "schema_skew": skew}))


def _summary(*entries: tuple[str, str, list[str], int, bool], refused_at: pd.Timestamp | None = None) -> dict:
    counts = Counter({(s, m, tuple(f)): n for s, m, f, n, _ in entries})
    refused = {(s, m, tuple(f)) for s, m, f, _, r in entries if r}
    at = refused_at if refused_at is not None else NOW - pd.Timedelta(minutes=1)
    return skew_summary(counts, refused, at if refused else None).model_dump(mode="json")


def test_health_is_ok_without_skew_and_silent_for_an_engine_that_does_not_publish_it(tmp_path):
    assert health.check_schema_skew(_ctx(tmp_path), "icm-demo") is None                    # no engine file
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps({"account": "icm-demo"}))
    assert health.check_schema_skew(_ctx(tmp_path), "icm-demo") is None                    # older engine
    _publish(tmp_path, _summary())
    c = health.check_schema_skew(_ctx(tmp_path), "icm-demo")
    assert c is not None and (c.name, c.status) == ("schema_skew:icm-demo", "ok")


def test_health_warns_on_skew_and_names_the_sources(tmp_path):
    _publish(tmp_path, _summary(("risk_<account>.json", "RiskState", ["new_cap"], 4, False),
                                ("orders_<account>.json", "orders file", ["brackets"], 1, False)))
    c = health.check_schema_skew(_ctx(tmp_path), "icm-demo")
    assert c is not None and c.status == "warn"
    assert "risk_<account>.json" in c.reason and "orders_<account>.json" in c.reason and "rollback" in c.reason
    assert c.name.startswith(health.NOTIFY_ON_WARN), "the owner is told about a warning, not only a fail"


def test_health_fails_when_an_owner_click_was_refused(tmp_path):
    _publish(tmp_path, _summary(("risk_<account>.json", "RiskState", ["new_cap"], 4, False),
                                ("approvals/decisions:p1", "BusDecision", ["max_lots"], 2, True)))
    c = health.check_schema_skew(_ctx(tmp_path), "icm-demo")
    assert c is not None and c.status == "fail"
    assert "approvals/decisions:p1" in c.reason and "click" in c.reason


def test_health_names_at_most_five_sources_in_about_400_characters_and_how_many_it_left_out(tmp_path):
    many = [(f"state/some_long_file_name_{i:02d}.json", "SomeLongModelName", [f"field_{j}" for j in range(10)], 1,
             False) for i in range(SKEW_SUMMARY_MAX + 5)]
    _publish(tmp_path, _summary(*many))
    c = health.check_schema_skew(_ctx(tmp_path), "icm-demo")
    assert c is not None and c.status == "warn"
    assert c.reason.endswith("+20 more (health --json)")                  # 25 distinct, 5 named
    assert sum(f"some_long_file_name_{i:02d}" in c.reason for i in range(25)) <= 5
    assert len(c.reason) <= 600                                          # ~400 for the list plus the sentence


def test_a_refusal_fails_for_59_minutes_then_warns_with_its_time(tmp_path):
    def at(minutes: int) -> health.Check:
        _publish(tmp_path, _summary(("approvals/decisions:p1", "BusDecision", ["max_lots"], 1, True),
                                    refused_at=NOW - pd.Timedelta(minutes=minutes)))
        c = health.check_schema_skew(_ctx(tmp_path), "icm-demo")
        assert c is not None
        return c
    assert health.SKEW_REFUSED_FAIL_S == 3600
    assert at(59).status == "fail"
    late = at(61)
    assert late.status == "warn" and "refused" in late.reason and "2026-01-15 14:59" in late.reason
    assert "approvals/decisions:p1" in late.reason


def test_an_older_block_without_the_refusal_time_reads_as_fail(tmp_path):
    block = _summary(("approvals/decisions:p1", "BusDecision", ["max_lots"], 1, True))
    del block["last_refused_utc"]
    _publish(tmp_path, block)
    c = health.check_schema_skew(_ctx(tmp_path), "icm-demo")
    assert c is not None and c.status == "fail"


def test_a_new_refusal_after_the_warn_fails_again_and_re_alerts(tmp_path):
    watch = health.HealthWatch(tmp_path)
    key = ("approvals/decisions:p1", "BusDecision", ["max_lots"], 1, True)
    _publish(tmp_path, _summary(key, refused_at=NOW - pd.Timedelta(minutes=90)))
    report = health.run_checks(_ctx(tmp_path))
    assert {c.name: c.status for c in report.checks}["schema_skew:icm-demo"] == "warn"
    watch.record(report)
    _publish(tmp_path, _summary(key, ("approvals/decisions:p2", "BusDecision", ["max_lots"], 1, True),
                                refused_at=NOW - pd.Timedelta(minutes=2)))
    text = watch.poll(health.run_checks(_ctx(tmp_path)))
    assert text is not None and "FAIL schema_skew:icm-demo" in text


def test_a_skew_check_that_raises_is_a_fail_of_its_own(tmp_path, monkeypatch):
    _publish(tmp_path, _summary())

    def boom(*_a, **_k):
        raise TypeError("corrupt")
    monkeypatch.setattr(health, "check_schema_skew", boom)
    got = {c.name: c.status for c in health.run_checks(_ctx(tmp_path)).checks}
    assert got["schema_skew:icm-demo"] == "fail"


def test_a_malformed_skew_block_warns_instead_of_raising(tmp_path):
    _publish(tmp_path, {"count": "lots"})
    c = health.check_schema_skew(_ctx(tmp_path), "icm-demo")
    assert c is not None and c.status == "warn" and "unreadable" in c.reason


def test_run_checks_includes_the_skew_check_per_account_and_alerts_once_per_change(tmp_path):
    _publish(tmp_path, _summary(("risk_<account>.json", "RiskState", ["new_cap"], 1, False)))
    watch = health.HealthWatch(tmp_path)
    report = health.run_checks(_ctx(tmp_path))
    assert {c.name: c.status for c in report.checks}["schema_skew:icm-demo"] == "warn"
    first = watch.poll(report)
    assert first is not None and "WARN schema_skew:icm-demo" in first
    again = watch.poll(health.run_checks(_ctx(tmp_path)))
    assert again is None                                                                  # not repeated
    _publish(tmp_path, _summary(("approvals/decisions:p1", "BusDecision", ["max_lots"], 1, True)))
    worse = watch.poll(health.run_checks(_ctx(tmp_path)))
    assert worse is not None and "FAIL schema_skew:icm-demo" in worse                     # warn -> fail alerts again


def test_health_reads_a_skew_block_a_newer_engine_wrote(tmp_path):
    """ADR 0010 / S12: a newer engine's extra field in the block (top level and per entry) is ignored, not 'unreadable'."""
    block = _summary(("approvals/decisions:p1", "BusDecision", ["max_lots"], 1, True))
    block["future_field"] = 1
    block["entries"][0]["future_field"] = "x"
    _publish(tmp_path, block)
    c = health.check_schema_skew(_ctx(tmp_path), "icm-demo")
    assert c is not None and c.status == "fail" and "approvals/decisions:p1" in c.reason


# ------------------------------------------------------------------ supervisor and scheduler (SRE review follow-up)
# They read state tolerantly too (aux.db scheduler_jobs -> JobState; the supervisor's files): a newer job-control field
# ignored after a rollback must reach the owner, not only their logs. Each publishes the same bounded block in its
# heartbeat_<service>.json (the Heartbeat writer, which the scheduler beats from a thread even during a long job).
def _hb_skew(tmp_path: Path, service: str) -> dict:
    return json.loads(health.heartbeat_path(tmp_path, service).read_text())["schema_skew"]


def test_the_supervisor_heartbeat_publishes_the_schema_skew_block(tmp_path, fresh_skew):
    from goldbot.ops import run as ops_run
    from goldbot.risk.supervisor import Supervisor
    sup, hb = Supervisor(tmp_path), health.Heartbeat(tmp_path, "supervisor")
    note_schema_skew("supervisor.json", "SupervisorState", ["new_cap"])
    ops_run.supervisor_pass(sup, hb)                                      # what run_supervisor loops on
    sk = _hb_skew(tmp_path, "supervisor")
    assert sk["count"] == 1 and sk["refused"] is False
    assert sk["entries"] == [{"source": "supervisor.json", "model": "SupervisorState", "fields": ["new_cap"],
                              "count": 1, "refused": False}]
    assert "schema_skew" not in json.loads((tmp_path / "supervisor.json").read_text())   # engines' file unchanged


def test_the_scheduler_heartbeat_publishes_a_newer_job_state_field_it_ignored(tmp_path, fresh_skew):
    from goldbot.db.bookkeeping import save_jobs
    from goldbot.ops.scheduler import Scheduler
    first = Scheduler(tmp_path / "scheduler.json")
    with first.db.write() as conn:                                        # a newer release's job-control field
        save_jobs(conn, {"retrain": json.dumps({"runs": 1, "paused_until": "2026-10-12T00:00:00Z"})})
    Scheduler(tmp_path / "scheduler.json")                                # the older release restarts and reads it
    health.start_heartbeat(tmp_path, "scheduler", every_s=3600)           # as run_scheduler does
    f = health.heartbeat_path(tmp_path, "scheduler")
    deadline = time.monotonic() + 5
    while not f.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    sk = _hb_skew(tmp_path, "scheduler")
    assert sk["count"] == 1
    assert sk["entries"][0] == {"source": "aux.db:scheduler_jobs", "model": "JobState", "fields": ["paused_until"],
                                "count": 1, "refused": False}


def test_a_failed_summary_build_still_beats_and_publishes_an_error_block(tmp_path, monkeypatch):
    def boom() -> None:
        raise RuntimeError("bug")
    monkeypatch.setattr(health, "schema_skew_summary", boom)
    assert health.Heartbeat(tmp_path, "telegram").beat()
    saved = json.loads(health.heartbeat_path(tmp_path, "telegram").read_text())
    assert "ts" in saved                                                  # liveness first
    assert saved["schema_skew"] == {"error": "RuntimeError"}              # the type, never the message
    c = health.check_service_schema_skew(_ctx(tmp_path), "telegram")
    assert c is not None and (c.name, c.status) == ("schema_skew:svc:telegram", "warn")
    assert "unreadable" in c.reason and "RuntimeError" in c.reason
    assert c.name.startswith(health.NOTIFY_ON_WARN)                       # the owner is told


def test_a_failed_summary_build_logs_its_traceback_once_per_process(tmp_path, monkeypatch, caplog):
    def boom() -> None:
        raise RuntimeError("bug")
    monkeypatch.setattr(health, "schema_skew_summary", boom)
    monkeypatch.setattr(health, "_skew_build_failures", 0)
    clock = [1_000.0]
    hb = health.Heartbeat(tmp_path, "news", every_s=1, clock=lambda: clock[0])
    caplog.set_level("DEBUG", logger=health.log.name)
    for _ in range(3):
        assert hb.beat()
        clock[0] += 2
    with_tb = [r for r in caplog.records if r.exc_info is not None]
    assert len(with_tb) == 1 and with_tb[0].levelname == "ERROR"
    counts = [r.getMessage() for r in caplog.records if r.levelname == "DEBUG" and "schema_skew" in r.getMessage()]
    assert [m.split()[-1] for m in counts] == ["2", "3"]                  # only a count after the first
    assert health._skew_build_failures == 3


def _publish_hb(tmp_path: Path, service: str, skew: object | None) -> None:
    doc: dict = {"service": service, "ts": NOW.timestamp()}
    if skew is not None:
        doc["schema_skew"] = skew
    health.heartbeat_path(tmp_path, service).write_text(json.dumps(doc))


def test_every_heartbeat_service_is_checked_for_skew():
    assert health.SKEW_SERVICES == health.HEARTBEAT_SERVICES
    assert "telegram" in health.SKEW_SERVICES                             # the decider: the blind spot that matters


@pytest.mark.parametrize("service", health.HEARTBEAT_SERVICES)
def test_health_checks_the_skew_block_of_each_service(tmp_path, service):
    name = f"schema_skew:svc:{service}"
    _publish_hb(tmp_path, service, _summary())
    c = health.check_service_schema_skew(_ctx(tmp_path), service)
    assert c is not None and (c.name, c.status) == (name, "ok")
    _publish_hb(tmp_path, service, _summary(("aux.db:scheduler_jobs", "JobState", ["paused_until"], 3, False)))
    c = health.check_service_schema_skew(_ctx(tmp_path), service)
    assert c is not None and c.status == "warn" and "aux.db:scheduler_jobs" in c.reason and "rollback" in c.reason
    assert c.name == name and c.name.startswith(health.NOTIFY_ON_WARN)
    _publish_hb(tmp_path, service, _summary(("some_strict_file", "Strict", ["x"], 1, True),
                                            refused_at=NOW - pd.Timedelta(minutes=59)))
    c = health.check_service_schema_skew(_ctx(tmp_path), service)
    assert c is not None and c.status == "fail" and "some_strict_file" in c.reason
    assert "click" not in c.reason                                         # not an approval: no click was dropped
    _publish_hb(tmp_path, service, _summary(("approvals/decisions:p1", "BusDecision", ["max_lots"], 1, True),
                                            refused_at=NOW - pd.Timedelta(minutes=59)))
    c = health.check_service_schema_skew(_ctx(tmp_path), service)
    assert c is not None and c.status == "fail" and "click" in c.reason    # wherever the bus refused it
    _publish_hb(tmp_path, service, _summary(("some_strict_file", "Strict", ["x"], 1, True),
                                            refused_at=NOW - pd.Timedelta(minutes=61)))
    c = health.check_service_schema_skew(_ctx(tmp_path), service)
    assert c is not None and c.status == "warn" and "2026-01-15 14:59" in c.reason
    _publish_hb(tmp_path, service, {"count": "lots"})
    c = health.check_service_schema_skew(_ctx(tmp_path), service)
    assert c is not None and c.status == "warn" and "unreadable" in c.reason


def test_a_service_check_never_clashes_with_an_account_of_the_same_name(tmp_path):
    ctx = health.HealthContext(state_dir=tmp_path, now=NOW, get_secret=lambda k: None,
                               accounts=[health.AccountRef(account_id="telegram")])
    (tmp_path / "engine_telegram.json").write_text(json.dumps({"account": "telegram", "schema_skew": _summary()}))
    _publish_hb(tmp_path, "telegram", _summary(("x", "M", ["f"], 1, False)))
    got = {c.name: c.status for c in health.run_checks(ctx).checks}
    assert (got["schema_skew:telegram"], got["schema_skew:svc:telegram"]) == ("ok", "warn")


@pytest.mark.parametrize("service", health.HEARTBEAT_SERVICES)
def test_an_older_heartbeat_without_the_block_is_skipped(tmp_path, service):
    assert health.check_service_schema_skew(_ctx(tmp_path), service) is None      # no heartbeat yet
    _publish_hb(tmp_path, service, None)
    assert health.check_service_schema_skew(_ctx(tmp_path), service) is None      # an older release's heartbeat
    names = {c.name for c in health.run_checks(_ctx(tmp_path)).checks}
    assert f"schema_skew:svc:{service}" not in names


def test_run_checks_includes_the_service_skew_checks_and_alerts_on_a_warn(tmp_path):
    for svc in health.HEARTBEAT_SERVICES:
        _publish_hb(tmp_path, svc, _summary())
    _publish_hb(tmp_path, "telegram", _summary(("approvals/x", "Proposal", ["new_field"], 1, False)))
    report = health.run_checks(_ctx(tmp_path))
    got = {c.name: c.status for c in report.checks}
    assert {s: got[f"schema_skew:svc:{s}"] for s in health.HEARTBEAT_SERVICES} == {
        s: ("warn" if s == "telegram" else "ok") for s in health.HEARTBEAT_SERVICES}
    text = health.HealthWatch(tmp_path).poll(report)
    assert text is not None and "WARN schema_skew:svc:telegram" in text


def test_a_service_skew_check_that_raises_is_a_fail_of_its_own(tmp_path, monkeypatch):
    def boom(*_a: object, **_k: object) -> None:
        raise TypeError("corrupt")
    monkeypatch.setattr(health, "check_schema_skew", boom)
    got = {c.name: c.status for c in health.run_checks(_ctx(tmp_path)).checks}
    assert all(got[f"schema_skew:svc:{s}"] == "fail" for s in health.HEARTBEAT_SERVICES)
