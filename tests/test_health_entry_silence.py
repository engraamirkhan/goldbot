"""entry_silence:<account> (incident 2026-11-09: vantage-demo entered nothing for 9.5 h of open market, every gold arm
retired and its thin controls capped by 09:10, and nothing alerted). Warn, through the health alert path, when (a) an
exploring account has no arm that may enter while its market is open, or (b) it went 4 h of London/New York market
time without a fill while arms may enter; at most once per account per UTC day (goldbot/ops/entry_silence.py)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.engine import exploration
from goldbot.engine.exploration import LEDGER, ExplorationRecord
from goldbot.ops import entry_silence as es
from goldbot.ops import health
from goldbot.ops.accounts import DailyEntries
from goldbot.ops.entry_silence import AccountSupply, ArmSupply
from goldbot.ops.health import AlertPolicy, HealthContext, HealthWatch
from goldbot.research import exploration_data as ed

MON = pd.Timestamp("2026-11-09", tz="UTC")              # a Monday: gold open from 00:00 UTC
SAT = pd.Timestamp("2026-11-14", tz="UTC")
VANTAGE = "vantage-demo"
DAILY = DailyEntries(max_per_day=40, max_per_hour=4, thin_control_share=0.25, thin_control_fills=30)


def at(hm: str, day: pd.Timestamp = MON) -> pd.Timestamp:
    return day + pd.Timedelta(hours=int(hm[:2]), minutes=int(hm[3:]))


def _gold_retired() -> dict[str, ArmSupply]:
    """Vantage on 2026-11-09: every gold arm retired in the spec (VI0 VC1H VW0 Z6V Z7V ZCV LCZ6V LCZ7V)."""
    edge = {a: ArmSupply(kind="edge", blocked="spec retired") for a in ("Z6V", "Z7V")}
    ctl = {a: ArmSupply(kind="control", blocked="spec retired") for a in ("VI0", "VC1H", "VW0", "ZCV", "LCZ6V", "LCZ7V")}
    return {**edge, **ctl}


def _thin() -> dict[str, ArmSupply]:
    return {"S0-GBPJPY": ArmSupply(kind="thin", instrument="GBPJPY"), "I0-EURUSD": ArmSupply(kind="thin",
                                                                                            instrument="EURUSD")}


def supply(arms: dict[str, ArmSupply], daily: DailyEntries | None = DAILY,
           pairs: dict[str, tuple[str, ...]] | None = None) -> AccountSupply:
    return AccountSupply(broker="vantage", daily=daily, arms=arms, pairs=pairs or {})


def ctx(tmp_path: Path, now: pd.Timestamp, view: dict[str, AccountSupply]) -> HealthContext:
    return HealthContext(state_dir=tmp_path, now=now, get_secret=lambda k: None, entry_supply=lambda: view)


_n = [0]


def fill(tmp_path: Path, arm: str, sent: pd.Timestamp, *, symbol: str = "XAUUSD", account: str = VANTAGE,
         signal: pd.Timestamp | None = None) -> None:
    _n[0] += 1
    i = _n[0]
    rec = ExplorationRecord(client_order_id=f"o{i}", account_id=account, arm_id=arm, family="f", tf="5m", side=1,
                            lots=0.01, signal_utc=signal if signal is not None else sent - pd.Timedelta(seconds=20),
                            sent_utc=sent, atr_usd=1.0, stop_usd=1.0, target_usd=1.0, hold_s=60, position_id=i,
                            symbol=symbol)
    with exploration.ledger_db(str(tmp_path)).write() as conn:
        LEDGER.upsert(conn, rec)


def _morning_thin(tmp_path: Path) -> None:
    """Ten thin-control fills from 07:00 to 09:10: the 0.25 x 40 thin share spent, as on vantage 2026-11-09."""
    for k in range(10):
        fill(tmp_path, "S0-GBPJPY" if k % 2 else "I0-EURUSD", at("07:00") + pd.Timedelta(minutes=14 * k + 4),
             symbol="GBPJPY" if k % 2 else "EURUSD")


def check(tmp_path: Path, now: pd.Timestamp, view: dict[str, AccountSupply]) -> health.Check | None:
    return health.check_entry_silence(ctx(tmp_path, now, view), VANTAGE)


def replay(tmp_path: Path, view: dict[str, AccountSupply], start: pd.Timestamp, end: pd.Timestamp,
           every_min: int = 5) -> list[tuple[pd.Timestamp, str]]:
    """Health passes over [start, end) through the Telegram service's alert rule (settings hysteresis): the messages."""
    w = HealthWatch(tmp_path / "watch", policy=AlertPolicy.from_settings(load_settings().telegram))
    out = []
    for now in pd.date_range(start, end, freq=f"{every_min}min", inclusive="left"):
        c = check(tmp_path, now, view)
        checks = [c] if c is not None else []
        rep = health.HealthReport(ts=now, status=health._worst([x.status for x in checks]), checks=checks)
        if (text := w.poll(rep)) is not None:
            out.append((now, text))
    return out


