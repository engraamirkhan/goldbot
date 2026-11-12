"""history_gap:<account> reads the account's decisions journal only when it changed. The engine writes one part file
per decision flush (at most once per bar close), but the Telegram service runs the check every 60 s; the DuckDB read
of the journal cost ~10 s of one core per pass on the brain (health pass cost, 2026-11-10). Between flushes the last
read is filtered to the current window, so the check decides exactly what a fresh read would: a refusal journalled
since the last pass is seen on the next one, and refusals that leave the window stop counting on time."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from goldbot.data.store import Store
from goldbot.ops import health
from goldbot.ops.checks import engine as engine_checks
from tests.test_health import NOW
from tests.test_history_gap_health import ACC, _ctx, _hourly


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every decisions-journal read the check makes."""
    calls: list[str] = []
    real = Store.read

    def counted(self: Store, table: str, **kw: object) -> pd.DataFrame:
        calls.append(table)
        return real(self, table, **kw)  # type: ignore[arg-type]
    monkeypatch.setattr(Store, "read", counted)
    return calls


def _fresh(ctx: health.HealthContext) -> object:
    engine_checks._JOURNAL.clear()
    return health.check_history_gap(ctx, ACC)


def test_an_unchanged_journal_is_read_once_across_passes_with_the_same_verdict(tmp_path: Path, reads: list[str]):
    ctx = _ctx(tmp_path, _hourly(6))
    first = health.check_history_gap(ctx, ACC)
    assert first is not None and first.status == "warn"
    for _ in range(5):
        assert health.check_history_gap(ctx, ACC) == first
    assert reads == ["decisions"]


def test_a_refusal_journalled_after_a_pass_is_seen_on_the_next_pass(tmp_path: Path, reads: list[str]):
    ctx = _ctx(tmp_path, _hourly(3))
    c = health.check_history_gap(ctx, ACC)
    assert c is not None and c.status == "ok"               # a short burst while the fill runs
    _ctx(tmp_path, [(ts - pd.Timedelta(hours=3), a, act) for ts, a, act in _hourly(3)])   # 3 more hours: a new part
    later = health.check_history_gap(ctx, ACC)
    assert later is not None and later.status == "warn" and "6 closes" in later.reason
    assert later == _fresh(ctx)
    assert len(reads) == 3                                  # first pass, the changed journal, the fresh read


def test_refusals_leaving_the_window_stop_counting_without_a_new_journal_write(tmp_path: Path, reads: list[str]):
    ctx = _ctx(tmp_path, _hourly(6))
    assert (c := health.check_history_gap(ctx, ACC)) is not None and c.status == "warn"
    later = ctx.model_copy(update={"now": NOW + pd.Timedelta(hours=4)})
    got = health.check_history_gap(later, ACC)
    assert got is not None and got.status == "ok"           # 3 closes over 2 h left in the window
    assert reads == ["decisions"]                           # answered from the last read, filtered to the window
    assert got == _fresh(later)


def test_a_clock_that_went_back_reads_the_journal_again(tmp_path: Path, reads: list[str]):
    ctx = _ctx(tmp_path, _hourly(6))
    health.check_history_gap(ctx, ACC)
    earlier = ctx.model_copy(update={"now": NOW - pd.Timedelta(hours=1)})
    assert health.check_history_gap(earlier, ACC) == _fresh(earlier)
    assert reads[:2] == ["decisions", "decisions"]          # the last read does not cover the earlier window


def test_each_account_and_symbol_keeps_its_own_read(tmp_path: Path, reads: list[str]):
    ctx = _ctx(tmp_path, _hourly(6), symbol="XAUUSD")
    c = health.check_history_gap(ctx, ACC)                   # vantage gold is XAUUSD+: nothing refused there
    assert c is not None and c.status == "ok"
    _ctx(tmp_path, _hourly(6))                               # now under XAUUSD+
    c = health.check_history_gap(ctx, ACC)
    assert c is not None and c.status == "warn"


def test_the_window_holds_exactly_the_rows_the_bounded_store_read_returns(tmp_path: Path):
    rows = _hourly(8) + [(NOW + pd.Timedelta(minutes=5), "trend-g0-a", "flat")]   # one after the window's end
    ctx = _ctx(tmp_path, rows)
    assert ctx.settings is not None
    since, end = NOW - pd.Timedelta(hours=health.HISTORY_GAP_WINDOW_H), NOW + pd.Timedelta(seconds=1)
    want = Store(ctx.settings.data_root).read("decisions", source=ACC.account_id, symbol=ACC.symbol,
                                              start=since, end=end)[list(engine_checks.JOURNAL_COLUMNS)]
    for _ in range(2):                                     # the read, then the cached rows
        got = engine_checks._journal_window(ctx.settings.data_root, ACC, since, end)
        pd.testing.assert_frame_equal(got, want)
