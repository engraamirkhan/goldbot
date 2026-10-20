"""`goldbot run fill-count`: demo fills per account per day by kind, cumulative and progress vs the target, read-only."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from typing import Literal

import pandas as pd

from goldbot.db.connection import Database
from goldbot.engine import cost_probe
from goldbot.ops import fill_count
from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
from tests.test_api_home_cards import ACC, LOGIN

START = fill_count.CAMPAIGN_START_UTC
NOW = pd.Timestamp("2026-10-21 12:00", tz="UTC")


def _run(tmp_path: Path, *argv: str) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = fill_count.main(["--state-dir", str(tmp_path), *argv], enabled=[ACC], now=NOW)
    return code, buf.getvalue()


def _trade(tmp_path: Path, pid: int, entry: pd.Timestamp, agent: str) -> None:
    append_closed_trade(tmp_path, ClosedTrade(account_id=ACC, broker="b", mode="demo", exit_utc=entry + pd.Timedelta(hours=1),
                                              ret=0.001, pnl=1.0, equity_before=1000.0, position_id=pid, agent_id=agent,
                                              entry_utc=entry))


def _probe(db: Database, cid: str, ts: pd.Timestamp, filled: bool = True, basis: str | None = None,
           purpose: Literal["cost_probe", "cost_measurement"] = "cost_probe") -> None:
    with db.write() as conn:
        cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
            client_order_id=cid, account_id=ACC, agent_id="mfi-g0", side=1, lots=0.01, sent_utc=ts,
            fill_price=2400.0 if filled else None, position_id=7 if filled else None, basis=basis, purpose=purpose))


def _seed(tmp_path: Path) -> None:
    _trade(tmp_path, 1, START + pd.Timedelta(hours=1), "box-g0-1h")
    _trade(tmp_path, 2, START - pd.Timedelta(hours=3), "box-g0-1h")                  # before the campaign
    _trade(tmp_path, 3, START + pd.Timedelta(hours=2), "cost_probe:mfi-g0")          # a probe: counted once, as a probe
    db = Database.open(tmp_path, "aux")
    _probe(db, "cp-1", START + pd.Timedelta(hours=2))
    _probe(db, "cp-2", START + pd.Timedelta(days=1))
    _probe(db, "cp-3", START + pd.Timedelta(days=1), basis="scheduled")
    _probe(db, "cp-4", START + pd.Timedelta(days=1), filled=False)                   # never filled


def test_counts_by_day_account_and_kind(tmp_path):
    _seed(tmp_path)
    code, out = _run(tmp_path, "--json")
    doc = json.loads(out)
    assert code == 0
    assert doc["cumulative"] == {"own_entry": 1, "mirror_probe": 2, "scheduled_probe": 1, "total": 4}
    assert [(r["day"], r["own_entry"], r["mirror_probe"], r["scheduled_probe"]) for r in doc["daily"]] == [
        ("2026-10-19", 1, 1, 0), ("2026-10-20", 0, 1, 1)]
    assert doc["progress"]["target"] == 300 and doc["progress"]["remaining"] == 296


def test_since_trims_daily_rows_but_not_the_cumulative(tmp_path):
    _seed(tmp_path)
    doc = json.loads(_run(tmp_path, "--json", "--since", "2026-10-20")[1])
    assert [r["day"] for r in doc["daily"]] == ["2026-10-20"] and doc["cumulative"]["total"] == 4


def test_text_report_and_masking(tmp_path):
    _seed(tmp_path)
    (tmp_path / "engine_x.json").write_text("{}")
    out = _run(tmp_path)[1]
    assert "cumulative: 4" in out and "target 300" in out and str(LOGIN) not in out


def test_empty_state_is_zero_not_an_error(tmp_path):
    code, out = _run(tmp_path)
    assert code == 0 and "(no fills)" in out and "cumulative: 0" in out


def test_unreadable_ledger_is_named_and_exits_1(tmp_path):
    (tmp_path / "aux.db").write_text("not a database")
    code, out = _run(tmp_path)
    assert code == 1 and "UNREADABLE cost-probe ledger" in out


def test_read_only_creates_no_files(tmp_path):
    import sqlite3
    _seed(tmp_path)
    c = sqlite3.connect(tmp_path / "aux.db")
    c.execute("PRAGMA journal_mode=DELETE")                     # rollback mode: a WAL-forcing open would change it
    c.close()
    before = sorted(p.name for p in tmp_path.iterdir()), (tmp_path / "aux.db").stat().st_mtime_ns
    assert _run(tmp_path)[0] == 0
    assert (sorted(p.name for p in tmp_path.iterdir()), (tmp_path / "aux.db").stat().st_mtime_ns) == before
    assert sqlite3.connect(tmp_path / "aux.db").execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_bad_arguments_exit_2(tmp_path):
    assert _run(tmp_path, "--since", "nonsense")[0] == 2
    assert _run(tmp_path, "--account", "nope")[0] == 2


def test_a_cost_measurement_row_is_a_scheduled_probe_whatever_its_id_or_basis_say(tmp_path):
    """Review fix (quant): the purpose is the classification; the "sched" match is only the fallback for rows without
    one, so a cost_measurement row with a plain id still counts as scheduled."""
    db = Database.open(tmp_path, "aux")
    _probe(db, "cp-plain", START + pd.Timedelta(hours=1), purpose="cost_measurement")
    _probe(db, "cm-sched-1", START + pd.Timedelta(hours=2))                       # fallback: the id says scheduled
    _probe(db, "cp-mirror", START + pd.Timedelta(hours=3))
    code, out = _run(tmp_path, "--json")
    assert code == 0
    assert json.loads(out)["cumulative"] == {"own_entry": 0, "mirror_probe": 1, "scheduled_probe": 2, "total": 3}
