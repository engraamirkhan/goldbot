"""discovery_nightly (ADR 0090): the brain's bounded nightly search over the G2 grammar on 1h/4h/1d, steered by priors
mined from our own demo labels and the forward results of discovered arms, power-gated per ADR 0085 before any K is
spent, inside the quarter's trial budget, explore window only (the holdout is never read), with a run record in aux.db
and the discovery ledger and candidate arm rows for the registration hand-off."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.db.connection import Database
from goldbot.research.autodisc import SearchLedger
from goldbot.research.autodisc import discovery as DN
from goldbot.research.autodisc import handoff as H
from goldbot.research.autodisc import nightly as NT
from goldbot.research.autodisc.gen_gp import GPConfig, catalogue, node_key, run_gp
from goldbot.research.autodisc.nulls import SyntheticProvider, inject_edge, make_noise_tensor
from goldbot.research.autodisc.spec import ExitTemplate, Leaf, StrategySpec
from goldbot.research.autodisc.windows import WindowViolation

FAMS = ("tsmom", "nr4_break")
N = 3000
SLOT = pd.Timestamp("2026-11-18 01:00", tz="UTC")


def settings(**disc):
    s = load_settings()
    d = s.research.discovery.model_copy(update={"enabled": True, "population": 8, "generations": 2, "null_reps": 2,
                                                "max_candidates": 4, **disc})
    # the shipped 2026Q4 owner budget (tests/conftest.py strips it so other tests see the base 20)
    research = s.research.model_copy(update={"discovery": d, "trial_budget_overrides": {"2026Q4": 3500}})
    return s.model_copy(update={"research": research})


def edge_backends(settings_, timeframe):
    prov = SyntheticProvider(N, seed=0)
    mask, side = prov.family_entries("tsmom", timeframe)
    t = inject_edge(make_noise_tensor(N, seed=7, timeframe=timeframe), mask, side, edge_r=0.4)
    return NT.Backends(tensor=t, provider=prov, families=FAMS, cost_line="synthetic", mode="synthetic")


def no_obs(*_a, **_k):
    return pd.DataFrame()


def kw(tmp_path, **extra):
    base = dict(backends=edge_backends, obs_reader=no_obs, registry_path=tmp_path / "registry.jsonl",
                min_events=60, learn_years=lambda t: 0.25)
    return {**base, **extra}


# ------------------------------------------------------------------ the holdout
def test_the_holdout_is_refused_before_the_bar_file_is_opened(tmp_path):
    from goldbot.research.cost_of_record import load_research_bars
    never = tmp_path / "never_opened.parquet"            # does not exist: opening it would raise FileNotFoundError
    with pytest.raises(WindowViolation, match="holdout"):
        DN.load_explore_bars(never, lo=pd.Timestamp("2025-09-01", tz="UTC"), hi=pd.Timestamp("2025-12-01", tz="UTC"))
    with pytest.raises(WindowViolation):
        DN.load_explore_bars(never, hi=pd.Timestamp("2018-06-01", tz="UTC"))
    with pytest.raises(ValueError, match="holdout"):                 # the shared loader the discovery bars go through
        load_research_bars(never, pd.Timestamp("2025-09-01", tz="UTC"), pd.Timestamp("2025-12-01", tz="UTC"))


def test_explore_bars_hold_no_row_from_2018_on_even_when_the_file_has_holdout_rows(tmp_path):
    ts = pd.to_datetime(["2017-12-20 10:00", "2017-12-29 21:58", "2018-01-02 01:00", "2025-11-03 12:00"], utc=True)
    df = pd.DataFrame({"ts_utc": ts, "bid_close": [1.0, 2.0, 3.0, 4.0], "ask_close": [1.1, 2.1, 3.1, 4.1]})
    path = tmp_path / "bars.parquet"
    df.to_parquet(path)
    got = DN.load_explore_bars(path, lo=pd.Timestamp("2017-12-01", tz="UTC"))
    assert len(got) == 2 and pd.to_datetime(got["ts_utc"], utc=True).max() < pd.Timestamp("2018-01-01", tz="UTC")


def test_a_per_year_bar_directory_never_opens_a_file_outside_the_explore_years(tmp_path):
    ts = pd.to_datetime(["2017-03-01 10:00", "2017-03-01 10:01"], utc=True)
    pd.DataFrame({"ts_utc": ts, "bid_close": [1.0, 2.0], "ask_close": [1.1, 2.1]}).to_parquet(
        tmp_path / "xauusd_1m_dukascopy_2017.parquet")
    for year in (2018, 2025):                            # not parquet at all: opening one would raise
        (tmp_path / f"xauusd_1m_dukascopy_{year}.parquet").write_text("sealed")
    got = DN.load_explore_bars(tmp_path, lo=pd.Timestamp("2017-01-01", tz="UTC"))
    assert len(got) == 2


# ------------------------------------------------------------------ the power gate
def _gate(learn_years, **kw):
    return DN.SpecPowerGate(load_settings(), timeframe="1h", day_of_bar=np.arange(N) // 96, learn_years=learn_years,
                            now=SLOT, **kw)


def test_k_is_not_spent_on_a_spec_whose_power_is_below_the_adr_0085_floor(tmp_path):
    prov = SyntheticProvider(N, seed=0)
    led = SearchLedger(tmp_path / "low.db", mode="synthetic")
    gate = _gate(learn_years=8.0)                 # ~300 signals over 8 years: TEST-OLD n_eff far below the floor
    run = run_gp(make_noise_tensor(N, seed=1, timeframe="1h"), prov,
                 GPConfig(timeframe="1h", population=6, generations=2, seed=2, min_events=60, families=FAMS), led,
                 admit=gate)
    assert led.k_search() == 0 and led.entries() == []
    assert gate.refused == len(run.evaluated) > 0 and gate.admitted == 0
    assert all(c.evaluation.floor_failures[0].startswith("NOT STARTED (power)") for c in run.evaluated)


def test_a_spec_with_power_at_the_floor_is_searched_and_counted_in_k(tmp_path):
    led = SearchLedger(tmp_path / "hi.db", mode="synthetic")
    gate = _gate(learn_years=0.25)
    run_gp(make_noise_tensor(N, seed=1, timeframe="1h"), SyntheticProvider(N, seed=0),
           GPConfig(timeframe="1h", population=6, generations=2, seed=2, min_events=60, families=FAMS), led, admit=gate)
    assert gate.admitted > 0 and led.k_search() == gate.admitted


def test_the_power_verdict_reads_only_signal_counts_never_an_outcome():
    gate = _gate(learn_years=8.0)
    mask = np.zeros(N, bool)
    mask[::5] = True
    side = np.ones(N, np.int8)
    spec = StrategySpec(timeframe="1h", side_rule="long", entry=Leaf(kind="time", name="asia"),
                        exit=ExitTemplate(stop="atr_1.0", target="1R"))
    v1 = gate.verdict(spec, mask, side)
    v2 = gate.verdict(spec, mask, side)
    assert v1 == v2 and v1.timeframe == "1h" and v1.gates[-1].name == "test_old" and v1.gates[-1].fresh


def test_a_timeframe_no_spec_could_power_is_not_searched_at_all():
    v = DN.timeframe_reachable(load_settings(), "1d", n_bars=2080, n_days=2080, learn_years=8.0, now=SLOT)
    assert not v.started and any("n_eff" in r or "power" in r for r in v.reasons)
    assert DN.timeframe_reachable(load_settings(), "1h", n_bars=47_000, n_days=2080, learn_years=8.0, now=SLOT).started


# ------------------------------------------------------------------ priors and feedback
def test_priors_reweight_the_grammar_and_stay_inside_their_bounds():
    leaf = Leaf(kind="structure", name="family_rule", params={"rule": "tsmom"})
    key = node_key(leaf)
    base = dict((node_key(lf), w) for lf, w in catalogue(FAMS))
    tilted = dict((node_key(lf), w) for lf, w in catalogue(FAMS, priors={key: 1.8}))
    assert tilted[key] == pytest.approx(base[key] * 1.8)
    assert all(tilted[k] == base[k] for k in base if k != key)
    cfg = load_settings().research.discovery
    w = DN.prior_weights({key: DN.NodeEvidence(n=10_000, sum_r=5_000.0),
                          "time:asia": DN.NodeEvidence(n=10_000, sum_r=-5_000.0)}, cfg)
    assert w[key] == cfg.prior_max and w["time:asia"] == cfg.prior_min
    with pytest.raises(ValueError):
        GPConfig(leaf_priors={key: 0.0})


def test_demo_labels_and_forward_results_become_node_evidence():
    spec = StrategySpec(timeframe="1h", side_rule="long",
                        entry=Leaf(kind="structure", name="family_rule", params={"rule": "tsmom"}),
                        filter=Leaf(kind="regime", name="rvol_q_hi"), exit=ExitTemplate(stop="atr_1.0", target="2R"))
    t0 = pd.Timestamp("2026-11-10 09:00", tz="UTC")       # London morning
    obs = pd.DataFrame({"arm_id": ["X1", "X1", "D2611-1", "C1"], "family": ["tsmom", "tsmom", "spec_rule", "tsmom"],
                        "tf": ["1h", "1h", "1h", "1h"], "is_control": [False, False, False, True],
                        "label_sim_r": [0.5, -0.1, 1.0, 9.0], "decision_utc": [t0, t0, t0, t0]})
    ev = DN.node_evidence(obs, families=FAMS, registered={"D2611-1": spec}, timeframes=("1h",))
    assert isinstance(spec.entry, Leaf)
    fam = ev["1h"][node_key(spec.entry)]
    assert fam.n == 3 and fam.sum_r == pytest.approx(1.4)       # two demo labels + the arm's forward label; control out
    assert ev["1h"]["regime:rvol_q_hi"].n == 1                  # the arm's forward result reaches its filter node too
    assert ev["1h"]["time:london"].n == 2                       # demo labels by the session they decided in


# ------------------------------------------------------------------ the run
def test_a_dry_run_makes_no_registry_ledger_aux_or_handoff_writes(tmp_path):
    before = set(tmp_path.rglob("*"))
    run = DN.run_discovery(settings(), tmp_path, SLOT, dry_run=True, **kw(tmp_path))
    assert run.dry_run and run.k_spent == 0 and run.status == "dry_run"
    assert set(tmp_path.rglob("*")) == before
    assert {tf.timeframe for tf in run.timeframes} == {"1h", "4h", "1d"}
    assert run.budget is not None and run.budget.planned_k > 0


def test_a_run_respects_its_time_budget_and_stops_before_the_next_timeframe(tmp_path):
    t = [0.0]

    def clock():
        t[0] += 400.0                             # each look at the clock is 400 s later: the first search spends it
        return t[0]

    run = DN.run_discovery(settings(budget_s=600), tmp_path, SLOT, clock=clock, **kw(tmp_path))
    searched = [x for x in run.timeframes if x.searched]
    assert len(searched) == 1 and run.status == "budget"
    assert all("time budget" in (x.skipped or "") for x in run.timeframes if not x.searched)


T_LONDON = pd.Timestamp("2026-11-10 09:00", tz="UTC")
# 500 demo labels of +1 R on the tsmom family (the family the synthetic edge sits on): the prior steers the search to it
TSMOM_LABELS = pd.DataFrame({"arm_id": ["X1"] * 500, "family": ["tsmom"] * 500, "tf": ["1h"] * 500,
                             "is_control": [False] * 500, "label_sim_r": [1.0] * 500, "decision_utc": [T_LONDON] * 500})


def steered():
    return settings(timeframes=["1h"], population=12, generations=3, null_reps=4, max_candidates=20, seed=1)


def test_a_run_records_counts_k_survivors_and_power_verdicts_in_aux_and_the_ledger(tmp_path):
    run = DN.run_discovery(steered(), tmp_path, SLOT, executable=lambda spec: None,
                           **kw(tmp_path, obs_reader=lambda *a, **k: TSMOM_LABELS))
    assert run.status == "ok" and run.k_spent > 0
    tf = run.timeframes[0]
    assert tf.priors["structure:family_rule:tsmom"] == settings().research.discovery.prior_max
    assert tf.power.admitted > 0 and tf.power.refused > 0 and tf.k_search_tonight == run.k_spent
    assert len(run.survivors) >= 1 and run.candidate_arms == len(run.survivors)
    with Database.open(tmp_path, "aux").read() as conn:
        row = conn.execute("SELECT night, status, k_spent, survivors, body FROM discovery_runs").fetchone()
    assert row[:4] == ("2026-11-18", "ok", run.k_spent, len(run.survivors))
    body = json.loads(row[4])
    assert body["timeframes"][0]["power"]["admitted"] == tf.power.admitted
    led = tmp_path / NT.LEDGER_FILE["synthetic"]
    assert H.runs(led)[-1]["k_spent"] == run.k_spent
    offered = H.pending(led)
    assert {c.spec_hash for c in offered} == set(run.survivors)
    assert all(c.power["started"] and c.stage == "explore_survivor" and c.rule == "spec_rule" for c in offered)
    report = tmp_path / "autodisc" / "discovery-2026-11-18.md"
    assert report.exists() and "power" in report.read_text()


def test_a_survivor_that_cannot_run_live_is_recorded_but_never_handed_off(tmp_path):
    run = DN.run_discovery(steered(), tmp_path, SLOT, executable=lambda spec: "no carrier",
                           **kw(tmp_path, obs_reader=lambda *a, **k: TSMOM_LABELS))
    assert H.pending(tmp_path / NT.LEDGER_FILE["synthetic"]) == []
    assert run.not_executable == len(run.survivors) >= 1


def test_a_spec_whose_stage_read_failed_is_never_offered_again(tmp_path):
    from goldbot.research.autodisc.stages import StageJournal
    first = DN.run_discovery(steered(), tmp_path / "a", SLOT, executable=lambda spec: None,
                             **kw(tmp_path / "a", obs_reader=lambda *a, **k: TSMOM_LABELS))
    h = first.survivors[0]
    j = StageJournal(tmp_path / "b" / NT.LEDGER_FILE["synthetic"])
    j.open("confirm", h, {})
    j.close("confirm", h, {"passed": False})
    again = DN.run_discovery(steered(), tmp_path / "b", SLOT, executable=lambda spec: None,
                             **kw(tmp_path / "b", obs_reader=lambda *a, **k: TSMOM_LABELS))
    assert h in again.survivors and again.stage_closed >= 1
    assert h not in {c.spec_hash for c in H.pending(tmp_path / "b" / NT.LEDGER_FILE["synthetic"])}


def test_a_run_over_the_quarter_budget_searches_nothing(tmp_path):
    run = DN.run_discovery(settings(), tmp_path, SLOT, budget_used=lambda q: 10**6, **kw(tmp_path))
    assert run.status == "over_budget" and run.k_spent == 0
    assert not (tmp_path / NT.LEDGER_FILE["synthetic"]).exists()


def test_catalogue_cards_get_a_power_verdict_and_cost_no_k(tmp_path):
    cards = tmp_path / "cards"
    cards.mkdir()
    (cards / "x.yaml").write_text(
        "meta: {lane: test}\ncandidates:\n"
        "- {id: C-1, tf: 1h, evidence: {n: 9000, window: '2010-2017 explore'}}\n"
        "- {id: C-2, tf: 1d, evidence: {n: 120, window: '2010-2017 explore'}}\n"
        "- {id: C-3, tf: 15m, evidence: {n: 9000, window: '2010-2017 explore'}}\n"
        "- {id: C-4, tf: 4h, rule: {entry: text only}}\n")
    tri = DN.card_triage(cards, load_settings(), SLOT)
    assert tri.started == ["C-1"] and tri.refused == ["C-2"] and tri.unscorable == ["C-4"] and tri.out_of_focus == 1


def test_the_job_skips_when_off_and_fails_loudly_when_on_without_bars(tmp_path):
    assert "skipped" in DN.run_job(load_settings(), tmp_path, SLOT)
    with pytest.raises(FileNotFoundError, match="bars_path"):
        DN.run_job(settings(bars_path=None), tmp_path, SLOT)


def test_the_discovery_module_imports_nothing_that_trades():
    src = Path(DN.__file__).read_text() + Path(H.__file__).read_text()
    for banned in ("goldbot.engine", "goldbot.risk", "goldbot.execution.broker", "RiskGate", "accounts.yaml",
                   "spec_arms.yaml", "registry.preregister", "arm_rules_redbox"):
        assert banned not in src, banned


def test_cost_of_record_v3_charges_the_dearest_cell_of_the_broker():
    extra, version, line = DN.cor_v3_extra(Path("config/cost_of_record_v3.yaml"), "icm")
    assert version.startswith("cor-") and extra >= 0.9 - 1e-9 and "v3" in line
    NT.assert_no_confirm_numbers(line)


def test_handoff_rows_are_insert_only_and_registration_removes_them_from_pending(tmp_path):
    led = tmp_path / "d.db"
    SearchLedger(led, mode="synthetic")
    row = H.CandidateArm(spec_hash="a" * 64, night="2026-11-18", timeframe="1h", style="intraday",
                         spec='{"x":1}', dsr=0.1, meets_confirm_dsr=False, k_search=10, power={"started": True})
    assert H.write_candidates(led, [row]) == 1 and H.write_candidates(led, [row]) == 0
    assert [c.spec_hash for c in H.pending(led)] == ["a" * 64]
    con = sqlite3.connect(led)
    con.execute("CREATE TABLE IF NOT EXISTS arm_registrations (seq INTEGER PRIMARY KEY, arm_id TEXT, spec_hash TEXT)")
    con.execute("INSERT INTO arm_registrations (arm_id, spec_hash) VALUES ('D2611-1', ?)", ("a" * 64,))
    con.commit()
    con.close()
    assert H.pending(led) == [] and H.registered(led) == {"D2611-1": "a" * 64}


def test_a_candidate_row_a_newer_release_wrote_still_reads(tmp_path):
    led = tmp_path / "d.db"
    SearchLedger(led, mode="synthetic")
    row = H.CandidateArm(spec_hash="b" * 64, night="2026-11-18", timeframe="4h", style="swing", spec="{}", dsr=None,
                         meets_confirm_dsr=False, k_search=1, power={})
    H.write_candidates(led, [row])
    con = sqlite3.connect(led)
    newer = json.loads(row.model_dump_json()) | {"future_field": {"from": "a newer release"}}
    con.execute("UPDATE candidate_arms SET payload = ?", (json.dumps(newer),))
    con.commit()
    con.close()
    assert H.pending(led) == [row]
