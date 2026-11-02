"""The broker tick record across the account's symbol list (multi-instrument demo, lane L4; goldbot/data/ticks.py).

Design rules: "store ... keyed (source=account, symbol=store_symbol), never pooled with XAUUSD" (plan), so another
instrument's days live under state/ticks/<account>/<instrument>/ with their own index while the account's gold keeps
today's path and files byte for byte; and the pull "must never sit in front of the engine while it can trade"
(trading-safety review of BACKLOG 79): with every asset class on the bridge only the weekend is quiet for all of them,
so another instrument is pulled only then."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import TickRecordSettings, load_settings
from goldbot.data import ticks as tk
from goldbot.ops.health import AccountRef, HealthContext
from goldbot.ops.jobs import tick_record
from tests.test_tick_recorder import (
    ACC,
    NOW,
    TZ,
    FakeTickBroker,
    _bounds,
    _job_ctx,
    _week_broker,
    synthetic_day,
)

SATURDAY = pd.Timestamp("2026-10-17 12:00", tz="UTC")
BREAK = pd.Timestamp("2026-10-15 21:20", tz="UTC")       # Thursday, New York's daily break: gold quiet, US500 not


def _record(tmp_path: Path, broker: Any, now: pd.Timestamp, instrument: str | None, **kw: Any) -> dict[str, Any]:
    kw.setdefault("max_days_per_run", 10)
    return tk.record_ticks(broker, tmp_path, "icm-probe", instrument or "XAUUSD", TZ, TickRecordSettings(**kw),
                           clock=lambda: now, sleep=lambda s: None, instrument=instrument)


def test_another_instrument_is_recorded_under_its_own_directory_and_index_never_with_gold(tmp_path):
    out = _record(tmp_path, _week_broker(), SATURDAY, "US500", backfill_days=5)
    assert out["written"][-1] == "2026-10-16"
    d = tmp_path / "ticks" / "icm-probe"
    assert (d / "US500" / "2026-10-16.parquet").exists() and not list(d.glob("*.parquet"))
    assert tk.read_index(tmp_path, "icm-probe") == {}                      # gold's index untouched
    assert tk.recorded_days("icm-probe", state_dir=tmp_path, instrument="US500")[-1].isoformat() == "2026-10-16"
    day = tk.load_day("icm-probe", "2026-10-16", state_dir=tmp_path, instrument="US500")
    assert len(day) == 3_000


def test_gold_keeps_todays_path_when_no_instrument_is_named(tmp_path):
    _record(tmp_path, _week_broker(), SATURDAY, None, backfill_days=1)
    assert (tmp_path / "ticks" / "icm-probe" / "2026-10-16.parquet").exists()


def test_another_instrument_is_not_pulled_in_golds_daily_break_only_at_the_weekend(tmp_path):
    broker = _week_broker()
    out = _record(tmp_path, broker, BREAK, "US500", backfill_days=1)
    assert out["written"] == [] and "deferred" in out and broker.calls == []
    assert tk.market_quiet(BREAK) and not tk.weekend_quiet(BREAK) and tk.weekend_quiet(SATURDAY)


@pytest.mark.parametrize("bad", ["../x", "_hidden", "a/b", "", "index.json/..", "x" * 40])
def test_instrument_names_cannot_escape_the_accounts_directory(tmp_path, bad):
    with pytest.raises(ValueError):
        tk.account_dir(tmp_path, "icm-probe", bad)


def test_health_reports_each_instrument_under_its_own_name(tmp_path):
    s, e = _bounds("2026-10-16")
    tk.write_day(tmp_path, "icm-probe", pd.Timestamp("2026-10-16").date(), synthetic_day(s, e, 50), symbol="US500",
                 server_tz=TZ, pulled_utc=NOW, instrument="US500")
    ctx = HealthContext(state_dir=tmp_path, now=NOW, settings=load_settings(), accounts=[AccountRef(account_id="icm-probe")],
                        get_secret=lambda k: None, disk_usage=lambda p: (100e9, 50e9, 50e9))
    c = tk.check_tick_record(ctx, "icm-probe", instrument="US500")
    assert c.name == "ticks:icm-probe:US500" and c.status == "warn"
    assert tk.check_tick_record(ctx, "icm-probe").name == "ticks:icm-probe"


def test_the_job_pulls_the_accounts_other_symbols_at_the_weekend_and_leaves_gold_as_it_was(tmp_path, monkeypatch):
    broker = _week_broker()
    seen: list[str] = []
    real = FakeTickBroker.copy_ticks

    def spy(self: FakeTickBroker, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        seen.append(symbol)
        return real(self, symbol, since_utc, n)
    monkeypatch.setattr(FakeTickBroker, "copy_ticks", spy)
    monkeypatch.setattr("goldbot.ops.jobs.account_symbols", lambda acc: (acc.symbol, "US500"), raising=False)
    out = tick_record(_job_ctx(tmp_path, lambda acc: broker, SATURDAY), SATURDAY)
    assert out[ACC.account_id]["written"] and out[f"{ACC.account_id}:US500"]["written"]
    assert {"XAUUSD", "US500"} <= set(seen)
    seen.clear()
    weekday = tick_record(_job_ctx(tmp_path / "w", lambda acc: _week_broker(), BREAK), BREAK)   # gold quiet only
    assert "US500" not in seen and weekday[f"{ACC.account_id}:US500"]["skipped"]
