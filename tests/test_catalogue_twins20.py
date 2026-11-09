"""EVAL-2 twins (method v6 section 2, prereg 2026-11-09 section 4.1, quant finding 7):

"Twins are drawn from decision bars after the signal's exit (intraday: the 7 days after exit; 1d: the 20 bars after
exit), excluding bars where any instance of the same family fires. Stop, target and hold are the signal's multiples of
ATR at the **twin's** decision bar." M = 20 replay-only twins per signal, same instrument, timeframe, side, hour +/-1 h
and regime level (when the card registers one), scored on the same replay path and cost path as the signal (swap by
the strict-interior rollover rule, #343)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue import dispatcher as D
from goldbot.research.catalogue import run as RUN
from goldbot.research.catalogue import sim as SIM
from goldbot.research.catalogue.core import EB, Events, atr
from goldbot.research.catalogue.data import DAY, HOUR, Bars
from tests.test_catalogue_random_walk import flat_bars, rw_cell

COL = {k: j for j, k in enumerate(SIM.TRADE_COLS)}


def _walk(tf: str = "1h", n: int = 4_000, seed: int = 7) -> Bars:
    return rw_cell("XAUUSD", tf, seed, n)[0]


def _events(b: Bars, every: int = 37, start: int = 60, *, stop_atr: float = 1.0, tgt_atr: float = 2.0,
            hold: int = 10) -> Events:
    a = atr(b, SIM.RISK_FLOOR_ATR_N)
    eb = EB()
    for j, i in enumerate(range(start, len(b) - 30, every)):
        eb.add(i, 1 if j % 2 == 0 else -1, stop_d=stop_atr * a[i], tgt_d=tgt_atr * a[i], hold=hold)
    return eb.build()


def _signals(b: Bars, ev: Events) -> tuple[np.ndarray, np.ndarray]:
    """(exit bar per event, -1 when not traded; family fire mask)."""
    tr = SIM.simulate(b, ev, control="random_side", seed_key="t")
    exits = np.full(len(ev), -1, np.int64)
    for row in tr:
        k = int(np.flatnonzero((ev.i == int(row[COL["i"]])) & (ev.side == int(row[COL["side"]])))[0])
        exits[k] = int(row[COL["x"]])
    fire = np.zeros(len(b), bool)
    fire[ev.i] = True
    return exits, fire


def test_twins_are_drawn_only_from_decision_bars_after_the_signals_exit_within_7_days() -> None:
    b = _walk()
    ev = _events(b)
    exits, fire = _signals(b, ev)
    draws = SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key="after")
    seen = 0
    for d in draws:
        x = int(exits[d.signal_k])
        bars = d.decision_bars[d.decision_bars >= 0]
        if x < 0:
            assert not len(bars)
            continue
        seen += len(bars)
        assert (bars > x).all()
        assert (b.ta[bars] <= b.ta[x] + SIM.TWIN_DAYS_INTRADAY * DAY).all()
    assert seen > 1_000


def test_daily_twins_are_drawn_from_the_20_bars_after_the_exit() -> None:
    b = _walk("1d", 1_500)
    ev = _events(b, every=23)
    exits, fire = _signals(b, ev)
    for d in SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key="1d"):
        x = int(exits[d.signal_k])
        bars = d.decision_bars[d.decision_bars >= 0]
        if len(bars):
            assert (bars > x).all() and (bars <= x + SIM.TWIN_BARS_1D).all()


def test_no_twin_lands_on_a_bar_where_any_instance_of_the_family_fires() -> None:
    b = _walk()
    ev = _events(b)
    exits, fire = _signals(b, ev)
    rng = np.random.default_rng(3)
    fire[rng.random(len(b)) < 0.3] = True          # the family's other variants and cells fire here too
    k = int(np.flatnonzero(exits >= 0)[0])
    pool = SIM.twin_pool(b, ev, k, exit_bar=int(exits[k]), family_fire=fire)
    open_pool = SIM.twin_pool(b, ev, k, exit_bar=int(exits[k]), family_fire=np.zeros(len(b), bool))
    assert len(pool) and not fire[pool].any() and fire[open_pool].any()
    for d in SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key="fire"):
        bars = d.decision_bars[d.decision_bars >= 0]
        assert not fire[bars].any()


def test_twins_share_the_signals_side_and_decision_hour_within_one_hour() -> None:
    b = _walk()
    ev = _events(b)
    exits, fire = _signals(b, ev)
    for d in SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key="hour"):
        k = d.signal_k
        i = int(ev.i[k])
        assert d.stratum[:4] == ("XAUUSD", "1h", int(ev.side[k]), int(b.ta[i] // HOUR % 24))
        assert d.stratum[4] is None
        bars = d.decision_bars[d.decision_bars >= 0]
        off = (b.ta[bars] - b.ta[i]) % DAY
        assert (np.minimum(off, DAY - off) <= SIM.TWIN_HOUR_TOL_H * HOUR).all()


def test_twins_keep_the_signals_regime_level_when_the_card_registers_one() -> None:
    b = _walk()
    ev = _events(b)
    exits, fire = _signals(b, ev)
    regime = (np.arange(len(b)) // 48 % 3).astype(np.int64)
    for d in SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key="reg", regime=regime):
        lvl = int(regime[int(ev.i[d.signal_k])])
        assert d.stratum[4] == lvl
        bars = d.decision_bars[d.decision_bars >= 0]
        assert (regime[bars] == lvl).all()


def test_no_twin_is_decided_at_or_after_the_windows_end() -> None:
    """A twin past the window end would read the next window's bars (explore twins scored on CONFIRM, or holdout)."""
    b = _walk()
    ev = _events(b)
    exits, fire = _signals(b, ev)
    b.meta["end"] = int(b.ta[len(b) // 2])
    for d in SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key="end"):
        bars = d.decision_bars[d.decision_bars >= 0]
        assert (b.ta[bars] < b.meta["end"]).all()


def test_every_traded_signal_gets_twenty_twins() -> None:
    b = _walk()
    ev = _events(b)
    exits, fire = _signals(b, ev)
    draws = SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key="m")
    assert SIM.TWIN_M == 20 and len(draws) == len(ev)
    traded = [d for d in draws if exits[d.signal_k] >= 0 and d.n_eligible > 0]
    assert len(traded) > 50
    for d in traded:
        assert d.decision_bars.dtype == np.int64 and len(d.decision_bars) == SIM.TWIN_M
        assert (d.decision_bars >= 0).all()
    net = SIM.simulate_twins(b, ev, traded, costs=SIM.default_model())
    assert net.shape == (len(traded), SIM.TWIN_M)
    assert np.isfinite(net).mean() > 0.95


