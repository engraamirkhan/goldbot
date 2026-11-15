"""The replay-parity monitor (performance-analyst spec 2026-11-15; docs/research/
prereg-amendment-2026-11-15-replay-only-controls.md section 3): closed gold fills against their r2 replay label, read
in the EVAL-2 S5 form (day-clustered), so a replay-only control's twin stays honest.

Rule as registered: gap per fill = side x (exit - fill) / stop_usd - label_mid_r; agreement = the fill's exit reason
against the label's barrier (time, weekend and broker exits count as time); window = the latest >= 300 fills over
>= 10 session days per broker x TF band and pooled. FIRE when the day-clustered 95 % CI of the mean gap lies wholly
outside [-0.02, +0.02] R, or agreement is below 90 % in two consecutive non-overlapping 300-fill windows."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.db.connection import Database
from goldbot.engine import exploration
from goldbot.engine.exploration import ExplorationRecord
from goldbot.research import daily_review as dr
from goldbot.research import exploration_data as ed
from goldbot.research import replay_parity as rp
from goldbot.research import review_actions as ra
from goldbot.research.catalogue import costs as cc
from goldbot.research.eval2 import forward as fw
from tests.test_daily_review import HOSTS, Sink, _run

pytestmark = pytest.mark.clock_at("2026-10-30T12:00:00Z")
T0 = pd.Timestamp("2026-10-01 09:00", tz="UTC")


def _frame(gaps: np.ndarray, *, days: int, agree: np.ndarray | None = None, broker: str = "icm",
           tf: str = "15m") -> pd.DataFrame:
    """Synthetic parity rows: fill i on session day i * days // n, in exit order."""
    n = len(gaps)
    day = [f"2026-10-{1 + i * days // n:02d}" for i in range(n)]
    agree = np.ones(n, bool) if agree is None else agree
    return pd.DataFrame({"candidate_id": [f"c{i}" for i in range(n)], "broker": broker, "tf": tf,
                         "session_day": day, "exit_ns": np.arange(n, dtype=np.int64),
                         "gap_r": gaps, "exit_barrier": "stop",
                         "label_barrier": np.where(agree, "stop", "target")})


