"""The dashboard's multi-instrument views (lane L4; multi-instrument UI spec 2026-11-02, goldbot/api/instruments_view.py).

Design rules quoted from the spec: "Every price via fmtPrice(symbol, x) with the instrument's digits + unit" (so the
API says each instrument's digits and unit), "never sum money across accounts", groups "Gold·Metals·Equity·FX·
Energy·Rates·Ags" in a fixed order "incl. zero", "server facet of symbols with trades", "single-instrument account
shows no movers". A record without a symbol is the account's gold: every view stays as it was for XAUUSD."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api import instruments_view as iv
from goldbot.api import trades_ledger as tl
from goldbot.api.app import create_app
from goldbot.api.schema import TradeKind, TradeRow
from goldbot.instruments import load_instruments
from goldbot.ops import accounts as acc_mod
from tests.test_api_views import _login

NOW = pd.Timestamp("2026-11-02 12:00", tz="UTC")
DAY0 = pd.Timestamp("2026-11-01 22:00", tz="UTC")


def _row(i: int, *, account: str = "icm-probe", symbol: str = "XAUUSD", status: Literal["open", "closed"] = "closed",
         pnl: float | None = 1.0, pnl_now: float | None = None, kind: TradeKind = "exploration") -> TradeRow:
    closed = status == "closed"
    return TradeRow.model_construct(
        trade_id=f"exploration:{account}:{i}", kind=kind, status=status, account=account, account_label="Scalp lab",
        mode="demo", family="random_control", family_label="Control", variant="5m", timeframe="5m", arm_id="S0",
        is_control=True, side="long", lots=0.01, entry=1.0, stop=0.9, stop_now=None, target=1.1,
        exit=1.1 if closed else None, exit_utc=NOW - pd.Timedelta(minutes=i) if closed else None,
        exit_reason="target" if closed else None, censored=False, opened_utc=NOW - pd.Timedelta(minutes=i + 5),
        held_s=300.0, hold_limit_s=3600.0, risk_amount=1.0, currency="GBP", pnl=pnl if closed else None, r=None,
        gross=None, pnl_now=pnl_now, r_now=None, price_now=None, costs=None, why="", evidence="counted",
        excluded_reason=None, stale=False, symbol=symbol)


# ------------------------------------------------------------------------------------------------ keys and labels
@pytest.mark.parametrize(("raw", "key"), [(None, "XAUUSD"), ("", "XAUUSD"), ("XAUUSD", "XAUUSD"),
                                          ("XAUUSD+", "XAUUSD"), ("XAUUSD247", "XAUUSD"), ("COPPER-C", "COPPER"),
                                          ("USNote10Y", "UST10Y"), ("US500", "US500"), ("ODD1", "ODD1")])
def test_a_records_symbol_maps_to_its_instrument_key_and_none_is_gold(raw, key):
    assert iv.instrument_key(raw) == key


def test_every_registry_instrument_has_a_label_and_gold_alone_of_the_non_metals_says_per_ounce():
    reg = load_instruments()
    assert set(reg.instruments) <= set(iv.LABELS)
    per_oz = {k for k, (_, unit) in iv.LABELS.items() if unit.endswith("/oz")}
    assert per_oz == {"XAUUSD", "XAGUSD", "XPTUSD", "XAUEUR", "XAUAUD"}
    assert all(reg.get(k).correlation_group in ("gold", "metals") for k in per_oz)


# ------------------------------------------------------------------------------------------------ engine reports
def test_entry_symbols_reads_the_engines_per_symbol_report_and_drops_what_it_cannot_read():
    e = {"symbols": {"US500": {"state": "market_closed", "text": "Market closed", "clears_at": "2026-11-02T14:30:00Z"},
                     "COPPER-C": {"state": "terms_missing"},
                     "XAGUSD": {"state": "exploding"}, "EURUSD": "can_enter"}}
    got = iv.entry_symbols(e)
    assert [(s.symbol, s.state) for s in got] == [("COPPER", "terms_missing"), ("US500", "market_closed")]
    assert got[1].clears_at is not None and got[1].clears_at.hour == 14 and got[0].text == "terms missing"
    assert iv.entry_symbols({}) == [] and iv.entry_symbols({"symbols": []}) == []


def test_group_exposure_lists_every_group_in_the_fixed_order_including_zero_ones():
    e = {"groups": {"fx": {"notional": 1200.5, "cap": 3000.0, "lots_by_symbol": {"EURUSD": 0.01, "USDJPY": 0.0},
                           "open_n": 1},
                    "equity": {"notional": 0.0, "cap": None}}, "heat_used": 12.0, "heat_limit": 50.0}
    groups, used, limit = iv.group_exposure(e)
    assert [g.group for g in groups] == ["gold", "metals", "equity", "fx", "energy", "rates", "ags"]
    fx = groups[3]
    assert (fx.notional, fx.cap, fx.lots_by_symbol, fx.open_n) == (1200.5, 3000.0, {"EURUSD": 0.01}, 1)
    assert groups[2].cap is None and groups[0].notional == 0.0 and (used, limit) == (12.0, 50.0)
    assert iv.group_exposure({"ts": 1}) == ([], None, None)


# ------------------------------------------------------------------------------------------------ Today by instrument
def test_by_symbol_sums_one_accounts_day_per_instrument_never_another_accounts():
    rows = [_row(1, symbol="US500", pnl=2.10), _row(2, symbol="US500", pnl=-0.60),
            _row(3, symbol="EURUSD", status="open", pnl_now=-3.0), _row(4, symbol="XAUUSD", pnl=0.5),
            _row(5, account="icm-demo", symbol="US500", pnl=100.0),
            _row(60 * 24, symbol="US500", pnl=9.0)]                       # closed yesterday: not today's
    got = {d.symbol: d for d in iv.by_symbol(rows, "icm-probe", DAY0)}
    assert (got["US500"].closed_pnl, got["US500"].closed_n, got["US500"].day_pnl) == (1.5, 2, 1.5)
    assert (got["EURUSD"].open_pnl, got["EURUSD"].open_n, got["EURUSD"].day_pnl) == (-3.0, 1, -3.0)
    assert [d.symbol for d in iv.by_symbol(rows, "icm-probe", DAY0)] == ["EURUSD", "US500", "XAUUSD"]   # by |P&L|


def test_an_unknown_net_makes_its_symbols_figure_none_never_a_smaller_number():
    got = iv.by_symbol([_row(1, symbol="US500", pnl=2.0), _row(2, symbol="US500", pnl=None)], "icm-probe", DAY0)
    assert got[0].closed_pnl is None and got[0].day_pnl is None and got[0].closed_n == 2


# ------------------------------------------------------------------------------------------------ the trade list
def _ledger() -> tl.Ledger:
    rows = [_row(1, symbol="US500"), _row(2, symbol="EURUSD"), _row(3, symbol="XAUUSD"), _row(4, symbol="XAGUSD"),
            _row(5, symbol="COPPER")]
    return tl.Ledger(as_of=NOW, rows=rows, unreadable=[])


def test_the_list_filters_by_symbol_and_by_group_and_the_facet_ignores_both():
    led = _ledger()
    page = tl.query(led, tl.TradeQuery(period="today", symbol=("US500",)))
    assert [r.symbol for r in page.rows] == ["US500"]
    assert page.symbols == ["COPPER", "EURUSD", "US500", "XAGUSD", "XAUUSD"]      # what the panel can offer
    metals = tl.query(led, tl.TradeQuery(period="today", group=("metals",)))
    assert sorted(r.symbol for r in metals.rows) == ["COPPER", "XAGUSD"]
    both = tl.query(led, tl.TradeQuery(period="today", group=("gold",), symbol=("US500",)))
    assert sorted(r.symbol for r in both.rows) == ["US500", "XAUUSD"]           # a group OR its picked symbols
    assert tl.query(led, tl.TradeQuery(period="today", symbol=("USDJPY",))).rows == []


def test_a_list_without_symbol_filters_is_unchanged():
    led = _ledger()
    assert len(tl.query(led, tl.TradeQuery(period="today")).rows) == 5


# ------------------------------------------------------------------------------------------------ /api/instruments
def _probe(**kw: Any) -> acc_mod.Account:
    acc = acc_mod.load_accounts(logins=False)["icm-probe"]
    object.__setattr__(acc, "instruments", kw.get("instruments", ()))
    return acc


def test_instruments_lists_each_accounts_symbols_with_digits_unit_group_and_the_engines_state():
    reg = load_instruments()
    reg = reg.model_copy(update={"instruments": {k: v.model_copy(update={"enabled": True})
                                                 for k, v in reg.instruments.items()}})
    acc = _probe(instruments=("USDJPY", "EURUSD", "US500"))
    engine = {"account_id": "icm-probe", "symbols": {"US500": {"state": "market_closed"},
                                                     "XAUUSD": {"state": "can_enter"}}}
    got = {r.symbol: r for r in iv.instruments([acc], [engine], reg)}
    assert list(got) == ["XAUUSD", "USDJPY", "EURUSD", "US500"]
    assert (got["USDJPY"].digits, got["EURUSD"].digits, got["US500"].digits) == (3, 5, 2)
    assert (got["US500"].unit, got["XAUUSD"].unit, got["EURUSD"].unit) == ("pts", "USD/oz", "")
    assert (got["US500"].state, got["XAUUSD"].state, got["USDJPY"].state) == ("market_closed", "can_enter", "unknown")
    assert got["USDJPY"].group == "fx" and got["USDJPY"].quote_ccy == "JPY" and got["US500"].account == "icm-probe"


@pytest.fixture
def client(tmp_path: Path) -> Any:
    state = tmp_path / "state"
    state.mkdir()
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", owner_email="o@x.io")
    c = TestClient(app)
    return c, _login(c, app), state


def test_the_instruments_route_needs_a_session_and_lists_gold_and_only_the_confirmed_instruments(client):
    """Gold for every enabled account, plus each instrument an account lists that the symbol check confirmed
    (`enabled: true`); an unconfirmed one (XAGUSD, `verify: true`) is never listed. Digits are the registry's."""
    c, h, state = client
    assert c.get("/api/instruments").status_code == 401
    (state / "engine_icm-demo.json").write_text(json.dumps({"account_id": "icm-demo", "ts": time.time()}))
    rows = c.get("/api/instruments", headers=h["viewer"]).json()
    reg = load_instruments()
    gold = [r for r in rows if r["symbol"] == "XAUUSD"]
    assert gold and all(r["digits"] == 2 and r["unit"] == "USD/oz" for r in gold)
    assert {r["symbol"] for r in rows} <= {k for k, s in reg.instruments.items() if s.enabled}
    assert all(r["digits"] == reg.get(r["symbol"]).digits for r in rows) and "XAGUSD" not in {r["symbol"] for r in rows}


