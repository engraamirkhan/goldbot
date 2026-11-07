"""TradingView author ideas: the frozen shadow scorer the pre-registration owes (prereg-2026-10-28-tv-author-ideas.md
section 6, amendments T-1 and T-2; docs/research/tv-ideas-score-2026-11-06.md section 5.2). Scheduler job `tva_scores`, nightly
after the bars are in. It places no order, has no RiskGate path and feeds no engine, arm or feature: the forward read
(prereg 3.3) is "on the shadow score of all ideas, never on arm fills".

What it scores, per idea of a followed author (settings `tv_ideas.authors`), from brain state/tv_ideas.db opened
read-only (`mode=ro`):
* Only the first-seen snapshot: "For live ideas, only the first-seen snapshot is ever scored. A later edit cannot
  change a live score" (prereg 1.5). The text is the `tv_ideas` row (the collector never overwrites a first release).
* Live ideas only (`backfilled = 0`): back-catalogue rows are the BC screen's, counted here as `backfilled`.
* Gold only: spot symbols on our XAUUSD minutes; futures (GC, MGC) "direction-only ... The levels are discarded, never
  shifted"; any other symbol `not_gold`, counted.
* The holdout is refused: an idea published or available inside `research.holdout_from..holdout_to` is `holdout`,
  never scored ("The scorer refuses HO-window ideas through the existing `holdout_window` guard", prereg 5).
* Edited = the text changed after first sight (more than one `tv_idea_versions` hash). The collector's own `edited`
  flag also counts the author's later notes ("target reached"); using it would drop winners from the decision read,
  a selection on the outcome. Amendment T-2 (7): "edited live ideas stay in the decision read, edited-excluded mean
  as sensitivity"; `edited` is recorded for that read.
* Decision-eligible (T-2 (6)): resolved, labelled long or short, and available at or after the dated admission of
  the author's extractor; earlier ideas are descriptive only.
* Minutes (T-2 (2)): a hole over 15 minutes in the hold, outside gold's daily break (23:55-01:05 NY+7) and the weekly
  close and not on one listed CME holiday, makes the idea `no_bars`, rescored nightly; still holed 5 weekdays after
  its horizon it is final `no_bars_final`, counted, never decision-eligible. R needs 14 whole server weekdays among
  the last 20 (T-2 (4)), else `no_bars`. No holdout minute is read.

Point in time (T-2 (1)): the entry is the first 1m minute at or after the idea's `available_utc` (we held its text;
the prereg's `first_seen_utc` is the listing sighting, at or before it, so this is the stricter of the two), found
with `asof_join` on `available_utc`; R = ATR14 of daily bars (the broker's server day, mid prices) joined with
`asof_join` on each day's close, so "as of the prior daily close". A descriptive second entry at `published_utc + 60
s` gives the capture loss (prereg 1.2).

Direction (amendment T-1): "Every admitted idea is scored as type D"; the side comes from a FROZEN extractor of the
first snapshot's text, admitted per author only after it passes the outcome-blind audit ("precision >= 95% ... and
coverage >= 50%"). The direction audit of 2026-10-31 failed and parked the line, so `ADMITTED_EXTRACTORS` is empty
and every idea is `unlabelled`, counted. Never an LLM ("Not used: an LLM classifier"), never the collector's parsed
direction or levels (both failed audit). An admitted extractor is recorded with the sha of its source on every row.

Reads per idea:
* Direction read (1.3(b)): side x (exit - fill) net of costs, in R = ATR14; horizon I 1 / D 5 / W 20 trading days; a
  catastrophic stop at 2R on 1m bid/ask. Scored for BOTH sides whatever the label: `r_long` is the drift baseline and
  (r_long + r_short) / 2 the random-direction baseline at the author's own timestamps (prereg 2); `r_side` is the
  labelled side's.
* Levels read (1.2-1.3(a)), descriptive since T-1, only when an admitted extractor yields entry, stop and T1 on spot:
  market or limit, fill window I 1 / D 3 / W 10 trading days, `no_fill` and `invalidated` score 0 R, stop first when
  both barriers print in one minute, maximum hold I 3 / D 10 / W 20, R = |fill - stop|; the 1.1 sanity rules demote
  to direction only (`demoted`).
* Costs (1.4): the bar spread at entry and exit (fills at ask / bid), plus `research_costs` (the canonical broker's
  measured extra cost when its table exists, else the priors; the source line is stored), plus swap for every
  rollover held (`SwapSpec`, triple on its weekday); the 1.5x stress on spread and extra, swap unchanged.

Every constant below is the pre-registration's: "The scorer has no tunable parameter". The rows go to aux.db
`tva_scores`; a final row is never rescored. The job reports counts and progress toward the read (>= 100 resolved
ideas and >= 60 publish days per author), never a running skill statistic: the read is taken once, by its rule.
"""
from __future__ import annotations

import hashlib
import inspect
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.store import asof_join
from goldbot.data.timeutil import NY_PLUS_7, epoch_ns, server_wall
from goldbot.labels.triple_barrier import SwapSpec, rollover_nights

if TYPE_CHECKING:
    from goldbot.config import Settings
    from goldbot.db.connection import Database
    from goldbot.execution.costs import CostTable
    from goldbot.risk.windows import HolidayList


Bucket = Literal["I", "D", "W"]
Label = Literal["long", "short", "abstain", "unlabelled"]
SymbolClass = Literal["spot", "futures", "other"]
Status = Literal["scored", "pending", "no_bars", "no_bars_final", "backfilled", "not_gold", "holdout"]
LevelsStatus = Literal["target", "stop", "time", "pending", "no_fill", "invalidated", "demoted"]

