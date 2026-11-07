"""Unit statistics and the pre-registered gates: EVAL-1 (docs/research/evaluation-audit-2026-11-07.md section (b),
quant sign-off C1-C6; reading rule docs/research/prereg-2026-11-07-eval1-reading-rule.md).

The unit statistic (EVAL-1 section 2): "A unit is evaluated as a book: daily net return on equity at the
pre-registered sizing ..., all costs included, benchmark = cash (zero on a margin account). Each unit has exactly one
sizing rule in the card" (C4). The catalogue's one sizing rule is fixed fractional risk per trade (RISK_PER_TRADE of
equity per 1 R), so the book's daily return is RISK_PER_TRADE x the day's net R; every t, sign and share below is
scale-free and is computed on the daily net R, marked to market on daily closes (`book_daily`).

Explore pass, ALL of (EVAL-1 section 3):
  E1  Newey-West t of the daily net book return >= 2, lag L = max(5, ceil(1.5 x median holding days)) (C3)
  E2  mean net R per trade > 0 at 1.0x cost
  E3  mean gross >= 2 x mean cost
  E4  >= 5 of the 8 explore years (2010-2017) with a net positive book
  E6  n >= 300 when every cell is 1d/1w, else >= 1,000 (unchanged)
  E7  learn_pre (2003-05..2005) mean net of the same sign, where that window has >= 60 trades
  E8  net > 0 after removing the top 1 % of trades; no single trade > 10 % of total net P&L; the book net > 0 after
      removing the top 1 % of days (C6)
  E9  drift guard (C1): against a financed buy-and-hold book of the same instruments and side (equal risk, same cost
      model, always in the market), Newey-West alpha t >= 1.0, alpha >= 50 % of the unit's mean daily net return, and
      the unit beats that book scaled to its own average gross exposure on net P&L in >= 5 of 8 years
  twin_kill  the matched random twin is a kill, not a pass gate (section 4): excess t <= -2 drops the unit
Reported only: drift_adj (E5 retired to a diagnostic), the twin excess and its t, DSR at explore.
CONFIRM (explore passers only, read once; section 5): BH-FDR q = 0.10 over the explore passers on the one-sided
  block-bootstrap p of the daily NET book return; >= 5 of 7 years net positive; net > 0 at 1.5x cost stress; max
  drawdown at the registered sizing <= the account's size-down stage (8 %).
TEST-OLD (CONFIRM passers only): n >= 60, net > 0, Newey-West t of the net book >= 1.28, and a unit with E9 beta > 0.3
  must be net >= 0.
Promotion to the live-candidate list (section 6, C5): DSR >= 0.95 at registry K_eff on the CONFIRM net series.
Void (quant C1 of the WS-3 card, unchanged): k or more passes among the random nulls that can reach their n floor, or
any buy-every-close sentinel passing.

Stricter readings where EVAL-1 leaves a choice (listed in the reading-rule doc, section 'Readings'): the gated
Newey-West t is the smaller of the Bartlett windows L and 2L; holding days are calendar days; the top 1 % is ceil(1 %)
and the day count is every trading day of the book; E9 is tested against the book of EVERY side the unit trades and
must pass against each; a year with no data is not a year beaten; a missing twin t (NaN) fails the kill; drawdown at
the registered sizing is linear in R (not compounded)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from goldbot.research.catalogue.costs import CostModel, default_model
from goldbot.research.catalogue.data import DAY
from goldbot.research.catalogue.sim import STRESS

EXPLORE_YEARS = tuple(range(2010, 2018))
CONFIRM_YEARS = tuple(range(2018, 2025))
T_MIN = 2.0                      # E1: Newey-West t of the daily net book >= 2
NW_LAG_MIN = 5                   # C3: L = max(5, ceil(1.5 x median holding days))
NW_LAG_HOLD = 1.5
GROSS_COST_MIN = 2.0
YEARS_MIN = 5
N_MIN_DAILY = 300
N_MIN_INTRADAY = 1000
LEARN_PRE_MIN = 60
BH_Q = 0.10
CONFIRM_YEARS_MIN = 5
TEST_OLD_N = 60
TEST_OLD_T = 1.28
TEST_OLD_BETA = 0.3              # E9 at TEST-OLD: a unit with beta > 0.3 must be net >= 0
TRIM_FRAC = 0.01                 # E8: the top 1 % of trades / of days removed
TOP_TRADE_SHARE = 0.10           # E8: no single trade > 10 % of total net P&L
E9_ALPHA_T_MIN = 1.0             # C1: Newey-West alpha t over the financed buy-and-hold book
E9_ALPHA_SHARE_MIN = 0.5         # C1: alpha >= 50 % of the unit's mean daily net return
E9_YEARS_MIN = 5                 # C1: beats the exposure-scaled book in >= 5 of 8 years
TWIN_KILL_T = -2.0               # section 4: excess over the twin at t <= -2 drops the unit
RISK_PER_TRADE = 0.005           # C4: the catalogue card's one sizing rule, fixed fractional 0.5 % per 1 R
CONFIRM_DD_MAX = 0.08            # section 5: the account's size-down stage (8 % today)
DSR_MIN = 0.95                   # section 6 (C5): promotion, on the CONFIRM net series at registry K_eff
BOOT_RESAMPLES = 20_000
BOOT_BLOCK = 5
NULL_RATE = 0.05


def _t(x: np.ndarray) -> float:
    x = np.asarray(x, float)
    if len(x) < 2:
        return float("nan")
    s = float(np.std(x, ddof=1))
    return float(np.mean(x) / (s / math.sqrt(len(x)))) if s > 0 else float("nan")


def overlap_lag(tr: pd.DataFrame) -> int:
    """L (quant C2): fill days inside one typical hold = max(1, ceil(median nights held x trading days / calendar
    span in days)). 1 for intraday or overnight holds."""
    if not len(tr):
        return 1
    days = np.unique(tr["t_fill"].to_numpy(np.int64) // DAY)
    span = max(1.0, float(days.max() - days.min() + 1))
    return max(1, int(math.ceil(float(tr["nights"].median()) * len(days) / span)))


def hac_t(x: np.ndarray, L: int) -> float:
    """Newey-West t of mean(x) > 0 for daily sums that overlap through holds of about L fill days (quant C2).
    L = 1: the plain t. L > 1: Bartlett kernel with bandwidth 2L. Deviation from 'lag L', recorded in prereg section 14:
    a Bartlett window of L understates an MA(L) long-run variance by about 30 % (t too large by ~20 %); 2L brings it
    within ~10 % (test_hac_t_on_overlapping_holds)."""
    x = np.asarray(x, float)
    n = len(x)
    if L <= 1 or n < 3:
        return _t(x)
    lag = min(2 * L, n - 1)
    d = x - x.mean()
    lrv = float(d @ d) / n
    for k in range(1, lag + 1):
        lrv += 2 * (1 - k / (lag + 1)) * float(d[k:] @ d[:-k]) / n
    if lrv <= 0:
        return float("nan")
    return float(x.mean() / math.sqrt(lrv / n))


def daily(tr: pd.DataFrame, col: str = "excess") -> pd.Series:
    """Per-UTC-fill-day sums of `col`. Rows where `col` is NaN are left out (a trade with no random-time twin has a NaN
    excess: "leave it out of the excess statistic only", quant item 4), so such a day is not a zero."""
    if not len(tr):
        return pd.Series(dtype=float)
    x = tr[col].to_numpy(float)
    ok = ~np.isnan(x)
    day = (tr["t_fill"].to_numpy(np.int64) // DAY)[ok]
    return pd.Series(x[ok]).groupby(day).sum()


def year_of_day(days: np.ndarray) -> np.ndarray:
    return np.asarray(pd.to_datetime(np.asarray(days, np.int64), unit="D").year)


# ------------------------------------------------------------------ EVAL-1 book (section 2)
@dataclass(frozen=True)
class Market:
    """Daily closes of a window: the base of the mark-to-market book and of the E9 buy-and-hold book.
    `closes[inst]`: the 1d close indexed by the UTC day (epoch days) its bar closes on; [day_from, day_to) is the
    window's decision span in UTC days; `costs` the window's cost model (None: costs.default_model(), synthetic)."""
    closes: dict[str, pd.Series]
    day_from: int
    day_to: int
    costs: CostModel | None = None


def market_from_bars(bars: dict[str, Any], dec_from_ns: int, end_ns: int, costs: CostModel | None = None) -> Market:
    """A `Market` from 1d `Bars` per instrument (close day = the UTC day of the bar's close, `(ta - 1) // DAY`)."""
    closes = {i: pd.Series(np.asarray(b.c, float), index=(np.asarray(b.ta, np.int64) - 1) // DAY)
              for i, b in bars.items() if len(b)}
    return Market(closes=closes, day_from=int(dec_from_ns // DAY), day_to=int(-(-end_ns // DAY)), costs=costs)


def _asof(c: pd.Series, days: np.ndarray) -> np.ndarray:
    """The last close at or before each day (NaN before the first close)."""
    k = np.searchsorted(c.index.to_numpy(np.int64), days, "right") - 1
    return np.where(k >= 0, c.to_numpy(float)[np.clip(k, 0, None)], np.nan)


def _grid(m: Market, extra: np.ndarray | None = None) -> np.ndarray:
    """The book's days: every UTC day of the window with a daily close of any instrument, plus any day with P&L."""
    parts = [c.index.to_numpy(np.int64) for c in m.closes.values()]
    days = np.unique(np.concatenate(parts)) if parts else np.zeros(0, np.int64)
    days = days[(days >= m.day_from) & (days < m.day_to)]
    if extra is not None and len(extra):
        days = np.union1d(days, np.asarray(extra, np.int64))
    return days


def _hold_days(tr: pd.DataFrame) -> np.ndarray:
    return (tr["t_exit"].to_numpy(np.int64) - tr["t_fill"].to_numpy(np.int64)) / DAY


def eval1_lag(tr: pd.DataFrame) -> int:
    """C3: "lag L = max(5, ceil(1.5 x median holding days)), fixed in the card". Holding days are calendar days
    (t_exit - t_fill) / DAY: the larger count, so the stricter t."""
    if not len(tr):
        return NW_LAG_MIN
    return max(NW_LAG_MIN, int(math.ceil(NW_LAG_HOLD * float(np.median(_hold_days(tr))) - 1e-9)))


def nw_t(x: np.ndarray, lag: int) -> float:
    """Newey-West t of mean(x) > 0 with the Bartlett kernel at exactly `lag` (weights 1 - k / (lag + 1))."""
    x = np.asarray(x, float)
    n = len(x)
    if n < 3:
        return float("nan")
    lag = max(0, min(int(lag), n - 1))
    d = x - x.mean()
    lrv = float(d @ d) / n
    for k in range(1, lag + 1):
        lrv += 2 * (1 - k / (lag + 1)) * float(d[k:] @ d[:-k]) / n
    return float(x.mean() / math.sqrt(lrv / n)) if lrv > 0 else float("nan")


def book_t(x: np.ndarray, lag: int) -> float:
    """The gated E1 t: the smaller of the Newey-West t at lag L (EVAL-1 C3, as written) and at 2L (the WS-3 card's
    section 14 finding: a Bartlett window of L understates an MA(L) long-run variance by ~30 %). Stricter reading."""
    a, b = nw_t(x, lag), nw_t(x, 2 * lag)
    return float(np.nanmin([a, b])) if np.isfinite(a) or np.isfinite(b) else float("nan")


def _nw_ols(y: np.ndarray, x: np.ndarray, lag: int) -> tuple[float, float, float]:
    """OLS y = a + b x with Newey-West (Bartlett, `lag`) standard errors: (a, b, se(a))."""
    X = np.column_stack([np.ones(len(x)), x])
    xtx_inv = np.linalg.pinv(X.T @ X)
    coef = xtx_inv @ X.T @ y
    g = X * (y - X @ coef)[:, None]
    s = g.T @ g
    for k in range(1, min(int(lag), len(y) - 1) + 1):
        gk = g[k:].T @ g[:-k]
        s += (1 - k / (lag + 1)) * (gk + gk.T)
    v = xtx_inv @ s @ xtx_inv
    return float(coef[0]), float(coef[1]), float(math.sqrt(max(v[0, 0], 0.0)))


def book_daily(tr: pd.DataFrame, m: Market, grid: np.ndarray | None = None) -> pd.Series:
    """The unit's daily net book return in R (x RISK_PER_TRADE = return on equity), marked to market on daily closes:
    a trade held over several UTC days books side x (close_d - previous mark) / risk on each day before its exit day;
    the exit day books the rest of its gross (so the days sum to the trade's gross exactly, including a fill through
    its stop booked at -1 R) and its whole cost. A side-0 trade (both OCO legs on one bar, quant C8) books everything on
    its exit day. Days with no position are 0 (benchmark cash, zero on a margin account)."""
    net_days: dict[int, float] = {}
    if len(tr):
        d0 = tr["t_fill"].to_numpy(np.int64) // DAY
        d1 = np.maximum(d0, (tr["t_exit"].to_numpy(np.int64) - 1) // DAY)
        gross, cost = tr["gross"].to_numpy(float), tr["cost"].to_numpy(float)
        side, fill, risk = tr["side"].to_numpy(float), tr["fill"].to_numpy(float), tr["risk"].to_numpy(float)
        inst = tr["inst"].astype(str).to_numpy()
        one = d1 == d0
        g1 = pd.Series(gross[one] - cost[one]).groupby(d0[one]).sum()
        for d, v in zip(g1.index.to_numpy(np.int64), g1.to_numpy(float)):
            net_days[int(d)] = net_days.get(int(d), 0.0) + float(v)
        for k in np.flatnonzero(~one):
            c = m.closes.get(inst[k])
            booked = 0.0
            if c is not None and side[k] != 0:
                cd = c.index.to_numpy(np.int64)
                sel = (cd >= d0[k]) & (cd < d1[k])
                prev = fill[k]
                for d, px in zip(cd[sel], c.to_numpy(float)[sel]):
                    if not np.isfinite(px):
                        continue
                    inc = side[k] * (px - prev) / risk[k]
                    net_days[int(d)] = net_days.get(int(d), 0.0) + inc
                    booked += inc
                    prev = px
            net_days[int(d1[k])] = net_days.get(int(d1[k]), 0.0) + gross[k] - booked - cost[k]
    days = np.fromiter(net_days.keys(), np.int64, len(net_days))
    g = _grid(m, days) if grid is None else grid
    out = pd.Series(0.0, index=g)
    if len(days):
        s = pd.Series(np.fromiter(net_days.values(), float, len(net_days)), index=days)
        out = out + s.reindex(g).fillna(0.0)
    return out


def _risk_frac(tr: pd.DataFrame) -> dict[str, float]:
    """Per instrument: the unit's median order risk as a fraction of price (the 1 R of an equal-risk leg)."""
    f = tr["risk"].to_numpy(float) / tr["fill"].to_numpy(float)
    return {str(i): float(np.median(f[(tr["inst"] == i).to_numpy()])) for i in tr["inst"].unique()}


def buy_hold_daily(m: Market, insts: list[str], side: int, rfrac: dict[str, float], grid: np.ndarray) -> pd.Series:
    """C1's "financed buy-and-hold book of the same instruments and side (equal risk, same cost model, always in the
    market)", in R per day on `grid`: each instrument held on `side` from its first close in the window at 1 R =
    rfrac x price (the unit's own median risk), books side x return / rfrac per day, pays the cost model's round trip
    once and its C2 financing (`CostModel.swap_nights` x `debit_pct` / 365) between consecutive book days (a book day
    is marked after its 21:00 UTC rollover)."""
    cm = m.costs or default_model()
    out = np.zeros(len(grid))
    if len(grid) < 2:
        return pd.Series(out, index=grid)
    tg = np.asarray(grid, np.int64) * DAY + 22 * 3_600_000_000_000
    for inst in insts:
        c = m.closes.get(inst)
        r = rfrac.get(inst, float("nan"))
        if c is None or not np.isfinite(r) or r <= 0:
            continue
        px = _asof(c, grid)
        ok = np.isfinite(px)
        if not ok.any():
            continue
        k0 = int(np.argmax(ok))
        out[k0] -= cm.round_trip(inst, float(px[k0]), float("nan")) / (r * float(px[k0]))
        both = ok[1:] & ok[:-1]
        ret = np.where(both, px[1:] / np.where(both, px[:-1], 1.0) - 1.0, 0.0)
        fin = np.array([cm.swap_nights(inst, int(a), int(b)) * cm.debit_pct(inst, side, int(a)) / 100.0 / 365.0
                        if ok_ else 0.0 for a, b, ok_ in zip(tg[:-1], tg[1:], both)])
        out[1:] += (side * ret - fin) / r
    return pd.Series(out, index=grid)


def gross_exposure(tr: pd.DataFrame, rfrac: dict[str, float], grid: np.ndarray, n_inst: int) -> float:
    """The unit's average gross exposure as a fraction of the buy-and-hold book: sum over trades of (position relative
    to the book's leg, rfrac x fill / risk) x calendar days held, over (calendar days of the book x instruments)."""
    if not len(tr) or len(grid) < 1 or n_inst < 1:
        return 0.0
    rf = tr["inst"].astype(str).map(rfrac).to_numpy(float)
    rel = rf * tr["fill"].to_numpy(float) / tr["risk"].to_numpy(float)
    span = float(grid[-1] - grid[0] + 1)
    return float(np.nansum(rel * _hold_days(tr)) / (span * n_inst))


def _year_sums(x: pd.Series) -> dict[int, float]:
    y = pd.Series(x.to_numpy(float), index=year_of_day(x.index.to_numpy())).groupby(level=0).sum()
    return {int(k): float(v) for k, v in zip(y.index.to_numpy(np.int64), y.to_numpy(float))}


def drift_guard(tr: pd.DataFrame, m: Market, book: pd.Series, lag: int) -> dict[str, dict[str, Any]]:
    """E9 (C1) against the financed buy-and-hold book of EVERY side the unit trades (stricter reading for a two-sided
    unit). Beta is estimated without side-0 trades (quant C8: "left out of ... E9 beta estimation"): OLS of the book of
    the sided trades on the buy-and-hold book with Newey-West errors (the larger se of lags L and 2L); alpha = that
    intercept + the side-0 trades' mean daily net (they stay in the unit's return)."""
    grid = book.index.to_numpy(np.int64)
    insts = sorted(str(i) for i in tr["inst"].unique())
    if any(i not in m.closes or not len(m.closes[i]) for i in insts):      # quant G1: never against a partial book
        return {"missing": {"alpha_t": float("nan"), "alpha_share": float("nan"), "years": {}, "beta": float("nan")}}
    rfrac = _risk_frac(tr)
    expo = gross_exposure(tr, rfrac, grid, len(insts))
    sided = tr[tr["side"].to_numpy(float) != 0]
    y = book_daily(sided, m, grid).to_numpy(float) if len(sided) else np.zeros(len(grid))
    y_full = book.to_numpy(float)
    mean_full = float(y_full.mean()) if len(y_full) else float("nan")
    unit_years = _year_sums(book)
    out: dict[str, dict[str, Any]] = {}
    for side, name in ((1, "long"), (-1, "short")):
        if not (sided["side"].to_numpy(float) * side > 0).any():
            continue
        bh = buy_hold_daily(m, insts, side, rfrac, grid)
        x = bh.to_numpy(float)
        if len(y) < 3 or float(np.var(x)) <= 0:
            out[name] = {"beta": float("nan"), "alpha": float("nan"), "alpha_t": float("nan"),
                         "alpha_share": float("nan"), "exposure": expo, "years": {}}
            continue
        a1, beta, se1 = _nw_ols(y, x, lag)
        _, _, se2 = _nw_ols(y, x, 2 * lag)
        alpha = a1 + mean_full - float(y.mean())
        se = max(se1, se2)
        years = {yr: {"unit": unit_years.get(yr, 0.0), "book_scaled": expo * v} for yr, v in _year_sums(bh).items()}
        out[name] = {"beta": beta, "alpha": alpha, "alpha_t": alpha / se if se > 0 else float("nan"),
                     "alpha_share": alpha / mean_full if mean_full > 0 else float("nan"), "exposure": expo,
                     "bh_mean": float(x.mean()), "years": years}
    return out


def _tail(tr: pd.DataFrame, book: pd.Series) -> dict[str, Any]:
    """E8 + C6: net > 0 without the top 1 % of trades (ceil), no trade > 10 % of the total net, the book net > 0
    without its top 1 % of days (ceil, of every book day)."""
    net = np.sort((tr["gross"] - tr["cost"]).to_numpy(float))
    total = float(net.sum())
    k = int(math.ceil(TRIM_FRAC * len(net)))
    d = np.sort(book.to_numpy(float))
    kd = int(math.ceil(TRIM_FRAC * len(d)))
    share = float(net[-1] / total) if total > 0 and len(net) else float("inf")
    return {"trimmed_net": float(net[:-k].sum()) if k else total, "top_trade_share": share,
            "trimmed_days_net": float(d[:-kd].sum()) if kd else float(d.sum()),
            "trades_ok": bool(len(net) > k and float(net[:-k].sum()) > 0),
            "top_trade_ok": bool(total > 0 and share <= TOP_TRADE_SHARE),
            "days_ok": bool(len(d) > kd and float(d[:-kd].sum()) > 0)}


def book_summary(tr: pd.DataFrame, m: Market) -> dict[str, Any]:
    """The EVAL-1 book statistics of one unit in one window (JSON-safe; the series itself is `book_daily`)."""
    b = book_daily(tr, m)
    x = b.to_numpy(float)
    L = eval1_lag(tr)
    eq = np.cumsum(x)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq)) if len(x) else 0.0
    sd = float(np.std(x, ddof=1)) if len(x) > 1 else float("nan")
    return {"n_days": int(len(x)), "lag": L, "t": book_t(x, L), "t_plain": _t(x),
            "mean": float(x.mean()) if len(x) else float("nan"),
            "sr_ann": float(x.mean() / sd * math.sqrt(252)) if sd > 0 else float("nan"),
            "years": _year_sums(b), "max_dd_r": dd, "risk_per_trade": RISK_PER_TRADE,
            "tail": _tail(tr, b), "e9": drift_guard(tr, m, b, L)}


def summarize(tr: pd.DataFrame, market: Market | None = None) -> dict[str, Any]:
    """Per-trade means, the twin's excess and its t (reported; a kill only), and with `market` the EVAL-1 book
    (`book_summary`) every gate reads. Without a market the book gates cannot pass."""
    n = len(tr)
    if n == 0:
        return {"n": 0}
    net = tr["gross"] - tr["cost"]
    d = daily(tr)
    yrs = pd.Series(d.to_numpy(), index=year_of_day(d.index.to_numpy())).groupby(level=0).sum()
    out = {
        "n": int(n), "n_days": int(len(d)),
        "gross": float(tr["gross"].mean()), "cost": float(tr["cost"].mean()), "net": float(net.mean()),
        "net_stress": float((tr["gross"] - STRESS * tr["cost"]).mean()),
        "excess": float(tr["excess"].mean()), "ctrl_gross": float(tr["ctrl_gross"].mean()),
        "lag_L": overlap_lag(tr),
        "t_excess_plain": _t(d.to_numpy()),
        "t_excess": hac_t(d.to_numpy(), overlap_lag(tr)),          # gated (HAC, quant C2)
        "t_net": _t(daily(tr.assign(net=net), "net").to_numpy()),
        "drift_adj": float(tr["drift_adj"].mean()),
        "long_share": float((tr["side"] > 0).mean()),
        "years_excess": {int(k): float(v) for k, v in zip(yrs.index.to_numpy(), yrs.to_numpy())},
        "by_cell": {f"{i}|{t}": {"n": int(len(g)), "excess": float(g["excess"].mean()),
                                 "net": float((g["gross"] - g["cost"]).mean())}
                    for (i, t), g in tr.groupby(["inst", "tf"])},
        "median_hold_nights": float(tr["nights"].median()),
    }
    if market is not None:
        out["book"] = book_summary(tr, market)
    return out


def _e9_pass(e: dict[str, Any], years: tuple[int, ...]) -> bool:
    yr = e.get("years", {})
    beaten = sum(1 for y in years if y in yr and yr[y]["unit"] > yr[y]["book_scaled"])
    return bool(np.isfinite(e.get("alpha_t", np.nan)) and e["alpha_t"] >= E9_ALPHA_T_MIN
                and np.isfinite(e.get("alpha_share", np.nan)) and e["alpha_share"] >= E9_ALPHA_SHARE_MIN
                and beaten >= E9_YEARS_MIN)


def explore_gates(s: dict[str, Any], timeframes: list[str], pre: dict[str, Any] | None,
                  years: tuple[int, ...] = EXPLORE_YEARS) -> dict[str, bool]:
    """EVAL-1 section 3, ALL of E1-E4, E6-E9, and the twin kill (section 4); E5 (drift_adj) is reported only."""
    if not s.get("n"):
        return {"n": False}
    daily_only = all(tf in ("1d", "1w") for tf in timeframes)
    bk = s.get("book")
    g: dict[str, bool] = {
        "E1": bool(bk and np.isfinite(bk["t"]) and bk["t"] >= T_MIN),
        "E2": bool(s["net"] > 0),
        "E3": bool(s["gross"] >= GROSS_COST_MIN * s["cost"] and s["gross"] > 0),
        "E4": bool(bk and sum(1 for y in years if bk["years"].get(y, 0.0) > 0) >= YEARS_MIN),
        "E6": bool(s["n"] >= (N_MIN_DAILY if daily_only else N_MIN_INTRADAY)),
        "E8": bool(bk and bk["tail"]["trades_ok"] and bk["tail"]["top_trade_ok"] and bk["tail"]["days_ok"]),
        "E9": bool(bk and bk["e9"] and all(_e9_pass(e, years) for e in bk["e9"].values())),
        "twin_kill": bool(np.isfinite(s["t_excess"]) and s["t_excess"] > TWIN_KILL_T),
    }
    if pre and pre.get("n", 0) >= LEARN_PRE_MIN:
        g["E7"] = bool(np.sign(pre["net"]) == np.sign(s["net"]) != 0)
    return g


def explore_pass(g: dict[str, bool]) -> bool:
    return bool(g) and all(g.values())

def block_boot_p(daily_x: np.ndarray, *, resamples: int = BOOT_RESAMPLES, block: int = BOOT_BLOCK,
                 seed: int = 0) -> float:
    """One-sided p of mean > 0 by a moving-block bootstrap of the centred daily series (block = max(5, L), C2)."""
    block = max(BOOT_BLOCK, int(block))
    x = np.asarray(daily_x, float)
    n = len(x)
    if n < 2 * block:
        return 1.0
    obs = float(x.mean())
    x0 = x - obs
    rng = np.random.default_rng(seed)
    nb = int(math.ceil(n / block))
    hits = 0
    done = 0
    while done < resamples:
        k = min(2000, resamples - done)
        starts = rng.integers(0, n - block + 1, (k, nb))
        idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(k, -1)[:, :n]
        hits += int((x0[idx].mean(axis=1) >= obs).sum())
        done += k
    return (1 + hits) / (resamples + 1)


def bh(p: np.ndarray, q: float = BH_Q) -> np.ndarray:
    p = np.asarray(p, float)
    m = len(p)
    if not m:
        return np.zeros(0, bool)
    order = np.argsort(p, kind="stable")
    ok = p[order] <= np.arange(1, m + 1) * q / m
    out = np.zeros(m, bool)
    if ok.any():
        out[order[: int(np.flatnonzero(ok).max()) + 1]] = True
    return out


def confirm_gates(s: dict[str, Any]) -> dict[str, bool]:
    """EVAL-1 section 5 (the BH-FDR leg is added by the caller over all passers): >= 5/7 years net positive, net > 0
    at 1.5x cost stress, max drawdown at the registered sizing (linear in R) <= the size-down stage."""
    bk = s.get("book")
    if not s.get("n") or not bk:
        return {"n": False}
    yrs_pos = sum(1 for y in CONFIRM_YEARS if bk["years"].get(y, 0.0) > 0)
    return {"years": yrs_pos >= CONFIRM_YEARS_MIN, "net_stress": bool(s["net_stress"] > 0),
            "max_dd": bool(bk["max_dd_r"] * RISK_PER_TRADE <= CONFIRM_DD_MAX)}


def test_old_gates(s: dict[str, Any]) -> dict[str, bool]:
    """TEST-OLD unchanged, on net, plus the E9 beta rule: "a unit with beta > 0.3 must be net >= 0"."""
    bk = s.get("book")
    if not s.get("n") or not bk:
        return {"n": False}
    beta = max((float(e.get("beta", np.nan)) for e in bk.get("e9", {}).values()), default=float("nan"))
    return {"n": s["n"] >= TEST_OLD_N, "net": bool(s["net"] > 0),
            "t_net": bool(np.isfinite(bk["t"]) and bk["t"] >= TEST_OLD_T),
            "beta_net": bool(not (np.isfinite(beta) and beta > TEST_OLD_BETA) or s["net"] >= 0)}


def promotion_gates(confirm_daily: np.ndarray, k_eff: int) -> dict[str, bool]:
    """EVAL-1 section 6 (C5): "DSR >= 0.95 at registry K_eff (`n_trials_effective`) on the CONFIRM net series (the
    explore+confirm DSR is reported only)". The demo kill rule is the engine's, not the screen's."""
    d = deflated_sharpe(np.asarray(confirm_daily, float), int(k_eff))["dsr"]
    return {"dsr": bool(np.isfinite(d) and d >= DSR_MIN)}

VOID_P = 0.02


def null_void_k(m: int, rate: float = NULL_RATE, p_max: float = VOID_P) -> int:
    """Smallest pass count k >= 1 with P(X >= k) <= p_max for X ~ Bin(m, rate): k null passes void the run
    (m = 4 -> k = 2, P = 0.014). m = 0 -> never void by count."""
    from scipy.stats import binom
    if m <= 0:
        return 10**9
    for k in range(1, m + 1):
        if binom.sf(k - 1, m, rate) <= p_max:
            return k
    return m + 1


def null_void(nulls: list[dict[str, Any]]) -> dict[str, Any]:
    """Quant C1. Each item: unit, pass, n_events, floor. Random nulls (NULL-RAND-*, NULL-XS-RANDOM) count only when
    their explore events reach their n floor; any LONGCLOSE drift sentinel passing is a harness defect and voids."""
    feas = [r for r in nulls if "LONGCLOSE" not in r["unit"] and r["n_events"] >= r["floor"]]
    infeas = [r["unit"] for r in nulls if "LONGCLOSE" not in r["unit"] and r["n_events"] < r["floor"]]
    sentinels = [r["unit"] for r in nulls if "LONGCLOSE" in r["unit"] and r["pass"]]
    k = null_void_k(len(feas))
    passes = sum(bool(r["pass"]) for r in feas)
    return {"feasible_nulls": [r["unit"] for r in feas], "cannot_reach_floor": infeas, "feasible_passes": passes,
            "void_at": k, "sentinel_passes": sentinels, "void": passes >= k or bool(sentinels)}


def deflated_sharpe(daily_x: np.ndarray, k: int) -> dict[str, float]:
    """Bailey & Lopez de Prado (2014) DSR of the daily excess against the expected max Sharpe of k null trials
    (per-day units; trial-Sharpe variance taken as the null 1/T). Reported, never gated."""
    from scipy.stats import norm
    x = np.asarray(daily_x, float)
    T = len(x)
    if T < 10 or k < 1:
        return {"sr": float("nan"), "sr0": float("nan"), "dsr": float("nan")}
    sd = float(x.std(ddof=1))
    if sd <= 0:
        return {"sr": float("nan"), "sr0": float("nan"), "dsr": float("nan")}
    sr = float(x.mean() / sd)
    g = 0.5772156649
    v = 1.0 / T
    sr0 = math.sqrt(v) * ((1 - g) * norm.ppf(1 - 1 / k) + g * norm.ppf(1 - 1 / (k * math.e))) if k > 1 else 0.0
    from scipy.stats import kurtosis, skew
    sk = float(skew(x))
    ku = float(kurtosis(x, fisher=False))
    den = math.sqrt(max(1e-12, 1 - sk * sr + (ku - 1) / 4 * sr * sr))
    dsr = float(norm.cdf((sr - sr0) * math.sqrt(T - 1) / den))
    return {"sr": sr, "sr0": sr0, "dsr": dsr}
