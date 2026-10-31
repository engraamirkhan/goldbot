"""MT5 terminal login from the keyring, without the owner (owner directive 2026-10-20: "MT5 terminal logins on the
boxes must happen without the owner (no VNC step)").

    goldbot-mt5 login <account> [--dismiss-wizard] [--retry-refused]   # on the MT5 box (python -m goldbot.ops.login)

One routine, `login_terminal`, used by the command and by the bridge (goldbot/ops/services/bridge.py):
* it logs in with the account's configured login (`mt5-login-<account>`), password (`mt5-<account>`) and server
  (config/accounts.yaml), read from the keyring, never prompted for; nothing else is ever logged in to;
* then it checks the terminal is on exactly that account (`accounts.verify_terminal_account`: login, server, demo vs
  real, hedging). A mismatch is failed and never retried; the bridge then refuses every call (wrong_account), as before;
* what was sent is kept across restarts (goldbot/ops/login_ledger.py; security review HIGH, trading-safety MEDIUM 1-2):
  a refused password (-6) is never sent again until the password or login is stored again (`goldbot-mt5 accounts set
  mt5-<account>`), a success, or `--retry-refused` (one deliberate try); other failures wait a doubling backoff from
  the end of the last login, and at most `max_attempts_per_hour` logins are sent per hour, whatever restarts the bridge;
* what it prints, logs and returns names the account id only: the login and the password are scrubbed from any terminal
  error text, and neither is ever formatted into a message.
Real-money accounts take the same path, for their own configured login only: this changes no authority (the phase gate,
`unlock_live` and RiskGate decide what is traded, not whether the terminal is logged in).

The bridge uses it twice: `attach_terminal` logs in when the attach fails (a terminal that lost its saved login sits at
the "Select a company to open an account with" wizard and answers initialize() without a login with IPC timeout -10005),
and `SelfHeal`, a background thread, logs in a terminal that answers but has no account. It is never run inside /health
or an RPC: it holds the bridge's terminal lock for one login (at most `heal_timeout_ms`, under the watchdog's 20 s
probe), and meanwhile /health answers not_logged_in with `login: in_progress` and RPCs get a prompt 503.
"""
from __future__ import annotations

import logging
import re
import sys
import threading
import time
from typing import Any, Callable, Literal, TypeVar

from goldbot.base import FrozenRecord
from goldbot.config import Mt5LoginSettings, load_settings
from goldbot.ops import accounts
from goldbot.ops.login_ledger import LoginLedger

log = logging.getLogger(__name__)

T = TypeVar("T")

AUTH_FAILED = -6          # RES_E_AUTH_FAILED: the broker refused the password (or login); never sent again by itself
# MetaTrader5 package error codes that a retry cannot fix: RES_E_INVALID_PARAMS, RES_E_AUTH_FAILED,
# RES_E_UNSUPPORTED. Everything else (-10005 IPC timeout, -10004 no IPC, -1 generic, ...) may pass on a later try.
PERMANENT_ERRORS = frozenset({-2, AUTH_FAILED, -7})
IPC_TIMEOUT = -10005
VK_ESCAPE = 0x1B
_LONG_DIGITS = re.compile(r"\d{5,}")
LoginStatus = Literal["ok", "failed", "refused", "rate_limited"]


class LoginResult(FrozenRecord):
    ok: bool
    reason: str = ""
    retry: bool = False         # worth another try (a timeout); never after a wrong account or a refused password
    status: LoginStatus = "failed"
    code: int | None = None     # the terminal's error code, when it gave one


def _scrub(text: str, acc: accounts.Account, password: str | None = None) -> str:
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


def refused_reason(acc: accounts.Account, mark: str) -> str:
    return (f"password refused by the broker ({mark}); nothing is sent until it is stored again: update it with "
            f"`goldbot-mt5 accounts set mt5-{acc.account_id}`")


def _gate(acc: accounts.Account, ledger: LoginLedger, settings: Mt5LoginSettings) -> LoginResult | None:
    """Why no login may be sent now (a standing refusal, the hourly cap), or None."""
    mark = ledger.refusal()
    if mark is not None:
        return LoginResult(ok=False, reason=refused_reason(acc, mark), status="refused")
    n = ledger.in_last_hour()
    if n >= settings.max_attempts_per_hour:
        return LoginResult(ok=False, status="rate_limited",
                           reason=f"{n} logins sent in the last hour (max {settings.max_attempts_per_hour}); "
                                  "the next is allowed when the oldest is an hour old")
    return None


