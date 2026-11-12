"""The health pass reads the decisions journal of one account from that account's own folder (CPU attribution
2026-11-10, round 3: after each 15-min engine flush, history_gap/order_failed's `Store.read("decisions", ...)` globbed
and opened the schema of all 9,447 part files of every account, ~2 s of one core per account).

The narrowed read (`Store.read(folder_only=True)`) scans only source=<account>/symbol=<symbol>/ month partitions from
the read's start. union_by_name then sees that folder's files only, so a column that is all-null there would type as
DuckDB's NULL/INTEGER instead of VARCHAR; the health columns are cast explicitly, and the frame is the one the
whole-table read gives for them (values, dtypes, order), on duckdb 1.x and 2.x alike.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

from goldbot.data.store import Store
from goldbot.ops.checks import engine as engine_checks
from goldbot.ops.health import AccountRef

NOW = pd.Timestamp("2026-11-10 13:30", tz="UTC")
ACC = AccountRef(account_id="vantage-demo", symbol="XAUUSD+")
COLS = list(engine_checks.JOURNAL_COLUMNS)


def _rows(account: str, start: pd.Timestamp, n: int, *, agent: str | None = "trend-0",
          unit: Literal["s", "ms", "us", "ns"] = "ns",
          extra: bool = False, step: str = "7min") -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq=step, tz="UTC") + pd.Timedelta(microseconds=123, nanoseconds=456)
    df = pd.DataFrame({"ts_utc": ts.as_unit(unit), "account_id": account, "agent_id": [agent] * n,
                       "action": ["order" if i % 5 == 0 else "history_gap:1h" if i % 3 == 0 else "flat"
                                  for i in range(n)],
                       "p": [None] * n, "mult": [0.5] * n, "proposal_id": [None] * n,
                       "detail": [json.dumps({"ok": i % 10 != 0, "retcode": 10006 + i % 3}) for i in range(n)]})
    if extra:
        df["note"] = "only on another source"
    return df


def _journal(root: Path) -> Store:
    """A multi-source journal: the account's own parts (one with every agent_id null, us and ns units, ties on ts),
    two other accounts (an extra column, other agents) and an older month."""
    store = Store(root)
    w0 = NOW - pd.Timedelta(hours=30)
    own = [_rows(ACC.account_id, w0, 120), _rows(ACC.account_id, w0, 120, agent=None),        # same ts: ties
           _rows(ACC.account_id, w0 + pd.Timedelta(hours=10), 60, unit="us"),
           _rows(ACC.account_id, NOW - pd.Timedelta(days=45), 40)]                             # an older month
    for part in own:
        store.append("decisions", part, source=ACC.account_id, symbol=ACC.symbol, dedupe=False)
    for acct in ("icm-demo", "icm-probe"):
        for k in range(6):
            store.append("decisions", _rows(acct, w0 + pd.Timedelta(hours=k), 50, agent=f"a{k}", extra=k == 2,
                                            unit="us" if k % 2 else "ns"),
                         source=acct, symbol="XAUUSD", dedupe=False)
    return store


def _whole(store: Store, start: pd.Timestamp) -> pd.DataFrame:
    """The read the health checks made before: the whole table, the health columns kept."""
    full = store.read("decisions", source=ACC.account_id, symbol=ACC.symbol, start=start)
    return full[COLS].reset_index(drop=True)


@pytest.fixture(autouse=True)
def _no_cache():
    engine_checks._JOURNAL.clear()
    yield
    engine_checks._JOURNAL.clear()


def test_the_folder_read_equals_the_whole_table_read_for_the_health_columns(tmp_path):
    store = _journal(tmp_path / "data")
    start = engine_checks._journal_from(NOW)
    got = store.read("decisions", source=ACC.account_id, symbol=ACC.symbol, start=start,
                     columns=engine_checks.JOURNAL_COLUMNS, folder_only=True)
    want = _whole(store, start)
    assert len(want) > 150 and want["agent_id"].isna().any()
    pd.testing.assert_frame_equal(got, want)
    since = NOW - pd.Timedelta(hours=6)
    win = engine_checks._journal_window(tmp_path / "data", ACC, since, NOW, read_from=start)
    pd.testing.assert_frame_equal(win, want[(want["ts_utc"] >= since) & (want["ts_utc"] < NOW)].reset_index(drop=True))


def test_an_all_null_column_in_the_folder_keeps_the_whole_table_dtype(tmp_path):
    store = Store(tmp_path / "data")
    start = NOW - pd.Timedelta(hours=2)
    store.append("decisions", _rows(ACC.account_id, start, 10, agent=None), source=ACC.account_id,
                 symbol=ACC.symbol, dedupe=False)                          # agent_id null in every own part
    store.append("decisions", _rows("icm-demo", start, 10), source="icm-demo", symbol="XAUUSD", dedupe=False)
    got = store.read("decisions", source=ACC.account_id, symbol=ACC.symbol, start=start,
                     columns=engine_checks.JOURNAL_COLUMNS, folder_only=True)
    pd.testing.assert_frame_equal(got, _whole(store, start))


def test_other_accounts_and_older_months_are_not_scanned(tmp_path):
    """A corrupt part elsewhere breaks the whole-table read; the folder read never opens it."""
    store = _journal(tmp_path / "data")
    start = engine_checks._journal_from(NOW)
    want = _whole(store, start)
    for folder in (store.partition_dir("decisions", "icm-demo", "XAUUSD", NOW),
                   store.partition_dir("decisions", ACC.account_id, ACC.symbol, NOW - pd.Timedelta(days=45))):
        (folder / "part-corrupt.parquet").write_bytes(b"not parquet" * 10)
    with pytest.raises(Exception):  # noqa: B017 - duckdb's own error type differs by major
        store.read("decisions", source=ACC.account_id, symbol=ACC.symbol, start=start)
    got = store.read("decisions", source=ACC.account_id, symbol=ACC.symbol, start=start,
                     columns=engine_checks.JOURNAL_COLUMNS, folder_only=True)
    pd.testing.assert_frame_equal(got, want)


def test_a_column_no_part_of_the_folder_has_falls_back_to_the_whole_table_read(tmp_path):
    store = Store(tmp_path / "data")
    start = NOW - pd.Timedelta(hours=2)
    store.append("decisions", _rows(ACC.account_id, start, 10).drop(columns=["detail"]), source=ACC.account_id,
                 symbol=ACC.symbol, dedupe=False)
    store.append("decisions", _rows("icm-demo", start, 10), source="icm-demo", symbol="XAUUSD", dedupe=False)
    got = store.read("decisions", source=ACC.account_id, symbol=ACC.symbol, start=start,
                     columns=engine_checks.JOURNAL_COLUMNS, folder_only=True)
    pd.testing.assert_frame_equal(got, _whole(store, start))
    assert got["detail"].isna().all()


def test_an_account_without_a_journal_folder_reads_empty(tmp_path):
    store = _journal(tmp_path / "data")
    got = store.read("decisions", source="nobody", symbol="XAUUSD", start=NOW - pd.Timedelta(hours=1),
                     columns=engine_checks.JOURNAL_COLUMNS, folder_only=True)
    assert got.empty


def test_folder_only_needs_the_folder_and_typed_columns(tmp_path):
    store = _journal(tmp_path / "data")
    for kw in ({"source": None, "symbol": ACC.symbol, "columns": engine_checks.JOURNAL_COLUMNS},
               {"source": ACC.account_id, "symbol": None, "columns": engine_checks.JOURNAL_COLUMNS},
               {"source": ACC.account_id, "symbol": ACC.symbol, "columns": COLS}):
        with pytest.raises(ValueError, match="folder_only"):
            store.read("decisions", folder_only=True, **kw)
