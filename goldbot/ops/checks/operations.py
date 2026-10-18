"""Ops and backups health checks: settings, accounts, disk, deploys, backups, retention, restore drill, agent spend."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pandas as pd

from goldbot.ops.checks._base import (
    BACKUP_FAIL_H,
    BACKUP_REPO_WARN_BYTES,
    BACKUP_SLOW_WARN_S,
    BACKUP_WARN_H,
    DISK_FAIL_FREE_GB,
    DISK_FAIL_FREE_PCT,
    DISK_WARN_FREE_GB,
    DISK_WARN_FREE_PCT,
    DRILL_WARN_DAYS,
    PRUNE_WARN_DAYS,
    SPEND_WARN_FRACTION,
    Check,
    Status,
    _age,
    _read_json,
    _skipped,
)
from goldbot.ops.checks.registry import BLOCK_STATIC, BLOCK_TAIL, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext


@register_check("static", block=BLOCK_STATIC, pos=10)
def check_settings(ctx: HealthContext) -> Check:
    if ctx.settings is None:
        return Check(name="settings", status="fail", reason=f"config/settings.yaml does not load: {ctx.settings_error}"[:300])
    tg = "" if ctx.settings.telegram.allowed_user_ids else "; telegram.allowed_user_ids is empty (no approvals, no alerts)"
    return Check(name="settings", status="warn" if tg else "ok", reason=f"loaded{tg}")


@register_check("static", block=BLOCK_STATIC, pos=20)
def check_accounts(ctx: HealthContext) -> Check:
    if ctx.accounts_error is not None:
        return Check(name="accounts", status="fail", reason=f"config/accounts.yaml does not load: {ctx.accounts_error}"[:300])
    if not ctx.accounts:
        return Check(name="accounts", status="warn", reason="no enabled accounts: no engine will trade")
    return Check(name="accounts", status="ok", reason="enabled: " + ", ".join(a.account_id for a in ctx.accounts))


@register_check("static", block=BLOCK_STATIC, pos=40)
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


@register_check("global", block=BLOCK_TAIL, pos=50)
def check_deploy(ctx: HealthContext) -> Check:
    """The last deploy attempt (state/deploys.jsonl, written by goldbot-deploy): a rollback warns, a failed rollback
    fails; a deploy is only ever started by the owner (Telegram [Deploy] or `sudo goldbot-deploy`). An owner's
    rollback (`goldbot-deploy --rollback`) warns too: an older version runs on purpose until the next deploy."""
    f = ctx.state_dir / "deploys.jsonl"
    if not f.exists():
        return Check(name="deploy", status="ok", reason="no deploy recorded yet")
    try:
        last = json.loads(f.read_text().splitlines()[-1])
    except (ValueError, OSError, IndexError) as exc:
        return Check(name="deploy", status="warn", reason=f"deploys.jsonl unreadable: {exc}")
    msg = f"{last.get('result')} {str(last.get('to', ''))[:8]} on {last.get('role')} at {last.get('ts')}: {last.get('detail', '')}"
    status = {"failed": "fail", "rolled_back": "warn", "refused": "warn", "rolled_back_manual": "warn",
              "rollback_reverted": "warn", "deployed_degraded": "warn"}.get(str(last.get("result")), "ok")
    return Check(name="deploy", status=cast(Status, status), reason=msg)


@register_check("global", block=BLOCK_TAIL, pos=60)
def check_backup_age(ctx: HealthContext) -> Check:
    """Age of the last successful encrypted off-host backup (state/backup_last.json, goldbot/ops/backup.py). Error
    texts in the record are masked by the backup module before they are written."""
    name = "backup_age"
    f = ctx.state_dir / "backup_last.json"
    if not f.exists():
        return Check(name=name, status="warn", reason="no backup recorded yet (RUNBOOK 'Backups': restic keys, "
                                                      "then `goldbot run backup --init`)")
    try:
        rec = _read_json(f)
        last_ok = pd.Timestamp(rec["last_ok_ts"]) if rec.get("last_ok_ts") else None
        attempt = pd.Timestamp(rec["ts"])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return Check(name=name, status="warn", reason=f"backup_last.json unreadable: {exc}"[:300])
    failed = "" if rec.get("ok") else f"; last attempt {attempt:%Y-%m-%d %H:%M} failed: {str(rec.get('error'))[:200]}"
    if last_ok is None:
        return Check(name=name, status="warn", reason="no successful backup yet" + failed)
    age_h = (ctx.now - last_ok).total_seconds() / 3600
    msg = f"last good backup {_age(age_h * 3600)} ago" + failed
    repo = rec.get("repo_bytes")
    if age_h > BACKUP_FAIL_H:
        return Check(name=name, status="fail", reason=msg + f" (> {BACKUP_FAIL_H:.0f} h)")
    if age_h > BACKUP_WARN_H:
        return Check(name=name, status="warn", reason=msg + f" (> {BACKUP_WARN_H:.0f} h)")
    if failed:
        return Check(name=name, status="warn", reason=msg)
    if isinstance(repo, (int, float)) and repo > BACKUP_REPO_WARN_BYTES:
        return Check(name=name, status="warn", reason=msg + f"; repository {repo / 1e9:.1f} GB of the 20 GB free tier")
    if rec.get("model_problems"):
        return Check(name=name, status="warn", reason=msg + "; " + "; ".join(rec["model_problems"])[:200])
    took = rec.get("duration_s")
    if isinstance(took, (int, float)) and took >= BACKUP_SLOW_WARN_S:
        return Check(name=name, status="warn", reason=msg + f"; it took {took / 60:.0f} min: restic cancels a backup "
                                                            "near 20 min under the no-delete key (ADR 0002)")
    return Check(name=name, status="ok", reason=msg)


@register_check("global", block=BLOCK_TAIL, pos=70)
def check_backup_prune(ctx: HealthContext) -> Check:
    """Retention runs only from the owner's Mac (the brain's key appends): the newest retention marker, as the brain's
    last backup read it (state/backup_last.json `last_prune_ts`), warns after 45 days. With no marker yet it warns
    only once the first good backup is 45 days old."""
    name = "backup_prune"
    f = ctx.state_dir / "backup_last.json"
    if not f.exists():
        return Check(name=name, status="ok", reason="no backup yet")
    try:
        rec = _read_json(f)
        pruned = pd.Timestamp(rec["last_prune_ts"]) if rec.get("last_prune_ts") else None
        first = pd.Timestamp(rec["first_ok_ts"]) if rec.get("first_ok_ts") else None
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return Check(name=name, status="warn", reason=f"backup_last.json unreadable: {exc}"[:300])
    how = "run scripts/backup_retention.sh on your Mac (RUNBOOK 0.6)"
    if pruned is None:
        if first is not None and (ctx.now - first).total_seconds() / 86400 > PRUNE_WARN_DAYS:
            return Check(name=name, status="warn", reason=f"no retention run recorded in {PRUNE_WARN_DAYS:.0f} days of "
                                                          f"backups: {how}")
        return Check(name=name, status="ok", reason=f"no retention run yet (monthly; {how})")
    age_d = (ctx.now - pruned).total_seconds() / 86400
    msg = f"last retention run {_age(age_d * 86400)} ago"
    if age_d > PRUNE_WARN_DAYS:
        return Check(name=name, status="warn", reason=msg + f" (> {PRUNE_WARN_DAYS:.0f} d): {how}")
    return Check(name=name, status="ok", reason=msg)


@register_check("global", block=BLOCK_TAIL, pos=75)
def check_object_storage(ctx: HealthContext) -> Check:
    """Size of the backup bucket, previous versions included (state/object_storage_size.json, goldbot/ops/storage_size.py,
    written after each backup): warn at 15 GB, fail at 19 GB of the 20 GB free tier (settings.backup). A missing,
    stale or unreadable reading is unknown (warn), never ok."""
    name = "object_storage"
    b = ctx.settings.backup if ctx.settings is not None else None
    warn_gb, fail_gb, max_h = (b.storage_warn_gb, b.storage_fail_gb, b.storage_max_age_h) if b else (15.0, 19.0, 48.0)
    how = "RUNBOOK 0.6 'Object Storage size'"
    f = ctx.state_dir / "object_storage_size.json"
    if not f.exists():
        if not (ctx.state_dir / "backup_last.json").exists():
            return Check(name=name, status="ok", reason="no backup yet, nothing to measure")
        return Check(name=name, status="warn", reason=f"size unknown: never read ({how})")
    try:
        rec = _read_json(f)
        ts = pd.Timestamp(rec["ts"])
        size = rec["bytes"]
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return Check(name=name, status="warn", reason=f"size unknown: object_storage_size.json unreadable: {exc}"[:300])
    if isinstance(size, bool) or not isinstance(size, (int, float)) or size < 0:
        why = str(rec.get("error") or "no number")[:80]
        return Check(name=name, status="warn", reason=f"size unknown: last read failed ({why}); {how}")
    age_h = (ctx.now - ts).total_seconds() / 3600
    gb = size / 1e9
    msg = f"{gb:.1f} GB with previous versions (read {_age(age_h * 3600)} ago; free tier 20 GB)"
    if age_h > max_h:
        return Check(name=name, status="warn", reason="size unknown: reading is stale; last " + msg)
    if gb >= fail_gb:
        return Check(name=name, status="fail", reason=msg + f" >= {fail_gb:g} GB: {how}")
    if gb >= warn_gb:
        return Check(name=name, status="warn", reason=msg + f" >= {warn_gb:g} GB: {how}")
    return Check(name=name, status="ok", reason=msg)


@register_check("global", block=BLOCK_TAIL, pos=80)
def check_restore_drill(ctx: HealthContext) -> Check:
    """The weekly restore drill (state/restore_drill_last.json): a failed drill fails, a stale one warns."""
    name = "restore_drill"
    f = ctx.state_dir / "restore_drill_last.json"
    if not f.exists():
        return Check(name=name, status="warn", reason="no restore drill yet (weekly, Sunday 10:00 UTC; "
                                                      "`goldbot run restore-drill` runs one now)")
    try:
        rec = _read_json(f)
        ts = pd.Timestamp(rec["ts"])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return Check(name=name, status="warn", reason=f"restore_drill_last.json unreadable: {exc}"[:300])
    if not rec.get("ok"):
        why = rec.get("error") or "; ".join(rec.get("problems") or [])
        return Check(name=name, status="fail", reason=f"drill at {ts:%Y-%m-%d %H:%M} failed: {str(why)[:300]}")
    age_d = (ctx.now - ts).total_seconds() / 86400
    msg = f"last drill {_age(age_d * 86400)} ago verified {rec.get('files', 0)} files"
    if age_d > DRILL_WARN_DAYS:
        return Check(name=name, status="warn", reason=msg + f" (> {DRILL_WARN_DAYS:.0f} d)")
    return Check(name=name, status="ok", reason=msg)


@register_check("global", block=BLOCK_TAIL, pos=100)
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
