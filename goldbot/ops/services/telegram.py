"""The Telegram service (`python -m goldbot.ops.run telegram`). Moved verbatim from goldbot/ops/run.py, which
re-exports it."""
from __future__ import annotations


def run_telegram() -> None:
    from goldbot.config import load_settings
    from goldbot.ops import accounts
    from goldbot.ops.health import start_heartbeat
    from goldbot.telegram.bot import TelegramBot
    settings = load_settings()
    token = accounts.get_secret("telegram-bot-token")
    if not token:
        raise SystemExit("no telegram-bot-token in the keyring: python -m goldbot.ops.accounts set telegram-bot-token")
    if not settings.telegram.allowed_user_ids:
        raise SystemExit("settings.yaml telegram.allowed_user_ids is empty: add your Telegram user id")
    start_heartbeat("state", "telegram")
    TelegramBot(token, "state", set(settings.telegram.allowed_user_ids), settings=settings.telegram).run()
