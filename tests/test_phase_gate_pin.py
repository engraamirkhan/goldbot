"""ADR 0085 pin: the owner's paper -> tiny-live phase gate keeps TODAY's criteria exactly.

ADR 0085 (power-first research gates) changes research and demo promotion only. Gate 1 (`gates_phase.evaluate_gates`,
"backtest_to_paper") and the tournament's shadow -> live check (`TrialRegistry.passed_gates`, jobs.tournament) read
`registry.phase_gate_refusal` and the legacy `results.gates` verdict: DSR at the registry's K pass/fail, the count
minimums (1,500 candidates, 60 a fold, 200 trades, 10 a year) and the holdout/rerun rules, as before.

The golden file was written by the code at 9d1abbc7 (origin/main before ADR 0085), where the predicate was named
`promotion_refusal`. Never regenerate it to make this test pass: a difference IS a change to the phase gate, which is
owner-only (CLAUDE.md non-negotiables; ADR 0085 consequences)."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.research import cpcv_gate
from goldbot.research import registry as RG
from tests.test_phase_gates import (
    COSTS,
    NOW,
    discovery_rows,
    dsr_inputs,
    full_state,
    report,
    trial_row,
    write_rows,
)

GOLDEN = Path(__file__).parent / "data" / "phase_gate_pin_golden.json"
Refusal = Callable[[dict[str, Any], list[dict[str, Any]]], str | None]


def _scenarios() -> dict[str, list[dict[str, Any]]]:
    """Representative registries: a pass, each legacy failure, the discovery/holdout and rerun rules, a registry K
    that sinks the DSR, and a rule-only row that the NEW research rules would favour (its model-path gates failed)."""
    rerun_fail = {**trial_row(9, dsr=0.97, mean_ret=0.002, passed=False, failed_check="per_fold"),
                  "ts": "2026-10-05T10:00:00+00:00"}
    first = {**trial_row(7, dsr=0.97, mean_ret=0.002, passed=True), "ts": "2026-10-02T10:00:00+00:00"}
    no_inputs = trial_row(7, dsr=0.97, mean_ret=0.002, passed=True)
    del no_inputs["results"]["model_filtered"]["dsr_inputs"]
    crowd = [{"trial": 100 + i, "family": "other", "status": "evaluated", "feature_version": "f",
              "config": {"i": i}, "results": {"gates": {"passed": False, "checks": []}, **COSTS}} for i in range(60)]
    rule_only = trial_row(7, dsr=0.40, mean_ret=0.002, passed=False, failed_check="candidates")
    rule_only["results"]["rule_only_gates"] = {"passed": True, "checks": []}
    rule_only["results"]["model_filtered"]["n"] = 150
    rule_only["results"]["model_filtered"]["dsr_inputs"] = dsr_inputs(0.40, n=150)
    holdout_only = {"trial": 8, "family": "session_open", "status": "holdout", "feature_version": "f",
                    "results": {"holdout_verdict": {"passed": True}, **COSTS}}
    return {
        "pass": [trial_row(7, dsr=0.97, mean_ret=0.002, passed=True)],
        "dsr_below_bar": [trial_row(7, dsr=0.90, mean_ret=0.002, passed=False)],
        "candidates_failed": [trial_row(7, dsr=0.97, mean_ret=0.002, passed=False, failed_check="candidates")],
        "per_fold_failed": [trial_row(7, dsr=0.97, mean_ret=0.002, passed=False, failed_check="per_fold")],
        "discovery_no_holdout": discovery_rows(holdout_passed=None),
        "discovery_holdout_failed": discovery_rows(holdout_passed=False, k=2),
        "discovery_holdout_passed": discovery_rows(holdout_passed=True, k=2),
        "later_rerun_fails": [first, rerun_fail],
        "no_dsr_inputs": [no_inputs],
        "registry_k_sinks_dsr": [trial_row(7, dsr=0.97, mean_ret=0.002, passed=True), *crowd],
        "rule_only_favoured_by_new_rules": [rule_only],
        "holdout_row_only": [holdout_only],
    }


def snapshot(tmp_path: Path, refusal: Refusal) -> dict[str, Any]:
    """Every phase-gate decision on every scenario: gate 1's items (name, met, evidence, need) and verdict, the
    predicate's reason on every row, `passed_gates` on every configuration and the CPCV gate's research refusal."""
    out: dict[str, Any] = {}
    for name, rows in _scenarios().items():
        root = tmp_path / name
        state, trials = full_state(root)
        write_rows(trials, rows)
        rep = report(state, trials)
        g1 = next(g for g in rep.gates if g.gate == "backtest_to_paper")
        reg = RG.TrialRegistry(trials)
        stored = reg._rows()
        configs = {(str(r.get("family")), json.dumps(r.get("config") or {}, sort_keys=True)) for r in stored}
        out[name] = {
            "gate1_met": g1.met,
            "gate1_items": [[i.name, i.met, i.evidence, i.need] for i in g1.items],
            "refusals": [refusal(r, stored) for r in stored],
            "passed_gates": sorted([fam, cfg, reg.passed_gates(fam, json.loads(cfg))] for fam, cfg in configs),
            "cpcv_gate_passing": [r.get("trial") for r in cpcv_gate.gate_passing(stored)],
        }
    return out


def _normalise(snap: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(snap, default=str))


def test_gate_1_and_passed_gates_decisions_are_byte_identical_to_before_adr_0085(tmp_path):
    """The phase gate's decisions on representative registries match, byte for byte, what 9d1abbc7 decided."""
    golden = json.loads(GOLDEN.read_text())
    assert _normalise(snapshot(tmp_path, RG.phase_gate_refusal)) == golden


