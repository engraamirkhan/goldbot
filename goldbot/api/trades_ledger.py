"""One trade ledger across every kind (docs/design/trades-ux-2026-10-27.md §2, item 129; ADR 0050): `GET /api/trades`
(filters, cursor paging, totals) and `GET /api/trades/{trade_id}`. Read-only: nothing here writes, sizes or orders.

Sources, each read as its writer leaves it, one row per trade (opened and closed merged):
* exploration and control arms: aux.db `exploration_trades` (filled rows; read-only connection, labs.aux), evidence
  voids from `evidence_exclusions` (scope "n");
* cost probes: aux.db `cost_probes`;
* engine strategy and real-money positions: the closed-trade record (ops/gates_phase.py) and, while open,
  state/orders_<account>.json (learning tags skipped: their ledgers above hold them; the orders file still gives an
  arm's or probe's current stop, `stop_now`);
* shadow trades that cleared their threshold: state/shadow_book.json (paper: no money, R gross from the return).
A source that cannot be read is named in `unreadable` and its trades are missing, not zero; the others still show.

Money: account currency; a closed row's `pnl` is the broker's net, and costs split spread (the entry's spread, both
legs, at the order's own USD rate), adverse entry slippage, commission and swap (a credit is a negative cost), so
`gross` = pnl + costs.total to the penny. An open row's `pnl_now` is at the engine's mid price from the fill, with the
engine's published USD -> account rate (item 128); a rate over FX_STALE_S old marks the row stale.

Blinding (ADR 0050 with the quant reviewer's binding tightenings), enforced here and never by the client:
* single trades always show their result in the detail view, and in any list whose totals are shown;
* "WITHHOLD every aggregate covering fewer than 3 unread arms": the totals' money is withheld when the matching rows
  hold an unread arm and either (a) the filter is narrowed to strategies (family, timeframe, arm, control, exit
  reason, evidence, or one of exploration / control without the other: a partly-read family is narrowed), or (b) any
  (account, opportunity day of the close) cell of the unread arms' closes covers fewer than `MIN_ARMS_FOR_TOTAL`
  strategy arms. Cells, not the whole set, and a closed trade is selected by its close day too, so two allowed totals
  never differ by a cell under the bar (today minus yesterday, all accounts minus one, two date ranges);
* the open-P&L total is withheld on its own when any account's unread open trades cover fewer than
  `MIN_ARMS_FOR_TOTAL` strategy arms (open trades are in a range only when it reaches now); each open row keeps its P&L;
* when the totals are withheld, the unread arms' closed rows in that list are `blinded`: result fields null;
* rows come newest first; there is no sort by result.
"""
from __future__ import annotations

import base64
import bisect
import json
import logging
import math
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TypeVar

import pandas as pd
from pydantic import PrivateAttr

from goldbot.api.activity import engines_by_account
from goldbot.api.schema import (
    CostSplit,
    ExitReason,
    TradeEvidence,
    TradeKind,
    TradeListPeriod,
    TradeRow,
    TradeSource,
    TradesPage,
    TradeStatus,
    TradeTotals,
)
from goldbot.api.strategy_labels import Catalog, account_label, catalog
from goldbot.base import FrozenRecord, load_record
from goldbot.config import tf_seconds
from goldbot.data.timeutil import ROLLOVER_UTC_H
from goldbot.engine import cost_probe
from goldbot.engine.exploration import HOLD_EXIT, ExplorationRecord
from goldbot.engine.shadow import ShadowBook
from goldbot.engine.store import OpenTrade
from goldbot.ops import gates_phase as gp
from goldbot.ops.account_view import usd_to_account
from goldbot.ops.report_days import day_bounds, report_day
from goldbot.research.attribution import family_of
from goldbot.telegram import labs
from goldbot.telegram.entry_text import account_name

log = logging.getLogger(__name__)

__all__ = ["FX_STALE_S", "LEDGER_TTL_S", "MAX_PAGE", "PAGE", "Ledger", "TradeQuery", "build_ledger", "detail",
           "exit_reason", "oday", "open_withheld", "query", "withheld_reason"]

T = TypeVar("T")

LEDGER_TTL_S = 10.0                 # one build serves every poll for 10 s (the List tab polls every 15 s)
PAGE = 50                           # rows per page by default (spec §3.2: "Load 50 more")
MAX_PAGE = 200
STALE_S = 60.0                      # the engine's figures (home_cards.STALE_S)
FX_STALE_S = 120.0                  # item 128: a USD -> account rate older than this is shown stale, never as current
WHY_MAX = 80
NARROWED = "the filter narrows to strategies whose arms are not read yet (ADR 0050): totals return at the read"
FEW_ARMS = (f"these trades cover fewer than {labs.MIN_ARMS_FOR_TOTAL} unread arms in an account and day "
            "(ADR 0050): open a single trade to see its result")
# raw exit codes (engine, broker deals, ledgers, shadow barriers) -> the closed vocabulary; anything else is "other"
EXIT_WORDS: dict[str, ExitReason] = {
    "target": "target", "tp": "target",
    "stop": "stop", "sl": "stop", "engine_stop_close": "stop",
    "trail": "trail", "trail_close": "trail",
    "time": "time_limit", "time_exit": "time_limit", "hard_flat": "time_limit", "flat": "time_limit",
    HOLD_EXIT: "time_limit", cost_probe.HOLD_EXIT: "time_limit",
    "weekend_close_loser": "friday_cut", "weekend_close_no_quote": "friday_cut", "weekend_flat": "friday_cut",
    "kill_switch_close": "kill_switch", "stop_out": "broker_stop_out",
    "broker_close": "manual",           # closed at the broker outside the engine (the owner or the terminal)
}
_ARM_KINDS: frozenset[TradeKind] = frozenset({"exploration", "control"})


