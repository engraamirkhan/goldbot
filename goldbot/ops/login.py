"""MT5 terminal login from the keyring, without the owner (owner directive 2026-10-20: "MT5 terminal logins on the
boxes must happen without the owner (no VNC step)").

    goldbot-mt5 login <account> [--dismiss-wizard]     # on the MT5 box (python -m goldbot.ops.login <account>)

One routine, `login_terminal`, used by the command and by the bridge (goldbot/ops/services/bridge.py):
* it logs in with the account's configured login (`mt5-login-<account>`), password (`mt5-<account>`) and server
  (config/accounts.yaml), read from the keyring, never prompted for; nothing else is ever logged in to;
* then it checks the terminal is on exactly that account (`accounts.verify_terminal_account`: login, server, demo vs
  real, hedging). A mismatch is failed and never retried; the bridge then refuses every call (wrong_account), as before;
* only a timeout or an IPC error is retried, with a doubling backoff (settings `mt5_login`). An authorization failure
  is never retried: brokers lock an account after repeated bad passwords;
* what it prints, logs and returns names the account id only: the login and the password are scrubbed from any terminal
  error text, and neither is ever formatted into a message.
Real-money accounts take the same path, for their own configured login only: this changes no authority (the phase gate,
`unlock_live` and RiskGate decide what is traded, not whether the terminal is logged in).

The bridge uses it twice: `attach_terminal` logs in when the attach fails (a terminal that lost its saved login sits at
the "Select a company to open an account with" wizard and answers initialize() without a login with IPC timeout -10005),
and `Relogin` is GuardedBroker's hook for a terminal that answers but has no account (/health not_logged_in), paced by
the same backoff and stopped after `heal_max_attempts` failures until the bridge restarts.
"""
from __future__ import annotations

import logging
import re
import sys
import time
from typing import Any, Callable, TypeVar

from goldbot.base import FrozenRecord
from goldbot.config import Mt5LoginSettings, load_settings
from goldbot.ops import accounts

log = logging.getLogger(__name__)

T = TypeVar("T")

# MetaTrader5 package error codes that a retry cannot fix: RES_E_INVALID_PARAMS, RES_E_AUTH_FAILED,
# RES_E_UNSUPPORTED. Everything else (-10005 IPC timeout, -10004 no IPC, -1 generic, ...) may pass on a later try.
PERMANENT_ERRORS = frozenset({-2, -6, -7})
VK_ESCAPE = 0x1B
_LONG_DIGITS = re.compile(r"\d{5,}")


class LoginResult(FrozenRecord):
    ok: bool
    reason: str = ""
    retry: bool = False         # worth another try (a timeout); never after a wrong account or a refused password


def _scrub(text: str, acc: accounts.Account, password: str | None) -> str:
    """Terminal error text with the password, the login and any long digit run (a login by another name) removed."""
    out = str(text)
    if password:
        out = out.replace(password, "***")
    if acc.login is not None:
        out = out.replace(str(int(acc.login)), "***")
    return _LONG_DIGITS.sub("***", out)


def dismiss_wizard() -> None:
    """Best effort (settings `mt5_login.dismiss_wizard`): press Escape on the foreground window, which on the Wine box
    is the terminal's "open an account" wizard when it shows. Done from inside Wine (user32.keybd_event), because the
    bridge and the command run under the Wine Windows Python, which cannot run the box's xdotool reliably; on the Xvfb
    display :99 the effect is the same key press. Not verified on the box; nothing happens off Windows."""
    if sys.platform == "win32":
        import ctypes
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_ESCAPE, 0, 0, 0)
        user32.keybd_event(VK_ESCAPE, 0, 2, 0)        # KEYEVENTF_KEYUP
        time.sleep(1.0)


def _backoff(settings: Mt5LoginSettings, n_failed: int) -> float:
    """The wait after the n-th failed try: backoff_s, doubling, at most backoff_max_s."""
    return float(min(settings.backoff_s * 2 ** (n_failed - 1), settings.backoff_max_s))


def _login_once(acc: accounts.Account, password: str, api: Any, settings: Mt5LoginSettings, *,
                attached: bool) -> LoginResult:
    assert acc.login is not None
    creds = {"login": int(acc.login), "password": password, "server": acc.server, "timeout": settings.timeout_ms}
    try:
        done = api.login(**creds) if attached else api.initialize(path=acc.terminal_path, **creds)
    except Exception as exc:                         # noqa: BLE001 - an IPC error: reported, scrubbed, retried
        return LoginResult(ok=False, reason=f"terminal raised {type(exc).__name__}: {_scrub(str(exc), acc, password)}",
                           retry=True)
    finally:
        creds.clear()
    if not done:
        code, msg = _last_error(api)
        what = "login" if attached else "initialize"
        return LoginResult(ok=False, reason=f"{what} failed: ({code}, {_scrub(msg, acc, password)})",
                           retry=code not in PERMANENT_ERRORS)
    a = api.account_info()
    if a is None:
        return LoginResult(ok=False, reason="the terminal accepted the login but reports no account", retry=True)
    from goldbot.execution.mt5_adapter import account_info_from_mt5
    why = accounts.verify_terminal_account(acc, account_info_from_mt5(a))
    if why:
        return LoginResult(ok=False, reason=f"refused: {_scrub(why, acc, password)}", retry=False)
    return LoginResult(ok=True, reason=f"logged in ({acc.mode}, {acc.server})")


def _last_error(api: Any) -> tuple[int, str]:
    try:
        code, msg = api.last_error()
        return int(code), str(msg)
    except Exception:                                # noqa: BLE001 - the error itself cannot be read
        return -1, "unknown terminal error"


