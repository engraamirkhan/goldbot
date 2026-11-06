"""The nightly learner weights only arms that can actually enter on the broker (PD 2026-11-06: its top weights, 0.3,
sat on retired or non-trading arms PDC4, DON4, VW1A, S4 and never steered an entry). A retired arm (its spec row or the
broker's `exploration_arm_status`), a blocked or refused one (spec status, ADR 0061 qualification), an arm with no
labelled fill in the evidence and an arm not configured on the broker get 0; the shares renormalise over the rest."""
from __future__ import annotations

import pandas as pd
import pytest

from goldbot.db.connection import Database
from goldbot.research import exploration_data as ex
from tests.test_exploration_data import T0, _cand, _label

pytestmark = pytest.mark.usefixtures("unknown_arms_are_gold")     # made-up arm ids read as gold (tests/conftest.py)
AS_OF = T0 + pd.Timedelta(days=6)
LIVE = ("S1a", "S1b", "S5a", "S5b")                     # made-up strategy arms: unknown to the spec, free to enter


def _evidence(db: Database, arms: dict[str, float]) -> None:
    """40 labelled fills of each arm at mean net R `arms[a]`."""
    for i in range(40):
        d = T0 + pd.Timedelta(hours=3 * i)
        for arm, r in arms.items():
            _cand(db, f"{arm}-{i}", arm=arm, decision=d)
            _label(db, f"{arm}-{i}", r + (0.3 if i % 2 else -0.3))


def _learn(db: Database, configured: dict[str, str], **kw: object) -> dict[str, float]:
    rep = ex.learn(db, broker="icm", as_of=AS_OF, arms=configured, seed=1, **kw)  # type: ignore[arg-type]
    return rep["weights"]


def test_a_retired_arm_gets_zero_weight_however_good_its_evidence_and_the_rest_renormalise(tmp_path):
    """S4 (spec row retired 2026-10-30, A2 readability; a retirement is never undone) had the best mean R."""
    db = Database.open(tmp_path, "aux")
    _evidence(db, {"S4": 2.0, "S1a": 0.5, "S1b": -0.5, "S5a": 0.0, "S5b": 0.1})
    w = _learn(db, {a: "scalp" for a in ("S4", *LIVE)})
    assert w["S4"] == 0.0
    assert sum(w[a] for a in LIVE) == pytest.approx(1.0) and w["S1a"] == pytest.approx(ex.CAP_SHARE)
    assert all(ex.FLOOR_SHARE - 1e-12 <= w[a] <= ex.CAP_SHARE + 1e-12 for a in LIVE)
    # the engine reads the run as it is: the retired arm at 0, never the equal-shares fallback
    assert ex.weights(db, ["S4", *LIVE], broker="icm", now=AS_OF) == w
    with db.connection() as conn:
        detail = conn.execute("SELECT detail FROM exploration_broker_weights WHERE arm_id = 'S4'").fetchone()[0]
    assert '"excluded": "retired"' in detail


def test_an_arm_retired_at_its_stop_on_this_broker_gets_zero_and_on_another_broker_keeps_its_weight(tmp_path):
    db = Database.open(tmp_path, "aux")
    _evidence(db, {a: r for a, r in zip(LIVE, (0.5, -0.5, 0.0, 0.1), strict=True)})
    with db.write() as conn:
        conn.execute("INSERT INTO exploration_arm_status (broker, arm_id, status, day, rule, recorded_ns) "
                     "VALUES ('vantage', 'S1b', 'retired', '2026-10-30', 'H-61', 0), "
                     "('icm', 'S1a', 'retired', '2026-10-30', 'H-61', 0)")
    w = _learn(db, {a: "scalp" for a in LIVE})
    assert w["S1a"] == 0.0 and w["S1b"] > 0.0


def test_a_blocked_or_unqualified_spec_arm_gets_zero(tmp_path):
    """S6 (spec status blocked) and S1A (a strategy arm without a passing qualification, ADR 0061) never enter."""
    db = Database.open(tmp_path, "aux")
    _evidence(db, {"S6": 1.0, "S1A": 1.0, "S1a": 0.5, "S1b": -0.5, "S5a": 0.0, "S5b": 0.1})
    w = _learn(db, {a: "scalp" for a in ("S6", "S1A", *LIVE)})
    assert w["S6"] == 0.0 and w["S1A"] == 0.0 and sum(w[a] for a in LIVE) == pytest.approx(1.0)


def test_an_arm_with_no_fill_in_the_evidence_and_an_arm_not_configured_on_the_broker_get_zero(tmp_path):
    db = Database.open(tmp_path, "aux")
    _evidence(db, {"S1a": 0.5, "S1b": -0.5, "S5a": 0.0, "S5b": 0.1, "GONE": 3.0})
    w = _learn(db, {**{a: "scalp" for a in LIVE}, "S9": "scalp"})       # S9 configured, never filled; GONE not here
    assert w["S9"] == 0.0 and w["GONE"] == 0.0 and sum(w[a] for a in LIVE) == pytest.approx(1.0)


def test_a_broker_whose_configured_arms_cannot_enter_still_records_its_run_all_at_zero(tmp_path):
    """The learner ran on real evidence (health `exploration_learning` reads the run), but nothing may enter."""
    db = Database.open(tmp_path, "aux")
    _evidence(db, {"S4": 1.0})
    w = _learn(db, {"S4": "scalp"})
    assert w == {"S4": 0.0}
    assert ex.weights(db, ["S4"], broker="icm", now=AS_OF) == {"S4": 0.0}


def test_entry_exclusions_names_why_each_spec_arm_cannot_enter(tmp_path):
    db = Database.open(tmp_path, "aux")
    got = ex.entry_exclusions(db, ["S4", "S6", "S1A", "S0", "S1a"], broker="icm")
    assert got == {"S4": "retired", "S6": "retired", "S1A": "blocked"}