# ---- the pre-registration's constants (prereg 1.1-1.4; none is tunable)
HORIZON_DAYS: dict[str, int] = {"I": 1, "D": 5, "W": 20}       # 1.3(b) direction read horizon, trading days
FILL_WINDOW_DAYS: dict[str, int] = {"I": 1, "D": 3, "W": 10}   # 1.2 limit validity
MAX_HOLD_DAYS: dict[str, int] = {"I": 3, "D": 10, "W": 20}     # 1.3(a) maximum hold
CAT_STOP_R = 2.0                                               # 1.3(b) catastrophic stop
ATR_DAYS = 14                                                  # R = ATR14 on daily bars: the 14 most recent ...
ATR_WINDOW_DAYS = 20                                           #   ... whole server weekdays among the last 20 (T-2 (4))
HOLE_FINAL_DAYS = 5                                            # T-2 (2): still holed 5 weekdays after the horizon: final
MARKET_BAND_ATR = 0.25                                         # 1.2 within 0.25 x ATR of the entry: market
SANITY_ENTRY_ATR = 3.0                                         # 1.1 |entry - price at publish| > 3 x ATR: demote
SANITY_MIN_STOP_RT = 2.0                                       # 1.1 stop distance below 2 x the round trip: demote
STRESS = 1.5                                                   # 1.4 cost stress on spread + extra
PUBLISH_LAG = pd.Timedelta(seconds=60)                         # 1.2 entry at published_utc + 60 s (capture read)
READ_MIN_IDEAS = 100                                           # 3.3 forward read: >= 100 resolved ideas ...
READ_MIN_DAYS = 60                                             #   ... and >= 60 publish-day clusters
MINUTE = pd.Timedelta(minutes=1)
MAX_GAP = pd.Timedelta(minutes=15)                             # T-2 (2): a hole longer than this -> no_bars ...
BREAK_FROM, BREAK_TO = pd.Timedelta(hours=23, minutes=55), pd.Timedelta(hours=1, minutes=5)   # ... outside the
#   server daily break 23:55-01:05 and the weekly close (Friday 23:55 to Monday 01:05, server time)
# the break's clock: gold's daily break is 17:00 New York, which is server midnight on NY+7. The configured broker
# clock (Europe/Athens) drifts an hour from it in the weeks the US and EU change clocks on different dates (measured
# 2026-10-26..30: the break sat at 22:58-00:02 Athens), and every day of those weeks would read as holed
BREAK_CLOCK = NY_PLUS_7
FULL_DAY_SHARE = 0.8                                           # a day with < 80% of the median day's minutes: no ATR
FINAL: frozenset[str] = frozenset({"scored", "no_bars_final", "backfilled", "not_gold", "holdout"})
SCORER_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
DB_FILE = "tv_ideas.db"                                        # = goldbot.data.tv_ideas.DB_FILE


# ------------------------------------------------------------------------------------------------- extractors
class Call(FrozenRecord):
    """A frozen extractor's read of one first-seen snapshot: a side or abstain, and the author's entry, stop and first
    target when it states all three."""
    label: Literal["long", "short", "abstain"]
    entry: float | None = None
    stop: float | None = None
    target: float | None = None

    @property
    def has_levels(self) -> bool:
        return self.entry is not None and self.stop is not None and self.target is not None


class Extractor(FrozenRecord):
    """A frozen direction (and levels) extractor of (title, description), admitted for an author at `admitted_utc` (the
    dated amendment). `sha` pins its source and admission time on every score row. Amendment T-2 (6): "forward
    universe per author = ideas with available_utc >= the dated amendment admitting that author's extractor; earlier
    ideas descriptive only"."""
    name: str
    fn: Callable[[str, str], Call]
    admitted_utc: UtcTimestamp

    @property
    def sha(self) -> str:
        body = inspect.getsource(self.fn) + "\n" + self.admitted_utc.isoformat()
        return hashlib.sha256(body.encode()).hexdigest()[:16]


# author (lower case) -> extractor. Empty: amendment T-1's one direction audit failed (tv-ideas-direction-audit-
# 2026-10-31.md) and parked the line to the Q1 refresh. An entry is added only with a dated amendment written before
# its outcome-blind audit, and the audit's pass (precision >= 95 %, coverage >= 50 %), under quant review
ADMITTED_EXTRACTORS: dict[str, Extractor] = {}


# ------------------------------------------------------------------------------------------------- costs and minutes
class ScoreCosts(FrozenRecord):
    """Prereg 1.4: the extra round-trip cost per oz beyond the bar spread, the swap, and the line naming the source."""
    extra_usd: float = Field(ge=0)
    swap: SwapSpec
    source: str

    @classmethod
    def from_research(cls, settings: Settings, table: CostTable | None) -> ScoreCosts:
        from goldbot.execution.costs import research_costs
        extra, swap, source = research_costs(settings, table)
        return cls(extra_usd=extra, swap=swap, source=source)


