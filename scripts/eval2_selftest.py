"""EVAL-2 self-tests S0-S6 (method v6 section 6; prereg 2026-11-09 sections 4.10 and 9). Run before any verdict.

    python scripts/eval2_selftest.py                 # S0..S6, full sizes, report in ~/tmp/eval2_selftest/
    python scripts/eval2_selftest.py --only S0       # one test
    python scripts/eval2_selftest.py --fast          # the CI sizes (selftest marker)
    python scripts/eval2_selftest.py --workers 4     # <= 8

Exit 1 when any test fails or is BLOCKED (a dependency lane has not landed): "S0-S6 pass, or the method is not used".
`assert_harness_sane(harness_hash)` is the gate the L2/L3 runner calls: it refuses unless the latest full (not --fast)
report of every one of S0-S4 and S6 for that harness hash passed (S5 too for forward reads).

Data: real bars only from the explore window, decisions AND every bar read in 2010-01-01..2017-12-31 (`explore_bars`:
gold 5m..1h resampled from the 2010-2017 1m files only, 4h/1d/1w cut at 2010-01-01; no warm-up bar from 2009, so no
TEST-OLD bar is touched). Nothing at or after 2018-01-01 is read: `explore_bars` refuses a bar outside the span, and the
bar cache is re-checked on every load (a stale or corrupt cache file fails closed). Market data never leaves ~/tmp.
Every null replicate (S1 arms, S3) reuses the same 2010-17 history: replicates are independent in their random draws,
not in the market path they run on (disclosed with every S1 result).

What each test needs
  S0  the harness (sim, costs, twins), the A1 cell floor (prereg section 9) and its sign-flipped skeleton route; the
      L2 half of the sentinels needs Lane 3 `eval2.hier`: S0 reads BLOCKED (never PASS) until it runs.
  S1  three null arms (NULL geometry, batch-1 signals circularly shifted by whole weeks, sign-flipped excess) through
      Lane 3 hier (L2) and confirm (control-variate net, BatchPRDS); BLOCKED until Lane 3 lands.
  S2  label-shift plants: Lane 3 hier + `stats.power_at`; BLOCKED until Lane 3 lands.
  S3  full-path plant: the harness half runs now; the L2 half needs Lane 3 hier, and the 15m cost-separation and
      gate-only plants are not built yet: S3 reads BLOCKED until both are in.
  S4  leakage: the realised-cost lint (Lane 2) runs now; the S21 OCO side-0 probe uses Lane 3 `eval2.probe`; the
      regime +1 shift is not applicable until a card registers a regime (no regime code in the harness).
  S5  execution, replay v2: Lane 5A; BLOCKED.
  S6  truncation at the decision close: per-cell and cross-sectional detectors, bars AND context (ctx.events, ctx.aux)
      cut at the decision instant, ATR14/ATR20, twin geometry and higher-timeframe visibility.

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
from goldbot.research.catalogue.costs import (  # noqa: E402
    CostModel,
    MissingCost,
    deciding_cost,
    default_model,
    window_model,
)
from goldbot.research.catalogue.data import DAY, HOUR, TF_NS, Bars  # noqa: E402

TestName = Literal["S0", "S1", "S2", "S3", "S4", "S5", "S6"]
TESTS: tuple[TestName, ...] = ("S0", "S1", "S2", "S3", "S4", "S5", "S6")
VERDICT_TESTS: tuple[TestName, ...] = ("S0", "S1", "S2", "S3", "S4", "S6")   # S5 gates forward reads only
MAX_WORKERS = 8

EXPLORE_FROM = int(pd.Timestamp("2010-01-01", tz="UTC").value)
EXPLORE_END = int(pd.Timestamp("2018-01-01", tz="UTC").value)        # exclusive: no bar closes after it
EXPLORE_YEARS = range(2010, 2018)
WEEK = 7 * DAY
# every file whose bytes can change a self-test result (code review HIGH-1); the eval2 package is globbed
HARNESS_FILES = (
    "goldbot/research/catalogue/sim.py", "goldbot/research/catalogue/costs.py", "goldbot/research/catalogue/core.py",
    "goldbot/research/catalogue/data.py", "goldbot/research/catalogue/dispatcher.py",
    "goldbot/research/catalogue/stats.py", "goldbot/research/catalogue/lint.py", "goldbot/research/catalogue/run.py",
    "goldbot/research/catalogue/specs.yaml", "goldbot/research/catalogue/detectors_catalogue.py",
    "goldbot/research/catalogue/detectors_patterns.py", "config/catalogue_costs.yaml", "scripts/eval2_selftest.py")
HARNESS_GLOBS = ("goldbot/research/eval2/*.py",)

# S0 thresholds (method section 6; the cell floor as amended by A1, prereg section 9.3)
S0_TOL_R = 0.01
S0_T_MAX = 2.0
S0_CELL_FLOOR_R = -0.05
S0_CELL_N = 300
S0_CHI2_P = 0.01
S0_CHI2_MIN_N = 30          # a cell enters the chi2 only from 30 instances (a clustered t at n 6 is not N(0, 1))
A1_COUNT_Q = 0.975          # (i) F <= the 97.5th percentile of the Poisson-binomial null
A1_SEVERE_ALPHA = 0.025     # (ii) Bonferroni t <= Phi^-1(0.025 / K)
# NULL-RAND entry rate per timeframe for S0 (registered 1-20 %, raised for n; A2 disclosure: side and bar are a hash of
# the bar time, so the null does not change, but more fire bars leave the twin pool and M_eff falls)
S0_RATE = {"5m": 0.03, "15m": 0.08, "30m": 0.10, "1h": 0.10, "4h": 0.15, "1d": 0.30, "1w": 0.50}
GOLD_TFS = ("5m", "15m", "30m", "1h", "4h", "1d", "1w")
NONGOLD_TFS = ("4h", "1d", "1w")
S0_FAST_CELLS = (("XAUUSD", "1h"),)
S0_FAST_YEARS = (2016, 2017)
NULL_HOLD = 10
# S3 (CI fast set): planted +0.20 R on 1h / 1,000 detected >= 80 %; planted 0 detected <= 5 %
S3_PLANT_R = 0.20
S3_N = 1_000
S3_DETECT_MIN = 0.80
S3_FALSE_MAX = 0.05
CP_SLACK = 0.02            # S1/S3 rates: Clopper-Pearson 90 % upper bound <= threshold + 2 pp
Z_DETECT = float(sps.norm.ppf(0.975))   # one-sided 2.5 %: a 5 % rule would sit ON the 5 % false-pass ceiling
# S1
S1_ARMS = ("null_geometry", "shifted", "sign_flip")
S1_RUN_SIZE = 10           # null families per simulated screen run: P(any family passes) is read per run
S1_MIN_SHIFT_DAYS = 20     # circular shifts are whole weeks >= 20 days (3 weeks and up)
BATCH1_FAMILIES = ("C03", "C02", "S21", "S20", "B07")   # method v6 (d): batch 1 at its explore edges


@dataclass(frozen=True, slots=True)
class SelfTestResult:
    test: TestName
    passed: bool
    metrics: Mapping[str, float]
    threshold: str
    harness_hash: str
    notes: tuple[str, ...] = field(default=())     # extra (not in the frozen signature's positional fields): reasons


class HarnessNotSane(RuntimeError):
    """The self-tests for this harness hash have not all passed: no L2/L3 verdict may be read on it."""


# ------------------------------------------------------------------ shared helpers
def out_dir() -> Path:
    d = Path(os.environ.get("EVAL2_SELFTEST_DIR", str(Path.home() / "tmp" / "eval2_selftest")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def harness_files() -> list[str]:
    """HARNESS_FILES plus every file matching HARNESS_GLOBS (Lane 3's eval2 package), repo-relative, sorted."""
    extra = sorted({p.relative_to(ROOT).as_posix() for g in HARNESS_GLOBS for p in ROOT.glob(g) if p.is_file()})
    return [*HARNESS_FILES, *extra]


def harness_hash() -> str:
    """sha256 (16 hex) of the harness files' bytes: S0-S6 results are valid for this hash only (method: once per
    harness hash). A missing file hashes as missing, so deleting one changes the hash too."""
    h = hashlib.sha256()
    for f in harness_files():
        p = ROOT / f
        h.update(f.encode())
        h.update(p.read_bytes() if p.exists() else b"<missing>")
    return h.hexdigest()[:16]


def assert_harness_sane(harness_hash: str, *, forward: bool = False, report_dir: Path | None = None
                        ) -> dict[str, str]:
    """Refuse (HarnessNotSane) unless, among the full (not --fast) reports written for `harness_hash` whose harness did
    not change during the run, the latest result of each of S0-S4 and S6 (and S5 when `forward`) passed and is not
    BLOCKED. Lane 3's L2/L3 runner calls this before any read. Returns {test: report file} on success. A report that
    does not parse counts as absent (fail closed); a later FAIL overrides an earlier PASS."""
    need = (*VERDICT_TESTS, "S5") if forward else VERDICT_TESTS
    latest: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for p in sorted((report_dir or out_dir()).glob("report_*.json")):
        try:
            doc = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if (doc.get("harness_hash") != harness_hash or doc.get("fast") is not False
                or doc.get("harness_changed") is not False):
            continue
        for r in doc.get("results", []):
            latest[str(r.get("test"))] = (p.name, r)
    problems = []
    for t in need:
        if t not in latest:
            problems.append(f"{t}: no full report for harness {harness_hash}")
            continue
        name, r = latest[t]
        metrics = r.get("metrics") or {}
        if r.get("passed") is not True or metrics.get("blocked"):
            problems.append(f"{t}: {'BLOCKED' if metrics.get('blocked') else 'FAIL'} in {name}")
    if problems:
        raise HarnessNotSane("EVAL-2 self-tests not all passed for this harness: " + "; ".join(problems))
    return {t: latest[t][0] for t in need}


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


def poisson_binomial_pmf(p: Sequence[float]) -> np.ndarray:
    """pmf of the number of successes among independent Bernoulli(p_c): exact convolution."""
    pmf = np.zeros(len(p) + 1)
    pmf[0] = 1.0
    for k, pc in enumerate(p, start=1):
        pmf[1:k + 1] = pmf[1:k + 1] * (1 - pc) + pmf[:k] * pc
        pmf[0] *= 1 - pc
    return pmf


def _key_rng(*key: object) -> np.random.Generator:
    return np.random.default_rng(int.from_bytes(hashlib.sha256("|".join(map(str, key)).encode()).digest()[:8], "big"))


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
    """Real bars of one cell, every bar inside 2010-01-01..2017-12-31 (no warm-up from 2009). Gold 1m-1h from the
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
    if inst == "XAUUSD" and tf == "1m":
        parts = []
        for y in yrs:
            m = DA._gold_minutes(range(y, y + 1))
            if len(m):
                parts.append(pd.DataFrame({"t": m["t"], "ta": m["t"] + pd.Timedelta(minutes=1), "o": m["open"],
                                           "h": m["high"], "l": m["low"], "c": m["close"], "spr": m["spr"],
                                           "vol": m["vol"], "src": "dukascopy_1m:XAUUSD"}))
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
            columns=["t", "ta", "o", "h", "l", "c", "spr", "vol", "src"])
    elif inst == "XAUUSD" and tf in DA.INTRADAY and tf != "4h":
        parts = [DA._resample_minutes(m, tf) for y in yrs if len(m := DA._gold_minutes(range(y, y + 1)))]
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
            columns=["t", "ta", "o", "h", "l", "c", "spr", "vol", "src"])
    elif tf == "1w":
        df = DA._weekly(DA._stored(inst, "1d"))
    else:
        df = DA._stored(inst, tf)
    if years:
        yr_of = pd.to_datetime(df["t"], utc=True).dt.year
        df = df[yr_of.isin(yrs).to_numpy()].reset_index(drop=True)
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
    u = _key_rng(seed_key, b.inst, b.tf).random((len(j), 2))
    eb = EB()
    for jj, (p, s) in zip(j, u):
        if p < rate:
            eb.add(int(jj), 1 if s < 0.5 else -1, stop_d=a[jj], tgt_d=2 * a[jj], hold=NULL_HOLD)
    return eb.build()


def instance_deciding_cost(b: Bars, *, side: int, fill: float, atr_i: float, risk: float, t_fill: int, t_exit: int,
                           stop_exit: bool, stop_entry: bool, costs: CostModel) -> float:
    """Lane 2's deciding cost (R, 1.0x) of one instance; NaN where Lane 2 has no pin (non-gold instruments, 1w):
    the caller then charges the sim's cost and records the source (never NaN -> 0)."""
    if not (np.isfinite(atr_i) and atr_i > 0 and np.isfinite(risk) and risk > 0):
        return float("nan")
    try:
        return float(deciding_cost(b.inst, b.tf, side=side, price=fill, atr_decision=atr_i, stop_atr=risk / atr_i,
                                   t_fill_ns=t_fill, t_exit_ns=t_exit, stop_exit_share=float(stop_exit),
                                   stop_entry_share=float(stop_entry), window="explore", costs=costs).r)
    except (MissingCost, ValueError, KeyError):
        return float("nan")


def cell_units(b: Bars, ev: Events, *, seed_key: str, costs: CostModel, family: str,
               fire: np.ndarray | None = None, fine: Bars | None = None, deciding: bool = False,
               cell_tag: str | None = None) -> pd.DataFrame:
    """EVAL-2 instances of one cell through the real harness path (run.unit_twins without the registry): `simulate`
    signals, `draw_twins` off the family's fire bars, `simulate_twins` with 20 twins, session-day clustering. Net and
    excess are at the sim's cost for signal and twins alike; `deciding` adds Lane 2's deciding cost per instance
    (`deciding_cost_r`, the L2 gate's cost; the sim's cost where Lane 2 has no pin, `dc_source` says which).
    `ambiguous`: the exit bar touched both the stop and the target (the sim takes the stop: method A1 report)."""
    tr = SIM.simulate(b, ev, control="random_side", seed_key=seed_key, costs=costs, fine=fine)
    cols = ["cell_id", "family", "inst", "tf", "side", "day", "decision_ns", "net_r", "twin_mean_net_r", "excess_r",
            "cost_r", "year", "n_twins", "gross_r", "ambiguous", "hold_days", "deciding_cost_r", "dc_source"]
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
    tw = SIM.simulate_twins(b, ev, [draws[k] for k in ks], costs=costs, fine=fine)
    gross = tr[:, COL["gross"]]
    net = gross - tr[:, COL["cost"]]
    ex = SIM.excess_r(net, tw)
    day = SIM.session_day(tr[:, COL["t_fill"]].astype(np.int64))
    amb = np.zeros(len(tr), bool)
    dc = np.full(len(tr), np.nan)
    a14 = K.atr(b, SIM.TWIN_ATR_N)
    for r, (row, k) in enumerate(zip(tr, ks)):
        side, x, fill = int(row[COL["side"]]), int(row[COL["x"]]), float(row[COL["fill"]])
        stop, tgt = SIM._levels(ev, k, fill)
        if int(row[COL["kind"]]) == 1 and np.isfinite(tgt) and not b.co[x]:
            amb[r] = bool((b.l[x] <= stop and b.h[x] >= tgt) if side > 0 else (b.h[x] >= stop and b.l[x] <= tgt))
        if deciding:
            i = int(row[COL["i"]])
            dc[r] = instance_deciding_cost(b, side=side, fill=fill, atr_i=float(a14[i]), risk=float(row[COL["risk"]]),
                                           t_fill=int(row[COL["t_fill"]]), t_exit=int(row[COL["t_exit"]]),
                                           stop_exit=int(row[COL["kind"]]) == 1, stop_entry=int(ev.kind[k]) == 1,
                                           costs=costs)
    src = np.where(np.isfinite(dc), "deciding", "sim")
    dc = np.where(np.isfinite(dc), dc, tr[:, COL["cost"]])
    from goldbot.research.catalogue import stats as S
    return pd.DataFrame({
        "cell_id": f"{family}|{cell_tag or f'{b.inst}|{b.tf}'}", "family": family, "inst": b.inst, "tf": b.tf,
        "side": tr[:, COL["side"]].astype(np.int64), "day": day, "decision_ns": tr[:, COL["t_dec"]].astype(np.int64),
        "net_r": net, "twin_mean_net_r": net - ex, "excess_r": ex, "cost_r": tr[:, COL["cost"]],
        "year": S.year_of_day(day), "n_twins": np.isfinite(tw).sum(axis=1), "gross_r": gross, "ambiguous": amb,
        "hold_days": (tr[:, COL["t_exit"]] - tr[:, COL["t_fill"]]) / DAY, "deciding_cost_r": dc, "dc_source": src})


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


def _write_json(name: str, obj: Any) -> Path:
    p = out_dir() / f"{name}_{time.strftime('%Y%m%dT%H%M%S')}_{os.getpid()}.json"
    p.write_text(json.dumps(obj, default=_jsonable, indent=1))
    return p


def _jsonable(x: Any) -> Any:
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, pd.DataFrame):
        return f"<frame {len(x)} rows>"
    return str(x)


