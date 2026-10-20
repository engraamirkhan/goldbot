"""The approval card's money lines (owner request): risk in the account currency with its share of equity, the
minimum-lot exception note, today's loss budget left if the trade loses, and the stop and target in money. Same
essentials in the Telegram text and in the dashboard's /api/proposals."""
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from goldbot.api.app import State, create_app
from goldbot.ops.account_view import loss_budget, proposal_budget, proposal_budget_from_state
from goldbot.telegram.approvals import Proposal, cash, ceil_cent, pct
from goldbot.telegram.bus import ApprovalBus
from tests.cards import submit_shown
from tests.test_api import _owner_headers

LIMITS = {"risk_per_trade": 0.005, "max_risk_per_trade": 0.01, "multiplier_bounds": [0.25, 1.5], "daily_cap": 0.02,
          "weekly_cap": 0.05}


def _prop(**kw: Any) -> Proposal:
    base: dict[str, Any] = dict(proposal_id="icm-1-a", account_id="icm-demo", agent_id="trend-g0-x", side=-1, lots=0.01,
                                entry=2400.2, stop=2404.2, target=2394.2, p=0.62, ev_r=0.31, spread_points=22,
                                top_features=[("adx14", 31.2)], risk_usd=4.0, created=time.time())
    base.update(kw)
    return Proposal(**base)


GBP_MIN_LOT = dict(currency="GBP", risk_amount=8.2, risk_fraction=0.0082, min_lot_exception=True, risk_normal=0.005,
                   risk_cap=0.01)


def _engine(**over: Any) -> dict[str, Any]:
    e = {"account": "icm-demo", "equity": 1000.0, "balance": 1000.0, "currency": "GBP", "day_start_equity": 1000.0,
         "week_start_equity": 1000.0, "stage": "normal", "limits": dict(LIMITS)}
    e.update(over)
    return e


def _sup() -> dict[str, Any]:
    """A fresh supervisor report whose combined caps have room to spare (the account's caps bind)."""
    return {"ts": time.time(), "daily_cap": 0.015, "combined_day_start_equity": 10_000.0, "weekly_cap": 0.04,
            "combined_week_start_equity": 10_000.0, "combined_equity": 10_000.0}


# ---------------------------------------------------------------------------------------------- formatting
def test_cash_and_percent_read_as_the_owner_writes_them():
    assert cash(8.2, "GBP") == "£8.20" and cash(1234.5, "USD") == "$1,234.50" and cash(3, "EUR") == "€3.00"
    assert cash(8.2, "CHF") == "8.20 CHF" and cash(8.2, None) == "8.20"
    assert pct(0.0082) == "0.82%" and pct(0.005) == "0.5%" and pct(0.01) == "1%"


def test_reward_scales_the_risk_by_target_over_stop_distance():
    p = _prop(**GBP_MIN_LOT)
    assert p.reward_amount == pytest.approx(12.30)
    assert _prop(risk_usd=None).reward_amount is None
    assert _prop(stop=2400.2, **GBP_MIN_LOT).reward_amount is None          # no stop distance: no ratio


# ---------------------------------------------------------------------------------------------- loss budget
def test_the_budget_if_this_trade_loses_takes_its_risk_from_every_cap():
    b = loss_budget(_engine(), {}, [_engine()], trade_risk=8.2)
    assert b is not None and b.remaining == pytest.approx(11.80) and b.period == "today"
    assert {c.name: round(c.remaining, 2) for c in b.caps} == {"account 2.0% daily cap": 11.8,
                                                                "account 5.0% weekly cap": 41.8}
    # the absolute limit reserves this trade's stop, not the smallest trade's
    e = _engine(limits={**LIMITS, "daily_loss_limit_abs": 15.0, "min_lot_risk_account": 4.0})
    b = loss_budget(e, {}, [e], trade_risk=8.2)
    assert b is not None and b.binding == "absolute daily limit" and b.remaining == pytest.approx(6.80)
    assert "smallest trade" not in b.binding_label
    spent = loss_budget(_engine(equity=985.0), {}, [_engine()], trade_risk=8.2)
    assert spent is not None and spent.remaining == 0.0                                    # floored at 0


