"""Multi-instrument plan (lane L3, engine): "XAUUSD behaviour byte-identical (golden/replay tests)". A four-day
XAUUSD replay through the engine (paper broker, auto mode, a 15m and a 1h agent with exit policies) is pinned against
tests/data/mi_xauusd_replay_golden.json, captured from main BEFORE the SymbolBook change: every decision row (wall-clock
stamps removed), every paper deal, every open trade's stop and target and the account state the gate reads. The golden
is rewritten only for an intended change of XAUUSD behaviour, never to make the multi-instrument change pass:
GOLDBOT_WRITE_MI_GOLDEN=1 pytest tests/test_mi_xauusd_replay_golden.py; the trading-safety reviewer signs any change."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter

pytestmark = pytest.mark.integration

GOLDEN = Path(__file__).parent / "data" / "mi_xauusd_replay_golden.json"
WRITE = os.environ.get("GOLDBOT_WRITE_MI_GOLDEN") == "1"
VOLATILE = {"ts", "error"}          # wall-clock stamps and exception reprs (object addresses) are not behaviour


def _canon(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _canon(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0])) if k not in VOLATILE}
    if isinstance(x, (list, tuple)):
        return [_canon(v) for v in x]
    if isinstance(x, float):
        return round(x, 9)
    if isinstance(x, pd.Timestamp):
        return x.isoformat()
    return x


def replay(tmp_path: Path) -> dict[str, Any]:
    ticks = synthetic_ticks("2025-03-03", "2025-03-07", ticks_per_minute=1, seed=5)
    pb = PaperBroker(equity=10_000)
    session = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    trend = SPECIALISTS["trend"](adx_min=0.0, pullback_atr=5.0)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path),
                              owner_user_id=111),
                 pb, [session, trend], {"session_open": ConstantModel(p=0.65), "trend": ConstantModel(p=0.66)},
                 ApprovalCenter({111}))
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
    deals = pb.deals_since(pd.Timestamp("2025-01-01", tz="UTC"))
    return _canon(json.loads(json.dumps({
        "decisions": eng.decisions,
        "deals": json.loads(deals.to_json(orient="records", date_format="iso")),
        "open": {str(k): {"sl": t.sl, "tp": t.tp, "lots": t.lots, "agent": t.agent_id} for k, t in eng.open.items()},
        "state": {"equity": eng.state.equity, "open_positions": eng.state.open_positions,
                  "open_notional": eng.state.open_notional, "spread_points": eng.state.spread_points,
                  "open_risk": eng.state.open_risk},
    }, default=str)))


def test_xauusd_replay_is_identical_to_main(tmp_path):
    got = replay(tmp_path)
    if WRITE:
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
    want = json.loads(GOLDEN.read_text())
    assert got["deals"], "the replay must trade, or it pins nothing"
    assert got == want
