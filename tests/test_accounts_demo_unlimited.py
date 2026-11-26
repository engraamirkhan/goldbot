"""The `demo_unlimited` account flag and the demo margin-safety floor setting (owner decision 2026-11-25: "there are no
limits anymore for demo accounts", "allow absolutely anything"; ADR 0097).

* accounts.yaml `demo_unlimited: true` is allowed on a demo probe_only account only (refused at load anywhere else,
  never ignored quietly); every enabled demo probe_only account in the repo config carries it.
* `engine_limits` sets `RiskLimits.demo_unlimited` only for such an account against its real terminal (never the
  paper stand-in), with the floor from settings.yaml `risk.demo_floor`.
* Every live and observe-only account's limits are exactly what they were: the flag off, every cap from settings.yaml
  and accounts.yaml."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from goldbot.config import DemoFloor, load_settings
from goldbot.ops import accounts as acc_mod
from goldbot.ops.services.engine import engine_limits
from goldbot.risk import RiskLimits

ROOT = Path(__file__).parents[1]


def _accounts() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(ROOT / "config" / "accounts.yaml", logins=False)


def test_settings_carry_the_demo_floor_on_at_a_fifth_of_the_high_water_mark_and_200_percent_margin() -> None:
    floor = load_settings().risk.demo_floor
    assert floor == DemoFloor(enabled=True, min_equity_frac=0.2, min_margin_level=2.0)
    raw = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())["risk"]["demo_floor"]
    assert raw == {"enabled": True, "min_equity_frac": 0.2, "min_margin_level": 2.0}


@pytest.mark.parametrize("bad", [{"min_equity_frac": 0.0}, {"min_equity_frac": 1.0}, {"min_margin_level": 0.5}])
def test_a_demo_floor_that_cannot_protect_the_balance_is_refused(bad) -> None:
    with pytest.raises(ValueError):
        DemoFloor(**bad)


def test_only_icm_probe_is_unlimited_during_its_canary() -> None:
    """ADR 0097 rollout (D22): the flag defaults to false everywhere and is set true on icm-probe only at its canary
    (owner 2026-11-25 "deploy and switch asap"); the other demo accounts follow after a clean 24 h canary and the
    W2-4 dispatch. A config that turned it on anywhere else first would fail here."""
    accounts = _accounts()
    assert acc_mod.Account.model_fields["demo_unlimited"].default is False
    assert {k for k, a in accounts.items() if a.demo_unlimited} == {"icm-probe"}
    raw = yaml.safe_load((ROOT / "config" / "accounts.yaml").read_text())["accounts"]
    assert {k for k, a in raw.items() if "demo_unlimited" in a} == {"icm-demo", "icm-probe", "vantage-demo", "vantage-247"}


@pytest.mark.parametrize("change", [{"mode": "live", "approval_mode": "propose", "probe_only": False,
                                     "exploration": {"enabled": False}},
                                    {"probe_only": False}])
def test_demo_unlimited_off_a_demo_probe_only_account_is_refused_at_load(change) -> None:
    probe = _accounts()["icm-probe"]
    with pytest.raises(ValueError):
        probe.model_validate({**probe.model_dump(), **change, "demo_unlimited": True})


def test_engine_limits_set_the_flag_and_the_floor_for_an_unlimited_demo_account_only() -> None:
    s = load_settings()
    probe = _accounts()["icm-probe"].model_copy(update={"demo_unlimited": True})     # as at the canary
    lim = engine_limits(s, probe)
    assert lim.demo_unlimited and lim.demo_floor == s.risk.demo_floor
    assert not engine_limits(s, probe, paper=True).demo_unlimited                # the paper stand-in: never
    assert not engine_limits(s, probe.model_copy(update={"demo_unlimited": False})).demo_unlimited


def test_every_live_and_observe_only_account_keeps_every_limit_unchanged() -> None:
    s = load_settings()
    live = {k: a for k, a in _accounts().items() if a.mode != "demo"}
    assert live, "the repo config names live accounts"
    for k, a in live.items():
        lim = engine_limits(s, a)
        assert lim.demo_unlimited is False, k
        base = RiskLimits.from_settings(s.risk, tiny_live=acc_mod.tiny_live_risk(a))
        if lim.pilot is None:
            for f in ("daily_cap", "weekly_cap", "dd_stage1", "dd_stage2", "max_positions", "max_spread_points",
                      "margin_level_floor", "max_combined_lots", "max_combined_notional_frac", "leverage_cap"):
                assert getattr(lim, f) == getattr(base, f), (k, f)
        assert lim.daily_loss_limit_abs == a.daily_loss_limit_abs, k
        assert lim.blackout_entries is True and lim.probe_only is False, k


LIVE_FIXTURE = ROOT / "tests" / "fixtures" / "live_limits_0bb07649.json"


def test_the_limits_of_every_account_that_is_not_demo_probe_only_are_byte_identical_to_before_adr_0097() -> None:
    """Owner rule 2026-11-25: real money unchanged. `engine_limits` of every live, observe-only and non-probe account,
    serialised, equals what origin/main 0bb07649 gave before this change (the fixture, written by running the same
    dump there). A deliberate, owner-approved change to a live limit regenerates the fixture in its own PR, so the
    diff of this file is the review signal; nothing else may move it. Regenerated once for ADR 0106 decision 2 (the
    owner's real-money limits): `real_money` and `adv_reference` on every account, and icm-live's pilot within 1 % /
    2 % with its drawdown stages back on."""
    import json
    s = load_settings()
    now = {k: json.loads(engine_limits(s, a).model_dump_json(exclude={"demo_unlimited", "demo_floor"}))
           for k, a in sorted(_accounts().items()) if not (a.mode == "demo" and a.probe_only)}
    assert json.dumps(now, sort_keys=True) == json.dumps(json.loads(LIVE_FIXTURE.read_text()), sort_keys=True)
    assert {"icm-live", "vantage-live"} <= set(now)


@pytest.mark.parametrize("change, ok", [({"order_timeout_s": 35.0}, False), ({"order_timeout_s": 15.0}, True),
                                        ({"order_timeout_s": 35.0, "order_lane": True}, True)])
def test_the_flag_needs_the_order_lane_or_an_order_timeout_of_15_s_at_most(change, ok) -> None:
    """Trading-safety M1: on Vantage (35 s, no order lane) a slow fill would hold every exit behind it; the loader
    refuses the flag there instead of relying on convention."""
    probe = _accounts()["icm-probe"]
    body = {**probe.model_dump(), **change, "demo_unlimited": True}
    if ok:
        assert probe.model_validate(body).demo_unlimited
    else:
        with pytest.raises(ValueError, match="order lane"):
            probe.model_validate(body)


def test_vantage_demo_cannot_take_the_flag_as_configured() -> None:
    vd = _accounts()["vantage-demo"]
    with pytest.raises(ValueError):
        vd.model_validate({**vd.model_dump(), "demo_unlimited": True})
