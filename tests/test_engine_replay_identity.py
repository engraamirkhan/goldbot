"""WS-4 (consolidated plan 2026-11-07): "Live tonight ONLY IF (a) the 7-day brain replay gives an identical decision set
for every existing arm". A seven-day replay (a weekend inside it: the Friday cut and the weekly open) of the engine with
exploration arms on 15m, 1h and 4h (random-bracket controls, the session-range and band-break controls) through
`Engine.on_tick` on the paper terminal is pinned against tests/data/engine_replay_identity_golden.json, captured from
cb3e52a4 BEFORE the 1d/1w change: every decision row (wall-clock stamps removed), every paper deal, every open trade.
Rewritten once for backlog 160 (open trades age below the bar-close floor): the replay starts cold, so its first
day's RB15 trades now time out after their 4 bars instead of riding to stop/target until the first decision frame.

The same replay with 1d and 1w arms added must give the existing arms the identical rows and deals, whether the 1d/1w
arms are dark (settings engine.timeframes_live, the default) or live with a long history to warm up from. The golden is
rewritten only for an intended change of the existing arms' behaviour, never to make a change pass:
GOLDBOT_WRITE_REPLAY_GOLDEN=1 pytest tests/test_engine_replay_identity.py; the trading-safety reviewer signs any change."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import Engine, EngineConfig
from goldbot.engine.exploration import Arm, ExplorationConfig, Pot
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.research.arms import Arm as SpecArm
from goldbot.risk import RiskLimits
from goldbot.telegram.approvals import ApprovalCenter

pytestmark = pytest.mark.integration

GOLDEN = Path(__file__).parent / "data" / "engine_replay_identity_golden.json"
WRITE = os.environ.get("GOLDBOT_WRITE_REPLAY_GOLDEN") == "1"
VOLATILE = {"ts", "error"}          # wall-clock stamps and exception reprs (object addresses) are not behaviour
START, END = "2025-03-03", "2025-03-12"    # Monday to the next Wednesday: a Friday cut and a weekly open inside
I0 = {"minutes": [0], "window_utc": ["07:00", "19:00"], "stop_atr": 1.5, "target_r": 2.0, "hold_bars": 4}


class _Terminal(PaperBroker):
    def account(self) -> AccountInfo:
        return super().account().model_copy(update={"trade_mode": "demo"})


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


def _control(arm_id: str, rule: str, tf: str, params: dict[str, Any], style: str = "intraday") -> Arm:
    spec = SpecArm(id=arm_id, rule=rule, timeframe=tf, params=params, max_open=2, role="control")  # type: ignore[arg-type]
    return Arm(spec=spec, style=style, control_share=0.1)


def existing_arms() -> list[Arm]:
    """The arms whose behaviour must not move: 15m, 1h and 4h, real rules (no test generators)."""
    return [_control("RB15", "random_bracket", "15m", I0), _control("RB1H", "random_bracket", "1h", I0),
            _control("RB4H", "random_bracket", "4h", {**I0, "hold_bars": 2}, style="swing"),
            _control("SRC15", "session_range_control", "15m",
                     {"sessions": ["asia_london", "london_ny"], "stop_atr": 1.5, "target_r": 3.0, "hold_bars": 16, "control": "GS1"}),
            _control("HBC15", "hurst_band_break_control", "15m",
                     {"bands": ["kc20_2.0atr", "bb20_2sd", "dc20"], "hurst_window": 128, "rank_window": 250,
                      "dedup_bars": 4, "stop_atr": 1.0, "hold_bars": 4, "min_stop_usd": 0.0, "max_stop_usd": 1e9})]


def replay(tmp_path: Path, arms: list[Arm], **cfg: Any) -> dict[str, Any]:
    ticks = synthetic_ticks(START, END, ticks_per_minute=1, seed=5)
    pots = {"intraday": Pot(gbp=500, max_open=20), "swing": Pot(gbp=500, max_open=20)}
    ex_cfg = ExplorationConfig(enabled=True, budget_share=0.9, max_open_total=40, pots=pots, arms=tuple(arms))
    pb = _Terminal(equity=10_000, adverse_slip_points=0.0)
    limits = RiskLimits(risk_per_trade=0.002, multiplier_bounds=(1.0, 1.0), max_positions=50, probe_only=True,
                        exploration_arms={a.arm_id: 400.0 for a in arms}, daily_loss_limit_abs=2_000.0)
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", approval_mode="auto",
                              declared_class="raw", state_dir=str(tmp_path), owner_user_id=111, probe_only=True,
                              exploration_config=ex_cfg, **cfg),
                 pb, [], {}, ApprovalCenter({111}), limits=limits)
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
    deals = pb.deals_since(pd.Timestamp("2025-01-01", tz="UTC"))
    return _canon(json.loads(json.dumps({
        "decisions": eng.decisions,
        "deals": json.loads(deals.to_json(orient="records", date_format="iso")),
        "open": {str(k): {"sl": t.sl, "tp": t.tp, "lots": t.lots, "agent": t.agent_id} for k, t in eng.open.items()},
    }, default=str)))


def _existing_only(got: dict[str, Any], extra: set[str]) -> dict[str, Any]:
    """The rows of the existing arms: the added arms' own rows (by agent, arm or proposal) left out."""
    tags = {"explore:" + a for a in extra}

    def mine(d: dict[str, Any]) -> bool:
        return d.get("agent") not in tags and d.get("arm") not in extra and not any(
            str(d.get("proposal", "")).startswith(t) for t in extra)
    return {**got, "decisions": [d for d in got["decisions"] if mine(d)]}


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    return {} if WRITE else json.loads(GOLDEN.read_text())


