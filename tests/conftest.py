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


@pytest.fixture(autouse=True)
def email_code_keyring(monkeypatch) -> dict[str, str]:
    """The email-code HMAC key (goldbot/api/auth.py `email_code_key`) in a dict instead of the OS keyring, and the
    in-process key cache emptied: no test ever writes `email-code-hmac-key` to the developer's keyring or to
    state/.secrets.json. A test reads or edits the dict to see or break the stored key."""
    import goldbot.api.auth as auth
    store: dict[str, str] = {}
    monkeypatch.setattr(auth, "_secret_get", store.get)
    monkeypatch.setattr(auth, "_secret_set", store.__setitem__)
    monkeypatch.setattr(auth, "_KEY_CACHE", {})
    return store


@pytest.fixture
def real_totp_clock(monkeypatch):
    """The wall clock for TOTP: two codes made within the same 30 s are the same code."""
    import goldbot.api.auth as auth
    monkeypatch.setattr(auth, "_totp_now", auth.time.time)


@pytest.fixture
def lockout_clock(monkeypatch):
    """The sign-in lockout's clock (goldbot/api/auth.py counts `auth_failures` by `time.time_ns`), pinned to one
    instant and moved only by the test (`clock[0] += ns`). A lockout test then proves the count, never how fast the
    machine ran: on the wall clock, a paused VM or a starved worker that lets 15 minutes pass mid-test prunes the
    failures and the sign-in goes through (BACKLOG 55)."""
    import goldbot.api.auth as auth
    clock = [auth.time.time_ns()]
    monkeypatch.setattr(auth.time, "time_ns", lambda: clock[0])
    return clock


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


def pytest_terminal_summary(terminalreporter: Any, config: Any) -> None:
    """The test order is random (pytest-randomly, dev extras); `-q` hides its header, so the seed is printed at the end
    of every run, CI included, to reproduce a failure that depends on the order (trading-safety review LOW-5)."""
    seed = getattr(config.option, "randomly_seed", None)
    if isinstance(seed, int) and config.pluginmanager.hasplugin("randomly"):
        terminalreporter.write_line(f"pytest-randomly seed {seed}: reproduce with -p randomly --randomly-seed={seed}")


@pytest.fixture(autouse=True)
def base_trial_budget(monkeypatch) -> None:
    """The shipped settings carry a dated owner override (research.trial_budget_overrides, 2026Q4 = 53); tests that
    pin the base quarterly budget of 20 run without it. tests/test_config.py reads the shipped override from the yaml."""
    from goldbot.config import load_settings
    for quarter in list(load_settings().research.trial_budget_overrides):       # the dict is mutable though the model is frozen
        monkeypatch.delitem(load_settings().research.trial_budget_overrides, quarter)


def shard_of(nodeid: str, count: int) -> int:
    """Shard (0..count-1) a test belongs to: a stable hash of its node id, so every test lands in exactly one shard
    on every machine and run (no pytest-split dependency, no timing file to keep fresh)."""
    import zlib
    return zlib.crc32(nodeid.encode()) % count


def pytest_collection_modifyitems(config, items) -> None:
    """CI runs the unit suite as a matrix: GOLDBOT_SHARD=i/n keeps only the tests of shard i (0-based) of n and
    deselects the rest. Unset (local runs) selects everything."""
    import os
    spec = os.environ.get("GOLDBOT_SHARD")
    if not spec:
        return
    index, count = (int(x) for x in spec.split("/"))
    if not 0 <= index < count:
        raise pytest.UsageError(f"GOLDBOT_SHARD={spec!r}: need 0 <= i < n")
    keep = [i for i in items if shard_of(i.nodeid, count) == index]
    config.hook.pytest_deselected(items=[i for i in items if i not in keep])
    items[:] = keep
