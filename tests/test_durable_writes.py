"""Durable state writes (found by the state-store review): order-path files are fsynced before and after the rename,
and the first-decision-wins approval file is never visible half-written."""
import os

import pytest

from goldbot.base import create_exclusive, write_atomic


def test_durable_write_fsyncs_the_file_and_the_directory(tmp_path, monkeypatch):
    calls: list[int] = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), real(fd))[1])
    write_atomic(tmp_path / "orders.json", '{"sent": {}}')
    assert (tmp_path / "orders.json").read_text() == '{"sent": {}}'
    assert len(calls) == (1 if os.name == "nt" else 2)                  # file, then directory
    assert not list(tmp_path.glob(".*tmp"))
    calls.clear()
    write_atomic(tmp_path / "engine.json", "{}", durable=False)          # heartbeat: no fsync
    assert calls == []


def test_a_failed_write_keeps_the_previous_content(tmp_path, monkeypatch):
    path = tmp_path / "risk.json"
    write_atomic(path, "old")
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        write_atomic(path, "new")
    assert path.read_text() == "old" and not list(tmp_path.glob(".*tmp"))


def test_the_first_decision_wins_and_is_never_empty(tmp_path, monkeypatch):
    path = tmp_path / "decisions" / "p1.json"
    assert create_exclusive(path, '{"approve": true}')
    assert not create_exclusive(path, '{"approve": false}')
    assert path.read_text() == '{"approve": true}'
    other = tmp_path / "decisions" / "p2.json"
    monkeypatch.setattr(os, "link", lambda *a: (_ for _ in ()).throw(OSError("crash before the link")))
    with pytest.raises(OSError):
        create_exclusive(other, '{"approve": true}')
    assert not other.exists() and not list(other.parent.glob(".*tmp"))   # nothing half-written blocks a retry
