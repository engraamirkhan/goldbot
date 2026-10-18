"""TradingView card (docs/ux/limits-and-tradingview.md, card B): `TvStatusView` says whether the direct pull's sign-in
works (state/tv_auth.db, written by goldbot/ops/tv_pull_health.py; tv.db is never opened), when it last pulled, and
counts live and history bars per indicator, the pull's rows (`origin` pull_live / pull_history) included. Amber only:
TradingView is an optional second feed and never stops trading, so nothing here is a failure state."""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from goldbot.api.tv_status import tv_status
from goldbot.data import tv_pull
from goldbot.data.store import Store
from goldbot.ops import tv_pull_health
from goldbot.ops.tv_pull_health import record_expired, record_ok, signin_state
from goldbot.webhook.inbox import TABLE, source_of

NOW = pd.Timestamp("2026-10-18 12:00", tz="UTC")


def pulled(store: Store, indicator: str, n: int, origin: str, *, end: pd.Timestamp = NOW, tf: str = "15") -> None:
    """`n` closed 15m bars ending at `end`, written the way the pull writes them (tv_pull._write)."""
    bar = pd.date_range(end=end - pd.Timedelta(minutes=15), periods=n, freq="15min", tz="UTC")
    df = pd.DataFrame({"ts_utc": bar, "bar_time": bar, "available_utc": bar + pd.Timedelta(minutes=16),
                       "fired_at": bar + pd.Timedelta(minutes=15), "received_at": bar + pd.Timedelta(minutes=16),
                       "indicator": indicator, "version": "7", "tf": tf, "price": np.arange(n, dtype="float64"),
                       "intrabar": False, "origin": origin, "meta_hash": "abc12345", "plot": "plot_0",
                       "value": np.arange(n, dtype="float64")})
    store.append(TABLE, df, source=source_of(indicator, "7"), symbol="XAUUSD")


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "data")


def test_the_origin_names_match_the_pull():
    assert (tv_pull.ORIGIN_LIVE, tv_pull.ORIGIN_HISTORY, tv_pull.CHART) == ("pull_live", "pull_history", "chart")


def test_no_tv_auth_file_reads_as_not_set_up_with_no_fix_command(tmp_path, store):
    v = tv_status(store, NOW, state_dir=tmp_path)
    assert v.signin == "not_set_up" and v.fix_command is None and v.last_pull_utc is None and v.stale is False


def test_a_recent_successful_pull_reads_as_signed_in(tmp_path, store):
    record_ok(tmp_path, NOW - pd.Timedelta(hours=8))
    v = tv_status(store, NOW, state_dir=tmp_path)
    assert v.signin == "ok" and v.stale is False and v.fix_command is None
    assert v.last_pull_utc is not None and pd.Timestamp(v.last_pull_utc) == NOW - pd.Timedelta(hours=8)


def test_no_pull_for_more_than_36_hours_is_stale_but_still_signed_in(tmp_path, store):
    record_ok(tmp_path, NOW - pd.Timedelta(hours=40))
    v = tv_status(store, NOW, state_dir=tmp_path)
    assert v.signin == "ok" and v.stale is True


def test_an_expired_cookie_gives_the_one_line_fix_command(tmp_path, store):
    record_ok(tmp_path, NOW - pd.Timedelta(hours=8))
    record_expired(tmp_path, NOW)
    v = tv_status(store, NOW, state_dir=tmp_path)
    assert v.signin == "expired" and v.last_pull_utc is not None and pd.Timestamp(v.last_pull_utc) == NOW - pd.Timedelta(hours=8)
    assert v.fix_command == ("python -m goldbot.ops.accounts set tradingview-sessionid && "
                             "python -m goldbot.ops.accounts set tradingview-sessionid-sign")
    assert "\n" not in v.fix_command


def test_an_unreadable_state_is_unknown_and_treated_as_expired(tmp_path, store):
    (tmp_path / tv_pull_health.DB_FILE).write_bytes(b"garbage" * 200)
    v = tv_status(store, NOW, state_dir=tmp_path)
    assert v.signin == "unknown" and v.fix_command is not None


def test_the_health_check_reads_the_same_state_as_the_card(tmp_path):
    record_ok(tmp_path, NOW - pd.Timedelta(hours=40))
    assert signin_state(tmp_path, NOW).stale is True
    conn = sqlite3.connect(tmp_path / tv_pull_health.DB_FILE)
    conn.execute("UPDATE tv_auth SET status = 'weird'")
    conn.commit()
    conn.close()
    assert signin_state(tmp_path, NOW).signin == "unknown"


def test_pulled_bars_count_as_live_or_history_per_indicator_and_the_chart_series_is_not_an_indicator(tmp_path, store):
    pulled(store, "Supertrend", 10, "pull_live")
    pulled(store, "Supertrend", 200, "pull_history", end=NOW - pd.Timedelta(days=5))
    pulled(store, "chart", 50, "pull_live")
    v = tv_status(store, NOW, state_dir=tmp_path)
    (row,) = v.indicators
    assert row.indicator == "Supertrend" and row.pull_live_24h == 10 and row.pull_history_bars == 200
    assert row.repaint_status == "unvalidated"
    assert v.live_bars_24h == 10 and v.history_bars == 200


def test_history_only_shows_no_history_imported_until_some_arrives(tmp_path, store):
    pulled(store, "RSI Divergence", 5, "pull_live")
    (row,) = tv_status(store, NOW, state_dir=tmp_path).indicators
    assert row.repaint_status == "none" and row.pull_history_bars == 0


def test_the_card_never_carries_plot_values(tmp_path, store):
    pulled(store, "Supertrend", 10, "pull_live")
    text = tv_status(store, NOW, state_dir=tmp_path).model_dump_json()
    assert "abc12345" not in text and '"value"' not in text
