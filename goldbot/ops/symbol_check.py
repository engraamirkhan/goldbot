"""The symbol check of an account's terminal (ADR 0062), read-only, before its engine is enabled.

    python -m goldbot.ops.symbol_check vantage-247   # on the brain through the account's bridge (or on the box)

Design rule: "a new account trades only a symbol its terminal lists, with the contract size the account declares and
trading enabled". The engine sizes from the terminal's own contract size (never the declared one), so a mismatch is a
configuration error to fix before `enabled: true`, not something to trade through. It asks the terminal only
`symbols_get` (when it has it: an older bridge does not) and `symbol_info`; it prints the swap and margin terms for the
owner's record. It never places, modifies or closes anything.
"""
from __future__ import annotations

import sys
from typing import Any

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
    finally:
        if hasattr(broker, "shutdown"):
            broker.shutdown()
    if info is not None:
        print(f"{acc.symbol}: contract {info.contract_size:g}, lots {info.volume_min:g}..{info.volume_max:g} step "
              f"{info.volume_step:g}, digits {info.digits}, swap mode {info.swap_mode} long {info.swap_long} short "
              f"{info.swap_short}, margin initial {info.margin_initial} maintenance {info.margin_maintenance}")
    for p in problems:
        print(f"FAIL: {p}")
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