def test_proposal_budget_finds_the_account_and_never_guesses():
    engines = [_engine(account="other", equity=500.0), _engine()]
    b = proposal_budget(engines, _sup(), "icm-demo", 8.2)
    assert b is not None and b.remaining == pytest.approx(11.80)
    for sup in ({}, {**_sup(), "ts": time.time() - 600}):         # missing, stale: can't check, never the 2% cap
        b = proposal_budget(engines, sup, "icm-demo", 8.2)
        assert b is not None and b.unknown is not None and b.unknown_paused
    assert proposal_budget(engines, {}, "missing", 8.2) is None
    assert proposal_budget(engines, {}, "icm-demo", None) is None
    assert proposal_budget([{"account": "icm-demo", "limits": "junk"}], {}, "icm-demo", 8.2) is None


def test_the_telegram_service_reads_the_budget_from_the_state_files_and_unknown_when_unreadable(tmp_path):
    assert proposal_budget_from_state(tmp_path, "icm-demo", 8.2) is None                  # no engine file yet
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(_engine()))
    b = proposal_budget_from_state(tmp_path, "icm-demo", 8.2)                             # no supervisor report yet
    assert b is not None and b.unknown_paused
    (tmp_path / "supervisor.json").write_text(json.dumps(_sup()))
    b = proposal_budget_from_state(tmp_path, "icm-demo", 8.2)
    assert b is not None and b.remaining == pytest.approx(11.80)
    (tmp_path / "supervisor.json").write_text("{torn")                     # the combined cap can't be checked
    b = proposal_budget_from_state(tmp_path, "icm-demo", 8.2)
    assert b is not None and b.unknown_paused
    (tmp_path / "supervisor.json").write_text(json.dumps(_sup()))
    (tmp_path / "engine_icm-demo.json").write_text("{torn")
    assert proposal_budget_from_state(tmp_path, "icm-demo", 8.2) is None                  # never a failed message


# ---------------------------------------------------------------------------------------------- Telegram
def test_telegram_text_shows_money_in_the_account_currency_with_the_min_lot_note_and_budget():
    p = _prop(**GBP_MIN_LOT)
    b = loss_budget(_engine(), _sup(), [_engine()], trade_risk=8.2)
    lines = p.text(b).splitlines()[1:]                                  # [0]: the money class (test_card_account_mode)
    assert lines[0] == "🔵 SHORT 0.01 lots XAUUSD"
    assert lines[1] == "risk £8.20 (0.82% of equity) if the stop is hit, before costs"
    assert lines[2] == "smallest trade size: above the normal 0.5%, within your 1% cap"
    assert lines[3] == "£11.80 left today if this loses (open trades aside)"
    assert lines[4] == "entry 2400.20  stop 2404.20 (−£8.20)  target 2394.20 (+£12.30)"
    assert lines[5] == "p=0.62  EV=+0.31R  spread 22pt"
    assert lines[-1] == "⏱ approve within 90 s or it expires · exits are automatic"


