"""Pyramid adds of a growth arm (growth sprint 2026-11-01, docs/research/growth-sprint-2026-11-01.md section 3, the
part deferred to 11-03 as a protocol deviation; ADR 0065).

The rule, as built here:

* "Add a unit at each +1 R from the first unit's entry, up to 3 adds": an arm registered with `adds` (accounts.yaml,
  growth-sized arms only) queues add k once the mid reaches the first unit's fill + side x k x R, R = the stop
  distance the RiskGate set on the first unit (its ledger row), one add a group a pass, each level once (`pyramid`).
  GS1C, the control, is registered with the same `adds`, so its groups add at the same R levels of its own entry.
* "Every add goes through the RiskGate": an add is a `Pending` like any signal (`exploration._send`: every engine
  bound, then `cost_probe._place`: `RiskGate.check`, the only path to an order), sized at most the first unit's lots
  and the arm's `unit_lots` (0.01 on GS1/GS1C, quant Q2: both legs' 8 units fit the 0.10 net lots).
  The 0.10 net-lot cap (ADR 0043 X3), the pots' and the day's money, the global and the host's position caps all
  count it. An add joins its group's slot (the arm's and the pot's `max_open` count groups), never a new one. A
  refused add is a blocked candidate (`exploration.candidate`), the read's shadow fill, and its level is not tried
  again.
* "After every add, open risk stays <= 1 unit": when an add is going out (after the gate allowed it, before it is
  sent) every earlier unit's stop moves to the newest add's own 1 R stop, or tighter where the gate floored that
  wider (`group_stop`), and again on the add's fill: no earlier unit loses there, so the group holds the newest add's
  one unit, netted or counted unit by unit as the engine's bounds do. Only ever tighter. The add path moves them by
  `modify` and confirms each at the broker first (`confirm_move`, safety S1/S2): a rejected or raising modify refuses
  the add (`StopMoveFailed`), and the engine's copy takes only a confirmed level. ADR 0065: the C2 screen's
  common stop (avg - R / units) puts an add's stop up to 1.75 R away, above the arm's per-trade cap at a unit's lots,
  and the minimal move (earlier units at their average entry) leaves them a loss the arm's day counts in full.
* No profit target (quant Q3): a unit of an arm with adds carries a 100 R (`NO_TARGET_R`) target, live and in the
  shadow replay; every unit of a group closes together at its stop or at the group's end, the first signal + the
  arm's `hold_bars` (`due`, from `exploration.exits`).
* "Exits are never gated", "restart safety": every pass, whatever the entry state (halted, paused, the arm retired),
  an open group whose loss at its stops is above one unit has its earlier stops moved back to the rule (`heal`, run
  first in the pass and in its own try, safety S3). A
  restart adopts the units by their order rows or MT5 comments: an add's id is its group's signal close + k seconds
  (`add_id`, decoded by `group_of`), the group's terms come from the first unit's ledger row, and the ladder resumes
  after the highest level already sent or recorded (`exploration_obs`).
* The record: each add's `ExplorationRecord.group_id` is the first unit's client order id, the contract
  `research.growth_ledger.fold_groups` and scripts/growth_sprint_read.py fold a group into one trade on.
"""
from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.config import tf_seconds
from goldbot.engine import cost_probe

if TYPE_CHECKING:
    from goldbot.engine.exploration import Arm, Exploration, Pending
    from goldbot.engine.runner import Engine
    from goldbot.engine.store import OpenTrade
    from goldbot.execution.broker import Position, Tick

log = logging.getLogger(__name__)

MAX_ADDS = 3                          # "up to 3 adds"
# quant Q3 2026-11-01: a pyramid unit has no profit target; the broker order and the shadow replay carry one this far
NO_TARGET_R = 100.0
CENT = 0.01                           # XAUUSD price step: a stop is rounded to it, tighter (as the BXR trail)
MOVE_ACTION = "exploration_pyramid_stops"
UNKNOWN_ACTION = "exploration_pyramid_group_unknown"


