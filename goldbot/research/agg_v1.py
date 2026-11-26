"""agg-v1: AdaHedge over frozen models, the forward "reinforce" step (ML plan 2026-11-25 ruling R7(b) and item 15:
"agg-v1 AdaHedge over frozen history-pretrained models plus 'keep all' and 'abstain', warm-started from the last
history OOF year"; "4 runs in a row with abstain at the top weight = a futility record"; ADR 0106 family G: "combine
A to F, weighted by recent out-of-sample performance"). Code and fixtures only: the judged runs are 2027Q1's card.

AdaHedge (de Rooij, van Erven, Grünwald and Koolen 2014, "Follow the leader if you can, hedge if you must"):
- experts: frozen models (their keep decisions on the same candidates) plus `KEEP_ALL` and `ABSTAIN`;
- a round is one session day; an expert's loss is minus the net R of the candidates it keeps that day (`day_losses`;
  abstain 0, keep-all minus the day's total);
- weights w ∝ exp(-eta L) with eta = ln K / Delta, Delta the cumulative mixability gap (`step`: h = w . l, the mix loss
  m = -ln(sum w exp(-eta l)) / eta, delta = h - m >= 0); Delta = 0 is eta = infinity: uniform on the current leaders.
  No learning rate and no loss range to tune: the method is scale-free (tested), and its regret against the best
  expert is at most 2 Delta (tested on fixtures);
- the aggregate keep is the weighted vote, keep iff sum of the keeping experts' weights >= 1/2 (`mix_keep`).
The state (`HedgeState`) is a frozen record with a sha; it never reads a label itself: the caller hands it losses of
rounds already closed, so a round's weights depend only on earlier rounds.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord

KEEP_ALL = "keep_all"
ABSTAIN = "abstain"
FUTILITY_RUNS = 4


class HedgeState(FrozenRecord):
    experts: tuple[str, ...]
    cum_loss: tuple[float, ...]
    delta: float                    # cumulative mixability gap
    mix_loss: float                 # cumulative loss of the weighted mixture (h)
    rounds: int
    top_by_run: tuple[str, ...]     # the expert with the top weight at the close of each judged run

    def sha256(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def init(experts: Sequence[str]) -> HedgeState:
    ex = tuple(str(e) for e in experts)
    if KEEP_ALL not in ex or ABSTAIN not in ex:
        raise ValueError(f"agg-v1 experts must include {KEEP_ALL!r} and {ABSTAIN!r}")
    if len(set(ex)) != len(ex):
        raise ValueError("agg-v1: duplicate expert")
    return HedgeState(experts=ex, cum_loss=(0.0,) * len(ex), delta=0.0, mix_loss=0.0, rounds=0, top_by_run=())


def weights(s: HedgeState) -> np.ndarray:
    L = np.asarray(s.cum_loss)
    if s.delta <= 0:
        lead = np.isclose(L, L.min(), rtol=0, atol=1e-12)
        return lead / lead.sum()
    eta = math.log(len(L)) / s.delta
    z = -eta * (L - L.min())
    w = np.exp(z)
    return w / w.sum()


def step(s: HedgeState, losses: np.ndarray) -> HedgeState:
    """One closed round: the losses of every expert, in `s.experts` order."""
    lo = np.asarray(losses, dtype=float)
    if lo.shape != (len(s.experts),) or not np.isfinite(lo).all():
        raise ValueError("agg-v1 step: one finite loss per expert")
    w = weights(s)
    h = float(w @ lo)
    if s.delta <= 0:
        m = float(lo[w > 0].min())
    else:
        eta = math.log(len(lo)) / s.delta
        a = -eta * lo
        top = float(a[w > 0].max())
        m = -(top + math.log(float(w @ np.exp(a - top)))) / eta
    gap = max(0.0, h - m)
    return s.model_copy(update={"cum_loss": tuple((np.asarray(s.cum_loss) + lo).tolist()), "delta": s.delta + gap,
                                "mix_loss": s.mix_loss + h, "rounds": s.rounds + 1})


def day_losses(net_r: np.ndarray, day: np.ndarray, keeps: Mapping[str, np.ndarray], *,
               experts: Sequence[str]) -> pd.DataFrame:
    """Per session day (rows, in day order) and expert (columns): minus the net R of the candidates it keeps."""
    net = np.asarray(net_r, dtype=float)
    cols = {}
    for e in experts:
        if e == ABSTAIN:
            k = np.zeros(len(net))
        elif e == KEEP_ALL:
            k = np.ones(len(net))
        else:
            k = np.asarray(keeps[e], dtype=float)
        cols[e] = -(net * k)
    return pd.DataFrame(cols).groupby(np.asarray(day), sort=True).sum()[list(experts)]


def warm_start(experts: Sequence[str], losses: pd.DataFrame) -> HedgeState:
    """Replay a history OOF year's day losses (columns exactly `experts`, rows in time order)."""
    if list(losses.columns) != list(experts):
        raise ValueError("agg-v1 warm start: the loss columns must be the experts, in order")
    s = init(experts)
    for row in losses.to_numpy(dtype=float):
        s = step(s, row)
    return s


def mix_keep(s: HedgeState, keeps: Mapping[str, np.ndarray]) -> np.ndarray:
    """The aggregate keep: the weighted vote (keep-all votes keep, abstain votes drop)."""
    w = weights(s)
    n = len(next(iter(keeps.values()))) if keeps else 0
    vote = np.zeros(n)
    for e, wi in zip(s.experts, w, strict=True):
        if e == KEEP_ALL:
            vote += wi
        elif e != ABSTAIN:
            vote += wi * np.asarray(keeps[e], dtype=float)
    return vote >= 0.5


def close_run(s: HedgeState) -> HedgeState:
    """Record the top-weight expert at the close of a judged run."""
    return s.model_copy(update={"top_by_run": (*s.top_by_run, s.experts[int(np.argmax(weights(s)))])})


def futile(s: HedgeState, runs: int = FUTILITY_RUNS) -> bool:
    """"4 runs in a row with abstain at the top weight = a futility record"."""
    return len(s.top_by_run) >= runs and all(t == ABSTAIN for t in s.top_by_run[-runs:])