# ------------------------------------------------------------------ L2 on Lane 3 (shared by S0 sentinels, S1-S3)
def cell_config_hash(cell_id: str) -> str:
    """The synthetic card's config hash of one cell (prereg 4.5: "de-duplicated by config hash"). Every self-test cell
    id is `<family>|<cell config>` with a family name unique to its replicate or plant, so the hash is distinct per
    (family, cell) and stable across runs: de-duplication never collapses two replicates, and leave-family-out drops
    exactly the held-out family's cells (an S1 arm B replicate is one whole family, all its units and cells)."""
    return hashlib.sha256(f"eval2-selftest|{cell_id}".encode()).hexdigest()[:16]


def cell_frame(units: pd.DataFrame, hier: Any, *, control: bool) -> pd.DataFrame:
    """Lane 3's L2 `cells` frame (`hier.cell_table(units, window="explore")`) for these units, with what the caller
    adds from the card (hier docstring): config_hash per cell and the control flag the Efron null reads; no overlaps
    (synthetic families share no instances)."""
    u = units[np.isfinite(units["excess_r"].to_numpy(float))]
    c = hier.cell_table(u, window="explore")
    c["config_hash"] = [cell_config_hash(str(x)) for x in c["cell_id"]]
    c["control"] = control
    return c


def deciding_cost_map(units: pd.DataFrame) -> dict[str, float]:
    """cell_id -> mean deciding cost (R, 1.0x) of the cell's instances; a missing cost raises (never NaN -> 0)."""
    col = "deciding_cost_r" if "deciding_cost_r" in units.columns else "cost_r"
    m = units.groupby("cell_id")[col].mean()
    bad = [str(k) for k, v in m.items() if not np.isfinite(v)]
    if bad:
        raise ValueError(f"no deciding cost for cells {bad[:3]}: refusing to read L2 at a cost of 0")
    return {str(k): float(v) for k, v in m.items()}


def l2_spec(hier: Any, population: pd.DataFrame) -> Any:
    """The frozen L2 spec for one self-test population (hier quant M6: posteriors and l2_decide refuse a spec without
    this code's hash and a prior population hash): prereg section 1 defaults, code_hash = `hier.code_hash()`, and
    prior_population_hash = sha256 over the population's sorted cell config hashes (what the card freezes)."""
    h = hashlib.sha256("|".join(sorted(population["config_hash"].astype(str))).encode()).hexdigest()
    return hier.HierSpec(prior_population_hash=h, code_hash=hier.code_hash())


def l2_family(units: pd.DataFrame, pop_cells: pd.DataFrame, *, family: str, hier: Any, control: bool = False) -> Any:
    """Lane 3's L2 on one family: cell_table -> fit_prior(population incl. the family; Lane 3 leaves it out) ->
    posteriors at the cells' deciding cost -> l2_decide, under the spec frozen on that population."""
    u = units[np.isfinite(units["excess_r"].to_numpy(float))]
    cells = cell_frame(u, hier, control=control)
    pop = pd.concat([pop_cells, cells], ignore_index=True)
    spec = l2_spec(hier, pop)
    prior = hier.fit_prior(pop, leave_out_family=family, spec=spec)
    posts = hier.posteriors(cells, prior, spec=spec, deciding_cost_r=deciding_cost_map(u))
    return hier.l2_decide(posts, family=family, spec=spec)


# ------------------------------------------------------------------ S0 harness
def registered_null_cells() -> set[tuple[str, str]]:
    """The registered NULL-RAND control cells (specs.yaml NULL-RAND-<tf> units x dispatcher.cells): S0's gate set."""
    from goldbot.research.catalogue import dispatcher as D
    return {c for u in D.units() if u["detector"] == "NULL_RAND" for c in D.cells(u)}


def registered_null_rates() -> dict[str, float]:
    from goldbot.research.catalogue import dispatcher as D
    return {u["timeframes"][0]: float(u["params"]["rate"]) for u in D.units() if u["detector"] == "NULL_RAND"}


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
        _, inv = np.unique(day[ok], return_inverse=True)
        ss, ts = np.bincount(inv, weights=sig[ok]), np.bincount(inv, weights=tw[ok])
        out["rho"] = float(np.corrcoef(ss, ts)[0, 1]) if ss.std() > 0 and ts.std() > 0 else float("nan")
    cells: list[dict[str, Any]] = []
    for cid, c in u.groupby("cell_id", sort=True):
        d = c["day"].to_numpy(np.int64)
        cg, ctg, csg, cn = cluster_t(c["gross_r"].to_numpy(float), d)
        ce, cte, cse, cne = cluster_t(c["excess_r"].to_numpy(float), d)
        cells.append({"cell": cid, "tf": str(c["tf"].iloc[0]), "n": cn, "gross": cg, "t_gross": ctg,
                      "se_gross": csg, "excess": ce, "t_excess": cte, "se_excess": cse, "n_excess": cne,
                      "m_eff": float(c.loc[c["n_twins"] > 0, "n_twins"].mean()),
                      "ambiguous_share": float(np.mean(c["ambiguous"].to_numpy(bool))) if "ambiguous" in c else
                      float("nan")})
    out["cells"] = cells
    t_ok = [c for c in cells if np.isfinite(c["t_excess"]) and c["n_excess"] >= S0_CHI2_MIN_N]
    chi = float(sum(c["t_excess"] ** 2 for c in t_ok))
    out["chi2"], out["chi2_df"] = chi, len(t_ok)
    out["chi2_p"] = float(sps.chi2.sf(chi, len(t_ok))) if t_ok else float("nan")
    tg_ok = [c for c in cells if np.isfinite(c["t_gross"]) and c["n"] >= S0_CHI2_MIN_N]
    chig = float(sum(c["t_gross"] ** 2 for c in tg_ok))
    out["chi2_gross_p"] = float(sps.chi2.sf(chig, len(tg_ok))) if tg_ok else float("nan")
    # the frozen (literal) floor, kept in every report (prereg 9.4)
    out["floor_fail"] = [c["cell"] for c in cells if c["n"] >= S0_CELL_N and c["gross"] < S0_CELL_FLOOR_R]
    out["a1_gross"] = a1_floor(cells, "gross")
    out["a1_excess"] = a1_floor(cells, "excess")
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


