"""The engine publishes its current key zones (goldbot/engine/key_zones.py) for the dashboard's price chart.

Design rule: display only. The zones never reach an order, a size or the RiskGate; a failure leaves the last zones and
never fails a bar close.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig, key_zones
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.features import key_levels
from goldbot.specialists import SPECIALISTS

BARS = ticks_to_1m(synthetic_ticks("2025-01-06", "2025-02-28", ticks_per_minute=1, seed=3))


def _engine(tmp_path: Path) -> Engine:
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path)),
                  PaperBroker(equity=10_000), [spec], {"session_open": ConstantModel(p=0.65)})


def _close(eng: Engine, n: int) -> tuple[pd.Timestamp, list[dict]]:
    bars = BARS.iloc[:n].reset_index(drop=True)
    eng.bars_1m = bars
    close_ts = pd.Timestamp(bars["visible_at"].iloc[-1])
    last = bars.iloc[-1]
    tick = Tick(ts_utc=close_ts, bid=float(last["bid_close"]), ask=float(last["ask_close"]))
    setattr(eng, "_rebuild_bars", lambda until=None: None)      # the bars are set above
    return close_ts, eng.on_bar_close(close_ts, tick)


def _n_at_15m_close() -> int:
    vis = pd.to_datetime(BARS["visible_at"], utc=True)
    on = vis.dt.minute.isin([0, 15, 30, 45]).to_numpy()
    return int(pd.Series(on).to_numpy().nonzero()[0][-50]) + 1


def test_a_bar_close_publishes_the_nearest_three_zones_above_and_below_with_strength_and_timeframes(tmp_path):
    eng = _engine(tmp_path)
    close_ts, _ = _close(eng, _n_at_15m_close())
    state = json.loads(Path(tmp_path, "engine_icm-demo.json").read_text())
    kz = state["key_zones"]
    want = key_levels.zones_asof(eng.bars_1m, close_ts)
    above, below = want.nearest(3)
    assert [z["price"] for z in kz["above"]] == [round(z.price, 2) for z in above]
    assert [z["price"] for z in kz["below"]] == [round(z.price, 2) for z in below]
    assert len(kz["above"]) == 3 and len(kz["below"]) == 3
    z0 = kz["above"][0]
    assert set(z0) == {"price", "lo", "hi", "strength", "strength_pct", "tfs", "touches", "role", "flipped"}
    assert z0["tfs"] and set(z0["tfs"]) <= {"15m", "1h", "4h", "1d", "1w", "1M", "round"}
    assert kz["as_of"] == pd.Timestamp(close_ts).isoformat() and kz["history_days"] > 40


def test_zones_are_rebuilt_only_when_a_new_15m_bar_has_closed(monkeypatch):
    calls: list[pd.Timestamp] = []
    eng: Any = SimpleNamespace(key_zones=None, key_zones_at=None)

    def build(complete: pd.DataFrame, t: pd.Timestamp) -> dict[str, Any]:
        calls.append(t)
        return {"as_of": t.isoformat()}

    monkeypatch.setattr(key_zones, "build", build)
    t0 = pd.Timestamp("2025-02-03 10:15", tz="UTC")
    for m in range(0, 31):
        key_zones.update(eng, BARS, t0 + pd.Timedelta(minutes=m))
    assert calls == [t0, t0 + pd.Timedelta(minutes=15), t0 + pd.Timedelta(minutes=30)]


def test_a_zone_failure_keeps_the_last_zones_and_never_fails_the_bar_close_or_its_decisions(tmp_path, monkeypatch):
    n = _n_at_15m_close()
    ref = _engine(tmp_path / "ref")
    _, want = _close(ref, n)
    eng = _engine(tmp_path / "x")
    eng.key_zones = {"as_of": "kept"}

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("bad book")

    monkeypatch.setattr(key_zones, "zones_asof", boom)
    _, got = _close(eng, n)
    assert eng.key_zones == {"as_of": "kept"}
    assert [(d.get("agent_id"), d.get("action")) for d in got] == [(d.get("agent_id"), d.get("action")) for d in want]
    state = json.loads(Path(tmp_path, "x", "engine_icm-demo.json").read_text())
    assert state["key_zones"] == {"as_of": "kept"}


def test_no_bars_publish_no_zones():
    assert key_zones.build(BARS.iloc[:0], pd.Timestamp("2025-02-03", tz="UTC")) is None
