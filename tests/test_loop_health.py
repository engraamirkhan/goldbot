"""R140: the feedback-loop health check (goldbot/ops/checks/loop.py) fails when a feedback writer is stale or failing,
reports "can't check" (never a pass) on unreadable state, and the daily loop_check job opens a `ci-loop` issue."""
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.ops import health
from goldbot.ops.checks import loop
from goldbot.research.daily_review import VariantProposal, WeeklyRollup, mark_weekly_consumed, weekly_path
from tests.test_health import SETTINGS, make_ctx, write

NOW = pd.Timestamp("2026-11-11 14:00", tz="UTC")          # Wednesday
FORBIDDEN = ("failed: HTTP 403 on POST uploads.github.com/repos/o/r/releases/404493121/assets: the keyring "
             "`github-token` lacks the fine-grained permission 'Contents: Read and write'")


def _job(finished: pd.Timestamp | None, *, ok: bool | None = True, result: dict[str, Any] | None = None,
         error: str | None = None) -> dict[str, Any]:
    return {"last_started": finished.isoformat() if finished is not None else None,
            "last_finished": finished.isoformat() if finished is not None else None,
            "last_ok": ok, "last_error": error, "last_result": result, "failures": 0 if ok is not False else 1}


def _scheduler(tmp: Path, **jobs: dict[str, Any]) -> None:
    write(tmp / "scheduler.json", {"ts": NOW.isoformat(), "jobs": jobs})


def _weights(tmp: Path, **runs: pd.Timestamp | None) -> None:
    con = sqlite3.connect(tmp / "aux.db")
    con.execute("CREATE TABLE exploration_broker_weights (broker TEXT, run_ns INTEGER)")
    con.execute("CREATE TABLE exploration_obs (broker TEXT, taken INTEGER, exit_ns INTEGER)")
    for broker, ts in runs.items():
        con.execute("INSERT INTO exploration_obs VALUES (?, 1, ?)", (broker, int((NOW - pd.Timedelta("5D")).value)))
        if ts is not None:
            con.execute("INSERT INTO exploration_broker_weights VALUES (?, ?)", (broker, int(ts.value)))
    con.commit()
    con.close()


def _reviews(tmp: Path, *days: str) -> None:
    for d in days:
        write(tmp / "aux" / "daily_review" / f"{d}.json", {"day": d})


def _healthy(tmp: Path) -> None:
    review = NOW.normalize() + pd.Timedelta("6h15min")
    _scheduler(tmp, daily_review=_job(review, result={"day": "2026-11-10"}),
               research_director=_job(NOW - pd.Timedelta("4D"), result={"registry_sync": 206}))
    _weights(tmp, icm=NOW - pd.Timedelta("15h"), vantage=NOW - pd.Timedelta("15h"))
    _reviews(tmp, "2026-11-10", "2026-11-09", "2026-11-08")
    write(weekly_path(tmp, "2026-11-08"), json.loads(WeeklyRollup(week_ending="2026-11-08",
                                                                  generated_utc=NOW - pd.Timedelta("2D")).model_dump_json()))


def _check(tmp: Path, now: pd.Timestamp = NOW) -> health.Check:
    return loop.check_feedback_loop(make_ctx(tmp, now=now))


def test_healthy_loop_passes(tmp_path):
    _healthy(tmp_path)
    c = _check(tmp_path)
    assert c.status == "ok", c.reason
    assert c.name == "feedback_loop"


def test_registry_sync_403_fails_the_check(tmp_path):
    _healthy(tmp_path)
    _scheduler(tmp_path, daily_review=_job(NOW.normalize() + pd.Timedelta("6h15min")),
               cpcv_quarterly=_job(NOW - pd.Timedelta("10D"), result={"registry_sync": 206}),
               monthly_research=_job(NOW - pd.Timedelta("9D"), result={"registry_sync": {"before": FORBIDDEN}}))
    c = _check(tmp_path)
    assert c.status == "fail"
    assert "registry" in c.reason and "403" in c.reason
    assert "404493121" not in c.reason                    # ids scrubbed: the text goes to a public issue


def test_registry_sync_superseded_failure_is_not_reported(tmp_path):
    _healthy(tmp_path)                                    # research_director (newer) synced after the 403
    _scheduler(tmp_path, daily_review=_job(NOW.normalize() + pd.Timedelta("6h15min")),
               monthly_research=_job(NOW - pd.Timedelta("9D"), result={"registry_sync": FORBIDDEN}),
               research_director=_job(NOW - pd.Timedelta("4D"), result={"registry_sync": 206}))
    assert _check(tmp_path).status == "ok"


def test_registry_outbox_unpublished_for_36h_fails(tmp_path):
    _healthy(tmp_path)
    box = tmp_path / "registry_outbox"
    box.mkdir()
    entry = box / "b17fc166242b390f.jsonl"
    entry.write_text('{"ts": "x"}\n')
    old = (NOW - pd.Timedelta("6D")).timestamp()
    os.utime(entry, (old, old))
    c = _check(tmp_path)
    assert c.status == "fail" and "registry_publish.sh" in c.reason
    os.utime(entry, (NOW.timestamp() - 3600, NOW.timestamp() - 3600))
    assert _check(tmp_path).status == "ok"


def test_registry_outbox_pending_since_marker_wins_over_mtime(tmp_path):
    """A newer payload replaces the file (new mtime); the marker keeps how long rows have waited."""
    from goldbot.research.registry_sync import _outbox_clear, _outbox_write
    box = tmp_path / "registry_outbox"
    _outbox_write(box, [{"ts": "a"}])
    since = loop.outbox_pending_since(box)
    assert since is not None
    _outbox_write(box, [{"ts": "a"}, {"ts": "b"}])
    assert loop.outbox_pending_since(box) == since
    _outbox_clear(box)
    assert loop.outbox_pending_since(box) is None