def a1_floor(cells: Sequence[Mapping[str, Any]], stat: Literal["gross", "excess"]) -> dict[str, Any]:
    """Amendment A1 (prereg 9.3) on one statistic over the cells with n >= 300 on it (gross: all instances; excess:
    instances with a twin), K of them. (i) F = #cells below -0.05 R must be <= F_crit, the smallest f with
    P(F <= f) >= 0.975 under the Poisson-binomial with p_c = Phi(-0.05 / se_c) from this run's own SEs; (ii) no cell
    below -0.05 R with t <= Phi^-1(0.025 / K). Reports F, F_crit, P(F >= observed), the severe and the failing cells."""
    nkey, se_key, t_key = ("n", "se_gross", "t_gross") if stat == "gross" else ("n_excess", "se_excess", "t_excess")
    c = [x for x in cells if x[nkey] >= S0_CELL_N and np.isfinite(x[se_key]) and x[se_key] > 0
         and np.isfinite(x[stat])]
    k = len(c)
    if not k:
        return {"K": 0, "F": 0, "F_crit": 0, "p_ge_obs": 1.0, "t_crit": float("nan"), "failing": [], "severe": [],
                "pass": True}
    p = [float(sps.norm.cdf(S0_CELL_FLOOR_R / x[se_key])) for x in c]
    cdf = np.cumsum(poisson_binomial_pmf(p))
    f_obs = sum(1 for x in c if x[stat] < S0_CELL_FLOOR_R)
    f_crit = int(np.argmax(cdf >= A1_COUNT_Q - 1e-12))
    p_ge = float(1.0 - cdf[f_obs - 1]) if f_obs > 0 else 1.0
    t_crit = float(sps.norm.ppf(A1_SEVERE_ALPHA / k))
    failing = [x["cell"] for x in c if x[stat] < S0_CELL_FLOOR_R]
    severe = [x["cell"] for x in c if x[stat] < S0_CELL_FLOOR_R and x[t_key] <= t_crit]
    return {"K": k, "F": f_obs, "F_crit": f_crit, "p_ge_obs": p_ge, "t_crit": t_crit, "failing": failing,
            "severe": severe, "pass": bool(f_obs <= f_crit and not severe)}


def sign_flip_skeleton(b: Bars, *, seed_key: str) -> Bars:
    """A1 (iv): the real bar times, gaps, close-only flags, segments, spreads and range path, with the sign of each
    bar's excursion about its open (and of each open-to-previous-close gap) flipped by independent fair coins keyed on
    `seed_key`. Magnitudes are the market's, signs are coin flips independent of them: a martingale, so the NULL gross
    on it is 0 whatever the market's structure (mean reversion, trends)."""
    n = len(b)
    rng = _key_rng("skeleton", seed_key, b.inst, b.tf)
    s_bar = rng.choice(np.array([-1.0, 1.0]), n)
    s_gap = rng.choice(np.array([-1.0, 1.0]), n)
    gap = np.zeros(n)
    gap[1:] = b.o[1:] - b.c[:-1]
    body, up, dn = b.c - b.o, b.h - b.o, b.o - b.l
    step = s_gap * gap + s_bar * body
    c = float(b.o[0]) + np.cumsum(step)
    o = c - s_bar * body
    h = np.where(s_bar > 0, o + up, o + dn)
    lo = np.where(s_bar > 0, o - dn, o - up)
    if n and float(np.min(lo)) <= 0:
        raise ValueError(f"{b.inst} {b.tf}: the skeleton path crossed zero (seed {seed_key})")
    out = Bars(b.inst, b.tf, b.t, b.ta, o, h, lo, c, b.spr, b.co, b.seg, b.vol)
    out.meta.update(b.meta)
    return out


def ou_bars(n: int, seed: int, *, kappa: float = 0.15, sd_bar: float = 1.0, sub: int = 32) -> Bars:
    """A strongly mean-reverting gold-1h-shaped path (Ornstein-Uhlenbeck at the 1-3 ATR scale): a 1:2 random-side
    bracket loses on it, so its NULL gross is significantly negative with no harness bias."""
    from scipy.signal import lfilter
    rng = np.random.default_rng(seed)
    s = sd_bar / math.sqrt(sub)
    x = np.asarray(lfilter([1.0], [1.0, -(1.0 - kappa / sub)], rng.normal(0, s, n * sub)), float)
    path = (1500.0 + x).reshape(n, sub)
    b0 = rw_bars("1h", n, seed)
    b = Bars("XAUUSD", "1h", b0.t, b0.ta, path[:, 0], path.max(axis=1), path.min(axis=1), path[:, -1],
             np.full(n, np.nan), np.zeros(n, bool), np.zeros(n, np.int32), np.full(n, 100.0))
    b.meta.update(b0.meta)
    return b


def null_gross_t(b: Bars, *, rate: float, seed_key: str) -> tuple[float, float, int]:
    """(mean gross, day-clustered t, n) of the NULL-RAND geometry on bars `b` (no twins: the gross only)."""
    ev = random_entries(b, rate=rate, seed_key=seed_key)
    tr = SIM.simulate(b, ev, control="random_side", seed_key=seed_key, costs=default_model())
    if not len(tr):
        return float("nan"), float("nan"), 0
    m, t, _, n = cluster_t(tr[:, COL["gross"]], SIM.session_day(tr[:, COL["t_fill"]].astype(np.int64)))
    return m, t, n


def skeleton_route_selfcheck(*, seed_key: str = "a1-route-check", n: int = 40_000) -> dict[str, float]:
    """The sign-flipped skeleton route is only relied on once it is tested (research-director condition): on a
    mean-reverting path the NULL gross must be significantly negative (the check has power) and on its skeleton within
    |t| < 2 (the flip removes market structure). Run on every S0 that would rely on the route; tests run it too."""
    b = ou_bars(n, 11)
    g, t, k = null_gross_t(b, rate=0.10, seed_key=seed_key)
    gs, ts, ks = null_gross_t(sign_flip_skeleton(b, seed_key=seed_key), rate=0.10, seed_key=seed_key)
    return {"ou_gross": g, "ou_t": t, "ou_n": float(k), "skeleton_gross": gs, "skeleton_t": ts,
            "skeleton_n": float(ks), "tested": float(t <= -2.0 and abs(ts) < 2.0)}


def skeleton_route(cells: Sequence[str], *, seed_key: str, n_by_cell: Mapping[str, int] | None = None
                   ) -> dict[str, Any]:
    """A1 (iv) on the gross-failing cells: NULL-RAND on each cell's sign-flipped skeleton (same S0 rate), then A1 (i)
    and (ii) on gross over those cells and the pooled |t| < 2. `tested` comes from skeleton_route_selfcheck: a pass
    through an untested route reads FAIL."""
    check = skeleton_route_selfcheck()
    per: list[dict[str, Any]] = []
    gross, days = [], []
    for k, cid in enumerate(cells):
        _, inst, tf = cid.split("|")
        b = sign_flip_skeleton(explore_bars(inst, tf), seed_key=seed_key)
        ev = random_entries(b, rate=S0_RATE[tf], seed_key="")
        tr = SIM.simulate(b, ev, control="random_side", seed_key=seed_key, costs=explore_costs())
        g = tr[:, COL["gross"]]
        d = SIM.session_day(tr[:, COL["t_fill"]].astype(np.int64))
        m, t, se, n = cluster_t(g, d)
        per.append({"cell": cid, "n": n, "gross": m, "t_gross": t, "se_gross": se, "excess": float("nan"),
                    "t_excess": float("nan"), "se_excess": float("nan"), "n_excess": 0})
        gross.append(g)
        days.append(d + 10_000_000 * k)
    a1 = a1_floor(per, "gross")
    pooled = cluster_t(np.concatenate(gross), np.concatenate(days)) if gross else (np.nan, np.nan, np.nan, 0)
    ok = bool(a1["pass"] and np.isfinite(pooled[1]) and abs(pooled[1]) < S0_T_MAX)
    return {"cells": per, "a1": a1, "pooled_gross": pooled[0], "pooled_t": pooled[1], "selfcheck": check,
            "tested": bool(check["tested"]), "pass": bool(ok and check["tested"])}


def s0_sentinels() -> tuple[dict[str, Any], dict[str, tuple[Bars, Events, CostModel]]]:
    """Buy-every-close (NULL_LONGCLOSE on gold 1d 2010-17) and a drift-walk long-every-close must fail E9 (drift is
    not edge); returned with their (bars, events, costs) for the L2 half."""
    from goldbot.research.catalogue import dispatcher as D
    from goldbot.research.catalogue import stats as S
    out: dict[str, Any] = {}
    cases: dict[str, tuple[Bars, Events, CostModel]] = {}
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
                     "E9": bool(gates.get("E9", False)), "explore_pass": S.explore_pass(gates), "L2_carried": None}
        cases[name] = (bars, ev, costs)
    return out, cases


def s0_sentinels_l2(cases: Mapping[str, tuple[Bars, Events, CostModel]], null_frames: Sequence[pd.DataFrame],
                    hier: Any, *, seed_key: str) -> dict[str, bool]:
    """The L2 half of the sentinels: each sentinel family through Lane 3's L2 with the S0 NULL-RAND cells as the
    control population; True = carried (a sentinel must never carry)."""
    pop = pd.concat([cell_frame(f, hier, control=True) for f in null_frames if len(f)], ignore_index=True)
    out = {}
    for name, (b, ev, costs) in cases.items():
        fam = f"SENTINEL-{name}"
        u = cell_units(b, ev, seed_key=f"{seed_key}|{name}", costs=costs, family=fam, deciding=True)
        out[name] = bool(l2_family(u, pop, family=fam, hier=hier).carried)
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


