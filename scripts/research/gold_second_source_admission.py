"""D-1 fallback (i): admission of a second independent 1m XAUUSD source for 2019-01..2025-09 (ADR 0105, quant ruling
2026-11-26). Outcome-blind: prices against prices; nothing is graded here.

One run, in this order (the ruling's order):
1. controls, written to the output JSON before any candidate number exists:
   * negative: HistData XAUUSD vs Dukascopy, 2010-2018, (b*) per year (< 0.10 expected; a year >= 0.10 makes the bound
     the 2010-18 maximum);
   * positive: HistData XAUUSD vs Dukascopy, 2019-2025, (b*) per year >= 0.50 (the same feed must be detected);
   * synthetic: Dukascopy + 0.37 vs Dukascopy, (b*) per year 2019-2025 >= 0.90;
2. the candidate (Forexite, raw/forexite_m1/_zips/DDMMYY.zip, the ASCII "MetaStock" files) per year 2019-2025:
   coverage, the r11 clock offset after the documented converter, (a) and (b*).
(b*) = the share of identical 1m close-to-close changes, both series rounded to the coarser tick grid, over the
consecutive common minutes where at least one series moved. (a) = the share of UTC hours whose last 1m close is equal
in both series on the coarser grid. Converter (documented "GMT+1", EU summer time per the ruling): local stamps as
Europe/Berlin -> UTC, then -1 min (close stamp -> open stamp). Coverage: share of Dukascopy session days (>= 60 % of
1,380 minutes) on which the candidate holds >= 60 % of Dukascopy's minutes; short below 0.95.

    uv run python scripts/research/gold_second_source_admission.py [--raw raw] [--out ~/tmp/dgrade/admission.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data.era_quality import HOLDOUT_CUT, R11, clock_check, day_of, epoch_ns_utc

ROOT = Path(__file__).resolve().parents[2]
NEG_YEARS = range(2010, 2019)
POS_YEARS = range(2019, 2026)
B_MAX, A_MAX, POS_MIN, SYN_MIN, COVER_MIN = 0.10, 0.10, 0.50, 0.90, 0.95
SYN_SHIFT = 0.37
FOREXITE_TICK = 0.01
FOREXITE_TZ = "Europe/Berlin"
PRICE_FIELD_DOCUMENTED = False      # ADR 0105: the MetaStock files' field (bid / mid / trade) is not stated by the source


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _cut(s: pd.Series, year: int) -> pd.Series:
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s = s[s.index < HOLDOUT_CUT]
    return s[np.asarray(day_of(pd.Series(s.index)).year == year)]


def dukascopy(raw: Path, year: int) -> pd.Series:
    parts = []
    for y in (year - 1, year):
        p = raw / f"xauusd_1m_dukascopy_{y}.parquet"
        if p.exists():
            b = pd.read_parquet(p, columns=["ts_utc", "bid_close", "dq_flag"])
            b = b[~b["dq_flag"].fillna("").astype(str).str.startswith("error")]
            parts.append(pd.Series(b["bid_close"].to_numpy(float), index=pd.DatetimeIndex(b["ts_utc"]).tz_convert("UTC")))
    return _cut(pd.concat(parts), year) if parts else pd.Series(dtype=float)


def histdata(raw: Path, year: int) -> pd.Series:
    parts = []
    for y in (year - 1, year):
        p = raw / "histdata_m1" / "XAUUSD" / f"xauusd_1m_bid_{y}.parquet"
        if p.exists():
            b = pd.read_parquet(p, columns=["ts_utc", "bid_close", "dq_flag"])
            b = b[~b["dq_flag"].fillna("").astype(str).str.startswith("error")]
            parts.append(pd.Series(b["bid_close"].to_numpy(float), index=pd.DatetimeIndex(b["ts_utc"]).tz_convert("UTC")))
    return _cut(pd.concat(parts), year) if parts else pd.Series(dtype=float)


def forexite_to_utc(local: pd.Series) -> pd.DatetimeIndex:
    """Documented converter: Europe/Berlin civil time -> UTC, then -1 min (close stamp -> open stamp). The DST switch
    hours (ambiguous / nonexistent) become NaT and are dropped by the caller."""
    t = pd.DatetimeIndex(local).tz_localize(FOREXITE_TZ, ambiguous="NaT", nonexistent="NaT").tz_convert("UTC")
    return t - pd.Timedelta(minutes=1)


def forexite(zips: Path, year: int) -> tuple[pd.Series, int]:
    """XAUUSD closes of the files dated Dec 31 of year-1 .. Dec 31 of year (UTC session days of `year` kept)."""
    parts, files = [], 0
    d = dt.date(year - 1, 12, 31)
    while d <= dt.date(year, 12, 31):
        p = zips / f"{d:%d%m%y}.zip"
        if p.exists() and p.stat().st_size > 0:
            files += 1
            with zipfile.ZipFile(p) as z:
                f = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
            f.columns = [c.strip("<>") for c in f.columns]
            f = f[f["TICKER"] == "XAUUSD"]
            if len(f):
                local = pd.to_datetime(f["DTYYYYMMDD"].astype(str) + f["TIME"].astype(str).str.zfill(6),
                                       format="%Y%m%d%H%M%S")
                parts.append(pd.Series(f["CLOSE"].to_numpy(float), index=forexite_to_utc(local)))
        d += dt.timedelta(days=1)
    if not parts:
        return pd.Series(dtype=float), files
    s = pd.concat(parts)
    return _cut(s[s.index.notna()], year), files


def tick_of(s: pd.Series) -> float:
    """The source grid: the coarsest 10^-k on which every price lies."""
    v = s.to_numpy(float)
    for k in range(0, 6):
        if np.all(np.abs(v * 10 ** k - np.round(v * 10 ** k)) < 1e-6):
            return 10.0 ** -k
    return 1e-6


def on_grid(v: np.ndarray, own: float, tick: float) -> np.ndarray:
    """Prices on the coarser grid in integer arithmetic: integer source points first (round(v / own tick)), then
    round-half-up to `tick` (ADR 0105): a constant shift moves every price by the same number of ticks."""
    pts = np.round(np.asarray(v, dtype="float64") / own).astype(np.int64)
    k = int(round(tick / own))
    return (pts + k // 2) // k if k > 1 else pts


def b_star(x: pd.Series, y: pd.Series, tick: float) -> tuple[float | None, int]:
    j = pd.concat([x.rename("x"), y.rename("y")], axis=1, join="inner").dropna()
    if len(j) < 2:
        return None, 0
    gx = on_grid(j["x"].to_numpy(), min(tick_of(j["x"]), tick), tick)
    gy = on_grid(j["y"].to_numpy(), min(tick_of(j["y"]), tick), tick)
    cont = np.diff(epoch_ns_utc(pd.Series(j.index))) == 60_000_000_000
    dx, dy = np.diff(gx)[cont], np.diff(gy)[cont]
    moved = (dx != 0) | (dy != 0)
    return (float((dx[moved] == dy[moved]).mean()) if moved.any() else None), int(moved.sum())


def a_share(x: pd.Series, y: pd.Series, tick: float) -> tuple[float | None, int]:
    hx = x.groupby(pd.DatetimeIndex(x.index).floor("h")).last()
    hy = y.groupby(pd.DatetimeIndex(y.index).floor("h")).last()
    j = pd.concat([hx.rename("x"), hy.rename("y")], axis=1, join="inner").dropna()
    if not len(j):
        return None, 0
    gx = on_grid(j["x"].to_numpy(), min(tick_of(j["x"]), tick), tick)
    gy = on_grid(j["y"].to_numpy(), min(tick_of(j["y"]), tick), tick)
    return float((gx == gy).mean()), len(j)


def coverage(cand: pd.Series, duk: pd.Series) -> tuple[float | None, int, int]:
    dd: pd.Series = day_of(pd.Series(duk.index)).value_counts()
    cd: pd.Series = day_of(pd.Series(cand.index)).value_counts() if len(cand) else pd.Series(dtype="int64")
    sess = dd[dd.to_numpy() >= 0.6 * 1380]
    if not len(sess):
        return None, 0, 0
    ok = cd.reindex(sess.index).fillna(0).to_numpy() >= 0.6 * sess.to_numpy()
    return float(ok.mean()), int(ok.sum()), len(sess)


def clock_offset(cand: pd.Series, duk: pd.Series, year: int) -> tuple[int | None, float | None]:
    h = duk.groupby(pd.DatetimeIndex(duk.index).floor("h")).last()
    ref = pd.DataFrame({"ts_utc": h.index, "close": h.to_numpy(), "spread": np.nan})
    cc = clock_check("XAUUSD", year, epoch_ns_utc(pd.Series(cand.index)), cand.to_numpy(float), ref)
    return cc.offset_min, cc.corr_at_offset


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", type=Path, default=ROOT / "raw")
    ap.add_argument("--out", type=Path, default=Path.home() / "tmp" / "dgrade" / "admission.json")
    a = ap.parse_args()
    out: dict[str, object] = {"script": "scripts/research/gold_second_source_admission.py",
                              "script_sha256": _sha(Path(__file__).resolve()), "ruling": "ADR 0105 (i)(b), 2026-11-26",
                              "started_utc": dt.datetime.now(dt.UTC).isoformat()}

    def write() -> None:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(out, indent=1, default=str))

    # ---- 1. controls first
    neg, pos, syn = {}, {}, {}
    for y in NEG_YEARS:
        d, h = dukascopy(a.raw, y), histdata(a.raw, y)
        t = max(tick_of(d), tick_of(h))
        neg[y] = {"b": b_star(h, d, t)[0], "moved": b_star(h, d, t)[1], "tick": t}
        print("neg", y, neg[y], flush=True)
    for y in POS_YEARS:
        d, h = dukascopy(a.raw, y), histdata(a.raw, y)
        t = max(tick_of(d), tick_of(h))
        b, n = b_star(h, d, t)
        pos[y] = {"b": b, "moved": n, "tick": t}
        own = tick_of(d)
        shifted = pd.Series((np.round(d.to_numpy() / own) + round(SYN_SHIFT / own)) * own, index=d.index)
        bs, ns = b_star(shifted, d, max(own, FOREXITE_TICK))
        syn[y] = {"b": bs, "moved": ns}
        print("pos", y, pos[y], "syn", syn[y], flush=True)
    neg_vals = [v["b"] for v in neg.values() if v["b"] is not None]
    bound = B_MAX if all(v < B_MAX for v in neg_vals) else max(neg_vals)
    valid = (all(v["b"] is not None and v["b"] >= POS_MIN for v in pos.values())
             and all(v["b"] is not None and v["b"] >= SYN_MIN for v in syn.values()))
    out["controls"] = {"negative_2010_18": neg, "positive_2019_25": pos, "synthetic_plus_37c": syn, "b_bound": bound,
                       "valid": valid}
    write()                                       # the controls are on disk before any candidate number exists
    # ---- 2. candidate
    zips = a.raw / "forexite_m1" / "_zips"
    cand: dict[int, dict[str, object]] = {}
    for y in POS_YEARS:
        d = dukascopy(a.raw, y)
        f, files = forexite(zips, y)
        t = max(FOREXITE_TICK, tick_of(d))
        cov, ok_days, sess = coverage(f, d)
        off, corr = clock_offset(f, d, y) if len(f) > 1000 else (None, None)
        av, ah = a_share(f, d, t)
        bv, bn = b_star(f, d, t)
        cand[y] = {"files": files, "minutes": len(f), "coverage": cov, "covered_days": ok_days, "session_days": sess,
                   "clock_offset_min": off, "clock_corr": corr, "a": av, "a_hours": ah, "b": bv, "b_moved": bn,
                   "tick": t, "observed_tick": tick_of(f) if len(f) else None,
                   "pass": bool(cov is not None and cov >= COVER_MIN and off is not None
                                and abs(off) <= R11["clock_offset_max_min"] and av is not None and av < A_MAX
                                and bv is not None and bv < bound)}
        print("forexite", y, cand[y], flush=True)
    measures = all(v["pass"] for v in cand.values())
    out["candidate"] = {"name": "forexite (ASCII MetaStock 1m files)", "years": cand,
                        "price_field_documented": PRICE_FIELD_DOCUMENTED, "measures_pass": measures}
    reasons = [r for r, bad in (("controls invalid", not valid), ("a/b*/coverage/clock fail", not measures),
                                ("price field not documented by the source", not PRICE_FIELD_DOCUMENTED)) if bad]
    out["admitted"] = not reasons
    out["not_admitted_because"] = reasons
    out["finished_utc"] = dt.datetime.now(dt.UTC).isoformat()
    write()
    print(json.dumps({k: out[k] for k in ("admitted", "not_admitted_because")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
