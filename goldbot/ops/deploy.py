"""Deployment by the owner's click (or by hand), never by itself.

* The Telegram service calls `DeployWatch.check()` every 10 minutes: when `main` has a commit this server does not run,
  that is a fast-forward and passed CI on that exact commit (GitHub check-runs, public API), it is written to
  state/deploy/pending.json and offered to the owner with [Deploy] [Skip] buttons. The check never writes the repo
  (the unit's ProtectSystem=strict leaves /opt/goldbot/.git read-only): `git ls-remote` for main, GitHub's compare
  API for the fast-forward and the commit subjects.
* The owner's tap (allow-listed Telegram id) writes state/deploy/approved.json for that exact commit (`approve`).
  A tap can only deploy code that is already merged and passed CI, so it cannot inject anything.
* The root-owned `goldbot-deploy` script (goldbot/ops/linux/goldbot-deploy.sh, installed outside the repo) applies an
  approval, or a commit the owner names on the server (`sudo goldbot-deploy latest`): it re-checks main and CI itself,
  restarts the services, verifies them, rolls back on failure and appends the result to state/deploys.jsonl, which the
  Telegram service reports (`new_results`).
* The MT5 box (terminal + bridge, rarely changed) is updated by hand: `sudo goldbot-deploy latest` there. Following a
  GitHub Deployment was rejected in review: anyone with Deployments write could create one, which is not the owner's
  click.
* An approval file can only be written by the Telegram service after an allow-listed tap; there is deliberately no CLI
  approve (on the server the owner runs `sudo goldbot-deploy` instead).

CLI (as the service user, `goldbot deploy ...` on the brain):
  python -m goldbot.ops.deploy status | check | skip <sha>
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import requests

REPO_API = "https://api.github.com/repos/engraamirkhan/goldbot"
REQUIRED_CHECKS = ("backend", "frontend")       # CI jobs that must conclude "success" on the commit
ACTIONS_APP_ID = 15368                           # GitHub Actions: check-runs from any other app are ignored
SHA = re.compile(r"^[0-9a-f]{40}$")
FetchJson = Callable[[str], Any]
log = logging.getLogger(__name__)


def _get_json(url: str) -> Any:
    r = requests.get(url, headers={"Accept": "application/vnd.github+json"}, timeout=15)
    r.raise_for_status()
    return r.json()


def ci_status(sha: str, fetch: FetchJson = _get_json) -> str:
    """'yes' when every required check passed on `sha`, 'wait' while one is missing or running, 'no' otherwise."""
    runs = {r["name"]: r for r in fetch(f"{REPO_API}/commits/{sha}/check-runs?per_page=100&filter=latest&app_id={ACTIONS_APP_ID}").get("check_runs", [])}
    if all(runs.get(n, {}).get("conclusion") == "success" for n in REQUIRED_CHECKS):
        return "yes"
    if any(runs.get(n, {}).get("status") != "completed" for n in REQUIRED_CHECKS):
        return "wait"
    return "no"


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


class DeployWatch:
    def __init__(self, state_dir: str | Path, repo_dir: str | Path, fetch: FetchJson = _get_json,
                 git: Callable[..., str] | None = None) -> None:
        self.dir = Path(state_dir) / "deploy"
        self.log = Path(state_dir) / "deploys.jsonl"
        self.repo = Path(repo_dir)
        self.fetch = fetch
        self.git = git or self._git
        self._warned: str | None = None                  # the reason last warned about (one warning per outage)

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    def _read(self, name: str) -> dict[str, Any]:
        p = self.dir / name
        try:
            return dict(json.loads(p.read_text())) if p.exists() else {}
        except (ValueError, OSError):
            return {}

    def skipped(self) -> set[str]:
        return set(self._read("skipped.json").get("shas", []))

    def check(self) -> dict[str, Any] | None:
        """A new deployable commit to offer, or None. Offered once per commit.

        Read-only on the repo: the service runs with ProtectSystem=strict and /opt/goldbot/.git is not writable, so
        origin's main is read with `git ls-remote` (never `fetch`), and the fast-forward and the commit subjects come
        from GitHub's compare API (the new commit is not in the local object store). Without an answer nothing is
        offered; a failure logs one concise warning per outage, never a traceback each pass."""
        try:
            new = self._remote_main()
            cur = self.git("rev-parse", "HEAD")
            if cur == new or new in self.skipped() or self._read("pending.json").get("sha") == new:
                self._ok()
                return None
            cmp = self._compare(cur, new)
            if cmp.get("status") != "ahead":
                self._ok()
                return None                               # not a fast-forward: the server has local commits
            if ci_status(new, self.fetch) != "yes":
                self._ok()
                return None
        except (subprocess.CalledProcessError, OSError, requests.RequestException, ValueError, KeyError,
                TypeError, AttributeError) as e:
            self._warn(e)
            return None
        self._ok()
        # GitHub lists the commits oldest first (up to 250); the offer shows the newest 15 and the full count
        subjects = [str(c["commit"]["message"]).split("\n", 1)[0] for c in reversed(cmp.get("commits", []))]
        pending = {"sha": new, "from": cur, "subjects": subjects[:15],
                   "n_commits": int(cmp.get("ahead_by", len(subjects))), "mt5": self.mt5_notes(new)}
        _write(self.dir / "pending.json", pending)
        return pending

    def _remote_main(self) -> str:
        """origin's main from `git ls-remote` (reads the remote, writes nothing under .git)."""
        out = self.git("ls-remote", "origin", "refs/heads/main").split()
        if len(out) < 2 or out[1] != "refs/heads/main" or not SHA.fullmatch(out[0]):
            raise ValueError("git ls-remote origin refs/heads/main: no main on origin")
        return out[0]

    def _compare(self, base: str, head: str) -> dict[str, Any]:
        """GitHub's compare of two commits: status 'ahead' means `base` is an ancestor of `head` (a fast-forward)."""
        if not (SHA.fullmatch(base) and SHA.fullmatch(head)):
            raise ValueError("compare: not a commit id")
        return dict(self.fetch(f"{REPO_API}/compare/{base}...{head}"))

    def _warn(self, e: BaseException) -> None:
        """One line per outage (the first, or when the reason changes); a recovery re-arms it."""
        if isinstance(e, subprocess.CalledProcessError):
            cmd = e.cmd if isinstance(e.cmd, str) else " ".join(str(c) for c in e.cmd)
            first = (str(e.stderr or "").strip().splitlines() or [""])[0]
            reason = f"`{cmd}` exited {e.returncode}: {first}".rstrip(": ")
        else:
            reason = f"{type(e).__name__}: {(str(e).splitlines() or [''])[0]}"
        if reason != self._warned:
            log.warning("deploy check: cannot read the newest version (%s); no offer this pass", reason[:300])
            self._warned = reason

    def _ok(self) -> None:
        if self._warned is not None:
            log.info("deploy check: reading the newest version works again")
            self._warned = None

    def mt5_notes(self, new: str) -> list[str]:
        """One line per bridged engine whose MT5 box does not run `new` (G6, BACKLOG 42): the one-click deploy updates
        only the brain. Read from the bridge verdict each engine publishes in engine_<account>.json; a commit is
        handed to the VCS only after it matched the SHA pattern. Read-only; never raises."""
        # lazily: this module has no goldbot import at load
        from goldbot.ops.accounts import engine_state_files
        notes: list[str] = []
        for f in engine_state_files(self.log.parent):     # a disabled account's MT5 box needs no update
            account = f.stem[len("engine_"):]
            try:
                compat = json.loads(f.read_text()).get("bridge_compat")
            except (ValueError, OSError, AttributeError):
                continue
            if not isinstance(compat, dict):
                continue                                  # no bridge (paper, local terminal) or not checked
            commit = compat.get("bridge_commit")
            if commit is None:
                if compat.get("status") in ("incompatible", "ok"):
                    notes.append(f"MT5 box ({account}) does not report its commit (older than BACKLOG 42): deploy "
                                 f"the MT5 box first (`sudo goldbot-deploy {new[:8]}` there)")
                continue
            if not isinstance(commit, str) or not SHA.fullmatch(commit):     # never echoed, never handed to the VCS
                notes.append(f"MT5 box ({account}) reports an unrecognised commit: check it before deploying")
                continue
            if commit == new:
                continue
            try:
                ancestor = self._compare(commit, new).get("status") == "ahead"
            except (OSError, requests.RequestException, ValueError, TypeError, AttributeError):
                notes.append(f"MT5 box ({account}) runs {commit[:8]}; GitHub did not say whether it is older than "
                             "this version: check it before deploying")
                continue
            if ancestor:
                notes.append(f"MT5 box ({account}) runs {commit[:8]}, older than this version: deploy the MT5 box "
                             f"first if this release changes the bridge (`sudo goldbot-deploy {new[:8]}` there)")
            else:
                notes.append(f"MT5 box ({account}) runs {commit[:8]}, not an ancestor of this version: check it "
                             "before deploying")
        return notes

    def approve(self, sha: str) -> bool:
        """The owner approved `sha` (must be the commit on offer). The root deploy script applies it and reads only
        the sha; who tapped is in the audit log, never in this file (A15)."""
        if not SHA.match(sha) or self._read("pending.json").get("sha") != sha:
            return False
        _write(self.dir / "approved.json", {"sha": sha})
        (self.dir / "pending.json").unlink(missing_ok=True)
        return True

    def skip(self, sha: str) -> bool:
        if self._read("pending.json").get("sha") != sha:
            return False
        shas = sorted(self.skipped() | {sha})[-50:]
        _write(self.dir / "skipped.json", {"shas": shas})
        (self.dir / "pending.json").unlink(missing_ok=True)
        return True

    def new_results(self) -> list[dict[str, Any]]:
        """Deploy results appended since the last call (for Telegram)."""
        if not self.log.exists():
            return []
        lines = self.log.read_text().splitlines()
        seen = int(self._read("reported.json").get("lines", 0))
        _write(self.dir / "reported.json", {"lines": len(lines)})
        out = []
        for line in lines[seen:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


def main(argv: list[str]) -> int:  # pragma: no cover - thin CLI
    from goldbot.config import ROOT
    w = DeployWatch("state", ROOT)
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        print(json.dumps({"pending": w._read("pending.json"), "approved": w._read("approved.json")}, indent=2))
        return 0
    if cmd == "check":
        print(json.dumps(w.check()))
        return 0
    if cmd == "skip" and len(argv) > 1:
        ok = w.skip(argv[1])
        print("ok" if ok else "not the commit on offer")
        return 0 if ok else 1
    print(__doc__)
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
