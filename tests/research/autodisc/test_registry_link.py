"""ADR 0058 decision 2, "the registry's K includes each batch's k_contrib": a phase-3 precondition (ADR 0058 amendment,
2026-10-30). Not wired in phase 2: the nightly search writes nothing to the trial registry, and its DSR is read at the
ledger's cumulative K_search. This test states the contract phase 3 must meet before any discovered spec reaches a
confirm trial; it is a strict xfail so the day the wiring lands it fails loudly until the marker is removed."""
import pytest

from goldbot.research.registry import TrialRegistry, quarter_of


@pytest.mark.xfail(strict=True, raises=AttributeError, reason="phase-3 precondition: discovery_batch registry rows (ADR 0058 amendment "
                                       "2026-10-30, F3)")
def test_a_discovery_batch_row_raises_the_k_of_discovered_candidates_without_a_budget_slot(tmp_path):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    reg.record_discovery_batch(k_contrib=500, generator="G2", ledger_head=(10, "0" * 64))   # type: ignore[attr-defined]
    assert reg.n_trials == 0 and reg.budget_used(quarter_of()) == 0          # never a trial, never a budget slot
    assert reg.n_trials_effective == 0                                        # independent hypotheses keep their K
    assert reg.n_trials_discovery == 500                                      # type: ignore[attr-defined]
