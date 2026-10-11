"""ADR 0010 follow-up: state written by a newer release (expected only after a rollback) reaches the owner, not only
the log. The engine publishes a bounded `schema_skew` summary in engine_<account>.json and health check
`schema_skew:<account>` warns while any skew is present (naming the sources) and fails when a strict reader refused a
record, i.e. an owner's click was dropped (bus.decisions_for)."""
import json
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


def test_the_summary_is_empty_when_nothing_was_read_from_a_newer_release(fresh_skew):
    assert base.schema_skew_summary() == SkewSummary(count=0, reads=0, refused=False, entries=[], truncated=0)


def test_the_summary_names_source_model_fields_count_and_whether_the_entry_was_refused(fresh_skew):
    note_schema_skew("risk_<account>.json", "RiskState", ["new_cap"])
    note_schema_skew("risk_<account>.json", "RiskState", ["new_cap"])
    note_schema_skew("approvals/decisions:p1", "BusDecision", ["max_lots"], refused=True)
    s = base.schema_skew_summary()
    assert (s.count, s.reads, s.refused, s.truncated) == (2, 3, True, 0)
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
    assert sk["refused"] is True and sk["count"] == 1
    assert sk["entries"] == [{"source": "approvals/decisions:icm-demo-1-x", "model": "BusDecision",
                              "fields": ["max_lots"], "count": 1, "refused": True}]


# ---------------------------------------------------------------------------------------------- health
def _ctx(tmp_path: Path) -> health.HealthContext:
    return health.HealthContext(state_dir=tmp_path, now=NOW, get_secret=lambda k: None,
                                accounts=[health.AccountRef(account_id="icm-demo")])


def _publish(tmp_path: Path, skew: object) -> None:
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps({"account": "icm-demo", "schema_skew": skew}))


def _summary(*entries: tuple[str, str, list[str], int, bool]) -> dict:
    counts = Counter({(s, m, tuple(f)): n for s, m, f, n, _ in entries})
    return skew_summary(counts, {(s, m, tuple(f)) for s, m, f, _, r in entries if r}).model_dump(mode="json")


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


def test_health_names_how_many_sources_the_bounded_summary_left_out(tmp_path):
    many = [(f"src{i:02d}", "M", ["x"], 1, False) for i in range(SKEW_SUMMARY_MAX + 5)]
    _publish(tmp_path, _summary(*many))
    c = health.check_schema_skew(_ctx(tmp_path), "icm-demo")
    assert c is not None and c.status == "warn" and "+5 more" in c.reason


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
    assert again is None or "schema_skew:icm-demo:" not in again                         # not repeated
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
