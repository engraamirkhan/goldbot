"""Is the MT5 bridge compatible with this brain? (BACKLOG 42, gap G6 in docs/deploy/2026-10-batch.md)

The two boxes are deployed separately, so the engine on the brain can run newer code than the bridge on the MT5 box:
a brain that sizes through `loss_per_lot` against a bridge without it refuses every entry, and a bridge that converts
MT5 stamps with another `server_tz` than the brain puts every session, rollover and Friday cut an hour off. The bridge
reports on its unauthenticated GET /health (goldbot/execution/bridge.py) its `server_tz`, protocol `api_version`, the
`methods` it serves and the `commit` it runs; this module reads that and judges it:

* `incompatible` (entries refused with the gate reason `bridge_incompatible`, health FAIL, preflight blocker): a method
  the brain calls is missing, the protocol version is older than the brain's, the zone differs, or the bridge does
  not report these at all (a bridge older than this check, or something else answering on the port).
* `unreachable`: /health did not answer (network, tunnel, 5xx). Not a block by itself: without the bridge there are no
  ticks, so the stale-data rule already blocks entries; the engine keeps its last verdict (`next_verdict`), health
  reports it (`bridge:<account>` fails).
* `ok`; a different commit on the two boxes is reported (health warning) but never blocks: doc-only commits differ.

Exits never read any of this: the verdict is one RiskGate input for entries only. The engine checks at start and every
60 s on a background thread (`BridgeCompatChecker`, goldbot/ops/run.py), so the tick loop never waits for /health.
Nothing here carries the bridge URL into a reason (it names the tunnel endpoint).
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable, Literal

import requests
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.config import ROOT
from goldbot.execution.bridge import API_VERSION, METHODS

log = logging.getLogger("goldbot.bridge_compat")

CompatStatus = Literal["ok", "incompatible", "unreachable"]
CHECK_TIMEOUT_S = 5.0                    # /health answers without touching the terminal
REQUIRED_METHODS = frozenset(METHODS)    # every method this brain's RemoteBroker calls
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
    return sha if sha is not None and _SHA.match(sha) else None


class BridgeInfo(FrozenRecord):
    """What the bridge's GET /health said. `reachable` False: no answer at all; `problem` set: it answered, but not
    with a bridge's report (older bridge without /health, junk)."""

    reachable: bool
    problem: str | None = None
    server_tz: str | None = None
    api_version: int | None = None
    methods: list[str] | None = None
    commit: str | None = None


def read_bridge_info(url: str, timeout_s: float = CHECK_TIMEOUT_S, get: Callable[..., Any] = requests.get) -> BridgeInfo:
    """GET <url>/health (no token, nothing reaches the terminal)."""
    try:
        r = get(f"{url.rstrip('/')}/health", timeout=timeout_s)
    except requests.RequestException as exc:
        return BridgeInfo(reachable=False, problem=f"/health unreachable ({type(exc).__name__})")
    if r.status_code >= 500:
        return BridgeInfo(reachable=False, problem=f"/health answered HTTP {r.status_code}")
    if r.status_code != 200:
        return BridgeInfo(reachable=True, problem=f"/health answered HTTP {r.status_code} (a bridge without /health)")
    try:
        body = r.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("ok") is not True:
        return BridgeInfo(reachable=True, problem="/health answered, but not with the bridge's report")
    tz, ver, methods, commit = body.get("server_tz"), body.get("api_version"), body.get("methods"), body.get("commit")
    return BridgeInfo(reachable=True, server_tz=tz if isinstance(tz, str) else None,
                      api_version=ver if isinstance(ver, int) and not isinstance(ver, bool) else None,
                      methods=[m for m in methods if isinstance(m, str)] if isinstance(methods, list) else None,
                      commit=commit if isinstance(commit, str) and _SHA.match(commit) else None)


def _short(sha: str | None) -> str:
    return sha[:12] if sha else "unknown"


class BridgeCompat(FrozenRecord):
    """The verdict on one bridge. `blocks`: entries are refused (`bridge_incompatible`)."""

    status: CompatStatus
    reason: str
    missing: list[str] = Field(default_factory=list)     # methods the brain calls that the bridge does not serve
    bridge_api_version: int | None = None
    bridge_commit: str | None = None
    brain_commit: str | None = None
    commit_differs: bool = False

    @property
    def blocks(self) -> bool:
        return self.status == "incompatible"


def assess(info: BridgeInfo, server_tz: str, brain_commit: str | None, *,
           required: frozenset[str] = REQUIRED_METHODS, api_version: int = API_VERSION) -> BridgeCompat:
    """The verdict for a /health report against this brain's methods, protocol version, zone and commit."""
    if not info.reachable:
        return BridgeCompat(status="unreachable", reason=f"{info.problem or '/health unreachable'}: compatibility not "
                            "checked (no ticks either: the stale-data rule blocks entries)", brain_commit=brain_commit)
    problems: list[str] = []
    missing: list[str] = []
    if info.problem is not None:
        problems.append(info.problem)
    elif info.methods is None or info.api_version is None:
        problems.append("the bridge does not report its protocol (older than BACKLOG 42): deploy this commit to the "
                        "MT5 box")
    else:
        missing = sorted(required - set(info.methods))
        if missing:
            problems.append(f"the bridge lacks {', '.join(missing)}")
        if info.api_version < api_version:
            problems.append(f"bridge protocol v{info.api_version} is older than this brain's v{api_version}")
    if info.problem is None:
        if info.server_tz is None:
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
                            reason="; ".join(problems) + commit_note)
    return BridgeCompat(status="ok", bridge_api_version=info.api_version, bridge_commit=info.commit,
                        brain_commit=brain_commit, commit_differs=differs,
                        reason=f"bridge protocol v{info.api_version} serves every method this brain calls, both use "
                               f"server_tz {server_tz}{commit_note}")


def check_bridge(url: str, server_tz: str, brain_commit: str | None, timeout_s: float = CHECK_TIMEOUT_S,
                 get: Callable[..., Any] = requests.get) -> BridgeCompat:
    return assess(read_bridge_info(url, timeout_s, get), server_tz, brain_commit)


def next_verdict(prev: BridgeCompat | None, new: BridgeCompat) -> BridgeCompat:
    """The verdict the engine applies: an unreachable /health keeps a previous `incompatible` (nothing shows the bridge
    changed; a restarted bridge answers on the next check), otherwise the new one."""
    if new.status == "unreachable" and prev is not None and prev.blocks:
        return prev.model_copy(update={"reason": f"{prev.reason} (last verdict kept: {new.reason.split(':')[0]})"})
    return new


class BridgeCompatChecker:
    """Runs `check()` on a background thread (never two at once) so the engine's tick loop never waits for /health:
    `start()` launches it, `collect()` returns the verdict once it finished, else None. A check that raised is an
    `unreachable` verdict naming the exception type only."""

    def __init__(self, check: Callable[[], BridgeCompat]) -> None:
        self.check = check
        self._thread: threading.Thread | None = None
        self._box: dict[str, BridgeCompat] = {}

    def start(self) -> bool:
        if self._thread is not None:
            return False
        box: dict[str, BridgeCompat] = {}

        def work() -> None:
            try:
                box["verdict"] = self.check()
            except Exception as exc:                 # the type only: a message can name the tunnel endpoint
                box["verdict"] = BridgeCompat(status="unreachable", reason=f"check failed ({type(exc).__name__})")
        self._box = box
        self._thread = threading.Thread(target=work, name="bridge-compat", daemon=True)
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
