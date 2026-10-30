"""Deterministic health checks over the state directory and settings (design: Deployment / observability).

Every check reads files the services already write and returns ok / warn / fail with a one-line reason; nothing here
talks to the network, a broker or Telegram, so the whole module is unit-tested with temporary state dirs.

  python -m goldbot.ops.run health                 # table, exit code 1 on any fail
  python -m goldbot.ops.run health --json          # machine-readable report
  python -m goldbot.ops.run health --static        # only checks that need no running service (used mid-update)
  python -m goldbot.ops.run health --out f.json    # also write the JSON report to a file (UTF-8)
  python -m goldbot.ops.run health --baseline f.json   # exit 1 only for fails that were not failing in f.json

Secrets are checked for PRESENCE only: the report names missing keys and never contains a value. The network
probes, the MT5 bridge's /health through the tunnel and the broker's latest tick for the server-clock check
(`goldbot/ops/server_clock.py`) and the bridge's reported protocol, methods, zone and commit
(`goldbot/ops/bridge_compat.py`), are injected (`HealthContext.bridge_probe`, `tick_reader`, `bridge_info`); tests
leave them unset.

Operational alerts (design S5, R11, X6) are checks too: a service heartbeat silent for 5 minutes fails, and a tripped
daily or weekly loss cap and an order that failed after the retries warn with `NOTIFY_ON_WARN`, so the Telegram alert
path tells the owner once per incident. Alerts only add information: nothing here changes orders, sizing or halts.
The design's stop rule (P6) is a check too: a breach fails `stop_rule`, which alerts; it recommends stopping and halts
nothing by itself. `phase_gates` shows whether the next roadmap gate is met (P7); it records nothing.

`HealthWatch` is the alert half: fed a report, it returns the Telegram text for checks that turned fail (and the ones
that recovered) since the last report, and persists the last statuses in state/health_last.json so a restart of the
Telegram service neither repeats nor drops an alert.

The checks themselves live in goldbot/ops/checks/, one module per area, each registered with `register_check`;
`run_checks` runs the registry in its fixed order (goldbot/ops/checks/registry.py). This module keeps the context,
the report, heartbeats, alerts and the CLI, and re-exports every check, threshold and helper it always exposed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from pydantic import Field

from goldbot.base import (
    FrozenRecord,
    Record,
    UtcTimestamp,
    load_record_json,
    schema_skew_summary,
    write_atomic,
)
from goldbot.config import DEFAULT_SETTINGS, Settings, settings_dict
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.data.crossfeed import check_reconciliation
from goldbot.data.ticks import check_tick_record
from goldbot.data.tv_ideas import check_tv_ideas
from goldbot.ops.checks._base import (
    _RANK,
    BACKUP_FAIL_H,
    BACKUP_REPO_WARN_BYTES,
    BACKUP_SLOW_WARN_S,
    BACKUP_WARN_H,
    BRIDGE_TIMEOUT_S,
    COSTS_FAIL_DAYS,
    COSTS_PUBLISHED_WARN_DAYS,
    COSTS_WARN_DAYS,
    DISK_FAIL_FREE_GB,
    DISK_FAIL_FREE_PCT,
    DISK_WARN_FREE_GB,
    DISK_WARN_FREE_PCT,
    DQ_WINDOW_HOURS,
    DRILL_WARN_DAYS,
    ENGINE_FAIL_S,
    ENGINE_WARN_S,
    FAILED_ORDER_WINDOW_H,
    HEARTBEAT_EVERY_S,
    HEARTBEAT_SERVICES,
    HEARTBEAT_SILENT_S,
    HWM_PENDING_WARN_S,
    JOB_OVERDUE_GRACE_S,
    JOB_RUNNING_FAIL_H,
    LOOP_FAIL_S,
    LOOP_STARTING_FAIL_S,
    LOOP_WARN_S,
    NEWS_STALE_POLLS,
    OPTIONAL_SECRETS,
    PRUNE_WARN_DAYS,
    QUOTE_INVALID_FAIL_S,
    REARM_APPLY_S,
    REQUIRED_SECRETS,
    SCHEDULER_HEARTBEAT_FAIL_S,
    SPEND_WARN_FRACTION,
    STUCK_APPROVAL_GRACE_S,
    SUPERVISOR_FAIL_S,
    SUPERVISOR_WARN_S,
    WATCH_FILE,
    WATCH_STALE_S,
    AccountRef,
    Check,
    Status,
    WatchState,
    _age,
    _epoch,
    _market_open_throughout,
    _read_json,
    _skipped,
    _worst,
    heartbeat_path,
    log,
)
from goldbot.ops.checks.auth import check_email_link, check_secrets
from goldbot.ops.checks.bridge import (
    TERMINAL_NOT_CONNECTED_FAIL_S,
    _age_s,
    _engine_bridge_unconfirmed,
    _engine_sessions,
    check_bridge,
    check_bridge_compat,
    check_pilot_server_clock,
    check_server_clock,
    check_terminal,
)
from goldbot.ops.checks.engine import (
    TICK_ERROR_RECENT_S,
    _failed_order_retcodes,
    check_engine,
    check_engine_loop,
    check_entry_status,
    check_failed_orders,
    check_tick_errors,
)
from goldbot.ops.checks.feeds import (
    PNL_BASIS_GROSS_WARN_PCT,
    PNL_BASIS_WINDOW_DAYS,
    check_costs,
    check_costs_published,
    check_data_quality,
    check_drift,
    check_exploration_learning,
    check_feature_versions,
    check_model_watch,
    check_news,
    check_pnl_basis,
)
from goldbot.ops.checks.operations import (
    check_accounts,
    check_agent_spend,
    check_backup_age,
    check_backup_prune,
    check_deploy,
    check_disk,
    check_object_storage,
    check_restore_drill,
    check_settings,
)
from goldbot.ops.checks.registry import blocks as check_blocks
from goldbot.ops.checks.registry import resolve
from goldbot.ops.checks.risk import (
    WINDOWS_FETCH_WARN,
    WINDOWS_HOLIDAY_WARN_DAYS,
    WINDOWS_UNAVAILABLE_FAIL,
    _control,
    check_entry_windows,
    check_halt,
    check_kill_switch,
    check_loss_caps,
    check_open_risk,
    check_phase,
    check_phase_gates,
    check_risk_state,
    check_stop_rule,
    check_supervisor,
    check_throttle,
)
from goldbot.ops.checks.services import (
    SKEW_CLICK_SOURCE,
    SKEW_REASON_CHARS,
    SKEW_REASON_SOURCES,
    SKEW_REFUSED_FAIL_S,
    SKEW_SERVICES,
    _running,
    _skew_sources,
    check_alert_loop,
    check_approvals,
    check_deadman,
    check_heartbeat,
    check_optional_heartbeat,
    check_scheduler,
    check_schema_skew,
    check_service_schema_skew,
)
from goldbot.ops.limits_job import check_limits_controller
from goldbot.ops.tv_pull_health import check_tv_pull

# the public surface: every name this module exposed before the checks moved to goldbot/ops/checks/ (callers and
# tests import and patch them here)
__all__ = [
    "AccountRef",
    "BACKUP_FAIL_H",
    "BACKUP_REPO_WARN_BYTES",
    "BACKUP_SLOW_WARN_S",
    "BACKUP_WARN_H",
    "BRIDGE_TIMEOUT_S",
    "TERMINAL_NOT_CONNECTED_FAIL_S",
    "COSTS_FAIL_DAYS",
    "COSTS_PUBLISHED_WARN_DAYS",
    "COSTS_WARN_DAYS",
    "Check",
    "DISK_FAIL_FREE_GB",
    "DISK_FAIL_FREE_PCT",
    "DISK_WARN_FREE_GB",
    "DISK_WARN_FREE_PCT",
    "DQ_WINDOW_HOURS",
    "DRILL_WARN_DAYS",
    "ENGINE_FAIL_S",
    "ENGINE_WARN_S",
    "FAILED_ORDER_WINDOW_H",
    "HEARTBEAT_EVERY_S",
    "HEARTBEAT_SERVICES",
    "HEARTBEAT_SILENT_S",
    "HealthContext",
    "HealthReport",
    "HealthWatch",
    "Heartbeat",
    "JOB_OVERDUE_GRACE_S",
    "JOB_RUNNING_FAIL_H",
    "LOOP_FAIL_S",
    "LOOP_STARTING_FAIL_S",
    "LOOP_WARN_S",
    "MAX_ALERT",
    "NEWS_STALE_POLLS",
    "NOTIFY_ON_WARN",
    "OPTIONAL_SECRETS",
    "PNL_BASIS_GROSS_WARN_PCT",
    "PNL_BASIS_WINDOW_DAYS",
    "PRUNE_WARN_DAYS",
    "QUOTE_INVALID_FAIL_S",
    "HWM_PENDING_WARN_S",
    "REARM_APPLY_S",
    "REQUIRED_SECRETS",
    "SCHEDULER_HEARTBEAT_FAIL_S",
    "SKEW_CLICK_SOURCE",
    "SKEW_REASON_CHARS",
    "SKEW_REASON_SOURCES",
    "SKEW_REFUSED_FAIL_S",
    "SKEW_SERVICES",
    "SPEND_WARN_FRACTION",
    "STUCK_APPROVAL_GRACE_S",
    "SUPERVISOR_FAIL_S",
    "SUPERVISOR_WARN_S",
    "Status",
    "TICK_ERROR_RECENT_S",
    "WATCH_FILE",
    "WATCH_STALE_S",
    "WINDOWS_FETCH_WARN",
    "WINDOWS_HOLIDAY_WARN_DAYS",
    "WINDOWS_UNAVAILABLE_FAIL",
    "WatchState",
    "_RANK",
    "_age",
    "_age_s",
    "_alerting",
    "_control",
    "_engine_bridge_unconfirmed",
    "_engine_sessions",
    "_epoch",
    "_failed_order_retcodes",
    "_guarded",
    "_last_run",
    "_load_report",
    "_market_open_throughout",
    "_note_skew_build_failure",
    "_read_json",
    "_running",
    "_skew_build_failures",
    "_skew_sources",
    "_skipped",
    "_worst",
    "alert_text",
    "bridge_tick_reader",
    "check_accounts",
    "check_agent_spend",
    "check_alert_loop",
    "check_approvals",
    "check_backup_age",
    "check_backup_prune",
    "check_object_storage",
    "check_bridge",
    "check_bridge_compat",
    "check_costs",
    "check_costs_published",
    "check_data_quality",
    "check_deadman",
    "check_deploy",
    "check_disk",
    "check_drift",
    "check_exploration_learning",
    "check_email_link",
    "check_engine",
    "check_engine_loop",
    "check_entry_status",
    "check_entry_windows",
    "check_failed_orders",
    "check_feature_versions",
    "check_halt",
    "check_heartbeat",
    "check_optional_heartbeat",
    "check_kill_switch",
    "check_limits_controller",
    "check_loss_caps",
    "check_model_watch",
    "check_news",
    "check_open_risk",
    "check_phase",
    "check_phase_gates",
    "check_pnl_basis",
    "check_reconciliation",
    "check_restore_drill",
    "check_risk_state",
    "check_scheduler",
    "check_schema_skew",
    "check_secrets",
    "check_server_clock",
    "check_pilot_server_clock",
    "check_terminal",
    "check_service_schema_skew",
    "check_settings",
    "check_stop_rule",
    "check_supervisor",
    "check_throttle",
    "check_tick_errors",
    "check_tick_record",
    "check_tv_ideas",
    "check_tv_pull",
    "exit_code",
    "heartbeat_path",
    "log",
    "main",
    "render",
    "run_checks",
    "start_heartbeat",
    "worsened",
]


class HealthReport(FrozenRecord):
    ts: UtcTimestamp
    status: Status
    checks: list[Check]

    def failing(self) -> set[str]:
        return {c.name for c in self.checks if c.status == "fail"}

    def counts(self) -> dict[str, int]:
        return {s: sum(c.status == s for c in self.checks) for s in ("ok", "warn", "fail")}


class HealthContext(Record):
    state_dir: Path
    now: UtcTimestamp
    settings: Settings | None = None
    settings_error: str | None = None
    accounts: list[AccountRef] = Field(default_factory=list)
    # real-money pilot accounts configured but not enabled yet: their server clock is measured before the unlock
    pilot_accounts: list[AccountRef] = Field(default_factory=list)
    accounts_error: str | None = None
    get_secret: Callable[[str], str | None]
    disk_usage: Callable[[str], Any] = shutil.disk_usage     # -> (total, used, free) in bytes
    sessions: SessionTable = DEFAULT_SESSIONS
    bridge_probe: Callable[[str], str | None] | None = None   # url -> None when /health answers, else why not
    tick_reader: Callable[[str, str], Any] | None = None      # (account_id, symbol) -> broker Tick (read-only); None: no bridge
    bridge_info: Callable[[str], Any] | None = None           # url -> bridge_compat.BridgeInfo from /health; None: not probed
    terminal_probe: Callable[[str], Any] | None = None        # url -> (terminal token, seconds in it); None: not probed
    commit: str | None = None                                 # this checkout's commit, compared with the bridge's
    clock: Callable[[], pd.Timestamp] | None = None           # host UTC read after a network probe; None: use `now`
    last_run: UtcTimestamp | None = None                      # the previous health run (health_last.json), if any
    memo: dict[str, Any] = Field(default_factory=dict)        # per-run reads shared by the per-account checks

    @classmethod
    def from_runtime(cls, state_dir: str | Path = "state", settings_path: str | Path = DEFAULT_SETTINGS,
                     now: pd.Timestamp | None = None) -> "HealthContext":
        """Context for the VPS: settings re-read from disk (not the cached copy, so a bad edit shows up), enabled
        accounts (live ones only after the phase gate) and the keyring."""
        from goldbot.ops import accounts
        settings, s_err, accs, a_err, pilots = None, None, [], None, []
        try:
            settings = Settings.model_validate(settings_dict(settings_path))
        except Exception as exc:
            s_err = f"{type(exc).__name__}: {exc}"
        try:
            from goldbot.ops.limits_job import adaptive_enabled
            accs = [AccountRef(account_id=a.account_id, server_tz=a.server_tz, symbol=a.symbol,
                               adaptive=settings is not None and adaptive_enabled(settings, a), live=a.mode != "demo")
                    for a in accounts.enabled_accounts()]
            on = {a.account_id for a in accs}
            pilots = [AccountRef(account_id=a.account_id, server_tz=a.server_tz, symbol=a.symbol)
                      for a in accounts.load_accounts(logins=False).values()
                      if a.pilot.enabled and a.account_id not in on]
        except Exception as exc:
            a_err = f"{type(exc).__name__}: {exc}"
        from goldbot.execution.bridge import probe_health, probe_terminal
        from goldbot.ops.bridge_compat import read_bridge_info, repo_commit
        return cls(state_dir=Path(state_dir), now=now if now is not None else pd.Timestamp.now("UTC"),
                   settings=settings, settings_error=s_err, accounts=accs, accounts_error=a_err, pilot_accounts=pilots,
                   get_secret=accounts.get_secret, bridge_probe=lambda url: probe_health(url, BRIDGE_TIMEOUT_S),
                   clock=lambda: pd.Timestamp.now("UTC"), last_run=_last_run(Path(state_dir)),
                   tick_reader=bridge_tick_reader, bridge_info=lambda url: read_bridge_info(url, BRIDGE_TIMEOUT_S),
                   terminal_probe=lambda url: probe_terminal(url, BRIDGE_TIMEOUT_S),
                   commit=repo_commit())


def bridge_tick_reader(account_id: str, symbol: str) -> Any:
    """The account's latest tick through its MT5 bridge (read-only `last_tick` RPC), or None without a bridge."""
    from goldbot.execution.bridge import RemoteBroker
    from goldbot.ops import accounts
    ep = accounts.bridge_endpoint(account_id)
    if ep is None:
        return None
    return RemoteBroker(ep[0], ep[1], name=f"health-clock-{account_id}", timeout_s=BRIDGE_TIMEOUT_S).last_tick(symbol)


