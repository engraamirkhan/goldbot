"""Shadow book: every challenger and the champion trade on paper at live prices, with no orders (design: Retraining
and promotion). The Saturday job reads each version's PerfStats from state/shadow_<version>.json to promote or retire
challengers, and the daily model watch runs the CUSUM alarm on a new champion's shadow trades.

Trade mechanics mirror the triple-barrier labels the models were trained on, so shadow and backtest are comparable:
entry at the signal bar's close on the paying side (ask for longs, bid for shorts), target/stop at
target_atr/stop_atr x ATR, stop assumed first when both are touched in one bar, time exit at the close of the
(max_bars + 1)-th bar after entry, return = side x (exit - entry) / entry. A trade opened with its specialist's exit
policy (trail, scale-out, hard flat) advances through `labels.exit_policy.policy_step`, the step the labels use, so
the shadow outcome of each policy equals its label; `stop` stays the initial stop (the trade's risk). Like the labels,
a stop is filled at the stop even when a bar gaps through it (optimistic; the live broker fills a gap at the market:
see labels.exit_policy), so on a gap the shadow return overstates the live one by the gap. Each trade also carries
its MAE / MFE (BACKLOG 23) from the same bars, with the labels' semantics (labels.excursion.label_excursions).

One engine hosts the book (the account on the canonical-cost broker), so trades are never counted twice.

Counterfactual outcomes (proposal P9): every candidate a version scores is recorded with its p, the threshold at the
time and whether the version would trade it (`taken`), so its calibration can be refitted on an unbiased sample
(`outcomes`; research/model.py `recalibrate`). One position per agent applies over every candidate, taken or not,
exactly as research thins candidates (`labels.one_at_a_time`) before the model filters them, so the taken trades are
the backtest's model-filtered set. Concurrency (research director ruling 2026-10-19, "the system needs to learn, so
more trades need to be opened and closed"): every signal is its own shadow trade, up to `max_open_per_agent` open at
once per agent and version (settings shadow.max_open_per_agent: 3 in production; 1, the labels' one-at-a-time, by
default), over every candidate taken or not so the counterfactual thinning never depends on p; a signal beyond the cap
is counted (`VersionBook.skipped_cap`) and logged "skipped: cap", never traded. Each trade's outcome is still its own
triple-barrier label. `stats` clusters taken trades by agent and session day (promotion.py). Trading statistics (`stats`, `returns_since`, the population) count taken trades
only. A book written before this change loads unchanged: its trades are taken (they were only ever the selected
ones) and carry no threshold, which keeps them out of `outcomes` (a selected-only sample is biased); a candidate
recorded with its threshold comes from a book that records every candidate.

Candidate-borne barriers (shadow roster amendment 2026-10-19): a family whose candidates carry their own R (`risk_usd`,
the structural stop distance its `barrier_atr` gives the labels), target in R (`target_atr`) or time exit (`max_bars`)
is shadow-traded on exactly those (`candidate_barriers`), its cost threshold in the same R; a family whose research
labels read the barriers on 1m bars (`Specialist.barriers_on_1m`, `triple_barrier_1m`: "the stop first only when both
fall in one minute") has its trades advanced minute by minute through the decision bar's 1m bars (`on_bar(minutes=)`;
without them, the decision bar with the stop first, as `triple_barrier`). The time barrier stays the decision bar's.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import Record, UtcTimestamp, load_record
from goldbot.config import tf_seconds
from goldbot.data.timeutil import opportunity_day
from goldbot.labels.excursion import Excursion, bar_prices, exit_bar_extremes
from goldbot.labels.exit_policy import ExitPolicy, PolicyState, new_state, policy_return, policy_step
from goldbot.research.metrics import breakeven_prob, sharpe_annualised, summarize
from goldbot.research.promotion import (
    EVIDENCE_START_UTC,
    PerfStats,
    cluster_sums,
    cluster_t_stat,
    quarantine_reason,
)

if TYPE_CHECKING:
    from goldbot.specialists.base import Specialist

log = logging.getLogger(__name__)


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
    scored_feature_version: str | None = None   # the frame version the model scored (None: older book)
    # MAE / MFE from the bars, as the labels (labels/excursion.py): price and bar time run while open; R (of the
    # initial risk |entry - stop|) set at the exit. None on a trade opened before the change (its first bars unseen).
    # The track starts at `entry` stamped `entry_ts` (the signal bar's open). A scaled trade's figures cover the whole
    # position until its final exit. A stop exit's MAE is -1 R by construction (gaps filled at the stop)
    mae_price: float | None = None
    mae_utc: UtcTimestamp | None = None
    mfe_price: float | None = None
    mfe_utc: UtcTimestamp | None = None
    mae_r: float | None = None
    mfe_r: float | None = None
    # ADR 0032: the host engine's limits profile when the candidate fired (shadow trades are unsized, so the limits do
    # not change them; tagged so no evidence set mixes profiles). None: an older book
    limits_profile: str | None = None
    # the barriers are read on the 1m bars inside each decision bar (`triple_barrier_1m`); False: on the decision bar
    barriers_on_1m: bool = False


class CandidateBarriers(Record):
    """A candidate's shadow barriers: R in USD, target and stop in R, time exit in decision bars, round-trip cost in R."""

    r_usd: float
    target_r: float
    stop_r: float
    max_bars: int
    cost_r: float


