"""ADR 0042 (part 1 and 1b): the demo limits at ~£11k, and deposits and withdrawals that are never P&L.

D1 "A deposit or withdrawal is a capital flow (MT5 DEAL_BALANCE). Never in P&L".
D3 "On a confirmed balance operation the mark becomes max(mark + net flow, balance), confirmed by the deal history
only (never by a second read alone). A loss before a deposit is not forgiven. A withdrawal lowers the mark by exactly
the withdrawn amount".
D4 "On a confirmed balance operation day_start_equity and week_start_equity move by the same net flow, so the loss so
far is neither forgiven nor created".
"""
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import Engine
from goldbot.execution.broker import AccountInfo
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits, hwm
from goldbot.risk.gate import Stage
from tests.test_hwm_confirmation import T0, TOL, _deals, _engine, _new_engine, _Terminal, _tick

DEP = 2          # MT5 DEAL_BALANCE
SELL = 1         # a closing trade deal (commission -1 in _deals)


def _balance(monkeypatch: Any, pb: PaperBroker, balance: float) -> None:
    """Every account read returns this balance (equity = balance: a flat book)."""
    real = pb.account

    def read() -> AccountInfo:
        return real().model_copy(update={"balance": balance, "equity": balance})
    monkeypatch.setattr(pb, "account", read)


def _history(monkeypatch: Any, pb: PaperBroker, *rows: tuple[pd.Timestamp, int, float]) -> list[pd.Timestamp]:
    """The broker's deal history (what MT5 returns from `since` on); the reads' `since` arguments are returned."""
    seen: list[pd.Timestamp] = []

    def deals_since(since: pd.Timestamp) -> pd.DataFrame:
        seen.append(since)
        d = _deals(*rows)
        return d[d["ts_utc"] >= since] if not d.empty else d
    monkeypatch.setattr(pb, "deals_since", deals_since)
    return seen


def _at(eng: Engine, t: pd.Timestamp) -> None:
    eng._refresh_account(_tick(t))


def _flows(eng: Engine) -> list[dict[str, Any]]:
    return [d for d in eng.decisions if d.get("action") == "capital_flow"]


M = pd.Timedelta(minutes=1)
LOSS = (T0 + 5 * M, SELL, -300.0)          # -300 profit, -1 commission: -301 on the balance


# ---------------------------------------------------------------------------------------------- I1: the demo limits
def test_both_demo_accounts_carry_the_adr_0042_limits_for_about_eleven_thousand_pounds(monkeypatch):
    monkeypatch.setattr(acc_mod, "get_secret", lambda k: None)
    accs = acc_mod.load_accounts()
    demo, probe = accs["icm-demo"], accs["icm-probe"]
    assert demo.daily_loss_limit_abs == 220 and probe.daily_loss_limit_abs == 220      # 2% of £11,000
    for a in (demo, probe):
        assert a.risk_overrides is not None
        c = a.risk_overrides.ceilings
        assert (c.daily, c.weekly, c.min_lot_risk_cap) == (0.05, 0.06, 0.01)        # weekly 10% -> 6% (a tightening)
        assert a.risk_overrides.profile == "demo-2026-10-18"                          # evidence in R is scale-free
        assert a.login is None                                                        # public repo
    cp = probe.cost_probe
    assert (cp.budget_share, cp.max_open, cp.max_loss) == (0.9, 4, 45.0)
    assert cp.max_loss <= cp.budget_share * 220 and cp.max_open * cp.max_loss <= cp.budget_share * 220


def test_the_weekly_ceiling_plus_one_daily_cap_stays_at_or_below_the_size_down_stage(monkeypatch):
    """ADR 0042: "a week can end one day past it: 6% + 2% = 8%, the size-down stage"."""
    monkeypatch.setattr(acc_mod, "get_secret", lambda k: None)
    limits = RiskLimits()
    for name in ("icm-demo", "icm-probe"):
        ceilings = acc_mod.load_accounts()[name].risk_overrides.ceilings           # type: ignore[union-attr]
        assert ceilings.weekly is not None
        assert ceilings.weekly + limits.daily_cap <= limits.dd_stage1 + 1e-12


# ---------------------------------------------------------------------------------------------- D3: the pure rule
def _conf(mark: float = 10_000.0, **kw: Any) -> hwm.HwmConfirmation:
    return hwm.HwmConfirmation(account_id="a", mark=mark, mark_utc=T0, **kw)


def _reader(*rows: tuple[pd.Timestamp, int, float]) -> Any:
    def deals(since: pd.Timestamp) -> pd.DataFrame:
        d = _deals(*rows)
        return d[d["ts_utc"] >= since]
    return deals


