"""Measured broker costs (proposal P9 / HANDOFF Q1 2027): the MT5 terminal's swap rates and the commission charged on
recent deals reach the nightly cost table, research and retraining use them, and the health check warns while the
table has no measured swap. MetaTrader5 is Windows-only: the adapter is driven here by a fake `mt5` module."""
import json
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.store import Store
from goldbot.execution import mt5_adapter
from goldbot.execution.costs import (
    BrokerTerms,
    CostTable,
    SlippageStat,
    SpreadStat,
    load_research_cost_table,
    research_costs,
    settings_extra_cost_usd,
    settings_swap,
)
from goldbot.execution.mt5_adapter import (
    commission_round_trip_per_lot,
    measure_broker_terms,
    swap_usd_per_lot,
)
from goldbot.ops import health
from goldbot.ops.accounts import Account
from goldbot.ops.jobs import JobContext, live_extra_cost_usd, live_swap, nightly_costs
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.population import Population
from goldbot.research.registry import TrialRegistry

SETTINGS = load_settings()
NOW = pd.Timestamp("2026-10-08 23:10", tz="UTC")
ACC = Account(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=None, terminal_path="",
              server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100, enabled=True)

SymInfo = namedtuple("SymInfo", "name visible swap_mode swap_long swap_short swap_rollover3days point trade_tick_value "
                                "trade_tick_size trade_contract_size currency_base currency_profit currency_margin")
Deal = namedtuple("Deal", "ticket position_id symbol type entry volume price commission fee swap profit time_msc")


def info(**kw: Any) -> SymInfo:
    base = dict(name="XAUUSD", visible=True, swap_mode=1, swap_long=-55.0, swap_short=12.0, swap_rollover3days=3,
                point=0.01, trade_tick_value=1.0, trade_tick_size=0.01, trade_contract_size=100.0, currency_base="XAU",
                currency_profit="USD", currency_margin="USD")
    return SymInfo(**{**base, **kw})


# ------------------------------------------------------------------------------------------------ swap conversion
@pytest.mark.parametrize("mode, long_, short, kw, expect", [
    (1, -55.0, 12.0, {}, (-55.0, 12.0)),                                   # points: x point x tick value / tick size
    (1, -55.0, 12.0, {"trade_tick_value": 0.5}, (-27.5, 6.0)),             # tick value in deposit currency per lot
    (2, -0.02, 0.005, {}, (-50.0, 12.5)),                                  # base currency XAU, x price (USD per XAU)
    (3, -60.0, 10.0, {}, (-60.0, 10.0)),                                   # margin currency USD: as is
    (4, -60.0, 10.0, {}, (-60.0, 10.0)),                                   # deposit currency USD: as is
    (5, -3.6, 1.8, {}, (-25.0, 12.5)),                                     # annual % of price x 100 oz / 360
    (6, -3.6, 1.8, {}, (-25.0, 12.5)),                                     # open price approximated by the current one
    (0, -3.0, 2.0, {}, (0.0, 0.0)),                                        # swaps disabled: none charged
])
def test_swap_modes_convert_to_usd_per_lot_per_night(mode, long_, short, kw, expect):
    lng, sht, note = swap_usd_per_lot(info(swap_mode=mode, swap_long=long_, swap_short=short, **kw),
                                      account_currency="USD", price=2500.0)
    assert (lng, sht) == pytest.approx(expect) and note


@pytest.mark.parametrize("mode, kw, currency", [
    (7, {}, "USD"), (8, {}, "USD"),                                        # reopen modes: no per-night rate
    (1, {}, "EUR"), (4, {}, "EUR"),                                        # deposit not USD: no conversion here
    (3, {"currency_margin": "EUR", "currency_base": "XAU"}, "USD"),        # margin currency neither USD nor base
    (42, {}, "USD"),                                                       # unknown mode
])
def test_unsupported_swap_modes_give_no_rate_and_say_why(mode, kw, currency):
    lng, sht, note = swap_usd_per_lot(info(swap_mode=mode, **kw), account_currency=currency, price=2500.0)
    assert lng is None and sht is None and "prior" in note