class Minutes:
    """1m bid/ask OHLC as numpy arrays, sorted by minute."""

    def __init__(self, ts_ns: np.ndarray, cols: Mapping[str, np.ndarray]):
        self.ts_ns = ts_ns
        self.bo, self.bh, self.bl, self.bc = (cols[f"bid_{c}"] for c in ("open", "high", "low", "close"))
        self.ao, self.ah, self.al, self.ac = (cols[f"ask_{c}"] for c in ("open", "high", "low", "close"))
        self.n = len(ts_ns)

    @classmethod
    def from_frame(cls, bars: pd.DataFrame) -> Minutes:
        b = bars.sort_values("ts_utc", kind="stable")
        ts = epoch_ns(pd.DatetimeIndex(pd.to_datetime(b["ts_utc"], utc=True)))
        cols = {f"{s}_{c}": b[f"{s}_{c}"].to_numpy(dtype=float) for s in ("bid", "ask")
                for c in ("open", "high", "low", "close")}
        return cls(ts, cols)

    def at_or_after(self, t: pd.Timestamp) -> int:
        """Index of the first minute at or after `t`; `n` when there is none yet."""
        return int(np.searchsorted(self.ts_ns, pd.Timestamp(t).value, side="left"))

    def ts(self, i: int) -> pd.Timestamp:
        return pd.Timestamp(int(self.ts_ns[i]), unit="ns", tz="UTC")

    def holes(self, clock: str, holidays: HolidayList | None = None) -> np.ndarray:
        """holes[i]: the minutes missing before minute i form a hole longer than MAX_GAP outside the daily break and
        the weekly close, and not on one listed holiday (`bad_gaps`)."""
        return bad_gaps(self.ts_ns, clock, holidays)


def bad_gaps(ts_ns: np.ndarray, server_tz: str, holidays: HolidayList | None = None) -> np.ndarray:
    """For each minute, True when the minutes missing just before it (from the previous minute's close) last more than
    MAX_GAP and do not lie inside one break: the daily break 23:55-01:05, or the weekly close from Friday 23:55 to
    Monday 01:05 (`server_tz` wall clock). T-2 (2): "A hole inside a date listed in config/holidays_cme_metals.yaml
    (CME trading day, NY+7; inside its validity only) is not a hole": the missing minutes, less the daily break at
    either end, all fall on one listed date the list covers."""
    out = np.zeros(len(ts_ns), dtype=bool)
    if len(ts_ns) < 2:
        return out
    missing = np.diff(ts_ns) - MINUTE.value
    for j in np.flatnonzero(missing > MAX_GAP.value):
        start = pd.Timestamp(int(ts_ns[j]), unit="ns", tz="UTC") + MINUTE
        end = pd.Timestamp(int(ts_ns[j + 1]), unit="ns", tz="UTC")
        s_wall, e_wall = server_wall(start, server_tz)[0], server_wall(end, server_tz)[0]
        tod = s_wall - s_wall.normalize()
        if tod >= BREAK_FROM:
            opened = s_wall.normalize() + BREAK_FROM
        elif tod <= BREAK_TO:
            opened = s_wall.normalize() - pd.Timedelta(days=1) + BREAK_FROM
        else:
            out[j + 1] = not _on_holiday(s_wall, e_wall, holidays)
            continue
        days = 3 if opened.dayofweek == 4 else 1                  # Friday's close reopens on Monday
        beyond = e_wall > opened.normalize() + pd.Timedelta(days=days) + BREAK_TO
        out[j + 1] = beyond and not _on_holiday(s_wall, e_wall, holidays)
    return out


def _on_holiday(s_wall: pd.Timestamp, e_wall: pd.Timestamp, holidays: HolidayList | None) -> bool:
    """The missing minutes [s_wall, e_wall) (wall clock), less the daily break at either end, lie on one date the
    holiday list covers and names."""
    if holidays is None:
        return False
    first = s_wall.normalize() + pd.Timedelta(days=1) if s_wall - s_wall.normalize() >= BREAK_FROM \
        else s_wall.normalize()
    last_min = e_wall - MINUTE
    last = last_min.normalize() - pd.Timedelta(days=1) if last_min - last_min.normalize() < BREAK_TO \
        else last_min.normalize()
    d = first.date()
    return first == last and holidays.covers(d) and holidays.thin(d) is not None


def add_trading_days(t: pd.Timestamp, n: int) -> pd.Timestamp:
    """`t` plus `n` weekdays (UTC calendar): Friday 15:00 + 1 = Monday 15:00."""
    out, k = pd.Timestamp(t), 0
    while k < n:
        out += pd.Timedelta(days=1)
        if out.dayofweek < 5:
            k += 1
    return out


def _pnl(side: int, fill: float, px: float, t_in: pd.Timestamp, t_out: pd.Timestamp, half_in: float, half_out: float,
         costs: ScoreCosts) -> tuple[float, float, int]:
    """(net $/oz, net $/oz under the 1.5x stress, swap nights) of one position: the spread is in the bid/ask fills."""
    nights = int(rollover_nights(pd.DatetimeIndex([t_in]), pd.DatetimeIndex([t_out]), costs.swap.server_tz,
                                 costs.swap.triple_weekday)[0])
    swap = nights * float(costs.swap.usd_per_oz_at(np.array([side]), fill)[0])     # broker sign: negative = paid
    net = side * (px - fill) - costs.extra_usd + swap
    return net, net - (STRESS - 1.0) * (half_in + half_out + costs.extra_usd), nights


# ------------------------------------------------------------------------------------------------- direction read
class SideOutcome(FrozenRecord):
    r: float
    r_stress: float
    exit: Literal["stop", "time"]
    exit_utc: UtcTimestamp
    nights: int
    resolved: bool


