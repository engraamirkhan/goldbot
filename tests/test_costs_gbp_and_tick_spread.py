"""costs-v1 for a GBP account (docs/research/costs-binding-2026-10-18.md section 1.3, K1-K4): the terminal's swap and
commission are converted to USD with the gate's conversion (`sizing.account_fx` of the broker's own figure), the
recorded broker ticks (`goldbot.data.ticks.load_day`) give a measured spread profile by UTC hour and session without
any fill, and the published table says per component whether it is measured, a prior or a mix, never one passed off
as the other. MetaTrader5 is Windows-only: the adapter is driven by a fake `mt5` module."""
import json
from collections import namedtuple
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.config import load_settings
from goldbot.data import ticks as tk
from goldbot.data.store import Store
from goldbot.execution import mt5_adapter
from goldbot.execution.costs import (
    BrokerTerms,
    CostTable,
    FieldSource,
    PublishedCostTable,
    SlippageStat,
    SpreadProfile,
    SpreadStat,
    load_research_cost_table,
    publishable,
    research_costs,
    spread_profile,
)
from goldbot.execution.mt5_adapter import measure_broker_terms, swap_usd_per_lot
from goldbot.ops.accounts import Account
from goldbot.ops.jobs import JobContext, nightly_costs, recorded_spread_profile
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.population import Population
from goldbot.research.registry import TrialRegistry

SETTINGS = load_settings()
NOW = pd.Timestamp("2026-11-20 23:10", tz="UTC")
TZ = "Europe/Athens"
ACC = Account(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=None, terminal_path="",
              server_tz=TZ, symbol="XAUUSD", magic_base=260100, enabled=True)
GBP_PER_USD = 0.8          # the broker's conversion in these fixtures: 1 USD of XAUUSD P&L is 0.80 GBP

SymInfo = namedtuple("SymInfo", "name visible swap_mode swap_long swap_short swap_rollover3days point trade_tick_value "
                                "trade_tick_size trade_contract_size currency_base currency_profit currency_margin")
Deal = namedtuple("Deal", "ticket position_id symbol type entry volume price commission fee swap profit time_msc")


def info(**kw: Any) -> SymInfo:
    """XAUUSD on a GBP deposit: the tick value is per lot in GBP (1 USD a 0.01 tick is 0.80 GBP)."""
    base = dict(name="XAUUSD", visible=True, swap_mode=1, swap_long=-55.0, swap_short=12.0, swap_rollover3days=3,
                point=0.01, trade_tick_value=GBP_PER_USD, trade_tick_size=0.01, trade_contract_size=100.0,
                currency_base="XAU", currency_profit="USD", currency_margin="USD")
    return SymInfo(**{**base, **kw})


def gbp_deals() -> pd.DataFrame:
    """One closed 1-lot position charged 2.80 GBP a side (5.60 GBP = 7.00 USD round trip at 0.80)."""
    return pd.DataFrame([Deal(1, 10, "XAUUSD", 0, 0, 1.0, 2500.0, -2.8, 0.0, 0.0, 0.0, 0)._asdict(),
                         Deal(2, 10, "XAUUSD", 1, 1, 1.0, 2501.0, -2.8, 0.0, -4.0, 80.0, 0)._asdict()])


# ------------------------------------------------------------------------------------------------ K1: swap in GBP
def test_points_swap_on_a_gbp_account_is_converted_to_usd_at_the_brokers_rate():
    lng, sht, note = swap_usd_per_lot(info(), account_currency="GBP", price=2500.0, acct_per_usd=GBP_PER_USD)
    assert (lng, sht) == pytest.approx((-55.0, 12.0))           # 0.80 GBP a point / 0.80 GBP per USD = 1 USD a point
    assert "0.8000 GBP per USD" in note


def test_deposit_currency_swap_on_a_gbp_account_is_converted_to_usd_at_the_brokers_rate():
    lng, sht, note = swap_usd_per_lot(info(swap_mode=4, swap_long=-48.0, swap_short=8.0), account_currency="GBP",
                                      price=2500.0, acct_per_usd=GBP_PER_USD)
    assert (lng, sht) == pytest.approx((-60.0, 10.0)) and "GBP per USD" in note


