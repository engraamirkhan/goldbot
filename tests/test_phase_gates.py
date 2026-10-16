"""P6 stop rule and P7 roadmap gates (goldbot/ops/gates_phase.py): every threshold on both sides of its boundary, and
nothing here records a gate, unlocks live or changes risk state."""
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.ops import accounts, gates_phase, health
from goldbot.ops.cost_imputation import CostImputer
from goldbot.ops.gates_phase import ClosedTrade, GateReport, evaluate_gates
from goldbot.ops.health import AccountRef, HealthContext

NOW = pd.Timestamp("2026-10-07 14:00", tz="UTC")
SETTINGS = load_settings()
CFG = SETTINGS.gates
CAP = SETTINGS.risk.weekly_cap
PAPER_START = NOW - pd.Timedelta(days=800)
LIVE_START = NOW - pd.Timedelta(days=400)
PRIORS = CostImputer.from_settings(SETTINGS)


def evaluate_stop_rule(trades: list[ClosedTrade], *a: Any) -> gates_phase.StopRuleStatus:
    return gates_phase.evaluate_stop_rule(trades, *a, imputer=PRIORS)


def trade(ret: float, *, mode: str = "demo", broker: str = "icmarkets", days_ago: float = 10.0, pnl: float | None = None,
          equity: float = 10_000.0, account: str | None = None, gross_held_days: float | None = None) -> ClosedTrade:
    """A net trade (the broker's P&L with commission and swap inside); with `gross_held_days`, a gross one (commission
    and swap unknown) entered that many days before its exit at $2,000, long, 0.1 lots."""
    exit_utc = NOW - pd.Timedelta(days=days_ago)
    costs: dict[str, Any] = (dict(commission=0.0, swap=0.0, pnl_basis="net") if gross_held_days is None else
                             dict(pnl_basis="gross", side=1, lots=0.1, entry_price=2_000.0,
                                  entry_utc=exit_utc - pd.Timedelta(days=gross_held_days)))
    return ClosedTrade(account_id=account or f"{broker}-{mode}", broker=broker, mode=mode,  # type: ignore[arg-type]
                       exit_utc=exit_utc, ret=ret, pnl=pnl if pnl is not None else ret * equity, equity_before=equity,
                       **costs)


def trades_over(n: int, ret: float | list[float], first_days_ago: float, last_days_ago: float, **kw: Any) -> list[ClosedTrade]:
    rets = ret if isinstance(ret, list) else [ret] * n
    step = (first_days_ago - last_days_ago) / max(n - 1, 1)
    return [trade(r, days_ago=first_days_ago - i * step, **kw) for i, r in enumerate(rets)]


def write_trades(state: Path, trades: list[ClosedTrade]) -> None:
    for f in gates_phase.closed_trade_files(state):
        f.unlink()
    if trades:
        gates_phase.append_closed_trade(state, trades[0])          # the writer's format ...
        with open(gates_phase.closed_trade_file(state, trades[0].account_id), "a", encoding="utf-8") as fh:
            fh.writelines(t.model_dump_json() + "\n" for t in trades[1:])   # ... the rest in one write


def write_phase(state: Path, recorded: dict[str, pd.Timestamp]) -> None:
    gates = [g for g in accounts.GATES if g in recorded]
    (state / "phase_state.json").write_text(json.dumps({
        "phase": len(gates), "gates_passed": gates,
        "gate_log": [{"gate": g, "ts_utc": recorded[g].strftime("%Y-%m-%dT%H:%M:%SZ"), "evidence": {"text": "t"}} for g in gates]}))


# the research cost treatment a row records (results.swap, results.cost_source): only then does its gate pass count
COSTS: dict = {"swap": {"long_usd_per_lot": -60.0, "short_usd_per_lot": 20.0}, "cost_source": "PRIORS ONLY: test"}


def dsr_inputs(dsr: float, *, n: int = 600, k: int = 1) -> dict[str, float]:
    """The stored deflated-Sharpe inputs (metrics.DsrInputs) of a row whose DSR is `dsr` (or a hair above) on `k`
    trials: gate 1 recomputes the DSR from them with the registry's current trial count."""
    from scipy.optimize import brentq

    from goldbot.research.metrics import DsrInputs
    def at(sr: float) -> DsrInputs:
        return DsrInputs(sr=sr, n=n, skew=0.0, kurtosis=3.0, var_sr_trials=1.0 / n)
    sr = float(brentq(lambda s: at(s).dsr(k) - dsr, -1.0, 1.0, xtol=1e-15))
    while at(sr).dsr(k) < dsr:
        sr = float(np.nextafter(sr, 1.0))
    return at(sr).model_dump()


def write_trial(path: Path, *, dsr: float = 0.97, n: int = 600, mean_ret: float = 0.002, max_dd: float = 0.05,
                years_passed: bool = True, status: str = "evaluated") -> None:
    row = {"trial": 7, "family": "session_open", "status": status,
           "results": {"model_filtered": {"n": n, "dsr": dsr, "mean_ret": mean_ret, "max_dd": max_dd,
                                          "dsr_inputs": dsr_inputs(dsr, n=n)},
                       "gates": {"passed": years_passed, "checks": [
                           {"name": "positive_years", "passed": years_passed, "detail": "positive years [2019, 2021, 2024]"}]},
                       **COSTS}}
    path.write_text(json.dumps(row) + "\n")