def _last_run(state_dir: Path) -> pd.Timestamp | None:
    """When the previous health report was recorded (state/health_last.json), or None."""
    try:
        return load_record_json(WatchState, (state_dir / WATCH_FILE).read_text(encoding="utf-8"), source=WATCH_FILE).ts
    except (ValueError, OSError):
        return None


_skew_build_failures = 0           # schema_skew summary builds that raised in this process (SRE review)


def _note_skew_build_failure(service: str) -> None:
    """Called inside the except block: the traceback once per process, then only a count at debug level, so a
    persistent bug does not fill the log every minute (health already WARNs on the error block)."""
    global _skew_build_failures
    _skew_build_failures += 1
    if _skew_build_failures == 1:
        log.exception("heartbeat %s: schema_skew summary failed (published as an error block; later failures are "
                      "counted at debug level)", service)
    else:
        log.debug("heartbeat %s: schema_skew summary failures %d", service, _skew_build_failures)


class Heartbeat:
    """state/heartbeat_<service>.json, written at most every `every_s` (call `beat()` from a loop of any period).
    A write error is logged, never raised: a full disk must not stop the supervisor's loop. It carries this process's
    bounded `schema_skew` block (ADR 0010), which health checks for every service (`schema_skew:svc:<service>`);
    building the block never costs the beat: on an error the beat carries {"error": <type>} instead, which health
    WARNs as unreadable."""

    def __init__(self, state_dir: str | Path, service: str, every_s: float = HEARTBEAT_EVERY_S,
                 clock: Callable[[], float] = time.time):
        self.path = heartbeat_path(state_dir, service)
        self.service, self.every_s, self.clock = service, every_s, clock
        self._last: float | None = None

    def beat(self) -> bool:
        now = self.clock()
        if self._last is not None and now - self._last < self.every_s:
            return False
        doc: dict[str, Any] = {"service": self.service, "ts": now}
        try:
            doc["schema_skew"] = schema_skew_summary().model_dump(mode="json")
        except Exception as exc:             # noqa: BLE001 - liveness first; health WARNs "block unreadable"
            doc["schema_skew"] = {"error": type(exc).__name__}          # the type, never the message
            _note_skew_build_failure(self.service)
        try:
            write_atomic(self.path, json.dumps(doc), durable=False)
        except OSError:
            log.exception("heartbeat %s", self.service)
            return False
        self._last = now
        return True


