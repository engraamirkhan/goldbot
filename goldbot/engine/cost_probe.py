"""Demo cost probe: minimum-lot demo orders that mirror challengers' shadow entries, so costs are measured on the
broker rather than assumed (ADR 0037, amending ADR 0036; the research director's ruling 2026-10-19).

The ruling: "mirror challengers' SHADOW entry signals as minimum-lot (broker min, 0.01) demo orders, choosing which
signal by a fixed round-robin rotation over agent_ids (never by P&L/score). At most one probe position open at a time;
real (live-agent) trades take priority". A cost probe, not a population status: probes feed only the costs pipeline.

* Which signal: a shadow entry (a taken one first; below its threshold too, owner 2026-10-19) opened at this bar
  close by a challenger (a shadow version that is no agent's champion, `Engine.live_versions`) of an agent the broker may trade (not shadow only; TradingView agents never, and
  a frozen roster rule agent on a RuleModel by the ruling of 2026-10-19, `_mirrorable`). One signal per agent (its versions in name order); among the agents the next agent id
  after the last one probed, wrapping (`rotate`). p, score and P&L never reach the choice.
* When: on the shadow host with `cost_probe` on, while the account holds probes only (up to `cost_probe_max_open`):
  no other position at the broker, no engine trade, no pending proposal and no order in flight. A live agent's candidate closes an
  open probe before that agent's decision (`yield_to_live`) and any other position closes it at the bar close (`step`):
  the probe never holds the slot, the heat or the daily allowance a real trade needs. An ambiguous probe order still
  refuses every entry (`unconfirmed_order_pending`): an unknown order state outranks the priority rule.
* The lock: the account is demo AND the terminal reports demo AND the engine is in auto (`auto_entry.permitted`, read
  fresh at the order: a terminal that is not demo, the owner's /mode propose, the re-arm window and the kill switch
  stop probes as they stop auto entries). Enabled on any other account it is refused at load (`accounts.Account`) and at
  the engine's start (`check_start`).
* The order: the RiskGate's `check`, the only path to an order, on an Intent whose largest lot is the broker's minimum,
  so the gate sizes and checks exactly the lots sent under the account's demo caps; then `Engine._execute` (the
  pending_orders row first). The trade is tagged `cost_probe:<agent_id>` (`probe_id`), keeps the challenger's stop,
  target, time exit and exit policy, and exits automatically like every trade (exits are never gated).
* Identity at the broker (trading-safety review): a probe order carries its own magic, magic_base + PROBE_MAGIC_OFFSET
  (no strategy family's, not the bridge smoke's), and a `cp-` comment, so a probe found with no order row (a lost
  orders file) is adopted as a probe (`adoption`), never as a live agent's trade, and the broker ledger knows it.
* Budget (trading-safety review): no new probe once today's realised probe loss reaches `cost_probe_budget` of the
  day's loss allowance, nor while less than that share of the allowance remains (`budget_refusal`): probes never use
  the part of the £50 a real trade needs. An unknown allowance or an unknown probe P&L today refuses (fail closed).
* Amendment (the research director's ruling 2026-10-19, "the system needs to learn, so more trades need to be opened
  and closed"): up to `cost_probe_max_open` (2) probe positions at once, at most one new probe per bar close, never
  two on one agent; still 0.01 lot and round-robin. Each probe's loss at its stop, from the broker's `loss_per_lot`
  at the minimum lot in the account currency, is at most `cost_probe_max_loss` (£4); a probe whose loss cannot be
  computed is refused. Architect trace 2026-10-25: "mirror probes must be evidence of the strategy, so they run at
  the strategy's OWN stop and hold, or are skipped": a challenger whose own stop loses more than that at the minimum
  lot is skipped (`cost_probe_refused:native_stop_above_cap`), never sent with a tightened stop. Each skip is a
  `SkipRecord` in aux.db `cost_probe_skips` (never the probe ledger: no order went out) with its agent, timeframe,
  ATR and native loss; the dynamic probe count reads it beside the sent probes. The rule for every reading of mirror
  probes (quant review 2026-10-25): sent probes are a tight-stop, lower-volatility, shorter-timeframe sample of each
  strategy, so their costs and outcomes are split by agent x timeframe and read beside `skip_counts`, never pooled. No new probe once today's realised probe loss + the open probes' risk
  (loss at their stops) + the new probe's would exceed `cost_probe_budget` x the day's loss limit (0.2 x £50 = £10).
  The bound is at the stop: a gap through it, commission and swap are outside it (the realised P&L counts them).
* Dynamic limits (owner 2026-10-19, docs/design/dynamic-limits-2026-10-19.md item 4; the account's `dynamic_limits`):
  "per probe, max_loss = min(loss of 0.01 lot at the challenger's own label-spec stop x ATR, budget_share x daily
  limit / max_open). max_open_dyn = clamp(floor(budget_share x limit / median probe stop loss), 1, cfg.max_open).
  Config max_loss/max_open become the ceilings and the fallback" (`probe_limits`). Tighten only: neither value can
  exceed the configured one, and an unknown limit or an unreadable or empty ledger gives the configured values. Every
  probe decision and ledger row says which values applied and why (`probe_max_loss`, `probe_max_open`, `basis`).
* The record: one `ProbeRecord` per order in aux.db `cost_probes` (purpose cost_probe, with the agent id and version):
  signal, requested and fill price, spread, send latency and retcode; at the close the exit, swap, commission and P&L
  from the broker's deals. A probe's close never goes to closed_trades.jsonl (phase gate, throttle, broker-ledger
  trades, attribution) and its fill never to the store's `fills` (the nightly slippage table, attribution's live
  fills): costs-v2 reads `probes`, and refitting costs on them is a later preregistered step.
* Learning volume on a probe_only account (owner directive 2026-10-20, hundreds of demo fills): UNTAKEN_PER_DAY_PROBE_ONLY
  below-threshold probes a risk day are allowed (`untaken_cap`): such an account sends no real trade to keep a share
  for. A mirror probe holds its shadow trade's own time barrier (`_ProbeAgent`) on every account (architect trace
  2026-10-25; the former 4-bar probe_only cap cut probes short of the strategy's exit).
* Scheduled probes, the measured-cost protocol's M1 arm (docs/research/prereg-cost-measurement-2026-10.md, section 1:
  "0.01 lot at a fixed slot time; side = +1 on even slot index, -1 on odd ... market close after 30 s; server SL at $3
  from fill (safety only)"), one per 15m close when `cost_probe.scheduled.enabled` (`scheduled_step`). Same lock
  (demo terminal, auto mode), the same order core as a mirror probe (`_place`: `RiskGate.check`, then
  `Engine._execute`), its own magic (magic_base + SCHEDULED_MAGIC_OFFSET), a `cm-sched-` order id and the
  `scheduled_m1` basis, purpose `cost_measurement` on its ledger row. Not in the rollover blackout, after the Friday cut
  or on a weekend; one open at a time; within the probe budget (counted fully) and at most `budget_share` of it.
  Excluded from the dynamic probe count and from the mirror slots (prereg K10). Its close after `hold_s` on the host
  clock (`hold_exits`, every pass of `Engine._retry_closes`) is never gated: halted entries, probes switched off and a
  restart (the trade's open time) all still close it. Its row records the quote the close was sent on (K11) and the
  broker's account-currency rate at send and close (K9). A signal-less probe cannot honestly show the learning-probe
  fence's positive edge, so it asks the RiskGate for the `cost_measurement` purpose (prereg K8; ADR 0037 Amendment 5,
  owner-approved 2026-10-20): the gate skips only the EV requirement, and only for the minimum lot on a terminal that
  reads demo now, a stop <= $3, a hold <= 60 s and a loss at the stop within the budget headroom and the dynamic
  per-probe ceiling; every other limit applies. No p is invented to pass it.
"""
from __future__ import annotations

import logging
import math
import sqlite3
import statistics
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, cast

import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.timeutil import epoch_ns, server_wall
from goldbot.db import Database, RecordRepo
from goldbot.engine import auto_entry, exploration
from goldbot.engine import magic as magics
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.ops.accounts import ScheduledProbe

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.engine.shadow import ShadowTrade
    from goldbot.engine.store import OpenTrade
    from goldbot.execution.broker import OrderResult, Tick
    from goldbot.risk import Intent
    from goldbot.risk.gate import GateDecision
    from goldbot.specialists.base import Specialist

log = logging.getLogger(__name__)

