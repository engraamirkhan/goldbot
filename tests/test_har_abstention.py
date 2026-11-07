"""The N-3 nightly HAR job (docs/research/prereg-2026-11-07-n3-har.md, goldbot/research/har_abstention.py): the forecast
for the next trading opportunity day is computed only from bars visible at the 22:00 UTC cut-off, the OLS fit never
reads a row at or after the holdout start, a low day is hat <= the trailing 250 forecasts' 20 % quantile, a stale or
short input writes nothing and fails the job, the first row of a day is kept, and `enabled: false` writes an `off` row."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.config import HarAbstentionSettings, ResearchSettings
from goldbot.db import Database
from goldbot.research import har_abstention as har

CFG = HarAbstentionSettings(fit_from=date(2023, 1, 1), fit_to=date(2025, 10, 1), min_fit_rows=200)
FRI_SLOT = pd.Timestamp("2025-11-07 22:05", tz="UTC")          # a Friday night: the next trading day is Monday
TUE_SLOT = pd.Timestamp("2025-11-04 22:05", tz="UTC")


def _bars(start: str = "2023-01-02", end: str = "2025-11-08", seed: int = 7) -> pd.DataFrame:
    """1h bid/ask bars on weekdays (a Sunday-evening open belongs to Monday's opportunity day), with a persistent
    daily volatility so the HAR fit has something to forecast."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, end)
    vol = np.exp(np.cumsum(rng.normal(0, 0.15, len(days))) * 0.3)
    rows: list[pd.Timestamp] = []
    sig: list[float] = []
    for d, v in zip(days, vol, strict=True):
        hours = pd.date_range(d - pd.Timedelta(hours=1), periods=22, freq="h", tz="UTC")   # 23:00 D-1 .. 20:00 D
        rows += list(hours)
        sig += [float(v)] * len(hours)
    ts = pd.DatetimeIndex(rows)
    step = rng.normal(0, 1, len(ts)) * np.asarray(sig)
    close = 2000 + np.cumsum(step)
    open_ = np.r_[close[0], close[:-1]]
    hi = np.maximum(open_, close) + np.abs(rng.normal(0, 1, len(ts))) * np.asarray(sig)
    lo = np.minimum(open_, close) - np.abs(rng.normal(0, 1, len(ts))) * np.asarray(sig)
    out = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(hours=1)})
    for side, off in (("bid", 0.0), ("ask", 0.2)):
        for c, v in (("open", open_), ("high", hi), ("low", lo), ("close", close)):
            out[f"{side}_{c}"] = v + off
    return out


BARS = _bars()


def _cut(df: pd.DataFrame, cut: pd.Timestamp) -> pd.DataFrame:
    return df[df["visible_at"] <= cut].reset_index(drop=True)


# ------------------------------------------------------------------------------------------------ the forecast
def test_the_cut_off_is_the_latest_2200_utc_close_at_or_before_the_slot():
    assert har.cut_off(pd.Timestamp("2025-11-04 22:05", tz="UTC")) == pd.Timestamp("2025-11-04 22:00", tz="UTC")
    assert har.cut_off(pd.Timestamp("2025-11-04 21:59", tz="UTC")) == pd.Timestamp("2025-11-03 22:00", tz="UTC")


@pytest.mark.parametrize("slot, day", [("2025-11-04 22:05", "2025-11-05"), ("2025-11-07 22:05", "2025-11-10"),
                                       ("2025-11-08 22:05", "2025-11-10"), ("2025-11-09 22:05", "2025-11-10")])
def test_the_row_is_for_the_next_trading_opportunity_day(slot, day):
    assert har.next_trading_day(har.cut_off(pd.Timestamp(slot, tz="UTC"))) == pd.Timestamp(day).date()


def test_a_row_carries_the_forecast_its_trailing_q20_and_the_low_flag():
    cut = har.cut_off(TUE_SLOT)
    row = har.forecast(har.mid_1h(BARS), cut, CFG, now=TUE_SLOT)
    assert row.for_day == pd.Timestamp("2025-11-05").date() and row.status == "ok"
    assert row.hat is not None and row.q20 is not None
    assert row.n_trailing == CFG.trailing_days and row.low == (row.hat <= row.q20)
    assert row.last_input_day == pd.Timestamp("2025-11-04").date() and row.inputs_close_utc == cut


def test_the_forecast_reads_only_bars_visible_at_the_cut_off():
    """Bars after 22:00 (or visible after it) cannot change the row: here they are replaced by wild values."""
    cut = har.cut_off(TUE_SLOT)
    base = har.forecast(har.mid_1h(BARS), cut, CFG, now=TUE_SLOT)
    later = BARS.copy()
    after = later["visible_at"] > cut
    for c in [c for c in later.columns if c.startswith(("bid_", "ask_"))]:
        later.loc[after, c] = later.loc[after, c] * 3.0
    got = har.forecast(har.mid_1h(later), cut, CFG, now=TUE_SLOT)
    assert (got.hat, got.q20, got.low) == (base.hat, base.q20, base.low)


def test_the_fit_never_reads_a_row_at_or_after_the_holdout_start():
    inputs = har.inputs_of(har.mid_1h(BARS), har.cut_off(TUE_SLOT))
    fit = har.frozen_fit(inputs, CFG)
    changed = inputs.copy()
    late = pd.DatetimeIndex(changed["close_utc"]) >= pd.Timestamp("2025-10-01", tz="UTC")
    changed.loc[late, "y"] = changed.loc[late, "y"] + 5.0
    assert har.frozen_fit(changed, CFG).coef == fit.coef
    assert fit.last_target_close is not None and fit.last_target_close < pd.Timestamp("2025-10-01", tz="UTC")


