"""Host health checks: CPU steal on this host and the needrestart rule (outage 2026-11-12..14).

Outage 2026-11-12..14 (~205 entry-blocked minutes, SRE diagnosis): the cloud hosts were CPU-starved (hypervisor steal
35-58%) and no check showed it, because nothing crashed; and the brain lacked the needrestart drop-in, so
unattended-upgrades restarted 12 goldbot units at 06:58 on a Saturday. Both checks only warn: they never halt trading,
change an order or restart anything.

cpu_steal: the share of CPU time the hypervisor gave to other guests (/proc/stat `steal` over all jiffies). Health
checks read files the services write, so the sampling lives in the service heartbeat (`StealSampler`, driven by
goldbot/ops/health.py `Heartbeat` from `start_heartbeat`): every beat (60 s) reads /proc/stat, keeps the samples of
the last STEAL_WINDOW_S, and publishes `cpu_steal` {window_s, steal_pct, min_pct} in heartbeat_<service>.json.
"Sustained" is the strict reading: every beat-to-beat interval of a window of at least STEAL_WINDOW_S above
STEAL_WARN_PCT, so one noisy minute neither warns nor clears it. It measures the host the services run on (the brain);
the MT5 boxes' own steal is not read by anything on the brain yet (BACKLOG item `mt5-box-cpu-steal`). A host without
/proc/stat (the Mac, Windows) publishes no block and the check is omitted.

needrestart: /etc/needrestart/conf.d/goldbot.conf (installed by goldbot/ops/linux/brain_bootstrap.sh and
mt5_box_upgrade.sh) keeps needrestart, run by unattended-upgrades, from restarting a goldbot unit. Missing on a host
where needrestart is installed warns; a host without /etc/needrestart has nothing that restarts units, and a context
without `etc_dir` (tests, a non-Linux host) omits the check.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

from goldbot.ops.checks._base import HEARTBEAT_SERVICES, HEARTBEAT_SILENT_S, Check, _epoch, heartbeat_path
from goldbot.ops.checks.registry import BLOCK_TAIL, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext

PROC_STAT = Path("/proc/stat")
STEAL_WARN_PCT = 20.0             # SRE 2026-11-14: steal above this, sustained, starves the engines' loops
STEAL_WINDOW_S = 10 * 60          # ... for this long
NEEDRESTART_CONF = Path("needrestart") / "conf.d" / "goldbot.conf"     # under etc_dir


def read_cpu(path: Path) -> tuple[int, int] | None:
    """(steal, total) jiffies of the aggregate `cpu` line of /proc/stat; None when it cannot be read. Total counts
    user, nice, system, idle, iowait, irq, softirq and steal (guest time is already inside user and nice)."""
    try:
        with open(path, encoding="ascii") as f:
            line = f.readline()
    except OSError:
        return None
    parts = line.split()
    if len(parts) < 9 or parts[0] != "cpu":
        return None
    try:
        vals = [int(v) for v in parts[1:9]]
    except ValueError:
        return None
    return vals[7], sum(vals)


class StealSampler:
    """The CPU steal of the last `window_s`, from samples taken at each `sample(now)` (epoch seconds)."""

    def __init__(self, path: Path = PROC_STAT, window_s: float = STEAL_WINDOW_S) -> None:
        self.path, self.window_s = path, window_s
        self._ring: deque[tuple[float, int, int]] = deque()

    def sample(self, now: float) -> dict[str, float] | None:
        """Read /proc/stat and return {window_s, steal_pct, min_pct} over the kept samples (the oldest is the newest
        one at least `window_s` old, so `window_s` reaches the window once enough beats ran); None until two samples
        exist or when /proc/stat cannot be read."""
        got = read_cpu(self.path)
        if got is None:
            return None
        if self._ring and (now <= self._ring[-1][0] or got[1] < self._ring[-1][2]):
            self._ring.clear()                    # the clock went back or the counters reset: start again
        self._ring.append((now, *got))
        while len(self._ring) > 2 and self._ring[1][0] <= now - self.window_s:
            self._ring.popleft()
        pcts = [100.0 * (b[1] - a[1]) / (b[2] - a[2]) for a, b in zip(self._ring, list(self._ring)[1:]) if b[2] > a[2]]
        if not pcts:
            return None
        first, last = self._ring[0], self._ring[-1]
        return {"window_s": round(last[0] - first[0], 1),
                "steal_pct": round(100.0 * (last[1] - first[1]) / (last[2] - first[2]), 1),
                "min_pct": round(min(pcts), 1)}


def _freshest_steal(ctx: HealthContext) -> dict[str, Any] | None:
    """The `cpu_steal` block of the most recent fresh service heartbeat that carries one."""
    best: tuple[float, dict[str, Any]] | None = None
    now = _epoch(ctx.now)
    for service in HEARTBEAT_SERVICES:
        try:
            doc = json.loads(heartbeat_path(ctx.state_dir, service).read_text(encoding="utf-8"))
            ts, block = float(doc["ts"]), doc.get("cpu_steal")
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(block, dict) and 0 <= now - ts <= HEARTBEAT_SILENT_S and (best is None or ts > best[0]):
            best = (ts, block)
    return None if best is None else best[1]


@register_check("global", block=BLOCK_TAIL, pos=170)
def check_cpu_steal(ctx: HealthContext) -> Check | None:
    """WARN when the hypervisor's CPU steal on this host stayed above STEAL_WARN_PCT for every interval of the last
    STEAL_WINDOW_S (module docstring); omitted when no fresh heartbeat carries a sample. Warn-only: trading goes on."""
    block = _freshest_steal(ctx)
    if block is None:
        return None
    try:
        span, mean, low = float(block["window_s"]), float(block["steal_pct"]), float(block["min_pct"])
    except (KeyError, TypeError, ValueError):
        return Check(name="cpu_steal", status="warn", reason="the heartbeat's cpu_steal block is unreadable")
    msg = f"CPU steal {mean:.0f}% over the last {span / 60:.0f} min (lowest minute {low:.0f}%) on this host"
    if span >= STEAL_WINDOW_S and low > STEAL_WARN_PCT:
        return Check(name="cpu_steal", status="warn",
                     reason=f"{msg}: the cloud host is CPU-starved (> {STEAL_WARN_PCT:.0f}% for "
                            f"{STEAL_WINDOW_S // 60} min); engine loops slow down. Nothing is halted")
    if span < STEAL_WINDOW_S:
        return Check(name="cpu_steal", status="ok", reason=f"{msg} (window filling)")
    return Check(name="cpu_steal", status="ok", reason=msg)


@register_check("global", block=BLOCK_TAIL, pos=171)
def check_needrestart(ctx: HealthContext) -> Check | None:
    """WARN when needrestart is installed but its goldbot drop-in is missing (unattended-upgrades would restart goldbot
    units; outage 2026-11-14 06:58). Omitted without `etc_dir`; ok when needrestart is not installed."""
    if ctx.etc_dir is None:
        return None
    if not (ctx.etc_dir / "needrestart").is_dir():
        return Check(name="needrestart", status="ok", reason="needrestart not installed: nothing restarts units")
    if (ctx.etc_dir / NEEDRESTART_CONF).is_file():
        return Check(name="needrestart", status="ok", reason="goldbot units are excluded from needrestart")
    return Check(name="needrestart", status="warn",
                 reason=f"/etc/{NEEDRESTART_CONF} missing: unattended-upgrades may restart goldbot units (outage "
                        "2026-11-14); re-run the host bootstrap's needrestart step (RUNBOOK 'needrestart')")
