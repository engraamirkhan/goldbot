"""EVAL-1 precondition (ii) (docs/research/evaluation-audit-2026-11-07.md): "every registered detector x control
pair, run on a driftless random walk with the unit's own bar structure, gives |t_excess| < 2 and |t_net + cost drag|
< 2". The WS-3 audit (ws3_explore_audit_1107) found the frozen harness b3f14388 gave excess t of 4-40 on zero-edge
data: no risk floor (D1), the OCO random-side twin forced to a full loss (D2), the random-time twin of a short with
its target above entry (D3) and the intraday random-time twin drawn before the signal (D4). This is the regression
guard that would have caught them, plus one behaviour test per defect.

Construction
  bars     a driftless (martingale) price path sampled up to SUB_MAX times per bar, so every bar's high/low/close is
           a real path and stop/limit fills at the order price are exact; weekdays only; larger gaps after weekends;
           more volatile bars at release instants (still driftless); the unit's own timeframes and its first
           instrument (gold when it has gold; cross-sectional units: all of their instruments)
  context  synthetic release/FOMC/holiday schedules, two-level policy rates, 40 years of driftless daily history
  pair     every registered unit x its registered control, or both controls when the unit has none (the harness picks
           by long share, so either can run); cells pooled as in dispatcher.unit_trades
  stats    the harness's own: HAC t of the daily-summed excess ("t_excess", the gated number) and the same HAC t of
           the daily-summed gross R (= net + cost drag), and the mean excess and gross R per trade

Decision rule (quant C9, eval1_signoff_1107, amended at the re-verify): "Each of the 3 samples pools walks until
1,000 trades (at most 8 walks). A pair fails if |t_pooled| >= 3.5, or if |mean| >= 0.03 R/trade with |t_pooled| >= 2.
A failure is re-tested once on 3 fresh samples, and it is confirmed only if the retest fails with the same sign." The
retest is samples 3-5 (walks 24-47). A pair with fewer than MIN_TRADES (30) trades after 24 walks is excluded from
EVAL-1 screening and listed. Listed in goldbot/research/catalogue/rw_guard.json, which this guard keeps exact both
ways and run.py leaves out of the screen: "sparse" units (the real trades do not depend on the control) and "failed"
pairs (strict xfail here). The first rule (|t| >= 2, same sign, on all 3 samples) let a bias of expected t 2 per
sample escape 87 % of the time and skipped 32 sparse pairs unverified; the C9 rule without the same-sign
confirmation and with 30-trade samples failed two zero-edge pairs whose retest flipped sign."""
from __future__ import annotations

import hashlib
from functools import lru_cache

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue import dispatcher as D
from goldbot.research.catalogue import sim as SIM
from goldbot.research.catalogue import stats as S
from goldbot.research.catalogue.core import EB, Ctx, Events
from goldbot.research.catalogue.data import DAY, HOUR, MIN, TF_NS, Bars

T0 = int(pd.Timestamp("2010-01-04", tz="UTC").value)          # a Monday
PATH_POINTS = 6_000_000      # path steps per cell (sub-steps per bar = PATH_POINTS / bars, within 64..SUB_MAX)
SUB_MAX = 1_536
REPS = 3                     # samples per test, pooled (C9)
MAX_WALKS = 8                # walks per sample at most: 3 samples = 24 walks
MIN_TRADES_SAMPLE = 1_000    # a sample pools walks until this many trades (re-verify R1)
T_FAIL = 3.5                 # |t_pooled| at or above: fail
T_MEAN = 2.0                 # |t_pooled| at or above AND |mean| >= MEAN_FAIL: fail
MEAN_FAIL = 0.03             # R/trade
MIN_TRADES = 30
WARM = 300
DAILY_VOL = 0.01
WEEKEND_GAP = 1.0            # sd of the gap after a weekend, in daily sigmas (other bars: 0.1 bar sigma)
RELEASE_VOL = 3.0            # bars opening within an hour after a release move this many times faster
PRICE = {"XAUUSD": 1500.0, "XAGUSD": 25.0, "XTIUSD": 80.0, "US500": 1500.0, "USTEC": 3000.0, "DE40": 8000.0,
         "UK100": 6000.0, "US30": 15000.0, "USINDEX": 80.0, "BTCUSD": 20000.0, "XAUEUR": 1200.0, "XAUJPY": 150000.0,
         "USDJPY": 100.0, "AUDJPY": 80.0, "EURJPY": 120.0, "GBPJPY": 140.0}
