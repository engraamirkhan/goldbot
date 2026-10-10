"""Server clock offset probe: measure the broker's UTC offset instead of trusting `server_tz`.

MT5 stamps ticks, bars and deals in *server time*; `goldbot.data.timeutil.server_to_utc` undoes that with the
account's configured `server_tz` (`Europe/Athens` = UTC+2/+3 on EU dates, `NY+7` = UTC+2/+3 on US dates). The two
disagree by one hour in the US-EU gap weeks (second Sunday of March to the last Sunday of March; last Sunday of
October to the first Sunday of November). A wrong zone shifts the session table, the rollover gate, the Friday
weekend cut and every tick/bar timestamp by an hour.

The probe is read-only: it asks the broker for its latest tick (`last_tick`, i.e. `symbol_info_tick` through the
adapter or the bridge, already converted with the configured zone) and compares it with this host's UTC clock (kept
by chrony/w32tm; preflight checks the sync). While gold trades, ticks arrive every second, so:

    configured offset  = server_utc_offset(server_tz, tick)      # what the conversion applied
    server wall clock  = tick.ts_utc + configured offset          # the raw stamp MT5 returned
    inferred offset    = server wall clock - now, rounded to the hour
    mismatch           = |tick.ts_utc - now|                      # 0 when the zone is right and the feed is live

A mismatch over `MISMATCH_TOLERANCE` (5 min) while the market is open is an ERROR for entries only (`entry_block`);
exits are never gated by it. With the market closed the last tick is hours old and nothing can be inferred, so the
probe reports "not measured" instead of guessing.

Caveat: with the MT5 bridge, the conversion happens on the MT5 box using ITS config/accounts.yaml; the configured
offset quoted here comes from this host's copy. Both are the same file from the same repo.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.data.timeutil import server_utc_offset

MISMATCH_TOLERANCE = pd.Timedelta(minutes=5)
OFFSET_ROUND = pd.Timedelta(hours=1)          # MT5 server clocks run whole-hour offsets (UTC+2/+3)

ProbeStatus = Literal["ok", "mismatch", "not_measured"]


class ServerClockProbe(FrozenRecord):
    account_id: str
    measured_utc: UtcTimestamp
    server_tz: str
    status: ProbeStatus
    reason: str
    tick_utc: UtcTimestamp | None = None
    tick_lag_s: float | None = None             # now - tick (positive: the tick is in the past)
    configured_offset_h: float | None = None    # the offset server_tz gives at the tick
    inferred_offset_h: float | None = None      # the offset the broker's clock shows (meaningful while trading)


def _hours(td: timedelta | pd.Timedelta) -> float:
    return round(pd.Timedelta(td).total_seconds() / 3600, 2)


def _fmt(h: float) -> str:
    return f"UTC{h:+g}"


def assess(account_id: str, server_tz: str, tick_utc: pd.Timestamp, now: pd.Timestamp,
           sessions: SessionTable = DEFAULT_SESSIONS) -> ServerClockProbe:
    """Compare one tick stamp (already converted to UTC with `server_tz`) with the host's UTC `now`."""
    now = pd.Timestamp(now).tz_convert("UTC")
    tick = pd.Timestamp(tick_utc).tz_convert("UTC")
    lag = now - tick
    configured = server_utc_offset(server_tz, tick)
    wall_minus_now = (tick + pd.Timedelta(configured)) - now
    inferred = pd.Timedelta(int(np.round(wall_minus_now / OFFSET_ROUND)) * OFFSET_ROUND)
    base: dict[str, Any] = dict(account_id=account_id, measured_utc=now, server_tz=server_tz, tick_utc=tick,
                                tick_lag_s=round(lag.total_seconds(), 1), configured_offset_h=_hours(configured),
                                inferred_offset_h=_hours(inferred))
    cfg = _fmt(_hours(configured))
    if abs(lag) <= MISMATCH_TOLERANCE:
        return ServerClockProbe(status="ok", **base,
                                reason=f"server clock {cfg} matches {server_tz} (tick {lag.total_seconds():.0f} s old)")
    if not bool(sessions.is_open(pd.DatetimeIndex([now]))[0]):
        if lag > pd.Timedelta(0):       # an old tick with the market closed says nothing about the offset
            return ServerClockProbe(status="not_measured", **base,
                                    reason=f"market closed: last tick {lag.total_seconds() / 60:.0f} min old, "
                                           f"offset not measurable ({server_tz} gives {cfg})")
    offset_gap = pd.Timedelta(configured) - inferred          # == lag when the only error is the zone
    if inferred != pd.Timedelta(configured) and abs(lag - offset_gap) <= MISMATCH_TOLERANCE:
        hint = f"server clock reads {_fmt(_hours(inferred))} but {server_tz} gives {cfg}"
    else:
        way = "from the future" if lag < pd.Timedelta(0) else "stale"
        hint = (f"tick {way} by {abs(lag).total_seconds() / 60:.0f} min (stale feed, wrong server_tz or host clock; "
                f"{server_tz} gives {cfg})")
    return ServerClockProbe(status="mismatch", **base,
                            reason=f"{hint}: entries blocked until server_tz matches (RUNBOOK: server clock); "
                                   f"exits unaffected")


def measure(broker: Any, account_id: str, symbol: str, server_tz: str, now: pd.Timestamp | None = None,
            sessions: SessionTable = DEFAULT_SESSIONS, clock: Callable[[], pd.Timestamp] | None = None) -> ServerClockProbe:
    """Read the broker's latest tick (read-only `last_tick`) and assess it. A failed read is "not measured", never
    an exception: the probe must not take a health run down with it."""
    try:
        tick = broker.last_tick(symbol)
    except Exception as exc:
        t = now if now is not None else (clock() if clock else pd.Timestamp.now("UTC"))
        return ServerClockProbe(account_id=account_id, measured_utc=t, server_tz=server_tz, status="not_measured",
                                reason=f"latest tick unreadable ({type(exc).__name__})")
    t = now if now is not None else (clock() if clock else pd.Timestamp.now("UTC"))   # read after the tick: no negative lag
    return assess(account_id, server_tz, tick.ts_utc, t, sessions)


def entry_block(probe: ServerClockProbe | None) -> str | None:
    """Why entries must not open given the latest probe, or None. Hook for the engine's entry gate (it never gates
    an exit): a measured mismatch blocks; "not measured" (market closed, terminal fault) does not, because the
    stale-tick and market-closed rules already cover those cases."""
    if probe is None or probe.status != "mismatch":
        return None
    return f"server clock mismatch: {probe.reason}"


__all__ = ["MISMATCH_TOLERANCE", "ServerClockProbe", "assess", "entry_block", "measure"]
