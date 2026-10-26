"""Arms expansion wave 1 (docs/research/arms-expansion-2026-10-26.md sections 3-5): the shipped arm spec and account
registry carry the 14 specialist families, their declared variants and the C1H/VC1H controls (19 new arm ids), each
frozen by its hash, on the host section 3 names, at "£17 (15m) / £30 (1h)" per trade, with the intraday pot's
`max_open` 5 -> 9 and the controls at "fixed share 0.25 of their pot, never bandit-allocated". "Account caps
unchanged"; live accounts stay disabled and every login stays null."""
from __future__ import annotations

from pathlib import Path

import pytest

from goldbot.ops import accounts as acc_mod
from goldbot.research import arms
from goldbot.research.arm_rules_specialist import build_specialist

SPEC = Path(__file__).resolve().parents[1] / "config" / "exploration_arms.yaml"
ICM = {"XBB15": "box_break", "XTV1": "smc_structure", "XEQ15": "eq_liquidity_run", "XSO15": "session_open",
       "XIMNY": "intraday_momentum", "XIMLN": "intraday_momentum", "XAD1H": "asia_drift", "XMR15": "mean_reversion",
       "XMR1H": "mean_reversion"}
VANTAGE = {"VXRIP1H": "box_rip", "VXRT1H": "box_retest", "VXFK1H": "fakey", "VXTL1H": "trend_level_signal",
           "VXTV2": "smc_structure", "VXBO1H": "breakout", "VXBO15": "breakout", "VXTR1H": "trend"}
CONTROLS = {"icm-demo": "C1H", "vantage-demo": "VC1H"}
BLOCKED = {"XTV1", "VXTV2"}      # SRE profile 2026-10-26: the smc replica state is not fed on the engine's bars
CAP = {"15m": 17.0, "1h": 30.0}


@pytest.fixture(scope="module")
def spec() -> dict[str, arms.Arm]:
    return {a.id: a for a in arms.load_arms(SPEC)}


@pytest.fixture(scope="module")
def accounts() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(logins=False)


def test_wave_one_adds_nineteen_hash_frozen_arm_ids(spec) -> None:
    new = set(ICM) | set(VANTAGE) | set(CONTROLS.values())
    assert len(new) == 19 and new <= set(spec)
    assert all(spec[i].config_hash == arms.arm_hash(spec[i]) and spec[i].max_open == 1 for i in new)


@pytest.mark.parametrize("arm_id", sorted({**ICM, **VANTAGE}))
def test_every_specialist_arm_resolves_its_family_on_its_timeframe(spec, arm_id) -> None:
    arm = spec[arm_id]
    family = {**ICM, **VANTAGE}[arm_id]
    assert arm.rule == "specialist" and arm.params["specialist"] == family
    built = build_specialist(arm)
    assert built.family == family and built.timeframe == arm.timeframe
    assert arm.status == ("blocked" if arm_id in BLOCKED else "run")
    # the stop band stops at the per-trade cap at 0.01 lot ($1.329 per £, as G1A/F1A)
    assert arm.params["min_stop_usd"] == 3.0 and arm.params["max_stop_usd"] <= CAP[arm.timeframe] * 1.33


def test_the_controls_are_1h_random_brackets_on_local_clocks(spec) -> None:
    for arm_id in CONTROLS.values():
        a = spec[arm_id]
        assert (a.rule, a.timeframe, a.status) == ("random_bracket", "1h", "run")
        assert a.params["minutes"] == [0] and a.params["stop_atr"] == 1.5 and a.params["target_r"] == 2.0
        assert a.params["hold_bars"] == 48 and arms.local_window(a.params) is not None


@pytest.mark.parametrize(("account", "ids"), [("icm-demo", ICM), ("vantage-demo", VANTAGE)])
def test_each_host_registers_its_wave_one_arms_at_the_timeframe_cap(spec, accounts, account, ids) -> None:
    reg = accounts[account].exploration.arms
    for arm_id in ids:
        assert reg[arm_id].style == "intraday" and reg[arm_id].control_share is None
        assert reg[arm_id].max_loss == CAP[spec[arm_id].timeframe]
    ctl = reg[CONTROLS[account]]
    assert (ctl.style, ctl.max_loss, ctl.control_share) == ("intraday", 30.0, 0.25)
    other = "vantage-demo" if account == "icm-demo" else "icm-demo"
    assert not set(ids) & set(accounts[other].exploration.arms), "a wave-1 arm runs on one host only"
    assert not set(ids) & set(accounts["icm-probe"].exploration.arms)


@pytest.mark.parametrize(("account", "gbp", "cap"), [("icm-demo", 108, 220), ("vantage-demo", 98, 200)])
def test_the_intraday_pot_opens_nine_and_no_other_limit_moves(accounts, account, gbp, cap) -> None:
    acc = accounts[account]
    ex = acc.exploration
    assert (ex.pots["intraday"].gbp, ex.pots["intraday"].max_open) == (gbp, 9)
    assert ex.pots["swing"].max_open == 1 and ex.max_open_total == 17 and ex.budget_share == 0.9
    assert acc.daily_loss_limit_abs == cap and acc.max_positions == 18
    assert acc.risk_overrides is not None and acc.risk_overrides.ceilings.weekly == 0.06
    assert sum(a.control_share or 0.0 for a in ex.arms.values() if a.style == "intraday") <= 1


def test_live_accounts_stay_disabled_and_no_login_is_written(accounts) -> None:
    assert not accounts["icm-live"].enabled and not accounts["vantage-live"].enabled
    raw = (SPEC.parent / "accounts.yaml").read_text()
    assert all(line.split("#")[0].strip() == "login: null" for line in raw.splitlines() if "login:" in line.split("#")[0])
