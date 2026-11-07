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


def _closed_day(bars: pd.DataFrame, day: str) -> pd.DataFrame:
    """`bars` without the opportunity day `day` (D-1 22:00 to D 22:00 UTC): a full CME closure."""
    d = pd.Timestamp(day, tz="UTC")
    return bars[~((bars["ts_utc"] >= d - pd.Timedelta(hours=2)) & (bars["ts_utc"] < d + pd.Timedelta(hours=22)))]


@pytest.mark.parametrize("slot, closed, day, last", [
    ("2025-04-18 22:05", "2025-04-18", "2025-04-21", "2025-04-17"),    # Good Friday -> Monday
    ("2024-12-25 22:05", "2024-12-25", "2024-12-26", "2024-12-24"),    # Christmas (Wednesday) -> Thursday
    ("2025-01-01 22:05", "2025-01-01", "2025-01-02", "2024-12-31"),    # New Year (Wednesday) -> Thursday
], ids=["good_friday", "25_dec", "1_jan"])
def test_the_day_after_a_full_close_holiday_gets_a_row(slot, closed, day, last):
    """Review (quant): the input is fresh when every weekday between the last input day and the forecast day is a
    gold full-close holiday (Good Friday, 25 Dec, 1 Jan; goldbot/data/cme_holidays.py)."""
    at = pd.Timestamp(slot, tz="UTC")
    row = har.forecast(har.mid_1h(_closed_day(BARS, closed)), har.cut_off(at), CFG, now=at)
    assert row.for_day == pd.Timestamp(day).date() and row.last_input_day == pd.Timestamp(last).date()


def test_the_monday_after_good_friday_gets_a_row():
    at = pd.Timestamp("2025-04-18 22:05", tz="UTC")
    row = har.forecast(har.mid_1h(_closed_day(BARS, "2025-04-18")), har.cut_off(at), CFG, now=at)
    assert row.for_day == pd.Timestamp("2025-04-21").date()


def test_a_missing_ordinary_weekday_before_a_holiday_is_still_stale():
    """Thursday 2025-04-17 is missing too: only the holiday itself may be skipped."""
    at = pd.Timestamp("2025-04-18 22:05", tz="UTC")
    bars = _closed_day(_closed_day(BARS, "2025-04-18"), "2025-04-17")
    with pytest.raises(har.HarUnavailable, match="stale"):
        har.forecast(har.mid_1h(bars), har.cut_off(at), CFG, now=at)


def test_the_gap_window_reaches_back_over_the_atr_warm_up_of_the_first_har_lookback_day():
    """Review: w = W / ATR(20) shifted, so the HAR(22) lookback's first day reads ATR_DAYS more input days."""
    from goldbot.features import day_type as dt
    days = pd.bdate_range("2024-01-01", periods=300)
    days = days.where(np.arange(300) < 16, days + pd.Timedelta(days=14))     # a 14-day hole between rows 15 and 16
    inputs = pd.DataFrame({"day": days})
    cfg = CFG.model_copy(update={"trailing_days": 250})
    first = 300 - 250
    assert first - max(dt.HAR_LAGS) - dt.ATR_DAYS <= 15 < first - max(dt.HAR_LAGS)
    with pytest.raises(har.HarUnavailable, match="gap"):
        har._check_gaps(inputs, np.arange(300), cfg)


def test_too_few_trailing_forecasts_write_no_row():
    with pytest.raises(har.HarUnavailable, match="trailing"):
        har.forecast(har.mid_1h(BARS), har.cut_off(TUE_SLOT), CFG.model_copy(update={"trailing_days": 5000}),
                     now=TUE_SLOT)


