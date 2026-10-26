"""Exploration arms: minimum-lot demo entries that trade a simple rule to MEASURE its edge on the broker (plan
2026-10-25, lane L2; the RiskGate's purpose `exploration`, lane L1).

The plan's rules, as built here:

* An arm (`Arm`) is a frozen ARM-SPEC rule (config/exploration_arms.yaml, hash-checked; `research.arm_rules`) as an
  account registers it (style, control share, per-trade cap: `config_for`). Each candidate carries the rule's own
  stop, target and hold. "Every signal is sent at the minimum lot with its own stop, hold and target", or refused and
  journalled; never resized, never with a tightened stop.
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
* Retired arms (daily learning-ops review, goldbot/research/daily_review.py): an arm retired at its pre-registered
  H-61 stop (aux.db `exploration_arm_status`, this host's broker) is read once a minute (`refresh_retired`); from then
  on it queues no signal and its waiting signals are recorded blocked `arm_retired`. It stays configured, so its open
  positions keep their stop, target, hold and every exit (never closed as an orphan). An unreadable table keeps the
  arms already known retired (a retirement is never undone by a read error); until the first read after a start
  succeeds no exploration entry is sent (its signals are recorded blocked `arm_status_unread`; trading-safety
  2026-10-26: a restart must not trade an arm it cannot know is not retired). Exits are never affected.
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
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import pandas as pd
from pydantic import Field, ValidationError, model_validator

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.config import tf_seconds
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.timeutil import epoch_ns
from goldbot.db import Database, RecordRepo
from goldbot.engine import auto_entry, cost_probe
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.research.arms import Arm as SpecArm
from goldbot.research.arms import arm_hash, load_arms

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.engine.store import OpenTrade
    from goldbot.execution.broker import OrderResult, Position, Tick
    from goldbot.ops.accounts import Account
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
# live 2026-10-26: a bar closing more than this after the host clock is a clock fault, never a candidate
CLOCK_AHEAD_S = 60.0
LOOKBACK = 200                      # bars of an arm's timeframe its rule reads
LOOKBACK_1M = 1_000                 # and at least this many minutes (S5's 15m EMA50 reads 750)
SUB_15M = ("1m", "5m")
MAX_NET_LOTS = 0.10                 # ADR 0043 X3: net same-side exposure on the account, lots
DD_WARN, DD_PAUSE = 0.06, 0.08      # ADR 0043 section 2: drawdown from the mark: warn, pause
PI_RANDOM = 1.0                     # champion zero: the random branch takes every admissible candidate (mu = 1)
SHARE_FLOOR, SHARE_CAP = 0.05, 0.30  # H-61's bounds on a strategy arm's share (L3 `bounded_shares`), fallback included
SPEC_PATH = Path(__file__).parents[2] / "config" / "exploration_arms.yaml"   # the frozen ARM-SPEC (hash-checked)
# a close by the arm's own barrier: anything else censors the label
NATURAL = frozenset({HOLD_EXIT, "time_exit", "stop", "target", "engine_stop_close"})


def stale_s(tf: str) -> float:
    """How old a signal may be when its order is sent: 10 s on 1m, 30 s on 5m, 60 s on 15m and above."""
    return 10.0 if tf == "1m" else 30.0 if tf == "5m" else 60.0


class Arm(FrozenRecord):
    """One exploration arm: its frozen rule (`spec`, config/exploration_arms.yaml, ARM-SPEC 2026-10-25: rule,
    timeframe, params, max_open, hash-checked) and the account's registration of it (config/accounts.yaml
    `exploration.arms`): the ADR 0043 sleeve (`style`) and, for a random control, its fixed share of it (controls are
    the null: never allocated by results). Each candidate carries its own stop, target and hold from the rule."""
    spec: SpecArm
    style: str
    control_share: float | None = Field(None, gt=0, le=1)

    @property
    def arm_id(self) -> str:
        return self.spec.id

    @property
    def tf(self) -> str:
        return self.spec.timeframe

    @property
    def max_open(self) -> int:
        return self.spec.max_open

    @property
    def family(self) -> str:
        return self.spec.rule

    @property
    def key(self) -> str:
        """Five hex characters of the arm id: its part of the order id and MT5 comment (adoption by comment)."""
        return hashlib.sha1(self.arm_id.encode()).hexdigest()[:5]

    @property
    def tag(self) -> str:
        return PREFIX + self.arm_id

    @property
    def hold_s(self) -> int:
        """A 1m/5m arm's hold on the host clock: the spec's `hold_bars` of its timeframe (every sub-15m rule has one)."""
        return int(self.spec.params.get("hold_bars", 1)) * tf_seconds(self.tf)

    @property
    def config_hash(self) -> str:
        """The frozen spec's hash (lane L3 `arm_config_hash`): a changed rule is a different arm's evidence."""
        return self.spec.config_hash or arm_hash(self.spec)


class Pot(FrozenRecord):
    """ADR 0043 section 1: a style's sleeve. Today's realised loss + open risk at the stops + the new trade's loss at
    its stop <= `gbp` (account currency); at most `max_open` of its arms' positions open."""
    gbp: float = Field(gt=0)
    max_open: int = Field(ge=1)


class ExplorationConfig(FrozenRecord):
    """The engine's exploration block (`config_for`): the account's `exploration` (enabled, budget_share,
    max_open_total, pots) with each registered arm's frozen rule; `dropped` names the registered arms left out (not
    in the spec), which the engine journals at its start."""
    enabled: bool = False
    budget_share: float = Field(0.1, gt=0, le=1)
    max_open_total: int = Field(1, ge=1)
    pots: dict[str, Pot] = Field(default_factory=dict)
    arms: tuple[Arm, ...] = ()
    dropped: tuple[str, ...] = ()

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


