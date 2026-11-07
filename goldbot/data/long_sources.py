"""Free sources for the long daily history and the per-instrument ranking (goldbot.data.long_history holds the
row contract and the stitch). Local research only: downloads are cached under the git-ignored
`raw/daily/_long_cache/`; nothing here is ever committed or published (ADR 0048).

Every source records its provider and licence (`LICENCES`); the stitched files carry `source` per row, and
`scripts/long_daily_history.py` writes a manifest with sha256, licence and overlap gaps next to them.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.data.daily import CUTOFF, DAILY_ROOT
from goldbot.data.long_history import (
    DEM_PER_EUR,
    TROY_OZ_PER_KG,
    OverlapStat,
    fixing_frame,
    goldhub_frame,
    load_dukascopy_daily,
    load_goldhub,
    parse_bbk_csv,
    parse_ecb_csv,
    parse_eia_xls,
    stitch,
    to_long,
    utc_stamp,
)

CACHE = DAILY_ROOT / "_long_cache"
LONG_ROOT = DAILY_ROOT / "long"
UA = {"User-Agent": "Mozilla/5.0 (goldbot research; local use)"}
YAHOO_P1 = int(pd.Timestamp("1920-01-01", tz="UTC").timestamp())
YAHOO_P2 = int((CUTOFF - pd.Timedelta(seconds=1)).timestamp())
# last ECB reference-rate date requested: the day before the research cutoff (the holdout stays sealed at source)
ECB_END = f"{CUTOFF - pd.Timedelta(days=1):%Y-%m-%d}"

LICENCES: dict[str, str] = {
    "dukascopy": "Dukascopy historical data: no redistribution (ADR 0048); local research only.",
    "synthetic": "Derived from Dukascopy bars in this repo's raw/ (same terms as Dukascopy).",
    "wgc_goldhub": "World Gold Council Goldhub (LBMA Gold Price PM, ICE Benchmark Administration IP): personal, "
                   "non-commercial research use under the Goldhub terms; never redistributed or published.",
    "bbk": "Deutsche Bundesbank time-series database (BBEX3; Frankfurt fixings): free use with the source "
           "'Deutsche Bundesbank' cited; kept local regardless.",
    "ecb": "European Central Bank Data Portal (EXR reference rates): reuse permitted with the ECB cited; kept local.",
    "eia": "U.S. Energy Information Administration spot prices: U.S. government public domain (EIA cites "
           "Refinitiv as the underlying source); kept local.",
    "yahoo": "Yahoo Finance chart data: personal, non-commercial use only, no redistribution; exchange/index "
             "provider IP. Local research only.",
}


def licence_for(source: str) -> str:
    return LICENCES.get(source.split(":")[0].split("_h1")[0].split("_1m")[0], "unknown: check before use")


# ----------------------------------------------------------------------------------------------------- download


def fetch_cached(url: str, path: Path, refresh: bool = False, tries: int = 4,
                 check: Callable[[Path], object] | None = None) -> bytes:
    """GET `url` once into `path` (atomic), then serve the cached bytes; exponential backoff on failure.

    `check` parses the downloaded file before it is cached: a body that does not parse (an HTTP-200 error page, a
    truncated or rate-limited response) is never written to `path`, so it cannot poison every later offline run.
    A cached file that no longer passes `check` is re-downloaded."""
    if path.exists() and path.stat().st_size > 0 and not refresh:
        try:
            if check is not None:
                check(path)
            return path.read_bytes()
        except Exception:  # noqa: BLE001 - a bad cache entry is replaced by a fresh download
            path.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.tmp{path.suffix}")  # keeps the suffix the parser sniffs
    err: Exception | None = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 - fixed https hosts
                data: bytes = r.read()
            tmp.write_bytes(data)
            if check is not None:
                check(tmp)
            tmp.replace(path)
            return data
        except Exception as e:  # noqa: BLE001 - retried, then re-raised
            err = e
            tmp.unlink(missing_ok=True)
            time.sleep(2.0 * 2**k)
    raise RuntimeError(f"download failed after {tries} tries: {url}: {err}")


def _nonempty(s: pd.Series | pd.DataFrame, what: str) -> None:
    if s.empty:
        raise ValueError(f"{what}: parsed to no rows")


def _yahoo_check(p: Path) -> None:
    res = json.loads(p.read_bytes())["chart"]["result"][0]
    if not res.get("timestamp"):
        raise ValueError("yahoo: no timestamps")


def _bbk_check(p: Path) -> None:
    _nonempty(parse_bbk_csv(p.read_bytes().decode("utf-8-sig")), "bbk")


def _ecb_check(p: Path) -> None:
    _nonempty(parse_ecb_csv(p.read_bytes().decode("utf-8")), "ecb")


def _eia_check(p: Path) -> None:
    _nonempty(parse_eia_xls(p), "eia")


def yahoo(ticker: str, refresh: bool = False, stamp_hour: int = 23, stamp_minute: int = 0) -> pd.DataFrame:
    """Yahoo chart API daily bars, full history to the cutoff, dated in the exchange's local date, stamped
    `date stamp_hour:stamp_minute UTC`."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}"
           f"?period1={YAHOO_P1}&period2={YAHOO_P2}&interval=1d&events=history")
    name = "".join(ch for ch in ticker.lower() if ch.isalnum())
    js = json.loads(fetch_cached(url, CACHE / f"yahoo_{name}.json", refresh, check=_yahoo_check))
    res = js["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    tz = res["meta"].get("exchangeTimezoneName") or "America/New_York"
    ts = pd.to_datetime(pd.Series(res["timestamp"], dtype="int64"), unit="s", utc=True).dt.tz_convert(tz)
    df = pd.DataFrame({"date": pd.DatetimeIndex(ts.dt.tz_localize(None)).as_unit("ns").normalize(),
                       "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"]})
    df = df.dropna(subset=["close"])
    df["available_utc"] = utc_stamp(df["date"], stamp_hour, stamp_minute)
    return to_long(df, f"yahoo:{ticker}")