def _warns(msgs: list[tuple[pd.Timestamp, str]], day: pd.Timestamp) -> list[pd.Timestamp]:
    return [t for t, text in msgs if t.floor("D") == day and f"WARN entry_silence:{VANTAGE}" in text]


# ------------------------------------------------------------------------------------------------ (a) no supply
def test_vantage_all_gold_retired_and_thin_controls_capped_warns_no_supply(tmp_path):
    _morning_thin(tmp_path)
    view = {VANTAGE: supply({**_gold_retired(), **_thin()})}
    before = check(tmp_path, at("09:00"), view)
    assert before is not None and before.status == "ok"          # 9 thin fills: the thin share is not spent yet
    c = check(tmp_path, at("10:30"), view)
    assert c is not None and c.name == f"entry_silence:{VANTAGE}" and c.status == "warn"
    assert "no exploration arm may enter" in c.reason and "8 spec retired" in c.reason
    assert "2 thin_controls" in c.reason and "10 of 40" in c.reason and "RUNBOOK" in c.reason


def test_the_alert_path_tells_entry_silence_warnings():
    assert health._alerting(f"entry_silence:{VANTAGE}", "warn")
    assert not health._alerting(f"entry_silence:{VANTAGE}", "ok")


def test_all_arms_retired_or_capped_warns_exactly_once_a_day(tmp_path):
    """The vantage day replayed every 5 minutes through the alert hysteresis: one warning on Monday (none on the
    daily break or after it), the recovery when Tuesday's thin share resets, nothing more."""
    _morning_thin(tmp_path)
    view = {VANTAGE: supply({**_gold_retired(), **_thin()})}
    msgs = replay(tmp_path, view, MON, MON + pd.Timedelta(hours=25))
    warns = _warns(msgs, MON)
    assert len(warns) == 1 and at("09:10") < warns[0] <= at("09:30")
    assert len([t for t, text in msgs if f"entry_silence:{VANTAGE}" in text]) == 2
    assert any("recovered: entry_silence:" in text for t, text in msgs if t >= MON + pd.Timedelta(days=1))


def test_runtime_retirements_and_cost_cells_count_as_no_supply(tmp_path):
    """An arm retired at its stop (aux.db exploration_arm_status) and a thin control whose instrument's cost cell is
    complete (30 fills ever, every arm of it) may not enter."""
    db = exploration.ledger_db(str(tmp_path))
    with db.write() as conn:
        conn.execute("INSERT INTO exploration_arm_status (broker, arm_id, status, day, rule, recorded_ns) "
                     "VALUES ('vantage', 'LB-GBPJPY', 'retired', '2026-11-06', 'H-61', 0)")
    for k in range(30):
        fill(tmp_path, "LB-GBPJPY", at("10:00", MON - pd.Timedelta(days=3)) + pd.Timedelta(minutes=k), symbol="GBPJPY")
    arms = {"LB-GBPJPY": ArmSupply(kind="edge", instrument="GBPJPY"),
            "S0-GBPJPY": ArmSupply(kind="thin", instrument="GBPJPY")}
    c = check(tmp_path, at("09:00"), {VANTAGE: supply(arms)})
    assert c is not None and c.status == "warn"
    assert "1 retired at its stop" in c.reason and "1 thin_cell_complete" in c.reason


