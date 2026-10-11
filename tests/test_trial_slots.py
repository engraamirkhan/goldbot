"""Contingent trial slots and withdrawal (ADR 0007): a survivor/follow-up/holdout slot is pre-registered before its
quarter with a deterministic derivation rule, holds the reservation only while its condition can still be met, and is
used by exactly one in-quarter ad-hoc pre-registration that names it (`slot_ref`) and runs exactly the derived
configuration."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from goldbot.config import load_settings
from goldbot.research import registry as registry_mod
from goldbot.research.director import is_queued, pending_preregistration, reserved_trials, slot_state
from goldbot.research.registry import (
    FROM_DISCOVERY_KEY,
    SURVIVOR_KEY,
    TrialBudgetExceeded,
    TrialRegistry,
    config_hash,
    is_trial,
    quarter_trials,
)
from goldbot.specialists import SPECIALISTS

Q = "2027Q1"
BUDGET = 20
FEB = datetime(2027, 2, 1, tzinfo=timezone.utc)
SPEC = {"timeframe": "4h", "target_atr": 3.0, "stop_atr": 1.5, "max_bars": 12}
POOL = ["H-03", "H-05", "H-07", "H-08", "H-10"]          # H-02's pre-registered hypothesis clusters (rows 6-9)
MEMBERS = {"H-03": ["macro_gvz", "rv_20"], "H-05": ["round_dist", "sr_dist"], "H-07": ["macro_vix"],
           "H-08": ["sweep_asia"], "H-10": ["jump_z"], "volatility": ["atr_14"]}
QUEUED: list[tuple[str, dict[str, Any]]] = [("discovery_tsmom", {"specialist": "tsmom", "spec": SPEC, "h": "H-02"}), ("tsmom", {"h": "H-01"}),
          ("tsmom", {"h": "H-06"}), ("asia_drift", {"h": "H-04"}), ("tsmom", {"h": "C4H"})]
RULE = ("the k-th H-02 hypothesis cluster (H-03/05/07/08/10) by group stability among clusters with stability >= 0.6, "
        "only if H-02 reads continue (OOF AUC >= 0.53, lower bound > 0.50); its members + side, H-02's label")
RETIRED = ("breakout", "intraday_momentum", "mean_reversion", "session_open", "trend")


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


def _verdict(decision: str = "continue", auc: float = 0.56, lb: float = 0.51) -> dict[str, Any]:
    return {"decision": decision, "oof_auc": auc, "auc_lower_bound": lb}


def _discovery_results(survivors: list[str], verdict: dict[str, Any] | None = None) -> dict[str, Any]:
    """H-02 grouped by hypothesis cluster: `survivors` pass (stability >= 0.6) in this order; the other clusters sit
    at 0.4; `volatility` (not a pre-registered cluster) is the most stable group and must never be a survivor."""
    stab = {c: 0.4 for c in POOL} | {s: round(0.95 - 0.05 * i, 2) for i, s in enumerate(survivors)}
    stab["volatility"] = 0.99
    return {"discovery": {"survivor_unit": "feature", "selected": [m for s in survivors for m in MEMBERS[s]],
                          "stability": {}, "group_stability": stab, "group_members": MEMBERS,
                          "reading_verdict": verdict or _verdict()}}


def _plan(tmp_path: Any, monkeypatch: pytest.MonkeyPatch, n_slots: int = 4,
          kinds: tuple[str, ...] = ("survivor",)) -> tuple[TrialRegistry, list[dict[str, Any]], dict[str, list]]:
    """The Q1 plan as amended: 5 queued rows and `n_slots` contingent slots per kind on H-02, written 2026-12-20."""
    reg = TrialRegistry(tmp_path / "r.jsonl")
    _clock(monkeypatch, 2026, 12, 20)
    queued = [_pre(reg, f, c, queue=True, target_quarter=Q) for f, c in QUEUED]
    slots = {kind: [reg.reserve_slot(queued[0], kind, k, derivation_rule=RULE, target_quarter=Q, survivor_pool=POOL)
                    for k in range(1, n_slots + 1)] for kind in kinds}
    return reg, queued, slots


def _run_queued(reg: TrialRegistry, queued: list[dict[str, Any]], survivors: list[str],
                verdict: dict[str, Any] | None = None) -> dict[str, Any]:
    """The 5 queued trials run in January; returns H-02's discovery row."""
    disc = _run(reg, queued[0], "2027-01-10T00:00:00+00:00", status="discovery",
                results=_discovery_results(survivors, verdict))
    for i, p in enumerate(queued[1:]):
        _run(reg, p, f"2027-01-{11 + i:02d}T00:00:00+00:00")
    return disc


