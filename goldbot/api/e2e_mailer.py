"""E2E-only fake mailer: the Playwright auth tests (web/e2e/auth.spec.ts) read the owner's sign-in link from a JSONL
outbox instead of a mailbox. It never sends anything, and it is REFUSED (E2EMailerRefused, at construction, before any
file is touched) unless every one of these holds:

1. the API it serves is bound to a loopback address, exactly `127.0.0.1` or `::1` (not `localhost`, which a hosts file
   can point anywhere, not `0.0.0.0`/`::` or any other address);
2. the explicit e2e flag is set: environment variable `GOLDBOT_E2E_FAKE_MAILER` is exactly `1`;
3. the outbox lies in a throwaway e2e state dir: its directory is inside the system temp directory, is named with the
   e2e server's mkdtemp prefix (`goldbot-e2e-`), is owned by this user and has mode 0700 (what mkdtemp makes); and the
   temp directory itself contains neither this repo nor /var/lib/goldbot (TMPDIR is the caller's to set, so
   `TMPDIR=$HOME` must not make the repo's state/ count as temporary).

Loopback alone proves nothing: the deployed API also listens on 127.0.0.1, behind cloudflared (app.client_ip), hence
the flag and the state-dir rule. It cannot be selected by configuration at all: no setting names it (settings reject
unknown keys), `mailer_from_settings` only ever returns the SMTP sender, and no module under goldbot/ imports this one;
only scripts/e2e_server.py builds it, passing the same host it hands to uvicorn (tests/test_e2e_mailer.py). The outbox
is opened with O_NOFOLLOW and created 0600: never through a symlink, never readable by other users.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path

E2E_FLAG = "GOLDBOT_E2E_FAKE_MAILER"
LOOPBACK_BINDS = frozenset({"127.0.0.1", "::1"})
STATE_PREFIX = "goldbot-e2e-"                   # scripts/e2e_server.py: tempfile.mkdtemp(prefix=STATE_PREFIX)
REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOYED_STATE = Path("/var/lib/goldbot")


class E2EMailerRefused(RuntimeError):
    """The fake mailer was asked for outside a local end-to-end test run."""


def require_e2e(bind_host: str, outbox: Path, environ: Mapping[str, str] | None = None) -> Path:
    """The outbox path (its directory resolved, the file itself never followed) when the fake mailer may run here;
    E2EMailerRefused otherwise (module docstring)."""
    env = os.environ if environ is None else environ
    if bind_host not in LOOPBACK_BINDS:
        raise E2EMailerRefused(f"the e2e fake mailer needs the API bound to 127.0.0.1 or ::1, not {bind_host!r}")
    if env.get(E2E_FLAG) != "1":
        raise E2EMailerRefused(f"the e2e fake mailer needs {E2E_FLAG}=1 (end-to-end test runs only)")
    tmp = Path(tempfile.gettempdir()).resolve()
    # resolved too: on macOS /var is a symlink to /private/var
    if any(p.is_relative_to(tmp) for p in (REPO_ROOT, DEPLOYED_STATE, DEPLOYED_STATE.resolve())):
        raise E2EMailerRefused(f"the system temp directory {tmp} contains the repo or {DEPLOYED_STATE}: the e2e fake "
                               f"mailer will not write there")
    parent = Path(outbox).parent.resolve()
    if not parent.is_relative_to(tmp):
        raise E2EMailerRefused("the e2e fake mailer writes only inside the system temp directory")
    try:
        st = os.stat(parent, follow_symlinks=False)
    except OSError:
        raise E2EMailerRefused("the e2e fake mailer's state directory does not exist") from None
    if not (stat.S_ISDIR(st.st_mode) and parent.name.startswith(STATE_PREFIX) and st.st_uid == os.getuid()
            and stat.S_IMODE(st.st_mode) == 0o700):
        raise E2EMailerRefused(f"the e2e fake mailer writes only in a {STATE_PREFIX}* directory made by mkdtemp "
                               f"(owned by this user, mode 0700)")
    return parent / Path(outbox).name


class CaptureMailer:
    """`goldbot.api.mailer.Mailer` that appends each message to `outbox` as one JSON line {to, subject, body}."""

    def __init__(self, outbox: Path, *, bind_host: str, environ: Mapping[str, str] | None = None):
        self.outbox = require_e2e(bind_host, outbox, environ)
        self._lock = threading.Lock()           # sends run in the API's thread pool

    def ready(self) -> bool:
        return True

    def send(self, to: str, subject: str, body: str) -> None:
        line = json.dumps({"to": to, "subject": subject, "body": body}) + "\n"
        with self._lock:
            # never through a symlink (O_NOFOLLOW raises ELOOP), and created readable by this user only
            fd = os.open(self.outbox, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(line)
