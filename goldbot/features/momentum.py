"""Momentum features for the time-series momentum and session intraday momentum families (proposal P4).

* `tsmom`: volatility-scaled trailing log returns over 24, 120 and 480 bars (on 1h bars: 1, 5 and 20 trading days;
  Moskowitz, Ooi and Pedersen 2012 scale by volatility so horizons and regimes compare), their mean and how many agree
  in sign. The specialist computes its own trigger with the same function (`tsmom_score`).
* `intraday_session`: for London and New York on their local wall clocks (DST handled by the zone), the minutes since
  the session open and the move since that open in ATR (Gao, Han, Li and Zhou 2018 use the first part of the session
  to predict the last). Outside the session both are NaN.

Everything uses bars up to and including the current one (checked by the pipeline's lookahead check)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.calendar import LOCAL_SESSIONS
from goldbot.features import (  # noqa: F401  (registered first: these families append to the column order)
    session,
    structure,
)
from goldbot.features.columns import Columns
from goldbot.features.registry import FeatureCtx, feature
from goldbot.features.technical import atr

TSMOM_HORIZONS = (24, 120, 480)
TSMOM_VOL_WINDOW = 96


def vol_scaled_returns(close: pd.Series, horizons: tuple[int, ...], vol_window: int) -> dict[int, pd.Series]:
    """Per horizon h: log return over h bars / (std of 1-bar log returns over `vol_window` bars x sqrt(h)), a z-score
    of the trailing move under a random walk."""
    lc = pd.Series(np.log(close.to_numpy(dtype=float)), index=close.index)
    sigma = lc.diff().rolling(vol_window, min_periods=vol_window).std().replace(0, np.nan)
    return {h: lc.diff(h) / (sigma * np.sqrt(h)) for h in horizons}


def tsmom_score(close: pd.Series, horizons: tuple[int, ...], vol_window: int) -> pd.Series:
    """Mean of the vol-scaled returns over `horizons` (NaN until the longest horizon has history)."""
    z = vol_scaled_returns(close, horizons, vol_window)
    return pd.concat(list(z.values()), axis=1).mean(axis=1, skipna=False)


@feature("tsmom", "momentum", lookback=max(TSMOM_HORIZONS) + TSMOM_VOL_WINDOW,
         signed={r"tsmom_z_\d+": 0.0, "tsmom_score": 0.0, "tsmom_agree": 0.0})
def f_tsmom(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    z = vol_scaled_returns(df["close"], TSMOM_HORIZONS, TSMOM_VOL_WINDOW)
    out = Columns(df.index)
    for h, s in z.items():
        out[f"tsmom_z_{h}"] = s
    zz = pd.concat(list(z.values()), axis=1)
    out["tsmom_score"] = zz.mean(axis=1, skipna=False)
    out["tsmom_agree"] = pd.DataFrame(np.sign(zz.to_numpy()), index=df.index).sum(axis=1, min_count=len(TSMOM_HORIZONS))
    return out.frame()


SESSION_PREFIX = {"london": "ldn", "newyork": "ny"}


@feature("intraday_session", "momentum", lookback=200, signed={r"im_(ldn|ny)_ret_atr": 0.0})
def f_intraday_session(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    ts = pd.DatetimeIndex(pd.to_datetime(df["ts_utc"], utc=True))
    a = atr(df, 14).to_numpy()
    close = df["close"].to_numpy(dtype=float)
    out = Columns(df.index)
    for name, p in SESSION_PREFIX.items():
        sess = LOCAL_SESSIONS[name]
        since, _ = sess.clock(ts)
        sopen = sess.open_price(df)
        out[f"im_{p}_min"] = since
        with np.errstate(invalid="ignore", divide="ignore"):
            out[f"im_{p}_ret_atr"] = np.where(a > 0, (close - sopen) / a, np.nan)
    return out.frame()