def _send(acc: accounts.Account, password: str, api: Any, settings: Mt5LoginSettings, *,
          attached: bool) -> LoginResult:
    from goldbot.execution.mt5_adapter import MT5_INIT_TIMEOUT_MS, account_info_from_mt5
    assert acc.login is not None
    creds = {"login": int(acc.login), "password": password, "server": acc.server}
    try:
        done = (api.login(**creds, timeout=settings.heal_timeout_ms) if attached
                else api.initialize(path=acc.terminal_path, **creds, timeout=MT5_INIT_TIMEOUT_MS))
    except Exception as exc:                         # noqa: BLE001 - an IPC error: reported, scrubbed, retried
        return LoginResult(ok=False, reason=f"terminal raised {type(exc).__name__}: {_scrub(str(exc), acc, password)}",
                           retry=True)
    finally:
        creds.clear()
    if not done:
        code, msg = _last_error(api)
        what = "login" if attached else "initialize"
        if code == AUTH_FAILED:
            return LoginResult(ok=False, status="refused", code=code,
                               reason=f"{what} failed: ({code}, {_scrub(msg, acc, password)}): "
                                      + refused_reason(acc, f"code {code}"))
        hint = (f"; likely a wrong server name (configured: {acc.server}) or a missing broker servers.dat: check "
                f"servers.dat in the terminal's config dir (Config/servers.dat beside terminal64.exe)"
                if code == IPC_TIMEOUT else "")
        return LoginResult(ok=False, reason=f"{what} failed: ({code}, {_scrub(msg, acc, password)}){hint}", code=code,
                           retry=code not in PERMANENT_ERRORS)
    a = api.account_info()
    if a is None:
        return LoginResult(ok=False, reason="the terminal accepted the login but reports no account", retry=True)
    why = accounts.verify_terminal_account(acc, account_info_from_mt5(a))
    if why:
        return LoginResult(ok=False, reason=f"refused: {_scrub(why, acc, password)}")
    return LoginResult(ok=True, status="ok", reason=f"logged in ({acc.mode}, {acc.server})")


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
                   ledger: LoginLedger | None = None, pace_first: bool = True, retry_refused: bool = False,
                   sleep: Callable[[float], None] = time.sleep,
                   dismiss: Callable[[], None] = dismiss_wizard) -> LoginResult:
    """Log the terminal in to `acc`'s configured account and check it is on exactly that account: up to
    `settings.attempts` tries for timeouts only, each after the ledger's backoff (`pace_first=False`: the first try
    does not wait, for the owner's command). `attached` (the bridge holding the IPC open) logs in on the session with
    mt5.login; otherwise mt5.initialize(path=..., login, password, server) attaches and logs in, and the caller shuts
    the IPC down. `retry_refused`: the owner's deliberate single try past a refusal (`--retry-refused`)."""
    ledger = ledger or LoginLedger(acc.account_id)
    password = _credentials(acc)
    if isinstance(password, LoginResult):
        return password
    attempts = settings.attempts
    if retry_refused and ledger.refusal() is not None:
        ledger.clear_refusal()
        attempts = 1
    result = LoginResult(ok=False, reason="not tried")
    for n in range(1, attempts + 1):
        blocked = _gate(acc, ledger, settings)
        if blocked is not None:
            return blocked
        wait = ledger.wait_s(settings)
        if wait > 0 and (n > 1 or pace_first):
            log.info("MT5 login for %s: waiting %.0f s (backoff after failed logins)", acc.account_id, wait)
            sleep(wait)
        if settings.dismiss_wizard and not attached:
            try:
                dismiss()
            except Exception:                        # noqa: BLE001 - best effort only
                log.info("could not press Escape on the terminal window; logging in anyway")
        ledger.begin()
        result = _send(acc, password, api, settings, attached=attached)
        ledger.finish(result.ok)
        if result.ok:
            ledger.clear_refusal()
            break
        if result.code == AUTH_FAILED:
            ledger.mark_refused(AUTH_FAILED)
        if not result.retry:
            break
        log.warning("MT5 login for %s, try %d of %d: %s", acc.account_id, n, attempts, result.reason)
    return result


