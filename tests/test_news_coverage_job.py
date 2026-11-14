"""BACKLOG 212: the brain's news collector coverage readout and outage check. A daily job reads the `news` table, records
news_features.coverage (first/last received, days, kept rows, backfilled rows dropped, scored) plus the scored share,
and writes a dq_event (warning) for every gap between receive stamps holding more than news_features.OUTAGE of open
gold-market time, once per gap; a weekend closure is not an outage."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from goldbot.data import news_coverage as nc
from goldbot.data.news_features import OUTAGE
from goldbot.data.store import Store
from goldbot.ops.jobs import JOBS

NOW = pd.Timestamp("2026-11-14 07:30", tz="UTC")


def _news(received: list[str], scored: list[bool] | None = None) -> pd.DataFrame:
    rec = pd.to_datetime(received, utc=True)
    n = len(rec)
    sc = scored or [False] * n
    return pd.DataFrame({
        "item_id": [f"i{k}" for k in range(n)], "ts_utc": rec - pd.Timedelta(minutes=5), "received_utc": rec,
        "written_utc": rec + pd.Timedelta(seconds=2), "title": "gold", "summary": "", "link": "",
        "scored": sc, "relevance": [0.8 if s else float("nan") for s in sc]})


def _store(tmp_path: Path, news: pd.DataFrame) -> Store:
    store = Store(tmp_path / "data")
    store.append("news", news, source="rss")
    return store


def test_the_readout_records_coverage_and_the_scored_share() -> None:
    news = _news(["2026-11-10 08:00", "2026-11-10 09:00", "2026-11-10 10:00", "2026-11-11 08:00"],
                 [True, False, False, False])
    out = nc.readout(news)
    assert out["rows"] == 4 and out["scored"] == 1 and out["scored_share"] == 0.25
    assert out["first_received_utc"].startswith("2026-11-10T08:00") and out["days"] == 1.0
    assert out["backfilled_dropped"] == 0


def test_a_gap_over_the_outage_in_open_market_time_is_an_outage_and_a_weekend_is_not() -> None:
    news = _news(["2026-11-06 20:00",            # Friday: the market closes 22:00 (winter)
                  "2026-11-08 23:30",            # Sunday after the open: 1.5 h of open time -> not an outage
                  "2026-11-10 08:00",            # Tuesday: 32.5 h of open time since Sunday -> an outage
                  "2026-11-10 09:00"])
    gaps = nc.outages(news)
    assert len(gaps) == 1
    g = gaps[0]
    assert g["span_from"] == pd.Timestamp("2026-11-08 23:30", tz="UTC")
    assert g["span_to"] == pd.Timestamp("2026-11-10 08:00", tz="UTC") and g["open_hours"] > OUTAGE / pd.Timedelta(hours=1)


def test_the_job_writes_each_outage_once_as_a_warning_and_returns_the_readout(tmp_path: Path) -> None:
    store = _store(tmp_path, _news(["2026-11-08 23:30", "2026-11-10 08:00", "2026-11-10 09:00"]))
    ctx = SimpleNamespace(store=store, state_dir=tmp_path / "state")
    out = JOBS["news_coverage"](ctx, NOW)  # type: ignore[arg-type]
    assert out["rows"] == 3 and out["outages"] == 1 and out["outages_new"] == 1
    ev = store.read("dq_events", source="rss")
    assert len(ev) == 1 and ev["check"].iloc[0] == "news_outage" and ev["severity"].iloc[0] == "warning"
    assert pd.Timestamp(ev["span_from"].iloc[0]) == pd.Timestamp("2026-11-08 23:30", tz="UTC")
    again = JOBS["news_coverage"](ctx, NOW)  # type: ignore[arg-type]
    assert again["outages_new"] == 0 and len(store.read("dq_events", source="rss")) == 1


def test_an_empty_news_table_reads_zero_and_writes_nothing(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    out = JOBS["news_coverage"](SimpleNamespace(store=store, state_dir=tmp_path / "state"), NOW)  # type: ignore[arg-type]
    assert out["rows"] == 0 and out["outages"] == 0
    assert store.read("dq_events", source="rss").empty


def test_the_schedule_carries_the_job() -> None:
    from goldbot.config import load_settings
    assert load_settings().scheduler.news_coverage.kind == "daily"


def test_the_scored_share_counts_prefiltered_headlines_and_the_trailing_three_days() -> None:
    """BACKLOG 259: only prefiltered headlines are ever scored (by design), so the share that measures the scorer is
    prefiltered-scored / prefiltered; `scored_share_3d` is that share over the trailing SCORED_WINDOW, which reads
    the scorer's health now rather than history from before rules-v1 existed (2026-11-13)."""
    news = _news(["2026-11-01 08:00", "2026-11-01 09:00", "2026-11-12 08:00", "2026-11-13 08:00", "2026-11-13 09:00",
                  "2026-11-14 06:00"], [False, False, True, True, False, True])
    news.loc[4, "title"] = "Agencies extend comment period"          # not prefiltered: never scored by design
    out = nc.readout(news, NOW)
    assert out["scored_share"] == 0.5                                # the old share: every kept row
    assert out["prefiltered"] == 5 and out["prefiltered_scored"] == 3
    assert out["prefiltered_scored_share"] == 0.6
    assert out["prefiltered_3d"] == 3 and out["scored_share_3d"] == 1.0
    assert nc.readout(news.iloc[:2], NOW)["scored_share_3d"] is None  # nothing in the window: no share
