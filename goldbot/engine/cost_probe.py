"""Demo cost probe: minimum-lot demo orders that mirror challengers' shadow entries, so costs are measured on the
broker rather than assumed (ADR 0037, amending ADR 0036; the research director's ruling 2026-10-19).

The ruling: "mirror challengers' SHADOW entry signals as minimum-lot (broker min, 0.01) demo orders, choosing which
signal by a fixed round-robin rotation over agent_ids (never by P&L/score). At most one probe position open at a time;
real (live-agent) trades take priority". A cost probe, not a population status: probes feed only the costs pipeline.

* Which signal: a taken shadow entry opened at this bar close by a challenger (a shadow version that is no agent's
  champion, `Engine.live_versions`) of an agent the broker may trade (not shadow only; TradingView agents never, and
  a frozen roster rule agent on a RuleModel by the ruling of 2026-10-19, `_mirrorable`). One signal per agent (its versions in name order); among the agents the next agent id
  after the last one probed, wrapping (`rotate`). p, score and P&L never reach the choice.
* When: on the shadow host with `cost_probe` on, on a flat account: no position at the broker, no engine trade, no
  pending proposal and no order in flight, so at most one probe position at a time. A live agent's candidate closes an
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
* The record: one `ProbeRecord` per order in aux.db `cost_probes` (purpose cost_probe, with the agent id and version):
  signal, requested and fill price, spread, send latency and retcode; at the close the exit, swap, commission and P&L
  from the broker's deals. A probe's close never goes to closed_trades.jsonl (phase gate, throttle, broker-ledger
  trades, attribution) and its fill never to the store's `fills` (the nightly slippage table, attribution's live
  fills): costs-v2 reads `probes`, and refitting costs on them is a later preregistered step.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.timeutil import epoch_ns
from goldbot.db import Database, RecordRepo
from goldbot.engine import auto_entry

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.engine.shadow import ShadowTrade
    from goldbot.engine.store import OpenTrade
    from goldbot.execution.broker import Tick
    from goldbot.specialists.base import Specialist

log = logging.getLogger(__name__)

PREFIX = "cost_probe:"
YIELD = "cost_probe_yield"          # the exit of a probe that makes way for a real trade
PROBE_MAGIC_OFFSET = 97             # magic_base + 97: no family's crc32 % 100 (tests guard it), not the smoke's 99
COMMENT = "cp-"                     # a probe's client order id, and so its MT5 comment, starts with this


def probe_id(agent_id: str) -> str:
    """The tag a probe trades under (its open trade, order row and journal): never the agent's own id, so the agent's
    one-position rule, its throttle and its closed-trade record never see the probe."""
    return PREFIX + agent_id


def is_probe(agent_id: str | None) -> bool:
    return bool(agent_id) and str(agent_id).startswith(PREFIX)


def magic(eng: Engine, agent: Specialist) -> int:
    """The order's magic: a probe's own, else the agent's family magic (`Engine._magic`)."""
    return eng.cfg.magic_base + PROBE_MAGIC_OFFSET if is_probe(agent.agent_id) else eng._magic(agent.family)


def is_probe_magic(m: int, magic_base: int) -> bool:
    return int(m) == magic_base + PROBE_MAGIC_OFFSET


def agent_of(eng: Engine, agent_id: str | None) -> Specialist | None:
    """The specialist of an agent id, a probe's tag included (its challenger: barriers and exit policy)."""
    return eng.agents.get(str(agent_id).removeprefix(PREFIX)) if agent_id else None


