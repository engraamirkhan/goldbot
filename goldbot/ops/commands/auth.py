"""Dashboard sign-in commands (goldbot/api): `revoke-sessions` and `mail-test`. The handlers are the old dispatcher's
branches, verbatim (mail-test ignores any words after it)."""
from __future__ import annotations

import sys

from goldbot.ops.commands.registry import register_command


@register_command("revoke-sessions", pos=170)
def _revoke_sessions(cmd: str, args: list[str]) -> None:
    from goldbot.api.auth import revoke_sessions_cli
    sys.exit(revoke_sessions_cli(args))


@register_command("mail-test", pos=180)
def _mail_test(cmd: str, args: list[str]) -> None:
    from goldbot.api.mailer import main as mail_main
    sys.exit(mail_main(["test"]))
