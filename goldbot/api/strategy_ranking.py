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
from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from operator import attrgetter
from statistics import median
from typing import Literal

import pandas as pd
from scipy.stats import t as student_t

from goldbot.api.schema import (
    AccountChoice,
    RankBand,
    RankFigures,
    RankingView,
    RankingWindow,
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


@dataclass(frozen=True, slots=True)
class _Fill:
    """One closed fill as the ranking reads it."""
    r: float
    pnl: float | None
    cost: float | None
    risk: float | None
    currency: str | None
    cell: tuple[str | None, int]        # (account, opportunity day ordinal)
    at: float                           # the close, epoch seconds
    trade_id: str
    arm: str | None
    variant: str
    timeframe: str
    account_label: str
    style: str


@dataclass
class _Group:
    key: str
    family: str
    label: str
    version: int
    kind: Kind
    fills: list[_Fill]                  # in the window
    recent: list[_Fill]                 # the last 30 days, whatever the window (the 7/30 d columns)
    arms: set[str]


# ------------------------------------------------------------------------------------------------ statistics
def _t(p: float, df: int) -> float:
    return float(student_t.ppf(p, df))


@dataclass
class _Pass:
    """Everything a row's columns need, from one pass over its fills in close order."""
    n: int = 0
    mean: float = 0.0
    se: float | None = None             # clustered by (account, opportunity day); None under 2 clusters
    clusters: int = 0
    days: int = 0
    wins: int = 0                       # net > 0 after costs (0 is not a win)
    win_money: float = 0.0
    loss_money: float = 0.0
    net: float = 0.0
    pnl_known: bool = True
    currencies: frozenset[str | None] = frozenset()
    cost_sum: float = 0.0
    cost_n: int = 0
    cost_r_sum: float = 0.0
    cost_r_n: int = 0
    drawdown: float = 0.0               # largest peak-to-trough of cumulative net R


def _pass(fills: Sequence[_Fill]) -> _Pass:
    """One pass. The CR1 variance of a mean: G/(G-1) x sum over clusters of (cluster sum - n_g x mean)^2 / N^2."""
    out = _Pass(n=len(fills))
    if not fills:
        return out
    sums: dict[tuple[str | None, int], list[float]] = {}
    total = cum = peak = dd = 0.0
    ccys: set[str | None] = set()
    for f in fills:
        r = f.r
        total += r
        c = sums.get(f.cell)
        if c is None:
            sums[f.cell] = [r, 1.0]
        else:
            c[0] += r
            c[1] += 1.0
        cum += r
        if cum > peak:
            peak = cum
        elif peak - cum > dd:
            dd = peak - cum
        ccys.add(f.currency)
        p = f.pnl
        if p is None:
            out.pnl_known = False
            out.wins += r > 0
        else:
            out.net += p
            if p > 0:
                out.wins += 1
                out.win_money += p
            elif p < 0:
                out.loss_money -= p
        if f.cost is not None:
            out.cost_sum += f.cost
            out.cost_n += 1
            if f.risk:
                out.cost_r_sum += f.cost / f.risk
                out.cost_r_n += 1
    n = out.n
    m = out.mean = total / n
    g = out.clusters = len(sums)
    out.days = len({day for _, day in sums})
    out.currencies = frozenset(ccys)
    out.drawdown = dd
    if g >= 2:
        out.se = math.sqrt(g / (g - 1) * sum((s - k * m) ** 2 for s, k in sums.values()) / n ** 2)
    return out


def _interval(m: float, se: float | None, g: int, level: float = CI) -> tuple[float, float] | None:
    if se is None or g < 2:
        return None
    half = _t(1 - (1 - level) / 2, g - 1) * se
    return round(m - half, 3), round(m + half, 3)


def _recent(fills: Sequence[_Fill], since: float) -> RecentFigures:
    rs = [f.r for f in fills if f.at >= since]
    return RecentFigures(n=len(rs), net_r_per_trade=round(sum(rs) / len(rs), 3) if rs else None)


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


def _figures(fills: Sequence[_Fill], recent: Sequence[_Fill], edge: _Edge | None, week_lo: float,
             month_lo: float, done: _Pass | None = None) -> tuple[RankFigures, _Pass]:
    """A row's or variant's columns over its window fills (in close order) and its last 30 days (`done`: the pass
    over these fills, already made)."""
    p = done or _pass(fills)
    last_7d, last_30d = _recent(recent, week_lo), _recent(recent, month_lo)
    trend: Literal["up", "down", "flat"] | None = None
    if (last_7d.n >= MIN_FILLS and last_30d.n >= MIN_FILLS and last_7d.net_r_per_trade is not None
            and last_30d.net_r_per_trade is not None):
        d = last_7d.net_r_per_trade - last_30d.net_r_per_trade
        trend = "flat" if abs(d) < FLAT_R else "up" if d > 0 else "down"
    n = p.n
    one_ccy = len(p.currencies) == 1 and None not in p.currencies and p.pnl_known and n > 0
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
        last_7d=last_7d, last_30d=last_30d, trend=trend), p


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


