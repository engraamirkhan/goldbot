"""Quant review of a8c3a6d7 (ML1 feature snapshots): point in time by WRITE time, per-decision feed resolution, the
engine's own frame, fair batches across accounts, and bounded retries (goldbot/research/feature_snapshots.py)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars
from goldbot.data.store import Store
from goldbot.db.connection import Database
from goldbot.research import exploration_data as ex
from goldbot.research import feature_snapshots as fs
from tests.test_feature_snapshots import DAYS, DECISIONS, START, _minutes

pytestmark = pytest.mark.usefixtures("unknown_arms_are_gold")
AT = DECISIONS[0]
SLOT = START + pd.Timedelta(days=DAYS)
ACC: dict[str, Any] = {"broker_of": {"icm-probe": "icm"}, "symbol_of": {"icm-probe": "XAUUSD"}}
PRICES = [f"{s}_{c}" for s in ("bid", "ask") for c in ("open", "high", "low", "close")]


@pytest.fixture(scope="module")
def minutes() -> pd.DataFrame:
    return _minutes()


def _cand(db: Database, cid: str, at: pd.Timestamp, *, account: str = "icm-probe", tf: str = "1h",
          arm: str = "I1") -> None:
    ex.record_candidate(db, candidate_id=cid, account_id=account, broker="icm", arm_id=arm, family="box",
                        style="intraday", tf=tf, side=1, decision_utc=at, stop_dist=2.0, target_dist=2.0,
                        max_hold_s=3600, taken=False, mu=0.0, branch="blocked", u=0.9, blocked_reason="cap")


def _written(m: pd.DataFrame) -> pd.DataFrame:
    """Minutes as the writers store them now: stamped with their write time, here one second after the close (inside
    account_bars.LIVE_WRITE_GRACE: built live, so known at the close as the engine knew it)."""
    return m.drop(columns=["visible_at"]).assign(available_utc=m["ts_utc"] + pd.Timedelta(minutes=1, seconds=1))


def _same(a: fs.Snapshot | None, b: fs.Snapshot | None) -> bool:
    assert a is not None and b is not None
    return a.features.keys() == b.features.keys() and all(
        (x is None and b.features[k] is None)
        or (x is not None and b.features[k] is not None
            and bool(np.isclose(x, float(b.features[k] or 0.0), rtol=1e-9, atol=1e-12)))
        for k, x in a.features.items())


def _stored(db: Database, cid: str) -> fs.Snapshot:
    row = fs.read_snapshots(db).loc[cid].to_dict()
    return fs.Snapshot(features={str(k): None if pd.isna(v) else float(v) for k, v in row.items()}, feature_version="x",
                       available_utc_max=AT)


def _spiked(m: pd.DataFrame, ts: pd.Timestamp, by: float = 25.0) -> pd.DataFrame:
    out = m.copy()
    i = out["ts_utc"] == ts
    out.loc[i, PRICES] = out.loc[i, PRICES] + by
    return out


LATE = AT - pd.Timedelta(minutes=5)              # a minute inside the decision's own 1h bar


# ---------------------------------------------------------------------------------------------- the writers
def test_the_engines_minute_writer_stamps_each_batch_with_its_write_time(tmp_path, minutes):
    from goldbot.data.resample import BAR_COLUMNS
    from goldbot.engine.history import LIVE_TABLE, BarWriter
    store = Store(tmp_path / "data")
    before = pd.Timestamp.now("UTC")
    w = BarWriter(store, "icm", "XAUUSD")
    w.put(minutes.head(30)[BAR_COLUMNS])
    assert w.join(30)
    got = store.read(LIVE_TABLE, source="icm")
    assert "available_utc" in got.columns and (pd.to_datetime(got["available_utc"], utc=True) >= before).all()


def test_the_broker_m1_reconcile_stamps_its_minutes_with_their_write_time(tmp_path):
    from goldbot.data import crossfeed as cf
    from goldbot.data.resample import ticks_to_1m
    from tests.test_crossfeed import NOW, FakeBroker, as_broker_m1, day_ticks
    ticks = day_ticks()
    store = Store(tmp_path / "data")
    store.append("ticks", ticks, source="icm-demo", dedupe=False)
    before = pd.Timestamp.now("UTC")
    cf.reconcile_account(store, FakeBroker(as_broker_m1(ticks_to_1m(ticks))), "icm-demo", "XAUUSD", NOW)
    got = store.read(cf.BROKER_TABLE, source="icm-demo")
    assert len(got) and (pd.to_datetime(got["available_utc"], utc=True) >= before).all()


# ---------------------------------------------------------------------------------------------- point in time
def test_a_minute_written_after_the_decision_is_not_read_at_it(tmp_path, minutes):
    """A back-filled minute carries visible_at = its close; its write time decides when it was known."""
    store = Store(tmp_path / "data")
    stored = _written(_spiked(minutes, LATE))
    stored.loc[stored["ts_utc"] == LATE, "available_utc"] = AT + pd.Timedelta(hours=2)
    store.append("bars_1m_live", stored, source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    _cand(db, "c", AT)
    assert fs.snapshot_pending(db, store, as_of=SLOT, **ACC)["status"] == {"ok": 1}
    without = fs.decision_features(minutes[minutes["ts_utc"] != LATE], "1h", AT)
    assert _same(_stored(db, "c"), without)
    assert not _same(_stored(db, "c"), fs.decision_features(_spiked(minutes, LATE), "1h", AT))      # power
    assert fs.snapshot_pit(db, "c") == ("written", 0)


def test_a_minute_revised_after_the_decision_reads_the_version_known_then_from_the_next_feed(tmp_path, minutes):
    """The engine's minute was rewritten after the decision (a re-pull with new prices); the terminal's M1 had the
    minute before it. Each decision resolves feeds as of itself: the live table's revision is not visible yet, so the
    broker M1 version is read."""
    store = Store(tmp_path / "data")
    live = _written(_spiked(minutes, LATE))
    live.loc[live["ts_utc"] == LATE, "available_utc"] = AT + pd.Timedelta(hours=3)
    store.append("bars_1m_live", live, source="icm", symbol="XAUUSD")
    store.append("bars_1m_broker", _written(minutes[minutes["ts_utc"] == LATE]), source="icm-probe", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    _cand(db, "c", AT)
    fs.snapshot_pending(db, store, as_of=SLOT, **ACC)
    assert _same(_stored(db, "c"), fs.decision_features(minutes, "1h", AT))


def test_a_quarantined_minute_is_read_by_no_snapshot(tmp_path, minutes):
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", _written(_spiked(minutes, LATE)), source="icm", symbol="XAUUSD")
    store.append("bars_quarantine", _written(minutes[minutes["ts_utc"] == LATE]), source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    _cand(db, "c", AT)
    fs.snapshot_pending(db, store, as_of=SLOT, **ACC)
    assert _same(_stored(db, "c"), fs.decision_features(minutes[minutes["ts_utc"] != LATE], "1h", AT))


def test_minutes_without_a_write_time_mark_the_snapshot_pit_nominal(tmp_path, minutes):
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", minutes.drop(columns=["visible_at"]), source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    _cand(db, "c", AT)
    fs.snapshot_pending(db, store, as_of=SLOT, **ACC)
    pit, nominal = fs.snapshot_pit(db, "c")
    assert pit == "nominal" and nominal is not None and nominal > 0


# ---------------------------------------------------------------------------------------------- the engine's frame
def _engine_row(minutes: pd.DataFrame, tf: str, at: pd.Timestamp) -> dict[str, Any]:
    """engine.frames.frame on the engine's memory at `at`, through Engine._context (no Engine is started)."""
    from goldbot.engine import frames
    from goldbot.engine.runner import Engine
    primary = object()
    eng: Any = SimpleNamespace(book=primary, primary=primary, cfg=SimpleNamespace(feature_window=fs.FEATURE_WINDOW),
                               _ctx_cache={}, _resample=resample_bars)
    eng._context = lambda complete, ctf, ts: Engine._context(eng, complete, ctf, ts)
    complete = minutes[minutes["visible_at"] <= at].tail(fs.ENGINE_MINUTES).reset_index(drop=True)
    fr = frames.frame(eng, complete, tf, at)
    assert fr is not None
    row = fr.X.drop(columns=[c for c in ("ts_utc", "visible_at") if c in fr.X.columns]).iloc[-1]
    return {str(k): (None if pd.isna(v) else float(v)) for k, v in row.items()}


