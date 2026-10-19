"""The live state path of the research rule families (shadow roster amendment 2026-10-19, sweep_absorb): a candidate's
own barriers (R = its stop distance `risk_usd`, its `target_atr` in R, its `max_bars`) and barriers read on 1m bars
reach the shadow book, so a shadow trade is the family's research label; such an agent is shadow only."""
import numpy as np
import pandas as pd
import pytest

from goldbot.engine.shadow import ShadowBook, ShadowTrade, candidate_barriers, minutes_of, record_candidate
from goldbot.labels.triple_barrier import BarrierSpec, one_at_a_time, triple_barrier_1m
from goldbot.research.population import SHADOW_ROSTER, Population
from goldbot.research.rule_model import RuleModel
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.base import AgentIdentity


def _minutes_and_bars(n15: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """1m bid/ask bars and the 15m bars aggregated from them (store schema with visible_at)."""
    rng = np.random.default_rng(seed)
    n = n15 * 15
    mid = 2400 + np.cumsum(rng.normal(0, 0.35, n))
    hi, lo = mid + rng.uniform(0.05, 0.6, n), mid - rng.uniform(0.05, 0.6, n)
    close = np.clip(mid + rng.normal(0, 0.15, n), lo, hi)
    ts = pd.date_range("2025-03-03 00:00", periods=n, freq="1min", tz="UTC")
    s = 0.2
    m1 = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1), "bid_high": hi, "bid_low": lo,
                       "bid_close": close, "ask_high": hi + s, "ask_low": lo + s, "ask_close": close + s})
    g = m1.groupby(m1["ts_utc"].dt.floor("15min"))
    b15 = pd.DataFrame({"bid_high": g["bid_high"].max(), "bid_low": g["bid_low"].min(),
                        "bid_close": g["bid_close"].last(), "ask_high": g["ask_high"].max(),
                        "ask_low": g["ask_low"].min(), "ask_close": g["ask_close"].last()})
    b15 = b15.rename_axis("ts_utc").reset_index()
    b15["visible_at"] = b15["ts_utc"] + pd.Timedelta(minutes=15)
    return m1, b15


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_shadow_trades_with_candidate_barriers_on_1m_bars_reproduce_the_research_labels(tmp_path, seed):
    m1, bars = _minutes_and_bars(240, seed)
    idx = np.arange(10, 220, 5)
    rng = np.random.default_rng(seed + 10)
    signals = pd.DataFrame({"idx": idx, "side": np.where(idx % 2 == 0, 1, -1),
                            "risk_usd": rng.uniform(0.8, 2.5, len(idx)), "target_atr": rng.uniform(0.8, 2.0, len(idx)),
                            "max_bars": np.full(len(idx), 16)})
    spec = BarrierSpec(target_atr=2.0, stop_atr=1.0, max_bars=16)
    r_arr = np.full(len(bars), np.nan)
    r_arr[idx] = signals["risk_usd"].to_numpy()
    r = pd.Series(r_arr, index=bars.index)
    labels = one_at_a_time(triple_barrier_1m(bars, m1, signals, spec, r)).set_index("idx")

    book = ShadowBook(tmp_path)
    book.track("v1", bars["ts_utc"].iloc[0])
    sig = {int(i): (int(sd), float(rk), float(tm), int(h)) for i, sd, rk, tm, h in signals.itertuples(index=False)}
    opened: dict[int, ShadowTrade] = {}
    for i in range(len(bars)):
        bar = bars.iloc[i]
        book.on_bar(bar, "15m", minutes=minutes_of(m1, bar, "15m"))
        if i in sig:
            side, risk, tmult, hold = sig[i]
            t = book.open_trade(version="v1", agent_id="a", side=side, bar_ts=bar["ts_utc"],
                                entry=float(bar["ask_close"] if side > 0 else bar["bid_close"]), atr_usd=risk,
                                target_atr=tmult, stop_atr=1.0, max_bars=hold, p=1.0, barriers_on_1m=True)
            if t is not None:
                opened[i] = t
    assert set(opened) == set(labels.index)
    lab_exit = dict(zip(labels.index, labels["exit"].to_numpy(dtype=float), strict=True))
    lab_barrier = dict(zip(labels.index, labels["barrier_hit"].astype(str), strict=True))
    lab_t = dict(zip(labels.index, labels["t_exit"].to_numpy(dtype=int), strict=True))
    seen = set()
    for i, t in opened.items():
        assert t.exit == pytest.approx(lab_exit[i])
        assert t.barrier == lab_barrier[i]
        assert t.exit_ts == bars["ts_utc"].iloc[lab_t[i]]
        seen.add(t.barrier)
    assert {"stop", "target"} <= seen