def config_for(acc: Account, spec_path: Path = SPEC_PATH) -> ExplorationConfig | None:
    """The engine's exploration block for `acc` (integration ruling 2026-10-25): None unless its `exploration` is
    enabled. Rules come from the frozen, hash-checked spec (`research.arms.load_arms`); the account registers the arms
    it runs (style, control share; the per-trade cap reaches the RiskGate as `exploration_arms`). A registered arm the
    spec lacks or blocks is dropped and logged here, and journalled at the engine's start (`dropped`)."""
    ex = acc.exploration
    if not ex.enabled:
        return None
    specs = {a.id: a for a in load_arms(spec_path)}
    arms, dropped = [], []
    for arm_id in sorted(ex.arms):
        reg = ex.arms[arm_id]
        if arm_id not in specs or specs[arm_id].status != "run" or reg.style is None:
            log.warning("exploration arm %s is registered on %s but missing or blocked in the frozen spec: dropped",
                        arm_id, acc.account_id)
            dropped.append(arm_id)
            continue
        arms.append(Arm(spec=specs[arm_id], style=reg.style, control_share=reg.control_share))
    return ExplorationConfig(enabled=True, budget_share=ex.budget_share, max_open_total=ex.max_open_total,
                             pots={k: Pot(gbp=v.gbp, max_open=v.max_open) for k, v in ex.pots.items()},
                             arms=tuple(arms), dropped=tuple(dropped))


class ExplorationRecord(FrozenRecord):
    """One exploration order and, once closed, its exit (aux.db `exploration_trades`, aux migration 16). Prices USD per
    oz; money in the account currency as the broker reports it."""
    client_order_id: str
    account_id: str
    arm_id: str
    family: str
    tf: str
    side: int
    lots: float
    signal_utc: UtcTimestamp                # the signal bar's close
    sent_utc: UtcTimestamp
    atr_usd: float                          # the rule's stop distance (USD/oz): the arm's R
    stop_usd: float                         # the stop distance the gate set
    target_usd: float
    hold_s: int
    requested_price: float | None = None
    spread_usd: float | None = None
    latency_ms: float | None = None
    retcode: int | None = None
    position_id: int | None = None
    fill_price: float | None = None
    stop_loss: float | None = None          # the loss at the gate's stop when sent, account currency
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


def ledger_db(state_dir: str) -> Database:
    """aux.db, whose migration 16 holds the exploration ledger and lane L3's tables."""
    return Database.open(state_dir, "aux")


# ------------------------------------------------------------------------------------------------ the engine's state
class Pending(FrozenRecord):
    """A candidate waiting to be sent: the rule's side, stop and target (USD/oz) and hold (bars of its timeframe)."""
    arm_id: str
    side: int
    bar_ts: UtcTimestamp                    # the signal bar's close (visible_at)
    stop_usd: float
    target_usd: float
    hold_bars: int


class _ArmAgent:
    """What `Engine._execute` and `_adopt` read of an agent, for an arm: its tag (own magic), family, timeframe and a
    time barrier. A 15m+ candidate's barrier is its hold (base bars = hold_bars x tf / decision tf); a 1m/5m arm's is
    a backstop one base bar past its hold (the hold itself is `exits`, on the host clock). No exit policy."""

    def __init__(self, arm: Arm, decision_tf: str, hold_bars: int, target_r: float = 1.0):
        self.arm, self.agent_id, self.family, self.timeframe = arm, arm.tag, arm.family, arm.tf
        sec, base = tf_seconds(arm.tf), tf_seconds(decision_tf)
        bars = hold_bars if arm.tf not in SUB_15M else math.ceil(
            (math.ceil(hold_bars * sec / base) + 1) * base / sec)
        self.label_spec = BarrierSpec(target_atr=target_r, stop_atr=1.0, max_bars=max(0, bars - 1), name="exploration")
        self.exit_spec = None
        self.shadow_only = self.requires_tv = False


class Exploration:
    """The engine's exploration state: the arms, the ledger, the signals waiting, the rotation and what was read."""

    def __init__(self, eng: Engine, cfg: ExplorationConfig):
        from goldbot.research import arm_rules
        self.cfg = cfg
        self.broker = eng.cfg.broker_name             # the key its evidence is learnt and read under (ADR 0043 am.)
        ok = [a for a in cfg.arms if a.spec.status == "run" and a.spec.rule in arm_rules.RULE_FUNCS]
        for name in [*cfg.dropped, *(a.arm_id for a in cfg.arms if a not in ok)]:
            # trading-safety L2: one bad arm never stops the engine; it is named and the others run
            log.warning("exploration arm %s dropped (not in the frozen spec, blocked there or no rule)", name)
            eng.decisions.append({"ts": time.time(), "agent": PREFIX + name, "action": "exploration_arm_dropped",
                                  "arm": name})
        self.arms = {a.arm_id: a for a in ok}
        self.by_key = {a.key: a for a in ok}
        self.db = ledger_db(eng.cfg.state_dir)
        self.queue: deque[Pending] = deque()
        self.last_arm: str | None = None
        self.last_bar: dict[str, pd.Timestamp] = {}
        self.last_close = eng._closed_through
        self.last_minute: pd.Timestamp | None = None
        self.ahead_seen: pd.Timestamp | None = None   # the last bar skipped as ahead of the host clock (journalled once)
        self.open_loss: dict[int, float] = {}       # position id -> its loss at the gate's stop when sent
        self.weights_cache: tuple[pd.Timestamp, dict[str, float]] | None = None
        self.champion_seen: dict[str, tuple[pd.Timestamp | None, bool]] = {}
        self.retired: frozenset[str] = frozenset()      # arms retired at their stop (`refresh_retired`)
        self.retired_read: pd.Timestamp | None = None
        self.retired_ok = False                         # a read has succeeded since the start: entries may go out
        self.lost_seen: dict[int, pd.Timestamp] = {}     # lost record -> the day it was last journalled

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


