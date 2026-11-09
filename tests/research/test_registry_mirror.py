"""The Mac's state/research_registry.jsonl is a read-only mirror of the brain's (PD ruling 2026-11-09, item d).

`registry_sync.pull_brain` replaces it with the brain copy (never one that drops a local key), 0444, and writes the
`<registry>.mirror` marker; with the marker present every writer refuses and leaves the bytes as they were."""
from __future__ import annotations

import hashlib
import json
import shlex
import stat
from pathlib import Path
from typing import Any

import pytest

from goldbot.research import registry_sync
from goldbot.research.registry import RegistryReadOnlyMirror, TrialRegistry


def _row(i: int) -> dict[str, Any]:
    return {"trial": i, "ts": f"2026-11-0{i}T00:00:00+00:00", "agent_id": "a", "config_hash": f"h{i}",
            "feature_version": "v1", "family": "f", "status": "evaluated", "results": {}}


def _encode(rows: list[dict[str, Any]]) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in rows).encode()


def _local(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    p = tmp_path / "state" / "research_registry.jsonl"
    p.parent.mkdir(parents=True)
    p.write_bytes(_encode(rows))
    return p


def test_a_copy_missing_a_local_key_is_refused(tmp_path: Path) -> None:
    local = _local(tmp_path, [_row(1), _row(2), _row(3)])
    before = local.read_bytes()
    with pytest.raises(ValueError, match="key"):
        registry_sync.pull_brain(local, fetch=lambda: _encode([_row(1), _row(2)]), source="brain")
    assert local.read_bytes() == before
    assert not Path(f"{local}.mirror").exists()
    assert list(local.parent.iterdir()) == [local]          # no temp file left behind


def test_an_empty_or_unparseable_copy_is_refused(tmp_path: Path) -> None:
    local = tmp_path / "research_registry.jsonl"
    with pytest.raises(ValueError):
        registry_sync.pull_brain(local, fetch=lambda: b"", source="brain")
    with pytest.raises(ValueError):
        registry_sync.pull_brain(local, fetch=lambda: _encode([_row(1)]) + b"{not json\n", source="brain")
    assert not local.exists()


def test_a_pull_writes_a_0444_file_and_the_marker(tmp_path: Path) -> None:
    local = _local(tmp_path, [_row(1)])
    data = _encode([_row(1), _row(2), _row(3)])
    n = registry_sync.pull_brain(local, fetch=lambda: data, source="goldbot-brain:/opt/goldbot/state/x.jsonl")
    assert n == 3
    assert local.read_bytes() == data
    assert stat.S_IMODE(local.stat().st_mode) == 0o444
    marker = json.loads(Path(f"{local}.mirror").read_text())
    assert marker["source"] == "goldbot-brain:/opt/goldbot/state/x.jsonl"
    assert marker["sha256"] == hashlib.sha256(data).hexdigest()
    assert marker["rows"] == 3
    assert marker["pulled_at"]
    # a second pull replaces the read-only mirror (the rows only grow)
    more = _encode([_row(1), _row(2), _row(3), _row(4)])
    assert registry_sync.pull_brain(local, fetch=lambda: more, source="brain") == 4
    assert local.read_bytes() == more
    assert stat.S_IMODE(local.stat().st_mode) == 0o444
    assert json.loads(Path(f"{local}.mirror").read_text())["rows"] == 4


def _writes(reg: TrialRegistry, local: Path) -> dict[str, Any]:
    return {
        "record": lambda: reg.record(agent_id="a", family="f", config={"x": 1}, feature_version="v1",
                                     rationale="r", results={}),
        "preregister": lambda: reg.preregister(agent_id="a", family="f", config={"x": 2}, feature_version="v1",
                                               rationale="r", reading_rule="rule"),
        "discovery_batch": lambda: reg.record_discovery_batch(k_contrib=1, generator="g", ledger_head=(1, "ab")),
        "evidence": lambda: reg.attach_evidence(1, "cpcv", {"p": 1}),
        "write_rows": lambda: registry_sync.write_rows(local, [_row(9)]),
    }


@pytest.mark.parametrize("write", ["record", "preregister", "discovery_batch", "evidence", "write_rows"])
def test_with_the_marker_every_write_is_refused(tmp_path: Path, write: str) -> None:
    local = _local(tmp_path, [_row(1)])
    Path(f"{local}.mirror").write_text("{}")
    reg = TrialRegistry(local)
    files = [local, reg.discovery_path, reg.evidence_path]
    before = {p: p.read_bytes() if p.exists() else None for p in files}
    with pytest.raises(RegistryReadOnlyMirror):
        _writes(reg, local)[write]()
    assert {p: p.read_bytes() if p.exists() else None for p in files} == before


@pytest.mark.parametrize("write", ["record", "preregister", "discovery_batch", "evidence", "write_rows"])
def test_without_the_marker_every_write_works(tmp_path: Path, write: str) -> None:
    local = _local(tmp_path, [_row(1)])
    reg = TrialRegistry(local)
    files = [local, reg.discovery_path, reg.evidence_path]
    before = {p: p.read_bytes() if p.exists() else None for p in files}
    _writes(reg, local)[write]()
    assert {p: p.read_bytes() if p.exists() else None for p in files} != before


def test_a_brain_repair_of_a_local_failed_row_is_not_a_shrink(tmp_path: Path) -> None:
    failed = {**_row(1), "status": "failed"}
    repair = {**_row(1), "agent_id": "repair", "config_hash": "h1r", "status": "evaluated",
              "repair": {"replaces": {k: failed[k] for k in registry_sync.KEY_FIELDS}, "trial": 1}}
    local = _local(tmp_path, [failed])
    brain = registry_sync.merge_rows([failed], [repair])
    assert registry_sync._key(failed) not in {registry_sync._key(r) for r in brain}
    assert registry_sync.pull_brain(local, fetch=lambda: _encode(brain), source="brain") == 1
    assert Path(f"{local}.mirror").exists()


def test_the_pull_holds_the_registry_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local = _local(tmp_path, [_row(1)])
    lock = local.with_suffix(local.suffix + ".lock")
    seen: list[bool] = []
    real = registry_sync._write_atomic

    def spy(path: Path, data: bytes, mode: int) -> None:
        seen.append(lock.exists())
        real(path, data, mode)

    monkeypatch.setattr(registry_sync, "_write_atomic", spy)
    registry_sync.pull_brain(local, fetch=lambda: _encode([_row(1), _row(2)]), source="brain")
    assert seen == [True, True]
    assert not lock.exists()


class _Done:
    returncode, stdout, stderr = 0, b"", b""


@pytest.mark.parametrize("remote", ["/opt/a b/reg.jsonl", "/opt/x.jsonl; rm -rf ~"])
def test_ssh_quotes_the_remote_path_and_ends_options_before_the_host(
        monkeypatch: pytest.MonkeyPatch, remote: str) -> None:
    calls: list[list[str]] = []

    def run(cmd: list[str], **kw: Any) -> _Done:
        calls.append(cmd)
        return _Done()

    monkeypatch.setattr(registry_sync.subprocess, "run", run)
    registry_sync.ssh_fetch("goldbot-brain", remote, ssh_config=Path("cfg"))
    cmd = calls[0]
    assert cmd[cmd.index("goldbot-brain") - 1] == "--"
    assert cmd[-1] == shlex.quote(remote)
    assert shlex.split(cmd[-1]) == [remote]


def test_a_host_that_looks_like_an_ssh_option_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*a: Any, **kw: Any) -> None:
        pytest.fail("ssh must not run")

    monkeypatch.setattr(registry_sync.subprocess, "run", run)
    with pytest.raises(ValueError, match="host"):
        registry_sync.ssh_fetch("-oProxyCommand=touch x", "/opt/x.jsonl")
