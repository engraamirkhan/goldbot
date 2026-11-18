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
                                                "max_candidates": 4, "k_cap_quarter": {"2026Q4": 1_000_000},
                                                **disc})
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
    assert gate.refused > 0 and gate.admitted == 0
    assert run.evaluated == ()                    # refused at sampling (redrawn), never evaluated


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


def READABLE(*_a):
    """A kill rule that can read every survivor."""
    return None


def steered():
    return settings(timeframes=["1h"], population=12, generations=3, null_reps=4, max_candidates=20, seed=1)


def test_a_run_records_counts_k_survivors_and_power_verdicts_in_aux_and_the_ledger(tmp_path):
    run = DN.run_discovery(steered(), tmp_path, SLOT, executable=lambda spec: None, kill_unreadable=READABLE,
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
    first = DN.run_discovery(steered(), tmp_path / "a", SLOT, executable=lambda spec: None, kill_unreadable=READABLE,
                             **kw(tmp_path / "a", obs_reader=lambda *a, **k: TSMOM_LABELS))
    h = first.survivors[0]
    j = StageJournal(tmp_path / "b" / NT.LEDGER_FILE["synthetic"])
    j.open("confirm", h, {})
    j.close("confirm", h, {"passed": False})
    again = DN.run_discovery(steered(), tmp_path / "b", SLOT, executable=lambda spec: None, kill_unreadable=READABLE,
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


# ------------------------------------------------------------------ review round 1 (quant B1-B3, M1, M2; sre d; code review 3)
def without_q4_override(**disc):
    s = settings(**disc)
    return s.model_copy(update={"research": s.research.model_copy(update={"trial_budget_overrides": {}})})


def budget_of(run: DN.DiscoveryRun) -> DN.BudgetLine:
    assert run.budget is not None
    return run.budget


def test_the_registry_check_counts_one_row_per_timeframe_not_the_planned_spec_k(tmp_path):
    line = DN.budget_line(settings(), SLOT, lambda q: 0, lambda q: 0)
    assert line.units == 3 and line.refused is None              # three timeframes -> at most three batch rows


def test_without_the_2026q4_override_the_night_is_over_budget(tmp_path):
    run = DN.run_discovery(without_q4_override(), tmp_path, SLOT, budget_used=lambda q: 158, **kw(tmp_path))
    assert run.status == "over_budget" and run.k_spent == 0 and "trial budget" in (budget_of(run).refused or "")


def test_a_quarter_the_k_cap_map_does_not_list_refuses_the_night(tmp_path):
    q2 = pd.Timestamp("2027-04-10 01:00", tz="UTC")
    assert "2027Q2" not in load_settings().research.discovery.k_cap_quarter
    run = DN.run_discovery(settings(), tmp_path, q2, budget_used=lambda q: 0, **kw(tmp_path))
    assert run.status == "over_budget" and "2027Q2 is not listed" in (budget_of(run).refused or "")
    assert not (tmp_path / NT.LEDGER_FILE["synthetic"]).exists()


def test_the_research_directors_q4_cap_is_read_and_q1_stays_closed():
    """docs/research/discovery-k-cap-2026-11-18.md: 2026Q4 15,000; 2027Q1 0 until a written Q4 read moves it."""
    cfg = load_settings().research.discovery
    assert cfg.k_cap_quarter == {"2026Q4": 15_000, "2027Q1": 0}
    assert cfg.k_cap_for("2026Q4") == 15_000 and cfg.k_cap_for("2027Q1") == 0 and cfg.k_cap_for("2027Q2") == 0
    line = DN.budget_line(load_settings(), SLOT, lambda q: 0, lambda q: 0)
    assert line.k_cap == 15_000 and line.refused is None


@pytest.mark.parametrize("bad", [{"2026-Q4": 10}, {"2026Q5": 10}, {"2026Q4": -1}, {"2026Q4": 2_000_000}])
def test_the_k_cap_map_only_takes_quarters_and_bounded_values(bad):
    from goldbot.config import DiscoverySettings
    with pytest.raises(ValueError):
        DiscoverySettings(k_cap_quarter=bad)


def real_edge_backends(settings_, timeframe):
    return edge_backends(settings_, timeframe).model_copy(update={"mode": "real"})


def test_two_nights_k_accumulate_against_the_quarters_k_cap(tmp_path, monkeypatch):
    import goldbot.research.registry as REG
    monkeypatch.setattr(REG, "_utcnow", lambda: SLOT.to_pydatetime())       # batch rows stamped in the slot's quarter
    s = steered()
    cap = DN.planned_k(s.research.discovery) + 1                             # room for one night at its bound
    s = s.model_copy(update={"research": s.research.model_copy(update={
        "discovery": s.research.discovery.model_copy(update={"k_cap_quarter": {"2026Q4": cap, "2027Q1": 0}})})})
    k = kw(tmp_path, backends=real_edge_backends)
    first = DN.run_discovery(s, tmp_path, SLOT, **k)
    assert first.status == "ok" and first.k_spent > 1 and budget_of(first).k_used == 0
    second = DN.run_discovery(s, tmp_path, SLOT + pd.Timedelta(days=1), **k)
    assert budget_of(second).k_used == first.k_spent                             # the prior-search seed is not this spend
    assert second.status == "over_budget" and "k_cap_quarter" in (budget_of(second).refused or "")


V3 = Path("config/cost_of_record_v3.yaml")


def test_v3_has_no_spread_split_so_labels_are_mid_and_each_full_cell_is_charged_in_bps():
    c = DN.cor_v3_cells(V3, "icm", price_usd=4000.0)
    assert not c.split
    assert c.bps["newyork"]["target"] == pytest.approx(0.9 / 4000.0 * 1e4)    # 2.25 bp, not 0.90 $/oz on 2010-17 prices
    assert set(c.bps) == {"asia", "london", "newyork"} and set(c.bps["asia"]) == {"stop", "target", "time"}
    assert "mid labels" in c.line and "swap prior" in c.line and "not measured" in c.line
    NT.assert_no_confirm_numbers(c.line)


def test_a_cost_table_with_a_spread_split_keeps_bid_ask_labels_and_charges_only_the_non_spread_part(tmp_path):
    cell = "{charged_usd: 0.50, spread_usd: 0.30, commission_usd: 0.08}"
    thin = "{charged_usd: 0.35, spread_usd: 0.30, commission_usd: 0.08}"      # 0.05 left: floored at the commission
    sess = "{stop: %s, target: %s, time: %s, all: %s}" % (cell, thin, cell, cell)
    (tmp_path / "v.yaml").write_text(f"version: v-test\ncells:\n  icm:\n    asia: {sess}\n    london: {sess}\n"
                                     f"    newyork: {sess}\n    all: {sess}\n")
    c = DN.cor_v3_cells(tmp_path / "v.yaml", "icm", price_usd=2000.0)
    assert c.split and c.bps["london"]["stop"] == pytest.approx(0.2 / 2000.0 * 1e4)
    assert c.bps["london"]["target"] == pytest.approx(0.08 / 2000.0 * 1e4)
    assert "bid/ask labels" in c.line


def test_each_label_is_charged_the_cell_of_its_session_and_exit():
    c = DN.CostCells(version="v", broker="icm", price_usd=1.0, split=False, line="x",
                     bps={"asia": {"stop": 1.0, "target": 2.0, "time": 3.0},
                          "london": {"stop": 4.0, "target": 5.0, "time": 6.0},
                          "newyork": {"stop": 7.0, "target": 8.0, "time": 9.0}})
    lab = pd.DataFrame({"ts_utc": pd.to_datetime(["2015-03-04 09:00", "2015-03-04 15:00", "2015-03-04 02:00",
                                                  "2015-03-04 09:00"], utc=True),
                        "barrier_hit": ["stop", "target", "time", "trail"]})
    got = DN.cell_charge(c)(lab) * 1e4
    assert got.tolist() == pytest.approx([4.0, 8.0, 3.0, 4.0])                 # a trail exit is a stop order


@pytest.fixture(scope="module")
def b1_2015():
    from goldbot.data.resample import ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    return ticks_to_1m(synthetic_ticks("2015-03-02", "2015-04-10", ticks_per_minute=2, seed=3))


def test_net_r_is_the_mid_r_less_one_cell_charge_so_the_spread_is_charged_once(b1_2015):
    from goldbot.execution.costs import settings_swap
    swap = settings_swap(load_settings())
    c = DN.CostCells(version="v", broker="icm", price_usd=1.0, split=False, line="x",
                     bps={s: {e: 5.0 for e in ("stop", "target", "time")} for s in ("asia", "london", "newyork")})
    mid = NT.backends_from_1m(b1_2015, "1h", extra_cost_usd=0.0, swap=swap, cost_line="", mid_labels=True).tensor
    net = NT.backends_from_1m(b1_2015, "1h", extra_cost_usd=0.0, swap=swap, cost_line="", mid_labels=True,
                              extra_cost_frac=DN.cell_charge(c)).tensor
    bid_ask = NT.backends_from_1m(b1_2015, "1h", extra_cost_usd=0.0, swap=swap, cost_line="").tensor
    ok = np.isfinite(mid.ret) & np.isfinite(net.ret)
    assert ok.sum() > 100
    np.testing.assert_allclose((net.ret - mid.ret)[ok], -5e-4)                # one cell charge, nothing else
    np.testing.assert_allclose(((net.ret - mid.ret) / net.risk)[ok], (-5e-4 / net.risk)[ok])
    assert np.nanmean(mid.ret - bid_ask.ret) > 0                              # bid/ask labels pay the spread: not used


def test_bar_backends_label_at_the_mid_and_charge_the_v3_cells(tmp_path, b1_2015):
    b1_2015.to_parquet(tmp_path / "xauusd_1m_dukascopy_2015.parquet")
    b = DN.BarBackends(settings(bars_path=str(tmp_path)))
    got = b(settings(), "4h")
    assert not b.cells.split and "swap prior" in got.cost_line
    want = NT.backends_from_1m(DN.load_explore_bars(tmp_path), "4h", extra_cost_usd=0.0, swap=b.swap, cost_line="",
                               mid_labels=True, extra_cost_frac=DN.cell_charge(b.cells)).tensor
    np.testing.assert_array_equal(got.tensor.ret, want.ret)


def reads_ledger_with(path: Path, families):
    from goldbot.research.eval2.runner import BATCH_P_KIND
    path.write_text(json.dumps({"kind": BATCH_P_KIND, "read_window": "test_old", "p": [[f, 0.4] for f in families]})
                    + "\n")
    return path


def test_a_reads_ledger_that_read_g2_on_test_old_admits_no_spec(tmp_path):
    reads = reads_ledger_with(tmp_path / "catalogue_reads.jsonl", ["autodisc:G2"])
    run = DN.run_discovery(steered(), tmp_path, SLOT, reads_ledger=reads, **kw(tmp_path))
    assert run.k_spent == 0 and all(not x.searched and x.power.admitted == 0 for x in run.timeframes)
    assert all("TEST-OLD" in (x.skipped or "") for x in run.timeframes)
    gate = _gate(learn_years=0.25, closed=DN.test_old_spent(tmp_path / "none.jsonl", reads))
    mask = np.ones(N, bool)
    assert gate(StrategySpec(timeframe="1h", side_rule="long", entry=Leaf(kind="time", name="asia"),
                             exit=ExitTemplate(stop="atr_1.0", target="1R")), mask, np.ones(N, np.int8))
    assert gate.admitted == 0 and gate.reasons["TEST-OLD spent"] == 1


def test_another_familys_test_old_read_leaves_g2_open(tmp_path):
    reads = reads_ledger_with(tmp_path / "catalogue_reads.jsonl", ["tsmom"])
    assert DN.test_old_spent(tmp_path / "registry.jsonl", reads) is None


def test_a_prior_promotion_read_of_g2_on_test_old_admits_no_spec(tmp_path):
    reg = tmp_path / "registry.jsonl"
    reg.write_text(json.dumps({"status": "promotion_confirm", "family": "autodisc:G2",
                               "results": {"promotion_confirm": {"source": "test_old"}}}) + "\n")
    assert "once per family" in (DN.test_old_spent(reg, tmp_path / "no_reads.jsonl") or "")
    run = DN.run_discovery(steered(), tmp_path, SLOT, reads_ledger=tmp_path / "no_reads.jsonl", **kw(tmp_path))
    assert run.k_spent == 0 and not any(x.searched for x in run.timeframes)


def test_the_forward_verdict_is_reported_never_a_gate_and_its_constants_match_the_engine():
    """Quant ruling (a), 2026-11-18: the forward window over the learning kill rule cannot reach the ADR 0085 floor
    (n_eff <= 128 < 200), so its verdict is reported on the candidate row, not evidence and not a gate."""
    from goldbot.engine.exploration_learning import LEARNING_READ_FILLS
    from goldbot.research.arms import LEARNING_MAX_DAYS
    assert DN.LEARNING_READ_FILLS == LEARNING_READ_FILLS and LEARNING_MAX_DAYS == 45
    v = DN.forward_verdict(load_settings(), "1h", signals=47_000, clusters=2080, learn_years=8.0)
    assert not v.started and v.gates[-1].name == "forward" and v.gates[-1].fresh and v.gates[-1].n_eff < 200


def test_the_hand_off_refuses_an_unreadable_kill_and_passes_a_readable_one():
    why = DN.kill_unreadable_rule()
    assert why("1h", 2_080, 2_080, 8.0) is not None and "50" in (why("1h", 2_080, 2_080, 8.0) or "")   # ~30 by until
    assert "signal days" in (why("1h", 47_000, 400, 8.0) or "")                      # ~6 signal days by until
    assert why("1h", 47_000, 2_080, 8.0) is None                                     # ~675 candidates, ~30 days


def test_a_survivor_whose_kill_rule_cannot_read_it_is_never_offered(tmp_path):
    never = DN.run_discovery(steered(), tmp_path / "a", SLOT, executable=lambda spec: None,
                             kill_unreadable=lambda *a: "too few", **kw(tmp_path / "a",
                                                                     obs_reader=lambda *a, **k: TSMOM_LABELS))
    assert len(never.survivors) >= 1 and never.candidate_arms == 0 and never.kill_unreadable == len(never.survivors)
    assert H.pending(tmp_path / "a" / NT.LEDGER_FILE["synthetic"]) == []
    shipped = DN.run_discovery(steered(), tmp_path / "b", SLOT, executable=lambda spec: None,
                               **kw(tmp_path / "b", obs_reader=lambda *a, **k: TSMOM_LABELS))
    offered = H.pending(tmp_path / "b" / NT.LEDGER_FILE["synthetic"])
    assert shipped.candidate_arms == len(offered) >= 1 and shipped.kill_unreadable == 0
    assert all(c.power_forward["reading"] == "reported, not evidence" and "started" in c.power_forward
               for c in offered)


def test_power_admission_is_outcome_blind(tmp_path):
    """Admission reads entry masks and sides only: with the labels permuted or NaN, the same specs are admitted."""
    import dataclasses
    t = make_noise_tensor(N, seed=1, timeframe="1h")
    rng = np.random.default_rng(0)
    variants = {"real": t,
                "permuted": dataclasses.replace(t, ret=rng.permutation(t.ret), risk=rng.permutation(t.risk),
                                                cost=rng.permutation(t.cost), t_exit=rng.permutation(t.t_exit)),
                "nan": dataclasses.replace(t, ret=np.full_like(t.ret, np.nan), risk=np.full_like(t.risk, np.nan),
                                           cost=np.full_like(t.cost, np.nan), t_exit=np.full_like(t.t_exit, -1))}
    seen = {}
    for name, tt in variants.items():
        gate = _gate(learn_years=0.25)
        run = run_gp(tt, SyntheticProvider(N, seed=0), GPConfig(timeframe="1h", population=8, generations=1, seed=3,
                                                                min_events=60, families=FAMS),
                     SearchLedger(tmp_path / f"{name}.db", mode="synthetic"), admit=gate)
        seen[name] = [c.spec_hash for c in run.evaluated if c.generation == 0]
        assert gate.refused > 0 and gate.admitted > 0, name          # the gate both refused and admitted: not vacuous
    assert seen["real"] and seen["real"] == seen["permuted"] == seen["nan"]


def test_prior_search_seed_is_outside_quarter_k_but_inside_n_trials_discovery(tmp_path, monkeypatch):
    import goldbot.research.registry as REG
    from goldbot.research.autodisc.k_link import GRAMMAR_V1_K, ensure_prior_searches
    monkeypatch.setattr(REG, "_utcnow", lambda: SLOT.to_pydatetime())
    reg = REG.TrialRegistry(tmp_path / "registry.jsonl")
    ensure_prior_searches(reg)
    reg.record_discovery_batch(k_contrib=123, generator="G2 gp", ledger_head=(1, "x"), ledger_id="L1")
    assert DN.quarter_k_used(reg.path)("2026Q4") == 123
    assert reg.n_trials_discovery == reg.n_trials_effective + GRAMMAR_V1_K + 123


def test_a_demo_label_from_the_holdout_year_has_no_effect_on_the_priors(tmp_path):
    seeded = pd.DataFrame({"arm_id": ["X9"] * 400, "family": ["nr4_break"] * 400, "tf": ["1h"] * 400,
                           "is_control": [False] * 400, "label_sim_r": [3.0] * 400,
                           "decision_utc": [pd.Timestamp("2026-06-10 09:00", tz="UTC")] * 400})
    s = settings(timeframes=["1h"])
    clean = DN.run_discovery(s, tmp_path / "a", SLOT, dry_run=True,
                             **kw(tmp_path / "a", obs_reader=lambda *a, **k: TSMOM_LABELS))
    mixed = DN.run_discovery(s, tmp_path / "b", SLOT, dry_run=True, **kw(
        tmp_path / "b", obs_reader=lambda *a, **k: pd.concat([TSMOM_LABELS, seeded], ignore_index=True)))
    assert clean.timeframes[0].priors == mixed.timeframes[0].priors
    assert "structure:family_rule:nr4_break" not in mixed.timeframes[0].priors


def test_each_judged_candidate_reports_how_many_plateau_neighbours_were_admitted(tmp_path):
    from goldbot.research.autodisc.gen_gp import neighbours
    from goldbot.research.autodisc.spec import spec_hash

    def admit(spec, mask, side):                  # outcome-blind: refuses about half the specs by their hash
        return [] if int(spec_hash(spec)[-1], 16) % 2 == 0 else ["odd"]

    b = edge_backends(None, "1h")
    led = SearchLedger(tmp_path / "n.db", mode="synthetic")
    r = NT.search_and_judge(b.tensor, b.provider, led, GPConfig(timeframe="1h", population=12, generations=3, seed=1,
                                                                min_events=60, families=FAMS),
                            reps=2, max_candidates=20, admit=admit)
    judged = [(c.spec_hash, c.neighbours_admitted, c.neighbours_total) for c in r.survivors] + [
        (c.spec_hash, c.neighbours_admitted, c.neighbours_total) for c in r.rejected]
    assert judged
    for h, admitted, total in judged:
        spec = led.get_spec(h)
        assert spec is not None
        nbs = neighbours(spec, "v1")
        assert total == len(nbs) and admitted == sum(not admit(nb, None, None) for nb in nbs)
    assert "neighbours admitted" in NT.render(r)


def test_refused_specs_are_redrawn_at_sampling_so_search_and_null_evaluate_only_admitted_specs(tmp_path):
    from goldbot.research.autodisc.gen_gp import gp_null
    from goldbot.research.autodisc.spec import spec_hash

    def admit(spec, mask, side):
        return [] if int(spec_hash(spec)[-1], 16) % 2 == 0 else ["odd"]

    led = SearchLedger(tmp_path / "r.db", mode="synthetic")
    cfg = GPConfig(timeframe="1h", population=10, generations=2, seed=4, min_events=60, families=FAMS)
    t, prov = make_noise_tensor(N, seed=1, timeframe="1h"), SyntheticProvider(N, seed=0)
    run = run_gp(t, prov, cfg, led, admit=admit)
    assert sum(c.generation == 0 for c in run.evaluated) == cfg.population      # refused draws replaced, not lost
    assert all(not admit(c.spec, None, None) for c in run.evaluated)
    gp_null(t, prov, cfg, led, reps=2, admit=admit)
    assert all(int(e.spec_hash[-1], 16) % 2 == 0 for e in led.entries())     # the null inherits the redraw


def test_an_over_budget_night_writes_the_report_its_summary_points_to(tmp_path):
    run = DN.run_discovery(settings(), tmp_path, SLOT, budget_used=lambda q: 10**6, **kw(tmp_path))
    path = DN.summary(run)["report"]
    assert path is not None and (tmp_path / path).exists() and "over_budget" in (tmp_path / path).read_text()


def test_a_second_run_is_refused_while_another_holds_the_discovery_lock(tmp_path):
    import fcntl
    lock = tmp_path / "autodisc" / "discovery.lock"
    lock.parent.mkdir(parents=True)
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="lock"):
            DN.run_job(settings(bars_path=str(tmp_path)), tmp_path, SLOT)
    with pytest.raises(FileNotFoundError, match="bars_path"):          # released: the run gets past the lock
        DN.run_job(settings(bars_path=None), tmp_path, SLOT)


def test_the_foreground_dry_run_prints_its_peak_rss_for_the_deploy_note(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(DN, "run_discovery", lambda *a, **k: DN.DiscoveryRun(night="2026-11-18", dry_run=True,
                                                                            status="dry_run", timeframes=[]))
    assert DN.main(["--state-dir", str(tmp_path), "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["max_rss_mb"] > 0 and out["report"] is None


def test_power_refusals_are_counted_and_reported_never_booked_as_k(tmp_path):
    run = DN.run_discovery(steered(), tmp_path, SLOT, **kw(tmp_path, obs_reader=lambda *a, **k: TSMOM_LABELS))
    assert run.n_refused_power == sum(x.power.refused for x in run.timeframes) > 0
    assert run.k_spent == SearchLedger(tmp_path / NT.LEDGER_FILE["synthetic"], mode="synthetic").k_search()
    with Database.open(tmp_path, "aux").read() as conn:
        body = json.loads(conn.execute("SELECT body FROM discovery_runs").fetchone()[0])
    assert body["n_refused_power"] == run.n_refused_power
    text = (tmp_path / "autodisc" / "discovery-2026-11-18.md").read_text()
    assert f"power-gate refusals {run.n_refused_power:,} (not K)" in text
    assert DN.summary(run)["n_refused_power"] == run.n_refused_power


def test_no_v3_cell_is_charged_below_its_commission():
    import yaml as Y
    c = DN.cor_v3_cells(V3, "icm", price_usd=4000.0)
    cells = Y.safe_load(V3.read_text())["cells"]["icm"]
    for sess, by in c.bps.items():
        for e, bps in by.items():
            assert bps >= cells[sess][e]["commission_usd"] / 4000.0 * 1e4 - 1e-12


def test_a_demo_label_frame_without_decision_utc_gives_the_uniform_priors(tmp_path):
    s = settings(timeframes=["1h"])
    uniform = DN.run_discovery(s, tmp_path / "a", SLOT, dry_run=True, **kw(tmp_path / "a"))
    undated = DN.run_discovery(s, tmp_path / "b", SLOT, dry_run=True, **kw(
        tmp_path / "b", obs_reader=lambda *a, **k: TSMOM_LABELS.drop(columns=["decision_utc"])))
    assert undated.timeframes[0].priors == uniform.timeframes[0].priors == {}


def test_a_killed_nights_unsynced_k_counts_against_the_next_nights_cap(tmp_path, monkeypatch):
    """A night killed before `sync_discovery_k` leaves its K in the ledger only; the next night's cap check counts it."""
    import goldbot.research.registry as REG
    monkeypatch.setattr(REG, "_utcnow", lambda: SLOT.to_pydatetime())
    led = SearchLedger(tmp_path / NT.LEDGER_FILE["real"], mode="real")
    rng = np.random.default_rng(0)
    from goldbot.research.autodisc.gen_gp import GENERATOR, random_spec
    cfg = GPConfig(timeframe="1h", population=8, generations=1, seed=0, families=FAMS)
    cat = catalogue(FAMS)
    while led.k_search() < 40:                                  # the killed night's specs, ledgered, never synced
        led.log_spec(random_spec(rng, cfg, cat), GENERATOR, "v")
    s = steered()
    cap = DN.planned_k(s.research.discovery) + 39               # room for the night, not for the unsynced 40 on top
    s = s.model_copy(update={"research": s.research.model_copy(update={
        "discovery": s.research.discovery.model_copy(update={"k_cap_quarter": {"2026Q4": cap}})})})
    run = DN.run_discovery(s, tmp_path, SLOT, **kw(tmp_path, backends=real_edge_backends))
    assert run.status == "over_budget" and budget_of(run).k_unsynced == 40
    assert DN.unsynced_k(tmp_path, tmp_path / "registry.jsonl") == 40
