"""Owner decision 2026-11-04: trade ONLY 25 instruments, remove all others.

The design rules this file holds, quoted from the decision and the registry:
- "trade ONLY these 25 instruments": the registry enables exactly them (USOIL = XTIUSD, NAS100 = USTEC, GER40 = DE40);
- "REMOVE (set enabled: false; KEEP the registry entries and magic_block numbers, because open positions are reconciled
  by magic block and blocks are never renumbered)";
- "the engine runs no book for them": no account lists them or registers one of their arms, and every spec row that
  trades one is retired (the row, id and K stay);
- "Exits for any position still open on a removed instrument must keep working (exits never gated)": a position in a
  removed instrument's magic range gets a wind-down book (entries refused, exits run), and an exploration position on
  it, whose arm no book runs any more, is closed at once (`exploration.ORPHAN_EXIT`);
- each new instrument gets "the same arm and control set the existing FX/index instruments got" on the one account
  that trades it, and BTCUSD sits in its own `crypto` notional group at no more leverage than any other class.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pandas as pd
import pytest

from goldbot.config import ROOT
from goldbot.engine import Engine, EngineConfig, books, exploration
from goldbot.engine.books import using
from goldbot.engine.magic import EXPLORATION_OFFSET
from goldbot.execution.broker import Position, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.instruments import LEVERAGE_BY_CLASS, instrument_magic_base, load_instruments, registry
from goldbot.ops import accounts
from goldbot.ops.services import engine as svc
from goldbot.research.arms import load_arms
from goldbot.risk.gate import RiskGate

SPEC = ROOT / "config" / "exploration_arms.yaml"

OWNER_25 = frozenset({"EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "XAUUSD", "XAGUSD", "XTIUSD", "US30", "USTEC", "UK100",
                      "DE40", "EURGBP", "EURJPY", "AUDJPY", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURAUD", "GBPAUD",
                      "BTCUSD", "XAUEUR", "XAUJPY", "USINDEX", "US500"})
# the blocks they held when they were removed: never renumbered, never reused
REMOVED_BLOCKS = {"XPTUSD": 2, "XAUAUD": 4, "JP225": 9, "XBRUSD": 18, "XNGUSD": 19, "UST10Y": 20, "BUND10Y": 21,
                  "COPPER": 22, "WHEAT": 23, "SOYBEAN": 24, "SUGAR": 25, "COFFEE": 26}
NEW = ("GBPJPY", "EURGBP", "EURJPY", "AUDJPY", "EURAUD", "GBPAUD", "BTCUSD", "XAUJPY", "USINDEX", "US30")
REG = load_instruments()
ACCOUNTS = accounts.load_accounts()
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")            # a Wednesday


@pytest.fixture(autouse=True)
def _fresh_registry() -> Iterator[None]:
    registry.cache_clear()
    yield
    registry.cache_clear()


def _listing(key: str) -> list[str]:
    return [a for a, acc in ACCOUNTS.items() if key in (acc.instruments or ())]


def _registered(acc: Any) -> set[str]:
    ex = getattr(acc, "exploration", None)
    return set(ex.arms) if ex is not None and ex.arms else set()


def test_the_registry_enables_exactly_the_owners_25_instruments():
    assert {k for k, s in REG.instruments.items() if s.enabled} == OWNER_25
    assert all(not REG.get(k).verify for k in OWNER_25)


def test_a_removed_instrument_keeps_its_entry_its_magic_block_and_its_broker_symbol_but_is_disabled():
    for key, block in REMOVED_BLOCKS.items():
        spec = REG.get(key)
        assert not spec.enabled, key
        assert spec.magic_block == block and spec.broker_symbols, key


def test_no_account_lists_a_removed_instrument_and_each_kept_or_new_one_is_on_exactly_one_demo_account():
    for key in REMOVED_BLOCKS:
        assert _listing(key) == [], key
    for key in OWNER_25 - {"XAUUSD"}:
        [name] = _listing(key)
        acc = ACCOUNTS[name]
        assert acc.mode == "demo" and acc.broker in REG.get(key).broker_symbols, (key, name)


def test_no_account_registers_an_arm_of_a_removed_instrument():
    spec = {a.id: a for a in load_arms(SPEC)}
    for name, acc in ACCOUNTS.items():
        for arm_id in _registered(acc):
            row = spec.get(arm_id)
            instrument = row.params.get("instrument") if row is not None else None
            assert instrument not in REMOVED_BLOCKS, (name, arm_id)


def test_every_spec_row_of_a_removed_instrument_is_retired_and_keeps_its_row():
    rows = [a for a in load_arms(SPEC) if a.params.get("instrument") in REMOVED_BLOCKS]
    assert len(rows) == 4 * len(REMOVED_BLOCKS)                 # S0, I0, LB, LBC each: rows never deleted
    assert all(a.retired_day == "2026-11-04" and a.retired_rule for a in rows)


def test_each_new_instrument_runs_the_same_controls_and_learning_pair_as_the_existing_ones():
    spec = {a.id: a for a in load_arms(SPEC)}
    for key in NEW:
        [name] = _listing(key)
        arms = {f"S0-{key}", f"I0-{key}", f"LB-{key}", f"LBC-{key}"}
        assert arms <= _registered(ACCOUNTS[name]), key
        for arm_id in arms:
            row = spec[arm_id]
            # S0-XAUJPY: retired 2026-11-07 (prereg-amendment-2026-11-07-kill-only.md section 4), I0-XAUJPY 2026-11-09
            # (prereg-amendment-2026-11-10-execution-controls.md section 9); I0-USTEC/XAUEUR/DE40/US500/US30
            # 2026-11-13 (docs/research/profitability-2026-11-12.md); every other non-gold row 2026-11-14 (research-
            # director ruling 2026-11-13, gold-only demo); rows and ids kept
            assert row.params["instrument"] == key and row.status == "run", arm_id
            retired_ok = {("S0-XAUJPY", "2026-11-07"), ("I0-XAUJPY", "2026-11-09"), (arm_id, "2026-11-14")} | {
                (f"I0-{k}", "2026-11-13") for k in ("USTEC", "XAUEUR", "DE40", "US500", "US30")}
            assert not row.retired_day or (arm_id, row.retired_day) in retired_ok, arm_id
        assert spec[f"LB-{key}"].role == "learning" and spec[f"LBC-{key}"].role == "control"


def test_new_magic_blocks_are_new_and_the_removed_blocks_are_never_reused():
    blocks = {REG.get(k).magic_block for k in NEW}
    assert len(blocks) == len(NEW)
    assert not blocks & set(REMOVED_BLOCKS.values()) and min(blocks) > max(REMOVED_BLOCKS.values())


def test_btcusd_has_its_own_crypto_group_at_no_more_leverage_than_any_other_class():
    btc = REG.get("BTCUSD")
    assert btc.correlation_group == "crypto" and btc.asset_class == "crypto"
    assert LEVERAGE_BY_CLASS[btc.leverage_class] == min(LEVERAGE_BY_CLASS.values())
    gate = RiskGate()
    crypto = gate.leverage(None, btc.leverage_class, "crypto")
    assert all(crypto <= gate.leverage(None, cls, None) for cls in LEVERAGE_BY_CLASS)


def _position(pid: int, sym: str, magic: int, px: float, comment: str = "") -> Position:
    return Position(position_id=pid, symbol=sym, side=1, lots=0.1, open_price=px, sl=px * 0.99, tp=None, magic=magic,
                    comment=comment, open_time_utc=T0, profit=0.0)


@pytest.mark.parametrize("key", sorted(REMOVED_BLOCKS))
def test_a_position_left_open_on_a_removed_instrument_gets_a_wind_down_book_on_the_account_that_traded_it(key):
    """The account that listed it before the decision (its broker's demo learning host) still finds the position in
    the instrument's magic range: a book that refuses every entry and runs every exit."""
    spec = REG.get(key)
    name = "icm-probe" if "icm" in spec.broker_symbols else "vantage-demo"
    acc = ACCOUNTS[name]
    sym = spec.broker_symbols[acc.broker]
    pb = PaperBroker()
    pb._positions[7] = _position(7, sym, _base(acc, key) + EXPLORATION_OFFSET, 100.0)
    got = {b.key: b for b in svc.instrument_books(acc, pb, None)}
    assert got[key].blocked == books.WIND_DOWN and got[key].exploration_config is None
    assert got[key].magic_base == _base(acc, key)


def _base(acc: accounts.Account, key: str) -> int:
    assert acc.instrument_magic_index is not None
    return instrument_magic_base(acc.instrument_magic_index, REG.get(key))


class _MultiPaper(PaperBroker):
    def on_tick(self, tick: Tick) -> None:
        return None


def test_an_exploration_position_on_a_removed_instrument_is_closed_at_once_and_its_book_takes_no_entry(
        tmp_path, monkeypatch):
    """COFFEE was vantage-demo's: its S0-COFFEE position, found open after the decision, has no arm on any book (the
    instrument is off the account), so `exploration.exits` closes it at once; its book refuses every entry."""
    acc = ACCOUNTS["vantage-demo"]
    base = _base(acc, "COFFEE")
    pb = _MultiPaper(equity=10_000)
    comment = exploration.make_order_id(acc.account_id, "S0-COFFEE", T0)
    pb._positions[7] = _position(7, "Coffee-C", base + EXPLORATION_OFFSET, 3.5, comment)
    specs = svc.instrument_books(acc, pb, None)
    eng = Engine(EngineConfig(account_id=acc.account_id, broker_name=acc.broker, mode="demo",
                              magic_base=acc.magic_base, state_dir=str(tmp_path), instruments=specs), pb, [], {})
    wind = eng.books["Coffee-C"]
    assert books.problem(wind) == books.WIND_DOWN
    closes: list[tuple[int, str]] = []
    with using(eng, wind):
        eng._last_tick = Tick(ts_utc=T0, bid=3.5, ask=3.502)
        pb.set_quote("Coffee-C", eng._last_tick)
        eng._last_atr = 0.02
        eng._reconcile(T0)
        assert 7 in eng.open and exploration.is_exploration(eng.open[7].agent_id)
        monkeypatch.setattr(eng, "_try_close", lambda p, why, now, **kw: closes.append((p, why)))
        exploration.exits(eng, T0)
    assert closes == [(7, exploration.ORPHAN_EXIT)]


def test_vantage_demo_trades_the_new_instruments_and_icm_probe_keeps_the_kept_ones():
    """Book count per account (incident 2026-11-02: icm-probe's box had no CPU for 20 books): the kept ICM instruments
    stay on icm-probe, the new ones go to vantage-demo, so neither host runs more books than before the decision."""
    probe = svc.account_instruments(ACCOUNTS["icm-probe"])
    vantage = svc.account_instruments(ACCOUNTS["vantage-demo"])
    assert set(vantage) == set(NEW)
    assert set(probe) == OWNER_25 - set(NEW) - {"XAUUSD"}
    assert len(probe) + 1 <= 20 and len(vantage) + 1 <= 11


def test_vantage_demo_gets_option_a_slots_and_every_money_cap_stays_as_it_was():
    """learning-mi-2026-11-02.md section 11, option (a) (quant review HIGH, PD 2026-11-04): "Raise vantage-demo
    `learning_max_open` to 12 and the intraday pot to 14. Keep the £8 per-trade and £160 learning caps and every loss
    limit unchanged (this is a demo slot count, not a loss limit)"."""
    acc = ACCOUNTS["vantage-demo"]
    ex = acc.exploration
    assert ex.learning_max_open == 12 and ex.pots["intraday"].max_open == 14
    assert (ex.max_open_total, acc.max_positions, ex.budget_share, ex.pot_budgets) == (23, 24, 0.825, False)
    assert acc.daily_loss_limit_abs == 200 and acc.risk_overrides is not None
    assert acc.risk_overrides.ceilings.weekly == 0.06 and acc.risk_overrides.ceilings.daily == 0.05
    spec = {a.id: a for a in load_arms(SPEC)}
    for key in NEW:
        assert ex.arms[f"LB-{key}"].max_loss == ex.arms[f"LBC-{key}"].max_loss == 8.0, key
        assert spec[f"LB-{key}"].learning["loss_cap_gbp"] == 160.0, key
        assert ex.arms[f"S0-{key}"].max_loss == 6.5 and ex.arms[f"I0-{key}"].max_loss == 17.0, key