def candidate_barriers(agent: Specialist, candidate: pd.Series | None, atr_usd: float,
                       cost_atr: float) -> CandidateBarriers:
    """The barriers `agent`'s research labels give `candidate` (a row of its candidates frame): its own `risk_usd` as
    R (the labels' `barrier_atr`), `target_atr` (in R) and `max_bars` when it carries them, else the label spec on the
    decision bar's ATR(14). The cost moves into the same unit, so the threshold is the labels' break-even."""
    ls = agent.label_spec
    r, target, hold = atr_usd, ls.target_atr, ls.max_bars
    if candidate is not None:
        if "risk_usd" in candidate.index and np.isfinite(float(candidate["risk_usd"])):
            r = float(candidate["risk_usd"])
        if "target_atr" in candidate.index and np.isfinite(float(candidate["target_atr"])):
            target = float(candidate["target_atr"])
        if "max_bars" in candidate.index and pd.notna(candidate["max_bars"]):
            hold = int(candidate["max_bars"])
    cost = cost_atr * atr_usd / r if r > 0 and np.isfinite(r) else cost_atr
    return CandidateBarriers(r_usd=r, target_r=target, stop_r=ls.stop_atr, max_bars=hold, cost_r=cost)


def minutes_of(bars_1m: pd.DataFrame, bar: pd.Series, timeframe: str) -> pd.DataFrame:
    """The 1m bars inside the decision bar `bar` (ts_utc in [open, open + timeframe)), oldest first."""
    if bars_1m.empty:
        return bars_1m
    start = pd.Timestamp(bar["ts_utc"])
    ts = pd.to_datetime(bars_1m["ts_utc"], utc=True)
    inside = (ts >= start) & (ts < start + pd.Timedelta(seconds=tf_seconds(timeframe)))
    return bars_1m[inside.to_numpy()].sort_values("ts_utc")


class VersionBook(Record):
    version: str
    started_utc: UtcTimestamp
    open: list[ShadowTrade] = Field(default_factory=list)
    closed: list[ShadowTrade] = Field(default_factory=list)
    skipped_cap: int = 0              # signals not traded because the agent already had max_open_per_agent open


