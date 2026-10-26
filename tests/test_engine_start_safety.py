"""Engine start safety for a second broker (trading-safety review of 10a18031, vantage-demo):

* M1: a demo or live account with no bridge secrets and no local terminal never falls through to the paper broker
  (fake fills keyed by the account's broker would reach its costs and evidence, and its £ daily limit would be dropped).
  The paper stand-in is a developer opt-in only (GOLDBOT_PAPER_ENGINE=1).
* M2: the research release bars are stored under the canonical symbol (XAUUSD); a broker trading a suffixed symbol
  (XAUUSD+) reads them under it (settings `brokers.<b>.symbol_map`), so its warm start is the full window, while its
  orders stay on the broker's symbol.
* LOW: the engine's price maths assumes a $0.01 point and 2-decimal prices (spread points, stop/target rounding, the
  gate's point); a terminal symbol that says otherwise refuses the start instead of mis-pricing orders."""
from __future__ import annotations

import json
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.execution.broker import SymbolInfo
from goldbot.ops import accounts
from goldbot.ops import run as run_mod
from goldbot.ops.services import engine as engine_service
from tests.test_engine_history_gap import LIVE_FROM, TICKS, _engine
from tests.test_engine_watchdog import ACC


class _Built(Exception):
    pass


def _start(tmp_path, monkeypatch, acc=ACC) -> list[Any]:
    import goldbot.engine as engine_pkg
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(accounts, "load_accounts", lambda path=None: {acc.account_id: acc})
    monkeypatch.setattr(accounts, "bridge_endpoint", lambda aid: None)
    monkeypatch.setattr(run_mod.sys, "platform", "linux")          # no local terminal on this host
    built: list[Any] = []

    def capture(cfg: Any, *a: Any, **k: Any) -> Any:
        built.append(cfg)
        raise _Built

    monkeypatch.setattr(engine_pkg, "Engine", capture)
    return built


# ------------------------------------------------------------------------------------------------ M1
def test_an_account_without_a_bridge_or_a_terminal_refuses_to_start_instead_of_trading_paper(tmp_path, monkeypatch):
    monkeypatch.delenv("GOLDBOT_PAPER_ENGINE", raising=False)
    built = _start(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="no MT5 bridge"):
        run_mod.run_engine("icm-demo")
    assert built == []
    e = json.loads((tmp_path / "state" / "engine_icm-demo.json").read_text())
    assert "no MT5 bridge" in e["startup_error"]


def test_the_paper_stand_in_runs_only_on_the_explicit_developer_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDBOT_PAPER_ENGINE", "1")
    built = _start(tmp_path, monkeypatch)
    with pytest.raises(_Built):
        run_mod.run_engine("icm-demo")
    assert len(built) == 1


# ------------------------------------------------------------------------------------------------ M2
def test_a_suffixed_broker_symbol_reads_the_release_under_the_canonical_symbol():
    s = load_settings()
    vantage = ACC.model_copy(update={"account_id": "vantage-demo", "broker": "vantage", "symbol": "XAUUSD+"})
    assert engine_service.release_symbol(s, vantage) == "XAUUSD"
    assert engine_service.release_symbol(s, ACC) == "XAUUSD"
    other = ACC.model_copy(update={"broker": "nobody", "symbol": "GOLD"})
    assert engine_service.release_symbol(s, other) == "GOLD"                     # unmapped: its own symbol


def test_the_engine_service_builds_the_engine_with_the_release_symbol(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDBOT_PAPER_ENGINE", "1")
    vantage = ACC.model_copy(update={"account_id": "vantage-demo", "broker": "vantage", "symbol": "XAUUSD+",
                                     "magic_base": 260200})
    built = _start(tmp_path, monkeypatch, vantage)
    with pytest.raises(_Built):
        run_mod.run_engine("vantage-demo")
    [cfg] = built
    assert cfg.symbol == "XAUUSD+" and cfg.release_symbol == "XAUUSD"


def test_warm_start_on_a_suffixed_symbol_loads_the_release_minutes(tmp_path):
    Store(tmp_path / "data").append("bars_1m", ticks_to_1m(TICKS[TICKS["ts_utc"] < LIVE_FROM]), source="dukascopy")
    eng = _engine(tmp_path, symbol="XAUUSD+", release_symbol="XAUUSD")
    assert eng.warm_start(LIVE_FROM) > 0
    assert pd.Timestamp(eng.bars_1m["ts_utc"].iloc[-1]) < LIVE_FROM
    unmapped = _engine(tmp_path / "x", symbol="XAUUSD+", release_symbol="XAUUSD+")
    Store(tmp_path / "x" / "data").append("bars_1m", ticks_to_1m(TICKS[TICKS["ts_utc"] < LIVE_FROM]),
                                          source="dukascopy")
    assert unmapped.warm_start(LIVE_FROM) == 0                                 # the old behaviour this fixes


# ------------------------------------------------------------------------------------------------ LOW
def _info(**kw: Any) -> SymbolInfo:
    base: dict[str, Any] = dict(name="XAUUSD+", digits=2, point=0.01, contract_size=100.0, volume_min=0.01,
                                volume_step=0.01, volume_max=50.0, stops_level_points=0.0, filling_modes=["IOC"],
                                trade_allowed=True)
    return SymbolInfo(**{**base, **kw})


def test_a_symbol_with_the_points_and_digits_the_engine_prices_in_is_accepted():
    assert engine_service.symbol_terms_problem(_info()) is None


@pytest.mark.parametrize("kw", [{"digits": 3, "point": 0.001}, {"digits": 2, "point": 0.1}, {"digits": 1}])
def test_a_symbol_quoted_in_other_points_or_digits_is_named(kw):
    why = engine_service.symbol_terms_problem(_info(**kw)) or ""
    assert "point" in why and "digits" in why, why


def test_the_engine_refuses_to_start_on_a_terminal_symbol_it_would_misprice(tmp_path, monkeypatch):
    from goldbot.execution.paper import PaperBroker
    monkeypatch.setenv("GOLDBOT_PAPER_ENGINE", "1")
    built = _start(tmp_path, monkeypatch)
    monkeypatch.setattr(PaperBroker, "symbol_info", lambda self, sym: _info(digits=3, point=0.001))
    with pytest.raises(SystemExit, match="digits"):
        run_mod.run_engine("icm-demo")
    assert built == []
