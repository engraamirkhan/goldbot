"""Entry windows, off by default (BACKLOG 17, playbook G-2: holidays and reopen; BACKLOG 18, G-3: tier-2 events):
RiskGate refuses entries `tier2_blackout`, `reopen_window` and `holiday_thin`, and `calendar_unavailable` when an
enabled window has no calendar to judge by (fail closed). Exits are never gated. Every edge is checked in both DST
regimes on fake clocks (tick time)."""
from datetime import date, time
from pathlib import Path

import pandas as pd
import pytest
from pandas.tseries.holiday import GoodFriday, USFederalHolidayCalendar

from goldbot.config import ROOT, HolidaySettings, ReopenSettings, Tier2Blackout, load_settings
from goldbot.data.calendar import SessionTable, TradingSession
from goldbot.data.econ_calendar import blackout_window, parse_ff_week, tier_of
from goldbot.data.store import Store
from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.windows import HolidayList, last_reopen, load_holidays, reopen_window, server_date
from goldbot.specialists.session_open import SessionOpenSpecialist
from goldbot.telegram.approvals import Proposal

TIER1 = ["CPI", "NFP", "FOMC", "PCE"]
S = pd.Timedelta(seconds=1)
M = pd.Timedelta(minutes=1)
STATIC = SessionTable()                       # Europe/Athens server clock, daily break 23:59-01:02 server time
ON = RiskLimits(tier2=Tier2Blackout(enabled=True), reopen=ReopenSettings(enabled=True),
                holidays=HolidaySettings(enabled=True))
# daily reopen (01:02 server, Tuesday) and weekly open (01:02 server, Monday) in UTC: winter EET (+2), summer EEST (+3)
DAILY = {"winter": pd.Timestamp("2026-01-12 23:02", tz="UTC"), "summer": pd.Timestamp("2026-07-13 22:02", tz="UTC")}
WEEKLY = {"winter": pd.Timestamp("2026-01-11 23:02", tz="UTC"), "summer": pd.Timestamp("2026-07-12 22:02", tz="UTC")}


def _intent() -> Intent:
    return Intent(agent_id="x", side=1, p=0.6, target_atr=1.5, stop_atr=1.0, atr_usd=4.0, cost_atr=0.1,
                  multiplier=1.0, price=2400.0)


def _state(**kw) -> AccountState:
    return AccountState(equity=10_000, balance_closed_hwm=10_000, day_start_equity=10_000, week_start_equity=10_000,
                        open_positions=0, margin_used=0, last_tick_age_s=1, spread_points=20, **kw)


# ---------------------------------------------------------------------------------------------- settings and gate
def test_settings_ship_every_window_off_with_the_proposed_minutes():
    risk = load_settings().risk
    assert risk.blackout.tier2 == Tier2Blackout(enabled=False, before_min=5, after_min=15)
    assert risk.reopen == ReopenSettings(enabled=False, after_daily_reopen_min=10, after_monday_open_min=30)
    assert not risk.holidays.enabled and risk.holidays.file == "config/holidays_cme_metals.yaml"
    lim = RiskLimits.from_settings(risk, tiny_live=False)
    assert (lim.tier2, lim.reopen, lim.holidays) == (risk.blackout.tier2, risk.reopen, risk.holidays)


FLAGS = {"tier2_blackout": "tier2_blackout", "reopen_window": "reopen_window", "holiday_thin": "holiday_thin",
         "calendar_unavailable": "calendar_unavailable"}


@pytest.mark.parametrize("flag", list(FLAGS))
def test_disabled_windows_leave_every_decision_identical(flag):
    """Off (the shipped default), a raised flag changes nothing: same decision, lots and reasons as without it."""
    gate = RiskGate()
    plain = gate.check(_intent(), _state())
    assert plain.allowed
    assert gate.check(_intent(), _state(**{flag: True})) == plain
    refused = gate.check(_intent(), _state(in_blackout=True, **{flag: True}))
    assert refused.reasons == ["news_blackout"]


@pytest.mark.parametrize("flag,reason", list(FLAGS.items()))
def test_enabled_windows_refuse_entries_with_their_own_reason(flag, reason):
    d = RiskGate(ON).check(_intent(), _state(**{flag: True}))
    assert not d.allowed and d.reasons == [reason]
    assert RiskGate(ON).check(_intent(), _state()).allowed


