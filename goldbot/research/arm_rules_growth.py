"""Growth sprint 2026-11-01 (docs/research/growth-sprint-2026-11-01.md section 2 C2, section 3): the demo growth test's
rules on 15m bars.

* `session_range_break` (GS1): "pyramided session-range breakout (2 variants: Asia range broken in London; London range
  broken in NY). Entry on a 15m close beyond the range. Initial stop 1.5 x ATR15." Section 3's fallback is what runs
  first: "GS1 launches with a single unit and a 3 R target"; the pyramid adds followed on 11-02 as an execution
  mechanic of the account (`engine.exploration_pyramid`, accounts.yaml `adds`, ADR 0065), so this rule decides the first
  unit only. One entry per session per day: the first 15m close beyond the range inside the break window.
* `session_range_control` (GS1C): "Entry time is uniform within the same session window on the same days, and
  direction is a fair coin. It is drawn from a seeded RNG that is logged." One entry per session whose range formed
  (the sessions GS1 was eligible in), at a close drawn from the sha256 seed of (arm, day, session), side from the seed
  of that close (`arm_rules.hash_side`): restart-safe and identical in history and live. GS1's stop, target and hold.
  Quant requirement (5), "pair GS1C 1:1 with GS1": a live control cannot know in advance whether GS1 will break, so it
  enters every eligible session and the read pairs the two by (day, session) (`research.growth_ledger.growth_read`).

Sessions are on city clocks, converted per date (each city's own DST): the Asia range runs 00:00 UTC to the London open
(08:00 London) and is broken in London 08:00-12:00 London; the London range runs 08:00 London to the New York open
(08:30 New York) and is broken in New York 08:30-11:00 New York. A range needs MIN_RANGE_BARS 15m bars; the common
filters (tier-1 blackout, rollover, Friday evening) apply to both arms."""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from goldbot.data.timeutil import utc_index
from goldbot.features.technical import atr
from goldbot.research.arm_rules import (
    LONDON,
    NEW_YORK,
    SEED_PREFIX,
    RuleContext,
    _frame,
    common_filter_ok,
    empty,
    hash_side,
)
from goldbot.research.arms import Arm

STEP = pd.Timedelta(minutes=15)
MIN_RANGE_BARS = 4
GROWTH_RULES = ("session_range_break", "session_range_control")
Clock = tuple[str, int, int]
# session -> (range start, range end = break window start, break window end), each (zone, hour, minute)
SESSIONS: dict[str, tuple[Clock, Clock, Clock]] = {
    "asia_london": (("UTC", 0, 0), (LONDON, 8, 0), (LONDON, 12, 0)),
    "london_ny": ((LONDON, 8, 0), (NEW_YORK, 8, 30), (NEW_YORK, 11, 0)),
}


def _at(day: pd.Timestamp, spec: Clock) -> pd.Timestamp:
    zone, h, m = spec
    return pd.Timestamp(year=day.year, month=day.month, day=day.day, hour=h, minute=m, tz=zone).tz_convert("UTC")


def session_bounds(day: pd.Timestamp, session: str) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """(range start, range end, break window end) of `session` on `day`, UTC."""
    a, b, c = SESSIONS[session]
    return _at(day, a), _at(day, b), _at(day, c)


def session_of(close: pd.Timestamp) -> str | None:
    """The session whose break window holds the decision close `close`, None outside every window."""
    day = pd.Timestamp(close).tz_convert("UTC").normalize()
    for s in SESSIONS:
        _, start, end = session_bounds(day, s)
        if start < close <= end:
            return s
    return None


def control_close(arm_id: str, day: pd.Timestamp, session: str) -> pd.Timestamp:
    """The control's decision close in `session` on `day`: uniform over the break window's 15m closes, from the seed
    `SEED_PREFIX|arm|day|session`; the schedule fixes it, not the bars present (live == history)."""
    _, start, end = session_bounds(day, session)
    n = max(1, int((end - start) / STEP))
    key = f"{SEED_PREFIX}|{arm_id}|{day:%Y-%m-%d}|{session}"
    u = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2 ** 64
    return start + STEP * (1 + int(u * n))


def _rows(arm: Arm, ctx: RuleContext, control: bool) -> pd.DataFrame:
    p = arm.params
    m = ctx.mid
    if len(m) == 0:
        return empty()
    ts = utc_index(m["ts_utc"])
    close = ctx.close_ts
    h, lo, c = (m[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    a = atr(m, 14).to_numpy()
    ok = common_filter_ok(close, ctx.blackout_lo, ctx.blackout_hi)
    rows: list[tuple[int, int, float]] = []
    for day in pd.DatetimeIndex(ts.normalize().unique()):
        if day.dayofweek >= 5:
            continue
        for session in p["sessions"]:
            r0, r1, w1 = session_bounds(day, session)
            in_range = np.flatnonzero((ts >= r0) & (ts < r1))
            if len(in_range) < MIN_RANGE_BARS:
                continue                                      # no range formed: GS1 not eligible, no control either
            window = np.flatnonzero((close > r1) & (close <= w1))
            if control:
                at = control_close(arm.id, day, session)
                hit = window[close[window] == at]
                if len(hit) and ok[hit[0]]:
                    rows.append((int(hit[0]), hash_side(arm.id, at), float(p["stop_atr"]) * a[hit[0]]))
                continue
            rh, rl = h[in_range].max(), lo[in_range].min()
            for t in window:
                side = 1 if c[t] > rh else -1 if c[t] < rl else 0
                if side:
                    if ok[t]:
                        rows.append((int(t), side, float(p["stop_atr"]) * a[t]))
                    break                                     # the first close beyond the range: one per session
    if not rows:
        return empty()
    rows.sort()
    return _frame(np.array([r[0] for r in rows]), np.array([r[1] for r in rows]), np.array([r[2] for r in rows]),
                  p["target_r"], p["hold_bars"])


def session_range_break(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """GS1: the first 15m close beyond the session's range inside its break window; stop `stop_atr` x ATR(14) of the
    15m bars, target `target_r` R, held `hold_bars` bars at most."""
    return _rows(arm, ctx, control=False)


def session_range_control(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """GS1C: one entry per eligible session at the seeded close (`control_close`), seeded side, GS1's barriers."""
    return _rows(arm, ctx, control=True)


def check(arm: Arm) -> None:
    """Load-time check: 15m, known sessions, the barriers present, a control naming the arm it controls."""
    p = arm.params
    if arm.timeframe != "15m":
        raise ValueError("a growth session-range arm runs on 15m bars")
    sessions = p.get("sessions")
    if not isinstance(sessions, list) or not sessions or set(sessions) - set(SESSIONS):
        raise ValueError(f"sessions must be a non-empty list of {sorted(SESSIONS)}")
    for k in ("stop_atr", "target_r", "hold_bars"):
        if k not in p:
            raise ValueError(f"missing params.{k}")
    if arm.rule == "session_range_control" and not p.get("control"):
        raise ValueError("a growth control names the arm it controls (params.control)")