PREFIX = "cost_probe:"
YIELD = "cost_probe_yield"          # the exit of a probe that makes way for a real trade
PROBE_MAGIC_OFFSET = magics.PROBE_OFFSET          # magic_base + 97 (goldbot/engine/magic.py: every reserved offset)
COMMENT = "cp-"                     # a probe's client order id, and so its MT5 comment, starts with this
# directors' ruling 2026-10-19 (ADR 0037 amendment): below-threshold (untaken) probes at most this many a risk day, at
# most half the open slots, and only while more than half the probe budget is left: taken entries keep the rest
UNTAKEN_PER_DAY = 12                # amendment 3 (2026-10-19): was 6
UNTAKEN_PER_DAY_PROBE_ONLY = 48     # owner directive 2026-10-20 (learning volume): a probe_only account keeps no reserve
PER_CLOSE = 2                       # amendment 3: new probes per bar close at most, from different agents
MAX_SAME_SIDE = 2                   # amendment 3: open probes on one side at most (one move, correlated losses)
# amendment 3: no new probe once the day's allowance left after the open probes' risk and the new probe's max loss
# would fall below this share of the day's loss limit (0.6 x £50 = £30: three real trades at the 1% cap)
REAL_TRADE_RESERVE = 0.6
# dynamic limits: the median loss at the strategy's own (unclamped) stop of this many most recent probes sets the
# dynamic probe count, once at least PROBE_LOSS_MIN_ROWS probes have recorded it (fewer: the configured count)
PROBE_LOSS_WINDOW = 20
PROBE_LOSS_MIN_ROWS = 5
# the measured-cost protocol's M1 arm (scheduled probes)
SCHEDULED = "scheduled"                     # the ledger's agent id of a scheduled probe
SCHEDULED_ID = PREFIX + SCHEDULED           # its trade tag
SCHEDULED_MAGIC_OFFSET = magics.SCHEDULED_PROBE_OFFSET   # magic_base + 96 (goldbot/engine/magic.py)
SCHEDULED_COMMENT = "cm-sched-"             # its order id and MT5 comment (the prereg's `cm-`; "sched" for the counter)
SCHEDULED_BASIS = "scheduled_m1"            # its ledger rows' basis
SCHEDULED_FAMILY = "cost_measurement"       # its Intent's family and its ledger rows' purpose
HOLD_EXIT = "cost_probe_hold"               # the exit of a scheduled probe held hold_s
NATIVE_SKIP = "native_stop_above_cap"       # a mirror probe whose own stop loses more than the ceiling: skipped
SLOT_S = 900                                # one slot per 15m close; the side alternates with the slot index


def real_trade_reserve(probe_only: bool) -> float:
    """The share of the day's loss limit kept for real trades. ADR 0037 amendment 4: a probe_only account sends no
    real trade (Engine._execute refuses one), so it keeps none and its probe budget alone binds, inside its own daily
    limit, which the RiskGate enforces unchanged."""
    return 0.0 if probe_only else REAL_TRADE_RESERVE


def untaken_cap(probe_only: bool) -> int:
    """Below-threshold probes a risk day: UNTAKEN_PER_DAY, or UNTAKEN_PER_DAY_PROBE_ONLY on a probe_only account (owner
    directive 2026-10-20: the demo is for learning; that account has no real trade whose share they could take)."""
    return UNTAKEN_PER_DAY_PROBE_ONLY if probe_only else UNTAKEN_PER_DAY


def probe_id(agent_id: str) -> str:
    """The tag a probe trades under (its open trade, order row and journal): never the agent's own id, so the agent's
    one-position rule, its throttle and its closed-trade record never see the probe."""
    return PREFIX + agent_id


def refuse_non_probe(eng: Engine, agent_id: str, proposal_id: str) -> bool:
    """ADR 0037 amendment 4: "probes must never consume the real trades' allowance", and a dedicated probe account
    (`probe_only`) never sends a real trade either: whatever reached the order path there (a live share, an approval)
    is refused and journalled, fail closed. Exploration arms are that account's own entries (plan 2026-10-25). True
    when refused."""
    if not eng.cfg.probe_only or is_learning(agent_id):
        return False
    eng.decisions.append({"ts": time.time(), "agent": agent_id, "action": "probe_only_refused", "proposal": proposal_id})
    return True


def is_probe(agent_id: str | None) -> bool:
    return bool(agent_id) and str(agent_id).startswith(PREFIX)


def is_learning(agent_id: str | None) -> bool:
    """A probe or an exploration arm: its fills and closes feed its own ledger, never the trade record or `fills`."""
    return is_probe(agent_id) or exploration.is_exploration(agent_id)


def magic(eng: Engine, agent: Specialist) -> int:
    """The order's magic: a scheduled probe's, a mirror probe's or an exploration arm's own, else the agent's family
    magic (`magic.family`)."""
    if agent.agent_id == SCHEDULED_ID:
        return eng.cfg.magic_base + SCHEDULED_MAGIC_OFFSET
    if exploration.is_exploration(agent.agent_id):
        return magics.exploration(eng.cfg.magic_base)
    return eng.cfg.magic_base + PROBE_MAGIC_OFFSET if is_probe(agent.agent_id) else magics.family(eng.cfg.magic_base,
                                                                                                  agent.family)


def is_probe_magic(m: int, magic_base: int) -> bool:
    """A mirror probe's magic or a scheduled probe's: the broker ledger reconciles both in the probe ledger."""
    return int(m) in (magic_base + PROBE_MAGIC_OFFSET, magic_base + SCHEDULED_MAGIC_OFFSET)


def agent_of(eng: Engine, agent_id: str | None) -> Specialist | None:
    """The specialist of an agent id, a probe's tag included (its challenger: barriers and exit policy)."""
    return eng.agents.get(str(agent_id).removeprefix(PREFIX)) if agent_id else None


def adoption(eng: Engine, p: Any, rec: Any) -> tuple[Specialist | None, str]:
    """(specialist, agent id) for a position `Engine._adopt` takes over. With its order row: the row's agent (a probe's
    tag kept, its challenger's specialist). Without one, an orphan: a probe's magic gives `cost_probe:orphan` (recorded
    in the probe ledger, never in the trade record), else the agent of the magic number when exactly one agent here
    has that family's magic."""
    if rec is not None:
        if exploration.is_exploration(rec.agent_id):
            return exploration.agent_for(eng, rec.agent_id), rec.agent_id
        return agent_of(eng, rec.agent_id), rec.agent_id
    if magics.is_exploration(p.magic, eng.cfg.magic_base):
        return exploration.adoption(eng, p)          # by its comment; an unknown arm is closed at once (`exits`)
    if int(p.magic) == eng.cfg.magic_base + SCHEDULED_MAGIC_OFFSET:
        return None, SCHEDULED_ID                   # its hold exit still closes it (`hold_exits`)
    if is_probe_magic(p.magic, eng.cfg.magic_base):
        return None, PREFIX + "orphan"
    hits = [a for a in eng.agents.values() if magics.family(eng.cfg.magic_base, a.family) == p.magic]
    agent = hits[0] if len(hits) == 1 else None
    return agent, "orphan" if agent is None else agent.agent_id


def probe_positions(deals: pd.DataFrame, magic_base: int) -> set[int]:
    """Position ids with a probe's or an exploration arm's magic in a deal history (MT5 columns position_id, magic)."""
    if deals is None or deals.empty or "magic" not in deals.columns:
        return set()
    return {int(pid) for pid, m in zip(deals["position_id"], deals["magic"])
            if pid and (is_probe_magic(m, magic_base) or magics.is_exploration(m, magic_base))}


def check_start(mode: str, enabled: bool) -> None:
    """The engine's start: probes on an account that is not demo fail loudly (ADR 0037), never run quietly."""
    if enabled and mode != "demo":
        raise ValueError(f"cost probes are for demo accounts only (ADR 0037): a {mode} account never probes")


def rotate(agent_ids: list[str], last: str | None) -> str:
    """The fixed round-robin: the first agent id after `last` in sorted order, wrapping. Agent ids only, so nothing
    else (p, score, P&L) can choose the probe."""
    ids = sorted(set(agent_ids))
    return next((a for a in ids if last is not None and a > last), ids[0])


class ProbeRecord(FrozenRecord):
    """One probe order and, once closed, its exit (aux.db `cost_probes`). Prices in USD per oz; money in the account
    currency as the broker reports it."""
    client_order_id: str
    account_id: str
    purpose: Literal["cost_probe", "cost_measurement"] = "cost_probe"     # cost_measurement: a scheduled probe
    agent_id: str                           # the challenger's agent id (the trade's tag is probe_id(agent_id))
    version: str | None = None              # the shadow version mirrored (None: recorded from the trade alone)
    side: int
    lots: float
    signal_utc: UtcTimestamp | None = None  # the signal bar's close
    signal_price: float | None = None       # the shadow entry: the signal bar's close on the paying side
    requested_price: float | None = None    # the quote the order was priced from
    spread_usd: float | None = None         # ask - bid of that quote
    sent_utc: UtcTimestamp
    latency_ms: float | None = None         # send to result as the engine saw it (the pending_orders write included)
    retcode: int | None = None
    position_id: int | None = None
    fill_price: float | None = None
    exit_utc: UtcTimestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    swap: float | None = None               # broker's deals (None: not reported)
    commission: float | None = None         # paid, positive (None: not reported)
    pnl: float | None = None                # net, from the broker's deals (None: the deals were not readable)
    stop_loss: float | None = None          # the loss at the stop when sent (clamped on rows before 2026-10-25)
    # the mirrored shadow entry cleared its threshold; False = a below-threshold learning probe (owner 2026-10-19),
    # capped by UNTAKEN_PER_DAY and half the slots and budget. Rows written before carry True (they were all taken)
    taken: bool = True
    # dynamic limits: the per-probe loss ceiling and the probe count that applied, and why (`ProbeLimits.basis`); None
    # on rows written before
    probe_max_loss: float | None = None
    probe_max_open: int | None = None
    basis: str | None = None
    # the loss at the strategy's own stop at the lots sent, account currency: what the dynamic probe count reads
    # (`stop_loss` was clamped to the ceiling on rows before 2026-10-25, so its median could never tighten the count).
    # None on rows before
    native_stop_loss: float | None = None
    # prereg K11: the quote the engine's own close was sent on (a scheduled probe's hold exit); None otherwise
    exit_bid: float | None = None
    exit_ask: float | None = None
    exit_quote_utc: UtcTimestamp | None = None
    # prereg K9: the broker's account currency per USD of XAUUSD P&L at send and at close (None: not read)
    fx_acct_per_usd: float | None = None
    exit_fx_acct_per_usd: float | None = None

    @property
    def slippage_usd(self) -> float | None:
        """Signal to fill, positive = paid: the cost a shadow entry does not see."""
        if self.fill_price is None or self.signal_price is None:
            return None
        return self.side * (self.fill_price - self.signal_price)

    @property
    def exec_slippage_usd(self) -> float | None:
        """Requested quote to fill, positive = paid: the broker's part of `slippage_usd`."""
        if self.fill_price is None or self.requested_price is None:
            return None
        return self.side * (self.fill_price - self.requested_price)


