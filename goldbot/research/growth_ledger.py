"""Growth sprint 2026-11-01 (docs/research/growth-sprint-2026-11-01.md section 3): the read of the demo growth test.

"Sizing: 1% of equity per unit, the RiskGate cap ... No cap is loosened. **Virtual ledger** at f = 5/10/20%, computed
from GS1's and GS1C's realised R." R is scale-free, so replaying the realised R sequence at a larger fraction answers
"would it have doubled" without loosening the RiskGate. Quant requirements (2026-11-01):

* (5) GS1C pairs 1:1 with GS1: the control enters every session GS1 is eligible in (`arm_rules_growth`), and the read
  pairs the two by (day, session); an unmatched control entry is left out of the paired excess. A GS1 or GS1C entry a
  daily or weekly cap refused is a shadow fill (its candidate row, scored by the nightly bar replay `exploration_data.label_candidates`:
  label_sim_r, net of the modelled costs; `ops.growth_report`), so the shared
  2%/6% caps cannot bias one arm against the other; the cap refusals are counted per arm and reason.
* (6) The virtual ledger compounds by entry time, sizing each trade on the equity realised at its entry (overlapping
  trades share that equity); equity floors at 0 when a gap passes -1/f (`ruined`); the refused (shadow) trades are
  reported; `margin_ok` says whether f with `units` pyramid units is executable at the broker's margin (the notional
  of f x equity at the stop, times the units, within the leverage at the gate's 300% margin level).

Costs: a real fill's R is net (the broker's P&L over the loss at the stop); a shadow fill's is the replay's, net of the
modelled costs."""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp

FRACTIONS = (0.05, 0.10, 0.20)
DOUBLE = 2.0
MARGIN_LEVEL = 3.0           # the gate's margin_level_floor (300%)
LEVERAGE = 20.0              # the gate's 1:20 figure (`RiskLimits.leverage_cap`): the conservative broker margin
GROWTH_ARMS = ("GS1", "GS1C")
GROWTH_TF_S = 900            # both decide on 15m closes: a pyramid add's id is its group's close + k seconds
CONTROL_OF = {"GS1C": "GS1"}
# a blocked candidate is a shadow fill unless its refusal says the entry was not a valid trade at all
# (`above_minimum_lot`: an order-core size refusal, a plumbing fault, never a cap the other arm escaped)
NOT_SHADOW = frozenset({"duplicate", "weekend_hold", "arm_retired", "arm_status_unread", "stale", "not_auto",
                        "above_minimum_lot"})


class LedgerTrade(FrozenRecord):
    """One GS1/GS1C trade in R: a real fill (net) or a shadow fill (a capped entry scored on the bars, gross)."""
    arm_id: str
    entry_utc: UtcTimestamp
    exit_utc: UtcTimestamp
    r: float
    session: str | None = None   # the break session (`arm_rules_growth.session_of` of the entry when None)
    shadow: bool = False
    group_id: str | None = None  # the first unit's client order id (its pyramid group)


class VirtualLedger(FrozenRecord):
    """The R sequence replayed at a risk fraction `f` of equity per trade, from 1.0."""
    f: float
    trades: int
    shadow_trades: int
    final_multiple: float
    peak_multiple: float
    doubled: bool                # equity reached DOUBLE x the start at some close
    max_drawdown: float          # peak-to-trough fraction of the realised curve
    ruined: bool                 # a loss passed -1/f: equity floored at 0
    margin_ok: bool | None = None
    leverage: float | None = None


class ArmRead(FrozenRecord):
    arm_id: str
    n: int                       # real fills
    mean_r: float | None = None
    sum_r: float | None = None
    shadow_n: int = 0
    refused: dict[str, int] = Field(default_factory=dict)          # first units a cap refused, by reason
    ledgers: dict[str, VirtualLedger] = Field(default_factory=dict)
    # quant Q4 2026-11-01: a refused pyramid add is no trade of its own (not in n, the means, the pairs or `ledgers`):
    # counted here by reason, and in `ledgers_uncapped`, each group's R plus its refused adds' replayed R (approx.)
    refused_adds: dict[str, int] = Field(default_factory=dict)
    ledgers_uncapped: dict[str, VirtualLedger] = Field(default_factory=dict)


class GrowthRead(FrozenRecord):
    arms: dict[str, ArmRead]
    pairs: int                   # (day, session) cells with both a GS1 and a GS1C trade, shadow fills included
    paired_excess_r: float | None = None   # mean of GS1 R - GS1C R over the pairs
    note: str = ""
    # quant Q4 sensitivity: the pairing without the (day, session) cells where either leg had a refused add
    pairs_no_refused_add: int = 0
    paired_excess_no_refused_add_r: float | None = None