def test_a_signal_with_no_eligible_bar_gets_no_twins_and_nan_net() -> None:
    b = _walk()
    ev = _events(b)
    exits, _ = _signals(b, ev)
    fire = np.ones(len(b), bool)                  # the family fires everywhere: nothing is eligible
    draws = SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key="none")
    assert all(d.n_eligible == 0 and (d.decision_bars == -1).all() for d in draws)
    net = SIM.simulate_twins(b, ev, draws[:3], costs=SIM.default_model())
    assert net.shape == (3, SIM.TWIN_M) and np.isnan(net).all()


def test_twin_draws_are_deterministic_for_a_seed_key_and_change_with_it() -> None:
    b = _walk()
    ev = _events(b)
    exits, fire = _signals(b, ev)
    one = SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key="unit-A")
    two = SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key="unit-A")
    other = SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key="unit-B")
    assert all(np.array_equal(x.decision_bars, y.decision_bars) for x, y in zip(one, two))
    assert any(not np.array_equal(x.decision_bars, y.decision_bars) for x, y in zip(one, other) if x.n_eligible > 1)
    a = SIM.simulate_twins(b, ev, one, costs=SIM.default_model())
    assert np.array_equal(a, SIM.simulate_twins(b, ev, two, costs=SIM.default_model()), equal_nan=True)


def test_a_signals_draws_do_not_depend_on_the_other_events_in_the_cell() -> None:
    """The draw is keyed on the signal (seed key, instrument, timeframe, bar, side), not its row number."""
    b = _walk()
    ev = _events(b)
    exits, fire = _signals(b, ev)
    full = SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key="k")
    keep = np.arange(len(ev)) >= 5
    part = SIM.draw_twins(b, ev.subset(keep), exits=exits[keep], family_fire=fire, seed_key="k")
    assert all(np.array_equal(x.decision_bars, y.decision_bars) for x, y in zip(full[5:], part))


