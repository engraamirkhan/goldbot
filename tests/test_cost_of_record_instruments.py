"""The cost of record per instrument (multi-instrument plan 2026-11-02, lane L1: "cost_of_record.py -> per-instrument
costs"). XAUUSD cells keep their broker key and every number; another instrument's fills form their own cells under
`<broker>:<instrument>` (never pooled with gold), with commission spread over that instrument's contract."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from goldbot.db.connection import Database
from goldbot.engine.exploration import LEDGER, ExplorationRecord
from goldbot.research import cost_of_record as C

HOSTS = {"acct-a": "brk"}
LO, HI = pd.Timestamp("2026-10-26 22:00", tz="UTC"), pd.Timestamp("2026-10-27 22:00", tz="UTC")
LONDON = pd.Timestamp("2026-10-27 09:00", tz="UTC")


def _trade(cid: str, *, symbol: str = "XAUUSD", req: float = 2400.0, spread: float = 0.08, slip: float = 0.10,
           lots: float = 0.01, comm: float = 0.06, fx: float = 0.75) -> ExplorationRecord:
    return ExplorationRecord(client_order_id=cid, account_id="acct-a", arm_id="S0", family="f", tf="1m", side=1,
                             lots=lots, signal_utc=LONDON, sent_utc=LONDON, atr_usd=3.0, stop_usd=3.0, target_usd=6.0,
                             hold_s=60, requested_price=req, spread_usd=spread, fill_price=req + slip,
                             exit_utc=LONDON + pd.Timedelta(minutes=5), exit_price=req + 1.0, exit_reason="time_exit",
                             commission=-comm, swap=0.0, fx_acct_per_usd=fx, symbol=symbol)


def _table(tmp_path: Path, rows: list[ExplorationRecord]) -> dict:
    db = Database.open(tmp_path, "aux")
    with db.write() as conn:
        for r in rows:
            LEDGER.upsert(conn, r)
    conn = sqlite3.connect(f"file:{tmp_path / 'aux.db'}?mode=ro", uri=True)
    try:
        fr = C.read_frames(conn, {}, HOSTS, LO, HI)
    finally:
        conn.close()
    return C.build_table(fr, version="t", window_utc=(LO.isoformat(), HI.isoformat()), resamples=200)


def test_an_older_row_without_a_symbol_is_gold() -> None:
    raw = _trade("g").model_dump()
    raw.pop("symbol")
    assert ExplorationRecord.model_validate(raw).symbol == "XAUUSD"


def test_gold_cells_keep_their_broker_key_and_numbers_beside_another_instrument(tmp_path: Path) -> None:
    only_gold = _table(tmp_path / "a", [_trade("g1")])
    mixed = _table(tmp_path / "b", [_trade("g1"), _trade("e1", symbol="EURUSD", req=1.08, spread=0.00002,
                                                         slip=0.00001, lots=0.1, comm=0.5)])
    assert mixed["cells"]["brk"] == only_gold["cells"]["brk"]
    assert set(mixed["cells"]) == {"brk", "brk:EURUSD"}


def test_another_instruments_commission_is_per_unit_of_its_own_contract(tmp_path: Path) -> None:
    t = _table(tmp_path, [_trade("e1", symbol="EURUSD", req=1.08, spread=0.00002, slip=0.00001, lots=0.1, comm=0.5,
                                 fx=0.75)])
    cell = t["cells"]["brk:EURUSD"]["all"]
    assert cell["commission_usd"] == pytest.approx(0.5 / (0.1 * 100_000.0 * 0.75))
    assert cell["spread_usd"] == pytest.approx(0.00002)


def test_a_symbol_the_registry_no_longer_lists_leaves_its_commission_unread(tmp_path: Path) -> None:
    t = _table(tmp_path, [_trade("g1"), _trade("x1", symbol="GONE", req=5.0, spread=0.01, slip=0.0)])
    assert t["cells"]["brk:GONE"]["all"]["commission_usd"] == 0.0 and "brk" in t["cells"]