def _agent(eng: Engine, arm: Arm, hold_bars: int | None = None, target_r: float = 1.0) -> Specialist:
    hold = hold_bars if hold_bars is not None else int(arm.spec.params.get("hold_bars", 1))
    return cast("Specialist", _ArmAgent(arm, eng.cfg.decision_tf, hold, target_r))


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
def whole_window(arm: SpecArm) -> bool:
    """A `specialist` arm reads the engine's whole 1m window (~40 trading days), not LOOKBACK bars: the family's own
    code warms up over long memories (eq_liquidity_run's ATR200 equal-level threshold found none of the history's
    candidates on 200 bars), and "the same function runs in the history matrix and live" (arms expansion 2026-10-26,
    section 3) holds only once they have converged. The written ARM-SPEC rules keep their lookback unchanged."""
    return arm.rule == "specialist"


def _bars(frame_1m: pd.DataFrame, tf: str, through: pd.Timestamp, whole: bool = False) -> pd.DataFrame | None:
    """The store-schema bars of `tf` the rule reads, the last one closing exactly at `through`; None when that bar
    is not there. `whole`: every bar of `frame_1m` (`whole_window`)."""
    sec = tf_seconds(tf)
    span = max(sec * (LOOKBACK + 1), 60 * LOOKBACK_1M)
    src = frame_1m if tf == "1d" or whole else frame_1m[frame_1m["ts_utc"] >= through - pd.Timedelta(seconds=span)]
    bars = src if tf == "1m" else resample_bars(src, tf)
    bars = bars[bars["visible_at"] <= through].reset_index(drop=True)
    if bars.empty or pd.Timestamp(bars["visible_at"].iloc[-1]) != through:
        return None
    return bars


def _signal(eng: Engine, ex: Exploration, arm: Arm, bars: pd.DataFrame, b1: pd.DataFrame,
            through: pd.Timestamp) -> None:
    """The arm's frozen rule (`arm_rules.candidates`: its window, the common filters, its stop band) on the bars up to
    `through`; a candidate on the bar that just closed is queued with its own stop, target and hold. A rule that
    raises is journalled (`exploration_rule_error`) and the other arms go on."""
    import numpy as np

    from goldbot.research import arm_rules
    if arm.arm_id in ex.retired or ex.last_bar.get(arm.arm_id) == through:
        return
    ex.last_bar[arm.arm_id] = through
    try:
        ctx = arm_rules.RuleContext(bars=bars, b1=b1, blackout_lo=np.zeros(0, dtype=np.int64),
                                    blackout_hi=np.zeros(0, dtype=np.int64))
        c = arm_rules.candidates(arm.spec, ctx)
    except Exception as exc:
        log.warning("exploration arm %s rule failed (%s)", arm.arm_id, type(exc).__name__)
        eng.decisions.append({"ts": time.time(), "agent": arm.tag, "action": "exploration_rule_error",
                              "error": type(exc).__name__})
        return
    last = c[c["idx"] == len(bars) - 1]
    for side, risk, target_r, max_bars in zip(last["side"].to_numpy(dtype=int), last["risk_usd"].to_numpy(dtype=float),
                                              last["target_r"].to_numpy(dtype=float),
                                              last["max_bars"].to_numpy(dtype=int)):
        ex.queue.append(Pending(arm_id=arm.arm_id, side=int(side), bar_ts=through, stop_usd=float(risk),
                                target_usd=float(target_r) * float(risk), hold_bars=int(max_bars) + 1))


def step(eng: Engine, close_ts: pd.Timestamp) -> bool:
    """At a base bar close (after the engine's decisions): the 15m+ arms whose bar closes now queue their signals.
    False when the close is deferred: on the live clock a close more than CLOCK_AHEAD_S after the host clock is a
    broker-clock fault (live 2026-10-26: server_tz Athens after EU DST against the broker's NY+7 stamped the 06:00 bar
    07:00). It is journalled once (`exploration_clock_ahead`) and nothing is evaluated or recorded: its candidate ids
    are the real bar's, and a refused record would shadow the real candidates (first record stands). The caller
    tries again on later passes, so the bar is evaluated once the host clock reaches it."""
    ex = eng.exploration
    if ex is None:
        return True
    if eng.cfg.live_clock and (ahead := (close_ts - eng.clock()).total_seconds()) > CLOCK_AHEAD_S:
        if ex.ahead_seen != close_ts:
            ex.ahead_seen = close_ts
            log.warning("exploration: bar %s closes %.0f s after the host clock; deferred (clock fault)", close_ts,
                        ahead)
            eng.decisions.append({"ts": time.time(), "agent": None, "action": "exploration_clock_ahead",
                                  "bar": close_ts.isoformat(), "ahead_s": round(ahead, 3)})
        return False
    arms = [a for a in ex.arms.values() if a.tf not in SUB_15M]
    if not arms or eng.bars_1m.empty:
        return True
    complete = eng.bars_1m[eng.bars_1m["visible_at"] <= close_ts]
    for tf, whole in sorted({(a.tf, whole_window(a.spec)) for a in arms}, key=lambda k: (tf_seconds(k[0]), k[1])):
        if tf != "1d" and int(close_ts.timestamp()) % tf_seconds(tf):
            continue
        bars = _bars(complete, tf, close_ts, whole)
        if bars is not None:
            b1 = complete[complete["ts_utc"] >= pd.Timestamp(bars["ts_utc"].iloc[0])].reset_index(drop=True)
            for arm in (a for a in arms if a.tf == tf and whole_window(a.spec) == whole):
                _signal(eng, ex, arm, bars, b1, close_ts)
    return True


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
    since = through - pd.Timedelta(seconds=max(tf_seconds(tfs[-1]) * (LOOKBACK + 1), 60 * LOOKBACK_1M))
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
    frame = frame[frame["visible_at"] <= through].reset_index(drop=True)
    for tf in tfs:
        bars = _bars(frame, tf, through)
        if bars is not None:
            for arm in (a for a in arms if a.tf == tf):
                _signal(eng, ex, arm, bars, frame, through)


