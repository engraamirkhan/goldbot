"""A brain token without `Contents: write` cannot publish the registry: the pending payload goes to a local outbox
(state/registry_outbox/) for the Mac to publish with its own gh (scripts/registry_publish.sh), logged at INFO, not
WARNING; the brain clears an entry once the release holds its rows (read-only from the Mac)."""
import logging
from pathlib import Path

import pytest

from goldbot.research import registry_sync as rs
from goldbot.research.registry import TrialRegistry


def _local(base: Path, specs: list[tuple[str, int]]) -> Path:
    reg = TrialRegistry(base / "state" / "research_registry.jsonl")
    for agent, x in specs:
        reg.record(agent_id=agent, family="session_open", config={"x": x}, feature_version="f1", rationale="t",
                   results={})
    return base / "state" / "research_registry.jsonl"


def _forbidden_push(monkeypatch: pytest.MonkeyPatch, remote: list[dict]) -> None:
    monkeypatch.setattr(rs, "pull_stamped", lambda t, bootstrap=False: (remote, (1, "t", 1, "d")))

    def refuse(t, rows, *, expected, strict=True):
        raise rs.RegistryForbidden("HTTP 403 on POST")
    monkeypatch.setattr(rs, "push_release", refuse)


def test_a_forbidden_upload_writes_the_pending_registry_to_the_outbox_and_logs_info(tmp_path, monkeypatch, caplog):
    local = _local(tmp_path, [("a", 1), ("b", 2)])
    _forbidden_push(monkeypatch, [])
    with caplog.at_level(logging.INFO, logger=rs.log.name):
        n = rs.sync(local, "tok")
    entries = list((local.parent / rs.OUTBOX).glob("*.jsonl"))
    assert n == 2 and len(entries) == 1
    assert rs.read_rows(entries[0]) == rs.read_rows(local)
    assert caplog.records and all(r.levelno <= logging.INFO for r in caplog.records)


def test_syncing_again_with_the_same_rows_leaves_exactly_one_identical_outbox_entry(tmp_path, monkeypatch):
    local = _local(tmp_path, [("a", 1)])
    _forbidden_push(monkeypatch, [])
    rs.sync(local, "tok")
    first = {p.name: p.read_bytes() for p in (local.parent / rs.OUTBOX).iterdir()}
    rs.sync(local, "tok")
    assert {p.name: p.read_bytes() for p in (local.parent / rs.OUTBOX).iterdir()} == first


def test_a_grown_registry_replaces_the_older_outbox_entry_and_no_partial_file_is_left(tmp_path, monkeypatch):
    local = _local(tmp_path, [("a", 1)])
    _forbidden_push(monkeypatch, [])
    rs.sync(local, "tok")
    TrialRegistry(local).record(agent_id="b", family="session_open", config={"x": 2}, feature_version="f1",
                                rationale="t", results={})
    rs.sync(local, "tok")
    names = [p.name for p in (local.parent / rs.OUTBOX).iterdir()]
    assert len(names) == 1 and names[0].endswith(".jsonl")
    assert len(rs.read_rows(local.parent / rs.OUTBOX / names[0])) == 2


def test_an_outbox_entry_whose_rows_are_all_on_the_release_is_cleared_on_the_next_sync(tmp_path, monkeypatch):
    local = _local(tmp_path, [("a", 1), ("b", 2)])
    _forbidden_push(monkeypatch, [])
    rs.sync(local, "tok")
    assert list((local.parent / rs.OUTBOX).iterdir())
    _forbidden_push(monkeypatch, rs.read_rows(local))         # the Mac published them
    rs.sync(local, "tok")
    assert not list((local.parent / rs.OUTBOX).iterdir())


def test_a_successful_upload_clears_the_outbox(tmp_path, monkeypatch):
    local = _local(tmp_path, [("a", 1)])
    _forbidden_push(monkeypatch, [])
    rs.sync(local, "tok")
    monkeypatch.setattr(rs, "pull_stamped", lambda t, bootstrap=False: ([], None))
    monkeypatch.setattr(rs, "push_release", lambda t, rows, *, expected, strict=True: None)
    rs.sync(local, "tok")
    assert not list((local.parent / rs.OUTBOX).iterdir())


def test_other_errors_still_raise_and_write_no_outbox(tmp_path, monkeypatch):
    local = _local(tmp_path, [("a", 1)])

    def down(t, bootstrap=False):
        raise OSError("net down")
    monkeypatch.setattr(rs, "pull_stamped", down)
    with pytest.raises(OSError):
        rs.sync(local, "tok")
    assert not (local.parent / rs.OUTBOX).exists()


def test_the_publish_command_merges_the_outbox_with_the_release_through_the_same_union(tmp_path, monkeypatch):
    """The Mac side: outbox files in, the release copy merged exactly as `sync` would (same rows, same numbers)."""
    local = _local(tmp_path, [("a", 1), ("b", 2)])
    outbox = tmp_path / "ob"
    outbox.mkdir()
    (outbox / "x.jsonl").write_text(local.read_text())
    (outbox / "y.jsonl").write_text(local.read_text())                       # a duplicate entry: no duplicate rows
    release = _local(tmp_path / "rel", [("w", 9)])
    seen: dict[str, list] = {}
    monkeypatch.setattr(rs, "pull_stamped", lambda t, bootstrap=False: (rs.read_rows(release), (1, "t", 1, "d")))
    monkeypatch.setattr(rs, "push_release", lambda t, rows, *, expected, strict=True: seen.setdefault("rows", rows))
    n = rs.publish_outbox(object(), outbox, tmp_path / "work.jsonl")  # type: ignore[arg-type]
    assert n == 3 and len(seen["rows"]) == 3
    assert seen["rows"] == rs.merge_rows(rs.read_rows(local), rs.read_rows(release))
