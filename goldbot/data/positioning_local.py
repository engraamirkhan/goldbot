"""Local (Mac) build of the point-in-time positioning data: CFTC COT for COMEX gold (disaggregated 2006 on, legacy 1986
on, every trader category) and the SPDR GLD holdings archive (2004 on). Market data stays on the Mac (ADR
data-local-only; the SPDR file also forbids redistribution): this writes `raw/positioning/positioning.parquet`, the
`positioning` table of the Parquet store and the downloaded source files with a sha256 manifest, and publishes nothing.

Availability rules, revisions and the history guard are goldbot/data/positioning.py's (`cot_available_utc`,
`gld_available_utc`, `merge_releases`, `check_keeps_history`); this module adds the GLD archive reader, data-quality
checks with quarantine and the build. Research schema: docs/research/positioning-data-schema.md.

GLD source: the issuer's own download, https://api.spdrgoldshares.com/api/v1/historical-archive?product=gld&exchange=NYSE&lang=en
(the link on spdrgoldshares.com/usa/gld; an xlsx, read here with the standard library). The old CSV archive URL now
redirects to a bar list PDF. Fetched with a plain, honest User-Agent; a non-workbook answer raises
GldSourceUnavailable (no scraping, no browser emulation) and the published GLD rows are kept.

  python -m goldbot.data.positioning_local --raw ~/myproject/goldbot/raw --store ~/myproject/goldbot/data
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import zipfile
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

import numpy as np
import pandas as pd
import requests

from goldbot.data.positioning import (
    COLUMNS,
    COT_FIRST_YEAR,
    COT_HIST_URL,
    COT_LEGACY_FIRST_YEAR,
    COT_LEGACY_HIST_URL,
    COT_LEGACY_SERIES,
    COT_LEGACY_SPLIT_SERIES,
    COT_LEGACY_YEAR_URL,
    COT_SERIES,
    COT_SPLIT_SERIES,
    COT_YEAR_URL,
    SHUTDOWN_FLOORS,
    GldSourceUnavailable,
    _get,
    _naive_days,
    _vintage_day,
    check_keeps_history,
    cot_frame,
    cot_legacy_frame,
    gld_available_utc,
    merge_releases,
    parse_cot,
    parse_cot_legacy,
    read_release,
    write_release,
)
from goldbot.data.quality import DQEvent, events_frame
from goldbot.data.store import Store

COT_DISAGG_SERIES = (*COT_SERIES, *COT_SPLIT_SERIES)
COT_LEGACY_ALL_SERIES = (*COT_LEGACY_SERIES, *COT_LEGACY_SPLIT_SERIES)
GLD_SERIES = ("gld_tonnes", "gld_ounces", "gld_shares", "gld_volume")
GLD_ARCHIVE_URL = "https://api.spdrgoldshares.com/api/v1/historical-archive?product=gld&exchange=NYSE&lang=en"
OZ_PER_TONNE = 32_150.7466
USER_AGENT = "goldbot-data (research)"

Fetched = tuple[pd.DataFrame, dict[str, bytes]]


# --------------------------------------------------------------------------------------------------- GLD archive
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _xlsx_rows(data: bytes, sheet_name_has: str) -> list[list[str | None]]:
    """The rows of the first sheet whose name contains `sheet_name_has`, as strings (shared strings resolved; numbers
    as written). Standard library only (no openpyxl dependency)."""
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    strings: list[str] = []
    if "xl/sharedStrings.xml" in names:
        sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
        strings = ["".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")) for si in sst.findall("m:si", _NS)]
    rels = {r.get("Id"): r.get("Target", "") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    sheets = ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", _NS)
    target = next((rels.get(s.get(_REL_NS)) for s in (sheets if sheets is not None else [])
                   if sheet_name_has.lower() in (s.get("name") or "").lower()), None)
    if not target:
        raise GldSourceUnavailable(f"GLD archive has no sheet named like {sheet_name_has!r}")
    path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
    root = ET.fromstring(z.read(path))
    out: list[list[str | None]] = []
    for row in root.iter(f"{{{_NS['m']}}}row"):
        cells: dict[int, str | None] = {}
        for c in row.findall("m:c", _NS):
            col = re.match(r"[A-Z]+", c.get("r", "A"))
            idx = 0
            for ch in (col.group(0) if col else "A"):
                idx = idx * 26 + (ord(ch) - 64)
            v = c.find("m:v", _NS)
            t = c.get("t")
            if t == "inlineStr":
                val: str | None = "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
            elif v is None:
                val = None
            elif t == "s":
                val = strings[int(v.text or 0)]
            else:
                val = v.text
            cells[idx - 1] = val
        out.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
    return out


def parse_gld_xlsx(data: bytes) -> pd.DataFrame:
    """The SPDR GLD historical archive workbook -> value_date, tonnes, ounces, oz_per_share, volume. Rows without a
    number (holidays) are dropped. Raises GldSourceUnavailable for anything that is not that workbook."""
    if not data.startswith(b"PK"):
        raise GldSourceUnavailable(f"GLD archive is not a workbook (starts {data[:12]!r})")
    try:
        rows = _xlsx_rows(data, "archive")
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise GldSourceUnavailable(f"GLD archive unreadable: {exc}") from exc
    head = next((i for i, r in enumerate(rows[:50]) if r and str(r[0] or "").strip().lower() == "date"), None)
    if head is None:
        raise GldSourceUnavailable("GLD archive has no Date header")
    names = [str(c or "").strip().lower() for c in rows[head]]

    def col(*needles: str) -> int:
        for i, n in enumerate(names):
            if all(k in n for k in needles):
                return i
        raise GldSourceUnavailable(f"GLD archive lacks a column with {needles}")

    ci = {"tonnes": col("tonnes"), "ounces": col("total ounces"), "oz_per_share": col("ounces", "per share"),
          "volume": col("volume")}
    body = [r + [None] * (len(names) - len(r)) for r in rows[head + 1:]]
    raw = pd.DataFrame({"date": [r[0] for r in body], **{k: [r[i] for r in body] for k, i in ci.items()}})
    dates = pd.to_datetime(raw["date"].astype(str).str.strip(), format="%d-%b-%Y", errors="coerce")
    out = pd.DataFrame({"value_date": dates})
    for k in ci:
        out[k] = pd.to_numeric(raw[k].astype(str).str.replace(",", "").str.strip(), errors="coerce")
    out = out.dropna(subset=["value_date", "tonnes"])
    out = out[out["tonnes"] > 0]
    if out.empty:
        raise GldSourceUnavailable("GLD archive parsed to no rows")
    return out.drop_duplicates("value_date", keep="last").sort_values("value_date").reset_index(drop=True)


def gld_archive_frame(parsed: pd.DataFrame, vintage: pd.Timestamp) -> pd.DataFrame:
    """parse_gld_xlsx rows -> long release rows (COLUMNS), source spdr, stamped by `gld_available_utc`."""
    if parsed.empty:
        return pd.DataFrame(columns=list(COLUMNS))
    days = _naive_days(parsed["value_date"])
    avail = gld_available_utc(days)
    shares = parsed["ounces"] / parsed["oz_per_share"].where(parsed["oz_per_share"] > 0)
    values = {"gld_tonnes": parsed["tonnes"], "gld_ounces": parsed["ounces"], "gld_shares": shares,
              "gld_volume": parsed["volume"]}
    parts = [pd.DataFrame({"series": s, "series_id": "GLD", "value_date": days,
                           "value": pd.to_numeric(v, errors="coerce").astype(float).to_numpy(),
                           "vintage": _vintage_day(vintage), "available_utc": avail, "source": "spdr"})
             for s, v in values.items()]
    out = pd.concat(parts, ignore_index=True)
    out = out[out["value"].notna()].reset_index(drop=True)
    out["ts_utc"] = out["available_utc"]
    return out[list(COLUMNS)]


# --------------------------------------------------------------------------------------------------- fetchers
def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _zip_frames(blobs: dict[str, bytes], parse: Callable[[bytes], pd.DataFrame]) -> list[pd.DataFrame]:
    frames = []
    for blob in blobs.values():
        z = zipfile.ZipFile(io.BytesIO(blob))
        frames += [parse(z.read(n)) for n in z.namelist() if n.lower().endswith((".txt", ".csv"))]
    return frames


def _fetch_zips(years: list[int], hist_url: str, year_url: str, parse: Callable[[bytes], pd.DataFrame]) -> Fetched:
    s = _session()
    urls = ([hist_url] if any(y <= 2016 for y in years) else []) + [year_url.format(year=y) for y in years if y > 2016]
    blobs = {u.rsplit("/", 1)[-1]: _get(u, retries=3, session=s).content for u in urls}
    frames = _zip_frames(blobs, parse)
    if not frames:
        raise RuntimeError(f"no data file in {list(blobs)}")
    df = pd.concat(frames, ignore_index=True)
    df = df[df["report_date"].dt.year.isin(years)]
    return df.drop_duplicates("report_date", keep="last").sort_values("report_date").reset_index(drop=True), blobs


def fetch_cot_raw(years: list[int]) -> Fetched:
    return _fetch_zips(years, COT_HIST_URL, COT_YEAR_URL, parse_cot)


def fetch_cot_legacy_raw(years: list[int]) -> Fetched:
    return _fetch_zips(years, COT_LEGACY_HIST_URL, COT_LEGACY_YEAR_URL, parse_cot_legacy)


def fetch_gld_raw() -> Fetched:
    try:
        r = _get(GLD_ARCHIVE_URL, retries=3, session=_session())
    except RuntimeError as exc:
        raise GldSourceUnavailable(str(exc)) from exc
    return parse_gld_xlsx(r.content), {"gld_us_archive.xlsx": r.content}


# --------------------------------------------------------------------------------------------------- data quality
_POSITION_PREFIXES = ("cot_",)
_NET_OK_NEGATIVE = re.compile(r"_net$")
COT_MIN_LAG = pd.Timedelta(days=3)          # Tuesday positions are never public before Friday
GLD_MIN_LAG = pd.Timedelta(hours=20, minutes=15)   # 4:15 pm NYT is 20:15 UTC at the latest (EST)
COT_GAP_DAYS = 8                             # weekly report: a gap above this is a missing week
GLD_GAP_BDAYS = 6                            # trading-day series: more than ~a week of missing days
GLD_JUMP_PCT = 5.0                           # day-on-day change in tonnes above this is flagged
GLD_TONNES_TOL_PCT = 0.5                     # tonnes vs ounces / 32,150.7466


def _ev(check: str, severity: str, ts: pd.Timestamp, detail: str) -> DQEvent:
    return DQEvent(ts_utc=pd.Timestamp(ts).tz_localize("UTC") if pd.Timestamp(ts).tzinfo is None else pd.Timestamp(ts),
                   check=check, severity=severity, detail=detail)


def _in_shutdown(d: pd.Timestamp) -> bool:
    return any(pd.Timestamp(a) - pd.Timedelta(days=7) <= d <= pd.Timestamp(b) + pd.Timedelta(days=7)
               for a, b, _ in SHUTDOWN_FLOORS)


def check_positioning(df: pd.DataFrame) -> list[DQEvent]:
    """Data-quality events for long positioning rows. Each event's detail starts `series|value_date|available_utc`
    for row-level errors, which `quarantine_positioning` uses.

    Errors (row quarantined): negative position or holding (net series excepted); a long/short/spread leg above the
    report's open interest; available_utc earlier than the value could exist (COT: value_date + 3 days; GLD: 20:15 UTC
    on value_date); a duplicate (series, value_date, available_utc) with different values.
    Warnings (kept, flagged): a cadence gap (COT > 8 days outside shutdowns; GLD > 6 business days); GLD tonnes
    off ounces / 32,150.7466 by more than 0.5 %; a GLD day-on-day tonnes change above 5 %."""
    if df.empty:
        return []
    d = df.copy()
    d["value_date"] = _naive_days(d["value_date"])
    d["available_utc"] = pd.DatetimeIndex(pd.to_datetime(d["available_utc"], utc=True)).as_unit("ns")
    events: list[DQEvent] = []

    def row_err(check: str, rows: pd.DataFrame, why: str) -> None:
        for s, day, avail, v in zip(rows["series"], pd.DatetimeIndex(rows["value_date"]),
                                    pd.DatetimeIndex(rows["available_utc"]), rows["value"]):
            events.append(_ev(check, "error", avail, f"{s}|{day.date()}|{avail.isoformat()}|{why} (value {v})"))

    is_cot = d["series"].str.startswith(_POSITION_PREFIXES)
    is_gld = d["source"] == "spdr"
    neg_ok = d["series"].str.contains(_NET_OK_NEGATIVE)
    row_err("negative_value", d[(is_cot | is_gld) & ~neg_ok & (d["value"] < 0)], "negative")

    # legs above open interest (same report, same vintage of availability)
    for oi_name, prefix in (("cot_open_interest", "cot_"), ("cot_legacy_open_interest", "cot_legacy_")):
        oi = d[d["series"] == oi_name][["value_date", "value"]].drop_duplicates("value_date", keep="last")
        legs = d[d["series"].str.startswith(prefix) & d["series"].str.contains(r"_(?:long|short|spread)$")]
        if prefix == "cot_":
            legs = legs[~legs["series"].str.startswith("cot_legacy_")]
        m = legs.merge(oi.rename(columns={"value": "oi"}), on="value_date", how="inner")
        row_err("above_open_interest", m[m["value"] > m["oi"] + 1e-9].drop(columns=["oi"]), "above open interest")

    lag = pd.to_timedelta(np.where(is_gld, GLD_MIN_LAG.value, COT_MIN_LAG.value), unit="ns")
    floor = d["value_date"].dt.tz_localize("UTC") + lag
    row_err("available_before_value", d[(is_cot | is_gld) & (d["available_utc"] < floor)], "available too early")

    dup = d[d.duplicated(["series", "value_date", "available_utc"], keep=False)]
    if len(dup):
        conflict = dup.groupby(["series", "value_date", "available_utc"])["value"].transform("nunique") > 1
        row_err("conflicting_duplicate", dup[conflict], "two values at one stamp")

    # warnings: cadence and GLD consistency
    for s, g in d.groupby("series"):
        days = pd.DatetimeIndex(sorted(g["value_date"].unique()))
        if len(days) < 2:
            continue
        if str(s).startswith("cot_"):
            gaps = days[1:] - days[:-1]
            for prev_day, gap in zip(days[:-1], gaps):
                legacy_early = str(s).startswith("cot_legacy_") and prev_day < pd.Timestamp("1992-10-01")
                if gap.days > COT_GAP_DAYS and not legacy_early and not _in_shutdown(prev_day):
                    events.append(_ev("cadence_gap", "warning", prev_day, f"{s}|{prev_day.date()}|gap {gap.days} days"))
        elif str(s).startswith("gld_"):
            bd = np.busday_count(days[:-1].values.astype("datetime64[D]"), days[1:].values.astype("datetime64[D]"))
            for prev_day, n in zip(days[:-1], bd):
                if n > GLD_GAP_BDAYS:
                    events.append(_ev("cadence_gap", "warning", prev_day, f"{s}|{prev_day.date()}|gap {n} bdays"))
    first = d.sort_values("available_utc").drop_duplicates(["series", "value_date"], keep="first")
    t = first[first["series"] == "gld_tonnes"].set_index("value_date")["value"].sort_index()
    oz = first[first["series"] == "gld_ounces"].set_index("value_date")["value"].sort_index()
    if len(t) and len(oz):
        both = pd.concat([t.rename("t"), oz.rename("oz")], axis=1).dropna()
        off = (100.0 * (both["t"] - both["oz"] / OZ_PER_TONNE).abs() / both["t"]) > GLD_TONNES_TOL_PCT
        for day in both.index[off]:
            events.append(_ev("gld_tonnes_ounces_mismatch", "warning", day,
                              f"gld_tonnes|{day.date()}|tonnes {both.at[day, 't']} vs ounces/{OZ_PER_TONNE}"))
    if len(t) > 1:
        chg = 100.0 * t.pct_change(fill_method=None).abs()
        for day in chg.index[chg > GLD_JUMP_PCT]:
            events.append(_ev("gld_jump", "warning", day, f"gld_tonnes|{day.date()}|day change {chg[day]:.2f} %"))
    return events


def quarantine_positioning(df: pd.DataFrame, events: list[DQEvent]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(clean, quarantined): rows named by an error event leave the frame; the quarantined rows carry `dq_check`.
    Warnings never drop a row."""
    keys: dict[tuple[str, pd.Timestamp, pd.Timestamp], str] = {}
    for e in events:
        if e.severity != "error":
            continue
        s, day, avail, *_ = e.detail.split("|")
        keys[(s, pd.Timestamp(day), pd.Timestamp(avail))] = e.check
    if not keys:
        return df.reset_index(drop=True), pd.DataFrame(columns=[*df.columns, "dq_check"])
    k = list(zip(df["series"], _naive_days(df["value_date"]),
                 pd.DatetimeIndex(pd.to_datetime(df["available_utc"], utc=True)).as_unit("ns")))
    checks = [keys.get((s, d, a)) for s, d, a in k]
    bad = np.array([c is not None for c in checks])
    q = df[bad].copy()
    q["dq_check"] = [c for c in checks if c is not None]
    return df[~bad].reset_index(drop=True), q.reset_index(drop=True)


