"""`autodisc-register` (ADR 0058 phase 4): a ledger survivor becomes a demo exploration arm inside the account's caps,
journalled (spec hash, ledger K, honesty results) before any file changes, never an order.

Every file is a temp copy: the real config/accounts.yaml (no keyring read), an empty spec arms file, a real-mode ledger
built here. Nothing is market data."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import exploration as X
from goldbot.ops import accounts as A
from goldbot.ops import autodisc_register as R
from goldbot.research.arms import load_arms
from goldbot.research.autodisc.gen_combine import family_spec
from goldbot.research.autodisc.ledger import LedgerError, SearchLedger
from goldbot.research.autodisc.spec import ExitTemplate, Leaf, StrategySpec, spec_hash

ROOT = Path(__file__).resolve().parents[1]
NOW = pd.Timestamp("2026-10-29 12:00", tz="UTC")
T2R = ExitTemplate(stop="atr_1.0", target="2R")
FILTERS = [None, *(Leaf(kind="time", name=d) for d in ("mon", "tue", "wed", "thu", "fri"))]
GOOD = {"n_events": 420, "mean_r": 0.08, "fitness": 2.4, "excess_r": 0.05, "stress_mean_r": 0.03, "floors": True}


def spec(i: int = 0) -> StrategySpec:
    return family_spec("box_break_close", "15m", FILTERS[i], T2R)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Any]:
    shutil.copy(ROOT / "config" / "accounts.yaml", tmp_path / "accounts.yaml")
    shutil.copy(ROOT / "config" / "spec_arms.yaml", tmp_path / "spec_arms.yaml")
    (tmp_path / "prereg.md").write_text("# forward read\n## d1\nthe reading rule\n")
    ledger = SearchLedger(tmp_path / "discovery.db", mode="real")
    for i in range(len(FILTERS)):
        ledger.log_spec(spec(i), "G1", "1")
        ledger.record_result(spec_hash(spec(i)), GOOD)
        ledger.log_spec(spec(i), "G1", "1", null=True)
        ledger.record_result(spec_hash(spec(i)), {"null_fitness": [0.5, 1.1, None, 0.9]}, null=True)
    return {"ledger_path": tmp_path / "discovery.db", "accounts_path": tmp_path / "accounts.yaml",
            "spec_arms_path": tmp_path / "spec_arms.yaml", "root": tmp_path, "now": NOW}


def register(env: dict[str, Any], i: int = 0, account: str = "icm-demo", **kw: Any) -> R.Registration:
    return R.register(spec_hash(spec(i)), account, "prereg.md#d1", **{**env, **kw})


def test_a_survivor_is_journalled_with_its_ledger_k_and_honesty_results_then_runs_on_the_demo_account(env):
    reg = register(env)
    ledger = SearchLedger(env["ledger_path"])
    assert reg.arm_id == "D2610-1" and reg.stage == "explore_survivor"
    assert (reg.k_search, reg.k_null) == (ledger.k_search(), ledger.k_null()) == (6, 6)
    assert (reg.ledger_seq, reg.ledger_digest) == ledger.head()
    assert reg.honesty.fitness == 2.4 and reg.honesty.null_threshold == 1.1 and reg.honesty.failures == []
    assert R.journal(env["ledger_path"]) == [reg]
    [arm] = load_arms(env["spec_arms_path"])                                   # hash-checked
    assert arm.id == reg.arm_id and arm.rule == "spec_rule" and arm.params["spec_hash"] == spec_hash(spec(0))
    acc = A.load_accounts(env["accounts_path"], logins=False)["icm-demo"]
    intraday = [a.max_loss for a in acc.exploration.arms.values() if a.style == "intraday" and a.control_share is None
                and a is not acc.exploration.arms[reg.arm_id]]
    assert acc.exploration.arms[reg.arm_id].max_loss == reg.max_loss == min(intraday)
    cfg = X.config_for(acc, X.SPEC_PATH, env["spec_arms_path"])
    assert cfg is not None and reg.arm_id in {a.arm_id for a in cfg.arms}
    # the rest of the account is byte for byte what it was, plus one line
    before = (ROOT / "config" / "accounts.yaml").read_text().splitlines()
    after = env["accounts_path"].read_text().splitlines()
    assert len(after) == len(before) + 1 and [x for x in after if x not in before] == [
        f"        {reg.arm_id}: {{ style: intraday, max_loss: {reg.max_loss:g} }}  # ADR 0058 spec_rule, spec "
        f"{reg.spec_hash[:12]}"]


def test_nothing_is_written_when_the_journal_row_cannot_be_written(env, monkeypatch):
    spec_before, acc_before = env["spec_arms_path"].read_text(), env["accounts_path"].read_text()

    def boom(*a: Any) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(R, "_append", boom)
    with pytest.raises(OSError):
        register(env)
    assert env["spec_arms_path"].read_text() == spec_before and env["accounts_path"].read_text() == acc_before


def test_a_dry_run_checks_everything_and_journals_and_writes_nothing(env):
    spec_before, acc_before = env["spec_arms_path"].read_text(), env["accounts_path"].read_text()
    reg, new_spec, new_accounts = R.plan(spec_hash(spec(0)), "icm-demo", "prereg.md#d1", **env)
    assert reg.arm_id in new_spec and reg.arm_id in new_accounts
    assert R.journal(env["ledger_path"]) == []
    assert env["spec_arms_path"].read_text() == spec_before and env["accounts_path"].read_text() == acc_before


def test_the_command_exits_2_on_a_refusal_and_writes_nothing(env, capsys):
    rc = R.main(["f" * 64, "--account", "icm-demo", "--prereg", "prereg.md#d1", "--dry-run",
                 "--ledger", str(env["ledger_path"])])
    assert rc == 2 and "refused" in capsys.readouterr().err
    assert R.journal(env["ledger_path"]) == []


@pytest.mark.parametrize("why", ["below_null", "no_result", "no_null", "unknown", "floors", "stress"])
def test_a_spec_that_is_not_an_explore_survivor_is_refused(env, tmp_path, why):
    h = spec_hash(spec(0))
    if why in ("below_null", "floors", "stress", "no_result", "no_null"):
        p = tmp_path / "other.db"
        led = SearchLedger(p, mode="real")
        led.log_spec(spec(0), "G1", "1")
        if why != "no_result":
            worse: dict[str, dict[str, Any]] = {"below_null": {"fitness": 1.0}, "floors": {"floors": False},
                                                "stress": {"stress_mean_r": -0.01}}
            res = {**GOOD, **worse.get(why, {})}
            led.record_result(h, res)
        if why != "no_null":
            led.log_spec(spec(0), "G1", "1", null=True)
            led.record_result(h, {"null_fitness": [0.5, 1.1, 0.2, 0.9]}, null=True)
        env = {**env, "ledger_path": p}
    else:
        h = "f" * 64
    with pytest.raises(R.RegistrationRefused):
        R.register(h, "icm-demo", "prereg.md#d1", **env)
    assert R.journal(env["ledger_path"]) == [] and load_arms(env["spec_arms_path"]) == []


def test_a_synthetic_ledger_never_registers_an_arm(env, tmp_path):
    led = SearchLedger(tmp_path / "synthetic.db", mode="synthetic")
    led.log_spec(spec(0), "G1", "1")
    with pytest.raises(LedgerError):
        R.register(spec_hash(spec(0)), "icm-demo", "prereg.md#d1", **{**env, "ledger_path": tmp_path / "synthetic.db"})


@pytest.mark.parametrize("account", ["icm-live", "vantage-live", "icm-depth", "no-such-account"])
def test_only_a_demo_probe_only_exploring_account_takes_a_spec_arm(env, account):
    with pytest.raises(R.RegistrationRefused):
        register(env, account=account)
    assert R.journal(env["ledger_path"]) == []


def test_the_per_trade_cap_stays_inside_the_accounts_caps_and_the_room_floor_ceiling(env):
    acc = A.load_accounts(env["accounts_path"], logins=False)["icm-demo"]
    pot = acc.exploration.pots["intraday"]
    peers = [a.max_loss for a in acc.exploration.arms.values() if a.style == "intraday" and a.control_share is None]
    ceiling = min(max(peers), X.SHARE_CAP * pot.gbp)
    with pytest.raises(R.RegistrationRefused, match="caps"):
        register(env, max_loss=ceiling + 0.5)
    assert register(env, max_loss=ceiling).max_loss == ceiling


def test_at_most_four_registrations_a_week_and_never_the_same_spec_twice(env):
    ids = [register(env, i).arm_id for i in range(4)]
    assert ids == ["D2610-1", "D2610-2", "D2610-3", "D2610-4"]
    with pytest.raises(R.RegistrationRefused, match="registered already"):
        register(env, 0, now=NOW + pd.Timedelta(days=7))
    with pytest.raises(R.RegistrationRefused, match="this week"):
        register(env, 4)
    assert register(env, 4, now=NOW + pd.Timedelta(days=7)).arm_id == "D2611-1"


def test_a_spec_the_engine_cannot_warm_up_or_without_a_written_prereg_is_refused(env):
    slow = family_spec("tsmom", "1d", Leaf(kind="regime", name="hurst_trend"), T2R)   # 1000 1d bars of Hurst warm-up
    led = SearchLedger(env["ledger_path"], mode="real")
    led.log_spec(slow, "G1", "1")
    led.record_result(spec_hash(slow), GOOD)
    with pytest.raises(R.RegistrationRefused, match="warm-up"):
        R.register(spec_hash(slow), "icm-demo", "prereg.md#d1", **env)
    for prereg in ("missing.md#d1", "prereg.md"):
        with pytest.raises(R.RegistrationRefused, match="prereg"):
            R.register(spec_hash(spec(0)), "icm-demo", prereg, **env)


def test_an_altered_journal_is_refused(env):
    import sqlite3
    register(env)
    con = sqlite3.connect(env["ledger_path"])
    con.execute("UPDATE arm_registrations SET payload = replace(payload, 'icm-demo', 'icm-probe')")
    con.commit()
    con.close()
    with pytest.raises(R.RegistrationRefused, match="altered"):
        R.journal(env["ledger_path"])
