"""Plan W1-1 S7 (docs/proposals/2026-10-w1-1-bridge-smoke-plan.md): the bridge smoke's magic is magic_base + 99
(`SMOKE_MAGIC_OFFSET`), so no specialist family may hash to it, or the engine would adopt the smoke's position as its
own (and GuardedBroker would refuse that family's orders off demo)."""
from __future__ import annotations

from goldbot.engine import EngineConfig, magic
from goldbot.execution.broker import SMOKE_MAGIC_OFFSET
from goldbot.specialists import SPECIALISTS


def test_no_family_magic_equals_the_smoke_offset():
    cfg = EngineConfig(account_id="icm-demo", broker_name="icm", magic_base=260100)
    magics = {family: magic.family(cfg.magic_base, family) for family in SPECIALISTS}
    assert magics and all(260100 <= m < 260100 + SMOKE_MAGIC_OFFSET for m in magics.values()), magics


# ------------------------------------------------------------------ multi-instrument magic blocks (lane L3)
def test_xauusd_block_zero_keeps_the_accounts_legacy_magic_base():
    """Plan: "XAUUSD paths byte-identical": gold's positions reconcile under today's magic_base after the release."""
    assert magic.book_base(260100, 0) == 260100
    assert magic.book_base(260500, 0) == 260500


def test_a_non_gold_block_is_3m_plus_account_idx_times_10k_plus_block_times_100():
    """Plan: "magic = 3_000_000 + account_idx*10_000 + magic_block*100 + offset"; account_idx is the account's slot in
    the 2601xx..2609xx magic_base series (icm-probe 260500 -> 5)."""
    assert magic.account_idx(260500) == 5
    assert magic.book_base(260500, 1) == 3_050_100
    assert magic.book_base(260200, 26) == 3_022_600
    assert magic.exploration(magic.book_base(260500, 1)) == 3_050_195


def test_a_magic_base_off_the_series_refuses_non_gold_blocks():
    import pytest
    for bad in (260150, 259900, 270000, 3_000_000):
        with pytest.raises(ValueError):
            magic.book_base(bad, 1)
    assert magic.book_base(260150, 0) == 260150      # gold never needs the series


def test_magic_blocks_of_every_configured_account_never_collide():
    """Every (account, instrument) range of 100 magics is disjoint from every other, gold's legacy ranges included."""
    from goldbot.instruments import load_instruments
    from goldbot.ops.accounts import load_accounts
    blocks = [s.magic_block for s in load_instruments().instruments.values()]
    ranges = []
    for acc in load_accounts().values():
        for b in blocks:
            base = magic.book_base(acc.magic_base, b)
            ranges.append((base, base + magic.BLOCK_SPAN))
    ranges.sort()
    assert all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:])), "magic ranges overlap"


def test_owns_accepts_the_set_of_an_accounts_blocks():
    bases = magic.book_bases(260500, (0, 1, 7))
    assert bases == frozenset({260500, 3_050_100, 3_050_700})
    assert magic.owns(260595, bases) and magic.owns(3_050_799, bases) and magic.owns(3_050_100, bases)
    assert not magic.owns(3_050_200, bases) and not magic.owns(260600, bases) and not magic.owns(260499, bases)