# --------------------------------------------------------------------------------------------------- build
@dataclass
class BuildResult:
    rows: int = 0
    added: int = 0
    quarantined: int = 0
    warnings: int = 0
    notes: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _save_sources(src_dir: Path, blobs: dict[str, bytes], now: pd.Timestamp) -> None:
    src_dir.mkdir(parents=True, exist_ok=True)
    man_path = src_dir / "manifest.json"
    manifest = json.loads(man_path.read_text()) if man_path.exists() else {}
    for name, blob in blobs.items():
        (src_dir / name).write_bytes(blob)
        manifest[name] = {"sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob),
                          "retrieved_utc": now.isoformat()}
    man_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))


def build(raw_root: Path | str, store_root: Path | str | None, *, now: pd.Timestamp | None = None,
          seed: Path | str | None = None, from_year: int = 0,
          fetch_cot_fn: Callable[[list[int]], Fetched] = fetch_cot_raw,
          fetch_legacy_fn: Callable[[list[int]], Fetched] = fetch_cot_legacy_raw,
          fetch_gld_fn: Callable[[], Fetched] = fetch_gld_raw) -> BuildResult:
    """Download, check, merge into `raw_root/positioning/positioning.parquet` (history kept; revisions are new rows),
    and load the `positioning` table of the store at `store_root` (None: skip). `seed` names a previously built file
    to start from when the local one does not exist yet (its first-release rows are kept as they are).

    Raises HistoryLoss (nothing written) if the merged frame would drop or change a previously written row."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    out_dir = Path(raw_root).expanduser() / "positioning"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "positioning.parquet"
    start = path if path.exists() else (Path(seed).expanduser() if seed else None)
    prev, last_ok = read_release(str(start)) if start is not None and start.exists() else (None, {})
    res = BuildResult()
    src_dir = out_dir / "src" / f"{now:%Y-%m-%d}"
    fresh: list[pd.DataFrame] = []

    def has(source: str, series: tuple[str, ...]) -> bool:
        """The published file already carries every series of this source (else a full backfill)."""
        return prev is not None and set(series) <= set(prev.loc[prev["source"] == source, "series"])

    jobs: list[tuple[str, int, Callable[[list[int]], Fetched], Callable[[pd.DataFrame, pd.Timestamp], pd.DataFrame],
                     tuple[str, ...]]] = [
        ("cftc", COT_FIRST_YEAR, fetch_cot_fn, partial(cot_frame, split=True), COT_DISAGG_SERIES),
        ("cftc_legacy", COT_LEGACY_FIRST_YEAR, fetch_legacy_fn, partial(cot_legacy_frame, split=True),
         COT_LEGACY_ALL_SERIES),
    ]
    for source, first_year, fetch, frame, series in jobs:
        first = from_year or (now.year - 1 if has(source, series) else first_year)
        try:
            parsed, blobs = fetch(list(range(first, now.year + 1)))
            _save_sources(src_dir, blobs, now)
            if len(parsed):
                fresh.append(frame(parsed, now))
                res.notes.append(f"{source}: {len(parsed):,} reports {parsed['report_date'].min():%Y-%m-%d} .. "
                                 f"{parsed['report_date'].max():%Y-%m-%d}")
            last_ok[source] = now
        except (RuntimeError, ValueError, OSError) as exc:
            res.failed.append(source)
            res.notes.append(f"{source} FAILED (written rows kept): {exc}")
    try:
        gld, blobs = fetch_gld_fn()
        _save_sources(src_dir, blobs, now)
        fresh.append(gld_archive_frame(gld, now))
        last_ok["spdr"] = now
        res.notes.append(f"spdr: {len(gld):,} days {gld['value_date'].min():%Y-%m-%d} .. {gld['value_date'].max():%Y-%m-%d}")
    except GldSourceUnavailable as exc:
        res.unavailable.append("spdr")
        res.notes.append(f"spdr UNAVAILABLE (written rows kept, not scraped): {exc}")

    new = pd.concat(fresh, ignore_index=True) if fresh else pd.DataFrame(columns=list(COLUMNS))
    events = check_positioning(new)
    clean, quarantined = quarantine_positioning(new, events)
    res.quarantined = len(quarantined)
    res.warnings = sum(e.severity == "warning" for e in events)
    merged = merge_releases(prev, clean, now, last_ok) if len(clean) else prev
    if merged is None:
        res.notes.append("nothing downloaded and no previous file: nothing written")
        return res
    check_keeps_history(prev, merged)          # raises HistoryLoss before anything is written
    tmp = path.with_suffix(".parquet.tmp")
    write_release(merged, str(tmp), last_ok)
    tmp.replace(path)
    if len(quarantined):
        qpath = out_dir / "quarantine.parquet"
        q = quarantined.assign(quarantined_utc=now)
        if qpath.exists():
            q = pd.concat([pd.read_parquet(qpath), q], ignore_index=True)
            q = q.drop_duplicates(["series", "value_date", "available_utc", "value", "dq_check"], keep="first")
        q.to_parquet(qpath, index=False)
    res.rows = len(merged)
    res.added = len(merged) - (0 if prev is None else len(prev))
    if store_root is not None:
        store = Store(Path(store_root).expanduser())
        for src, g in merged.groupby("source"):
            store.append("positioning", g.drop(columns=["source"]), source=str(src))
        if events:
            ev = events_frame(events)
            store.append("dq_events", ev.assign(table="positioning"), source="positioning", dedupe=False)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--raw", default="raw", help="raw folder (positioning/ is created inside)")
    ap.add_argument("--store", default="data", help="Parquet store root ('' to skip)")
    ap.add_argument("--seed", default="", help="a previous positioning.parquet to start from when none exists locally")
    ap.add_argument("--from-year", type=int, default=0)
    args = ap.parse_args(argv)
    res = build(args.raw, args.store or None, seed=args.seed or None, from_year=args.from_year)
    for n in res.notes:
        print(n)
    print(f"rows {res.rows:,} (added {res.added:,}); quarantined {res.quarantined}; warnings {res.warnings}")
    return 1 if res.failed else 0


if __name__ == "__main__":
    sys.exit(main())