def test_telegram_text_without_the_exception_spent_budget_or_unknown_budget():
    p = _prop(currency="GBP", risk_amount=4.1, risk_fraction=0.0041, risk_normal=0.005)
    txt = p.text()
    assert "smallest trade" not in txt
    assert "loss budget left today: unknown" in txt
    cant = loss_budget(_engine(), {}, [_engine()], trade_risk=4.1)                # supervisor report missing
    assert "loss budget left today: can't check (supervisor not reporting; new entries paused)" in p.text(cant)
    assert not [x for x in p.text(cant).splitlines() if " left " in x and "£" in x]       # no roomier amount
    not_summed = {**_sup(), "combined_day_start_equity": 0.0}
    assert "can't check (the supervisor hasn't added up the accounts yet)" in p.text(
        loss_budget(_engine(), not_summed, [_engine()], trade_risk=4.1))
    spent = loss_budget(_engine(equity=980.0), _sup(), [_engine()], trade_risk=4.1)
    assert "no loss budget left today if this loses (open trades aside): new entries stop" in p.text(spent)
    week = _engine(day_start_equity=995.0, week_start_equity=1045.0)        # up £5 today, down £45 this week
    assert "£3.15 left this week if this loses (open trades aside)" in p.text(loss_budget(week, _sup(), [week], trade_risk=4.1))


def test_telegram_text_from_an_older_engine_labels_the_dollar_figure():
    assert _prop().text().splitlines()[2] == "risk $4.00 (USD) if the stop is hit, before costs"
    assert "stop 2404.20 (−$4.00)" in _prop().text()
    txt = _prop(risk_usd=None).text()
    assert "risk: not reported by this engine version" in txt and "stop 2404.20  target" in txt


# ---------------------------------------------------------------------------------------------- API
def test_api_proposal_carries_currency_risk_min_lot_and_the_live_budget(tmp_path):
    bus = ApprovalBus(tmp_path)
    bus.publish(_prop(**GBP_MIN_LOT))
    bus.publish(_prop(proposal_id="old"))                                       # an older engine: dollars only
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(_engine()))
    (tmp_path / "supervisor.json").write_text(json.dumps(_sup()))
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    h = _owner_headers(c, app)
    rows = {r["proposal_id"]: r for r in c.get("/api/proposals", headers=h).json()}
    r = rows["icm-1-a"]
    assert (r["currency"], r["risk_amount"], r["risk_fraction"]) == ("GBP", 8.2, 0.0082)
    assert (r["min_lot_exception"], r["risk_normal"], r["risk_cap"]) == (True, 0.005, 0.01)
    assert r["reward_amount"] == pytest.approx(12.30)
    assert r["loss_left"] == pytest.approx(11.80) and r["loss_left_period"] == "today"
    old = rows["old"]
    assert old["risk_amount"] is None and old["risk_usd"] == 4.0 and old["loss_left"] is None
    assert old["min_lot_exception"] is False


@pytest.mark.parametrize("halted, want", [(False, "mixed_currencies"), (True, "mixed_currencies_paused")])
def test_api_proposal_with_a_gbp_and_a_usd_engine_says_cant_check_without_an_amount(tmp_path, halted, want,
                                                                                    enabled_accounts):
    """BACKLOG 61: with mixed currencies the combined caps are left out, so the card never shows the account cap's
    roomier figure: "can't check" (amber; red while the supervisor halts on `mixed_account_currencies`)."""
    enabled_accounts("icm-demo", "vantage-demo")
    bus = ApprovalBus(tmp_path)
    bus.publish(_prop(**GBP_MIN_LOT))
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(_engine()))
    (tmp_path / "engine_vantage-demo.json").write_text(json.dumps(_engine(account="vantage-demo", currency="USD")))
    sup = {**_sup(), **({"halt": True, "reasons": ["mixed_account_currencies"]} if halted else {})}
    (tmp_path / "supervisor.json").write_text(json.dumps(sup))
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    [r] = c.get("/api/proposals", headers=_owner_headers(c, app)).json()
    assert (r["loss_left"], r["loss_left_period"], r["loss_cant_check"]) == (None, None, want)


