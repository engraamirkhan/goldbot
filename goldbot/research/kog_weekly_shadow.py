"""The KoG weekly RED BOX TARGETS forward shadow (prereg-2026-11-23-kog-weekly-redbox.md sections 3, 4 and 7.1):
"Shadow only: never an order, never a promotion source, never real money". Called by the nightly `tva_scores` job
(research/tv_ideas.py `run_job`) after the ideas are scored; rows go to aux.db `kog_weekly_shadow`. This module
places no order, imports nothing from the engine, execution or RiskGate (a test checks the import graph) and feeds
no arm, feature or model.

The frozen extractor stays byte-identical in research/kog_weekly.py (kogw-1, pinned by sha in the card); this module
only calls `is_weekly_report` and `extract` on each post's first snapshot (the `tv_ideas` row's text).

Section 3, as scored here (no tunable parameter):
* Setups: "A setup is used only if both sides of its post were extracted, because the stop needs the box"; otherwise
  one post row `one_side_only` (or `extractor_abstain`), written at once with kogw-1's reasons.
* Window: "From `available_utc` to the broker's Friday close minus 5 minutes of the first broker trading week with
  bars after `available_utc`" (server clock NY+7: Friday 23:55). One post covers one week.
* Signal: the first 1h (primary) or 15m bar that closes strictly beyond the break level on its mid close, inside the
  window, among the bars that can see the post (`asof_join` of the post's `available_utc` on the bar's close). NY+7 is
  a whole-hour offset of UTC, so the server-clock 15m and 1h bars are the UTC ones. Each side fires at most once.
* Entry at the open of the first 1m bar at or after the signal close + 60 s, ask for a long, bid for a short; none if
  that bar is at or after the window end (`entry_after_window`). Stop = the opposite break level; R = |fill - stop|,
  under $3 refused (`r_below_3`). Targets at or behind the fill dropped (none left: `no_targets_left`); 1/n exits at
  each target, filled at the target price when the 1m bid (long) / ask (short) touches it; the stop first when both
  print in one minute; a gap through the stop fills at the minute's open; the rest exits at the last 1m close before
  the window end (bid for a long, ask for a short).
* Net R = the sum of the 1/n parts in initial-R units. C4: the bid/ask fills carry the spread; `costs.extra_usd` once
  on the full size; STOP_ADDON_USD on the fraction stopped; swap per part for every rollover strictly inside its hold,
  "never credited". Stress (`r_stress`) = 1.5 x the added costs (extra and stop add-on); the median-spread shortfall
  under $0.129 is a population term the reader applies from `spread_usd`. `r_gross` adds back every cost (spread,
  extra, add-on, swap).

Controls (section 4), all seeded with `numpy.random.SeedSequence([zlib.crc32(str(idea_id).encode()), r])`:
* (a) r = 0..199: the whole block (both levels, both ladders) shifted by U(-w, +w), w = the box width, the same rule
  run on the shifted levels; one value per replicate (None: it did not fire or was refused).
* (c) r = 2000 + stream: a coin; heads keeps the event, tails takes the other side at the same entry minute with the
  stop and every target mirrored about its fill at the event's distances.
* P4 twins r = 1000 + stream: 20 drawn without replacement among the same TF's decision bars of the 7 days after the
  event's exit, at the event bar's server hour +- 1 h, never a bar where a KWR signal fires; same side, entry lag,
  costs and clock rule; the stop and each target at the event's own ATR14 multiple (on the event's timeframe) of the
  twin's ATR14.
  stream = 2 x tf index (1h 0, 15m 1) + side index (long 0, short 1).

A post is scored once its week has closed plus 7 days (the twins' week) and the minutes are in. A hole in the
minutes (tv_ideas `bad_gaps`) waits, and 5 weekdays later is final `no_bars`. Every row is final when written and
never rewritten. No idea text and no price level is in any row or log line; `risk_usd` is a distance.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from collections.abc import Collection, Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.store import asof_join
from goldbot.data.timeutil import NY_PLUS_7, server_to_utc, server_wall
from goldbot.labels.triple_barrier import rollover_nights
from goldbot.research.kog_weekly import EXTRACTOR_VERSION, extract, is_weekly_report
from goldbot.research.tv_ideas import (
    BREAK_CLOCK,
    MAX_GAP,
    MINUTE,
    STRESS,
    Minutes,
    ScoreCosts,
    add_trading_days,
    bad_gaps,
)

if TYPE_CHECKING:
    from goldbot.db.connection import Database
    from goldbot.risk.windows import HolidayList

__all__ = ["N_SHIFT", "N_TWINS", "ShadowRow", "TfBars", "block_shift", "save_shadow", "score_shadow", "score_side",
           "stored_post_ids", "stored_rows", "tf_bars", "twin_candidates", "twin_geometry", "window_end"]

Side = Literal["long", "short"]
Tf = Literal["1h", "15m"]
Reason = Literal["one_side_only", "extractor_abstain", "no_signal", "r_below_3", "no_targets_left",
                 "entry_after_window", "no_bars"]

# ---- the card's constants (sections 3-4; none is tunable)
TFS: dict[Tf, pd.Timedelta] = {"1h": pd.Timedelta(hours=1), "15m": pd.Timedelta(minutes=15)}
ENTRY_LAG = pd.Timedelta(seconds=60)                 # entry at the signal bar's close + 60 s
CLOSE_BEFORE = pd.Timedelta(minutes=5)               # window end = the broker's Friday close (24:00 NY+7) - 5 min
MIN_R_USD = 3.0                                      # R below $3: refused
STOP_ADDON_USD = 0.145                               # C4: added on each stop exit, per oz
N_SHIFT = 200                                        # control (a) replicates per post
N_TWINS = 20                                         # P4 twins per event
TWIN_DAYS = pd.Timedelta(days=7)                     # twins from the 7 days after the event's exit
TWIN_HOURS = 1                                       # server hour +- 1
ATR_N = 14
HOLE_FINAL_DAYS = 5                                  # a holed week is final `no_bars` 5 weekdays after the horizon
SCORER_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
_SIDES: tuple[Side, ...] = ("long", "short")


# ------------------------------------------------------------------------------------------------- rows
class ShadowRow(FrozenRecord):
    """One (post, side, timeframe) of the shadow, or one post row (side and tf '-') for a post refused whole. Holds no
    idea text and no price level: times, R multiples, a spread paid and counts only."""
    idea_id: int
    side: Literal["long", "short", "-"]
    tf: Literal["1h", "15m", "-"]
    published_utc: UtcTimestamp
    available_utc: UtcTimestamp
    window_end_utc: UtcTimestamp | None = None
    status: Literal["scored", "refused"]
    reason: Reason | None = None
    extractor: str = EXTRACTOR_VERSION
    extractor_abstain: str | None = None             # kogw-1's reasons on this post, comma separated
    signal_utc: UtcTimestamp | None = None
    fill_utc: UtcTimestamp | None = None
    exit_utc: UtcTimestamp | None = None             # the last part's exit
    n_targets: int | None = None
    frac_stopped: float | None = None
    r_net: float | None = None
    r_gross: float | None = None
    r_stress: float | None = None
    spread_usd: float | None = None                  # the spread paid, $/oz (entry half + the parts' exit halves)
    risk_usd: float | None = None                    # R = |fill - stop|, $/oz: a distance, never a level
    control_a_mean: float | None = None
    control_a_n: int | None = None
    control_a_r: tuple[float | None, ...] = ()
    control_c_r: float | None = None
    control_c_flipped: bool | None = None
    twin_mean_r: float | None = None
    twin_n: int | None = None
    twin_r: tuple[float, ...] = ()
    cost_source: str
    scorer_sha: str = SCORER_SHA


class Outcome(FrozenRecord):
    """One scored ladder: R multiples, its exits and what it paid."""
    signal_utc: UtcTimestamp | None = None
    fill_utc: UtcTimestamp
    exit_utc: UtcTimestamp
    n_targets: int = Field(ge=1)
    frac_stopped: float
    r_net: float
    r_gross: float
    r_stress: float
    spread_usd: float
    fill: float                                      # in memory only (twins and control c); never stored
    risk: float
    targets: tuple[float, ...]
    stop: float


# ------------------------------------------------------------------------------------------------- bars and clock
class TfBars:
    """1h or 15m mid bars from the 1m minutes, stamped by close (UTC ns), with the bar's server open hour and ATR14
    (simple mean of 14 true ranges, available at the bar's close). Only complete bars."""

    def __init__(self, close_ns: np.ndarray, hour: np.ndarray, close: np.ndarray, atr: np.ndarray, step_ns: int):
        self.close_ns, self.hour, self.close, self.atr, self.step_ns = close_ns, hour, close, atr, step_ns
        self.n = len(close_ns)

    def close_ts(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.close_ns.astype("datetime64[ns]"), tz="UTC")


def tf_bars(m: Minutes, tf: Tf) -> TfBars:
    step = int(TFS[tf].value)
    if m.n == 0:
        z = np.zeros(0)
        return TfBars(np.zeros(0, dtype=np.int64), np.zeros(0, dtype=int), z, z, step)
    key = m.ts_ns // step
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    lasts = np.r_[starts[1:] - 1, m.n - 1]
    mid_h, mid_l = (m.bh + m.ah) / 2, (m.bl + m.al) / 2
    high, low = np.maximum.reduceat(mid_h, starts), np.minimum.reduceat(mid_l, starts)
    close = (m.bc[lasts] + m.ac[lasts]) / 2
    close_ns = (key[starts] + 1) * step
    keep = close_ns <= m.ts_ns[-1] + MINUTE.value                    # the last bar only once complete
    prev = np.r_[np.nan, close[:-1]]
    tr = np.nanmax(np.c_[high - low, np.abs(high - prev), np.abs(low - prev)], axis=1)
    atr = pd.Series(tr).rolling(ATR_N, min_periods=ATR_N).mean().to_numpy()
    opens = pd.DatetimeIndex((key[starts] * step).astype("datetime64[ns]"), tz="UTC")
    hour = server_wall(opens, NY_PLUS_7).hour.to_numpy()
    return TfBars(close_ns[keep], hour[keep], close[keep], atr[keep], step)


def window_end(m: Minutes, available: pd.Timestamp) -> pd.Timestamp | None:
    """The broker's Friday close (24:00 NY+7) minus 5 minutes of the first trading week with a minute at or after
    `available`; None while no such minute exists."""
    i = m.at_or_after(available)
    if i >= m.n:
        return None
    wall = server_wall(m.ts(i), NY_PLUS_7)[0]
    end = wall.normalize() + pd.Timedelta(days=(4 - wall.dayofweek) % 7 + 1) - CLOSE_BEFORE
    if wall >= end:
        end += pd.Timedelta(days=7)
    return server_to_utc(pd.DatetimeIndex([end]), NY_PLUS_7)[0]


def _sees(tb: TfBars, available: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
    """Bars that can see the post (asof_join of its `available_utc` on the bar's close) and close inside the window."""
    if tb.n == 0:
        return np.zeros(0, dtype=bool)
    left = pd.DataFrame({"ts_utc": tb.close_ts(), "_pos": np.arange(tb.n)})
    j = asof_join(left, pd.DataFrame({"available_utc": [available], "_seen": [1.0]}))
    seen = j.sort_values("_pos")["_seen"].notna().to_numpy()
    return seen & (tb.close_ns <= end.value)


# ------------------------------------------------------------------------------------------------- the ladder
def _ladder(m: Minutes, i: int, side: int, stop: float, targets: Sequence[float], end: pd.Timestamp,
            costs: ScoreCosts) -> Outcome:
    """Section 3's exits from entry minute `i` (filled at its open) up to the window `end`: 1/n at each target, the stop
    first in a shared minute, the rest at the last close before `end`."""
    fill = float(m.ao[i] if side > 0 else m.bo[i])
    half_in = float(m.ao[i] - m.bo[i]) / 2
    iend = max(m.at_or_after(end), i + 1)
    if side > 0:
        stop_hit = m.bl[i:iend] <= stop
    else:
        stop_hit = m.ah[i:iend] >= stop
    hs = np.flatnonzero(stop_hit)
    ks = i + int(hs[0]) if hs.size else None
    n = len(targets)
    swap_night = min(0.0, float(costs.swap.usd_per_oz_at(np.array([side]), fill)[0]))   # never credited
    t_in = m.ts(i)
    parts: list[tuple[float, pd.Timestamp, float, bool]] = []                           # (px, t_out, half_out, stop)
    for tgt in targets:
        th = np.flatnonzero(m.bh[i:iend] >= tgt) if side > 0 else np.flatnonzero(m.al[i:iend] <= tgt)
        kt = i + int(th[0]) if th.size else None
        if kt is not None and (ks is None or kt < ks):
            parts.append((float(tgt), m.ts(kt), float(m.ao[kt] - m.bo[kt]) / 2, False))
        elif ks is not None:
            opn = float(m.bo[ks] if side > 0 else m.ao[ks])
            px = stop if ks == i else (min(stop, opn) if side > 0 else max(stop, opn))
            parts.append((px, m.ts(ks), float(m.ao[ks] - m.bo[ks]) / 2, True))
        else:
            j = iend - 1
            parts.append((float(m.bc[j] if side > 0 else m.ac[j]), m.ts(j) + MINUTE,
                          float(m.ac[j] - m.bc[j]) / 2, False))
    outs = pd.DatetimeIndex([p[1] for p in parts])
    nights = rollover_nights(pd.DatetimeIndex([t_in] * n), outs, costs.swap.server_tz, costs.swap.triple_weekday,
                             costs.swap.seven_night)
    risk = abs(fill - stop)
    frac_stopped = sum(p[3] for p in parts) / n
    move = sum(side * (p[0] - fill) for p in parts) / n
    swap = swap_night * float(nights.sum()) / n
    added = costs.extra_usd + STOP_ADDON_USD * frac_stopped
    net = move + swap - added
    spread = half_in + sum(p[2] for p in parts) / n
    return Outcome(fill_utc=t_in, exit_utc=max(outs), n_targets=n, frac_stopped=frac_stopped, r_net=net / risk,
                   r_gross=(net + added - swap + spread) / risk, r_stress=(net - (STRESS - 1.0) * added) / risk,
                   spread_usd=spread, fill=fill, risk=risk, targets=tuple(targets), stop=stop)


def _score_side(m: Minutes, tb: TfBars, sees: np.ndarray, end: pd.Timestamp, side: Side, above: float, below: float,
                targets: Sequence[float], costs: ScoreCosts) -> Outcome | Reason:
    s = 1 if side == "long" else -1
    fired = sees & ((tb.close > above) if s > 0 else (tb.close < below))
    if not fired.any():
        return "no_signal"
    k = int(np.argmax(fired))
    sig = pd.Timestamp(int(tb.close_ns[k]), unit="ns", tz="UTC")
    i = m.at_or_after(sig + ENTRY_LAG)
    if i >= m.n or m.ts(i) >= end:
        return "entry_after_window"
    fill = float(m.ao[i] if s > 0 else m.bo[i])
    stop = below if s > 0 else above
    if s * (fill - stop) < MIN_R_USD:
        return "r_below_3"
    left = [t for t in targets if s * (t - fill) > 0]
    if not left:
        return "no_targets_left"
    return _ladder(m, i, s, stop, left, end, costs).model_copy(update={"signal_utc": sig})


def score_side(m: Minutes, tb: TfBars, *, available: pd.Timestamp, window_end: pd.Timestamp, side: Side,
               above: float, below: float, targets: Sequence[float], costs: ScoreCosts) -> Outcome | Reason:
    """Section 3 for one side of one post on one timeframe: the outcome, or the refusal code."""
    return _score_side(m, tb, _sees(tb, available, window_end), window_end, side, above, below, targets, costs)


# ------------------------------------------------------------------------------------------------- controls
def _rng(idea_id: int, r: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([zlib.crc32(str(idea_id).encode()), r]))


def block_shift(idea_id: int, r: int, width: float) -> float:
    """Control (a) replicate r's shift of the whole block: U(-w, +w)."""
    return float(_rng(idea_id, r).uniform(-width, width))


def twin_geometry(*, fill: float, side: int, event_fill: float, event_stop: float, event_targets: Sequence[float],
                  event_atr: float, twin_atr: float) -> tuple[float, tuple[float, ...]]:
    """P4: the twin's stop and targets, each at the event's own ATR14 multiple of the twin's ATR14."""
    stop = fill - side * abs(event_fill - event_stop) / event_atr * twin_atr
    return stop, tuple(fill + side * abs(t - event_fill) / event_atr * twin_atr for t in event_targets)


def twin_candidates(tb: TfBars, *, event_open_ns: int, exit_ns: int, fires: Collection[int]) -> np.ndarray:
    """P4's eligible decision bars: closing in (exit, exit + 7 days], at the event bar's server hour +- 1 h, never a
    bar where a KWR signal fires (`fires`: closes, ns), with an ATR14."""
    hour = int(server_wall(pd.Timestamp(event_open_ns, unit="ns", tz="UTC"), NY_PLUS_7).hour[0])
    dh = np.abs(tb.hour - hour)
    near = np.minimum(dh, 24 - dh) <= TWIN_HOURS
    when = (tb.close_ns > exit_ns) & (tb.close_ns <= exit_ns + TWIN_DAYS.value)
    free = ~np.isin(tb.close_ns, np.fromiter(fires, dtype=np.int64, count=len(fires)))
    return np.flatnonzero(near & when & free & np.isfinite(tb.atr) & (tb.atr > 0))


def _twins(m: Minutes, tb: TfBars, ev: Outcome, s: int, idea_id: int, stream: int, fires: Collection[int],
           costs: ScoreCosts) -> list[float]:
    assert ev.signal_utc is not None
    k = int(np.searchsorted(tb.close_ns, ev.signal_utc.value))
    event_atr = float(tb.atr[k])
    if not np.isfinite(event_atr) or event_atr <= 0:
        return []
    cand = twin_candidates(tb, event_open_ns=int(tb.close_ns[k]) - tb.step_ns, exit_ns=ev.exit_utc.value,
                           fires=fires)
    if cand.size == 0:
        return []
    pick = np.sort(_rng(idea_id, 1000 + stream).choice(cand, size=min(N_TWINS, cand.size), replace=False))
    out: list[float] = []
    for c in pick:
        i = m.at_or_after(pd.Timestamp(int(tb.close_ns[c]), unit="ns", tz="UTC") + ENTRY_LAG)
        end = window_end(m, pd.Timestamp(int(tb.close_ns[c]), unit="ns", tz="UTC"))
        if i >= m.n or end is None or m.ts(i) >= end:
            continue
        fill = float(m.ao[i] if s > 0 else m.bo[i])
        stop, tg = twin_geometry(fill=fill, side=s, event_fill=ev.fill, event_stop=ev.stop, event_targets=ev.targets,
                                 event_atr=event_atr, twin_atr=float(tb.atr[c]))
        out.append(_ladder(m, i, s, stop, tg, end, costs).r_net)
    return out


def _coin(m: Minutes, ev: Outcome, s: int, idea_id: int, stream: int, end: pd.Timestamp,
          costs: ScoreCosts) -> tuple[float, bool]:
    """Control (c): keep the event, or take the other side at its entry minute with mirrored distances."""
    if int(_rng(idea_id, 2000 + stream).integers(2)) == 0:
        return ev.r_net, False
    i = m.at_or_after(ev.fill_utc)
    o = -s
    fill = float(m.ao[i] if o > 0 else m.bo[i])
    stop = fill - o * abs(ev.fill - ev.stop)
    tg = [fill + o * abs(t - ev.fill) for t in ev.targets]
    return _ladder(m, i, o, stop, tg, end, costs).r_net, True


# ------------------------------------------------------------------------------------------------- the scorer
def _visible(b1: pd.DataFrame, now: pd.Timestamp, holdout: tuple[pd.Timestamp, pd.Timestamp] | None) -> Minutes:
    if b1.empty:
        return Minutes(np.zeros(0, dtype=np.int64), {f"{s}_{c}": np.zeros(0) for s in ("bid", "ask")
                                                     for c in ("open", "high", "low", "close")})
    t = pd.to_datetime(b1["ts_utc"], utc=True)
    keep = t < now
    if holdout is not None:                                            # no holdout minute is ever read
        keep &= ~((t >= holdout[0]) & (t < holdout[1]))
    vis = b1[keep]
    return Minutes.from_frame(vis) if not vis.empty else _visible(pd.DataFrame(), now, None)


def score_shadow(posts: pd.DataFrame, b1: pd.DataFrame, costs: ScoreCosts, *, since: pd.Timestamp,
                 now: pd.Timestamp, skip: Collection[int] = (),
                 holdout: tuple[pd.Timestamp, pd.Timestamp] | None = None,
                 holidays: HolidayList | None = None) -> list[ShadowRow]:
    """The rows of every KoG weekly report in `posts` (tv_ideas `read_ideas`) available at or after `since` and not in
    `skip`, scored on the 1m bid/ask minutes of `b1` visible before `now`. A post whose week (plus the twins' week)
    has not closed yet gives no row. Module docstring for the rules."""
    m = _visible(b1, now, holdout)
    bars = {tf: tf_bars(m, tf) for tf in TFS}
    weekly = posts[[is_weekly_report(str(p["author"]), str(p["title"]), pd.Timestamp(p["published_utc"]))
                    for p in cast(list[dict[str, Any]], posts.to_dict("records"))]] if len(posts) else posts
    weekly = weekly[pd.to_datetime(weekly["available_utc"], utc=True) >= since] if len(weekly) else weekly
    if holdout is not None and len(weekly):
        av = pd.to_datetime(weekly["available_utc"], utc=True)
        weekly = weekly[~((av >= holdout[0]) & (av < holdout[1]))]
    holes = bad_gaps(m.ts_ns, BREAK_CLOCK, holidays)
    # pass 1: every post's setups, window and signals (the twins exclude every KWR signal bar)
    plans: list[tuple[dict[str, Any], Any, pd.Timestamp | None]] = []
    fires: dict[Tf, set[int]] = {tf: set() for tf in TFS}
    first = m.ts(0) if m.n else None
    for p in cast(list[dict[str, Any]], weekly.to_dict("records")):
        ex = extract(str(p["description"]))
        # a post available before the first loaded minute has no window in these minutes (the job loads from the
        # earliest post not stored yet): no signals from it, and it is not scored here
        inside = first is not None and pd.Timestamp(p["available_utc"]) >= first
        end = window_end(m, pd.Timestamp(p["available_utc"])) if inside else None
        plans.append((p, ex, end))
        sides = {s.side: s for s in ex.setups}
        if end is None or len(sides) != 2:
            continue
        for tf, tb in bars.items():
            sees = _sees(tb, pd.Timestamp(p["available_utc"]), end)
            for side, lvl in (("long", sides["long"].level), ("short", sides["short"].level)):
                hit = sees & ((tb.close > lvl) if side == "long" else (tb.close < lvl))
                if hit.any():
                    fires[tf].add(int(tb.close_ns[int(np.argmax(hit))]))
    rows: list[ShadowRow] = []
    last = m.ts(m.n - 1) if m.n else None
    for p, ex, end in plans:
        iid = int(p["idea_id"])
        if iid in skip:
            continue
        base: dict[str, Any] = dict(idea_id=iid, published_utc=p["published_utc"], available_utc=p["available_utc"],
                                    extractor_abstain=",".join(ex.abstain) or None, cost_source=costs.source)
        sides = {s.side: s for s in ex.setups}
        if len(sides) != 2:
            reason: Reason = "one_side_only" if sides else "extractor_abstain"
            rows.append(ShadowRow(side="-", tf="-", status="refused", reason=reason, **base))
            continue
        if end is None or last is None:
            continue
        horizon = end + TWIN_DAYS
        if not (m.at_or_after(horizon) < m.n or last >= horizon - MAX_GAP):
            continue                                                   # the week plus the twins' week not in yet
        base["window_end_utc"] = end
        i0, i1 = m.at_or_after(pd.Timestamp(p["available_utc"])), m.at_or_after(horizon)
        if holes[i0 + 1: i1 + 1].any():
            if now >= add_trading_days(horizon, HOLE_FINAL_DAYS):
                rows.append(ShadowRow(side="-", tf="-", status="refused", reason="no_bars", **base))
            continue
        rows.extend(_score_post(m, bars, p, sides["long"], sides["short"], end, fires, base, costs))
    return rows


def _score_post(m: Minutes, bars: dict[Tf, TfBars], p: dict[str, Any], lo: Any, sh: Any, end: pd.Timestamp,
                fires: dict[Tf, set[int]], base: dict[str, Any], costs: ScoreCosts) -> list[ShadowRow]:
    iid, avail = int(p["idea_id"]), pd.Timestamp(p["available_utc"])
    width = float(lo.level - sh.level)
    shifts = [block_shift(iid, r, width) for r in range(N_SHIFT)]
    out: list[ShadowRow] = []
    for ti, (tf, tb) in enumerate(bars.items()):
        sees = _sees(tb, avail, end)
        for si, side in enumerate(_SIDES):
            setup = lo if side == "long" else sh
            stream = 2 * ti + si
            # control (a) on every side, fired or not: replicates of fired sides only would bias delta-a toward 0
            reps: list[float | None] = []
            for d in shifts:
                rep = _score_side(m, tb, sees, end, side, lo.level + d, sh.level + d,
                                  [t + d for t in setup.targets], costs)
                reps.append(None if isinstance(rep, str) else rep.r_net)
            fired = [x for x in reps if x is not None]
            ctrl: dict[str, Any] = dict(control_a_mean=float(np.mean(fired)) if fired else None,
                                        control_a_n=len(fired), control_a_r=tuple(reps))
            got = _score_side(m, tb, sees, end, side, lo.level, sh.level, setup.targets, costs)
            if isinstance(got, str):
                out.append(ShadowRow(side=side, tf=tf, status="refused", reason=got, **ctrl, **base))
                continue
            s = 1 if side == "long" else -1
            twins = _twins(m, tb, got, s, iid, stream, fires[tf], costs)
            c_r, flipped = _coin(m, got, s, iid, stream, end, costs)
            out.append(ShadowRow(
                side=side, tf=tf, status="scored", signal_utc=got.signal_utc, fill_utc=got.fill_utc,
                exit_utc=got.exit_utc, n_targets=got.n_targets, frac_stopped=got.frac_stopped, r_net=got.r_net,
                r_gross=got.r_gross, r_stress=got.r_stress, spread_usd=got.spread_usd, risk_usd=got.risk,
                control_c_r=c_r, control_c_flipped=flipped, **ctrl,
                twin_mean_r=float(np.mean(twins)) if twins else None, twin_n=len(twins), twin_r=tuple(twins),
                **base))
    return out


# ------------------------------------------------------------------------------------------------- storage
_COLUMNS = tuple(ShadowRow.model_fields)
_JSON = frozenset({"control_a_r", "twin_r"})


def _db_value(k: str, v: Any) -> Any:
    if k in _JSON:
        return json.dumps(list(v))
    if isinstance(v, pd.Timestamp):
        return v.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(v, bool):
        return int(v)
    return v


def save_shadow(aux: Database, rows: Iterable[ShadowRow], *, scored_utc: pd.Timestamp) -> int:
    """Insert `rows` into aux.db `kog_weekly_shadow`; a (post, side, tf) already stored is never rewritten (every row
    is final when written), so re-running a night writes nothing. Returns the rows written."""
    cols = [*_COLUMNS, "scored_utc"]
    sql = f"INSERT OR IGNORE INTO kog_weekly_shadow ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    n = 0
    with aux.write() as conn:
        for row in rows:
            cur = conn.execute(sql, [_db_value(c, getattr(row, c)) for c in _COLUMNS] + [_db_value("", scored_utc)])
            n += cur.rowcount
    return n


def stored_rows(aux: Database) -> list[ShadowRow]:
    with aux.connection() as conn:
        conn.row_factory = sqlite3.Row
        got = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM kog_weekly_shadow ORDER BY idea_id, tf, side").fetchall()
    out = []
    for r in got:
        d = {c: r[c] for c in _COLUMNS}
        for c in _JSON:
            d[c] = tuple(json.loads(d[c]))
        if d["control_c_flipped"] is not None:
            d["control_c_flipped"] = bool(d["control_c_flipped"])
        out.append(ShadowRow(**d))
    return out


def stored_post_ids(aux: Database) -> set[int]:
    with aux.connection() as conn:
        return {int(r[0]) for r in conn.execute("SELECT DISTINCT idea_id FROM kog_weekly_shadow")}


def summarise(rows: Iterable[ShadowRow]) -> dict[str, Any]:
    """Counts only (rows by status and reason): the shadow is descriptive and read once by the card's rule, so the
    nightly job reports no running R."""
    by: dict[str, int] = {}
    for r in rows:
        key = r.status if r.reason is None else f"{r.status}:{r.reason}"
        by[key] = by.get(key, 0) + 1
    return dict(sorted(by.items()))
