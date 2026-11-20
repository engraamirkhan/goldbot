"""The nightly arm read proves a window was its own engine's (ADR 0091, 2026-11-20; the quant
conditions Q1-Q6 of the plan; research/arm_integrity.py "One writer per stored minute").

Design rule: a candidate with a boot id is read only when its window is proven: its start held the writer lock and no
later start began before the decision; no start without the lock began inside the window; every live minute of the
window is the account's own key's copy, written by a locked start (the deciding one or an earlier one) inside that
start's life. Then `sampled_minute` no longer applies and no other account's events void it; every other rule still
does. Anything else is void `unproven_writer` (fail closed), never a refusal. Legacy-key minutes are read only before
the account's first locked start, and the account's own copy of a minute wins.
Synthetic rows only; every store is a temp copy."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.data import crossfeed
from goldbot.data import quarantine as qr
from goldbot.data.account_bars import (
    LEGACY,
    OWN,
    account_bars_1m,
    engine_boots,
    live_cutover,
    minute_versions,
)
from goldbot.data.store import Store
from goldbot.data.timeutil import epoch_ns
from goldbot.engine import arm_registry as AR
from goldbot.engine import history
from goldbot.research import arm_integrity as I
from goldbot.research import arm_rules_spec as S
from tests.test_arm_integrity import _engine_window as _engine_window  # noqa: F401  (autouse)
from tests.test_arm_integrity import _held, _late_detail, _m1, _sorted, _versions
from tests.test_discovered_arm_registration import env
from tests.test_exploration_engine import CLOSE
from tests.test_exploration_engine import _rules as _rules  # noqa: F401  (autouse)

__all__ = ["env"]

A, B, P, U = "a" * 32, "b" * 32, "p" * 32, "u" * 32
REPO = Path(__file__).resolve().parents[1]


def _own(eng: Any, writer: str = A) -> pd.DataFrame:
    return _versions(eng.bars_1m, writer=writer, keyed=OWN)


def _start(eng: Any) -> pd.Timestamp:
    return pd.Timestamp(eng.bars_1m["ts_utc"].iloc[0]).tz_convert("UTC")


def _boots(*rows: tuple[pd.Timestamp, str, bool]) -> list[tuple[int, str, bool]]:
    return [(int(t.value), b, k) for t, b, k in rows]


# ---------------------------------------------------------------- Q1: the deciding start, locked, alone
def test_a_window_written_by_its_own_locked_start_is_read_and_sampled_minutes_no_longer_void_it(env, tmp_path):
    """Q3: the sampled void is dropped for a proven window only: its own late minute and another engine's late
    minute stand as the copy this start held."""
    eng, arm, engine = _held(env, tmp_path)
    boots = _boots((_start(eng) - pd.Timedelta(days=1), A, True))
    v = _sorted(_own(eng), _m1(eng.bars_1m))
    ns = int(pd.Timestamp(eng.bars_1m["ts_utc"].iloc[-60]).value)
    late: list[Any] = [(ns, ns, True)]
    other: list[Any] = [([(ns, ns, True)], [])]
    got = I.AccountMinutes(v, boots=boots, late=late, sharers=other).held(arm, CLOSE, A)
    assert isinstance(got, pd.DataFrame) and AR.decision_bracket(got, arm, CLOSE) == engine
    assert I.AccountMinutes(v, boots=boots, late=late, sharers=other).held(arm, CLOSE) == I.UNPROVEN_WRITER


def test_an_unlocked_start_alongside_a_locked_one_voids_the_decision(env, tmp_path):
    """Q1: a start without the lock that began inside the window (a second process the lock did not stop) voids
    the decision, whoever wrote the stored copies."""
    eng, arm, _ = _held(env, tmp_path)
    v = _sorted(_own(eng), _m1(eng.bars_1m))
    inside = pd.Timestamp(eng.bars_1m["ts_utc"].iloc[-200]).tz_convert("UTC")
    boots = _boots((_start(eng) - pd.Timedelta(days=1), A, True), (inside, U, False))
    assert I.AccountMinutes(v, boots=boots).held(arm, CLOSE, A) == I.UNPROVEN_WRITER
    assert I.UNPROVEN_WRITER not in I.REFUSAL_CAUSES and I.UNPROVEN_WRITER not in I.UNVERIFYING


def test_a_start_on_a_host_without_flock_never_proves_its_rows(env, tmp_path):
    """Q6: no flock (the Windows VPS) records the start unlocked; its decisions are void, never read."""
    eng, arm, _ = _held(env, tmp_path)
    v = _sorted(_own(eng), _m1(eng.bars_1m))
    boots = _boots((_start(eng) - pd.Timedelta(days=1), A, False))
    assert I.AccountMinutes(v, boots=boots).held(arm, CLOSE, A) == I.UNPROVEN_WRITER


def test_an_unknown_boot_id_or_a_minute_without_a_writer_voids(env, tmp_path):
    eng, arm, _ = _held(env, tmp_path)
    boots = _boots((_start(eng) - pd.Timedelta(days=1), A, True))
    v = _sorted(_own(eng), _m1(eng.bars_1m))
    assert I.AccountMinutes(v, boots=boots).held(arm, CLOSE, B) == I.UNPROVEN_WRITER
    plain = _sorted(_versions(eng.bars_1m, keyed=OWN), _m1(eng.bars_1m))            # no writer column at all
    assert I.AccountMinutes(plain, boots=boots).held(arm, CLOSE, A) == I.UNPROVEN_WRITER


def test_a_candidate_without_a_boot_id_decided_after_the_first_recorded_start_is_void(env, tmp_path):
    """Q4: such a decision came from an unknown start (an engine without the boot record); before the first start
    the rules before the boot record still read it."""
    eng, arm, engine = _held(env, tmp_path)
    v = _sorted(_versions(eng.bars_1m), _m1(eng.bars_1m))
    before = _boots((CLOSE - pd.Timedelta(minutes=5), A, True))
    assert I.AccountMinutes(v, boots=before).held(arm, CLOSE) == I.UNPROVEN_WRITER
    after = _boots((CLOSE + pd.Timedelta(hours=1), A, True))
    got = I.AccountMinutes(v, boots=after).held(arm, CLOSE)
    assert isinstance(got, pd.DataFrame) and AR.decision_bracket(got, arm, CLOSE) == engine


# ---------------------------------------------------------------- Q2: sequential starts, write times inside a life
def test_sequential_locked_starts_inside_one_window_are_proven(env, tmp_path):
    eng, arm, engine = _held(env, tmp_path)
    restart = pd.Timestamp(eng.bars_1m["ts_utc"].iloc[-300]).tz_convert("UTC")
    live = _own(eng)
    early = pd.to_datetime(live["written_at"], utc=True) < restart
    live.loc[early, "writer"] = P
    boots = _boots((_start(eng) - pd.Timedelta(days=1), P, True), (restart, A, True))
    got = I.AccountMinutes(_sorted(live, _m1(eng.bars_1m)), boots=boots).held(arm, CLOSE, A)
    assert isinstance(got, pd.DataFrame) and AR.decision_bracket(got, arm, CLOSE) == engine
    # a row of the earlier start written after the later one began: outside its life
    stale = live.copy()
    stale.loc[stale.index[-200], "writer"] = P
    assert I.AccountMinutes(_sorted(stale, _m1(eng.bars_1m)), boots=boots).held(arm, CLOSE, A) == I.UNPROVEN_WRITER


def test_a_minute_a_later_start_overwrote_after_the_decision_voids(env, tmp_path):
    """Q2: A decides at CLOSE; B restarts after it and rewrites the window's last minute within the live grace of
    its close (so the as-of read takes it): B's copy was never what A held."""
    eng, arm, _ = _held(env, tmp_path)
    live = _own(eng)
    i = live.index[-1]
    live.loc[i, "writer"], live.loc[i, "written_at"] = B, CLOSE + pd.Timedelta(seconds=30)
    boots = _boots((_start(eng) - pd.Timedelta(days=1), A, True), (CLOSE + pd.Timedelta(seconds=20), B, True))
    assert I.AccountMinutes(_sorted(live, _m1(eng.bars_1m)), boots=boots).held(arm, CLOSE, A) == I.UNPROVEN_WRITER
    # a later start that began before the decision: A was no longer the account's engine
    early = _boots((_start(eng) - pd.Timedelta(days=1), A, True), (CLOSE - pd.Timedelta(minutes=1), B, True))
    assert I.AccountMinutes(_sorted(_own(eng), _m1(eng.bars_1m)), boots=early).held(arm, CLOSE, A) == \
        I.UNPROVEN_WRITER