@pytest.mark.parametrize("currency, rate", [("GBP", None), ("GBP", float("nan")), ("GBP", 0.0), ("", 0.8),
                                            ("USD", 0.8)])           # USD more than 1% off 1: not XAUUSD terms
def test_a_swap_needing_a_conversion_the_broker_did_not_give_stays_unmeasured(currency, rate):
    for mode in (1, 4):
        lng, sht, note = swap_usd_per_lot(info(swap_mode=mode), account_currency=currency, price=2500.0,
                                          acct_per_usd=rate)
        assert lng is None and sht is None and "prior" in note


def test_a_usd_account_converts_as_before_without_a_broker_rate():
    lng, sht, _ = swap_usd_per_lot(info(trade_tick_value=1.0), account_currency="USD", price=2500.0)
    assert (lng, sht) == pytest.approx((-55.0, 12.0))


def test_a_swap_already_in_usd_needs_no_account_conversion_on_a_gbp_account():
    lng, _, _ = swap_usd_per_lot(info(swap_mode=3, swap_long=-60.0), account_currency="GBP", price=2500.0)
    assert lng == pytest.approx(-60.0)                           # margin currency USD: no deposit currency involved


# ------------------------------------------------------------------------------------------------ K2: commission in GBP
def test_commission_on_a_gbp_account_is_converted_to_usd_at_the_brokers_rate():
    t = measure_broker_terms("icm-demo", info(), account_currency="GBP", price=2500.0, deals=gbp_deals(), now=NOW,
                             acct_per_usd=GBP_PER_USD)
    assert t.commission_per_lot_round_trip_usd == pytest.approx(7.0) and t.commission_lots == pytest.approx(1.0)
    assert t.swap_long_usd_per_lot == pytest.approx(-55.0)
    assert any("commission" in n and "0.8000 GBP per USD" in n for n in t.notes)


def test_commission_on_a_gbp_account_without_the_brokers_rate_stays_the_setting():
    t = measure_broker_terms("icm-demo", info(), account_currency="GBP", price=2500.0, deals=gbp_deals(), now=NOW,
                             acct_per_usd=None)
    assert t.commission_per_lot_round_trip_usd is None and t.swap_long_usd_per_lot is None
    assert any("commission from settings" in n for n in t.notes)


def test_the_adapter_converts_a_gbp_terminals_swap_and_commission_with_order_calc_profit(monkeypatch):
    Tick = namedtuple("Tick", "time_msc bid ask")
    Acct = namedtuple("Acct", "currency")

    def calc_profit(action: int, symbol: str, lots: float, p_open: float, p_close: float) -> float:
        return (p_close - p_open) * (1 if action == 0 else -1) * 100.0 * lots * GBP_PER_USD

    deals = [Deal(*row) for row in gbp_deals().itertuples(index=False)]
    fake = SimpleNamespace(initialize=lambda **kw: True, symbol_select=lambda s, on: True, symbol_info=lambda s: info(),
                           last_error=lambda: (0, ""), shutdown=lambda: None, account_info=lambda: Acct("GBP"),
                           symbol_info_tick=lambda s: Tick(0, 2499.9, 2500.1), history_deals_get=lambda a, b: tuple(deals),
                           ORDER_TYPE_BUY=0, ORDER_TYPE_SELL=1, order_calc_profit=calc_profit)
    monkeypatch.setattr(mt5_adapter, "mt5", fake)
    b = mt5_adapter.MT5Broker(terminal_path="", login=1, password="x", server="s", server_tz=TZ, symbol="XAUUSD",
                              account_label="icm-demo", verify=None)
    t = b.broker_terms("icm-demo", NOW - pd.Timedelta(days=30), NOW)
    assert t.swap_long_usd_per_lot == pytest.approx(-55.0) and t.commission_per_lot_round_trip_usd == pytest.approx(7.0)


# ------------------------------------------------------------------------------------------------ K4: tick spread profile
def _day_ticks(day: date, spreads_by_hour: dict[int, list[float]]) -> pd.DataFrame:
    rows = []
    for h, spreads in spreads_by_hour.items():
        for k, s in enumerate(spreads):
            ts = pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=h, seconds=k)
            rows.append({"ts_utc": ts, "bid": 2500.0, "ask": 2500.0 + s, "flags": 6})
    return pd.DataFrame(rows)