def write_costs(state: Path, *, slip_mean: float = 0.13, prior: float = 0.10, n_ticks: int = 50_000, account: str = "icm-demo") -> None:
    (state / f"costs_{account}.json").write_text(json.dumps({
        "account_id": account, "n_ticks": n_ticks, "spread": {"london": {"median": 0.2}} if n_ticks else {},
        "slippage": {"london:market": {"mean": slip_mean, "n": 40, "from_prior": False}}, "slippage_prior_usd": prior}))


def full_state(tmp_path: Path) -> tuple[Path, Path]:
    """Evidence on which every gate is met (paper 0.001 vs backtest 0.002, live 0.0006 vs paper 0.001: both exactly at
    the allowed shortfall plus a hair), and no stop-rule condition."""
    state = tmp_path / "state"
    state.mkdir(parents=True)
    write_phase(state, {"foundation_to_backtest": PAPER_START - pd.Timedelta(days=60), "backtest_to_paper": PAPER_START,
                        "paper_to_tiny_live": LIVE_START})
    trials = tmp_path / "trials.jsonl"
    write_trial(trials)
    write_costs(state)
    for name, val in (("shuffle_auc", 0.51), ("asof_violations", 0.0), ("feed_mismatch_share", 0.005)):
        gates_phase.record_evidence(state, name, value=val, now=NOW)
    gates_phase.record_evidence(state, "chaos_drill", passed=True, detail="terminal killed mid-position", now=NOW)
    paper = trades_over(150, 0.001001, 790, 410)
    live = (trades_over(150, 0.000601, 390, 10, mode="live", broker="icmarkets")
            + trades_over(150, 0.000601, 389, 11, mode="live", broker="vantage"))
    write_trades(state, paper + live)
    return state, trials


def report(state: Path, trials: Path, now: pd.Timestamp = NOW) -> GateReport:
    return evaluate_gates(state, SETTINGS, now, trials_path=trials)


def item(rep: GateReport, gate: str, name: str) -> bool:
    g = next(x for x in rep.gates if x.gate == gate)
    return next(i for i in g.items if i.name == name).met


def evidence(rep: GateReport, gate: str, name: str) -> str:
    g = next(x for x in rep.gates if x.gate == gate)
    return next(i for i in g.items if i.name == name).evidence


def trial_row(trial: int, *, dsr: float, mean_ret: float, passed: bool, failed_check: str | None = None) -> dict:
    """A swap-charged walk-forward row whose own items (DSR, trades, positive years) are all fine; `passed` is its
    recorded research-gate verdict, `failed_check` the check that failed it."""
    checks = [{"name": "positive_years", "passed": True, "detail": "positive years [2019, 2021, 2024]"}]
    if failed_check:
        checks.append({"name": failed_check, "passed": False, "detail": f"{failed_check} below the design's minimum"})
    return {"trial": trial, "family": "session_open", "status": "evaluated",
            "results": {"model_filtered": {"n": 600, "dsr": dsr, "mean_ret": mean_ret, "max_dd": 0.05,
                                           "dsr_inputs": dsr_inputs(dsr)},
                        "gates": {"passed": passed, "checks": checks}, **COSTS}}


def gate_met(rep: GateReport, gate: str) -> bool:
    return next(x for x in rep.gates if x.gate == gate).met


# ------------------------------------------------------------------------------------------------ the full picture
def test_every_gate_met_on_full_evidence_and_rendered(tmp_path):
    state, trials = full_state(tmp_path)
    rep = report(state, trials)
    assert [g.met for g in rep.gates] == [True, True, True, True], gates_phase.render(rep)
    assert not rep.stop_rule.breached
    text = gates_phase.render(rep)
    assert "paper_to_tiny_live: MET" in text and "never records a gate or unlocks live" in text


def test_nothing_recorded_means_nothing_met(tmp_path):
    rep = evaluate_gates(tmp_path, SETTINGS, NOW, trials_path=tmp_path / "none.jsonl")
    assert not any(g.met for g in rep.gates) and not any(g.recorded for g in rep.gates)
    assert not rep.stop_rule.breached and rep.stop_rule.pooled_trades == 0


def test_a_gate_needs_its_predecessor_recorded(tmp_path):
    state, trials = full_state(tmp_path)
    write_phase(state, {"foundation_to_backtest": PAPER_START - pd.Timedelta(days=60)})
    rep = report(state, trials)
    assert gate_met(rep, "backtest_to_paper")
    assert not item(rep, "paper_to_tiny_live", "previous_gate") and not gate_met(rep, "paper_to_tiny_live")
    assert not item(rep, "paper_to_tiny_live", "paper_days")     # the paper clock starts at the backtest_to_paper record


# ------------------------------------------------------------------------------------------------ gate 0: leakage audit
@pytest.mark.parametrize("name,ok,bad", [("shuffle_auc", 0.52, 0.5201), ("shuffle_auc", 0.48, 0.4799),
                                         ("asof_violations", 0.0, 1.0), ("feed_mismatch_share", 0.01, 0.0101)])
def test_leakage_audit_boundaries(tmp_path, name, ok, bad):
    state, trials = full_state(tmp_path)
    gates_phase.record_evidence(state, name, value=ok, now=NOW)
    assert item(report(state, trials), "foundation_to_backtest", name)
    gates_phase.record_evidence(state, name, value=bad, now=NOW)
    rep = report(state, trials)
    assert not item(rep, "foundation_to_backtest", name) and not gate_met(rep, "foundation_to_backtest")