def bbk(key: str, refresh: bool = False) -> pd.Series:
    """One Bundesbank BBEX3 daily series, e.g. `D.USD.DEM.AA.AC.000` (DEM per USD, Frankfurt fixing)."""
    url = f"https://api.statistiken.bundesbank.de/rest/data/BBEX3/{key}?format=csv&lang=en"
    return parse_bbk_csv(fetch_cached(url, CACHE / f"bbk_{key}.csv", refresh, check=_bbk_check)
                         .decode("utf-8-sig"))


def ecb(currency: str, refresh: bool = False) -> pd.Series:
    """ECB euro reference rate: units of `currency` per EUR, 1999-01-04 on."""
    url = (f"https://data-api.ecb.europa.eu/service/data/EXR/D.{currency}.EUR.SP00.A?format=csvdata"
           f"&startPeriod=1999-01-01&endPeriod={ECB_END}")
    return parse_ecb_csv(fetch_cached(url, CACHE / f"ecb_{currency}.csv", refresh, check=_ecb_check).decode("utf-8"))


def eia(series: str, refresh: bool = False) -> pd.Series:
    """EIA daily spot series, e.g. RWTC (WTI Cushing), RBRTE (Brent), RNGWHHD (Henry Hub)."""
    path = CACHE / f"eia_{series}.xls"
    fetch_cached(f"https://www.eia.gov/dnav/pet/hist_xls/{series}d.xls" if series != "RNGWHHD"
                 else "https://www.eia.gov/dnav/ng/hist_xls/RNGWHHDd.xls", path, refresh, check=_eia_check)
    return parse_eia_xls(path)


# ------------------------------------------------------------------------------------------- derived fixings


