"""D-1: grade Dukascopy XAUUSD (the gold the system trains and trades on) under the frozen r11-v2.1 tier rule, against
independent references, and write the A16 source-precedence manifest. Outcome-blind: prices against prices only.

The tier rule is `goldbot.data.era_quality.grade_month_v2` at the frozen blob c6f283d7 (r11-v2.1); this script refuses
to grade if the module's bytes differ or the tree is dirty (quant ruling 5). Here the subject is Dukascopy 1m and the
references are, per year:
* 2009-2018: HistData XAUUSD (an independent feed: hour-ending closes shared with Dukascopy 0.00-0.04,
  evidence_r11-v2.csv), hourly from its 1m bid bars, native (its own high/low), its confirmed-spike minutes left out.
  Clock, per-month and US/EU gap-window offsets are checked exactly as scripts/histdata_m1.py does.
* 2019-2025-09: HistData is the SAME source (shares 0.91-0.97): the Dukascopy close check is circular, so the month also
  needs the independent fix (B2) and can reach A-consistent at best; without a fix it is P.
* 2003-2008: no HistData gold. Under r11-v2.1 a month with no independent hourly reference is C (no_reference): the
  daily fix is evidence only (fix_share is reported), it cannot carry a tier until a rule says so (quant).
The fix: raw/fixes/GLDNAV_XAU_PM.csv (scripts/gold_fix_gld.py: the LBMA Gold Price PM as implied by the SPDR Gold Trust
NAV, 15:00 London, 2004-11-18 on; low-side bias <= ~3.3 bp, measured here per year).

Writes (local, raw/dukascopy_grade/_reports/, never committed):
  tiers_dukascopy_r11-v2.1.csv     one row per session month, MonthGradeV2 + source + git sha
  clock_dukascopy_r11-v2.1.csv     clock check per year, month and gap-window offsets, same-source decision
  fix_evidence.csv                 per year: fix days, bp of the Dukascopy bid vs the fix (median, p02, p98, within
                                   10 bp, MAD) at 15:00 London (the nominal time r11-v2.1 checks) and at
                                   +2..+30 min (alignment control: the auction's price is set after 15:00)
  summary.json                     months per tier per year, reason counts, sha256 of every file
and raw/tiers/tiers_served_r11-v2.1.csv: the tiers of the SERVING source per instrument-month (HistData tiers for every
key but XAUUSD, these Dukascopy tiers for XAUUSD), the one file fs-v1 loads (Tiers.load refuses two tiers per month).
Commits nothing itself; `--manifest` (re)writes config/data/source_precedence.yaml (A16) from the reports.

    uv run python scripts/dukascopy_gold_grade.py grade [--root raw] [--workers 1]
    uv run python scripts/dukascopy_gold_grade.py manifest [--root raw]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.era_quality import (  # noqa: E402
    HOLDOUT_CUT,
    R11_V2,
    R11_V21,
    R11_V21_ID,
    ClockCheck,
    clock_check,
    day_of,
    epoch_ns_utc,
    gap_week_offsets,
    grade_month_v2,
    month_offsets,
    same_source_share,
)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO / "raw"
FROZEN_BLOB = "c6f283d763718bcf32f1c3971acd812a868fce63"     # goldbot/data/era_quality.py, r11-v2.1
KEY = "XAUUSD"
SESSION_MINUTES = 1380
FIRST_YEAR, LAST_YEAR = 2003, HOLDOUT_CUT.year
FIX_ID = "GLDNAV_XAU_PM"
FIX_SCAN_MIN = (0, 2, 4, 6, 10, 30)
TAG = R11_V21_ID.split("-2026")[0]                            # r11-v2.1
MANIFEST = REPO / "config" / "data" / "source_precedence.yaml"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _nice() -> None:
    try:
        os.nice(10)
    except OSError:
        pass


def git_state() -> tuple[str, bool, str]:
    run = lambda *a: subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True).stdout.strip()  # noqa: E731
    return (run("rev-parse", "HEAD"), bool(run("status", "--porcelain", "--untracked-files=no")),
            run("hash-object", "goldbot/data/era_quality.py"))


# ---------------------------------------------------------------------------------------------------------- inputs
def _session_year(ts: pd.Series) -> np.ndarray:
    return np.asarray(day_of(ts).year)


def subject_bars(raw: Path, year: int) -> pd.DataFrame:
    """Dukascopy 1m bid bars whose SESSION day falls in `year` (the previous file's 21:00-24:00 UTC tail of Dec 31
    opens this year's first session), error-flagged minutes dropped, holdout cut."""
    frames = []
    for y in (year - 1, year):
        p = raw / f"xauusd_1m_dukascopy_{y}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p, columns=["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close",
                                                      "dq_flag"]))
    if not frames:
        return pd.DataFrame(columns=["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close", "dq_flag"])
    b = pd.concat(frames, ignore_index=True)
    b["ts_utc"] = pd.to_datetime(b["ts_utc"], utc=True)
    b = b[~b["dq_flag"].fillna("").astype(str).str.startswith("error")]
    b = b[b["ts_utc"] < HOLDOUT_CUT].drop_duplicates("ts_utc").sort_values("ts_utc")
    b = b[_session_year(b["ts_utc"]) == year].reset_index(drop=True)
    b["dq_flag"] = b["dq_flag"].fillna("").astype(str)
    return b


