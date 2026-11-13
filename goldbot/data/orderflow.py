"""Order-flow proxies from 1m bars (R139, plan item 8; 4-ai-systems.md s.5 rank 4).

We have no L2 and no trade signs: Dukascopy gives 1m bid/ask OHLC and a tick volume that counts the bid side's quote
updates (stored as `tick_count`; scripts/dukascopy_year.py drops the ask side's), not signed trades. So the "imbalance"
of the bulk-volume classification (Easley, Lopez de Prado and O'Hara 2012: each minute's count split by Phi(dp / sigma),
dp the minute's mid close-to-close change, sigma its rolling std over SIGMA_N minutes) is here a quote-update-weighted
return score, named `of_vw_retz_*` for what it is (quant review 2026-11-13). It is a close cousin of plain returns and
momentum, so a survivor must beat those features in the same screen (SCREEN_NOTE in goldbot/features/orderflow.py).

Per 1m bar, every value is known at that bar's `visible_at` (its close; a corrected minute's later stamp is kept), so
`available_utc` = `visible_at`; windows are time-based and end at the bar, (t - w, t]:
* `of_vw_retz_15m`, `of_vw_retz_60m`: sum(tick_count x (2 Phi - 1)) / sum(tick_count), in [-1, 1];
* `of_vw_run`: the signed tick count of the current run of same-direction minutes (a flat minute or a gap > RUN_GAP ends
  it) over the last 60 minutes' tick count;
* `of_spread_rel_15m`: mean spread over 15 min / mean spread over the trailing day;
* `of_spread_chg_60m`: mean spread over the last 15 min / over the 45 min before, minus 1;
* `of_spread_max_rel_60m`: max spread_max over 60 min / mean spread over the trailing day;
* `of_tick_rate_15m`: log(tick count per minute over 15 min / per minute over the trailing day).
A minute with tick_count <= 0 (the `warning:no_volume` minutes of the early archive) is missing, never quiet: it
carries no weight and a window with no volume reads NaN.

No fill columns: fill slippage pooled every account and symbol, is endogenous to our own trading, needed a 7-day stale
tolerance and is NaN before October 2026, so it would act as a date marker (quant review 2026-11-13). MFE/MAE wait for
excursion fields in `fills` (BACKLOG 214).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import ndtr

SIGMA_N = 60
RUN_GAP = pd.Timedelta(minutes=5)
DAY = "1D"
COLUMNS: tuple[str, ...] = ("of_vw_retz_15m", "of_vw_retz_60m", "of_vw_run", "of_spread_rel_15m", "of_spread_chg_60m",
                            "of_spread_max_rel_60m", "of_tick_rate_15m")


def _mid_frame(bars: pd.DataFrame) -> pd.DataFrame:
    """ts_utc-indexed close, spread, spread_max, tick_count (NaN for no volume) and available_utc, from the store's
    bid/ask schema or a mid frame (close, spread)."""
    ts = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True))
    if "bid_close" in bars.columns:
        close = (bars["bid_close"].to_numpy(dtype=float) + bars["ask_close"].to_numpy(dtype=float)) / 2.0
        spread = bars["spread_mean"].to_numpy(dtype=float)
    else:
        close = bars["close"].to_numpy(dtype=float)
        spread = bars["spread"].to_numpy(dtype=float)
    smax = bars["spread_max"].to_numpy(dtype=float) if "spread_max" in bars.columns else spread
    tc = bars["tick_count"].to_numpy(dtype=float)
    vis = pd.to_datetime(bars["visible_at"], utc=True) if "visible_at" in bars.columns else ts + pd.Timedelta(minutes=1)
    f = pd.DataFrame({"close": close, "spread": spread, "spread_max": smax, "tc": np.where(tc > 0, tc, np.nan),
                      "available_utc": pd.DatetimeIndex(vis)}, index=ts)
    return f[~f.index.duplicated(keep="last")].sort_index()


def orderflow_frame(bars_1m: pd.DataFrame) -> pd.DataFrame:
    """DataFrame(available_utc, *COLUMNS), one row per 1m bar (see the module docstring)."""
    if bars_1m is None or len(bars_1m) == 0:
        return pd.DataFrame(columns=["available_utc", *COLUMNS])
    f = _mid_frame(bars_1m)
    dp = f["close"].diff()
    sigma = dp.rolling(SIGMA_N, min_periods=20).std()
    z = (dp / sigma.where(sigma > 0)).fillna(0.0).to_numpy()
    tc = f["tc"].fillna(0.0)
    signed = tc * (2.0 * ndtr(z) - 1.0)
    out = pd.DataFrame(index=f.index)
    sums = {w: tc.rolling(w).sum() for w in ("15min", "60min", DAY)}
    for w, col in (("15min", "of_vw_retz_15m"), ("60min", "of_vw_retz_60m")):
        out[col] = signed.rolling(w).sum() / sums[w].where(sums[w] > 0)
    # runs of same-direction minutes: a new run at every sign change, flat minute or time gap
    direction = np.sign(dp.fillna(0.0))
    gap = f.index.to_series().diff() > RUN_GAP
    new_run = (direction != direction.shift()) | (direction == 0) | gap
    run_id = new_run.cumsum()
    run_tc = tc.groupby(run_id).cumsum() * direction
    out["of_vw_run"] = run_tc / sums["60min"].where(sums["60min"] > 0)
    sp = f["spread"]
    day_mean = sp.rolling(DAY).mean()
    s15, n15 = sp.rolling("15min").sum(), sp.rolling("15min").count()
    s60, n60 = sp.rolling("60min").sum(), sp.rolling("60min").count()
    prior = (s60 - s15) / (n60 - n15).where(n60 - n15 > 0)
    m15 = s15 / n15.where(n15 > 0)
    out["of_spread_rel_15m"] = m15 / day_mean.where(day_mean > 0)
    out["of_spread_chg_60m"] = m15 / prior.where(prior > 0) - 1.0
    out["of_spread_max_rel_60m"] = f["spread_max"].rolling("60min").max() / day_mean.where(day_mean > 0)
    vol_min = {w: f["tc"].rolling(w).count() for w in ("15min", DAY)}
    rate15 = sums["15min"] / vol_min["15min"].where(vol_min["15min"] > 0)
    rate_day = sums[DAY] / vol_min[DAY].where(vol_min[DAY] > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["of_tick_rate_15m"] = np.log(rate15 / rate_day.where(rate_day > 0))
    # a window whose minutes all lack volume: the volume-derived columns are unknown, not zero
    no_vol15 = vol_min["15min"] == 0
    out.loc[no_vol15, ["of_vw_retz_15m", "of_tick_rate_15m"]] = np.nan
    out.loc[sums["60min"] <= 0, ["of_vw_retz_60m", "of_vw_run"]] = np.nan
    out.insert(0, "available_utc", f["available_utc"].to_numpy())
    return out.reset_index(drop=True)[["available_utc", *COLUMNS]]


def ctx_frame(bars_1m: pd.DataFrame) -> pd.DataFrame:
    """1m bars for ctx["bars_1m"], stamped `available_utc` = visible_at (each minute's close), so a caller that cuts
    ctx tables by availability (pipeline.lookahead_check) cuts them exactly where the features do."""
    df = bars_1m.copy()
    vis = df["visible_at"] if "visible_at" in df.columns else pd.to_datetime(df["ts_utc"], utc=True) + pd.Timedelta(minutes=1)
    df["available_utc"] = pd.to_datetime(vis, utc=True)
    return df
