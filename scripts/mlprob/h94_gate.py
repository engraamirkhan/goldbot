"""H-94 range-to-cost gate on the 11 pooled trend rules (docs/research/prereg-2026-11-13-h94-range-cost-gate.md,
frozen in commit 4a985b57 before any outcome under the gate was computed).

    uv run python scripts/mlprob/h94_gate.py --selftest     parity with the trend lane + shuffled-gate gain; never
                                                            computes the real gate's split of outcomes
    uv run python scripts/mlprob/h94_gate.py --read         THE one explore read (2010-17); refuses a second

The pool is the trend lane's engine as `mlnet_pool.py` runs it (`mlnet_engine.STRATS` / `simulate`, code unchanged):
the 11 rules, their signals, stops, exits, one position per rule and the 4 leak-free twins per trade. This runner only
filters, truncates and re-prices that pool. Only the explore window exists here: TEST-OLD 2008-09 is a separate,
reviewed look (card section 6) and is refused.

Implementation choices fixed in this commit, before the read (card sections 2-4 leave them open):
- Gate and filters act on the pool's trade list. A trade removed by the gate, the tier-1 rule or the cost floor does
  not free its rule for a signal the one-position rule had blocked (gated = a subset of ungated; U compares the two).
- Tier-1 = `us_calendar` tier 1 (CPI, NFP, FOMC statement and press conference, PCE = TIER1_PATTERNS), known before
  the release (available_utc <= event_utc). No fill in [T-60, T+30] min; a position open at T-5 min closes at the open
  of the first minute at or after T-5 ("news" exit: full round trip, no overshoot). Twins follow the same rule.
- Cost floor: skip a trade whose 1 R stop is < 3 x (round trip + the overshoot charge), both of the fill's session,
  bps-scaled at the fill price; twins inherit their trade's skip.
- Session: `gold_sessions` (city clocks, BACKLOG 146); the thin hours (New York close to the Tokyo open) count as asia
  (card section 3). Gate session = the decision's; charge session = the fill's.
- Costs (card section 3): round trip = the worse broker's cell (`gold_session_rt`, cor-2026-10-31.2), overshoot =
  ICM's charge (max($0.12, ci90 upper); Vantage's overshoot is untrusted) on stop and trail exits, both x price/4,000
  (W2). Financing W3.3: nights by the engine's `rollover_nights` (Wednesday triple), notional = fill price, long pays
  DTB3 + 0.74 %/yr, short receives DTB3 - 0.74 %/yr (pays if negative); DTB3 asof the fill on available_utc. Stress:
  overshoot $0.70 x price/4,000.
- HAR: range(d) = 1e4 x ln(high/low) of the UTC day's 1m mid bars, days with >= 600 minutes only (Sunday stubs and
  holiday half-days are neither targets nor lags). OLS of ln range(d) on 1, ln range(d-1), mean ln range(d-5..d-1),
  mean ln range(d-22..d-1); F(d) = exp(fit). Lags never cross the 2006-2008/09 gap. Prediction year Y uses rows
  2003-01..2005-12 plus 2010-01-01..(Y-1)-12-31; 2009 forecasts (warm-up for the trailing median only) use the 2010
  coefficients. F(d) is available at the previous day's last minute + 1 min and joined to each decision by
  `asof_join` on available_utc; M(d) = median of F over the 250 previous forecast days.
- Day = the forecast day joined to the decision (clusters, placebo days, bootstrap). Year = the decision's UTC year.
- Placebo gate: per year x session x side cell, days in random order, each opened if it brings the opened trade count
  closer to the real gate's count in that cell (stops at it); 1,000 draws, seed 20261113.
- Uplift CI: 90 % percentile, day-cluster bootstrap, 2,000 draws (reported, not gating).

Market data stays on the Mac: no trade row leaves the process except the local cache under ~/tmp/h94.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mlnet_engine as E  # noqa: E402
import mlnet_pool as P  # noqa: E402

from goldbot.data.store import asof_join  # noqa: E402
from goldbot.labels import SwapSpec, rollover_nights  # noqa: E402
from goldbot.research import arms as A  # noqa: E402

COR_PIN = "cor-2026-10-31.2"
REF_PX = 4000.0
STRESS_OS = 0.70
FLOOR_MULT = 3.0
PRE_MIN, POST_MIN, LEAD_MIN = 60, 30, 5
MIN_DAY_BARS = 600
MEDIAN_N = 250
FIT_OLD = (pd.Timestamp("2003-01-01"), pd.Timestamp("2006-01-01"))
EXPLORE_YEARS = range(2010, 2018)
WARM_FROM = pd.Timestamp("2008-10-01", tz="UTC")       # lags for the 2009 forecasts behind the first trailing median
N_PLACEBO, N_SHUFFLE, N_BOOT = 1000, 200, 2000
SEED = 20261113
SESS3 = ("asia", "london", "newyork")
OUT = Path.home() / "tmp" / "h94"
TREND_EXPLORE = Path.home() / "tmp" / "sprint200_trend" / "explore.json"
DOCS = E.ROOT / "docs" / "research" / "mlprob"


# ---------------------------------------------------------------- costs and sessions
def session_rt() -> dict[str, float]:
    """Worse-broker round trip per session, USD/oz (card section 3: asia 0.204, london 0.293, newyork 0.263)."""
    from goldbot.research.catalogue.costs import gold_session_rt, load_cost_of_record
    rt = gold_session_rt(load_cost_of_record(), COR_PIN)
    return {s: float(rt[s]) for s in SESS3}


def session_overshoot() -> dict[str, float]:
    costs = A.load_costs(E.ROOT / "config" / "arm_costs.yaml")
    costs.require_version(COR_PIN)
    return {s: float(costs.overshoot_charge_usd("icm", s)) for s in SESS3}


def sessions(t_ns: np.ndarray) -> np.ndarray:
    """Gold session of each instant; the thin hours count as asia (card section 3)."""
    from goldbot.research.catalogue.costs import GOLD_THIN, gold_sessions
    lab = gold_sessions(np.asarray(t_ns, np.int64))
    return np.where(lab == GOLD_THIN, "asia", lab).astype(object)


def gate_mult(rt: dict[str, float]) -> dict[str, float]:
    """RT(session) / RT_mean: London ~1.16, New York ~1.04, Asia ~0.81 x the trailing median."""
    m = float(np.mean([rt[s] for s in SESS3]))
    return {s: rt[s] / m for s in SESS3}


# ---------------------------------------------------------------- HAR forecast
def har_design(lr: np.ndarray) -> np.ndarray:
    """[1, lr(d-1), mean lr(d-5..d-1), mean lr(d-22..d-1)] per row of one contiguous block (NaN without 22 lags)."""
    n = len(lr)
    x = np.full((n, 4), np.nan)
    cs = np.r_[0.0, np.cumsum(lr)]
    for i in range(22, n):
        x[i] = (1.0, lr[i - 1], (cs[i] - cs[i - 5]) / 5, (cs[i] - cs[i - 22]) / 22)
    return x


def daily_ranges(raw: Path) -> pd.DataFrame:
    """UTC-day mid ranges in bps (>= MIN_DAY_BARS minutes): 2003-05 (fit block) and 2008-10..2017 (lags, warm-up,
    explore). 2006-09 days are never fit rows; 2008-10..2009 serve as lags and as the first trailing median only."""
    parts = []
    for y in [2003, 2004, 2005, *range(2008, 2018)]:
        p = pd.read_parquet(raw / f"xauusd_1m_dukascopy_{y}.parquet",
                            columns=["ts_utc", "bid_high", "ask_high", "bid_low", "ask_low"])
        ts = pd.DatetimeIndex(p["ts_utc"]).as_unit("ns")
        keep = np.asarray(ts < pd.Timestamp("2018-01-01", tz="UTC"))
        if y == 2008:
            keep &= np.asarray(ts >= WARM_FROM)
        p = p.loc[keep]
        ts = pd.DatetimeIndex(p["ts_utc"]).as_unit("ns")
        hi = (p["bid_high"].to_numpy(float) + p["ask_high"].to_numpy(float)) / 2
        lo = (p["bid_low"].to_numpy(float) + p["ask_low"].to_numpy(float)) / 2
        f = pd.DataFrame({"day": ts.tz_convert(None).normalize(), "t": E._ns(ts), "h": hi, "l": lo})
        parts.append(f.groupby("day").agg(n=("t", "size"), last_t=("t", "max"), h=("h", "max"), l=("l", "min")))
    d = pd.concat(parts).sort_index()
    d = d[~d.index.duplicated()]
    d = d[d.n >= MIN_DAY_BARS].copy()
    d["rng_bps"] = 1e4 * np.log(d.h / d.l)
    d["block"] = np.where(d.index < pd.Timestamp("2006-01-01"), 0, 1)
    assert d.index.max() < pd.Timestamp("2018-01-01") and not ((d.index >= "2006-01-01") & (d.index < "2008-10-01")).any()
    return d


def har_forecasts(d: pd.DataFrame) -> tuple[pd.DataFrame, dict[int, list[float]]]:
    """Forecast rows (fday, available_utc, F, M) for every block-1 day with 22 lags, and the coefficients by year."""
    rows = []
    for _, g in d.groupby("block", sort=True):
        lr = np.log(g.rng_bps.to_numpy())
        x = har_design(lr)
        rows.append(pd.DataFrame({"fday": g.index, "block": g.block.to_numpy(), "y": lr, "x0": x[:, 0], "x1": x[:, 1],
                                  "x5": x[:, 2], "x22": x[:, 3], "prev_last": np.r_[np.nan, g.last_t.to_numpy()[:-1]]}))
    h = pd.concat(rows, ignore_index=True)
    h = h[np.isfinite(h.x22)].reset_index(drop=True)
    xc = ["x0", "x1", "x5", "x22"]
    old = h[(h.block == 0) & (h.fday >= FIT_OLD[0]) & (h.fday < FIT_OLD[1])]
    coefs: dict[int, list[float]] = {}
    for yy in EXPLORE_YEARS:
        new = h[(h.block == 1) & (h.fday >= pd.Timestamp("2010-01-01")) & (h.fday < pd.Timestamp(f"{yy}-01-01"))]
        fit = pd.concat([old, new])
        beta, *_ = np.linalg.lstsq(fit[xc].to_numpy(), fit.y.to_numpy(), rcond=None)
        coefs[yy] = [float(b) for b in beta]
    f = h[h.block == 1].copy()
    yr = np.maximum(f.fday.dt.year.to_numpy(), EXPLORE_YEARS[0])
    b = np.array([coefs[int(v)] for v in yr])
    f["F"] = np.exp((f[xc].to_numpy() * b).sum(axis=1))
    f["coef_year"] = yr
    f["M"] = f.F.shift(1).rolling(MEDIAN_N, min_periods=MEDIAN_N).median()
    f["available_utc"] = pd.to_datetime(f.prev_last.astype("int64") + E.MIN, utc=True)
    return f[["fday", "available_utc", "F", "M", "coef_year"]].reset_index(drop=True), coefs


def join_gate(trades: pd.DataFrame, fc: pd.DataFrame, mult: dict[str, float]) -> pd.DataFrame:
    """Forecast as of each decision (`asof_join` on available_utc) and the gate (card section 4)."""
    left = trades.assign(ts_utc=pd.to_datetime(trades.dec.to_numpy(), utc=True), _pos=np.arange(len(trades)))
    j = asof_join(left, fc, on="ts_utc").sort_values("_pos").drop(columns=["_pos", "ts_utc"]).reset_index(drop=True)
    assert j.M.notna().all(), "a decision without a trailing median: warm-up too short"
    gsess = sessions(j.dec.to_numpy())
    j["gsess"] = gsess
    j["open"] = j.F.to_numpy() >= j.M.to_numpy() * np.array([mult[s] for s in gsess])
    j["year"] = pd.to_datetime(j.dec.to_numpy(), utc=True).year
    return j


# ---------------------------------------------------------------- tier-1 flat and pricing
def tier1_events(path: Path, end_ns: int) -> np.ndarray:
    c = pd.read_parquet(path, columns=["event_type", "tier", "event_utc", "available_utc"])
    ev = E._ns(pd.DatetimeIndex(pd.to_datetime(c.event_utc, utc=True)))
    av = E._ns(pd.DatetimeIndex(pd.to_datetime(c.available_utc, utc=True)))
    ok = (c.tier.to_numpy() == 1) & (av <= ev) & (ev < end_ns)
    return np.unique(ev[ok])


def tier1(t_fill: np.ndarray, t_exit: np.ndarray, side: np.ndarray, gross: np.ndarray, kind: np.ndarray,
          t: np.ndarray, o: np.ndarray, ev: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(keep, gross, t_exit, kind) after the tier-1 rule: no fill in [T-60, T+30] min; a position open at T-5 min
    closes at the open of the first minute at or after T-5 (only if that minute is before the original exit)."""
    t_fill = np.asarray(t_fill, np.int64)
    t_exit = np.asarray(t_exit, np.int64).copy()
    gross = np.asarray(gross, float).copy()
    kind = np.asarray(kind, object).copy()
    j = np.searchsorted(ev, t_fill - POST_MIN * E.MIN, side="left")       # first event with T + 30 >= fill
    blocked = (j < len(ev)) & (ev[np.minimum(j, len(ev) - 1)] - PRE_MIN * E.MIN <= t_fill)
    lead = ev - LEAD_MIN * E.MIN
    k = np.searchsorted(lead, t_fill, side="right")                       # first T-5 strictly after the fill
    for i in np.flatnonzero(~blocked & (k < len(ev))):
        tc = int(lead[k[i]])
        if tc >= t_exit[i]:
            continue
        kc = int(np.searchsorted(t, tc, side="left"))
        if kc >= len(t) or int(t[kc]) >= t_exit[i]:
            continue
        e = float(o[int(np.searchsorted(t, t_fill[i], side="left"))])
        gross[i] = float(side[i]) * (float(o[kc]) - e)
        t_exit[i] = int(t[kc])
        kind[i] = "news"
    return ~blocked, gross, t_exit, kind


