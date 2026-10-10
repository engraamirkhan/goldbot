"""Deterministic performance attribution (BACKLOG item 12): every breakdown on a synthetic shadow book, the noise
marking of small cells, cost attribution against the cost table, calibration on taken and untaken candidates, the
exit mix, live fills against the cost table, the read_attribution tool and the improvement agent's wiring."""
import json

import numpy as np
import pandas as pd
import pytest

from goldbot.agents.roles import ROLES
from goldbot.agents.tools import ReadOnlyTools
from goldbot.config import AttributionSettings
from goldbot.data.store import Store
from goldbot.engine.shadow import ShadowTrade
from goldbot.execution.costs import CostTable, SlippageStat, SpreadStat
from goldbot.labels.triple_barrier import SwapSpec
from goldbot.research.attribution import (
    REPORT_FILE,
    SUMMARY_FILE,
    CostModel,
    VersionRole,
    build_report,
    cell_stats,
    family_of,
    render_markdown,
    save_report,
)

NOW = pd.Timestamp("2026-10-09 23:50", tz="UTC")
S = AttributionSettings(window_days=180, min_trades=30, calibration_bins=10, calibration_min_bin=10)
FREE = CostModel(spread_usd={}, slippage_usd={}, slippage_prior_usd=0.0, commission_usd_per_oz=0.0, swap=None,
                 source="test: no costs")


def _trade(i: int, *, agent="tsmom-g0-abcdef0123", side=1, hour=8, r=1.0, barrier=None, tf="1h", taken=True, p=0.6,
           version=None, day=0, risk=10.0, bars=1) -> ShadowTrade:
    """A closed shadow trade entered on 2026-09-01 + day at `hour` UTC (signal bar open = hour - tf), R = r."""
    entry_px = 2400.0
    close = pd.Timestamp("2026-09-01", tz="UTC") + pd.Timedelta(days=day, hours=hour)
    entry_ts = close - pd.Timedelta(hours=1 if tf == "1h" else 0, minutes=15 if tf == "15m" else 0)
    exit_px = entry_px + side * r * risk
    barrier = barrier or ("target" if r > 0 else "stop")
    return ShadowTrade(version=version or f"{agent}-v1", agent_id=agent, side=side, entry_ts=entry_ts, entry=entry_px,
                       stop=entry_px - side * risk, target=entry_px + side * 2 * risk, max_bars=10, timeframe=tf, p=p,
                       threshold=0.55, taken=taken, p_raw=p, bars_held=bars, exit_ts=entry_ts + pd.Timedelta(hours=bars),
                       exit=exit_px, barrier=barrier, ret=side * (exit_px - entry_px) / entry_px)


def _book(n_win: int, n_loss: int, **kw) -> list[ShadowTrade]:
    out = [_trade(i, r=2.0, day=i % 30, **kw) for i in range(n_win)]
    return out + [_trade(n_win + i, r=-1.0, day=i % 30, **kw) for i in range(n_loss)]


# ------------------------------------------------------------------------------------------------ cell statistics
def test_cell_stats_carry_count_t_stat_interval_hit_rate_and_profit_factor():
    gross = np.array([2.0] * 40 + [-1.0] * 20)
    c = cell_stats(gross, gross - 0.1, min_trades=30)
    assert c.n == 60 and c.mean_r_gross == pytest.approx(1.0) and c.mean_r_net == pytest.approx(0.9)
    sd = float(np.std(gross, ddof=1))
    assert c.t_stat == pytest.approx(0.9 / (sd / np.sqrt(60)), rel=1e-6)
    assert c.ci95_net is not None and c.ci95_net[0] < 0.9 < c.ci95_net[1]
    assert c.hit_rate == pytest.approx(40 / 60)
    assert c.profit_factor == pytest.approx((40 * 1.9) / (20 * 1.1), rel=1e-6)
    assert c.verdict == "positive"


def test_cells_below_the_minimum_count_are_marked_noise_whatever_their_mean():
    small = cell_stats(np.full(29, 3.0) + np.linspace(0, 0.1, 29), np.full(29, 3.0) + np.linspace(0, 0.1, 29), min_trades=30)
    assert small.verdict == "noise" and small.n == 29
    flat = cell_stats(np.array([1.0, -1.0] * 20), np.array([1.0, -1.0] * 20), min_trades=30)
    assert flat.verdict == "indistinguishable from zero"
    neg = cell_stats(np.array([-1.0] * 30 + [0.5] * 10), np.array([-1.0] * 30 + [0.5] * 10), min_trades=30)
    assert neg.verdict == "negative"


