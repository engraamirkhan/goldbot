"""The XAUUSD247 weekend lane (ADR 0062): `vantage-247`, a Vantage Standard STP demo trading the 24/7 gold CFD only at
the weekend, on its own terminal beside vantage-demo. "An always-open account trades when its own calendar says open;
every other account keeps the weekday calendar and the Friday cut byte for byte. A weekend_only account takes entries
only inside its window and is flat before the Monday reconvergence. Nothing of XAUUSD247 pools with XAUUSD."""
from __future__ import annotations

from datetime import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.config import load_settings
from goldbot.data.calendar import (
    DEFAULT_SESSIONS,
    PauseWindow,
    SessionTable,
    WeekendWindow,
    session_table_cache,
    session_table_from_cache,
)
from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import Engine, EngineConfig, cost_probe, exploration, magic, weekend_lane
from goldbot.execution.broker import AccountInfo, OrderIntent, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
from goldbot.ops import jobs
from goldbot.ops.services.engine import release_symbol
from goldbot.research import arm_rules, arms
from goldbot.risk import RiskLimits
from goldbot.telegram.approvals import ApprovalCenter

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "config" / "exploration_arms.yaml"
SAT = pd.Timestamp("2025-03-08 12:00", tz="UTC")          # Saturday noon UTC: gold closed, XAUUSD247 open
MON = pd.Timestamp("2025-03-10 10:00", tz="UTC")          # Monday 10:00 UTC: the main session
FRI_LATE = pd.Timestamp("2025-03-07 22:00", tz="UTC")     # Friday 22:00 UTC (00:00 Athens Saturday): inside the lane
SUN_CUT = pd.Timestamp("2025-03-09 21:00", tz="UTC")      # the lane's default flat cut
LANE_ARMS = {"WKS0": "S0", "WKI0": "I0"}                 # the weekend controls and the arms whose geometry they copy


@pytest.fixture(scope="module")
def accounts() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(logins=False)


# ------------------------------------------------------------------------------------------------ the account
def test_vantage_247_ships_disabled_as_a_demo_probe_only_weekend_lane_on_its_own_symbol(accounts):
    acc = accounts["vantage-247"]
    assert acc.mode == "demo" and not acc.enabled and acc.login is None
    assert acc.probe_only and not acc.shadow_host and acc.approval_mode == "auto"
    assert acc.broker == "vantage247" and acc.symbol == "XAUUSD247" and acc.store_symbol == "XAUUSD247"
    assert acc.contract_size == 1.0 and acc.account_class == "standard"
    assert acc.sessions == "always_open" and acc.pauses == () and acc.weekend_only
    assert acc.weekend_window == WeekendWindow()                       # Fri 21:00 .. Sun 21:00 UTC
    assert acc.terminal_path == r"C:\MT5\Vantage247\terminal64.exe"
    assert acc.daily_loss_limit_abs is not None and acc.daily_loss_limit_abs <= 20


def test_vantage_247_runs_only_the_weekend_random_controls_and_its_own_cost_probe(accounts):
    acc = accounts["vantage-247"]
    assert set(acc.exploration.arms) == set(LANE_ARMS)
    assert all(a.control_share is not None for a in acc.exploration.arms.values())
    assert acc.cost_probe.enabled and acc.cost_probe.scheduled.enabled
    spec = {a.id: a for a in arms.load_arms(SPEC)}
    for lane_id, src in LANE_ARMS.items():
        a, s = spec[lane_id], spec[src]
        assert a.role == "control" and a.rule == "random_bracket" and a.forward_only and a.status == "run"
        geometry = ("stop_usd", "stop_atr", "target_r", "hold_bars", "minutes")
        assert a.timeframe == s.timeframe and {k: a.params.get(k) for k in geometry} == {k: s.params.get(k) for k in geometry}
        assert a.params["weekdays"] == [5, 6]
        assert not a.entry_refused                                       # a control: exempt from the qualification


def test_the_weekend_controls_never_join_the_history_matrix(accounts):
    """Forward only: XAUUSD247 has no history, and the matrix runs on XAUUSD bars (pooling)."""
    assert not set(LANE_ARMS) & set(arms.runnable_ids(SPEC))