def price_h94(df: pd.DataFrame, rt: dict[str, float], osh: dict[str, float], dtb3: pd.DataFrame,
              markup: tuple[float, float], swap: SwapSpec) -> pd.DataFrame:
    """Net R at the H-94 cost (module docstring). Needs t_fill, t_exit, side, risk, gross, kind, px."""
    df = df.copy()
    sess = sessions(df.t_fill.to_numpy())
    scale = df.px.to_numpy() / REF_PX
    rt_ = np.array([rt[s] for s in sess]) * scale
    os_ = np.array([osh[s] for s in sess]) * scale
    stop = df.kind.isin(["stop", "trail"]).to_numpy()
    ent = pd.DatetimeIndex(pd.to_datetime(df.t_fill.to_numpy(), utc=True))
    ext = pd.DatetimeIndex(pd.to_datetime(df.t_exit.to_numpy(), utc=True))
    nights = rollover_nights(ent, ext, swap.server_tz, swap.triple_weekday)
    left = pd.DataFrame({"ts_utc": ent, "_pos": np.arange(len(df))})
    r = asof_join(left, dtb3[["available_utc", "rate_pct"]]).sort_values("_pos").rate_pct.to_numpy(float)
    assert np.isfinite(r).all(), "DTB3 unknown at a fill"
    side = df.side.to_numpy()
    pct = np.where(side > 0, r + markup[0], markup[1] - r)                # %/yr paid (negative = credit)
    fin = nights * df.px.to_numpy() * pct / 100.0 / 365.0
    risk = df.risk.to_numpy()
    df["sess"] = sess
    df["rt"], df["os"], df["fin"] = rt_, os_ * stop, fin
    df["gross_R"] = df.gross.to_numpy() / risk
    df["cost_R"] = (rt_ + os_ * stop + fin) / risk
    df["R"] = df.gross_R - df.cost_R
    df["R_stress"] = (df.gross.to_numpy() - rt_ - STRESS_OS * scale * stop - fin) / risk
    df["floor_skip"] = risk < FLOOR_MULT * (rt_ + os_)
    return df


