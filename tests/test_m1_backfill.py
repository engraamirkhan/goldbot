"""The broker's M1 back-fill (quant review of fix/arm-integrity-voids, M1 MEDIUM): `feed_reconcile` pulled only the
last 24 h, so a missed or late run, or a short broker reply, left a permanent hole in `bars_1m_broker` and held every
4h discovered arm unverified (`no_broker_coverage`) for weeks. Each run now also re-pulls, per account, the days of
the last BACKFILL_MAX whose stored M1 lacks open minutes; `m1-backfill` does the same by hand over READ_DAYS. The
back-fill writes only minutes not stored yet (first release kept), never grades them and never emits divergence
events: the reconcile's own 24 h report is what it was."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data import crossfeed as cf
from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import epoch_ns
from goldbot.ops import m1_backfill as MB
from goldbot.ops.jobs import feed_reconcile
from goldbot.research import arm_integrity as I
from tests.test_crossfeed import ACC, _job_ctx, as_broker_m1
from tests.test_reconcile_coverage import RangeBroker

MON = pd.Timestamp("2026-10-05 23:20", tz="UTC")          # Monday's slot
WED = MON + pd.Timedelta(days=2)


@pytest.fixture(scope="module")
def week_m1() -> pd.DataFrame:
    """The broker's M1 (copy_rates rows) from the Friday before to Thursday."""
    t = synthetic_ticks("2026-10-02 00:00", "2026-10-08 23:59", ticks_per_minute=2, seed=11)
    t = t[["ts_utc", "bid", "ask"]].drop_duplicates("ts_utc", keep="last").reset_index(drop=True)
    return as_broker_m1(ticks_to_1m(t))


def _stored(store: Store) -> pd.DataFrame:
    got = store.read(cf.BROKER_TABLE, source=ACC.account_id, symbol=ACC.symbol)
    return got.sort_values("ts_utc").reset_index(drop=True)


def _observed(store: Store, lo: pd.Timestamp, hi: pd.Timestamp) -> bool:
    """What the arm read asks of the stored M1 (`AccountMinutes.observed`): no hole over 2 h of open minutes."""
    ts = pd.to_datetime(_stored(store)["ts_utc"], utc=True)
    m = I.AccountMinutes(pd.DataFrame({"ts_utc": ts, "rank": I.BROKER_M1_RANK}))
    return m.observed(int(lo.value), int(hi.value))


class Clock:
    def __init__(self, step: float) -> None:
        self.t, self.step = 0.0, step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def test_a_skipped_tuesday_run_leaves_no_m1_hole_once_wednesdays_runs(tmp_path, week_m1):
    """The closing test: Monday's run, no Tuesday run, Wednesday's run: the M1 observes Monday to Wednesday."""
    ctx = _job_ctx(tmp_path, lambda acc: RangeBroker(week_m1))
    feed_reconcile(ctx, MON)
    feed_reconcile(ctx, WED)
    assert _observed(ctx.store, MON - pd.Timedelta(hours=20), WED - pd.Timedelta(minutes=1))
    # without the back-fill (the old 24 h pull) Tuesday's day is a hole: the arm read is `no_broker_coverage`
    old = _job_ctx(tmp_path / "old", lambda acc: RangeBroker(week_m1))
    cf.reconcile_account(old.store, RangeBroker(week_m1), ACC.account_id, ACC.symbol, MON, backfill=pd.Timedelta(0))
    cf.reconcile_account(old.store, RangeBroker(week_m1), ACC.account_id, ACC.symbol, WED, backfill=pd.Timedelta(0))
    assert not _observed(old.store, MON - pd.Timedelta(hours=20), WED - pd.Timedelta(minutes=1))


