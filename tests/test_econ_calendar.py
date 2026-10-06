"""Economic calendar: Forex Factory feed -> tiered events -> archive job -> engine news blackout and the analyst tool."""
import json

import pandas as pd
import pytest

from goldbot.agents.tools import ReadOnlyTools
from goldbot.config import load_settings
from goldbot.data.econ_calendar import blackout_window, parse_ff_week
from goldbot.data.store import Store
from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops.jobs import JobContext, calendar_archive
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.population import Population
from goldbot.research.registry import TrialRegistry
from goldbot.risk import Intent, RiskGate
from goldbot.specialists import SPECIALISTS

NOW = pd.Timestamp("2026-10-12 06:10", tz="UTC")     # a Monday morning
TIER1 = ["CPI", "NFP", "FOMC", "PCE"]
FEED = [
    {"title": "CPI m/m", "country": "USD", "date": "2026-10-14T08:30:00-04:00", "impact": "High", "forecast": "0.3%", "previous": "0.4%"},
    {"title": "Core CPI m/m", "country": "USD", "date": "2026-10-14T08:30:00-04:00", "impact": "High", "forecast": "0.3%", "previous": "0.3%"},
    {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-10-16T08:30:00-04:00", "impact": "High", "forecast": "150K", "previous": "142K"},
    {"title": "FOMC Statement", "country": "USD", "date": "2026-10-15T14:00:00-04:00", "impact": "High", "forecast": "", "previous": ""},
    {"title": "Federal Funds Rate", "country": "USD", "date": "2026-10-15T14:00:00-04:00", "impact": "High", "forecast": "4.00%", "previous": "4.25%"},
    {"title": "Core PCE Price Index m/m", "country": "USD", "date": "2026-10-16T08:30:00-04:00", "impact": "High", "forecast": "0.2%", "previous": "0.2%"},
    {"title": "ISM Manufacturing PMI", "country": "USD", "date": "2026-10-12T10:00:00-04:00", "impact": "High", "forecast": "49.5", "previous": "49.1"},
    {"title": "CPI y/y", "country": "EUR", "date": "2026-10-13T05:00:00-04:00", "impact": "High", "forecast": "2.1%", "previous": "2.2%"},
    {"title": "Unemployment Claims", "country": "USD", "date": "2026-10-15T08:30:00-04:00", "impact": "Medium", "forecast": "225K", "previous": "231K"},
    {"title": "no date", "country": "USD", "impact": "High"},
    {"title": "naive time", "country": "USD", "date": "2026-10-13T08:30:00", "impact": "High"},
]


def test_feed_is_parsed_into_utc_events_with_the_design_tiers():
    df = parse_ff_week(json.dumps(FEED), NOW, TIER1).set_index("title")
    assert len(df) == 9                                       # the undated and the naive-time rows are skipped
    assert df.loc["CPI m/m", "ts_utc"] == pd.Timestamp("2026-10-14 12:30", tz="UTC")    # EDT -> UTC
    tiers = df["tier"].to_dict()
    assert {k for k, v in tiers.items() if v == 1} == {"CPI m/m", "Core CPI m/m", "Non-Farm Employment Change",
                                                         "FOMC Statement", "Federal Funds Rate", "Core PCE Price Index m/m"}
    assert tiers["ISM Manufacturing PMI"] == 2 and tiers["CPI y/y"] == 3 and tiers["Unemployment Claims"] == 3
    assert df["event_id"].is_unique
    # the same feed fetched again yields the same ids (the archive replaces, never duplicates)
    assert set(parse_ff_week(FEED, NOW + pd.Timedelta(days=1), TIER1)["event_id"]) == set(df["event_id"])


def test_blackout_window_is_15_minutes_before_to_30_after_a_tier1_event():
    ev = parse_ff_week(FEED, NOW, TIER1)
    cpi = pd.Timestamp("2026-10-14 12:30", tz="UTC")
    assert blackout_window(ev, cpi - pd.Timedelta(minutes=16), 15, 30) is None
    hit = blackout_window(ev, cpi - pd.Timedelta(minutes=15), 15, 30)
    assert hit is not None and hit["title"] in {"CPI m/m", "Core CPI m/m"}
    assert blackout_window(ev, cpi + pd.Timedelta(minutes=30), 15, 30) is not None
    assert blackout_window(ev, cpi + pd.Timedelta(minutes=31), 15, 30) is None
    ism = pd.Timestamp("2026-10-12 14:00", tz="UTC")           # tier 2: no blackout
    assert blackout_window(ev, ism, 15, 30) is None


def _ctx(tmp_path, fetch):
    return JobContext(settings=load_settings(), store=Store(tmp_path / "data"), state_dir=tmp_path,
                      models=ModelRegistry(tmp_path / "m"), trials=TrialRegistry(tmp_path / "t.jsonl"), accounts=[],
                      population=Population(tmp_path / "p.json"), fetch_calendar=fetch)


def test_archive_job_stores_the_week_and_fails_loudly_on_an_empty_feed(tmp_path):
    assert "skipped" in calendar_archive(_ctx(tmp_path, None), NOW)
    ctx = _ctx(tmp_path, lambda: json.dumps(FEED))
    out = calendar_archive(ctx, NOW)
    assert out["events"] == 9 and out["by_tier"] == {"1": 6, "2": 1, "3": 2}
    assert out["next_tier1"][0].startswith("Wed 12:30")
    calendar_archive(ctx, NOW + pd.Timedelta(days=1))          # daily re-fetch: replaced, not duplicated
    assert len(ctx.store.read("calendar_events")) == 9
    with pytest.raises(RuntimeError, match="no events"):
        calendar_archive(_ctx(tmp_path, lambda: "[]"), NOW)


def test_engine_blocks_entries_inside_the_blackout(tmp_path):
    store = Store(tmp_path / "data")
    store.append("calendar_events", parse_ff_week(FEED, NOW, TIER1), source="forexfactory")
    broker = PaperBroker(equity=10_000)
    spec = SPECIALISTS["session_open"]()
    eng = Engine(EngineConfig(account_id="x", broker_name="icm", state_dir=str(tmp_path), data_root=str(tmp_path / "data"),
                              news_blackout=True), broker, [spec], {})
    cpi = pd.Timestamp("2026-10-14 12:30", tz="UTC")
    for ts, inside in ((cpi - pd.Timedelta(minutes=20), False), (cpi - pd.Timedelta(minutes=5), True),
                       (cpi + pd.Timedelta(minutes=25), True), (cpi + pd.Timedelta(minutes=45), False)):
        tick = Tick(ts_utc=ts, bid=2400.0, ask=2400.2)
        broker.on_tick(tick)
        eng._calendar = (None, eng._calendar[1])              # force a re-read as if 5 minutes had passed
        eng._refresh_account(tick)
        assert eng.state.in_blackout is inside, ts
    eng._refresh_account(Tick(ts_utc=cpi, bid=2400.0, ask=2400.2))
    d = RiskGate().check(Intent(agent_id="x", side=1, p=0.6, target_atr=1.5, stop_atr=1.0, atr_usd=4.0, cost_atr=0.1,
                                multiplier=1.0, price=2400.0), eng.state)
    assert not d.allowed and "news_blackout" in d.reasons
    eng._write_state()
    assert json.loads((tmp_path / "engine_x.json").read_text())["blackout"]["title"] in {"CPI m/m", "Core CPI m/m"}


def test_analyst_reads_the_calendar_with_blackout_windows(tmp_path):
    store = Store(tmp_path / "data")
    store.append("calendar_events", parse_ff_week(FEED, NOW, TIER1), source="forexfactory")
    tools = ReadOnlyTools(tmp_path, store, now=lambda: NOW)
    out, err = tools.call("read_calendar", {"days_ahead": 3, "max_tier": 2}, ["read_calendar"])
    assert not err
    ev = json.loads(out)["events"]
    assert [e["title"] for e in ev][:2] == ["ISM Manufacturing PMI", "CPI m/m"]   # within 3 days, tiers 1-2, by time
    assert "CPI y/y" not in {e["title"] for e in ev}                                # tier 3 filtered
    cpi = next(e for e in ev if e["title"] == "CPI m/m")
    assert cpi["blackout_utc"] == ["2026-10-14T12:15:00+00:00", "2026-10-14T13:00:00+00:00"]
    empty = ReadOnlyTools(tmp_path, Store(tmp_path / "none"), now=lambda: NOW)
    assert "no calendar archived" in empty.call("read_calendar", {"days_ahead": 1, "max_tier": 1}, ["read_calendar"])[0]
