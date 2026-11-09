"""EVAL-2 self-tests S0-S6 (method v6 section 6; prereg 2026-11-09 section 4.10). Run before any verdict is trusted.

    python scripts/eval2_selftest.py                 # S0..S6, full sizes, report in ~/tmp/eval2_selftest/
    python scripts/eval2_selftest.py --only S0       # one test
    python scripts/eval2_selftest.py --fast          # the CI sizes (selftest marker)
    python scripts/eval2_selftest.py --workers 4     # <= 8

Exit 1 when any test fails or is BLOCKED (a dependency lane has not landed): "S0-S6 pass, or the method is not used".

Data: real bars only from the explore window, decisions AND every bar read in 2010-01-01..2017-12-31 (`explore_bars`:
gold 5m..1h resampled from the 2010-2017 1m files only, 4h/1d/1w cut at 2010-01-01; no warm-up bar from 2009, so no
TEST-OLD bar is touched). Nothing at or after 2018-01-01 is read: `explore_bars` refuses a bar outside the span, and the
bar cache is re-checked on every load (a stale or corrupt cache file fails closed). Market data never leaves ~/tmp.

What each test needs
  S0  the harness only (sim, costs, twins): runs now.
  S1  null population: the harness (precomputed per-bar NULL nets) + Lane 3 `eval2.hier` / `eval2.confirm` /
      `catalogue.stats.cell_day_means`; BLOCKED until Lane 3 lands. The null population itself is built here.
  S2  label-shift plants: Lane 3 hier + `stats.power_at`; BLOCKED until Lane 3 lands.
  S3  full-path plant: the harness transmission check runs now (planted +0.20 R and 0 on gold-1h-shaped walks through
      sim + costs + 20 twins); L2 detection and the COST-BOUND / gate-only reads need Lane 3 (reported BLOCKED parts).
  S4  leakage: the realised-cost lint (Lane 2) runs now; the S21 OCO side-0 probe needs Lane 3 `eval2.probe`; the
      regime +1 shift is not applicable until a card registers a regime (no regime code in the harness).
  S5  execution, replay v2: Lane 5A; BLOCKED.
  S6  truncation: signals, ATR14/ATR20, twin geometry and higher-timeframe visibility on truncated bars: runs now
      (tests/test_eval2_truncation.py is the CI copy).

Not trials (prereg section 2): the plants and null replicates here are not registered and not counted in K."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import stats as sps

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from goldbot.research.catalogue import core as K  # noqa: E402
from goldbot.research.catalogue import data as DA  # noqa: E402
from goldbot.research.catalogue import sim as SIM  # noqa: E402
from goldbot.research.catalogue.core import EB, Ctx, Events  # noqa: E402
from goldbot.research.catalogue.costs import CostModel, default_model, window_model  # noqa: E402
from goldbot.research.catalogue.data import DAY, HOUR, TF_NS, Bars  # noqa: E402

TestName = Literal["S0", "S1", "S2", "S3", "S4", "S5", "S6"]
TESTS: tuple[TestName, ...] = ("S0", "S1", "S2", "S3", "S4", "S5", "S6")
MAX_WORKERS = 8

EXPLORE_FROM = int(pd.Timestamp("2010-01-01", tz="UTC").value)
EXPLORE_END = int(pd.Timestamp("2018-01-01", tz="UTC").value)        # exclusive: no bar closes after it
EXPLORE_YEARS = range(2010, 2018)
HARNESS_FILES = ("goldbot/research/catalogue/sim.py", "goldbot/research/catalogue/costs.py",
                 "goldbot/research/catalogue/core.py", "goldbot/research/catalogue/data.py",
                 "goldbot/research/catalogue/dispatcher.py", "config/catalogue_costs.yaml", "scripts/eval2_selftest.py")

# S0 thresholds (method section 6, verbatim numbers)
S0_TOL_R = 0.01
S0_T_MAX = 2.0
S0_CELL_FLOOR_R = -0.05
S0_CELL_N = 300
S0_CHI2_P = 0.01
S0_CHI2_MIN_N = 30          # a cell enters the chi2 only from 30 instances (a clustered t at n 6 is not N(0, 1))
# NULL-RAND entry rate per timeframe for S0 (the registered 1-4 % raised for n; the entry rate does not change the null:
# side and bar are a hash of the bar time). One position per cell at a time still caps n.
S0_RATE = {"5m": 0.03, "15m": 0.08, "30m": 0.10, "1h": 0.10, "4h": 0.15, "1d": 0.30, "1w": 0.50}
GOLD_TFS = ("5m", "15m", "30m", "1h", "4h", "1d", "1w")
NONGOLD_TFS = ("4h", "1d", "1w")
S0_FAST_CELLS = (("XAUUSD", "1h"),)
S0_FAST_YEARS = (2016, 2017)
# S3 (CI fast set): planted +0.20 R on 1h / 1,000 detected >= 80 %; planted 0 detected <= 5 %
S3_PLANT_R = 0.20
S3_N = 1_000
S3_DETECT_MIN = 0.80
S3_FALSE_MAX = 0.05
CP_SLACK = 0.02            # S1/S3 rates: Clopper-Pearson 90 % upper bound <= threshold + 2 pp
Z_DETECT = float(sps.norm.ppf(0.975))   # one-sided 2.5 %: a 5 % rule would sit ON the 5 % false-pass ceiling


@dataclass(frozen=True, slots=True)
class SelfTestResult:
    test: TestName
    passed: bool
    metrics: Mapping[str, float]
    threshold: str
    harness_hash: str
    notes: tuple[str, ...] = field(default=())     # extra (not in the frozen signature's positional fields): reasons


# ------------------------------------------------------------------ shared helpers
def out_dir() -> Path:
    d = Path(os.environ.get("EVAL2_SELFTEST_DIR", str(Path.home() / "tmp" / "eval2_selftest")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def harness_hash() -> str:
    """sha256 (16 hex) of the harness files' bytes: S0-S6 results are valid for this hash only (method: once per
    harness hash)."""
    h = hashlib.sha256()
    for f in HARNESS_FILES:
        p = ROOT / f
        h.update(f.encode())
        h.update(p.read_bytes() if p.exists() else b"<missing>")
    return h.hexdigest()[:16]


def cluster_t(x: np.ndarray, day: np.ndarray) -> tuple[float, float, float, int]:
    """(mean, t, se, n) of the finite values of `x` with the standard error clustered by `day`."""
    x = np.asarray(x, float)
    ok = np.isfinite(x)
    x, day = x[ok], np.asarray(day)[ok]
    if len(x) < 2:
        return float("nan"), float("nan"), float("nan"), int(len(x))
    m = float(x.mean())
    _, inv = np.unique(day, return_inverse=True)
    s = np.bincount(inv, weights=x - m)
    se = float(np.sqrt((s ** 2).sum())) / len(x)
    return m, (m / se if se > 0 else float("nan")), se, int(len(x))


def cp_upper(k: int, n: int, level: float = 0.90) -> float:
    """One-sided Clopper-Pearson upper bound at `level` for k successes in n trials."""
    if n <= 0:
        return 1.0
    if k >= n:
        return 1.0
    return float(sps.beta.ppf(level, k + 1, n - k))


def cp_lower(k: int, n: int, level: float = 0.90) -> float:
    if n <= 0 or k <= 0:
        return 0.0
    return float(sps.beta.ppf(1 - level, k, n - k + 1))


def _check_span(b: Bars, lo: int = EXPLORE_FROM, hi: int = EXPLORE_END) -> Bars:
    """Fail closed: every bar opens at or after `lo` and closes at or before `hi` (explore 2010-2017 only)."""
    if len(b) and (int(b.t.min()) < lo or int(b.ta.max()) > hi or int(b.t.max()) >= hi):
        raise DA.HoldoutSealed(f"{b.inst} {b.tf}: a bar outside 2010-01-01..2017-12-31 reached the self-test")
    DA.assert_sealed(b.ta, b.t)
    return b


def _bars_from_frame(inst: str, tf: str, df: pd.DataFrame) -> Bars:
    t = DA.epoch_ns(df["t"]) if len(df) else np.zeros(0, np.int64)
    ta = DA.epoch_ns(df["ta"]) if len(df) else np.zeros(0, np.int64)
    keep = (t >= EXPLORE_FROM) & (t < EXPLORE_END) & (ta <= EXPLORE_END)
    b = DA._frame_to_bars(inst, tf, df[keep].reset_index(drop=True))
    b.meta.update(window="explore", dec_from=EXPLORE_FROM, end=EXPLORE_END)
    return _check_span(b)


def explore_bars(inst: str, tf: str, years: Sequence[int] | None = None) -> Bars:
    """Real bars of one cell, every bar inside 2010-01-01..2017-12-31 (no warm-up from 2009). Gold 5m-1h from the
    yearly 1m files of those years only (one year at a time: bars never span a year end), 4h/1d from the stored files,
    1w from the stored 1d. Cached as npz under out_dir()/bars; a cache file is re-checked on load (fail closed)."""
    yrs = tuple(years) if years else tuple(EXPLORE_YEARS)
    if any(y not in EXPLORE_YEARS for y in yrs):
        raise DA.HoldoutSealed(f"years {yrs}: the self-tests read explore 2010-2017 only")
    cache = out_dir() / "bars" / f"{inst}_{tf}_{yrs[0]}_{yrs[-1]}.npz"
    if cache.exists():
        z = np.load(cache, allow_pickle=False)
        b = Bars(inst, tf, z["t"], z["ta"], z["o"], z["h"], z["l"], z["c"], z["spr"], z["co"], z["seg"], z["vol"])
        b.meta.update(window="explore", dec_from=EXPLORE_FROM, end=EXPLORE_END)
        return _check_span(b)
    if inst == "XAUUSD" and tf in DA.INTRADAY and tf != "4h":
        parts = [DA._resample_minutes(m, tf) for y in yrs if len(m := DA._gold_minutes(range(y, y + 1)))]
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
            columns=["t", "ta", "o", "h", "l", "c", "spr", "vol", "src"])
    elif tf == "1w":
        df = DA._weekly(DA._stored(inst, "1d"))
    else:
        df = DA._stored(inst, tf)
    if years:
        y = pd.to_datetime(df["t"], utc=True).dt.year
        df = df[y.isin(yrs).to_numpy()].reset_index(drop=True)
    b = _bars_from_frame(inst, tf, df)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache, t=b.t, ta=b.ta, o=b.o, h=b.h, l=b.l, c=b.c, spr=b.spr, co=b.co, seg=b.seg, vol=b.vol)
    return b


def explore_costs() -> CostModel:
    """The explore window's cost model (measured table + FRED benchmark rates cut at 2018-01-01)."""
    return window_model("explore")


