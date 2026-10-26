"""GitHub issues for the brain's automatic reports (the daily learning-ops review, goldbot/research/daily_review.py):
comment on a numbered issue, or comment on the newest OPEN issue carrying a label and open one when there is none.

Rules, as built here:
* The token is the keyring's `github-token` (`python -m goldbot.ops.accounts set github-token`), passed in by the
  caller; it is sent only in the Authorization header to api.github.com, never logged and never written to a file.
* One request at a time with a timeout; any HTTP or network error raises (`GitHubError`): the caller keeps the body in
  state and carries on. Rate limits are the caller's: the review posts at most once per kind per day.
* Bodies are capped at MAX_BODY characters (GitHub refuses above 65,536).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

API = "https://api.github.com"
TIMEOUT_S = 20.0
MAX_BODY = 60_000


class GitHubError(RuntimeError):
    """A request to the GitHub API failed (status, network or a malformed answer)."""


class IssueSink(Protocol):
    """Where the review's issue text goes; `GitHubIssues` on the brain, a recorder in tests."""

    def comment(self, number: int, body: str) -> str: ...

    def open_or_comment(self, label: str, title: str, body: str) -> str: ...


class GitHubIssues:
    def __init__(self, token: str, repo: str, *, timeout_s: float = TIMEOUT_S) -> None:
        if not token:
            raise ValueError("a GitHub token is required")
        self._token = token
        self.repo = repo
        self.timeout_s = timeout_s

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{API}/repos/{self.repo}{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:   # noqa: S310 - fixed https host
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as exc:
            raise GitHubError(f"GitHub {method} {path}: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise GitHubError(f"GitHub {method} {path}: {type(exc).__name__}") from None

    def comment(self, number: int, body: str) -> str:
        got = self._call("POST", f"/issues/{int(number)}/comments", {"body": body[:MAX_BODY]})
        return str(got.get("html_url", "")) if isinstance(got, dict) else ""

    def open_or_comment(self, label: str, title: str, body: str) -> str:
        """Comment on the newest open issue labelled `label` (pull requests excluded), else open one with it."""
        q = urllib.parse.urlencode({"labels": label, "state": "open", "sort": "created", "direction": "desc",
                                    "per_page": 10})
        found = self._call("GET", f"/issues?{q}")
        issues = [i for i in found if isinstance(i, dict) and "pull_request" not in i] if isinstance(found, list) else []
        if issues:
            return self.comment(int(issues[0]["number"]), f"**{title}**\n\n{body}")
        got = self._call("POST", "/issues", {"title": title[:250], "body": body[:MAX_BODY], "labels": [label]})
        return str(got.get("html_url", "")) if isinstance(got, dict) else ""
