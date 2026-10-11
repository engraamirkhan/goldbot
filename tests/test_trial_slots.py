"""Contingent trial slots and withdrawal (ADR 0007): a survivor/follow-up/holdout slot is pre-registered before its
quarter with a deterministic derivation rule, holds the reservation only while its condition can still be met, and is
used by exactly one in-quarter ad-hoc pre-registration that names it (`slot_ref`) and satisfies the rule."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from goldbot.research import registry as registry_mod
from goldbot.research.director import is_queued, pending_preregistration, reserved_trials, slot_state
from goldbot.research.registry import (
    FROM_DISCOVERY_KEY,
    SURVIVOR_KEY,
    TrialBudgetExceeded,
    TrialRegistry,
    is_trial,
    quarter_trials,
)

Q = "2027Q1"
BUDGET = 20
FEB = datetime(2027, 2, 1, tzinfo=timezone.utc)
QUEUED = [("discovery_tsmom", "H-02"), ("tsmom", "H-01"), ("tsmom", "H-06"), ("asia_drift", "H-04"), ("tsmom", "C4H")]
RULE = "the k-th H-02 survivor by recorded stability (results.discovery), its own pre-registered trial"


def _clock(monkeypatch: pytest.MonkeyPatch, y: int, m: int, d: int) -> None:
    monkeypatch.setattr(registry_mod, "_utcnow", lambda: datetime(y, m, d, tzinfo=timezone.utc))


def _pre(reg: TrialRegistry, family: str, config: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return reg.preregister(agent_id="a", family=family, config=config, feature_version="f", rationale="r",
                           reading_rule="rule", **kw)


def _run(reg: TrialRegistry, pre: dict[str, Any], ts: str, status: str = "evaluated",
         results: dict[str, Any] | None = None) -> dict[str, Any]:
    """A trial row linked to `pre`, stamped `ts` (record() stamps the wall clock, so it is appended directly)."""
    row = {"trial": reg.n_trials + 1, "ts": ts, "agent_id": "a", "family": pre["family"],
           "config_hash": pre["config_hash"], "config": pre["config"], "feature_version": "f", "status": status,
           "results": results or {},
           "preregistration": {"trial": pre["trial"], "ts": pre["ts"], "config_hash": pre["config_hash"]}}
    reg._append(row)
    return row


def _discovery_results(survivors: list[str], unit: str = "feature") -> dict[str, Any]:
    stab = {s: round(0.95 - 0.05 * i, 2) for i, s in enumerate(survivors)}
    if unit == "group":
        return {"discovery": {"survivor_unit": "group", "survivor_groups": survivors, "group_stability": stab,
                              "selected": [f"{g}_col" for g in survivors]}}
    return {"discovery": {"survivor_unit": "feature", "selected": survivors, "stability": stab, "survivor_groups": []}}


def _plan(tmp_path: Any, monkeypatch: pytest.MonkeyPatch, n_slots: int = 4,
          kinds: tuple[str, ...] = ("survivor",)) -> tuple[TrialRegistry, list[dict[str, Any]], dict[str, list]]:
    """The Q1 plan as amended: 5 queued rows and `n_slots` contingent slots per kind on H-02, written 2026-12-20."""
    reg = TrialRegistry(tmp_path / "r.jsonl")
    _clock(monkeypatch, 2026, 12, 20)
    queued = [_pre(reg, f, {"h": h}, queue=True, target_quarter=Q) for f, h in QUEUED]
    slots = {kind: [reg.reserve_slot(queued[0], kind, k, derivation_rule=RULE, target_quarter=Q)
                    for k in range(1, n_slots + 1)] for kind in kinds}
    return reg, queued, slots


def _run_queued(reg: TrialRegistry, queued: list[dict[str, Any]], survivors: list[str],
                unit: str = "feature") -> dict[str, Any]:
    """The 5 queued trials run in January; returns H-02's discovery row."""
    disc = _run(reg, queued[0], "2027-01-10T00:00:00+00:00", status="discovery",
                results=_discovery_results(survivors, unit))
    for i, p in enumerate(queued[1:]):
        _run(reg, p, f"2027-01-{11 + i:02d}T00:00:00+00:00")
    return disc


def _survivor_cfg(disc: dict[str, Any], name: str, **extra: Any) -> dict[str, Any]:
    return {"spec": "tsmom-4h", FROM_DISCOVERY_KEY: disc["trial"], SURVIVOR_KEY: name, **extra}


