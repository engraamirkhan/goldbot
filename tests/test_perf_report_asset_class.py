"""Owner directive 2026-11-25: the performance report breaks the learning closes down by asset class and by family x
asset class (net, trades, win rate, money per trade, mean R and t), lists a family split across classes, and never
lets a voided label reach a class row (PR #442 rule)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.db import Database
from goldbot.engine import exploration
from goldbot.engine.exploration import ExplorationRecord
from goldbot.ops.perf_report import DIMENSIONS, MIN_TRADES, build_performance_report, format_markdown

NOW = pd.Timestamp("2026-11-20 09:00", tz="UTC")
START, END = "2026-11-16", "2026-11-20"
ACC = "icm-probe"


@pytest.fixture(autouse=True)
def _accounts(enabled_accounts):
    enabled_accounts(ACC)


def _arm(state: Path, cid: str, arm: str, symbol: str, pnl: float, minute: int) -> None:
    sent = pd.Timestamp("2026-11-16 08:00", tz="UTC") + pd.Timedelta(hours=3 * minute)   # spread over the days
    rec = ExplorationRecord(
        client_order_id=cid, account_id=ACC, arm_id=arm, family="x", tf="5m", side=1, lots=0.01, signal_utc=sent,
        sent_utc=sent, atr_usd=3.0, stop_usd=3.0, target_usd=6.0, hold_s=3600, requested_price=1.0, spread_usd=0.0,
        fill_price=1.0, stop_loss=2.0, cost=0.0, exit_utc=sent + pd.Timedelta(minutes=10), exit_price=1.0,
        exit_reason="target", swap=0.0, commission=0.0, pnl=pnl, censored=False, fx_acct_per_usd=0.8, symbol=symbol)
    with Database.open(state, "aux").write() as conn:
        exploration.LEDGER.upsert(conn, rec)


def _state(tmp_path: Path) -> Path:
    (tmp_path / f"engine_{ACC}.json").write_text(json.dumps({"account": ACC, "mode": "demo", "currency": "GBP",
                                                             "usd_to_account": 0.8, "ts": NOW.timestamp()}))
    for i in range(MIN_TRADES):
        _arm(tmp_path, f"g{i}", "ZZ1", "XAUUSD", 1.0 if i % 2 else 0.6, i)          # +0.4 R mean, gold
        _arm(tmp_path, f"f{i}", "ZZ1-EURUSD", "EURUSD", -1.0 if i % 2 else -0.6, i)  # -0.4 R mean, fx
    _arm(tmp_path, "void", "ZZ1-UST10Y", "UST10Y", -30.0, 31)                       # a voided label: bond, -15 R
    with Database.open(tmp_path, "aux").write() as conn:
        conn.execute("INSERT INTO exploration_label_void VALUES ('void', 'v2', 'non-gold on gold bars', 0)")
    return tmp_path


def test_learning_closes_by_asset_class_and_family_x_class_flag_a_split_and_leave_voided_labels_out(tmp_path):
    rep = build_performance_report(_state(tmp_path), start=START, end=END, now=NOW)
    [a] = rep.accounts
    lr = a.learning
    assert lr is not None
    by = {g.key: g for g in lr.asset_classes}
    assert set(by) == {"metal", "fx"}                                  # the voided bond close is in no class row
    metal = by["metal"]
    assert (metal.trades, metal.win_rate) == (MIN_TRADES, 1.0) and metal.mean_r == pytest.approx(0.4)
    assert metal.net_pnl == pytest.approx(0.8 * MIN_TRADES) and metal.mean_pnl == pytest.approx(0.8)
    assert metal.t_r is not None and metal.t_r > 0
    fam = {g.key for g in lr.family_classes}
    assert len(fam) == 2 and all(k.endswith((" / metal", " / fx")) for k in fam)
    assert lr.class_splits == ["x"]
    md = format_markdown(rep)
    assert "| asset class |" in md and "Split (significant) across classes" in md and "(t " in md


def test_the_record_trades_have_asset_class_and_family_class_dimensions_from_their_symbol():
    """Code review of PR #445 (M1): a non-gold engine's closed trade records its symbol; it is classed by it."""
    from goldbot.ops.gates_phase import ClosedTrade
    from goldbot.ops.perf_report import _dims
    assert {"asset_class", "family_class"} <= set(DIMENSIONS)
    base: dict[str, Any] = dict(account_id="icm-demo", broker="icm", mode="demo", exit_utc=NOW, ret=0.001, pnl=1.0,
                equity_before=1000.0, family="box_break")
    fx = _dims(ClosedTrade(**base, symbol="EURUSD"), {}, None, None)
    gold = _dims(ClosedTrade(**base), {}, None, None)
    assert (fx["asset_class"], fx["family_class"]) == ("fx", "box_break / fx")
    assert gold["asset_class"] == "metal"


def test_a_groups_t_is_clustered_by_account_and_day_like_the_other_readers(tmp_path):
    """Code review of PR #445 (L): the class rows' t is the CR1 clustered t (account x opportunity day)."""
    from goldbot.ops.class_breakdown import clustered_t
    rep = build_performance_report(_state(tmp_path), start=START, end=END, now=NOW)
    [a] = rep.accounts
    assert a.learning is not None
    metal = next(g for g in a.learning.asset_classes if g.key == "metal")
    rs = [(0.5 if i % 2 else 0.3, ("icm-probe", str((pd.Timestamp("2026-11-16 08:00", tz="UTC")
                                                       + pd.Timedelta(hours=3 * i + 2, minutes=10)).date())))
          for i in range(MIN_TRADES)]
    assert metal.t_r == clustered_t(rs)
