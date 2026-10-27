"""The strategy ranking (docs/design/trades-ux-2026-10-27.md §7, backlog 136; ADR 0053): one builder for
`GET /api/strategies/ranking`, the Strategies tab's ranked view, the morning Telegram report and the weekly digest, so
the four never disagree. Pure: rows in, a RankingView out; nothing here reads a file, writes, sizes or orders.

Design rules kept (ADR 0053 with the quant reviewer's binding conditions):
* "Row = strategy family, pooled over its timeframes and accounts, current rule version only": exploration arms by
  family and rule version (config/strategy_labels.yaml `version`; a rule edit is a new arm id and config_hash, so "v2"
  starts at n 0), engine strategies (demo and real-money positions) by family. Shadow models and cost probes are never
  ranked; random controls are the yardstick, in their own band; families stopped by their rule (H-61 stop, or a read
  that retired them) sit in the retired band.
* "Sort by the Bonferroni-adjusted lower bound of net R per trade after costs": `mean - t(1 - 0.025/K, G-1) x SE`, K the
  rows ranked in this ranking, SE clustered by (account, opportunity day of the close), G those clusters. The
  unadjusted 95% CI (t, G-1 df) is shown beside it. Ties by larger n, then name.
* "A family needs at least 30 fills on at least 10 opportunity days to be ranked": below that it is greyed, unranked,
  listed after the ranked rows by n, and says why.
* "early, not proven" (status `early`) until the read: exploration rows take the recorded verdict of their read
  unit(s) (config/arm_reads.yaml), engine strategies stay early (no per-strategy gate pass is recorded).
* Edge vs random: the evaluator's recorded `vs_control_r` once the row's single read unit is read (source "read",
  its 90% interval); before that net R/trade minus the paired control's (ADR 0051 pairing, per arm; a row is the
  fill-weighted mean of its arms), labelled "interim".
* The ranking is descriptive and never changes trading: reads and gates use their pre-registered rule and the
  deflated Sharpe with the real K; promotion reads only the recorded verdict. tests/test_strategy_ranking.py fails CI
  when any module outside RANKING_READERS imports this one.
Only closed fills with an R count (net of every cost); a fill voided as evidence (quarantine) is left out, as in the
read's n. Money stays in the account currency and is never summed across currencies.
"""
from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

from goldbot.api.schema import (
    AccountChoice,
    RankBand,
    RankFigures,
    RankingView,
    RankingWindow,
    RankProgress,
    RankRow,
    RankStatus,
    RankVariant,
    RecentFigures,
    TradeRow,
)
from goldbot.api.strategy_labels import Catalog
from goldbot.data.timeutil import ROLLOVER_UTC_H
from goldbot.ops.report_days import day_bounds, report_day
from goldbot.telegram.labs import _TF_STYLE

__all__ = ["ALPHA", "HEADING", "MIN_DAYS", "MIN_FILLS", "RANKING_READERS", "WINDOW_DAYS", "Ranker", "rank",
           "rank_moves"]

ALPHA = 0.025                       # one-sided, shared by the K ranked rows (Bonferroni: each gets ALPHA / K)
CI = 0.95                           # the unadjusted interval shown beside the key
MIN_FILLS = 30                      # quant reviewer, binding: fewer fills cannot be ranked
MIN_DAYS = 10                       # ... nor fewer opportunity days (a clustered CI needs the clusters)
FLAT_R = 0.01                       # 7 d and 30 d within this of each other: "flat"
HEADING = ("Ranked by the least each trade is likely earning after costs. Early results move a lot; nothing is "
           "proven before its read.")
WINDOW_DAYS: dict[RankingWindow, int | None] = {"7d": 7, "30d": 30, "all": None}
TF_ORDER = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
# the only modules that may import this one (ADR 0053 decision 6f; the import test reads this tuple)
RANKING_READERS = ("goldbot.api.app", "goldbot.telegram.ranking_text", "goldbot.telegram.digest",
                   "goldbot.telegram.weekly_digest")
