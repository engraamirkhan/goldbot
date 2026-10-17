"""The safety net for moving the engine's state save/load out of runner.py into engine/store.py (TD-2, ADR 0023 step 1;
docs/proposals/2026-10-runner-seam-map.md, "Proving move-only", PR 0). Written against today's runner.py, with no
production change: the move PR must pass every test here unchanged.

1. The golden grid: 4 stages x kill_everything x a pending 12% read x halted_at x propose_only x 3 order-row sets
   (192 cases). Each case sets the persisted state, writes it (`_write_state`: engine, risk and orders files), restarts
   an engine on the same directory and broker, and compares canonical JSON with tests/data/engine_store_golden/: the
   three files as written (`ts` removed), the restarted engine's state attributes and the orders file its start
   rewrote. Stage has three members; the
   fourth "stage" is an older risk file without the `stage` key (saved size-down, the key removed before the restart),
   so the loader's default is pinned too.
2. Edge tests named in the seam map, each stating the rule it holds. Where a test already exists it is referenced
   (`REUSED`) rather than copied.

The golden is written only for an intended change of what the engine persists or how it restarts, never to make a
move pass: GOLDBOT_WRITE_STORE_GOLDEN=1 pytest tests/test_engine_store_golden.py -k grid -p no:randomly; the
trading-safety reviewer signs any change to it.
"""
from __future__ import annotations

import errno
import hashlib
import itertools
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot import base
from goldbot.engine import Engine, EngineConfig
from goldbot.engine.runner import OpenTrade, SentOrder
from goldbot.execution.broker import OrderIntent, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.risk.gate import Stage
from goldbot.telegram.approvals import ApprovalCenter
from goldbot.telegram.bus import ApprovalBus
from tests.test_kill_switch_everything import _book, _crash, _fail_positions_once_when_halted, _ids

GOLDEN = Path(__file__).parent / "data" / "engine_store_golden"
WRITE = os.environ.get("GOLDBOT_WRITE_STORE_GOLDEN") == "1"
OWNER = 111
ACCOUNT = "icm-demo"
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")        # a Wednesday: the paper broker's clock
OLD = pd.Timestamp("2020-01-06 09:00", tz="UTC")       # older than the 7-day window whatever day the suite runs
# inside the 7-day window whatever day the suite runs (`_save_orders` prunes against the host clock, which the test
# cannot pin without patching pandas)
FRESH = pd.Timestamp("2200-01-06 09:00", tz="UTC")
OWN_MAGIC = 260150

# the fourth entry is an older risk file without `stage`: saved size-down, the key removed before the restart
STAGES: dict[str, Stage] = {"normal": Stage.NORMAL, "size_down": Stage.SIZE_DOWN, "halted": Stage.HALTED,
                            "stage_key_absent": Stage.SIZE_DOWN}
ROW_SETS = ("none", "old", "fresh")


def _tick(t: pd.Timestamp, bid: float = 2400.0) -> Tick:
    return Tick(ts_utc=t, bid=bid, ask=bid + 0.2)


def _broker() -> PaperBroker:
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(T0))
    return pb


def _engine(state_dir: Path, pb: PaperBroker, bus: ApprovalBus | None = None) -> Engine:
    return Engine(EngineConfig(account_id=ACCOUNT, broker_name="icm", state_dir=str(state_dir), owner_user_id=OWNER),
                  pb, [], {}, ApprovalCenter({OWNER}, bus=bus))


def _row(cid: str, ts: pd.Timestamp, status: str, position_id: int | None = None) -> SentOrder:
    return SentOrder(client_order_id=cid, ts_utc=ts, agent_id="session_open-g0-x", side=1, magic=OWN_MAGIC, lots=0.1,
                     max_bars=8, sl=2396.0, tp=2406.0, status=status, position_id=position_id, atr_usd=4.0)


@pytest.fixture
def fresh_skew(monkeypatch: pytest.MonkeyPatch) -> None:
    """The schema-skew counter is process-wide and published in the engine file: each case starts from an empty one,
    so another test's skew never reaches the golden."""
    from collections import Counter
    monkeypatch.setattr(base, "_skew", Counter())
    monkeypatch.setattr(base, "_refused", set())
    monkeypatch.setattr(base, "_last_refused", None)


