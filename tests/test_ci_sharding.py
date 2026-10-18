"""CI splits the unit suite across a matrix by GOLDBOT_SHARD=i/n (tests/conftest.py)."""
import os
import subprocess
import sys

from tests.conftest import shard_of


def test_every_test_id_lands_in_exactly_one_shard():
    ids = [f"tests/test_x{i % 7}.py::test_{i}[p{i % 3}]" for i in range(500)]
    for count in (2, 3, 4):
        owners = [[s for s in range(count) if shard_of(i, count) == s] for i in ids]
        assert all(len(o) == 1 for o in owners)
        assert {o[0] for o in owners} == set(range(count))


def _collect(shard: str | None) -> set[str]:
    env = {**os.environ, **({"GOLDBOT_SHARD": shard} if shard else {})}
    cmd = [sys.executable, "-m", "pytest", "tests/test_min_lot_exception.py", "tests/test_data_pipeline_integration.py",
           "--collect-only", "-q", "-p", "no:randomly", "-p", "no:cacheprovider"]
    out = subprocess.run(cmd, capture_output=True, text=True, env=env).stdout
    return {line for line in out.splitlines() if "::" in line}


def test_shards_of_real_test_files_partition_them_with_nothing_lost_or_duplicated():
    whole = _collect(None)
    parts = [_collect(f"{i}/3") for i in range(3)]
    assert len(whole) >= 5
    assert set().union(*parts) == whole
    assert sum(len(p) for p in parts) == len(whole)
