"""An invalid feed never stops the exits that need no price (trading-safety review of fefc6bc).

A dropped quote (NaN, crossed, non-positive or out of order) or a `last_tick` read that raises skips everything that
needs our price, but closes already decided are retried, a pending kill-switch flatten runs (market orders need no
price of ours), time barriers and the Friday flatten are judged on the host clock, and the state file keeps being
written with `quote_invalid_since` so health FAILs (alerted) after QUOTE_INVALID_FAIL_S of invalid quotes in market
hours. Also: the gate checks domains (price, ATR, stop, contract and volumes > 0, equity > 0) and keeps the flag
reasons beside `invalid_state`; an approval is refused on a quote the tick rules reject; the supervisor's halts
alert; health survives type-corrupt engine files; the supervisor traceback is rate-limited."""
import json
import logging
import math
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.timeutil import NY_PLUS_7, server_wall
from goldbot.execution.broker import OrderResult, Tick
from goldbot.ops import health
from goldbot.ops import run as ops_run
from goldbot.risk import AccountState, Intent, RiskGate
from goldbot.risk.gate import Stage
from goldbot.risk.supervisor import Supervisor
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import Outcome, Proposal
from tests.test_engine_clock import FRI, WED, _engine, _intent, _open, _StopStuck, _tick

NAN = Tick(ts_utc=WED, bid=math.nan, ask=math.nan)


class _FlakyClose(_StopStuck):
    """The first `fail` closes are refused (a requote); later ones fill."""
    fail = 0
    calls = 0

    def close(self, position_id: int, lots: float | None = None) -> OrderResult:
        self.calls += 1
        if self.fail > 0:
            self.fail -= 1
            return OrderResult(ok=False, retcode=10004, order_id=None, position_id=position_id, filled_lots=0.0,
                               price=None, message="requote")
        return super().close(position_id, lots)


def _setup(tmp_path: Path, start: pd.Timestamp = WED) -> tuple[Any, _FlakyClose, list[pd.Timestamp], int]:
    pb = _FlakyClose(equity=10_000)
    pb.on_tick(_tick(start))
    eng, _, host = _engine(tmp_path, start=start, pb=pb)
    pid = _open(pb, 1, 2300.0)
    eng.open[pid] = eng._adopt(next(p for p in pb.positions() if p.position_id == pid))
    return eng, pb, host, pid


def _nan_feed(eng: Any, host: list[pd.Timestamp], seconds: int, step: float = 1.0) -> None:
    t0 = host[0]
    for i in range(int(seconds / step)):
        host[0] = t0 + pd.Timedelta(seconds=(i + 1) * step)
        eng.on_tick(Tick(ts_utc=host[0], bid=math.nan, ask=math.nan))


def _open_ids(pb: Any) -> set[int]:
    return {p.position_id for p in pb.positions()}


# ---------------------------------------------------------------------------------------------- MEDIUM-2: engine
def test_a_persistently_nan_feed_still_retries_a_pending_close(tmp_path):
    eng, pb, host, pid = _setup(tmp_path)
    pb.fail = 1
    assert not eng._try_close(pid, "time_exit", WED).ok and pid in eng._close_due      # decided, refused once
    _nan_feed(eng, host, 3)
    assert pid not in _open_ids(pb) and pid not in eng._close_due


def test_a_persistently_nan_feed_still_flattens_on_the_kill_switch(tmp_path):
    eng, pb, host, pid = _setup(tmp_path)
    eng.state.stage = Stage.HALTED
    eng._kill_pending = True
    _nan_feed(eng, host, 2)
    assert pid not in _open_ids(pb) and not eng._kill_pending


def test_a_time_barrier_is_judged_on_the_host_clock_while_quotes_are_invalid(tmp_path):
    eng, pb, host, pid = _setup(tmp_path)
    eng.open[pid].max_bars = 2                                          # two 15m decision bars
    host[0] = WED + pd.Timedelta(minutes=29)
    _nan_feed(eng, host, 30)
    assert pid in _open_ids(pb)                                         # 29.5 min: not yet
    _nan_feed(eng, host, 31)
    assert pid not in _open_ids(pb)
    assert any(d.get("action") == "time_exit" and d.get("position") == pid for d in eng.decisions)


