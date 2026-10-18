"""Research commands (goldbot/research): `owner-history`, `ruin` (G-5), `sizing-feasibility` (G-7), and the TradingView
chart-export pair `tv-import` / `tv-repaint-check`. The first three are the old dispatcher's branches, verbatim."""
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


@register_command("tv-import", pos=260)
def _tv_import(cmd: str, args: list[str]) -> None:
    from goldbot.data.tv_csv import main as tv_import_main
    sys.exit(tv_import_main(args))


@register_command("tv-repaint-check", pos=270)
def _tv_repaint_check(cmd: str, args: list[str]) -> None:
    from goldbot.research.tv_repaint import main as tv_repaint_main
    sys.exit(tv_repaint_main(args))