def _bbk_dem(cur: str, refresh: bool) -> pd.Series:
    """DEM per ONE unit of `cur` (JPY and CHF are quoted per 100)."""
    s = bbk(f"D.{cur}.DEM.AA.AC.000", refresh)
    return s / 100.0 if cur in ("JPY", "CHF") else s


def bbk_gold_usd(refresh: bool = False) -> pd.Series:
    """Frankfurt gold fixing (DEM per kg, 1968-06-18..1998-12-31) / Frankfurt USD fixing -> USD per troy ounce."""
    gold = bbk("D.XAU.DEM.EA.AC.C01", refresh)
    usd = _bbk_dem("USD", refresh)
    j = pd.concat([gold, usd], axis=1, join="inner").dropna()
    return (j.iloc[:, 0] / j.iloc[:, 1] / TROY_OZ_PER_KG).rename("close")


def _pair(base: str, quote: str, per: dict[str, pd.Series], home: str) -> pd.Series:
    """Units of `quote` per one `base`. home "DEM": per[c] = DEM per c, EUR = the synthetic DEM-euro (1.95583 DEM),
    price = DEM/base over DEM/quote. home "EUR": per[c] = c per EUR, price = (quote/EUR) over (base/EUR)."""
    unit = DEM_PER_EUR if home == "DEM" else 1.0

    def leg(c: str) -> pd.Series:
        return pd.Series(unit, index=per[next(iter(per))].index) if c == "EUR" else per[c]

    b, q = leg(base), leg(quote)
    j = pd.concat([b, q], axis=1, join="inner").dropna()
    out = j.iloc[:, 0] / j.iloc[:, 1] if home == "DEM" else j.iloc[:, 1] / j.iloc[:, 0]
    return out.rename("close")


def bbk_fx(pair: str, refresh: bool = False) -> pd.Series | None:
    base, quote = pair[:3], pair[3:]
    if not {base, quote} <= BBK_FX:
        return None
    per = {c: _bbk_dem(c, refresh) for c in sorted({base, quote} - {"EUR"})}
    return _pair(base, quote, per, "DEM")


def ecb_fx(pair: str, refresh: bool = False) -> pd.Series | None:
    base, quote = pair[:3], pair[3:]
    if not {base, quote} <= ECB_FX:
        return None
    per = {c: ecb(c, refresh) for c in sorted({base, quote} - {"EUR"})}
    return _pair(base, quote, per, "EUR")


# ------------------------------------------------------------------------------------------- instrument specs

BBK_FX = {"USD", "GBP", "JPY", "CHF", "CAD", "EUR"}          # Frankfurt fixings with a daily DEM rate
ECB_FX = {"USD", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "EUR"}


class SourceRef(FrozenRecord):
    kind: str          # yahoo | eia | bbk_fx | ecb_fx | bbk_gold | goldhub
    ref: str = ""      # ticker / series / pair / path


class InstrumentSpec(FrozenRecord):
    key: str
    dukascopy: tuple[str, ...]           # anchor candidates under raw/daily/, first that exists wins
    older: tuple[SourceRef, ...]         # ranked, best first, after the Dukascopy anchor
    note: str = ""


def _y(t: str) -> SourceRef:
    return SourceRef(kind="yahoo", ref=t)


def _fx(pair: str) -> tuple[SourceRef, ...]:
    refs: list[SourceRef] = []
    if {pair[:3], pair[3:]} <= ECB_FX:
        refs.append(SourceRef(kind="ecb_fx", ref=pair))
    if {pair[:3], pair[3:]} <= BBK_FX:
        refs.append(SourceRef(kind="bbk_fx", ref=pair))
    return tuple(refs)


def _spec(key: str, older: tuple[SourceRef, ...], *duka: str, note: str = "") -> InstrumentSpec:
    return InstrumentSpec(key=key, dukascopy=duka or (f"bars/{key.lower()}_1d.parquet",), older=older, note=note)