def test_without_its_minutes_a_1m_barrier_trade_falls_back_to_the_decision_bar_with_the_stop_first(tmp_path):
    m1, bars = _minutes_and_bars(3, 0)
    bar = bars.iloc[1].copy()
    book = ShadowBook(tmp_path)
    book.track("v1", bars["ts_utc"].iloc[0])
    entry = float(bars["ask_close"].iloc[0])
    # a bar spanning both barriers: stop first without the minutes (conservative), as `triple_barrier`
    bar["bid_high"], bar["bid_low"] = entry + 10, entry - 10
    t = book.open_trade(version="v1", agent_id="a", side=1, bar_ts=bars["ts_utc"].iloc[0], entry=entry, atr_usd=1.0,
                        target_atr=2.0, stop_atr=1.0, max_bars=16, p=1.0, barriers_on_1m=True)
    book.on_bar(bar, "15m")
    assert t is not None and t.barrier == "stop"


def test_the_minutes_of_a_decision_bar_are_the_1m_bars_inside_it():
    m1, bars = _minutes_and_bars(3, 0)
    got = minutes_of(m1, bars.iloc[1], "15m")
    assert len(got) == 15
    assert got["ts_utc"].iloc[0] == bars["ts_utc"].iloc[1]
    assert got["ts_utc"].iloc[-1] == bars["ts_utc"].iloc[1] + pd.Timedelta(minutes=14)


def test_a_candidate_carrying_its_risk_sizes_the_shadow_barriers_in_r_and_the_cost_in_r():
    agent = SPECIALISTS["sweep_absorb"]()
    cand = pd.Series({"idx": 5, "side": -1, "risk_usd": 4.0, "max_bars": 16})
    b = candidate_barriers(agent, cand, atr_usd=8.0, cost_atr=0.1)
    assert (b.r_usd, b.target_r, b.stop_r, b.max_bars) == (4.0, 2.0, 1.0, 16)
    assert b.cost_r == pytest.approx(0.2)            # 0.1 ATR of 8 USD = 0.8 USD = 0.2 R of 4 USD
    eq = SPECIALISTS["eq_liquidity_run"]()
    b2 = candidate_barriers(eq, pd.Series({"idx": 5, "side": 1, "risk_usd": 2.0, "target_atr": 1.3, "max_bars": 16}),
                            atr_usd=8.0, cost_atr=0.1)
    assert (b2.r_usd, b2.target_r) == (2.0, 1.3)


def test_a_candidate_without_its_own_risk_keeps_the_atr_barriers_of_its_label_spec():
    agent = SPECIALISTS["breakout"]()
    ls = agent.label_spec
    b = candidate_barriers(agent, pd.Series({"idx": 5, "side": 1}), atr_usd=8.0, cost_atr=0.1)
    assert (b.r_usd, b.target_r, b.stop_r, b.max_bars, b.cost_r) == (8.0, ls.target_atr, ls.stop_atr, ls.max_bars, 0.1)
    assert candidate_barriers(agent, None, atr_usd=8.0, cost_atr=0.1) == b


def test_record_candidate_opens_a_sweep_absorb_shadow_trade_at_its_structural_stop_on_1m_barriers(tmp_path):
    _, bars = _minutes_and_bars(3, 0)
    agent = SPECIALISTS["sweep_absorb"]()
    book = ShadowBook(tmp_path)
    book.track("v1", bars["ts_utc"].iloc[0])
    Xm = pd.DataFrame({"ts_utc": bars["ts_utc"], "f": 0.0})
    bar = bars.iloc[2]
    t = record_candidate(book, version="v1", model=RuleModel(rule="sweep_absorb"), agent=agent, tf="15m", bar=bar, Xm=Xm, last=2,
                         side=-1, atr_usd=8.0, cost_atr=0.1,
                         candidate=pd.Series({"idx": 2, "side": -1, "risk_usd": 4.0, "max_bars": 16}))
    assert t is not None and t.taken and t.barriers_on_1m
    entry = float(bar["bid_close"])
    assert t.stop == pytest.approx(entry + 4.0) and t.target == pytest.approx(entry - 8.0) and t.max_bars == 16