def test_family_comes_from_the_agent_id():
    assert family_of("session_open-g2-0123456789") == "session_open"
    assert family_of("tsmom") == "tsmom"


# ------------------------------------------------------------------------------------------------ breakdowns
def test_every_breakdown_is_present_and_splits_the_taken_trades():
    trades = (_book(30, 10, hour=3) +                                   # asia long winners
              _book(5, 25, side=-1, hour=9, agent="mean_rev-g0-1111111111", tf="15m") +   # london short losers
              _book(10, 10, hour=15, agent="breakout-g1-2222222222"))   # new york, flat
    rep = build_report(trades, now=NOW, costs=FREE, settings=S)
    b = rep.breakdowns
    assert set(b) >= {"timeframe", "family", "agent", "session", "side", "regime", "decision", "exit"}
    assert b["session"]["asia"].n == 40 and b["session"]["london"].n == 30 and b["session"]["newyork"].n == 20
    assert b["side"]["long"].n == 60 and b["side"]["short"].n == 30
    assert b["timeframe"]["15m"].n == 30 and b["timeframe"]["1h"].n == 60
    assert set(b["family"]) == {"tsmom", "mean_rev", "breakout"}
    assert b["agent"]["mean_rev-g0-1111111111"].mean_r_net == pytest.approx((5 * 2 - 25) / 30)
    assert b["session"]["asia"].verdict == "positive" and b["session"]["london"].verdict == "negative"
    assert b["session"]["newyork"].verdict == "noise"                   # 20 < 30 trades
    assert rep.overall.n == 90


def test_regime_is_the_volatility_tercile_of_the_last_complete_day_before_entry():
    days = pd.date_range("2026-03-01", "2026-09-30", tz="UTC")
    vol = pd.Series(np.where(days < pd.Timestamp("2026-09-01", tz="UTC"), np.linspace(0.001, 0.003, len(days)), 0.01),
                    index=days)
    trades = _book(20, 20, hour=10)
    rep = build_report(trades, now=NOW, costs=FREE, settings=S, daily_vol=vol)
    assert set(rep.breakdowns["regime"]) == {"high"} and rep.breakdowns["regime"]["high"].n == 40
    # entry on the first day of the history: no completed day before it -> unknown, never a guess
    early = [_trade(0, day=0)]
    rep2 = build_report(early, now=NOW, costs=FREE, settings=S,
                        daily_vol=pd.Series([0.01] * 90, index=pd.date_range("2026-09-01", periods=90, tz="UTC")))
    assert set(rep2.breakdowns["regime"]) == {"unknown"}
    assert set(build_report(early, now=NOW, costs=FREE, settings=S).breakdowns["regime"]) == {"unknown"}


def test_untaken_candidates_are_counterfactual_only_and_kept_out_of_the_trading_breakdowns():
    trades = _book(30, 10) + _book(5, 35, taken=False, p=0.4)
    rep = build_report(trades, now=NOW, costs=FREE, settings=S)
    assert rep.overall.n == 40 and rep.n_candidates == 80 and rep.n_taken == 40
    assert rep.breakdowns["decision"]["taken"].n == 40 and rep.breakdowns["decision"]["not_taken"].n == 40
    assert (rep.breakdowns["decision"]["not_taken"].mean_r_net or 0.0) < 0


def test_challenger_trades_and_pre_promotion_trades_are_reported_apart():
    champ = _book(30, 10, version="tsmom-g0-abcdef0123-v1")
    chall = _book(1, 39, version="tsmom-g0-abcdef0123-v2")
    roles = {"tsmom-g0-abcdef0123-v1": VersionRole(status="champion", promoted_utc=None),
             "tsmom-g0-abcdef0123-v2": VersionRole(status="challenger", promoted_utc=None)}
    rep = build_report(champ + chall, now=NOW, costs=FREE, settings=S, roles=roles)
    assert rep.overall.n == 40 and rep.overall.mean_r_net == pytest.approx(1.25)
    assert rep.challengers.n == 40 and (rep.challengers.mean_r_net or 0.0) < 0
    # a promoted version counts from its promotion only (before it, it traded beside the then champion)
    late = {"tsmom-g0-abcdef0123-v1": VersionRole(status="champion", promoted_utc=pd.Timestamp("2026-09-16", tz="UTC"))}
    rep2 = build_report(champ, now=NOW, costs=FREE, settings=S, roles=late)
    assert 0 < rep2.overall.n < 40 and rep2.challengers.n == 40 - rep2.overall.n