def s0_decide(m: Mapping[str, Any], *, sentinels_e9: Sequence[bool], route: Mapping[str, Any] | None,
              sentinel_l2: Mapping[str, bool] | None) -> tuple[str, list[str]]:
    """S0's reading: "PASS", "FAIL" or "BLOCKED" with reasons. Core: pooled |gross|, |excess| <= 0.01 R and |t| < 2,
    chi2 p > 0.01, both sentinels fail E9, A1 on excess, and A1 on gross OR (gross-only failure) the tested skeleton
    route. A core pass still reads BLOCKED until the L2 half of the sentinels has run (research-director condition)."""
    why = []
    if not (abs(m["gross"]) <= S0_TOL_R and abs(m["t_gross"]) < S0_T_MAX):
        why.append("pooled gross")
    if not (abs(m["excess"]) <= S0_TOL_R and abs(m["t_excess"]) < S0_T_MAX):
        why.append("pooled excess")
    if not m["chi2_p"] > S0_CHI2_P:
        why.append("chi2 dispersion")
    if any(sentinels_e9):
        why.append("a sentinel passed E9")
    if not m["a1_excess"]["pass"]:
        why.append("A1 on excess (no exception)")
    if not m["a1_gross"]["pass"]:
        if not m["a1_excess"]["pass"]:
            why.append("A1 on gross")
        elif route is None:
            why.append("A1 on gross: skeleton route not run")
        elif not route["tested"]:
            why.append("A1 on gross: skeleton route untested (selfcheck failed): reads FAIL")
        elif not route["pass"]:
            why.append("A1 on gross: skeleton route failed")
    if why:
        return "FAIL", why
    if sentinel_l2 is None:
        return "BLOCKED", ["L2 half of the sentinels not run (Lane 3 eval2.hier)"]
    carried = [k for k, v in sentinel_l2.items() if v]
    return ("FAIL", [f"sentinel carried at L2: {carried}"]) if carried else ("PASS", [])


def s0_harness(*, harness_hash: str, seed_key: str, fast: bool = False, workers: int = 4) -> SelfTestResult:
    """S0 on real explore bars: NULL-RAND controls per timeframe with 20 twins, the A1 cell floor (prereg 9.3) on gross
    and excess, the sentinels (E9; L2 half on Lane 3). The literal floor and every A1 field are reported. Fast (CI):
    one gold 1h cell, 2016-17, |t| < 3 only."""
    t0 = time.time()
    res = _pool_map(_s0_cell, [(i, tf, seed_key, fast) for i, tf in s0_cells(fast)], workers)
    skipped = {f"{r['inst']}|{r['tf']}": r["skipped"] for r in res if "skipped" in r}
    reg = registered_null_cells()
    frames = [r["units"] for r in res if "units" in r and (fast or (r["inst"], r["tf"]) in reg)]
    ext = [r["units"] for r in res if "units" in r and not fast and (r["inst"], r["tf"]) not in reg]
    m = s0_metrics(frames)
    mx = s0_metrics(frames + ext) if ext else {}
    rates = registered_null_rates()
    notes = [f"skipped {k}: {v}" for k, v in skipped.items()]
    notes.append("A2 disclosure: S0 entry rates " + ", ".join(f"{tf} {S0_RATE[tf]:.2f} (registered "
                 f"{rates.get(tf, float('nan')):.2f})" for tf in GOLD_TFS) + "; the 1w twin span (8 bars) is the A2 "
                 "rule, not in the method; M_eff ceilings 1h <= 15, 4h <= 5, 1w <= 8")
    notes.append(f"literal floor (method v6, kept for the record): {m['floor_fail'] or 'none'}")
    for stat in ("gross", "excess"):
        a = m[f"a1_{stat}"]
        notes.append(f"A1 {stat}: K {a['K']}, F {a['F']}, F_crit {a['F_crit']}, P(F >= obs) {a['p_ge_obs']:.3f}, "
                     f"t_crit {a['t_crit']:.2f}, below floor {a['failing'] or 'none'}, severe {a['severe'] or 'none'}"
                     f": {'pass' if a['pass'] else 'FAIL'}")
    if mx:
        notes.append(f"all {len(frames) + len(ext)} cells (registered + {len(ext)} non-registered, reported, not "
                     f"gated): pooled gross {mx['gross']:+.4f} (t {mx['t_gross']:+.2f}), excess {mx['excess']:+.4f} "
                     f"(t {mx['t_excess']:+.2f}), chi2 p {mx['chi2_p']:.3f}; A1 gross F {mx['a1_gross']['F']}/"
                     f"{mx['a1_gross']['F_crit']} severe {mx['a1_gross']['severe']}, A1 excess F "
                     f"{mx['a1_excess']['F']}/{mx['a1_excess']['F_crit']} severe {mx['a1_excess']['severe']}")
    sent: dict[str, Any] = {}
    route: dict[str, Any] | None = None
    if fast:
        status = "PASS" if abs(m["t_gross"]) < 3 and abs(m["t_excess"]) < 3 else "FAIL"
        thr = "fast: one gold 1h cell 2016-17, |t gross| < 3 and |t excess| < 3"
    else:
        sent, cases = s0_sentinels()
        if not m["a1_gross"]["pass"] and m["a1_excess"]["pass"]:
            route = skeleton_route(sorted(set(m["a1_gross"]["failing"]) | set(m["a1_gross"]["severe"])),
                                   seed_key=seed_key)
            notes.append(f"A1 (iv) skeleton route: tested {route['tested']}, A1 {route['a1']}, pooled t "
                         f"{route['pooled_t']:.2f}: {'pass' if route['pass'] else 'FAIL'}")
        hier = _lane("goldbot.research.eval2.hier")
        status, why = s0_decide(m, sentinels_e9=[v["E9"] for v in sent.values()], route=route, sentinel_l2=None)
        if status == "BLOCKED" and hier is not None:
            l2 = s0_sentinels_l2(cases, frames, hier, seed_key=seed_key)
            for k, v in l2.items():
                sent[k]["L2_carried"] = v
            status, why = s0_decide(m, sentinels_e9=[v["E9"] for v in sent.values()], route=route, sentinel_l2=l2)
        notes.extend(f"{status}: {w}" for w in why)
        thr = ("pooled |gross|, |excess| <= 0.01 R and |t| < 2; chi2 p > 0.01; A1 (prereg 9.3) on gross and excess "
               "(gross-only failure: tested skeleton route); sentinels fail E9 and never carry at L2")
    metrics: dict[str, float] = {k: float(v) for k, v in m.items() if isinstance(v, (int, float))}
    metrics["cells_run"] = float(len(frames))
    metrics["literal_floor_fail_n"] = float(len(m["floor_fail"]))
    for stat in ("gross", "excess"):
        a = m[f"a1_{stat}"]
        for k in ("K", "F", "F_crit", "p_ge_obs", "t_crit"):
            metrics[f"a1_{stat}_{k}"] = float(a[k])
        metrics[f"a1_{stat}_severe_n"] = float(len(a["severe"]))
        metrics[f"a1_{stat}_pass"] = float(a["pass"])
    metrics["secs"] = round(time.time() - t0, 1)
    for k, v in sent.items():
        metrics[f"sentinel_{k}_E9"] = float(v["E9"])
        metrics[f"sentinel_{k}_gross"] = v["gross"]
    if status == "BLOCKED":
        metrics["blocked"] = 1.0
        thr = "BLOCKED: L2 half of the sentinels (Lane 3); " + thr
    _write_json("S0", {"metrics": m, "extension": mx, "sentinels": sent, "skipped": skipped, "route": route,
                       "status": status,
                       "bars": {f"{r['inst']}|{r['tf']}": [r.get("bars"), r.get("first"), r.get("last_close"),
                                                          r.get("secs")] for r in res if "units" in r}})
    return SelfTestResult("S0", status == "PASS", metrics, thr, harness_hash, tuple(notes))


# ------------------------------------------------------------------ per-bar NULL table (S1 arms A and C)
def _null_event(d: int, side: int, a: float) -> Events:
    return Events(i=np.array([d], np.int64), side=np.array([side], np.int8), kind=np.zeros(1, np.int8),
                  px=np.array([np.nan]), valid=np.ones(1, np.int64), stop=np.array([np.nan]), stop_d=np.array([a]),
                  tgt=np.array([np.nan]), tgt_d=np.array([2 * a]), hold=np.array([NULL_HOLD], np.int64),
                  oco=np.zeros(1, np.int8))


def null_trade(b: Bars, d: int, side: int, a14d: float, a20: np.ndarray, cm: CostModel
               ) -> tuple[float, float, int, int, int, float] | None:
    """One NULL-geometry market order decided at bar d, by `sim`'s single-order rules (`order_risk`, `_fill`,
    `_levels`, instant exits, `walk`, `cost_price`; the subset of `sim._replay_net` a market order takes):
    (gross R, sim cost R, exit kind, t_fill, t_exit, fill); None when no order is placed or it never fills."""
    ev = _null_event(d, side, a14d)
    risk = SIM.order_risk(b, ev, 0, a20, cm)
    if risk is None:
        return None
    fr = SIM._fill(b, d, side, 0, float("nan"), 1)
    if fr is None:
        return None
    f, fill, at_open = fr
    if int(b.t[f]) >= int(b.meta.get("end", 2**62)):
        return None
    stop, tgt = SIM._levels(ev, 0, fill)
    dist = side * (fill - stop)
    last = min(len(b) - 1, f + NULL_HOLD - 1)
    t_fill = int(b.t[f])
    instant = False
    if dist <= 0:
        w, instant = SIM.Walk(f, fill, 1, t_fill), True
    elif np.isfinite(tgt) and side * (tgt - fill) <= 0:
        w, instant = SIM.Walk(f, fill, 2, t_fill), True
    else:
        w = SIM.walk(b, f, side, fill, stop, tgt, last, None, at_open)
    gross = side * (w.exit_px - fill) / risk
    if instant and w.kind == 1:
        gross = min(-1.0, gross)
    cost = SIM.cost_price(b.inst, side, fill, float(b.spr[f]), w.kind, t_fill, w.t_exit, False, cm) / risk
    return gross, cost, w.kind, t_fill, w.t_exit, fill


def null_table(b: Bars, costs: CostModel, *, deciding: bool = False) -> dict[str, np.ndarray]:
    """Per bar d and side (+1 col 0, -1 col 1), the NULL-geometry entry decided at d: `net` (gross - sim cost: the
    sim's own net, tested against `simulate` + `simulate_twins`), `dc` (Lane 2's deciding cost, NaN where Lane 2 has
    no pin; with `deciding`), `hold_days`. A twin of a NULL-geometry signal at bar d IS the NULL entry at d (stop and
    target are ATR14 multiples at the twin's bar), so one table gives every signal's and every twin's net."""
    n = len(b)
    a14 = K.atr(b, SIM.TWIN_ATR_N)
    a20 = K.atr(b, SIM.RISK_FLOOR_ATR_N)
    net = np.full((n, 2), np.nan)
    dc = np.full((n, 2), np.nan)
    hold = np.full((n, 2), np.nan)
    for d in range(n - 1):
        if not (np.isfinite(a14[d]) and a14[d] > 0):
            continue
        for c, side in enumerate((1, -1)):
            r = null_trade(b, d, side, float(a14[d]), a20, costs)
            if r is None:
                continue
            gross, cost, kind, t_fill, t_exit, fill = r
            net[d, c] = gross - cost
            hold[d, c] = (t_exit - t_fill) / DAY
            if deciding:
                dc[d, c] = instance_deciding_cost(b, side=side, fill=fill, atr_i=float(a14[d]), risk=float(a14[d]),
                                                  t_fill=t_fill, t_exit=t_exit, stop_exit=kind == 1,
                                                  stop_entry=False, costs=costs)
            if not np.isfinite(dc[d, c]):
                dc[d, c] = cost
    return {"net": net, "dc": dc, "hold_days": hold}


