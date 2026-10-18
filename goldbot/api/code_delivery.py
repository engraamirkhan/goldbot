"""How the owner's one-time sign-in code reaches them (BACKLOG 77, ADR 0028 as amended 2026-10-17): a `CodeDelivery`
seam with two implementations, chosen by `auth.code_channel` (goldbot/config.py AuthSettings): `telegram` (default) and
`mail` (the SMTP path of goldbot/api/mailer.py, kept for later).

Telegram design rules (security conditions 1-4):
* One chat. The keyring key `telegram-owner-chat-id` (`python -m goldbot.ops.accounts set telegram-owner-chat-id`) holds
  ONE positive integer that must also be in `telegram.allowed_user_ids`; before every send a Bot API `getChat` must
  return `type == "private"` for it. Any failure sends nothing (the caller audits `code_channel_unavailable`). Never a
  group, never a chat id from a request, and no code path can change or set the chat id.
* The API sends with `sendMessage` itself, over HTTPS with certificate checks, after the answer has gone (the same
  background task as the mail). It never calls `getUpdates`: the bot service alone polls. The code is never written to
  agent_runs.jsonl (`AuthStore._notify_owner`), the Telegram outbox, aux.db or any file; only the Telegram message id is
  stored with the challenge, so the message can be deleted on use, burn, resend and expiry (`deleteMessage`, best
  effort).
* The bot token is in the request URL: `quiet_http_logs()` sets httpx and httpcore to WARNING, and a failure is
  reported by exception type only (httpx errors can carry the URL).
* The message is the code on line 1, its expiry and the "Not you?" line; `protect_content` is on (no forward or copy
  of the message) and there is no `parse_mode`, so nothing in it is interpreted.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Protocol

import httpx
from pydantic import Field

from goldbot.api.mailer import Mailer, email_code_message
from goldbot.base import FrozenRecord

BOT_TOKEN_KEY = "telegram-bot-token"             # the bot service's own token (goldbot/ops/services/telegram.py)
CHAT_ID_KEY = "telegram-owner-chat-id"
API_BASE = "https://api.telegram.org"
READY_CACHE_S = 60.0                              # how long `ready()` trusts its last keyring lookup
NOT_YOU = "Not you? Your password is known — change it with your authenticator."
SecretLookup = Callable[[str], str | None]


class CodeChannelUnavailable(RuntimeError):
    """The channel cannot deliver: not set up, the chat is not the owner's private chat, or the Bot API said no.
    The message never carries the token, the chat id or the code."""


class CodeMessage(FrozenRecord):
    """What a delivery formats: the code (never in a repr, so never in a log line) and when it works until."""
    code: str = Field(repr=False)
    expires_local: str
    expires_utc: str
    requested_local: str
    network: str


class CodeDelivery(Protocol):
    channel: str

    def ready(self) -> bool:
        """True when a send can be attempted (setup complete); no network."""
        ...

    def send(self, msg: CodeMessage) -> str | None:
        """Deliver the code; raises on any failure. Returns the delivered message's id when it can be deleted later."""
        ...

    def delete(self, message_id: str) -> None:
        """Best effort removal of a delivered code (a failure is the caller's to ignore)."""
        ...


def quiet_http_logs() -> None:
    """httpx logs every request URL at INFO and the bot token is in that URL: keep both loggers at WARNING."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _keyring_secret(key: str) -> str | None:
    from goldbot.ops.accounts import get_secret
    return get_secret(key)


def parse_chat_id(raw: str | None) -> int | None:
    """The one positive integer of `telegram-owner-chat-id`, or None: ASCII digits only (no sign, space, decimal
    point or zero), so a group id (negative) can never pass."""
    if raw is None or not (raw.isascii() and raw.isdigit()):
        return None
    n = int(raw)
    return n if n > 0 else None


def telegram_code_text(msg: CodeMessage) -> str:
    return (f"{msg.code}\n\n"
            f"This is your goldbot sign-in code. It works once, until {msg.expires_local} ({msg.expires_utc}).\n\n"
            f"{NOT_YOU}")


class TelegramCodeDelivery:
    channel = "telegram"

    def __init__(self, allowed_user_ids: Sequence[int], secret: SecretLookup = _keyring_secret,
                 transport: httpx.BaseTransport | None = None, timeout_s: float = 10.0,
                 clock: Callable[[], float] = time.monotonic):
        self._allowed = frozenset(allowed_user_ids)
        self._secret = secret
        self._transport = transport
        self._timeout_s = timeout_s
        self._clock = clock
        self._ready: tuple[float, bool] | None = None

    def _lookup(self, key: str) -> str | None:
        try:
            return self._secret(key) or None
        except Exception:                       # noqa: BLE001 - an unreadable keyring means not configured
            return None

    def _chat_id(self) -> int | None:
        chat = parse_chat_id(self._lookup(CHAT_ID_KEY))
        return chat if chat is not None and chat in self._allowed else None

    def ready(self) -> bool:
        now = self._clock()
        if self._ready is not None and now - self._ready[0] < READY_CACHE_S:
            return self._ready[1]
        ok = self._chat_id() is not None and self._lookup(BOT_TOKEN_KEY) is not None
        self._ready = (now, ok)
        return ok

    def _call(self, token: str, method: str, payload: dict[str, object]) -> object:
        with httpx.Client(transport=self._transport, timeout=self._timeout_s, verify=True,
                          follow_redirects=False) as http:
            r = http.post(f"{API_BASE}/bot{token}/{method}", json=payload)
        data = r.json()
        if r.status_code != 200 or not isinstance(data, dict) or data.get("ok") is not True:
            raise CodeChannelUnavailable(f"{method} was refused")
        return data.get("result")

    def send(self, msg: CodeMessage) -> str | None:
        chat, token = self._chat_id(), self._lookup(BOT_TOKEN_KEY)
        if chat is None or token is None:
            raise CodeChannelUnavailable("telegram-owner-chat-id or the bot token is missing or not allowed")
        info = self._call(token, "getChat", {"chat_id": chat})
        if not isinstance(info, dict) or info.get("type") != "private" or info.get("id") != chat:
            raise CodeChannelUnavailable("the owner chat is not a private chat")
        sent = self._call(token, "sendMessage", {"chat_id": chat, "text": telegram_code_text(msg),
                                                 "protect_content": True})
        mid = sent.get("message_id") if isinstance(sent, dict) else None
        return str(mid) if isinstance(mid, int) else None

    def delete(self, message_id: str) -> None:
        chat, token = self._chat_id(), self._lookup(BOT_TOKEN_KEY)
        if chat is None or token is None or not message_id.isdigit():
            return
        self._call(token, "deleteMessage", {"chat_id": chat, "message_id": int(message_id)})


class MailCodeDelivery:
    """The SMTP path (goldbot/api/mailer.py): the code mailed to `auth.owner_email`; nothing to delete afterwards."""
    channel = "mail"

    def __init__(self, mailer: Mailer | None, owner_email: Callable[[], str | None], enabled: bool = True):
        self._mailer, self._owner, self._enabled = mailer, owner_email, enabled

    def ready(self) -> bool:
        return self._enabled and self._mailer is not None and self._mailer.ready()

    def send(self, msg: CodeMessage) -> str | None:
        to = self._owner()
        if self._mailer is None or to is None:
            raise CodeChannelUnavailable("mail is not configured")
        subject, text = email_code_message(msg.code, msg.expires_local, msg.expires_utc, msg.requested_local,
                                           msg.network)
        self._mailer.send(to, subject, text)
        return None

    def delete(self, message_id: str) -> None:
        return None