def test_a_slot_row_is_a_queued_preregistration_with_no_config_hash_written_before_its_quarter(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=1)
    s = slots["survivor"][0]
    assert s["status"] == "preregistered" and s["queue"] is True and s["config_hash"] is None
    assert s["slot"]["kind"] == "survivor" and s["slot"]["k"] == 1 and s["slot"]["derivation_rule"] == RULE
    assert s["slot"]["parent"]["ts"] == queued[0]["ts"] and s["target_quarter"] == Q
    assert is_queued(s) and not is_trial(s) and reg.n_trials == 0
    _clock(monkeypatch, 2027, 1, 2)
    with pytest.raises(ValueError, match="before its target quarter starts"):
        reg.reserve_slot(queued[0], "survivor", 2, derivation_rule=RULE, target_quarter=Q)
    with pytest.raises(ValueError, match="kind"):
        reg.reserve_slot(queued[0], "other", 1, derivation_rule=RULE, target_quarter="2027Q2")
    with pytest.raises(ValueError, match="k"):
        reg.reserve_slot(queued[0], "survivor", 0, derivation_rule=RULE, target_quarter="2027Q2")
    with pytest.raises(ValueError, match="parent"):
        reg.reserve_slot({"ts": "2020-01-01T00:00:00+00:00", "config_hash": "nope"}, "survivor", 1,
                         derivation_rule=RULE, target_quarter="2027Q2")


def test_a_survivor_slot_is_used_by_the_kth_survivors_adhoc_preregistration(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch)
    r = reserved_trials(reg._rows(), Q, 9)
    assert (r.run, r.pending, r.reserved) == (0, 9, 9)          # 5 queued + 4 slots held before the quarter
    disc = _run_queued(reg, queued, ["vol_a", "trend_b", "macro_c", "level_d"])
    r = reserved_trials(reg._rows(), Q, 9)
    assert (r.run, r.pending, r.expired, r.reserved) == (5, 4, 0, 4)
    assert slot_state(reg._rows(), slots["survivor"][0]) == "live"
    _clock(monkeypatch, 2027, 2, 1)
    cfg = _survivor_cfg(disc, "vol_a")
    p = _pre(reg, "tsmom", cfg, slot_ref=slots["survivor"][0])
    assert p["queue"] is False and p["slot_ref"]["ts"] == slots["survivor"][0]["ts"]
    # covered even though the open budget is spent: 20 - 5 used - 4 reserved = 11, use it all first
    for i in range(11):
        reg._append({"trial": reg.n_trials + 1, "ts": f"2027-01-2{i % 9}T00:00:00+00:00", "family": "trend",
                     "config_hash": f"o{i}", "status": "evaluated", "config": {}, "results": {}})
    with pytest.raises(TrialBudgetExceeded):
        reg.check_budget_reserved([("trend", {"z": 1})], BUDGET, 9, now=FEB)
    quarter, (match,) = reg.check_budget_reserved([("tsmom", cfg)], BUDGET, 9, now=FEB)
    assert quarter == Q and match == p
    _run(reg, p, "2027-02-02T00:00:00+00:00")
    r = reserved_trials(reg._rows(), Q, 9)
    assert (r.run, r.pending, r.reserved) == (6, 3, 3)
    assert slot_state(reg._rows(), slots["survivor"][0]) == "used"


def test_survivor_slots_are_freed_when_the_parent_records_no_survivors(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch)
    _run_queued(reg, queued, [])
    rows = reg._rows()
    r = reserved_trials(rows, Q, 9)
    assert (r.run, r.pending, r.expired, r.reserved) == (5, 0, 4, 0)
    assert {slot_state(rows, s) for s in slots["survivor"]} == {"expired"}


def test_only_as_many_survivor_slots_stay_live_as_survivors_were_recorded(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch)
    _run_queued(reg, queued, ["g1", "g2"], unit="group")
    rows = reg._rows()
    assert [slot_state(rows, s) for s in slots["survivor"]] == ["live", "live", "expired", "expired"]
    r = reserved_trials(rows, Q, 9)
    assert (r.run, r.pending, r.expired, r.reserved) == (5, 2, 2, 2)


def test_before_the_parent_runs_a_survivor_slot_is_held_but_cannot_be_used(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=1)
    assert slot_state(reg._rows(), slots["survivor"][0]) == "pending"
    _clock(monkeypatch, 2027, 1, 5)
    cfg = {"spec": "x", FROM_DISCOVERY_KEY: 1, SURVIVOR_KEY: "vol_a"}
    _pre(reg, "tsmom", cfg, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded, match="not live"):
        reg.check_budget_reserved([("tsmom", cfg)], BUDGET, 6, now=datetime(2027, 1, 5, tzinfo=timezone.utc))