def test_calendar_unavailable_refuses_only_when_a_calendar_window_is_on():
    """The reopen window needs no calendar, so with only it enabled a missing calendar is not a reason."""
    only_reopen = RiskLimits(reopen=ReopenSettings(enabled=True))
    assert RiskGate(only_reopen).check(_intent(), _state(calendar_unavailable=True)).allowed
    for lim in (RiskLimits(tier2=Tier2Blackout(enabled=True)), RiskLimits(holidays=HolidaySettings(enabled=True))):
        assert RiskGate(lim).check(_intent(), _state(calendar_unavailable=True)).reasons == ["calendar_unavailable"]


# ---------------------------------------------------------------------------------------------- tier 2 (BACKLOG 18)
def _feed(offset: str, day: str) -> list[dict]:
    return [
        {"title": "PPI m/m", "country": "USD", "date": f"{day}T08:30:00{offset}", "impact": "High"},
        {"title": "Unemployment Claims", "country": "USD", "date": f"{day}T10:00:00{offset}", "impact": "Medium"},
        {"title": "CPI m/m", "country": "USD", "date": f"{day}T14:00:00{offset}", "impact": "High"},
        {"title": "German PPI m/m", "country": "EUR", "date": f"{day}T03:00:00{offset}", "impact": "High"},
    ]


def test_the_g3_tier2_list_is_tier2_whatever_impact_the_feed_gives():
    """G-3 names PPI, retail sales, ISM, GDP, JOLTS, claims, FOMC minutes and the Fed chair; Forex Factory marks some
    of them Medium, which would leave them out of a tier defined by impact alone."""
    for title in ("PPI m/m", "Core Retail Sales m/m", "ISM Manufacturing PMI", "ISM Services PMI",
                  "Advance GDP q/q", "JOLTS Job Openings", "Unemployment Claims", "FOMC Meeting Minutes",
                  "Fed Chair Powell Speaks"):
        assert tier_of("USD", title, "Medium", TIER1) == 2, title
    assert tier_of("USD", "CPI m/m", "Medium", TIER1) == 1
    assert tier_of("EUR", "German PPI m/m", "High", TIER1) == 3
    assert tier_of("USD", "FOMC Member Waller Speaks", "Medium", TIER1) == 3


@pytest.mark.parametrize("regime,offset,day,ppi_utc", [
    ("winter", "-05:00", "2026-01-15", "2026-01-15 13:30"),
    ("summer", "-04:00", "2026-07-15", "2026-07-15 12:30"),
])
def test_tier2_blackout_covers_5_minutes_before_to_15_after_in_both_dst_regimes(regime, offset, day, ppi_utc):
    ev = parse_ff_week(_feed(offset, day), pd.Timestamp(day, tz="UTC") - pd.Timedelta(days=2), TIER1)
    ppi = pd.Timestamp(ppi_utc, tz="UTC")
    assert blackout_window(ev, ppi - 5 * M - S, 5, 15, tier=2) is None
    assert (blackout_window(ev, ppi - 5 * M, 5, 15, tier=2) or {}).get("title") == "PPI m/m"
    assert blackout_window(ev, ppi + 15 * M, 5, 15, tier=2) is not None
    assert blackout_window(ev, ppi + 15 * M + S, 5, 15, tier=2) is None
    claims = ppi + pd.Timedelta(minutes=90)
    assert (blackout_window(ev, claims, 5, 15, tier=2) or {}).get("title") == "Unemployment Claims"
    cpi = ppi + pd.Timedelta(hours=5, minutes=30)
    assert blackout_window(ev, cpi, 5, 15, tier=2) is None          # tier 1 has its own window (news_blackout)
    assert blackout_window(ev, cpi, 15, 30) is not None


def _engine(tmp_path: Path, limits: RiskLimits, calendar: list[dict] | None = None, data: bool = True,
            **cfg) -> tuple[Engine, PaperBroker]:
    root = tmp_path / "data"
    if calendar is not None:
        Store(root).append("calendar_events", parse_ff_week(calendar, pd.Timestamp("2026-01-01", tz="UTC"), TIER1),
                           source="forexfactory")
    pb = PaperBroker(equity=10_000, commission_per_lot_side=0.0, adverse_slip_points=0.0)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=111,
                              data_root=str(root) if data else None, **cfg),
                 pb, [SessionOpenSpecialist()], {}, limits=limits)
    return eng, pb


