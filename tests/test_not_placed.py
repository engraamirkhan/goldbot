"""Order not placed (docs/ux/order-not-placed.md): one wording table for the Telegram card, the owner notice and the
API's `not_placed` field, for every definite refusal of an approved entry by the MT5 box or the broker."""
from __future__ import annotations

import pytest

from goldbot.engine.runner import DEFINITE_REJECT_RETCODES
from goldbot.execution.broker import OrderResult
from goldbot.execution.not_placed import (
    CEILING_CODES,
    NotPlaced,
    approval_cause,
    broker_code,
    card_text,
    not_placed,
    notice_text,
    refusal_code,
)
from goldbot.telegram.entry_text import account_name

PID = "icm-demo-51234567-1700000000-ab12"
RAW = "Request rejected by trade server"


def _codes() -> list[str]:
    return ([f"broker_refused:{r}" for r in sorted(DEFINITE_REJECT_RETCODES)]
            + ["broker_refused:10027:paper", "bridge_stale_order", *CEILING_CODES])


def _res(retcode: int, message: str = RAW) -> OrderResult:
    return OrderResult(ok=False, retcode=retcode, order_id=None, position_id=None, filled_lots=0.0, price=None,
                       message=message)


def _code(res: OrderResult, paper: bool = False) -> str | None:
    return refusal_code(res, paper=paper, definite=DEFINITE_REJECT_RETCODES)


@pytest.mark.parametrize("retcode", sorted(DEFINITE_REJECT_RETCODES))
def test_every_definite_reject_has_a_row_or_reaches_the_fallback_that_names_its_code(retcode):
    np = not_placed(f"broker_refused:{retcode}")
    assert np is not None and np.by == "broker"
    if np.reason is None:
        assert np.code == retcode and np.next_step == f"If it repeats, ask Claude to check (broker code {retcode})."
        assert f"(code {retcode})" in card_text(f"broker_refused:{retcode}")  # type: ignore[operator]
    else:
        assert np.code is None


@pytest.mark.parametrize("code", _codes())
def test_no_owner_string_carries_a_proposal_id_an_internal_code_the_word_retcode_or_the_raw_message(code):
    np = not_placed(code)
    assert np is not None
    texts = [card_text(code) or "", np.reason or "", np.next_step,
             notice_text(np, side=1, lots=0.37, symbol="XAUUSD", account=account_name("icm-demo-51234567"))]
    for t in texts:
        for banned in (PID, "bridge_", "broker_refused", "retcode", RAW, "51234567"):
            assert banned not in t, (code, banned, t)


def test_a_bridge_stale_refusal_is_coded_by_its_prefix_before_its_retcode():
    assert _code(_res(10006, "bridge_stale_order: 12.3 s old")) == "bridge_stale_order"
    assert _code(_res(10006)) == "broker_refused:10006"
    assert _code(_res(10019)) == "broker_refused:10019"


def test_only_a_definite_reject_gets_a_refusal_code():
    for ambiguous in (-1, 10008, 10010, 10011, 10012, 10031, 99999):
        assert _code(_res(ambiguous)) is None
        assert _code(_res(ambiguous, "bridge_stale_order: 12.3 s old")) is None     # the prefix alone is not enough
    filled = OrderResult(ok=True, retcode=10009, order_id=1, position_id=2, filled_lots=0.1, price=2400.0, message="")
    assert _code(filled) is None


def test_a_ceiling_prefix_on_an_ok_result_is_not_a_refusal():
    ok = OrderResult(ok=True, retcode=10006, order_id=1, position_id=None, filled_lots=0.0, price=None,
                     message="bridge_notional_ceiling: notional 7500.00 USD > 6 x equity 1000.00")
    assert _code(ok) is None                                   # matches runner.bridge_refused: `not res.ok` first


def test_the_paper_brokers_10027_is_a_duplicate_and_a_terminals_is_algo_trading_off():
    assert _code(_res(10027), paper=True) == "broker_refused:10027:paper"
    assert _code(_res(10019), paper=True) == "broker_refused:10019"         # only 10027 depends on the kind
    assert not_placed("broker_refused:10027:paper") == NotPlaced(
        by="broker", reason="it was already sent; this repeat was refused", nothing_sent=False,
        next_step="Nothing to do: the first send stands.")
    assert not_placed("broker_refused:10027") == NotPlaced(
        by="broker", reason="Algo Trading is switched off in the MT5 terminal",
        next_step="Turn on Algo Trading in the MT5 terminal.")


def test_the_paper_duplicate_never_claims_nothing_was_sent_on_the_card_or_the_notice():
    """Trading-safety review LOW: the first send of a duplicate did go out, so "Nothing was sent" would be false."""
    code = "broker_refused:10027:paper"
    np = not_placed(code)
    assert np is not None
    assert card_text(code) == ("🛑 NOT PLACED: approved, but the broker refused it: it was already sent; this repeat "
                               "was refused.")
    assert notice_text(np, side=1, lots=0.37, symbol="XAUUSD", account="icm-demo") == (
        "Order not placed: the broker refused LONG 0.37 lots XAUUSD on icm-demo: it was already sent; this repeat was "
        "refused. Open trades keep their stops. Nothing to do: the first send stands.")
    for other in _codes():
        if other != code:
            assert (n := not_placed(other)) is not None and n.nothing_sent, other


def test_broker_code_reads_the_retcode_of_any_broker_refusal_and_nothing_else():
    assert broker_code("broker_refused:10019") == 10019 and broker_code("broker_refused:10027:paper") == 10027
    for other in ("bridge_stale_order", "bridge_notional_ceiling", "daily_loss", "broker_refused:", "broker_refused:x"):
        assert broker_code(other) is None and (other.startswith("bridge_") or not_placed(other) is None)