def test_the_trades_route_takes_symbol_and_group_filters_and_answers_the_facet(client):
    c, h, _ = client
    page = c.get("/api/trades?period=today&symbol=US500&group=fx", headers=h["viewer"]).json()
    assert page["rows"] == [] and page["symbols"] == []
    assert c.get("/api/trades?group=bonds", headers=h["viewer"]).status_code == 422


# ------------------------------------------------------------------------------------------------ the routes carry them
def test_the_limits_route_carries_each_groups_exposure_and_the_heat(client):
    from tests.test_api_limits import engine_file
    c, h, state = client
    engine_file(state, groups={"energy": {"notional": 800.0, "cap": 2000.0, "lots_by_symbol": {"XTIUSD": 0.01},
                                          "open_n": 1}}, heat_used=4.2, heat_limit=50.0)
    (a,) = c.get("/api/limits", headers=h["viewer"]).json()["accounts"]
    assert [g["group"] for g in a["groups"]][4] == "energy" and a["groups"][4]["notional"] == 800.0
    assert (a["heat_used"], a["heat_limit"]) == (4.2, 50.0)


def test_a_single_symbol_engines_limits_carry_no_groups(client):
    from tests.test_api_limits import engine_file
    c, h, state = client
    engine_file(state)
    (a,) = c.get("/api/limits", headers=h["viewer"]).json()["accounts"]
    assert a["groups"] == [] and a["heat_used"] is None


