"""Sprint-3000 trials in the registry (goldbot/research/sprint3000/trials.py, quant review H1): every spec and every
possible family ensemble is pre-registered before the first score (no trial, no budget slot) and the run's K counts them
all from the registry at run time."""
from __future__ import annotations

from pathlib import Path

from goldbot.research.registry import TrialRegistry, quarter_of
from goldbot.research.sprint3000 import trials as T
from goldbot.research.sprint3000 import vocab as V

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "sprint3000" / "sample_catalogue.yaml"


def test_preregistration_rows_take_no_budget_and_k_counts_every_planned_row(tmp_path: Path) -> None:
    cat = V.load_catalogue(SAMPLE)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    reg.record(agent_id="x", family="other", config={"a": 1}, feature_version="v", rationale="r", results={})
    families = {s.family for s in cat.specs}
    planned = len(cat.specs) + 2 * len(families)                       # a candidate and a null ensemble per family
    assert len(T.planned_rows(cat)) == planned
    k0, _ = T.program_k(reg, cat)
    assert k0 == 1 + planned                                           # K never waits for the paperwork
    assert T.preregister_catalogue(reg, cat) == planned
    assert T.preregister_catalogue(reg, cat) == 0                      # idempotent
    assert reg.n_trials == 1 and reg.budget_used(quarter_of()) == 1    # no trial, no budget slot
    k1, source = T.program_k(reg, cat)
    assert k1 == k0 and "registry" in source
    assert all(r.get("rule_ref") == T.RULE_REF for r in reg._rows()[1:])
