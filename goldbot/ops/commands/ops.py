"""Operations commands that hand over to their own module's main: backups and the restore drill, setup, preflight,
health and the reports. The handlers are the old dispatcher's branches, verbatim (backup receives its own name)."""
from __future__ import annotations

import sys

from goldbot.ops.commands.registry import register_command


@register_command("backup", "restore", "restore-drill", pos=140)
def _backup(cmd: str, args: list[str]) -> None:
    from goldbot.ops.backup import main as backup_main
    sys.exit(backup_main([cmd, *args]))


@register_command("setup", pos=150)
def _setup(cmd: str, args: list[str]) -> None:
    from goldbot.ops.setup_wizard import main as setup_main
    sys.exit(setup_main(args))


@register_command("preflight", pos=160)
def _preflight(cmd: str, args: list[str]) -> None:
    from goldbot.ops.preflight import main as preflight_main
    sys.exit(preflight_main(args))


@register_command("health", pos=200)
def _health(cmd: str, args: list[str]) -> None:
    from goldbot.ops.health import main as health_main
    sys.exit(health_main(args))


@register_command("monthly-report", pos=220)
def _monthly_report(cmd: str, args: list[str]) -> None:
    from goldbot.ops.monthly_report import main as monthly_main
    sys.exit(monthly_main(args))


@register_command("perf-report", pos=230)
def _perf_report(cmd: str, args: list[str]) -> None:
    from goldbot.ops.perf_report import main as perf_main
    sys.exit(perf_main(args))
