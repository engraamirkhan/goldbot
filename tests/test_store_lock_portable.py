"""goldbot/data/store.py locks partitions portably: fcntl.flock on POSIX, msvcrt.locking on Windows (the VPS runs
goldbot natively; fcntl does not exist there, so a module-level `import fcntl` failed every import of the store).

The Windows path is exercised here by loading a second copy of the module with sys.platform = "win32", fcntl made
unimportable and a fake msvcrt that keeps real (in-process) byte-range lock semantics: exclusive, non-blocking
LK_NBLCK raising OSError while another handle holds the byte, LK_UNLCK releasing it.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pandas as pd
import pytest

import goldbot.data.store as store_mod

REPO = Path(__file__).resolve().parents[1]


class FakeMsvcrt(types.ModuleType):
    LK_UNLCK, LK_LOCK, LK_NBLCK, LK_RLCK, LK_NBRLCK = 0, 1, 2, 3, 4

    def __init__(self) -> None:
        super().__init__("msvcrt")
        self.held: dict[tuple[int, int], int] = {}            # (st_dev, st_ino) of the locked file -> fd holding it
        self.calls: list[tuple[str, int]] = []
        self.guard = threading.Lock()

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        assert nbytes == 1 and os.lseek(fd, 0, os.SEEK_CUR) == 0, "lock byte 0 of the lock file"
        st = os.fstat(fd)
        key = (st.st_dev, st.st_ino)
        with self.guard:
            if mode == self.LK_UNLCK:
                assert self.held.get(key) == fd, "unlock by the handle that holds it"
                del self.held[key]
                self.calls.append(("unlock", fd))
                return
            assert mode == self.LK_NBLCK, "non-blocking lock, retried by the store (LK_LOCK gives up after 10 s)"
            if key in self.held:
                raise OSError(36, "Resource deadlock avoided")
            self.held[key] = fd
            self.calls.append(("lock", fd))


@pytest.fixture
def win_store():
    """A copy of goldbot/data/store.py imported as on Windows; the interpreter is restored before the test runs."""
    fake = FakeMsvcrt()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "platform", "win32")
        mp.setitem(sys.modules, "fcntl", None)                       # `import fcntl` raises ImportError
        mp.setitem(sys.modules, "msvcrt", fake)
        spec = importlib.util.spec_from_file_location("store_as_on_windows", store_mod.__file__)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod, fake


def _bars(start: str, n: int) -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "available_utc": ts, "close": range(n)})


def test_the_store_imports_and_works_without_fcntl(win_store, tmp_path):
    mod, fake = win_store
    s = mod.Store(tmp_path)
    assert s.append("bars_15m", _bars("2026-01-01", 4), source="x") == 4
    s.append("bars_15m", _bars("2026-01-01", 6), source="x")                   # overlaps: deduped under the lock
    assert len(s.read("bars_15m", source="x")) == 6
    locks = [c for c in fake.calls if c[0] == "lock"]
    assert locks and len(locks) == len(fake.calls) / 2                         # every lock released
    assert fake.held == {}


def test_a_windows_writer_waits_for_the_lock_instead_of_failing(win_store, tmp_path):
    mod, fake = win_store
    s = mod.Store(tmp_path)
    s.append("bars_15m", _bars("2026-01-01", 2), source="x")
    pdir = next(tmp_path.rglob(".lock")).parent
    done = threading.Event()

    def writer() -> None:
        s.append("bars_15m", _bars("2026-01-01", 3), source="x")
        done.set()

    with mod.Store._partition_lock(pdir):
        t = threading.Thread(target=writer)
        t.start()
        time.sleep(0.3)
        assert not done.is_set()                                               # blocked behind the holder
    t.join(10)
    assert done.is_set() and len(s.read("bars_15m", source="x")) == 3


def test_readers_release_their_windows_locks_before_closing(win_store, tmp_path):
    """Windows does not promise to release a byte-range lock on close: the reader unlocks every handle first."""
    mod, fake = win_store
    s = mod.Store(tmp_path)
    s.append("bars_15m", _bars("2026-01-01", 2), source="x")
    s.append("bars_15m", _bars("2026-02-01", 2), source="x")                       # a second partition
    fake.calls.clear()
    s.read("bars_15m", source="x")
    assert [c[0] for c in fake.calls] == ["lock", "lock", "unlock", "unlock"] and fake.held == {}


def test_posix_keeps_flock(tmp_path):
    src = Path(store_mod.__file__).read_text(encoding="utf-8")
    assert "\nimport fcntl" not in src, "fcntl only inside the POSIX branch"
    s = store_mod.Store(tmp_path)
    s.append("bars_15m", _bars("2026-01-01", 2), source="x")
    assert len(s.read("bars_15m", source="x")) == 2


def test_the_mt5_bridge_never_imports_the_store():
    """The Wine bridge installs only mt5-bridge-requirements.txt (no duckdb): it must not reach goldbot.data.store."""
    code = ("import sys\nimport goldbot.execution.bridge, goldbot.execution.mt5_adapter, goldbot.ops.accounts, "
            "goldbot.ops.bridge_compat\nprint('goldbot.data.store' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"
