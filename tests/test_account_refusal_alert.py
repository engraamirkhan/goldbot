"""Trading-safety follow-up to the per-order account check (MT5Broker `verify`): a terminal switched to another account
refuses every entry ("terminal_account_changed: ..."), on the auto, probe and approval paths alike. The owner is told
once per streak (Telegram, via the approval bus): one alert at the first refusal, none while the refusals continue,
and a filled entry ends the streak so the next refusal alerts again. The raw reason stays in the journal."""
from __future__ import annotations

from typing import Any

from goldbot.execution.broker import ACCOUNT_REFUSAL, OrderIntent, OrderResult
from goldbot.execution.paper import PaperBroker
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.bus import ApprovalBus
from tests.test_engine_safety import T0, _engine, _notices, _prop, _tick

MESSAGE = f"{ACCOUNT_REFUSAL}: terminal is logged in to another account than icm-demo"


class _Switched(PaperBroker):
    """Refuses an entry as the per-order account check does while `switched`; fills as on paper otherwise."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.switched = True

    def place_order(self, intent: OrderIntent) -> OrderResult:
        if self.switched:
            return OrderResult(ok=False, retcode=10006, order_id=None, position_id=None, filled_lots=0.0, price=None,
                               message=MESSAGE)
        return super().place_order(intent)


def _send(eng: Any, n: int) -> None:
    eng._execute(_prop(f"icm-demo-acct-{n}"), SPECIALISTS["session_open"](), 0.03, 2396.0, 2406.0, requested=2400.2)


def _setup(tmp_path: Any) -> tuple[Any, _Switched]:
    pb = _Switched(equity=10_000)
    pb.on_tick(_tick(T0))
    eng, _ = _engine(tmp_path, pb)
    eng.center.bus = ApprovalBus(tmp_path)
    return eng, pb


def test_a_switched_terminal_alerts_the_owner_once_while_its_refusals_continue(tmp_path):
    eng, _ = _setup(tmp_path)
    for n in range(3):
        _send(eng, n)
    [notice] = _notices(tmp_path)
    assert notice == ("Entries refused: the MT5 terminal for icm-demo is logged in to a different account. Nothing "
                      "was sent; open trades keep their stops and exits stay automatic. Entries stay refused until "
                      "the terminal is back on this account; this alert is not repeated until an entry goes through.")
    assert ACCOUNT_REFUSAL not in notice and "icm-demo-acct" not in notice
    journal = [d for d in eng.decisions if d.get("action") == "account_refused"]
    assert [d["message"] for d in journal] == [MESSAGE] * 3      # the raw reason stays in the journal


def test_a_filled_entry_ends_the_streak_so_the_next_refusal_alerts_again(tmp_path):
    eng, pb = _setup(tmp_path)
    _send(eng, 0)
    pb.switched = False
    _send(eng, 1)
    assert pb.positions()
    pb.switched = True
    _send(eng, 2)
    _send(eng, 3)
    assert len(_notices(tmp_path)) == 2


def test_another_refusal_with_the_same_retcode_does_not_alert_as_a_switched_account(tmp_path):
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(T0))
    eng, _ = _engine(tmp_path, pb)
    eng.center.bus = ApprovalBus(tmp_path)
    pb.place_order = lambda intent: OrderResult(ok=False, retcode=10006, order_id=None,  # type: ignore[method-assign]
                                                position_id=None, filled_lots=0.0, price=None, message="rejected")
    _send(eng, 0)
    assert _notices(tmp_path) == []
    assert not [d for d in eng.decisions if d.get("action") == "account_refused"]