def reference_hourly(raw: Path, year: int) -> tuple[pd.DataFrame, int]:
    """HistData XAUUSD hourly bid (ts_utc hour start, open/high/low/close, spread NaN: HistData has no ask) for the
    hours whose session day is in `year`; minutes HistData's own r11 check confirmed as spikes are left out (counted)."""
    frames = []
    for y in (year - 1, year):
        p = raw / "histdata_m1" / KEY / f"xauusd_1m_bid_{y}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p, columns=["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close",
                                                      "dq_flag"]))
    if not frames:
        return pd.DataFrame(), 0
    b = pd.concat(frames, ignore_index=True)
    b["ts_utc"] = pd.to_datetime(b["ts_utc"], utc=True)
    b = b[(b["ts_utc"] < HOLDOUT_CUT)].drop_duplicates("ts_utc").sort_values("ts_utc")
    flags = b["dq_flag"].fillna("").astype(str)
    spikes = flags.eq("warning:spike_confirmed") | flags.str.startswith("error")
    b = b[~spikes]
    h = pd.DatetimeIndex(b["ts_utc"]).floor("h")
    g = b.assign(h=h).groupby("h", sort=True)
    out = pd.DataFrame({"open": g["bid_open"].first(), "high": g["bid_high"].max(), "low": g["bid_low"].min(),
                        "close": g["bid_close"].last()}).rename_axis("ts_utc").reset_index()
    out["spread"] = np.nan
    out = out[_session_year(out["ts_utc"]) == year].reset_index(drop=True)
    return out, int(spikes.sum())


def load_fix(raw: Path) -> pd.DataFrame | None:
    p = raw / "fixes" / f"{FIX_ID}.csv"
    if not p.exists():
        return None
    f = pd.read_csv(p)
    f["fix_utc"] = pd.to_datetime(f["fix_utc"], utc=True)
    return f.loc[f["fix_utc"] < HOLDOUT_CUT, ["fix_utc", "value"]].reset_index(drop=True)


def same_source_years(raw: Path) -> tuple[dict[int, bool], dict[int, bool], dict[int, float]]:
    """Same-source and partial-share decisions per year from the frozen evidence (HistData XAUUSD vs Dukascopy: the
    share is a property of the pair, measured once under r11-v2)."""
    ev = pd.read_csv(raw / "histdata_m1" / "_reports" / "evidence_r11-v2.csv")
    ev = ev[ev["key"] == KEY]
    sh = dict(zip(ev["year"].astype(int), pd.to_numeric(ev["same_source_share"], errors="coerce"), strict=True))
    same = {y: bool(s >= R11_V2["same_source_share"]) for y, s in sh.items()}
    part = {y: bool(R11_V21["partial_share"] <= s < R11_V2["same_source_share"]) for y, s in sh.items()}
    return same, part, sh


def fix_bp(bars: pd.DataFrame, at_utc: pd.Series, value: np.ndarray) -> np.ndarray:
    """bp of the last Dukascopy bid close known at each instant (bar close <= instant, <= 5 min old) vs the value."""
    close_t = epoch_ns_utc(bars["ts_utc"]) + 60_000_000_000
    t = epoch_ns_utc(at_utc)
    j = np.searchsorted(close_t, t, side="right") - 1
    ok = j >= 0
    age = np.full(len(t), np.inf)
    age[ok] = (t[ok] - close_t[j[ok]]) / 60e9
    use = ok & (age <= R11_V2["fix_bar_max_age_min"])
    c = bars["bid_close"].to_numpy(dtype="float64")[j[use]]
    return np.log(c / value[use]) * 1e4


