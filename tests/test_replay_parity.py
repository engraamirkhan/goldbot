"""The replay-parity monitor (performance-analyst spec 2026-11-15; docs/research/
prereg-amendment-2026-11-15-replay-only-controls.md section 3, as amended by the quant and code reviews of 0fb8fbff):
closed gold fills against their r2 replay label, in the EVAL-2 S5 form, so a replay-only control's twin stays honest.

Rule as registered: the population and the gap are S5's (`exploration_data.fill_replay_pairs`): gap = fill_r -
replay_r (r2 sim_r, both over the candidate's own stop_dist), the entry leg alone for a broker whose demo exits fill at
the level. The gap fires on a one-sided e-process per direction beyond +-0.02 R (`eval2.forward.e_execution`, one
term per session day), Bonferroni over the streams formed, once a scope has >= 300 fills over >= 10 session days.
Agreement = the fill's exit leg against the label's barrier over the exits a label can have (stop, target, time);
FIRE below 90 % in two consecutive non-overlapping 300-fill windows spanning >= 10 session days. Scopes: broker x TF
band, and a pooled read (no 1m-5m band) that alerts only."""
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
from goldbot.research.eval2 import runner as er
from tests.test_daily_review import HOSTS, Sink, _run

pytestmark = pytest.mark.clock_at("2026-10-30T12:00:00Z")
T0 = pd.Timestamp("2026-09-10 09:00", tz="UTC")
HI = pd.Timestamp("2026-10-22 22:00", tz="UTC")


def _frame(gaps: np.ndarray, *, days: int, agree: np.ndarray | None = None, broker: str = "icm",
           tf: str = "15m", reason: Any = "stop") -> pd.DataFrame:
    """Synthetic parity rows: fill i on session day i * days // n, in exit order; the label agrees where `agree`."""
    n = len(gaps)
    day = [f"d{i * days // n:03d}" for i in range(n)]
    agree = np.ones(n, bool) if agree is None else agree
    return pd.DataFrame({"candidate_id": [f"c{i}" for i in range(n)], "broker": broker, "tf": tf,
                         "session_day": day, "exit_ns": np.arange(n, dtype=np.int64), "gap_r": gaps,
                         "exit_reason": reason, "label_barrier": np.where(agree, "stop", "target")})


