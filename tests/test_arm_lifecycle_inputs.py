"""The nightly `arm_lifecycle` job's inputs for discovered learning arms
(docs/research/prereg-amendment-2026-11-18-discovered-arms.md 5.1 and section 4).

Section 4 (quant ruling 2026-11-18 Q6): "The stored `stop_dist`, `target_dist` and `max_hold_s` must equal
`arm_rules_spec.bracket(spec, bars closed <= decision_ns, t)`, recomputed from the bars, within 1e-9. Otherwise the
candidate is void." 5.1 (5): "A refused bracket (section 4) on 3 or more candidates, or on any taken candidate"
retires. Section 2.2: "Any change to the control's parameters after registration voids the arm ... and it retires."

So the job must hand the lifecycle read the engine's journal (`arm_registry_bracket_void`,
`arm_registry_control_changed`) and a recompute from the very minutes the engine held at the decision (its own stored
minutes, point in time, in the engine's window: Wilder ATR depends on where its window starts). Every store, state
dir and registry is a temp copy; the minutes are the engine harness's synthetic ticks."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.data.store import Store
from goldbot.db import Database
from goldbot.engine import arm_registry as AR
from goldbot.engine import broker_bars, exploration
from goldbot.ops.accounts import load_accounts
from goldbot.research import arm_integrity as I
from goldbot.research import arm_lifecycle as L
from goldbot.research import arm_rules_spec as S
from tests.test_arm_lifecycle import _disc_obs, _obs
from tests.test_discovered_arm_registration import _engine_with, _ex, arm_rows, env, register
from tests.test_exploration_engine import CLOSE, _at
from tests.test_exploration_engine import (
    _rules as _rules,  # noqa: F401  (autouse: the test arms' fixed rules)
)

__all__ = ["env"]                       # the registration fixture, shared with test_discovered_arm_registration

HOST = {"icm-probe": ("icm", "XAUUSD")}
NIGHT = CLOSE + pd.Timedelta(days=1)


def _stored(tmp_path: Path, minutes: pd.DataFrame) -> Store:
    """The engine's minutes as its history writer stores them (`bars_1m_live`, source = broker, written live)."""
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", minutes.assign(available_utc=pd.to_datetime(minutes["visible_at"], utc=True)
                                                + pd.Timedelta(seconds=1)), source="icm", symbol="XAUUSD")
    return store


def _engine_bracket(env: dict[str, Any], tmp_path: Path) -> tuple[Any, Any, S.Bracket]:
    """(engine, its learning arm, the bracket the engine checks the arm's signal at CLOSE against)."""
    eng, pb, p = _engine_with(env, tmp_path)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    arm = _ex(eng).arms[p.reg.arm_id]
    engine = AR.decision_bracket(eng.bars_1m, arm.spec, CLOSE)
    assert engine is not None
    return eng, arm, engine


