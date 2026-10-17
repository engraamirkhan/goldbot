"""Trade-level performance metrics, deflated Sharpe ratio, and the EV/bet-sizing map."""
from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field
from scipy import stats

from goldbot.base import FrozenRecord
from goldbot.data.timeutil import epoch_ns


def expectancy_r(ret: np.ndarray, risk_r: np.ndarray) -> float:
    """Mean return in R (per unit risked)."""
    return float(np.mean(ret / risk_r))


def sharpe_annualised(trade_ret: np.ndarray, trades_per_year: float) -> float:
    if len(trade_ret) < 2 or np.std(trade_ret, ddof=1) == 0:
        return 0.0
    return float(np.mean(trade_ret) / np.std(trade_ret, ddof=1) * np.sqrt(trades_per_year))


def max_drawdown(trade_ret: np.ndarray) -> float:
    eq = np.cumprod(1 + trade_ret)
    peak = np.maximum.accumulate(eq)
    return float(np.max(1 - eq / peak)) if len(eq) else 0.0


def profit_factor(trade_ret: np.ndarray) -> float:
    gains, losses = trade_ret[trade_ret > 0].sum(), -trade_ret[trade_ret < 0].sum()
    return float(gains / losses) if losses > 0 else float("inf")


def deflated_sharpe(sr_hat: float, n_trials: int, n_obs: int, skew: float, kurt: float, var_sr_trials: float) -> float:
    """Bailey & López de Prado (2014) Deflated Sharpe Ratio: probability that the observed SR (per-period,
    not annualised) beats the expected maximum SR from `n_trials` unskilled trials."""
    if n_trials < 1 or n_obs < 3:
        return 0.0
    emc = 0.5772156649
    z1 = stats.norm.ppf(1 - 1.0 / n_trials) if n_trials > 1 else 0.0
    z2 = stats.norm.ppf(1 - 1.0 / (n_trials * np.e)) if n_trials > 1 else 0.0
    sr0 = np.sqrt(max(var_sr_trials, 1e-12)) * ((1 - emc) * z1 + emc * z2)
    return float(stats.norm.cdf((sr_hat - sr0) * np.sqrt(n_obs - 1) / _sr_denom(sr_hat, skew, kurt)))


def _sr_denom(sr_hat: float, skew: float, kurt: float) -> float:
    """The non-normality scale of the Sharpe estimate (Mertens 2002): sqrt(1 - skew SR + (kurt - 1) / 4 SR^2)."""
    return float(np.sqrt(max(1 - skew * sr_hat + (kurt - 1) / 4 * sr_hat ** 2, 1e-12)))


def breakeven_prob(target_atr: float, stop_atr: float, cost_atr: float) -> float:
    return (stop_atr + cost_atr) / (target_atr + stop_atr)


def expected_value_r(p: np.ndarray, target_atr: float, stop_atr: float, cost_atr: float) -> np.ndarray:
    return p * target_atr - (1 - p) * stop_atr - cost_atr


def size_multiplier(p: np.ndarray, allocator_w: float, target_atr: float, stop_atr: float, cost_atr: float,
                    width: float = 0.20) -> np.ndarray:
    """Capped linear map from calibrated probability to [0, 1] times allocator weight."""
    p0 = breakeven_prob(target_atr, stop_atr, cost_atr)
    return allocator_w * np.clip((p - p0) / width, 0.0, 1.0)


MIN_TRADES_FOR_DSR = 200      # below this the deflated Sharpe is not reported (asymptotic statistic; design gates)


