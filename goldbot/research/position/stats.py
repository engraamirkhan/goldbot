"""Read statistics of position specs (amendment W3.1, W3.2, W3.4) and BH-FDR across a batch.

W3.2 pass statistics: "Primary statistic: the mean of pnl_d over all calendar trading days in the window, flat days
included. t-statistic: a stationary day-block bootstrap with mean block length 20 days and 4,000 resamples. The annual
Sharpe is reported with it. Years positive: ... at least 60% of available years positive, rounded up. Drift-adjusted
gross: sum of e_{d-1} x (r_d - mu_w)." Null: "Excess = actual net minus the mean null net. The C1 rule (excess >= 0)
applies." W3.4: "n >= 30 round trips ... >= 500 days in market. A round trip is an entry from flat or a sign change,
closed by a return to flat or a flip." W3 addendum: a same-sign re-entry at the next open after a disaster stop
continues its round trip (stops are counted apart, `n_stops`), and only years with at least one day in the market count
for the years rule (warm-up and all-flat years are not "available").

W3.1 power: "P_explore = Phi(SR_prior x sqrt(Y_learn) - 2.0)"; "P_chain, simulated as in rethink Appendix C, with
confirm over 7.75 years and TEST-OLD over 3.9 years"; blends "SR_prior = SR_member x sqrt(N / (1 + (N - 1) rho))".
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.research.autodisc.stats import stationary_bootstrap_indices

if TYPE_CHECKING:
    from goldbot.research.position.engine import EngineConfig

TRADING_DAYS = 252
_CHUNK = 500


class PositionStats(FrozenRecord):
    """One market's or one book's read on one window under one venue (sums in units of starting equity)."""
    n_days: int
    mean_daily: float
    net_total: float
    gross_total: float
    cost_total: float
    financing_total: float                       # signed: a credit is positive
    sharpe: float                                # annualised, daily net
    t_boot: float                                # stationary day-block bootstrap t of the daily net mean
    gross_t: float
    years_positive: int
    n_years: int                                 # years with at least one day in the market
    years_rule: bool                             # >= 60% of those years positive, rounded up
    drift_adj_gross: float
    long_net: float
    short_net: float
    n_round_trips_signal: int                    # W3.4 round trips entered inside the window (stop re-entries continue)
    n_stops: int                                 # disaster-stop exits (at the open or inside the day)
    days_in_market: int
    null_kind: Literal["sign", "exposure"] | None = None
    null_mean_net: float | None = None
    null_excess: float | None = None             # net_total - null_mean_net (C1: >= 0)
    null_p: float | None = Field(None, ge=0, le=1)
    buy_hold_net: float | None = None
    buy_hold_sharpe: float | None = None


def sharpe(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    sd = float(x.std(ddof=1)) if len(x) > 1 else 0.0
    return float(x.mean() / sd * math.sqrt(TRADING_DAYS)) if sd > 0 else 0.0


def block_t(x: np.ndarray | pd.Series, *, resamples: int, block_days: float, seed: int) -> float:
    """Mean / (std of the stationary day-block bootstrap means): W3.2's t."""
    v = np.asarray(x, dtype=float)
    n = len(v)
    if n < 2 or float(np.abs(v).sum()) == 0.0:
        return 0.0
    means = []
    for i, start in enumerate(range(0, resamples, _CHUNK)):
        k = min(_CHUNK, resamples - start)
        means.append(v[stationary_bootstrap_indices(n, k, block_days, seed + i)].mean(axis=1))
    se = float(np.concatenate(means).std(ddof=1))
    return float(v.mean() / se) if se > 0 else 0.0


def years_needed(n_years: int) -> int:
    """60% of `n_years`, rounded up (integer arithmetic: 0.6 x 10 is not 6 in floating point)."""
    return (6 * n_years + 9) // 10


def years_positive(daily: pd.Series, in_market: pd.Series | None = None) -> tuple[int, int]:
    """(years with a positive net sum, years counted): a year counts only with at least one day in the market."""
    years = pd.DatetimeIndex(daily.index).year
    by = daily.groupby(years).sum()
    if in_market is not None:
        active = in_market.astype(bool).groupby(pd.DatetimeIndex(in_market.index).year).any()
        by = by[active.reindex(by.index).fillna(False).astype(bool).to_numpy()]
    return int((by > 0).sum()), int(len(by))