# ---------------------------------------------------------------------------------------------- 1. the golden grid
def _set_rows(eng: Engine, pb: PaperBroker, rows: str) -> None:
    """The three order-row sets. old: a `sending` row (never pruned, found unfilled on restart), a filled and a
    rejected row past the 7-day window (pruned at the save), a lost record. fresh: a filled row whose position is
    open at the broker and in the open-trade table, an unfilled row and a `sending` row, all inside the window."""
    if rows == "old":
        eng._orders = {"old-sending": _row("old-sending", OLD, "sending"),
                       "old-filled": _row("old-filled", OLD, "filled", position_id=1),
                       "old-rejected": _row("old-rejected", OLD, "rejected")}
        eng._records_lost = [7]
    elif rows == "fresh":
        r = pb.place_order(OrderIntent(client_order_id="fresh-filled", symbol="XAUUSD", side=1, lots=0.1, sl=2396.0,
                                       tp=2406.0, magic=OWN_MAGIC, comment="fresh-filled"))
        assert r.ok and r.position_id is not None
        eng._orders = {"fresh-filled": _row("fresh-filled", FRESH, "filled", position_id=r.position_id),
                       "fresh-unfilled": _row("fresh-unfilled", FRESH, "unfilled"),
                       "fresh-sending": _row("fresh-sending", FRESH, "sending")}
        eng.open = {r.position_id: OpenTrade(position_id=r.position_id, agent_id="session_open-g0-x", side=1, lots=0.1,
                                             entry_bar_ts=T0, max_bars=8, bars_held=2, sl=2396.0, tp=2406.0,
                                             client_order_id="fresh-filled", open_price=r.price, initial_sl=2396.0,
                                             initial_lots=0.1, opened_utc=T0)}
    eng.sent_ids = set(eng._orders)


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=1, default=str)


def _no_ts(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k != "ts"}


def _iso(t: pd.Timestamp | None) -> str | None:
    return None if t is None else pd.Timestamp(t).isoformat()


def _restart_dump(eng: Engine) -> dict[str, Any]:
    """Every attribute the store's load sets or the constructor derives from it (seam map: "Stays in runner.py")."""
    st = eng.state
    return {"stage": Stage(st.stage).value, "day_start_equity": st.day_start_equity,
            "week_start_equity": st.week_start_equity, "balance_closed_hwm": st.balance_closed_hwm,
            "last_reset": _iso(eng._last_reset), "rearm_seen": eng._rearm_seen, "weekend_done": eng._weekend_done,
            "halted_at": _iso(eng._halted_at), "propose_only_until": _iso(eng._propose_only_until),
            "rearm_refused": eng._rearm_refused, "stage2_pending_since": _iso(eng._stage2_pending_since),
            "stage2_clean_since": _iso(eng._stage2_clean_since), "kill_everything": eng._kill_everything,
            "kill_pending": eng._kill_pending, "mode_reset_due": eng._mode_reset_due,
            "approval_mode": eng.cfg.approval_mode, "sent_ids": sorted(eng.sent_ids),
            "orders": {k: o.model_dump(mode="json") for k, o in sorted(eng._orders.items())},
            "open": {str(k): t.model_dump(mode="json") for k, t in sorted(eng.open.items())},
            "closing": [c.model_dump(mode="json") for c in eng._closes], "records_lost": eng._records_lost,
            "decisions": [_no_ts(d) for d in eng.decisions]}