COL = {k: j for j, k in enumerate(SIM.TRADE_COLS)}


def random_entries(b: Bars, *, rate: float, seed_key: str) -> Events:
    """NULL-RAND geometry (random side, stop 1 ATR14, target 2 ATR14, hold 10, market entry) at a keyed `rate` of
    the bars where ATR14 exists. With seed_key "" this is dispatcher.null_rand's draw (hash of the bar time)."""
    if not seed_key:
        from goldbot.research.catalogue import dispatcher as D
        return D.null_rand(b, {"rate": rate}, Ctx(window="explore"))
    a = K.atr(b, 14)
    j = np.flatnonzero(np.isfinite(a))
    rng = np.random.default_rng(int.from_bytes(hashlib.sha256(f"{seed_key}|{b.inst}|{b.tf}".encode()).digest()[:8],
                                               "big"))
    u = rng.random((len(j), 2))
    eb = EB()
    for jj, (p, s) in zip(j, u):
        if p < rate:
            eb.add(int(jj), 1 if s < 0.5 else -1, stop_d=a[jj], tgt_d=2 * a[jj], hold=10)
    return eb.build()


def cell_units(b: Bars, ev: Events, *, seed_key: str, costs: CostModel, family: str,
               fire: np.ndarray | None = None) -> pd.DataFrame:
    """EVAL-2 instances of one cell through the real harness path (run.unit_twins without the registry): `simulate`
    signals, `draw_twins` off the family's fire bars, `simulate_twins` with 20 twins, session-day clustering."""
    tr = SIM.simulate(b, ev, control="random_side", seed_key=seed_key, costs=costs)
    cols = ["cell_id", "family", "inst", "tf", "side", "day", "decision_ns", "net_r", "twin_mean_net_r", "excess_r",
            "cost_r", "year", "n_twins", "gross_r"]
    if not len(tr):
        return pd.DataFrame(columns=cols)
    row_of = {(int(i), int(s)): k for k, (i, s) in enumerate(zip(ev.i, ev.side))}
    ks = [row_of[(int(r[COL["i"]]), int(r[COL["side"]]))] for r in tr]
    exits = np.full(len(ev), -1, np.int64)
    exits[ks] = tr[:, COL["x"]].astype(np.int64)
    if fire is None:
        fire = np.zeros(len(b), bool)
        fire[ev.i] = True
    draws = SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key=seed_key)
    tw = SIM.simulate_twins(b, ev, [draws[k] for k in ks], costs=costs)
    gross = tr[:, COL["gross"]]
    net = gross - tr[:, COL["cost"]]
    ex = SIM.excess_r(net, tw)
    day = SIM.session_day(tr[:, COL["t_fill"]].astype(np.int64))
    from goldbot.research.catalogue import stats as S
    return pd.DataFrame({
        "cell_id": f"{family}|{b.inst}|{b.tf}", "family": family, "inst": b.inst, "tf": b.tf,
        "side": tr[:, COL["side"]].astype(np.int64), "day": day, "decision_ns": tr[:, COL["t_dec"]].astype(np.int64),
        "net_r": net, "twin_mean_net_r": net - ex, "excess_r": ex, "cost_r": tr[:, COL["cost"]],
        "year": S.year_of_day(day), "n_twins": np.isfinite(tw).sum(axis=1), "gross_r": gross})


def _lane(mod: str) -> Any | None:
    """A Lane 3/5A module, or None while it has not landed (the dependent part reads BLOCKED)."""
    import importlib
    try:
        return importlib.import_module(mod)
    except ImportError:
        return None


def _blocked(test: TestName, what: str, hh: str, metrics: Mapping[str, float] | None = None,
             notes: Sequence[str] = ()) -> SelfTestResult:
    return SelfTestResult(test, False, {"blocked": 1.0, **(metrics or {})}, f"BLOCKED: {what}", hh, tuple(notes))


def _pool_map(fn: Callable[[Any], Any], items: Sequence[Any], workers: int) -> list[Any]:
    workers = max(1, min(MAX_WORKERS, workers))
    if workers == 1 or len(items) <= 1:
        return [fn(x) for x in items]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


# ------------------------------------------------------------------ S0 harness
def registered_null_cells() -> set[tuple[str, str]]:
    """The registered NULL-RAND control cells (specs.yaml NULL-RAND-<tf> units x dispatcher.cells): S0's gate set."""
    from goldbot.research.catalogue import dispatcher as D
    return {c for u in D.units() if u["detector"] == "NULL_RAND" for c in D.cells(u)}


def s0_cells(fast: bool) -> list[tuple[str, str]]:
    if fast:
        return list(S0_FAST_CELLS)
    out = [("XAUUSD", tf) for tf in GOLD_TFS]
    for inst in DA.BAR_KEYS:
        if inst == "XAUUSD":
            continue
        for tf in NONGOLD_TFS:
            if (DA.raw_dir() / "daily" / "bars" / f"{DA.BAR_KEYS[inst]}_{'1d' if tf == '1w' else tf}.parquet").exists():
                out.append((inst, tf))
    return out


def _s0_cell(args: tuple[str, str, str, bool]) -> dict[str, Any]:
    inst, tf, seed_key, fast = args
    t0 = time.time()
    try:
        b = explore_bars(inst, tf, S0_FAST_YEARS if fast else None)
        cm = explore_costs()
        cm.inst(inst)
    except (FileNotFoundError, LookupError) as e:
        return {"inst": inst, "tf": tf, "skipped": f"{type(e).__name__}: {e}"}
    ev = random_entries(b, rate=S0_RATE[tf], seed_key="")
    df = cell_units(b, ev, seed_key=seed_key, costs=cm, family="NULL-RAND")
    return {"inst": inst, "tf": tf, "units": df, "bars": len(b), "secs": round(time.time() - t0, 1),
            "first": int(b.t[0]) if len(b) else 0, "last_close": int(b.ta[-1]) if len(b) else 0}


