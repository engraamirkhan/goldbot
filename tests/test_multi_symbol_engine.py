"""Multi-instrument demo plan (2026-11-02, lane L3, engine): "one engine per account with N SymbolBooks; one
AccountRisk (RiskGate, AccountState, kill switch) shared; serial gate.check across symbols"; "magic = 3_000_000 +
account_idx*10_000 + magic_block*100 + offset"; live and observe accounts never trade a non-gold instrument."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import Engine, EngineConfig, books, magic
from goldbot.engine.books import BOOK_ATTRS, SYMBOL_STATE, BookSpec, using
from goldbot.execution.broker import Position, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.specialists.trend import TrendSpecialist
from goldbot.telegram.approvals import Proposal

T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")       # a Wednesday
SILVER = BookSpec(key="XAGUSD", symbol="XAGUSD", store_symbol="XAGUSD", magic_block=1, digits=2, point=0.01,
                  contract_size=100.0)
OIL = BookSpec(key="XTIUSD", symbol="XTIUSD", store_symbol="XTIUSD", magic_block=17, digits=2, point=0.01,
               contract_size=100.0)
TREND = TrendSpecialist()


class MultiPaper(PaperBroker):
    """The paper terminal with every symbol's quote set explicitly (`set_quote`), as MT5 serves each its own: the
    engine's `on_tick` hook (which keys a tick to the paper broker's one symbol) does nothing here."""

    def on_tick(self, tick: Tick) -> None:
        return None


def _engine(tmp_path: Path, pb: PaperBroker, specs: tuple[BookSpec, ...] = (SILVER,), **cfg: Any) -> Engine:
    return Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", magic_base=260500,
                               state_dir=str(tmp_path), owner_user_id=111, instruments=specs, **cfg),
                  pb, [TREND], {})


def _quote(pb: PaperBroker, sym: str, px: float, at: pd.Timestamp = T0, spread: float = 0.02) -> Tick:
    t = Tick(ts_utc=at, bid=px, ask=px + spread)
    pb.set_quote(sym, t)
    return t


def _enter(eng: Engine, sym: str, px: float, cid: str) -> int:
    with using(eng, eng.books[sym]):
        prop = Proposal(proposal_id=cid, account_id="icm-probe", agent_id=TREND.agent_id, side=1, lots=0.1, entry=px,
                        stop=px - 1, target=px + 2, p=0.6, ev_r=0.1, spread_points=0.0, top_features=[])
        before = set(eng.open)
        eng._execute(prop, TREND, 0.1, px - 1, px + 2, requested=px, atr_usd=1.0)
        (pid,) = set(eng.open) - before
        return pid


def test_a_non_gold_book_has_its_own_magic_block_and_gold_keeps_its_base(tmp_path):
    eng = _engine(tmp_path, MultiPaper(equity=10_000))
    assert eng.cfg.magic_base == 260500 and eng.cfg.symbol == "XAUUSD"          # gold: the primary, unchanged
    silver = eng.books["XAGUSD"]
    with using(eng, silver):
        assert eng.cfg.symbol == "XAGUSD" and eng.cfg.magic_base == magic.book_base(260500, 1) == 3_050_100
        assert eng.cfg.release_symbol == "XAGUSD" and not eng.cfg.cost_probe and not eng.cfg.broker_bars
    assert eng.cfg.symbol == "XAUUSD"                     # the block restored the active book


def test_books_reconcile_positions_by_magic_block_after_restart(tmp_path):
    """Each book's trades come back to that book after a restart (its own order file), an orphan in a block's range is
    adopted by that block's book only, and gold never sweeps a silver trade as closed."""
    pb = MultiPaper(equity=10_000, commission_per_lot_side=0.0, adverse_slip_points=0.0)
    _quote(pb, "XAUUSD", 2000.0)
    _quote(pb, "XAGUSD", 30.0)
    eng = _engine(tmp_path, pb)
    gold_pid = _enter(eng, "XAUUSD", 2000.0, "icm-probe-1-gold")
    silver_pid = _enter(eng, "XAGUSD", 30.0, "icm-probe-1-silver")
    assert pb._positions[silver_pid].magic // 100 * 100 == 3_050_100
    orphan = Position(position_id=999, symbol="XAGUSD", side=-1, lots=0.1, open_price=30.0, sl=31.0, tp=None,
                      magic=3_050_100 + 95, comment="", open_time_utc=T0, profit=0.0)
    pb._positions[999] = orphan
    assert (tmp_path / "books" / "icm-probe" / "orders_XAGUSD.json").exists()
    assert not list(tmp_path.glob("orders_*XAG*"))          # never an `orders_*.json` the API reads as an account

    again = _engine(tmp_path, pb)
    assert set(again.open) == {gold_pid}
    silver = again.books["XAGUSD"]
    with using(again, silver):
        assert silver_pid in again.open
        again._reconcile(T0)
        assert set(again.open) == {silver_pid, 999} and not again._foreign
    again._reconcile(T0)
    again._sweep_closed({p.position_id for p in pb.positions()})
    assert set(again.open) == {gold_pid} and not again._closes and not again._foreign