def test_missing_evidence_is_not_met_and_bad_evidence_is_refused(tmp_path):
    rep = evaluate_gates(tmp_path, SETTINGS, NOW, trials_path=tmp_path / "t.jsonl")
    assert not item(rep, "foundation_to_backtest", "shuffle_auc")
    with pytest.raises(ValueError, match="unknown evidence"):
        gates_phase.record_evidence(tmp_path, "vibes", value=1.0)
    with pytest.raises(ValueError, match="exactly one"):
        gates_phase.record_evidence(tmp_path, "chaos_drill", value=1.0, passed=True)
    assert gates_phase.evidence_main(["chaos_drill", "--passed", "no"], state_dir=tmp_path) == 0
    assert not item(evaluate_gates(tmp_path, SETTINGS, NOW, trials_path=tmp_path / "t.jsonl"), "paper_to_tiny_live", "chaos_drill")
    assert gates_phase.evidence_main(["vibes", "--value", "1"], state_dir=tmp_path) == 1


# ------------------------------------------------------------------------------------------------ gate 1: backtest -> paper
def test_backtest_dsr_and_trade_count_boundaries(tmp_path):
    state, trials = full_state(tmp_path)
    write_trial(trials, dsr=CFG.backtest_min_dsr, n=CFG.backtest_min_trades)
    rep = report(state, trials)
    assert item(rep, "backtest_to_paper", "dsr") and item(rep, "backtest_to_paper", "backtest_trades")
    write_trial(trials, dsr=CFG.backtest_min_dsr - 1e-4, n=CFG.backtest_min_trades - 1)
    rep = report(state, trials)
    assert not item(rep, "backtest_to_paper", "dsr") and not item(rep, "backtest_to_paper", "backtest_trades")
    assert not gate_met(rep, "backtest_to_paper")


def test_backtest_positive_years_holdout_rows_and_spread_model(tmp_path):
    state, trials = full_state(tmp_path)
    write_trial(trials, years_passed=False)
    assert not item(report(state, trials), "backtest_to_paper", "positive_years")
    write_trial(trials, status="holdout")                       # a held-out scoring is never backtest evidence
    assert not item(report(state, trials), "backtest_to_paper", "dsr")
    write_trial(trials)
    write_costs(state, n_ticks=0)                               # spread still the prior, not measured from own ticks
    assert not item(report(state, trials), "backtest_to_paper", "live_tick_spread_model")


# ------------------------------------------------------------------------------------------------ gate 2: paper -> tiny live
def test_paper_trade_count_boundary(tmp_path):
    state, trials = full_state(tmp_path)
    live = [t for t in gates_phase.load_closed_trades(state)[0] if t.mode == "live"]
    write_trades(state, trades_over(CFG.paper_min_trades - 1, 0.001001, 790, 410) + live)
    assert not item(report(state, trials), "paper_to_tiny_live", "paper_trades")
    write_trades(state, trades_over(CFG.paper_min_trades, 0.001001, 790, 410) + live)
    assert item(report(state, trials), "paper_to_tiny_live", "paper_trades")


def test_paper_days_boundary(tmp_path):
    state, trials = full_state(tmp_path)
    at = PAPER_START + pd.Timedelta(days=CFG.paper_min_days)
    assert item(report(state, trials, now=at), "paper_to_tiny_live", "paper_days")
    assert not item(report(state, trials, now=at - pd.Timedelta(minutes=1)), "paper_to_tiny_live", "paper_days")


def test_paper_expectancy_within_half_of_backtest(tmp_path):
    state, trials = full_state(tmp_path)
    live = [t for t in gates_phase.load_closed_trades(state)[0] if t.mode == "live"]
    floor = (1 - CFG.paper_expectancy_within) * 0.002
    write_trades(state, trades_over(150, floor * 1.0001, 790, 410) + live)
    assert item(report(state, trials), "paper_to_tiny_live", "paper_expectancy")
    write_trades(state, trades_over(150, floor * 0.999, 790, 410) + live)
    assert not item(report(state, trials), "paper_to_tiny_live", "paper_expectancy")
    write_trades(state, trades_over(150, 0.01, 790, 410) + live)       # better than backtest always passes
    assert item(report(state, trials), "paper_to_tiny_live", "paper_expectancy")
    write_trial(trials, mean_ret=-0.001)                                # a losing backtest is no reference
    assert not item(report(state, trials), "paper_to_tiny_live", "paper_expectancy")


@pytest.mark.parametrize("failed_check", ["candidates_per_fold", "complete_folds"])
def test_a_swap_charged_trial_that_failed_its_candidate_or_fold_checks_leaves_gate_1_unmet(tmp_path, failed_check):
    """Review of 978c667: DSR, trades and positive years can all be met by a trial that failed the research gates
    (too few candidates per fold, incomplete folds); gate 1 requires the trial's own gate pass (gate_pass_counts)."""
    state, trials = full_state(tmp_path)
    assert item(report(state, trials), "backtest_to_paper", "research_gates")
    trials.write_text(json.dumps(trial_row(7, dsr=0.97, mean_ret=0.002, passed=False, failed_check=failed_check)) + "\n")
    rep = report(state, trials)
    assert item(rep, "backtest_to_paper", "dsr") and item(rep, "backtest_to_paper", "positive_years")
    assert item(rep, "backtest_to_paper", "backtest_trades")
    assert not item(rep, "backtest_to_paper", "research_gates") and not gate_met(rep, "backtest_to_paper")
    assert failed_check in evidence(rep, "backtest_to_paper", "research_gates")