class DsrInputs(FrozenRecord):
    """Everything the deflated Sharpe of a trade sample needs except the trial count, stored with the result
    (`summarize` -> `dsr_inputs`) so a reader can recompute it with the registry's count of today. Quant review
    2026-10-16: the backtest -> paper gate wants "DSR >= 1.0 on the true trial count", and the count at run time is not
    the true count once later trials have been run."""

    sr: float                          # per-trade Sharpe (mean / sd), not annualised
    n: int = Field(ge=1)               # trades
    skew: float
    kurtosis: float                    # Pearson (normal = 3)
    var_sr_trials: float               # variance of the SR across trials (summarize: 1 / n)

    @classmethod
    def of(cls, r: np.ndarray) -> DsrInputs:
        """The inputs of a sample of per-period returns (one per trade, or one per day): SR = mean / sd (sd 1 when it
        is 0 or undefined), Pearson kurtosis, var(SR) = 1 / n. `summarize` and `day_summed_dsr` both build them here."""
        r = np.asarray(r, dtype=float)
        sd = np.std(r, ddof=1) if len(r) > 1 else 0.0
        return cls(sr=float(np.mean(r) / (sd if sd > 0 else 1)), n=len(r), skew=float(stats.skew(r)),
                   kurtosis=float(stats.kurtosis(r, fisher=False)), var_sr_trials=1.0 / max(len(r), 1))

    def dsr(self, n_trials: int) -> float:
        return deflated_sharpe(self.sr, n_trials, self.n, self.skew, self.kurtosis, self.var_sr_trials)

    def t(self) -> float:
        """The effective t the DSR deflates: SR sqrt(n - 1) / sqrt(1 - skew SR + (kurt - 1) / 4 SR^2), its z against a
        zero Sharpe (the pre-registration's "t about 4.0 at K = 60" is this t at DSR 0.95)."""
        return float(self.sr * np.sqrt(max(self.n - 1, 0)) / _sr_denom(self.sr, self.skew, self.kurtosis))


def summarize(trades: pd.DataFrame, trades_per_year: float, n_trials: int = 1) -> dict:
    """Trade-level summary. `dsr` is None below MIN_TRADES_FOR_DSR trades: on a few dozen trades the deflated Sharpe's
    normal approximation and its skew/kurtosis terms are meaningless (16 trades once read 0.991). `dsr` uses `n_trials`
    (the count at run time); `dsr_inputs` (a DsrInputs dump, None with `dsr`) lets a reader recompute it on a later count."""
    r = trades["ret"].to_numpy()
    if len(r) == 0:
        return {"n": 0}
    inputs = DsrInputs.of(r) if len(r) >= MIN_TRADES_FOR_DSR else None
    dsr = inputs.dsr(n_trials) if inputs is not None else None
    return {
        "n": int(len(r)),
        "hit_rate": float(np.mean(r > 0)),
        "mean_ret": float(np.mean(r)),
        "std_ret": float(np.std(r, ddof=1)) if len(r) > 1 else 0.0,
        "profit_factor": profit_factor(r),
        "sharpe_ann": sharpe_annualised(r, trades_per_year),
        "max_dd": max_drawdown(r),
        "dsr": dsr,
        "dsr_inputs": inputs.model_dump() if inputs is not None else None,
    }


NS_PER_DAY = 86_400 * 10**9


def daily_sums(r: np.ndarray, entry_utc: pd.DatetimeIndex | pd.Series) -> np.ndarray:
    """One summed value per UTC entry day that has a trade, in day order: days without a trade are no observation
    (pre-registration 2027Q1 O-5/O-6, E-6: "over days with at least one model-filtered trade only")."""
    days = epoch_ns(pd.DatetimeIndex(entry_utc)) // NS_PER_DAY     # epoch ns are UTC whatever the stamp's zone
    keys, inv = np.unique(days, return_inverse=True)
    return np.bincount(inv, weights=np.asarray(r, dtype=float), minlength=len(keys))


