"""GD-1 A1 on demo (docs/research/prereg-amendment-2026-11-20-gd1-asia-learning.md; the card is
prereg-2026-11-20-gold-daily.md, H-98, trial #3440): "A1 Asia long: each weekday, long 00:00 -> 07:00 UTC ..., flat
otherwise", run forward as a learning-class arm (ADR 0061 amendment 2026-11-02) with a replay-only control. One function
per rule, used by history and live (`arm_rules.RULE_FUNCS`); a decision on the close of bar t reads bars <= t only (here
the close time alone: no price enters either rule).

* `clock_hold` (GD1D on icm-demo, GD1P on icm-probe): long on the close of the 1h bar ending at `entry_utc` (00:00 UTC)
  on each of `weekdays` (UTC, Monday 0). A 15m+ arm sends at its close with no delay (`arms.send_delay_s`), so the
  order goes on the first tick the engine handles at or after 00:00. No signal and no target ("No signal and no target",
  the deploy plan): the target is NO_TARGET_R (50 R, out of reach). The stop is the row's fixed `stop_usd`, a
  catastrophic stop at the broker (amendment section 3: $48, about 2.5 sd of the 7-hour window, the most that fits the
  ADR 0043 swing per-trade cap of GBP 40 at 0.01 lot). Hold `hold_bars` 1h bars: the exit is the close of the 07:00
  UTC bar, and the engine also closes it on the wall clock at its signal close plus its hold (`WALL_CLOCK_EXIT`,
  `engine.exploration.exits`), so a missing decision bar never holds it past 07:00. Never through the rollover: an
  entry hour whose window ends after ROLLOVER_H is refused at load.
* `clock_hold_control` (GD1CD, GD1CP), the matched control (amendment section 4): the same side, stop, target and hold
  on the close of the 1h bar at ONE seeded hour a weekday drawn from `entry_hours_utc` (07:00..13:00 UTC: the
  complement of the arm's window before the rollover, the deploy plan's "the same lot held 07:00 -> 00:00 (the
  complement)" in 7-hour pieces). The draw is `drawn_hour`: `arm_rules.hash_u` of the control's id and the UTC day,
  restart-safe and identical in history and live. Its row is `control_mode: replay_only` (amendment 2026-11-15): every
  candidate is recorded and labelled by the r2 replay, and no order is ever sent (`engine.exploration._send`).

The common filters (tier-1 blackout, 20:45-22:15 UTC, Friday from 19:00 UTC) apply to both, as to every arm
(`arm_rules.screened_frame`)."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from goldbot.research.arm_rules import RuleContext, _frame, empty, hash_u
from goldbot.research.arms import Arm

CLOCK_HOLD_RULES = ("clock_hold", "clock_hold_control")
# the rules whose time exit the engine also takes on the wall clock (`engine.exploration.exits`, `_clock_due`)
WALL_CLOCK_EXIT = frozenset({"clock_hold"})
TIMEFRAME = "1h"
HOLD_BARS = 7                       # 00:00 -> 07:00 UTC
NO_TARGET_R = 50.0                  # "no target": 50 x a $48 stop is out of reach in 7 hours
SIDE = 1                            # A1 is long only
ROLLOVER_H = 20                     # a window ends at or before 20:00 UTC: never into the 20:45-22:15 rollover
WEEKDAYS = frozenset(range(5))      # Monday..Friday by the UTC day of the decision close


def _hour(text: object) -> int:
    """`HH:00` as its hour; any other form (minutes, malformed) raises."""
    s = str(text)
    if len(s) != 5 or s[2] != ":" or not s[:2].isdigit() or s[3:] != "00" or int(s[:2]) > 23:
        raise ValueError(f"entry_utc {text!r} is not a whole UTC hour HH:00")
    return int(s[:2])


def _ends_before_rollover(hour: int, name: str) -> None:
    if hour + HOLD_BARS > ROLLOVER_H:
        raise ValueError(f"{name} {hour:02d}:00 + {HOLD_BARS} h runs into the 20:45 UTC rollover")


def check(arm: Arm) -> None:
    """Load-time refusal of a row the engine cannot run as the amendment registers it: 1h, long, the fixed $ stop, the
    out-of-reach target, a 7-bar hold, weekdays only, and every window before the rollover."""
    p = arm.params
    if arm.timeframe != TIMEFRAME:
        raise ValueError(f"{arm.rule} runs on {TIMEFRAME}, not {arm.timeframe}")
    if p.get("hold_bars") != HOLD_BARS:
        raise ValueError(f"{arm.rule} holds hold_bars {HOLD_BARS} (00:00 -> 07:00 UTC)")
    if p.get("side") != SIDE:
        raise ValueError(f"{arm.rule} is long only (side {SIDE})")
    if p.get("target_r") != NO_TARGET_R:
        raise ValueError(f"{arm.rule} has no target: target_r {NO_TARGET_R}")
    if not isinstance(p.get("stop_usd"), int | float) or float(p["stop_usd"]) <= 0 or "stop_atr" in p:
        raise ValueError(f"{arm.rule} needs a fixed stop_usd > 0")
    days = p.get("weekdays")
    if not isinstance(days, list) or not days or not set(days) <= WEEKDAYS:
        raise ValueError(f"{arm.rule} weekdays must be a non-empty subset of Monday..Friday (0..4)")
    if arm.rule == "clock_hold":
        _ends_before_rollover(_hour(p.get("entry_utc")), "entry_utc")
        return
    hours = p.get("entry_hours_utc")
    if not isinstance(hours, list) or not hours or len(set(hours)) != len(hours) \
            or not all(isinstance(h, int) and 0 <= h <= 23 for h in hours):
        raise ValueError(f"{arm.rule} needs entry_hours_utc, distinct whole UTC hours")
    for h in hours:
        _ends_before_rollover(h, "entry_hours_utc")


def drawn_hour(arm_id: str, day: pd.Timestamp, hours: Sequence[int]) -> int:
    """The control's hour on UTC `day`: `hours[floor(u x len(hours))]` with u = hash_u(its id, the day's midnight)."""
    return int(hours[min(len(hours) - 1, int(hash_u(arm_id, pd.Timestamp(day).normalize()) * len(hours)))])


def _weekday_closes(arm: Arm, ctx: RuleContext) -> tuple[pd.DatetimeIndex, np.ndarray]:
    close = ctx.close_ts
    ok = (close.minute.to_numpy() == 0) & np.isin(close.dayofweek.to_numpy(), arm.params["weekdays"])
    return close, ok


def _candidates(arm: Arm, idx: np.ndarray) -> pd.DataFrame:
    if len(idx) == 0:
        return empty()
    p = arm.params
    return _frame(idx, np.full(len(idx), SIDE), np.full(len(idx), float(p["stop_usd"])), p["target_r"], p["hold_bars"])


def clock_hold(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """GD1D/GD1P (module docstring): long on the 1h close at `entry_utc` of each weekday."""
    close, ok = _weekday_closes(arm, ctx)
    ok &= close.hour.to_numpy() == _hour(arm.params["entry_utc"])
    return _candidates(arm, np.flatnonzero(ok))


def clock_hold_control(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """GD1CD/GD1CP (module docstring): long on the 1h close at the day's seeded hour from `entry_hours_utc`."""
    close, ok = _weekday_closes(arm, ctx)
    hours = list(arm.params["entry_hours_utc"])
    days = close.normalize()
    drawn = {d: drawn_hour(arm.id, d, hours) for d in days[ok].unique()}
    ok &= np.array([o and drawn[d] == h for o, d, h in zip(ok, days, close.hour, strict=True)], dtype=bool)
    return _candidates(arm, np.flatnonzero(ok))