def test_magic_ranges_still_do_not_overlap_and_every_login_stays_null(accounts):
    bases = sorted(a.magic_base for a in accounts.values())
    assert accounts["vantage-247"].magic_base == 260700
    assert all(b - a >= 100 for a, b in zip(bases, bases[1:]))
    raw = (REPO / "config" / "accounts.yaml").read_text(encoding="utf-8")
    assert all(line.split("#")[0].strip() == "login: null" for line in raw.splitlines() if "login:" in line.split("#")[0])


def test_the_shared_login_check_still_passes_with_the_lane_account(accounts):
    accs = {k: v.model_copy(update={"login": 1000 + i}) for i, (k, v) in enumerate(accounts.items())}
    assert acc_mod.shared_logins(accs.values()) == []
    # the same login on vantage-demo's server would be refused once both are enabled
    twin = accs["vantage-247"].model_copy(update={"enabled": True, "login": accs["vantage-demo"].login,
                                                  "server": accs["vantage-demo"].server})
    assert acc_mod.shared_logins([accs["vantage-demo"], twin]) == [("vantage-247", "vantage-demo")]


def test_the_lane_account_is_not_enabled_until_its_login_exists(accounts):
    assert "vantage-247" not in {a.account_id for a in acc_mod.enabled_accounts(logins=False)}


def _lane(**kw: Any) -> acc_mod.Account:
    base: dict[str, Any] = dict(account_id="x247", broker="vantage247", mode="demo", server="S", login=None,
                                terminal_path="t", server_tz="NY+7", symbol="XAUUSD247", magic_base=270000,
                                enabled=False, sessions="always_open", store_symbol="XAUUSD247", weekend_only=True)
    base.update(kw)
    return acc_mod.Account(**base)


@pytest.mark.parametrize("bad", [
    {"sessions": "broker"},                                  # weekend_only needs an always-open symbol
    {"mode": "live"},                                        # always-open trading is demo only (owner-only for money)
    {"store_symbol": None},                                  # its data needs its own store symbol
    {"store_symbol": "XAUUSD"},                              # never the weekday history's symbol
    {"pauses": (PauseWindow(start=time(1), end=time(2)),), "sessions": "broker", "weekend_only": False},
])
def test_a_lane_config_that_could_trade_or_pool_wrongly_fails_at_load(bad):
    with pytest.raises(ValidationError):
        _lane(**bad)


def test_a_weekend_only_account_with_a_strategy_arm_fails_at_load():
    ex = acc_mod.Exploration(enabled=True, budget_share=0.5, max_open_total=2,
                             pots={"scalp": acc_mod.ExplorationPot(gbp=5, max_open=1)},
                             arms={"S1A": acc_mod.ExplorationArm(max_loss=2, style="scalp")})
    with pytest.raises(ValidationError, match="controls only"):
        _lane(probe_only=True, daily_loss_limit_abs=20, exploration=ex)


def test_an_always_open_account_never_shares_its_broker_key_with_a_weekday_account(tmp_path):
    raw = (REPO / "config" / "accounts.yaml").read_text(encoding="utf-8")
    f = tmp_path / "accounts.yaml"
    f.write_text(raw.replace("    broker: vantage247\n", "    broker: vantage\n"), encoding="utf-8")
    with pytest.raises(ValueError, match="broker key"):
        acc_mod.load_accounts(f, logins=False)


def test_the_lane_reads_and_writes_its_own_store_symbol_and_its_broker_is_not_the_canonical_one(accounts):
    s = load_settings()
    acc = accounts["vantage-247"]
    assert release_symbol(s, acc) == "XAUUSD247"
    assert release_symbol(s, accounts["vantage-demo"]) == "XAUUSD"
    assert not s.brokers["vantage247"].canonical_costs
    others = {a.broker for k, a in accounts.items() if k != "vantage-247"}
    assert acc.broker not in others


def test_no_other_brokers_cost_table_is_ever_the_lanes(tmp_path, accounts):
    from goldbot.execution.costs import CostTable
    for aid, n in (("vantage-demo", 10), ("vantage-247", 500)):
        CostTable(account_id=aid, built_utc=SAT, spread={}, slippage={}, commission_per_lot_side_usd=0.0,
                  slippage_prior_usd=0.05, n_fills=n).save(tmp_path / f"costs_{aid}.json")
    found = jobs.broker_cost_table(list(accounts.values()), tmp_path, "vantage")
    assert found is not None and found[1].account_id == "vantage-demo"


