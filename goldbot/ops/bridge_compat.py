"""Is the MT5 bridge compatible with this brain? (BACKLOG 42, gap G6 in docs/deploy/2026-10-batch.md)

The two boxes are deployed separately, so the engine on the brain can run newer code than the bridge on the MT5 box:
a brain that sizes through `loss_per_lot` against a bridge without it refuses every entry, and a bridge that converts
MT5 stamps with another `server_tz` than the brain puts every session, rollover and Friday cut an hour off. The bridge
reports on its unauthenticated GET /health (goldbot/execution/bridge.py) its `server_tz`, protocol `api_version`, the
`methods` it serves and the `commit` it runs; this module reads that and judges it:

* `incompatible` (entries refused with the gate reason `bridge_incompatible`, health FAIL, preflight blocker): a method
  the brain calls is missing, the protocol versions do not meet each other's `min_compatible`, the zone differs or is
  not a zone name, or the bridge does not report these at all (a bridge older than this check, junk on the port, a
  /health body over 64 KiB).
* `unchecked` (also refuses entries as `bridge_incompatible`): the engine has not confirmed the bridge yet (it starts
  in this state), has only ever seen /health unreachable, or its last confirmation is over 5 minutes old.
* `unreachable`: /health did not answer (network, tunnel, 5xx, the total read deadline). After an earlier reachable
  `ok` it is not a block by itself (no ticks either: the stale-data rule blocks entries) until the `ok` ages out;
  after an `incompatible` that verdict is kept (`next_verdict`); health reports it (`bridge:<account>` fails).
* `ok`; a different commit on the two boxes is reported (health warning) but never blocks: doc-only commits differ.

Exits never read any of this: the verdict is one RiskGate input for entries only. The engine checks at start and every
60 s on a background thread (`BridgeCompatChecker`, goldbot/ops/run.py), so the tick loop never waits for /health.
Nothing here carries the bridge URL into a reason (it names the tunnel endpoint).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Literal

import pandas as pd
import requests
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.config import ROOT
from goldbot.data.timeutil import CLOCK_AHEAD_TOLERANCE_S
from goldbot.execution.bridge import API_VERSION, METHODS, MIN_COMPATIBLE, OPTIONAL_METHODS

log = logging.getLogger("goldbot.bridge_compat")

CompatStatus = Literal["ok", "incompatible", "unreachable", "unchecked"]
CHECK_TIMEOUT_S = 5.0                    # /health reads only the terminal state (no order); also the total read deadline
MAX_HEALTH_BYTES = 64 * 1024             # the bridge's report is ~400 bytes; anything larger is not it
MAX_VERDICT_AGE_S = 300.0                # an ok older than this counts as unchecked (checks run every 60 s)
UNCHECKED_GRACE_S = 120.0                # health: an engine unchecked longer than this FAILs (Telegram); warns inside
HUNG_AFTER_S = 3 * CHECK_TIMEOUT_S       # a check thread alive this long is abandoned and replaced (daemon thread)
REQUIRED_METHODS = frozenset(METHODS) - OPTIONAL_METHODS   # every method the brain calls without a fallback
_TZ = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")             # a zone name (Europe/Athens, NY+7); anything else is refused
_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")     # sha-1 or sha-256 object names


def repo_commit(root: Path = ROOT) -> str | None:
    """The commit checked out at `root`, read from the .git files (no subprocess: the bridge runs under the Wine Python,
    where no git binary is on the path). Handles a detached HEAD (how goldbot-deploy checks out), a branch ref (loose
    or packed) and a linked worktree. None when it cannot be read; never raises."""
    try:
        dot = root / ".git"
        if dot.is_file():                                  # a linked worktree: "gitdir: <path>"
            line = dot.read_text(encoding="utf-8").strip()
            if not line.startswith("gitdir:"):
                return None
            gitdir = (root / line.split(":", 1)[1].strip()).resolve()
            common_file = gitdir / "commondir"
            common = (gitdir / common_file.read_text(encoding="utf-8").strip()).resolve() \
                if common_file.is_file() else gitdir
        else:
            gitdir = common = dot
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head[4:].strip()
            if not ref.startswith("refs/") or ".." in ref:
                return None
            sha = None
            for base in (gitdir, common):
                f = base / ref
                if f.is_file():
                    sha = f.read_text(encoding="utf-8").strip()
                    break
            packed = common / "packed-refs"
            if sha is None and packed.is_file():
                for row in packed.read_text(encoding="utf-8").splitlines():
                    parts = row.split()
                    if len(parts) == 2 and parts[1] == ref:
                        sha = parts[0]
                        break
        else:
            sha = head
    except (OSError, ValueError):
        return None
    return sha if sha is not None and _SHA.fullmatch(sha) else None


class BridgeInfo(FrozenRecord):
    """What the bridge's GET /health said. `reachable` False: no answer in time; `problem` set: it answered, but not
    with a bridge's report (older bridge without /health, junk, a body over MAX_HEALTH_BYTES)."""

    reachable: bool
    problem: str | None = None
    server_tz: str | None = None
    server_tz_invalid: bool = False      # reported, but not a zone name (never echoed)
    api_version: int | None = None
    min_compatible: int | None = None
    methods: list[str] | None = None
    commit: str | None = None


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def read_bridge_info(url: str, timeout_s: float = CHECK_TIMEOUT_S, get: Callable[..., Any] = requests.get) -> BridgeInfo:
    """GET <url>/health (no token, no order: only the terminal state is read), streamed: at most MAX_HEALTH_BYTES are read, and the
    whole GET runs on a daemon thread joined with a hard deadline of `timeout_s`. requests' timeout is per socket read,
    so a server dripping header bytes would otherwise hold the call far longer; an abandoned read ends when the socket
    does (one at most per check, i.e. per minute)."""
    box: dict[str, BridgeInfo] = {}

    def work() -> None:
        try:
            box["info"] = _read_bridge_info(url, timeout_s, get)
        except Exception as exc:                      # the type only: a message can name the tunnel endpoint
            box["info"] = BridgeInfo(reachable=False, problem=f"/health unreadable ({type(exc).__name__})")
    t = threading.Thread(target=work, name="bridge-health-get", daemon=True)
    t.start()
    t.join(timeout_s)
    info = box.get("info")
    if t.is_alive() or info is None:
        return BridgeInfo(reachable=False, problem=f"/health not read within the {timeout_s:g} s deadline")
    return info


