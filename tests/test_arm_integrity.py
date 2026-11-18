"""A discovered learning arm's bracket integrity at night, fail closed (goldbot/research/arm_integrity.py; the four
reviews of 611d5adb, 2026-11-18; docs/research/prereg-amendment-2026-11-18-discovered-arms.md 4, 5.1 (5), 2.2).

The night may RETIRE on what it sees, but it may only call a read clean (evidence) when it can show it saw everything:
the engines' whole journal over every candidate day (F1, F2), from an engine that can write the void actions (F3),
the minutes of every account the arm ran on (F4), each failure isolated to its candidate or arm (F5, F6), and the
owner told when an arm stays unverified (F7). The recompute reads only what the engine held (P1-P3), once per
candidate (P4); every refusal carries both brackets and its cause (C2); a retirement is never undone (C3).
Synthetic rows only; every store, state dir and registry is a temp copy."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.store import Store
from goldbot.db import Database
from goldbot.engine import arm_registry as AR
from goldbot.engine import broker_bars, exploration, history
from goldbot.research import arm_integrity as I
from goldbot.research import arm_lifecycle as L
from goldbot.research import arm_rules_spec as S
from tests.test_arm_lifecycle import (
    DAY,
    GOOD,
    PASS_1H,
    SETTLED,
    _arm_rows,
    _covered,
    _disc,
    _disc_obs,
    _dread,
    _journal,
    _obs,
)
from tests.test_discovered_arm_registration import _engine_with, _ex, env, register, registry_rows
from tests.test_exploration_engine import CLOSE, _at
from tests.test_exploration_engine import (
    _rules as _rules,  # noqa: F401  (autouse: the test arms' fixed rules)
)

__all__ = ["env"]                       # the registration fixture, shared with test_discovered_arm_registration


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database.open(str(tmp_path), "aux")


def _clean(obs: pd.DataFrame | None = None) -> pd.DataFrame:
    return _disc_obs(_obs(0.0, seed=3, arm_id="D1")) if obs is None else obs


def _decisions(rows: list[dict[str, Any]], account: str = "icm-probe") -> pd.DataFrame:
    return pd.DataFrame([{"ts_utc": pd.Timestamp(r["ts"]), "account_id": account,
                          "agent_id": r.get("agent", "explore:D1"), "action": r["action"],
                          "detail": r.get("detail", "{}")} for r in rows])


# ---------------------------------------------------------------- F1 / F2 / F3: the journal's coverage
def test_a_journal_without_its_schema_columns_reads_as_unusable_and_holds_the_arm_as_unverified(db):
    bad = pd.DataFrame({"ts_utc": [pd.Timestamp("2026-11-10", tz="UTC")], "action": ["x"]})     # no account_id
    host = I.host_read("icm-probe", bad)
    assert host.journal_why is not None and "schema" in host.journal_why
    assert I.host_read("icm-probe", None).journal_why is not None
    obs = _clean()
    r = _dread(db, obs, hosts={"icm-probe": host})
    assert r.decision == "HOLD" and r.unverified and not r.validated and "journal" in r.reason


def test_an_account_with_a_candidate_day_without_any_journal_row_holds_the_arm_as_unverified(db):
    obs = _clean()
    days = sorted(set(obs["session_day"].astype(str)))
    host = _covered(obs)["icm-probe"].model_copy(update={"days": tuple(d for d in days if d != days[5])})
    r = _dread(db, obs, hosts={"icm-probe": host})
    assert r.decision == "HOLD" and r.unverified and days[5] in r.reason


def test_an_account_whose_journal_ends_before_its_last_candidate_holds_the_arm_as_unverified(db):
    obs = _clean()
    last = int(pd.Timestamp(obs.loc[obs["arm_id"] == "D1", "decision_utc"].max()).value)
    host = _covered(obs)["icm-probe"].model_copy(update={"last_ns": last - 1})
    r = _dread(db, obs, hosts={"icm-probe": host})
    assert r.decision == "HOLD" and r.unverified and "ends before" in r.reason


def test_coverage_is_judged_on_every_journal_row_before_the_void_actions_are_filtered():
    rows = [{"ts": "2026-11-10 14:00Z", "action": "exploration:sent"},
            {"ts": "2026-11-11 14:00Z", "action": I.CAPABLE_ACTION,
             "detail": json.dumps({"arm": "D1", "capability": I.CAPABILITY})}]
    host = I.host_read("icm-probe", _decisions(rows))
    assert host.journal_why is None and set(host.days) == {"2026-11-10", "2026-11-11"}
    assert host.last_ns == int(pd.Timestamp("2026-11-11 14:00Z").value)
    assert host.capable_ns == int(pd.Timestamp("2026-11-11 14:00Z").value)


def test_without_a_capability_marker_since_the_registration_the_arm_holds_as_unverified(db):
    obs = _clean()
    before = int(pd.Timestamp("2026-09-01", tz="UTC").value)                 # since is 2026-10-01
    for capable in (None, before):
        host = _covered(obs)["icm-probe"].model_copy(update={"capable_ns": capable})
        r = _dread(db, obs, hosts={"icm-probe": host})
        assert r.decision == "HOLD" and r.unverified and "capability" in r.reason
    old = _decisions([{"ts": "2026-11-11 14:00Z", "action": I.CAPABLE_ACTION,
                       "detail": json.dumps({"capability": I.CAPABILITY - 1})}])
    assert I.host_read("icm-probe", old).capable_ns is None                 # an older contract proves nothing


def test_the_engine_journals_its_capability_when_it_loads_a_discovered_learning_arm(env, tmp_path):
    eng, pb, p = _engine_with(env, tmp_path)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    marks = [d for d in eng.decisions if d.get("action") == AR.CAPABLE]
    assert [m["arm"] for m in marks] == [p.reg.arm_id]                      # the arm, not its replay-only control
    assert marks[0]["capability"] == AR.CAPABILITY == I.CAPABILITY
    assert set(marks[0]["actions"]) == {AR.BRACKET_VOID, AR.CONTROL_CHANGED}
    assert (AR.CAPABLE, AR.BRACKET_VOID, AR.CONTROL_CHANGED) == \
        (I.CAPABLE_ACTION, I.BRACKET_VOID_ACTION, I.CONTROL_CHANGED_ACTION)


# ---------------------------------------------------------------- F4 / F5 / F6: isolation, never a silent drop
def test_an_account_with_unreadable_minutes_holds_the_arm_as_unverified_never_dropped_silently(db):
    obs = _clean()
    r = _dread(db, obs, recompute=lambda arm, row: I.MINUTES_UNREADABLE)
    assert r.decision == "HOLD" and r.unverified and I.MINUTES_UNREADABLE in r.reason
    assert r.void_by_cause.get(I.MINUTES_UNREADABLE, 0) > 0


def test_a_recompute_that_raises_voids_only_its_candidate(db):
    obs = _clean()
    bad = obs.loc[_arm_rows(obs, taken=False)[0], "decision_utc"]

    def recompute(arm: Any, row: Any) -> Any:
        if pd.Timestamp(row["decision_utc"]) == bad:
            raise ValueError("boom")
        return GOOD
    r = _dread(db, obs, recompute=recompute)
    assert r.void_by_cause == {"error:ValueError": 1} and r.n_cands == 119 and not r.unverified
    assert r.n_bracket_void == 0 and r.decision == "HOLD"


def test_a_read_that_raises_holds_only_that_arm_unverified_and_the_other_arms_are_still_decided(db, monkeypatch):
    obs = _clean()
    monkeypatch.setattr(L, "read_obs", lambda *a, **k: obs)
    real = L.read_arm

    def read_arm(db: Any, obs: Any, arm: Any, **kw: Any) -> Any:
        if arm.id == "D1":
            raise KeyError("bad row")
        return real(db, obs, arm, **kw)
    monkeypatch.setattr(L, "read_arm", read_arm)
    other = _disc().model_copy(update={"id": "D2"})
    reads = {r.arm_id: r for r in L.run(db, hosts={"icm-probe": "icm"}, arms=[_disc(), other], controls={},
                                         caps={"D2": 0.0}, day=DAY, now=SETTLED, parity=PASS_1H,
                                         journal=_journal(), recompute=lambda arm, row: GOOD, hosts_read=_covered(obs))}
    assert reads["D1"].decision == "HOLD" and reads["D1"].unverified and "KeyError" in reads["D1"].reason
    assert "D2" in reads                                                     # the job went on


def test_a_void_row_whose_detail_does_not_parse_counts_as_on_a_taken_candidate(db):
    obs = _clean()
    bad = pd.DataFrame([{"ts_utc": pd.Timestamp("2026-11-10", tz="UTC"), "account_id": "icm-probe",
                         "agent_id": "explore:D1", "action": AR.BRACKET_VOID, "detail": "{not json"}])
    got = I.integrity(None, obs, _disc(), broker="icm", journal=bad, recompute=lambda arm, row: GOOD,
                      accounts={"icm-probe"}, since=pd.Timestamp("2026-10-01", tz="UTC"))
    assert got.on_taken and got.engine_void and got.n_refused == 1


# ---------------------------------------------------------------- C2: both brackets and the cause
def test_a_mismatch_refusal_journals_the_engines_and_the_nights_bracket_and_the_summary_counts_by_cause(db):
    obs = _clean()
    i = _arm_rows(obs, taken=False)[0]
    obs.loc[i, "stop_dist"] = GOOD.stop_dist + 1e-6
    r = _dread(db, obs)
    [ref] = r.refusals
    assert ref["engine"]["stop_dist"] == GOOD.stop_dist + 1e-6 and ref["night"] == GOOD.model_dump()
    assert ref["cause"] == "bracket_mismatch" and r.refused_by_cause == {"bracket_mismatch": 1}
    out = L.summary([r])
    assert out["bracket_refused_by_cause"] == {"bracket_mismatch": 1} and out["unverified"] == 0


# ---------------------------------------------------------------- P4: one verdict per candidate, append-only
def test_a_candidates_verdict_is_cached_and_never_recomputed_on_a_later_night(db):
    obs = _clean()
    calls: list[int] = []

    def recompute(arm: Any, row: Any) -> Any:
        calls.append(1)
        return GOOD
    first = _dread(db, obs, recompute=recompute)
    n = len(calls)
    assert n == 120 and first.n_cands == 120                                 # the arm's 120 candidates, once
    again = _dread(db, obs, recompute=recompute)
    assert len(calls) == n and again.n_cands == first.n_cands


def test_a_void_candidate_is_not_cached_and_is_tried_again(db):
    obs = _clean()
    _dread(db, obs, recompute=lambda arm, row: None)
    calls: list[int] = []

    def recompute(arm: Any, row: Any) -> Any:
        calls.append(1)
        return GOOD
    _dread(db, obs, recompute=recompute)
    assert len(calls) == 120


def test_a_bracket_changed_after_its_cached_verdict_is_refused(db):
    obs = _clean()
    _dread(db, obs)
    i = _arm_rows(obs, taken=True)[0]
    obs.loc[i, "target_dist"] = GOOD.target_dist + 1.0                      # an UPDATE after the night verified it
    r = _dread(db, obs, recompute=lambda arm, row: pytest.fail("a cached candidate is never recomputed"))
    assert r.decision == "RETIRE" and r.refused_by_cause == {"bracket_updated": 1}


def test_the_verdict_cache_is_append_only(db):
    _dread(db, _clean())
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM arm_lifecycle_verdicts").fetchone()[0] == 120
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with db.write() as conn:
            conn.execute("UPDATE arm_lifecycle_verdicts SET verdict = 'ok'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with db.write() as conn:
            conn.execute("DELETE FROM arm_lifecycle_verdicts")


# ---------------------------------------------------------------- P1-P3, C2 quarantine: only what the engine held
def _versions(minutes: pd.DataFrame, rank: int = 0, **cols: Any) -> pd.DataFrame:
    v = minutes.copy()
    v["ts_utc"] = pd.to_datetime(v["ts_utc"], utc=True)
    v["visible_at"] = v["ts_utc"] + pd.Timedelta(minutes=1)
    v["written"], v["rank"], v["written_at"], v["replaced"] = True, rank, v["visible_at"], False
    return v.assign(**cols)


def _held(env: dict[str, Any], tmp_path: Path) -> tuple[Any, Any, S.Bracket]:
    eng, pb, p = _engine_with(env, tmp_path)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    arm = _ex(eng).arms[p.reg.arm_id]
    engine = AR.decision_bracket(eng.bars_1m, arm.spec, CLOSE)
    assert engine is not None
    return eng, arm.spec, engine


def test_a_1d_or_1w_window_cannot_be_proven_from_the_store_and_is_void_never_a_refusal(env, tmp_path):
    eng, arm, _ = _held(env, tmp_path)
    m = I.AccountMinutes(_versions(eng.bars_1m))
    for tf in ("1d", "1w"):
        assert m.held(arm.model_copy(update={"timeframe": tf}), CLOSE) == I.WINDOW_UNPROVABLE
    assert I.WINDOW_UNPROVABLE not in I.REFUSAL_CAUSES


def test_release_minutes_after_the_cutover_in_the_window_void_the_candidate(env, tmp_path):
    eng, arm, engine = _held(env, tmp_path)
    live = _versions(eng.bars_1m)
    cut = live.index[len(live) // 2]
    gap = live.loc[[cut + 10]].assign(rank=3)                              # a release minute filling a live gap
    v = pd.concat([live.drop(index=[cut + 10]), gap]).sort_values(["ts_utc", "rank"]).reset_index(drop=True)
    assert I.AccountMinutes(v).held(arm, CLOSE) == I.RELEASE_AFTER_CUTOVER
    before = live.loc[: cut - 1].assign(rank=3)                            # release only before the first live minute
    v2 = pd.concat([before, live.loc[cut:]]).sort_values(["ts_utc", "rank"]).reset_index(drop=True)
    held = I.AccountMinutes(v2).held(arm, CLOSE)
    assert isinstance(held, pd.DataFrame) and AR.decision_bracket(held, arm, CLOSE) == engine


def test_the_brokers_m1_never_fills_a_minute_the_engine_never_held(env, tmp_path):
    eng, arm, _ = _held(env, tmp_path)
    live = _versions(eng.bars_1m)
    gone = live.index[-5]
    m1 = live.loc[[gone]].assign(rank=1, bid_high=live.loc[gone, "bid_high"] + 30.0)
    v = pd.concat([live.drop(index=[gone]), m1]).sort_values(["ts_utc", "rank"]).reset_index(drop=True)
    held = I.AccountMinutes(v).held(arm, CLOSE)
    assert isinstance(held, pd.DataFrame) and pd.Timestamp(live.loc[gone, "ts_utc"]) not in set(held["ts_utc"])


def test_a_minute_flagged_replaced_and_written_after_the_decision_voids_the_candidate_without_a_dq_event(env,
                                                                                                         tmp_path):
    """P3: the replacement is persisted with the minute itself, so a crash before the bar close's dq flush cannot
    lose it."""
    eng, arm, engine = _held(env, tmp_path)
    live = _versions(eng.bars_1m)
    i = live.index[-3]
    late = live.copy()
    late.loc[i, "replaced"], late.loc[i, "written_at"] = True, CLOSE + pd.Timedelta(seconds=30)
    assert I.AccountMinutes(late).held(arm, CLOSE) == I.REPLACED_MINUTE
    early = live.copy()
    early.loc[i, "replaced"], early.loc[i, "written_at"] = True, CLOSE - pd.Timedelta(seconds=30)
    held = I.AccountMinutes(early).held(arm, CLOSE)
    assert isinstance(held, pd.DataFrame) and AR.decision_bracket(held, arm, CLOSE) == engine


def test_the_engine_stores_a_replaced_minute_with_its_flag(tmp_path):
    store = Store(tmp_path / "data")
    w = history.BarWriter(store, "icm", "XAUUSD")
    t = pd.Timestamp("2026-11-10 10:00", tz="UTC")
    row = {"ts_utc": t, "visible_at": t + pd.Timedelta(minutes=1), "bid_open": 1.0, "bid_high": 1.0, "bid_low": 1.0,
           "bid_close": 1.0, "ask_open": 1.1, "ask_high": 1.1, "ask_low": 1.1, "ask_close": 1.1, "tick_count": 1,
           "spread_mean": 0.1, "spread_max": 0.1}
    w.put(pd.DataFrame([row]))
    w.put(pd.DataFrame([{**row, "ts_utc": t + pd.Timedelta(minutes=1)}]).assign(**{history.REPLACED_COL: True}))
    assert w.join(5.0)
    got = store.read(history.LIVE_TABLE, source="icm", symbol="XAUUSD")
    assert list(got.sort_values("ts_utc")[history.REPLACED_COL].fillna(False).astype(bool)) == [False, True]


def test_broker_bars_replace_queues_the_minutes_flagged(monkeypatch):
    seen: list[pd.DataFrame] = []
    monkeypatch.setattr(history, "queue_minutes", lambda eng, done: seen.append(done))
    t = pd.Timestamp("2026-11-10 10:00", tz="UTC")
    cols = {"bid_open": 1.0, "bid_high": 1.0, "bid_low": 1.0, "bid_close": 1.0, "ask_open": 1.1, "ask_high": 1.1,
            "ask_low": 1.1, "ask_close": 1.1, "tick_count": 1, "spread_mean": 0.1, "spread_max": 0.1}
    mine = pd.DataFrame([{"ts_utc": t, "visible_at": t + pd.Timedelta(minutes=1), **cols}])
    eng = type("E", (), {"bars_1m": mine, "_dq_pending": [], "_resamplers": {}, "_ctx_cache": {},
                         "_bars_clean": None})()
    bb = broker_bars.BrokerBars()
    bb.fallback = {t}
    broker_bars._replace(eng, bb, mine.assign(bid_close=1.05, bid_high=1.05))
    assert seen and bool(seen[0][history.REPLACED_COL].all())


def test_a_quarantined_minute_in_the_window_is_its_own_cause_and_never_a_refusal(env, tmp_path):
    eng, arm, _ = _held(env, tmp_path)
    live = _versions(eng.bars_1m)
    gone = pd.Timestamp(live.loc[live.index[-20], "ts_utc"])
    m = I.AccountMinutes(live.drop(index=[live.index[-20]]).reset_index(drop=True),
                         quarantined=[int(gone.value)])
    assert m.held(arm, CLOSE) == I.QUARANTINED_MINUTE
    assert I.QUARANTINED_MINUTE not in I.REFUSAL_CAUSES


# ---------------------------------------------------------------- F7: told when an arm stays unverified
def _health(state: Path, now: pd.Timestamp) -> Any:
    from goldbot.ops import health
    ctx = type("Ctx", (), {"state_dir": state, "now": now})()
    return health.check_discovered_arm_reads(ctx)


def _night(db: Database, day: str, unverified: bool, arm_id: str = "D1") -> None:
    r = L.ArmRead(day=day, broker="icm", arm_id=arm_id, role="learning", tf="1h", discovered=True,
                  unverified=unverified, reason="bracket integrity unverified: x" if unverified else "")
    L.record(db, [r], now=pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1, hours=6))


def test_a_discovered_arm_unverified_two_nights_running_warns_and_the_warning_is_told(tmp_path):
    from goldbot.ops import health
    db = Database.open(str(tmp_path), "aux")
    now = pd.Timestamp("2026-11-26 07:00", tz="UTC")
    _night(db, "2026-11-24", True)
    assert _health(tmp_path, now).status == "ok"                             # one night is not a pattern
    _night(db, "2026-11-25", True)
    c = _health(tmp_path, now)
    assert c.status == "warn" and "icm:D1" in c.reason
    assert c.name.startswith(health.NOTIFY_ON_WARN)
    _night(db, "2026-11-26", False)
    assert _health(tmp_path, now).status == "ok"


def test_the_unverified_check_reads_ok_without_an_aux_db(tmp_path):
    assert _health(tmp_path, pd.Timestamp("2026-11-26", tz="UTC")).status == "ok"


# ---------------------------------------------------------------- C3: a retirement is never undone
def test_a_retirement_is_never_reinstated_by_a_later_clean_read(db, monkeypatch):
    obs = _clean()
    monkeypatch.setattr(L, "read_obs", lambda *a, **k: obs)
    kw: dict[str, Any] = dict(hosts={"icm-probe": "icm"}, controls={}, caps={}, now=SETTLED, parity=PASS_1H,
                              recompute=lambda arm, row: GOOD, hosts_read=_covered(obs))
    j = _journal((AR.CONTROL_CHANGED, "D1", pd.Timestamp("2026-11-08", tz="UTC"), {}))
    [r] = L.run(db, arms=[_disc()], day=DAY, journal=j, **kw)
    assert r.decision == "RETIRE"
    [again] = L.run(db, arms=[_disc()], day="2026-11-18", journal=_journal(), **kw)
    assert again.decision == "HOLD"
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM exploration_arm_status WHERE arm_id = 'D1' "
                            "AND status = 'retired'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM exploration_arm_status WHERE arm_id = 'D1' "
                            "AND status != 'retired'").fetchone()[0] == 0


def test_a_retired_spec_hash_never_registers_again_and_writes_no_k_row(env):
    from goldbot.ops import autodisc_register as R
    from goldbot.research.autodisc.spec import spec_hash
    from tests.test_discovered_arm_registration import spec
    first = register(env)
    with Database.open(str(env["state_dir"]), "aux").write() as conn:
        conn.execute("INSERT INTO exploration_arm_status (broker, arm_id, status, day, rule, recorded_ns) "
                     "VALUES ('icm', ?, 'retired', '2026-11-24', 'arm lifecycle: kill rule', 0)", (first.reg.arm_id,))
    k_before = len(registry_rows(env))
    with pytest.raises(R.RegistrationRefused, match="never again"):
        R.register_learning_to_db(spec_hash(spec(0)), "icm-demo", **env)
    assert len(registry_rows(env)) == k_before                              # no K row for a refused re-registration
    other = register(env, 1)                                                # another spec: a new registration
    assert other.k_id != first.k_id and len(registry_rows(env)) == k_before + 2   # its K row and its NOT_TRIAL row
