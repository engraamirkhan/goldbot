"""ML review 2026-11-13 (docs/research/ml-review-2026-11-13.md), lane ML1, items 1 and 2: the Thompson bandit (H-61)
gives way to equal shares among the enterable arms inside the same floor and caps, the H-62 online meta-labeller and
the specialists' Saturday LightGBM retrain are retired (code and data kept, switched off in settings)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.db.connection import Database
from goldbot.ops import jobs
from goldbot.research import exploration_data as ex

pytestmark = pytest.mark.usefixtures("unknown_arms_are_gold")     # made-up arm ids read as gold (tests/conftest.py)
T0 = pd.Timestamp("2026-10-05 08:00", tz="UTC")


def _db(tmp_path: Path) -> Database:
    return Database.open(tmp_path, "aux")


def _evidence(db: Database, arm: str, r: float, n: int = 20) -> None:
    for i in range(n):
        cid = f"{arm}-{i}"
        ex.record_candidate(db, candidate_id=cid, account_id="icm-probe", broker="icm", arm_id=arm, family="vwap",
                            style="intraday", tf="15m", side=1, decision_utc=T0 + pd.Timedelta(hours=i), stop_dist=2.0,
                            target_dist=2.0, max_hold_s=3600, taken=True, mu=0.5, branch="random", u=0.9)
        with db.write() as conn:
            dec = conn.execute("SELECT decision_ns FROM exploration_obs WHERE candidate_id = ?", (cid,)).fetchone()[0]
            conn.execute("UPDATE exploration_obs SET label_sim_r = ?, label_mid_r = ?, label_exit_ns = ?, "
                         "cost_model_version = 'c1' WHERE candidate_id = ?",
                         (r + (0.2 if i % 2 else -0.2), r, dec + 600 * 10**9, cid))


def _detail(db: Database, arm: str) -> dict[str, Any]:
    with db.connection() as conn:
        return dict(json.loads(conn.execute("SELECT detail FROM exploration_broker_weights WHERE arm_id = ?",
                                            (arm,)).fetchone()[0]))


# ---------------------------------------------------------------------------------------------- item 1: allocator
def test_the_learner_gives_every_enterable_arm_an_equal_share_whatever_its_results_by_default(tmp_path):
    """Review section 2 row 3: weighted next-day R +0.085 against equal +0.086, rank correlation 0.003 over 24
    broker-days. A winner, a loser, a flat arm and a cold arm (no fill yet) share alike; an arm not on the broker
    still gets 0."""
    db = _db(tmp_path)
    for arm, r in (("W", 0.6), ("L", -0.6), ("F", 0.0), ("GONE", 0.4)):
        _evidence(db, arm, r)
    arms = {a: "intraday" for a in ("W", "L", "F", "COLD")}
    out = ex.learn(db, broker="icm", as_of=T0 + pd.Timedelta(days=3), arms=arms, learning=(), spec_paths=())
    w = out["weights"]
    assert w["GONE"] == 0.0
    assert w["W"] == w["L"] == w["F"] == w["COLD"] == pytest.approx(0.25)
    assert _detail(db, "W")["method"] == ex.ALLOCATOR_EQUAL == "equal"
    assert ex.weights(db, ["W", "L", "F", "COLD"], broker="icm", now=T0 + pd.Timedelta(days=3)) == pytest.approx(
        {a: 0.25 for a in ("W", "L", "F", "COLD")})


def test_equal_shares_stay_inside_the_thirty_percent_cap_and_an_arms_own_ceiling():
    """The caps are unchanged: two enterable arms hold 30 % each (the rest of the style's pot is unspent, never moved
    onto another arm), and an arm's own ceiling below the cap binds."""
    shares = ex.allocate({"A": [0.1], "B": [-0.1]}, {"A": "scalp", "B": "scalp"}, allocator="equal")
    assert shares == pytest.approx({"A": ex.CAP_SHARE, "B": ex.CAP_SHARE})
    capped = ex.allocate({"A": [0.1], "B": [0.1], "C": [], "D": [0.2]}, dict.fromkeys("ABCD", "scalp"),
                         allocator="equal", ceilings={"A": 0.1}, cold={"C"})
    assert capped["A"] == pytest.approx(0.1) and all(capped[a] == pytest.approx(ex.CAP_SHARE) for a in "BCD")
    assert sum(capped.values()) <= 1 + 1e-9