def test_paper_expectancy_names_its_comparator_and_never_switches_to_a_trial_that_failed_the_gates(tmp_path):
    """Review of 978c667: the comparator is the best backtest trial; a later swap-charged trial with a higher DSR that
    failed the gates (and whose mean happens to match paper) must not silently become the reference and pass."""
    state, trials = full_state(tmp_path)
    rep = report(state, trials)
    assert item(rep, "paper_to_tiny_live", "paper_expectancy")
    assert "trial #7" in evidence(rep, "paper_to_tiny_live", "paper_expectancy")
    rows = [trial_row(7, dsr=0.97, mean_ret=0.002, passed=True),
            trial_row(9, dsr=0.99, mean_ret=0.001, passed=False, failed_check="candidates_per_fold")]
    trials.write_text("".join(json.dumps(r) + "\n" for r in rows))
    rep = report(state, trials)
    text = evidence(rep, "paper_to_tiny_live", "paper_expectancy")
    assert not item(rep, "paper_to_tiny_live", "paper_expectancy") and "trial #9" in text
    assert "did not pass the research gates" in text
    assert not item(rep, "tiny_live_to_full_size", "live_drawdown")      # the same comparator, the same refusal


# quant review 2026-10-16: a discovery-derived config needs its holdout pass; the DSR is recomputed with today's trial
# count; reruns of one config are not best-of-N
def discovery_rows(*, holdout_passed: bool | None, k: int = 1) -> list[dict]:
    """A swap-charged walk-forward row of a discovery-derived config that passed its gates (DSR 0.97 on `k` trials),
    and, unless `holdout_passed` is None, that config's one holdout scoring."""
    from goldbot.research.registry import FROM_DISCOVERY_KEY, config_hash
    cfg = {"band_z": 2.0, FROM_DISCOVERY_KEY: {"ts": "2026-10-01T00:00:00+00:00", "trial": 3}}
    wf = {**trial_row(7, dsr=0.97, mean_ret=0.002, passed=True), "config": cfg, "config_hash": config_hash(cfg),
          "feature_version": "f", "ts": "2026-10-02T10:00:00+00:00"}
    wf["results"]["model_filtered"]["dsr_inputs"] = dsr_inputs(0.97, k=k)
    if holdout_passed is None:
        return [wf]
    ho = {"trial": 8, "family": "session_open", "status": "holdout", "config": cfg, "config_hash": config_hash(cfg),
          "feature_version": "f", "ts": "2026-10-03T10:00:00+00:00",
          "results": {"holdout_verdict": {"passed": holdout_passed}, **COSTS}}
    return [wf, ho]


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_gate_1_is_unmet_for_a_discovery_derived_trial_without_its_holdout_pass(tmp_path):
    """registry.passed_gates already required it; gate 1 read gate_pass_counts alone (quant review 2026-10-16, HIGH)."""
    state, trials = full_state(tmp_path)
    write_rows(trials, discovery_rows(holdout_passed=None))
    rep = report(state, trials)
    assert item(rep, "backtest_to_paper", "dsr") and item(rep, "backtest_to_paper", "backtest_trades")
    assert not item(rep, "backtest_to_paper", "research_gates") and not gate_met(rep, "backtest_to_paper")
    assert "holdout" in evidence(rep, "backtest_to_paper", "research_gates")
    write_rows(trials, discovery_rows(holdout_passed=False, k=2))           # a failed holdout scoring: still unmet
    rep = report(state, trials)
    assert not item(rep, "backtest_to_paper", "research_gates") and not gate_met(rep, "backtest_to_paper")
    assert not item(rep, "paper_to_tiny_live", "paper_expectancy")         # and no backtest reference either


def test_gate_1_is_met_for_a_discovery_derived_trial_once_its_holdout_passed(tmp_path):
    state, trials = full_state(tmp_path)
    write_rows(trials, discovery_rows(holdout_passed=True, k=2))
    rep = report(state, trials)
    assert item(rep, "backtest_to_paper", "research_gates") and gate_met(rep, "backtest_to_paper"), gates_phase.render(rep)


def test_gate_1_dsr_is_recomputed_with_the_registrys_current_trial_count(tmp_path):
    """The stored model_filtered.dsr used the trial count of its run; gate 1 needs "DSR on the true trial count", which
    grows with every later trial (quant review 2026-10-16). DSR 0.96 at K=10 must fail at K=200."""
    state, trials = full_state(tmp_path)
    best = trial_row(7, dsr=0.96, mean_ret=0.002, passed=True)
    best["results"]["model_filtered"]["dsr_inputs"] = dsr_inputs(0.96, k=10)
    others = [{"trial": i, "family": "trend", "status": "screened", "results": {}} for i in range(8, 8 + 9)]
    write_rows(trials, [best, *others])                                     # K = 10, as at run time
    rep = report(state, trials)
    assert item(rep, "backtest_to_paper", "dsr"), evidence(rep, "backtest_to_paper", "dsr")
    others = [{"trial": i, "family": "trend", "status": "screened", "results": {}} for i in range(8, 8 + 199)]
    write_rows(trials, [best, *others])                                     # K = 200 today: the same row now fails
    rep = report(state, trials)
    assert not item(rep, "backtest_to_paper", "dsr") and not gate_met(rep, "backtest_to_paper")
    text = evidence(rep, "backtest_to_paper", "dsr")
    assert "200 trials" in text and "0.960 stored" in text