class DaySummedDsr(FrozenRecord):
    """The day-summed deflated Sharpe of the discovery survivors' reading rule (pre-registration 2027Q1 O-5/O-6, E-6):
    "computed on one summed net R per entry UTC day, over days with at least one model-filtered trade only (n = G such
    days), with var(SR) = 1/G, the skew and kurtosis of those daily sums, and the same K = `n_trials_effective` as the
    gate". Same-day trades share one move, so a day carries less evidence than its trade count says; with one trade a
    day both sides are the same number. Stored with the trial (results.model_filtered.day_summed) and re-read on
    today's K by `gates.day_summed_check`; `at(k)` recomputes it.

    The per-trade side is on the same net R, so the two ts compare like with like: it is R basis, NOT the gate DSR
    (`summarize` and `gates.current_dsr` are on `ret`, the fraction of price; they differ when the risk varies, quant
    review 2026-10-17). A pass verdict takes the gate's DSR from `current_dsr`, never from this record."""

    per_trade_r: DsrInputs             # the same net-R series, one observation per trade (R basis, not the gate's)
    day_summed: DsrInputs              # one summed net R per entry UTC day with a trade: n = G, var(SR) = 1 / G
    n_trades: int = Field(ge=1)
    n_days: int = Field(ge=1)          # G
    n_trials: int = Field(ge=1)        # K the two DSRs below use
    dsr_per_trade_r: float             # R basis, not the gate DSR
    dsr_day_summed: float
    t_per_trade_r: float               # DsrInputs.t: the effective t each DSR deflates
    t_day_summed: float

    @classmethod
    def of(cls, per_trade_r: DsrInputs, day_summed: DsrInputs, n_trials: int) -> DaySummedDsr:
        k = max(int(n_trials), 1)
        return cls(per_trade_r=per_trade_r, day_summed=day_summed, n_trades=per_trade_r.n, n_days=day_summed.n,
                   n_trials=k, dsr_per_trade_r=per_trade_r.dsr(k), dsr_day_summed=day_summed.dsr(k),
                   t_per_trade_r=per_trade_r.t(), t_day_summed=day_summed.t())

    def at(self, n_trials: int) -> DaySummedDsr:
        """The same sample read on another trial count (the registry's `n_trials_effective` today)."""
        return DaySummedDsr.of(self.per_trade_r, self.day_summed, n_trials)


def day_summed_dsr(net_r: np.ndarray, entry_utc: pd.DatetimeIndex | pd.Series, n_trials: int) -> DaySummedDsr | None:
    """E-6: the day-summed DSR of model-filtered trades (`net_r`, net R per trade; `entry_utc`, each trade's entry) on
    `n_trials`, beside the per-trade DSR of the same R series. None below MIN_TRADES_FOR_DSR trades or MIN_TRADES_FOR_DSR
    days with a trade: the statistic is asymptotic on its observations, which are the days (O-5/O-6 rule, amended
    2026-10-17 before any H-14 outcome; the reading then fails closed)."""
    r = np.asarray(net_r, dtype=float)
    sums = daily_sums(r, entry_utc)
    if len(r) < MIN_TRADES_FOR_DSR or len(sums) < MIN_TRADES_FOR_DSR:
        return None
    return DaySummedDsr.of(DsrInputs.of(r), DsrInputs.of(sums), n_trials)


def expectancy(ret: np.ndarray, risk: np.ndarray) -> dict:
    """Per-trade expectancy of a set of trades: mean return, mean R (return / fraction of price risked), hit rate and
    the t-statistic of the mean R (0 below two trades)."""
    ret, risk = np.asarray(ret, dtype=float), np.asarray(risk, dtype=float)
    ok = np.isfinite(ret) & np.isfinite(risk) & (risk > 0)
    ret, r = ret[ok], ret[ok] / risk[ok]
    n = len(r)
    if n == 0:
        return {"n": 0}
    sd = float(np.std(r, ddof=1)) if n > 1 else 0.0
    return {"n": int(n), "mean_ret": float(np.mean(ret)), "mean_r": float(np.mean(r)), "hit_rate": float(np.mean(ret > 0)),
            "t_stat": float(np.mean(r) / sd * np.sqrt(n)) if sd > 0 else 0.0}

def calibration_ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """Expected calibration error over `bins` equal-width bins of p (1.0 for an empty sample)."""
    if len(p) == 0:
        return 1.0
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    ece = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            ece += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(ece)
