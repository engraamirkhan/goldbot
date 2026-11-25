"""Trading performance by asset class, and by strategy family x asset class (owner directive 2026-11-25: "trading
analysis must be broken down by instrument category (metals, indices, ETFs, commodities, fx, energy, bonds, agri,
crypto), because a strategy can perform differently on different instruments").

One figure record (`ClassFigures`), one builder over closed fills (`class_figures`) and one split flag
(`class_splits`), shared by the trades API, the lab messages and the performance report; the strategy ranking fills
the same record from its own numpy index (its clustered pass is the same CR1 estimator as `_cr1_t` here). The class
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
* "no conclusion drawn from fewer than ~30 trades" (performance-analyst brief): a family is flagged as split across
  classes only when it has a positive mean net R in one class and a negative one in another, each from at least
  MIN_SPLIT_FILLS fills with an R. Descriptive: the flag changes no read, gate, share or order.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Hashable, Iterable
from functools import lru_cache
from typing import NamedTuple

from goldbot.base import FrozenRecord
from goldbot.instruments import ASSET_CLASS_ORDER, UNKNOWN_CLASS, registry

MIN_SPLIT_FILLS = 30                # per class, as the ranking's MIN_FILLS and the perf report's MIN_TRADES


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


def _cr1_t(rs: list[tuple[float, Hashable]]) -> float | None:
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
        n_r=len(rs), mean_r=round(mean, 3) if mean is not None else None, t=_cr1_t(rs))


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


def splits(classes: Iterable[tuple[int, float | None]], min_n: int = MIN_SPLIT_FILLS) -> bool:
    """One family's classes as (fills with an R, mean net R): positive in one class and negative in another, each from
    `min_n` fills with an R."""
    signs = {math.copysign(1, m) for n, m in classes if m is not None and m != 0 and n >= min_n}
    return len(signs) == 2


def is_split(figs: Iterable[ClassFigures], min_n: int = MIN_SPLIT_FILLS) -> bool:
    """`splits` over one family's ClassFigures."""
    return splits(((f.n_r, f.mean_r) for f in figs), min_n)


def class_splits(figs: Iterable[ClassFigures], min_n: int = MIN_SPLIT_FILLS) -> list[str]:
    """The families (family x class rows; class totals are ignored) that are split across classes, by name."""
    by_fam: dict[str, list[ClassFigures]] = {}
    for f in figs:
        if f.family is not None:
            by_fam.setdefault(f.family, []).append(f)
    return sorted(fam for fam, fs in by_fam.items() if is_split(fs, min_n))
