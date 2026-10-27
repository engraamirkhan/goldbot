"""Item 132 (docs/design/trades-ux-2026-10-27.md §2.3, §3.4; ADR 0050): `GET /api/strategies/board`. One row per
family with its timeframes as variants: n counted toward each arm's single read (300, or its pre-registration's n),
an ETA at the 7-day pace, a status (warming, running, read, retired) and the control it is read against. Before the
read no result of an arm (money, R, hit rate, a "near its stop" band) is in the payload; after it, the recorded
verdict and the read sample's frozen figures, with the comparison against the control once the control is read too.
Lab totals only over 3+ unread arms per account and day, all sleeves of an account or none."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api import strategy_board as sb
from goldbot.api import trades_ledger as tl
from goldbot.api.app import create_app
from goldbot.api.strategy_labels import load_catalog
from goldbot.ops import gates_phase
from goldbot.ops.gates_phase import ClosedTrade
from tests.test_api_views import _login
from tests.test_trades_ledger import DAY, NOW, SCALP, _arm, _engine

H = pd.Timedelta(hours=1)


@pytest.fixture(autouse=True)
def _accounts(enabled_accounts):
    enabled_accounts(SCALP, DAY)


def _cat(tmp_path: Path, reads: dict[str, dict[str, Any]] | None = None, read_at: dict[str, int] | None = None) -> Any:
    f = tmp_path / "reads.yaml"
    f.write_text(json.dumps({"reads": reads or {}, "read_at": read_at or {}}))
    return load_catalog(reads_path=f)


def _board(state: Path, cat: Any, now: pd.Timestamp = NOW) -> Any:
    return sb.board(tl.build_ledger(state, now, cat=cat), cat, state, held_out=())


def _variant(view: Any, arm: str) -> Any:
    return next(v for f in view.families for v in f.variants if v.arm_id == arm)


def _family(view: Any, family: str) -> Any:
    return next(f for f in view.families if f.family == family)


# ------------------------------------------------------------------------------------------------ before the read
def test_an_unread_arm_shows_progress_eta_status_and_anneal_state_but_no_result(tmp_path):
    _engine(tmp_path, SCALP)
    for i in range(14):                                           # 14 counted trades in 7 days: 2 a day
        _arm(tmp_path, f"a{i}", "S1A", sent=NOW - pd.Timedelta(hours=12 * i + 2), pnl=7.77 if i % 2 else -3.33)
    view = _board(tmp_path, _cat(tmp_path))
    v = _variant(view, "S1A")
    assert (v.n_counted, v.n_read_at, v.status, v.anneal_state, v.read) == (14, 300, "running", "E", None)
    assert v.fills_7d == 14 and v.fills_today >= 1 and v.control_arm_id == "S0"
    assert v.eta_read_date == date(2026, 10, 27) + timedelta(days=143)          # 286 left at 2 a day
    text = view.model_dump_json()
    for leak in ("7.77", "3.33", "31.08", "1.85"):                    # a trade, the sum, the mean R
        assert leak not in text, leak


def test_an_arm_with_no_counted_trade_yet_is_warming(tmp_path):
    _engine(tmp_path, SCALP)
    _arm(tmp_path, "o1", "S2A", exit_after=None)                   # open: not counted yet
    v = _variant(_board(tmp_path, _cat(tmp_path)), "S2A")
    assert (v.status, v.n_counted, v.open_now, v.eta_read_date) == ("warming", 0, 1, None)


def test_a_registered_arm_without_trades_is_on_the_board_and_a_blocked_arm_is_not(tmp_path):
    view = _board(tmp_path, _cat(tmp_path))
    assert _variant(view, "S3B").status == "warming"
    assert not any(v.arm_id == "S6" for f in view.families for v in f.variants)        # spec status blocked


def test_the_read_size_is_the_preregistrations_n_else_300(tmp_path):
    view = _board(tmp_path, _cat(tmp_path, read_at={"BXR15": 150}))
    assert _variant(view, "BXR15").n_read_at == 150 and _variant(view, "I2A").n_read_at == 300


def test_an_arm_at_its_stop_reads_retired_and_a_running_loss_is_never_near_its_stop(tmp_path):
    """Quant tightening 2: before the read only "running" or "retired at its stop", never "near its stop"."""
    _engine(tmp_path, SCALP)
    for i in range(13):                                            # -13 R x 2.4: past the scalp stop of -30 R
        _arm(tmp_path, f"s{i}", "S2A", pnl=-2.4 * 2.5, sent=NOW - pd.Timedelta(minutes=30 + i))
    for i in range(8):                                             # -20 R: within 50% of its stop, still "running"
        _arm(tmp_path, f"r{i}", "S3A", pnl=-2.4 * 2.5, sent=NOW - pd.Timedelta(minutes=30 + i))
    view = _board(tmp_path, _cat(tmp_path))
    s2, s3 = _variant(view, "S2A"), _variant(view, "S3A")
    assert (s2.status, s2.anneal_state, s2.eta_read_date) == ("retired", "R", None)
    assert s3.status == "running" and "within_50pct" not in view.model_dump_json()


def test_a_family_is_one_row_with_its_timeframes_as_variants(tmp_path):
    fam = _family(_board(tmp_path, _cat(tmp_path)), "box_break_close")
    tfs = {v.timeframe for v in fam.variants}
    assert {"5m", "15m", "30m", "1h", "4h"} <= tfs and fam.family_label != "box_break_close"
    assert "Day/swing lab" in fam.accounts and "icm-demo" not in fam.accounts


def test_families_are_ordered_by_state_then_closeness_to_the_read_never_by_result(tmp_path):
    _engine(tmp_path, SCALP)
    for i in range(5):
        _arm(tmp_path, f"w{i}", "S5A", pnl=9.0, sent=NOW - pd.Timedelta(minutes=30 + i))        # winning, fewer
    for i in range(9):
        _arm(tmp_path, f"l{i}", "S1A", pnl=-1.0, sent=NOW - pd.Timedelta(minutes=30 + i))       # losing, closer
    cat = _cat(tmp_path, reads={"G1A": {"read_utc": "2026-10-20T00:00:00Z", "verdict": "proven edge", "state": "P"}})
    order = [f.family for f in _board(tmp_path, cat).families]
    assert order[0] == "level_break"                                                           # proven first
    assert order.index("session_vwap_fade") < order.index("ema_pullback")                      # closeness, not R
    states = [f.state for f in _board(tmp_path, cat).families]
    assert states.index("control") > max(i for i, s in enumerate(states) if s == "running")


# ------------------------------------------------------------------------------------------------ after the read
def _read_arm(state: Path) -> None:
    """I2A: 4 counted trades before its read (R 1.0, -0.5, 2.0, -0.5 over the 2.4 stop) and 2 after; its control I0:
    4 counted trades before."""
    _engine(state, DAY)
    t0 = pd.Timestamp("2026-10-20 08:00", tz="UTC")
    for i, r in enumerate((1.0, -0.5, 2.0, -0.5)):
        _arm(state, f"r{i}", "I2A", account=DAY, tf="15m", sent=t0 + i * H, pnl=round(2.4 * r, 2))
        _arm(state, f"c{i}", "I0", account=DAY, tf="15m", sent=t0 + i * H, pnl=round(2.4 * (0.5 if i % 2 else -0.5), 2))
    for i, r in enumerate((1.0, 0.5)):
        _arm(state, f"after{i}", "I2A", account=DAY, tf="15m", sent=NOW - (i + 2) * H, pnl=round(2.4 * r, 2))


def test_a_read_arm_shows_its_verdict_and_the_read_samples_frozen_figures_and_since_read(tmp_path):
    _read_arm(tmp_path)
    cat = _cat(tmp_path, reads={"I2A": {"read_utc": "2026-10-21T00:00:00Z", "verdict": "candidate: beats its control",
                                        "state": "C1"}}, read_at={"I2A": 4})
    v = _variant(_board(tmp_path, cat), "I2A")
    assert (v.status, v.anneal_state, v.eta_read_date) == ("read", "C1", None)
    r = v.read
    assert r is not None and r.verdict == "candidate: beats its control" and r.n == 4
    assert r.net_r_per_trade == pytest.approx(0.5) and r.hit_rate == pytest.approx(0.5)
    assert r.net_pnl == pytest.approx(4.8) and r.currency == "GBP"
    assert r.ci90 is not None and r.ci90[0] < 0.5 < r.ci90[1]
    assert r.since_read is not None and r.since_read.n == 2 and r.since_read.net_r_per_trade == pytest.approx(0.75)
    assert r.vs_control_r is None and r.vs_control_ci90 is None                # its control I0 is not read


def test_the_difference_against_the_control_shows_once_the_control_is_read_too(tmp_path):
    _read_arm(tmp_path)
    read = {"read_utc": "2026-10-21T00:00:00Z", "verdict": "candidate", "state": "C1"}
    cat = _cat(tmp_path, reads={"I2A": read, "I0": {**read, "verdict": "null", "state": "R"}}, read_at={"I2A": 4})
    r = _variant(_board(tmp_path, cat), "I2A").read
    assert r is not None and r.vs_control_r == pytest.approx(0.5)                # 0.5 - 0.0
    assert r.vs_control_ci90 is not None and r.vs_control_ci90[0] < 0.5 < r.vs_control_ci90[1]


def test_a_partly_read_family_shows_the_read_variant_and_withholds_the_unread_one(tmp_path):
    """Quant tightening 3: the unread variant's seeded R and sum appear nowhere on the board."""
    _read_arm(tmp_path)
    for i in range(3):
        _arm(tmp_path, f"m{i}", "MB15", account=DAY, tf="15m", pnl=-6.67, sent=NOW - (i + 1) * H)
    cat = _cat(tmp_path, reads={"I2A": {"read_utc": "2026-10-21T00:00:00Z", "verdict": "candidate", "state": "C1"}},
               read_at={"I2A": 4})
    view = _board(tmp_path, cat)
    assert _variant(view, "I2A").read is not None and _variant(view, "MB15").read is None
    text = view.model_dump_json()
    for leak in ("6.67", "20.01", "-2.779", "2.78"):                           # MB15's trade, sum and R
        assert leak not in text, leak


