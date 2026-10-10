"""Deterministic health checks over the state directory and settings (design: Deployment / observability).

Every check reads files the services already write and returns ok / warn / fail with a one-line reason; nothing here
talks to the network, a broker or Telegram, so the whole module is unit-tested with temporary state dirs.

  python -m goldbot.ops.run health                 # table, exit code 1 on any fail
  python -m goldbot.ops.run health --json          # machine-readable report
  python -m goldbot.ops.run health --static        # only checks that need no running service (used mid-update)
  python -m goldbot.ops.run health --out f.json    # also write the JSON report to a file (UTF-8)
  python -m goldbot.ops.run health --baseline f.json   # exit 1 only for fails that were not failing in f.json

Secrets are checked for PRESENCE only: the report names missing keys and never contains a value.

`HealthWatch` is the alert half: fed a report, it returns the Telegram text for checks that turned fail (and the ones
that recovered) since the last report, and persists the last statuses in state/health_last.json so a restart of the
Telegram service neither repeats nor drops an alert.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Literal, cast

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, Record, UtcTimestamp
from goldbot.config import DEFAULT_SETTINGS, Settings, settings_dict
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable

Status = Literal["ok", "warn", "fail"]
_RANK: dict[str, int] = {"ok": 0, "warn": 1, "fail": 2}

# ---- thresholds (seconds unless named otherwise); the reasons quote them so an alert explains itself
SUPERVISOR_WARN_S = 20
SUPERVISOR_FAIL_S = 60            # engines block entries when the supervisor heartbeat is older than this
ENGINE_WARN_S = 20 * 60           # engines write their state at every 15m bar close
ENGINE_FAIL_S = 45 * 60
SCHEDULER_HEARTBEAT_FAIL_S = 5 * 60   # run_forever saves every 30 s while idle
JOB_OVERDUE_GRACE_S = 15 * 60
JOB_RUNNING_FAIL_H = 12.0         # a job "running" this long means the scheduler died mid-job
COSTS_WARN_DAYS = 4.0             # nightly on weekdays: Friday -> Monday is 3 days
COSTS_FAIL_DAYS = 8.0
DQ_WINDOW_HOURS = 24              # data-quality error events within this window raise a warning
NEWS_STALE_POLLS = 3              # feed health older than this many poll intervals: the news service is down
SPEND_WARN_FRACTION = 0.8
STUCK_APPROVAL_GRACE_S = 120
DISK_FAIL_FREE_GB, DISK_FAIL_FREE_PCT = 2.0, 5.0
DISK_WARN_FREE_GB, DISK_WARN_FREE_PCT = 5.0, 10.0
REARM_APPLY_S = 10 * 60           # an engine applies a re-arm on its next tick
WATCH_STALE_S = 15 * 60           # health_last.json older than this: the Telegram alert loop is not running

REQUIRED_SECRETS = ("telegram-bot-token", "tradingview-webhook-secret")
OPTIONAL_SECRETS = ("anthropic-api-key", "github-token")    # features switch off without them (logged at start)


class Check(FrozenRecord):
    name: str
    status: Status
    reason: str


class HealthReport(FrozenRecord):
    ts: UtcTimestamp
    status: Status
    checks: list[Check]

    def failing(self) -> set[str]:
        return {c.name for c in self.checks if c.status == "fail"}

    def counts(self) -> dict[str, int]:
        return {s: sum(c.status == s for c in self.checks) for s in ("ok", "warn", "fail")}


class AccountRef(FrozenRecord):
    """The part of an account the checks need (keeps tests free of accounts.yaml)."""

    account_id: str


class HealthContext(Record):
    state_dir: Path
    now: UtcTimestamp
    settings: Settings | None = None
    settings_error: str | None = None
    accounts: list[AccountRef] = Field(default_factory=list)
    accounts_error: str | None = None
    get_secret: Callable[[str], str | None]
    disk_usage: Callable[[str], Any] = shutil.disk_usage     # -> (total, used, free) in bytes
    sessions: SessionTable = DEFAULT_SESSIONS

    @classmethod
    def from_runtime(cls, state_dir: str | Path = "state", settings_path: str | Path = DEFAULT_SETTINGS,
                     now: pd.Timestamp | None = None) -> "HealthContext":
        """Context for the VPS: settings re-read from disk (not the cached copy, so a bad edit shows up), enabled
        accounts (live ones only after the phase gate) and the keyring."""
        from goldbot.ops import accounts
        settings, s_err, accs, a_err = None, None, [], None
        try:
            settings = Settings.model_validate(settings_dict(settings_path))
        except Exception as exc:
            s_err = f"{type(exc).__name__}: {exc}"
        try:
            accs = [AccountRef(account_id=a.account_id) for a in accounts.enabled_accounts()]
        except Exception as exc:
            a_err = f"{type(exc).__name__}: {exc}"
        return cls(state_dir=Path(state_dir), now=now if now is not None else pd.Timestamp.now("UTC"),
                   settings=settings, settings_error=s_err, accounts=accs, accounts_error=a_err,
                   get_secret=accounts.get_secret)


# --------------------------------------------------------------------------------------------- helpers
def _worst(statuses: list[Status]) -> Status:
    return max(statuses, key=lambda s: _RANK[s]) if statuses else "ok"


def _age(seconds: float) -> str:
    s = max(seconds, 0.0)
    if s < 120:
        return f"{s:.0f} s"
    if s < 7200:
        return f"{s / 60:.0f} min"
    if s < 172800:
        return f"{s / 3600:.1f} h"
    return f"{s / 86400:.1f} d"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _epoch(now: pd.Timestamp) -> float:
    return now.timestamp()


def _market_open_throughout(ctx: HealthContext, seconds: float) -> bool:
    """True when the gold market was open at `now` and `seconds` ago (no weekend or daily break to explain silence).
    Checking both ends is enough for the windows used here (minutes; the daily break is an hour)."""
    idx = pd.DatetimeIndex([ctx.now, ctx.now - pd.Timedelta(seconds=seconds)])
    return bool(ctx.sessions.is_open(idx).all())


def _skipped(name: str, why: str) -> Check:
    return Check(name=name, status="warn", reason=f"skipped: {why}")


# --------------------------------------------------------------------------------------------- static checks
def check_settings(ctx: HealthContext) -> Check:
    if ctx.settings is None:
        return Check(name="settings", status="fail", reason=f"config/settings.yaml does not load: {ctx.settings_error}"[:300])
    tg = "" if ctx.settings.telegram.allowed_user_ids else "; telegram.allowed_user_ids is empty (no approvals, no alerts)"
    return Check(name="settings", status="warn" if tg else "ok", reason=f"loaded{tg}")


def check_accounts(ctx: HealthContext) -> Check:
    if ctx.accounts_error is not None:
        return Check(name="accounts", status="fail", reason=f"config/accounts.yaml does not load: {ctx.accounts_error}"[:300])
    if not ctx.accounts:
        return Check(name="accounts", status="warn", reason="no enabled accounts: no engine will trade")
    return Check(name="accounts", status="ok", reason="enabled: " + ", ".join(a.account_id for a in ctx.accounts))


def check_secrets(ctx: HealthContext) -> Check:
    """Presence only. Values are never returned, compared or logged."""
    def present(key: str) -> bool:
        try:
            return bool(ctx.get_secret(key))
        except Exception:
            return False
    required = list(REQUIRED_SECRETS) + [f"mt5-{a.account_id}" for a in ctx.accounts]
    missing = [k for k in required if not present(k)]
    optional = [k for k in OPTIONAL_SECRETS if not present(k)]
    hint = " (python -m goldbot.ops.accounts set <key>)"
    if missing:
        extra = f"; optional missing: {', '.join(optional)}" if optional else ""
        return Check(name="secrets", status="fail", reason=f"missing in keyring: {', '.join(missing)}{extra}{hint}")
    if optional:
        return Check(name="secrets", status="warn", reason=f"optional missing: {', '.join(optional)}{hint}")
    return Check(name="secrets", status="ok", reason=f"{len(required) + len(OPTIONAL_SECRETS)} keys present")


def check_disk(ctx: HealthContext) -> Check:
    root = Path(ctx.settings.data_root) if ctx.settings is not None else Path(".")
    probe = root
    while not probe.exists() and probe != probe.parent:      # data_root not created yet: measure its volume
        probe = probe.parent
    try:
        total, _used, free = ctx.disk_usage(str(probe))
    except OSError as exc:
        return Check(name="disk", status="fail", reason=f"cannot stat {root}: {exc}")
    gb, pct = free / 1e9, 100.0 * free / total if total else 0.0
    msg = f"{gb:.1f} GB free ({pct:.0f}%) on the volume of {root}"
    if gb < DISK_FAIL_FREE_GB or pct < DISK_FAIL_FREE_PCT:
        return Check(name="disk", status="fail", reason=msg)
    if gb < DISK_WARN_FREE_GB or pct < DISK_WARN_FREE_PCT:
        return Check(name="disk", status="warn", reason=msg)
    return Check(name="disk", status="ok", reason=msg)


# --------------------------------------------------------------------------------------------- service checks
def check_supervisor(ctx: HealthContext) -> Check:
    f = ctx.state_dir / "supervisor.json"
    if not f.exists():
        return Check(name="supervisor", status="fail", reason="no supervisor.json: supervisor not running, engines block entries")
    try:
        s = _read_json(f)
    except (ValueError, OSError) as exc:
        return Check(name="supervisor", status="fail", reason=f"supervisor.json unreadable: {exc}")
    age = _epoch(ctx.now) - float(s.get("ts", 0))
    if age > SUPERVISOR_FAIL_S:
        return Check(name="supervisor", status="fail",
                     reason=f"heartbeat {_age(age)} old (> {SUPERVISOR_FAIL_S} s): engines block entries")
    if s.get("halt"):
        return Check(name="supervisor", status="warn",
                     reason=f"combined-cap HALT: {', '.join(s.get('reasons') or [])}; heartbeat {_age(age)}")
    if age > SUPERVISOR_WARN_S:
        return Check(name="supervisor", status="warn", reason=f"heartbeat {_age(age)} old")
    note = ", size-down" if s.get("size_down") else ""
    return Check(name="supervisor", status="ok", reason=f"heartbeat {_age(age)}{note}")


def check_engine(ctx: HealthContext, account_id: str) -> Check:
    name = f"engine:{account_id}"
    f = ctx.state_dir / f"engine_{account_id}.json"
    market = _market_open_throughout(ctx, ENGINE_FAIL_S)
    if not f.exists():
        return Check(name=name, status="fail" if market else "warn", reason=f"no engine_{account_id}.json (engine never ran)")
    try:
        e = _read_json(f)
    except (ValueError, OSError) as exc:
        return Check(name=name, status="fail", reason=f"engine state unreadable: {exc}")
    parts: list[str] = []
    statuses: list[Status] = []
    age = _epoch(ctx.now) - float(e.get("ts", 0))
    if not market:
        parts.append(f"market closed, state {_age(age)} old")
    elif age > ENGINE_FAIL_S:
        statuses.append("fail")
        parts.append(f"state {_age(age)} old (> {ENGINE_FAIL_S // 60} min while the market is open)")
    elif age > ENGINE_WARN_S:
        statuses.append("warn")
        parts.append(f"state {_age(age)} old")
    else:
        parts.append(f"state {_age(age)} old")
    stage = str(e.get("stage", "unknown"))
    if stage == "halted":
        statuses.append("fail")
        parts.append("stage HALTED (drawdown stage 2): no entries until re-armed")
        if e.get("rearm_refused"):
            parts.append(f"last re-arm refused: {e['rearm_refused']}")
    elif stage == "size_down":
        statuses.append("warn")
        parts.append("stage size_down")
    else:
        parts.append(f"stage {stage}")
    stale_tick = ctx.settings.risk.stale_tick_seconds if ctx.settings is not None else 20
    tick_age = float(e.get("last_tick_age_s") or 0.0)
    if market and tick_age > stale_tick:
        statuses.append("warn")
        parts.append(f"last tick {_age(tick_age)} old (> {stale_tick} s)")
    if market and e.get("stale_bars"):
        statuses.append("warn")
        parts.append("last bar older than one decision period: entries blocked")
    if e.get("dq_error"):
        statuses.append("fail")
        checks = ", ".join(e.get("dq_checks") or []) or "stale feed"
        parts.append(f"data-quality error ({checks}): entries blocked")
    cls = str(e.get("account_class") or "unknown")
    if e.get("mode", "paper") != "paper":
        if cls == "unknown":
            statuses.append("warn")
            parts.append("account class unknown: entries blocked until the Friday classifier reads raw or standard")
        elif cls == "standard":
            parts.append("account class standard: 15m families off except session_open, edge must exceed 1.5x cost")
    b = e.get("blackout")
    if isinstance(b, dict) and b:
        parts.append(f"news blackout: {b.get('title', '?')}")
    return Check(name=name, status=_worst(statuses), reason="; ".join(parts))


def _control(ctx: HealthContext) -> dict[str, Any] | None:
    """control.json as a dict ({} when absent); None when unreadable."""
    f = ctx.state_dir / "control.json"
    if not f.exists():
        return {}
    try:
        c = _read_json(f)
        return c if isinstance(c, dict) else None
    except (ValueError, OSError):
        return None


def check_halt(ctx: HealthContext) -> Check:
    c = _control(ctx)
    if c is None:
        return Check(name="owner_halt", status="fail", reason="control.json unreadable: engines treat it as halted")
    rearm = ""
    if c.get("rearm_id"):
        at = pd.Timestamp(float(c.get("rearm_ts") or 0), unit="s", tz="UTC")
        rearm = f"; last owner re-arm by {c.get('rearm_by')} at {at:%Y-%m-%d %H:%M} UTC"
    if c.get("halted"):
        since = pd.Timestamp(float(c.get("ts") or 0), unit="s", tz="UTC")
        why = f" ({c['reason']})" if c.get("reason") else ""
        return Check(name="owner_halt", status="warn",
                     reason=f"HALTED by {c.get('by')}{why} since {since:%Y-%m-%d %H:%M} UTC; re-arm on the dashboard{rearm}")
    return Check(name="owner_halt", status="ok", reason="armed" + rearm)


def check_risk_state(ctx: HealthContext, account_id: str) -> Check:
    """state/risk_<account>.json: the engine's persisted risk periods, drawdown stage and the last re-arm it applied.
    It is read even while the engine is down, so a persisted drawdown halt is visible before a restart."""
    name = f"risk:{account_id}"
    f = ctx.state_dir / f"risk_{account_id}.json"
    if not f.exists():
        return Check(name=name, status="warn", reason=f"no risk_{account_id}.json yet (engine never ran)")
    try:
        r = _read_json(f)
        if not isinstance(r, dict):
            raise ValueError("not an object")
    except (ValueError, OSError) as exc:
        return Check(name=name, status="fail", reason=f"risk state unreadable (the engine refuses to start): {exc}")
    parts: list[str] = []
    statuses: list[Status] = []
    stage = str(r.get("stage", "normal"))
    if stage == "halted":
        statuses.append("fail")
        parts.append("drawdown HALT persisted: owner re-arm on the dashboard needed")
    else:
        parts.append(f"stage {stage}")
    last = r.get("last_reset")
    if last:
        days = (ctx.now - pd.Timestamp(last)).total_seconds() / 86400
        if days > COSTS_WARN_DAYS and _market_open_throughout(ctx, ENGINE_FAIL_S):
            statuses.append("warn")
            parts.append(f"loss-cap day last rolled {days:.1f} d ago")
    c = _control(ctx) or {}
    rid = c.get("rearm_id")
    if rid and r.get("rearm_seen") != rid and _epoch(ctx.now) - float(c.get("rearm_ts") or 0) > REARM_APPLY_S:
        statuses.append("warn")
        parts.append("the last owner re-arm has not been applied by this engine (engine down?)")
    return Check(name=name, status=_worst(statuses), reason="; ".join(parts))


def check_phase(ctx: HealthContext) -> Check:
    """state/phase_state.json (written by `run.py record-gate`): live accounts unlock only after paper_to_tiny_live."""
    from goldbot.ops.accounts import GATES
    f = ctx.state_dir / "phase_state.json"
    if not f.exists():
        return Check(name="phase", status="ok", reason="phase 0, no gate recorded (demo only)")
    try:
        st = _read_json(f)
        passed = list(st.get("gates_passed", []))
        phase = int(st.get("phase", 0))
    except (ValueError, OSError, AttributeError, TypeError) as exc:
        return Check(name="phase", status="fail", reason=f"phase_state.json unreadable: {exc}")
    if passed != list(GATES[:len(passed)]):
        return Check(name="phase", status="fail",
                     reason=f"gates out of order or unknown: {passed} (record with `python -m goldbot.ops.run record-gate`)")
    return Check(name="phase", status="ok", reason=f"phase {phase}, gates passed: {', '.join(passed) or 'none'}")


def _running(job: dict[str, Any]) -> pd.Timestamp | None:
    started, finished = job.get("last_started"), job.get("last_finished")
    if not started:
        return None
    s = pd.Timestamp(started)
    if finished is None or s > pd.Timestamp(finished):
        return s
    return None


def check_scheduler(ctx: HealthContext) -> list[Check]:
    """One heartbeat check plus one check per configured job."""
    f = ctx.state_dir / "scheduler.json"
    if not f.exists():
        return [Check(name="scheduler", status="fail", reason="no scheduler.json: scheduler never started")]
    try:
        raw = _read_json(f)
        hb = pd.Timestamp(raw["ts"])
        jobs: dict[str, dict[str, Any]] = raw.get("jobs", {})
    except (ValueError, OSError, KeyError) as exc:
        return [Check(name="scheduler", status="fail", reason=f"scheduler.json unreadable: {exc}")]
    running = {n: s for n, j in jobs.items() if (s := _running(j)) is not None}
    hb_age = (ctx.now - hb).total_seconds()
    if hb_age <= SCHEDULER_HEARTBEAT_FAIL_S:
        out = [Check(name="scheduler", status="ok", reason=f"heartbeat {_age(hb_age)}")]
    elif running:      # a job blocks the loop (a retrain takes hours); the job check judges how long
        out = [Check(name="scheduler", status="ok", reason=f"busy with {', '.join(sorted(running))}; heartbeat {_age(hb_age)}")]
    else:
        out = [Check(name="scheduler", status="fail", reason=f"heartbeat {_age(hb_age)} old: scheduler not running")]
    expected = list(type(ctx.settings.scheduler).model_fields) if ctx.settings is not None else []
    for name in sorted(set(expected) | set(jobs)):
        cname = f"job:{name}"
        j = jobs.get(name)
        if j is None:
            out.append(Check(name=cname, status="warn", reason="configured but not registered yet (scheduler not restarted?)"))
            continue
        statuses: list[Status] = []
        parts: list[str] = []
        if name in running:
            ran_h = (ctx.now - running[name]).total_seconds() / 3600
            if ran_h > JOB_RUNNING_FAIL_H:
                statuses.append("fail")
                parts.append(f"started {ran_h:.1f} h ago and never finished (scheduler died mid-job?)")
            else:
                parts.append(f"running for {ran_h:.1f} h")
        if j.get("last_ok") is False:
            statuses.append("fail")
            err = str(j.get("last_error") or "").strip().splitlines()
            parts.append(f"last run failed ({j.get('failures', 0)} failures): {err[0][:160] if err else '?'}")
        nxt = j.get("next_slot")
        if nxt and name not in running:
            late = (ctx.now - pd.Timestamp(nxt)).total_seconds()
            if late > JOB_OVERDUE_GRACE_S:
                statuses.append("fail")
                parts.append(f"overdue: slot {pd.Timestamp(nxt):%Y-%m-%d %H:%M} passed {_age(late)} ago")
        skipped = j.get("last_skipped")
        if skipped and j.get("last_slot") and pd.Timestamp(skipped) >= pd.Timestamp(j["last_slot"]) \
                and (j.get("last_started") is None or pd.Timestamp(skipped) > pd.Timestamp(j["last_started"])):
            statuses.append("warn")
            parts.append(f"slot {pd.Timestamp(j['last_slot']):%Y-%m-%d %H:%M} skipped (missed by more than max_late)")
        if not parts:
            last = j.get("last_finished")
            parts.append(f"ok, last ran {pd.Timestamp(last):%Y-%m-%d %H:%M}" if last else "waiting for its first slot")
            if nxt:
                parts.append(f"next {pd.Timestamp(nxt):%Y-%m-%d %H:%M}")
        out.append(Check(name=cname, status=_worst(statuses), reason="; ".join(parts)))
    return out


def check_costs(ctx: HealthContext, account_id: str) -> Check:
    name = f"costs:{account_id}"
    f = ctx.state_dir / f"costs_{account_id}.json"
    if not f.exists():
        return Check(name=name, status="warn", reason="no cost table yet (nightly_costs has not run): engine uses priors")
    try:
        raw = _read_json(f)
        built = pd.Timestamp(raw["built_utc"])
        built = built.tz_localize("UTC") if built.tzinfo is None else built
    except (ValueError, OSError, KeyError) as exc:
        return Check(name=name, status="fail", reason=f"cost table unreadable: {exc}")
    days = (ctx.now - built).total_seconds() / 86400
    msg = f"built {days:.1f} d ago"
    if days > COSTS_FAIL_DAYS:
        return Check(name=name, status="fail", reason=msg + f" (> {COSTS_FAIL_DAYS:.0f} d)")
    if days > COSTS_WARN_DAYS:
        return Check(name=name, status="warn", reason=msg)
    if raw.get("swap_long_usd_per_lot") is None or raw.get("swap_short_usd_per_lot") is None:
        # research and retraining then charge the settings' swap prior (see state/broker_terms_<account>.json)
        return Check(name=name, status="warn", reason=msg + "; no measured swap from the terminal: swap prior in use")
    return Check(name=name, status="ok", reason=msg)


def check_data_quality(ctx: HealthContext) -> Check:
    """Design (Data quality): errors quarantine the batch and alert. Error-severity events (duplicate stamps, bid >
    ask, out-of-order bars) recorded by the loaders or the engines in the last DQ_WINDOW_HOURS warn here; their rows
    are in `bars_quarantine`, not in the bar tables, and an engine blocks entries while its feed is in error."""
    if ctx.settings is None:
        return Check(name="data_quality", status="warn", reason="settings unreadable: data root unknown")
    root = Path(ctx.settings.data_root)
    if not (root / "dq_events").exists():
        return Check(name="data_quality", status="ok", reason="no data-quality events recorded")
    from goldbot.data.store import Store
    try:
        ev = Store(root).read("dq_events", start=ctx.now - pd.Timedelta(hours=DQ_WINDOW_HOURS), end=ctx.now)
    except (ValueError, OSError) as exc:
        return Check(name="data_quality", status="warn", reason=f"dq_events unreadable: {exc}")
    errs = ev[ev["severity"] == "error"] if not ev.empty and "severity" in ev.columns else ev.iloc[0:0]
    if errs.empty:
        return Check(name="data_quality", status="ok", reason=f"no error events in {DQ_WINDOW_HOURS} h ({len(ev)} warnings)")
    kinds = ", ".join(f"{k} x{n}" for k, n in errs["check"].value_counts().items())
    return Check(name="data_quality", status="warn",
                 reason=f"{len(errs)} error events in {DQ_WINDOW_HOURS} h ({kinds}): rows quarantined, see bars_quarantine")


def check_drift(ctx: HealthContext) -> Check:
    """state/drift.json (daily drift_watch): system halt fails, agent halts and size-downs warn, a stale file warns."""
    f = ctx.state_dir / "drift.json"
    if not f.exists():
        return Check(name="drift", status="ok", reason="no drift report yet (no champion, or drift_watch not run)")
    try:
        d = _read_json(f)
    except (ValueError, OSError) as exc:
        return Check(name="drift", status="fail", reason=f"drift.json unreadable (entries halted): {exc}")
    if d.get("system_halt"):
        why = "; ".join(d["system_halt"].get("reasons") or [])
        return Check(name="drift", status="fail",
                     reason=f"SYSTEM HALT pending review ({why}): python -m goldbot.ops.run drift-review")
    parts, status = [], "ok"
    if d.get("halted"):
        status = "warn"
        parts.append(f"halted agents: {', '.join(sorted(d['halted']))}")
    if d.get("size_factor"):
        status = "warn"
        parts.append(f"sized down: {', '.join(sorted(d['size_factor']))}")
    age_h = (ctx.now - pd.Timestamp(d.get("ts"))).total_seconds() / 3600 if d.get("ts") else float("inf")
    if age_h > 72:
        status = "warn"
        parts.append(f"report {age_h:.0f} h old")
    return Check(name="drift", status=cast(Status, status), reason="; ".join(parts) or "no drift")


def check_deploy(ctx: HealthContext) -> Check:
    """The last deploy attempt (state/deploys.jsonl, written by goldbot-deploy): a rollback warns, a failed rollback
    fails; a deploy is only ever started by the owner (Telegram [Deploy] or `sudo goldbot-deploy`)."""
    f = ctx.state_dir / "deploys.jsonl"
    if not f.exists():
        return Check(name="deploy", status="ok", reason="no deploy recorded yet")
    try:
        last = json.loads(f.read_text().splitlines()[-1])
    except (ValueError, OSError, IndexError) as exc:
        return Check(name="deploy", status="warn", reason=f"deploys.jsonl unreadable: {exc}")
    msg = f"{last.get('result')} {str(last.get('to', ''))[:8]} on {last.get('role')} at {last.get('ts')}: {last.get('detail', '')}"
    status = {"failed": "fail", "rolled_back": "warn", "refused": "warn"}.get(str(last.get("result")), "ok")
    return Check(name="deploy", status=cast(Status, status), reason=msg)


def check_news(ctx: HealthContext) -> Check:
    if ctx.settings is not None and not ctx.settings.news.feeds:
        return Check(name="news", status="ok", reason="no feeds configured")
    f = ctx.state_dir / "news_feeds.json"
    if not f.exists():
        return Check(name="news", status="warn", reason="no news_feeds.json yet: news service not started")
    try:
        feeds: dict[str, dict[str, Any]] = _read_json(f)
    except (ValueError, OSError) as exc:
        return Check(name="news", status="fail", reason=f"news_feeds.json unreadable: {exc}")
    if not feeds:
        return Check(name="news", status="warn", reason="news_feeds.json lists no feeds")
    poll = ctx.settings.news.poll_seconds if ctx.settings is not None else 300
    last = max(pd.Timestamp(h["ts"]) for h in feeds.values() if h.get("ts")) if any(h.get("ts") for h in feeds.values()) else None
    age = (ctx.now - last).total_seconds() if last is not None else float("inf")
    bad = sorted(n for n, h in feeds.items() if not h.get("ok"))
    if age > NEWS_STALE_POLLS * poll:
        return Check(name="news", status="fail", reason=f"last poll {_age(age)} ago (> {NEWS_STALE_POLLS} polls): news service down")
    if len(bad) == len(feeds):
        return Check(name="news", status="fail", reason=f"all {len(feeds)} feeds failing: no shock blackout possible")
    if bad:
        errs = "; ".join(f"{n}: {str(feeds[n].get('error', '?'))[:80]}" for n in bad)
        return Check(name="news", status="warn", reason=f"{len(bad)}/{len(feeds)} feeds failing ({errs})")
    return Check(name="news", status="ok", reason=f"{len(feeds)} feeds ok, last poll {_age(age)} ago")


def check_agent_spend(ctx: HealthContext) -> Check:
    cap = ctx.settings.agents.monthly_cap_usd if ctx.settings is not None else None
    if cap is None:
        return _skipped("agent_spend", "settings did not load")
    if cap <= 0:
        return Check(name="agent_spend", status="ok", reason="monthly cap is 0: staff agents and headline scoring off")
    f = ctx.state_dir / "agent_spend.json"
    try:
        spent = float(_read_json(f).get(f"{ctx.now:%Y-%m}", 0.0)) if f.exists() else 0.0
    except (ValueError, OSError, AttributeError) as exc:
        return Check(name="agent_spend", status="fail", reason=f"agent_spend.json unreadable: {exc}")
    frac = spent / cap
    msg = f"{ctx.now:%Y-%m}: ${spent:.2f} of ${cap:.2f} ({100 * frac:.0f}%)"
    if frac >= 1.0:
        return Check(name="agent_spend", status="fail", reason=msg + ": cap reached, agents and headline scoring stopped")
    if frac >= SPEND_WARN_FRACTION:
        return Check(name="agent_spend", status="warn", reason=msg)
    return Check(name="agent_spend", status="ok", reason=msg)


def check_approvals(ctx: HealthContext) -> Check:
    """Pending proposals past their window that no engine archived. Warn, not fail: the bus refuses decisions on them
    (nothing can be entered from them) and a dead engine already fails its own check; they mostly mean an engine
    restarted with a proposal open."""
    d = ctx.state_dir / "approvals" / "pending"
    now = _epoch(ctx.now)
    stuck: list[tuple[float, str]] = []
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            p = _read_json(f)
            over = now - float(p["created"]) - float(p.get("window_s", 90))
        except (ValueError, OSError, KeyError, TypeError):
            stuck.append((float("inf"), f.stem))
            continue
        if over > STUCK_APPROVAL_GRACE_S and p.get("outcome") is None:
            stuck.append((over, f.stem))
    if not stuck:
        return Check(name="approvals", status="ok", reason="no proposal stuck past its window")
    oldest = max(stuck)
    return Check(name="approvals", status="warn",
                 reason=f"{len(stuck)} pending proposal(s) past their window, oldest {oldest[1]} "
                        f"({_age(oldest[0]) if oldest[0] != float('inf') else 'unreadable'}); the engine never archived them")


def check_alert_loop(ctx: HealthContext) -> Check:
    f = ctx.state_dir / WATCH_FILE
    if not f.exists():
        return Check(name="alerts", status="warn", reason="health alerts never ran (Telegram service not started)")
    try:
        ts = WatchState.model_validate_json(f.read_text(encoding="utf-8")).ts
    except (ValueError, OSError) as exc:
        return Check(name="alerts", status="warn", reason=f"health_last.json unreadable: {exc}")
    age = (ctx.now - ts).total_seconds() if ts is not None else float("inf")
    if age > WATCH_STALE_S:
        return Check(name="alerts", status="warn", reason=f"last health alert pass {_age(age)} ago: Telegram service down?")
    return Check(name="alerts", status="ok", reason=f"last pass {_age(age)} ago")


# --------------------------------------------------------------------------------------------- report
def run_checks(ctx: HealthContext, *, static_only: bool = False) -> HealthReport:
    """All checks in a fixed order. `static_only`: settings, accounts, secrets and disk (no service needs to run)."""
    checks = [check_settings(ctx), check_accounts(ctx), check_secrets(ctx), check_disk(ctx)]
    if not static_only:
        checks.append(check_supervisor(ctx))
        checks.append(check_halt(ctx))
        checks.append(check_phase(ctx))
        for a in ctx.accounts:
            checks += [check_engine(ctx, a.account_id), check_risk_state(ctx, a.account_id)]
        checks += check_scheduler(ctx)
        checks += [check_costs(ctx, a.account_id) for a in ctx.accounts]
        checks += [check_data_quality(ctx), check_drift(ctx), check_deploy(ctx), check_news(ctx), check_agent_spend(ctx), check_approvals(ctx), check_alert_loop(ctx)]
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


# --------------------------------------------------------------------------------------------- alerts
WATCH_FILE = "health_last.json"
MAX_ALERT = 3900                   # Telegram's limit is 4096 characters


class WatchState(Record):
    ts: UtcTimestamp | None = None
    statuses: dict[str, Status] = Field(default_factory=dict)


def alert_text(prev: dict[str, Status], report: HealthReport) -> str | None:
    """The message for checks that turned fail since `prev` and for failing checks that recovered; None when nothing
    changed state. A check that stays failing is not repeated; one that flaps fail -> ok -> fail alerts again."""
    new = [c for c in report.checks if c.status == "fail" and prev.get(c.name) != "fail"]
    now = {c.name: c.status for c in report.checks}
    recovered = sorted(n for n, s in prev.items() if s == "fail" and now.get(n) != "fail")
    if not new and not recovered:
        return None
    lines = []
    if new:
        lines.append(f"goldbot health: {len(new)} new failure(s)")
        lines += [f"FAIL {c.name}: {c.reason}" for c in new]
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
            self.state = WatchState.model_validate_json(self.path.read_text(encoding="utf-8")) if self.path.exists() else WatchState()
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
        return HealthReport.model_validate_json(p.read_text(encoding="utf-8"))
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
    return exit_code(report, baseline)
