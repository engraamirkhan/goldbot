"""ADR 0058 decision 2, "the registry's K includes each batch's k_contrib" (F3, a phase-3 precondition; ADR 0058
amendment 2026-10-30 and its phase-3 amendment). Design 3.1: independent human hypotheses keep the registry's K
(`n_trials_effective`); a discovered candidate reads its DSR at `n_trials_discovery` = that K + every batch's k_contrib.
A discovery batch is never a trial and never a budget slot, also on a host running an older build: the batch rows live
in a sidecar (`<registry>.discovery.jsonl`) that the registry rows, `is_trial`, `quarter_trials` and registry_sync never
read, so an older `is_trial` (which counts every unknown status) never sees one."""
import json

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.research import registry_sync
from goldbot.research.autodisc import SearchLedger
from goldbot.research.autodisc import nightly as NT
from goldbot.research.autodisc.k_link import GRAMMAR_V1_K, dsr_k_offset, sync_discovery_k
from goldbot.research.autodisc.nulls import SyntheticProvider, inject_edge, make_noise_tensor
from goldbot.research.autodisc.spec import ExitTemplate, Leaf, StrategySpec
from goldbot.research.registry import DISCOVERY_BATCH, TrialRegistry, quarter_of

OLD_BUILD_NOT_TRIALS = frozenset({"preregistered", "withdrawn", "verdict"})     # is_trial on a host before phase 3


def old_is_trial(row: dict) -> bool:
    return row.get("status") not in OLD_BUILD_NOT_TRIALS


def spec(i: int) -> StrategySpec:
    return StrategySpec(timeframe="1h", side_rule="long", entry=Leaf(kind="time", name="london", params={"i": i}),
                        exit=ExitTemplate(stop="atr_1.0", target="1R"))


def test_a_discovery_batch_row_raises_the_k_of_discovered_candidates_without_a_budget_slot(tmp_path):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    reg.record_discovery_batch(k_contrib=500, generator="G2", ledger_head=(10, "0" * 64))
    assert reg.n_trials == 0 and reg.budget_used(quarter_of()) == 0          # never a trial, never a budget slot
    assert reg.n_trials_effective == 0                                        # independent hypotheses keep their K
    assert reg.n_trials_discovery == 500


def test_discovered_candidates_read_the_registry_k_plus_every_batch(tmp_path):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    reg.record(agent_id="a", family="f", config={"x": 1}, feature_version="v", rationale="r", results={})
    reg.record_discovery_batch(k_contrib=300, generator="G2", ledger_head=(5, "a" * 64))
    reg.record_discovery_batch(k_contrib=200, generator="G2", ledger_head=(9, "b" * 64))
    assert reg.n_trials_effective == 1 and reg.n_trials_discovery == 501
    assert [b["k_contrib"] for b in reg.discovery_batches()] == [300, 200]


def test_an_older_build_never_counts_a_discovery_batch_as_a_trial_or_a_budget_slot(tmp_path):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    reg.record(agent_id="a", family="f", config={"x": 1}, feature_version="v", rationale="r", results={})
    before = reg.path.read_text()
    reg.record_discovery_batch(k_contrib=500, generator="G2", ledger_head=(10, "0" * 64))
    assert reg.path.read_text() == before                                     # the synced file is untouched
    rows = [json.loads(x) for x in reg.path.read_text().splitlines() if x.strip()]
    assert sum(old_is_trial(r) for r in rows) == 1                            # an older host: still one trial
    merged = registry_sync.merge_rows(rows, rows)                             # and a sync carries no batch row
    assert all(r.get("status") != DISCOVERY_BATCH for r in merged)


def test_a_discovery_batch_cannot_be_written_as_a_registry_row(tmp_path):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    with pytest.raises(ValueError, match="discovery_batch"):
        reg.record(agent_id="a", family="f", config={}, feature_version="v", rationale="r", results={},
                   status=DISCOVERY_BATCH)
    with pytest.raises(ValueError):
        reg.record_discovery_batch(k_contrib=-1, generator="G2", ledger_head=(1, "0" * 64))


