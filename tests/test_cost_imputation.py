"""Conservative imputed cost for gross closed trades (goldbot/ops/cost_imputation.py; quant review 2026-10-12): gross
rows are kept and charged the measured (or prior) commission plus paid swap for every rollover held through, the
triple day counted."""
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.execution.costs import CostTable
from goldbot.labels.triple_barrier import SwapSpec
from goldbot.ops.cost_imputation import CostImputer
from goldbot.ops.gates_phase import ClosedTrade

SETTINGS = load_settings()
TUE = pd.Timestamp("2026-10-06 10:00", tz="UTC")      # a Tuesday; Wednesday is the triple-swap rollover
PRICE = 2_000.0


def ct(**kw: Any) -> ClosedTrade:
    base: dict[str, Any] = dict(account_id="icm-demo", broker="icm", mode="demo", exit_utc=TUE + pd.Timedelta(hours=2),
                                ret=0.001, pnl=20.0, equity_before=10_000.0, side=1, lots=0.1, entry_utc=TUE,
                                entry_price=PRICE, initial_sl=PRICE - 10.0)
    base.update(kw)
    return ClosedTrade(**base)


def table(state: Path, *, commission: float = 3.0, swap_long: float | None = None, swap_short: float | None = None) -> None:
    CostTable(account_id="icm-demo", built_utc=TUE, spread={}, slippage={}, commission_per_lot_side_usd=commission,
              slippage_prior_usd=0.15, swap_long_usd_per_lot=swap_long, swap_short_usd_per_lot=swap_short,
              swap_triple_weekday=None if swap_long is None else 2).save(state / "costs_icm-demo.json")


def test_a_net_row_is_charged_nothing():
    c = CostImputer.from_settings(SETTINGS).charge(ct(commission=0.7, swap=0.0, pnl_basis="net", ret_basis="pnl"))
    assert not c.imputed and c.ret == 0.0 and c.r == 0.0


def test_a_gross_intraday_row_pays_the_measured_commission_both_sides(tmp_path):
    table(tmp_path, commission=3.0)
    c = CostImputer.from_state(tmp_path, SETTINGS).charge(ct(pnl_basis="gross"))
    assert c.imputed and c.priced
    assert c.usd_per_oz == pytest.approx(0.06)                       # 2 x $3 per lot / 100 oz, no rollover held
    assert c.ret == pytest.approx(0.06 / PRICE)
    assert c.r == pytest.approx(0.06 / 10.0)                         # in R: cost over the initial risk per oz
    assert c.usd == pytest.approx(0.06 * 0.1 * 100)


def test_without_a_cost_table_the_settings_commission_of_the_broker_is_charged():
    c = CostImputer.from_settings(SETTINGS).charge(ct(pnl_basis="gross"))
    assert c.usd_per_oz == pytest.approx(2 * SETTINGS.costs.commission_per_lot_side_usd["icm"] / 100)
    # an unknown broker pays the largest configured commission (conservative)
    c = CostImputer.from_settings(SETTINGS).charge(ct(broker="other", account_id="x", pnl_basis="gross"))
    assert c.usd_per_oz == pytest.approx(2 * max(SETTINGS.costs.commission_per_lot_side_usd.values()) / 100)


def test_swap_is_charged_for_every_rollover_held_with_the_triple_day_counted_three_times(tmp_path):
    table(tmp_path, commission=0.0, swap_long=-50.0, swap_short=10.0)
    t = ct(pnl_basis="gross", exit_utc=TUE + pd.Timedelta(days=2))   # Tuesday and Wednesday (triple) rollovers
    c = CostImputer.from_state(tmp_path, SETTINGS).charge(t)
    assert c.usd_per_oz == pytest.approx(4 * 50.0 / 100)


def test_a_swap_credit_is_never_counted():
    imp = CostImputer.from_settings(SETTINGS)
    imp.fallback["icm"] = imp.fallback["icm"].model_copy(update={
        "commission_per_lot_side_usd": 0.0,
        "swap": SwapSpec(long_usd_per_lot=-60.0, short_usd_per_lot=25.0, server_tz="Europe/Athens")})
    c = imp.charge(ct(side=-1, initial_sl=PRICE + 10.0, pnl_basis="gross", exit_utc=TUE + pd.Timedelta(days=2)))
    assert c.imputed and c.usd_per_oz == 0.0


def test_the_swap_prior_applies_when_the_table_has_no_measured_swap(tmp_path):
    table(tmp_path, commission=0.0)
    c = CostImputer.from_state(tmp_path, SETTINGS).charge(ct(pnl_basis="gross", exit_utc=TUE + pd.Timedelta(days=1)))
    assert c.usd_per_oz == pytest.approx(-SETTINGS.costs.swap_long_usd_per_lot / 100)


def test_only_the_components_the_return_lacks_are_charged(tmp_path):
    table(tmp_path, commission=3.0, swap_long=-50.0, swap_short=0.0)
    imp = CostImputer.from_state(tmp_path, SETTINGS)
    overnight = TUE + pd.Timedelta(days=1)
    # the commission is known and inside pnl (and so inside ret): only the unknown swap is charged
    assert imp.charge(ct(commission=0.6, pnl_basis="gross", exit_utc=overnight)).usd_per_oz == pytest.approx(0.5)
    # a price return carries neither, whatever the record's P&L says: both are charged
    t = ct(commission=0.6, swap=-5.0, pnl_basis="net", ret_basis="price", exit_utc=overnight)
    assert imp.charge(t).usd_per_oz == pytest.approx(0.06 + 0.5)


def test_a_legacy_row_without_a_basis_is_gross_unless_it_records_both_costs():
    imp = CostImputer.from_settings(SETTINGS)
    assert imp.charge(ct()).imputed
    assert not imp.charge(ct(commission=0.7, swap=0.0)).imputed


def test_a_row_that_cannot_be_priced_is_kept_counted_and_charged_nothing():
    imp = CostImputer.from_settings(SETTINGS)
    rows = [ct(pnl_basis="gross", entry_price=None), ct(pnl_basis="gross", entry_utc=None),
            ct(pnl_basis="net", commission=0.7, swap=0.0)]
    rets, s = imp.net_rets(rows)
    assert rets == [0.001, 0.001, 0.001]                              # nothing dropped
    assert s.n == 3 and s.n_gross == 2 and s.n_unpriced == 2
    assert "could not be priced" in s.text()


def test_r_falls_back_to_the_ratio_of_r_to_ret_when_the_initial_stop_is_unknown():
    c = CostImputer.from_settings(SETTINGS).charge(ct(pnl_basis="gross", initial_sl=None, r=2.0, ret=0.01))
    assert c.r == pytest.approx(c.ret * 200.0)
    assert CostImputer.from_settings(SETTINGS).charge(ct(pnl_basis="gross", initial_sl=None)).r is None


def test_the_summary_names_the_gross_count_and_the_total_imputed():
    imp = CostImputer.from_settings(SETTINGS)
    rets, s = imp.net_rets([ct(pnl_basis="gross"), ct(pnl_basis="gross"), ct(pnl_basis="net", commission=1, swap=0)])
    per = 2 * SETTINGS.costs.commission_per_lot_side_usd["icm"] / 100
    assert rets[0] == pytest.approx(0.001 - per / PRICE) and rets[2] == 0.001
    assert s.n_gross == 2 and s.total_ret == pytest.approx(2 * per / PRICE)
    assert s.total_usd == pytest.approx(2 * per * 0.1 * 100)
    assert s.text().startswith("2 gross row(s) charged an imputed cost of")
    assert imp.net_rets([])[1].text() == "all net"
