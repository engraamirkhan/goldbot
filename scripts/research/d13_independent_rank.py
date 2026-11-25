"""D-13 PRE-SCREEN (read-only): order candidate additional instruments by how many INDEPENDENT graded learn days they
could add, outcome-blind. A pre-screen only: it decides what to pull and measure first, never admission.

Nothing here reads a label, a signal or a P&L, and nothing is written to raw/ or the repo: Dukascopy day candles (one
bi5 per instrument-year, bid, capped at the last research day 2025-09-30 before caching) go to ~/tmp/d13/cache and the
pre-screen to --out.

Per candidate (quant CH3, 2026-11-26):
* rho_learn: Dimson correlation of daily log returns with Dukascopy XAUUSD over learn 2010-01..2017-12, the sum of the
  lag -1, 0 and +1 correlations (non-synchronous closes: an index closing hours before gold's day boundary puts part of
  its co-movement in the next day), |rho| capped at 1. Pearson at lag 0 and Spearman beside it.
* route: what r11-v2.x can grade it against (HistData independent through 2018, same source from 2019; FRED H.10 fix for
  FX -> A-consistent possible; cap B for index/energy; no HistData -> every month C under the rule).
* ab_learn (projection, labelled so): learn days x the A/A-consistent/B share of months the graded instruments of the
  same route reached (served tiers file).
* info_learn = ab_learn x (1 - rho_learn^2). THE ORDER IS info_learn ONLY.
* judge columns (rho_judge, days_judge, ab_judge) are REPORTED ONLY: they never sort, filter or admit.
* missing_years: years with no day candle file (404 or a fetch that kept failing); never dropped silently.
Admission (not here): scripts/research/measure_rho.py on the learn window: the day-mean rho_u,inst of the candidate vs
gold, upper 95 % bound, max over PC1 and the per-instrument sign, <= 0.2 in every hold bucket.

    uv run python scripts/research/d13_independent_rank.py --tiers raw/tiers/tiers_served_r11-v2.2.csv --out ~/tmp/d13
"""
from __future__ import annotations

import argparse
import http.client
import json
import lzma
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data.holdout_guard import HOLDOUT_START

DATAFEED = "https://datafeed.dukascopy.com/datafeed"
UA = {"User-Agent": "goldbot-data/1"}
YEARS = range(2010, 2026)
CUT = HOLDOUT_START.tz_localize(None)            # day candles are naive UTC dates
LEARN = (pd.Timestamp("2010-01-01"), pd.Timestamp("2018-01-01"))
JUDGE = (pd.Timestamp("2018-01-01"), CUT)
REC = np.dtype([("sec", ">i4"), ("open", ">i4"), ("close", ">i4"), ("low", ">i4"), ("high", ">i4"),
                ("volume", ">f4")])