# ------------------------------------------------------------------------------------------------ the calendar
def test_an_always_open_table_is_open_at_the_weekend_and_the_default_table_is_not():
    t = SessionTable(always_open=True)
    assert t.is_open(pd.DatetimeIndex([SAT, MON, FRI_LATE])).tolist() == [True, True, True]
    assert DEFAULT_SESSIONS.is_open(pd.DatetimeIndex([SAT])).tolist() == [False]
    assert t.entry_block(SAT) is None and DEFAULT_SESSIONS.entry_block(SAT) is not None


def test_a_broker_pause_closes_an_always_open_table_inside_it_only():
    t = SessionTable(always_open=True, pauses=(PauseWindow(weekday=5, start=time(11, 30), end=time(12, 30)),))
    sat = pd.DatetimeIndex([SAT, SAT + pd.Timedelta(hours=1), SAT + pd.Timedelta(days=7)])
    assert t.is_open(sat).tolist() == [False, True, False]
    assert t.is_open(pd.DatetimeIndex([MON])).tolist() == [True]


def test_an_always_open_table_never_takes_the_brokers_weekday_session_list():
    class _Broker:
        def trading_sessions(self, symbol: str) -> list:
            raise AssertionError("not asked")
    base = SessionTable(always_open=True)
    t = SessionTable.from_broker(_Broker(), "XAUUSD247", SAT, base)
    assert t.always_open and t.broker_windows is None and t.source == "always_open"


def test_the_engine_cache_keeps_always_open_and_the_pauses():
    t = SessionTable(always_open=True, pauses=(PauseWindow(start=time(1), end=time(2)),))
    back = session_table_from_cache(session_table_cache(t))
    assert back is not None and back.always_open and back.pauses == t.pauses
    plain = session_table_from_cache(session_table_cache(DEFAULT_SESSIONS))
    assert plain is not None and not plain.always_open


def test_a_pause_a_newer_engine_wrote_with_an_extra_field_still_reads():
    cache = session_table_cache(SessionTable(always_open=True, pauses=(PauseWindow(start=time(1), end=time(2)),)))
    cache["pauses"][0]["reason"] = "a field from a newer release"
    back = session_table_from_cache(cache)
    assert back is not None and back.pauses == (PauseWindow(start=time(1), end=time(2)),)


def test_the_weekend_window_holds_friday_night_to_sunday_night_utc():
    w = WeekendWindow()
    assert [w.inside(x) for x in (FRI_LATE, SAT, SUN_CUT - pd.Timedelta(minutes=1), SUN_CUT, MON)] == \
        [True, True, True, False, False]
    assert w.next_cut(SAT) == SUN_CUT and w.next_cut(SUN_CUT) == SUN_CUT + pd.Timedelta(days=7)
    assert w.last_cut(MON) == SUN_CUT and w.last_cut(SUN_CUT) == SUN_CUT


# ------------------------------------------------------------------------------------------------ the engine
class _Terminal(PaperBroker):
    def account(self) -> AccountInfo:
        return super().account().model_copy(update={"trade_mode": "demo"})


def _engine(tmp_path: Path, *, lane: bool, weekend_only: bool = True, **cfg: Any) -> tuple[Engine, PaperBroker]:
    pb = _Terminal(equity=10_000, adverse_slip_points=0.0, symbol="XAUUSD247" if lane else "XAUUSD")
    extra: dict[str, Any] = {"always_open": True, "symbol": "XAUUSD247", "release_symbol": "XAUUSD247",
                             "weekend_window": WeekendWindow() if weekend_only else None} if lane else {}
    eng = Engine(EngineConfig(account_id="v247" if lane else "icm-probe", broker_name="vantage247" if lane else "icm",
                              mode="demo", approval_mode="auto", declared_class="raw", state_dir=str(tmp_path),
                              owner_user_id=111, probe_only=True, server_tz="NY+7", **extra, **cfg),
                 pb, [], {}, ApprovalCenter({111}), limits=RiskLimits(probe_only=True, max_positions=10))
    return eng, pb


def _tick(pb: PaperBroker, at: pd.Timestamp, px: float = 2400.0) -> Tick:
    t = Tick(ts_utc=at, bid=px, ask=px + 0.2)
    pb.on_tick(t)
    return t


def _open(eng: Engine, pb: PaperBroker, side: int, at: pd.Timestamp, px: float = 2400.0) -> int:
    _tick(pb, at, px)
    r = pb.place_order(OrderIntent(client_order_id=f"t{side}{at.value}", symbol=eng.cfg.symbol, side=side, lots=0.01,
                                   sl=px - side * 10, tp=px + side * 10, magic=magic.exploration(eng.cfg.magic_base)))
    assert r.ok and r.position_id is not None
    return r.position_id


