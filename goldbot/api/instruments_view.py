"""The dashboard's view of the multi-instrument demo (lane L4; docs: multi-instrument UI spec 2026-11-02). Read-only.

Design rules (UI spec, ui-ux-designer 2026-11-02): "Every price via fmtPrice(symbol, x) with the instrument's digits +
unit"; "never sum money across accounts"; groups in the fixed order Gold, Metals, Equity, FX, Energy, Rates, Ags;
"grey = closed/disabled, amber = stale/unknown terms, red = spent group or account cap only". This module turns what
the registry (config/instruments.yaml) and the engines publish into those shapes; it never decides anything.

What the engines publish for it (state/engine_<account>.json, written by lanes L2/L3; every key optional, absent on a
single-symbol engine, read tolerantly here, a malformed entry dropped and never guessed):
* `symbols`: {broker or instrument symbol: {"state": InstrumentState, "text": str, "clears_at": ISO UTC | null}}
* `groups`: {group: {"notional": float, "cap": float | null, "lots_by_symbol": {symbol: lots}, "open_n": int}}
* `heat_used`, `heat_limit`: the loss at every stop on every symbol, and its limit (account currency)
* `symbols[<symbol>].price` (its mid now) and `.lot_value` (account currency per 1.0 of price per lot, the broker's
  `loss_per_lot` over a distance of 1): a non-gold position's P&L now. Without them that P&L is None, never priced at
  the account's gold `price` (that would show a US500 trade moved by gold)
Trade records (orders, positions, exploration, cost probes) carry `symbol` (their broker symbol); a record without one
is the account's gold, as every record before the multi-instrument demo is.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, get_args

import pandas as pd

import goldbot.instruments as registry_file
from goldbot.api.schema import (
    EntrySymbol,
    GroupExposure,
    InstrumentGroup,
    InstrumentState,
    InstrumentView,
    SymbolDay,
    TradeRow,
)
from goldbot.instruments import Instruments, InstrumentSpec, load_instruments

log = logging.getLogger(__name__)

GOLD = "XAUUSD"
GROUP_ORDER: tuple[InstrumentGroup, ...] = get_args(InstrumentGroup)
_STATES: frozenset[str] = frozenset(get_args(InstrumentState))
# the owner's words and the unit a price is quoted in (display only; the terms are the registry's). An FX rate needs
# no unit; an index is in points; anything not listed shows its profit currency
LABELS: dict[str, tuple[str, str]] = {
    "XAUUSD": ("Gold", "USD/oz"), "XAGUSD": ("Silver", "USD/oz"), "XPTUSD": ("Platinum", "USD/oz"),
    "XAUEUR": ("Gold in euros", "EUR/oz"), "XAUAUD": ("Gold in Australian dollars", "AUD/oz"),
    "US500": ("S&P 500", "pts"), "USTEC": ("Nasdaq 100", "pts"), "DE40": ("DAX 40", "pts"),
    "UK100": ("FTSE 100", "pts"), "JP225": ("Nikkei 225", "pts"),
    "EURUSD": ("Euro / dollar", ""), "USDJPY": ("Dollar / yen", ""), "GBPUSD": ("Pound / dollar", ""),
    "AUDUSD": ("Aussie / dollar", ""), "USDCAD": ("Dollar / Canadian", ""), "USDCHF": ("Dollar / franc", ""),
    "NZDUSD": ("Kiwi / dollar", ""),
    "XTIUSD": ("WTI crude", "USD/bbl"), "XBRUSD": ("Brent crude", "USD/bbl"), "XNGUSD": ("Natural gas", "USD/MMBtu"),
    "UST10Y": ("US 10-year note", ""), "BUND10Y": ("German 10-year Bund", ""), "COPPER": ("Copper", "USD/lb"),
    "WHEAT": ("Wheat", ""), "SOYBEAN": ("Soybeans", ""), "SUGAR": ("Sugar", ""), "COFFEE": ("Coffee", ""),
    # owner decision 2026-11-04: the 10 added instruments
    "GBPJPY": ("Pound / yen", ""), "EURGBP": ("Euro / pound", ""), "EURJPY": ("Euro / yen", ""),
    "AUDJPY": ("Aussie / yen", ""), "EURAUD": ("Euro / Aussie", ""), "GBPAUD": ("Pound / Aussie", ""),
    "BTCUSD": ("Bitcoin", "USD"), "XAUJPY": ("Gold in yen", "JPY/oz"), "USINDEX": ("US dollar index", "pts"),
    "US30": ("Dow Jones 30", "pts"),
}


# config/instruments.yaml as last parsed: {path: ((mtime_ns, size) or None when missing, registry or None)}
_REGISTRY: dict[Path, tuple[tuple[int, int] | None, Instruments | None]] = {}


def _registry(reg: Instruments | None) -> Instruments | None:
    """The registry given, else config/instruments.yaml parsed once per version of the file (its mtime and size).
    Live 2026-11-04 (brain, py-spy): parsed once per ledger row, the api and telegram services each spent 96-97% of a
    core in yaml, starving the engines on the 2-core host. An edit is read on the next call, as before; an unreadable
    or missing file is logged once per version (missing is one) and every view falls back to gold terms."""
    if reg is not None:
        return reg
    path = Path(registry_file.DEFAULT_INSTRUMENTS)
    try:
        st = path.stat()
        version: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
    except OSError:
        version = None
    hit = _REGISTRY.get(path)
    if hit is not None and hit[0] == version:
        return hit[1]
    got: Instruments | None
    try:
        got = load_instruments(path)
    except Exception:                                   # noqa: BLE001 - a bad registry: gold-only views, logged
        log.exception("instruments.yaml unreadable: the dashboard shows gold terms only")
        got = None
    _REGISTRY[path] = (version, got)
    return got


def instrument_key(raw: Any, reg: Instruments | None = None) -> str:
    """The instrument key of a record's symbol: none (every record before the multi-instrument demo) is gold; a
    registry key is itself; a broker symbol (XAUUSD+, COPPER-C) maps to its key; anything else is kept as given."""
    if not isinstance(raw, str) or not raw.strip():
        return GOLD
    sym = raw.strip()
    r = _registry(reg)
    if r is None or sym in r.instruments:
        return sym
    return next((k for k, s in r.instruments.items() if sym in s.broker_symbols.values()), sym)


def group_of(key: str, reg: Instruments | None = None) -> InstrumentGroup | None:
    r = _registry(reg)
    spec = r.instruments.get(key) if r is not None else None
    return spec.correlation_group if spec is not None else None


def _label(spec: InstrumentSpec) -> tuple[str, str]:
    return LABELS.get(spec.key, (spec.key, spec.currency_profit))


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _when(v: Any) -> datetime | None:
    try:
        ts = pd.Timestamp(v) if isinstance(v, str) and v else None
    except (TypeError, ValueError):
        return None
    if ts is None or pd.isna(ts):
        return None
    return (ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")).to_pydatetime()


# ------------------------------------------------------------------------------------------------ engine reports
def symbol_quote(e: dict[str, Any], key: str) -> tuple[float | None, float | None]:
    """(mid price, account currency per 1.0 of price per lot) of a non-gold instrument from the engine's `symbols`
    report; (None, None) when it does not publish both, finite and the value > 0."""
    raw = e.get("symbols")
    if not isinstance(raw, dict):
        return None, None
    for sym, v in raw.items():
        if isinstance(v, dict) and instrument_key(sym) == key:
            price, value = _num(v.get("price")), _num(v.get("lot_value"))
            if price is not None and value is not None and value > 0:
                return price, value
            return None, None
    return None, None


def entry_symbols(e: dict[str, Any], reg: Instruments | None = None) -> list[EntrySymbol]:
    """EntryStatus.symbols from the engine's `symbols` report, by instrument key; [] on a single-symbol engine."""
    raw = e.get("symbols")
    if not isinstance(raw, dict):
        return []
    out = []
    for sym, v in raw.items():
        if not isinstance(v, dict) or v.get("state") not in _STATES:
            continue
        text = v.get("text")
        out.append(EntrySymbol(symbol=instrument_key(sym, reg), state=v["state"],
                               text=text if isinstance(text, str) and text else str(v["state"]).replace("_", " "),
                               clears_at=_when(v.get("clears_at"))))
    return sorted(out, key=lambda s: s.symbol)


