"""E2E-only fake mailer: the Playwright auth tests (web/e2e/auth.spec.ts) read the owner's sign-in link from a JSONL
outbox instead of a mailbox. It never sends anything, and it is REFUSED (E2EMailerRefused, at construction, before any
file is touched) unless every one of these holds:

1. the API it serves is bound to a loopback address, exactly `127.0.0.1` or `::1` (not `localhost`, which a hosts file
   can point anywhere, not `0.0.0.0`/`::` or any other address);
2. the explicit e2e flag is set: environment variable `GOLDBOT_E2E_FAKE_MAILER` is exactly `1`;
3. the outbox lies inside the system temp directory (the e2e server's throwaway state dir), never a deployed state dir.

Loopback alone proves nothing: the deployed API also listens on 127.0.0.1, behind cloudflared (app.client_ip), hence
the flag and the temp-dir rule. It cannot be selected by configuration at all: no setting names it (settings reject
unknown keys), `mailer_from_settings` only ever returns the SMTP sender, and no module under goldbot/ imports this one;
only scripts/e2e_server.py builds it, passing the same host it hands to uvicorn (tests/test_e2e_mailer.py).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path

E2E_FLAG = "GOLDBOT_E2E_FAKE_MAILER"
LOOPBACK_BINDS = frozenset({"127.0.0.1", "::1"})


class E2EMailerRefused(RuntimeError):
    """The fake mailer was asked for outside a local end-to-end test run."""


def require_e2e(bind_host: str, outbox: Path, environ: Mapping[str, str] | None = None) -> Path:
    """The resolved outbox path when the fake mailer may run here; E2EMailerRefused otherwise (module docstring)."""
    env = os.environ if environ is None else environ
    if bind_host not in LOOPBACK_BINDS:
        raise E2EMailerRefused(f"the e2e fake mailer needs the API bound to 127.0.0.1 or ::1, not {bind_host!r}")
    if env.get(E2E_FLAG) != "1":
        raise E2EMailerRefused(f"the e2e fake mailer needs {E2E_FLAG}=1 (end-to-end test runs only)")
    path = Path(outbox).resolve()
    if not path.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise E2EMailerRefused("the e2e fake mailer writes only inside the system temp directory")
    return path


class CaptureMailer:
    """`goldbot.api.mailer.Mailer` that appends each message to `outbox` as one JSON line {to, subject, body}."""

    def __init__(self, outbox: Path, *, bind_host: str, environ: Mapping[str, str] | None = None):
        self.outbox = require_e2e(bind_host, outbox, environ)
        self._lock = threading.Lock()           # sends run in the API's thread pool

    def ready(self) -> bool:
        return True

    def send(self, to: str, subject: str, body: str) -> None:
        line = json.dumps({"to": to, "subject": subject, "body": body}) + "\n"
        with self._lock, open(self.outbox, "a", encoding="utf-8") as fh:
            fh.write(line)
