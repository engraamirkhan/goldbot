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

from goldbot.data import crossfeed
from goldbot.data.store import Store
from goldbot.data.timeutil import epoch_ns
from goldbot.db import Database
from goldbot.engine import arm_registry as AR
from goldbot.engine import broker_bars, exploration, history
from goldbot.research import arm_integrity as I
from goldbot.research import arm_lifecycle as L
from goldbot.research import arm_rules_spec as S
from goldbot.research.exploration_data import session_day
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


@pytest.fixture(autouse=True)
def _engine_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine harness holds about 2,600 minutes; the arm's window is capped below that (engine and night alike:
    both read `exploration.lookback`), so a whole window exists and `window_incomplete` is not what is tested."""
    monkeypatch.setattr(exploration, "LOOKBACK_MAX_1M", 2000)


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
    assert host.marks == {"D1": (int(pd.Timestamp("2026-11-11 14:00Z").value),)}


def test_without_a_capability_marker_since_the_registration_the_arm_holds_as_unverified(db):
    obs = _clean()
    before = int(pd.Timestamp("2026-09-01", tz="UTC").value)                 # since is 2026-10-01
    for marks in ({}, {"D1": (before,)}):
        host = _covered(obs)["icm-probe"].model_copy(update={"marks": marks})
        r = _dread(db, obs, hosts={"icm-probe": host})
        assert r.decision == "HOLD" and r.unverified and "capability" in r.reason
    old = _decisions([{"ts": "2026-11-11 14:00Z", "action": I.CAPABLE_ACTION,
                       "detail": json.dumps({"capability": I.CAPABILITY - 1})}])
    assert I.host_read("icm-probe", old).marks == {}                        # an older contract proves nothing


def _load(ts: str, arm: str = "D1", mark: bool = True, capability: int = I.CAPABILITY) -> list[dict[str, Any]]:
    rows = [{"ts": ts, "action": I.LOADED_ACTION, "detail": json.dumps({"arm": arm})}]
    return rows + ([{"ts": ts, "action": I.CAPABLE_ACTION,
                     "detail": json.dumps({"arm": arm, "capability": capability})}] if mark else [])


def test_every_load_of_the_arm_since_its_registration_needs_its_own_capability_marker(db):
    """S1 (trading-safety review of deb81d05): a load with no marker of the same arm at the same time, a rollback to
    an engine without the contract, holds the arm unverified, even when an earlier load was marked."""
    obs = _clean()
    days = [{"ts": f"{d} 14:00Z", "action": "exploration:sent"} for d in sorted(set(obs["session_day"]))]
    last = [{"ts": "2026-11-18 06:00Z", "action": "exploration:sent"}]
    marked = I.host_read("icm-probe", _decisions(days + last + _load("2026-10-01 12:00Z") + _load("2026-11-02 08:00Z")))
    assert _dread(db, obs, hosts={"icm-probe": marked}).unverified is False
    rolled = I.host_read("icm-probe", _decisions(days + last + _load("2026-10-01 12:00Z")
                                                 + _load("2026-11-02 08:00Z", mark=False)))
    r = _dread(db, obs, hosts={"icm-probe": rolled})
    assert r.unverified and "load" in r.reason and "2026-11-02" in r.reason
    other = I.host_read("icm-probe", _decisions(days + last + _load("2026-10-01 12:00Z")
                                                + _load("2026-11-02 08:00Z", mark=False)
                                                + _load("2026-11-02 08:00Z", arm="D9")[1:]))
    assert _dread(db, obs, hosts={"icm-probe": other}).unverified           # another arm's marker proves nothing
    old = I.host_read("icm-probe", _decisions(days + last + _load("2026-10-01 12:00Z", capability=0)))
    assert _dread(db, obs, hosts={"icm-probe": old}).unverified


def test_the_engine_writes_the_load_and_its_marker_at_the_same_time(env, tmp_path):
    eng, pb, p = _engine_with(env, tmp_path)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    load = [d for d in eng.decisions if d.get("action") == AR.LOADED and d.get("arm") == p.reg.arm_id]
    mark = [d for d in eng.decisions if d.get("action") == AR.CAPABLE and d.get("arm") == p.reg.arm_id]
    assert len(load) == len(mark) == 1 and load[0]["ts"] == mark[0]["ts"] and AR.LOADED == I.LOADED_ACTION


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


def test_a_window_the_store_cannot_prove_leaves_the_arm_unverified_never_silent(db):
    """Q2: a 1d/1w candidate is not only void: the arm reads unverified, so the health check tells it."""
    r = _dread(db, _clean(), recompute=lambda arm, row: I.WINDOW_UNPROVABLE)
    assert I.WINDOW_UNPROVABLE in I.UNVERIFYING
    assert r.unverified and r.decision == "HOLD" and I.WINDOW_UNPROVABLE in r.reason


def test_registration_refuses_a_1d_or_1w_spec(env):
    """Q2: discovered learning arms run on 1h/4h only (2.4 (d)), so no 1d/1w arm ever reaches the night (a 1w spec
    cannot even be written: `StrategySpec.timeframe` has no 1w)."""
    from goldbot.ops import autodisc_register as R
    from goldbot.research.autodisc.gen_combine import family_spec
    from goldbot.research.autodisc.ledger import SearchLedger
    from goldbot.research.autodisc.spec import spec_hash
    from tests.test_discovered_arm_registration import GOOD as EXPLORE
    from tests.test_discovered_arm_registration import T2R
    assert set(S.LEARNING_TFS) == {"1h", "4h"}
    ledger = SearchLedger(env["ledger_path"], mode="real")
    for tf in ("1d",):
        sp = family_spec("box_break_close", tf, None, T2R)
        ledger.log_spec(sp, "G1", "1")
        ledger.record_result(spec_hash(sp), EXPLORE)
        ledger.log_spec(sp, "G1", "1", null=True)
        ledger.record_result(spec_hash(sp), {"null_fitness": [0.5, 1.1, None, 0.9]}, null=True)
        with pytest.raises(R.RegistrationRefused, match=r"2\.4 \(d\)"):
            R.register_learning_to_db(spec_hash(sp), "icm-demo", **env)


def _m1(minutes: pd.DataFrame, **cols: Any) -> pd.DataFrame:
    """The broker's own M1 (`bars_1m_broker`, the reconcile's pull) of `minutes`: evidence, never a bar held."""
    return _versions(minutes, rank=1, **cols)


def _sorted(*parts: pd.DataFrame) -> pd.DataFrame:
    return pd.concat(parts).sort_values(["ts_utc", "rank"], kind="stable").reset_index(drop=True)


def test_a_window_missing_its_last_minutes_is_incomplete_void_and_never_cached_then_stored_when_they_arrive(
        env, tmp_path):
    """Q1: night N has the candidate's window without its last 5 minutes (not yet in the brain's store, nor the
    broker's M1): void `window_incomplete`, not a refusal, no verdict. Night N+1 has them: the verdict is stored,
    ok."""
    eng, arm, engine = _held(env, tmp_path)
    full = _sorted(_versions(eng.bars_1m), _m1(eng.bars_1m))
    short = full[full["ts_utc"] < CLOSE - pd.Timedelta(minutes=5)]
    assert I.AccountMinutes(short).held(arm, CLOSE) == I.WINDOW_INCOMPLETE
    assert I.AccountMinutes(full.tail(400)).held(arm, CLOSE) == I.WINDOW_INCOMPLETE      # shorter than the window
    assert I.WINDOW_INCOMPLETE not in I.REFUSAL_CAUSES and I.WINDOW_INCOMPLETE not in I.UNVERIFYING
    db = Database.open(str(tmp_path / "night"), "aux")
    row = {"arm_id": arm.id, "account_id": "icm-probe", "decision_utc": CLOSE, "session_day": session_day(CLOSE),
           "taken": False, "stop_dist": engine.stop_dist, "target_dist": engine.target_dist,
           "max_hold_s": engine.max_hold_s, "recorded_ns": int(CLOSE.value) + 1_000_000_000, "entry_utc": pd.NaT}
    obs = pd.DataFrame([row])
    host = {"icm-probe": I.HostRead(account_id="icm-probe", days=(session_day(CLOSE),),
                                    last_ns=int(CLOSE.value) + 10**12, marks={arm.id: (0,)})}
    kw: dict[str, Any] = dict(broker="icm", journal=_journal(), accounts={"icm-probe"},
                              since=pd.Timestamp(0, tz="UTC"), hosts=host, now=CLOSE + pd.Timedelta(days=3),
                              settled=lambda day, hold: CLOSE + pd.Timedelta(days=2))
    night_n = I.integrity(db, obs, arm, recompute=I.engine_recompute({"icm-probe": short}), **kw)
    assert night_n.void_by_cause == {I.WINDOW_INCOMPLETE: 1} and night_n.n_refused == 0
    assert I.verdicts(db, "icm", arm.id) == {}
    night_n1 = I.integrity(db, obs, arm, recompute=I.engine_recompute({"icm-probe": full}), **kw)
    assert night_n1.void_by_cause == {} and night_n1.n_refused == 0
    assert [v["verdict"] for v in I.verdicts(db, "icm", arm.id).values()] == ["ok"]


def test_a_verdict_is_written_only_once_its_day_has_settled_and_its_account_is_covered(db):
    """Q1: before the settle lag, or while its account's coverage fails, a verdict is read but never frozen."""
    obs = _clean()
    since = pd.Timestamp("2026-10-01", tz="UTC")
    kw: dict[str, Any] = dict(broker="icm", journal=_journal(), recompute=lambda arm, row: GOOD,
                              accounts={"icm-probe"}, since=since, now=SETTLED)
    I.integrity(db, obs, _disc(), hosts=_covered(obs), settled=lambda day, hold: SETTLED + pd.Timedelta(days=1), **kw)
    assert I.verdicts(db, "icm", "D1") == {}                                 # not settled yet
    gap = _covered(obs)["icm-probe"].model_copy(update={"marks": {}})
    I.integrity(db, obs, _disc(), hosts={"icm-probe": gap}, settled=lambda day, hold: SETTLED, **kw)
    assert I.verdicts(db, "icm", "D1") == {}                                 # its account is not covered
    I.integrity(db, obs, _disc(), hosts=_covered(obs), settled=lambda day, hold: SETTLED, **kw)
    assert len(I.verdicts(db, "icm", "D1")) == 120