def test_the_spread_profile_gives_exact_medians_and_p90_by_utc_hour_and_session():
    rng = np.random.default_rng(7)
    a = {9: list(np.round(rng.uniform(0.10, 0.30, 400), 2)), 22: list(np.round(rng.uniform(0.3, 0.9, 50), 2))}
    b = {9: list(np.round(rng.uniform(0.10, 0.40, 300), 2)), 14: [0.12] * 30}
    days = [_day_ticks(date(2026, 11, 3), a), _day_ticks(date(2026, 11, 4), b)]
    crossed = pd.DataFrame([{"ts_utc": pd.Timestamp("2026-11-04 09:30", tz="UTC"), "bid": 2500.0, "ask": 2499.0,
                             "flags": 6}])
    prof = spread_profile([days[0], pd.concat([days[1], crossed])], first_day=date(2026, 11, 3),
                          last_day=date(2026, 11, 4))
    assert prof is not None and prof.days == 2 and prof.n_ticks == 400 + 50 + 300 + 30     # the crossed quote is out
    nine = np.array(a[9] + b[9])
    assert prof.by_hour["09"].median == pytest.approx(np.median(nine), abs=1e-9)
    assert prof.by_hour["09"].p90 == pytest.approx(np.percentile(nine, 90), abs=1e-9)
    assert prof.by_hour["09"].n == 700 and prof.by_hour["14"].median == pytest.approx(0.12)
    assert set(prof.by_hour) == {"09", "14", "22"}
    london = np.array(a[9] + b[9])                                       # 09 UTC is London in November
    assert prof.by_session["london"].median == pytest.approx(np.median(london), abs=1e-9)
    assert "asia" not in prof.by_session            # 22 UTC is the closed break: in the hour profile, in no session


def test_the_spread_profile_is_an_aggregate_only_no_tick_or_timestamp_leaves_it():
    prof = spread_profile([_day_ticks(date(2026, 11, 3), {h: [0.2] * 500 for h in range(24)})],
                          first_day=date(2026, 11, 3), last_day=date(2026, 11, 3))
    assert prof is not None
    raw = json.loads(prof.model_dump_json())
    assert set(raw) == {"source", "first_day", "last_day", "days", "days_skipped", "n_ticks", "by_session", "by_hour"}
    assert len(raw["by_hour"]) == 24 and len(raw["by_session"]) <= 3
    assert all(set(v) == {"median", "p90", "n"} for v in [*raw["by_hour"].values(), *raw["by_session"].values()])
    assert len(prof.model_dump_json()) < 5_000                            # ~5 KB at most (section 1.4 step 6)


def test_no_valid_tick_gives_no_spread_profile():
    assert spread_profile([pd.DataFrame(columns=["ts_utc", "bid", "ask", "flags"])], first_day=date(2026, 11, 3),
                          last_day=date(2026, 11, 3)) is None


def test_a_tick_with_a_non_finite_price_never_reaches_the_spread_profile():
    day = _day_ticks(date(2026, 11, 3), {9: [0.2] * 10})
    bad = day.iloc[:3].assign(ask=[np.nan, np.inf, 2500.3], bid=[2500.0, 2500.0, np.inf])
    prof = spread_profile([pd.concat([day, bad])], first_day=date(2026, 11, 3), last_day=date(2026, 11, 3))
    assert prof is not None and prof.n_ticks == 10 and prof.by_hour["09"].p90 == pytest.approx(0.2)


def _record(state: Path, days: list[date]) -> None:
    for d in days:
        tk.write_day(state, "icm-demo", d, _day_ticks(d, {9: [0.15] * 40, 15: [0.20] * 40}), symbol="XAUUSD",
                     server_tz=TZ, pulled_utc=NOW)