def test_sync_tops_the_registry_up_to_the_ledger_k_search_once(tmp_path):
    led = SearchLedger(tmp_path / "d.db", mode="real")
    for i in range(7):
        led.log_spec(spec(i), "G2", "1")
    led.log_spec(spec(99), "G2", "1", null=True)                              # null specs are K_null, never K
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    row = sync_discovery_k(reg, led, generator="G2 gp", timeframe="1h")
    assert row is not None and row["k_contrib"] == 7 and row["ledger_id"] == led.ledger_id()
    assert reg.n_trials_discovery == GRAMMAR_V1_K + 7
    assert sync_discovery_k(reg, led, generator="G2 gp") is None              # nothing new: no row
    for i in range(7, 10):
        led.log_spec(spec(i), "G2", "1")
    assert sync_discovery_k(reg, led, generator="G2 gp")["k_contrib"] == 3   # type: ignore[index]
    assert reg.n_trials_discovery - GRAMMAR_V1_K == led.k_search() == 10
    other = SearchLedger(tmp_path / "e.db", mode="real")                      # another ledger counts on its own
    other.log_spec(spec(0), "G2", "1")
    assert sync_discovery_k(reg, other, generator="G2 gp")["k_contrib"] == 1  # type: ignore[index]
    assert reg.n_trials_discovery == GRAMMAR_V1_K + 11


def test_a_synthetic_ledger_never_reaches_the_registry(tmp_path):
    led = SearchLedger(tmp_path / "s.db", mode="synthetic")
    led.log_spec(spec(0), "G2", "1")
    with pytest.raises(ValueError, match="synthetic"):
        sync_discovery_k(TrialRegistry(tmp_path / "registry.jsonl"), led, generator="G2 gp")


@pytest.mark.parametrize("mode", ["real", "synthetic"])
def test_the_nightly_job_records_its_batch_k_contrib_only_for_a_real_search(tmp_path, mode):
    s = load_settings()
    reg_path = tmp_path / "registry.jsonl"
    on = s.model_copy(update={"research": s.research.model_copy(update={
        "registry": str(reg_path),
        "autodisc": s.research.autodisc.model_copy(update={"nightly_enabled": True, "population": 8,
                                                           "generations": 1, "null_reps": 1})})})
    prov = SyntheticProvider(3000, seed=0)
    mask, side = prov.family_entries("tsmom", "15m")
    t = inject_edge(make_noise_tensor(3000, seed=7), mask, side, edge_r=0.4)
    out = NT.run_nightly_job(on, tmp_path, pd.Timestamp("2026-11-10 01:30", tz="UTC"), min_events=60,
                             backends=lambda st, tf: NT.Backends(tensor=t, provider=prov, families=("tsmom",),
                                                                 cost_line="synthetic", mode=mode))
    reg = TrialRegistry(reg_path)
    if mode == "real":
        assert reg.n_trials_discovery == GRAMMAR_V1_K + out["k_search"] and reg.n_trials == 0
        assert out["k_contrib"] == out["k_search"]
        assert out["k_dsr"] == reg.n_trials_discovery                      # the explore DSR's K (quant F1)
    else:
        assert reg.discovery_batches() == [] and "k_contrib" not in out


def test_the_grammar_v1_runs_are_counted_once_in_the_discovery_k(tmp_path):
    """Quant F1: grammar v1's 864 specs (the 2026-10-30 runs, 288 per timeframe) predate the registry link."""
    assert GRAMMAR_V1_K == 864
    led = SearchLedger(tmp_path / "d.db", mode="real")
    led.log_spec(spec(0), "G2", "1")
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    sync_discovery_k(reg, led, generator="G2 gp")
    sync_discovery_k(reg, led, generator="G2 gp")
    rows = [b for b in reg.discovery_batches() if b["generator"] == "grammar-v1 (A4)"]
    assert len(rows) == 1 and rows[0]["k_contrib"] == 864
    assert reg.n_trials_discovery >= 864 + led.k_search()


def test_the_explore_dsr_is_read_at_the_registry_discovery_k(tmp_path):
    led = SearchLedger(tmp_path / "d.db", mode="real")
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    reg.record(agent_id="a", family="f", config={"x": 1}, feature_version="v", rationale="r", results={})
    off = dsr_k_offset(reg, led)                                               # before tonight's specs
    for i in range(5):
        led.log_spec(spec(i), "G2", "1")
    sync_discovery_k(reg, led, generator="G2 gp")
    assert off + led.k_search() == reg.n_trials_discovery == 1 + 864 + 5