def test_release_minutes_after_the_cutover_in_the_window_void_the_candidate(env, tmp_path):
    """A live minute missing after the cut-over: the release minute there is never read. With the broker's M1
    showing the bar, it is a gap (void); before the broker's M1 is pulled past the decision, the window is not
    provable yet (`window_incomplete`, tried again), never ok."""
    eng, arm, engine = _held(env, tmp_path)
    live = _versions(eng.bars_1m)
    cut = live.index[len(live) // 2]
    gap = live.loc[[cut + 10]].assign(rank=3)                              # a release minute filling a live gap
    v = _sorted(live.drop(index=[cut + 10]), gap)
    assert I.AccountMinutes(v).held(arm, CLOSE) == I.WINDOW_INCOMPLETE
    assert I.AccountMinutes(_sorted(v, _m1(eng.bars_1m))).held(arm, CLOSE) == I.GAP_AFTER_CUTOVER
    before = live.loc[: cut - 1].assign(rank=3)                            # release only before the first live minute
    v2 = _sorted(before, live.loc[cut:], _m1(eng.bars_1m))
    held = I.AccountMinutes(v2).held(arm, CLOSE)
    assert isinstance(held, pd.DataFrame) and AR.decision_bracket(held, arm, CLOSE) == engine


def test_a_release_minute_where_the_brokers_m1_shows_no_bar_after_the_cutover_is_not_held_and_the_read_verifies(
        env, tmp_path):
    """Root cause of the 4h voids: the engine never holds a release minute at or after its cut-over
    (`engine.history`: release minutes before the cut-over only), but the night filled every live gap from the
    release and then voided. A minute the broker's own M1 shows it never had (its M1 observing the span, no bar at
    the minute) was never an engine minute: the night leaves it out, as the engine did, and recomputes."""
    eng, arm, _ = _held(env, tmp_path)
    live = _versions(eng.bars_1m)
    cut = live.index[len(live) // 2]
    quiet = cut + 10
    m1 = live.drop(index=[quiet]).assign(rank=1)                           # the broker had no bar at `quiet`
    other = live.loc[[quiet]].assign(rank=3, bid_high=live.loc[quiet, "bid_high"] + 40.0)
    v = _sorted(live.loc[: cut - 1].assign(rank=3), live.loc[cut:].drop(index=[quiet]), other, m1)
    held = I.AccountMinutes(v).held(arm, CLOSE)
    assert isinstance(held, pd.DataFrame)
    assert pd.Timestamp(live.loc[quiet, "ts_utc"]) not in set(held["ts_utc"])   # never the release's minute
    engine_held = I.AccountMinutes(_sorted(live.loc[: cut - 1].assign(rank=3),
                                           live.loc[cut:].drop(index=[quiet]), m1)).held(arm, CLOSE)
    assert isinstance(engine_held, pd.DataFrame)
    assert AR.decision_bracket(held, arm, CLOSE) == AR.decision_bracket(engine_held, arm, CLOSE)


def test_a_gap_after_the_cutover_the_brokers_m1_has_a_bar_for_voids_even_without_a_release_minute(env, tmp_path):
    """A genuine gap: the broker had the minute (its M1 shows it), the engine's stored minutes lack it as of the
    decision. The engine may have held it (a write lost or late), so the window is not provable: void. This holds
    after the release ends too (the night used to see such a gap only where the release had the minute)."""
    eng, arm, _ = _held(env, tmp_path)
    live = _versions(eng.bars_1m)
    cut = live.index[len(live) // 2]
    lost = cut + 10
    v = _sorted(live.loc[: cut - 1].assign(rank=3), live.loc[cut:].drop(index=[lost]), live.assign(rank=1))
    assert I.AccountMinutes(v).held(arm, CLOSE) == I.GAP_AFTER_CUTOVER
    assert I.GAP_AFTER_CUTOVER not in I.REFUSAL_CAUSES and I.GAP_AFTER_CUTOVER not in I.UNVERIFYING
    # the broker's M1 itself with a hole of more than BROKER_SILENCE_MAX of open minutes (a missed pull) around it:
    # it does not observe the span, so nothing is proven either way: unverified and told, never ok
    far = live.index[(pd.to_datetime(live["ts_utc"], utc=True)
                      - pd.Timestamp(live.loc[lost, "ts_utc"])).abs() <= I.BROKER_SILENCE_MAX]
    sparse = live.drop(index=far).assign(rank=1)
    other = live.loc[[lost]].assign(rank=3)
    v2 = _sorted(live.loc[: cut - 1].assign(rank=3), live.loc[cut:].drop(index=[lost]), other, sparse)
    assert I.AccountMinutes(v2).held(arm, CLOSE) == I.NO_BROKER_COVERAGE
    assert I.NO_BROKER_COVERAGE in I.UNVERIFYING


def test_a_minute_the_engine_lacks_with_no_broker_m1_after_it_is_never_read_as_ok(env, tmp_path):
    """Quant review of bd861f03 (MEDIUM): no proof of a gap is not proof of none. The engine stored no copy of a
    minute and the reconcile has not pulled the broker's M1 past it yet: `window_incomplete` (tried again), never a
    window shifted one minute back and a final verdict. Still not pulled past COVERAGE_WAIT after the decision:
    `no_broker_coverage` (unverified, told), never incomplete for ever."""
    eng, arm, _ = _held(env, tmp_path)
    live = _versions(eng.bars_1m)
    lost = live.index[-30]
    m1 = _m1(eng.bars_1m.loc[: lost - 1])                                   # pulled up to before the minute only
    v = _sorted(live.drop(index=[lost]), m1)
    assert I.AccountMinutes(v).held(arm, CLOSE) == I.WINDOW_INCOMPLETE
    assert I.AccountMinutes(v, now=CLOSE + pd.Timedelta(days=1)).held(arm, CLOSE) == I.WINDOW_INCOMPLETE
    late = CLOSE + I.COVERAGE_WAIT + pd.Timedelta(minutes=1)
    assert I.AccountMinutes(v, now=late).held(arm, CLOSE) == I.NO_BROKER_COVERAGE
    assert I.AccountMinutes(live.drop(index=[lost])).held(arm, CLOSE) == I.WINDOW_INCOMPLETE     # no M1 at all
    assert I.COVERAGE_WAIT == pd.Timedelta(hours=crossfeed.RECONCILE_MAX_AGE_HOURS)


def test_a_lost_last_minute_the_broker_had_is_a_gap_once_its_m1_is_pulled_not_incomplete_for_ever(env, tmp_path):
    """Trading-safety review of bd861f03 (LOW): the minute before the decision stored by no one but the broker's M1
    has it: a void (`gap_after_cutover`), not `window_incomplete` night after night."""
    eng, arm, _ = _held(env, tmp_path)
    live = _versions(eng.bars_1m)
    last = live.index[(pd.to_datetime(live["ts_utc"], utc=True) == CLOSE - pd.Timedelta(minutes=1)).to_numpy()][0]
    v = _sorted(live.drop(index=[last]), _m1(eng.bars_1m))
    assert I.AccountMinutes(v, now=CLOSE + pd.Timedelta(days=1)).held(arm, CLOSE) == I.GAP_AFTER_CUTOVER


def test_the_engines_saved_rebased_cutover_cuts_the_release_minutes_off_where_the_engine_did(env, tmp_path):
    """Quant review of bd861f03 (LOW): the engine's cut-over is the earlier of its first stored minute and the one a
    history rebase saved (aux.db `history_cutover`, kept even if its minutes were lost). Release minutes between
    the saved cut-over and the first stored minute were not what the engine held: the night never reads them."""
    eng, arm, engine = _held(env, tmp_path)
    live = _versions(eng.bars_1m)
    cut = live.index[len(live) // 2]
    v = _sorted(live.loc[: cut - 1].assign(rank=3), live.loc[cut:], _m1(eng.bars_1m))
    held = I.AccountMinutes(v).held(arm, CLOSE)
    assert isinstance(held, pd.DataFrame) and AR.decision_bracket(held, arm, CLOSE) == engine
    saved = int(pd.Timestamp(live.loc[cut - 50, "ts_utc"]).value)
    assert I.AccountMinutes(v, saved_cutover=saved).held(arm, CLOSE) == I.GAP_AFTER_CUTOVER
    later = int(pd.Timestamp(live.loc[cut + 50, "ts_utc"]).value)          # a later saved one changes nothing
    held = I.AccountMinutes(v, saved_cutover=later).held(arm, CLOSE)
    assert isinstance(held, pd.DataFrame) and AR.decision_bracket(held, arm, CLOSE) == engine


def test_the_saved_cutover_is_read_from_the_engines_aux_db_and_an_unreadable_one_leaves_the_arm_unverified(
        env, tmp_path, monkeypatch):
    eng, arm, _ = _held(env, tmp_path)
    store = Store(tmp_path / "data")
    minutes = eng.bars_1m.assign(available_utc=pd.to_datetime(eng.bars_1m["visible_at"], utc=True))
    store.append("bars_1m_live", minutes, source="icm", symbol="XAUUSD")
    aux = Database.open(str(tmp_path / "aux"), "aux")
    assert I.saved_cutover(aux, "icm-probe", "icm", "XAUUSD") is None
    with aux.write() as conn:
        conn.execute("INSERT INTO history_cutover (account_id, source, symbol, cutover_ns, updated_ns) "
                     "VALUES (?, ?, ?, ?, ?)", ("icm-probe", "icm", "XAUUSD", 123, 1))
    assert I.saved_cutover(aux, "icm-probe", "icm", "XAUUSD") == 123

    def boom(*a: Any, **k: Any) -> None:
        raise sqlite3.OperationalError("no such table")
    monkeypatch.setattr(I, "saved_cutover", boom)
    got = I.stored_recompute(store, {"icm-probe": ("icm", "XAUUSD")}, now=CLOSE + pd.Timedelta(days=1), db=aux)
    assert got(arm, {"account_id": "icm-probe", "decision_utc": CLOSE}) == I.CUTOVER_UNREADABLE
    assert I.CUTOVER_UNREADABLE in I.UNVERIFYING


def test_overlapping_replacement_events_are_accounted_for_together_by_the_sum_of_their_counts(env, tmp_path):
    """Trading-safety review of bd861f03 (LOW): two events whose spans overlap must not vouch for each other with
    the same flagged minutes: event A (2 minutes) and event B (1 minute) need 3 flagged minutes, not 2."""
    eng, arm, engine = _held(env, tmp_path)
    live = _versions(eng.bars_1m)
    a, b = live.index[-10], live.index[-5]
    ns = {k: int(pd.Timestamp(live.loc[k, "ts_utc"]).value) for k in (a, b, b + 1)}
    events = [(ns[a], 2), (ns[b], 1)]
    two = live.copy()
    two.loc[[a, b], "replaced"] = True
    v = _sorted(two, _m1(eng.bars_1m))
    assert I.AccountMinutes(v, replaced=events).held(arm, CLOSE) == I.REPLACED_MINUTE
    three = live.copy()
    three.loc[[a, b, b + 1], "replaced"] = True
    held = I.AccountMinutes(_sorted(three, _m1(eng.bars_1m)), replaced=events).held(arm, CLOSE)
    assert isinstance(held, pd.DataFrame) and AR.decision_bracket(held, arm, CLOSE) == engine


def test_a_weekend_the_brokers_m1_is_silent_across_is_observed_and_release_minutes_there_are_no_gap():
    """Quant review of bd861f03 (item 3): the release may print Sunday-open or Friday-close minutes the broker
    lacks; the broker's M1 hole over the weekend holds almost no minutes the session table expects open, so it
    observes the span and those minutes are no gap. A missed weekday pull is not silence."""
    fri = pd.date_range("2025-01-31 18:00", "2025-01-31 21:56", freq="min", tz="UTC")
    sun = pd.date_range("2025-02-02 23:20", "2025-02-03 02:00", freq="min", tz="UTC", inclusive="left")
    broker = pd.DatetimeIndex(fri.append(sun))
    release = pd.date_range("2025-02-02 22:00", "2025-02-02 23:19", freq="min", tz="UTC")
    at = pd.Timestamp("2025-02-03 02:00", tz="UTC")

    def frame(stamps: pd.DatetimeIndex, rank: int) -> pd.DataFrame:
        return pd.DataFrame({"ts_utc": stamps, "rank": rank})
    v = _sorted(frame(broker, 0), frame(broker, 1), frame(release, 3))
    m = I.AccountMinutes(v)
    own = epoch_ns(broker)
    assert m.cutover_cause(own, int(own.min()), at) is None
    lost = own[own != int(sun[5].value)]                                    # a Sunday minute the broker had
    assert m.cutover_cause(lost, int(own.min()), at) == I.GAP_AFTER_CUTOVER
    wed = pd.date_range("2025-02-04 00:00", "2025-02-06 00:00", freq="min", tz="UTC", inclusive="left")
    gone = (wed >= pd.Timestamp("2025-02-04 12:00", tz="UTC")) & (wed < pd.Timestamp("2025-02-05 12:00", tz="UTC"))
    pulled = wed[~gone]                                                      # a day of the broker's M1 not pulled
    w = I.AccountMinutes(_sorted(frame(wed, 0), frame(pulled, 1)))
    assert w.cutover_cause(epoch_ns(wed), int(wed[0].value), pd.Timestamp("2025-02-06 00:00", tz="UTC")) == \
        I.NO_BROKER_COVERAGE


def test_the_brokers_m1_never_fills_a_minute_the_engine_never_held(env, tmp_path):
    """The broker's M1 is evidence, never a bar: where the engine's minute is stored, the M1's (another price) is not
    read; where it is not, the M1 does not fill it, it says the broker had a bar the store lacks (a gap, void)."""
    eng, arm, engine = _held(env, tmp_path)
    live = _versions(eng.bars_1m)
    gone = live.index[-5]
    m1 = _m1(eng.bars_1m)
    m1.loc[gone, "bid_high"] = m1.loc[gone, "bid_high"] + 30.0
    held = I.AccountMinutes(_sorted(live, m1)).held(arm, CLOSE)
    assert isinstance(held, pd.DataFrame) and AR.decision_bracket(held, arm, CLOSE) == engine
    assert I.AccountMinutes(_sorted(live.drop(index=[gone]), m1)).held(arm, CLOSE) == I.GAP_AFTER_CUTOVER


def test_a_minute_flagged_replaced_and_written_after_the_decision_voids_the_candidate_without_a_dq_event(env,
                                                                                                         tmp_path):
    """P3: the replacement is persisted with the minute itself, so a crash before the bar close's dq flush cannot
    lose it."""
    eng, arm, engine = _held(env, tmp_path)
    live = _versions(eng.bars_1m)
    i = live.index[-3]
    late = live.copy()
    late.loc[i, "replaced"], late.loc[i, "written_at"] = True, CLOSE + pd.Timedelta(seconds=30)
    assert I.AccountMinutes(_sorted(late, _m1(eng.bars_1m))).held(arm, CLOSE) == I.REPLACED_MINUTE
    early = live.copy()
    early.loc[i, "replaced"], early.loc[i, "written_at"] = True, CLOSE - pd.Timedelta(seconds=30)
    held = I.AccountMinutes(_sorted(early, _m1(eng.bars_1m))).held(arm, CLOSE)
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


def test_a_replacement_written_after_the_decision_and_past_the_live_grace_is_found_through_the_store(env, tmp_path):
    """K1 (code review of deb81d05, ~/tmp/rv/y.py): `as_of_minutes` drops a replacement written after the decision,
    so the flag is read on every stored version of the window, before the as-of filter."""
    from goldbot.data.account_bars import LIVE_WRITE_GRACE
    from goldbot.data.store import Store
    eng, arm, engine = _held(env, tmp_path)
    minutes = eng.bars_1m.copy()
    minutes["available_utc"] = pd.to_datetime(minutes["visible_at"], utc=True) + pd.Timedelta(seconds=1)
    minutes["replaced"] = False
    i = minutes.index[-40]
    minutes.loc[i, "available_utc"] = CLOSE + LIVE_WRITE_GRACE + pd.Timedelta(minutes=3)
    minutes.loc[i, "replaced"] = True
    minutes.loc[i, "bid_high"] += 20.0
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", minutes, source="icm", symbol="XAUUSD")
    got = I.stored_recompute(store, {"icm-probe": ("icm", "XAUUSD")}, now=CLOSE + pd.Timedelta(days=1))
    assert got(arm, {"account_id": "icm-probe", "decision_utc": CLOSE}) == I.REPLACED_MINUTE


def test_a_quarantined_minute_in_the_window_is_its_own_cause_and_never_a_refusal(env, tmp_path):
    eng, arm, _ = _held(env, tmp_path)
    live = _versions(eng.bars_1m)
    gone = pd.Timestamp(live.loc[live.index[-20], "ts_utc"])
    m1 = _m1(eng.bars_1m.drop(index=[live.index[-20]]))                   # quarantine leaves it out of every feed
    m = I.AccountMinutes(_sorted(live.drop(index=[live.index[-20]]), m1), quarantined=[int(gone.value)])
    assert m.held(arm, CLOSE) == I.QUARANTINED_MINUTE
    assert I.QUARANTINED_MINUTE not in I.REFUSAL_CAUSES


# ---------------------------------------------------------------- F7: told when an arm stays unverified
def _health(state: Path, now: pd.Timestamp) -> Any:
    from goldbot.ops import health
    ctx = type("Ctx", (), {"state_dir": state, "now": now})()
    return health.check_discovered_arm_reads(ctx)


def _night(db: Database, day: str, unverified: bool, arm_id: str = "D1", decision: str = "HOLD") -> None:
    r = L.ArmRead(day=day, broker="icm", arm_id=arm_id, role="learning", tf="1h", discovered=True,
                  unverified=unverified, reason="bracket integrity unverified: x" if unverified else "",
                  decision=decision)  # type: ignore[arg-type]
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


def test_an_arm_no_longer_read_or_retired_never_warns(tmp_path):
    """K2 (code review of deb81d05, ~/tmp/rv/x.py): only the latest lifecycle night counts, and a retired arm is
    not read any more."""
    db = Database.open(str(tmp_path), "aux")
    now = pd.Timestamp("2026-11-26 07:00", tz="UTC")
    _night(db, "2026-09-01", True, arm_id="OLD")
    _night(db, "2026-09-02", True, arm_id="OLD")                          # not read since September
    _night(db, "2026-11-24", True, arm_id="GONE")
    _night(db, "2026-11-25", True, arm_id="GONE", decision="RETIRE")      # retired on its last read
    _night(db, "2026-11-25", False, arm_id="LIVE")
    c = _health(tmp_path, now)
    assert c.status == "ok", c.reason
    _night(db, "2026-11-24", True, arm_id="D1")
    _night(db, "2026-11-25", True, arm_id="D1")
    c = _health(tmp_path, now)
    assert c.status == "warn" and "icm:D1" in c.reason and "OLD" not in c.reason and "GONE" not in c.reason


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
