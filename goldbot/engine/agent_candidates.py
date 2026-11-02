"""Each agent's candidates at a bar close, kept out of runner.py (ADR 0023: runner.py only shrinks).

Three kinds of agent, one rule each:

* an agent that reads a carried replica state (`replica_params`, engine/replica_context.py) is served by the host's
  `ReplicaShadow` (set by goldbot/ops/services/engine.py), or proposes nothing while none is set. Shadow only
  (`Specialist.shadow_only`): the engine never sends its entries to the broker;
* an agent that needs TradingView values (`requires_tv`) proposes nothing: the TradingView chart path was retired on
  2026-11-02 (owner: "remove the TradingView chart part"), so such a family has no input and fails closed;
* every other agent: its own rule on the decision frame, unchanged.

Before the retirement this lived in engine/tv_context.py (`TvShadowWatch`), which also re-checked TradingView agents'
shadow entries after the close; nothing else changed for the replica or the ordinary agents.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from goldbot.engine.replica_context import ReplicaShadow
from goldbot.specialists.base import Specialist


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int)})


class AgentCandidates:
    """The engine's holder of the replica source; `candidates` gives every agent's candidates at a bar close."""

    def __init__(self) -> None:
        self.replica: ReplicaShadow | None = None      # set by the host (ops.run) to serve replica agents, shadow only

    def candidates(self, fr: Any, agents: list[Specialist]) -> dict[str, pd.DataFrame]:
        """agent_id -> its candidates (idx, side) on the decision frame `fr` (runner._Frame: dec, m, X)."""
        out: dict[str, pd.DataFrame] = {}
        for ag in agents:
            if getattr(ag, "replica_params", None) is not None:      # carried state, or nothing (ReplicaShadow)
                out[ag.agent_id] = self.replica.candidates(ag, fr) if self.replica is not None else _empty()
            elif ag.requires_tv:                                     # no TradingView input any more: fails closed
                out[ag.agent_id] = _empty()
            else:
                out[ag.agent_id] = ag.candidates(fr.m, fr.X)
        return out
