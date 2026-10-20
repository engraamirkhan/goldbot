"""Only ENABLED accounts' engine state reaches the dashboard and health (owner bug 2026-10-20): a stale
state/engine_icm-probe.json left by a brief run of a since-disabled account showed "Entries blocked: Engine not
reporting (icm-probe)" and an extra Account tile. Every reader lists engine files through
`goldbot.ops.accounts.engine_state_files`, which keeps the accounts `enabled_accounts` returns (the source health's
`accounts` check uses); an enabled account whose report is missing or stale still shows "Engine not reporting"."""
import json
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api import activity, explain
from goldbot.api import entry_status as es
from goldbot.api.app import State, create_app
from goldbot.api.auth import totp_code
from goldbot.ops import accounts, health
from goldbot.ops.health import AccountRef, HealthContext
from goldbot.telegram.bus import ApprovalBus

WED = pd.Timestamp("2026-10-14 14:00", tz="UTC")     # gold trades
SAT = pd.Timestamp("2026-10-17 10:00", tz="UTC")     # weekend: market closed

YAML = """accounts:
  icm-demo:
    broker: icm
    mode: demo
    server: ICMarketsSC-Demo
    login: null
    terminal_path: C:\\MT5\\ICMarkets\\terminal64.exe
    server_tz: Europe/Athens
    symbol: XAUUSD
    magic_base: 260100
    enabled: true
  icm-probe:
    broker: icm
    mode: demo
    server: ICMarketsSC-Demo
    login: null
    terminal_path: C:\\MT5\\ICMarkets\\terminal64.exe
    server_tz: Europe/Athens
    symbol: XAUUSD
    magic_base: 260500
    enabled: false
"""


def engine(account: str, ts: float, **over) -> dict:
    e = {"account": account, "broker": "icm", "mode": "demo", "ts": ts, "equity": 1000.0, "balance": 1000.0,
         "day_start_equity": 1000.0, "week_start_equity": 1000.0, "balance_closed_hwm": 1000.0, "stage": "normal",
         "open_positions": 0, "account_class": "raw", "currency": "GBP", "spread_points": 20.0, "last_tick_age_s": 1.0,
         "tick_ahead_s": 0.0, "market_closed": False, "session_source": "broker", "tick_error": False,
         "dq_error": False, "stale_bars": False, "blackout": None, "server_clock": {"status": "ok", "reason": "fine"},
         "throttle": {"enabled": False}, "sessions": {"server_tz": "Europe/Athens", "source": "static"},
         "entry_blocks": [], "entry_refusals": {}, "approval_mode": "auto"}
    e.update(over)
    return e


@pytest.fixture
def yaml_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    f = tmp_path / "accounts.yaml"
    f.write_text(YAML)
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", f)
    return f


@pytest.fixture
def state(tmp_path: Path, yaml_file: Path) -> Path:
    """icm-demo enabled and reporting now; icm-probe disabled, its file left from a run a day ago."""
    sd = tmp_path / "state"
    sd.mkdir()
    (sd / "engine_icm-demo.json").write_text(json.dumps(engine("icm-demo", WED.timestamp())))
    (sd / "engine_icm-probe.json").write_text(json.dumps(engine("icm-probe", WED.timestamp() - 86400,
                                                                approval_mode="propose", mode="demo")))
    (sd / "supervisor.json").write_text(json.dumps({"ts": WED.timestamp() - 2}))
    return sd


def test_engine_state_files_lists_only_enabled_accounts(state):
    assert [f.name for f in accounts.engine_state_files(state)] == ["engine_icm-demo.json"]
    assert [f.name for f in accounts.engine_state_files(state, enabled={"icm-probe"})] == ["engine_icm-probe.json"]


def test_enabled_account_ids_follow_accounts_yaml_without_reading_the_keyring(yaml_file, monkeypatch):
    def no_keyring(key: str) -> str | None:
        raise AssertionError("the keyring was read")
    monkeypatch.setattr(accounts, "get_secret", no_keyring)
    assert accounts.enabled_account_ids() == frozenset({"icm-demo"})


def test_every_engine_file_is_listed_when_accounts_yaml_cannot_be_read(state, yaml_file):
    """Fail open: a config error must not hide a running engine from the risk view (health's `accounts` FAILs)."""
    yaml_file.write_text("accounts: [not, a, mapping")
    assert accounts.enabled_account_ids() is None
    assert [f.name for f in accounts.engine_state_files(state)] == ["engine_icm-demo.json", "engine_icm-probe.json"]


def test_a_disabled_accounts_leftover_engine_file_is_ignored_by_every_dashboard_reader(state):
    assert [e["account"] for e in State(state).engines()] == ["icm-demo"]
    assert list(activity.engines_by_account(state, None)) == ["icm-demo"]
    assert explain._engine_modes(state) == {"icm-demo": "auto"}
    rows = es.statuses_from_state(state, WED, None)
    assert [(a, s.state) for a, _, s in rows] == [("icm-demo", "open")]


