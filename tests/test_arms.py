"""goldbot.research.arms: the hard holdout guard, the frozen cost model, the arm loader (ids, config hashes) and the
prior formula."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research import arms

ROOT = Path(__file__).resolve().parents[1]
ARMS = ROOT / "config" / "exploration_arms.yaml"
ARM_SPEC_IDS = {"S0", "S0R", "S1A", "S1B", "S2A", "S2B", "S3A", "S3B", "S4", "S5A", "S5B", "S6", "I0", "I1", "I2A", "I2B",
                "MB15", "MB30", "MB1H", "MSA15", "I3", "W0", "W1A", "W1B", "W2", "W3",
                # broker twins (vantage-demo, ADR 0043 broker amendment): same rule bodies, own ids
                "VI0", "VI2A", "VI2B", "VMB15", "VMB30", "VMB1H", "VMS15", "VW0", "VW1A", "VW1B", "VW2",
                # time-structure arms (prereg-2026-10-26-time-structure.md), blocked until their generators land
                "F1A", "F1C", "G1A", "G1C", "WFRI",
                # arms expansion wave 1 (arms-expansion-2026-10-26.md section 3): specialist arms and 1h controls
                "XBB15", "XTV1", "XEQ15", "XSO15", "XIMNY", "XIMLN", "XAD1H", "XMR15", "XMR1H", "C1H",
                "VXRIP1H", "VXRT1H", "VXFK1H", "VXTL1H", "VXTV2", "VXBO1H", "VXBO15", "VXTR1H", "VC1H",
                # arms expansion wave 2 (arms-2026-10-25.md section 8, 8.13, 8.14) and H-80's box family
                "F2A", "F2C", "PDC1", "PDC4", "OBR15", "KSQ1", "KSQ4", "DON4", "DOND", "C30A", "C30C", "KA15", "XC15",
                "BXR15", "BXR30", "BXR1H", "BXR4H", "BXT15", "BXT30", "BXT1H", "BXT4H",
                "BXF15", "BXF30", "BXF1H", "BXF4H",
                # H-81 scalp volume (prereg-2026-10-27-scalp-volume.md, amendments (a), (b)): 18 strata and controls,
                # 9 Vantage S-twins (blocked until Vantage's cost gate)
                "Z1P", "Z2P", "Z5P", "Z8P", "Z7P", "Z4P", "Z3D", "Z4D", "Z6D", "Z7D", "Z9D", "Z10D", "ZCD",
                "Z3V", "Z5V", "Z6V", "Z7V", "ZCV",
                "VS0", "VS1A", "VS1B", "VS2A", "VS2B", "VS3A", "VS3B", "VS5A", "VS5B",
                # ADR 0062: the XAUUSD247 weekend lane's two random controls (forward only, vantage-247)
                "WKS0", "WKI0",
                # growth sprint 2026-11-01: the owner experiment and its random-entry control (icm-demo)
                "GS1", "GS1C"}


def test_time_structure_arms_are_forward_only_and_never_run_on_history() -> None:
    loaded = {a.id: a for a in arms.load_arms(ARMS)}
    new = {"F1A", "F1C", "G1A", "G1C", "WFRI"}
    assert all(loaded[i].forward_only for i in new)      # forward-only whether blocked or running
    assert not new & set(arms.runnable_ids(ARMS))


def test_a_forward_only_arm_stays_out_of_the_history_matrix_once_it_runs(tmp_path: Path) -> None:
    def row(arm_id: str, extra: str = "") -> str:
        h = arms.arm_hash(arms.Arm(id=arm_id, rule="random_bracket", timeframe="15m"))
        return f"- {{id: {arm_id}, rule: random_bracket, timeframe: 15m, status: run{extra}, config_hash: {h}}}\n"

    p = tmp_path / "a.yaml"
    p.write_text("arms:\n" + row("A") + row("B", ", forward_only: true"))
    assert [a.id for a in arms.load_arms(p)] == ["A", "B"]          # the flag is run policy: the hash still holds
    assert arms.runnable_ids(p) == ["A"]


def test_holdout_guard_refuses_blocks_and_bars_past_the_end() -> None:
    arms.assert_block_allowed(pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2025-09-30", tz="UTC"))
    with pytest.raises(ValueError, match="holdout"):
        arms.assert_block_allowed(pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2025-10-01", tz="UTC"))
    with pytest.raises(ValueError, match="holdout"):
        arms.assert_block_allowed(pd.Timestamp("2025-10-02", tz="UTC"), pd.Timestamp("2025-12-31", tz="UTC"))
    ts = pd.date_range("2025-09-29", "2025-10-03", freq="1D", tz="UTC")
    bars = pd.DataFrame({"ts_utc": ts, "x": range(len(ts))})
    with pytest.raises(AssertionError, match="holdout"):
        arms.assert_no_holdout_rows(bars)
    cut = arms.cut_at_holdout(bars)
    assert cut["ts_utc"].max() < pd.Timestamp("2025-10-01", tz="UTC")
    arms.assert_no_holdout_rows(cut)


def test_cut_at_holdout_is_unit_safe() -> None:
    ts = pd.DatetimeIndex(pd.to_datetime(["2025-09-30 23:59:00", "2025-10-01 00:00:00"], utc=True)).as_unit("s")
    assert len(arms.cut_at_holdout(pd.DataFrame({"ts_utc": ts}))) == 1


def test_blocks_cover_2015_to_2025_09_30_in_two_year_blocks() -> None:
    b = arms.blocks()
    assert b[0][0] == "2015-01-01" and b[-1][1] == "2025-09-30" and len(b) == 6
    for (_, e), (s, _) in zip(b, b[1:], strict=False):
        assert pd.Timestamp(s) - pd.Timestamp(e) == pd.Timedelta(days=1)


def test_parse_block_rejects_garbage_and_holdout() -> None:
    assert arms.parse_block("2019-01-01:2020-12-31")[0] == pd.Timestamp("2019-01-01", tz="UTC")
    with pytest.raises(ValueError):
        arms.parse_block("2019-01-01")
    with pytest.raises(ValueError, match="holdout"):
        arms.parse_block("2025-01-01:2025-12-31")


def test_frozen_cost_model_values() -> None:
    c = arms.load_costs(ROOT / "config" / "arm_costs.yaml")
    assert c.round_trip_usd == 0.23 and c.overshoot_scenarios_usd == (0.0, 0.70)


def test_net_r_charges_round_trip_and_overshoot_on_stop_exits_only() -> None:
    c = arms.ArmCosts(round_trip_usd=0.23, overshoot_scenarios_usd=(0.0, 0.70))
    move = np.array([6.0, -3.0, 1.0])            # USD per oz, signed
    risk = np.array([3.0, 3.0, 3.0])
    stop = np.array([False, True, False])
    r0 = arms.net_r(move, risk, stop, c, overshoot_usd=0.0)
    r1 = arms.net_r(move, risk, stop, c, overshoot_usd=0.70)
    assert r0 == pytest.approx([(6 - 0.23) / 3, (-3 - 0.23) / 3, (1 - 0.23) / 3])
    assert r1[0] == r0[0] and r1[2] == r0[2] and r1[1] == pytest.approx((-3 - 0.23 - 0.70) / 3)


def test_arm_ids_are_the_arm_spec_ids_with_matching_hashes() -> None:
    loaded = arms.load_arms(ARMS)
    assert {a.id for a in loaded} == ARM_SPEC_IDS
    for a in loaded:
        assert a.config_hash == arms.arm_hash(a)


def test_a_changed_arm_fails_its_hash(tmp_path: Path) -> None:
    # an arm with no broker twin (every S arm has its Vantage twin VS*, whose body check would refuse it first)
    text = ARMS.read_text().replace("\"p\": 0.07", "\"p\": 0.08", 1)
    p = tmp_path / "a.yaml"
    p.write_text(text)
    with pytest.raises(ValueError, match="config_hash"):
        arms.load_arms(p)


def test_runnable_arms_and_blocked_reasons() -> None:
    loaded = {a.id: a for a in arms.load_arms(ARMS)}
    for blocked in ("S6", "I1", "I3", "W3"):
        assert loaded[blocked].status == "blocked" and loaded[blocked].note
    assert loaded["S0"].status == "run" and loaded["S0"].timeframe == "1m"
    assert loaded["W1B"].timeframe == "1d" and loaded["S1A"].timeframe == "5m"
    assert arms.runnable_ids(ARMS)[:2] == ["S0", "S0R"]


def test_arm_loader_rejects_duplicates_unknown_rules_and_missing_note(tmp_path: Path) -> None:
    def load(text: str) -> list[arms.Arm]:
        p = tmp_path / "a.yaml"
        p.write_text(text)
        return arms.load_arms(p, check_hash=False)

    row = "{id: A, rule: random_bracket, timeframe: 15m, params: {}}"
    with pytest.raises(ValueError, match="duplicate"):
        load(f"arms:\n- {row}\n- {row}\n")
    with pytest.raises(ValueError):
        load("arms:\n- {id: A, rule: nope, timeframe: 15m, params: {}}\n")
    with pytest.raises(ValueError, match="note"):
        load("arms:\n- {id: A, rule: blocked, timeframe: 15m, params: {}, status: blocked}\n")
    with pytest.raises(ValueError):
        load("arms:\n- {id: A, rule: random_bracket, timeframe: 7m, params: {}}\n")


def test_prior_is_half_the_oos_mean_with_weight_n_over_100_capped_at_50() -> None:
    def trades(year: int, n: int, r: float) -> pd.DataFrame:
        return pd.DataFrame({"ts_utc": pd.Timestamp(f"{year}-03-01", tz="UTC") + pd.to_timedelta(np.arange(n), "h"),
                             "net_r": r})
    df = pd.concat([trades(2016, 1000, 5.0), trades(2020, 400, 0.2), trades(2023, 400, 0.4)])
    p = arms.build_prior(df)
    assert p["n_oos"] == 800 and p["mean_r"] == pytest.approx(0.5 * 0.3) and p["weight"] == pytest.approx(8.0)
    big = arms.build_prior(trades(2021, 9000, 0.1))
    assert big["weight"] == 50.0 and big["mean_r"] == pytest.approx(0.05)
    none = arms.build_prior(trades(2016, 10, 1.0))
    assert none == {"n_oos": 0, "mean_r": 0.0, "weight": 0.0}


def test_one_registry_k_row_per_run(tmp_path: Path) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("record_arms_k_row", ROOT / "scripts" / "record_arms_k_row.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    reg = tmp_path / "registry.jsonl"
    row = mod.record(reg, {"S0": {"n_total": 5}}, "run-1", ARMS, ROOT / "config" / "arm_costs.yaml")
    assert len(reg.read_text().splitlines()) == 1 and row["family"] == "arms_matrix" and row["trial"] == 1
    assert row["config"]["arms"]["S0"] and "S6" not in row["config"]["arms"] and row["config"]["run_id"] == "run-1"


def _k_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location("record_arms_k_row", ROOT / "scripts" / "record_arms_k_row.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_h60_adds_k_24_once_and_a_rerun_adds_only_its_own_row(tmp_path: Path) -> None:
    from goldbot.research.registry import TrialRegistry
    mod = _k_mod()
    reg = tmp_path / "registry.jsonl"
    costs = ROOT / "config" / "arm_costs.yaml"
    first = mod.record(reg, {}, "run-1", ARMS, costs)
    assert first["status"] == "discovery" and first["results"]["discovery"]["k_eff"] == 24
    assert TrialRegistry(reg).n_trials_effective == 1 + 24
    again = mod.record(reg, {}, "run-2", ARMS, costs)
    assert again["results"]["discovery"]["k_eff"] == 0
    assert TrialRegistry(reg).n_trials_effective == 2 + 24        # the rerun is one look, no new arms


def test_correction_nets_h60_to_24_over_the_two_wrongly_recorded_rows(tmp_path: Path) -> None:
    from goldbot.research.registry import TrialRegistry
    mod = _k_mod()
    reg = tmp_path / "registry.jsonl"
    t = TrialRegistry(reg)
    for run in ("40187046794-1", "40191721046-1"):             # the two evaluated rows of the first dispatches
        t.record(agent_id="arms-matrix", family="arms_matrix", config={"run_id": run}, feature_version="n/a",
                 rationale="r", results={"kind": "historical_arms_matrix"})
    assert t.n_trials_effective == 2
    row = mod.correct(reg)
    assert row["status"] == "discovery" and len(row["results"]["corrects"]) == 2
    assert TrialRegistry(reg).n_trials_effective == 24            # 2 rows + the correction row + k_eff 21
    assert mod.correct(reg) is None                               # idempotent
    assert TrialRegistry(reg).n_trials_effective == 24
    # and a later run adds no more arms
    assert mod.record(reg, {}, "run-3", ARMS, ROOT / "config" / "arm_costs.yaml")["results"]["discovery"]["k_eff"] == 0


def test_correction_counts_a_prior_k_eff_zero_discovery_row(tmp_path: Path) -> None:
    from goldbot.research.registry import TrialRegistry
    mod = _k_mod()
    reg = tmp_path / "registry.jsonl"
    t = TrialRegistry(reg)
    t.record(agent_id="arms-matrix", family="arms_matrix", config={"run_id": "a"}, feature_version="n/a",
             rationale="r", results={"kind": "historical_arms_matrix"})
    t.record(agent_id="arms-matrix", family="arms_matrix", config={"run_id": "b"}, feature_version="n/a",
             status="discovery", rationale="r", results={"kind": "historical_arms_matrix", "discovery": {"k_eff": 0}})
    assert mod.correct(reg)["results"]["discovery"]["k_eff"] == 24 - 2 - 1
    assert TrialRegistry(reg).n_trials_effective == 24


def test_a_refusal_is_logged_once_per_process_at_info_then_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    """Every health run calls load_arms; the ~22 'refuses new demo entries' lines were repeated each time."""
    arms._REFUSAL_LOGGED.clear()
    with caplog.at_level("DEBUG", logger=arms.log.name):
        first = arms.load_arms(ARMS)
        n_info = sum(r.levelname == "INFO" and "refuses new demo entries" in r.message for r in caplog.records)
        caplog.clear()
        second = arms.load_arms(ARMS)
    assert n_info > 0, "the first load reports each refusal"
    assert not [r for r in caplog.records if r.levelname == "INFO" and "refuses new demo entries" in r.message]
    assert [r for r in caplog.records if r.levelname == "DEBUG" and "refuses new demo entries" in r.message]
    # qualification behaviour is unchanged (ADR 0061)
    assert [(a.id, a.entry_refused) for a in first] == [(a.id, a.entry_refused) for a in second]
