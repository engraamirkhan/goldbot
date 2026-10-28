"""The one interface this slice still codes against that another slice has not delivered: `MaskProvider`.

REPLACE AT INTEGRATION (ADR 0058 phase 1, backlog 140): `autodisc.primitives` (leaf -> point-in-time boolean over
`goldbot/features`) implements it. Everything else (spec, ledger, windows, outcomes, evaluate) is the real module.
G1 specs name their family in `spec.family`; the provider resolves it to the family's own entry bars and side.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from goldbot.research.autodisc.spec import Predicate

BoolArr = NDArray[np.bool_]
SideArr = NDArray[np.int8]


class MaskProvider(Protocol):
    """Point-in-time masks over ONE outcome tensor's bars (same length as `tensor.ts_utc`)."""

    def family_entries(self, family: str, timeframe: str) -> tuple[BoolArr, SideArr]:
        """(entry mask, side per bar: +1 long / -1 short / 0 none) of an existing family's rule."""
        ...

    def predicate_mask(self, pred: Predicate, timeframe: str) -> BoolArr: ...
