"""Profitability cycle 2026-11-12, ruling (a) (docs/research/profitability-2026-11-12.md section 4): "retire the demo
arms that lost money with no live strategy to control": ZCD, F1A, F1C, G1A, G1C and IEX15 get `retired_day`
2026-11-13 and the rule on their own frozen spec rows (unhashed: id, hash and K stay; open positions keep every exit).

The I0-<instrument> leak: I0 retired on 2026-11-06, yet I0-USTEC, I0-XAUEUR, I0-DE40, I0-US500 (icm-probe) and I0-US30
(vantage-demo) kept entering through 11-12. Each is its own spec row (`<family>-<instrument>`, own id and hash, the
family's rule body on `params.instrument`), and `spec_retired` read only the row's own `retired_day`. The engine now
gives a per-instrument row its family's retirement (`exploration.family_retirement`): "a retired family stops all its
per-instrument rows", kept configured so an open position keeps its stop, target, hold and every exit."""
from __future__ import annotations

from pathlib import Path

import pytest

from goldbot.engine import exploration
from goldbot.ops import accounts as acc_mod
from goldbot.research import arms

SPEC = Path(__file__).resolve().parents[1] / "config" / "exploration_arms.yaml"
RETIRED_1113 = ("ZCD", "F1A", "F1C", "G1A", "G1C", "IEX15")
RULE = "profitability-2026-11-12 (a): net-negative control or cost read with no live counterpart"
I0_LEAK = {"icm-probe": ("I0-USTEC", "I0-XAUEUR", "I0-DE40", "I0-US500"), "vantage-demo": ("I0-US30", "I0-XAUJPY")}


@pytest.fixture(scope="module")
def spec() -> dict[str, arms.Arm]:
    return {a.id: a for a in arms.load_arms(SPEC)}            # hash-checked: a retirement never edits the frozen body


@pytest.fixture(scope="module")
def accounts() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(logins=False)


def test_the_six_loss_arms_are_retired_on_2026_11_13_under_the_ruling_with_id_and_hash_unchanged(spec) -> None:
    for arm_id in RETIRED_1113:
        a = spec[arm_id]
        assert a.retired and a.retired_day == "2026-11-13" and a.retired_rule == RULE, arm_id
        assert a.config_hash == arms.arm_hash(a), arm_id


def test_the_learning_arms_that_take_the_freed_slots_stay_active_with_their_controls(spec) -> None:
    for arm_id in ("Z10D", "Z9D", "Z5P", "LCZ9D", "LCZ10D", "IEX1H"):
        assert not spec[arm_id].retired and spec[arm_id].status == "run", arm_id


def test_every_per_instrument_row_of_the_retired_i0_family_is_refused_and_kept_configured_on_its_account(
        accounts) -> None:
    for account_id, ids in I0_LEAK.items():
        cfg = exploration.config_for(accounts[account_id])
        assert cfg is not None
        running, refused = {a.arm_id for a in cfg.arms}, exploration.spec_retired(cfg)
        for arm_id in ids:
            assert arm_id in running, (account_id, arm_id)    # configured: an open position keeps every exit
            assert arm_id in refused, (account_id, arm_id)    # no new entry


def test_every_registered_row_of_a_retired_family_is_refused_on_every_account(accounts, spec) -> None:
    for acc in accounts.values():
        cfg = exploration.config_for(acc)
        if cfg is None:
            continue
        refused = exploration.spec_retired(cfg)
        for a in cfg.arms:
            inst = a.spec.params.get("instrument")
            fam = spec.get(a.arm_id.removesuffix(f"-{inst}")) if inst else None
            if fam is not None and fam.retired and fam.rule == a.spec.rule:
                assert a.arm_id in refused, (acc.account_id, a.arm_id)


def test_a_retired_per_instrument_row_journals_its_familys_rule(accounts) -> None:
    cfg = exploration.config_for(accounts["icm-probe"])
    assert cfg is not None
    row = {a.arm_id: a for a in cfg.arms}["I0-USTEC"].spec
    assert row.retired_day == "2026-11-06" and "family I0" in str(row.retired_rule)
    assert "control-thinning" in str(row.retired_rule)


def _row(arm_id: str, *, rule: str = "random_bracket", instrument: str | None = None,
         retired: bool = False) -> arms.Arm:
    params: dict[str, object] = {"minutes": [0], "stop_atr": 1.5, "target_r": 2.0, "hold_bars": 96}
    if instrument:
        params["instrument"] = instrument
    return arms.Arm(id=arm_id, rule=rule, timeframe="15m", params=params, max_open=1, status="run",
                    retired_day="2026-11-06" if retired else None, retired_rule="test" if retired else "")


def test_a_per_instrument_row_takes_its_retired_familys_retirement() -> None:
    specs = {"I0": _row("I0", retired=True), "I0-DE40": _row("I0-DE40", instrument="DE40")}
    got = exploration.family_retirement(specs["I0-DE40"], specs)
    assert got.retired and got.retired_day == "2026-11-06" and got.retired_rule == "family I0 retired: test"
    assert arms.arm_hash(got) == arms.arm_hash(specs["I0-DE40"])


def test_a_per_instrument_row_of_an_active_family_stays_active() -> None:
    specs = {"S0": _row("S0"), "S0-DE40": _row("S0-DE40", instrument="DE40")}
    assert not exploration.family_retirement(specs["S0-DE40"], specs).retired


def test_a_row_is_in_a_family_only_with_its_instrument_as_id_suffix_the_same_rule_and_a_gold_family_row() -> None:
    other_rule = _row("I0-DE40", instrument="DE40").model_copy(update={"rule": "tsmom"})
    specs = {"I0": _row("I0", retired=True), "I0-X": _row("I0-X", instrument="DE40"), "I0-DE40": other_rule,
             "LB-US500": _row("LB-US500", instrument="US500", retired=True),     # a non-gold row is no family
             "LB-US500-DE40": _row("LB-US500-DE40", instrument="DE40")}
    for arm_id in ("I0-X", "I0-DE40", "LB-US500-DE40"):
        assert not exploration.family_retirement(specs[arm_id], specs).retired, arm_id


def test_a_row_retired_on_its_own_keeps_its_own_day_and_rule() -> None:
    own = _row("I0-XAUJPY", instrument="XAUJPY").model_copy(update={"retired_day": "2026-11-09",
                                                                     "retired_rule": "own"})
    specs = {"I0": _row("I0", retired=True), "I0-XAUJPY": own}
    assert exploration.family_retirement(own, specs) is own
