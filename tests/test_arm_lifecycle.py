"""Build 2 (owner directive 2026-11-18, revised by the quant review of the same day): the kill-only lifecycle per demo
arm, goldbot/research/arm_lifecycle.py, with the rule of docs/research/arm-lifecycle-2026-11-18.md: "RETIRE when the
evidence says the arm is worse than its twin, or the existing kill rules fire ... otherwise HOLD", by the replay-parity
monitor's e-process on the excess of the arm's replayed candidates over its matched replay twin, the day terms frozen
at their first read, every decision journalled, a RETIRE applied through the engine's retirement. No promotion, no
size change. Synthetic labels only; nothing here is market data."""
from __future__ import annotations

import json
import typing
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.db import Database
from goldbot.research import arm_lifecycle as L
from goldbot.research.arms import Arm, load_arms

ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-11-17"
NOW = pd.Timestamp("2026-11-18 06:25", tz="UTC")
SETTLED = pd.Timestamp("2026-12-01", tz="UTC")             # every day of the synthetic reads has settled (B5)
SCOPE = "icm:15m-30m"
PASS = {SCOPE: L.ParityGap(scope=SCOPE, status="PASS", mean_gap_r=0.03)}


def _arm(arm_id: str = "A1", role: str = "strategy", **params: Any) -> Arm:
    return Arm(id=arm_id, rule="random_bracket", timeframe="15m", role=role,  # type: ignore[arg-type]
               params={"stop_usd": 3.0, "target_r": 1.0, "hold_bars": 4, **params})


