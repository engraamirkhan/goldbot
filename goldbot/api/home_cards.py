"""The Home status card stack (docs/ux/home-redesign-2026-10-19.md, "Decision 2026-10-19"): the three read-only views
the redesign needs that no endpoint gave yet. Nothing here writes or decides; each view is its own route so one
failing source never blanks Home ("each card its own query").

* N1 `open_positions` (`GET /api/positions/open`): every open position the engines hold (state/orders_<account>.json,
  engine/store.py `OpenTrade`), cost probes included and tagged, with entry, stop, target, the floating P&L in the
  account currency and R now. The floating P&L is at the engine's published mid price, before spread, commission and
  swap (`side x (mid - entry) x lots x contract x usd_to_account`); it is None when the engine publishes no price,
  contract size or conversion ("never a guessed exchange rate", account_view.usd_to_account). R is measured against
  the stop at entry (`initial_sl`, else the current stop).
* N2 `strategy_watch` (`GET /api/strategies/watch`): every strategy agent that is not retired (state/agents.json) and
  every agent the shadow book traded in the last 7 days, with a state in words: in a trade (a taken shadow trade is
  open), signal (a candidate fired on the agent's last bar), idle (the market is closed), watching (the last signal is
  older) or unknown (no record, or the book cannot be read). The engine publishes no box edges or MFI value yet, so
  `box_high` / `box_low` / `mfi` stay None: "be honest when a state is unknown".
* `today_money` (`GET /api/today`): per account, today's P&L after costs split into closed (the net records closed this
  opportunity day: positions and cost probes) and open (the broker's equity - balance), today's costs (commission and
  swap from those records; spread in money where the record keeps it, flagged incomplete otherwise), and the loss meter
  naming the limit that binds first: the probe stop (ADR 0037 amendment 3: no new probe once the day's loss leaves less
  than the real-trade reserve plus one probe's loss, `limit x (1 - REAL_TRADE_RESERVE) - max_loss`, ~£16 of £50, and
  never past `budget_share x limit`; a probe_only account keeps no reserve, ADR 0037 amendment 4) or the account's
  daily loss limit. Money older than `STALE_S` is marked stale,
  never shown as current.

Design rules kept: "accounts appear by config name. Any run of 5 or more digits is cut to its last two"
(`entry_text.account_name`); demo and live are separate rows, never summed; a source that cannot be read is named in
`unreadable` and the others still show.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from goldbot.api.activity import engines_by_account
from goldbot.api.schema import (
    DayCosts,
    LossLimit,
    LossMeter,
    OpenPosition,
    OpenPositionsView,
    StrategyWatch,
    StrategyWatchView,
    TodayAccount,
    TodayView,
)
from goldbot.base import FrozenRecord, load_record
from goldbot.config import TF_SECONDS, tf_seconds
from goldbot.engine import cost_probe
from goldbot.engine.shadow import ShadowBook, ShadowTrade
from goldbot.engine.store import OpenTrade
from goldbot.ops import gates_phase as gp
from goldbot.ops.account_view import usd_to_account
from goldbot.ops.report_days import day_bounds, report_day
from goldbot.research.attribution import family_of
from goldbot.telegram.entry_text import account_name

log = logging.getLogger(__name__)

__all__ = ["HOME_TTL_S", "STALE_S", "WATCH_DAYS", "ProbeTerms", "open_positions", "probe_terms_from_config",
           "strategy_watch", "today_money"]

HOME_TTL_S = 5.0                    # one build serves every poll for 5 s (the Home cards poll every 5-15 s)
STALE_S = 60.0                      # the decision: money or status older than 60 s is stale, never "allowed"
WATCH_DAYS = 7                      # a shadow agent seen in this many days is watched even when agents.json omits it


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _age(engine: dict[str, Any], now: pd.Timestamp) -> float | None:
    ts = _num(engine.get("ts"))
    return None if ts is None else round(max(0.0, now.timestamp() - ts), 1)


def _lot(engine: dict[str, Any]) -> dict[str, Any]:
    v = engine.get("lot_terms")
    return v if isinstance(v, dict) else {}


def _side(s: int) -> Literal["long", "short"]:
    return "long" if s > 0 else "short"


def _mode(v: Any) -> Literal["demo", "live", "paper"] | None:
    return v if v in ("demo", "live", "paper") else None


# ------------------------------------------------------------------------------------------------ N1 open positions
def _position(account: str, t: OpenTrade, e: dict[str, Any], now: pd.Timestamp) -> OpenPosition:
    entry = t.open_price if t.open_price is not None else t.entry
    price = _num(e.get("price"))
    lot = _lot(e)
    contract, fx = _num(lot.get("contract_oz")), usd_to_account(e)
    move = None if price is None or entry is None else t.side * (price - entry)
    pnl = None if move is None or contract is None or fx is None else round(move * t.lots * contract * fx, 2)
    risk_stop = t.initial_sl if t.initial_sl is not None else t.sl
    risk = None if entry is None or risk_stop is None else abs(entry - risk_stop)
    r_now = None if move is None or not risk else round(move / risk, 2)
    at_risk = None if entry is None or t.sl is None else max(0.0, t.side * (entry - t.sl))
    open_risk = None if at_risk is None or contract is None or fx is None else round(at_risk * t.lots * contract * fx, 2)
    age = _age(e, now)
    ccy = e.get("currency")
    return OpenPosition(account=account_name(account), mode=_mode(e.get("mode")), probe=cost_probe.is_probe(t.agent_id),
                        position_id=t.position_id, agent_id=t.agent_id, family=family_of(t.agent_id.split(":", 1)[-1]),
                        timeframe=t.timeframe, side=_side(t.side), lots=t.lots, entry=entry,
                        stop=t.sl, target=t.tp, opened_utc=t.opened_utc or t.entry_bar_ts, price=price,
                        floating_pnl=pnl, currency=ccy if isinstance(ccy, str) else None, r_now=r_now, open_risk=open_risk,
                        age_s=age,
                        stale=age is None or age > STALE_S)


def open_positions(state_dir: str | Path, now: pd.Timestamp,
                   engines: list[dict[str, Any]] | None = None) -> OpenPositionsView:
    """Every open position from the orders files, oldest first. An orders file that cannot be read is named (by its
    account, digits cut) and its positions are missing, not zero; the other accounts still show."""
    state = Path(state_dir)
    by_acc = engines_by_account(state, engines)
    out: list[OpenPosition] = []
    unreadable: list[str] = []
    for f in sorted(state.glob("orders_*.json")):
        account = f.stem.removeprefix("orders_")
        try:
            raw = json.loads(f.read_text()).get("open", {})
            trades = [load_record(OpenTrade, v, source="orders_<account>.json") for v in raw.values()]
        except Exception as exc:          # noqa: BLE001 - reporting only: one torn file never blanks the card
            log.warning("open positions: %s unreadable (%s)", f.name, type(exc).__name__)
            unreadable.append(account_name(account))
            continue
        out += [_position(account, t, by_acc.get(account, {}), now) for t in trades]
    return OpenPositionsView(as_of=now, positions=sorted(out, key=lambda p: p.opened_utc), unreadable=unreadable)


# ------------------------------------------------------------------------------------------------ N2 strategy watch
def _tf_from_id(agent_id: str) -> str | None:
    """A timeframe named as the id's last part (`<family>-g0-4h`); None otherwise (never guessed)."""
    last = agent_id.rsplit("-", 1)[-1]
    return last if last in TF_SECONDS else None


