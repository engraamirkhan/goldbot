"""A scheduler job's heavy work in a bounded child process (ADR 0090, for `discovery_nightly`; ADR 0083's reasons: the
2-vCPU ARM brain (one core for the child, one for the engines) is shared with the engines, and the scheduler is
single-threaded).

    run_bounded(fn, args, *, budget_s, nice, memory_mb) -> BoundedResult

The child is a fresh `spawn` process (no copy of the scheduler's state or locks) that lowers its own CPU priority to
`nice`, offers itself first to the OOM killer (oom_score_adj 500, Linux), caps its data segment at `memory_mb`
(RLIMIT_DATA, Linux; macOS does not enforce it; not RLIMIT_AS, which pyarrow's and numpy's large virtual reservations
would trip long before real memory runs out) and starts with one BLAS/OpenMP thread, so it never competes with an
engine at equal priority, never costs an engine its memory, nor oversubscribes the cores. It runs `fn(*args)` and
sends back the value, or the error's type and message, with its peak RSS. Past `budget_s` the parent terminates it,
then kills it: the result says `timed_out` and the caller decides what that means. Never used on the order or exit
path."""
from __future__ import annotations

import multiprocessing as mp
import os
import resource
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from goldbot.base import FrozenRecord

THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
TERM_WAIT_S = 10.0
OOM_SCORE_ADJ = Path("/proc/self/oom_score_adj")
OOM_ADJ = "500"                                # the child goes before the engines (0) when the brain runs out


class BoundedResult(FrozenRecord):
    ok: bool
    timed_out: bool = False
    value: Any = None
    error: str | None = None
    exitcode: int | None = None
    elapsed_s: float = 0.0
    max_rss_mb: float | None = None           # the child's peak resident memory (None: it sent nothing back)


def cap_memory(memory_mb: int | None) -> None:
    """RLIMIT_DATA at `memory_mb` on Linux (private writable memory: an allocation past it raises MemoryError in the
    child); nothing elsewhere."""
    if memory_mb is None or not sys.platform.startswith("linux"):
        return
    lim = int(memory_mb) * 2**20
    resource.setrlimit(resource.RLIMIT_DATA, (lim, lim))


def volunteer_for_oom(path: Path = OOM_SCORE_ADJ) -> None:
    """On Linux, oom_score_adj 500: when the brain runs out of memory the kernel kills this child before an engine.
    Raising it needs no privilege; an OSError (no procfs, a sandbox) never stops the child. Nothing elsewhere."""
    if not sys.platform.startswith("linux"):
        return
    try:
        path.write_text(OOM_ADJ)
    except OSError:
        pass


def peak_rss_mb() -> float:
    """This process's peak resident memory in MB (ru_maxrss: KB on Linux, bytes on macOS)."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 2**20 if sys.platform == "darwin" else rss / 2**10


def _child(conn: Connection, fn: Callable[..., Any], args: tuple[Any, ...], nice: int, memory_mb: int | None) -> None:
    try:
        os.nice(max(0, nice - os.nice(0)))
        volunteer_for_oom()
        cap_memory(memory_mb)
        conn.send(("ok", fn(*args), peak_rss_mb()))
    except BaseException as exc:              # noqa: BLE001 - everything goes back to the parent as text
        conn.send(("error", f"{type(exc).__name__}: {exc}".splitlines()[0][:500], peak_rss_mb()))
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
    try:
        with _one_thread():
            proc.start()                      # may raise (an unpicklable fn or argument, no process left)
    except BaseException:
        recv.close()
        send.close()
        raise
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
            kind, payload, rss = recv.recv()
        except EOFError:                      # the child died without a word (OOM killer, a signal)
            proc.join(TERM_WAIT_S)
            return BoundedResult(ok=False, exitcode=proc.exitcode, elapsed_s=clock() - t0,
                                 error=f"the child died with exit code {proc.exitcode}")
        proc.join(TERM_WAIT_S)
        if kind == "ok":
            return BoundedResult(ok=True, value=payload, exitcode=proc.exitcode, elapsed_s=clock() - t0,
                                 max_rss_mb=rss)
        return BoundedResult(ok=False, error=str(payload), exitcode=proc.exitcode, elapsed_s=clock() - t0,
                             max_rss_mb=rss)
    finally:
        recv.close()
        if proc.is_alive():
            proc.kill()
            proc.join()