class ShadowSplit(FrozenRecord):
    """The refused GS1/GS1C candidates (`exploration_obs`, taken = 0) of one account, by place on the ladder."""
    firsts: list[LedgerTrade] = Field(default_factory=list)            # labelled shadow first units: one trade each
    refused: dict[str, dict[str, int]] = Field(default_factory=dict)  # arm -> first-unit refusals by reason
    refused_adds: dict[str, dict[str, int]] = Field(default_factory=dict)   # arm -> cap-refused adds by reason
    add_r: dict[str, list[float]] = Field(default_factory=dict)        # group id -> its refused adds' replayed R
    add_groups: list[str] = Field(default_factory=list)                # groups with a cap-refused add


def split_shadow(rows: Iterable[Sequence[Any]], account_id: str) -> ShadowSplit:
    """Quant Q4: rows (candidate_id, arm_id, decision_ns, blocked_reason, label_sim_r, label_exit_ns, max_hold_s)
    decoded by `engine.exploration_pyramid.decode`. A first unit (k = 0) is counted by reason and, refused by a cap
    and labelled, is one shadow trade (R = label_sim_r). An add (k = 1..3) refused by a cap (a reason not in
    NOT_SHADOW) is never a trade: counted in `refused_adds`, its R kept for the uncapped ledger."""
    from goldbot.engine.exploration_pyramid import decode
    firsts: list[LedgerTrade] = []
    refused: dict[str, dict[str, int]] = {}
    refused_adds: dict[str, dict[str, int]] = {}
    add_r: dict[str, list[float]] = {}
    add_groups: list[str] = []
    for cid, arm, d_ns, why, label, exit_ns, hold_s in rows:
        arm, reason = str(arm), str(why or "unknown")
        g = decode(account_id, arm, GROWTH_TF_S, str(cid))
        if g is not None and g[1] > 0:
            if reason in NOT_SHADOW:
                continue
            by = refused_adds.setdefault(arm, {})
            by[reason] = by.get(reason, 0) + 1
            if g[0] not in add_groups:
                add_groups.append(g[0])
            if label is not None:
                add_r.setdefault(g[0], []).append(float(label))
            continue
        by = refused.setdefault(arm, {})
        by[reason] = by.get(reason, 0) + 1
        if reason in NOT_SHADOW or label is None:
            continue
        entry = pd.Timestamp(int(d_ns), unit="ns", tz="UTC")
        end = pd.Timestamp(int(exit_ns), unit="ns", tz="UTC") if exit_ns is not None \
            else entry + pd.Timedelta(seconds=int(hold_s or 0))
        firsts.append(LedgerTrade(arm_id=arm, entry_utc=entry, exit_utc=end, r=float(label), shadow=True,
                                      group_id=str(cid)))
    return ShadowSplit(firsts=firsts, refused=refused, refused_adds=refused_adds, add_r=add_r, add_groups=add_groups)


def group_cell(group_id: str) -> tuple[pd.Timestamp, str | None] | None:
    """The (day, session) cell of a group, from its first unit's signal close in its id."""
    from goldbot.engine.exploration import signal_close
    from goldbot.research.arm_rules_growth import session_of
    t = signal_close(group_id)
    return (t.normalize(), session_of(t)) if t is not None else None


def margin_ok(f: float, stop_usd: float, price: float, units: int, leverage: float = LEVERAGE) -> bool:
    """Whether `units` units each risking `f` of equity at a `stop_usd` stop fit the margin: notional per unit = f /
    stop x price (in equities), margin = units x notional / leverage, at most 1 / MARGIN_LEVEL of equity."""
    if not (stop_usd > 0 and price > 0 and leverage > 0):
        return False
    return units * f / stop_usd * price / leverage <= 1.0 / MARGIN_LEVEL + 1e-12


def virtual_ledger(trades: Iterable[LedgerTrade], f: float, *, stop_usd: float | None = None,
                   price: float | None = None, units: int = 1, leverage: float = LEVERAGE) -> VirtualLedger:
    """`trades` compounded by entry time at fraction `f`: each trade's stake is f x the equity realised at its entry
    (exits at the same instant settle first), settled at its exit; equity never goes below 0."""
    rows = sorted(trades, key=lambda t: t.entry_utc)
    events: list[tuple[pd.Timestamp, int, int]] = []          # (time, 0 exit / 1 entry, trade index)
    for i, t in enumerate(rows):
        events += [(t.entry_utc, 1, i), (t.exit_utc, 0, i)]
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    eq, peak, dd, ruined = 1.0, 1.0, 0.0, False
    stake: dict[int, float] = {}
    for _, kind, i in events:
        if kind == 1:
            stake[i] = f * eq
            continue
        eq += stake.pop(i, 0.0) * rows[i].r
        if eq <= 0.0:
            eq, ruined = 0.0, True
        peak = max(peak, eq)
        dd = max(dd, 1.0 - eq / peak if peak > 0 else 0.0)
    ok = margin_ok(f, stop_usd, price, units, leverage) if stop_usd is not None and price is not None else None
    return VirtualLedger(f=f, trades=len(rows), shadow_trades=sum(t.shadow for t in rows), final_multiple=eq,
                         peak_multiple=peak, doubled=peak >= DOUBLE - 1e-12, max_drawdown=dd, ruined=ruined,
                         margin_ok=ok, leverage=leverage if ok is not None else None)