@pytest.mark.parametrize("at, weekend", [(MON, False), (pd.Timestamp("2025-03-07 19:31", tz="UTC"), True), (SAT, True)])
def test_a_weekday_account_keeps_the_friday_cut_and_the_weekend_block_unchanged(tmp_path, at, weekend):
    eng, pb = _engine(tmp_path, lane=False)
    assert weekend_lane.lane(eng.cfg, at) is None                      # the old path, untouched
    eng._server_clock(at, _tick(pb, at))
    assert eng.state.weekend is weekend


@pytest.mark.parametrize("at, blocked", [(FRI_LATE, False), (SAT, False), (SUN_CUT, True), (MON, True)])
def test_a_weekend_only_account_takes_entries_only_inside_its_window(tmp_path, at, blocked):
    eng, pb = _engine(tmp_path, lane=True)
    eng._server_clock(at, _tick(pb, at))
    assert eng.state.weekend is blocked
    assert eng.sessions.always_open and eng.sessions.entry_block(at) is None


def test_an_always_open_account_without_weekend_only_has_no_cut_at_all(tmp_path):
    eng, pb = _engine(tmp_path, lane=True, weekend_only=False)
    pid = _open(eng, pb, -1, FRI_LATE)
    for at in (pd.Timestamp("2025-03-07 19:31", tz="UTC"), SAT, SUN_CUT, MON):
        eng._server_clock(at, _tick(pb, at))
        assert not eng.state.weekend
    assert pid in {p.position_id for p in pb.positions()}


def test_at_the_sunday_cut_every_own_position_is_closed_winners_too_once(tmp_path):
    eng, pb = _engine(tmp_path, lane=True)
    win, lose = _open(eng, pb, 1, SAT, 2400.0), _open(eng, pb, -1, SAT, 2400.0)
    after = SUN_CUT + pd.Timedelta(seconds=5)
    eng._server_clock(after, _tick(pb, after, 2405.0))                 # the long wins, the short loses
    assert not {win, lose} & {p.position_id for p in pb.positions()}
    closes = [d for d in eng.decisions if d.get("action") == "weekend_lane_flat"]
    assert {d["position"] for d in closes} == {win, lose}
    assert eng._weekend_done == weekend_lane.lane(eng.cfg, after).key   # type: ignore[union-attr]
    eng._server_clock(after + pd.Timedelta(minutes=1), _tick(pb, after + pd.Timedelta(minutes=1)))
    assert len([d for d in eng.decisions if d.get("action") == "weekend_lane_flat"]) == 2


def test_without_a_quote_the_lane_still_flattens_after_the_cut(tmp_path):
    eng, pb = _engine(tmp_path, lane=True)
    pid = _open(eng, pb, 1, SAT)
    after = SUN_CUT + pd.Timedelta(minutes=2)
    eng.clock = lambda: after
    eng._quote_invalid_since = after - pd.Timedelta(seconds=1)       # a fresh gap: the lane does not wait for "dead"
    eng._host_clock_exits(after)
    assert pid not in {p.position_id for p in pb.positions()}


def test_a_weekday_accounts_no_quote_friday_flatten_still_waits_for_a_dead_feed(tmp_path):
    eng, pb = _engine(tmp_path, lane=False)
    fri = pd.Timestamp("2025-03-07 19:45", tz="UTC")                  # past the 21:30 Athens cut
    pid = _open(eng, pb, 1, fri - pd.Timedelta(hours=1))
    eng._quote_invalid_since = fri - pd.Timedelta(seconds=1)
    eng._host_clock_exits(fri)
    assert pid in {p.position_id for p in pb.positions()}


def test_an_exploration_hold_may_not_reach_the_lanes_cut_and_the_friday_cut_does_not_apply(tmp_path):
    eng, _ = _engine(tmp_path, lane=True)
    # an hour from Friday 21:15 UTC reaches the weekday cut (21:30 Athens = 19:30 UTC) for a weekday engine only
    fri = pd.Timestamp("2025-03-07 21:15", tz="UTC")
    assert not exploration.crosses_friday(eng, fri, 3600)
    assert exploration.crosses_friday(_engine(tmp_path / "wk", lane=False)[0], pd.Timestamp("2025-03-07 19:15",
                                                                                             tz="UTC"), 3600)
    assert exploration.crosses_friday(eng, pd.Timestamp("2025-03-07 19:15", tz="UTC"), 600)   # before the window
    assert exploration.crosses_friday(eng, SUN_CUT - pd.Timedelta(minutes=30), 3600)
    assert not exploration.crosses_friday(eng, SAT, 3600)
    flat, _ = _engine(tmp_path / "plain", lane=True, weekend_only=False)
    assert not exploration.crosses_friday(flat, SUN_CUT - pd.Timedelta(minutes=30), 3600)