def adoption(eng: Engine, p: Any, rec: Any) -> tuple[Specialist | None, str]:
    """(specialist, agent id) for a position `Engine._adopt` takes over. With its order row: the row's agent (a probe's
    tag kept, its challenger's specialist). Without one, an orphan: a probe's magic gives `cost_probe:orphan` (recorded
    in the probe ledger, never in the trade record), else the agent of the magic number when exactly one agent here
    has that family's magic."""
    if rec is not None:
        return agent_of(eng, rec.agent_id), rec.agent_id
    if is_probe_magic(p.magic, eng.cfg.magic_base):
        return None, PREFIX + "orphan"
    hits = [a for a in eng.agents.values() if eng._magic(a.family) == p.magic]
    agent = hits[0] if len(hits) == 1 else None
    return agent, "orphan" if agent is None else agent.agent_id


def probe_positions(deals: pd.DataFrame, magic_base: int) -> set[int]:
    """Position ids with a probe's magic in a deal history (MT5 columns position_id, magic)."""
    if deals is None or deals.empty or "magic" not in deals.columns:
        return set()
    return {int(pid) for pid, m in zip(deals["position_id"], deals["magic"]) if pid and is_probe_magic(m, magic_base)}


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
    purpose: Literal["cost_probe"] = "cost_probe"
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


class _ProbeAgent:
    """The challenger's specialist under the probe's tag: its family (magic), timeframe, barriers and exit policy."""

    def __init__(self, agent: Specialist):
        self._agent = agent

    @property
    def agent_id(self) -> str:
        return probe_id(self._agent.agent_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._agent, name)


class CostProbe:
    """The engine's probe state: the ledger, the shadow entries already seen and the rotation's last agent."""

    def __init__(self, eng: Engine):
        assert eng.shadow is not None
        self.db = Database.open(eng.cfg.state_dir, "aux")
        self.seen = _open_keys(eng)             # entries open at the start are old: never probed after a restart
        with self.db.connection() as conn:
            r = conn.execute("SELECT agent_id FROM cost_probes WHERE account_id = ? ORDER BY ts_ns DESC LIMIT 1",
                             (eng.cfg.account_id,)).fetchone()
        self.last_agent: str | None = str(r[0]) if r else None

    @classmethod
    def start(cls, eng: Engine) -> CostProbe | None:
        check_start(eng.cfg.mode, eng.cfg.cost_probe)
        if not eng.cfg.cost_probe:
            return None
        if eng.shadow is None:
            log.warning("cost probe on, but this engine is not the shadow host: no shadow entries to mirror")
            return None
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
    """agent id -> (version, shadow entry, specialist): this close's new taken challenger entries, one per agent."""
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
            if (version, t.agent_id, t.entry_ts) in new and t.taken and agent is not None and _mirrorable(eng, version, agent):
                out.setdefault(t.agent_id, (version, t, agent))
    return out


def _probes_open(eng: Engine) -> list[int]:
    return [pid for pid, t in eng.open.items() if is_probe(t.agent_id)]


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
        _step(eng, probe, close_ts, tick)
    except Exception as exc:
        log.exception("cost probe step failed at %s; no probe this bar close", close_ts)
        eng.decisions.append({"ts": close_ts.isoformat(), "agent": None, "action": "cost_probe_error",
                              "error": type(exc).__name__})


def _step(eng: Engine, probe: CostProbe, close_ts: pd.Timestamp, tick: Tick) -> None:
    signals = _signals(eng, probe)
    others = any(not is_probe(t.agent_id) for t in eng.open.values()) or bool(eng.pending) or _in_flight(eng, False)
    if _probes_open(eng):
        if others or eng.state.open_positions > len(_probes_open(eng)):
            _make_way(eng, close_ts)
        return
    if others or eng.state.open_positions > 0 or _in_flight(eng, True) or not signals:
        return
    if eng._auto_paused is not None and eng._auto_paused[0] == close_ts:
        return
    if eng.cfg.mode != "demo" or not auto_entry.permitted(eng, close_ts):
        return
    if (why := budget_refusal(eng, probe, close_ts)) is not None:
        eng.decisions.append({"ts": close_ts.isoformat(), "agent": None, "action": f"cost_probe_budget:{why}"})
        return
    version, t, agent = signals[rotate(list(signals), probe.last_agent)]
    probe.last_agent = agent.agent_id
    _send(eng, probe, version, t, agent, close_ts, tick)


