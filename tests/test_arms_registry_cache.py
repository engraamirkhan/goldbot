"""goldbot.research.arms `_registry_rows`: the research registry is parsed once per file version, not once per arm.
The telegram health pass stamps the qualification of ~1,100 arms, each reading the registry; re-parsing the whole
JSONL on every read cost ~45 s of one core per pass on the brain (telegram CPU attribution 2026-11-10). A changed file
(append, rewrite, replace) is read again, so no stale answer is ever served."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from goldbot.research import arms


def _row(h: str, family: str = "tsmom") -> str:
    return json.dumps({"config_hash": h, "family": family, "status": "evaluated"}) + "\n"


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = json.loads

    def loads(s: str | bytes, *a: object, **kw: object) -> object:
        calls.append(str(s))
        return real(s, *a, **kw)  # type: ignore[arg-type]
    monkeypatch.setattr(arms.json, "loads", loads)
    return calls


def test_the_registry_is_parsed_once_across_many_reads_of_an_unchanged_file(tmp_path: Path, counted: list[str]):
    reg = tmp_path / "research_registry.jsonl"
    reg.write_text(_row("a" * 12) + _row("b" * 12, arms.LEARNING_FAMILY))
    for _ in range(200):
        assert arms._registry_hashes(reg) == frozenset({"a" * 12, "b" * 12})
        assert arms._learning_hashes(reg) == frozenset({"b" * 12})
    assert len(counted) == 2                         # one parse of each of the 2 lines, for 400 reads


def test_an_appended_row_is_seen_at_once_and_parsed_again(tmp_path: Path, counted: list[str]):
    reg = tmp_path / "research_registry.jsonl"
    reg.write_text(_row("a" * 12))
    assert arms._registry_hashes(reg) == frozenset({"a" * 12})
    with reg.open("a") as f:
        f.write(_row("c" * 12, arms.LEARNING_FAMILY))
    assert arms._registry_hashes(reg) == frozenset({"a" * 12, "c" * 12})
    assert arms._learning_hashes(reg) == frozenset({"c" * 12})
    assert len(counted) == 1 + 2                     # first read, then the re-read of both lines; then cached


def test_a_rewrite_of_the_same_size_and_mtime_is_still_seen_when_the_file_is_replaced(tmp_path: Path):
    reg = tmp_path / "research_registry.jsonl"
    reg.write_text(_row("a" * 12))
    st = reg.stat()
    assert arms._registry_hashes(reg) == frozenset({"a" * 12})
    new = tmp_path / "new.jsonl"
    new.write_text(_row("d" * 12))                   # same length
    os.utime(new, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(new, reg)                             # an atomic replace: a new inode
    assert arms._registry_hashes(reg) == frozenset({"d" * 12})


def test_a_missing_registry_is_still_none_and_a_later_file_is_read(tmp_path: Path):
    reg = tmp_path / "research_registry.jsonl"
    assert arms._registry_hashes(reg) is None
    assert arms._learning_hashes(reg) == frozenset()
    reg.write_text(_row("e" * 12))
    assert arms._registry_hashes(reg) == frozenset({"e" * 12})
