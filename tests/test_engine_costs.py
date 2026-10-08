"""The engine prices each candidate with the nightly cost table for the current session, and falls back to its
configured cost when there is no table or the table is unreadable."""
import pandas as pd

from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.execution.costs import CostTable, SlippageStat, SpreadStat
from goldbot.execution.paper import PaperBroker


def _engine(tmp_path) -> Engine:
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), cost_atr=0.10),
                  PaperBroker(), [], {"session_open": ConstantModel(p=0.6)})


def _table() -> CostTable:
    return CostTable(account_id="icm-demo", built_utc=pd.Timestamp("2025-03-03", tz="UTC"),
                     spread={"london": SpreadStat(median=0.12, p90=0.2, n=1000), "asia": SpreadStat(median=0.30, p90=0.5, n=1000)},
                     slippage={"london:market": SlippageStat(mean=0.05, n=60, from_prior=False)},
                     commission_per_lot_side_usd=3.5, slippage_prior_usd=0.15)


def test_cost_comes_from_the_table_by_session(tmp_path):
    eng = _engine(tmp_path)
    assert eng._cost_atr(2.0, pd.Timestamp("2025-03-04 09:00", tz="UTC")) == 0.10      # no table yet: fallback
    _table().save(tmp_path / "costs_icm-demo.json")
    london = eng._cost_atr(2.0, pd.Timestamp("2025-03-04 09:00", tz="UTC"))
    # 0.12 spread + 2 x 0.05 slippage + 2 x 3.5 / 100 commission = 0.29 $/oz over a 2.0 ATR
    assert abs(london - 0.29 / 2.0) < 1e-9
    asia = eng._cost_atr(2.0, pd.Timestamp("2025-03-04 02:00", tz="UTC"))
    # asia has no measured slippage: the 0.15 prior applies
    assert abs(asia - (0.30 + 0.30 + 0.07) / 2.0) < 1e-9
    # newyork has no spread row: borrows the widest measured session (asia)
    assert eng._cost_atr(2.0, pd.Timestamp("2025-03-04 15:00", tz="UTC")) == asia


def test_threshold_cost_excludes_the_spread_the_labels_already_pay(tmp_path):
    eng = _engine(tmp_path)
    ts = pd.Timestamp("2025-03-04 09:00", tz="UTC")
    assert eng._cost_atr(2.0, ts, ex_spread=True) == 0.10                               # no table: full fallback
    _table().save(tmp_path / "costs_icm-demo.json")
    # london: 2 x 0.05 slippage + 2 x 3.5 / 100 commission = 0.17 $/oz (the 0.12 spread is in the labels already)
    assert abs(eng._cost_atr(2.0, ts, ex_spread=True) - 0.17 / 2.0) < 1e-9
    assert abs(eng._cost_atr(2.0, ts) - eng._cost_atr(2.0, ts, ex_spread=True) - 0.12 / 2.0) < 1e-9   # gate: full
    assert _table().round_trip_ex_spread_atr("london", 0.0) is None


def test_unreadable_table_falls_back(tmp_path):
    eng = _engine(tmp_path)
    (tmp_path / "costs_icm-demo.json").write_text("{not json")
    assert eng._cost_atr(2.0, pd.Timestamp("2025-03-04 09:00", tz="UTC")) == 0.10


def test_account_class_is_reported_from_the_classifier_state(tmp_path):
    eng = _engine(tmp_path)
    assert eng._account_class() == "unknown"
    (tmp_path / "classifier_icm-demo.json").write_text('{"class": "raw", "pending": null, "runs": []}')
    assert eng._account_class() == "raw"