def _read_bridge_info(url: str, timeout_s: float, get: Callable[..., Any]) -> BridgeInfo:
    deadline = time.monotonic() + timeout_s
    try:
        r = get(f"{url.rstrip('/')}/health", timeout=timeout_s, stream=True)
    except requests.RequestException as exc:
        return BridgeInfo(reachable=False, problem=f"/health unreachable ({type(exc).__name__})")
    try:
        if r.status_code >= 500:
            return BridgeInfo(reachable=False, problem=f"/health answered HTTP {r.status_code}")
        if r.status_code != 200:
            return BridgeInfo(reachable=True, problem=f"/health answered HTTP {r.status_code} (a bridge without /health)")
        buf = bytearray()
        for chunk in r.iter_content(4096):
            buf += chunk
            if len(buf) > MAX_HEALTH_BYTES:
                return BridgeInfo(reachable=True, problem="/health answered over 64 KiB: not the bridge's report")
            if time.monotonic() > deadline:
                return BridgeInfo(reachable=False, problem=f"/health not read within the {timeout_s:g} s deadline")
    except requests.RequestException as exc:
        return BridgeInfo(reachable=False, problem=f"/health unreadable ({type(exc).__name__})")
    finally:
        try:
            r.close()
        except Exception:
            pass
    try:
        body = json.loads(bytes(buf))
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("ok") is not True:
        return BridgeInfo(reachable=True, problem="/health answered, but not with the bridge's report")
    tz, methods, commit = body.get("server_tz"), body.get("methods"), body.get("commit")
    tz_ok = isinstance(tz, str) and _TZ.fullmatch(tz) is not None
    return BridgeInfo(reachable=True, server_tz=tz if tz_ok else None, server_tz_invalid=tz is not None and not tz_ok,
                      api_version=_int(body.get("api_version")), min_compatible=_int(body.get("min_compatible")),
                      methods=[m for m in methods if isinstance(m, str)] if isinstance(methods, list) else None,
                      commit=commit if isinstance(commit, str) and _SHA.fullmatch(commit) else None)


def _short(sha: str | None) -> str:
    return sha[:12] if sha else "unknown"