@pytest.mark.parametrize("tf", ["15m", "1h"])
def test_a_snapshot_is_exactly_the_engines_frame_row_on_the_same_minutes(minutes, tf):
    for at in (DECISIONS[0], DECISIONS[5] + pd.Timedelta(minutes=15)):
        snap = fs.decision_features(minutes, tf, at)
        assert snap is not None
        engine = fs.Snapshot(features=_engine_row(minutes, tf, at), feature_version="x", available_utc_max=at)
        assert _same(snap, engine)


# ---------------------------------------------------------------------------------------------- batches
def test_a_stuck_account_never_starves_a_healthy_one_and_waits_for_its_retry_date(tmp_path, minutes):
    """Quant HIGH: 700 pending bars of an account with no bars ahead of a healthy account's backlog."""
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", _written(minutes), source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    for i in range(700):
        _cand(db, f"dead-{i}", AT + pd.Timedelta(days=3, minutes=i), account="dead-acc", tf="1m")
    for i in range(3):
        _cand(db, f"lost-{i}", AT + pd.Timedelta(days=2, minutes=i), account="gone-acc", tf="1m")
    for i, at in enumerate(DECISIONS[:4]):
        _cand(db, f"ok-{i}", at)
    brokers = {"icm-probe": "icm", "dead-acc": "vantage"}
    rep = fs.snapshot_pending(db, store, broker_of=brokers, symbol_of={}, as_of=SLOT, max_decisions=600)
    assert {f"ok-{i}" for i in range(4)} <= set(fs.read_snapshots(db).index)
    assert rep["no_bars"] == ["dead-acc"] and rep["unknown_account"] == ["gone-acc"]
    again = fs.snapshot_pending(db, store, broker_of=brokers, symbol_of={}, as_of=SLOT + pd.Timedelta(hours=1))
    assert again["deferred"] == ["dead-acc", "gone-acc"] and again["no_bars"] == []
    later = fs.snapshot_pending(db, store, broker_of=brokers, symbol_of={},
                                as_of=SLOT + fs.ACCOUNT_RETRY_AFTER + pd.Timedelta(minutes=1))
    assert later["no_bars"] == ["dead-acc"]                     # tried again once its date passed


