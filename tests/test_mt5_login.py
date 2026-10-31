"""MT5 terminal login from the keyring (owner directive 2026-10-20: terminal logins on the boxes happen without the
owner, no VNC step). `goldbot-mt5 login <account>` and the bridge's self-heal share one routine: log in with the
configured login, password and server, then check the terminal is on exactly that account (login, server, demo vs
real, hedging; accounts.verify_terminal_account) or report failed. A login or password never appears in what it
prints, logs or raises; every value here is made up.

Security review (HIGH) and trading-safety review (MEDIUM 1-2, LOW 3) of 4278c39f: what was sent to the broker is kept
across restarts (goldbot/ops/login_ledger.py): a refused password is never sent again until it is stored again, the
backoff and an hourly cap survive a restart, and the bridge logs in from a background thread, never inside /health
or an RPC, so a slow login cannot block the watchdog's probe or hold a close past its age limit."""
from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from goldbot.config import Mt5LoginSettings, load_settings
from goldbot.ops import accounts, login_ledger
from goldbot.ops import login as mt5_login
from goldbot.ops.login_ledger import LoginLedger

LOGIN = 51234567                       # made up
PASSWORD = "Made-Up-Pw-9f3k"           # made up
SERVER = "ICMarketsSC-Demo"
TERMINAL = r"C:\MT5\ICMarkets\terminal64.exe"


def _acc(mode: str = "demo", login: int | None = LOGIN) -> accounts.Account:
    return accounts.Account(account_id="icm-probe", broker="icm", mode=mode, server=SERVER,  # type: ignore[arg-type]
                            login=login, terminal_path=TERMINAL, server_tz="Europe/Athens", symbol="XAUUSD",
                            magic_base=260100, enabled=True)


