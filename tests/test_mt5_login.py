"""MT5 terminal login from the keyring (owner directive 2026-10-20: terminal logins on the boxes happen without the
owner, no VNC step). `goldbot-mt5 login <account>` and the bridge's self-heal share one routine: log in with the
configured login, password and server, then check the terminal is on exactly that account (login, server, demo vs
real, hedging; accounts.verify_terminal_account) or report failed. A login or password never appears in what it
prints, logs or raises; every value here is made up."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from goldbot.config import Mt5LoginSettings, load_settings
from goldbot.ops import accounts
from goldbot.ops import login as mt5_login

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


@pytest.fixture
def stored(monkeypatch):
    secrets = {"mt5-icm-probe": PASSWORD}
    monkeypatch.setattr(accounts, "get_secret", lambda key: secrets.get(key))
    return secrets


FAST = Mt5LoginSettings(attempts=3, backoff_s=5, backoff_max_s=60, heal_max_attempts=3)


def _no_secret(text: str) -> None:
    assert PASSWORD not in text and str(LOGIN) not in text


# ---- the routine
def test_login_initializes_the_terminal_with_the_configured_login_password_and_server(stored):
    api = FakeMT5()
    r = mt5_login.login_terminal(_acc(), api, FAST, sleep=lambda s: None)
    assert r.ok
    name, kw = api.calls[0]
    assert name == "initialize"
    assert kw["path"] == TERMINAL and kw["login"] == LOGIN and kw["password"] == PASSWORD and kw["server"] == SERVER
    assert kw["timeout"] == FAST.timeout_ms


def test_an_attached_bridge_logs_in_on_its_open_session_instead_of_initializing_again(stored):
    api = FakeMT5()
    r = mt5_login.login_terminal(_acc(), api, FAST, attached=True, sleep=lambda s: None)
    assert r.ok and [c[0] for c in api.calls] == ["login"]
    assert api.calls[0][1]["login"] == LOGIN and api.calls[0][1]["server"] == SERVER


def test_a_terminal_that_ends_on_another_login_is_failed_and_never_retried(stored):
    api = FakeMT5(login=LOGIN + 1)
    r = mt5_login.login_terminal(_acc(), api, FAST, sleep=lambda s: None)
    assert not r.ok and not r.retry and "another account" in r.reason
    assert len(api.calls) == 1
    _no_secret(r.reason)


def test_a_real_terminal_under_a_demo_account_is_failed(stored):
    r = mt5_login.login_terminal(_acc("demo"), FakeMT5(trade_mode=2), FAST, sleep=lambda s: None)
    assert not r.ok and "real" in r.reason


def test_a_live_account_logs_in_only_to_its_own_real_login(stored):
    assert mt5_login.login_terminal(_acc("live"), FakeMT5(trade_mode=2), FAST, sleep=lambda s: None).ok
    r = mt5_login.login_terminal(_acc("live"), FakeMT5(trade_mode=0), FAST, sleep=lambda s: None)
    assert not r.ok and "demo" in r.reason


def test_a_netting_account_is_failed_as_the_engine_needs_hedging(stored):
    r = mt5_login.login_terminal(_acc(), FakeMT5(margin_mode=0), FAST, sleep=lambda s: None)
    assert not r.ok and "hedging" in r.reason


def test_no_stored_password_fails_without_prompting_or_touching_the_terminal(monkeypatch):
    monkeypatch.setattr(accounts, "get_secret", lambda key: None)
    monkeypatch.setattr(accounts, "_prompt", lambda *a, **k: pytest.fail("must never prompt"))
    api = FakeMT5()
    r = mt5_login.login_terminal(_acc(), api, FAST, sleep=lambda s: None)
    assert not r.ok and not r.retry and "no password stored" in r.reason and api.calls == []


def test_no_stored_login_fails_without_touching_the_terminal(stored):
    api = FakeMT5()
    r = mt5_login.login_terminal(_acc(login=None), api, FAST, sleep=lambda s: None)
    assert not r.ok and "no login stored" in r.reason and api.calls == []


def test_a_timeout_is_retried_with_growing_backoff_up_to_the_attempt_limit(stored):
    api = FakeMT5(fail=[(-10005, "IPC timeout")] * 5)
    waits: list[float] = []
    r = mt5_login.login_terminal(_acc(), api, FAST, sleep=waits.append)
    assert not r.ok and "-10005" in r.reason
    assert len(api.calls) == FAST.attempts == 3
    assert waits == [5, 10]                           # backoff between attempts, none after the last


def test_backoff_never_exceeds_its_ceiling(stored):
    s = Mt5LoginSettings(attempts=5, backoff_s=40, backoff_max_s=60)
    waits: list[float] = []
    mt5_login.login_terminal(_acc(), FakeMT5(fail=[(-10005, "IPC timeout")] * 9), s, sleep=waits.append)
    assert waits == [40, 60, 60, 60]


def test_a_timeout_then_success_logs_in(stored):
    api = FakeMT5(fail=[(-10005, "IPC timeout")])
    assert mt5_login.login_terminal(_acc(), api, FAST, sleep=lambda s: None).ok and len(api.calls) == 2


def test_an_authorization_failure_is_never_retried_so_the_broker_never_locks_the_account(stored):
    api = FakeMT5(fail=[(-6, "Terminal: Authorization failed")] * 3)
    r = mt5_login.login_terminal(_acc(), api, FAST, sleep=lambda s: pytest.fail("no retry"))
    assert not r.ok and not r.retry and len(api.calls) == 1


def test_a_terminal_error_naming_the_login_or_password_is_scrubbed(stored):
    api = FakeMT5(fail=[(-6, f"auth failed for {LOGIN} with {PASSWORD}")])
    r = mt5_login.login_terminal(_acc(), api, FAST, sleep=lambda s: None)
    _no_secret(r.reason)
    assert "-6" in r.reason


def test_the_wizard_is_dismissed_before_each_initialize_only_when_the_flag_is_on(stored):
    keys: list[str] = []
    on = Mt5LoginSettings(attempts=2, backoff_s=1, dismiss_wizard=True)
    mt5_login.login_terminal(_acc(), FakeMT5(fail=[(-10005, "IPC timeout")]), on, sleep=lambda s: None,
                             dismiss=lambda: keys.append("esc"))
    assert keys == ["esc", "esc"]
    keys.clear()
    mt5_login.login_terminal(_acc(), FakeMT5(), FAST, sleep=lambda s: None, dismiss=lambda: keys.append("esc"))
    assert keys == []


def test_a_failing_wizard_dismissal_does_not_stop_the_login(stored):
    def boom() -> None:
        raise OSError("no display")
    on = Mt5LoginSettings(dismiss_wizard=True)
    assert mt5_login.login_terminal(_acc(), FakeMT5(), on, sleep=lambda s: None, dismiss=boom).ok


# ---- the command
def test_the_command_prints_ok_and_shuts_the_ipc_down_without_the_login(stored, monkeypatch, capsys):
    api = FakeMT5()
    monkeypatch.setattr(mt5_login, "_api", lambda: api)
    monkeypatch.setattr(accounts, "load_accounts", lambda: {"icm-probe": _acc()})
    assert mt5_login.main(["icm-probe"]) == 0
    out = capsys.readouterr()
    assert out.out.startswith("ok: icm-probe") and api.shutdowns == 1
    _no_secret(out.out + out.err)


def test_the_command_prints_failed_with_the_reason_and_exits_non_zero(stored, monkeypatch, capsys):
    api = FakeMT5(fail=[(-6, "Authorization failed")])
    monkeypatch.setattr(mt5_login, "_api", lambda: api)
    monkeypatch.setattr(accounts, "load_accounts", lambda: {"icm-probe": _acc()})
    assert mt5_login.main(["icm-probe"]) == 1
    out = capsys.readouterr()
    assert out.out.startswith("failed: icm-probe:") and "-6" in out.out and api.shutdowns == 1
    _no_secret(out.out + out.err)


def test_the_command_refuses_an_unknown_account(monkeypatch, capsys):
    monkeypatch.setattr(accounts, "load_accounts", lambda: {"icm-probe": _acc()})
    assert mt5_login.main(["nope"]) == 1
    assert capsys.readouterr().out.startswith("failed: nope:")


def test_the_command_without_the_metatrader5_package_says_where_to_run_it(stored, monkeypatch, capsys):
    monkeypatch.setattr(mt5_login, "_api", lambda: None)
    monkeypatch.setattr(accounts, "load_accounts", lambda: {"icm-probe": _acc()})
    assert mt5_login.main(["icm-probe"]) == 1
    assert "MT5 box" in capsys.readouterr().out


# ---- bridge start-up: attach, and log in only when the attach fails
def test_the_bridge_attaches_without_a_login_when_the_terminal_answers(stored):
    api, made = FakeMT5(), list[int]()

    def make() -> str:
        made.append(1)
        return "broker"
    assert mt5_login.attach_terminal(_acc(), api, FAST, make, sleep=lambda s: None) == "broker"
    assert made == [1] and api.calls == []


def test_a_failed_attach_logs_in_from_the_keyring_then_attaches_again(stored):
    api, tries = FakeMT5(), []

    def make() -> str:
        tries.append(1)
        if len(tries) == 1:
            raise RuntimeError("mt5.initialize failed: (-10005, 'IPC timeout')")
        return "broker"
    assert mt5_login.attach_terminal(_acc(), api, FAST, make, sleep=lambda s: None) == "broker"
    assert [c[0] for c in api.calls] == ["initialize"] and api.shutdowns == 1 and len(tries) == 2


def test_a_failed_attach_without_stored_credentials_raises_as_before(monkeypatch):
    monkeypatch.setattr(accounts, "get_secret", lambda key: None)

    def make() -> str:
        raise RuntimeError("mt5.initialize failed: (-10005, 'IPC timeout')")
    with pytest.raises(RuntimeError, match="-10005"):
        mt5_login.attach_terminal(_acc(), FakeMT5(), FAST, make, sleep=lambda s: None)


# ---- bridge self-heal: the not_logged_in state, bounded
class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_self_heal_logs_in_once_then_waits_out_a_growing_backoff(stored):
    api, clock = FakeMT5(fail=[(-10005, "IPC timeout")] * 9), Clock()
    heal = mt5_login.Relogin(_acc(), api, FAST, clock=clock)
    assert heal() is False and len(api.calls) == 1
    assert heal() is False and len(api.calls) == 1          # not due: no tight loop on every /health
    clock.t = 5
    heal()
    assert len(api.calls) == 2
    clock.t = 14
    heal()
    assert len(api.calls) == 2                               # second wait is 10 s
    clock.t = 15
    heal()
    assert len(api.calls) == 3
    clock.t = 10_000
    assert heal() is False and len(api.calls) == 3           # heal_max_attempts reached: stops for this process


def test_self_heal_after_success_starts_counting_again(stored):
    api, clock = FakeMT5(), Clock()
    heal = mt5_login.Relogin(_acc(), api, FAST, clock=clock)
    assert heal() is True and [c[0] for c in api.calls] == ["login"]
    api.logged = False
    clock.t = 1
    assert heal() is True and len(api.calls) == 2


def test_self_heal_stops_for_good_on_an_authorization_failure(stored, caplog):
    api, clock = FakeMT5(fail=[(-6, f"Authorization failed {LOGIN}")] * 3), Clock()
    heal = mt5_login.Relogin(_acc(), api, FAST, clock=clock)
    with caplog.at_level(logging.INFO):
        heal()
        clock.t = 10_000
        heal()
    assert len(api.calls) == 1
    _no_secret(caplog.text)


def test_guarded_broker_heals_a_lost_login_and_reports_the_state_after(stored):
    from goldbot.execution.bridge import GuardedBroker
    from goldbot.execution.mt5_adapter import account_info_from_mt5

    api = FakeMT5()

    class Term:
        def terminal_info(self) -> Any:
            return SimpleNamespace(connected=True)

        def account(self) -> Any:
            a = api.account_info()
            if a is None:
                raise AttributeError("no account")
            return account_info_from_mt5(a)
    acc = _acc()
    g = GuardedBroker(Term(), magic_base=260100, verify=lambda info: accounts.verify_terminal_account(acc, info),
                      relogin=mt5_login.Relogin(acc, api, FAST, clock=Clock()))
    assert g.terminal_state() == "ok" and [c[0] for c in api.calls] == ["login"]


def test_guarded_broker_never_logs_in_over_a_wrong_account(stored):
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
    g = GuardedBroker(Term(), magic_base=260100, verify=lambda info: accounts.verify_terminal_account(acc, info),
                      relogin=mt5_login.Relogin(acc, api, FAST, clock=Clock()))
    assert g.terminal_state() == "wrong_account" and api.calls == []


def test_settings_carry_the_login_block_with_the_wizard_flag_off():
    s = load_settings().mt5_login
    assert s.attempts >= 1 and s.backoff_s > 0 and s.backoff_max_s >= s.backoff_s and s.dismiss_wizard is False