# ----------------------------------------------------------------------------------------------------------- grade
def grade_year(task: tuple[int, str, bool, bool]) -> tuple[dict[str, object], list[dict[str, object]],
                                                          dict[str, object]]:
    year, raw_s, same, partial = task
    raw = Path(raw_s)
    try:
        bars = subject_bars(raw, year)
        ref, ref_spikes = reference_hourly(raw, year)
        fix_all = load_fix(raw)
        fix = None if fix_all is None else fix_all[fix_all["fix_utc"].dt.year == year].reset_index(drop=True)
        fix_status = "ok" if fix is not None and len(fix) else "pending"
        ts, c = epoch_ns_utc(bars["ts_utc"]), bars["bid_close"].to_numpy(dtype="float64")
        if len(ref) and len(bars):
            cc = clock_check(KEY, year, ts, c, ref)
            offsets = month_offsets(ts, c, ref)
            gaps = gap_week_offsets(ts, c, ref, year)
            share_cents = same_source_share(ts, c, ref, 100.0)
        else:
            cc = ClockCheck(key=KEY, year=year, offset_min=None, corr_at_offset=None, corr_at_zero=None, hours=0,
                            agree_share=None, agree_tol=None, passed=False, reason="no_reference")
            offsets, gaps, share_cents = {}, {}, None
        gap_bad = {k: o for k, o in gaps.items() if o is not None and abs(o) > R11_V2["gap_week_offset_max"]}
        quarantined = (cc.offset_min is not None and not cc.passed) or bool(gap_bad)
        month = day_of(bars["ts_utc"]).to_period("M")
        ref_month = day_of(ref["ts_utc"]).to_period("M") if len(ref) else None
        grades = []
        for per, idx in bars.groupby(month).groups.items():
            if str(per) >= HOLDOUT_CUT.strftime("%Y-%m"):
                continue
            b = bars.loc[idx]
            r = ref.loc[np.asarray(ref_month == per)] if ref_month is not None else None
            r = r if r is not None and len(r) else None
            g = grade_month_v2(KEY, str(per), b, r, SESSION_MINUTES, True, quarantined=quarantined,
                               clock_offset=offsets.get(str(per)), same_source=same, fix=fix,
                               fix_status=fix_status, partial_share=partial)
            grades.append({**g.model_dump(), "source": "dukascopy",
                           "reference_source": "histdata:XAUUSD" if r is not None else "none",
                           "fix_source": FIX_ID if fix_status == "ok" else "none"})
        ev: dict[str, object] = {"year": year, "fix_days": 0 if fix is None else len(fix)}
        if fix is not None and len(fix):
            # the nominal fix time (what r11-v2.1 checks) and later minutes: the PM auction STARTS at 15:00 London
            # and its price is discovered minutes later, so the alignment control shows where the fix really sits
            for name, when in ((f"london_1500_plus{m:02d}", fix["fix_utc"] + pd.Timedelta(minutes=m))
                               for m in FIX_SCAN_MIN):
                bp = fix_bp(bars, when, fix["value"].to_numpy(dtype="float64"))
                ev.update({f"{name}_days": len(bp),
                           f"{name}_bp_median": float(np.median(bp)) if len(bp) else None,
                           f"{name}_bp_mad": float(np.median(np.abs(bp - np.median(bp)))) if len(bp) else None,
                           f"{name}_bp_p02": float(np.quantile(bp, 0.02)) if len(bp) else None,
                           f"{name}_bp_p98": float(np.quantile(bp, 0.98)) if len(bp) else None,
                           f"{name}_within_10bp": float((np.abs(bp) <= R11_V2["fix_bp"]).mean()) if len(bp) else None})
        clock = {**cc.model_dump(), "status": "ok", "quarantined": quarantined,
                 "bad_months": json.dumps({m: o for m, o in offsets.items() if abs(o) > 1}),
                 "gap_offsets": json.dumps(gaps), "same_source": same, "partial_share": partial,
                 "reverse_share_cents": share_cents, "fix_status": fix_status, "bars": len(bars),
                 "ref_hours": len(ref), "ref_spike_minutes_left_out": ref_spikes}
        return clock, grades, ev
    except Exception as exc:  # noqa: BLE001
        return {"key": KEY, "year": year, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:300]}, [], \
            {"year": year}