def direction_read(m: Minutes, t0: pd.Timestamp, *, side: int, atr: float, bucket: str,
                   costs: ScoreCosts) -> SideOutcome | None:
    """Prereg 1.3(b): enter at market on the first minute at or after `t0` (ask for a long, bid for a short), hold to
    the horizon or the catastrophic stop at 2R (touched on the bid for a long, the ask for a short; a gap through it
    exits at the open), mark at the close. Unresolved until a minute at or past the horizon exists. None: no minute
    at or after `t0` yet."""
    i0 = m.at_or_after(t0)
    if i0 >= m.n:
        return None
    t_in = m.ts(i0)
    fill = float(m.ao[i0] if side > 0 else m.bo[i0])
    half_in = float(m.ao[i0] - m.bo[i0]) / 2
    stop = fill - side * CAT_STOP_R * atr
    iend = m.at_or_after(add_trading_days(t_in, HORIZON_DAYS[bucket]))
    hits = np.flatnonzero(m.bl[i0:iend] <= stop) if side > 0 else np.flatnonzero(m.ah[i0:iend] >= stop)
    if hits.size:
        k = i0 + int(hits[0])
        opn = float(m.bo[k] if side > 0 else m.ao[k])
        px = stop if k == i0 else (min(stop, opn) if side > 0 else max(stop, opn))
        t_out, how, resolved = m.ts(k), "stop", True
        half_out = float(m.ao[k] - m.bo[k]) / 2
    else:
        j = max(iend - 1, i0)
        px = float(m.bc[j] if side > 0 else m.ac[j])
        t_out, how, resolved = m.ts(j) + MINUTE, "time", iend < m.n
        half_out = float(m.ac[j] - m.bc[j]) / 2
    net, stressed, nights = _pnl(side, fill, px, t_in, t_out, half_in, half_out, costs)
    return SideOutcome(r=net / atr, r_stress=stressed / atr, exit=how, exit_utc=t_out, nights=nights,  # type: ignore[arg-type]
                       resolved=resolved)


# ------------------------------------------------------------------------------------------------- levels read
class LevelsOutcome(FrozenRecord):
    status: LevelsStatus
    r: float | None = None
    r_stress: float | None = None
    fill_utc: UtcTimestamp | None = None
    exit_utc: UtcTimestamp | None = None


def levels_read(m: Minutes, t0: pd.Timestamp, call: Call, *, atr: float, bucket: str,
                costs: ScoreCosts) -> LevelsOutcome:
    """Prereg 1.2-1.3(a) on the author's entry, stop and first target, from the first minute at or after `t0`.

    * Sanity (1.1): a stop or target on the wrong side of the entry, |entry - price| > 3 x ATR, or a stop closer than
      2 round trips demotes the idea to direction only (`demoted`).
    * Market when the price lies between the stop and the entry or within 0.25 x ATR of the entry; otherwise a limit
      at the entry for the fill window. A target touched before the fill (or in the fill minute, whose order is
      unknown), or a gap through the stop, is `invalidated`; no touch in the window is `no_fill`; both score 0 R.
    * After the fill, the first barrier on the bid (long) / ask (short) decides, the stop first when both print in one
      minute (also in the fill minute); at the maximum hold the position is marked at the close. R = |fill - stop|."""
    i0 = m.at_or_after(t0)
    if i0 >= m.n or call.label not in ("long", "short") or not call.has_levels:
        return LevelsOutcome(status="pending" if i0 >= m.n else "demoted")
    side = 1 if call.label == "long" else -1
    entry, stop, target = float(call.entry), float(call.stop), float(call.target)  # type: ignore[arg-type]
    p0 = float(m.ao[i0] + m.bo[i0]) / 2
    round_trip = float(m.ao[i0] - m.bo[i0]) + costs.extra_usd
    if (side * (entry - stop) <= 0 or side * (target - entry) <= 0 or abs(entry - p0) > SANITY_ENTRY_ATR * atr
            or abs(entry - stop) < SANITY_MIN_STOP_RT * round_trip):
        return LevelsOutcome(status="demoted")
    market = min(stop, entry) <= p0 <= max(stop, entry) or abs(p0 - entry) <= MARKET_BAND_ATR * atr
    if market:
        f, fill = i0, float(m.ao[i0] if side > 0 else m.bo[i0])
    else:
        iw = m.at_or_after(add_trading_days(m.ts(i0), FILL_WINDOW_DAYS[bucket]))
        f, fill = -1, float("nan")
        for j in range(i0, iw):
            if (m.bh[j] >= target) if side > 0 else (m.al[j] <= target):
                return LevelsOutcome(status="invalidated", r=0.0, r_stress=0.0)
            if (m.al[j] <= entry) if side > 0 else (m.bh[j] >= entry):
                opn = float(m.ao[j] if side > 0 else m.bo[j])
                fill = min(entry, opn) if side > 0 else max(entry, opn)
                if side * (fill - stop) <= 0:                  # gapped through the stop: it traded before the fill
                    return LevelsOutcome(status="invalidated", r=0.0, r_stress=0.0)
                f = j
                break
        if f < 0:
            return LevelsOutcome(status="no_fill" if iw < m.n else "pending",
                                 r=0.0 if iw < m.n else None, r_stress=0.0 if iw < m.n else None)
    t_in = m.ts(f)
    half_in = float(m.ao[f] - m.bo[f]) / 2
    iend = m.at_or_after(add_trading_days(t_in, MAX_HOLD_DAYS[bucket]))
    if side > 0:
        stop_hit, tgt_hit = m.bl[f:iend] <= stop, m.bh[f:iend] >= target
    else:
        stop_hit, tgt_hit = m.ah[f:iend] >= stop, m.al[f:iend] <= target
    any_hit = np.flatnonzero(stop_hit | tgt_hit)
    if any_hit.size:
        k = f + int(any_hit[0])
        is_stop = bool(stop_hit[k - f])                        # stop first when both print in one minute
        level = stop if is_stop else target
        opn = float(m.bo[k] if side > 0 else m.ao[k])
        worse = (min if side > 0 else max) if is_stop else (max if side > 0 else min)
        px = level if k == f else worse(level, opn)
        t_out, status = m.ts(k), ("stop" if is_stop else "target")
        half_out = float(m.ao[k] - m.bo[k]) / 2
    else:
        if iend >= m.n:
            return LevelsOutcome(status="pending", fill_utc=t_in)
        j = max(iend - 1, f)
        px = float(m.bc[j] if side > 0 else m.ac[j])
        t_out, status = m.ts(j) + MINUTE, "time"
        half_out = float(m.ac[j] - m.bc[j]) / 2
    risk = abs(fill - stop)
    net, stressed, _ = _pnl(side, fill, px, t_in, t_out, half_in, half_out, costs)
    return LevelsOutcome(status=status, r=net / risk, r_stress=stressed / risk, fill_utc=t_in,  # type: ignore[arg-type]
                         exit_utc=t_out)