class FakeMT5:
    """The MetaTrader5 package surface the login uses. `on` is the account the terminal ends on after a login."""

    def __init__(self, *, fail: list[tuple[int, str]] | None = None, login: int = LOGIN, trade_mode: int = 0,
                 margin_mode: int = 2, server: str = SERVER) -> None:
        self.fail = list(fail or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.logged = False
        self.err: tuple[int, str] = (1, "Success")
        self.acct = SimpleNamespace(login=login, equity=1000.0, balance=1000.0, margin=0.0, margin_free=1000.0,
                                    leverage=30, currency="GBP", server=server, trade_mode=trade_mode,
                                    margin_mode=margin_mode)
        self.shutdowns = 0

    def _do(self, name: str, kw: dict[str, Any]) -> bool:
        self.calls.append((name, kw))
        if self.fail:
            self.err = self.fail.pop(0)
            return False
        self.logged = True
        return True

    def initialize(self, **kw: Any) -> bool:
        return self._do("initialize", kw)

    def login(self, *args: Any, **kw: Any) -> bool:
        assert not args, "credentials are passed by keyword"
        return self._do("login", kw)

    def last_error(self) -> tuple[int, str]:
        return self.err

    def account_info(self) -> Any:
        return self.acct if self.logged else None

    def shutdown(self) -> None:
        self.shutdowns += 1


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def led(tmp_path, monkeypatch, clock) -> LoginLedger:
    monkeypatch.setattr(login_ledger, "DEFAULT_DIR", tmp_path / "mt5-login")
    return LoginLedger("icm-probe", tmp_path / "mt5-login", clock=clock)


@pytest.fixture
def stored(monkeypatch, led):
    secrets = {"mt5-icm-probe": PASSWORD}
    monkeypatch.setattr(accounts, "get_secret", lambda key: secrets.get(key))
    return secrets


FAST = Mt5LoginSettings(attempts=3, backoff_s=5, backoff_max_s=60, max_attempts_per_hour=10)


def _no_secret(text: str) -> None:
    assert PASSWORD not in text and str(LOGIN) not in text


def _recording(waits: list[float], clock: Clock) -> Any:
    def sleep(s: float) -> None:
        waits.append(s)
        clock.sleep(s)
    return sleep


def _login(api: FakeMT5, led: LoginLedger, clock: Clock, s: Mt5LoginSettings = FAST, acc: Any = None,
           **kw: Any) -> mt5_login.LoginResult:
    return mt5_login.login_terminal(acc or _acc(), api, s, ledger=led, sleep=clock.sleep, **kw)


# ---- the routine
def test_login_initializes_the_terminal_with_the_configured_login_password_and_server(stored, led, clock):
    api = FakeMT5()
    assert _login(api, led, clock).ok
    name, kw = api.calls[0]
    assert name == "initialize"
    assert kw["path"] == TERMINAL and kw["login"] == LOGIN and kw["password"] == PASSWORD and kw["server"] == SERVER


def test_an_attached_bridge_logs_in_on_its_open_session_within_the_watchdog_probe_timeout(stored, led, clock):
    api = FakeMT5()
    assert _login(api, led, clock, attached=True).ok and [c[0] for c in api.calls] == ["login"]
    assert api.calls[0][1]["login"] == LOGIN and api.calls[0][1]["server"] == SERVER
    assert api.calls[0][1]["timeout"] == FAST.heal_timeout_ms < 20_000      # the watchdog's probe timeout is 20 s


def test_a_terminal_that_ends_on_another_login_is_failed_and_never_retried(stored, led, clock):
    api = FakeMT5(login=LOGIN + 1)
    r = _login(api, led, clock)
    assert not r.ok and not r.retry and "another account" in r.reason and len(api.calls) == 1
    _no_secret(r.reason)


def test_a_real_terminal_under_a_demo_account_is_failed(stored, led, clock):
    r = _login(FakeMT5(trade_mode=2), led, clock)
    assert not r.ok and "real" in r.reason


def test_a_live_account_logs_in_only_to_its_own_real_login(stored, led, clock):
    assert _login(FakeMT5(trade_mode=2), led, clock, acc=_acc("live")).ok
    r = _login(FakeMT5(trade_mode=0), led, clock, acc=_acc("live"))
    assert not r.ok and "demo" in r.reason


def test_a_netting_account_is_failed_as_the_engine_needs_hedging(stored, led, clock):
    r = _login(FakeMT5(margin_mode=0), led, clock)
    assert not r.ok and "hedging" in r.reason


def test_no_stored_password_fails_without_prompting_or_touching_the_terminal(monkeypatch, led, clock):
    monkeypatch.setattr(accounts, "get_secret", lambda key: None)
    monkeypatch.setattr(accounts, "_prompt", lambda *a, **k: pytest.fail("must never prompt"))
    api = FakeMT5()
    r = _login(api, led, clock)
    assert not r.ok and not r.retry and "no password stored" in r.reason and api.calls == []


def test_no_stored_login_fails_without_touching_the_terminal(stored, led, clock):
    api = FakeMT5()
    r = _login(api, led, clock, acc=_acc(login=None))
    assert not r.ok and "no login stored" in r.reason and api.calls == []


def test_a_timeout_is_retried_with_growing_backoff_up_to_the_attempt_limit(stored, led, clock):
    api, waits = FakeMT5(fail=[(-10005, "IPC timeout")] * 5), list[float]()
    r = mt5_login.login_terminal(_acc(), api, FAST, ledger=led, sleep=_recording(waits, clock))
    assert not r.ok and "-10005" in r.reason and len(api.calls) == FAST.attempts == 3
    assert waits == [5, 10]


def test_backoff_never_exceeds_its_ceiling():
    s = Mt5LoginSettings(backoff_s=40, backoff_max_s=60)
    assert [login_ledger.backoff_s(s, n) for n in range(5)] == [0, 40, 60, 60, 60]


def test_a_timeout_then_success_logs_in(stored, led, clock):
    api = FakeMT5(fail=[(-10005, "IPC timeout")])
    assert _login(api, led, clock).ok and len(api.calls) == 2


def test_a_terminal_error_naming_the_login_or_password_is_scrubbed(stored, led, clock):
    r = _login(FakeMT5(fail=[(-10005, f"timeout for {LOGIN} with {PASSWORD}")] * 3), led, clock)
    _no_secret(r.reason)
    assert "-10005" in r.reason


def test_the_wizard_is_dismissed_before_each_initialize_only_when_the_flag_is_on(stored, led, clock):
    keys: list[str] = []
    on = Mt5LoginSettings(attempts=2, backoff_s=1, dismiss_wizard=True)
    _login(FakeMT5(fail=[(-10005, "IPC timeout")]), led, clock, s=on, dismiss=lambda: keys.append("esc"))
    assert keys == ["esc", "esc"]
    keys.clear()
    _login(FakeMT5(), led, clock, dismiss=lambda: keys.append("esc"))
    assert keys == []


def test_a_failing_wizard_dismissal_does_not_stop_the_login(stored, led, clock):
    def boom() -> None:
        raise OSError("no display")
    assert _login(FakeMT5(), led, clock, s=Mt5LoginSettings(dismiss_wizard=True), dismiss=boom).ok


# ---- a refused password is never sent again, across restarts (security HIGH, trading-safety MEDIUM 1)
@pytest.mark.parametrize("mode,trade_mode", [("demo", 0), ("live", 2)])
def test_a_refused_password_is_marked_and_never_sent_again_after_a_restart(stored, led, clock, mode, trade_mode):
    api = FakeMT5(fail=[(-6, "Authorization failed")], trade_mode=trade_mode)
    r = _login(api, led, clock, acc=_acc(mode))
    assert not r.ok and not r.retry and len(api.calls) == 1
    clock.t += 7 * 24 * 3600                                  # any number of restarts later: a new ledger, same files
    again = FakeMT5(trade_mode=trade_mode)
    r2 = mt5_login.login_terminal(_acc(mode), again, FAST, ledger=LoginLedger("icm-probe", led._dir, clock=clock),
                                  sleep=clock.sleep)
    assert not r2.ok and r2.status == "refused" and again.calls == []
    assert "goldbot-mt5 accounts set mt5-icm-probe" in r2.reason


def test_the_refusal_mark_holds_a_time_and_code_only(stored, led, clock):
    _login(FakeMT5(fail=[(-6, f"Authorization failed {LOGIN} {PASSWORD}")]), led, clock)
    text = "".join(p.read_text() for p in led._dir.iterdir())
    _no_secret(text)
    assert "code -6" in text


@pytest.mark.parametrize("key", ["mt5-icm-probe", "mt5-login-icm-probe"])
def test_storing_the_password_or_login_again_clears_the_refusal(stored, led, clock, monkeypatch, key):
    _login(FakeMT5(fail=[(-6, "Authorization failed")]), led, clock)
    assert led.refusal()
    monkeypatch.setattr(accounts, "secret_store", lambda: "keyring")
    monkeypatch.setattr(accounts, "keyring", SimpleNamespace(set_password=lambda *a: None))
    accounts.set_secret(key, "made-up-new-value")
    assert led.refusal() is None and led.attempts() == []


def test_storing_a_bridge_token_leaves_the_refusal(stored, led, clock, monkeypatch):
    _login(FakeMT5(fail=[(-6, "Authorization failed")]), led, clock)
    monkeypatch.setattr(accounts, "secret_store", lambda: "keyring")
    monkeypatch.setattr(accounts, "keyring", SimpleNamespace(set_password=lambda *a: None))
    accounts.set_secret("mt5-bridge-token-icm-probe", "t" * 40)
    assert led.refusal()


def test_a_successful_login_clears_an_old_refusal(stored, led, clock):
    led.mark_refused(-6)
    assert _login(FakeMT5(), led, clock, retry_refused=True).ok
    assert led.refusal() is None


# ---- backoff and the hourly cap survive a restart (trading-safety MEDIUM 2)
def test_the_backoff_after_a_failure_is_waited_out_by_a_restarted_process(stored, led, clock):
    _login(FakeMT5(fail=[(-10005, "IPC timeout")] * 9), led, clock, s=Mt5LoginSettings(attempts=1, backoff_s=30))
    waits: list[float] = []
    api = FakeMT5()
    mt5_login.login_terminal(_acc(), api, Mt5LoginSettings(attempts=1, backoff_s=30),
                             ledger=LoginLedger("icm-probe", led._dir, clock=clock),
                             sleep=_recording(waits, clock))
    assert waits == [30] and len(api.calls) == 1


def test_the_hourly_cap_holds_across_restarts(stored, led, clock):
    s = Mt5LoginSettings(attempts=1, backoff_s=1, backoff_max_s=1, max_attempts_per_hour=3)
    for _ in range(3):
        _login(FakeMT5(fail=[(-10005, "IPC timeout")]), led, clock, s=s)
        clock.t += 60
    api = FakeMT5()
    r = mt5_login.login_terminal(_acc(), api, s, ledger=LoginLedger("icm-probe", led._dir, clock=clock),
                                 sleep=clock.sleep)
    assert r.status == "rate_limited" and api.calls == []
    clock.t += 3600
    assert _login(FakeMT5(), led, clock, s=s).ok


def test_the_attempt_is_timed_when_the_login_returns_not_when_it_starts(stored, led, clock):
    class Slow(FakeMT5):
        def login(self, *args: Any, **kw: Any) -> bool:
            clock.t += 15                                     # the login takes 15 s
            return super().login(*args, **kw)
    start = clock.t
    _login(Slow(fail=[(-10005, "IPC timeout")]), led, clock, s=Mt5LoginSettings(attempts=1, backoff_s=20),
           attached=True)
    assert led.attempts()[-1][0] == start + 15
    assert led.wait_s(Mt5LoginSettings(backoff_s=20)) == 20


# ---- the command
def _command(monkeypatch, api: Any) -> None:
    monkeypatch.setattr(mt5_login, "_api", lambda: api)
    monkeypatch.setattr(accounts, "load_accounts", lambda: {"icm-probe": _acc()})


def test_the_command_prints_ok_and_shuts_the_ipc_down_without_the_login(stored, monkeypatch, capsys):
    api = FakeMT5()
    _command(monkeypatch, api)
    assert mt5_login.main(["icm-probe"]) == 0
    out = capsys.readouterr()
    assert out.out.startswith("ok: icm-probe") and api.shutdowns == 1
    _no_secret(out.out + out.err)


def test_the_command_prints_failed_with_the_reason_and_exits_non_zero(stored, monkeypatch, capsys):
    api = FakeMT5(fail=[(-6, "Authorization failed")])
    _command(monkeypatch, api)
    assert mt5_login.main(["icm-probe"]) == 1
    out = capsys.readouterr()
    assert out.out.startswith("failed: icm-probe:") and "refused" in out.out and api.shutdowns == 1
    _no_secret(out.out + out.err)


def test_the_command_sends_nothing_while_the_password_stands_refused(stored, led, monkeypatch, capsys):
    led.mark_refused(-6)
    api = FakeMT5()
    _command(monkeypatch, api)
    assert mt5_login.main(["icm-probe"]) == 1 and api.calls == []
    assert "goldbot-mt5 accounts set mt5-icm-probe" in capsys.readouterr().out


def test_the_command_retry_refused_clears_the_mark_and_tries_exactly_once(stored, led, monkeypatch, capsys):
    led.mark_refused(-6)
    api = FakeMT5(fail=[(-6, "Authorization failed")] * 3)
    _command(monkeypatch, api)
    assert mt5_login.main(["icm-probe", "--retry-refused"]) == 1 and len(api.calls) == 1
    assert led.refusal()                                      # refused again: marked again
    api2 = FakeMT5()
    _command(monkeypatch, api2)
    led.mark_refused(-6)
    assert mt5_login.main(["icm-probe", "--retry-refused"]) == 0 and led.refusal() is None


def test_the_command_refuses_an_unknown_account(monkeypatch, capsys):
    monkeypatch.setattr(accounts, "load_accounts", lambda: {"icm-probe": _acc()})
    assert mt5_login.main(["nope"]) == 1
    assert capsys.readouterr().out.startswith("failed: nope:")


def test_the_command_without_the_metatrader5_package_says_where_to_run_it(stored, monkeypatch, capsys):
    _command(monkeypatch, None)
    assert mt5_login.main(["icm-probe"]) == 1
    assert "MT5 box" in capsys.readouterr().out


# ---- bridge start-up: attach, and log in only when the attach fails
def test_the_bridge_attaches_without_a_login_when_the_terminal_answers(stored, led, clock):
    api, made = FakeMT5(), list[int]()

    def make() -> str:
        made.append(1)
        return "broker"
    assert mt5_login.attach_terminal(_acc(), api, FAST, make, ledger=led, sleep=clock.sleep) == "broker"
    assert made == [1] and api.calls == []


def test_a_failed_attach_logs_in_from_the_keyring_then_attaches_again(stored, led, clock, caplog):
    api, tries = FakeMT5(), []

    def make() -> str:
        tries.append(1)
        if len(tries) == 1:
            raise RuntimeError(f"mt5.initialize failed: (-10005, 'IPC timeout {LOGIN}')")
        return "broker"
    with caplog.at_level(logging.INFO):
        assert mt5_login.attach_terminal(_acc(), api, FAST, make, ledger=led, sleep=clock.sleep) == "broker"
    assert [c[0] for c in api.calls] == ["initialize"] and api.shutdowns == 1 and len(tries) == 2
    _no_secret(caplog.text)


def test_a_failed_attach_without_stored_credentials_raises_as_before(monkeypatch, led, clock):
    monkeypatch.setattr(accounts, "get_secret", lambda key: None)

    def make() -> str:
        raise RuntimeError("mt5.initialize failed: (-10005, 'IPC timeout')")
    with pytest.raises(RuntimeError, match="-10005"):
        mt5_login.attach_terminal(_acc(), FakeMT5(), FAST, make, ledger=led, sleep=clock.sleep)


def test_a_restart_loop_after_a_refused_password_sends_it_once(stored, led, clock):
    api = FakeMT5(fail=[(-6, "Authorization failed")] * 99)

    def make() -> str:
        raise RuntimeError("mt5.initialize failed: (-10005, 'IPC timeout')")
    for _ in range(50):                                       # systemd: Restart=always, RestartSec=10
        with pytest.raises(RuntimeError):
            mt5_login.attach_terminal(_acc(), api, FAST, make, ledger=LoginLedger("icm-probe", led._dir, clock=clock),
                                      sleep=clock.sleep)
        clock.t += 10
    assert len(api.calls) == 1


# ---- bridge self-heal: a background thread, never inside /health or an RPC
def _heal(api: FakeMT5, led: LoginLedger, clock: Clock, needs: Any = lambda: True,
          s: Mt5LoginSettings = FAST) -> mt5_login.SelfHeal:
    return mt5_login.SelfHeal(_acc(), api, s, threading.Lock(), needs, ledger=led)


def test_self_heal_logs_in_when_the_terminal_has_no_account(stored, led, clock):
    api = FakeMT5()
    assert _heal(api, led, clock).step() is True and [c[0] for c in api.calls] == ["login"]


def test_self_heal_does_nothing_while_the_terminal_is_logged_in(stored, led, clock):
    api = FakeMT5()
    assert _heal(api, led, clock, needs=lambda: False).step() is False and api.calls == []


def test_self_heal_waits_out_the_persisted_backoff(stored, led, clock):
    api = FakeMT5(fail=[(-10005, "IPC timeout")] * 9)
    h = _heal(api, led, clock)
    h.step()
    h.step()
    assert len(api.calls) == 1 and h.status() == "waiting"
    clock.t += 5
    h.step()
    assert len(api.calls) == 2
    h2 = _heal(api, LoginLedger("icm-probe", led._dir, clock=clock), clock)    # the bridge restarted
    h2.step()
    assert len(api.calls) == 2                                # still inside the 10 s wait


def test_self_heal_reports_a_refused_password_and_sends_nothing(stored, led, clock):
    led.mark_refused(-6)
    api = FakeMT5()
    h = _heal(api, led, clock)
    assert h.step() is False and api.calls == [] and h.status() == "refused"


def test_self_heal_never_logs_in_over_a_wrong_account(stored, led, clock):
    from goldbot.execution.bridge import GuardedBroker
    from goldbot.execution.mt5_adapter import account_info_from_mt5

    api = FakeMT5(login=LOGIN + 1)
    api.logged = True

    class Term:
        def terminal_info(self) -> Any:
            return SimpleNamespace(connected=True)

        def account(self) -> Any:
            return account_info_from_mt5(api.account_info())
    acc = _acc()
    g = GuardedBroker(Term(), magic_base=260100, verify=lambda info: accounts.verify_terminal_account(acc, info))
    h = _heal(api, led, clock, needs=lambda: g.terminal_state() == "not_logged_in")
    assert h.step() is False and api.calls == []


def test_a_slow_login_never_blocks_health_past_the_probe_timeout(stored, led, clock):
    """The heal holds the terminal lock for its whole login; /health must still answer well inside the watchdog's
    20 s probe timeout, as not_logged_in with the login in progress, and a close must get a prompt refusal (the
    engine resends it) rather than wait past its 10 s age limit."""
    from http.server import HTTPServer

    import requests

    from goldbot.execution.bridge import LOCK_WAIT_S, make_handler

    lock, release = threading.Lock(), threading.Event()

    class Busy:
        def status(self) -> str | None:
            return "in_progress"

    class Term:
        server_tz = "Europe/Athens"

        def terminal_state(self) -> str:
            return "not_logged_in"

        def close(self, *a: Any) -> Any:
            pytest.fail("no call while the login holds the terminal")
    lock.acquire()                                            # a login is running
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(Term(), "t" * 40, lock=lock, login_status=Busy().status))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        t0 = time.monotonic()
        r = requests.get(f"{url}/health", timeout=10)
        assert time.monotonic() - t0 < LOCK_WAIT_S + 2 < 20
        assert r.status_code == 503 and r.json()["terminal"] == "not_logged_in" and r.json()["login"] == "in_progress"
        t0 = time.monotonic()
        r = requests.post(f"{url}/rpc", json={"method": "close", "args": [], "sent_at": time.time()},
                          headers={"Authorization": "Bearer " + "t" * 40}, timeout=10)
        assert time.monotonic() - t0 < LOCK_WAIT_S + 2 < 10
        assert r.status_code == 503 and "login in progress" in r.json()["error"]
    finally:
        release.set()
        lock.release()
        httpd.shutdown()
        httpd.server_close()


