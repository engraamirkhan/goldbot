"""Quant ruling 2026-11-06 Q9: the health check warns when the cost of record's ICM (or any broker's) cells were last
measured more than 30 days ago (goldbot/ops/checks/feeds.py `check_cost_of_record`)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from goldbot.ops.checks import feeds
from tests.test_health import make_ctx


def _record(tmp_path: Path, end: str) -> Path:
    f = tmp_path / "cost_of_record.yaml"
    f.write_text(f'version: v\nwindow_utc: ["2026-09-01T22:00:00+00:00", "{end}"]\ncells:\n  icm: {{}}\n  vantage: {{}}\n')
    return f


def test_icm_cells_older_than_thirty_days_warn_and_fresh_ones_pass(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    monkeypatch.setattr(feeds, "COST_OF_RECORD_PATH",
                        _record(tmp_path, (ctx.now - pd.Timedelta(days=31)).isoformat()))
    c = feeds.check_cost_of_record(ctx)
    assert c.status == "warn" and "icm, vantage last measured 31 d ago" in c.reason
    monkeypatch.setattr(feeds, "COST_OF_RECORD_PATH",
                        _record(tmp_path, (ctx.now - pd.Timedelta(days=29)).isoformat()))
    assert feeds.check_cost_of_record(ctx).status == "ok"


def test_an_unreadable_cost_of_record_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(feeds, "COST_OF_RECORD_PATH", tmp_path / "missing.yaml")
    c = feeds.check_cost_of_record(make_ctx(tmp_path))
    assert c.status == "warn" and "unreadable" in c.reason


def test_the_repositorys_cost_of_record_is_readable():
    assert feeds.COST_OF_RECORD_PATH.exists()