# ------------------------------------------------------------------------------------------------- point in time
def daily_atr(bars: pd.DataFrame, server_tz: str) -> pd.DataFrame:
    """T-2 (4): R = the mean true range of mid daily bars cut at the broker's server midnight, over the 14 most recent
    WHOLE server weekdays among the last 20 (whole: at least 80% of the median day's minutes; a weekday with no minute
    at all counts as not whole). A true range uses the previous whole day's close. Each day's ATR is available at its
    last minute's close: `available_utc`, `atr_usd`; NaN (the idea is then `no_bars`) with fewer than 14."""
    if bars.empty:
        return pd.DataFrame({"available_utc": pd.DatetimeIndex([], tz="UTC"), "atr_usd": []})
    b = bars.sort_values("ts_utc", kind="stable")
    ts = pd.DatetimeIndex(pd.to_datetime(b["ts_utc"], utc=True))
    f = pd.DataFrame({"day": server_wall(ts, server_tz).normalize(), "ts": ts,
                      "h": (b["bid_high"].to_numpy() + b["ask_high"].to_numpy()) / 2,
                      "l": (b["bid_low"].to_numpy() + b["ask_low"].to_numpy()) / 2,
                      "c": (b["bid_close"].to_numpy() + b["ask_close"].to_numpy()) / 2})
    f = f[f["day"].dt.dayofweek < 5]
    d = f.groupby("day").agg(h=("h", "max"), l=("l", "min"), c=("c", "last"), last=("ts", "max"), n=("ts", "size"))
    d = d.reindex(pd.bdate_range(d.index.min(), d.index.max()))          # a weekday with no minute is not whole
    d["n"] = d["n"].fillna(0)
    whole = (d["n"] >= FULL_DAY_SHARE * d.loc[d["n"] > 0, "n"].median()).to_numpy()
    w = d[whole]
    prev = w["c"].shift()
    tr_whole = pd.concat([w["h"] - w["l"], (w["h"] - prev).abs(), (w["l"] - prev).abs()], axis=1).max(axis=1)
    tr_whole[prev.isna()] = np.nan                             # a true range needs the previous whole close
    tr = tr_whole.reindex(d.index).to_numpy(dtype=float)
    atr = np.full(len(d), np.nan)
    for i in range(len(d)):
        window = tr[max(0, i - ATR_WINDOW_DAYS + 1): i + 1]
        valid = window[np.isfinite(window)]
        if len(valid) >= ATR_DAYS:
            atr[i] = valid[-ATR_DAYS:].mean()
    out = pd.DataFrame({"available_utc": d["last"] + MINUTE, "atr_usd": atr})
    return out.dropna(subset=["available_utc"]).reset_index(drop=True)


def atr_at(points: pd.DataFrame, atr: pd.DataFrame) -> np.ndarray:
    """The ATR available at each `points.ts_utc` (asof_join on the daily bar's close), in `points`' order; NaN
    before the first full ATR."""
    left = pd.DataFrame({"ts_utc": points["ts_utc"].to_numpy(), "_pos": np.arange(len(points))})
    j = asof_join(left, atr, on="ts_utc", available_col="available_utc")
    return j.sort_values("_pos")["atr_usd"].to_numpy(dtype=float)


def entry_minutes(m: Minutes, times: pd.Series) -> np.ndarray:
    """Index of the first minute at or after each time (`m.n`: none yet), through `asof_join` on `available_utc`: each
    minute takes the latest time available by its open, so an idea's entry is the first minute that can see it."""
    t = pd.DatetimeIndex(pd.to_datetime(times, utc=True))
    order = np.argsort(epoch_ns(t), kind="stable")
    other = pd.DataFrame({"available_utc": t[order], "_rank": np.arange(len(t), dtype=float)})
    left = pd.DataFrame({"ts_utc": pd.DatetimeIndex(m.ts_ns.astype("datetime64[ns]"), tz="UTC")})
    if left.empty or other.empty:
        return np.full(len(t), m.n, dtype=int)
    seen = asof_join(left, other, on="ts_utc", available_col="available_utc")["_rank"].fillna(-1).to_numpy()
    out = np.empty(len(t), dtype=int)
    out[order] = np.searchsorted(seen, np.arange(len(t)), side="left")
    return out