# ------------------------------------------------------------------------------------------------ totals
def _three_arms(state: Path, *, swing: bool) -> None:
    _engine(state, DAY)
    for arm, pnl in (("I2A", 1.11), ("MB15", 2.22), ("I2B", 3.33)):
        _arm(state, f"i-{arm}", arm, account=DAY, tf="15m", pnl=pnl)
    if swing:
        _arm(state, "w-W2", "W2", account=DAY, tf="4h", pnl=4.44)


def test_a_lab_sleeve_over_three_unread_arms_shows_its_money(tmp_path):
    _three_arms(tmp_path, swing=False)
    totals = {(t.account_label, t.style): t for t in _board(tmp_path, _cat(tmp_path)).lab_totals}
    t = totals[("Day/swing lab", "intraday")]
    assert (t.arms, t.n, t.net_pnl, t.withheld_reason) == (3, 3, 6.66, None)


def test_one_sleeve_under_three_arms_withholds_every_sleeve_of_that_account(tmp_path):
    """Else the short sleeve is the List's account total minus the others (a difference isolating 1-2 arms)."""
    _three_arms(tmp_path, swing=True)
    view = _board(tmp_path, _cat(tmp_path))
    day = [t for t in view.lab_totals if t.account_label == "Day/swing lab"]
    assert {t.style for t in day} == {"intraday", "swing"}
    assert all(t.net_pnl is None and t.costs is None and t.withheld_reason for t in day)
    assert "4.44" not in view.model_dump_json() and "6.66" not in view.model_dump_json()