def test_scheduled_cost_probes_run_at_the_weekend_on_the_lane_only(tmp_path):
    eng, _ = _engine(tmp_path, lane=True)
    plain, _ = _engine(tmp_path / "plain", lane=False)
    assert cost_probe.schedule_skip(eng, SAT) is None
    assert cost_probe.schedule_skip(eng, MON) == "weekend_lane"
    assert cost_probe.schedule_skip(plain, SAT) == "weekend"
    assert cost_probe.schedule_skip(plain, MON) is None


def test_the_engine_records_its_weekend_ticks_under_its_own_store_symbol(tmp_path):
    from goldbot.data.store import Store
    eng, pb = _engine(tmp_path, lane=True, data_root=str(tmp_path / "data"))
    for k in range(3):
        eng._tick_log.append(_tick(pb, SAT + pd.Timedelta(seconds=k)))
    eng.flush_ticks()
    assert len(Store(tmp_path / "data").read("ticks", source="v247", symbol="XAUUSD247")) == 3
    assert Store(tmp_path / "data").read("ticks", source="v247", symbol="XAUUSD").empty


# ------------------------------------------------------------------------------------------------ the control rule
def _ctx(start: str, end: str) -> arm_rules.RuleContext:
    ticks = synthetic_ticks(start, end, ticks_per_minute=1, seed=3, sessions=SessionTable(always_open=True))
    b1 = ticks_to_1m(ticks, SessionTable(always_open=True))
    return arm_rules.RuleContext(bars=b1, b1=b1, blackout_lo=np.zeros(0, dtype="int64"),
                                 blackout_hi=np.zeros(0, dtype="int64"))


def test_a_weekend_control_fires_on_saturday_and_sunday_only_outside_the_rollover():
    spec = {a.id: a for a in arms.load_arms(SPEC)}
    ctx = _ctx("2025-03-06", "2025-03-11")
    for arm_id in LANE_ARMS:
        out = arm_rules.random_bracket(spec[arm_id], ctx)
        days = ctx.close_ts[out["idx"].to_numpy()]
        assert set(days.dayofweek) == {5, 6}
        m = days.hour * 60 + days.minute
        assert not ((m >= 20 * 60 + 45) & (m <= 22 * 60 + 15)).any()   # the common rollover filter still applies
    # S0 itself is unchanged: Monday to Friday only, never Friday after 19:00
    s0 = arm_rules.random_bracket(spec["S0"], ctx)
    assert set(ctx.close_ts[s0["idx"].to_numpy()].dayofweek) <= {0, 1, 2, 3, 4}


def _explorer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, lane: bool) -> tuple[Engine, PaperBroker]:
    """An engine running the real WKI0 control (I0's geometry on Saturday/Sunday closes) on always-open bars."""
    from goldbot.engine.exploration import Arm, ExplorationConfig, Pot
    monkeypatch.setattr(cost_probe, "stop_loss", lambda *a, **k: 1.0)
    spec = {a.id: a for a in arms.load_arms(SPEC)}
    cfg = ExplorationConfig(enabled=True, budget_share=0.8, max_open_total=6,
                            pots={"intraday": Pot(gbp=8, max_open=3)},
                            arms=(Arm(spec=spec["WKI0"], style="intraday", control_share=1.0),))
    pb = _Terminal(equity=10_000, adverse_slip_points=0.0, symbol="XAUUSD247" if lane else "XAUUSD")
    extra: dict[str, Any] = {"always_open": True, "symbol": "XAUUSD247", "release_symbol": "XAUUSD247",
                             "weekend_window": WeekendWindow()} if lane else {}
    eng = Engine(EngineConfig(account_id="v247", broker_name="vantage247" if lane else "icm", mode="demo",
                              approval_mode="auto", declared_class="raw", state_dir=str(tmp_path), owner_user_id=111,
                              probe_only=True, exploration_config=cfg, **extra),
                 pb, [], {}, ApprovalCenter({111}),
                 limits=RiskLimits(risk_per_trade=0.002, multiplier_bounds=(1.0, 1.0), max_positions=10,
                                   probe_only=True, exploration_arms={"WKI0": 40.0}, daily_loss_limit_abs=220.0))
    return eng, pb