def cmd_grade(a: argparse.Namespace) -> int:
    sha, dirty, blob = git_state()
    if blob != FROZEN_BLOB:
        print(f"REFUSED: goldbot/data/era_quality.py is blob {blob}, the frozen r11-v2.1 rule is {FROZEN_BLOB}")
        return 2
    if dirty:
        print("REFUSED: the working tree has uncommitted changes; grades are stamped with a commit (quant ruling 5)")
        return 2
    raw = Path(a.root)
    same, part, _ = same_source_years(raw)
    tasks = [(y, str(raw), same.get(y, False), part.get(y, False) and not same.get(y, False))
             for y in range(FIRST_YEAR, LAST_YEAR + 1)]
    clocks, tiers, evs = [], [], []
    with ProcessPoolExecutor(max_workers=max(1, min(a.workers, 3)), initializer=_nice) as ex:
        for cc, grades, ev in ex.map(grade_year, tasks):
            clocks.append(cc)
            tiers.extend(grades)
            evs.append(ev)
            mix = pd.Series([g["tier"] for g in grades]).value_counts().to_dict()
            print(f"{cc['year']} same={cc.get('same_source')} clock={cc.get('offset_min')} {cc.get('reason')} "
                  f"fix_w10={ev.get('london_1500_plus00_within_10bp')} tiers={mix}", flush=True)
    rep = raw / "dukascopy_grade" / "_reports"
    rep.mkdir(parents=True, exist_ok=True)
    t = pd.DataFrame(tiers)
    t["reasons"] = t["reasons"].map(";".join)
    t = t.assign(git_sha=sha, dirty=False, rule_blob=blob)
    t.to_csv(rep / f"tiers_dukascopy_{TAG}.csv", index=False)
    pd.DataFrame(clocks).assign(git_sha=sha, r11_id=R11_V21_ID).to_csv(rep / f"clock_dukascopy_{TAG}.csv", index=False)
    pd.DataFrame(evs).assign(git_sha=sha).to_csv(rep / "fix_evidence.csv", index=False)
    served = write_served(raw, t)
    t["year"] = t["month"].str[:4]
    reasons: dict[str, int] = {}
    for r in t["reasons"]:
        for x in str(r).split(";"):
            if x:
                k = x.split(" ")[0]
                reasons[k] = reasons.get(k, 0) + 1
    # calibration control (outcome-blind): months graded A against the independent minute reference, and whether
    # the frozen fix check (>= 0.98 of >= 15 fix days within 10 bp) would pass them too
    ind_a = t[(t["tier"] == "A") & ~t["same_source"].astype(bool)]
    fix_pass = (ind_a["fix_share"] >= R11_V21["fix_day_share_min"]) & (ind_a["fix_days"] >= R11_V21["fix_days_min"])
    control = {"independent_A_months": len(ind_a), "of_which_pass_fix_check": int(fix_pass.sum()),
               "fix_share_median_on_A": float(ind_a["fix_share"].median()) if len(ind_a) else None}
    summary = {"r11_id": R11_V21_ID, "fix_check_control": control, "rule_blob": blob, "git_sha": sha, "months": len(t),
               "per_year": {y: g["tier"].value_counts().to_dict() for y, g in t.groupby("year")},
               "total": t["tier"].value_counts().to_dict(), "reasons": dict(sorted(reasons.items())),
               "files": {p.name: _sha(p) for p in sorted(rep.glob("*.csv"))} | {served.name: _sha(served)},
               "failed_years": [c["year"] for c in clocks if c.get("status") == "failed"],
               "written_utc": dt.datetime.now(dt.UTC).isoformat()}
    (rep / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({k: summary[k] for k in ("total", "reasons", "failed_years", "fix_check_control")}, default=str))
    print(pd.crosstab(t["year"], t["tier"]).to_string())
    return 0 if not summary["failed_years"] else 1


def write_served(raw: Path, duk: pd.DataFrame) -> Path:
    """HistData tiers for every key but XAUUSD + the Dukascopy XAUUSD tiers: the tier of the source that serves each
    instrument-month (A16; fs-v1 history_minutes serves XAUUSD from Dukascopy and every other key from HistData)."""
    hd = pd.read_csv(raw / "histdata_m1" / "_reports" / f"tiers_{TAG}.csv")
    ids = set(hd["r11_id"].dropna().astype(str)) | set(duk["r11_id"].dropna().astype(str))
    if ids != {R11_V21_ID}:
        raise ValueError(f"mixed r11 ids {ids}")
    hd = hd[hd["key"] != KEY].sort_values("bars").drop_duplicates(["key", "month"], keep="last").assign(
        source="histdata")
    cols = ["key", "month", "tier", "reasons", "source", "r11_id", "bars", "days", "same_source", "fix_status"]
    out = pd.concat([hd[cols], duk[cols]], ignore_index=True).sort_values(["key", "month"]).reset_index(drop=True)
    p = raw / "tiers" / f"tiers_served_{TAG}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(p, index=False)
    return p