def start_heartbeat(state_dir: str | Path, service: str, every_s: float = HEARTBEAT_EVERY_S) -> threading.Thread:
    """A daemon thread beating for a service whose loop lives elsewhere (uvicorn, the Telegram bot, a scheduler
    blocked in a long job): it proves the process is alive; the service's own checks judge its loop."""
    hb = Heartbeat(state_dir, service, every_s)

    def loop() -> None:  # pragma: no cover - runs for the life of the process
        while True:
            hb.beat()
            time.sleep(every_s)
    t = threading.Thread(target=loop, name=f"heartbeat-{service}", daemon=True)
    t.start()
    return t


# --------------------------------------------------------------------------------------------- report
def _guarded(name: str, fn: Callable[[], "Check | list[Check] | None"]) -> list[Check]:
    """One check; an exception (a type-corrupt state file, a bug) becomes a FAIL named after it instead of aborting the
    whole report (trading-safety review LOW-3)."""
    try:
        out = fn()
    except Exception as exc:                 # noqa: BLE001 - every check is independent
        log.exception("health check %s raised", name)
        return [Check(name=name, status="fail", reason=f"the check raised {type(exc).__name__}: its state file is "
                                                       "corrupt or the check has a bug (health log)")]
    return [] if out is None else out if isinstance(out, list) else [out]


