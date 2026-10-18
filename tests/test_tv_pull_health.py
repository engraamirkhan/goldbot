"""TradingView pull health (goldbot/ops/tv_pull_health.py): TV must never block trading or exits, so every bad state
is a WARN, never a FAIL; the owner gets one Telegram notice per expiry, kept across restarts."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from goldbot.ops.tv_pull_health import (
    NOTICE_TEXT,
    STALE_HOURS,
    check_tv_pull,
    record_expired,
    record_ok,
)

NOW = pd.Timestamp("2026-10-14 12:00", tz="UTC")


def _ctx(tmp_path: Path) -> Any:
    return SimpleNamespace(state_dir=tmp_path, now=NOW)


def _notices(tmp_path: Path) -> list[dict[str, Any]]:
    f = tmp_path / "agent_runs.jsonl"
    rows = [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
    return [r for r in rows if r.get("status") == "notice"]


def test_expiry_notifies_once_across_restart(tmp_path: Path):
    assert record_expired(tmp_path, NOW) is True
    # a restart: the pull fails again in a fresh process, same expiry
    assert record_expired(tmp_path, NOW + pd.Timedelta(minutes=5)) is False
    assert record_expired(tmp_path, NOW + pd.Timedelta(hours=3)) is False
    notes = _notices(tmp_path)
    assert len(notes) == 1 and notes[0]["detail"] == NOTICE_TEXT
    assert "goldbot.ops.accounts set tradingview-sessionid`" in NOTICE_TEXT
    assert "goldbot.ops.accounts set tradingview-sessionid-sign`" in NOTICE_TEXT


def test_next_expiry_after_a_successful_pull_notifies_again(tmp_path: Path):
    record_expired(tmp_path, NOW)
    record_ok(tmp_path, NOW + pd.Timedelta(hours=1))
    assert record_expired(tmp_path, NOW + pd.Timedelta(days=30)) is True
    assert len(_notices(tmp_path)) == 2


def test_corrupt_state_counts_as_expired(tmp_path: Path):
    record_ok(tmp_path, NOW)
    con = sqlite3.connect(tmp_path / "tv_auth.db")
    con.execute("UPDATE tv_auth SET status = 'garbage'")
    con.commit()
    con.close()
    c = check_tv_pull(_ctx(tmp_path))
    assert c.status == "warn" and "expired" in c.reason


def test_unreadable_database_counts_as_expired_and_warns(tmp_path: Path):
    (tmp_path / "tv_auth.db").write_bytes(b"not a sqlite file" * 50)
    c = check_tv_pull(_ctx(tmp_path))
    assert c.status == "warn" and "expired" in c.reason


def test_expired_state_is_warn_not_fail(tmp_path: Path):
    record_expired(tmp_path, NOW)
    c = check_tv_pull(_ctx(tmp_path))
    assert c.name == "tv_pull" and c.status == "warn"


def test_stale_pull_is_warn_not_fail(tmp_path: Path):
    record_ok(tmp_path, NOW - pd.Timedelta(hours=STALE_HOURS + 1))
    assert check_tv_pull(_ctx(tmp_path)).status == "warn"
    record_ok(tmp_path, NOW - pd.Timedelta(hours=1))
    assert check_tv_pull(_ctx(tmp_path)).status == "ok"


def test_no_state_yet_is_ok(tmp_path: Path):
    assert check_tv_pull(_ctx(tmp_path)).status == "ok"


def test_corrupt_database_is_moved_aside_and_the_expiry_still_notifies_once(tmp_path: Path):
    (tmp_path / "tv.db").write_bytes(b"webhook inbox bytes")
    (tmp_path / "tv_auth.db").write_bytes(b"not a sqlite file" * 50)
    assert record_expired(tmp_path, NOW) is True
    assert record_expired(tmp_path, NOW + pd.Timedelta(hours=1)) is False
    assert len(_notices(tmp_path)) == 1
    assert len(list(tmp_path.glob("tv_auth.db.corrupt-*"))) == 1
    assert (tmp_path / "tv.db").read_bytes() == b"webhook inbox bytes"      # the inbox is never touched
    assert check_tv_pull(_ctx(tmp_path)).status == "warn"
