"""Import the owner's TradingView "Export chart data" CSV into the `tv_indicators` dataset (opt-in research history).

    python -m goldbot.ops.run tv-import <csv> --indicator smc --version 1.0 --tf 15

The export has `time, open, high, low, close` and then one column per visible plot, often with repeated names, so the
plots are named by position (`plot_0..N`, the same names the alert template uses) and the header text is kept as
`plot_label`. Rows go to the same dataset the live alerts fill, beside them, never over them:

* `origin="csv"` and `repaint_validated=false` on every row (the repaint check, goldbot/research/tv_repaint.py, flips it
  per indicator x timeframe). A live-alert row has no `origin`; the store key includes `origin`, so a CSV row can never
  replace an alert row, and the readers (goldbot/features/tradingview.py) take the alert whenever a bar has one.
* "Every join to bars via asof_join on available_utc": TradingView's `time` is the bar's OPEN, so
  `available_utc = open + timeframe` (the bar close), never earlier. A bar still forming at import time is dropped (its
  values will change); a bar that opens in the future is an error (a wrong clock or a wrong timezone, fail closed).
* Fail closed on: no `time` column, a time that does not parse, a cell that is not a number (empty and `NaN` are fine:
  they mean the plot had no value), times that do not strictly increase, spacing that is not a whole number of
  timeframes (gaps for weekends and holidays are allowed), an unknown timeframe. Nothing is written on an error.
* A time without a zone is ambiguous (the chart's own timezone, which the file does not say) and is REFUSED: read as UTC
  a chart behind UTC (New York) would stamp bars earlier than real, so `available_utc` would precede the data = look-ahead,
  which the spacing and future-row checks cannot see. Say `--tz <IANA zone>` (converted DST-aware; a time in the repeated
  or skipped DST hour is an error) or `--assume-utc` when the chart is set to UTC. Epoch and zone-carrying times are exact.
* Idempotent: importing the same file again rewrites the same rows. A file over `MAX_BYTES` is refused.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import math
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.data.store import Store
from goldbot.webhook.app import tf_seconds
from goldbot.webhook.inbox import TABLE, safe_symbol, source_of

ORIGIN = "csv"
MAX_BYTES = 50 * 1024 * 1024        # a 1m export of several years is a few MB; this only stops a wrong file
_OHLC = ("open", "high", "low", "close")
_EMPTY = {"", "nan", "n/a", "na", "none", "null"}


class ImportReport(FrozenRecord):
    indicator: str
    version: str
    symbol: str
    tf: str
    source: str
    bars: int = Field(ge=0)
    rows: int = Field(ge=0)
    dropped_forming: int = Field(ge=0)
    first_bar: str
    last_bar: str
    file_sha256: str


def normalize_tf(tf: str) -> str:
    """The alert's `{{interval}}` spelling: minutes as digits, `1D` for a day; an unknown timeframe is an error."""
    t = tf.strip().upper()
    t = {"D": "1D", "W": "1W"}.get(t, t)
    if tf_seconds(t) <= 0:
        raise ValueError(f"unknown timeframe {tf!r}: use minutes (15, 60, 240) or 1D, 1W")
    return t


def _zone(tz: str | None, assume_utc: bool) -> ZoneInfo | None:
    if tz and assume_utc:
        raise ValueError("give --tz or --assume-utc, not both")
    if not tz:
        return None
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone {tz!r}: use an IANA name such as America/New_York") from exc


def _parse_time(raw: str, line: int, tz: ZoneInfo | None, assume_utc: bool) -> pd.Timestamp:
    """Epoch and zone-carrying times are exact; a zoneless time needs `tz` or `assume_utc` (see the module doc)."""
    s = raw.strip()
    try:
        if s.lstrip("-").isdigit():
            n = int(s)
            return pd.Timestamp(n // 1000 if abs(n) > 10**11 else n, unit="s", tz="UTC")     # ms exports tolerated
        t = pd.Timestamp(s)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"line {line}: time {raw!r} does not parse") from exc
    if t.tzinfo is not None:
        return t.tz_convert("UTC")
    if assume_utc:
        return t.tz_localize("UTC")
    if tz is None:
        raise ValueError(f"line {line}: time {raw!r} has no timezone; say which zone the chart was set to with "
                         f"--tz <IANA zone, e.g. America/New_York>, or --assume-utc if it is UTC")
    try:
        return t.tz_localize(tz, ambiguous="raise", nonexistent="raise").tz_convert("UTC")
    except ValueError as exc:
        raise ValueError(f"line {line}: time {raw!r} is ambiguous or does not exist in {tz.key} (DST change)") from exc


def _number(raw: str, line: int, col: str) -> float:
    s = raw.strip()
    if s.lower() in _EMPTY:
        return math.nan
    try:
        v = float(s.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"line {line}: {col!r} value {raw!r} is not a number") from exc
    if not math.isfinite(v):
        raise ValueError(f"line {line}: {col!r} value {raw!r} is not a finite number")
    return v


