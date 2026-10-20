"""Incident 2026-10-20: every scheduled cost probe on icm-probe came back `order_check: ` with retcode -1 and nothing
in the bridge's log, because `mt5.order_check` returned None and the adapter dropped MT5's own error. A None now says
`order_check: none: <mt5.last_error()>` and logs a warning naming the client order id, the request's keys with their
value TYPES (never the comment or magic) and last_error; a refused check keeps its comment and adds the retcode.
Trading-safety ruling: an order_check failure happens BEFORE any send, so it is a definite reject
(RETCODE_PRECHECK_FAILED, in DEFINITE_REJECT_RETCODES), never ambiguous; a None from order_send stays ambiguous (-1)."""
from __future__ import annotations

import logging

from goldbot.engine.runner import DEFINITE_REJECT_RETCODES
from goldbot.engine.unconfirmed import AMBIGUOUS, sent_status
from goldbot.execution import mt5_adapter
from goldbot.execution.broker import PRECHECK_REFUSAL, RETCODE_PRECHECK_FAILED, OrderIntent
from goldbot.execution.not_placed import not_placed, refusal_code
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
    assert not res.ok and res.retcode == RETCODE_PRECHECK_FAILED != -1 and term.sent == []    # order_send never called
    assert res.message == f"{PRECHECK_REFUSAL}: order_check: none: {LAST_ERROR}"
    assert RETCODE_PRECHECK_FAILED in DEFINITE_REJECT_RETCODES
    assert sent_status(res, paper=False, definite=DEFINITE_REJECT_RETCODES) == "rejected"     # never ambiguous
    code = refusal_code(res, paper=False, definite=DEFINITE_REJECT_RETCODES)
    assert code == PRECHECK_REFUSAL and not_placed(code).by == "mt5_box"                    # type: ignore[union-attr]
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


def test_a_check_refused_with_an_otherwise_ambiguous_code_is_still_a_definite_precheck_reject(monkeypatch):
    """10031 (no connection) is ambiguous from order_send; from order_check nothing was sent."""
    term = _terminal()
    term.order_check = lambda req: Chk(10031, "No connection")
    res = _broker(monkeypatch, term).place_order(_intent())
    assert res.retcode == RETCODE_PRECHECK_FAILED and term.sent == []
    assert res.message == f"{PRECHECK_REFUSAL}: order_check: No connection (retcode 10031)"
    assert sent_status(res, paper=False, definite=DEFINITE_REJECT_RETCODES) == "rejected"


def test_a_none_from_order_send_stays_ambiguous(monkeypatch):
    term = _terminal()
    term.order_send = lambda req: None
    res = _broker(monkeypatch, term).place_order(_intent())
    assert res.retcode == -1 and -1 not in DEFINITE_REJECT_RETCODES
    assert sent_status(res, paper=False, definite=DEFINITE_REJECT_RETCODES) == AMBIGUOUS
