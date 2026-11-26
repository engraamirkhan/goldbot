"""Trading performance by asset class, and by strategy family x asset class (owner directive 2026-11-25: "trading
analysis must be broken down by instrument category (metals, indices, ETFs, commodities, fx, energy, bonds, agri,
crypto), because a strategy can perform differently on different instruments").

One figure record (`ClassFigures`), one builder over closed fills (`class_figures`) and one split flag
(`class_splits`), shared by the trades API, the lab messages and the performance report; the strategy ranking fills
the same record from its own numpy index (its clustered pass is the same CR1 estimator as `clustered_t` here). The class
of a fill comes from `asset_class_of` below only: the ONE mapping, kept out of goldbot/instruments.py (a bridge file
that must not import the research arm spec, tests/test_bridge_files.py). The registry has no ETF class (no ETF is
listed) and splits "commodities" into metal, energy and agri.

Design rules kept:
* Net only, after every cost: a fill's money is the broker's net P&L and its R that net over the loss at the stop.
* Money is never summed across currencies: a class (or family x class) with fills in two currencies shows no money
  (fills that all carry no currency are one account's own read, as the lab's, and sum in its currency).
* Voided evidence never reaches a class (PR #442 rule: "any per-class R read must go through read_obs or exclude
  exploration_label_void"; raw reads carried voided non-gold labels at -10..-16 R): a fill whose candidate has a
  voided label (aux.db `exploration_label_void`, any replay version) or is void as evidence for n
  (`evidence_exclusions`) is left out of every class figure (`class_voids`); its money stays in the callers' totals.
  No class figure here reads `exploration_obs` labels at all: R is the fill's own broker net over its stop.
* t is the mean net R over its clustered standard error (CR1, clusters as the caller gives them: the ranking's and the
  lab's (account, opportunity day)); None under 2 clusters or with no spread (never infinity).
* "no conclusion drawn from fewer than ~30 trades" (performance-analyst brief) and code review of PR #445 (M3): a
  family is flagged "split (significant)" only when its best class has a positive mean net R, its worst a negative
  one, each from at least MIN_SPLIT_FILLS fills with an R, and the CR1-clustered t of best minus worst reaches
  max(2, the Bonferroni critical t over the class pairs) (`split_test`; a null simulation in
  tests/test_asset_class_reporting.py keeps the false-flag rate near 5% or under). Descriptive: the flag changes no
  read, gate, share or order.
* A gross-only fill (no net P&L) carries no R here: nothing gross is read as net. Demo learning cohort fills (ADR
  0095, evidence `learning_cohort`) and model-gate exploration samples (ADR 0101, `explore_sample`; both
  `evidence_exclusions.NOT_EDGE_EVIDENCE`) are in no class figure in any reader; their money stays in the totals.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Hashable, Iterable, Sequence
from functools import lru_cache
from typing import NamedTuple

import numpy as np
from scipy.stats import t as student_t

from goldbot.base import FrozenRecord
from goldbot.instruments import ASSET_CLASS_ORDER, UNKNOWN_CLASS, registry

MIN_SPLIT_FILLS = 30                # per class, as the ranking's MIN_FILLS and the perf report's MIN_TRADES
SPLIT_T = 2.0                       # code review of PR #445 (M3): the best-minus-worst class difference's t floor
SPLIT_ALPHA = 0.05                  # ... and the two-sided level, Bonferroni over the class pairs
SPLIT_LABEL = "split (significant)"


def asset_class_of(name: str | None) -> str:
    """The asset class (config/instruments.yaml `asset_class`) of an instrument key, a store or broker symbol, or an
    exploration arm id: the ONE mapping every trading-performance report reads (owner directive 2026-11-25: "a strategy
    can perform differently on different instruments"). XAUUSD, XAUUSD+ and XAUUSD247 are "metal". An arm id names
    its instrument by suffix ("S0-SUGAR", "I0-XBRUSD": `params.instrument`, hashed into the id) or, a gold arm, by
    none: a suffix-less id takes its spec row's instrument (`goldbot.research.arms.arm_instruments`). A name neither
    the registry nor the arm spec knows is UNKNOWN_CLASS, never a gold default."""
    if not name:
        return UNKNOWN_CLASS
    reg = registry()
    spec = reg.resolve(name)
    if spec is None and "-" in name:
        spec = reg.resolve(name.rsplit("-", 1)[1])
    if spec is None and "-" not in name:
        key = _arm_instruments().get(name)
        spec = reg.resolve(key) if key is not None else None
    return spec.asset_class if spec is not None else UNKNOWN_CLASS


@lru_cache(maxsize=1)
def _arm_instruments() -> dict[str, str]:
    """Each arm spec row's instrument, read once per process (as `registry`); empty when the spec cannot be read (a
    suffix-less arm id is then UNKNOWN_CLASS, never guessed)."""
    from goldbot.research.arms import arm_instruments
    try:
        return arm_instruments()
    except Exception:   # noqa: BLE001 - a torn or missing spec (yaml, OSError ...) leaves the arm ids unknown
        return {}


class ClassFigures(FrozenRecord):
    """One asset class's closed fills (`family` None), or one family's in one class: net of every cost."""
    asset_class: str                        # goldbot.instruments.ASSET_CLASS_ORDER ("unknown": not in the registry)
    family: str | None = None
    n: int                                  # closed fills
    net_pnl: float | None = None            # account currency; None when currencies differ or none is known
    currency: str | None = None
    net_per_trade: float | None = None
    win_rate: float | None = None           # share of fills with net > 0 (0 is not a win)
    n_r: int = 0                            # fills with a net R
    mean_r: float | None = None
    t: float | None = None                  # mean net R / its clustered SE; None under 2 clusters or no spread


class ClassFill(NamedTuple):
    """One closed fill as the breakdown reads it."""
    family: str
    asset_class: str
    pnl: float | None                       # net, account currency; None: not known
    r: float | None                         # net R; None: not known
    currency: str | None
    cluster: Hashable                       # the clustering cell of its SE ((account, opportunity day) by default)


def class_voids(conn: sqlite3.Connection) -> frozenset[str]:
    """The order (candidate) ids kept out of every class figure: a voided label of any replay version, or void as
    evidence for n. A table an older aux.db lacks contributes none."""
    out: set[str] = set()
    try:
        out |= {str(c) for (c,) in conn.execute("SELECT DISTINCT candidate_id FROM exploration_label_void")}
    except sqlite3.OperationalError:
        pass
    try:
        out |= {str(c) for c, s in conn.execute("SELECT client_order_id, scope FROM evidence_exclusions")
                if "n" in json.loads(s)}
    except sqlite3.OperationalError:
        pass
    return frozenset(out)


def cr1_t(mean: float, se: float | None) -> float | None:
    """mean / se, None when the SE is missing or zero."""
    return round(mean / se, 2) if se is not None and se > 0 else None


def clustered_t(rs: Sequence[tuple[float, Hashable]]) -> float | None:
    """The CR1 clustered t of the mean: SE^2 = G/(G-1) x sum over clusters of (cluster sum - n_g x mean)^2 / N^2."""
    n = len(rs)
    if n == 0:
        return None
    mean = sum(r for r, _ in rs) / n
    cells: dict[Hashable, list[float]] = {}
    for r, c in rs:
        cells.setdefault(c, []).append(r)
    g = len(cells)
    if g < 2:
        return None
    dev = sum((sum(v) - len(v) * mean) ** 2 for v in cells.values())
    return cr1_t(mean, math.sqrt(g / (g - 1) * dev / n ** 2))


def _figures(cls: str, family: str | None, fills: list[ClassFill]) -> ClassFigures:
    money = [f for f in fills if f.pnl is not None]
    ccys = {f.currency for f in money}
    one = len(ccys) == 1
    net = round(sum(f.pnl for f in money if f.pnl is not None), 2) if one else None
    rs = [(f.r, f.cluster) for f in fills if f.r is not None]
    mean = sum(r for r, _ in rs) / len(rs) if rs else None
    return ClassFigures(
        asset_class=cls, family=family, n=len(fills), net_pnl=net, currency=next(iter(ccys)) if one else None,
        net_per_trade=round(net / len(money), 2) if net is not None else None,
        win_rate=round(sum((f.pnl or 0.0) > 0 for f in money) / len(money), 3) if money else None,
        n_r=len(rs), mean_r=round(mean, 3) if mean is not None else None, t=clustered_t(rs))


def class_order(cls: str) -> int:
    return ASSET_CLASS_ORDER.index(cls) if cls in ASSET_CLASS_ORDER else len(ASSET_CLASS_ORDER)


def class_figures(fills: Iterable[ClassFill], *, families: bool = True) -> list[ClassFigures]:
    """Per asset class (family None), then (with `families`) per family x class: classes in ASSET_CLASS_ORDER,
    families by name."""
    by_cls: dict[str, list[ClassFill]] = {}
    by_fam: dict[tuple[str, str], list[ClassFill]] = {}
    for f in fills:
        by_cls.setdefault(f.asset_class, []).append(f)
        if families:
            by_fam.setdefault((f.family, f.asset_class), []).append(f)
    out = [_figures(c, None, by_cls[c]) for c in sorted(by_cls, key=class_order)]
    out += [_figures(c, fam, by_fam[(fam, c)]) for fam, c in sorted(by_fam, key=lambda k: (k[0], class_order(k[1])))]
    return out


def split_test(rs: Sequence[float], classes: Sequence[Hashable], clusters: Sequence[Hashable],
               min_n: int = MIN_SPLIT_FILLS, alpha: float = SPLIT_ALPHA) -> bool:
    """One family's fills with an R (net R, asset class, cluster): "split (significant)" when, over its classes with
    `min_n` fills or more, the best class's mean net R is positive, the worst's negative, and the CR1-clustered t of
    best minus worst reaches max(2, t(1 - alpha / (2 m), G - 1)), m the class pairs (Bonferroni: the best and worst
    are picked after looking). The difference's SE takes each fill's influence ((r - mean_best) / n_best, or
    -(r - mean_worst) / n_worst) summed per cluster, so a cluster shared by both classes is one draw:
    SE^2 = G/(G-1) x sum over clusters of u_g^2."""
    r = np.asarray(rs, dtype=float)
    if r.size == 0:
        return False
    _, cls = np.unique(np.asarray([str(c) for c in classes]), return_inverse=True)
    n = np.bincount(cls)
    means = np.bincount(cls, r) / np.maximum(n, 1)
    ok = np.flatnonzero(n >= min_n)
    k = ok.size
    if k < 2:
        return False
    best, worst = ok[np.argmax(means[ok])], ok[np.argmin(means[ok])]
    if not (means[best] > 0 > means[worst]):
        return False
    sel = (cls == best) | (cls == worst)
    u = np.where(cls == best, (r - means[best]) / n[best], -(r - means[worst]) / n[worst])[sel]
    _, cell = np.unique(np.asarray([str(c) for c in clusters], dtype=object)[sel].astype(str), return_inverse=True)
    g = int(cell.max()) + 1
    if g < 2:
        return False
    se = math.sqrt(g / (g - 1) * float(np.sum(np.bincount(cell, u) ** 2)))
    if se <= 0:
        return False
    pairs = k * (k - 1) / 2
    crit = max(SPLIT_T, float(student_t.ppf(1 - alpha / (2 * pairs), g - 1)))
    return float(means[best] - means[worst]) / se >= crit


def class_splits(fills: Iterable[ClassFill], min_n: int = MIN_SPLIT_FILLS) -> list[str]:
    """The families split across classes ("split (significant)", `split_test` on their fills with an R), by name."""
    by_fam: dict[str, list[ClassFill]] = {}
    for f in fills:
        if f.r is not None:
            by_fam.setdefault(f.family, []).append(f)
    return sorted(fam for fam, fs in by_fam.items()
                  if split_test([f.r for f in fs if f.r is not None], [f.asset_class for f in fs],
                                [f.cluster for f in fs], min_n))