def bars_digest(b: Bars) -> str:
    """sha256 (12 hex) of the bar times and closes: a cached table belongs to exactly these bars (code review LOW-2)."""
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(b.t, np.int64).tobytes())
    h.update(np.ascontiguousarray(b.c, np.float64).tobytes())
    return h.hexdigest()[:12]


# ------------------------------------------------------------------ S1 null population
S1_GRID = (("XAUUSD", "15m"), ("XAUUSD", "30m"), ("XAUUSD", "1h"), ("XAUUSD", "4h"), ("XAUUSD", "1d"),
           ("XAGUSD", "4h"), ("XAGUSD", "1d"), ("EURUSD", "4h"), ("EURUSD", "1d"), ("US500", "4h"), ("US500", "1d"))
Tables = Mapping[tuple[str, str], tuple[Bars, Mapping[str, np.ndarray]]]


def _s1_table(args: tuple[str, str]) -> tuple[str, str, dict[str, np.ndarray] | None, Bars | None]:
    inst, tf = args
    try:
        b = explore_bars(inst, tf)
    except FileNotFoundError:
        return inst, tf, None, None
    p = out_dir() / "null_tables" / f"{inst}_{tf}_{harness_hash()}_{bars_digest(b)}.npz"
    if p.exists():
        z = np.load(p)
        return inst, tf, {k: z[k] for k in ("net", "dc", "hold_days")}, b
    tab = null_table(b, explore_costs(), deciding=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    np.savez(p, **tab)  # type: ignore[arg-type]  # numpy stub: **kwds vs allow_pickle
    return inst, tf, tab, b


def null_family_units(tables: Tables, *, family: str, seed_key: str,
                      rate: Mapping[str, float] = S0_RATE) -> pd.DataFrame:
    """Arm A, one null family over the cell grid: random entries (keyed by `seed_key`), one position per cell at a time
    (a NULL trade exits at the latest at decision + hold bars, so the next entry is decided after that: slightly
    sparser than `simulate`, never overlapping), twins drawn by `draw_twins` after that latest exit and off the
    family's fire bars, every net, deciding cost and hold read from the per-bar table (`null_table`)."""
    from goldbot.research.catalogue import stats as S
    frames = []
    for (inst, tf), (b, tab) in tables.items():
        net, dc, hold = tab["net"], tab["dc"], tab["hold_days"]
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
            busy = i + NULL_HOLD
        if not keep:
            continue
        kk = np.array(keep)
        fire = np.zeros(len(b), bool)
        fire[ev.i] = True
        exits = np.full(len(ev), -1, np.int64)
        exits[kk] = np.minimum(ev.i[kk] + NULL_HOLD, len(b) - 2)   # conservative: the latest a NULL trade can exit
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
            "net_r": sig, "twin_mean_net_r": sig - ex, "excess_r": ex, "cost_r": dc[ev.i[kk], sides],
            "deciding_cost_r": dc[ev.i[kk], sides], "hold_days": hold[ev.i[kk], sides],
            "year": S.year_of_day(day)}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def sign_flip_units(units: pd.DataFrame, *, seed_key: str, family: str) -> pd.DataFrame:
    """Arm C: the excess of every instance multiplied by a fair coin per (cell, session day), keyed on `seed_key`; the
    twin mean is kept, so net' = twin mean + flipped excess. Null by construction, real dispersion and clustering."""
    u = units.copy()
    keys = u["cell_id"].astype(str) + "|" + u["day"].astype(str)
    uniq, inv = np.unique(keys.to_numpy(), return_inverse=True)
    s = _key_rng("flip", seed_key, family).choice(np.array([-1.0, 1.0]), len(uniq))[inv]
    u["excess_r"] = u["excess_r"].to_numpy(float) * s
    u["net_r"] = u["twin_mean_net_r"].to_numpy(float) + u["excess_r"].to_numpy(float)
    u["family"] = family
    u["cell_id"] = family + "|" + u["inst"].astype(str) + "|" + u["tf"].astype(str)
    return u


def _concat_events(parts: Sequence[Events], like: Events) -> Events:
    rows = [p.table() for p in parts]
    oco = [p.oco if p.oco is not None else np.zeros(len(p), np.int8) for p in parts]
    if not rows:
        return K.empty()
    a = np.vstack(rows)
    o = np.concatenate(oco).astype(np.int8)
    order = np.lexsort((-a[:, 1], a[:, 0]))
    a, o = a[order], o[order]
    return Events(i=a[:, 0].astype(np.int64), side=a[:, 1].astype(np.int8), kind=a[:, 2].astype(np.int8), px=a[:, 3],
                  valid=a[:, 4].astype(np.int64), stop=a[:, 5], stop_d=a[:, 6], tgt=a[:, 7], tgt_d=a[:, 8],
                  hold=a[:, 9].astype(np.int64), exit_long=like.exit_long, exit_short=like.exit_short, oco=o)


def shift_weeks_bound(b: Bars) -> int:
    """The largest whole-week shift drawn for bars `b`: the cell's decision span inside 2010-17 less 3 weeks."""
    lo = max(EXPLORE_FROM, int(b.ta[0])) if len(b) else EXPLORE_FROM
    hi = min(EXPLORE_END, int(b.ta[-1]) + 1) if len(b) else EXPLORE_END
    return (hi - lo) // WEEK - 3


def shift_events(b: Bars, ev: Events, *, seed_key: str, weeks: int | None = None) -> tuple[Events, int]:
    """Arm B: every signal (an OCO pair as one) re-decided `weeks` whole weeks later, circularly inside the cell's own
    bars within 2010-17 (weeks >= 3, so >= 20 days), keyed on `seed_key`; its own geometry re-scaled to the new bar's ATR14
    (`sim._twin_events`, the same re-anchoring the twins use), own kind, validity, hold and exit masks.
    Returns (events, weeks)."""
    lo = max(EXPLORE_FROM, int(b.ta[0])) if len(b) else EXPLORE_FROM
    hi = min(EXPLORE_END, int(b.ta[-1]) + 1) if len(b) else EXPLORE_END
    span = hi - lo                                      # the cell's own decision span inside 2010-17
    max_w = span // WEEK - 3
    if max_w <= 3:
        raise ValueError(f"{b.inst} {b.tf}: {span / WEEK:.1f} weeks of bars cannot hold a >= 3-week circular shift")
    if weeks is None:
        weeks = int(_key_rng("shift", seed_key, b.inst, b.tf).integers(3, max_w))
    if not 3 <= weeks < max_w + 3:
        raise ValueError(f"{b.inst} {b.tf}: a {weeks}-week shift is outside 3..{max_w + 2} weeks")
    shift = weeks * WEEK
    a14 = K.atr(b, SIM.TWIN_ATR_N)
    parts, seen = [], set()
    for k in range(len(ev)):
        sib = SIM._oco_sibling(ev, k)
        if sib is not None and sib < k:
            continue
        t = int(b.ta[int(ev.i[k])])
        if t < lo:
            continue
        target = lo + (t - lo + shift) % span
        d = int(np.searchsorted(b.ta, target))
        if d >= len(b) - 1 or d in seen:
            continue
        tev = SIM._twin_events(b, ev, k, d, a14, opposite=False)
        if tev is None:
            continue
        seen.add(d)
        parts.append(tev)
    return _concat_events(parts, ev), weeks


FamilyCells = Sequence[tuple[str, Bars, Events]]


def batch1_families(ctx: Ctx | None = None) -> dict[str, list[tuple[str, Bars, Events]]]:
    """Batch-1 family -> [(unit id, bars, events)] over every registered unit of the family (BATCH1_FAMILIES) and each
    of its gold cells, explore 2010-17 bars, real context."""
    from goldbot.research.catalogue import dispatcher as D
    ctx = ctx or D.build_ctx("explore")
    out: dict[str, list[tuple[str, Bars, Events]]] = {}
    for u in D.units():
        if u["family"] not in BATCH1_FAMILIES or u.get("xs"):
            continue
        for inst, tf in D.cells(u):
            if inst != "XAUUSD":
                continue
            b = explore_bars(inst, tf)
            ev = D.DETECTORS[u["detector"]](b, u["params"], ctx)
            if len(ev):
                out.setdefault(str(u["family"]), []).append((str(u["unit"]), b, ev))
    return out


def arm_b_family(rep: int, families: Mapping[str, Any]) -> str:
    """The batch-1 family replicate `rep` shifts: the families in name order, cycled."""
    names = sorted(families)
    return names[rep % len(names)]


def shifted_family_units(rep: int, seed_key: str, source: str, cells: FamilyCells, fine: Bars | None, *,
                         costs: CostModel | None = None) -> pd.DataFrame:
    """Arm B, one replicate (quant round 2, C1): EVERY registered unit and gold cell of ONE batch-1 family, shifted by
    the replicate's one keyed whole-week shift (>= 3 weeks, within every cell's span), all under one family id
    S1B<rep>; one cell per (unit, inst, tf), so the family's cells are the source family's cells. Twins avoid the bars
    where any shifted unit of the family fires in that (inst, tf); real simulate + 20 twins at the window's costs."""
    cm = costs or explore_costs()
    fam = f"S1B{rep:04d}"
    bound = min(shift_weeks_bound(b) for _, b, _ in cells)
    if bound <= 3:
        raise ValueError(f"{source}: the shortest cell holds {bound + 3} weeks, too few for a >= 3-week shift")
    weeks = int(_key_rng("shift", seed_key, rep, source).integers(3, bound))
    shifted = [(unit, b, shift_events(b, ev, seed_key=f"{seed_key}|{rep}", weeks=weeks)[0]) for unit, b, ev in cells]
    fire: dict[tuple[str, str], np.ndarray] = {}
    for _, b, sev in shifted:
        m = fire.setdefault((b.inst, b.tf), np.zeros(len(b), bool))
        m[sev.i[(sev.i >= 0) & (sev.i < len(m))]] = True
    frames = []
    for unit, b, sev in shifted:
        if not len(sev):
            continue
        f = cell_units(b, sev, seed_key=f"{seed_key}|{rep}|{unit}", costs=cm, family=fam,
                       fire=fire[(b.inst, b.tf)], fine=fine if b.inst == "XAUUSD" else None, deciding=True,
                       cell_tag=f"{unit}|{b.inst}|{b.tf}")
        frames.append(f)
    u = pd.concat([f for f in frames if len(f)], ignore_index=True) if any(len(f) for f in frames) else pd.DataFrame(
        columns=["cell_id", "family", "inst", "tf"])
    u["source_family"] = source
    u["shift_weeks"] = weeks
    return u


def _s1_shift_rep(args: tuple[int, str, str, FamilyCells, Bars | None]) -> pd.DataFrame:
    rep, seed_key, source, cells, fine = args
    return shifted_family_units(rep, seed_key, source, cells, fine)