def run_checks(ctx: HealthContext, *, static_only: bool = False) -> HealthReport:
    """All checks in a fixed order: the registry's (goldbot/ops/checks/registry.py). `static_only`: settings, accounts,
    secrets and disk (no service needs to run). Each check is guarded (`_guarded`): one that raises is a FAIL of its
    own, never a missing report."""
    g = _guarded
    checks: list[Check] = []
    for scope, specs in check_blocks(static_only=static_only):
        specs = [s for s in specs if s.when is None or s.when(ctx)]
        if scope == "account":
            for a in ctx.accounts:
                for s in specs:
                    checks += g(f"{s.guard}:{a.account_id}",
                                partial(resolve(s.fn), ctx, a if s.pass_account else a.account_id))
        elif scope == "service":
            for svc in specs[0].services if specs else ():
                for s in specs:
                    checks += g(f"{s.guard}:{svc}", partial(resolve(s.fn), ctx, svc))
        else:
            for s in specs:
                checks += g(s.guard, partial(resolve(s.fn), ctx))
    return HealthReport(ts=ctx.now, status=_worst([c.status for c in checks]), checks=checks)


def render(report: HealthReport) -> str:
    width = max((len(c.name) for c in report.checks), default=10)
    lines = [f"{c.status.upper():4}  {c.name:<{width}}  {c.reason}" for c in report.checks]
    n = report.counts()
    lines.append(f"overall: {report.status} ({n['fail']} fail, {n['warn']} warn, {n['ok']} ok) at {report.ts:%Y-%m-%d %H:%M:%S} UTC")
    return "\n".join(lines)