def _ns(ts: pd.Timestamp) -> int:
    return int(epoch_ns(pd.DatetimeIndex([ts]))[0])


LEDGER: RecordRepo[ProbeRecord] = RecordRepo("cost_probes", ProbeRecord, key="client_order_id", tolerant=True, columns={
    "client_order_id": lambda r: r.client_order_id, "account_id": lambda r: r.account_id,
    "agent_id": lambda r: r.agent_id, "position_id": lambda r: r.position_id, "ts_ns": lambda r: _ns(r.sent_utc),
    "closed": lambda r: int(r.exit_utc is not None)})


class SkipRecord(FrozenRecord):
    """A mirror probe skipped because its strategy's own stop loses more than the probe ceiling at the minimum lot
    (aux.db `cost_probe_skips`; architect trace 2026-10-25). No order was sent, so it is never a ProbeRecord: the
    dynamic probe count reads its native loss beside the sent probes' (quant review: sent probes alone only ever show
    stops inside the ceiling), and `skip_counts` splits the skips by agent x timeframe."""
    client_order_id: str                    # the id the probe would have had (one attempt per agent per bar close)
    account_id: str
    agent_id: str
    version: str
    timeframe: str
    side: int
    taken: bool                             # the mirrored shadow entry cleared its threshold
    signal_utc: UtcTimestamp                # the signal bar's close
    atr_usd: float                          # the signal bar's ATR as the shadow entry used it (the regime's scale)
    native_stop_loss: float                 # the loss at the strategy's own stop at the minimum lot, account currency
    probe_max_loss: float                   # the ceiling it exceeded
    basis: str | None = None                # which limits applied and why (`ProbeLimits.basis`)


SKIPS: RecordRepo[SkipRecord] = RecordRepo("cost_probe_skips", SkipRecord, key="client_order_id", tolerant=True,
                                           columns={"client_order_id": lambda r: r.client_order_id,
                                                    "account_id": lambda r: r.account_id,
                                                    "agent_id": lambda r: r.agent_id,
                                                    "ts_ns": lambda r: _ns(r.signal_utc)})


class _ProbeAgent:
    """The challenger's specialist under the probe's tag: its family (magic), timeframe, barriers and exit policy;
    with `max_bars`, its time barrier set to that many of its bars (the shadow trade's own, which a candidate's
    research labels may set apart from the label spec's)."""

    def __init__(self, agent: Specialist, max_bars: int | None = None):
        self._agent = agent
        self._max_bars = max_bars

    @property
    def agent_id(self) -> str:
        return probe_id(self._agent.agent_id)

    @property
    def label_spec(self) -> BarrierSpec:
        ls = self._agent.label_spec
        if self._max_bars is None or ls.max_bars == self._max_bars:
            return ls
        return ls.model_copy(update={"max_bars": self._max_bars})

    def __getattr__(self, name: str) -> Any:
        return getattr(self._agent, name)


class _ScheduledAgent:
    """What `Engine._execute` reads of an agent, for a scheduled probe: its tag (own magic), its family, the base
    timeframe and a time barrier of one base bar (a backstop: the hold exit closes it after hold_s), no exit policy."""

    def __init__(self, timeframe: str):
        self.agent_id, self.family, self.timeframe = SCHEDULED_ID, SCHEDULED_FAMILY, timeframe
        self.label_spec = BarrierSpec(target_atr=1.0, stop_atr=1.0, max_bars=0, name=SCHEDULED_FAMILY, has_target=False)
        self.exit_spec = None
        self.shadow_only = self.requires_tv = False


class CostProbe:
    """The engine's probe state: the ledger, the shadow entries already seen and the rotation's last agent. Without
    the shadow book (a learning host, `shadow_host: false`) it runs the scheduled probes only (`step`)."""

    def __init__(self, eng: Engine):
        self.db = Database.open(eng.cfg.state_dir, "aux")
        # entries open at the start are old: never probed after a restart (none to see without the shadow book)
        self.seen = _open_keys(eng) if eng.shadow is not None else set()
        with self.db.connection() as conn:
            r = conn.execute("SELECT agent_id FROM cost_probes WHERE account_id = ? AND agent_id != ? "
                             "ORDER BY ts_ns DESC LIMIT 1", (eng.cfg.account_id, SCHEDULED)).fetchone()
        self.last_agent: str | None = str(r[0]) if r else None
        # untaken probes sent by this process (client order id -> sent): counted even when the order raised and no
        # ledger row was written (safety review LOW-2)
        self.untaken_sent: dict[str, pd.Timestamp] = {}
        self._eng = eng
        self._dynamic: bool | None = None          # set only by tests; None = read the account flag
        # scheduled probes: position id -> when its hold began (the hold clock); position id -> the close's quote
        self.held: dict[int, pd.Timestamp] = {}
        self.exit_quotes: dict[int, tuple[float, float, pd.Timestamp]] = {}

    @property
    def dynamic(self) -> bool:
        """Dynamic limits: the account's `dynamic_limits` (accounts.yaml), read as the engine's limits read it
        (`EngineLimits.dynamic`), at use: the probe starts before the engine's limits exist. Off when unknown, so
        the configured values apply (fail closed)."""
        if self._dynamic is not None:
            return self._dynamic
        eng = self._eng
        return bool(getattr(eng.cfg, "dynamic_limits", False)) or \
            bool(getattr(getattr(eng, "_limits", None), "dynamic", False))

    @dynamic.setter
    def dynamic(self, on: bool) -> None:
        self._dynamic = on

    @classmethod
    def start(cls, eng: Engine) -> CostProbe | None:
        check_start(eng.cfg.mode, eng.cfg.cost_probe)
        if not eng.cfg.cost_probe:
            return None
        if eng.shadow is None:
            # a learning host without the shadow book (vantage-demo, live 2026-10-26: its scheduled probes never ran)
            # still measures its own costs with the scheduled probes; mirror probes need the shadow book
            if not eng.cfg.cost_probe_scheduled.enabled:
                log.warning("cost probe on, but this engine is not the shadow host and scheduled probes are off: "
                            "nothing to probe")
                return None
            log.info("cost probe on without the shadow book: scheduled probes only, no mirror probes")
        return cls(eng)


def _open_keys(eng: Engine) -> set[tuple[str, str, pd.Timestamp]]:
    assert eng.shadow is not None
    return {(v, t.agent_id, t.entry_ts) for v, b in eng.shadow.books.items() for t in b.open}


def _mirrorable(eng: Engine, version: str, agent: Specialist) -> bool:
    """Trading-safety ruling 2026-10-19: a TradingView agent is never mirrored; another shadow-only agent only when it
    is a frozen roster rule agent whose shadow version is a RuleModel (ADR 0038). Its probe measures cost only (never
    the agent's evidence: probes stay out of closed_trades); demo only, RiskGate, own magic and budget as every probe."""
    if agent.requires_tv:
        return False
    if not agent.shadow_only:
        return True
    from goldbot.research.rule_model import is_uncalibrated
    return bool(getattr(agent, "frozen_config", False)) and is_uncalibrated(eng.shadow_models[version][1])


def _signals(eng: Engine, probe: CostProbe) -> dict[str, tuple[str, ShadowTrade, Specialist]]:
    """agent id -> (version, shadow entry, specialist): this close's new challenger entries, one per agent, a taken one
    first. Owner directive 2026-10-19 (learning needs trades): an entry below its threshold is mirrored too; the
    RiskGate still checks it as any probe (a negative-EV one is refused there, and the refusal is journalled)."""
    assert eng.shadow is not None
    now = _open_keys(eng)
    new, probe.seen = now - probe.seen, now
    champions = set(eng.live_versions.values())
    out: dict[str, tuple[str, ShadowTrade, Specialist]] = {}
    for version in sorted(eng.shadow.books):
        if version in champions or version not in eng.shadow_models:
            continue
        for t in eng.shadow.books[version].open:
            agent = eng.agents.get(t.agent_id)
            if (version, t.agent_id, t.entry_ts) in new and agent is not None and _mirrorable(eng, version, agent):
                if t.agent_id not in out or (t.taken and not out[t.agent_id][1].taken):
                    out[t.agent_id] = (version, t, agent)
    return out