def on_pass(eng: Engine, tick: Tick) -> None:
    """Every engine pass with a quote, after its exits and its bar close: queue the arms' new signals and send. Never
    raises (an error is journalled `exploration_error`; the pass and every exit go on)."""
    ex = eng.exploration
    if ex is None:
        return
    try:
        now = eng._now(tick)
        refresh_retired(eng, ex, now)
        ct = eng._closed_through
        if ct is not None and (ex.last_close is None or ct > ex.last_close) and step(eng, ct):
            ex.last_close = ct                      # a deferred close (clock ahead) is tried again next pass
        _minutes(eng, ex, now)
        if ex.queue and not ex.retired_ok:      # fail closed: the retired arms are not known yet
            _block(eng, ex, list(ex.queue), now, "arm_status_unread")
        elif ex.queue:
            _enter(eng, ex, now, tick)
    except Exception as exc:
        log.exception("exploration pass failed; no exploration entry this pass")
        eng.decisions.append({"ts": time.time(), "agent": None, "action": "exploration_error",
                              "error": type(exc).__name__})


def refresh_retired(eng: Engine, ex: Exploration, now: pd.Timestamp) -> None:
    """Once a minute: the arms of this host's broker retired at their pre-registered stop (aux.db
    `exploration_arm_status`, written by the daily review). A newly retired arm is journalled
    (`exploration_arm_retired`) and its waiting signals are recorded blocked `arm_retired`. Read errors keep the set
    already known (a retirement is permanent). Entries only: nothing here touches an open position."""
    minute = now.floor("min")
    if ex.retired_read == minute:
        return
    ex.retired_read = minute
    try:
        with ex.db.connection() as conn:
            got = {str(a) for (a,) in conn.execute(
                "SELECT arm_id FROM exploration_arm_status WHERE broker = ? AND status = 'retired'", (ex.broker,))}
    except Exception as exc:
        log.warning("exploration retired arms unreadable (%s): the known set stands%s", type(exc).__name__,
                    "" if ex.retired_ok else "; no entry until it is read")
        return
    ex.retired_ok = True
    new = sorted((got & set(ex.arms)) - ex.retired)
    if not new:
        return
    ex.retired = ex.retired | frozenset(new)
    for arm_id in new:
        log.warning("exploration arm %s retired at its pre-registered stop: no new entries", arm_id)
        eng.decisions.append({"ts": now.isoformat(), "agent": PREFIX + arm_id, "action": "exploration_arm_retired",
                              "arm": arm_id})
    _block(eng, ex, [s for s in ex.queue if s.arm_id in ex.retired], now, "arm_retired")


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
    null, never allocated by results); a strategy arm its research share of the style's budget (lane L3's
    `exploration_data.weights` for ONE style's strategy arms on this host's broker: its newest bandit run held in
    [5%, 30%]; equal shares in the same bounds when it is missing, stale or unreadable, here too) scaled by what the
    controls leave (1 - their shares; quant Q3). The pot's £ stays the backstop. Read at most once a minute."""
    from goldbot.research.exploration_data import weights
    minute = now.floor("min")
    if ex.weights_cache is not None and ex.weights_cache[0] == minute:
        return ex.weights_cache[1]
    out: dict[str, float] = {}
    for style in ex.cfg.pots:
        arms = [a for a in ex.arms.values() if a.style == style]
        strat = [a.arm_id for a in arms if a.control_share is None]
        rest = max(0.0, 1.0 - sum(a.control_share or 0.0 for a in arms))
        try:
            w = weights(ex.db, strat, broker=ex.broker, now=now) if strat else {}
        except Exception as exc:                    # the reader falls back itself; this is the last line
            log.warning("exploration weights unreadable (%s): equal shares", type(exc).__name__)
            w = {a: min(SHARE_CAP, max(SHARE_FLOOR, 1.0 / len(strat))) for a in strat}
        for a in arms:
            v = a.control_share if a.control_share is not None else rest * float(w.get(a.arm_id, 0.0))
            out[a.arm_id] = v if math.isfinite(v) and v >= 0 else 0.0
    ex.weights_cache = (minute, out)
    return out