def exit_code(report: HealthReport, baseline: HealthReport | None = None) -> int:
    """1 when something fails; with a baseline, only fails that were not already failing in it count."""
    fails = report.failing() - (baseline.failing() if baseline is not None else set())
    return 1 if fails else 0


def worsened(report: HealthReport, baseline: HealthReport) -> list[str]:
    """Checks whose status is worse than in the baseline (ok < warn < fail; a check new since the baseline counts
    from ok; one that disappeared is not worse), sorted. goldbot-deploy embeds the same rule (WORSE_PY) so it also
    works against a release that predates this function."""
    before = {c.name: _RANK[c.status] for c in baseline.checks}
    return sorted(c.name for c in report.checks if _RANK[c.status] > before.get(c.name, 0))


# --------------------------------------------------------------------------------------------- alerts
MAX_ALERT = 3900                   # Telegram's limit is 4096 characters


# warnings the owner is told about (R11, X6; a missed backup night, so a broken backup is not first seen at 72 h)
NOTIFY_ON_WARN = ("daily_cap:", "weekly_cap:", "order_failed:", "backup_age", "backup_prune", "object_storage", "tick_errors:",
                  "throttle:", "entry_windows:", "open_risk:", "schema_skew:", "kill_switch:",
                  "heartbeat:webhook", "heartbeat:tv-pull")      # opt-in TradingView services: WARN, still told