# ---------------------------------------------------------------- Q3: the write paths that change a minute later
def test_a_proven_window_still_voids_on_a_replacement_written_after_the_decision(env, tmp_path):
    """broker_bars._replace (after GIVE_UP or not): flagged, written after the decision: K1."""
    eng, arm, _ = _held(env, tmp_path)
    live = _own(eng)
    i = live.index[-3]
    live.loc[i, "replaced"], live.loc[i, "written_at"] = True, CLOSE + pd.Timedelta(seconds=30)
    boots = _boots((_start(eng) - pd.Timedelta(days=1), A, True))
    assert I.AccountMinutes(_sorted(live, _m1(eng.bars_1m)), boots=boots).held(arm, CLOSE, A) == I.REPLACED_MINUTE


def test_a_proven_window_still_voids_on_a_fill_or_rebase_written_after_the_decision(env, tmp_path):
    """history.absorb / _rebase: flagged filled, written after the decision (past the live grace): the as-of read
    lacks it, L1 voids."""
    eng, arm, _ = _held(env, tmp_path)
    for flag in (history.FILLED_COL, "rebase"):
        live = _own(eng)
        i = live.index[-60]
        live.loc[i, "written_at"] = CLOSE + pd.Timedelta(hours=2)
        live.loc[i, "visible_at"] = CLOSE + pd.Timedelta(hours=2)
        live[history.FILLED_COL] = False
        live.loc[i, history.FILLED_COL] = True
        boots = _boots((_start(eng) - pd.Timedelta(days=1), A, True))
        got = I.AccountMinutes(_sorted(live, _m1(eng.bars_1m)), boots=boots).held(arm, CLOSE, A)
        assert got in (I.GAP_AFTER_CUTOVER, I.LATE_MINUTE), flag