def test_a_short_broker_reply_is_filled_by_the_next_run(tmp_path, week_m1):
    """Tuesday's terminal served its last 497 minutes only; Wednesday's run re-pulls the rest of Tuesday."""
    store = Store(tmp_path / "data")
    tue = MON + pd.Timedelta(days=1)

    class Short(RangeBroker):
        def get_bars_range(self, symbol: str, tf: str, s: pd.Timestamp, e: pd.Timestamp) -> pd.DataFrame:
            return super().get_bars_range(symbol, tf, s, e).tail(497).reset_index(drop=True)
    cf.reconcile_account(store, Short(week_m1), ACC.account_id, ACC.symbol, tue, backfill=pd.Timedelta(0))
    assert not _observed(store, tue - pd.Timedelta(hours=20), tue - pd.Timedelta(minutes=1))
    rep = cf.reconcile_account(store, RangeBroker(week_m1), ACC.account_id, ACC.symbol, WED)
    assert rep.backfill["minutes_added"] > 500
    assert _observed(store, tue - pd.Timedelta(hours=20), WED - pd.Timedelta(minutes=1))


def test_the_backfill_never_grades_or_double_counts_and_never_rewrites_a_stored_minute(tmp_path, week_m1):
    """The 24 h report is the same with and without a back-filled history, the back-fill emits no divergence event,
    and a minute already stored keeps its first write (available_utc and prices) when the broker serves it again."""
    plain = cf.reconcile_account(Store(tmp_path / "a"), RangeBroker(week_m1), ACC.account_id, ACC.symbol, WED,
                                 backfill=pd.Timedelta(0))
    store = Store(tmp_path / "b")
    first = cf.reconcile_account(store, RangeBroker(week_m1), ACC.account_id, ACC.symbol, MON)
    before = _stored(store)
    moved = week_m1.copy()
    moved[["open", "high", "low", "close"]] += 5.0                      # a revised M1 from the broker
    rep = cf.reconcile_account(store, RangeBroker(moved), ACC.account_id, ACC.symbol, WED)
    grade = ("broker_minutes", "engine_minutes", "matched", "close_mismatch", "missing_engine", "missing_broker",
             "divergence", "severity", "window_from", "window_to")
    expected = cf.reconcile_account(Store(tmp_path / "c"), RangeBroker(moved), ACC.account_id, ACC.symbol, WED,
                                    backfill=pd.Timedelta(0))
    assert {k: getattr(rep, k) for k in grade} == {k: getattr(expected, k) for k in grade}
    assert plain.broker_minutes == rep.broker_minutes                  # never the back-filled days
    assert first.backfill["minutes_added"] > 0 and rep.backfill["minutes_added"] > 0
    after = _stored(store)
    kept = after[after["ts_utc"].isin(before["ts_utc"])].reset_index(drop=True)
    pd.testing.assert_frame_equal(kept[before.columns], before)       # first release kept, never overwritten
    ev = store.read("dq_events", source=ACC.account_id)
    assert not any(str(c).startswith("reconcile_backfill") for c in ev["check"]) if ev is not None else True


def test_a_complete_history_makes_no_backfill_calls_and_a_rerun_adds_nothing(tmp_path, week_m1):
    store = Store(tmp_path / "data")
    r1 = cf.backfill_broker_m1(store, RangeBroker(week_m1), ACC.account_id, ACC.symbol,
                               start=MON - pd.Timedelta(days=3), end=WED)
    assert r1.minutes_added > 0 and r1.max_open_hole == 0
    again = RangeBroker(week_m1)
    r2 = cf.backfill_broker_m1(store, again, ACC.account_id, ACC.symbol, start=MON - pd.Timedelta(days=3), end=WED)
    assert r2.minutes_added == 0 and r2.chunks_pulled == 0 and again.ranges == []


def test_the_nightly_backfill_is_capped_and_stops_at_its_time_budget(tmp_path, week_m1):
    store = Store(tmp_path / "data")
    b = RangeBroker(week_m1)
    cf.reconcile_account(store, b, ACC.account_id, ACC.symbol, WED)
    cap = WED.floor("min") - cf.BACKFILL_MAX
    assert min(s for s, _ in b.ranges) >= cap                           # never before the cap
    slow = RangeBroker(week_m1)
    r = cf.backfill_broker_m1(Store(tmp_path / "slow"), slow, ACC.account_id, ACC.symbol,
                              start=WED - pd.Timedelta(days=5), end=WED, budget_s=10.0, clock=Clock(6.0))
    assert r.stopped == "budget" and 0 < r.chunks_pulled < r.chunks_needed
    newest = max(s for s, _ in slow.ranges)
    assert newest >= WED - pd.Timedelta(days=1)                         # the freshest days first


