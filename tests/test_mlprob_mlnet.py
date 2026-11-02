"""MLNET point-in-time features: momentum uses only trades closed before the decision; consensus excludes the rule;
the walk-forward never trains on a trade that exits at or after the scored year."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "mlprob"))
import mlnet_pool as P  # noqa: E402
import mlnet_select as S  # noqa: E402

H = P.E.HOUR


def test_momentum_uses_only_closed_trades() -> None:
    tr = pd.DataFrame({"rule": ["a"] * 6, "t_exit": np.arange(1, 7) * H, "R": [1.0, 2, 3, 4, 5, 100]})
    m = P.momentum(tr, np.array([6 * H]), np.array(["a"]), k=3, min_n=2)   # the trade exiting AT 6h is still open
    assert m[0] == (3 + 4 + 5) / 3
    assert np.isnan(P.momentum(tr, np.array([2 * H]), np.array(["a"]), k=3, min_n=2)[0])


def test_momentum_is_per_rule() -> None:
    tr = pd.DataFrame({"rule": ["a", "b", "a"], "t_exit": [H, 2 * H, 3 * H], "R": [1.0, -9.0, 3.0]})
    m = P.momentum(tr, np.array([10 * H, 10 * H]), np.array(["a", "b"]), k=5, min_n=1)
    assert list(m) == [2.0, -9.0]


def test_consensus_window_and_self_exclusion() -> None:
    sig = pd.DataFrame({"dec": [0, 10 * H, 30 * H, 30 * H, 40 * H], "side": [1, 1, -1, 1, 1],
                        "rule": ["b", "b", "b", "a", "c"]})
    same, opp = P.consensus(sig, np.array([30 * H]), np.array([1]), np.array(["a"]))
    # (6h, 30h]: b@10h same, b@30h opposite; b@0 outside, a's own signal and c@40h (future) excluded
    assert same[0] == 1 and opp[0] == 1


def _pool(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = np.sort(rng.integers(S.ts(2010), S.ts(2014), n))
    d = pd.DataFrame({c: rng.normal(size=n) for c in S.NUM})
    d["rule"] = rng.choice(S.RULES, n)
    d["t_fill"], d["t_exit"] = t, t + 30 * H
    d["R"] = rng.normal(size=n)
    return d


def test_walk_forward_trains_only_on_closed_trades(monkeypatch: object) -> None:
    pool = _pool(3000, 0)
    seen: list[tuple[int, int]] = []
    orig = S.Fitted.__init__

    def spy(self: S.Fitted, train: pd.DataFrame, shuffle: bool = False) -> None:
        seen.append((int(train.t_exit.max()), len(train)))
        orig(self, train, shuffle)
    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(S.Fitted, "__init__", spy)
    try:
        sc = S.walk_forward(pool, pool, [2012, 2013])
    finally:
        mp.undo()
    assert [m < S.ts(y) for (m, _), y in zip(seen, [2012, 2013], strict=True)] == [True, True]
    assert sc.t_fill.min() >= S.ts(2012) and {"MLNET-V1", "MLNET-V2", "MLNET-V3", "MLNET-V4"} <= set(sc.columns)


def test_cluster_t_matches_plain_t_for_singleton_clusters() -> None:
    r = np.random.default_rng(1).normal(0.1, 1, 500)
    tc = S.cl_t(r, np.arange(500))
    assert abs(tc - S.plain_t(r)) / abs(S.plain_t(r)) < 0.01
