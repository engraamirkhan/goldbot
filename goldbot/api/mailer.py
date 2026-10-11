"""Outgoing mail for the owner's email sign-in link: plain SMTP with STARTTLS, standard library only.

* Settings `auth.mail` (goldbot/config.py MailSettings): host, port, from-address, dashboard origin, enabled. Real
  values only in the server's git-ignored config/settings.local.yaml (the repo is public).
* The SMTP password (Gmail: a Google App Password) is read from the OS keyring under `smtp-password`
  (`python -m goldbot.ops.accounts set smtp-password`); it is never a setting, an env var, in the repo or in a log.
* `SmtpMailer.send` blocks (connect, STARTTLS, login, send, each bounded by `timeout_s`): the API calls it off the
  event loop, after the generic answer has gone. A failure is the caller's to log, by exception type only: smtplib's
  errors can carry the recipient and the message.

Check the setup on the server without signing in: `python -m goldbot.ops.run mail-test` (on the brain:
`goldbot run mail-test`) sends a test message to `auth.owner_email`.
"""
from __future__ import annotations

import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from typing import Callable, Protocol

from goldbot.config import MailSettings

SMTP_PASSWORD_KEY = "smtp-password"
READY_CACHE_S = 60.0                     # how long `SmtpMailer.ready()` trusts its last keyring lookup
SecretLookup = Callable[[str], str | None]


class MailNotConfigured(RuntimeError):
    """Mail is off, incomplete, or has no SMTP password in the keyring."""


class Mailer(Protocol):
    def ready(self) -> bool:
        """True when a send can be attempted (settings complete and the SMTP password stored)."""
        ...

    def send(self, to: str, subject: str, body: str) -> None:
        """Send one plain-text message; raises on any failure."""
        ...


def _keyring_secret(key: str) -> str | None:
    from goldbot.ops.accounts import get_secret
    return get_secret(key)


class SmtpMailer:
    """SMTP submission with STARTTLS (a verified TLS context) and login; the password is looked up per send, so
    storing it needs no API restart. `ready()` is cached for READY_CACHE_S: it runs on every /api/auth/state and
    email-link request, and a keyring lookup per request is needless load (security review L5)."""

    def __init__(self, settings: MailSettings, secret: SecretLookup = _keyring_secret,
                 clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self._secret = secret
        self._clock = clock
        self._ready: tuple[float, bool] | None = None        # (checked at, answer)

    def _password(self) -> str | None:
        try:
            return self._secret(SMTP_PASSWORD_KEY) or None
        except Exception:                       # noqa: BLE001 - an unreadable keyring means not configured
            return None

    def ready(self) -> bool:
        now = self._clock()
        if self._ready is not None and now - self._ready[0] < READY_CACHE_S:
            return self._ready[1]
        s = self.settings
        ok = bool(s.enabled and s.host and s.from_address and s.dashboard_origin) and self._password() is not None
        self._ready = (now, ok)
        return ok

    def send(self, to: str, subject: str, body: str) -> None:
        s = self.settings
        password = self._password()
        if not (s.enabled and s.host and s.from_address) or password is None:
            raise MailNotConfigured("auth.mail is off or incomplete, or no smtp-password in the keyring")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = s.from_address, to, subject
        msg.set_content(body)
        with smtplib.SMTP(s.host, s.port, timeout=s.timeout_s) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(s.username or s.from_address, password)
            smtp.send_message(msg)


def email_link_message(origin: str, token: str, ttl_min: int) -> tuple[str, str]:
    """(subject, plain-text body) of the owner's sign-in link. The token rides in the URL fragment (`#signin=`): a
    browser never sends the fragment to a server, so it stays out of access logs and Referer headers."""
    return ("Your goldbot sign-in link",
            f"Sign in to goldbot:\n\n{origin}/#signin={token}\n\n"
            f"The link works once and expires in {ttl_min} minutes. You will still need the code from your "
            f"authenticator app.\n\n"
            f"If this wasn't you, ignore this email: nobody can sign in with the link without your authenticator "
            f"code.\n")


def sign_in_notice_message(when_utc: str, network: str) -> tuple[str, str]:
    """(subject, body) of the notice sent after every sign-in with an emailed link: no token, and only a coarse
    network hint (`auth.coarse_ip`), never the full address."""
    return ("goldbot: new sign-in with an emailed link",
            f"Someone signed in to the goldbot dashboard as the owner with an emailed sign-in link and the "
            f"authenticator code, at {when_utc}, from {network}.\n\n"
            f"If this was you, there is nothing to do.\n\n"
            f"If it was not you, your mailbox and your authenticator are both compromised: on the server run "
            f"`python -m goldbot.ops.run revoke-sessions` (signs everyone out and cancels every sign-in link), set "
            f"auth.mail.enabled to false, change your password and your email password, and check the audit trail.\n")


def mailer_from_settings(settings: MailSettings) -> SmtpMailer | None:
    """The SMTP sender when `auth.mail.enabled`, else None (the email sign-in link is then not offered)."""
    return SmtpMailer(settings) if settings.enabled else None


def main(argv: list[str]) -> int:
    """`python -m goldbot.ops.run mail-test` (argv ["test"]): one test message to auth.owner_email; prints the error
    type on failure, never the address or the server's reply."""
    if argv != ["test"]:
        print(__doc__)
        return 1
    from goldbot.config import load_settings
    auth = load_settings().auth
    m = mailer_from_settings(auth.mail)
    if m is None or not auth.owner_email or not m.ready():
        print("refused: set auth.mail (enabled, host, from_address, dashboard_origin) and auth.owner_email in "
              "config/settings.local.yaml, and store the password with `python -m goldbot.ops.accounts set "
              "smtp-password`")
        return 2
    try:
        m.send(auth.owner_email, "goldbot test message",
               "This is a test from goldbot: email sign-in links can reach you.\n")
    except Exception as exc:                    # noqa: BLE001 - the type only: smtplib errors carry addresses
        print(f"not sent: {type(exc).__name__}")
        return 1
    print("sent a test message to auth.owner_email")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