def _case(state_dir: Path, stage: str, kill: bool, pending: bool, halted: bool, propose: bool,
          rows: str) -> dict[str, Any]:
    """Save one combination through `_write_state`, restart on the same directory and broker, and return the three
    files as written (`ts` removed from the engine file), the restarted engine's dump and the orders file as the
    restart rewrote it (reconciled `sending` rows)."""
    pb = _broker()
    eng = _engine(state_dir, pb)
    st = eng.state
    st.stage = STAGES[stage]
    st.day_start_equity, st.week_start_equity, st.balance_closed_hwm = 9_900.0, 10_100.0, 10_500.0
    eng._last_reset, eng._rearm_seen, eng._weekend_done = T0 - pd.Timedelta(hours=9), "rearm-1", "2025-02-28"
    eng._rearm_refused = "re-arm needs 10 trading days of positive paper shadow" if stage == "halted" else None
    eng._kill_everything = kill
    eng._stage2_pending_since = T0 - pd.Timedelta(minutes=5) if pending else None
    eng._stage2_clean_since = T0 - pd.Timedelta(minutes=1)        # in memory only: a restart restarts the recovery
    eng._halted_at = T0 - pd.Timedelta(days=1) if halted else None
    eng._propose_only_until = T0 + pd.Timedelta(days=30) if propose else None
    _set_rows(eng, pb, rows)
    eng._write_state()
    files = {kind: json.loads((state_dir / f"{kind}_{ACCOUNT}.json").read_text()) for kind in ("orders", "risk", "engine")}
    if stage == "stage_key_absent":
        risk = state_dir / f"risk_{ACCOUNT}.json"
        risk.write_text(json.dumps({k: v for k, v in files["risk"].items() if k != "stage"}))
    again = _engine(state_dir, pb)
    after = json.loads((state_dir / f"orders_{ACCOUNT}.json").read_text())
    return {"orders": files["orders"], "risk": files["risk"], "engine": _no_ts(files["engine"]),
            "restart": _restart_dump(again), "orders_after": after}


def _cases() -> list[tuple[str, tuple[str, bool, bool, bool, bool, str]]]:
    out = []
    for stage, kill, pending, halted, propose, rows in itertools.product(STAGES, (False, True), (False, True),
                                                                         (False, True), (False, True), ROW_SETS):
        cid = f"{stage}-kill{int(kill)}-pending{int(pending)}-halted{int(halted)}-propose{int(propose)}-{rows}"
        out.append((cid, (stage, kill, pending, halted, propose, rows)))
    return out


def _key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def test_save_load_round_trip_matches_golden_grid(tmp_path, fresh_skew):
    """Design (Stack: "SQLite for live state"; TRACEABILITY X7, A11): the engine's saved state and its restart are
    the contract the store move keeps word for word. Each kind is stored once per distinct content (the cases name
    the content by key), so a diff names the case and the file that changed."""
    kinds = ("orders", "risk", "engine", "restart", "orders_after")
    got_cases: dict[str, dict[str, str]] = {}
    got_blobs: dict[str, dict[str, Any]] = {k: {} for k in kinds}
    for cid, args in _cases():
        out = _case(tmp_path / cid, *args)
        got_cases[cid] = {}
        for kind in kinds:
            text = _canon(out[kind])
            key = _key(text)
            got_cases[cid][kind] = key
            got_blobs[kind][key] = json.loads(text)
    assert len(got_cases) == 192
    blob = json.dumps(got_blobs)
    assert str(tmp_path) not in blob and str(Path.home()) not in blob   # E5: machine-independent
    if WRITE:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        (GOLDEN / "cases.json").write_text(_canon(got_cases) + "\n")
        for kind in kinds:
            (GOLDEN / f"{kind}.json").write_text(_canon(got_blobs[kind]) + "\n")
    assert (GOLDEN / "cases.json").exists(), f"{GOLDEN} missing: GOLDBOT_WRITE_STORE_GOLDEN=1 pytest {Path(__file__).name}"
    want_cases = json.loads((GOLDEN / "cases.json").read_text())
    want_blobs = {kind: json.loads((GOLDEN / f"{kind}.json").read_text()) for kind in kinds}
    assert sorted(want_cases) == sorted(got_cases)
    for cid in got_cases:
        for kind in kinds:
            want = want_blobs[kind][want_cases[cid][kind]]
            got = got_blobs[kind][got_cases[cid][kind]]
            assert got == want, f"{cid}: {kind} differs from the golden"