def test_weights_older_than_36h_fail_closed(tmp_path):
    _healthy(tmp_path)
    (tmp_path / "aux.db").unlink()
    _weights(tmp_path, icm=NOW - pd.Timedelta("40h"), vantage=NOW - pd.Timedelta("15h"))
    c = _check(tmp_path)
    assert c.status == "fail" and "icm" in c.reason and "weights" in c.reason
    (tmp_path / "aux.db").unlink()
    _weights(tmp_path, icm=NOW - pd.Timedelta("15h"), vantage=None)     # a broker that trades but never got weights
    c = _check(tmp_path)
    assert c.status == "fail" and "vantage" in c.reason


def test_weights_age_skips_the_weekend(tmp_path):
    """The learner runs Mon-Fri: Friday's weights are fresh on Monday morning, and stale by Tuesday midday."""
    _healthy(tmp_path)
    (tmp_path / "aux.db").unlink()
    friday = pd.Timestamp("2026-11-06 23:10", tz="UTC")
    _weights(tmp_path, icm=friday)
    def weights(now: pd.Timestamp) -> str:
        return next(i.verdict for i in loop.loop_items(tmp_path, now, SETTINGS) if i.writer == "weights")
    assert weights(pd.Timestamp("2026-11-09 12:00", tz="UTC")) == "ok"           # Monday: 12.8 weekday hours
    assert weights(pd.Timestamp("2026-11-10 13:00", tz="UTC")) == "fail"         # Tuesday: Monday's run missed


def test_unreadable_state_reports_cant_check(tmp_path):
    _healthy(tmp_path)
    (tmp_path / "aux.db").write_bytes(b"not a database" * 100)
    (tmp_path / "scheduler.json").write_text("{")
    c = _check(tmp_path)
    assert c.status != "ok"
    assert "can't check" in c.reason
    assert c.name.startswith(health.NOTIFY_ON_WARN)        # a warn still reaches the owner


def test_daily_review_missing_for_its_slot_fails(tmp_path):
    _healthy(tmp_path)
    (tmp_path / "aux" / "daily_review" / "2026-11-10.json").unlink()
    c = _check(tmp_path, NOW + pd.Timedelta("6h"))         # past the 06:15 slot's 12 h grace
    assert c.status == "fail" and "2026-11-10" in c.reason


def test_weekly_review_missing_fails(tmp_path):
    _healthy(tmp_path)
    weekly_path(tmp_path, "2026-11-08").unlink()
    c = _check(tmp_path)
    assert c.status == "fail" and "weekly-2026-11-08" in c.reason


def test_weekly_proposals_unread_fail_until_consumed(tmp_path):
    _healthy(tmp_path)
    week = WeeklyRollup(week_ending="2026-11-01", generated_utc=NOW - pd.Timedelta("9D"),
                        proposals=[VariantProposal(title="t", rationale="r")])
    write(weekly_path(tmp_path, "2026-11-01"), json.loads(week.model_dump_json()))
    c = _check(tmp_path)
    assert c.status == "fail" and "weekly-2026-11-01" in c.reason and "unread" in c.reason
    mark_weekly_consumed(tmp_path, "weekly-2026-11-01.json", by="research_director", now=NOW)
    assert _check(tmp_path).status == "ok"


def test_errored_job_fails(tmp_path):
    _healthy(tmp_path)
    doc = json.loads((tmp_path / "scheduler.json").read_text())
    doc["jobs"]["nightly_costs"] = _job(NOW - pd.Timedelta("15h"), ok=False, error="boom at 10.0.0.1\nmore")
    write(tmp_path / "scheduler.json", doc)
    c = _check(tmp_path)
    assert c.status == "fail" and "nightly_costs" in c.reason and "10.0.0.1" not in c.reason


def test_registered_in_the_health_report(tmp_path):
    _healthy(tmp_path)
    names = [c.name for c in health.run_checks(make_ctx(tmp_path, now=NOW)).checks]
    assert "feedback_loop" in names
    assert health.check_feedback_loop is loop.check_feedback_loop


class Sink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def comment(self, number: int, body: str) -> str:
        raise AssertionError("loop_check never comments on a numbered issue")

    def open_or_comment(self, label: str, title: str, body: str) -> str:
        self.calls.append((label, title, body))
        return "https://example.invalid/1"


def test_loop_job_opens_a_ci_loop_issue_once_a_day(tmp_path):
    _healthy(tmp_path)
    _scheduler(tmp_path, daily_review=_job(NOW.normalize() + pd.Timedelta("6h15min")),
               monthly_research=_job(NOW - pd.Timedelta("1D"), result={"registry_sync": FORBIDDEN}))
    sink = Sink()
    out = loop.run_loop_check(tmp_path, NOW, SETTINGS, sink)
    assert out["status"] == "fail" and out["posted"] == "posted"
    assert len(sink.calls) == 1
    label, title, body = sink.calls[0]
    assert label == "ci-loop" and "403" in body and "404493121" not in body
    loop.run_loop_check(tmp_path, NOW + pd.Timedelta("1h"), SETTINGS, sink)
    assert len(sink.calls) == 1                           # once a day


def test_loop_job_posts_nothing_when_healthy_and_keeps_text_without_a_token(tmp_path):
    _healthy(tmp_path)
    sink = Sink()
    assert loop.run_loop_check(tmp_path, NOW, SETTINGS, sink)["status"] == "ok"
    assert sink.calls == []
    _scheduler(tmp_path, monthly_research=_job(NOW - pd.Timedelta("1D"), result={"registry_sync": FORBIDDEN}))
    out = loop.run_loop_check(tmp_path, NOW, SETTINGS, None)
    assert out["posted"] == "outbox"