def _probes_open(eng: Engine) -> list[int]:
    return [pid for pid, t in eng.open.items() if is_probe(t.agent_id)]


def _mirror_probes_open(eng: Engine) -> list[int]:
    """The open probes that hold a mirror slot: every probe but a scheduled one (prereg K10: scheduled probes stay out
    of `max_open`; their risk still counts in the budget, `open_risk`)."""
    return [pid for pid in _probes_open(eng) if (t := eng.open.get(pid)) is None or t.agent_id != SCHEDULED_ID]


def _in_flight(eng: Engine, probes: bool) -> bool:
    from goldbot.engine import unconfirmed
    return any(o.status in ("sending", unconfirmed.AMBIGUOUS) and is_probe(o.agent_id) == probes
               for o in eng._orders.values())


def _reread(eng: Engine, close_ts: pd.Timestamp | None) -> None:
    """The account read again after a probe order or a probe's exit; a failed read stops auto entries for this bar
    close (fail closed: no entry on unknown facts)."""
    try:
        eng._refresh_account(eng.broker.last_tick(eng.cfg.symbol))
    except Exception as exc:
        log.warning("account re-read after a cost probe failed (%s): auto entries stop for this bar close",
                    type(exc).__name__)
        if close_ts is not None:
            eng._auto_paused = eng._auto_paused or (close_ts, "account_unread")


def _make_way(eng: Engine, close_ts: pd.Timestamp | None) -> None:
    """Close the open probes; one already due to close is left to `_retry_closes` (its reason and backoff kept)."""
    closed = [eng._try_close(pid, YIELD, None).ok for pid in _probes_open(eng) if pid not in eng._close_due]
    if any(closed):
        _reread(eng, close_ts)


def yield_to_live(eng: Engine, agents: list[Specialist], cands_by_agent: dict[str, pd.DataFrame], last: int,
                  close_ts: pd.Timestamp) -> None:
    """Real trades take priority: a live agent (a broker share above 0) with a candidate at this close closes the open
    probe before its decision, and the account is read again, so the probe never takes what the trade needs. A close
    the broker refuses stays due and is retried like any close (the gate then still counts the probe: fail closed)."""
    if not _probes_open(eng):
        return
    for a in agents:
        share = 0.0 if a.shadow_only else 1.0 if eng.live_shares is None else eng.live_shares.get(a.agent_id, 0.0)
        cands = cands_by_agent.get(a.agent_id)
        if share > 0 and cands is not None and not cands.empty and int(cands["idx"].iloc[-1]) == last:
            _make_way(eng, close_ts)
            return


def step(eng: Engine, close_ts: pd.Timestamp, tick: Tick) -> None:
    """At the end of a bar close's decisions: close an open probe if any other position or order exists, else send at
    most one probe for this close's new challenger entries (see the module docstring). A probe never fails the bar
    close: an error is logged and journalled (`cost_probe_error`), and the shadow save, the state file and the journal
    that follow still run (an order that raised is handled in `_send` like an auto entry's)."""
    probe = eng.probe
    if probe is None:
        return
    try:
        if eng.shadow is not None:          # mirror probes need the shadow book; scheduled ones below do not
            _step(eng, probe, close_ts, tick)
    except Exception as exc:
        log.exception("cost probe step failed at %s; no probe this bar close", close_ts)
        eng.decisions.append({"ts": close_ts.isoformat(), "agent": None, "action": "cost_probe_error",
                              "error": type(exc).__name__})
    try:
        scheduled_step(eng, probe, close_ts, tick)
    except Exception as exc:
        log.exception("scheduled cost probe failed at %s; none this bar close", close_ts)
        eng.decisions.append({"ts": close_ts.isoformat(), "agent": SCHEDULED_ID, "action": "cost_probe_scheduled_error",
                              "error": type(exc).__name__})


def _step(eng: Engine, probe: CostProbe, close_ts: pd.Timestamp, tick: Tick) -> None:
    signals = _signals(eng, probe)
    if signals and exploration.paused(eng, close_ts):      # ADR 0043 X4: -8% on the probe host pauses mirror probes
        eng.decisions.append({"ts": close_ts.isoformat(), "agent": None, "action": "exploration_paused_dd",
                              "purpose": "cost_probe", "drawdown": exploration.drawdown(eng)})
        return
    probes_open = _probes_open(eng)
    others = any(not is_probe(t.agent_id) for t in eng.open.values()) or bool(eng.pending) or _in_flight(eng, False)
    if others or eng.state.open_positions > len(probes_open):
        if probes_open:
            _make_way(eng, close_ts)
        return
    # amendment 3 (directors' ruling 2026-10-19): up to PER_CLOSE new probes per bar close, from different agents, each
    # checked afresh (slots, budget, untaken caps, the same-side cap) after the one before it
    for _ in range(PER_CLOSE):
        probes_open = _mirror_probes_open(eng)          # prereg K10: a scheduled probe holds no mirror slot
        lim = probe_limits(eng, probe)
        if len(probes_open) >= lim.max_open or _in_flight(eng, True):
            return
        busy = {eng.open[pid].agent_id.removeprefix(PREFIX) for pid in probes_open}
        signals = {a: sig for a, sig in signals.items() if a not in busy}       # never two probes on one agent
        sides = [eng.open[pid].side for pid in probes_open]
        signals = {a: sig for a, sig in signals.items() if sides.count(sig[1].side) < MAX_SAME_SIDE}
        if not signals:
            return
        if eng._auto_paused is not None and eng._auto_paused[0] == close_ts:
            return
        if eng.cfg.mode != "demo" or not auto_entry.permitted(eng, close_ts):
            return
        why, headroom = budget_refusal(eng, probe, close_ts)
        if why is not None:
            eng.decisions.append({"ts": close_ts.isoformat(), "agent": None, "action": f"cost_probe_budget:{why}"})
            return
        untaken_why = untaken_refusal(eng, probe, close_ts, headroom, lim.max_open)
        if untaken_why is not None and not any(sig[1].taken for sig in signals.values()):
            eng.decisions.append({"ts": close_ts.isoformat(), "agent": None,
                                  "action": f"cost_probe_untaken:{untaken_why}"})
            return
        if untaken_why is not None:
            signals = {a: sig for a, sig in signals.items() if sig[1].taken}
        version, t, agent = signals.pop(rotate(list(signals), probe.last_agent))
        if not t.taken:
            headroom -= eng.cfg.cost_probe_budget * _limit(eng) / 2      # the taken entries' half stays untouched
        probe.last_agent = agent.agent_id
        _send(eng, probe, version, t, agent, close_ts, tick, headroom, lim)
        if not signals:
            return


class ProbeLimits(FrozenRecord):
    """The probe count and the per-probe loss ceiling that apply now, and why. Fixed (the configured values) unless
    the account has dynamic limits; a dynamic value never exceeds the configured one (it is the ceiling)."""
    max_open: int
    max_loss: float              # the ceiling of one probe's loss at its stop; its own stop applies when inside it
    loss_basis: str              # fixed | config_cap | budget_cap | fixed:<why the dynamic value is unknown>
    open_basis: str              # fixed | config_cap | budget | fixed:<why the dynamic value is unknown>

    def basis(self, loss_basis: str | None = None) -> str:
        return f"max_loss={loss_basis or self.loss_basis};max_open={self.open_basis}"