@pytest.mark.parametrize(("code", "text"), [
    ("broker_refused:10019", "🛑 NOT PLACED: approved, but the broker refused it: there wasn't enough free margin. "
                             "Nothing was sent."),
    ("broker_refused:10016", "🛑 NOT PLACED: approved, but the broker refused it: the stop or target wasn't accepted "
                             "(too close to the price). Nothing was sent."),
    ("bridge_stale_order", "🛑 NOT PLACED: approved, but the MT5 box refused it: it took more than 10 s to check the "
                           "order, so it dropped it rather than trade on an old price. Nothing was sent."),
    ("broker_refused:10006", "🛑 NOT PLACED: approved, but the broker refused it (code 10006). Nothing was sent."),
])
def test_the_telegram_card_reads_the_four_worked_examples_byte_for_byte(code, text):
    from goldbot.telegram.bot import outcome_text
    assert outcome_text(f"GATE_REFUSED: {code}") == text


@pytest.mark.parametrize(("code", "side", "lots", "text"), [
    ("broker_refused:10019", 1, 0.37,
     "Order not placed: the broker refused LONG 0.37 lots XAUUSD on icm-demo: there wasn't enough free margin. Nothing "
     "was sent; open trades keep their stops. Check free margin on the Overview before approving the next trade."),
    ("broker_refused:10006", -1, 0.1,
     "Order not placed: the broker refused SHORT 0.1 lots XAUUSD on icm-demo (code 10006). Nothing was sent; open "
     "trades keep their stops. If it repeats, ask Claude to check (broker code 10006)."),
    ("bridge_stale_order", 1, 0.37,
     "Order not placed: the MT5 box refused LONG 0.37 lots XAUUSD on icm-demo: it took more than 10 s to check the "
     "order, so it dropped it rather than trade on an old price. Nothing was sent; open trades keep their stops. "
     "Nothing to do if it's a one-off. If it repeats, the MT5 box is overloaded or slow: check the Health tab."),
])
def test_the_owner_notice_reads_the_worked_examples_byte_for_byte(code, side, lots, text):
    np = not_placed(code)
    assert np is not None
    assert notice_text(np, side=side, lots=lots, symbol="XAUUSD", account="icm-demo") == text


@pytest.mark.parametrize(("code", "reason"), [
    ("bridge_notional_ceiling",
     "its safety limit stops open gold worth more than 6× equity, and this trade would have gone over it"),
    ("bridge_notional_ceiling_unreadable", "it couldn't read the account figures it checks, so it refused to be safe"),
])
def test_the_ceiling_codes_keep_their_shipped_words_on_every_surface(code, reason):
    from goldbot.telegram.bot import outcome_text
    np = not_placed(code)
    assert np is not None and np.by == "mt5_box" and np.reason == reason
    shipped = {"bridge_notional_ceiling": "🛑 NOT PLACED: approved, but the MT5 box's safety limit refused it (open "
                                          "gold would be worth more than 6× equity). Nothing was sent.",
               "bridge_notional_ceiling_unreadable": "🛑 NOT PLACED: approved, but the MT5 box couldn't read the "
                                                     "account figures it checks, so it refused to be safe. Nothing "
                                                     "was sent."}
    assert outcome_text(f"GATE_REFUSED: {code}") == shipped[code]


def test_a_risk_gate_refusal_is_not_a_not_placed_code():
    from goldbot.telegram.bot import outcome_text
    assert not_placed("daily_loss") is None and card_text("daily_loss") is None
    # BACKLOG 72 (docs/ux/order-not-confirmed.md): the RiskGate's code in words, never the raw code
    assert outcome_text("GATE_REFUSED: daily_loss") == ("🛑 NOT PLACED: approved, but the risk check refused it at "
                                                        "approval (daily loss)")
    assert outcome_text("GATE_REFUSED: heat_cap, owner_halt") == (
        "🛑 NOT PLACED: approved, but the risk check refused it at approval (too much risk already open, owner halt)")


@pytest.mark.parametrize(("codes", "outcome", "cause"), [
    (None, None, None), ([], None, None),
    (["spread"], None, "risk_check"), (["daily_loss", "heat_cap"], None, "risk_check"),
    (["broker_refused:10019"], None, "not_placed"), (["broker_refused:10027:paper"], None, "not_placed"),
    (["broker_refused:10006"], None, "not_placed"), (["bridge_stale_order"], None, "not_placed"),
    (["bridge_notional_ceiling"], None, "not_placed"), (["bridge_notional_ceiling_unreadable"], None, "not_placed"),
    (["not_confirmed"], None, "not_confirmed"), (["not_confirmed"], "pending", "not_confirmed"),
    (["not_confirmed"], "still_unknown", "not_confirmed"), (["not_confirmed"], "no_trade", "not_placed"),
    (["not_confirmed"], "opened", "opened"), (["not_confirmed"], "opened_closed", "opened"),
    (["not_confirmed"], "opened_late", "opened"), (["not_confirmed"], "a future outcome", "not_confirmed"),
])
def test_an_approved_card_is_sorted_by_cause_risk_check_mt5_box_or_broker_not_confirmed_or_opened(codes, outcome, cause):
    # BACKLOG 83: only the RiskGate's own codes are "refused by the risk check"; a resolved not_confirmed card counts
    # by what the broker showed
    assert approval_cause(codes, outcome) == cause
