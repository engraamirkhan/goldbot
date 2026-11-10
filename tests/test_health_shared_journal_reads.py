"""order_failed:<account> and data_quality read through the health pass's part-file caches (CPU attribution
2026-11-10, round 3: `_failed_order_retcodes` made a fresh DuckDB read of the whole decisions table, 1.7-2.1 s per
warm pass, and data_quality the same of dq_events, 0.4-0.5 s). The rule kept: a cached read is answered only while
the table's part files are the ones it read (Store writes every part under a new name, never rewrites one), filtered
to the check's window, so every verdict and retcode is the one a fresh read gives. history_gap and order_failed share
one read from the earlier of their windows, so they never evict each other, across a month boundary too."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from goldbot.data.quality import DQEvent, events_frame
from goldbot.data.store import Store
from goldbot.ops import health
from goldbot.ops.checks import engine as engine_checks
from goldbot.ops.checks import feeds as feed_checks
from goldbot.ops.checks._base import FAILED_ORDER_WINDOW_H
from tests.test_health import NOW, SETTINGS, make_ctx, order_row, write
from tests.test_history_gap_health import ACC, GAP


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every Store.read the checks make, by table."""
    calls: list[str] = []
    real = Store.read

    def counted(self: Store, table: str, **kw: object) -> pd.DataFrame:
        calls.append(table)
        return real(self, table, **kw)  # type: ignore[arg-type]
    monkeypatch.setattr(Store, "read", counted)
    return calls


def _journal(root: str, rows: list[tuple[pd.Timestamp, str, str, dict]]) -> None:
    Store(root).append("decisions", pd.DataFrame([
        {"ts_utc": ts, "account_id": ACC.account_id, "agent_id": agent, "action": action, "p": 0.0, "mult": 0.0,
         "proposal_id": None, "detail": json.dumps(detail)} for ts, agent, action, detail in rows]),
        source=ACC.account_id, symbol=ACC.symbol, dedupe=False)


def _account(tmp_path: Path, now: pd.Timestamp = NOW) -> health.HealthContext:
    s = SETTINGS.model_copy(update={"data_root": str(tmp_path / "data")})
    write(tmp_path / f"engine_{ACC.account_id}.json", {"history_gap": GAP})
    return make_ctx(tmp_path, now=now, settings=s, accounts=[ACC])


def _orders(tmp_path: Path, sent: dict[str, pd.Timestamp]) -> None:
    write(tmp_path / f"orders_{ACC.account_id}.json",
          {"sent": {cid: order_row("rejected", ts) for cid, ts in sent.items()}, "open": {}})


def _fail(ts: pd.Timestamp, retcode: int) -> tuple[pd.Timestamp, str, str, dict]:
    return (ts + pd.Timedelta(seconds=2), "trend-0", "order", {"ok": False, "retcode": retcode})


def _pass(ctx: health.HealthContext) -> tuple[object, list[str]]:
    gap = health.check_history_gap(ctx, ACC)
    return gap, [c.reason for c in health.check_failed_orders(ctx, ACC)]


def _fresh_retcodes(ctx: health.HealthContext) -> list[tuple[pd.Timestamp, str, object]]:
    """The pre-cache reader, verbatim: a bounded Store.read of the window."""
    assert ctx.settings is not None
    since = ctx.now - pd.Timedelta(hours=FAILED_ORDER_WINDOW_H)
    d = Store(ctx.settings.data_root).read("decisions", source=ACC.account_id, symbol=ACC.symbol, start=since,
                                           end=ctx.now)
    d = d[d["action"] == "order"]
    return [(pd.Timestamp(r["ts_utc"]), str(r["agent_id"]), json.loads(r["detail"]).get("retcode"))
            for _, r in d.iterrows() if json.loads(r["detail"]).get("ok") is False]


def test_history_gap_and_order_failed_share_one_read_of_an_unchanged_journal(tmp_path: Path, reads: list[str]):
    ctx = _account(tmp_path)
    sent = NOW - pd.Timedelta(hours=10)                      # inside order_failed's 24 h, outside history_gap's 6 h
    _orders(tmp_path, {"p-bad": sent})
    _journal(str(tmp_path / "data"), [_fail(sent, 10006), (NOW - pd.Timedelta(hours=1), "a", "history_gap:1h", {})])
    first = _pass(ctx)
    assert "retcode 10006" in first[1][0]
    for _ in range(3):
        assert _pass(ctx) == first
    assert reads == ["decisions"]