def _alerting(name: str, status: str) -> bool:
    return status == "fail" or (status == "warn" and name.startswith(NOTIFY_ON_WARN))


def alert_text(prev: dict[str, Status], report: HealthReport) -> str | None:
    """The message for checks that turned fail (or a NOTIFY_ON_WARN warning) since `prev` and for alerted checks that
    recovered; None when nothing changed state. A check that stays failing is not repeated; one that flaps
    fail -> ok -> fail alerts again. A notice that simply ages out of the report (an old failed order) is not
    announced as recovered."""
    now = {c.name: c.status for c in report.checks}
    new = [c for c in report.checks if _alerting(c.name, c.status) and prev.get(c.name) != c.status
           and prev.get(c.name) != "fail"]
    recovered = sorted(n for n, s in prev.items() if _alerting(n, s) and not _alerting(n, now.get(n, "ok"))
                       and (s == "fail" or n in now))
    if not new and not recovered:
        return None
    lines = []
    if new:
        nf = sum(c.status == "fail" for c in new)
        heads = [f"{nf} new failure(s)"] if nf else []
        heads += [f"{len(new) - nf} new notice(s)"] if len(new) > nf else []
        lines.append("goldbot health: " + ", ".join(heads))
        lines += [f"{c.status.upper()} {c.name}: {c.reason}" for c in new]
    if recovered:
        lines.append("recovered: " + ", ".join(f"{n} ({now.get(n, 'gone')})" for n in recovered))
    still = sorted(report.failing() - {c.name for c in new})
    if still:
        lines.append("still failing: " + ", ".join(still))
    text = "\n".join(lines)
    return text if len(text) <= MAX_ALERT else text[:MAX_ALERT - 40].rstrip() + "\n... (python -m goldbot.ops.run health)"