def budget_refusal(eng: Engine, probe: CostProbe, close_ts: pd.Timestamp) -> str | None:
    """Why the probe budget refuses a new probe now, or None: the day's loss allowance unknown, less than
    `cost_probe_budget` of it remaining, today's probe P&L unknown, or today's realised probe loss at that share or more.
    Today is the engine's risk day (from its last reset; the UTC day before the first)."""
    from goldbot.engine.limits_apply import daily_allowance
    allowance = daily_allowance(eng.gate, eng.state)
    limit, remaining = allowance["daily_loss_limit"], allowance["daily_loss_remaining"]
    if limit is None or remaining is None or limit <= 0:
        return "allowance_unknown"
    share = eng.cfg.cost_probe_budget * limit
    if remaining < share:
        return "allowance_low"
    since = eng._last_reset if eng._last_reset is not None else close_ts.floor("D")
    with probe.db.connection() as conn:
        today = [r for r in LEDGER.select(conn, "account_id = ? AND closed = 1", (eng.cfg.account_id,))
                 if r.exit_utc is not None and r.exit_utc >= since]
    if any(r.pnl is None for r in today):
        return "probe_pnl_unknown"
    if -sum(float(r.pnl or 0.0) for r in today) >= share:
        return "probe_loss_reached"
    return None


def _send(eng: Engine, probe: CostProbe, version: str, t: ShadowTrade, agent: Specialist, close_ts: pd.Timestamp,
          tick: Tick) -> None:
    from goldbot.risk import Intent
    from goldbot.risk.gate import broker_margin
    from goldbot.telegram.approvals import Proposal
    tag, ls, side = probe_id(agent.agent_id), agent.label_spec, t.side
    pid = _probe_order_id(eng, tag, close_ts)
    if pid in eng.sent_ids:
        return
    atr_usd = abs(t.entry - t.stop) / ls.stop_atr              # the signal bar's ATR, as the shadow entry used it
    price = tick.ask if side > 0 else tick.bid
    terms = eng._sizing_terms(side, price, ls.stop_atr * atr_usd, cycle=close_ts)
    terms["volume_max"] = terms.get("volume_min", Intent.model_fields["volume_min"].default)   # the minimum lot only
    intent = Intent(agent_id=tag, side=side, p=t.p, target_atr=ls.target_atr, stop_atr=ls.stop_atr, atr_usd=atr_usd,
                    cost_atr=eng._cost_atr(atr_usd, close_ts), multiplier=1.0, price=price, timeframe=t.timeframe,
                    family=agent.family, swap_usd_per_oz=eng._swap_usd_per_oz(side, close_ts), **terms)
    gd = eng.gate.check(intent, eng.state, close_ts, margin_required=broker_margin(eng.broker, eng.cfg.symbol),
                        throttle=eng._agent_throttle(tag, t.timeframe, close_ts))
    if not gd.allowed or gd.lots > intent.volume_min + 1e-9:
        eng.decisions.append({"ts": close_ts.isoformat(), "agent": tag, "version": version,
                              "action": "cost_probe_refused:" + ",".join(gd.reasons or ["above_minimum_lot"])})
        return
    stop, target = price - side * gd.stop_distance, price + side * ls.target_atr * atr_usd
    prop = Proposal(proposal_id=pid, account_id=eng.cfg.account_id, agent_id=tag, side=side, lots=gd.lots,
                    entry=t.entry, stop=stop, target=target, p=t.p, ev_r=0.0, spread_points=eng.state.spread_points,
                    top_features=[])
    sent = pd.Timestamp.now("UTC")
    t0 = time.perf_counter()
    try:
        res = eng._execute(prop, cast("Specialist", _ProbeAgent(agent)), gd.lots, stop, target, requested=price,
                           atr_usd=atr_usd, gd=gd)
    except Exception as exc:        # as an auto entry (review LOW-C): the row stays "sending", reconciliation resolves it
        eng._auto_paused = (close_ts, "order_raised")
        eng.note_tick_error(exc)
        eng.decisions.append({"ts": close_ts.isoformat(), "agent": tag, "version": version, "proposal": pid,
                              "action": f"cost_probe:auto_unconfirmed:{type(exc).__name__}"})
        return
    latency_ms = (time.perf_counter() - t0) * 1000.0
    eng.decisions.append({"ts": close_ts.isoformat(), "agent": tag, "version": version, "proposal": pid,
                          "action": "cost_probe:" + auto_entry.action(res)})
    if res is None:
        return
    filled = res.ok and res.position_id is not None
    rec = ProbeRecord(client_order_id=pid, account_id=eng.cfg.account_id, agent_id=agent.agent_id, version=version,
                      side=side, lots=float(res.filled_lots if filled else gd.lots), signal_utc=close_ts,
                      signal_price=t.entry, requested_price=price, spread_usd=round(tick.ask - tick.bid, 6),
                      sent_utc=sent, latency_ms=round(latency_ms, 3), retcode=res.retcode,
                      position_id=res.position_id if filled else None,
                      fill_price=(res.price if res.price is not None else price) if filled else None)
    try:
        with probe.db.write() as conn:
            LEDGER.upsert(conn, rec)
    except Exception as exc:        # the order stands; its close is recorded from the trade (`record_close`)
        log.warning("cost probe %s not written to the ledger (%s)", pid, type(exc).__name__)
    if filled:
        _reread(eng, close_ts)


