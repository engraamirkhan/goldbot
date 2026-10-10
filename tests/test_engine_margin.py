"""R8 engine wiring: both RiskGate checks (at proposal and at the approval re-check) get the broker's margin source
(`broker_margin(broker, symbol)`), and a broker fault falls back to the 1:20 figure, never a smaller margin."""
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
from tests.test_cov_engine import OWNER, T0, _pend, _tick


def _spy(eng: Engine) -> list[Any]:
    seen: list[Any] = []
    real = eng.gate.check

    def spy(intent: Any, st: Any, now_utc: Any = None, margin_required: Any = None) -> Any:
        gd = real(intent, st, now_utc, margin_required=margin_required)
        seen.append((margin_required, gd))
        return gd

    eng.gate.check = spy                                                    # type: ignore[method-assign]
    return seen


def _engine(tmp_path: Path, margin: Any) -> tuple[Engine, ApprovalCenter, list[Any]]:
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(T0))
    pb.margin_required = margin                                            # type: ignore[method-assign]
    center = ApprovalCenter({OWNER})
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=OWNER),
                 pb, [], {}, center)
    eng.bars_1m = pd.DataFrame({"ts_utc": [T0 - pd.Timedelta(minutes=1)], "visible_at": [T0]})   # fresh data
    return eng, center, _spy(eng)


def test_the_engine_passes_the_broker_margin_to_the_gate_at_approval(tmp_path):
    calls: list[tuple[str, int, float, float]] = []

    def margin(symbol: str, side: int, lots: float, price: float) -> float:
        calls.append((symbol, side, lots, price))
        return lots * 100 * price / 5.0                                     # the broker is stricter than 1:20

    eng, center, seen = _engine(tmp_path, margin)
    _pend(eng, center, "icm-demo-9-a")
    center.decide("icm-demo-9-a", OWNER, True)
    [(fn, gd)] = seen
    assert fn is not None and calls and calls[-1][0] == "XAUUSD"
    _, _, lots, price = calls[-1]
    assert gd.margin_needed == pytest.approx(lots * 100 * price / 5.0) and gd.margin_note.startswith("broker")


def test_a_broker_margin_fault_at_approval_falls_back_to_one_to_twenty_never_less(tmp_path):
    lots_asked: list[float] = []

    def margin(symbol: str, side: int, lots: float, price: float) -> float:
        lots_asked.append(lots)
        raise RuntimeError("terminal down")

    eng, center, seen = _engine(tmp_path, margin)
    _pend(eng, center, "icm-demo-9-b")
    center.decide("icm-demo-9-b", OWNER, True)
    [(fn, gd)] = seen
    assert fn is not None and lots_asked and gd.margin_note.startswith("fallback 1:20")
    assert gd.margin_needed == pytest.approx(lots_asked[-1] * 100 * 2400.2 / 20.0)


@pytest.mark.integration
def test_the_engine_passes_the_broker_margin_on_every_proposal_and_approval_check(tmp_path):
    pb = PaperBroker(equity=10_000)
    asked: list[float] = []

    def margin(symbol: str, side: int, lots: float, price: float) -> float:
        asked.append(lots)
        return lots * 100 * price / 20.0

    pb.margin_required = margin                                            # type: ignore[method-assign]
    center = ApprovalCenter({111})
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="propose",
                              state_dir=str(tmp_path), owner_user_id=111),
                 pb, [spec], {"session_open": ConstantModel(p=0.65)}, center)
    seen = _spy(eng)
    ticks = synthetic_ticks("2025-03-03", "2025-03-06", ticks_per_minute=1, seed=5)
    approvals = 0
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
        for pid in list(center.pending):
            center.decide(pid, 111, True)
            approvals += 1
    assert approvals >= 1 and len(seen) >= 2 * approvals                   # one check at proposal, one at approval
    assert all(fn is not None for fn, _ in seen) and asked
