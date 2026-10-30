"""The test clock (FR-2, ADR 0060 decision 8): run the whole suite N days ahead of the real calendar, and pin single tests.

`GOLDBOT_TEST_SHIFT_DAYS=30` moves every clock reader N days ahead. A test that bakes in a calendar date (a quarter, a
"today", a dated override that expires) passes on the day it was written and fails on everyone's PR a few days later
(CI issues #218, #220); running the suite shifted finds those on the author's own PR.

`@pytest.mark.clock_at("2026-10-30T12:00:00Z")` pins one test (or a module through `pytestmark`) to an instant: the
clock starts there and keeps ticking. That is the fix for a test that reads the calendar. `@pytest.mark.real_clock(reason=)`
opts one test out of any shift and pin: only for a test coupled to a clock the OS owns (file mtimes, `git` commit
times), never to hide a date-bound expectation.

No time library is installed (`time-machine` is the production-grade choice, ADR 0060), so this patches the readers a
test or the code under test calls: time.time / time.time_ns, datetime.datetime and datetime.date (`now`, `utcnow`,
`today`, in every module already holding the class) and pd.Timestamp.now (utcnow and today call it). Not moved:
time.monotonic / perf_counter (durations), file mtimes, C-level reads that bypass Python (`pd.Timestamp("now")`).
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import time as _time
from typing import Any

ENV = "GOLDBOT_TEST_SHIFT_DAYS"
_REAL: dict[str, Any] = {}          # originals; empty = not installed
_OFF = [0.0]                         # seconds the clock is ahead of the real one


def shift_days_from_env(environ: "os._Environ[str] | dict[str, str] | None" = None) -> int:
    raw = (os.environ if environ is None else environ).get(ENV, "").strip()
    if not raw:
        return 0
    try:
        days = int(raw)
    except ValueError:
        raise ValueError(f"{ENV}={raw!r}: need a whole number of days (0-365)") from None
    if not 0 <= days <= 365:
        raise ValueError(f"{ENV}={raw!r}: need a whole number of days (0-365)")
    return days


def offset() -> float:
    return _OFF[0]


def set_offset(seconds: float) -> None:
    """Move the clock to `seconds` ahead of the real one (installs the patches on first use)."""
    if not _REAL:
        install(0)
    _OFF[0] = seconds


def offset_to(when: Any) -> float:
    """Seconds from the real now to the instant `when` (an ISO string with a zone, a datetime or a pd.Timestamp)."""
    import pandas as pd
    ts = pd.Timestamp(when)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.timestamp() - _real_time()


def _real_time() -> float:
    return float(_REAL["time"]() if _REAL else _time.time())


def real_today() -> _dt.date:
    """Today on the real calendar, whatever shift or pin is active (quarantine expiry is judged on it)."""
    return _dt.date.fromtimestamp(_real_time())


def install(days: int) -> None:
    """Install the patches with a clock `days` ahead (0 = patched but not moved, used by pins). A repeat call only
    sets the shift again."""
    _OFF[0] = days * 86400.0
    if _REAL:
        return
    import pandas as pd
    import pydantic.v1  # noqa: F401 - subclasses `date` with its own metaclass: import it before the shifted classes exist

    real_dt: Any = _dt.datetime
    real_date: Any = _dt.date
    real_time, real_time_ns = _time.time, _time.time_ns
    real_ts_now = pd.Timestamp.now.__func__ if hasattr(pd.Timestamp.now, "__func__") else None
    _REAL.update(dt=real_dt, date=real_date, time=real_time, time_ns=real_time_ns,
                 ts_now=pd.Timestamp.__dict__.get("now"), replaced=[])

    def off() -> _dt.timedelta:
        return _dt.timedelta(seconds=_OFF[0])

    class _DateMeta(type):
        def __instancecheck__(cls, obj: Any) -> bool:
            return isinstance(obj, real_date)

    class _DatetimeMeta(type):
        def __instancecheck__(cls, obj: Any) -> bool:
            return isinstance(obj, real_dt)

    class ShiftedDate(real_date, metaclass=_DateMeta):
        @classmethod
        def today(cls) -> Any:
            return cls.fromtimestamp(real_time() + _OFF[0])         # C `today()` reads the patched time.time itself

    class ShiftedDatetime(real_dt, metaclass=_DatetimeMeta):
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return real_dt.now(tz) + off()

        @classmethod
        def utcnow(cls) -> Any:
            return real_dt.utcnow() + off()

        @classmethod
        def today(cls) -> Any:
            return cls.fromtimestamp(real_time() + _OFF[0])

    ShiftedDate.__name__ = ShiftedDate.__qualname__ = "date"
    ShiftedDatetime.__name__ = ShiftedDatetime.__qualname__ = "datetime"
    ShiftedDate.__module__ = ShiftedDatetime.__module__ = "datetime"

    swaps: dict[Any, Any] = {real_dt: ShiftedDatetime, real_date: ShiftedDate}
    setattr(_dt, "datetime", ShiftedDatetime)
    setattr(_dt, "date", ShiftedDate)
    for mod in list(sys.modules.values()):                            # `from datetime import datetime` copies
        ns = getattr(mod, "__dict__", None)
        if not isinstance(ns, dict) or mod is _dt:
            continue
        for name, value in list(ns.items()):
            try:
                repl = swaps.get(value) if isinstance(value, type) else None
            except TypeError:
                continue
            if repl is not None:
                ns[name] = repl
                _REAL["replaced"].append((ns, name, value))

    _time.time = lambda: real_time() + _OFF[0]
    _time.time_ns = lambda: real_time_ns() + int(_OFF[0] * 1_000_000_000)

    if real_ts_now is not None:
        setattr(pd.Timestamp, "now", classmethod(lambda cls, tz=None: real_ts_now(cls, tz) + off()))


def uninstall() -> None:
    if not _REAL:
        return
    import pandas as pd

    setattr(_dt, "datetime", _REAL["dt"])
    setattr(_dt, "date", _REAL["date"])
    for ns, name, value in _REAL["replaced"]:
        ns[name] = value
    _time.time, _time.time_ns = _REAL["time"], _REAL["time_ns"]
    orig = _REAL.get("ts_now")
    if orig is not None:
        setattr(pd.Timestamp, "now", orig)
    _REAL.clear()
    _OFF[0] = 0.0