def _day_noisy(n: int, days: int, mean: float, spread: float = 0.15) -> np.ndarray:
    """Gaps whose day means alternate mean +- spread: the day-clustered se is about spread / sqrt(days)."""
    return np.array([mean + spread * (-1) ** (i * days // n) for i in range(n)])


# ------------------------------------------------------------------------------------------------ the rule
def test_a_parity_passing_sample_does_not_fire_where_the_naive_point_rule_would():
    f = _frame(_day_noisy(320, 16, 0.03), days=16)
    assert abs(f["gap_r"].mean()) > rp.TOL_R                         # the naive 0.02 point rule would fire
    [read] = [r for r in rp.reads(f) if r.scope == "icm:15m-30m"]
    assert read.ci_lo_r is not None and read.ci_hi_r is not None
    assert read.status == "PASS" and read.n >= rp.MIN_FILLS and read.ci_lo_r < rp.TOL_R < read.ci_hi_r


def test_a_broken_sample_fires_on_the_mean_gap():
    f = _frame(_day_noisy(320, 16, 0.30), days=16)
    reads = {r.scope: r for r in rp.reads(f)}
    lo = reads["icm:15m-30m"].ci_lo_r
    assert reads["icm:15m-30m"].status == "FIRE" and lo is not None and lo > rp.TOL_R
    assert reads["pooled"].status == "FIRE"


def test_a_fill_worse_than_its_replay_fires_too():
    f = _frame(_day_noisy(320, 16, -0.30), days=16)
    [read] = [r for r in rp.reads(f) if r.scope == "icm:15m-30m"]
    assert read.status == "FIRE" and read.ci_hi_r is not None and read.ci_hi_r < -rp.TOL_R


def test_barrier_disagreement_in_two_consecutive_300_fill_windows_fires():
    agree = np.arange(600) % 10 >= 2                                  # 80 % in both windows
    f = _frame(_day_noisy(600, 30, 0.0), days=30, agree=agree)
    [read] = [r for r in rp.reads(f) if r.scope == "icm:15m-30m"]
    assert read.status == "FIRE" and read.agreement == pytest.approx(0.8) and read.agreement_prev == pytest.approx(0.8)


def test_barrier_disagreement_in_the_latest_window_alone_does_not_fire():
    agree = np.r_[np.ones(300, bool), np.arange(300) % 10 >= 2]
    f = _frame(_day_noisy(600, 30, 0.0), days=30, agree=agree)
    [read] = [r for r in rp.reads(f) if r.scope == "icm:15m-30m"]
    assert read.status == "PASS" and read.agreement_prev == pytest.approx(1.0)


@pytest.mark.parametrize(("n", "days"), [(299, 20), (400, 9)])
def test_under_300_fills_or_10_session_days_is_insufficient_and_never_fires(n, days):
    f = _frame(_day_noisy(n, days, 0.5), days=days)
    [read] = [r for r in rp.reads(f) if r.scope == "icm:15m-30m"]
    assert read.status == "INSUFFICIENT"


def test_the_window_is_the_latest_fills_reaching_300_and_10_days_so_old_breakage_ages_out():
    gaps = np.r_[np.full(400, 0.6), _day_noisy(320, 16, 0.0)]
    f = _frame(gaps, days=36)
    [read] = [r for r in rp.reads(f) if r.scope == "icm:15m-30m"]
    assert read.status == "PASS" and read.n < 400


def test_time_weekend_and_broker_exits_count_as_time():
    assert rp.exit_barrier("stop") == rp.exit_barrier("engine_stop_close") == "stop"
    assert rp.exit_barrier("target") == "target"
    for why in ("exploration_hold", "time_exit", "weekend_close_loser", "broker", "manual", None):
        assert rp.exit_barrier(why) == "time"


# ------------------------------------------------------------------------------------------------ the population
def _label(db: Database, cid: str, *, mid_r: float, barrier: str) -> None:
    with db.write() as conn:
        conn.execute("INSERT INTO exploration_replay (candidate_id, replay_version, label_exit_ns, sim_r, mid_r, barrier, "
                     "entry_price, exit_price, entry_charge_usd, exit_charge_usd, cost_model_version, "
                     "cost_effective_ns, labelled_ns) VALUES (?, 'r2', 1, ?, ?, ?, 2000, 2001, 0, 0, 'v', 1, 1)",
                     (cid, mid_r, mid_r, barrier))


def _fill(db: Database, cid: str, *, at: pd.Timestamp, side: int = 1, fill: float = 2000.0, exit_price: float = 2003.0,
          stop: float = 3.0, reason: str = "target", symbol: str = "XAUUSD", closed: bool = True,
          host: str = "icm-probe", tf: str = "15m") -> None:
    ed.record_candidate(db, candidate_id=cid, account_id=host, broker=HOSTS[host], arm_id="IEX1H", family="f",
                        style="intraday", tf=tf, side=side, decision_utc=at, stop_dist=stop, target_dist=stop,
                        max_hold_s=3600, taken=True, is_control=True, branch="random", u=0.5, mu=1.0)
    rec = ExplorationRecord(client_order_id=cid, account_id=host, arm_id="IEX1H", family="f", tf=tf, side=side,
                            lots=0.01, signal_utc=at, sent_utc=at, atr_usd=stop, stop_usd=stop, target_usd=stop,
                            hold_s=3600, position_id=1, fill_price=fill,
                            exit_utc=at + pd.Timedelta(minutes=30) if closed else None,
                            exit_price=exit_price if closed else None, exit_reason=reason if closed else None,
                            pnl=1.0 if closed else None, symbol=symbol)
    with db.write() as conn:
        exploration.LEDGER.upsert(conn, rec)


def test_the_population_is_closed_gold_fills_not_excluded_joined_to_their_r2_label(tmp_path):
    db = Database.open(tmp_path, "aux")
    _fill(db, "keep", at=T0, side=-1, fill=2000.0, exit_price=1997.0, reason="target")
    _label(db, "keep", mid_r=0.9, barrier="target")
    _fill(db, "silver", at=T0, symbol="XAGUSD")
    _label(db, "silver", mid_r=1.0, barrier="target")
    _fill(db, "open", at=T0, closed=False)
    _label(db, "open", mid_r=1.0, barrier="target")
    _fill(db, "nolabel", at=T0)
    _fill(db, "void", at=T0)
    _label(db, "void", mid_r=1.0, barrier="target")
    with db.write() as conn:
        conn.execute("INSERT INTO evidence_exclusions (client_order_id, kind, account_id, scope, reason, span_ref, "
                     "criterion_version, decided_ns, blind) VALUES ('void', 'exploration', 'icm-probe', '[]', 'r', "
                     "'s', 'c', 1, 1)")
    got = rp.parity_fills(db, ["icm"])
    assert list(got["candidate_id"]) == ["keep"]
    row = got.iloc[0]
    assert row["gap_r"] == pytest.approx(-1 * (1997.0 - 2000.0) / 3.0 - 0.9)
    assert row["exit_barrier"] == "target" and row["label_barrier"] == "target" and row["broker"] == "icm"


# ------------------------------------------------------------------------------------------------ what a fire does
def _broken(db: Database, n: int = 320, days: int = 16) -> None:
    for i in range(n):
        at = T0 + pd.Timedelta(days=i * days // n, minutes=i % 50)
        cid = f"b{i}"
        _fill(db, cid, at=at, fill=2000.0, exit_price=2003.0, reason="target")    # +1 R at the fill
        _label(db, cid, mid_r=0.5 + 0.15 * (-1) ** (i * days // n), barrier="target")   # the replay says +0.5 R


def test_a_fire_alerts_records_an_automatic_review_action_and_marks_the_twin_reads_unvalidated(tmp_path):
    db = Database.open(tmp_path, "aux")
    _broken(db)
    review = _run(tmp_path, sink=Sink())
    fired = [p for p in review.parity if p.status == "FIRE"]
    assert {p.scope for p in fired} >= {"icm:15m-30m", "pooled"}
    assert any(i.kind == "parity" for i in review.incidents)
    with db.connection() as conn:
        rows = conn.execute("SELECT kind, automatic FROM review_actions").fetchall()
    assert rows and all(k == "replay_parity" and a == 1 for k, a in rows)
    assert {"icm:15m-30m", "pooled"} <= rp.revoked(db)
    _run(tmp_path, sink=Sink())                                         # a rerun records nothing twice
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM review_actions").fetchone()[0] == len(rows)


def test_a_dry_run_reads_parity_and_records_nothing(tmp_path):
    db = Database.open(tmp_path, "aux")
    _broken(db)
    review = _run(tmp_path, dry_run=True)
    assert any(p.status == "FIRE" for p in review.parity)
    assert rp.revoked(db) == frozenset()


def test_a_revocation_is_never_lifted_by_a_later_passing_read(tmp_path):
    db = Database.open(tmp_path, "aux")
    rp.record_fire(db, [rp.ParityRead(scope="icm:15m-30m", broker="icm", band="15m-30m", n=300, n_days=10,
                                      mean_gap_r=0.3, ci_lo_r=0.2, ci_hi_r=0.4, status="FIRE", reason="t")],
                   day="2026-10-22", now=pd.Timestamp("2026-10-23", tz="UTC"))
    review = _run(tmp_path, sink=Sink())                                 # no fills at all: nothing fires
    assert not [p for p in review.parity if p.status == "FIRE"]
    assert rp.revoked(db) == {"icm:15m-30m"}


def test_a_revoked_band_is_unvalidated_for_the_forward_read_and_the_deciding_cost():
    revoked = frozenset({"icm:15m-30m"})
    assert fw.revoked_for(revoked, "icm", "15m") and fw.revoked_for(revoked, "icm", "30m")
    assert not fw.revoked_for(revoked, "icm", "1h") and not fw.revoked_for(revoked, "vantage", "15m")
    assert fw.revoked_for(frozenset({"pooled"}), "vantage", "1h")
    units = pd.DataFrame({"broker": ["icm", "icm"], "tf": ["15m", "1h"], "fill_minus_replay_r": [0.1, 0.1],
                          "entry_fill_minus_replay_r": [0.0, 0.0], "exit_kind": ["stop", "stop"]})
    got = fw.apply_s5(units, {("icm", "15m-30m"): True, ("icm", "1h-4h"): True}, revoked=revoked)
    assert list(got["s5_ok"]) == [False, True] and np.isnan(got["fill_minus_replay_r"].iloc[0])


def test_a_revoked_band_is_charged_its_s5_gap_even_when_s5_passed(monkeypatch):
    pin = dict(cc._pin("XAUUSD", "15m"))
    pin["s5_passed"] = ["vantage:15m"]
    monkeypatch.setattr(cc, "_pin", lambda inst, tf: pin)
    kw: dict[str, Any] = dict(side=1, price=2000.0, atr_decision=7.0, stop_atr=1.0, t_fill_ns=0,
                              t_exit_ns=60 * 10**9, stop_exit_share=0.5, stop_entry_share=0.0, window="forward",
                              broker="vantage", costs=cc.default_model())
    assert cc.deciding_cost("XAUUSD", "15m", **kw).s5_gap_r == 0.0
    assert cc.deciding_cost("XAUUSD", "15m", s5_revoked=True, **kw).s5_gap_r > 0.0


def test_the_feedback_loop_check_fails_on_a_recent_fire(tmp_path):
    from goldbot.ops.checks import loop
    db = Database.open(tmp_path, "aux")
    now = pd.Timestamp("2026-10-23 07:00", tz="UTC")
    assert loop._parity(tmp_path, now).verdict == "ok"
    rp.record_fire(db, [rp.ParityRead(scope="pooled", broker="pooled", band="all", n=300, n_days=10,
                                      mean_gap_r=0.3, ci_lo_r=0.2, ci_hi_r=0.4, status="FIRE", reason="t")],
                   day="2026-10-22", now=now)
    item = loop._parity(tmp_path, now + pd.Timedelta(hours=1))
    assert item.verdict == "fail" and "pooled" in item.reason
    later = loop._parity(tmp_path, now + pd.Timedelta(days=rp.ALERT_DAYS + 1))
    assert later.verdict == "ok" and "pooled" in later.reason          # still named: the revocation stands


def test_the_review_action_kind_is_automatic():
    assert "replay_parity" in ra.AUTOMATIC_KINDS


def test_review_record_round_trips_with_parity_reads(tmp_path):
    db = Database.open(tmp_path, "aux")
    _broken(db)
    review = _run(tmp_path, sink=Sink())
    again = dr.load_review(tmp_path, review.day)
    assert again is not None and again.parity == review.parity