# -------------------------------------------------------------------------------------------------- A16 manifest
def _runs(months: list[str], label: list[str]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for m, lab in zip(months, label, strict=True):
        if out and out[-1]["serves"] == lab and (pd.Period(str(out[-1]["to"]), "M") + 1) == pd.Period(m, "M"):
            out[-1]["to"] = m
            out[-1]["months"] = int(str(out[-1]["months"])) + 1
        else:
            out.append({"from": m, "to": m, "serves": lab, "months": 1})
    return out


def cmd_manifest(a: argparse.Namespace) -> int:
    raw = Path(a.root)
    served = raw / "tiers" / f"tiers_served_{TAG}.csv"
    duk = raw / "dukascopy_grade" / "_reports" / f"tiers_dukascopy_{TAG}.csv"
    hd = raw / "histdata_m1" / "_reports" / f"tiers_{TAG}.csv"
    s = pd.read_csv(served, dtype={"month": str})
    summary = json.loads((raw / "dukascopy_grade" / "_reports" / "summary.json").read_text())
    span = pd.period_range("2003-01", (HOLDOUT_CUT - pd.Timedelta(days=1)).strftime("%Y-%m"), freq="M").astype(str)
    usable = {"A": "intraday+daily", "A-consistent": "intraday+daily", "B": "daily", "C": "none", "F": "none",
              "P": "none"}
    inst: dict[str, object] = {}
    for key, g in s.groupby("key"):
        tier = dict(zip(g["month"], g["tier"], strict=True))
        src = "dukascopy" if key == KEY else "histdata"
        lab = [f"{src}:{usable.get(tier[m], 'none')}" if m in tier else "none:no_data" for m in span]
        inst[str(key)] = {"source": src, "tiers": {k: int(v) for k, v in g["tier"].value_counts().items()},
                          "runs": _runs(list(span), lab)}
    doc = {"id": "a16-source-precedence-v1", "r11_id": R11_V21_ID, "rule_blob": FROZEN_BLOB,
           "graded_at_git_sha": summary["git_sha"], "holdout_cut": HOLDOUT_CUT.isoformat(),
           "rule": ("one source per instrument-minute: XAUUSD from Dukascopy 1m (bid+ask), every other key from "
                    "HistData 1m bid (goldbot.research.feature_store.history_minutes); a month serves intraday rows "
                    "at A/A-consistent, daily (1d/1w) rows at B, nothing at C/F/P or without data"),
           "files": {"tiers_served": {"path": f"raw/tiers/{served.name}", "sha256": _sha(served)},
                     "tiers_dukascopy": {"path": f"raw/dukascopy_grade/_reports/{duk.name}", "sha256": _sha(duk)},
                     "tiers_histdata": {"path": f"raw/histdata_m1/_reports/{hd.name}", "sha256": _sha(hd)},
                     "fix": {"path": f"raw/fixes/{FIX_ID}.csv",
                             "sha256": _sha(raw / "fixes" / f"{FIX_ID}.csv")}},
           "not_served": {"histdata:XAUUSD": "same instrument as Dukascopy XAUUSD; Dukascopy serves (bid+ask, ticks)"},
           "instruments": inst}
    head = ("# A16 source-precedence manifest (D-1): which source serves each instrument-month and at what use, from the\n"
            "# r11-v2.1 served tiers. Generated by `scripts/dukascopy_gold_grade.py manifest`; never edit by hand.\n"
            "# Counts and sha256 only: no market data lives in this file (ADR 0048).\n")
    MANIFEST.write_text(head + yaml.safe_dump(doc, sort_keys=False, width=120, default_flow_style=None))
    print(f"wrote {MANIFEST.relative_to(REPO)}: {len(inst)} instruments")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["grade", "manifest"])
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--workers", type=int, default=1)
    a = ap.parse_args(argv)
    return cmd_grade(a) if a.cmd == "grade" else cmd_manifest(a)


if __name__ == "__main__":
    raise SystemExit(main())