def _at(eng: Engine, pb: PaperBroker, ts: pd.Timestamp, px: float = 2400.0) -> AccountState:
    tick = Tick(ts_utc=ts, bid=px, ask=px + 0.2)
    pb.on_tick(tick)
    eng._calendar = (None, eng._calendar[1])              # force a re-read as if 5 minutes had passed
    eng._refresh_account(tick)
    return eng.state


@pytest.mark.parametrize("offset,day,ppi_utc", [("-05:00", "2026-01-15", "2026-01-15 13:30"),
                                                ("-04:00", "2026-07-15", "2026-07-15 12:30")])
def test_engine_raises_the_tier2_flag_at_both_edges_from_the_live_calendar(tmp_path, offset, day, ppi_utc):
    eng, pb = _engine(tmp_path, RiskLimits(tier2=Tier2Blackout(enabled=True)), _feed(offset, day))
    ppi = pd.Timestamp(ppi_utc, tz="UTC")
    for ts, inside in ((ppi - 5 * M - S, False), (ppi - 5 * M, True), (ppi + 15 * M, True), (ppi + 15 * M + S, False)):
        st = _at(eng, pb, ts)
        assert st.tier2_blackout is inside and not st.calendar_unavailable, ts
        assert not st.in_blackout                         # the tier-1 blackout is not switched on by tier 2
    d = eng.gate.check(_intent(), _at(eng, pb, ppi))
    assert not d.allowed and "tier2_blackout" in d.reasons


def test_tier2_without_a_store_or_with_an_empty_archive_fails_closed(tmp_path):
    ppi = pd.Timestamp("2026-01-15 13:30", tz="UTC")
    lim = RiskLimits(tier2=Tier2Blackout(enabled=True))
    for eng, pb in (_engine(tmp_path / "a", lim, data=False), _engine(tmp_path / "b", lim)):
        st = _at(eng, pb, ppi + pd.Timedelta(hours=3))
        assert st.calendar_unavailable and not st.tier2_blackout
        d = eng.gate.check(_intent(), st)
        assert not d.allowed and "calendar_unavailable" in d.reasons


def test_tier2_with_an_unreadable_archive_fails_closed(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, RiskLimits(tier2=Tier2Blackout(enabled=True)), _feed("-05:00", "2026-01-15"))

    def broken(*_a, **_k):
        raise OSError("disk gone")
    assert eng.store is not None
    monkeypatch.setattr(eng.store, "read", broken)
    assert _at(eng, pb, pd.Timestamp("2026-01-15 18:00", tz="UTC")).calendar_unavailable


# ---------------------------------------------------------------------------------------------- reopen (BACKLOG 17)
@pytest.mark.parametrize("regime", ["winter", "summer"])
def test_daily_reopen_window_refuses_through_10_minutes_inclusive_in_both_dst_regimes(regime):
    cfg = ReopenSettings(enabled=True)
    r = DAILY[regime]
    assert not STATIC.is_open(pd.DatetimeIndex([r - S]))[0]       # the break ends exactly at r
    assert last_reopen(STATIC, r + 3 * M, 30 * M) == (r, False)
    assert reopen_window(STATIC, r, cfg)
    assert reopen_window(STATIC, r + 10 * M, cfg)
    assert not reopen_window(STATIC, r + 10 * M + S, cfg)
    assert not reopen_window(STATIC, r - S, cfg)                  # closed: market_closed, not this window
    assert not reopen_window(STATIC, r + pd.Timedelta(hours=6), cfg)


@pytest.mark.parametrize("regime", ["winter", "summer"])
def test_weekly_open_window_refuses_through_30_minutes_inclusive_in_both_dst_regimes(regime):
    cfg = ReopenSettings(enabled=True)
    r = WEEKLY[regime]
    assert last_reopen(STATIC, r + 3 * M, 30 * M) == (r, True)
    assert reopen_window(STATIC, r, cfg)
    assert reopen_window(STATIC, r + 30 * M, cfg)
    assert not reopen_window(STATIC, r + 30 * M + S, cfg)
    assert reopen_window(STATIC, r + 20 * M, cfg) and not reopen_window(STATIC, DAILY[regime] + 20 * M, cfg)