def _key(t: LedgerTrade) -> tuple[pd.Timestamp, str | None]:
    from goldbot.research.arm_rules_growth import session_of
    return t.entry_utc.normalize(), t.session or session_of(t.entry_utc)


def growth_read(trades: Sequence[LedgerTrade], *, refused: Mapping[str, Mapping[str, int]] | None = None,
                shadow: Sequence[LedgerTrade] = (), fractions: Sequence[float] = FRACTIONS,
                stop_usd: float | None = None, price: float | None = None, units: int = 1,
                refused_adds: Mapping[str, Mapping[str, int]] | None = None,
                add_r: Mapping[str, Sequence[float]] | None = None, add_groups: Sequence[str] = ()) -> GrowthRead:
    """GS1 against GS1C: per arm the real fills, the shadow fills and the cap refusals, a virtual ledger per fraction
    (real and shadow fills together: the uncapped sequence), and the paired excess R by (day, session). Refused
    pyramid adds (quant Q4) are only counted (`refused_adds`), added to their group's R in `ledgers_uncapped`, and
    their cells dropped in the pairing sensitivity."""
    refused, refused_adds, add_r = refused or {}, refused_adds or {}, add_r or {}
    every = [*trades, *(s.model_copy(update={"shadow": True}) for s in shadow)]
    out: dict[str, ArmRead] = {}
    for arm in GROWTH_ARMS:
        real = [t.r for t in trades if t.arm_id == arm]
        mine = [t for t in every if t.arm_id == arm]
        wide = [t.model_copy(update={"r": t.r + float(sum(add_r.get(t.group_id or "", ())))}) for t in mine]
        out[arm] = ArmRead(arm_id=arm, n=len(real), mean_r=float(np.mean(real)) if real else None,
                           sum_r=float(np.sum(real)) if real else None, shadow_n=sum(t.shadow for t in mine),
                           refused=dict(refused.get(arm, {})), refused_adds=dict(refused_adds.get(arm, {})),
                           ledgers={str(f): virtual_ledger(mine, f, stop_usd=stop_usd, price=price, units=units)
                                    for f in fractions},
                           ledgers_uncapped={str(f): virtual_ledger(wide, f) for f in fractions})
    by: dict[str, dict[tuple[pd.Timestamp, str | None], list[float]]] = {a: {} for a in GROWTH_ARMS}
    for t in every:
        if t.arm_id in by:
            by[t.arm_id].setdefault(_key(t), []).append(t.r)
    cells = {k: float(np.mean(g)) - float(np.mean(by["GS1C"][k])) for k, g in by["GS1"].items() if k in by["GS1C"]}
    drop = {c for c in (group_cell(g) for g in add_groups) if c is not None}
    excess, kept = _mean(list(cells.values())), [v for k, v in cells.items() if k not in drop]
    n = len(cells)
    return GrowthRead(arms=out, pairs=n, paired_excess_r=excess,
                      note="one week of ~12 trades cannot separate edge from luck" if n < 40 else "",
                      pairs_no_refused_add=len(kept), paired_excess_no_refused_add_r=_mean(kept))


def _mean(xs: list[float]) -> float | None:
    v = float(np.mean(xs)) if xs else None
    return v if v is None or math.isfinite(v) else None


def fold_groups(records: Iterable[Any]) -> list[LedgerTrade]:
    """Closed exploration records (`engine.exploration.ExplorationRecord`) of the growth arms as trades. Contract with
    scripts/growth_sprint_read.py: a pyramid add carries `group_id` = the first unit's client_order_id (the first
    unit its own id or None); a group is ONE trade, R = the group's net P&L over the first unit's `stop_loss`, from the
    first unit's decision close to the last exit. A group whose first unit, P&L or stop loss is not known is left out
    (never scored on a guess); without the group_id each add would count as a trade of its own."""
    groups: dict[str, list[Any]] = {}
    for r in records:
        if r.arm_id in GROWTH_ARMS and r.fill_price is not None:
            groups.setdefault(r.group_id or r.client_order_id, []).append(r)
    out: list[LedgerTrade] = []
    for key, rows in groups.items():
        first = next((r for r in rows if r.client_order_id == key), None)
        if first is None or any(r.exit_utc is None or r.pnl is None for r in rows):
            continue
        if first.stop_loss is None or not first.stop_loss > 0:
            continue
        out.append(LedgerTrade(arm_id=first.arm_id, entry_utc=first.signal_utc,
                               exit_utc=max(r.exit_utc for r in rows),
                               r=float(sum(r.pnl for r in rows)) / float(first.stop_loss), group_id=key))
    return sorted(out, key=lambda t: t.entry_utc)