def realised(eng: Engine, ex: Exploration, since: pd.Timestamp,
             lost: list[tuple[int, str]] | None = None) -> dict[str, float]:
    """arm id -> today's realised loss (>= 0, net of the arm's profits): every arm with a trade closed since `since`,
    at 0 when it is flat or up (so the keys are the arms that have traded today, `_arm_room`'s first trade). A row
    closed with no P&L counts at its loss at the stop plus its cost.

    An exploration close whose record is not in the ledger yet (`unrecorded`: queued by the reconcile's sweep or a bar
    close's exit, which are written after `on_pass`; a write being retried; or given up as lost) counts the same way,
    never optimistically: at its ledger row's loss at the stop plus its cost, else its arm's per-trade cap (which
    bounds both), until its record lands, then from the ledger only (no double count) (trading-safety HIGH
    2026-10-26). It counts on its own day only (its close time; for a lost id after a restart, its row's sent time
    plus its hold); on a later day its arm stays a key at 0: spent (its share binds, no first trade) and never charged
    again. An id with no exploration ledger row and no exploration trade is another ledger's (a strategy's or a cost
    probe's) and is skipped (trading-safety MEDIUM). The given-up records counted go to `lost` (position, arm).
    Raises when the ledger cannot be read (the caller refuses: fail closed)."""
    pending = unrecorded(eng)
    with ex.db.connection() as conn:
        rows = LEDGER.select(conn, "account_id = ? AND closed = 1 AND exit_ns >= ?",
                             (eng.cfg.account_id, _ns(since)))
        marks = ",".join("?" * len(pending))
        mine = LEDGER.select(conn, f"account_id = ? AND position_id IN ({marks})",
                             (eng.cfg.account_id, *pending)) if pending else []
    pnl: dict[str, float] = {}
    for r in rows:
        v = r.pnl if r.pnl is not None else -((r.stop_loss or 0.0) + (r.cost or 0.0))
        pnl[r.arm_id] = pnl.get(r.arm_id, 0.0) + v
    done = {r.position_id for r in mine if r.exit_utc is not None}
    sent = {r.position_id: r for r in mine if r.exit_utc is None}
    caps = eng.gate.limits.exploration_arms
    for pid, (arm_id, at, gone) in pending.items():
        row = sent.get(pid)
        if pid in done or (row is None and arm_id is None):
            continue                            # recorded (counted from the ledger), or not an exploration trade
        if row is not None:
            arm_id = row.arm_id
            at = at if at is not None else row.sent_utc + pd.Timedelta(seconds=row.hold_s)
        if arm_id is None or at is None:        # unreachable: a bare id is kept only with its ledger row
            continue
        if at < since:
            pnl.setdefault(arm_id, 0.0)         # an earlier day's: spent, never charged again
        elif row is not None and row.stop_loss is not None and row.cost is not None:
            pnl[arm_id] = pnl.get(arm_id, 0.0) - (row.stop_loss + row.cost)
        else:
            pnl[arm_id] = pnl.get(arm_id, 0.0) - caps.get(arm_id, max(caps.values(), default=0.0))
        if gone and lost is not None:
            lost.append((pid, arm_id))
    return {a: max(0.0, -v) for a, v in pnl.items()}


def unrecorded(eng: Engine) -> dict[int, tuple[str | None, pd.Timestamp | None, bool]]:
    """position id -> (arm id, close time, given up) of every close whose record is not written: queued
    (`Engine._closes`; exploration trades only), given up in this process (`_throttle_lost`, the same) and given up
    in an earlier process (`_records_lost`: only the id survives a restart, any trade's; arm and time unknown)."""
    out: dict[int, tuple[str | None, pd.Timestamp | None, bool]] = {}
    gone, queued = set(eng._records_lost), [*eng._closes, *eng._throttle_lost]
    for c in queued:
        if is_exploration(c.trade.agent_id):
            out[c.position_id] = (c.trade.agent_id.removeprefix(PREFIX), c.at, c.position_id in gone)
    for pid in gone - {c.position_id for c in queued}:
        out[pid] = (None, None, True)
    return {pid: v for pid, v in out.items() if pid not in eng._recorded}


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


def probe_slot(eng: Engine) -> int:
    """ADR 0043 section 1 ("one of 18 kept for the scheduled probe"): 1 when this host runs scheduled cost probes,
    else 0. `max_open_total` counts exploration positions only, so it cannot keep that slot once mirror probes (or any
    other position) are open; this holds it against every position but an open scheduled probe. It only ever
    lowers what exploration may take; the host's max_positions is never raised."""
    return int(eng.cfg.cost_probe and eng.cfg.cost_probe_scheduled.enabled)


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
        lost: list[tuple[int, str]] = []
        self.lost = realised(eng, ex, since, lost)
        day = now.floor("D")
        for pid, arm_id in lost:                    # once a day per record: the owner repairs it (RUNBOOK)
            if ex.lost_seen.get(pid) != day:
                ex.lost_seen[pid] = day
                log.warning("exploration: arm %s held by the lost record of position %s", arm_id, pid)
                eng.decisions.append({"ts": now.isoformat(), "agent": PREFIX + arm_id,
                                      "action": "exploration_blocked_lost_record", "position": pid, "arm": arm_id})
        self.share = shares(ex, now)
        self.held: dict[str, float | None] = {}
        # "host": every position the RiskGate's max_positions counts (this engine's and foreign ones on the symbol)
        # but an open scheduled probe, whose slot `_send` keeps free (ADR 0043: one of 18 kept for it)
        self.count: dict[str, int] = {"host": len(eng._foreign) + sum(
            t.agent_id != cost_probe.SCHEDULED_ID for t in eng.open.values())}
        for pid in _open(eng):
            tr = eng.open[pid]
            arm = ex.arms.get(tr.agent_id.removeprefix(PREFIX))
            loss = _held(eng, ex, pid, tr)
            for k in ("*", *((arm.arm_id, "pot:" + arm.style) if arm is not None else ())):
                _add(self.held, k, loss)
                self.count[k] = self.count.get(k, 0) + 1
        self.cap: int | None = -1                   # -1: not read yet (the first signal's terms read it)
        self.why: str | None = None                 # the last signal's refusal

    def lost_in(self, ex: Exploration, style: str | None = None) -> float:
        return sum(v for a, v in self.lost.items() if style is None or (a in ex.arms and ex.arms[a].style == style))

    def note(self, arm: Arm, loss: float) -> None:
        for k in ("*", arm.arm_id, "pot:" + arm.style):
            _add(self.held, k, loss)
            self.count[k] = self.count.get(k, 0) + 1
        self.count["host"] += 1


