"""The symbol check of an account's terminal (ADR 0062), read-only, before its engine is enabled.

    python -m goldbot.ops.symbol_check vantage-247   # on the brain through the account's bridge (or on the box)

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
from dataclasses import dataclass, field
from typing import Any

from goldbot.instruments import InstrumentSpec, load_instruments
from goldbot.ops import accounts


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


@dataclass
class SymbolReport:
    """One instrument's check: `problems` stop it being enabled; `notes` are for the owner's record."""
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _terms(sym: str, spec: InstrumentSpec, info: Any) -> list[str]:
    out = []
    pairs: list[tuple[str, Any, Any]] = [
        ("contract size", info.contract_size, spec.contract_size), ("digits", info.digits, spec.digits),
        ("minimum lot", info.volume_min, spec.min_lot), ("lot step", info.volume_step, spec.lot_step)]
    for what, got, want in pairs:
        if abs(float(got) - float(want)) > 1e-9:
            out.append(f"{sym}: {what} {got:g} on the terminal, {want:g} in config/instruments.yaml")
    for what, got, want in (("profit currency", getattr(info, "currency_profit", None), spec.currency_profit),
                            ("triple-swap day", getattr(info, "swap_rollover3days", None), spec.swap_rollover3days)):
        if got is not None and got != want:
            out.append(f"{sym}: {what} {got} on the terminal, {want} in config/instruments.yaml")
    return out


def check_instruments(acc: accounts.Account, broker: Any) -> dict[str, SymbolReport]:
    """Every instrument of the account's list but its own symbol (`check_symbol` checks that), by broker symbol."""
    from goldbot.data.calendar import profile_disagreement, session_profile
    from goldbot.execution.market_watch import account_symbols
    reg = load_instruments()
    out: dict[str, SymbolReport] = {}
    for sym in account_symbols(acc, reg)[1:]:
        rep = out[sym] = SymbolReport()
        spec = reg.by_broker_symbol(str(acc.broker), sym)
        if spec is None:
            rep.problems.append(f"{sym}: not in config/instruments.yaml for broker {acc.broker}")
            continue
        listing = getattr(broker, "symbols_get", None)
        try:
            names = listing(sym) if listing is not None else None
        except Exception:                     # noqa: BLE001 - an older bridge: symbol_info alone
            names = None
        if names is not None and sym not in names:
            rep.problems.append(f"{sym} is not listed by the terminal of {acc.account_id}")
            continue
        try:
            info = broker.symbol_info(sym)
        except Exception as exc:              # noqa: BLE001 - symbol_info None raises in the adapter
            rep.problems.append(f"{sym}: symbol_info failed ({type(exc).__name__})")
            continue
        rep.problems += _terms(sym, spec, info)
        if not info.trade_allowed:
            rep.problems.append(f"{sym}: trading disabled on the terminal")
        try:
            reported = broker.trading_sessions(sym)
            rep.notes += profile_disagreement(session_profile(spec.session_profile, acc.server_tz), reported)
        except Exception as exc:              # noqa: BLE001 - reported, never a problem: the engine keeps the profile
            rep.notes.append(f"{sym}: sessions not read ({type(exc).__name__}: {exc}); the "
                             f"{spec.session_profile} profile applies alone")
    return out


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
    try:
        problems = check_symbol(acc, broker)
        info = broker.symbol_info(acc.symbol) if not problems else None
        others = check_instruments(acc, broker)
    finally:
        if hasattr(broker, "shutdown"):
            broker.shutdown()
    if info is not None:
        print(f"{acc.symbol}: contract {info.contract_size:g}, lots {info.volume_min:g}..{info.volume_max:g} step "
              f"{info.volume_step:g}, digits {info.digits}, swap mode {info.swap_mode} long {info.swap_long} short "
              f"{info.swap_short}, margin initial {info.margin_initial} maintenance {info.margin_maintenance}")
    for sym, rep in others.items():
        print(f"{sym}: {'ok' if not rep.problems else f'{len(rep.problems)} problem(s)'}")
        for n in rep.notes:
            print(f"  note: {n}")
        problems += rep.problems
    for p in problems:
        print(f"FAIL: {p}")
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