# ---------------------------------------------------------------- simulation (workers)
_CTX: Any = None


def _init(raw: str) -> None:
    global _CTX
    _CTX = E.build_ctx(Path(raw), "explore")


def _frame(rows: list[dict[str, Any]], sid: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["rule"] = sid
    k0 = np.searchsorted(_CTX.t, df.t_fill.to_numpy(), side="left")
    df["px"] = _CTX.o[k0]
    return df


def _sim(sid: str) -> dict[str, Any]:
    ctx = _CTX
    costs = A.load_costs(E.ROOT / "config" / "arm_costs.yaml")
    shipped = SwapSpec.shipped()
    res = E.simulate(ctx, sid, E.STRATS[sid](ctx))
    par = E.price(res["real"], costs, shipped)                              # the trend lane's own pricing
    parity = {"n": int(len(par)), "net_R": float(par.R.mean()), "gross_R": float(par.gross_R.mean())}
    ev = tier1_events(Path(E.ROOT.parents[2] / "raw" / "calendar" / "us_calendar.parquet")
                      if (E.ROOT.parents[2] / "raw").exists() else E.ROOT / "raw" / "calendar" / "us_calendar.parquet",
                      ctx.end)
    out: dict[str, Any] = {"sid": sid, "parity": parity, "n_events": int(len(ev))}
    for key in ("real", "twins"):
        df = _frame(res[key], sid)
        keep, g, tx, kd = tier1(df.t_fill.to_numpy(), df.t_exit.to_numpy(), df.side.to_numpy(), df.gross.to_numpy(),
                                df.kind.to_numpy(), ctx.t, ctx.o, ev)
        df["t1_block"] = ~keep
        df["t1_forced"] = keep & (kd == "news")
        df["gross"], df["t_exit"], df["kind"] = g, tx, kd
        out[key] = df
    return out


def load_pool(raw: Path, workers: int) -> dict[str, Any]:
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(str(raw),)) as ex:
        res = list(ex.map(_sim, P.SIDS))
    from goldbot.config import load_settings
    from goldbot.research.position.financing import load_fred
    markup = tuple(load_settings().research.position.markup_pct_by_symbol["XAUUSD"])
    dtb3 = load_fred(raw / "daily" / "fred_DTB3.parquet")
    rt, osh, swap = session_rt(), session_overshoot(), SwapSpec.shipped()
    assert swap.triple_weekday == 2
    real = pd.concat([r["real"] for r in res], ignore_index=True)
    twins = pd.concat([r["twins"] for r in res], ignore_index=True)
    real = price_h94(real, rt, osh, dtb3, (float(markup[0]), float(markup[1])), swap)
    twins = price_h94(twins, rt, osh, dtb3, (float(markup[0]), float(markup[1])), swap)
    return {"real": real, "twins": twins, "parity": {r["sid"]: r["parity"] for r in res},
            "n_events": res[0]["n_events"], "rt": rt, "os": osh, "markup": list(markup)}