class HealthWatch:
    """Persisted dedupe for health alerts (state/health_last.json). `poll()` is called by the Telegram service."""

    def __init__(self, state_dir: str | Path):
        self.path = Path(state_dir) / WATCH_FILE
        try:
            self.state = (load_record_json(WatchState, self.path.read_text(encoding="utf-8"), source=WATCH_FILE)
                          if self.path.exists() else WatchState())
        except (ValueError, OSError):
            self.state = WatchState()       # unreadable: start clean (current fails are announced once more)

    def alert(self, report: HealthReport) -> str | None:
        """The text to send for this report (None: nothing changed state). Does not record anything."""
        return alert_text(self.state.statuses, report)

    def record(self, report: HealthReport) -> None:
        """Remember this report's statuses; call after the alert was delivered so a failed send is retried."""
        self.state = WatchState(ts=report.ts, statuses={c.name: c.status for c in report.checks})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(self.state.model_dump_json(), encoding="utf-8")
        tmp.replace(self.path)

    def poll(self, report: HealthReport) -> str | None:
        text = self.alert(report)
        self.record(report)
        return text


# --------------------------------------------------------------------------------------------- CLI
def _load_report(path: str) -> HealthReport | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return load_record_json(HealthReport, p.read_text(encoding="utf-8"), source="health report")
    except ValueError:
        return None


def main(argv: list[str], ctx: HealthContext | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run health")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--static", action="store_true", help="only checks that need no running service")
    ap.add_argument("--out", help="also write the JSON report to this file (UTF-8)")
    ap.add_argument("--baseline", help="a previous --out report: exit 1 only for fails not failing there")
    ap.add_argument("--state-dir", default="state")
    args = ap.parse_args(argv)
    ctx = ctx or HealthContext.from_runtime(args.state_dir)
    report = run_checks(ctx, static_only=args.static)
    print(report.model_dump_json(indent=1) if args.json else render(report))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report.model_dump_json(indent=1), encoding="utf-8")
    baseline = _load_report(args.baseline) if args.baseline else None
    if args.baseline and baseline is not None and baseline.failing():
        print(f"baseline already failing (ignored for the exit code): {', '.join(sorted(baseline.failing()))}", file=sys.stderr)
    if baseline is not None and (worse := worsened(report, baseline)):
        print(f"worse than the baseline: {', '.join(worse)}", file=sys.stderr)
    return exit_code(report, baseline)
