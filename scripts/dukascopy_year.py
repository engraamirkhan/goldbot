"""Download one year of XAUUSD 1-minute bars from Dukascopy (bid and ask separately, via dukascopy-node),
merge into goldbot's bar schema, validate coverage month by month, and write one Parquet for the year.

Why m1 rather than ticks: Dukascopy serves m1 as one file per day per price side (2 requests/day) versus
24 hourly tick files; the tick route gets throttled on shared runners and silently returns gaps. Spread on
these bars is ask_close - bid_close per minute, which is adequate for price-structure features; the
execution-cost model uses the brokers' own ticks (design: Data architecture).

Two dukascopy-node behaviours shape the download loop: `-to` is exclusive (bars with ts < to), and by default
one day that exhausts its retries aborts the whole request with no output. So each side is fetched in
week-sized chunks with an exclusive end, retried with backoff, and a last salvage pass runs with
--no-fail-after-retries so a single bad day costs that day rather than the month.

Source (2026-10-19): days are read straight from Dukascopy's bi5 archive (datafeed.dukascopy.com, one file per day
per side, `_fetch_datafeed`); dukascopy-node is only the fallback. dukascopy-node 1.50 reads jetta.dukascopy.com, which
answered HTTP 202 with an empty body for every day after 2026-10-05, so run 39255449249 published October unchanged
(4,126 bars, ending 10-05) and reported it "ok". A month whose download returns nothing is now reported "stale".
There is no designed lag: a day is fetched once it has closed (yesterday and earlier).

With --existing (last published Parquet for the year) months that are already complete are kept and only
missing, partial or still-open months are downloaded again.

With --months (targeted refetch, G1/G2) exactly the listed months of this year are downloaded again, whatever their
size or flags, and every other month is kept from --existing (required). Each listed month goes through
merge_never_shrink, so a refetch can only add or repair days, never lose one.

Instruments (`--symbol`, INSTRUMENTS): xauusd (default) and xagusd, same archive and bar schema. A month whose median
bid lies outside the instrument's price band is reported SCALE and never published (a wrong point size), and months
before the instrument's first archive day are not downloaded (before_history; issue #108).

  python scripts/dukascopy_year.py 2025 [--symbol xauusd] [--out xauusd_1m_dukascopy_2025.parquet] [--report report.md]
                                        [--existing xauusd_1m_dukascopy_2025.parquet] [--months 2025-02,2025-03]
"""
from __future__ import annotations

import argparse
import datetime as dt
import lzma
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dukascopy_months import parse_months  # noqa: E402

from goldbot.base import FrozenRecord  # noqa: E402
from goldbot.data.calendar import DEFAULT_SESSIONS  # noqa: E402
from goldbot.data.quality import check_bars, quarantine  # noqa: E402
from goldbot.data.resample import BAR_COLUMNS  # noqa: E402

MIN_BARS_PER_FULL_MONTH = 15_000   # ~20 trading days x 1,380 minutes = 27,600; accept down to 15,000
MIN_BARS_PARTIAL_MONTH = 5_000     # below this a month is reported MISSING


def month_range(year: int, today: dt.date | None = None) -> Iterator[tuple[dt.date, dt.date]]:
    """(first day, exclusive end) per month of `year`, covering completed days only: Dukascopy publishes a day's
    m1 file after the day closes, so today is never requested (on the 1st the current month is skipped)."""
    today = today or dt.date.today()
    for m in range(1, 13):
        start = dt.date(year, m, 1)
        if start >= today:
            break
        end = dt.date(year + 1, 1, 1) if m == 12 else dt.date(year, m + 1, 1)
        yield start, min(end, today)


def week_chunks(start: dt.date, end: dt.date, days: int = 7) -> Iterator[tuple[dt.date, dt.date]]:
    """Split [start, end) into chunks of at most `days`; chunks with no weekday (no gold trading) are skipped."""
    a = start
    while a < end:
        b = min(a + dt.timedelta(days=days), end)
        if any((a + dt.timedelta(days=i)).weekday() < 5 for i in range((b - a).days)):
            yield a, b
        a = b


