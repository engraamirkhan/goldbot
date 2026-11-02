"""Multi-instrument plan, lane L3 (engine) safety fixes, found by the architect's survey:

* "SAFETY BUG sl/tp round(..., 2) runner.py:1325 and trail runner.py:1473-1474 -> round to info.digits": every price the
  engine sends (an order's stop and target, a trailed stop, an orphan's stop) is rounded to the instrument's own digits
  (`EngineConfig.price_digits`, the verified InstrumentSpec value, checked against the terminal at start). XAUUSD has 2
  digits, so gold is unchanged; a 3-digit silver or a 5-digit FX quote is never cut to the cent.
* "runner.py:971 fallback must REFUSE": a contract size the terminal has never given is unknown, never 100 oz; entries
  are refused (`contract_unknown`, a data-quality error) while every exit runs.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import Position, SymbolInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.specialists.trend import TrendSpecialist
from goldbot.telegram.approvals import Proposal

TREND = TrendSpecialist()
T0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")


class ThreeDigitBroker(PaperBroker):
    """A paper terminal quoting a 3-digit symbol (XAGUSD-like: point 0.001)."""

    def symbol_info(self, symbol: str) -> SymbolInfo:
        return super().symbol_info(symbol).model_copy(update={"digits": 3, "point": 0.001})


class NoTermsBroker(PaperBroker):
    """A terminal that cannot describe the symbol (symbol_info raises)."""

    def symbol_info(self, symbol: str) -> SymbolInfo:
        raise ConnectionError("symbol_info unavailable")


def _engine(tmp_path: Any, pb: PaperBroker, **cfg: Any) -> Engine:
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=111,
                               **cfg), pb, [TREND], {})


def _enter(eng: Engine, pb: PaperBroker, price: float, stop: float, target: float) -> int:
    pb.on_tick(Tick(ts_utc=T0, bid=price, ask=price))
    prop = Proposal(proposal_id=f"icm-demo-{int(T0.timestamp())}-x", account_id="icm-demo", agent_id=TREND.agent_id,
                    side=1, lots=0.1, entry=price, stop=stop, target=target, p=0.6, ev_r=0.1, spread_points=0.0,
                    top_features=[])
    before = set(eng.open)
    eng._execute(prop, TREND, 0.1, stop, target, requested=price, atr_usd=0.0123)
    (pid,) = set(eng.open) - before
    return pid


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    ts = pd.date_range(T0, periods=len(rows), freq="1h", tz="UTC")
    o, h, lo, c = (list(x) for x in zip(*rows))
    return pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta("1h"), "bid_open": o, "bid_high": h,
                         "bid_low": lo, "bid_close": c, "ask_open": o, "ask_high": h, "ask_low": lo, "ask_close": c})


def test_sl_tp_trail_round_to_symbol_digits(tmp_path):
    """A 3-digit instrument: the order's stop and target and the trailed stop keep the third decimal (cent rounding
    would move a silver stop by up to half a cent, a 5-digit FX stop by up to 50 points)."""
    pb = ThreeDigitBroker(symbol="XAGUSD", equity=10_000, commission_per_lot_side=0.0, adverse_slip_points=0.0)
    eng = _engine(tmp_path, pb, price_digits=3, point=0.001, symbol="XAGUSD")
    pid = _enter(eng, pb, 30.0, stop=30.0 - 0.0123456, target=30.0 + 0.2469123)
    pos = pb.positions(symbol="XAGUSD")[0]
    assert (pos.sl, pos.tp) == (29.988, 30.247) == (eng.open[pid].sl, eng.open[pid].tp)
    # TREND trails 1.5 ATR behind the best price once 1.25 ATR beyond entry (ATR 0.0123 here)
    t1 = T0 + pd.Timedelta(hours=1)
    pb.on_tick(Tick(ts_utc=t1, bid=30.05, ask=30.05))
    eng._manage_policies("1h", _bars([(30.0, 30.0501, 30.0, 30.05)]), t1)
    want = round(30.0501 - 1.5 * 0.0123, 3)                      # 30.0316, never 30.03
    assert eng.open[pid].sl == pb.positions(symbol="XAGUSD")[0].sl == want == 30.032


def test_an_orphan_stop_is_rounded_to_the_symbol_digits(tmp_path):
    pb = ThreeDigitBroker(symbol="XAGUSD", equity=10_000)
    eng = _engine(tmp_path, pb, price_digits=3, point=0.001, symbol="XAGUSD")
    pb.on_tick(Tick(ts_utc=T0, bid=30.0, ask=30.0))
    pb._positions[7] = Position(position_id=7, symbol="XAGUSD", side=1, lots=0.1, open_price=30.0, sl=None, tp=None,
                                magic=eng.cfg.magic_base + 1, comment="", open_time_utc=T0, profit=0.0)
    eng._last_atr = 0.0123
    eng._reconcile(T0)
    assert eng.open[7].sl == round(30.0 - 1.5 * 0.0123, 3) == 29.982


def test_gold_still_rounds_to_the_cent(tmp_path):
    pb = PaperBroker(equity=10_000, commission_per_lot_side=0.0, adverse_slip_points=0.0)
    eng = _engine(tmp_path, pb)
    assert eng.cfg.price_digits == 2 and eng.cfg.point == 0.01
    pid = _enter(eng, pb, 2000.0, stop=1998.7654, target=2003.4567)
    assert (eng.open[pid].sl, eng.open[pid].tp) == (1998.77, 2003.46)


def test_an_unknown_contract_size_refuses_entries_and_never_assumes_100_oz(tmp_path):
    pb = NoTermsBroker(equity=10_000)
    eng = _engine(tmp_path, pb)
    tick = Tick(ts_utc=T0, bid=2000.0, ask=2000.2)
    pb.on_tick(tick)
    assert eng._contract_size() is None
    eng._refresh_account(tick)
    assert eng.state.dq_error
    assert "contract_unknown" in eng.gate.account_reasons(eng.state) or eng.state.dq_error
    eng._write_state()
    import json
    st = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert "contract_unknown" in st["dq_checks"]


def test_an_unknown_contract_size_never_stops_an_exit(tmp_path):
    pb = NoTermsBroker(equity=10_000)
    eng = _engine(tmp_path, pb)
    pb.on_tick(Tick(ts_utc=T0, bid=2000.0, ask=2000.0))
    pb._positions[9] = Position(position_id=9, symbol="XAUUSD", side=1, lots=0.1, open_price=2000.0, sl=1990.0,
                                tp=None, magic=eng.cfg.magic_base + 1, comment="", open_time_utc=T0, profit=0.0)
    eng._reconcile(T0)
    assert 9 in eng.open
    assert eng._try_close(9, "time_exit", T0).ok and not pb.positions()


def test_a_pyramid_stop_is_rounded_tighter_on_the_instruments_own_point():
    """Plan: "exploration_pyramid ... CENT becomes the point". Gold keeps the cent (the default)."""
    from goldbot.engine import exploration_pyramid as P
    assert P.tighter_round(30.0011, 1, point=0.001, digits=3) == 30.002
    assert P.tighter_round(1.234567, -1, point=0.00001, digits=5) == 1.23456
    assert P.tighter_round(100.001, 1) == 100.01