def test_the_friday_flatten_closes_every_own_position_when_no_valid_quote_says_which_lose(tmp_path):
    start = FRI + pd.Timedelta(minutes=20)                              # 18:20 UTC, before the 18:30 UTC cut
    eng, pb, host, pid = _setup(tmp_path, start=start)
    _nan_feed(eng, host, 5)
    assert pid in _open_ids(pb)
    host[0] = FRI + pd.Timedelta(minutes=31)                            # past the cut on the host clock
    _nan_feed(eng, host, 2)
    assert pid not in _open_ids(pb)
    assert any(d.get("action") == "weekend_close_no_quote" for d in eng.decisions)


def test_a_last_tick_read_that_raises_still_retries_a_pending_close(tmp_path):
    eng, pb, host, pid = _setup(tmp_path)
    pb.fail = 1
    eng._try_close(pid, "time_exit", WED)

    class Dead:
        def last_tick(self, symbol: str) -> Tick:
            raise ConnectionError("bridge down")
    host[0] = WED + pd.Timedelta(seconds=2)
    assert ops_run.engine_pass(eng, Dead(), "XAUUSD") is False
    assert eng.state.tick_error and pid not in _open_ids(pb)


def test_the_state_keeps_being_written_with_quote_invalid_since_and_health_fails_after_60_s(tmp_path):
    eng, pb, host, _ = _setup(tmp_path)
    eng.on_tick(_tick(WED))
    _nan_feed(eng, host, 30)
    e = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert pd.Timestamp(e["quote_invalid_since"]) == WED + pd.Timedelta(seconds=1)
    ctx = health.HealthContext(state_dir=tmp_path, now=host[0], get_secret=lambda k: None)
    assert "quotes invalid" not in health.check_engine(ctx, "icm-demo").reason
    _nan_feed(eng, host, 35)
    e = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert time.time() - e["ts"] < 5                                    # written while every quote is invalid
    ctx = health.HealthContext(state_dir=tmp_path, now=host[0], get_secret=lambda k: None)
    c = health.check_engine(ctx, "icm-demo")
    assert c.status == "fail" and "quotes invalid" in c.reason and health._alerting(c.name, c.status)
    host[0] += pd.Timedelta(seconds=1)
    eng.on_tick(_tick(host[0]))                                         # a valid quote clears it
    eng._write_state()
    assert json.loads((tmp_path / "engine_icm-demo.json").read_text())["quote_invalid_since"] is None


def test_repeated_bad_quotes_are_one_data_quality_event_with_a_count(tmp_path):
    eng, _, host, _ = _setup(tmp_path)
    _nan_feed(eng, host, 500, step=0.25)
    evs = [e for e in eng._dq_pending if e.check == "non_finite_quote"]
    assert len(evs) == 1 and "2000 ticks" in evs[0].detail


