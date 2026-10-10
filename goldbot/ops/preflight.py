"""`goldbot run preflight`: a read-only checklist for the brain, run BEFORE starting the services.

Each item prints a tick or a cross and, when it is not OK, the exact command that fixes it. It changes nothing: it
reads settings, the secret store (presence only; no value is ever printed or compared except the terminal's account,
which is checked by `accounts.verify_terminal_account` and never echoed), file modes, `systemctl`/`timedatectl`
state, and asks the MT5 bridge for /health and the terminal's account through the tunnel.

Exit code 1 when any blocking item fails; warnings (backups not configured yet) do not block.

  goldbot run preflight [--account icm-demo]

Reuses goldbot.ops.health for the settings load, the bridge probe and disk headroom.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Literal

import pandas as pd

from goldbot.base import FrozenRecord, Record
from goldbot.config import DEFAULT_SETTINGS, ROOT, Settings, settings_dict
from goldbot.ops import health
from goldbot.ops.setup_wizard import RESTIC_KEYS

Status = Literal["ok", "warn", "fail"]
MARK = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
MEM_FAIL_GB, MEM_WARN_GB = 0.5, 1.5        # MemAvailable before any service runs (24 GB brain; services need ~2 GB)
SETUP = "goldbot run setup"
TUNNEL_UNIT = "goldbot-bridge-tunnel"
DEPLOY_TIMER = "goldbot-deploy.timer"


class Item(FrozenRecord):
    name: str
    status: Status
    detail: str
    fix: str                     # the exact command or step that makes it pass (shown when not ok)
    blocking: bool = True        # a blocking fail makes the exit code 1


def run_cmd(argv: list[str]) -> tuple[int, str]:
    """(exit code, stdout) of a read-only system command; 127 when it is not installed."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return p.returncode, p.stdout.strip()


def read_meminfo() -> dict[str, int]:
    """/proc/meminfo in kB; empty when unreadable (not Linux)."""
    out: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            out[k.strip()] = int(v.split()[0])
    except (OSError, ValueError, IndexError):
        return {}
    return out


def _terminal_account(url: str, token: str) -> Any:
    from goldbot.execution.bridge import RemoteBroker
    return RemoteBroker(url, token, name="preflight").account()     # read-only RPC; never an order call


def _load_accounts() -> dict[str, Any]:
    from goldbot.ops import accounts
    return dict(accounts.load_accounts())


class PreflightContext(Record):
    root: Path = ROOT
    state_dir: Path = ROOT / "state"
    settings_path: Path = DEFAULT_SETTINGS
    account_id: str = "icm-demo"
    get_secret: Callable[[str], str | None]
    run_cmd: Callable[[list[str]], tuple[int, str]] = run_cmd
    bridge_probe: Callable[[str], str | None] | None = None        # url -> None when /health answers
    terminal_account: Callable[[str, str], Any] = _terminal_account
    load_accounts: Callable[[], dict[str, Any]] = _load_accounts
    disk_usage: Callable[[str], Any] = shutil.disk_usage
    meminfo: Callable[[], dict[str, int]] = read_meminfo

    @classmethod
    def from_runtime(cls, account_id: str = "icm-demo") -> "PreflightContext":
        from goldbot.execution.bridge import probe_health
        from goldbot.ops import accounts
        return cls(account_id=account_id, get_secret=accounts.get_secret,
                   bridge_probe=lambda url: probe_health(url, health.BRIDGE_TIMEOUT_S))


def _has(ctx: PreflightContext, key: str) -> bool:
    try:
        return bool(ctx.get_secret(key))
    except Exception:
        return False


def _health_ctx(ctx: PreflightContext, settings: Settings | None, err: str | None) -> health.HealthContext:
    return health.HealthContext(state_dir=ctx.state_dir, now=pd.Timestamp.now("UTC"), settings=settings,
                                settings_error=err, get_secret=ctx.get_secret, disk_usage=ctx.disk_usage,
                                bridge_probe=ctx.bridge_probe)