# ---------------------------------------------------------------- bar parity (section 4, Q6)
def test_the_nightly_recompute_from_the_engines_stored_minutes_equals_the_engines_bracket_for_the_same_candidate(
        env, tmp_path):
    eng, arm, engine = _engine_bracket(env, tmp_path)
    store = _stored(tmp_path, eng.bars_1m)
    night = I.stored_recompute(store, HOST, now=NIGHT)
    got = night(arm.spec, {"account_id": "icm-probe", "decision_utc": CLOSE})
    assert got == engine                                       # exactly, not within a tolerance
    s = exploration.Pending(arm_id=arm.arm_id, side=1, bar_ts=CLOSE, stop_usd=got.stop_dist,
                            target_usd=got.target_dist, hold_bars=got.max_hold_s // 3600)
    assert AR.bracket_refusal(eng, arm, s, CLOSE + pd.Timedelta(seconds=2)) is None
    row = {"stop_dist": engine.stop_dist, "target_dist": engine.target_dist, "max_hold_s": engine.max_hold_s,
           "decision_ns": int(CLOSE.value), "recorded_ns": int(CLOSE.value) + 1_000_000_000, "entry_ns": None}
    assert S.bracket_void(row, got) is None                    # the engine's candidate stands at night


def test_minutes_stored_after_the_decision_are_not_read_by_the_nightly_recompute(env, tmp_path):
    """Point in time: minutes after the decision, in the store by night, change nothing (T6 through the store)."""
    eng, arm, engine = _engine_bracket(env, tmp_path)
    later = eng.bars_1m.tail(30).copy()
    later["ts_utc"] = pd.to_datetime(later["ts_utc"], utc=True) + pd.Timedelta(hours=2)
    later["visible_at"] = later["ts_utc"] + pd.Timedelta(minutes=1)
    for c in ("bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close"):
        later[c] = later[c] * 1.05
    store = _stored(tmp_path, pd.concat([eng.bars_1m, later], ignore_index=True))
    night = I.stored_recompute(store, HOST, now=NIGHT)
    assert night(arm.spec, {"account_id": "icm-probe", "decision_utc": CLOSE}) == engine


def test_another_window_gives_another_wilder_atr_so_the_window_must_be_the_engines(env, tmp_path):
    """Why the window matters: Wilder ATR depends on where its window starts. The same minutes cut shorter give
    another bracket, which would void a bracket the engine rightly accepted."""
    eng, arm, engine = _engine_bracket(env, tmp_path)
    got = exploration.arm_bars(eng.bars_1m[eng.bars_1m["visible_at"] <= CLOSE], arm.spec, CLOSE)
    assert got is not None
    shorter = S.bracket(S.spec_of(arm.spec), got[0].tail(20).reset_index(drop=True), CLOSE)
    assert shorter is not None and S.bracket_mismatch(shorter, engine) is not None


def test_a_window_minute_the_engine_replaced_after_the_decision_leaves_the_candidate_unrecomputable_not_refused(
        env, tmp_path):
    """The engine decides on a sampled minute when the broker's bar is late and replaces it within GIVE_UP; the store
    keeps only the last copy. A replacement logged (`broker_bar_replaced`) within REPLACE_SPAN before the decision
    means the store no longer shows what the engine held: no recompute (void for the read, never a refusal)."""
    eng, arm, engine = _engine_bracket(env, tmp_path)
    held = eng.bars_1m[eng.bars_1m["visible_at"] <= CLOSE]
    at = {"account_id": "icm-probe", "decision_utc": CLOSE}

    def replaced(tmp: Path, back: int) -> tuple[Store, pd.DataFrame]:
        """The engine's minutes with the one `back` minutes before CLOSE replaced by the broker's (a wider range
        than sampled), the store holding only that last copy, written as live."""
        i = held.index[-back]
        revised = eng.bars_1m.copy()
        for c in ("bid_high", "ask_high"):
            revised.loc[i, c] = revised.loc[i, c] + 25.0
        return _stored(tmp, revised), revised.loc[[i]]

    store, minute = replaced(tmp_path / "near", 3)              # a minute of the decision bar
    blind = I.stored_recompute(store, HOST, now=NIGHT)(arm.spec, at)
    assert blind is not None and blind != engine                 # the hazard is real: not what the engine held
    _replaced_event(store, minute)
    assert I.stored_recompute(store, HOST, now=NIGHT)(arm.spec, at) == I.REPLACED_MINUTE
    # a minute replaced more than REPLACE_SPAN before the decision was replaced before it: the stored copy is the
    # one the engine held, so the night recomputes from it
    back = int(I.REPLACE_SPAN / pd.Timedelta(minutes=1)) + 2
    store, minute = replaced(tmp_path / "far", back)
    _replaced_event(store, minute)
    expected = AR.decision_bracket(pd.concat([held.drop(index=minute.index), minute]).sort_values("ts_utc"),
                                   arm.spec, CLOSE)
    assert expected is not None and expected != engine
    assert I.stored_recompute(store, HOST, now=NIGHT)(arm.spec, at) == expected


def _replaced_event(store: Store, minute: pd.DataFrame) -> None:
    """The engine's dq row for a replaced minute (`broker_bars._replace`: ts_utc = the minute)."""
    store.append("dq_events", pd.DataFrame([{"ts_utc": pd.Timestamp(minute["ts_utc"].iloc[0]),
                                             "check": broker_bars.REPLACED, "severity": "warning",
                                             "detail": "1 minute(s) replaced"}]),
                 source="icm-probe", symbol="XAUUSD", dedupe=False)


def test_the_replacement_span_covers_the_engines_give_up_and_one_rates_call():
    assert I.REPLACE_SPAN >= broker_bars.GIVE_UP + pd.Timedelta(minutes=broker_bars.FETCH_N + 1)


def test_unreadable_replacement_events_leave_every_candidate_of_the_account_unrecomputable(env, tmp_path,
                                                                                           monkeypatch):
    eng, arm, _ = _engine_bracket(env, tmp_path)
    store = _stored(tmp_path, eng.bars_1m)
    real = store.read

    def read(table: str, **kw: Any) -> pd.DataFrame:
        if table == "dq_events":
            raise OSError("unreadable")
        return real(table, **kw)
    monkeypatch.setattr(store, "read", read)
    night = I.stored_recompute(store, HOST, now=NIGHT)
    assert night(arm.spec, {"account_id": "icm-probe", "decision_utc": CLOSE}) == I.DQ_UNREADABLE


# ---------------------------------------------------------------- the nightly job acts on the engine's journal
SLOT = pd.Timestamp("2026-11-25 06:25", tz="UTC")


def _ctx(env: dict[str, Any], store: Store) -> Any:
    from goldbot.config import load_settings
    return type("Ctx", (), {"settings": load_settings(), "state_dir": env["state_dir"], "store": store,
                            "accounts": list(load_accounts(env["accounts_path"], logins=False).values())})()


def _journal(store: Store, account: str, action: str, arm_id: str, ts: pd.Timestamp, **detail: Any) -> None:
    """One engine journal row, as `Engine.flush_journal` writes it."""
    store.append("decisions", pd.DataFrame([{"ts_utc": ts, "account_id": account, "agent_id": "explore:" + arm_id,
                                             "action": action, "p": None, "mult": None, "proposal_id": None,
                                             "detail": json.dumps({"arm": arm_id, **detail})}]),
                 source=account, symbol="XAUUSD", dedupe=False)


def _status(state: Path) -> list[tuple[str, str]]:
    with Database.open(str(state), "aux").connection() as conn:
        return [(str(a), str(r)) for a, r in conn.execute("SELECT arm_id, rule FROM exploration_arm_status")]


def test_the_nightly_job_retires_a_discovered_arm_on_a_journalled_control_change(env, tmp_path):
    from goldbot.ops import jobs
    p = register(env)
    store = Store(tmp_path / "data")
    _journal(store, "icm-demo", AR.CONTROL_CHANGED, p.reg.arm_id, pd.Timestamp("2026-11-24 03:00", tz="UTC"),
             control=p.reg.arm_id + "C", why="changed after registration")
    out = jobs.arm_lifecycle(_ctx(env, store), SLOT)
    [r] = [x for x in L.reads_of(Database.open(str(env["state_dir"]), "aux"), "2026-11-24")
           if x.arm_id == p.reg.arm_id and x.broker == "icm"]
    assert r.decision == "RETIRE" and "control changed" in r.reason and r.void_from is not None, out
    assert any(a == p.reg.arm_id and "control changed" in rule for a, rule in _status(env["state_dir"]))


def test_the_nightly_job_retires_a_discovered_arm_on_a_bracket_void_the_engine_journalled_at_the_entry(env, tmp_path,
                                                                                               monkeypatch):
    from goldbot.ops import jobs
    p = register(env)
    arm_id = p.reg.arm_id
    obs = _disc_obs(_obs(0.0, arm_id=arm_id, control=arm_id + "C", days=2, end="2026-11-24"), arm_id=arm_id)
    obs["account_id"] = "icm-demo"
    taken = obs[(obs["arm_id"] == arm_id) & obs["taken"]].iloc[0]
    monkeypatch.setattr(L, "read_obs", lambda *a, **k: obs)
    store = Store(tmp_path / "data")
    _journal(store, "icm-demo", AR.BRACKET_VOID, arm_id, taken["decision_utc"] + pd.Timedelta(seconds=2),
             bar=taken["decision_utc"].isoformat(), why="bracket_mismatch")
    jobs.arm_lifecycle(_ctx(env, store), SLOT)
    [r] = [x for x in L.reads_of(Database.open(str(env["state_dir"]), "aux"), "2026-11-24") if x.arm_id == arm_id and x.broker == "icm"]
    assert r.decision == "RETIRE" and r.kill == I.ENGINE_VOID_KILL and r.n_bracket_void >= 1
    assert any(a == arm_id and "bracket integrity" in rule for a, rule in _status(env["state_dir"]))


def test_an_unreadable_journal_holds_a_discovered_arm_as_unverified_never_evidence(env, tmp_path, monkeypatch):
    from goldbot.ops import jobs
    p = register(env)
    store = Store(tmp_path / "data")
    real = store.read

    def read(table: str, **kw: Any) -> pd.DataFrame:
        if table == "decisions":
            raise OSError("unreadable")
        return real(table, **kw)
    monkeypatch.setattr(store, "read", read)
    jobs.arm_lifecycle(_ctx(env, store), SLOT)
    [r] = [x for x in L.reads_of(Database.open(str(env["state_dir"]), "aux"), "2026-11-24")
           if x.arm_id == p.reg.arm_id and x.broker == "icm"]
    assert r.decision == "HOLD" and r.unverified and not r.validated and "journal" in r.reason


def test_the_journal_is_read_from_the_earliest_discovered_arm_registration_on(env, tmp_path):
    p = register(env)
    store = Store(tmp_path / "data")
    _journal(store, "icm-demo", AR.CONTROL_CHANGED, p.reg.arm_id, pd.Timestamp("2026-11-01", tz="UTC"))   # before since
    _journal(store, "icm-demo", "exploration:sent", p.reg.arm_id, pd.Timestamp("2026-11-24", tz="UTC"))  # not read
    _journal(store, "icm-demo", AR.BRACKET_VOID, p.reg.arm_id, pd.Timestamp("2026-11-24", tz="UTC"), bar="x")
    from goldbot.research.arms import Arm
    arm = Arm.model_validate(json.loads(arm_rows(env)[0].row))
    got = I.integrity_inputs(store, {"icm-demo": ("icm", "XAUUSD")}, [arm], now=SLOT)
    assert got.journal is not None and got.recompute is not None and got.hosts is not None
    assert list(got.journal["action"]) == [AR.BRACKET_VOID]
    assert got.hosts["icm-demo"].journal_why is None                       # coverage reads every row, not only voids
    assert len(got.hosts["icm-demo"].days) == 1
    assert I.integrity_inputs(store, {"icm-demo": ("icm", "XAUUSD")}, [], now=SLOT) == I.NightInputs()


def test_the_night_reads_the_engines_whole_minute_memory():
    """READ_DAYS of minutes before the earliest registration cover the engine's whole 1m memory (about 62 trading
    days), so no learning timeframe's window is read shorter at night than the engine read it."""
    from goldbot.engine.runner import EngineConfig
    assert I.ENGINE_MINUTES == EngineConfig.model_fields["max_bars_in_memory"].default
    assert I.READ_DAYS * 23 * 60 * 5 / 7 >= I.ENGINE_MINUTES
