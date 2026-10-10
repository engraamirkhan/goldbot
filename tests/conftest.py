"""Shared fixtures."""
from datetime import timedelta
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def stepping_totp_clock(monkeypatch):
    """Each read of the TOTP clock (goldbot/api/auth.py `_totp_now`) moves one 30-s step forward, so a test that
    signs in several times gets a fresh time step for every code instead of tripping the replay guard: a code made
    by `totp_code` at step s is checked one step later, inside the +-1 window. Tests of the replay guard itself use
    the `real_totp_clock` fixture."""
    import goldbot.api.auth as auth
    t = [auth.time.time()]

    def now() -> float:
        t[0] += 30.0
        return t[0]

    monkeypatch.setattr(auth, "_totp_now", now)


@pytest.fixture
def real_totp_clock(monkeypatch):
    """The wall clock for TOTP: two codes made within the same 30 s are the same code."""
    import goldbot.api.auth as auth
    monkeypatch.setattr(auth, "_totp_now", auth.time.time)


@pytest.fixture
def queued_prereg() -> dict[str, Any]:
    """`TrialRegistry.preregister` keyword arguments for a row of the pre-registered queue of the current quarter: a
    queued row must be written before its target quarter starts, so it is stamped the day before."""
    from goldbot.research.registry import quarter_of, quarter_start
    q = quarter_of()
    return {"queue": True, "target_quarter": q, "now": quarter_start(q) - timedelta(days=1)}