# ---------------------------------------------------------------- statistics
def clustered_diff_t(y: np.ndarray, x: np.ndarray, g: np.ndarray) -> tuple[float, float]:
    """(mean(y|x) - mean(y|~x), its cluster-robust t (CR1, clusters g)) from OLS of y on [1, x]."""
    xm = np.column_stack([np.ones(len(y)), np.asarray(x, float)])
    xtx_inv = np.linalg.inv(xm.T @ xm)
    beta = xtx_inv @ xm.T @ y
    u = y - xm @ beta
    codes, gi = np.unique(g, return_inverse=True)
    s = np.zeros((len(codes), 2))
    np.add.at(s, gi, xm * u[:, None])
    nc, n = len(codes), len(y)
    adj = nc / (nc - 1) * (n - 1) / (n - 2)
    v = adj * xtx_inv @ (s.T @ s) @ xtx_inv
    return float(beta[1]), float(beta[1] / np.sqrt(v[1, 1]))


def placebo_masks(cell: np.ndarray, day: np.ndarray, target: dict[Any, int], n_draws: int,
                  seed: int) -> np.ndarray:
    """(n_draws, n) boolean: per cell, whole days in random order, each day opened if it brings the opened trade count
    closer to the target (the real gate's count in that cell); stops at the target."""
    rng = np.random.default_rng(seed)
    n = len(cell)
    out = np.zeros((n_draws, n), dtype=bool)
    for c in pd.unique(cell):
        idx = np.flatnonzero(cell == c)
        days, inv, cnt = np.unique(day[idx], return_inverse=True, return_counts=True)
        tgt = int(target.get(c, 0))
        for b in range(n_draws):
            chosen = np.zeros(len(days), dtype=bool)
            acc = 0
            for d in rng.permutation(len(days)):
                if abs(acc + cnt[d] - tgt) < abs(acc - tgt):
                    chosen[d] = True
                    acc += int(cnt[d])
                    if acc == tgt:
                        break
            out[b, idx] = chosen[inv]
    return out