# ------------------------------------------------------------------------------------------------- ideas
def bucket_of(tf: str | None) -> Bucket:
    """T-2 (5): I = up to 240 minutes or a seconds chart; D = over 240 minutes and 1D..6D; W = weeks and months; a
    missing timeframe is D."""
    s = (tf or "").strip().upper()
    if s.endswith("S"):                                        # seconds charts
        return "I"
    if s.isdigit():
        return "I" if int(s) <= 240 else "D"
    if s.endswith("W") or s.endswith("M"):
        return "W"
    return "D"


def symbol_class(symbol: str) -> SymbolClass:
    """Spot gold (XAUUSD from any broker, GOLD) is scored with its levels; COMEX futures (GC1!, GC, MGC and their
    contract months) direction-only; anything else is not gold."""
    tick = symbol.split(":")[-1].strip().upper()
    if tick.startswith("XAUUSD") or tick == "GOLD":
        return "spot"
    if re.fullmatch(r"M?GC(\d!|[FGHJKMNQUVXZ]\d{2,4})?", tick):
        return "futures"
    return "other"


def _read_only(path: str | Path) -> sqlite3.Connection:
    """tv_ideas.db for reading only: the scorer can never write the collector's file."""
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, timeout=5)


def read_ideas(path: str | Path, authors: Iterable[str]) -> pd.DataFrame:
    """The followed authors' ideas as first released, with the count of distinct text versions (an edit is > 1)."""
    names = {a.lower() for a in authors}
    conn = _read_only(path)
    try:
        df = pd.read_sql_query(
            "SELECT i.idea_id, i.author, i.symbol, i.tf, i.title, i.description, i.published_utc, i.first_seen_utc, "
            "i.available_utc, i.backfilled, (SELECT COUNT(DISTINCT v.content_hash) FROM tv_idea_versions v "
            "WHERE v.idea_id = i.idea_id) AS n_versions FROM tv_ideas i ORDER BY i.available_utc, i.idea_id", conn)
    finally:
        conn.close()
    df = df[df["author"].str.lower().isin(names)].reset_index(drop=True)
    for c in ("published_utc", "first_seen_utc", "available_utc"):
        df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601").dt.as_unit("ns")
    df["backfilled"] = df["backfilled"].astype(bool)
    return df


class ScoreRow(FrozenRecord):
    """One idea's shadow score (aux.db `tva_scores`). Holds no idea text and no author level."""
    idea_id: int
    author: str
    bucket: Bucket
    symbol_class: SymbolClass
    published_utc: UtcTimestamp
    available_utc: UtcTimestamp
    status: Status
    edited: bool
    label: Label = "unlabelled"
    extractor: str | None = None
    extractor_sha: str | None = None
    decision_eligible: bool = False
    entry_utc: UtcTimestamp | None = None
    atr_usd: float | None = None
    r_long: float | None = None
    r_short: float | None = None
    r_long_stress: float | None = None
    r_short_stress: float | None = None
    exit_long: str | None = None
    exit_short: str | None = None
    r_long_pub: float | None = None
    r_short_pub: float | None = None
    r_side: float | None = None
    r_side_stress: float | None = None
    levels_status: LevelsStatus | None = None
    levels_r: float | None = None
    levels_r_stress: float | None = None
    cost_source: str
    scorer_sha: str = SCORER_SHA

    @property
    def final(self) -> bool:
        return self.status in FINAL and self.levels_status != "pending"


def _holdout(t: pd.Timestamp, window: tuple[pd.Timestamp, pd.Timestamp] | None) -> bool:
    return window is not None and window[0] <= t < window[1]