def test_a_paired_control_without_an_active_arm_cannot_enter(tmp_path):
    arms = {"Z6V": ArmSupply(kind="edge", blocked="spec retired"), "ZCV": ArmSupply(kind="control")}
    c = check(tmp_path, at("09:00"), {VANTAGE: supply(arms, pairs={"ZCV": ("Z6V", "Z7V")})})
    assert c is not None and c.status == "warn" and "1 control without an active arm" in c.reason
    ok = check(tmp_path, at("09:00"), {VANTAGE: supply({**arms, "Z7V": ArmSupply(kind="edge")},
                                                       pairs={"ZCV": ("Z6V", "Z7V")})})
    assert ok is not None and ok.status == "ok"


def test_no_supply_is_not_judged_on_a_closed_market_day(tmp_path):
    c = check(tmp_path, at("12:00", SAT), {VANTAGE: supply(_gold_retired())})
    assert c is not None and c.status == "ok" and "market closed today" in c.reason


def test_an_account_that_does_not_explore_has_no_check(tmp_path):
    assert check(tmp_path, at("12:00"), {}) is None
    no_view = HealthContext(state_dir=tmp_path, now=at("12:00"), get_secret=lambda k: None)
    assert [c for c in health.run_checks(no_view).checks if c.name.startswith("entry_silence")] == []


# ------------------------------------------------------------------------------------------------ (b) silence
def _lb() -> dict[str, ArmSupply]:
    return {**_gold_retired(), **_thin(), "LB-GBPJPY": ArmSupply(kind="edge", instrument="GBPJPY")}


def test_four_hours_of_london_new_york_without_a_fill_warns(tmp_path):
    _morning_thin(tmp_path)
    fill(tmp_path, "LB-GBPJPY", at("10:30"), symbol="GBPJPY")
    view = {VANTAGE: supply(_lb())}
    early = check(tmp_path, at("14:29"), view)
    assert early is not None and early.status == "ok"
    c = check(tmp_path, at("14:30"), view)
    assert c is not None and c.status == "warn"
    assert "no exploration fill for 4 h" in c.reason and "10:30-14:30 UTC" in c.reason and "1 arm(s)" in c.reason


def test_asia_silence_does_not_count(tmp_path):
    """No fill at all since midnight: at 10:00 only the two London hours count."""
    c = check(tmp_path, at("10:00"), {VANTAGE: supply(_lb())})
    assert c is not None and c.status == "ok" and "longest London/NY silence today 2 h 00 min" in c.reason


def test_a_spent_session_or_day_budget_is_silence_by_design(tmp_path):
    """max_per_day 3 splits 1/1/1 with no history: London's one fill at 08:05 spends London, so London stays quiet by
    design until New York opens at 13:30 UTC (08:30 New York, winter)."""
    daily = DailyEntries(max_per_day=3, thin_control_fills=None)
    fill(tmp_path, "LB-GBPJPY", at("08:05"), symbol="GBPJPY")
    c = check(tmp_path, at("13:25"), {VANTAGE: supply(_lb(), daily=daily)})
    assert c is not None and c.status == "ok"
    for hm in ("00:10", "13:40"):                                  # Asia, then New York: the day's 3 are spent
        fill(tmp_path, "LB-GBPJPY", at(hm), symbol="GBPJPY")
    spent = check(tmp_path, at("21:00"), {VANTAGE: supply(_lb(), daily=daily)})
    assert spent is not None and spent.status == "ok" and spent.reason.startswith("day budget spent")