def s0_metrics(frames: Sequence[pd.DataFrame]) -> dict[str, Any]:
    """Pooled and per-cell S0 statistics on real bars. Real cells share calendar days, so the pooled SE is clustered on
    the session day across cells (no per-cell day offset, unlike the independent random walks of the CI copy)."""
    u = pd.concat([f for f in frames if len(f)], ignore_index=True)
    day = u["day"].to_numpy(np.int64)
    g, tg, seg, n = cluster_t(u["gross_r"].to_numpy(float), day)
    e, te, see, ne = cluster_t(u["excess_r"].to_numpy(float), day)
    has = u["n_twins"].to_numpy() > 0
    out: dict[str, Any] = {"n": n, "n_with_twin": int(has.sum()), "gross": g, "t_gross": tg, "se_gross": seg,
                           "excess": e, "t_excess": te, "se_excess": see,
                           "m_eff": float(u.loc[has, "n_twins"].mean()) if has.any() else float("nan")}
    sig = u["net_r"].to_numpy(float)
    tw = u["twin_mean_net_r"].to_numpy(float)
    ok = np.isfinite(sig) & np.isfinite(tw)
    if ok.sum() > 3:
        dd, inv = np.unique(day[ok], return_inverse=True)
        ss, ts = np.bincount(inv, weights=sig[ok]), np.bincount(inv, weights=tw[ok])
        out["rho"] = float(np.corrcoef(ss, ts)[0, 1]) if ss.std() > 0 and ts.std() > 0 else float("nan")
    cells: list[dict[str, Any]] = []
    for cid, c in u.groupby("cell_id", sort=True):
        d = c["day"].to_numpy(np.int64)
        cg, ctg, _, cn = cluster_t(c["gross_r"].to_numpy(float), d)
        ce, cte, _, cne = cluster_t(c["excess_r"].to_numpy(float), d)
        cells.append({"cell": cid, "tf": str(c["tf"].iloc[0]), "n": cn, "gross": cg, "t_gross": ctg,
                      "se_gross": cg / ctg if ctg else float("nan"), "excess": ce, "t_excess": cte, "n_excess": cne,
                      "m_eff": float(c.loc[c["n_twins"] > 0, "n_twins"].mean())})
    out["cells"] = cells
    t_ok = [c for c in cells if np.isfinite(c["t_excess"]) and c["n_excess"] >= S0_CHI2_MIN_N]
    chi = float(sum(c["t_excess"] ** 2 for c in t_ok))
    out["chi2"], out["chi2_df"] = chi, len(t_ok)
    out["chi2_p"] = float(sps.chi2.sf(chi, len(t_ok))) if t_ok else float("nan")
    tg_ok = [c for c in cells if np.isfinite(c["t_gross"]) and c["n"] >= S0_CHI2_MIN_N]
    chig = float(sum(c["t_gross"] ** 2 for c in tg_ok))
    out["chi2_gross_p"] = float(sps.chi2.sf(chig, len(tg_ok))) if tg_ok else float("nan")
    out["floor_fail"] = [c["cell"] for c in cells if c["n"] >= S0_CELL_N and c["gross"] < S0_CELL_FLOOR_R]
    # the floor as written has no sampling term: a sound cell at n 300-1,000 (SE 0.04-0.08 R) sits below -0.05 R with
    # probability Phi(-0.05 / SE), 10-27 %. Reported: the expected number of literal floor failures of a sound harness,
    # and the cells that are below the floor AND significantly negative (t <= -2), the reading a quant amendment needs
    big = [c for c in cells if c["n"] >= S0_CELL_N and np.isfinite(c["se_gross"]) and c["se_gross"] > 0]
    out["floor_expected_false"] = float(sum(sps.norm.cdf(S0_CELL_FLOOR_R / c["se_gross"]) for c in big))
    out["floor_cells"] = len(big)
    out["floor_fail_sig"] = [c["cell"] for c in big if c["gross"] < S0_CELL_FLOOR_R and c["t_gross"] <= -2.0]
    by_tf = {}
    for tf, c in u.groupby("tf", sort=False):
        d = c["day"].to_numpy(np.int64)
        tg_, ttg, _, tn = cluster_t(c["gross_r"].to_numpy(float), d)
        te_, tte, _, _ = cluster_t(c["excess_r"].to_numpy(float), d)
        by_tf[str(tf)] = {"n": tn, "gross": tg_, "t_gross": ttg, "excess": te_, "t_excess": tte,
                          "m_eff": float(c.loc[c["n_twins"] > 0, "n_twins"].mean()),
                          "m_full_share": float((c["n_twins"] >= SIM.TWIN_M).mean())}
    out["by_tf"] = by_tf
    return out


def s0_sentinels() -> dict[str, Any]:
    """Buy-every-close (NULL_LONGCLOSE on gold 1d 2010-17) and a drift-walk long-every-close must fail E9 (drift is
    not edge). The L2 half of the sentinel needs Lane 3 (reported None until then)."""
    from goldbot.research.catalogue import dispatcher as D
    from goldbot.research.catalogue import stats as S
    out: dict[str, Any] = {}
    cm = explore_costs()
    b = explore_bars("XAUUSD", "1d")
    ev = D.null_longclose(b, {}, Ctx(window="explore"))
    for name, bars, costs in (("buy_every_close", b, cm), ("drift_walk", drift_walk(b), default_model())):
        tr = SIM.simulate(bars, ev, control="random_time", seed_key=f"sentinel|{name}", costs=costs,
                          drift_per_ns=SIM.window_drift_per_ns(bars))
        df = pd.DataFrame(tr, columns=SIM.TRADE_COLS)
        df["inst"], df["tf"] = "XAUUSD", "1d"
        mk = S.market_from_bars({"XAUUSD": bars}, EXPLORE_FROM, EXPLORE_END, costs)
        s = S.summarize(df, mk)
        gates = S.explore_gates(s, ["1d"], None)
        out[name] = {"n": int(s.get("n", 0)), "gross": float(s.get("gross", float("nan"))),
                     "E9": bool(gates.get("E9", False)), "explore_pass": S.explore_pass(gates), "L2": None}
    return out


def drift_walk(b: Bars, *, annual: float = 0.15, seed: int = 20261109) -> Bars:
    """A random walk with b's timestamps and ATR scale plus a strong upward drift (15 %/yr): long-every-close earns the
    drift, which E9 must refuse as beta."""
    rng = np.random.default_rng(seed)
    n = len(b)
    r = np.log(b.c[1:] / b.c[:-1])
    sd = float(np.nanstd(r)) if n > 2 else 0.01
    mu = annual / 252
    steps = rng.normal(mu, sd, n)
    c = float(b.c[0]) * np.exp(np.cumsum(steps))
    o = np.concatenate([[float(b.c[0])], c[:-1]])
    wig = np.abs(rng.normal(0, sd / 2, n))
    h = np.maximum(o, c) * (1 + wig)
    lo = np.minimum(o, c) * (1 - wig)
    out = Bars(b.inst, b.tf, b.t.copy(), b.ta.copy(), o, h, lo, c, b.spr.copy(), np.zeros(n, bool),
               np.zeros(n, np.int32), b.vol.copy())
    out.meta.update(b.meta)
    return out


