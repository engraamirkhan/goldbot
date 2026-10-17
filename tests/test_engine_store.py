"""engine/store.py (ADR 0023 step 1): the engine's state files behind `EngineStore`. The engine-level contract (what is
saved, what a restart rebuilds) is pinned by tests/test_engine_store_golden.py; these tests hold the store's own
interface: None only for a confirmed-absent file, an unreadable one raises the parse's own error, the snapshots are
built from the parsed values (S1), the engine file is not fsynced (S3) and the protocol is what both implementations
must satisfy (S6)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot import base
from goldbot.engine import Engine, EngineConfig, runner, store
from goldbot.engine.store import (
    EngineStore,
    JsonEngineStore,
    OpenTrade,
    OrdersSnapshot,
    PendingClose,
    RiskSnapshot,
    SentOrder,
)
from goldbot.execution.paper import PaperBroker
from goldbot.risk.gate import Stage
from goldbot.telegram.approvals import ApprovalCenter

ACCOUNT = "icm-demo"
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")


def _risk() -> RiskSnapshot:
    return RiskSnapshot(last_reset=T0, day_start_equity=9_900.0, week_start_equity=10_100.0,
                        balance_closed_hwm=10_500.0, stage=Stage.HALTED, rearm_seen="rearm-1",
                        weekend_done="2025-02-28", halted_at=T0 - pd.Timedelta(days=1),
                        propose_only_until=None, rearm_refused="not yet", kill_everything=True,
                        stage2_pending_since=T0 - pd.Timedelta(minutes=5))


def _orders() -> OrdersSnapshot:
    row = SentOrder(client_order_id="a", ts_utc=T0, agent_id="g", side=1, magic=1, lots=0.1, max_bars=8, sl=1.0,
                    tp=2.0, status="filled", position_id=5)
    tr = OpenTrade(position_id=5, agent_id="g", side=1, lots=0.1, entry_bar_ts=T0, max_bars=8)
    return OrdersSnapshot(sent={"a": row}, open={5: tr},
                          closing=[PendingClose(position_id=6, trade=tr.model_copy(update={"position_id": 6}),
                                                reason="time", at=T0)],
                          records_lost=[7])


def test_the_json_store_is_an_engine_store():
    """S6: the protocol is the contract every implementation (JSON today, core.db in W2-1) is checked against."""
    s: EngineStore = JsonEngineStore(Path("state"), ACCOUNT)
    assert isinstance(s, EngineStore)


def test_the_runner_re_exports_the_moved_records():
    """Move-only: `from goldbot.engine.runner import OpenTrade` keeps working for every existing caller."""
    assert (runner.OpenTrade, runner.PendingClose, runner.SentOrder) == (OpenTrade, PendingClose, SentOrder)


def test_absent_files_load_as_confirmed_absent(tmp_path):
    """"None = confirmed absent": no file is a first start, not an error."""
    s = JsonEngineStore(tmp_path, ACCOUNT)
    assert s.load_risk() is None and s.load_orders() is None


def test_risk_and_orders_round_trip_to_the_same_snapshot(tmp_path):
    s = JsonEngineStore(tmp_path, ACCOUNT)
    s.save_risk(_risk())
    s.save_orders(_orders())
    assert s.load_risk() == _risk()
    got = s.load_orders()
    assert got is not None
    assert got.model_dump(mode="json") == _orders().model_dump(mode="json")
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"orders_{ACCOUNT}.json", f"risk_{ACCOUNT}.json"]


def test_a_risk_file_with_a_key_this_release_does_not_know_loads_without_a_skew_note(tmp_path, monkeypatch):
    """S1: the snapshot is built from the parsed values, never by validating the file's dict (FrozenRecord forbids
    extra keys); the parse ignores unknown keys, as it did in the runner (the skew note is BACKLOG 74, not this move)."""
    from collections import Counter
    monkeypatch.setattr(base, "_skew", Counter())
    (tmp_path / f"risk_{ACCOUNT}.json").write_text(json.dumps({"stage": "halted", "from_a_newer_release": 1}))
    got = JsonEngineStore(tmp_path, ACCOUNT).load_risk()
    assert got is not None and got.stage == Stage.HALTED and not base._skew


def test_an_older_risk_file_takes_the_parse_defaults(tmp_path):
    """S2, word for word: kill_everything False, stage2_pending_since None, stage NORMAL, equities 0.0."""
    (tmp_path / f"risk_{ACCOUNT}.json").write_text("{}")
    got = JsonEngineStore(tmp_path, ACCOUNT).load_risk()
    assert got == RiskSnapshot(last_reset=None, day_start_equity=0.0, week_start_equity=0.0, balance_closed_hwm=0.0,
                               stage=Stage.NORMAL, rearm_seen=None, weekend_done=None, halted_at=None,
                               propose_only_until=None, rearm_refused=None, kill_everything=False,
                               stage2_pending_since=None)


@pytest.mark.parametrize("kind", ["risk", "orders"])
def test_an_unreadable_file_raises_the_json_error_not_none(tmp_path, kind):
    """S2: "unreadable -> fail loudly": the same exception type as the runner's parse raised."""
    (tmp_path / f"{kind}_{ACCOUNT}.json").write_text('{"cut short')
    s = JsonEngineStore(tmp_path, ACCOUNT)
    with pytest.raises(json.JSONDecodeError):
        s.load_risk() if kind == "risk" else s.load_orders()


@pytest.mark.parametrize("field", [{"rearm_seen": 5}, {"halted_at": "NaT"}])
def test_a_risk_file_value_the_engine_never_writes_refuses_the_start(tmp_path, field):
    """Deviation from S2, kept on the trading-safety ruling (2026-10-17): the snapshot's types refuse what the old
    parse let through. A hand-edited non-string `rearm_seen` would never equal a re-arm id (a fail-open re-arm check),
    and a `NaT` `halted_at` crashed mid-tick; both now refuse the start with a ValueError (pydantic ValidationError)."""
    (tmp_path / f"risk_{ACCOUNT}.json").write_text(json.dumps({"stage": "halted", **field}))
    with pytest.raises(ValueError):
        JsonEngineStore(tmp_path, ACCOUNT).load_risk()
    with pytest.raises(ValueError):
        Engine(EngineConfig(account_id=ACCOUNT, broker_name="icm", state_dir=str(tmp_path), owner_user_id=111),
               PaperBroker(equity=10_000), [], {}, ApprovalCenter({111}))


def test_the_engine_file_is_written_without_fsync_and_the_others_durably(tmp_path, monkeypatch):
    """S3: "a heartbeat, rewritten constantly, needs no fsync"; halts, stages and sent ids survive a power cut."""
    calls: list[tuple[str, bool]] = []

    def spy(path: Path, text: str, durable: bool = True) -> None:
        calls.append((Path(path).name, durable))

    monkeypatch.setattr(store, "write_atomic", spy)
    s = JsonEngineStore(tmp_path, ACCOUNT)
    payload: dict[str, Any] = {"account": ACCOUNT}
    s.write_engine_state(payload)
    s.save_risk(_risk())
    s.save_orders(_orders())
    assert calls == [(f"engine_{ACCOUNT}.json", False), (f"risk_{ACCOUNT}.json", True),
                     (f"orders_{ACCOUNT}.json", True)]