def _obs(arm_r: float, ctrl_r: float = 0.0, *, days: int = 30, per_day: int = 4, seed: int = 1,
         gap: float = 0.0, arm_id: str = "A1", control: str = "C1", hours: tuple[int, ...] = (9, 14),
         taken_share: float = 0.5, end: str = DAY) -> pd.DataFrame:
    """`days` session days ending on `end`: `per_day` arm candidates a day (replay R ~ N(arm_r, 1); the first
    `taken_share` of them filled at replay + `gap`) and twice as many control candidates (replay R ~ N(ctrl_r, 1)),
    in `hours` UTC cells, both sides."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in pd.date_range(end=end, periods=days, freq="D"):
        at = d.tz_localize("UTC")
        for i in range(per_day):
            h, side = hours[i % len(hours)], 1 if (i // len(hours)) % 2 == 0 else -1
            sim = float(rng.normal(arm_r, 1.0))
            taken = i < per_day * taken_share
            rows.append({"arm_id": arm_id, "taken": taken, "label_sim_r": sim, "side": side,
                         "label_fill_r": sim + gap if taken else np.nan,
                         "decision_utc": at + pd.Timedelta(hours=h), "session_day": f"{d:%Y-%m-%d}"})
        for i in range(2 * per_day):
            h, side = hours[i % len(hours)], 1 if (i // len(hours)) % 2 == 0 else -1
            rows.append({"arm_id": control, "taken": False, "label_fill_r": np.nan, "side": side,
                         "label_sim_r": float(rng.normal(ctrl_r, 1.0)),
                         "decision_utc": at + pd.Timedelta(hours=h), "session_day": f"{d:%Y-%m-%d}"})
    return pd.DataFrame(rows)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database.open(str(tmp_path), "aux")


def _read(db: Database, obs: pd.DataFrame, arm: Arm | None = None, parity: dict[str, L.ParityGap] | None = None,
          control: str | None = "C1", day: str = DAY, now: pd.Timestamp = SETTLED) -> L.ArmRead:
    return L.read_arm(db, obs, arm or _arm(), broker="icm", control=control, accounts={"icm-probe"},
                      parity=PASS if parity is None else parity, cap=5.0, day=day, now=now)


# ---------------------------------------------------------------- the twin and the figures
def test_the_twin_is_the_controls_replay_mean_in_the_same_hour_and_side_cell_up_to_the_session_day():
    cands = pd.DataFrame({"decision_utc": pd.to_datetime(["2026-11-02 09:15", "2026-11-03 09:15", "2026-11-03 14:00",
                                                          "2026-11-03 09:20"], utc=True),
                          "session_day": ["2026-11-02", "2026-11-03", "2026-11-03", "2026-11-03"],
                          "side": [1, 1, 1, -1]})
    ctrl = pd.DataFrame({"decision_utc": pd.to_datetime(["2026-11-02 09:40", "2026-11-03 09:05", "2026-11-04 09:05",
                                                         "2026-11-03 09:10"], utc=True),
                         "session_day": ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-03"],
                         "side": [1, 1, 1, -1], "label_sim_r": [1.0, 3.0, 100.0, -7.0]})
    got = L.twin_means(cands, ctrl)
    assert got[0] == 1.0 and got[1] == 2.0           # point in time: the 11-04 label is never read
    assert np.isnan(got[2])                           # no control label at 14:00: unmatched
    assert got[3] == -7.0                             # the short side reads the short cell only


def test_the_excess_is_read_on_every_replayed_candidate_taken_or_refused_never_shifted_by_the_fill_gap(db):
    r = _read(db, _obs(0.2, 0.0, gap=0.5))
    assert r.n_cands == 120 and r.n_fills == 60 and r.n_excess == 120 and r.n_days == 30 and r.validated
    assert r.gap_r == pytest.approx(0.5)              # the fill gap is reported
    obs = _obs(0.2, 0.0, gap=0.5)
    twin = L.twin_means(obs[obs["arm_id"] == "A1"].reset_index(drop=True), obs[obs["arm_id"] == "C1"])
    assert r.excess_r is not None and abs(r.excess_r - 0.2) < 0.3     # ~ arm 0.2 - control 0.0, the gap left out
    assert np.isfinite(twin).all()


@pytest.mark.parametrize("parity,why", [
    ({}, "not read"),
    ({SCOPE: L.ParityGap(scope=SCOPE, status="INSUFFICIENT")}, "INSUFFICIENT"),
    ({SCOPE: L.ParityGap(scope=SCOPE, status="FIRE", mean_gap_r=0.1, revoked=True)}, "revoked"),
])
def test_an_unvalidated_twin_is_never_evidence_and_the_arm_holds(db, parity, why):
    r = L.decide(_read(db, _obs(-1.0), parity=parity))
    assert not r.validated and why in r.reason and r.decision == "HOLD"
    assert r.e_worse > L.E_RETIRE                     # the evidence is there, but the twin is not validated


def test_too_few_candidates_or_days_hold(db):
    r = L.decide(_read(db, _obs(-1.0, days=8)))
    assert r.decision == "HOLD" and "insufficient" in r.reason


def test_a_read_with_most_candidates_in_cells_without_a_control_label_is_not_readable(db):
    obs = _obs(-1.0, per_day=8)
    arm = obs.index[obs["arm_id"] == "A1"]
    obs.loc[arm[arm % 4 != 0], "decision_utc"] += pd.Timedelta(hours=2)      # 3 in 4 to 11h/16h: no control there
    r = L.decide(_read(db, obs))
    assert r.decision == "HOLD" and "not readable" in r.reason and (r.unmatched_share or 0) > L.MAX_UNMATCHED


# ---------------------------------------------------------------- the decisions
def test_an_arm_clearly_worse_than_its_twin_is_retired(db):
    r = L.decide(_read(db, _obs(-0.8, 0.0)))
    assert r.decision == "RETIRE" and r.e_worse >= L.E_RETIRE and "worse than its replay twin" in r.reason


def test_an_arm_no_different_from_its_twin_holds(db):
    r = L.decide(_read(db, _obs(0.0, 0.0, seed=3)))
    assert r.decision == "HOLD" and r.e_worse < L.E_RETIRE


def test_an_arm_clearly_better_than_its_twin_only_holds_with_its_e_better_reported_not_evidence(db):
    r = L.decide(_read(db, _obs(1.0, 0.0)).model_copy(update={"net_gbp": 50.0}))
    assert r.decision == "HOLD" and r.e_better >= L.E_RETIRE and "reported, not evidence" in r.reason
    assert typing.get_args(L.Decision) == ("HOLD", "RETIRE")                              # no SIZE_UP at all


def test_a_kill_rule_retires_whatever_the_evidence(db):
    r = L.decide(_read(db, _obs(1.0)).model_copy(update={"kill": "learning_loss_cap 25 reached"}))
    assert r.decision == "RETIRE" and "kill rule" in r.reason


def test_no_matched_control_holds(db):
    r = L.decide(_read(db, _obs(-1.0), control=None))
    assert r.decision == "HOLD" and "no matched control" in r.reason


# ---------------------------------------------------------------- the frozen path
def test_adding_later_rows_never_changes_a_journalled_day_term(db):
    first = _read(db, _obs(-0.3, seed=7, days=20, end="2026-11-07"), day="2026-11-07")
    with db.connection() as conn:
        before = conn.execute("SELECT day, term, n FROM arm_lifecycle_terms ORDER BY day").fetchall()
    # late labels on an earlier day and a later control row in the same cells arrive after the first read
    obs = pd.concat([_obs(-0.3, seed=7, days=20, end="2026-11-07"),
                     _obs(5.0, seed=8, days=1, end="2026-11-03"),
                     _obs(-0.3, seed=9, days=10, end=DAY)], ignore_index=True)
    second = _read(db, obs)
    with db.connection() as conn:
        after = conn.execute("SELECT day, term, n FROM arm_lifecycle_terms ORDER BY day").fetchall()
    assert after[:len(before)] == before and len(after) == len(before) + 10
    assert second.n_days == 30 and first.n_days == 20
    with pytest.raises(Exception, match="append-only"), db.write() as conn:
        conn.execute("UPDATE arm_lifecycle_terms SET term = 0")


def test_the_e_process_reads_the_stored_terms_only(db):
    r1 = _read(db, _obs(-0.8))
    r2 = _read(db, _obs(3.0, seed=11))               # the same days read again with other labels: nothing moves
    assert (r2.e_worse, r2.excess_r, r2.n_days) == (r1.e_worse, r1.excess_r, r1.n_days)


# ---------------------------------------------------------------- which arms, and what is applied
def test_controls_cost_arms_and_owner_experiments_are_never_decided_and_red_box_arms_only_by_kill_rules(db):
    arms = {a.id: a for a in load_arms(ROOT / "config" / "exploration_arms.yaml", check_hash=False)}
    for i in ("RB15", "RB1H", "RB4H"):
        assert L.fixed_window(arms[i]), i
    assert not any(L.decided(a) for a in arms.values() if a.role in ("control", "cost", "owner_experiment"))
    assert L.decided(_arm()) and not L.fixed_window(_arm(end_utc="2026-11-22T22:00:00Z"))
    rb = L.decide(_read(db, _obs(-1.0, arm_id="RB15"), arm=arms["RB15"]))
    assert rb.decision == "HOLD" and "fixed-window" in rb.reason
    killed = L.decide(rb.model_copy(update={"kill": "learning_expired after 2026-11-22"}))
    assert killed.decision == "RETIRE"


def test_run_journals_every_decision_and_applies_a_retire_through_the_engines_retirement(db, monkeypatch):
    obs = pd.concat([_obs(-0.8, arm_id="BAD"), _obs(0.0, seed=4, arm_id="OK", control="C2")], ignore_index=True)
    monkeypatch.setattr(L, "read_obs", lambda *a, **k: obs)
    reads = L.run(db, hosts={"icm-probe": "icm"}, arms=[_arm("BAD"), _arm("OK")],
                  controls={"BAD": "C1", "OK": "C2"}, caps={}, day=DAY, now=SETTLED, parity=PASS)
    assert {r.arm_id: r.decision for r in reads} == {"BAD": "RETIRE", "OK": "HOLD"}
    assert {r.arm_id: (r.decision, r.applied) for r in L.reads_of(db, DAY)} == {"BAD": ("RETIRE", True),
                                                                                 "OK": ("HOLD", False)}
    with db.connection() as conn:
        rows = conn.execute("SELECT broker, arm_id, status, rule FROM exploration_arm_status").fetchall()
    assert [(b, a, s) for b, a, s, _ in rows] == [("icm", "BAD", "retired")] and "arm lifecycle" in rows[0][3]
    assert L.summary(reads)["retired"] == ["icm:BAD"]


def test_a_retired_decision_is_read_by_the_engine_which_takes_no_new_entry_of_the_arm(tmp_path, monkeypatch):
    from goldbot.engine import exploration
    from tests.test_exploration_engine import CLOSE, _at, _engine
    from tests.test_exploration_engine import _arm as engine_arm
    eng, pb = _engine(tmp_path, [engine_arm("BAD"), engine_arm("OK")])
    monkeypatch.setattr(L, "read_obs", lambda *a, **k: _obs(-0.8, arm_id="BAD"))
    L.run(Database.open(str(tmp_path), "aux"), hosts={"icm-probe": "icm"}, arms=[_arm("BAD")],
          controls={"BAD": "C1"}, caps={}, day=DAY, now=SETTLED, parity=PASS)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert eng.exploration is not None and "BAD" in eng.exploration.retired and "OK" not in eng.exploration.retired
    assert not [t for t in eng.open.values() if t.agent_id == exploration.PREFIX + "BAD"]
    assert [t for t in eng.open.values() if t.agent_id == exploration.PREFIX + "OK"]


def test_the_parity_seam_reads_unvalidated_until_the_monitor_is_deployed(db, monkeypatch):
    import importlib
    real = importlib.import_module

    def missing(name: str, *a: Any) -> Any:
        if name == "goldbot.research.replay_parity":
            raise ModuleNotFoundError(name)
        return real(name, *a)
    monkeypatch.setattr(importlib, "import_module", missing)
    assert L.parity_gaps(db, ["icm"], before=NOW) == {}


def test_the_job_is_scheduled_after_the_daily_review():
    from goldbot.config import load_settings
    from goldbot.ops.jobs import JOBS
    s = load_settings()
    assert "arm_lifecycle" in JOBS and s.research.arm_lifecycle.enabled
    assert s.scheduler.arm_lifecycle.at > s.scheduler.daily_review.at


def test_the_job_reads_every_good_registry_row_when_one_row_is_bad(tmp_path, monkeypatch):
    """R9: one bad hot-loaded row is left out alone."""
    from goldbot.ops import jobs
    from tests.test_arm_registry import _hot_row, _register
    _register(tmp_path)
    _register(tmp_path, _hot_row("D2610-2", config_hash="0" * 16))
    _register(tmp_path, _hot_row("D2610-3"))
    rows = jobs.registry_rows(Database.open(str(tmp_path), "aux"))
    assert [r.arm_id for r in rows] == ["D2610-1", "D2610-2", "D2610-3"]
    seen: dict[str, Any] = {}

    def run(db: Any, **kw: Any) -> list[Any]:
        seen.update(kw)
        return []
    monkeypatch.setattr(L, "run", run)
    from goldbot.config import load_settings
    from goldbot.ops.accounts import load_accounts
    ctx = type("Ctx", (), {"settings": load_settings(), "state_dir": tmp_path,
                           "accounts": list(load_accounts(ROOT / "config" / "accounts.yaml", logins=False).values())})()
    jobs.arm_lifecycle(ctx, NOW)
    ids = {a.id for a in seen["arms"]}
    assert {"D2610-1", "D2610-3"} <= ids and "D2610-2" not in ids


def test_a_read_a_newer_release_wrote_with_an_extra_field_still_reads(db):
    """ADR 0010: the journal is read tolerantly (a rollback reads a newer release's rows)."""
    body = L.ArmRead(day=DAY, broker="icm", arm_id="A1", role="strategy", tf="15m").model_dump()
    with db.write() as conn:
        conn.execute("INSERT INTO arm_lifecycle_reads (day, broker, arm_id, decision, recorded_ns, body) "
                     "VALUES (?, 'icm', 'A1', 'HOLD', 0, ?)", (DAY, json.dumps({**body, "new": 1})))
    [r] = L.reads_of(db, DAY)
    assert r.arm_id == "A1" and r.decision == "HOLD"


# ---------------------------------------------------------------- B5: a day freezes only once its labels have settled
D5 = "2026-11-12"                                                       # a Thursday


def _late(obs: pd.DataFrame) -> pd.DataFrame:
    """`obs` with D5's first arm candidate not labelled yet."""
    out = obs.copy()
    i = out.index[(out["arm_id"] == "A1") & (out["session_day"] == D5)][0]
    out.loc[i, "label_sim_r"] = np.nan
    return out


def _stored(db: Database) -> dict[str, tuple[Any, ...]]:
    with db.connection() as conn:
        rows = conn.execute("SELECT day, term, n, n_unlabelled, void FROM arm_lifecycle_terms").fetchall()
    return {r[0]: r[1:] for r in rows}


def test_a_day_is_not_frozen_before_its_labels_settle_and_a_label_arriving_within_the_lag_is_in_its_term(db):
    full = _obs(-0.3, days=15, end=D5, seed=21)
    due = L.settled_at(D5, L.arm_hold(_arm()))
    assert due == pd.Timestamp("2026-11-12 22:00", tz="UTC") + pd.Timedelta(hours=1) + L.SETTLE_PAD
    _read(db, _late(full), day=D5, now=due - pd.Timedelta(minutes=1))
    assert D5 not in _stored(db) and "2026-11-11" in _stored(db)          # not before the lag has passed
    _read(db, full, day=D5, now=due)                                      # the late label has arrived meanwhile
    days = L.day_terms(full, _arm(), control="C1", day=D5)[0]
    want: Any = days.loc[days["day"] == D5, "term"].iloc[0]
    term, n, unlabelled, void = _stored(db)[D5]
    assert (n, unlabelled, void) == (4, 0, 0) and term == pytest.approx(float(want))


def test_a_settled_day_with_more_than_5_percent_unlabelled_is_stored_void_and_never_read(db):
    r = _read(db, _late(_obs(-0.3, days=15, end=D5, seed=21)), day=D5)
    term, n, unlabelled, void = _stored(db)[D5]
    assert term is None and unlabelled == 1 and void == 1
    assert r.n_days == 14                                                 # the void day is not a term


def test_a_hold_that_reaches_the_weekend_waits_for_it_and_a_longer_candidate_hold_is_used(db):
    friday = L.settled_at("2026-11-13", pd.Timedelta(hours=1))
    assert friday == pd.Timestamp("2026-11-13 22:00", tz="UTC") + pd.Timedelta(hours=1, days=2) + L.SETTLE_PAD
    obs = _obs(-0.3, days=15, end=D5, seed=21).assign(max_hold_s=3 * 86400)
    _read(db, obs, day=D5, now=L.settled_at(D5, pd.Timedelta(hours=1)))
    assert D5 not in _stored(db)                                          # its candidates hold 3 days


def test_an_arm_without_a_hold_on_its_row_or_spec_waits_the_default_and_a_spec_time_exit_is_read():
    from goldbot.research.autodisc.gen_combine import family_spec
    from goldbot.research.autodisc.spec import ExitTemplate, canonical_json, spec_hash

    def spec_arm(target: Any, bars: int | None) -> Arm:
        sp = family_spec("box_break_close", "1h", None, ExitTemplate(stop="atr_1.0", target=target, time_bars=bars))
        return Arm.model_construct(id="D1", rule="spec_rule", timeframe="1h",   # a time exit never runs live
                   params={"spec": canonical_json(sp), "spec_hash": spec_hash(sp), "prereg": "docs/x.md#d1"})
    assert L.arm_hold(spec_arm("2R", None)) == L.SETTLE_DEFAULT
    assert L.arm_hold(spec_arm("time", 6)) == pd.Timedelta(hours=6)
    assert L.arm_hold(_arm()) == pd.Timedelta(hours=1)


# ---------------------------------------------------------------- S1: the kill rules read the broker's accounts only
def test_a_kill_rule_reads_only_the_accounts_of_the_broker_it_retires_for(db):
    from goldbot.engine import exploration
    learn = Arm(id="LRN", rule="random_bracket", timeframe="15m", role="learning",
                params={"stop_usd": 3.0, "target_r": 1.0, "hold_bars": 4},
                learning={"prereg": "docs/x.md", "k_id": "0" * 12, "control": "C1", "loss_cap_gbp": 10.0,
                          "until": "2026-12-31", "since": "2026-11-01"})
    t = pd.Timestamp("2026-11-05 10:00", tz="UTC")
    with db.write() as conn:
        for i in range(3):                                                # a £15 loss on the vantage account only
            exploration.LEDGER.upsert(conn, exploration.ExplorationRecord(
                client_order_id=f"v-{i}", account_id="vantage-demo", arm_id="LRN", family="x", tf="15m", side=1,
                lots=0.01, signal_utc=t, sent_utc=t, atr_usd=3.0, stop_usd=3.0, target_usd=3.0, hold_s=3600,
                fill_price=2000.0, exit_utc=t + pd.Timedelta(hours=1), pnl=-5.0, censored=False))
    reads = L.run(db, hosts={"icm-probe": "icm", "vantage-demo": "vantage"}, arms=[learn], controls={},
                  caps={}, day=DAY, now=SETTLED, parity={})
    by = {r.broker: r for r in reads}
    assert by["icm"].kill is None and by["icm"].decision == "HOLD"
    assert by["vantage"].kill is not None and by["vantage"].decision == "RETIRE"
    with db.connection() as conn:
        assert conn.execute("SELECT broker FROM exploration_arm_status").fetchall() == [("vantage",)]


# ---------------------------------------------------------------- discovered learning arms (amendment 2026-11-18, 5.1)
# docs/research/prereg-amendment-2026-11-18-discovered-arms.md 5.1 (5): "A refused bracket (section 4) on 3 or more
# candidates, or on any taken candidate" retires; section 4: a void candidate "is void for the read"; section 2.2:
# after a control change "its reads are void from the change onward, and it retires".
from goldbot.engine import arm_registry as AR  # noqa: E402
from goldbot.research import arm_rules_spec as S  # noqa: E402

PASS_1H = {"icm:1h-4h": L.ParityGap(scope="icm:1h-4h", status="PASS", mean_gap_r=0.03)}
GOOD = S.Bracket(stop_dist=3.0, target_dist=6.0, max_hold_s=7200)


def _disc(arm_id: str = "D1") -> Arm:
    return Arm.model_construct(
        id=arm_id, rule="spec_rule", timeframe="1h", role="learning", status="run",
        params={"per_trade_bracket": S.BRACKET_PREFIX + "abcdef012345", "hold_bars": 2,
                "prereg": S.DISCOVERED_PREREG + "#d1"},
        learning={"prereg": S.DISCOVERED_PREREG, "k_id": "0" * 12, "control": "C1", "loss_cap_gbp": 60.0,
                  "until": "2026-12-31", "since": "2026-10-01"})


def _disc_obs(obs: pd.DataFrame, arm_id: str = "D1") -> pd.DataFrame:
    """`obs` with the columns a candidate row carries: its account, id, the bracket in its INSERT (GOOD) recorded 1 s
    after the decision, and its entry 2 s after it when taken; its short candidates 30 min into the hour (one candidate
    of an arm a decision bar, as live)."""
    out = obs.reset_index(drop=True).copy()
    out.loc[(out["arm_id"] == arm_id) & (out["side"] == -1), "decision_utc"] += pd.Timedelta(minutes=30)
    dec = out["decision_utc"]
    out["account_id"] = "icm-probe"
    out["candidate_id"] = [f"c{i}" for i in range(len(out))]
    out["stop_dist"], out["target_dist"], out["max_hold_s"] = GOOD.stop_dist, GOOD.target_dist, GOOD.max_hold_s
    out["recorded_ns"] = [int(t.value) + 1_000_000_000 for t in dec]
    out["entry_utc"] = [t + pd.Timedelta(seconds=2) if k else pd.NaT for t, k in zip(dec, out["taken"], strict=True)]
    return out


def _journal(*rows: tuple[str, str, pd.Timestamp, dict[str, Any]]) -> pd.DataFrame:
    """Journal rows as the store's `decisions` table holds them (action, agent, ts_utc, detail JSON)."""
    cols = ["ts_utc", "account_id", "agent_id", "action", "detail"]
    return pd.DataFrame([{"ts_utc": ts, "account_id": "icm-probe", "agent_id": "explore:" + arm, "action": action,
                          "detail": json.dumps({"arm": arm, **detail})} for action, arm, ts, detail in rows],
                        columns=cols)


def _dread(db: Database, obs: pd.DataFrame, journal: pd.DataFrame | None = None, **kw: Any) -> L.ArmRead:
    return L.decide(L.read_arm(db, obs, _disc(), broker="icm", control="C1", accounts={"icm-probe"},
                               parity=PASS_1H, cap=5.0, day=DAY, now=SETTLED,
                               journal=_journal() if journal is None else journal,
                               recompute=kw.get("recompute", lambda arm, row: GOOD)))


def _arm_rows(obs: pd.DataFrame, taken: bool) -> pd.Index:
    return obs.index[(obs["arm_id"] == "D1") & (obs["taken"] == taken)]


def test_a_clean_discovered_arm_reads_exactly_as_any_arm(db, tmp_path):
    obs = _disc_obs(_obs(-0.8, arm_id="D1"))
    r = _dread(db, obs)
    plain = L.decide(L.read_arm(Database.open(str(tmp_path / "other"), "aux"), obs, _arm("D1"), broker="icm",
                                control="C1", accounts={"icm-probe"}, parity=PASS_1H, cap=5.0, day=DAY,
                                now=SETTLED))
    assert L.discovered(_disc()) and not L.discovered(_arm("D1"))
    assert r.n_bracket_void == 0 and r.void_from is None
    assert (r.n_cands, r.n_days, r.e_worse, r.decision) == (plain.n_cands, plain.n_days, plain.e_worse, "RETIRE")
    assert "worse than its replay twin" in r.reason


def test_a_bracket_void_candidate_is_excluded_from_the_read_and_two_refusals_do_not_retire(db):
    obs = _disc_obs(_obs(0.0, seed=3, arm_id="D1"))
    off, journalled = _arm_rows(obs, taken=False)[:2]
    obs.loc[off, "stop_dist"] = GOOD.stop_dist + 1e-6                      # not the recompute: void
    bar = obs.loc[journalled, "decision_utc"]
    r = _dread(db, obs, _journal((AR.BRACKET_VOID, "D1", bar + pd.Timedelta(seconds=2),
                                  {"bar": bar.isoformat(), "why": "bracket_mismatch"})))
    assert r.n_cands == 118 and r.n_bracket_void == 2                     # 120 replayed, both void ones left out
    assert r.decision == "HOLD" and "bracket" not in (r.kill or "")
    with db.connection() as conn:
        n = conn.execute("SELECT SUM(n) FROM arm_lifecycle_terms WHERE arm_id = 'D1'").fetchone()[0]
    assert n == 118


def test_three_refused_brackets_on_candidates_retire_the_arm_journalled_with_its_reason(db, monkeypatch):
    obs = _disc_obs(_obs(0.0, seed=3, arm_id="D1"))
    for i in _arm_rows(obs, taken=False)[:3]:
        obs.loc[i, "recorded_ns"] = int(obs.loc[i, "decision_utc"].value) + 61_000_000_000     # bracket_late
    monkeypatch.setattr(L, "read_obs", lambda *a, **k: obs)
    [r] = L.run(db, hosts={"icm-probe": "icm"}, arms=[_disc()], controls={}, caps={}, day=DAY, now=SETTLED,
                parity=PASS_1H, journal=_journal(), recompute=lambda arm, row: GOOD)
    assert r.decision == "RETIRE" and r.n_bracket_void == 3 and "bracket integrity" in r.reason
    with db.connection() as conn:
        rows = conn.execute("SELECT arm_id, rule FROM exploration_arm_status").fetchall()
    assert rows[0][0] == "D1" and "bracket integrity" in rows[0][1]
    assert "bracket integrity" in L.reads_of(db, DAY)[0].reason


def test_one_refused_bracket_on_a_taken_candidate_retires_the_arm(db):
    obs = _disc_obs(_obs(0.0, seed=3, arm_id="D1"))
    i = _arm_rows(obs, taken=True)[0]
    obs.loc[i, "recorded_ns"] = int(obs.loc[i, "entry_utc"].value)                      # at the entry: late
    r = _dread(db, obs)
    assert r.decision == "RETIRE" and r.n_bracket_void == 1 and "taken" in r.reason


def test_an_unverifiable_recompute_voids_the_candidate_but_is_not_a_refusal(db):
    """No bars at night (the job's store) is not the arm's fault: the candidates are void for the read (fail closed)
    and the arm holds, never retired on it."""
    r = _dread(db, _disc_obs(_obs(-0.8, arm_id="D1")), recompute=lambda arm, row: None)
    assert r.n_cands == 0 and r.n_bracket_void == 0 and r.decision == "HOLD"


def test_every_read_from_a_control_change_on_is_void_and_the_arm_retires(db):
    obs = _disc_obs(_obs(1.0, arm_id="D1"))
    at = pd.Timestamp("2026-11-08 03:00", tz="UTC")
    r = _dread(db, obs, _journal((AR.CONTROL_CHANGED, "D1", at, {"control": "C1", "why": "changed after registration"})))
    assert r.decision == "RETIRE" and "control changed" in r.reason and not r.validated
    assert r.void_from == at.isoformat()
    with db.connection() as conn:
        days = [d for (d,) in conn.execute("SELECT day FROM arm_lifecycle_terms WHERE arm_id = 'D1'")]
    assert days and max(days) < "2026-11-08"                            # nothing from the change's session day on
    again = _dread(db, obs, _journal((AR.CONTROL_CHANGED, "D1", at, {"control": "C1"})))
    assert again.decision == "RETIRE" and again.n_days == r.n_days      # the next night's read is void too


def test_a_discovered_arm_read_without_the_journal_or_a_recompute_is_never_evidence(db):
    obs = _disc_obs(_obs(-0.8, arm_id="D1"))
    r = L.decide(L.read_arm(db, obs, _disc(), broker="icm", control="C1", accounts={"icm-probe"}, parity=PASS_1H,
                            cap=5.0, day=DAY, now=SETTLED))
    assert r.decision == "HOLD" and not r.validated and "bracket integrity unverified" in r.reason