def _confirm_run(fams: Sequence[pd.DataFrame], carried: Mapping[str, tuple[str, ...]], *, confirm: Any,
                 stats: Any, seed_key: str) -> bool:
    """One simulated screen run's confirm leg: the carried null families read at BatchPRDS batch 1 on the L3
    control-variate net (Lane 3 `stats.control_variate_net`, weights n / var(excess) per carried cell from the family's
    own units), p by Lane 3's day-block bootstrap at mean block = the E1 lag (max(5, ceil(1.5 x median hold days)));
    True when any family rejects (every family is null)."""
    from goldbot.research.catalogue import stats as S
    read = [(f, carried[str(f["family"].iloc[0])]) for f in fams if carried.get(str(f["family"].iloc[0]))]
    if not read:
        return False
    batch = confirm.BatchPRDS(window="test_old")
    m = len(read)
    resamples = confirm.confirm_resamples(m, batch.level(1, m))
    ps = []
    for f, cells in read:
        u = f[f["cell_id"].isin(cells) & np.isfinite(f["excess_r"].to_numpy(float))]
        g = u.groupby("cell_id")["excess_r"]
        w = {str(c): float(n / v) for c, n, v in zip(g.size().index, g.size(), g.var(ddof=1)) if v > 0}
        hold = u["hold_days"].to_numpy(float) if "hold_days" in u else np.zeros(1)
        lag = max(S.NW_LAG_MIN, int(math.ceil(S.NW_LAG_HOLD * float(np.nanmedian(hold)))))
        ps.append(confirm.boot_p_one_sided(u, lambda x, w=w: float(stats.control_variate_net(x, w)),
                                           mean_block=float(lag), resamples=resamples,
                                           seed_key=f"{seed_key}|{f['family'].iloc[0]}"))
    return bool(np.asarray(batch.test(ps, j=1), bool).any())


def s1_arm(fams: Sequence[pd.DataFrame], *, arm: str, seed_key: str, hier: Any, confirm: Any, stats: Any
           ) -> dict[str, float]:
    """L2 + confirm on one arm's null families. Families are grouped into runs of S1_RUN_SIZE (a simulated screen):
    L2 carry rate per family; P(any family passes confirm) per run (all null, so it equals the FDR)."""
    fams = [f[np.isfinite(f["excess_r"].to_numpy(float))].reset_index(drop=True) for f in fams if len(f)]
    fams = [f for f in fams if len(f)]
    cells = {str(f["family"].iloc[0]): cell_frame(f, hier, control=True) for f in fams}
    allc = pd.concat(list(cells.values()), ignore_index=True)
    carried: dict[str, tuple[str, ...]] = {}
    for f in fams:
        fam = str(f["family"].iloc[0])
        dec = l2_family(f, allc[allc["family"] != fam], family=fam, hier=hier, control=True)
        carried[fam] = tuple(dec.carried)
    n = len(fams)
    k = sum(1 for v in carried.values() if v)
    runs = [fams[j:j + S1_RUN_SIZE] for j in range(0, n - S1_RUN_SIZE + 1, S1_RUN_SIZE)]
    any_pass = sum(_confirm_run(r, carried, confirm=confirm, stats=stats, seed_key=f"{seed_key}|{arm}|{j}")
                   for j, r in enumerate(runs))
    return {"families": float(n), "carried": float(k), "carry_rate": k / n if n else float("nan"),
            "carry_ub90": cp_upper(k, n), "runs": float(len(runs)), "any_pass": float(any_pass),
            "any_pass_rate": any_pass / len(runs) if runs else float("nan"),
            "any_pass_ub90": cp_upper(int(any_pass), len(runs)),
            "pass": float(cp_upper(k, n) <= 0.05 + CP_SLACK and bool(runs)
                          and cp_upper(int(any_pass), len(runs)) <= 0.10 + CP_SLACK)}


def s1_null_population(*, reps: int = 500, workers: int = 8, seed_key: str) -> SelfTestResult:
    """S1 (prereg 9 C1 and method section 6): >= 500 null families per arm, run before L2 on any real cell.
      A null_geometry  random entries over the real cell grid with the NULL geometry and exits (per-bar table);
      B shifted        per replicate ONE batch-1 family (cycled), every registered unit and gold cell of it, all
                       circularly shifted by the replicate's one whole-week shift (>= 20 days), own geometry, exits
                       and costs, real simulate + 20 twins, under one family id;
      C sign_flip      arm A's excess with a fair coin per (cell, session day).
    Each arm: L2 family carry CP 90 % UB <= 7 %; P(any family passes confirm) per run CP 90 % UB <= 12 %. Deciding
    cost: Lane 2's per instance where pinned (gold 5m-1d), else the sim's (recorded in dc_source); never 0. The forward
    e-process and shuffled-regime parts are BLOCKED (Lane 5A, no regime code)."""
    hh = harness_hash()
    hier, confirm, stats = (_lane("goldbot.research.eval2.hier"), _lane("goldbot.research.eval2.confirm"),
                            _lane("goldbot.research.catalogue.stats"))
    if hier is None or confirm is None or not hasattr(stats, "control_variate_net"):
        return _blocked("S1", "Lane 3 eval2.hier / eval2.confirm / stats.control_variate_net not landed", hh,
                        notes=("the three null arms are built (null_table, null_family_units, shift_events, "
                               "sign_flip_units); run S1 first once Lane 3 merges, before any L2 on real cells",))
    tabs = _pool_map(_s1_table, list(S1_GRID), workers)
    tables = {(i, tf): (b, tab) for i, tf, tab, b in tabs if tab is not None and b is not None}
    b1 = batch1_families()
    fine = explore_bars("XAUUSD", "1m") if any(np.isin(ev.kind, SIM.INTRABAR).any()
                                               for cells in b1.values() for _, _, ev in cells) else None
    jobs = [(r, seed_key, arm_b_family(r, b1), b1[arm_b_family(r, b1)], fine) for r in range(reps)] if b1 else []
    shifted = _pool_map(_s1_shift_rep, jobs, workers)
    return s1_from_arms(tables, shifted, reps=reps, seed_key=seed_key, hier=hier, confirm=confirm, stats=stats, hh=hh)


def s1_from_arms(tables: Tables, shifted: Sequence[pd.DataFrame], *, reps: int, seed_key: str, hier: Any,
                 confirm: Any, stats: Any, hh: str) -> SelfTestResult:
    arm_a = [null_family_units(tables, family=f"S1A{r:04d}", seed_key=seed_key) for r in range(reps)]
    arm_c = [sign_flip_units(f, seed_key=seed_key, family=f"S1C{r:04d}") for r, f in enumerate(arm_a) if len(f)]
    res = {"null_geometry": s1_arm(arm_a, arm="A", seed_key=seed_key, hier=hier, confirm=confirm, stats=stats),
           "sign_flip": s1_arm(arm_c, arm="C", seed_key=seed_key, hier=hier, confirm=confirm, stats=stats)}
    notes = ["shared history: every replicate runs on the same 2010-17 explore path; replicates are independent in "
             "their draws, not in the market path (the CP bounds assume independent replicates)",
             "deciding cost: Lane 2 per instance on gold 5m-1d, the sim's cost on non-gold (Lane 2 has no pin there)",
             "forward e-process: Lane 5A", "shuffled regime: no regime code"]
    if shifted:
        res["shifted"] = s1_arm(shifted, arm="B", seed_key=seed_key, hier=hier, confirm=confirm, stats=stats)
    else:
        notes.append("arm B (shifted batch-1 signals): no batch-1 unit produced events: the arm is missing")
    m = {f"{arm}_{k}": v for arm, r in res.items() for k, v in r.items()}
    ok = len(res) == len(S1_ARMS) and all(r["pass"] for r in res.values()) and all(
        r["families"] >= min(reps, 500) for r in res.values())
    thr = ("per arm (NULL geometry, shifted batch-1, sign-flipped): >= 500 families, L2 carry CP90 UB <= 0.07, "
           "P(any pass | all null) per run CP90 UB <= 0.12; forward e-process and shuffled-regime parts BLOCKED")
    return SelfTestResult("S1", bool(ok), m, thr, hh, tuple(notes))


# ------------------------------------------------------------------ S2 label shift
def s2_label_shift(*, plants_r: Sequence[float] = (0.05, 0.10, 0.20), reps: int, seed_key: str,
                   fast: bool = False) -> SelfTestResult:
    """S2: plant +d R net in 10 of ~300 cells (1h/4h only) with a null 15m neighbour; detection within +/-10 pp of
    the power table (`stats.power_at`), the 15m neighbour not carried in >= 90 % of runs, 90 % intervals cover the
    plant 85-95 %. Needs Lane 3 hier and stats.power_at."""
    hh = harness_hash()
    hier, stats = _lane("goldbot.research.eval2.hier"), _lane("goldbot.research.catalogue.stats")
    if hier is None or not hasattr(stats, "power_at"):
        return _blocked("S2", "Lane 3 eval2.hier / stats.power_at not landed", hh)
    return s2_run(plants_r=plants_r, reps=reps, seed_key=seed_key, hier=hier, stats=stats, hh=hh,
                  n_fam=6 if fast else 30)


def _synthetic_null_cells(rng: np.random.Generator, n_fam: int, *, sd: float = 1.3, days: int = 600,
                          per_day: float = 1.5) -> pd.DataFrame:
    """~10 cells per family (gold 15m/30m/1h/4h/1d + 4h/1d of 2-3 others): instance-level null excess with sd 1.3 R
    (the pooled explore sd) on session days, as a units frame (deciding cost 0.10 R: a real, non-zero cost)."""
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
                                      "twin_mean_net_r": 0.0, "excess_r": ex, "cost_r": 0.10,
                                      "deciding_cost_r": 0.10, "year": 2010 + day // 250}))
    return pd.concat(rows, ignore_index=True)


def s2_run(*, plants_r: Sequence[float], reps: int, seed_key: str, hier: Any, stats: Any, hh: str,
           n_fam: int = 30) -> SelfTestResult:
    m: dict[str, float] = {}
    ok = True
    for d in plants_r:
        det = expected = n15 = cover = n_pl = 0.0
        for r in range(reps):
            u = _synthetic_null_cells(_key_rng(seed_key, d, r), n_fam)
            planted_fams = [f"S2F{k:03d}" for k in range(min(5, n_fam))]
            plant = u["family"].isin(planted_fams) & u["tf"].isin(("1h", "4h")) & (u["inst"] == "XAUUSD")
            u.loc[plant, ["excess_r", "net_r"]] += d
            cells = cell_frame(u, hier, control=False)
            spec = l2_spec(hier, cells)
            planted_ids = set(u.loc[plant, "cell_id"])
            for fam in planted_fams:
                prior = hier.fit_prior(cells, leave_out_family=fam, spec=spec)
                fc = cells[cells["family"] == fam]
                posts = hier.posteriors(fc, prior, spec=spec, deciding_cost_r=deciding_cost_map(u[u["family"] == fam]))
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


def _s3_rep(args: tuple[float, int, str]) -> dict[str, Any]:
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
    fam = f"S3P{rep:05d}" if g else f"NULL-S3{rep:05d}"
    df = cell_units(b, ev, seed_key=f"{seed_key}|{rep}", costs=default_model(), family=fam)
    e, t, se, n = cluster_t(df["excess_r"].to_numpy(float), df["day"].to_numpy(np.int64))
    return {"g": g, "rep": rep, "n": n, "excess": e, "t": t, "se": se, "detected": float(t >= Z_DETECT),
            "units": df}