def test_twin_stop_target_and_hold_are_the_signals_atr_multiples_at_the_twins_bar() -> None:
    """Flat bars with the range doubled from bar 50: the twin at bar 70 gets the signal's 1.2 / 3.0 ATR at bar 70's
    ATR, which differs from the signal's; the hold stays the signal's."""
    b = flat_bars(120)
    b.h[50:] = 101.0
    b.l[50:] = 98.0
    a20 = atr(b, SIM.RISK_FLOOR_ATR_N)
    assert a20[70] > 1.5 * a20[40]
    eb = EB()
    eb.add(40, 1, stop_d=1.2 * a20[40], tgt_d=3.0 * a20[40], hold=7)
    ev = eb.build()
    tev = SIM._twin_events(b, ev, 0, 70, a20, opposite=False)
    assert tev is not None
    assert int(tev.i[0]) == 70 and int(tev.side[0]) == 1 and int(tev.hold[0]) == 7
    assert tev.stop_d[0] == pytest.approx(1.2 * a20[70])
    assert tev.tgt_d[0] == pytest.approx(3.0 * a20[70])


def test_absolute_stops_targets_and_entry_offsets_scale_with_the_twins_atr_from_its_close() -> None:
    b = flat_bars(120)
    b.h[50:] = 101.0
    b.l[50:] = 98.0
    b.c[70] = 100.4
    a20 = atr(b, SIM.RISK_FLOOR_ATR_N)
    eb = EB()
    eb.add(40, -1, kind=1, px=100.0 - 0.5 * a20[40], valid=3, stop=100.0 + 0.5 * a20[40], tgt=100.0 - 2.5 * a20[40],
           hold=5)
    ev = eb.build()
    k = 70
    tev = SIM._twin_events(b, ev, 0, k, a20, opposite=False)
    assert tev is not None
    px = 100.4 - 0.5 * a20[k]
    assert tev.px[0] == pytest.approx(px)
    assert tev.stop[0] == pytest.approx(px + 1.0 * a20[k])
    assert tev.tgt[0] == pytest.approx(px - 2.0 * a20[k])
    assert int(tev.kind[0]) == 1 and int(tev.valid[0]) == 3
    opp = SIM._twin_events(b, ev, 0, k, a20, opposite=True)
    assert opp is not None
    px_o = 100.4 + 0.5 * a20[k]
    assert int(opp.side[0]) == 1
    assert opp.px[0] == pytest.approx(px_o)
    assert opp.stop[0] == pytest.approx(px_o - 1.0 * a20[k])
    assert opp.tgt[0] == pytest.approx(px_o + 2.0 * a20[k])


def test_a_twin_replayed_at_the_signals_own_bar_reproduces_the_signals_net_r() -> None:
    """Same replay path and cost path: the twin walk is the signal's walk (guards against the two drifting apart)."""
    b = _walk()
    ev = _events(b, every=53)
    tr = SIM.simulate(b, ev, control="random_side", seed_key="own")
    assert len(tr) > 30
    draws = []
    for row in tr:
        k = int(np.flatnonzero((ev.i == int(row[COL["i"]])) & (ev.side == int(row[COL["side"]])))[0])
        draws.append(SIM.TwinDraw(k, np.array([int(ev.i[k])], np.int64), ("XAUUSD", "1h", int(ev.side[k]), 0, None),
                                  1))
    net = SIM.simulate_twins(b, ev, draws, costs=SIM.default_model())
    np.testing.assert_allclose(net[:, 0], tr[:, COL["gross"]] - tr[:, COL["cost"]], rtol=0, atol=1e-12)


def test_twins_pay_swap_by_the_strict_interior_rollover_rule() -> None:
    """A daily twin held over 17:00 New York pays the same financing the signal's cost path charges (#343)."""
    b = _walk("1d", 300)
    eb = EB()
    a = atr(b, SIM.RISK_FLOOR_ATR_N)
    eb.add(100, 1, stop_d=50 * a[100], hold=3)        # no stop/target: a 3-bar time exit, three rollovers held
    ev = eb.build()
    d = SIM.TwinDraw(0, np.array([150], np.int64), ("XAUUSD", "1d", 1, 0, None), 1)
    net = SIM.simulate_twins(b, ev, [d], costs=SIM.default_model())[0, 0]
    tev = SIM._twin_events(b, ev, 0, 150, a, opposite=False)
    assert tev is not None
    risk = SIM.order_risk(b, tev, 0, a)
    assert risk is not None
    fill, exit_px = b.o[151], b.c[153]
    cost = SIM.cost_price("XAUUSD", 1, fill, float(b.spr[151]), 0, int(b.t[151]), int(b.ta[153]))
    assert SIM.default_model().swap_nights("XAUUSD", int(b.t[151]), int(b.ta[153])) >= 1
    assert net == pytest.approx((exit_px - fill) / risk - cost / risk)


