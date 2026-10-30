"""`autodisc-register` (ADR 0058 phase 4): a ledger survivor becomes a demo exploration arm inside the account's caps,
journalled (spec hash, ledger K, honesty results) before any file changes, never an order.

Every file is a temp copy: the real config/accounts.yaml (no keyring read), an empty spec arms file, a real-mode ledger
built here. Nothing is market data."""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
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
    subprocess.run(["git", "init", "-q", "-b", "feature/autodisc-arm", str(tmp_path)], check=True)
    ledger = SearchLedger(tmp_path / "discovery.db", mode="real")
    for i in range(len(FILTERS)):
        ledger.log_spec(spec(i), "G1", "1")
        ledger.record_result(spec_hash(spec(i)), GOOD)
        ledger.log_spec(spec(i), "G1", "1", null=True)
        ledger.record_result(spec_hash(spec(i)), {"null_fitness": [0.5, 1.1, None, 0.9]}, null=True)
    return {"ledger_path": tmp_path / "discovery.db", "accounts_path": tmp_path / "accounts.yaml",
            "spec_arms_path": tmp_path / "spec_arms.yaml", "root": tmp_path, "now": NOW,
            "stage": "explore"}                     # explore-only survivors register behind the explicit flag


def register(env: dict[str, Any], i: int = 0, account: str = "icm-demo", **kw: Any) -> R.Registration:
    return R.register(spec_hash(spec(i)), account, "prereg.md#d1", **{**env, **kw})


def test_a_survivor_is_journalled_with_its_ledger_k_and_honesty_results_then_runs_on_the_demo_account(env):
    reg = register(env)
    ledger = SearchLedger(env["ledger_path"])
    assert reg.arm_id == "D2610-1" and reg.stage == "explore_survivor" and reg.requested_stage == "explore"
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


# ---------------------------------------------------------------- review findings (trading-safety, code review)
def tampered_ids(env: dict[str, Any]) -> tuple[str, str]:
    """(arm id in spec_arms.yaml?, in accounts?) as strings, for a crash-replay read."""
    reg = R.journal(env["ledger_path"])[0]
    in_spec = reg.arm_id in {a.id for a in load_arms(env["spec_arms_path"])}
    in_acc = reg.arm_id in A.load_accounts(env["accounts_path"], logins=False)[reg.account_id].exploration.arms
    return str(in_spec), str(in_acc)


@pytest.mark.parametrize("fail_on,expect", [("spec_arms.yaml", ("False", "False")), ("accounts.yaml", ("True", "False"))])
def test_a_crash_after_the_journal_row_is_completed_by_rerunning_without_a_new_row_or_weekly_slot(
        env, monkeypatch, fail_on, expect):
    real = R._write_atomic

    def flaky(path: Path, text: str) -> None:
        if Path(path).name == fail_on:
            raise OSError("killed")
        real(path, text)
    monkeypatch.setattr(R, "_write_atomic", flaky)
    with pytest.raises(OSError):
        register(env)
    assert tampered_ids(env) == expect and len(R.journal(env["ledger_path"])) == 1
    monkeypatch.setattr(R, "_write_atomic", real)
    again = register(env)
    assert again == R.journal(env["ledger_path"])[0] and tampered_ids(env) == ("True", "True")
    assert len(R.journal(env["ledger_path"])) == 1                      # no new row, no second weekly slot
    assert [register(env, i).arm_id for i in (1, 2, 3)] == ["D2610-2", "D2610-3", "D2610-4"]      # 3 slots left
    with pytest.raises(R.RegistrationRefused, match="registered already"):
        register(env)                                                    # both files have it: now refused
    [a] = [x for x in load_arms(env["spec_arms_path"]) if x.id == again.arm_id]                    # hash-checked
    assert a.config_hash == again.config_hash


def test_the_weekly_and_duplicate_checks_are_recounted_inside_the_journal_transaction(env):
    regs = [register(env, i) for i in range(4)]
    stale = regs[0].model_copy(update={"arm_id": "D2610-9", "spec_hash": "e" * 64})
    with pytest.raises(R.RegistrationRefused, match="this week"):         # a plan that read the journal before the others
        R._append(env["ledger_path"], stale)
    dup = regs[0].model_copy(update={"arm_id": "D2611-1", "written_utc": (NOW + pd.Timedelta(days=7)).isoformat()})
    with pytest.raises(R.RegistrationRefused, match="registered already"):
        R._append(env["ledger_path"], dup)
    taken = dup.model_copy(update={"spec_hash": "f" * 64, "arm_id": regs[1].arm_id})
    with pytest.raises(R.RegistrationRefused, match="arm id"):
        R._append(env["ledger_path"], taken)
    assert len(R.journal(env["ledger_path"])) == 4