def test_an_approval_on_a_crossed_quote_is_refused(tmp_path):
    eng, pb, host, _ = _setup(tmp_path)
    agent = SPECIALISTS["session_open"]()
    prop = Proposal(proposal_id="icm-demo-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2400.2, stop=2396.2, target=2406.2, p=0.62, ev_r=0.2, spread_points=20, top_features=[])
    eng.pending[prop.proposal_id] = (_intent(), prop, agent)
    prop.outcome = Outcome.APPROVED
    pb.on_tick(Tick(ts_utc=WED, bid=2400.5, ask=2400.0))                 # crossed
    before = len(pb.positions())
    eng._on_decision(prop)
    assert len(pb.positions()) == before
    assert prop.gate_refusal == ["bad_quote:bid_gt_ask"]


# ---------------------------------------------------------------------------------------------- MEDIUM-3: domains
def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=10_000.0, balance_closed_hwm=10_000.0, day_start_equity=10_000.0,
                                week_start_equity=10_000.0, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _gi(**kw: Any) -> Intent:
    base: dict[str, Any] = dict(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=4.0, cost_atr=0.01,
                                multiplier=1.0, price=2400.0)
    base.update(kw)
    return Intent(**base)


@pytest.mark.parametrize("field,value", [("price", 0.0), ("price", -2400.0), ("atr_usd", 0.0), ("atr_usd", -4.0),
                                         ("stop_atr", 0.0), ("contract_oz", 0.0), ("volume_step", 0.0),
                                         ("volume_min", 0.0), ("volume_max", -1.0)])
def test_an_intent_number_outside_its_domain_is_refused_by_name(field, value):
    d = RiskGate().check(_gi(**{field: value}), _state())
    assert not d.allowed and d.lots == 0.0 and "invalid_state" in d.reasons and f"invalid:{field}" in d.reasons


@pytest.mark.parametrize("equity", [0.0, -50.0])
def test_a_non_positive_equity_is_refused_and_never_moves_the_drawdown_stage(equity):
    st = _state(equity=equity)
    d = RiskGate().check(_gi(), st)
    assert "invalid:equity" in d.reasons and st.stage == Stage.NORMAL


def test_the_flag_reasons_are_kept_beside_invalid_state_in_the_journal():
    d = RiskGate().check(_gi(price=math.nan), _state(tick_error=True, owner_halt=True))
    assert d.reasons == ["owner_halt", "tick_error", "invalid_state", "invalid:price"]


# ---------------------------------------------------------------------------------------------- MEDIUM-1: health
def _sup(tmp_path: Path, **s: Any) -> health.Check:
    (tmp_path / "supervisor.json").write_text(json.dumps({"ts": time.time(), "halt": True, **s}))
    return health.check_supervisor(health.HealthContext(state_dir=tmp_path, now=pd.Timestamp.now("UTC"),
                                                        get_secret=lambda k: None))


def test_a_supervisor_error_halt_fails_health_and_is_alerted(tmp_path):
    c = _sup(tmp_path, reasons=["supervisor_error"], error="RuntimeError")
    assert c.status == "fail" and health._alerting(c.name, c.status)
    assert "RuntimeError" in c.reason and "combined-cap" not in c.reason


def test_an_unreadable_engine_halt_fails_health_naming_the_file_and_is_alerted(tmp_path):
    c = _sup(tmp_path, reasons=["engine_state_unreadable"], unreadable_engines=["engine_b.json"])
    assert c.status == "fail" and health._alerting(c.name, c.status)
    assert "engine_b.json" in c.reason and "combined-cap" not in c.reason


def test_a_combined_cap_halt_still_reads_combined_cap(tmp_path):
    c = _sup(tmp_path, reasons=["combined_daily_cap"])
    assert c.status == "warn" and "combined-cap HALT" in c.reason


@pytest.mark.parametrize("patch", [{"ts": "now"}, {"ts": None}, {"last_tick_age_s": "x"}, {"equity": "x"},
                                   {"positions_unreadable": "x"}])
def test_health_survives_a_type_corrupt_engine_file_and_fails_it(tmp_path, patch):
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps({"account": "icm-demo", "ts": time.time(), **patch}))
    ctx = health.HealthContext(state_dir=tmp_path, now=pd.Timestamp.now("UTC"), get_secret=lambda k: None,
                               accounts=[health.AccountRef(account_id="icm-demo")])
    report = health.run_checks(ctx)
    c = next(c for c in report.checks if c.name == "engine:icm-demo")
    assert c.status == "fail"


