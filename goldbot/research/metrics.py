"""Trade-level performance metrics, deflated Sharpe ratio, and the EV/bet-sizing map."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


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
    denom = np.sqrt(max(1 - skew * sr_hat + (kurt - 1) / 4 * sr_hat ** 2, 1e-12))
    return float(stats.norm.cdf((sr_hat - sr0) * np.sqrt(n_obs - 1) / denom))


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


def summarize(trades: pd.DataFrame, trades_per_year: float, n_trials: int = 1) -> dict:
    """Trade-level summary. `dsr` is None below MIN_TRADES_FOR_DSR trades: on a few dozen trades the deflated Sharpe's
    normal approximation and its skew/kurtosis terms are meaningless (16 trades once read 0.991)."""
    r = trades["ret"].to_numpy()
    if len(r) == 0:
        return {"n": 0}
    sr_per_trade = np.mean(r) / (np.std(r, ddof=1) if len(r) > 1 and np.std(r, ddof=1) > 0 else 1)
    dsr = (deflated_sharpe(sr_per_trade, n_trials, len(r), float(stats.skew(r)), float(stats.kurtosis(r, fisher=False)),
                           var_sr_trials=1.0 / max(len(r), 1))
           if len(r) >= MIN_TRADES_FOR_DSR else None)
    return {
        "n": int(len(r)),
        "hit_rate": float(np.mean(r > 0)),
        "mean_ret": float(np.mean(r)),
        "std_ret": float(np.std(r, ddof=1)) if len(r) > 1 else 0.0,
        "profit_factor": profit_factor(r),
        "sharpe_ann": sharpe_annualised(r, trades_per_year),
        "max_dd": max_drawdown(r),
        "dsr": dsr,
    }


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
