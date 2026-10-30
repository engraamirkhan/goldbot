"""Reporting blind spot (measured on the brain 2026-10-30): demo accounts closed ~420 exploration trades a day, but
`perf-report` said "No closed trades or proposals in this range" and the `pnl_basis` health check "no closed trades in
the last 30 d", because both read only state/closed_trades*.jsonl. Exploration and cost-probe closes are never written
there (engine/exploration.py, engine/cost_probe.py: that record feeds the phase gate, the stop rule and the throttle),
so both reports now also read the trade ledger (ops/learning_ledger.py), labelled by kind, with the closed-trade
honesty rules (net only where commission and swap are both recorded; no interval under 30 net R)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.db import Database
from goldbot.engine import cost_probe, exploration
from goldbot.engine.exploration import ExplorationRecord
from goldbot.ops import gates_phase, health, learning_ledger
from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade, load_closed_trades
from goldbot.ops.perf_report import MIN_TRADES, build_performance_report, format_markdown
from tests.test_health import make_ctx

NOW = pd.Timestamp("2026-10-30 09:00", tz="UTC")       # a Friday
START, END = "2026-10-26", "2026-10-30"
ACC = "icm-probe"
STRAT, CONTROL = "S1A", "S0"                           # a strategy arm (session_vwap_fade) and a random_bracket control


@pytest.fixture(autouse=True)
def _accounts(enabled_accounts):
    enabled_accounts(ACC, "icm-demo")


def _arm(state: Path, cid: str, arm: str = STRAT, *, account: str = ACC, day: str = "2026-10-28", minute: int = 0,
         pnl: float | None = 1.2, stop_loss: float = 2.4, commission: float | None = 0.07,
         swap: float | None = -0.03, pid: int | None = None, side: int = 1, censored: bool = False) -> None:
    """One closed exploration row in the real body shape (aux.db `exploration_trades`)."""
    sent = pd.Timestamp(f"{day} 10:00", tz="UTC") + pd.Timedelta(minutes=minute)
    rec = ExplorationRecord(
        client_order_id=cid, account_id=account, arm_id=arm, family="x", tf="5m", side=side, lots=0.01, signal_utc=sent,
        sent_utc=sent, atr_usd=3.0, stop_usd=3.0, target_usd=6.0, hold_s=3600, requested_price=2400.0, spread_usd=0.2,
        fill_price=2400.1, stop_loss=stop_loss, cost=0.4, position_id=pid, exit_utc=sent + pd.Timedelta(minutes=10),
        exit_price=2406.1, exit_reason="target", swap=swap, commission=commission, pnl=pnl, censored=censored,
        fx_acct_per_usd=0.8)
    with Database.open(state, "aux").write() as conn:
        exploration.LEDGER.upsert(conn, rec)


def _probe(state: Path, cid: str = "cp-1", account: str = ACC) -> None:
    with Database.open(state, "aux").write() as conn:
        cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
            client_order_id=cid, account_id=account, agent_id="mean_reversion-g0-ab", side=-1, lots=0.01,
            requested_price=2401.0, spread_usd=0.25, sent_utc=pd.Timestamp("2026-10-28 08:00", tz="UTC"),
            fill_price=2400.9, exit_utc=pd.Timestamp("2026-10-28 09:00", tz="UTC"), exit_price=2399.0,
            exit_reason="time_exit", swap=0.0, commission=0.07, pnl=1.41, stop_loss=3.0, fx_acct_per_usd=0.8))


def _engine(state: Path, account: str = ACC) -> None:
    (state / f"engine_{account}.json").write_text(json.dumps({"account": account, "mode": "demo", "currency": "GBP",
                                                              "usd_to_account": 0.8, "ts": NOW.timestamp()}))


def _report(state: Path, **kw: Any):
    return build_performance_report(state, start=START, end=END, now=NOW, **kw)


def _closed(state: Path, pid: int, account: str = "icm-demo") -> None:
    append_closed_trade(state, ClosedTrade(
        account_id=account, broker="icm", mode="demo", exit_utc=pd.Timestamp("2026-10-28 12:00", tz="UTC"), ret=0.0,
        pnl=2.0, equity_before=1000.0, position_id=pid, r=1.0, commission=0.1, swap=0.0, pnl_currency="GBP"))


# ---------------------------------------------------------------------------------------------- perf report
def test_perf_report_shows_exploration_control_and_probe_closes_for_an_account_with_no_closed_trades(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "e1")
    _arm(tmp_path, "e2", minute=20, pnl=-2.4)
    _arm(tmp_path, "c1", CONTROL, minute=40)
    _probe(tmp_path)
    rep = _report(tmp_path)
    [a] = rep.accounts
    assert a.account_id == ACC and a.overall.trades == 0                      # the closed-trade sections are untouched
    assert a.learning is not None
    kinds = {g.key: g for g in a.learning.kinds}
    assert {k: g.trades for k, g in kinds.items()} == {"exploration": 2, "control": 1, "cost probe": 1}
    assert kinds["exploration"].net_pnl == pytest.approx(-1.2) and kinds["exploration"].net_r == pytest.approx(-0.5)
    md = format_markdown(rep)
    assert "No closed trades or proposals" not in md and "not in the phase-gate record" in md


def test_control_arms_sit_beside_the_families_in_the_by_family_table(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "e1")
    _arm(tmp_path, "c1", CONTROL, minute=30)
    [a] = _report(tmp_path).accounts
    assert a.learning is not None
    assert [g.key for g in a.learning.families] == ["exploration / session_vwap_fade", "control / random_bracket"]


def test_net_money_and_r_only_where_commission_and_swap_are_both_recorded(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "e1")
    _arm(tmp_path, "e2", minute=10, commission=None)                           # costs not recorded: counted, not net
    [a] = _report(tmp_path).accounts
    assert a.learning is not None
    [g] = a.learning.kinds
    assert g.trades == 2 and g.r_trades == 1 and g.money_trades == 1 and g.basis_not_recorded == 1
    assert g.net_pnl == pytest.approx(1.2)


def test_thirty_trade_rule_applies_to_exploration_groups(tmp_path):
    _engine(tmp_path)
    for i in range(MIN_TRADES - 1):
        _arm(tmp_path, f"e{i}", minute=i, pnl=1.2 if i % 2 else -1.2)
    [a] = _report(tmp_path).accounts
    assert a.learning is not None
    g = a.learning.kinds[0]
    assert g.trades == MIN_TRADES - 1 and not g.sufficient and g.verdict.startswith("insufficient sample")
    _arm(tmp_path, "e-last", minute=59)
    [g] = _report(tmp_path).accounts[0].learning.kinds
    assert g.trades == MIN_TRADES and g.r_ci90 is not None


def test_learning_groups_are_counted_apart_in_the_multiple_groups_note(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "e1")
    rep = _report(tmp_path)
    [a] = rep.accounts
    assert a.learning is not None
    assert rep.groups == sum(len(gs) for gs in a.by.values())                  # the closed-trade count is as before
    assert rep.learning_groups == len(a.learning.kinds) + len(a.learning.families)
    assert (f"plus {rep.learning_groups} learning groups (kind rows overlap family rows); about 1 in 10 groups "
            "shows an interval by chance") in format_markdown(rep)


def test_a_trade_in_both_sources_is_counted_once_in_the_closed_trade_record(tmp_path):
    _engine(tmp_path, "icm-demo")
    _closed(tmp_path, pid=77)
    _arm(tmp_path, "dup", account="icm-demo", pid=77)                          # same account and broker position
    _arm(tmp_path, "own", account="icm-demo", minute=30, pid=78)
    [a] = _report(tmp_path).accounts
    assert a.overall.trades == 1 and a.learning is not None
    assert [(g.key, g.trades) for g in a.learning.kinds] == [("exploration", 1)]


def test_trades_outside_the_range_and_other_accounts_are_left_out(tmp_path):
    _arm(tmp_path, "old", day="2026-10-20")
    _arm(tmp_path, "mine")
    _arm(tmp_path, "other", account="icm-demo")
    [a] = _report(tmp_path, account=ACC).accounts
    assert a.learning is not None and [g.trades for g in a.learning.kinds] == [1]


def test_an_unreadable_aux_db_is_named_in_the_report_errors(tmp_path):
    (tmp_path / "aux.db").write_bytes(b"not a database")
    rep = _report(tmp_path)
    assert any("exploration" in e and "missing, not zero" in e for e in rep.errors)


def test_without_learning_trades_the_report_has_no_learning_section(tmp_path):
    _engine(tmp_path, "icm-demo")
    _closed(tmp_path, pid=1)
    [a] = _report(tmp_path).accounts
    assert a.learning is None and "phase-gate record" not in format_markdown(_report(tmp_path))


# ---------------------------------------------------------------------------------------------- gate isolation
def test_exploration_and_probe_trades_never_reach_the_closed_trade_record_the_phase_gate_reads(tmp_path):
    _closed(tmp_path, pid=1)
    before, _ = load_closed_trades(tmp_path)
    files = sorted(p.name for p in tmp_path.glob("closed_trades*"))
    for i in range(5):
        _arm(tmp_path, f"e{i}", minute=i, account="icm-demo")
    _probe(tmp_path, account="icm-demo")
    rep = _report(tmp_path)                                                    # the reports read, they never write
    health.check_pnl_basis(make_ctx(tmp_path, now=NOW), "icm-demo")
    after, err = load_closed_trades(tmp_path)
    assert err is None and after == before and len(after) == 1
    assert sorted(p.name for p in tmp_path.glob("closed_trades*")) == files
    assert rep.accounts[0].overall.trades == 1                                 # gate evidence: the one closed trade
    assert gates_phase.closed_trade_files(tmp_path) == [gates_phase.closed_trade_file(tmp_path, "icm-demo")]


def test_reader_drops_a_ledger_row_whose_position_is_in_the_record(tmp_path):
    _closed(tmp_path, pid=5)
    _arm(tmp_path, "dup", account="icm-demo", pid=5)
    trades, _ = load_closed_trades(tmp_path)
    got = learning_ledger.closed_learning_trades(tmp_path, NOW, learning_ledger.recorded_keys(trades))
    assert got.rows == [] and got.unreadable == []


# ---------------------------------------------------------------------------------------------- health check
def test_pnl_basis_counts_exploration_and_probe_closes_with_their_kinds(tmp_path):
    for i in range(3):
        _arm(tmp_path, f"e{i}", minute=i, day="2026-10-29")
    _arm(tmp_path, "c1", CONTROL, minute=9, day="2026-10-29")
    _probe(tmp_path)
    c = health.check_pnl_basis(make_ctx(tmp_path, now=NOW), ACC)
    assert c.status == "ok" and c.reason.startswith("0 of 5 closed trades in the last 30 d")
    assert "(exploration 3, control 1, probes 1)" in c.reason


def test_pnl_basis_warns_when_exploration_rows_lack_costs(tmp_path):
    for i in range(4):
        _arm(tmp_path, f"e{i}", minute=i, day="2026-10-29", swap=None)
    _arm(tmp_path, "ok", minute=9, day="2026-10-29")
    c = health.check_pnl_basis(make_ctx(tmp_path, now=NOW), ACC)
    assert c.status == "warn" and c.reason.startswith("4 of 5 closed trades") and "exploration 5" in c.reason


def test_pnl_basis_adds_exploration_to_the_closed_trades_of_the_account_once(tmp_path):
    _closed(tmp_path, pid=9)
    _arm(tmp_path, "dup", account="icm-demo", pid=9, day="2026-10-28")
    _arm(tmp_path, "e1", account="icm-demo", pid=10, day="2026-10-28", minute=5)
    c = health.check_pnl_basis(make_ctx(tmp_path, now=NOW), "icm-demo")
    assert c.reason.startswith("0 of 2 closed trades") and "exploration 1" in c.reason


def test_pnl_basis_says_no_closed_trades_when_neither_source_has_any(tmp_path):
    c = health.check_pnl_basis(make_ctx(tmp_path, now=NOW), ACC)
    assert c.status == "ok" and "no closed trades in the last 30 d" in c.reason


def test_pnl_basis_warns_with_the_reason_when_aux_db_is_unreadable(tmp_path):
    (tmp_path / "aux.db").write_bytes(b"not a database")
    c = health.check_pnl_basis(make_ctx(tmp_path, now=NOW), ACC)
    assert c.status == "warn" and "exploration" in c.reason and "unreadable" in c.reason


def test_pnl_basis_ignores_exploration_closes_older_than_30_days(tmp_path):
    _arm(tmp_path, "old", day="2026-09-01")
    assert "no closed trades" in health.check_pnl_basis(make_ctx(tmp_path, now=NOW), ACC).reason
