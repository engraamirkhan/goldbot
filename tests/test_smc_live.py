"""The replica live (quant review 2026-10-18, before any shadow or live use of replica columns): (1) a plain tail is
refused, (2) a state from other code or other parameters is refused, (3) the state is bootstrapped from the 2010 series
start, persisted in SQLite on every bar, and any discontinuity (a gap, a revised bar, a bar_index jump, a restart on
stale state) re-bootstraps, so every value equals the full-history run."""
from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.db.connection import Database
from goldbot.features import smc_replica as R
from goldbot.features.registry import FEATURES, FULL_HISTORY, NEEDS_STATE, build_features
from goldbot.features.smc_live import SERIES_START, ReplicaFeed
from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES

CB = R.context_bars()


@pytest.fixture(scope="module")
def series() -> pd.DataFrame:
    """15m mid bars starting the series at the pinned 2010 start."""
    b1 = ticks_to_1m(synthetic_ticks("2010-01-04", "2010-02-20", ticks_per_minute=1, seed=11))
    return mid(resample_bars(b1, "15m").reset_index(drop=True))


@pytest.fixture(scope="module")
def full(series: pd.DataFrame) -> pd.DataFrame:
    return R.replica_frame(series).reset_index(drop=True)


class Source:
    """The store as the feed sees it: the series through a bar, and a count of the bootstraps it served."""

    def __init__(self, series: pd.DataFrame):
        self.series, self.calls = series, 0

    def through(self, end: int):
        def load() -> pd.DataFrame:
            self.calls += 1
            return self.series.iloc[:end + 1].reset_index(drop=True)
        return load


def _feed(tmp_path, name: str = "xauusd-15m") -> ReplicaFeed:
    return ReplicaFeed(Database.open(tmp_path, "aux"), name)


def _step(feed: ReplicaFeed, src: Source, end: int, tail_len: int = CB + 40):
    start = max(0, end + 1 - tail_len)
    return feed.update(src.series.iloc[start:end + 1].reset_index(drop=True), start, src.through(end))


def _equal(frame: pd.DataFrame, full: pd.DataFrame, start: int, positions) -> None:
    got = frame.reset_index(drop=True)
    want = full.iloc[[start + int(i) for i in positions]].reset_index(drop=True)
    pd.testing.assert_frame_equal(got, want)


def test_the_engine_refuses_replica_features_on_a_plain_tail(series):
    name = R.enable()
    assert NEEDS_STATE in FEATURES[name].tags
    with pytest.raises(ValueError, match="carried state"):
        build_features(series.iloc[-500:].reset_index(drop=True), [name])
    assert len(build_features(series, [name], {FULL_HISTORY: True})) == len(series)    # research: the whole series
    assert not any(NEEDS_STATE in FEATURES[n].tags for n in DEFAULT_FEATURE_NAMES)      # what the engine builds


def test_a_state_from_other_code_or_other_parameters_is_refused(series):
    tail = series.iloc[:1200]
    _, state = R.replica_run(series.iloc[:1000])
    with pytest.raises(R.StaleReplicaState, match="code"):
        R.replica_run(tail, state=state.model_copy(update={"code_version": "0"}))
    with pytest.raises(R.StaleReplicaState, match="params"):
        R.replica_run(tail, state=state.model_copy(update={"params_hash": "0" * 16}))
    old = state.model_dump(mode="json")
    old.pop("code_version")
    with pytest.raises(ValidationError):                       # written before the field existed: never current
        R.ReplicaState.model_validate(old)
    assert R.ReplicaParams().digest() != R.ReplicaParams(equal_threshold=0.2).digest()


def test_bootstrap_starts_at_the_series_start_and_the_state_is_persisted_every_bar(tmp_path, series, full):
    src = Source(series)
    feed = _feed(tmp_path)
    end = 2000
    up = _step(feed, src, end)
    assert up.bootstrapped and up.reason == "no state" and src.calls == 1
    _equal(up.frame, full, end + 1 - (CB + 40), up.frame.index)
    for e in range(end + 1, end + 6):
        up = _step(feed, src, e)
        assert not up.bootstrapped and src.calls == 1 and list(up.frame.index) == [CB + 39]
        _equal(up.frame, full, e + 1 - (CB + 40), up.frame.index)
        row = feed.stored()
        assert row is not None and row["bar_index"] == e and row["bar_time"] == up.state.bar_time


