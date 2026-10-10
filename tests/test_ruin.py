"""Drawdown and risk-of-ruin Monte Carlo (playbook G-5, BACKLOG item 20): analytic cases, the block bootstrap and the
inputs it reads."""
from __future__ import annotations

import json
import math
from pathlib import Path

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
    gamblers_ruin_probability,
    longest_run,
    main,
    r_from_closed_trades,
    r_from_shadow_book,
    simulate,
)

# loose limits so a test can switch on only the threshold it is about
_OFF = dict(daily_cap=0.99, weekly_cap=0.99, supervisor_daily_cap=0.99, supervisor_weekly_cap=0.99,
            drawdown_stage1=0.98, drawdown_stage2=0.99, drawdown_stage1_clear=0.5)


def _limits(**kw: float) -> RuinLimits:
    return RuinLimits(**{"risk_per_trade": 0.01, **_OFF, **kw})


def _trip(rep: RuinReport, name: str):
    return next(t for t in rep.thresholds if t.name == name)


def test_limits_come_from_the_configured_settings():
    risk = load_settings().risk
    lim = RuinLimits.from_settings(risk)
    assert (lim.risk_per_trade, lim.drawdown_stage1, lim.drawdown_stage2) == (risk.risk_per_trade, 0.08, 0.12)
    assert (lim.daily_cap, lim.weekly_cap) == (risk.daily_cap, risk.weekly_cap)
    assert (lim.supervisor_daily_cap, lim.supervisor_weekly_cap) == (risk.supervisor_daily_cap, risk.supervisor_weekly_cap)
    assert RuinLimits.from_settings(risk, tiny_live=True).risk_per_trade == risk.risk_per_trade_tiny_live


def test_all_winning_trades_never_draw_down_or_trip_anything():
    cfg = RuinConfig(limits=RuinLimits.from_settings(load_settings().risk), trades_per_week=10, weeks=52, n_paths=200)
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


def test_daily_cap_stops_the_day_and_the_week_carries_on():
    # 20 trades a week = 4 a day; at 1.5% risk one loss is 1.5% (< 2%) and two are 2.98%, so the cap trips on the
    # second loss of each day and the 3rd and 4th trades are skipped; the next day starts again
    lim = _limits(risk_per_trade=0.015, daily_cap=0.02)
    rep = simulate(RuinConfig(limits=lim, trades_per_week=20, weeks=1, n_paths=10, horizons_weeks=(1,)), r=-np.ones(5))
    assert _trip(rep, "daily_cap").p_by_weeks[1] == 1.0
    assert rep.mean_trades_taken == 10                      # 2 of 4 a day, 5 days
    assert rep.final_equity.median == pytest.approx(0.985 ** 10)


def test_stage_one_halves_risk_and_stage_two_halts_the_path():
    # losses of 1%: stage 1 at the 9th loss, then 0.5% losses until 12% (0.9135 * 0.995^k <= 0.88 at k = 8)
    lim = _limits(drawdown_stage1=0.08, drawdown_stage2=0.12)
    rep = simulate(RuinConfig(limits=lim, trades_per_week=100, weeks=1, n_paths=10, horizons_weeks=(1,)), r=-np.ones(5))
    k = math.ceil(math.log(0.88 / 0.99 ** 9) / math.log(0.995))
    assert rep.mean_trades_taken == 9 + k
    assert _trip(rep, "drawdown_stage2").p_by_weeks[1] == 1.0
    assert rep.final_equity.median == pytest.approx(0.99 ** 9 * 0.995 ** k)


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
    cfg = RuinConfig(limits=RuinLimits.from_settings(load_settings().risk), trades_per_week=8, weeks=26, n_paths=500,
                     horizons_weeks=(13, 26), seed=3)
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
    cfg = RuinConfig(limits=RuinLimits.from_settings(load_settings().risk), trades_per_week=10, weeks=5, n_paths=200,
                     horizons_weeks=(5,), apply_rules=False, block_length=50, seed=5)
    rep = simulate(cfg, r=r)
    assert rep.max_drawdown.median >= 1 - 0.995 ** 10 - 1e-12
    assert rep.block_length == 50


def test_config_refuses_a_horizon_beyond_the_simulated_weeks():
    with pytest.raises(ValueError):
        RuinConfig(limits=_limits(), trades_per_week=5, weeks=10, horizons_weeks=(26,))


def test_r_from_closed_trades_keeps_order_and_skips_trades_without_r(tmp_path: Path):
    from goldbot.ops.gates_phase import ClosedTrade
    def ct(day: int, r: float | None) -> ClosedTrade:
        return ClosedTrade(account_id="a", broker="icm", mode="demo", exit_utc=pd.Timestamp(f"2026-10-0{day}", tz="UTC"),
                           ret=0.0, pnl=0.0, equity_before=1000, r=r)
    assert r_from_closed_trades([ct(3, -1.0), ct(1, 2.0), ct(2, None)]).tolist() == [2.0, -1.0]


def test_r_from_shadow_book_counts_taken_closed_trades_only(tmp_path: Path):
    book = {"v1": {"version": "v1", "started_utc": "2026-10-01T00:00:00Z", "open": [], "closed": [
        {"version": "v1", "agent_id": "a", "side": 1, "entry_ts": "2026-10-02T00:00:00Z", "entry": 100.0,
         "stop": 98.0, "target": 104.0, "max_bars": 10, "p": 0.6, "exit_ts": "2026-10-02T05:00:00Z", "exit": 104.0},
        {"version": "v1", "agent_id": "a", "side": -1, "entry_ts": "2026-10-01T00:00:00Z", "entry": 100.0,
         "stop": 101.0, "target": 98.0, "max_bars": 10, "p": 0.6, "exit_ts": "2026-10-01T05:00:00Z", "exit": 101.0},
        {"version": "v1", "agent_id": "a", "side": 1, "entry_ts": "2026-10-03T00:00:00Z", "entry": 100.0,
         "stop": 98.0, "target": 104.0, "max_bars": 10, "p": 0.4, "taken": False,
         "exit_ts": "2026-10-03T05:00:00Z", "exit": 104.0}]}}
    (tmp_path / "shadow_book.json").write_text(json.dumps(book))
    assert r_from_shadow_book(tmp_path).tolist() == [-1.0, 2.0]   # by exit time; the untaken candidate is left out


def test_cli_parametric_writes_a_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    out = tmp_path / "ruin.json"
    rc = main(["--win-rate", "0.5", "--avg-win", "1.6", "--avg-loss", "1.0", "--trades-per-week", "6",
               "--weeks", "26", "--paths", "300", "--out", str(out)])
    assert rc == 0
    rep = RuinReport.model_validate_json(out.read_text())
    assert rep.source == "parametric" and rep.config.limits.drawdown_stage2 == 0.12
    assert "drawdown_stage2" in capsys.readouterr().out


def test_cli_reads_r_from_a_backtest_trades_file(tmp_path: Path):
    f = tmp_path / "trades.csv"
    pd.DataFrame({"r": [1.0, -1.0, 2.0, -1.0] * 10}).to_csv(f, index=False)
    out = tmp_path / "ruin.json"
    assert main(["--r-file", str(f), "--trades-per-week", "5", "--weeks", "13", "--paths", "200",
                 "--out", str(out)]) == 0
    rep = RuinReport.model_validate_json(out.read_text())
    assert rep.source == "bootstrap" and rep.n_source_trades == 40


def test_cli_without_any_r_source_fails_with_a_message(capsys: pytest.CaptureFixture[str]):
    assert main(["--trades-per-week", "5"]) == 2
    assert "no R source" in capsys.readouterr().err