def _ref(row: dict[str, Any]) -> dict[str, Any]:
    return {"ts": row["ts"], "config_hash": row["config_hash"]}


def _survivor_cfg(disc: dict[str, Any], name: str, **extra: Any) -> dict[str, Any]:
    """Exactly the derived configuration: H-02's spec and label, the cluster's recorded members, the two tags."""
    return {**SPEC, FROM_DISCOVERY_KEY: _ref(disc), SURVIVOR_KEY: name, "features": ["side", *MEMBERS[name]], **extra}


def _check(reg: TrialRegistry, cfg: dict[str, Any], setting: int = 9, family: str = "tsmom",
           now: datetime = FEB) -> list[dict[str, Any] | None]:
    return reg.check_budget_reserved([(family, cfg)], BUDGET, setting, now=now, retired=RETIRED)[1]


def test_a_slot_row_is_a_queued_preregistration_with_no_config_hash_written_before_its_quarter(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=1)
    s = slots["survivor"][0]
    assert s["status"] == "preregistered" and s["queue"] is True and s["config_hash"] is None
    assert s["slot"]["kind"] == "survivor" and s["slot"]["k"] == 1 and s["slot"]["derivation_rule"] == RULE
    assert s["slot"]["parent"]["ts"] == queued[0]["ts"] and s["target_quarter"] == Q
    assert s["slot"]["survivor_pool"] == POOL
    assert s["slot"]["rule"] == {"min_stability": 0.6, "min_auc": 0.53, "min_auc_lower_bound": 0.5}
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


def test_a_survivor_slot_is_used_by_the_kth_clusters_derived_config(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch)
    r = reserved_trials(reg._rows(), Q, 9)
    assert (r.run, r.pending, r.reserved) == (0, 9, 9)          # 5 queued + 4 slots held before the quarter
    disc = _run_queued(reg, queued, ["H-05", "H-03", "H-10", "H-08"])
    r = reserved_trials(reg._rows(), Q, 9)
    assert (r.run, r.pending, r.expired, r.reserved) == (5, 4, 0, 4)
    assert slot_state(reg._rows(), slots["survivor"][0]) == "live"
    _clock(monkeypatch, 2027, 2, 1)
    cfg = _survivor_cfg(disc, "H-05")
    p = _pre(reg, "tsmom", cfg, slot_ref=slots["survivor"][0])
    assert p["queue"] is False and p["slot_ref"]["ts"] == slots["survivor"][0]["ts"]
    for i in range(11):                                          # spend the open budget: 20 - 5 used - 4 reserved
        reg._append({"trial": reg.n_trials + 1, "ts": f"2027-01-2{i % 9}T00:00:00+00:00", "family": "trend",
                     "config_hash": f"o{i}", "status": "evaluated", "config": {}, "results": {}})
    with pytest.raises(TrialBudgetExceeded):
        _check(reg, {"z": 1}, family="trend")
    assert _check(reg, cfg) == [p]
    _run(reg, p, "2027-02-02T00:00:00+00:00")
    r = reserved_trials(reg._rows(), Q, 9)
    assert (r.run, r.pending, r.reserved) == (6, 3, 3)
    assert slot_state(reg._rows(), slots["survivor"][0]) == "used"


def test_survivor_slots_are_freed_when_the_parent_records_no_surviving_cluster(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch)
    _run_queued(reg, queued, [])                                 # only the non-cluster group `volatility` is stable
    rows = reg._rows()
    r = reserved_trials(rows, Q, 9)
    assert (r.run, r.pending, r.expired, r.reserved) == (5, 0, 4, 0)
    assert {slot_state(rows, s) for s in slots["survivor"]} == {"expired"}


