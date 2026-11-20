"""The MT5 bridge's own files: a deploy restarts the bridges only when one of these changed (audit 2026-11-20, SRE
finding 2: every deploy restarted all three bridges in market hours, and each restart blocks entries until a decision
bar closes clean). ADR 0094.

`BRIDGE_FILES_RE` is the one definition; goldbot/ops/linux/goldbot-deploy.sh carries the same string (root never runs
repo code, so it cannot import this) and tests/test_bridge_files.py pins the two equal. A file belongs here when code
in it runs inside the bridge process (`python -m goldbot.ops.run bridge <account>`, under the Wine Python) or its
order-lane child, or when the bridge reads it at start (config/settings.yaml's mt5_login, accounts.yaml,
instruments.yaml), or when it pins the bridge's packages. The test walks every goldbot import of every file in the set
(top level and inside functions, TYPE_CHECKING blocks aside) and fails on one that is neither in the set nor named
below with the reason it is not on the bridge's path, so a new dependency of the bridge cannot slip past unseen.

Why a stale, un-restarted bridge is safe for a change outside the set: the bridge imports its whole module graph at
start (goldbot/ops/run.py imports every service), keeps it in memory, reads its config at start only, and its order
lane is started once by it; nothing outside the set runs in it. What the set cannot see, a change that would make the
bridge refuse to START (accounts.yaml validation through research/arms.py, risk/pilot.py, ...), is covered by
`start_check`, which goldbot-deploy runs with the target's code before it keeps a checkout-only deploy.

The brain's `bridge_compat:<account>` uses `differs_outside_bridge` so a bridge left on an older commit whose bridge
files equal the brain's is ok, not a standing "commits differ" warning.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

BRIDGE_FILES_RE = (
    r"^(goldbot/__init__\.py"
    r"|goldbot/(config|base|instruments)\.py"
    r"|goldbot/execution/[a-z0-9_]+\.py"
    r"|goldbot/ops/(__init__|run|accounts|login|login_ledger|observe|bridge_compat)\.py"
    r"|goldbot/ops/services/(__init__|_base|bridge|engine)\.py"
    r"|goldbot/ops/commands/(__init__|registry|services)\.py"
    r"|goldbot/data/(__init__|timeutil|calendar|cme_holidays)\.py"
    r"|goldbot/engine/(__init__|magic)\.py"
    r"|goldbot/risk/(__init__|sizing)\.py"
    r"|goldbot/labels/(__init__|triple_barrier|exit_policy)\.py"
    r"|config/(settings|accounts|instruments)\.yaml"
    r"|goldbot/ops/linux/mt5-bridge-requirements\.txt)$"
)
_RE = re.compile(BRIDGE_FILES_RE)

# Files in the set whose goldbot imports are NOT followed: the code of theirs that runs in the bridge uses only set
# members; the rest of what they import is loaded at start and never called by the bridge.
NOT_FOLLOWED: dict[str, str] = {
    "goldbot/ops/run.py": "dispatch: imports every service; only `bridge` and `bridge-order-lane` run here",
    "goldbot/ops/commands/__init__.py": "registers every command; only the two bridge commands run here",
    "goldbot/ops/commands/services.py": "the service commands; only `bridge` and `bridge-order-lane` run here",
    "goldbot/ops/services/engine.py": "only account_magic_bases / account_block_symbols / account_managed_bases / "
                                      "account_instruments run on the bridge (goldbot.instruments, goldbot.ops.accounts)",
    "goldbot/ops/observe.py": "only observe_bridge, select_symbols and trading_bridge_refusal run on the bridge",
    "goldbot/ops/accounts.py": "accounts.yaml validation (research/arms.py, risk/pilot.py, risk/limits_profile.py) "
                               "decides only whether the bridge starts: start_check runs it with the target's code",
    "goldbot/execution/not_confirmed.py": "engine-side; in the set only because execution/ is taken whole",
    "goldbot/engine/__init__.py": "package init re-exporting the engine; the bridge runs goldbot.engine.magic only",
    "goldbot/risk/__init__.py": "package init re-exporting the RiskGate; the bridge runs goldbot.risk.sizing only",
}


def is_bridge_file(path: str) -> bool:
    """True when a repo-relative path (git's `diff --name-only` form) is one of the bridge's files."""
    return bool(_RE.fullmatch(path.strip()))


def bridge_changes(names: list[str] | tuple[str, ...]) -> list[str]:
    """The bridge files among changed paths, in order."""
    return [n for n in names if n.strip() and is_bridge_file(n)]


def differs_outside_bridge(root: Path, a: str, b: str, *, timeout_s: float = 10.0) -> bool | None:
    """True when commits `a` and `b` differ only in files outside the bridge's set (the bridge on `a` serves exactly
    what one on `b` would); False when a bridge file differs; None when git cannot tell (unknown commit, no git)."""
    if not (re.fullmatch(r"[0-9a-f]{40}", a or "") and re.fullmatch(r"[0-9a-f]{40}", b or "")):
        return None
    key = (str(root), a, b)
    if key in _DIFF_CACHE:
        return _DIFF_CACHE[key]
    try:
        r = subprocess.run(["git", "-C", str(root), "diff", "--name-only", a, b], capture_output=True, text=True,
                           timeout=timeout_s, env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"})
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    out = not bridge_changes(r.stdout.splitlines())
    _DIFF_CACHE[key] = out
    return out


_DIFF_CACHE: dict[tuple[str, str, str], bool] = {}


def start_check(account_ids: list[str]) -> list[str]:
    """What would stop a bridge of this checkout from starting, before any terminal is touched: the whole module graph
    imports (as `python -m goldbot.ops.run` does), settings and accounts load and validate, and each account's symbol
    list and magic blocks resolve (run_bridge's steps up to the attach). Logins are not read (no keyring). Empty: ok."""
    problems: list[str] = []
    try:
        import goldbot.ops.run  # noqa: F401 - the bridge's own import of everything
        from goldbot.config import load_settings
        from goldbot.execution.market_watch import account_symbols
        from goldbot.ops import accounts
        from goldbot.ops.services.engine import (
            account_block_symbols,
            account_magic_bases,
            account_managed_bases,
        )
        load_settings()
        accs = accounts.load_accounts(logins=False)
    except Exception as exc:  # noqa: BLE001 - any failure here is a bridge that would not start
        return [f"load: {type(exc).__name__}: {str(exc)[:300]}"]
    for aid in account_ids:
        acc = accs.get(aid)
        if acc is None:
            problems.append(f"{aid}: not in config/accounts.yaml")
            continue
        try:
            account_symbols(acc)
            account_magic_bases(acc)
            account_block_symbols(acc)
            account_managed_bases(acc)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{aid}: {type(exc).__name__}: {str(exc)[:300]}")
    return problems


def main(argv: list[str]) -> int:
    """`python -m goldbot.ops.bridge_files check <account> ...`: prints `ok` (exit 0) or one problem per line (exit 3;
    exit 1 stays Python's own failure, which goldbot-deploy reads as "could not check" and deploys in full).
    `python -m goldbot.ops.bridge_files changed < names`: the bridge files among the paths on stdin."""
    if argv[:1] == ["check"] and len(argv) > 1 and all(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", a) for a in argv[1:]):
        problems = start_check(argv[1:])
        print("\n".join(problems) if problems else "ok")
        return 3 if problems else 0
    if argv == ["changed"]:
        print("\n".join(bridge_changes(sys.stdin.read().splitlines())))
        return 0
    print(main.__doc__)
    return 2


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(main(sys.argv[1:]))
