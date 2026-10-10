"""One trial registry across every place trials are run (design: "registry as sole source of N").

The research workflow keeps registry.jsonl on release `research-v1`; the VPS monthly loop keeps
state/research_registry.jsonl. Both merge with the release copy before and after writing, so the deflated
Sharpe's trial count includes every trial exactly once. Merging is a union keyed on what identifies a trial
(timestamp, agent, config hash, feature version), renumbered in time order, so it is idempotent and order-free; a
result row's `preregistration.trial` link is rewritten to its pre-registration's new number.

  python -m goldbot.research.registry_sync merge <a.jsonl> <b.jsonl> [-o out.jsonl]
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from goldbot.research.registry import is_trial

REPO = "engraamirkhan/goldbot"
TAG = "research-v1"
ASSET = "registry.jsonl"


def _key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (str(row.get("ts")), str(row.get("agent_id")), str(row.get("config_hash")), str(row.get("feature_version")))


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def merge_rows(*sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for rows in sources:
        for r in rows:
            seen.setdefault(_key(r), r)
    merged = sorted(seen.values(), key=lambda r: (str(r.get("ts")), str(r.get("agent_id"))))
    out, n = [], 0
    for r in merged:              # a pre-registration is not a trial: it carries the number its result row will take
        if is_trial(r):
            n += 1
            out.append({**r, "trial": n})
        else:
            out.append({**r, "trial": n + 1})
    number = {(str(r.get("ts")), str(r.get("config_hash"))): r["trial"] for r in out if not is_trial(r)}
    for i, r in enumerate(out):   # the result row's link follows its pre-registration's new number
        link = r.get("preregistration")
        if isinstance(link, dict):
            new = number.get((str(link.get("ts")), str(link.get("config_hash"))))
            if new is not None and new != link.get("trial"):
                out[i] = {**r, "preregistration": {**link, "trial": new}}
    return out


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
    tmp.replace(path)


def merge_files(out: Path, *inputs: Path) -> int:
    rows = merge_rows(*(read_rows(p) for p in inputs))
    write_rows(out, rows)
    return sum(1 for r in rows if is_trial(r))


# ------------------------------------------------------------------ release copy (VPS side)
def _api(url: str, token: str, *, method: str = "GET", data: bytes | None = None, ctype: str | None = None) -> Any:
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    if ctype:
        hdr["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, headers=hdr, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
    return json.loads(body) if body else None


def pull_release(token: str, repo: str = REPO) -> list[dict[str, Any]]:
    try:
        rel = _api(f"https://api.github.com/repos/{repo}/releases/tags/{TAG}", token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []          # no release yet: nothing recorded remotely
        raise
    for a in rel["assets"]:
        if a["name"] == ASSET:
            req = urllib.request.Request(a["url"], headers={"Authorization": f"Bearer {token}", "Accept": "application/octet-stream"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return [json.loads(line) for line in r.read().decode().splitlines() if line.strip()]
    return []


def push_release(token: str, rows: list[dict[str, Any]], repo: str = REPO) -> None:
    try:
        rel = _api(f"https://api.github.com/repos/{repo}/releases/tags/{TAG}", token)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        rel = _api(f"https://api.github.com/repos/{repo}/releases", token, method="POST", ctype="application/json",
                   data=json.dumps({"tag_name": TAG, "name": "Research trial registry", "make_latest": "false",
                                    "body": "registry.jsonl: every evaluated trial (feeds the deflated Sharpe)."}).encode())
    for a in rel["assets"]:
        if a["name"] == ASSET:
            _api(f"https://api.github.com/repos/{repo}/releases/assets/{a['id']}", token, method="DELETE")
    upload = rel["upload_url"].split("{")[0] + f"?name={ASSET}"
    data = "".join(json.dumps(r, default=str) + "\n" for r in rows).encode()
    _api(upload, token, method="POST", data=data, ctype="application/x-ndjson")


def sync(local: Path, token: str, repo: str = REPO) -> int:
    """Pull the release copy, union it with the local file, write both back. Returns the merged trial count."""
    rows = merge_rows(read_rows(local), pull_release(token, repo))
    write_rows(local, rows)
    push_release(token, rows, repo)
    return sum(1 for r in rows if is_trial(r))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge", help="union registry files into one, renumbered")
    m.add_argument("inputs", nargs="+", type=Path)
    m.add_argument("-o", "--out", type=Path, default=None)
    args = ap.parse_args()
    out = args.out or args.inputs[0]
    print(f"{merge_files(out, *args.inputs)} trials -> {out}")


if __name__ == "__main__":
    main()