def test_only_as_many_survivor_slots_stay_live_as_clusters_pass(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch)
    _run_queued(reg, queued, ["H-08", "H-07"])
    rows = reg._rows()
    assert [slot_state(rows, s) for s in slots["survivor"]] == ["live", "live", "expired", "expired"]
    r = reserved_trials(rows, Q, 9)
    assert (r.run, r.pending, r.expired, r.reserved) == (5, 2, 2, 2)


@pytest.mark.parametrize("verdict", [_verdict("stop", auc=0.50, lb=0.47), _verdict("inconclusive", 0.525, 0.49),
                                     _verdict("continue", auc=0.52, lb=0.51), _verdict("continue", 0.55, 0.50)])
def test_survivor_and_followup_slots_expire_unless_the_parent_reads_continue_under_its_rule(tmp_path, monkeypatch,
                                                                                         verdict):
    """Quant HIGH 2 (the reviewer's probe): stable clusters are not survivors when H-02's reading rule says stop."""
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=1)
    _clock(monkeypatch, 2026, 12, 21)
    fslot = reg.reserve_slot(queued[0], "followup", 1, derivation_rule="H-02 follow-up", target_quarter=Q,
                             family="tsmom", config_hashes=[config_hash({"f": 1})])
    _run_queued(reg, queued, ["H-05", "H-03"], verdict=verdict)
    rows = reg._rows()
    assert slot_state(rows, slots["survivor"][0]) == "expired"
    assert slot_state(rows, fslot) == "expired"


def test_before_the_parent_runs_a_survivor_slot_is_held_but_cannot_be_used(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=1)
    assert slot_state(reg._rows(), slots["survivor"][0]) == "pending"
    _clock(monkeypatch, 2027, 1, 5)
    cfg = {**SPEC, FROM_DISCOVERY_KEY: _ref(queued[0]), SURVIVOR_KEY: "H-05", "features": MEMBERS["H-05"]}
    _pre(reg, "tsmom", cfg, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded, match="not live"):
        _check(reg, cfg, setting=6, now=datetime(2027, 1, 5, tzinfo=timezone.utc))


def test_a_slot_claim_with_the_wrong_rank_or_parent_is_refused(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=2)
    disc = _run_queued(reg, queued, ["H-05", "H-03"])
    _clock(monkeypatch, 2027, 2, 1)
    wrong_rank = _survivor_cfg(disc, "H-03")                     # the 2nd cluster claimed on slot 1
    _pre(reg, "tsmom", wrong_rank, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded, match="rank"):
        _check(reg, wrong_rank)
    wrong_parent = {**_survivor_cfg(disc, "H-05"), FROM_DISCOVERY_KEY: _ref(queued[1])}
    _pre(reg, "tsmom", wrong_parent, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded, match="from_discovery"):
        _check(reg, wrong_parent)
    by_number = {**_survivor_cfg(disc, "H-05", v=1), FROM_DISCOVERY_KEY: disc["trial"]}   # a number is renumbered
    _pre(reg, "tsmom", by_number, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded, match="from_discovery"):
        _check(reg, by_number)
    not_a_cluster = {**_survivor_cfg(disc, "H-05"), SURVIVOR_KEY: "volatility", "features": MEMBERS["volatility"]}
    _pre(reg, "tsmom", not_a_cluster, slot_ref=slots["survivor"][1])
    with pytest.raises(TrialBudgetExceeded, match="rank"):
        _check(reg, not_a_cluster)
    ok1, ok2 = _survivor_cfg(disc, "H-05"), _survivor_cfg(disc, "H-03")
    _pre(reg, "tsmom", ok1, slot_ref=slots["survivor"][0])
    _pre(reg, "tsmom", ok2, slot_ref=slots["survivor"][1])
    _, preregs = reg.check_budget_reserved([("tsmom", ok1), ("tsmom", ok2)], BUDGET, 9, now=FEB, retired=RETIRED)
    assert all(p is not None for p in preregs)


