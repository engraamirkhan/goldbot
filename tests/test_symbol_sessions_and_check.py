"""Per-symbol sessions from the broker and the symbol check per symbol (lane L4, asked by L2 in PR #307).

Design rules: "Sessions per asset class checked vs broker trading_sessions" (plan); the MetaTrader5 Python package has
no session schedule (mt5_adapter docstring), so for an instrument other than the account's gold the adapter reports the
hours the broker actually quoted: the minutes with an M1 bar over the last two weeks, per weekday from the latest day
of that weekday that traded, short gaps (thin minutes) merged. Gold's call is unchanged (the documented hours). The
symbol check runs per symbol of the account's list: listed, terms against the registry, trading enabled, and where the
broker's hours and the instrument's session profile differ (calendar.profile_disagreement, reported, never a FAIL:
the engine intersects the two)."""
from __future__ import annotations

from collections.abc import Set as AbstractSet
from datetime import date
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.calendar import TradingSession
from goldbot.execution import mt5_adapter
from goldbot.execution.broker import SymbolInfo
from goldbot.ops import accounts as acc_mod
from goldbot.ops import symbol_check

TZ = "Europe/Athens"
NOW = pd.Timestamp("2026-11-04 12:00", tz="UTC")          # Wednesday


def _rates(days: list[str], spans: list[tuple[str, str]], skip: AbstractSet[str] = frozenset()) -> np.ndarray:
    """M1 bars (server wall clock) on each of `days` over each [start, end) of `spans`, minus the minutes in `skip`."""
    ts = []
    for d in days:
        for a, b in spans:
            for t in pd.date_range(f"{d} {a}", f"{d} {b}", freq="1min", inclusive="left"):
                if t.strftime("%H:%M") not in skip:
                    ts.append(int(t.timestamp()))
    out = np.zeros(len(ts), dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                                   ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")])
    out["time"] = ts
    return out


def _opens(rates: np.ndarray) -> pd.Series:
    """The bars' open times as the adapter reads them (server wall clock, naive)."""
    return pd.Series(pd.to_datetime([int(t) for t in rates["time"]], unit="s"))


def test_the_observed_template_merges_thin_gaps_and_keeps_real_breaks():
    minutes = _opens(_rates(["2026-11-02"], [("01:10", "22:55")], skip={"03:00", "03:01"}))
    tpl = mt5_adapter.observed_template(minutes)
    assert tpl == [(0, 1 * 3600 + 600, 22 * 3600 + 55 * 60)]                # a 2-minute hole is a thin market, open
    minutes = _opens(_rates(["2026-11-02"], [("01:10", "08:40"), ("11:05", "22:55")]))
    assert len(mt5_adapter.observed_template(minutes)) == 2                  # Osaka's midday break stays closed


def test_each_weekday_takes_its_latest_trading_day_and_a_quiet_weekend_is_closed():
    days = ["2026-10-26", "2026-10-27", "2026-11-02", "2026-11-03"]          # two Mondays and two Tuesdays
    minutes = pd.concat([_opens(_rates(days[:2], [("02:00", "10:00")])), _opens(_rates(days[2:], [("03:00", "09:00")]))])
    tpl = dict(((wd, (a, b)) for wd, a, b in mt5_adapter.observed_template(minutes)))
    assert tpl == {0: (3 * 3600, 9 * 3600), 1: (3 * 3600, 9 * 3600)}


def _fake(rates: np.ndarray | None) -> SimpleNamespace:
    return SimpleNamespace(initialize=lambda **k: True, symbol_select=lambda s, on: True,
                           symbol_info=lambda s: SimpleNamespace(visible=True), last_error=lambda: (1, "err"),
                           shutdown=lambda: None, copy_rates_range=lambda s, tf, a, b: rates, TIMEFRAME_M1=1)


def _adapter(monkeypatch: pytest.MonkeyPatch, rates: np.ndarray | None) -> Any:
    monkeypatch.setattr(mt5_adapter, "mt5", _fake(rates))
    return mt5_adapter.MT5Broker(terminal_path="", login=None, password=None, server="s", server_tz=TZ,
                                 symbol="XAUUSD", account_label="icm-probe", verify=None)


def test_another_symbols_sessions_are_the_hours_the_broker_quoted_it(monkeypatch):
    days = [str(d.date()) for d in pd.date_range("2026-10-26", "2026-11-03") if d.weekday() < 5]
    b = _adapter(monkeypatch, _rates(days, [("16:30", "23:00")]))
    got = b.trading_sessions("US500")
    assert got and all(isinstance(s, TradingSession) for s in got)
    assert {s.weekday for s in got} == {0, 1, 2, 3, 4} and len({s.server_date for s in got}) == 5
    wed = next(s for s in got if s.weekday == 2)
    start = pd.Timestamp(f"{wed.server_date} 16:30").tz_localize(TZ).tz_convert("UTC")      # whatever the DST
    assert (wed.start_utc.hour, wed.start_utc.minute) == (start.hour, start.minute)


def test_gold_keeps_its_documented_hours_and_no_bars_is_an_error_not_always_open(monkeypatch):
    b = _adapter(monkeypatch, None)
    assert b.trading_sessions("XAUUSD")                                      # unchanged path, no bars read
    with pytest.raises(RuntimeError, match="no M1 bars"):
        b.trading_sessions("US500")


# ------------------------------------------------------------------------------------------------ the symbol check
def _info(name: str, **kw: Any) -> SymbolInfo:
    base: dict[str, Any] = dict(name=name, digits=2, point=0.01, contract_size=1.0, volume_min=0.1, volume_step=0.1,
                                volume_max=50.0, stops_level_points=0.0, filling_modes=["IOC"], trade_allowed=True,
                                currency_profit="USD", swap_rollover3days=5)
    return SymbolInfo(**{**base, **kw})


class _Broker:
    def __init__(self, infos: dict[str, SymbolInfo], sessions: list[TradingSession] | Exception) -> None:
        self.infos, self.sessions = infos, sessions

    def symbols_get(self, pattern: str = "*") -> list[str]:
        return [n for n in self.infos if n.startswith(pattern.rstrip("*"))]

    def symbol_info(self, s: str) -> SymbolInfo:
        if s not in self.infos:
            raise RuntimeError("symbol_info None")
        return self.infos[s]

    def trading_sessions(self, s: str) -> list[TradingSession]:
        if isinstance(self.sessions, Exception):
            raise self.sessions
        return self.sessions


def _probe() -> acc_mod.Account:
    acc = acc_mod.load_accounts(logins=False)["icm-probe"]
    object.__setattr__(acc, "instruments", ("US500", "EURUSD"))
    return acc


def _enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from goldbot.instruments import load_instruments
    reg = load_instruments()
    reg = reg.model_copy(update={"instruments": {k: v.model_copy(update={"enabled": True})
                                                 for k, v in reg.instruments.items()}})
    monkeypatch.setattr(symbol_check, "load_instruments", lambda: reg)


def test_each_symbol_of_the_list_is_checked_against_the_registry(monkeypatch):
    _enabled(monkeypatch)
    broker = _Broker({"XAUUSD": _info("XAUUSD", contract_size=100.0), "US500": _info("US500", digits=1),
                      "EURUSD": _info("EURUSD", digits=5, point=0.00001, contract_size=100000.0, trade_allowed=False,
                                      swap_rollover3days=3)}, sessions=RuntimeError("no M1 bars"))
    rep = symbol_check.check_instruments(_probe(), broker)
    assert set(rep) == {"US500", "EURUSD"}
    assert any("digits 1 on the terminal, 2 in config/instruments.yaml" in p for p in rep["US500"].problems)
    assert any("trading disabled" in p for p in rep["EURUSD"].problems)
    assert any("sessions not read" in n for n in rep["US500"].notes)


def test_a_symbol_the_terminal_does_not_list_is_a_problem_and_the_others_are_still_checked(monkeypatch):
    _enabled(monkeypatch)
    broker = _Broker({"XAUUSD": _info("XAUUSD", contract_size=100.0),
                      "EURUSD": _info("EURUSD", digits=5, point=0.00001, contract_size=100000.0, swap_rollover3days=3, volume_min=0.01, volume_step=0.01)}, sessions=[])
    rep = symbol_check.check_instruments(_probe(), broker)
    assert rep["US500"].problems == ["US500 is not listed by the terminal of icm-probe"]
    assert rep["EURUSD"].problems == []


def test_hours_that_differ_from_the_profile_are_reported_as_notes_never_problems(monkeypatch):
    _enabled(monkeypatch)
    from goldbot.data.calendar import resolve_server_sessions
    always = resolve_server_sessions([(wd, 0, 86_400) for wd in range(5)], TZ, date(2026, 11, 4))
    broker = _Broker({"XAUUSD": _info("XAUUSD", contract_size=100.0), "US500": _info("US500"),
                      "EURUSD": _info("EURUSD", digits=5, point=0.00001, contract_size=100000.0, swap_rollover3days=3, volume_min=0.01, volume_step=0.01)}, always)
    rep = symbol_check.check_instruments(_probe(), broker)
    assert rep["US500"].problems == [] and any("index_us profile" in n or "broker open" in n for n in rep["US500"].notes)


# ------------------------------------------------------------------- incident 2026-11-02: today's bars end at "now"
def test_todays_unfinished_server_day_never_sets_that_weekdays_hours():
    """Read at 20:38 server time on a Monday, today's bars stop at 20:38: taken as Monday's hours, every symbol read
    "market closed" from a minute after the read (all 14 open ICM books and 7 Vantage books on 2026-11-02). The
    template uses complete server days only: the Monday before."""
    minutes = pd.concat([_opens(_rates(["2026-10-26"], [("00:05", "23:55")])),
                         _opens(_rates(["2026-11-02"], [("00:05", "20:38")]))])
    assert mt5_adapter.observed_template(minutes) == [(0, 5 * 60, 20 * 3600 + 38 * 60)]       # the trap
    assert mt5_adapter.observed_template(minutes, before=date(2026, 11, 2)) == [(0, 5 * 60, 23 * 3600 + 55 * 60)]


def test_trading_sessions_reads_complete_days_only(monkeypatch):
    days = [str(d.date()) for d in pd.date_range("2026-10-26", "2026-10-30")]
    rates = np.concatenate([_rates(days, [("00:05", "23:55")]), _rates(["2026-11-02"], [("00:05", "20:38")])])
    b = _adapter(monkeypatch, rates)
    now = pd.Timestamp("2026-11-02 18:38", tz="UTC")                                        # 20:38 in Athens
    monkeypatch.setattr(mt5_adapter, "_utcnow", lambda: now)
    mon = next(s for s in b.trading_sessions("EURUSD") if s.server_date == date(2026, 11, 2))
    end = pd.Timestamp("2026-11-02 23:55").tz_localize(TZ).tz_convert("UTC")
    assert (mon.end_utc.hour, mon.end_utc.minute) == (end.hour, end.minute)


def test_only_todays_bars_is_no_template_rather_than_a_closing_market(monkeypatch):
    b = _adapter(monkeypatch, _rates(["2026-11-02"], [("00:05", "20:38")]))
    monkeypatch.setattr(mt5_adapter, "_utcnow", lambda: pd.Timestamp("2026-11-02 18:38", tz="UTC"))
    with pytest.raises(RuntimeError, match="no M1 bars"):                  # the engine keeps its static profile
        b.trading_sessions("EURUSD")
