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
    R11_ID,
    ClockCheck,
    HistorySource,
    clock_check,
    daily_from_hourly,
    day_of,
    drop_holdout,
    epoch_ns_utc,
    grade_month,
    history_sources,
    month_offsets,
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


def reconvert(bars: pd.DataFrame, rule: str) -> pd.DataFrame:
    """A year stored under the "ny" rule re-stamped under `rule` (the New York local stamps are recovered exactly by
    the inverse conversion), re-sorted, de-duplicated and cut at the holdout again."""
    local = pd.Series(pd.DatetimeIndex(bars["ts_utc"]).tz_convert("America/New_York").tz_localize(None),
                      index=bars.index)
    out = bars.assign(ts_utc=histdata_to_utc(local, rule)).dropna(subset=["ts_utc"])
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
    df = df[pd.DatetimeIndex(df["ts_utc"]).year == year].reset_index(drop=True)   # stamps that crossed New Year
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
    return r["status"] in ("absent", "empty")       # failed and scale years are retried


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
    tasks = tasks_for(a.keys, base)
    print(f"{len(tasks)} instrument-years to pull with {a.workers} workers", flush=True)
    with ProcessPoolExecutor(max_workers=a.workers, initializer=_nice) as ex:
        for rec in ex.map(pull_one, tasks):
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
            h = None
            for c, exp in e.legs.items():
                v = legs[c].set_index("ts_utc")
                part = pd.DataFrame({"close": v["close"] ** exp, "high": v["high"] ** exp, "low": v["low"] ** exp,
                                     "rel": abs(exp) * v["spread"] / v["close"]})
                if exp < 0:
                    part["high"], part["low"] = part["low"], part["high"]
                h = part if h is None else pd.DataFrame({
                    "close": h["close"] * part["close"], "high": h["high"] * part["high"],
                    "low": h["low"] * part["low"], "rel": h["rel"] + part["rel"]}).dropna(subset=["close"])
            assert h is not None
            for c in ("close", "high", "low"):
                h[c] = h[c] * e.scale
            h["spread"] = h["rel"] * h["close"]
            h = h.drop(columns="rel").rename_axis("ts_utc").reset_index()
        if h is None or h.empty:
            continue
        mon = pd.DatetimeIndex(h["ts_utc"]).month
        frames.append(h[np.isin(mon, months)])
        for m in months:
            native[f"{year}-{m:02d}"] = e.native
    ref = pd.concat(frames, ignore_index=True).sort_values("ts_utc") if frames else pd.DataFrame()
    return ref.reset_index(drop=True), native