class ShadowBook:
    def __init__(self, state_dir: str | Path, max_open_per_agent: int = 1,
                 evidence_start: pd.Timestamp | None = EVIDENCE_START_UTC):
        """evidence_start (H-52): the cut `save` writes each version's record (state/shadow_<version>.json, read by the
        promotion gate and the research director's shadow_z) with, so a trade opened before the history-gap fix is
        "never clustered, scored or counted as promotion evidence"; None (tests on older bars) cuts nothing."""
        self.max_open_per_agent = max_open_per_agent
        self.evidence_start = evidence_start
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "shadow_book.json"
        raw = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.books: dict[str, VersionBook] = {k: load_record(VersionBook, v, source="shadow_book.json")
                                                  for k, v in raw.items()}

    # ------------------------------------------------------------------ trading
    def track(self, version: str, now: pd.Timestamp) -> None:
        """Start a version's shadow clock (idempotent)."""
        if version not in self.books:
            self.books[version] = VersionBook(version=version, started_utc=now)

    def open_trade(self, *, version: str, agent_id: str, side: int, bar_ts: pd.Timestamp, entry: float, atr_usd: float,
                   target_atr: float, stop_atr: float, max_bars: int, p: float, timeframe: str = "15m",
                   threshold: float | None = None, taken: bool = True, p_raw: float | None = None,
                   policy: ExitPolicy | None = None, signal_close: pd.Timestamp | None = None,
                   scored_feature_version: str | None = None, limits_profile: str | None = None,
                   barriers_on_1m: bool = False) -> ShadowTrade | None:
        """signal_close: when the signal bar closed (its visible_at; default bar_ts + timeframe), which the hard-flat
        deadline counts from, as the labels do."""
        if not np.isfinite(atr_usd) or atr_usd <= 0:
            return None
        book = self.books[version]
        if any(t.entry_ts == bar_ts for t in book.open) or any(t.entry_ts == bar_ts for t in book.closed[-5:]):
            return None   # one shadow entry per version per signal bar, even if the bar is replayed
        if sum(t.agent_id == agent_id for t in book.open) >= self.max_open_per_agent:
            book.skipped_cap += 1     # ruling 2026-10-19: counted, not traded (taken or not: thinning never reads p)
            log.info("shadow %s %s at %s skipped: cap (%d open)", version, agent_id, bar_ts, self.max_open_per_agent)
            return None
        stop = entry - side * stop_atr * atr_usd
        state = None
        if policy is not None and policy.active:
            close = signal_close if signal_close is not None else bar_ts + pd.Timedelta(seconds=tf_seconds(timeframe))
            state = new_state(policy, entry=entry, stop=stop, signal_close=close)
        t = ShadowTrade(version=version, agent_id=agent_id, side=side, entry_ts=bar_ts, entry=entry,
                        stop=stop, target=entry + side * target_atr * atr_usd,
                        max_bars=max_bars, p=p, timeframe=timeframe, threshold=threshold, taken=taken, p_raw=p_raw,
                        policy=policy if state is not None else None, atr_usd=atr_usd if state is not None else None,
                        state=state, scored_feature_version=scored_feature_version,
                        limits_profile=limits_profile, barriers_on_1m=barriers_on_1m and state is None)
        book.open.append(t)
        return t

    def on_bar(self, bar: pd.Series, timeframe: str = "15m", minutes: pd.DataFrame | None = None) -> list[ShadowTrade]:
        """Advance the open trades of `timeframe` by one completed bar of it (store schema: ts_utc, bid/ask
        high/low/close). Each trade counts its time barrier in bars of its own agent's timeframe, as its labels do.
        `minutes`: the 1m bars inside `bar` (`minutes_of`), which a `barriers_on_1m` trade reads its barriers on."""
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
                elif t.barriers_on_1m and minutes is not None and len(minutes):
                    self._minute_barriers(t, bar, ts, minutes)
                else:
                    self._barrier_bar(t, bar, ts)
                self._excursion_bar(t, bar, ts)
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

    def _minute_barriers(self, t: ShadowTrade, bar: pd.Series, ts: pd.Timestamp, minutes: pd.DataFrame) -> None:
        """The barriers minute by minute (`triple_barrier_1m`: the stop first only inside one minute), the exit stamped
        on the decision bar holding the minute; then the time barrier at the decision bar's close."""
        for _, mb in minutes.iterrows():
            if t.side > 0:
                hit_stop, hit_tgt = mb["bid_low"] <= t.stop, mb["bid_high"] >= t.target
            else:
                hit_stop, hit_tgt = mb["ask_high"] >= t.stop, mb["ask_low"] <= t.target
            if hit_stop:
                self._close(t, ts, t.stop, "stop")
                return
            if hit_tgt:
                self._close(t, ts, t.target, "target")
                return
        if t.bars_held >= t.max_bars + 1:
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
    def _excursion_bar(t: ShadowTrade, bar: pd.Series, ts: pd.Timestamp) -> None:
        """Fold this bar into the trade's MAE / MFE after its exit decision (labels/excursion.py: the exit bar under the
        stop-first convention), and set the R figures at the exit. A trade whose track did not start on its first bar
        (opened before the change) keeps none: its earlier bars were never seen."""
        if t.mae_price is None or t.mae_utc is None or t.mfe_price is None or t.mfe_utc is None:
            if t.bars_held != 1:
                return
            ex = Excursion.start(t.entry, t.entry_ts)
        else:
            ex = Excursion(mae_price=t.mae_price, mae_utc=t.mae_utc, mfe_price=t.mfe_price, mfe_utc=t.mfe_utc)
        fav, adv = bar_prices(t.side, bar)
        if t.exit is not None and t.barrier is not None:
            fav, adv = exit_bar_extremes(t.side, fav, adv, t.exit, t.barrier)
        ex.fold(t.side, adv, ts, fav, ts)
        t.mae_price, t.mae_utc, t.mfe_price, t.mfe_utc = ex.mae_price, ex.mae_utc, ex.mfe_price, ex.mfe_utc
        if t.exit is not None:
            s = ex.summary(t.side, t.entry, t.stop)
            t.mae_r, t.mfe_r = s["mae_r"], s["mfe_r"]

    @staticmethod
    def _close(t: ShadowTrade, ts: pd.Timestamp, px: float, barrier: str) -> None:
        t.exit_ts, t.exit, t.barrier = ts, float(px), barrier
        if t.state is not None and t.policy is not None:
            t.ret = policy_return(t.side, t.entry, t.exit, t.state.scale_exit, t.policy.scale_fraction)
        else:
            t.ret = t.side * (t.exit - t.entry) / t.entry

    # ------------------------------------------------------------------ results
    def stats(self, version: str, now: pd.Timestamp, evidence_start: pd.Timestamp | None = None) -> PerfStats:
        """Clustered record of `version`'s taken trades. evidence_start (H-52, promotion.EVIDENCE_START_UTC): trades
        opened before it are quarantined (`promotion.quarantine_reason`) and left out of the trades and clusters."""
        book = self.books[version]
        weeks = max((now - book.started_utc).total_seconds() / (7 * 86400), 0.0)
        taken = [t for t in book.closed if t.taken and t.ret is not None
                 and quarantine_reason(t.entry_ts, evidence_start) is None]
        rets = pd.DataFrame({"ret": [t.ret for t in taken]})
        if rets.empty:
            return PerfStats(n_trades=0, sharpe_ann=0.0, hit_rate=0.0, max_dd=0.0, trades_per_week=0.0, weeks=weeks,
                             n_clusters=0)
        span = max(weeks, 1 / 7)
        per_week = len(rets) / span
        s = summarize(rets, trades_per_year=per_week * 52)
        # ruling 2026-10-19: one cluster per agent per session day; Sharpe on the cluster sums, a robust t
        days = opportunity_day(pd.DatetimeIndex([t.entry_ts for t in taken]))
        keys = [(t.agent_id, d) for t, d in zip(taken, days, strict=True)]
        sums = cluster_sums(rets["ret"].tolist(), keys)
        sharpe = sharpe_annualised(sums, len(sums) / span * 52)
        return PerfStats(n_trades=s["n"], sharpe_ann=sharpe, hit_rate=s["hit_rate"], max_dd=s["max_dd"],
                         trades_per_week=per_week, weeks=weeks, mean_ret=s["mean_ret"], std_ret=s["std_ret"],
                         n_clusters=len(sums), t_stat=cluster_t_stat(rets["ret"].tolist(), keys))

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
            (self.dir / f"shadow_{version}.json").write_text(
                self.stats(version, now, self.evidence_start).model_dump_json())