def twin_excess(real: pd.DataFrame, twins: pd.DataFrame) -> tuple[float, int]:
    m = twins.groupby(["rule", "id"]).R.mean()
    key = pd.MultiIndex.from_arrays([real.rule, real.id])
    tw = m.reindex(key).to_numpy()
    ok = np.isfinite(tw)
    return float((real.R.to_numpy()[ok] - tw[ok]).mean()) if ok.any() else float("nan"), int(ok.sum())


def boot_u(r: np.ndarray, op: np.ndarray, day: np.ndarray, n: int, seed: int) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    codes, gi = np.unique(day, return_inverse=True)
    sr_all, n_all = np.bincount(gi, r), np.bincount(gi)
    sr_g, n_g = np.bincount(gi, r * op), np.bincount(gi, op.astype(float))
    us = []
    for _ in range(n):
        w = np.bincount(rng.integers(0, len(codes), len(codes)), minlength=len(codes))
        us.append((w @ sr_g) / (w @ n_g) - (w @ sr_all) / (w @ n_all))
    lo, hi = np.percentile(us, [5, 95])
    return float(lo), float(hi)


def uplift(df: pd.DataFrame, op: np.ndarray) -> float:
    return float(df.R.to_numpy()[op].mean() - df.R.mean())


# ---------------------------------------------------------------- the two modes
def build(raw: Path, workers: int) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    pool = load_pool(raw, workers)
    days = daily_ranges(raw)
    fc, coefs = har_forecasts(days)
    mult = gate_mult(pool["rt"])
    real = pool["real"]
    n_all = len(real)
    t1_block = int(real.t1_block.sum())
    kept = real[~real.t1_block]
    floor = int(kept.floor_skip.sum())
    ung = kept[~kept.floor_skip].reset_index(drop=True)
    ung = join_gate(ung, fc, mult)
    tw = pool["twins"]
    tw = tw[~tw.t1_block].merge(ung[["rule", "id"]], on=["rule", "id"])
    meta = {"parity": pool["parity"], "n_pool": n_all, "n_tier1_events": pool["n_events"],
            "tier1_entry_block": t1_block, "tier1_entry_block_rate": t1_block / n_all,
            "tier1_forced_exit_rate": float(kept.t1_forced.mean()), "cost_floor_skip": floor,
            "cost_floor_bind_rate": floor / max(len(kept), 1), "n_ungated": int(len(ung)),
            "rt_usd": pool["rt"], "overshoot_usd": pool["os"], "markup_pct": pool["markup"], "gate_mult": mult,
            "har_coef_by_year": coefs, "n_forecast_days": int(len(fc)),
            "gate_open_share": float(ung.open.mean()),
            "gate_open_share_by_session": {s: float(v) for s, v in ung.groupby("gsess").open.mean().items()}}
    return ung, tw, meta


