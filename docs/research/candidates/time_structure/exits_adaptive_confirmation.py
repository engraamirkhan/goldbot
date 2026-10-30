"""H-80 R2 confirmation read, 2018-01-01 .. 2025-09-30, run ONCE by the frozen rule in
docs/research/prereg-exits-adaptive-2026-10-26.md section 5. Bars on or after 2025-10-01 (holdout) are never read.

It reuses the discovery script's entry rule, exit walks (E0, E4, E8), Friday rule, cost model and day-clustered t
unchanged (`exits_adaptive_discovery.py`); only the window and the read differ. No parameter is changed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import multiprocessing
import sys
from concurrent.futures import ProcessPoolExecutor
from math import erfc, sqrt
from pathlib import Path

import pandas as pd

CUT = pd.Timestamp("2025-10-01", tz="UTC")
START = pd.Timestamp("2018-01-01", tz="UTC")
_spec = importlib.util.spec_from_file_location("exits_adaptive_discovery", Path(__file__).with_name("exits_adaptive_discovery.py"))
disc = importlib.util.module_from_spec(_spec)
sys.modules["exits_adaptive_discovery"] = disc
_spec.loader.exec_module(disc)


def load(raw: Path) -> pd.DataFrame:
    cols = ["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close",
            "tick_count"]
    parts = []
    for y in range(2018, 2026):
        f = raw / f"xauusd_1m_dukascopy_{y}.parquet"
        if y == 2025:       # the holdout starts 2025-10-01: filtered at read, never loaded
            parts.append(pd.read_parquet(f, columns=cols, filters=[("ts_utc", "<", CUT.to_pydatetime())]))
        else:
            parts.append(pd.read_parquet(f, columns=cols))
    d = pd.concat(parts, ignore_index=True)
    d["ts_utc"] = pd.to_datetime(d["ts_utc"], utc=True).astype("datetime64[ns, UTC]")
    d = d[(d["ts_utc"] < CUT) & (d["ts_utc"] >= START)].sort_values("ts_utc").drop_duplicates("ts_utc")
    for k in ("open", "high", "low", "close"):
        d[k] = (d[f"bid_{k}"] + d[f"ask_{k}"]) / 2
    assert d["ts_utc"].max() < CUT and d["ts_utc"].min() >= START
    return d[["ts_utc", "open", "high", "low", "close", "tick_count"]].set_index("ts_utc")


disc.load = load
disc.EXITS = ("E0", "E4", "E8")


def p_one_sided(t: float) -> float:
    return 0.5 * erfc(t / sqrt(2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(4, mp_context=multiprocessing.get_context("fork")) as ex:
        parts = list(ex.map(disc.run_tf, [(tf, str(a.raw)) for tf in disc.RULES]))
    df = pd.concat(parts, ignore_index=True)
    assert df["ts"].max() < CUT
    df.to_parquet(a.out / "trades.parquet")
    s = disc.summarise(df)
    s.to_csv(a.out / "summary.csv", index=False)
    print(s[["tf", "exit", "n", "gross", "t", "netA", "netM", "netMT", "yrs_gross_pos", "stopped"]]
          .to_string(float_format=lambda v: f"{v:.4f}"))
    read(df, a.out)


def read(df: pd.DataFrame, out_dir: Path) -> None:
    """The two paired comparisons on the same entries, pooled and per timeframe, and the rule's conditions."""
    d = df.pivot_table(index=["tf", "ts"], columns="exit", values="R").reset_index()
    d["day"] = pd.to_datetime(d["ts"]).dt.tz_convert("America/New_York").dt.date
    cost = disc.COST_M + disc.OVERSHOOT * df["stopped"]
    dm = df.assign(netM=df["R"] - cost / df["r_usd"], netA=df["R"] - disc.COST_A / df["r_usd"])
    pm = dm.pivot_table(index=["tf", "ts"], columns="exit", values="netM").reset_index()
    pa = dm.pivot_table(index=["tf", "ts"], columns="exit", values="netA").reset_index()
    rows = []
    for tf, g in list(d.groupby("tf")) + [("pooled", d)]:
        gm = pm if tf == "pooled" else pm[pm["tf"] == tf]
        ga = pa if tf == "pooled" else pa[pa["tf"] == tf]
        for ex in ("E4", "E8"):
            x = (g[ex] - g["E0"]).to_numpy()
            xm = (gm[ex] - gm["E0"]).to_numpy()
            xa = (ga[ex] - ga["E0"]).to_numpy()
            t = disc.clustered_t(x, g["day"].to_numpy())
            name = ex + "-E0"
            p = p_one_sided(t)
            rows.append(dict(tf=tf, cmp=name, n=len(x), gross_diff=x.mean(), t=t, p1=p, net_m=xm.mean(), net_a=xa.mean()))
    r = pd.DataFrame(rows)
    r.to_csv(out_dir / "paired.csv", index=False)
    print(r.to_string(float_format=lambda v: f"{v:.4f}"))
    pooled = r[r["tf"] == "pooled"].set_index("cmp")
    p4, p8 = pooled.loc["E4-E0", "p1"], pooled.loc["E8-E0", "p1"]
    k4 = 2 if min(p4, p8) == p4 else 1
    h4 = min(1.0, p4 * k4)
    h8 = min(1.0, p8 * (3 - k4))
    holm = dict(E4=h4, E8=h8)
    print("holm_adjusted_p", holm, "p_at_t2", p_one_sided(2.0))
    e4 = pooled.loc["E4-E0"]
    tfs = r[(r["tf"] != "pooled") & (r["cmp"] == "E4-E0")]
    big = tfs["n"] >= 150
    reads = tfs[big]
    need = 3 if len(reads) == 4 else 2
    res = dict(pooled_E4=e4.to_dict(), tf_reads=reads[["tf", "n", "gross_diff", "t"]].to_dict("records"),
               tf_no_read=tfs[~big]["tf"].tolist(), same_sign=int((reads["gross_diff"] > 0).sum()), needed=need,
               holm=holm, e4_net_ge_e0_net=bool(e4["net_m"] >= 0), pooled_E8=pooled.loc["E8-E0"].to_dict())
    (out_dir / "read.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
