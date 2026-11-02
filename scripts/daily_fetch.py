"""Build the local daily research set (raw/daily/, git-ignored; never committed or published: data-local-only).

Conventions and loaders: goldbot/data/daily.py (21:00 UTC close, mid prices, hard cutoff 2025-10-01).

Steps (each resumable and idempotent; run in any order, `manifest` last):
  fetch     Dukascopy hourly candles, bid and ask, one file per instrument-month-side, cached under raw/daily/_cache/
            (a 404 is cached as an empty `.404` marker). One request at a time per thread, a polite pause, exponential
            backoff on HTTP 429/503 and dropped connections; a month that still fails is skipped and the next run asks
            for it again. Hourly rather than 1m: one request per month-side instead of ~22 per month-side, and the
            archive throttles this host hard (cross_asset_fetch.py measured 6-75 s per 1m day file).
  build     cached months -> raw/daily/<symbol>.parquet (daily mid bars, n_hours), with the price-band scale check.
  xauusd    raw/xauusd_1m_dukascopy_<year>.parquet (the XAUUSD lane's files, read only) -> raw/daily/xauusd.parquet.
  fred      FRED DTB3, DFII10, DTWEXBGS, DTWEXB, T10YIE -> raw/daily/fred_<ID>.parquet (goldbot.data.macro conventions;
            rows whose available_utc is at or after the cutoff are dropped).
  yahoo     Yahoo daily bars (default ^GSPC, the spec 033 fallback; --tickers for others) -> raw/daily/yahoo_<ticker>.parquet,
            available_utc = date 21:15 UTC for ^GSPC (prereg), 23:00 UTC otherwise.
  manifest  sha256 + quality table per file -> --out (default ~/tmp/daily_manifest.json), markdown table on stdout.

  uv run python scripts/daily_fetch.py fetch [--symbols XAGUSD EURUSD ...] [--threads 2] [--pause 0.5]
  uv run python scripts/daily_fetch.py build [--symbols ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import lzma
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.daily import (  # noqa: E402
    CUTOFF,
    DAILY_ROOT,
    FRED_DAILY,
    OECD_3M_RATES,
    POS1_SYMBOLS,
    SPECS,
    assert_before_cutoff,
    bars_to_daily,
    check_daily,
    decode_hourly,
    hourly_mid,
    minutes_to_daily,
    months,
    oecd_monthly_available_utc,
)

DATAFEED = "https://datafeed.dukascopy.com/datafeed"
START = dt.date(2003, 1, 1)
UA = {"User-Agent": "goldbot-data/1"}
_lock = threading.Lock()


def cache_path(root: Path, code: str, month: dt.date, side: str) -> Path:
    return root / "_cache" / code / f"{month:%Y-%m}_{side}.bi5"


def hourly_url(code: str, month: dt.date, side: str) -> str:
    assert_before_cutoff(month, "month")
    return f"{DATAFEED}/{code}/{month.year:04d}/{month.month - 1:02d}/{side}_candles_hour_1.bi5"


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def fetch_one(root: Path, code: str, month: dt.date, side: str, pause: float, tries: int = 7) -> str:
    """'cached' | 'ok' | '404' | 'failed: ...'"""
    p = cache_path(root, code, month, side)
    if p.exists() or p.with_suffix(".404").exists():
        return "cached"
    url = hourly_url(code, month, side)
    err = ""
    for k in range(tries):
        time.sleep(pause)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:  # noqa: S310
                raw = bytes(r.read())
            if raw:
                lzma.decompress(raw)          # a truncated body or an error page fails here, and is asked for again
            _write_atomic(p, raw)
            return "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                _write_atomic(p.with_suffix(".404"), b"")
                return "404"
            err = f"HTTP {e.code}"
        except (OSError, lzma.LZMAError) as e:
            err = str(e)[:80]
        time.sleep(min(5.0 * 2 ** k, 240.0))
    return f"failed: {err}"


def cmd_fetch(args: argparse.Namespace) -> int:
    root: Path = args.root
    tasks = []
    for sym in args.symbols:
        spec = SPECS[sym]
        for m in months(max(START, spec.hourly_start)):
            for side in ("BID", "ASK"):
                tasks.append((spec.code, m, side))
    todo = [t for t in tasks if not (cache_path(root, *t).exists() or cache_path(root, *t).with_suffix(".404").exists())]
    print(f"{len(tasks):,} month-sides, {len(todo):,} to fetch", flush=True)
    failed: list[str] = []
    done = 0

    def run(t: tuple[str, dt.date, str]) -> None:
        nonlocal done
        res = fetch_one(root, *t, pause=args.pause)
        with _lock:
            done += 1
            if res.startswith("failed"):
                failed.append(f"{t[0]} {t[1]:%Y-%m} {t[2]}: {res}")
            if done % 100 == 0 or res.startswith("failed"):
                print(f"  {done:,}/{len(todo):,} {t[0]} {t[1]:%Y-%m} {t[2]} {res}", flush=True)

    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        list(ex.map(run, todo))
    print(f"done; {len(failed)} failed (re-run to resume)", flush=True)
    for f in failed:
        print("  " + f)
    return 1 if failed else 0


def build_symbol(root: Path, sym: str) -> tuple[pd.DataFrame | None, list[str]]:
    spec = SPECS[sym]
    frames, notes = [], []
    for m in months(max(START, spec.hourly_start)):
        sides = {}
        for side in ("BID", "ASK"):
            p = cache_path(root, spec.code, m, side)
            if p.exists():
                raw = p.read_bytes()
                sides[side] = decode_hourly(lzma.decompress(raw), m, spec.point) if raw else None
            elif p.with_suffix(".404").exists():
                sides[side] = None
            else:
                notes.append(f"{m:%Y-%m} {side} not fetched")
        bid, ask = sides.get("BID"), sides.get("ASK")
        if bid is None or ask is None or bid.empty or ask.empty:
            if (bid is None) != (ask is None):
                notes.append(f"{m:%Y-%m} one side missing")
            continue
        mid = hourly_mid(bid, ask)
        med = float(mid["close"].median())
        if not spec.price_ok(med):
            raise SystemExit(f"{sym} {m:%Y-%m}: median close {med:.4f} outside {spec.price_band} (point size?)")
        frames.append(mid)
    if not frames:
        return None, notes
    hourly = pd.concat(frames).drop_duplicates("ts_utc").sort_values("ts_utc")
    return bars_to_daily(hourly, "n_hours", f"dukascopy_h1:{spec.code}"), notes


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.parquet")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def cmd_build(args: argparse.Namespace) -> int:
    for sym in args.symbols:
        df, notes = build_symbol(args.root, sym)
        if df is None:
            print(f"{sym}: no data ({'; '.join(notes[:3])})")
            continue
        check_daily(df, sym)
        _write_parquet(df, args.root / f"{sym.lower()}.parquet")
        print(f"{sym}: {len(df):,} days {df['date'].min():%Y-%m-%d}..{df['date'].max():%Y-%m-%d}"
              + (f"; {len(notes)} notes: {'; '.join(notes[:3])}" if notes else ""), flush=True)
    return 0


def cmd_xauusd(args: argparse.Namespace) -> int:
    src = args.root.parent
    files = sorted(p for p in src.glob("xauusd_1m_dukascopy_*.parquet") if int(p.stem[-4:]) <= 2025)
    cols = ["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close",
            "dq_flag"]
    frames = []
    for p in files:
        b = pd.read_parquet(p, columns=cols)
        b = b[~b["dq_flag"].fillna("").astype(str).str.startswith("error")]
        b = b[pd.to_datetime(b["ts_utc"], utc=True) < CUTOFF]
        frames.append(minutes_to_daily(b))
        print(f"  {p.name}: {len(b):,} minutes", flush=True)
    df = pd.concat(frames).groupby("date", as_index=False).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "spread_close": "last", "n_minutes": "sum",
         "visible_at": "first", "source": "first"})
    check_daily(df, "XAUUSD")
    _write_parquet(df, args.root / "xauusd.parquet")
    print(f"XAUUSD: {len(df):,} days {df['date'].min():%Y-%m-%d}..{df['date'].max():%Y-%m-%d} from {len(files)} files")
    return 0


def cmd_fred(args: argparse.Namespace) -> int:
    from goldbot.data.macro import fetch_fred_csv, fred_frame
    today = pd.Timestamp.now(tz="UTC").normalize()
    rc = 0
    oecd = set(OECD_3M_RATES.values())
    for sid in args.series or [*FRED_DAILY, *OECD_3M_RATES.values()]:
        try:
            obs = fetch_fred_csv(sid, "2002-01-01", end="2025-09-30")
        except RuntimeError as e:
            print(f"FAILED {e}")
            rc = 1
            continue
        obs = obs[obs["value_date"] < CUTOFF.tz_localize(None)]
        f = fred_frame(sid, obs, today)
        if sid in oecd:     # monthly MEI average: the conservative release-lag stamp, not the daily H.15 rule
            f["available_utc"] = oecd_monthly_available_utc(f["value_date"])
            f["ts_utc"] = f["available_utc"]
        f = f[f["available_utc"] < CUTOFF].reset_index(drop=True)
        _write_parquet(f, args.root / f"fred_{sid}.parquet")
        print(f"{sid}: {len(f):,} rows {f['value_date'].min():%Y-%m-%d}..{f['value_date'].max():%Y-%m-%d}", flush=True)
        time.sleep(2)
    return rc


def yahoo_daily(ticker: str, available_after_close: pd.Timedelta) -> pd.DataFrame:
    """Yahoo chart API daily bars for `ticker`, 2002-01-01 through 2025-09-30 (period2 is before the cutoff), dated in
    the exchange's local date; available_utc = date + `available_after_close` (UTC)."""
    p1 = int(pd.Timestamp("2002-01-01", tz="UTC").timestamp())
    p2 = int(pd.Timestamp("2025-09-30 23:59:59", tz="UTC").timestamp())
    assert_before_cutoff(pd.Timestamp(p2, unit="s"), "period2")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?period1={p1}&period2={p2}"
           "&interval=1d&events=history")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as r:  # noqa: S310
        js = json.loads(r.read())
    res = js["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    tz = res["meta"].get("exchangeTimezoneName") or "America/New_York"
    ts = pd.to_datetime(pd.Series(res["timestamp"]), unit="s", utc=True).dt.tz_convert(tz)
    df = pd.DataFrame({"date": pd.DatetimeIndex(ts.dt.date.astype(str)).as_unit("ns"), "open": q["open"],
                       "high": q["high"], "low": q["low"], "close": q["close"]}).dropna()
    df = df[df["date"] < CUTOFF.tz_localize(None)].drop_duplicates("date").reset_index(drop=True)
    df["available_utc"] = (df["date"] + available_after_close).dt.tz_localize("UTC")
    df = df[df["available_utc"] < CUTOFF].reset_index(drop=True)
    df["source"] = f"yahoo:{ticker}"
    return df


def cmd_yahoo(args: argparse.Namespace) -> int:
    """^GSPC (spec 033 fallback): available 21:15 UTC on the day (prereg). Anything else: 23:00 UTC, after every US
    futures settlement in both DST regimes."""
    for t in args.tickers:
        lag = pd.Timedelta(hours=21, minutes=15) if t == "^GSPC" else pd.Timedelta(hours=23)
        df = yahoo_daily(t, lag)
        name = "yahoo_" + "".join(ch for ch in t.lower() if ch.isalnum()) + ".parquet"
        _write_parquet(df, args.root / name)
        print(f"{t}: {len(df):,} days {df['date'].min():%Y-%m-%d}..{df['date'].max():%Y-%m-%d} -> {name}", flush=True)
        time.sleep(2)
    return 0


def quality(df: pd.DataFrame, date_col: str = "date") -> dict[str, object]:
    d = pd.DatetimeIndex(pd.to_datetime(df[date_col])).normalize()
    wk = pd.bdate_range(d.min(), d.max())
    missing = wk.difference(d)
    gaps = (d[1:] - d[:-1]).days.to_numpy() if len(d) > 1 else np.zeros(1, dtype="int64")
    out: dict[str, object] = {"first": f"{d.min():%Y-%m-%d}", "last": f"{d.max():%Y-%m-%d}", "days": int(len(d)),
                              "missing_weekdays": int(len(missing)), "max_gap_days": int(gaps.max())}
    for c in ("n_hours", "n_minutes"):
        if c in df:
            out["short_days"] = int((df[c] < (6 if c == "n_hours" else 360)).sum())
    if len(d) > 1 and float(np.median(gaps)) >= 28:      # a monthly series: count missing months, not weekdays
        mons = pd.period_range(d.min(), d.max(), freq="M")
        out["missing_weekdays"] = 0
        out["missing_months"] = int(len(mons) - len(set(d.to_period("M"))))
        return out
    if gaps.max() > 4:
        i = int(gaps.argmax())
        out["max_gap_at"] = f"{d[i]:%Y-%m-%d}..{d[i + 1]:%Y-%m-%d}"
    if {"open", "close"} <= set(df.columns) and len(df) > 1:
        # close -> next open jumps: futures-based CFDs (WTI, Brent, softs) roll with a price gap the bars keep
        jump = (df["open"].iloc[1:].to_numpy() / df["close"].iloc[:-1].to_numpy() - 1.0)
        out["open_jumps_gt_3pct"] = int((np.abs(jump) > 0.03).sum())
    return out


def cmd_manifest(args: argparse.Namespace) -> int:
    entries = {}
    for p in sorted(args.root.glob("*.parquet")):
        df = pd.read_parquet(p)
        q = quality(df, "value_date" if p.name.startswith("fred_") else "date")
        q.update({"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size, "rows": len(df),
                  "source": str(df["source"].iloc[0]) if "source" in df else "fred:" + p.stem[5:]})
        entries[p.name] = q
    man = {"built_utc": pd.Timestamp.now(tz="UTC").isoformat(), "cutoff_utc": CUTOFF.isoformat(),
           "root": str(args.root), "files": entries}
    out = Path(os.path.expanduser(args.out))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(man, indent=1))
    print("| file | first | last | days | missing weekdays | max gap (d) | short days | open jumps >3% | sha256 (12) |")
    print("|---|---|---|---:|---:|---:|---:|---:|---|")
    for n, q in entries.items():
        print(f"| {n} | {q['first']} | {q['last']} | {q['days']:,} | {q['missing_weekdays']} | {q['max_gap_days']}"
              f"{' (' + str(q['max_gap_at']) + ')' if 'max_gap_at' in q else ''} | {q.get('short_days', '')} | "
              f"{q.get('open_jumps_gt_3pct', '')} | "
              f"{str(q['sha256'])[:12]} |")
    print(f"wrote {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["fetch", "build", "xauusd", "fred", "yahoo", "manifest"])
    ap.add_argument("--symbols", nargs="*", default=list(POS1_SYMBOLS))
    ap.add_argument("--root", type=Path, default=DAILY_ROOT)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--pause", type=float, default=0.5)
    ap.add_argument("--out", default="~/tmp/daily_manifest.json")
    ap.add_argument("--tickers", nargs="*", default=["^GSPC"])
    ap.add_argument("--series", nargs="*", default=None, help="fred step: these FRED ids only (default: all)")
    args = ap.parse_args()
    if args.symbols == ["all"]:
        args.symbols = list(SPECS)
    unknown = [s for s in args.symbols if s not in SPECS]
    if unknown:
        ap.error(f"unknown symbols {unknown}; known: {sorted(SPECS)}")
    return {"fetch": cmd_fetch, "build": cmd_build, "xauusd": cmd_xauusd, "fred": cmd_fred, "yahoo": cmd_yahoo,
            "manifest": cmd_manifest}[args.step](args)


if __name__ == "__main__":
    sys.exit(main())
