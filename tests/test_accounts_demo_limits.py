"""Demo limits in config/accounts.yaml (owner 2026-11-02: "fix the limits of our system to trade ... for the system to be
not bound to test, learn and improve signals"; the demo-limits ADR). Every flag is accepted on a demo probe_only
account only, every default is the old behaviour, and a live account's limits are unchanged:

* `exploration.pot_budgets` (default true): false drops the pots' £ sleeves (a pot then needs no `gbp`, and the pots
  are not summed against the budget); `exploration.learning_max_open` (default 0) bounds the learning arms;
* `demo_weekend_flat` (default false) and `news_blackout_entries` (default true): off their default only on a demo
  probe_only account;
* the probe host cap `max_positions` up to 24;
* `engine_limits`: `blackout_entries` false only on a demo probe_only account that says so, never on the paper stand-in.
"""
from __future__ import annotations

from typing import Any

import pytest

from goldbot.config import load_settings
from goldbot.ops import accounts as acc_mod
from goldbot.ops.services import engine as svc

DEMO = ("icm-demo", "icm-probe", "vantage-demo")


def _accounts() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(logins=False)


def _with(acc_id: str, **change: Any) -> acc_mod.Account:
    """`acc_id` from accounts.yaml with `change` (dotted `exploration.<key>` for its exploration block), validated."""
    raw = _accounts()[acc_id].model_dump()
    for k, v in change.items():
        if k.startswith("exploration."):
            raw["exploration"][k.split(".", 1)[1]] = v
        else:
            raw[k] = v
    return acc_mod.Account.model_validate(raw)


def test_the_defaults_are_the_old_behaviour() -> None:
    ex = acc_mod.Exploration()
    assert ex.pot_budgets is True and ex.learning_max_open == 0
    acc = acc_mod.Account.model_validate({**_accounts()["icm-demo"].model_dump(), "demo_weekend_flat": False,
                                          "news_blackout_entries": True})
    assert acc.demo_weekend_flat is False and acc.news_blackout_entries is True


def test_a_pot_without_gbp_loads_only_without_pot_budgets() -> None:
    pots = {"scalp": {"max_open": 4}}
    arms = {"S0": {"style": "scalp", "max_loss": 6.5, "control_share": 0.25}}
    with pytest.raises(ValueError, match="gbp"):
        _with("icm-probe", **{"exploration.pots": pots, "exploration.arms": arms, "exploration.pot_budgets": True})
    acc = _with("icm-probe", **{"exploration.pots": pots, "exploration.arms": arms, "exploration.pot_budgets": False})
    assert acc.exploration.pots["scalp"].gbp is None


def test_a_pot_that_names_its_gbp_always_fits_inside_the_budget() -> None:
    """Trading-safety M1: without pot budgets a pot that names its £ still binds, so it must still fit the day."""
    big = {"scalp": {"gbp": 10_000, "max_open": 4}}
    arms = {"S0": {"style": "scalp", "max_loss": 6.5, "control_share": 0.25}}
    for on in (True, False):
        with pytest.raises(ValueError, match="pots must fit"):
            _with("icm-probe", **{"exploration.pots": big, "exploration.arms": arms, "exploration.pot_budgets": on})


@pytest.mark.parametrize("flag, value", [("demo_weekend_flat", True), ("news_blackout_entries", False)])
def test_a_demo_flag_off_its_default_loads_only_on_a_demo_probe_only_account(flag: str, value: bool) -> None:
    assert getattr(_with("icm-probe", **{flag: value}), flag) is value
    with pytest.raises(ValueError, match=flag):
        _with("icm-live", **{flag: value})
    plain = _accounts()["icm-demo"].model_dump()
    plain.update(probe_only=False, max_positions=None, exploration={"enabled": False}, cost_probe={"enabled": False})
    with pytest.raises(ValueError, match=flag):
        acc_mod.Account.model_validate({**plain, flag: value})


