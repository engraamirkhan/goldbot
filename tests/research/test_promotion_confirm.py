"""ADR 0085 confirm for research and demo promotion (goldbot/research/promotion_confirm.py): Holm on fresh data at a
fixed convergent alpha-spending schedule from ONE global never-reset ledger; the rule-only gates without the model
path's count minimums; registry-K DSR report-only."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.research import gates
from goldbot.research import promotion_confirm as PC
from goldbot.research.registry import TrialRegistry, n_trials_effective, row_ref

EPOCH = PC.LEDGER_EPOCH
NOW = EPOCH + timedelta(days=30)
STAMP = PC.FrozenStamp(card_hash="c" * 12, code_sha="a" * 40, feature_version="fv1", cost_of_record_version="cor-3")


def units(mean: float, *, n_days: int = 600, per_day: int = 1, sd: float = 1.2, seed: int = 0,
          start: str = "2008-01-02") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = np.repeat(np.arange(n_days), per_day)
    ts = pd.Timestamp(start, tz="UTC") + pd.to_timedelta(days, unit="D")
    return pd.DataFrame({"day": days, "net_r": rng.normal(mean, sd, len(days)), "ts_utc": ts})


def passing_learn_gates() -> dict:
    """Pooled family trades positive in 4 years incl. 2021."""
    ts = pd.to_datetime([f"{y}-03-01" for y in (2018, 2019, 2020, 2021) for _ in range(12)], utc=True)
    return gates.rule_only_promotion_gates(pd.DataFrame({"ts_utc": ts, "ret": 0.1}), dsr=None)


def item(family: str = "fam", *, source: PC.Source = "test_old", kind: PC.Kind = "rule_only",
         config: dict | None = None, learn_ref: dict | None = None) -> PC.ConfirmItem:
    return PC.ConfirmItem(family=family, config=config or {"p": family}, source=source, kind=kind, stamp=STAMP,
                          cost_basis="measured_upper_ci", learn_gates=passing_learn_gates(),
                          learn_ref=learn_ref or {"ts": "2026-11-01T00:00:00+00:00", "config_hash": "x", "trial": 1})


def run(reg: TrialRegistry, batch: list[tuple[PC.ConfirmItem, pd.DataFrame]], now: datetime = NOW, **kw) -> list[dict]:
    return PC.run_batch(reg, batch, now=now, mean_block=3.0, agent_id="evaluator", **kw)


# ------------------------------------------------------------------------------------------------ the schedule
def test_the_schedule_is_q_times_two_to_the_minus_year_split_over_the_fixed_batches_and_sums_to_q():
    assert PC.Q_PROMOTION == 0.05 and PC.BATCHES_PER_YEAR == 4
    assert PC.batch_alpha(1) == pytest.approx(0.05 * 0.5 / 4)
    assert PC.batch_alpha(3) == pytest.approx(0.05 / 8 / 4)
    total = sum(PC.batch_alpha(y) * PC.BATCHES_PER_YEAR for y in range(1, 200))
    assert total == pytest.approx(0.05) and total <= 0.05


def test_ledger_years_run_from_the_adr_epoch_and_nothing_is_read_before_it():
    assert PC.ledger_year(EPOCH) == 1
    assert PC.ledger_year(EPOCH + timedelta(days=364)) == 1
    assert PC.ledger_year(datetime(2027, 11, 15, tzinfo=timezone.utc)) == 2
    with pytest.raises(ValueError):
        PC.ledger_year(EPOCH - timedelta(seconds=1))


def test_a_year_takes_at_most_its_fixed_batches_and_unused_slots_are_never_carried(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    for i in range(PC.BATCHES_PER_YEAR):
        run(reg, [(item(f"f{i}"), units(0.0, seed=i))], now=NOW + timedelta(days=i))
    with pytest.raises(PC.LedgerFull):
        run(reg, [(item("late"), units(0.0))], now=NOW + timedelta(days=10))
    nxt = PC.PromotionLedger(reg._rows()).next_slot(datetime(2027, 11, 20, tzinfo=timezone.utc))
    assert (nxt.year, nxt.slot) == (2, 1) and nxt.alpha_j == pytest.approx(PC.batch_alpha(2))


def test_the_ledger_is_never_reset_it_is_derived_from_the_append_only_registry(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    run(reg, [(item("a"), units(0.0))])
    again = TrialRegistry(tmp_path / "r.jsonl")
    assert PC.PromotionLedger(again._rows()).next_slot(NOW).slot == 2


# ------------------------------------------------------------------------------------------------ Holm and the read
def test_holm_at_the_batch_level_decides_each_config_and_bh_would_reject_more():
    """Holm (FWER <= alpha_j under any dependence), not BH: at alpha 0.03 over [0.012, 0.02, 0.025] BH rejects all
    three, Holm none beyond what its step-down allows."""
    p = [0.012, 0.02, 0.025]
    assert list(PC.holm(p, 0.03)) == [False, False, False]
    from goldbot.research.eval2.confirm import _bh
    assert list(_bh(np.asarray(p), 0.03)) == [True, True, True]


def test_a_strong_fresh_edge_passes_and_a_null_fails(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    out = run(reg, [(item("strong"), units(0.40, n_days=800, seed=1)), (item("null"), units(0.0, n_days=800, seed=2))])
    res = {r["family"]: r["results"]["promotion_confirm"] for r in out}
    assert res["strong"]["holm_rejected"] and res["strong"]["passed"]
    assert not res["null"]["passed"]
    assert res["strong"]["alpha_j"] == pytest.approx(PC.batch_alpha(1)) and res["strong"]["m_j"] == 2
    assert res["strong"]["ledger_id"] == PC.LEDGER_ID


def test_every_read_is_a_charged_trial_row_with_its_frozen_stamp(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    out = run(reg, [(item("a"), units(0.1))])
    assert n_trials_effective(reg._rows()) == 1 and out[0]["status"] == PC.PROMOTION_CONFIRM
    assert out[0]["results"]["promotion_confirm"]["stamp"] == STAMP.model_dump()


def test_a_frozen_stamp_needs_every_field():
    with pytest.raises(ValidationError):
        PC.FrozenStamp(card_hash="", code_sha="a" * 40, feature_version="f", cost_of_record_version="c")
    with pytest.raises(ValidationError):
        PC.FrozenStamp(card_hash="c", code_sha="xyz", feature_version="f", cost_of_record_version="c")


def test_the_read_is_net_of_measured_costs_at_the_upper_ci_only():
    with pytest.raises(ValidationError):
        PC.ConfirmItem(**{**item().model_dump(), "cost_basis": "priors"})


def test_too_few_clustered_observations_fail_even_when_holm_rejects(tmp_path):
    """The 200-trade minimum becomes a minimum clustered n_eff on the fresh-data test: 900 trades on 150 days (6 a
    day, perfectly correlated within the day) are about 150 independent observations."""
    u = units(0.5, n_days=150, per_day=6, seed=3)
    u["net_r"] = u.groupby("day")["net_r"].transform("first")
    reg = TrialRegistry(tmp_path / "r.jsonl")
    res = run(reg, [(item("a"), u)])[0]["results"]["promotion_confirm"]
    assert res["n_trades"] == 900 and res["n_eff"] < PC.MIN_FRESH_N_EFF
    assert not res["passed"] and "n_eff" in res["why"]


def test_clustered_n_eff_is_n_for_one_trade_a_day_and_the_days_for_identical_same_day_trades():
    one = units(0.1, n_days=400, seed=4)
    assert PC.clustered_n_eff(one["net_r"].to_numpy(), one["day"].to_numpy()) == pytest.approx(400, rel=0.15)
    u = units(0.1, n_days=400, per_day=5, seed=5)
    u["net_r"] = u.groupby("day")["net_r"].transform("first")
    assert PC.clustered_n_eff(u["net_r"].to_numpy(), u["day"].to_numpy()) == pytest.approx(400, rel=0.15)


def test_the_registry_k_dsr_is_reported_with_k_but_decides_nothing(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    for i in range(300):
        reg.record(agent_id="a", family="other", config={"i": i}, feature_version="f", rationale="r", results={})
    res = run(reg, [(item("strong"), units(0.40, n_days=800, seed=1))])[0]["results"]["promotion_confirm"]
    assert res["dsr_report_only"]["k"] == 301 and res["dsr_report_only"]["dsr"] is not None
    assert res["passed"]


def test_the_size_comes_from_the_fresh_data_effect(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    u = units(0.40, n_days=800, seed=1)
    res = run(reg, [(item("a"), u)])[0]["results"]["promotion_confirm"]
    assert res["size_basis_mean_r"] == pytest.approx(float(u["net_r"].mean()))


# ------------------------------------------------------------------------------------------------ fresh data
def test_test_old_is_read_once_per_family_and_never_after_an_eval2_read(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    run(reg, [(item("a"), units(0.0))])
    with pytest.raises(PC.NotFresh, match="TEST-OLD"):
        run(reg, [(item("a", config={"other": 1}), units(0.0))], now=NOW + timedelta(days=1))
    with pytest.raises(PC.NotFresh, match="EVAL-2"):
        run(reg, [(item("b"), units(0.0))], now=NOW + timedelta(days=1), eval2_test_old_families={"b"})


def test_the_holdout_is_read_at_most_once_per_config(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    run(reg, [(item("a", source="holdout"), units(0.0))])
    with pytest.raises(PC.NotFresh, match="holdout"):
        run(reg, [(item("a", source="holdout"), units(0.0))], now=NOW + timedelta(days=1))


def test_a_refused_batch_spends_no_slot_and_writes_nothing(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    with pytest.raises(PC.NotFresh):
        run(reg, [(item("b"), units(0.0))], eval2_test_old_families={"b"})
    assert reg._rows() == [] and PC.PromotionLedger([]).next_slot(NOW).slot == 1


# ------------------------------------------------------------------------------------------------ the predicate
def test_research_promotion_rests_only_on_a_passed_confirm_of_the_one_ledger(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    row = run(reg, [(item("strong"), units(0.40, n_days=800, seed=1))])[0]
    assert PC.research_promotion_refusal(row, reg._rows()) is None
    eval2_like = {**row, "results": {"promotion_confirm": {**row["results"]["promotion_confirm"],
                                                           "ledger_id": "eval2:BatchPRDS:q0.10"}}}
    assert "ledger" in str(PC.research_promotion_refusal(eval2_like, reg._rows()))
    walk_forward = {"status": "evaluated", "family": "strong", "results": {"gates": {"passed": True}}}
    assert PC.research_promotion_refusal(walk_forward, reg._rows()) == "not an ADR 0085 promotion confirm"


def test_a_later_failed_confirm_of_the_same_config_revokes_the_pass(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    row = run(reg, [(item("a"), units(0.40, n_days=800, seed=1))])[0]
    run(reg, [(item("a", source="holdout"), units(-0.2, n_days=300, seed=9))], now=NOW + timedelta(days=1))
    assert "later" in str(PC.research_promotion_refusal(row, reg._rows()))


def test_a_rule_only_card_needs_no_candidate_or_fold_minimum_but_a_model_card_still_does(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    learn = reg.record(agent_id="a", family="m", config={"p": "m"}, feature_version="f", rationale="r",
                       results={"gates": {"passed": False, "checks": [
                           {"name": "candidates", "passed": False, "detail": "900 (need 1,500)"},
                           {"name": "per_fold", "passed": True, "detail": ""}]}})
    rows = run(reg, [(item("m", kind="model", learn_ref=row_ref(learn)), units(0.40, n_days=800, seed=1)),
                     (item("r", kind="rule_only"), units(0.40, n_days=800, seed=2))])
    by = {r["family"]: r for r in rows}
    assert "candidates" in str(PC.research_promotion_refusal(by["m"], reg._rows()))
    assert PC.research_promotion_refusal(by["r"], reg._rows()) is None


def test_failed_learn_regime_gates_refuse_the_promotion(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    bad = item("a").model_copy(update={"learn_gates": {"passed": False, "checks": []}})
    row = run(reg, [(bad, units(0.40, n_days=800, seed=1))])[0]
    assert "learn" in str(PC.research_promotion_refusal(row, reg._rows()))


# ------------------------------------------------------------------------------------------------ rule-only gates
def test_rule_only_promotion_gates_have_no_count_minimums_and_report_the_dsr_only():
    out = passing_learn_gates()
    names = {c["name"] for c in out["checks"]}
    assert "candidates" not in names and "per_fold" not in names and out["passed"]
    assert out["dsr_report_only"] is None or "dsr" in out["dsr_report_only"]


def test_pre_registered_multi_year_blocks_let_a_1d_rule_score_its_regime_years():
    """A 1d rule with 6 trades a year never makes 10 in a calendar year; 2-year blocks hold 12."""
    ts = pd.to_datetime([f"{y}-0{m}-01" for y in range(2016, 2024) for m in range(1, 7)], utc=True)
    taken = pd.DataFrame({"ts_utc": ts, "ret": 0.1})
    assert not gates.rule_only_promotion_gates(taken, dsr=None)["passed"]
    blocks = [(2016, 2017), (2018, 2019), (2020, 2021), (2022, 2023)]
    assert gates.rule_only_promotion_gates(taken, dsr=None, blocks=blocks)["passed"]


def test_blocks_must_be_pre_registered_disjoint_multi_year_spans():
    taken = pd.DataFrame({"ts_utc": pd.to_datetime(["2020-01-01"], utc=True), "ret": [0.1]})
    with pytest.raises(ValueError):
        gates.rule_only_promotion_gates(taken, dsr=None, blocks=[(2018, 2020), (2020, 2021)])
    assert math.isfinite(PC.MIN_FRESH_N_EFF)
