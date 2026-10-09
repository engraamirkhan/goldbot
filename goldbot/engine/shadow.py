"""Shadow book: every challenger and the champion trade on paper at live prices, with no orders (design: Retraining
and promotion). The Saturday job reads each version's PerfStats from state/shadow_<version>.json to promote or retire
challengers, and the daily model watch runs the CUSUM alarm on a new champion's shadow trades.

Trade mechanics mirror the triple-barrier labels the models were trained on, so shadow and backtest are comparable:
entry at the signal bar's close on the paying side (ask for longs, bid for shorts), target/stop at
target_atr/stop_atr x ATR, stop assumed first when both are touched in one bar, time exit at the close of the
(max_bars + 1)-th bar after entry, return = side x (exit - entry) / entry.

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
    barrier: str | None = None        # target | stop | time
    ret: float | None = None


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
                   threshold: float | None = None, taken: bool = True, p_raw: float | None = None) -> ShadowTrade | None:
        if not np.isfinite(atr_usd) or atr_usd <= 0:
            return None
        book = self.books[version]
        if any(t.entry_ts == bar_ts for t in book.open) or any(t.entry_ts == bar_ts for t in book.closed[-5:]):
            return None   # one shadow entry per version per signal bar, even if the bar is replayed
        if any(t.agent_id == agent_id for t in book.open):
            return None   # one position per agent at a time (taken or not), as the labels it was trained on
        t = ShadowTrade(version=version, agent_id=agent_id, side=side, entry_ts=bar_ts, entry=entry,
                        stop=entry - side * stop_atr * atr_usd, target=entry + side * target_atr * atr_usd,
                        max_bars=max_bars, p=p, timeframe=timeframe, threshold=threshold, taken=taken, p_raw=p_raw)
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
                if t.exit is None:
                    still.append(t)
                else:
                    book.closed.append(t)
                    closed.append(t)
            book.open = still
        return closed

    @staticmethod
    def _close(t: ShadowTrade, ts: pd.Timestamp, px: float, barrier: str) -> None:
        t.exit_ts, t.exit, t.barrier = ts, float(px), barrier
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
