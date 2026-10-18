"""One trial registry across every place trials are run (design: "registry as sole source of N").

The research workflow keeps registry.jsonl on release `research-v1`; the VPS monthly loop keeps
state/research_registry.jsonl. Both merge with the release copy before and after writing, so the deflated
Sharpe's trial count includes every trial exactly once. Merging is a union keyed on what identifies a trial
(timestamp, agent, config hash, feature version), renumbered in time order, so it is idempotent and order-free; a
result row's `preregistration.trial` link is rewritten to its pre-registration's new number. A `failed` row named by
a repair row's `repair.replaces` (scripts/registry_repair.py) is dropped from the union: the repair takes its place.

  python -m goldbot.research.registry_sync merge <a.jsonl> <b.jsonl> [-o out.jsonl]
  python -m goldbot.research.registry_sync fetch OUT [--bootstrap] | save LOCAL [--bootstrap] | recover   (via gh)
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Protocol

from goldbot.research.registry import is_trial

REPO = "engraamirkhan/goldbot"
TAG = "research-v1"
ASSET = "registry.jsonl"
log = logging.getLogger(__name__)


KEY_FIELDS = ("ts", "agent_id", "config_hash", "feature_version")    # what identifies a row in every union
FAILED = "failed"


def _key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (str(row.get("ts")), str(row.get("agent_id")), str(row.get("config_hash")), str(row.get("feature_version")))


def _superseded(rows: list[dict[str, Any]]) -> set[tuple[str, str, str, str]]:
    """Keys of the `failed` rows a repair row replaces (`repair.replaces`, scripts/registry_repair.py): the record step
    wrote a failed row for a cell whose real row it wrongly refused, and the repair puts that row in its place under the
    same timestamp, so the trial keeps its number and K is unchanged. A union drops the replaced row from every copy
    (a stale one, e.g. the VPS's, cannot bring it back); only a failed row can be replaced, never a real trial."""
    out: set[tuple[str, str, str, str]] = set()
    for r in rows:
        rep = r.get("repair")
        if isinstance(rep, dict) and isinstance(rep.get("replaces"), dict):
            out.add(_key(rep["replaces"]))
    return out


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def merge_rows(*sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for rows in sources:
        for r in rows:
            seen.setdefault(_key(r), r)
    for k in _superseded(list(seen.values())):
        if k in seen and seen[k].get("status") == FAILED and "repair" not in seen[k]:
            del seen[k]
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


# ------------------------------------------------------------------ release copy (research-v1/registry.jsonl)
# Writers: the workflows (research.yml, preregister-quarter.yml through scripts/registry_release.sh, serialised by
# `concurrency: research-registry`) and the VPS monthly loop (`sync`, outside that group; BACKLOG 51). One protocol,
# here, for both; only the transport differs (HttpTransport with the VPS token, GhTransport with the workflow's gh).
# Every write is a compare-and-swap on the asset GitHub serves:
#   1. pull: read the release, note the registry asset's stamp (id, updated_at, size, digest), download it;
#   2. merge with the local rows and refuse a union shorter than the published copy;
#   3. upload the merged rows under a pending name (the slow step), so the published copy is in place while bytes
#      are in flight;
#   4. re-read the release just before the swap: a stamp other than the one pulled means another writer landed;
#   5. delete the pulled asset by its id (a 404 means someone replaced it meanwhile);
#   6. rename the pending asset to registry.jsonl and re-read: published means exactly our id is the lowest-id asset
#      named registry.jsonl (the rename's status code is not relied on).
# Losing at 4-5 deletes the pending asset and raises RegistryRace; another writer's copy published at 6 does the same.
# `save_rows` then pulls, merges and tries again (jittered backoff), a bounded number of times, and fails loudly.
# Once the old asset is deleted the pending upload is never deleted unless another copy is published: if the rename
# keeps failing it stays, reads refuse a pending-only release (never "start empty", which would shrink the
# deflated-Sharpe N and restart the numbering), and `recover` unions every copy and publishes it the same way.
# The local file keeps every union it saw, so a lost race drops no row, and the keyed union counts a trial once.
API = "https://api.github.com"
PENDING = f"{ASSET}.pending-"     # an upload in flight; never read as the registry
ATTEMPTS = 3
RENAME_ATTEMPTS = 3
RELEASE_TITLE = "Research trial registry"
RELEASE_NOTES = ("registry.jsonl: every trial and pre-registration (feeds the deflated Sharpe). Written by the research "
                 "and preregister-quarter workflows and the VPS monthly loop (goldbot.research.registry_sync).")
RECOVER = "scripts/registry_release.sh recover"
GH_TIMEOUT_S = 120
_sleep = time.sleep

Stamp = tuple[int, str, int, str] | None     # (id, updated_at, size, digest) of registry.jsonl; None when absent


class RegistryRace(RuntimeError):
    """The published registry changed between the pull and the push (another writer landed), or another writer is
    between its delete and its rename. Retried by `save_rows`."""


class RegistryStranded(RuntimeError):
    """The old registry asset is deleted and the pending upload holding the merge could not be published. The pending
    asset is left in place; reads refuse until `recover` publishes it. Not retried."""


class RegistryMissing(RuntimeError):
    """The release exists but has no registry asset: never read as an empty registry without `bootstrap`."""


class Transport(Protocol):
    def release(self) -> dict[str, Any] | None: ...                          # None only when the release is absent
    def create_release(self) -> dict[str, Any]: ...
    def upload(self, rel: dict[str, Any], name: str, data: bytes) -> dict[str, Any]: ...   # the new asset
    def download(self, asset: dict[str, Any]) -> bytes: ...
    def delete(self, asset_id: int) -> bool: ...                             # False on a 404
    def rename(self, asset_id: int, name: str) -> None: ...


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


class HttpTransport:
    """The VPS side: the GitHub REST API with the keyring token."""

    def __init__(self, token: str, repo: str = REPO) -> None:
        self.token, self.base = token, f"{API}/repos/{repo}"

    def release(self) -> dict[str, Any] | None:
        try:
            rel: dict[str, Any] = _api(f"{self.base}/releases/tags/{TAG}", self.token)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        return rel

    def create_release(self) -> dict[str, Any]:
        rel: dict[str, Any] = _api(
            f"{self.base}/releases", self.token, method="POST", ctype="application/json",
            data=json.dumps({"tag_name": TAG, "name": RELEASE_TITLE, "make_latest": "false",
                             "body": RELEASE_NOTES}).encode())
        return rel

    def upload(self, rel: dict[str, Any], name: str, data: bytes) -> dict[str, Any]:
        asset: dict[str, Any] = _api(rel["upload_url"].split("{")[0] + f"?name={name}", self.token, method="POST",
                                     data=data, ctype="application/x-ndjson")
        return asset

    def download(self, asset: dict[str, Any]) -> bytes:
        return _download(asset["url"], self.token)

    def delete(self, asset_id: int) -> bool:
        try:
            _api(f"{self.base}/releases/assets/{asset_id}", self.token, method="DELETE")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise
        return True

    def rename(self, asset_id: int, name: str) -> None:
        _api(f"{self.base}/releases/assets/{asset_id}", self.token, method="PATCH", ctype="application/json",
             data=json.dumps({"name": name}).encode())


class GhError(RuntimeError):
    def __init__(self, args: list[str], stderr: str) -> None:
        m = re.search(r"HTTP (\d{3})", stderr)
        self.code = int(m.group(1)) if m else None
        super().__init__(f"gh {' '.join(args)} failed: {stderr.strip()}")


class GhTransport:
    """The workflow side: the `gh` CLI with the job's GH_TOKEN, on the checked-out repo."""

    def __init__(self, gh: str = "gh") -> None:
        self.gh, self.base = gh, "repos/{owner}/{repo}"

    def _bytes(self, *args: str) -> bytes:
        try:
            r = subprocess.run([self.gh, *args], capture_output=True, check=False, timeout=GH_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise GhError(list(args), f"timed out after {GH_TIMEOUT_S} s") from exc
        if r.returncode != 0:
            raise GhError(list(args), r.stderr.decode(errors="replace"))
        return r.stdout

    def _run(self, *args: str) -> str:
        return self._bytes(*args).decode()

    def release(self) -> dict[str, Any] | None:
        try:
            rel: dict[str, Any] = json.loads(self._run("api", f"{self.base}/releases/tags/{TAG}"))
        except GhError as exc:
            if exc.code == 404:
                return None
            raise
        return rel

    def create_release(self) -> dict[str, Any]:
        self._run("release", "create", TAG, "--title", RELEASE_TITLE, "--latest=false", "--notes", RELEASE_NOTES)
        rel = self.release()
        if rel is None:
            raise RuntimeError(f"release {TAG} not found right after creating it")
        return rel

    def upload(self, rel: dict[str, Any], name: str, data: bytes) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as d:          # the asset name is the file name
            (Path(d) / name).write_bytes(data)
            self._run("release", "upload", TAG, str(Path(d) / name))     # never --clobber
        now = self.release() or {}
        for a in now.get("assets", []):
            if a["name"] == name:
                return dict(a)
        raise RuntimeError(f"uploaded {name} but {TAG} does not list it")

    def download(self, asset: dict[str, Any]) -> bytes:   # by id: exactly the asset that was stamped
        return self._bytes("api", "-H", "Accept: application/octet-stream",
                           f"{self.base}/releases/assets/{int(asset['id'])}")

    def delete(self, asset_id: int) -> bool:
        try:
            self._run("api", "-X", "DELETE", f"{self.base}/releases/assets/{asset_id}")
        except GhError as exc:
            if exc.code == 404:
                return False
            raise
        return True

    def rename(self, asset_id: int, name: str) -> None:
        self._run("api", "-X", "PATCH", f"{self.base}/releases/assets/{asset_id}", "-f", f"name={name}")


def _published(rel: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Assets named registry.jsonl, lowest id first: should two ever coexist, every reader and writer picks the
    same one (the earliest upload)."""
    found = [dict(a) for a in (rel or {}).get("assets", []) if a["name"] == ASSET]
    return sorted(found, key=lambda a: int(a["id"]))


def _asset(rel: dict[str, Any] | None, own: str | None = None, *, strict: bool = True) -> dict[str, Any] | None:
    """The published registry asset. With none published but a pending upload present (other than `own`, the
    caller's), another writer is between its delete and its rename, or stranded there: starting from an empty
    registry then would drop every row (`strict`; `recover` reads that state on purpose)."""
    if rel is None:
        return None
    published = _published(rel)
    if published:
        return published[0]
    if strict and any(str(a["name"]).startswith(PENDING) and a["name"] != own for a in rel.get("assets", [])):
        raise RegistryRace(f"{TAG} has a pending {ASSET} upload but no published one: a write is in flight, or "
                           f"stranded (run `{RECOVER}` if this persists)")
    return None


def _stamp(asset: dict[str, Any] | None) -> Stamp:
    if asset is None:
        return None
    return (int(asset["id"]), str(asset.get("updated_at")), int(asset.get("size") or 0), str(asset.get("digest")))


def _parse(raw: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in raw.decode().splitlines() if line.strip()]


def _encode(rows: list[dict[str, Any]]) -> bytes:
    return "".join(json.dumps(r, default=str) + "\n" for r in rows).encode()


def _backoff(attempt: int) -> float:
    return random.uniform(0.5, 1.5) * attempt      # a mid-swap state lasts about a second


def pull_stamped(t: Transport, *, bootstrap: bool = False) -> tuple[list[dict[str, Any]], Stamp]:
    """The published rows and the stamp of the asset they came from (step 1). Empty only when the release does not
    exist, or with `bootstrap` when it exists without a registry asset."""
    rel = t.release()
    asset = _asset(rel)
    if asset is None:
        if rel is not None and not bootstrap:
            raise RegistryMissing(f"release {TAG} exists but has no {ASSET}: refusing to start from an empty registry "
                                  "(pass --bootstrap only for a deliberately new registry)")
        return [], None
    return _parse(t.download(asset)), _stamp(asset)


def _drop(t: Transport, asset_id: int, name: str) -> None:
    try:                       # best effort: a stale pending asset is ignored while registry.jsonl exists
        t.delete(asset_id)
    except Exception as cleanup:   # the original error is the one to report
        log.warning("could not delete the pending registry upload %s: %s", name, cleanup)


def _drop_named(t: Transport, name: str) -> None:
    try:
        ids = [int(a["id"]) for a in (t.release() or {}).get("assets", []) if a["name"] == name]
    except Exception as exc:
        log.warning("could not look up the pending registry upload %s: %s", name, exc)
        return
    for i in ids:
        _drop(t, i, name)


def _publish(t: Transport, staged_id: int, pending: str, deleted_id: int | None) -> None:
    """Step 6, after the old asset (`deleted_id`, None when there was none) is gone: rename, then require our id to
    be the published copy. Our pending asset is deleted only when a fresh read (one that lists our upload, and from
    which the deleted id is filtered) shows another writer's copy published; a stale read is re-read after a wait.
    Otherwise our upload is the only full copy and stays."""
    for attempt in range(1, RENAME_ATTEMPTS + 1):
        try:
            t.rename(staged_id, ASSET)
        except Exception as exc:     # the status code is not relied on: the re-read decides
            log.warning("renaming %s failed (attempt %d/%d): %s", pending, attempt, RENAME_ATTEMPTS, exc)
        rel = t.release()
        listed = {int(a["id"]) for a in (rel or {}).get("assets", [])}
        published = [a for a in _published(rel) if int(a["id"]) != deleted_id]
        if published and int(published[0]["id"]) == staged_id:
            return
        if published and staged_id in listed:
            _drop(t, staged_id, pending)
            raise RegistryRace(f"another {TAG}/{ASSET} was published during the swap")
        if published or deleted_id in listed or staged_id not in listed:
            log.warning("stale release read after the swap (attempt %d/%d): reading again", attempt, RENAME_ATTEMPTS)
        if attempt < RENAME_ATTEMPTS:
            _sleep(_backoff(attempt))
    if deleted_id is None:           # nothing was deleted: push_release removes our upload, the release is as it was
        raise RegistryStranded(f"{pending} could not be published as {TAG}/{ASSET}; the release was left as it was "
                               "(nothing was deleted): try again")
    raise RegistryStranded(f"{TAG}/{ASSET} was deleted but {pending} could not be renamed; it holds the merged "
                           f"registry and was left in place: run `{RECOVER}`")


def push_release(t: Transport, rows: list[dict[str, Any]], *, expected: Stamp, strict: bool = True) -> None:
    """Publish `rows` only while the registry asset still has the stamp `expected` (steps 3-6). Raises RegistryRace
    when another writer landed, leaving the published copy as that writer left it."""
    rel = t.release()
    if rel is None:
        if expected is not None:
            raise RegistryRace(f"release {TAG} disappeared after the pull")
        rel = t.create_release()
    pending = f"{PENDING}{uuid.uuid4().hex[:12]}"
    try:
        staged_id = int(t.upload(rel, pending, _encode(rows))["id"])
    except BaseException:          # the upload may have landed before the failure (gh lists the release after it)
        _drop_named(t, pending)
        raise
    deleted_id: int | None = None
    try:
        current = _asset(t.release(), own=pending, strict=strict)
        if _stamp(current) != expected:
            raise RegistryRace(f"{TAG}/{ASSET} changed after the pull ({expected} -> {_stamp(current)})")
        if current is not None:
            if not t.delete(int(current["id"])):
                raise RegistryRace(f"{TAG}/{ASSET} was replaced just before the swap")
            deleted_id = int(current["id"])
        _publish(t, staged_id, pending, deleted_id)
    except BaseException:
        if deleted_id is None:     # after the delete our upload may be the only copy: _publish decides
            _drop(t, staged_id, pending)
        raise


def save_rows(t: Transport, local: Path, *, attempts: int = ATTEMPTS, bootstrap: bool = False) -> int:
    """Pull the release copy, union it with `local`, write both back. Returns the merged trial count.

    A writer landing between the pull and the push (RegistryRace) means pull, merge and push again after a short
    jittered wait, at most `attempts` times; `local` keeps every union it saw, so nothing is lost when the last
    attempt fails too."""
    rows = read_rows(local)
    last: RegistryRace | None = None
    for attempt in range(1, attempts + 1):
        try:
            remote, stamp = pull_stamped(t, bootstrap=bootstrap)
            rows = merge_rows(rows, remote)
            if len(rows) < len(remote):
                raise ValueError(f"the merged registry has {len(rows)} rows, the published one {len(remote)}: "
                                 "refusing to upload")
            write_rows(local, rows)
            push_release(t, rows, expected=stamp)
        except RegistryRace as exc:
            last = exc
            log.warning("trial registry push lost a race (attempt %d/%d): %s", attempt, attempts, exc)
            if attempt < attempts:
                _sleep(_backoff(attempt))
            continue
        return sum(1 for r in rows if is_trial(r))
    raise RegistryRace(f"{TAG}/{ASSET} changed during each of {attempts} saves; the local registry keeps the union, "
                       f"the published copy was left as the other writer wrote it (last: {last})")


def sync(local: Path, token: str, repo: str = REPO, *, attempts: int = ATTEMPTS) -> int:
    """The VPS monthly loop's sync (scheduler `sync_trials`)."""
    return save_rows(HttpTransport(token, repo), local, attempts=attempts)


def recover(t: Transport) -> int:
    """Union every registry copy on the release (registry.jsonl and each pending upload, e.g. one stranded by a
    failed rename) and publish it through the same compare-and-swap; then delete the merged pending copies. Returns
    the merged trial count."""
    rel = t.release()
    if rel is None:
        raise RegistryMissing(f"release {TAG} does not exist: nothing to recover")
    copies = [a for a in rel.get("assets", []) if a["name"] == ASSET or str(a["name"]).startswith(PENDING)]
    if not copies:
        raise RegistryMissing(f"release {TAG} has no {ASSET} and no pending upload: nothing to recover")
    copies.sort(key=lambda a: (a["name"] != ASSET, int(a["id"])))     # the published copy first
    sources = [_parse(t.download(a)) for a in copies]
    rows = merge_rows(*sources)
    if len(rows) < max(len(s) for s in sources):
        raise ValueError("the merged registry is shorter than one of its copies: refusing to publish")
    seen: set[tuple[str, str, str, str]] = set()
    for a, src in zip(copies, sources, strict=True):    # what each copy adds, before anything is published
        added = sorted({_key(r) for r in src} - seen)
        seen.update(added)
        print(f"{a['name']} (id {a['id']}, {len(src)} rows): adds {len(added)} rows"
              + "".join(f"\n  {ts} {agent} {cfg} {fv}" for ts, agent, cfg, fv in added))
    print(f"publishing {len(rows)} rows ({sum(1 for r in rows if is_trial(r))} trials)")
    push_release(t, rows, expected=_stamp(_asset(rel, strict=False)), strict=False)
    for a in copies:
        if a["name"] != ASSET:
            _drop(t, int(a["id"]), str(a["name"]))
    return sum(1 for r in rows if is_trial(r))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge", help="union registry files into one, renumbered")
    m.add_argument("inputs", nargs="+", type=Path)
    m.add_argument("-o", "--out", type=Path, default=None)
    f = sub.add_parser("fetch", help="(gh) the published registry into OUT; empty only when the release is absent")
    f.add_argument("out", type=Path)
    f.add_argument("--bootstrap", action="store_true", help="also start empty when the release has no registry")
    s = sub.add_parser("save", help="(gh) union LOCAL with the published registry and publish it (compare-and-swap)")
    s.add_argument("local", type=Path)
    s.add_argument("--bootstrap", action="store_true", help="allow a release without a registry (a new registry)")
    sub.add_parser("recover", help="(gh) union every registry copy on the release, including pending uploads, "
                                   "and publish it")
    args = ap.parse_args()
    if args.cmd == "merge":
        out = args.out or args.inputs[0]
        print(f"{merge_files(out, *args.inputs)} trials -> {out}")
        return
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        if args.cmd == "fetch":
            rows, _ = pull_stamped(GhTransport(), bootstrap=args.bootstrap)
            write_rows(args.out, rows)
            print(f"{len(rows)} rows -> {args.out}")
        elif args.cmd == "save":
            before = len(read_rows(args.local))
            n = save_rows(GhTransport(), args.local, bootstrap=args.bootstrap)
            print(f"published {args.local}: {n} trials ({len(read_rows(args.local))} rows, {before} local before)")
        else:
            print(f"recovered {recover(GhTransport())} trials into {TAG}/{ASSET}")
    except (RegistryRace, RegistryStranded, RegistryMissing, GhError, ValueError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