def probe_limits(eng: Engine, probe: CostProbe) -> ProbeLimits:
    """Design item 4 (docs/design/dynamic-limits-2026-10-19.md): the per-probe ceiling is min(cfg max_loss,
    budget_share x the day's loss limit / cfg max_open) and the count clamp(floor(budget_share x limit / the median loss
    at the strategy's own stop of the last PROBE_LOSS_WINDOW probe attempts, sent or skipped), 1, cfg max_open).
    The median is of the native
    loss (quant review: the loss as sent was clamped to the ceiling before 2026-10-25, so it could never tighten the
    count; since then a probe is sent at its own stop or skipped, and old rows still carry their clamped stop_loss).
    The day's limit is already dynamic (ADR 0031), so both tighten with it; budget_share stays static. Fail closed: an
    unknown limit, an unreadable ledger or fewer than PROBE_LOSS_MIN_ROWS probes with a native loss gives the
    configured value."""
    cfg_open, cfg_loss = eng.cfg.cost_probe_max_open, eng.cfg.cost_probe_max_loss
    if not probe.dynamic:
        return ProbeLimits(max_open=cfg_open, max_loss=cfg_loss, loss_basis="fixed", open_basis="fixed")
    try:
        limit = _limit(eng)
    except Exception as exc:
        log.warning("daily loss limit unreadable for the probe limits (%s): configured values", type(exc).__name__)
        limit = math.nan
    if not (math.isfinite(limit) and limit > 0):
        return ProbeLimits(max_open=cfg_open, max_loss=cfg_loss, loss_basis="fixed:limit_unknown",
                           open_basis="fixed:limit_unknown")
    budget = eng.cfg.cost_probe_budget * limit
    share = budget / cfg_open
    max_loss, loss_basis = (cfg_loss, "config_cap") if cfg_loss <= share else (share, "budget_cap")
    try:
        with probe.db.connection() as conn:
            rows = LEDGER.select(conn, "account_id = ?", (eng.cfg.account_id,), order_by="ts_ns")
            skipped = SKIPS.select(conn, "account_id = ?", (eng.cfg.account_id,), order_by="ts_ns")
    except Exception as exc:
        log.warning("cost probe ledger unreadable (%s): the configured probe count", type(exc).__name__)
        return ProbeLimits(max_open=cfg_open, max_loss=max_loss, loss_basis=loss_basis,
                           open_basis="fixed:ledger_unreadable")
    # every attempt's own stop, sent or skipped (quant review 2026-10-25: a probe is sent only at a stop inside the
    # ceiling, so the sent ones alone would always give the configured count), the most recent by signal time
    attempts = [(r.signal_utc or r.sent_utc, float(r.native_stop_loss)) for r in rows
                if r.purpose == "cost_probe"                                            # prereg K10: never scheduled
                and r.native_stop_loss is not None and r.native_stop_loss > 0]
    attempts += [(k.signal_utc, k.native_stop_loss) for k in skipped if k.native_stop_loss > 0]
    recent = [loss for _, loss in sorted(attempts, key=lambda a: a[0])][-PROBE_LOSS_WINDOW:]
    if len(recent) < PROBE_LOSS_MIN_ROWS:
        return ProbeLimits(max_open=cfg_open, max_loss=max_loss, loss_basis=loss_basis, open_basis="fixed:no_history")
    n = math.floor(budget / statistics.median(recent))
    max_open, open_basis = (cfg_open, "config_cap") if n >= cfg_open else (max(1, n), "budget")
    return ProbeLimits(max_open=max_open, max_loss=max_loss, loss_basis=loss_basis, open_basis=open_basis)


def stop_loss(eng: Engine, side: int, price: float, distance: float, lots: float) -> float | None:
    """The account-currency loss of `lots` when the price moves `distance` against `side` from `price`: the broker's
    `loss_per_lot` (MT5 order_calc_profit, else tick value / tick size). None when the broker cannot say (fail closed)."""
    fn = getattr(eng.broker, "loss_per_lot", None)
    if not callable(fn) or not (math.isfinite(distance) and distance >= 0 and math.isfinite(lots) and lots > 0):
        return None
    if distance == 0:
        return 0.0
    try:
        v = fn(eng.cfg.symbol, side, price, distance)
        loss = float(v) * lots if v is not None else math.nan
    except Exception as exc:
        log.warning("cost probe loss unavailable (%s)", type(exc).__name__)
        return None
    return loss if math.isfinite(loss) and loss > 0 else None


def open_risk(eng: Engine) -> float | None:
    """The open probes' loss at their current stops (0 for one whose stop is at or past its entry); None when any is
    unknown (no stop, no fill price or no broker figure)."""
    total = 0.0
    for pid in _probes_open(eng):
        tr = eng.open[pid]
        entry = tr.open_price if tr.open_price is not None else tr.entry
        if tr.sl is None or entry is None:
            return None
        loss = stop_loss(eng, tr.side, entry, max(0.0, tr.side * (entry - tr.sl)), tr.lots)
        if loss is None:
            return None
        total += loss
    return total


def budget_refusal(eng: Engine, probe: CostProbe, close_ts: pd.Timestamp) -> tuple[str | None, float]:
    """(why the probe budget refuses a new probe now or None, the loss a new probe may still add). The budget is
    `cost_probe_budget` x the day's loss limit (0.2 x £50 = £10): refused when the allowance is unknown, the open
    probes' risk is unknown, or today's realised probe loss (`realised_loss`) + the open probes' risk leaves nothing.
    On a shared account also while less than the budget remains or a new probe would cut into the real trades'
    reserve. A probe_only account keeps no reserve (ADR 0037 amendment 4), so it is refused only when its own day
    cannot take one more probe at its worst case; "less than the budget remains" no longer applies there (trading-
    safety review 2026-10-20: it stopped every probe once a £30 day was down ~£3). Today is the engine's risk day
    (from its last reset; the UTC day before the first). A probe profit never adds headroom."""
    from goldbot.engine.limits_apply import daily_allowance
    allowance = daily_allowance(eng.gate, eng.state)
    limit, remaining = allowance["daily_loss_limit"], allowance["daily_loss_remaining"]
    if limit is None or remaining is None or limit <= 0:
        return "allowance_unknown", 0.0
    share = eng.cfg.cost_probe_budget * limit
    reserve = real_trade_reserve(eng.cfg.probe_only) * limit
    if reserve > 0 and remaining < share:
        return "allowance_low", 0.0
    since = eng._last_reset if eng._last_reset is not None else close_ts.floor("D")
    held = open_risk(eng)
    if held is None:
        return "open_risk_unknown", 0.0
    headroom = share - realised_loss(eng, probe, since) - held
    if headroom <= 0:
        return "probe_loss_reached", 0.0
    if remaining - held - min(eng.cfg.cost_probe_max_loss, headroom) < reserve:   # real trades' part (0 on probe_only)
        return ("real_trade_reserve" if reserve > 0 else "allowance_low"), 0.0
    return None, headroom


def realised_loss(eng: Engine, probe: CostProbe, since: pd.Timestamp, agent_id: str | None = None) -> float:
    """Today's realised probe loss (>= 0; a profit never offsets it) over the rows closed since `since` (one agent's
    when given). A row closed with no P&L (`record_close` ran before the exit deal reached the broker's history) is
    read again from the deal history and the P&L written back; one still unknown counts at its worst case, the
    larger of `cost_probe_max_loss` and its loss at the stop, so an unknown P&L neither under-counts nor freezes every
    probe until the risk-day reset (code review 2026-10-20). A failed deal read counts the same worst case."""
    where, args = "account_id = ?" + (" AND agent_id = ?" if agent_id is not None else "") + " AND closed = 1", \
        tuple(x for x in (eng.cfg.account_id, agent_id) if x is not None)
    with probe.db.connection() as conn:
        rows = [r for r in LEDGER.select(conn, where, args) if r.exit_utc is not None and r.exit_utc >= since]
    pnl = 0.0
    for r in rows:
        v = r.pnl if r.pnl is not None else _pnl_from_deals(eng, probe, r)
        pnl += v if v is not None else -max(eng.cfg.cost_probe_max_loss, r.stop_loss or 0.0)
    return max(0.0, -pnl)


def _pnl_from_deals(eng: Engine, probe: CostProbe, r: ProbeRecord) -> float | None:
    """A closed row's P&L read again from the broker's deals (written back to the ledger), or None when the exit deal
    is still not there, the row has no position or the read fails."""
    if r.position_id is None:
        return None
    from goldbot.engine.store import OpenTrade
    t0 = min(x for x in (r.signal_utc, r.sent_utc, r.exit_utc) if x is not None)     # the deal search starts before it
    tr = OpenTrade(position_id=r.position_id, agent_id=PREFIX + r.agent_id, side=r.side, lots=r.lots,
                   entry_bar_ts=t0, max_bars=0, opened_utc=t0)
    try:
        info, _ = eng._exit_deals(r.position_id, tr)
    except Exception as exc:
        log.warning("cost probe %s: deal history unreadable (%s); counted at its worst case", r.client_order_id,
                    type(exc).__name__)
        return None
    if info is None or info.get("pnl") is None:
        return None
    update = {"pnl": info["pnl"], **{k: info[k] for k in ("swap", "commission") if getattr(r, k) is None}}
    try:
        with probe.db.write() as conn:
            LEDGER.upsert(conn, r.model_copy(update=update))
    except Exception as exc:        # the figure still counts this pass; the write is retried on the next read
        log.warning("cost probe %s P&L not written back (%s)", r.client_order_id, type(exc).__name__)
    return float(info["pnl"])


def _phase_file(eng: Engine) -> Path:
    """The roadmap phase file the gate reads for a learning probe (F1): the repo's absolute state/phase_state.json
    (accounts.PHASE_FILE, as the bridge smoke) when the engine runs on the repo's state dir; else the engine's own
    state dir resolved (tests, a second state dir). Never relative to the working directory (safety review LOW-1)."""
    from goldbot.ops import accounts
    own = Path(eng.cfg.state_dir).resolve()
    return accounts.PHASE_FILE if own == accounts.PHASE_FILE.parent.resolve() else own / "phase_state.json"


def _limit(eng: Engine) -> float:
    from goldbot.engine.limits_apply import daily_allowance
    return float(daily_allowance(eng.gate, eng.state)["daily_loss_limit"] or 0.0)