def test_a_corrected_row_a_script_wrote_without_a_writer_voids_a_proven_window(env, tmp_path):
    """scripts/quarantine_tz_ticks_2026_10_26.py writes corrected minutes with no writer: unproven."""
    eng, arm, _ = _held(env, tmp_path)
    live = _own(eng)
    live.loc[live.index[-60], "writer"] = None
    boots = _boots((_start(eng) - pd.Timedelta(days=1), A, True))
    assert I.AccountMinutes(_sorted(live, _m1(eng.bars_1m)), boots=boots).held(arm, CLOSE, A) == I.UNPROVEN_WRITER


# ---------------------------------------------------------------- Q4: the legacy key, its cut-over, the own copy
def _store_both(tmp_path: Path, eng: Any, cut: pd.Timestamp) -> Store:
    """Legacy key (icm) with every minute, the own key (icm-probe) from `cut` written by A, A's locked boot at cut."""
    store = Store(tmp_path / "data")
    minutes = eng.bars_1m.assign(available_utc=pd.to_datetime(eng.bars_1m["visible_at"], utc=True))
    store.append("bars_1m_live", minutes, source="icm", symbol="XAUUSD")
    own = minutes[pd.to_datetime(minutes["ts_utc"], utc=True) >= cut]
    store.append("bars_1m_live", own.assign(writer=A), source="icm-probe", symbol="XAUUSD")
    store.append(crossfeed.BROKER_TABLE, minutes, source="icm-probe", symbol="XAUUSD")
    ev = pd.DataFrame({"ts_utc": [cut], "check": [history.BOOT_CHECK], "severity": ["info"],
                       "detail": [json.dumps({"writer_lock": True})], "writer": [A]})
    store.append("dq_events", ev, source="icm-probe", symbol="XAUUSD", dedupe=False)
    return store


def test_legacy_minutes_are_read_only_before_the_first_locked_start_and_the_accounts_own_copy_wins(env, tmp_path):
    eng, _, _ = _held(env, tmp_path)
    cut = pd.Timestamp(eng.bars_1m["ts_utc"].iloc[-500]).tz_convert("UTC")
    store = _store_both(tmp_path, eng, cut)
    boots = engine_boots(store, "icm-probe")
    assert list(boots["writer"]) == [A] and live_cutover(boots) == cut
    both = pd.Timestamp(eng.bars_1m["ts_utc"].iloc[-100]).tz_convert("UTC")
    v = minute_versions(store, account_id="icm-probe", broker="icm", symbol="XAUUSD", start=None, end=None,
                        as_of=CLOSE + pd.Timedelta(days=1))
    live = v[v["rank"] == I.LIVE_RANK]
    assert (live.groupby("ts_utc").size() == 1).all()                  # one version per minute: own wins
    ts = pd.to_datetime(live["ts_utc"], utc=True)
    assert set(live.loc[ts >= cut, "keyed"]) == {OWN} and set(live.loc[ts < cut, "keyed"]) == {LEGACY}
    assert live.loc[ts == both, "writer"].tolist() == [A]
    # a legacy minute after the cut-over that the own key lacks is never read
    gone = pd.Timestamp(eng.bars_1m["ts_utc"].iloc[-50]).tz_convert("UTC")
    qr.move_rows(store, "bars_1m_live", source="icm-probe", symbol="XAUUSD", reason=qr.BACKDATED_CORRECTION,
                 select=lambda _, r: pd.to_datetime(r["ts_utc"], utc=True) == gone, now=CLOSE, apply=True)
    bars = account_bars_1m(store, account_id="icm-probe", broker="icm", symbol="XAUUSD", start=None, end=None,
                           as_of=CLOSE + pd.Timedelta(days=1))
    assert gone not in set(pd.to_datetime(bars["ts_utc"], utc=True))