def exit_reason(raw: str | None) -> ExitReason:
    """The closed vocabulary for a raw exit code; never None for a closed trade (an unknown code is "other")."""
    return EXIT_WORDS.get((raw or "").strip().lower(), "other")


_EPOCH = date(1970, 1, 1).toordinal()
_SHIFT_S = (24 - ROLLOVER_UTC_H) * 3600


def oday(ts: datetime) -> date:
    """The opportunity day of a UTC instant ([D-1 22:00, D 22:00) is D), as report_days.report_day, by arithmetic on
    the epoch seconds (the ledger asks it of every closed row on every query)."""
    return date.fromordinal(_EPOCH + int((ts.timestamp() + _SHIFT_S) // 86400))


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _side(s: int) -> Literal["long", "short"]:
    return "long" if s > 0 else "short"


def _mode(v: Any) -> Literal["demo", "live", "paper"] | None:
    return v if v in ("demo", "live", "paper") else None


def _r2(x: float | None) -> float | None:
    return None if x is None else round(x, 2)


def _why(text: str) -> str:
    return text if len(text) <= WHY_MAX else text[:WHY_MAX - 1].rstrip() + "…"


# ------------------------------------------------------------------------------------------------ context
@dataclass(frozen=True)
class _Acct:
    """What an account's engine publishes that a row needs (state/engine_<account>.json)."""
    mode: Literal["demo", "live", "paper"] | None = None
    currency: str | None = None
    price: float | None = None
    contract: float = labs.CONTRACT_OZ
    fx: float | None = None
    stale: bool = True                  # engine figures or rate too old: never current


def _acct(e: dict[str, Any], now: pd.Timestamp) -> _Acct:
    ts = _num(e.get("ts"))
    engine_old = ts is None or now.timestamp() - ts > STALE_S
    fx = usd_to_account(e)
    fx_at = e.get("usd_to_account_utc")
    fx_old = False
    if fx is not None and str(e.get("currency") or "").upper() != "USD":
        try:
            age = (now - pd.Timestamp(str(fx_at))).total_seconds()
            fx_old = not math.isfinite(age) or age > FX_STALE_S
        except (TypeError, ValueError):
            fx_old = True
    raw = e.get("lot_terms")
    lot: dict[str, Any] = raw if isinstance(raw, dict) else {}
    ccy = e.get("currency")
    return _Acct(mode=_mode(e.get("mode")), currency=ccy if isinstance(ccy, str) else None, price=_num(e.get("price")),
                 contract=_num(lot.get("contract_oz")) or labs.CONTRACT_OZ, fx=fx, stale=engine_old or fx_old)


@dataclass
class _Ctx:
    now: pd.Timestamp
    cat: Catalog
    accts: dict[str, _Acct]
    orders: dict[tuple[str, int], OpenTrade] = field(default_factory=dict)

    def acct(self, account: str) -> _Acct:
        return self.accts.get(account, _Acct())


def _costs(*, lots: float, contract: float, side: int, fx: float | None, spread_usd: float | None,
           requested: float | None, fill: float | None, commission: float | None, swap: float | None,
           priced: bool = True, exit_fill: float | None = None, exit_bid: float | None = None,
           exit_ask: float | None = None) -> CostSplit:
    """The split, each part rounded to the penny and `total` their sum (so gross = net + total exactly).

    Finance audit 2026-10-27: slippage is signed (favourable slippage is a negative cost, as adverse is a positive
    one) and covers both fills where the request is known: the entry against its requested price and the exit fill
    against the quote the close was sent on (bid for a long, ask for a short). Spread is half the entry's spread plus
    half the exit's; without a recorded exit quote the exit half is the entry's (`spread_exit_estimated`)."""
    oz = lots * contract
    spread = slip = None
    exit_quote = (exit_bid if side > 0 else exit_ask) if exit_fill is not None else None
    exit_spread = exit_ask - exit_bid if exit_ask is not None and exit_bid is not None else None
    if priced and fx is not None and fx > 0:
        if spread_usd is not None:
            legs = spread_usd / 2 + (exit_spread if exit_spread is not None else spread_usd) / 2
            spread = round(max(0.0, legs) * oz * fx, 2)
        if requested is not None and fill is not None:
            move = (fill - requested) * side
            if exit_quote is not None and exit_fill is not None:
                move += (exit_quote - exit_fill) * side
            slip = round(move * oz * fx, 2)
    comm = round(abs(commission), 2) if commission is not None else None
    sw = round(-swap, 2) if swap is not None else None
    parts = [spread, slip, comm, sw]
    return CostSplit(spread=spread, slippage=slip, commission=comm, swap=sw,
                     total=round(sum(p for p in parts if p is not None), 2), complete=all(p is not None for p in parts),
                     spread_exit_estimated=priced and spread is not None and exit_spread is None,
                     slippage_exit_known=exit_quote is not None)


def _open_money(side: int, entry: float | None, a: _Acct, lots: float, risk: float | None
                ) -> tuple[float | None, float | None]:
    """(pnl_now, r_now) at the engine's mid price and rate; None when either is unknown (never a guessed rate)."""
    if entry is None or a.price is None or a.fx is None:
        return None, None
    pnl = round(side * (a.price - entry) * lots * a.contract * a.fx, 2)
    return pnl, (round(pnl / risk, 3) if risk else None)


# ------------------------------------------------------------------------------------------------ the sources
def _voids(conn: sqlite3.Connection) -> dict[str, str]:
    """Order id -> the reason it is void for an arm's n (aux.db `evidence_exclusions`); {} on an older file."""
    try:
        rows = conn.execute("SELECT client_order_id, scope, reason FROM evidence_exclusions").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(c): str(r) for c, s, r in rows if "n" in json.loads(s)}


def _exploration(state: Path, ctx: _Ctx) -> list[TradeRow]:
    with labs.aux(state) as conn:
        if conn is None:
            return []
        recs = [t for t in labs.trades(conn) if t.fill_price is not None]
        voids = _voids(conn)
    return [_arm_row(t, ctx, voids) for t in recs]


def _arm_row(t: ExplorationRecord, ctx: _Ctx, voids: dict[str, str]) -> TradeRow:
    info = ctx.cat.arm(t.arm_id)
    family = info.family if info is not None else (t.family or "unknown")
    control = info.is_control if info is not None else False
    a = ctx.acct(t.account_id)
    entry = t.fill_price
    assert entry is not None
    closed = t.exit_utc is not None
    costs = _costs(lots=t.lots, contract=a.contract, side=t.side, fx=t.fx_acct_per_usd, spread_usd=t.spread_usd,
                   requested=t.requested_price, fill=entry, commission=t.commission, swap=t.swap)
    risk = t.stop_loss if t.stop_loss and t.stop_loss > 0 else None
    held = (t.exit_utc or ctx.now) - t.sent_utc
    evidence: TradeEvidence
    if not closed or t.pnl is None:
        evidence = "pending"
    elif t.client_order_id in voids:
        evidence = "excluded"
    elif t.censored:
        evidence = "censored"
    else:
        evidence = "counted"
    pnl_now, r_now = (None, None) if closed else _open_money(t.side, entry, a, t.lots, risk)
    held_by = ctx.orders.get((t.account_id, t.position_id or -1))
    why = ctx.cat.why(family) if not control else f"Control: {ctx.cat.why(family)[:1].lower()}{ctx.cat.why(family)[1:]}"
    return TradeRow(
        trade_id=f"exploration:{account_name(t.account_id)}:{t.client_order_id}", kind="control" if control else "exploration",
        status="closed" if closed else "open", account=account_name(t.account_id),
        account_label=account_label(t.account_id), mode=a.mode or "demo", family=family,
        family_label=ctx.cat.family_label(family), variant=info.variant if info is not None else t.tf,
        timeframe=t.tf, arm_id=t.arm_id, is_control=control, side=_side(t.side), lots=t.lots, entry=entry,
        stop=round(entry - t.side * t.stop_usd, 2), stop_now=held_by.sl if held_by is not None and not closed else None,
        target=round(entry + t.side * t.target_usd, 2), exit=t.exit_price, exit_utc=t.exit_utc,
        exit_reason=exit_reason(t.exit_reason) if closed else None, censored=bool(t.censored), opened_utc=t.sent_utc,
        held_s=max(0.0, held.total_seconds()), hold_limit_s=float(t.hold_s), risk_amount=_r2(risk),
        currency=a.currency, pnl=_r2(t.pnl) if closed else None,
        r=round(t.pnl / risk, 3) if closed and t.pnl is not None and risk else None,
        gross=round(round(t.pnl, 2) + costs.total, 2) if closed and t.pnl is not None else None,
        pnl_now=pnl_now, r_now=r_now, price_now=None if closed else a.price, costs=costs, why=_why(why),
        evidence=evidence, excluded_reason=voids.get(t.client_order_id), blinded=False,
        stale=False if closed else a.stale, position_id=t.position_id)


def _probes(state: Path, ctx: _Ctx) -> list[TradeRow]:
    out = []
    for p in cost_probe.probes(state):
        if p.fill_price is None:
            continue
        a = ctx.acct(p.account_id)
        scheduled = p.purpose == "cost_measurement"
        family = "cost_probe" if scheduled else family_of(p.agent_id.removeprefix(cost_probe.PREFIX))
        label = "Cost probe" if scheduled else f"Cost probe · {ctx.cat.family_label(family)}"
        closed = p.exit_utc is not None
        costs = _costs(lots=p.lots, contract=a.contract, side=p.side, fx=p.fx_acct_per_usd, spread_usd=p.spread_usd,
                       requested=p.requested_price, fill=p.fill_price, commission=p.commission, swap=p.swap,
                       exit_fill=p.exit_price, exit_bid=p.exit_bid, exit_ask=p.exit_ask)
        risk = p.stop_loss if p.stop_loss and p.stop_loss > 0 else None
        pnl_now, r_now = (None, None) if closed else _open_money(p.side, p.fill_price, a, p.lots, risk)
        why = ("Scheduled cost measurement at the minimum lot" if scheduled
               else f"Measures the real costs of {ctx.cat.family_label(family)}'s signal")
        held_by = ctx.orders.get((p.account_id, p.position_id or -1))
        out.append(TradeRow(
            trade_id=f"cost_probe:{account_name(p.account_id)}:{p.client_order_id}", kind="cost_probe",
            status="closed" if closed else "open", account=account_name(p.account_id),
            account_label=account_label(p.account_id), mode=a.mode or "demo", family=family, family_label=label,
            variant=None, timeframe=None, arm_id=None, is_control=False, side=_side(p.side), lots=p.lots,
            entry=p.fill_price, stop=None, stop_now=held_by.sl if held_by is not None and not closed else None,
            target=None, exit=p.exit_price, exit_utc=p.exit_utc, exit_reason=exit_reason(p.exit_reason) if closed else None,
            censored=False, opened_utc=p.sent_utc, held_s=max(0.0, ((p.exit_utc or ctx.now) - p.sent_utc).total_seconds()),
            hold_limit_s=None, risk_amount=_r2(risk), currency=a.currency, pnl=_r2(p.pnl) if closed else None,
            r=round(p.pnl / risk, 3) if closed and p.pnl is not None and risk else None,
            gross=round(round(p.pnl, 2) + costs.total, 2) if closed and p.pnl is not None else None,
            pnl_now=pnl_now, r_now=r_now, price_now=None if closed else a.price, costs=costs, why=_why(why),
            evidence="not_evidence", excluded_reason=None, blinded=False, stale=False if closed else a.stale,
            position_id=p.position_id))
    return out


def _orders(state: Path) -> dict[tuple[str, int], OpenTrade]:
    """Every open trade the engines hold, by (account, position id); a torn file raises (the source is unreadable)."""
    out: dict[tuple[str, int], OpenTrade] = {}
    for f in sorted(state.glob("orders_*.json")):
        account = f.stem.removeprefix("orders_")
        for v in json.loads(f.read_text()).get("open", {}).values():
            t = load_record(OpenTrade, v, source="orders_<account>.json")
            out[(account, t.position_id)] = t
    return out


def _strategy_why(ctx: _Ctx, family: str, kind: TradeKind) -> str:
    base = ctx.cat.why(family)
    return _why(f"{base} (owner-confirmed)" if kind == "real" else f"{base} (model, demo auto entry)")


def _positions(state: Path, ctx: _Ctx) -> list[TradeRow]:
    trades, err = gp.load_closed_trades(state)
    if err:
        raise ValueError(err)
    out: list[TradeRow] = []
    seen: set[tuple[str, int]] = set()
    for c in trades:
        if cost_probe.is_learning(c.agent_id):
            continue
        a = ctx.acct(c.account_id)
        kind: TradeKind = "real" if c.mode == "live" else "strategy"
        family = c.family or family_of(c.agent_id or "unknown")
        net = gp.pnl_basis(c) == "net"
        costs = _costs(lots=c.lots or 0.0, contract=a.contract, side=c.side or 1, fx=None, spread_usd=None,
                       requested=None, fill=None, commission=c.commission, swap=c.swap, priced=False)
        risk = abs(c.pnl / c.r) if c.r else None
        ccy = c.pnl_currency or a.currency
        side = c.side or 1
        out.append(TradeRow(
            trade_id=f"position:{account_name(c.account_id)}:{c.position_id or c.client_order_id}", kind=kind,
            status="closed", account=account_name(c.account_id), account_label=account_label(c.account_id),
            mode=c.mode, family=family, family_label=ctx.cat.family_label(family), variant=c.timeframe,
            timeframe=c.timeframe, arm_id=None, is_control=False, side=_side(side), lots=c.lots, entry=c.entry_price,
            stop=c.initial_sl, stop_now=None, target=c.final_tp, exit=c.exit_price, exit_utc=c.exit_utc,
            exit_reason=exit_reason(c.exit_reason), censored=False, opened_utc=c.entry_utc or c.exit_utc,
            held_s=(c.exit_utc - c.entry_utc).total_seconds() if c.entry_utc is not None else None, hold_limit_s=None,
            risk_amount=_r2(risk), currency=ccy, pnl=_r2(c.pnl) if net else None, r=c.r,
            gross=round(round(c.pnl, 2) + costs.total, 2) if net else _r2(c.pnl), pnl_now=None, r_now=None,
            price_now=None, costs=costs, why=_strategy_why(ctx, family, kind), evidence="not_evidence",
            excluded_reason=None, blinded=False, stale=False, position_id=c.position_id))
        if c.position_id is not None:
            seen.add((c.account_id, c.position_id))
    for (account, pid), t in ctx.orders.items():
        if cost_probe.is_learning(t.agent_id) or (account, pid) in seen:
            continue
        a = ctx.acct(account)
        kind = "real" if a.mode == "live" else "strategy"
        family = family_of(t.agent_id)
        entry = t.open_price if t.open_price is not None else t.entry
        stop0 = t.initial_sl if t.initial_sl is not None else t.sl
        risk = (round(abs(entry - stop0) * t.lots * a.contract * a.fx, 2)
                if entry is not None and stop0 is not None and a.fx is not None else None)
        pnl_now, r_now = _open_money(t.side, entry, a, t.lots, risk)
        opened = t.opened_utc or t.entry_bar_ts
        out.append(TradeRow(
            trade_id=f"position:{account_name(account)}:{pid}", kind=kind, status="open", account=account_name(account),
            account_label=account_label(account), mode=a.mode, family=family, family_label=ctx.cat.family_label(family),
            variant=t.timeframe, timeframe=t.timeframe, arm_id=None, is_control=False, side=_side(t.side), lots=t.lots,
            entry=entry, stop=stop0, stop_now=t.sl, target=t.tp, exit=None, exit_utc=None, exit_reason=None,
            censored=False, opened_utc=opened, held_s=max(0.0, (ctx.now - opened).total_seconds()),
            hold_limit_s=float(t.max_bars * tf_seconds(t.timeframe)) if t.timeframe else None, risk_amount=risk,
            currency=a.currency, pnl=None, r=None, gross=None, pnl_now=pnl_now, r_now=r_now, price_now=a.price,
            costs=None, why=_strategy_why(ctx, family, kind), evidence="not_evidence", excluded_reason=None,
            blinded=False, stale=a.stale, position_id=pid))
    return out


def _shadow(state: Path, ctx: _Ctx) -> list[TradeRow]:
    """Taken shadow trades (a counterfactual candidate is a calibration sample, not a trade)."""
    from goldbot.api.activity import _r as shadow_r
    if not (state / "shadow_book.json").exists():
        return []
    out = []
    for book in ShadowBook(state).books.values():
        for t in [*book.open, *book.closed]:
            if not t.taken:
                continue
            bar = pd.Timedelta(seconds=tf_seconds(t.timeframe))
            family = family_of(t.agent_id)
            opened = t.entry_ts + bar
            closed = t.exit_ts is not None
            exit_at = t.exit_ts + bar if t.exit_ts is not None else None
            out.append(TradeRow(
                trade_id=f"shadow:{t.agent_id}:{t.version}:{t.entry_ts:%Y%m%dT%H%M}", kind="shadow",
                status="closed" if closed else "open", account=None, account_label=account_label(None), mode="paper",
                family=family, family_label=ctx.cat.family_label(family), variant=t.timeframe, timeframe=t.timeframe,
                arm_id=None, is_control=False, side=_side(t.side), lots=None, entry=t.entry, stop=t.stop,
                stop_now=None, target=t.target, exit=t.exit, exit_utc=exit_at,
                exit_reason=exit_reason(t.barrier) if closed else None, censored=False, opened_utc=opened,
                held_s=max(0.0, ((exit_at or ctx.now) - opened).total_seconds()),
                hold_limit_s=float(t.max_bars * bar.total_seconds()), risk_amount=None, currency=None, pnl=None,
                r=shadow_r(t.ret, t.entry, t.stop) if closed else None, gross=None, pnl_now=None, r_now=None,
                price_now=None, costs=None, why=_why(f"{ctx.cat.why(family)} (paper, p {t.p:.2f} cleared its bar)"),
                evidence="not_evidence", excluded_reason=None, blinded=False, stale=False))
    return out


# ------------------------------------------------------------------------------------------------ the build
class Ledger(FrozenRecord):
    """Every trade of every kind, newest activity first, and the sources that could not be read. Its query index and
    result cache (`_Index`) are built on the first query and live as long as this build (LEDGER_TTL_S)."""
    as_of: pd.Timestamp
    rows: list[TradeRow]
    unreadable: list[TradeSource]
    _index: Any = PrivateAttr(default=None)

    def model_post_init(self, context: Any, /) -> None:
        """The index is built with the ledger (inside the API's 10 s cache build), never on a request."""
        self._index = _Index.build(self.rows)

    def index(self) -> _Index:
        if self._index is None:
            self._index = _Index.build(self.rows)
        return self._index


def _read(name: TradeSource, read: Callable[[], list[T]], unreadable: list[TradeSource]) -> list[T]:
    try:
        return read()
    except Exception as exc:              # noqa: BLE001 - reporting only: one torn source never blanks the ledger
        log.warning("trades ledger: %s unreadable (%s)", name, type(exc).__name__)
        unreadable.append(name)
        return []


def _key(r: TradeRow) -> tuple[datetime, str]:
    return (r.exit_utc or r.opened_utc, r.trade_id)


def build_ledger(state_dir: str | Path, now: pd.Timestamp, engines: list[dict[str, Any]] | None = None,
                 cat: Catalog | None = None) -> Ledger:
    """Read every source once (the API caches the result LEDGER_TTL_S)."""
    state = Path(state_dir)
    unreadable: list[TradeSource] = []
    ctx = _Ctx(now=now, cat=cat or catalog(),
               accts={k: _acct(e, now) for k, e in engines_by_account(state, engines).items()})
    orders = _read("orders", lambda: [_orders(state)], unreadable)
    ctx.orders = orders[0] if orders else {}
    rows = [*_read("exploration", lambda: _exploration(state, ctx), unreadable),
            *_read("probes", lambda: _probes(state, ctx), unreadable),
            *_read("positions", lambda: _positions(state, ctx), unreadable),
            *_read("shadow", lambda: _shadow(state, ctx), unreadable)]
    rows.sort(key=_key, reverse=True)
    return Ledger(as_of=now, rows=rows, unreadable=unreadable)


# ------------------------------------------------------------------------------------------------ query
class TradeQuery(FrozenRecord):
    """The List tab's filters (spec §3.2). Empty lists mean "all"; `kind` empty means every kind but shadow (the
    spec's default). `since` / `until` are whole opportunity days and override `period`."""
    period: TradeListPeriod = "today"
    since: date | None = None
    until: date | None = None
    account: tuple[str, ...] = ()
    family: tuple[str, ...] = ()
    timeframe: tuple[str, ...] = ()
    arm: tuple[str, ...] = ()
    kind: tuple[TradeKind, ...] = ()
    status: TradeStatus | None = None
    exit_reason: tuple[ExitReason, ...] = ()
    evidence: tuple[TradeEvidence, ...] = ()
    control: bool | None = None
    cursor: str | None = None
    limit: int = PAGE

    @property
    def plain(self) -> bool:
        """Only dates and accounts narrow the list (the default kinds, every status): the bisected slices are the
        answer, less shadow trades."""
        return not (self.kind or self.family or self.timeframe or self.arm or self.status or self.exit_reason
                    or self.evidence or self.control is not None)

    @property
    def narrowed(self) -> bool:
        """The filter selects strategies rather than labs (ADR 0050 tightening): its totals over unread arms are an
        arm's or a family's own figure."""
        arm_kinds = _ARM_KINDS & set(self.kind)
        return bool(self.family or self.timeframe or self.arm or self.exit_reason or self.evidence
                    or self.control is not None or len(arm_kinds) == 1)


PERIOD_DAYS: dict[TradeListPeriod, int | None] = {"today": 1, "7d": 7, "30d": 30, "all": None}


def bounds(q: TradeQuery, now: pd.Timestamp) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """[since, until) in UTC: whole opportunity days."""
    if q.since is not None or q.until is not None:
        lo = day_bounds(q.since.isoformat())[0] if q.since is not None else None
        hi = day_bounds(q.until.isoformat())[1] if q.until is not None else None
        return lo, hi
    days = PERIOD_DAYS[q.period]
    if days is None:
        return None, None
    first = date.fromisoformat(report_day(now)) - timedelta(days=days - 1)
    return day_bounds(first.isoformat())[0], None


DEFAULT_KINDS: frozenset[TradeKind] = frozenset({"real", "strategy", "exploration", "control", "cost_probe"})


def _matcher(q: TradeQuery, lo: pd.Timestamp | None, hi: pd.Timestamp | None,
             now: pd.Timestamp) -> Callable[[TradeRow], bool]:
    """One predicate per query, its sets built once (the ledger can hold 50k+ rows: p95 under 300 ms). A closed trade
    is in [lo, hi) by its CLOSE, the instant the blinding cells group it by (quant 2026-10-27, condition 1: selecting
    by the open let [D..D+1] minus [D..D] isolate one arm); an open trade only in a range that reaches now."""
    open_in = hi is None or hi > now
    kinds = frozenset(q.kind) or DEFAULT_KINDS
    accounts, families, tfs, arms = set(q.account), set(q.family), set(q.timeframe), set(q.arm)
    exits, evidence = set(q.exit_reason), set(q.evidence)

    def match(r: TradeRow) -> bool:
        if r.kind not in kinds:
            return False
        if r.exit_utc is None:
            if not open_in:
                return False
        elif (lo is not None and r.exit_utc < lo) or (hi is not None and r.exit_utc >= hi):
            return False
        return ((not accounts or r.account in accounts) and (not families or r.family in families)
                and (not tfs or r.timeframe in tfs) and (not arms or r.arm_id in arms)
                and (q.status is None or r.status == q.status) and (not exits or r.exit_reason in exits)
                and (not evidence or r.evidence in evidence) and (q.control is None or r.is_control == q.control))
    return match


def withheld_reason(rows: Iterable[TradeRow], *, narrowed: bool, cat: Catalog) -> tuple[str | None, bool]:
    """(why an aggregate over `rows` is withheld, else None; whether the unread arms' closed rows are blinded too),
    ADR 0050 with the quant tightenings. Unread: every arm (controls included) not listed in config/arm_reads.yaml.
    Totals: withheld when the filter is narrowed to strategies, or when any (account, opportunity day of the close)
    cell of unread closes covers fewer than MIN_ARMS_FOR_TOTAL strategy arms. Rows: blinded when the filter is
    narrowed, or when the whole set's unread closes cover fewer than MIN_ARMS_FOR_TOTAL strategy arms (the list is an
    arm's own record); a mixed list keeps single trades' results (accepted in the ADR). Open trades form no cell: their
    risk and P&L are the owner's money now (ADR 0050 decision 1), and the Now tab shows them per trade."""
    cells: dict[tuple[str | None, int], set[str]] = {}
    unread = False
    for r in rows:
        if r.arm_id is None or cat.is_read(r.arm_id):
            continue
        unread = True
        if r.exit_utc is None:
            continue
        arms = cells.setdefault((r.account, int((r.exit_utc.timestamp() + _SHIFT_S) // 86400)), set())
        if not r.is_control:
            arms.add(cat.unit_of(r.arm_id))     # a pooled family is one read unit: its arms count once
    if not unread:
        return None, False
    if narrowed:
        return NARROWED, True
    if any(len(a) < labs.MIN_ARMS_FOR_TOTAL for a in cells.values()):
        everywhere = set().union(*cells.values())
        return FEW_ARMS, len(everywhere) < labs.MIN_ARMS_FOR_TOTAL
    return None, False


def open_withheld(rows: Iterable[TradeRow], cat: Catalog) -> bool:
    """Quant 2026-10-27, condition 3: an open-P&L total is an aggregate too. True when any account's unread open
    trades cover fewer than MIN_ARMS_FOR_TOTAL strategy arms (an account with unread open controls only included).
    Each open row keeps its own P&L (ADR 0050 decision 1); only the total is withheld."""
    cells: dict[str | None, set[str]] = {}
    for r in rows:
        if r.exit_utc is None and r.arm_id is not None and not cat.is_read(r.arm_id):
            arms = cells.setdefault(r.account, set())
            if not r.is_control:
                arms.add(cat.unit_of(r.arm_id))
    return any(len(a) < labs.MIN_ARMS_FOR_TOTAL for a in cells.values())


def _blind(r: TradeRow) -> TradeRow:
    """A closed row of an unread arm in a withheld list: its result fields null (ADR 0050 decision 3)."""
    return r.model_copy(update={"pnl": None, "r": None, "gross": None, "exit": None, "exit_reason": None,
                                "blinded": True})


def totals(rows: Sequence[TradeRow], reason: str | None, open_hidden: bool = False) -> TradeTotals:
    """The rows' sums in one pass: money only from rows that carry it, in one currency (never summed across
    currencies). A closed trade whose net is unknown (gross-only record, unreadable deals) is counted in
    `net_unknown` and makes `net_pnl` None: the sum of the others is `net_partial`, never shown as the net (finance
    audit 2026-10-27: "a number missing a trade is not a net figure")."""
    n_open = unknown = 0
    net = costs = open_pnl = open_risk = 0.0
    ccys: set[str] = set()
    for r in rows:
        if r.currency is not None and (r.pnl is not None or r.pnl_now is not None or r.risk_amount is not None):
            ccys.add(r.currency)
        if r.status == "open":
            n_open += 1
            open_pnl += r.pnl_now or 0.0
            open_risk += r.risk_amount or 0.0
        else:
            if r.pnl is None and r.kind != "shadow":
                unknown += 1
            net += r.pnl or 0.0
            costs += r.costs.total if r.costs is not None else 0.0
    mixed = len(ccys) > 1
    money = reason is None and not mixed
    return TradeTotals(
        n=len(rows), n_open=n_open, n_closed=len(rows) - n_open, currency=next(iter(ccys)) if len(ccys) == 1 else None,
        mixed_currencies=mixed, net_pnl=round(net, 2) if money and not unknown else None,
        net_unknown=unknown, net_partial=round(net, 2) if money and unknown else None,
        costs=round(costs, 2) if money else None,
        open_pnl=round(open_pnl, 2) if money and not open_hidden else None, open_pnl_withheld=open_hidden,
        open_risk=round(open_risk, 2) if not mixed else None, withheld=reason is not None, withheld_reason=reason)


def _encode(r: TradeRow) -> str:
    at, tid = _key(r)
    return base64.urlsafe_b64encode(f"{pd.Timestamp(at).isoformat()}|{tid}".encode()).decode()


def _decode(cursor: str) -> tuple[pd.Timestamp, str]:
    try:
        at, tid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        ts = pd.Timestamp(at)
        if ts.tzinfo is None:                     # security LOW: a crafted naive cursor would fail the compare (500)
            raise ValueError("naive cursor time")
        return ts, tid
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("cursor is not one this API gave") from exc


@dataclass
class _Closed:
    """Closed rows newest close first, with their negated close instants (ascending: bisected) and opportunity days."""
    rows: list[TradeRow] = field(default_factory=list)
    neg: list[float] = field(default_factory=list)
    days: list[int] = field(default_factory=list)

    def add(self, r: TradeRow) -> None:
        assert r.exit_utc is not None
        secs = r.exit_utc.timestamp()
        self.rows.append(r)
        self.neg.append(-secs)
        self.days.append(int((secs + _SHIFT_S) // 86400))

    def span(self, lo: pd.Timestamp | None, hi: pd.Timestamp | None) -> tuple[int, int]:
        """The slice closing in [lo, hi), by bisection (no scan of the rows outside it)."""
        a = bisect.bisect_right(self.neg, -hi.timestamp()) if hi is not None else 0
        b = bisect.bisect_right(self.neg, -lo.timestamp()) if lo is not None else len(self.neg)
        return a, max(a, b)


@dataclass
class _Index:
    """Built once per ledger build (PR #206 CI: p95 over 50k rows): closed rows by close time, overall and per
    account, the open rows apart, and the answers already given for a filter (the ledger is immutable until the next
    build, so an answer stays right for its whole life)."""
    closed: _Closed
    by_account: dict[str | None, _Closed]
    open: list[TradeRow]
    answers: dict[tuple[Any, ...], tuple[list[TradeRow], str | None, bool, TradeTotals]] = field(default_factory=dict)

    @classmethod
    def build(cls, rows: Sequence[TradeRow]) -> _Index:
        closed, opened = _Closed(), []
        by_account: dict[str | None, _Closed] = {}
        for r in rows:                                  # newest activity first: closed rows stay newest close first
            if r.exit_utc is None:
                opened.append(r)
                continue
            closed.add(r)
            by_account.setdefault(r.account, _Closed()).add(r)
        return cls(closed=closed, by_account=by_account, open=opened)


ANSWERS_MAX = 256                       # distinct filters remembered per ledger build (10 s)


def _scan(closed: Sequence[TradeRow], days: Sequence[int], opened: Sequence[TradeRow], narrowed: bool,
          cat: Catalog) -> tuple[str | None, bool, TradeTotals]:
    """`withheld_reason`, `open_withheld` and `totals` in one pass over the matching rows (same rules, same results;
    tests/test_trades_ledger.py checks them against each other): read and counted by read unit, as there."""
    is_read, unit_of = cat.is_read, cat.unit_of
    cells: dict[tuple[str | None, int], set[str]] = {}
    open_cells: dict[str | None, set[str]] = {}
    unread = False
    n_closed = unknown = 0
    net = costs = open_pnl = open_risk = 0.0
    ccys: set[str] = set()
    for r, day in zip(closed, days):
        n_closed += 1
        pnl = r.pnl
        if pnl is None:
            if r.kind != "shadow":
                unknown += 1
        else:
            net += pnl
        if r.costs is not None:
            costs += r.costs.total
        if r.currency is not None and (pnl is not None or r.risk_amount is not None):
            ccys.add(r.currency)
        arm = r.arm_id
        if arm is not None and not is_read(arm):
            unread = True
            got = cells.setdefault((r.account, day), set())
            if not r.is_control:
                got.add(unit_of(arm))       # a pooled family is one read unit: its arms count once
    for r in opened:
        if r.pnl_now is not None:
            open_pnl += r.pnl_now
        if r.risk_amount is not None:
            open_risk += r.risk_amount
        if r.currency is not None and (r.pnl_now is not None or r.risk_amount is not None or r.pnl is not None):
            ccys.add(r.currency)
        arm = r.arm_id
        if arm is not None and not is_read(arm):
            unread = True
            got = open_cells.setdefault(r.account, set())
            if not r.is_control:
                got.add(unit_of(arm))
    reason, blind = None, False
    if unread and narrowed:
        reason, blind = NARROWED, True
    elif unread and any(len(a) < labs.MIN_ARMS_FOR_TOTAL for a in cells.values()):
        reason, blind = FEW_ARMS, len(set().union(*cells.values())) < labs.MIN_ARMS_FOR_TOTAL
    open_hidden = any(len(a) < labs.MIN_ARMS_FOR_TOTAL for a in open_cells.values())
    mixed = len(ccys) > 1
    money = reason is None and not mixed
    t = TradeTotals(
        n=n_closed + len(opened), n_open=len(opened), n_closed=n_closed,
        currency=next(iter(ccys)) if len(ccys) == 1 else None, mixed_currencies=mixed,
        net_pnl=round(net, 2) if money and not unknown else None, net_unknown=unknown,
        net_partial=round(net, 2) if money and unknown else None, costs=round(costs, 2) if money else None,
        open_pnl=round(open_pnl, 2) if money and not open_hidden else None, open_pnl_withheld=open_hidden,
        open_risk=round(open_risk, 2) if not mixed else None, withheld=reason is not None, withheld_reason=reason)
    return reason, blind, t


def _answer(ledger: Ledger, q: TradeQuery, cat: Catalog) -> tuple[list[TradeRow], str | None, bool, TradeTotals]:
    """Every matching row (newest activity first), why totals are withheld, whether unread rows are blinded, and the
    totals; remembered per filter for the life of the ledger build."""
    idx = ledger.index()
    key = (q.model_copy(update={"cursor": None, "limit": PAGE}), id(cat))
    hit = idx.answers.get(key)
    if hit is not None:
        return hit
    lo, hi = bounds(q, ledger.as_of)
    match = _matcher(q, None, None, ledger.as_of)        # the dates are applied by bisection below
    opened = [r for r in idx.open if (hi is None or hi > ledger.as_of) and match(r)]
    sources = ([idx.by_account[a] for a in dict.fromkeys(q.account) if a in idx.by_account] if q.account
               else [idx.closed])
    rows: list[TradeRow] = []
    days: list[int] = []
    for src in sources:
        a, b = src.span(lo, hi)
        if q.plain:                                     # nothing but dates, accounts and the default kinds
            rows += src.rows[a:b]
            days += src.days[a:b]
            continue
        keep = [i for i in range(a, b) if match(src.rows[i])]
        rows += [src.rows[i] for i in keep]
        days += [src.days[i] for i in keep]
    if q.plain:
        days = [d for r, d in zip(rows, days) if r.kind != "shadow"]
        rows = [r for r in rows if r.kind != "shadow"]
    reason, blind, t = _scan(rows, days, opened, q.narrowed, cat)
    if opened or len(sources) > 1:
        rows = sorted(rows + opened, key=_key, reverse=True)
    if len(idx.answers) >= ANSWERS_MAX:
        idx.answers.clear()
    idx.answers[key] = (rows, reason, blind, t)
    return rows, reason, blind, t


def query(ledger: Ledger, q: TradeQuery, cat: Catalog | None = None) -> TradesPage:
    """One page of the matching rows (newest activity first) and the totals of all of them, blinded server-side."""
    cat = cat or catalog()
    rows, reason, blind, t = _answer(ledger, q, cat)
    start = 0
    if q.cursor:
        after = _decode(q.cursor)
        start = next((i for i, r in enumerate(rows) if _key(r) < after), len(rows))
    limit = max(1, min(q.limit, MAX_PAGE))
    page = rows[start:start + limit]
    if blind:
        page = [_blind(r) if r.arm_id and not cat.is_read(r.arm_id) and r.status == "closed" else r for r in page]
    more = start + limit < len(rows)
    return TradesPage(as_of=ledger.as_of, rows=page, next_cursor=_encode(page[-1]) if more and page else None,
                      totals=t, unreadable=ledger.unreadable)


def detail(ledger: Ledger, trade_id: str) -> TradeRow | None:
    """One trade with its own result, whatever its arm's read (ADR 0050: one trade, the owner's money)."""
    return next((r for r in ledger.rows if r.trade_id == trade_id), None)
