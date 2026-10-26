"""One account's engine and a repair of its state files never run at once (trading-safety LOW of e7fba842).

`goldbot run repair-lost-record` rewrites orders_<account>.json, which a running engine saves over (its open-trade
table): a repair that checked "engine stopped" once and then ran unlocked could lose the engine's table if the engine
started meanwhile. Both take a flock on state/.engine-<account>.lock: the engine a SHARED lock for its whole life
(`hold_engine`), the repair an EXCLUSIVE one (`take_repair`). Either refuses while the other holds it. Opened as the
bridge smoke's lock is (`smoke.acquire_lock`: O_NOFOLLOW | O_CLOEXEC, a regular file this user owns, mode 0600).
Unix only (the brain); where flock does not exist (the Windows VPS) the engine takes none, and the repair refuses.
"""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

log = logging.getLogger(__name__)

_held: dict[tuple[str, str], int] = {}       # (state dir, account) -> the engine's descriptor, kept for its life


def lock_path(state_dir: str | Path, account_id: str) -> Path:
    return Path(state_dir) / f".engine-{account_id}.lock"


def _take(state_dir: str | Path, account_id: str, exclusive: bool) -> int:
    """The descriptor of the account's lock, taken without blocking. BlockingIOError when the other side holds it;
    OSError when it cannot be created or is not a regular file this user owns."""
    import fcntl
    path = lock_path(state_dir, account_id)
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


def take_repair(state_dir: str | Path, account_id: str) -> int:
    """The repair's exclusive hold. BlockingIOError while an engine of the account runs; OSError or ImportError when
    it cannot be taken (the caller refuses: no repair without the lock)."""
    return _take(state_dir, account_id, exclusive=True)


def release(fd: int) -> None:
    os.close(fd)                               # closing the descriptor releases the flock
