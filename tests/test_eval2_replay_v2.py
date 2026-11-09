"""EVAL-2 Lane 5A, replay v2 (docs/research/eval2-method-2026-11-09.md section 5; prereg-2026-11-09-eval2.md section 5).

"Per broker, per leg (entry, stop exit, target exit, time exit), by session: charge max(0, adverse upper 95 % bound of
the measured slippage mean) once the cell has >= min_fills, else the $0.15 prior. Favourable slippage is never
credited." "Stop exit price = level - side x measured stop overshoot (its own term, not double-charged with the stop
leg)." "1m/5m deadline = decision + delay + max_hold." "Void every non-gold v1 label ...; every reader filters by
instrument." "`label_sim_r` is never overwritten." Quant vantage ruling 2026-11-09: vantage-demo fills stops and
targets at the level, so its exit legs are never measured from demo; "any real-money vantage hurdle uses the larger of
the ICM-measured exit slip per leg and session and the stop-slip prior"."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.db.connection import Database
from goldbot.db.migrations import AUX, compat_back_to, migrate
from goldbot.engine.exploration import LEDGER, ExplorationRecord
from goldbot.execution import costs as C
from goldbot.research import exploration_data as ex

pytestmark = pytest.mark.usefixtures("unknown_arms_are_gold")     # made-up arm ids read as gold (tests/conftest.py)
T0 = pd.Timestamp("2026-10-05 08:00", tz="UTC")                   # a Monday, London session
PRIOR = ex.SLIPPAGE_FLOOR_USD
V1 = ex.ReplayCosts(broker="icm", version="c1", effective_utc=T0 - pd.Timedelta(days=1), slippage_usd=0.15,
                    commission_rt_per_oz=0.07)


def _db(tmp_path: Path) -> Database:
    return Database.open(tmp_path, "aux")


def _m1(start: pd.Timestamp, mids: list[tuple[float, float, float]], spread: float = 0.2) -> pd.DataFrame:
    rows = []
    for i, (o, h, lo) in enumerate(mids):
        r: dict[str, Any] = {"ts_utc": start + pd.Timedelta(minutes=i)}
        for side, off in (("bid", -spread / 2), ("ask", spread / 2)):
            r.update({f"{side}_open": o + off, f"{side}_high": h + off, f"{side}_low": lo + off,
                      f"{side}_close": o + off})
        rows.append(r)
    return pd.DataFrame(rows)


def _v2(charges: dict[str, float] | None = None, broker: str = "icm") -> ex.ReplayCostsV2:
    return ex.ReplayCostsV2(broker=broker, version="r2-test", effective_utc=T0 - pd.Timedelta(days=1),
                            charges=charges or {}, commission_rt_per_oz=0.07)


def _cand(db: Database, cid: str, *, arm: str = "S1a", broker: str = "icm", account: str = "icm-probe",
          decision: pd.Timestamp = T0, tf: str = "1m", taken: bool = True, side: int = 1, stop: float = 2.0,
          target: float = 2.0, hold_s: int = 600) -> None:
    ex.record_candidate(db, candidate_id=cid, account_id=account, broker=broker, arm_id=arm, family="vwap",
                        style="scalp", tf=tf, side=side, decision_utc=decision, stop_dist=stop, target_dist=target,
                        max_hold_s=hold_s, taken=taken, mu=0.5, branch="random", u=0.9)


def _label_v1(db: Database, cid: str, sim: float) -> None:
    with db.write() as conn:
        dec = conn.execute("SELECT decision_ns FROM exploration_obs WHERE candidate_id = ?", (cid,)).fetchone()[0]
        conn.execute("UPDATE exploration_obs SET label_sim_r = ?, label_mid_r = ?, label_exit_ns = ?, "
                     "cost_model_version = 'c1' WHERE candidate_id = ?", (sim, sim, dec + 600 * 10**9, cid))


def _ledger(db: Database, cid: str, *, account: str = "icm-probe", side: int = 1, q: float = 2000.0,
            fill: float = 2000.0, reason: str | None = "stop", exit_price: float | None = 1997.9,
            stop: float = 2.0, target: float = 2.0, symbol: str = "XAUUSD", censored: bool = False,
            at: pd.Timestamp = T0) -> None:
    rec = ExplorationRecord(client_order_id=cid, account_id=account, arm_id="S1a", family="vwap", tf="1m", side=side,
                            lots=0.01, signal_utc=at, sent_utc=at + pd.Timedelta(seconds=5), atr_usd=stop,
                            stop_usd=stop, target_usd=target, hold_s=600, requested_price=q, fill_price=fill,
                            exit_utc=at + pd.Timedelta(minutes=5) if reason else None, exit_price=exit_price,
                            exit_reason=reason, censored=censored, symbol=symbol)
    with db.write() as conn:
        LEDGER.upsert(conn, rec)


def _legs(leg: str, adverse: list[float], *, start: pd.Timestamp = T0, side: int = 1) -> pd.DataFrame:
    """Leg rows with the given adverse slippage ($/oz, + = worse), one per day from `start` (London hours)."""
    return pd.DataFrame({"ts_utc": [start + pd.Timedelta(days=i) for i in range(len(adverse))], "leg": leg,
                         "side": side, "level": 2000.0, "requested": 2000.0,
                         "filled": [2000.0 + side * a for a in adverse]})


def _fixed(costs: ex.ReplayCostsV2) -> ex.CostOfDay:
    """One model for every session day (a test shortcut; the nightly run uses `daily_costs_v2`)."""
    return lambda _start: costs


# ---------------------------------------------------------------------------------------------- schema
def test_migration_27_adds_the_versioned_replay_and_void_tables_additively_and_both_refuse_rewrites(tmp_path):
    m = AUX[26]
    assert (m.version, m.name, m.additive) == (27, "exploration_replay_v2", True)
    assert compat_back_to(AUX, 27) == compat_back_to(AUX, 26)
    with sqlite3.connect(tmp_path / "aux.db") as conn:
        migrate(conn, "aux")
        conn.execute("INSERT INTO exploration_replay (candidate_id, replay_version, label_exit_ns, sim_r, mid_r, "
                     "barrier, entry_price, exit_price, entry_charge_usd, exit_charge_usd, cost_model_version, "
                     "cost_effective_ns, labelled_ns) VALUES ('c', 'r2', 1, 0.1, 0.2, 'stop', 1, 1, 0, 0, 'v', 1, 1)")
        conn.execute("INSERT INTO exploration_label_void VALUES ('c', 'r1', 'why', 1)")
        for sql in ("UPDATE exploration_replay SET sim_r = 9", "DELETE FROM exploration_replay",
                    "UPDATE exploration_label_void SET reason = 'x'", "DELETE FROM exploration_label_void"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                conn.execute(sql)


# ---------------------------------------------------------------------------------------------- leg table
def test_a_measured_leg_charges_the_upper_95_bound_of_its_adverse_mean_and_a_thin_cell_the_prior():
    flat = C.leg_slippage_table(_legs("stop", [0.09] * 60), prior_usd=PRIOR, min_fills=50)["london:stop"]
    assert (flat.n, flat.from_prior) == (60, False)
    assert flat.mean == pytest.approx(0.09) and flat.charged == pytest.approx(0.09)    # no spread: bound = mean
    rng = np.random.default_rng(1)
    noisy = C.leg_slippage_table(_legs("stop", list(0.09 + rng.normal(0, 0.5, 60))), prior_usd=PRIOR,
                                 min_fills=50)["london:stop"]
    assert noisy.ub95 is not None and noisy.mean is not None and noisy.ub95 > noisy.mean
    assert noisy.charged == pytest.approx(max(0.0, noisy.ub95))
    thin = C.leg_slippage_table(_legs("stop", [0.01] * 49), prior_usd=PRIOR, min_fills=50)["london:stop"]
    assert (thin.n, thin.from_prior, thin.charged) == (49, True, PRIOR)                # a lucky small cell never counts


def test_favourable_slippage_is_never_credited():
    tgt = C.leg_slippage_table(_legs("target", [-0.17] * 80), prior_usd=PRIOR, min_fills=50)["london:target"]
    assert tgt.mean == pytest.approx(-0.17) and tgt.charged == 0.0                     # give-back measured, not credited


def test_exit_legs_are_signed_by_the_closing_order_so_a_long_stopped_below_its_level_is_adverse():
    sell = C.leg_slippage_table(_legs("stop", [0.2] * 50, side=-1), prior_usd=PRIOR, min_fills=50)["london:stop"]
    assert sell.mean == pytest.approx(0.2)


def test_the_time_leg_has_no_recorded_quote_so_it_always_charges_the_prior():
    t = C.leg_slippage_table(_legs("time", [0.0] * 200), prior_usd=PRIOR, min_fills=50)
    assert t["london:time"].charged == PRIOR and t["london:time"].from_prior
    assert set(t) == {f"{s}:{leg}" for s in C.SESSIONS for leg in C.LEGS}


def test_a_real_money_vantage_exit_charges_the_larger_of_icm_measured_exit_slip_and_the_prior():
    icm = C.leg_slippage_table(pd.concat([_legs("stop", [0.30] * 60), _legs("target", [-0.2] * 60)]),
                               prior_usd=PRIOR, min_fills=50)
    assert C.real_money_exit_charge(icm, "london:stop", prior_usd=PRIOR) == pytest.approx(0.30)
    assert C.real_money_exit_charge(icm, "london:target", prior_usd=PRIOR) == PRIOR      # ICM 0 < the prior
    assert C.real_money_exit_charge({}, "asia:stop", prior_usd=PRIOR) == PRIOR
    with pytest.raises(ValueError, match="exit leg"):
        C.real_money_exit_charge(icm, "london:entry", prior_usd=PRIOR)


# ---------------------------------------------------------------------------------------------- ledger legs
def test_ledger_legs_read_entry_and_exit_slip_from_the_exploration_ledger_gold_only_counting_what_is_left_out(tmp_path):
    db = _db(tmp_path)
    _ledger(db, "long-stop", side=1, q=2000.0, fill=2000.05, reason="stop", exit_price=1997.9)     # level 1998
    _ledger(db, "short-target", side=-1, q=2000.0, fill=1999.98, reason="target", exit_price=1997.8)  # level 1998
    _ledger(db, "hold", reason="exploration_hold", exit_price=2000.5)
    _ledger(db, "no-exit", reason="stop", exit_price=None)
    _ledger(db, "trailed", reason="stop", censored=True)
    _ledger(db, "fx", symbol="EURUSD", q=1.1, fill=1.1, exit_price=1.09)
    _ledger(db, "open", reason=None, exit_price=None)
    _ledger(db, "later", at=T0 + pd.Timedelta(days=3))
    legs, counts = ex.ledger_legs(db, broker_of={"icm-probe": "icm"}, as_of=T0 + pd.Timedelta(days=1))
    adverse = {(str(r["client_order_id"]), str(r["leg"])):
               float(r["side"]) * (float(r["filled"]) - float(r["requested"])) for r in legs.to_dict("records")}
    assert adverse[("long-stop", "entry")] == pytest.approx(0.05)
    assert adverse[("long-stop", "stop")] == pytest.approx(0.10)              # sold 0.10 below its 1998 stop
    assert adverse[("short-target", "entry")] == pytest.approx(0.02)          # sold 0.02 below the quote
    assert adverse[("short-target", "target")] == pytest.approx(-0.20)        # bought 0.20 below its target
    assert ("hold", "time") not in adverse and ("hold", "entry") in adverse  # no quote at a time close
    assert not {c for c, _ in adverse} & {"fx", "later"}
    assert ("trailed", "stop") not in adverse and ("no-exit", "stop") not in adverse
    assert counts["no_exit_price"] == 1 and counts["non_gold"] == 1 and counts["censored"] == 1
    assert set(legs["broker"]) == {"icm"}


def test_ledger_legs_leave_out_fills_void_for_the_cost_cells(tmp_path):
    from goldbot.research import evidence_exclusions as ev
    db = _db(tmp_path)
    _ledger(db, "void")
    _ledger(db, "kept")
    ev.record(db, [ev.Exclusion(client_order_id="void", kind="exploration", account_id="icm-probe",
                                scope=ev.EXPLORATION_SCOPE, reason="test", span_ref="s", decided_utc=T0)])
    legs, counts = ex.ledger_legs(db, broker_of={"icm-probe": "icm"}, as_of=T0 + pd.Timedelta(days=1))
    assert set(legs["client_order_id"]) == {"kept"} and counts["evidence_void"] == 1


def test_replay_costs_v2_takes_each_brokers_own_entry_legs_and_never_fits_vantage_exits_from_vantage_demo(tmp_path):
    legs = pd.concat([_legs("entry", [0.0] * 60).assign(broker="icm"),
                      _legs("stop", [0.40] * 60, side=-1).assign(broker="icm"),
                      _legs("entry", [0.05] * 60).assign(broker="vantage"),
                      _legs("stop", [0.0] * 60, side=-1).assign(broker="vantage"),       # filled at the level
                      _legs("target", [0.0] * 60, side=-1).assign(broker="vantage")])
    icm = ex.replay_costs_v2(V1, legs, before=T0 + pd.Timedelta(days=70), min_fills=50)
    van = ex.replay_costs_v2(V1.model_copy(update={"broker": "vantage"}), legs, before=T0 + pd.Timedelta(days=70),
                             min_fills=50)
    assert icm.charge("entry", "london") == pytest.approx(0.0) and icm.charge("stop", "london") == pytest.approx(0.40)
    assert van.charge("entry", "london") == pytest.approx(0.05)                 # its own entry leg is real
    assert van.charge("stop", "london") == pytest.approx(0.40)                  # max(ICM 0.40, prior), never its 0
    assert van.charge("target", "london") == PRIOR                              # max(ICM unmeasured -> prior, prior)
    assert icm.charge("time", "asia") == PRIOR
    assert icm.commission_rt_per_oz == V1.commission_rt_per_oz and icm.broker == "icm"
    assert icm.effective_utc <= T0 + pd.Timedelta(days=70) and "r2" in icm.version


# ---------------------------------------------------------------------------------------------- the v2 path
def test_replay_v2_charges_the_measured_entry_and_exit_legs_and_the_stop_once_as_a_price_overshoot():
    flat = (2000.0, 2000.0, 2000.0)
    costs = _v2({"london:entry": 0.02, "london:stop": 0.10, "london:target": 0.03})
    kw: dict[str, Any] = dict(side=1, decision_utc=T0, stop_dist=2.0, target_dist=2.0, max_hold_s=600, tf="15m",
                              costs=costs)
    stop = ex.replay_label_v2(_m1(T0, [flat, (2000.0, 2000.0, 1997.0)] + [flat] * 10), **kw)
    assert stop is not None and stop.barrier == "stop"
    entry = 2000.1                                                         # ask open
    assert stop.exit_price == pytest.approx(entry - 2.0 - 0.10)            # level less the measured overshoot
    assert stop.entry_charge_usd == pytest.approx(0.02) and stop.exit_charge_usd == 0.0   # never charged twice
    assert stop.sim_r == pytest.approx((-2.0 - 0.10 - 0.02 - 0.07) / 2.0)
    tgt = ex.replay_label_v2(_m1(T0, [flat, (2000.0, 2003.0, 2000.0)] + [flat] * 10), **kw)
    assert tgt is not None and tgt.barrier == "target" and tgt.exit_price == pytest.approx(entry + 2.0)
    assert tgt.sim_r == pytest.approx((2.0 - 0.02 - 0.03 - 0.07) / 2.0)
    v1 = ex.replay_label(_m1(T0, [flat, (2000.0, 2003.0, 2000.0)] + [flat] * 10), side=1, decision_utc=T0,
                         stop_dist=2.0, target_dist=2.0, max_hold_s=600, costs=V1)
    assert v1 is not None and v1.sim_r == pytest.approx((2.0 - 0.30 - 0.07) / 2.0)   # v1 unchanged: 2 x $0.15


def test_replay_v2_a_stop_gapped_through_fills_at_the_worse_of_the_open_and_the_level_less_overshoot():
    flat = (2000.0, 2000.0, 2000.0)
    kw: dict[str, Any] = dict(side=1, decision_utc=T0, stop_dist=2.0, target_dist=2.0, max_hold_s=600, tf="15m",
                              costs=_v2({"london:stop": 0.10}))
    gap = ex.replay_label_v2(_m1(T0, [flat, (1990.0, 1990.0, 1990.0)] + [flat] * 10), **kw)
    assert gap is not None and gap.exit_price == pytest.approx(1990.0 - 0.1)       # the bid open, not level - 0.10


def test_replay_v2_1m_and_5m_time_limits_run_from_decision_plus_send_delay_and_longer_tfs_from_the_decision():
    bars = _m1(T0, [(2000.0, 2000.0, 2000.0)] * 10 + [(2001.0, 2001.0, 2001.0)] * 5)
    kw: dict[str, Any] = dict(side=1, decision_utc=T0, stop_dist=20.0, target_dist=20.0, max_hold_s=600,
                              costs=_v2(), entry_delay_s=5.0)
    short = ex.replay_label_v2(bars, tf="1m", **kw)
    long_ = ex.replay_label_v2(bars, tf="15m", **kw)
    assert short is not None and long_ is not None
    assert short.exit_utc == T0 + pd.Timedelta(minutes=11)        # first minute at or after decision + 5 s + 600 s
    assert long_.exit_utc == T0 + pd.Timedelta(minutes=10)
    assert short.barrier == long_.barrier == "time"
    assert short.exit_charge_usd == PRIOR                          # the time leg: the prior (no quote recorded)


# ---------------------------------------------------------------------------------------------- labelling and voids
def test_label_candidates_v2_writes_one_r2_row_per_candidate_and_never_touches_label_sim_r(tmp_path):
    db = _db(tmp_path)
    _cand(db, "a")
    _cand(db, "b")
    _label_v1(db, "a", -0.4)
    bars = _m1(T0, [(2000.0, 2000.0, 2000.0), (2000.0, 2003.0, 2000.0)] + [(2000.0, 2000.0, 2000.0)] * 20)
    cutoff = T0 + pd.Timedelta(minutes=30)
    assert ex.label_candidates_v2(db, bars, _fixed(_v2()), broker="icm", cutoff=cutoff) == 2
    assert ex.label_candidates_v2(db, bars, _fixed(_v2()), broker="icm", cutoff=cutoff) == 0     # written once
    v1 = ex.read_obs(db).set_index("candidate_id")
    assert v1.loc["a", "label_sim_r"] == -0.4 and pd.isna(v1.loc["b", "label_sim_r"])    # v1 untouched
    v2 = ex.read_obs(db, replay_version="r2").set_index("candidate_id")
    assert v2["label_sim_r"].astype(float)["a"] > 0 and v2.loc["a", "label_barrier"] == "target"
    assert set(v2["cost_model_version"]) == {"r2-test"}


def test_an_r2_cost_model_effective_on_or_after_the_candidates_session_day_is_refused(tmp_path):
    db = _db(tmp_path)
    _cand(db, "a")
    bars = _m1(T0, [(2000.0, 2000.0, 2000.0)] * 20)
    late = _v2().model_copy(update={"effective_utc": ex.session_start(ex.session_day(T0))})
    with pytest.raises(ValueError, match="point in time"):
        ex.label_candidates_v2(db, bars, _fixed(late), broker="icm", cutoff=T0 + pd.Timedelta(minutes=30))


def test_label_candidates_v2_never_replays_another_instrument_on_gold_bars(tmp_path):
    db = _db(tmp_path)
    _cand(db, "gold", arm="G1")
    _cand(db, "fx", arm="S0-EURUSD")
    bars = _m1(T0, [(2000.0, 2000.0, 2000.0)] * 30)
    n = ex.label_candidates_v2(db, bars, _fixed(_v2()), broker="icm", cutoff=T0 + pd.Timedelta(minutes=30),
                               instruments={"G1": "XAUUSD", "S0-EURUSD": "EURUSD"})
    assert n == 1
    with db.connection() as conn:
        assert {r[0] for r in conn.execute("SELECT candidate_id FROM exploration_replay")} == {"gold"}


def test_every_non_gold_v1_label_is_voided_once_with_its_paired_and_beyond_20r_counts(tmp_path):
    db = _db(tmp_path)
    inst = {"G1": "XAUUSD", "S0-SUGAR": "SUGAR", "S0-NZDUSD": "NZDUSD"}
    _cand(db, "gold", arm="G1")
    _cand(db, "sugar", arm="S0-SUGAR")
    _cand(db, "nzd", arm="S0-NZDUSD", taken=False)
    _cand(db, "nzd-open", arm="S0-NZDUSD")                       # never labelled: nothing to void
    _cand(db, "gold-on-fx-ledger", arm="G1")                      # the ledger says it traded another symbol
    for cid, r in (("gold", 0.3), ("sugar", -1882.0), ("nzd", 4.0), ("gold-on-fx-ledger", 0.1)):
        _label_v1(db, cid, r)
    ex.record_fill(db, candidate_id="sugar", entry_utc=T0, exit_utc=T0 + pd.Timedelta(minutes=5),
                   entry_price=0.2, exit_price=0.21, cost_per_oz=0.0, censored=False)
    _ledger(db, "gold-on-fx-ledger", symbol="EURUSD", q=1.1, fill=1.1, exit_price=1.09)
    got = ex.void_non_gold_labels(db, instruments=inst, now=T0)
    assert got == {"voided": 3, "paired": 1, "beyond_20r": 1, "unknown_arm_labels": 0}
    assert ex.void_non_gold_labels(db, instruments=inst, now=T0)["voided"] == 0           # idempotent
    with db.connection() as conn:
        assert {r[0] for r in conn.execute("SELECT candidate_id FROM exploration_label_void WHERE "
                                           "replay_version = 'r1'")} == {"sugar", "nzd", "gold-on-fx-ledger"}
        assert conn.execute("SELECT label_sim_r FROM exploration_obs WHERE candidate_id = 'sugar'").fetchone()[0] \
            == -1882.0                                            # the stored v1 label is never rewritten


def test_no_reader_sees_a_void_label_whether_or_not_it_filters_by_instrument(tmp_path):
    db = _db(tmp_path)
    _cand(db, "gold", arm="G1")
    _cand(db, "sugar", arm="S0-SUGAR", decision=T0 + pd.Timedelta(minutes=1))
    _label_v1(db, "gold", 0.3)
    _label_v1(db, "sugar", -1882.0)
    ex.void_non_gold_labels(db, instruments={"G1": "XAUUSD", "S0-SUGAR": "SUGAR"}, now=T0)
    raw = ex.read_obs(db).set_index("candidate_id")                          # no instrument filter
    assert raw.loc["gold", "label_sim_r"] == 0.3 and pd.isna(raw.loc["sugar", "label_sim_r"])
    as_of = ex.read_obs(db, as_of=T0 + pd.Timedelta(days=1))
    assert list(as_of["candidate_id"]) == ["gold"]


def test_the_learner_names_the_replay_version_it_reads(tmp_path, monkeypatch):
    db = _db(tmp_path)
    seen: list[str] = []
    real = ex.read_obs

    def spy(*a: Any, **k: Any) -> pd.DataFrame:
        seen.append(k.get("replay_version", "<default>"))
        return real(*a, **k)
    monkeypatch.setattr(ex, "read_obs", spy)
    ex.learn(db, broker="icm", as_of=T0, learning=())
    ex.training_table(db, style="scalp", broker="icm", as_of=T0)
    assert seen and set(seen) == {ex.REPLAY_V1}


# ---------------------------------------------------------------------------------------------- S5 pairs
def test_fill_replay_pairs_are_gold_r2_only_with_the_entry_leg_in_r(tmp_path):
    db = _db(tmp_path)
    _cand(db, "a", stop=2.0)
    _cand(db, "fx", arm="S0-EURUSD")
    bars = _m1(T0, [(2000.0, 2000.0, 2000.0), (2000.0, 2003.0, 2000.0)] + [(2000.0, 2000.0, 2000.0)] * 20)
    inst = {"S1a": "XAUUSD", "S0-EURUSD": "EURUSD"}
    ex.label_candidates_v2(db, bars, _fixed(_v2({"london:entry": 0.04})), broker="icm", cutoff=T0 + pd.Timedelta(minutes=30),
                           instruments=inst)
    for cid in ("a", "fx"):
        ex.record_fill(db, candidate_id=cid, entry_utc=T0, exit_utc=T0 + pd.Timedelta(minutes=2),
                       entry_price=2000.0, exit_price=2002.1, cost_per_oz=0.07, censored=False)
    p = ex.fill_replay_pairs(db, broker="icm", instruments=inst)
    assert list(p["candidate_id"]) == ["a"] and p.loc[0, "tf"] == "1m"
    # replay entry 2000.1 (ask) + 0.04 charged; the fill paid 2000.0: 0.14 better, over the 2.0 stop
    assert p.loc[0, "entry_fill_minus_replay_r"] == pytest.approx(0.14 / 2.0)
    assert p.loc[0, "fill_r"] == pytest.approx((2.1 - 0.07) / 2.0)
    assert p["replay_r"].notna().all()


def test_on_fills_with_no_real_slippage_replay_v2_removes_the_bias_the_v1_slippage_prior_carries(tmp_path):
    """The pre-registered S5 expectation in miniature: fills that pay no slippage beat the v1 replay by its 2 x $0.15
    prior; on v2, with measured zero legs, the gap is gone (target give-back stays uncredited, none here)."""
    db = _db(tmp_path)
    flat = (2000.0, 2000.0, 2000.0)
    bars = _m1(T0, [flat, (2000.0, 2003.0, 2000.0)] + [flat] * 30)
    for i in range(5):
        _cand(db, f"c{i}", stop=2.0, target=2.0)
    cutoff = T0 + pd.Timedelta(minutes=30)
    ex.label_candidates(db, bars, V1, cutoff=cutoff)
    zero = {f"london:{leg}": 0.0 for leg in ("entry", "stop", "target")}
    ex.label_candidates_v2(db, bars, _fixed(_v2(zero)), broker="icm", cutoff=cutoff)
    for i in range(5):                                       # the broker's fill: the replay's own path, no slippage
        ex.record_fill(db, candidate_id=f"c{i}", entry_utc=T0, exit_utc=T0 + pd.Timedelta(minutes=2),
                       entry_price=2000.1, exit_price=2002.1, cost_per_oz=0.07, censored=False)
    v1 = ex.read_obs(db)
    p = ex.fill_replay_pairs(db, broker="icm")
    assert (v1["label_fill_r"] - v1["label_sim_r"]).mean() == pytest.approx(0.30 / 2.0)
    assert (p["fill_r"] - p["replay_r"]).abs().max() == pytest.approx(0.0, abs=1e-12)


# ---------------------------------------------------------------------------------------------- vantage ruling (b)
def test_stops_a_bar_gapped_through_that_still_filled_at_the_level_are_counted_as_synthetic():
    flat = (2000.0, 2000.0, 2000.0)
    bars = _m1(T0, [flat] * 4 + [(1995.0, 1995.0, 1995.0)] + [flat] * 5)       # minute 4 opens through 1998
    legs = pd.DataFrame({"client_order_id": ["synthetic", "real", "no-gap"], "leg": "stop", "side": -1,
                         "level": 1998.0, "requested": 1998.0, "filled": [1998.0, 1994.8, 1998.0],
                         "ts_utc": [T0 + pd.Timedelta(minutes=4, seconds=2)] * 2 + [T0 + pd.Timedelta(minutes=7)]})
    assert ex.synthetic_stop_fills(legs, bars) == 1


# ---------------------------------------------------------------------------------------------- the nightly run
def test_the_nightly_learner_voids_non_gold_labels_and_labels_replay_v2_from_the_ledger_leg_table(tmp_path,
                                                                                                    monkeypatch):
    from goldbot.ops import jobs
    from goldbot.research import arms as A
    from tests.test_exploration_broker import _acc, _ctx
    monkeypatch.setattr(A, "arm_instruments", lambda path=None: {"S1a": "XAUUSD", "S0-SUGAR": "SUGAR"})
    db = _db(tmp_path)
    _cand(db, "g")
    _cand(db, "s", arm="S0-SUGAR")
    _label_v1(db, "s", -1882.0)
    _ledger(db, "g")
    bars = {"icm-probe": _m1(T0, [(2000.0, 2000.0, 2000.0)] * 120)}
    rep = jobs.exploration_learn(_ctx(tmp_path, [_acc("icm-probe", "icm")], bars), T0 + pd.Timedelta(hours=1))
    assert rep["label_void"]["voided"] == 1
    r2 = rep["replay_v2"]
    assert r2["labelled"] == {"icm-probe": 1} and r2["cost_model"]["icm"].startswith("settings-priors:icm+legs-r2")
    assert r2["legs"]["icm"]["london:entry"] == 1 and r2["synthetic_stop_fills"] == {"icm-probe": 0}
    assert ex.read_obs(db, replay_version="r2").set_index("candidate_id").loc["g", "label_barrier"] == "time"
    assert r2["error"] is None


def test_a_replay_v2_failure_never_stops_the_r1_labels_or_the_learner(tmp_path, monkeypatch):
    from goldbot.ops import jobs
    from tests.test_exploration_broker import _acc, _ctx

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("ledger unreadable")
    monkeypatch.setattr(jobs, "ledger_legs", boom)
    _cand(_db(tmp_path), "g")
    bars = {"icm-probe": _m1(T0, [(2000.0, 2000.0, 2000.0)] * 120)}
    rep = jobs.exploration_learn(_ctx(tmp_path, [_acc("icm-probe", "icm")], bars), T0 + pd.Timedelta(hours=1))
    assert rep["labelled"] == {"icm-probe": 1} and "icm" in rep["brokers"]          # r1 and the learner ran
    assert rep["replay_v2"]["error"] == "ledger_legs:RuntimeError" and rep["replay_v2"]["labelled"] == {}


# ---------------------------------------------------------------------------------------------- review round 1 (8b2345c4)
def _target_bars() -> pd.DataFrame:
    flat = (2000.0, 2000.0, 2000.0)
    return _m1(T0, [flat, (2000.0, 2003.0, 2000.0)] + [flat] * 30)


def test_r2_label_is_unchanged_when_fills_on_or_after_its_session_day_are_added(tmp_path):
    """Quant C1: "Each r2 label is costed only from ledger fills before its own session day"."""
    before = _legs("entry", [0.05] * 60, start=T0 - pd.Timedelta(days=70)).assign(broker="icm")
    later = pd.concat([before, _legs("entry", [1.0] * 60, start=T0).assign(broker="icm"),
                       _legs("entry", [1.0] * 5, start=ex.session_start(ex.session_day(T0))).assign(broker="icm")])
    sims = []
    for i, legs in enumerate((before, later)):
        db = _db(tmp_path / str(i))
        _cand(db, "a")
        ex.label_candidates_v2(db, _target_bars(), ex.daily_costs_v2(V1, legs, min_fills=50), broker="icm",
                               cutoff=T0 + pd.Timedelta(minutes=30))
        r = ex.read_obs(db, replay_version="r2").set_index("candidate_id")
        sims.append((float(r["label_sim_r"].astype(float)["a"]), str(r.loc["a", "cost_model_version"])))
    assert sims[0] == sims[1]
    assert "<20261004T2100" in sims[0][1]            # the as-of (the session day's start) is in the version
    tonight = ex.replay_costs_v2(V1, later, before=T0 + pd.Timedelta(days=61), min_fills=50)
    assert tonight.charge("entry", "london") > 0.5   # the later fills do move a later model


def test_stops_that_filled_at_a_gapped_bar_open_charge_no_overshoot(tmp_path):
    """Quant C6: the stop overshoot is measured against the bar-implied price (sell close: min(level, bid open))."""
    times = [T0 - pd.Timedelta(days=80) + pd.Timedelta(days=i, minutes=3, seconds=20) for i in range(60)]
    legs = pd.DataFrame({"client_order_id": [f"s{i}" for i in range(60)], "account_id": "icm-probe",
                         "broker": "icm", "leg": "stop", "ts_utc": times, "side": -1, "level": 1998.0,
                         "requested": 1998.0, "filled": 1990.0})
    bars = pd.DataFrame([{"ts_utc": t.floor("min"), "bid_open": 1990.0, "ask_open": 1990.2} for t in times])
    gone = legs.assign(account_id="no-bars", client_order_id=lambda f: "x" + f["client_order_id"]).iloc[:3]
    adj, dropped = ex.bar_implied_stop_legs(pd.concat([legs, gone]), {"icm-probe": bars})
    assert dropped == 3 and len(adj) == 60
    assert np.allclose(adj["requested"], 1990.0) and np.allclose(adj["level"], 1998.0)
    assert ex.replay_costs_v2(V1, adj, before=T0, min_fills=50).charge("stop", "london") == 0.0
    raw = ex.replay_costs_v2(V1, legs, before=T0, min_fills=50)
    assert raw.charge("stop", "london") == pytest.approx(8.0)          # what the level alone would have charged


def test_vantage_entries_filled_at_the_quote_take_the_larger_of_icms_entry_cell_and_the_prior():
    """Quant C7: at >= 95 % of entries within $0.005 of the quote, the entry leg is not evidence either."""
    legs = pd.concat([_legs("entry", [0.0] * 60).assign(broker="vantage"),
                      _legs("entry", [0.30] * 60).assign(broker="icm")])
    van = ex.replay_costs_v2(V1.model_copy(update={"broker": "vantage"}), legs, before=T0 + pd.Timedelta(days=70),
                             min_fills=50)
    assert van.entry_at_quote_share == 1.0 and van.charge("entry", "london") == pytest.approx(0.30)
    real = pd.concat([_legs("entry", list(np.linspace(-0.2, 0.2, 60))).assign(broker="vantage")])
    van2 = ex.replay_costs_v2(V1.model_copy(update={"broker": "vantage"}), real, before=T0 + pd.Timedelta(days=70),
                              min_fills=50)
    assert van2.entry_at_quote_share is not None and van2.entry_at_quote_share < 0.95
    assert van2.charge("entry", "london") < PRIOR                      # its own spread-out entries are evidence


def test_forward_labels_use_the_cards_pinned_r2_model_and_a_nightly_refit_never_changes_them(tmp_path):
    """Quant C5: the forward replay uses the r2 model version and hash recorded in the card at forward start."""
    db = _db(tmp_path)
    _cand(db, "a")
    first = _legs("entry", [0.02] * 60, start=T0 - pd.Timedelta(days=70)).assign(broker="icm")
    card = ex.replay_costs_v2(V1, first, before=T0 - pd.Timedelta(days=1), min_fills=50)
    card_hash = ex.cost_model_hash(card)
    rows = ex.read_obs(db)
    cutoff = T0 + pd.Timedelta(minutes=30)
    pinned = ex.replay_frame_v2(_target_bars(), rows, ex.pinned_costs(card, card_hash), cutoff=cutoff)
    refit_legs = pd.concat([first, _legs("entry", [0.9] * 60, start=T0 - pd.Timedelta(days=9, hours=0)
                                         ).assign(broker="icm").iloc[:8]])
    refit = ex.replay_frame_v2(_target_bars(), rows, ex.daily_costs_v2(V1, refit_legs, min_fills=50), cutoff=cutoff)
    again = ex.replay_frame_v2(_target_bars(), rows, ex.pinned_costs(card, card_hash), cutoff=cutoff)
    assert again["sim_r"].tolist() == pinned["sim_r"].tolist() != refit["sim_r"].tolist()
    other = ex.replay_costs_v2(V1, refit_legs, before=T0 - pd.Timedelta(days=1), min_fills=50)
    with pytest.raises(ValueError, match="hash"):
        ex.pinned_costs(other, card_hash)


def test_a_replay_with_no_bar_at_its_entry_minute_is_unresolved_never_entered_at_a_later_open(tmp_path):
    """Code review of 8b2345c4: a 10-07 decision on bars from 10-10 has no entry; r1 and r2 alike."""
    decision = pd.Timestamp("2026-10-07 08:00", tz="UTC")
    bars = _m1(pd.Timestamp("2026-10-10 08:00", tz="UTC"), [(2000.0, 2000.0, 2000.0)] * 30)
    kw: dict[str, Any] = dict(side=1, decision_utc=decision, stop_dist=2.0, target_dist=2.0, max_hold_s=86400 * 7)
    assert ex.replay_label(bars, costs=V1, **kw) is None
    assert ex.replay_label_v2(bars, tf="15m", costs=_v2(), **kw) is None
    db = _db(tmp_path)
    _cand(db, "old", decision=decision, tf="15m", hold_s=86400 * 7)
    assert ex.label_candidates_v2(db, bars, _fixed(_v2()), broker="icm",
                                  cutoff=pd.Timestamp("2026-10-15", tz="UTC")) == 0


def test_every_vantage_broker_key_in_accounts_yaml_is_a_demo_exit_at_level_broker():
    """Code review of 8b2345c4: vantage, vantage247 and vantage_obs all fill stops and targets at the level."""
    import yaml

    from goldbot.research.arms import REPO_ROOT
    raw = yaml.safe_load((REPO_ROOT / "config" / "accounts.yaml").read_text())
    accounts = raw["accounts"].values() if isinstance(raw["accounts"], dict) else raw["accounts"]
    keys = {str(a["broker"]) for a in accounts if isinstance(a, dict) and str(a.get("broker", "")).startswith("vantage")}
    assert keys and keys <= C.DEMO_EXIT_AT_LEVEL


def test_a_void_failure_still_writes_the_r1_labels_but_runs_neither_r2_nor_the_learner(tmp_path, monkeypatch):
    """Code review of 8b2345c4 (LOW): if voiding fails, r1 labelling still runs; only what reads labels is skipped."""
    from goldbot.ops import jobs
    from tests.test_exploration_broker import _acc, _ctx

    def boom(*a: Any, **k: Any) -> Any:
        raise sqlite3.OperationalError("locked")
    monkeypatch.setattr(jobs, "void_non_gold_labels", boom)
    _cand(_db(tmp_path), "g")
    bars = {"icm-probe": _m1(T0, [(2000.0, 2000.0, 2000.0)] * 120)}
    rep = jobs.exploration_learn(_ctx(tmp_path, [_acc("icm-probe", "icm")], bars), T0 + pd.Timedelta(hours=1))
    assert rep["labelled"] == {"icm-probe": 1} and rep["brokers"] == {}
    assert rep["label_void"] == {"error": "label_void:OperationalError"} and rep["replay_v2"]["labelled"] == {}


def test_fill_replay_pairs_report_the_fills_exit_kind_and_the_paired_rows_on_unknown_arms(tmp_path):
    """Quant C10: S5 gives the bias by exit kind and the unknown-arm label count."""
    db = _db(tmp_path)
    _cand(db, "a")
    _cand(db, "ghost", arm="GHOST")
    inst = {"S1a": "XAUUSD"}
    ex.label_candidates_v2(db, _target_bars(), _fixed(_v2()), broker="icm", cutoff=T0 + pd.Timedelta(minutes=30),
                           instruments=inst)
    _label_v1(db, "ghost", 0.1)
    with db.write() as conn:
        conn.execute("INSERT INTO exploration_replay VALUES ('ghost', 'r2', ?, 0.1, 0.1, 'stop', 1, 1, 0, 0, 'v', 1, 1)",
                     (int(T0.value) + 60 * 10**9,))
    for cid in ("a", "ghost"):
        ex.record_fill(db, candidate_id=cid, entry_utc=T0, exit_utc=T0 + pd.Timedelta(minutes=2),
                       entry_price=2000.1, exit_price=2002.1, cost_per_oz=0.07, censored=False)
    _ledger(db, "a", reason="target", exit_price=2002.1)
    p = ex.fill_replay_pairs(db, broker="icm", instruments=inst)
    assert p["exit_kind"].tolist() == ["target"] and p.attrs["unknown_arm_labels"] == 1