def test_the_seven_day_replay_of_the_existing_15m_1h_4h_arms_is_byte_identical_to_the_golden(tmp_path, golden):
    got = replay(tmp_path, existing_arms())
    if WRITE:
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
        golden = got
    assert got["decisions"], "the replay decided nothing: the identity would be vacuous"
    assert any(str(d.get("action", "")).startswith("exploration:executed") for d in got["decisions"]), \
        "no exploration order went out in the replay: the identity would not cover the order path"
    assert got == golden


def _long_arms() -> list[Arm]:
    """A 1d and a 1w arm on the same real rule (random bracket), as the catalogue adapter's would sit beside them."""
    return [_control("RB1D", "random_bracket", "1d", {**I0, "hold_bars": 2}, style="swing"),
            _control("RB1W", "random_bracket", "1w", {**I0, "hold_bars": 1}, style="swing")]


def _history(root: Path) -> Path:
    """A long daily history (bars-file schema, 21:00 UTC roll) from 2023 into the replay's window."""
    from tests.test_engine_long_tf import _history_frame
    (root / "bars").mkdir(parents=True)
    _history_frame("2023-01-02", pd.Timestamp("2025-03-05 21:00", tz="UTC")).to_parquet(
        root / "bars" / "xauusd_1d.parquet")
    return root


def test_dark_1d_and_1w_arms_leave_every_existing_arms_replay_byte_identical(tmp_path, golden) -> None:
    got = replay(tmp_path, existing_arms() + _long_arms())
    dark = [d for d in got["decisions"] if d.get("action") == "exploration_arm_dark"]
    assert sorted(d["arm"] for d in dark) == ["RB1D", "RB1W"]
    assert _existing_only(got, {"RB1D", "RB1W"}) == golden


def test_live_1d_and_1w_arms_with_their_warm_up_leave_every_existing_arms_replay_byte_identical(tmp_path, golden,
                                                                                                monkeypatch) -> None:
    from goldbot.config import INTRADAY_LIVE
    from goldbot.engine import long_bars
    fed: list[tuple[str, pd.Timestamp]] = []
    bars = long_bars.LongHistory.bars

    def spy(self: long_bars.LongHistory, arm: Any, complete: pd.DataFrame, through: pd.Timestamp) -> Any:
        got = bars(self, arm, complete, through)
        if not isinstance(got, str):
            fed.append((arm.arm_id, through))
        return got
    monkeypatch.setattr(long_bars.LongHistory, "bars", spy)
    (tmp_path / "state").mkdir()
    got = replay(tmp_path / "state", existing_arms() + _long_arms(), timeframes_live=(*INTRADAY_LIVE, "1d", "1w"),
                 long_history_dir=str(_history(tmp_path / "hist")))
    assert not [d for d in got["decisions"] if d.get("action") == "exploration_arm_dark"]
    # the 1d arm decides on its warm-up once the window holds a complete day, the 1w arm at the weekly open
    assert {a for a, _ in fed} == {"RB1D", "RB1W"}, fed
    assert _existing_only(got, {"RB1D", "RB1W"}) == golden