def untaken_refusal(eng: Engine, probe: CostProbe, close_ts: pd.Timestamp, headroom: float,
                    max_open: int | None = None) -> str | None:
    """Why a below-threshold (untaken) probe may not be sent now, or None (directors' ruling 2026-10-19): at most
    `untaken_cap` sent this risk day, at most half of the probe count open (`max_open`, the one `probe_limits`
    applies; `cost_probe_max_open` when not given), and only while more than half the probe budget is left. An
    unreadable ledger refuses (fail closed)."""
    since = eng._last_reset if eng._last_reset is not None else close_ts.floor("D")
    try:
        with probe.db.connection() as conn:
            every = LEDGER.select(conn, "account_id = ?", (eng.cfg.account_id,))
    except Exception as exc:
        log.warning("cost probe ledger unreadable (%s): no untaken probe", type(exc).__name__)
        return "ledger_unreadable"
    rows = [r for r in every if not r.taken]
    sent = {r.client_order_id for r in rows if r.sent_utc >= since} | {k for k, ts in probe.untaken_sent.items()
                                                                        if ts >= since}
    if len(sent) >= untaken_cap(eng.cfg.probe_only):
        return "daily_cap"
    # an open probe the ledger does not know as taken (untaken, or adopted with no row) holds an untaken slot
    taken_ids = {r.position_id for r in every if r.taken and r.position_id is not None}
    slots = eng.cfg.cost_probe_max_open if max_open is None else max_open
    if sum(1 for pid in _mirror_probes_open(eng) if pid not in taken_ids) >= slots // 2:
        return "slots_reserved"
    if headroom <= eng.cfg.cost_probe_budget * _limit(eng) / 2:
        return "budget_reserved"
    return None


def _send(eng: Engine, probe: CostProbe, version: str, t: ShadowTrade, agent: Specialist, close_ts: pd.Timestamp,
          tick: Tick, headroom: float, lim: ProbeLimits) -> None:
    from goldbot.risk import Intent
    tag, ls, side = probe_id(agent.agent_id), agent.label_spec, t.side
    pid = _probe_order_id(eng, tag, close_ts)
    if pid in eng.sent_ids:
        return
    atr_usd = abs(t.entry - t.stop) / ls.stop_atr              # the signal bar's ATR, as the shadow entry used it
    price = tick.ask if side > 0 else tick.bid

    # which limits applied and why, on every decision and the ledger row (dynamic limits)
    applied: dict[str, Any] = {"probe_max_loss": lim.max_loss, "probe_max_open": lim.max_open,
                               "basis": lim.basis("fixed:probe_loss_unknown")}

    def refuse(why: str) -> None:
        eng.decisions.append({"ts": close_ts.isoformat(), "agent": tag, "version": version,
                              "action": "cost_probe_refused:" + why, **applied})
        if why == NATIVE_SKIP:
            _record_skip(eng, probe, SkipRecord(
                client_order_id=pid, account_id=eng.cfg.account_id, agent_id=agent.agent_id, version=version,
                timeframe=t.timeframe, side=side, taken=t.taken, signal_utc=close_ts, atr_usd=round(atr_usd, 4),
                native_stop_loss=applied["native_stop_loss"], probe_max_loss=round(lim.max_loss, 2),
                basis=applied["basis"]))

    terms = eng._sizing_terms(side, price, ls.stop_atr * atr_usd, cycle=close_ts)
    lot = terms.get("volume_min")
    native = stop_loss(eng, side, price, ls.stop_atr * atr_usd, float(lot)) if lot is not None else None
    if native is None:
        return refuse("probe_loss_unknown")
    # dynamic limits: the probe's loss is its own stop's at the minimum lot while that is inside the ceiling
    max_loss, loss_basis = lim.max_loss, lim.loss_basis
    if loss_basis in ("config_cap", "budget_cap") and native <= max_loss:
        max_loss, loss_basis = native, "native_stop"
    # the decision's detail splits every probe reading by agent x timeframe and the regime's ATR (quant review)
    applied = {"probe_max_loss": round(max_loss, 2), "probe_max_open": lim.max_open, "basis": lim.basis(loss_basis),
               "timeframe": t.timeframe, "atr_usd": round(atr_usd, 4), "native_stop_loss": round(native, 2)}
    # architect trace 2026-10-25: "mirror probes must be evidence of the strategy, so they run at the strategy's OWN
    # stop and hold, or are skipped". A native stop losing more than the ceiling (cost_probe_max_loss or the dynamic
    # one) at the minimum lot is skipped, never sent with a tighter stop (the former clamp measured a different trade)
    if native > lim.max_loss + 1e-9:
        return refuse(NATIVE_SKIP)
    terms["volume_max"] = lot                                    # the minimum lot only
    intent = Intent(agent_id=tag, side=side, p=t.p, target_atr=ls.target_atr, stop_atr=ls.stop_atr, atr_usd=atr_usd,
                    cost_atr=eng._cost_atr(atr_usd, close_ts), multiplier=1.0, price=price, timeframe=t.timeframe,
                    family=agent.family, swap_usd_per_oz=eng._swap_usd_per_oz(side, close_ts),
                    learning_probe=True, account_mode=eng.cfg.mode,
                    phase_file=_phase_file(eng), **terms)
    # architect trace 2026-10-25: the probe holds the agent's own time barrier, its shadow trade's (the candidate's
    # max_bars when it carries one), on every account; no probe_only cap
    order_agent = _ProbeAgent(agent, t.max_bars)

    def record(res: OrderResult, gd: GateDecision, filled: bool, loss: float, sent: pd.Timestamp,
               latency_ms: float) -> ProbeRecord:
        return ProbeRecord(client_order_id=pid, account_id=eng.cfg.account_id, agent_id=agent.agent_id, version=version,
                           side=side, lots=float(res.filled_lots if filled else gd.lots), signal_utc=close_ts,
                           signal_price=t.entry, requested_price=price, spread_usd=round(tick.ask - tick.bid, 6),
                           sent_utc=sent, latency_ms=round(latency_ms, 3), retcode=res.retcode,
                           position_id=res.position_id if filled else None,
                           fill_price=(res.price if res.price is not None else price) if filled else None,
                           stop_loss=round(loss, 2), taken=t.taken, probe_max_loss=applied["probe_max_loss"],
                           probe_max_open=lim.max_open, basis=applied["basis"], native_stop_loss=round(native, 2),
                           fx_acct_per_usd=terms.get("acct_per_usd"))

    _place(eng, probe.db, intent, cast("Specialist", order_agent), pid=pid, close_ts=close_ts, entry=t.entry,
           target=price + side * ls.target_atr * atr_usd, max_loss=lim.max_loss, headroom=headroom,
           native_distance=ls.stop_atr * atr_usd,
           applied=lambda: applied, refuse=refuse, action="cost_probe:", journal={"agent": tag, "version": version},
           record=record, before_send=None if t.taken else lambda: probe.untaken_sent.__setitem__(pid, close_ts))


def _place(eng: Engine, db: Database, intent: Intent, agent: Specialist, *, pid: str, close_ts: pd.Timestamp,
           entry: float, target: float, max_loss: float, headroom: float, applied: Callable[[], dict[str, Any]],
           refuse: Callable[[str], None], action: str, journal: dict[str, Any],
           record: Callable[[OrderResult, GateDecision, bool, float, pd.Timestamp, float], Any],
           repo: RecordRepo[Any] | None = None, over_cap: Callable[[float], str] | None = None,
           max_stop: float | None = None, before_send: Callable[[], None] | None = None,
           native_distance: float | None = None) -> int | None:
    """Every probe's order core, mirror and scheduled, and an exploration arm's (`repo`: its ledger, the probe ledger
    by default): `RiskGate.check` (the only path to an order) on the minimum-lot
    intent, the loss at the stop the gate set within `max_loss` and the budget `headroom`, then `Engine._execute`
    (pending_orders row first), the journal and the ledger row. `before_send` runs once the order is going out (an
    untaken probe's count, even when the send raises). A mirror probe's loss above the cap at its strategy's own stop
    (`native_distance`, not widened by the gate) is the same skip as before the gate, `native_stop_above_cap` (safety
    review 2026-10-25); `probe_loss_above_cap` stays for a stop the gate floored wider; `over_cap` names the reason
    instead (an exploration arm's binding bound). The position id of a fill, else None."""
    from goldbot.risk.gate import broker_margin
    from goldbot.telegram.approvals import Proposal
    side, price = intent.side, intent.price
    gd = eng.gate.check(intent, eng.state, close_ts, margin_required=broker_margin(eng.broker, eng.cfg.symbol),
                        throttle=eng._agent_throttle(intent.agent_id, intent.timeframe, close_ts))
    if not gd.allowed or gd.lots > intent.volume_min + 1e-9:
        refuse(",".join(gd.reasons or ["above_minimum_lot"]))
        return None
    if max_stop is not None and gd.stop_distance > max_stop + 1e-9:     # the gate floored it wider than allowed
        refuse("stop_above_cap")
        return None
    loss = stop_loss(eng, side, price, gd.stop_distance, gd.lots)   # at the stop the gate set (stops level floored)
    if loss is None:
        refuse("probe_loss_unknown")
        return None
    if loss > max_loss + 1e-9:
        own = native_distance is not None and gd.stop_distance <= native_distance + 1e-9
        refuse(over_cap(loss) if over_cap is not None else NATIVE_SKIP if own else "probe_loss_above_cap")
        return None
    if loss > headroom + 1e-9:
        eng.decisions.append({"ts": close_ts.isoformat(), "agent": None, "action": "cost_probe_budget:probe_budget_full",
                              **applied()})
        return None
    stop = price - side * gd.stop_distance
    prop = Proposal(proposal_id=pid, account_id=eng.cfg.account_id, agent_id=intent.agent_id, side=side, lots=gd.lots,
                    entry=entry, stop=stop, target=target, p=intent.p, ev_r=0.0, spread_points=eng.state.spread_points,
                    top_features=[])
    sent = pd.Timestamp.now("UTC")
    if before_send is not None:
        before_send()
    t0 = time.perf_counter()
    try:
        res = eng._execute(prop, agent, gd.lots, stop, target, requested=price, atr_usd=intent.atr_usd, gd=gd)
    except Exception as exc:        # as an auto entry (review LOW-C): the row stays "sending", reconciliation resolves it
        eng._auto_paused = (close_ts, "order_raised")
        eng.note_tick_error(exc)
        eng.decisions.append({"ts": close_ts.isoformat(), **journal, "proposal": pid,
                              "action": f"{action}auto_unconfirmed:{type(exc).__name__}", **applied()})
        return None
    latency_ms = (time.perf_counter() - t0) * 1000.0
    eng.decisions.append({"ts": close_ts.isoformat(), **journal, "proposal": pid,
                          "action": action + auto_entry.action(res), **applied()})
    if res is None:
        return None
    filled = res.ok and res.position_id is not None
    try:
        with db.write() as conn:
            (repo or LEDGER).upsert(conn, record(res, gd, filled, loss, sent, latency_ms))
    except Exception as exc:        # the order stands; its close is recorded from the trade (`record_close`)
        log.warning("cost probe %s not written to the ledger (%s)", pid, type(exc).__name__)
    if filled:
        _reread(eng, close_ts)
    return res.position_id if filled else None