def test_gate_1_dsr_fails_closed_on_a_row_without_its_stored_dsr_inputs(tmp_path):
    """A row written before the inputs were stored cannot be recomputed: it is not evidence, whatever its stored DSR."""
    state, trials = full_state(tmp_path)
    legacy = trial_row(7, dsr=0.99, mean_ret=0.002, passed=True)
    del legacy["results"]["model_filtered"]["dsr_inputs"]
    write_rows(trials, [legacy])
    rep = report(state, trials)
    assert not item(rep, "backtest_to_paper", "dsr") and not gate_met(rep, "backtest_to_paper")
    assert "no stored DSR inputs" in evidence(rep, "backtest_to_paper", "dsr")


def test_gate_1_reads_only_the_latest_rerun_of_a_config_so_a_later_failure_revokes_an_earlier_pass(tmp_path):
    from goldbot.research.registry import config_hash
    state, trials = full_state(tmp_path)
    cfg = {"band_z": 2.0}
    common = {"config": cfg, "config_hash": config_hash(cfg), "feature_version": "f"}
    passed = {**trial_row(7, dsr=0.99, mean_ret=0.002, passed=True), **common, "ts": "2026-10-02T10:00:00+00:00"}
    rerun = {**trial_row(8, dsr=0.90, mean_ret=0.001, passed=False, failed_check="complete_folds"), **common,
             "ts": "2026-10-03T10:00:00+00:00"}
    write_rows(trials, [passed])
    assert item(report(state, trials), "backtest_to_paper", "research_gates")
    write_rows(trials, [passed, rerun])
    rep = report(state, trials)
    assert not item(rep, "backtest_to_paper", "research_gates") and not gate_met(rep, "backtest_to_paper")
    assert "later rerun" in evidence(rep, "backtest_to_paper", "research_gates")


def test_fills_within_thirty_percent_of_the_modelled_slippage(tmp_path):
    state, trials = full_state(tmp_path)
    write_costs(state, slip_mean=0.10 * (1 + CFG.paper_fills_within) - 1e-9, prior=0.10)
    assert item(report(state, trials), "paper_to_tiny_live", "fills")
    write_costs(state, slip_mean=0.10 * (1 + CFG.paper_fills_within) + 1e-6, prior=0.10)
    assert not item(report(state, trials), "paper_to_tiny_live", "fills")


def test_chaos_drill_must_have_passed(tmp_path):
    state, trials = full_state(tmp_path)
    gates_phase.record_evidence(state, "chaos_drill", passed=False, now=NOW)
    rep = report(state, trials)
    assert not item(rep, "paper_to_tiny_live", "chaos_drill") and not gate_met(rep, "paper_to_tiny_live")


def test_shadow_record_is_shown_but_never_decides(tmp_path):
    state, trials = full_state(tmp_path)
    (state / "shadow_book.json").write_text(json.dumps({"v1": {"closed": [{"ret": -0.01, "taken": True}] * 5}}))
    rep = report(state, trials)
    g = next(x for x in rep.gates if x.gate == "paper_to_tiny_live")
    shadow = next(i for i in g.items if i.name == "shadow_record")
    assert not shadow.required and "5 closed shadow trades" in shadow.evidence and g.met


# ------------------------------------------------------------------------------------------------ gate 3: tiny live -> full
def test_live_trade_count_and_days_boundaries(tmp_path):
    state, trials = full_state(tmp_path)
    paper = [t for t in gates_phase.load_closed_trades(state)[0] if t.mode == "demo"]
    write_trades(state, paper + trades_over(150, 0.000601, 390, 10, mode="live")
                 + trades_over(CFG.live_min_trades - 151, 0.000601, 389, 11, mode="live", broker="vantage"))
    assert not item(report(state, trials), "tiny_live_to_full_size", "live_trades")
    at = LIVE_START + pd.Timedelta(days=CFG.live_min_days)
    state2, trials2 = full_state(tmp_path / "b")
    assert item(report(state2, trials2, now=at), "tiny_live_to_full_size", "live_days")
    assert not item(report(state2, trials2, now=at - pd.Timedelta(minutes=1)), "tiny_live_to_full_size", "live_days")


def test_live_expectancy_within_forty_percent_of_paper(tmp_path):
    state, trials = full_state(tmp_path)
    paper = [t for t in gates_phase.load_closed_trades(state)[0] if t.mode == "demo"]
    floor = (1 - CFG.live_expectancy_within) * 0.001001
    for factor, met in ((1.0001, True), (0.999, False)):
        write_trades(state, paper + trades_over(150, floor * factor, 390, 10, mode="live")
                     + trades_over(150, floor * factor, 389, 11, mode="live", broker="vantage"))
        assert item(report(state, trials), "tiny_live_to_full_size", "live_expectancy") is met


def test_live_drawdown_under_one_and_a_half_times_backtest(tmp_path):
    state, trials = full_state(tmp_path)
    paper = [t for t in gates_phase.load_closed_trades(state)[0] if t.mode == "demo"]
    rets = [0.0006] * 149 + [-0.01]                       # one 1% loss after the peak: live max drawdown 0.01
    write_trades(state, paper + trades_over(150, rets, 390, 10, mode="live")
                 + trades_over(150, 0.0, 389.5, 10.5, mode="live", broker="vantage"))
    write_trial(trials, max_dd=0.0067)                    # 1.5 x 0.0067 = 0.01005 > 0.01
    assert item(report(state, trials), "tiny_live_to_full_size", "live_drawdown")
    write_trial(trials, max_dd=0.0066)                    # 1.5 x 0.0066 = 0.0099 < 0.01
    assert not item(report(state, trials), "tiny_live_to_full_size", "live_drawdown")