class BridgeCompat(FrozenRecord):
    """The verdict on one bridge. `blocks`: entries are refused (`bridge_incompatible`). `checked_at`: when the bridge
    last answered a check (None: never confirmed); a verdict older than MAX_VERDICT_AGE_S counts as unchecked
    (`effective`)."""

    status: CompatStatus
    reason: str
    missing: list[str] = Field(default_factory=list)     # methods the brain calls that the bridge does not serve
    bridge_api_version: int | None = None
    bridge_commit: str | None = None
    brain_commit: str | None = None
    commit_differs: bool = False
    checked_at: UtcTimestamp | None = None
    unchecked_since: UtcTimestamp | None = None          # when the engine became unchecked (kept while it stays so)

    @property
    def blocks(self) -> bool:
        return self.status in ("incompatible", "unchecked")


def unchecked(why: str, checked_at: pd.Timestamp | None = None, *, now: pd.Timestamp | None = None,
              since: pd.Timestamp | None = None) -> BridgeCompat:
    """Not confirmed (engine starting, never reached, or the last confirmation too old): blocks entries.
    `unchecked_since` is `since` when it continues an earlier unchecked verdict, else `now` (default: the host clock),
    so health can tell a start-up moment from an engine stuck blocked (UNCHECKED_GRACE_S)."""
    at = since if since is not None else now if now is not None else pd.Timestamp.now("UTC")
    return BridgeCompat(status="unchecked", reason=f"bridge not confirmed: {why}", checked_at=checked_at,
                        unchecked_since=at)


def assess(info: BridgeInfo, server_tz: str, brain_commit: str | None, *, now: pd.Timestamp | None = None,
           required: frozenset[str] = REQUIRED_METHODS, api_version: int = API_VERSION,
           min_compatible: int = MIN_COMPATIBLE) -> BridgeCompat:
    """The verdict for a /health report against this brain's methods, protocol versions, zone and commit. `now` stamps
    `checked_at` on a reachable answer (default: the host clock)."""
    if not info.reachable:
        return BridgeCompat(status="unreachable", reason=f"{info.problem or '/health unreachable'}: compatibility not "
                            "checked (no ticks either: the stale-data rule blocks entries)", brain_commit=brain_commit)
    at = now if now is not None else pd.Timestamp.now("UTC")
    problems: list[str] = []
    missing: list[str] = []
    if info.problem is not None:
        problems.append(info.problem)
    elif info.methods is None or info.api_version is None or info.min_compatible is None:
        problems.append("the bridge does not report its protocol (older than BACKLOG 42): deploy this commit to the "
                        "MT5 box")
    else:
        missing = sorted(required - set(info.methods))
        if missing:
            problems.append(f"the bridge lacks {', '.join(missing)}")
        if info.api_version < min_compatible:
            problems.append(f"bridge protocol v{info.api_version} is below this brain's min_compatible "
                            f"v{min_compatible}")
        if api_version < info.min_compatible:
            problems.append(f"this brain's protocol v{api_version} is below the bridge's min_compatible "
                            f"v{info.min_compatible}")
    if info.problem is None:
        if info.server_tz_invalid:
            problems.append("the bridge reports an invalid server_tz")
        elif info.server_tz is None:
            problems.append("the bridge does not report its server_tz")
        elif info.server_tz != server_tz:
            problems.append(f"the bridge converts with server_tz {info.server_tz}, this brain uses {server_tz}")
    differs = bool(brain_commit and info.commit and brain_commit != info.commit)
    commit_note = (f"; commits differ (bridge {_short(info.commit)}, brain {_short(brain_commit)})" if differs
                   else "" if info.commit and brain_commit else
                   f"; commit not compared (bridge {_short(info.commit)}, brain {_short(brain_commit)})")
    if problems:
        return BridgeCompat(status="incompatible", missing=missing, bridge_api_version=info.api_version,
                            bridge_commit=info.commit, brain_commit=brain_commit, commit_differs=differs,
                            reason="; ".join(problems) + commit_note, checked_at=at)
    return BridgeCompat(status="ok", bridge_api_version=info.api_version, bridge_commit=info.commit,
                        brain_commit=brain_commit, commit_differs=differs, checked_at=at,
                        reason=f"bridge protocol v{info.api_version} serves every method this brain calls, both use "
                               f"server_tz {server_tz}{commit_note}")


def check_bridge(url: str, server_tz: str, brain_commit: str | None, timeout_s: float = CHECK_TIMEOUT_S,
                 get: Callable[..., Any] = requests.get) -> BridgeCompat:
    return assess(read_bridge_info(url, timeout_s, get), server_tz, brain_commit)