def _enter(eng: Engine, ex: Exploration, now: pd.Timestamp, tick: Tick) -> None:
    keep: deque[Pending] = deque()
    for s in ex.queue:
        age = (now - s.bar_ts).total_seconds()
        if age > stale_s(ex.arms[s.arm_id].tf):
            eng.decisions.append({"ts": now.isoformat(), "agent": PREFIX + s.arm_id, "action": "exploration_stale",
                                  "age_s": round(age, 3)})
            candidate(eng, ex, s, now, "stale")
        else:
            keep.append(s)
    ex.queue = keep
    if not ex.queue or _unsettled(eng, now):
        return
    if paused(eng, now):
        for s in ex.queue:
            eng.decisions.append({"ts": now.isoformat(), "agent": PREFIX + s.arm_id,
                                  "action": "exploration_paused_dd", "drawdown": drawdown(eng)})
            candidate(eng, ex, s, now, "paused_dd")
        ex.queue.clear()
        return
    try:
        ps = _Pass(eng, ex, now)
    except Exception as exc:
        log.warning("exploration ledger unreadable (%s): no exploration entry", type(exc).__name__)
        eng.decisions.append({"ts": now.isoformat(), "agent": None, "action": "exploration_refused:ledger_unreadable"})
        return
    sent = 0
    order = _order(ex)
    for i, s in enumerate(order):
        if sent >= PER_PASS:
            break
        if _unsettled(eng, now):
            # trading-safety M1: an order that raised or came back ambiguous is in no count yet (global cap, net
            # lots, slots), so nothing else goes out until it is settled; the rest are blocked candidates now
            _block(eng, ex, order[i:], now, "order_unsettled")
            return
        arm = ex.arms[s.arm_id]
        res = _send(eng, ex, ps, arm, s, now, tick)
        if res == "cap":
            _block(eng, ex, order[i:], now, ps.why or "global_cap")   # quant Q1: recorded now, never left to go stale
            return
        ex.queue.remove(s)
        if ps.why != "duplicate":
            candidate(eng, ex, s, now, None if res == "sent" else ps.why, ps.share.get(arm.arm_id))
        if res == "sent":
            sent += 1
            ex.last_arm = arm.arm_id


def _unsettled(eng: Engine, now: pd.Timestamp) -> bool:
    """An exploration order still sending or ambiguous, or an order that raised in this pass (`_auto_paused`)."""
    return _in_flight(eng) or (eng._auto_paused is not None and eng._auto_paused[0] == now)


def _block(eng: Engine, ex: Exploration, signals: list[Pending], now: pd.Timestamp, why: str) -> None:
    for s in signals:
        if s in ex.queue:
            ex.queue.remove(s)
        candidate(eng, ex, s, now, why)


def policy(ex: Exploration, style: str, now: pd.Timestamp) -> str:
    """The branch that chooses a style's candidates (quant ruling 2026-10-25, "champion zero"): `random` until lane
    L3's `serving_model` returns a champion for the style; no meta_p is ever served from a shadow model. The ranked
    branch is not built here yet, so with a champion the engine stays on the random branch and says so
    (`exploration_ranked_not_built`, once a minute at most): it never ranks on a model it cannot score."""
    from goldbot.research.exploration_data import serving_model
    minute = now.floor("min")
    if ex.champion_seen.get(style, (None, False))[0] != minute:
        try:
            champ = serving_model(ex.db, style, now, broker=ex.broker) is not None
        except Exception:
            champ = False
        ex.champion_seen[style] = (minute, champ)
        if champ:
            log.warning("exploration: a %s champion is served but the ranked branch is not built; random branch", style)
    return "random"


def draw(candidate_id: str) -> float:
    """The candidate's seeded uniform u in [0, 1): its id hashed, so a replay draws the same."""
    return int(hashlib.sha1(candidate_id.encode()).hexdigest()[:13], 16) / float(16 ** 13)


def candidate(eng: Engine, ex: Exploration, s: Pending, now: pd.Timestamp, blocked: str | None,
              share: float | None = None) -> None:
    """Every candidate, taken or not, to lane L3's `exploration_data.record_candidate` with its propensity: the random
    branch takes an admissible candidate when u < PI_RANDOM (mu = PI_RANDOM); a candidate a cap, the budget, the gate,
    staleness or the pause stopped is `blocked` with mu 0 and its reason (research E6: off-policy estimates stay
    unbiased). The arm's share of its pot goes with it (quant Q3). The feature snapshot is not built in the engine
    yet (None). Best effort: never raises."""
    from goldbot.research.exploration_data import record_candidate
    arm = ex.arms[s.arm_id]
    cid = order_id(eng, arm, s.bar_ts)
    branch = policy(ex, arm.style, now)
    u = draw(cid)
    taken = blocked is None
    try:
        record_candidate(ex.db, candidate_id=cid, account_id=eng.cfg.account_id, broker=ex.broker, arm_id=arm.arm_id,
                         family=arm.family, style=arm.style, tf=arm.tf, side=s.side, decision_utc=s.bar_ts,
                         stop_dist=s.stop_usd, target_dist=s.target_usd, max_hold_s=s.hold_bars * tf_seconds(arm.tf),
                         taken=taken, is_control=arm.control_share is not None, arm_config_hash=arm.config_hash,
                         blocked_reason=blocked, branch=branch if taken else "blocked", u=u,
                         mu=PI_RANDOM if taken else 0.0, pi_rand_arm=PI_RANDOM,
                         arm_share=share if share is not None else shares(ex, now).get(arm.arm_id))
    except Exception as exc:
        log.warning("exploration candidate %s not recorded (%s)", cid, type(exc).__name__)



