"""HistData free M1 (bid) history for the owner's instruments (D2, lane W1-E), local only: never committed or published.

Source: histdata.com's free M1 ASCII archive, one zip per pair-year through the site's own download form (the route of
scripts/cross_asset_fetch.py). Stamps are New York local time WITH daylight saving (repo finding 2026-11-01); they are
converted to UTC here and re-verified per instrument-year by `check` against the Dukascopy hourly cache.

Layout under <root>/histdata_m1/ (root = the git-ignored raw/; ADR 0048, data-local-only):
  _zips/<CODE>_<year>.zip                the source archive as served (kept: lineage, re-parse without a download)
  <KEY>/<key>_1m_bid_<year>.parquet      research partition: ts_utc, visible_at, bid_open..bid_close, source, dq_flag;
                                         strictly before the holdout cut 2025-10-01 (quant R10: sealed minutes are
                                         dropped and counted, never stored)
  _quarantine/<KEY>/...                  error rows (broken OHLC, duplicate minutes) and whole years
                                         that failed the clock check; kept, never loaded
  MANIFEST.jsonl                         one line per instrument-year: status, rows, first/last ts, sha256 of zip and
                                         parquet, sealed minutes dropped, quarantined rows
  SHA256SUMS                             `sha256sum -c` format for the brain inbox copy (D11)
  _reports/clock.csv, tiers.csv          clock check per instrument-year, R11 tier per instrument-month

Commands:
  uv run python scripts/histdata_m1.py pull  [--keys EURUSD,...] [--workers 3] [--root raw]
  uv run python scripts/histdata_m1.py check [--keys ...] [--workers 3] [--root raw]
  uv run python scripts/histdata_m1.py sums  [--root raw]
Resumable: a pair-year with a manifest line and its parquet on disk is skipped (re-running is a no-op); zips are cached.
Workers are niced processes; keep <= 3 on the Mac while research runs (D6: <= 8 workers in total).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import lzma
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.daily import decode_hourly  # noqa: E402
from goldbot.data.era_quality import (  # noqa: E402
    HOLDOUT_CUT,
    R11_V2,
    R11_V2_ID,
    R11_V21,
    R11_V21_ID,
    ClockCheck,
    HistorySource,
    clock_check,
    combine_legs,
    day_of,
    drop_holdout,
    epoch_ns_utc,
    gap_week_offsets,
    gap_week_score,
    grade_month_v2,
    history_sources,
    month_offsets,
    same_source_share,
    spike_confirmed,
    spike_mask,
)

HISTDATA = "https://www.histdata.com"
HD_PAGE = HISTDATA + "/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/{pair}/{year}"
UA = {"User-Agent": "goldbot-data/1"}
FIRST_YEAR = 2003            # D2: research history from 2003
LAST_YEAR = HOLDOUT_CUT.year  # 2025: its file holds Jan-Sep research minutes; the rest is dropped at the cut
PAUSE_S = 2.0                # per request per worker: polite to a free archive
THROTTLE_TRIES = 4           # an empty form token means throttled: wait 2, 4, 6 min, then give the year up for now
THROTTLE_WAIT_S = 120.0
EXTRA_POINTS = {"USDSEK": 100000.0}   # reference legs that are not owner instruments (ICE USDX formula)
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "raw"


# ------------------------------------------------------------------------------------------------------------ parse
TZ_RULES = ("ny", "eet-7")


def histdata_to_utc(local: pd.Series, rule: str = "ny") -> pd.Series:
    """HistData local stamps -> UTC; the DST switch hours (Sunday night, market shut) become NaT.
    "ny": New York time with US daylight saving (repo finding 2026-11-01; every year checked to 2008).
    "eet-7": an EET server clock (UTC+2/+3, EU daylight saving) minus 7 h, i.e. New York-like stamps that switch on
    the EU dates: the clock check found HistData on this rule in later years (+60 min in the weeks between the US and
    EU switches: EURUSD 2019-03-11..03-31 and 2019-10-28; XAUUSD 2023 the same). `check` picks the rule per
    instrument-year from the per-month clock offsets."""
    if rule == "eet-7":
        loc = (pd.DatetimeIndex(local) + pd.Timedelta(hours=7)).tz_localize("Europe/Helsinki", ambiguous="NaT",
                                                                          nonexistent="NaT")
    else:
        loc = pd.DatetimeIndex(local).tz_localize("America/New_York", ambiguous="NaT", nonexistent="NaT")
    return pd.Series(loc.tz_convert("UTC").as_unit("ns"), index=local.index)


def to_local(ts_utc: pd.Series, rule: str) -> pd.Series:
    """The HistData local stamp a UTC time came from under `rule` (exact inverse of histdata_to_utc)."""
    t = pd.DatetimeIndex(pd.to_datetime(ts_utc, utc=True))
    if rule == "eet-7":
        loc = t.tz_convert("Europe/Helsinki").tz_localize(None) - pd.Timedelta(hours=7)
    else:
        loc = t.tz_convert("America/New_York").tz_localize(None)
    return pd.Series(loc, index=ts_utc.index)


def reconvert(bars: pd.DataFrame, rule: str, stored: str = "ny") -> pd.DataFrame:
    """A year stored under `stored` re-stamped under `rule` (local stamps recovered exactly), re-sorted,
    de-duplicated and cut at the holdout again."""
    if rule == stored:
        return bars
    out = bars.assign(ts_utc=histdata_to_utc(to_local(bars["ts_utc"], stored), rule)).dropna(subset=["ts_utc"])
    out = out.sort_values("ts_utc", kind="stable").drop_duplicates("ts_utc").reset_index(drop=True)
    out["visible_at"] = out["ts_utc"] + pd.Timedelta(seconds=60)
    return drop_holdout(out)[0]


def parse_histdata(text: str | bytes, code: str) -> pd.DataFrame:
    """HistData ASCII M1 rows `YYYYMMDD HHMMSS;open;high;low;close;volume` (bid, bar open, New York time) -> goldbot's
    bid columns in UTC with visible_at (bar close: ts + 60 s, when the bar became knowable) and source."""
    raw = text.encode() if isinstance(text, str) else text
    f = pd.read_csv(io.BytesIO(raw), sep=";", header=None, names=["t", "o", "h", "l", "c", "v"],
                    dtype={"t": "str", "o": "float64", "h": "float64", "l": "float64", "c": "float64"})
    ts = histdata_to_utc(pd.to_datetime(f["t"], format="%Y%m%d %H%M%S"))
    out = pd.DataFrame({"ts_utc": ts, "bid_open": f["o"], "bid_high": f["h"], "bid_low": f["l"], "bid_close": f["c"]})
    out = out.dropna(subset=["ts_utc"]).sort_values("ts_utc", kind="stable").reset_index(drop=True)
    out.insert(1, "visible_at", out["ts_utc"] + pd.Timedelta(seconds=60))
    out["source"] = f"histdata:{code}"
    return out


def split_errors(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(kept rows with `dq_flag`, error rows with `dq_reason`). Errors, quarantined (kept on disk, never loaded):
    non-positive or broken OHLC (low above open/close, high below), duplicate minutes (the later copy). Warnings stay
    in the kept rows with a flag, never dropped: `warning:spike_15mad`, a reverting spike by era_quality.spike_mask
    per month. R11 says spikes are "removed, logged"; the removal is left to the reader (label factory) because the
    15-MAD rule also fires on genuine news minutes (smoke test 2026-11-25: EURUSD 2025 has 4-5 a month), and a
    removal at ingest could not be undone if the quant re-rules the definition."""
    reason = pd.Series("", index=df.index, dtype=object)
    px = df[["bid_open", "bid_high", "bid_low", "bid_close"]]
    bad = (px <= 0).any(axis=1) | (df["bid_low"] > px.min(axis=1)) | (df["bid_high"] < px.max(axis=1))
    reason[bad] = "ohlc"
    dup = df["ts_utc"].duplicated(keep="first") & (reason == "")
    reason[dup] = "duplicate_minute"
    kept = df[reason == ""].reset_index(drop=True)
    flag = pd.Series("", index=kept.index, dtype=object)
    month = pd.DatetimeIndex(kept["ts_utc"]).tz_localize(None).to_period("M")
    for _, idx in kept.groupby(month).groups.items():
        m = spike_mask(kept.loc[idx, "bid_close"])
        flag[m[m].index] = "warning:spike_15mad"
    kept["dq_flag"] = flag.astype(str)
    q = df[reason != ""].assign(dq_reason=reason[reason != ""])
    return kept, q.reset_index(drop=True)


