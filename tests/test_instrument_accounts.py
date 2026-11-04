"""Multi-instrument demo, lane L1: which account trades which instrument (config/accounts.yaml `instruments`), the
magic range of each, and the registry look-ups the other lanes size and check by (goldbot/instruments.py).

Plan rules under test: "real-money/live and observe accounts refuse any non-XAUUSD instrument"; "magic = 3_000_000 +
account_idx*10_000 + magic_block*100 + offset (magic_block per instrument in config)"; "ICM on icm-probe (19) ...
Vantage on vantage-demo (7)"; "adding an instrument is a YAML change only".
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from goldbot import instruments as inst_mod
from goldbot.instruments import GOLD, instrument_magic_base, load_instruments, registry
from goldbot.ops import accounts as acc_mod

# owner decision 2026-11-04 (trade only the owner's 25; tests/test_owner_instrument_list.py): icm-probe keeps 14 of its
# 19, vantage-demo drops its 7 and trades the 10 added instruments
ICM_14 = {"XAGUSD", "XAUEUR", "US500", "USTEC", "DE40", "UK100", "EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCAD",
          "USDCHF", "NZDUSD", "XTIUSD"}
VANTAGE_10 = {"GBPJPY", "EURGBP", "EURJPY", "AUDJPY", "EURAUD", "GBPAUD", "BTCUSD", "XAUJPY", "USINDEX", "US30"}


def _accounts() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(logins=False)


def _with(acc_id: str, **change: Any) -> acc_mod.Account:
    raw = _accounts()[acc_id].model_dump()
    raw.update(change)
    return acc_mod.Account.model_validate(raw)


# --- the configured lists ---

def test_icm_probe_lists_its_14_kept_instruments_and_vantage_demo_the_10_added_ones() -> None:
    accs = _accounts()
    assert set(accs["icm-probe"].instruments) == ICM_14 and len(accs["icm-probe"].instruments) == 14
    assert set(accs["vantage-demo"].instruments) == VANTAGE_10 and len(accs["vantage-demo"].instruments) == 10


def test_every_other_account_trades_its_gold_symbol_only() -> None:
    for aid, acc in _accounts().items():
        if aid not in ("icm-probe", "vantage-demo"):
            assert acc.instruments == (), aid


def test_no_live_or_observe_account_lists_an_instrument() -> None:
    for acc in _accounts().values():
        if acc.is_live or acc.is_observe_only:
            assert acc.instruments == () and acc.instrument_magic_index is None


# --- live and observe accounts refuse any non-XAUUSD instrument ---

def test_a_live_account_listing_a_non_gold_instrument_is_refused_at_load() -> None:
    with pytest.raises(ValidationError, match="XAUUSD only"):
        _with("icm-live", instruments=("EURUSD",), instrument_magic_index=9)


def test_a_live_account_whose_own_symbol_is_not_gold_is_refused_at_load() -> None:
    with pytest.raises(ValidationError, match="XAUUSD only"):
        _with("icm-live", symbol="EURUSD")


def test_an_observe_only_account_with_a_non_gold_symbol_is_refused() -> None:
    base = _accounts()["vantage-live-obs"].model_dump()
    with pytest.raises(ValidationError, match="XAUUSD only"):
        acc_mod.Account.model_validate({**base, "observe_symbols": ("XAUUSD247", "EURUSD")})
    with pytest.raises(ValidationError, match="XAUUSD only"):
        acc_mod.Account.model_validate({**base, "instruments": ("XAGUSD",), "instrument_magic_index": 9})


def test_a_demo_account_may_list_the_instruments_of_its_broker() -> None:
    acc = _with("icm-probe", instruments=("EURUSD", "US500"))
    assert acc.instruments == ("EURUSD", "US500")


# --- the list itself ---

@pytest.mark.parametrize("bad, why", [
    (("NOPE",), "not in config/instruments.yaml"),
    (("COPPER",), "not listed for broker icm"),
    (("EURUSD", "EURUSD"), "twice"),
    ((GOLD,), "own symbol"),
])
def test_an_unknown_foreign_repeated_or_gold_instrument_is_refused(bad: tuple[str, ...], why: str) -> None:
    with pytest.raises(ValidationError, match=why):
        _with("icm-probe", instruments=bad)


def test_an_account_with_instruments_needs_its_magic_index() -> None:
    with pytest.raises(ValidationError, match="instrument_magic_index"):
        _with("icm-probe", instrument_magic_index=None)


# --- magic ranges ---

def test_instrument_magics_follow_the_plan_formula_and_gold_keeps_its_magic_base() -> None:
    reg = registry()
    assert instrument_magic_base(5, reg.get("EURUSD")) == 3_000_000 + 5 * 10_000 + 10 * 100
    acc = _accounts()["icm-probe"]
    assert acc_mod.instrument_magic_base(acc, GOLD) == acc.magic_base       # XAUUSD byte-identical
    assert acc_mod.instrument_magic_base(acc, "EURUSD") == instrument_magic_base(
        acc.instrument_magic_index or 0, reg.get("EURUSD"))


def test_no_two_accounts_and_no_two_instruments_share_a_magic_number() -> None:
    owner: dict[int, tuple[str, str]] = {}
    for acc in _accounts().values():
        for key in (GOLD, *acc.instruments):
            base = acc_mod.instrument_magic_base(acc, key)
            for m in range(base, base + 100):
                assert m not in owner, (acc.account_id, key, owner.get(m))
                owner[m] = (acc.account_id, key)


def test_two_accounts_with_one_magic_index_are_refused_at_load(tmp_path: Any) -> None:
    text = acc_mod.ACCOUNTS_FILE.read_text()
    probe = _accounts()["icm-probe"].instrument_magic_index
    demo = _accounts()["vantage-demo"].instrument_magic_index
    assert probe is not None and demo is not None and probe != demo
    p = tmp_path / "accounts.yaml"
    p.write_text(text.replace(f"instrument_magic_index: {demo}", f"instrument_magic_index: {probe}"))
    with pytest.raises(ValueError, match="instrument_magic_index"):
        acc_mod.load_accounts(p, logins=False)


# --- registry look-ups the other lanes use ---

def test_resolve_finds_an_instrument_by_key_store_symbol_or_the_brokers_symbol() -> None:
    reg = registry()
    assert reg.resolve("COPPER").key == "COPPER"                       # type: ignore[union-attr]
    assert reg.resolve("COPPER-C", broker="vantage").key == "COPPER"   # type: ignore[union-attr]
    assert reg.resolve("XAUUSD+", broker="vantage").key == GOLD        # type: ignore[union-attr]
    assert reg.resolve("XAUUSD247").key == GOLD                        # type: ignore[union-attr]
    assert reg.resolve("COPPER-C", broker="icm") is None
    assert reg.resolve("NOPE") is None


def test_contract_size_and_point_come_from_the_registry() -> None:
    assert inst_mod.contract_size(GOLD) == 100.0 and inst_mod.point_of(GOLD) == 0.01
    assert inst_mod.contract_size("EURUSD") == 100000.0 and inst_mod.point_of("USDJPY") == 0.001
    with pytest.raises(KeyError):
        inst_mod.contract_size("NOPE")


def test_an_arms_instrument_is_its_params_instrument_else_gold() -> None:
    assert inst_mod.arm_instrument({}) == GOLD
    assert inst_mod.arm_instrument({"instrument": "EURUSD"}) == "EURUSD"


def test_every_non_gold_instrument_is_listed_by_one_broker_so_its_terms_are_that_brokers() -> None:
    """The registry holds one set of terms per instrument; a second broker with other terms would need per-broker
    terms (an interface change), so the file refuses to grow one silently."""
    for key, spec in load_instruments().instruments.items():
        if key != GOLD:
            assert len(spec.broker_symbols) == 1, key


def test_the_triple_swap_weekday_converts_mt5_sunday_numbering_to_monday_zero() -> None:
    reg = registry()
    assert inst_mod.triple_weekday(reg.get(GOLD)) == 2            # MT5 3 = Wednesday
    assert inst_mod.triple_weekday(reg.get("US500")) == 4         # MT5 5 = Friday
    weekend = reg.get("US500").model_copy(update={"swap_rollover3days": 6})
    with pytest.raises(ValueError, match="weekday"):
        inst_mod.triple_weekday(weekend)


def test_every_instrument_has_a_spread_ceiling_and_gold_keeps_the_gates_45_points() -> None:
    from goldbot.config import load_settings
    reg = registry()
    assert reg.get(GOLD).max_spread_points == 45 == load_settings().risk.max_spread_points
    assert all(s.max_spread_points > 0 for s in reg.instruments.values())
    # in each instrument's own points: EURUSD's 10 points are one pip, never gold's 45
    assert reg.get("EURUSD").max_spread_points * reg.get("EURUSD").point <= 0.0002