def test_the_dashboard_accounts_and_banner_leave_out_a_disabled_account(state):
    (state / "engine_icm-demo.json").write_text(json.dumps(engine("icm-demo", time.time())))   # the API's clock
    (state / "supervisor.json").write_text(json.dumps({"ts": time.time()}))
    app = create_app(state, web_dist=state / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    uri = c.post("/api/auth/setup", json={"setup_code": app.state.st.auth.setup_code, "email": "o@x.io",
                                           "password": "owner password 123"}).json()["totp_uri"]
    tok = c.post("/api/auth/login", json={"email": "o@x.io", "password": "owner password 123",
                                          "totp": totp_code(uri.split("secret=")[1].split("&")[0])}).json()["token"]
    rows = c.get("/api/accounts", headers={"Authorization": f"Bearer {tok}"}).json()
    assert [r["account_id"] for r in rows] == ["icm-demo"]
    assert all(b["code"] != "engine_not_reporting" for r in rows for b in r["entry_status"]["blocks"])


def test_an_enabled_account_with_a_stale_report_still_shows_engine_not_reporting(state):
    (state / "engine_icm-demo.json").write_text(json.dumps(engine("icm-demo", WED.timestamp() - 86400)))
    [(acct, _, s)] = es.statuses_from_state(state, WED, None)
    assert acct == "icm-demo" and s.state == "blocked"
    assert [b.code for b in s.blocks] == ["engine_not_reporting"]


# ------------------------------------------------------------------------------------- health: entry_status:<account>
def ctx(sd: Path, now: pd.Timestamp = WED, ids: tuple[str, ...] = ("icm-demo",)) -> HealthContext:
    return HealthContext(state_dir=sd, now=now, accounts=[AccountRef(account_id=i) for i in ids],
                         get_secret=lambda k: None)


def test_health_entry_status_line_is_the_dashboards_status_for_each_enabled_account(state):
    c = health.check_entry_status(ctx(state), "icm-demo")
    [(_, _, s)] = es.statuses_from_state(state, WED, None, accounts=["icm-demo"])
    assert c.name == "entry_status:icm-demo" and c.status == "ok"
    assert c.reason == f"OPEN: {s.headline}"


def test_health_entry_status_fails_when_the_dashboard_shows_engine_not_reporting(state):
    (state / "engine_icm-demo.json").write_text(json.dumps(engine("icm-demo", WED.timestamp() - 86400)))
    c = health.check_entry_status(ctx(state), "icm-demo")
    [(_, _, s)] = es.statuses_from_state(state, WED, None)
    assert c.status == "fail"
    assert c.reason == "BLOCKED: " + "; ".join(b.text for b in s.blocks)
    assert "Engine not reporting" in c.reason


def test_health_entry_status_only_warns_for_the_owners_halt(state):
    ApprovalBus(state).set_halt(True, by="telegram:1")
    c = health.check_entry_status(ctx(state), "icm-demo")
    assert c.status == "warn" and c.reason.startswith("BLOCKED: New entries halted")


def test_health_entry_status_is_ok_while_the_market_is_closed(state):
    (state / "supervisor.json").write_text(json.dumps({"ts": SAT.timestamp() - 2}))
    (state / "engine_icm-demo.json").write_text(json.dumps(engine("icm-demo", SAT.timestamp(), market_closed=True,
                                                                          entry_blocks=["market_closed"])))
    c = health.check_entry_status(ctx(state, SAT), "icm-demo")
    assert c.status == "ok" and c.reason.startswith("WAITING")


def test_health_entry_status_warns_when_an_enabled_account_has_no_report(state):
    (state / "engine_icm-demo.json").unlink()
    c = health.check_entry_status(ctx(state), "icm-demo")
    assert c.status == "warn" and "no engine report" in c.reason


def test_health_runs_entry_status_for_enabled_accounts_only(state):
    names = [c.name for c in health.run_checks(ctx(state)).checks]
    assert "entry_status:icm-demo" in names
    assert not any("icm-probe" in n for n in names)


def test_the_status_reads_the_supervisor_heartbeat_at_the_same_instant_as_the_engine_report(state):
    """One clock for the whole status: health evaluates at its own `now`, the dashboard at the wall clock."""
    [(_, _, s)] = es.statuses_from_state(state, WED, None)
    assert s.state == "open"
    assert health.check_entry_status(ctx(state), "icm-demo").status == "ok"


def test_the_health_entry_status_check_writes_nothing(state):
    before = sorted(p.relative_to(state) for p in state.rglob("*"))
    health.check_entry_status(ctx(state), "icm-demo")
    assert sorted(p.relative_to(state) for p in state.rglob("*")) == before
