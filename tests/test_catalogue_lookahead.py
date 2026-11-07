"""WS-3 catalogue screen: truncation-prefix look-ahead test per detector (plan tests_reviews), holdout/test_old seals,
close-only fills, OCO conservatism, the buy-every-close null failing explore, the budget abort and the gold cost."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue import core as K
from goldbot.research.catalogue import dispatcher as D
from goldbot.research.catalogue import run as R
from goldbot.research.catalogue import sim as SIM
from goldbot.research.catalogue import stats as S
from goldbot.research.catalogue.core import EB, Ctx
from goldbot.research.catalogue.data import DAY, HOUR, TF_NS, Bars, window
from goldbot.research.windows import HoldoutSealed, TestOldSealed

T0 = int(pd.Timestamp("2012-01-02", tz="UTC").value)


def synth(inst: str = "XAUUSD", tf: str = "1d", n: int = 900, seed: int = 1, drift: float = 0.0,
          gap: float = 0.001) -> Bars:
    rng = np.random.default_rng(seed)
    step = TF_NS[tf]
    r = rng.standard_t(4, n) * 0.01 + drift
    c = 100 * np.exp(np.cumsum(r))
    o = np.concatenate([[100.0], c[:-1]]) * (1 + rng.normal(0, gap, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.006, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.006, n)))
    t = T0 + np.arange(n, dtype=np.int64) * step
    if tf == "1d":
        t = T0 + 21 * HOUR + np.arange(n, dtype=np.int64) * DAY
    b = Bars(inst, tf, t, t + step, o, h, lo, c, np.full(n, 0.01), np.zeros(n, bool), np.zeros(n, np.int32),
             rng.integers(100, 1000, n).astype(float))
    b.meta.update(dec_from=int(t[0]), end=int(t[-1]) + 10 * step)
    return b


def ctx_for(b: Bars) -> Ctx:
    ctx = Ctx(window="explore")
    ev = b.t[np.arange(50, len(b), 23)] + 2 * HOUR
    for name in D.EVENT_SETS:
        ctx.events[name] = ev
    days = (b.ta[::17] - 1) // DAY + 2
    for ex in ("US", "DE", "UK"):
        ctx.events[f"holiday_{ex}"] = days
    for i, ccy in enumerate(D.RATES):
        ctx.aux[f"rate_{ccy}"] = (np.array([b.t[0] - DAY]), np.array([float(3 * i)]))
    for inst in D.LONG_KEYS:
        av = np.arange(int(b.t[0]) - 40 * 365 * DAY, int(b.ta[-1]), DAY, dtype=np.int64)
        rng = np.random.default_rng(len(inst))
        ctx.aux[f"long_{inst}"] = (av, 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(av)))), np.zeros(len(av), bool))
    return ctx


def _same(full: K.Events, part: K.Events, n: int) -> None:
    a = full.table()
    a = a[a[:, 0] < n - 1]            # events decided before the cut's last bar
    p = part.table()
    p = p[p[:, 0] < n - 1]
    assert a.shape == p.shape, f"event count differs before bar {n}: {len(a)} vs {len(p)}"
    np.testing.assert_allclose(np.nan_to_num(a, nan=-9e9), np.nan_to_num(p, nan=-9e9), rtol=1e-9, atol=1e-9)
    for m_full, m_part in ((full.exit_long, part.exit_long), (full.exit_short, part.exit_short)):
        if m_full is not None and m_part is not None:
            assert np.array_equal(m_full[: n - 1], m_part[: n - 1])


PER_CELL = sorted(D.DETECTORS)
TF_FOR = {"S02": "4h", "S04": "4h", "S08": "4h", "B08": "5m", "F10": "5m", "B10": "30m", "B04": "1h", "F03": "1h",
          "NULL_RAND": "1h"}
INST_FOR = {"S02": "US500", "S04": "US500", "S08": "EURUSD", "S11": "EURUSD"}


# parameter overrides that make the synthetic series produce events, so no truncation test is vacuous; the
# detector code paths are the registered ones (only thresholds/grids change)
TEST_OVERRIDE = {"B04": {"gold_grid": 2.0}, "F03": {"gold_grid": 2.0}, "S26": {"z": 0.5}, "B09": {"g": 0.05},
                 "C08": {}, "C09": {}, "C10": {"range": 0.3}}
SYNTH_KW: dict[str, dict[str, float]] = {"B09": {"gap": 0.02}}


def _units_using(det: str) -> list[dict]:
    return [u for u in D.units() if u["detector"] == det] or [{"params": {}}]


@pytest.mark.parametrize("det", PER_CELL)
def test_truncation_prefix_no_lookahead(det: str) -> None:
    tf = TF_FOR.get(det, "1d")
    b = synth(INST_FOR.get(det, "XAUUSD"), tf, n=1200 if tf != "5m" else 1500, gap=SYNTH_KW.get(det, {}).get("gap", 0.001))
    ctx = ctx_for(b)
    fn = D.DETECTORS[det]
    total = 0
    for u in _units_using(det):
        params = {**u["params"], **TEST_OVERRIDE.get(det, {})}
        full = fn(b, params, ctx)
        total += len(full)
        for cut in range(260, len(b), 97):
            _same(full, fn(b.truncate(cut), params, ctx), cut)
    if det not in ("C08",):                     # cup-with-handle is too rare on 1,200 synthetic bars
        assert total > 0, f"{det}: the synthetic series produced no event, the test would be vacuous"


@pytest.mark.parametrize("det", sorted(D.XS_DETECTORS))
def test_truncation_prefix_cross_sectional(det: str) -> None:
    insts = ["XAUUSD", "XAGUSD", "XTIUSD", "US500", "USTEC", "DE40", "EURUSD", "GBPUSD", "AUDUSD", "USDJPY",
             "USDCAD", "USDCHF", "NZDUSD"]
    bs = {k: synth(k, "1d", n=700, seed=i + 3) for i, k in enumerate(insts)}
    ctx = ctx_for(next(iter(bs.values())))
    fn = D.XS_DETECTORS[det]
    for u in _units_using(det):
        full = fn(bs, u["params"], ctx)
        for cut in (400, 520, 650):
            part = fn({k: b.truncate(cut) for k, b in bs.items()}, u["params"], ctx)
            for k in full:
                _same(full[k], part.get(k, K.empty()), cut)


def test_every_registered_unit_has_a_detector() -> None:
    for u in D.units():
        assert u["detector"] in D.DETECTORS or u["detector"] in D.XS_DETECTORS, u["unit"]


def test_holdout_and_test_old_sealed() -> None:
    with pytest.raises(HoldoutSealed):
        window("holdout")
    with pytest.raises(TestOldSealed):
        window("test_old")
    assert window("confirm").end == int(pd.Timestamp("2025-10-01", tz="UTC").value)


def test_daily_decision_never_sees_a_later_bar() -> None:
    """A 21:00 decision never uses a bar that becomes available later (yahoo 23:00 rule): fills come after ta[i]."""
    b = synth(n=300)
    ev = EB()
    ev.add(100, 1, stop_d=1.0, hold=1)
    tr = SIM.simulate(b, ev.build(), control="random_side", seed_key="x")
    assert tr[0, SIM.TRADE_COLS.index("t_fill")] >= b.ta[100]


def test_close_only_bar_fills_and_stops_at_the_close() -> None:
    j = 30                                                  # after the ATR20 warm-up (risk floor)
    b = synth(n=60)
    b.co[j + 1] = True
    b.l[j + 1] = b.h[j + 1] = b.c[j + 1]
    ev = EB()
    ev.add(j, 1, stop=b.c[j + 1] * 0.5, hold=3)
    tr = SIM.simulate(b, ev.build(), control="random_side", seed_key="x")
    assert tr[0, SIM.TRADE_COLS.index("fill")] == pytest.approx(b.c[j + 1])
    b2 = synth(n=60)
    b2.co[j + 2] = True
    b2.c[j + 2] = b2.o[j + 1] * 0.9
    b2.h[j + 2] = b2.l[j + 2] = b2.c[j + 2]
    b2.l[j + 1] = min(b2.l[j + 1], b2.o[j + 1])
    ev2 = EB()
    ev2.add(j, 1, stop=b2.o[j + 1] * 0.95, hold=5)
    tr2 = SIM.simulate(b2, ev2.build(), control="random_side", seed_key="x")
    assert tr2[0, SIM.TRADE_COLS.index("x")] == j + 2      # stopped on the close-only bar, at its close
    order_risk = b2.c[j] - b2.o[j + 1] * 0.95               # R on the order's risk at the decision close (quant C7)
    assert tr2[0, SIM.TRADE_COLS.index("gross")] == pytest.approx((b2.c[j + 2] - b2.o[j + 1]) / order_risk)


def test_oco_both_legs_on_one_bar_is_a_full_loss() -> None:
    b = synth(n=60)
    j = 20
    eb = EB()
    eb.oco_pair(j, long=dict(kind=1, px=b.h[j + 1] - 1e-6, valid=1, stop=b.l[j + 1] - 5, hold=2),
                short=dict(kind=1, px=b.l[j + 1] + 1e-6, valid=1, stop=b.h[j + 1] + 5, hold=2))
    tr = SIM.simulate(b, eb.build(), control="random_side", seed_key="x")
    assert len(tr) == 1 and tr[0, SIM.TRADE_COLS.index("gross")] == pytest.approx(-1.0)


def test_gold_cost_is_the_cost_of_record() -> None:
    rt = SIM.round_trip("XAUUSD", 4000.0, 0.05)
    assert rt == pytest.approx(0.166)
    assert rt / 4000 * 1e4 == pytest.approx(0.415, abs=0.01)          # ~0.4 bp at $4,000
    assert SIM.overshoot("XAUUSD", rt) == pytest.approx(0.11)


def test_buy_every_close_null_fails_explore() -> None:
    """A drifting market: buying every close earns gross, but the random-time same-side twin earns the same drift,
    so the matched excess t stays below 2 and the null fails explore (A2 drift guard)."""
    frames = []
    for i in range(2):
        b = synth("US500", "1d", n=2400, seed=i + 1, drift=0.0006)
        b.meta.update(dec_from=int(b.t[260]), end=int(b.ta[-1]) + 1)
        ev = D.null_longclose(b, {}, Ctx("explore"))
        tr = SIM.simulate(b, ev, control="random_time", seed_key=f"null{i}")
        df = pd.DataFrame(tr, columns=SIM.TRADE_COLS)
        df["inst"], df["tf"] = f"I{i}", "1d"
        frames.append(df)
    s = S.summarize(pd.concat(frames, ignore_index=True))
    assert s["gross"] > 0
    g = S.explore_gates(s, ["1d"], None)
    assert not all(g.values())
    assert not g["t_excess"]


def test_enumerator_aborts_over_quarter_budget() -> None:
    with pytest.raises(R.BudgetAbort):
        R.assert_budget(n_units=200, used=3300, cap=3500, reserved=12)
    assert R.assert_budget(n_units=163, used=3173, cap=3500, reserved=12) == 152


def test_bh_and_null_bound() -> None:
    assert S.bh(np.array([0.001, 0.2, 0.03])).tolist() == [True, False, True]
    assert S.null_void_k(4) == 2


def test_zigzag_pivots_known_only_from_confirmation() -> None:
    b = synth(n=600)
    piv = K.zigzag(b, 2.0)
    assert np.all(piv.conf > piv.bar)
    part = K.zigzag(b.truncate(400), 2.0)
    m = piv.known_at(398)
    assert np.array_equal(piv.bar[:m], part.bar[:m]) and np.array_equal(piv.conf[:m], part.conf[:m])


def test_block_bootstrap_p() -> None:
    rng = np.random.default_rng(0)
    assert S.block_boot_p(rng.normal(0.5, 1, 400), resamples=2000) < 0.01
    assert 0.2 < S.block_boot_p(rng.normal(0.0, 1, 400), resamples=2000) < 0.95


def test_window_read_is_claimed_once(tmp_path, monkeypatch) -> None:
    reg = tmp_path / "research_registry.jsonl"
    monkeypatch.setattr(R, "default_registry", lambda: reg)
    R._claim("confirm")
    with pytest.raises(SystemExit):
        R._claim("confirm")
    R._claim("confirm", resume=True)          # same frozen commit: a crashed read may continue, recorded