def test_reopen_window_follows_the_broker_session_table_where_it_reports_one():
    """A broker that reopens at 01:10 server time (22:10 UTC in summer) moves the window with it."""
    reopen_utc = pd.Timestamp("2026-07-13 22:10", tz="UTC")
    sessions = [TradingSession(weekday=d.weekday(), start_utc=time(22, 10) if d.weekday() else time(22, 2),
                               end_utc=time(20, 59), server_date=d)
                for d in (date(2026, 7, 13), date(2026, 7, 14), date(2026, 7, 15))]
    table = STATIC.model_copy(update={
        "broker_windows": tuple(sorted(s.interval(STATIC.server_tz) for s in sessions)),
        "covers_from": pd.Timestamp("2026-07-12 21:00", tz="UTC"),
        "covers_until": pd.Timestamp("2026-07-19 21:00", tz="UTC"), "source": "broker"})
    cfg = ReopenSettings(enabled=True)
    assert last_reopen(table, reopen_utc + M, 30 * M) == (reopen_utc, False)
    assert reopen_window(table, reopen_utc + 10 * M, cfg) and not reopen_window(table, reopen_utc + 10 * M + S, cfg)
    assert not reopen_window(STATIC, reopen_utc + 10 * M, cfg)    # the static 01:02 table would have allowed it


def test_engine_raises_the_reopen_flag_only_when_enabled(tmp_path):
    r = DAILY["winter"]
    eng, pb = _engine(tmp_path / "on", RiskLimits(reopen=ReopenSettings(enabled=True)))
    assert _at(eng, pb, r + 5 * M).reopen_window and not eng.state.calendar_unavailable
    assert not _at(eng, pb, r + 11 * M).reopen_window
    off, pb2 = _engine(tmp_path / "off", RiskLimits())
    assert not _at(off, pb2, r + 5 * M).reopen_window


def test_a_session_table_that_raises_fails_the_reopen_window_closed(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, RiskLimits(reopen=ReopenSettings(enabled=True)))
    t = DAILY["winter"] + pd.Timedelta(hours=6)
    assert not _at(eng, pb, t).reopen_window

    def broken(self, utc_ts):
        raise ValueError("bad table")
    monkeypatch.setattr(SessionTable, "is_open", broken)
    assert _at(eng, pb, t + M).reopen_window


# ---------------------------------------------------------------------------------------------- holidays (BACKLOG 17)
def test_the_shipped_holiday_list_matches_the_rules_for_every_year_it_covers():
    hl = load_holidays(load_settings().risk.holidays.file)
    assert hl.version == "cme-metals-v1" and hl.valid_from == date(2026, 1, 1) and hl.valid_through >= date(2028, 1, 1)
    start, end = pd.Timestamp(hl.valid_from), pd.Timestamp(hl.valid_through)
    want = {d.date() for d in USFederalHolidayCalendar().holidays(start, end)}
    want |= {d.date() for d in GoodFriday.dates(start, end)}
    want |= {date(y, m, d) for y in range(start.year, end.year + 1) for m, d in ((12, 24), (12, 26), (12, 31), (1, 1),
                                                                                 (1, 2))}
    assert set(hl.days) == {d for d in want if d.weekday() < 5 and hl.valid_from <= d <= hl.valid_through}


@pytest.mark.parametrize("regime,day,midnight_utc", [
    ("winter", date(2026, 11, 26), "2026-11-25 22:00"),    # Thanksgiving; server midnight = 17:00 New York (EST)
    ("summer", date(2026, 6, 19), "2026-06-18 21:00"),     # Juneteenth; server midnight = 17:00 New York (EDT)
])
def test_a_holiday_is_its_whole_server_day_at_both_edges_in_both_dst_regimes(regime, day, midnight_utc):
    hl = load_holidays(ROOT / "config" / "holidays_cme_metals.yaml")
    start = pd.Timestamp(midnight_utc, tz="UTC")
    end = start + pd.Timedelta(days=1)
    assert server_date(STATIC, start - S) != day and hl.thin(server_date(STATIC, start - S)) is None
    assert server_date(STATIC, start) == day and hl.thin(day) is not None
    assert server_date(STATIC, end - S) == day
    assert server_date(STATIC, end) != day and hl.thin(server_date(STATIC, end)) is None