def _roster(state: Path) -> list[str]:
    f = state / "agents.json"
    rows = json.loads(f.read_text()) if f.exists() else []
    return [str(r["agent_id"]) for r in rows if isinstance(r, dict) and r.get("agent_id") and r.get("status") != "retired"]


def _price(x: float) -> str:
    return f"{x:,.2f}"


def _watch(agent_id: str, trades: list[ShadowTrade] | None, market_closed: bool, now: pd.Timestamp) -> StrategyWatch:
    tf = next((t.timeframe for t in trades or []), None) or _tf_from_id(agent_id)
    common: dict[str, Any] = dict(agent_id=agent_id, family=family_of(agent_id), timeframe=tf)
    if trades is None:
        return StrategyWatch(state="unknown", text="unknown: the shadow book cannot be read", **common)
    bar = pd.Timedelta(seconds=tf_seconds(tf)) if tf else None
    held = [t for t in trades if t.taken and t.exit_ts is None]
    if held:
        t = max(held, key=lambda x: x.entry_ts)
        side = _side(t.side)
        return StrategyWatch(state="in_trade", side=side, entry=t.entry, stop=t.stop, target=t.target,
                             signal_utc=t.entry_ts + bar if bar else None,
                             text=f"in a trade (shadow): {side} from {_price(t.entry)}, stop {_price(t.stop)}, "
                                  f"target {_price(t.target)}", **common)
    last = max(trades, key=lambda x: x.entry_ts, default=None)
    signal_utc = last.entry_ts + bar if last is not None and bar is not None else None
    if last is not None and signal_utc is not None and bar is not None and now - signal_utc <= bar:
        side = _side(last.side)
        why = "taken" if last.taken else "below its threshold, not taken"
        return StrategyWatch(state="signal", side=side, signal_utc=signal_utc, taken=last.taken,
                             text=f"signal: {side}, {why}", **common)
    if market_closed:
        return StrategyWatch(state="idle", signal_utc=signal_utc, text="idle: market closed", **common)
    if signal_utc is not None:
        return StrategyWatch(state="watching", signal_utc=signal_utc, text="watching; last signal", **common)
    return StrategyWatch(state="unknown", text="no signal recorded yet; the engine does not publish what it watches",
                         **common)