def score_ideas(ideas: pd.DataFrame, bars: pd.DataFrame, *, costs: ScoreCosts,
                holdout: tuple[pd.Timestamp, pd.Timestamp] | None, as_of: pd.Timestamp,
                extractors: Mapping[str, Extractor] = ADMITTED_EXTRACTORS,
                holidays: HolidayList | None = None) -> list[ScoreRow]:
    """Score `ideas` (read_ideas) on `bars` (1m bid/ask) visible by `as_of`; `holidays` exempts holes on its listed
    dates (None: no exemption). Module docstring for the rules."""
    if not bars.empty:
        t = pd.to_datetime(bars["ts_utc"], utc=True)
        keep = t < as_of
        if holdout is not None:                                # no holdout minute is ever read
            keep &= ~((t >= holdout[0]) & (t < holdout[1]))
        vis = bars[keep]
    else:
        vis = bars
    m = Minutes.from_frame(vis) if not vis.empty else Minutes(np.zeros(0, dtype=np.int64),
                                                             {f"{s}_{c}": np.zeros(0) for s in ("bid", "ask")
                                                              for c in ("open", "high", "low", "close")})
    atr_table = daily_atr(vis, costs.swap.server_tz)
    holes = m.holes(BREAK_CLOCK, holidays)
    t_live = ideas["available_utc"]
    t_pub = ideas["published_utc"] + PUBLISH_LAG
    i_live, i_pub = entry_minutes(m, t_live), entry_minutes(m, t_pub)
    entry_ts = [m.ts(i) if i < m.n else t for i, t in zip(i_live, t_live, strict=True)]
    pub_ts = [m.ts(i) if i < m.n else t for i, t in zip(i_pub, t_pub, strict=True)]
    atr_live = atr_at(pd.DataFrame({"ts_utc": entry_ts}), atr_table) if len(ideas) else np.zeros(0)
    atr_pub = atr_at(pd.DataFrame({"ts_utc": pub_ts}), atr_table) if len(ideas) else np.zeros(0)
    rows: list[ScoreRow] = []
    records = cast(list[dict[str, Any]], ideas.to_dict("records"))
    for k, idea in enumerate(records):
        base: dict[str, Any] = dict(idea_id=int(idea["idea_id"]), author=str(idea["author"]),
                                    bucket=bucket_of(idea["tf"]), symbol_class=symbol_class(str(idea["symbol"])),
                                    published_utc=idea["published_utc"], available_utc=idea["available_utc"],
                                    edited=int(idea["n_versions"]) > 1, cost_source=costs.source)
        if base["symbol_class"] == "other":
            rows.append(ScoreRow(status="not_gold", **base))
            continue
        if _holdout(idea["published_utc"], holdout) or _holdout(idea["available_utc"], holdout):
            rows.append(ScoreRow(status="holdout", **base))
            continue
        if idea["backfilled"]:
            rows.append(ScoreRow(status="backfilled", **base))
            continue
        ex = extractors.get(str(idea["author"]).lower())
        call = ex.fn(str(idea["title"]), str(idea["description"])) if ex is not None else None
        if ex is not None:
            base.update(extractor=ex.name, extractor_sha=ex.sha)
        label: Label = call.label if call is not None else "unlabelled"
        base["label"] = label
        if i_live[k] >= m.n:
            rows.append(ScoreRow(status="pending", **base))
            continue
        atr = float(atr_live[k])
        if not np.isfinite(atr) or atr <= 0:
            rows.append(ScoreRow(status="no_bars", entry_utc=entry_ts[k], **base))
            continue
        bucket = base["bucket"]
        lo = direction_read(m, entry_ts[k], side=1, atr=atr, bucket=bucket, costs=costs)
        sh = direction_read(m, entry_ts[k], side=-1, atr=atr, bucket=bucket, costs=costs)
        assert lo is not None and sh is not None                # an entry minute exists
        out: dict[str, Any] = dict(entry_utc=entry_ts[k], atr_usd=atr, r_long=lo.r, r_short=sh.r,
                                   r_long_stress=lo.r_stress, r_short_stress=sh.r_stress, exit_long=lo.exit,
                                   exit_short=sh.exit)
        last = max(lo.exit_utc, sh.exit_utc)
        if holes[i_live[k] + 1: m.at_or_after(last) + 1].any():   # T-2 (2): a hole in the hold, rescored nightly ...
            horizon = add_trading_days(entry_ts[k], HORIZON_DAYS[bucket])
            final = as_of >= add_trading_days(horizon, HOLE_FINAL_DAYS)   # ... until 5 weekdays after the horizon
            rows.append(ScoreRow(status="no_bars_final" if final else "no_bars", entry_utc=entry_ts[k], atr_usd=atr,
                                 **base))
            continue
        pub_pending = False
        if i_pub[k] < m.n and np.isfinite(atr_pub[k]) and atr_pub[k] > 0:   # descriptive capture read
            for side, key in ((1, "r_long_pub"), (-1, "r_short_pub")):
                p = direction_read(m, pub_ts[k], side=side, atr=float(atr_pub[k]), bucket=bucket, costs=costs)
                out[key] = p.r if p is not None and p.resolved else None
                pub_pending |= p is None or not p.resolved
        if label in ("long", "short"):
            chosen = lo if label == "long" else sh
            out.update(r_side=chosen.r, r_side_stress=chosen.r_stress)
            if call is not None and call.has_levels and base["symbol_class"] == "spot":
                lv = levels_read(m, entry_ts[k], call, atr=atr, bucket=bucket, costs=costs)
                out.update(levels_status=lv.status, levels_r=lv.r, levels_r_stress=lv.r_stress)
        status: Status = "scored" if lo.resolved and sh.resolved and not pub_pending else "pending"
        # T-2 (6)/(7): only ideas available after the author's extractor was admitted; an edited live idea stays in
        # (scored on its first snapshot; `edited` is kept for the edited-excluded sensitivity read)
        eligible = (status == "scored" and label in ("long", "short") and ex is not None
                    and idea["available_utc"] >= ex.admitted_utc)
        rows.append(ScoreRow(status=status, decision_eligible=eligible, **base, **out))
    return rows


# ------------------------------------------------------------------------------------------------- storage
_COLUMNS = tuple(f for f in ScoreRow.model_fields)


def _db_value(v: Any) -> Any:
    if isinstance(v, pd.Timestamp):
        return v.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(v, bool):
        return int(v)
    return v


def save_scores(aux: Database, rows: Iterable[ScoreRow], *, scored_utc: pd.Timestamp) -> int:
    """Upsert `rows` into aux.db `tva_scores`; returns the rows written. A final stored row is never rescored: only its
    `edited` can go 0 -> 1 (it stays decision-eligible, T-2 (7)), and an unlabelled one takes a later-admitted extractor's
    label (the first snapshot and the frozen extractor are both fixed, so the label is point in time)."""
    rows = list(rows)
    cols = [*_COLUMNS, "scored_utc"]
    sql = f"INSERT OR REPLACE INTO tva_scores ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    n = 0
    with aux.write() as conn:
        held = {int(r[0]): r for r in conn.execute(
            "SELECT idea_id, status, levels_status, edited, label FROM tva_scores")}
        for row in rows:
            old = held.get(row.idea_id)
            if old is not None and old[1] in FINAL and old[2] != "pending":
                relabel = old[4] == "unlabelled" and row.label != "unlabelled" and row.final
                if not relabel:
                    if row.edited and not old[3]:
                        conn.execute("UPDATE tva_scores SET edited = 1 WHERE idea_id = ?",
                                     (row.idea_id,))
                        n += 1
                    continue
            conn.execute(sql, [_db_value(getattr(row, c)) for c in _COLUMNS] + [_db_value(scored_utc)])
            n += 1
    return n