class StopMoveFailed(RuntimeError):
    """An earlier unit's stop is not at the add's level: the add is not sent (open risk would pass one unit)."""


class Unit(FrozenRecord):
    """One open unit of a group: its fill, lots, the stop in force and its place on the ladder (0 = the first)."""
    position_id: int
    lots: float
    price: float
    sl: float | None
    add_no: int


class Group(FrozenRecord):
    """A group's terms, from the first unit's ledger row (persisted: read again after a restart)."""
    group_id: str                     # the first unit's client order id
    arm_id: str
    side: int
    entry: float                      # the first unit's fill price
    risk: float                       # R: the stop distance the RiskGate set on the first unit, USD/oz
    lots: float                       # the first unit's lots: an add is at most this
    target_usd: float                 # the first unit's target distance: each add's own, from its fill
    end_utc: UtcTimestamp             # the first unit's signal close + its hold: no add holds past it

    @property
    def budget(self) -> float:
        """The first unit's risk, lots x USD/oz: the most the group may lose at its stops."""
        return self.lots * self.risk


class AddPlan:
    """What `_send` needs for one add: its group, the level the earlier units move to, their positions, and the change
    in the open risk the engine's bounds count (the earlier units at that level instead of their stops now, <= 0)."""

    def __init__(self, group: Group, stop: float, earlier: list[tuple[int, OpenTrade]],
                 positions: dict[int, Position], delta: float):
        self.group, self.stop, self.earlier, self.positions, self.delta = group, stop, earlier, positions, delta


# ------------------------------------------------------------------------------------------------ identity
def add_id(eng: Engine, arm: Arm, group_id: str, k: int) -> str:
    """Add k's client order id: the order id of the group's signal close + k seconds. Deterministic, so a restart
    never sends the same add twice (`Engine.sent_ids`) and its MT5 comment names its group."""
    return make_add_id(eng.cfg.account_id, arm.arm_id, group_id, k)


def make_add_id(account_id: str, arm_id: str, group_id: str, k: int) -> str:
    """`add_id` from the account and arm ids alone."""
    from goldbot.engine.exploration import make_order_id, signal_close
    signal = signal_close(group_id)
    if signal is None:
        raise ValueError(f"{group_id!r} is not an exploration order id")
    return make_order_id(account_id, arm_id, signal + pd.Timedelta(seconds=k))


def group_of(eng: Engine, arm: Arm, cid: str | None) -> tuple[str, int] | None:
    """(the group id, the place on the ladder) of the arm's order id `cid` (`decode`)."""
    return decode(eng.cfg.account_id, arm.arm_id, tf_seconds(arm.tf), cid)


def decode(account_id: str, arm_id: str, tf_s: int, cid: str | None) -> tuple[str, int] | None:
    """(the group id, the place on the ladder) of an order or candidate id of `arm_id` on `account_id`: a first unit's
    signal close is a close of the arm's timeframe (`tf_s` seconds; k = 0), an add's is k seconds past it. None for an
    id that is neither. Pure: the growth read decodes its candidates with it."""
    from goldbot.engine.exploration import make_order_id, signal_close
    signal = signal_close(cid)
    if cid is None or signal is None or make_order_id(account_id, arm_id, signal)[:7] != cid[:7]:
        return None
    k = int(signal.timestamp()) % tf_s
    if k == 0:
        return (cid, 0) if make_order_id(account_id, arm_id, signal) == cid else None
    if k > MAX_ADDS:
        return None
    first = make_order_id(account_id, arm_id, signal - pd.Timedelta(seconds=k))
    return (first, k) if make_add_id(account_id, arm_id, first, k) == cid else None


