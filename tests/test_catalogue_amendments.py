"""WS-3 catalogue screen: the quant sign-off amendments C1-C4 (2026-11-07), landed before the explore read."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue import dispatcher as D
from goldbot.research.catalogue import sim as SIM
from goldbot.research.catalogue import stats as S
from goldbot.research.catalogue.core import EB
from goldbot.research.catalogue.costs import load_table
from goldbot.research.catalogue.data import DAY, Bars
from tests.test_catalogue_lookahead import ctx_for, synth


def _nulls(passes: dict[str, bool]) -> list[dict]:
    spec = {"NULL-RAND-5m": (5870, 1000), "NULL-RAND-15m": (1890, 1000), "NULL-RAND-30m": (965, 1000),
            "NULL-RAND-1h": (499, 1000), "NULL-RAND-4h": (1627, 1000), "NULL-RAND-1d": (277, 300),
            "NULL-RAND-1w": (46, 300), "NULL-XS-RANDOM": (1384, 300), "NULL-LONGCLOSE-US500": (1449, 300),
            "NULL-LONGCLOSE-IDX": (6522, 300), "NULL-LONGCLOSE-GOLD": (2077, 300)}
    return [{"unit": u, "pass": passes.get(u, False), "n_events": n, "floor": f} for u, (n, f) in spec.items()]


def test_null_void_rule() -> None:
    v = S.null_void(_nulls({}))
    assert sorted(v["feasible_nulls"]) == ["NULL-RAND-15m", "NULL-RAND-4h", "NULL-RAND-5m", "NULL-XS-RANDOM"]
    assert set(v["cannot_reach_floor"]) == {"NULL-RAND-30m", "NULL-RAND-1h", "NULL-RAND-1d", "NULL-RAND-1w"}
    assert v["void_at"] == 2 and not v["void"]
    assert not S.null_void(_nulls({"NULL-RAND-5m": True}))["void"]                       # 1 of 4: not void
    assert S.null_void(_nulls({"NULL-RAND-5m": True, "NULL-XS-RANDOM": True}))["void"]   # 2 of 4: void
    # infeasible nulls never count
    assert not S.null_void(_nulls({"NULL-RAND-1h": True, "NULL-RAND-1w": True, "NULL-RAND-5m": True}))["void"]
    assert S.null_void(_nulls({"NULL-LONGCLOSE-GOLD": True}))["void"]                    # a sentinel pass: void


def test_hac_t_on_overlapping_holds() -> None:
    rng = np.random.default_rng(7)
    n, L = 40_000, 10
    e = rng.normal(0, 1, n + L)
    x = 0.5 + np.convolve(e, np.ones(L), "valid")[:n]           # daily sums of holds overlapping L fill days
    true_t = float(x.mean()) / math.sqrt(L * L / n)              # true long-run variance L^2 for unit shocks
    naive = S._t(x)
    hac = S.hac_t(x, L)
    assert abs(hac / true_t - 1) < 0.15
    assert hac < naive / 1.5
    iid = rng.normal(0.01, 1, n)
    assert abs(S.hac_t(iid, L) / S._t(iid) - 1) < 0.05
    assert S.hac_t(iid, 1) == S._t(iid)


def test_overlap_lag_from_holds() -> None:
    days = np.arange(0, 400, 2)                                   # a fill every other day
    tr = pd.DataFrame({"t_fill": days * DAY, "hold_nights": np.full(len(days), 20.0)})
    assert S.overlap_lag(tr) == 11                               # ceil(20 nights x 200 fill days / 399 days)
    assert S.overlap_lag(tr.assign(hold_nights=0.0)) == 1


def test_sim_reproduces_sprint3000_fixture() -> None:
    """Three gold 1d trades through catalogue.sim vs sprint3000.engine.walk (each daily bar as one tape step):
    a long gapping through its stop (filled at the open), a short hitting its target, a long held Friday -> Monday.
    Gross R must match exactly. Costs follow different conventions, so they are checked against hand values:
    sprint3000 scales the round trip by entry / reference price (bps model) and charges its measured swap with a
    Wednesday triple; the catalogue charges the cost of record in USD (the fill session's: 22:00 UTC is the thin hours
    after the New York close, $0.293, backlog 146; + $0.11 overshoot on stop exits) and
    the C2 financing per 17:00 New York rollover held (see the comment below).
    This test found that an exit exactly AT 21:00 was charged that night's rollover; fixed before the run."""
    from goldbot.research.sprint3000.engine import walk as s3walk
    from goldbot.research.sprint3000.market import Tape

    w0 = 20                                                      # flat warm-up bars: ATR20 (risk floor) defined
    m = w0 + 12
    bd = pd.bdate_range("2011-12-05", periods=m, tz="UTC")       # 20 business days = 4 weeks: weekdays unchanged
    t = np.array([int((d - pd.Timedelta(hours=2)).value) for d in bd], np.int64)     # prior day 22:00
    ta = np.array([int((d + pd.Timedelta(hours=21)).value) for d in bd], np.int64)
    o = np.full(m, 1300.0)
    h = np.full(m, 1302.0)
    lo = np.full(m, 1298.0)
    c = np.full(m, 1300.0)
    o[w0 + 3], h[w0 + 3], lo[w0 + 3], c[w0 + 3] = 1290.0, 1291.0, 1288.0, 1289.0     # trade 1: gap through the stop
    o[w0 + 7], h[w0 + 7], lo[w0 + 7], c[w0 + 7] = 1299.0, 1301.0, 1293.0, 1295.0     # trade 2: short target 1294 hit
    c[w0 + 10], h[w0 + 10] = 1306.0, 1307.0                                          # trade 3: Monday close 1306
    b = Bars("XAUUSD", "1d", t, ta, o, h, lo, c, np.full(m, 0.3), np.zeros(m, bool), np.zeros(m, np.int32),
             np.zeros(m))
    b.meta.update(dec_from=int(t[w0]), end=int(ta[-1]) + DAY)
    assert pd.Timestamp(ta[w0 + 10]).day_name() == "Monday" and pd.Timestamp(t[w0 + 9]).day_name() == "Thursday"
    eb = EB()
    eb.add(w0 + 1, 1, stop_d=5.0, hold=5)
    eb.add(w0 + 5, -1, stop_d=5.0, tgt_d=6.0, hold=5)
    eb.add(w0 + 8, 1, stop_d=10.0, hold=2)
    tr = pd.DataFrame(SIM.simulate(b, eb.build(), control="random_side", seed_key="fx"), columns=SIM.TRADE_COLS)
    assert len(tr) == 3
    tp = Tape.of(t, o, h, lo, c)
    for k, (side, risk, tgt_r, last) in enumerate(((1, 5.0, np.nan, 6), (-1, 5.0, 1.2, 10), (1, 10.0, np.nan, 10))):
        ex = s3walk(tp, int(tr["f"][k]), w0 + last, side, risk, tgt_r, np.nan, -1)
        assert tr["gross"][k] == pytest.approx(ex.gross / risk, abs=1e-9)
    assert tr["gross"].tolist() == pytest.approx([-2.0, 1.2, 0.6])
    # EVAL-1 C2 financing (benchmark rates 0 in the default model: the measured markup only, /365); the rollover is
    # 17:00 New York (22:00 UTC in December); a fill or an exit AT the rollover is not held through it; gold's triple
    # day is Wednesday, Sat/Sun 0. Tue 22:00 -> Wed 22:00 and Mon -> Tue: none; Thu 22:00 -> Mon 21:00: Friday's
    # (calendar: Fri, Sat, Sun).
    assert tr["hold_nights"].tolist() == [0, 0, 3]
    assert tr["swap_nights"].tolist() == [0, 0, 1]
    tab = load_table()["instruments"]["XAUUSD"]
    lng, sht = tab["markup_long"] / 100 / 365 * 1300.0, tab["markup_short"] / 100 / 365 * 1300.0
    thin = 0.293                    # every fill is a 22:00 UTC daily open: the thin hours (dearest session, quant)
    assert tr["cost"][0] == pytest.approx((thin + 0.11) / 5.0, abs=1e-9)              # gapped stop: overshoot
    assert tr["cost"][1] == pytest.approx(thin / 5.0, abs=1e-9)
    assert tr["cost"][2] == pytest.approx((thin + lng) / 10.0, abs=1e-9)
    assert sht > 0