def _arm_room(share_gbp: float, lost: float, held: float, arm_cap: float, first: bool) -> float:
    """An arm's room today: its share of the pot less today's realised loss and open risk. Its `first` trade of the
    day (none closed since the day's reset, none open) may always go, up to its per-trade cap: a share below one
    trade's loss (W1A 0.20 x £40 = £8 against ≈ £29 at the stop, live 2026-10-26) otherwise refused it on every bar.
    "First" is by trades, never by P&L: a trade closed flat or in profit nets to no loss, and used to make the arm
    fresh again, trading at its cap on every bar. The pot, the host budget and the RiskGate still bind."""
    room = share_gbp - lost - held
    return max(room, arm_cap) if first else room

def _send(eng: Engine, ex: Exploration, ps: _Pass, arm: Arm, s: Pending, now: pd.Timestamp,
          tick: Tick) -> Literal["sent", "refused", "cap"]:
    """One signal's checks, in order, each refusal journalled with its reason and the arm's share of its pot (the
    propensity, ADR 0043 / research E6), then the order core. "cap": the global cap is full. The checks before the
    gate use the rule's stop; the gate may floor it wider (stops level, spread), so the order core judges the same
    bounds again on the loss at the gate's stop, and that loss is the position's open risk (trading-safety L1)."""
    tag, side, pot = arm.tag, s.side, ex.cfg.pots[arm.style]
    pid = order_id(eng, arm, s.bar_ts)
    price = tick.ask if side > 0 else tick.bid
    stop_d, target_d, hold_s = s.stop_usd, s.target_usd, s.hold_bars * tf_seconds(arm.tf)
    applied: dict[str, Any] = {"arm": arm.arm_id, "tf": arm.tf, "style": arm.style, "stop_usd": round(stop_d, 4),
                               "propensity": round(ps.share.get(arm.arm_id, 0.0), 4)}

    def refuse(why: str) -> None:
        ps.why = why
        eng.decisions.append({"ts": now.isoformat(), "agent": tag, "proposal": pid,
                              "action": "exploration_refused:" + why, **applied})

    ps.why = None
    if pid in eng.sent_ids:
        ps.why = "duplicate"
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
    if ps.count["host"] >= eng.gate.limits.max_positions - probe_slot(eng):
        refuse("probe_slot")
        return "cap"
    if ps.count.get("pot:" + arm.style, 0) >= pot.max_open:
        refuse("pot_slots")
        return "refused"
    if ps.count.get(arm.arm_id, 0) >= arm.max_open:
        refuse("arm_max_open")
        return "refused"
    if crosses_friday(eng, now, hold_s):            # quant Q2: every style, swing included
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
    cost = cost_probe.stop_loss(eng, side, price, eng._cost_atr(stop_d, now) * stop_d, float(lot))
    if loss is None or cost is None:
        refuse("loss_unknown")
        return "refused"
    applied.update(stop_loss=round(loss, 2), cost=round(cost, 2))
    arm_cap = eng.gate.limits.exploration_arms.get(arm.arm_id)
    held_arm, held_pot, held_all = ps.held.get(arm.arm_id, 0.0), ps.held.get("pot:" + arm.style, 0.0), \
        ps.held.get("*", 0.0)
    if arm_cap is None:
        refuse("arm_cap")
        return "refused"
    if held_arm is None or held_pot is None or held_all is None:
        refuse("open_risk_unknown")
        return "refused"
    # what the new trade may lose at its stop under each bound, in the order they are journalled
    rooms = {"arm_cap": arm_cap - cost,              # trading-safety LOW: the round trip counts too
             "budget": (ps.total - ps.lost_in(ex) - held_all) if ps.total is not None else 0.0,
             "pot": pot.gbp - ps.lost_in(ex, arm.style) - held_pot,
             "arm_budget": _arm_room(ps.share.get(arm.arm_id, 0.0) * pot.gbp, ps.lost.get(arm.arm_id, 0.0), held_arm,
                                     arm_cap, first=arm.arm_id not in ps.lost and not ps.count.get(arm.arm_id))}

    def binding(at_stop: float) -> str | None:
        return next((k for k, room in rooms.items() if at_stop > room + 1e-9), None)

    if (why := binding(loss)) is not None:
        refuse(why)
        return "refused"
    room = min(rooms.values())
    intent = _intent(eng, arm, side, price, s, now, terms, room)
    if intent is None:
        refuse("gate_unsupported")
        return "refused"
    at_gate: list[float] = []

    def record(res: OrderResult, gd: GateDecision, filled: bool, at_stop: float, sent: pd.Timestamp,
               latency_ms: float) -> ExplorationRecord:
        at_gate.append(at_stop)
        return ExplorationRecord(
            client_order_id=pid, account_id=eng.cfg.account_id, arm_id=arm.arm_id, family=arm.family, tf=arm.tf,
            side=side, lots=float(res.filled_lots if filled else gd.lots), signal_utc=s.bar_ts, sent_utc=sent,
            atr_usd=stop_d, stop_usd=gd.stop_distance, target_usd=target_d, hold_s=hold_s,
            requested_price=price, spread_usd=round(tick.ask - tick.bid, 6), latency_ms=round(latency_ms, 3),
            retcode=res.retcode, position_id=res.position_id if filled else None,
            fill_price=(res.price if res.price is not None else price) if filled else None,
            stop_loss=round(at_stop, 2), cost=round(cost, 2), fx_acct_per_usd=terms.get("acct_per_usd"))

    pos = cost_probe._place(eng, ex.db, intent, _agent(eng, arm, s.hold_bars, target_d / stop_d), repo=LEDGER,
                            pid=pid, close_ts=now, entry=price, target=price + side * target_d, max_loss=room,
                            headroom=room, applied=lambda: applied, refuse=refuse, action="exploration:",
                            journal={"agent": tag, "arm": arm.arm_id}, record=record,
                            over_cap=lambda at_stop: binding(at_stop) or "arm_cap")
    if pos is None:
        return "refused"
    ex.open_loss[pos] = at_gate[0] if at_gate else loss
    ps.note(arm, ex.open_loss[pos])
    return "sent"


