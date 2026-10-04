"""The scheduler's jobs end to end against a synthetic store: nightly cost table + classifier from logged ticks and
fills, the Saturday retrain's challenger -> shadow -> promotion cycle, and one bounded research month."""
import json
from pathlib import Path

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.quality import check_bars
from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.execution.costs import CostTable
from goldbot.ops.accounts import Account
from goldbot.ops.jobs import (
    JobContext,
    build_scheduler,
    label_grid,
    monthly_research,
    nightly_costs,
    saturday_retrain,
)
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.promotion import PerfStats
from goldbot.research.registry import TrialRegistry

pytestmark = pytest.mark.integration

ACC = Account(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=None, terminal_path="",
              server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100, enabled=True)


@pytest.fixture(scope="module")
def bars_store(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("data")
    store = Store(root)
    b1, _ = check_bars(ticks_to_1m(synthetic_ticks("2022-10-01", "2025-10-01", ticks_per_minute=1, seed=11)))
    store.append("bars_1m", b1, source="synthetic")
    for tf in ("15m", "1h", "1d"):
        store.append(f"bars_{tf}", resample_bars(b1, tf), source="synthetic")
    return root


def _ctx(root: Path, tmp_path: Path, **research) -> JobContext:
    s = load_settings()
    if research:
        s = s.model_copy(update={"research": s.research.model_copy(update=research)})
    return JobContext(settings=s, store=Store(root), state_dir=tmp_path, models=ModelRegistry(tmp_path / "models"),
                      trials=TrialRegistry(tmp_path / "trials.jsonl"), accounts=[ACC])


def test_nightly_costs_from_logged_ticks_and_fills(tmp_path):
    store = Store(tmp_path / "data")
    ticks = synthetic_ticks("2025-09-22", "2025-09-27", ticks_per_minute=4, seed=3)
    store.append("ticks", ticks[["ts_utc", "bid", "ask"]], source="icm-demo", dedupe=False)
    fills = pd.DataFrame({"ts_utc": pd.date_range("2025-09-23 09:00", periods=60, freq="3min", tz="UTC"),
                          "client_order_id": [f"o{i}" for i in range(60)], "side": 1, "requested": 2400.0, "filled": 2400.04,
                          "order_type": "market", "commission": 0.0})
    store.append("fills", fills, source="icm-demo", dedupe=False)
    ctx = _ctx(tmp_path / "data", tmp_path)
    friday = pd.Timestamp("2025-09-26 23:10", tz="UTC")
    out = nightly_costs(ctx, friday)
    row = out["icm-demo"]
    assert row["ticks"] == len(ticks) and row["fills"] == 60
    table = CostTable.load(tmp_path / "costs_icm-demo.json")
    assert table is not None and set(table.spread) >= {"london", "newyork"}
    assert not table.slippage["london:market"].from_prior and table.slippage["london:market"].mean == pytest.approx(0.04)
    assert table.commission_per_lot_side_usd == 3.5
    # Friday's run also classifies the account (persisted for the engine and the dashboard)
    assert row["account_class"] in {"raw", "standard", "unknown"}
    assert json.loads((tmp_path / "classifier_icm-demo.json").read_text())["class"] == row["account_class"]


def test_retrain_challenger_shadow_promotion_cycle(bars_store, tmp_path):
    ctx = _ctx(bars_store, tmp_path)
    sat = pd.Timestamp("2025-09-27 06:00", tz="UTC")
    first = saturday_retrain(ctx, sat)["session_open"]
    version = first["retrain"]["challenger"]
    assert ctx.models.get(version).status == "challenger"
    # a challenger in shadow blocks the next retrain from replacing it
    nxt = saturday_retrain(ctx, sat + pd.Timedelta(weeks=1))["session_open"]
    assert nxt["retrain"].startswith("skipped") and nxt["challengers"][0]["action"] == "waiting"
    # the shadow book reports a record that passes every gate -> automatic promotion; the engine can load it
    bt = PerfStats.model_validate(ctx.models.get(version).backtest)
    good = PerfStats(n_trades=max(bt.n_trades, 60), sharpe_ann=max(bt.sharpe_ann, 1.0), hit_rate=bt.hit_rate,
                     max_dd=bt.max_dd * 0.5, trades_per_week=bt.trades_per_week, weeks=6.0)
    (tmp_path / f"shadow_{version}.json").write_text(good.model_dump_json())
    third = saturday_retrain(ctx, sat + pd.Timedelta(weeks=6))["session_open"]
    assert third["challengers"][0] == {"version": version, "action": "promoted"}
    assert ModelRegistry(tmp_path / "models").champion_models()["session_open"].feature_names
    assert "challenger" in third["retrain"]                 # a fresh challenger starts its own shadow period


def test_monthly_research_is_bounded_and_counted(bars_store, tmp_path):
    ctx = _ctx(bars_store, tmp_path, trial_budget_per_month=2)
    out = monthly_research(ctx, pd.Timestamp("2025-09-07 08:00", tz="UTC"))
    assert out["session_open"]["trials"] == 2 and ctx.trials.n_trials == 2
    report = Path(out["report"]).read_text()
    assert "Research loop 2025-09" in report and report.count("\n| ") >= 3


def test_label_grid_is_the_26_neighbours_of_the_base():
    grid = label_grid({"target_atr": 1.5, "stop_atr": 1.0, "max_bars": 16}, 0.25)
    assert len(grid) == 26 and {"target_atr": 1.5, "stop_atr": 1.0, "max_bars": 16} not in grid
    assert {g["max_bars"] for g in grid} == {12, 16, 20}


def test_build_scheduler_registers_every_job(tmp_path):
    ctx = _ctx(tmp_path / "data", tmp_path)
    sch = build_scheduler(ctx, clock=lambda: pd.Timestamp("2026-10-02 12:00", tz="UTC"))
    nxt = {k: v["next_slot"] for k, v in sch.status()["jobs"].items()}
    assert nxt == {"nightly_costs": "2026-10-02T23:10:00+00:00", "saturday_retrain": "2026-10-03T06:00:00+00:00",
                   "model_watch": "2026-10-02T23:30:00+00:00", "monthly_research": "2026-10-04T08:00:00+00:00"}
