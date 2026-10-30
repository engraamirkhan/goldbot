"""The outcome tensor of autonomous discovery (ADR 0058, design section 3.2 step 2): for every decision bar, side and
exit template, the triple-barrier outcome net of costs, built once. A spec evaluation is then a boolean mask over bars
plus a side rule, a lookup and a sum (`select`), never a relabelling.

Nothing here reimplements labels or costs. Every outcome comes from `labels.triple_barrier` (`triple_barrier`, or
`triple_barrier_1m` for a rule read on 1m barriers), the spread is in the fills (entry at the ask for a long, bid for
a short), `extra_cost_usd` (slippage and commission, `execution.costs.research_costs`) is taken off the return per
entry price and the swap is charged by `triple_barrier(swap=)`, exactly as `research.pipeline.prepare` labels a family
for `research.screen`. R is the stop distance as a fraction of the entry price (`pipeline._risk_fraction`).

Layout: arrays of shape (n_bars, 2, n_exits); side index 0 is long (+1), 1 is short (-1). A cell with no outcome (the
last bar, an undefined ATR) is NaN in `ret` and -1 in `t_entry`/`t_exit`.

Window guard: `build_tensor` takes the bars as `windows.ExploreFrame` (construction raises `WindowViolation` for any bar
at or after 2018-01-01); `tensor.digest` is that frame's digest (the evaluator's input digest).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from goldbot.labels.triple_barrier import BarrierSpec, SwapSpec, triple_barrier, triple_barrier_1m
from goldbot.research.autodisc.spec import ExitTemplate
from goldbot.research.autodisc.windows import ExploreFrame, StageFrame

LONG, SHORT = 1, -1
SIDES: tuple[int, int] = (LONG, SHORT)                 # side index 0 / 1
_NO_TARGET_ATR = 1e6                                   # a time-exit template: the target is out of reach


@dataclass(frozen=True)
class BarrierExit:
    """One stop/target/time-barrier exit in ATR units. `target_atr` None is a time exit at `max_bars`."""
    name: str
    stop_atr: float
    target_atr: float | None
    max_bars: int

    def barrier_spec(self) -> BarrierSpec:
        return BarrierSpec(target_atr=self.target_atr if self.target_atr is not None else _NO_TARGET_ATR,
                           stop_atr=self.stop_atr, max_bars=self.max_bars, name=self.name,
                           has_target=self.target_atr is not None)

    @classmethod
    def from_barrier_spec(cls, spec: BarrierSpec) -> BarrierExit:
        """A family's own barriers as a template (the parity re-expression of an existing family)."""
        return cls(name=spec.name, stop_atr=spec.stop_atr, target_atr=spec.target_atr if spec.has_target else None,
                   max_bars=spec.max_bars)


def barrier_exit(exit_: ExitTemplate, max_bars: int) -> BarrierExit:
    """The numeric barriers of a spec's menu exit (spec.ExitTemplate): stop 1.0/1.5 ATR, target 1R/2R of that stop, or a
    time exit at `time_bars`; `max_bars` is the time barrier of the 1R/2R ones. The structural stop needs a price level and is not in the tensor (ValueError)."""
    if exit_.stop == "structural":
        raise ValueError("the structural stop is not in the outcome tensor")
    stop = float(exit_.stop.split("_")[1])
    if exit_.target == "time":
        assert exit_.time_bars is not None
        return BarrierExit(f"s{stop:g}_time", stop, None, exit_.time_bars)
    mult = 1 if exit_.target == "1R" else 2
    return BarrierExit(f"s{stop:g}_t{exit_.target}", stop, mult * stop, max_bars)


def default_menu(max_bars: int) -> tuple[BarrierExit, ...]:
    """The design's fixed menu of 6: stop {1.0, 1.5} ATR x {target 1R, target 2R, time exit at `max_bars`}.
    (The design's "structural" stop needs a price level from the spec and is not in the tensor.)"""
    out: list[BarrierExit] = []
    for stop in (1.0, 1.5):
        out += [BarrierExit(f"s{stop:g}_t1R", stop, stop, max_bars), BarrierExit(f"s{stop:g}_t2R", stop, 2 * stop, max_bars),
                BarrierExit(f"s{stop:g}_time", stop, None, max_bars)]
    return tuple(out)


