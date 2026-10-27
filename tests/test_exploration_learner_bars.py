"""The nightly exploration learner must find the account's own 1m bars (brain 2026-10-27: 0 of 349 candidates were
ever labelled and every bandit run had n = 0, silently: the learner read `bars_1m` with source=<account>, which on the
brain holds only the reference feed; the account's minutes live in `bars_1m_live` (source = broker, the broker's
symbol, e.g. XAUUSD+) and `bars_1m_broker` (source = account)). These tests use a real Store so a fake that ignores
the table name can never hide that again. Also: the minutes are point in time (a minute or a correction that became
visible after the cutoff is never read), and the health check that FAILs when trades closed but the learner did not
label or write weights."""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from goldbot.config import load_settings
from goldbot.data.account_bars import account_bars_1m
from goldbot.data.store import Store
from goldbot.db.connection import Database
from goldbot.ops import health, jobs
from goldbot.research import exploration_data as ex
from tests.test_health import make_ctx

T0 = pd.Timestamp("2026-10-26 08:00", tz="UTC")
SLOT = pd.Timestamp("2026-10-26 23:10", tz="UTC")


def _m1(start: pd.Timestamp, n: int, price: float = 2000.0, spread: float = 0.2,
        visible_lag: pd.Timedelta = pd.Timedelta(minutes=1)) -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="min")
    out = pd.DataFrame({"ts_utc": ts, "visible_at": ts + visible_lag})
    for side, off in (("bid", -spread / 2), ("ask", spread / 2)):
        for c in ("open", "high", "low", "close"):
            out[f"{side}_{c}"] = price + off
    out["tick_count"] = 10.0
    return out


def _acc(account_id: str, broker: str, symbol: str, arms: tuple[Any, ...] = ()) -> Any:
    return SimpleNamespace(account_id=account_id, broker=broker, symbol=symbol, server_tz="Europe/Athens",
                           exploration=SimpleNamespace(arms=arms))


def _cand(db: Database, cid: str, account: str, broker: str, decision: pd.Timestamp = T0) -> None:
    ex.record_candidate(db, candidate_id=cid, account_id=account, broker=broker, arm_id="S1A", family="vwap",
                        style="scalp", tf="1m", side=1, decision_utc=decision, stop_dist=2.0, target_dist=2.0,
                        max_hold_s=600, taken=True, mu=0.5, branch="random", u=0.9)


# ---------------------------------------------------------------------------------------------- the reader
def test_the_accounts_minutes_come_from_its_brokers_live_feed_then_its_terminal_m1_never_the_reference(tmp_path):
    store = Store(tmp_path / "data")
    store.append("bars_1m", _m1(T0, 60, price=1.0), source="dukascopy")                        # reference: never
    store.append("bars_1m_live", _m1(T0, 60, price=9.0), source="icm", symbol="XAUUSD")         # another broker
    store.append("bars_1m_live", _m1(T0, 30, price=2000.0), source="vantage", symbol="XAUUSD+")
    store.append("bars_1m_broker", _m1(T0, 60, price=3000.0), source="vantage-demo", symbol="XAUUSD+")
    got = account_bars_1m(store, account_id="vantage-demo", broker="vantage", symbol="XAUUSD+",
                          start=T0, end=T0 + pd.Timedelta(hours=1), as_of=SLOT)
    assert len(got) == 60 and got["ts_utc"].is_monotonic_increasing and not got["ts_utc"].duplicated().any()
    assert (got["bid_open"].iloc[:30] == 2000.0 - 0.1).all()          # the live feed first
    assert (got["bid_open"].iloc[30:] == 3000.0 - 0.1).all()          # the terminal's M1 fills its hole


def test_a_minute_or_a_correction_visible_after_the_cutoff_is_never_read(tmp_path):
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", _m1(T0, 10), source="icm")
    late = _m1(T0 + pd.Timedelta(minutes=10), 5, visible_lag=pd.Timedelta(hours=20))          # written long after
    store.append("bars_1m_live", late, source="icm")
    cutoff = T0 + pd.Timedelta(hours=1)
    got = account_bars_1m(store, account_id="icm-demo", broker="icm", symbol="XAUUSD", start=T0, end=cutoff,
                          as_of=cutoff)
    assert len(got) == 10
    # the terminal's M1 of those minutes was visible in time: it serves them, not the late live rows
    store.append("bars_1m_broker", _m1(T0 + pd.Timedelta(minutes=10), 5, price=2100.0), source="icm-demo")
    got = account_bars_1m(store, account_id="icm-demo", broker="icm", symbol="XAUUSD", start=T0, end=cutoff,
                          as_of=cutoff)
    assert len(got) == 15 and (got["bid_open"].iloc[10:] == 2100.0 - 0.1).all()


def test_no_feed_gives_an_empty_frame(tmp_path):
    got = account_bars_1m(Store(tmp_path / "data"), account_id="x", broker="y", symbol="XAUUSD", start=T0,
                          end=SLOT, as_of=SLOT)
    assert got.empty


