"""One trial registry across every place trials are run (design: "registry as sole source of N").

The research workflow keeps registry.jsonl on release `research-v1`; the VPS monthly loop keeps
state/research_registry.jsonl. Both merge with the release copy before and after writing, so the deflated
Sharpe's trial count includes every trial exactly once. Merging is a union keyed on what identifies a trial
(timestamp, agent, config hash, feature version), renumbered in time order, so it is idempotent and order-free; a
result row's `preregistration.trial` link is rewritten to its pre-registration's new number.

  python -m goldbot.research.registry_sync merge <a.jsonl> <b.jsonl> [-o out.jsonl]
  gh api repos/{owner}/{repo}/releases/tags/research-v1 | python -m goldbot.research.registry_sync stamp [--own N]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from goldbot.research.registry import is_trial

REPO = "engraamirkhan/goldbot"
TAG = "research-v1"
ASSET = "registry.jsonl"
log = logging.getLogger(__name__)


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
# The workflows serialise their writes through `concurrency: research-registry`; the VPS monthly loop writes outside
# that group (BACKLOG 51), so every write here is a compare-and-swap on the asset GitHub serves:
#   1. pull: read the release, note the registry asset's stamp (id, updated_at, size, digest), download it;
#   2. merge with the local rows and refuse a union shorter than the published copy;
#   3. upload the merged rows under a pending name (the slow step), so the published copy is never missing while
#      bytes are in flight;
#   4. re-read the release just before the swap: a stamp other than the one pulled means another writer landed;
#   5. delete the pulled asset by its id (a 404 means someone replaced it meanwhile) and rename the pending asset to
#      registry.jsonl (a 422 means someone uploaded one meanwhile).
# Losing the race at 4 or 5 deletes the pending asset and raises RegistryRace; `sync` then pulls, merges and tries
# again a bounded number of times, and fails loudly if the asset keeps changing. The local file keeps every union it
# saw, so a lost race never drops a row, and the keyed union never counts a trial twice.
# scripts/registry_release.sh save runs the same protocol for the workflows.
API = "https://api.github.com"
PENDING = f"{ASSET}.pending-"     # an upload in flight; never read as the registry
ATTEMPTS = 3

Stamp = tuple[int, str, int, str] | None     # (id, updated_at, size, digest) of registry.jsonl; None when absent


class RegistryRace(RuntimeError):
    """The published registry changed between the pull and the push (another writer landed)."""


def _api(url: str, token: str, *, method: str = "GET", data: bytes | None = None, ctype: str | None = None) -> Any:
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    if ctype:
        hdr["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, headers=hdr, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
    return json.loads(body) if body else None


def _download(url: str, token: str) -> bytes:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data: bytes = r.read()
    return data


def _release(token: str, repo: str) -> dict[str, Any] | None:
    try:
        rel: dict[str, Any] = _api(f"{API}/repos/{repo}/releases/tags/{TAG}", token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None        # no release yet: nothing recorded remotely
        raise
    return rel


def _asset(rel: dict[str, Any] | None, own: str | None = None) -> dict[str, Any] | None:
    """The published registry asset. With none published but a pending upload present (other than `own`, the
    caller's), another writer is between its delete and its rename, or crashed there: starting from an empty
    registry then would drop every row."""
    if rel is None:
        return None
    assets = rel.get("assets", [])
    for a in assets:
        if a["name"] == ASSET:
            return dict(a)
    if any(str(a["name"]).startswith(PENDING) and a["name"] != own for a in assets):
        raise RegistryRace(f"{TAG} has a pending {ASSET} upload but no published one: a write is in flight")
    return None


def _stamp(asset: dict[str, Any] | None) -> Stamp:
    if asset is None:
        return None
    return (int(asset["id"]), str(asset.get("updated_at")), int(asset.get("size") or 0), str(asset.get("digest")))


def pull_stamped(token: str, repo: str = REPO) -> tuple[list[dict[str, Any]], Stamp]:
    """The published rows and the stamp of the asset they came from (step 1)."""
    asset = _asset(_release(token, repo))
    if asset is None:
        return [], None
    raw = _download(asset["url"], token)
    return [json.loads(line) for line in raw.decode().splitlines() if line.strip()], _stamp(asset)


def pull_release(token: str, repo: str = REPO) -> list[dict[str, Any]]:
    return pull_stamped(token, repo)[0]


def _create_release(token: str, repo: str) -> dict[str, Any]:
    rel: dict[str, Any] = _api(
        f"{API}/repos/{repo}/releases", token, method="POST", ctype="application/json",
        data=json.dumps({"tag_name": TAG, "name": "Research trial registry", "make_latest": "false",
                         "body": "registry.jsonl: every evaluated trial (feeds the deflated Sharpe)."}).encode())
    return rel


def _delete_asset(token: str, repo: str, asset_id: int) -> None:
    _api(f"{API}/repos/{repo}/releases/assets/{asset_id}", token, method="DELETE")


def push_release(token: str, rows: list[dict[str, Any]], repo: str = REPO, *, expected: Stamp = None) -> None:
    """Publish `rows` only while the registry asset still has the stamp `expected` (steps 3-5). Raises RegistryRace
    otherwise and leaves the published copy as the other writer left it."""
    rel = _release(token, repo)
    if rel is None:
        if expected is not None:
            raise RegistryRace(f"release {TAG} disappeared after the pull")
        rel = _create_release(token, repo)
    pending = f"{PENDING}{uuid.uuid4().hex[:12]}"
    upload = rel["upload_url"].split("{")[0] + f"?name={pending}"
    data = "".join(json.dumps(r, default=str) + "\n" for r in rows).encode()
    staged: dict[str, Any] = _api(upload, token, method="POST", data=data, ctype="application/x-ndjson")
    try:
        current = _asset(_release(token, repo), own=pending)
        if _stamp(current) != expected:
            raise RegistryRace(f"{TAG}/{ASSET} changed after the pull ({expected} -> {_stamp(current)})")
        if current is not None:
            try:
                _delete_asset(token, repo, int(current["id"]))
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise RegistryRace(f"{TAG}/{ASSET} was replaced just before the swap") from exc
                raise
        try:
            _api(f"{API}/repos/{repo}/releases/assets/{staged['id']}", token, method="PATCH",
                 ctype="application/json", data=json.dumps({"name": ASSET}).encode())
        except urllib.error.HTTPError as exc:
            if exc.code == 422:
                raise RegistryRace(f"another {TAG}/{ASSET} was uploaded during the swap") from exc
            raise
    except BaseException:
        try:                   # best effort: a stale pending asset is ignored while registry.jsonl exists
            _delete_asset(token, repo, int(staged["id"]))
        except Exception as cleanup:   # the original error is the one to report
            log.warning("could not delete the pending registry upload %s: %s", pending, cleanup)
        raise


def sync(local: Path, token: str, repo: str = REPO, *, attempts: int = ATTEMPTS) -> int:
    """Pull the release copy, union it with the local file, write both back. Returns the merged trial count.

    A writer landing between the pull and the push (RegistryRace) means pull, merge and push again, at most
    `attempts` times; the local file keeps every union it saw, so nothing is lost when the last attempt fails too."""
    rows = read_rows(local)
    for attempt in range(1, attempts + 1):
        remote, stamp = pull_stamped(token, repo)
        rows = merge_rows(rows, remote)
        if len(rows) < len(remote):
            raise ValueError(f"the merged registry has {len(rows)} rows, the published one {len(remote)}: "
                             "refusing to upload")
        write_rows(local, rows)
        try:
            push_release(token, rows, repo, expected=stamp)
        except RegistryRace as exc:
            log.warning("trial registry push lost a race (attempt %d/%d): %s", attempt, attempts, exc)
            continue
        return sum(1 for r in rows if is_trial(r))
    raise RegistryRace(f"{TAG}/{ASSET} changed during each of {attempts} pushes; the local registry keeps the union, "
                       "the published copy was left as the other writer wrote it")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge", help="union registry files into one, renumbered")
    m.add_argument("inputs", nargs="+", type=Path)
    m.add_argument("-o", "--out", type=Path, default=None)
    s = sub.add_parser("stamp", help="read a release's JSON on stdin; print the registry asset's stamp, tab-separated "
                                     "(id, updated_at, size, digest), or `none`; exit 1 when only a pending upload "
                                     "exists (used by scripts/registry_release.sh save)")
    s.add_argument("--own", default=None, help="the caller's pending asset name, ignored by the in-flight check")
    s.add_argument("--id-of", default=None, help="print only the id of the asset with this name")
    args = ap.parse_args()
    if args.cmd == "stamp":
        sys.exit(_stamp_cli(json.load(sys.stdin), own=args.own, id_of=args.id_of))
    out = args.out or args.inputs[0]
    print(f"{merge_files(out, *args.inputs)} trials -> {out}")


def _stamp_cli(rel: dict[str, Any], *, own: str | None, id_of: str | None) -> int:
    if id_of is not None:
        ids = [a["id"] for a in rel.get("assets", []) if a["name"] == id_of]
        if len(ids) != 1:
            print(f"{TAG} has {len(ids)} assets named {id_of}", file=sys.stderr)
            return 1
        print(ids[0])
        return 0
    try:
        stamp = _stamp(_asset(rel, own=own))
    except RegistryRace as exc:
        print(exc, file=sys.stderr)
        return 1
    print("none" if stamp is None else "\t".join(str(v) for v in stamp))
    return 0


if __name__ == "__main__":
    main()