def test_a_back_fill_into_the_own_key_never_moves_the_legacy_cut_over(env, tmp_path):
    """Q4: a fill written to the own key at an old minute leaves the cut-over at the first locked start."""
    eng, _, _ = _held(env, tmp_path)
    cut = pd.Timestamp(eng.bars_1m["ts_utc"].iloc[-500]).tz_convert("UTC")
    store = _store_both(tmp_path, eng, cut)
    old = eng.bars_1m.iloc[[10]].assign(available_utc=CLOSE, filled=True, writer=A)
    store.append("bars_1m_live", old, source="icm-probe", symbol="XAUUSD")
    v = minute_versions(store, account_id="icm-probe", broker="icm", symbol="XAUUSD", start=None, end=None,
                        as_of=CLOSE + pd.Timedelta(days=1))
    live = v[v["rank"] == I.LIVE_RANK]
    # every legacy minute before the first locked start is still read, but the one the own key now holds
    assert len(live) == len(eng.bars_1m) and (live["keyed"] == LEGACY).sum() == len(eng.bars_1m) - 500 - 1


def test_a_decision_whose_window_holds_legacy_minutes_is_void_and_one_wholly_after_the_cut_over_is_read(env,
                                                                                                         tmp_path):
    """Q4 end to end through the store: legacy rows are unproven; the same decision with the cut-over before its
    window is read whatever another engine on the old shared key kept sampled."""
    eng, arm, engine = _held(env, tmp_path)
    row = {"account_id": "icm-probe", "decision_utc": CLOSE, "boot_id": A}
    host, now = {"icm-probe": ("icm", "XAUUSD")}, CLOSE + pd.Timedelta(days=1)
    late = pd.DatetimeIndex([pd.Timestamp(eng.bars_1m["ts_utc"].iloc[-60])]).tz_convert("UTC")
    mid = _store_both(tmp_path / "mid", eng, pd.Timestamp(eng.bars_1m["ts_utc"].iloc[-500]).tz_convert("UTC"))
    assert I.stored_recompute(mid, host, now=now)(arm, row) == I.UNPROVEN_WRITER
    whole = _store_both(tmp_path / "whole", eng, _start(eng) - pd.Timedelta(hours=1))
    ev = pd.DataFrame({"ts_utc": late, "check": [I.BAR_LATE], "severity": ["warning"], "detail": [_late_detail(late)]})
    whole.append("dq_events", ev, source="icm-demo", symbol="XAUUSD", dedupe=False)     # the other icm engine
    got = I.stored_recompute(whole, host, now=now, sharers={"icm-probe": ("icm-demo",)})(arm, row)
    assert isinstance(got, S.Bracket) and got == engine
    assert I.stored_recompute(whole, host, now=now, sharers={"icm-probe": ("icm-demo",)})(
        arm, {**row, "boot_id": None}) == I.UNPROVEN_WRITER


def test_the_saved_cut_over_is_the_earlier_of_the_own_and_the_legacy_key(tmp_path):
    from goldbot.db import Database
    db = Database.open(str(tmp_path), "aux")
    with db.write() as conn:
        for src, ns in (("icm", 200), ("icm-probe", 100)):
            conn.execute("INSERT INTO history_cutover (account_id, source, symbol, cutover_ns, updated_ns) "
                         "VALUES (?, ?, ?, ?, 0)", ("icm-probe", src, "XAUUSD", ns))
    assert I.saved_cutover(db, "icm-probe", "icm", "XAUUSD") == 100