@pytest.mark.parametrize("family", ["sweep_absorb", "eq_liquidity_run", "box_break", "box_rip"])
def test_an_agent_whose_r_is_candidate_borne_or_read_on_1m_bars_is_shadow_only(family):
    """The live engine sizes with label_spec x ATR(14) on decision bars: such an agent cannot trade live as labelled."""
    assert SPECIALISTS[family]().shadow_only


@pytest.mark.parametrize("family", ["breakout", "trend", "mean_reversion", "session_open"])
def test_a_plain_atr_barrier_family_is_not_made_shadow_only(family):
    assert not SPECIALISTS[family]().shadow_only


def test_sweep_absorb_is_a_frozen_roster_member_in_shadow_with_its_pre_registered_config(tmp_path):
    assert ("sweep_absorb", "15m") in SHADOW_ROSTER
    assert getattr(SPECIALISTS["sweep_absorb"], "frozen_config", False)
    pop = Population(tmp_path / "population.json")
    pop.ensure_founders(pd.Timestamp("2026-10-19", tz="UTC"))
    aid = AgentIdentity(family="sweep_absorb", config={"rung": "prereg"}).agent_id
    m = pop.members[aid]
    assert m.status == "shadow" and m.capital_weight == 0 and m.specialist().timeframe == "15m"


def test_the_box_break_close_roster_ids_are_unchanged_by_the_amendment(tmp_path):
    pop = Population(tmp_path / "population.json")
    pop.ensure_founders(pd.Timestamp("2026-10-19", tz="UTC"))
    for cfg in ({}, {"timeframe": "1h"}, {"timeframe": "4h"}):
        assert AgentIdentity(family="box_break_close", config=cfg).agent_id in pop.members


def test_sweep_absorb_proposes_on_the_engine_tail_what_it_proposes_on_the_whole_series():
    """Point in time on the engine's 800-bar tail: its last-bar candidate equals the full series' at that bar."""
    rng = np.random.default_rng(3)
    n = 1800
    mid = 2400 + np.cumsum(rng.normal(0, 1.0, n))
    hi, lo = mid + rng.uniform(0.2, 2.0, n), mid - rng.uniform(0.2, 2.0, n)
    ts = pd.date_range("2025-03-03 00:00", periods=n, freq="15min", tz="UTC")
    m = pd.DataFrame({"ts_utc": ts, "open": mid, "high": hi, "low": lo, "close": np.clip(mid + rng.normal(0, .5, n), lo, hi)})
    agent = SPECIALISTS["sweep_absorb"]()
    f = agent.candidates(m, pd.DataFrame(index=m.index))
    full = {int(i): (int(sd), float(rk)) for i, sd, rk in zip(f["idx"], f["side"], f["risk_usd"], strict=True)}
    hits = 0
    for end in range(900, n):
        if end not in full and end % 6:
            continue                              # every candidate bar, a sixth of the others
        tail = m.iloc[end - 800:end + 1].reset_index(drop=True)
        c = agent.candidates(tail, pd.DataFrame(index=tail.index))
        live = c[c["idx"] == len(tail) - 1]
        if end in full:
            hits += 1
            assert list(live["side"]) == [full[end][0]]
            assert live["risk_usd"].to_numpy(dtype=float)[0] == pytest.approx(full[end][1], rel=1e-9)
        else:
            assert live.empty
    assert hits > 5


def test_a_live_founder_overriding_barrier_atr_is_not_shadow_only():
    """CI 2026-10-19: tsmom overrides `barrier_atr` (daily ATR when configured) and must stay a live-capable agent."""
    from goldbot.research.population import founder_base
    from goldbot.specialists import SPECIALISTS
    for tf in ("1h", "4h"):
        assert not SPECIALISTS["tsmom"](**founder_base("tsmom", tf)).shadow_only
