"""BACKLOG 51: the VPS monthly loop's registry push (goldbot.research.registry_sync) re-checks the published asset just
before the swap, so a workflow upload that lands between the pull and the push is merged, never overwritten. A fake
release (no network) plays GitHub's asset API: names are unique (a second upload or a rename onto a taken name is a
422), a deleted id is a 404, every write gets a new id and updated_at."""
from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from goldbot.research import registry_sync as rs
from goldbot.research.registry import TrialRegistry, is_trial

REPO = "o/r"
BASE = f"{rs.API}/repos/{REPO}"


def _row(ts: str, h: str, agent: str = "a") -> dict[str, Any]:
    return {"trial": 1, "ts": ts, "agent_id": agent, "config_hash": h, "feature_version": "f1", "family": "tsmom",
            "status": "evaluated", "results": {}}


def _err(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "fake", {}, io.BytesIO(b""))  # type: ignore[arg-type]


class FakeRelease:
    """research-v1 as GitHub serves it. `before_swap` runs once per push, after the pending upload and before the
    pusher's re-check (the window a concurrent writer lands in); `before_delete` just before the delete of the old id."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, *, exists: bool = True) -> None:
        self.exists = exists
        self.assets: dict[int, dict[str, Any]] = {}
        self._next = 100
        self.clock = 0
        self.before_swap: Callable[[FakeRelease], object] | None = None
        self.before_delete: Callable[[FakeRelease], object] | None = None
        self.calls: list[tuple[str, str]] = []
        if rows is not None:
            self.put(rows)

    # -- what another writer does (the workflow's `registry_release.sh save`)
    def put(self, rows: list[dict[str, Any]], name: str = rs.ASSET) -> int:
        for i, a in list(self.assets.items()):
            if a["name"] == name:
                del self.assets[i]
        self._next += 1
        self.clock += 1
        body = "".join(json.dumps(r) + "\n" for r in rows).encode()
        self.assets[self._next] = {"id": self._next, "name": name, "updated_at": f"t{self.clock}", "size": len(body),
                                   "digest": f"sha256:{hash(body)}", "url": f"{BASE}/releases/assets/{self._next}",
                                   "body": body}
        return self._next

    def published(self) -> list[dict[str, Any]]:
        for a in self.assets.values():
            if a["name"] == rs.ASSET:
                return [json.loads(x) for x in a["body"].decode().splitlines()]
        return []

    def names(self) -> list[str]:
        return sorted(a["name"] for a in self.assets.values())

    # -- the HTTP surface registry_sync uses
    def api(self, url: str, token: str, *, method: str = "GET", data: bytes | None = None,
            ctype: str | None = None) -> Any:
        self.calls.append((method, url))
        if url == f"{BASE}/releases/tags/{rs.TAG}" and method == "GET":
            if not self.exists:
                raise _err(url, 404)
            return {"upload_url": "https://uploads.example/assets{?name,label}",
                    "assets": [{k: v for k, v in a.items() if k != "body"} for a in self.assets.values()]}
        if url == f"{BASE}/releases" and method == "POST":
            self.exists = True
            return self.api(f"{BASE}/releases/tags/{rs.TAG}", token)
        if url.startswith("https://uploads.example/assets?name=") and method == "POST":
            name = url.split("=", 1)[1]
            if name in self.names():
                raise _err(url, 422)
            new = self.put([json.loads(x) for x in (data or b"").decode().splitlines()], name)
            if self.before_swap is not None:
                hook, self.before_swap = self.before_swap, None
                hook(self)
            return {k: v for k, v in self.assets[new].items() if k != "body"}
        if url.startswith(f"{BASE}/releases/assets/"):
            aid = int(url.rsplit("/", 1)[1])
            if method == "DELETE":
                if self.before_delete is not None:
                    hook, self.before_delete = self.before_delete, None
                    hook(self)
                if aid not in self.assets:
                    raise _err(url, 404)
                del self.assets[aid]
                return None
            if method == "PATCH":
                name = json.loads(data or b"{}")["name"]
                if aid not in self.assets:
                    raise _err(url, 404)
                if name in self.names():
                    raise _err(url, 422)
                self.clock += 1
                self.assets[aid].update(name=name, updated_at=f"t{self.clock}")
                return {k: v for k, v in self.assets[aid].items() if k != "body"}
        raise AssertionError(f"unexpected call {method} {url}")

    def download(self, url: str, token: str) -> bytes:
        aid = int(url.rsplit("/", 1)[1])
        body: bytes = self.assets[aid]["body"]
        return body


@pytest.fixture
def release(monkeypatch: pytest.MonkeyPatch) -> FakeRelease:
    fake = FakeRelease([_row("2026-10-01T00:00:00+00:00", "aaa", "w")])
    monkeypatch.setattr(rs, "_api", fake.api)
    monkeypatch.setattr(rs, "_download", fake.download)
    return fake


def _local(tmp_path: Path, *rows: dict[str, Any]) -> Path:
    p = tmp_path / "research_registry.jsonl"
    rs.write_rows(p, list(rows))
    return p


def _hashes(rows: list[dict[str, Any]]) -> list[str]:
    return [r["config_hash"] for r in rows]


def _assert_one_published_copy(release: FakeRelease) -> None:
    assert release.names() == [rs.ASSET]                        # no pending upload left behind


def test_sync_publishes_the_union_through_a_pending_asset_and_leaves_one_copy(release: FakeRelease,
                                                                             tmp_path: Path) -> None:
    local = _local(tmp_path, _row("2026-10-02T00:00:00+00:00", "vps"))
    assert rs.sync(local, "tok", REPO) == 2
    assert _hashes(release.published()) == ["aaa", "vps"]
    _assert_one_published_copy(release)
    uploads = [u for m, u in release.calls if u.startswith("https://uploads.example")]
    assert uploads and all(f"name={rs.PENDING}" in u for u in uploads)      # never uploaded straight over the copy


def test_a_workflow_upload_between_the_pull_and_the_push_is_merged_not_overwritten(release: FakeRelease,
                                                                                    tmp_path: Path) -> None:
    local = _local(tmp_path, _row("2026-10-02T00:00:00+00:00", "vps"))

    def workflow_writes(rel: FakeRelease) -> None:          # research.yml saves its trial while the VPS uploads
        rel.put(rel.published() + [_row("2026-10-03T00:00:00+00:00", "wf", "w")])
    release.before_swap = workflow_writes
    n = rs.sync(local, "tok", REPO)
    published = release.published()
    assert _hashes(published) == ["aaa", "vps", "wf"]                 # both writers' rows survive
    assert n == 3 and [r["trial"] for r in published] == [1, 2, 3]    # each trial counted once
    assert _hashes(rs.read_rows(local)) == ["aaa", "vps", "wf"]
    _assert_one_published_copy(release)


def test_a_replacement_between_the_recheck_and_the_delete_is_detected_by_the_delete(release: FakeRelease,
                                                                                    tmp_path: Path) -> None:
    """`gh release upload --clobber` deletes the old id itself: our delete of that id is a 404, so we retry."""
    local = _local(tmp_path, _row("2026-10-02T00:00:00+00:00", "vps"))
    release.before_delete = lambda rel: rel.put(rel.published() + [_row("2026-10-03T00:00:00+00:00", "wf", "w")])
    assert rs.sync(local, "tok", REPO) == 3
    assert _hashes(release.published()) == ["aaa", "vps", "wf"]
    _assert_one_published_copy(release)


def test_an_upload_landing_in_the_swap_gap_makes_the_rename_fail_and_the_retry_merges_it(
        release: FakeRelease, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local = _local(tmp_path, _row("2026-10-02T00:00:00+00:00", "vps"))
    real_delete = rs._delete_asset
    landed: list[int] = []

    def delete_then_someone_uploads(token: str, repo: str, asset_id: int) -> None:
        real_delete(token, repo, asset_id)
        if not landed and release.assets.get(asset_id) is None and rs.ASSET not in release.names():
            landed.append(release.put([_row("2026-10-03T00:00:00+00:00", "wf", "w")]))
    monkeypatch.setattr(rs, "_delete_asset", delete_then_someone_uploads)
    assert rs.sync(local, "tok", REPO) == 3
    assert landed and _hashes(release.published()) == ["aaa", "vps", "wf"]
    _assert_one_published_copy(release)


def test_a_registry_that_keeps_changing_fails_loudly_after_a_bounded_number_of_tries_and_loses_nothing(
        release: FakeRelease, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local = _local(tmp_path, _row("2026-10-02T00:00:00+00:00", "vps"))
    pushes = 0
    real_push = rs.push_release

    def push_with_a_writer_every_time(token: str, rows: list[dict[str, Any]], repo: str = rs.REPO, *,
                                      expected: rs.Stamp = None) -> None:
        nonlocal pushes
        pushes += 1
        release.before_swap = lambda rel: rel.put(rel.published() + [_row(f"2026-10-1{pushes}T00:00:00+00:00",
                                                                          f"wf{pushes}", "w")])
        real_push(token, rows, repo, expected=expected)
    monkeypatch.setattr(rs, "push_release", push_with_a_writer_every_time)
    with pytest.raises(rs.RegistryRace, match="each of 3 pushes"):
        rs.sync(local, "tok", REPO, attempts=3)
    assert pushes == 3
    assert _hashes(release.published()) == ["aaa", "wf1", "wf2", "wf3"]     # the other writer's copy is untouched
    _assert_one_published_copy(release)
    kept = rs.read_rows(local)                               # the VPS keeps its row and every row it saw
    assert {"vps", "aaa", "wf1", "wf2"} <= set(_hashes(kept)) and len(set(_hashes(kept))) == len(kept)
    assert TrialRegistry(local).n_trials == sum(1 for r in kept if is_trial(r))


def test_the_monthly_loop_records_a_lost_race_and_carries_on(release: FakeRelease, tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from goldbot.ops.jobs import _sync_trials

    local = _local(tmp_path, _row("2026-10-02T00:00:00+00:00", "vps"))
    monkeypatch.setattr(rs, "push_release", lambda *a, **k: (_ for _ in ()).throw(rs.RegistryRace("busy")))

    class Ctx:
        trials = TrialRegistry(local)
        sync_trials = staticmethod(lambda path: rs.sync(path, "tok", REPO))
    assert str(_sync_trials(Ctx())).startswith("failed:")                 # type: ignore[arg-type]
    assert _hashes(release.published()) == ["aaa"]


def test_a_pending_upload_without_a_published_copy_is_never_read_as_an_empty_registry(release: FakeRelease,
                                                                                       tmp_path: Path) -> None:
    """Another writer is between its delete and its rename (or crashed there): pulling empty would drop every row."""
    rows = release.published()
    release.assets.clear()
    release.put(rows, f"{rs.PENDING}other")
    local = _local(tmp_path, _row("2026-10-02T00:00:00+00:00", "vps"))
    with pytest.raises(rs.RegistryRace, match="in flight"):
        rs.sync(local, "tok", REPO)
    assert release.names() == [f"{rs.PENDING}other"]                     # nothing published over it
    assert _hashes(rs.read_rows(local)) == ["vps"]


def test_a_release_created_by_another_writer_after_an_empty_pull_is_not_overwritten(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeRelease(exists=False)
    monkeypatch.setattr(rs, "_api", fake.api)
    monkeypatch.setattr(rs, "_download", fake.download)
    fake.before_swap = lambda rel: rel.put([_row("2026-10-03T00:00:00+00:00", "wf", "w")])
    local = _local(tmp_path, _row("2026-10-02T00:00:00+00:00", "vps"))
    assert rs.sync(local, "tok", REPO) == 2
    assert _hashes(fake.published()) == ["vps", "wf"]
    _assert_one_published_copy(fake)


@pytest.mark.parametrize("exists", [True, False], ids=["empty-release", "no-release"])
def test_the_first_ever_push_publishes_despite_its_own_pending_upload(monkeypatch: pytest.MonkeyPatch,
                                                                       tmp_path: Path, exists: bool) -> None:
    fake = FakeRelease(exists=exists)
    monkeypatch.setattr(rs, "_api", fake.api)
    monkeypatch.setattr(rs, "_download", fake.download)
    local = _local(tmp_path, _row("2026-10-02T00:00:00+00:00", "vps"))
    assert rs.sync(local, "tok", REPO) == 1
    assert _hashes(fake.published()) == ["vps"]
    _assert_one_published_copy(fake)


def test_the_stamp_command_used_by_the_workflow_script(capsys: pytest.CaptureFixture[str]) -> None:
    rel = {"assets": [{"id": 5, "name": rs.ASSET, "updated_at": "t1", "size": 9, "digest": "sha256:d"},
                      {"id": 6, "name": f"{rs.PENDING}mine", "updated_at": "t2", "size": 9}]}
    assert rs._stamp_cli(rel, own=None, id_of=None) == 0 and capsys.readouterr().out == "5\tt1\t9\tsha256:d\n"
    assert rs._stamp_cli(rel, own=None, id_of=f"{rs.PENDING}mine") == 0 and capsys.readouterr().out == "6\n"
    only_pending = {"assets": rel["assets"][1:]}
    assert rs._stamp_cli(only_pending, own=f"{rs.PENDING}mine", id_of=None) == 0
    assert capsys.readouterr().out == "none\n"
    assert rs._stamp_cli(only_pending, own=None, id_of=None) == 1 and "in flight" in capsys.readouterr().err
    assert rs._stamp_cli({"assets": []}, own=None, id_of=None) == 0 and capsys.readouterr().out == "none\n"


def test_sync_still_refuses_a_union_shorter_than_the_published_copy(release: FakeRelease, tmp_path: Path) -> None:
    dup = _row("2026-10-01T00:00:00+00:00", "aaa", "w")
    release.put([dup, dup, dup])                             # the keyed union keeps one: 1 row < 3 published
    local = _local(tmp_path)
    with pytest.raises(ValueError, match="refusing to upload"):
        rs.sync(local, "tok", REPO)
    assert len(release.published()) == 3 and not any(m == "PATCH" for m, _ in release.calls)