def _credentials(acc: accounts.Account) -> str | LoginResult:
    """The stored password, or why there is none. Never prompts: the bridge is a service with no one to ask."""
    if acc.login is None:
        return LoginResult(ok=False, reason=f"no login stored (goldbot-mt5 accounts add {acc.account_id})")
    return accounts.stored_password(acc) or LoginResult(
        ok=False, reason=f"no password stored (goldbot-mt5 accounts add {acc.account_id})")


def login_terminal(acc: accounts.Account, api: Any, settings: Mt5LoginSettings, *, attached: bool = False,
                   sleep: Callable[[float], None] = time.sleep,
                   dismiss: Callable[[], None] = dismiss_wizard) -> LoginResult:
    """Log the terminal in to `acc`'s configured account and check it is on exactly that account: up to
    `settings.attempts` tries, waiting the backoff between them, for timeouts only. `attached` (the bridge holding the
    IPC open) logs in on the session with mt5.login; otherwise mt5.initialize(path=..., login, password, server)
    attaches and logs in, and the caller shuts the IPC down."""
    password = _credentials(acc)
    if isinstance(password, LoginResult):
        return password
    result = LoginResult(ok=False, reason="not tried")
    for n in range(1, settings.attempts + 1):
        if settings.dismiss_wizard and not attached:
            try:
                dismiss()
            except Exception:                        # noqa: BLE001 - best effort only
                log.info("could not press Escape on the terminal window; logging in anyway")
        result = _login_once(acc, password, api, settings, attached=attached)
        if result.ok or not result.retry:
            break
        if n < settings.attempts:
            wait = _backoff(settings, n)
            log.warning("MT5 login for %s, try %d of %d: %s; next try in %.0f s", acc.account_id, n,
                        settings.attempts, result.reason, wait)
            sleep(wait)
    return result


def attach_terminal(acc: accounts.Account, api: Any, settings: Mt5LoginSettings, make_broker: Callable[[], T], *,
                    sleep: Callable[[float], None] = time.sleep,
                    dismiss: Callable[[], None] = dismiss_wizard) -> T:
    """The bridge's attach: the terminal's saved login first (`make_broker`, as before); when that fails and
    credentials are stored, log in from the keyring and attach again. Without credentials, or when the second attach
    fails, the error is raised as before (Restart=always tries again in 10 s; the login's own backoff paces that)."""
    try:
        return make_broker()
    except RuntimeError as first:
        if acc.login is None or not accounts.stored_password(acc):
            raise
        log.warning("MT5 attach for %s failed (%s); logging in from the keyring", acc.account_id, first)
    result = login_terminal(acc, api, settings, sleep=sleep, dismiss=dismiss)
    try:
        api.shutdown()                               # the login's own IPC; the broker opens its own below
    except Exception:                                # noqa: BLE001 - nothing to close
        pass
    log.log(logging.INFO if result.ok else logging.ERROR, "MT5 login for %s: %s: %s", acc.account_id,
            "ok" if result.ok else "failed", result.reason)
    return make_broker()


class Relogin:
    """GuardedBroker's `relogin` hook: one login try (mt5.login on the bridge's open session) per call when one is due,
    so a /health read never loops. After a failure the next try waits the backoff (doubling up to backoff_max_s);
    after `heal_max_attempts` failures in a row, or any failure a retry cannot fix (bad password, wrong account), it
    stops until the bridge restarts. A success resets the count."""

    def __init__(self, acc: accounts.Account, api: Any, settings: Mt5LoginSettings, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._acc, self._api, self._settings, self._clock = acc, api, settings, clock
        self._failed = 0
        self._next = float("-inf")
        self.stopped = False

    def __call__(self) -> bool:
        now = self._clock()
        if self.stopped or now < self._next:
            return False
        s = self._settings.model_copy(update={"attempts": 1})
        result = login_terminal(self._acc, self._api, s, attached=True, sleep=lambda _: None)
        if result.ok:
            self._failed, self._next = 0, now
            log.info("MT5 terminal for %s was not logged in: %s", self._acc.account_id, result.reason)
            return True
        self._failed += 1
        if not result.retry or self._failed >= self._settings.heal_max_attempts:
            self.stopped = True
            log.error("MT5 self-heal login for %s failed (%s); not trying again until the bridge restarts "
                      "(RUNBOOK 'Terminal down')", self._acc.account_id, result.reason)
            return False
        self._next = now + _backoff(self._settings, self._failed)
        log.warning("MT5 self-heal login for %s failed (%s); next try in %.0f s", self._acc.account_id,
                    result.reason, self._next - now)
        return False


def _api() -> Any:
    from goldbot.execution import mt5_adapter
    return mt5_adapter.mt5


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 1
    aid = args[0]
    try:
        acc = accounts.load_accounts()[aid]
    except KeyError:
        print(f"failed: {aid}: not in config/accounts.yaml")
        return 1
    api = _api()
    if api is None:
        print(f"failed: {aid}: the MetaTrader5 package is not installed here (run it on the MT5 box)")
        return 1
    settings = load_settings().mt5_login
    if "--dismiss-wizard" in argv:
        settings = settings.model_copy(update={"dismiss_wizard": True})
    try:
        result = login_terminal(acc, api, settings)
    finally:
        try:
            api.shutdown()                           # this command's IPC only; the terminal stays logged in
        except Exception:                            # noqa: BLE001 - nothing to close
            pass
    print(f"ok: {aid}: {result.reason}" if result.ok else f"failed: {aid}: {result.reason}")
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