def test_a_slot_claim_with_the_wrong_rank_or_parent_is_refused(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=2)
    disc = _run_queued(reg, queued, ["vol_a", "trend_b"])
    _clock(monkeypatch, 2027, 2, 1)
    wrong_rank = _survivor_cfg(disc, "trend_b")                  # the 2nd survivor claimed on slot 1
    _pre(reg, "tsmom", wrong_rank, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded, match="rank"):
        reg.check_budget_reserved([("tsmom", wrong_rank)], BUDGET, 9, now=FEB)
    wrong_parent = {**_survivor_cfg(disc, "vol_a"), FROM_DISCOVERY_KEY: disc["trial"] + 1}
    _pre(reg, "tsmom", wrong_parent, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded, match="from_discovery"):
        reg.check_budget_reserved([("tsmom", wrong_parent)], BUDGET, 9, now=FEB)
    unknown = _survivor_cfg(disc, "not_a_survivor", v=2)
    _pre(reg, "tsmom", unknown, slot_ref=slots["survivor"][1])
    with pytest.raises(TrialBudgetExceeded, match="rank"):
        reg.check_budget_reserved([("tsmom", unknown)], BUDGET, 9, now=FEB)
    # the right claims are covered
    ok1, ok2 = _survivor_cfg(disc, "vol_a", v=3), _survivor_cfg(disc, "trend_b", v=3)
    _pre(reg, "tsmom", ok1, slot_ref=slots["survivor"][0])
    _pre(reg, "tsmom", ok2, slot_ref=slots["survivor"][1])
    _, preregs = reg.check_budget_reserved([("tsmom", ok1), ("tsmom", ok2)], BUDGET, 9, now=FEB)
    assert all(p is not None for p in preregs)


def test_slot_ref_is_only_for_an_adhoc_preregistration_naming_a_slot_row(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=1)
    _clock(monkeypatch, 2026, 12, 21)
    with pytest.raises(ValueError, match="queue=False"):
        _pre(reg, "tsmom", {"x": 1}, queue=True, target_quarter=Q, slot_ref=slots["survivor"][0])
    with pytest.raises(ValueError, match="slot"):
        _pre(reg, "tsmom", {"x": 1}, slot_ref=queued[1])


def test_one_slot_covers_one_run(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=1)
    disc = _run_queued(reg, queued, ["vol_a"])
    for i in range(14):                                          # spend the open budget: 20 - 5 - 1 = 14
        reg._append({"trial": reg.n_trials + 1, "ts": "2027-01-20T00:00:00+00:00", "family": "trend",
                     "config_hash": f"o{i}", "status": "evaluated", "config": {}, "results": {}})
    _clock(monkeypatch, 2027, 2, 1)
    cfg = _survivor_cfg(disc, "vol_a")
    p = _pre(reg, "tsmom", cfg, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded):                     # two runs in one call: the second is open budget
        reg.check_budget_reserved([("tsmom", cfg), ("tsmom", cfg)], BUDGET, 6, now=FEB)
    _run(reg, p, "2027-02-02T00:00:00+00:00")
    _run(reg, p, "2027-02-03T00:00:00+00:00")                    # a second run linked to the same row uses nothing
    r = reserved_trials(reg._rows(), Q, 6)
    assert (r.run, r.pending, r.reserved) == (6, 0, 0)
    again = _survivor_cfg(disc, "vol_a", v=2)                    # a second pre-registration naming the used slot
    _pre(reg, "tsmom", again, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded, match="used"):
        reg.check_budget_reserved([("tsmom", again)], BUDGET, 6, now=FEB)


def test_withdraw_frees_a_queued_row_and_a_slot_and_is_not_a_trial(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch)
    _clock(monkeypatch, 2026, 12, 22)
    w = reg.withdraw(queued[3], "H-04 dropped before the freeze")
    assert w["status"] == "withdrawn" and w["reason"] and not is_trial(w)
    assert reg.n_trials == 0 and quarter_trials(reg._rows(), "2026Q4") == 0
    rows = reg._rows()
    assert pending_preregistration(rows, Q, "asia_drift", queued[3]["config_hash"]) is None
    r = reserved_trials(rows, Q, 9)
    assert (r.run, r.withdrawn, r.pending, r.reserved) == (0, 1, 8, 8)
    reg.withdraw(slots["survivor"][3], "only three H-02 survivor trials planned")
    rows = reg._rows()
    assert slot_state(rows, slots["survivor"][3]) == "withdrawn"
    r = reserved_trials(rows, Q, 9)
    assert (r.withdrawn, r.pending, r.reserved) == (2, 7, 7)
    with pytest.raises(ValueError, match="already withdrawn"):
        reg.withdraw(queued[3], "twice")
    disc = _run_queued(reg, [q for i, q in enumerate(queued) if i != 3], ["a", "b", "c", "d"])
    with pytest.raises(ValueError, match="already run"):
        reg.withdraw(queued[0], "too late")
    _clock(monkeypatch, 2027, 2, 1)
    cfg = _survivor_cfg(disc, "d")
    _pre(reg, "tsmom", cfg, slot_ref=slots["survivor"][3])      # the withdrawn slot cannot be used
    with pytest.raises(TrialBudgetExceeded, match="withdrawn"):
        reg.check_budget_reserved([("tsmom", cfg)], 4 + 3, 9, now=FEB)


