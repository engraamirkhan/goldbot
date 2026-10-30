"""Review round on the learning-trade reports: a family is read against the random control on the same risk day x
session x side (not against zero), voided rows are skipped and censored rows counted apart, gross is pnl + commission
+ swap, and the closed-trade sections do not move when learning trades exist."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.db import Database
from goldbot.engine import cost_probe
from goldbot.ops import health, learning_ledger
from goldbot.ops.perf_report import format_markdown
from tests.test_health import make_ctx
from tests.test_learning_reports import (
    ACC,
    CONTROL,
    NOW,
    STRAT,
    _accounts,
    _arm,
    _closed,
    _engine,
    _probe,
    _report,
)

__all__ = ["_accounts"]            # the autouse fixture that enables the accounts


def _paired(state: Path, n_days: int = 5, per_day: int = 8, edge: float = 0.0,
            skip_control_day: str | None = None) -> None:
    """A family and the random control on the same days, sessions and side. Each day has its own drift (the market),
    both sides see the same noise; the family adds `edge` R on top (stop_loss is 2.4)."""
    for d in range(n_days):
        day = f"2026-10-{26 + d}"
        for i in range(per_day):
            drift = (d - 2) * 0.6 + (0.6 if i % 2 else -0.6)
            _arm(state, f"f-{d}-{i}", STRAT, day=day, minute=i, pnl=drift + edge * 2.4)
            if day != skip_control_day:
                _arm(state, f"c-{d}-{i}", CONTROL, day=day, minute=i, pnl=drift)


def _family(rep: Any) -> Any:
    [a] = rep.accounts
    return {g.key: g for g in a.learning.families}["exploration / session_vwap_fade"]


# ---------------------------------------------------------------------------------------------- matched control
def test_a_family_that_only_rides_the_same_day_drift_reads_not_distinguishable_from_the_random_control(tmp_path):
    _engine(tmp_path)
    _paired(tmp_path)
    g = _family(_report(tmp_path))
    assert g.excess_r == pytest.approx(0.0, abs=1e-9) and g.excess_ci90 is not None
    assert g.verdict.startswith("not distinguishable from the random control")
    assert g.mean_r is not None and "above zero" not in g.verdict


def test_a_family_with_a_real_edge_over_the_control_on_the_same_cells_reads_above(tmp_path):
    _engine(tmp_path)
    _paired(tmp_path, edge=0.3)
    g = _family(_report(tmp_path))
    assert g.excess_r == pytest.approx(0.3) and g.excess_ci90 is not None and g.excess_ci90[0] > 0
    assert g.verdict.startswith("above the random control")


def test_a_family_below_the_control_reads_below(tmp_path):
    _engine(tmp_path)
    _paired(tmp_path, edge=-0.3)
    assert _family(_report(tmp_path)).verdict.startswith("below the random control")


def test_cells_without_control_trades_are_dropped_and_counted(tmp_path):
    _engine(tmp_path)
    _paired(tmp_path, skip_control_day="2026-10-30")
    g = _family(_report(tmp_path))
    assert g.excess_dropped == 8 and g.excess_cells == 4
    assert "8 unmatched" in g.verdict


def test_fewer_than_thirty_on_a_side_gives_no_interval_on_the_difference(tmp_path):
    _engine(tmp_path)
    _paired(tmp_path, n_days=2, per_day=8, edge=0.3)
    g = _family(_report(tmp_path))
    assert g.excess_r == pytest.approx(0.3) and g.excess_ci90 is None and g.verdict.startswith("insufficient sample")


def test_no_learning_row_reads_mean_r_above_zero_and_the_table_says_no_row_is_evidence(tmp_path):
    _engine(tmp_path)
    _paired(tmp_path, edge=0.3)
    rep = _report(tmp_path)
    [a] = rep.accounts
    assert a.learning is not None
    assert all("above zero" not in g.verdict and "below zero" not in g.verdict
               for g in (*a.learning.kinds, *a.learning.families))
    md = format_markdown(rep)
    assert "no row here is evidence" in md and "registry trial" in md


# ---------------------------------------------------------------------------------------------- voided and censored
def _void(state: Path, cid: str) -> None:
    from goldbot.research.evidence_exclusions import Exclusion, record
    record(Database.open(state, "aux"), [Exclusion(
        client_order_id=cid, kind="exploration", account_id=ACC, scope=("n",), reason="quarantine", span_ref="s",
        decided_utc=pd.Timestamp("2026-10-29", tz="UTC"))])


def test_voided_rows_are_skipped_and_censored_rows_are_counted_beside_the_row_never_in_r_or_money(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "ok")
    _arm(tmp_path, "void", minute=5, pnl=50.0)
    _arm(tmp_path, "cens", minute=9, pnl=-30.0, censored=True)
    _void(tmp_path, "void")
    rep = _report(tmp_path)
    [g] = rep.accounts[0].learning.kinds
    assert (g.trades, g.censored, g.r_trades) == (1, 1, 1) and g.net_pnl == pytest.approx(1.2)
    assert "(+1 censored)" in format_markdown(rep)


def test_pnl_basis_skips_voided_and_counts_censored_apart(tmp_path):
    _arm(tmp_path, "ok", day="2026-10-29")
    _arm(tmp_path, "void", day="2026-10-29", minute=5)
    _arm(tmp_path, "cens", day="2026-10-29", minute=9, censored=True)
    _void(tmp_path, "void")
    c = health.check_pnl_basis(make_ctx(tmp_path, now=NOW), ACC)
    assert c.reason.startswith("0 of 1 closed trades") and "1 censored" in c.reason


# ---------------------------------------------------------------------------------------------- gross, currency, errors
def test_gross_is_pnl_plus_commission_and_swap_only_never_the_ledgers_modelled_spread(tmp_path):
    _engine(tmp_path)                                      # a rate and a price: the ledger could model a spread
    _arm(tmp_path, "e1")                                   # pnl 1.2, commission 0.07, swap -0.03 (paid)
    [g] = _report(tmp_path).accounts[0].learning.kinds
    assert g.gross_pnl == pytest.approx(1.30) and g.gross_r == pytest.approx(1.30 / 2.4)
    [row] = learning_ledger.closed_learning_trades(tmp_path, NOW).rows
    assert row.gross == pytest.approx(1.30)


def test_the_closed_trade_sections_are_identical_with_and_without_learning_trades(tmp_path):
    _engine(tmp_path, "icm-demo")
    _closed(tmp_path, pid=1)
    without = _report(tmp_path).accounts[0].model_dump(exclude={"learning"})
    for i in range(4):
        _arm(tmp_path, f"e{i}", account="icm-demo", minute=i)
    rep = _report(tmp_path)
    assert rep.accounts[0].learning is not None
    assert rep.accounts[0].model_dump(exclude={"learning"}) == without


def test_the_learning_tables_carry_their_own_currency_and_the_record_sections_stay_without_one(tmp_path):
    _arm(tmp_path, "e1")                                   # no engine file, no closed trades: the record has no currency
    [a] = _report(tmp_path).accounts
    assert a.currency is None and a.learning is not None
    ccys = {c.currency for c in learning_ledger.closed_learning_trades(tmp_path, NOW).rows}
    assert a.learning.currency == (next(iter(ccys)) if len(ccys) == 1 else None)


def test_a_failing_ledger_read_is_a_report_error_not_a_crash(tmp_path, monkeypatch):
    from goldbot.ops import perf_report

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("torn")
    monkeypatch.setattr(perf_report, "closed_learning_trades", boom)
    _engine(tmp_path, "icm-demo")
    _closed(tmp_path, pid=1)
    rep = _report(tmp_path)
    assert len(rep.accounts) == 1 and any("RuntimeError" in e for e in rep.errors)


def test_the_health_run_builds_the_learning_ledger_once_for_all_accounts(tmp_path, monkeypatch):
    from goldbot.api import trades_ledger
    calls: list[dict[str, Any]] = []
    real = trades_ledger.build_ledger

    def counting(*a: Any, **k: Any) -> Any:
        calls.append(k)
        return real(*a, **k)
    monkeypatch.setattr(trades_ledger, "build_ledger", counting)
    _arm(tmp_path, "e1", day="2026-10-29")
    ctx = make_ctx(tmp_path, now=NOW)
    health.check_pnl_basis(ctx, ACC)
    health.check_pnl_basis(ctx, "icm-demo")
    assert len(calls) == 1 and calls[0].get("read_only") is True


def test_reports_open_the_cost_probe_source_without_migrating_aux_db(tmp_path, monkeypatch):
    seen: list[dict[str, Any]] = []
    real = cost_probe.probes

    def spy(state: Any, account_id: Any = None, **k: Any) -> Any:
        seen.append(k)
        return real(state, account_id, **k)
    monkeypatch.setattr(cost_probe, "probes", spy)
    _probe(tmp_path)
    learning_ledger.closed_learning_trades(tmp_path, NOW)
    assert seen and seen[0].get("migrate") is False


def test_account_flag_keeps_an_account_whose_config_name_embeds_digits_and_has_only_learning_trades(
        tmp_path, enabled_accounts):
    enabled_accounts(ACC, "icm-demo-51234567")
    _arm(tmp_path, "e1", account="icm-demo-51234567")
    rep = _report(tmp_path, account="icm-demo-51234567")
    assert [a.account_id for a in rep.accounts] == ["icm-demo-51234567"]