FUTURES_NOTE = "Yahoo continuous front-month futures: level carries the futures basis and roll days jump."
SPECS: dict[str, InstrumentSpec] = {s.key: s for s in (
    _spec("XAUUSD", (SourceRef(kind="goldhub"), _y("GC=F"), SourceRef(kind="bbk_gold")),
          note="Goldhub (if downloaded) > COMEX GC=F (2000-08+) > Frankfurt fixing in USD (1968-06..1998-12)."),
    _spec("XAGUSD", (_y("SI=F"),), note=FUTURES_NOTE),
    _spec("XPTUSD", (_y("PL=F"),), "bars/xptusd_1d.parquet", "xptusd.parquet", note=FUTURES_NOTE),
    _spec("EURUSD", _fx("EURUSD"), note="Before 1999: synthetic DEM-euro (1.95583 DEM), not the ECU."),
    _spec("USDJPY", _fx("USDJPY")),
    _spec("GBPUSD", _fx("GBPUSD")),
    _spec("USDCHF", _fx("USDCHF")),
    _spec("USDCAD", _fx("USDCAD")),
    _spec("AUDUSD", _fx("AUDUSD")),
    _spec("NZDUSD", _fx("NZDUSD")),
    _spec("EURGBP", _fx("EURGBP")),
    _spec("EURJPY", _fx("EURJPY")),
    _spec("GBPJPY", _fx("GBPJPY")),
    _spec("AUDJPY", _fx("AUDJPY")),
    _spec("EURAUD", _fx("EURAUD")),
    _spec("GBPAUD", _fx("GBPAUD")),
    _spec("USINDEX", (_y("DX-Y.NYB"),), note="ICE US Dollar Index (DX-Y.NYB) before the synthetic Dukascopy USDX."),
    _spec("US500", (SourceRef(kind="yahoo", ref="^GSPC"),), "bars/us500_1d.parquet", "us500.parquet",
          note="S&P 500 cash index; open=high=low=close before 1962 (blanked)."),
    _spec("USTEC", (_y("^NDX"),), "bars/ustec_1d.parquet", "us100.parquet", note="Nasdaq-100 cash index."),
    _spec("US30", (_y("^DJI"),), note="Dow Jones Industrial Average cash index."),
    _spec("DE40", (_y("^GDAXI"),), "bars/de40_1d.parquet", "ger40.parquet", note="DAX cash index."),
    _spec("UK100", (_y("^FTSE"),), note="FTSE 100 cash index."),
    _spec("JP225", (_y("^N225"),), "jp225.parquet", note="Nikkei 225 cash index (Tokyo date)."),
    _spec("XTIUSD", (SourceRef(kind="eia", ref="RWTC"), _y("CL=F")), "bars/xtiusd_1d.parquet", "wti.parquet",
          note="EIA WTI Cushing spot (1986+)."),
    _spec("XBRUSD", (SourceRef(kind="eia", ref="RBRTE"), _y("BZ=F")), note="EIA Brent spot (1987+); no Dukascopy file."),
    _spec("XNGUSD", (_y("NG=F"), SourceRef(kind="eia", ref="RNGWHHD")),
          note="NYMEX NG=F (2000+, complete) before EIA Henry Hub spot (1997-2000; EIA has month-long holes "
               "in 2003-04). " + FUTURES_NOTE),
    _spec("COPPER", (_y("HG=F"),), note=FUTURES_NOTE + " No Dukascopy file."),
    _spec("UST10Y", (_y("ZN=F"),), note="CBOT 10-year T-note futures price; " + FUTURES_NOTE),
    _spec("WHEAT", (_y("ZW=F"),), note=FUTURES_NOTE),
    _spec("SOYBEAN", (_y("ZS=F"),), note=FUTURES_NOTE),
    _spec("SUGAR", (_y("SB=F"),), note=FUTURES_NOTE),
    _spec("COFFEE", (_y("KC=F"),), note=FUTURES_NOTE),
)}
PRIORITY = ("XAUUSD", "XAGUSD", "USINDEX", "EURUSD", "US500", "XTIUSD", "XBRUSD")


