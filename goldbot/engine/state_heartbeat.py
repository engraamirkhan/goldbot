"""The host-clock state write while the market is closed (BACKLOG 59), moved verbatim out of engine/runner.py (ADR 0023:
runner.py only shrinks): `due` was `Engine._state_heartbeat_due`, `write` `_write_heartbeat`, `feed`
`_heartbeat_feed`. The runner calls `due`/`write` from `_on_tick` and `feed` from `_write_state`."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine


def due(eng: Engine) -> bool:
    """BACKLOG 59: on the live clock, with the market CLOSED per the session table, no state write for
    state_every_s of HOST time. With the market closed the tick stamp is frozen, so the tick-time cadence above
    never fires and health would read a live engine's file (bridge_compat `checked_at`, `ts`) as hours old.
    Never in an open session (review HIGH): a frozen feed in market hours must leave the file to age, so health
    FAILs it (bridge_compat after 5 min, engine after 45 min) as before. A session table that cannot be read is
    not due (fail toward the alert). A host clock stepped back is due in any session, so the file never keeps a
    stamp from the future (BACKLOG 58). Replays and backtests (tick clock only) never take this path."""
    if not eng.cfg.live_clock:
        return False
    now = eng.clock()
    elapsed = math.inf if eng._state_written_host is None else (now - eng._state_written_host).total_seconds()
    if elapsed < 0:
        return True
    if elapsed < eng.cfg.state_every_s:
        return False
    try:
        return eng.sessions.entry_block(now) is not None
    except Exception:
        return False


def write(eng: Engine) -> None:
    """The host-clock state write (BACKLOG 59): it republishes and decides nothing. No account read, no stage
    machine, kill switch, weekend rule or other exit step (those stay on the tick-time refresh), and no EngineState
    field changes. The fields that describe the feed are restated on the host clock so they stay true: the last
    tick's age and lead, and whether the session table has the market closed now."""
    eng._state_written_host = eng.clock()   # a failing write is retried (and a tick error) once per cadence
    eng._write_state(heartbeat=True)


def feed(eng: Engine) -> dict[str, Any]:
    """`last_tick_age_s`, `tick_ahead_s` and `market_closed` now on the host clock, as `_refresh_account` measures
    them (signed age; a broken session table reads closed). Empty before the first valid tick."""
    if eng._last_tick is None:
        return {}
    now = eng.clock()
    age = (now - eng._last_tick.ts_utc).total_seconds()
    try:
        closed = eng.sessions.entry_block(now) is not None
    except Exception:
        closed = True
    return {"last_tick_age_s": abs(age), "tick_ahead_s": max(0.0, -age), "market_closed": closed}