def _record_skip(eng: Engine, probe: CostProbe, rec: SkipRecord) -> None:
    """A skipped attempt to `cost_probe_skips`; a failed write is logged, never raised (the skip stands)."""
    try:
        with probe.db.write() as conn:
            SKIPS.upsert(conn, rec)
    except Exception as exc:
        log.warning("cost probe skip %s not written (%s)", rec.client_order_id, type(exc).__name__)


# ------------------------------------------------------------------------------------------- scheduled probes (M1)
def scheduled_side(slot: pd.Timestamp) -> int:
    """The prereg's side rule: +1 on an even slot index (15m slots since the epoch), -1 on an odd one. The clock
    alone chooses it, never a fill or an outcome."""
    return 1 if (int(slot.timestamp()) // SLOT_S) % 2 == 0 else -1


def schedule_skip(eng: Engine, ts: pd.Timestamp) -> str | None:
    """Why no scheduled probe is sent at `ts`, or None: a weekend day (UTC or server), the rollover blackout (the
    configured window in its own zone, New York by default, so it moves with US DST; the daily break and the reopen
    inside it) or past the Friday weekend cut (server time, `weekend_cut`)."""
    sch: ScheduledProbe = eng.cfg.cost_probe_scheduled
    srv = server_wall(pd.DatetimeIndex([ts]), eng.cfg.server_tz)[0]
    if ts.weekday() >= 5 or srv.weekday() >= 5:
        return "weekend"
    hm, (a, b) = ts.tz_convert(sch.blackout_tz).strftime("%H:%M"), sch.blackout
    if (a <= hm < b) if a <= b else (hm >= a or hm < b):
        return "blackout"
    h, m = (int(x) for x in eng.cfg.weekend_cut.split(":"))
    if srv.weekday() == 4 and srv.hour * 60 + srv.minute >= h * 60 + m:
        return "friday_cut"
    return None


def _scheduled_headroom(eng: Engine, probe: CostProbe, close_ts: pd.Timestamp) -> float:
    """Prereg K10: what scheduled probes may still lose today, within `budget_share` of the day's probe budget (their
    realised loss on this risk day counted, an unknown P&L at its worst case: `realised_loss`; an unknown limit
    leaves nothing: fail closed)."""
    since = eng._last_reset if eng._last_reset is not None else close_ts.floor("D")
    share = eng.cfg.cost_probe_scheduled.budget_share * eng.cfg.cost_probe_budget * _limit(eng)
    return share - realised_loss(eng, probe, since, SCHEDULED)


def scheduled_step(eng: Engine, probe: CostProbe, close_ts: pd.Timestamp, tick: Tick) -> None:
    """At a 15m close, after the mirror probes: one scheduled market probe (the module docstring), when enabled.
    Skipped (journalled `cost_probe_scheduled_skip:<why>`) in the schedule's blackouts, while anything else is open,
    pending or in flight, outside a demo terminal in auto mode, or past the probe budget or the scheduled share of it;
    silently while one is still open (one at a time)."""
    sch: ScheduledProbe = eng.cfg.cost_probe_scheduled
    if not sch.enabled:
        return

    def skip(why: str) -> None:
        eng.decisions.append({"ts": close_ts.isoformat(), "agent": SCHEDULED_ID, "action": "cost_probe_scheduled_skip:" + why})

    if (why := schedule_skip(eng, close_ts)) is not None:
        return skip(why)
    if any(t.agent_id == SCHEDULED_ID for t in eng.open.values()):
        return
    # exploration trades are learning too (probe_only account, live 2026-10-26: they stopped every scheduled probe);
    # a strategy position, a pending proposal or any order in flight still makes the probe wait
    learning_open = [pid for pid, t in eng.open.items() if is_learning(t.agent_id)]
    if any(not is_learning(t.agent_id) for t in eng.open.values()) or eng.pending or _in_flight(eng, False) \
            or _in_flight(eng, True) or eng.state.open_positions > len(learning_open):
        return skip("busy")
    if eng._auto_paused is not None and eng._auto_paused[0] == close_ts:
        return skip("paused")
    if eng.cfg.mode != "demo" or not auto_entry.permitted(eng, close_ts):
        return skip("not_auto")
    why, headroom = budget_refusal(eng, probe, close_ts)
    if why is not None:
        return skip("budget:" + why)
    own = _scheduled_headroom(eng, probe, close_ts)
    if own <= 1e-9:
        return skip("scheduled_budget")
    _send_scheduled(eng, probe, close_ts, tick, min(headroom, own))


def _send_scheduled(eng: Engine, probe: CostProbe, close_ts: pd.Timestamp, tick: Tick, headroom: float) -> None:
    """The M1 order: 0.01 lot, the slot's side, a $3 server stop (and a symmetric $3 safety target), through `_place`.
    The Intent is what the probe is: no signal (p 0.5), a time exit (`has_target` False) of hold_s; no swap (the
    blackout keeps the 30 s hold off the rollover)."""
    from goldbot.risk import Intent
    sch: ScheduledProbe = eng.cfg.cost_probe_scheduled
    pid = _scheduled_order_id(eng, close_ts)
    if pid in eng.sent_ids:
        return
    side = scheduled_side(close_ts)
    price = tick.ask if side > 0 else tick.bid
    lim = probe_limits(eng, probe)
    applied: dict[str, Any] = {"probe_max_loss": round(lim.max_loss, 2), "basis": SCHEDULED_BASIS}

    def refuse(why: str) -> None:
        eng.decisions.append({"ts": close_ts.isoformat(), "agent": SCHEDULED_ID,
                              "action": "cost_probe_scheduled_refused:" + why, **applied})

    terms = eng._sizing_terms(side, price, sch.stop_usd, cycle=close_ts)
    if terms.get("volume_min") is None:
        return refuse("probe_loss_unknown")
    terms["volume_max"] = terms["volume_min"]                    # the minimum lot only
    intent = Intent(agent_id=SCHEDULED_ID, side=side, p=0.5, target_atr=1.0, stop_atr=1.0, atr_usd=sch.stop_usd,
                    cost_atr=eng._cost_atr(sch.stop_usd, close_ts), multiplier=1.0, price=price,
                    timeframe=eng.cfg.decision_tf, family=SCHEDULED_FAMILY, has_target=False, learning_probe=True,
                    account_mode=eng.cfg.mode, phase_file=_phase_file(eng), purpose="cost_measurement",
                    terminal_trade_mode=_terminal_trade_mode(eng), hold_s=sch.hold_s, probe_headroom=headroom,
                    probe_max_loss=lim.max_loss, **terms)

    def record(res: OrderResult, gd: GateDecision, filled: bool, loss: float, sent: pd.Timestamp,
               latency_ms: float) -> ProbeRecord:
        return ProbeRecord(client_order_id=pid, account_id=eng.cfg.account_id, purpose="cost_measurement",
                           agent_id=SCHEDULED, side=side, lots=float(res.filled_lots if filled else gd.lots),
                           signal_utc=close_ts, requested_price=price, spread_usd=round(tick.ask - tick.bid, 6),
                           sent_utc=sent, latency_ms=round(latency_ms, 3), retcode=res.retcode,
                           position_id=res.position_id if filled else None,
                           fill_price=(res.price if res.price is not None else price) if filled else None,
                           stop_loss=round(loss, 2), probe_max_loss=applied["probe_max_loss"], basis=SCHEDULED_BASIS,
                           fx_acct_per_usd=terms.get("acct_per_usd"))

    pos = _place(eng, probe.db, intent, cast("Specialist", _ScheduledAgent(eng.cfg.decision_tf)), pid=pid,
                 close_ts=close_ts, entry=price, target=price + side * sch.stop_usd, max_loss=lim.max_loss,
                 headroom=headroom, applied=lambda: applied, refuse=refuse, action="cost_probe_scheduled:",
                 journal={"agent": SCHEDULED_ID}, record=record, max_stop=sch.stop_usd)
    if pos is not None:
        probe.held[pos] = _hold_clock(eng, tick.ts_utc)


def _terminal_trade_mode(eng: Engine) -> str | None:
    """What the terminal itself reports (MT5 `account_info().trade_mode`), read fresh for the gate's cost-measurement
    fence (ADR 0037 Amendment 5); an unreadable account gives None, which the gate refuses."""
    try:
        return getattr(eng.broker.account(), "trade_mode", None)
    except Exception:
        return None


def _hold_clock(eng: Engine, now: pd.Timestamp) -> pd.Timestamp:
    """The hold's clock: the host clock on a live engine, else the time the caller passes (replays and tests)."""
    return eng.clock() if eng.cfg.live_clock else now


def hold_exits(eng: Engine, now: pd.Timestamp) -> None:
    """Close every scheduled probe held `hold_s` (from its fill on the hold clock; after a restart, from the broker's
    open time), at market, `HOLD_EXIT`. Runs on every pass that retries closes (`Engine._retry_closes`, with a quote
    or without one) whatever the entry state: not gated by halts, the mode, the budget or probes being switched off.
    A refused close stays due and is retried like any close. Never raises (it runs outside any guard)."""
    try:
        at = _hold_clock(eng, now)
        hold = eng.cfg.cost_probe_scheduled.hold_s
        probe = eng.probe
        for pid, tr in list(eng.open.items()):
            if tr.agent_id != SCHEDULED_ID or pid in eng._close_due:
                continue
            since = (probe.held.get(pid) if probe is not None else None) or tr.opened_utc or tr.entry_bar_ts
            if (at - since).total_seconds() < hold:
                continue
            q = eng._last_tick
            if probe is not None and q is not None:
                probe.exit_quotes[pid] = (q.bid, q.ask, q.ts_utc)     # prereg K11: the quote the close is sent on
            eng._try_close(pid, HOLD_EXIT, now)
    except Exception:
        log.exception("scheduled probe hold exits failed; retried next pass")


def _scheduled_order_id(eng: Engine, close_ts: pd.Timestamp) -> str:
    """`cm-sched-<slot epoch s>-<8 hex>`: 28 characters, the whole id fits MT5's 29-character comment
    (MT5_COMMENT_MAX; a 30-character id made order_check return None, incident 2026-10-20)."""
    import hashlib
    h = hashlib.sha1(f"{eng.cfg.account_id}|{SCHEDULED_ID}|{close_ts.isoformat()}".encode()).hexdigest()[:8]
    return f"{SCHEDULED_COMMENT}{int(close_ts.timestamp())}-{h}"


def _probe_order_id(eng: Engine, tag: str, close_ts: pd.Timestamp) -> str:
    """`cp-<close epoch s>-<hash>`: 24 characters, so the whole id (with its `cp-`) fits MT5's 29-character comment (MT5_COMMENT_MAX)."""
    import hashlib
    h = hashlib.sha1(f"{eng.cfg.account_id}|{tag}|{close_ts.isoformat()}".encode()).hexdigest()[:10]
    return f"{COMMENT}{int(close_ts.timestamp())}-{h}"


def record_close(eng: Engine, pid: int, tr: OpenTrade, reason: str, px: float | None, at: pd.Timestamp) -> bool:
    """A probe's close goes to the probe ledger, never to closed_trades.jsonl; False for any other trade. Exit, swap,
    commission and P&L from the broker's deals (the engine's own close price when they are unreadable). Raises when the
    ledger cannot be written, so the close stays queued and is retried (`Engine._flush_closed`)."""
    if exploration.is_exploration(tr.agent_id):
        return exploration.record_close(eng, pid, tr, reason, px, at)       # its own ledger (plan 2026-10-25)
    if not is_probe(tr.agent_id):
        return False
    try:
        info, _ = eng._exit_deals(pid, tr)
    except Exception:
        log.exception("deal history unreadable for cost probe position %s; recorded from the engine's fill", pid)
        info = None
    known = info or {}
    cid = tr.client_order_id or f"position-{pid}"
    db = eng.probe.db if eng.probe is not None else Database.open(eng.cfg.state_dir, "aux")
    quote = eng.probe.exit_quotes.pop(pid, None) if eng.probe is not None else None
    exit_px = known.get("exit_price", px)
    at_px = exit_px if exit_px is not None else tr.open_price
    try:                            # prereg K9: the conversion at the close, read after the exit (never before it)
        fx = eng._usd_to_account(tr.side, float(at_px), 1.0) if at_px is not None else None
    except Exception:
        fx = None
    scheduled = tr.agent_id == SCHEDULED_ID
    with db.write() as conn:
        rec = LEDGER.get(conn, cid) or ProbeRecord(
            client_order_id=cid, account_id=eng.cfg.account_id, agent_id=tr.agent_id.removeprefix(PREFIX),
            side=tr.side, lots=float(tr.initial_lots or tr.lots), sent_utc=tr.opened_utc or tr.entry_bar_ts,
            fill_price=tr.open_price, purpose="cost_measurement" if scheduled else "cost_probe",
            basis=SCHEDULED_BASIS if scheduled else None)
        LEDGER.upsert(conn, rec.model_copy(update={
            "position_id": pid, "exit_utc": known.get("exit_utc", at),
            "exit_price": exit_px, "exit_reason": known.get("reason") or reason,
            "swap": known.get("swap"), "commission": known.get("commission"), "pnl": known.get("pnl"),
            "exit_fx_acct_per_usd": fx,
            **({"exit_bid": quote[0], "exit_ask": quote[1], "exit_quote_utc": quote[2]} if quote else {})}))
    eng._recorded.add(pid)
    return True


# ---------------------------------------------------------------------------------------------- readers (costs-v2)
def probes(state_dir: str | Path, account_id: str | None = None) -> list[ProbeRecord]:
    """Every probe record, oldest first (one account's when given): what costs-v2 reads. Empty when aux.db has none."""
    f = Path(state_dir) / "aux.db"
    if not f.exists():
        return []
    db = Database.open(state_dir, "aux")
    with db.connection() as conn:
        if account_id is None:
            return LEDGER.select(conn, order_by="ts_ns")
        return LEDGER.select(conn, "account_id = ?", (account_id,), order_by="ts_ns")


def skips(state_dir: str | Path, account_id: str | None = None) -> list[SkipRecord]:
    """Every skipped mirror probe, oldest first (one account's when given). Empty when aux.db has none."""
    if not (Path(state_dir) / "aux.db").exists():
        return []
    db = Database.open(state_dir, "aux")
    with db.connection() as conn:
        if account_id is None:
            return SKIPS.select(conn, order_by="ts_ns")
        return SKIPS.select(conn, "account_id = ?", (account_id,), order_by="ts_ns")


def skip_counts(state_dir: str | Path, account_id: str | None = None) -> dict[tuple[str, str], dict[str, float]]:
    """Skipped mirror probes per (agent id, timeframe): the count and the median ATR and native stop loss. Split, never
    pooled (quant review 2026-10-25): skips fall on wide stops, high volatility and long timeframes, so the probes
    that were sent are a tight-stop sample of each strategy; any reading of them is per agent x timeframe, beside
    these counts."""
    groups: dict[tuple[str, str], list[SkipRecord]] = {}
    for r in skips(state_dir, account_id):
        groups.setdefault((r.agent_id, r.timeframe), []).append(r)
    return {k: {"skips": len(v), "median_atr_usd": statistics.median(r.atr_usd for r in v),
                "median_native_stop_loss": statistics.median(r.native_stop_loss for r in v)}
            for k, v in groups.items()}


def closed_position_ids(db: Database, account_id: str) -> set[int]:
    """The position ids of an account's closed probes and exploration trades: the broker ledger reconciles them here
    (aux.db cost_probes and exploration_trades), not in closed_trades."""
    out: set[int] = set()
    for table in ("cost_probes", "exploration_trades"):
        try:
            with db.connection() as conn:
                rows = conn.execute(f"SELECT position_id FROM {table} WHERE account_id = ? AND closed = 1 "
                                    "AND position_id IS NOT NULL", (account_id,)).fetchall()
        except sqlite3.Error as exc:
            log.warning("%s unreadable (%s): its closes reconciled as trades", table, type(exc).__name__)
            continue
        out |= {int(r[0]) for r in rows}
    return out
