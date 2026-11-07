"""EVAL-1 explore gates in the catalogue screen statistics (docs/research/evaluation-audit-2026-11-07.md section (b),
quant sign-off C1-C6; reading rule docs/research/prereg-2026-11-07-eval1-reading-rule.md).

The unit is a book: daily net return on equity (mark-to-market on daily closes, all costs), benchmark cash. Synthetic
markets are business-day closes over the explore years 2010-2017; trades are built on those closes with the frozen
sim's cost model, so gross, cost and the mark-to-market agree exactly."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue import sim as SIM
from goldbot.research.catalogue import stats as S
from goldbot.research.catalogue.data import DAY, HOUR

D0 = int(pd.Timestamp("2010-01-01", tz="UTC").value) // DAY
D1 = int(pd.Timestamp("2018-01-01", tz="UTC").value) // DAY
FX = ("EURUSD", "GBPUSD", "AUDUSD")
IDX = ("US500", "DE40", "UK100")


def market(insts: tuple[str, ...], drift: float, seed: int, vol: float = 0.01) -> S.Market:
    """Business-day closes (UTC day of the daily close) for 2010-2017, geometric walk with daily log drift `drift`."""
    days = np.array([d for d in range(D0, D1) if pd.Timestamp(d * DAY, unit="ns").dayofweek < 5], np.int64)
    rng = np.random.default_rng(seed)
    closes = {}
    for k, inst in enumerate(insts):
        px0 = 1.2 if inst in FX else 2000.0
        r = rng.normal(drift, vol, len(days))
        closes[inst] = pd.Series(px0 * np.exp(np.cumsum(r)), index=days)
        rng = np.random.default_rng(seed * 100 + k + 1)
    return S.Market(closes=closes, day_from=D0, day_to=D1)


def trades(m: S.Market, *, hold: int, every: int, side_fn, risk_frac: float = 0.01, seed: int = 0) -> pd.DataFrame:
    """Trades filled at a daily close and exited `hold` business days later at that close. `side_fn(fwd_return, rng)`
    picks the side (+1/-1) from the forward return over the hold (an edge) or ignores it (no edge)."""
    rng = np.random.default_rng(seed)
    rows = []
    for inst, c in m.closes.items():
        days, px = c.index.to_numpy(), c.to_numpy()
        for j in range(5, len(days) - hold, every):
            side = int(side_fn(px[j + hold] / px[j] - 1, rng))
            fill, ex = float(px[j]), float(px[j + hold])
            risk = risk_frac * fill
            t_fill = int(days[j]) * DAY + 21 * HOUR
            t_exit = int(days[j + hold]) * DAY + 21 * HOUR
            gross = side * (ex - fill) / risk
            cost = SIM.cost_price(inst, side, fill, float("nan"), 0, t_fill, t_exit) / risk
            ctrl = float(rng.normal(0, 1))
            rows.append({"t_fill": t_fill, "t_exit": t_exit, "side": side, "fill": fill, "risk": risk,
                         "gross": gross, "cost": cost, "nights": SIM.nights(t_fill, t_exit), "ctrl_gross": ctrl,
                         "excess": gross - ctrl, "drift_adj": gross, "inst": inst, "tf": "1d"})
    return pd.DataFrame(rows)


def always_long(_: float, __: np.random.Generator) -> int:
    return 1


def foresight(p: float):
    def f(fwd: float, rng: np.random.Generator) -> int:
        right = 1 if fwd > 0 else -1
        return right if rng.random() < p else -right
    return f


def test_a_pure_drift_long_only_unit_fails_the_drift_guard_e9_even_when_its_book_t_clears_e1() -> None:
    """C1: 'E1 against cash rewards exposure, not skill'. Always-long holds on a strongly drifting market earn the
    drift: the cash-benchmark t passes, but the alpha over the financed buy-and-hold book of the same instruments and
    side does not."""
    m = market(IDX, drift=0.0012, seed=3)
    tr = trades(m, hold=3, every=2, side_fn=always_long, risk_frac=0.02)
    s = S.summarize(tr, m)
    g = S.explore_gates(s, ["1d"], None)
    assert s["book"]["t"] >= 2 and g["E1"] and g["E2"]
    e9 = s["book"]["e9"]["long"]
    assert e9["beta"] > 0.3
    assert not g["E9"]
    assert e9["alpha_t"] < S.E9_ALPHA_T_MIN or e9["alpha_share"] < S.E9_ALPHA_SHARE_MIN
    assert not S.explore_pass(g)


def test_a_unit_that_beats_its_twin_but_loses_against_cash_fails_e1_and_the_twin_is_only_a_kill() -> None:
    """EVAL-1 section 4: the twin 'is a kill, not a pass gate'; section 2: 'benchmark = cash'. A unit whose twin is
    much worse (excess t far above 2) but whose net book return is below zero fails."""
    m = market(FX, drift=0.0, seed=5)
    tr = trades(m, hold=1, every=1, side_fn=foresight(0.5), seed=1)
    tr["ctrl_gross"] = tr["gross"] - 0.5           # a twin 0.5 R worse on every trade
    tr["excess"] = 0.5 + np.random.default_rng(1).normal(0, 0.2, len(tr))
    tr["cost"] = tr["cost"] + 0.05                  # and net below cash
    s = S.summarize(tr, m)
    assert s["t_excess"] > 2
    g = S.explore_gates(s, ["1d"], None)
    assert g["twin_kill"]                            # not worse than random timing: the kill does not fire
    assert not g["E1"] and not g["E2"]
    assert not S.explore_pass(g)


def test_a_real_timing_edge_net_of_financing_passes_every_explore_gate() -> None:
    """A two-sided unit that is right 58 % of the time over 2-day holds on a driftless market, paying spread,
    commission and the nightly financing of the frozen cost model, passes E1-E9 and the twin kill."""
    m = market(FX, drift=0.0, seed=11)
    tr = trades(m, hold=2, every=1, side_fn=foresight(0.58), seed=2)
    tr["ctrl_gross"] = 0.0
    tr["excess"] = tr["gross"]
    assert (tr["cost"] > 0).all() and tr["nights"].min() >= 1      # financing is charged on every hold
    s = S.summarize(tr, m)
    g = S.explore_gates(s, ["1d"], None)
    assert s["net"] > 0
    assert g == {k: True for k in g}, g
    assert set(g) >= {"E1", "E2", "E3", "E4", "E6", "E8", "E9", "twin_kill"}
    assert set(s["book"]["e9"]) == {"long", "short"}                # E9 against the book of every side traded
    assert S.explore_pass(g)


def test_the_book_t_uses_the_eval1_newey_west_lag_from_the_median_hold() -> None:
    """C3: 'lag L = max(5, ceil(1.5 x median holding days)), fixed in the card'. A 10-day hold gives L = 15; the
    gated t is the stricter of the Bartlett windows L and 2L on the daily net book."""
    m = market(FX, drift=0.0, seed=13)
    tr = trades(m, hold=10, every=1, side_fn=foresight(0.55), seed=3)
    s = S.summarize(tr, m)
    hold_days = float(((tr["t_exit"] - tr["t_fill"]) / DAY).median())
    assert s["book"]["lag"] == max(5, math.ceil(1.5 * hold_days)) == 21     # 14 calendar days x 1.5
    x = S.book_daily(tr, m).to_numpy()
    want = min(S.nw_t(x, 21), S.nw_t(x, 42))
    assert s["book"]["t"] == pytest.approx(want)
    assert s["book"]["t"] < S._t(x)                      # overlapping holds: the NW t is below the plain t


def test_eval1_lag_is_at_least_five_for_intraday_holds() -> None:
    tr = pd.DataFrame({"t_fill": [0, DAY], "t_exit": [HOUR, DAY + 2 * HOUR]})
    assert S.eval1_lag(tr) == 5


def test_newey_west_t_shrinks_on_positively_autocorrelated_series_and_equals_plain_t_at_lag_zero() -> None:
    rng = np.random.default_rng(1)
    e = rng.normal(0, 1, 20_010)
    x = 1.0 + np.convolve(e, np.ones(10), "valid")[:20_000]
    assert S.nw_t(x, 0) == pytest.approx(S._t(x), rel=1e-3)
    assert 0 < S.nw_t(x, 15) < S._t(x) / 2


def test_mark_to_market_book_sums_to_the_trades_net_and_spreads_multi_day_holds_over_their_days() -> None:
    m = market(FX[:1], drift=0.0, seed=17)
    tr = trades(m, hold=4, every=7, side_fn=foresight(0.5), seed=4)
    b = S.book_daily(tr, m)
    assert float(b.sum()) == pytest.approx(float((tr["gross"] - tr["cost"]).sum()))
    assert int((b != 0).sum()) >= 4 * len(tr) - 5      # 3 marked days + the exit day each, not the fill day only


def test_e8_fails_when_one_trade_carries_more_than_ten_percent_of_the_net() -> None:
    """E8: 'no single trade > 10 % of total net P&L' (the S21-V2 holiday-bar case)."""
    m = market(FX, drift=0.0, seed=11)
    tr = trades(m, hold=2, every=1, side_fn=foresight(0.58), seed=2)
    total = float((tr["gross"] - tr["cost"]).sum())
    g = tr["gross"].to_numpy(float).copy()
    g[0] += 0.2 * total
    tr["gross"] = g
    s = S.summarize(tr, m)
    assert not S.explore_gates(s, ["1d"], None)["E8"]


def test_e8_fails_when_the_book_is_not_net_positive_without_its_top_one_percent_of_days() -> None:
    """C6: 'the book is net > 0 after removing the top 1 % of days'."""
    m = market(FX, drift=0.0, seed=19)
    tr = trades(m, hold=1, every=1, side_fn=foresight(0.5), seed=5)
    tr["cost"] = 0.0
    tr["gross"] -= float(tr["gross"].mean()) + 0.001                       # a small net loss on every trade ...
    big = tr.groupby(tr["t_fill"] // DAY).head(1).sample(frac=0.009, random_state=0).index
    tr.loc[big, "gross"] += 0.5 * len(tr) / len(big) * 0.01                 # ... rescued by under 1 % of days
    s = S.summarize(tr, m)
    assert s["net"] > 0
    assert not s["book"]["tail"]["days_ok"]
    assert not S.explore_gates(s, ["1d"], None)["E8"]


def test_the_twin_kill_drops_a_unit_worse_than_random_timing_at_t_minus_two() -> None:
    m = market(FX, drift=0.0, seed=11)
    tr = trades(m, hold=2, every=1, side_fn=foresight(0.58), seed=2)
    tr["ctrl_gross"] = tr["gross"] + 0.5
    tr["excess"] = -0.5 + np.random.default_rng(0).normal(0, 0.1, len(tr))
    g = S.explore_gates(S.summarize(tr, m), ["1d"], None)
    assert not g["twin_kill"] and not S.explore_pass(g)


def test_learn_pre_must_have_the_same_sign_of_net_not_of_excess() -> None:
    m = market(FX, drift=0.0, seed=11)
    tr = trades(m, hold=2, every=1, side_fn=foresight(0.58), seed=2)
    s = S.summarize(tr, m)
    assert S.explore_gates(s, ["1d"], {"n": 100, "net": 0.05, "excess": -0.2})["E7"]
    assert not S.explore_gates(s, ["1d"], {"n": 100, "net": -0.05, "excess": 0.2})["E7"]
    assert "E7" not in S.explore_gates(s, ["1d"], {"n": 59, "net": -0.05, "excess": -0.2})


def test_without_a_market_the_book_gates_cannot_pass() -> None:
    m = market(FX, drift=0.0, seed=11)
    tr = trades(m, hold=2, every=1, side_fn=foresight(0.58), seed=2)
    g = S.explore_gates(S.summarize(tr), ["1d"], None)
    assert not g["E1"] and not g["E9"] and not S.explore_pass(g)


def test_confirm_reads_net_years_the_1_5x_stress_and_the_drawdown_at_the_registered_sizing() -> None:
    """EVAL-1 section 5: '>= 5/7 years net positive; net > 0 at 1.5x cost stress; max drawdown at the registered
    sizing <= the account's size-down stage (8 % today)'."""
    good: dict[str, Any] = {"n": 500, "net": 0.1, "net_stress": 0.02,
            "book": {"years": {y: 1.0 for y in S.CONFIRM_YEARS}, "max_dd_r": 10.0}}
    assert all(S.confirm_gates(good).values())
    assert not S.confirm_gates({**good, "net_stress": -0.01})["net_stress"]
    assert not S.confirm_gates({**good, "book": {**good["book"], "max_dd_r": 17.0}})["max_dd"]   # 17 x 0.5 % > 8 %
    two_bad = {**good["book"], "years": {y: (1.0 if y > 2019 else -1.0) for y in S.CONFIRM_YEARS}}
    assert all(S.confirm_gates({**good, "book": two_bad}).values())                             # 5 of 7
    three_bad = {**good["book"], "years": {y: (1.0 if y > 2020 else -1.0) for y in S.CONFIRM_YEARS}}
    assert not S.confirm_gates({**good, "book": three_bad})["years"]


def test_test_old_requires_a_high_beta_unit_to_be_net_non_negative_on_net_t() -> None:
    base = {"n": 80, "net": 0.02, "book": {"t": 1.5, "e9": {"long": {"beta": 0.5}}}}
    assert all(S.test_old_gates(base).values())
    assert not S.test_old_gates({**base, "net": -0.01})["beta_net"]
    assert not S.test_old_gates({**base, "book": {"t": 1.0, "e9": {"long": {"beta": 0.1}}}})["t_net"]


def test_promotion_needs_dsr_095_on_the_confirm_net_series_at_registry_k() -> None:
    """C5: 'DSR >= 0.95 at registry K_eff on the CONFIRM net series (the explore+confirm DSR is reported only)'."""
    rng = np.random.default_rng(0)
    strong = rng.normal(0.3, 1.0, 1700)
    weak = rng.normal(0.02, 1.0, 1700)
    assert S.promotion_gates(strong, k_eff=3263)["dsr"]
    assert not S.promotion_gates(weak, k_eff=3263)["dsr"]


def test_a_unit_with_one_traded_instrument_missing_its_daily_closes_fails_e9() -> None:
    """Quant G1: E9 is never computed against a partial buy-and-hold book."""
    m = market(FX[:2], drift=0.0, seed=11)
    tr = trades(m, hold=2, every=1, side_fn=foresight(0.58), seed=2)
    partial = S.Market(closes={FX[0]: m.closes[FX[0]]}, day_from=m.day_from, day_to=m.day_to)
    s = S.summarize(tr, partial)
    assert set(s["book"]["e9"]) == {"missing"}
    assert not S.explore_gates(s, ["1d"], None)["E9"]
    assert S.explore_gates(S.summarize(tr, m), ["1d"], None)["E9"]


def test_the_run_stops_when_a_traded_instrument_has_no_daily_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quant G1: a traded instrument without daily closes stops the run before any result is written."""
    from goldbot.research.catalogue import dispatcher as D
    from goldbot.research.catalogue import run as R

    def no_bars(*a: Any, **k: Any) -> Any:
        raise FileNotFoundError("none")
    monkeypatch.setattr(D, "get_bars", no_bars)
    with pytest.raises(FileNotFoundError, match="EVAL-1 book: no 1d closes for EURUSD in explore"):
        R._market(pd.DataFrame({"inst": ["EURUSD"]}), "explore")