def test_brokers_within_fifteen_percent_and_both_need_enough_trades(tmp_path):
    state, trials = full_state(tmp_path)
    paper = [t for t in gates_phase.load_closed_trades(state)[0] if t.mode == "demo"]
    for vantage, met in ((0.000601 * 0.851, True), (0.000601 * 0.849, False)):
        write_trades(state, paper + trades_over(150, 0.000601, 390, 10, mode="live")
                     + trades_over(150, vantage, 389, 11, mode="live", broker="vantage"))
        assert item(report(state, trials), "tiny_live_to_full_size", "brokers_agree") is met
    write_trades(state, paper + trades_over(300, 0.000601, 390, 10, mode="live")
                 + trades_over(CFG.broker_min_trades - 1, 0.000601, 389, 11, mode="live", broker="vantage"))
    assert not item(report(state, trials), "tiny_live_to_full_size", "brokers_agree")


# ------------------------------------------------------------------------------------------------ P6 stop rule
def losing(n: int, mean: float = -0.0005) -> list[ClosedTrade]:
    """n trades alternating around `mean` (sd 0.004): with n > 500 the one-sided 90% lower bound is below zero."""
    return [trade(mean + (0.004 if i % 2 else -0.004), days_ago=500 - i * 0.5) for i in range(n)]


def test_stop_rule_needs_eighteen_months(tmp_path):
    t = losing(501)
    months_18 = PAPER_START + pd.Timedelta(days=18 * 365.25 / 12)
    assert evaluate_stop_rule(t, PAPER_START, CAP, CFG, months_18).breached
    assert not evaluate_stop_rule(t, PAPER_START, CAP, CFG, months_18 - pd.Timedelta(hours=1)).breached


def test_stop_rule_needs_more_than_500_pooled_trades():
    assert not evaluate_stop_rule(losing(500), PAPER_START, CAP, CFG, NOW).breached
    s = evaluate_stop_rule(losing(501), PAPER_START, CAP, CFG, NOW)
    assert s.breached and s.pooled_trades == 501 and "lower 90% bound" in s.reasons[0]


def test_stop_rule_needs_the_lower_bound_below_zero():
    s = evaluate_stop_rule(losing(600, mean=0.0005), PAPER_START, CAP, CFG, NOW)    # mean - 1.28 sd/sqrt(n) > 0
    assert s.lower_bound is not None and s.lower_bound > 0 and not s.breached
    s = evaluate_stop_rule(losing(600, mean=0.0001), PAPER_START, CAP, CFG, NOW)    # positive mean, bound below zero
    assert s.lower_bound is not None and s.lower_bound < 0 and s.breached
    assert gates_phase.lower_bound([0.001], 0.9) is None


def test_stop_rule_pools_paper_and_live_since_the_paper_record():
    t = [trade(-0.0005 + (0.004 if i % 2 else -0.004), mode="live" if i % 3 == 0 else "demo", days_ago=500 - i * 0.5)
         for i in range(501)]
    assert evaluate_stop_rule(t, PAPER_START, CAP, CFG, NOW).breached
    # trades before the paper phase began (backtest_to_paper recorded later) are not pooled
    assert not evaluate_stop_rule(t, NOW - pd.Timedelta(days=300), CAP, CFG, NOW).breached


def test_single_loss_larger_than_the_weekly_cap_breaches_at_any_time():
    at_cap = trade(-0.01, pnl=-CAP * 10_000.0, days_ago=1)
    assert not evaluate_stop_rule([at_cap], None, CAP, CFG, NOW).breached
    over = trade(-0.01, pnl=-CAP * 10_000.0 - 0.01, mode="live", days_ago=1)
    s = evaluate_stop_rule([at_cap, over], None, CAP, CFG, NOW)
    assert s.breached and "exceeds the weekly cap" in s.reasons[0] and "(live)" in s.reasons[0]


def test_incident_also_fails_the_paper_and_live_gate_items(tmp_path):
    state, trials = full_state(tmp_path)
    trades = gates_phase.load_closed_trades(state)[0]
    write_trades(state, trades + [trade(-0.01, pnl=-0.06 * 10_000.0, days_ago=5)])
    rep = report(state, trials)
    assert rep.stop_rule.breached
    assert not item(rep, "paper_to_tiny_live", "no_incident") and not item(rep, "tiny_live_to_full_size", "no_incident")


# ------------------------------------------------------------------------------------------------ health
def ctx(state: Path) -> HealthContext:
    return HealthContext(state_dir=state, now=NOW, settings=SETTINGS, accounts=[AccountRef(account_id="icm-demo")],
                         get_secret=lambda k: None, disk_usage=lambda p: (100e9, 50e9, 50e9))


def test_stop_rule_health_check_fails_with_what_it_does(tmp_path):
    assert health.check_stop_rule(ctx(tmp_path)).status == "ok"
    write_trades(tmp_path, losing(501))
    write_phase(tmp_path, {"foundation_to_backtest": PAPER_START, "backtest_to_paper": PAPER_START})
    c = health.check_stop_rule(ctx(tmp_path))
    assert c.status == "fail" and c.reason.startswith("STOP RULE BREACHED")
    assert "/halt" in c.reason and "Nothing was halted automatically" in c.reason
    assert "stop_rule" in health.run_checks(ctx(tmp_path)).failing()
    for f in gates_phase.closed_trade_files(tmp_path):
        f.write_text("{not json\n")                                    # an unreadable record: warn, never "ok"
    assert health.check_stop_rule(ctx(tmp_path)).status == "warn"