def test_a_survivor_slot_covers_only_the_parents_family_spec_and_the_clusters_members(tmp_path, monkeypatch):
    """Quant HIGH 1: only the two tags right is not enough."""
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=1)
    disc = _run_queued(reg, queued, ["H-05"])
    _clock(monkeypatch, 2027, 2, 1)
    cfg = _survivor_cfg(disc, "H-05")
    _pre(reg, "asia_drift", cfg, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded, match="family"):
        _check(reg, cfg, family="asia_drift")
    label = {**_survivor_cfg(disc, "H-05"), "target_atr": 9.9}
    _pre(reg, "tsmom", label, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded, match="spec"):
        _check(reg, label)
    feats = {**_survivor_cfg(disc, "H-05"), "features": ["side", "round_dist", "anything_else"]}
    _pre(reg, "tsmom", feats, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded, match="features"):
        _check(reg, feats)


def test_a_slot_never_covers_a_retired_family(tmp_path, monkeypatch):
    """The reviewer's probe: a retired family with arbitrary features was covered by the survivor slot."""
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=1)
    disc = _run_queued(reg, queued, ["H-05"])
    _clock(monkeypatch, 2027, 2, 1)
    cfg = {FROM_DISCOVERY_KEY: _ref(disc), SURVIVOR_KEY: "H-05", "features": ["anything", "else"], "target_atr": 9.9}
    _pre(reg, "breakout", cfg, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded, match="retired"):
        _check(reg, cfg, family="breakout")
    # without an explicit list the configured research.retired_families apply (fail closed)
    assert "breakout" in {e.family for e in load_settings().research.retired_families}
    with pytest.raises(TrialBudgetExceeded, match="retired"):
        reg.check_budget_reserved([("breakout", cfg)], BUDGET, 9, now=FEB)


def test_slot_ref_is_only_for_an_adhoc_preregistration_naming_a_slot_row(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=1)
    _clock(monkeypatch, 2026, 12, 21)
    with pytest.raises(ValueError, match="queue=False"):
        _pre(reg, "tsmom", {"x": 1}, queue=True, target_quarter=Q, slot_ref=slots["survivor"][0])
    with pytest.raises(ValueError, match="slot"):
        _pre(reg, "tsmom", {"x": 1}, slot_ref=queued[1])