def test_symbol_state_is_per_book_so_an_entry_is_never_gated_on_another_symbols_quote(tmp_path):
    pb = MultiPaper(equity=10_000)
    eng = _engine(tmp_path, pb)
    tick = _quote(pb, "XAUUSD", 2000.0, spread=0.2)
    eng._refresh_account(tick)
    assert eng.state.spread_points == pytest.approx(20.0)
    with using(eng, eng.books["XAGUSD"]):
        assert eng.state.stale_bars and eng.state.market_closed and eng.state.dq_error   # never refreshed: refused
        assert eng.state.equity == 10_000                                                # the account's, shared
        eng.state.spread_points = 3.0
    assert eng.state.spread_points == pytest.approx(20.0)
    with using(eng, eng.books["XAGUSD"]):
        assert eng.state.spread_points == 3.0
    assert set(SYMBOL_STATE) <= set(type(eng.state).model_fields)


def test_exposure_counts_every_books_positions(tmp_path):
    """Plan: account-wide heat. A refresh on any book counts both symbols' positions, each priced on its own quote."""
    pb = MultiPaper(equity=10_000, commission_per_lot_side=0.0, adverse_slip_points=0.0)
    gold = _quote(pb, "XAUUSD", 2000.0)
    _quote(pb, "XAGUSD", 30.0)
    eng = _engine(tmp_path, pb)
    _enter(eng, "XAUUSD", 2000.0, "icm-probe-2-gold")
    _enter(eng, "XAGUSD", 30.0, "icm-probe-2-silver")
    with using(eng, eng.books["XAGUSD"]):
        eng._last_tick = silver = Tick(ts_utc=T0, bid=30.0, ask=30.02)     # as `_on_tick` keeps it
        eng._refresh_account(silver)
    eng._last_tick = gold
    eng._refresh_account(gold)
    assert eng.state.open_positions == 2 and eng.state.open_lots == pytest.approx(0.2)
    assert eng.state.open_risk is not None and eng.state.open_risk > 0
    assert eng.state.open_notional == pytest.approx(0.1 * 100 * 2000.01 + 0.1 * 100 * 30.01, rel=1e-6)


def test_a_live_account_never_opens_a_non_gold_book(tmp_path):
    with pytest.raises(ValueError, match="demo accounts only"):
        Engine(EngineConfig(account_id="icm-live", broker_name="icm", mode="live", magic_base=260300,
                            state_dir=str(tmp_path), instruments=(SILVER,)), MultiPaper(), [], {})


def test_every_book_attribute_lives_on_the_book_and_none_on_the_engine(tmp_path):
    eng = _engine(tmp_path, MultiPaper(equity=10_000), specs=(SILVER, OIL))
    for book in eng.books.values():
        missing = [a for a in BOOK_ATTRS if a not in book.__dict__]
        assert not missing, (book, missing)
    assert not set(BOOK_ATTRS) & set(eng.__dict__)


def test_gold_alone_never_switches_and_publishes_no_books(tmp_path):
    pb = PaperBroker(equity=10_000)
    pb.on_tick(Tick(ts_utc=T0, bid=2000.0, ask=2000.2))
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)), pb, [], {})
    assert list(eng.books) == ["XAUUSD"] and books.published(eng) == {}
    eng._write_state()
    assert "books" not in json.loads((tmp_path / "engine_icm-demo.json").read_text())


def test_the_account_file_carries_each_books_view(tmp_path):
    pb = MultiPaper(equity=10_000)
    eng = _engine(tmp_path, pb)
    eng._refresh_account(_quote(pb, "XAUUSD", 2000.0))
    with using(eng, eng.books["XAGUSD"]):
        eng._write_state()               # a non-gold book never writes the account's file
    assert not (tmp_path / "engine_icm-probe.json").exists()
    eng._write_state()
    st = json.loads((tmp_path / "engine_icm-probe.json").read_text())
    assert set(st["books"]) == {"XAUUSD", "XAGUSD"} and st["books"]["XAGUSD"]["magic_base"] == 3_050_100
    assert st["books"]["XAGUSD"]["terms_problem"] == "instrument_terms_missing"