def test_window_keeps_exits_inside_it_and_nothing_after_now():
    old = [_trade(0, day=-400)]
    future = [_trade(1, day=60)]
    rep = build_report(old + future + _book(3, 3), now=NOW, costs=FREE, settings=S)
    assert rep.overall.n == 6


# ------------------------------------------------------------------------------------------------ costs
def _costs(swap: SwapSpec | None = None) -> CostModel:
    table = CostTable(account_id="icm-demo", built_utc=NOW, spread={"asia": SpreadStat(median=0.30, p90=0.5, n=100),
                                                                      "london": SpreadStat(median=0.20, p90=0.3, n=100)},
                      slippage={"london:market": SlippageStat(mean=0.05, n=80, from_prior=False)},
                      commission_per_lot_side_usd=3.5, slippage_prior_usd=0.10)
    return CostModel.from_table(table, swap=swap)


def test_cost_attribution_per_trade_in_r_against_the_cost_table():
    t = _trade(0, hour=9, r=1.0, risk=10.0)      # london, long, shadow R already pays the spread
    rep = build_report([t], now=NOW, costs=_costs(), settings=S)
    row = rep.trades[0]
    assert row.spread_r == pytest.approx(0.20 / 10) and row.slippage_r == pytest.approx(2 * 0.05 / 10)
    assert row.commission_r == pytest.approx(2 * 3.5 / 100 / 10) and row.swap_r == 0.0
    assert row.r_gross == pytest.approx(1.0 + 0.02)              # spread added back: gross = before every cost
    assert row.r_net == pytest.approx(1.0 - 0.01 - 0.007)
    # a session without a slippage cell is charged the prior; without its own spread, the widest measured one
    ny = build_report([_trade(0, hour=15, risk=10.0)], now=NOW, costs=_costs(), settings=S).trades[0]
    assert ny.slippage_r == pytest.approx(2 * 0.10 / 10) and ny.spread_r == pytest.approx(0.30 / 10)
    summary = rep.costs
    assert summary.mean_r["spread"] == pytest.approx(0.02) and summary.source.startswith("cost table icm-demo")


def test_swap_is_charged_per_rollover_night_and_costs_that_flip_a_winner_are_counted():
    swap = SwapSpec(long_usd_per_lot=-60.0, short_usd_per_lot=0.0, triple_weekday=2, server_tz="Europe/Athens")
    # held 30 h from Monday 2026-09-07 09:00 UTC: one server midnight -> one night at -0.60 $/oz
    t = _trade(0, day=6, hour=9, r=0.05, risk=10.0, bars=30, barrier="time")
    rep = build_report([t], now=NOW, costs=_costs(swap), settings=S)
    row = rep.trades[0]
    assert row.nights == 1 and row.swap_r == pytest.approx(0.60 / 10)
    assert row.r_gross > 0 and row.r_net < 0
    assert rep.costs.killed == 1


# ------------------------------------------------------------------------------------------------ calibration and exits
def test_calibration_compares_p_with_the_realised_outcome_on_taken_and_untaken_candidates():
    rng = np.random.default_rng(0)
    trades = []
    for i in range(400):
        p = float(rng.uniform(0.3, 0.8))
        hit = rng.uniform() < p
        trades.append(_trade(i, p=p, r=2.0 if hit else -1.0, taken=p >= 0.55, day=i % 30))
    rep = build_report(trades, now=NOW, costs=FREE, settings=S)
    cal = rep.calibration
    assert set(cal) == {"taken", "not_taken", "all"}
    assert cal["taken"].n + cal["not_taken"].n == cal["all"].n == 400
    assert cal["all"].ece is not None and cal["all"].brier is not None
    assert cal["all"].ece < 0.08 and 0 < cal["all"].brier < 0.25
    bins = cal["all"].bins
    assert sum(b.n for b in bins) == 400 and all(b.lo <= b.mean_p <= b.hi for b in bins)
    assert all(b.noise == (b.n < 10) for b in cal["taken"].bins + cal["not_taken"].bins)
    # a model whose p says 0.7 but hits 0.3 is badly calibrated
    bad = [_trade(i, p=0.7, r=2.0 if i % 10 < 3 else -1.0, day=i % 30) for i in range(100)]
    assert build_report(bad, now=NOW, costs=FREE, settings=S).calibration["all"].ece == pytest.approx(0.4, abs=1e-9)