def test_phase_gates_health_check_names_the_next_gate(tmp_path):
    c = health.check_phase_gates(ctx(tmp_path))
    assert c.status == "ok" and "next gate foundation_to_backtest: not met" in c.reason and "shuffle_auc" in c.reason
    for name, val in (("shuffle_auc", 0.5), ("asof_violations", 0.0), ("feed_mismatch_share", 0.0)):
        gates_phase.record_evidence(tmp_path, name, value=val, now=NOW)
    assert "MET on the evidence" in health.check_phase_gates(ctx(tmp_path)).reason


# ------------------------------------------------------------------------------------------------ never unlocks
def test_nothing_here_records_a_gate_unlocks_live_or_touches_risk_state(tmp_path, monkeypatch, capsys):
    state, trials = full_state(tmp_path)
    write_phase(state, {"foundation_to_backtest": PAPER_START - pd.Timedelta(days=60), "backtest_to_paper": PAPER_START})
    write_trades(state, gates_phase.load_closed_trades(state)[0] + losing(600)
                 + [trade(-0.01, pnl=-0.06 * 10_000.0, mode="live", days_ago=3)])   # every gate item and the stop rule
    acc_yaml = tmp_path / "accounts.yaml"
    acc_yaml.write_text("accounts:\n  icm-live:\n    enabled: false\n")
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", acc_yaml)

    def forbidden(*a: Any, **k: Any) -> None:
        raise AssertionError("gate evaluation must never record a gate, unlock live or edit accounts.yaml")
    for fn in ("unlock_live", "record_gate", "_edit_yaml_line", "set_secret"):
        monkeypatch.setattr(accounts, fn, forbidden)
    before = {p.name: p.read_bytes() for p in state.iterdir()}

    monkeypatch.setattr(gates_phase, "_registry_path", lambda s, st: trials)
    assert gates_phase.main([], state_dir=state, settings=SETTINGS, now=NOW) == 0
    assert "stop rule: BREACHED" in capsys.readouterr().out
    health.run_checks(ctx(state))

    after = {p.name: p.read_bytes() for p in state.iterdir()}
    assert set(after) - set(before) == {gates_phase.REPORT_FILE}             # the report is the only new file
    assert {k: v for k, v in after.items() if k in before} == before          # phase_state, trades, evidence unchanged
    assert not any(n.startswith(("control", "risk_", "drift")) for n in after)
    assert acc_yaml.read_text() == "accounts:\n  icm-live:\n    enabled: false\n"
    assert json.loads((state / "phase_state.json").read_text())["gates_passed"] == ["foundation_to_backtest", "backtest_to_paper"]


def test_gate_module_source_has_no_path_to_unlock_or_halt():
    src = Path(gates_phase.__file__).read_text()
    code = src.split('"""', 2)[2]                       # past the module docstring, which names what it never does
    for needle in ("unlock_live(", "record_gate(", "_edit_yaml_line", "control.json", "risk_", "halted", "set_secret"):
        assert needle not in code, needle


def test_a_closed_trade_written_by_a_newer_release_still_counts(tmp_path):
    """ADR 0010: a field a newer release added to the closed-trade record is ignored, never a skipped line."""
    t = trade(0.001)
    row = {**json.loads(t.model_dump_json()), "future_field": {"anything": 1}}
    gates_phase.closed_trade_file(tmp_path, t.account_id).write_text(json.dumps(row) + "\n")
    trades, err = gates_phase.load_closed_trades(tmp_path)
    assert err is None and trades == [t]


# ------------------------------------------------------------------------------------------------ gross rows (quant review)
# A gross row (commission and swap unknown) is kept and charged the conservative imputed cost (ops/cost_imputation.py):
# at $2,000 and no cost table, at least the largest configured commission both sides, 2 x 3.5 / 100 / 2,000 = 3.5e-5,
# plus the swap prior for any rollover a 2.4-hour hold crosses
INTRADAY_CHARGE = 2 * max(SETTINGS.costs.commission_per_lot_side_usd.values()) / 100 / 2_000.0


def paid(rows: list[ClosedTrade]) -> list[ClosedTrade]:
    """The same gross rows with each return raised by exactly its imputed cost (net of it, they read as recorded)."""
    return [t.model_copy(update={"ret": t.ret + PRIORS.charge(t).ret}) for t in rows]


def item_of(rep: GateReport, gate: str, name: str) -> gates_phase.GateItem:
    return next(i for i in next(x for x in rep.gates if x.gate == gate).items if i.name == name)


def test_gross_paper_rows_are_kept_and_charged_the_imputed_cost_in_paper_expectancy(tmp_path):
    state, trials = full_state(tmp_path)
    live = [t for t in gates_phase.load_closed_trades(state)[0] if t.mode == "live"]
    # met on the gross figure (0.001001 vs the 0.001 floor), not met once each row pays its commission
    gross = trades_over(150, 0.001001, 790, 410, gross_held_days=0.1)
    write_trades(state, gross + live)
    rep = report(state, trials)
    assert item(rep, "paper_to_tiny_live", "paper_trades")                    # nothing dropped
    assert not item(rep, "paper_to_tiny_live", "paper_expectancy")
    text = evidence(rep, "paper_to_tiny_live", "paper_expectancy")
    total = PRIORS.net_rets(gross)[1].total_ret
    assert total >= 150 * INTRADAY_CHARGE
    assert "150 gross row(s)" in text and f"{total:.5f}" in text
    # the same rows paid exactly their charge pass: the charge is what failed them
    write_trades(state, paid(gross) + live)
    assert item(report(state, trials), "paper_to_tiny_live", "paper_expectancy")