def score(model: Any, X: pd.DataFrame) -> tuple[float, float | None]:
    """(calibrated p, raw score or None) for one row: a MetaLabelModel exposes its raw score, so the shadow book can
    keep it for later recalibration; any other model gives p only."""
    raw_fn, cal_fn = getattr(model, "predict_raw", None), getattr(model, "calibrated", None)
    if raw_fn is None or cal_fn is None:
        return float(model.predict(X)[0]), None
    raw = np.asarray(raw_fn(X), dtype=float)
    return float(np.asarray(cal_fn(raw), dtype=float)[0]), float(raw[0])


def record_candidate(book: ShadowBook, *, version: str, model: Any, agent: Specialist, tf: str, bar: pd.Series,
                     Xm: pd.DataFrame, last: int, side: int, atr_usd: float, cost_atr: float,
                     entry: float | None = None, limits_profile: str | None = None,
                     candidate: pd.Series | None = None) -> ShadowTrade | None:
    """Score `agent`'s candidate on the decision bar `bar` (row `last` of the scoring frame `Xm`) with `version`'s model
    and record it. Every candidate is recorded (P9 counterfactual shadow): the ones below the threshold too, flagged not
    taken, so a recalibration sees an unbiased sample; only taken ones count as shadow trades. Entry: the bar's close
    on the trade's side, or `entry` (a TradingView agent's: the price when its value arrived, engine/tv_context.py).
    `candidate`: the candidates-frame row, whose own barriers the trade takes (`candidate_barriers`)."""
    feats = Xm.drop(columns=["ts_utc"]).iloc[[last]].replace([np.inf, -np.inf], np.nan)
    feats["side"] = side
    cols = [c for c in model.feature_names if c in feats.columns] if model.feature_names else list(feats.columns)
    p, p_raw = score(model, feats[cols])
    b = candidate_barriers(agent, candidate, atr_usd, cost_atr)
    threshold = breakeven_prob(b.target_r, b.stop_r, b.cost_r) + 0.02
    px = float(bar["ask_close"] if side > 0 else bar["bid_close"]) if entry is None else float(entry)
    return book.open_trade(version=version, agent_id=agent.agent_id, side=side, bar_ts=pd.Timestamp(bar["ts_utc"]),
                           entry=px, atr_usd=b.r_usd, target_atr=b.target_r, stop_atr=b.stop_r,
                           max_bars=b.max_bars, p=p, timeframe=tf, threshold=threshold, taken=p > threshold,
                           p_raw=p_raw, policy=agent.exit_spec, signal_close=pd.Timestamp(bar["visible_at"]),
                           scored_feature_version=str(Xm.attrs.get("feature_version", "")),
                           limits_profile=limits_profile, barriers_on_1m=agent.barriers_on_1m)
