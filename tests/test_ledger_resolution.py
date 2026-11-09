"""Trading-safety review 2026-11-09, R2: an exploration or cost-probe order whose result was neither a fill nor a
definite reject (retcode -1: a timeout, a lost reply) is written to its ledger row as sent, unfilled. When the look-up
(BACKLOG 72, engine/unconfirmed.py) later settles it, the row said nothing, so a reader saw an order that never filled
beside a position that did. Design rule: on settling, the row carries `resolution` (`adopted` / `opened_closed` /
`unfilled`); the retcode stays as the broker answered (the record of what was sent)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pandas as pd
import pytest

from goldbot.engine import Engine, cost_probe, exploration, unconfirmed
from goldbot.engine.cost_probe import ProbeRecord
from goldbot.engine.exploration import ExplorationRecord
from tests.test_engine_safety import T0, _prop
from tests.test_order_not_confirmed import AGENT, PID, SL, TP, _at, _broker, _engine, _look, _Unanswered


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[pd.Timestamp]:
    """The look-up's host clock (`unconfirmed._now`), as tests/test_order_not_confirmed.py moves it."""
    now = [pd.Timestamp.now("UTC")]
    monkeypatch.setattr(unconfirmed, "_now", lambda: now[0])
    return now


ARM = exploration.PREFIX + "S0-15m"
PROBE = cost_probe.PREFIX + "session_open-x"


class _Tagged:
    """The specialist under an exploration or probe tag, as `_place` hands it to `Engine._execute`."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id

    def __getattr__(self, name: str) -> Any:
        return getattr(AGENT, name)


def _sent(tmp_path: Path, pb: _Unanswered, agent_id: str) -> Engine:
    """The order sent for `agent_id` with no answer, and its ledger row written as `_place` writes it (retcode -1)."""
    eng, _ = _engine(tmp_path, pb)
    db = exploration.ledger_db(str(tmp_path))
    with db.write() as conn:
        if agent_id == ARM:
            exploration.LEDGER.upsert(conn, ExplorationRecord(
                client_order_id=PID, account_id="icm-demo", arm_id="S0-15m", family="control", tf="15m", side=1,
                lots=0.1, signal_utc=T0, sent_utc=T0, atr_usd=4.0, stop_usd=4.0, target_usd=6.0, hold_s=3600,
                retcode=-1))
        else:
            cost_probe.LEDGER.upsert(conn, ProbeRecord(client_order_id=PID, account_id="icm-demo",
                                                       agent_id="session_open-x", side=1, lots=0.1, sent_utc=T0,
                                                       retcode=-1))
    res = eng._execute(_prop(PID), cast(Any, _Tagged(agent_id)), 0.1, SL, TP, requested=2400.2)
    assert res is not None and eng._orders[PID].status == "ambiguous"
    return eng


def _row(tmp_path: Path, agent_id: str) -> ExplorationRecord | ProbeRecord | None:
    repo = exploration.LEDGER if agent_id == ARM else cost_probe.LEDGER
    with exploration.ledger_db(str(tmp_path)).read() as conn:
        return repo.get(conn, PID)


@pytest.mark.parametrize("agent_id", [ARM, PROBE])
def test_an_adopted_minus_one_row_reads_adopted(tmp_path, clock, agent_id):
    pb = _broker(retcode=-1, fill=0.1)
    eng = _sent(tmp_path, pb, agent_id)
    _look(eng)
    assert eng._orders[PID].status == "filled"
    row = _row(tmp_path, agent_id)
    assert row is not None and row.resolution == "adopted" and row.retcode == -1


@pytest.mark.parametrize("agent_id", [ARM, PROBE])
def test_a_minus_one_row_opened_and_closed_before_the_look_reads_opened_closed(tmp_path, clock, agent_id):
    pb = _broker(retcode=-1, fill=0.1)
    eng = _sent(tmp_path, pb, agent_id)
    [pos] = pb.positions()
    pb.close(pos.position_id)
    _look(eng)
    row = _row(tmp_path, agent_id)
    assert row is not None and row.resolution == "opened_closed"


@pytest.mark.parametrize("agent_id", [ARM, PROBE])
def test_a_minus_one_row_with_no_trade_after_two_clean_looks_reads_unfilled(tmp_path, clock, agent_id):
    pb = _broker(retcode=-1)
    eng = _sent(tmp_path, pb, agent_id)
    _at(clock, eng, 5)
    _look(eng)
    _at(clock, eng, 125)
    _look(eng)
    row = _row(tmp_path, agent_id)
    assert row is not None and row.resolution == "unfilled"


def test_a_row_still_unresolved_carries_no_resolution(tmp_path, clock):
    pb = _broker(retcode=-1)
    eng = _sent(tmp_path, pb, ARM)
    _at(clock, eng, 5)
    _look(eng)
    row = _row(tmp_path, ARM)
    assert row is not None and row.resolution is None


def test_an_ordinary_agent_s_settled_order_touches_no_ledger(tmp_path, clock):
    pb = _broker(retcode=-1, fill=0.1)
    eng, _ = _engine(tmp_path, pb)
    eng._execute(_prop(PID), AGENT, 0.1, SL, TP, requested=2400.2)
    _look(eng)
    assert eng._orders[PID].status == "filled"
    assert not (tmp_path / "aux.db").exists() or _row(tmp_path, ARM) is None
