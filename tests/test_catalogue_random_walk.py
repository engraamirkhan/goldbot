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
           by long share, so either can run); cells pooled as in dispatcher.unit_trades; walks are pooled until the
           pair has MIN_TRADES trades (at most MAX_WALKS), else the pair is skipped as uninformative
  stats    the harness's own: HAC t of the daily-summed excess ("t_excess", the gated number) and the same HAC t of
           the daily-summed gross R (= net + cost drag)

Multiplicity. ~300 pairs x 2 statistics: a literal "|t| < 2 on one walk" fails a CORRECT harness with probability
1 - 0.954^600. A pair fails only when |t| >= 2 with the same sign on each of REPS independent samples (false-fail rate
per statistic 2 x 0.023^3 ~ 2e-5; a bias of t >= 4 per sample still fails with probability > 0.9). The D1-D4 defects
gave t of 4-40 here."""
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
REPS = 3
MAX_WALKS = 6
T_MAX = 2.0
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

# Pairs that still fail with D1-D4 fixed, for a reason outside this change. strict: the marker must go when fixed.
# D5 (found by this test, 2026-11-07): a stop/limit entry filled INSIDE a bar has its stop checked against that bar's
# whole range, including the part before the fill ("conservative", sim.walk). B04's stop sits 0.6 ATR below a buy
# stop whose fill bar usually opened below it, so 60 % of its zero-edge trades are stopped by the pre-fill low:
# gross -0.49 R/trade, t -4 to -6. Conservative for a pass, but it fails EVAL-1 (ii) and would trip the EVAL-1 twin
# kill (excess t <= -2) on a zero-edge unit. Fix needs a fill-bar rule from finer bars; escalated to quant-reviewer.
# B04-V2 (gold $100 grid, fewer trades) carries the same bias but does not replicate at |t| >= 2 on these seeds, so
# it is not listed (the seeds are fixed: the list is deterministic).
KNOWN_D5 = {"B04-V0", "B04-V3"}


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
    return b


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


@lru_cache(maxsize=None)
def walk_events(uid: str, walk: int) -> tuple[tuple[Bars, Events], ...]:
    """One driftless walk of every cell of the unit, and the unit's events on it."""
    u = D.unit_by_id(uid)
    out: list[tuple[Bars, Events]] = []
    for tf in u["timeframes"]:
        n = XS_BARS if u.get("xs") else N_BARS[tf]
        ctx = rw_ctx(*_span(tf, n), _seed(uid, walk, "ctx", tf))
        hot = np.sort(np.concatenate([v for k, v in ctx.events.items() if not k.startswith("holiday")]))
        if u.get("xs"):
            bs = {k: rw_bars(k, tf, _seed(uid, walk, k, tf), n) for k in u["instruments"]}
            out += [(bs[k], ev) for k, ev in D.XS_DETECTORS[u["detector"]](bs, u["params"], ctx).items()]
        else:
            inst = "XAUUSD" if "XAUUSD" in u["instruments"] else u["instruments"][0]
            b = rw_bars(inst, tf, _seed(uid, walk, inst, tf), n, hot)
            out.append((b, D.detect_cell(u, b, ctx)))
    return tuple(out)


def walk_trades(uid: str, control: str, walk: int) -> pd.DataFrame:
    frames = []
    for b, ev in walk_events(uid, walk):
        tr = SIM.simulate(b, ev, control=control, seed_key=f"{uid}|{walk}")
        if len(tr):
            df = pd.DataFrame(tr, columns=SIM.TRADE_COLS)
            df["inst"], df["tf"] = b.inst, b.tf
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=[*SIM.TRADE_COLS, "inst", "tf"])


def pair_stats(uid: str, control: str, rep: int) -> dict[str, float]:
    """Sample `rep` of the pair: walks rep*MAX_WALKS.. pooled until MIN_TRADES trades (samples never share a walk)."""
    frames: list[pd.DataFrame] = []
    n = 0
    for w in range(rep * MAX_WALKS, (rep + 1) * MAX_WALKS):
        tr = walk_trades(uid, control, w)
        tr["day_offset"] = w - rep * MAX_WALKS
        frames.append(tr)
        n += len(tr)
        if n >= MIN_TRADES:
            break
    if n == 0:
        return {"n": 0, "t_excess": 0.0, "t_gross": 0.0, "excess": 0.0, "gross": 0.0}
    tr = pd.concat([f for f in frames if len(f)], ignore_index=True)
    # walks share a calendar; shift each to its own block of days so a day sums one walk's trades only
    tr["t_fill"] = tr["t_fill"].astype(np.int64) + tr["day_offset"].astype(np.int64) * 6_000 * DAY
    s = S.summarize(tr)
    t_gross = S.hac_t(S.daily(tr, "gross").to_numpy(), S.overlap_lag(tr))
    return {"n": int(s["n"]), "t_excess": float(s["t_excess"]), "t_gross": float(t_gross),
            "excess": float(s["excess"]), "gross": float(s["gross"])}


def pairs() -> list[tuple[str, str]]:
    out = []
    for u in D.units():
        ctrls = [u["control"]] if u.get("control") else ["random_side", "random_time"]
        out += [(u["unit"], c) for c in ctrls]
    return out


def _params() -> list:
    return [pytest.param(uid, c, marks=pytest.mark.xfail(strict=True, reason="D5 fill-bar stop, see KNOWN_D5"))
            if uid in KNOWN_D5 else (uid, c) for uid, c in pairs()]


@pytest.mark.parametrize("uid,control", _params())
def test_detector_and_control_show_no_edge_on_a_driftless_random_walk(uid: str, control: str) -> None:
    first = pair_stats(uid, control, 0)
    if first["n"] < MIN_TRADES:
        pytest.skip(f"{uid}: {first['n']} trades on {MAX_WALKS} walks, too rare for an informative t")
    bad: dict[str, list[float]] = {}
    for key in ("t_excess", "t_gross"):
        ts = [first[key]]
        while len(ts) < REPS and abs(ts[-1]) >= T_MAX and np.sign(ts[-1]) == np.sign(ts[0]):
            ts.append(pair_stats(uid, control, len(ts))[key])
        if len(ts) == REPS and abs(ts[-1]) >= T_MAX and np.sign(ts[-1]) == np.sign(ts[0]):
            bad[key] = ts
    assert not bad, f"{uid} x {control}: |t| >= {T_MAX}, same sign, on all {REPS} driftless samples: {bad}"


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