def parity_check(par: dict[str, dict[str, float]]) -> dict[str, Any]:
    ref = {r["id"]: r for r in json.loads(TREND_EXPLORE.read_text())["results"]}
    rows, ok = {}, True
    for sid, p in par.items():
        r = ref[sid]
        same = p["n"] == r["n"] and abs(p["net_R"] - r["net_R"]) < 1e-12 and abs(p["gross_R"] - r["gross_R"]) < 1e-12
        ok &= same
        rows[sid] = {"n": p["n"], "n_ref": r["n"], "net_R": p["net_R"], "net_R_ref": r["net_R"], "exact": same}
    return {"exact_all": bool(ok), "rules": rows}


def selftest(raw: Path, workers: int) -> None:
    ung, _, meta = build(raw, workers)
    par = parity_check(meta["parity"])
    # shuffled gate: the real gate's (day, session) open flags permuted across days within year x session. The real
    # gate's own outcome split is never computed here.
    rng = np.random.default_rng(SEED + 1)
    key = ung[["fday", "gsess", "year", "open"]].drop_duplicates(["fday", "gsess"]).reset_index(drop=True)
    us = []
    for _ in range(N_SHUFFLE):
        sh = key.copy()
        for ix in sh.groupby(["year", "gsess"]).indices.values():
            sh.loc[ix, "open"] = rng.permutation(sh.loc[ix, "open"].to_numpy())
        op = ung[["fday", "gsess"]].merge(sh[["fday", "gsess", "open"]], on=["fday", "gsess"], how="left").open
        us.append(uplift(ung, op.to_numpy(bool)))
    cell = (ung.year.astype(str) + "|" + ung.gsess.astype(str) + "|" + ung.side.astype(str)).to_numpy()
    target = pd.Series(ung.open.to_numpy()).groupby(cell).sum().astype(int).to_dict()
    masks = placebo_masks(cell, ung.fday.to_numpy(), target, N_PLACEBO, SEED)
    pu = np.array([uplift(ung, m) for m in masks])
    sh_mean, sh_sd = float(np.mean(us)), float(np.std(us, ddof=1))
    out = {"card": "prereg-2026-11-13-h94-range-cost-gate.md @ 4a985b57", "parity": par,
           "shuffled_gate": {"n": N_SHUFFLE, "mean_U": sh_mean, "sd_U": sh_sd, "mc_se": sh_sd / np.sqrt(N_SHUFFLE),
                             "pass_abs_le_0.005": abs(sh_mean) <= 0.005},
           "placebo_machinery": {"n": N_PLACEBO, "mean_U": float(pu.mean()), "p95_U": float(np.percentile(pu, 95)),
                                 "matched_count_share": float(masks.sum(1).mean() / ung.open.sum())},
           "pool_ungated": {"n": int(len(ung)), "net_R": float(ung.R.mean()), "gross_R": float(ung.gross_R.mean()),
                            "cost_R": float(ung.cost_R.mean())},
           **{k: v for k, v in meta.items() if k != "parity"}}
    out["selftest_pass"] = bool(par["exact_all"] and out["shuffled_gate"]["pass_abs_le_0.005"])
    OUT.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "h94-selftest.json").write_text(json.dumps(out, indent=1, default=float) + "\n")
    print(json.dumps({k: out[k] for k in ("selftest_pass", "shuffled_gate", "placebo_machinery", "pool_ungated",
                                          "gate_open_share", "gate_open_share_by_session", "cost_floor_bind_rate",
                                          "tier1_entry_block_rate", "tier1_forced_exit_rate")}, indent=1, default=float))
    print({s: (r["n"], r["n_ref"], r["exact"]) for s, r in par["rules"].items()})


