"""Incident 2026-10-20 (icm-probe, MetaTrader5 package 5.0.6231 under Wine): `mt5.order_check` returns None with
`(-2, 'Invalid "comment" argument')` for a comment of 30 characters or more; 29 is accepted. Every scheduled cost
probe (`cm-sched-<epoch>-<10 hex>`, 30 characters) was refused before sending. The order comment is now at most
MT5_COMMENT_MAX (29) characters everywhere, and the comment look-ups still find a position opened before the change
with the legacy 31-character slice."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from goldbot.engine import cost_probe, unconfirmed
from goldbot.execution.broker import MT5_COMMENT_MAX, OrderIntent, comment_keys
from goldbot.execution.paper import PaperBroker
from tests.test_engine_safety import _prop
from tests.test_mt5_order_account_check import _broker as _mt5_broker
from tests.test_mt5_order_account_check import _terminal
from tests.test_order_not_confirmed import AGENT, SL, TP, _broker, _engine, _look

LONG_PID = "icm-demo-session_open-1793008790000000000-a1b2c3"      # 48 characters, as a strategy proposal id
SLOT = pd.Timestamp("2026-10-20T12:45", tz="UTC")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[pd.Timestamp]:
    now = [pd.Timestamp.now("UTC")]
    monkeypatch.setattr(unconfirmed, "_now", lambda: now[0])
    return now


def test_mt5_comment_max_is_the_measured_29_characters():
    assert MT5_COMMENT_MAX == 29


def test_the_scheduled_probe_id_keeps_its_prefix_and_fits_the_mt5_comment_whole():
    eng = SimpleNamespace(cfg=SimpleNamespace(account_id="icm-probe"))
    pid = cost_probe._scheduled_order_id(eng, SLOT)                     # type: ignore[arg-type]
    assert pid.startswith(cost_probe.SCHEDULED_COMMENT) and len(pid) == 28 <= MT5_COMMENT_MAX
    assert pid == cost_probe._scheduled_order_id(eng, SLOT)             # type: ignore[arg-type]


def test_the_mirror_probe_id_fits_the_mt5_comment_whole():
    eng = SimpleNamespace(cfg=SimpleNamespace(account_id="icm-probe"))
    pid = cost_probe._probe_order_id(eng, "mirror", SLOT)              # type: ignore[arg-type]
    assert pid.startswith(cost_probe.COMMENT) and len(pid) == 24 <= MT5_COMMENT_MAX


def test_a_strategy_proposal_is_sent_with_the_last_29_characters_of_its_id_as_comment(tmp_path):
    pb = _broker(fill=0.1)
    eng, _ = _engine(tmp_path, pb)
    eng._execute(_prop(LONG_PID), AGENT, 0.1, SL, TP, requested=2400.2)
    [pos] = pb.positions()
    assert pos.comment == LONG_PID[-MT5_COMMENT_MAX:] and len(pos.comment) == MT5_COMMENT_MAX


def test_the_mt5_adapter_never_passes_a_comment_over_29_characters_to_order_check_or_order_send(monkeypatch):
    term = _terminal()
    checked: list[dict] = []
    check = term.order_check

    def order_check(req: dict) -> object:
        checked.append(dict(req))
        return check(req)

    term.order_check = order_check
    intent = OrderIntent(client_order_id=LONG_PID, symbol="XAUUSD", side=1, lots=0.01, sl=2390.0, tp=2430.0,
                         magic=260177, comment=LONG_PID)
    res = _mt5_broker(monkeypatch, term).place_order(intent)
    assert res.ok
    assert checked[0]["comment"] == LONG_PID[:MT5_COMMENT_MAX] and term.sent[0]["comment"] == LONG_PID[:MT5_COMMENT_MAX]


def test_comment_keys_accept_the_whole_id_the_29_slice_and_the_legacy_31_slice():
    assert set(comment_keys(LONG_PID)) == {LONG_PID, LONG_PID[-29:], LONG_PID[-31:]}


@pytest.mark.parametrize("width", [MT5_COMMENT_MAX, 31], ids=["29-slice", "legacy-31-slice"])
def test_an_unconfirmed_order_is_found_by_its_29_or_legacy_31_character_comment(tmp_path, clock, width):
    pb = _broker()                                                      # the send times out, nothing filled
    eng, _ = _engine(tmp_path, pb)
    eng._execute(_prop(LONG_PID), AGENT, 0.1, SL, TP, requested=2400.2)
    assert eng._orders[LONG_PID].status == "ambiguous"
    PaperBroker.place_order(pb, OrderIntent(client_order_id=LONG_PID, symbol="XAUUSD", side=1, lots=0.1, sl=SL, tp=TP,
                                            magic=eng._magic(AGENT.family), comment=LONG_PID[-width:]))
    [pos] = pb.positions()
    _look(eng)
    assert eng._orders[LONG_PID].status == "filled" and eng._orders[LONG_PID].position_id == pos.position_id


@pytest.mark.parametrize("width", [MT5_COMMENT_MAX, 31], ids=["29-slice", "legacy-31-slice"])
def test_entry_deals_are_matched_by_the_29_or_legacy_31_character_comment(width):
    deals = pd.DataFrame([{"comment": LONG_PID[-width:], "entry": 0, "position_id": 7},
                          {"comment": "someone-else", "entry": 0, "position_id": 8}])
    assert list(unconfirmed._entries(deals, LONG_PID)["position_id"]) == [7]


@pytest.mark.parametrize("width", [MT5_COMMENT_MAX, 31], ids=["29-slice", "legacy-31-slice"])
def test_a_sending_row_is_resolved_by_the_29_or_legacy_31_character_comment(tmp_path, width, monkeypatch):
    pb = _broker(fill=0.1)
    eng, _ = _engine(tmp_path, pb)
    eng._execute(_prop(LONG_PID), AGENT, 0.1, SL, TP, requested=2400.2)
    [pos] = pb.positions()
    found: list[int] = []
    monkeypatch.setattr(unconfirmed, "adopt_sent", lambda e, cid, rec, p: found.append(p.position_id))
    rec = eng._orders[LONG_PID]
    eng._resolve_sent(LONG_PID, rec, {pos.position_id: pos.model_copy(update={"comment": LONG_PID[-width:]})})
    assert found == [pos.position_id]