def test_a_stale_input_day_writes_no_row():
    """The last stub-free day must be the trading day before the forecast day (here Tuesday's bars are missing)."""
    cut = har.cut_off(TUE_SLOT)
    gap = BARS[~((BARS["ts_utc"] >= "2025-11-03 22:00") & (BARS["ts_utc"] < cut))]
    with pytest.raises(har.HarUnavailable, match="stale"):
        har.forecast(har.mid_1h(gap), cut, CFG, now=TUE_SLOT)


def test_too_few_trailing_forecasts_write_no_row():
    with pytest.raises(har.HarUnavailable, match="trailing"):
        har.forecast(har.mid_1h(BARS), har.cut_off(TUE_SLOT), CFG.model_copy(update={"trailing_days": 5000}),
                     now=TUE_SLOT)


def test_a_fit_window_after_the_holdout_start_is_refused_at_load():
    with pytest.raises(ValidationError, match="holdout"):
        ResearchSettings(har_abstention=HarAbstentionSettings(fit_to=date(2025, 11, 1)))


# ------------------------------------------------------------------------------------------------ bars and the job
class _Store:
    """The release's 1h bars before the broker's first live minute, the broker's 1m live bars from it on."""

    def __init__(self, release: pd.DataFrame, live: pd.DataFrame):
        self.release, self.live = release, live

    def read(self, table: str, *, source: str | None = None, symbol: str | None = None, start: Any = None,
             end: Any = None, columns: Any = None) -> pd.DataFrame:
        if table == "bars_1h" and source == "dukascopy":
            return self.release
        if table == "bars_1m_live" and source == "icm":
            return self.live
        return pd.DataFrame()


def _minutes(h1: pd.DataFrame) -> pd.DataFrame:
    """Each hour bar as 60 identical-range minutes (open/close at the ends, the hour's high and low inside)."""
    reps = h1.loc[h1.index.repeat(60)].reset_index(drop=True)
    reps["ts_utc"] = reps["ts_utc"] + pd.to_timedelta(np.tile(np.arange(60), len(h1)), unit="min")
    reps["visible_at"] = reps["ts_utc"] + pd.Timedelta(minutes=1)
    return reps


def _ctx(tmp_path: Any, store: Any, enabled: bool = True) -> Any:
    settings = SimpleNamespace(research=SimpleNamespace(har_abstention=CFG.model_copy(update={"enabled": enabled})))
    acc = SimpleNamespace(account_id="icm-demo", broker="icm", symbol="XAUUSD", enabled=True)
    return SimpleNamespace(settings=settings, store=store, state_dir=tmp_path, accounts=[acc])


def _split(cut_over: str = "2025-10-20 05:00") -> _Store:
    co = pd.Timestamp(cut_over, tz="UTC")
    return _Store(BARS[BARS["ts_utc"] < co].reset_index(drop=True),
                  _minutes(BARS[BARS["ts_utc"] >= co].reset_index(drop=True)))


def test_the_job_bars_are_the_release_up_to_the_live_feeds_first_minute_and_the_live_feed_after():
    store = _split()
    cut = har.cut_off(TUE_SLOT)
    got = har.read_bars(store, broker="icm", account_id="icm-demo", symbol="XAUUSD", cut=cut)
    want = har.mid_1h(_cut(BARS, cut))
    pd.testing.assert_frame_equal(got[["ts_utc", "high", "low"]].reset_index(drop=True),
                                  want[["ts_utc", "high", "low"]].reset_index(drop=True), check_dtype=False)


def test_the_job_writes_the_row_and_a_later_run_for_the_same_day_keeps_the_first(tmp_path):
    out = har.run_job(_ctx(tmp_path, _split()), FRI_SLOT, now=FRI_SLOT)
    assert out["status"] == "ok" and out["for_day"] == "2025-11-10"
    db = Database.open(str(tmp_path), "aux")
    first = har.row_for(db, pd.Timestamp("2025-11-10").date(), FRI_SLOT + pd.Timedelta(minutes=1))
    assert first is not None
    har.run_job(_ctx(tmp_path, _split()), FRI_SLOT + pd.Timedelta(days=1), now=FRI_SLOT + pd.Timedelta(days=1))
    again = har.row_for(db, pd.Timestamp("2025-11-10").date(), FRI_SLOT + pd.Timedelta(days=2))
    assert again == first


def test_a_disabled_job_writes_an_off_row(tmp_path):
    out = har.run_job(_ctx(tmp_path, _split(), enabled=False), TUE_SLOT, now=TUE_SLOT)
    assert out["status"] == "off"
    latest = har.latest_row(Database.open(str(tmp_path), "aux"), TUE_SLOT + pd.Timedelta(minutes=1))
    assert latest is not None and latest.status == "off"


def test_a_job_without_bars_fails_and_writes_nothing(tmp_path):
    with pytest.raises(har.HarUnavailable):
        har.run_job(_ctx(tmp_path, _Store(pd.DataFrame(), pd.DataFrame())), TUE_SLOT, now=TUE_SLOT)
    assert har.latest_row(Database.open(str(tmp_path), "aux"), TUE_SLOT + pd.Timedelta(days=1)) is None


def test_the_job_is_scheduled_after_the_daily_close():
    from goldbot.config import load_settings
    from goldbot.ops.jobs import JOBS
    s = load_settings().scheduler.har_days
    assert "har_days" in JOBS and s.kind == "daily" and "22:00" < s.at < "23:00"