def test_an_opposite_side_twin_at_the_same_instant_flips_a_symmetric_trade() -> None:
    b = _walk()
    ev = _events(b, every=53, stop_atr=1.5, tgt_atr=1.5, hold=10**6)
    exits, _ = _signals(b, ev)
    ks = [k for k in range(len(ev)) if exits[k] >= 0]
    draws = SIM.same_instant_draws(b, ev, ks)
    sig = SIM.simulate_twins(b, ev, draws, costs=SIM.default_model())[:, 0]
    opp = SIM.simulate_twins(b, ev, draws, costs=SIM.default_model(), opposite_side=True)[:, 0]
    big = np.abs(sig) > 0.5
    assert big.sum() > 20
    assert (np.sign(sig[big]) == -np.sign(opp[big])).mean() > 0.95


def test_excess_r_is_the_signal_minus_the_nanmean_of_its_twins() -> None:
    s = np.array([1.0, -0.5, 0.2])
    tw = np.array([[0.0, 1.0, np.nan], [np.nan, np.nan, np.nan], [0.2, 0.2, 0.2]])
    ex = SIM.excess_r(s, tw)
    assert ex[0] == pytest.approx(0.5) and np.isnan(ex[1]) and ex[2] == pytest.approx(0.0)


def test_twin_correlation_is_the_day_clustered_correlation_of_signal_and_twin_mean() -> None:
    rng = np.random.default_rng(1)
    n = 4_000
    day = np.repeat(np.arange(n // 4), 4)
    s = rng.normal(size=n)
    indep = rng.normal(size=(n, 20))
    assert abs(SIM.twin_correlation(s, indep, day)) < 0.08
    tied = s[:, None] + 0.1 * rng.normal(size=(n, 20))
    assert SIM.twin_correlation(s, tied, day) > 0.95
    assert np.isnan(SIM.twin_correlation(s[:4], indep[:4], day[:4]))


# ------------------------------------------------------------------ run.py wiring
def test_the_first_eval2_read_of_2018_25_is_batch_15_and_of_test_old_batch_1(tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    led = tmp_path / RUN.READS_LEDGER
    monkeypatch.setattr(RUN, "default_registry", lambda: tmp_path / "research_registry.jsonl")
    assert RUN.read_batch_index("confirm") == 15
    assert RUN.read_batch_index("test_old") == 1
    rows = [{"window": "explore", "ts": "2026-11-07T09:38:00+00:00"},
            {"window": "confirm", "ts": "2026-11-12T10:00:00+00:00"},
            {"window": "confirm", "ts": "2026-11-12T11:00:00+00:00", "resume": True},
            {"window": "test_old", "ts": "2026-11-10T09:00:00+00:00"}]
    led.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert RUN.read_batch_index("confirm") == 16       # a resume continues the same read, it is not a batch
    assert RUN.read_batch_index("test_old") == 2


def test_unit_twins_frame_excludes_bars_where_a_sibling_unit_of_the_family_fires(
        monkeypatch: pytest.MonkeyPatch) -> None:
    b = _walk()
    ev = _events(b, every=41)
    sib = _events(b, every=7, start=61)
    units = [{"unit": "FX-V0", "family": "FX"}, {"unit": "FX-V1", "family": "FX"}, {"unit": "OT-V0", "family": "OT"}]
    by = {"FX-V0": ev, "FX-V1": sib, "OT-V0": _events(b, every=3)}
    monkeypatch.setattr(D, "units", lambda: units)
    monkeypatch.setattr(D, "unit_events", lambda u, win, ctx, **kw: {("XAUUSD", "1h"): (b, by[u["unit"]])})
    ctx = D.Ctx(window="explore", aux={"costs": SIM.default_model()})
    df, draws = RUN.unit_twins(units[0], "explore", ctx)
    assert list(df.columns[:12]) == ["cell_id", "family", "inst", "tf", "side", "day", "decision_ns", "net_r",
                                     "twin_mean_net_r", "excess_r", "cost_r", "year"]
    assert len(df) > 30 and (df["family"] == "FX").all()
    fire = np.zeros(len(b), bool)
    fire[ev.i] = fire[sib.i] = True
    for d in draws[("XAUUSD", "1h")]:
        bars = d.decision_bars[d.decision_bars >= 0]
        assert not fire[bars].any()
    np.testing.assert_allclose(df["excess_r"], df["net_r"] - df["twin_mean_net_r"])
    assert pd.api.types.is_integer_dtype(df["day"]) and (df["n_twins"] <= SIM.TWIN_M).all()