def stored_rows(aux: Database) -> list[ScoreRow]:
    with aux.connection() as conn:
        conn.row_factory = sqlite3.Row
        got = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM tva_scores ORDER BY idea_id").fetchall()
    out = []
    for r in got:
        d = {c: r[c] for c in _COLUMNS}
        d["edited"], d["decision_eligible"] = bool(d["edited"]), bool(d["decision_eligible"])
        out.append(ScoreRow(**d))
    return out


def summarise(rows: Iterable[ScoreRow]) -> dict[str, dict[str, Any]]:
    """Per author: counts by status and label, edits, and progress toward the forward read (decision-eligible resolved
    ideas and their distinct UTC publish days, against >= 100 and >= 60). Deliberately no running skill statistic."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        a = out.setdefault(r.author, {"ideas": 0, "scored": 0, "pending": 0, "no_bars": 0, "no_bars_final": 0,
                                      "backfilled": 0,
                                      "not_gold": 0, "holdout": 0, "labelled": 0, "unlabelled": 0, "abstain": 0,
                                      "edited": 0, "eligible_resolved": 0, "_days": set()})
        a["ideas"] += 1
        a[r.status] += 1
        if r.status in ("backfilled", "not_gold", "holdout"):
            continue
        a["labelled" if r.label in ("long", "short") else r.label] += 1
        a["edited"] += int(r.edited)
        if r.decision_eligible:
            a["eligible_resolved"] += 1
            a["_days"].add(r.published_utc.date())
    for a in out.values():
        a["publish_days"] = len(a.pop("_days"))
        a["read_ready"] = a["eligible_resolved"] >= READ_MIN_IDEAS and a["publish_days"] >= READ_MIN_DAYS
    return out


# ------------------------------------------------------------------------------------------------- the job
def run_job(settings: Settings, state_dir: str | Path, *, store: Any, accounts: Iterable[Any], slot: pd.Timestamp,
            costs: ScoreCosts | None = None) -> dict[str, Any]:
    """Nightly `tva_scores`: score every idea whose stored row is not final on the canonical-cost broker's own 1m
    minutes (data/account_bars.py) visible by `slot`, store the rows, report counts. No idea text leaves the file."""
    from goldbot.data.account_bars import account_bars_1m
    from goldbot.db.connection import Database
    path = Path(state_dir) / DB_FILE
    if not path.exists():
        return {"skipped": f"no {DB_FILE} (the collector runs on the brain)"}
    costs = costs or ScoreCosts.from_research(settings, None)
    ideas = read_ideas(path, settings.tv_ideas.authors)
    aux = Database.open(state_dir, "aux")
    # a final row is done, unless it is an unlabelled score whose author has an admitted extractor now
    done = {r.idea_id for r in stored_rows(aux) if r.final and not (
        r.status == "scored" and r.label == "unlabelled" and r.author.lower() in ADMITTED_EXTRACTORS)}
    work = ideas[~ideas["idea_id"].isin(done)].reset_index(drop=True)
    edited = ideas[ideas["n_versions"] > 1]["idea_id"].tolist()
    canonical = {b for b, cfg in settings.brokers.items() if cfg.canonical_costs}
    holdout = settings.research.holdout_window()
    # the minutes are read only from the earliest idea that can still be scored (never the back catalogue, another
    # symbol or a holdout idea: the first night must not load years of 1m bars), and never from inside the holdout
    live = work[(work["symbol"].map(symbol_class) != "other") & ~work["backfilled"]
                & ~work["published_utc"].map(lambda t: _holdout(t, holdout))
                & ~work["available_utc"].map(lambda t: _holdout(t, holdout))]
    bars, bars_from = pd.DataFrame(), None
    if not live.empty:
        start = live["available_utc"].min() - pd.Timedelta(days=3 * ATR_DAYS)
        if holdout is not None and start < holdout[1]:
            start = holdout[1]
        for acc in accounts:
            if acc.broker not in canonical:
                continue
            got = account_bars_1m(store, account_id=acc.account_id, broker=acc.broker, symbol=acc.symbol,
                                  start=start, end=slot, as_of=slot)
            if not got.empty:
                bars, bars_from = got, acc.account_id
                break
    holidays, calendar = _holidays(settings)
    rows = score_ideas(work, bars, costs=costs, holdout=holdout, as_of=slot, holidays=holidays)
    written = save_scores(aux, rows, scored_utc=slot)
    if edited:
        with aux.write() as conn:
            conn.executemany("UPDATE tva_scores SET edited = 1 WHERE idea_id = ? AND edited = 0",
                             [(int(i),) for i in edited])
    return {"ideas": len(ideas), "rescored": len(work), "written": written, "bars_account": bars_from,
            "cost_source": costs.source, "extractors": sorted(ADMITTED_EXTRACTORS), "holidays": calendar,
            "authors": summarise(stored_rows(aux))}


def _holidays(settings: Settings) -> tuple[HolidayList | None, str]:
    """The CME metals holiday list (`risk.holidays.file`) for the hole exemption, and a line naming it. Unreadable:
    no exemption (a holiday hole then stays `no_bars`, conservative), said in the job's output."""
    from goldbot.risk.windows import load_holidays
    try:
        cal = load_holidays(settings.risk.holidays.file)
    except (OSError, ValueError) as exc:
        return None, f"unavailable ({type(exc).__name__}): no holiday exemption"
    return cal, f"{cal.version} {cal.valid_from}..{cal.valid_through}"