N_BARS = {"5m": 20_000, "15m": 16_000, "30m": 12_000, "1h": 10_000, "4h": 6_000, "1d": 4_000, "1w": 1_200}
XS_BARS = 2_000

# Finer bars of a cell with stop/limit entries (quant C10: the fill bar is resolved from 1m bars): the same path cut
# into at most FINE_MAX bars per bar (1m up to 4h, 6 minutes on 1d, 30 minutes on 1w). Without them (D5, found by this
# guard) B04 lost 0.49 R/trade on zero-edge data, t -4 to -6.
FINE_MAX = 240


def _seed(*key: object) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, key)).encode()).digest()[:4], "big")


def _times(tf: str, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Bar open / close instants on weekdays (daily bars roll at 21:00 UTC: Monday's bar opens Sunday 21:00)."""
    if tf == "1w":
        t = T0 - 3 * HOUR + np.arange(n, dtype=np.int64) * 7 * DAY
        return t, t + 5 * DAY
    if tf == "1d":
        days = np.arange(n * 7 // 5 + 7, dtype=np.int64)
        days = days[days % 7 < 5][:n]                          # T0 is a Monday
        t = T0 - 3 * HOUR + days * DAY
        return t, t + DAY
    step = TF_NS[tf]
    per_day = DAY // step
    k = np.arange((n * 7 // 5 // per_day + 7) * per_day, dtype=np.int64)
    k = k[(k // per_day) % 7 < 5][:n]
    t = T0 + k * step
    return t, t + step


def rw_bars(inst: str, tf: str, seed: int, n: int, hot: np.ndarray | None = None) -> Bars:
    """A driftless random walk (the price, not only its log, is a martingale) with this timeframe's bar structure.
    `hot`: release instants; bars opening within the hour after one are RELEASE_VOL times more volatile."""
    return rw_cell(inst, tf, seed, n, hot)[0]


def rw_cell(inst: str, tf: str, seed: int, n: int, hot: np.ndarray | None = None, *, fine: bool = False
            ) -> tuple[Bars, Bars | None]:
    """`rw_bars`, and with `fine` the finer bars of the same path (each bar cut into min(its minutes, FINE_MAX))."""
    rng = np.random.default_rng(seed)
    t, ta = _times(tf, n)
    days_per_bar = 5.0 if tf == "1w" else TF_NS[tf] / DAY
    sig = np.full(n, DAILY_VOL * np.sqrt(days_per_bar))
    if hot is not None and len(hot) and tf not in ("1d", "1w"):
        k = np.searchsorted(hot, t, side="right") - 1
        sig[(k >= 0) & (t - hot[np.maximum(k, 0)] < HOUR)] *= RELEASE_VOL
    sub = max(64, min(SUB_MAX, PATH_POINTS // n))
    s = (sig / np.sqrt(sub))[:, None]
    steps = rng.normal(0.0, 1.0, (n, sub)) * s - 0.5 * s * s
    gap_sd = 0.1 * sig
    gap_sd[1:][np.diff(t) > 2 * DAY] = WEEKEND_GAP * DAILY_VOL
    steps[:, 0] += rng.normal(0.0, 1.0, n) * gap_sd - 0.5 * gap_sd ** 2
    path = PRICE.get(inst, 1.3 if len(inst) == 6 else 1500.0) * np.exp(np.cumsum(steps.ravel())).reshape(n, sub)
    b = Bars(inst, tf, t, ta, path[:, 0], path.max(axis=1), path.min(axis=1), path[:, -1], np.full(n, np.nan),
             np.zeros(n, bool), np.zeros(n, np.int32), rng.integers(100, 1000, n).astype(float))
    b.meta.update(dec_from=int(ta[min(WARM, n // 4)]), end=int(ta[-1]) + 1)
    if not fine:
        return b, None
    m = min(sub, FINE_MAX, int((ta[0] - t[0]) // MIN))
    st = np.arange(m) * sub // m
    end = np.append(st[1:], sub) - 1
    ft = (t[:, None] + (ta - t)[:, None] * st[None, :] // sub).ravel()
    nf = len(ft)
    fb = Bars(inst, "1m", ft, np.append(ft[1:], int(ta[-1])), path[:, st].ravel(),
              np.maximum.reduceat(path, st, axis=1).ravel(), np.minimum.reduceat(path, st, axis=1).ravel(),
              path[:, end].ravel(), np.full(nf, np.nan), np.zeros(nf, bool), np.zeros(nf, np.int32), np.zeros(nf))
    return b, fb


def rw_ctx(t_from: int, t_to: int, seed: int) -> Ctx:
    """Exogenous schedules: weekday 13:30 UTC releases on ~1 day in 7, FOMC 18:00 UTC on ~1 in 30, holidays on ~1 in
    40; policy rates alternating 0 / 3 % across currencies (so carry rules can fire); 40 years of driftless history."""
    rng = np.random.default_rng(seed)
    ctx = Ctx(window="explore")
    days = np.arange(t_from // DAY - 2, t_to // DAY + 2, dtype=np.int64)
    wk = days[(days + 3) % 7 < 5]                              # weekdays (day 0 = Thursday)
    rel = wk[rng.random(len(wk)) < 1 / 7] * DAY + 13 * HOUR + 30 * MIN
    fomc = wk[rng.random(len(wk)) < 1 / 30] * DAY + 18 * HOUR
    for name in D.EVENT_SETS:
        ctx.events[name] = np.sort(np.concatenate([rel, fomc]))
    ctx.events["fomc_statement"] = fomc
    hol = wk[rng.random(len(wk)) < 1 / 40]
    for ex in ("US", "DE", "UK"):
        ctx.events[f"holiday_{ex}"] = hol
    for i, ccy in enumerate(D.RATES):
        ctx.aux[f"rate_{ccy}"] = (np.array([t_from - 400 * DAY]), np.array([3.0 * (i % 2)]))
    av = np.arange(t_from - 40 * 365 * DAY, t_to, DAY, dtype=np.int64)
    for inst in D.LONG_KEYS:
        r = np.random.default_rng(_seed(seed, inst)).normal(0, DAILY_VOL, len(av))
        ctx.aux[f"long_{inst}"] = (av, 100 * np.exp(np.cumsum(r - 0.5 * DAILY_VOL ** 2)), np.zeros(len(av), bool))
    return ctx


def _span(tf: str, n: int) -> tuple[int, int]:
    t, ta = _times(tf, n)
    return int(t[0]), int(ta[-1])


@lru_cache(maxsize=4)        # finer bars are large: a pair walks each sample once, the cache only spares the twin pair
def walk_events(uid: str, walk: int) -> tuple[tuple[Bars, Events, Bars | None], ...]:
    """One driftless walk of every cell of the unit, the unit's events on it, and the cell's finer bars where the
    cell has stop/limit entries (the harness gives those cells gold 1m bars, C10)."""
    u = D.unit_by_id(uid)
    out: list[tuple[Bars, Events, Bars | None]] = []
    for tf in u["timeframes"]:
        n = XS_BARS if u.get("xs") else N_BARS[tf]
        ctx = rw_ctx(*_span(tf, n), _seed(uid, walk, "ctx", tf))
        hot = np.sort(np.concatenate([v for k, v in ctx.events.items() if not k.startswith("holiday")]))
        if u.get("xs"):
            bs = {k: rw_bars(k, tf, _seed(uid, walk, k, tf), n) for k in u["instruments"]}
            out += [(bs[k], ev, None) for k, ev in D.XS_DETECTORS[u["detector"]](bs, u["params"], ctx).items()]
        else:
            inst = "XAUUSD" if "XAUUSD" in u["instruments"] else u["instruments"][0]
            b, fine = rw_cell(inst, tf, _seed(uid, walk, inst, tf), n, hot, fine=True)
            ev = D.detect_cell(u, b, ctx)
            out.append((b, ev, fine if D.intrabar(b, ev) else None))
    return tuple(out)


def walk_trades(uid: str, control: str, walk: int) -> pd.DataFrame:
    frames = []
    for b, ev, fine in walk_events(uid, walk):
        tr = SIM.simulate(b, ev, control=control, seed_key=f"{uid}|{walk}", fine=fine)
        if len(tr):
            df = pd.DataFrame(tr, columns=SIM.TRADE_COLS)
            df["inst"], df["tf"] = b.inst, b.tf
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=[*SIM.TRADE_COLS, "inst", "tf"])


def pair_stats(uid: str, control: str, first: int = 0) -> dict[str, float]:
    """REPS samples first.. of the pair, pooled. Sample r: walks r*MAX_WALKS.. pooled until MIN_TRADES_SAMPLE trades
    (samples never share a walk); every walk keeps its own block of days."""
    frames: list[pd.DataFrame] = []
    for rep in range(first, first + REPS):
        n = 0
        for w in range(rep * MAX_WALKS, (rep + 1) * MAX_WALKS):
            tr = walk_trades(uid, control, w)
            tr["walk"] = w
            frames.append(tr)
            n += len(tr)
            if n >= MIN_TRADES_SAMPLE:
                break
    frames = [f for f in frames if len(f)]
    if not frames:
        return {"n": 0, "t_excess": 0.0, "t_gross": 0.0, "excess": 0.0, "gross": 0.0}
    tr = pd.concat(frames, ignore_index=True)
    # walks share a calendar; shift each to its own block of days so a day sums one walk's trades only
    tr["t_fill"] = tr["t_fill"].astype(np.int64) + tr["walk"].astype(np.int64) * 6_000 * DAY
    s = S.summarize(tr)
    t_gross = S.hac_t(S.daily(tr, "gross").to_numpy(), S.overlap_lag(tr))
    return {"n": int(s["n"]), "t_excess": float(s["t_excess"]), "t_gross": float(t_gross),
            "excess": float(s["excess"]), "gross": float(s["gross"])}


def guard_failures(st: dict[str, float]) -> dict[str, tuple[float, float]]:
    """The statistics of one pooled test that fail C9: {name: (t, mean)}; a NaN t (no variance) fails."""
    bad = {}
    for t_key, m_key in (("t_excess", "excess"), ("t_gross", "gross")):
        t, m = st[t_key], st[m_key]
        if not np.isfinite(t) or abs(t) >= T_FAIL or (abs(m) >= MEAN_FAIL and abs(t) >= T_MEAN):
            bad[t_key] = (t, m)
    return bad


def confirmed_failures(bad: dict[str, tuple[float, float]], retest: dict[str, tuple[float, float]]
                       ) -> dict[str, tuple[tuple[float, float], tuple[float, float]]]:
    """The first test's failures that the retest repeats with the same sign of t."""
    return {k: (bad[k], retest[k]) for k in bad if k in retest and np.sign(retest[k][0]) == np.sign(bad[k][0])}


def rw_sparse() -> set[str]:
    return set(D.rw_guard()["sparse"])


def _params() -> list:
    failed = set(D.rw_guard()["failed"])
    return [pytest.param(uid, c, marks=pytest.mark.xfail(strict=True, reason="listed C9 guard failure (rw_guard.json)"))
            if f"{uid}|{c}" in failed else (uid, c) for uid, c in pairs()]


def pairs() -> list[tuple[str, str]]:
    out = []
    for u in D.units():
        ctrls = [u["control"]] if u.get("control") else ["random_side", "random_time"]
        out += [(u["unit"], c) for c in ctrls]
    return out


@pytest.mark.parametrize("uid,control", _params())
def test_detector_and_control_show_no_edge_on_a_driftless_random_walk(uid: str, control: str) -> None:
    first = pair_stats(uid, control, 0)
    if first["n"] < MIN_TRADES:
        assert uid in rw_sparse(), f"{uid}: {first['n']} trades on {REPS * MAX_WALKS} walks: list it in rw_guard.json"
        pytest.skip(f"{uid}: {first['n']} trades on {REPS * MAX_WALKS} walks: excluded from EVAL-1 screening (listed)")
    assert uid not in rw_sparse(), f"{uid} has {first['n']} trades on the guard: remove it from rw_guard.json"
    bad = guard_failures(first)
    confirmed = {}
    if bad:
        retest = guard_failures(pair_stats(uid, control, REPS))
        confirmed = confirmed_failures(bad, retest)
    assert not confirmed, f"{uid} x {control}: pooled |t| >= {T_FAIL} or |mean| >= {MEAN_FAIL} R with |t| >= {T_MEAN}, " \
                    f"on the first 3 samples and, same sign, on the retest ((t, mean), (t, mean)): {confirmed} (n {first['n']})"


@pytest.mark.parametrize("st,fails", [
    ({"t_excess": 3.6, "excess": 0.001, "t_gross": 0.0, "gross": 0.0}, {"t_excess"}),
    ({"t_excess": 0.0, "excess": 0.0, "t_gross": -2.1, "gross": -0.031}, {"t_gross"}),
    ({"t_excess": 2.5, "excess": 0.02, "t_gross": 3.4, "gross": 0.029}, set()),
    ({"t_excess": 1.9, "excess": 0.5, "t_gross": float("nan"), "gross": 0.0}, {"t_gross"}),
])
def test_a_pair_fails_on_a_pooled_t_of_3_5_or_a_mean_of_0_03_r_with_t_2(st: dict[str, float], fails: set[str]) -> None:
    assert set(guard_failures(st)) == fails


@pytest.mark.parametrize("uid", ["S21-V1", "S20-V1", "S02-V0", "F08-V0", "NULL-RAND-1h", "NULL-XS-RANDOM"])
def test_the_random_walk_pairs_are_not_vacuous(uid: str) -> None:
    """Frequent units must trade on one walk, so a harness that stops trading cannot pass the guard by skipping."""
    assert len(walk_trades(uid, "random_side", 0)) >= MIN_TRADES


# ------------------------------------------------------------------ one behaviour test per audit defect
def flat_bars(n: int = 80, *, tf: str = "1d", o: float = 100.0, h: float = 100.5, lo: float = 99.0,
              c: float = 100.0) -> Bars:
    t, ta = _times(tf, n)
    b = Bars("XAUUSD", tf, t, ta, np.full(n, o), np.full(n, h), np.full(n, lo), np.full(n, c), np.full(n, np.nan),
             np.zeros(n, bool), np.zeros(n, np.int32), np.zeros(n))
    b.meta.update(dec_from=int(ta[0]), end=int(ta[-1]) + 1)
    return b


def test_a_stop_inside_the_risk_floor_is_not_traded_but_counted() -> None:
    """D1: stop distance >= max(0.25 x ATR20, 3 x round trip), else the event is not a trade. Flat bars: ATR20 1.5,
    gold round trip $0.166, floor max(0.375, 0.498) = 0.498."""
    b = flat_bars()
    eb = EB()
    eb.add(40, 1, stop_d=0.45, hold=1)          # inside the floor
    eb.add(50, 1, stop_d=0.55, hold=1)          # outside it
    counts: dict[str, int] = {}
    tr = SIM.simulate(b, eb.build(), control="random_side", seed_key="x", counts=counts)
    assert tr[:, SIM.TRADE_COLS.index("i")].tolist() == [50]
    assert counts == {"risk_floor": 1}
    assert SIM.risk_floor("XAUUSD", 100.0, np.nan, 1.5) == pytest.approx(3 * 0.166)
    assert SIM.risk_floor("XAUUSD", 100.0, np.nan, 4.0) == pytest.approx(1.0)


def test_an_event_before_atr20_exists_is_not_traded() -> None:
    b = flat_bars()
    eb = EB()
    eb.add(5, 1, stop_d=5.0, hold=1)
    counts: dict[str, int] = {}
    assert len(SIM.simulate(b, eb.build(), control="random_side", seed_key="x", counts=counts)) == 0
    assert counts == {"risk_floor": 1}


def test_an_oco_pair_with_one_leg_inside_the_floor_is_not_placed() -> None:
    b = flat_bars()
    b.h[41] = 101.0                             # only the long leg (px 100.8) fills
    eb = EB()
    eb.oco_pair(40, long=dict(kind=1, px=100.8, valid=1, stop=99.8, hold=1),
                short=dict(kind=1, px=98.5, valid=1, stop=98.6, hold=1))   # short risk 0.1 < floor
    assert len(SIM.simulate(b, eb.build(), control="random_side", seed_key="x")) == 0


def test_oco_random_side_twin_is_not_forced_to_a_full_loss_when_both_legs_fill() -> None:
    """D2: both legs on one bar -> the real trade is a full loss (card rule); the twin takes the opposite of each leg
    the bar touched: short from the long's entry and long from the short's entry, each walked to the close."""
    b = flat_bars()
    up, dn, risk = 101.0, 99.0, 2.0
    b.o[41], b.h[41], b.l[41], b.c[41] = 100.0, 102.5, 97.5, 100.4     # neither twin reaches its stop (+-2)
    eb = EB()
    eb.oco_pair(40, long=dict(kind=1, px=up, valid=1, stop=up - risk, hold=1),
                short=dict(kind=1, px=dn, valid=1, stop=dn + risk, hold=1))
    tr = SIM.simulate(b, eb.build(), control="random_side", seed_key="x")
    assert len(tr) == 1
    gross = tr[0, SIM.TRADE_COLS.index("gross")]
    assert gross == pytest.approx(-1.0)
    opp = (up - 100.4) / risk + (100.4 - dn) / risk                     # = (up - dn) / risk = 1
    assert tr[0, SIM.TRADE_COLS.index("ctrl_gross")] == pytest.approx(0.5 * (gross + opp))
    assert tr[0, SIM.TRADE_COLS.index("excess")] == pytest.approx(0.5 * (gross - opp))


def test_random_time_twin_of_a_short_puts_its_target_below_entry() -> None:
    """D3: identical bars, so a short that hits its target on the fill bar has a twin that does the same: excess 0.
    The frozen harness put the twin's target at entry + 0.8 and booked -0.8 R."""
    b = flat_bars()
    eb = EB()
    eb.add(40, -1, stop_d=1.0, tgt_d=0.8, hold=3)
    tr = SIM.simulate(b, eb.build(), control="random_time", seed_key="x")
    assert tr[0, SIM.TRADE_COLS.index("gross")] == pytest.approx(0.8)
    assert tr[0, SIM.TRADE_COLS.index("ctrl_gross")] == pytest.approx(0.8)
    assert tr[0, SIM.TRADE_COLS.index("excess")] == pytest.approx(0.0)


@pytest.mark.parametrize("tf", ["5m", "30m", "4h"])
def test_intraday_random_time_twin_starts_after_the_fill_at_the_same_time_of_day(tf: str) -> None:
    """D4: never from the bars that made the signal; same session (time of day) on a later day."""
    b = rw_bars("XAUUSD", tf, 1, 4_000)
    for f in range(300, 3_000, 37):
        g = SIM.twin_candidates(b, f, hold_bars=3)
        assert len(g) and (g > f).all()
        assert (b.t[g] % DAY == b.t[f] % DAY).all()
        assert (b.t[g] - b.t[f] <= SIM.TWIN_DAYS * DAY).all()
        assert SIM._pick_bar(b, f, "k", f - 1, 3) in set(g.tolist())


@pytest.mark.parametrize("tf,span", [("1d", 20), ("1w", 8)])
def test_daily_and_weekly_random_time_twin_starts_after_the_fill(tf: str, span: int) -> None:
    b = rw_bars("XAUUSD", tf, 1, 600)
    g = SIM.twin_candidates(b, 300, hold_bars=2)
    assert g.tolist() == list(range(301, 301 + span))
    assert SIM.twin_candidates(b, 598, hold_bars=2).tolist() == []    # the hold does not fit: no twin, no trade


@pytest.mark.parametrize("tf", ["5m", "1h", "1d", "1w"])
def test_the_guard_finer_bars_are_the_same_path_as_their_bar(tf: str) -> None:
    b, fine = rw_cell("XAUUSD", tf, 3, 400, fine=True)
    assert fine is not None and (np.diff(fine.t) > 0).all()
    a = np.searchsorted(fine.t, b.t)
    z = np.searchsorted(fine.t, b.ta)
    assert (z > a).all()
    assert np.allclose(fine.o[a], b.o) and np.allclose(fine.c[z - 1], b.c)
    assert np.allclose(np.maximum.reduceat(fine.h, a), b.h) and np.allclose(np.minimum.reduceat(fine.l, a), b.l)


def test_a_retest_that_fails_with_the_opposite_sign_does_not_fail_the_pair() -> None:
    first = {"t_excess": (2.4, 0.2), "t_gross": (2.1, 0.3)}
    assert confirmed_failures(first, {"t_excess": (-2.7, -0.3), "t_gross": (2.2, 0.25)}) == {
        "t_gross": ((2.1, 0.3), (2.2, 0.25))}
    assert confirmed_failures(first, {}) == {}


def test_each_sample_pools_walks_until_1000_trades() -> None:
    assert MIN_TRADES_SAMPLE == 1_000 and MAX_WALKS == 8 and MIN_TRADES == 30


# ------------------------------------------------------------------ EVAL-1 precondition (ii), C1: the drift guard
E9_SEEDS = tuple(range(20))          # fixed before the first run (quant G2)
E9_DRIFT = 0.0005                    # log drift per daily bar
E9_BARS = 2_400                      # weekday 1d bars from 2010-01-04: decisions from bar WARM (2011-02) to 2019-03
E9_YEARS = tuple(range(2011, 2019))  # the 8 full years the decisions cover
E9_SHARE_MAX = 0.05


def drift_bars(inst: str, seed: int) -> Bars:
    """The guard's 1d random walk (`rw_cell`) with a log drift of E9_DRIFT per bar added (prices of bar k scaled by
    exp(E9_DRIFT x k): the path inside a bar is unchanged, the drift arrives at the bar boundary)."""
    b = rw_bars(inst, "1d", seed, E9_BARS)
    f = np.exp(E9_DRIFT * np.arange(E9_BARS))
    return Bars(b.inst, b.tf, b.t, b.ta, b.o * f, b.h * f, b.l * f, b.c * f, b.spr, b.co, b.seg, b.vol, dict(b.meta))


def long_only_null_units() -> list[str]:
    """Every NULL-family 1d unit whose events are all side +1 (on the first walk), cross-sectional ones included."""
    out = []
    for u in D.units():
        if u["family"] != "NULL" or u["timeframes"] != ["1d"]:
            continue
        sides = np.concatenate([ev.side for _, ev in e9_walk_events(u["unit"], E9_SEEDS[0])[1]] or [np.zeros(0)])
        if len(sides) and (sides == 1).all():
            out.append(u["unit"])
    return out


def e9_walk_events(uid: str, seed: int) -> tuple[dict[str, Bars], list[tuple[Bars, Events]]]:
    u = D.unit_by_id(uid)
    bs = {k: drift_bars(k, _seed(uid, seed, "e9", k)) for k in u["instruments"]}
    ctx = rw_ctx(*_span("1d", E9_BARS), _seed(uid, seed, "e9ctx"))
    if u.get("xs"):
        return bs, [(bs[k], ev) for k, ev in D.XS_DETECTORS[u["detector"]](bs, u["params"], ctx).items()]
    return bs, [(b, D.detect_cell(u, b, ctx)) for b in bs.values()]


def e9_gates(uid: str, seed: int) -> dict[str, bool]:
    bs, evs = e9_walk_events(uid, seed)
    frames = []
    for b, ev in evs:
        tr = SIM.simulate(b, ev, control="random_time", seed_key=f"{uid}|e9|{seed}")
        if len(tr):
            df = pd.DataFrame(tr, columns=SIM.TRADE_COLS)
            df["inst"], df["tf"] = b.inst, b.tf
            frames.append(df)
    if not frames:
        return {"E1": False, "E9": False}
    first = next(iter(bs.values()))
    m = S.market_from_bars(bs, int(first.meta["dec_from"]), int(first.meta["end"]))
    s = S.summarize(pd.concat(frames, ignore_index=True), m)
    return S.explore_gates(s, ["1d"], None, years=E9_YEARS)


def test_long_only_controls_and_the_sentinel_fail_e9_on_a_drifting_walk() -> None:
    """EVAL-1 precondition (ii) (C1): on 20 seeded 1d walks with drift 0.0005/day, the buy-every-close sentinel passes
    E9 on none, and pooled over every long-only NULL unit x walk the share with E1 and E9 both passing is <= 5 %."""
    units = long_only_null_units()
    sentinels = [u for u in units if "LONGCLOSE" in u]
    assert sentinels, "no buy-every-close sentinel found among the long-only NULL units"
    res = {(u, sd): e9_gates(u, sd) for u in units for sd in E9_SEEDS}
    sentinel_e9 = [(u, sd) for (u, sd), g in res.items() if u in sentinels and g["E9"]]
    both = sum(1 for g in res.values() if g["E1"] and g["E9"])
    print(f"long-only NULL units {units}; walks {len(E9_SEEDS)}; sentinel E9 passes {sentinel_e9}; "
          f"E1 passes {sum(g['E1'] for g in res.values())}/{len(res)}; E9 passes {sum(g['E9'] for g in res.values())}; E1&E9 {both}/{len(res)}")
    assert not sentinel_e9
    assert both / len(res) <= E9_SHARE_MAX
