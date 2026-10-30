"""Two accounts on one MT5 login (data-engineer finding 2026-10-30: icm-probe's terminal ran on icm-demo's login
10-19..10-25 and nothing caught it). Three guards, none of which ever prints, logs or stores a login:

* `load_accounts` refuses two ENABLED accounts that resolve to the same (login, server) from the keyring;
* the daily broker ledger keeps a one-way fingerprint of the terminal's (login, server) and the reconcile health
  check FAILs when another account's terminal reported the same one that day;
* the same check WARNs when two accounts' balance snapshots are identical for a day (rows from before the
  fingerprint, or a broker that hides the login)."""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest
import yaml

from goldbot.ops import accounts as acc_mod
from goldbot.ops import ledger
from goldbot.ops.accounts import Account
from goldbot.ops.health import HealthContext
from tests.test_accounts import isolated  # noqa: F401  (fixture)
from tests.test_broker_ledger import DAY1, MAGIC, SETTINGS, FakeBroker, _db, _health

FAKE = "48213977"                                      # a made-up login: it must appear in no error, log or row
OTHER = "48213978"


def _logins(monkeypatch: pytest.MonkeyPatch, table: dict[str, str]) -> None:
    monkeypatch.setattr(acc_mod, "get_secret", lambda key: table.get(key))


def test_two_enabled_accounts_resolving_to_one_login_and_server_are_refused_without_naming_the_login(
        isolated, monkeypatch, caplog):  # noqa: F811
    _logins(monkeypatch, {"mt5-login-icm-demo": FAKE, "mt5-login-icm-probe": FAKE})
    with pytest.raises(ValueError, match="icm-demo.*icm-probe|icm-probe.*icm-demo") as exc:
        acc_mod.load_accounts(isolated)
    assert FAKE not in str(exc.value) and FAKE not in caplog.text


def test_distinct_logins_a_disabled_twin_or_another_server_all_load(isolated, monkeypatch):  # noqa: F811
    _logins(monkeypatch, {"mt5-login-icm-demo": FAKE, "mt5-login-icm-probe": OTHER, "mt5-login-icm-live": FAKE})
    assert acc_mod.load_accounts(isolated)["icm-live"].login == int(FAKE)       # disabled: never trades
    raw = yaml.safe_load(isolated.read_text())
    raw["accounts"]["vantage-demo"]["server"] = "Other-Demo"
    isolated.write_text(yaml.safe_dump(raw))
    _logins(monkeypatch, {"mt5-login-icm-demo": FAKE, "mt5-login-vantage-demo": FAKE})
    assert acc_mod.load_accounts(isolated)["vantage-demo"].login == int(FAKE)    # same number, another server


def test_without_logins_nothing_is_read_and_nothing_is_compared(isolated, monkeypatch):  # noqa: F811
    _logins(monkeypatch, {"mt5-login-icm-demo": FAKE, "mt5-login-icm-probe": FAKE})
    assert all(a.login is None for a in acc_mod.load_accounts(isolated, logins=False).values())


def test_health_fails_the_accounts_check_when_two_enabled_accounts_share_a_login(
        isolated, monkeypatch, tmp_path):  # noqa: F811
    from goldbot.ops.checks.operations import check_accounts
    _logins(monkeypatch, {"mt5-login-icm-demo": FAKE, "mt5-login-icm-probe": FAKE})
    ctx = HealthContext.from_runtime(state_dir=tmp_path)
    c = check_accounts(ctx)
    assert c.status == "fail" and "same MT5 login" in c.reason and FAKE not in c.reason


# ------------------------------------------------------------------------------------------------ the ledger
def _acc(account_id: str, magic: int) -> Account:
    return Account(account_id=account_id, broker="icm", mode="demo", server="ICMarketsSC-Demo", login=None,
                   terminal_path="", server_tz="Europe/Athens", symbol="XAUUSD", magic_base=magic, enabled=True)


def _run(tmp_path, account: Account, broker: FakeBroker) -> ledger.LedgerReport:
    return ledger.run_ledger(_db(tmp_path), broker, account, tmp_path, DAY1, SETTINGS, clock=lambda: DAY1,
                             sleep=lambda s: None)


DEMO, PROBE = _acc("icm-demo", MAGIC), _acc("icm-probe", 260500)


def test_two_accounts_whose_terminals_report_one_login_on_a_day_fail_their_reconcile_check(tmp_path):
    _run(tmp_path, DEMO, FakeBroker(1000.0, [], login=int(FAKE)))
    _run(tmp_path, PROBE, FakeBroker(2500.0, [], login=int(FAKE)))
    for acc, other in ((DEMO, "icm-probe"), (PROBE, "icm-demo")):
        status, reason = ledger.ledger_status(_health(tmp_path), acc.account_id) or ("", "")
        assert status == "fail" and other in reason and "one MT5 login" in reason and FAKE not in reason
    with sqlite3.connect(tmp_path / "aux.db") as c:
        assert FAKE not in "\n".join(c.iterdump())                  # only a one-way fingerprint is kept


def test_distinct_terminals_with_distinct_balances_stay_ok(tmp_path):
    _run(tmp_path, DEMO, FakeBroker(1000.0, [], login=int(FAKE)))
    _run(tmp_path, PROBE, FakeBroker(2500.0, [], login=int(OTHER)))
    status, _ = ledger.ledger_status(_health(tmp_path), "icm-probe") or ("", "")
    assert status == "ok"


def test_two_accounts_with_identical_balance_snapshots_on_a_day_warn(tmp_path):
    _run(tmp_path, DEMO, FakeBroker(1234.56, [], equity=1230.01, login=int(FAKE)))
    _run(tmp_path, PROBE, FakeBroker(1234.56, [], equity=1230.01, login=int(OTHER)))
    status, reason = ledger.ledger_status(_health(tmp_path), "icm-probe") or ("", "")
    assert status == "warn" and "icm-demo" in reason and "identical" in reason


def test_a_snapshot_from_another_day_is_never_compared(tmp_path):
    _run(tmp_path, DEMO, FakeBroker(1000.0, [], login=int(FAKE)))
    ledger.run_ledger(_db(tmp_path), FakeBroker(1000.0, [], login=int(FAKE)), PROBE, tmp_path,
                      DAY1 + pd.Timedelta(days=1), SETTINGS, clock=lambda: DAY1 + pd.Timedelta(days=1),
                      sleep=lambda s: None)
    status, _ = ledger.ledger_status(_health(tmp_path, DAY1 + pd.Timedelta(days=1)), "icm-probe") or ("", "")
    assert status == "ok"
