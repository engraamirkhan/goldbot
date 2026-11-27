"""The sr-v3 cost-of-record table for training (ADR 0098 R-H, ADR 0102 cost(x), card 3.4; quant H1 on the trainer):
a round-trip cost in bp per instrument x broker x session x tf, and a swap rate per instrument and month.

    python scripts/research/sr_v3_cost_table.py --out ~/tmp/sr-v3-trainsets/costs

Round trip (`rt_bp`):
- XAUUSD: max(W2, the engine cost of record's charged ub95 converted to bp at its window's mean broker mid), per
  broker x session x tf. Brokers pool their accounts by the larger charge (icm = icm-demo, icm-probe). A tf cell below
  min_fills (or never filled: 30m, 2h, 1d, 1w) takes the broker x session `all` cell when that has min_fills, else W2
  (`basis` says which). This is stricter than ADR 0102's literal "a cell below min_fills takes W2": W2 (0.575 bp)
  is below every measured session cell, so the literal rule would charge a sparse 1h cell less than a measured one
  (flagged for the quant; the direction is the conservative one).
- every other instrument: there is no measured cost of record yet (ECR is gold fills only). The prior is the
  brokers' typical spread (config/instruments.yaml `max_spread_points` / 3, the FALLBACK documented there) plus the
  configured round-trip commission (settings `commission_per_lot_side_usd` x 2 per 100k base units) in bp of the
  2010-17 mean price, floored at W2; `basis` = "prior". Replace with a measured ECR when the brokers' FX fills reach
  min_fills.

Swap (`swap`): a per-night financing rate as a fraction of notional, broker sign (negative = paid), per month, the
value of record as-of the month's first instant on `available_utc` (point in time):
- XAUUSD: the shipped research swap of record (`SwapSpec.research_shipped`, a rate at its reference price),
  constant; XAGUSD takes the same metal rate (no silver measurement; flagged);
- FX: the 3-month rate differential (FRED IR3TIB01 OECD interbank, DTB3 for USD; raw/daily, PIT `available_utc`)
  less a broker markup `MARKUP_PA` per side, / 360 per night: long = (r_base - r_quote - m) / 360, short =
  (r_quote - r_base - m) / 360. The markup is a prior to calibrate against the brokers' symbol swaps.

Outputs (local, never committed): cost_table.parquet, swap_table.parquet and cost_table.json (sources, the content
sha256 = the cost version a trainer pins, quality checks).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from goldbot.research import decision_cost as DC

ROOT = Path(__file__).resolve().parents[2]
TFS: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w")
SESSIONS: tuple[str, ...] = ("asia", "london", "newyork")
GOLD = "XAUUSD"
FX10: tuple[str, ...] = ("AUDJPY", "AUDUSD", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF",
                         "USDJPY")
METALS: tuple[str, ...] = ("XAGUSD",)
RATE_SERIES: dict[str, str] = {"USD": "DTB3", "AUD": "IR3TIB01AUM156N", "CAD": "IR3TIB01CAM156N",
                               "CHF": "IR3TIB01CHM156N", "EUR": "IR3TIB01EZM156N", "GBP": "IR3TIB01GBM156N",
                               "JPY": "IR3TIB01JPM156N", "NZD": "IR3TIB01NZM156N"}
MARKUP_PA = 0.01                      # broker swap markup per side, a prior (retail markups run 0.5-1.5 % a year)
DAYS_PA = 360.0
LEARN = (2010, 2017)
MONTHS = pd.period_range("2003-01", "2025-09", freq="M")       # holdout 2025-10..2026-09 sealed: never in the table


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------------------------------------- gold: ECR
def ecr_cells(path: Path) -> tuple[dict[str, Any], pd.DataFrame]:
    d = yaml.safe_load(path.read_text())
    rows = []
    for acc, ss in d["cells"].items():
        broker = d["brokers"][acc]
        for s, tfs in ss.items():
            for tf, c in tfs.items():
                rows.append({"broker": broker, "account": acc, "session": s, "tf": tf, "n": int(c["n"]),
                             "n_days": int(c["n_days"]), "charged_usd": float(c["charged_usd"]),
                             "sparse": bool(c.get("sparse", False))})
    return d, pd.DataFrame(rows)


def broker_mean_mid(raw: Path, broker_account: str, window: tuple[pd.Timestamp, pd.Timestamp]) -> float:
    fr = [pd.read_parquet(p) for p in sorted((raw / "broker_m1" / broker_account).glob("*.parquet"))]
    m = pd.concat(fr, ignore_index=True)
    ts = pd.to_datetime(m["ts_utc"], utc=True)
    keep = (ts >= window[0]) & (ts < window[1])
    if "symbol" in m:
        keep &= m["symbol"].astype(str).str.upper().str.startswith(GOLD)
    m = m[keep]
    if "bid_close" in m and "ask_close" in m:
        mid = (m["bid_close"].astype(float) + m["ask_close"].astype(float)) / 2
    else:
        mid = m["close"].astype(float)
    if not len(mid):
        raise ValueError(f"no {broker_account} minutes in the ECR window {window}")
    return float(mid.mean())


def gold_rows(ecr_path: Path, raw: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    d, cells = ecr_cells(ecr_path)
    window = (pd.Timestamp(d["window_utc"][0]), pd.Timestamp(d["window_utc"][1]))
    min_fills = int(d["min_fills"])
    accounts = {b: sorted(a for a, bb in d["brokers"].items() if bb == b) for b in sorted(set(d["brokers"].values()))}
    price = {b: broker_mean_mid(raw, next(a for a in accts if (raw / "broker_m1" / a).exists()), window)
             for b, accts in accounts.items()}
    out = []
    for b in sorted(accounts):
        for s in SESSIONS:
            for tf in TFS:
                g = cells[(cells["broker"] == b) & (cells["session"] == s) & (cells["tf"] == tf)]
                basis = "ecr"
                if not len(g) or int(g["n"].max()) < min_fills:
                    g2 = cells[(cells["broker"] == b) & (cells["session"] == s) & (cells["tf"] == "all")]
                    if len(g2) and int(g2["n"].max()) >= min_fills:
                        basis = "ecr-session-all" + (":tf-below-min-fills" if len(g) else ":no-tf-cell")
                        g = g2
                    else:
                        basis = "w2:below-min-fills"
                ok = g[g["n"] >= min_fills] if basis.startswith("ecr") else g.iloc[0:0]
                usd = float(ok["charged_usd"].max()) if len(ok) else np.nan
                bp = usd / price[b] * 1e4 if np.isfinite(usd) else np.nan
                rt = max(DC.RT_BP, bp) if np.isfinite(bp) else DC.RT_BP
                out.append({"instrument": GOLD, "broker": b, "session": s, "tf": tf, "w2_bp": DC.RT_BP,
                            "measured_usd": usd, "ecr_ub95_usd": usd, "measured_n": int(g["n"].max()) if len(g) else 0,
                            "ref_price": price[b], "measured_bp": bp, "rt_bp": rt,
                            "basis": basis if np.isfinite(bp) and bp > DC.RT_BP else
                            (basis if not np.isfinite(bp) else f"{basis}:w2-floor"),
                            "source": f"{ecr_path.name} ({d['version']}, charge_rule {d['charge_rule']})"})
    meta = {"ecr_version": d["version"], "ecr_file": str(ecr_path), "ecr_sha256": sha256_bytes(ecr_path.read_bytes()),
            "window_utc": [str(window[0]), str(window[1])], "min_fills": min_fills, "ref_price_usd": price,
            "accounts": accounts}
    return pd.DataFrame(out), meta


# ---------------------------------------------------------------------------------------------- other instruments
def learn_mean_price(raw: Path, inst: str) -> float:
    ps = [p for y in range(LEARN[0], LEARN[1] + 1)
          for p in (raw / "histdata_m1" / inst).glob(f"*_1m_bid_{y}.parquet")]
    if not ps:
        raise FileNotFoundError(f"no HistData minutes for {inst}")
    return float(np.mean([pd.read_parquet(p, columns=["bid_close"])["bid_close"].astype(float).mean() for p in ps]))


def prior_rows(raw: Path, instruments_yaml: Path, commission: dict[str, float]) -> tuple[pd.DataFrame, dict[str, Any]]:
    spec = yaml.safe_load(instruments_yaml.read_text())
    table = spec.get("instruments", spec)
    px = {i: learn_mean_price(raw, i) for i in (*FX10, *METALS)}
    usd_per_base = {"USD": 1.0, "EUR": px["EURUSD"], "GBP": px["GBPUSD"], "AUD": px["AUDUSD"], "NZD": px["NZDUSD"]}
    out = []
    for inst in (*FX10, *METALS):
        t = table[inst]
        spread_px = float(t["max_spread_points"]) / 3.0 * float(t["point"])
        spread_bp = spread_px / px[inst] * 1e4
        units = float(t["contract_size"])
        base = inst[:3]
        notional_usd = units * (px[inst] if inst in METALS else usd_per_base[base])
        for b, side_usd in sorted(commission.items()):
            comm_bp = 2 * side_usd / notional_usd * 1e4
            raw_bp = spread_bp + comm_bp
            for s in SESSIONS:
                for tf in TFS:
                    out.append({"instrument": inst, "broker": b, "session": s, "tf": tf, "w2_bp": DC.RT_BP,
                                "measured_usd": np.nan, "measured_n": 0, "ref_price": px[inst],
                                "measured_bp": np.nan, "prior_bp": raw_bp, "rt_bp": max(DC.RT_BP, raw_bp),
                                "basis": "prior" if raw_bp > DC.RT_BP else "prior:w2-floor",
                                "source": "instruments.yaml max_spread_points/3 (FALLBACK) + settings commission x2"})
    meta = {"instruments_sha256": sha256_bytes(instruments_yaml.read_bytes()), "commission_per_lot_side_usd":
            commission, "learn_mean_price": px}
    return pd.DataFrame(out), meta


# ---------------------------------------------------------------------------------------------- gold spread (N2)
def spread_table(raw: Path, years: range = range(2003, 2026)) -> pd.DataFrame:
    """Dukascopy XAUUSD measured bid/ask spread (USD/oz, the minute's mean tick spread) per session x month,
    2003-01..2025-09 (the holdout and later never read): minutes, days, mean, median, p90 and ub95 = mean + 1.96 x the
    standard error of the day means (clustered by session day). `available_utc` = the first instant after the month:
    a month's spread is known only once the month is over (row_rt_bp joins as-of on it)."""
    from goldbot.data.calendar import DEFAULT_SESSIONS
    out = []
    for y in years:
        p = raw / f"xauusd_1m_dukascopy_{y}.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p, columns=["ts_utc", "spread_mean", "dq_flag"])
        ts = pd.DatetimeIndex(pd.to_datetime(d["ts_utc"], utc=True))
        spr = d["spread_mean"].astype(float).to_numpy()
        keep = np.asarray(ts < pd.Timestamp("2025-10-01", tz="UTC")) \
            & ~d["dq_flag"].astype(str).str.startswith("error").to_numpy() & np.isfinite(spr) & (spr > 0)
        d, ts = d[keep], ts[keep]
        f = pd.DataFrame({"session": np.asarray(DEFAULT_SESSIONS.session_label(ts)).astype(str),
                          "month": ts.strftime("%Y-%m"), "day": (ts + pd.Timedelta(hours=2)).strftime("%Y-%m-%d"),
                          "spread": d["spread_mean"].astype(float).to_numpy()})
        f = f[f["session"].isin(SESSIONS)]
        for (sess, mo), g in f.groupby(["session", "month"], sort=True):
            dm = g.groupby("day")["spread"].mean()
            se = float(dm.std(ddof=1) / np.sqrt(len(dm))) if len(dm) > 1 else float("nan")
            mean = float(g["spread"].mean())
            out.append({"instrument": GOLD, "session": sess, "month": mo, "minutes": len(g), "days": len(dm),
                        "mean_usd": mean, "median_usd": float(g["spread"].median()),
                        "p90_usd": float(g["spread"].quantile(0.9)),
                        "ub95_usd": mean + 1.96 * se if np.isfinite(se) else mean,
                        "available_utc": (pd.Period(str(mo), "M") + 1).start_time.tz_localize("UTC")})
    return pd.DataFrame(out)


def commission_rt_usd_per_oz(commission: dict[str, float], contract_oz: float = 100.0) -> dict[str, float]:
    return {b: 2.0 * v / contract_oz for b, v in commission.items()}


def row_rt_bp(rows: pd.DataFrame, cost: pd.DataFrame, spread: pd.DataFrame, commission_rt: dict[str, float]
              ) -> np.ndarray:
    """Per gold row (broker, session, tf, decision_utc, entry): rt = max(the cell's rt_bp, ECR ub95 USD / entry x 1e4,
    (Dukascopy spread ub95 of the session's latest COMPLETED month + the broker's round-trip commission) / entry x
    1e4) (quant N2). The spread is joined as-of on its `available_utc` (no part of the decision month is used)."""
    c = rows[["broker", "session", "tf"]].merge(
        cost[cost["instrument"] == GOLD][["broker", "session", "tf", "rt_bp", "ecr_ub95_usd"]],
        on=["broker", "session", "tf"], how="left")
    entry = rows["entry"].to_numpy(float)
    ecr = c["ecr_ub95_usd"].to_numpy(float) / entry * 1e4
    sp = np.full(len(rows), np.nan)
    dec = pd.to_datetime(rows["decision_utc"], utc=True).to_numpy()
    for sess, g in spread.sort_values("available_utc").groupby("session"):
        m = (rows["session"] == sess).to_numpy()
        if not m.any():
            continue
        av = pd.to_datetime(g["available_utc"], utc=True).to_numpy()
        i = np.searchsorted(av, dec[m], side="right") - 1
        v = g["ub95_usd"].to_numpy(float)
        sp[m] = np.where(i >= 0, v[np.clip(i, 0, None)], np.nan)
    comm = rows["broker"].map(commission_rt).to_numpy(float)
    spread_bp = (sp + comm) / entry * 1e4
    return np.asarray(np.nanmax(np.vstack([c["rt_bp"].to_numpy(float), ecr, spread_bp]), axis=0))


# ---------------------------------------------------------------------------------------------- swap
def rate_asof(raw: Path, ccy: str, at: pd.DatetimeIndex) -> np.ndarray:
    s = pd.read_parquet(raw / "daily" / f"fred_{RATE_SERIES[ccy]}.parquet")
    s = s.assign(av=pd.to_datetime(s["available_utc"], utc=True)).sort_values("av")
    s = s[np.isfinite(s["value"].astype(float))]
    idx = np.searchsorted(s["av"].to_numpy(), at.to_numpy(), side="right") - 1   # value available at or before `at`
    v = s["value"].astype(float).to_numpy() / 100.0
    return np.where(idx >= 0, v[np.clip(idx, 0, None)], np.nan)


def swap_table(raw: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    at = pd.DatetimeIndex([p.start_time.tz_localize("UTC") for p in MONTHS])
    rates = {c: rate_asof(raw, c, at) for c in RATE_SERIES}
    usd = rates["USD"]
    out = []
    for inst in (GOLD, *METALS):        # metal lease rate taken as 0: long pays r_USD + m, short pays m - r_USD (>= 0)
        out.append(pd.DataFrame({"instrument": inst, "month": MONTHS.astype(str), "asof_utc": at,
                                 "long_per_night": np.minimum(0.0, (0.0 - usd - MARKUP_PA) / DAYS_PA),
                                 "short_per_night": np.minimum(0.0, (usd - 0.0 - MARKUP_PA) / DAYS_PA),
                                 "r_base": 0.0, "r_quote": usd, "markup_pa": MARKUP_PA,
                                 "basis": "USD 3m rate (DTB3) as-of available_utc + markup, both sides, never a credit"
                                          " (quant N3)" + ("" if inst == GOLD else "; XAG takes the same metal rule")}))
    for inst in FX10:
        rb, rq = rates[inst[:3]], rates[inst[3:]]
        out.append(pd.DataFrame({"instrument": inst, "month": MONTHS.astype(str), "asof_utc": at,
                                 "long_per_night": (rb - rq - MARKUP_PA) / DAYS_PA,
                                 "short_per_night": (rq - rb - MARKUP_PA) / DAYS_PA, "r_base": rb, "r_quote": rq,
                                 "markup_pa": MARKUP_PA,
                                 "basis": f"3m rate differential {RATE_SERIES[inst[:3]]}-{RATE_SERIES[inst[3:]]} "
                                          "as-of available_utc, less markup"}))
    meta = {"rate_series": RATE_SERIES, "rate_files_sha256": {c: sha256_bytes(
        (raw / "daily" / f"fred_{s}.parquet").read_bytes()) for c, s in RATE_SERIES.items()},
            "markup_pa": MARKUP_PA, "days_pa": DAYS_PA,
            "metals": "long = min(0, -(r_USD + m)/360), short = min(0, (r_USD - m)/360); lease rate 0 (quant N3)",
            "note": "FRED values are the latest vintage (interbank rates are rarely revised); as-of on available_utc"}
    return pd.concat(out, ignore_index=True), meta


# ---------------------------------------------------------------------------------------------- checks
def checks(cost: pd.DataFrame, swap: pd.DataFrame, spread: pd.DataFrame | None = None) -> dict[str, Any]:
    want = {(i, s, tf) for i in (GOLD, *FX10, *METALS) for s in SESSIONS for tf in TFS}
    have = set(zip(cost["instrument"], cost["session"], cost["tf"], strict=True))
    learn = swap[swap["month"].between(f"{LEARN[0]}-01", f"{LEARN[1]}-12")]
    q = {"cells_complete": not (want - have), "missing_cells": sorted(map(str, want - have))[:20],
         "rt_bp_finite_positive": bool(np.isfinite(cost["rt_bp"]).all() and (cost["rt_bp"] > 0).all()),
         "rt_bp_at_least_w2": bool((cost["rt_bp"] >= DC.RT_BP - 1e-12).all()),
         "rt_bp_range": [float(cost["rt_bp"].min()), float(cost["rt_bp"].max())],
         "rt_bp_sane": bool((cost["rt_bp"] < 50).all()),       # > 50 bp round trip is a unit error, not a cost
         "swap_learn_finite": bool(np.isfinite(learn[["long_per_night", "short_per_night"]]).all().all()),
         "swap_annual_sane": bool((learn[["long_per_night", "short_per_night"]].abs() * DAYS_PA < 0.2).all().all()),
         "swap_no_holdout": bool(swap["month"].max() < "2025-10"),
         "swap_markup_pays": bool(((learn["long_per_night"] + learn["short_per_night"]) <= 1e-12).all()),
         "metal_swap_never_credit": bool((swap[swap["instrument"].isin([GOLD, *METALS])][
             ["long_per_night", "short_per_night"]] <= 0).all().all())}
    if spread is not None:
        want_m = {(se, str(m)) for se in SESSIONS for m in MONTHS}
        have_m = set(zip(spread["session"], spread["month"], strict=True))
        q["spread_months_missing"] = len(want_m - have_m)
        q["spread_learn_complete"] = not {(se, m) for se, m in want_m - have_m
                                          if f"{LEARN[0]}-01" <= m <= f"{LEARN[1]}-12"}
        q["spread_no_holdout"] = bool(spread["month"].max() < "2025-10")
        q["spread_positive_sane"] = bool(((spread["ub95_usd"] > 0) & (spread["ub95_usd"] < 5)).all())
    q["pass"] = all(v for k, v in q.items() if isinstance(v, bool))
    return q


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=ROOT / "raw")
    ap.add_argument("--ecr", type=Path, default=ROOT / "config" / "engine_costs" / "ecr-2026-11-20.2.yaml")
    ap.add_argument("--instruments", type=Path, default=ROOT / "config" / "instruments.yaml")
    ap.add_argument("--out", type=Path, default=Path.home() / "tmp" / "sr-v3-trainsets" / "costs")
    a = ap.parse_args()
    from goldbot.config import load_settings
    commission = dict(load_settings(ROOT / "config" / "settings.yaml").costs.commission_per_lot_side_usd)
    g, gmeta = gold_rows(a.ecr, a.raw)
    o, ometa = prior_rows(a.raw, a.instruments, commission)
    cost = pd.concat([g, o], ignore_index=True)
    swap, smeta = swap_table(a.raw)
    spread = spread_table(a.raw)
    comm_rt = commission_rt_usd_per_oz(commission)
    a.out.mkdir(parents=True, exist_ok=True)
    cp, sp, dp = a.out / "cost_table.parquet", a.out / "swap_table.parquet", a.out / "gold_spread_table.parquet"
    cost.to_parquet(cp, index=False)
    swap.to_parquet(sp, index=False)
    spread.to_parquet(dp, index=False)
    h = hashlib.sha256()
    for f in (cost, swap, spread):
        h.update(pd.util.hash_pandas_object(f, index=False).to_numpy().tobytes())
    h.update(json.dumps(comm_rt, sort_keys=True).encode())
    q = checks(cost, swap, spread)
    meta = {"cost_version": "srv3-cost-" + h.hexdigest()[:16], "content_sha256": h.hexdigest(),
            "files": {"cost_table": str(cp), "cost_table_sha256": sha256_bytes(cp.read_bytes()),
                      "swap_table": str(sp), "swap_table_sha256": sha256_bytes(sp.read_bytes()),
                      "gold_spread_table": str(dp), "gold_spread_table_sha256": sha256_bytes(dp.read_bytes())},
            "rule": "cell rt_bp = max(W2, ECR ub95 / window mean broker mid x 1e4) per broker x session x tf; a sparse "
                    "tf takes the session `all` cell, else W2; non-gold: prior (spread+commission), W2 floor, "
                    "cost_version label 'prior'. GOLD PER ROW (quant N2, row_rt_bp): max(cell rt_bp, ecr_ub95_usd / "
                    "entry x 1e4, (gold spread ub95_usd of the session's latest completed month, as-of available_utc "
                    "+ commission_rt_usd_per_oz[broker]) / entry x 1e4)",
            "commission_rt_usd_per_oz": comm_rt,
            "fx_cost_label": "prior (quant: FX reads are labelled cost_version=prior until measured)",
            "w2_bp": DC.RT_BP, "overshoot_bp": DC.OVERSHOOT_BP, "gold": gmeta, "other": ometa, "swap": smeta,
            "session_rule": "goldbot.research.realised_costs.session_of (DEFAULT_SESSIONS.session_label, UTC)",
            "quality": q, "built_utc": str(pd.Timestamp.now(tz="UTC"))}
    (a.out / "cost_table.json").write_text(json.dumps(meta, indent=1, default=str))
    print(json.dumps({"cost_version": meta["cost_version"], "quality": q}, indent=1, default=str))
    print(cost.pivot_table(index=["instrument", "broker"], columns="session", values="rt_bp", aggfunc="max")
          .round(3).to_string())
    return 0 if q["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
