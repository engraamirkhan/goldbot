"""BACKLOG 51: a registry push (goldbot.research.registry_sync, the one protocol both writers run) re-checks the
published asset just before the swap, so a write that lands between the pull and the push is merged, never
overwritten. A fake release (no network) plays GitHub's asset API: names are unique (a second upload onto a taken
name is a 422), a deleted id is a 404, every write gets a new id and updated_at. The rename's own status is not
relied on: the fake can fail it (502) or report success without renaming, and the pusher re-reads instead."""
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


Hook = Callable[["FakeRelease"], object] | None


class FakeRelease:
    """research-v1 as GitHub serves it. Hooks, each run once: `before_swap` after a pending upload (before the
    pusher's re-check), `before_delete` just before a delete, `on_patch` just before a rename. `patch_fails` makes
    that many renames answer 502 without renaming; `patch_noop` makes that many answer 200 without renaming; `get_fails_after_upload`
    makes the next release read after an upload answer 502."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, *, exists: bool = True) -> None:
        self.exists = exists
        self.assets: dict[int, dict[str, Any]] = {}
        self._next = 100
        self.clock = 0
        self.before_swap: Hook = None
        self.before_delete: Hook = None
        self.on_patch: Hook = None
        self.patch_fails = 0
        self.patch_noop = 0
        self.get_fails_after_upload = False
        self.calls: list[tuple[str, str]] = []
        if rows is not None:
            self.put(rows)

    # -- what another writer outside the protocol does (or the test's setup)
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

    def rows_of(self, name: str) -> list[dict[str, Any]]:
        a = next(a for a in self.assets.values() if a["name"] == name)
        return [json.loads(x) for x in a["body"].decode().splitlines()]

    def published(self) -> list[dict[str, Any]]:
        return self.rows_of(rs.ASSET) if rs.ASSET in self.names() else []

    def names(self) -> list[str]:
        return sorted(a["name"] for a in self.assets.values())

    def pending(self) -> list[str]:
        return [n for n in self.names() if n.startswith(rs.PENDING)]

    @staticmethod
    def _run(hook: Hook, rel: FakeRelease) -> None:
        if hook is not None:
            hook(rel)

    # -- the HTTP surface registry_sync.HttpTransport uses
    def api(self, url: str, token: str, *, method: str = "GET", data: bytes | None = None,
            ctype: str | None = None) -> Any:
        self.calls.append((method, url))
        if url == f"{BASE}/releases/tags/{rs.TAG}" and method == "GET":
            if self.get_fails_after_upload and any(m == "POST" and "uploads" in u for m, u in self.calls):
                self.get_fails_after_upload = False
                raise _err(url, 502)
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
            hook, self.before_swap = self.before_swap, None
            self._run(hook, self)
            return {k: v for k, v in self.assets[new].items() if k != "body"}
        if url.startswith(f"{BASE}/releases/assets/"):
            aid = int(url.rsplit("/", 1)[1])
            if method == "DELETE":
                hook, self.before_delete = self.before_delete, None
                self._run(hook, self)
                if aid not in self.assets:
                    raise _err(url, 404)
                del self.assets[aid]
                return None
            if method == "PATCH":
                hook, self.on_patch = self.on_patch, None
                self._run(hook, self)
                if self.patch_fails > 0:
                    self.patch_fails -= 1
                    raise _err(url, 502)
                if aid not in self.assets:
                    raise _err(url, 404)
                if self.patch_noop > 0:          # 200, nothing renamed: the status code proves nothing
                    self.patch_noop -= 1
                    return {k: v for k, v in self.assets[aid].items() if k != "body"}
                name = json.loads(data or b"{}")["name"]
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


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeRelease) -> FakeRelease:
    monkeypatch.setattr(rs, "_api", fake.api)
    monkeypatch.setattr(rs, "_download", fake.download)
    monkeypatch.setattr(rs, "_sleep", lambda s: None)
    return fake


@pytest.fixture
def release(monkeypatch: pytest.MonkeyPatch) -> FakeRelease:
    return _install(monkeypatch, FakeRelease([_row("2026-10-01T00:00:00+00:00", "aaa", "w")]))


def _t() -> rs.HttpTransport:
    return rs.HttpTransport("tok", REPO)


def _local(tmp_path: Path, *rows: dict[str, Any], name: str = "research_registry.jsonl") -> Path:
    p = tmp_path / name
    rs.write_rows(p, list(rows))
    return p


def _hashes(rows: list[dict[str, Any]]) -> list[str]:
    return [r["config_hash"] for r in rows]


VPS = _row("2026-10-02T00:00:00+00:00", "vps")
WF = _row("2026-10-03T00:00:00+00:00", "wf", "w")


def test_sync_publishes_the_union_through_a_pending_asset_and_leaves_one_copy(release: FakeRelease,
                                                                             tmp_path: Path) -> None:
    assert rs.sync(_local(tmp_path, VPS), "tok", REPO) == 2
    assert _hashes(release.published()) == ["aaa", "vps"] and release.names() == [rs.ASSET]
    uploads = [u for m, u in release.calls if u.startswith("https://uploads.example")]
    assert uploads and all(f"name={rs.PENDING}" in u for u in uploads)      # never uploaded straight over the copy


def test_a_workflow_upload_between_the_pull_and_the_push_is_merged_not_overwritten(release: FakeRelease,
                                                                                    tmp_path: Path) -> None:
    local = _local(tmp_path, VPS)
    release.before_swap = lambda rel: rel.put(rel.published() + [WF])
    n = rs.sync(local, "tok", REPO)
    published = release.published()
    assert _hashes(published) == ["aaa", "vps", "wf"]                 # both writers' rows survive
    assert n == 3 and [r["trial"] for r in published] == [1, 2, 3]    # each trial counted once
    assert _hashes(rs.read_rows(local)) == ["aaa", "vps", "wf"] and release.names() == [rs.ASSET]


def test_a_replacement_between_the_recheck_and_the_delete_is_detected_by_the_delete(release: FakeRelease,
                                                                                    tmp_path: Path) -> None:
    """`gh release upload --clobber` deletes the old id itself: our delete of that id is a 404, so we retry."""
    release.before_delete = lambda rel: rel.put(rel.published() + [WF])
    assert rs.sync(_local(tmp_path, VPS), "tok", REPO) == 3
    assert _hashes(release.published()) == ["aaa", "vps", "wf"] and release.names() == [rs.ASSET]


def test_a_copy_published_during_the_rename_is_detected_by_the_re_read_not_the_status_code(
        release: FakeRelease, tmp_path: Path) -> None:
    """MEDIUM-2: the rename answers 200 without renaming while another copy is published; the re-read sees that
    registry.jsonl is not our id, drops our upload and retries the merge."""
    release.patch_noop = 1
    release.on_patch = lambda rel: rel.put([_row("2026-10-01T00:00:00+00:00", "aaa", "w"), WF])
    assert rs.sync(_local(tmp_path, VPS), "tok", REPO) == 3
    assert _hashes(release.published()) == ["aaa", "vps", "wf"] and release.names() == [rs.ASSET]


def test_a_rename_reported_ok_but_not_done_is_retried_and_never_reported_as_published(
        release: FakeRelease, tmp_path: Path) -> None:
    release.patch_noop = 99
    with pytest.raises(rs.RegistryStranded, match="recover"):
        rs.sync(_local(tmp_path, VPS), "tok", REPO)
    assert sum(1 for m, _ in release.calls if m == "PATCH") == rs.RENAME_ATTEMPTS
    assert len(release.pending()) == 1 and _hashes(release.rows_of(release.pending()[0])) == ["aaa", "vps"]


def test_a_rename_that_fails_once_after_the_delete_is_retried_and_publishes(release: FakeRelease,
                                                                           tmp_path: Path) -> None:
    release.patch_fails = 1
    assert rs.sync(_local(tmp_path, VPS), "tok", REPO) == 2
    assert _hashes(release.published()) == ["aaa", "vps"] and release.names() == [rs.ASSET]


def test_a_rename_that_keeps_failing_after_the_delete_strands_the_merge_and_never_reads_as_empty(
        release: FakeRelease, tmp_path: Path) -> None:
    """HIGH-1 (reviewer's exp_rename_fail.py): PATCH 502 after a successful delete. The pending upload is the only
    full copy: it stays, the next read refuses instead of starting from an empty registry (which would shrink the
    deflated-Sharpe N and restart the numbering), and `recover` publishes it."""
    release.patch_fails = 99
    local = _local(tmp_path, VPS)
    with pytest.raises(rs.RegistryStranded, match="registry_release.sh recover"):
        rs.sync(local, "tok", REPO)
    assert release.names() == release.pending() and len(release.pending()) == 1
    assert _hashes(release.rows_of(release.pending()[0])) == ["aaa", "vps"]
    with pytest.raises(rs.RegistryRace, match="in flight"):
        rs.pull_stamped(_t())                                    # a workflow's fetch, or the VPS next month
    with pytest.raises(rs.RegistryRace):
        rs.sync(_local(tmp_path, WF, name="wf.jsonl"), "tok", REPO)
    assert len(release.pending()) == 1                           # still nothing published over it
    release.patch_fails = 0
    assert rs.recover(_t()) == 2
    assert _hashes(release.published()) == ["aaa", "vps"] and release.names() == [rs.ASSET]
    assert rs.sync(_local(tmp_path, WF, name="wf.jsonl"), "tok", REPO) == 3


def test_recover_unions_every_pending_copy_with_the_published_one(release: FakeRelease) -> None:
    release.put([VPS], f"{rs.PENDING}one")
    release.put([WF], f"{rs.PENDING}two")
    assert rs.recover(_t()) == 3
    assert _hashes(release.published()) == ["aaa", "vps", "wf"] and release.names() == [rs.ASSET]


def test_a_failed_recheck_drops_our_pending_upload(release: FakeRelease, tmp_path: Path) -> None:
    """MEDIUM-1: any failure before the delete removes our pending asset (the published copy is untouched)."""
    release.get_fails_after_upload = True
    with pytest.raises(urllib.error.HTTPError):
        rs.sync(_local(tmp_path, VPS), "tok", REPO)
    assert release.names() == [rs.ASSET] and _hashes(release.published()) == ["aaa"]


def test_a_registry_that_keeps_changing_fails_loudly_after_a_bounded_number_of_tries_and_loses_nothing(
        release: FakeRelease, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local = _local(tmp_path, VPS)
    pushes = 0
    real_push = rs.push_release
    waits: list[float] = []
    monkeypatch.setattr(rs, "_sleep", waits.append)

    def push_with_a_writer_every_time(t: rs.Transport, rows: list[dict[str, Any]], *, expected: rs.Stamp,
                                      strict: bool = True) -> None:
        nonlocal pushes
        pushes += 1
        n = pushes
        release.before_swap = lambda rel: rel.put(rel.published() + [_row(f"2026-10-1{n}T00:00:00+00:00",
                                                                          f"wf{n}", "w")])
        real_push(t, rows, expected=expected, strict=strict)
    monkeypatch.setattr(rs, "push_release", push_with_a_writer_every_time)
    with pytest.raises(rs.RegistryRace, match="each of 3 saves"):
        rs.sync(local, "tok", REPO, attempts=3)
    assert pushes == 3 and len(waits) == 2 and all(0.5 <= w <= 3.0 for w in waits)   # jittered backoff between
    assert _hashes(release.published()) == ["aaa", "wf1", "wf2", "wf3"]     # the other writer's copy is untouched
    assert release.names() == [rs.ASSET]
    kept = rs.read_rows(local)                               # the VPS keeps its row and every row it saw
    assert {"vps", "aaa", "wf1", "wf2"} <= set(_hashes(kept)) and len(set(_hashes(kept))) == len(kept)
    assert TrialRegistry(local).n_trials == sum(1 for r in kept if is_trial(r))


def test_a_mid_swap_state_seen_at_the_pull_is_retried_after_a_backoff(release: FakeRelease, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """LOW: another writer is between its delete and its rename when we pull; it finishes while we wait."""
    rows = release.published()
    release.assets.clear()
    pid = release.put(rows, f"{rs.PENDING}other")
    waits: list[float] = []

    def wait_and_let_the_other_writer_finish(s: float) -> None:
        waits.append(s)
        release.assets[pid]["name"] = rs.ASSET
    monkeypatch.setattr(rs, "_sleep", wait_and_let_the_other_writer_finish)
    assert rs.sync(_local(tmp_path, VPS), "tok", REPO) == 2
    assert len(waits) == 1 and _hashes(release.published()) == ["aaa", "vps"] and release.names() == [rs.ASSET]


def test_two_protocol_writers_interleaved_on_an_existing_registry_both_land(release: FakeRelease,
                                                                            tmp_path: Path) -> None:
    """Both pull the same stamp; B's whole save runs while A's upload is in flight; A's re-check then fails and A
    merges B's copy."""
    t = _t()
    a_local, b_local = _local(tmp_path, VPS, name="a.jsonl"), _local(tmp_path, WF, name="b.jsonl")
    _, stamp_a = rs.pull_stamped(t)
    release.before_swap = lambda rel: rs.save_rows(t, b_local)
    with pytest.raises(rs.RegistryRace):
        rs.push_release(t, rs.merge_rows(rs.read_rows(a_local), release.published()), expected=stamp_a)
    assert _hashes(release.published()) == ["aaa", "wf"] and release.names() == [rs.ASSET]
    assert rs.save_rows(t, a_local) == 3
    assert _hashes(release.published()) == ["aaa", "vps", "wf"] and release.names() == [rs.ASSET]


def test_two_protocol_writers_on_the_first_ever_push_with_both_stamps_none(monkeypatch: pytest.MonkeyPatch,
                                                                         tmp_path: Path) -> None:
    fake = _install(monkeypatch, FakeRelease(exists=False))
    t = _t()
    a_rows, stamp_a = rs.pull_stamped(t)
    b_rows, stamp_b = rs.pull_stamped(t)
    assert stamp_a is None and stamp_b is None and a_rows == b_rows == []

    def b_pushes(rel: FakeRelease) -> None:                 # B sees A's pending upload and no published copy
        with pytest.raises(rs.RegistryRace, match="in flight"):
            rs.push_release(t, [WF], expected=stamp_b)
    fake.before_swap = b_pushes
    rs.push_release(t, [VPS], expected=stamp_a)
    assert _hashes(fake.published()) == ["vps"] and fake.names() == [rs.ASSET]     # B dropped its pending upload
    assert rs.save_rows(t, _local(tmp_path, WF, name="b.jsonl")) == 2            # B's retry merges A's copy
    assert _hashes(fake.published()) == ["vps", "wf"] and fake.names() == [rs.ASSET]


def test_the_monthly_loop_records_a_lost_race_and_carries_on(release: FakeRelease, tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from goldbot.ops.jobs import _sync_trials

    local = _local(tmp_path, VPS)
    monkeypatch.setattr(rs, "push_release", lambda *a, **k: (_ for _ in ()).throw(rs.RegistryRace("busy")))

    class Ctx:
        trials = TrialRegistry(local)
        sync_trials = staticmethod(lambda path: rs.sync(path, "tok", REPO))
    assert str(_sync_trials(Ctx())).startswith("failed:")                 # type: ignore[arg-type]
    assert _hashes(release.published()) == ["aaa"]


def test_a_pending_upload_without_a_published_copy_is_never_read_as_an_empty_registry(release: FakeRelease,
                                                                                       tmp_path: Path) -> None:
    rows = release.published()
    release.assets.clear()
    release.put(rows, f"{rs.PENDING}other")
    local = _local(tmp_path, VPS)
    with pytest.raises(rs.RegistryRace, match="in flight"):
        rs.sync(local, "tok", REPO)
    assert release.names() == [f"{rs.PENDING}other"] and _hashes(rs.read_rows(local)) == ["vps"]


def test_a_release_without_a_registry_is_read_as_empty_only_with_bootstrap(monkeypatch: pytest.MonkeyPatch,
                                                                          tmp_path: Path) -> None:
    fake = _install(monkeypatch, FakeRelease(exists=True))
    with pytest.raises(rs.RegistryMissing, match="--bootstrap"):
        rs.sync(_local(tmp_path, VPS), "tok", REPO)
    assert fake.names() == []
    assert rs.save_rows(_t(), _local(tmp_path, VPS), bootstrap=True) == 1
    assert _hashes(fake.published()) == ["vps"] and fake.names() == [rs.ASSET]


def test_the_first_ever_push_creates_the_release_and_publishes(monkeypatch: pytest.MonkeyPatch,
                                                               tmp_path: Path) -> None:
    fake = _install(monkeypatch, FakeRelease(exists=False))
    assert rs.sync(_local(tmp_path, VPS), "tok", REPO) == 1
    assert _hashes(fake.published()) == ["vps"] and fake.names() == [rs.ASSET]


def test_a_release_created_by_another_writer_after_an_empty_pull_is_not_overwritten(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _install(monkeypatch, FakeRelease(exists=False))
    fake.before_swap = lambda rel: rel.put([WF])
    assert rs.sync(_local(tmp_path, VPS), "tok", REPO) == 2
    assert _hashes(fake.published()) == ["vps", "wf"] and fake.names() == [rs.ASSET]


def test_sync_still_refuses_a_union_shorter_than_the_published_copy(release: FakeRelease, tmp_path: Path) -> None:
    dup = _row("2026-10-01T00:00:00+00:00", "aaa", "w")
    release.put([dup, dup, dup])                             # the keyed union keeps one: 1 row < 3 published
    with pytest.raises(ValueError, match="refusing to upload"):
        rs.sync(_local(tmp_path), "tok", REPO)
    assert len(release.published()) == 3 and not any(m == "PATCH" for m, _ in release.calls)
