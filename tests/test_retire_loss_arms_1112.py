"""Profitability cycle 2026-11-12, ruling (a) (docs/research/profitability-2026-11-12.md section 4): "retire the demo
arms that lost money with no live strategy to control". ZCD, F1A, F1C, G1A, G1C and IEX15, and the named
per-instrument controls I0-USTEC, I0-XAUEUR, I0-DE40, I0-US500 (icm-probe) and I0-US30 (vantage-demo), get
`retired_day` 2026-11-13 and the rule on their own frozen spec rows (unhashed: id, hash and K stay; open positions keep
every exit). I0-XAUJPY was retired on its own row on 2026-11-09 and is left as it is.

Why the named I0-<instrument> rows kept entering after I0 retired on 2026-11-06: each is its own spec row, and
prereg-amendment-2026-11-06-control-thinning.md point 4 stops them only when their instrument's cost cell has 30 fills
(`thin_cell_complete`); the I0 retirement never covered them (quant ruling on R133). The other I0-<instrument> rows
stay under that rule, so they are not retired here."""
from __future__ import annotations

from pathlib import Path

import pytest

from goldbot.engine import exploration
from goldbot.ops import accounts as acc_mod
from goldbot.research import arms

SPEC = Path(__file__).resolve().parents[1] / "config" / "exploration_arms.yaml"
RULE = "profitability-2026-11-12 (a): net-negative control or cost read with no live counterpart"
DAY = "2026-11-13"
ARMS = ("ZCD", "F1A", "F1C", "G1A", "G1C", "IEX15")
I0_NAMED = {"icm-probe": ("I0-USTEC", "I0-XAUEUR", "I0-DE40", "I0-US500"), "vantage-demo": ("I0-US30",)}
RETIRED_1113 = {*ARMS, *(i for ids in I0_NAMED.values() for i in ids)}
# still under the cost-cell stop (thin_cell_complete): the ruling did not name them
I0_KEPT = ("I0-AUDUSD", "I0-USDCAD", "I0-NZDUSD", "I0-AUDJPY", "I0-EURAUD", "I0-GBPAUD", "I0-USINDEX")


@pytest.fixture(scope="module")
def spec() -> dict[str, arms.Arm]:
    return {a.id: a for a in arms.load_arms(SPEC)}            # hash-checked: a retirement never edits the frozen body


@pytest.fixture(scope="module")
def accounts() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(logins=False)


def test_the_named_loss_arms_are_retired_on_2026_11_13_under_the_ruling_with_id_and_hash_unchanged(spec) -> None:
    for arm_id in sorted(RETIRED_1113):
        a = spec[arm_id]
        assert a.retired and a.retired_day == DAY and a.retired_rule == RULE, arm_id
        assert a.config_hash == arms.arm_hash(a), arm_id


def test_i0_xaujpy_keeps_its_own_2026_11_09_retirement(spec) -> None:
    assert spec["I0-XAUJPY"].retired_day == "2026-11-09"


def test_the_unnamed_i0_instrument_controls_were_left_to_the_cost_cell_rule_until_the_gold_only_ruling(spec) -> None:
    """Ruling (a) did not retire them; the research-director ruling of 2026-11-13 (gold-only demo) did, on 11-14."""
    for arm_id in I0_KEPT:
        a = spec[arm_id]
        assert a.status == "run" and a.retired_day == "2026-11-14" and a.retired_rule == arms.GOLD_ONLY_RULE, arm_id


def test_the_learning_arms_that_take_the_freed_slots_stay_active_with_their_controls(spec) -> None:
    for arm_id in ("Z10D", "Z9D", "Z5P", "LCZ9D", "LCZ10D", "IEX1H"):
        assert not spec[arm_id].retired and spec[arm_id].status == "run", arm_id


def test_the_named_i0_rows_are_refused_and_kept_configured_on_their_accounts(accounts) -> None:
    for account_id, ids in I0_NAMED.items():
        cfg = exploration.config_for(accounts[account_id])
        assert cfg is not None
        running, refused = {a.arm_id for a in cfg.arms}, exploration.spec_retired(cfg)
        for arm_id in ids:
            assert arm_id in running, (account_id, arm_id)    # configured: an open position keeps every exit
            assert arm_id in refused, (account_id, arm_id)    # no new entry


def test_a_retired_gold_family_does_not_retire_its_per_instrument_rows_in_the_engine(accounts, spec) -> None:
    """I0 is retired; I0-AUDUSD on icm-probe was not by it: its stop was its own cost cell (control-thinning point
    4), and it is retired only by its own row (the gold-only ruling of 2026-11-13), never through its family."""
    cfg = exploration.config_for(accounts["icm-probe"])
    assert cfg is not None
    assert "I0-AUDUSD" in {a.arm_id for a in cfg.arms}
    assert spec["I0-AUDUSD"].retired_rule == arms.GOLD_ONLY_RULE
    assert not hasattr(exploration, "family_retirement")