# ---------------------------------------------------------------------------------------------- the nightly run
def test_the_nightly_learner_labels_from_the_live_minutes_when_bars_1m_has_only_the_reference(tmp_path):
    """The brain's state on 2026-10-27, reproduced: bars_1m holds Dukascopy only, the accounts' minutes are in
    bars_1m_live (vantage under XAUUSD+) and bars_1m_broker."""
    store = Store(tmp_path / "data")
    store.append("bars_1m", _m1(T0, 120), source="dukascopy")
    store.append("bars_1m_live", _m1(T0, 120), source="icm", symbol="XAUUSD")
    store.append("bars_1m_broker", _m1(T0, 120), source="vantage-demo", symbol="XAUUSD+")
    db = Database.open(tmp_path, "aux")
    _cand(db, "i1", "icm-demo", "icm")
    _cand(db, "p1", "icm-probe", "icm")
    _cand(db, "v1", "vantage-demo", "vantage")
    arm = SimpleNamespace(arm_id="S1A", style="scalp", control_share=None)
    accounts = [_acc("icm-demo", "icm", "XAUUSD", (arm,)), _acc("icm-probe", "icm", "XAUUSD", (arm,)),
                _acc("vantage-demo", "vantage", "XAUUSD+", (arm,))]
    ctx: Any = SimpleNamespace(settings=load_settings(), store=store, state_dir=tmp_path, accounts=accounts)
    rep = jobs.exploration_learn(ctx, SLOT)
    assert rep["labelled"] == {"icm-demo": 1, "icm-probe": 1, "vantage-demo": 1}
    assert rep["no_bars"] == []
    obs = ex.read_obs(db).set_index("candidate_id")
    assert obs["label_sim_r"].notna().all() and obs["labelled_ns"].notna().all()
    for broker in ("icm", "vantage"):
        assert rep["brokers"][broker]["weights"] == {"S1A": 0.3}        # one arm: its cap


def test_an_account_with_pending_candidates_and_no_minutes_is_reported_never_silent(tmp_path, caplog):
    db = Database.open(tmp_path, "aux")
    _cand(db, "i1", "icm-demo", "icm")
    ctx: Any = SimpleNamespace(settings=load_settings(), store=Store(tmp_path / "data"), state_dir=tmp_path,
                          accounts=[_acc("icm-demo", "icm", "XAUUSD")])
    rep = jobs.exploration_learn(ctx, SLOT)
    assert rep["labelled"] == {"icm-demo": 0} and rep["no_bars"] == ["icm-demo"]
    assert "no 1m bars" in caplog.text


# ---------------------------------------------------------------------------------------------- the health check
NOW = pd.Timestamp("2026-10-07 14:00", tz="UTC")        # tests/test_health.NOW


def _obs(path: Path, rows: list[tuple[str, str, int | None, int | None]],
         runs: Sequence[tuple[int, str]] = ()) -> None:
    """(candidate, broker, exit_ns, labelled_ns) rows and (run_ns, broker) weights runs in a minimal aux.db."""
    c = sqlite3.connect(path / "aux.db")
    c.execute("CREATE TABLE exploration_obs (candidate_id TEXT, broker TEXT, taken INTEGER, exit_ns INTEGER, "
              "labelled_ns INTEGER, quarantine TEXT)")
    c.execute("CREATE TABLE exploration_broker_weights (run_ns INTEGER, broker TEXT, arm_id TEXT)")
    c.executemany("INSERT INTO exploration_obs VALUES (?, ?, 1, ?, ?, NULL)", rows)
    c.executemany("INSERT INTO exploration_broker_weights VALUES (?, ?, 'S1A')", runs)
    c.commit()
    c.close()


def _ns(hours_ago: float) -> int:
    return int((NOW - pd.Timedelta(hours=hours_ago)).value)


def test_learning_fails_when_trades_closed_a_day_ago_and_nothing_was_labelled_since(tmp_path):
    _obs(tmp_path, [("a", "icm", _ns(30), None), ("b", "icm", _ns(2), None)], runs=[(_ns(20), "icm")])
    c = health.check_exploration_learning(make_ctx(tmp_path))
    assert c.status == "fail" and "icm" in c.reason and "labelled" in c.reason


def test_learning_fails_when_no_weights_run_followed_a_closed_trade(tmp_path):
    _obs(tmp_path, [("a", "vantage", _ns(30), _ns(20))], runs=[(_ns(40), "vantage")])
    c = health.check_exploration_learning(make_ctx(tmp_path))
    assert c.status == "fail" and "vantage" in c.reason and "weights" in c.reason


def test_learning_is_ok_when_labels_and_weights_followed_every_trade_closed_a_day_ago(tmp_path):
    _obs(tmp_path, [("a", "icm", _ns(30), _ns(20)), ("b", "icm", _ns(1), None)], runs=[(_ns(20), "icm")])
    c = health.check_exploration_learning(make_ctx(tmp_path))
    assert c.status == "ok", c.reason


def test_learning_is_ok_with_no_trade_closed_long_enough_ago_and_without_aux_db(tmp_path):
    assert health.check_exploration_learning(make_ctx(tmp_path)).status == "ok"
    _obs(tmp_path, [("a", "icm", _ns(3), None)])
    assert health.check_exploration_learning(make_ctx(tmp_path)).status == "ok"


def test_the_weekend_gap_between_friday_and_monday_nightlies_does_not_fail(tmp_path):
    """Sunday-evening closes are labelled at Monday 23:10 (nightly_costs runs on weekdays): within the grace."""
    _obs(tmp_path, [("a", "icm", _ns(25), None)], runs=[(_ns(60), "icm")])
    assert health.check_exploration_learning(make_ctx(tmp_path)).status == "ok"