def _from_check(c: health.Check, name: str, fix: str, *, blocking: bool = True) -> Item:
    return Item(name=name, status=c.status, detail=c.reason, fix=fix, blocking=blocking)


def _unit(ctx: PreflightContext, verb: str, unit: str) -> str | None:
    """`systemctl <verb> <unit>` output ("active", "enabled", ...), or None when systemctl is missing."""
    code, out = ctx.run_cmd(["systemctl", verb, unit])
    return None if code == 127 else (out or "unknown")


def run_preflight(ctx: PreflightContext) -> list[Item]:
    items: list[Item] = []
    aid = ctx.account_id
    settings: Settings | None = None
    err: str | None = None
    try:
        settings = Settings.model_validate(settings_dict(ctx.settings_path))
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
    hctx = _health_ctx(ctx, settings, err)
    local = Path(ctx.settings_path).with_name("settings.local.yaml").name

    # 1. settings
    s = health.check_settings(hctx)
    items.append(Item(name="settings", status="fail" if settings is None else "ok",
                      detail=s.reason if settings is None else "config/settings.yaml and settings.local.yaml load",
                      fix=f"open config/{local} with `sudo -u goldbot nano /opt/goldbot/config/{local}`, correct the "
                          "line named in the error (or move the file away and run `goldbot run setup` again)"))

    # 2. owner email
    owner = settings.auth.owner_email if settings is not None else None
    items.append(Item(name="owner_email", status="ok" if owner else "fail",
                      detail="auth.owner_email is set" if owner else "auth.owner_email is not set: the dashboard owner "
                                                                     "account cannot be created",
                      fix=f"{SETUP}  (step 5: dashboard owner email)"))

    # 3. Telegram
    has_tok = _has(ctx, "telegram-bot-token")
    items.append(Item(name="telegram_token", status="ok" if has_tok else "fail",
                      detail="telegram-bot-token stored" if has_tok else "telegram-bot-token missing: no Approve buttons, no alerts",
                      fix=f"{SETUP}  (step 3: Telegram bot token)"))
    ids = settings.telegram.allowed_user_ids if settings is not None else []
    items.append(Item(name="telegram_allowlist", status="ok" if ids else "fail",
                      detail=f"{len(ids)} allowed Telegram user(s)" if ids else "telegram.allowed_user_ids is empty: "
                                                                               "nobody can approve an entry",
                      fix=f"{SETUP}  (step 4: your Telegram user id)"))

    # 4. tunnel service
    st = _unit(ctx, "is-active", TUNNEL_UNIT)
    items.append(Item(name="tunnel_service", status="ok" if st == "active" else "fail",
                      detail=f"{TUNNEL_UNIT} is {st}" if st else "systemctl not found (run preflight on the brain)",
                      fix="sudo goldbot-tunnel <mt5 private ip>   (then `journalctl -u goldbot-bridge-tunnel -n 20` "
                          "if it stays inactive; the MT5 box must have run goldbot-mt5-authorize)"))

    # 5. bridge configured, answering, and the terminal account
    url = ctx.get_secret(f"mt5-bridge-url-{aid}") if _has(ctx, f"mt5-bridge-url-{aid}") else None
    token = ctx.get_secret(f"mt5-bridge-token-{aid}") if _has(ctx, f"mt5-bridge-token-{aid}") else None
    missing = [k for k, v in (("address", url), ("token", token)) if not v]
    items.append(Item(name="bridge_config", status="fail" if missing else "ok",
                      detail=f"bridge {' and '.join(missing)} missing for {aid}" if missing else f"bridge address and token stored for {aid}",
                      fix=f"{SETUP}  (step 2: MT5 bridge), or `goldbot accounts bridge-use {aid}`"))
    bridge_fix = (f"on the MT5 box: `sudo systemctl status goldbot-bridge@{aid}` (start it with `sudo systemctl start "
                  f"goldbot-bridge@{aid}`); on the brain: `sudo systemctl restart {TUNNEL_UNIT}`")
    bc = health.check_bridge(hctx, aid) if url else None
    if bc is None:
        items.append(Item(name="bridge_health", status="fail", fix=bridge_fix,
                          detail="not checked: " + ("no bridge address stored" if not url else "no probe available")))
    else:
        items.append(_from_check(bc, "bridge_health", bridge_fix))
    acc_fix = (f"if the login is wrong here: `goldbot accounts add {aid}`; if the terminal is logged out or on another "
               "account: redo the one-time MT5 login on the MT5 box (RUNBOOK 0.3, Screen Sharing)")
    if bc is None or bc.status != "ok" or not token or url is None:
        items.append(Item(name="terminal_account", status="fail", fix=acc_fix,
                          detail="not checked: the bridge does not answer yet"))
    else:
        items.append(_terminal_item(ctx, url, token, acc_fix))

    # 6. dashboard build
    dist = ctx.root / "web" / "dist" / "index.html"
    items.append(Item(name="web_dist", status="ok" if dist.is_file() else "fail",
                      detail="web/dist is built" if dist.is_file() else "web/dist/index.html missing: the dashboard has no pages",
                      fix='sudo -u goldbot -H bash -c "cd /opt/goldbot/web && npm ci && npm run build"'))

    # 7. state files private
    items.append(_private_files(ctx))

    # 8. backups (warn only)
    have = [k for k in RESTIC_KEYS if _has(ctx, k)]
    items.append(Item(name="backups", status="ok" if len(have) == len(RESTIC_KEYS) else "warn", blocking=False,
                      detail="restic keys stored" if len(have) == len(RESTIC_KEYS)
                      else f"backups not configured ({len(have)} of {len(RESTIC_KEYS)} restic keys stored)",
                      fix=f"{SETUP}  (step 8: backups), then `goldbot run backup --init` (RUNBOOK 0.6)"))

    # 9. time sync
    items.append(_time_sync(ctx))

    # 10. disk and memory
    items.append(_from_check(health.check_disk(hctx), "disk",
                             "free space: `sudo journalctl --vacuum-size=200M` and `sudo apt-get clean`, then check "
                             "`du -sh /opt/goldbot/*`"))
    items.append(_memory(ctx))

    # 11. deploy timer
    t = _unit(ctx, "is-enabled", DEPLOY_TIMER)
    items.append(Item(name="deploy_timer", status="ok" if t == "enabled" else "fail",
                      detail=f"{DEPLOY_TIMER} is {t}" if t else "systemctl not found (run preflight on the brain)",
                      fix=f"sudo systemctl enable --now {DEPLOY_TIMER}"))
    return items