def test_s17_history_masks_test_old_months() -> None:
    """Changing any 2006-02..2009-12 monthly RETURN (the TEST-OLD outcomes) leaves S17's explore events unchanged (C4)."""
    insts = ["XAUUSD", "XAGUSD", "US500", "EURUSD", "GBPUSD", "USDJPY"]
    bs = {k: synth(k, "1d", n=800, seed=i + 3) for i, k in enumerate(insts)}
    ctx = ctx_for(next(iter(bs.values())))
    for u in (x for x in D.units() if x["family"] == "S17"):
        base = D.XS_DETECTORS["S17"](bs, u["params"], ctx)
        moved_ctx = ctx_for(next(iter(bs.values())))
        for inst in D.LONG_KEYS:
            av, cl, fut = moved_ctx.aux[f"long_{inst}"]
            cl = cl.copy()
            m = av >= pd.Timestamp("2006-02-01", tz="UTC").value            # level shift inside Feb 2006
            m2 = av >= pd.Timestamp("2008-06-01", tz="UTC").value           # and inside Jun 2008
            cl[m] *= 1.5
            cl[m2] *= 0.7
            moved_ctx.aux[f"long_{inst}"] = (av, cl, fut)
        moved = D.XS_DETECTORS["S17"](bs, u["params"], moved_ctx)
        assert sum(len(e) for e in base.values()) > 0
        for k in base:
            np.testing.assert_array_equal(base[k].table(), moved[k].table())
