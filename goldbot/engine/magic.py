"""Every magic number an engine puts on an order, in one place (moved out of `Engine._magic` and engine/cost_probe.py,
ADR 0023 runner split).

An account's goldbot magics are magic_base .. magic_base + 99 (`Engine._own`, `GuardedBroker`, the broker ledger). A
strategy family's magic is magic_base + crc32(family) % 100, "crc32 is stable across restarts (str hash() is salted),
so positions reconcile to a family". The offsets below are reserved for orders that are not a family's, so a position
found with no order row is adopted for what it is (never as a live agent's trade); tests guard that no family's crc32
lands on one of them:

* 95, exploration arms (goldbot/engine/exploration.py);
* 96, the scheduled cost probe (the measured-cost protocol's M1 arm);
* 97, a mirror cost probe (ADR 0037);
* 99, the bridge smoke (ADR 0021; `goldbot.execution.broker.SMOKE_MAGIC_OFFSET`, kept there for the bridge).
"""
from __future__ import annotations

import zlib

from goldbot.execution.broker import SMOKE_MAGIC_OFFSET

EXPLORATION_OFFSET = 95
SCHEDULED_PROBE_OFFSET = 96
PROBE_OFFSET = 97
RESERVED = (EXPLORATION_OFFSET, SCHEDULED_PROBE_OFFSET, PROBE_OFFSET, SMOKE_MAGIC_OFFSET)


def family(magic_base: int, name: str) -> int:
    """Per-family magic: crc32 is stable across restarts (str hash() is salted), so positions reconcile to a family."""
    return magic_base + zlib.crc32(name.encode()) % 100


def exploration(magic_base: int) -> int:
    return magic_base + EXPLORATION_OFFSET


def is_exploration(m: int, magic_base: int) -> bool:
    return int(m) == magic_base + EXPLORATION_OFFSET
