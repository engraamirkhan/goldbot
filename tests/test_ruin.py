"""Drawdown and risk-of-ruin Monte Carlo (playbook G-5, BACKLOG item 20): analytic cases, the gate's sizing, the block
bootstrap and the inputs it reads."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.research.ruin import (
    ParametricR,
    RuinConfig,
    RuinLimits,
    RuinReport,
    block_bootstrap,
    format_report,
    gamblers_ruin_probability,
    longest_run,
    main,
    r_from_closed_trades,
    r_from_shadow_book,
    simulate,
)
from goldbot.risk.gate import RiskLimits
from goldbot.risk.sizing import effective_risk

# loose limits so a test can switch on only the threshold it is about
_OFF: dict[str, Any] = dict(daily_cap=0.99, weekly_cap=0.99, supervisor_daily_cap=0.99, supervisor_weekly_cap=0.99,
                            drawdown_stage1=0.98, drawdown_stage2=0.99, drawdown_stage1_clear=0.5,
                            max_risk_per_trade=0.01, multiplier_bounds=(0.25, 1.5))


def _limits(**kw: Any) -> RuinLimits:
    return RuinLimits(**{"risk_per_trade": 0.01, **_OFF, **kw})


def _live() -> RuinLimits:
    return RuinLimits.from_settings(load_settings().risk)


def _trip(rep: RuinReport, name: str):
    return next(t for t in rep.thresholds if t.name == name)


def test_limits_come_from_the_gate_and_supervisor_limits_built_from_settings():
    risk = load_settings().risk
    lim = RuinLimits.from_settings(risk)
    assert (lim.risk_per_trade, lim.drawdown_stage1, lim.drawdown_stage2) == (risk.risk_per_trade, 0.08, 0.12)
    assert (lim.daily_cap, lim.weekly_cap) == (risk.daily_cap, risk.weekly_cap)
    assert (lim.supervisor_daily_cap, lim.supervisor_weekly_cap) == (risk.supervisor_daily_cap, risk.supervisor_weekly_cap)
    assert lim.max_risk_per_trade == RiskLimits().max_risk_per_trade
    assert tuple(lim.multiplier_bounds) == tuple(risk.multiplier_bounds)
    assert RuinLimits.from_settings(risk, tiny_live=True).risk_per_trade == risk.risk_per_trade_tiny_live


def test_all_winning_trades_never_draw_down_or_trip_anything():
    cfg = RuinConfig(limits=_live(), trades_per_week=10, weeks=52, n_paths=200)
    rep = simulate(cfg, r=np.ones(50))
    assert rep.max_drawdown.p99 == 0 and rep.risk_of_ruin == 0
    assert all(p == 0 for t in rep.thresholds for p in t.p_by_weeks.values())
    assert rep.final_equity.median == pytest.approx(1.005 ** 520)


def test_all_losing_trades_hit_the_eight_percent_stage_on_the_exact_trade():
    # 0.99^8 = 0.9227 (7.7% down), 0.99^9 = 0.9135 (8.6%): the 9th loss trips stage 1 at 9/10 = 0.9 weeks
    cfg = RuinConfig(limits=_limits(drawdown_stage1=0.08), trades_per_week=10, weeks=4, n_paths=50,
                     horizons_weeks=(1,), apply_rules=False)
    rep = simulate(cfg, r=-np.ones(20))
    st1 = _trip(rep, "drawdown_stage1")
    assert st1.p_by_weeks[1] == 1.0 and st1.median_weeks_to_first == pytest.approx(0.9)
    assert rep.max_drawdown.median == pytest.approx(1 - 0.99 ** 40)


def test_daily_cap_stops_the_day_and_the_next_day_starts_again():
    # 20 trades a week = 4 a day; at 1% risk one loss is 1% (< 1.5%) and two are 1.99%, so the cap trips on the
    # second loss of each day and the 3rd and 4th trades are skipped
    lim = _limits(daily_cap=0.015)
    rep = simulate(RuinConfig(limits=lim, trades_per_week=20, weeks=1, n_paths=10, horizons_weeks=(1,)), r=-np.ones(5))
    assert _trip(rep, "daily_cap").p_by_weeks[1] == 1.0
    assert rep.mean_trades_taken == 10
    assert rep.final_equity.median == pytest.approx(0.99 ** 10)


def test_stage_one_quarters_the_risk_like_the_gate_and_stage_two_halts_the_path():
    # gate: SIZE_DOWN halves risk per trade AND caps the multiplier at 0.5, so 1% becomes 0.25% at m = 1
    lim = _limits(drawdown_stage1=0.08, drawdown_stage2=0.12)
    rep = simulate(RuinConfig(limits=lim, trades_per_week=100, weeks=1, n_paths=10, horizons_weeks=(1,)), r=-np.ones(5))
    k = math.ceil(math.log(0.88 / 0.99 ** 9) / math.log(0.9975))
    assert rep.risk_normal == pytest.approx(0.01) and rep.risk_size_down == pytest.approx(0.0025)
    assert rep.mean_trades_taken == 9 + k
    assert _trip(rep, "drawdown_stage2").p_by_weeks[1] == 1.0
    assert rep.final_equity.median == pytest.approx(0.99 ** 9 * 0.9975 ** k)


@pytest.mark.parametrize("mult,risk", [(1.0, 0.005), (1.5, 0.005), (0.1, 0.005), (3.0, 0.008), (1.5, 0.001)])
def test_the_multiplier_goes_through_the_gate_arithmetic(mult: float, risk: float):
    lim = _live().model_copy(update={"risk_per_trade": risk})
    rep = simulate(RuinConfig(limits=lim, trades_per_week=5, weeks=2, n_paths=10, multiplier=mult,
                              horizons_weeks=(2,)), r=np.ones(10))
    gl = RiskLimits(risk_per_trade=risk)
    assert rep.risk_normal == pytest.approx(effective_risk(gl, False, mult).target)
    assert rep.risk_size_down == pytest.approx(effective_risk(gl, True, mult).target)


def test_gamblers_ruin_formula():
    assert gamblers_ruin_probability(0.5, 3, 10) == pytest.approx(0.7)
    assert gamblers_ruin_probability(0.55, 10, None) == pytest.approx((0.45 / 0.55) ** 10)
    assert gamblers_ruin_probability(0.45, 10, None) == 1.0
    p, r = 0.6, 0.4 / 0.6
    assert gamblers_ruin_probability(p, 2, 5) == pytest.approx((r ** 2 - r ** 5) / (1 - r ** 5))


def test_monte_carlo_matches_gamblers_ruin_for_a_plus_minus_one_r_walk():
    # fixed-dollar risk of 1% of the start, ruin at 10% down = 10 units, p = 0.55: (0.45/0.55)^10 = 0.134
    cfg = RuinConfig(limits=_limits(), trades_per_week=100, weeks=40, n_paths=8000, horizons_weeks=(40,),
                     apply_rules=False, compounding=False, ruin_level=0.10, seed=7)
    rep = simulate(cfg, parametric=ParametricR(win_rate=0.55, avg_win_r=1.0, avg_loss_r=1.0))
    assert rep.risk_of_ruin == pytest.approx(gamblers_ruin_probability(0.55, 10, None), abs=0.015)


def test_same_seed_gives_the_same_report_and_another_seed_does_not():
    r = np.random.default_rng(1).normal(0.1, 1.0, 300)
    cfg = RuinConfig(limits=_live(), trades_per_week=8, weeks=26, n_paths=500, horizons_weeks=(13, 26), seed=3)
    a, b = simulate(cfg, r=r), simulate(cfg, r=r)
    assert a.model_dump_json() == b.model_dump_json()
    assert simulate(cfg.model_copy(update={"seed": 4}), r=r).model_dump_json() != a.model_dump_json()


def test_bootstrap_with_a_whole_sample_block_keeps_every_streak():
    r = np.array([-1.0] * 6 + [2.0] * 4 + [-1.0] * 3 + [1.0] * 7)
    paths = block_bootstrap(r, n_paths=50, n_trades=20, block_length=20, rng=np.random.default_rng(0))
    for row in paths:   # every path is a rotation of the sample, so the circular loss streaks are the sample's
        assert sorted(row) == sorted(r)
        assert longest_run(np.concatenate([row, row]) < 0) >= 6


def test_block_bootstrap_preserves_loss_streaks_that_iid_resampling_breaks():
    r = np.tile(np.array([-1.0] * 12 + [1.5] * 12), 7)            # streaks of 12 losses
    rng = np.random.default_rng(0)
    blocks = block_bootstrap(r, n_paths=400, n_trades=168, block_length=24, rng=rng)
    iid = block_bootstrap(r, n_paths=400, n_trades=168, block_length=1, rng=rng)
    streak_b = np.mean([longest_run(p < 0) for p in blocks])
    streak_i = np.mean([longest_run(p < 0) for p in iid])
    assert streak_b >= 12 and streak_i < 10
    assert blocks.mean() == pytest.approx(r.mean())                # every 24-trade block is one whole period


def test_a_sample_loss_streak_shows_up_in_every_whole_sample_path_drawdown():
    r = np.array([1.0] * 20 + [-1.0] * 10 + [2.0] * 20)
    cfg = RuinConfig(limits=_live(), trades_per_week=10, weeks=5, n_paths=200, horizons_weeks=(5,), apply_rules=False,
                     block_length=50, seed=5)
    rep = simulate(cfg, r=r)
    assert rep.max_drawdown.median >= 1 - 0.995 ** 10 - 1e-12
    assert rep.block_length == 50


def test_results_are_also_reported_at_twice_the_block_length():
    r = np.random.default_rng(2).normal(0.1, 1.0, 200)
    rep = simulate(RuinConfig(limits=_live(), trades_per_week=8, weeks=26, n_paths=300, block_length=10,
                              horizons_weeks=(26,)), r=r)
    assert rep.double_block is not None and rep.double_block.block_length == 20
    assert 0 <= rep.double_block.p_stage1 <= 1 and rep.double_block.max_drawdown_p95 >= 0
    assert "block 20" in format_report(rep)


def test_haircut_and_demean_shift_the_simulated_mean():
    r = np.random.default_rng(3).normal(0.3, 1.0, 40)
    base = RuinConfig(limits=_live(), trades_per_week=5, weeks=10, n_paths=100, horizons_weeks=(10,))
    cut = simulate(base.model_copy(update={"r_haircut": 0.1}), r=r)
    assert cut.simulated_mean_r == pytest.approx(r.mean() - 0.1, abs=1e-6)
    zero = simulate(base.model_copy(update={"demean": True}), r=r)
    assert zero.simulated_mean_r == pytest.approx(0.0, abs=1e-6)
    plain = simulate(base, r=r)
    assert any("--demean" in n for n in plain.notes)               # n = 40 < 100: the report recommends a stress run


def test_every_report_states_its_assumptions():
    rep = simulate(RuinConfig(limits=_live(), trades_per_week=5, weeks=4, n_paths=10, horizons_weeks=(4,)),
                   r=np.ones(10))
    text = " ".join(rep.notes)
    assert "evenly spaced" in text and "one position at a time" in text
    assert "multiplier" in text and "closed equity" in text
    assert "(halted at 12%; see rules-off run)" in format_report(rep)


def test_r_from_closed_trades_keeps_order_skips_trades_without_r_and_is_net():
    from goldbot.ops.gates_phase import ClosedTrade

    def ct(day: int, r: float | None) -> ClosedTrade:
        return ClosedTrade(account_id="a", broker="icm", mode="demo", exit_utc=pd.Timestamp(f"2026-10-0{day}", tz="UTC"),
                           ret=0.0, pnl=0.0, equity_before=1000, r=r)
    s = r_from_closed_trades([ct(3, -1.0), ct(1, 2.0), ct(2, None)])
    assert s.r.tolist() == [2.0, -1.0] and s.cost_basis == "net"
    assert s.exit_utc is not None and len(s.exit_utc) == 2


def _shadow(version: str, closes: list[dict[str, Any]]) -> dict[str, Any]:
    trades = []
    for c in closes:
        trades.append({"version": version, "agent_id": "tsmom-x", "side": c.get("side", 1),
                       "entry_ts": c["day"] + "T00:00:00Z", "entry": 100.0, "stop": c.get("stop", 98.0),
                       "target": 104.0, "max_bars": 10, "p": 0.6, "taken": c.get("taken", True),
                       "exit_ts": c["day"] + "T05:00:00Z", "exit": c["exit"], "ret": c["ret"]})
    return {"version": version, "started_utc": "2026-10-01T00:00:00Z", "open": [], "closed": trades}


def _book(tmp_path: Path) -> Path:
    book = {
        "v1": _shadow("v1", [
            {"day": "2026-10-02", "exit": 104.0, "ret": 0.04},                              # +2R
            {"day": "2026-10-01", "side": -1, "stop": 101.0, "exit": 101.0, "ret": -0.01},   # -1R
            {"day": "2026-10-03", "exit": 104.0, "ret": 0.04, "taken": False},              # counterfactual
            # scale-out: half at 104 then the rest at entry: ret 0.02 = +1R, though exit == entry
            {"day": "2026-10-04", "exit": 100.0, "ret": 0.02}]),
        "v2": _shadow("v2", [{"day": "2026-10-05", "exit": 98.0, "ret": -0.02}]),
    }
    (tmp_path / "shadow_book.json").write_text(json.dumps(book))
    return tmp_path


def test_shadow_r_is_one_version_from_its_return_so_scale_outs_count(tmp_path: Path):
    s = r_from_shadow_book(_book(tmp_path), version="v1")
    assert s.r.tolist() == [-1.0, 2.0, 1.0] and s.versions == ["v1"] and s.cost_basis == "spread_only"


def test_shadow_versions_are_pooled_only_when_asked(tmp_path: Path):
    _book(tmp_path)
    with pytest.raises(ValueError, match="pool"):
        r_from_shadow_book(tmp_path)
    s = r_from_shadow_book(tmp_path, pool=True)
    assert s.versions == ["v1", "v2"] and len(s.r) == 4


def test_shadow_default_is_the_agents_champion(tmp_path: Path):
    _book(tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    entry = {"version": "v2", "family": "tsmom", "agent_id": "tsmom-x", "status": "champion",
             "created_utc": "2026-10-01T00:00:00Z", "feature_version": "f", "feature_names": [], "artefact": "a",
             "sha256": "0"}
    (models / "registry.json").write_text(json.dumps([entry, {**entry, "version": "v1", "status": "challenger"}]))
    s = r_from_shadow_book(tmp_path, agent_id="tsmom-x", models_dir=models)
    assert s.versions == ["v2"] and s.r.tolist() == [-1.0]


def test_cli_shadow_names_the_version_and_the_cost_basis(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _book(tmp_path)
    out = tmp_path / "r.json"
    assert main(["--shadow", str(tmp_path), "--version", "v1", "--trades-per-week", "3", "--weeks", "4",
                 "--horizons", "4", "--paths", "50", "--out", str(out)]) == 0
    rep = RuinReport.model_validate_json(out.read_text())
    assert rep.versions == ["v1"] and rep.cost_basis == "spread_only"
    text = capsys.readouterr().out
    assert "v1" in text and "spread only" in text
    assert main(["--shadow", str(tmp_path), "--trades-per-week", "3", "--models-dir", str(tmp_path / "none")]) == 2
    assert "--pool-versions" in capsys.readouterr().err


def test_observed_trade_rate_is_reported_and_a_mismatch_warned(tmp_path: Path):
    from goldbot.ops.gates_phase import ClosedTrade
    days = pd.date_range("2026-01-05", periods=40, freq="7D", tz="UTC")     # 1 trade a week for 39 weeks
    lines = [ClosedTrade(account_id="a", broker="icm", mode="demo", exit_utc=d, ret=0.0, pnl=0.0, equity_before=1000,
                         r=1.0 if i % 2 else -1.0, position_id=i).model_dump_json() for i, d in enumerate(days)]
    (tmp_path / "closed_trades_a.jsonl").write_text("\n".join(lines) + "\n")
    out = tmp_path / "r.json"
    assert main(["--closed-trades", str(tmp_path), "--trades-per-week", "5", "--weeks", "4", "--horizons", "4",
                 "--paths", "50", "--out", str(out)]) == 0
    rep = RuinReport.model_validate_json(out.read_text())
    assert rep.observed_trades_per_week == pytest.approx(1.0)                # 39 gaps over 39 weeks
    assert rep.cost_basis == "net"
    assert any("differs" in n for n in rep.notes)


def test_cli_parametric_writes_a_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    out = tmp_path / "ruin.json"
    rc = main(["--win-rate", "0.5", "--avg-win", "1.6", "--avg-loss", "1.0", "--trades-per-week", "6",
               "--weeks", "26", "--paths", "300", "--out", str(out)])
    assert rc == 0
    rep = RuinReport.model_validate_json(out.read_text())
    assert rep.source == "parametric" and rep.config.limits.drawdown_stage2 == 0.12
    assert rep.cost_basis == "parametric"
    assert "drawdown_stage2" in capsys.readouterr().out


def test_cli_reads_r_from_a_backtest_trades_file(tmp_path: Path):
    f = tmp_path / "trades.csv"
    pd.DataFrame({"r": [1.0, -1.0, 2.0, -1.0] * 10}).to_csv(f, index=False)
    out = tmp_path / "ruin.json"
    assert main(["--r-file", str(f), "--trades-per-week", "5", "--weeks", "13", "--paths", "200",
                 "--out", str(out)]) == 0
    rep = RuinReport.model_validate_json(out.read_text())
    assert rep.source == "bootstrap" and rep.n_source_trades == 40 and rep.cost_basis == "unknown"


def test_cli_without_any_r_source_fails_with_a_message(capsys: pytest.CaptureFixture[str]):
    assert main(["--trades-per-week", "5"]) == 2
    assert "no R source" in capsys.readouterr().err


def test_cli_refuses_two_r_sources(tmp_path: Path):
    with pytest.raises(SystemExit):
        main(["--shadow", str(tmp_path), "--r-file", str(tmp_path / "x.csv"), "--trades-per-week", "5"])


def test_config_refuses_a_horizon_beyond_the_simulated_weeks():
    with pytest.raises(ValueError):
        RuinConfig(limits=_limits(), trades_per_week=5, weeks=10, horizons_weeks=(26,))
