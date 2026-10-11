"""Performance report for the performance-analyst and finance-auditor agents (goldbot/ops/perf_report.py; ADR 0004
section 6, docs/research/opportunity-days.md 3.4). Synthetic state only: every section, the small-sample flags, the
missing-field handling, read-only access to state and no identifiers in the output."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data.store import Store
from goldbot.engine.shadow import ShadowTrade, VersionBook
from goldbot.execution.costs import CostTable, SlippageStat, SpreadStat
from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
from goldbot.ops.perf_report import MIN_TRADES, build_performance_report, format_markdown, main
from goldbot.telegram.approvals import Outcome, Proposal

ACC = "icm-demo"
SO = "session_open-g0-0123456789"          # session_open: fixed 15m
BO = "breakout-g1-abcdef0123"              # breakout: 1h by default, 15m allowed -> timeframe from population.json
START, END = "2026-10-05", "2026-10-09"    # Mon..Fri (BST: London 08:00 = 07:00 UTC, New York 08:30 = 12:30 UTC)
NOW = pd.Timestamp("2026-10-10 06:00", tz="UTC")
TELEGRAM_ID = 987654321
EMAIL = "owner@example.org"


def _ct(pid: int, exit_utc: str, pnl: float, r: float | None, *, agent: str | None = SO, side: int | None = 1,
        entry_utc: str | None = None, commission: float | None = 0.5, swap: float | None = 0.0,
        equity_before: float = 1000.0, lots: float | None = 0.01, account: str = ACC) -> ClosedTrade:
    ex = pd.Timestamp(exit_utc, tz="UTC")
    en = pd.Timestamp(entry_utc, tz="UTC") if entry_utc else (ex - pd.Timedelta(hours=1))
    return ClosedTrade(account_id=account, broker="ICMarketsSC-Demo-77001234", mode="demo", exit_utc=ex, ret=0.0,
                       pnl=pnl, equity_before=equity_before, position_id=pid, client_order_id=f"coid-secret-{pid}",
                       agent_id=agent, side=side, lots=lots, entry_utc=en,
                       entry_price=2000.0, exit_price=2001.0, r=r, commission=commission, swap=swap,
                       pnl_currency="GBP")


def _engine(state: Path, currency: str = "GBP", **extra) -> None:
    e = {"account": ACC, "broker": "ICMarketsSC-Demo-77001234", "currency": currency, "equity": 990.0,
         "balance_closed_hwm": 1030.0, "stage": "normal",
         "limits": {"daily_cap": 0.02, "weekly_cap": 0.05, "daily_loss_limit_abs": 50.0}}
    e.update(extra)
    (state / f"engine_{ACC}.json").write_text(json.dumps(e))


def _base_trades(state: Path) -> None:
    """Five trades over the week: London and New York session_open longs, an Asia breakout short."""
    for t in [
        _ct(1, "2026-10-05 08:00", 16.0, 2.0, entry_utc="2026-10-05 07:15"),            # london, long
        _ct(2, "2026-10-05 14:00", -8.0, -1.0, entry_utc="2026-10-05 13:00"),           # newyork, long
        _ct(3, "2026-10-06 03:00", 8.0, 1.0, agent=BO, side=-1, entry_utc="2026-10-06 01:00"),   # asia, short
        _ct(4, "2026-10-07 09:00", -8.0, -1.0, entry_utc="2026-10-07 07:30"),           # london, long
        _ct(5, "2026-10-04 12:00", 99.0, 9.0, entry_utc="2026-10-04 11:00"),            # Sunday: before the range
    ]:
        append_closed_trade(state, t)
    (state / "population.json").write_text(json.dumps([
        {"agent_id": BO, "family": "breakout", "config": {"timeframe": "15m"}, "generation": 1,
         "created_utc": "2026-09-01T00:00:00Z", "status_since_utc": "2026-09-01T00:00:00Z"}]))


def _groups(acc, dim: str) -> dict:
    return {g.key: g for g in acc.by[dim]}


# ---------------------------------------------------------------------------------------------- trades by dimension
def test_net_and_gross_r_and_money_per_trade_and_per_day_by_every_dimension(tmp_path):
    _engine(tmp_path)
    _base_trades(tmp_path)
    rep = build_performance_report(tmp_path, start=START, end=END, now=NOW)
    [a] = rep.accounts
    assert a.account_id == ACC and a.currency == "GBP" and a.mode == "demo"
    o = a.overall
    assert o.trades == 4 and rep.weekdays == 5                     # the Sunday trade is outside the range
    assert o.net_pnl == pytest.approx(8.0) and o.net_r == pytest.approx(1.0)
    # gross = net + commission - swap (spread and slippage are inside the fill prices)
    assert o.gross_pnl == pytest.approx(10.0) and o.gross_trades == 4
    # gross R = gross / initial risk, the risk being net pnl / net R (8 per R here)
    assert o.gross_r == pytest.approx(1.0 + 4 * 0.5 / 8.0)
    assert o.mean_pnl == pytest.approx(2.0) and o.mean_r == pytest.approx(0.25)
    assert o.pnl_per_day == pytest.approx(8.0 / 5) and o.r_per_day == pytest.approx(1.0 / 5)
    fam = _groups(a, "family")
    assert set(fam) == {"session_open", "breakout"}
    assert fam["session_open"].trades == 3 and fam["session_open"].net_r == pytest.approx(0.0)
    assert fam["breakout"].net_pnl == pytest.approx(8.0)
    assert set(_groups(a, "agent")) == {SO, BO}
    tf = _groups(a, "timeframe")
    assert tf["15m"].trades == 4                                 # session_open fixed 15m; breakout's clone is 15m
    sess = _groups(a, "session")
    assert {k: g.trades for k, g in sess.items()} == {"london": 2, "newyork": 1, "asia": 1}
    side = _groups(a, "side")
    assert side["long"].trades == 3 and side["short"].trades == 1
    assert _groups(a, "day_type") == {"not recorded": _groups(a, "day_type")["not recorded"]}
    md = format_markdown(rep)
    for heading in ("By family", "By agent", "By timeframe", "By session", "By side", "By day type"):
        assert heading in md
    assert "asia (Tokyo)" in md and "newyork (New York)" in md


def test_a_group_under_thirty_trades_is_flagged_and_draws_no_conclusion(tmp_path):
    _engine(tmp_path)
    _base_trades(tmp_path)
    [a] = build_performance_report(tmp_path, start=START, end=END, now=NOW).accounts
    for g in [a.overall, *a.by["family"]]:
        assert g.sufficient is False and g.r_ci90 is None
        assert g.verdict.startswith("insufficient sample")
    md = format_markdown(build_performance_report(tmp_path, start=START, end=END, now=NOW))
    assert f"fewer than {MIN_TRADES} trades" in md


def test_thirty_trades_get_an_interval_and_a_reading(tmp_path):
    _engine(tmp_path)
    for i in range(MIN_TRADES):
        day = pd.Timestamp("2026-10-05") + pd.Timedelta(days=i % 5)
        append_closed_trade(tmp_path, _ct(100 + i, f"{day:%Y-%m-%d} 10:00", 8.0 if i % 3 else -8.0,
                                          1.0 if i % 3 else -1.0, entry_utc=f"{day:%Y-%m-%d} 09:00"))
    [a] = build_performance_report(tmp_path, start=START, end=END, now=NOW).accounts
    o = a.overall
    assert o.trades == MIN_TRADES and o.sufficient is True and o.r_ci90 is not None and o.mean_r is not None
    lo, hi = o.r_ci90
    assert lo < o.mean_r < hi
    assert o.verdict == "mean R above zero (90% interval)"


# ---------------------------------------------------------------------------------------------- missing fields
def test_missing_fields_are_reported_as_not_recorded_never_guessed(tmp_path):
    _engine(tmp_path)
    append_closed_trade(tmp_path, _ct(1, "2026-10-05 10:00", 16.0, 2.0, entry_utc="2026-10-05 09:00"))
    bare = ClosedTrade(account_id=ACC, broker="b", mode="demo", exit_utc=pd.Timestamp("2026-10-06 10:00", tz="UTC"),
                       ret=0.0, pnl=-5.0, equity_before=1016.0, position_id=2)     # legacy: no agent, side, R, costs
    append_closed_trade(tmp_path, bare)
    rep = build_performance_report(tmp_path, start=START, end=END, now=NOW)
    [a] = rep.accounts
    for dim in ("family", "agent", "timeframe", "session", "side", "day_type"):
        assert "not recorded" in _groups(a, dim), dim
    assert a.overall.trades == 2 and a.overall.r_trades == 1 and a.overall.gross_trades == 1
    assert a.overall.net_pnl == pytest.approx(11.0)
    assert a.not_recorded["r"] == 1 and a.not_recorded["commission"] == 1 and a.not_recorded["entry_utc"] == 1
    assert a.not_recorded["agent_id"] == 1
    md = format_markdown(rep)
    assert "gross on 1 of 2" in md and "not recorded" in md


def test_record_gap_fields_are_used_when_a_newer_record_carries_them(tmp_path):
    _engine(tmp_path)
    t = _ct(1, "2026-10-05 10:00", 16.0, 2.0, agent=None, entry_utc="2026-10-05 09:00")
    raw = json.loads(t.model_dump_json())
    raw.update(family="trend", timeframe="4h", session="newyork", day_type="opportunity")   # added by the gap lane
    (tmp_path / f"closed_trades_{ACC}.jsonl").write_text(json.dumps(raw) + "\n")
    [a] = build_performance_report(tmp_path, start=START, end=END, now=NOW).accounts
    assert set(_groups(a, "family")) == {"trend"} and set(_groups(a, "timeframe")) == {"4h"}
    assert set(_groups(a, "session")) == {"newyork"} and set(_groups(a, "day_type")) == {"opportunity"}


def test_a_torn_line_is_reported_and_the_rest_still_counts(tmp_path):
    _engine(tmp_path)
    _base_trades(tmp_path)
    with open(tmp_path / f"closed_trades_{ACC}.jsonl", "a") as fh:
        fh.write('{"account_id": "icm-demo", "pnl": 1\n')
    rep = build_performance_report(tmp_path, start=START, end=END, now=NOW)
    assert rep.accounts[0].overall.trades == 4
    assert any("unreadable" in e for e in rep.errors)


def test_an_empty_state_folder_reports_nothing_without_failing(tmp_path):
    rep = build_performance_report(tmp_path, start=START, end=END, now=NOW)
    assert rep.accounts == []
    assert "No closed trades or proposals" in format_markdown(rep)


# ---------------------------------------------------------------------------------------------- approvals and shadow
def _proposal(state: Path, close: str, outcome: Outcome | None, *, side: int = 1, reason: str | None = None,
              agent: str = SO, gate_refusal: list[str] | None = None, folder: str = "done") -> Proposal:
    ts = pd.Timestamp(close, tz="UTC")
    p = Proposal(proposal_id=f"{ACC}-{int(ts.timestamp())}-0a1b2c3d4e", account_id=ACC, agent_id=agent, side=side,
                 lots=0.01, entry=2000.0, stop=1990.0, target=2025.0, p=0.6, ev_r=0.2, spread_points=20.0,
                 top_features=[("x", 0.1)], currency="GBP", risk_amount=8.0, created=ts.timestamp() + 2,
                 outcome=outcome, reason_code=reason, decided_by=TELEGRAM_ID if outcome else None,
                 decided_via=f"dashboard:{EMAIL}" if outcome else None, gate_refusal=gate_refusal)
    d = state / "approvals" / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{p.proposal_id}.json").write_text(p.model_dump_json())
    return p


def _shadow(close: str, *, side: int = 1, exit_price: float | None, version: str = "v1", agent: str = SO,
            p: float = 0.6) -> ShadowTrade:
    """A 15m shadow trade whose signal bar closed at `close`: entry 2000, stop 10 away."""
    entry_ts = pd.Timestamp(close, tz="UTC") - pd.Timedelta(minutes=15)
    t = ShadowTrade(version=version, agent_id=agent, side=side, entry_ts=entry_ts, entry=2000.0,
                    stop=2000.0 - side * 10.0, target=2000.0 + side * 25.0, max_bars=16, timeframe="15m", p=p,
                    threshold=0.55, taken=True)
    if exit_price is not None:
        t.exit_ts, t.exit, t.barrier = entry_ts + pd.Timedelta(hours=1), exit_price, "time"
        t.ret = side * (exit_price - 2000.0) / 2000.0
    return t


def _book(state: Path, trades: list[ShadowTrade]) -> None:
    books: dict[str, VersionBook] = {}
    for t in trades:
        b = books.setdefault(t.version, VersionBook(version=t.version, started_utc=pd.Timestamp("2026-09-01", tz="UTC")))
        (b.closed if t.exit is not None else b.open).append(t)
    (state / "shadow_book.json").write_text(json.dumps({k: json.loads(v.model_dump_json()) for k, v in books.items()}))


def test_owner_decisions_are_counted_and_rejected_and_expired_signals_get_their_shadow_outcome(tmp_path):
    _engine(tmp_path)
    _proposal(tmp_path, "2026-10-05 09:00", Outcome.APPROVED)
    _proposal(tmp_path, "2026-10-05 10:00", Outcome.APPROVED, gate_refusal=["spread"])
    _proposal(tmp_path, "2026-10-06 09:00", Outcome.REJECTED, reason="news")
    _proposal(tmp_path, "2026-10-06 10:00", Outcome.REJECTED, reason="news", side=-1)
    _proposal(tmp_path, "2026-10-07 09:00", Outcome.EXPIRED)
    _proposal(tmp_path, "2026-10-07 11:00", Outcome.EXPIRED)          # no shadow trade: unmatched
    _proposal(tmp_path, "2026-10-08 09:00", Outcome.REJECTED, reason="cost")   # shadow still open
    _proposal(tmp_path, "2026-09-30 09:00", Outcome.REJECTED, reason="news")   # before the range
    _book(tmp_path, [
        _shadow("2026-10-05 09:00", exit_price=2020.0),                       # approved: +2R
        _shadow("2026-10-06 09:00", exit_price=2030.0),                       # rejected: +3R
        _shadow("2026-10-06 09:00", exit_price=1990.0, version="v2", p=0.4),  # another version, p further: ignored
        _shadow("2026-10-06 10:00", side=-1, exit_price=2010.0),              # rejected short: -1R
        _shadow("2026-10-07 09:00", exit_price=1990.0),                       # expired: -1R
        _shadow("2026-10-08 09:00", exit_price=None),
    ])
    [a] = build_performance_report(tmp_path, start=START, end=END, now=NOW).accounts
    ap = a.approvals
    assert (ap.proposals, ap.approved, ap.rejected, ap.expired) == (7, 2, 3, 2)
    assert ap.gate_refused_after_approval == 1 and ap.reasons == {"news": 2, "cost": 1}
    cf = {c.outcome: c for c in ap.counterfactual}
    rej, exp = cf["rejected"], cf["expired"]
    assert (rej.proposals, rej.matched, rej.still_open, rej.unmatched) == (3, 2, 1, 0)
    assert rej.net_r == pytest.approx(2.0) and rej.wins == 1
    assert rej.money == pytest.approx(16.0) and rej.money_trades == 2 and rej.currency == "GBP"   # R x risk_amount
    assert (exp.matched, exp.unmatched) == (1, 1) and exp.net_r == pytest.approx(-1.0)
    assert cf["approved"].net_r == pytest.approx(2.0)
    assert rej.sufficient is False and rej.verdict.startswith("insufficient sample")
    md = format_markdown(build_performance_report(tmp_path, start=START, end=END, now=NOW))
    assert "Owner decisions" in md and "would have" in md


# ---------------------------------------------------------------------------------------------- costs
def _cost_table(state: Path) -> None:
    CostTable(account_id=ACC, built_utc=pd.Timestamp("2026-10-04", tz="UTC"),
              spread={"london": SpreadStat(median=0.2, p90=0.3, n=100)},
              slippage={"london:market": SlippageStat(mean=0.05, n=60, from_prior=False)},
              commission_per_lot_side_usd=3.5, slippage_prior_usd=0.1).save(state / f"costs_{ACC}.json")


def test_realised_commission_is_compared_with_the_modelled_one_only_in_the_same_currency(tmp_path):
    _engine(tmp_path, currency="USD")
    _cost_table(tmp_path)
    for i, com in enumerate([0.07, 0.09]):
        t = _ct(i + 1, f"2026-10-0{5 + i} 10:00", 10.0, 1.0, entry_utc=f"2026-10-0{5 + i} 09:00", commission=com,
                swap=-0.2)
        append_closed_trade(tmp_path, t.model_copy(update={"pnl_currency": "USD"}))
    [a] = build_performance_report(tmp_path, start=START, end=END, now=NOW).accounts
    c = a.costs
    assert c.commission_trades == 2 and c.commission_total == pytest.approx(0.16)
    assert c.swap_trades == 2 and c.swap_total == pytest.approx(-0.4)
    assert c.modelled_commission_total == pytest.approx(2 * 2 * 3.5 * 0.01)           # both sides x lots
    assert c.commission_vs_model == pytest.approx(0.16 / 0.14)
    assert c.cost_r_mean == pytest.approx((0.08 + 0.2) / 10.0)                     # (commission - swap) / risk


def test_a_gbp_account_says_the_commission_is_not_comparable_rather_than_converting(tmp_path):
    _engine(tmp_path)
    _cost_table(tmp_path)
    append_closed_trade(tmp_path, _ct(1, "2026-10-05 10:00", 10.0, 1.0, entry_utc="2026-10-05 09:00"))
    [a] = build_performance_report(tmp_path, start=START, end=END, now=NOW).accounts
    assert a.costs.commission_vs_model is None
    assert "not comparable" in (a.costs.commission_note or "")


def test_realised_slippage_from_the_fills_is_set_against_the_cost_table_by_session(tmp_path):
    _engine(tmp_path)
    _cost_table(tmp_path)
    append_closed_trade(tmp_path, _ct(1, "2026-10-05 10:00", 10.0, 1.0, entry_utc="2026-10-05 09:00"))
    data = tmp_path / "data"
    fills = pd.DataFrame({"ts_utc": pd.to_datetime(["2026-10-05 08:00", "2026-10-05 09:00", "2026-10-06 02:00"],
                                                   utc=True),
                          "client_order_id": ["a", "b", "c"], "agent_id": [SO] * 3, "side": [1, -1, 1],
                          "lots": [0.01] * 3, "requested": [2000.0, 2000.0, 2000.0], "filled": [2000.1, 1999.9, 2000.0],
                          "order_type": ["market"] * 3, "retcode": [10009] * 3, "commission": [np.nan] * 3})
    Store(data).append("fills", fills, source=ACC, dedupe=False)
    [a] = build_performance_report(tmp_path, start=START, end=END, now=NOW, data_root=data).accounts
    slip = {s.session: s for s in a.costs.slippage}
    assert slip["london"].fills == 2 and slip["london"].realised_mean_usd == pytest.approx(0.1)
    assert slip["london"].modelled_usd == pytest.approx(0.05) and slip["london"].modelled_from == "measured"
    assert slip["asia"].modelled_usd == pytest.approx(0.1) and slip["asia"].modelled_from == "prior"
    assert slip["london"].sufficient is False


# ---------------------------------------------------------------------------------------------- north star
def _bars(data: Path, opp_day: str) -> None:
    """30-minute bars on weekday opportunity days from 2026-08-24: range 10 every day, 30 on `opp_day`."""
    from goldbot.features.day_type import opportunity_day
    ts = pd.date_range("2026-08-23 22:00", "2026-10-09 21:30", freq="30min", tz="UTC")
    day = opportunity_day(ts)
    ts = ts[day.dayofweek < 5]
    day = opportunity_day(ts)
    amp = np.where(day == pd.Timestamp(opp_day), 15.0, 5.0)
    mid = 2000.0
    df = pd.DataFrame({"ts_utc": ts})
    for side, off in (("bid", -0.1), ("ask", 0.1)):
        df[f"{side}_open"] = mid + off
        df[f"{side}_high"] = mid + amp + off
        df[f"{side}_low"] = mid - amp + off
        df[f"{side}_close"] = mid + off
    Store(data).append("bars_1m", df, source="dukascopy")


def test_opportunity_day_capture_against_the_fifty_pound_north_star(tmp_path):
    _engine(tmp_path)
    data = tmp_path / "data"
    _bars(data, "2026-10-07")
    for t in [
        _ct(1, "2026-10-07 15:00", 52.0, 6.5, entry_utc="2026-10-07 08:00"),     # opportunity day: £52, 6.5R
        _ct(2, "2026-10-05 10:00", -8.0, -1.0, entry_utc="2026-10-05 09:00"),    # ordinary day
    ]:
        append_closed_trade(tmp_path, t)
    rep = build_performance_report(tmp_path, start=START, end=END, now=NOW, data_root=data)
    [a] = rep.accounts
    ns = a.north_star
    assert ns.opportunity_days == 1 and ns.classified_days == 5
    assert ns.traded_opportunity_days == 1 and ns.captured_1r == 1 and ns.days_50_plus_on_opportunity == 1
    assert ns.pnl_on_opportunity_days == pytest.approx(52.0) and ns.pnl_on_other_days == pytest.approx(-8.0)
    dt = _groups(a, "day_type")
    assert dt["opportunity"].trades == 1 and dt["ordinary"].trades == 1
    assert a.days.days_50_plus == 1 and a.days.traded_days == 2 and a.days.weekdays == 5
    md = format_markdown(rep)
    assert "North star" in md and "£50" in md


def test_without_bars_the_opportunity_days_are_not_recorded(tmp_path):
    _engine(tmp_path)
    _base_trades(tmp_path)
    [a] = build_performance_report(tmp_path, start=START, end=END, now=NOW).accounts
    assert a.north_star.opportunity_days is None and "not recorded" in a.north_star.note


# ---------------------------------------------------------------------------------------------- drawdown
def test_drawdown_worst_day_and_worst_week_are_set_against_the_limits(tmp_path):
    _engine(tmp_path)
    for t in [
        _ct(1, "2026-10-05 10:00", 30.0, 3.0, equity_before=1000.0),
        _ct(2, "2026-10-06 10:00", -10.3, -1.0, equity_before=1030.0),
        _ct(3, "2026-10-06 12:00", -10.197, -1.0, equity_before=1019.7),      # day loss 20.497 = 1.99% of 1030
        _ct(4, "2026-10-07 10:00", -9.99, -1.0, equity_before=1009.503),
    ]:
        append_closed_trade(tmp_path, t)
    [a] = build_performance_report(tmp_path, start=START, end=END, now=NOW).accounts
    d = a.drawdown
    assert d.max_drawdown_pct == pytest.approx(100 * (1 - 0.99 * 0.99 * (1 - 9.99 / 1009.503)), abs=1e-6)
    assert d.dd_stage1_pct == pytest.approx(8.0) and d.dd_stage2_pct == pytest.approx(12.0)
    assert d.worst_day == "2026-10-06" and d.worst_day_loss == pytest.approx(20.497)
    assert d.worst_day_pct == pytest.approx(100 * 20.497 / 1030.0)
    assert d.daily_cap_pct == pytest.approx(2.0) and d.daily_loss_limit_abs == pytest.approx(50.0)
    assert d.days_at_daily_limit == 0
    assert d.worst_week_loss == pytest.approx(0.487) and d.worst_week_pct == pytest.approx(0.0487)   # on 1,000
    assert d.weekly_cap_pct == pytest.approx(5.0)
    assert d.engine_drawdown_pct == pytest.approx(100 * (1030.0 - 990.0) / 1030.0) and d.engine_stage == "normal"


def test_a_week_without_a_loss_has_no_worst_week_and_the_markdown_says_so(tmp_path):
    _engine(tmp_path)
    append_closed_trade(tmp_path, _ct(1, "2026-10-05 10:00", 30.0, 3.0))
    append_closed_trade(tmp_path, _ct(2, "2026-10-06 10:00", -8.0, -1.0, equity_before=1030.0))
    rep = build_performance_report(tmp_path, start=START, end=END, now=NOW)
    d = rep.accounts[0].drawdown
    assert d.worst_week is None and d.worst_week_loss is None and d.worst_day == "2026-10-06"
    md = format_markdown(rep)
    assert "no losing week" in md and "absolute £50.00" in md


# ---------------------------------------------------------------------------------------------- journal
def test_the_journal_counts_proposals_gate_refusals_by_reason_and_failed_orders(tmp_path):
    _engine(tmp_path)
    append_closed_trade(tmp_path, _ct(1, "2026-10-05 10:00", 10.0, 1.0, entry_utc="2026-10-05 09:00"))
    data = tmp_path / "data"
    rows = pd.DataFrame({"ts_utc": pd.to_datetime(["2026-10-05 09:00"] * 4 + ["2026-09-01 09:00"], utc=True),
                         "account_id": [ACC] * 5, "agent_id": [SO] * 5,
                         "action": ["proposed", "gate:spread,blackout", "gate:spread", "order", "gate:spread"],
                         "p": [0.6] * 5, "mult": [1.0] * 5, "proposal_id": [None] * 5,
                         "detail": ["{}", "{}", "{}", json.dumps({"ok": False}), "{}"]})
    Store(data).append("decisions", rows, source=ACC, dedupe=False)
    [a] = build_performance_report(tmp_path, start=START, end=END, now=NOW, data_root=data).accounts
    j = a.journal
    assert j is not None and j.decisions == 4 and j.proposed == 1
    assert j.gate_refusals == {"spread": 2, "blackout": 1} and j.orders == 1 and j.orders_failed == 1


# ---------------------------------------------------------------------------------------------- safety of the output
def _snapshot(d: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(d)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(d.rglob("*"))}


def _full_state(state: Path) -> None:
    _engine(state)
    _base_trades(state)
    _cost_table(state)
    _proposal(state, "2026-10-06 09:00", Outcome.REJECTED, reason="news")
    _book(state, [_shadow("2026-10-06 09:00", exit_price=2030.0)])


def test_the_report_never_writes_to_the_state_folder(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    _full_state(state)
    before = _snapshot(state)
    assert main(["--state", str(state), "--from", START, "--to", END, "--out", str(tmp_path / "out")]) == 0
    assert _snapshot(state) == before
    assert main(["--state", str(state), "--from", START, "--to", END, "--out", str(state / "reports")]) == 2
    assert _snapshot(state) == before


def test_no_identifiers_reach_the_markdown_or_the_json(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    _full_state(state)
    out = tmp_path / "out"
    assert main(["--state", str(state), "--from", START, "--to", END, "--out", str(out)]) == 0
    md = (out / f"perf_{START}_{END}.md").read_text()
    js = (out / f"perf_{START}_{END}.json").read_text()
    for text in (md, js):
        for secret in (str(TELEGRAM_ID), EMAIL, "77001234", "coid-secret", "ICMarketsSC", "0a1b2c3d4e"):
            assert secret not in text, secret
    rep = json.loads(js)
    assert rep["accounts"][0]["account_id"] == ACC and rep["start"] == START and rep["end"] == END


def test_perf_report_runs_through_the_run_dispatch_and_the_script(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    _full_state(state)
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root)}
    for cmd in ([sys.executable, "-m", "goldbot.ops.run", "perf-report"],
                [sys.executable, str(root / "scripts" / "performance_report.py")]):
        out = tmp_path / ("out_" + str(len(cmd)))
        r = subprocess.run([*cmd, "--state", str(state), "--from", START, "--to", END, "--out", str(out)],
                           capture_output=True, text=True, env=env, cwd=tmp_path, timeout=120)
        assert r.returncode == 0, r.stderr
        assert (out / f"perf_{START}_{END}.md").exists() and (out / f"perf_{START}_{END}.json").exists()


def test_a_bad_date_range_is_refused(tmp_path):
    assert main(["--state", str(tmp_path), "--from", "2026-10-09", "--to", "2026-10-05"]) == 2
    assert main(["--state", str(tmp_path), "--from", "05/10/2026"]) == 2


def test_a_family_with_several_timeframes_outside_the_population_has_its_timeframe_not_recorded(tmp_path):
    _engine(tmp_path)
    append_closed_trade(tmp_path, _ct(1, "2026-10-05 10:00", 8.0, 1.0, agent=BO, entry_utc="2026-10-05 09:00"))
    [a] = build_performance_report(tmp_path, start=START, end=END, now=NOW).accounts
    assert set(_groups(a, "family")) == {"breakout"} and set(_groups(a, "timeframe")) == {"not recorded"}


def test_the_default_output_folder_is_kept_out_of_the_public_repo():
    root = Path(__file__).resolve().parents[1]
    assert "/reports/" in (root / ".gitignore").read_text().splitlines()