def _day_noisy(n: int, days: int, mean: float, spread: float = 0.15) -> np.ndarray:
    """Gaps whose day means alternate mean +- spread."""
    return np.array([mean + spread * (-1) ** (i * days // n) for i in range(n)])


def _scope(reads: list[rp.ParityRead], scope: str) -> rp.ParityRead:
    [r] = [x for x in reads if x.scope == scope]
    return r


# ------------------------------------------------------------------------------------------------ the gap
def test_a_parity_passing_sample_does_not_fire_where_the_naive_point_rule_would():
    f = _frame(_day_noisy(320, 16, 0.03), days=16)
    assert abs(f["gap_r"].mean()) > rp.TOL_R                         # the naive 0.02 point rule would fire
    read = _scope(rp.reads(f), "icm:15m-30m")
    assert read.status == "PASS" and read.n >= rp.MIN_FILLS


def test_a_broken_sample_fires_on_the_mean_gap():
    f = _frame(_day_noisy(450, 30, 0.30), days=30)
    read = _scope(rp.reads(f), "icm:15m-30m")
    assert read.status == "FIRE" and read.e_value >= read.e_threshold and read.revokes


def test_a_fill_worse_than_its_replay_fires_too():
    f = _frame(_day_noisy(450, 30, -0.30), days=30)
    assert _scope(rp.reads(f), "icm:15m-30m").status == "FIRE"


@pytest.mark.parametrize(("n", "days"), [(299, 20), (400, 9)])
def test_under_300_fills_or_10_session_days_is_insufficient_and_never_fires(n, days):
    f = _frame(_day_noisy(n, days, 0.5), days=days)
    assert _scope(rp.reads(f), "icm:15m-30m").status == "INSUFFICIENT"


def test_under_daily_relooks_a_true_gap_of_0_fires_in_under_5_percent_of_250_day_runs_over_13_scopes():
    """Quant MEDIUM 5: the family-wise false-fire rate of the gap rule with a read every day. An e-process is valid at
    every look, so its running maximum after 250 days is the daily re-looks' worst case."""
    rng = np.random.default_rng(20261115)
    reps, scopes, days = 30, 13, 250
    threshold = rp.E_PER_STREAM * 2 * scopes
    hits = 0
    for _ in range(reps):
        for s in range(scopes):
            k = (5, 15, 30, 60)[s % 4]
            x = (rng.normal(0, 0.03, days)[:, None] + rng.normal(0, 0.3, (days, k))).ravel()
            if rp.gap_evidence(x, np.repeat(np.arange(days), k)) >= threshold:
                hits += 1
                break
    assert hits / reps < 0.05


# ------------------------------------------------------------------------------------------------ agreement
def test_barrier_disagreement_in_two_consecutive_300_fill_windows_fires():
    agree = np.arange(600) % 10 >= 2                                  # 80 % in both windows
    read = _scope(rp.reads(_frame(_day_noisy(600, 30, 0.0), days=30, agree=agree)), "icm:15m-30m")
    assert read.status == "FIRE" and read.agreement == pytest.approx(0.8) and read.agreement_prev == pytest.approx(0.8)


def test_barrier_disagreement_in_the_latest_window_alone_does_not_fire():
    agree = np.r_[np.ones(300, bool), np.arange(300) % 10 >= 2]
    read = _scope(rp.reads(_frame(_day_noisy(600, 30, 0.0), days=30, agree=agree)), "icm:15m-30m")
    assert read.status == "PASS" and read.agreement_prev == pytest.approx(1.0)


def test_barrier_disagreement_on_fewer_than_10_session_days_does_not_fire():
    agree = np.arange(600) % 10 >= 2
    read = _scope(rp.reads(_frame(np.zeros(600), days=5, agree=agree)), "icm:15m-30m")
    assert read.status != "FIRE"


def test_weekend_flats_and_other_non_label_exits_leave_agreement_and_their_share_is_reported():
    reason = np.where(np.arange(700) % 100 < 15, "weekend_flat", "stop")      # 15 % weekend flats, label says stop
    read = _scope(rp.reads(_frame(np.zeros(700), days=35, reason=reason)), "icm:15m-30m")
    assert read.status == "PASS" and read.agreement == pytest.approx(1.0)
    assert read.excluded_share == pytest.approx(0.15)


def test_the_engines_stop_close_is_a_stop_and_a_trailed_stop_is_left_out():
    assert rp.exit_leg("engine_stop_close") == rp.exit_leg("stop") == "stop"
    assert rp.exit_leg("target") == "target" and rp.exit_leg("exploration_hold") == rp.exit_leg("time_exit") == "time"
    for why in ("trail_close", "weekend_flat", "broker_close", "manual", "orphan", None):
        assert rp.exit_leg(why) is None


# ------------------------------------------------------------------------------------------------ pooled
def test_the_pooled_read_leaves_out_the_1m_5m_band_and_a_pooled_fire_revokes_nothing(tmp_path):
    scalp = _frame(_day_noisy(450, 30, 0.30), days=30, tf="1m")
    reads = rp.reads(scalp)
    assert _scope(reads, "icm:1m-5m").status == "FIRE" and _scope(reads, "pooled").n == 0
    both = pd.concat([scalp, _frame(_day_noisy(450, 30, 0.30), days=30).assign(candidate_id=lambda d: "i" + d.index.astype(str))])
    pooled = _scope(rp.reads(both), "pooled")
    assert pooled.status == "FIRE" and not pooled.revokes
    db = Database.open(tmp_path, "aux")
    assert rp.record_fire(db, [pooled], day="2026-10-22", now=HI) == [] and rp.revoked(db) == frozenset()
    assert not fw.revoked_for(frozenset({"pooled"}), "icm", "15m")


# ------------------------------------------------------------------------------------------------ the population
def _pair(db: Database, cid: str, *, at: pd.Timestamp, fill_r: float, sim_r: float, side: int = 1, stop: float = 3.0,
          reason: str = "target", barrier: str = "target", host: str = "icm-probe", tf: str = "15m",
          replay_entry: float = 2000.0, charge: float = 0.0, symbol: str = "XAUUSD") -> None:
    """One taken gold candidate with its broker fill (fill_r at its stop_dist, no cost beyond the prices), its r2
    label (sim_r, mid_r a spread above it) and its ledger row (exit reason)."""
    entry, exit_at = 2000.0, at + pd.Timedelta(minutes=30)
    ed.record_candidate(db, candidate_id=cid, account_id=host, broker=HOSTS[host], arm_id="IEX1H", family="f",
                        style="intraday", tf=tf, side=side, decision_utc=at, stop_dist=stop, target_dist=stop,
                        max_hold_s=3600, taken=True, is_control=True, branch="random", u=0.5, mu=1.0)
    exit_price = entry + side * fill_r * stop
    ed.record_fill(db, candidate_id=cid, entry_utc=at, exit_utc=exit_at, entry_price=entry, exit_price=exit_price,
                   cost_per_oz=0.0, censored=False)
    with db.write() as conn:
        conn.execute("INSERT INTO exploration_replay (candidate_id, replay_version, label_exit_ns, sim_r, mid_r, barrier, "
                     "entry_price, exit_price, entry_charge_usd, exit_charge_usd, cost_model_version, "
                     "cost_effective_ns, labelled_ns) VALUES (?, 'r2', ?, ?, ?, ?, ?, 2001, ?, 0, 'v', 1, 1)",
                     (cid, int(exit_at.value), sim_r, sim_r + 0.1, barrier, replay_entry, charge))
        exploration.LEDGER.upsert(conn, ExplorationRecord(
            client_order_id=cid, account_id=host, arm_id="IEX1H", family="f", tf=tf, side=side, lots=0.01,
            signal_utc=at, sent_utc=at, atr_usd=stop, stop_usd=stop, target_usd=stop, hold_s=3600, position_id=1,
            fill_price=entry, exit_utc=exit_at, exit_price=exit_price, exit_reason=reason, pnl=1.0, symbol=symbol))


def test_the_gap_is_fill_r_minus_the_r2_sim_r_so_spread_on_both_legs_reads_as_parity(tmp_path):
    """Quant BLOCKER 1: a fill identical to its replay except for the spread on both legs (in fill_r and in sim_r,
    never in mid_r) is parity. The old fill-vs-mid gap read -0.1 R on every fill and fired."""
    db = Database.open(tmp_path, "aux")
    for i in range(320):
        _pair(db, f"p{i}", at=T0 + pd.Timedelta(days=i * 16 // 320, minutes=i % 50), fill_r=0.9, sim_r=0.9)
    got = rp.parity_fills(db, ["icm"], before=HI)
    assert len(got) == 320 and np.allclose(got["gap_r"], 0.0)
    assert _scope(rp.reads(got), "icm:15m-30m").status == "PASS"


def test_a_broker_whose_demo_exits_fill_at_the_level_is_read_on_the_entry_leg_alone(tmp_path):
    db = Database.open(tmp_path, "aux")
    _pair(db, "v1", at=T0, fill_r=1.0, sim_r=0.5, host="vantage-demo", replay_entry=2000.3, charge=0.0)
    [row] = rp.parity_fills(db, ["vantage"], before=HI).to_dict("records")
    assert row["gap_r"] == pytest.approx(0.3 / 3.0)                  # the entry leg: + = the fill better
    assert "vantage" in fw.DEMO_EXIT_AT_LEVEL


def test_the_population_is_s5s_pairs_closed_before_the_reviewed_days_end(tmp_path):
    db = Database.open(tmp_path, "aux")
    _pair(db, "keep", at=T0, fill_r=1.0, sim_r=0.9, reason="engine_stop_close", barrier="stop")
    _pair(db, "late", at=HI + pd.Timedelta(minutes=1), fill_r=1.0, sim_r=0.9)
    _pair(db, "silver", at=T0, fill_r=1.0, sim_r=1.0, symbol="XAGUSD")
    with db.write() as conn:
        conn.execute("UPDATE exploration_obs SET arm_id = 'S0-XAGUSD' WHERE candidate_id = 'silver'")
    got = rp.parity_fills(db, ["icm"], before=HI)
    assert list(got["candidate_id"]) == ["keep"]
    row = got.iloc[0]
    assert row["gap_r"] == pytest.approx(0.1) and row["exit_reason"] == "engine_stop_close"
    assert row["label_barrier"] == "stop" and row["broker"] == "icm"
    assert set(rp.parity_fills(db, ["icm"], before=HI + pd.Timedelta(hours=1))["candidate_id"]) == {"keep", "late"}


# ------------------------------------------------------------------------------------------------ what a fire does
def _broken(db: Database, n: int = 360, days: int = 30) -> None:
    for i in range(n):
        d = i * days // n
        _pair(db, f"b{i}", at=T0 + pd.Timedelta(days=d, minutes=i % 50), fill_r=1.0,
              sim_r=0.5 + 0.15 * (-1) ** d)                            # the fill does 0.5 R better than its replay


def test_a_fire_alerts_records_an_automatic_review_action_and_marks_the_twin_reads_unvalidated(tmp_path):
    db = Database.open(tmp_path, "aux")
    _broken(db)
    review = _run(tmp_path, sink=Sink())
    fired = {p.scope for p in review.parity if p.status == "FIRE"}
    assert {"icm:15m-30m", "pooled"} <= fired
    assert any(i.kind == "parity" for i in review.incidents)
    with db.connection() as conn:
        rows = conn.execute("SELECT kind, automatic, json_extract(body, '$.subject') FROM review_actions").fetchall()
    assert rows == [("replay_parity", 1, "icm:15m-30m")]                # the pooled fire alerts only
    assert rp.revoked(db) == {"icm:15m-30m"}
    _run(tmp_path, sink=Sink())                                         # a rerun records nothing twice
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM review_actions").fetchone()[0] == 1


def test_a_dry_run_reads_parity_and_records_nothing(tmp_path):
    db = Database.open(tmp_path, "aux")
    _broken(db)
    review = _run(tmp_path, dry_run=True)
    assert any(p.status == "FIRE" for p in review.parity)
    assert rp.revoked(db) == frozenset()


def test_a_failed_fire_record_is_an_unreadable_incident_not_a_crash(tmp_path, monkeypatch):
    db = Database.open(tmp_path, "aux")
    _broken(db)

    def boom(*a: Any, **k: Any) -> list[str]:
        raise OSError("disk full")
    monkeypatch.setattr(rp, "record_fire", boom)
    review = _run(tmp_path, sink=Sink())
    assert any(i.kind == "unreadable" and "replay parity" in i.detail for i in review.incidents)
    assert any(i.kind == "parity" for i in review.incidents)


def _fire(scope: str = "icm:15m-30m") -> rp.ParityRead:
    broker, band = scope.split(":")
    return rp.ParityRead(scope=scope, broker=broker, band=band, n=300, n_days=10, e_value=900.0, e_threshold=80.0,
                         status="FIRE", revokes=True, reason="t")


def test_a_revocation_is_never_lifted_by_a_later_passing_read(tmp_path):
    db = Database.open(tmp_path, "aux")
    rp.record_fire(db, [_fire()], day="2026-10-22", now=pd.Timestamp("2026-10-23", tz="UTC"))
    review = _run(tmp_path, sink=Sink())                                 # no fills at all: nothing fires
    assert not [p for p in review.parity if p.status == "FIRE"]
    assert rp.revoked(db) == {"icm:15m-30m"}


def test_a_revoked_band_is_unvalidated_for_the_forward_read_and_the_deciding_cost():
    revoked = frozenset({"icm:15m-30m"})
    assert fw.revoked_for(revoked, "icm", "15m") and fw.revoked_for(revoked, "icm", "30m")
    assert not fw.revoked_for(revoked, "icm", "1h") and not fw.revoked_for(revoked, "vantage", "15m")
    units = pd.DataFrame({"broker": ["icm", "icm"], "tf": ["15m", "1h"], "fill_minus_replay_r": [0.1, 0.1],
                          "entry_fill_minus_replay_r": [0.0, 0.0], "exit_kind": ["stop", "stop"]})
    got = fw.apply_s5(units, {("icm", "15m-30m"): True, ("icm", "1h-4h"): True}, revoked=revoked)
    assert list(got["s5_ok"]) == [False, True] and np.isnan(got["fill_minus_replay_r"].iloc[0])


def test_a_recorded_fire_stops_the_forward_read_counting_through_the_runner(tmp_path, monkeypatch):
    """Quant BLOCKER 2: the runner's forward read takes the recorded revocations; a family with a unit in a revoked
    scope waits (no forward read counts), its fill columns blanked, even with S5 passed."""
    db = Database.open(tmp_path, "aux")
    rp.record_fire(db, [_fire()], day="2026-10-22", now=HI)
    seen: dict[str, Any] = {}
    monkeypatch.setattr(er, "harness_gate", lambda **k: "h")
    monkeypatch.setattr(er.FW, "forward_state", lambda u, **k: seen.update(units=u, **k) or "state")
    units = pd.DataFrame({"family": ["C03"], "broker": ["icm"], "tf": ["15m"], "day": ["d"], "s5_ok": [True],
                          "fill_minus_replay_r": [0.1], "entry_fill_minus_replay_r": [0.0], "exit_kind": ["stop"]})
    er.run_forward_read(units, family="C03", l3_shrunk_r=0.1, s5_passed=True, revoked=er.forward_revocations(db))
    assert seen["s5_passed"] is False and not seen["units"]["s5_ok"].any()
    assert np.isnan(seen["units"]["fill_minus_replay_r"]).all()


def test_the_forward_read_refuses_while_the_revocations_cannot_be_read(tmp_path, monkeypatch):
    db = Database.open(tmp_path, "aux")

    def broken(_db: Database) -> frozenset[str]:
        raise OSError("aux.db locked")
    monkeypatch.setattr(rp, "revoked", broken)
    with pytest.raises(er.ReadRefused, match="replay-parity"):
        er.forward_revocations(db)


def test_a_recorded_fire_charges_the_s5_gap_in_the_deciding_cost_even_when_s5_passed(tmp_path, monkeypatch):
    db = Database.open(tmp_path, "aux")
    rp.record_fire(db, [_fire("vantage:15m-30m")], day="2026-10-22", now=HI)
    pin = dict(cc._pin("XAUUSD", "15m"))
    pin["s5_passed"] = ["vantage:15m"]
    monkeypatch.setattr(cc, "_pin", lambda inst, tf: pin)
    kw: dict[str, Any] = dict(side=1, price=2000.0, atr_decision=7.0, stop_atr=1.0, t_fill_ns=0,
                              t_exit_ns=60 * 10**9, stop_exit_share=0.5, stop_entry_share=0.0, window="forward",
                              broker="vantage", costs=cc.default_model())
    assert cc.deciding_cost("XAUUSD", "15m", **kw).s5_gap_r == 0.0
    revoked = fw.revoked_for(er.forward_revocations(db), "vantage", "15m")
    assert cc.deciding_cost("XAUUSD", "15m", s5_revoked=revoked, **kw).s5_gap_r > 0.0


def test_the_feedback_loop_check_fails_on_a_recent_fire(tmp_path):
    from goldbot.ops.checks import loop
    db = Database.open(tmp_path, "aux")
    now = pd.Timestamp("2026-10-23 07:00", tz="UTC")
    assert loop._parity(tmp_path, now).verdict == "ok"
    rp.record_fire(db, [_fire()], day="2026-10-22", now=now)
    item = loop._parity(tmp_path, now + pd.Timedelta(hours=1))
    assert item.verdict == "fail" and "icm:15m-30m" in item.reason
    later = loop._parity(tmp_path, now + pd.Timedelta(days=rp.ALERT_DAYS + 1))
    assert later.verdict == "ok" and "icm:15m-30m" in later.reason      # still named: the revocation stands


def test_the_review_action_kind_is_automatic():
    assert "replay_parity" in ra.AUTOMATIC_KINDS


def test_review_record_round_trips_with_parity_reads(tmp_path):
    db = Database.open(tmp_path, "aux")
    _broken(db)
    review = _run(tmp_path, sink=Sink())
    again = dr.load_review(tmp_path, review.day)
    assert again is not None and again.parity == review.parity
