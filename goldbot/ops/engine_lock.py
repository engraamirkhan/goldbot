"""One account's engine and a repair of its state files never run at once (trading-safety LOW of e7fba842).

`goldbot run repair-lost-record` rewrites orders_<account>.json, which a running engine saves over (its open-trade
table): a repair that checked "engine stopped" once and then ran unlocked could lose the engine's table if the engine
started meanwhile. Both take a flock on state/.engine-<account>.lock: the engine a SHARED lock for its whole life
(`hold_engine`), the repair an EXCLUSIVE one (`take_repair`). Either refuses while the other holds it. Opened as the
bridge smoke's lock is (`smoke.acquire_lock`: O_NOFOLLOW | O_CLOEXEC, a regular file this user owns, mode 0600).
Unix only (the brain); where flock does not exist (the Windows VPS) the engine takes none, and the repair refuses.

One engine per account writes its minutes (ADR 0091, 2026-11-20; trading-safety S1-S3): the
engine also takes an EXCLUSIVE flock on a lock file of its own, state/.engine-writer-<account>.lock (`hold_writer`),
for its whole life, before any bridge connection, and writes its pid into it. The repair protocol above is unchanged
(another file). Held by another engine of the account: the start is refused (the caller exits, the service manager
restarts it, the holder's state is never touched). Not takeable for any other reason (no flock on the Windows VPS,
an unwritable state dir): the engine starts and its rows are unproven (`WriterHold.held` False, recorded with its
boot). No pidfile, O_EXCL or takeover rule: the kernel releases a flock when its holder dies, kill -9 included.
"""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from goldbot.base import FrozenRecord

log = logging.getLogger(__name__)

_held: dict[tuple[str, str], int] = {}       # (state dir, account) -> the engine's descriptor, kept for its life
_writers: dict[tuple[str, str], int] = {}    # (state dir, account) -> the writer lock's descriptor, kept for its life


class WriterHold(FrozenRecord):
    """The outcome of `hold_writer`: `held` (this process holds the account's writer lock for its life); `refused`
    (another engine of the account holds it: why, naming `holder_pid` as the holder wrote it, None if unreadable).
    Neither: the lock could not be taken (no flock, an unwritable state dir) and the engine starts unproven."""
    held: bool
    refused: str | None = None
    holder_pid: int | None = None


def lock_path(state_dir: str | Path, account_id: str) -> Path:
    return Path(state_dir) / f".engine-{account_id}.lock"


def writer_lock_path(state_dir: str | Path, account_id: str) -> Path:
    return Path(state_dir) / f".engine-writer-{account_id}.lock"


def _take(state_dir: str | Path, account_id: str, exclusive: bool) -> int:
    """The descriptor of the account's lock, taken without blocking. BlockingIOError when the other side holds it;
    OSError when it cannot be created or is not a regular file this user owns."""
    return _flock(lock_path(state_dir, account_id), exclusive)


def _flock(path: Path, exclusive: bool) -> int:
    """`path`'s descriptor with its flock taken without blocking (the errors of `_take`)."""
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise PermissionError(f"{path.name} is not a regular file owned by this user")
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise
    return fd


def hold_engine(state_dir: str | Path, account_id: str) -> str | None:
    """At engine start: hold the account's lock shared for the life of the process. Returns why the engine must not
    start (a repair holds it), else None. A lock that cannot be taken for any other reason (no flock on this system,
    an unwritable state dir) is logged and the engine starts: a repair then finds no engine lock and still refuses
    unless the engine's unit is inactive and its heartbeat old (`smoke.engine_running`)."""
    key = (str(Path(state_dir).resolve()), account_id)
    if key in _held:
        return None
    try:
        _held[key] = _take(state_dir, account_id, exclusive=False)
    except BlockingIOError:
        return "a repair of its state files is running (repair-lost-record); start again when it ends"
    except (OSError, ImportError) as exc:
        log.warning("engine lock for %s not taken (%s); starting without it", account_id, type(exc).__name__)
    return None


def hold_writer(state_dir: str | Path, account_id: str) -> WriterHold:
    """At engine start, before any bridge connection: hold the account's writer lock exclusively for the life of the
    process and write this pid into it (module docstring). Never raises."""
    key = (str(Path(state_dir).resolve()), account_id)
    if key in _writers:
        return WriterHold(held=True)
    path = writer_lock_path(state_dir, account_id)
    try:
        fd = _flock(path, exclusive=True)
    except BlockingIOError:
        pid = holder_pid(path)
        return WriterHold(held=False, holder_pid=pid,
                          refused=f"another engine of {account_id} holds its writer lock (pid {pid if pid else 'unknown'})")
    except (OSError, ImportError) as exc:
        log.warning("writer lock for %s not taken (%s): starting, its minutes are unproven", account_id,
                    type(exc).__name__)
        return WriterHold(held=False)
    try:
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
    except OSError as exc:                     # the lock is held; only the refusal message loses the pid
        log.warning("writer lock for %s: pid not written (%s)", account_id, type(exc).__name__)
    _writers[key] = fd
    return WriterHold(held=True)


def holder_pid(path: Path) -> int | None:
    """The pid the writer lock's holder wrote, None when unreadable (read without the lock: a holder writes it once,
    right after taking it)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            text = os.read(fd, 32).decode(errors="replace").strip()
        finally:
            os.close(fd)
        return int(text) if text.isdigit() else None
    except OSError:
        return None


def take_repair(state_dir: str | Path, account_id: str) -> int:
    """The repair's exclusive hold. BlockingIOError while an engine of the account runs; OSError or ImportError when
    it cannot be taken (the caller refuses: no repair without the lock)."""
    return _take(state_dir, account_id, exclusive=True)


def release(fd: int) -> None:
    os.close(fd)                               # closing the descriptor releases the flock
