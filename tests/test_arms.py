"""goldbot.research.arms: the hard holdout guard, per-era cost pricing, the exploration-arm loader and the prior."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research import arms

ROOT = Path(__file__).resolve().parents[1]


def test_holdout_guard_refuses_blocks_and_bars_past_the_end() -> None:
    arms.assert_block_allowed(pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2025-09-30", tz="UTC"))
    with pytest.raises(ValueError, match="holdout"):
        arms.assert_block_allowed(pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2025-10-01", tz="UTC"))
    with pytest.raises(ValueError, match="holdout"):
        arms.assert_block_allowed(pd.Timestamp("2025-10-02", tz="UTC"), pd.Timestamp("2025-12-31", tz="UTC"))
    ts = pd.date_range("2025-09-29", "2025-10-03", freq="1D", tz="UTC")
    bars = pd.DataFrame({"ts_utc": ts, "x": range(len(ts))})
    with pytest.raises(AssertionError, match="holdout"):
        arms.assert_no_holdout_rows(bars)
    cut = arms.cut_at_holdout(bars)
    assert cut["ts_utc"].max() < pd.Timestamp("2025-10-01", tz="UTC")
    arms.assert_no_holdout_rows(cut)


def test_cut_at_holdout_is_unit_safe() -> None:
    ts = pd.DatetimeIndex(pd.to_datetime(["2025-09-30 23:59:00", "2025-10-01 00:00:00"], utc=True)).as_unit("s")
    out = arms.cut_at_holdout(pd.DataFrame({"ts_utc": ts}))
    assert len(out) == 1


def test_blocks_cover_2015_to_2025_09_30_in_two_year_blocks() -> None:
    b = arms.blocks()
    assert b[0][0] == "2015-01-01" and b[-1][1] == "2025-09-30"
    assert all(s <= e for s, e in b) and len(b) == 6
    for (_, e), (s, _) in zip(b, b[1:], strict=False):
        assert pd.Timestamp(s) - pd.Timestamp(e) == pd.Timedelta(days=1)


def test_parse_block_rejects_garbage_and_holdout() -> None:
    assert arms.parse_block("2019-01-01:2020-12-31")[0] == pd.Timestamp("2019-01-01", tz="UTC")
    with pytest.raises(ValueError):
        arms.parse_block("2019-01-01")
    with pytest.raises(ValueError, match="holdout"):
        arms.parse_block("2025-01-01:2025-12-31")


def test_era_cost_is_priced_per_era_and_floors_the_spread() -> None:
    table = arms.load_era_costs(ROOT / "config" / "era_costs.yaml")
    ts = pd.DatetimeIndex(pd.to_datetime(["2015-06-01", "2024-06-01"], utc=True))
    cheap = arms.extra_cost_usd(ts, np.array([0.0, 0.0]), table)
    assert cheap[0] > 0 and cheap[1] > 0
    assert cheap[0] != cheap[1]                      # eras differ
    wide = arms.extra_cost_usd(ts, np.array([9.0, 9.0]), table)
    assert np.allclose(wide, [table.at(ts[0]).fixed_usd(), table.at(ts[1]).fixed_usd()])   # a wide bar spread: no top-up
    assert (cheap >= wide).all()


def test_era_table_must_cover_2015_and_be_ordered(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("eras:\n  - {from: 2016-01-01, spread_floor_usd: 0.3, commission_per_lot_side_usd: 3.5, slippage_usd: 0.1}\n")
    with pytest.raises(ValueError, match="2015"):
        arms.load_era_costs(p)


def test_shipped_arms_load_with_both_random_controls() -> None:
    loaded = arms.load_arms(ROOT / "config" / "exploration_arms.yaml")
    ids = {a.id for a in loaded}
    assert {"random_1m", "random_15m"} <= ids
    r = {a.id: a for a in loaded}
    assert r["random_1m"].timeframe == "1m" and r["random_15m"].timeframe == "15m"
    assert all(a.kind == "random" and a.seed is not None for a in (r["random_1m"], r["random_15m"]))


def test_arm_loader_rejects_bad_arms(tmp_path: Path) -> None:
    def load(text: str) -> list[arms.Arm]:
        p = tmp_path / "a.yaml"
        p.write_text(text)
        return arms.load_arms(p)

    with pytest.raises(ValueError, match="duplicate"):
        load("arms:\n- {id: a, kind: random, timeframe: 15m, seed: 1}\n- {id: a, kind: random, timeframe: 15m, seed: 2}\n")
    with pytest.raises(ValueError, match="seed"):
        load("arms:\n- {id: a, kind: random, timeframe: 15m}\n")
    with pytest.raises(ValueError, match="family"):
        load("arms:\n- {id: a, kind: specialist, timeframe: 15m, family: nope}\n")
    with pytest.raises(ValueError):
        load("arms:\n- {id: a, kind: random, timeframe: 7m, seed: 1}\n")
    ok = load("arms:\n- {id: s, kind: specialist, timeframe: 15m, family: session_open, config: {}}\n")
    assert ok[0].family == "session_open"


def test_random_candidates_are_seeded_and_both_sides() -> None:
    a = arms.Arm(id="r", kind="random", timeframe="15m", seed=7)
    n = 5000
    c1, c2 = arms.random_candidates(a, n), arms.random_candidates(a, n)
    pd.testing.assert_frame_equal(c1, c2)
    assert set(c1["side"]) == {-1, 1} and c1["idx"].max() < n
    other = arms.random_candidates(arms.Arm(id="r", kind="random", timeframe="15m", seed=8), n)
    assert not c1.equals(other)


def test_prior_uses_oos_blocks_halved_and_capped() -> None:
    def trades(year: int, n: int, r: float) -> pd.DataFrame:
        return pd.DataFrame({"ts_utc": pd.Timestamp(f"{year}-03-01", tz="UTC") + pd.to_timedelta(np.arange(n), "h"),
                             "net_r": r})
    df = pd.concat([trades(2016, 1000, 5.0), trades(2020, 40, 0.2), trades(2023, 40, 0.4)])
    p = arms.build_prior(df)
    assert p["n_oos"] == 80 and p["weight"] == 40.0 and p["mean_r"] == pytest.approx(0.3)   # 2016 is in-sample
    big = arms.build_prior(pd.concat([trades(2021, 400, 0.1)]))
    assert big["weight"] == 50.0
    empty = arms.build_prior(trades(2016, 10, 1.0))
    assert empty["weight"] == 0.0 and empty["mean_r"] == 0.0