@pytest.mark.parametrize("branch", ["main", "master", None])
def test_registration_refuses_unless_the_checkout_is_on_a_branch_other_than_main(env, branch):
    root = env["root"]
    if branch is None:
        subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "x"], check=True)
        subprocess.run(["git", "-C", str(root), "checkout", "-q", "--detach"], check=True)
    else:
        subprocess.run(["git", "-C", str(root), "checkout", "-q", "-B", branch], check=True)
    before = env["accounts_path"].read_text()
    with pytest.raises(R.RegistrationRefused, match="branch"):
        register(env)
    assert R.journal(env["ledger_path"]) == [] and env["accounts_path"].read_text() == before
    R.plan(spec_hash(spec(0)), "icm-demo", "prereg.md#d1", **env)         # a dry run is harmless anywhere


def test_registration_refuses_outside_a_git_checkout(env, tmp_path):
    shutil.rmtree(env["root"] / ".git")
    with pytest.raises(R.RegistrationRefused, match="branch"):
        register(env)


def test_the_per_trade_cap_is_rounded_to_pence_before_the_ceiling_check_and_the_journal(env):
    reg = register(env, max_loss=6.123456789)
    assert reg.max_loss == 6.12
    assert "max_loss: 6.12 }" in env["accounts_path"].read_text()
    assert A.load_accounts(env["accounts_path"], logins=False)["icm-demo"].exploration.arms[reg.arm_id].max_loss == 6.12


def test_the_digest_covers_the_row_columns_and_a_head_anchor_detects_a_deleted_tail(env):
    for i in range(3):
        register(env, i)
    head = R.verify(env["ledger_path"])
    assert head.rows == 3 and head.digest == R.verify(env["ledger_path"], head=head.digest).digest
    con = sqlite3.connect(env["ledger_path"])
    con.execute("UPDATE arm_registrations SET written_utc = '2020-01-01T00:00:00+00:00' WHERE seq = 2")
    con.commit()
    con.close()
    with pytest.raises(R.RegistrationRefused, match="altered"):
        R.journal(env["ledger_path"])                                     # a column outside the payload is covered


def test_deleting_the_tail_row_is_detected_against_the_head_anchor(env):
    for i in range(3):
        register(env, i)
    anchor = R.verify(env["ledger_path"]).digest
    con = sqlite3.connect(env["ledger_path"])
    con.execute("DELETE FROM arm_registrations WHERE seq = 3")
    con.commit()
    con.close()
    assert len(R.journal(env["ledger_path"])) == 2                        # the chain alone cannot see it
    with pytest.raises(R.RegistrationRefused, match=f"expected head {anchor}"):
        R.verify(env["ledger_path"], head=anchor)


@pytest.mark.parametrize("exc", [sqlite3.IntegrityError("UNIQUE constraint failed: arm_registrations.arm_id"),
                                 OSError("disk full")])
def test_the_command_exits_2_with_one_line_on_a_database_or_file_error(env, monkeypatch, capsys, exc):
    def boom(*a: Any, **k: Any) -> None:
        raise exc
    monkeypatch.setattr(R, "register", boom)
    rc = R.main([spec_hash(spec(0)), "--account", "icm-demo", "--prereg", "prereg.md#d1",
                 "--ledger", str(env["ledger_path"])])
    err = capsys.readouterr().err.strip().splitlines()
    assert rc == 2 and len(err) == 1 and str(exc) in err[0]


def test_the_verify_command_prints_the_head_and_refuses_a_wrong_anchor(env, capsys):
    register(env)
    assert R.main(["--verify", "--ledger", str(env["ledger_path"])]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] == 1 and out["head"] == R.verify(env["ledger_path"]).digest
    assert R.main(["--verify", "--head", "0" * 64, "--ledger", str(env["ledger_path"])]) == 2
    assert "expected head" in capsys.readouterr().err


