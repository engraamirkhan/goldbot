"""The Telegram bot token is part of every Bot API URL, and httpx logs request URLs at INFO: the service entry point
must keep httpx below INFO so the token never reaches journald (security review of the first deploy, 2026-10-16)."""
import logging

import goldbot.ops.run  # noqa: F401  (configures logging on import, as the services do)


def test_httpx_request_lines_are_not_logged_at_info():
    # the logger's own level: under pytest the root logger already has handlers, so basicConfig's root level is moot
    assert logging.getLogger("httpx").level >= logging.WARNING


def test_a_bot_api_request_line_never_reaches_the_log(caplog):
    caplog.set_level(logging.INFO)
    logging.getLogger("httpx").info("HTTP Request: POST https://api.telegram.org/bot123456789:FAKE/getUpdates")
    assert "bot123456789" not in caplog.text
