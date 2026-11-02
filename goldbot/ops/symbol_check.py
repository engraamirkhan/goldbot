"""The symbol check of an account's terminal (ADR 0062), read-only, before its engine is enabled.

    python -m goldbot.ops.symbol_check vantage-247   # on the brain through the account's bridge (or on the box)
    python -m goldbot.ops.symbol_check icm-probe     # its gold symbol, then every instrument it lists

Multi-instrument demo (plan 2026-11-02, lane L1: "symbol_check per symbol"): every instrument the account lists
(accounts.yaml `instruments`) is checked against config/instruments.yaml the same way: listed, trading enabled, and the
declared contract size, digits, point, minimum lot, lot step, profit currency and triple-swap day (the last two only
when the terminal reports them: unknown is not a mismatch). Each line it prints is the terminal's own terms in the
registry's field names, so a `verify: true` entry is confirmed (or corrected) from it and only then set `verify: false`.

Design rule: "a new account trades only a symbol its terminal lists, with the contract size the account declares and
trading enabled". The engine sizes from the terminal's own contract size (never the declared one), so a mismatch is a
configuration error to fix before `enabled: true`, not something to trade through. It asks the terminal only
`symbols_get` (when it has it: an older bridge does not) and `symbol_info`; it prints the swap and margin terms for the
owner's record. It never places, modifies or closes anything.

Per symbol (multi-instrument demo, lane L4; RUNBOOK "symbol_check per symbol"): every other instrument of the account's
list (market_watch.account_symbols) is checked too: listed by the terminal, symbol_info readable, trading enabled, and
its contract size, digits, minimum lot, lot step, profit currency and triple-swap day equal to config/instruments.yaml
(a difference is a problem: the registry's terms are what the RiskGate sizes and rounds with). Its hours as the broker
quotes them (MT5Broker.trading_sessions) against the instrument's session profile (calendar.profile_disagreement) are
NOTES, never problems: the engine trades only where both are open.
"""
from __future__ import annotations

import sys
from typing import Any

from goldbot.execution.broker import SymbolInfo
from goldbot.instruments import InstrumentSpec, load_instruments, registry
from goldbot.ops import accounts

TERM_TOLERANCE = 1e-9


def check_symbol(acc: accounts.Account, broker: Any) -> list[str]:
    """Why `acc.symbol` cannot be traded on `broker`'s terminal as configured; empty when it can."""
    listing = getattr(broker, "symbols_get", None)
    if listing is not None:
        try:
            names = listing(acc.symbol)
        except Exception as exc:              # noqa: BLE001 - an older bridge (HTTP 403) or a terminal error
            names = None
            print(f"note: symbols_get unavailable ({type(exc).__name__}); checking symbol_info alone", file=sys.stderr)
        if names is not None and acc.symbol not in names:
            return [f"{acc.symbol} is not listed by the terminal of {acc.account_id} (wrong server or account type?)"]
    try:
        info = broker.symbol_info(acc.symbol)
    except Exception as exc:                  # noqa: BLE001 - symbol_info None raises in the adapter
        return [f"{acc.symbol}: symbol_info failed ({type(exc).__name__})"]
    problems: list[str] = []
    if acc.contract_size is not None and abs(float(info.contract_size) - acc.contract_size) > 1e-9:
        problems.append(f"{acc.symbol}: contract size {info.contract_size:g} on the terminal, {acc.contract_size:g} "
                        f"declared in config/accounts.yaml")
    if not info.trade_allowed:
        problems.append(f"{acc.symbol}: trading disabled on the terminal")
    return problems


def _listed(broker: Any) -> set[str] | None:
    """The terminal's symbol names, None when it cannot say (an older bridge, a terminal error)."""
    listing = getattr(broker, "symbols_get", None)
    if listing is None:
        return None
    try:
        return set(listing("*"))
    except Exception as exc:                  # noqa: BLE001 - an older bridge (HTTP 403) or a terminal error
        print(f"note: symbols_get unavailable ({type(exc).__name__}); checking symbol_info alone", file=sys.stderr)
        return None


def instrument_problems(spec: InstrumentSpec, name: str, info: SymbolInfo) -> list[str]:
    """Why the terminal's `info` for broker symbol `name` is not the registry's `spec`; empty when it is."""
    out: list[str] = []
    for what, have, want in (("contract size", info.contract_size, spec.contract_size),
                             ("point", info.point, spec.point), ("minimum lot", info.volume_min, spec.min_lot),
                             ("lot step", info.volume_step, spec.lot_step)):
        if abs(float(have) - float(want)) > TERM_TOLERANCE * max(1.0, abs(float(want))):
            out.append(f"{name}: {what} {float(have):g} on the terminal, {float(want):g} in config/instruments.yaml")
    if int(info.digits) != spec.digits:
        out.append(f"{name}: digits {info.digits} on the terminal, {spec.digits} in config/instruments.yaml")
    if info.currency_profit is not None and info.currency_profit.upper() != spec.currency_profit.upper():
        out.append(f"{name}: profit currency {info.currency_profit} on the terminal, {spec.currency_profit} in "
                   f"config/instruments.yaml")
    if info.swap_rollover3days is not None and int(info.swap_rollover3days) != spec.swap_rollover3days:
        out.append(f"{name}: triple swap day {info.swap_rollover3days} on the terminal, {spec.swap_rollover3days} in "
                   f"config/instruments.yaml (MT5 numbering, 0 = Sunday)")
    if not info.trade_allowed:
        out.append(f"{name}: trading disabled on the terminal")
    return out