def test_the_nightly_batch_is_shared_fairly_between_accounts(tmp_path, minutes):
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", _written(minutes), source="icm", symbol="XAUUSD")
    store.append("bars_1m_live", _written(minutes), source="icm2", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    for i, at in enumerate(DECISIONS[:5]):
        _cand(db, f"a{i}", at)
        _cand(db, f"b{i}", at, account="icm-two")
    rep = fs.snapshot_pending(db, store, broker_of={"icm-probe": "icm", "icm-two": "icm2"}, symbol_of={},
                              as_of=SLOT, max_decisions=4)
    got = set(fs.read_snapshots(db).index)
    assert rep["decisions"] == 4 and len({c for c in got if c[0] == "a"}) == 2 == len({c for c in got if c[0] == "b"})


# ---------------------------------------------------------------------------------------------- retries
def test_too_little_history_is_retried_a_bounded_number_of_nights_then_recorded(tmp_path, minutes):
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", _written(minutes), source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    _cand(db, "early", START + pd.Timedelta(days=1))
    for night in range(fs.MAX_ATTEMPTS):
        rep = fs.snapshot_pending(db, store, as_of=SLOT + night * pd.Timedelta(days=1), **ACC)
        assert rep["status"] == {"insufficient_bars": 1}
        assert rep["written"] == (1 if night == fs.MAX_ATTEMPTS - 1 else 0)
    assert fs.snapshot_pending(db, store, as_of=SLOT + 9 * pd.Timedelta(days=1), **ACC)["decisions"] == 0
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM exploration_feature_snapshots").fetchone()[0] == "insufficient_bars"


def test_a_retry_the_same_night_is_not_attempted(tmp_path, minutes):
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", _written(minutes), source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    _cand(db, "early", START + pd.Timedelta(days=1))
    fs.snapshot_pending(db, store, as_of=SLOT, **ACC)
    assert fs.snapshot_pending(db, store, as_of=SLOT + pd.Timedelta(hours=1), **ACC)["decisions"] == 0


def test_the_reference_minutes_the_engine_loads_after_a_restart_fill_an_early_history(tmp_path, minutes):
    """Its own broker minutes start after the decision's history window; the release (dukascopy) minutes the engine
    warm-starts on fill it, so the retry succeeds once they are in the store."""
    store = Store(tmp_path / "data")
    own_from = START + pd.Timedelta(days=30)
    store.append("bars_1m_live", _written(minutes[minutes["ts_utc"] >= own_from]), source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    at = own_from + pd.Timedelta(hours=2)
    _cand(db, "c", at)
    assert fs.snapshot_pending(db, store, as_of=SLOT, **ACC)["status"] == {"insufficient_bars": 1}
    store.append("bars_1m", minutes[minutes["ts_utc"] < own_from].drop(columns=["visible_at"]), source="dukascopy",
                 symbol="XAUUSD")
    rep = fs.snapshot_pending(db, store, as_of=SLOT + pd.Timedelta(days=1), **ACC)
    assert rep["status"] == {"ok": 1}
    assert _same(_stored(db, "c"), fs.decision_features(minutes, "1h", at))
    assert fs.snapshot_pit(db, "c")[0] == "nominal"            # the release minutes carry no write time


# ---------------------------------------------------------------------------------------------- coverage
def test_coverage_counts_gold_candidates_only(tmp_path):
    db = Database.open(tmp_path, "aux")
    _cand(db, "g", AT)
    _cand(db, "fx", AT, arm="FX1")
    cov = fs.coverage(db, since=START, instruments={"I1": "XAUUSD", "FX1": "EURUSD"})
    assert cov["candidates"] == 1