def test_one_slot_covers_one_run(tmp_path, monkeypatch):
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=1)
    disc = _run_queued(reg, queued, ["H-05"])
    for i in range(14):                                          # spend the open budget: 20 - 5 - 1 = 14
        reg._append({"trial": reg.n_trials + 1, "ts": "2027-01-20T00:00:00+00:00", "family": "trend",
                     "config_hash": f"o{i}", "status": "evaluated", "config": {}, "results": {}})
    _clock(monkeypatch, 2027, 2, 1)
    cfg = _survivor_cfg(disc, "H-05")
    p = _pre(reg, "tsmom", cfg, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded):                     # two runs in one call: the second is open budget
        reg.check_budget_reserved([("tsmom", cfg), ("tsmom", cfg)], BUDGET, 6, now=FEB, retired=RETIRED)
    _run(reg, p, "2027-02-02T00:00:00+00:00")
    _run(reg, p, "2027-02-03T00:00:00+00:00")                    # a second run linked to the same row uses nothing
    r = reserved_trials(reg._rows(), Q, 6)
    assert (r.run, r.pending, r.reserved) == (6, 0, 0)
    again = _survivor_cfg(disc, "H-05", v=2)                     # a second pre-registration naming the used slot
    _pre(reg, "tsmom", again, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded, match="used"):
        _check(reg, again, setting=6)


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
    disc = _run_queued(reg, [q for i, q in enumerate(queued) if i != 3], ["H-05", "H-03", "H-10", "H-08"])
    with pytest.raises(ValueError, match="already run"):
        reg.withdraw(queued[0], "too late")
    _clock(monkeypatch, 2027, 2, 1)
    cfg = _survivor_cfg(disc, "H-08")
    _pre(reg, "tsmom", cfg, slot_ref=slots["survivor"][3])      # the withdrawn slot cannot be used
    with pytest.raises(TrialBudgetExceeded, match="withdrawn"):
        _check(reg, cfg)


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
    disc = _run_queued(reg, queued, ["H-05", "H-03"])
    assert [slot_state(reg._rows(), s) for s in hold] == ["pending", "pending"]   # survivors not run yet
    _clock(monkeypatch, 2027, 2, 1)
    c1, c2 = _survivor_cfg(disc, "H-05"), _survivor_cfg(disc, "H-03")
    h1 = _pre(reg, "tsmom", c1, slot_ref=hold[0])               # a holdout scoring claimed before its gate pass
    with pytest.raises(TrialBudgetExceeded, match="not live"):
        _check(reg, c1)
    reg.withdraw(h1, "written too early")
    p1, p2 = _pre(reg, "tsmom", c1, slot_ref=slots["survivor"][0]), _pre(reg, "tsmom", c2, slot_ref=slots["survivor"][1])
    _run(reg, p1, "2027-02-02T00:00:00+00:00", results={"gates": {"passed": True}})
    _run(reg, p2, "2027-02-03T00:00:00+00:00", results={"gates": {"passed": False}})
    rows = reg._rows()
    assert [slot_state(rows, s) for s in hold] == ["live", "expired"]
    _clock(monkeypatch, 2027, 2, 10)
    feb10 = datetime(2027, 2, 10, tzinfo=timezone.utc)
    h1 = _pre(reg, "tsmom", c1, slot_ref=hold[0])
    assert _check(reg, c1, setting=11, now=feb10) == [h1]
    other = {**c1, "v": 9}                                       # a holdout of another config is not survivor 1's
    _pre(reg, "tsmom", other, slot_ref=hold[0])
    with pytest.raises(TrialBudgetExceeded, match="keys|survivor trial"):
        _check(reg, other, setting=11, now=feb10)


def test_a_holdout_slot_expires_when_its_survivor_slot_was_used_without_an_evaluated_trial(tmp_path, monkeypatch):
    """Quant choice 1: a survivor run that stopped at the screen (status "screened") can never pass its gates."""
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=1, kinds=("survivor", "holdout"))
    disc = _run_queued(reg, queued, ["H-05"])
    _clock(monkeypatch, 2027, 2, 1)
    p = _pre(reg, "tsmom", _survivor_cfg(disc, "H-05"), slot_ref=slots["survivor"][0])
    _run(reg, p, "2027-02-02T00:00:00+00:00", status="screened")
    rows = reg._rows()
    assert slot_state(rows, slots["survivor"][0]) == "used"
    assert slot_state(rows, slots["holdout"][0]) == "expired"


