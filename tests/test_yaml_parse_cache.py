"""goldbot.config `safe_load_cached`: a config YAML is parsed once per file version, not once per read. The Telegram
service's health pass (every 60 s on the 2-vCPU brain) re-parsed config/exploration_arms.yaml once per exploring
account and config/accounts.yaml about eight times, and the strategy catalogue (pnl_basis, through the trade ledger)
parsed the arm spec, labels, reads and accounts files again: the largest share of a 14-17 s pass after #360 (health
pass cost, 2026-11-10). A changed file (edit, append, atomic replace) is parsed again before it is answered from, and
every caller gets its own copy, so no caller can change what the next one reads."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from goldbot import config
from goldbot.api import strategy_labels
from goldbot.engine.exploration import SPEC_PATH
from goldbot.ops import accounts
from goldbot.research import arms


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every YAML parse the cache makes, by the text's first line."""
    calls: list[str] = []
    real = yaml.safe_load

    def counted(text: str) -> object:
        calls.append(text.split("\n", 1)[0])
        return real(text)
    monkeypatch.setattr(config.yaml, "safe_load", counted)
    return calls


def test_an_unchanged_file_is_parsed_once_across_many_reads(tmp_path: Path, parses: list[str]):
    f = tmp_path / "a.yaml"
    f.write_text("# a\nx: [1, 2]\n")
    for _ in range(50):
        assert config.safe_load_cached(f) == {"x": [1, 2]}
    assert len(parses) == 1


def test_a_caller_mutating_its_result_never_changes_what_the_next_caller_reads(tmp_path: Path):
    f = tmp_path / "a.yaml"
    f.write_text("x: [1, 2]\ny: {z: 3}\n")
    got = config.safe_load_cached(f)
    got["x"].append(99)
    got["y"]["z"] = 0
    got["new"] = True
    assert config.safe_load_cached(f) == {"x": [1, 2], "y": {"z": 3}}


def test_an_edited_file_is_parsed_again_at_once(tmp_path: Path, parses: list[str]):
    f = tmp_path / "a.yaml"
    f.write_text("x: 1\n")
    assert config.safe_load_cached(f) == {"x": 1}
    with f.open("a") as fh:
        fh.write("y: 2\n")
    assert config.safe_load_cached(f) == {"x": 1, "y": 2}
    assert len(parses) == 2


def test_an_atomic_replace_of_the_same_size_and_mtime_is_still_seen(tmp_path: Path):
    f = tmp_path / "a.yaml"
    f.write_text("x: 1\n")
    st = f.stat()
    assert config.safe_load_cached(f) == {"x": 1}
    new = tmp_path / "new.yaml"
    new.write_text("x: 2\n")                          # same length
    os.utime(new, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(new, f)                                # a new inode
    assert config.safe_load_cached(f) == {"x": 2}


def test_a_missing_or_broken_file_raises_as_a_plain_parse_did(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        config.safe_load_cached(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("x: [1, 2\n")
    with pytest.raises(yaml.YAMLError):
        config.safe_load_cached(bad)
    bad.write_text("x: [1, 2]\n")                     # fixed: read at once, no remembered failure
    assert config.safe_load_cached(bad) == {"x": [1, 2]}


def test_the_arm_spec_is_parsed_once_across_health_passes(parses: list[str]):
    first = arms.load_arms(SPEC_PATH, check_hash=False)
    for _ in range(3):                                 # one load per exploring account, every pass
        assert arms.load_arms(SPEC_PATH, check_hash=False) == first
    assert len(parses) <= 1                            # 0 when an earlier test of this worker already parsed it


def test_the_accounts_file_is_parsed_once_across_reads(parses: list[str]):
    first = accounts.load_accounts(logins=False)
    for _ in range(8):
        assert accounts.load_accounts(logins=False) == first
    assert len(parses) <= 1


def test_the_strategy_catalogue_parses_its_input_files_once_across_passes(parses: list[str]):
    first = strategy_labels.load_catalog()
    n = len(parses)
    assert n <= 4                                      # labels, arm spec, reads, accounts: at most once each
    for _ in range(3):
        assert strategy_labels.load_catalog() == first
    assert len(parses) == n


def test_the_catalogue_reads_an_edited_input_file_on_the_next_load(tmp_path: Path, parses: list[str]):
    labels = tmp_path / "strategy_labels.yaml"
    labels.write_text(strategy_labels.LABELS_PATH.read_text())
    reads = tmp_path / "arm_reads.yaml"
    reads.write_text(strategy_labels.READS_PATH.read_text())
    before = strategy_labels.load_catalog(labels, reads_path=reads)
    n = len(parses)
    assert strategy_labels.load_catalog(labels, reads_path=reads) == before and len(parses) == n
    raw = yaml.load(labels.read_text(), Loader=yaml.SafeLoader)      # not counted: the fixture wraps safe_load
    family = next(iter(raw["families"]))
    raw["families"][family]["label"] = "Renamed in test"
    labels.write_text(yaml.safe_dump(raw))
    after = strategy_labels.load_catalog(labels, reads_path=reads)
    assert after.family_label(family) == "Renamed in test" != before.family_label(family)
    assert len(parses) == n + 1                        # the edited file alone is parsed again
