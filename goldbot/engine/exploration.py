"""Exploration arms: minimum-lot demo entries that trade a simple rule to MEASURE its edge on the broker (plan
2026-10-25, lane L2; the RiskGate's purpose `exploration`, lane L1).

The plan's rules, as built here:

* An arm (`Arm`) is a rule family with its parameters on one timeframe, a side rule, its own stop and target (ATR(14)
  multiples of its timeframe) and its own hold (`hold_bars` of its timeframe). "Every signal is sent at the minimum lot
  with its own stop, hold and target", or refused and journalled; never resized, never with a tightened stop.
* When: arms on 15m and above decide at the engine's bar closes (`step`), after the engine's own decisions, so the
  live agents' decisions are the same with exploration on. Arms on 1m and 5m decide on the engine's pass (`on_pass`)
  "reading only already-complete minutes (bars_1m + ticks_to_1m(eng.ticks) at close + 1.5 s)": a minute joins once
  its close is GRACE_S behind the clock. Nothing here writes `bars_1m`, `ticks` or `_closed_through`.
* Sending: at most PER_PASS orders a pass, "FIFO with fair rotation across arms" (`_order`); a signal older than its
  staleness (10 s on 1m, 30 s on 5m, 60 s on 15m and above) is dropped and journalled `exploration_stale`. Each order
  goes through the cost probe's order core (`cost_probe._place`: `RiskGate.check`, the only path to an order, then
  `Engine._execute`), on an Intent of purpose `exploration` carrying its `arm_id`, so the gate's fence applies (demo
  probe_only account, demo terminal, the minimum lot, a registered arm, the arm's per-trade cap).
* Caps the engine enforces (the gate does not; trading-safety review of L1, 2026-10-25):
  - global: open exploration positions at most min(`max_open_total`, floor(CAP_SHARE x min(notional cap, margin
    cap))), read live: the notional cap is NOTIONAL_X x equity / the minimum lot's notional, the margin cap the number
    of minimum lots that keep the margin level at MARGIN_LEVEL (300%) on the larger of the broker's margin and 1:20;
  - the exploration budget: `budget_share` x the day's loss limit, against today's realised exploration loss + the open
    exploration positions' loss at their stops + the new entry's (as the cost probe's `budget_refusal`);
  - each arm's day: its share of that budget (`weights`: the research weights when lane L3's reader is there, equal
    shares until then), never above the arm's own ceiling `daily_gbp`;
  - each arm's trade: the loss at the stop PLUS the measured round trip (spread, slippage, commission: the engine's
    `_cost_atr`) within the arm's per-trade cap (`RiskLimits.exploration_arms`), which the gate checks on the loss at
    the stop alone (trading-safety LOW, 2026-10-25);
  - `max_open` positions of one arm.
  An unknown figure (equity, the conversion, a position's stop loss, the ledger) refuses: fail closed.
* Exits are never gated: the engine's server stop and target, its stop check, the Friday cut and the kill switch act
  on exploration positions as on any of this engine's magic range (`Engine._own`). A 1m/5m arm's hold is judged on the
  host clock on every pass with or without a quote (`exits`, from `Engine._retry_closes`), halted or not; a 15m+ arm's
  is the engine's time barrier. A position whose arm is unknown (no longer configured) is closed at once.
* Identity: magic_base + 95 (`goldbot.engine.magic`), order id and MT5 comment `ex<arm5>-<epoch10>-<h8>` (27 chars,
  inside MT5's 29). After a restart a position is adopted by its order row or its comment and its hold resumes from
  the broker's open time.
* The record: one `ExplorationRecord` per order in aux.db `exploration_trades`, never closed_trades.jsonl. A close for
  any reason but the arm's own stop, target or hold (the Friday cut, a kill switch, a moved stop, a manual close) is
  `censored`: the label did not run to its own barrier.
"""
from __future__ import annotations

import hashlib
import logging
import math
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal, cast

import pandas as pd
from pydantic import Field, ValidationError, model_validator

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.config import tf_seconds
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.timeutil import epoch_ns
from goldbot.db import Database, RecordRepo
from goldbot.engine import auto_entry, cost_probe
from goldbot.features.technical import atr
from goldbot.labels.triple_barrier import BarrierSpec

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.engine.store import OpenTrade
    from goldbot.execution.broker import OrderResult, Position, Tick
    from goldbot.risk import Intent
    from goldbot.risk.gate import GateDecision
    from goldbot.specialists.base import Specialist

log = logging.getLogger(__name__)

PREFIX = "explore:"                 # an exploration trade's tag (open trade, order row, journal): never an agent id
COMMENT = "ex"                      # its client order id, and so its MT5 comment, starts with this
HOLD_EXIT = "exploration_hold"      # a 1m/5m arm's hold, on the host clock
ORPHAN_EXIT = "exploration_orphan"  # a position whose arm is not configured: no rule to hold it to, closed at once
PER_PASS = 4                        # orders a pass at most (plan: "<= 4 entries per pass")
CAP_SHARE = 0.9                     # the global cap's share of the notional and margin caps
NOTIONAL_X = 6.0                    # notional cap: 6 x equity
MARGIN_LEVEL = 3.0                  # margin cap: the margin level kept at 300%
FALLBACK_LEVERAGE = 20.0            # the gate's 1:20 margin figure: the margin used is never below it
GRACE_S = 1.5                       # a minute is complete GRACE_S after its close
LOOKBACK = 200                      # bars of an arm's timeframe its rule and ATR read
ATR_N = 14
SUB_15M = ("1m", "5m")
SWING = "swing"                     # ADR 0043: the one style allowed to hold over the weekend
MAX_NET_LOTS = 0.10                 # ADR 0043 X3: net same-side exposure on the account, lots
DD_WARN, DD_PAUSE = 0.06, 0.08      # ADR 0043 section 2: drawdown from the mark: warn, pause
# a close by the arm's own barrier: anything else censors the label
NATURAL = frozenset({HOLD_EXIT, "time_exit", "stop", "target", "engine_stop_close"})

