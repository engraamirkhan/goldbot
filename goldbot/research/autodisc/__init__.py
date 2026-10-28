"""Autonomous strategy discovery, phase 1 (ADR 0058, backlog 140).

Public interface of the foundation slice (other slices import these names):

    spec     Leaf, Group, ExitTemplate, StrategySpec, canonicalise(spec), canonical_json(spec), spec_hash(spec)
    ledger   SearchLedger(path), LedgerEntry, LedgerError, LedgerTamperError
    windows  EXPLORE_START, EXPLORE_CUTOFF, WindowViolation, ExploreFrame, guard_times, truncate_to_explore

Selection happens on 2010-2017 only; every spec is ledgered before its result.
"""
from __future__ import annotations

from goldbot.research.autodisc.ledger import LedgerEntry, LedgerError, LedgerTamperError, SearchLedger
from goldbot.research.autodisc.spec import (
    ExitTemplate,
    Group,
    Leaf,
    StrategySpec,
    canonical_json,
    canonicalise,
    spec_hash,
)
from goldbot.research.autodisc.windows import (
    EXPLORE_CUTOFF,
    EXPLORE_START,
    ExploreFrame,
    WindowViolation,
    guard_times,
    truncate_to_explore,
)

__all__ = [
    "EXPLORE_CUTOFF", "EXPLORE_START", "ExitTemplate", "ExploreFrame", "Group", "Leaf", "LedgerEntry",
    "LedgerError", "LedgerTamperError", "SearchLedger", "StrategySpec", "WindowViolation", "canonical_json",
    "canonicalise", "guard_times", "spec_hash", "truncate_to_explore",
]