def check_one(task: tuple[str, int, str, str, str]) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Clock check, tz-rule choice and R11 grades of one stored instrument-year. `stored_rule` (from the manifest) is
    the rule its stamps are in; only a year still on "ny" is tried under "eet-7" (idempotent: a re-run never shifts a
    re-stamped year twice)."""
    key, year, base_s, raw_s, stored_rule = task
    base, raw = Path(base_s), Path(raw_s)
    src = history_sources().instruments[key]
    p = year_path(base, key, year)
    bars = pd.read_parquet(p)
    bars_raw = bars                     # spikes are flagged in place, never removed (split_errors)
    ref, native = reference_hourly(key, year, raw)
    if len(ref):
        cc = clock_check(key, year, epoch_ns_utc(bars["ts_utc"]), bars["bid_close"].to_numpy(dtype="float64"), ref)
    else:
        cc = ClockCheck(key=key, year=year, offset_min=None, corr_at_offset=None, corr_at_zero=None, hours=0,
                        agree_share=None, agree_tol=None, passed=False, reason="no_reference")
    offsets = month_offsets(epoch_ns_utc(bars["ts_utc"]), bars["bid_close"].to_numpy(dtype="float64"), ref,
                            src.weekend) if len(ref) else {}
    rule, new_rec = stored_rule, None
    if stored_rule == "ny" and any(abs(o) > 1 for o in offsets.values()):
        alt = reconvert(bars, "eet-7")
        alt_off = month_offsets(epoch_ns_utc(alt["ts_utc"]), alt["bid_close"].to_numpy(dtype="float64"), ref,
                                src.weekend)
        if sum(abs(o) > 1 for o in alt_off.values()) < sum(abs(o) > 1 for o in offsets.values()):
            rule, bars, offsets = "eet-7", alt, alt_off
            cc = clock_check(key, year, epoch_ns_utc(bars["ts_utc"]), bars["bid_close"].to_numpy(dtype="float64"),
                             ref)
            sha = _write_parquet(bars, p)
            new_rec = {"key": key, "year": year, "status": "ok", "tz": "eet-7", "rows": len(bars),
                       "first": str(bars["ts_utc"].iloc[0]), "last": str(bars["ts_utc"].iloc[-1]),
                       "parquet": str(p.relative_to(base)), "parquet_sha256": sha, "restamped_by": "check",
                       "holdout_cut": HOLDOUT_CUT.isoformat(), "at": dt.datetime.now(dt.UTC).isoformat()}
    bars_raw = bars
    quarantined = cc.offset_min is not None and not cc.passed
    ref_daily = daily_from_hourly(ref, src.weekend) if len(ref) else None
    grades = []
    month = day_of(bars_raw["ts_utc"], src.weekend).to_period("M")     # the session day's month
    for per, idx in bars_raw.groupby(month).groups.items():
        if str(per) >= HOLDOUT_CUT.strftime("%Y-%m"):
            continue                    # the stub of the first sealed session day (Sep 30 21:00-24:00 UTC)
        g = grade_month(key, str(per), bars_raw.loc[idx], ref_daily, src.session_minutes,
                        native.get(str(per), False), src.weekend, quarantined, offsets.get(str(per)))
        grades.append(g.model_dump())
    if quarantined:
        dst = base / "_quarantine" / key / p.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(p, dst)
    bad_months = {m: o for m, o in offsets.items() if abs(o) > 1}
    return {**cc.model_dump(), "quarantined": quarantined, "bad_months": json.dumps(bad_months), "tz_rule": rule,
            "manifest": json.dumps(new_rec) if new_rec else ""}, grades


def cmd_check(a: argparse.Namespace) -> int:
    base, raw = Path(a.root) / "histdata_m1", Path(a.root)
    man = read_manifest(base)
    tasks = [(k, y, str(base), str(raw), str(man.get((k, y), {}).get("tz", "ny")).replace("America/New_York", "ny"))
             for k in a.keys for y in range(FIRST_YEAR, LAST_YEAR + 1) if year_path(base, k, y).exists()]
    clocks, tiers = [], []
    with ProcessPoolExecutor(max_workers=a.workers, initializer=_nice) as ex:
        for cc, grades in ex.map(check_one, tasks):
            m = cc.pop("manifest")
            if m:
                append_manifest(base, json.loads(str(m)))     # a year re-stamped under the eet-7 rule
            clocks.append(cc)
            tiers.extend(grades)
            t = pd.Series([g["tier"] for g in grades]).value_counts().to_dict()
            print(f"{cc['key']} {cc['year']} clock={cc['offset_min']} agree={cc['agree_share']} "
                  f"{cc['reason']} tiers={t}", flush=True)
    rep = base / "_reports"
    rep.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(clocks).to_csv(rep / "clock.csv", index=False)
    tdf = pd.DataFrame(tiers)
    if len(tdf):
        tdf["reasons"] = tdf["reasons"].map(";".join)
    tdf.to_csv(rep / "tiers.csv", index=False)
    print(f"r11 {R11_ID}: {len(tdf)} instrument-months graded; clock fails {sum(bool(c['quarantined']) for c in clocks)}")
    print(f"SHA256SUMS: {write_sums(base)} files", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["pull", "check", "sums", "trim-cache"])
    ap.add_argument("--keys", default=None, help="comma-separated owner keys (default: every key with a HistData code)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="the git-ignored raw/ directory")
    a = ap.parse_args(argv)
    hd = [k for k, s in history_sources().instruments.items() if s.histdata]
    a.keys = [k.strip() for k in a.keys.split(",")] if a.keys else hd
    unknown = set(a.keys) - set(hd)
    if unknown:
        ap.error(f"no HistData source for {sorted(unknown)}")
    a.workers = max(1, min(a.workers, 3))
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
