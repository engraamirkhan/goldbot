"""The service commands: each starts one long-running service from goldbot/ops/services (NSSM on the VPS, the systemd
units in goldbot/ops/linux/systemd). The handlers are the old dispatcher's branches, verbatim."""
from __future__ import annotations

from goldbot.ops.commands.registry import register_command
from goldbot.ops.services._base import resolve
from goldbot.ops.services.api import run_api
from goldbot.ops.services.bridge import run_bridge
from goldbot.ops.services.engine import run_engine
from goldbot.ops.services.news import run_news
from goldbot.ops.services.scheduler import run_scheduler
from goldbot.ops.services.supervisor import run_supervisor
from goldbot.ops.services.telegram import run_telegram
from goldbot.ops.services.webhook import run_webhook


@register_command("supervisor", pos=10)
def _supervisor(cmd: str, args: list[str]) -> None:
    resolve(run_supervisor)()


@register_command("engine", pos=20)
def _engine(cmd: str, args: list[str]) -> None:
    resolve(run_engine)(args[0])


@register_command("bridge", pos=40)
def _bridge(cmd: str, args: list[str]) -> None:
    resolve(run_bridge)(args[0])


@register_command("api", pos=50)
def _api(cmd: str, args: list[str]) -> None:
    resolve(run_api)()


@register_command("webhook", pos=60)
def _webhook(cmd: str, args: list[str]) -> None:
    resolve(run_webhook)()


@register_command("scheduler", pos=70)
def _scheduler(cmd: str, args: list[str]) -> None:
    resolve(run_scheduler)()


@register_command("telegram", pos=80)
def _telegram(cmd: str, args: list[str]) -> None:
    resolve(run_telegram)()


@register_command("news", pos=90)
def _news(cmd: str, args: list[str]) -> None:
    resolve(run_news)()