def test_telegram_text_with_mixed_currencies_says_cant_check_with_the_reason():
    p = _prop(currency="GBP", risk_amount=4.1, risk_fraction=0.0041, risk_normal=0.005)
    engines = [_engine(), _engine(account="vantage-demo", currency="USD")]
    amber = loss_budget(_engine(), _sup(), engines, trade_risk=4.1)
    assert "loss budget left today: can't check (the accounts report different currencies)" in p.text(amber)
    red = loss_budget(_engine(), {**_sup(), "halt": True, "reasons": ["mixed_account_currencies"]}, engines,
                      trade_risk=4.1)
    assert ("loss budget left today: can't check (the accounts report different currencies; new entries paused)"
            in p.text(red))
    assert not [x for x in p.text(red).splitlines() if " left " in x and "£" in x]       # no roomier amount


@pytest.mark.parametrize("report, want", [("missing", "paused"), ("stale", "paused"), ("not summed", "not_summed")])
def test_api_proposal_says_the_budget_cant_be_checked_without_an_amount(tmp_path, report, want):
    """Missing or stale supervisor report: paused (red on the card); fresh but not summed yet: amber. Never the
    account cap's roomier figure in loss_left."""
    bus = ApprovalBus(tmp_path)
    bus.publish(_prop(**GBP_MIN_LOT))
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(_engine()))
    if report == "stale":
        (tmp_path / "supervisor.json").write_text(json.dumps({**_sup(), "ts": time.time() - 600}))
    elif report == "not summed":
        (tmp_path / "supervisor.json").write_text(json.dumps({**_sup(), "combined_day_start_equity": 0.0}))
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    [r] = c.get("/api/proposals", headers=_owner_headers(c, app)).json()
    assert (r["loss_left"], r["loss_left_period"], r["loss_cant_check"]) == (None, None, want)


def test_api_decided_cards_carry_no_live_budget(tmp_path):
    bus = ApprovalBus(tmp_path)
    bus.publish(_prop(**GBP_MIN_LOT))
    submit_shown(bus, "icm-1-a", False, "cost", by="dashboard:o@x.io")
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(_engine()))
    (tmp_path / "supervisor.json").write_text(json.dumps(_sup()))
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    rows = c.get("/api/proposals/recent", headers=_owner_headers(c, app)).json()
    assert rows[0]["risk_amount"] == 8.2 and rows[0]["loss_left"] is None


# ---------------------------------------------------------------------------------------------- review fixes
def test_unreadable_engine_files_never_fail_the_approval_list_or_the_accounts(tmp_path, monkeypatch):
    bus = ApprovalBus(tmp_path)
    bus.publish(_prop(**GBP_MIN_LOT))
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(_engine()))
    (tmp_path / "supervisor.json").write_text(json.dumps(_sup()))
    (tmp_path / "engine_broken.json").mkdir()                   # read_text raises IsADirectoryError (an OSError)
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    h = _owner_headers(c, app)
    r = c.get("/api/proposals", headers=h)
    assert r.status_code == 200 and r.json()[0]["loss_left"] == pytest.approx(11.80)    # the bad file is skipped
    assert c.get("/api/accounts", headers=h).status_code == 200

    def unreadable(self: State) -> list[dict]:
        raise OSError("disk gone")
    monkeypatch.setattr(State, "engines", unreadable)
    r = c.get("/api/proposals", headers=h)
    assert r.status_code == 200 and r.json()[0]["loss_left"] is None and r.json()[0]["risk_amount"] == 8.2


def test_the_shown_risk_is_never_below_the_gates():
    assert ceil_cent(8.2012) == 8.21 and ceil_cent(8.21) == 8.21 and ceil_cent(8.2) == 8.2 and ceil_cent(0.0) == 0.0
    assert "risk £8.21 " in _prop(currency="GBP", risk_amount=8.2012, risk_fraction=0.0082).text()
    assert "stop 2404.20 (−£8.21)" in _prop(currency="GBP", risk_amount=8.2012).text()
    # the budget left is shown rounded down: never more room than there is
    b = loss_budget(_engine(), _sup(), [_engine()], trade_risk=8.2012)
    assert b is not None and "£11.79 left today" in _prop(currency="GBP", risk_amount=8.2012).text(b)
