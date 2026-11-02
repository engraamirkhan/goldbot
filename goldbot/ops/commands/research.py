"""Research commands (goldbot/research): `owner-history`, `ruin` (G-5), `sizing-feasibility` (G-7), the TradingView
author ideas `tv-ideas`, the daily learning-ops review `daily-review`, and `autodisc-register` (ADR 0058 phase 4). The first three are the old dispatcher's branches, verbatim."""
from __future__ import annotations

import sys

from goldbot.ops.commands.registry import register_command


@register_command("owner-history", pos=190)
def _owner_history(cmd: str, args: list[str]) -> None:
    from goldbot.research.owner_history import main as owner_history_main
    sys.exit(owner_history_main(args))


@register_command("ruin", pos=210)
def _ruin(cmd: str, args: list[str]) -> None:
    from goldbot.research.ruin import main as ruin_main
    sys.exit(ruin_main(args))


@register_command("sizing-feasibility", pos=240)
def _sizing_feasibility(cmd: str, args: list[str]) -> None:
    from goldbot.research.min_lot import main as feasibility_main
    sys.exit(feasibility_main(args))


@register_command("tv-ideas", pos=285)
def _tv_ideas(cmd: str, args: list[str]) -> None:
    from goldbot.data.tv_ideas import main as tv_ideas_main
    sys.exit(tv_ideas_main(args))


@register_command("ledger", pos=290)
def _ledger(cmd: str, args: list[str]) -> None:
    from goldbot.ledger.cli import main as ledger_main
    sys.exit(ledger_main(args))


@register_command("daily-review", pos=300)
def _daily_review(cmd: str, args: list[str]) -> None:
    from goldbot.research.daily_review import main as daily_review_main
    sys.exit(daily_review_main(args))


@register_command("autodisc-register", pos=320)
def _autodisc_register(cmd: str, args: list[str]) -> None:
    from goldbot.ops.autodisc_register import main as autodisc_register_main
    sys.exit(autodisc_register_main(args))