def s0_harness(*, harness_hash: str, seed_key: str, fast: bool = False, workers: int = 4) -> SelfTestResult:
    """S0 on real explore bars: NULL-RAND controls per timeframe with 20 twins.

    Gates (method section 6): pooled mean gross and mean excess within +/-0.01 R with |t| < 2 (day-clustered); no
    control cell with gross < -0.05 R at n >= 300; control-cell dispersion = sampling variance (chi2 of the cells'
    squared excess t, p > 0.01); buy-every-close and drift-walk fail E9. Per-timeframe |t| is reported and enters
    through the chi2: gating |t| < 2 in each of ~14 (TF x gross/excess) cells would fail a sound harness about half
    the time. Fast (CI): one gold 1h cell, 2016-17, |t| < 3 only."""
    t0 = time.time()
    res = _pool_map(_s0_cell, [(i, tf, seed_key, fast) for i, tf in s0_cells(fast)], workers)
    skipped = {f"{r['inst']}|{r['tf']}": r["skipped"] for r in res if "skipped" in r}
    reg = registered_null_cells()
    frames = [r["units"] for r in res if "units" in r and (fast or (r["inst"], r["tf"]) in reg)]
    ext = [r["units"] for r in res if "units" in r and not fast and (r["inst"], r["tf"]) not in reg]
    m = s0_metrics(frames)
    mx = s0_metrics(frames + ext) if ext else {}
    notes = [f"skipped {k}: {v}" for k, v in skipped.items()]
    if m.get("floor_fail"):
        notes.append(f"cell floor (literal) failed in {m['floor_fail']}; significantly below the floor (t <= -2): "
                     f"{m['floor_fail_sig'] or 'none'}; expected literal failures of a sound harness over "
                     f"{m['floor_cells']} cells at n >= 300: {m['floor_expected_false']:.1f}")
    if mx:
        notes.append(f"all {len(frames) + len(ext)} cells (registered + {len(ext)} non-registered, reported, not "
                     f"gated): pooled gross {mx['gross']:+.4f} (t {mx['t_gross']:+.2f}), excess {mx['excess']:+.4f} "
                     f"(t {mx['t_excess']:+.2f}), chi2 p {mx['chi2_p']:.3f}, literal floor fails {mx['floor_fail']}, "
                     f"significant {mx['floor_fail_sig']}")
    if fast:
        ok = abs(m["t_gross"]) < 3 and abs(m["t_excess"]) < 3
        thr = "fast: one gold 1h cell 2016-17, |t gross| < 3 and |t excess| < 3"
        sent: dict[str, Any] = {}
    else:
        sent = s0_sentinels()
        ok = (abs(m["gross"]) <= S0_TOL_R and abs(m["t_gross"]) < S0_T_MAX and abs(m["excess"]) <= S0_TOL_R
              and abs(m["t_excess"]) < S0_T_MAX and not m["floor_fail"] and m["chi2_p"] > S0_CHI2_P
              and not any(v["E9"] for v in sent.values()))
        thr = ("pooled |gross|, |excess| <= 0.01 R and |t| < 2; no cell gross < -0.05 R at n >= 300; chi2 p > 0.01; "
               "sentinels fail E9 (L2 half BLOCKED on Lane 3)")
        notes.append("sentinel L2 half not run: Lane 3 hier not landed")
    metrics = {k: float(v) for k, v in m.items() if isinstance(v, (int, float))}
    metrics["cells_run"] = float(len(frames))
    metrics["floor_fail_n"] = float(len(m["floor_fail"]))
    metrics["floor_fail_sig_n"] = float(len(m["floor_fail_sig"]))
    metrics["secs"] = round(time.time() - t0, 1)
    for k, v in sent.items():
        metrics[f"sentinel_{k}_E9"] = float(v["E9"])
        metrics[f"sentinel_{k}_gross"] = v["gross"]
    _write_json("S0", {"metrics": m, "extension": mx, "sentinels": sent, "skipped": skipped,
                       "bars": {f"{r['inst']}|{r['tf']}": [r.get("bars"), r.get("first"), r.get("last_close"),
                                                          r.get("secs")] for r in res if "units" in r}})
    return SelfTestResult("S0", bool(ok), metrics, thr, harness_hash, tuple(notes))


def _write_json(name: str, obj: Any) -> Path:
    p = out_dir() / f"{name}_{time.strftime('%Y%m%dT%H%M%S')}.json"
    p.write_text(json.dumps(obj, default=_jsonable, indent=1))
    return p


def _jsonable(x: Any) -> Any:
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, pd.DataFrame):
        return f"<frame {len(x)} rows>"
    return str(x)


# ------------------------------------------------------------------ per-bar NULL table (S1, S2 base)
def null_table(b: Bars, costs: CostModel) -> np.ndarray:
    """net[n, 2] of a NULL-geometry market entry decided at every bar, for side +1 (col 0) and -1
    (col 1), by the harness's own single-order replay (`sim._replay_net` on `sim._twin_events`): a twin of a
    NULL-geometry signal at bar d IS the NULL entry at d (stop/target are ATR14 multiples at the twin's bar), so one
    table gives every signal's and every twin's net. tests/test_eval2_selftest.py checks the table against
    `simulate` + `simulate_twins`. NaN where no order is placed (no ATR14, risk floor) or it never fills."""
    n = len(b)
    a14 = K.atr(b, SIM.TWIN_ATR_N)
    a20 = K.atr(b, SIM.RISK_FLOOR_ATR_N)
    net = np.full((n, 2), np.nan)
    for d in range(n - 1):
        if not (np.isfinite(a14[d]) and a14[d] > 0):
            continue
        for c, side in enumerate((1, -1)):
            ev = Events(i=np.array([d]), side=np.array([side], np.int8), kind=np.zeros(1, np.int8),
                        px=np.array([np.nan]), valid=np.ones(1, np.int64), stop=np.array([np.nan]),
                        stop_d=np.array([a14[d]]), tgt=np.array([np.nan]), tgt_d=np.array([2 * a14[d]]),
                        hold=np.array([10], np.int64), oco=np.zeros(1, np.int8))
            net[d, c] = SIM._replay_net(b, ev, a20, costs, None)
    return net


# ------------------------------------------------------------------ S1 null population
S1_GRID = (("XAUUSD", "15m"), ("XAUUSD", "30m"), ("XAUUSD", "1h"), ("XAUUSD", "4h"), ("XAUUSD", "1d"),
           ("XAGUSD", "4h"), ("XAGUSD", "1d"), ("EURUSD", "4h"), ("EURUSD", "1d"), ("US500", "4h"), ("US500", "1d"))


def _s1_table(args: tuple[str, str]) -> tuple[str, str, np.ndarray | None, Bars | None]:
    inst, tf = args
    p = out_dir() / "null_tables" / f"{inst}_{tf}_{harness_hash()}.npz"
    try:
        b = explore_bars(inst, tf)
    except FileNotFoundError:
        return inst, tf, None, None
    if p.exists():
        return inst, tf, np.load(p)["net"], b
    net = null_table(b, explore_costs())
    p.parent.mkdir(parents=True, exist_ok=True)
    np.savez(p, net=net)
    return inst, tf, net, b


