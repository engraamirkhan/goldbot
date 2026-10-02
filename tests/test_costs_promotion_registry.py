import numpy as np
import pandas as pd
import pytest

from goldbot.execution.costs import CostTable, build_cost_table, slippage_table, spread_table
from goldbot.research.model import MetaLabelModel
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.promotion import PerfStats, evaluate_promotion


def _ticks() -> pd.DataFrame:
    # Tuesday: asia 02:00 wide, london 09:00 tight, newyork 15:00 medium; one crossed quote that must be ignored
    rows = []
    for hh, spread in ((2, 0.40), (9, 0.10), (15, 0.20)):
        for k in range(100):
            t = pd.Timestamp(f"2025-03-04 {hh:02d}:00", tz="UTC") + pd.Timedelta(seconds=k)
            rows.append({"ts_utc": t, "bid": 2400.0, "ask": 2400.0 + spread + (0.02 if k % 10 == 0 else 0.0)})
    rows.append({"ts_utc": pd.Timestamp("2025-03-04 09:30", tz="UTC"), "bid": 2401.0, "ask": 2400.0})
    rows.append({"ts_utc": pd.Timestamp("2025-03-08 12:00", tz="UTC"), "bid": 2400.0, "ask": 2405.0})   # Saturday: closed
    return pd.DataFrame(rows)


def test_spread_table_by_session_ignores_crossed_and_closed_quotes():
    t = spread_table(_ticks())
    assert set(t) == {"asia", "london", "newyork"}
    assert t["london"].median == pytest.approx(0.10) and t["london"].n == 100
    assert t["asia"].median == pytest.approx(0.40) and 0.40 < t["asia"].p90 < 0.42   # 10% of quotes at 0.42


def _fills(n: int, session_hour: int, slip: float) -> pd.DataFrame:
    ts = [pd.Timestamp(f"2025-03-04 {session_hour:02d}:00", tz="UTC") + pd.Timedelta(minutes=i) for i in range(n)]
    side = np.where(np.arange(n) % 2 == 0, 1, -1)
    requested = np.full(n, 2400.0)
    return pd.DataFrame({"ts_utc": ts, "side": side, "requested": requested, "filled": requested + side * slip,
                         "order_type": "market"})


def test_slippage_keeps_the_prior_until_enough_fills_and_signs_by_side():
    few = slippage_table(_fills(10, 9, 0.01), prior_usd=0.15, min_fills=50)
    assert few["london:market"].mean == 0.15 and few["london:market"].from_prior and few["london:market"].n == 10
    many = slippage_table(_fills(60, 9, 0.03), prior_usd=0.15, min_fills=50)
    assert many["london:market"].mean == pytest.approx(0.03) and not many["london:market"].from_prior   # buys and sells both worse
    assert many["asia:market"].from_prior                                                              # no asia fills


def test_round_trip_and_no_spread_means_unknown():
    table = build_cost_table("icm-demo", _ticks(), _fills(60, 9, 0.03), commission_per_lot_side_usd=3.5, slippage_prior_usd=0.15)
    assert table.round_trip_usd_per_oz("london") == pytest.approx(0.10 + 2 * 0.03 + 0.07)
    assert table.round_trip_atr("london", 0.0) is None
    empty = build_cost_table("icm-demo", pd.DataFrame(columns=["ts_utc", "bid", "ask"]), pd.DataFrame(),
                             commission_per_lot_side_usd=3.5, slippage_prior_usd=0.15)
    assert empty.round_trip_usd_per_oz("london") is None and empty.notes


def test_cost_table_round_trips_through_json(tmp_path):
    table = build_cost_table("icm-demo", _ticks(), _fills(5, 9, 0.0), commission_per_lot_side_usd=3.0, slippage_prior_usd=0.15,
                             now=pd.Timestamp("2025-03-04 23:10", tz="UTC"))
    table.save(tmp_path / "c.json")
    back = CostTable.load(tmp_path / "c.json")
    assert back == table and back is not None and back.built_utc.tzinfo is not None


# --------------------------------------------------------------------------------------- promotion gates
BT = PerfStats(n_trades=300, sharpe_ann=1.4, hit_rate=0.45, max_dd=0.10, trades_per_week=4.0)


def _shadow(**kw) -> PerfStats:
    base = dict(n_trades=60, sharpe_ann=1.2, hit_rate=0.44, max_dd=0.08, trades_per_week=4.2, weeks=6.0)
    return PerfStats(**{**base, **kw})


def test_good_shadow_record_is_promoted():
    d = evaluate_promotion(BT, _shadow(), champion=BT)
    assert d.ready and d.promote and d.failed == []


@pytest.mark.parametrize("kw,gate", [
    (dict(weeks=3.0), "shadow_length"), (dict(n_trades=39), "shadow_length"),
    (dict(sharpe_ann=0.79), "sharpe_floor"),
    (dict(sharpe_ann=0.85), "sharpe_vs_backtest"),           # 0.55 below a 1.4 backtest
    (dict(hit_rate=0.30), "hit_rate"),                       # 2+ binomial SE below 0.45 at n=60
    (dict(max_dd=0.16), "drawdown"),                         # cap is 1.5 x 0.10
    (dict(trades_per_week=6.0), "turnover"),                 # 50% above the champion
])
def test_each_gate_blocks_promotion(kw, gate):
    d = evaluate_promotion(BT, _shadow(**kw), champion=BT)
    assert not d.promote and gate in d.failed
    assert d.ready == (gate != "shadow_length")


def test_turnover_gate_is_skipped_without_a_champion():
    d = evaluate_promotion(BT, _shadow(trades_per_week=20.0), champion=None)
    assert d.promote and "turnover" not in [c.name for c in d.checks]


# --------------------------------------------------------------------------------------- model registry
def _model(tag: str) -> MetaLabelModel:
    return MetaLabelModel(feature_names=["atr14", "adx14"], feature_version=f"f-{tag}")


def test_registry_lifecycle_and_restore(tmp_path):
    reg = ModelRegistry(tmp_path)
    t0 = pd.Timestamp("2026-10-03 06:00", tz="UTC")
    a = reg.add_challenger(_model("a"), family="session_open", agent_id="session_open-g0-x", backtest=BT.model_dump(), now=t0)
    assert reg.champion("session_open") is None and reg.champion_models() == {}
    reg.promote(a.version, now=t0)
    b = reg.add_challenger(_model("b"), family="session_open", agent_id="session_open-g0-x", backtest={}, now=t0 + pd.Timedelta(days=7))
    reg.promote(b.version)
    champ = reg.champion("session_open")
    assert champ is not None and champ.version == b.version and reg.get(a.version).status == "previous"
    # the registry survives a restart and loads the artefact it points at
    reloaded = ModelRegistry(tmp_path)
    assert reloaded.champion_models()["session_open"].feature_version == "f-b"
    assert reloaded.get(a.version).created_utc == t0
    # the new champion trips its alarm: the previous one comes back
    reloaded.restore_previous("session_open", "CUSUM alarm in first two weeks")
    champ = reloaded.champion("session_open")
    assert champ is not None and champ.version == a.version and reloaded.get(b.version).status == "retired"
    with pytest.raises(ValueError):
        reloaded.promote(b.version)                         # only challengers can be promoted


def test_tampered_artefact_is_refused(tmp_path):
    reg = ModelRegistry(tmp_path)
    e = reg.add_challenger(_model("a"), family="session_open", agent_id="x", backtest={})
    reg.promote(e.version)
    (tmp_path / e.artefact).write_bytes(b"not the model")
    with pytest.raises(ValueError, match="checksum"):
        reg.champion_models()