def s3_l2_half(res: Sequence[Mapping[str, Any]], hier: Any) -> dict[str, float]:
    """The L2 half: every rep's cell through Lane 3's L2 (prior on all other reps' cells, the null reps as controls);
    planted carried >= 80 % (CP 90 % LB >= 0.78), null carried <= 5 % (CP 90 % UB <= 0.07)."""
    frames = {str(r["units"]["family"].iloc[0]): r["units"] for r in res if len(r["units"])}
    pop = pd.concat([cell_frame(u, hier, control=fam.startswith("NULL")) for fam, u in frames.items()],
                    ignore_index=True)
    pl = nu = cpl = cnu = 0
    for fam, u in frames.items():
        carried = bool(l2_family(u, pop[pop["family"] != fam], family=fam, hier=hier,
                                 control=fam.startswith("NULL")).carried)
        if fam.startswith("NULL"):
            nu, cnu = nu + 1, cnu + carried
        else:
            pl, cpl = pl + 1, cpl + carried
    return {"l2_detect_rate": cpl / pl if pl else float("nan"), "l2_detect_cp90_lo": cp_lower(cpl, pl),
            "l2_false_rate": cnu / nu if nu else float("nan"), "l2_false_cp90_ub": cp_upper(cnu, nu),
            "l2_pass": float(cp_lower(cpl, pl) >= S3_DETECT_MIN - CP_SLACK and cp_upper(cnu, nu)
                             <= S3_FALSE_MAX + CP_SLACK)}


def s3_full_path(*, reps: int, seed_key: str, fast: bool = False, workers: int = 4) -> SelfTestResult:
    """S3. Harness half (runs now): a planted +0.20 R pattern and a planted 0 on gold-1h-shaped walks (n about 1,000
    signals each, 20 twins, costs) through sim: detected (one-sided day-clustered t >= 1.96 on mean excess) in >= 80 %
    of reps (CP 90 % LB >= 0.78 in the full run), the null in <= 5 % (CP 90 % UB <= 7 %), pooled planted excess within
    3 SE of +0.20 R. L2 half: Lane 3 hier. The 15m cost-separation plant and the gate-only plant are not built yet.
    S3 reads BLOCKED (never PASS) while any half is missing; a failing harness half reads FAIL."""
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
    m: dict[str, float] = {
        "reps": float(r), "detect_rate": det / len(pl), "false_rate": fal / len(nu),
        "detect_cp90_lo": cp_lower(int(det), len(pl)), "false_cp90_ub": cp_upper(int(fal), len(nu)),
        "planted_excess": pooled, "planted_se": pooled_se, "null_excess": pooled0, "null_se": pooled0_se,
        "mean_n": float(np.mean([x["n"] for x in res])), "harness_half_pass": float(rate_ok and bias_ok)}
    thr = ("harness half: planted +0.20 R detected >= 80 % (CP90 LB >= 0.78), planted 0 <= 5 % (CP90 UB <= 0.07), "
           "pooled planted excess within 3 SE of +0.20 R; L2 half: same rates at L2; cost-separation and gate-only "
           "plants")
    _write_json("S3", {"metrics": m, "reps": [{k: v for k, v in x.items() if k != "units"} for x in res]})
    if not (rate_ok and bias_ok):
        return SelfTestResult("S3", False, m, thr, hh, ("harness half FAILED",))
    hier = _lane("goldbot.research.eval2.hier")
    if hier is None:
        return _blocked("S3", "Lane 3 eval2.hier not landed (L2 half); cost-separation and gate-only plants not "
                        "built; harness half passed", hh, m)
    m.update(s3_l2_half(res, hier))
    if not m["l2_pass"]:
        return SelfTestResult("S3", False, m, thr, hh, ("L2 half FAILED",))
    return _blocked("S3", "the 15m cost-separation plant and the gate-only plant are not built (Lane 4 follow-up); "
                    "harness and L2 halves passed", hh, m)


# ------------------------------------------------------------------ S4 leakage
def s4_leakage(*, seed_key: str) -> SelfTestResult:
    """S4: the realised-cost filter is refused by the lint (Lane 2, runs now); the S21 OCO side-0 trap probe |t| < 2
    uses Lane 3 eval2.probe (BLOCKED part while absent); regime +1-bar shift: n/a until a card registers a regime."""
    hh = harness_hash()
    from goldbot.research.catalogue import lint as L
    cards: list[dict[str, Any]] = [{"filters": {"cost_r": "< 0.1"}}, {"filters": ["realised_cost_r < 0.1"]},
                                   {"entry": {"field": "slippage_usd", "max": 0.05}},
                                   {"gate": "overshoot_usd < 0.1"}]
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
    pr = s4_s21_probe(probe, seed_key=seed_key)
    m.update(pr)
    ok = lint_ok and abs(pr["s21_probe_t"]) < 2
    return SelfTestResult("S4", bool(ok), m, "lint refuses realised filters; S21 probe |t| < 2", hh,
                          ("regime +1 shift: no regime code yet",))


def oco_instances(b: Bars, ev: Events) -> list[tuple[int, int, int]]:
    """One instance per order or OCO pair (`sim._oco_sibling`): (event row of the pair's first leg, fill bar, side),
    side = the leg that fills first (`sim._fill`), 0 when both legs fill on the same bar (the side-0 OCO bar);
    orders that never fill are left out."""
    out = []
    for k in range(len(ev)):
        sib = SIM._oco_sibling(ev, k)
        if sib is not None and sib < k:
            continue
        legs = [k] if sib is None else [k, sib]
        fills = [SIM._fill(b, int(ev.i[j]), int(ev.side[j]), int(ev.kind[j]), float(ev.px[j]), int(ev.valid[j]))
                 for j in legs]
        done = [(fr[0], j) for fr, j in zip(fills, legs) if fr is not None]
        if not done:
            continue
        f0 = min(f for f, _ in done)
        first = [j for f, j in done if f == f0]
        out.append((k, int(f0), 0 if len(first) > 1 else int(ev.side[first[0]])))
    return out


def probe_units(b: Bars, ev: Events, probe: Any, *, seed_key: str, horizon: int = 10
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Signal and 20-twin probe values (Lane 3 `probe.first_passage`) for each OCO-de-duplicated instance, and its
    session day. Each twin re-decides the pair at its own bar (`sim._twin_events`) and is resolved the same way, so a
    twin whose two legs fill on one bar is side 0 too."""
    inst = oco_instances(b, ev)
    if not inst:
        z = np.zeros(0)
        return z, np.zeros((0, SIM.TWIN_M)), z
    ks = np.array([k for k, _, _ in inst])
    fills = np.array([f for _, f, _ in inst])
    sides = np.array([s for _, _, s in inst])
    sig = probe.first_passage(b, fill_bars=fills, sides=sides, horizon_bars=horizon)
    fire = np.zeros(len(b), bool)
    fire[ev.i] = True
    exits = np.full(len(ev), -1, np.int64)
    exits[ks] = np.minimum(fills + horizon, len(b) - 2)
    draws = SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key=seed_key)
    a14 = K.atr(b, SIM.TWIN_ATR_N)
    tw = np.full((len(ks), SIM.TWIN_M), np.nan)
    for r, k in enumerate(ks):
        tf_, ts_, slot = [], [], []
        for c, d in enumerate(draws[int(k)].decision_bars.tolist()):
            if d < 0:
                continue
            tev = SIM._twin_events(b, ev, int(k), int(d), a14, opposite=False)
            res = oco_instances(b, tev) if tev is not None else []
            if res:
                tf_.append(res[0][1])
                ts_.append(res[0][2])
                slot.append(c)
        if slot:
            tw[r, slot] = probe.first_passage(b, fill_bars=np.array(tf_), sides=np.array(ts_), horizon_bars=horizon)
    day = SIM.session_day(b.t[fills].astype(np.int64))
    return np.asarray(sig, float), tw, day


def s4_s21_probe(probe: Any, *, seed_key: str) -> dict[str, float]:
    """S21-V0 stretch OCO on gold 1h explore bars through Lane 3's probe (first passage +/-1 ATR, side-0 OCO bar =
    loss, `probe.probe_cell` against 20 twins): |t| must be < 2 (the naive read showed 8.4)."""
    from goldbot.research.catalogue import dispatcher as D
    u = next(v for v in D.units() if str(v["unit"]).startswith("S21-V0"))
    b = explore_bars("XAUUSD", "1h")
    ev = D.DETECTORS[u["detector"]](b, u["params"], D.build_ctx("explore"))
    ev = ev.subset(b.ta[ev.i] >= EXPLORE_FROM)
    sig, tw, day = probe_units(b, ev, probe, seed_key=seed_key)
    res = probe.probe_cell(sig, tw, day, family="S21", cell_id="S21-V0|XAUUSD|1h")
    t = float(res.excess_atr / res.se_atr) if res.se_atr > 0 else float("nan")
    return {"s21_probe_t": t, "s21_probe_n": float(res.n), "s21_probe_excess_atr": float(res.excess_atr),
            "s21_side0_share": float(np.mean(sig == -1.0)) if len(sig) else float("nan")}


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
Detector = Callable[[Bars, "int | None"], Events]


def truncate_ctx(ctx: Ctx, cut: int, *, scheduled_events: bool = True) -> Ctx:
    """The context known at instant `cut`: every (available_ns, values, ...) aux series (FRED rates, long histories)
    cut at availability <= `cut`; other aux entries (the cost model) as they are. `ctx.events` holds SCHEDULED instants
    (release calendars, FOMC dates, exchange holidays) that a desk knows ahead: with `scheduled_events` they are kept
    whole (ctx carries no publication time to cut them on: data.us_events only enforces available <= event); without
    it they are cut at the instant itself (the strict reading; S05-S07 read future scheduled dates by design)."""
    out = Ctx(window=ctx.window)
    for k, v in ctx.events.items():
        a = np.asarray(v)
        if scheduled_events:
            out.events[k] = a
            continue
        at = a * DAY if k.startswith("holiday_") else a
        out.events[k] = a[at <= cut]
    for k, v in ctx.aux.items():
        if isinstance(v, tuple) and v and isinstance(v[0], np.ndarray) and v[0].dtype.kind in "iu":
            keep = v[0] <= cut
            out.aux[k] = tuple(np.asarray(x)[keep] if isinstance(x, np.ndarray) and len(x) == len(v[0]) else x
                               for x in v)
        else:
            out.aux[k] = v
    return out


def truncation_mismatches(b: Bars, ev: Events, idx: Sequence[int], *, detector: Detector | None = None
                          ) -> dict[str, int]:
    """For each sampled event row k in `idx` (decision bar i): bars cut at bar i's close (the decision bar's open +
    tf) with the context cut at that instant (`detector(bars, cut)`; None = the full context) must give the same
    signal rows, ATR14 and ATR20 at i; a twin decided at bar d must have the same geometry on bars cut at d's close.
    Returns mismatch counts per item."""
    out = {"signal": 0, "atr14": 0, "atr20": 0, "twin": 0, "checked": 0, "twin_checked": 0, "last_bar_suppressed": 0}
    a14, a20 = K.atr(b, 14), K.atr(b, 20)
    full = ev.table()
    for k in idx:
        i = int(ev.i[k])
        bt = b.truncate(i + 1)
        cut = int(b.ta[i])
        out["checked"] += 1
        if detector is not None:
            want = _rows_at(full, i, full[k, 1])
            got = _rows_at(detector(bt, cut).table(), i, full[k, 1])
            if len(got) < len(want):
                # missing on the strict prefix: a detector may need a next bar to exist (not its values); the decisive
                # check then changes every bar after i (values, not existence) and needs the same rows
                out["last_bar_suppressed"] += 1
                got = _rows_at(detector(perturb_after(b, i), cut).table(), i, full[k, 1])
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


def xs_truncation_mismatches(bs: Mapping[str, Bars], fn: Callable[[dict[str, Bars], Ctx], dict[str, Events]],
                             ctx: Ctx, picks: Sequence[tuple[str, int]]) -> dict[str, int]:
    """Cross-sectional detectors: for each (instrument, event row) pick, EVERY instrument's bars and the context are
    cut at that event's decision instant; the instrument's rows at its decision bar must be identical. Missing on the
    strict cut: every instrument's bars after the instant are replaced instead (values, not existence)."""
    out = {"xs_signal": 0, "xs_checked": 0, "xs_last_bar_suppressed": 0, "xs_skipped": 0}
    full = fn(dict(bs), ctx)
    for inst, k in picks:
        b = bs[inst]
        t = full[inst].table()
        i = int(t[k, 0])
        cut = int(b.ta[i])
        out["xs_checked"] += 1
        want = _rows_at(t, i, t[k, 1])
        cut_bs = {s: x.truncate(int(np.searchsorted(x.ta, cut, "right"))) for s, x in bs.items()}
        if any(len(x) == 0 for x in cut_bs.values()):
            out["xs_skipped"] += 1               # an instrument with no bar yet: the book would change, not compared
            out["xs_checked"] -= 1
            continue
        got = _rows_at(fn(cut_bs, truncate_ctx(ctx, cut)).get(inst, K.empty()).table(), i, t[k, 1])
        if len(got) < len(want):
            out["xs_last_bar_suppressed"] += 1
            pert = {s: perturb_after(x, int(np.searchsorted(x.ta, cut, "right")) - 1) for s, x in bs.items()}
            got = _rows_at(fn(pert, truncate_ctx(ctx, cut)).get(inst, K.empty()).table(), i, t[k, 1])
        if got.shape != want.shape or not np.allclose(got, want, rtol=1e-12, atol=1e-12):
            out["xs_signal"] += 1
    return out