def next_verdict(prev: BridgeCompat | None, new: BridgeCompat) -> BridgeCompat:
    """The verdict the engine applies. An unreachable /health never confirms a bridge: with no earlier reachable verdict
    (none, or unchecked) it stays `unchecked` (blocking); after an `incompatible` that verdict is kept; after an `ok`
    the unreachable verdict applies (no block by itself: stale data blocks) and keeps the ok's `checked_at`, so it
    ages into `unchecked` after MAX_VERDICT_AGE_S (`effective`). Any reachable verdict replaces the previous one."""
    if new.status != "unreachable":
        return new
    if prev is None or prev.status == "unchecked":
        return unchecked(new.reason.split(":")[0], prev.checked_at if prev is not None else None,
                         since=prev.unchecked_since if prev is not None else None)
    if prev.blocks:
        return prev.model_copy(update={"reason": f"{prev.reason} (last verdict kept: {new.reason.split(':')[0]})"})
    return new.model_copy(update={"checked_at": prev.checked_at})


def effective(v: BridgeCompat, now: pd.Timestamp, max_age_s: float = MAX_VERDICT_AGE_S) -> BridgeCompat:
    """`v`, or `unchecked` when it does not block and the bridge was last confirmed over `max_age_s` ago (or never):
    a hung check thread or a long silence must not leave an old `ok` in force. A `checked_at` ahead of `now` (a
    clock step, a restored state file) is unchecked too: its age would be negative for ever (found 2026-10-16)."""
    if v.blocks:
        return v
    if v.checked_at is None:
        return unchecked("never confirmed", now=now)
    age = (now - v.checked_at).total_seconds()
    if age < -CLOCK_AHEAD_TOLERANCE_S:
        ahead = f"{-age:.0f} s" if -age < 120 else f"{-age / 60:.0f} min"
        return unchecked(f"last confirmation stamped {ahead} ahead of this clock", v.checked_at, now=now)
    if age > max_age_s:
        return unchecked(f"last confirmed {age / 60:.0f} min ago (over {max_age_s / 60:g} min)", v.checked_at, now=now)
    return v


class BridgeCompatChecker:
    """Runs `check()` on a background thread (never two at once) so the engine's tick loop never waits for /health:
    `start()` launches it, `collect()` returns the verdict once it finished, else None. A check that raised is an
    `unreachable` verdict naming the exception type only."""

    def __init__(self, check: Callable[[], BridgeCompat], *, clock: Callable[[], float] = time.monotonic,
                 hung_after_s: float = HUNG_AFTER_S) -> None:
        self.check, self.clock, self.hung_after_s = check, clock, hung_after_s
        self._thread: threading.Thread | None = None
        self._started = 0.0
        self._box: dict[str, BridgeCompat] = {}

    def start(self) -> bool:
        """Launch a check unless one runs. Self-healing: a check alive longer than `hung_after_s` is abandoned (a
        daemon thread; its late result goes to its own box and is never collected) and a new one starts, so a hung
        read cannot keep the engine `unchecked` for good. Called once per minute: at most one leaked thread a minute."""
        if self._thread is not None:
            if not self._thread.is_alive() or self.clock() - self._started <= self.hung_after_s:
                return False                         # finished (collect it) or still inside its time
            log.warning("bridge check alive %.0f s (> %.0f s): abandoned, starting a new one",
                        self.clock() - self._started, self.hung_after_s)
        box: dict[str, BridgeCompat] = {}

        def work() -> None:
            try:
                box["verdict"] = self.check()
            except Exception as exc:                 # the type only: a message can name the tunnel endpoint
                box["verdict"] = BridgeCompat(status="unreachable", reason=f"check failed ({type(exc).__name__})")
        self._box = box
        self._thread = threading.Thread(target=work, name="bridge-compat", daemon=True)
        self._started = self.clock()
        self._thread.start()
        return True

    def collect(self) -> BridgeCompat | None:
        if self._thread is None or self._thread.is_alive():
            return None
        self._thread = None
        return self._box.get("verdict")

    def run_once(self, timeout_s: float = CHECK_TIMEOUT_S + 1.0) -> BridgeCompat | None:
        """Blocking, for the engine's start (before the tick loop): waits at most `timeout_s`."""
        self.start()
        if self._thread is not None:
            self._thread.join(timeout_s)
        return self.collect()