# ---------------------------------------------------------------------------------------------- LOW-2
def test_the_supervisor_traceback_is_logged_once_then_rate_limited(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(ops_run, "_SUP_ERR_LOGGED", {})
    sup = Supervisor(tmp_path)

    def boom() -> dict:
        raise RuntimeError("bug")
    sup.evaluate = boom                                                 # type: ignore[method-assign]
    hb = type("HB", (), {"beat": lambda self: None})()
    with caplog.at_level(logging.WARNING, logger="goldbot.run"):
        for _ in range(50):
            ops_run.supervisor_pass(sup, hb)
    tracebacks = [r for r in caplog.records if r.exc_info]
    assert len(tracebacks) == 1


# ---------------------------------------------------------------------------------------------- re-verification NEW-1/2
CUT = FRI + pd.Timedelta(minutes=30)                                    # Friday 21:30 server (NY+7) = 18:30 UTC


def server_date(t: pd.Timestamp) -> str:
    return server_wall(t, NY_PLUS_7)[0].date().isoformat()


def _winner_before_the_cut(tmp_path: Path) -> tuple[Any, _FlakyClose, list[pd.Timestamp], int]:
    eng, pb, host, pid = _setup(tmp_path, start=FRI + pd.Timedelta(minutes=20))   # long from 2400.2
    host[0] = CUT - pd.Timedelta(seconds=1)
    pb.on_tick(_tick(host[0], 2410.0))
    eng.on_tick(_tick(host[0], 2410.0))                                 # a valid quote just before the cut: a winner
    return eng, pb, host, pid


def test_one_crossed_quote_just_after_the_cut_never_flattens_a_winner(tmp_path):
    eng, pb, host, pid = _winner_before_the_cut(tmp_path)
    host[0] = CUT + pd.Timedelta(seconds=2)
    eng.on_tick(Tick(ts_utc=host[0], bid=2410.5, ask=2410.0))           # crossed
    assert pid in _open_ids(pb)
    assert not any(d.get("action") == "weekend_close_no_quote" for d in eng.decisions)


def test_a_rejected_tighten_then_one_nan_tick_never_flattens_a_winner(tmp_path):
    eng, pb, host, pid = _winner_before_the_cut(tmp_path)
    host[0] = CUT + pd.Timedelta(seconds=1)
    pb.on_tick(_tick(host[0], 2410.0))
    eng.on_tick(_tick(host[0], 2410.0))
    eng._refresh_account(_tick(host[0], 2410.0))                        # the account refresh runs the weekend rule
    assert any(d.get("action") == "weekend_tighten" and not d["ok"] for d in eng.decisions)   # _StopStuck refused it
    host[0] += pd.Timedelta(seconds=1)
    eng.on_tick(Tick(ts_utc=host[0], bid=math.nan, ask=math.nan))
    assert pid in _open_ids(pb)


def test_a_feed_dead_for_a_minute_after_the_cut_with_no_valid_quote_since_flattens(tmp_path):
    eng, pb, host, pid = _winner_before_the_cut(tmp_path)
    host[0] = CUT
    _nan_feed(eng, host, 30)
    assert pid in _open_ids(pb)
    _nan_feed(eng, host, 31)
    assert pid not in _open_ids(pb)


def test_the_priceless_time_barrier_needs_a_dead_feed_for_a_minute(tmp_path):
    eng, pb, host, pid = _setup(tmp_path)
    eng.open[pid].bars_held, eng.open[pid].max_bars = 5, 2              # already past its barrier
    host[0] = WED + pd.Timedelta(seconds=1)
    eng.on_tick(Tick(ts_utc=host[0], bid=math.nan, ask=math.nan))       # one bad quote: left to the next bar close
    assert pid in _open_ids(pb)
    _nan_feed(eng, host, 60)
    assert pid not in _open_ids(pb)


def test_a_winner_held_over_the_weekend_is_not_time_exited_by_a_bad_quote_at_the_reopen(tmp_path):
    eng, pb, host, pid = _setup(tmp_path, start=FRI)
    eng._weekend_done = server_date(CUT)                                # Friday's rule already ran
    tr = eng.open[pid]
    tr.bars_held, tr.max_bars = 3, 20                                   # 17 decision bars of market time left
    eng._closed_through = pd.Timestamp("2026-10-30 20:45", tz="UTC")
    host[0] = pd.Timestamp("2026-11-02 00:00", tz="UTC")                # Monday 00:00 UTC, the market reopened
    _nan_feed(eng, host, 90)                                            # 54 h of wall time, a handful of open bars
    assert pid in _open_ids(pb)


def test_the_engine_dead_feed_threshold_equals_the_health_one():
    from goldbot.engine.runner import QUOTE_DEAD_S
    assert QUOTE_DEAD_S == health.QUOTE_INVALID_FAIL_S


def test_a_refused_close_that_is_not_market_closed_is_resent_at_most_once_a_second(tmp_path):
    eng, pb, host, pid = _setup(tmp_path)
    pb.fail = 100
    eng._try_close(pid, "time_exit", WED)
    for i in range(1, 4):                                               # valid ticks every 0.25 s
        host[0] = WED + pd.Timedelta(seconds=0.25 * i)
        eng.on_tick(_tick(host[0], 2401.0 + i * 0.01))
    assert pb.calls == 1
    host[0] = WED + pd.Timedelta(seconds=1.0)
    eng.on_tick(_tick(host[0], 2401.5))
    assert pb.calls == 2
