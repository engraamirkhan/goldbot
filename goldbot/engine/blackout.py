"""News-blackout re-score of open trades (design, News blackout: "early close if p < 0.5"), moved out of
engine/runner.py, and the trades whose agent has no served model (PR #494 trading-safety MEDIUM-3).

In a tier-1 calendar blackout each open trade of a known agent is re-scored by its model on the agent's latest
completed bar with the trade's side; p < 0.5 closes it at market, never gated by approval.

An agent's model can be dropped at a model reload while it has open trades: the serving rule refuses it
(model_registry.load_for_serving, ADR 0109), or a CUSUM alarm retired the champion and its previous model would not
serve (model_watch `restore_refused`). Such a trade cannot be re-scored. Its exits are untouched: the broker stop and
target and the exit policy still manage it; nothing here gates or delays an exit. The engine says so once per
position and event (`blackout_rescore_no_model`) and publishes the positions (`open_without_model`) so health warns
while any is open.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger("goldbot.engine")


def served_model(eng: Any, agent: Any) -> Any:
    """The agent's served model (keyed by agent id, or by family in a single-agent setup), None when it has none."""
    return eng.models.get(agent.agent_id) or eng.models.get(agent.family)


def open_without_model(eng: Any) -> list[int]:
    """Open positions of a known agent that has no served model."""
    return sorted(pid for pid, tr in eng.open.items()
                  if (a := eng.agents.get(tr.agent_id)) is not None and served_model(eng, a) is None)


def _note_no_model(eng: Any, pid: int, tr: Any, ev: dict) -> None:
    """Record `blackout_rescore_no_model` once per (position, blackout event)."""
    said: set[tuple[int, str]] = eng.__dict__.setdefault("_no_model_said", set())
    key = (pid, str(ev.get("title")))
    if key in said:
        return
    said.add(key)
    log.warning("blackout re-score skipped for position %s: agent %s has no served model", pid, tr.agent_id)
    eng.decisions.append({"ts": time.time(), "agent": tr.agent_id, "action": "blackout_rescore_no_model",
                          "position": pid, "event": ev.get("title")})


def rescore(eng: Any, pid: int, tr: Any, complete: pd.DataFrame, close_ts: pd.Timestamp, frames: dict[str, Any],
            ev: dict) -> None:
    """Re-score one open trade; close it at market (`_policy_close`, never gated) when p < 0.5."""
    agent = eng.agents.get(tr.agent_id)
    if agent is None:
        return
    model = served_model(eng, agent)
    if model is None:
        _note_no_model(eng, pid, tr, ev)
        return
    fr = frames.get(agent.timeframe)
    if fr is None:
        fr = eng._frame(complete, agent.timeframe, close_ts)
        if fr is None:
            return
        frames[agent.timeframe] = fr
    Xm = eng._scoring_frame(fr, model)
    if Xm is None:
        return                                        # a model only scores the frame version it was trained on
    feats = Xm.drop(columns=["ts_utc"]).iloc[[len(Xm) - 1]].replace([np.inf, -np.inf], np.nan)
    feats["side"] = tr.side
    cols = [c for c in model.feature_names if c in feats.columns] if model.feature_names else list(feats.columns)
    p = float(model.predict(feats[cols])[0])
    if p < 0.5:
        eng._policy_close(pid, tr, "blackout_close", p=round(p, 4), event=ev.get("title"))