def _intent(eng: Engine, arm: Arm, side: int, price: float, s: Pending, now: pd.Timestamp,
            terms: dict[str, Any], headroom: float) -> Intent | None:
    """The minimum-lot Intent of purpose `exploration` (lane L1's fence), in the rule's R: the stop is 1 "ATR" of
    `stop_usd`, the target its R multiple. No p is invented: 0.5, the gate skips only the edge rules for this purpose.
    None when the gate's Intent does not take it (fail closed)."""
    from goldbot.risk import Intent
    try:
        return Intent(agent_id=arm.tag, side=side, p=0.5, target_atr=s.target_usd / s.stop_usd, stop_atr=1.0,
                      atr_usd=s.stop_usd, cost_atr=eng._cost_atr(s.stop_usd, now), multiplier=1.0, price=price,
                      timeframe=arm.tf, family=arm.family, swap_usd_per_oz=eng._swap_usd_per_oz(side, now),
                      learning_probe=True, account_mode=eng.cfg.mode, phase_file=cost_probe._phase_file(eng),
                      purpose="exploration", arm_id=arm.arm_id,
                      terminal_trade_mode=cost_probe._terminal_trade_mode(eng),
                      hold_s=float(s.hold_bars * tf_seconds(arm.tf)), probe_headroom=headroom,
                      **{**terms, "volume_max": terms.get("volume_min")})
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


def sweep(eng: Engine, live: set[int]) -> None:
    """At every reconciliation (`reconcile_every_s` of tick time, besides the bar close): an exploration position the
    broker no longer lists whose exit deal is seen (its stop or target at the broker) is forgotten and its record
    queued, so its arm, pot and host slots and its open risk are free for the next signal instead of at the next
    decision-bar close (a 1m arm signals every minute). One whose exit deal is not seen yet, or whose deals cannot be
    read, is left to `Engine._sweep_closed` and its confirmation count at the bar close, as every other trade. Never
    raises."""
    try:
        for pid, tr in [(k, t) for k, t in eng.open.items() if k not in live and is_exploration(t.agent_id)]:
            try:
                summary, _ = eng._exit_deals(pid, tr)
            except Exception:                   # counted by _read_deals; the bar close's sweep asks again
                continue
            if summary is None:
                continue
            eng._unconfirmed.pop(pid, None)
            del eng.open[pid]
            eng._note_closed(pid, tr, "broker_close")
    except Exception:
        log.exception("exploration sweep failed; the bar close's sweep records the close")


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
    """Lane L3's fill row for a closed exploration trade (`exploration_data.record_fill`, keyed by the candidate id =
    this ledger's client order id; written once). `cost_per_oz`: commission and paid swap per oz in USD at the
    entry's rate (the spread is in the fill prices). The entry is the fill on the broker's clock, never after the
    exit. Best effort: a row L3 cannot take is logged, never raised (the exploration ledger is the trade's record)."""
    from goldbot.research.exploration_data import record_fill
    if r.fill_price is None or r.exit_price is None or r.exit_utc is None or not r.lots:
        return
    try:
        oz = r.lots * eng._contract_size()
        paid = (r.commission or 0.0) + max(0.0, -(r.swap or 0.0))
        entry = filled or r.sent_utc
        record_fill(db, candidate_id=r.client_order_id, entry_utc=min(entry, r.exit_utc), exit_utc=r.exit_utc,
                    entry_price=r.fill_price, exit_price=r.exit_price,
                    cost_per_oz=paid / (oz * r.fx_acct_per_usd) if r.fx_acct_per_usd and oz > 0 else 0.0,
                    censored=bool(r.censored))
    except Exception as exc:
        log.warning("exploration fill %s not recorded (%s)", r.client_order_id, type(exc).__name__)


def trades(state_dir: str, account_id: str | None = None) -> list[ExplorationRecord]:
    """The exploration ledger (one account's when given), for research (lane L3) and reports."""
    db = ledger_db(state_dir)
    with db.connection() as conn:
        if account_id is None:
            return LEDGER.select(conn)
        return LEDGER.select(conn, "account_id = ?", (account_id,))