def test_balance_operations_are_the_capital_flows_and_credit_and_trades_are_not():
    d = _deals(LOSS, (T0 + 6 * M, DEP, 1_000.0), (T0 + 7 * M, 3, 500.0), (T0 + 8 * M, DEP, -200.0))
    flows = hwm.capital_flows(d, after=T0)
    assert [(f.ts_utc, f.amount) for f in flows] == [(T0 + 6 * M, 1_000.0), (T0 + 8 * M, -200.0)]
    assert hwm.capital_flows(d, after=T0 + 6 * M) == [hwm.CapitalFlow(ts_utc=T0 + 8 * M, amount=-200.0)]


def test_a_deposit_after_a_loss_raises_the_mark_by_the_deposit_so_the_loss_is_not_forgiven():
    rows = (LOSS, (T0 + 10 * M, DEP, 10_000.0))
    r = hwm.apply_flows(_conf(), 19_699.0, TOL, hwm.capital_flows(_deals(*rows), T0), _reader(*rows))
    assert r.applied and r.conf.mark == 20_000.0 and r.net == 10_000.0               # not the balance (19,699)
    assert r.conf.flows == 10_000.0 and r.conf.mark_utc == T0                       # the anchor is kept


def test_a_withdrawal_lowers_the_mark_by_exactly_the_withdrawn_amount():
    rows = (LOSS, (T0 + 10 * M, DEP, -2_000.0))
    r = hwm.apply_flows(_conf(), 7_699.0, TOL, hwm.capital_flows(_deals(*rows), T0), _reader(*rows))
    assert r.applied and r.conf.mark == 8_000.0


def test_a_flow_clears_an_unconfirmed_candidate():
    rows = ((T0 + 10 * M, DEP, 1_000.0),)
    conf = _conf(pending_balance=11_000.0, pending_since_utc=T0 + 10 * M, candidate_utc=T0 + 10 * M)
    r = hwm.apply_flows(conf, 11_000.0, TOL, hwm.capital_flows(_deals(*rows), T0), _reader(*rows))
    assert r.applied and r.conf.mark == 11_000.0 and r.conf.pending_balance is None


def test_a_flow_from_before_the_mark_was_set_is_already_in_it_and_is_not_added_again():
    rows = ((T0 + 10 * M, DEP, 1_000.0),)
    conf = hwm.HwmConfirmation(account_id="a", mark=11_000.0, mark_utc=T0 + 20 * M)
    r = hwm.apply_flows(conf, 11_000.0, TOL, hwm.capital_flows(_deals(*rows), T0), _reader(*rows))
    assert not r.applied and r.conf is conf


def test_a_flow_the_deal_history_does_not_reconcile_with_the_balance_is_not_applied_to_the_mark():
    """A mark that already holds the deposit (a skewed deal clock, an older release's raise) must never count it
    twice: a phantom drawdown of the deposit's size would trip the 12% kill switch."""
    rows = ((T0 + 10 * M, DEP, 1_000.0),)
    conf = _conf(mark=11_000.0)                                                     # set at T0, already 11,000
    r = hwm.apply_flows(conf, 11_000.0, TOL, hwm.capital_flows(_deals(*rows), T0), _reader(*rows))
    assert not r.applied and r.reason == "unreconciled" and r.conf is conf


def test_without_an_anchor_or_a_readable_history_a_flow_is_not_applied_to_the_mark():
    rows = ((T0 + 10 * M, DEP, 1_000.0),)
    flows = hwm.capital_flows(_deals(*rows), T0)
    bare = hwm.HwmConfirmation(account_id="a", mark=10_000.0)
    assert hwm.apply_flows(bare, 11_000.0, TOL, flows, _reader(*rows)).reason == "no_anchor"

    def broken(since: pd.Timestamp) -> pd.DataFrame:
        raise RuntimeError("history_deals_get failed")
    assert hwm.apply_flows(_conf(), 11_000.0, TOL, flows, broken).reason == "unreadable"


def test_after_a_deposit_the_deals_still_explain_a_profit_so_the_mark_is_max_of_mark_plus_deposit_and_balance():
    rows = ((T0 + 5 * M, SELL, 501.0), (T0 + 10 * M, DEP, 10_000.0))            # +500 closed, then the deposit
    r = hwm.apply_flows(_conf(), 20_500.0, TOL, hwm.capital_flows(_deals(*rows), T0), _reader(*rows))
    assert r.conf.mark == 20_000.0
    s = hwm.step(r.conf, r.conf.mark, 20_500.0, T0 + 11 * M, TOL, _reader(*rows))
    assert s.outcome == "raised" and s.mark == 20_500.0 and s.by == "deals"


