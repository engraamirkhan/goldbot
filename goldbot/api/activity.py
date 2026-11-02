"""The trade readers behind the Home trades summary (api/trades_overview.py) and the ledger's account lookup: whole
trades (`Trade`: the opened and the closed event) merged from what the engines already write. Nothing here writes or
decides. (The Home live feed, `GET /api/activity`, that first used these readers was retired on 2026-11-02.)

Sources, each read as its writer leaves it:
* shadow trades opened and closed: state/shadow_book.json (engine/shadow.py), counterfactual candidates included and
  marked `taken` False (a calibration sample, not a trade). The book stamps bars by their open time, so a trade's
  events sit at its signal bar's close (the entry) and at its exit bar's close.
* account positions opened and closed: open ones from state/orders_<account>.json (engine/store.py `OpenTrade`),
  closed ones from the closed-trade record (ops/gates_phase.py `ClosedTrade`, opened at `entry_utc` when recorded).
* cost-probe orders opened and closed: aux.db `cost_probes` (engine/cost_probe.py `probes`). A probe's open position
  in the orders file is left to the ledger, so it is never counted twice.

Design rules kept: "accounts appear by config name. Any run of 5 or more digits is cut to its last two"
(`entry_text.account_name`). A source that cannot be read is named in `unreadable` and the others still read (a
dashboard error never stops trading and never hides the rest).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypeVar

import pandas as pd

from goldbot.api.schema import ActivityEvent, ActivitySource
from goldbot.base import FrozenRecord, load_record
from goldbot.config import tf_seconds
from goldbot.engine import cost_probe
from goldbot.engine.shadow import ShadowBook
from goldbot.engine.store import OpenTrade
from goldbot.ops import gates_phase as gp
from goldbot.ops.accounts import engine_state_files
from goldbot.telegram.entry_text import account_name

log = logging.getLogger(__name__)

__all__ = ["Trade", "engines_by_account", "read_trades"]

T = TypeVar("T")


def _side(s: int | None) -> Literal["long", "short"] | None:
    return None if not s else ("long" if s > 0 else "short")


def _mode(v: Any) -> Literal["demo", "live", "paper"] | None:
    return v if v in ("demo", "live", "paper") else None


def _r(ret: float | None, entry: float, stop: float) -> float | None:
    """R of the initial risk from a return: ret x entry / |entry - stop| (the shadow book records no R)."""
    risk = abs(entry - stop)
    return None if ret is None or risk <= 0 else round(ret * entry / risk, 3)


class Trade(FrozenRecord):
    """One trade as the trades summary (api/trades_overview.py) reads it, each source parsed once: its opened event
    (None for a closed-trade record without `entry_utc`) and its closed event (None while open). `taken` False marks a counterfactual shadow candidate; `net` says the closed P&L is net of every cost."""
    source: ActivitySource
    opened: ActivityEvent | None = None
    closed: ActivityEvent | None = None
    taken: bool = True
    net: bool = False

    @property
    def events(self) -> list[ActivityEvent]:
        return [e for e in (self.opened, self.closed) if e is not None]


def _shadow(state: Path) -> list[Trade]:
    """Every shadow trade, counterfactual candidates included (`taken` False; the summary leaves them out)."""
    if not (state / "shadow_book.json").exists():
        return []
    out: list[Trade] = []
    for book in ShadowBook(state).books.values():
        for t in [*book.open, *book.closed]:
            bar = pd.Timedelta(seconds=tf_seconds(t.timeframe))
            common: dict[str, Any] = dict(mode="paper", agent_id=t.agent_id, version=t.version, timeframe=t.timeframe,
                                          side=_side(t.side), p=round(t.p, 4), entry=t.entry, stop=t.stop,
                                          target=t.target)
            closed = None if t.exit_ts is None else ActivityEvent(
                ts=t.exit_ts + bar, kind="shadow_closed", exit=t.exit, reason=t.barrier, r=_r(t.ret, t.entry, t.stop),
                ret=t.ret, **common)
            out.append(Trade(source="shadow", taken=t.taken, closed=closed,
                             opened=ActivityEvent(ts=t.entry_ts + bar, kind="shadow_opened", **common)))
    return out


def _positions(state: Path, engines: dict[str, dict[str, Any]]) -> list[Trade]:
    out: list[Trade] = []
    seen: set[tuple[str, int]] = set()
    trades, err = gp.load_closed_trades(state)
    if err:
        raise ValueError(err)
    for c in trades:
        acc = account_name(c.account_id)
        common: dict[str, Any] = dict(account=acc, mode=c.mode, agent_id=c.agent_id, timeframe=c.timeframe,
                                      side=_side(c.side), lots=c.lots, entry=c.entry_price)
        opened = None if c.entry_utc is None else ActivityEvent(ts=c.entry_utc, kind="position_opened", **common)
        # a legacy record without pnl_currency is in the account currency (as progress.trades_summary reads it)
        ccy = c.pnl_currency or engines.get(c.account_id, {}).get("currency")
        closed = ActivityEvent(ts=c.exit_utc, kind="position_closed", exit=c.exit_price, reason=c.exit_reason, r=c.r,
                               ret=c.ret, pnl=c.pnl, currency=ccy if isinstance(ccy, str) else None, **common)
        out.append(Trade(source="positions", opened=opened, closed=closed, net=gp.pnl_basis(c) == "net"))
        if c.position_id is not None:
            seen.add((c.account_id, c.position_id))
    for f in sorted(state.glob("orders_*.json")):
        account = f.stem.removeprefix("orders_")
        raw = json.loads(f.read_text()).get("open", {})
        for v in raw.values():
            t = load_record(OpenTrade, v, source="orders_<account>.json")
            if cost_probe.is_learning(t.agent_id) or (account, t.position_id) in seen:   # probes and arms: learning
                continue
            out.append(Trade(source="positions", opened=ActivityEvent(
                ts=t.opened_utc or t.entry_bar_ts, kind="position_opened", account=account_name(account),
                mode=_mode(engines.get(account, {}).get("mode")), agent_id=t.agent_id, timeframe=t.timeframe,
                side=_side(t.side), lots=t.lots, entry=t.open_price if t.open_price is not None else t.entry,
                stop=t.sl, target=t.tp)))
    return out


def _probes(state: Path, engines: dict[str, dict[str, Any]]) -> list[Trade]:
    out: list[Trade] = []
    for p in cost_probe.probes(state):
        ccy = engines.get(p.account_id, {}).get("currency")
        common: dict[str, Any] = dict(account=account_name(p.account_id), mode="demo", agent_id=p.agent_id,
                                      version=p.version, side=_side(p.side), lots=p.lots, entry=p.fill_price)
        closed = None
        if p.exit_utc is not None:
            r = round(p.pnl / p.stop_loss, 3) if p.pnl is not None and p.stop_loss else None
            closed = ActivityEvent(ts=p.exit_utc, kind="probe_closed", exit=p.exit_price, reason=p.exit_reason,
                                   pnl=p.pnl, currency=ccy if isinstance(ccy, str) else None, r=r, **common)
        # the ledger's P&L is the broker's deals, net of every cost (engine/cost_probe.py ProbeRecord.pnl)
        out.append(Trade(source="probes", opened=ActivityEvent(ts=p.sent_utc, kind="probe_opened", **common),
                         closed=closed, net=True))
    return out


def engines_by_account(state: Path, engines: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """The engines' own state by account (`engines` None: the enabled accounts' state/engine_<account>.json, as the
    API's State.engines)."""
    if engines is None:
        engines = []
        for f in engine_state_files(state):
            try:
                engines.append(json.loads(f.read_text()))
            except (OSError, ValueError):
                continue
    return {str(e["account"]): e for e in engines if isinstance(e, dict) and e.get("account")}


def _read(name: ActivitySource, read: Callable[[], list[T]], unreadable: list[ActivitySource]) -> list[T]:
    try:
        return read()
    except Exception as exc:              # noqa: BLE001 - reporting only: one torn file never blanks the view
        log.warning("activity: %s unreadable (%s)", name, type(exc).__name__)
        unreadable.append(name)
        return []


def read_trades(state_dir: str | Path, engines: list[dict[str, Any]] | None = None
                ) -> tuple[list[Trade], list[ActivitySource]]:
    """Every shadow trade, account position and cost probe, and the sources that could not be read (named, the others
    still read)."""
    state = Path(state_dir)
    unreadable: list[ActivitySource] = []
    return _trades(state, engines_by_account(state, engines), unreadable), unreadable


def _trades(state: Path, by_acc: dict[str, dict[str, Any]], unreadable: list[ActivitySource]) -> list[Trade]:
    return [*_read("shadow", lambda: _shadow(state), unreadable),
            *_read("positions", lambda: _positions(state, by_acc), unreadable),
            *_read("probes", lambda: _probes(state, by_acc), unreadable)]
