"""Shared fixtures."""
from datetime import timedelta
from typing import Any, Callable

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
def queued_prereg(monkeypatch: pytest.MonkeyPatch) -> Callable[..., dict[str, Any]]:
    """`queued_prereg(registry, **preregister_kwargs)`: write a row of the pre-registered queue of the current quarter.
    A queued row must be written before its target quarter starts and `preregister` stamps the registry's own clock
    (no backdating), so the clock is set to the day before the quarter for that one call."""
    from goldbot.research import registry as registry_mod
    q = registry_mod.quarter_of()

    def write(reg: Any, **kw: Any) -> dict[str, Any]:
        with monkeypatch.context() as m:
            m.setattr(registry_mod, "_utcnow", lambda: registry_mod.quarter_start(q) - timedelta(days=1))
            row: dict[str, Any] = reg.preregister(queue=True, target_quarter=q, **kw)
        return row
    return write