def months_to_fetch(existing: pd.DataFrame | None, months: list[tuple[dt.date, dt.date]], today: dt.date | None = None,
                    retry_flagged: bool = False) -> set[str]:
    """Month keys (YYYY-MM) that need downloading: absent or short in `existing`, or still open. Closed months
    flagged partial/imputed are retried only with `retry_flagged` (G5: the weekly retry of the three bid-imputed 2026
    months cost ~4 h of Actions minutes a week and never repaired them; repairs are targeted dispatches)."""
    today = today or dt.date.today()
    keys = {f"{s:%Y-%m}" for s, _ in months}
    if existing is None or existing.empty:
        return keys
    ts = pd.DatetimeIndex(pd.to_datetime(existing["ts_utc"], utc=True))
    key = pd.Series(ts.strftime("%Y-%m"), index=existing.index)
    counts = key.value_counts()
    flags = existing["dq_flag"].astype(str) if "dq_flag" in existing else pd.Series("", index=existing.index)
    partial = set(key[flags.str.contains("partial_month|ask_imputed|bid_imputed")].unique())
    need = set()
    for s, e in months:
        k = f"{s:%Y-%m}"
        natural_end = dt.date(s.year + 1, 1, 1) if s.month == 12 else dt.date(s.year, s.month + 1, 1)
        still_open = natural_end > today  # the current month keeps growing
        if still_open or counts.get(k, 0) < MIN_BARS_PER_FULL_MONTH or (retry_flagged and k in partial):
            need.add(k)
    return need