def episodes(b: np.ndarray, held: np.ndarray, stop_open: np.ndarray | None = None) -> np.ndarray:
    """Round-trip id per day (-1 flat). A new one starts on a day holding units after the fill that follows a flat day
    or a flip (an entry from flat or a sign change). A same-sign re-entry at the next open after a disaster stop
    continues it: the previous day held units until its stop (b != 0), or was stopped at the open (`stop_open`, the
    re-entry being the next open). `held` is accepted for the caller's symmetry; b and the stop flags decide."""
    b = np.asarray(b, dtype=float)
    so = np.zeros(len(b), dtype=bool) if stop_open is None else np.asarray(stop_open, dtype=bool)
    out = np.full(len(b), -1, dtype=int)
    cur, alive, grace = -1, 0.0, False
    for d in range(len(b)):
        if b[d] == 0:
            grace = bool(so[d]) and alive != 0
            alive = alive if grace else 0.0
            continue
        s = float(np.sign(b[d]))
        if not (s == alive and (grace or (d > 0 and b[d - 1] != 0))):
            cur += 1
        alive, grace = s, False
        out[d] = cur
    return out


def _null_fields(net_total: float, null: np.ndarray | None, kind: str) -> dict[str, Any]:
    if null is None:
        return {}
    m = float(np.mean(null))
    return {"null_kind": kind, "null_mean_net": m, "null_excess": net_total - m,
            "null_p": float((1 + np.sum(null >= net_total)) / (1 + len(null)))}


def _core(net: pd.Series, gross: pd.Series, frame: pd.DataFrame, cfg: EngineConfig,
          in_market: pd.Series) -> dict[str, Any]:
    yp, ny = years_positive(net, in_market)
    return {"n_days": int(len(net)), "mean_daily": float(net.mean()) if len(net) else 0.0,
            "net_total": float(net.sum()), "gross_total": float(gross.sum()), "cost_total": float(frame["cost"].sum()),
            "financing_total": float(frame["financing"].sum()), "sharpe": sharpe(net.to_numpy()),
            "t_boot": block_t(net, resamples=cfg.boot_resamples, block_days=cfg.boot_block_days, seed=cfg.null_seed),
            "gross_t": block_t(gross, resamples=cfg.boot_resamples, block_days=cfg.boot_block_days, seed=cfg.null_seed),
            "years_positive": yp, "n_years": ny, "years_rule": ny > 0 and yp >= years_needed(ny),
            "long_net": float(frame["long"].sum()), "short_net": float(frame["short"].sum())}


def drift_adjusted_gross(frame: pd.DataFrame) -> float:
    """gross - mu_w x sum(exposure held): mu_w is the market's mean daily return in the window (diagnostic only)."""
    mu = float(frame["ret"].mean()) if frame["ret"].notna().any() else 0.0
    return float(frame["gross"].sum() - mu * frame["expo"].sum())


def _counts(frame: pd.DataFrame) -> dict[str, Any]:
    """W3.4 round trips and the stop exits of one market's window frame (a composite's parts are separated by their
    flat edges, so concatenated frames count the same as their parts)."""
    ep = episodes(frame["units"].to_numpy(), frame["held"].to_numpy(), frame["stop_open"].to_numpy())
    return {"n_round_trips_signal": int(len(np.unique(ep[ep >= 0]))),
            "n_stops": int((frame["stop_open"].astype(bool) | frame["stop_day"].astype(bool)).sum())}


def _buy_hold(buy_hold: pd.Series | None) -> dict[str, Any]:
    if buy_hold is None:
        return {}
    return {"buy_hold_net": float(buy_hold.sum()), "buy_hold_sharpe": sharpe(buy_hold.to_numpy())}