def read(raw: Path, workers: int) -> None:
    marker = OUT / "explore_read.marker"
    if marker.exists():
        raise SystemExit(f"the explore read was already taken ({marker}); card section 5 allows one")
    st = json.loads((DOCS / "h94-selftest.json").read_text())
    if not st.get("selftest_pass"):
        raise SystemExit("self-test not passed: no read")
    OUT.mkdir(parents=True, exist_ok=True)
    marker.write_text(pd.Timestamp.now(tz="UTC").isoformat())
    ung, tw, meta = build(raw, workers)
    op = ung.open.to_numpy(bool)
    g = ung[op]
    r = ung.R.to_numpy()
    U = uplift(ung, op)
    diff, t = clustered_diff_t(r, op.astype(float), ung.fday.to_numpy())
    yrs = {int(str(y)): float(gg.R[gg.open].mean() - gg.R.mean()) for y, gg in ung.groupby("year")}
    n_pos = sum(v > 0 for v in yrs.values())
    cost_g = float(g.cost_R.mean())
    gross_g = float(g.gross_R.mean())
    cell = (ung.year.astype(str) + "|" + ung.gsess.astype(str) + "|" + ung.side.astype(str)).to_numpy()
    target = pd.Series(op).groupby(cell).sum().astype(int).to_dict()
    masks = placebo_masks(cell, ung.fday.to_numpy(), target, N_PLACEBO, SEED)
    pu = np.array([uplift(ung, m) for m in masks])
    p95 = float(np.percentile(pu, 95))
    tw_g = tw.merge(g[["rule", "id"]], on=["rule", "id"])
    ex_g, n_ex = twin_excess(g, tw_g)
    ex_u, _ = twin_excess(ung, tw)
    sh = ung.side.to_numpy() < 0
    U_short = float(r[sh & op].mean() - r[sh].mean())
    U_long = float(r[~sh & op].mean() - r[~sh].mean())
    ci = boot_u(r, op, ung.fday.to_numpy(), N_BOOT, SEED + 2)
    legs = {
        "1_U_ge_0.05_and_t_ge_2": bool(U >= 0.05 and t >= 2.0),
        "2_U_pos_ge_5_of_8_years": bool(n_pos >= 5),
        "3_gated_net_pos_and_gross_ge_3x_cost": bool(g.R.mean() > 0 and gross_g >= 3 * cost_g),
        "4_U_above_placebo_p95": bool(U > p95),
        "5_gated_twin_excess_ge_0": bool(np.isfinite(ex_g) and ex_g >= 0),
        "6_short_U_ge_0": bool(U_short >= 0),
    }
    by = {}
    for col in ("rule", "gsess", "side"):
        by[col] = {str(k): {"n": int(len(gg)), "n_open": int(gg.open.sum()),
                            "U": float(gg.R[gg.open].mean() - gg.R.mean()) if gg.open.any() else None}
                   for k, gg in ung.groupby(col)}
    out = {"card": "prereg-2026-11-13-h94-range-cost-gate.md @ 4a985b57", "window": "explore 2010-01-01..2017-12-31",
           "verdict": "CONTINUE to TEST-OLD review" if all(legs.values()) else "FAIL: H-94 retired",
           "legs": legs,
           "U": U, "U_t_dayclustered": t, "open_minus_closed": diff, "U_ci90_dayboot": list(ci),
           "gated": {"n": int(len(g)), "net_R": float(g.R.mean()), "gross_R": gross_g, "cost_R": cost_g,
                     "stress_net_R": float(g.R_stress.mean()), "twin_excess": ex_g, "n_with_twins": n_ex},
           "ungated": {"n": int(len(ung)), "net_R": float(ung.R.mean()), "gross_R": float(ung.gross_R.mean()),
                       "cost_R": float(ung.cost_R.mean()), "stress_net_R": float(ung.R_stress.mean()),
                       "twin_excess": ex_u},
           "U_by_year": yrs, "years_positive": n_pos, "U_short": U_short, "U_long": U_long,
           "placebo": {"n": N_PLACEBO, "p95": p95, "mean": float(pu.mean()), "rank_pct": float((pu < U).mean())},
           "by": by, **{k: v for k, v in meta.items() if k != "parity"}}
    (DOCS / "h94-explore.json").write_text(json.dumps(out, indent=1, default=float) + "\n")
    print(json.dumps({k: out[k] for k in ("verdict", "legs", "U", "U_t_dayclustered", "U_ci90_dayboot", "gated",
                                          "ungated", "U_by_year", "years_positive", "U_short", "U_long", "placebo",
                                          "gate_open_share", "cost_floor_bind_rate", "tier1_entry_block_rate",
                                          "tier1_forced_exit_rate")}, indent=1, default=float))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=E.ROOT.parents[2] / "raw" if (E.ROOT.parents[2] / "raw").exists()
                    else E.ROOT / "raw")
    ap.add_argument("--workers", type=int, default=3)
    m = ap.add_mutually_exclusive_group(required=True)
    m.add_argument("--selftest", action="store_true")
    m.add_argument("--read", action="store_true")
    a = ap.parse_args()
    assert a.workers <= 4, "the shared Mac"
    (selftest if a.selftest else read)(a.raw, a.workers)


if __name__ == "__main__":
    main()