def test_the_thompson_bandit_stays_selectable_and_still_favours_the_winner(tmp_path):
    db = _db(tmp_path)
    results = {"W": 0.6, "L": -0.6, "F1": 0.0, "F2": 0.0, "F3": 0.0, "F4": 0.0}
    for arm, r in results.items():
        _evidence(db, arm, r)
    out = ex.learn(db, broker="icm", as_of=T0 + pd.Timedelta(days=3), arms=dict.fromkeys(results, "intraday"),
                   learning=(), spec_paths=(), allocator="thompson")
    w = out["weights"]
    assert w["W"] == pytest.approx(ex.CAP_SHARE) and w["W"] > w["L"]
    assert _detail(db, "W")["method"] == "thompson_nig_net_r_within_style"


def test_an_unknown_allocator_is_refused():
    with pytest.raises(ValueError, match="allocator"):
        ex.allocate({"A": [0.1]}, {"A": "scalp"}, allocator="greedy")      # type: ignore[arg-type]


def test_settings_ship_equal_shares_with_the_meta_labeller_and_the_specialist_retrain_off():
    r = load_settings().research
    assert r.exploration.allocator == "equal"
    assert r.exploration.meta_labeller is False
    assert r.specialist_retrain is False
    assert r.exploration.feature_snapshots is True and r.exploration.snapshot_max_decisions > 0


# ---------------------------------------------------------------------------------------------- item 2: H-62
@pytest.mark.parametrize(("allocator", "meta", "day", "retrain"), [
    ("equal", False, "2026-10-11", False),        # a Sunday, the meta-labeller retired: no retrain
    ("equal", True, "2026-10-11", True),          # switched back on: Sundays only, as before
    ("thompson", True, "2026-10-12", False),
])
def test_the_nightly_learner_reads_the_allocator_and_the_meta_labeller_switch_from_settings(allocator, meta, day,
                                                                                           retrain):
    s = load_settings()
    s = s.model_copy(update={"research": s.research.model_copy(update={
        "exploration": s.research.exploration.model_copy(update={"allocator": allocator, "meta_labeller": meta})})})
    opts = jobs.learner_options(s, pd.Timestamp(f"{day} 23:10", tz="UTC"))
    assert opts == {"allocator": allocator, "retrain": retrain}


# ---------------------------------------------------------------------------------------------- item 2: specialists
class _Pop:
    def __init__(self, member: Any) -> None:
        self.member, self.saved = member, False

    def ensure_founders(self, slot: Any) -> None:
        pass

    def active(self, *kinds: str) -> list[Any]:
        return [self.member]

    def save(self, path: Any) -> None:
        self.saved = True


def _retrain_ctx(tmp_path: Path, enabled: bool) -> Any:
    s = load_settings()
    s = s.model_copy(update={"research": s.research.model_copy(update={"specialist_retrain": enabled})})
    spec = SimpleNamespace(timeframe="1h", barriers_on_1m=False)
    member = SimpleNamespace(agent_id="tsmom-1h", family="tsmom", specialist=lambda: spec)
    return SimpleNamespace(settings=s, sync_bars=None, population=_Pop(member), state_dir=tmp_path,
                           models=SimpleNamespace(by_agent=lambda agent, kind: []))


def test_the_saturday_job_trains_no_new_challenger_once_the_specialist_retrain_is_retired(tmp_path, monkeypatch):
    """Review section 2 row 1: shadow AUC 0.44 on 145 closed trades, Brier 0.206 against 0.197 for the base rate, 0
    live agents, retrain stalled 25 days. The job still judges challengers already in shadow and saves the
    population; it fits nothing new."""
    decided: list[str] = []

    def decide(ctx: Any, agent: str, slot: Any) -> list[Any]:
        decided.append(agent)
        return []
    monkeypatch.setattr(jobs, "_decide_challengers", decide)

    def no_fit(*a: Any, **k: Any) -> None:
        raise AssertionError("a retired retrain must not walk forward")
    monkeypatch.setattr(jobs, "_walk_forward", no_fit)
    ctx = _retrain_ctx(tmp_path, enabled=False)
    out = jobs.saturday_retrain(ctx, pd.Timestamp("2026-11-14 06:00", tz="UTC"))
    assert out["tsmom-1h"]["retrain"].startswith("skipped: specialist retrain retired")
    assert decided == ["tsmom-1h"] and ctx.population.saved


def test_the_saturday_job_still_walks_forward_when_the_retrain_is_switched_back_on(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_decide_challengers", lambda ctx, agent, slot: [])
    calls: list[Any] = []
    monkeypatch.setattr(jobs, "_walk_forward", lambda ctx, spec, slot, months: calls.append(spec))
    out = jobs.saturday_retrain(_retrain_ctx(tmp_path, enabled=True), pd.Timestamp("2026-11-14 06:00", tz="UTC"))
    assert len(calls) == 1 and out["tsmom-1h"]["retrain"] == "no model: not enough bars or candidates"