def scale_errors(df: pd.DataFrame, src: HistorySource) -> list[str]:
    """Months whose median close lies outside the era band (a point-size error): the year is not written."""
    month = pd.DatetimeIndex(df["ts_utc"]).tz_localize(None).to_period("M")
    med = df.groupby(month)["bid_close"].median()
    return [f"{p} median={v:.5g} band={src.band(str(p))}" for p, v in med.items() if not src.price_ok(float(v), str(p))]


# ------------------------------------------------------------------------------------------------------------- pull
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _get(url: str, data: bytes | None = None, referer: str | None = None, tries: int = 6) -> bytes | None:
    headers = dict(UA)
    if referer:
        headers["Referer"] = referer
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310 (fixed https host)
                return bytes(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
        except OSError:
            pass
        time.sleep(5.0 * 2 ** min(k, 4))
    return None


CONTROL_PAGE = HD_PAGE.format(pair="eurusd", year=2019)


def _control_has_token() -> bool:
    html = _get(CONTROL_PAGE)
    time.sleep(PAUSE_S)
    return bool(html and re.search(rb'name="tk" id="tk" value="[0-9a-f]+"', html))


def download_zip(code: str, year: int, zdir: Path) -> tuple[Path | None, str]:
    """The pair-year zip, cached (atomic write). (None, why) when HistData has no such year or the fetch failed."""
    out = zdir / f"{code}_{year}.zip"
    if out.exists() and zipfile.is_zipfile(out):
        if year >= HOLDOUT_CUT.year:
            trim_zip_to_cut(out)
        return out, "cached"
    page = HD_PAGE.format(pair=code.lower(), year=year)
    m = None
    for k in range(THROTTLE_TRIES):
        html = _get(page)
        time.sleep(PAUSE_S)
        if html is None:
            return None, "page_failed"
        m = re.search(rb'name="tk" id="tk" value="([0-9a-f]+)"', html)
        if m:
            break
        if f"_M1_{year}.zip".encode() not in html or _control_has_token():
            # no file offered, or an empty token while a control page still gets one: HistData does not serve this
            # pair-year (XAUEUR 2019+ on 2026-11-25: the page names the zip but never issues a token)
            return None, "absent"
        # an empty token everywhere is a per-IP throttle: back off; a year that stays throttled is retried next run
        time.sleep(THROTTLE_WAIT_S * (k + 1))
    if not m:
        return None, "throttled"
    body = urllib.parse.urlencode({"tk": m.group(1).decode(), "date": year, "datemonth": year, "platform": "ASCII",
                                   "timeframe": "M1", "fxpair": code}).encode()
    blob = _get(HISTDATA + "/get.php", data=body, referer=page)
    time.sleep(PAUSE_S)
    if not blob or not zipfile.is_zipfile(io.BytesIO(blob)):
        return None, "download_failed"
    zdir.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    tmp.write_bytes(blob)
    os.replace(tmp, out)
    if year >= HOLDOUT_CUT.year:
        trim_zip_to_cut(out)            # never keep sealed minutes, not even in the source cache
    return out, "downloaded"


# Local stamps at or after this are at or after the holdout cut under BOTH stamp rules (in late September New York
# and EET-7 are both UTC-4): 2025-09-30 20:00 local = 2025-10-01 00:00 UTC.
LOCAL_CUT = (HOLDOUT_CUT - pd.Timedelta(hours=4)).tz_localize(None)


def trim_zip_to_cut(path: Path) -> tuple[int, int]:
    """Rewrite a cached HistData zip in place with only the rows stamped before the holdout cut (quant R10: the source
    cache is a research store too; review of PR #453). (rows kept, rows dropped). Atomic; a zip already trimmed
    drops 0 and is left byte-identical."""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not names:
            return 0, 0
        lines = z.read(names[0]).decode().splitlines()
    cut = LOCAL_CUT.strftime("%Y%m%d %H%M%S")
    keep = [ln for ln in lines if ln.strip() and ln[:15] < cut]
    dropped = sum(1 for ln in lines if ln.strip()) - len(keep)
    if dropped:
        tmp = path.with_suffix(".part")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr(names[0], "\n".join(keep) + "\n")
        os.replace(tmp, path)
    return len(keep), dropped


def _write_parquet(df: pd.DataFrame, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return _sha256(path)


def year_path(base: Path, key: str, year: int) -> Path:
    return base / key / f"{key.lower()}_1m_bid_{year}.parquet"


def pull_one(task: tuple[str, int, str]) -> dict[str, object]:
    """pull_one_ with any exception recorded as status failed (the run and its reports go on)."""
    try:
        return pull_one_(task)
    except Exception as exc:  # noqa: BLE001 (one bad pair-year must not end a 400-task run)
        return {"key": task[0], "year": task[1], "status": "failed", "why": f"{type(exc).__name__}: {exc}"[:300],
                "at": dt.datetime.now(dt.UTC).isoformat()}


def pull_one_(task: tuple[str, int, str]) -> dict[str, object]:
    """One instrument-year: download, parse, holdout cut, error quarantine, scale check, write. Returns its manifest
    line (status ok | absent | scale | empty | failed)."""
    key, year, base_s = task
    base = Path(base_s)
    src = history_sources().instruments[key]
    era = src.histdata_for(year)
    assert era is not None
    code = era.code
    rec: dict[str, object] = {"key": key, "year": year, "source": f"histdata:{code}", "tz": "America/New_York",
                              "era_to": era.to,
                              "holdout_cut": HOLDOUT_CUT.isoformat(), "at": dt.datetime.now(dt.UTC).isoformat()}
    zpath, how = download_zip(code, year, base / "_zips")
    if zpath is None:
        return {**rec, "status": "absent" if how == "absent" else "failed", "why": how}
    rec["zip_sha256"] = _sha256(zpath)
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not names:
            return {**rec, "status": "failed", "why": "no csv in zip"}
        df = parse_histdata(z.read(names[0]), code)
    df, sealed = drop_holdout(df)
    # every row of the file is kept, also the New York evening of Dec 31 (UTC Jan 1 of the next year): the next
    # year's file starts at its own local Jan 1, so nothing overlaps (review of #453: those hours were dropped)
    rec["sealed_dropped"] = sealed
    end = era.end()
    if end is not None:
        inside = df["ts_utc"] < end
        rec["out_of_era_dropped"] = int((~inside).sum())     # the code carries another series after `to`
        df = df[inside].reset_index(drop=True)
    if df.empty:
        return {**rec, "status": "empty"}
    bad = scale_errors(df, src)
    if bad:
        return {**rec, "status": "scale", "why": bad[:6]}
    clean, err = split_errors(df)
    if len(err):
        _write_parquet(err, base / "_quarantine" / key / f"{key.lower()}_errors_{year}.parquet")
    sha = _write_parquet(clean, year_path(base, key, year))
    return {**rec, "status": "ok", "rows": len(clean), "quarantined_rows": len(err),
            "flagged_rows": int((clean["dq_flag"] != "").sum()),
            "quarantine_reasons": err["dq_reason"].value_counts().to_dict() if len(err) else {},
            "first": str(clean["ts_utc"].iloc[0]), "last": str(clean["ts_utc"].iloc[-1]),
            "parquet": str(year_path(base, key, year).relative_to(base)), "parquet_sha256": sha}


def _nice() -> None:
    try:
        os.nice(10)
    except OSError:
        pass


def read_manifest(base: Path) -> dict[tuple[str, int], dict[str, object]]:
    p = base / "MANIFEST.jsonl"
    out: dict[tuple[str, int], dict[str, object]] = {}
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if "key" in r and "year" in r:            # event lines (zip_trimmed) carry no instrument-year
                    out[(str(r["key"]), int(r["year"]))] = r     # the last line for a pair-year wins
    return out


def append_manifest(base: Path, rec: dict[str, object]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    with open(base / "MANIFEST.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str, sort_keys=True) + "\n")


def done(base: Path, man: dict[tuple[str, int], dict[str, object]], key: str, year: int) -> bool:
    r = man.get((key, year))
    if r is None:
        return False
    if r["status"] == "ok":
        return year_path(base, key, year).exists()
    if r["status"] == "quarantined":
        return True                                   # failed its clock check: never pulled back into the partition
    if r["status"] == "absent":
        return int(str(r.get("absent_seen", 1))) >= 2  # one disagreeing page is not proof; absent twice is
    return r["status"] == "empty"                     # failed and scale years are retried


def tasks_for(keys: list[str], base: Path) -> list[tuple[str, int, str]]:
    man = read_manifest(base)
    out = []
    for key in keys:
        src = history_sources().instruments[key]
        for y in src.histdata_years(FIRST_YEAR, LAST_YEAR):
            if not done(base, man, key, y):
                out.append((key, y, str(base)))
    return out


def write_sums(base: Path) -> int:
    files = sorted(p for p in base.rglob("*") if p.is_file() and p.suffix in (".parquet", ".zip"))
    lines = [f"{_sha256(p)}  {p.relative_to(base).as_posix()}" for p in files]
    (base / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    return len(lines)


def cmd_pull(a: argparse.Namespace) -> int:
    base = Path(a.root) / "histdata_m1"
    if a.reparse:
        # re-parse every stored year from its cached zip (no download): a parse-rule change reaches the store, e.g.
        # keeping the New York evening of Dec 31 (review of #453). The year comes back stamped "ny"; `check` then
        # stamps it by its era's rule, re-flags spikes and regrades.
        man0 = read_manifest(base)
        tasks = [(k, y, str(base)) for (k, y), r in sorted(man0.items())
                 if k in a.keys and r["status"] in ("ok", "quarantined")
                 and history_sources().instruments[k].histdata_for(y) is not None]
    else:
        tasks = tasks_for(a.keys, base)
    print(f"{len(tasks)} instrument-years to pull with {a.workers} workers", flush=True)
    with ProcessPoolExecutor(max_workers=a.workers, initializer=_nice) as ex:
        man = read_manifest(base)
        for rec in ex.map(pull_one, tasks):
            prev = man.get((str(rec["key"]), int(str(rec["year"]))))
            if rec["status"] == "absent":
                rec["absent_seen"] = int(str(prev.get("absent_seen", 1))) + 1 if prev and prev["status"] == "absent" \
                    else 1
            append_manifest(base, rec)
            print(f"{rec['key']} {rec['year']} {rec['status']} rows={rec.get('rows')} "
                  f"sealed={rec.get('sealed_dropped')} q={rec.get('quarantined_rows')} {rec.get('why', '')}", flush=True)
    print(f"SHA256SUMS: {write_sums(base)} files", flush=True)
    return 0


# ------------------------------------------------------------------------------------------------ reference + check
def _points() -> dict[str, float]:
    pts = dict(EXTRA_POINTS)
    for s in history_sources().instruments.values():
        if s.dukascopy is not None:
            pts[s.dukascopy.code] = s.dukascopy.point
    return pts


def _leg_hourly(cache: Path, code: str, year: int, point: float) -> pd.DataFrame:
    """Bid hourly candles of one Dukascopy code for a year, with the close spread (ask - bid) where the ask exists."""
    frames = []
    for m in range(1, 13):
        month = dt.date(year, m, 1)
        sides = {}
        for side in ("BID", "ASK"):
            p = cache / code / f"{month:%Y-%m}_{side}.bi5"
            if p.exists() and p.stat().st_size > 0:
                sides[side] = decode_hourly(lzma.decompress(p.read_bytes()), month, point)
        if "BID" not in sides:
            continue
        b = sides["BID"]
        if "ASK" in sides:
            b = b.merge(sides["ASK"][["ts_utc", "close"]].rename(columns={"close": "ask_close"}), on="ts_utc", how="left")
            b["spread"] = b["ask_close"] - b["close"]
        else:
            b["spread"] = np.nan
        frames.append(b[["ts_utc", "open", "high", "low", "close", "spread"]])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _gold_hourly(raw: Path, year: int) -> pd.DataFrame:
    p = raw / f"xauusd_1m_dukascopy_{year}.parquet"
    if not p.exists():
        return pd.DataFrame()
    b = pd.read_parquet(p, columns=["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close", "ask_close"])
    b = b[pd.to_datetime(b["ts_utc"], utc=True) < HOLDOUT_CUT]
    h = pd.DatetimeIndex(pd.to_datetime(b["ts_utc"], utc=True)).floor("h")
    g = b.assign(h=h, spread=b["ask_close"] - b["bid_close"]).groupby("h", sort=True)
    out = pd.DataFrame({"open": g["bid_open"].first(), "high": g["bid_high"].max(), "low": g["bid_low"].min(),
                        "close": g["bid_close"].last(), "spread": g["spread"].last()})
    return out.rename_axis("ts_utc").reset_index()


def reference_hourly(key: str, year: int, raw: Path) -> tuple[pd.DataFrame, dict[str, bool]]:
    """The independent reference hours for `key` in `year` (eras per month from the source map) and, per month
    (YYYY-MM), whether that month's reference is native."""
    src = history_sources().instruments[key]
    cache, pts = raw / "daily" / "_cache", _points()
    frames, native = [], {}
    eras = {e.from_: e for e in src.reference}
    by_era: dict[str, list[int]] = {}
    for m in range(1, 13):
        e = src.reference_for(f"{year}-{m:02d}")
        if e is not None:
            by_era.setdefault(e.from_, []).append(m)
    for frm, months in by_era.items():
        e = eras[frm]
        h: pd.DataFrame | None
        if e.dukascopy_1m:
            h = _gold_hourly(raw, year)
        else:
            # gold's hourly cache starts 2025-05: its leg comes from the Dukascopy 1m yearly files
            legs = {c: _gold_hourly(raw, year) if c == "XAUUSD" else _leg_hourly(cache, c, year, pts[c]) for c in e.legs}
            if any(v.empty for v in legs.values()):
                continue
            h = combine_legs({c: v.set_index("ts_utc") for c, v in legs.items()}, e.legs, e.scale).reset_index()
        if h is None or h.empty:
            continue
        mon = pd.DatetimeIndex(h["ts_utc"]).month
        frames.append(h[np.isin(mon, months)])
        for m in months:
            native[f"{year}-{m:02d}"] = e.native
    ref = pd.concat(frames, ignore_index=True).sort_values("ts_utc") if frames else pd.DataFrame()
    return ref.reset_index(drop=True), native


REPO = Path(__file__).resolve().parents[1]


def git_state() -> tuple[str, bool]:
    """(HEAD sha, dirty): every report carries both; grading refuses a dirty tree (quant ruling 5)."""
    import subprocess
    sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True).stdout.strip())
    return sha, dirty


def _stored_rule(man: dict[tuple[str, int], dict[str, object]], key: str, year: int) -> str:
    return str(man.get((key, year), {}).get("tz", "ny")).replace("America/New_York", "ny")


def evidence_one(task: tuple[str, int, str, str, str]) -> dict[str, object]:
    """Outcome-blind evidence for one stored instrument-year: offset-0 rank correlation over the US/EU gap-window
    hours under each stamp rule, the best offset per gap window under each, and the same-source share (quant rulings
    1 and 2). Nothing is written."""
    key, year, base_s, raw_s, stored = task
    out: dict[str, object] = {"key": key, "year": year, "stored_rule": stored}
    try:
        base, raw = Path(base_s), Path(raw_s)
        src = history_sources().instruments[key]
        bars = pd.read_parquet(year_path(base, key, year))
        ref, native = reference_hourly(key, year, raw)
        if not len(ref):
            return {**out, "status": "no_reference"}
        for rule in TZ_RULES:
            b = reconvert(bars, rule, stored)
            ts, c = epoch_ns_utc(b["ts_utc"]), b["bid_close"].to_numpy(dtype="float64")
            out[f"gap_score_{rule}"] = gap_week_score(ts, c, ref, year)
            out[f"gap_offsets_{rule}"] = json.dumps(gap_week_offsets(ts, c, ref, year))
        nat = all(native.values()) and src.dukascopy is not None and not any(
            e.dukascopy_1m for e in src.reference)
        gold = any(e.dukascopy_1m for e in src.reference)
        point = src.dukascopy.point if src.dukascopy is not None else None
        out["same_source_share"] = same_source_share(epoch_ns_utc(bars["ts_utc"]),
                                                     bars["bid_close"].to_numpy(dtype="float64"), ref, point) \
            if (nat or gold) and point else None
        return {**out, "status": "ok"}
    except Exception as exc:  # noqa: BLE001
        return {**out, "status": "failed", "why": f"{type(exc).__name__}: {exc}"[:300]}


def cmd_evidence(a: argparse.Namespace) -> int:
    sha, dirty = git_state()
    if dirty:
        print("REFUSED: the working tree has uncommitted changes; evidence is stamped with a commit", flush=True)
        return 2
    base, raw = Path(a.root) / "histdata_m1", Path(a.root)
    man = read_manifest(base)
    tasks = [(k, y, str(base), str(raw), _stored_rule(man, k, y)) for k in a.keys
             for y in range(FIRST_YEAR, LAST_YEAR + 1) if year_path(base, k, y).exists()]
    rows = []
    with ProcessPoolExecutor(max_workers=a.workers, initializer=_nice) as ex:
        for r in ex.map(evidence_one, tasks):
            rows.append(r)
            print(f"{r['key']} {r['year']} ny={r.get('gap_score_ny')} eet7={r.get('gap_score_eet-7')} "
                  f"same={r.get('same_source_share')} {r['status']}", flush=True)
    df = pd.DataFrame(rows).assign(git_sha=sha, dirty=dirty, r11_id=R11_V2_ID)
    rep = base / "_reports"
    rep.mkdir(parents=True, exist_ok=True)
    df.to_csv(rep / "evidence_r11-v2.csv", index=False)
    d = df.dropna(subset=["gap_score_ny", "gap_score_eet-7"])
    win = np.where(d["gap_score_eet-7"] - d["gap_score_ny"] > 0.05, "eet-7",
                   np.where(d["gap_score_ny"] - d["gap_score_eet-7"] > 0.05, "ny", "tie"))
    print(pd.crosstab(d["year"], win).to_string(), flush=True)
    return 0


FIX_DIR = "fixes"   # raw/fixes/<FRED id>.csv (observation_date, value), fetched off the Mac, cut at the holdout


def load_fix(key: str, year: int, raw: Path) -> tuple[pd.DataFrame | None, str]:
    """(fixes of `year` as fix_utc/value, status): "ok", "cap_b", or "pending" (no spec, a series file missing)."""
    spec = history_sources().instruments[key].fix
    if spec is None or spec.pending is not None:
        return None, "pending"
    if spec.cap is not None:
        return None, "cap_b"
    assert spec.at is not None
    val: pd.Series | None = None
    for sid, e in spec.series.items():
        f = raw / FIX_DIR / f"{sid}.csv"
        if not f.exists():
            return None, "pending"
        d = pd.read_csv(f)
        v = pd.to_numeric(d.iloc[:, 1], errors="coerce")
        v.index = pd.to_datetime(d.iloc[:, 0])
        v = v.dropna() ** e
        val = v if val is None else (val * v).dropna()
    assert val is not None
    val = val[pd.DatetimeIndex(val.index).year == year]
    hhmm, zone = spec.at.split(" ")
    local = val.index + pd.Timedelta(hours=int(hhmm[:2]), minutes=int(hhmm[3:]))
    utc = pd.DatetimeIndex(local).tz_localize(zone, ambiguous="NaT", nonexistent="NaT").tz_convert("UTC")
    out = pd.DataFrame({"fix_utc": utc, "value": val.to_numpy()}).dropna()
    return out[out["fix_utc"] < HOLDOUT_CUT].reset_index(drop=True), "ok"


def check_one(task: tuple[str, int, str, str, str, bool, bool]) -> tuple[dict[str, object], list[dict[str, object]]]:
    """check_one_ with any exception recorded as a failed clock row (the run and its reports go on)."""
    try:
        return check_one_(task)
    except Exception as exc:  # noqa: BLE001
        return {"key": task[0], "year": task[1], "status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:300],
                "manifest": ""}, []


def check_one_(task: tuple[str, int, str, str, str, bool, bool]) -> tuple[dict[str, object], list[dict[str, object]]]:
    """R11 v2 for one stored instrument-year: stamp it under its era's DST rule (re-stamp if stored otherwise), clock
    check per year, per month and per US/EU gap window, confirmed-spike flags, then one grade per session month."""
    key, year, base_s, raw_s, stored, same_source, partial = task
    base, raw = Path(base_s), Path(raw_s)
    hs = history_sources()
    src = hs.instruments[key]
    p = year_path(base, key, year)
    bars = pd.read_parquet(p)
    if "dq_flag" not in bars:
        bars["dq_flag"] = ""
    rule = hs.dst_rule(year)
    changed = rule != stored
    if changed:
        bars = reconvert(bars, rule, stored)
    ref, native = reference_hourly(key, year, raw)
    ts, c = epoch_ns_utc(bars["ts_utc"]), bars["bid_close"].to_numpy(dtype="float64")
    if len(ref):
        cc = clock_check(key, year, ts, c, ref)
        offsets = month_offsets(ts, c, ref, src.weekend)
        gaps = gap_week_offsets(ts, c, ref, year)
    else:
        cc = ClockCheck(key=key, year=year, offset_min=None, corr_at_offset=None, corr_at_zero=None, hours=0,
                        agree_share=None, agree_tol=None, passed=False, reason="no_reference")
        offsets, gaps = {}, {}
    gap_bad = {k: o for k, o in gaps.items() if o is not None and abs(o) > 1}
    quarantined = (cc.offset_min is not None and not cc.passed) or bool(gap_bad)
    fix, fix_status = load_fix(key, year, raw)
    grades = []
    month = day_of(bars["ts_utc"], src.weekend).to_period("M")
    ref_month = day_of(ref["ts_utc"], src.weekend).to_period("M") if len(ref) else None
    flags = bars["dq_flag"].astype(str).copy()
    for per, idx in bars.groupby(month).groups.items():
        if str(per) >= HOLDOUT_CUT.strftime("%Y-%m"):
            continue                    # the stub of the first sealed session day (Sep 30 21:00-24:00 UTC)
        if str(per)[:4] != str(year):
            continue                    # hours after Dec 31 21:00 UTC open next year's first session: that month is
            #                             graded from its own file (a stub grade of 26-47 bars duplicated 301 months)
        b = bars.loc[idx]
        r = ref.loc[np.asarray(ref_month == per)] if ref_month is not None else None
        g = grade_month_v2(key, str(per), b, r, src.session_minutes, native.get(str(per), False),
                           weekend=src.weekend, quarantined=quarantined, clock_offset=offsets.get(str(per)),
                           same_source=same_source, fix=fix, fix_status=fix_status, partial_share=partial)
        grades.append(g.model_dump())
        if r is not None and len(r):
            conf = spike_confirmed(b, spike_mask(b["bid_close"]), r)
            flags.loc[conf[conf].index] = "warning:spike_confirmed"
    if not flags.equals(bars["dq_flag"].astype(str)):
        bars["dq_flag"] = flags
        changed = True
    new_rec: dict[str, object] | None = None
    if changed:
        sha = _write_parquet(bars, p)
        new_rec = {"key": key, "year": year, "status": "ok", "tz": rule, "rows": len(bars),
                   "first": str(bars["ts_utc"].iloc[0]), "last": str(bars["ts_utc"].iloc[-1]),
                   "parquet": str(p.relative_to(base)), "parquet_sha256": sha, "rewritten_by": f"check {R11_V21_ID}",
                   "holdout_cut": HOLDOUT_CUT.isoformat(), "at": dt.datetime.now(dt.UTC).isoformat()}
    if quarantined:
        dst = base / "_quarantine" / key / p.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(p, dst)
        new_rec = {**(new_rec or {"key": key, "year": year, "tz": rule}), "status": "quarantined",
                   "why": cc.reason if not gap_bad else f"gap-window offsets {gap_bad}",
                   "parquet": str(dst.relative_to(base)), "at": dt.datetime.now(dt.UTC).isoformat()}
    bad_months = {m: o for m, o in offsets.items() if abs(o) > 1}
    return {**cc.model_dump(), "status": "ok", "quarantined": quarantined, "bad_months": json.dumps(bad_months),
            "gap_offsets": json.dumps(gaps), "tz_rule": rule, "same_source": same_source, "fix_status": fix_status,
            "manifest": json.dumps(new_rec, default=str) if new_rec else ""}, grades


def same_source_decisions(ev: pd.DataFrame) -> dict[tuple[str, int], bool]:
    """Measured share >= 0.50 is same source; where the share cannot be measured (a synthetic reference), the year
    takes the majority of the instruments measured that year (conservative: HistData is one feed)."""
    ev = ev.copy()
    thr = R11_V2["same_source_share"]
    ev["same"] = ev["same_source_share"] >= thr
    measured = ev.dropna(subset=["same_source_share"])
    by_year = measured.groupby("year")["same"].mean()
    out = {}
    for key, year, share in zip(ev["key"], ev["year"], pd.to_numeric(ev["same_source_share"], errors="coerce")):
        out[(str(key), int(year))] = bool(share >= thr) if np.isfinite(share) else bool(by_year.get(year, 0) >= 0.5)
    return out


def partial_share_years(ev: pd.DataFrame) -> set[tuple[str, int]]:
    """Instrument-years sharing >= 0.10 of hour-ending closes with Dukascopy (quant re-verify B3)."""
    sh = pd.to_numeric(ev["same_source_share"], errors="coerce")
    sel = sh >= R11_V21["partial_share"]
    return {(str(k), int(y)) for k, y in zip(ev.loc[sel, "key"], ev.loc[sel, "year"])}


def write_v2_reports(rep: Path, clocks: list[dict[str, object]], tiers: list[dict[str, object]], sha: str) -> None:
    """Reports of the current grade id (R11_V21_ID), next to the kept earlier ones: v1 and v2 files are never
    overwritten (quant condition 3); the diff carries the earlier tiers and reasons."""
    tag = R11_V21_ID.split("-2026")[0]                      # r11-v2.1
    pd.DataFrame(clocks).assign(git_sha=sha, dirty=False, r11_id=R11_V21_ID).to_csv(rep / f"clock_{tag}.csv",
                                                                                   index=False)
    t2 = pd.DataFrame(tiers)
    if t2.empty:
        return
    t2["reasons"] = t2["reasons"].map(";".join)
    t2 = t2.assign(git_sha=sha, dirty=False)
    # volatility regime: tercile of the month's realised 1m volatility within the instrument (outcome-blind)
    t2["rv_tercile"] = t2.groupby("key")["rv"].transform(
        lambda x: pd.qcut(x.rank(method="first"), 3, labels=["low", "mid", "high"]) if x.notna().sum() >= 3 else None)
    t2.to_csv(rep / f"tiers_{tag}.csv", index=False)
    t2["year"] = t2["month"].str[:4]
    pd.crosstab([t2["year"], t2["rv_tercile"]], t2["tier"]).to_csv(rep / f"tier_mix_{tag}.csv")
    m = t2[["key", "month", "tier", "reasons"]].rename(columns={"tier": "tier_v21", "reasons": "reasons_v21"})
    for old in ("v2", "v1"):
        op = rep / f"tiers_r11-{old}.csv"
        if op.exists():
            o = pd.read_csv(op).sort_values("bars").drop_duplicates(["key", "month"], keep="last")   # v1 stubs
            o = o[["key", "month", "tier", "reasons"]].rename(columns={"tier": f"tier_{old}",
                                                                       "reasons": f"reasons_{old}"})
            m = m.merge(o, on=["key", "month"], how="left")
    m = m.fillna({"tier_v1": "-", "tier_v2": "-"})
    m.to_csv(rep / "tiers_v2_v21_diff.csv", index=False)
    if "tier_v2" in m:
        pd.crosstab(m["tier_v2"], m["tier_v21"], margins=True).to_csv(rep / "tiers_v2_v21_matrix.csv")
        pd.crosstab(m["key"], [m["tier_v2"], m["tier_v21"]]).to_csv(rep / "tiers_v2_v21_by_key.csv")


def cmd_check(a: argparse.Namespace) -> int:
    sha, dirty = git_state()
    if dirty:
        print("REFUSED: the working tree has uncommitted changes; grades are stamped with a commit (quant ruling 5)",
              flush=True)
        return 2
    base, raw = Path(a.root) / "histdata_m1", Path(a.root)
    rep = base / "_reports"
    rep.mkdir(parents=True, exist_ok=True)
    for name in ("tiers", "clock"):                      # keep the v1 grades next to v2 (quant condition 3)
        old, kept = rep / f"{name}.csv", rep / f"{name}_r11-v1.csv"
        if old.exists() and not kept.exists():
            os.replace(old, kept)
    evp = rep / "evidence_r11-v2.csv"
    if not evp.exists():
        print("REFUSED: run `evidence` first (same-source shares and the gap-window DST evidence)", flush=True)
        return 2
    same = same_source_decisions(pd.read_csv(evp))
    # every grade run regrades every stored year: a year a previous run quarantined comes back for the grade and goes
    # back to _quarantine if it fails again (a report never silently loses its F rows)
    for q in sorted((base / "_quarantine").glob("*/*_1m_bid_*.parquet")):
        dst = base / q.parent.name / q.name
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(q, dst)
    man = read_manifest(base)
    partial = partial_share_years(pd.read_csv(evp))
    tasks = [(k, y, str(base), str(raw), _stored_rule(man, k, y), same.get((k, y), False),
              (k, y) in partial and not same.get((k, y), False)) for k in a.keys
             for y in range(FIRST_YEAR, LAST_YEAR + 1) if year_path(base, k, y).exists()]
    clocks, tiers = [], []
    with ProcessPoolExecutor(max_workers=a.workers, initializer=_nice) as ex:
        for cc, grades in ex.map(check_one, tasks):
            m = cc.pop("manifest")
            if m:
                append_manifest(base, json.loads(str(m)))
            clocks.append(cc)
            tiers.extend(grades)
            t = pd.Series([g["tier"] for g in grades]).value_counts().to_dict()
            print(f"{cc['key']} {cc['year']} rule={cc.get('tz_rule')} same={cc.get('same_source')} "
                  f"clock={cc.get('offset_min')} {cc.get('reason')} tiers={t}", flush=True)
    write_v2_reports(rep, clocks, tiers, sha)
    print(f"r11 {R11_V21_ID} at {sha[:8]}: {len(tiers)} instrument-months graded; "
          f"quarantined {sum(bool(c.get('quarantined')) for c in clocks)}; failed "
          f"{sum(c.get('status') == 'failed' for c in clocks)}", flush=True)
    print(f"SHA256SUMS: {write_sums(base)} files", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["pull", "evidence", "check", "sums", "trim-cache"])
    ap.add_argument("--keys", default=None, help="comma-separated owner keys (default: every key with a HistData code)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--reparse", action="store_true",
                    help="pull: re-parse every stored year from the cached zips (no network)")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="the git-ignored raw/ directory")
    a = ap.parse_args(argv)
    hd = [k for k, s in history_sources().instruments.items() if s.histdata]
    a.keys = [k.strip() for k in a.keys.split(",")] if a.keys else hd
    unknown = set(a.keys) - set(hd)
    if unknown:
        ap.error(f"no HistData source for {sorted(unknown)}")
    a.workers = max(1, min(a.workers, 3))
    if a.cmd == "evidence":
        return cmd_evidence(a)
    if a.cmd == "trim-cache":
        base = Path(a.root) / "histdata_m1"
        for z in sorted((base / "_zips").glob(f"*_{HOLDOUT_CUT.year}.zip")):
            kept, dropped = trim_zip_to_cut(z)
            print(f"{z.name}: kept {kept} rows, dropped {dropped} sealed rows", flush=True)
            if dropped:
                append_manifest(base, {"event": "zip_trimmed", "zip": z.name, "sealed_rows_dropped": dropped,
                                       "zip_sha256": _sha256(z), "at": dt.datetime.now(dt.UTC).isoformat()})
        print(f"SHA256SUMS: {write_sums(base)} files")
        return 0
    if a.cmd == "pull":
        return cmd_pull(a)
    if a.cmd == "check":
        return cmd_check(a)
    print(f"SHA256SUMS: {write_sums(Path(a.root) / 'histdata_m1')} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
