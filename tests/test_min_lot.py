"""Minimum-lot feasibility (playbook G-7, BACKLOG item 22): the smallest equity at which the planned stop can be
sized at the minimum lot, and the risk the minimum lot really takes at a given equity."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.research.min_lot import (
    Feasibility,
    LotSpec,
    feasibility_from_bars,
    main,
    min_lot_feasibility,
)
from goldbot.risk.gate import AccountState, Intent, RiskGate


def test_playbook_example_one_hundred_dollar_stop_at_half_percent():
    # 0.01 lot = 1 oz: a $75/oz stop risks $75; within the gate's 1.2x tolerance at 0.5% that needs $12,500
    f = min_lot_feasibility(price=4000.0, stop_distance=75.0, risk_fraction=0.005)
    assert f.min_lot_risk_usd == pytest.approx(75.0)
    assert f.min_equity_strict == pytest.approx(15_000.0)
    assert f.min_equity_gate == pytest.approx(12_500.0)
    tiny = min_lot_feasibility(price=4000.0, stop_distance=170.0, risk_fraction=0.001)
    assert tiny.min_equity_gate == pytest.approx(170 / 0.0012)          # ~ $141.7k, the playbook's upper figure


def test_stop_from_atr_times_the_stop_multiple():
    f = min_lot_feasibility(price=4000.0, atr_usd=60.0, stop_atr=1.5, risk_fraction=0.005)
    assert f.stop_distance == pytest.approx(90.0) and f.atr_usd == 60.0 and f.stop_atr == 1.5


def test_actual_risk_at_min_lot_for_a_given_equity():
    f = min_lot_feasibility(price=4000.0, stop_distance=100.0, risk_fraction=0.005, equity=10_000.0)
    assert f.lots_raw == pytest.approx(0.005)
    assert f.lots == 0.01
    assert f.actual_risk_fraction == pytest.approx(0.01)                 # twice the target
    assert not f.allowed and f.reason == "min_lot_exceeds_risk"


def test_a_large_account_is_sized_down_to_the_lot_step():
    f = min_lot_feasibility(price=4000.0, stop_distance=10.0, risk_fraction=0.005, equity=10_000.0)
    assert f.lots_raw == pytest.approx(0.05) and f.lots == pytest.approx(0.05) and f.allowed
    g = min_lot_feasibility(price=4000.0, stop_distance=7.0, risk_fraction=0.005, equity=10_000.0)
    assert g.lots == pytest.approx(0.07)                                 # 0.0714 rounded down
    assert g.actual_risk_fraction is not None and g.actual_risk_fraction <= 0.005


def test_feasibility_needs_a_stop_distance_or_atr_and_multiple():
    with pytest.raises(ValueError):
        min_lot_feasibility(price=4000.0, risk_fraction=0.005)


def _gate(equity: float, atr: float) -> Any:
    st = AccountState(equity=equity, balance_closed_hwm=equity, day_start_equity=equity, week_start_equity=equity,
                      open_positions=0, margin_used=0.0, last_tick_age_s=1.0, spread_points=25.0)
    it = Intent(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=atr, cost_atr=0.01,
                multiplier=1.0, price=2400.0)
    return RiskGate().check(it, st)


@pytest.mark.parametrize("equity", [5000.0, 9166.0, 9167.0, 9200.0, 12_000.0, 20_000.0])
def test_agrees_with_the_risk_gate_on_whether_the_minimum_lot_is_allowed(equity: float):
    d = _gate(equity, atr=55.0)
    f = min_lot_feasibility(price=2400.0, stop_distance=55.0, risk_fraction=0.005, equity=equity,
                            lot=LotSpec(volume_max=2.0))
    assert f.allowed == d.allowed
    if d.allowed:
        assert f.lots == pytest.approx(d.lots) and f.actual_risk_fraction == pytest.approx(d.risk_fraction)
    assert (equity >= f.min_equity_gate) == d.allowed


def _bars(n: int, close: float, rng_usd: float) -> pd.DataFrame:
    ts = pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC")
    c = np.full(n, close)
    return pd.DataFrame({"ts_utc": ts, "open": c, "high": c + rng_usd / 2, "low": c - rng_usd / 2, "close": c})


def test_feasibility_from_bars_uses_the_last_close_and_atr14():
    f = feasibility_from_bars(_bars(60, 4000.0, 80.0), stop_atr=1.5, risk_fraction=0.005)
    assert f.price == pytest.approx(4000.0)
    assert f.atr_usd == pytest.approx(80.0)                              # constant range: ATR = the range
    assert f.stop_distance == pytest.approx(120.0)
    assert f.as_of_utc == pd.Timestamp("2026-03-01", tz="UTC")


def test_feasibility_from_too_few_bars_is_refused():
    with pytest.raises(ValueError):
        feasibility_from_bars(_bars(5, 4000.0, 80.0), stop_atr=1.5, risk_fraction=0.005)


def test_cli_with_arguments(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    out = tmp_path / "f.json"
    assert main(["--price", "4000", "--stop-distance", "75", "--risk", "0.005", "--equity", "10000",
                 "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "12,500" in text
    rows = [Feasibility.model_validate(r) for r in __import__("json").loads(out.read_text())]
    assert rows[0].min_equity_gate == pytest.approx(12_500.0)


def test_cli_reads_bars_per_family_and_timeframe_from_the_store(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    from goldbot.data.store import Store
    store = Store(tmp_path)
    b = _bars(60, 4000.0, 80.0).assign(volume=1.0)
    store.append("bars_1d", b, source="dukascopy")
    assert main(["--data-root", str(tmp_path), "--timeframes", "1d", "--equity", "25000"]) == 0
    text = capsys.readouterr().out
    assert "tsmom" in text and "1d" in text
    assert main(["--data-root", str(tmp_path / "empty"), "--timeframes", "1d"]) == 2
    assert "no bars" in capsys.readouterr().err


def test_run_py_dispatches_both_reports():
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    a = subprocess.run([sys.executable, "-m", "goldbot.ops.run", "sizing-feasibility", "--price", "4000",
                        "--stop-distance", "75", "--risk", "0.005"], cwd=root, capture_output=True, text=True)
    assert a.returncode == 0 and "$12,500" in a.stdout
    b = subprocess.run([sys.executable, "-m", "goldbot.ops.run", "ruin", "--win-rate", "0.5", "--avg-win", "1.5",
                        "--avg-loss", "1", "--trades-per-week", "5", "--weeks", "8", "--horizons", "4",
                        "--paths", "100"], cwd=root, capture_output=True, text=True)
    assert b.returncode == 0 and "drawdown_stage2" in b.stdout
