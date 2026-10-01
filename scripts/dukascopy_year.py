"""Download one year of XAUUSD 1-minute bars from Dukascopy (bid and ask separately, via dukascopy-node),
merge into goldbot's bar schema, validate coverage month by month, and write one Parquet for the year.

Why m1 rather than ticks: Dukascopy serves m1 as one file per day per price side (2 requests/day) versus
24 hourly tick files; the tick route gets throttled on shared runners and silently returns gaps. Spread on
these bars is ask_close - bid_close per minute, which is adequate for price-structure features; the
execution-cost model uses the brokers' own ticks (design: Data architecture).

  python scripts/dukascopy_year.py 2025 [--out xauusd_1m_dukascopy_2025.parquet] [--report report.md]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.calendar import DEFAULT_SESSIONS  # noqa: E402
from goldbot.data.quality import check_bars  # noqa: E402
from goldbot.data.resample import BAR_COLUMNS  # noqa: E402

MIN_BARS_PER_FULL_MONTH = 15_000   # ~20 trading days x 1,380 minutes = 27,600; accept down to 15,000


def month_range(year: int):
    today = dt.date.today()
    for m in range(1, 13):
        start = dt.date(year, m, 1)
        if start > today:
            break
        end = (dt.date(year + 1, 1, 1) if m == 12 else dt.date(year, m + 1, 1)) - dt.timedelta(days=1)
        yield start, min(end, today)


def _run(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def download_side(start: dt.date, end: dt.date, side: str, outdir: Path) -> pd.DataFrame | None:
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = ["npx", "-y", "dukascopy-node", "-i", "xauusd", "-from", start.isoformat(), "-to", end.isoformat(),
           "-t", "m1", "-p", side, "-v", "true", "-f", "csv", "-dir", str(outdir), "-bs", "4", "-bp", "1500", "-r", "6", "-ch", "0"]
    r = _run(cmd)
    files = sorted(outdir.glob(f"xauusd-m1-{side}-{start.isoformat()}-*.csv"), key=os.path.getmtime)
    if not files or files[-1].stat().st_size < 1000:
        print(f"  {side} {start:%Y-%m}: no data (rc={r.returncode}) {r.stderr[-300:]}", flush=True)
        return None
    df = pd.read_csv(files[-1])
    df.columns = [c.lower() for c in df.columns]
    df["ts_utc"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    files[-1].unlink(missing_ok=True)
    return df[["ts_utc", "open", "high", "low", "close", "volume"]]


def month_bars(start: dt.date, end: dt.date, tmp: Path, attempts: int = 3) -> pd.DataFrame | None:
    for a in range(attempts):
        bid = download_side(start, end, "bid", tmp)
        ask = download_side(start, end, "ask", tmp)
        if bid is not None and ask is not None:
            m = bid.merge(ask, on="ts_utc", suffixes=("_bid", "_ask"), how="inner")
            imputed = False
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
                imputed = True
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
            out["dq_flag"] = "warning:ask_imputed" if imputed else ""
            days = (end - start).days + 1
            expected_full = days >= 27
            if len(out) >= MIN_BARS_PER_FULL_MONTH or (not expected_full and len(out) > 1000):
                return out
            print(f"  {start:%Y-%m}: only {len(out):,} bars (attempt {a + 1}); retrying", flush=True)
        time.sleep(20 * (a + 1))
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("year", type=int)
    ap.add_argument("--out", default=None)
    ap.add_argument("--tmp", default="raw/dukascopy")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()
    frames, rows = [], []
    for start, end in month_range(args.year):
        t0 = time.time()
        b = month_bars(start, end, Path(args.tmp))
        n = 0 if b is None else len(b)
        rows.append((f"{start:%Y-%m}", n, "ok" if b is not None else "MISSING", f"{time.time() - t0:.0f}s"))
        print(f"{start:%Y-%m}: {n:,} bars [{time.time() - t0:.0f}s]", flush=True)
        if b is not None:
            frames.append(b)
    report = "| month | 1m bars | status | time |\n|---|---:|---|---|\n" + "\n".join(f"| {m} | {n:,} | {s} | {t} |" for m, n, s, t in rows)
    if args.report:
        Path(args.report).write_text(report)
    print(report)
    if not frames:
        return 1
    out = pd.concat(frames).drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)
    flags = out.pop("dq_flag")
    out, dq = check_bars(out)
    out["dq_flag"] = [a if a else b for a, b in zip(flags.values, out["dq_flag"].values)]
    out["source"] = "dukascopy"
    path = args.out or f"xauusd_1m_dukascopy_{args.year}.parquet"
    out.to_parquet(path, compression="zstd", index=False)
    print(f"wrote {path}: {len(out):,} bars, {len(dq)} dq events")
    shutil.rmtree(args.tmp, ignore_errors=True)
    missing = [m for m, n, s, t in rows if s == "MISSING"]
    return 0 if not missing else 2


if __name__ == "__main__":
    sys.exit(main())