def test_an_open_non_gold_position_is_never_priced_at_the_accounts_gold_price():
    from goldbot.api import home_cards
    from goldbot.engine.store import OpenTrade
    base = dict(position_id=7, agent_id="exp_S0_5m", side=1, lots=0.1, entry=5000.0, sl=4990.0, tp=5020.0,
                timeframe="5m", max_bars=12, entry_bar_ts=NOW - pd.Timedelta(minutes=5))
    t = OpenTrade.model_validate(base)
    object.__setattr__(t, "symbol", "US500")
    e = {"price": 2400.0, "currency": "GBP", "ts": NOW.timestamp(), "usd_to_account": 0.8,
         "usd_to_account_utc": NOW.isoformat(), "lot_terms": {"contract_oz": 100}}
    p = home_cards._position("icm-probe", t, e, NOW)
    assert p.symbol == "US500" and p.floating_pnl is None and p.price is None and p.stale
    e["symbols"] = {"US500": {"state": "can_enter", "price": 5010.0, "lot_value": 0.8}}
    p = home_cards._position("icm-probe", t, e, NOW)
    assert (p.price, p.floating_pnl, p.risk_amount) == (5010.0, 0.8, 0.8)    # 10 pts x 0.1 lot x 0.8 GBP/pt/lot


def test_the_entry_status_lists_each_symbols_state_only_on_a_multi_symbol_engine():
    from goldbot.api import entry_status as es
    from goldbot.risk.gate import RiskLimits
    e = {"account_id": "icm-probe", "ts": NOW.timestamp(), "mode": "demo",
         "symbols": {"US500": {"state": "group_cap", "text": "Equity cap spent"}}}
    s = es.account_entry_status(e, now=NOW, owner_halt=None, supervisor_reasons=None, drift_reasons=None, budget=None,
                                limits=RiskLimits())
    assert [(x.symbol, x.state, x.text) for x in s.symbols] == [("US500", "group_cap", "Equity cap spent")]
    del e["symbols"]
    assert es.account_entry_status(e, now=NOW, owner_halt=None, supervisor_reasons=None, drift_reasons=None,
                                   budget=None, limits=RiskLimits()).symbols == []


def test_the_dashboards_snapshot_of_the_terms_matches_the_registry_and_the_apis_words():
    """web/src/lib/instruments.ts TERMS renders prices before /api/instruments answers: its digits, units, words and
    groups must be the registry's and this module's, or a price would show in the wrong digits until the first poll."""
    import re
    src = (Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "instruments.ts").read_text()
    terms = {m[0]: (int(m[1]), m[2], m[3], m[4])
             for m in re.findall(r'(\w+): T\((\d+), "([^"]*)", "([^"]*)", "(\w+)"\)', src)}
    reg = load_instruments()
    assert set(terms) == set(reg.instruments)
    for key, (digits, unit, label, group) in terms.items():
        spec = reg.get(key)
        assert (digits, group) == (spec.digits, spec.correlation_group), key
        assert (label, unit) == iv.LABELS[key], key
