"""The CONFIRM of record for 1d/1w exploration arms (ADR 0081 point 5, quant amendment; BACKLOG 254).

The rule: "the CONFIRM of record for any 1d/1w arm re-fills each trade at the first 1m open at or after its live entry
close (`long_bars.entry_close`), with Friday 1d signals dropped. Until such a CONFIRM exists, no 1d/1w arm may qualify."
Research filled a 1d/1w signal at the next bar's open (the WS-3 catalogue, `data/daily.py`) on the feature-day roll;
the engine decides on the 21:00 UTC roll and sends after the broker's reopen. Here, so history equals live:

- `decision_bars`: the engine's own daily bars (`long_bars.daily_from_1m`, the 21:00 UTC roll, the weekend folded
  into Monday) and, for 1w, their ISO weeks (`long_bars.weekly`, known at the Friday close);
- `entry_delays`: per decision close, the seconds to its live entry close (`long_bars.reopen_after` on the session
  table, `long_bars.entry_close`: 1d 23:30 UTC in EET / 22:30 in EEST, 1w Sunday 23:15 / Monday 00:15 UTC); NaN for a
  Friday 1d close (never sent live) or a close with no reopen within `long_bars.REOPEN_HORIZON` (given up live);
- the hold counts from the decision close (`triple_barrier_next_open_1m`'s close of bar idx + 1 + max_bars), as the
  engine's 1d/1w time exit does (`long_bars.time_exit_due`).

The common filter is judged at each bar's nominal entry by `arm_rules.long_tf_context` (enforced on every caller)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.engine import long_bars

BASE_S = 15 * 60          # the engine's decision clock (settings engine decision_tf 15m): entry closes fall on it


def decision_bars(b1: pd.DataFrame, tf: str) -> pd.DataFrame:
    """The 1d/1w decision bars the engine builds from these minutes (store schema, positional index). The first day
    may be partial (the minutes start inside it): it is left out, as `long_bars.joined` leaves it out live."""
    daily = long_bars.daily_from_1m(b1).iloc[1:].reset_index(drop=True)
    return (long_bars.weekly(daily) if tf == "1w" else daily).reset_index(drop=True)


def entry_delays(tf: str, closes: pd.DatetimeIndex, sessions: SessionTable = DEFAULT_SESSIONS,
                 base_s: int = BASE_S) -> np.ndarray:
    """Seconds from each decision close to its live entry close; NaN where the engine never sends it (a Friday 1d
    close; no reopen within the horizon)."""
    out = np.full(len(closes), np.nan)
    for i, c in enumerate(pd.DatetimeIndex(closes)):
        if tf == "1d" and c.weekday() == 4:
            continue
        reopen = long_bars.reopen_after(sessions, c)
        if reopen is not None:
            out[i] = (long_bars.entry_close(tf, c, reopen, base_s) - c).total_seconds()
    return out
