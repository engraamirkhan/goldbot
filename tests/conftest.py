"""Shared fixtures."""
import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

import pytest

from tests import clockshift

# FR-2: GOLDBOT_TEST_SHIFT_DAYS=N runs every test N days ahead of the real calendar (tests/clockshift.py). Installed
# at import, before collection, so module-level clock reads in tests and production code are shifted too; xdist
# workers import this file and inherit the variable.
SHIFT_DAYS = clockshift.shift_days_from_env()
clockshift.install(SHIFT_DAYS)         # always, even at 0: the patched classes must exist before any model imports them
                                        # (a pydantic/FastAPI model built on the real `datetime` and one built on the patched
                                        # class disagree), so every run, shifted or not, runs on the same classes


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


@pytest.fixture
def enabled_accounts(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """`enabled_accounts("a", "b")`: exactly these accounts count as enabled for the readers that list engine state
    (goldbot.ops.accounts.engine_state_files), whatever config/accounts.yaml says."""
    from goldbot.ops import accounts

    def enable(*ids: str) -> None:
        monkeypatch.setattr(accounts, "account_ids",
                            lambda: accounts.AccountIds(listed=frozenset(ids), enabled=frozenset(ids)))
    return enable


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


_BLOCKED_CLIS = {"gh", "ssh", "curl"}


def _blocked_cli(args: Any) -> str | None:
    """The blocked CLI a subprocess argv (or shell string) would run, else None."""
    import os
    import shlex
    if isinstance(args, (str, bytes, os.PathLike)):
        text = os.fsdecode(args)
        try:
            words = shlex.split(text)
        except ValueError:
            words = text.split()
        candidates = words      # a shell line: any word may be a command
    else:
        try:
            candidates = [os.fsdecode(a) for a in args]
        except TypeError:
            return None
    if candidates and os.path.basename(candidates[0]) in {"sh", "bash", "zsh"}:
        candidates = [w for c in candidates[1:] for w in c.split()]     # only a shell's argv can hide a command word
    elif not isinstance(args, (str, bytes, os.PathLike)):
        candidates = candidates[:1]
    import tempfile
    tmp = os.path.realpath(tempfile.gettempdir())
    for word in candidates:
        if os.path.basename(word) in _BLOCKED_CLIS:
            if os.sep in word and os.path.realpath(word).startswith(tmp + os.sep):
                continue                # a fake `gh` script a test wrote under tmp_path, not the real tool
            return os.path.basename(word)
    return None


@pytest.fixture(autouse=True)
def no_real_network_or_cli(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """CI has no `gh` token, no ssh keys and no network, a Mac has all three, so a test that reaches for them passes
    locally and fails in CI (a test that called the real `gh` did exactly that). Any test that runs `gh`/`ssh`/`curl`
    as a subprocess or connects a socket beyond loopback / unix sockets fails here, on every machine, unless it is
    marked `@pytest.mark.network`. Stub the call instead (monkeypatch `subprocess.run` or the module's wrapper)."""
    if request.node.get_closest_marker("network"):
        return
    import socket
    import subprocess

    real_init = subprocess.Popen.__init__

    def popen_init(self: Any, args: Any, *a: Any, **kw: Any) -> None:
        cli = _blocked_cli(args)
        if cli:
            raise AssertionError(f"test ran the real `{cli}` ({args!r}): stub it or mark the test @pytest.mark.network")
        real_init(self, args, *a, **kw)

    monkeypatch.setattr(subprocess.Popen, "__init__", popen_init)

    def local(address: Any) -> bool:
        host = address[0] if isinstance(address, tuple) and address else address
        return isinstance(host, str) and (host in {"localhost", "::1", "", "0.0.0.0"} or host.startswith("127."))

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def refuse(address: Any) -> None:
        raise AssertionError(f"test opened a network socket to {address!r}: stub it or mark the test @pytest.mark.network")

    def connect(self: socket.socket, address: Any) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6) and not local(address):
            refuse(address)
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if self.family in (socket.AF_INET, socket.AF_INET6) and not local(address):
            refuse(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)

    real_gai = socket.getaddrinfo

    def getaddrinfo(host: Any, *a: Any, **kw: Any) -> Any:
        if isinstance(host, (str, bytes)) and not local((os.fsdecode(host),)) and not _is_ip(os.fsdecode(host)):
            refuse((host,))
        return real_gai(host, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def _is_ip(host: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def shard_of(nodeid: str, count: int) -> int:
    """Shard (0..count-1) a test belongs to: a stable hash of its node id, so every test lands in exactly one shard
    on every machine and run (no pytest-split dependency)."""
    import zlib
    return zlib.crc32(nodeid.encode()) % count


WEIGHTS_FILE = Path(__file__).resolve().parent / "shard_weights.json"


def load_shard_weights() -> dict[str, float]:
    """Seconds of the known-heavy tests (tests/shard_weights.json; refresh with `scripts/ci_summary.py weights`).
    The file only steers balance: a stale or missing entry costs shard balance, never a test (every collected test
    is still assigned to exactly one shard)."""
    import json
    try:
        data = json.loads(WEIGHTS_FILE.read_text())
        # integration and unit ids never overlap, so one map serves both suites
        return {str(k): float(v) for k, v in {**data.get("unit_seconds", {}), **data["seconds"]}.items()}
    except (OSError, ValueError, KeyError):
        return {}


def shard_assignment(nodeids: list[str], count: int, weights: dict[str, float]) -> dict[str, int]:
    """Shard of every test: the weighted (heavy) tests first, longest first, each onto the currently lightest shard
    (LPT); every other test by `shard_of`. Depends only on the set of ids and the weights, never on collection
    order, so every shard and every xdist worker computes the same answer."""
    loads = [0.0] * count
    out: dict[str, int] = {}
    for n in sorted((n for n in set(nodeids) if n in weights), key=lambda n: (-weights[n], n)):
        s = min(range(count), key=lambda k: (loads[k], k))
        out[n] = s
        loads[s] += weights[n]
    for n in nodeids:
        out.setdefault(n, shard_of(n, count))
    return out


def pytest_collection_modifyitems(config, items) -> None:
    """CI runs the unit and integration suites as matrices: GOLDBOT_SHARD=i/n keeps only the tests of shard i
    (0-based) of n and deselects the rest. Unset (local runs) selects everything. Quarantine marks apply first."""
    import os
    apply_quarantine(items)
    spec = os.environ.get("GOLDBOT_SHARD")
    if not spec:
        return
    index, count = (int(x) for x in spec.split("/"))
    if not 0 <= index < count:
        raise pytest.UsageError(f"GOLDBOT_SHARD={spec!r}: need 0 <= i < n")
    owner = shard_assignment([i.nodeid for i in items], count, load_shard_weights())
    keep = [i for i in items if owner[i.nodeid] == index]
    config.hook.pytest_deselected(items=[i for i in items if i not in keep])
    items[:] = keep


def pytest_configure(config: Any) -> None:
    config.addinivalue_line("markers", "quarantine(issue, until): known-bad test, skipped until the date (YYYY-MM-DD, at "
                            "most 48 h out when set); after `until` it FAILS the run so it is fixed or re-dated, never forgotten")
    config.addinivalue_line("markers", "clock_at(when): pin this test's clock to the instant `when` (ISO string with a zone); "
                            "it keeps ticking from there. The fix for a test that reads the calendar (tests/clockshift.py)")
    config.addinivalue_line("markers", "real_clock(reason): run on the real clock whatever shift is active; only for a test "
                            "coupled to a clock the OS owns (file mtimes, git commit times), the reason says which")
    config.addinivalue_line("markers", "guard: a guard test (golden, registry, allowlist, line cap, shard partition); "
                            "`pytest -m guard` is the set a lane runs on every iteration, whatever else it touched")


def pytest_report_header(config: Any) -> str | None:
    if SHIFT_DAYS:
        import datetime as dt
        return f"goldbot clock shifted +{SHIFT_DAYS} days (GOLDBOT_TEST_SHIFT_DAYS): today is {dt.date.today()}"
    return None


def quarantine_terms(item: Any) -> tuple[str, Any] | None:
    """(issue, until) of a `quarantine` marker, validated: both are required and `until` is an ISO date."""
    import datetime
    mark = item.get_closest_marker("quarantine")
    if mark is None:
        return None
    issue, until = mark.kwargs.get("issue"), mark.kwargs.get("until")
    if not issue or not until or mark.args:
        raise pytest.UsageError(f"{item.nodeid}: quarantine needs issue=... and until='YYYY-MM-DD' (keywords only)")
    try:
        return str(issue), datetime.date.fromisoformat(str(until))
    except ValueError:
        raise pytest.UsageError(f"{item.nodeid}: quarantine until={until!r} is not an ISO date") from None


def apply_quarantine(items: list[Any]) -> None:
    """Quarantined tests are skipped until `until`; once it has passed they run and FAIL (an expired quarantine is a
    debt the build must not carry silently). Judged on the real calendar, never the shifted one."""
    today = clockshift.real_today()
    for item in items:
        terms = quarantine_terms(item)
        if terms is None:
            continue
        issue, until = terms
        if today > until:
            item._goldbot_expired_quarantine = (issue, until)
        else:
            item.add_marker(pytest.mark.skip(reason=f"quarantined until {until} ({issue})"))


@pytest.fixture(autouse=True)
def expired_quarantine_fails(request: pytest.FixtureRequest) -> None:
    expired = getattr(request.node, "_goldbot_expired_quarantine", None)
    if expired:
        pytest.fail(f"quarantine of {request.node.nodeid} expired on {expired[1]} ({expired[0]}): fix the test or "
                    f"re-date the quarantine with a reason on the issue", pytrace=False)


@pytest.fixture(autouse=True)
def test_clock(request: pytest.FixtureRequest) -> Any:
    """Apply the `clock_at` / `real_clock` marks (tests/clockshift.py) for the length of one test."""
    pin, real = request.node.get_closest_marker("clock_at"), request.node.get_closest_marker("real_clock")
    if pin is None and real is None:
        yield
        return
    if real is not None and not (real.kwargs.get("reason") or (real.args and real.args[0])):
        pytest.fail("real_clock needs a reason: which OS clock is this test coupled to?", pytrace=False)
    before = clockshift.offset()
    if real is not None:
        clockshift.set_offset(0.0) if clockshift._REAL else None
    else:
        assert pin is not None and pin.args, "clock_at needs the instant, e.g. clock_at('2026-10-30T12:00:00Z')"
        clockshift.set_offset(clockshift.offset_to(pin.args[0]))
    try:
        yield
    finally:
        if clockshift._REAL:
            clockshift.set_offset(before)