def test_the_shared_read_does_not_thrash_when_the_failed_order_window_spans_a_month_boundary(
        tmp_path: Path, reads: list[str]):
    now = pd.Timestamp("2026-11-01 08:00", tz="UTC")         # 6 h back is November, 24 h back is October
    ctx = _account(tmp_path, now)
    sent = pd.Timestamp("2026-10-31 20:00", tz="UTC")
    _orders(tmp_path, {"p-bad": sent})
    _journal(str(tmp_path / "data"), [_fail(sent, 10019), (now - pd.Timedelta(hours=1), "a", "flat", {})])
    first = _pass(ctx)
    assert "retcode 10019" in first[1][0]
    assert _pass(ctx) == first
    assert reads == ["decisions"]


def test_a_failed_order_journalled_after_a_pass_is_reported_with_its_retcode_on_the_next_pass(
        tmp_path: Path, reads: list[str]):
    ctx = _account(tmp_path)
    one = NOW - pd.Timedelta(hours=3)
    _orders(tmp_path, {"p-one": one})
    _journal(str(tmp_path / "data"), [_fail(one, 10006)])
    assert ["retcode 10006" in r for r in _pass(ctx)[1]] == [True]
    two = NOW - pd.Timedelta(minutes=30)
    _orders(tmp_path, {"p-one": one, "p-two": two})
    _journal(str(tmp_path / "data"), [_fail(two, 10031)])     # the engine's next flush: a new part file
    later = _pass(ctx)[1]
    assert "retcode 10006" in later[0] and "retcode 10031" in later[1]
    assert reads == ["decisions", "decisions"]


def test_the_cached_retcodes_equal_a_fresh_bounded_read(tmp_path: Path):
    ctx = _account(tmp_path)
    rows = [_fail(NOW - pd.Timedelta(hours=h), 10000 + h) for h in (1, 5, 23, 25, 30)]          # 25 h, 30 h: outside
    rows.append((NOW + pd.Timedelta(minutes=1), "trend-0", "order", {"ok": False, "retcode": 1}))  # after now: outside
    _journal(str(tmp_path / "data"), rows)
    since = NOW - pd.Timedelta(hours=FAILED_ORDER_WINDOW_H)
    for _ in range(2):                                        # the read, then the cached rows
        assert engine_checks._failed_order_retcodes(ctx, ACC.account_id, since, ACC.symbol) == _fresh_retcodes(ctx)
    assert [rc for _, _, rc in _fresh_retcodes(ctx)] == [10023, 10005, 10001]


def _dq(tmp_path: Path, ts: pd.Timestamp, severity: str, check: str) -> None:
    Store(tmp_path / "data").append("dq_events", events_frame([DQEvent(ts_utc=ts, check=check, severity=severity,
                                                                       detail="x")]), source="icm-demo")


def test_data_quality_reads_dq_events_once_while_no_event_is_written(tmp_path: Path, reads: list[str]):
    ctx = _account(tmp_path)
    _dq(tmp_path, NOW - pd.Timedelta(hours=2), "warning", "spike")
    first = health.check_data_quality(ctx)
    assert first.status == "ok"
    for _ in range(3):
        assert health.check_data_quality(ctx) == first
    assert reads == ["dq_events"]


def test_a_new_error_event_is_seen_on_the_next_pass_and_matches_a_fresh_read(tmp_path: Path, reads: list[str]):
    ctx = _account(tmp_path)
    _dq(tmp_path, NOW - pd.Timedelta(hours=2), "warning", "spike")
    assert health.check_data_quality(ctx).status == "ok"
    _dq(tmp_path, NOW - pd.Timedelta(hours=1), "error", "duplicate_ts")
    c = health.check_data_quality(ctx)
    assert c.status == "warn" and "duplicate_ts x1" in c.reason
    assert reads == ["dq_events", "dq_events"]
    feed_checks._DQ.clear()
    assert health.check_data_quality(ctx) == c                # the same verdict a fresh read gives


def test_an_event_of_another_symbol_also_invalidates_the_dq_read(tmp_path: Path, reads: list[str]):
    """A part of any symbol can widen the table's columns (union_by_name over the whole table): any new part re-reads."""
    ctx = _account(tmp_path)
    _dq(tmp_path, NOW - pd.Timedelta(hours=2), "warning", "spike")
    health.check_data_quality(ctx)
    Store(tmp_path / "data").append("dq_events", events_frame([DQEvent(ts_utc=NOW - pd.Timedelta(hours=1),
                                                                       check="spike", severity="warning", detail="x")]),
                                    source="icm-demo", symbol="XAGUSD")
    health.check_data_quality(ctx)
    assert reads == ["dq_events", "dq_events"]