def group_exposure(e: dict[str, Any], reg: Instruments | None = None
                   ) -> tuple[list[GroupExposure], float | None, float | None]:
    """(groups in the fixed order, zero included, heat used, heat limit) from the engine's report; ([], None, None)
    when it publishes no groups (a single-symbol engine)."""
    raw = e.get("groups")
    heat = (_num(e.get("heat_used")), _num(e.get("heat_limit")))
    if not isinstance(raw, dict):
        return [], *heat
    out = []
    for g in GROUP_ORDER:
        v = raw.get(g)
        v = v if isinstance(v, dict) else {}
        lots_raw = v.get("lots_by_symbol")
        lots = {instrument_key(s, reg): lot for s, raw_n in (lots_raw.items() if isinstance(lots_raw, dict) else ())
                if (lot := _num(raw_n)) is not None and lot > 0}
        n = v.get("open_n")
        out.append(GroupExposure(group=g, notional=_num(v.get("notional")) or 0.0, cap=_num(v.get("cap")),
                                 lots_by_symbol=lots, open_n=n if isinstance(n, int) and n >= 0 else 0))
    return out, *heat


# ------------------------------------------------------------------------------------------------ GET /api/instruments
def instruments(accounts: Iterable[Any], engines: Sequence[dict[str, Any]], reg: Instruments | None = None,
                symbols_of: Any = None) -> list[InstrumentView]:
    """One row per instrument per enabled account: its own symbol first, then its list
    (`market_watch.account_symbols`), with the engine's state for it ("unknown" while the engine does not report
    the symbol; "can_enter" for a single-symbol engine's own gold is never assumed either)."""
    from goldbot.execution.market_watch import account_symbols
    from goldbot.telegram.entry_text import account_name
    r = _registry(reg)
    by_acct = {str(e.get("account_id")): e for e in engines if isinstance(e, dict)}
    out: list[InstrumentView] = []
    for acc in accounts:
        if not getattr(acc, "enabled", True):
            continue
        try:
            syms = symbols_of(acc) if symbols_of is not None else account_symbols(acc, r)
        except Exception:                                # noqa: BLE001 - one account's bad list: its gold only
            log.exception("%s: symbol list unreadable", acc.account_id)
            syms = (acc.symbol,)
        states = {s.symbol: s.state for s in entry_symbols(by_acct.get(acc.account_id, {}), r)}
        for sym in syms:
            key = instrument_key(sym, r)
            spec = r.instruments.get(key) if r is not None else None
            if spec is None:
                continue
            label, unit = _label(spec)
            out.append(InstrumentView(symbol=key, label=label, account=account_name(acc.account_id),
                                      group=spec.correlation_group, digits=spec.digits, unit=unit,
                                      quote_ccy=spec.currency_profit, state=states.get(key, "unknown")))
    return out