def strategy_watch(state_dir: str | Path, now: pd.Timestamp,
                   engines: list[dict[str, Any]] | None = None) -> StrategyWatchView:
    """One row per watched agent, by family then timeframe. The market is closed only when every reporting engine says
    so (no engine reporting: not known, so not idle)."""
    state = Path(state_dir)
    unreadable: list[Literal["agents", "shadow"]] = []
    try:
        roster = _roster(state)
    except Exception as exc:              # noqa: BLE001 - reporting only
        log.warning("strategy watch: agents.json unreadable (%s)", type(exc).__name__)
        roster, unreadable = [], ["agents"]
    by_agent: dict[str, list[ShadowTrade]] | None = {}
    try:
        recent = now - pd.Timedelta(days=WATCH_DAYS)
        for book in ShadowBook(state).books.values() if (state / "shadow_book.json").exists() else []:
            for t in [*book.open, *book.closed]:
                by_agent.setdefault(t.agent_id, []).append(t)   # type: ignore[union-attr]
        roster += [a for a, ts in (by_agent or {}).items()
                   if a not in roster and any(t.exit_ts is None or t.entry_ts >= recent for t in ts)]
    except Exception as exc:              # noqa: BLE001 - reporting only: every state is then unknown
        log.warning("strategy watch: shadow book unreadable (%s)", type(exc).__name__)
        by_agent = None
        unreadable.append("shadow")
    reports = list(engines_by_account(state, engines).values())
    closed = bool(reports) and all(e.get("market_closed") is True for e in reports)
    rows = [_watch(a, None if by_agent is None else by_agent.get(a, []), closed, now) for a in roster]
    rows.sort(key=lambda r: (r.family, tf_seconds(r.timeframe) if r.timeframe else 10**9, r.agent_id))
    return StrategyWatchView(as_of=now, agents=rows, unreadable=unreadable)


# ------------------------------------------------------------------------------------------------ today's money
class ProbeTerms(FrozenRecord):
    """An account's cost-probe terms (config/accounts.yaml `cost_probe`, ops/accounts.CostProbe), enabled only."""
    budget_share: float
    max_loss: float
    probe_only: bool = False      # ADR 0037 amendment 4: a dedicated probe account keeps no real-trade reserve


def probe_terms_from_config() -> dict[str, ProbeTerms]:
    """Accounts with cost probes on; {} when accounts.yaml cannot be read (the meter then shows the daily limit only)."""
    try:
        from goldbot.ops.accounts import load_accounts
        return {k: ProbeTerms(budget_share=a.cost_probe.budget_share, max_loss=a.cost_probe.max_loss,
                              probe_only=a.probe_only)
                for k, a in load_accounts().items() if a.cost_probe.enabled}
    except Exception as exc:              # noqa: BLE001 - reporting only
        log.warning("today: accounts.yaml unreadable (%s): probe stop not shown", type(exc).__name__)
        return {}