def _terminal_item(ctx: PreflightContext, url: str, token: str, fix: str) -> Item:
    from goldbot.ops import accounts
    try:
        acc = ctx.load_accounts().get(ctx.account_id)
    except Exception as exc:
        return Item(name="terminal_account", status="fail", fix=fix, detail=f"config/accounts.yaml does not load: {type(exc).__name__}")
    if acc is None:
        return Item(name="terminal_account", status="fail", fix=fix, detail=f"{ctx.account_id} is not in config/accounts.yaml")
    try:
        info = ctx.terminal_account(url, token)
    except Exception as exc:          # the type only: a message could carry the URL
        return Item(name="terminal_account", status="fail", fix=fix,
                    detail=f"the bridge did not return the terminal's account ({type(exc).__name__})")
    why = accounts.verify_terminal_account(acc, info)
    if why:
        return Item(name="terminal_account", status="fail", fix=fix, detail=why)
    return Item(name="terminal_account", status="ok", fix=fix,
                detail=f"terminal logged in to the registered {acc.mode} account on {acc.server}")


def _private_files(ctx: PreflightContext) -> Item:
    fix_dir = ctx.state_dir
    files = sorted([*ctx.state_dir.glob("*.db"), *ctx.state_dir.glob("*.db-wal"), *ctx.state_dir.glob("*.db-shm"),
                    *ctx.state_dir.glob(".secrets.json")]) if ctx.state_dir.is_dir() else []
    loose = [f.name for f in files if f.stat().st_mode & 0o077]
    if loose:
        return Item(name="db_files_private", status="fail", detail=f"readable by other users: {', '.join(loose)}",
                    fix=" && ".join(f"sudo chmod 600 {fix_dir / n}" for n in loose))
    detail = f"{len(files)} state file(s) are 0600" if files else "no database yet (the services create it 0600)"
    return Item(name="db_files_private", status="ok", detail=detail, fix=f"sudo chmod 600 {fix_dir}/*.db")