# ---------------------------------------------------------------------------------------------- D3 + D4: the engine
def test_a_deposit_mid_day_moves_the_mark_and_both_starts_by_the_deposit_and_keeps_the_loss_so_far(tmp_path,
                                                                                                    monkeypatch):
    eng, pb = _engine(tmp_path)                                    # mark 10,000 at T0; risk day and week rolled at T0
    _history(monkeypatch, pb, LOSS)
    _balance(monkeypatch, pb, 9_699.0)
    _at(eng, T0 + 6 * M)
    st = eng.state
    assert (st.balance_closed_hwm, st.day_start_equity, st.week_start_equity) == (10_000, 10_000, 10_000)
    _history(monkeypatch, pb, LOSS, (T0 + 10 * M, DEP, 10_000.0))
    _balance(monkeypatch, pb, 19_699.0)
    _at(eng, T0 + 11 * M)
    assert (st.balance_closed_hwm, st.day_start_equity, st.week_start_equity) == (20_000, 20_000, 20_000)
    assert st.day_start_equity - st.equity == pytest.approx(301.0)                  # the loss so far, unchanged
    assert st.week_start_equity - st.equity == pytest.approx(301.0)
    assert Stage(st.stage) == Stage.NORMAL
    [flow] = _flows(eng)
    assert flow["kind"] == "deposit" and flow["amount"] == 10_000.0 and flow["mark_applied"] is True
    assert flow["day_start_shift"] == 10_000.0 and flow["week_start_shift"] == 10_000.0
    # the journal carries the amount, never an identifier (public repo: no login, no deal ticket)
    assert set(flow) == {"ts", "action", "kind", "amount", "deal_utc", "mark_from", "mark_to", "mark_applied",
                         "day_start_shift", "week_start_shift"}
    assert not [d for d in eng.decisions if d.get("action") == "hwm_raised"]       # a flow is not a rise


def test_after_a_mid_day_deposit_the_daily_caps_fire_at_the_same_pound_loss_as_without_it(tmp_path, monkeypatch):
    """ADR 0042 I3: "deposit mid-day then a loss -> the daily %, £ and weekly caps fire at the same £ loss as without
    the deposit" (before the fix the £10k top-up read as £10k profit until the roll)."""
    def loss_so_far(state_dir: Path, deposit: bool) -> AccountState:
        eng, pb = _engine(state_dir)
        rows = [LOSS, (T0 + 10 * M, DEP, 10_000.0)] if deposit else [LOSS]
        _history(monkeypatch, pb, *rows)
        _balance(monkeypatch, pb, 19_699.0 if deposit else 9_699.0)
        _at(eng, T0 + 11 * M)
        return eng.state
    with_dep, without = loss_so_far(tmp_path / "a", True), loss_so_far(tmp_path / "b", False)
    for st in (with_dep, without):
        assert st.day_start_equity - st.equity == pytest.approx(301.0)
        assert st.week_start_equity - st.equity == pytest.approx(301.0)
    gate = RiskGate(RiskLimits(daily_loss_limit_abs=300.0, daily_cap=0.05))      # the demo ceiling: the £ binds
    intent = Intent(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=5.0, cost_atr=0.01,
                    multiplier=0.6, price=100.0, account_currency="USD")
    for st in (with_dep, without):
        view = AccountState(equity=st.equity, balance_closed_hwm=st.balance_closed_hwm,
                            day_start_equity=st.day_start_equity, week_start_equity=st.week_start_equity,
                            open_positions=0, margin_used=0.0, last_tick_age_s=1.0, spread_points=25.0)
        assert gate.check(intent, view).reasons == ["daily_loss_limit_abs"]       # 301 lost >= 300