def _weekdays(n: int) -> list[date]:
    out: list[date] = []
    d = NOW.date() - timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def test_recorded_ticks_in_the_window_build_the_profile_and_a_tampered_day_is_skipped(tmp_path):
    days = _weekdays(12)
    _record(tmp_path, days)
    _record(tmp_path, [NOW.date() - timedelta(days=45)])                       # outside the 30-day window
    bad = tk.account_dir(tmp_path, "icm-demo") / f"{days[0].isoformat()}.parquet"
    bad.write_bytes(bad.read_bytes() + b"x")                                   # sha256 no longer matches the index
    prof, note = recorded_spread_profile(tmp_path, "icm-demo", NOW, window_days=30, min_days=10)
    assert prof is not None and prof.days == 11 and prof.days_skipped == 1
    assert prof.first_day == days[1] and prof.last_day == days[-1]
    assert prof.by_hour["09"].median == pytest.approx(0.15) and prof.by_hour["15"].median == pytest.approx(0.20)
    assert "11 recorded days" in note and "1 skipped" in note


def test_the_current_server_day_is_partial_and_never_counted(tmp_path):
    days = _weekdays(10)
    _record(tmp_path, [*days, NOW.date()])                                     # today's file: an incomplete day
    prof, _ = recorded_spread_profile(tmp_path, "icm-demo", NOW, window_days=30, min_days=10)
    assert prof is not None and prof.days == 10 and prof.last_day == days[-1]


def test_a_day_file_missing_a_column_is_skipped_not_fatal(tmp_path, monkeypatch):
    days = _weekdays(11)
    _record(tmp_path, days)
    real = tk.load_day

    def load(account: str, day: date, **kw: Any) -> pd.DataFrame:
        if day == days[3]:
            raise KeyError("ask")                                              # a file without the ask column
        return real(account, day, **kw)
    monkeypatch.setattr(tk, "load_day", load)
    prof, note = recorded_spread_profile(tmp_path, "icm-demo", NOW, window_days=30, min_days=10)
    assert prof is not None and prof.days == 10 and prof.days_skipped == 1 and "1 skipped" in note


def test_fewer_recorded_days_than_the_minimum_give_no_profile_and_say_why(tmp_path):
    _record(tmp_path, _weekdays(4))
    prof, note = recorded_spread_profile(tmp_path, "icm-demo", NOW, window_days=30, min_days=10)
    assert prof is None and "4 recorded days" in note and "fewer than 10" in note
    prof, note = recorded_spread_profile(tmp_path / "none", "icm-demo", NOW, window_days=30, min_days=10)
    assert prof is None and "0 recorded days" in note


# ------------------------------------------------------------------------------------------------ K3: publish labelled
def _ctx(tmp_path: Path) -> JobContext:
    return JobContext(settings=SETTINGS, store=Store(tmp_path / "data"), state_dir=tmp_path,
                      models=ModelRegistry(tmp_path / "models"), trials=TrialRegistry(tmp_path / "trials.jsonl"),
                      accounts=[ACC], population=Population(tmp_path / "population.json"))


def _terms(**kw: Any) -> BrokerTerms:
    base: dict[str, Any] = dict(account_id="icm-demo", measured_utc=NOW - pd.Timedelta(hours=3), swap_long_usd_per_lot=-48.0,
                                swap_short_usd_per_lot=9.0, swap_triple_weekday=2, swap_mode=1)
    return BrokerTerms(**{**base, **kw})


def test_costs_v1_publishes_the_recorded_spread_without_fills_with_slippage_and_commission_labelled_priors(tmp_path):
    ctx = _ctx(tmp_path)
    _terms().save(tmp_path / "broker_terms_icm-demo.json")
    _record(tmp_path, _weekdays(12))
    sent: list[bytes] = []

    def upload(data: bytes) -> str:
        sent.append(data)
        return "https://example.invalid/costs_measured.json"
    ctx.upload_costs = upload
    out = nightly_costs(ctx, NOW)                                      # no fills and no engine tick sample
    assert out["publish"]["published"] is True and out["icm-demo"]["spread_profile_days"] == 12
    raw = json.loads(sent[0])
    assert {k: v["source"] for k, v in raw["fields"].items()} == {
        "spread": "prior", "spread_profile": "measured", "slippage": "prior", "commission": "prior", "swap": "measured"}
    assert raw["fields"]["slippage"]["n"] == 0 and raw["fields"]["spread_profile"]["n"] == 12
    assert all(s["from_prior"] for s in raw["slippage"].values())
    assert "icm-demo" not in sent[0].decode()
    (tmp_path / "pub.json").write_bytes(sent[0])
    table = load_research_cost_table(tmp_path / "pub.json")
    assert table is not None and table.spread_profile is not None and table.spread_profile.days == 12
    _, _, source = research_costs(SETTINGS, table)
    assert "slippage prior (no measured cell)" in source and "commission from settings" in source
    assert "broker spread profile 12 days (reported, not charged)" in source


