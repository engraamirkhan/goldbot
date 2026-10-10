"""Deployment by the owner's click (or by hand), never by itself.

* The Telegram service calls `DeployWatch.check()` every 10 minutes: when `main` has a commit this server does not run,
  that is a fast-forward and passed CI on that exact commit (GitHub check-runs, public API), it is written to
  state/deploy/pending.json and offered to the owner with [Deploy] [Skip] buttons.
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
        """A new deployable commit to offer, or None. Offered once per commit."""
        self.git("fetch", "-q", "origin", "main")
        cur, new = self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/main")
        if cur == new or new in self.skipped() or self._read("pending.json").get("sha") == new:
            return None
        try:
            self.git("merge-base", "--is-ancestor", cur, new)
        except subprocess.CalledProcessError:
            return None                                   # not a fast-forward: the server has local commits
        if ci_status(new, self.fetch) != "yes":
            return None
        subjects = self.git("log", "--format=%s", f"{cur}..{new}").splitlines()
        pending = {"sha": new, "from": cur, "subjects": subjects[:15], "n_commits": len(subjects)}
        _write(self.dir / "pending.json", pending)
        return pending

    def approve(self, sha: str, by: int) -> bool:
        """The owner approved `sha` (must be the commit on offer). The root deploy script applies it."""
        if not SHA.match(sha) or self._read("pending.json").get("sha") != sha:
            return False
        _write(self.dir / "approved.json", {"sha": sha, "by": by})
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