def test_a_gross_row_that_cannot_be_priced_fails_the_expectancy_items_closed(tmp_path):
    state, trials = full_state(tmp_path)
    trades = gates_phase.load_closed_trades(state)[0]
    unpriced = trade(0.01, days_ago=400.5).model_copy(update={"pnl_basis": "gross", "commission": None,
                                                              "swap": None, "entry_price": None})
    write_trades(state, trades + [unpriced])
    rep = report(state, trials)
    assert not item(rep, "paper_to_tiny_live", "paper_expectancy")
    assert "could not be priced" in evidence(rep, "paper_to_tiny_live", "paper_expectancy")


def test_gross_live_rows_are_charged_in_live_expectancy_drawdown_and_broker_agreement(tmp_path):
    state, trials = full_state(tmp_path)
    paper = [t for t in gates_phase.load_closed_trades(state)[0] if t.mode == "demo"]
    floor = (1 - CFG.live_expectancy_within) * 0.001001
    write_trades(state, paper + trades_over(150, floor * 1.0001, 390, 10, mode="live", gross_held_days=0.1)
                 + trades_over(150, floor * 1.0001, 389, 11, mode="live", broker="vantage", gross_held_days=0.1))
    rep = report(state, trials)
    assert not item(rep, "tiny_live_to_full_size", "live_expectancy")
    for name in ("live_expectancy", "live_drawdown", "brokers_agree"):
        assert "300 gross row(s)" in evidence(rep, "tiny_live_to_full_size", name), name


def test_broker_agreement_compares_the_charged_expectancy(tmp_path):
    """Two brokers that agree on the gross figure, one of them gross: after the charge they are more than 15% apart."""
    state, trials = full_state(tmp_path)
    paper = [t for t in gates_phase.load_closed_trades(state)[0] if t.mode == "demo"]
    write_trades(state, paper + trades_over(150, 0.0002, 390, 10, mode="live")
                 + trades_over(150, 0.0002, 389, 11, mode="live", broker="vantage", gross_held_days=0.1))
    assert 0.0002 * 0.15 < INTRADAY_CHARGE
    assert not item(report(state, trials), "tiny_live_to_full_size", "brokers_agree")


def test_the_gross_share_item_is_context_only_while_the_setting_is_off(tmp_path):
    state, trials = full_state(tmp_path)
    assert SETTINGS.gates.max_gross_share is None
    rep = report(state, trials)
    for gate, name in (("paper_to_tiny_live", "paper_gross_share"), ("tiny_live_to_full_size", "live_gross_share")):
        it = item_of(rep, gate, name)
        assert not it.required and "0 of" in it.evidence and "off" in it.need
    assert [g.met for g in rep.gates] == [True, True, True, True]


@pytest.mark.parametrize("n_gross,met", [(7, True), (8, False)])
def test_the_gross_share_item_decides_the_gate_when_the_owner_sets_a_limit(tmp_path, n_gross, met):
    state, trials = full_state(tmp_path)
    trades = gates_phase.load_closed_trades(state)[0]
    paper = [t for t in trades if t.mode == "demo"]
    live = [t for t in trades if t.mode == "live"]
    # 7 of 150 is 4.7% (under 5%), 8 of 150 is 5.3%; the gross rows are paid enough to keep expectancy met
    mixed = paper[n_gross:] + paid(trades_over(n_gross, 0.0011, 600, 500, gross_held_days=0.1))
    write_trades(state, mixed + live)
    settings = SETTINGS.model_copy(update={"gates": CFG.model_copy(update={"max_gross_share": 0.05})})
    rep = evaluate_gates(state, settings, NOW, trials_path=trials)
    it = item_of(rep, "paper_to_tiny_live", "paper_gross_share")
    assert it.required and it.met is met and gate_met(rep, "paper_to_tiny_live") is met
    assert f"{n_gross} of 150" in it.evidence


def test_the_incident_check_adds_the_imputed_cost_to_a_gross_loss():
    """Review (optional item): a gross loss exactly at the weekly cap breaches once its imputed cost is added (the USD
    charge read as account currency: conservative on a GBP or EUR deposit)."""
    net = trade(-0.01, pnl=-CAP * 10_000.0, days_ago=1)
    gross = trade(-0.01, pnl=-CAP * 10_000.0, days_ago=1, gross_held_days=0.1)
    assert not evaluate_stop_rule([net], None, CAP, CFG, NOW).breached
    s = evaluate_stop_rule([gross], None, CAP, CFG, NOW)
    assert s.breached and "exceeds the weekly cap" in s.reasons[0]


def test_the_stop_rule_charges_gross_rows_and_keeps_them_pooled():
    """A positive gross mean whose bound is above zero; each row held over Wednesday's triple swap pays enough that the
    charged bound falls below zero: the rule breaches on the charged figure, with every row still pooled."""
    gross = [trade(0.0005 + (0.004 if i % 2 else -0.004), days_ago=500 - i * 0.5, gross_held_days=2.0)
             for i in range(600)]
    net = [trade(t.ret, days_ago=500 - i * 0.5) for i, t in enumerate(gross)]
    assert not evaluate_stop_rule(net, PAPER_START, CAP, CFG, NOW).breached
    s = evaluate_stop_rule(gross, PAPER_START, CAP, CFG, NOW)
    assert s.breached and s.pooled_trades == 600 and "600 gross row(s)" in s.detail