def test_a_broker_without_the_range_method_or_failing_is_told_not_raised(tmp_path, week_m1):
    from tests.test_crossfeed import FakeBroker
    store = Store(tmp_path / "data")
    r = cf.backfill_broker_m1(store, FakeBroker(week_m1), ACC.account_id, ACC.symbol,
                              start=MON - pd.Timedelta(days=2), end=MON)
    assert r.stopped == "no_range" and r.minutes_added == 0

    class Down(RangeBroker):
        def get_bars_range(self, *a: Any) -> pd.DataFrame:
            raise TimeoutError("bridge busy")
    rep = cf.reconcile_account(store, RangeBroker(week_m1), ACC.account_id, ACC.symbol, MON)   # today's window
    rep = cf.reconcile_account(store, Down(week_m1), ACC.account_id, ACC.symbol, WED)
    assert rep.backfill["errors"] and "bridge busy" in rep.note
    ev = store.read("dq_events", source=ACC.account_id)
    assert "reconcile_backfill_failed" in set(ev["check"])
    assert set(ev.loc[ev["check"] == "reconcile_backfill_failed", "severity"]) == {"warning"}


def test_backfilled_minutes_are_known_only_from_their_write_time(tmp_path, week_m1):
    """Point in time: a minute pulled after the fact carries its write time, so as-of readers (exploration labels,
    `account_bars_1m`) never see it before it was written."""
    store = Store(tmp_path / "data")
    t0 = pd.Timestamp.now("UTC")
    cf.backfill_broker_m1(store, RangeBroker(week_m1), ACC.account_id, ACC.symbol, start=MON - pd.Timedelta(days=2),
                          end=MON)
    got = _stored(store)
    assert (epoch_ns(pd.DatetimeIndex(pd.to_datetime(got["available_utc"], utc=True))) >= int(t0.value)).all()


def test_m1_backfill_command_runs_every_enabled_account_through_its_bridge(tmp_path, week_m1, monkeypatch, capsys):
    store = Store(tmp_path / "data")
    seen: list[str] = []
    monkeypatch.setattr(MB, "_accounts", lambda ids: [ACC])
    monkeypatch.setattr(MB, "_store", lambda root: store)

    def broker(acc: Any) -> RangeBroker:
        seen.append(acc.account_id)
        return RangeBroker(week_m1)
    monkeypatch.setattr(MB, "_broker", broker)
    monkeypatch.setattr(MB, "_now", lambda: WED)
    assert MB.main(["--all", "--days", "6", "--pause-s", "0"]) == 0
    out = capsys.readouterr().out
    assert seen == [ACC.account_id] and ACC.account_id in out and "minutes added" in out
    assert _observed(store, WED - pd.Timedelta(days=5), WED - pd.Timedelta(minutes=1))
    assert MB.main(["--all", "--days", "0"]) == 2                       # refused: nothing to pull
    assert np.isclose(MB.DEFAULT_DAYS, I.READ_DAYS)


def test_the_m1_backfill_command_is_registered():
    import goldbot.ops.run  # noqa: F401  (registers the commands)
    from goldbot.ops.commands.registry import find
    assert find("m1-backfill") is not None


def test_the_backfill_cap_and_budget_are_documented_constants():
    assert cf.BACKFILL_MAX == pd.Timedelta(days=7)
    assert 0 < cf.BACKFILL_BUDGET_S <= 300
    assert Path("docs/RUNBOOK.md").read_text().count("m1-backfill") >= 1