def test_a_followup_slot_fixes_its_family_and_configs_before_the_quarter(tmp_path, monkeypatch):
    reg, queued, _ = _plan(tmp_path, monkeypatch, n_slots=0)
    _clock(monkeypatch, 2026, 12, 21)
    with pytest.raises(ValueError, match="family"):
        reg.reserve_slot(queued[1], "followup", 1, derivation_rule="H-01 model", target_quarter=Q)
    with pytest.raises(ValueError, match="config_hashes or overrides"):
        reg.reserve_slot(queued[1], "followup", 1, derivation_rule="H-01 model", target_quarter=Q, family="tsmom")
    allowed = {"preset": "slow", "model": True}
    f1 = reg.reserve_slot(queued[1], "followup", 1, derivation_rule="H-01 continue: the slow-TSMOM model",
                          target_quarter=Q, family="tsmom", config_hashes=[config_hash(allowed)])
    f2 = reg.reserve_slot(queued[1], "followup", 2, derivation_rule="H-01 continue: exit C-5", target_quarter=Q,
                          family="tsmom", overrides={"preset": "slow", "exit": "c5"})
    _run_queued(reg, queued, [])
    h01_run = next(r for r in reg._rows() if r.get("status") == "evaluated" and r["config"] == {"h": "H-01"})
    assert slot_state(reg._rows(), f1) == "pending"              # a rule-only parent waits for the evaluator
    _clock(monkeypatch, 2027, 2, 1)
    reg.record_verdict(h01_run, "continue", "pre-registered reading rule met (net t 1.9, 4 positive years)")
    rows = reg._rows()
    assert [slot_state(rows, f) for f in (f1, f2)] == ["live", "live"]
    exact = {**SPECIALISTS["tsmom"].default_config, "preset": "slow", "exit": "c5"}
    for fam, cfg, slot, why in (("asia_drift", allowed, f1, "family"), ("tsmom", {"preset": "fast"}, f1, "allowed"),
                                ("tsmom", {**exact, "exit": "c4"}, f2, "allowed"),
                                ("tsmom", {**exact, "target_atr": 9.0}, f2, "allowed"),         # probe 6: a superset
                                ("tsmom", {"preset": "slow", "exit": "c5", "max_bars": 500}, f2, "allowed")):
        _pre(reg, fam, cfg, slot_ref=slot)
        with pytest.raises(TrialBudgetExceeded, match=why):
            _check(reg, cfg, family=fam)
    p1 = _pre(reg, "tsmom", allowed, slot_ref=f1)
    assert _check(reg, allowed) == [p1]
    p2 = _pre(reg, "tsmom", exact, slot_ref=f2)                  # default_config + the overrides, exactly
    assert _check(reg, exact) == [p2]


def test_an_evaluator_verdict_is_not_a_trial_is_final_and_a_stop_frees_the_followups(tmp_path, monkeypatch):
    reg, queued, _ = _plan(tmp_path, monkeypatch, n_slots=0)
    _clock(monkeypatch, 2026, 12, 21)
    f1 = reg.reserve_slot(queued[3], "followup", 1, derivation_rule="H-04 continue: time-exit meta-label",
                          target_quarter=Q, family="asia_drift", overrides={"meta": "time_exit"})
    _run_queued(reg, queued, [])
    h04_run = next(r for r in reg._rows() if r.get("status") == "evaluated" and r["config"] == {"h": "H-04"})
    _clock(monkeypatch, 2027, 2, 1)
    n = reg.n_trials
    v = reg.record_verdict(h04_run, "stop", "net mean R <= 0")
    assert v["status"] == "verdict" and not is_trial(v) and reg.n_trials == n
    assert slot_state(reg._rows(), f1) == "expired"
    with pytest.raises(ValueError, match="already"):
        reg.record_verdict(h04_run, "continue", "second look")
    with pytest.raises(ValueError, match="decision"):
        reg.record_verdict(queued[1], "maybe", "x")


def test_quant_table_13_locks_8_and_9_with_slots_locks_none_when_4_survivors_run(tmp_path, monkeypatch):
    names = ["H-05", "H-03", "H-10", "H-08"]

    def locked(setting: int, slots: bool, root: Any) -> int:
        reg, queued, sl = _plan(root, monkeypatch, n_slots=4 if slots else 0)
        disc = _run_queued(reg, queued, names)
        _clock(monkeypatch, 2027, 2, 1)
        for k, name in enumerate(names):
            cfg = _survivor_cfg(disc, name)
            p = _pre(reg, "tsmom", cfg, slot_ref=sl["survivor"][k]) if slots else _pre(reg, "tsmom", cfg)
            _check(reg, cfg, setting=setting)
            _run(reg, p, f"2027-02-{2 + k:02d}T00:00:00+00:00")
        n = 0
        while True:                                              # every other open trial the quarter allows
            try:
                _check(reg, {"o": n}, setting=setting, family="trend")
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


def test_a_survivor_claim_with_keys_outside_the_spec_and_the_tags_is_refused(tmp_path, monkeypatch):
    """Probe 5: another bars window or model parameters are not the derived configuration."""
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=1)
    disc = _run_queued(reg, queued, ["H-05"])
    _clock(monkeypatch, 2027, 2, 1)
    cfg = {**_survivor_cfg(disc, "H-05"), "bars_from": "2018-01-01", "max_depth": 12, "min_score": 0.1}
    _pre(reg, "tsmom", cfg, slot_ref=slots["survivor"][0])
    with pytest.raises(TrialBudgetExceeded, match=r"keys \['bars_from', 'max_depth', 'min_score'\]"):
        _check(reg, cfg)


