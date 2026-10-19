"""The Home live feed (owner request 2026-10-19: "show what the system is doing"): `GET /api/activity`, one
time-ordered list, newest first, merged from what the engines already write. Nothing here writes or decides.

Sources, each read as its writer leaves it:
* shadow trades opened and closed: state/shadow_book.json (engine/shadow.py). Taken trades only: a counterfactual
  candidate (`taken` False) is a calibration sample, not activity. The book stamps bars by their open time, so a trade
  shows at its signal bar's close (the entry) and at its exit bar's close.
* account positions opened and closed: open ones from state/orders_<account>.json (engine/store.py `OpenTrade`),
  closed ones from the closed-trade record (ops/gates_phase.py `ClosedTrade`, opened at `entry_utc` when recorded).
* cost-probe orders opened and closed: aux.db `cost_probes` (engine/cost_probe.py `probes`). A probe's open position
  in the orders file is left to the ledger, so it is never shown twice.
* entries the engine refused: the store's `decisions` journal (Engine.flush_journal) for the last `JOURNAL_DAYS`, the
  rows whose action is a refusal (`REFUSED`). Scores below the threshold are every bar's routine and are left out.

Design rules kept: "accounts appear by config name. Any run of 5 or more digits is cut to its last two"
(`entry_text.account_name`); no detail text from the journal (it can hold exception text), only the action code and
the version. A source that cannot be read is named in `unreadable` and the others still show (a dashboard error never
stops trading and never hides the rest).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from goldbot.api.schema import ActivityEvent, ActivitySource, ActivityView
from goldbot.base import load_record
from goldbot.config import tf_seconds
from goldbot.data.store import Store
from goldbot.engine import cost_probe
from goldbot.engine.shadow import ShadowBook
from goldbot.engine.store import OpenTrade
from goldbot.ops import gates_phase as gp
from goldbot.telegram.entry_text import account_name

log = logging.getLogger(__name__)

__all__ = ["ACTIVITY_TTL_S", "JOURNAL_DAYS", "MAX_EVENTS", "REFUSED", "activity_view"]

MAX_EVENTS = 200                    # the most one call returns (and the cache holds)
ACTIVITY_TTL_S = 10.0               # one build serves every poll for 10 s (the Home panel polls every 15 s)
JOURNAL_DAYS = 2                    # refusals older than this are history, not activity
# journal actions that are an entry refused or blocked (engine/runner.py, auto_entry.py, cost_probe.py)
REFUSED = ("gate:", "gate_at_approval:", "auto_refused", "bridge_refused", "auto_rejected:", "auto_paused:",
           "drift_halt", "feature_version_mismatch", "uncalibrated_rule_model", "cost_probe_refused:",
           "cost_probe_budget:", "cost_probe:auto_rejected:")
# at one instant a close is newer than an open, and both are newer than a refusal
_RANK: dict[str, int] = {"refused": 0, "shadow_opened": 1, "position_opened": 1, "probe_opened": 1,
                         "shadow_closed": 2, "position_closed": 2, "probe_closed": 2}


def _side(s: int | None) -> Literal["long", "short"] | None:
    return None if not s else ("long" if s > 0 else "short")


def _mode(v: Any) -> Literal["demo", "live", "paper"] | None:
    return v if v in ("demo", "live", "paper") else None


def _r(ret: float | None, entry: float, stop: float) -> float | None:
    """R of the initial risk from a return: ret x entry / |entry - stop| (the shadow book records no R)."""
    risk = abs(entry - stop)
    return None if ret is None or risk <= 0 else round(ret * entry / risk, 3)


def _shadow(state: Path) -> list[ActivityEvent]:
    if not (state / "shadow_book.json").exists():
        return []
    out: list[ActivityEvent] = []
    for book in ShadowBook(state).books.values():
        for t in [*book.open, *book.closed]:
            if not t.taken:
                continue
            bar = pd.Timedelta(seconds=tf_seconds(t.timeframe))
            common: dict[str, Any] = dict(mode="paper", agent_id=t.agent_id, version=t.version, timeframe=t.timeframe,
                                          side=_side(t.side), p=round(t.p, 4), entry=t.entry, stop=t.stop,
                                          target=t.target)
            out.append(ActivityEvent(ts=t.entry_ts + bar, kind="shadow_opened", **common))
            if t.exit_ts is not None:
                out.append(ActivityEvent(ts=t.exit_ts + bar, kind="shadow_closed", exit=t.exit, reason=t.barrier,
                                         r=_r(t.ret, t.entry, t.stop), ret=t.ret, **common))
    return out


def _positions(state: Path, engines: dict[str, dict[str, Any]]) -> list[ActivityEvent]:
    out: list[ActivityEvent] = []
    seen: set[tuple[str, int]] = set()
    trades, err = gp.load_closed_trades(state)
    if err:
        raise ValueError(err)
    for c in trades:
        acc = account_name(c.account_id)
        common: dict[str, Any] = dict(account=acc, mode=c.mode, agent_id=c.agent_id, timeframe=c.timeframe,
                                      side=_side(c.side), lots=c.lots, entry=c.entry_price)
        if c.entry_utc is not None:
            out.append(ActivityEvent(ts=c.entry_utc, kind="position_opened", **common))
        out.append(ActivityEvent(ts=c.exit_utc, kind="position_closed", exit=c.exit_price, reason=c.exit_reason,
                                 r=c.r, ret=c.ret, pnl=c.pnl, currency=c.pnl_currency, **common))
        if c.position_id is not None:
            seen.add((c.account_id, c.position_id))
    for f in sorted(state.glob("orders_*.json")):
        account = f.stem.removeprefix("orders_")
        raw = json.loads(f.read_text()).get("open", {})
        for v in raw.values():
            t = load_record(OpenTrade, v, source="orders_<account>.json")
            if cost_probe.is_probe(t.agent_id) or (account, t.position_id) in seen:
                continue
            out.append(ActivityEvent(ts=t.opened_utc or t.entry_bar_ts, kind="position_opened",
                                     account=account_name(account), mode=_mode(engines.get(account, {}).get("mode")),
                                     agent_id=t.agent_id, timeframe=t.timeframe, side=_side(t.side), lots=t.lots,
                                     entry=t.open_price if t.open_price is not None else t.entry, stop=t.sl,
                                     target=t.tp))
    return out


def _probes(state: Path, engines: dict[str, dict[str, Any]]) -> list[ActivityEvent]:
    out: list[ActivityEvent] = []
    for p in cost_probe.probes(state):
        ccy = engines.get(p.account_id, {}).get("currency")
        common: dict[str, Any] = dict(account=account_name(p.account_id), mode="demo", agent_id=p.agent_id,
                                      version=p.version, side=_side(p.side), lots=p.lots, entry=p.fill_price)
        out.append(ActivityEvent(ts=p.sent_utc, kind="probe_opened", **common))
        if p.exit_utc is not None:
            r = round(p.pnl / p.stop_loss, 3) if p.pnl is not None and p.stop_loss else None
            out.append(ActivityEvent(ts=p.exit_utc, kind="probe_closed", exit=p.exit_price, reason=p.exit_reason,
                                     pnl=p.pnl, currency=ccy if isinstance(ccy, str) else None, r=r, **common))
    return out


def _version(detail: Any) -> str | None:
    try:
        v = json.loads(detail).get("version") if isinstance(detail, str) else None
    except (ValueError, AttributeError):
        return None
    return v if isinstance(v, str) else None


def _refusals(store: Store, now: pd.Timestamp, engines: dict[str, dict[str, Any]]) -> list[ActivityEvent]:
    df = store.read("decisions", start=now - pd.Timedelta(days=JOURNAL_DAYS),
                    columns=["ts_utc", "account_id", "agent_id", "action", "p", "detail"])
    if df.empty:
        return []
    df = df[df["action"].astype(str).str.startswith(REFUSED)]
    out = []
    for row in df.to_dict("records"):
        acc = row["account_id"] if isinstance(row["account_id"], str) else None
        p = float(row["p"]) if isinstance(row["p"], (int, float)) and pd.notna(row["p"]) else None
        out.append(ActivityEvent(ts=pd.Timestamp(row["ts_utc"]), kind="refused",
                                 account=account_name(acc) if acc else None,
                                 mode=_mode(engines.get(acc or "", {}).get("mode")),
                                 agent_id=row["agent_id"] if isinstance(row["agent_id"], str) else None,
                                 version=_version(row["detail"]), p=p, reason=str(row["action"])))
    return out


def _newest_first(events: Iterable[ActivityEvent]) -> list[ActivityEvent]:
    return sorted(events, key=lambda e: (e.ts, _RANK[e.kind]), reverse=True)


def activity_view(state_dir: str | Path, store: Store | None, now: pd.Timestamp,
                  engines: list[dict[str, Any]] | None = None, limit: int = MAX_EVENTS) -> ActivityView:
    """The newest `limit` events from every source (store None: no journal, so no refusals). A source that raises is
    logged by exception type and named in `unreadable`; the others still show."""
    state = Path(state_dir)
    if engines is None:                    # the engines' own state files, as the API's State.engines reads them
        engines = []
        for f in state.glob("engine_*.json"):
            try:
                engines.append(json.loads(f.read_text()))
            except (OSError, ValueError):
                continue
    by_acc = {str(e["account"]): e for e in engines if isinstance(e, dict) and e.get("account")}
    readers: list[tuple[ActivitySource, Any]] = [
        ("shadow", lambda: _shadow(state)), ("positions", lambda: _positions(state, by_acc)),
        ("probes", lambda: _probes(state, by_acc))]
    if store is not None:
        readers.append(("journal", lambda: _refusals(store, now, by_acc)))
    events: list[ActivityEvent] = []
    unreadable: list[ActivitySource] = []
    for name, read in readers:
        try:
            events.extend(read())
        except Exception as exc:          # noqa: BLE001 - reporting only: one torn file never blanks the feed
            log.warning("activity feed: %s unreadable (%s)", name, type(exc).__name__)
            unreadable.append(name)
    return ActivityView(events=_newest_first(events)[:max(0, limit)], unreadable=unreadable)