def find_goldhub(dirs: tuple[Path, ...]) -> Path | None:
    """The newest .xlsx in `dirs` whose name looks like a WGC/Goldhub prices workbook."""
    hits: list[Path] = []
    for d in dirs:
        if d.is_dir():
            hits += [p for p in d.glob("*.xls*")
                     if any(k in p.name.lower() for k in ("goldhub", "gold_price", "gold price", "gold-price",
                                                          "prices", "wgc"))
                     and "gold" in p.name.lower()]
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def load_source(ref: SourceRef, refresh: bool = False, goldhub_path: Path | None = None) -> pd.DataFrame | None:
    """One ranked source -> long rows, or None when it is unavailable (a missing Goldhub file, a pair no
    provider quotes)."""
    if ref.kind == "yahoo":
        return yahoo(ref.ref, refresh, *((21, 15) if ref.ref == "^GSPC" else (23, 0)))
    if ref.kind == "eia":
        return fixing_frame(eia(ref.ref, refresh), f"eia:{ref.ref}", "UTC", 23)
    if ref.kind == "bbk_gold":
        return fixing_frame(bbk_gold_usd(refresh), "bbk:XAU_DEM_frankfurt_fix_in_USD", "Europe/Berlin", 17)
    if ref.kind == "goldhub":
        return goldhub_frame(load_goldhub(goldhub_path)) if goldhub_path is not None else None
    if ref.kind in ("bbk_fx", "ecb_fx"):
        fn: Callable[[str, bool], pd.Series | None] = bbk_fx if ref.kind == "bbk_fx" else ecb_fx
        s = fn(ref.ref, refresh)
        if s is None or s.empty:
            return None
        src, hh = ("bbk:frankfurt_fix", 17) if ref.kind == "bbk_fx" else ("ecb:reference_rate", 16)
        return fixing_frame(s, f"{src}:{ref.ref}", "Europe/Berlin", hh)
    raise ValueError(f"unknown source kind {ref.kind!r}")


def load_anchor(spec: InstrumentSpec, root: Path = DAILY_ROOT) -> pd.DataFrame | None:
    for rel in spec.dukascopy:
        p = root / rel
        if p.exists():
            return load_dukascopy_daily(p)
    return None


class BuildResult(FrozenRecord):
    key: str
    rows: int
    overlaps: tuple[OverlapStat, ...]
    failures: tuple[str, ...]          # sources that could not be loaded, with the reason
    anchor: str | None


def build_instrument(spec: InstrumentSpec, refresh: bool = False, goldhub_path: Path | None = None,
                     root: Path = DAILY_ROOT,
                     loader: Callable[[SourceRef], pd.DataFrame | None] | None = None,
                     ) -> tuple[pd.DataFrame, BuildResult]:
    """Dukascopy anchor + ranked older sources -> the stitched series and its build record. A source that fails
    is recorded and skipped (the next-ranked one still contributes); the stitch is validated before return."""
    anchor = load_anchor(spec, root)
    segs: list[pd.DataFrame] = [anchor] if anchor is not None else []
    failures: list[str] = []
    for ref in spec.older:
        try:
            seg = loader(ref) if loader is not None else load_source(ref, refresh, goldhub_path)
        except Exception as e:  # noqa: BLE001 - one bad source must not sink the instrument
            failures.append(f"{ref.kind}:{ref.ref}: {type(e).__name__}: {e}"[:300])
            continue
        if seg is None or seg.empty:
            failures.append(f"{ref.kind}:{ref.ref}: unavailable")
            continue
        segs.append(seg)
    df, stats = stitch(segs)
    anchor_src = str(anchor["source"].iloc[0]) if anchor is not None and not anchor.empty else None
    return df, BuildResult(key=spec.key, rows=len(df), overlaps=tuple(stats), failures=tuple(failures),
                           anchor=anchor_src)