class SymbolReport(list):
    """One instrument's check (lanes L1 and L4 merged): the list itself is its problems, which stop it being enabled
    (`problems` names it too); `notes` are for the owner's record (hours that differ from its session profile)."""

    def __init__(self, problems: list[str] | None = None) -> None:
        super().__init__(problems or [])
        self.notes: list[str] = []

    @property
    def problems(self) -> list[str]:
        return self


def check_instruments(acc: accounts.Account, broker: Any) -> dict[str, SymbolReport]:
    """Per instrument the account lists (accounts.yaml `instruments`, keys of config/instruments.yaml): why it cannot
    be traded on `broker`'s terminal as declared (an empty report when it can), and how its broker hours differ from
    its session profile (notes, never problems: the engine keeps the profile for entries). Reads `symbols_get` once,
    then `symbol_info` and `trading_sessions` per symbol; never places, modifies or closes anything."""
    if not acc.instruments:
        return {}
    from goldbot.data.calendar import profile_disagreement, session_profile
    reg = load_instruments()
    names = _listed(broker)
    out: dict[str, SymbolReport] = {}
    for key in acc.instruments:
        spec = reg.get(key)
        name = spec.broker_symbols[acc.broker]
        rep = out[key] = SymbolReport()
        if names is not None and name not in names:
            rep.append(f"{name} is not listed by the terminal of {acc.account_id}")
            continue
        try:
            info = broker.symbol_info(name)
        except Exception as exc:              # noqa: BLE001 - symbol_info None raises in the adapter
            rep.append(f"{name}: symbol_info failed ({type(exc).__name__})")
            continue
        rep += instrument_problems(spec, name, info)
        sessions = getattr(broker, "trading_sessions", None)
        if sessions is None:
            continue
        try:
            rep.notes += profile_disagreement(session_profile(spec.session_profile, acc.server_tz), sessions(name))
        except Exception as exc:              # noqa: BLE001 - reported, never a problem: the engine keeps the profile
            rep.notes.append(f"{name}: sessions not read ({type(exc).__name__}: {exc}); the "
                             f"{spec.session_profile} profile applies alone")
    return out


def terms_line(spec: InstrumentSpec, info: SymbolInfo) -> str:
    """The terminal's terms in config/instruments.yaml's field names, for the owner's record and the registry edit."""
    cp = info.currency_profit if info.currency_profit is not None else "?"
    sw = info.swap_rollover3days if info.swap_rollover3days is not None else "?"
    return (f"{spec.key} ({info.name}): contract_size: {float(info.contract_size):g}, digits: {info.digits}, point: "
            f"{float(info.point):g}, min_lot: {float(info.volume_min):g}, lot_step: {float(info.volume_step):g}, "
            f"currency_profit: {cp}, swap_rollover3days: {sw}; max lot {float(info.volume_max):g}, swap mode "
            f"{info.swap_mode} long {info.swap_long} short {info.swap_short}, margin initial {info.margin_initial}")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m goldbot.ops.symbol_check <account>")
        return 2
    # the scheduler's read-only broker: the account's bridge on the brain, the terminal's saved login on the box
    from goldbot.ops.services.scheduler import _job_broker
    acc = accounts.load_accounts()[argv[0]]
    broker = _job_broker(acc)
    if broker is None:
        print(f"no bridge configured for {acc.account_id} here (bridge-use) and not on the MT5 box")
        return 2
    terms: list[str] = []
    per: dict[str, SymbolReport] = {}
    try:
        problems = check_symbol(acc, broker)
        info = broker.symbol_info(acc.symbol) if not problems else None
        per = check_instruments(acc, broker)
        reg = registry()
        for key, found in per.items():
            if not found:
                spec = reg.get(key)
                terms.append(terms_line(spec, broker.symbol_info(spec.broker_symbols[acc.broker])))
            problems += found
    finally:
        if hasattr(broker, "shutdown"):
            broker.shutdown()
    if info is not None:
        print(f"{acc.symbol}: contract {info.contract_size:g}, lots {info.volume_min:g}..{info.volume_max:g} step "
              f"{info.volume_step:g}, digits {info.digits}, swap mode {info.swap_mode} long {info.swap_long} short "
              f"{info.swap_short}, margin initial {info.margin_initial} maintenance {info.margin_maintenance}")
    for line in terms:
        print(line)
    for key, rep in per.items():
        for n in rep.notes:
            print(f"  note ({key}): {n}")
    for p in problems:
        print(f"FAIL: {p}")
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