_SHIFT_S = (24 - ROLLOVER_UTC_H) * 3600     # trades_ledger.oday: [D-1 22:00, D 22:00) UTC is the opportunity day D
_ARM_KINDS = frozenset({"exploration", "control"})
_ENGINE_KINDS = frozenset({"strategy", "real"})
_READ_STATUS: dict[str, RankStatus] = {"P": "proven", "C1": "candidate", "R": "not_proven"}
_STATUS_ORDER: tuple[RankStatus, ...] = ("proven", "candidate", "not_proven")
Kind = Literal["exploration", "engine", "control"]


# ------------------------------------------------------------------------------------------------ the index
# Built once per ledger build (as the List's, trades_ledger._Index; PR #211 CI): every rankable fill as columns in
# close order, so each filter's answer is a handful of vectorised passes (np.unique / np.bincount over group and
# (account, day) cluster codes), never a scan of row objects.
_Meta = tuple[str, str, int, Kind, str, str]      # key, family, version, kind, variant, style


def _arm_meta(arm: str, r: TradeRow, cat: Catalog) -> _Meta:
    """An exploration or control arm's row key, family, rule version, kind, variant and style (catalog first)."""
    info = cat.arm(arm)
    tf = r.timeframe or ""
    family = info.family if info is not None else r.family
    version = info.version if info is not None else 1
    control = info.is_control if info is not None else r.is_control
    kind: Kind = "control" if control else "exploration"
    variant = (info.variant if info is not None else r.variant) or tf
    style = info.style if info is not None and info.style else _TF_STYLE.get(tf, "scalp")
    key = f"control:{family}" if control else family if version == 1 else f"{family}:v{version}"
    return key, family, version, kind, variant, style


def _read_unit(arm: str, cat: Catalog) -> tuple[str, bool] | None:
    """Where an arm's counted (admissible, closed) fills count toward a read, as labs.arm_statuses counts n: its read
    unit, and whether only its stop exits count (S0R); None for a control or virtual exits on another arm's fills."""
    info = cat.arm(arm)
    if info is None:
        return arm, False
    if not info.counted or info.is_control:
        return None
    return cat.unit_of(arm), info.count == "stop_exits"


class _Codes:
    """Strings to dense int codes, in order of first sight."""

    def __init__(self) -> None:
        self.code: dict[Any, int] = {}
        self.names: list[Any] = []

    def __call__(self, x: Any) -> int:
        c = self.code.get(x)
        if c is None:
            c = self.code[x] = len(self.names)
            self.names.append(x)
        return c


@dataclass
class _Index:
    """Every fill the ranking reads, as columns sorted by (close, trade id); codes index the `*_meta` lists."""
    r: np.ndarray
    pnl: np.ndarray
    cost: np.ndarray                    # NaN: not recorded
    risk: np.ndarray                    # NaN: not recorded
    at: np.ndarray                      # the close, epoch seconds
    cell: np.ndarray                    # (account, opportunity day) cluster code
    day: np.ndarray                     # opportunity day code
    acct: np.ndarray
    ccy: np.ndarray
    style: np.ndarray
    label: np.ndarray                   # the account's label
    group: np.ndarray                   # the row (key) code
    variant: np.ndarray                 # (group, timeframe, variant) code
    arm: np.ndarray                     # -1: an engine fill
    counted: np.ndarray                 # its fills are the family's (not virtual exits on another arm's)
    explore: np.ndarray                 # an exploration (strategy) arm's fill
    control: np.ndarray                 # a control arm's fill
    groups: list[tuple[str, str, int, Kind]]       # key, family, version, kind
    variants: list[tuple[int, str, str]]           # group code, timeframe, variant
    arms: list[str]
    accounts: dict[str | None, int]
    ccys: list[str | None]
    styles: dict[str, int]
    style_names: list[str]
    labels: list[str]
    choices: list[AccountChoice]
    prog_unit: np.ndarray               # read progress: each counted fill's read unit code, and its close
    prog_at: np.ndarray
    units: list[str]
    ncell: int
    nday: int