def test_the_probe_host_cap_goes_up_to_24_and_no_further() -> None:
    assert _with("icm-probe", max_positions=24).max_positions == 24
    with pytest.raises(ValueError):
        _with("icm-probe", max_positions=25)


# ------------------------------------------------------------------------------------------------ engine_limits
def test_blackout_entries_are_off_only_on_a_demo_probe_only_account_that_says_so_and_never_on_paper() -> None:
    s = load_settings()
    off = _with("icm-probe", news_blackout_entries=False)
    assert svc.engine_limits(s, off).blackout_entries is False
    assert svc.engine_limits(s, off, paper=True).blackout_entries is True
    assert svc.engine_limits(s, _with("icm-probe", news_blackout_entries=True)).blackout_entries is True


def test_every_live_account_keeps_blackout_entries_and_no_weekend_flat() -> None:
    s = load_settings()
    for acc in _accounts().values():
        if acc.mode == "live" and not acc.is_observe_only:
            assert svc.engine_limits(s, acc).blackout_entries is True, acc.account_id
            assert svc.engine_weekend_flat(acc) is False, acc.account_id


def test_the_engine_gets_demo_weekend_flat_only_from_a_demo_probe_only_account() -> None:
    assert svc.engine_weekend_flat(_with("icm-probe", demo_weekend_flat=True)) is True
    assert svc.engine_weekend_flat(_with("icm-probe", demo_weekend_flat=False)) is False


def test_the_news_blackout_is_still_computed_on_every_engine() -> None:
    """The blackout state is still raised on every account (journal, records' `in_blackout`); only the gate's refusal
    is account policy (`RiskLimits.blackout_entries`)."""
    import inspect
    assert "news_blackout=True," in inspect.getsource(svc)


# ------------------------------------------------------------------------------------------------ the shipped config
def test_the_three_demo_learning_hosts_carry_the_demo_limits() -> None:
    accs = _accounts()
    for acc_id in DEMO:
        a = accs[acc_id]
        assert a.mode == "demo" and a.probe_only, acc_id
        assert a.exploration.pot_budgets is False and a.exploration.learning_max_open == 6, acc_id
        assert a.demo_weekend_flat is True and a.news_blackout_entries is False, acc_id
        assert a.max_positions == 24 and a.exploration.max_open_total == 23, acc_id
    pots = {k: {s: p.max_open for s, p in accs[k].exploration.pots.items()} for k in DEMO}
    assert accs["icm-demo"].exploration.pots["growth"].gbp == 80        # M1: GS1/GS1C's £80 day unchanged
    assert pots == {"icm-demo": {"scalp": 11, "intraday": 10, "swing": 3, "growth": 2},
                    "icm-probe": {"scalp": 19, "intraday": 6}, "vantage-demo": {"scalp": 11, "intraday": 6, "swing": 2}}


def test_the_cost_probe_shares_grow_and_each_day_still_sums_to_at_most_one() -> None:
    accs = _accounts()
    assert accs["icm-probe"].cost_probe.budget_share == pytest.approx(0.2363)
    assert accs["icm-probe"].exploration.budget_share == pytest.approx(0.7637)
    assert accs["vantage-demo"].cost_probe.budget_share == pytest.approx(0.175)
    assert accs["vantage-demo"].exploration.budget_share == pytest.approx(0.825)
    for acc_id in ("icm-probe", "vantage-demo"):
        a = accs[acc_id]
        assert a.cost_probe.budget_share + a.exploration.budget_share == pytest.approx(1.0)


def test_the_weekend_lane_and_the_live_accounts_keep_every_old_limit() -> None:
    accs = _accounts()
    for acc in accs.values():
        if acc.account_id in DEMO:
            continue
        assert acc.demo_weekend_flat is False and acc.news_blackout_entries is True, acc.account_id
        assert acc.exploration.pot_budgets is True and acc.exploration.learning_max_open == 0, acc.account_id
