"""`goldbot run repair-lost-record` (trading-safety MEDIUM 2026-10-26): an exploration close whose record was given up
as lost holds its arm (no first trade; ADR 0043 budget) until the record is written. With the engine stopped, the
command writes the record from the broker's deals and drops the id from the engine's lost list; anything it cannot
repair (another ledger's record, no exit deal, deals unreadable) stays listed and is named."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import exploration
from goldbot.engine.store import JsonEngineStore
from goldbot.ops import engine_lock, repair_records
from tests.test_exploration_engine import (  # noqa: F401 - _rules: the test arms' rules (autouse)
    CLOSE,
    _at,
    _fresh_arm,
    _lost,
    _rules,
    _stopped_at_the_broker,
)

ACCOUNT = "icm-probe"


def _lost_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, int]:
    """An arm's position stopped out at the broker whose record was given up as lost, the engine's state saved."""
    eng, pb = _fresh_arm(tmp_path)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    pid = _stopped_at_the_broker(eng, pb)
    _lost(eng, monkeypatch)
    eng._save_orders()
    return eng, pb, pid


def _lost_ids(tmp_path: Path) -> list[int]:
    snap = JsonEngineStore(tmp_path, ACCOUNT).load_orders()
    assert snap is not None
    return snap.records_lost


def test_a_lost_exploration_record_is_written_from_the_brokers_deals_and_its_id_dropped(tmp_path, monkeypatch):
    eng, pb, pid = _lost_close(tmp_path, monkeypatch)
    [out] = repair_records.repair(tmp_path, ACCOUNT, pb)
    assert (out.position_id, out.outcome, out.arm_id) == (pid, "repaired", "a") and out.pnl is not None
    [rec] = exploration.trades(str(tmp_path), ACCOUNT)
    assert rec.exit_utc is not None and rec.exit_reason == "stop" and rec.pnl == pytest.approx(out.pnl)
    assert rec.censored is True                       # not the engine's own record: research never reads it as clean
    assert _lost_ids(tmp_path) == []
    eng2, _ = _fresh_arm(tmp_path, pb=pb)
    assert eng2.exploration is not None and not eng2._records_lost
    eng2._last_reset = None
    assert set(exploration._Pass(eng2, eng2.exploration, CLOSE).lost) == {"a"}      # from the ledger now
    assert not [d for d in eng2.decisions if d.get("action") == "exploration_blocked_lost_record"]


def test_a_dry_run_names_what_it_would_repair_and_writes_nothing(tmp_path, monkeypatch):
    _, pb, pid = _lost_close(tmp_path, monkeypatch)
    [out] = repair_records.repair(tmp_path, ACCOUNT, pb, dry_run=True)
    assert (out.position_id, out.outcome) == (pid, "repaired")
    [rec] = exploration.trades(str(tmp_path), ACCOUNT)
    assert rec.exit_utc is None and _lost_ids(tmp_path) == [pid]


def test_what_it_cannot_repair_stays_listed_and_is_named(tmp_path, monkeypatch):
    """Another ledger's lost record (a strategy's or a cost probe's) and a close with no exit deal at the broker."""
    eng, pb, pid = _lost_close(tmp_path, monkeypatch)
    eng._records_lost.append(987654)
    eng._save_orders()
    monkeypatch.setattr(pb, "deals_since", lambda since: pd.DataFrame(columns=["position_id", "type", "lots"]))
    out = {o.position_id: o.outcome for o in repair_records.repair(tmp_path, ACCOUNT, pb)}
    assert out == {pid: "no_exit_deal", 987654: "not_exploration"}
    assert _lost_ids(tmp_path) == [pid, 987654]

    def broken(since: pd.Timestamp) -> pd.DataFrame:
        raise ConnectionError("bridge down")

    monkeypatch.setattr(pb, "deals_since", broken)
    assert [o.outcome for o in repair_records.repair(tmp_path, ACCOUNT, pb, positions=[pid])] == ["deals_unreadable"]
    assert _lost_ids(tmp_path) == [pid, 987654]


def test_a_record_already_in_the_ledger_only_drops_the_id(tmp_path, monkeypatch):
    eng, pb, pid = _lost_close(tmp_path, monkeypatch)
    [c] = eng._throttle_lost
    exploration.record_close(eng, pid, c.trade, c.reason, c.price, c.at)
    [out] = repair_records.repair(tmp_path, ACCOUNT, pb)
    assert out.outcome == "already_recorded" and _lost_ids(tmp_path) == []


def test_the_command_refuses_while_the_engine_may_be_running(tmp_path, monkeypatch, capsys):
    """It rewrites the engine's orders file, which a running engine overwrites: the unit must be inactive."""
    monkeypatch.setattr("goldbot.ops.accounts.load_accounts",
                        lambda *a, **k: {ACCOUNT: SimpleNamespace(account_id=ACCOUNT, mode="demo")})
    monkeypatch.setattr("goldbot.ops.smoke.systemctl_state", lambda account_id: "active")
    assert repair_records.main([ACCOUNT, "--state", str(tmp_path)]) == repair_records.EXIT_REFUSED
    assert "stop the engine first" in capsys.readouterr().out


# ------------------------------------------------------------------ the engine and a repair never run at once
def _stopped_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("goldbot.ops.accounts.load_accounts",
                        lambda *a, **k: {ACCOUNT: SimpleNamespace(account_id=ACCOUNT, mode="demo")})
    monkeypatch.setattr("goldbot.ops.smoke.systemctl_state", lambda account_id: "inactive")
    monkeypatch.setattr("goldbot.ops.accounts.bridge_endpoint", lambda account_id: ("http://127.0.0.1:1", "t"))


def test_the_repair_is_refused_while_an_engine_of_the_account_holds_its_lock(tmp_path, monkeypatch, capsys):
    """Trading-safety LOW of e7fba842: the unit may read inactive while an engine process runs (started by hand, or
    starting); the engine's shared lock refuses the repair whatever systemd says."""
    _stopped_unit(monkeypatch)
    assert engine_lock.hold_engine(tmp_path, ACCOUNT) is None
    assert repair_records.main([ACCOUNT, "--state", str(tmp_path)]) == repair_records.EXIT_REFUSED
    assert "engine" in capsys.readouterr().out


def test_an_engine_start_is_refused_while_a_repair_holds_the_account(tmp_path, monkeypatch):
    from goldbot.ops import accounts
    from goldbot.ops import run as run_mod
    from tests.test_start_refusal import ACC
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(accounts, "load_accounts", lambda path=None: {"icm-demo": ACC})
    fd = engine_lock.take_repair(tmp_path / "state", "icm-demo")
    try:
        with pytest.raises(SystemExit, match="repair-lost-record"):
            run_mod.run_engine("icm-demo")
    finally:
        engine_lock.release(fd)


def test_the_repair_writes_nothing_when_the_engine_may_have_started_before_it_saves(tmp_path, monkeypatch):
    """The heartbeat is read again immediately before anything is written; any change aborts the whole repair."""
    _, pb, pid = _lost_close(tmp_path, monkeypatch)
    with pytest.raises(repair_records.EngineStarted, match="beat"):
        repair_records.repair(tmp_path, ACCOUNT, pb, check=lambda: "the engine loop beat 3 s ago")
    [rec] = exploration.trades(str(tmp_path), ACCOUNT)
    assert rec.exit_utc is None and _lost_ids(tmp_path) == [pid]