# ------------------------------------------------------------------------------------------------ the stop rule
def group_stop(units: list[Unit], budget: float, side: int) -> float | None:
    """The common stop of every unit but the newest: the newest unit's own stop, or tighter where the group would
    still lose more than `budget` (lots x USD/oz) at its stops (an add whose stop the gate floored wider than R): the
    level S with sum(lots x side x (fill - S)) = budget - the newest's risk, S = (sum(lots x fill) - side x (budget -
    newest risk)) / sum(lots). The group then loses at most `budget` at its stops, netted. With the adds at their
    levels every earlier fill is at or behind the newest unit's stop, so no earlier unit loses at it and the engine's
    own open risk (each unit's loss alone, a locked gain at 0: `exploration._held`) is the newest unit's one unit too;
    an add filled far past its level (a gap) can leave an earlier unit a loss there, which the engine's bounds then
    count in full (a later add is refused by them, never sent over them). None with no earlier unit, or a stop
    unknown."""
    if len(units) < 2:
        return None
    newest = max(units, key=lambda u: u.add_no)
    if newest.sl is None:
        return None
    earlier = [u for u in units if u is not newest]
    lots = sum(u.lots for u in earlier)
    if not lots > 0:
        return None
    room = budget - newest.lots * side * (newest.price - newest.sl)
    return _tighter(side, newest.sl, (sum(u.lots * u.price for u in earlier) - side * room) / lots)


def tighter_round(level: float, side: int) -> float:
    """`level` on the price step, rounded the tighter way (up for a long's stop, down for a short's)."""
    steps = level / CENT
    return round((math.ceil(steps - 1e-6) if side > 0 else math.floor(steps + 1e-6)) * CENT, 2)


def _tighter(side: int, a: float | None, b: float) -> float:
    return b if a is None or side * (b - a) > 1e-9 else a


def group_loss(units: list[Unit], side: int) -> float | None:
    """What the group loses if every stop is hit, lots x USD/oz (signed: a stop past its fill locks a gain)."""
    total = 0.0
    for u in units:
        if u.sl is None:
            return None
        total += u.lots * side * (u.price - u.sl)
    return total


# ------------------------------------------------------------------------------------------------ groups
def open_units(eng: Engine, arm: Arm, group_id: str | None = None) -> dict[str, list[tuple[Unit, OpenTrade]]]:
    """The arm's open units by group (one group when given); a position closing (`_close_due`) is no longer held."""
    out: dict[str, list[tuple[Unit, OpenTrade]]] = {}
    for pid, tr in eng.open.items():
        if tr.agent_id != arm.tag or pid in eng._close_due:
            continue
        g = group_of(eng, arm, tr.client_order_id)
        price = tr.open_price if tr.open_price is not None else tr.entry
        if g is None or price is None or (group_id is not None and g[0] != group_id):
            continue
        out.setdefault(g[0], []).append((Unit(position_id=pid, lots=float(tr.lots), price=float(price), sl=tr.sl,
                                              add_no=g[1]), tr))
    return out


def group(eng: Engine, ex: Exploration, arm: Arm, group_id: str, now: pd.Timestamp) -> Group | None:
    """The group's terms from its first unit's ledger row, cached; None (journalled once) when that row is missing or
    incomplete: no add without them. A read error is not cached (tried again next pass)."""
    from goldbot.engine.exploration import LEDGER
    if group_id in ex.groups:
        return ex.groups[group_id]
    with ex.db.connection() as conn:
        rec = LEDGER.get(conn, group_id)
    g = None
    if rec is not None and rec.fill_price is not None and rec.stop_usd > 0 and rec.lots > 0:
        g = Group(group_id=group_id, arm_id=arm.arm_id, side=rec.side, entry=float(rec.fill_price),
                  risk=float(rec.stop_usd), lots=float(rec.lots), target_usd=float(rec.target_usd),
                  end_utc=rec.signal_utc + pd.Timedelta(seconds=rec.hold_s))
    else:
        eng.decisions.append({"ts": now.isoformat(), "agent": arm.tag, "action": UNKNOWN_ACTION, "group": group_id})
    ex.groups[group_id] = g
    return g


