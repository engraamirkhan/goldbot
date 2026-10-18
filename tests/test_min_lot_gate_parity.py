"""min_lot_feasibility must make the RiskGate's sizing decision exactly (strategy-researcher F1, F2,
docs/research/sizability-2026-10-18.md): the same account-currency conversion and the same minimum-lot exception cap,
so for the GBP account it neither overstates risk by the GBPUSD rate nor reports "refused" where the gate places."""
import itertools
from typing import Any

import pytest

from goldbot.research.min_lot import LotSpec, min_lot_feasibility
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits

ATRS = [2.0, 8.0, 20.0, 55.0, 110.0]
STOP_MULTIPLES = [0.75, 1.0, 1.5]
BALANCES = [500.0, 1_000.0, 2_500.0, 10_000.0, 60_000.0]
CURRENCIES: list[tuple[str, float | None]] = [("USD", None), ("GBP", 1 / 1.33), ("GBP", None)]
CAPS = [None, 0.005, 0.01]
STAGES = [False, True]
RISK = 0.005


def _gate_decision(atr: float, mult: float, bal: float, cur: tuple[str, float | None], cap: float | None,
                   down: bool) -> Any:
    st = AccountState(equity=bal, balance_closed_hwm=bal, day_start_equity=bal, week_start_equity=bal,
                      open_positions=0, margin_used=0.0, last_tick_age_s=1.0, spread_points=25.0,
                      combined_size_down=down)
    it = Intent(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=mult, atr_usd=atr, cost_atr=0.01,
                multiplier=1.0, price=4_150.0, account_currency=cur[0], acct_per_usd=cur[1])
    return RiskGate(RiskLimits(risk_per_trade=RISK, min_lot_risk_cap=cap)).check(it, st)


@pytest.mark.parametrize("atr,mult,bal,cur,cap,down", list(itertools.product(
    ATRS, STOP_MULTIPLES, BALANCES, CURRENCIES, CAPS, STAGES)))
def test_min_lot_feasibility_places_or_refuses_exactly_as_the_gate_does(atr, mult, bal, cur, cap, down):
    d = _gate_decision(atr, mult, bal, cur, cap, down)
    f = min_lot_feasibility(price=4_150.0, atr_usd=atr, stop_atr=mult, risk_fraction=RISK, equity=bal,
                            lot=LotSpec(), size_down=down, spread_points=25.0, account_currency=cur[0],
                            acct_per_usd=cur[1], min_lot_risk_cap=cap)
    sizing_refusal = {"min_lot_exceeds_risk", "fx_conversion_unavailable"} & set(d.reasons)
    assert f.allowed == (not sizing_refusal)
    if sizing_refusal:
        assert f.reason == sorted(sizing_refusal)[0]
    elif d.allowed:
        assert f.lots == pytest.approx(d.lots)
        assert f.actual_risk_fraction == pytest.approx(d.risk_fraction)
        assert f.min_lot_exception == ("min_lot_exception" in d.reasons)


def test_gbp_account_risk_is_converted_so_a_gbp_balance_that_fits_is_not_reported_refused():
    # $10.90 stop: one 0.01 lot loses GBP 8.20 = 0.82% of GBP 1,000, over the 0.6% tolerance but within the 1% cap
    f = min_lot_feasibility(price=4_150.0, stop_distance=10.90, risk_fraction=0.005, equity=1_000.0,
                            account_currency="GBP", acct_per_usd=1 / 1.33, min_lot_risk_cap=0.01)
    assert f.min_lot_risk_acct == pytest.approx(10.90 / 1.33)
    assert f.actual_risk_fraction == pytest.approx(10.90 / 1.33 / 1_000.0) and f.allowed and f.min_lot_exception


def test_gbp_account_without_a_conversion_is_refused_not_guessed():
    f = min_lot_feasibility(price=4_150.0, stop_distance=10.0, risk_fraction=0.005, equity=1_000.0,
                            account_currency="GBP", acct_per_usd=None)
    assert f.allowed is False and f.reason == "fx_conversion_unavailable"


def test_the_exception_cap_is_off_by_default_and_halved_in_size_down():
    kw: dict[str, Any] = dict(price=4_150.0, stop_distance=30.0, risk_fraction=0.005, equity=3_000.0)
    # the minimum lot risks 1.0% of 3,000: allowed under a 1% cap; refused with the cap off or halved in size-down
    assert min_lot_feasibility(**kw, min_lot_risk_cap=0.01).allowed
    assert not min_lot_feasibility(**kw, min_lot_risk_cap=None).allowed
    assert not min_lot_feasibility(**kw, min_lot_risk_cap=0.01, size_down=True).allowed