def test_a_restart_resumes_from_sqlite_without_a_bootstrap(tmp_path, series, full):
    src = Source(series)
    _step(_feed(tmp_path), src, 2000)
    again = _feed(tmp_path)                                    # a new process: the same aux.db
    up = _step(again, src, 2003)
    assert not up.bootstrapped and src.calls == 1 and len(up.frame) == 3
    _equal(up.frame, full, 2004 - (CB + 40), up.frame.index)


def test_a_series_that_does_not_start_at_the_pin_is_refused(tmp_path, series):
    late = series.iloc[500:].reset_index(drop=True)
    feed = _feed(tmp_path)
    with pytest.raises(ValueError, match=f"{SERIES_START:%Y-%m-%d}"):
        feed.update(late.iloc[-400:].reset_index(drop=True), len(late) - 400, lambda: late)
    assert feed.stored() is None                               # nothing persisted from a wrong start


@pytest.mark.parametrize("break_it", ["gap", "revised", "bar_index", "stale"])
def test_any_discontinuity_rebootstraps_and_the_values_stay_exact(tmp_path, series, full, break_it):
    src = Source(series)
    feed = _feed(tmp_path)
    _step(feed, src, 2000)
    end, tail_len = 2001, CB + 40
    start = end + 1 - tail_len
    tail = series.iloc[start:end + 1].reset_index(drop=True)
    before = start
    if break_it == "gap":                                      # the engine missed bars: the snapshot is not in the tail
        end, start = 2500, 2500 + 1 - tail_len
        tail, before = series.iloc[start:end + 1].reset_index(drop=True), start
    elif break_it == "revised":                                # a bar before the snapshot came back different
        tail.loc[tail_len - 10, "close"] += 0.5
        src.series = pd.concat([series.iloc[:start], tail]).reset_index(drop=True)
    elif break_it == "bar_index":                              # the store holds one more bar before the tail
        before = start + 1
    else:                                                      # the stored state was written by other code
        with feed.db.write() as conn:
            text = conn.execute("SELECT state FROM replica_state").fetchone()[0]
            conn.execute("UPDATE replica_state SET state = ?, code_version = '0'",
                         (text.replace('"code_version":"1"', '"code_version":"0"'),))
    up = feed.update(tail, before, src.through(end))
    assert up.bootstrapped and src.calls == 2, up.reason
    truth = full if break_it != "revised" else R.replica_frame(src.series.iloc[:end + 1]).reset_index(drop=True)
    _equal(up.frame, truth, start, up.frame.index)


def test_the_last_bars_snapshots_are_kept_so_a_rule_can_resume_from_before_its_decision_bar(tmp_path, series):
    from goldbot.features.smc_live import KEEP_STATES
    src = Source(series)
    feed = _feed(tmp_path)
    for e in range(2000, 2000 + KEEP_STATES + 4):
        _step(feed, src, e)
    with feed.db.connection() as conn:
        kept = [r[0] for r in conn.execute("SELECT bar_index FROM replica_state ORDER BY bar_index")]
    last = 2000 + KEEP_STATES + 3
    assert kept == list(range(last - KEEP_STATES + 1, last + 1))
    ts = pd.DatetimeIndex(pd.to_datetime(series["ts_utc"], utc=True))
    s = feed.state_at(ts[last - 2])
    assert s is not None and s == R.replica_run(series.iloc[:last - 1])[1]
    assert feed.state_at(ts[last - KEEP_STATES - 1]) is None              # pruned
    assert ReplicaFeed(feed.db, feed.feed, R.ReplicaParams(swing_len=40)).state_at(ts[last]) is None   # other block