def summarise(frame: pd.DataFrame, *, cfg: EngineConfig, null: np.ndarray | None, null_kind: str,
              buy_hold: pd.Series | None) -> PositionStats:
    """One market's daily frame (engine.book_path) -> its stats."""
    core = _core(frame["net"], frame["gross"], frame, cfg, frame["units"] != 0)
    bh = _buy_hold(buy_hold)
    return PositionStats(**core, drift_adj_gross=drift_adjusted_gross(frame), **_counts(frame),
                         days_in_market=int((frame["units"] != 0).sum()),
                         **_null_fields(core["net_total"], null, null_kind), **bh)


def summarise_book(book: pd.DataFrame, assets: dict[str, pd.DataFrame], *, cfg: EngineConfig, null: np.ndarray | None,
                   null_kind: str, buy_hold: pd.Series | None) -> PositionStats:
    """The book: daily sums over its markets; round trips summed; a day is in the market if any market is."""
    core = _core(book["net"], book["gross"], book, cfg, book["units"] != 0)
    counts = [_counts(f) for f in assets.values()]
    bh = _buy_hold(buy_hold)
    return PositionStats(**core, drift_adj_gross=float(sum(drift_adjusted_gross(f) for f in assets.values())),
                         n_round_trips_signal=sum(c["n_round_trips_signal"] for c in counts),
                         n_stops=sum(c["n_stops"] for c in counts), days_in_market=int(book["units"].sum()),
                         **_null_fields(core["net_total"], null, null_kind), **bh)


def explore_checks(s: PositionStats) -> dict[str, bool]:
    """The W3 explore legs (POS-1 section 4) on one read; the pass is all of them."""
    return {"net>0": s.mean_daily > 0, "t_boot>=2": s.t_boot >= 2.0, "years>=60%": s.years_rule,
            "drift_adj_gross>0": s.drift_adj_gross > 0,
            "null_excess>=0": s.null_excess is not None and s.null_excess >= 0,
            "round_trips>=30": s.n_round_trips_signal >= 30, "days_in_market>=500": s.days_in_market >= 500}


def bh_fdr(p: Sequence[float] | np.ndarray, q: float = 0.10) -> np.ndarray:
    """Benjamini-Hochberg step-up at `q`: reject the k smallest p where k is the largest i with p_(i) <= i q / m."""
    pv = np.asarray(p, dtype=float)
    m = len(pv)
    out = np.zeros(m, dtype=bool)
    if m == 0:
        return out
    order = np.argsort(pv)
    ok = np.flatnonzero(pv[order] <= q * np.arange(1, m + 1) / m)
    if len(ok):
        out[order[:ok[-1] + 1]] = True
    return out


# ---------------------------------------------------------------- W3.1 power
def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def p_one_sided(t: float) -> float:
    """One-sided p of a bootstrap t (normal reference): the p W2/C1 put into BH-FDR at confirm."""
    return 1.0 - _phi(t)


def p_explore(sr: float, y_learn: float) -> float:
    return _phi(sr * math.sqrt(y_learn) - 2.0)


def sr_blend(sr_member: float, n: int, rho: float) -> float:
    return sr_member * math.sqrt(n / (1.0 + (n - 1) * rho))


def p_chain(sr: float, y_learn: float, *, draws: int = 200_000, seed: int = 20261102, confirm_years: float = 7.75,
            testold_years: float = 3.9) -> float:
    """Rethink Appendix C: yearly Sharpe draws ~ N(SR, 1) per year (a part year scaled); explore t >= 2 over y_learn
    years; confirm t >= 1.65 with >= 5 of its years positive; TEST-OLD t >= 1.28."""
    rng = np.random.default_rng(seed)
    explore = rng.normal(sr * math.sqrt(y_learn), 1.0, draws) >= 2.0
    lengths = np.array([1.0] * int(confirm_years) + ([confirm_years % 1] if confirm_years % 1 else []))
    yearly = rng.normal(sr * lengths, np.sqrt(lengths), (draws, len(lengths)))
    confirm = (yearly.sum(axis=1) / math.sqrt(lengths.sum()) >= 1.65) & ((yearly > 0).sum(axis=1) >= 5)
    testold = rng.normal(sr * math.sqrt(testold_years), 1.0, draws) >= 1.28
    return float(np.mean(explore & confirm & testold))
