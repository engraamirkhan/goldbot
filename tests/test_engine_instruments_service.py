"""The engine service's multi-instrument start (lane L3, goldbot/ops/services/engine.py): which books an account gets,
the terms check per instrument, the arm caps per instrument copy and the wind-down across every book's range."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.execution.broker import Position
from goldbot.execution.paper import PaperBroker
from goldbot.instruments import Instruments, load_instruments
from goldbot.ops.services import engine as svc


def _acc(**kw: Any) -> Any:
    base = dict(account_id="icm-probe", broker="icm", mode="demo", is_observe_only=False, magic_base=260500,
                instruments=["XAGUSD", "XPTUSD"], enabled=True)
    return SimpleNamespace(**{**base, **kw})


def _confirmed(monkeypatch: pytest.MonkeyPatch, *keys: str) -> None:
    """The registry as lane L1's symbol check leaves it: these instruments enabled and confirmed."""
    reg = load_instruments()
    got = {k: (v.model_copy(update={"enabled": True, "verify": False, "digits": 2, "point": 0.01})
               if k in keys else v) for k, v in reg.instruments.items()}
    monkeypatch.setattr("goldbot.instruments.load_instruments", lambda *a, **k: Instruments(instruments=got))


def test_only_demo_accounts_get_non_gold_books():
    assert svc.account_instruments(_acc()) == ["XAGUSD", "XPTUSD"]
    assert svc.account_instruments(_acc(mode="live")) == []
    assert svc.account_instruments(_acc(is_observe_only=True)) == []
    assert svc.account_instruments(_acc(instruments=None)) == []
    assert svc.account_instruments(_acc(instruments=["XAUUSD"])) == []


def test_an_unconfirmed_or_misquoted_instrument_is_left_out_and_the_rest_trade(monkeypatch):
    _confirmed(monkeypatch, "XAGUSD")                  # XPTUSD stays `verify: true`: placeholder terms
    books = svc.instrument_books(_acc(), PaperBroker(), None)
    assert [b.key for b in books] == ["XAGUSD"]
    b = books[0]
    assert (b.symbol, b.store_symbol, b.magic_block, b.instrument.correlation_group) == ("XAGUSD", "XAGUSD", 1,
                                                                                         "metals")

    class ThreeDigits(PaperBroker):
        def symbol_info(self, symbol: str) -> Any:
            return super().symbol_info(symbol).model_copy(update={"digits": 3, "point": 0.001})
    assert svc.instrument_books(_acc(), ThreeDigits(), None) == ()


def test_each_instrument_copy_of_an_arm_carries_the_arms_cap(monkeypatch):
    monkeypatch.setattr("goldbot.ops.accounts.exploration_arm_caps", lambda acc: {"I0": 17.0, "S0": 6.5})
    caps = svc.instrument_arm_caps(_acc())
    assert caps == {"I0": 17.0, "S0": 6.5, "I0@XAGUSD": 17.0, "S0@XAGUSD": 6.5, "I0@XPTUSD": 17.0, "S0@XPTUSD": 6.5}
    assert svc.instrument_arm_caps(_acc(instruments=[])) == {"I0": 17.0, "S0": 6.5}


def test_a_disabled_account_winds_down_while_any_book_holds_a_position():
    pb = PaperBroker()
    t0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")
    pb._positions[1] = Position(position_id=1, symbol="XPTUSD", side=1, lots=0.1, open_price=900.0, sl=890.0, tp=None,
                                magic=3_050_200 + 95, comment="", open_time_utc=t0, profit=0.0)
    assert svc.disabled_wind_down(_acc(enabled=False), pb)
    with pytest.raises(SystemExit):
        svc.disabled_wind_down(_acc(enabled=False, instruments=[]), pb)    # gold alone: its own range only
