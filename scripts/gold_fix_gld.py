"""Independent daily gold fix for D-1 (grading Dukascopy XAUUSD): the LBMA Gold Price PM as implied by the SPDR Gold
Trust's official NAV, local only (raw/fixes/, never committed or published: the source forbids redistribution).

Why this route (2026-11-25/26): the LBMA's own JSON is behind a bot challenge (HTTP 403), FRED and ALFRED removed every
IBA/LBMA series in Jan 2022 (the ALFRED page now redirects to the removal notice), the Bundesbank's BBEX3 holds only the
pre-1999 Frankfurt DM fixing, and the old GLD CSV path now redirects to a bar list. The trust's sponsor publishes its
daily NAV history (api.spdrgoldshares.com historical-archive, XLSX) and the prospectus values the trust's gold at the
LBMA Gold Price PM (15:00 London). So

    implied_fix = NAV per share / ounces of gold per share

equals the PM price less the trust's accrued, unpaid liabilities per ounce (the sponsor fee accrues daily, is paid
monthly: 0.40 % / 12 = at most ~3.3 bp, always on the low side). That bias is stated, measured against Dukascopy by
`scripts/dukascopy_gold_grade.py` (median and p02/p98 bp per year) and is inside the frozen 10 bp tolerance; it is not
corrected (a correction would be a model, not a fix).

Days dropped, each counted: London closures and early closes (no PM price; the NAV then carries a stale price) from the
pinned exchange calendar (XLON), and a stale-value guard (|implied_t / implied_t-1 - 1| < 0.2 bp: the NAV carried the
previous price). Rows strictly before the holdout cut (quant R10) are kept.

Output raw/fixes/GLDNAV_XAU_PM.csv: observation_date, value, fix_utc (15:00 Europe/London), available_utc (the NAV is
struck after the 16:00 New York close: available from the next UTC midnight after that, conservative), source.
The raw XLSX is kept as raw/fixes/_src/gld_archive_<UTC date>.xlsx with its sha256 in GLDNAV_XAU_PM.json.

    uv run python scripts/gold_fix_gld.py [--root raw] [--xlsx path/to/an/already/fetched/file.xlsx]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.era_quality import HOLDOUT_CUT  # noqa: E402
from goldbot.data.exchange_holidays import calendar_sha256, early_closes, exchange_holidays  # noqa: E402

URL = "https://api.spdrgoldshares.com/api/v1/historical-archive?product=gld&exchange=NYSE&lang=en"
SHEET = "US GLD Historical Archive"
FIX_ID = "GLDNAV_XAU_PM"
FIX_AT = "15:00 Europe/London"
STALE_BP = 0.2
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "raw"


def fetch(dst_dir: Path) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(URL, headers={"User-Agent": "goldbot-data/1"})
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 (fixed https URL)
        body = r.read()
    if not body.startswith(b"PK"):
        raise RuntimeError(f"{URL}: not an XLSX ({body[:40]!r})")
    p = dst_dir / f"gld_archive_{dt.datetime.now(dt.UTC):%Y-%m-%d}.xlsx"
    p.write_bytes(body)
    return p


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):  # type: ignore[union-attr]
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_sheet(path: Path, name: str) -> list[list[str | None]]:
    """The named sheet of an XLSX as rows of cell strings (stdlib only: no spreadsheet dependency for one file)."""
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rid = next(s.get(f"{RNS}id") for s in wb.iter(f"{NS}sheet") if s.get("name") == name)
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels if r.get("Id") == rid)
        target = str(target).lstrip("/")
        sheet = ET.fromstring(z.read(target if target.startswith("xl/") else f"xl/{target}"))
    rows: list[list[str | None]] = []
    for r in sheet.iter(f"{NS}row"):
        cells: dict[int, str | None] = {}
        for c in r.iter(f"{NS}c"):
            v = c.find(f"{NS}v")
            if c.get("t") == "s" and v is not None:
                val: str | None = shared[int(v.text or 0)]
            elif c.get("t") == "inlineStr":
                val = "".join(t.text or "" for t in c.iter(f"{NS}t"))
            else:
                val = v.text if v is not None else None
            cells[_col(str(c.get("r")))] = val
        rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
    return rows


def _date(x: str | None) -> pd.Timestamp | None:
    if x is None or not str(x).strip():
        return None
    s = str(x).strip()
    try:
        if re.fullmatch(r"\d+(\.0+)?", s):                       # Excel serial day
            return pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(float(s)))
        return pd.Timestamp(dt.datetime.strptime(s, "%d-%b-%Y"))
    except ValueError:
        return None


def implied_fix(rows: list[list[str | None]]) -> tuple[pd.DataFrame, dict[str, int]]:
    head = [str(h or "").strip() for h in rows[0]]
    i_date = head.index("Date")
    i_nav = next(i for i, h in enumerate(head) if h.startswith("NAV/Share"))
    i_oz = head.index("Ounces of Gold per Share")
    recs = []
    bad = 0
    for r in rows[1:]:
        d = _date(r[i_date] if len(r) > i_date else None)
        try:
            nav, oz = float(str(r[i_nav])), float(str(r[i_oz]))
        except (TypeError, ValueError, IndexError):
            bad += 1
            continue
        if d is None or not (nav > 0 and oz > 0):
            bad += 1
            continue
        recs.append((d, nav / oz))
    f = pd.DataFrame(recs, columns=["observation_date", "value"]).drop_duplicates("observation_date", keep="last")
    f = f.sort_values("observation_date").reset_index(drop=True)
    counts = {"rows_read": len(rows) - 1, "rows_unparsed": bad}
    day = (f["observation_date"] - pd.Timestamp("1970-01-01")).dt.days.to_numpy()
    closed, _ = exchange_holidays("UK")
    early, _, _ = early_closes("UK")
    london = np.isin(day, closed) | np.isin(day, early)
    counts["dropped_london_closed_or_early_close"] = int(london.sum())
    f = f[~london].reset_index(drop=True)
    stale = np.r_[False, np.abs(np.diff(np.log(f["value"].to_numpy()))) * 1e4 < STALE_BP]
    counts["dropped_stale"] = int(stale.sum())
    f = f[~stale].reset_index(drop=True)
    hhmm, zone = FIX_AT.split(" ")
    local = f["observation_date"] + pd.Timedelta(hours=int(hhmm[:2]), minutes=int(hhmm[3:]))
    f["fix_utc"] = pd.DatetimeIndex(local).tz_localize(zone).tz_convert("UTC")
    ny_close = pd.DatetimeIndex(f["observation_date"] + pd.Timedelta(hours=16)).tz_localize("America/New_York")
    f["available_utc"] = ny_close.tz_convert("UTC").normalize() + pd.Timedelta(days=1)
    f["source"] = "spdr_gld_nav_implied_lbma_pm"
    sealed = f["fix_utc"] >= HOLDOUT_CUT
    counts["dropped_holdout"] = int(sealed.sum())
    f = f[~sealed].reset_index(drop=True)
    counts["rows_kept"] = len(f)
    return f, counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--xlsx", type=Path, default=None, help="parse this file instead of fetching")
    a = ap.parse_args(argv)
    out_dir = a.root / "fixes"
    src = a.xlsx if a.xlsx is not None else fetch(out_dir / "_src")
    f, counts = implied_fix(read_sheet(src, SHEET))
    csv = out_dir / f"{FIX_ID}.csv"
    out = f.assign(observation_date=f["observation_date"].dt.strftime("%Y-%m-%d"))
    out[["observation_date", "value", "fix_utc", "available_utc", "source"]].to_csv(csv, index=False)
    meta = {"fix_id": FIX_ID, "at": FIX_AT, "url": URL, "source_file": src.name,
            "source_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
            "csv_sha256": hashlib.sha256(csv.read_bytes()).hexdigest(), "first": str(out["observation_date"].iloc[0]),
            "last": str(out["observation_date"].iloc[-1]), "holdout_cut": HOLDOUT_CUT.isoformat(),
            "calendar_sha256": calendar_sha256(), "stale_bp": STALE_BP, **counts,
            "bias": "value = PM price less accrued trust liabilities per ounce (sponsor fee, <= ~3.3 bp, low side)",
            "written_utc": dt.datetime.now(dt.UTC).isoformat()}
    (out_dir / f"{FIX_ID}.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