@dataclass
class OutcomeTensor:
    """Outcomes of every (bar, side, exit): see the module docstring for the layout."""
    ts_utc: pd.DatetimeIndex
    exits: tuple[BarrierExit, ...]
    ret: np.ndarray            # net signed return (fraction of entry price): spread in the fill, extra cost and swap off
    risk: np.ndarray           # stop distance / entry price: ret / risk is the trade's R
    cost: np.ndarray           # round-trip cost as a fraction of price: spread at the signal bar + extra - swap_ret
    t_entry: np.ndarray        # position of the entry bar (-1: no outcome)
    t_exit: np.ndarray         # position of the exit bar (-1: no outcome)
    digest: str = ""
    _drift: dict[int, np.ndarray] = field(default_factory=dict, repr=False)

    @property
    def n_bars(self) -> int:
        return len(self.ts_utc)

    def exit_index(self, name: str) -> int:
        return [e.name for e in self.exits].index(name)

    def r(self, exit_i: int, cost_mult: float = 1.0) -> np.ndarray:
        """R of every (bar, side) for one exit, with the cost scaled by `cost_mult` (1.0: as labelled; 1.5: the stress)."""
        ret = self.ret[:, :, exit_i] - (cost_mult - 1.0) * self.cost[:, :, exit_i]
        return ret / self.risk[:, :, exit_i]

    def drift_table(self, exit_i: int) -> np.ndarray:
        """Mean R of ALL bars by (side, UTC hour of the entry bar), for one exit: the random-entry baseline of the same
        timing and side (design 3.2 step 5), as an expectation rather than a draw. Shape (2, 24); NaN where empty."""
        if exit_i not in self._drift:
            r = self.r(exit_i)
            hour = self.ts_utc.hour.to_numpy()
            tab = np.full((2, 24), np.nan)
            for s in range(2):
                for h in range(24):
                    v = r[(hour == h) & np.isfinite(r[:, s]), s]
                    if len(v):
                        tab[s, h] = float(v.mean())
            self._drift[exit_i] = tab
        return self._drift[exit_i]

    def select(self, mask: np.ndarray, side: np.ndarray | int, exit_i: int) -> Events:
        """The events of a spec: bars where `mask` is true, on `side` (+1 / -1 per bar, 0 or a missing outcome: none),
        one position at a time exactly as `labels.one_at_a_time` (a candidate is kept once the previous kept one has
        exited). `side` may be one int for a fixed side."""
        mask = np.asarray(mask, dtype=bool)
        side_arr = np.full(self.n_bars, int(side), dtype=np.int8) if isinstance(side, int) else np.asarray(side, dtype=np.int8)
        if len(mask) != self.n_bars or len(side_arr) != self.n_bars:
            raise ValueError("mask and side must have one value per bar")
        idx_l: list[np.ndarray] = []
        side_l: list[np.ndarray] = []
        for k, sv in enumerate(SIDES):
            sel = np.flatnonzero(mask & (side_arr == sv) & (self.t_entry[:, k, exit_i] >= 0)
                                 & np.isfinite(self.ret[:, k, exit_i]))
            idx_l.append(sel)
            side_l.append(np.full(len(sel), k))
        idx0, si0 = np.concatenate(idx_l), np.concatenate(side_l)
        order = np.lexsort((si0, idx0))                     # signal order: by bar, long before short
        idx, si = idx0[order], si0[order]
        te, tx = self.t_entry[idx, si, exit_i], self.t_exit[idx, si, exit_i]
        keep = _one_at_a_time(te, tx)
        idx, si = idx[keep], si[keep]
        return Events(idx=idx, side_i=si, ret=self.ret[idx, si, exit_i], risk=self.risk[idx, si, exit_i],
                      cost=self.cost[idx, si, exit_i], t_entry=self.t_entry[idx, si, exit_i],
                      t_exit=self.t_exit[idx, si, exit_i], ts_utc=self.ts_utc[idx])


@dataclass
class Events:
    """The kept events of one spec (arrays of equal length, in entry order)."""
    idx: np.ndarray
    side_i: np.ndarray
    ret: np.ndarray
    risk: np.ndarray
    cost: np.ndarray
    t_entry: np.ndarray
    t_exit: np.ndarray
    ts_utc: pd.DatetimeIndex

    def __len__(self) -> int:
        return len(self.idx)

    def r(self, cost_mult: float = 1.0) -> np.ndarray:
        return (self.ret - (cost_mult - 1.0) * self.cost) / self.risk