def parse_export(text: str, *, tz: ZoneInfo | None = None, assume_utc: bool = False) -> tuple[list[str], list[pd.Timestamp], list[float], list[list[float]]]:
    """(plot labels, bar-open times, closes, plot values per bar) from the export's text."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("the file is empty")
    header = [h.strip() for h in rows[0]]
    low = [h.lower() for h in header]
    if "time" not in low:
        raise ValueError(f"no 'time' column in the header ({', '.join(header)}): is this a TradingView chart export?")
    ti = low.index("time")
    ci = low.index("close") if "close" in low else None
    skip = {ti, *(low.index(n) for n in _OHLC if n in low)}
    plot_cols = [i for i in range(len(header)) if i not in skip]
    times: list[pd.Timestamp] = []
    closes: list[float] = []
    values: list[list[float]] = []
    for n, r in enumerate(rows[1:], start=2):
        if len(r) < len(header):
            r = r + [""] * (len(header) - len(r))
        times.append(_parse_time(r[ti], n, tz, assume_utc))
        closes.append(_number(r[ci], n, "close") if ci is not None else math.nan)
        values.append([_number(r[i], n, header[i]) for i in plot_cols])
    return [header[i] for i in plot_cols], times, closes, values


def import_tv_csv(path: str | Path, *, store: Store, indicator: str, version: str, tf: str, symbol: str = "XAUUSD",
                  tz: str | None = None, assume_utc: bool = False, now: pd.Timestamp | None = None) -> ImportReport:
    """Validate the export and write its closed bars to `tv_indicators` beside the live alerts (see the module doc)."""
    tf = normalize_tf(tf)
    sec = tf_seconds(tf)
    zone = _zone(tz, assume_utc)
    size = Path(path).stat().st_size
    if size > MAX_BYTES:
        raise ValueError(f"{path} is {size} bytes, larger than the {MAX_BYTES} byte cap: is this a chart export?")
    raw = Path(path).read_bytes()
    labels, times, closes, values = parse_export(raw.decode("utf-8-sig"), tz=zone, assume_utc=assume_utc)
    if not times:
        raise ValueError("the file has a header but no bars")
    now = pd.Timestamp.now(tz="UTC") if now is None else (now.tz_convert("UTC") if now.tzinfo else now.tz_localize("UTC"))
    width = pd.Timedelta(seconds=sec)
    for a, b in zip(times, times[1:]):
        if b <= a:
            raise ValueError(f"time must strictly increase: {b.isoformat()} follows {a.isoformat()}")
        step = (b - a).total_seconds()
        ok = step >= sec - 3600 if sec >= 86400 else step % sec == 0           # a day's open moves with the DST hour
        if not ok:
            raise ValueError(f"spacing {step:g} s between {a.isoformat()} and {b.isoformat()} is not a whole number of "
                             f"{tf} bars (gaps are fine, a different timeframe or timezone is not)")
    if times[-1] > now:
        raise ValueError(f"future rows: the bar opening {times[-1].isoformat()} is after now ({now.isoformat()})")
    keep = [i for i, t in enumerate(times) if t + width <= now]
    dropped = len(times) - len(keep)
    if not keep:
        raise ValueError("every bar is still forming; nothing to import")
    sha = hashlib.sha256(raw).hexdigest()
    frames = []
    for i in keep:
        bar = times[i]
        frames.append(pd.DataFrame({
            "ts_utc": bar, "bar_time": bar, "available_utc": bar + width, "fired_at": bar + width, "received_at": now,
            "indicator": indicator, "version": version, "tf": tf, "price": None if math.isnan(closes[i]) else closes[i],
            "intrabar": False, "plot": [f"plot_{k}" for k in range(len(labels))], "value": values[i],
            "plot_label": labels, "origin": ORIGIN, "repaint_validated": False, "file_sha256": sha,
        }))
    df = pd.concat(frames, ignore_index=True)
    df["price"] = df["price"].astype("float64")
    source = source_of(indicator, version)
    store.append(TABLE, df, source=source, symbol=safe_symbol(symbol))
    return ImportReport(indicator=indicator, version=version, symbol=symbol, tf=tf, source=source, bars=len(keep),
                        rows=len(df), dropped_forming=dropped, first_bar=times[keep[0]].isoformat(),
                        last_bar=times[keep[-1]].isoformat(), file_sha256=sha)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run.py tv-import", description="Import a TradingView 'Export chart data' CSV "
                                 "(history for backtests; unvalidated until tv-repaint-check passes).")
    ap.add_argument("csv", type=Path)
    ap.add_argument("--indicator", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--tf", required=True, help="chart timeframe: 15, 60, 240, 1D")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--tz", default=None, help="IANA timezone of the chart's times when the file has none "
                    "(America/New_York); DST-aware")
    ap.add_argument("--assume-utc", action="store_true", help="the file's zoneless times are UTC (chart set to UTC)")
    ap.add_argument("--data-root", default=None, help="store root (default: settings data_root)")
    ap.add_argument("--now", default=None, help="clock override for tests (ISO time)")
    a = ap.parse_args(argv)
    if a.data_root is None:
        from goldbot.config import load_settings
        a.data_root = load_settings().data_root
    try:
        rep = import_tv_csv(a.csv, store=Store(a.data_root), indicator=a.indicator, version=a.version, tf=a.tf,
                            symbol=a.symbol, tz=a.tz, assume_utc=a.assume_utc, now=pd.Timestamp(a.now) if a.now else None)
    except (ValueError, OSError) as exc:
        print(f"tv-import: {exc}", file=sys.stderr)
        return 2
    print(f"imported {rep.bars} bars ({rep.rows} rows) of {rep.source} {rep.tf}, {rep.first_bar} to {rep.last_bar}; "
          f"{rep.dropped_forming} still-forming bar dropped; origin=csv, repaint_validated=false: results read from "
          f"them are 'unvalidated (repaint risk)' until `tv-repaint-check` passes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
