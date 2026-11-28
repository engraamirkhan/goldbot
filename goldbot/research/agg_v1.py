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

Point in time (quant 6 on #489): a round's loss is known only when its last candidate's horizon has ended, so
`day_losses` also returns each round's `open_utc` (first decision) and `close_utc` (last horizon end), and `replay`
applies a round's loss only once its close_utc is at or before the opening of the round being weighted (a round that
straddles the next one's open waits). The history entry point `history_rounds` takes an OOF frame: every keep column
is `k_<expert>`, and each row carries the `fold_train_end` of the fold that produced its keeps; a row whose
fold_train_end + gap is after its decision is not out of fold and is refused.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord

KEEP_ALL = "keep_all"
ABSTAIN = "abstain"
OPEN, CLOSE = "open_utc", "close_utc"
OOF_COLUMNS = ("decision_utc", "horizon_end_utc", "day", "net_r", "fold_train_end")
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


def day_losses(net_r: np.ndarray, day: np.ndarray, keeps: Mapping[str, np.ndarray], *, experts: Sequence[str],
               decision_utc: Any, horizon_end_utc: Any) -> pd.DataFrame:
    """Per session day (rows, in day order): each expert's loss (minus the net R of the candidates it keeps), the
    round's `open_utc` (its first decision) and `close_utc` (its last horizon end: when the loss is known)."""
    net = np.asarray(net_r, dtype=float)
    cols: dict[str, Any] = {}
    for e in experts:
        if e == ABSTAIN:
            k = np.zeros(len(net))
        elif e == KEEP_ALL:
            k = np.ones(len(net))
        else:
            k = np.asarray(keeps[e], dtype=float)
        cols[e] = -(net * k)
    g = np.asarray(day)
    out = pd.DataFrame(cols).groupby(g, sort=True).sum()[list(experts)]
    t = pd.DataFrame({OPEN: pd.to_datetime(decision_utc, utc=True), CLOSE: pd.to_datetime(horizon_end_utc, utc=True)})
    t = t.groupby(g, sort=True).agg({OPEN: "min", CLOSE: "max"})
    return out.join(t)


def replay(experts: Sequence[str], rounds: pd.DataFrame) -> tuple[np.ndarray, HedgeState]:
    """(the weights each round was decided with, the state after every round has closed): rounds in time order with
    the expert columns and OPEN / CLOSE; a round's loss enters only when its CLOSE <= the next weighted round's OPEN."""
    _check_experts(experts, rounds)
    s = init(experts)
    L = rounds[list(experts)].to_numpy(dtype=float)
    opens = pd.DatetimeIndex(pd.to_datetime(rounds[OPEN], utc=True))
    closes = pd.DatetimeIndex(pd.to_datetime(rounds[CLOSE], utc=True))
    pending: list[int] = []
    used = np.empty((len(rounds), len(experts)))
    for t in range(len(rounds)):
        ready = [i for i in pending if closes[i] <= opens[t]]
        for i in ready:
            s = step(s, L[i])
        pending = [i for i in pending if i not in ready]
        used[t] = weights(s)
        pending.append(t)
    for i in pending:
        s = step(s, L[i])
    return used, s


def _check_experts(experts: Sequence[str], losses: pd.DataFrame) -> None:
    if [c for c in losses.columns if c not in (OPEN, CLOSE)] != list(experts):
        raise ValueError("agg-v1: the loss columns must be the experts, in order")


def history_rounds(oof: pd.DataFrame, *, experts: Sequence[str], gap: pd.Timedelta) -> pd.DataFrame:
    """The day rounds of a history OOF frame (`OOF_COLUMNS` plus `k_<expert>` per model expert). Refused: any other
    column (a keep not named k_ is not an OOF keep) and any row whose fold_train_end + `gap` is after its decision."""
    models = [e for e in experts if e not in (KEEP_ALL, ABSTAIN)]
    want = {*OOF_COLUMNS, *(f"k_{e}" for e in models)}
    extra = sorted(set(oof.columns) - want)
    missing = sorted(want - set(oof.columns))
    if extra or missing:
        raise ValueError(f"agg-v1 history: keep columns are k_<expert> for {models} beside {list(OOF_COLUMNS)}; "
                         f"unexpected {extra}, missing {missing}")
    dec = pd.DatetimeIndex(pd.to_datetime(oof["decision_utc"], utc=True))
    fte = pd.DatetimeIndex(pd.to_datetime(oof["fold_train_end"], utc=True))
    bad = np.asarray(fte + gap > dec)
    if bad.any():
        raise ValueError(f"agg-v1 history: {int(bad.sum())} rows are not out of fold (fold_train_end + {gap} after the "
                         f"decision), first {dec[bad][0]}")
    return day_losses(oof["net_r"].to_numpy(dtype=float), oof["day"].to_numpy(),
                      {e: oof[f"k_{e}"].to_numpy(dtype=bool) for e in models}, experts=experts,
                      decision_utc=dec, horizon_end_utc=oof["horizon_end_utc"])


def warm_start(experts: Sequence[str], losses: pd.DataFrame) -> HedgeState:
    """Replay a history OOF year's day losses (expert columns exactly `experts`, rows in time order; with OPEN / CLOSE
    the point-in-time `replay`, whose final state applies every round in order too)."""
    _check_experts(experts, losses)
    if OPEN in losses.columns and CLOSE in losses.columns:
        return replay(experts, losses)[1]
    s = init(experts)
    for row in losses[list(experts)].to_numpy(dtype=float):
        s = step(s, row)
    return s


def mix_keep(s: HedgeState, keeps: Mapping[str, np.ndarray], *, n: int) -> np.ndarray:
    """The aggregate keep of `n` candidates: the weighted vote (keep-all votes keep, abstain votes drop)."""
    w = weights(s)
    vote = np.zeros(n)
    for e, wi in zip(s.experts, w, strict=True):
        if e == KEEP_ALL:
            vote += wi
        elif e != ABSTAIN:
            k = np.asarray(keeps[e], dtype=float)
            if k.shape != (n,):
                raise ValueError(f"agg-v1 mix_keep: {e} keeps {k.shape}, not ({n},)")
            vote += wi * k
    return vote >= 0.5


def close_run(s: HedgeState) -> HedgeState:
    """Record the top-weight expert at the close of a judged run."""
    return s.model_copy(update={"top_by_run": (*s.top_by_run, s.experts[int(np.argmax(weights(s)))])})


def futile(s: HedgeState, runs: int = FUTILITY_RUNS) -> bool:
    """"4 runs in a row with abstain at the top weight = a futility record"."""
    return len(s.top_by_run) >= runs and all(t == ABSTAIN for t in s.top_by_run[-runs:])