def test_a_three_month_hole_in_the_trailing_window_writes_no_row():
    """MED-2: a gap of more than `max_gap_days` (4) calendar days between consecutive input days, anywhere in the
    trailing 250 forecasts or their HAR(22) lookback, fails the job: the quantile would rest on a broken series."""
    cut = har.cut_off(TUE_SLOT)
    hole = BARS[~((BARS["ts_utc"] >= "2025-03-01") & (BARS["ts_utc"] < "2025-06-01"))]
    with pytest.raises(har.HarUnavailable, match="gap"):
        har.forecast(har.mid_1h(hole), cut, CFG, now=TUE_SLOT)


def test_a_hole_in_the_window_also_fails_the_job_and_writes_nothing(tmp_path):
    hole = BARS[~((BARS["ts_utc"] >= "2025-03-01") & (BARS["ts_utc"] < "2025-06-01"))].reset_index(drop=True)
    with pytest.raises(har.HarUnavailable, match="gap"):
        har.run_job(_ctx(tmp_path, _Store(hole, pd.DataFrame())), TUE_SLOT, now=TUE_SLOT)
    assert har.latest_row(Database.open(str(tmp_path), "aux"), TUE_SLOT + pd.Timedelta(days=1)) is None


def test_a_long_weekend_of_four_days_is_not_a_gap():
    """Thursday -> Monday (a Friday holiday) is 4 calendar days: allowed."""
    cut = har.cut_off(TUE_SLOT)
    holiday = BARS[~((BARS["ts_utc"] >= "2025-06-05 22:00") & (BARS["ts_utc"] < "2025-06-06 22:00"))]
    assert har.forecast(har.mid_1h(holiday), cut, CFG, now=TUE_SLOT).status == "ok"


def test_a_hole_older_than_the_window_and_its_lookback_is_allowed():
    cut = har.cut_off(TUE_SLOT)
    old = BARS[~((BARS["ts_utc"] >= "2023-06-01") & (BARS["ts_utc"] < "2023-09-01"))]
    assert har.forecast(har.mid_1h(old), cut, CFG, now=TUE_SLOT).status == "ok"


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


def test_a_retry_slot_runs_the_same_job_at_2235_and_keeps_the_first_row(tmp_path):
    """Review: a second slot (har_days_retry, 22:35 UTC) writes the row when 22:05 failed; INSERT OR IGNORE keeps a
    row the first slot already wrote."""
    from goldbot.config import load_settings
    from goldbot.ops.jobs import JOBS
    s = load_settings().scheduler.har_days_retry
    assert JOBS["har_days_retry"] is JOBS["har_days"] and s.kind == "daily" and s.at == "22:35"
    store = _split()
    first = har.run_job(_ctx(tmp_path, store), TUE_SLOT, now=TUE_SLOT)
    retry_slot = TUE_SLOT.normalize() + pd.Timedelta(hours=22, minutes=35)
    again = har.run_job(_ctx(tmp_path, store), retry_slot, now=retry_slot)
    assert first["written"] and not again["written"] and again["for_day"] == first["for_day"]


def test_n3_ships_disabled_until_the_k_row_and_the_overlap_measurement():
    """Quant R7: research.har_abstention.enabled is false by default and in config/settings.yaml."""
    from goldbot.config import load_settings
    assert HarAbstentionSettings().enabled is False
    assert load_settings().research.har_abstention.enabled is False


def test_the_job_is_scheduled_after_the_daily_close():
    from goldbot.config import load_settings
    from goldbot.ops.jobs import JOBS
    s = load_settings().scheduler.har_days
    assert "har_days" in JOBS and s.kind == "daily" and "22:00" < s.at < "23:00"


def test_the_void_band_is_0_or_9_plus_low_days_of_20_as_the_prereg_states():
    """Quant re-verify 2026-11-07: a 20-day window with 0 low days or 9 or more is void (about 2 % by chance)."""
    from pathlib import Path

    from goldbot.research import har_abstention as H

    assert H.LOW_DAYS_VOID == (0, 9)
    text = Path(__file__).resolve().parents[1].joinpath("docs/research/prereg-2026-11-07-n3-har.md").read_text()
    assert "0 low days or 9 or more" in text and "12-28%" not in text