def null_family_units(tables: Mapping[tuple[str, str], tuple[Bars, np.ndarray]], *, family: str, seed_key: str,
                      rate: Mapping[str, float] = S0_RATE) -> pd.DataFrame:
    """One null family over the cell grid: random entries (keyed by `seed_key`), one position per cell at a time (a
    NULL trade exits at the latest at decision + hold bars, so the next entry is decided after that: slightly sparser
    than `simulate`, never overlapping), twins drawn by `draw_twins` after that latest exit and off the family's fire
    bars, every net read from the per-bar table (`null_table`)."""
    from goldbot.research.catalogue import stats as S
    frames = []
    for (inst, tf), (b, net) in tables.items():
        ev = random_entries(b, rate=rate[tf], seed_key=f"{seed_key}|{family}")
        if not len(ev):
            continue
        keep, busy = [], -1
        for k in range(len(ev)):
            i = int(ev.i[k])
            c = 0 if int(ev.side[k]) > 0 else 1
            if i < 1 or int(b.ta[i]) < EXPLORE_FROM or i <= busy or not np.isfinite(net[i, c]):
                continue
            keep.append(k)
            busy = i + 10
        if not keep:
            continue
        kk = np.array(keep)
        fire = np.zeros(len(b), bool)
        fire[ev.i] = True
        exits = np.full(len(ev), -1, np.int64)
        exits[kk] = np.minimum(ev.i[kk] + 10, len(b) - 2)        # conservative: the latest a NULL trade can exit
        draws = SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key=f"{seed_key}|{family}")
        sides = np.where(ev.side[kk] > 0, 0, 1)
        sig = net[ev.i[kk], sides]
        tw = np.full((len(kk), SIM.TWIN_M), np.nan)
        for r, k in enumerate(kk):
            d = draws[k].decision_bars
            ok = d >= 0
            tw[r, ok] = net[d[ok], sides[r]]
        ex = SIM.excess_r(sig, tw)
        day = SIM.session_day(b.t[np.minimum(ev.i[kk] + 1, len(b) - 1)].astype(np.int64))
        frames.append(pd.DataFrame({
            "cell_id": f"{family}|{inst}|{tf}", "family": family, "inst": inst, "tf": tf,
            "side": ev.side[kk].astype(np.int64), "day": day, "decision_ns": b.ta[ev.i[kk]].astype(np.int64),
            "net_r": sig, "twin_mean_net_r": sig - ex, "excess_r": ex, "cost_r": np.nan, "year": S.year_of_day(day)}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def l2_family(units: pd.DataFrame, prior_cells: pd.DataFrame, *, family: str, hier: Any, stats: Any,
              spec: Any) -> Any:
    """Lane 3's L2 on one family against the frozen signatures: cell_day_means -> fit_prior(leave the family out) ->
    posteriors -> l2_decide. The deciding cost per cell is its mean cost (Lane 2's deciding_cost when it is wired)."""
    cells = stats.cell_day_means(units, col="excess_r")
    meta = units.groupby("cell_id")[["family", "inst", "tf"]].first().reset_index()
    cells = cells.merge(meta, on="cell_id", how="left")
    pop = pd.concat([prior_cells, cells], ignore_index=True)
    prior = hier.fit_prior(pop, leave_out_family=family, spec=spec)
    cost = units.groupby("cell_id")["cost_r"].mean().fillna(0.0).to_dict()
    posts = hier.posteriors(cells, prior, spec=spec, deciding_cost_r={str(k): float(v) for k, v in cost.items()})
    return hier.l2_decide(posts, family=family, spec=spec)


def s1_null_population(*, reps: int = 500, workers: int = 8, seed_key: str) -> SelfTestResult:
    """S1: >= 500 null families (random entries over the real cell grid, real bars and costs, NULL stop geometry and
    exits) through L2 and the confirm gate. Gates: L2 family carry rate CP 90 % UB <= 7 %; P(any family passes when
    all are null) and confirm FDR each with CP 90 % UB <= 12 %. The forward e-process and shuffled-regime parts are
    BLOCKED (Lane 5A, no regime code). Every replicate stays on explore bars: no TEST-OLD bar is read by a null."""
    hh = harness_hash()
    hier, confirm, stats = (_lane("goldbot.research.eval2.hier"), _lane("goldbot.research.eval2.confirm"),
                            _lane("goldbot.research.catalogue.stats"))
    if hier is None or confirm is None or not hasattr(stats, "cell_day_means"):
        return _blocked("S1", "Lane 3 eval2.hier / eval2.confirm / stats.cell_day_means not landed", hh,
                        notes=("the null population builder (null_table, null_family_units) is ready; "
                               "run S1 first once Lane 3 merges, before any L2 on real cells",))
    tabs = _pool_map(_s1_table, list(S1_GRID), workers)
    tables = {(i, tf): (b, net) for i, tf, net, b in tabs if net is not None and b is not None}
    return s1_from_tables(tables, reps=reps, seed_key=seed_key, hier=hier, confirm=confirm, stats=stats, hh=hh)


def s1_from_tables(tables: Mapping[tuple[str, str], tuple[Bars, np.ndarray]], *, reps: int, seed_key: str,
                   hier: Any, confirm: Any, stats: Any, hh: str, batch_m: int = 3) -> SelfTestResult:
    spec = hier.HierSpec()
    fams = [null_family_units(tables, family=f"S1NULL{r:04d}", seed_key=seed_key) for r in range(reps)]
    # an instance with no twin has no excess (quant item 4: left out of the excess); never hand NaN to Lane 3
    fams = [f[np.isfinite(f["excess_r"].to_numpy(float))].reset_index(drop=True) for f in fams if len(f)]
    fams = [f for f in fams if len(f)]
    all_cells = []
    for f in fams:
        c = stats.cell_day_means(f, col="excess_r")
        all_cells.append(c.merge(f.groupby("cell_id")[["family", "inst", "tf"]].first().reset_index(), on="cell_id"))
    pop = pd.concat(all_cells, ignore_index=True)
    carried_fam = carried_cells = n_cells = 0
    carried_by_fam: dict[str, tuple[str, ...]] = {}
    for f in fams:
        fam = str(f["family"].iloc[0])
        dec = l2_family(f, pop[pop["family"] != fam], family=fam, hier=hier, stats=stats, spec=spec)
        carried_by_fam[fam] = tuple(dec.carried)
        carried_fam += bool(dec.carried)
        carried_cells += len(dec.carried)
        n_cells += f["cell_id"].nunique()
    n = len(fams)
    rate = carried_fam / n if n else float("nan")
    ub = cp_upper(carried_fam, n)
    # confirm: batches of `batch_m` carried null families at batch 1; any rejection is false (every family is null)
    batch = confirm.BatchPRDS(window="test_old")
    carried = [f for f in fams if carried_by_fam[str(f["family"].iloc[0])]]
    any_pass = n_batches = 0
    for j0 in range(0, len(carried) - batch_m + 1, batch_m):
        grp = carried[j0:j0 + batch_m]
        ps = [confirm.boot_p_one_sided(g, lambda u: float(u["excess_r"].mean()), mean_block=5.0,
                                       resamples=confirm.confirm_resamples(batch_m, batch.level(1, batch_m)),
                                       seed_key=f"{seed_key}|s1|{j0}|{k}") for k, g in enumerate(grp)]
        rej = np.asarray(batch.test(ps, j=1), bool)
        any_pass += bool(rej.any())
        n_batches += 1
    fwer = any_pass / n_batches if n_batches else 0.0
    fwer_ub = cp_upper(any_pass, n_batches) if n_batches else 0.0
    ok = ub <= 0.05 + CP_SLACK and fwer_ub <= 0.10 + CP_SLACK
    m = {"families": float(n), "carry_rate": rate, "carry_ub90": ub, "cells": float(n_cells),
         "cells_carried": float(carried_cells), "confirm_batches": float(n_batches), "any_pass_rate": fwer,
         "any_pass_ub90": fwer_ub}
    thr = ("L2 family carry CP90 UB <= 0.07; P(any pass | all null) = FDR (all null) CP90 UB <= 0.12; "
           "forward e-process crossings and shuffled-regime parts BLOCKED")
    return SelfTestResult("S1", bool(ok), m, thr, hh, ("forward e-process: Lane 5A", "shuffled regime: no regime code"))


# ------------------------------------------------------------------ S2 label shift
def s2_label_shift(*, plants_r: Sequence[float] = (0.05, 0.10, 0.20), reps: int, seed_key: str,
                   fast: bool = False) -> SelfTestResult:
    """S2: plant +d R net in 10 of ~300 cells (1h/4h only) with a null 15m neighbour; detection within +/-10 pp of
    the power table (`stats.power_at`), the 15m neighbour not carried in >= 90 % of runs, 90 % intervals cover the
    plant 85-95 %. Needs Lane 3 hier and stats.power_at."""
    hh = harness_hash()
    hier, stats = _lane("goldbot.research.eval2.hier"), _lane("goldbot.research.catalogue.stats")
    if hier is None or not hasattr(stats, "power_at") or not hasattr(stats, "cell_day_means"):
        return _blocked("S2", "Lane 3 eval2.hier / stats.power_at / stats.cell_day_means not landed", hh)
    return s2_run(plants_r=plants_r, reps=reps, seed_key=seed_key, hier=hier, stats=stats, hh=hh,
                  n_fam=6 if fast else 30)


def _synthetic_null_cells(rng: np.random.Generator, n_fam: int, *, sd: float = 1.3, days: int = 600,
                          per_day: float = 1.5) -> pd.DataFrame:
    """~10 cells per family (gold 15m/30m/1h/4h/1d + 4h/1d of 2-3 others): instance-level null excess with sd 1.3 R
    (the pooled explore sd) on session days, as a units frame."""
    tfs = (("XAUUSD", "15m"), ("XAUUSD", "30m"), ("XAUUSD", "1h"), ("XAUUSD", "4h"), ("XAUUSD", "1d"),
           ("XAGUSD", "1h"), ("XAGUSD", "4h"), ("EURUSD", "4h"), ("US500", "4h"), ("US500", "1d"))
    rows = []
    for f in range(n_fam):
        fam = f"S2F{f:03d}"
        for inst, tf in tfs:
            n = int(rng.poisson(per_day * days * (0.25 if tf in ("4h", "1d") else 1.0))) + 50
            day = np.sort(rng.integers(0, days, n))
            ex = rng.normal(0, sd, n)
            rows.append(pd.DataFrame({"cell_id": f"{fam}|{inst}|{tf}", "family": fam, "inst": inst, "tf": tf,
                                      "side": 1, "day": day, "decision_ns": day * DAY, "net_r": ex,
                                      "twin_mean_net_r": 0.0, "excess_r": ex, "cost_r": 0.0,
                                      "year": 2010 + day // 250}))
    return pd.concat(rows, ignore_index=True)


def s2_run(*, plants_r: Sequence[float], reps: int, seed_key: str, hier: Any, stats: Any, hh: str,
           n_fam: int = 30) -> SelfTestResult:
    spec = hier.HierSpec()
    m: dict[str, float] = {}
    ok = True
    for d in plants_r:
        det = expected = n15 = cover = n_pl = 0.0
        for r in range(reps):
            rng = np.random.default_rng(int.from_bytes(hashlib.sha256(f"{seed_key}|{d}|{r}".encode()).digest()[:8],
                                                       "big"))
            u = _synthetic_null_cells(rng, n_fam)
            planted_fams = [f"S2F{k:03d}" for k in range(min(5, n_fam))]
            plant = u["family"].isin(planted_fams) & u["tf"].isin(("1h", "4h")) & (u["inst"] == "XAUUSD")
            u.loc[plant, ["excess_r", "net_r"]] += d
            cells = stats.cell_day_means(u, col="excess_r").merge(
                u.groupby("cell_id")[["family", "inst", "tf"]].first().reset_index(), on="cell_id")
            planted_ids = set(u.loc[plant, "cell_id"])
            for fam in planted_fams:
                prior = hier.fit_prior(cells, leave_out_family=fam, spec=spec)
                fc = cells[cells["family"] == fam]
                posts = hier.posteriors(fc, prior, spec=spec, deciding_cost_r={c: 0.0 for c in fc["cell_id"]})
                dec = hier.l2_decide(posts, family=fam, spec=spec)
                for p in posts:
                    if p.cell_id in planted_ids:
                        n_pl += 1
                        det += p.cell_id in dec.carried
                        cu = u[u["cell_id"] == p.cell_id]
                        sd = float(cu["excess_r"].std(ddof=1))
                        expected += stats.power_at(n=len(cu), sd=sd, delta=d, alpha=0.20)
                        cover += abs(p.mean - d) <= 1.645 * p.sd
                    elif p.cell_id.endswith("|XAUUSD|15m"):
                        n15 += p.cell_id not in dec.carried
        rate, pw = det / max(n_pl, 1), expected / max(n_pl, 1)
        not15 = n15 / max(reps * len(planted_fams), 1)
        cov = cover / max(n_pl, 1)
        m.update({f"detect_{d}": rate, f"power_{d}": pw, f"not15_{d}": not15, f"cover90_{d}": cov})
        ok = ok and abs(rate - pw) <= 0.10 and not15 >= 0.90 and 0.85 <= cov <= 0.95
    thr = "detection within +/-10 pp of power_at; 15m neighbour not carried >= 90 %; 90 % interval cover 85-95 %"
    return SelfTestResult("S2", bool(ok), m, thr, hh)


# ------------------------------------------------------------------ S3 full path
def rw_bars(tf: str, n: int, seed: int, *, inst: str = "XAUUSD", daily_vol: float = 0.01, sub: int = 64,
            jumps: Mapping[int, float] | None = None) -> Bars:
    """A driftless (martingale) gold-like path, weekdays only, `sub` path points per bar (high/low/close are real).
    `jumps` {bar k: amount}: the path jumps by `amount` (price units) right after bar k's opening point and stays there
    (S3's plant: with k the fill bar, the jump follows the fill at the open before any exit can happen)."""
    rng = np.random.default_rng(seed)
    step = TF_NS[tf]
    t0 = int(pd.Timestamp("2012-01-02", tz="UTC").value)
    t: list[int] = []
    x = t0
    while len(t) < n:
        if pd.Timestamp(x, unit="ns").weekday() < 5:
            t.append(x)
        x += step
    ta = np.asarray(t, np.int64) + step
    sig = daily_vol * math.sqrt(step / DAY)
    s = sig / math.sqrt(sub)
    steps = rng.normal(0, s, (n, sub)) - 0.5 * s * s
    flat = 1500.0 * np.exp(np.cumsum(steps.ravel()))
    for k, amt in (jumps or {}).items():
        if 0 <= k < n:
            flat[k * sub + 1:] += amt
    path = flat.reshape(n, sub)
    b = Bars(inst, tf, np.asarray(t, np.int64), ta, path[:, 0], path.max(axis=1), path.min(axis=1), path[:, -1],
             np.full(n, np.nan), np.zeros(n, bool), np.zeros(n, np.int32), np.full(n, 100.0))
    b.meta.update(dec_from=int(ta[50]), end=int(ta[-1]) + 1)
    return b


def plant_jumps(ev: Events, a: np.ndarray, g_atr: float) -> dict[int, float]:
    """The synthetic bar pattern: each signal's path jumps by side x g_atr x ATR14(i) right after its fill bar's
    opening point (decision bar i, market fill at bar i + 1's open), inside that bar, and stays there. After the fill
    the price is a martingale plus that jump and no exit can come before it, so by optional stopping the signal's
    expected gross is exactly +g_atr R (risk = stop_d = ATR14(i); g_atr < 1 never jumps through the 1 ATR stop or the
    2 ATR target). The twins (after the exit, off the fire bars) see no jump of their own.

    The first S3 run jumped at bar i + 2's open instead: trades that exited inside the fill bar (about 16 %) missed it,
    and the harness measured +0.168 R (SE 0.005) against a nominal +0.20 R; the plant, not the harness, was short."""
    out: dict[int, float] = {}
    for i, s in zip(ev.i, ev.side):
        if np.isfinite(a[i]):
            out[int(i) + 1] = out.get(int(i) + 1, 0.0) + int(s) * g_atr * float(a[i])
    return out


def _s3_rep(args: tuple[float, int, str]) -> dict[str, float]:
    g, rep, seed_key = args
    seed = int.from_bytes(hashlib.sha256(f"{seed_key}|s3|{rep}".encode()).digest()[:4], "big")
    n_bars = int(S3_N * 26)                          # one signal per ~26 bars: n about S3_N after the busy rule
    b0 = rw_bars("1h", n_bars, seed)
    a = K.atr(b0, 14)
    rng = np.random.default_rng(seed + 1)
    eb = EB()
    last = -100
    for j in range(60, n_bars - 40):
        if j - last >= 20 and rng.random() < 0.16 and np.isfinite(a[j]):
            eb.add(j, 1 if rng.random() < 0.5 else -1, stop_d=a[j], tgt_d=2 * a[j], hold=10)
            last = j
    ev = eb.build()
    b = rw_bars("1h", n_bars, seed, jumps=plant_jumps(ev, a, g)) if g else b0
    # stop/target distances are fixed at the decision bar (ATR14 before the jump), as the detector would set them
    df = cell_units(b, ev, seed_key=f"{seed_key}|{rep}", costs=default_model(), family="S3")
    e, t, se, n = cluster_t(df["excess_r"].to_numpy(float), df["day"].to_numpy(np.int64))
    return {"g": g, "rep": rep, "n": n, "excess": e, "t": t, "se": se, "detected": float(t >= Z_DETECT)}


def s3_full_path(*, reps: int, seed_key: str, fast: bool = False, workers: int = 4) -> SelfTestResult:
    """S3 harness half (runs now): a planted +0.20 R pattern and a planted 0 on gold-1h-shaped walks (n about 1,000
    signals each, 20 twins, costs) through sim. Pass: the planted cell is detected (one-sided day-clustered t >=
    1.96, one-sided 2.5 %, on mean excess) in >= 80 % of reps (CP 90 % lower bound >= 0.78 in the full run), the null in <= 5 % (CP 90 %
    UB <= 7 %), and the pooled planted excess within 3 SE of +0.20 R (the harness transmits the plant without bias).
    The L2 half (detection by hier), the 15m cost-separation plant and the gate-only plant need Lane 3: BLOCKED parts.
    Fast (CI): 4 planted + 4 null reps; the rate gates read the point rate only (4 reps cannot bound a 5 % rate)."""
    hh = harness_hash()
    r = max(1, reps)
    jobs = [(S3_PLANT_R, k, seed_key) for k in range(r)] + [(0.0, 10_000 + k, seed_key) for k in range(r)]
    res = _pool_map(_s3_rep, jobs, workers)
    pl = [x for x in res if x["g"] > 0]
    nu = [x for x in res if x["g"] == 0]
    det, fal = sum(x["detected"] for x in pl), sum(x["detected"] for x in nu)
    w = np.array([1 / x["se"] ** 2 for x in pl])
    pooled = float(np.sum(w * np.array([x["excess"] for x in pl])) / w.sum())
    pooled_se = float(1 / math.sqrt(w.sum()))
    wn = np.array([1 / x["se"] ** 2 for x in nu])
    pooled0 = float(np.sum(wn * np.array([x["excess"] for x in nu])) / wn.sum())
    pooled0_se = float(1 / math.sqrt(wn.sum()))
    bias_ok = abs(pooled - S3_PLANT_R) <= 3 * pooled_se and abs(pooled0) <= 3 * pooled0_se
    if fast:
        rate_ok = det / len(pl) >= S3_DETECT_MIN and fal / len(nu) <= 0.25
    else:
        rate_ok = cp_lower(int(det), len(pl)) >= S3_DETECT_MIN - CP_SLACK and cp_upper(int(fal), len(nu)) <= (
            S3_FALSE_MAX + CP_SLACK)
    m = {"reps": float(r), "detect_rate": det / len(pl), "false_rate": fal / len(nu),
         "detect_cp90_lo": cp_lower(int(det), len(pl)), "false_cp90_ub": cp_upper(int(fal), len(nu)),
         "planted_excess": pooled, "planted_se": pooled_se, "null_excess": pooled0, "null_se": pooled0_se,
         "mean_n": float(np.mean([x["n"] for x in res]))}
    thr = ("harness half: planted +0.20 R detected >= 80 % (CP90 LB >= 0.78), planted 0 <= 5 % (CP90 UB <= 0.07), "
           "pooled planted excess within 3 SE of +0.20 R; L2 half / cost-separation / gate-only BLOCKED on Lane 3")
    hier = _lane("goldbot.research.eval2.hier")
    notes = () if hier is not None else ("L2 detection, 15m COST-BOUND plant and gate-only plant: Lane 3 not landed",)
    _write_json("S3", {"metrics": m, "reps": res})
    return SelfTestResult("S3", bool(rate_ok and bias_ok), m, thr, hh, notes)


# ------------------------------------------------------------------ S4 leakage
def s4_leakage(*, seed_key: str) -> SelfTestResult:
    """S4: the realised-cost filter is refused by the lint (Lane 2, runs now); the S21 OCO side-0 trap probe |t| < 2
    needs Lane 3 eval2.probe (BLOCKED part); regime +1-bar shift: n/a until a card registers a regime."""
    hh = harness_hash()
    from goldbot.research.catalogue import lint as L
    cards: list[dict[str, Any]] = [{"filters": {"cost_r": "< 0.1"}}, {"filters": ["realised_cost_r < 0.1"]},
             {"entry": {"field": "slippage_usd", "max": 0.05}}, {"gate": "overshoot_usd < 0.1"}]
    refused = 0
    for c in cards:
        try:
            L.assert_decision_time(c)
        except L.RealisedFieldFilter:
            refused += 1
    clean_ok = not L.lint_card({"filters": {"deciding_cost_r": "< 0.15", "rt_over_stop": "<= 0.15"}})
    lint_ok = refused == len(cards) and clean_ok
    probe = _lane("goldbot.research.eval2.probe")
    m = {"lint_refused": float(refused), "lint_cards": float(len(cards)), "lint_clean_ok": float(clean_ok)}
    if probe is None:
        return _blocked("S4", "Lane 3 eval2.probe not landed (S21 OCO side-0 probe); lint part "
                        + ("passed" if lint_ok else "FAILED"), hh, m, ("regime +1 shift: no regime code yet",))
    t_s21 = s4_s21_probe_t(probe, seed_key=seed_key)
    m["s21_probe_t"] = t_s21
    ok = lint_ok and abs(t_s21) < 2
    return SelfTestResult("S4", bool(ok), m, "lint refuses realised filters; S21 probe |t| < 2", hh,
                          ("regime +1 shift: no regime code yet",))


def s4_s21_probe_t(probe: Any, *, seed_key: str) -> float:
    """S21-V0 stretch OCO on gold 1h explore bars: first passage at +/-1 ATR with the side-0 OCO bar counted as a
    loss, signal vs 20 twins, day-clustered t (the naive read showed 8.4)."""
    from goldbot.research.catalogue import dispatcher as D
    u = next(v for v in D.units() if str(v["unit"]).startswith("S21-V0"))
    b = explore_bars("XAUUSD", "1h")
    det = D.DETECTORS[u["detector"]]
    ev = det(b, u["params"], D.build_ctx("explore"))
    keep = (b.ta[ev.i] >= EXPLORE_FROM)
    ev = ev.subset(keep)
    fill = np.minimum(ev.i + 1, len(b) - 1)
    sides = np.where(ev.oco.astype(bool) | np.r_[False, ev.oco[:-1].astype(bool)], 0, ev.side).astype(np.int64) \
        if ev.oco is not None else ev.side.astype(np.int64)
    sig = probe.first_passage(b, fill_bars=fill, sides=sides, horizon_bars=10)
    fire = np.zeros(len(b), bool)
    fire[ev.i] = True
    exits = np.minimum(ev.i + 10, len(b) - 2)
    draws = SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key=seed_key)
    tw = np.full((len(ev), SIM.TWIN_M), np.nan)
    for r, dr in enumerate(draws):
        d = dr.decision_bars[dr.decision_bars >= 0]
        if len(d):
            tw[r, :len(d)] = probe.first_passage(b, fill_bars=np.minimum(d + 1, len(b) - 1),
                                                 sides=np.full(len(d), sides[r]), horizon_bars=10)
    day = SIM.session_day(b.t[fill].astype(np.int64))
    res = probe.probe_cell(sig.astype(float), tw, day, family="S21", cell_id="S21-V0|XAUUSD|1h")
    return float(res.excess_atr / res.se_atr) if res.se_atr > 0 else float("nan")


# ------------------------------------------------------------------ S5 execution
def s5_execution(*, broker: str, tf_band: str, replay_version: str = "r2") -> SelfTestResult:
    """S5: fill vs replay v2 bias |b| <= 0.02 R per broker and timeframe band (gold only). Needs Lane 5A (replay v2
    side table and eval2.forward). Vantage is blocked until its exit deal price is recorded."""
    hh = harness_hash()
    fwd = _lane("goldbot.research.eval2.forward")
    if fwd is None:
        return _blocked("S5", f"Lane 5A replay {replay_version} / eval2.forward not landed ({broker}, {tf_band})", hh)
    return _blocked("S5", "replay v2 bias read is wired by Lane 5A (`forward_state` reads count only after S5)", hh)


# ------------------------------------------------------------------ S6 truncation
def truncation_mismatches(b: Bars, ev: Events, idx: Sequence[int], *,
                          detector: Callable[[Bars], Events] | None = None) -> dict[str, int]:
    """For each sampled event row k in `idx` (decision bar i): bars truncated at bar i's close (the decision bar's
    open + tf) must give the same signal row, ATR14 and ATR20 at i; and a twin decided at bar d (from the full bars)
    must have the same geometry on bars truncated at d's close. Returns mismatch counts per item."""
    out = {"signal": 0, "atr14": 0, "atr20": 0, "twin": 0, "checked": 0, "twin_checked": 0, "last_bar_suppressed": 0}
    a14, a20 = K.atr(b, 14), K.atr(b, 20)
    full = ev.table()
    for k in idx:
        i = int(ev.i[k])
        bt = b.truncate(i + 1)
        out["checked"] += 1
        if detector is not None:
            want = _rows_at(full, i, full[k, 1])
            got = _rows_at(detector(bt).table(), i, full[k, 1])
            if len(got) < len(want):
                # missing on the strict prefix: a detector may need a next bar to exist (not its values); the decisive
                # check then changes every bar after i (values, not existence) and needs the same rows
                out["last_bar_suppressed"] += 1
                got = _rows_at(detector(perturb_after(b, i)).table(), i, full[k, 1])
            if got.shape != want.shape or not np.allclose(got, want, rtol=1e-12, atol=1e-12):
                out["signal"] += 1
        t14, t20 = K.atr(bt, 14)[i], K.atr(bt, 20)[i]
        out["atr14"] += not _same_float(t14, a14[i])
        out["atr20"] += not _same_float(t20, a20[i])
        d = min(len(b) - 2, i + 15)          # a twin is decided after the signal: none for a signal at the data end
        if d <= i:
            continue
        out["twin_checked"] += 1
        full_tw = SIM._twin_events(b, ev, k, d, a14, opposite=False)
        bd = b.truncate(d + 1)
        tr_tw = SIM._twin_events(bd, ev, k, d, K.atr(bd, 14), opposite=False)
        if (full_tw is None) != (tr_tw is None) or (
                full_tw is not None and tr_tw is not None
                and not np.allclose(np.nan_to_num(full_tw.table()), np.nan_to_num(tr_tw.table()), rtol=1e-12)):
            out["twin"] += 1
    return out


def _rows_at(t: np.ndarray, i: int, side: float) -> np.ndarray:
    """Every event row decided at bar i on `side` (a detector may emit several, e.g. two triangles breaking on one
    bar), NaN-safe and sorted, so the comparison is of the multiset, never of whichever row came first."""
    r = t[(t[:, 0] == i) & (t[:, 1] == side)] if len(t) else np.zeros((0, 10))
    r = np.nan_to_num(r, nan=-9e9)
    return r[np.lexsort(r.T[::-1])] if len(r) else r


def perturb_after(b: Bars, i: int, seed: int = 7) -> Bars:
    """b with every bar after i replaced by an unrelated path that starts at bar i's close (same times, same length)."""
    n = len(b)
    rng = np.random.default_rng(seed + i)
    m = n - i - 1
    out = Bars(b.inst, b.tf, b.t, b.ta, b.o.copy(), b.h.copy(), b.l.copy(), b.c.copy(), b.spr, b.co, b.seg,
               b.vol.copy())
    out.meta.update(b.meta)
    if m <= 0:
        return out
    c = float(b.c[i]) * np.exp(np.cumsum(rng.normal(0.002, 0.02, m)))
    o = np.concatenate([[float(b.c[i])], c[:-1]])
    out.o[i + 1:], out.c[i + 1:] = o, c
    out.h[i + 1:] = np.maximum(o, c) * 1.01
    out.l[i + 1:] = np.minimum(o, c) * 0.99
    out.vol[i + 1:] = rng.integers(1, 5000, m)
    return out


def _same_float(a: float, b: float) -> bool:
    return (not np.isfinite(a) and not np.isfinite(b)) or bool(np.isclose(a, b, rtol=1e-12, atol=0))


def htf_visibility_violations(lo: Bars, hi: Bars) -> int:
    """Higher-timeframe context is visible only after its close: for every lower-TF decision instant (its close), the
    as-of HTF bar has ta <= that instant, and the next HTF bar (not yet closed) is never returned."""
    q = lo.ta
    k = DA.asof_ns(q, hi.ta, np.arange(len(hi), dtype=float))
    ok = np.isfinite(k)
    kk = k[ok].astype(np.int64)
    bad = int((hi.ta[kk] > q[ok]).sum())
    nxt = kk + 1
    m = nxt < len(hi)
    bad += int((hi.ta[nxt[m]] <= q[ok][m]).sum())          # a closed later bar skipped = stale context
    return bad


def s6_truncation(*, samples: int, seed_key: str, fast: bool = False) -> SelfTestResult:
    """S6 on registered detectors: per sampled instance, the signal, ATR14/ATR20 and the twin geometry recomputed on
    truncated bars are identical; HTF context (4h/1d/1w over 1h) is visible only after its close. Fast: synthetic
    walks (no market data). Regime terciles: n/a until a card registers a regime (reported)."""
    from goldbot.research.catalogue import dispatcher as D
    hh = harness_hash()
    rng = np.random.default_rng(int.from_bytes(hashlib.sha256(seed_key.encode()).digest()[:8], "big"))
    tot: dict[str, int] = {"signal": 0, "atr14": 0, "atr20": 0, "twin": 0, "checked": 0, "twin_checked": 0,
                           "last_bar_suppressed": 0, "units_run": 0}
    units = [u for u in D.units() if not u.get("xs") and u["detector"] in D.DETECTORS]
    pick = rng.choice(len(units), size=min(len(units), 12 if fast else 60), replace=False)
    ctx = Ctx(window="explore") if fast else D.build_ctx("explore")
    if fast:
        from goldbot.research.catalogue.dispatcher import EVENT_SETS, RATES
        for name in EVENT_SETS:
            ctx.events[name] = np.zeros(0, np.int64)
        for ccy in RATES:
            ctx.aux[f"rate_{ccy}"] = (np.zeros(1, np.int64), np.zeros(1))
    for j in pick:
        u = units[int(j)]
        tf = u["timeframes"][0]
        inst = u["instruments"][0]
        if fast or (inst != "XAUUSD" and tf not in NONGOLD_TFS):
            b = rw_bars(tf if tf in TF_NS else "1h", 1500, int(j))
        else:
            try:   # bounded: detector look-backs need <= 1 year intraday, <= 4 years daily/weekly
                b = explore_bars(inst, tf, (2014, 2015, 2016, 2017) if tf in ("1d", "1w") else (2017,))
            except (FileNotFoundError, KeyError):
                continue
        det = D.DETECTORS[u["detector"]]
        try:
            ev = det(b, u["params"], ctx)
        except (KeyError, ValueError, IndexError):
            continue                              # a detector needing context the fast ctx lacks: skipped, not failed
        if not len(ev):
            continue
        idx = [int(x) for x in rng.choice(len(ev), size=min(len(ev), max(1, samples // len(pick))), replace=False)]

        def run_det(bb: Bars, det: Callable[..., Events] = det, params: dict[str, Any] = u["params"]) -> Events:
            return det(bb, params, ctx)
        r = truncation_mismatches(b, ev, idx, detector=run_det)
        tot["units_run"] += 1
        for k, v in r.items():
            tot[k] += v
    h1 = rw_bars("1h", 3000, 7) if fast else explore_bars("XAUUSD", "1h", (2016,))
    hi = DA._frame_to_bars("XAUUSD", "4h", pd.DataFrame({
        "t": pd.to_datetime(h1.t[::4], utc=True), "ta": pd.to_datetime(h1.t[::4] + 4 * HOUR, utc=True),
        "o": h1.o[::4], "h": h1.h[::4], "l": h1.l[::4], "c": h1.c[::4]}))
    htf_bad = htf_visibility_violations(h1, hi)
    ok = tot["checked"] > 0 and all(tot[k] == 0 for k in ("signal", "atr14", "atr20", "twin")) and htf_bad == 0
    m = {k: float(v) for k, v in tot.items()} | {"htf_violations": float(htf_bad)}
    return SelfTestResult("S6", bool(ok), m, "every mismatch count 0; HTF bar visible only after its close", hh,
                          ("regime terciles: n/a, no regime code in the harness",))


# ------------------------------------------------------------------ main
def run(only: Sequence[str], *, fast: bool, workers: int, seed_key: str, reps: int | None = None
        ) -> list[SelfTestResult]:
    hh = harness_hash()
    out: list[SelfTestResult] = []
    for t in only:
        t0 = time.time()
        if t == "S0":
            r = s0_harness(harness_hash=hh, seed_key=seed_key, fast=fast, workers=workers)
        elif t == "S1":
            r = s1_null_population(reps=reps or (50 if fast else 500), workers=workers, seed_key=seed_key)
        elif t == "S2":
            r = s2_label_shift(reps=reps or (20 if fast else 200), seed_key=seed_key, fast=fast)
        elif t == "S3":
            r = s3_full_path(reps=reps or (4 if fast else 100), seed_key=seed_key, fast=fast, workers=workers)
        elif t == "S4":
            r = s4_leakage(seed_key=seed_key)
        elif t == "S5":
            r = s5_execution(broker="icm", tf_band="intraday")
        else:
            r = s6_truncation(samples=200 if fast else 2_000, seed_key=seed_key, fast=fast)
        print(f"{r.test} {'PASS' if r.passed else ('BLOCKED' if r.metrics.get('blocked') else 'FAIL')} "
              f"({time.time() - t0:.0f}s) {r.threshold}", flush=True)
        print("   ", json.dumps({k: round(v, 5) for k, v in r.metrics.items()}), flush=True)
        for n in r.notes:
            print("    note:", n, flush=True)
        out.append(r)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="+", choices=TESTS, default=list(TESTS))
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--reps", type=int, default=None)
    ap.add_argument("--seed-key", default="eval2-selftest-2026-11-09")
    a = ap.parse_args(argv)
    if not 1 <= a.workers <= MAX_WORKERS:
        ap.error(f"--workers must be 1..{MAX_WORKERS}")
    res = run(a.only, fast=a.fast, workers=a.workers, seed_key=a.seed_key, reps=a.reps)
    rep = {"harness_hash": harness_hash(), "fast": a.fast, "seed_key": a.seed_key,
           "results": [{"test": r.test, "passed": r.passed, "metrics": dict(r.metrics), "threshold": r.threshold,
                        "notes": list(r.notes)} for r in res]}
    p = _write_json("report", rep)
    print("report:", p)
    return 0 if res and all(r.passed for r in res) else 1


if __name__ == "__main__":
    raise SystemExit(main())
