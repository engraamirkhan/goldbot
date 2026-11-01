"""What the MT5 keyring login (goldbot/ops/login.py) has sent to the broker, kept across process restarts.

Security review of 4278c39f (HIGH) and trading-safety review (MEDIUM 1, 2): the bridge restarts every 10 s under
systemd and the watchdog restarts it too, so a per-process limit is no limit: a refused password resent on every start
gets the account locked by the broker. Two small files per account under state/mt5-login/, owner-only, holding times
and MT5 error codes only, never a login or a password:
* `<account>.refused`: the password was refused (RES_E_AUTH_FAILED, -6). While it exists no login path sends any
  credential. It goes when the password or login is stored again (`accounts set/add`, `accounts.set_secret`), on a
  successful login, or with `goldbot-mt5 login <account> --retry-refused` (one deliberate try).
* `<account>.attempts`: one line per login sent in the last 24 h, `<epoch seconds> ok|fail`. The backoff after
  consecutive failures and the hourly cap (`mt5_login.max_attempts_per_hour`) are read from it, so a restart neither
  resets the wait nor the count.
A file that cannot be read counts against a login (a refusal that cannot be read is a refusal)."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from goldbot.base import write_private
from goldbot.config import ROOT, Mt5LoginSettings

DEFAULT_DIR = ROOT / "state" / "mt5-login"
KEEP_S = 24 * 3600
_ACCOUNT = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def backoff_s(settings: Mt5LoginSettings, n_failed: int) -> float:
    """The wait after the n-th failed login in a row: backoff_s, doubling, at most backoff_max_s."""
    if n_failed <= 0:
        return 0.0
    return float(min(settings.backoff_s * 2 ** (n_failed - 1), settings.backoff_max_s))


class LoginLedger:
    def __init__(self, account_id: str, directory: Path | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        if not _ACCOUNT.match(account_id):
            raise ValueError("account id must be lower-case letters, digits and dashes")
        self.account_id = account_id
        self._dir = directory if directory is not None else DEFAULT_DIR
        self._clock = clock

    @property
    def _refused(self) -> Path:
        return self._dir / f"{self.account_id}.refused"

    @property
    def _attempts(self) -> Path:
        return self._dir / f"{self.account_id}.attempts"

    def now(self) -> float:
        """The ledger's clock (epoch seconds), so callers time an attempt on the same clock as its records."""
        return self._clock()

    # ------------------------------------------------------------------ refusal
    def refusal(self) -> str | None:
        """"<UTC time> code <n>" while the password stands refused, else None."""
        if not self._refused.exists():
            return None
        try:
            text = self._refused.read_text(encoding="utf-8").strip()
        except OSError:
            return "unreadable refusal mark"
        return re.sub(r"[^0-9A-Za-z:+ .-]", "", text)[:64] or "refusal mark"

    def mark_refused(self, code: int) -> None:
        at = datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        write_private(self._refused, f"{at} code {int(code)}\n")

    def clear(self) -> None:
        """New credentials: forget the refusal and the attempts (their waits and counts were for the old ones)."""
        for p in (self._refused, self._attempts):
            p.unlink(missing_ok=True)

    # ------------------------------------------------------------------ attempts
    def attempts(self) -> list[tuple[float, bool]]:
        """(epoch, ok) of the logins sent in the last 24 h, oldest first. An unreadable file reads as one failure now."""
        if not self._attempts.exists():
            return []
        try:
            lines = self._attempts.read_text(encoding="utf-8").splitlines()
        except OSError:
            return [(self._clock(), False)]
        out: list[tuple[float, bool]] = []
        for line in lines:
            parts = line.split()
            if len(parts) == 2 and parts[1] in ("ok", "fail"):
                try:
                    out.append((float(parts[0]), parts[1] == "ok"))
                except ValueError:
                    continue
        return sorted(out)

    def _write(self, rows: list[tuple[float, bool]]) -> None:
        now = self._clock()
        keep = [a for a in rows if now - a[0] < KEEP_S]
        write_private(self._attempts, "".join(f"{t:.0f} {'ok' if k else 'fail'}\n" for t, k in keep))

    def begin(self) -> None:
        """Written BEFORE a login is sent, as a failure: a process killed mid-login still counts it."""
        self._write(self.attempts() + [(self._clock(), False)])

    def finish(self, ok: bool) -> None:
        """The login returned: the attempt is re-timed to now (the backoff runs from the end of the login, not its
        start; trading-safety review MEDIUM 2) with its outcome."""
        rows = self.attempts()
        self._write(rows[:-1] + [(self._clock(), ok)] if rows else [(self._clock(), ok)])

    def clear_refusal(self) -> None:
        self._refused.unlink(missing_ok=True)

    def failures_in_a_row(self) -> int:
        n = 0
        for _, ok in reversed(self.attempts()):
            if ok:
                break
            n += 1
        return n

    def in_last_hour(self) -> int:
        now = self._clock()
        return sum(1 for t, _ in self.attempts() if now - t < 3600)

    def wait_s(self, settings: Mt5LoginSettings) -> float:
        """Seconds until the next login may be sent: the backoff after the failures in a row, from the last one."""
        a = self.attempts()
        n = self.failures_in_a_row()
        if not a or n == 0:
            return 0.0
        return max(0.0, a[-1][0] + backoff_s(settings, n) - self._clock())


def clear_for_secret(key: str, directory: Path | None = None) -> None:
    """`accounts.set_secret` hook: storing `mt5-<account>` (the password) or `mt5-login-<account>` clears that
    account's refusal and attempts. Other keys (bridge token, listen address, ...) leave them alone."""
    if key.startswith("mt5-login-"):
        account_id = key[len("mt5-login-"):]
    elif key.startswith("mt5-") and not key.startswith("mt5-bridge-"):
        account_id = key[len("mt5-"):]
    else:
        return
    if _ACCOUNT.match(account_id):
        LoginLedger(account_id, directory).clear()
