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
from goldbot.ops import repair_records
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