def test_a_not_started_power_row_changes_no_phase_gate_decision_and_costs_no_k(tmp_path):
    """A "NOT STARTED (power)" row reads no data, so it is no trial: the registry K, gate 1 and passed_gates are the
    same with and without it."""
    from goldbot.research.power_first import NOT_STARTED_POWER
    rows = _scenarios()["pass"]
    refused = {"trial": 50, "family": "other", "status": NOT_STARTED_POWER, "config": {"x": 1},
               "ts": "2026-10-03T00:00:00+00:00", "results": {"power_first": {"started": False}}}
    assert RG.n_trials_effective([*rows, refused]) == RG.n_trials_effective(rows)
    state, trials = full_state(tmp_path / "a")
    write_rows(trials, rows)
    base = report(state, trials)
    state2, trials2 = full_state(tmp_path / "b")
    write_rows(trials2, [*rows, refused])
    with_row = report(state2, trials2)
    items = [[(i.name, i.met, i.evidence) for i in g.items] for g in base.gates if g.gate == "backtest_to_paper"]
    items2 = [[(i.name, i.met, i.evidence) for i in g.items] for g in with_row.gates if g.gate == "backtest_to_paper"]
    assert items == items2


def test_a_research_promotion_confirm_pass_never_satisfies_the_phase_gate(tmp_path):
    """A Holm-confirmed rule-only card (ADR 0085 research/demo promotion) is no gate-1 evidence: gate 1 still needs a
    walk-forward row that passed today's research gates."""
    from goldbot.research.promotion_confirm import PROMOTION_CONFIRM
    confirm = {"trial": 11, "family": "session_open", "status": PROMOTION_CONFIRM, "feature_version": "f",
               "config": {}, "ts": pd.Timestamp(NOW).isoformat(),
               "results": {"promotion_confirm": {"holm_rejected": True, "passed": True}, **COSTS}}
    rows = [trial_row(7, dsr=0.40, mean_ret=0.002, passed=False, failed_check="candidates"), confirm]
    state, trials = full_state(tmp_path)
    write_rows(trials, rows)
    rep = report(state, trials)
    g1 = next(g for g in rep.gates if g.gate == "backtest_to_paper")
    assert not g1.met
    assert not RG.TrialRegistry(trials).passed_gates("session_open", {})
    assert RG.phase_gate_refusal(confirm, rows) == "not a walk-forward trial"