def _index(rows: Iterable[TradeRow], cat: Catalog) -> _Index:
    """One pass over the ledger's rows. A row without its net (a gross-only engine record, unreadable deals) carries
    a gross R: never ranked (after costs only); voided evidence, shadow models and cost probes neither."""
    metas: dict[str, _Meta] = {}
    reads_to: dict[str, tuple[str, bool] | None] = {}
    groups, variants, arms_c, accts, ccys, styles, labels, units = (_Codes() for _ in range(8))
    uncounted = {a for a, i in cat.arms.items() if not i.counted}  # H-80: virtual exits on another arm's fills
    choices: dict[str, str] = {}
    cols: dict[str, list[Any]] = {k: [] for k in ("r", "pnl", "cost", "risk", "at", "day", "acct", "ccy", "style",
                                                  "label", "group", "variant", "arm", "counted", "kind", "tid")}
    prog_unit: list[int] = []
    prog_at: list[float] = []
    for r in rows:
        if r.account is not None and r.kind in _ARM_KINDS | _ENGINE_KINDS:
            choices.setdefault(r.account, r.account_label)
        arm = r.arm_id
        if r.status != "closed" or r.exit_utc is None:
            continue
        at = r.exit_utc.timestamp()
        if arm is not None and r.kind == "exploration" and r.evidence == "counted":
            to = reads_to[arm] if arm in reads_to else reads_to.setdefault(arm, _read_unit(arm, cat))
            if to is not None and (not to[1] or r.exit_reason == "stop"):
                prog_unit.append(units(to[0]))
                prog_at.append(at)
        if r.r is None or r.pnl is None or r.evidence == "excluded":
            continue
        tf = r.timeframe or ""
        meta: _Meta
        if r.kind in _ARM_KINDS and arm is not None:
            cached = metas.get(arm)
            meta = cached if cached is not None else metas.setdefault(arm, _arm_meta(arm, r, cat))
        elif r.kind in _ENGINE_KINDS:
            meta, arm = (f"engine:{r.family}", r.family, 1, "engine", tf, _TF_STYLE.get(tf, "scalp")), None
        else:
            continue
        key, family, version, kind, variant, style = meta
        g = groups((key, family, version, kind))
        c = cols
        c["r"].append(r.r)
        c["pnl"].append(r.pnl)
        c["cost"].append(r.costs.total if r.costs is not None else math.nan)
        c["risk"].append(r.risk_amount if r.risk_amount else math.nan)
        c["at"].append(at)
        c["day"].append(int((at + _SHIFT_S) // 86400))             # [D-1 22:00, D 22:00) UTC is the opportunity day D
        c["acct"].append(accts(r.account))
        c["ccy"].append(ccys(r.currency))
        c["style"].append(styles(style))
        c["label"].append(labels(r.account_label))
        c["group"].append(g)
        c["variant"].append(variants((g, tf, variant)))
        c["arm"].append(arms_c(arm) if arm is not None else -1)
        c["counted"].append(arm not in uncounted)
        c["kind"].append(kind)
        c["tid"].append(r.trade_id)
    day = np.asarray(cols["day"], dtype=np.int64)
    d0 = int(day.min()) if day.size else 0
    nday = int(day.max()) - d0 + 1 if day.size else 1
    acct = np.asarray(cols["acct"], dtype=np.int64)
    order = np.lexsort((np.asarray(cols["tid"], dtype=str), np.asarray(cols["at"], dtype=float))) \
        if day.size else np.zeros(0, dtype=np.int64)
    kinds = np.asarray(cols["kind"], dtype=object)

    def col(name: str, dtype: Any) -> np.ndarray:
        return np.asarray(cols[name], dtype=dtype)[order]
    return _Index(
        r=col("r", float), pnl=col("pnl", float), cost=col("cost", float), risk=col("risk", float),
        at=col("at", float), cell=(acct * nday + day - d0)[order], day=(day - d0)[order], acct=acct[order],
        ccy=col("ccy", np.int64), style=col("style", np.int64), label=col("label", np.int64),
        group=col("group", np.int64), variant=col("variant", np.int64), arm=col("arm", np.int64),
        counted=col("counted", bool), explore=(kinds == "exploration")[order], control=(kinds == "control")[order],
        groups=groups.names, variants=variants.names, arms=arms_c.names, accounts=accts.code, ccys=ccys.names,
        styles=styles.code, style_names=styles.names, labels=labels.names,
        choices=[AccountChoice(account=a, label=lbl) for a, lbl in sorted(choices.items(), key=lambda kv: kv[1])],
        prog_unit=np.asarray(prog_unit, dtype=np.int64), prog_at=np.asarray(prog_at, dtype=float), units=units.names,
        ncell=max(1, len(accts.names) * nday), nday=nday)


# ------------------------------------------------------------------------------------------------ statistics
def _t(p: float, df: int) -> float:
    return float(student_t.ppf(p, df))


@dataclass
class _Pass:
    """Everything a row's columns need, over its fills."""
    n: int = 0
    mean: float = 0.0
    se: float | None = None             # clustered by (account, opportunity day); None under 2 clusters
    clusters: int = 0
    days: int = 0
    wins: int = 0                       # net > 0 after costs (0 is not a win)
    win_money: float = 0.0
    loss_money: float = 0.0
    net: float = 0.0
    currencies: frozenset[str | None] = frozenset()
    cost_sum: float = 0.0
    cost_n: int = 0
    cost_r_sum: float = 0.0
    cost_r_n: int = 0
    drawdown: float = 0.0               # largest peak-to-trough of cumulative net R in close order


def _agg(ix: _Index, sel: np.ndarray, codes: np.ndarray) -> dict[int, _Pass]:
    """Per code, the pass over the selected fills (`sel`: indices in close order). The CR1 variance of a mean:
    G/(G-1) x sum over clusters of (cluster sum - n_g x mean)^2 / N^2."""
    if sel.size == 0:
        return {}
    uc, inv = np.unique(codes, return_inverse=True)
    inv = inv.astype(np.int64)
    k = uc.size
    r, pnl, cost, risk = ix.r[sel], ix.pnl[sel], ix.cost[sel], ix.risk[sel]
    n = np.bincount(inv, minlength=k)
    mean = np.bincount(inv, r, k) / n
    wins = np.bincount(inv, (pnl > 0).astype(float), k)
    win_m = np.bincount(inv, np.where(pnl > 0, pnl, 0.0), k)
    loss_m = np.bincount(inv, np.where(pnl < 0, -pnl, 0.0), k)
    net = np.bincount(inv, pnl, k)
    has = ~np.isnan(cost)
    cost_sum = np.bincount(inv, np.where(has, cost, 0.0), k)
    cost_n = np.bincount(inv, has.astype(float), k)
    hr = has & (risk > 0)
    cost_r_sum = np.bincount(inv, np.where(hr, cost / np.where(hr, risk, 1.0), 0.0), k)
    cost_r_n = np.bincount(inv, hr.astype(float), k)
    pairs, pinv = np.unique(inv * ix.ncell + ix.cell[sel], return_inverse=True)
    pg = pairs // ix.ncell
    s_cell = np.bincount(pinv, r)
    n_cell = np.bincount(pinv)
    g = np.bincount(pg, minlength=k)
    dev = np.bincount(pg, (s_cell - n_cell * mean[pg]) ** 2, k)
    days = np.bincount(np.unique(inv * ix.nday + ix.day[sel]) // ix.nday, minlength=k)
    ccy_pairs = np.unique(inv * max(1, len(ix.ccys)) + ix.ccy[sel])
    ccys: dict[int, set[str | None]] = {}
    for p in ccy_pairs.tolist():
        ccys.setdefault(p // max(1, len(ix.ccys)), set()).add(ix.ccys[p % max(1, len(ix.ccys))])
    order = np.argsort(inv, kind="stable")                  # each code's fills, still in close order
    ro = r[order]
    ends = np.cumsum(n)
    out: dict[int, _Pass] = {}
    for i in range(k):
        seg = ro[ends[i] - n[i]:ends[i]]
        c = np.cumsum(seg)
        dd = float(np.max(np.maximum.accumulate(np.maximum(c, 0.0)) - c))
        gi, ni = int(g[i]), int(n[i])
        se = math.sqrt(gi / (gi - 1) * float(dev[i]) / ni ** 2) if gi >= 2 else None
        out[int(uc[i])] = _Pass(
            n=ni, mean=float(mean[i]), se=se, clusters=gi, days=int(days[i]), wins=int(wins[i]),
            win_money=float(win_m[i]), loss_money=float(loss_m[i]), net=float(net[i]),
            currencies=frozenset(ccys.get(i, set())), cost_sum=float(cost_sum[i]), cost_n=int(cost_n[i]),
            cost_r_sum=float(cost_r_sum[i]), cost_r_n=int(cost_r_n[i]), drawdown=max(0.0, dd))
    return out


def _recent(ix: _Index, sel: np.ndarray, codes: np.ndarray, since: float) -> dict[int, RecentFigures]:
    """Per code, net R per trade over the selected fills closed since `since`, with its n."""
    keep = ix.at[sel] >= since
    c, r = codes[keep], ix.r[sel][keep]
    if c.size == 0:
        return {}
    uc, inv = np.unique(c, return_inverse=True)
    n = np.bincount(inv)
    s = np.bincount(inv, r)
    return {int(u): RecentFigures(n=int(n[i]), net_r_per_trade=round(float(s[i] / n[i]), 3)) for i, u in enumerate(uc)}


def _interval(m: float, se: float | None, g: int, level: float = CI) -> tuple[float, float] | None:
    if se is None or g < 2:
        return None
    half = _t(1 - (1 - level) / 2, g - 1) * se
    return round(m - half, 3), round(m + half, 3)


@dataclass(frozen=True)
class _Edge:
    value: float
    ci: tuple[float, float] | None
    level: float
    source: Literal["read", "interim"]


def _interim_edge(arms: Collection[str], own: dict[str, _Pass], controls: dict[str, _Pass],
                  cat: Catalog) -> _Edge | None:
    """The fill-weighted mean of the arms' differences with their paired controls (ADR 0051), 95% CI with the
    smallest df: descriptive, labelled interim. An arm or control under 2 clusters gives no difference."""
    parts = []
    for arm in arms:
        info = cat.arm(arm)
        a, c = own.get(arm), controls.get(info.control_arm or "") if info is not None else None
        if a is None or c is None or a.se is None or c.se is None:
            continue
        parts.append((a.n, a.mean - c.mean, a.se ** 2 + c.se ** 2, min(a.clusters, c.clusters) - 1))
    if not parts:
        return None
    total = sum(n for n, *_ in parts)
    value = sum(n / total * d for n, d, _, _ in parts)
    var = sum((n / total) ** 2 * v for n, _, v, _ in parts)
    half = _t(1 - (1 - CI) / 2, min(df for *_, df in parts)) * math.sqrt(var)
    return _Edge(round(value, 3), (round(value - half, 3), round(value + half, 3)), CI, "interim")


def _recorded_edge(arms: Collection[str], cat: Catalog) -> _Edge | None:
    """The evaluator's recorded comparison, when the row's strategy arms are one read unit and it is read."""
    units = {cat.unit_of(a) for a in arms}
    if len(units) != 1:
        return None
    read = cat.reads.get(next(iter(units)))
    if read is None or read.vs_control_r is None:
        return None
    return _Edge(read.vs_control_r, read.vs_control_ci90, 0.90, "read")


_NONE_RECENT = RecentFigures(n=0, net_r_per_trade=None)


def _figures(p: _Pass, last_7d: RecentFigures, last_30d: RecentFigures, edge: _Edge | None) -> RankFigures:
    """A row's or variant's columns from its pass over its window fills and its last 7 and 30 days."""
    trend: Literal["up", "down", "flat"] | None = None
    if (last_7d.n >= MIN_FILLS and last_30d.n >= MIN_FILLS and last_7d.net_r_per_trade is not None
            and last_30d.net_r_per_trade is not None):
        d = last_7d.net_r_per_trade - last_30d.net_r_per_trade
        trend = "flat" if abs(d) < FLAT_R else "up" if d > 0 else "down"
    n = p.n
    one_ccy = len(p.currencies) == 1 and None not in p.currencies and n > 0
    net = round(p.net, 2) if one_ccy else None
    return RankFigures(
        n=n, days=p.days, clusters=p.clusters, net_r_per_trade=round(p.mean, 3) if n else None,
        ci95=_interval(p.mean, p.se, p.clusters), lower_adjusted=None,
        hit_rate=round(p.wins / n, 3) if n else None,
        net_pnl=net, net_per_trade=round(net / n, 2) if net is not None else None,
        currency=next(iter(p.currencies)) if one_ccy else None,
        profit_factor=round(p.win_money / p.loss_money, 2) if one_ccy and p.loss_money > 0 else None,
        no_losses=one_ccy and p.loss_money == 0, max_drawdown_r=round(p.drawdown, 3) if n else None,
        cost_per_trade=round(p.cost_sum / p.cost_n, 2) if p.cost_n and len(p.currencies) == 1 else None,
        cost_per_trade_r=round(p.cost_r_sum / p.cost_r_n, 3) if p.cost_r_n else None,
        edge_vs_control_r=edge.value if edge else None, edge_ci=edge.ci if edge else None,
        edge_ci_level=edge.level if edge else None, edge_source=edge.source if edge else None,
        last_7d=last_7d, last_30d=last_30d, trend=trend)


# ------------------------------------------------------------------------------------------------ status and bands
def _status(arms: Collection[str], kind: Kind, cat: Catalog, retired: Collection[str]) -> RankStatus:
    """The best recorded verdict once any read unit of the row's strategy arms is read and the others are read or
    stopped; "early" while an unread arm still runs; "retired" when every arm stopped at its pre-registered stop
    unread. Engine strategies and controls stay early."""
    if kind != "exploration" or not arms:
        return "early"
    reads = [r for a in arms if (r := cat.reads.get(cat.unit_of(a))) is not None]
    if not all(a in retired or cat.reads.get(cat.unit_of(a)) is not None for a in arms):
        return "early"
    if reads:
        return min((_READ_STATUS[r.state] for r in reads), key=_STATUS_ORDER.index)
    return "retired"


def _band(kind: Kind, status: RankStatus, f: RankFigures) -> RankBand:
    if kind == "control":
        return "control"
    if status in ("retired", "not_proven"):
        return "retired"
    return "ranked" if f.n >= MIN_FILLS and f.days >= MIN_DAYS else "too_few"


def _why(f: RankFigures) -> str:
    return f"too few to rank ({f.n} of {MIN_FILLS} fills, {f.days} of {MIN_DAYS} days)"


def _tf_key(tf: str) -> int:
    return TF_ORDER.index(tf) if tf in TF_ORDER else len(TF_ORDER)


def _progress(arms: Collection[str], kind: Kind, cat: Catalog, unit_n: dict[str, int]) -> RankProgress | None:
    """The row's unread unit closest to its read (n counted of its read size)."""
    if kind != "exploration":
        return None
    best: RankProgress | None = None
    for u in sorted({cat.unit_of(a) for a in arms}):
        size = next((cat.read_size(a) for a in arms if cat.unit_of(a) == u), None)
        if u in cat.reads or not size:
            continue
        p = RankProgress(unit=u, n_counted=unit_n.get(u, 0), n_read_at=size)
        if best is None or p.n_counted / p.n_read_at > best.n_counted / best.n_read_at:
            best = p
    return best


def _window_lo(as_of: pd.Timestamp, days: int | None) -> float | None:
    """The first instant of the window's first opportunity day, epoch seconds; None for "all"."""
    if days is None:
        return None
    today = date.fromisoformat(report_day(as_of))
    return float(day_bounds((today - timedelta(days=days - 1)).isoformat())[0].timestamp())


def _pairs(a: np.ndarray, b: np.ndarray, nb: int) -> dict[int, list[int]]:
    """Each value of `a` with the distinct values of `b` beside it."""
    out: dict[int, list[int]] = {}
    for p in np.unique(a * nb + b).tolist():
        out.setdefault(p // nb, []).append(p % nb)
    return out


# ------------------------------------------------------------------------------------------------ the builder
def _rank(ix: _Index, cat: Catalog, as_of: pd.Timestamp, window: RankingWindow, account: str | None,
          style: str | None, retired: Collection[str], unreadable: Sequence[str]) -> RankingView:
    lo = _window_lo(as_of, WINDOW_DAYS[window])
    week_lo, month_lo = _window_lo(as_of, 7), _window_lo(as_of, 30)
    assert week_lo is not None and month_lo is not None
    hi = as_of.timestamp()
    base = ix.at < hi
    if account is not None:
        base &= ix.acct == ix.accounts.get(account, -2)
    win = base & (ix.at >= lo) if lo is not None else base
    styled = (ix.style == ix.styles.get(style, -2)) if style is not None else np.ones_like(base)
    gsel = np.flatnonzero(win & styled)                     # the rows' window fills, in close order
    csel = np.flatnonzero(win & ix.control & (ix.arm >= 0))     # the controls' (any style)
    controls = {ix.arms[a]: p for a, p in _agg(ix, csel, ix.arm[csel]).items()}
    counted = gsel[ix.counted[gsel]]
    g_pass = _agg(ix, counted, ix.group[counted])
    v_pass = _agg(ix, gsel, ix.variant[gsel])
    esel = gsel[ix.explore[gsel]]
    own = {ix.arms[a]: p for a, p in _agg(ix, esel, ix.arm[esel]).items()}
    rsel = np.flatnonzero(base & styled & (ix.at >= month_lo))
    rc = rsel[ix.counted[rsel]]
    g7, g30 = _recent(ix, rc, ix.group[rc], week_lo), _recent(ix, rc, ix.group[rc], month_lo)
    v7, v30 = _recent(ix, rsel, ix.variant[rsel], week_lo), _recent(ix, rsel, ix.variant[rsel], month_lo)
    narm = max(1, len(ix.arms) + 1)
    g_arms = _pairs(ix.group[gsel], ix.arm[gsel] + 1, narm)
    v_arms = _pairs(ix.variant[gsel], ix.arm[gsel] + 1, narm)
    g_vars = _pairs(ix.group[gsel], ix.variant[gsel], max(1, len(ix.variants)))
    g_labels = _pairs(ix.group[gsel], ix.label[gsel], max(1, len(ix.labels)))
    style_n = np.bincount(ix.group[gsel] * max(1, len(ix.style_names)) + ix.style[gsel])
    unit_n = {ix.units[u]: int(c) for u, c in enumerate(np.bincount(ix.prog_unit[ix.prog_at < hi],
                                                                    minlength=len(ix.units)))}
    uncounted = {a for a, i in cat.arms.items() if not i.counted}
    built: list[RankRow] = []
    for g, vs in g_vars.items():
        key, family, version, kind = ix.groups[g]
        arms = {ix.arms[a - 1] for a in g_arms.get(g, []) if a > 0}
        strat = (arms - uncounted) or arms
        status = _status(strat, kind, cat, retired)
        edge = None if kind != "exploration" else (_recorded_edge(strat, cat) or _interim_edge(strat, own, controls, cat))
        fig = _figures(g_pass.get(g, _Pass()), g7.get(g, _NONE_RECENT), g30.get(g, _NONE_RECENT), edge)
        band = _band(kind, status, fig)
        variants = []
        for v in sorted(vs, key=lambda v: (_tf_key(ix.variants[v][1]), ix.variants[v][2])):
            _, tf, name = ix.variants[v]
            va = {ix.arms[a - 1] for a in v_arms.get(v, []) if a > 0}
            v_edge = None if kind != "exploration" else (_recorded_edge(va, cat)
                                                         or _interim_edge(va, own, controls, cat))
            variants.append(RankVariant(variant=name, timeframe=tf, status=_status(va, kind, cat, retired),
                                        figures=_figures(v_pass[v], v7.get(v, _NONE_RECENT), v30.get(v, _NONE_RECENT),
                                                         v_edge)))
        ns = max(1, len(ix.style_names))
        counts = style_n[g * ns:(g + 1) * ns]
        built.append(RankRow(
            key=key, family=family, family_label=cat.family_label(family) + (f" v{version}" if version > 1 else ""),
            version=version, style=ix.style_names[int(np.argmax(counts))] if counts.size and counts.max() else (style or ""),
            kind=kind, timeframes=sorted({ix.variants[v][1] for v in vs}, key=_tf_key),
            accounts=sorted(ix.labels[x] for x in g_labels.get(g, [])), band=band, rank=None,
            unranked_reason=_why(fig) if band == "too_few" else None, status=status,
            read_progress=_progress(strat, kind, cat, unit_n), figures=fig, variants=variants))
    ranked = [r for r in built if r.band == "ranked"]
    k = len(ranked)
    passes = {ix.groups[g][0]: p for g, p in g_pass.items()}
    for row in ranked:
        p = passes[row.key]
        if p.se is not None:
            row.figures.lower_adjusted = round(p.mean - _t(1 - ALPHA / k, p.clusters - 1) * p.se, 3)
    ranked.sort(key=lambda r: (-(r.figures.lower_adjusted if r.figures.lower_adjusted is not None else -math.inf),
                               -r.figures.n, r.family_label))
    for i, row in enumerate(ranked, 1):
        row.rank = i
    few = sorted((r for r in built if r.band == "too_few"), key=lambda r: (-r.figures.n, r.family_label))
    clusters = [r.figures.clusters for r in ranked]
    t_adj = round(_t(1 - ALPHA / k, max(1, int(median(clusters)) - 1)), 2) if k else None
    return RankingView(
        as_of=as_of.to_pydatetime(), window=window, account=account, style=style, heading=HEADING, k_ranked=k,
        t_adjusted=t_adj, min_fills=MIN_FILLS, min_days=MIN_DAYS, rows=[*ranked, *few],
        controls=sorted((r for r in built if r.band == "control"), key=lambda r: r.family_label),
        retired=sorted((r for r in built if r.band == "retired"), key=lambda r: r.family_label),
        account_choices=ix.choices, unreadable=list(unreadable))


def rank(rows: Iterable[TradeRow], cat: Catalog, as_of: pd.Timestamp, *, window: RankingWindow = "all",
         account: str | None = None, style: str | None = None, retired: Collection[str] = (),
         unreadable: Sequence[str] = ()) -> RankingView:
    """The ranking over `rows` (the trade ledger's) closed before `as_of` in `window`, filtered to one account (config
    name) and one style when given. `retired`: the arms at their pre-registered stop (labs.arm_statuses). One-off:
    the API and the reports use a Ranker (the index built once per ledger build)."""
    return _rank(_index(rows, cat), cat, as_of, window, account, style, retired, unreadable)


@dataclass
class Ranker:
    """One ledger build's rankings: its index built with it (never on a request) and its answers remembered per
    filter, as the List's are (trades_ledger._Index): the ledger is immutable until its next build, so an answer
    stays right for the build's life."""
    rows: Sequence[TradeRow]
    cat: Catalog
    as_of: pd.Timestamp
    retired: Collection[str] = ()
    unreadable: Sequence[str] = ()
    answers: dict[tuple[str, str | None, str | None], RankingView] = field(default_factory=dict)
    index: _Index | None = None

    def __post_init__(self) -> None:
        if self.index is None:
            self.index = _index(self.rows, self.cat)

    def __call__(self, window: RankingWindow = "all", account: str | None = None,
                 style: str | None = None) -> RankingView:
        key = (window, account, style)
        hit = self.answers.get(key)
        if hit is None:
            assert self.index is not None
            hit = self.answers[key] = _rank(self.index, self.cat, self.as_of, window, account, style, self.retired,
                                            self.unreadable)
        return hit


def rank_moves(now: RankingView, before: RankingView) -> dict[str, str]:
    """Each ranked row's move since `before` (the weekly digest): "up 2", "down 1", "same" or "new"."""
    prev = {r.key: r.rank for r in before.rows if r.rank is not None}
    out = {}
    for r in now.rows:
        if r.rank is None:
            continue
        p = prev.get(r.key)
        out[r.key] = ("new" if p is None else "same" if p == r.rank
                      else f"up {p - r.rank}" if p > r.rank else f"down {r.rank - p}")
    return out