def _time_sync(ctx: PreflightContext) -> Item:
    fix = "sudo systemctl enable --now chrony   (then wait a minute and run preflight again)"
    code, out = ctx.run_cmd(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    chrony = _unit(ctx, "is-active", "chrony")
    if code == 127 or chrony is None:
        return Item(name="time_sync", status="fail", detail="timedatectl/systemctl not found (run preflight on the brain)", fix=fix)
    if chrony != "active":
        return Item(name="time_sync", status="fail", detail=f"chrony is {chrony}", fix=fix)
    if out.strip().lower() != "yes":
        return Item(name="time_sync", status="fail", detail="chrony runs but the clock is not synchronised yet", fix=fix)
    return Item(name="time_sync", status="ok", detail="clock synchronised (chrony)", fix=fix)


def _memory(ctx: PreflightContext) -> Item:
    fix = "free memory: `sudo systemctl stop goldbot-vnc` and other leftovers, check `top`; the brain needs the 24 GB shape"
    info = ctx.meminfo()
    if "MemAvailable" not in info:
        return Item(name="memory", status="fail", detail="cannot read /proc/meminfo (run preflight on the brain)", fix=fix)
    gb = info["MemAvailable"] / 1e6
    total = info.get("MemTotal", 0) / 1e6
    status: Status = "fail" if gb < MEM_FAIL_GB else "warn" if gb < MEM_WARN_GB else "ok"
    return Item(name="memory", status=status, detail=f"{gb:.1f} GB available of {total:.1f} GB", fix=fix)


def render(items: list[Item]) -> str:
    width = max(len(i.name) for i in items)
    lines = ["goldbot preflight (read-only; run before starting the services)"]
    for i in items:
        lines.append(f"{MARK[i.status]} {i.name:<{width}}  {i.detail}")
        if i.status != "ok":
            lines.append(f"   {'':<{width}}  fix: {i.fix}")
    blocking = [i for i in items if i.status == "fail" and i.blocking]
    warns = [i for i in items if i.status != "ok" and not (i.status == "fail" and i.blocking)]
    if blocking:
        lines.append(f"NOT READY: {len(blocking)} blocking problem(s), {len(warns)} warning(s). Fix the ❌ items, then run "
                     "`goldbot run preflight` again.")
    else:
        lines.append(f"READY: no blocking problems ({len(warns)} warning(s)). Start the services (RUNBOOK 0.4), "
                     "then `goldbot run health`.")
    return "\n".join(lines)


def exit_code(items: list[Item]) -> int:
    return 1 if any(i.status == "fail" and i.blocking for i in items) else 0


def main(argv: list[str], ctx: PreflightContext | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goldbot run preflight", description="Read-only checklist before starting services.")
    ap.add_argument("--account", default="icm-demo", help="the MT5 account this server trades (default icm-demo)")
    args = ap.parse_args(argv)
    ctx = ctx or PreflightContext.from_runtime(args.account)
    items = run_preflight(ctx)
    print(render(items))
    return exit_code(items)
