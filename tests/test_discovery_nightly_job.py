"""The brain's `discovery_nightly` scheduler job (ADR 0090; ADR 0083's reasons for retiring `autodisc_nightly`): the
search runs in a child process at a low CPU priority, under a memory cap and a wall-clock budget, in a quiet slot on
the single-threaded scheduler, and is killed when it overruns."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from goldbot.config import DiscoverySettings, load_settings
from goldbot.ops import bounded as B
from goldbot.ops.jobs import JOBS, discovery_nightly
from goldbot.ops.scheduler import schedule_of

SLOT = pd.Timestamp("2026-11-18 01:00", tz="UTC")


def env_var(name: str) -> str | None:
    """Module level, so a spawned child can unpickle it."""
    return os.environ.get(name)


def test_the_job_is_registered_with_a_daily_slot_clear_of_the_other_nightly_jobs():
    s = load_settings()
    assert JOBS["discovery_nightly"] is discovery_nightly
    cfg = s.scheduler.discovery_nightly
    assert cfg.kind == "daily" and cfg.max_late_hours <= 2
    schedule_of(cfg)
    start = pd.Timedelta(cfg.at + ":00")
    end = start + pd.Timedelta(seconds=s.research.discovery.budget_s + s.research.discovery.kill_grace_s)
    for name in type(s.scheduler).model_fields:
        other = getattr(s.scheduler, name)
        if name == "discovery_nightly" or other.kind != "daily":
            continue
        at = pd.Timedelta(other.at + ":00")
        assert not (start <= at < end), f"{name} at {other.at} falls inside the discovery window"


def test_the_job_skips_without_starting_a_child_when_off():
    ctx = SimpleNamespace(settings=load_settings(), state_dir=None)
    assert load_settings().research.discovery.enabled is False
    assert "skipped" in discovery_nightly(ctx, SLOT)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [{"nice": 5}, {"budget_s": 9000}, {"memory_mb": 64}, {"kill_grace_s": 3600},
                                 {"timeframes": ["15m"]}, {"timeframes": []}])
def test_discovery_settings_can_only_stay_gentle_on_the_brain(bad):
    with pytest.raises(ValueError):
        DiscoverySettings(**bad)


def test_a_child_that_overruns_its_budget_is_killed_and_reported():
    t0 = time.monotonic()
    out = B.run_bounded(time.sleep, (60,), budget_s=1.0, nice=15, memory_mb=None)
    assert out.timed_out and not out.ok and time.monotonic() - t0 < 30


def test_the_child_runs_at_the_configured_nice_level_and_returns_its_value():
    out = B.run_bounded(os.nice, (0,), budget_s=60, nice=15, memory_mb=None)
    assert out.ok and out.value >= 15


def test_a_child_error_is_reported_not_raised():
    out = B.run_bounded(int, ("not a number",), budget_s=60, nice=15, memory_mb=None)
    assert not out.ok and not out.timed_out and "ValueError" in (out.error or "")


def test_the_memory_cap_sets_the_data_segment_limit(monkeypatch):
    calls = []
    monkeypatch.setattr(B.resource, "setrlimit", lambda what, lim: calls.append((what, lim)))
    monkeypatch.setattr(B.sys, "platform", "linux")
    B.cap_memory(1024)
    assert calls == [(B.resource.RLIMIT_DATA, (1024 * 2**20, 1024 * 2**20))]


def test_the_child_starts_with_one_blas_thread_and_the_parent_env_is_restored(monkeypatch):
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    out = B.run_bounded(env_var, ("OMP_NUM_THREADS",), budget_s=60, nice=15, memory_mb=None)
    assert out.value == "1" and "OMP_NUM_THREADS" not in os.environ


def test_an_enabled_job_without_bars_fails_through_its_child_instead_of_skipping(tmp_path):
    s = load_settings()
    on = s.model_copy(update={"research": s.research.model_copy(update={
        "discovery": s.research.discovery.model_copy(update={"enabled": True, "bars_path": None})})})
    ctx = SimpleNamespace(settings=on, state_dir=tmp_path)
    with pytest.raises(RuntimeError, match="bars_path"):
        discovery_nightly(ctx, SLOT)  # type: ignore[arg-type]


# ------------------------------------------------------------------ review round 1 (sre b, c; code review 1, 2)
def enabled_ctx(tmp_path):
    s = load_settings()
    on = s.model_copy(update={"research": s.research.model_copy(update={
        "discovery": s.research.discovery.model_copy(update={"enabled": True})})})
    return SimpleNamespace(settings=on, state_dir=tmp_path)


def discovery_rows(state_dir):
    from goldbot.db.connection import Database
    with Database.open(state_dir, "aux").read() as conn:
        return conn.execute("SELECT status, body FROM discovery_runs").fetchall()


def test_the_child_volunteers_for_the_oom_killer_on_linux(monkeypatch, tmp_path):
    adj = tmp_path / "oom_score_adj"
    monkeypatch.setattr(B.sys, "platform", "linux")
    B.volunteer_for_oom(adj)
    assert adj.read_text() == "500"
    B.volunteer_for_oom(tmp_path / "no" / "such" / "file")        # an OSError never stops the child
    monkeypatch.setattr(B.sys, "platform", "darwin")
    B.volunteer_for_oom(tmp_path / "mac")
    assert not (tmp_path / "mac").exists()


def test_the_child_reports_its_peak_rss():
    out = B.run_bounded(os.nice, (0,), budget_s=60, nice=15, memory_mb=None)
    assert out.ok and out.max_rss_mb is not None and 5 < out.max_rss_mb < 4096


def test_a_failed_process_start_closes_both_pipe_ends(monkeypatch):
    ends = []

    class End:
        closed = False

        def close(self):
            self.closed = True

    class Ctx:
        def Pipe(self, duplex=False):
            ends.extend([End(), End()])
            return ends[0], ends[1]

        def Process(self, **kw):
            return SimpleNamespace(start=lambda: (_ for _ in ()).throw(RuntimeError("cannot pickle")),
                                   is_alive=lambda: False)

    monkeypatch.setattr(B.mp, "get_context", lambda kind: Ctx())
    with pytest.raises(RuntimeError, match="pickle"):
        B.run_bounded(os.nice, (0,), budget_s=60, nice=15, memory_mb=None)
    assert len(ends) == 2 and all(e.closed for e in ends)


def test_the_job_result_carries_elapsed_and_peak_rss_and_warns_near_its_budget(monkeypatch, tmp_path):
    ctx = enabled_ctx(tmp_path)
    budget = ctx.settings.research.discovery.budget_s
    for elapsed, warn in ((0.5 * budget, False), (0.85 * budget, True)):
        monkeypatch.setattr(B, "run_bounded", lambda *a, e=elapsed, **k: B.BoundedResult(
            ok=True, value={"status": "ok", "health": "discovery ok: K 3"}, elapsed_s=e, max_rss_mb=1500.0))
        got = discovery_nightly(ctx, SLOT)
        assert got["elapsed_s"] == elapsed and got["max_rss_mb"] == 1500.0
        assert ("80 %" in got["health"]) is warn and "discovery ok: K 3" in got["health"]


@pytest.mark.parametrize(("result", "status"), [
    (B.BoundedResult(ok=False, timed_out=True, error="killed after its 5700 s budget"), "killed"),
    (B.BoundedResult(ok=False, error="MemoryError: "), "failed"),
    (B.BoundedResult(ok=False, error="the child died with exit code -9", exitcode=-9), "failed")])
def test_every_child_that_does_not_finish_writes_a_discovery_runs_row(monkeypatch, tmp_path, result, status):
    monkeypatch.setattr(B, "run_bounded", lambda *a, **k: result)
    with pytest.raises(RuntimeError, match="discovery_nightly"):
        discovery_nightly(enabled_ctx(tmp_path), SLOT)
    rows = discovery_rows(tmp_path)
    assert [r[0] for r in rows] == [status] and (result.error or "") in rows[0][1]
