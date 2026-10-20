"""Incident 2026-10-20: every scheduled cost probe on icm-probe came back `order_check: ` with retcode -1 and nothing
in the bridge's log, because `mt5.order_check` returned None and the adapter dropped MT5's own error. A None now says
`order_check: none: <mt5.last_error()>` and logs a warning naming the client order id, the request's keys with their
value TYPES (never the comment or magic) and last_error; a refused check keeps its comment and adds the retcode.
retcode -1 for a None is unchanged (the engine's ambiguity handling is a trading-safety decision)."""
from __future__ import annotations

import logging

from goldbot.execution import mt5_adapter
from goldbot.execution.broker import OrderIntent
from tests.test_mt5_order_account_check import Chk, _broker, _terminal

LAST_ERROR = (-2, 'Invalid "type_filling" argument')
COMMENT = "probe-secret-comment"
MAGIC = 260177


def _intent() -> OrderIntent:
    return OrderIntent(client_order_id="icm-probe-2026-10-20T12:45-abc", symbol="XAUUSD", side=1, lots=0.01,
                       sl=2390.0, tp=2430.0, magic=MAGIC, comment=COMMENT)


def test_an_order_check_that_returns_none_says_mt5s_last_error_and_sends_nothing(monkeypatch, caplog):
    term = _terminal()
    term.order_check = lambda req: None
    term.last_error = lambda: LAST_ERROR
    with caplog.at_level(logging.WARNING, logger="goldbot.mt5"):
        res = _broker(monkeypatch, term).place_order(_intent())
    assert not res.ok and res.retcode == -1 and term.sent == []
    assert res.message == f"order_check: none: {LAST_ERROR}"
    [rec] = [r for r in caplog.records if "order_check" in r.getMessage()]
    text = rec.getMessage()
    assert "icm-probe-2026-10-20T12:45-abc" in text and str(LAST_ERROR) in text
    for kv in ("volume=float", "sl=float", "tp=float", "magic=int", "comment=str", "type_filling=int", "symbol=str"):
        assert kv in text, (kv, text)
    assert COMMENT not in text and str(MAGIC) not in text              # types only, never the values


def test_a_refused_order_check_keeps_its_comment_and_adds_the_retcode(monkeypatch):
    term = _terminal()
    term.order_check = lambda req: Chk(10027, "AutoTrading disabled by client")
    res = _broker(monkeypatch, term).place_order(_intent())
    assert not res.ok and res.retcode == 10027 and term.sent == []
    assert res.message == "order_check: AutoTrading disabled by client (retcode 10027)"


def test_a_passed_order_check_still_sends(monkeypatch):
    term = _terminal()
    res = _broker(monkeypatch, term).place_order(_intent())
    assert res.ok and len(term.sent) == 1 and mt5_adapter.RETCODE_DONE == res.retcode