def _probe_order_id(eng: Engine, tag: str, close_ts: pd.Timestamp) -> str:
    """`cp-<close epoch s>-<hash>`: 24 characters, so the whole id (with its `cp-`) fits MT5's 31-character comment."""
    import hashlib
    h = hashlib.sha1(f"{eng.cfg.account_id}|{tag}|{close_ts.isoformat()}".encode()).hexdigest()[:10]
    return f"{COMMENT}{int(close_ts.timestamp())}-{h}"


def record_close(eng: Engine, pid: int, tr: OpenTrade, reason: str, px: float | None, at: pd.Timestamp) -> bool:
    """A probe's close goes to the probe ledger, never to closed_trades.jsonl; False for any other trade. Exit, swap,
    commission and P&L from the broker's deals (the engine's own close price when they are unreadable). Raises when the
    ledger cannot be written, so the close stays queued and is retried (`Engine._flush_closed`)."""
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
    with db.write() as conn:
        rec = LEDGER.get(conn, cid) or ProbeRecord(
            client_order_id=cid, account_id=eng.cfg.account_id, agent_id=tr.agent_id.removeprefix(PREFIX),
            side=tr.side, lots=float(tr.initial_lots or tr.lots), sent_utc=tr.opened_utc or tr.entry_bar_ts,
            fill_price=tr.open_price)
        LEDGER.upsert(conn, rec.model_copy(update={
            "position_id": pid, "exit_utc": known.get("exit_utc", at),
            "exit_price": known.get("exit_price", px), "exit_reason": known.get("reason") or reason,
            "swap": known.get("swap"), "commission": known.get("commission"), "pnl": known.get("pnl")}))
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


def closed_position_ids(db: Database, account_id: str) -> set[int]:
    """The position ids of an account's closed probes: the broker ledger reconciles them here, not in closed_trades."""
    try:
        with db.connection() as conn:
            rows = conn.execute("SELECT position_id FROM cost_probes WHERE account_id = ? AND closed = 1 "
                                "AND position_id IS NOT NULL", (account_id,)).fetchall()
    except sqlite3.Error as exc:
        log.warning("cost probe ledger unreadable (%s): probe closes reconciled as trades", type(exc).__name__)
        return set()
    return {int(r[0]) for r in rows}
