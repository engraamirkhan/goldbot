"""Design rules of the October batch that had no test, or only an indirect one (each test names the rule).

RiskGate flags one by one and at their boundaries, the absolute daily limit's corners, the currency conversion's
fail-closed corners, the supervisor's money split, the approval re-check reading fresh state, exits-first ordering in
on_tick, and the auth lockout's reserve/release bookkeeping."""
import json
import math
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import goldbot.api.auth as auth_mod
from goldbot.api.auth import LOCKOUT_FAILURES, AuthStore
from goldbot.engine import Engine
from goldbot.ops.run import engine_pass
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.supervisor import Supervisor, SupervisorLimits
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import Outcome, Proposal
from goldbot.telegram.outbox import Outbox
from tests.test_engine_clock import WED, _engine, _intent, _open, _tick

GBP = 1 / 1.33


def _state(equity: float = 10_000.0, day0: float | None = None, **kw: Any) -> AccountState:
    d0 = equity if day0 is None else day0
    base: dict[str, Any] = dict(equity=equity, balance_closed_hwm=max(equity, d0), day_start_equity=d0,
                                week_start_equity=d0, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _usd(stop: float = 4.0, mult: float = 1.0, **kw: Any) -> Intent:
    base: dict[str, Any] = dict(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=stop, cost_atr=0.01,
                                multiplier=mult, price=100.0, account_currency="USD")
    base.update(kw)
    return Intent(**base)


# ---------------------------------------------------------------------------------------------- RiskGate state flags
@pytest.mark.parametrize("flag,reason", [({"market_closed": True}, "market_closed"),
                                         ({"clock_mismatch": True}, "server_clock_mismatch"),
                                         ({"tick_error": True}, "tick_error"),
                                         ({"tick_ahead_s": 10.5}, "tick_from_future")])
def test_each_new_state_flag_refuses_an_entry_on_its_own_with_its_own_reason(flag, reason):
    gate = RiskGate()
    assert gate.check(_usd(), _state()).allowed                                  # the baseline entry is fine
    d = gate.check(_usd(), _state(**flag))
    assert not d.allowed and d.reasons == [reason] and d.lots == 0.0


def test_the_flags_stack_and_each_is_named():
    d = RiskGate().check(_usd(), _state(market_closed=True, clock_mismatch=True, tick_error=True, tick_ahead_s=99.0))
    assert set(d.reasons) == {"market_closed", "server_clock_mismatch", "tick_error", "tick_from_future"}


def test_a_tick_exactly_at_the_future_limit_is_allowed_and_one_float_step_past_it_is_refused():
    lim = RiskLimits().max_tick_ahead_s
    gate = RiskGate()
    assert gate.check(_usd(), _state(tick_ahead_s=lim)).allowed                   # `>` not `>=`
    assert gate.check(_usd(), _state(tick_ahead_s=math.nextafter(lim, math.inf))).reasons == ["tick_from_future"]


def test_the_stale_tick_limit_is_inclusive_of_its_boundary():
    lim = RiskLimits().stale_tick_seconds
    gate = RiskGate()
    assert gate.check(_usd(), _state(last_tick_age_s=float(lim))).allowed
    assert gate.check(_usd(), _state(last_tick_age_s=lim + 0.001)).reasons == ["stale_data"]


# ---------------------------------------------------------------------------------------------- time-exit EV
def test_time_exit_family_is_found_from_the_agent_id_unknown_families_pass_and_a_broken_one_fails_closed(monkeypatch):
    gate = RiskGate()
    assert gate.check(_usd(agent_id="no_such_family-g1-abc123"), _state()).allowed     # an unknown family is not judged
    ad = _usd(agent_id="asia_drift-g1-abc123", p=0.99, target_atr=3.0)                 # family derived from the id alone
    assert "time_exit_ev_unsupported" in gate.check(ad, _state()).reasons
    monkeypatch.setitem(SPECIALISTS, "broken", type("Broken", (), {"label_spec": property(lambda s: 1 / 0)}))
    assert "time_exit_ev_unsupported" in gate.check(_usd(family="broken"), _state()).reasons   # cannot build: closed


def test_time_exit_ev_unsupported_returns_no_lots_and_does_not_hide_the_other_reasons():
    d = RiskGate().check(_usd(has_target=False), _state(tick_error=True))
    assert not d.allowed and d.lots == 0.0
    assert d.reasons == ["tick_error", "time_exit_ev_unsupported"]


# ---------------------------------------------------------------------------------------------- absolute daily limit
def test_a_day_in_profit_counts_no_loss_but_the_trades_own_risk_can_still_exceed_the_limit():
    gate = RiskGate(RiskLimits(daily_loss_limit_abs=30.0))
    up = _state(10_500.0, 10_000.0)                                               # +500 today: loss so far is 0, not -500
    big = gate.check(_usd(4.0, 1.0), up)                                          # 0.005 x 10,500 = 52.5 at the stop
    assert not big.allowed and big.reasons == ["daily_loss_limit_abs"]
    small = gate.check(_usd(4.0, 0.5), up)                                        # about 26 <= 30 although +500 on the day
    assert small.allowed and small.risk_amount <= 30.0


def test_without_a_day_start_equity_the_absolute_limit_alone_applies():
    gate = RiskGate(RiskLimits(daily_loss_limit_abs=30.0))
    st = _state(10_000.0, 0.0)
    assert gate.daily_loss_limit(st) == 30.0
    assert gate.check(_usd(4.0, 1.0), st).reasons == ["daily_loss_limit_abs"]      # 50 at the stop > 30
    assert RiskGate().daily_loss_limit(st) == math.inf                            # neither known: unlimited, never 0


def test_the_minimum_lot_exception_is_still_bound_by_the_absolute_limit_and_leaves_no_note_when_refused():
    lim = RiskLimits(risk_per_trade=0.005, min_lot_risk_cap=0.01, daily_loss_limit_abs=5.0)
    st = _state(1_000.0)
    cur: dict[str, Any] = {"account_currency": "GBP", "acct_per_usd": GBP}
    d = RiskGate(lim).check(_usd(10.90, 1.0, price=4_150.0, **cur), st)           # min lot risks about 8.2 > 5
    assert not d.allowed and d.reasons == ["daily_loss_limit_abs"]
    ok = RiskGate(lim.model_copy(update={"daily_loss_limit_abs": 9.0})).check(_usd(10.90, 1.0, price=4_150.0, **cur), st)
    assert ok.allowed and ok.reasons == ["min_lot_exception"]


def test_a_trade_that_exactly_fills_the_remaining_budget_is_allowed_and_a_cent_over_is_refused():
    base: dict[str, Any] = dict(risk_per_trade=0.004, multiplier_bounds=(1.0, 1.0))
    st = _state(9_990.0, 10_000.0)                                                # 10 lost so far
    off = RiskGate(RiskLimits(**base)).check(_usd(4.0, 1.0), st)                  # limit off
    ra = off.risk_amount + off.cost_amount     # the trade's stop risk and round-trip cost (BACKLOG 36)
    assert off.risk_amount > 0 and off.cost_amount > 0
    ok = RiskGate(RiskLimits(daily_loss_limit_abs=10.0 + ra, **base)).check(_usd(4.0, 1.0), _state(9_990.0, 10_000.0))
    assert ok.allowed                                                             # projection == limit: allowed
    over = RiskGate(RiskLimits(daily_loss_limit_abs=10.0 + ra - 0.01, **base)).check(_usd(4.0, 1.0), _state(9_990.0, 10_000.0))
    assert over.reasons == ["daily_loss_limit_abs"]                               # one cent short: refused


# ---------------------------------------------------------------------------------------------- currency conversion
@pytest.mark.parametrize("fx,reason", [(math.nan, "invalid_state"), (math.inf, "invalid_state"),
                                       (-math.inf, "invalid_state"), (0.0, "fx_conversion_unavailable"),
                                       (-0.75, "fx_conversion_unavailable")])
def test_a_gbp_account_with_a_junk_conversion_refuses_with_nothing_sized(fx, reason):
    d = RiskGate().check(_usd(account_currency="GBP", acct_per_usd=fx), _state())
    assert not d.allowed and d.reasons[0] == reason and d.lots == 0.0       # non-finite: refused before any check


@pytest.mark.parametrize("cur,fx,allowed", [(" usd ", None, True), ("usd", 1.0, True), ("USD", None, True), ("USD", 1.0099, True),
                                            ("USD", 1.0101, False), ("", None, False)])
def test_a_usd_account_is_one_by_definition_and_a_blank_currency_is_never_assumed_usd(cur, fx, allowed):
    d = RiskGate().check(_usd(account_currency=cur, acct_per_usd=fx), _state())
    assert d.allowed is allowed
    if not allowed:
        assert d.reasons == ["fx_conversion_unavailable"]


def test_a_usd_account_with_a_nan_broker_conversion_is_refused_not_assumed_one():
    # the engine maps a junk broker figure to None (runner._usd_to_account); a NaN reaching the gate is a bug upstream
    d = RiskGate().check(_usd(account_currency="USD", acct_per_usd=math.nan), _state())
    assert not d.allowed and d.reasons == ["invalid_state", "invalid:acct_per_usd"]


def test_the_conversion_check_never_masks_a_flag_reason():
    d = RiskGate().check(_usd(account_currency="GBP", acct_per_usd=None), _state(tick_error=True))
    assert d.reasons == ["tick_error"]                                            # flags are reported first, sizing never ran


# ---------------------------------------------------------------------------------------------- supervisor money split
def _eng(d: Path, account: str, equity: float, day0: float, currency: str | None = "GBP", ts: float | None = None,
         **extra: Any) -> None:
    body: dict[str, Any] = {"account": account, "ts": time.time() if ts is None else ts, "equity": equity,
                            "day_start_equity": day0, "week_start_equity": day0, "balance_closed_hwm": day0, **extra}
    if currency is not None:
        body["currency"] = currency
    (d / f"engine_{account}.json").write_text(json.dumps(body))


def test_the_absolute_limit_halts_at_the_limit_and_not_a_cent_before(tmp_path):
    lim = SupervisorLimits(daily_loss_limit_abs=50.0, daily_cap=0.5)               # the percentage cap is out of the way
    _eng(tmp_path, "a", 4_950.0, 5_000.0)                                          # exactly 50 lost
    assert Supervisor(tmp_path, lim).evaluate()["reasons"] == ["combined_daily_loss_abs"]
    _eng(tmp_path, "a", 4_950.01, 5_000.0)
    assert not Supervisor(tmp_path, lim).evaluate()["halt"]


def test_the_smallest_published_limit_wins_over_a_larger_explicit_one(tmp_path):
    _eng(tmp_path, "a", 4_970.0, 5_000.0, daily_loss_limit_abs=30.0)
    s = Supervisor(tmp_path, SupervisorLimits(daily_loss_limit_abs=100.0, daily_cap=0.5)).evaluate()
    assert s["daily_loss_limit_abs"] == 30.0 and "combined_daily_loss_abs" in s["reasons"]


def test_a_stale_engine_in_the_same_currency_counts_its_money_but_a_stale_other_currency_never_does(tmp_path):
    old = time.time() - 3_600
    _eng(tmp_path, "fresh", 900.0, 1_000.0)
    _eng(tmp_path, "stale-gbp", 1_000.0, 1_000.0, ts=old)
    _eng(tmp_path, "stale-usd", 50_000.0, 50_000.0, "USD", ts=old)
    s = Supervisor(tmp_path).evaluate()
    assert s["combined_equity"] == pytest.approx(1_900.0) and s["money_excluded"] == ["engine_stale-usd.json"]
    assert "mixed_account_currencies" not in s["reasons"]                          # one fresh engine mixes nothing
    assert s["exposure"]["stale-usd"]["equity"] == 0.0                             # and adds no equity to the exposure cap


def test_an_excluded_engines_open_lots_stay_in_the_exposure(tmp_path):
    old = time.time() - 3_600
    _eng(tmp_path, "fresh", 1_000.0, 1_000.0, open_lots=0.5, open_notional=100.0)
    _eng(tmp_path, "stale-usd", 50_000.0, 50_000.0, "USD", ts=old, open_lots=1.0, open_notional=2_000.0)
    s = Supervisor(tmp_path).evaluate()
    assert s["combined_open_lots"] == pytest.approx(1.5) and s["combined_open_notional"] == pytest.approx(2_100.0)


def test_two_fresh_engines_in_two_currencies_halt_even_when_each_is_healthy(tmp_path):
    _eng(tmp_path, "a", 1_000.0, 1_000.0, "GBP")
    _eng(tmp_path, "b", 1_000.0, 1_000.0, "USD")
    s = Supervisor(tmp_path).evaluate()
    assert s["halt"] and s["reasons"] == ["mixed_account_currencies"]
    assert Supervisor.engine_should_halt(tmp_path) == (True, "mixed_account_currencies")


# ---------------------------------------------------------------------------------------------- approval re-check
def _pending(eng: Engine, pid: str = "icm-demo-r") -> Proposal:
    agent = SPECIALISTS["session_open"]()
    prop = Proposal(proposal_id=pid, account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.12,
                    entry=2400.2, stop=2396.2, target=2406.2, p=0.62, ev_r=0.2, spread_points=20, top_features=[])
    eng.pending[pid] = (_intent(), prop, agent)
    return prop


def _approve(eng: Engine, prop: Proposal) -> list[str]:
    prop.outcome = Outcome.APPROVED
    eng._on_decision(prop)
    return [str(d["action"]) for d in eng.decisions if str(d.get("action", "")).startswith("gate_at_approval")]


def test_the_approval_re_check_sees_a_loss_taken_since_the_proposal_daily_cap(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    eng._refresh_account(_tick(WED))                                              # the day starts at 10,000
    assert eng.gate.check(_intent(), eng.state).allowed
    prop = _pending(eng)
    pb._balance = 9_790.0                                                         # -2.1% before the owner clicks
    assert _approve(eng, prop) == ["gate_at_approval:daily_cap"] and not pb.positions()


def test_the_approval_re_check_applies_the_absolute_limit_to_the_loss_taken_since(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    eng.gate = RiskGate(RiskLimits(daily_loss_limit_abs=60.0))
    eng._refresh_account(_tick(WED))
    assert eng.gate.check(_intent(), eng.state).allowed                           # 48 at the stop <= 60
    prop = _pending(eng)
    pb._balance = 9_980.0                                                         # 20 lost (0.2%): 20 + 48 > 60
    assert _approve(eng, prop) == ["gate_at_approval:daily_loss_limit_abs"] and not pb.positions()


def test_the_approval_re_check_refuses_a_feed_that_ran_ahead_of_the_host_clock(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    eng._refresh_account(_tick(WED))
    prop = _pending(eng)
    pb.on_tick(_tick(WED + pd.Timedelta(seconds=30)))                             # host clock still at WED
    out = _approve(eng, prop)
    assert out and "tick_from_future" in out[0] and not pb.positions()


def test_the_approval_re_check_refuses_when_the_supervisor_halted_since_and_when_a_tick_error_is_open(tmp_path):
    eng, pb, _ = _engine(tmp_path, halt_checks=True)
    sup = tmp_path / "supervisor.json"
    sup.write_text(json.dumps({"ts": time.time(), "halt": False, "reasons": []}))
    eng._refresh_account(_tick(WED))
    assert eng.gate.check(_intent(), eng.state).allowed
    prop = _pending(eng)
    sup.write_text(json.dumps({"ts": time.time(), "halt": True, "reasons": ["combined_daily_cap"]}))
    out = _approve(eng, prop)
    assert out and "supervisor_halt" in out[0] and not pb.positions()
    sup.write_text(json.dumps({"ts": time.time(), "halt": False, "reasons": []}))
    prop2 = _pending(eng, "icm-demo-r2")
    eng.note_tick_error(RuntimeError("boom"))
    out = _approve(eng, prop2)
    assert out and "tick_error" in out[-1] and not pb.positions()


# ---------------------------------------------------------------------------------------------- exits first
def test_exits_run_before_anything_that_logs_reads_the_account_or_touches_state(tmp_path):
    eng, pb, host = _engine(tmp_path)
    order: list[str] = []
    for name in ("_stop_check", "_retry_closes", "_scale_out", "_log_tick", "_refresh_broker_terms",
                 "_roll_risk_period", "_reconcile", "_refresh_account", "_write_state", "_flush_closed"):
        real = getattr(eng, name)

        def spy(*a: Any, _n: str = name, _r: Any = real, **k: Any) -> Any:
            order.append(_n)
            return _r(*a, **k)
        setattr(eng, name, spy)
    eng.on_tick(_tick(WED + pd.Timedelta(seconds=1)))
    after_exits = ("_log_tick", "_refresh_broker_terms", "_roll_risk_period", "_reconcile", "_write_state",
                   "_refresh_account")
    assert order[:3] == ["_stop_check", "_retry_closes", "_scale_out"]
    assert "_log_tick" in order and "_roll_risk_period" in order
    assert all(order.index(n) > 2 for n in after_exits if n in order)
    assert order[-1] == "_flush_closed"                                           # closes recorded after every exit


def test_an_engine_stop_closes_the_position_even_when_every_other_step_of_the_tick_raises(tmp_path):
    eng, pb, host = _engine(tmp_path)
    pid = _open(pb, 1, 2300.0)                                                    # the BROKER's stop is far away
    tr = eng._adopt(next(p for p in pb.positions() if p.position_id == pid))
    tr.sl = 2399.0                                                                # the engine's own stop is tight
    eng.open[pid] = tr

    def boom(*a: Any, **k: Any) -> None:
        raise OSError("disk full")
    eng.center.poll_bus = boom                                                    # type: ignore[method-assign,assignment]
    for name in ("_poll_rearm", "_log_tick", "_roll_risk_period"):
        setattr(eng, name, boom)
    host[0] = WED + pd.Timedelta(seconds=1)
    pb.on_tick(_tick(host[0], 2398.0))                                            # bid through the engine's stop only
    assert pid in {p.position_id for p in pb.positions()}                         # the broker's own stop did not fire
    engine_pass(eng, pb, "XAUUSD")                                                # the run loop's pass: logs, never raises
    assert pid not in {p.position_id for p in pb.positions()}                     # the broker never held it: engine closed
    assert eng.state.tick_error


# ---------------------------------------------------------------------------------------------- auth lockout
OWNER = "o@x.io"
PW = "owner password 123"


@pytest.fixture
def store(tmp_path):
    s = AuthStore(tmp_path, owner_email=OWNER)
    e = s.bootstrap_owner(s.setup_code or "", OWNER, PW)
    return s, e.totp_uri.split("secret=")[1].split("&")[0]


def _rows(s: AuthStore, key: str) -> int:
    with s.db.connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM auth_failures WHERE key = ?", (key,)).fetchone()[0])


def test_reserve_counts_the_attempt_up_front_and_a_locked_attempt_adds_nothing(store):
    s, _ = store
    for i in range(LOCKOUT_FAILURES):
        r = s._reserve(OWNER, "login_locked", "203.0.113.1")
        assert (r.prev, r.new) == (i, i + 1)
    assert _rows(s, f"email:{OWNER}") == LOCKOUT_FAILURES
    for _ in range(3):
        with pytest.raises(PermissionError):
            s._reserve(OWNER, "login_locked", "203.0.113.1")
    assert _rows(s, f"email:{OWNER}") == LOCKOUT_FAILURES and _rows(s, "ip:203.0.113.1") == LOCKOUT_FAILURES


def test_release_clears_the_email_counter_and_its_own_ip_row_but_not_other_failures_of_that_ip(store):
    s, _ = store
    s._reserve("other@x.io", "login_locked", "203.0.113.1")                       # someone else's failure, same IP
    for _ in range(LOCKOUT_FAILURES - 1):
        s._reserve(OWNER, "login_locked", "203.0.113.1")
    r = s._reserve(OWNER, "login_locked", "203.0.113.1")
    with s.db.write() as conn:
        s._release(conn, r)
    assert _rows(s, f"email:{OWNER}") == 0
    assert _rows(s, "ip:203.0.113.1") == LOCKOUT_FAILURES                         # 1 other + 4 earlier owner failures; the 5th went
    assert s._reserve(OWNER, "login_locked", "203.0.113.1").prev == 0              # a fresh budget after the success


def test_many_successes_from_one_ip_never_reach_the_ip_lockout(store):
    s, _ = store
    for _ in range(auth_mod.IP_LOCKOUT_FAILURES * 2):
        r = s._reserve(OWNER, "login_locked", "198.51.100.5")
        with s.db.write() as conn:
            s._release(conn, r)
    assert _rows(s, "ip:198.51.100.5") == 0
    s._reserve(OWNER, "login_locked", "198.51.100.5")


def test_a_failure_exactly_one_window_old_has_expired_and_one_a_second_younger_still_counts(store, monkeypatch):
    s, _ = store
    base = 1_800_000_000 * 10**9
    monkeypatch.setattr(auth_mod.time, "time_ns", lambda: base)
    for _ in range(LOCKOUT_FAILURES):
        s._reserve(OWNER, "login_locked")
    window = auth_mod.LOCKOUT_WINDOW_S * 10**9
    monkeypatch.setattr(auth_mod.time, "time_ns", lambda: base + window - 10**9)
    with pytest.raises(PermissionError):
        s._reserve(OWNER, "login_locked")                                         # one second inside the window: locked
    monkeypatch.setattr(auth_mod.time, "time_ns", lambda: base + window)
    assert s._reserve(OWNER, "login_locked").prev == 0                            # `<=`: a failure of exactly 15 min is gone


def test_a_right_login_resets_the_budget_so_four_failures_do_not_carry_over(store):
    s, secret = store
    for _ in range(LOCKOUT_FAILURES - 1):
        with pytest.raises(PermissionError):
            s.login(OWNER, "wrong password!!", "000000")
    s.login(OWNER, PW, auth_mod.totp_code(secret))
    for _ in range(LOCKOUT_FAILURES - 1):                                         # four more are again not a lockout
        with pytest.raises(PermissionError, match="invalid credentials"):
            s.login(OWNER, "wrong password!!", "000000")
    assert _rows(s, f"email:{OWNER}") == LOCKOUT_FAILURES - 1


def test_forgot_password_with_the_current_password_is_not_a_failure_and_never_locks(store):
    s, _ = store
    _, uri = s.accept_invite(s.create_invite(OWNER, "v@x.io", "viewer"), "viewer password 123")
    secret = uri.split("secret=")[1].split("&")[0]
    for _ in range(LOCKOUT_FAILURES * 2):                                         # same password: refused, not counted
        with pytest.raises(ValueError, match="differ"):
            s.forgot_password("v@x.io", auth_mod.totp_code(secret), "viewer password 123")
    assert _rows(s, "email:v@x.io") == 0
    s.forgot_password("v@x.io", auth_mod.totp_code(secret), "a different viewer password")   # still unlocked


# ---------------------------------------------------------------------------------------------- state store step 3
@pytest.mark.parametrize("garbage", [b"", b"{", b"\x00\xff\xfe", b'{"sent": {"p": "notalist"}}',
                                     b'{"sent": [], "reports_offset": "x"}', b"[]", b"null"])
def test_a_corrupt_old_format_outbox_file_after_migration_never_forgets_a_sent_proposal_or_stops_the_service(
        tmp_path, garbage):
    """A rolled-back release that leaves garbage in telegram_outbox.json must not make the next start crash or
    forget (and so re-send) a proposal: the dual-read merge fails safe and keeps aux.db's rows."""
    box = Outbox(tmp_path)
    box.mark_sent("p1", [(42, 7)])
    (tmp_path / "telegram_outbox.json").write_bytes(garbage)
    again = Outbox(tmp_path)
    assert again.sent == {"p1": [(42, 7)]}
    again.mark_sent("p2", [(42, 8)])                                            # and it keeps working
    assert set(Outbox(tmp_path).sent) == {"p1", "p2"}