def _rows_at(t: np.ndarray, i: int, side: float) -> np.ndarray:
    """Every event row decided at bar i on `side` (a detector may emit several, e.g. two triangles breaking on one
    bar), NaN-safe and sorted, so the comparison is of the multiset, never of whichever row came first."""
    r = t[(t[:, 0] == i) & (t[:, 1] == side)] if len(t) else np.zeros((0, 10))
    r = np.nan_to_num(r, nan=-9e9)
    return r[np.lexsort(r.T[::-1])] if len(r) else r


def perturb_after(b: Bars, i: int, seed: int = 7) -> Bars:
    """b with every bar after i replaced by an unrelated path that starts at bar i's close (same times, same length):
    prices, volume, spreads, close-only flags and source segments all change after i."""
    n = len(b)
    rng = np.random.default_rng(seed + max(i, 0))
    m = n - i - 1
    out = Bars(b.inst, b.tf, b.t, b.ta, b.o.copy(), b.h.copy(), b.l.copy(), b.c.copy(), b.spr.copy(), b.co.copy(),
               b.seg.copy(), b.vol.copy())
    out.meta.update(b.meta)
    if m <= 0 or i < 0:
        return out
    c = float(b.c[i]) * np.exp(np.cumsum(rng.normal(0.002, 0.02, m)))
    o = np.concatenate([[float(b.c[i])], c[:-1]])
    out.o[i + 1:], out.c[i + 1:] = o, c
    out.h[i + 1:] = np.maximum(o, c) * 1.01
    out.l[i + 1:] = np.minimum(o, c) * 0.99
    out.vol[i + 1:] = rng.integers(1, 5000, m)
    out.spr[i + 1:] = np.abs(rng.normal(0.5, 0.3, m))
    out.co[i + 1:] = rng.random(m) < 0.05
    out.seg[i + 1:] = int(b.seg[i]) + 1 + (rng.random(m) < 0.01).cumsum()
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


def _fast_ctx() -> Ctx:
    from goldbot.research.catalogue.dispatcher import EVENT_SETS, RATES
    ctx = Ctx(window="explore")
    for name in EVENT_SETS:
        ctx.events[name] = np.zeros(0, np.int64)
    for ccy in RATES:
        ctx.aux[f"rate_{ccy}"] = (np.zeros(1, np.int64), np.zeros(1))
    return ctx


def s6_truncation(*, samples: int, seed_key: str, fast: bool = False) -> SelfTestResult:
    """S6 on registered detectors: per sampled instance, the signal (bars and context cut at the decision close),
    ATR14/ATR20 and the twin geometry are identical on truncated inputs; cross-sectional units cut every instrument at
    the decision instant; HTF context (4h over 1h) is visible only after its close. Fast: synthetic walks (no market
    data). Regime terciles: n/a until a card registers a regime (reported). Detectors behind the last-bar-suppressed
    events (decided only once a next bar exists: an engine port would fire them a bar late) are listed."""
    from goldbot.research.catalogue import dispatcher as D
    hh = harness_hash()
    rng = _key_rng(seed_key)
    tot: dict[str, int] = {"signal": 0, "atr14": 0, "atr20": 0, "twin": 0, "checked": 0, "twin_checked": 0,
                           "last_bar_suppressed": 0, "units_run": 0, "xs_signal": 0, "xs_checked": 0,
                           "xs_last_bar_suppressed": 0, "xs_skipped": 0, "xs_units_run": 0, "units_skipped": 0}
    suppressed: dict[str, int] = {}
    units = [u for u in D.units() if not u.get("xs") and u["detector"] in D.DETECTORS]
    pick = rng.choice(len(units), size=min(len(units), 12 if fast else 60), replace=False)
    ctx = _fast_ctx() if fast else D.build_ctx("explore")
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
                tot["units_skipped"] += 1
                continue
        det = D.DETECTORS[u["detector"]]
        try:
            ev = det(b, u["params"], ctx)
        except (KeyError, ValueError, IndexError):
            tot["units_skipped"] += 1              # a detector needing context the fast ctx lacks: skipped, not failed
            continue
        if not len(ev):
            continue
        idx = [int(x) for x in rng.choice(len(ev), size=min(len(ev), max(1, samples // len(pick))), replace=False)]

        def run_det(bb: Bars, cut: int | None, det: Callable[..., Events] = det,
                    params: dict[str, Any] = u["params"]) -> Events:
            return det(bb, params, ctx if cut is None else truncate_ctx(ctx, cut))
        r = truncation_mismatches(b, ev, idx, detector=run_det)
        tot["units_run"] += 1
        if r["last_bar_suppressed"]:
            suppressed[f"{u['detector']}|{tf}"] = suppressed.get(f"{u['detector']}|{tf}", 0) + r["last_bar_suppressed"]
        for k, v in r.items():
            tot[k] += v
    xs_units = [u for u in D.units() if u.get("xs") and u["detector"] in D.XS_DETECTORS]
    for j in rng.choice(len(xs_units), size=min(len(xs_units), 3 if fast else 8), replace=False) if xs_units else []:
        u = xs_units[int(j)]
        tf = u["timeframes"][0]
        bs: dict[str, Bars] = {}
        for s, inst in enumerate(u["instruments"]):
            try:
                bs[inst] = rw_bars(tf, 800, 100 + s, inst=inst) if fast else explore_bars(
                    inst, tf, (2014, 2015, 2016, 2017))
            except (FileNotFoundError, KeyError):
                continue
        bs = {k: v for k, v in bs.items() if len(v)}
        if len(bs) < 2:
            tot["units_skipped"] += 1
            continue
        t_start = max(int(v.ta[0]) for v in bs.values())     # every instrument has bars from here on
        xfn = D.XS_DETECTORS[u["detector"]]

        def run_xs(bb: dict[str, Bars], c: Ctx, xfn: Callable[..., dict[str, Events]] = xfn,
                   params: dict[str, Any] = u["params"]) -> dict[str, Events]:
            return xfn(bb, params, c)
        try:
            full = run_xs(dict(bs), ctx)
        except (KeyError, ValueError, IndexError):
            tot["units_skipped"] += 1
            continue
        picks = []
        for inst, e in full.items():
            elig = np.flatnonzero(bs[inst].ta[e.i] >= t_start) if len(e) and inst in bs else np.zeros(0, np.int64)
            picks += [(inst, int(k)) for k in rng.choice(elig, size=min(len(elig), 3), replace=False)] if len(elig) else []
        if not picks:
            continue
        r2 = xs_truncation_mismatches(bs, run_xs, ctx, picks)
        tot["xs_units_run"] += 1
        if r2["xs_last_bar_suppressed"]:
            suppressed[f"{u['detector']}|{tf}"] = suppressed.get(f"{u['detector']}|{tf}", 0) + r2[
                "xs_last_bar_suppressed"]
        for k, v in r2.items():
            tot[k] += v
    h1 = rw_bars("1h", 3000, 7) if fast else explore_bars("XAUUSD", "1h", (2016,))
    hi = DA._frame_to_bars("XAUUSD", "4h", pd.DataFrame({
        "t": pd.to_datetime(h1.t[::4], utc=True), "ta": pd.to_datetime(h1.t[::4] + 4 * HOUR, utc=True),
        "o": h1.o[::4], "h": h1.h[::4], "l": h1.l[::4], "c": h1.c[::4]}))
    htf_bad = htf_visibility_violations(h1, hi)
    ok = (tot["checked"] > 0 and all(tot[k] == 0 for k in ("signal", "atr14", "atr20", "twin", "xs_signal"))
          and htf_bad == 0)
    m = {k: float(v) for k, v in tot.items()} | {"htf_violations": float(htf_bad)}
    notes = ["regime terciles: n/a, no regime code in the harness",
             "last-bar-suppressed (decided only once a next bar exists) by detector|tf: "
             + (", ".join(f"{k} {v}" for k, v in sorted(suppressed.items())) or "none")]
    _write_json("S6", {"metrics": m, "last_bar_suppressed_by_detector": suppressed})
    return SelfTestResult("S6", bool(ok), m, "every mismatch count 0 (bars and context cut at the decision close; "
                          "cross-sectional cut at the decision instant); HTF bar visible only after its close", hh,
                          tuple(notes))


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
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--only", nargs="+", choices=TESTS, default=list(TESTS))
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--reps", type=int, default=None)
    ap.add_argument("--seed-key", default="eval2-selftest-2026-11-09")
    a = ap.parse_args(argv)
    if not 1 <= a.workers <= MAX_WORKERS:
        ap.error(f"--workers must be 1..{MAX_WORKERS}")
    h0 = harness_hash()
    res = run(a.only, fast=a.fast, workers=a.workers, seed_key=a.seed_key, reps=a.reps)
    h1 = harness_hash()
    rep = {"harness_hash": h0, "harness_hash_end": h1, "harness_changed": h0 != h1, "fast": a.fast,
           "seed_key": a.seed_key,
           "results": [{"test": r.test, "passed": r.passed, "metrics": dict(r.metrics), "threshold": r.threshold,
                        "notes": list(r.notes)} for r in res]}
    p = _write_json("report", rep)
    print("report:", p, "(harness changed during the run: not usable)" if h0 != h1 else "")
    return 0 if res and all(r.passed for r in res) and h0 == h1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
