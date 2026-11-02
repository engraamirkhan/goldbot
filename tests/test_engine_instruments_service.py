"""The engine service's multi-instrument start (lane L3, goldbot/ops/services/engine.py): which books an account gets,
the terms check per instrument, the arm caps per instrument copy and the wind-down across every book's range."""
from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.execution.broker import Position
from goldbot.execution.paper import PaperBroker
from goldbot.instruments import GOLD, Instruments, load_instruments, registry
from goldbot.ops.services import engine as svc


def _acc(**kw: Any) -> Any:
    base = dict(account_id="icm-probe", broker="icm", mode="demo", is_observe_only=False, magic_base=260500,
                instruments=["XAGUSD", "XPTUSD"], instrument_magic_index=5, enabled=True)
    return SimpleNamespace(**{**base, **kw})


@pytest.fixture(autouse=True)
def _fresh_registry() -> Iterator[None]:
    """`registry()` caches whatever `load_instruments` returns: a patched copy (`_confirmed`) never outlives a test."""
    registry.cache_clear()
    yield
    registry.cache_clear()


def _confirmed(monkeypatch: pytest.MonkeyPatch, *keys: str) -> None:
    """The registry as lane L1's symbol check leaves it: these instruments enabled and confirmed, every other
    non-gold one a placeholder (`verify: true`), whatever config/instruments.yaml says today."""
    reg = load_instruments()
    got = {k: (v.model_copy(update={"enabled": True, "verify": False, "digits": 2, "point": 0.01}) if k in keys
               else v if k == GOLD else v.model_copy(update={"verify": True}))
           for k, v in reg.instruments.items()}
    monkeypatch.setattr("goldbot.instruments.load_instruments", lambda *a, **k: Instruments(instruments=got))


def test_only_demo_accounts_get_non_gold_books():
    assert svc.account_instruments(_acc()) == ["XAGUSD", "XPTUSD"]
    assert svc.account_instruments(_acc(mode="live")) == []
    assert svc.account_instruments(_acc(is_observe_only=True)) == []
    assert svc.account_instruments(_acc(instruments=None)) == []
    assert svc.account_instruments(_acc(instruments=["XAUUSD"])) == []


def test_an_unconfirmed_or_misquoted_instrument_gets_a_book_that_refuses_every_entry(monkeypatch):
    """L1: "NEVER trade a spec with verify: true": its book opens (bars, exits) but `blocked` refuses its entries; a
    terminal quoting other digits than the registry blocks it too, and its prices round to the terminal's digits."""
    _confirmed(monkeypatch, "XAGUSD")                  # XPTUSD stays `verify: true`: placeholder terms
    books = svc.instrument_books(_acc(), PaperBroker(), None)
    assert [(b.key, b.blocked) for b in books] == [("XAGUSD", None), ("XPTUSD", "instrument_unverified")]
    b = books[0]
    assert (b.symbol, b.store_symbol, b.magic_base, b.instrument.correlation_group) == ("XAGUSD", "XAGUSD",
                                                                                        3_050_100, "metals")

    class ThreeDigits(PaperBroker):
        def symbol_info(self, symbol: str) -> Any:
            return super().symbol_info(symbol).model_copy(update={"digits": 3, "point": 0.001})
    got = svc.instrument_books(_acc(), ThreeDigits(), None)
    assert [(b.blocked, b.digits) for b in got] == [("instrument_unverified", 3)] * 2


def test_a_disabled_account_winds_down_while_any_book_holds_a_position():
    pb = PaperBroker()
    t0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")
    pb._positions[1] = Position(position_id=1, symbol="XPTUSD", side=1, lots=0.1, open_price=900.0, sl=890.0, tp=None,
                                magic=3_050_200 + 95, comment="", open_time_utc=t0, profit=0.0)
    assert svc.disabled_wind_down(_acc(enabled=False), pb)
    with pytest.raises(SystemExit):
        svc.disabled_wind_down(_acc(enabled=False, instruments=[]), pb)    # gold alone: its own range only