def test_record_verdict_refuses_a_discovery_row(tmp_path, monkeypatch):
    """Probe 2: a discovery is read by its own recorded verdict; a verdict row could not flip a STOP."""
    reg, queued, slots = _plan(tmp_path, monkeypatch, n_slots=1)
    disc = _run_queued(reg, queued, ["H-05"], verdict=_verdict("stop", 0.50, 0.47))
    _clock(monkeypatch, 2027, 2, 1)
    with pytest.raises(ValueError, match="discovery"):
        reg.record_verdict(disc, "continue", "flip attempt")
    assert slot_state(reg._rows(), slots["survivor"][0]) == "expired"


def _screened(reg: TrialRegistry, family: str, config: dict[str, Any], screen: dict[str, Any],
              split: dict[str, Any] | None = None, rule_id: str | None = None,
              reading_rule: str = "rule") -> dict[str, Any]:
    """A screened trial row, linked to an ad-hoc pre-registration carrying `rule_id` (None: no pre-registration)."""
    link = None
    if rule_id is not None or reading_rule != "rule":
        pre = reg.preregister(agent_id="a", family=family, config=config, feature_version="f", rationale="r",
                              reading_rule=reading_rule, rule_id=rule_id)
        link = {"trial": pre["trial"], "ts": pre["ts"], "config_hash": pre["config_hash"]}
    row = {"trial": reg.n_trials + 1, "ts": f"2027-01-{20 + reg.n_trials % 9:02d}T00:00:00+00:00", "agent_id": "a",
           "family": family, "config_hash": config_hash(config), "config": config, "feature_version": "f",
           "status": "screened", "results": {"screen": screen, "rule_only_split": split}}
    if link:
        row["preregistration"] = link
    reg._append(row)
    return row


def _scr(verdict: str, n: int, gross_t: float, net_mean_r: float, net_t: float) -> dict[str, Any]:
    return {"verdict": verdict, "passed": verdict == "pass", "n": n, "gross_t": gross_t, "gross_mean_r": 0.05,
            "net_mean_r": net_mean_r, "net_t": net_t, "min_events": 150}


def _split(long_r: float, long_t: float, n_pos: int, chop: bool, n_long_pos: int = 4,
           long_chop: bool = True) -> dict[str, Any]:
    return {"long": {"n": 120, "mean_r": long_r, "t_stat": long_t}, "short": {"n": 40},
            "n_positive_years": n_pos, "chop_year_positive": chop,
            "n_long_positive_years": n_long_pos, "long_chop_year_positive": long_chop}


H01 = {"signal_tf": "1d"}
C4H = {"timeframe": "4h", "max_bars": 12}


