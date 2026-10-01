import json
from pathlib import Path

import pandas as pd
import pytest

from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter

pytestmark = pytest.mark.integration


def _run(approval_mode: str, tmp_path: Path, days: int = 5):
    ticks = synthetic_ticks("2025-03-03", f"2025-03-{3 + days:02d}", ticks_per_minute=1, seed=5)
    pb = PaperBroker(equity=10_000)
    center = ApprovalCenter({111})
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)  # permissive for the test
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode=approval_mode, state_dir=str(tmp_path), owner_user_id=111),
                 pb, [spec], {"session_open": ConstantModel(p=0.65)}, center)
    decisions = []
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        decisions += eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
        # approve everything that gets proposed, like an owner tapping Approve
        if approval_mode == "propose":
            for pid in list(center.pending):
                center.decide(pid, 111, True)
    return eng, pb, center, decisions


def test_engine_auto_mode_places_orders_and_writes_state(tmp_path):
    eng, pb, center, decisions = _run("auto", tmp_path)
    executed = [d for d in decisions if d["action"] == "executed:auto"]
    assert executed, "expected at least one auto-executed trade on synthetic data"
    deals = pb.deals_since(pd.Timestamp("2025-01-01", tz="UTC"))
    assert (deals["type"] == "entry").sum() >= 1
    st = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert st["account"] == "icm-demo" and st["equity"] > 0
    # idempotency: no proposal id was sent twice
    ids = [d["proposal"] for d in decisions if d.get("proposal")]
    assert len(ids) == len(set(ids))


def test_engine_propose_mode_waits_for_approval(tmp_path):
    eng, pb, center, decisions = _run("propose", tmp_path)
    proposed = [d for d in decisions if d["action"] == "proposed"]
    assert proposed
    orders = [d for d in eng.decisions if d.get("action") == "order"]
    assert orders and all(o["ok"] for o in orders)
    assert center.veto_value()["approved"] == len(orders) or center.veto_value()["approved"] >= len(orders)