def test_engine_raises_the_holiday_flag_on_a_listed_day_only(tmp_path):
    eng, pb = _engine(tmp_path, RiskLimits(holidays=HolidaySettings(enabled=True)))
    thanksgiving = pd.Timestamp("2026-11-26 15:00", tz="UTC")
    st = _at(eng, pb, thanksgiving)
    assert st.holiday_thin and not st.calendar_unavailable
    assert "holiday_thin" in eng.gate.check(_intent(), st).reasons
    assert not _at(eng, pb, thanksgiving + pd.Timedelta(days=1)).holiday_thin
    off, pb2 = _engine(tmp_path / "off", RiskLimits())
    assert not _at(off, pb2, thanksgiving).holiday_thin


def test_a_missing_or_expired_holiday_list_fails_closed(tmp_path):
    lim = RiskLimits(holidays=HolidaySettings(enabled=True, file=str(tmp_path / "nope.yaml")))
    eng, pb = _engine(tmp_path, lim)
    st = _at(eng, pb, pd.Timestamp("2026-11-24 15:00", tz="UTC"))
    assert st.calendar_unavailable and not st.holiday_thin
    assert "calendar_unavailable" in eng.gate.check(_intent(), st).reasons
    shipped, pb2 = _engine(tmp_path / "s", RiskLimits(holidays=HolidaySettings(enabled=True)))
    assert not _at(shipped, pb2, pd.Timestamp("2028-12-29 15:00", tz="UTC")).calendar_unavailable
    assert _at(shipped, pb2, pd.Timestamp("2029-01-03 15:00", tz="UTC")).calendar_unavailable   # past valid_through


def test_a_holiday_list_rejects_days_outside_its_validity():
    with pytest.raises(ValueError, match="outside"):
        HolidayList(version="v", source="s", valid_from=date(2026, 1, 1), valid_through=date(2026, 12, 31),
                    days={date(2027, 1, 1): "New Year's Day"})


# ---------------------------------------------------------------------------------------------- disabled and exits
def test_with_every_window_off_the_engine_raises_no_flag_at_any_window(tmp_path):
    eng, pb = _engine(tmp_path, RiskLimits(), _feed("-05:00", "2026-01-15"))
    for ts in (pd.Timestamp("2026-01-15 13:30", tz="UTC"), DAILY["winter"] + M, WEEKLY["summer"] + M,
               pd.Timestamp("2026-11-26 15:00", tz="UTC"), pd.Timestamp("2030-01-08 15:00", tz="UTC")):
        st = _at(eng, pb, ts)
        assert not (st.tier2_blackout or st.reopen_window or st.holiday_thin or st.calendar_unavailable), ts


def test_exits_run_while_every_window_refuses_entries(tmp_path):
    """Entries only: a stop is still taken on Thanksgiving, inside a reopen window, with no calendar at all."""
    eng, pb = _engine(tmp_path, ON, data=False)
    t0 = pd.Timestamp("2026-11-25 23:03", tz="UTC")       # Thanksgiving 01:03 server, 1 min after the daily reopen
    pb.on_tick(Tick(ts_utc=t0, bid=2000.0, ask=2000.0))
    agent = eng.agents[next(iter(eng.agents))]
    ls = agent.label_spec
    prop = Proposal(proposal_id=f"icm-demo-{int(t0.timestamp())}-x", account_id="icm-demo", agent_id=agent.agent_id,
                    side=1, lots=0.1, entry=2000.0, stop=2000.0 - ls.stop_atr, target=2000.0 + ls.target_atr, p=0.6,
                    ev_r=0.1, spread_points=0.0, top_features=[])
    eng._execute(prop, agent, 0.1, 2000.0 - ls.stop_atr, 2000.0 + ls.target_atr, requested=2000.0, atr_usd=1.0)
    (pid,) = eng.open
    st = _at(eng, pb, t0 + M, px=2000.0)
    assert st.reopen_window and st.holiday_thin and st.calendar_unavailable
    assert set(eng.gate.check(_intent(), st).reasons) >= {"reopen_window", "holiday_thin", "calendar_unavailable"}
    pos = pb._positions[pid]
    pb._positions[pid] = pos.model_copy(update={"sl": None})   # stop lost at the broker: the engine's own check acts
    hit = Tick(ts_utc=t0 + 2 * M, bid=2000.0 - 2 * ls.stop_atr, ask=2000.0 - 2 * ls.stop_atr)
    pb.on_tick(hit)
    eng.on_tick(hit)
    assert not pb.positions()
    assert any(d["action"] == "engine_stop_close" for d in eng.decisions)
