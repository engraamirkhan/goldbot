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
