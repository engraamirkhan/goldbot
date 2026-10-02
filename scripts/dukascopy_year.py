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

With --existing (last published Parquet for the year) months that are already complete are kept and only
missing, partial or still-open months are downloaded again.

  python scripts/dukascopy_year.py 2025 [--out xauusd_1m_dukascopy_2025.parquet] [--report report.md]
                                        [--existing xauusd_1m_dukascopy_2025.parquet]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.calendar import DEFAULT_SESSIONS  # noqa: E402
from goldbot.data.quality import check_bars  # noqa: E402
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


def months_to_fetch(existing: pd.DataFrame | None, months: list[tuple[dt.date, dt.date]], today: dt.date | None = None) -> set[str]:
    """Month keys (YYYY-MM) that need downloading: absent, short or flagged partial in `existing`, or still open."""
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
        if still_open or counts.get(k, 0) < MIN_BARS_PER_FULL_MONTH or k in partial:
            need.add(k)
    return need


def _run(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _fetch(start: dt.date, end: dt.date, side: str, outdir: Path, salvage: bool = False) -> tuple[pd.DataFrame | None, str]:
    """One dukascopy-node call for [start, end). Returns (frame or None, error text)."""
    outdir.mkdir(parents=True, exist_ok=True)
    for f in outdir.glob(f"xauusd-m1-{side}-*.csv"):
        f.unlink(missing_ok=True)
    cmd = ["npx", "-y", "dukascopy-node", "-i", "xauusd", "-from", start.isoformat(), "-to", end.isoformat(),
           "-t", "m1", "-p", side, "-v", "true", "-f", "csv", "-dir", str(outdir),
           "-bs", "2", "-bp", "1500", "-r", "5", "-rp", "4000"] + (["-fr"] if salvage else [])
    try:
        r = _run(cmd)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    files = sorted(outdir.glob(f"xauusd-m1-{side}-*.csv"), key=os.path.getmtime)
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


def download_side(start: dt.date, end: dt.date, side: str, outdir: Path, attempts: int = 4) -> pd.DataFrame | None:
    frames = []
    for a, b in week_chunks(start, end):
        df, err = None, ""
        for i in range(attempts):
            df, err = _fetch(a, b, side, outdir, salvage=(i == attempts - 1))
            if df is not None:
                break
            time.sleep(10 * (i + 1))
        if df is None:
            print(f"  {side} {a}..{b}: no data after {attempts} attempts ({err})", flush=True)
        else:
            frames.append(df)
    if not frames:
        return None
    return pd.concat(frames).drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)


def month_bars(start: dt.date, end: dt.date, tmp: Path, attempts: int = 2) -> pd.DataFrame | None:
    for a in range(attempts):
        bid = download_side(start, end, "bid", tmp)
        ask = download_side(start, end, "ask", tmp)
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
                "tick_count": m["volume_bid"].fillna(0).astype(int),
            })
            out["spread_mean"] = (out["ask_close"] - out["bid_close"]).clip(lower=0)
            out["spread_max"] = (out["ask_high"] - out["bid_low"]).clip(lower=0)
            out = out[(out["bid_close"] > 0) & (out["ask_close"] >= out["bid_close"])]
            out = out[DEFAULT_SESSIONS.is_open(pd.DatetimeIndex(out["ts_utc"]))]
            out.insert(1, "visible_at", out["ts_utc"] + pd.Timedelta(minutes=1))
            out = out[BAR_COLUMNS].sort_values("ts_utc").drop_duplicates("ts_utc").reset_index(drop=True)
            out["dq_flag"] = f"warning:{imputed}_imputed" if imputed else ""
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("year", type=int)
    ap.add_argument("--out", default=None)
    ap.add_argument("--tmp", default="raw/dukascopy")
    ap.add_argument("--report", default=None)
    ap.add_argument("--existing", default=None, help="previously published Parquet for this year; complete months are kept")
    ap.add_argument("--refetch-all", action="store_true", help="download every month; --existing is only a fallback")
    args = ap.parse_args()
    months = list(month_range(args.year))
    existing = None
    if args.existing and Path(args.existing).exists():
        existing = pd.read_parquet(args.existing)
        existing["ts_utc"] = pd.to_datetime(existing["ts_utc"], utc=True)
        print(f"existing: {len(existing):,} bars from {args.existing}", flush=True)
    todo = {f"{m:%Y-%m}" for m, _ in months} if args.refetch_all else months_to_fetch(existing, months)
    frames, rows = [], []
    for start, end in months:
        k = f"{start:%Y-%m}"
        t0 = time.time()
        if k not in todo:
            assert existing is not None   # months_to_fetch keeps a month only when a previous file has it
            kept = _published_month(existing, start, end)
            rows.append((k, len(kept), "kept", "0s"))
            frames.append(kept)
            print(f"{k}: kept {len(kept):,} bars", flush=True)
            continue
        b = month_bars(start, end, Path(args.tmp))
        if b is None and existing is not None:
            # download failed again: keep whatever was published before rather than lose it
            old = _published_month(existing, start, end)
            b = old if len(old) else None
        n = 0 if b is None else len(b)
        if b is None:
            status = "MISSING"
        elif (b["dq_flag"] == "warning:partial_month").any():
            status = "partial"
        elif b["dq_flag"].astype(str).str.endswith("_imputed").any():
            status = "imputed"
        else:
            status = "ok"
        rows.append((k, n, status, f"{time.time() - t0:.0f}s"))
        print(f"{k}: {n:,} bars [{time.time() - t0:.0f}s]", flush=True)
        if b is not None:
            frames.append(b)
    report = "| month | 1m bars | status | time |\n|---|---:|---|---|\n" + "\n".join(f"| {m} | {n:,} | {s} | {t} |" for m, n, s, t in rows)
    n_ok = sum(1 for _, _, s, _ in rows if s in ("ok", "kept", "imputed"))
    report += f"\n\n{n_ok}/{len(rows)} months complete; " + ", ".join(f"{s}: {sum(1 for r in rows if r[2] == s)}" for s in ("ok", "kept", "imputed", "partial", "MISSING"))
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
    out["source"] = "dukascopy"
    path = args.out or f"xauusd_1m_dukascopy_{args.year}.parquet"
    out.to_parquet(path, compression="zstd", index=False)
    print(f"wrote {path}: {len(out):,} bars, {len(dq)} dq events")
    shutil.rmtree(args.tmp, ignore_errors=True)
    missing = [m for m, n, s, t in rows if s == "MISSING"]
    return 0 if not missing else 2


if __name__ == "__main__":
    sys.exit(main())