@pytest.mark.parametrize("family,config,rule_id,screen,split,decision", [
    # H-04: continue iff gross t >= 2.0 on >= the floor's events AND net mean R > 0
    ("asia_drift", {"stop_atr": 1.5}, "H-04", _scr("pass", 4000, 2.4, 0.02, 1.0), None, {"continue"}),
    ("asia_drift", {"stop_atr": 1.5}, "H-04", _scr("pass", 4000, 2.4, -0.01, -0.3), None, {"stop"}),
    ("asia_drift", {"stop_atr": 1.5}, "H-04", _scr("fail", 4000, 1.1, 0.02, 0.5), None, {"stop"}),
    ("asia_drift", {"stop_atr": 1.5}, "H-04", _scr("inconclusive (event floor)", 400, 2.5, 0.05, 1.0), None,
     {"inconclusive"}),
    # H-01
    ("tsmom", H01, "H-01", _scr("fail", 300, 1.0, 0.05, 1.0), _split(0.1, 2.0, 4, True), {"stop"}),
    ("tsmom", H01, "H-01", _scr("inconclusive (event floor)", 120, 2.5, 0.1, 2.0), None, {"inconclusive"}),
    ("tsmom", H01, "H-01", _scr("pass", 300, 2.5, -0.02, -0.5), _split(0.1, 2.0, 4, True), {"stop"}),
    ("tsmom", H01, "H-01", _scr("pass", 300, 2.5, 0.05, 1.2), _split(0.1, 2.0, 4, True), {"inconclusive"}),
    ("tsmom", H01, "H-01", _scr("pass", 300, 2.5, 0.05, 1.9), _split(0.1, 2.0, 2, True), {"inconclusive"}),
    ("tsmom", H01, "H-01", _scr("pass", 300, 2.5, 0.05, 1.9), _split(-0.01, 2.0, 4, True), {"inconclusive"}),
    # quant re-review: >= 3 positive net years incl. 2021/22 on long-only too
    ("tsmom", H01, "H-01", _scr("pass", 300, 2.5, 0.05, 1.9), _split(0.1, 1.8, 4, True, n_long_pos=2),
     {"inconclusive"}),
    ("tsmom", H01, "H-01", _scr("pass", 300, 2.5, 0.05, 1.9), _split(0.1, 1.8, 4, True, long_chop=False),
     {"inconclusive"}),
    ("tsmom", H01, "H-01", _scr("pass", 300, 2.5, 0.05, 1.9), _split(0.1, 1.8, 4, True), {"continue"}),
    # tsmom 4h continuity: net mean R <= 0 retires the horizon; otherwise only "not retired" (never continue)
    ("tsmom", C4H, "C4H", _scr("fail", 2000, 0.4, -0.03, -1.0), None, {"stop"}),
    ("tsmom", C4H, "C4H", _scr("fail", 2000, 0.4, 0.01, 0.2), None, {"inconclusive"}),
    # keyed on the pre-registration's rule id, not the config shape: an unlinked slow-tsmom config is just a screen
    ("tsmom", H01, None, _scr("pass", 300, 2.5, 0.05, 1.9), _split(0.1, 1.8, 4, True), {"stop", "inconclusive"}),
    ("tsmom", C4H, None, _scr("fail", 2000, 0.4, 0.01, 0.2), None, {"stop"}),
])
def test_record_verdict_on_a_screened_row_must_be_the_preregistered_decision(tmp_path, family, config, rule_id,
                                                                          screen, split, decision):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    row = _screened(reg, family, config, screen, split, rule_id=rule_id)
    for other in sorted({"continue", "stop", "inconclusive"} - decision):
        with pytest.raises(ValueError, match="pre-registered decision"):
            reg.record_verdict(row, other, "a reading that does not follow the rule")
    pick = sorted(decision)[0]
    v = reg.record_verdict(row, pick, "computed from the recorded results")
    assert v["decision"] == pick and v["rule_checked"] is True


def test_an_unknown_screened_pass_continues_only_citing_its_preregistrations_reading_rule(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    rule = "continue if gross t >= 2.0 on >= 1,000 events and net mean R > 0"
    row = _screened(reg, "breakout", {"x": 1}, _scr("pass", 1500, 2.2, 0.02, 1.0), reading_rule=rule)
    with pytest.raises(ValueError, match="pre-registered decision"):
        reg.record_verdict(row, "continue", "looks good")                      # cites nothing
    with pytest.raises(ValueError, match="pre-registered decision"):
        reg.record_verdict(row, "continue", "looks good", rule="some other rule")
    assert reg.record_verdict(row, "continue", "the pre-registered rule holds", rule=rule)["decision"] == "continue"
    bare = _screened(reg, "breakout", {"x": 2}, _scr("pass", 1500, 2.2, 0.02, 1.0))     # no pre-registration
    with pytest.raises(ValueError, match="pre-registered decision"):
        reg.record_verdict(bare, "continue", "x", rule=rule)


def test_preregister_accepts_only_known_rule_ids(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    with pytest.raises(ValueError, match="rule_id"):
        reg.preregister(agent_id="a", family="tsmom", config={}, feature_version="f", rationale="r",
                        reading_rule="r", rule_id="H-99")