def test_nothing_measured_about_the_spread_is_still_refused(tmp_path):
    table = CostTable(account_id="icm-demo", built_utc=NOW, spread={}, slippage={}, commission_per_lot_side_usd=3.5,
                      slippage_prior_usd=0.15, swap_long_usd_per_lot=-48.0, swap_short_usd_per_lot=9.0)
    pub, why = publishable(table, "icm")
    assert pub is None and "no measured spread" in why


def _slip(**cells: bool) -> dict[str, SlippageStat]:
    return {k: SlippageStat(mean=0.05 if measured else 0.15, n=60 if measured else 3, from_prior=not measured)
            for k, measured in cells.items()}


@pytest.mark.parametrize("cells, label", [({"london:market": True, "asia:market": True}, "measured"),
                                          ({"london:market": True, "asia:market": False}, "mixed"),
                                          ({"london:market": False, "asia:market": False}, "prior")])
def test_slippage_mixing_measured_and_prior_cells_is_labelled_mixed(cells, label):
    table = CostTable(account_id="icm-demo", built_utc=NOW, spread={"london": SpreadStat(median=0.2, p90=0.3, n=9)},
                      slippage=_slip(**cells), commission_per_lot_side_usd=3.5, slippage_prior_usd=0.15, n_fills=63,
                      swap_long_usd_per_lot=-48.0, swap_short_usd_per_lot=9.0)
    pub, _ = publishable(table, "icm")
    assert pub is not None and pub.fields["slippage"].source == label


def _published(**kw: Any) -> dict[str, Any]:
    prof = SpreadProfile(first_day=date(2026, 11, 3), last_day=date(2026, 11, 18), days=12, n_ticks=900,
                         by_session={"london": SpreadStat(median=0.15, p90=0.2, n=900)},
                         by_hour={"09": SpreadStat(median=0.15, p90=0.2, n=900)})
    base: dict[str, Any] = dict(
        broker="icm", measured_at=NOW, spread={"london": SpreadStat(median=0.2, p90=0.3, n=9)},
        slippage=_slip(**{"london:market": True, "asia:market": False}), slippage_prior_usd=0.15,
        commission_per_lot_side_usd=3.5, swap_long_usd_per_lot=-48.0, swap_short_usd_per_lot=9.0, n_ticks=9, n_fills=63,
        spread_profile=prof,
        fields={"spread": FieldSource(source="measured", n=9), "slippage": FieldSource(source="mixed", n=63),
                "commission": FieldSource(source="prior", n=0.0), "swap": FieldSource(source="measured"),
                "spread_profile": FieldSource(source="measured", n=12)})
    return {**base, **kw}


def test_a_published_table_whose_labels_disagree_with_its_values_is_refused():
    PublishedCostTable(**_published())                                      # consistent: accepted
    f = _published()["fields"]
    for bad in ({**f, "slippage": FieldSource(source="measured", n=63)},     # a prior cell passed off as measured
                {**f, "slippage": FieldSource(source="prior", n=63)},
                {**f, "swap": FieldSource(source="prior")},
                {**f, "spread": FieldSource(source="prior", n=9)},
                {k: v for k, v in f.items() if k != "spread_profile"},      # a profile without its label
                {k: v for k, v in f.items() if k != "commission"}):          # a component without a label
        with pytest.raises(ValidationError):
            PublishedCostTable(**_published(fields=bad))
    with pytest.raises(ValidationError):                                   # a label for a profile that is not there
        PublishedCostTable(**_published(spread_profile=None))
