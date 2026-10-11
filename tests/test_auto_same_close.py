"""Trading-safety review HIGH (BACKLOG 19 review): in auto mode every entry decided at one bar close was checked against
the account as read before the first one filled, so `max_positions`, the combined cap, the heat cap and the
same-direction cap could each be passed within one bar close.

The rule now: after an auto entry is sent, the engine reads the account again before the next decision. A fill the
broker does not show yet is added to the facts conservatively (one more position on its side, its lots, the gate's
margin and its stop risk). An ambiguous result (not filled and not a definite reject) or a failed re-read stops auto
entries for the rest of that bar close. Exits are not touched."""
from typing import Any

import pandas as pd
import pytest

from goldbot.config import HeatSettings
from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.execution.broker import OrderIntent, OrderResult, Position, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.risk import RiskLimits
from goldbot.specialists.session_open import SessionOpenSpecialist
from goldbot.specialists.trend import TrendSpecialist
from goldbot.telegram.approvals import ApprovalCenter


class _LongSessionOpen(SessionOpenSpecialist):
    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"idx": [len(mid_bars) - 1], "side": [1]})


class _LongTrend(TrendSpecialist):                  # 1h: decides at closes that complete an hour
    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"idx": [len(mid_bars) - 1], "side": [1]})


class LaggingPaper(PaperBroker):
    """A broker whose positions() does not show a fill yet (as a terminal can lag its order result)."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.hidden: set[int] = set()

    def place_order(self, intent: OrderIntent) -> OrderResult:
        res = super().place_order(intent)
        if res.position_id is not None:
            self.hidden.add(res.position_id)
        return res

    def positions(self, magic_prefix: int | None = None) -> list[Position]:
        return [p for p in super().positions(magic_prefix) if p.position_id not in self.hidden]


class AmbiguousPaper(PaperBroker):
    """The first order comes back neither filled nor definitely rejected (a send that timed out: retcode -1)."""

    def __init__(self, retcode: int = -1, **kw: Any) -> None:
        super().__init__(**kw)
        self.first = True
        self.retcode = retcode

    def place_order(self, intent: OrderIntent) -> OrderResult:
        if self.first:
            self.first = False
            return OrderResult(ok=False, retcode=self.retcode, order_id=None, position_id=None, filled_lots=0.0,
                               price=None, message="send failed")
        return super().place_order(intent)


# small risk and a fixed multiplier, so the combined-notional cap never refuses the second entry on its own
BASE: dict[str, Any] = dict(risk_per_trade=0.002, multiplier_bounds=(1.0, 1.0), max_positions=5)


def _run(tmp_path: Any, agents: list[Any], limits: RiskLimits, pb: PaperBroker | None = None,
         mode: str = "auto", start: str = "2025-03-03") -> tuple[Engine, PaperBroker, list[dict]]:
    ticks = synthetic_ticks(start, "2025-03-05", ticks_per_minute=1, seed=5)
    last, px = pd.Timestamp(ticks["ts_utc"].iloc[-1]), float(ticks["bid"].iloc[-1])
    pb = pb if pb is not None else PaperBroker(equity=10_000, adverse_slip_points=0.0)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode=mode, state_dir=str(tmp_path),
                              owner_user_id=111), pb, agents, {a.family: ConstantModel(p=0.9) for a in agents},
                 ApprovalCenter({111}), limits=limits)
    eng.bars_1m = ticks_to_1m(ticks)
    tick = Tick(ts_utc=last, bid=px, ask=px + 0.2)
    pb.on_tick(tick)
    close = (last + pd.Timedelta(minutes=15)).floor("15min")
    assert close == close.floor("1h")                       # this close completes a 15m and a 1h bar
    return eng, pb, eng.on_bar_close(close, tick)


def _actions(decisions: list[dict]) -> list[str]:
    return [str(d["action"]) for d in decisions if d.get("agent")]


def _two() -> list[Any]:
    """Two 15m agents (same family, different configuration, so different agent ids), each long at every close."""
    return [_LongSessionOpen(), _LongSessionOpen(min_body_pct=0.31)]


def test_two_agents_at_one_close_in_auto_mode_the_second_sees_the_first_fill_for_max_positions(tmp_path):
    eng, pb, dec = _run(tmp_path, _two(), RiskLimits(**{**BASE, "max_positions": 1}))
    assert _actions(dec) == ["executed:auto", "gate:max_positions"], _actions(dec)
    assert len(pb.positions()) == 1


def test_the_control_two_agents_both_trade_when_the_caps_allow_two(tmp_path):
    eng, pb, dec = _run(tmp_path, _two(), RiskLimits(**BASE))
    assert _actions(dec) == ["executed:auto", "executed:auto"], _actions(dec)


def test_the_heat_cap_sees_the_first_fill_of_the_same_close(tmp_path):
    heat = HeatSettings(enabled=True, max_open_risk=0.002, max_same_direction=5)   # one 0.01 lot (~0.14%) fits, two do not
    eng, pb, dec = _run(tmp_path, _two(), RiskLimits(**BASE, heat=heat))
    assert _actions(dec) == ["executed:auto", "gate:heat_cap"], _actions(dec)


def test_the_same_direction_cap_sees_the_first_fill_of_the_same_close(tmp_path):
    heat = HeatSettings(enabled=True, max_open_risk=0.5, max_same_direction=1)
    eng, pb, dec = _run(tmp_path, _two(), RiskLimits(**BASE, heat=heat))
    assert _actions(dec) == ["executed:auto", "gate:same_direction_cap"], _actions(dec)


def test_two_timeframes_closing_together_see_each_others_fill(tmp_path):
    eng, pb, dec = _run(tmp_path, [_LongSessionOpen(), _LongTrend()], RiskLimits(**{**BASE, "max_positions": 1}),
                         start="2025-02-24")                         # 120 1h bars before the close
    assert _actions(dec) == ["executed:auto", "gate:max_positions"], _actions(dec)


@pytest.mark.parametrize(("limits", "reason"), [
    (dict(max_positions=1), "max_positions"),
    (dict(heat=HeatSettings(enabled=True, max_open_risk=0.002, max_same_direction=5)), "heat_cap"),
    (dict(heat=HeatSettings(enabled=True, max_open_risk=0.5, max_same_direction=1)), "same_direction_cap"),
])
def test_a_broker_that_does_not_show_the_fill_yet_still_refuses_the_second(tmp_path, limits, reason):
    pb = LaggingPaper(equity=10_000, adverse_slip_points=0.0)
    eng, _, dec = _run(tmp_path, _two(), RiskLimits(**{**BASE, **limits}), pb=pb)
    assert _actions(dec) == ["executed:auto", f"gate:{reason}"], _actions(dec)
    st = eng.state
    assert st.open_positions == 1 and st.open_long == 1 and st.open_lots > 0 and st.margin_used > 0


def test_an_ambiguous_order_result_stops_auto_entries_for_the_rest_of_the_close(tmp_path):
    # LOW-B: the journal says the order was not confirmed, never "executed"
    pb = AmbiguousPaper(equity=10_000, adverse_slip_points=0.0)
    eng, _, dec = _run(tmp_path, _two(), RiskLimits(**BASE), pb=pb)
    assert _actions(dec) == ["auto_unconfirmed:-1", "auto_paused:unconfirmed_order"], _actions(dec)
    assert not pb.positions()


@pytest.mark.parametrize("retcode", [10019, 10032, 10046])   # no money, only real accounts, open-order limit
def test_a_definite_reject_is_journalled_as_rejected_and_does_not_stop_the_next_entry(tmp_path, retcode):
    pb = AmbiguousPaper(retcode=retcode, equity=10_000, adverse_slip_points=0.0)     # nothing placed
    eng, _, dec = _run(tmp_path, _two(), RiskLimits(**BASE), pb=pb)
    assert _actions(dec) == [f"auto_rejected:{retcode}", "executed:auto"], _actions(dec)
    assert len(pb.positions()) == 1


def test_a_failed_account_reread_after_a_fill_stops_auto_entries_for_the_rest_of_the_close(tmp_path):
    class Flaky(PaperBroker):
        fills = 0

        def place_order(self, intent: OrderIntent) -> OrderResult:
            self.fills += 1
            return super().place_order(intent)

        def account(self) -> Any:
            if self.fills:
                raise RuntimeError("terminal gone")
            return super().account()

    pb = Flaky(equity=10_000, adverse_slip_points=0.0)
    eng, _, dec = _run(tmp_path, _two(), RiskLimits(**BASE), pb=pb)
    assert _actions(dec) == ["executed:auto", "auto_paused:account_unread"], _actions(dec)
    assert len(pb.positions()) == 1


def test_an_order_call_that_raises_pauses_auto_entries_and_leaves_the_row_sending(tmp_path):
    # LOW-C: a bridge timeout or terminal error inside place_order may or may not have placed the order: the row stays
    # "sending" for the restart reconciliation, the error blocks entries, and no second order is sent this close
    class Raising(PaperBroker):
        calls = 0

        def place_order(self, intent: OrderIntent) -> OrderResult:
            self.calls += 1
            raise TimeoutError("bridge timed out")

    pb = Raising(equity=10_000, adverse_slip_points=0.0)
    eng, _, dec = _run(tmp_path, _two(), RiskLimits(**BASE), pb=pb)
    first, second = _actions(dec)
    assert first == "auto_unconfirmed:TimeoutError" and pb.calls == 1
    assert second.startswith(("gate:", "auto_paused:")) and "executed" not in second, second
    assert eng._auto_paused is not None and eng._auto_paused[1] == "order_raised"
    assert [o.status for o in eng._orders.values()] == ["sending"]
    assert eng.state.tick_error and eng.tick_errors_total == 1