def _run(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


DATAFEED = "https://datafeed.dukascopy.com/datafeed"
BI5_M1 = np.dtype([("sec", ">i4"), ("open", ">i4"), ("close", ">i4"), ("low", ">i4"), ("high", ">i4"), ("volume", ">f4")])


class Instrument(FrozenRecord):
    """One Dukascopy instrument on this pipeline.

    point: bi5 prices are integers in 1/point of the quote currency. price_band: (low, high) a month's median bid close
    must lie in, a coarse guard against a wrong point size (a factor of 10 or more), not a spike filter; months outside
    are reported SCALE and never published. history_start: the first day the archive serves; months that end on or
    before it are not downloaded (reported before_history), the month holding it starts there."""

    code: str
    point: float
    price_band: tuple[float, float]
    history_start: dt.date | None = None

    def price_ok(self, price: float) -> bool:
        return self.price_band[0] <= price <= self.price_band[1]


INSTRUMENTS: dict[str, Instrument] = {
    # gold: ~255 $ (2001 low) to ~4,400 $ (2026); 2003-05-04 is the start Tickstory and our own downloader default
    # (download_dukascopy.sh) give; the first published bar is checked against it after each pre-2010 build
    "xauusd": Instrument(code="XAUUSD", point=1000.0, price_band=(150.0, 20_000.0), history_start=dt.date(2003, 5, 4)),
    # silver: ~4 $ (2003) to ~50 $ (1980, 2011); point 1/1000 like gold; the SCALE guard catches a wrong one
    "xagusd": Instrument(code="XAGUSD", point=1000.0, price_band=(2.0, 200.0), history_start=None),
}
XAUUSD = INSTRUMENTS["xauusd"]


def datafeed_url(day: dt.date, side: str, inst: Instrument | None = None) -> str:
    """Dukascopy's bi5 archive URL for one day of m1 candles; the archive's month index is zero-based."""
    code = (inst or XAUUSD).code
    return f"{DATAFEED}/{code}/{day.year:04d}/{day.month - 1:02d}/{day.day:02d}/{side.upper()}_candles_min_1.bi5"


def _http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "goldbot-data/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https host)
        return bytes(r.read())


def _fetch_datafeed(start: dt.date, end: dt.date, side: str, inst: Instrument | None = None) -> tuple[pd.DataFrame | None, str]:
    """m1 candles for [start, end) straight from the bi5 archive (primary source since 2026-10-19).

    Root cause of the 2026-10 hole: dukascopy-node 1.50 reads jetta.dukascopy.com, which answered HTTP 202 with an
    empty body for every recent day, and the tool saved that as an empty file; the archive had every day. Records are
    24 bytes big-endian: seconds from midnight, open, close, low, high (int, 1/point), volume (float). Minutes with
    volume 0 (market closed) are dropped as dukascopy-node does, except on a day with no volume at all (seen in the
    early archive), where only flat minutes (open = high = low = close) are dropped. A weekday that errors, or whose
    file does not decode (an error page served with 200, a truncated body: LZMAError used to kill the whole year), is
    left out (missing_weekdays asks for it again); (None, error) only when no day could be read at all."""
    inst = inst or XAUUSD
    frames, errors, days = [], [], 0
    d = start
    while d < end:
        if d.weekday() != 5:   # Saturday never trades; Sunday carries the Asian open
            days += 1
            try:
                raw = _http_get(datafeed_url(d, side, inst))
            except (OSError, urllib.error.URLError) as e:
                errors.append(f"{d}: {e}")
                d += dt.timedelta(days=1)
                continue
            if raw:
                try:
                    data = lzma.decompress(raw)
                except lzma.LZMAError as e:
                    errors.append(f"{d}: corrupt bi5 ({len(raw)} bytes): {e}")
                    d += dt.timedelta(days=1)
                    continue
                rec = np.frombuffer(data[: len(data) // 24 * 24], dtype=BI5_M1)
                f = pd.DataFrame({k: rec[k].astype("int64" if k != "volume" else "float64") for k in ("sec", "open", "close", "low", "high", "volume")})
                if (f["volume"] > 0).any():
                    f = f[f["volume"] > 0].copy()
                else:
                    flat = (f["open"] == f["high"]) & (f["high"] == f["low"]) & (f["low"] == f["close"])
                    f = f[~flat].copy()
                if not f.empty:
                    f["ts_utc"] = pd.Timestamp(d, tz="UTC") + pd.to_timedelta(f["sec"], unit="s")
                    for c in ("open", "high", "low", "close"):
                        f[c] = f[c] / inst.point
                    frames.append(f[["ts_utc", "open", "high", "low", "close", "volume"]])
        d += dt.timedelta(days=1)
    if errors and len(errors) == days:
        return None, "; ".join(errors)[-200:]
    if not frames:
        return None, "empty"
    return pd.concat(frames).sort_values("ts_utc").reset_index(drop=True), ""


def _fetch(start: dt.date, end: dt.date, side: str, outdir: Path, salvage: bool = False,
           inst: Instrument | None = None) -> tuple[pd.DataFrame | None, str]:
    """[start, end) from the bi5 archive, falling back to dukascopy-node. Returns (frame or None, error text)."""
    df, err = _fetch_datafeed(start, end, side, inst)
    if df is not None:
        return df, ""
    df, err2 = _fetch_node(start, end, side, outdir, salvage=salvage, inst=inst)
    return df, "" if df is not None else f"datafeed: {err}; node: {err2}"


def _fetch_node(start: dt.date, end: dt.date, side: str, outdir: Path, salvage: bool = False,
                inst: Instrument | None = None) -> tuple[pd.DataFrame | None, str]:
    """One dukascopy-node call for [start, end). Returns (frame or None, error text)."""
    sym = (inst or XAUUSD).code.lower()
    outdir.mkdir(parents=True, exist_ok=True)
    for f in outdir.glob(f"{sym}-m1-{side}-*.csv"):
        f.unlink(missing_ok=True)
    cmd = ["npx", "-y", "dukascopy-node", "-i", sym, "-from", start.isoformat(), "-to", end.isoformat(),
           "-t", "m1", "-p", side, "-v", "true", "-f", "csv", "-dir", str(outdir),
           "-bs", "2", "-bp", "1500", "-r", "5", "-rp", "4000"] + (["-fr"] if salvage else [])
    try:
        r = _run(cmd)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    files = sorted(outdir.glob(f"{sym}-m1-{side}-*.csv"), key=os.path.getmtime)
    if not files or files[-1].stat().st_size < 100:
        err = (r.stderr.strip() or r.stdout.strip())[-200:].replace("\n", " ")
        return None, f"rc={r.returncode} {err}"
    df = pd.read_csv(files[-1])
    files[-1].unlink(missing_ok=True)
    if df.empty:
        return None, "empty file"
    df.columns = [c.lower() for c in df.columns]
    df["ts_utc"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df[["ts_utc", "open", "high", "low", "close", "volume"]], ""


def download_side(start: dt.date, end: dt.date, side: str, outdir: Path, attempts: int = 4,
                  day_attempts: int = 3, inst: Instrument | None = None) -> pd.DataFrame | None:
    frames = []
    for a, b in week_chunks(start, end):
        df, err = None, ""
        for i in range(attempts):
            df, err = _fetch(a, b, side, outdir, salvage=(i == attempts - 1), inst=inst)
            if df is not None:
                break
            time.sleep(10 * (i + 1))
        if df is None:
            print(f"  {side} {a}..{b}: no data after {attempts} attempts ({err})", flush=True)
        else:
            frames.append(df)
    if not frames:
        return None
    got = pd.concat(frames).drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)
    # The last attempt of a week runs with -fr, which skips a throttled (HTTP 429) day without failing: the week
    # "succeeds" minus that day (root cause of the 0-bar weekdays). Ask for each such day again on its own, slowly.
    for day in missing_weekdays(got, start, end):
        for i in range(day_attempts):
            df, _err = _fetch(day, day + dt.timedelta(days=1), side, outdir, inst=inst)
            if df is not None:
                got = pd.concat([got, df]).drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)
                break
            time.sleep(30 * (i + 1))
    return got


def missing_weekdays(df: pd.DataFrame, start: dt.date, end: dt.date) -> list[dt.date]:
    """Weekdays in [start, end) with no bar at all (closures included: a refetch of those is cheap and returns empty)."""
    have = set(pd.DatetimeIndex(df["ts_utc"]).date)
    days = (start + dt.timedelta(days=i) for i in range((end - start).days))
    return [d for d in days if d.weekday() < 5 and d not in have]


def month_bars(start: dt.date, end: dt.date, tmp: Path, attempts: int = 2,
               inst: Instrument | None = None) -> pd.DataFrame | None:
    for a in range(attempts):
        bid = download_side(start, end, "bid", tmp, inst=inst)
        ask = download_side(start, end, "ask", tmp, inst=inst)
        if (bid is None) != (ask is None) and a == attempts - 1:
            # one side never came back: impute it from the other (flagged) rather than lose the month
            have = bid if bid is not None else ask
            assert have is not None
            bid, ask = (bid if bid is not None else have.iloc[0:0]), (ask if ask is not None else have.iloc[0:0])
        if bid is not None and ask is not None and len(bid) + len(ask) > 0:
            m = bid.merge(ask, on="ts_utc", suffixes=("_bid", "_ask"), how="inner")
            imputed = ""
            if len(m) < 0.5 * max(len(bid), len(ask)):
                # one side is patchy on Dukascopy's end (seen for 2025-02): keep the fuller side and impute the other
                # from the median spread where both exist, flagged so research can exclude it if it matters.
                base, other = (bid, "ask") if len(bid) >= len(ask) else (ask, "bid")
                spread = float((m["close_ask"] - m["close_bid"]).median()) if len(m) > 100 else 0.25
                sign = 1 if other == "ask" else -1
                m = base.rename(columns={c: f"{c}_{'bid' if other == 'ask' else 'ask'}" for c in ("open", "high", "low", "close", "volume")})
                for c in ("open", "high", "low", "close"):
                    m[f"{c}_{other}"] = m[f"{c}_{'bid' if other == 'ask' else 'ask'}"] + sign * spread
                m[f"volume_{other}"] = m[f"volume_{'bid' if other == 'ask' else 'ask'}"]
                imputed = other
                print(f"  {start:%Y-%m}: {other} side patchy ({len(bid)} bid / {len(ask)} ask rows); imputed with spread {spread:.2f}", flush=True)
            out = pd.DataFrame({
                "ts_utc": m["ts_utc"],
                "bid_open": m["open_bid"], "bid_high": m["high_bid"], "bid_low": m["low_bid"], "bid_close": m["close_bid"],
                "ask_open": m["open_ask"], "ask_high": m["high_ask"], "ask_low": m["low_ask"], "ask_close": m["close_ask"],
                # Dukascopy XAUUSD volumes are fractional: an int cast truncated most minutes to 0, which made every
                # tick-volume feature constant. Features only use ratios and z-scores, so the scale does not matter.
                "tick_count": m["volume_bid"].fillna(0.0).astype(float).round(6),
            })
            out["spread_mean"] = (out["ask_close"] - out["bid_close"]).clip(lower=0)
            out["spread_max"] = (out["ask_high"] - out["bid_low"]).clip(lower=0)
            out = out[(out["bid_close"] > 0) & (out["ask_close"] >= out["bid_close"])]
            out = out[DEFAULT_SESSIONS.is_open(pd.DatetimeIndex(out["ts_utc"]))]
            out.insert(1, "visible_at", out["ts_utc"] + pd.Timedelta(minutes=1))
            out = out[BAR_COLUMNS].sort_values("ts_utc").drop_duplicates("ts_utc").reset_index(drop=True)
            out["dq_flag"] = f"warning:{imputed}_imputed" if imputed else ""
            # a minute without volume (the early archive's volume-less days) is kept, flagged: tick-volume features
            # must not read its 0 as a quiet minute
            out.loc[(out["tick_count"] <= 0) & (out["dq_flag"] == ""), "dq_flag"] = "warning:no_volume"
            days = (end - start).days
            expected_full = days >= 27
            if len(out) >= MIN_BARS_PER_FULL_MONTH or (not expected_full and len(out) > 1000):
                return out
            if a == attempts - 1 and len(out) >= MIN_BARS_PARTIAL_MONTH:
                # consistent shortfall across attempts = a gap in Dukascopy's archive, not throttling: keep, flagged
                out["dq_flag"] = out["dq_flag"].where(out["dq_flag"] != "", "warning:partial_month")
                print(f"  {start:%Y-%m}: accepting partial month with {len(out):,} bars (flagged)", flush=True)
                return out
            print(f"  {start:%Y-%m}: only {len(out):,} bars (attempt {a + 1}); retrying", flush=True)
        time.sleep(20 * (a + 1))
    return None


def _published_month(existing: pd.DataFrame, start: dt.date, end: dt.date) -> pd.DataFrame:
    ts = existing["ts_utc"]
    b = existing[(ts >= pd.Timestamp(start, tz="UTC")) & (ts < pd.Timestamp(end, tz="UTC"))]
    b = b.drop(columns=["source"], errors="ignore").copy()
    b["dq_flag"] = b["dq_flag"].fillna("") if "dq_flag" in b else ""
    return b


def _flagged(flags: pd.Series) -> pd.Series:
    return flags.fillna("").astype(str).str.contains("partial_month|ask_imputed|bid_imputed")


def merge_never_shrink(published: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """Combine a re-downloaded month with the published one, one UTC day at a time, so that no published day is
    ever replaced by one with fewer bars (G1/G5: a throttled re-download used to replace the whole month).

    Per day the fresh bars win only if they are strictly more, or equally many and they repair a flagged
    (imputed/partial) published day with an unflagged one; otherwise the published day is kept bar for bar, so
    first-published values stay reproducible. Days only one side has are taken from that side."""
    if published.empty:
        return fresh
    if fresh.empty:
        return published
    pday = published["ts_utc"].dt.floor("D")
    fday = fresh["ts_utc"].dt.floor("D")
    pn, fn = pday.value_counts(), fday.value_counts()
    pflag = _flagged(published["dq_flag"]).groupby(pday).any() if "dq_flag" in published else pd.Series(dtype=bool)
    fflag = _flagged(fresh["dq_flag"]).groupby(fday).any() if "dq_flag" in fresh else pd.Series(dtype=bool)
    take_fresh = set()
    for d, n in fn.items():
        p = int(pn.get(d, 0))
        if n > p or (n == p and bool(pflag.get(d, False)) and not bool(fflag.get(d, False))):
            take_fresh.add(d)
    out = pd.concat([published[~pday.isin(take_fresh)], fresh[fday.isin(take_fresh)]])
    kept = sorted(d for d in fn.index if d not in take_fresh and int(pn.get(d, 0)) > 0)
    if kept:
        print(f"  never-shrink: kept {len(kept)} published day(s) the re-download did not improve on", flush=True)
    return out.sort_values("ts_utc").drop_duplicates("ts_utc").reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("year", type=int)
    ap.add_argument("--symbol", default="xauusd", choices=sorted(INSTRUMENTS),
                    help="Dukascopy instrument (default xauusd); names the output file <symbol>_1m_dukascopy_<year>.parquet")
    ap.add_argument("--out", default=None)
    ap.add_argument("--tmp", default="raw/dukascopy")
    ap.add_argument("--report", default=None)
    ap.add_argument("--existing", default=None, help="previously published Parquet for this year; complete months are kept")
    ap.add_argument("--refetch-all", action="store_true", help="download every month; --existing is only a fallback")
    ap.add_argument("--retry-flagged", action="store_true",
                    help="also download closed months flagged partial/imputed (off by default: hours per month)")
    ap.add_argument("--months", default=None,
                    help="targeted refetch: comma-separated YYYY-MM; only this year's are downloaded, the rest are kept "
                         "from --existing (required)")
    args = ap.parse_args()
    inst = INSTRUMENTS[args.symbol]
    months = list(month_range(args.year))
    wanted: set[str] | None = None
    if args.months is not None:
        if args.refetch_all or args.retry_flagged:
            ap.error("--months is a targeted refetch; it cannot be combined with --refetch-all or --retry-flagged")
        try:
            listed = parse_months(args.months)
        except ValueError as e:
            ap.error(f"--months: {e}")
        wanted = {m for m in listed if m.startswith(f"{args.year:04d}-")}
        if not wanted:
            ap.error(f"--months lists no month of {args.year}: {','.join(listed)}")
        unknown = wanted - {f"{s:%Y-%m}" for s, _ in months}
        if unknown:
            ap.error(f"--months: {','.join(sorted(unknown))} has no completed day to download")
        if not (args.existing and Path(args.existing).exists()):
            # unlisted months are kept from the published file; without it the year would be published with only the
            # listed months
            print(f"REFUSED: --months needs the published {args.year} file (--existing); nothing downloaded", flush=True)
            return 1
    existing = None
    if args.existing and Path(args.existing).exists():
        existing = pd.read_parquet(args.existing)
        existing["ts_utc"] = pd.to_datetime(existing["ts_utc"], utc=True)
        print(f"existing: {len(existing):,} bars from {args.existing}", flush=True)
    if wanted is not None:
        todo = wanted
        print(f"targeted refetch of {','.join(sorted(wanted))}; every other month is kept as published", flush=True)
    elif args.refetch_all:
        todo = {f"{m:%Y-%m}" for m, _ in months}
    else:
        todo = months_to_fetch(existing, months, retry_flagged=args.retry_flagged)
    frames, rows = [], []
    for start, end in months:
        k = f"{start:%Y-%m}"
        t0 = time.time()
        if inst.history_start is not None and end <= inst.history_start:
            # the archive has nothing this early: asking cost hours of retries per month (issue #108) and a MISSING
            rows.append((k, 0, "before_history", "0s"))
            print(f"{k}: before {inst.code}'s first archive day {inst.history_start}; not downloaded", flush=True)
            continue
        if k not in todo:
            assert existing is not None   # months_to_fetch keeps a month only when a previous file has it
            kept = _published_month(existing, start, end)
            rows.append((k, len(kept), "kept", "0s"))
            frames.append(kept)
            print(f"{k}: kept {len(kept):,} bars", flush=True)
            continue
        first = max(start, inst.history_start) if inst.history_start is not None else start
        b = month_bars(first, end, Path(args.tmp), inst=inst)
        download_failed = b is None
        if existing is not None:
            # never publish a day with fewer bars than before: a failed download keeps the published month, a
            # short one keeps every published day it does not improve on (also under --refetch-all)
            old = _published_month(existing, start, end)
            if b is None:
                b = old if len(old) else None
            else:
                b = merge_never_shrink(old, b)
        n = 0 if b is None else len(b)
        median = float(b["bid_close"].median()) if b is not None and len(b) else float("nan")
        if b is None:
            status = "MISSING"
        elif not inst.price_ok(median):
            # a wrong point size or a foreign series: never published (exit 2 opens the failure issue)
            status = "SCALE"
            print(f"  {k}: median bid {median:.4f} is outside {inst.code}'s band {inst.price_band}; month NOT published",
                  flush=True)
        elif download_failed:
            # the download returned nothing and the published month was kept: not "ok" (2026-10 was reported ok
            # while every day after the 5th was missing)
            status = "stale"
        elif (b["dq_flag"] == "warning:partial_month").any():
            status = "partial"
        elif b["dq_flag"].astype(str).str.endswith("_imputed").any():
            status = "imputed"
        else:
            status = "ok"
        rows.append((k, n, status, f"{time.time() - t0:.0f}s"))
        print(f"{k}: {n:,} bars [{time.time() - t0:.0f}s]", flush=True)
        if b is not None and status != "SCALE":
            frames.append(b)
    report = "| month | 1m bars | status | time |\n|---|---:|---|---|\n" + "\n".join(f"| {m} | {n:,} | {s} | {t} |" for m, n, s, t in rows)
    n_ok = sum(1 for _, _, s, _ in rows if s in ("ok", "kept", "imputed"))
    n_due = sum(1 for _, _, s, _ in rows if s != "before_history")
    report += f"\n\n{inst.code}: {n_ok}/{n_due} months complete; " + ", ".join(
        f"{s}: {sum(1 for r in rows if r[2] == s)}"
        for s in ("ok", "kept", "imputed", "partial", "stale", "MISSING", "SCALE", "before_history"))
    if args.report:
        Path(args.report).write_text(report)
    print(report)
    if not frames:
        return 1
    out = pd.concat(frames).drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)
    out = out[[c for c in BAR_COLUMNS] + ["dq_flag"]]
    flags = out.pop("dq_flag").fillna("").astype(str)
    out, dq = check_bars(out)
    out["dq_flag"] = [a if a else b for a, b in zip(flags.to_numpy(), out["dq_flag"].to_numpy())]
    out, held = quarantine(out)                   # error rows (bid > ask) are not published
    if not held.empty:
        held_path = (args.out or f"{args.symbol}_1m_dukascopy_{args.year}.parquet").replace(".parquet", "_quarantine.parquet")
        held.to_parquet(held_path, compression="zstd", index=False)
        print(f"QUARANTINED {len(held):,} bars with an error flag -> {held_path}")
    out["source"] = "dukascopy"
    path = args.out or f"{args.symbol}_1m_dukascopy_{args.year}.parquet"
    # write then rename: a run killed mid-write (job timeout) never leaves a truncated file for the publish step
    tmp_path = f"{path}.tmp"
    out.to_parquet(tmp_path, compression="zstd", index=False)
    os.replace(tmp_path, path)
    print(f"wrote {path}: {len(out):,} bars, {len(dq)} dq events")
    shutil.rmtree(args.tmp, ignore_errors=True)
    missing = [m for m, n, s, t in rows if s in ("MISSING", "SCALE")]
    return 0 if not missing else 2


if __name__ == "__main__":
    sys.exit(main())