Signal = Callable[[pd.DataFrame, Mapping[str, float]], int]


def stale_s(tf: str) -> float:
    """How old a signal may be when its order is sent: 10 s on 1m, 30 s on 5m, 60 s on 15m and above."""
    return 10.0 if tf == "1m" else 30.0 if tf == "5m" else 60.0


# ------------------------------------------------------------------------------------------------ rule families
def _donchian(m: pd.DataFrame, p: Mapping[str, float]) -> int:
    """+1 when the bar closes above the highest high of the `n` bars before it, -1 below their lowest low."""
    n = int(p.get("n", 20))
    if n < 1 or len(m) < n + 1:
        return 0
    c = float(m["close"].iloc[-1])
    hi, lo = m["high"].to_numpy()[-n - 1:-1], m["low"].to_numpy()[-n - 1:-1]
    return 1 if c > hi.max() else -1 if c < lo.min() else 0


def _roc(m: pd.DataFrame, p: Mapping[str, float]) -> int:
    """The sign of the `n`-bar change in close when it exceeds `k` ATR, else 0."""
    n, k = int(p.get("n", 10)), float(p.get("k", 1.0))
    if n < 1 or len(m) < n + 1 or not math.isfinite(float(m["atr"].iloc[-1])):
        return 0
    c = m["close"].to_numpy()
    move = float(c[-1] - c[-1 - n])
    return 1 if move > k * float(m["atr"].iloc[-1]) else -1 if move < -k * float(m["atr"].iloc[-1]) else 0


# family -> rule: mid OHLC bars of the arm's timeframe (with `atr`), the last row the bar just closed -> +1, -1 or 0
FAMILIES: dict[str, Signal] = {"donchian": _donchian, "roc": _roc}


class Arm(FrozenRecord):
    """One exploration arm (the architect's plan 2026-10-25). `stop` and `target` are ATR(14) multiples of `tf`;
    `daily_gbp` is the arm's ceiling on a day's loss (account currency, GBP), its share of the budget never above it."""
    arm_id: str = Field(min_length=1, max_length=64)
    family: str
    params: dict[str, float] = Field(default_factory=dict)
    tf: Literal["1m", "5m", "15m", "1h", "4h", "1d"]
    side_rule: Literal["follow", "fade", "long", "short"] = "follow"
    stop: float = Field(gt=0)
    hold_bars: int = Field(ge=1)
    target: float = Field(gt=0)
    max_open: int = Field(1, ge=1)
    daily_gbp: float = Field(gt=0)
    # ADR 0043: the sleeve (pot) the arm draws on, and a random control's fixed share of it (controls are the null:
    # never allocated by results); None = a strategy arm, which splits the rest by the research weights
    style: str
    control_share: float | None = Field(None, gt=0, le=1)

    @property
    def key(self) -> str:
        """Five hex characters of the arm id: its part of the order id and MT5 comment (adoption by comment)."""
        return hashlib.sha1(self.arm_id.encode()).hexdigest()[:5]

    @property
    def tag(self) -> str:
        return PREFIX + self.arm_id

    @property
    def hold_s(self) -> int:
        return self.hold_bars * tf_seconds(self.tf)

    def side(self, raw: int) -> int:
        if self.side_rule == "fade":
            return -raw
        if self.side_rule == "long":
            return raw if raw > 0 else 0
        if self.side_rule == "short":
            return raw if raw < 0 else 0
        return raw


class Pot(FrozenRecord):
    """ADR 0043 section 1: a style's sleeve. Today's realised loss + open risk at the stops + the new trade's loss at
    its stop <= `gbp` (account currency); at most `max_open` of its arms' positions open."""
    gbp: float = Field(gt=0)
    max_open: int = Field(ge=1)