def test_exit_mix_splits_stop_target_time_and_policy():
    trades = (_book(30, 0) + _book(0, 20) + [_trade(i, r=0.2, barrier="time", day=i) for i in range(5)] +
              [_trade(i, r=0.5, barrier="trail", day=i) for i in range(3)] + [_trade(i, r=-0.1, barrier="flat", day=i)
                                                                               for i in range(2)])
    rep = build_report(trades, now=NOW, costs=FREE, settings=S)
    ex = rep.exits
    assert {k: v.n for k, v in ex.items()} == {"target": 30, "stop": 20, "time": 5, "policy": 5}
    assert ex["target"].share == pytest.approx(0.5) and ex["policy"].share == pytest.approx(5 / 60)
    assert rep.breakdowns["exit"]["policy"].n == 5


# ------------------------------------------------------------------------------------------------ live fills
def test_live_fill_slippage_is_measured_against_the_cost_table_cell():
    fills = pd.DataFrame({"ts_utc": pd.date_range("2026-09-10 09:00", periods=40, freq="1h", tz="UTC"),
                          "client_order_id": [f"o{i}" for i in range(40)], "side": 1, "requested": 2400.0,
                          "filled": 2400.15, "order_type": "market"})
    fills = fills[fills["ts_utc"].dt.hour.between(7, 11)]     # london only
    orders = {"sent": {f"o{i}": {"sl": 2390.0, "status": "filled"} for i in range(40)}}
    table = _costs()
    rep = build_report([], now=NOW, costs=table, settings=S, fills={"icm-demo": fills}, orders={"icm-demo": orders},
                       tables={"icm-demo": table})
    live = rep.live["icm-demo"]
    assert live.fills == len(fills) and live.mean_slippage_usd == pytest.approx(0.15)
    assert live.mean_table_usd == pytest.approx(0.05) and live.mean_excess_usd == pytest.approx(0.10)
    assert live.mean_excess_r == pytest.approx(0.10 / 10.0)
    assert live.verdict == "noise"                          # fewer fills than the minimum count
    assert live.orders == {"filled": 40}


# ------------------------------------------------------------------------------------------------ output and tool
def test_report_is_deterministic_and_saved_with_a_markdown_summary(tmp_path):
    trades = _book(30, 10) + _book(4, 6, side=-1, hour=15)
    a = build_report(trades, now=NOW, costs=_costs(), settings=S)
    b = build_report(list(reversed(trades)), now=NOW, costs=_costs(), settings=S)
    assert a.model_dump_json() == b.model_dump_json()
    save_report(a, tmp_path)
    data = json.loads((tmp_path / REPORT_FILE).read_text())
    assert data["as_of"].startswith("2026-10-09") and data["min_trades"] == 30
    md = (tmp_path / SUMMARY_FILE).read_text()
    assert md == render_markdown(a)
    assert "noise" in md and "## By session" in md
    assert "changes nothing" in md


def test_read_attribution_tool_returns_the_report_and_its_sections(tmp_path):
    tools = ReadOnlyTools(tmp_path, Store(tmp_path / "data"), now=lambda: NOW)
    missing = json.loads(tools.call("read_attribution", {"section": "summary"}, ["read_attribution"])[0])
    assert missing["missing"] == "attribution"
    save_report(build_report(_book(30, 10), now=NOW, costs=FREE, settings=S), tmp_path)
    text, err = tools.call("read_attribution", {"section": "summary"}, ["read_attribution"])
    out = json.loads(text)
    assert not err and out["as_of"].startswith("2026-10-09") and "Expectancy" in out["summary"]
    sess = json.loads(tools.call("read_attribution", {"section": "session"}, ["read_attribution"])[0])
    assert sess["session"]["london"]["n"] == 40 and sess["session"]["london"]["verdict"] == "positive"
    assert tools.call("read_attribution", {"section": "users"}, ["read_attribution"])[1] is True
    d = tools.definitions(["read_attribution"])[0]
    assert d["input_schema"]["properties"]["section"]["enum"][0] == "summary"


def test_improvement_agent_and_research_analyst_read_the_attribution_first():
    for name in ("improvement_agent", "research_analyst"):
        role = ROLES[name]
        assert "read_attribution" in role.tools and "read_attribution" in role.task
    assert "noise" in ROLES["improvement_agent"].task
    # the feedback path is still the bounded hypothesis tool; nothing new can write
    assert "file_hypothesis" in ROLES["improvement_agent"].tools and "run_trial" not in ROLES["improvement_agent"].tools