def _meter(e: dict[str, Any], probe: ProbeTerms | None) -> LossMeter | None:
    limit, remaining = _num(e.get("daily_loss_limit")), _num(e.get("daily_loss_remaining"))
    if limit is None or remaining is None or limit <= 0:
        return None
    used = round(max(0.0, limit - remaining), 2)
    caps = [LossLimit(key="daily_limit", label="daily loss limit: all new entries stop", limit=round(limit, 2),
                      remaining=round(remaining, 2))]
    if probe is not None:
        reserve = cost_probe.real_trade_reserve(probe.probe_only)
        stop = round(min(probe.budget_share * limit, limit * (1 - reserve) - probe.max_loss), 2)
        if stop > 0:
            caps.insert(0, LossLimit(key="probe_stop", label="probe stop: new cost probes stop", limit=stop,
                                     remaining=round(max(0.0, stop - used), 2)))
    return LossMeter(used=used, limits=caps, binding=min(caps, key=lambda c: c.remaining).key)


def _closed_today(state: Path, account: str, now: pd.Timestamp, e: dict[str, Any]
                  ) -> tuple[float, int, DayCosts, bool]:
    """(net P&L, count, costs, complete) of the records closed this opportunity day. `complete` False when a record
    is gross or unreadable: the closed figure then leaves it out and says so."""
    start, end = day_bounds(report_day(now))
    trades, err = gp.load_closed_trades(state)
    pnl, n, commission, swap, spread, spread_ok, complete = 0.0, 0, 0.0, 0.0, 0.0, True, err is None
    for c in trades:
        if c.account_id != account or not start <= c.exit_utc < end:
            continue
        if gp.pnl_basis(c) != "net":
            complete = False
            continue
        pnl, n = pnl + c.pnl, n + 1
        commission += c.commission or 0.0
        swap += c.swap or 0.0
        spread_ok = False                 # a position record keeps its spread in points, not in money
    lot = _lot(e)
    contract, fx = _num(lot.get("contract_oz")), usd_to_account(e)
    for p in cost_probe.probes(state, account):
        if p.exit_utc is None or not start <= p.exit_utc < end:
            continue
        if p.pnl is None:
            complete = False
            continue
        pnl, n = pnl + p.pnl, n + 1
        commission += p.commission or 0.0
        swap += p.swap or 0.0
        if p.spread_usd is None or contract is None or fx is None:
            spread_ok = False
        else:
            spread += p.spread_usd * p.lots * contract * fx
    costs = DayCosts(spread=round(spread, 2), spread_complete=spread_ok, commission=round(commission, 2),
                     swap=round(swap, 2))
    return round(pnl, 2), n, costs, complete


def today_money(state_dir: str | Path, now: pd.Timestamp, probe_terms: dict[str, ProbeTerms],
                engines: list[dict[str, Any]] | None = None) -> TodayView:
    """One row per reporting engine (demo and live never summed). A record source that cannot be read leaves the
    closed figures None (`closed_unreadable`), the broker's figures still show."""
    state = Path(state_dir)
    rows: list[TodayAccount] = []
    for account, e in sorted(engines_by_account(state, engines).items()):
        eq, d0, bal = _num(e.get("equity")), _num(e.get("day_start_equity")), _num(e.get("balance"))
        try:
            closed, n, costs, complete = _closed_today(state, account, now, e)
            readable = True
        except Exception as exc:          # noqa: BLE001 - reporting only
            log.warning("today: closed records unreadable (%s)", type(exc).__name__)
            closed, n, costs, complete, readable = None, 0, None, False, False
        age = _age(e, now)
        ccy = e.get("currency")
        rows.append(TodayAccount(
            account=account_name(account), mode=_mode(e.get("mode")), currency=ccy if isinstance(ccy, str) else None,
            day_pnl=round(eq - d0, 2) if eq is not None and d0 else None,
            open_pnl=round(eq - bal, 2) if eq is not None and bal is not None else None,
            closed_pnl=closed if readable else None, closed_n=n, closed_complete=complete, costs=costs,
            loss_meter=_meter(e, probe_terms.get(account)), age_s=age, stale=age is None or age > STALE_S))
    return TodayView(as_of=now, accounts=rows)
