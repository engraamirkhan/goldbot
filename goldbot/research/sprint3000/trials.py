"""Sprint-3000 trials in the program registry (quant review H1): "Every spec counts in K whether it fires or not".

Before the first score, every catalogue spec and every family ensemble (one candidate and one null-control ensemble
per family, the most the ensemble stage can form; the level batch forms its own, ENS-LV-*, per family of its specs) is
written as a PRE-REGISTRATION row (`TrialRegistry.preregister`, queue=False, rule_ref = the C1 amendment). A pre-registration is a promise, not a look: it takes no trial-budget slot
and does not raise the registry's count, so this module never touches `research.trial_budget_quarter` or its
overrides. Turning these rows into trial rows after the run is a budget decision for the owner (the quarterly cap is
500 at most, the catalogue holds 3,000): the result rows are not written here.

The run's K is read from the registry at run time: `n_trials_effective` plus every sprint-3000 pre-registration that has
no trial row yet, so the 3,000 specs and the ensembles count before their rows exist."""
from __future__ import annotations

from typing import Any

from goldbot.research.registry import PREREGISTERED, TrialRegistry, config_hash, is_trial
from goldbot.research.sprint3000 import vocab as V

FAMILY_PREFIX = "sprint3000:"
RULE_REF = "docs/research/sprint3000/prereg-amendment-c1.md"
FEATURE_VERSION = "dukascopy-1m-mid"
READING_RULE = "config/research/sprint3000_rules.yaml (explore -> ensemble -> confirm BH-FDR -> test_old)"


def planned_rows(cat: V.Catalogue) -> list[tuple[str, dict[str, Any]]]:
    """(registry family, config) of every spec and every possible family ensemble, in rank then family order."""
    rows: list[tuple[str, dict[str, Any]]] = [
        (FAMILY_PREFIX + s.family, {"sprint3000": s.id, "catalogue_sha256": cat.sha256,
                                    "spec": s.model_dump(mode="json")}) for s in cat.specs]
    for fam in dict.fromkeys(s.family for s in cat.specs):
        for ens in (f"ENS-{fam}", f"ENS-NULL-{fam}"):
            rows.append((FAMILY_PREFIX + fam, {"sprint3000": ens, "catalogue_sha256": cat.sha256,
                                               "ensemble_of_family": fam}))
    # the level batch's own ensembles (amendment C1 ruling e); the main rows above keep their config hashes
    for fam in dict.fromkeys(s.family for s in cat.specs if s.batch == "levels"):
        for ens in (f"ENS-LV-{fam}", f"ENS-LV-NULL-{fam}"):
            rows.append((FAMILY_PREFIX + fam, {"sprint3000": ens, "catalogue_sha256": cat.sha256,
                                               "ensemble_of_family": fam, "batch": "levels"}))
    return rows


def preregister_catalogue(reg: TrialRegistry, cat: V.Catalogue, agent_id: str = "implementer") -> int:
    """Write the missing pre-registrations (idempotent); returns how many were written. Under the registry lock."""
    written = 0
    with reg.locked():
        have = {(r.get("family"), r.get("config_hash")) for r in reg._rows() if r.get("status") == PREREGISTERED}
        for family, config in planned_rows(cat):
            if (family, config_hash(config)) in have:
                continue
            reg.preregister(agent_id=agent_id, family=family, config=config, feature_version=FEATURE_VERSION,
                            rationale=f"sprint-3000 {config['sprint3000']} (owner directive 2026-11-02)",
                            reading_rule=READING_RULE, queue=False, rule_ref=RULE_REF)
            written += 1
    return written


def program_k(reg: TrialRegistry, cat: V.Catalogue) -> tuple[int, str]:
    """(K, source): the registry's effective trials plus every planned sprint-3000 row not yet run as a trial (planned
    rows not even pre-registered count too: K never waits for the paperwork)."""
    rows = reg._rows()
    ran = {(r.get("family"), r.get("config_hash")) for r in rows if is_trial(r)}
    pending = sum(1 for fam, cfg in planned_rows(cat) if (fam, config_hash(cfg)) not in ran)
    n_eff = reg.n_trials_effective
    return n_eff + pending, f"registry {reg.path.name}: {n_eff} effective trials + {pending} sprint-3000 pending"