def test_an_unsettled_order_on_any_book_refuses_every_entry(tmp_path):
    from goldbot.engine.store import SentOrder
    eng = _engine(tmp_path, MultiPaper(equity=10_000))
    with using(eng, eng.books["XAGUSD"]):
        eng._orders["x"] = SentOrder(client_order_id="x", ts_utc=T0, agent_id="a", side=1, magic=3_050_100, lots=0.1,
                                     max_bars=4, sl=29.0, tp=31.0, status="sending")
    eng._save_orders()
    assert eng.state.unconfirmed_order


def test_account_pass_runs_every_book_serially_gold_first(tmp_path):
    eng = _engine(tmp_path, MultiPaper(equity=10_000), specs=(SILVER, OIL))
    seen: list[tuple[str, str]] = []

    def one(e: Engine, broker: Any, symbol: str) -> bool:
        seen.append((symbol, e.cfg.symbol))
        return True
    assert books.account_pass(eng, eng.broker, one)
    assert seen == [("XAUUSD", "XAUUSD"), ("XAGUSD", "XAGUSD"), ("XTIUSD", "XTIUSD")]
    assert eng.book is eng.primary


def test_an_allowed_entry_is_booked_on_the_shared_state_before_the_next_symbol_is_checked(tmp_path):
    """L2's serial gate: `books.check` books every allowed decision (RiskGate.book) on a multi-symbol account only, so a
    second symbol on the same bar sees the first one's risk; gold alone calls gate.check exactly as before."""
    from types import SimpleNamespace

    eng = _engine(tmp_path, MultiPaper(equity=10_000))
    assert eng.gate.limits.multi_symbol
    booked: list[Any] = []
    allowed = SimpleNamespace(allowed=True)
    eng.gate.check = lambda *a, **k: allowed                         # type: ignore[method-assign]
    eng.gate.book = lambda st, intent, d: booked.append((intent, d))   # type: ignore[method-assign]
    assert books.check(eng, "intent", T0) is allowed and booked == [("intent", allowed)]

    gold = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path / "g")),
                  MultiPaper(), [], {})
    assert not gold.gate.limits.multi_symbol
    gold.gate.check = lambda *a, **k: allowed                        # type: ignore[method-assign]
    gold.gate.book = lambda *a: booked.append(a)                     # type: ignore[method-assign]
    books.check(gold, "intent", T0)
    assert len(booked) == 1


def test_only_atr_scaled_random_controls_run_on_another_instrument_each_under_its_own_arm_id():
    """Plan: "random controls S0/I0 ... per instrument (never pooled); no ... dollar-constant arms". S0's $3 stop
    means nothing on silver; I0's 1.5 ATR does. The copy's id, order key and tag carry the instrument."""
    from goldbot.engine import exploration as X
    from goldbot.research.arms import load_arms
    specs = {a.id: a for a in load_arms(X.SPEC_PATH)}
    pots = {"scalp": X.Pot(gbp=10, max_open=3), "intraday": X.Pot(gbp=10, max_open=3)}
    cfg = X.ExplorationConfig(enabled=True, pots=pots,
                              arms=(X.Arm(spec=specs["S0"], style="scalp", control_share=0.25),
                                    X.Arm(spec=specs["I0"], style="intraday", control_share=0.3)))
    got = X.book_config(cfg, "XAGUSD")
    assert got is not None and [a.arm_id for a in got.arms] == ["I0@XAGUSD"]
    a = got.arms[0]
    assert a.tag == "explore:I0@XAGUSD" and a.key != cfg.arms[1].key and a.spec.id == "I0"
    assert X.book_config(cfg.model_copy(update={"arms": cfg.arms[:1]}), "XAGUSD") is None


def test_a_non_gold_books_rows_are_keyed_by_account_and_store_symbol_never_pooled_with_gold(tmp_path):
    """Plan: "store bars_1m_live keyed (source=account, symbol=store_symbol), never pooled with XAUUSD"."""
    from goldbot.data.store import Store
    pb = MultiPaper(equity=10_000)
    spec = SILVER.model_copy(update={"symbol": "XAGUSD.x", "store_symbol": "XAGUSD"})
    eng = _engine(tmp_path, pb, specs=(spec,), data_root=str(tmp_path / "data"))
    assert books.live_key(eng) == ("icm", "XAUUSD")                  # gold: unchanged
    with using(eng, eng.books["XAGUSD.x"]):
        assert books.live_key(eng) == ("icm-probe", "XAGUSD") and books.store_symbol(eng) == "XAGUSD"
        for k in range(3):
            eng.on_tick(_quote(pb, "XAGUSD.x", 30.0 + k / 100, T0 + pd.Timedelta(seconds=k)))
        eng.flush_ticks()
    store = Store(tmp_path / "data")
    assert len(store.read("ticks", source="icm-probe", symbol="XAGUSD")) == 3
    assert store.read("ticks", source="icm-probe", symbol="XAUUSD").empty
