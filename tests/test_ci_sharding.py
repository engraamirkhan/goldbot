"""CI splits the unit and integration suites across matrices by GOLDBOT_SHARD=i/n (tests/conftest.py)."""
import json
import os
import subprocess
import sys

from tests.conftest import WEIGHTS_FILE, load_shard_weights, shard_assignment, shard_of


def test_every_test_id_lands_in_exactly_one_shard():
    ids = [f"tests/test_x{i % 7}.py::test_{i}[p{i % 3}]" for i in range(500)]
    for count in (2, 3, 4):
        owners = [[s for s in range(count) if shard_of(i, count) == s] for i in ids]
        assert all(len(o) == 1 for o in owners)
        assert {o[0] for o in owners} == set(range(count))


def _collect(shard: str | None) -> set[str]:
    # CI exports GOLDBOT_SHARD for the whole job; the unsharded baseline must not inherit it
    env = {k: v for k, v in os.environ.items() if k != "GOLDBOT_SHARD"}
    if shard:
        env["GOLDBOT_SHARD"] = shard
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


# --- weighted sharding: the integration suite is a few long engine replays and many short tests; a plain hash put
# 336 s in one of six shards and 15 s in another (local --durations, 2026-10-18). Known-heavy tests are spread first.

def test_weighted_assignment_is_a_partition_and_deterministic():
    ids = [f"tests/test_x{i % 7}.py::test_{i}" for i in range(300)]
    weights = {ids[i]: float(100 - i) for i in range(0, 60, 3)}
    for count in (1, 2, 4, 6):
        a = shard_assignment(ids, count, weights)
        assert set(a) == set(ids) and set(a.values()) <= set(range(count))
        assert a == shard_assignment(list(reversed(ids)), count, weights)   # collection order never changes it


def test_weighted_assignment_balances_the_heavy_tests():
    sizes = [103, 63, 58, 52, 50, 49, 47, 40, 39, 39, 38, 37]
    heavy = {f"tests/test_engine.py::test_{i}": float(w) for i, w in enumerate(sizes)}
    a = shard_assignment(list(heavy), 4, heavy)
    loads = [sum(w for n, w in heavy.items() if a[n] == s) for s in range(4)]
    assert max(loads) - min(loads) <= 40     # LPT: within the largest remaining item; the hash gave a 2.3x spread


def test_unweighted_tests_keep_their_hash_shard():
    ids = [f"tests/test_y.py::test_{i}" for i in range(50)]
    a = shard_assignment(ids, 4, {"tests/test_other.py::test_heavy": 99.0})   # a stale weight is simply unused
    assert all(a[n] == shard_of(n, 4) for n in ids)


def test_the_committed_weights_file_is_valid():
    data = json.loads(WEIGHTS_FILE.read_text())
    assert data["seconds"] and all("::" in k and v > 0 for k, v in data["seconds"].items())
    assert data["unit_seconds"] and all("::" in k and v > 0 for k, v in data["unit_seconds"].items())
    assert load_shard_weights() == {**data["unit_seconds"], **data["seconds"]}
