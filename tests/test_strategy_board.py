"""Item 132 (docs/design/trades-ux-2026-10-27.md §2.3, §3.4; ADR 0050): `GET /api/strategies/board`. One row per
family with its timeframes as variants: n counted toward each arm's single read (300, or its pre-registration's n),
an ETA at the 7-day pace, a status (warming, running, read, retired) and the control it is read against. Before the
read no result of an arm (money, R, hit rate, a "near its stop" band) is in the payload; after it, the recorded
verdict and the read sample's frozen figures, with the comparison against the control once the control is read too.
Lab totals show their money whatever the arms' reads (ADR 0053, backlog 139); a family's results before its read are
the ranking's (tests/test_strategy_ranking.py)."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml
from fastapi.testclient import TestClient

from goldbot.api import strategy_board as sb
from goldbot.api import trades_ledger as tl
from goldbot.api.app import create_app
from goldbot.api.strategy_labels import READS_PATH, load_catalog
from goldbot.ops import gates_phase
from goldbot.ops.gates_phase import ClosedTrade
from tests.test_api_views import _login
from tests.test_trades_ledger import DAY, NOW, SCALP, _arm, _engine

H = pd.Timedelta(hours=1)


@pytest.fixture(autouse=True)
def _accounts(enabled_accounts):
    enabled_accounts(SCALP, DAY, "vantage-demo")


def _cat(tmp_path: Path, reads: dict[str, dict[str, Any]] | None = None, **over: Any) -> Any:
    """A catalog with these reads and the evaluator's shipped read sizes and units (config/arm_reads.yaml), with
    `over` replacing its other keys."""
    raw = yaml.safe_load(READS_PATH.read_text())
    f = tmp_path / "reads.yaml"
    f.write_text(json.dumps({**raw, "reads": reads or {}, **over}))
    return load_catalog(reads_path=f)


def _board(state: Path, cat: Any, now: pd.Timestamp = NOW) -> Any:
    return sb.board(tl.build_ledger(state, now, cat=cat), cat, state, held_out=())


def _variant(view: Any, arm: str) -> Any:
    return next(v for f in view.families for v in f.variants if v.arm_id == arm)


def _family(view: Any, family: str) -> Any:
    return next(f for f in view.families if f.family == family)


# ------------------------------------------------------------------------------------------------ before the read
def test_an_unread_arm_shows_progress_eta_status_and_anneal_state_and_leaves_its_result_to_the_ranking(tmp_path):
    _engine(tmp_path, SCALP)
    for i in range(14):                                           # 14 counted trades in 7 days: 2 a day
        _arm(tmp_path, f"a{i}", "S1A", sent=NOW - pd.Timedelta(hours=12 * i + 2), pnl=7.77 if i % 2 else -3.33)
    view = _board(tmp_path, _cat(tmp_path))
    v = _variant(view, "S1A")
    assert (v.n_counted, v.n_read_at, v.status, v.anneal_state, v.read) == (14, 300, "running", "E", None)
    assert v.fills_7d == 14 and v.fills_today >= 1 and v.control_arm_id == "S0"
    assert v.eta_read_date == date(2026, 10, 27) + timedelta(days=143)          # 286 left at 2 a day
    text = view.model_dump_json(include={"families"})               # the lab total carries the money (139)
    for leak in ("7.77", "3.33", "31.08", "1.85"):                    # a trade, the sum, the mean R: the ranking's
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


def test_the_read_size_is_the_evaluators_and_a_control_without_a_read_has_none(tmp_path):
    view = _board(tmp_path, _cat(tmp_path))
    assert _variant(view, "WFRI").n_read_at == 170 and _variant(view, "I2A").n_read_at == 300     # Fridays; H-61
    assert _variant(view, "S0R").n_read_at == 100                                               # its stop exits
    assert _variant(view, "S0").n_read_at is None and _variant(view, "S0").eta_read_date is None
    assert not _family(view, "hurst_band_break").pooled                         # read per arm: no pooled unit


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
                                        "state": "C1"}})
    v = _variant(_board(tmp_path, cat), "I2A")
    assert (v.status, v.anneal_state, v.eta_read_date) == ("read", "C1", None)
    r = v.read
    assert r is not None and r.verdict == "candidate: beats its control" and r.n == 4
    assert r.net_r_per_trade == pytest.approx(0.5) and r.hit_rate == pytest.approx(0.5)
    assert r.net_pnl == pytest.approx(4.8) and r.currency == "GBP"
    assert r.ci90 is not None and r.ci90[0] < 0.5 < r.ci90[1]
    assert r.since_read is not None and r.since_read.n == 2 and r.since_read.net_r_per_trade == pytest.approx(0.75)
    assert r.vs_control_r is None and r.vs_control_ci90 is None                # nothing recorded


def test_the_difference_against_the_control_is_only_the_evaluators_recorded_one_never_computed(tmp_path):
    """Quant 2026-10-27 condition 1: the prereg defines the comparison (H-81: day-paired family minus control, the
    controls never read); a view computing its own (here 0.5 - 0.0 from the seeded trades) would be a second read."""
    _read_arm(tmp_path)
    read = {"read_utc": "2026-10-21T00:00:00Z", "verdict": "candidate", "state": "C1"}
    cat = _cat(tmp_path, reads={"I2A": read, "I0": {**read, "verdict": "null", "state": "R"}})
    r = _variant(_board(tmp_path, cat), "I2A").read
    assert r is not None and r.vs_control_r is None and r.vs_control_ci90 is None     # control read, nothing recorded
    cat = _cat(tmp_path, reads={"I2A": {**read, "vs_control_r": 0.31, "vs_control_ci90": [0.02, 0.6]}})
    r = _variant(_board(tmp_path, cat), "I2A").read
    assert r is not None and (r.vs_control_r, r.vs_control_ci90) == (0.31, (0.02, 0.6))


# ------------------------------------------------------------------------------------------------ pooled read units
def _pooled(state: Path) -> None:
    """H-81 nr4_break on two brokers: Z6D (Day/swing lab) 3 counted closes, Z6V (Vantage lab) 2."""
    _engine(state, DAY)
    _engine(state, "vantage-demo")
    for i, pnl in enumerate((4.41, -2.4, 3.97)):
        _arm(state, f"zd{i}", "Z6D", account=DAY, sent=NOW - (i + 2) * H, pnl=pnl)
    for i, pnl in enumerate((-2.4, 5.13)):
        _arm(state, f"zv{i}", "Z6V", account="vantage-demo", sent=NOW - (i + 2) * H, pnl=pnl)


def test_an_h81_family_shows_one_pooled_progress_and_eta_and_its_arms_no_per_arm_read(tmp_path):
    """Quant 2026-10-27 condition 2: H-81 reads each family POOLED at its 300th fill (config/read_units.yaml)."""
    _pooled(tmp_path)
    view = _board(tmp_path, _cat(tmp_path))
    fam = _family(view, "nr4_break")
    assert [u.unit_id for u in fam.pooled] == ["H-81:Z6"]
    u = fam.pooled[0]
    assert (u.n_counted, u.n_read_at, u.status, u.read) == (5, 300, "running", None) and u.eta_read_date is not None
    assert set(u.arm_ids) == {"Z6D", "Z6V"}
    for arm in ("Z6D", "Z6V"):
        v = _variant(view, arm)
        assert v.pooled and v.read_unit == "H-81:Z6" and v.eta_read_date is None and v.read is None
    assert not _variant(view, "ZCD").pooled                                    # the control is its own unit
    text = view.model_dump_json()
    for leak in ("4.41", "5.13", "8.71", "0.726"):                             # trades, pooled sum, pooled mean R
        assert leak not in text, leak


def test_a_read_pooled_family_shows_its_frozen_pooled_sample_and_unblinds_its_arms(tmp_path):
    _pooled(tmp_path)
    read = {"read_utc": NOW.isoformat(), "verdict": "no edge after costs", "state": "R", "vs_control_r": -0.04,
            "vs_control_ci90": [-0.2, 0.12]}
    cat = _cat(tmp_path, reads={"H-81:Z6": read})
    u = _family(_board(tmp_path, cat), "nr4_break").pooled[0]
    assert u.status == "read" and u.read is not None and u.read.n == 5 and u.read.vs_control_r == -0.04
    assert u.read.net_pnl == pytest.approx(8.71)
    assert cat.is_read("Z6D") and cat.is_read("Z6V") and not cat.is_read("ZCD")


def test_h80s_pooled_n_is_bxrs_fills_not_the_virtual_exits_on_them(tmp_path):
    _engine(tmp_path, DAY)
    for i, arm in enumerate(("BXR1H", "BXT1H", "BXF1H", "BXR15")):
        _arm(tmp_path, f"bx{i}", arm, account=DAY, tf="1h", sent=NOW - (i + 2) * H, pnl=1.0)
    u = _family(_board(tmp_path, _cat(tmp_path)), "box_next").pooled[0]
    assert (u.unit_id, u.n_counted, u.n_read_at) == ("H-80:BXR", 2, 300)


def test_s0rs_n_is_its_stop_exits(tmp_path):
    _engine(tmp_path, SCALP)
    for i, reason in enumerate(("stop", "target", "stop", "time_exit")):
        _arm(tmp_path, f"s0r{i}", "S0R", tf="1m", sent=NOW - (i + 2) * H, pnl=-1.0, reason=reason)
    v = _variant(_board(tmp_path, _cat(tmp_path)), "S0R")
    assert (v.n_counted, v.n_read_at, v.pooled) == (2, 100, False) and v.eta_read_date is not None


def test_units_whose_arms_disagree_on_n_read_nothing_and_pool_nothing(tmp_path):
    bad = _cat(tmp_path, reads={"H-81:Z6": {"read_utc": NOW.isoformat(), "verdict": "x"}},
               read_units={"H-81:Z6": {"arms": ["Z6D", "Z6V", "S1A"]}}, read_at={"Z6D": 300, "Z6V": 300, "S1A": 200})
    assert not bad.is_read("Z6D") and bad.unit_of("Z6D") == "Z6D"


def test_a_partly_read_family_shows_the_read_variant_and_withholds_the_unread_one(tmp_path):
    """Quant tightening 3: the unread variant's seeded R and sum appear nowhere on the board."""
    _read_arm(tmp_path)
    for i in range(3):
        _arm(tmp_path, f"m{i}", "MB15", account=DAY, tf="15m", pnl=-6.67, sent=NOW - (i + 1) * H)
    cat = _cat(tmp_path, reads={"I2A": {"read_utc": "2026-10-21T00:00:00Z", "verdict": "candidate", "state": "C1"}})
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


def test_a_lab_sleeves_money_shows_whatever_its_arms_reads(tmp_path):
    _three_arms(tmp_path, swing=True)
    totals = {(t.account_label, t.style): t for t in _board(tmp_path, _cat(tmp_path)).lab_totals}
    t = totals[("Day/swing lab", "intraday")]
    assert (t.arms, t.n, t.net_pnl) == (3, 3, 6.66) and totals[("Day/swing lab", "swing")].net_pnl == 4.44


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
def test_the_board_route_needs_a_sign_in_and_carries_progress_and_lab_money(tmp_path, monkeypatch):
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
    assert [t["net_pnl"] for t in body["lab_totals"]] == [25.68]