class ExplorationConfig(FrozenRecord):
    """The engine's exploration block: the account's `exploration` (config/accounts.yaml, `accounts.Exploration`:
    enabled, budget_share, max_open_total, pots) with the arms' definitions."""
    enabled: bool = False
    budget_share: float = Field(0.1, gt=0, le=1)
    max_open_total: int = Field(1, ge=1)
    pots: dict[str, Pot] = Field(default_factory=dict)
    arms: tuple[Arm, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> ExplorationConfig:
        ids, keys = [a.arm_id for a in self.arms], [a.key for a in self.arms]
        if len(set(ids)) != len(ids) or len(set(keys)) != len(keys):
            raise ValueError("exploration arm ids (and their 5-character keys) must be unique")
        if missing := sorted({a.style for a in self.arms} - set(self.pots)):
            raise ValueError(f"exploration arms name styles with no pot: {missing}")
        for style in self.pots:
            if sum(a.control_share or 0.0 for a in self.arms if a.style == style) > 1 + 1e-9:
                raise ValueError(f"exploration controls' shares of the {style} pot exceed 1")
        return self


class ExplorationRecord(FrozenRecord):
    """One exploration order and, once closed, its exit (aux.db `exploration_trades`). Prices USD per oz; money in
    the account currency as the broker reports it."""
    client_order_id: str
    account_id: str
    arm_id: str
    family: str
    tf: str
    side: int
    lots: float
    signal_utc: UtcTimestamp                # the signal bar's close
    sent_utc: UtcTimestamp
    atr_usd: float
    stop_usd: float                         # the stop distance the gate set
    target_usd: float
    hold_s: int
    requested_price: float | None = None
    spread_usd: float | None = None
    latency_ms: float | None = None
    retcode: int | None = None
    position_id: int | None = None
    fill_price: float | None = None
    stop_loss: float | None = None          # the loss at the stop when sent, account currency
    cost: float | None = None               # the measured round trip at the lots sent, account currency
    exit_utc: UtcTimestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    swap: float | None = None
    commission: float | None = None
    pnl: float | None = None                # net, from the broker's deals (None: not readable)
    censored: bool | None = None            # closed by anything but the arm's own stop, target or hold
    fx_acct_per_usd: float | None = None    # the broker's account currency per USD at the order (None: not read)


def _ns(ts: pd.Timestamp) -> int:
    return int(epoch_ns(pd.DatetimeIndex([ts]))[0])


LEDGER: RecordRepo[ExplorationRecord] = RecordRepo(
    "exploration_trades", ExplorationRecord, key="client_order_id", tolerant=True, columns={
        "client_order_id": lambda r: r.client_order_id, "account_id": lambda r: r.account_id,
        "arm_id": lambda r: r.arm_id, "position_id": lambda r: r.position_id, "ts_ns": lambda r: _ns(r.sent_utc),
        "closed": lambda r: int(r.exit_utc is not None),
        "exit_ns": lambda r: _ns(r.exit_utc) if r.exit_utc is not None else None})
# the table, until it moves into goldbot/db/migrations.py (lane L3 owns the migrations): additive, as cost_probes
DDL = ("""CREATE TABLE IF NOT EXISTS exploration_trades (
            client_order_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, arm_id TEXT NOT NULL, position_id INTEGER,
            ts_ns INTEGER NOT NULL, closed INTEGER NOT NULL, exit_ns INTEGER, body TEXT NOT NULL) STRICT""",
       "CREATE INDEX IF NOT EXISTS exploration_trades_account ON exploration_trades(account_id, exit_ns)")


def ledger_db(state_dir: str) -> Database:
    db = Database.open(state_dir, "aux")
    with db.write() as conn:
        for s in DDL:
            conn.execute(s)
    return db


# ------------------------------------------------------------------------------------------------ the engine's state
class Pending(FrozenRecord):
    arm_id: str
    side: int
    bar_ts: UtcTimestamp                    # the signal bar's close (visible_at)
    atr_usd: float


class _ArmAgent:
    """What `Engine._execute` and `_adopt` read of an agent, for an arm: its tag (own magic), family, timeframe and a
    time barrier. A 15m+ arm's barrier is its hold (base bars = hold_bars x tf / decision tf); a 1m/5m arm's is a
    backstop one base bar past its hold (the hold itself is `exits`, on the host clock). No exit policy."""

    def __init__(self, arm: Arm, decision_tf: str):
        self.arm, self.agent_id, self.family, self.timeframe = arm, arm.tag, arm.family, arm.tf
        sec, base = tf_seconds(arm.tf), tf_seconds(decision_tf)
        bars = arm.hold_bars if arm.tf not in SUB_15M else math.ceil(
            (math.ceil(arm.hold_s / base) + 1) * base / sec)
        self.label_spec = BarrierSpec(target_atr=arm.target, stop_atr=arm.stop, max_bars=max(0, bars - 1),
                                      name="exploration")
        self.exit_spec = None
        self.shadow_only = self.requires_tv = False


class Exploration:
    """The engine's exploration state: the arms, the ledger, the signals waiting, the rotation and what was read."""

    def __init__(self, eng: Engine, cfg: ExplorationConfig):
        unknown = sorted({a.family for a in cfg.arms} - set(FAMILIES))
        if unknown:
            raise ValueError(f"exploration arms name unknown rule families: {unknown}")
        self.cfg = cfg
        self.arms = {a.arm_id: a for a in cfg.arms}
        self.by_key = {a.key: a for a in cfg.arms}
        self.db = ledger_db(eng.cfg.state_dir)
        self.queue: deque[Pending] = deque()
        self.last_arm: str | None = None
        self.last_bar: dict[str, pd.Timestamp] = {}
        self.last_close = eng._closed_through
        self.last_minute: pd.Timestamp | None = None
        self.open_loss: dict[int, float] = {}       # position id -> its loss at the stop when sent
        self.weights_cache: tuple[pd.Timestamp, dict[str, float]] | None = None

    @classmethod
    def start(cls, eng: Engine) -> Exploration | None:
        cfg: ExplorationConfig | None = eng.cfg.exploration_config
        if cfg is None or not cfg.enabled:
            return None
        if eng.cfg.mode != "demo" or not eng.cfg.probe_only:
            raise ValueError("exploration runs on demo probe_only accounts only (plan 2026-10-25)")
        return cls(eng, cfg)


def is_exploration(agent_id: str | None) -> bool:
    return bool(agent_id) and str(agent_id).startswith(PREFIX)


def agent_for(eng: Engine, tag: str | None) -> Specialist | None:
    ex = eng.exploration
    arm = ex.arms.get(str(tag).removeprefix(PREFIX)) if ex is not None and tag else None
    return _agent(eng, arm) if arm is not None else None


def _agent(eng: Engine, arm: Arm) -> Specialist:
    return cast("Specialist", _ArmAgent(arm, eng.cfg.decision_tf))


def adoption(eng: Engine, p: Position) -> tuple[Specialist | None, str]:
    """An exploration position found with no order row: its arm from the comment's key (`ex<arm5>-`), else orphan
    (closed at once by `exits`)."""
    ex = eng.exploration
    c = str(p.comment or "")
    arm = ex.by_key.get(c[len(COMMENT):len(COMMENT) + 5]) if ex is not None and c.startswith(COMMENT) else None
    return (_agent(eng, arm), arm.tag) if arm is not None else (None, PREFIX + "orphan")


def order_id(eng: Engine, arm: Arm, bar_ts: pd.Timestamp) -> str:
    """`ex<arm5>-<epoch10>-<h8>`: 27 characters, the whole id fits MT5's 29-character comment (MT5_COMMENT_MAX)."""
    h = hashlib.sha1(f"{eng.cfg.account_id}|{arm.arm_id}|{bar_ts.isoformat()}".encode()).hexdigest()[:8]
    return f"{COMMENT}{arm.key}-{int(bar_ts.timestamp()):010d}-{h}"


def _open(eng: Engine) -> list[int]:
    return [pid for pid, t in eng.open.items() if is_exploration(t.agent_id)]


def _in_flight(eng: Engine) -> bool:
    from goldbot.engine import unconfirmed
    return any(o.status in ("sending", unconfirmed.AMBIGUOUS) and is_exploration(o.agent_id)
               for o in eng._orders.values())


# ------------------------------------------------------------------------------------------------ signals
def _bars(frame_1m: pd.DataFrame, tf: str, through: pd.Timestamp) -> pd.DataFrame | None:
    """Mid bars of `tf` with ATR, the last one closing exactly at `through`; None when that bar is not there."""
    sec = tf_seconds(tf)
    src = frame_1m if tf == "1d" else frame_1m[frame_1m["ts_utc"] >= through - pd.Timedelta(seconds=sec * (LOOKBACK + 1))]
    bars = src if tf == "1m" else resample_bars(src, tf)
    bars = bars[bars["visible_at"] <= through]
    if len(bars) < ATR_N + 1 or pd.Timestamp(bars["visible_at"].iloc[-1]) != through:
        return None
    m = mid(bars.tail(LOOKBACK).reset_index(drop=True))
    m["atr"] = atr(m, ATR_N).to_numpy()
    return m


def _signal(ex: Exploration, arm: Arm, m: pd.DataFrame, through: pd.Timestamp) -> None:
    if ex.last_bar.get(arm.arm_id) == through:
        return
    ex.last_bar[arm.arm_id] = through
    a = float(m["atr"].iloc[-1])
    side = arm.side(FAMILIES[arm.family](m, arm.params)) if math.isfinite(a) and a > 0 else 0
    if side:
        ex.queue.append(Pending(arm_id=arm.arm_id, side=side, bar_ts=through, atr_usd=a))


def step(eng: Engine, close_ts: pd.Timestamp) -> None:
    """At a base bar close (after the engine's decisions): the 15m+ arms whose bar closes now queue their signals."""
    ex = eng.exploration
    if ex is None:
        return
    arms = [a for a in ex.arms.values() if a.tf not in SUB_15M]
    if not arms or eng.bars_1m.empty:
        return
    complete = eng.bars_1m[eng.bars_1m["visible_at"] <= close_ts]
    for tf in sorted({a.tf for a in arms}, key=tf_seconds):
        if tf != "1d" and int(close_ts.timestamp()) % tf_seconds(tf):
            continue
        m = _bars(complete, tf, close_ts)
        if m is not None:
            for arm in (a for a in arms if a.tf == tf):
                _signal(ex, arm, m, close_ts)


def _minutes(eng: Engine, ex: Exploration, now: pd.Timestamp) -> None:
    """The 1m/5m arms: once a minute is GRACE_S past its close, the complete minutes (bars_1m and the engine's
    unbuilt ticks, both copied, never written) are read and the arms whose bar closed queue their signals."""
    through = (now - pd.Timedelta(seconds=GRACE_S)).floor("min")
    if ex.last_minute is not None and through <= ex.last_minute:
        return
    ex.last_minute = through
    arms = [a for a in ex.arms.values() if a.tf in SUB_15M]
    tfs = sorted({a.tf for a in arms if int(through.timestamp()) % tf_seconds(a.tf) == 0}, key=tf_seconds)
    if not tfs:
        return
    since = through - pd.Timedelta(seconds=tf_seconds(tfs[-1]) * (LOOKBACK + 1))
    b = eng.bars_1m
    parts = [b[b["ts_utc"] >= since]] if not b.empty else []
    ticks = [t for t in eng.ticks if t.ts_utc < through]
    if ticks:
        parts.append(ticks_to_1m(pd.DataFrame({"ts_utc": [t.ts_utc for t in ticks], "bid": [t.bid for t in ticks],
                                               "ask": [t.ask for t in ticks]}), DEFAULT_SESSIONS))
    parts = [p for p in parts if not p.empty]
    if not parts:
        return
    frame = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    frame = frame.drop_duplicates("ts_utc", keep="last").sort_values("ts_utc").reset_index(drop=True)
    for tf in tfs:
        m = _bars(frame, tf, through)
        if m is not None:
            for arm in (a for a in arms if a.tf == tf):
                _signal(ex, arm, m, through)


def on_pass(eng: Engine, tick: Tick) -> None:
    """Every engine pass with a quote, after its exits and its bar close: queue the arms' new signals and send. Never
    raises (an error is journalled `exploration_error`; the pass and every exit go on)."""
    ex = eng.exploration
    if ex is None:
        return
    try:
        now = eng._now(tick)
        ct = eng._closed_through
        if ct is not None and (ex.last_close is None or ct > ex.last_close):
            ex.last_close = ct
            step(eng, ct)
        _minutes(eng, ex, now)
        if ex.queue:
            _enter(eng, ex, now, tick)
    except Exception as exc:
        log.exception("exploration pass failed; no exploration entry this pass")
        eng.decisions.append({"ts": time.time(), "agent": None, "action": "exploration_error",
                              "error": type(exc).__name__})


# ------------------------------------------------------------------------------------------------ entries
def _order(ex: Exploration) -> list[Pending]:
    """The queue in sending order: rounds over the arms, one signal of each per round, arms ordered by their oldest
    signal (FIFO) and then by the rotation (the first arm id after the last one served)."""
    by_arm: dict[str, deque[Pending]] = {}
    for s in ex.queue:
        by_arm.setdefault(s.arm_id, deque()).append(s)
    last = ex.last_arm

    def rank(arm_id: str) -> tuple[pd.Timestamp, int, str]:
        return by_arm[arm_id][0].bar_ts, 0 if last is None or arm_id > last else 1, arm_id

    order = sorted(by_arm, key=rank)
    out: list[Pending] = []
    while any(by_arm.values()):
        out += [by_arm[a].popleft() for a in order if by_arm[a]]
    return out


def shares(ex: Exploration, now: pd.Timestamp) -> dict[str, float]:
    """Each arm's share of its pot (ADR 0043 "within a sleeve"): a control its fixed `control_share` (controls are the
    null, never allocated by results); the strategy arms the rest x their research share (lane L3's
    `exploration_data.weights`: the newest bandit run, capped, never re-normalised upward; equal shares when it is
    missing, stale or unreadable). Read at most once a minute."""
    from goldbot.research.exploration_data import weights
    minute = now.floor("min")
    if ex.weights_cache is not None and ex.weights_cache[0] == minute:
        return ex.weights_cache[1]
    out: dict[str, float] = {}
    for style in ex.cfg.pots:
        arms = [a for a in ex.arms.values() if a.style == style]
        rest = max(0.0, 1.0 - sum(a.control_share or 0.0 for a in arms))
        strat = [a.arm_id for a in arms if a.control_share is None]
        try:
            w = weights(ex.db, strat, now=now) if strat else {}
        except Exception as exc:                    # the reader falls back itself; this is the last line
            log.warning("exploration weights unreadable (%s): equal shares", type(exc).__name__)
            w = {a: 1.0 / len(strat) for a in strat}
        for a in arms:
            v = a.control_share if a.control_share is not None else rest * float(w.get(a.arm_id, 0.0))
            out[a.arm_id] = v if math.isfinite(v) and v >= 0 else 0.0
    ex.weights_cache = (minute, out)
    return out


def realised(eng: Engine, ex: Exploration, since: pd.Timestamp) -> dict[str, float]:
    """arm id -> today's realised loss (>= 0, net of the arm's profits). A row closed with no P&L counts at its loss at
    the stop plus its cost. Raises when the ledger cannot be read (the caller refuses: fail closed)."""
    with ex.db.connection() as conn:
        rows = LEDGER.select(conn, "account_id = ? AND closed = 1 AND exit_ns >= ?",
                             (eng.cfg.account_id, _ns(since)))
    pnl: dict[str, float] = {}
    for r in rows:
        v = r.pnl if r.pnl is not None else -((r.stop_loss or 0.0) + (r.cost or 0.0))
        pnl[r.arm_id] = pnl.get(r.arm_id, 0.0) + v
    return {a: max(0.0, -v) for a, v in pnl.items()}


def drawdown(eng: Engine) -> float | None:
    """The account's drawdown from its high-water mark (the gate's stage measure), 0.06 = -6%; None when unknown."""
    st = eng.state
    if not (math.isfinite(st.equity) and math.isfinite(st.balance_closed_hwm) and st.balance_closed_hwm > 0):
        return None
    return max(0.0, 1.0 - st.equity / st.balance_closed_hwm)


def paused(eng: Engine, now: pd.Timestamp) -> bool:
    """ADR 0043 section 2 (X4): on a probe_only account at a drawdown of DD_PAUSE (8%, the gate's SIZE_DOWN) or more,
    every exploration and mirror-probe entry pauses (`exploration_paused_dd`; the scheduled cost probes go on); at
    DD_WARN (6%) a warning is journalled once a day. An unknown drawdown pauses (fail closed). Exits are never
    gated. False on any other account (the rule is the probe host's)."""
    if not eng.cfg.probe_only:
        return False
    dd = drawdown(eng)
    if dd is None or dd >= DD_PAUSE - 1e-12:
        return True
    day = now.floor("D")
    if dd >= DD_WARN - 1e-12 and _warned.get(id(eng)) != day:
        _warned[id(eng)] = day
        eng.decisions.append({"ts": now.isoformat(), "agent": None, "action": "exploration_dd_warning",
                              "drawdown": round(dd, 4)})
    return False


_warned: dict[int, pd.Timestamp] = {}


def crosses_friday(eng: Engine, now: pd.Timestamp, hold_s: int) -> bool:
    """ADR 0043 X3: a scalp or intraday entry whose maximum hold reaches the Friday weekend cut (server time)."""
    from goldbot.data.timeutil import server_wall
    end = now + pd.Timedelta(seconds=hold_s)
    h, m = (int(x) for x in eng.cfg.weekend_cut.split(":"))
    srv_now, srv_end = server_wall(pd.DatetimeIndex([now, end]), eng.cfg.server_tz)
    days_to_fri = (4 - srv_now.weekday()) % 7
    cut = (srv_now.normalize() + pd.Timedelta(days=days_to_fri, hours=h, minutes=m))
    if cut <= srv_now:
        cut += pd.Timedelta(days=7)
    return bool(srv_end >= cut)


def _held(eng: Engine, ex: Exploration, pid: int, tr: OpenTrade) -> float | None:
    """A position's loss at its current stop (0 at or past its entry), the figure sent when the stop is unmoved."""
    if pid in ex.open_loss and tr.sl == tr.initial_sl:
        return ex.open_loss[pid]
    entry = tr.open_price if tr.open_price is not None else tr.entry
    if tr.sl is None or entry is None:
        return None
    dist = tr.side * (entry - tr.sl)
    return 0.0 if dist <= 0 else cost_probe.stop_loss(eng, tr.side, entry, dist, tr.lots)


def global_cap(eng: Engine, ex: Exploration, price: float, terms: Mapping[str, Any]) -> int | None:
    """min(max_open_total, floor(CAP_SHARE x min(notional cap, margin cap))) from equity now; None when unknown."""
    from goldbot.risk.gate import broker_margin
    eq, lot, oz, fx = eng.state.equity, terms.get("volume_min"), terms.get("contract_oz"), terms.get("acct_per_usd")
    if not (math.isfinite(eq) and eq > 0) or lot is None or oz is None or fx is None:
        return None
    notional = price * float(oz) * float(lot) * float(fx)
    fn = broker_margin(eng.broker, eng.cfg.symbol)
    try:
        broker = fn(1, float(lot), price) if fn is not None else None
    except Exception:
        broker = None
    margin = max(float(broker) if broker is not None and math.isfinite(broker) else 0.0,
                 notional / FALLBACK_LEVERAGE)
    if not (notional > 0 and margin > 0):
        return None
    cap = math.floor(CAP_SHARE * min(NOTIONAL_X * eq / notional, eq / (MARGIN_LEVEL * margin)))
    return min(ex.cfg.max_open_total, cap)


def _add(d: dict[str, float | None], k: str, v: float | None) -> None:
    before = d.get(k, 0.0)
    d[k] = None if v is None or before is None else before + v


class _Pass:
    """What one pass reads once: the budgets, today's realised loss and the open risk, by arm, by pot and in all."""

    def __init__(self, eng: Engine, ex: Exploration, now: pd.Timestamp):
        from goldbot.engine.limits_apply import daily_allowance
        limit = daily_allowance(eng.gate, eng.state)["daily_loss_limit"]
        self.total = ex.cfg.budget_share * limit if limit is not None and limit > 0 else None
        since = eng._last_reset if eng._last_reset is not None else now.floor("D")
        self.lost = realised(eng, ex, since)
        self.share = shares(ex, now)
        self.held: dict[str, float | None] = {}
        self.count: dict[str, int] = {}
        for pid in _open(eng):
            tr = eng.open[pid]
            arm = ex.arms.get(tr.agent_id.removeprefix(PREFIX))
            loss = _held(eng, ex, pid, tr)
            for k in ("*", *((arm.arm_id, "pot:" + arm.style) if arm is not None else ())):
                _add(self.held, k, loss)
                self.count[k] = self.count.get(k, 0) + 1
        self.cap: int | None = -1                   # -1: not read yet (the first signal's terms read it)

    def lost_in(self, ex: Exploration, style: str | None = None) -> float:
        return sum(v for a, v in self.lost.items() if style is None or (a in ex.arms and ex.arms[a].style == style))

    def note(self, arm: Arm, loss: float) -> None:
        for k in ("*", arm.arm_id, "pot:" + arm.style):
            _add(self.held, k, loss)
            self.count[k] = self.count.get(k, 0) + 1


def _enter(eng: Engine, ex: Exploration, now: pd.Timestamp, tick: Tick) -> None:
    keep: deque[Pending] = deque()
    for s in ex.queue:
        age = (now - s.bar_ts).total_seconds()
        if age > stale_s(ex.arms[s.arm_id].tf):
            eng.decisions.append({"ts": now.isoformat(), "agent": PREFIX + s.arm_id, "action": "exploration_stale",
                                  "age_s": round(age, 3)})
        else:
            keep.append(s)
    ex.queue = keep
    if not ex.queue or _in_flight(eng):
        return
    if paused(eng, now):
        for s in ex.queue:
            eng.decisions.append({"ts": now.isoformat(), "agent": PREFIX + s.arm_id,
                                  "action": "exploration_paused_dd", "drawdown": drawdown(eng)})
        ex.queue.clear()
        return
    try:
        ps = _Pass(eng, ex, now)
    except Exception as exc:
        log.warning("exploration ledger unreadable (%s): no exploration entry", type(exc).__name__)
        eng.decisions.append({"ts": now.isoformat(), "agent": None, "action": "exploration_refused:ledger_unreadable"})
        return
    sent = 0
    for s in _order(ex):
        if sent >= PER_PASS:
            break
        arm = ex.arms[s.arm_id]
        res = _send(eng, ex, ps, arm, s, now, tick)
        if res == "cap":
            break                                   # the global cap is full: the rest wait (until they are stale)
        ex.queue.remove(s)
        if res == "sent":
            sent += 1
            ex.last_arm = arm.arm_id


def _send(eng: Engine, ex: Exploration, ps: _Pass, arm: Arm, s: Pending, now: pd.Timestamp,
          tick: Tick) -> Literal["sent", "refused", "cap"]:
    """One signal's checks, in order, each refusal journalled with its reason and the arm's share of its pot (the
    propensity, ADR 0043 / research E6), then the order core. "cap": the global cap is full."""
    tag, side, pot = arm.tag, s.side, ex.cfg.pots[arm.style]
    pid = order_id(eng, arm, s.bar_ts)
    price = tick.ask if side > 0 else tick.bid
    stop_d, target_d = arm.stop * s.atr_usd, arm.target * s.atr_usd
    applied: dict[str, Any] = {"arm": arm.arm_id, "tf": arm.tf, "style": arm.style, "atr_usd": round(s.atr_usd, 4),
                               "propensity": round(ps.share.get(arm.arm_id, 0.0), 4)}

    def refuse(why: str) -> None:
        eng.decisions.append({"ts": now.isoformat(), "agent": tag, "proposal": pid,
                              "action": "exploration_refused:" + why, **applied})

    if pid in eng.sent_ids:
        return "refused"
    terms = eng._sizing_terms(side, price, stop_d, cycle=s.bar_ts)
    lot = terms.get("volume_min")
    if ps.cap == -1:
        ps.cap = global_cap(eng, ex, price, terms)
    if ps.cap is None:
        refuse("cap_unknown")
        return "refused"
    if ps.count.get("*", 0) >= ps.cap:
        refuse("global_cap")
        return "cap"
    if ps.count.get("pot:" + arm.style, 0) >= pot.max_open:
        refuse("pot_slots")
        return "refused"
    if ps.count.get(arm.arm_id, 0) >= arm.max_open:
        refuse("arm_max_open")
        return "refused"
    if arm.style != SWING and crosses_friday(eng, now, arm.hold_s):
        refuse("weekend_hold")
        return "refused"
    if lot is None:
        refuse("loss_unknown")
        return "refused"
    net = sum(t.side * t.lots for t in eng.open.values()) + side * float(lot)
    if abs(net) > MAX_NET_LOTS + 1e-9:
        refuse("same_side")
        return "refused"
    if eng.cfg.mode != "demo" or not auto_entry.permitted(eng, now):
        refuse("not_auto")
        return "refused"
    loss = cost_probe.stop_loss(eng, side, price, stop_d, float(lot))
    cost = cost_probe.stop_loss(eng, side, price, eng._cost_atr(s.atr_usd, now) * s.atr_usd, float(lot))
    if loss is None or cost is None:
        refuse("loss_unknown")
        return "refused"
    applied.update(stop_loss=round(loss, 2), cost=round(cost, 2))
    arm_cap = eng.gate.limits.exploration_arms.get(arm.arm_id)
    if arm_cap is None or loss + cost > arm_cap + 1e-9:            # trading-safety LOW: the round trip counts too
        refuse("arm_cap")
        return "refused"
    held_arm, held_pot, held_all = ps.held.get(arm.arm_id, 0.0), ps.held.get("pot:" + arm.style, 0.0), \
        ps.held.get("*", 0.0)
    if held_arm is None or held_pot is None or held_all is None:
        refuse("open_risk_unknown")
        return "refused"
    if ps.total is None or ps.lost_in(ex) + held_all + loss > ps.total + 1e-9:
        refuse("budget")
        return "refused"
    if ps.lost_in(ex, arm.style) + held_pot + loss > pot.gbp + 1e-9:
        refuse("pot")
        return "refused"
    headroom = min(arm.daily_gbp, ps.share.get(arm.arm_id, 0.0) * pot.gbp) - ps.lost.get(arm.arm_id, 0.0) - held_arm
    if loss > headroom + 1e-9:
        refuse("arm_budget")
        return "refused"
    intent = _intent(eng, arm, side, price, s, now, terms, headroom)
    if intent is None:
        refuse("gate_unsupported")
        return "refused"

    def record(res: OrderResult, gd: GateDecision, filled: bool, at_stop: float, sent: pd.Timestamp,
               latency_ms: float) -> ExplorationRecord:
        return ExplorationRecord(
            client_order_id=pid, account_id=eng.cfg.account_id, arm_id=arm.arm_id, family=arm.family, tf=arm.tf,
            side=side, lots=float(res.filled_lots if filled else gd.lots), signal_utc=s.bar_ts, sent_utc=sent,
            atr_usd=s.atr_usd, stop_usd=gd.stop_distance, target_usd=target_d, hold_s=arm.hold_s,
            requested_price=price, spread_usd=round(tick.ask - tick.bid, 6), latency_ms=round(latency_ms, 3),
            retcode=res.retcode, position_id=res.position_id if filled else None,
            fill_price=(res.price if res.price is not None else price) if filled else None,
            stop_loss=round(at_stop, 2), cost=round(cost, 2), fx_acct_per_usd=terms.get("acct_per_usd"))

    pos = cost_probe._place(eng, ex.db, intent, _agent(eng, arm), repo=LEDGER, pid=pid, close_ts=now, entry=price,
                            target=price + side * target_d, max_loss=headroom, headroom=headroom,
                            applied=lambda: applied, refuse=refuse, action="exploration:",
                            journal={"agent": tag, "arm": arm.arm_id}, record=record, native_distance=stop_d)
    if pos is None:
        return "refused"
    ex.open_loss[pos] = loss
    ps.note(arm, loss)
    return "sent"


def _intent(eng: Engine, arm: Arm, side: int, price: float, s: Pending, now: pd.Timestamp,
            terms: dict[str, Any], headroom: float) -> Intent | None:
    """The minimum-lot Intent of purpose `exploration` (lane L1's fence). No p is invented: 0.5, the gate skips only
    the edge rules for this purpose. None when the gate's Intent does not know the purpose (fail closed)."""
    from goldbot.risk import Intent
    try:
        return Intent(agent_id=arm.tag, side=side, p=0.5, target_atr=arm.target, stop_atr=arm.stop,
                      atr_usd=s.atr_usd, cost_atr=eng._cost_atr(s.atr_usd, now), multiplier=1.0, price=price,
                      timeframe=arm.tf, family=arm.family, swap_usd_per_oz=eng._swap_usd_per_oz(side, now),
                      learning_probe=True, account_mode=eng.cfg.mode, phase_file=cost_probe._phase_file(eng),
                      purpose="exploration", arm_id=arm.arm_id,
                      terminal_trade_mode=cost_probe._terminal_trade_mode(eng), hold_s=float(arm.hold_s),
                      probe_headroom=headroom, **{**terms, "volume_max": terms.get("volume_min")})
    except ValidationError as exc:
        log.warning("exploration intent refused by its schema (%s)", exc.error_count())
        return None


# ------------------------------------------------------------------------------------------------ exits and records
def exits(eng: Engine, now: pd.Timestamp) -> None:
    """Every pass, with a quote or without (`Engine._retry_closes`), whatever the entry state (halted, propose mode,
    exploration switched off): a 1m/5m arm's position held `hold_s` on the host clock (from its fill; after a restart
    the broker's open time) is closed (`HOLD_EXIT`); a position whose arm is not configured is closed at once
    (`ORPHAN_EXIT`). A refused close stays due and is retried like any close. Never raises."""
    try:
        ex = eng.exploration
        at = eng.clock() if eng.cfg.live_clock else now
        for pid, tr in list(eng.open.items()):
            if not is_exploration(tr.agent_id) or pid in eng._close_due:
                continue
            arm = ex.arms.get(tr.agent_id.removeprefix(PREFIX)) if ex is not None else None
            if arm is None:
                eng._try_close(pid, ORPHAN_EXIT, now)
                continue
            since = tr.opened_utc or tr.entry_bar_ts
            if arm.tf in SUB_15M and since is not None and (at - since).total_seconds() >= arm.hold_s:
                eng._try_close(pid, HOLD_EXIT, now)
    except Exception:
        log.exception("exploration exits failed; retried next pass")


def record_close(eng: Engine, pid: int, tr: OpenTrade, reason: str, px: float | None, at: pd.Timestamp) -> bool:
    """An exploration close goes to the exploration ledger, never closed_trades.jsonl; False for any other trade. Exit,
    swap, commission and P&L from the broker's deals; `censored` unless the arm's own stop, target or hold closed it
    with its stop unmoved. Raises when the ledger cannot be written (the close stays queued and is retried)."""
    if not is_exploration(tr.agent_id):
        return False
    try:
        info, _ = eng._exit_deals(pid, tr)
    except Exception:
        log.exception("deal history unreadable for exploration position %s; recorded from the engine's fill", pid)
        info = None
    known = info or {}
    why = known.get("reason") or reason
    censored = why not in NATURAL or (tr.initial_sl is not None and tr.sl != tr.initial_sl)
    ex = eng.exploration
    db = ex.db if ex is not None else ledger_db(eng.cfg.state_dir)
    cid = tr.client_order_id or f"position-{pid}"
    with db.write() as conn:
        arm_id = tr.agent_id.removeprefix(PREFIX)
        arm = ex.arms.get(arm_id) if ex is not None else None
        start = tr.opened_utc or tr.entry_bar_ts
        rec = LEDGER.get(conn, cid) or ExplorationRecord(
            client_order_id=cid, account_id=eng.cfg.account_id, arm_id=arm_id,
            family=arm.family if arm else "", tf=arm.tf if arm else "", side=tr.side,
            lots=float(tr.initial_lots or tr.lots), signal_utc=start, sent_utc=start, atr_usd=0.0,
            stop_usd=abs((tr.open_price or 0.0) - (tr.initial_sl or tr.open_price or 0.0)), target_usd=0.0,
            hold_s=arm.hold_s if arm else 0, fill_price=tr.open_price, position_id=pid)
        rec = rec.model_copy(update={
            "position_id": pid, "exit_utc": known.get("exit_utc", at), "exit_price": known.get("exit_price", px),
            "exit_reason": why, "swap": known.get("swap"), "commission": known.get("commission"),
            "pnl": known.get("pnl"), "censored": censored})
        LEDGER.upsert(conn, rec)
    _observe(eng, db, rec, tr.opened_utc)
    if ex is not None:
        ex.open_loss.pop(pid, None)
    eng._recorded.add(pid)
    return True


def _observe(eng: Engine, db: Database, r: ExplorationRecord, filled: pd.Timestamp | None) -> None:
    """Lane L3's learning row for a closed exploration trade (`exploration_data.record_obs`, keyed by this ledger's
    client order id; the first record stands). The round trip in USD/oz is the spread paid at entry plus the
    commission and paid swap converted at the entry's rate. The entry is the fill (the broker's clock), the decision
    the send, never before the signal bar's close nor after the fill. Best effort: a row L3 cannot take is logged, never raised
    (the exploration ledger above is the record of the trade)."""
    from goldbot.research.exploration_data import record_obs
    if r.fill_price is None or r.exit_price is None or r.exit_utc is None or not r.lots:
        return
    try:
        oz = r.lots * eng._contract_size()
        paid = (r.commission or 0.0) + max(0.0, -(r.swap or 0.0))
        fees = paid / (oz * r.fx_acct_per_usd) if r.fx_acct_per_usd and oz > 0 else 0.0
        entry = filled or r.sent_utc
        record_obs(db, obs_id=r.client_order_id, account_id=r.account_id, arm_id=r.arm_id, family=r.family, tf=r.tf,
                   side=r.side, decision_utc=max(r.signal_utc, min(r.sent_utc, entry)), entry_utc=entry,
                   exit_utc=max(r.exit_utc, entry),
                   entry_price=r.fill_price, exit_price=r.exit_price, stop=r.fill_price - r.side * r.stop_usd,
                   cost_per_oz=(r.spread_usd or 0.0) + fees, censored=bool(r.censored))
    except Exception as exc:
        log.warning("exploration observation %s not recorded (%s)", r.client_order_id, type(exc).__name__)


def trades(state_dir: str, account_id: str | None = None) -> list[ExplorationRecord]:
    """The exploration ledger (one account's when given), for research (lane L3) and reports."""
    db = ledger_db(state_dir)
    with db.connection() as conn:
        if account_id is None:
            return LEDGER.select(conn)
        return LEDGER.select(conn, "account_id = ?", (account_id,))