# ---------------------------------------------------------------------------------------------- 2. edge tests
# Already held elsewhere, and part of this safety net (REUSED, not copied):
#   newer keys in the orders file: tests/test_engine_safety.py
#     ::test_a_restart_after_a_rollback_loads_an_orders_file_a_newer_release_wrote (row fields)
#     ::test_a_restart_warns_about_an_orders_file_table_this_release_does_not_know (a top-level table)
#   a saved close-everything after a restart, through the account refresh: tests/test_kill_switch_everything.py
#     ::test_after_a_restart_a_saved_close_everything_still_closes_the_whole_account
#     ::test_a_failed_mode_write_on_a_flat_trip_is_retried_after_a_restart (the owed mode reset, A10)
#   a full disk on the trip (patches runner.write_atomic, so the move edits that import site): tests/
#     test_equity_confirmation.py::test_a_failed_save_on_the_halt_still_closes_the_whole_account_on_that_read
#   the first-start re-arm end to end: tests/test_risk_ops.py::test_first_start_ignores_an_old_rearm
def _risk_file(state_dir: Path) -> Path:
    return state_dir / f"risk_{ACCOUNT}.json"


def _orders_file(state_dir: Path) -> Path:
    return state_dir / f"orders_{ACCOUNT}.json"


def test_an_older_risk_file_without_kill_everything_restarts_closing_this_engines_book_only(tmp_path):
    """F1 (`_load_risk_state`): "absent (an older file) = this engine's book only, as that release did"; ADR 0016: no
    `stage2_pending_since` = nothing pending. The halt itself survives, and the halted engine still owes the mode
    reset (A10)."""
    _risk_file(tmp_path).write_text(json.dumps({
        "last_reset": T0.isoformat(), "day_start_equity": 9_900.0, "week_start_equity": 10_100.0,
        "balance_closed_hwm": 10_500.0, "stage": "halted", "rearm_seen": "rearm-1", "weekend_done": "2025-02-28"}))
    eng = _engine(tmp_path, _broker())
    assert eng.state.stage == Stage.HALTED and eng.state.balance_closed_hwm == 10_500.0
    assert not eng._kill_everything and not eng._kill_pending and eng._mode_reset_due
    assert eng._stage2_pending_since is None and eng._halted_at is None and eng._propose_only_until is None


@pytest.mark.parametrize(("kind", "text"), [
    ("risk", '{"stage": "halted", "balance_closed_hwm": 10'),                      # cut short by a crash
    ("risk", '{"stage": "not_a_stage"}'),                                          # a value this release cannot read
    ("orders", "\x00\x00\x00"),                                                    # garbage
    ("orders", '{"sent": {"a": {"client_order_id": "a", "lots": "many"}}}'),       # a row that does not validate
])
def test_a_corrupt_risk_or_orders_file_refuses_the_start(tmp_path, kind, text):
    """"unreadable -> fail loudly rather than forget a drawdown halt" / "... rather than forget what was sent": the
    engine never starts on a file it cannot read, so it can neither trade out of a halt nor re-send an order."""
    (tmp_path / f"{kind}_{ACCOUNT}.json").write_text(text)
    with pytest.raises(ValueError):
        _engine(tmp_path, _broker())


def test_absent_files_take_the_first_start_rearm_from_the_bus_and_write_nothing(tmp_path):
    """First start (`_load_risk_state`): "an earlier re-arm on the bus is not a re-arm of this engine", so the bus's
    current re-arm id is taken as seen; with no orders file there is nothing to reconcile and no file is written."""
    bus = ApprovalBus(tmp_path / "bus")
    rearm = bus.owner_rearm(by="dashboard").rearm_id
    assert rearm is not None
    eng = _engine(tmp_path, _broker(), bus)
    assert eng._rearm_seen == rearm and eng.state.stage == Stage.NORMAL and not eng._kill_everything
    assert eng._orders == {} and eng.sent_ids == set() and eng.open == {}
    assert not _risk_file(tmp_path).exists() and not _orders_file(tmp_path).exists()
    again = _engine(tmp_path / "other", _broker())                                 # no bus: nothing seen
    assert again._rearm_seen is None


def test_a_saved_risk_file_keeps_its_own_rearm_id_over_the_bus(tmp_path):
    """The first-start rule applies only when the risk file is absent: a saved id is the engine's own, so a re-arm
    issued while it was down is still answered after the restart."""
    _risk_file(tmp_path).write_text(json.dumps({"stage": "halted", "rearm_seen": "rearm-old"}))
    bus = ApprovalBus(tmp_path / "bus")
    bus.owner_rearm(by="dashboard")
    eng = _engine(tmp_path, _broker(), bus)
    assert eng._rearm_seen == "rearm-old"


