"""Item 131 (docs/design/trades-ux-2026-10-27.md §2.4, §3.5): Today per account from the trade ledger, one builder
with GET /api/trades. Fills against the per-account target and the pace; net after costs equal to today's closed
ledger rows to the penny; caps, open risk and the exploration sleeves used; a 14-trading-day net strip with the daily
target, week and month to date; quiet-day wording only when the volatility screen publishes it; the arms' own money
withheld before their read (ADR 0050)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.api import home_cards as hc
from goldbot.api import trades_ledger as tl
from goldbot.api.today_ledger import AccountPlan, account_day, plans_from_config
from goldbot.config import load_settings
from tests.test_trades_ledger import DAY, NOW, SCALP, _arm, _catalog, _closed, _engine, _probe


@pytest.fixture(autouse=True)
def _accounts(enabled_accounts):
    enabled_accounts(SCALP, DAY)


def _day(state: Path, cat: Any, account: str = SCALP, plan: AccountPlan | None = None,
         **engine: Any) -> dict[str, Any]:
    _engine(state, account, **engine)
    led = tl.build_ledger(state, NOW, cat=cat)
    e = {"open_positions": 1, "open_risk": 2.4, "equity": 10_050.0, "day_start_equity": 10_000.0,
         "week_start_equity": 10_100.0, "daily_loss_limit": 200.0, "daily_loss_remaining": 180.0,
         "limits": {"weekly_cap": 0.05}, **engine}
    return account_day(account, e, led.rows, NOW, plan or AccountPlan(fills_target=200, pot=138.0), cat)


def _three_arms(state: Path) -> None:
    _arm(state, "a1", "S1A", pnl=1.10)
    _arm(state, "a2", "S2A", pnl=-0.40)
    _arm(state, "a3", "S3A", pnl=0.25)
    _arm(state, "c1", "S0", pnl=-0.60)


def test_todays_net_equals_the_sum_of_todays_closed_ledger_rows_to_the_penny(tmp_path):
    _three_arms(tmp_path)
    cat = _catalog(tmp_path)
    d = _day(tmp_path, cat)
    rows = [r for r in tl.build_ledger(tmp_path, NOW, cat=cat).rows if r.status == "closed"]
    assert d["net_today"] == round(sum(r.pnl or 0 for r in rows), 2) == round(1.10 - 0.40 + 0.25 - 0.60, 2)
    assert d["costs_today"].total == round(sum(r.costs.total for r in rows if r.costs), 2)
    page = tl.query(tl.build_ledger(tmp_path, NOW, cat=cat), tl.TradeQuery(account=(SCALP,)), cat)
    assert page.totals.net_pnl == d["net_today"]                                  # the List tab's line agrees


def test_fills_are_counted_against_the_accounts_target_with_a_pace(tmp_path):
    _three_arms(tmp_path)
    _arm(tmp_path, "o1", "S1A", exit_after=None, sent=NOW - pd.Timedelta(minutes=30))
    d = _day(tmp_path, _catalog(tmp_path))
    assert (d["fills_today"], d["fills_target"]) == (5, 200)
    # 5 fills in the last 2 h, 10 h to the day's end (22:00 UTC): 5 + 5 / 2 x 10
    assert d["pace"] == 30
    assert d["by_kind"]["exploration"].fills == 4 and d["by_kind"]["control"].fills == 1


def test_the_arms_own_money_is_withheld_before_their_read_but_other_kinds_show(tmp_path):
    _three_arms(tmp_path)
    _engine(tmp_path, DAY)
    _probe(tmp_path)
    _closed(tmp_path, DAY, "demo", 41, 3.95, "trail_close")
    cat = _catalog(tmp_path)
    scalp = _day(tmp_path, cat)
    for k in ("exploration", "control"):
        kd = scalp["by_kind"][k]
        assert kd.withheld and kd.net_pnl is None and kd.net_r is None and kd.closed >= 1
    day = _day(tmp_path, cat, account=DAY)
    assert day["by_kind"]["cost_probe"].net_pnl == 1.41 and day["by_kind"]["strategy"].net_pnl == 3.95
    read = _day(tmp_path, _catalog(tmp_path, read=("S1A", "S2A", "S3A", "S0")))
    assert read["by_kind"]["control"].net_pnl == -0.60


def test_a_day_under_three_unread_arms_withholds_its_net_and_names_it(tmp_path):
    _arm(tmp_path, "a1", "S1A", pnl=1.10)
    _arm(tmp_path, "a2", "S2A", pnl=-0.40)
    d = _day(tmp_path, _catalog(tmp_path))
    assert d["net_today"] is None and "net_today" in d["withheld"] and d["exploration_pot_used"] is None
    assert d["strip"][-1].withheld and d["strip"][-1].net_pnl is None


def test_the_strip_has_14_trading_days_with_the_daily_target_and_week_and_month_to_date(tmp_path):
    _three_arms(tmp_path)
    for i, d in enumerate(("2026-10-20", "2026-10-21")):
        for j, arm in enumerate(("S1A", "S2A", "S3A")):
            _arm(tmp_path, f"h{i}{j}", arm, sent=pd.Timestamp(f"{d} 10:00", tz="UTC"), pnl=1.0)
    out = _day(tmp_path, _catalog(tmp_path))
    strip = out["strip"]
    assert len(strip) == 14 and all(s.day.weekday() < 5 for s in strip) and strip[-1].day.isoformat() == "2026-10-27"
    assert all(s.target == 500.0 for s in strip)                         # 5% of the £10,000 day start (ADR 0004)
    assert next(s for s in strip if s.day.isoformat() == "2026-10-21").net_pnl == 3.0
    assert out["week_to_date"].since.isoformat() == "2026-10-26" and out["week_to_date"].n == 4
    assert out["month_to_date"].n == 10 and out["month_to_date"].net_pnl == round(6.0 + 0.35, 2)


def test_caps_open_risk_and_the_exploration_sleeves_used(tmp_path):
    _three_arms(tmp_path)
    _arm(tmp_path, "o1", "S1A", exit_after=None, stop_loss=2.4)
    d = _day(tmp_path, _catalog(tmp_path))
    assert (d["open_n"], d["open_risk"]) == (1, 2.4)
    assert (d["day_cap_used"].used, d["day_cap_used"].limit) == (20.0, 200.0)
    assert (d["week_cap_used"].used, d["week_cap_used"].limit) == (50.0, 505.0)
    # realised arm losses today: S2A 0.40 + S0 0.60 (S1A and S3A are up) + the open arm's 2.40 at its stop
    assert d["exploration_pot_used"].used == 3.4 and d["exploration_pot_used"].limit == 138.0


def test_quiet_day_wording_only_when_the_volatility_screen_says_so(tmp_path):
    cat = _catalog(tmp_path)
    assert _day(tmp_path, cat)["quiet_day"] is None
    assert _day(tmp_path, cat, opportunity_day=False)["quiet_day"] is True
    assert _day(tmp_path, cat, opportunity_day=True)["quiet_day"] is False


def test_a_live_account_has_no_fill_target_and_plans_come_from_settings():
    plans = plans_from_config(load_settings())
    assert plans and all((p.fills_target is None) == (k.endswith("-live")) for k, p in plans.items())
    assert all(p.fills_target == 200 for k, p in plans.items() if not k.endswith("-live"))


def test_the_today_view_carries_the_ledger_fields_per_account(tmp_path, monkeypatch):
    _three_arms(tmp_path)
    _engine(tmp_path, SCALP)
    cat = _catalog(tmp_path)
    monkeypatch.setattr(tl, "catalog", lambda: cat)
    monkeypatch.setattr(hc, "catalog", lambda: cat)
    v = hc.today_money(tmp_path, NOW, {}, plans={SCALP: AccountPlan(fills_target=200)})
    a = v.accounts[0]
    assert (a.account_label, a.fills_today, a.fills_target) == ("Scalp lab", 4, 200) and len(a.strip) == 14
    assert a.net_today == round(1.10 - 0.40 + 0.25 - 0.60, 2) and v.unreadable == []


def test_closed_pnl_is_withheld_with_the_day_so_the_other_kinds_cannot_reveal_the_arms(tmp_path, monkeypatch):
    """Quant condition 2: day P&L less open less closed (positions and probes) would be the arms' result."""
    _arm(tmp_path, "a1", "S1A", pnl=1.10)
    _arm(tmp_path, "a2", "S2A", pnl=-0.40)
    _engine(tmp_path, SCALP)
    cat = _catalog(tmp_path)
    monkeypatch.setattr(tl, "catalog", lambda: cat)
    monkeypatch.setattr(hc, "catalog", lambda: cat)
    a = hc.today_money(tmp_path, NOW, {}).accounts[0]
    assert a.closed_pnl is None and a.costs is None and "closed_pnl" in a.withheld
    assert a.day_pnl is not None                                             # the broker's figure: the owner's money
