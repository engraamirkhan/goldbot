"""The symbols one MT5 bridge serves: the account's own symbol plus its enabled instruments (multi-instrument demo).

Design rules (multi-instrument plan, adopted 2026-11-02): "bridge selects the account's symbol list"; "real-money/live
and observe accounts refuse any non-XAUUSD instrument". The list is the account's `symbol` (its gold, selected and
required at the adapter's start, exactly as before) followed by every instrument the account names in `instruments`
(instruments.yaml keys; lane L1 adds the field to accounts.yaml) that the registry enables and maps to this account's
broker. A live or observe-only account gets its own symbol only, whatever it names, so a real terminal's Market Watch
never grows. While no account names instruments this is `(acc.symbol,)`: today's bridges are unchanged.

MAX_BRIDGE_SYMBOLS bounds one bridge: Market Watch on the 1 GB MT5 box (icm-probe, 19 instruments + gold; RUNBOOK
"Max bars in chart 100k"), one ticks_many request, and the queue it is sized for (bridge_queue.MAX_QUEUED).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from goldbot.instruments import Instruments, load_instruments

log = logging.getLogger("goldbot.bridge")

MAX_BRIDGE_SYMBOLS = 32
# a broker symbol name: XAUUSD, XAUUSD+, COPPER-C, USNote10Y, ... (never a leading "_": the wire's __ts__/__frame__ keys)
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+#-]{0,31}$")


def account_symbols(acc: Any, instruments: Instruments | None = None) -> tuple[str, ...]:
    """The broker symbols the account's bridge selects and serves, its own symbol first (see the module docstring)."""
    own = str(acc.symbol)
    if getattr(acc, "is_live", False) or getattr(acc, "is_observe_only", False):
        return (own,)
    wanted = tuple(getattr(acc, "instruments", None) or ())
    if not wanted:
        return (own,)
    reg = instruments if instruments is not None else load_instruments()
    out = [own]
    for key in wanted:
        spec = reg.instruments.get(key)
        sym = spec.broker_symbols.get(str(acc.broker)) if spec is not None else None
        if spec is None or sym is None or not spec.enabled:
            log.warning("%s: instrument %s not served (unknown, not on broker %s, or disabled)", acc.account_id, key,
                        acc.broker)
            continue
        if sym not in out:
            out.append(sym)
    if len(out) > MAX_BRIDGE_SYMBOLS:
        raise ValueError(f"{acc.account_id}: {len(out)} symbols, a bridge serves at most {MAX_BRIDGE_SYMBOLS}")
    return tuple(out)


def check_symbols(symbols: Any) -> list[str]:
    """`symbols` as a list of names, or ValueError: a list of at most MAX_BRIDGE_SYMBOLS non-empty strings."""
    if isinstance(symbols, (str, bytes)) or not isinstance(symbols, (list, tuple)):
        raise ValueError("ticks_many: symbols must be a list of names")
    names = list(symbols)
    if len(names) > MAX_BRIDGE_SYMBOLS:
        raise ValueError(f"ticks_many: {len(names)} symbols, at most {MAX_BRIDGE_SYMBOLS} per call")
    if not all(isinstance(s, str) and _SYMBOL.match(s) for s in names):
        raise ValueError("ticks_many: every symbol is a broker symbol name (letters, digits, . _ + # -, at most 32)")
    return names
