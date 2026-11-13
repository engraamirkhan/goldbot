"""BACKLOG 195: the perf report reads the MFE / MAE R135 records on every closed exploration body (`mfe_r`, `mae_r`,
`mfe_usd`, `mae_usd`, `excursion_approx`). Acceptance: "the performance report's per-arm table shows median MFE R and
MAE R with n, leaving out rows without the fields (older rows) and counting `excursion_approx` rows separately; a test
with a mixed old/new ledger proves the old rows are skipped, not read as 0 R". With it, per arm and family: the stop
exits' share against the bracket's driftless random-walk null, the median MFE of losers (the move given back) and the
median MAE of winners (a stop too tight?)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.db import Database
from goldbot.engine import exploration
from goldbot.engine.exploration import ExplorationRecord
from goldbot.ops import learning_ledger
from goldbot.ops.perf_report import build_performance_report, format_markdown

NOW = pd.Timestamp("2026-11-20 09:00", tz="UTC")       # a Friday
START, END = "2026-11-16", "2026-11-20"
ACC = "icm-probe"
STRAT, CONTROL = "S1A", "S0"                           # session_vwap_fade and the random_bracket control


@pytest.fixture(autouse=True)
def _accounts(enabled_accounts):
    enabled_accounts(ACC)


def _engine(state: Path) -> None:
    (state / f"engine_{ACC}.json").write_text(json.dumps({"account": ACC, "mode": "demo", "currency": "GBP",
                                                          "usd_to_account": 0.8, "ts": NOW.timestamp()}))


def _arm(state: Path, cid: str, arm: str = STRAT, *, minute: int = 0, pnl: float = 1.2, exit_reason: str = "target",
         stop_usd: float = 3.0, target_usd: float = 6.0, censored: bool = False, **excursion: Any) -> None:
    """One closed exploration row in the real body shape; `excursion` the R135 fields (absent: an older row)."""
    sent = pd.Timestamp("2026-11-18 10:00", tz="UTC") + pd.Timedelta(minutes=minute)
    rec = ExplorationRecord(
        client_order_id=cid, account_id=ACC, arm_id=arm, family="x", tf="5m", side=1, lots=0.01, signal_utc=sent,
        sent_utc=sent, atr_usd=3.0, stop_usd=stop_usd, target_usd=target_usd, hold_s=3600, requested_price=2400.0,
        spread_usd=0.2, fill_price=2400.1, stop_loss=2.4, cost=0.4, exit_utc=sent + pd.Timedelta(minutes=10),
        exit_price=2406.1, exit_reason=exit_reason, swap=-0.03, commission=0.07, pnl=pnl, censored=censored,
        fx_acct_per_usd=0.8, **excursion)
    with Database.open(state, "aux").write() as conn:
        exploration.LEDGER.upsert(conn, rec)


def _learning(state: Path):
    rep = build_performance_report(state, start=START, end=END, now=NOW)
    [a] = rep.accounts
    assert a.learning is not None
    return rep, a.learning


def _arm_row(lr, key: str = "exploration / session_vwap_fade / S1A"):
    return {e.key: e for e in lr.excursion_arms}[key]


def test_ledger_reader_carries_the_excursion_fields_and_leaves_them_none_on_older_rows(tmp_path):
    _arm(tmp_path, "new", mfe_r=1.4, mae_r=-0.3, mfe_usd=4.2, mae_usd=-0.9, excursion_approx=False)
    _arm(tmp_path, "old", minute=20)
    rows = {c.position_id or c.exit_utc.minute: c for c in learning_ledger.read_learning_closes(tmp_path, NOW).rows}
    new, old = rows[10], rows[30]
    assert (new.mfe_r, new.mae_r, new.mfe_usd, new.mae_usd, new.excursion_approx) == (1.4, -0.3, 4.2, -0.9, False)
    assert (old.mfe_r, old.mae_r, old.excursion_approx) == (None, None, None)
    assert (new.exit_reason, new.stop_usd, new.target_usd) == ("target", 3.0, 6.0)


def test_mixed_old_and_new_ledger_skips_the_old_rows_instead_of_reading_them_as_zero_r(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "n1", mfe_r=2.0, mae_r=-0.4, excursion_approx=False)
    _arm(tmp_path, "n2", minute=5, mfe_r=1.0, mae_r=-0.2, excursion_approx=False)
    for i in range(3):                                                        # older rows: no fields at all
        _arm(tmp_path, f"o{i}", minute=10 + i)
    e = _arm_row(_learning(tmp_path)[1])
    assert (e.trades, e.recorded, e.not_recorded, e.approx) == (5, 2, 3, 0)
    assert e.mfe_r_median == pytest.approx(1.5) and e.mae_r_median == pytest.approx(-0.3)   # 0 R would pull both in


def test_approximate_excursions_are_counted_apart_and_never_in_the_exact_medians(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "exact", mfe_r=2.0, mae_r=-0.5, excursion_approx=False)
    _arm(tmp_path, "approx", minute=5, mfe_r=9.0, mae_r=-0.9, excursion_approx=True)
    e = _arm_row(_learning(tmp_path)[1])
    assert (e.recorded, e.approx) == (1, 1)
    assert e.mfe_r_median == pytest.approx(2.0) and e.mae_r_median == pytest.approx(-0.5)
    assert e.approx_mfe_r_median == pytest.approx(9.0) and e.approx_mae_r_median == pytest.approx(-0.9)


def test_losers_median_mfe_and_winners_median_mae_are_read_with_their_counts(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "l1", pnl=-2.4, exit_reason="stop", mfe_r=0.8, mae_r=-1.0, mfe_usd=2.4, excursion_approx=False)
    _arm(tmp_path, "l2", minute=5, pnl=-2.4, exit_reason="stop", mfe_r=0.4, mae_r=-1.0, mfe_usd=1.2,
         excursion_approx=False)
    _arm(tmp_path, "w1", minute=10, mfe_r=2.0, mae_r=-0.6, excursion_approx=False)
    e = _arm_row(_learning(tmp_path)[1])
    assert (e.losers, e.loser_mfe_r_median, e.loser_mfe_usd_median) == (2, pytest.approx(0.6), pytest.approx(1.8))
    assert (e.winners, e.winner_mae_r_median) == (1, pytest.approx(-0.6))


def test_stop_exit_share_is_set_against_the_brackets_random_walk_null(tmp_path):
    _engine(tmp_path)
    # stop 3, target 6: a driftless walk reaches the stop first 6 / (3 + 6) = 2/3 of the time
    _arm(tmp_path, "s1", pnl=-2.4, exit_reason="stop")
    _arm(tmp_path, "s2", minute=5, pnl=-2.4, exit_reason="stop")
    _arm(tmp_path, "t1", minute=10, exit_reason="target")
    _arm(tmp_path, "h1", minute=15, pnl=0.1, exit_reason=exploration.HOLD_EXIT)   # not a barrier exit
    e = _arm_row(_learning(tmp_path)[1])
    assert (e.barrier_exits, e.stop_exits) == (3, 2)
    assert e.stop_share == pytest.approx(2 / 3) and e.stop_null == pytest.approx(2 / 3)


def test_censored_trades_stay_out_of_the_excursion_rows(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "c", censored=True, mfe_r=5.0, mae_r=-0.1, excursion_approx=False)
    _arm(tmp_path, "k", minute=5, mfe_r=1.0, mae_r=-0.1, excursion_approx=False)
    e = _arm_row(_learning(tmp_path)[1])
    assert e.trades == 1 and e.mfe_r_median == pytest.approx(1.0)


def test_excursions_are_read_per_arm_and_per_family_with_the_control_beside_them(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "e1", mfe_r=1.0, mae_r=-0.2, excursion_approx=False)
    _arm(tmp_path, "c1", CONTROL, minute=5, mfe_r=0.5, mae_r=-0.7, excursion_approx=False)
    rep, lr = _learning(tmp_path)
    assert [e.key for e in lr.excursion_families] == ["exploration / session_vwap_fade", "control / random_bracket"]
    assert [e.key for e in lr.excursion_arms] == ["exploration / session_vwap_fade / S1A",
                                                  "control / random_bracket / S0"]
    md = format_markdown(rep)
    assert "Excursions (MFE / MAE" in md and "exploration / session_vwap_fade / S1A" in md
    assert "random-walk null" in md


def test_an_arm_with_no_recorded_excursion_says_not_recorded_with_the_count(tmp_path):
    _engine(tmp_path)
    _arm(tmp_path, "old")
    rep, lr = _learning(tmp_path)
    e = _arm_row(lr)
    assert (e.recorded, e.not_recorded, e.mfe_r_median) == (0, 1, None)
    assert "not recorded (1)" in format_markdown(rep)