def test_commission_is_per_lot_round_trip_over_complete_positions():
    deals = pd.DataFrame([
        Deal(1, 10, "XAUUSD", 0, 0, 0.5, 2500.0, -1.75, 0.0, 0.0, 0.0, 0)._asdict(),     # in, 0.5 lot
        Deal(2, 10, "XAUUSD", 1, 1, 0.5, 2501.0, -1.75, 0.0, -2.0, 50.0, 0)._asdict(),   # out (swap is not commission)
        Deal(3, 11, "XAUUSD", 1, 0, 1.0, 2500.0, -3.5, -0.1, 0.0, 0.0, 0)._asdict(),     # in, 1 lot, with a fee
        Deal(4, 11, "XAUUSD", 0, 1, 1.0, 2499.0, -3.5, -0.1, 0.0, 100.0, 0)._asdict(),
        Deal(5, 12, "XAUUSD", 0, 0, 2.0, 2500.0, -7.0, 0.0, 0.0, 0.0, 0)._asdict(),      # still open: not counted
        Deal(6, 13, "EURUSD", 0, 0, 1.0, 1.1, -9.0, 0.0, 0.0, 0.0, 0)._asdict(),         # other symbol
        Deal(7, 13, "EURUSD", 1, 1, 1.0, 1.1, -9.0, 0.0, 0.0, 0.0, 0)._asdict(),
        Deal(8, 0, "", 2, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 1000.0, 0)._asdict(),               # balance deposit
    ])
    rt, lots, note = commission_round_trip_per_lot(deals, "XAUUSD")
    assert lots == pytest.approx(1.5) and rt == pytest.approx((3.5 + 7.2) / 1.5) and "2 closed positions" in note
    assert commission_round_trip_per_lot(deals.iloc[4:], "XAUUSD")[0] is None
    assert commission_round_trip_per_lot(pd.DataFrame(), "XAUUSD")[0] is None


def test_measured_terms_carry_swap_triple_day_and_commission_or_say_why_not(caplog):
    t = measure_broker_terms("icm-demo", info(swap_mode=1, swap_long=-55.0, swap_short=12.0, swap_rollover3days=3),
                             account_currency="USD", price=2500.0, deals=pd.DataFrame(), now=NOW)
    assert (t.swap_long_usd_per_lot, t.swap_short_usd_per_lot, t.swap_triple_weekday) == (-55.0, 12.0, 2)   # Wed = 2
    assert t.commission_per_lot_round_trip_usd is None and any("commission" in n for n in t.notes)
    t = measure_broker_terms("icm-demo", info(swap_mode=7, swap_rollover3days=6), account_currency="USD",
                             price=2500.0, deals=pd.DataFrame(), now=NOW)
    assert t.swap_long_usd_per_lot is None and t.swap_triple_weekday is None
    assert "settings prior" in caplog.text                                  # the fallback is logged with its reason


# ------------------------------------------------------------------------------------------------ the adapter
class FakeMT5(SimpleNamespace):
    """Just the calls MT5Broker.__init__ and broker_terms make."""


def fake_mt5(sym: SymInfo, deals: list[Deal]) -> FakeMT5:
    Tick = namedtuple("Tick", "time_msc bid ask")
    Acct = namedtuple("Acct", "currency")
    return FakeMT5(initialize=lambda **kw: True, symbol_select=lambda s, on: True, symbol_info=lambda s: sym,
                   last_error=lambda: (0, ""), shutdown=lambda: None, account_info=lambda: Acct("USD"),
                   symbol_info_tick=lambda s: Tick(0, 2499.9, 2500.1), history_deals_get=lambda a, b: tuple(deals))


def test_adapter_reads_swap_and_commission_from_the_terminal(monkeypatch):
    deals = [Deal(1, 10, "XAUUSD", 0, 0, 1.0, 2500.0, -3.5, 0.0, 0.0, 0.0, 1_700_000_000_000),
             Deal(2, 10, "XAUUSD", 1, 1, 1.0, 2501.0, -3.5, 0.0, -5.5, 100.0, 1_700_000_100_000)]
    monkeypatch.setattr(mt5_adapter, "mt5", fake_mt5(info(swap_mode=5, swap_long=-3.6, swap_short=1.8), deals))
    b = mt5_adapter.MT5Broker(terminal_path="", login=1, password="x", server="s", server_tz="Europe/Athens",
                              symbol="XAUUSD", account_label="icm-demo")
    t = b.broker_terms("icm-demo", NOW - pd.Timedelta(days=30), NOW)
    assert t.swap_long_usd_per_lot == pytest.approx(-3.6 / 100 / 360 * 2500.0 * 100)
    assert t.commission_per_lot_round_trip_usd == pytest.approx(7.0) and t.swap_mode == 5