# ------------------------------------------------------------------- review round of 4bac3d3e (quant 3, code a-c)
def test_any_backfill_failure_is_a_warning_and_the_24h_report_and_events_are_still_saved(tmp_path, week_m1,
                                                                                        monkeypatch):
    """Quant 3 / code (b): a read of the stored M1 (a month partition), the point or a write failing inside the
    back-fill must not lose the 24 h report and its dq_events: one `reconcile_backfill_failed` warning instead."""
    store = Store(tmp_path / "data")

    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("partition unreadable")
    monkeypatch.setattr(cf, "_stored_m1", boom)
    rep = cf.reconcile_account(store, RangeBroker(week_m1), ACC.account_id, ACC.symbol, WED, state_dir=tmp_path)
    assert cf.reconcile_file(tmp_path, ACC.account_id).exists() and rep.broker_minutes > 1000
    assert "partition unreadable" in rep.note and rep.backfill["errors"]
    ev = store.read("dq_events", source=ACC.account_id)
    assert {"reconcile_missing_engine", "reconcile_backfill_failed"} <= set(ev["check"])
    assert set(ev.loc[ev["check"] == "reconcile_backfill_failed", "severity"]) == {"warning"}


def test_a_stored_minute_keeps_its_first_copy_even_when_a_concurrent_writer_lands_between_check_and_append(
        tmp_path, week_m1, monkeypatch):
    """Code (a): a by-hand m1-backfill overlapping the 23:20 reconcile. The existence check and the append are one
    step under the partition lock (Store.append keep="first"): a copy written after another's never wins."""
    store = Store(tmp_path / "data")
    first = cf.broker_m1_bars(week_m1.head(30))
    assert cf.store_new_m1(store, first, ACC.account_id, ACC.symbol) == 30
    before = _stored(store)
    monkeypatch.setattr(cf, "_stored_m1", lambda *a, **k: np.zeros(0, dtype=np.int64))   # the race: check saw none
    moved = week_m1.head(40).copy()
    moved[["open", "high", "low", "close"]] += 5.0
    assert cf.store_new_m1(store, cf.broker_m1_bars(moved), ACC.account_id, ACC.symbol) == 10
    after = _stored(store)
    pd.testing.assert_frame_equal(after.head(30)[before.columns], before)
    assert len(after) == 40


def test_store_append_keep_first_keeps_the_stored_row_and_counts_only_new_keys(tmp_path):
    store = Store(tmp_path / "data")
    ts = pd.date_range("2026-10-30 23:58", periods=4, freq="min", tz="UTC")    # crosses a month partition
    store.append(cf.BROKER_TABLE, pd.DataFrame({"ts_utc": ts[:2], "x": [1.0, 2.0]}), source="a")
    n = store.append(cf.BROKER_TABLE, pd.DataFrame({"ts_utc": ts, "x": [9.0, 9.0, 3.0, 4.0]}), source="a",
                     keep="first")
    assert n == 2
    got = store.read(cf.BROKER_TABLE, source="a").sort_values("ts_utc")
    assert got["x"].tolist() == [1.0, 2.0, 3.0, 4.0]
    with pytest.raises(ValueError):
        store.append(cf.BROKER_TABLE, pd.DataFrame({"ts_utc": ts, "x": 0.0}), source="a", keep="first", dedupe=False)


def test_the_nightly_backfill_paces_its_bridge_calls(tmp_path, week_m1, monkeypatch):
    """Code (c): the nightly back-fill waits BACKFILL_PAUSE_S (0.5 s) between bridge calls (tests/conftest.py sets it
    to 0 for speed; the default is read at call time)."""
    monkeypatch.setattr(cf, "BACKFILL_PAUSE_S", 0.5)
    seen: dict[str, Any] = {}
    real = cf.backfill_broker_m1

    def spy(*a: Any, **k: Any) -> Any:
        seen.update(k)
        return real(*a, **{**k, "pause_s": 0.0})
    monkeypatch.setattr(cf, "backfill_broker_m1", spy)
    cf.reconcile_account(Store(tmp_path / "data"), RangeBroker(week_m1), ACC.account_id, ACC.symbol, WED)
    assert seen["pause_s"] == 0.5