# FRED H.10 noon buying rates exist for these currencies (DEX* series): an FX pair of USD and these has a fix route
H10 = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK", "MXN", "ZAR", "SGD", "HKD", "CNY"}
# histdata.com free M1 list (fetched 2026-11-25): the only independent minute reference the frozen rule accepts
HISTDATA = set("""AUDCAD AUDCHF AUDJPY AUDNZD AUDUSD AUXAUD BCOUSD CADCHF CADJPY CHFJPY ETXEUR EURAUD EURCAD EURCHF EURCZK
EURDKK EURGBP EURHUF EURJPY EURNOK EURNZD EURPLN EURSEK EURTRY EURUSD FRXEUR GBPAUD GBPCAD GBPCHF GBPJPY GBPNZD GBPUSD
GRXEUR HKXHKD JPXJPY NSXUSD NZDCAD NZDCHF NZDJPY NZDUSD SGDJPY SPXUSD UDXUSD UKXGBP USDCAD USDCHF USDCZK USDDKK USDHKD
USDHUF USDJPY USDMXN USDNOK USDPLN USDSEK USDSGD USDTRY USDZAR WTIUSD XAGUSD XAUAUD XAUCHF XAUEUR XAUGBP XAUUSD
ZARJPY""".split())
# candidate Dukascopy code -> (class, HistData code or None). Owner keys already in the store are listed apart.
CANDIDATES: dict[str, tuple[str, str | None]] = {
    # indices
    "JPNIDXJPY": ("index", "JPXJPY"), "HKGIDXHKD": ("index", "HKXHKD"), "AUSIDXAUD": ("index", "AUXAUD"),
    "FRAIDXEUR": ("index", "FRXEUR"), "EUSIDXEUR": ("index", "ETXEUR"), "CHIIDXUSD": ("index", None),
    "ESPIDXEUR": ("index", None), "NLDIDXEUR": ("index", None), "CHEIDXCHF": ("index", None),
    "USSC2000IDXUSD": ("index", None), "INDIDXUSD": ("index", None),
    # energy and industrial/precious metals
    "BRENTCMDUSD": ("energy", "BCOUSD"), "GASCMDUSD": ("energy", None), "DIESELCMDUSD": ("energy", None),
    "COPPERCMDUSD": ("metal", None), "XPTCMDUSD": ("metal", None), "XPDCMDUSD": ("metal", None),
    # rates (bond CFDs)
    "USTBONDTRUSD": ("rates", None), "BUNDTREUR": ("rates", None), "UKGILTTRGBP": ("rates", None),
    # agriculture
    "SOYBEANCMDUSX": ("agri", None), "COFFEECMDUSX": ("agri", None), "COCOACMDUSD": ("agri", None),
    "SUGARCMDUSD": ("agri", None), "COTTONCMDUSX": ("agri", None), "ORANGEJUICECMDUSX": ("agri", None),
    "WHEATCMDUSX": ("agri", None), "CORNCMDUSX": ("agri", None),
    # crypto
    "BTCUSD": ("crypto", None), "ETHUSD": ("crypto", None), "LTCUSD": ("crypto", None),
    # FX not yet in the store
    **{c: ("fx", c) for c in ("USDMXN", "USDZAR", "USDTRY", "USDNOK", "USDSEK", "USDSGD", "USDHKD", "USDPLN", "USDCZK",
                              "USDHUF", "USDDKK", "EURCHF", "EURNOK", "EURSEK", "EURPLN", "EURTRY", "CADJPY", "CHFJPY",
                              "AUDNZD", "AUDCAD", "AUDCHF", "NZDJPY", "EURCAD", "EURNZD", "GBPCHF", "GBPCAD", "CADCHF",
                              "NZDCAD", "NZDCHF", "GBPNZD", "ZARJPY", "SGDJPY")},
    "USDCNH": ("fx", None), "USDTHB": ("fx", None), "USDILS": ("fx", None),
}
# owner keys already graded (served tiers key -> Dukascopy code): the baseline the candidates are compared with
STORED: dict[str, str] = {
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY", "USDCHF": "USDCHF", "USDCAD": "USDCAD",
    "AUDUSD": "AUDUSD", "NZDUSD": "NZDUSD", "EURGBP": "EURGBP", "EURJPY": "EURJPY", "GBPJPY": "GBPJPY",
    "AUDJPY": "AUDJPY", "EURAUD": "EURAUD", "GBPAUD": "GBPAUD", "XAGUSD": "XAGUSD", "XAUEUR": "XAUEUR",
    "US500": "USA500IDXUSD", "USTEC": "USATECHIDXUSD", "US30": "USA30IDXUSD", "UK100": "GBRIDXGBP",
    "DE40": "DEUIDXEUR", "XTIUSD": "LIGHTCMDUSD", "USINDEX": "DOLLARIDXUSD",
}
ROUTE_OF_STORED = {**{k: "histdata+h10" for k in ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
                                                  "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "EURAUD", "GBPAUD")},
                   **{k: "histdata+cap_b" for k in ("US500", "USTEC", "US30", "UK100", "DE40", "XTIUSD", "USINDEX")},
                   "XAGUSD": "histdata+pending", "XAUEUR": "histdata+pending"}


def route(cls: str, code: str, hd: str | None) -> str:
    if hd is None or hd not in HISTDATA:
        return "none"
    if cls == "fx":
        return "histdata+h10" if {code[:3], code[3:6]} <= H10 else "histdata+pending"
    return "histdata+cap_b"


def cap(body: bytes, year: int) -> bytes:
    """The day records strictly before CUT, re-compressed: a year file never holds a sealed day on disk (L4)."""
    raw = lzma.decompress(body)
    rec = np.frombuffer(raw[: len(raw) // 24 * 24], dtype=REC)
    day = pd.Timestamp(f"{year}-01-01") + pd.to_timedelta(rec["sec"].astype("int64"), unit="s")
    keep = np.asarray(day < CUT)
    return body if keep.all() else lzma.compress(rec[keep].tobytes(), format=lzma.FORMAT_ALONE)


def fetch(code: str, year: int, cache: Path, pause: float) -> bytes | None:
    p = cache / code / f"{year}.bi5"
    miss = p.with_suffix(".404")
    if p.exists():
        body = p.read_bytes()
        capped = cap(body, year)
        if capped != body:                       # a file cached before the cap existed
            p.write_bytes(capped)
        return capped
    if miss.exists():
        return None
    url = f"{DATAFEED}/{code}/{year}/BID_candles_day_1.bi5"
    for k in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:  # noqa: S310
                body = cap(r.read(), year)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body)
            time.sleep(pause)
            return body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                miss.parent.mkdir(parents=True, exist_ok=True)
                miss.write_bytes(b"")
                return None
            time.sleep(min(60.0, 5.0 * 2 ** k))
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, lzma.LZMAError):
            time.sleep(min(60.0, 5.0 * 2 ** k))
    return None


def daily_closes(code: str, cache: Path, pause: float) -> tuple[pd.Series, list[int]]:
    """(weekday closes before CUT, years with no file): a missing year is recorded, never dropped silently (L3)."""
    parts, missing = [], []
    for y in YEARS:
        b = fetch(code, y, cache, pause)
        if not b:
            missing.append(y)
            continue
        raw = lzma.decompress(b)
        rec = np.frombuffer(raw[: len(raw) // 24 * 24], dtype=REC)
        f = pd.DataFrame({"sec": rec["sec"].astype("int64"), "close": rec["close"].astype("float64"),
                          "volume": rec["volume"].astype("float64")})
        f = f[(f["volume"] > 0) & (f["close"] > 0)]
        day = pd.Timestamp(f"{y}-01-01") + pd.to_timedelta(f["sec"].to_numpy(), unit="s")
        parts.append(pd.Series(f["close"].to_numpy(), index=pd.DatetimeIndex(day).normalize()))
    if not parts:
        return pd.Series(dtype="float64"), missing
    s = pd.concat(parts)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    idx = pd.DatetimeIndex(s.index)
    return s[(idx < CUT) & (idx.dayofweek < 5)], missing


def _corr(a: pd.Series, b: pd.Series, lo: pd.Timestamp, hi: pd.Timestamp
          ) -> tuple[float | None, float | None, float | None, int]:
    """(Dimson rho = sum of the lag -1, 0, +1 correlations of daily log returns, Pearson lag 0, Spearman lag 0, pairs)
    on the common days in [lo, hi)."""
    j = pd.concat([np.log(a).diff(), np.log(b).diff()], axis=1, join="inner").dropna()
    j.columns = ["x", "g"]
    j = j[(j.index >= lo) & (j.index < hi)]
    if len(j) < 60:
        return None, None, None, len(j)
    dimson = sum(float(j["x"].corr(j["g"].shift(-k))) for k in (-1, 0, 1))
    return dimson, float(j["x"].corr(j["g"])), float(j["x"].corr(j["g"], method="spearman")), len(j)


def ab_rates(tiers: Path) -> dict[str, dict[str, float]]:
    """Share of graded months at A, A-consistent or B per route and window, from the served r11-v2.1 tiers."""
    t = pd.read_csv(tiers, dtype={"month": str})
    t = t[t["key"].isin(ROUTE_OF_STORED)]
    t["route"] = t["key"].map(ROUTE_OF_STORED)
    t["ab"] = t["tier"].isin(["A", "A-consistent", "B"])
    m = pd.PeriodIndex(t["month"], freq="M").to_timestamp()
    out: dict[str, dict[str, float]] = {}
    for name, (lo, hi) in (("learn", LEARN), ("judge", JUDGE)):
        w = t[(m >= lo) & (m < hi)]
        for r, g in w.groupby("route"):
            out.setdefault(str(r), {})[name] = float(g["ab"].mean())
    out["none"] = {"learn": 0.0, "judge": 0.0}
    return out


def actual_ab_days(tiers: Path, key: str) -> tuple[int, int]:
    t = pd.read_csv(tiers, dtype={"month": str})
    t = t[(t["key"] == key) & t["tier"].isin(["A", "A-consistent", "B"])]
    m = pd.PeriodIndex(t["month"], freq="M").to_timestamp()
    return (int(t.loc[(m >= LEARN[0]) & (m < LEARN[1]), "days"].sum()),
            int(t.loc[(m >= JUDGE[0]) & (m < JUDGE[1]), "days"].sum()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tiers", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path.home() / "tmp" / "d13")
    ap.add_argument("--pause", type=float, default=0.5)
    a = ap.parse_args()
    cache = a.out / "cache"
    gold, _ = daily_closes("XAUUSD", cache, a.pause)
    rates = ab_rates(a.tiers)
    rows = []
    todo = [(c, cls, hd, False) for c, (cls, hd) in CANDIDATES.items()] + \
        [(code, "stored", key, True) for key, code in STORED.items()]
    for code, cls, hd, stored in todo:
        s, missing = daily_closes(code, cache, a.pause)
        rec: dict[str, object] = {"code": code, "class": cls, "stored": stored, "missing_years": missing,
                                  "screen": "pre-screen (admission: measure_rho.py)"}
        if stored:
            rec["route"] = ROUTE_OF_STORED[str(hd)]
            rec["key"] = hd
        else:
            rec["route"] = route(cls, code, hd)
            rec["histdata"] = hd if hd in HISTDATA else None
        for name, (lo, hi) in (("learn", LEARN), ("judge", JUDGE)):
            dim, p, sp, n = _corr(s, gold, lo, hi) if len(s) else (None, None, None, 0)
            days = int(((s.index >= lo) & (s.index < hi)).sum()) if len(s) else 0
            rec.update({f"rho_{name}": dim, f"pearson_{name}": p, f"spearman_{name}": sp, f"days_{name}": days,
                        f"pairs_{name}": n})
            rec[f"ab_{name}"] = round(days * rates.get(str(rec["route"]), {}).get(name, 0.0))
        if stored:
            rec["ab_learn_actual"], rec["ab_judge_actual"] = actual_ab_days(a.tiers, str(hd))
        rl = rec["rho_learn"]
        r = min(abs(float(str(rl))), 1.0) if rl is not None else None
        rec["info_learn"] = round(float(str(rec["ab_learn"])) * (1 - r ** 2)) if r is not None else 0
        rec["first_day"] = str(s.index.min().date()) if len(s) else None
        rows.append(rec)
        print(json.dumps(rec, default=str), flush=True)
    df = pd.DataFrame(rows).sort_values(["stored", "info_learn"], ascending=[True, False])   # learn only
    a.out.mkdir(parents=True, exist_ok=True)
    df.to_csv(a.out / "d13_prescreen.csv", index=False)
    (a.out / "d13_ab_rates.json").write_text(json.dumps(rates, indent=1))
    cols = ["code", "class", "route", "rho_learn", "days_learn", "ab_learn", "info_learn", "missing_years",
            "rho_judge", "days_judge", "ab_judge"]                     # judge columns: reported only
    print(json.dumps(rates))
    print(df[cols].round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