# ------------------------------------------------------------------------------------------------ cost table
def _ctx(tmp_path: Path) -> JobContext:
    return JobContext(settings=SETTINGS, store=Store(tmp_path / "data"), state_dir=tmp_path,
                      models=ModelRegistry(tmp_path / "models"), trials=TrialRegistry(tmp_path / "trials.jsonl"),
                      accounts=[ACC], population=Population(tmp_path / "population.json"))


def _terms(**kw: Any) -> BrokerTerms:
    base: dict[str, Any] = dict(account_id="icm-demo", measured_utc=NOW - pd.Timedelta(hours=3), swap_long_usd_per_lot=-48.0,
                                swap_short_usd_per_lot=9.0, swap_triple_weekday=4, swap_mode=1,
                                commission_per_lot_round_trip_usd=6.0, commission_lots=3.0)
    return BrokerTerms(**{**base, **kw})


def test_nightly_costs_fill_swap_and_commission_from_the_terminal(tmp_path):
    ctx = _ctx(tmp_path)
    _terms().save(tmp_path / "broker_terms_icm-demo.json")
    nightly_costs(ctx, NOW)
    table = CostTable.load(tmp_path / "costs_icm-demo.json")
    assert table is not None and table.commission_measured and table.commission_per_lot_side_usd == 3.0
    assert (table.swap_long_usd_per_lot, table.swap_short_usd_per_lot, table.swap_triple_weekday) == (-48.0, 9.0, 4)
    # research and retraining charge them through the existing paths
    sw = live_swap(ctx)
    assert (sw.long_usd_per_lot, sw.short_usd_per_lot, sw.triple_weekday, sw.server_tz) == (-48.0, 9.0, 4, "Europe/Athens")
    assert live_extra_cost_usd(ctx) == pytest.approx(2 * SETTINGS.costs.slippage_prior_usd + 6.0 / 100)


def test_stale_or_missing_terms_keep_the_settings_values(tmp_path):
    ctx = _ctx(tmp_path)
    nightly_costs(ctx, NOW)                                     # no terms file yet
    table = CostTable.load(tmp_path / "costs_icm-demo.json")
    assert table is not None and not table.commission_measured and table.swap_long_usd_per_lot is None
    assert table.commission_per_lot_side_usd == SETTINGS.costs.commission_per_lot_side_usd["icm"]
    assert live_swap(ctx) == settings_swap(SETTINGS)
    _terms(measured_utc=NOW - pd.Timedelta(days=10)).save(tmp_path / "broker_terms_icm-demo.json")
    nightly_costs(ctx, NOW)
    table = CostTable.load(tmp_path / "costs_icm-demo.json")
    assert table is not None and table.swap_long_usd_per_lot is None and any("stale" in n for n in table.notes)
    # swap known but commission not measured yet: the swap is used, commission stays the setting
    _terms(commission_per_lot_round_trip_usd=None, commission_lots=0.0).save(tmp_path / "broker_terms_icm-demo.json")
    nightly_costs(ctx, NOW)
    table = CostTable.load(tmp_path / "costs_icm-demo.json")
    assert table is not None and table.swap_long_usd_per_lot == -48.0 and not table.commission_measured


def test_health_warns_until_the_cost_table_has_measured_swap(tmp_path):
    ctx = health.HealthContext(state_dir=tmp_path, now=NOW, settings=SETTINGS,
                               accounts=[health.AccountRef(account_id="icm-demo")], get_secret=lambda k: "x")
    f = tmp_path / "costs_icm-demo.json"
    f.write_text(json.dumps({"account_id": "icm-demo", "built_utc": (NOW - pd.Timedelta(days=1)).isoformat()}))
    c = health.check_costs(ctx, "icm-demo")
    assert c.status == "warn" and "no measured swap" in c.reason
    f.write_text(json.dumps({"account_id": "icm-demo", "built_utc": (NOW - pd.Timedelta(days=1)).isoformat(),
                             "swap_long_usd_per_lot": -48.0, "swap_short_usd_per_lot": 9.0}))
    assert health.check_costs(ctx, "icm-demo").status == "ok"


