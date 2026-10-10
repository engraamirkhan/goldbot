"""Data quality (design: Data architecture, row D22): warnings commit with a dq_flag; error rows are quarantined in
`bars_quarantine`, never in the bar tables, and the health check alerts on error events."""
import pandas as pd
import pytest

from goldbot.data.quality import DQEvent, check_bars, events_frame, quarantine
from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.ops import health
from tests.test_health import NOW, SETTINGS, make_ctx


@pytest.fixture(scope="module")
def bars1m() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2025-03-03", "2025-03-05", ticks_per_minute=3))


def test_error_rows_are_quarantined_and_warnings_stay(bars1m):
    b = bars1m.copy()
    i = len(b) // 2
    b.loc[i, ["bid_close", "ask_close"]] = b.loc[i, ["bid_close", "ask_close"]] * 1.20   # spike: a warning
    b.loc[i + 5, "ask_close"] = b.loc[i + 5, "bid_close"] - 1.0           # bid > ask: an error
    dup = b.iloc[[10]].copy()                                              # a re-delivered minute
    flagged, _ = check_bars(pd.concat([b, dup], ignore_index=True))
    clean, held = quarantine(flagged)
    assert len(clean) + len(held) == len(flagged) and len(held) == 2
    assert not clean["dq_flag"].str.contains("error:").any() and held["dq_flag"].str.startswith("error:").all()
    assert clean["dq_flag"].str.contains("warning:spike").any()           # warnings commit with their flag
    assert clean["ts_utc"].is_unique and b["ts_utc"].iloc[10] in set(clean["ts_utc"])   # the last copy is kept
    assert b["ts_utc"].iloc[i + 5] not in set(clean["ts_utc"])


def test_a_clean_batch_passes_whole(bars1m):
    flagged, _ = check_bars(bars1m)
    clean, held = quarantine(flagged)
    assert len(clean) == len(bars1m) and held.empty
    assert quarantine(bars1m)[1].empty                                     # unchecked frame: nothing to split


def test_the_store_keeps_quarantined_rows_in_their_own_table(tmp_path, bars1m):
    store = Store(tmp_path)
    store.append("bars_quarantine", bars1m.head(3).assign(dq_flag="error:bid_gt_ask"), source="icm", dedupe=False)
    assert len(store.read("bars_quarantine", source="icm")) == 3
    assert store.read("bars_1m", source="icm").empty


def _ctx(tmp_path):
    s = SETTINGS.model_copy(update={"data_root": str(tmp_path / "data")})
    return make_ctx(tmp_path, settings=s)


def _event(ts: pd.Timestamp, severity: str, check: str) -> DQEvent:
    return DQEvent(ts_utc=ts, check=check, severity=severity, detail="x")


def test_health_alerts_on_error_events_of_the_last_day_only(tmp_path):
    ctx = _ctx(tmp_path)
    assert health.check_data_quality(ctx).status == "ok"                  # nothing recorded
    store = Store(tmp_path / "data")
    store.append("dq_events", events_frame([_event(NOW - pd.Timedelta(hours=2), "warning", "spike"),
                                            _event(NOW - pd.Timedelta(days=3), "error", "bid_gt_ask")]), source="icm-demo")
    assert health.check_data_quality(ctx).status == "ok"                  # a warning, and an old error
    store.append("dq_events", events_frame([_event(NOW - pd.Timedelta(hours=1), "error", "duplicate_ts")]),
                 source="icm-demo")
    c = health.check_data_quality(ctx)
    assert c.status == "warn" and "duplicate_ts x1" in c.reason and "quarantined" in c.reason