# ------------------------------------------------------------------------------------------------ the builder
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


def _fill(r: TradeRow, cat: Catalog, metas: dict[str, _Meta]) -> tuple[_Meta, _Fill] | None:
    """The row's group and its fill; None for anything the ranking does not read. `metas` caches each arm's. A row
    without its net (a gross-only engine record, unreadable deals) carries a gross R: never ranked (after costs only)."""
    if r.status != "closed" or r.exit_utc is None or r.r is None or r.pnl is None or r.evidence == "excluded":
        return None
    tf = r.timeframe or ""
    arm = r.arm_id
    meta: _Meta
    if r.kind in _ARM_KINDS and arm is not None:
        cached = metas.get(arm)
        meta = cached if cached is not None else metas.setdefault(arm, _arm_meta(arm, r, cat))
    elif r.kind in _ENGINE_KINDS:
        meta, arm = (f"engine:{r.family}", r.family, 1, "engine", tf, _TF_STYLE.get(tf, "scalp")), None
    else:
        return None                                       # shadow models and cost probes are never ranked
    at = r.exit_utc.timestamp()
    return meta, _Fill(
        r=r.r, pnl=r.pnl, cost=r.costs.total if r.costs is not None else None, risk=r.risk_amount,
        currency=r.currency, cell=(r.account, int((at + _SHIFT_S) // 86400)),   # [D-1 22:00, D 22:00) UTC is D
        at=at, trade_id=r.trade_id, arm=arm, variant=meta[4], timeframe=tf, account_label=r.account_label,
        style=meta[5])


def _window_lo(as_of: pd.Timestamp, days: int | None) -> float | None:
    """The first instant of the window's first opportunity day, epoch seconds; None for "all"."""
    if days is None:
        return None
    today = date.fromisoformat(report_day(as_of))
    return float(day_bounds((today - timedelta(days=days - 1)).isoformat())[0].timestamp())


def rank(rows: Iterable[TradeRow], cat: Catalog, as_of: pd.Timestamp, *, window: RankingWindow = "all",
         account: str | None = None, style: str | None = None, retired: Collection[str] = (),
         unreadable: Sequence[str] = ()) -> RankingView:
    """The ranking over `rows` (the trade ledger's) closed before `as_of` in `window`, filtered to one account (config
    name) and one style when given. `retired`: the arms at their pre-registered stop (labs.arm_statuses)."""
    lo = _window_lo(as_of, WINDOW_DAYS[window])
    week_lo, month_lo = _window_lo(as_of, 7), _window_lo(as_of, 30)
    assert week_lo is not None and month_lo is not None
    hi = as_of.timestamp()
    groups: dict[str, _Group] = {}
    by_control: dict[str, list[_Fill]] = {}             # control arm -> its fills in the window (any style)
    metas: dict[str, _Meta] = {}
    choices: dict[str, str] = {}
    for r in rows:
        if r.account is not None and r.kind in _ARM_KINDS | _ENGINE_KINDS:
            choices.setdefault(r.account, r.account_label)
        if account is not None and r.account != account:
            continue
        got = _fill(r, cat, metas)
        if got is None or got[1].at >= hi:
            continue
        (key, family, version, kind, _, f_style), f = got
        in_window = lo is None or f.at >= lo
        if kind == "control" and f.arm is not None and in_window:
            by_control.setdefault(f.arm, []).append(f)
        if style is not None and f_style != style:
            continue
        g = groups.get(key)
        if g is None:
            label = cat.family_label(family) + (f" v{version}" if version > 1 else "")
            g = groups[key] = _Group(key=key, family=family, label=label, version=version, kind=kind, fills=[],
                                     recent=[], arms=set())
        if in_window:
            g.fills.append(f)
        if f.at >= month_lo:
            g.recent.append(f)
        if f.arm is not None:
            g.arms.add(f.arm)
    groups = {k: g for k, g in groups.items() if g.fills}  # a family with no fill in the window is no row (n 0)
    order = attrgetter("at", "trade_id")
    controls = {}
    for arm, fs in by_control.items():
        fs.sort(key=order)
        controls[arm] = _pass(fs)
    uncounted = {a for a, i in cat.arms.items() if not i.counted}   # H-80: virtual exits on another arm's fills
    built: list[RankRow] = []
    passes: dict[str, _Pass] = {}
    for g in groups.values():
        g.fills.sort(key=order)                         # close order: the drawdown's, once per group
        by_arm: dict[str, list[_Fill]] = {}
        by_variant: dict[tuple[str, str], list[_Fill]] = {}
        for f in g.fills:
            if f.arm is not None:
                by_arm.setdefault(f.arm, []).append(f)
            by_variant.setdefault((f.timeframe, f.variant), []).append(f)
        own: dict[str, _Pass] = {}
        v_pass: dict[tuple[str, str], _Pass] = {}
        if g.kind == "exploration":                     # an arm alone in its variant shares the variant's pass
            alone = {fs[0].arm: v for v, fs in by_variant.items()
                     if fs[0].arm is not None and len(by_arm[fs[0].arm]) == len(fs)}
            v_pass = {v: _pass(by_variant[v]) for v in set(alone.values())}
            own = {arm: v_pass[alone[arm]] if arm in alone else _pass(fs) for arm, fs in by_arm.items()}
        counted = [f for f in g.fills if f.arm not in uncounted]
        strat = (g.arms - uncounted) or g.arms
        status = _status(strat, g.kind, cat, retired)
        edge = None if g.kind != "exploration" else (_recorded_edge(strat, cat)
                                                     or _interim_edge(strat, own, controls, cat))
        fig, passes[g.key] = _figures(counted, [f for f in g.recent if f.arm not in uncounted], edge, week_lo,
                                      month_lo)
        band = _band(g.kind, status, fig)
        recent_by: dict[tuple[str, str], list[_Fill]] = {}
        for f in g.recent:
            recent_by.setdefault((f.timeframe, f.variant), []).append(f)
        variants = []
        for v in sorted(by_variant, key=lambda tv: (_tf_key(tv[0]), tv[1])):
            vf = by_variant[v]
            v_arms = {f.arm for f in vf if f.arm is not None}
            v_edge = None if g.kind != "exploration" else (_recorded_edge(v_arms, cat)
                                                           or _interim_edge(v_arms, own, controls, cat))
            variants.append(RankVariant(
                variant=v[1], timeframe=v[0], status=_status(v_arms, g.kind, cat, retired),
                figures=_figures(vf, recent_by.get(v, []), v_edge, week_lo, month_lo, v_pass.get(v))[0]))
        styles = Counter(f.style for f in g.fills).most_common(1)
        built.append(RankRow(
            key=g.key, family=g.family, family_label=g.label, version=g.version,
            style=styles[0][0] if styles else (style or ""), kind=g.kind,
            timeframes=sorted({f.timeframe for f in g.fills}, key=_tf_key),
            accounts=sorted({f.account_label for f in g.fills}), band=band, rank=None,
            unranked_reason=_why(fig) if band == "too_few" else None, status=status, figures=fig, variants=variants))
    ranked = [r for r in built if r.band == "ranked"]
    k = len(ranked)
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
        as_of=as_of.to_pydatetime(), window=window, account=account, style=style, heading=HEADING, k_ranked=k, t_adjusted=t_adj,
        min_fills=MIN_FILLS, min_days=MIN_DAYS, rows=[*ranked, *few],
        controls=sorted((r for r in built if r.band == "control"), key=lambda r: r.family_label),
        retired=sorted((r for r in built if r.band == "retired"), key=lambda r: r.family_label),
        account_choices=[AccountChoice(account=a, label=lbl) for a, lbl in sorted(choices.items(), key=lambda kv: kv[1])],
        unreadable=list(unreadable))


@dataclass
class Ranker:
    """One ledger build's rankings, remembered per filter: the ledger is immutable until its next build, so an answer
    stays right for the build's life (the API keeps one Ranker per build; the List's answers work the same way)."""
    rows: Sequence[TradeRow]
    cat: Catalog
    as_of: pd.Timestamp
    retired: Collection[str] = ()
    unreadable: Sequence[str] = ()
    answers: dict[tuple[str, str | None, str | None], RankingView] = field(default_factory=dict)

    def __call__(self, window: RankingWindow = "all", account: str | None = None,
                 style: str | None = None) -> RankingView:
        key = (window, account, style)
        hit = self.answers.get(key)
        if hit is None:
            hit = self.answers[key] = rank(self.rows, self.cat, self.as_of, window=window, account=account,
                                           style=style, retired=self.retired, unreadable=self.unreadable)
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
