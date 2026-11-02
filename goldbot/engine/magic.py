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

Multi-instrument demo (plan 2026-11-02, lane L3): "magic = 3_000_000 + account_idx*10_000 + magic_block*100 +
offset (magic_block per instrument in config)". Each instrument's book (engine/books.py) has its own range of 100,
`book_base`, and the offsets above inside it. XAUUSD is block 0 and keeps the account's own magic_base, so every gold
position open today reconciles unchanged ("XAUUSD paths byte-identical"). account_idx is the account's slot in the
magic_base series every configured account uses (260_000 + idx*100: icm-demo 260100 -> 1, icm-probe 260500 -> 5), so
the non-gold ranges (3_0xx_xxx) never meet a gold range (2601xx..2609xx) nor another account's. Never renumber a block
once its instrument has traded: open positions are reconciled by it.
"""
from __future__ import annotations

import zlib
from collections.abc import Iterable

from goldbot.execution.broker import SMOKE_MAGIC_OFFSET

EXPLORATION_OFFSET = 95
SCHEDULED_PROBE_OFFSET = 96
PROBE_OFFSET = 97
RESERVED = (EXPLORATION_OFFSET, SCHEDULED_PROBE_OFFSET, PROBE_OFFSET, SMOKE_MAGIC_OFFSET)

MAGIC_ROOT = 3_000_000          # plan: the non-gold books' root
ACCOUNT_SPAN = 10_000           # one account's non-gold blocks: account_idx * ACCOUNT_SPAN
BLOCK_SPAN = 100                # one instrument's magics: offsets 0..99
LEGACY_ROOT = 260_000           # today's magic_base series: 260_000 + account_idx * 100
MAX_ACCOUNT_IDX = 99


def family(magic_base: int, name: str) -> int:
    """Per-family magic: crc32 is stable across restarts (str hash() is salted), so positions reconcile to a family."""
    return magic_base + zlib.crc32(name.encode()) % 100


def exploration(magic_base: int) -> int:
    return magic_base + EXPLORATION_OFFSET


def is_exploration(m: int, magic_base: int) -> bool:
    return int(m) == magic_base + EXPLORATION_OFFSET


def account_idx(magic_base: int) -> int:
    """The account's slot in the magic_base series (260_000 + idx*100, idx 1..99); ValueError off the series, so a
    non-gold block is never numbered from a base that could collide (fail closed)."""
    idx, rest = divmod(int(magic_base) - LEGACY_ROOT, BLOCK_SPAN)
    if rest or not 1 <= idx <= MAX_ACCOUNT_IDX:
        raise ValueError(f"magic_base {magic_base} is not 260_000 + idx*100 (idx 1..{MAX_ACCOUNT_IDX}): no instrument "
                         "block can be numbered from it")
    return idx


def book_base(magic_base: int, block: int) -> int:
    """The first magic of instrument `block` on the account whose (gold) magic_base is `magic_base`: block 0 (XAUUSD)
    is magic_base itself; any other block 3_000_000 + account_idx*10_000 + block*100."""
    if not 0 <= int(block) < ACCOUNT_SPAN // BLOCK_SPAN:
        raise ValueError(f"magic block {block} outside 0..{ACCOUNT_SPAN // BLOCK_SPAN - 1}")
    if block == 0:
        return int(magic_base)
    return MAGIC_ROOT + account_idx(magic_base) * ACCOUNT_SPAN + int(block) * BLOCK_SPAN


def book_bases(magic_base: int, blocks: Iterable[int]) -> frozenset[int]:
    """Every book base of an account: the set reconciliation, the wind-down check and the bridge guard accept."""
    return frozenset(book_base(magic_base, b) for b in blocks)


def owns(magic: int, bases: Iterable[int]) -> bool:
    """`magic` lies in one of the books' ranges (base .. base + 99)."""
    m = int(magic)
    return any(b <= m < b + BLOCK_SPAN for b in bases)