@pytest.mark.parametrize("lane", [True, False])
def test_a_saturday_control_entry_is_sent_on_the_lane_and_never_on_a_weekday_account(tmp_path, monkeypatch, lane):
    eng, pb = _explorer(tmp_path, monkeypatch, lane=lane)
    ticks = synthetic_ticks("2025-03-06", "2025-03-09", ticks_per_minute=1, seed=4,
                            sessions=SessionTable(always_open=True))
    close = SAT
    eng.bars_1m = ticks_to_1m(ticks[ticks["ts_utc"] < close], weekend_lane.bar_table(eng))
    px = float(ticks[ticks["ts_utc"] < close]["bid"].iloc[-1])
    tick = Tick(ts_utc=close + pd.Timedelta(seconds=2), bid=px, ask=px + 0.2)
    pb.on_tick(tick)
    eng.clock = lambda: tick.ts_utc
    eng._last_tick = tick
    eng._refresh_account(tick)
    eng._closed_through = close
    exploration.on_pass(eng, tick)
    sent = [d for d in eng.decisions if str(d.get("action", "")).startswith("exploration:")]
    if lane:
        assert [d["action"] for d in sent] == ["exploration:executed:auto"], eng.decisions[-5:]
        assert [p.symbol for p in pb.positions()] == ["XAUUSD247"]
    else:
        assert not sent and not pb.positions()                       # weekday bars: no Saturday close exists


def test_tick_record_leaves_an_always_open_account_to_its_engine(tmp_path, accounts):
    lane = accounts["vantage-247"]
    def asked(a: Any) -> Any:
        raise AssertionError("its bridge is never asked for the bulk tick pull")
    ctx: Any = SimpleNamespace(broker_for=asked, accounts=[lane], clock=lambda: SAT, sleep=None, settings=None,
                               state_dir=tmp_path)
    out = jobs.tick_record(ctx, SAT)
    assert "always open" in out["vantage-247"]["skipped"]


# ------------------------------------------------------------------------------------------------ health
def _health(tmp_path: Path, *, enabled: bool, now: pd.Timestamp, state: dict | None) -> Any:
    import json

    from goldbot.ops import health
    if state is not None:
        (tmp_path / "engine_vantage-247.json").write_text(json.dumps(state), encoding="utf-8")
    ctx = health.HealthContext(state_dir=tmp_path, now=now, get_secret=lambda k: None,
                               weekend_lanes=[health.WeekendLaneRef(account_id="vantage-247", enabled=enabled,
                                                                    window=WeekendWindow())])
    return health.check_weekend_247(ctx)


def test_the_weekend_247_line_says_off_while_the_account_is_disabled(tmp_path):
    c = _health(tmp_path, enabled=False, now=SAT, state=None)
    assert c.name == "weekend_247" and c.status == "ok" and "off" in c.reason


def test_the_weekend_247_line_fails_a_silent_engine_inside_the_window_and_passes_a_live_one(tmp_path):
    stale = {"ts": (SAT - pd.Timedelta(minutes=30)).timestamp(), "open_positions": 0, "last_tick_age_s": 1800}
    assert _health(tmp_path, enabled=True, now=SAT, state=stale).status == "fail"
    live = {"ts": SAT.timestamp() - 5, "open_positions": 1, "last_tick_age_s": 3}
    c = _health(tmp_path, enabled=True, now=SAT, state=live)
    assert c.status == "ok" and "in its window" in c.reason


def test_the_weekend_247_line_fails_a_position_carried_past_the_sunday_cut(tmp_path):
    later = SUN_CUT + pd.Timedelta(minutes=20)
    carried = {"ts": later.timestamp() - 5, "open_positions": 1, "last_tick_age_s": 3}
    c = _health(tmp_path, enabled=True, now=later, state=carried)
    assert c.status == "fail" and "past" in c.reason
    flat = {"ts": MON.timestamp() - 5, "open_positions": 0, "last_tick_age_s": 3}
    assert _health(tmp_path, enabled=True, now=MON, state=flat).status == "ok"
