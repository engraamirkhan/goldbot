"""Host health (outage 2026-11-12..14): CPU steal on the brain and the needrestart drop-in. Both warn only."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from goldbot.ops import health
from goldbot.ops.checks import host
from goldbot.ops.health import Check, HealthContext, Heartbeat
from tests.test_health import NOW, make_ctx

HOUR = 3600


def _stat(path: Path, steal: int, total: int) -> None:
    """A /proc/stat whose aggregate line has `steal` jiffies of `total` (the rest idle)."""
    path.write_text(f"cpu  {total - steal} 0 0 0 0 0 0 {steal} 0 0\ncpu0 1 2 3 4 5 6 7 8 0 0\n", encoding="ascii")


def _run(tmp_path: Path, steal_per_min: list[int]) -> dict[str, float] | None:
    """One sampler fed a minute per entry: `steal_per_min[i]` % steal in minute i."""
    stat = tmp_path / "stat"
    sampler = host.StealSampler(stat)
    steal = total = 0
    _stat(stat, steal, total)
    out = sampler.sample(0.0)
    for i, pct in enumerate(steal_per_min, start=1):
        steal, total = steal + pct * 60, total + 100 * 60
        _stat(stat, steal, total)
        out = sampler.sample(60.0 * i)
    return out


def test_the_sampler_reads_the_aggregate_cpu_line_of_proc_stat(tmp_path):
    (tmp_path / "stat").write_text("cpu  10 2 3 80 1 1 1 7 4 0\n", encoding="ascii")
    assert host.read_cpu(tmp_path / "stat") == (7, 105)
    assert host.read_cpu(tmp_path / "missing") is None


def test_steal_is_measured_over_the_last_ten_minutes_with_its_lowest_minute(tmp_path):
    out = _run(tmp_path, [5] * 5 + [40] * 10)
    assert out == {"window_s": 600.0, "steal_pct": 40.0, "min_pct": 40.0}       # older minutes dropped
    assert _run(tmp_path, [40] * 3) == {"window_s": 180.0, "steal_pct": 40.0, "min_pct": 40.0}


def _beat(tmp_path: Path, service: str, block: dict | None, age_s: float = 30) -> None:
    doc: dict = {"service": service, "ts": NOW.timestamp() - age_s}
    if block is not None:
        doc["cpu_steal"] = block
    health.heartbeat_path(tmp_path, service).write_text(json.dumps(doc), encoding="utf-8")


def _steal(tmp_path: Path) -> Check:
    c = health.check_cpu_steal(make_ctx(tmp_path))
    assert c is not None
    return c


def _restart(ctx: HealthContext) -> Check:
    c = health.check_needrestart(ctx)
    assert c is not None
    return c


def test_steal_above_twenty_percent_in_every_minute_for_ten_minutes_warns(tmp_path):
    """SRE 2026-11-14: cloud steal of 35-58% starved the engines; nothing crashed, so no check showed it."""
    _beat(tmp_path, "scheduler", {"window_s": 600, "steal_pct": 41, "min_pct": 35})
    c = health.check_cpu_steal(make_ctx(tmp_path))
    assert c is not None and (c.name, c.status) == ("cpu_steal", "warn") and "41%" in c.reason
    assert c.name.startswith(health.NOTIFY_ON_WARN)                  # the owner is told (after the alert window)
    assert not health.alerts_at_once(c)                              # ... never at once, and it halts nothing


def test_a_short_spike_or_a_filling_window_does_not_warn(tmp_path):
    _beat(tmp_path, "scheduler", {"window_s": 600, "steal_pct": 30, "min_pct": 4})      # one bad stretch, not sustained
    assert _steal(tmp_path).status == "ok"
    _beat(tmp_path, "scheduler", {"window_s": 240, "steal_pct": 60, "min_pct": 55})     # just restarted
    c = _steal(tmp_path)
    assert c.status == "ok" and "filling" in c.reason


def test_the_freshest_heartbeat_with_a_sample_is_used_and_none_omits_the_check(tmp_path):
    assert health.check_cpu_steal(make_ctx(tmp_path)) is None                       # no heartbeat carries one
    _beat(tmp_path, "api", {"window_s": 600, "steal_pct": 50, "min_pct": 45}, age_s=HOUR)
    assert health.check_cpu_steal(make_ctx(tmp_path)) is None                       # stale: not this host now
    _beat(tmp_path, "telegram", {"window_s": 600, "steal_pct": 2, "min_pct": 1}, age_s=10)
    _beat(tmp_path, "news", {"window_s": 600, "steal_pct": 50, "min_pct": 45}, age_s=50)
    assert _steal(tmp_path).status == "ok"



def test_the_service_heartbeat_publishes_the_steal_sample(tmp_path):
    stat = tmp_path / "stat"
    clock = [NOW.timestamp()]
    hb = Heartbeat(tmp_path, "scheduler", clock=lambda: clock[0], proc_stat=stat)
    _stat(stat, 0, 0)
    assert hb.beat()
    for i in range(1, 11):
        clock[0] += 60
        _stat(stat, 30 * 60 * i, 100 * 60 * i)
        assert hb.beat()
    saved = json.loads(health.heartbeat_path(tmp_path, "scheduler").read_text())
    assert saved["cpu_steal"] == {"window_s": 600.0, "steal_pct": 30.0, "min_pct": 30.0}
    c = health.check_cpu_steal(make_ctx(tmp_path, now=pd.Timestamp(clock[0], unit="s", tz="UTC")))
    assert c is not None and c.status == "warn"


def test_a_heartbeat_without_proc_stat_publishes_no_steal_block(tmp_path):
    hb = Heartbeat(tmp_path, "scheduler", proc_stat=tmp_path / "missing")
    assert hb.beat()
    assert "cpu_steal" not in json.loads(health.heartbeat_path(tmp_path, "scheduler").read_text())


# ------------------------------------------------------------------------------------------------ needrestart
def test_a_missing_needrestart_drop_in_warns(tmp_path):
    """2026-11-14 06:58 (a Saturday): unattended-upgrades + needrestart restarted 12 goldbot units on the brain, which
    never got the drop-in brain_bootstrap.sh installs."""
    etc = tmp_path / "etc"
    (etc / "needrestart" / "conf.d").mkdir(parents=True)
    c = health.check_needrestart(make_ctx(tmp_path, etc_dir=etc))
    assert c is not None and (c.name, c.status) == ("needrestart", "warn")
    assert "/etc/needrestart/conf.d/goldbot.conf" in c.reason and c.name.startswith(health.NOTIFY_ON_WARN)
    (etc / "needrestart" / "conf.d" / "goldbot.conf").write_text("$nrconf{override_rc} = {};\n")
    assert _restart(make_ctx(tmp_path, etc_dir=etc)).status == "ok"


def test_needrestart_not_installed_is_ok_and_no_etc_dir_omits_the_check(tmp_path):
    (tmp_path / "etc").mkdir()
    assert _restart(make_ctx(tmp_path, etc_dir=tmp_path / "etc")).status == "ok"
    assert health.check_needrestart(make_ctx(tmp_path)) is None


def test_the_runtime_context_reads_etc_on_linux_only(monkeypatch, tmp_path):
    monkeypatch.setattr(health.sys, "platform", "linux")
    assert health.HealthContext.from_runtime(tmp_path).etc_dir == Path("/etc")
    monkeypatch.setattr(health.sys, "platform", "darwin")
    assert health.HealthContext.from_runtime(tmp_path).etc_dir is None