def test_health_reports_a_refused_login_with_a_fixed_token(stored, led, clock):
    from http.server import HTTPServer

    import requests

    from goldbot.execution.bridge import make_handler

    class Term:
        def terminal_state(self) -> str:
            return "not_logged_in"
    led.mark_refused(-6)
    h = _heal(FakeMT5(), led, clock)
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(Term(), "t" * 40, lock=threading.Lock(), login_status=h.status))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        body = requests.get(f"http://127.0.0.1:{httpd.server_address[1]}/health", timeout=10).json()
        assert body["terminal"] == "not_logged_in" and body["login"] == "refused"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_settings_carry_the_login_block_with_the_wizard_flag_off():
    s = load_settings().mt5_login
    assert s.attempts >= 1 and s.backoff_s > 0 and s.backoff_max_s >= s.backoff_s and s.dismiss_wizard is False
    assert s.heal_timeout_ms < 20_000 and s.max_attempts_per_hour >= 1


def test_an_ipc_timeout_names_the_server_and_the_servers_dat_check(stored, led, clock):
    """-10005 on a second terminal was a missing broker servers.dat (2026-10-30): say so, name the server."""
    acc = _acc()
    r = _login(FakeMT5(fail=[(-10005, "IPC timeout")] * 3), led, clock, acc=acc)
    assert "-10005" in r.reason and acc.server in r.reason
    assert "servers.dat" in r.reason and "config" in r.reason.lower() and "server name" in r.reason
    _no_secret(r.reason)
