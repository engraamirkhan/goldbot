"""Quant ruling 2026-11-09, item 3(b): an ad-hoc (`queue: false`) pre-registration that is not withdrawn and has had no
linked result row for more than STALE_PREREG_DAYS (7) days counts as a trial of the quarter of its `ts`, in both
`quarter_trials` (the budget) and `n_trials_effective` (the deflated Sharpe's K). A run that looked at the data but
never recorded its result must not leave the budget and K untouched. Queued rows stay under the reservation
(`director.reserved_trials`); a loop row stays out of the quarter's budget (ADR 0029) but in K."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from goldbot.research.registry import (
    LOOP_RULE_REF,
    PREREGISTERED,
    WITHDRAWN,
    TrialRegistry,
    n_trials_effective,
    quarter_trials,
    row_ref,
)

Q = "2026Q4"
NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=8)          # 2026-11-12: older than 7 days, inside Q
FRESH = NOW - timedelta(days=3)


def _prereg(ts: datetime, h: str = "abc", *, queue: bool = False, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"trial": 1, "ts": ts.isoformat(), "family": "tsmom", "config_hash": h,
                           "status": PREREGISTERED, "results": {}}
    if queue:
        row["target_quarter"] = Q
    else:
        row["queue"] = False
    return {**row, **extra}


def _result(ts: datetime, prereg: dict[str, Any] | None = None, h: str = "abc", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"trial": 1, "ts": ts.isoformat(), "family": "tsmom", "config_hash": h,
                           "status": "evaluated", "results": {}}
    if prereg is not None:
        row["preregistration"] = {"ts": prereg["ts"], "trial": prereg["trial"], "config_hash": prereg["config_hash"]}
    return {**row, **extra}


def test_a_stale_unlinked_adhoc_preregistration_counts_in_the_budget_and_in_k() -> None:
    rows = [_prereg(OLD)]
    assert quarter_trials(rows, Q, now=NOW) == 1
    assert n_trials_effective(rows, now=NOW) == 1


def test_a_stale_adhoc_preregistration_counts_in_the_quarter_of_its_own_stamp() -> None:
    rows = [_prereg(datetime(2026, 9, 25, tzinfo=timezone.utc))]
    assert quarter_trials(rows, "2026Q3", now=NOW) == 1
    assert quarter_trials(rows, Q, now=NOW) == 0


def test_a_linked_preregistration_is_counted_once_through_its_result_row() -> None:
    p = _prereg(OLD - timedelta(days=30))
    rows = [p, _result(OLD, p)]
    assert quarter_trials(rows, Q, now=NOW) == 1
    assert n_trials_effective(rows, now=NOW) == 1


def test_a_result_linked_after_seven_days_still_counts_the_preregistration_once() -> None:
    p = _prereg(OLD)
    rows = [p, _result(NOW - timedelta(hours=1), p)]
    assert quarter_trials(rows, Q, now=NOW) == 1
    assert n_trials_effective(rows, now=NOW) == 1


def test_a_queued_preregistration_is_not_counted_here_so_never_twice() -> None:
    p = _prereg(datetime(2026, 9, 20, tzinfo=timezone.utc), queue=True)
    assert quarter_trials([p], Q, now=NOW) == 0
    assert n_trials_effective([p], now=NOW) == 0
    rows = [p, _result(OLD, p)]
    assert quarter_trials(rows, Q, now=NOW) == 1
    assert n_trials_effective(rows, now=NOW) == 1


def test_a_loop_preregistration_is_out_of_the_quarter_budget_but_in_k() -> None:
    rows = [_prereg(OLD, rule_ref=LOOP_RULE_REF), _result(OLD, h="loopres", rule_ref=LOOP_RULE_REF)]
    assert quarter_trials(rows, Q, now=NOW) == 0
    assert n_trials_effective(rows, now=NOW) == 2


def test_an_adhoc_preregistration_younger_than_seven_days_is_not_counted_yet() -> None:
    rows = [_prereg(FRESH)]
    assert quarter_trials(rows, Q, now=NOW) == 0
    assert n_trials_effective(rows, now=NOW) == 0
    assert quarter_trials([_prereg(NOW - timedelta(days=7))], Q, now=NOW) == 0


SHA = "0" * 64
EVIDENCE = [{"path": "runs/tsmom/abc/out.json", "sha256": SHA}]


def _withdrawal(p: dict[str, Any], reason: str, evidence: Any = None, ts: datetime | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"trial": 2, "ts": (ts or OLD + timedelta(hours=1)).isoformat(), "family": "tsmom",
                           "config_hash": p["config_hash"], "status": WITHDRAWN, "withdraws": row_ref(p),
                           "reason": reason, "results": {}}
    if evidence is not None:
        row["evidence"] = evidence
    return row


def test_a_withdrawal_stating_unevaluable_or_never_formed_with_evidence_exempts_the_preregistration() -> None:
    for reason in ("unevaluable: no bars after the session filter", "never formed: 0 boxes in the window"):
        p = _prereg(OLD)
        rows = [p, _withdrawal(p, reason, EVIDENCE)]
        assert quarter_trials(rows, Q, now=NOW) == 0, reason
        assert n_trials_effective(rows, now=NOW) == 0, reason


def test_a_withdrawal_without_evidence_or_with_another_reason_does_not_exempt() -> None:
    p = _prereg(OLD)
    for w in (_withdrawal(p, "unevaluable: no bars"),                                   # no evidence
              _withdrawal(p, "unevaluable: no bars", []),                               # empty evidence
              _withdrawal(p, "unevaluable: no bars", [{"path": "out.json"}]),           # no sha256
              _withdrawal(p, "unevaluable: no bars", [{"path": "out.json", "sha256": "abc"}]),  # not a sha256
              _withdrawal(p, "unevaluable: ", EVIDENCE),                                # no cause
              _withdrawal(p, "not run", EVIDENCE),                                      # another reason
              _withdrawal(p, "superseded by a better spec", EVIDENCE)):
        assert quarter_trials([p, w], Q, now=NOW) == 1, w
        assert n_trials_effective([p, w], now=NOW) == 1, w


def test_a_withdrawal_written_after_a_read_of_the_spec_does_not_exempt() -> None:
    p = _prereg(OLD)
    read = _result(OLD + timedelta(minutes=30), h="abc")         # an unlinked read of the same spec
    rows = [p, read, _withdrawal(p, "unevaluable: no bars", EVIDENCE)]
    assert quarter_trials(rows, Q, now=NOW) == 2
    assert n_trials_effective(rows, now=NOW) == 2


def test_a_not_a_trial_note_on_the_preregistration_exempts_nothing() -> None:
    rows = [_prereg(OLD, rationale="not a trial", note="NOT A TRIAL: exploratory", plan={"not_a_trial": True})]
    assert quarter_trials(rows, Q, now=NOW) == 1
    assert n_trials_effective(rows, now=NOW) == 1


def test_withdraw_records_the_evidence_of_an_unevaluable_spec(tmp_path: Any) -> None:
    reg = TrialRegistry(tmp_path / "trials.jsonl")
    p = reg.preregister(agent_id="a", family="tsmom", config={"x": 1}, feature_version="v1", rationale="r",
                        reading_rule="rule")
    w = reg.withdraw(p, "never formed: 0 boxes", evidence=EVIDENCE)
    assert w["evidence"] == EVIDENCE


def test_the_registry_budget_and_k_count_a_stale_preregistration(tmp_path: Any, monkeypatch: Any) -> None:
    from goldbot.research import registry as registry_mod
    reg = TrialRegistry(tmp_path / "trials.jsonl")
    monkeypatch.setattr(registry_mod, "_utcnow", lambda: OLD)
    reg.preregister(agent_id="a", family="tsmom", config={"x": 1}, feature_version="v1", rationale="r",
                    reading_rule="rule")
    monkeypatch.setattr(registry_mod, "_utcnow", lambda: NOW)
    assert reg.budget_used(Q) == 1
    assert reg.n_trials_effective == 1