# ------------------------------------------------------------------------------------------------ Today by instrument
def by_symbol(rows: Iterable[TradeRow], account: str, day_start: pd.Timestamp) -> list[SymbolDay]:
    """TodayAccount.by_symbol: per instrument, the account's trades closed since `day_start` (net) and open now
    (P&L now), from the trade ledger's rows (shadow excluded: paper, no money). Never summed across accounts. A row
    whose money is unknown makes that figure None for its symbol, never a smaller number."""
    acc: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.account != account or r.kind == "shadow":
            continue
        if r.status == "closed" and (r.exit_utc is None or pd.Timestamp(r.exit_utc) < day_start):
            continue
        a = acc.setdefault(r.symbol, {"closed": 0.0, "open": 0.0, "cn": 0, "on": 0, "cu": False, "ou": False})
        if r.status == "closed":
            a["cn"] += 1
            if r.pnl is None:
                a["cu"] = True
            else:
                a["closed"] += r.pnl
        else:
            a["on"] += 1
            if r.pnl_now is None:
                a["ou"] = True
            else:
                a["open"] += r.pnl_now
    out = []
    for sym, a in acc.items():
        closed = None if a["cu"] else round(a["closed"], 2)
        opened = None if a["ou"] else round(a["open"], 2)
        out.append(SymbolDay(symbol=sym, day_pnl=None if closed is None or opened is None else round(closed + opened, 2),
                             closed_pnl=closed, open_pnl=opened, closed_n=a["cn"], open_n=a["on"]))
    return sorted(out, key=lambda d: (-(abs(d.day_pnl) if d.day_pnl is not None else -1.0), d.symbol))
