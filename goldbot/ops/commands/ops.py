"""Operations commands that hand over to their own module's main: backups and the restore drill, setup, preflight,
health, the reports, the bridge smoke and the lost-record repair. The handlers are the old dispatcher's branches, verbatim (backup receives
its own name); bridge-smoke (BACKLOG 1) is new."""
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


@register_command("positions", pos=205)
def _positions(cmd: str, args: list[str]) -> None:
    """Open trades per enabled account, read-only (state files), for an operator without the dashboard login."""
    from goldbot.ops.positions import main as positions_main
    sys.exit(positions_main(args))


@register_command("shadow-report", pos=206)
def _shadow_report(cmd: str, args: list[str]) -> None:
    """Shadow trades, cost probes and engine history-gap/guard state, read-only (state files), no shell access needed."""
    from goldbot.ops.shadow_report import main as shadow_report_main
    sys.exit(shadow_report_main(args))


@register_command("fill-count", pos=207)
def _fill_count(cmd: str, args: list[str]) -> None:
    """Demo fills per account per day by kind, cumulative and progress against the 300-by-Friday target, read-only."""
    from goldbot.ops.fill_count import main as fill_count_main
    sys.exit(fill_count_main(args))


@register_command("broker-history-depth", pos=208)
def _broker_history_depth(cmd: str, args: list[str]) -> None:
    """Earliest bar the broker's MT5 server serves per timeframe, read-only (get_bars only; quiet window)."""
    from goldbot.ops.broker_depth import main as depth_main
    sys.exit(depth_main(args))


@register_command("monthly-report", pos=220)
def _monthly_report(cmd: str, args: list[str]) -> None:
    from goldbot.ops.monthly_report import main as monthly_main
    sys.exit(monthly_main(args))


@register_command("perf-report", pos=230)
def _perf_report(cmd: str, args: list[str]) -> None:
    from goldbot.ops.perf_report import main as perf_main
    sys.exit(perf_main(args))


@register_command("repair-lost-record", pos=310)
def _repair_lost_record(cmd: str, args: list[str]) -> None:
    """Trading-safety MEDIUM 2026-10-26: lost exploration records written from the broker's deals (engine stopped)."""
    from goldbot.ops.repair_records import main as repair_main
    sys.exit(repair_main(args))


@register_command("bridge-smoke", pos=250)
def _bridge_smoke(cmd: str, args: list[str]) -> None:
    """BACKLOG 1 (ADR 0021): one minimum-lot round trip on a demo terminal, after one owner click."""
    from goldbot.ops.smoke import main as smoke_main
    sys.exit(smoke_main(args))
