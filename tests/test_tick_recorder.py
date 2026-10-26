"""BACKLOG 79: the daily broker tick record for the H-A holiday diagnostic (goldbot/data/ticks.py).

One Parquet per broker server date under state/ticks/<account>/, pulled with copy_ticks through the scheduler's
read-only broker after the server day closes (the pre-registered H-A method, docs/research/
holiday-list-measurement-2026-10.md), written atomically, indexed with sha256, pruned after `retain_days`, a health
WARN on a thin trading day, and a research loader."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from goldbot.config import TickRecordSettings, load_settings
from goldbot.data import ticks as tk
from goldbot.data.timeutil import epoch_ns
from goldbot.ops import health
from goldbot.ops.accounts import Account
from goldbot.ops.health import AccountRef, HealthContext
from goldbot.ops.jobs import JOBS, JobContext, tick_record
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.population import Population
from goldbot.research.registry import TrialRegistry

ACC = Account(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=None, terminal_path="",
              server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100, enabled=True)
TZ = "Europe/Athens"
# Friday 2026-10-16 23:00 UTC: server date 2026-10-16 (Athens, UTC+3) closed at 21:00 UTC
NOW = pd.Timestamp("2026-10-16 23:00", tz="UTC")


def synthetic_day(start: pd.Timestamp, end: pd.Timestamp, n: int, seed: int = 1) -> pd.DataFrame:
    """n ticks spread over [start, end), millisecond stamps, a random-walk mid and a 0.10-0.30 spread."""
    rng = np.random.default_rng(seed)
    span_ms = int((end - start).total_seconds() * 1000)
    offs = np.sort(rng.choice(span_ms, size=n, replace=False))
    ts = pd.DatetimeIndex(start + pd.to_timedelta(offs, unit="ms")).as_unit("ms")
    mid = 2650.0 + np.cumsum(rng.normal(0, 0.05, n))
    spread = np.round(rng.uniform(0.10, 0.30, n), 2)
    bid = np.round(mid - spread / 2, 2)
    return pd.DataFrame({"ts_utc": ts, "bid": bid, "ask": np.round(bid + spread, 2),
                         "flags": rng.integers(2, 7, n).astype(np.int64)})


class FakeTickBroker:
    """copy_ticks as MT5 serves it: from a request time floored to the second (copy_ticks_from takes datetimes), at
    most n rows, distinct (ts, bid, ask, flags)."""

    def __init__(self, ticks: pd.DataFrame) -> None:
        self.ticks = ticks.sort_values("ts_utc").reset_index(drop=True)
        self.calls: list[tuple[pd.Timestamp, int]] = []
        self.shut = False

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        self.calls.append((since_utc, n))
        since = pd.Timestamp(since_utc).floor("s")
        return self.ticks[self.ticks["ts_utc"] >= since].head(n).reset_index(drop=True)

    def shutdown(self) -> None:
        self.shut = True


def _bounds(day: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    return tk.server_day_bounds(pd.Timestamp(day).date(), TZ)


# ------------------------------------------------------------------------------------------- server day
def test_server_day_bounds_follow_the_server_zone_across_dst():
    s, e = _bounds("2026-10-16")              # Athens summer time: UTC+3
    assert (s, e) == (pd.Timestamp("2026-10-15 21:00", tz="UTC"), pd.Timestamp("2026-10-16 21:00", tz="UTC"))
    s, e = _bounds("2026-10-25")              # the EU switch: a 25-hour server day
    assert (s, e) == (pd.Timestamp("2026-10-24 21:00", tz="UTC"), pd.Timestamp("2026-10-25 22:00", tz="UTC"))
    s, e = tk.server_day_bounds(pd.Timestamp("2026-10-16").date(), "NY+7")   # 17:00 New York
    assert (s, e) == (pd.Timestamp("2026-10-15 21:00", tz="UTC"), pd.Timestamp("2026-10-16 21:00", tz="UTC"))


def test_trading_days_due_are_complete_weekday_server_dates_within_the_backfill():
    days = tk.days_due(NOW, TZ, backfill_days=7)
    labels = [str(d) for d in days]
    assert labels == ["2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
    # 20:59 UTC on the 16th: the server day 2026-10-16 is still open, so it is not due
    assert str(tk.days_due(pd.Timestamp("2026-10-16 20:59", tz="UTC"), TZ, backfill_days=1)[-1]) == "2026-10-15"


# ------------------------------------------------------------------------------------------- pull
def test_pull_pages_through_copy_ticks_and_keeps_only_the_server_day():
    s, e = _bounds("2026-10-16")
    inside = synthetic_day(s, e, 25_000)
    outside = synthetic_day(e, e + pd.Timedelta(hours=1), 300, seed=2)
    broker = FakeTickBroker(pd.concat([inside, outside], ignore_index=True))
    got = tk.pull_day(broker, "XAUUSD", s, e, chunk=4_000)
    assert len(got) == len(inside) and got["ts_utc"].is_monotonic_increasing
    assert got["ts_utc"].min() >= s and got["ts_utc"].max() < e
    assert len(broker.calls) > 6 and all(n == 4_000 for _, n in broker.calls)


def test_pull_refuses_to_loop_when_a_page_cannot_advance():
    s, e = _bounds("2026-10-16")
    same_second = pd.DataFrame({"ts_utc": pd.DatetimeIndex([s + pd.Timedelta(milliseconds=i) for i in range(50)]),
                                "bid": 2650.0 + np.arange(50) * 0.01, "ask": 2650.2 + np.arange(50) * 0.01,
                                "flags": 6})
    with pytest.raises(RuntimeError, match="cannot page"):
        tk.pull_day(FakeTickBroker(same_second), "XAUUSD", s, e, chunk=10)


class SyncingBroker(FakeTickBroker):
    """The terminal still loading the day's history: the first request that reaches `cutoff` returns a short page
    ending there (2026-10-19 on the brain: 45,876 ticks, 22:02-01:00 UTC, then a short page); later requests see it all."""

    def __init__(self, ticks: pd.DataFrame, cutoff: pd.Timestamp) -> None:
        super().__init__(ticks)
        self.cutoff, self.loading = cutoff, True

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        page = super().copy_ticks(symbol, since_utc, n)
        if self.loading and (page["ts_utc"] >= self.cutoff).any():
            self.loading = False
            return page[page["ts_utc"] < self.cutoff].reset_index(drop=True)
        return page


def test_a_short_page_while_the_terminal_loads_history_is_not_the_end_of_the_day():
    s, e = _bounds("2026-10-16")
    day = synthetic_day(s, e, 25_000)
    broker = SyncingBroker(day, s + pd.Timedelta(hours=3))
    got = tk.pull_day(broker, "XAUUSD", s, e, chunk=2_000, whole_day=True)
    assert len(got) == len(day) and got["ts_utc"].max() > e - pd.Timedelta(minutes=5)


class GapBroker(FakeTickBroker):
    """A terminal that cannot serve [hole_from, hole_to): requests from inside it return nothing."""

    def __init__(self, ticks: pd.DataFrame, hole_from: pd.Timestamp, hole_to: pd.Timestamp) -> None:
        super().__init__(ticks[(ticks["ts_utc"] < hole_from) | (ticks["ts_utc"] >= hole_to)])
        self.hole_from, self.hole_to = hole_from, hole_to

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        page = super().copy_ticks(symbol, since_utc, n)
        if self.hole_from <= pd.Timestamp(since_utc) < self.hole_to:
            return page.iloc[0:0]
        return page[page["ts_utc"] < self.hole_from] if pd.Timestamp(since_utc) < self.hole_from else page


def test_ticks_ending_early_raise_instead_of_passing_for_a_whole_day():
    s, e = _bounds("2026-10-16")
    broker = GapBroker(synthetic_day(s, e, 5_000), s + pd.Timedelta(hours=3), e + pd.Timedelta(days=1))
    with pytest.raises(tk.TruncatedPull, match="before the server day ends"):
        tk.pull_day(broker, "XAUUSD", s, e, chunk=1_000, whole_day=True)
    # the engine's history fill (whole_day off) keeps what the terminal served: its gaps come from the broker's 1m bars
    part = tk.pull_day(broker, "XAUUSD", s, e, chunk=1_000)
    assert 0 < len(part) and part["ts_utc"].max() < s + pd.Timedelta(hours=3)


def test_a_truncated_day_is_not_written_and_newer_days_still_are(tmp_path):
    week = _week_broker().ticks
    s, e = _bounds("2026-10-12")
    broker = GapBroker(week, s + pd.Timedelta(hours=3), e)
    out = _record(tmp_path, broker, backfill_days=4)
    assert out["written"] == ["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
    assert "2026-10-12" in out["incomplete"][0] and out["behind"] == 1
    assert not (tmp_path / "ticks" / "icm-demo" / "2026-10-12.parquet").exists()


class ShortPageBroker(FakeTickBroker):
    """Pages shorter than requested in mid-stream for a second reason: the adapter drops repeated (time_msc, bid, ask,
    flags) rows that COPY_TICKS_ALL returns (volume-only updates; 263-338 same-millisecond rows a day on the brain)."""

    def __init__(self, ticks: pd.DataFrame, short_every: int = 1, drop: int = 3) -> None:
        super().__init__(ticks)
        self.short_every, self.drop = short_every, drop

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        page = super().copy_ticks(symbol, since_utc, n)
        if len(page) == n and len(self.calls) % self.short_every == 0:
            return page.drop(index=page.index[1:1 + self.drop]).reset_index(drop=True)
        return page


@pytest.mark.parametrize("short_every", [1, 3])
def test_a_page_short_from_dropped_duplicates_is_not_the_end_of_the_day(short_every):
    s, e = _bounds("2026-10-16")
    day = synthetic_day(s, e, 20_000)
    broker = ShortPageBroker(day, short_every=short_every)
    got = tk.pull_day(broker, "XAUUSD", s, e, chunk=1_000, whole_day=True)
    assert got["ts_utc"].max() == day["ts_utc"].max() and len(tk._london_ny(got)) > 10_000
    assert len(got) >= len(day) - 3 * len(broker.calls)          # only the rows the broker itself dropped are missing


# ------------------------------------------------------------------------------------------- write, index, epoch_ns
def test_written_times_are_epoch_ns_whatever_the_unit_and_round_trip_exactly(tmp_path):
    s, e = _bounds("2026-10-16")
    df = synthetic_day(s, e, 2_000)
    for unit in ("ms", "us", "ns"):
        d = df.assign(ts_utc=pd.DatetimeIndex(df["ts_utc"]).as_unit(unit))
        rec = tk.write_day(tmp_path, "icm-demo", pd.Timestamp("2026-10-16").date(), d, symbol="XAUUSD",
                           server_tz=TZ, pulled_utc=NOW)
        raw = pq.read_table(tmp_path / "ticks" / "icm-demo" / "2026-10-16.parquet").to_pandas()
        assert raw["time_ns"].dtype == np.int64
        assert (raw["time_ns"].to_numpy() == epoch_ns(df["ts_utc"])).all(), unit
        assert rec.rows == 2_000
    back = tk.load_day("icm-demo", "2026-10-16", state_dir=tmp_path)
    assert list(back.columns) == ["ts_utc", "bid", "ask", "flags"]
    assert (epoch_ns(back["ts_utc"]) == epoch_ns(df["ts_utc"])).all()
    assert str(back["ts_utc"].dt.tz) == "UTC"


def test_day_file_is_private_atomic_and_carries_no_identifier_beyond_the_label(tmp_path):
    s, e = _bounds("2026-10-16")
    tk.write_day(tmp_path, "icm-demo", pd.Timestamp("2026-10-16").date(), synthetic_day(s, e, 500), symbol="XAUUSD",
                 server_tz=TZ, pulled_utc=NOW)
    d = tmp_path / "ticks" / "icm-demo"
    f = d / "2026-10-16.parquet"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and stat.S_IMODE(d.stat().st_mode) == 0o700
    meta = {k.decode(): v.decode() for k, v in pq.read_schema(f).metadata.items() if k.decode().startswith("goldbot.")}
    assert set(meta) == {"goldbot.account", "goldbot.symbol", "goldbot.server_date", "goldbot.server_tz",
                         "goldbot.source", "goldbot.pulled_utc"}
    assert meta["goldbot.account"] == "icm-demo"
    assert not [p for p in d.iterdir() if p.name.endswith(".tmp")]
    idx = json.loads((d / tk.INDEX_FILE).read_text())
    assert set(idx["days"]["2026-10-16"]) == set(tk.DayRecord.model_fields)
    assert stat.S_IMODE((d / tk.INDEX_FILE).stat().st_mode) == 0o600


def test_a_failed_write_leaves_the_previous_file_and_no_temp(tmp_path, monkeypatch):
    s, e = _bounds("2026-10-16")
    day = pd.Timestamp("2026-10-16").date()
    tk.write_day(tmp_path, "icm-demo", day, synthetic_day(s, e, 100), symbol="XAUUSD", server_tz=TZ, pulled_utc=NOW)
    f = tmp_path / "ticks" / "icm-demo" / "2026-10-16.parquet"
    before = f.read_bytes()

    def boom(*a: Any, **k: Any) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(tk.pq, "write_table", boom)
    with pytest.raises(OSError, match="disk full"):
        tk.write_day(tmp_path, "icm-demo", day, synthetic_day(s, e, 200, seed=3), symbol="XAUUSD", server_tz=TZ,
                     pulled_utc=NOW)
    assert f.read_bytes() == before
    assert not [p for p in f.parent.iterdir() if p.name.endswith(".tmp")]


def test_account_labels_cannot_escape_the_ticks_directory(tmp_path):
    for bad in ("../x", "a/b", "", "icm demo", "12345678"):
        with pytest.raises(ValueError):
            tk.load_day(bad, "2026-10-16", state_dir=tmp_path)


def test_fifty_thousand_ticks_stay_under_a_megabyte(tmp_path):
    s, e = _bounds("2026-10-16")
    rec = tk.write_day(tmp_path, "icm-demo", pd.Timestamp("2026-10-16").date(), synthetic_day(s, e, 50_000),
                       symbol="XAUUSD", server_tz=TZ, pulled_utc=NOW)
    assert rec.bytes < 1_000_000


# ------------------------------------------------------------------------------------------- loader
def test_loader_refuses_a_missing_day_and_a_changed_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="2026-10-16"):
        tk.load_day("icm-demo", "2026-10-16", state_dir=tmp_path)
    s, e = _bounds("2026-10-16")
    tk.write_day(tmp_path, "icm-demo", pd.Timestamp("2026-10-16").date(), synthetic_day(s, e, 100), symbol="XAUUSD",
                 server_tz=TZ, pulled_utc=NOW)
    f = tmp_path / "ticks" / "icm-demo" / "2026-10-16.parquet"
    os.chmod(f, 0o600)
    f.write_bytes(f.read_bytes() + b"x")
    with pytest.raises(ValueError, match="sha256"):
        tk.load_day("icm-demo", "2026-10-16", state_dir=tmp_path)


# ------------------------------------------------------------------------------------------- record, retention
def _record(tmp_path: Path, broker: Any, now: pd.Timestamp = NOW, **kw: Any) -> dict[str, Any]:
    kw.setdefault("max_days_per_run", 10)
    cfg = TickRecordSettings(**kw)
    return tk.record_ticks(broker, tmp_path, "icm-demo", "XAUUSD", TZ, cfg, clock=lambda: now, sleep=lambda s: None)


def _week_broker() -> FakeTickBroker:
    frames = []
    for i, day in enumerate(("2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16")):
        s, e = _bounds(day)
        frames.append(synthetic_day(s, e, 3_000, seed=i))
    return FakeTickBroker(pd.concat(frames, ignore_index=True))


def test_record_pulls_each_due_day_once_and_is_idempotent(tmp_path):
    broker = _week_broker()
    out = _record(tmp_path, broker, backfill_days=5)
    assert out["written"] == ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
    assert tk.recorded_days("icm-demo", state_dir=tmp_path) == [pd.Timestamp(d).date() for d in out["written"]]
    calls = len(broker.calls)
    again = _record(tmp_path, broker, backfill_days=5)
    assert again["written"] == [] and len(broker.calls) == calls
    rec = tk.read_index(tmp_path, "icm-demo")["2026-10-16"]
    assert rec.rows == 3_000 and 0 < rec.london_ny_ticks < 3_000 and rec.median_spread is not None


def test_retention_deletes_days_older_than_retain_days(tmp_path):
    broker = _week_broker()
    _record(tmp_path, broker, backfill_days=5)
    later = pd.Timestamp("2026-10-19 21:20", tz="UTC")   # Monday, in the daily break: the 12th-14th are > 4 days old
    out = _record(tmp_path, FakeTickBroker(broker.ticks.head(0)), later, backfill_days=1, retain_days=4)
    assert out["pruned"] == ["2026-10-12", "2026-10-13", "2026-10-14"]
    d = tmp_path / "ticks" / "icm-demo"
    # the 19th: the terminal served no tick and no later session has opened yet, so an empty file would claim a
    # tickless day; it is reported incomplete and pulled again by a later run
    assert sorted(p.name for p in d.glob("*.parquet")) == ["2026-10-15.parquet", "2026-10-16.parquet"]
    assert out["incomplete"][0].startswith("2026-10-19")
    assert "2026-10-12" not in tk.read_index(tmp_path, "icm-demo")


# ------------------------------------------------------------------------------------------- health
def _health(tmp_path: Path, now: pd.Timestamp = NOW + pd.Timedelta(hours=1)) -> HealthContext:
    return HealthContext(state_dir=tmp_path, now=now, settings=load_settings(), accounts=[AccountRef(account_id="icm-demo")],
                         get_secret=lambda k: None, disk_usage=lambda p: (100e9, 50e9, 50e9))


def _one_day(tmp_path: Path, n: int, london_ny_only: bool = True) -> None:
    s, e = _bounds("2026-10-16")
    if london_ny_only:
        s, e = pd.Timestamp("2026-10-16 08:00", tz="UTC"), pd.Timestamp("2026-10-16 16:00", tz="UTC")
    tk.write_day(tmp_path, "icm-demo", pd.Timestamp("2026-10-16").date(), synthetic_day(s, e, n), symbol="XAUUSD",
                 server_tz=TZ, pulled_utc=NOW)


def test_health_warns_on_a_thin_trading_day_and_passes_a_full_one(tmp_path):
    assert tk.check_tick_record(_health(tmp_path), "icm-demo").status == "ok"      # nothing recorded yet
    _one_day(tmp_path, 999)
    c = tk.check_tick_record(_health(tmp_path), "icm-demo")
    assert c.name == "ticks:icm-demo" and c.status == "warn" and "999 London/NY ticks" in c.reason
    _one_day(tmp_path, 1_000)
    assert tk.check_tick_record(_health(tmp_path), "icm-demo").status == "ok"


def test_health_warns_when_the_record_stops_advancing(tmp_path):
    _one_day(tmp_path, 5_000)
    c = tk.check_tick_record(_health(tmp_path, NOW + pd.Timedelta(days=5)), "icm-demo")
    assert c.status == "warn" and "2026-10-16" in c.reason


def test_health_registry_runs_the_tick_check_per_account(tmp_path):
    _one_day(tmp_path, 10)
    names = {c.name: c.status for c in health.run_checks(_health(tmp_path)).checks}
    assert names["ticks:icm-demo"] == "warn"


# ------------------------------------------------------------------------------------------- job, settings, backup
def _job_ctx(tmp_path: Path, broker_for: Any, now: pd.Timestamp = NOW) -> JobContext:
    from goldbot.data.store import Store
    return JobContext(settings=load_settings(), store=Store(tmp_path / "data"), state_dir=tmp_path,
                      models=ModelRegistry(tmp_path / "models"), trials=TrialRegistry(tmp_path / "trials.jsonl"),
                      accounts=[ACC], population=Population(tmp_path / "population.json"), broker_for=broker_for,
                      clock=lambda: now, sleep=lambda s: None)


def test_job_records_per_account_skips_without_a_broker_and_reports_failures(tmp_path):
    assert JOBS["tick_record"] is tick_record
    broker = _week_broker()
    out = tick_record(_job_ctx(tmp_path, lambda acc: broker), NOW)
    assert out["icm-demo"]["written"] == ["2026-10-09", "2026-10-12", "2026-10-13"] and broker.shut  # oldest first
    assert "skipped" in tick_record(_job_ctx(tmp_path, None), NOW)
    assert tick_record(_job_ctx(tmp_path, lambda acc: None), NOW)["icm-demo"]["skipped"]

    def boom(acc: Account) -> Any:
        raise RuntimeError("bridge down")
    with pytest.raises(RuntimeError, match="icm-demo: RuntimeError: bridge down"):
        tick_record(_job_ctx(tmp_path, boom), NOW)


def test_settings_defaults_cover_the_h_a_window():
    s = load_settings()
    assert s.ticks.min_london_ny_ticks == 1000 and s.ticks.backfill_days == 7
    # H-A reads 2026-10-18 .. 2027-04-30 plus 14-day controls: the first THIN day must outlive the verdict
    assert s.ticks.retain_days >= (pd.Timestamp("2027-05-14") - pd.Timestamp("2026-11-12")).days
    for name in ("tick_record", "tick_record_winter"):          # trading-safety: no catch-up into London/NY
        assert s.scheduler.tick_record.kind == "daily" and getattr(s.scheduler, name).max_late_hours <= 1
    assert JOBS["tick_record_winter"] is tick_record
    # each slot falls in the daily break of one DST regime (17:00-18:00 New York)
    assert tk.market_quiet(pd.Timestamp(f"2026-07-15 {s.scheduler.tick_record.at}", tz="UTC"))
    assert tk.market_quiet(pd.Timestamp(f"2026-12-16 {s.scheduler.tick_record_winter.at}", tz="UTC"))
    # a run catches up up to 3 days within its budget (one a run never caught up after a failed run); a cold page of
    # an old day took 2.1-4.7 s on the brain (2026-10-18..24), so the limit is 5 s, still under ORDER_MAX_AGE_S
    assert s.ticks.max_days_per_run == 3 and s.ticks.page_limit_s == 5.0 and s.ticks.page_limit_s < 10
    # three accounts' budgets fit the 45-minute quiet window of the daily break
    assert 3 * s.ticks.run_budget_s <= 45 * 60


def test_backup_keeps_day_files_and_drops_temporaries():
    from goldbot.ops.backup import _excluded_state
    assert not _excluded_state(Path("ticks/icm-demo/2026-10-16.parquet"))
    assert not _excluded_state(Path("ticks/icm-demo/index.json"))
    assert _excluded_state(Path("ticks/icm-demo/.2026-10-16.parquet.abc.tmp"))


# ------------------------------------------------------------------------------------------- trading safety (review)
@pytest.mark.parametrize("utc, quiet", [
    ("2026-07-15 21:20", True),      # Wed 17:20 EDT: daily break
    ("2026-07-15 22:40", False),     # Wed 18:40 EDT: reopened
    ("2026-12-16 22:20", True),      # Wed 17:20 EST: daily break
    ("2026-12-16 21:20", False),     # Wed 16:20 EST: New York afternoon
    ("2026-10-16 23:00", True),      # Fri 19:00 EDT: weekend
    ("2026-10-17 12:00", True),      # Saturday
    ("2026-12-20 22:45", True),      # Sun 17:45 EST: before the reopen
    ("2026-12-20 22:55", False),     # Sun 17:55 EST: too close to the 18:00 reopen
    ("2026-07-15 21:02", False),     # Wed 17:02 EDT: the break has just started (late ticks)
])
def test_market_quiet_is_the_daily_break_or_the_weekend(utc, quiet):
    assert tk.market_quiet(pd.Timestamp(utc, tz="UTC")) is quiet


def test_job_skips_while_the_market_is_open_and_never_calls_the_broker(tmp_path):
    broker = _week_broker()
    out = tick_record(_job_ctx(tmp_path, lambda acc: broker, pd.Timestamp("2026-10-14 13:00", tz="UTC")), NOW)
    assert "market open" in out["skipped"] and broker.calls == []


class _Clock:
    def __init__(self, t0: pd.Timestamp) -> None:
        self.t = t0

    def __call__(self) -> pd.Timestamp:
        return self.t


class SlowBroker(FakeTickBroker):
    def __init__(self, ticks: pd.DataFrame, clock: _Clock, page_s: float) -> None:
        super().__init__(ticks)
        self.clock, self.page_s = clock, page_s

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        self.clock.t += pd.Timedelta(seconds=self.page_s)
        return super().copy_ticks(symbol, since_utc, n)


def _timed(tmp_path: Path, page_s: float, t0: pd.Timestamp = NOW, **kw: Any) -> tuple[dict[str, Any], SlowBroker, list[float]]:
    clock = _Clock(t0)
    s, e = _bounds("2026-10-16")
    broker = SlowBroker(synthetic_day(s, e, 20_000), clock, page_s)
    sleeps: list[float] = []
    kw.setdefault("backfill_days", 0)
    cfg = TickRecordSettings(page_ticks=1_000, **kw)
    out = tk.record_ticks(broker, tmp_path, "icm-demo", "XAUUSD", TZ, cfg, clock=clock, sleep=sleeps.append)
    return out, broker, sleeps


def test_pages_pause_between_calls(tmp_path):
    out, broker, sleeps = _timed(tmp_path, 0.1, pause_s=0.25)
    assert out["written"] == ["2026-10-16"] and len(broker.calls) >= 20
    assert sleeps and set(sleeps) == {0.25} and len(sleeps) == len(broker.calls) - 1


def test_a_slow_page_defers_the_day_and_writes_nothing(tmp_path):
    out, broker, _ = _timed(tmp_path, 3.0, page_limit_s=2.0)
    assert out["written"] == [] and "2026-10-16" in out["deferred"] and "page" in out["deferred"]
    assert len(broker.calls) == 1 and not (tmp_path / "ticks" / "icm-demo" / "2026-10-16.parquet").exists()


def test_the_run_budget_defers_the_day_and_the_next_run_resumes(tmp_path):
    out, broker, _ = _timed(tmp_path, 1.0, run_budget_s=5)
    assert out["written"] == [] and "budget" in out["deferred"] and len(broker.calls) <= 6
    again, _, _ = _timed(tmp_path, 0.01, run_budget_s=5)
    assert again["written"] == ["2026-10-16"]


def test_the_pull_stops_when_the_quiet_window_ends(tmp_path):
    # Sunday 2026-10-18 17:49:50 EDT: the window closes at 17:50 (before the 18:00 reopen), each page takes 1.5 s
    out, broker, _ = _timed(tmp_path, 1.5, pd.Timestamp("2026-10-18 21:49:50", tz="UTC"), page_limit_s=2.0,
                            run_budget_s=300, backfill_days=2)
    assert out["written"] == [] and "market" in out["deferred"] and len(broker.calls) < 20


def test_one_day_per_run_while_catching_up(tmp_path):
    broker = _week_broker()
    out = _record(tmp_path, broker, backfill_days=5, max_days_per_run=1)
    assert out["written"] == ["2026-10-12"] and out["behind"] == 4


class ReadOnlyBroker(FakeTickBroker):
    """Every order method raises: the job must never reach one."""

    def place_order(self, *a: Any, **k: Any) -> Any:
        raise AssertionError("place_order called")

    def modify(self, *a: Any, **k: Any) -> Any:
        raise AssertionError("modify called")

    def close(self, *a: Any, **k: Any) -> Any:
        raise AssertionError("close called")

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"{name} called: only copy_ticks and shutdown are allowed")


def test_job_is_read_only(tmp_path):
    broker = ReadOnlyBroker(_week_broker().ticks)
    out = tick_record(_job_ctx(tmp_path, lambda acc: broker), NOW)
    assert out["icm-demo"]["written"] == ["2026-10-09", "2026-10-12", "2026-10-13"] and broker.calls and broker.shut


# ------------------------------------------------------------------------------------------- bridge bounds (trading-safety)
class TimedBroker(FakeTickBroker):
    """A RemoteBroker stand-in: its client timeout and read retries as the recorder sets them for each call."""

    def __init__(self, ticks: pd.DataFrame, clock: _Clock | None = None, page_s: float = 0.0,
                 raise_timeout: bool = False) -> None:
        super().__init__(ticks)
        self.timeout_s, self.read_attempts = 10.0, 2
        self.seen: list[tuple[float, int]] = []
        self.at: list[pd.Timestamp] = []
        self.clock, self.page_s, self.raise_timeout = clock, page_s, raise_timeout

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        self.seen.append((self.timeout_s, self.read_attempts))
        if self.clock is not None:
            self.at.append(self.clock())
            self.clock.t += pd.Timedelta(seconds=self.page_s)
        if self.raise_timeout:
            from goldbot.execution.bridge import BridgeUnavailable
            raise BridgeUnavailable("bridge copy_ticks: ReadTimeout")
        return super().copy_ticks(symbol, since_utc, n)


def test_each_page_has_its_own_client_timeout_and_no_retry_and_the_broker_is_restored(tmp_path):
    broker = TimedBroker(_week_broker().ticks)
    out = _record(tmp_path, broker, backfill_days=4, page_limit_s=5.0)
    assert out["written"] and broker.seen and set(broker.seen) == {(5.0, 1)}
    assert (broker.timeout_s, broker.read_attempts) == (10.0, 2)


def test_a_page_timeout_defers_the_day_instead_of_failing_the_job(tmp_path):
    broker = TimedBroker(_week_broker().ticks, raise_timeout=True)
    out = _record(tmp_path, broker, backfill_days=4)
    assert out["written"] == [] and "timeout" in out["deferred"].lower() and len(broker.seen) == 1
    assert (broker.timeout_s, broker.read_attempts) == (10.0, 2)


@pytest.mark.parametrize("hours", [24, 3])          # a whole day, and a truncated one (the TruncatedPull retries)
def test_no_copy_ticks_call_once_the_quiet_window_has_ended(tmp_path, hours):
    # Sunday 2026-10-18 17:49:40 EDT: the window closes at 17:50; each page takes 1.5 s and each retry wait 2 s
    clock = _Clock(pd.Timestamp("2026-10-18 21:49:40", tz="UTC"))
    s, e = _bounds("2026-10-16")
    broker = TimedBroker(synthetic_day(s, s + pd.Timedelta(hours=hours), 20_000), clock, page_s=1.5)

    def sleep(sec: float) -> None:
        clock.t += pd.Timedelta(seconds=sec)

    cfg = TickRecordSettings(page_ticks=1_000, backfill_days=2, max_days_per_run=3, run_budget_s=600)
    out = tk.record_ticks(broker, tmp_path, "icm-demo", "XAUUSD", TZ, cfg, clock=clock, sleep=sleep)
    assert broker.at and all(tk.market_quiet(t) for t in broker.at)
    assert clock() >= pd.Timestamp("2026-10-18 21:50", tz="UTC") and "market" in out.get("deferred", "")
    assert out["written"] == []