def test_a_withdrawal_mid_day_lowers_the_mark_and_both_starts_and_is_no_drawdown(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _history(monkeypatch, pb, (T0 + 10 * M, DEP, -2_000.0))
    _balance(monkeypatch, pb, 8_000.0)
    _at(eng, T0 + 11 * M)
    st = eng.state
    assert (st.balance_closed_hwm, st.day_start_equity, st.week_start_equity) == (8_000, 8_000, 8_000)
    assert Stage(st.stage) == Stage.NORMAL                                          # not a 20% drawdown
    assert _flows(eng)[0]["kind"] == "withdrawal"


def test_a_deposit_while_the_market_was_closed_is_in_the_rolled_starts_and_moves_only_the_mark(tmp_path, monkeypatch):
    """The 2026-10-25 case: the top-up landed with the market closed and the first tick rolls the risk day and week to
    post-deposit equity, so the starts must not move again; the mark still rises by the deposit (D3)."""
    eng, pb = _engine(tmp_path)                                    # Wednesday T0
    saturday, sunday_open = pd.Timestamp("2025-03-08 12:00", tz="UTC"), pd.Timestamp("2025-03-09 22:00", tz="UTC")
    _history(monkeypatch, pb, LOSS, (saturday, DEP, 10_000.0))
    _balance(monkeypatch, pb, 19_699.0)
    _at(eng, sunday_open)
    st = eng.state
    assert (st.day_start_equity, st.week_start_equity) == (19_699, 19_699)         # the roll; no shift on top
    assert st.balance_closed_hwm == 20_000                                          # 10,000 + deposit, loss carried
    [flow] = _flows(eng)
    assert flow["day_start_shift"] == 0.0 and flow["week_start_shift"] == 0.0


def test_a_flow_between_the_week_roll_and_the_day_roll_moves_only_the_week_start(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)                                    # day and week rolled at T0 (Wednesday)
    thursday = pd.Timestamp("2025-03-06 00:00", tz="UTC")
    eng._last_reset = thursday                                     # the engine was down over the deposit; the day
    eng.state.day_start_equity = 20_000.0                          # roll then took the post-deposit equity
    _history(monkeypatch, pb, (T0 + 60 * M, DEP, 10_000.0))
    _balance(monkeypatch, pb, 20_000.0)
    _at(eng, thursday + 30 * M)
    st = eng.state
    assert (st.day_start_equity, st.week_start_equity, st.balance_closed_hwm) == (20_000, 20_000, 20_000)


def test_without_a_recorded_week_roll_a_flow_before_the_day_roll_leaves_the_week_start(tmp_path, monkeypatch):
    """An older risk file has no week roll time: the day roll is taken as the week's (never a double shift, which
    would block a deposit's whole week of entries)."""
    eng, pb = _engine(tmp_path)
    thursday = pd.Timestamp("2025-03-06 00:00", tz="UTC")
    eng._last_reset, eng._week_reset = thursday, None
    eng.state.day_start_equity = eng.state.week_start_equity = 20_000.0
    _history(monkeypatch, pb, (T0 + 60 * M, DEP, 10_000.0))
    _balance(monkeypatch, pb, 20_000.0)
    _at(eng, thursday + 30 * M)
    assert (eng.state.day_start_equity, eng.state.week_start_equity) == (20_000, 20_000)


def test_a_flow_is_applied_once_across_later_reads_and_trade_closes(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    dep = (T0 + 10 * M, DEP, 10_000.0)
    _history(monkeypatch, pb, dep)
    _balance(monkeypatch, pb, 20_000.0)
    _at(eng, T0 + 11 * M)
    _at(eng, T0 + 12 * M)
    _history(monkeypatch, pb, dep, (T0 + 13 * M, SELL, 101.0))                    # a winning close: +100
    _balance(monkeypatch, pb, 20_100.0)
    _at(eng, T0 + 14 * M)
    st = eng.state
    assert len(_flows(eng)) == 1
    assert (st.day_start_equity, st.week_start_equity, st.balance_closed_hwm) == (20_000, 20_000, 20_100)


def test_an_unreadable_deal_history_applies_nothing_and_the_flow_is_applied_once_it_reads(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)

    def broken(since: pd.Timestamp) -> pd.DataFrame:
        raise RuntimeError("history_deals_get failed")
    monkeypatch.setattr(pb, "deals_since", broken)
    _balance(monkeypatch, pb, 20_000.0)
    _at(eng, T0 + 11 * M)                                                           # never raises
    assert (eng.state.balance_closed_hwm, eng.state.day_start_equity) == (10_000, 10_000)
    _history(monkeypatch, pb, (T0 + 10 * M, DEP, 10_000.0))
    _at(eng, T0 + 12 * M)                                  # the candidate above the mark keeps the history being read
    assert (eng.state.balance_closed_hwm, eng.state.day_start_equity) == (20_000, 20_000)


def test_the_flow_anchor_and_the_week_roll_survive_a_restart_so_a_flow_is_never_applied_twice(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    dep = (T0 + 10 * M, DEP, 10_000.0)
    _history(monkeypatch, pb, dep)
    _balance(monkeypatch, pb, 20_000.0)
    _at(eng, T0 + 11 * M)
    risk = json.loads(eng._risk_path().read_text())
    assert pd.Timestamp(risk["flows_through"]) == T0 + 10 * M and pd.Timestamp(risk["week_reset"]) == T0
    again = _new_engine(tmp_path, pb)
    _at(again, T0 + 12 * M)
    st = again.state
    assert (st.balance_closed_hwm, st.day_start_equity, st.week_start_equity) == (20_000, 20_000, 20_000)
    assert not _flows(again)


def test_the_paper_broker_has_no_capital_flows_and_its_deal_history_is_not_read_for_them(tmp_path, monkeypatch):
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(T0))
    eng = _new_engine(tmp_path, pb)
    eng._refresh_account(_tick(T0))
    seen = _history(monkeypatch, pb)
    _at(eng, T0 + M)
    assert seen == [] and not _flows(eng)
    assert isinstance(pb, PaperBroker) and not isinstance(pb, _Terminal)
