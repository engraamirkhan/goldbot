"""Point-in-time Parquet store with DuckDB views.

Layout: data/{table}/source=X/symbol=XAUUSD/year=YYYY/month=MM/part-*.parquet
Every table carries `ts_utc` (tz-aware UTC). Tables whose rows become known later than the
event they describe (macro, news, tv_signals) also carry `available_utc`, and every join to
bars uses `available_utc`, never the nominal time. `asof_join` enforces this.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Iterable

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

TABLES = {
    "ticks", "bars_1m", "bars_15m", "bars_1h", "bars_4h", "bars_1d", "bars_1w",
    "macro", "calendar_events", "news", "tv_signals", "tv_ideas", "features", "labels",
    "trades", "decisions", "dq_events", "cost_tables", "fills",
}

KEY_COLUMNS = {
    "ticks": ["ts_utc", "bid", "ask"],
    "tv_signals": ["signal_hash"],
    "macro": ["series", "value_date", "vintage"],
    "fills": ["ts_utc", "client_order_id"],
}


def _utc(x: str | pd.Timestamp) -> pd.Timestamp:
    """Naive values are taken as UTC; aware ones are converted (pd.Timestamp(aware, tz=...) raises)."""
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths
    def table_dir(self, table: str) -> Path:
        if table not in TABLES:
            raise ValueError(f"unknown table {table!r}")
        return self.root / table

    def partition_dir(self, table: str, source: str, symbol: str, ts: pd.Timestamp) -> Path:
        return self.table_dir(table) / f"source={source}" / f"symbol={symbol}" / f"year={ts.year}" / f"month={ts.month:02d}"

    # ------------------------------------------------------------------ write
    def append(self, table: str, df: pd.DataFrame, *, source: str, symbol: str = "XAUUSD", dedupe: bool = True) -> int:
        """Append rows, de-duplicating against the key columns within each partition.

        dedupe=False writes each call as a new part file without reading the partition back: for append-only
        logs (the engine's ticks and fills) where rewriting a month of rows on every flush would be the cost."""
        if df.empty:
            return 0
        df = df.copy()
        if "ts_utc" not in df.columns:
            raise ValueError("every table row needs ts_utc")
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
        df = df.sort_values("ts_utc")
        written = 0
        keys = KEY_COLUMNS.get(table, ["ts_utc"])
        for (y, m), part in df.groupby([df["ts_utc"].dt.year, df["ts_utc"].dt.month]):
            pdir = self.partition_dir(table, source, symbol, part["ts_utc"].iloc[0])
            pdir.mkdir(parents=True, exist_ok=True)
            existing = self._read_partition(pdir) if dedupe else None
            if existing is not None and not existing.empty:
                merged = pd.concat([existing, part], ignore_index=True)
                merged = merged.drop_duplicates(subset=[k for k in keys if k in merged.columns], keep="last")
                for f in pdir.glob("*.parquet"):
                    f.unlink()
                part_out = merged.sort_values("ts_utc")
            else:
                part_out = part
            pq.write_table(
                pa.Table.from_pandas(part_out, preserve_index=False),
                pdir / f"part-{uuid.uuid4().hex[:8]}.parquet",
                compression="zstd",
            )
            written += len(part)
        return written

    @staticmethod
    def _read_partition(pdir: Path) -> pd.DataFrame | None:
        files = sorted(pdir.glob("*.parquet"))
        if not files:
            return None
        return pd.concat([pq.read_table(f).to_pandas() for f in files], ignore_index=True)

    # ------------------------------------------------------------------- read
    def read(
        self,
        table: str,
        *,
        source: str | None = None,
        symbol: str = "XAUUSD",
        start: str | pd.Timestamp | None = None,
        end: str | pd.Timestamp | None = None,
        columns: Iterable[str] | None = None,
    ) -> pd.DataFrame:
        tdir = self.table_dir(table)
        if not tdir.exists() or not any(tdir.rglob("*.parquet")):
            return pd.DataFrame()
        con = duckdb.connect()
        cols = ", ".join(columns) if columns else "*"
        glob = str(tdir / "**" / "*.parquet")
        where = ["symbol = ?"]
        params: list = [symbol]
        if source:
            where.append("source = ?")
            params.append(source)
        if start is not None:
            where.append("ts_utc >= ?")
            params.append(_utc(start).to_pydatetime())
        if end is not None:
            where.append("ts_utc < ?")
            params.append(_utc(end).to_pydatetime())
        sql = f"SELECT {cols} FROM read_parquet('{glob}', hive_partitioning=true) WHERE {' AND '.join(where)} ORDER BY ts_utc"
        df = con.execute(sql, params).df()
        con.close()
        if "ts_utc" in df.columns:
            df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
        return df

    def sql(self, query: str) -> pd.DataFrame:
        """Run DuckDB SQL; tables are exposed as views named after the table."""
        con = duckdb.connect()
        for t in TABLES:
            tdir = self.table_dir(t)
            if tdir.exists() and any(tdir.rglob("*.parquet")):
                con.execute(
                    f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{tdir / '**' / '*.parquet'}', hive_partitioning=true)"
                )
        df = con.execute(query).df()
        con.close()
        return df


def asof_join(bars: pd.DataFrame, other: pd.DataFrame, *, on: str = "ts_utc", available_col: str = "available_utc",
              prefix: str = "", tolerance: pd.Timedelta | None = None) -> pd.DataFrame:
    """Join `other` onto `bars` using only rows whose `available_utc` <= bar time.

    This is the single mechanism through which macro, news, calendar and TradingView data reach
    features. It is deliberately impossible to join on the nominal/value date here.
    """
    if available_col not in other.columns:
        raise ValueError(f"asof_join requires {available_col!r}; never join on nominal dates")
    left = bars.sort_values(on).copy()
    right = other.sort_values(available_col).copy()
    # one timestamp unit on both keys (pandas 3 keeps s/ms/us/ns per source and merge_asof rejects a mismatch)
    left[on] = pd.DatetimeIndex(pd.to_datetime(left[on], utc=True)).as_unit("ns")
    right[available_col] = pd.DatetimeIndex(pd.to_datetime(right[available_col], utc=True)).as_unit("ns")
    if prefix:
        right = right.rename(columns={c: f"{prefix}{c}" for c in right.columns if c not in (available_col,)})
    out = pd.merge_asof(
        left, right, left_on=on, right_on=available_col, direction="backward",
        allow_exact_matches=True, tolerance=tolerance,
    )
    return out.drop(columns=[available_col])
