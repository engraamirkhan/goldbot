"""scripts/run_arm_backtest.py on synthetic 1m bars: the random control runs end to end, trades stay inside the block,
and the loader refuses a block that reaches the holdout."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from goldbot.research import arms as A
from tests.test_count_owed_events import _bars_1m

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_arm_backtest", ROOT / "scripts" / "run_arm_backtest.py")
assert spec and spec.loader
rab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rab)


def test_random_15m_dry_run_net_of_era_costs() -> None:
    b1 = _bars_1m("2023-01-02", "2023-05-01")
    arm = next(a for a in A.load_arms(ROOT / "config" / "exploration_arms.yaml") if a.id == "random_15m")
    start, end = pd.Timestamp("2023-02-01", tz="UTC"), pd.Timestamp("2023-04-15", tz="UTC")
    trades, s = rab.run_arm(b1, arm, start, end, A.load_era_costs(ROOT / "config" / "era_costs.yaml"))
    assert s["n_trades"] > 10 and s["n_trades"] == len(trades)
    assert trades["ts_utc"].min() >= start and trades["ts_utc"].max() < end + pd.Timedelta(days=1)
    assert (trades["extra_cost_usd"] > 0).all() and (trades["net_r"] < trades["gross_r"]).all()
    again, _ = rab.run_arm(b1, arm, start, end, A.load_era_costs(ROOT / "config" / "era_costs.yaml"))
    pd.testing.assert_frame_equal(trades, again)          # seeded: reproducible


def test_loader_refuses_a_block_in_the_holdout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="holdout"):
        rab.load_block_bars(tmp_path, pd.Timestamp("2025-06-01", tz="UTC"), pd.Timestamp("2025-10-31", tz="UTC"))


def test_run_arm_asserts_on_holdout_bars() -> None:
    b1 = _bars_1m("2025-09-28", "2025-10-03")
    arm = A.Arm(id="r", kind="random", timeframe="15m", seed=1)
    with pytest.raises(AssertionError, match="holdout"):
        rab.run_arm(b1, arm, pd.Timestamp("2025-09-28", tz="UTC"), pd.Timestamp("2025-09-30", tz="UTC"),
                    A.load_era_costs(ROOT / "config" / "era_costs.yaml"))