# ------------------------------------------------------------------------------------------------ publish and research
def _measured_table(**kw: Any) -> CostTable:
    base: dict[str, Any] = dict(
        account_id="icm-demo", built_utc=NOW, spread={"london": SpreadStat(median=0.2, p90=0.3, n=5000)},
        slippage={"london:market": SlippageStat(mean=0.05, n=80, from_prior=False),
                  "asia:market": SlippageStat(mean=0.15, n=4, from_prior=True)},
        commission_per_lot_side_usd=3.0, slippage_prior_usd=0.15, n_ticks=5000, n_fills=84,
        notes=["broker terms measured for account icm-demo login 51234567"], swap_long_usd_per_lot=-48.0,
        swap_short_usd_per_lot=9.0, swap_triple_weekday=4, commission_measured=True, commission_lots=3.0)
    return CostTable(**{**base, **kw})


def _keys(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


def _sink(sent: list[bytes]) -> Callable[[bytes], str]:
    def upload(data: bytes) -> str:
        sent.append(data)
        return "https://example.invalid/costs_measured.json"
    return upload


def test_the_published_cost_table_holds_costs_only_no_account_identifier_or_equity(tmp_path):
    from goldbot.ops.jobs import publish_costs
    _measured_table().save(tmp_path / "costs_icm-demo.json")
    sent: list[bytes] = []
    res = publish_costs(SETTINGS, [ACC], tmp_path, _sink(sent), NOW)
    assert res["published"] is True and len(sent) == 1
    text = sent[0].decode()
    raw = json.loads(text)
    banned = ("account", "login", "server", "balance", "equity", "margin", "profit", "note")
    assert not [k for k in _keys(raw) if any(b in k.lower() for b in banned)]
    assert "icm-demo" not in text and "51234567" not in text and "ICMarketsSC" not in text
    assert raw["broker"] == "icm" and raw["measured_at"].startswith("2026-10-08")
    assert {k: v["source"] for k, v in raw["fields"].items()} == {
        "spread": "measured", "slippage": "measured", "commission": "measured", "swap": "measured"}
    assert raw["fields"]["slippage"]["n"] == 84 and raw["fields"]["commission"]["n"] == 3.0
    rec = json.loads((tmp_path / "costs_published.json").read_text())
    assert rec["published_utc"] == NOW.isoformat() and rec["last_result"] == "ok"


@pytest.mark.parametrize("kw, why", [({"swap_long_usd_per_lot": None}, "swap not measured"),
                                     ({"n_fills": 49}, "49 fills, fewer than 50")])
def test_publishing_is_refused_while_a_prior_would_pass_as_a_measurement(tmp_path, kw, why):
    from goldbot.ops.jobs import publish_costs
    _measured_table(**kw).save(tmp_path / "costs_icm-demo.json")
    sent: list[bytes] = []
    res = publish_costs(SETTINGS, [ACC], tmp_path, _sink(sent), NOW)
    assert res == {"published": False, "reason": res["reason"]} and why in res["reason"] and not sent
    rec = json.loads((tmp_path / "costs_published.json").read_text())
    assert "published_utc" not in rec and why in rec["last_result"]


def test_an_upload_failure_is_recorded_and_the_cost_job_stays_green(tmp_path):
    ctx = _ctx(tmp_path)
    _measured_table().save(tmp_path / "costs_icm-demo.json")

    def down(_: bytes) -> str:
        raise OSError("network unreachable")
    ctx.upload_costs = down
    _terms().save(tmp_path / "broker_terms_icm-demo.json")
    out = nightly_costs(ctx, NOW)        # rebuilds the table from the empty store (0 fills): refused, no upload
    assert out["publish"]["published"] is False and "fewer than 50" in out["publish"]["reason"]
    from goldbot.ops.jobs import publish_costs
    _measured_table().save(tmp_path / "costs_icm-demo.json")
    res = publish_costs(SETTINGS, [ACC], tmp_path, down, NOW)
    assert res["published"] is False and "upload failed: OSError: network unreachable" in res["reason"]


def test_health_warns_when_the_published_table_is_missing_or_older_than_8_days(tmp_path):
    on = SETTINGS.model_copy(update={"costs": SETTINGS.costs.model_copy(update={"publish_release": True})})
    ctx = health.HealthContext(state_dir=tmp_path, now=NOW, settings=on, accounts=[], get_secret=lambda k: "x")
    c = health.check_costs_published(ctx)
    assert c.status == "warn" and "never published" in c.reason and "priors" in c.reason
    rec = tmp_path / "costs_published.json"
    rec.write_text(json.dumps({"published_utc": (NOW - pd.Timedelta(days=2)).isoformat(), "last_result": "ok"}))
    assert health.check_costs_published(ctx).status == "ok"
    rec.write_text(json.dumps({"published_utc": (NOW - pd.Timedelta(days=9)).isoformat(),
                               "last_result": "swap not measured by the terminal yet"}))
    c = health.check_costs_published(ctx)
    assert c.status == "warn" and "> 8 d" in c.reason and "swap not measured" in c.reason
    assert "costs:published" in {x.name for x in health.run_checks(ctx).checks}
    off = ctx.model_copy(update={"settings": SETTINGS})                         # publishing off: no check at all
    assert "costs:published" not in {x.name for x in health.run_checks(off).checks}


def test_publish_costs_out_writes_the_published_json_and_research_uses_it(tmp_path):
    from goldbot.ops.run import publish_costs_cli
    vantage = ACC.model_copy(update={"account_id": "vantage-demo", "broker": "vantage"})
    out = tmp_path / "costs_measured.json"
    assert publish_costs_cli(["--out", str(out)], accounts=[vantage, ACC], state_dir=tmp_path) == 1   # no table yet
    _measured_table().save(tmp_path / "costs_icm-demo.json")
    assert publish_costs_cli(["--out", str(out)], accounts=[vantage, ACC], state_dir=tmp_path) == 0
    assert "icm-demo" not in out.read_text()
    table = load_research_cost_table(out)
    assert table is not None and table.swap_long_usd_per_lot == -48.0 and table.commission_measured
    extra, swap, source = research_costs(SETTINGS, table)
    assert extra == pytest.approx(2 * (0.05 + 0.15) / 2 + 6.0 / 100) and swap.long_usd_per_lot == -48.0
    assert swap.server_tz == settings_swap(SETTINGS).server_tz
    assert "cost table icm (published) built 2026-10-08" in source and "commission measured, swap measured" in source
    assert load_research_cost_table(tmp_path / "absent.json") is None
    extra, swap, source = research_costs(SETTINGS, None)                     # no asset: the settings priors, said so
    assert extra == settings_extra_cost_usd(SETTINGS) and swap == settings_swap(SETTINGS)
    assert source.startswith("PRIORS ONLY") and "costs-v1" in source


def test_publish_costs_uploads_with_the_given_uploader(tmp_path):
    from goldbot.ops.run import publish_costs_cli
    _measured_table().save(tmp_path / "costs_icm-demo.json")
    sent: list[bytes] = []
    assert publish_costs_cli([], accounts=[ACC], state_dir=tmp_path, upload=_sink(sent)) == 0
    assert json.loads(sent[0])["broker"] == "icm"
    _measured_table(n_fills=3).save(tmp_path / "costs_icm-demo.json")
    assert publish_costs_cli([], accounts=[ACC], state_dir=tmp_path, upload=_sink(sent)) == 1
    assert len(sent) == 1


def test_the_engine_writes_the_terminal_terms_every_few_hours_and_survives_a_failure(tmp_path):
    from goldbot.engine import ConstantModel, Engine, EngineConfig
    from goldbot.execution.broker import Tick
    from goldbot.execution.paper import PaperBroker

    calls: list[pd.Timestamp] = []

    class TermsBroker(PaperBroker):
        fail = False

        def broker_terms(self, account_id: str, since_utc: pd.Timestamp, now: pd.Timestamp) -> BrokerTerms:
            calls.append(now)
            if self.fail:
                raise RuntimeError("terminal gone")
            assert now - since_utc == pd.Timedelta(days=90)
            return _terms(measured_utc=now)

    broker = TermsBroker()
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)), broker, [],
                 {"session_open": ConstantModel(p=0.6)})
    t0 = pd.Timestamp("2025-03-04 09:00:00", tz="UTC")
    for k in range(3):
        eng.on_tick(Tick(ts_utc=t0 + pd.Timedelta(seconds=k), bid=2500.0, ask=2500.2))
    assert len(calls) == 1
    saved = BrokerTerms.load(tmp_path / "broker_terms_icm-demo.json")
    assert saved is not None and saved.measured_utc == t0
    broker.fail = True                                         # a failing terminal call never stops the engine
    eng.on_tick(Tick(ts_utc=t0 + pd.Timedelta(hours=7), bid=2500.0, ask=2500.2))
    assert len(calls) == 2 and BrokerTerms.load(tmp_path / "broker_terms_icm-demo.json") == saved
