"""Shadow book: every challenger and the champion trade on paper at live prices, with no orders (design: Retraining
and promotion). The Saturday job reads each version's PerfStats from state/shadow_<version>.json to promote or retire
challengers, and the daily model watch runs the CUSUM alarm on a new champion's shadow trades.

Trade mechanics mirror the triple-barrier labels the models were trained on, so shadow and backtest are comparable:
entry at the signal bar's close on the paying side (ask for longs, bid for shorts), target/stop at
target_atr/stop_atr x ATR, stop assumed first when both are touched in one bar, time exit at the close of the
(max_bars + 1)-th bar after entry, return = side x (exit - entry) / entry. A trade opened with its specialist's exit
policy (trail, scale-out, hard flat) advances through `labels.exit_policy.policy_step`, the step the labels use, so
the shadow outcome of each policy equals its label; `stop` stays the initial stop (the trade's risk).

One engine hosts the book (the account on the canonical-cost broker), so trades are never counted twice.

Counterfactual outcomes (proposal P9): every candidate a version scores is recorded with its p, the threshold at the
time and whether the version would trade it (`taken`), so its calibration can be refitted on an unbiased sample
(`outcomes`; research/model.py `recalibrate`). One position per agent applies over every candidate, taken or not,
exactly as research thins candidates (`labels.one_at_a_time`) before the model filters them, so the taken trades are
the backtest's model-filtered set. Trading statistics (`stats`, `returns_since`, the population) count taken trades
only. A book written before this change loads unchanged: its trades are taken (they were only ever the selected
ones) and carry no threshold, which keeps them out of `outcomes` (a selected-only sample is biased); a candidate
recorded with its threshold comes from a book that records every candidate.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import Record, UtcTimestamp
from goldbot.config import tf_seconds
from goldbot.labels.exit_policy import ExitPolicy, PolicyState, new_state, policy_return, policy_step
from goldbot.research.metrics import summarize
from goldbot.research.promotion import PerfStats


class ShadowTrade(Record):
    version: str
    agent_id: str
    side: int
    entry_ts: UtcTimestamp            # signal bar open time (the bar whose close is the entry)
    entry: float
    stop: float
    target: float
    max_bars: int                     # in bars of `timeframe`
    timeframe: str = "15m"
    p: float
    threshold: float | None = None    # the version's entry threshold when the candidate fired (None: older book)
    taken: bool = True                # p cleared the threshold (would have traded); False = counterfactual only
    p_raw: float | None = None        # the model's uncalibrated score, so a recalibration can re-map it
    bars_held: int = 0
    exit_ts: UtcTimestamp | None = None
    exit: float | None = None
    barrier: str | None = None        # target | stop | time (and trail | flat under an exit policy)
    ret: float | None = None
    policy: ExitPolicy | None = None  # the specialist's exit policy (None: the plain barriers)
    atr_usd: float | None = None      # the signal bar's ATR, which the policy's distances are in
    state: PolicyState | None = None  # trailed stop, best price, scale-out fill, hard-flat deadline


class VersionBook(Record):
    version: str
    started_utc: UtcTimestamp
    open: list[ShadowTrade] = Field(default_factory=list)
    closed: list[ShadowTrade] = Field(default_factory=list)


class ShadowBook:
    def __init__(self, state_dir: str | Path):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "shadow_book.json"
        raw = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.books: dict[str, VersionBook] = {k: VersionBook.model_validate(v) for k, v in raw.items()}

    # ------------------------------------------------------------------ trading
    def track(self, version: str, now: pd.Timestamp) -> None:
        """Start a version's shadow clock (idempotent)."""
        if version not in self.books:
            self.books[version] = VersionBook(version=version, started_utc=now)

    def open_trade(self, *, version: str, agent_id: str, side: int, bar_ts: pd.Timestamp, entry: float, atr_usd: float,
                   target_atr: float, stop_atr: float, max_bars: int, p: float, timeframe: str = "15m",
                   threshold: float | None = None, taken: bool = True, p_raw: float | None = None,
                   policy: ExitPolicy | None = None, signal_close: pd.Timestamp | None = None) -> ShadowTrade | None:
        """signal_close: when the signal bar closed (its visible_at; default bar_ts + timeframe), which the hard-flat
        deadline counts from, as the labels do."""
        if not np.isfinite(atr_usd) or atr_usd <= 0:
            return None
        book = self.books[version]
        if any(t.entry_ts == bar_ts for t in book.open) or any(t.entry_ts == bar_ts for t in book.closed[-5:]):
            return None   # one shadow entry per version per signal bar, even if the bar is replayed
        if any(t.agent_id == agent_id for t in book.open):
            return None   # one position per agent at a time (taken or not), as the labels it was trained on
        stop = entry - side * stop_atr * atr_usd
        state = None
        if policy is not None and policy.active:
            close = signal_close if signal_close is not None else bar_ts + pd.Timedelta(seconds=tf_seconds(timeframe))
            state = new_state(policy, entry=entry, stop=stop, signal_close=close)
        t = ShadowTrade(version=version, agent_id=agent_id, side=side, entry_ts=bar_ts, entry=entry,
                        stop=stop, target=entry + side * target_atr * atr_usd,
                        max_bars=max_bars, p=p, timeframe=timeframe, threshold=threshold, taken=taken, p_raw=p_raw,
                        policy=policy if state is not None else None, atr_usd=atr_usd if state is not None else None,
                        state=state)
        book.open.append(t)
        return t

    def on_bar(self, bar: pd.Series, timeframe: str = "15m") -> list[ShadowTrade]:
        """Advance the open trades of `timeframe` by one completed bar of it (store schema: ts_utc, bid/ask
        high/low/close). Each trade counts its time barrier in bars of its own agent's timeframe, as its labels do."""
        ts = pd.Timestamp(bar["ts_utc"])
        closed = []
        for book in self.books.values():
            still = []
            for t in book.open:
                if t.timeframe != timeframe or ts <= t.entry_ts:
                    still.append(t)            # the entry bar itself never exits the trade
                    continue
                t.bars_held += 1
                if t.state is not None:
                    self._policy_bar(t, bar, ts, timeframe)
                else:
                    self._barrier_bar(t, bar, ts)
                if t.exit is None:
                    still.append(t)
                else:
                    book.closed.append(t)
                    closed.append(t)
            book.open = still
        return closed

    def _barrier_bar(self, t: ShadowTrade, bar: pd.Series, ts: pd.Timestamp) -> None:
        if t.side > 0:
            hit_stop, hit_tgt = bar["bid_low"] <= t.stop, bar["bid_high"] >= t.target
        else:
            hit_stop, hit_tgt = bar["ask_high"] >= t.stop, bar["ask_low"] <= t.target
        if hit_stop:
            self._close(t, ts, t.stop, "stop")
        elif hit_tgt:
            self._close(t, ts, t.target, "target")
        elif t.bars_held >= t.max_bars + 1:
            self._close(t, ts, float(bar["bid_close"] if t.side > 0 else bar["ask_close"]), "time")

    def _policy_bar(self, t: ShadowTrade, bar: pd.Series, ts: pd.Timestamp, timeframe: str) -> None:
        """One bar under the trade's exit policy (labels.exit_policy.policy_step), then the time barrier."""
        assert t.state is not None and t.policy is not None and t.atr_usd is not None
        long = t.side > 0
        close = float(bar["bid_close"] if long else bar["ask_close"])
        close_ts = pd.Timestamp(bar["visible_at"]) if "visible_at" in bar.index and pd.notna(bar["visible_at"]) \
            else ts + pd.Timedelta(seconds=tf_seconds(timeframe))
        st = t.state.model_copy()
        out = policy_step(t.policy, st, side=t.side, entry=t.entry, atr=t.atr_usd, initial_stop=t.stop, target=t.target,
                          fav=float(bar["bid_high"] if long else bar["ask_low"]),
                          adv=float(bar["bid_low"] if long else bar["ask_high"]), close=close, close_ts=close_ts)
        t.state = st
        if out is None and t.bars_held >= t.max_bars + 1:
            out = ("time", close)
        if out is not None:
            self._close(t, ts, out[1], out[0])

    @staticmethod
    def _close(t: ShadowTrade, ts: pd.Timestamp, px: float, barrier: str) -> None:
        t.exit_ts, t.exit, t.barrier = ts, float(px), barrier
        if t.state is not None and t.policy is not None:
            t.ret = policy_return(t.side, t.entry, t.exit, t.state.scale_exit, t.policy.scale_fraction)
        else:
            t.ret = t.side * (t.exit - t.entry) / t.entry

    # ------------------------------------------------------------------ results
    def stats(self, version: str, now: pd.Timestamp) -> PerfStats:
        book = self.books[version]
        weeks = max((now - book.started_utc).total_seconds() / (7 * 86400), 0.0)
        rets = pd.DataFrame({"ret": [t.ret for t in book.closed if t.taken]})
        if rets.empty:
            return PerfStats(n_trades=0, sharpe_ann=0.0, hit_rate=0.0, max_dd=0.0, trades_per_week=0.0, weeks=weeks)
        per_week = len(rets) / max(weeks, 1 / 7)
        s = summarize(rets, trades_per_year=per_week * 52)
        return PerfStats(n_trades=s["n"], sharpe_ann=s["sharpe_ann"], hit_rate=s["hit_rate"], max_dd=s["max_dd"],
                         trades_per_week=per_week, weeks=weeks, mean_ret=s["mean_ret"], std_ret=s["std_ret"])

    def returns_since(self, version: str, since: pd.Timestamp) -> list[float]:
        book = self.books.get(version)
        if book is None:
            return []
        return [t.ret for t in book.closed
                if t.taken and t.ret is not None and t.exit_ts is not None and t.exit_ts >= since]

    def outcomes(self, version: str, since: pd.Timestamp | None = None) -> list[ShadowTrade]:
        """Closed candidates of `version` recorded with their threshold (every candidate, taken or not), exited at or
        after `since`: the unbiased sample a recalibration may use. Trades of an older book (selected ones only, no
        threshold) are left out."""
        book = self.books.get(version)
        if book is None:
            return []
        return [t for t in book.closed if t.threshold is not None and t.exit_ts is not None
                and (since is None or t.exit_ts >= since)]

    def save(self, now: pd.Timestamp) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: b.model_dump(mode="json") for k, b in self.books.items()}))
        tmp.replace(self.path)
        for version in self.books:
            (self.dir / f"shadow_{version}.json").write_text(self.stats(version, now).model_dump_json())
