"""dukascopy_year.py end to end with the network call faked: month assembly, side imputation, the coverage
report, the Parquet written, and the incremental pass that keeps complete months from the published file."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.integration

_spec = importlib.util.spec_from_file_location("dukascopy_year_it", Path(__file__).resolve().parents[1] / "scripts" / "dukascopy_year.py")
assert _spec is not None and _spec.loader is not None
dy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dy)

TODAY = dt.date(2024, 5, 20)


class _FixedDate(dt.date):
    @classmethod
    def today(cls) -> "_FixedDate":
        return cls(TODAY.year, TODAY.month, TODAY.day)


@pytest.fixture
def fake_dukascopy(monkeypatch):
    calls: list[tuple[str, dt.date]] = []
    dead_ask = {"2024-03"}   # Dukascopy never serves the ask side for March

    def fetch(start, end, side, outdir, salvage=False):
        calls.append((side, start))
        if side == "ask" and f"{start:%Y-%m}" in dead_ask:
            return None, "rc=1 Request failed with status 503"
        ts = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), freq="1min", inclusive="left")
        ts = ts[ts.dayofweek < 5]
        px = 2000 + np.cumsum(np.random.default_rng(0).normal(0, 0.1, len(ts))) + (0.3 if side == "ask" else 0.0)
        return pd.DataFrame({"ts_utc": ts, "open": px, "high": px + 0.1, "low": px - 0.1, "close": px, "volume": 1.0}), ""

    monkeypatch.setattr(dy, "_fetch", fetch)
    monkeypatch.setattr(dy.time, "sleep", lambda s: None)
    monkeypatch.setattr(dy.dt, "date", _FixedDate)
    return calls


def _run(monkeypatch, tmp_path: Path, *extra: str) -> tuple[int, str]:
    out = tmp_path / f"out{len(list(tmp_path.glob('out*')))}.parquet"
    report = tmp_path / "report.md"
    monkeypatch.setattr(sys, "argv", ["dukascopy_year.py", "2024", "--out", str(out), "--report", str(report),
                                      "--tmp", str(tmp_path / "raw"), *extra])
    return dy.main(), str(out)


def test_full_build_then_incremental_gap_fill(fake_dukascopy, monkeypatch, tmp_path):
    rc, first = _run(monkeypatch, tmp_path)
    assert rc == 0
    bars = pd.read_parquet(first)
    months = bars["ts_utc"].dt.strftime("%Y-%m")
    assert sorted(months.unique()) == ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05"]
    # the exclusive end keeps each month's last trading day
    assert (bars["ts_utc"].dt.date == dt.date(2024, 1, 31)).any()
    assert (bars["ts_utc"].dt.date == dt.date(2024, 4, 30)).any()
    # March's ask side was imputed from bid and flagged
    march = bars[months == "2024-03"]
    assert (march["dq_flag"] == "warning:ask_imputed").all()
    assert ((march["ask_close"] - march["bid_close"]).round(2) == 0.25).all()
    assert (bars["ask_close"] >= bars["bid_close"]).all()
    report = (tmp_path / "report.md").read_text()
    assert "| 2024-03 |" in report and "imputed" in report and "5/5 months complete" in report

    n_first = len(fake_dukascopy)
    rc, second = _run(monkeypatch, tmp_path, "--existing", first)
    assert rc == 0
    refetched = {f"{s:%Y-%m}" for _, s in fake_dukascopy[n_first:]}
    assert refetched == {"2024-03", "2024-05"}          # imputed month + the still-open month only
    again = pd.read_parquet(second)
    assert len(again) == len(bars) and list(again.columns) == list(bars.columns)


def test_failed_refetch_keeps_previously_published_month(fake_dukascopy, monkeypatch, tmp_path):
    rc, first = _run(monkeypatch, tmp_path)
    assert rc == 0
    published = pd.read_parquet(first)

    def down(start, end, side, outdir, salvage=False):
        return None, "rc=1 Request failed with status 503"
    monkeypatch.setattr(dy, "_fetch", down)
    rc, second = _run(monkeypatch, tmp_path, "--existing", first, "--refetch-all")
    assert rc == 0                                        # nothing lost: every month fell back to the published bars
    assert len(pd.read_parquet(second)) == len(published)