def test_engine_strategies_show_net_money_and_r_with_n_and_a_90_ci(tmp_path):
    _engine(tmp_path, DAY)
    for i, (pnl, r) in enumerate(((3.0, 1.5), (-2.0, -1.0), (4.0, 2.0))):
        gates_phase.append_closed_trade(tmp_path, ClosedTrade(
            account_id=DAY, broker="icm", mode="demo", exit_utc=NOW - (i + 1) * H, ret=0.001, pnl=pnl,
            equity_before=10_000.0, position_id=100 + i, agent_id="box_break_close-g0-1h", side=1, lots=0.05,
            entry_utc=NOW - (i + 3) * H, entry_price=2400.0, exit_price=2401.0, exit_reason="tp", r=r,
            commission=0.35, swap=0.0, timeframe="1h", initial_sl=2398.0, final_tp=2404.0))
    eng = _board(tmp_path, _cat(tmp_path)).engine
    s = next(e for e in eng if e.kind == "strategy")
    assert (s.n, s.net_pnl, s.timeframe, s.n_today) == (3, 5.0, "1h", 3)
    assert s.net_r_per_trade == pytest.approx(2.5 / 3, abs=1e-3) and s.hit_rate == pytest.approx(2 / 3, abs=1e-3)
    assert s.ci90 is not None and s.ci90[0] < s.net_r_per_trade < s.ci90[1]


def test_learning_progress_counts_arms_by_status_and_trades_counted_this_week(tmp_path):
    _read_arm(tmp_path)
    cat = _cat(tmp_path, reads={"I2A": {"read_utc": "2026-10-21T00:00:00Z", "verdict": "candidate", "state": "C1"}})
    learning = _board(tmp_path, cat).learning
    assert learning.read == 1 and learning.proven == 0 and learning.running > 10
    assert learning.counted_7d == 2                         # I2A's 2 since the read (the rest closed 7+ days ago)


# ------------------------------------------------------------------------------------------------ the API
def test_the_board_route_needs_a_sign_in_and_never_carries_an_unread_arms_result(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    _engine(state, SCALP)
    for i, arm in enumerate(("S1A", "S2A")):
        _arm(state, f"x{i}", arm, pnl=12.34 + i)
    cat = _cat(tmp_path)
    monkeypatch.setattr(sb, "catalog", lambda: cat)
    monkeypatch.setattr(tl, "catalog", lambda: cat)
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", owner_email="o@x.io")
    c = TestClient(app)
    assert c.get("/api/strategies/board").status_code == 401
    h = _login(c, app)["viewer"]
    res = c.get("/api/strategies/board", headers=h)
    assert res.status_code == 200
    body = res.json()
    assert any(v["arm_id"] == "S1A" and v["n_counted"] == 1 for f in body["families"] for v in f["variants"])
    for leak in ("12.34", "13.34", "25.68", "5.14"):
        assert leak not in res.text, leak