def attach_terminal(acc: accounts.Account, api: Any, settings: Mt5LoginSettings, make_broker: Callable[[], T], *,
                    ledger: LoginLedger | None = None, sleep: Callable[[float], None] = time.sleep,
                    dismiss: Callable[[], None] = dismiss_wizard) -> T:
    """The bridge's attach: the terminal's saved login first (`make_broker`, as before); when that fails and
    credentials are stored, log in from the keyring and attach again. Without credentials, or when the second attach
    fails, the error is raised as before (Restart=always tries again in 10 s); the ledger's refusal mark, backoff and
    hourly cap keep those restarts from resending credentials."""
    try:
        return make_broker()
    except RuntimeError as first:
        if acc.login is None or not accounts.stored_password(acc):
            raise
        log.warning("MT5 attach for %s failed (%s); logging in from the keyring", acc.account_id,
                    _scrub(str(first), acc))
    result = login_terminal(acc, api, settings, ledger=ledger, sleep=sleep, dismiss=dismiss)
    try:
        api.shutdown()                               # the login's own IPC; the broker opens its own below
    except Exception:                                # noqa: BLE001 - nothing to close
        pass
    log.log(logging.INFO if result.ok else logging.ERROR, "MT5 login for %s: %s: %s", acc.account_id,
            "ok" if result.ok else "failed", result.reason)
    return make_broker()


class SelfHeal:
    """The bridge's background login (trading-safety review MEDIUM 2: never inside /health or an RPC). Every
    `heal_interval_s` it takes the bridge's terminal lock (waiting for a running RPC), asks `needs_login` (the terminal
    answers but has no account: GuardedBroker.terminal_state() == "not_logged_in"; never wrong_account) and, when a
    login is allowed (no standing refusal, backoff over, under the hourly cap: all from the ledger, so a restart
    resets nothing), sends one mt5.login of at most `heal_timeout_ms`. `status()` is the fixed token /health shows."""

    def __init__(self, acc: accounts.Account, api: Any, settings: Mt5LoginSettings, lock: threading.Lock,
                 needs_login: Callable[[], bool], *, ledger: LoginLedger | None = None) -> None:
        self._acc, self._api, self._settings, self._lock = acc, api, settings, lock
        self._needs = needs_login
        self._ledger = ledger or LoginLedger(acc.account_id)
        self._busy = False
        self._stop = threading.Event()

    def status(self) -> str | None:
        """in_progress | refused | rate_limited | waiting, or None when no login is pending or held back."""
        if self._busy:
            return "in_progress"
        blocked = _gate(self._acc, self._ledger, self._settings)
        if blocked is not None:
            return blocked.status
        return "waiting" if self._ledger.wait_s(self._settings) > 0 else None

    def step(self) -> bool:
        """One look; True when it logged the terminal in."""
        if self.status() is not None:                # refused, capped or backing off: no lock taken, nothing sent
            return False
        with self._lock:
            try:
                needed = self._needs()
            except Exception:                        # noqa: BLE001 - state unreadable: not a reason to log in
                needed = False
            if not needed:
                return False
            self._busy = True
            try:
                result = login_terminal(self._acc, self._api, self._settings.model_copy(update={"attempts": 1}),
                                        attached=True, ledger=self._ledger, sleep=lambda _: None)
            finally:
                self._busy = False
        log.log(logging.INFO if result.ok else logging.ERROR, "MT5 self-heal login for %s: %s: %s",
                self._acc.account_id, "ok" if result.ok else "failed", result.reason)
        return result.ok

    def _run(self) -> None:  # pragma: no cover - the loop; step() is tested
        while not self._stop.wait(self._settings.heal_interval_s):
            try:
                self.step()
            except Exception:                        # noqa: BLE001 - the thread must outlive one bad look
                log.exception("MT5 self-heal step failed")

    def start(self) -> threading.Thread:  # pragma: no cover - thread start
        t = threading.Thread(target=self._run, name=f"mt5-self-heal-{self._acc.account_id}", daemon=True)
        t.start()
        return t


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
        result = login_terminal(acc, api, settings, pace_first=False, retry_refused="--retry-refused" in argv)
    finally:
        try:
            api.shutdown()                           # this command's IPC only; the terminal stays logged in
        except Exception:                            # noqa: BLE001 - nothing to close
            pass
    print(f"ok: {aid}: {result.reason}" if result.ok else f"failed: {aid}: {result.reason}")
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
