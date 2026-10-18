"""A declared account class (config/accounts.yaml `account_class`) is the baseline until a measurement is stored; a
measured class that disagrees never loosens the account (stricter wins) and health warns."""
import json
from pathlib import Path

import pandas as pd
import pytest

from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import Tick
from goldbot.execution.classifier import StoredClass
from goldbot.execution.paper import PaperBroker
from goldbot.ops import health
from goldbot.ops.accounts import Account, load_accounts
from goldbot.ops.smoke import _account_class
from goldbot.telegram.approvals import ApprovalCenter
from tests.test_health import engine as engine_file
from tests.test_health import make_ctx

T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")
TICK = Tick(ts_utc=T0, bid=2400.0, ask=2400.2)


def _engine(tmp_path: Path, declared: str | None) -> Engine:
    pb = PaperBroker(equity=10_000)
    pb.on_tick(TICK)
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode="demo", declared_class=declared,
                               state_dir=str(tmp_path)), pb, [], {}, ApprovalCenter({1}))


def _stored(tmp_path: Path, cls: str) -> None:
    (tmp_path / "classifier_icm-demo.json").write_text(json.dumps({"class": cls, "pending": None, "runs": []}))


def test_only_icm_demo_declares_a_raw_account_in_the_shipped_config():
    accounts = load_accounts()
    assert accounts["icm-demo"].account_class == "raw"
    assert [a for a, acc in accounts.items() if acc.account_class is not None] == ["icm-demo"]


def test_an_account_class_other_than_raw_or_standard_is_refused_at_load():
    with pytest.raises(ValueError):
        Account(account_id="x", broker="icm", mode="demo", server="s", login=None, terminal_path="", server_tz="Europe/Athens",
                symbol="XAUUSD", magic_base=1, enabled=True, account_class="unknown")  # type: ignore[arg-type]


def test_a_declared_raw_account_is_not_blocked_as_unknown_before_any_measurement(tmp_path):
    eng = _engine(tmp_path, "raw")
    eng._refresh_account(TICK)
    assert eng.state.account_class == "raw"                                  # no classifier file yet
    _stored(tmp_path, "unknown")
    eng._class_cache = (-2.0, StoredClass())
    eng._refresh_account(TICK)
    assert eng.state.account_class == "raw"


def test_an_undeclared_account_with_no_measurement_stays_unknown(tmp_path):
    eng = _engine(tmp_path, None)
    eng._refresh_account(TICK)
    assert eng.state.account_class == "unknown"


def test_standard_then_unknown_stays_standard_for_a_declared_raw_account(tmp_path):
    eng = _engine(tmp_path, "raw")
    (tmp_path / "classifier_icm-demo.json").write_text(
        json.dumps({"class": "unknown", "pending": None, "last_known": "standard", "runs": []}))
    eng._refresh_account(TICK)
    assert eng.state.account_class == "standard" and eng._measured_class() == "standard"


def test_a_torn_classifier_file_makes_a_declared_account_standard_and_health_warns(tmp_path):
    eng = _engine(tmp_path, "raw")
    (tmp_path / "classifier_icm-demo.json").write_text("{torn")
    eng._refresh_account(TICK)
    assert eng.state.account_class == "standard" and eng._measured_class() == "unreadable"
    ctx = make_ctx(tmp_path)
    engine_file(tmp_path, 60, mode="demo", account_class="standard", account_class_declared="raw",
                account_class_measured="unreadable")
    c = health.check_engine(ctx, "icm-demo")
    assert c.status == "warn" and "classifier file unreadable" in c.reason


def test_a_torn_classifier_file_on_an_undeclared_account_stays_unknown(tmp_path):
    eng = _engine(tmp_path, None)
    (tmp_path / "classifier_icm-demo.json").write_text("{torn")
    eng._refresh_account(TICK)
    assert eng.state.account_class == "unknown"


def test_a_measured_standard_overrides_a_declared_raw(tmp_path):
    eng = _engine(tmp_path, "raw")
    _stored(tmp_path, "standard")
    eng._refresh_account(TICK)
    assert eng.state.account_class == "standard"


def test_a_measured_raw_never_loosens_a_declared_standard(tmp_path):
    eng = _engine(tmp_path, "standard")
    _stored(tmp_path, "raw")
    eng._refresh_account(TICK)
    assert eng.state.account_class == "standard"


def test_the_smoke_check_reads_the_same_effective_class(tmp_path):
    acc = Account(account_id="icm-demo", broker="icm", mode="demo", server="s", login=None, terminal_path="",
                  server_tz="Europe/Athens", symbol="XAUUSD", magic_base=1, enabled=True, account_class="raw")
    assert _account_class(tmp_path, acc) == "raw"
    _stored(tmp_path, "standard")
    assert _account_class(tmp_path, acc) == "standard"
    assert _account_class(tmp_path, acc.model_copy(update={"account_class": None})) == "standard"
    (tmp_path / "classifier_icm-demo.json").unlink()
    assert _account_class(tmp_path, acc.model_copy(update={"account_class": None})) == "unknown"


def test_the_engine_publishes_declared_and_measured_classes(tmp_path):
    eng = _engine(tmp_path, "raw")
    eng._refresh_account(TICK)
    eng._write_state()
    published = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert (published["account_class"], published["account_class_declared"],
            published["account_class_measured"]) == ("raw", "raw", "unknown")


def test_health_warns_when_the_declared_and_measured_classes_disagree(tmp_path):
    ctx = make_ctx(tmp_path)
    engine_file(tmp_path, 60, mode="demo", account_class="standard", account_class_declared="raw",
                account_class_measured="standard")
    c = health.check_engine(ctx, "icm-demo")
    assert c.status == "warn" and "declared raw, measured standard: check the account type" in c.reason
    engine_file(tmp_path, 60, mode="demo", account_class="raw", account_class_declared="raw",
                account_class_measured="raw")
    assert health.check_engine(ctx, "icm-demo").status == "ok"
    engine_file(tmp_path, 60, mode="demo", account_class="raw", account_class_declared="raw",
                account_class_measured="unknown")
    assert health.check_engine(ctx, "icm-demo").status == "ok"