def _one_at_a_time(t_entry: np.ndarray, t_exit: np.ndarray) -> np.ndarray:
    """Boolean keep mask over events already in signal order: `labels.one_at_a_time` (sort by t_entry, keep an event
    whose entry is after the previous kept event's exit), returned in the input order."""
    order = np.argsort(t_entry, kind="stable")
    keep = np.zeros(len(t_entry), dtype=bool)
    busy = -1
    for i in order:
        if t_entry[i] > busy:
            keep[i] = True
            busy = t_exit[i]
    return keep


def build_tensor(bars_in: ExploreFrame | StageFrame, atr: pd.Series, exits: Sequence[BarrierExit], *,
                 extra_cost_usd: float = 0.0, swap: SwapSpec | None = None, bars_1m_in: ExploreFrame | None = None,
                 mode: Literal["bar", "1m"] = "bar") -> OutcomeTensor:
    """The outcome tensor of `bars_in` (decision bars as an ExploreFrame: store schema with ts_utc and bid_/ask_ OHLC) for `exits`.
    `atr`: the barrier ATR per bar (the same series the family's labels use). `extra_cost_usd` and `swap` come from
    `execution.costs.research_costs(settings, table)` (the conservative table: the max of the refit cell and the table
    is the caller's choice, ADR 0045). `mode="1m"` reads the barriers on `bars_1m` (`triple_barrier_1m`, a rule
    pre-registered on 1m barriers); "bar" is the decision-bar labeller every family without `barriers_on_1m` uses."""
    # re-checked here: a frame mutated after its guard is refused (a StageFrame: confirm.py, ADR 0058 phase 3)
    StageFrame(bars_in.frame, bars_in.stage) if isinstance(bars_in, StageFrame) else ExploreFrame(bars_in.frame)
    bars = bars_in.frame.reset_index(drop=True)
    bars_1m = None if bars_1m_in is None else bars_1m_in.frame
    if mode == "1m" and bars_1m is None:
        raise ValueError("mode='1m' needs bars_1m_in")
    n, e_n = len(bars), len(exits)
    shape = (n, 2, e_n)
    ret, risk, cost = np.full(shape, np.nan), np.full(shape, np.nan), np.full(shape, np.nan)
    t_entry, t_exit = np.full(shape, -1, dtype=np.int32), np.full(shape, -1, dtype=np.int32)
    a = atr.to_numpy(dtype=float)
    spread = (bars["ask_close"].to_numpy(dtype=float) - bars["bid_close"].to_numpy(dtype=float))
    signals = pd.DataFrame({"idx": np.repeat(np.arange(n), 2), "side": np.tile(np.array(SIDES), n)})
    for k, ex in enumerate(exits):
        spec = ex.barrier_spec()
        if mode == "1m":
            assert bars_1m is not None
            lab = triple_barrier_1m(bars, bars_1m, signals, spec, atr, swap=swap)
        else:
            lab = triple_barrier(bars, signals, spec, atr, swap=swap)
        if lab.empty:
            continue
        i, s = lab["idx"].to_numpy(dtype=int), (lab["side"].to_numpy(dtype=int) < 0).astype(int)
        entry = lab["entry"].to_numpy(dtype=float)
        r_ret = lab["ret"].to_numpy(dtype=float)
        swap_ret = lab["swap_ret"].to_numpy(dtype=float) if "swap_ret" in lab else np.zeros(len(lab))
        extra = extra_cost_usd / entry
        ret[i, s, k] = r_ret - extra
        risk[i, s, k] = ex.stop_atr * a[i] / entry
        cost[i, s, k] = spread[i] / entry + extra - swap_ret
        t_entry[i, s, k], t_exit[i, s, k] = lab["t_entry"].to_numpy(dtype=int), lab["t_exit"].to_numpy(dtype=int)
    ts = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True))
    return OutcomeTensor(ts_utc=ts, exits=tuple(exits), ret=ret, risk=risk, cost=cost, t_entry=t_entry, t_exit=t_exit,
                         digest=bars_in.digest)