def test_a_newer_key_in_an_orders_row_is_dropped_and_noted_as_schema_skew(tmp_path, fresh_skew):
    """ADR 0010 (rollback = restart): a field a newer release added to a sent order is ignored and counted, so health
    reports the skew; the row itself loads."""
    row = {**_row("a", OLD, "rejected").model_dump(mode="json"), "future_field": 1}
    _orders_file(tmp_path).write_text(json.dumps({"sent": {"a": row}}))
    eng = _engine(tmp_path, _broker())
    assert "a" in eng.sent_ids
    assert base.schema_skew()[("orders_<account>.json", "SentOrder", ("future_field",))] == 1


def test_a_newer_key_in_the_risk_file_is_ignored_and_the_file_still_loads(tmp_path, fresh_skew):
    """Pinned so the move keeps it: the risk file's keys are read with `get` and any other is ignored, so a file a
    newer release wrote still starts the engine in its stage. Whether the key is noted as schema skew is the store's
    contract (BACKLOG 74, tests/test_store.py), not this edge's."""
    _risk_file(tmp_path).write_text(json.dumps({"stage": "size_down", "future_cap": 0.5}))
    eng = _engine(tmp_path, _broker())
    assert eng.state.stage == Stage.SIZE_DOWN


def test_sending_rows_are_never_pruned_and_settled_rows_go_after_seven_days(tmp_path):
    """pending_orders: "every id ever sent (pruned after a week)"; a `sending` row is kept whatever its age, since its
    fate is unknown until reconciliation, and a pruned id is gone from the file too."""
    eng = _engine(tmp_path, _broker())
    now = pd.Timestamp.now("UTC")
    old, recent = now - pd.Timedelta(days=8), now - pd.Timedelta(days=6)
    eng._orders = {"old-sending": _row("old-sending", old, "sending"),
                   "old-filled": _row("old-filled", old, "filled", position_id=1),
                   "old-unfilled": _row("old-unfilled", old, "unfilled"),
                   "old-rejected": _row("old-rejected", old, "rejected"),
                   "recent-filled": _row("recent-filled", recent, "filled", position_id=2)}
    eng._save_orders()
    assert set(eng._orders) == {"old-sending", "recent-filled"}
    assert set(json.loads(_orders_file(tmp_path).read_text())["sent"]) == {"old-sending", "recent-filled"}


@pytest.mark.parametrize("kill", [True, False])
def test_a_saved_kill_everything_restarts_pending_and_owes_the_mode_reset(tmp_path, kill):
    """F1: "a saved close-everything (`_load_risk_state`) retries at once"; A10: the halted engine owes the owner's
    mode reset after a restart. Without the saved flag the halt still owes the reset but closes only its own book."""
    eng = _engine(tmp_path, _broker())
    eng.state.stage, eng._kill_everything = Stage.HALTED, kill
    eng._save_risk_state()
    again = _engine(tmp_path, _broker())
    assert again._kill_everything is kill and again._kill_pending is kill and again._mode_reset_due


def test_a_full_disk_during_the_flatten_never_narrows_it(tmp_path, monkeypatch):
    """Trading-safety F1 and BACKLOG 44 MEDIUM-1: with every state-file rename failing (ENOSPC, below `write_atomic`,
    so this holds wherever the save lives) and the tripping read's positions() timing out, the 12% trip still marks
    the whole account, never raises, and the next refresh closes every position, the other magic's included."""
    eng, pb, other = _book(tmp_path)
    real_replace = os.replace

    def full(src: Any, dst: Any, *a: Any, **k: Any) -> None:
        if Path(dst).parent == tmp_path:
            raise OSError(errno.ENOSPC, "No space left on device")
        real_replace(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", full)
    _fail_positions_once_when_halted(monkeypatch, eng, pb)
    eng._refresh_account(_crash(pb))                                    # never raises
    assert eng.state.stage == Stage.HALTED and eng._kill_everything and eng._kill_pending and eng.state.tick_error
    assert other in _ids(pb)
    eng._refresh_account(_crash(pb, 2))
    assert other not in _ids(pb) and pb.positions() == []
    assert json.loads(_risk_file(tmp_path).read_text())["stage"] == "normal"     # the disk really was full throughout