def test_withdrawing_the_parent_frees_its_survivor_slots(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=2)
    _clock(monkeypatch, 2026, 12, 22)
    reg.withdraw(queued[0], "H-02 dropped")
    rows = reg._rows()
    assert {slot_state(rows, s) for s in slots["survivor"]} == {"expired"}
    r = reserved_trials(rows, Q, 7)
    assert (r.withdrawn, r.expired, r.pending, r.reserved) == (1, 2, 4, 4)


def test_a_holdout_slot_is_live_only_after_survivor_k_passed_its_gates(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=2, kinds=("survivor", "holdout"))
    hold = slots["holdout"]
    assert [slot_state(reg._rows(), s) for s in hold] == ["pending", "pending"]
    disc = _run_queued(reg, queued, ["vol_a", "trend_b"])
    assert [slot_state(reg._rows(), s) for s in hold] == ["pending", "pending"]   # survivors not run yet
    _clock(monkeypatch, 2027, 2, 1)
    c1, c2 = _survivor_cfg(disc, "vol_a"), _survivor_cfg(disc, "trend_b")
    h1 = _pre(reg, "tsmom", c1, slot_ref=hold[0])               # a holdout scoring claimed before its gate pass
    with pytest.raises(TrialBudgetExceeded, match="not live"):
        reg.check_budget_reserved([("tsmom", c1)], BUDGET, 9, now=FEB)
    reg.withdraw(h1, "written too early")
    p1, p2 = _pre(reg, "tsmom", c1, slot_ref=slots["survivor"][0]), _pre(reg, "tsmom", c2, slot_ref=slots["survivor"][1])
    _run(reg, p1, "2027-02-02T00:00:00+00:00", results={"gates": {"passed": True}})
    _run(reg, p2, "2027-02-03T00:00:00+00:00", results={"gates": {"passed": False}})
    rows = reg._rows()
    assert [slot_state(rows, s) for s in hold] == ["live", "expired"]
    _clock(monkeypatch, 2027, 2, 10)
    h1 = _pre(reg, "tsmom", c1, slot_ref=hold[0])
    _, (m,) = reg.check_budget_reserved([("tsmom", c1)], BUDGET, 11, now=datetime(2027, 2, 10, tzinfo=timezone.utc))
    assert m == h1
    other = {**c1, "v": 9}                                       # a holdout of another config is not survivor 1's
    _pre(reg, "tsmom", other, slot_ref=hold[0])
    with pytest.raises(TrialBudgetExceeded, match="survivor trial"):
        reg.check_budget_reserved([("tsmom", other)], BUDGET, 11, now=datetime(2027, 2, 10, tzinfo=timezone.utc))


def test_quant_table_13_locks_8_and_9_with_slots_locks_none_when_4_survivors_run(tmp_path, monkeypatch):
    def locked(setting: int, slots: bool, root: Any) -> int:
        reg, queued, sl = _plan(root, monkeypatch, n_slots=4 if slots else 0)
        disc = _run_queued(reg, queued, ["a", "b", "c", "d"])
        _clock(monkeypatch, 2027, 2, 1)
        for k, name in enumerate(["a", "b", "c", "d"]):
            cfg = _survivor_cfg(disc, name)
            p = _pre(reg, "tsmom", cfg, slot_ref=sl["survivor"][k]) if slots else _pre(reg, "tsmom", cfg)
            reg.check_budget_reserved([("tsmom", cfg)], BUDGET, setting, now=FEB)
            _run(reg, p, f"2027-02-{2 + k:02d}T00:00:00+00:00")
        n = 0
        while True:                                              # every other open trial the quarter allows
            try:
                reg.check_budget_reserved([("trend", {"o": n})], BUDGET, setting, now=FEB)
            except TrialBudgetExceeded:
                break
            reg._append({"trial": reg.n_trials + 1, "ts": "2027-03-01T00:00:00+00:00", "family": "trend",
                         "config_hash": f"o{n}", "status": "evaluated", "config": {}, "results": {}})
            n += 1
        used = quarter_trials(reg._rows(), Q)
        assert reserved_trials(reg._rows(), Q, setting).reserved == BUDGET - used
        return BUDGET - used
    assert locked(13, False, tmp_path / "a") == 8
    assert locked(9, True, tmp_path / "b") == 0
