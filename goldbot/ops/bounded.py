"""A scheduler job's heavy work in a bounded child process (ADR 0090, for `discovery_nightly`; ADR 0083's reasons: the
4-core ARM brain is shared with the engines, and the scheduler is single-threaded).

    run_bounded(fn, args, *, budget_s, nice, memory_mb) -> BoundedResult

The child is a fresh `spawn` process (no copy of the scheduler's state or locks) that lowers its own CPU priority to
`nice`, caps its data segment at `memory_mb` (RLIMIT_DATA, Linux; macOS does not enforce it; not RLIMIT_AS, which
pyarrow's and numpy's large virtual reservations would trip long before real memory runs out) and starts with one BLAS/OpenMP
thread, so it never competes with an engine at equal priority nor oversubscribes the cores. It runs `fn(*args)` and
sends back the value, or the error's type and message. Past `budget_s` the parent terminates it, then kills it: the
result says `timed_out` and the caller decides what that means. Never used on the order or exit path."""
from __future__ import annotations

import multiprocessing as mp
import os
import resource
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from multiprocessing.connection import Connection
from typing import Any

from goldbot.base import FrozenRecord

THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
TERM_WAIT_S = 10.0


class BoundedResult(FrozenRecord):
    ok: bool
    timed_out: bool = False
    value: Any = None
    error: str | None = None
    exitcode: int | None = None
    elapsed_s: float = 0.0


def cap_memory(memory_mb: int | None) -> None:
    """RLIMIT_DATA at `memory_mb` on Linux (private writable memory: an allocation past it raises MemoryError in the
    child); nothing elsewhere."""
    if memory_mb is None or not sys.platform.startswith("linux"):
        return
    lim = int(memory_mb) * 2**20
    resource.setrlimit(resource.RLIMIT_DATA, (lim, lim))


def _child(conn: Connection, fn: Callable[..., Any], args: tuple[Any, ...], nice: int, memory_mb: int | None) -> None:
    try:
        os.nice(max(0, nice - os.nice(0)))
        cap_memory(memory_mb)
        conn.send(("ok", fn(*args)))
    except BaseException as exc:              # noqa: BLE001 - everything goes back to the parent as text
        conn.send(("error", f"{type(exc).__name__}: {exc}".splitlines()[0][:500]))
    finally:
        conn.close()


@contextmanager
def _one_thread() -> Iterator[None]:
    """The spawned child inherits the environment at start: one BLAS/OpenMP thread unless the host already set one.
    The parent's own environment is restored."""
    old = {k: os.environ.get(k) for k in THREAD_VARS}
    for k in THREAD_VARS:
        os.environ.setdefault(k, "1")
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def run_bounded(fn: Callable[..., Any], args: tuple[Any, ...], *, budget_s: float, nice: int,
                memory_mb: int | None, clock: Callable[[], float] = time.monotonic) -> BoundedResult:
    """`fn(*args)` in a bounded child (module docstring). `fn` and `args` must be picklable (a module-level function)."""
    ctx = mp.get_context("spawn")
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(send, fn, args, nice, memory_mb), daemon=True,
                       name=f"bounded:{getattr(fn, '__name__', 'job')}")
    t0 = clock()
    with _one_thread():
        proc.start()
    send.close()
    try:
        if not recv.poll(budget_s):
            proc.terminate()
            proc.join(TERM_WAIT_S)
            if proc.is_alive():
                proc.kill()
                proc.join()
            return BoundedResult(ok=False, timed_out=True, exitcode=proc.exitcode, elapsed_s=clock() - t0,
                                 error=f"killed after its {budget_s:.0f} s budget")
        try:
            kind, payload = recv.recv()
        except EOFError:                      # the child died without a word (OOM killer, a signal)
            proc.join(TERM_WAIT_S)
            return BoundedResult(ok=False, exitcode=proc.exitcode, elapsed_s=clock() - t0,
                                 error=f"the child died with exit code {proc.exitcode}")
        proc.join(TERM_WAIT_S)
        if kind == "ok":
            return BoundedResult(ok=True, value=payload, exitcode=proc.exitcode, elapsed_s=clock() - t0)
        return BoundedResult(ok=False, error=str(payload), exitcode=proc.exitcode, elapsed_s=clock() - t0)
    finally:
        recv.close()
        if proc.is_alive():
            proc.kill()
            proc.join()