def test_a_prereg_path_outside_the_repo_is_refused(env, tmp_path):
    outside = tmp_path.parent / "elsewhere.md"
    outside.write_text("## d1\n")
    for bad in (str(outside) + "#d1", "../elsewhere.md#d1"):
        with pytest.raises(R.RegistrationRefused, match="under the repo"):
            R.register(spec_hash(spec(0)), "icm-demo", bad, **env)


# ---------------------------------------------------------------- phase 3: the stage a registration rests on
def _stage(env: dict[str, Any], i: int, stage: str, outcome: dict[str, Any] | None) -> None:
    from goldbot.research.autodisc.stages import StageJournal
    j = StageJournal(env["ledger_path"])
    j.open(stage, spec_hash(spec(i)), {})                                 # type: ignore[arg-type]
    if outcome is not None:
        j.close(stage, spec_hash(spec(i)), outcome)                       # type: ignore[arg-type]


def _confirm(env: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in env.items() if k != "stage"}               # no --stage flag: the default


def test_an_explore_only_survivor_is_refused_without_the_explicit_explore_stage(env, capsys):
    before = env["spec_arms_path"].read_text()
    with pytest.raises(R.RegistrationRefused, match="--stage explore"):
        R.register(spec_hash(spec(0)), "icm-demo", "prereg.md#d1", **_confirm(env))
    assert R.journal(env["ledger_path"]) == [] and env["spec_arms_path"].read_text() == before
    rc = R.main([spec_hash(spec(0)), "--account", "icm-demo", "--prereg", "prereg.md#d1", "--dry-run",
                 "--ledger", str(env["ledger_path"])])
    assert rc == 2 and "confirm passer" in capsys.readouterr().err
    R.main([spec_hash(spec(0)), "--account", "icm-demo", "--prereg", "prereg.md#d1", "--dry-run",
            "--ledger", str(env["ledger_path"]), "--stage", "explore"])      # (the repo has no prereg.md: refused there)
    assert "confirm passer" not in capsys.readouterr().err                  # the flag passes the stage check


@pytest.mark.parametrize("reads,stage", [
    ([("confirm", {"passed": True})], "confirm_passer"),
    ([("confirm", {"passed": True}), ("holdout", {"passed": True})], "holdout_passer"),
])
def test_a_confirm_or_holdout_passer_registers_by_default_and_the_journal_records_its_stage(env, reads, stage):
    for st, out in reads:
        _stage(env, 0, st, out)
    reg = R.register(spec_hash(spec(0)), "icm-demo", "prereg.md#d1", **_confirm(env))
    assert reg.stage == stage and reg.requested_stage == "confirm"
    assert R.journal(env["ledger_path"])[0].stage == stage


@pytest.mark.parametrize("reads", [
    [("confirm", {"passed": False})],
    [("confirm", {"passed": False, "void": "crash"})],
    [("confirm", None)],                                                   # opened, never closed: void
    [("confirm", {"passed": True}), ("holdout", {"passed": False})],     # the one-way stop
])
def test_a_spec_that_failed_or_voided_a_stage_read_never_registers_even_with_the_explore_flag(env, reads):
    for st, out in reads:
        _stage(env, 0, st, out)
    with pytest.raises(R.RegistrationRefused, match="final"):
        register(env)
    assert R.journal(env["ledger_path"]) == []


def test_the_default_ledger_is_the_nightly_searchs_real_ledger():
    from goldbot.research.autodisc.nightly import LEDGER_FILE
    assert R.LEDGER_PATH.name == LEDGER_FILE["real"]


def test_a_journal_row_written_before_phase_3_still_reads():
    old = {"arm_id": "D2610-1", "account_id": "icm-demo", "spec_hash": "a" * 64, "timeframe": "15m",
           "style": "intraday", "max_loss": 5.0, "prereg": "p.md#x", "stage": "explore_survivor", "k_search": 1,
           "k_null": 1, "ledger_seq": 1, "ledger_digest": "0" * 64, "config_hash": "c", "written_utc": NOW.isoformat(),
           "honesty": {"n_events": 1, "mean_r": 0.1, "fitness": 1.0, "excess_r": 0.1, "stress_mean_r": 0.1,
                       "floors": True, "null_threshold": 0.5, "null_reps": 2}}
    assert R.Registration.model_validate(old).requested_stage is None