def test_one_warning_a_day_even_when_fills_resume_after_the_silence(tmp_path):
    """(b) is the longest silent run so far today: a fill at 16:00 does not clear it, so the owner hears it once."""
    _morning_thin(tmp_path)
    for hm in ("10:30", "16:00"):
        fill(tmp_path, "LB-GBPJPY", at(hm), symbol="GBPJPY")
    msgs = replay(tmp_path, {VANTAGE: supply(_lb())}, at("06:00"), MON + pd.Timedelta(hours=24))
    warns = _warns(msgs, MON)
    assert len(warns) == 1 and at("14:30") <= warns[0] <= at("14:50")
    assert not any("recovered: entry_silence:" in text for t, text in msgs if t < MON + pd.Timedelta(days=1))


# ------------------------------------------------------------------------------------------------ the config side
def test_supply_from_the_real_config_blocks_every_spec_retired_arm():
    from goldbot.engine.exploration import config_for, spec_retired
    from goldbot.ops import accounts
    listed = accounts.load_accounts(logins=False)
    every = [a for a in listed.values() if a.mode == "demo" and a.exploration.enabled]
    demo = [a for a in every if not a.weekend_only]
    if not demo:
        pytest.skip("no exploring demo account configured")
    live = load_settings().engine.timeframes_live
    view = es.supply_by_account(every, live)
    assert set(view) == {a.account_id for a in demo}          # a weekend_only lane is weekend_247's, not judged here
    for acc in demo:
        cfg = config_for(acc)
        assert cfg is not None
        s = view[acc.account_id]
        assert s.broker == acc.broker and s.daily == cfg.daily
        assert set(s.arms) == {a.arm_id for a in cfg.arms}
        for arm_id in spec_retired(cfg):
            assert s.arms[arm_id].blocked is not None


def test_session_split_is_the_engines_rule():
    from goldbot.engine import exploration_budget as budget
    assert budget.session_split([], 40) == {"asia": 13, "london": 13, "newyork": 13}
    london = [int(at("09:00").value)] * 3 + [int(at("15:00").value)]
    assert budget.session_split(london, 40) == {"asia": 1, "london": 30, "newyork": 10}


def test_history_candidates_shape_the_session_budgets(tmp_path):
    """Trailing edge candidates all in London: London takes the day's budget, so a London silence counts until its
    budget is spent, and a New York with a budget of 1 is spent by its first fill."""
    db = exploration.ledger_db(str(tmp_path))
    for d in range(1, 6):
        ed.record_candidate(db, candidate_id=f"c{d}", account_id=VANTAGE, broker="vantage", arm_id="LB-GBPJPY",
                            family="f", style="intraday", tf="5m", side=1,
                            decision_utc=at("09:00", MON - pd.Timedelta(days=d)), stop_dist=1.0, target_dist=1.0,
                            max_hold_s=60, taken=True)
    daily = DailyEntries(max_per_day=10, thin_control_fills=None)
    fill(tmp_path, "LB-GBPJPY", at("14:05"), symbol="GBPJPY")      # New York's one (max(1, round(10 x 0)))
    v = es.evaluate(supply(_lb(), daily=daily), tmp_path / "aux.db", VANTAGE, at("20:00"), health.DEFAULT_SESSIONS)
    assert (v.run_min, v.run_from, v.run_to) == (6 * 60 + 5, at("08:00"), at("14:05"))   # 08:00 to the NY fill
    assert v.silent and not v.no_supply


def test_the_supply_view_is_read_once_per_ttl_or_config_change(monkeypatch):
    """Reading the arm spec costs about 2 s; the Telegram service runs health every 60 s."""
    calls: list[int] = []

    def read(accs: object, live: object) -> dict[str, AccountSupply]:
        calls.append(1)
        return {}
    monkeypatch.setattr(es, "supply_by_account", read)
    es._VIEW.clear()
    t = [0.0]
    for step in (0.0, 60.0, es.VIEW_TTL_S - 61.0):
        t[0] += step
        es.cached_supply([], ["15m"], clock=lambda: t[0])
    assert len(calls) == 1
    t[0] += 2.0                                                    # past the TTL
    es.cached_supply([], ["15m"], clock=lambda: t[0])
    es.cached_supply([], ["15m", "1h"], clock=lambda: t[0])        # another key: read at once
    assert len(calls) == 3
    es._VIEW.clear()