def ladder(eng: Engine, ex: Exploration, arm: Arm, group_id: str, held: list[int]) -> int:
    """The highest level of the group already tried (sent, or recorded as a candidate taken or refused), cached; after
    a restart read again from the sent ids, the open units and the candidates (`exploration_obs`)."""
    if group_id in ex.ladder:
        return ex.ladder[group_id]
    ids = {add_id(eng, arm, group_id, k): k for k in range(1, MAX_ADDS + 1)}
    seen = {k for i, k in ids.items() if i in eng.sent_ids} | set(held)
    with ex.db.connection() as conn:
        rows = conn.execute(f"SELECT candidate_id FROM exploration_obs WHERE candidate_id IN ({','.join('?' * len(ids))})",
                            tuple(ids)).fetchall()
    seen |= {ids[str(r[0])] for r in rows if str(r[0]) in ids}
    ex.ladder[group_id] = max(seen, default=0)
    return ex.ladder[group_id]


# ------------------------------------------------------------------------------------------------ every pass
def pyramid(eng: Engine, ex: Exploration, now: pd.Timestamp, tick: Tick) -> None:
    """Every pass with a quote (`exploration.on_pass`): each open group of an arm with adds is held to one unit at its
    stops (`heal`, whatever the entry state), then, unless the arm is retired, its next level is queued once the mid
    reaches it. One add a group a pass; the level is spent when queued."""
    from goldbot.engine.exploration import Pending
    mid = (tick.bid + tick.ask) / 2.0
    live: set[str] = set()
    for arm in (a for a in ex.arms.values() if a.adds > 0):
        for gid, held in open_units(eng, arm).items():
            live.add(gid)
            g = group(eng, ex, arm, gid, now)
            if g is None:
                continue
            heal(eng, ex, arm, g, now)
            if arm.arm_id in ex.retired or any(s.group_id == gid for s in ex.queue) \
                    or not any(u.add_no == 0 for u, _ in held):
                continue                              # no entry, one already waiting, or the first unit is gone
            k = ladder(eng, ex, arm, gid, [u.add_no for u, _ in held]) + 1
            if k > arm.adds or g.side * (mid - (g.entry + g.side * k * g.risk)) < 0:
                continue
            bars = int((g.end_utc - now).total_seconds() // tf_seconds(arm.tf))
            if bars < 1:
                continue                              # the group's hold ends within the bar: no add outlives it
            ex.ladder[gid] = k
            ex.queue.append(Pending(arm_id=arm.arm_id, side=g.side, bar_ts=now, stop_usd=g.risk,
                                    target_usd=NO_TARGET_R * g.risk, hold_bars=bars, group_id=gid, add_no=k))
    for cache in (ex.groups, ex.ladder):            # a closed group's terms and ladder are read no more
        for gid in [k for k in cache if k not in live]:
            del cache[gid]


def heal(eng: Engine, ex: Exploration, arm: Arm, g: Group, now: pd.Timestamp, force: bool = False) -> None:
    """Hold the group to one unit at its stops: when its loss at the stops is above `g.budget` (a cent a lot of
    rounding allowed; `force`: always, after an add's fill), the earlier units' stops move to `group_stop`, only ever
    tighter (`Engine._tighten_stop`: a close at market when the price is already through it). Exits are never gated:
    this runs on every pass whatever the entry state."""
    held = open_units(eng, arm, g.group_id).get(g.group_id, [])
    units = [u for u, _ in held]
    loss = group_loss(units, g.side)
    if len(units) < 2 or (not force and loss is not None and loss <= g.budget + CENT * sum(u.lots for u in units)):
        return
    level = group_stop(units, g.budget, g.side)
    if level is None:
        return
    stop = tighter_round(level, g.side)
    newest = max(u.add_no for u in units)
    earlier = [(u.position_id, tr) for u, tr in held if u.add_no != newest]
    if all(tr.sl is not None and g.side * (tr.sl - stop) >= -1e-9 for _, tr in earlier):
        return
    positions = eng._read_positions("pyramid stops moved on the next pass")
    if positions is None:
        return
    move(eng, g, stop, earlier, {p.position_id: p for p in positions}, now)


def move(eng: Engine, g: Group, stop: float, earlier: list[tuple[int, OpenTrade]], positions: dict[int, Position],
         now: pd.Timestamp) -> bool:
    """`heal`'s move: the earlier units' stops to `stop` (never looser) by the engine's own stop move, which keeps the
    level it expects (`tr.sl`; reconciliation sends it again if a modify did not stick, a close at market when the
    price is through it). A unit whose move raises is logged and the others still move; journalled. True when every
    one still open is there."""
    for pid, tr in earlier:
        pos = positions.get(pid)
        if pos is None or pid not in eng.open:
            continue
        try:
            eng._tighten_stop(pid, tr, pos, _tighter(g.side, tr.sl, stop))
        except Exception:
            log.exception("pyramid stop of position %s not moved; tried again next pass", pid)
    eng._save_orders()
    eng.decisions.append({"ts": now.isoformat(), "agent": "explore:" + g.arm_id, "action": MOVE_ACTION,
                          "group": g.group_id, "sl": stop, "positions": [pid for pid, _ in earlier]})
    return all(tr.sl is not None and g.side * (tr.sl - stop) >= -1e-9
               for pid, tr in earlier if pid in eng.open and pid not in eng._close_due)


def confirm_move(eng: Engine, g: Group, stop: float, earlier: list[tuple[int, OpenTrade]],
                 positions: dict[int, Position], now: pd.Timestamp) -> None:
    """The add path's move (safety S1/S2): each earlier unit's broker stop to `stop` by `modify`, its result read, a
    raise caught per position; then the broker's positions read again. The engine's copy (`tr.sl`) takes only a
    broker stop seen tighter, so open risk is never counted below what the broker holds. Raises `StopMoveFailed`
    (the add is not sent) unless every earlier unit still open has its broker stop at `stop` or tighter."""
    failed: list[str] = []
    for pid, tr in earlier:
        if pid not in eng.open or pid in eng._close_due:
            continue
        pos = positions.get(pid)
        if pos is None:
            failed.append(f"{pid}:unseen")
            continue
        if pos.sl is not None and g.side * (pos.sl - stop) >= -1e-9:
            continue
        try:
            res = eng.broker.modify(pid, stop, pos.tp)
            if not res.ok:
                failed.append(f"{pid}:{res.retcode}")
        except Exception as exc:
            log.warning("pyramid stop of position %s: modify raised (%s); the add is not sent", pid,
                        type(exc).__name__)
            failed.append(f"{pid}:{type(exc).__name__}")
    seen = eng._read_positions("the add is not sent; its stops stand")
    live = {p.position_id: p for p in seen} if seen is not None else {}
    for pid, tr in earlier:
        if pid not in eng.open or pid in eng._close_due:
            continue
        got = live.get(pid)
        if got is not None and got.sl is not None and (tr.sl is None or g.side * (got.sl - tr.sl) > 1e-9):
            tr.sl = got.sl                            # confirmed at the broker: the engine's copy follows it
        if got is None or got.sl is None or g.side * (got.sl - stop) < -1e-9:
            failed.append(f"{pid}:not_at_level")
    eng._save_orders()
    eng.decisions.append({"ts": now.isoformat(), "agent": "explore:" + g.arm_id, "action": MOVE_ACTION,
                          "group": g.group_id, "sl": stop, "positions": [pid for pid, _ in earlier],
                          "ok": not failed, **({"failed": sorted(set(failed))} if failed else {})})
    if failed:
        raise StopMoveFailed(g.group_id)


def due(eng: Engine, arm: Arm, tr: OpenTrade, at: pd.Timestamp) -> bool:
    """Quant Q3: a unit of a pyramid arm at its group's end, the first unit's signal close + the arm's `hold_bars`
    bars (from the order id: no read, restart-safe). Every unit of the group closes then, together."""
    from goldbot.engine.exploration import signal_close
    g = group_of(eng, arm, tr.client_order_id)
    start = signal_close(g[0]) if g is not None else None
    if start is None:
        return False
    return at >= start + pd.Timedelta(seconds=int(arm.spec.params.get("hold_bars", 1)) * tf_seconds(arm.tf))


# ------------------------------------------------------------------------------------------------ one add
def plan(eng: Engine, ex: Exploration, arm: Arm, s: Pending, tick: Tick, price: float, lots: float) -> AddPlan | str:
    """The add `s` at `price` and at most `lots`: the level its earlier units move to and the change in open risk the
    engine's bounds count, or the reason it is refused (fail closed: an unknown group, stop, loss or position)."""
    g = ex.groups.get(s.group_id or "")
    if g is None:
        return "group_unknown"
    held = open_units(eng, arm, g.group_id).get(g.group_id, [])
    if not any(u.add_no == 0 for u, _ in held):
        return "group_closed"                     # the first unit is gone: the group takes no more adds
    add = Unit(position_id=-1, lots=lots, price=price, sl=price - g.side * s.stop_usd, add_no=s.add_no)
    level = group_stop([*(u for u, _ in held), add], g.budget, g.side)
    if level is None:
        return "open_risk_unknown"
    stop = tighter_round(level, g.side)
    exit_px = tick.bid if g.side > 0 else tick.ask
    if g.side * (exit_px - stop) <= 0:
        return "add_stop_through_price"
    delta = 0.0
    for u, tr in held:
        from goldbot.engine.exploration import _held
        now_loss = _held(eng, ex, u.position_id, tr)
        dist = max(0.0, g.side * (u.price - _tighter(g.side, u.sl, stop)))
        new_loss = 0.0 if dist <= 0 else cost_probe.stop_loss(eng, g.side, u.price, dist, u.lots)
        if now_loss is None or new_loss is None:
            return "open_risk_unknown"
        delta += min(0.0, new_loss - now_loss)
    positions = eng._read_positions("the add is refused; tried at its next level")
    if positions is None:
        return "positions_unknown"
    return AddPlan(g, stop, [(u.position_id, tr) for u, tr in held], {p.position_id: p for p in positions}, delta)


def before_send(eng: Engine, p: AddPlan, now: pd.Timestamp) -> None:
    """`cost_probe._place`'s hook once the gate allowed the add: the earlier units to the plan's level first, so the
    group never holds more than one unit at its stops; raises `StopMoveFailed` (nothing sent) unless the broker
    confirms every one (`confirm_move`)."""
    confirm_move(eng, p.group, p.stop, p.earlier, p.positions, now)


def journal_start(eng: Engine, ex: Exploration) -> None:
    """Each arm with adds, journalled at the engine's start (`exploration_pyramid`): the switch time the read splits
    single-unit and pyramided groups on."""
    for a in sorted((a for a in ex.arms.values() if a.adds > 0), key=lambda a: a.arm_id):
        eng.decisions.append({"ts": time.time(), "agent": a.tag, "action": "exploration_pyramid", "arm": a.arm_id,
                              "adds": a.adds})


def counts_as_slot(eng: Engine, arm: Arm | None, tr: Any) -> bool:
    """Whether an open position takes its arm's and its pot's slot: an add joins its group's (False), anything else
    takes one."""
    if arm is None or arm.adds <= 0:
        return True
    g = group_of(eng, arm, getattr(tr, "client_order_id", None))
    return g is None or g[1] == 0