# ---------------------------------------------------------------- Q5: every reader of the live key
def test_a_minute_quarantined_from_the_accounts_own_live_key_is_read_from_no_feed(tmp_path):
    t0 = pd.Timestamp("2026-11-23 10:00", tz="UTC")
    ts = pd.date_range(t0, periods=5, freq="min")
    bars = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1),
                         **{f"{s}_{c}": 2000.0 + (0.1 if s == "ask" else 0.0) for s in ("bid", "ask")
                            for c in ("open", "high", "low", "close")}, "tick_count": 1, "spread_mean": 0.1,
                         "spread_max": 0.1})
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", bars.assign(writer=A), source="icm-demo", symbol="XAUUSD")
    store.append(crossfeed.BROKER_TABLE, bars, source="icm-demo", symbol="XAUUSD")
    qr.move_rows(store, "bars_1m_live", source="icm-demo", symbol="XAUUSD", reason=qr.BACKDATED_CORRECTION,
                 select=lambda _, r: pd.to_datetime(r["ts_utc"], utc=True) == ts[2], now=t0, apply=True)
    as_of = t0 + pd.Timedelta(hours=1)
    for got in (account_bars_1m(store, account_id="icm-demo", broker="icm", symbol="XAUUSD", start=None, end=None,
                                as_of=as_of),
                minute_versions(store, account_id="icm-demo", broker="icm", symbol="XAUUSD", start=None, end=None,
                                as_of=as_of, reference=None)):
        assert ts[2] not in set(pd.to_datetime(got["ts_utc"], utc=True)) and len(set(got["ts_utc"])) == 4
    assert epoch_ns(pd.DatetimeIndex([ts[2]]))[0] > 0


# every module that reads or writes bars_1m_live, each handled for the per-account key (ADR 0091)
LIVE_KEY_FILES = {
    "goldbot/api/trade_chart.py", "goldbot/config.py", "goldbot/data/account_bars.py", "goldbot/data/quarantine.py",
    "goldbot/data/store.py", "goldbot/engine/book_backfill.py", "goldbot/engine/runner.py", "goldbot/engine/books.py", "goldbot/engine/history.py",
    "goldbot/instruments.py", "goldbot/ops/jobs.py", "goldbot/ops/long_history.py", "goldbot/research/arm_integrity.py",
    "goldbot/research/har_abstention.py", "scripts/quarantine_tz_ticks_2026_10_26.py",
}


def test_every_reader_of_the_live_minutes_is_known_and_none_lists_them_by_source():
    """Q5: a reader listing bars_1m_live by source (a glob over source=, a read without a source) would count a
    minute twice, once per key; a new reader of the table must be checked against the per-account key and added here."""
    out = subprocess.run(["git", "grep", "-n", "-E", r"bars_1m_live|LIVE_TABLE", "--", "goldbot", "scripts"],
                         cwd=REPO, capture_output=True, text=True, check=True).stdout
    files = {line.split(":", 1)[0] for line in out.splitlines()}
    assert files <= LIVE_KEY_FILES, sorted(files - LIVE_KEY_FILES)
    by_source = re.compile(r"table_dir\((\"bars_1m_live\"|LIVE_TABLE)\)|read\((\"bars_1m_live\"|LIVE_TABLE)\s*[,)]"
                           r"(?![^\n]*source=)")
    hits = [line for line in out.splitlines() if by_source.search(line)]
    assert hits == [], hits


def test_the_long_history_and_the_trade_chart_read_the_accounts_own_live_key_over_the_brokers(monkeypatch):
    """Q5: the 1d history refresh reads the legacy key, then the account's own (whose minute wins); the trade chart
    reads the broker's key with the account's own over it."""

    from goldbot.api import trade_chart
    from goldbot.ops import long_history
    from tests.test_long_history_refresh import GOLD, SETTINGS
    assert long_history.gold_key(SETTINGS, [GOLD]) == (("icm", "XAUUSD"), ("icm-demo", "XAUUSD"))
    monkeypatch.setattr(trade_chart, "_account_terms", lambda account: ("icm", "XAUUSD"))
    live = [parts for parts, _ in trade_chart._sources("icm-demo") if parts[0][0] == "bars_1m_live"]
    assert live == [(("bars_1m_live", "icm", "XAUUSD"), ("bars_1m_live", "icm-demo", "XAUUSD"))]   # own key last: wins


def test_engine_starts_that_cannot_be_read_leave_every_window_unproven(env, tmp_path):
    """Data and quant review of 7b439c30: a dq_events read that fails never breaks the bar read; the proof fails
    closed, for a candidate with a boot id and one without alike."""
    eng, arm, _ = _held(env, tmp_path)
    v = _sorted(_own(eng), _m1(eng.bars_1m))
    boots = _boots((_start(eng) - pd.Timedelta(days=1), A, True))
    m = I.AccountMinutes(v, boots=boots, boots_unreadable=True)
    assert m.held(arm, CLOSE, A) == I.UNPROVEN_WRITER and m.held(arm, CLOSE) == I.UNPROVEN_WRITER
